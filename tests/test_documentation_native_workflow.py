"""Native workflow result validation and lease-fenced coordinator regression tests."""
import asyncio
import copy
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.documentation import native_result

SNAPSHOT = {'a.md': 'It works.', 'b.md': 'A second claim.', 'code.py': 'works = True'}
PATHS = ['a.md', 'b.md']
PLAN = {'version': 2, 'assignments': [['a.md'], ['b.md']], 'static_triage': False}
STATIC = {'version': 1, 'fingerprint': 'f' * 64, 'items': [], 'counts': {'aligned': 0, 'outdated': 0, 'unverified': 0}, 'triage_threshold': 20}
ASSESSMENT = {'claim': 'It works.', 'documentation_citation': {'path': 'a.md', 'start_line': 1, 'end_line': 1},
              'verdict': 'supported', 'code_citations': [{'path': 'code.py', 'start_line': 1, 'end_line': 1}],
              'suggested_update': '', 'rationale': 'Implemented.'}


def result(status='passed', b_status='completed', static=STATIC, plan=PLAN):
    identity = native_result.job_identity(SNAPSHOT, PATHS, static)
    binding = {'adapter': 'antigravity-native-workflow-v1', 'runtime': '1.5.8', 'model': 'm', 'job_sha256': identity,
               'prompt_sha256': 'a' * 64, 'settings_sha256': 'b' * 64, 'deadline_seconds': 600,
               'native_session_resumable': False, 'plan_sha256': native_result.digest(plan)}
    files = [{'path': 'a.md', 'status': 'completed', 'assessments': [copy.deepcopy(ASSESSMENT)], 'no_factual_claims': False, 'reason': None},
             {'path': 'b.md', 'status': b_status, 'assessments': [], 'no_factual_claims': b_status == 'completed', 'reason': 'No factual claims.' if b_status == 'completed' else None}]
    agents = [{'reviewer': i + 1, 'files': [file], 'read_ranges': [{'path': 'code.py', 'start_line': 1, 'end_line': 1}] if i == 0 else [],
               'activity': {'steps': 1, 'errors': 0, 'claims_recorded': 1 if i == 0 else 0}}
              for i, file in enumerate(files)]
    return {'status': status, 'model': 'm', 'models': ['m'], 'identity': 'binding',
            'workflow': {'version': 1, 'job_identity_sha256': identity, 'plan': copy.deepcopy(plan), 'agents': agents},
            'metrics': {'prompt_calls': 3, 'elapsed_seconds': 1.0, 'model_calls': None, 'input_tokens': None, 'output_tokens': None},
            'execution': {**binding, 'sha256': native_result.digest(binding)}}


def validate(payload, completed=()):
    return native_result.validate_result(payload, SNAPSHOT, PATHS, 'm', 'binding', 600, completed, static_review=STATIC)


def test_native_result_accepts_two_reviewers():
    assert validate(result())['plan'] == PLAN


@pytest.mark.parametrize('mutation', ['duplicate', 'foreign_file', 'unread_citation', 'pending_passed', 'bad_digest', 'wrong_doc'])
def test_native_result_rejects_invalid_evidence(mutation):
    payload = result()
    workflow = payload['workflow']
    if mutation == 'duplicate':
        workflow['agents'][1]['files'][0]['path'] = 'a.md'
    elif mutation == 'foreign_file':
        workflow['agents'][1]['files'][0]['path'] = 'foreign.md'
    elif mutation == 'unread_citation':
        workflow['agents'][0]['read_ranges'] = []
    elif mutation == 'pending_passed':
        workflow['agents'][1]['files'][0]['status'] = 'pending'
    elif mutation == 'bad_digest':
        payload['execution']['sha256'] = '0' * 64
    else:
        workflow['agents'][0]['files'][0]['assessments'][0]['documentation_citation']['path'] = 'b.md'
    with pytest.raises(ValueError):
        validate(payload)


URL = os.environ.get('SENTRA_TEST_DATABASE_URL')


@pytest.mark.integration
@pytest.mark.skipif(not URL, reason='SENTRA_TEST_DATABASE_URL is not configured')
@pytest.mark.parametrize('scenario', ['complete', 'resume', 'invalid', 'incomplete', 'lost_lease', 'stale_doc', 'stale_code', 'budget', 'exhausted', 'claimless_resume', 'runtime', 'budget_stop', 'no_progress', 'triage_progress'])
def test_native_coordinator_persists_fenced_results(monkeypatch, scenario):
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app import antigravity_bridge
    from app.documentation import native_service, model, hybrid
    from app.models import Repository, Job, DocumentationReview, DocumentationReviewTask

    async def run():
        engine = create_async_engine(make_url(URL).set(drivername='postgresql+asyncpg'), poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(native_service, 'SessionLocal', sessions)
        monkeypatch.setattr(model, 'compatible_review', lambda *_: True)
        monkeypatch.setattr(model, 'review_settings', lambda: {})
        repo_id, job_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        settings = {'model': 'm', 'identity': 'binding', 'deadline_seconds': 600, 'max_prompt_calls': 100}
        manifest = {'model_settings': settings, 'unreadable_files': []}
        if scenario in ('resume', 'budget', 'claimless_resume'):
            manifest['native_plan'] = copy.deepcopy(PLAN)
        if scenario in ('budget', 'exhausted'):
            manifest['native_cost'] = {'prompt_calls': 100 if scenario == 'exhausted' else 4, 'elapsed_seconds': 0, 'model_calls': None, 'input_tokens': None, 'output_tokens': None}
        current_snapshot = dict(SNAPSHOT)
        if scenario in ('stale_doc', 'stale_code'):
            manifest['static_review'] = hybrid.static_review(SNAPSHOT, PATHS, 'abc', settings)
            current_snapshot['a.md' if scenario == 'stale_doc' else 'code.py'] += ' changed'
        if scenario == 'triage_progress':
            report = {'version': 1, 'fingerprint': hybrid.review_fingerprint(SNAPSHOT, PATHS, 'abc', settings),
                'items': [{'id': str(i), 'status': 'unverified', 'claim': 'It works.',
                           'doc': {'file': 'a.md', 'start_line': 1, 'end_line': 1}} for i in range(20)],
                'counts': {'aligned': 0, 'outdated': 0, 'unverified': 20}, 'triage_threshold': 20}
            monkeypatch.setattr(hybrid, 'static_review', lambda *_: report)
        called = []
        async def bridge(snapshot, paths, selected, identity, **kwargs):
            called.append(kwargs)
            async with sessions() as session:
                saved = await session.get(DocumentationReview, run_id)
                assert saved.manifest.get('static_review') == kwargs['static_review']
            report = kwargs.get('static_review', STATIC)
            payload = result('incomplete', 'pending', report) if scenario in ('incomplete', 'budget_stop', 'no_progress') else result(static=report)
            if scenario == 'triage_progress':
                payload = result('incomplete', 'pending', report, {**PLAN, 'static_triage': True})
                payload['workflow']['static_triage'] = {'reviewer': 3, 'finding_ids': [str(i) for i in range(20)],
                    'processed_ids': ['0'], 'assessments': [{'finding_id': '0', 'assessment': copy.deepcopy(ASSESSMENT)}],
                    'read_ranges': [{'path': 'code.py', 'start_line': 1, 'end_line': 1}],
                    'activity': {'steps': 1, 'errors': 0, 'claims_recorded': 1}, 'finished': False}
            if scenario in ('budget_stop', 'no_progress'):
                payload['category'] = 'budget_exhausted' if scenario == 'budget_stop' else 'no_progress'
            if scenario == 'runtime':
                activity = {'events': 2, 'last_event_at': 123.0, 'phase': 'responding', 'prompt_turns': 1, 'last_stop_reason': None}
                payload['workflow']['runtime_activity'] = activity
                payload['workflow']['agents'][0]['runtime_activity'] = activity
                await kwargs['on_progress'](payload)
                async with sessions() as check:
                    rows = (await check.execute(select(DocumentationReviewTask).where(DocumentationReviewTask.run_id == run_id).order_by(DocumentationReviewTask.path))).scalars().all()
                    original_activity_at = rows[0].state['activity']['updated_at']
                payload['workflow']['agents'][1]['activity']['steps'] += 1
                await kwargs['on_progress'](payload)
                async with sessions() as check:
                    rows = (await check.execute(select(DocumentationReviewTask).where(DocumentationReviewTask.run_id == run_id).order_by(DocumentationReviewTask.path))).scalars().all()
                    assert rows[0].state['activity']['updated_at'] == original_activity_at
            if scenario in ('resume', 'budget'):
                assert kwargs['completed_files'] == ['a.md'] and kwargs['plan'] == PLAN
                payload['workflow']['agents'][0]['files'][0]['assessments'] = []
            if scenario == 'claimless_resume':
                assert kwargs['completed_files'] == ['b.md']
                payload['workflow']['agents'][1]['files'][0].update(no_factual_claims=False, reason=None)
            if scenario == 'budget':
                assert kwargs['max_prompt_calls'] == 96
                payload['metrics']['prompt_calls'] = 1
                await kwargs['on_progress'](payload)
                payload['metrics']['prompt_calls'] = 2
                await kwargs['on_progress'](payload)
                return payload
            if scenario == 'invalid':
                payload['workflow']['agents'][0]['read_ranges'] = []
            if scenario == 'lost_lease':
                async with sessions() as session:
                    row = await session.get(Job, job_id)
                    row.lease_token = uuid.uuid4()
                    await session.commit()
            await kwargs['on_progress'](payload)
            if scenario == 'triage_progress':
                async with sessions() as check:
                    saved = await check.get(DocumentationReview, run_id)
                    assert saved.manifest['completed_triage_ids'] == ['0']
            return payload
        monkeypatch.setattr(antigravity_bridge, 'workflow', bridge)
        try:
            async with sessions() as session:
                session.add(Repository(id=repo_id, name='native-test', remote_url='https://example.invalid/repo', status='ready', commit_sha='abc'))
                await session.flush()
                session.add(Job(id=job_id, repo_id=repo_id, kind='documentation_review', payload={'run_id': str(run_id)}, status='running', attempts=1, lease_token=uuid.uuid4(), lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=5)))
                await session.flush()
                session.add(DocumentationReview(id=run_id, repo_id=repo_id, job_id=job_id, commit_sha='abc', status='running', model='m', snapshot=current_snapshot, manifest=manifest))
                await session.flush()
                for path in PATHS:
                    prior = scenario in ('resume', 'budget', 'invalid', 'lost_lease') and path == 'a.md'
                    claimless = scenario == 'claimless_resume' and path == 'b.md'
                    session.add(DocumentationReviewTask(id=uuid.uuid4(), run_id=run_id, path=path, start_line=1, end_line=1, content=current_snapshot[path], status='completed' if (scenario in ('resume', 'budget') and prior) or claimless else 'pending', state={'assessments': [ASSESSMENT]} if prior else {'no_factual_claims': True, 'completion_reason': 'Previously verified.'} if claimless else {}, assessments=[]))
                await session.commit()
                job = await session.get(Job, job_id)
                from sqlalchemy import select
                tasks = (await session.execute(select(DocumentationReviewTask).where(DocumentationReviewTask.run_id == run_id))).scalars().all()
            if scenario in ('stale_doc', 'stale_code'):
                with pytest.raises(model.ProviderError, match='model_configuration_changed'):
                    await native_service.run_native_review(job, run_id, current_snapshot, tasks, manifest)
                assert not called
            elif scenario == 'lost_lease':
                with pytest.raises(RuntimeError, match='lease lost'):
                    await native_service.run_native_review(job, run_id, SNAPSHOT, tasks, manifest)
            else:
                outcome = await native_service.run_native_review(job, run_id, SNAPSHOT, tasks, manifest)
                assert outcome['status'] == {'complete': 'completed', 'resume': 'completed', 'invalid': 'failed', 'incomplete': 'paused', 'budget': 'completed', 'exhausted': 'paused', 'claimless_resume': 'completed', 'runtime': 'completed', 'budget_stop': 'paused', 'no_progress': 'paused', 'triage_progress': 'paused'}[scenario]
            async with sessions() as session:
                current = (await session.execute(select(DocumentationReviewTask).where(DocumentationReviewTask.run_id == run_id).order_by(DocumentationReviewTask.path))).scalars().all()
                run = await session.get(DocumentationReview, run_id)
                if scenario == 'triage_progress':
                    assert run.manifest['completed_triage_ids'] == ['0']
                    assert run.manifest['native_triage_finished'] is False
                if scenario == 'runtime':
                    assert run.manifest['native_runtime']['agents'][0]['events'] == 2
                if scenario in ('budget_stop', 'no_progress'):
                    assert run.reason == ('native_prompt_budget_exhausted' if scenario == 'budget_stop' else 'agent_no_progress')
                if scenario == 'budget':
                    assert run.manifest['native_cost']['prompt_calls'] == 6
                if scenario == 'exhausted':
                    assert not called and run.reason == 'native_prompt_budget_exhausted' and run.manifest.get('static_review')
                if scenario == 'claimless_resume':
                    assert current[1].state['no_factual_claims'] is True and current[1].state['completion_reason'] == 'Previously verified.'
                if scenario in ('stale_doc', 'stale_code', 'exhausted'):
                    assert all(t.status == 'pending' for t in current)
                elif scenario in ('invalid', 'lost_lease'):
                    assert current[0].state['assessments'] == [ASSESSMENT] and current[1].status == 'pending'
                elif scenario in ('incomplete', 'budget_stop', 'no_progress', 'triage_progress'):
                    assert current[0].status == 'completed' and current[1].status == 'pending'
                else:
                    assert all(t.status == 'completed' for t in current) and current[0].state['assessments'] == [ASSESSMENT]
        finally:
            async with sessions() as session:
                repo = await session.get(Repository, repo_id)
                if repo: await session.delete(repo)
                await session.commit()
            await engine.dispose()
    asyncio.run(run())


def test_static_reuse_requires_owned_resolved_instance():
    report = copy.deepcopy(STATIC)
    report['items'] = [
        {'id': 'resolved', 'status': 'aligned', 'claim_type': 'http_status', 'claim': 'It works.', 'doc': {'file': 'a.md', 'start_line': 1, 'end_line': 1}},
        {'id': 'unverified', 'status': 'unverified', 'claim_type': 'http_status', 'claim': 'A second claim.', 'doc': {'file': 'b.md', 'start_line': 1, 'end_line': 1}},
    ]
    payload = result(static=report)
    agent = payload['workflow']['agents'][0]
    agent['files'][0]['assessments'] = []
    agent['reused_static_ids'] = ['resolved']
    assert native_result.validate_result(payload, SNAPSHOT, PATHS, 'm', 'binding', 600, static_review=report)
    for invalid in ('unverified', 'not-present'):
        agent['reused_static_ids'] = [invalid]
        with pytest.raises(ValueError):
            native_result.validate_result(payload, SNAPSHOT, PATHS, 'm', 'binding', 600, static_review=report)


def test_triage_resume_requires_existing_or_new_evidence_for_every_processed_id():
    report = copy.deepcopy(STATIC)
    report['items'] = [{'id': str(i), 'status': 'unverified', 'claim_type': 'http_status', 'claim': 'It works.', 'doc': {'file': 'a.md', 'start_line': 1, 'end_line': 1}} for i in range(20)]
    plan = {**PLAN, 'static_triage': True}
    payload = result('incomplete', 'pending', report, plan)
    assessment = {**copy.deepcopy(ASSESSMENT), 'verdict': 'insufficient', 'code_citations': []}
    triage = {'reviewer': 3, 'finding_ids': [str(i) for i in range(20)], 'processed_ids': ['0', '1'],
              'assessments': [{'finding_id': '1', 'assessment': assessment}], 'read_ranges': [],
              'activity': {'steps': 1, 'errors': 0, 'claims_recorded': 1}, 'finished': False}
    payload['workflow']['static_triage'] = triage
    assert native_result.validate_result(payload, SNAPSHOT, PATHS, 'm', 'binding', 600, static_review=report, completed_triage_ids=['0'])
    triage['processed_ids'].append('2')
    with pytest.raises(ValueError):
        native_result.validate_result(payload, SNAPSHOT, PATHS, 'm', 'binding', 600, static_review=report, completed_triage_ids=['0'])


@pytest.mark.integration
@pytest.mark.skipif(not URL, reason='SENTRA_TEST_DATABASE_URL is not configured')
def test_create_route_uses_whole_file_only_for_native(monkeypatch):
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from app.models import Repository
    from app.routers import documentation
    from types import SimpleNamespace

    async def run():
        engine = create_async_engine(make_url(URL).set(drivername='postgresql+asyncpg'), poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(documentation, 'SessionLocal', sessions)
        monkeypatch.setattr(documentation.model_connections, 'resolve', lambda *_: SimpleNamespace(model='api-model'))
        content = '# First\nOne claim.\n## Second\nAnother claim.\n'
        async def capture(session, repo_id):
            return {'README.md': content}, {'documentation_files': ['README.md'], 'unreadable_files': [], 'scope': 'test'}
        monkeypatch.setattr(documentation, 'capture', capture)
        ids = [uuid.uuid4(), uuid.uuid4()]
        try:
            async with sessions() as session:
                for repo_id in ids:
                    session.add(Repository(id=repo_id, name='sections-test', remote_url='https://example.invalid/repo', status='ready', commit_sha='abc'))
                await session.commit()
            monkeypatch.setattr(documentation.doc_model, 'review_settings', lambda: {'kind': 'antigravity', 'workflow': 'file_agents_v1', 'model': 'native-model'})
            native = await documentation.create_review(ids[0])
            monkeypatch.setattr(documentation.doc_model, 'review_settings', lambda: {'kind': 'api'})
            api = await documentation.create_review(ids[1])
            assert native['coverage']['sections_total'] == 1
            assert api['coverage']['sections_total'] == 2
        finally:
            async with sessions() as session:
                for repo_id in ids:
                    repo = await session.get(Repository, repo_id)
                    if repo: await session.delete(repo)
                await session.commit()
            await engine.dispose()
    asyncio.run(run())


def test_triage_rejects_other_verbatim_claim_from_same_line():
    report = copy.deepcopy(STATIC)
    report['items'] = [{'id': str(i), 'status': 'unverified', 'claim': 'It works.', 'doc': {'file': 'a.md', 'start_line': 1, 'end_line': 1}} for i in range(20)]
    plan = {**PLAN, 'static_triage': True}
    payload = result('incomplete', 'pending', report, plan)
    assessment = {**copy.deepcopy(ASSESSMENT), 'claim': 'works', 'verdict': 'insufficient', 'code_citations': []}
    payload['workflow']['static_triage'] = {'reviewer': 3, 'finding_ids': [str(i) for i in range(20)], 'processed_ids': ['0'],
        'assessments': [{'finding_id': '0', 'assessment': assessment}], 'read_ranges': [],
        'activity': {'steps': 1, 'errors': 0, 'claims_recorded': 1}, 'finished': False}
    with pytest.raises(ValueError):
        native_result.validate_result(payload, SNAPSHOT, PATHS, 'm', 'binding', 600, static_review=report)


def test_native_runtime_activity_is_optional_and_rejects_raw_events():
    payload = result()
    runtime = {'events': 4, 'last_event_at': 123.0, 'phase': 'responding', 'prompt_turns': 2, 'last_stop_reason': 'end_turn', 'permission_requests': 1, 'permission_denials': 0}
    payload['workflow']['runtime_activity'] = runtime
    payload['workflow']['agents'][0]['runtime_activity'] = dict(runtime)
    assert validate(payload)
    for key, value in [('events', -1), ('last_event_at', float('nan')), ('phase', 'private text'), ('raw_content', 'private'), ('permission_denials', True)]:
        modified = copy.deepcopy(payload)
        modified['workflow']['agents'][0]['runtime_activity'][key] = value
        with pytest.raises(ValueError): validate(modified)


def test_native_reported_limit_is_distinct_from_confirmed_provider_throttling():
    from app.documentation.native_service import failure_reason
    assert failure_reason({'category': 'model_limit_reported'}) == 'agent_model_limit_reported'
    assert failure_reason({'category': 'rate_limited'}) == 'rate_limited'
    assert failure_reason({'category': 'budget_exhausted'}) == 'native_prompt_budget_exhausted'


def test_native_activity_rejects_unbounded_unrecognized_error_text():
    payload = result()
    payload['workflow']['agents'][0]['activity']['last_error_code'] = 'finding_evidence_mismatch'
    assert validate(payload)
    payload['workflow']['agents'][0]['activity']['last_error_code'] = 'private provider response'
    with pytest.raises(ValueError): validate(payload)
    payload['workflow']['agents'][0]['activity'].pop('last_error_code')
    payload['workflow']['agents'][0]['activity']['raw_text'] = 'private'
    with pytest.raises(ValueError): validate(payload)
