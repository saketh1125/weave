import hashlib
import json
import pytest
from app.documentation import agent_review, model
from app import antigravity_bridge, model_preferences

SECTION = {'id': 's', 'path': 'README.md', 'start_line': 1, 'end_line': 1, 'content': 'It works.'}
SNAPSHOT = {'README.md': 'It works.', 'app.py': 'works = True'}
ASSESSMENT = {'claim': 'It works.', 'documentation_citation': {'path': 'README.md', 'start_line': 1, 'end_line': 1}, 'verdict': 'supported', 'code_citations': [{'path': 'app.py', 'start_line': 1, 'end_line': 1}], 'suggested_update': '', 'rationale': 'Implemented.'}

def report():
    digest = hashlib.sha256(json.dumps({'snapshot': SNAPSHOT, 'section': SECTION, 'limits': {'max_steps': 64, 'max_output_bytes': 65536}}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    binding = {'adapter': 'antigravity-native-acp-v1', 'runtime': '1.5.8', 'model': 'm', 'job_sha256': digest, 'prompt_sha256': 'b' * 64, 'settings_sha256': 'c' * 64, 'deadline_seconds': 120, 'native_session_resumable': False}
    execution = {**binding, 'sha256': hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()}
    return {'status': 'passed', 'model': 'm', 'identity': 'binding', 'models': ['m'], 'execution': execution, 'review': {'section_id': 's', 'finished': True, 'job_identity_sha256': digest, 'read_ranges': [{'path': 'app.py', 'start_line': 1, 'end_line': 1}], 'assessments': [ASSESSMENT]}}

@pytest.mark.asyncio
async def test_agent_result_validates_evidence_and_binds_identity(monkeypatch):
    async def bridge(snapshot, section, selected, expected_identity=None):
        assert snapshot == SNAPSHOT and section == SECTION and selected == 'm' and expected_identity == 'binding'
        return report()
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    result = await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding')
    assert result['status'] == 'completed' and result['assessments'] == [ASSESSMENT]

@pytest.mark.asyncio
async def test_agent_rejects_unread_or_out_of_snapshot_citations(monkeypatch):
    async def bridge(*args, **kwargs):
        result = report()
        result['review']['read_ranges'][0]['end_line'] = 2
        return result
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    assert (await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding'))['status'] == 'failed'

@pytest.mark.asyncio
async def test_incomplete_agent_run_pauses_without_reusing_session(monkeypatch):
    async def bridge(*args, **kwargs): return {'status': 'incomplete', 'model': 'm', 'identity': 'binding'}
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    assert (await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding')) == {'status': 'paused', 'reason': 'agent_incomplete'}

@pytest.mark.asyncio
@pytest.mark.parametrize('details, status, reason', [
    ({'category': 'rate_limited'}, 'paused', 'rate_limited'),
    ({'reason': 'transport deadline exceeded'}, 'paused', 'agent_timeout'),
    ({'category': 'provider_connection_failed'}, 'failed', 'agent_connection_failed'),
    ({'reason': 'fixture-secret', 'category': ['fixture-secret']}, 'failed', 'agent_failed'),
])
async def test_native_failures_keep_safe_diagnostics_without_accepting_evidence(monkeypatch, details, status, reason):
    async def bridge(*args, **kwargs):
        return {'status': 'failed', 'model': 'm', 'identity': 'binding', 'stage': 'acp_session',
                'rpcErrorCode': -32603, 'review': {'assessments': [ASSESSMENT]}, **details}
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    result = await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding')
    assert result['status'] == status and result['reason'] == reason
    assert result['state']['failure'] == {'reason': reason, 'stage': 'acp_session', 'rpc_error_code': -32603}
    assert 'assessments' not in result and 'fixture-secret' not in json.dumps(result)

@pytest.mark.asyncio
async def test_failure_does_not_bypass_identity_binding(monkeypatch):
    async def bridge(*args, **kwargs):
        return {'status': 'failed', 'model': 'other', 'identity': 'binding', 'category': 'rate_limited'}
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    assert (await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding'))['reason'] == 'invalid_agent_result'


@pytest.mark.asyncio
async def test_validated_partial_claims_survive_timeout(monkeypatch):
    async def bridge(*args, **kwargs):
        result = report()
        result.update(status='failed', reason='transport deadline exceeded')
        result['review']['finished'] = False
        return result
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    result = await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding')
    assert result['status'] == 'paused' and result['reason'] == 'agent_timeout'
    assert result['assessments'] == result['state']['assessments'] == [ASSESSMENT]


@pytest.mark.asyncio
async def test_live_checkpoint_saves_evidence_before_native_session_finishes(monkeypatch):
    states = []
    async def bridge(*args, on_progress, **kwargs):
        result = report()
        result.update(status='incomplete', activity={'steps': 5, 'errors': 0, 'claims_recorded': 1})
        result['review']['finished'] = False
        await on_progress(result)
        assert len(states) == 1 and states[0]['assessments'] == [ASSESSMENT]
        result.update(status='failed', reason='transport deadline exceeded')
        return result
    async def save(state): states.append(state)
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    result = await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding', save_state=save)
    assert result['status'] == 'paused'
    assert states[0]['activity']['steps'] == 5 and states[0]['activity']['phase'] == 'investigating'


@pytest.mark.asyncio
async def test_live_checkpoint_rejects_forged_evidence(monkeypatch):
    async def bridge(*args, on_progress, **kwargs):
        result = report()
        result.update(status='incomplete', activity={'steps': 1, 'errors': 0, 'claims_recorded': 1})
        result['review']['read_ranges'] = []
        await on_progress(result)
    async def forbidden(state): raise AssertionError('unvalidated evidence must not be saved')
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    from app.model_connections import ProviderError
    with pytest.raises(ProviderError):
        await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding', save_state=forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize('errors, expected', [(0, 'completed'), (1, 'failed')])
async def test_claimless_completion_requires_explicit_error_free_finish(monkeypatch, errors, expected):
    async def bridge(*args, **kwargs):
        result = report()
        result['review'].update(assessments=[], no_factual_claims=True, errors=errors, reason='Heading only.')
        return result
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    assert (await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding'))['status'] == expected

@pytest.mark.asyncio
async def test_review_accepts_bound_five_minute_execution(monkeypatch):
    async def bridge(*args, **kwargs):
        result = report()
        binding = {**result['execution'], 'deadline_seconds': 300}
        binding.pop('sha256')
        result['execution'] = {**binding, 'sha256': hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()}
        return result
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    assert (await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding'))['status'] == 'completed'

def test_agent_settings_bind_model_and_runtime(monkeypatch):
    monkeypatch.setenv('SENTRA_ANTIGRAVITY_REVIEW_TIMEOUT_SECONDS', '600')
    monkeypatch.setattr(model_preferences, 'get_review_agent_selection', lambda: {'model': 'm', 'identity': 'binding'})
    settings = model.review_settings()
    assert settings['kind'] == 'antigravity' and settings['identity'] == 'binding'
    run = type('Run', (), {'manifest': {'model_settings': settings}})()
    assert model.compatible_review(run, settings)
    assert not model.compatible_review(run, {**settings, 'identity': 'changed'})
    monkeypatch.setenv('SENTRA_ANTIGRAVITY_REVIEW_TIMEOUT_SECONDS', '900')
    assert not model.compatible_review(run, model.review_settings())


def test_review_response_exposes_safe_activity_without_inflating_completed_counts():
    from types import SimpleNamespace
    from app.routers.documentation import render
    run = SimpleNamespace(id='run', job_id=None, commit_sha='abc', status='paused', model='m', reason='agent_timeout',
        manifest={'documentation_files': ['README.md'], 'unreadable_files': [], 'scope': 'indexed'})
    task = SimpleNamespace(status='pending', path='README.md', start_line=1, end_line=4, assessments=[],
        state={'failure': {'steps': 37, 'errors': 0, 'reason': 'agent_timeout', 'secret': 'not public'}})
    result = render(run, [task])
    assert result['activity'] == {'path': 'README.md', 'start_line': 1, 'end_line': 4, 'steps': 37,
        'errors': 0, 'claims_recorded': 0, 'updated_at': 0, 'phase': 'paused'}
    assert result['coverage']['sections_completed'] == result['coverage']['claims_assessed'] == 0
    assert 'not public' not in json.dumps(result)


def test_resumed_claims_are_deduplicated_by_documented_claim_and_citation():
    from app.documentation.service import merge_assessments
    revised = {**ASSESSMENT, 'rationale': 'More direct evidence.'}
    assert merge_assessments([ASSESSMENT], [revised]) == [revised]

@pytest.mark.asyncio
@pytest.mark.parametrize('tamper', ['digest', 'execution', 'empty', 'ranges', 'model', 'deadline'])
async def test_agent_rejects_unbound_or_incomplete_results(monkeypatch, tamper):
    async def bridge(*args, **kwargs):
        result = report()
        if tamper == 'digest': result['review']['job_identity_sha256'] = '0' * 64
        if tamper == 'execution': result['execution']['model'] = 'other'
        if tamper == 'empty': result['review']['assessments'] = []
        if tamper == 'ranges': result['review']['read_ranges'] *= 513
        if tamper == 'model': result['models'] = ['other']
        if tamper == 'deadline':
            binding = {**result['execution'], 'deadline_seconds': 901}
            binding.pop('sha256')
            result['execution'] = {**binding, 'sha256': hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()}
        return result
    monkeypatch.setattr(antigravity_bridge, 'review', bridge)
    assert (await agent_review.review_section(SNAPSHOT, SECTION, 'm', 'binding'))['status'] == 'failed'

@pytest.mark.integration
def test_persisted_legacy_agent_sections_resume_without_planner(monkeypatch):
    import asyncio
    import os
    import uuid
    from datetime import datetime, timedelta, timezone
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.documentation import service, planner
    from app.models import Repository, Job, DocumentationReview, DocumentationReviewTask

    url = os.environ.get('SENTRA_TEST_DATABASE_URL')
    if not url:
        pytest.skip('SENTRA_TEST_DATABASE_URL is not configured')
    async def exercise():
        engine = create_async_engine(make_url(url).set(drivername='postgresql+asyncpg'), poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(service, 'SessionLocal', sessions)
        monkeypatch.setattr(model_preferences, 'get_review_agent_selection', lambda: {'model': 'm', 'identity': 'binding'})
        current_settings = model.review_settings
        monkeypatch.setattr(model, 'review_settings', lambda: {key: value for key, value in current_settings().items() if key != 'workflow'})
        async def no_planner(*args, **kwargs): raise AssertionError('planner must not run')
        monkeypatch.setattr(planner, 'create_plan', no_planner)
        rid, jid, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ids = [uuid.uuid4(), uuid.uuid4()]
        calls = []
        prior_claim_counts = []
        async def run_agent(snapshot, section, selected, identity, save_state=None):
            calls.append(section['id'])
            prior_claim_counts.append(len(section['reviewed_claims']))
            await save_state({'activity': {'steps': 7, 'errors': 0, 'claims_recorded': 1,
                                          'phase': 'investigating', 'updated_at': 1700000000}, 'assessments': [ASSESSMENT]})
            async with sessions() as session:
                checkpoint = await session.get(DocumentationReviewTask, uuid.UUID(section['id']))
                assert checkpoint.state['activity']['steps'] == 7 and len(checkpoint.assessments) == 1
            if section['id'] == str(ids[1]) and calls.count(str(ids[1])) == 1:
                return {'status': 'paused', 'reason': 'agent_timeout',
                        'state': {'agent_version': 1, 'failure': {'reason': 'agent_timeout', 'stage': 'acp_session'}}}
            return {'status': 'completed', 'assessments': [ASSESSMENT], 'state': {'agent_version': 1, 'job_identity_sha256': 'a' * 64}}
        monkeypatch.setattr(agent_review, 'review_section', run_agent)
        try:
            async with sessions() as session:
                session.add(Repository(id=rid, name='agent-test', remote_url='https://example.invalid/repo', status='ready', commit_sha='abc'))
                await session.flush()
                session.add(Job(id=jid, repo_id=rid, kind='documentation_review', payload={'run_id': str(run_id)}, status='running', attempts=1, lease_token=uuid.uuid4(), lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=5)))
                await session.flush()
                session.add(DocumentationReview(id=run_id, repo_id=rid, job_id=jid, commit_sha='abc', status='queued', model='m', snapshot=SNAPSHOT, manifest={'unreadable_files': [], 'model_settings': model.review_settings()}))
                await session.flush()
                for i, task_id in enumerate(ids):
                    session.add(DocumentationReviewTask(id=task_id, run_id=run_id, path=f'{i}.md', start_line=1, end_line=1, content='It works.', status='pending', state={}, assessments=[]))
                await session.commit()
                job = await session.get(Job, jid)
            assert (await service.run_review(job))['status'] == 'paused'
            async with sessions() as session:
                review = await session.get(DocumentationReview, run_id)
                first, second = [await session.get(DocumentationReviewTask, task_id) for task_id in ids]
                assert review.manifest['review_plan']['assignments'] == [[str(i) for i in ids]]
                assert first.status == 'completed' and len(first.assessments) == 1 and second.status == 'pending'
                assert review.reason == 'agent_timeout'
                assert second.state['failure'] == {'reason': 'agent_timeout', 'stage': 'acp_session'}
                assert len(second.assessments) == 1 and second.state['activity']['phase'] == 'paused'
            assert (await service.run_review(job))['status'] == 'completed'
            assert calls == [str(ids[0]), str(ids[1]), str(ids[1])]
            assert prior_claim_counts == [0, 0, 1]
            async with sessions() as session:
                second = await session.get(DocumentationReviewTask, ids[1])
                assert second.status == 'completed' and len(second.assessments) == 1
        finally:
            async with sessions() as session:
                repo = await session.get(Repository, rid)
                if repo: await session.delete(repo)
                await session.commit()
            await engine.dispose()
    asyncio.run(exercise())
