import asyncio
import pytest
from scripts import antigravity_workflow as workflow


def test_reviewers_prompt_concurrently_and_resume_skips_planner():
    class Client:
        def __init__(self):
            self.count = 0
            self.started = 0
            self.both = asyncio.Event()
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new':
                self.count += 1
                return {'sessionId': str(self.count), 'configOptions': [{'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            if method == 'session/prompt':
                self.started += 1
                if self.started == 2: self.both.set()
                await asyncio.wait_for(self.both.wait(), 1)
                return {}
    states = {'planner': {'plan': {'version': 1, 'assignments': [['a.md'], ['b.md']]}},
              'reviewer-1': {'reviewer': 1, 'files': [{'path': 'a.md', 'status': 'pending'}], 'finished': False},
              'reviewer-2': {'reviewer': 2, 'files': [{'path': 'b.md', 'status': 'pending'}], 'finished': False}}
    client = Client()
    async def read(key):
        if key.startswith('reviewer') and client.started == 2:
            states[key]['finished'] = True
        return states[key]
    result, model, _ = asyncio.run(workflow.exercise(client, 'm', 60, read))
    assert client.count == 2 and client.started == 2 and model == 'm'
    assert len(result['agents']) == 2


def test_workflow_binding_uses_canonical_hash():
    result = workflow.binding('m', 'job', None, 600)
    assert result['sha256'] == workflow.digest({k: v for k, v in result.items() if k != 'sha256'})
    assert result['plan_sha256'] == workflow.digest(None)


def test_failure_exports_latest_partial_before_container_cleanup(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(workflow.local, 'ROOT', tmp_path)
    async def command(args, env, **kwargs): events.append('cleanup' if 'down' in args else 'command')
    monkeypatch.setattr(workflow.local, 'command', command)
    class Process:
        returncode = None
        def __init__(self):
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()
        def kill(self): self.returncode = -1
        async def wait(self): return self.returncode
    async def spawn(*args, **kwargs): return Process()
    monkeypatch.setattr(workflow.asyncio, 'create_subprocess_exec', spawn)
    class Client:
        def __init__(self, process, **kwargs): self.terminate = kwargs['terminate']
        async def close(self): await self.terminate()
    monkeypatch.setattr(workflow, 'ACPClient', Client)
    async def exercise(client, model, deadline, read, callback, claims, state, max_prompt_calls, static_summary):
        state.update(model='m', models=['m'])
        raise workflow.ACPError('remote request failed', category='rate_limited')
    monkeypatch.setattr(workflow, 'exercise', exercise)
    async def read(compose, env, endpoint):
        events.append(endpoint)
        if endpoint == 'planner': return {'version': 1, 'job_identity_sha256': 'id', 'plan': {'version': 1, 'assignments': [['a.md']]}}
        return {'reviewer': 1, 'files': [{'path': 'a.md', 'assessments': [{'claim': 'kept'}]}]}
    monkeypatch.setattr(workflow, 'read_result', read)
    result = asyncio.run(workflow.run({'a.md': 'claim'}, ['a.md'], 'm', 60))
    assert result['status'] == 'failed' and result['category'] == 'rate_limited'
    assert result['workflow']['agents'][0]['files'][0]['assessments'][0]['claim'] == 'kept'
    assert events.index('reviewer-1') < events.index('cleanup')


def test_aggregate_output_limit_is_explicit():
    with pytest.raises(workflow.ACPError, match='workflow output limit'):
        workflow.bounded({'value': 'x' * 10_800_001})


def test_planner_continues_without_replacing_session():
    class Client:
        def __init__(self): self.prompts = []
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': params['mcpServers'][0]['url'], 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            if method == 'session/prompt': self.prompts.append(params['sessionId'])
    client = Client()
    async def read(endpoint):
        if endpoint == 'planner':
            return {'plan': {'version': 1, 'assignments': [['a.md']]} if len(client.prompts) >= 2 else None}
        return {'reviewer': 1, 'files': [{'path': 'a.md', 'status': 'completed'}], 'finished': True}
    result, _, _ = asyncio.run(workflow.exercise(client, 'm', 60, read))
    assert len(client.prompts) == 2 and client.prompts[0] == client.prompts[1]
    assert result['plan']['assignments'] == [['a.md']]


def test_prompt_budget_is_shared_across_parallel_lanes():
    class Client:
        def __init__(self): self.prompts = 0
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': params['mcpServers'][0]['url'], 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            if method == 'session/prompt': self.prompts += 1
    client = Client()
    async def read(endpoint):
        if endpoint == 'planner': return {'plan': {'version': 1, 'assignments': [['a.md'], ['b.md']]}}
        return {'files': [], 'finished': False}
    state = {}
    with pytest.raises(workflow.ACPError, match='budget exhausted'):
        asyncio.run(workflow.exercise(client, 'm', 60, read, state=state, max_prompt_calls=1))
    assert client.prompts == state.get('prompt_calls', 0) == 0


def test_static_triage_runs_in_parallel_and_counts_toward_budget():
    class Client:
        def __init__(self):
            self.started = []
            self.both = asyncio.Event()
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': params['mcpServers'][0]['url'], 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            if method == 'session/prompt':
                self.started.append(params['sessionId'])
                if len(self.started) == 2: self.both.set()
                await asyncio.wait_for(self.both.wait(), 1)
    client = Client()
    async def read(endpoint):
        if endpoint == 'planner': return {'plan': {'version': 2, 'assignments': [['a.md']], 'static_triage': True}}
        if endpoint == 'reviewer-1': return {'files': [{'path': 'a.md', 'status': 'pending'}], 'finished': len(client.started) == 2}
        return {'finished': len(client.started) == 2}
    state = {}
    result, _, _ = asyncio.run(workflow.exercise(client, 'm', 60, read, state=state, max_prompt_calls=2))
    assert len(client.started) == state['prompt_calls'] == 2
    assert any('static-triage' in sid for sid in client.started) and result['static_triage']['finished']


def test_failure_cancels_other_inflight_reviewers():
    class Client:
        def __init__(self):
            self.started = asyncio.Event()
            self.cancelled = False
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': params['mcpServers'][0]['url'], 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            if method == 'session/prompt':
                if params['sessionId'].endswith('reviewer-1'):
                    await self.started.wait()
                    raise workflow.ACPError('remote request failed', category='rate_limited')
                self.started.set()
                try: await asyncio.Event().wait()
                finally: self.cancelled = True
    client = Client()
    async def read(endpoint):
        if endpoint == 'planner': return {'plan': {'version': 2, 'assignments': [['a.md'], ['b.md']], 'static_triage': False}}
        return {'files': [], 'finished': False}
    with pytest.raises(workflow.ACPError, match='remote request failed'):
        asyncio.run(workflow.exercise(client, 'm', 60, read))
    assert client.cancelled
