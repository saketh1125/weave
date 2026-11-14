import copy
import json
import pytest
from app.documentation import prompt_budget, quota, model, reviewer


def test_prefix_bound_and_invalidation():
    messages = [{'role': 'user', 'content': 'x' * 24000}]
    tools = [{'name': 'test'}]
    account = prompt_budget.record(messages, tools, 'm', {'prompt_tokens': 5000})
    appended = [{'role': 'assistant', 'content': 'thinking'}, {'role': 'tool', 'content': 'result'}]
    bound = prompt_budget.upper_bound(messages + appended, tools, 'm', account)
    assert bound == 5000 + len(json.dumps(appended, ensure_ascii=False, sort_keys=True).encode()) + 256
    assert bound + 4096 < quota.TPM
    changed = copy.deepcopy(messages); changed[0]['content'] += 'y'
    assert prompt_budget.upper_bound(changed + appended, tools, 'm', account) is None
    assert prompt_budget.upper_bound(messages, tools, 'other', account) is None
    assert prompt_budget.upper_bound(messages, [], 'm', account) is None
    assert prompt_budget.record(messages, tools, 'm', {}) is None


@pytest.mark.asyncio
async def test_quota_respects_output_and_forwards_no_hint():
    with pytest.raises(model.ProviderError, match='context_budget_exceeded'):
        await quota.request([], [], model_name='test', max_tokens=4096, prompt_tokens_upper_bound=16000)


@pytest.mark.asyncio
async def test_reviewer_persists_prefix_across_resume(monkeypatch):
    monkeypatch.setattr(reviewer, 'MAX_CALLS_PER_INVOCATION', 1)
    monkeypatch.setattr(reviewer.asyncio, 'sleep', lambda _: _no_sleep())
    snapshots = []
    async def save(s): snapshots.append(s)
    async def first(messages, tools, *, model, max_tokens):
        return {'role': 'assistant', 'content': 't' * 21000, 'tool_calls': [{'id': 'a', 'type': 'function', 'function': {'name': 'list_files', 'arguments': '{}'}}]}, {'prompt_tokens': 5000, 'completion_tokens': 5000}
    class Tools:
        async def list_files(self): return ['app.py']
    section = {'id': 's', 'path': 'README.md', 'start_line': 1, 'end_line': 1, 'content': 'Hello'}
    result = await reviewer.review_section(section, Tools(), save_state=save, complete=first)
    assert result['status'] == 'paused'
    assert result['state']['prompt_accounting']['prompt_tokens'] == 10256
    assert result['state']['messages'][-2]['content'] == 't' * 21000
    tampered = copy.deepcopy(result['state']['messages'])
    tampered[-2]['content'] += 'x'
    account = result['state']['prompt_accounting']
    assert prompt_budget.upper_bound(tampered, reviewer.TOOLS, reviewer.provider.DEFAULT_MODEL, account) is None
    assert prompt_budget.upper_bound(result['state']['messages'], [], reviewer.provider.DEFAULT_MODEL, account) is None
    assert prompt_budget.upper_bound(result['state']['messages'], reviewer.TOOLS, 'other', account) is None
    async def second(messages, tools, *, model, max_tokens, prompt_tokens_upper_bound=None):
        assert messages[-2]['content'] == 't' * 21000
        assert prompt_tokens_upper_bound + max_tokens < quota.TPM
        return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'b', 'type': 'function', 'function': {'name': 'finish_review', 'arguments': '{"reason":"no_factual_claims"}'}}]}, {}
    resumed = await reviewer.review_section(section, Tools(), state=result['state'], save_state=save, complete=second)
    assert resumed['status'] == 'completed'
    assert resumed['state']['prompt_accounting'] is None


async def _no_sleep():
    pass

@pytest.mark.asyncio
@pytest.mark.parametrize('sent', [False, True])
async def test_rate_limit_counts_only_sent_calls(sent):
    async def save(state): pass
    async def limited(messages, tools, **kwargs):
        raise model.ProviderError('rate_limited', 1, request_sent=sent)
    section = {'id': 's', 'path': 'README.md', 'start_line': 1, 'end_line': 1, 'content': 'Hello'}
    result = await reviewer.review_section(section, object(), save_state=save, complete=limited)
    assert result['status'] == 'paused'
    assert result['state']['calls'] == int(sent)
    assert (result['state']['tokens'] > 0) == sent
    if not sent:
        result['state']['next_call_at'] = 0
        async def finish(messages, tools, **kwargs):
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'b', 'type': 'function', 'function': {'name': 'finish_review', 'arguments': '{"reason":"no_factual_claims"}'}}]}, {}
        resumed = await reviewer.review_section(section, object(), state=result['state'], save_state=save, complete=finish)
        assert resumed['status'] == 'completed' and resumed['state']['calls'] == 1

@pytest.mark.asyncio
async def test_investigation_checkpoint_records_insufficient_then_resets(monkeypatch):
    monkeypatch.setattr(reviewer.asyncio, 'sleep', lambda _: _no_sleep())
    async def save(state): pass
    class Tools:
        async def list_files(self): return []
    calls = 0
    async def complete(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= 6:
            assert not kwargs.get('force_assessment')
            name, args = 'list_files', {}
        elif calls == 7:
            assert kwargs['force_assessment'] is True
            name, args = 'record_assessment', {'claim': 'Hello', 'documentation_citation': {'path': 'README.md', 'start_line': 1, 'end_line': 1}, 'verdict': 'insufficient', 'code_citations': [], 'suggested_update': '', 'rationale': 'No evidence found'}
        else:
            assert not kwargs.get('force_assessment')
            name, args = 'finish_review', {'reason': 'all_claims_reviewed'}
        return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': str(calls), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, {}
    section = {'id': 's', 'path': 'README.md', 'start_line': 1, 'end_line': 1, 'content': 'Hello'}
    result = await reviewer.review_section(section, Tools(), save_state=save, complete=complete)
    assert result['status'] == 'completed'
    assert result['assessments'][0]['verdict'] == 'insufficient'


def test_invalid_assessment_does_not_reset_investigation_count():
    messages = []
    for i in range(6):
        messages.append({'role': 'assistant', 'tool_calls': [{'id': str(i), 'function': {'name': 'read_source'}}]})
    messages.extend([{'role': 'assistant', 'tool_calls': [{'id': 'r', 'function': {'name': 'record_assessment'}}]}, {'role': 'tool', 'tool_call_id': 'r', 'content': '{"error":"invalid_tool_request_or_result"}'}])
    assert reviewer.investigation_count(messages) == 6


@pytest.mark.asyncio
async def test_forced_assessment_is_native_tool_choice(monkeypatch):
    import httpx
    monkeypatch.setenv('MISTRAL_API_KEY', 'test')
    from app import model_connections
    async def respond(request):
        payload = json.loads(request.content)
        assert payload['tool_choice'] == {'type': 'function', 'function': {'name': 'record_assessment'}}
        assert 'force_assessment' not in payload
        assert payload['tools'] == reviewer.TOOLS
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant'}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(model_connections, '_transport', lambda connection: httpx.MockTransport(respond))
    await model.complete([], reviewer.TOOLS, force_assessment=True)

@pytest.mark.asyncio
async def test_paraphrase_feedback_allows_verbatim_correction(monkeypatch):
    monkeypatch.setattr(reviewer.asyncio, 'sleep', lambda _: _no_sleep())
    async def save(state): pass
    class Tools:
        async def read_source(self, path, start_line, end_line):
            return {'path': path, 'start_line': start_line, 'end_line': end_line, 'content': 'dependencies = {}'}
    calls = 0
    async def complete(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            name, args = 'read_source', {'path': 'package.json', 'start_line': 1, 'end_line': 1}
        elif calls in (2, 3):
            if calls == 3:
                feedback = json.loads(messages[-1]['content'])
                assert feedback['reason'] == 'claim_not_verbatim'
                assert feedback['cited_documentation'] == '- **Frontend:** React, TailwindCSS, Force Graph'
            name, args = 'record_assessment', {'claim': 'Frontend stack includes TailwindCSS' if calls == 2 else 'React, TailwindCSS, Force Graph', 'documentation_citation': {'path': 'README.md', 'start_line': 19, 'end_line': 19}, 'verdict': 'conflicting', 'code_citations': [{'path': 'package.json', 'start_line': 1, 'end_line': 1}], 'suggested_update': 'Update dependencies', 'rationale': 'Dependency absent'}
        else:
            assert json.loads(messages[-1]['content'])['recorded'] is True
            name, args = 'finish_review', {'reason': 'all_claims_reviewed'}
        return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': str(calls), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, {}
    section = {'id': 's', 'path': 'README.md', 'start_line': 19, 'end_line': 19, 'content': '- **Frontend:** React, TailwindCSS, Force Graph'}
    result = await reviewer.review_section(section, Tools(), save_state=save, complete=complete)
    assert result['status'] == 'completed'
    assert result['assessments'][0]['claim'] == 'React, TailwindCSS, Force Graph'

@pytest.mark.asyncio
async def test_unread_citation_temporarily_allows_source_read(monkeypatch):
    monkeypatch.setattr(reviewer.asyncio, 'sleep', lambda _: _no_sleep())
    async def save(state): pass
    class Tools:
        async def list_files(self): return ['app.py']
        async def read_source(self, path, start_line, end_line):
            return {'path': path, 'start_line': start_line, 'end_line': end_line, 'content': 'ready = True'}
    calls = 0
    item = {'claim': 'Hello', 'documentation_citation': {'path': 'README.md', 'start_line': 1, 'end_line': 1}, 'verdict': 'supported', 'code_citations': [{'path': 'app.py', 'start_line': 1, 'end_line': 1}], 'suggested_update': '', 'rationale': 'Evidence read'}
    async def complete(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= 6:
            name, args = 'list_files', {}
        elif calls == 7:
            assert kwargs.get('force_assessment') is True
            name, args = 'record_assessment', item
        elif calls == 8:
            assert not kwargs.get('force_assessment')
            feedback = json.loads(messages[-1]['content'])
            assert feedback['reason'] == 'unread_source_citation' and 'read_source' in feedback['hint']
            name, args = 'read_source', {'path': 'app.py', 'start_line': 1, 'end_line': 1}
        elif calls == 9:
            assert kwargs.get('force_assessment') is True
            name, args = 'record_assessment', item
        else:
            name, args = 'finish_review', {'reason': 'all_claims_reviewed'}
        return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': str(calls), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, {}
    section = {'id': 's', 'path': 'README.md', 'start_line': 1, 'end_line': 1, 'content': 'Hello'}
    result = await reviewer.review_section(section, Tools(), save_state=save, complete=complete)
    assert result['status'] == 'completed' and len(result['assessments']) == 1
