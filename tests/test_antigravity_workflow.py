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


def test_planner_monitor_does_not_emit_plan_without_agents():
    class Client:
        def __init__(self):
            self.submitted = False
            self.observed = asyncio.Event()
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': params['mcpServers'][0]['url'], 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            if method == 'session/prompt' and params['sessionId'].endswith('planner'):
                self.submitted = True
                await asyncio.wait_for(self.observed.wait(), 1)
    client = Client()
    async def read(endpoint):
        if endpoint == 'planner':
            if client.submitted:
                client.observed.set()
                return {'plan': {'version': 2, 'assignments': [['a.md']], 'static_triage': False}}
            return {'plan': None}
        return {'reviewer': 1, 'files': [{'path': 'a.md', 'status': 'completed'}], 'finished': True}
    updates = []
    asyncio.run(workflow.exercise(client, 'm', 60, read, lambda state, *_: updates.append(state)))
    assert any(update['plan'] is not None for update in updates)
    assert all(update['plan'] is None or len(update['agents']) == len(update['plan']['assignments']) for update in updates)


def test_reviewer_can_continue_past_three_turns_and_reports_activity():
    class Client:
        def __init__(self): self.turns = 0
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': 's', 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            if method == 'session/prompt':
                self.turns += 1
                return {'stopReason': 'max_tokens'}
    client = Client()
    async def read(endpoint):
        if endpoint == 'planner': return {'plan': {'assignments': [['a.md']]}}
        return {'files': [], 'finished': client.turns >= 4}
    result, _, _ = asyncio.run(workflow.exercise(client, 'm', 60, read, max_prompt_calls=4))
    assert client.turns == 4
    assert result['agents'][0]['runtime_activity']['prompt_turns'] == 4
    assert result['agents'][0]['runtime_activity']['phase'] == 'finished'


def test_unchanged_end_turns_stop_with_explicit_category():
    class Client:
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': 's', 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            return {'stopReason': 'end_turn'}
    async def read(endpoint):
        if endpoint == 'planner': return {'plan': {'assignments': [['a.md']]}}
        return {'files': [], 'finished': False}
    with pytest.raises(workflow.ACPError) as error:
        asyncio.run(workflow.exercise(Client(), 'm', 60, read))
    assert error.value.category == 'no_progress'


def test_activity_counter_changes_do_not_count_as_review_progress():
    class Client:
        def __init__(self): self.turns = 0
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': 's', 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            if method == 'session/prompt':
                self.turns += 1
                return {'stopReason': 'end_turn'}
    client = Client()
    async def read(endpoint):
        if endpoint == 'planner': return {'plan': {'assignments': [['a.md']]}}
        return {'files': [{'path': 'a.md', 'status': 'pending', 'assessments': []}], 'read_ranges': [], 'finished': False,
                'activity': {'steps': client.turns, 'errors': client.turns},
                'runtime_activity': {'events': client.turns, 'prompt_turns': client.turns}}
    with pytest.raises(workflow.ACPError) as error:
        asyncio.run(workflow.exercise(client, 'm', 60, read))
    assert error.value.category == 'no_progress' and client.turns == 3


def test_source_range_growth_counts_as_review_progress():
    class Client:
        def __init__(self): self.turns = 0
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': 's', 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            if method == 'session/prompt':
                self.turns += 1
                return {'stopReason': 'end_turn'}
    client = Client()
    async def read(endpoint):
        if endpoint == 'planner': return {'plan': {'assignments': [['a.md']]}}
        ranges = [{'path': 'code.py', 'start_line': 1, 'end_line': n} for n in range(1, client.turns + 1)]
        return {'files': [{'path': 'a.md', 'status': 'pending', 'assessments': []}], 'read_ranges': ranges,
                'finished': client.turns >= 4, 'activity': {'steps': client.turns, 'errors': 0}}
    result, _, _ = asyncio.run(workflow.exercise(client, 'm', 60, read, max_prompt_calls=4))
    assert client.turns == 4 and result['agents'][0]['finished'] is True


def test_parallel_lanes_get_initial_turn_before_continuations():
    class Client:
        def __init__(self): self.calls = []
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': params['mcpServers'][0]['url'], 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            if method == 'session/prompt':
                self.calls.append(params['sessionId'])
                return {'stopReason': 'max_tokens'}
    client = Client()
    async def read(endpoint):
        if endpoint == 'planner': return {'plan': {'assignments': [['a.md'], ['b.md']]}}
        return {'files': [], 'finished': False}
    with pytest.raises(workflow.ACPError) as error:
        asyncio.run(workflow.exercise(client, 'm', 60, read, max_prompt_calls=2))
    assert error.value.category == 'budget_exhausted'
    assert len(client.calls) == 2 and len(set(client.calls)) == 2


def test_refusal_stops_without_consuming_remaining_budget():
    class Client:
        def __init__(self): self.prompts = 0
        async def request(self, method, params):
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': 's', 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]}]}
            self.prompts += 1
            return {'stopReason': 'refusal'}
    client = Client()
    async def read(endpoint):
        if endpoint == 'planner': return {'plan': {'assignments': [['a.md']]}}
        return {'files': [], 'finished': False}
    with pytest.raises(workflow.ACPError) as error:
        asyncio.run(workflow.exercise(client, 'm', 60, read))
    assert error.value.category == 'refusal' and client.prompts == 1


def test_sessions_select_default_mode_when_offered_and_prompts_restrict_tools():
    class Client:
        def __init__(self): self.calls = []
        async def request(self, method, params):
            self.calls.append((method, params))
            if method == 'initialize': return {'protocolVersion': 1}
            if method == 'session/new': return {'sessionId': 's', 'configOptions': [
                {'id': 'model', 'currentValue': 'm', 'options': [{'value': 'm'}]},
                {'id': 'mode', 'currentValue': 'other', 'options': [{'value': 'default'}]}]}
    async def read(endpoint):
        if endpoint == 'planner': return {'plan': {'assignments': [['a.md']]}}
        return {'files': [], 'finished': True}
    client = Client()
    asyncio.run(workflow.exercise(client, 'm', 60, read))
    assert any(method == 'session/set_config_option' and params['configId'] == 'mode' and params['value'] == 'default'
               for method, params in client.calls)
    for prompt in (workflow.PLANNER, workflow.REVIEWER, workflow.TRIAGE):
        assert 'only the provided sentra MCP tools' in prompt
        assert 'native agent delegation' in prompt
    assert 'submit_plan is the Weave reviewer allocation mechanism; call it directly' in workflow.PLANNER
