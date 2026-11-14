import json
from types import SimpleNamespace
import pytest
from app.documentation import planner, quota, model


def test_plan_requires_exact_disjoint_coverage():
    assert planner.validate_plan({'assignments': [['a'], ['b']]}, ['a', 'b'])['version'] == 1
    for groups in ([['a'], ['a']], [['a']], [['a', 'b'], []], [['a'], ['b'], ['c']]):
        with pytest.raises(ValueError):
            planner.validate_plan({'assignments': groups}, ['a', 'b'])


@pytest.mark.asyncio
async def test_planner_calls_model_with_inventory():
    async def complete(messages, tools, **kwargs):
        inventory = json.loads(messages[1]['content'])
        assert inventory == {'files': ['README.md'], 'sections': [['0', 0, 1, 2]]}
        return {'role': 'assistant', 'tool_calls': [{'id': 'call-1', 'type': 'function',
                'function': {'name': 'assign_sections', 'arguments': '{"assignments":[["0"]]}'}}]}, {}
    task = SimpleNamespace(id='a', path='README.md', start_line=1, end_line=2)
    assert (await planner.create_plan([task], 'model', complete))['assignments'] == [['a']]


@pytest.mark.asyncio
async def test_compact_inventory_maps_short_ids_back_to_uuid_assignments():
    tasks = [SimpleNamespace(id=f'uuid-{i:04}', path='docs/guide.md', start_line=i * 10 + 1, end_line=i * 10 + 5)
             for i in range(200)]
    seen = {}

    async def complete(messages, tools, **kwargs):
        inventory = json.loads(messages[1]['content'])
        seen['inventory'] = inventory
        # The repeated path is transmitted once and each section uses a short ordinal.
        assert inventory['files'] == ['docs/guide.md']
        assert inventory['sections'][0] == ['0', 0, 1, 5]
        assert inventory['sections'][-1] == ['199', 0, 1991, 1995]
        return {'role': 'assistant', 'tool_calls': [{'id': 'call-1', 'type': 'function',
                'function': {'name': 'assign_sections',
                'arguments': json.dumps({'assignments': [[str(i) for i in range(100)],
                                                         [str(i) for i in range(100, 200)]]})}}]}, {}

    result = await planner.create_plan(tasks, 'model', complete)
    assert result['version'] == 1
    assert result['assignments'] == [[f'uuid-{i:04}' for i in range(100)],
                                     [f'uuid-{i:04}' for i in range(100, 200)]]
    assert len(json.dumps(seen['inventory'], separators=(',', ':')).encode()) < 10000


@pytest.mark.asyncio
@pytest.mark.parametrize(('bad_groups', 'feedback_text'), [
    ([['0', '0'], ['1']], 'duplicate IDs'),
    ([['0'], []], 'nonempty'),
])
async def test_planner_repairs_invalid_tool_plan_once_with_protocol_feedback(bad_groups, feedback_text):
    tasks = [SimpleNamespace(id=f'uuid-{i}', path='README.md', start_line=i + 1, end_line=i + 1)
             for i in range(2)]
    calls = 0

    async def complete(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {'role': 'assistant', 'content': None, 'reasoning_content': 'preserve me',
                    'tool_calls': [{'id': 'call-repair', 'type': 'function',
                                    'function': {'name': 'assign_sections',
                                                 'arguments': json.dumps({'assignments': bad_groups})}}]}, {}
        assert messages[2]['role'] == 'assistant'
        assert messages[2]['reasoning_content'] == 'preserve me'
        assert messages[3]['role'] == 'tool'
        assert messages[3]['tool_call_id'] == 'call-repair'
        tool_feedback = json.loads(messages[3]['content'])
        assert tool_feedback['error'] == 'invalid_plan'
        assert feedback_text in tool_feedback['feedback']
        return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-valid', 'type': 'function',
                'function': {'name': 'assign_sections',
                             'arguments': '{"assignments":[["0"],["1"]]}'}}]}, {}

    result = await planner.create_plan(tasks, 'model', complete)
    assert calls == 2
    assert result['assignments'] == [['uuid-0'], ['uuid-1']]


@pytest.mark.asyncio
async def test_planner_rejects_second_invalid_plan_without_fallback():
    tasks = [SimpleNamespace(id=f'uuid-{i}', path='README.md', start_line=i + 1, end_line=i + 1)
             for i in range(2)]
    calls = 0

    async def complete(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'call-{calls}', 'type': 'function',
                'function': {'name': 'assign_sections',
                             'arguments': '{"assignments":[["0","0"],["1"]]}'}}]}, {}

    with pytest.raises(ValueError, match='invalid_plan'):
        await planner.create_plan(tasks, 'model', complete)
    assert calls == 2


@pytest.mark.asyncio
async def test_planner_rejects_inventory_over_request_budget_before_provider_call():
    task = SimpleNamespace(id='a', path='docs/' + 'x' * 17000 + '.md', start_line=1, end_line=2)
    called = False

    async def complete(*args, **kwargs):
        nonlocal called
        called = True

    with pytest.raises(ValueError, match='inventory_too_large'):
        await planner.create_plan([task], 'model', complete)
    assert called is False



@pytest.mark.asyncio
async def test_quota_reconciles_only_matching_window(monkeypatch):
    from app import db
    from app.documentation import quota, model
    import sys
    from sqlalchemy import Column, String
    from sqlalchemy.orm import declarative_base
    base = declarative_base()
    class Quota(base):
        __tablename__ = 'test_quota'
        id = Column(String, primary_key=True)
    monkeypatch.setitem(sys.modules, 'app.models', SimpleNamespace(DocumentationProviderQuota=Quota))
    row = SimpleNamespace(window_start=0.0, next_call_at=0.0, blocked_until=0.0, reserved_requests=0, reserved_tokens=0)
    class Result:
        def scalar_one(self): return row
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, statement): return Result()
        async def commit(self): pass
    monkeypatch.setattr(db, 'SessionLocal', Session)
    from app import model_quota
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def no_slot(connection):
        yield
    monkeypatch.setattr(model_quota, '_concurrency_slot', no_slot)
    monkeypatch.setattr(quota, 'INTERVAL', 0)
    async def complete(*args, **kwargs):
        return {'role': 'assistant'}, {'total_tokens': 12}
    monkeypatch.setattr(__import__('app.model_connections', fromlist=['complete']), 'complete', complete)
    await quota.request([], [], model_name='test', max_tokens=100)
    assert row.reserved_requests == 1 and row.reserved_tokens == 12
    async def crossed(*args, **kwargs):
        row.window_start += 60
        row.reserved_tokens = 3
        return {'role': 'assistant'}, {'prompt_tokens': 1, 'completion_tokens': 1}
    monkeypatch.setattr(__import__('app.model_connections', fromlist=['complete']), 'complete', crossed)
    await quota.request([], [], model_name='test', max_tokens=100)
    assert row.reserved_requests == 2 and row.reserved_tokens == 3
