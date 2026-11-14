"""Lease fencing checks using the explicitly configured disposable test database."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
import pytest

URL = os.environ.get('SENTRA_TEST_DATABASE_URL')
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason='SENTRA_TEST_DATABASE_URL is not configured')]


def test_review_checkpoint_rejects_expired_or_replaced_lease():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.engine import make_url
    from types import SimpleNamespace
    from app.models import Repository, Job, DocumentationReview
    from app.documentation.service import fenced

    async def run():
        engine = create_async_engine(make_url(URL).set(drivername='postgresql+asyncpg'))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        rid, jid, token, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        try:
            async with sessions() as session:
                session.add(Repository(id=rid, name='review-test', remote_url='https://example.invalid/test', status='ready', commit_sha='abc'))
                await session.flush()
                session.add(Job(id=jid, repo_id=rid, kind='documentation_review', payload={'run_id': str(run_id)}, status='running', attempts=1,
                                lease_token=token, lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=1)))
                await session.flush()
                session.add(DocumentationReview(id=run_id, repo_id=rid, job_id=jid, commit_sha='abc', status='running', model='test', snapshot={}, manifest={}))
                await session.commit()
                job = await session.get(Job, jid)
                await fenced(session, job)
                await session.rollback()
                job = await session.get(Job, jid)
                old_lease = SimpleNamespace(id=job.id, repo_id=job.repo_id, payload=job.payload,
                                            lease_token=job.lease_token, attempts=job.attempts)
                job.lease_token = uuid.uuid4()
                job.attempts = 2
                await session.commit()
                with pytest.raises(RuntimeError, match='lease lost'):
                    await fenced(session, old_lease)
                await session.rollback()
                job = await session.get(Job, jid)
                job.lease_expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
                await session.commit()
                with pytest.raises(RuntimeError, match='lease lost'):
                    await fenced(session, job)
                await session.rollback()
                job = await session.get(Job, jid)
                job.lease_expires_at = datetime.now(timezone.utc)+timedelta(minutes=1)
                repo = await session.get(Repository, rid)
                repo.commit_sha = 'new-commit'
                await session.commit()
                with pytest.raises(RuntimeError, match='commit changed'):
                    await fenced(session, job)
                review = await session.get(DocumentationReview, run_id)
                assert review.status == 'stale'
        finally:
            async with sessions() as session:
                repo = await session.get(Repository, rid)
                if repo:
                    await session.delete(repo)
                    await session.commit()
            await engine.dispose()
    asyncio.run(run())


def test_database_rejects_duplicate_and_invalid_section_ranges():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import IntegrityError
    from app.models import Repository, DocumentationReview, DocumentationReviewTask

    async def run():
        engine = create_async_engine(make_url(URL).set(drivername='postgresql+asyncpg'))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        rid, review_id = uuid.uuid4(), uuid.uuid4()
        def task(start, end):
            return DocumentationReviewTask(id=uuid.uuid4(), run_id=review_id, path='README.md', start_line=start,
                                           end_line=end, content='test', status='pending', state={}, assessments=[])
        try:
            async with sessions() as session:
                session.add(Repository(id=rid, name='review-test', remote_url='https://example.invalid/test'))
                await session.flush()
                session.add(DocumentationReview(id=review_id, repo_id=rid, commit_sha='abc', status='queued', model='test', snapshot={}, manifest={}))
                await session.flush()
                session.add(task(1, 2))
                await session.commit()
                session.add(task(1, 2))
                with pytest.raises(IntegrityError):
                    await session.commit()
                await session.rollback()
                session.add(task(0, 2))
                with pytest.raises(IntegrityError):
                    await session.commit()
                await session.rollback()
        finally:
            async with sessions() as session:
                repo = await session.get(Repository, rid)
                if repo:
                    await session.delete(repo)
                    await session.commit()
            await engine.dispose()
    asyncio.run(run())


def test_review_api_pause_resume_and_persisted_citations(monkeypatch):
    import hashlib
    import json
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app import db, jobs, main
    from app.auth import issue_session_token
    from app.config import settings
    from app.documentation import reviewer, model, service, quota
    from app.models import Repository, File, Chunk, Job, DocumentationProviderQuota
    from app.routers import documentation

    sync_url = make_url(URL).set(drivername='postgresql+psycopg')
    cfg = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    cfg.set_main_option('sqlalchemy.url', sync_url.render_as_string(hide_password=False).replace('%', '%%'))
    command.upgrade(cfg, 'head')
    engine = create_async_engine(make_url(URL).set(drivername='postgresql+asyncpg'), poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, 'SessionLocal', sessions)
    monkeypatch.setattr(db, 'engine', engine)
    monkeypatch.setattr(jobs, 'SessionLocal', sessions)
    monkeypatch.setattr(service, 'SessionLocal', sessions)
    monkeypatch.setattr(documentation, 'SessionLocal', sessions)
    monkeypatch.setattr(main, 'engine', engine)
    monkeypatch.setattr(settings, 'database_url', make_url(URL).set(drivername='postgresql+asyncpg').render_as_string(hide_password=False))
    monkeypatch.setattr(settings, 'sentra_access_token', 'test-access-token-0123456789-abcdef')
    monkeypatch.setattr(reviewer, 'MAX_CALLS_PER_INVOCATION', 2)
    monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', json.dumps({'mistral_primary': {'adapter': 'mistral', 'model': 'mistral-large-4', 'endpoint': 'https://api.mistral.ai/v1', 'credential_env': 'MISTRAL_API_KEY', 'quota_group': 'mistral:1', 'tpm': 100000, 'context_tokens': 100000, 'interval': 0}}))
    rid = uuid.uuid4()

    async def seed():
        async with sessions() as session:
            row = await session.get(DocumentationProviderQuota, 'mistral:1', with_for_update=True)
            row.next_call_at = row.blocked_until = row.window_start = 0
            row.reserved_tokens = row.reserved_requests = 0
            session.add(Repository(id=rid, name='review-test', remote_url='https://example.invalid/test', status='ready', commit_sha='abc'))
            await session.flush()
            for path, content in [('README.md', 'The service uses port 80.\n'), ('app.py', 'PORT = 8080\n')]:
                raw = content.encode()
                fid = uuid.uuid4()
                digest = hashlib.sha256(raw).hexdigest()
                session.add(File(id=fid, repo_id=rid, path=path, byte_len=len(raw), content_hash=digest))
                await session.flush()
                session.add(Chunk(id=uuid.uuid4(), file_id=fid, repo_id=rid, kind='test', name=path,
                    qualified_name=path, start_line=1, end_line=1, byte_start=0, byte_end=len(raw), content=content, content_hash=digest))
            await session.commit()
    def call(name, args):
        return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': str(uuid.uuid4()), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, {}
    replies = iter([
        call('read_source', {'path': 'app.py', 'start_line': 1, 'end_line': 1}),
        call('record_assessment', {'claim': 'The service uses port 80.', 'documentation_citation': {'path': 'README.md', 'start_line': 1, 'end_line': 1}, 'verdict': 'conflicting', 'code_citations': [{'path': 'app.py', 'start_line': 1, 'end_line': 1}], 'suggested_update': 'Use port 8080.', 'rationale': 'The code sets port 8080.'}),
        call('finish_review', {'reason': 'all_claims_reviewed'}),
    ])
    async def complete(messages, tools, **kwargs):
        if tools[0]['function']['name'] == 'assign_sections':
            inventory = json.loads(messages[1]['content'])
            return call('assign_sections', {'assignments': [[item[0] for item in inventory['sections']]]})
        return next(replies)
    monkeypatch.setattr(__import__('app.model_connections', fromlist=['complete']), 'complete', complete)
    async def execute(job_id):
        async with sessions() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            job.status, job.attempts, job.lease_token = 'running', 1, uuid.uuid4()
            job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            await session.commit()
            token = job.lease_token
        result = await service.run_review(job)
        await jobs.finish(job.id, token, result=result)
        return result
    async def cleanup():
        async with sessions() as session:
            repo = await session.get(Repository, rid)
            if repo:
                await session.delete(repo)
            row = await session.get(DocumentationProviderQuota, 'mistral:1', with_for_update=True)
            row.next_call_at = row.blocked_until = row.window_start = 0
            row.reserved_tokens = row.reserved_requests = 0
            await session.commit()
        await engine.dispose()
    try:
        asyncio.run(seed())
        with TestClient(main.app) as client:
            headers = {'Authorization': 'Bearer ' + issue_session_token(settings.sentra_access_token)}
            base = f'/api/repos/{rid}/documentation/reviews'
            assert client.get(base + '/latest', headers=headers).json() is None
            response = client.post(base, headers=headers)
            assert response.status_code == 200, response.text
            created = response.json()
            assert created['coverage']['sections_total'] == 1
            assert client.post(base, headers=headers).json()['run_id'] == created['run_id']
            assert asyncio.run(execute(created['job_id']))['status'] == 'paused'
            paused = client.get(base + '/latest', headers=headers).json()
            assert paused['coverage']['claims_assessed'] == 1, paused
            resumed = client.post(base + '/' + created['run_id'] + '/resume', headers=headers)
            assert resumed.status_code == 200, resumed.text
            assert asyncio.run(execute(resumed.json()['job_id']))['status'] == 'completed'
            final = client.get(base + '/latest', headers=headers).json()
            assert final['coverage']['sections_completed'] == 1
            assert len(final['assessments']) == 1
            assert final['assessments'][0]['doc']['file'] == 'README.md'
            assert final['assessments'][0]['code_citations'][0]['file'] == 'app.py'
            repeated = client.post(base, headers=headers)
            assert repeated.status_code == 200
            assert repeated.json()['run_id'] == created['run_id']
            assert repeated.json()['status'] == 'completed'
            assert len(repeated.json()['assessments']) == 1
            assert client.post(base, headers=headers, json={'paths': ['missing.md']}).status_code == 422
            selected = client.post(base, headers=headers, json={'paths': ['README.md']})
            assert selected.status_code == 200, selected.text
            assert selected.json()['run_id'] != created['run_id']
            assert selected.json()['coverage']['scope'].startswith('Selected Markdown')
            assert client.post(base, headers=headers, json={'paths': ['README.md']}).json()['run_id'] == selected.json()['run_id']
    finally:
        asyncio.run(cleanup())

def test_two_lanes_resume_persisted_plan_and_account_quotas(monkeypatch):
    import json
    from sqlalchemy import select
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app import db
    from app.documentation import model, quota, service
    from app.models import Repository, Job, DocumentationReview, DocumentationReviewTask, DocumentationProviderQuota

    async def run():
        engine = create_async_engine(make_url(URL).set(drivername='postgresql+asyncpg'), poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(db, 'SessionLocal', sessions)
        monkeypatch.setattr(db, 'engine', engine)
        monkeypatch.setattr(service, 'SessionLocal', sessions)
        monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', json.dumps({name: {'adapter': 'mistral', 'model': 'mistral-large-4', 'endpoint': 'https://api.mistral.ai/v1', 'credential_env': credential, 'quota_group': group, 'tpm': 100000, 'context_tokens': 100000, 'interval': 0} for name, credential, group in [('mistral_primary', 'MISTRAL_API_KEY', 'mistral:1'), ('mistral_secondary', 'MISTRAL_API_KEY2', 'mistral:2')]}))
        monkeypatch.setenv('MISTRAL_API_KEY2', 'test-second-account')
        rid, jid, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ids = [uuid.uuid4(), uuid.uuid4()]
        counts = {'planner': 0, str(ids[0]): 0, str(ids[1]): 0}
        calls = []
        def response(name, args):
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': str(uuid.uuid4()), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, {}
        async def complete(messages, tools, **kwargs):
            slot = 2 if kwargs['connection'].quota_group == 'mistral:2' else 1
            calls.append(slot)
            if tools[0]['function']['name'] == 'assign_sections':
                counts['planner'] += 1
                return response('assign_sections', {'assignments': [['0'], ['1']]})
            section = json.loads(messages[1]['content'])['assigned_documentation_section']
            key = section['id']; counts[key] += 1
            if key == str(ids[1]) and counts[key] == 1:
                raise model.ProviderError('rate_limited', 30)
            return response('finish_review', {'reason': 'no_factual_claims'})
        monkeypatch.setattr(__import__('app.model_connections', fromlist=['complete']), 'complete', complete)
        try:
            async with sessions() as session:
                for row in (await session.execute(select(DocumentationProviderQuota).with_for_update())).scalars():
                    row.next_call_at = row.blocked_until = row.window_start = 0
                    row.reserved_tokens = row.reserved_requests = 0
                session.add(Repository(id=rid, name='parallel-test', remote_url='https://example.invalid/repo', status='ready', commit_sha='abc'))
                await session.flush()
                session.add(Job(id=jid, repo_id=rid, kind='documentation_review', payload={'run_id': str(run_id)}, status='running', attempts=1, lease_token=uuid.uuid4(), lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=5)))
                await session.flush()
                session.add(DocumentationReview(id=run_id, repo_id=rid, job_id=jid, commit_sha='abc', status='queued', model=model.DEFAULT_MODEL, snapshot={}, manifest={'unreadable_files': [], 'model_settings': model.review_settings()}))
                await session.flush()
                for i, task_id in enumerate(ids):
                    session.add(DocumentationReviewTask(id=task_id, run_id=run_id, path=f'{i}.md', start_line=1, end_line=1, content='# Title', status='pending', state={}, assessments=[]))
                await session.commit()
                job = await session.get(Job, jid)
            result = await service.run_review(job)
            assert result['status'] == 'paused'
            # A fresh session observes account 2's cooldown while account 1 remains usable.
            async def probe(*args, **kwargs):
                return {'role': 'assistant', 'content': 'ok'}, {}
            monkeypatch.setattr(__import__('app.model_connections', fromlist=['complete']), 'complete', probe)
            with pytest.raises(model.ProviderError, match='') as blocked:
                await quota.request([], [], model_name='test', max_tokens=1, credential_slot=2)
            assert blocked.value.reason == 'rate_limited'
            assert (await quota.request([], [], model_name='test', max_tokens=1, credential_slot=1))[0]['content'] == 'ok'
            monkeypatch.setattr(__import__('app.model_connections', fromlist=['complete']), 'complete', complete)
            async with sessions() as session:
                review = await session.get(DocumentationReview, run_id)
                assert review.manifest['review_plan']['assignments'] == [[str(ids[0])], [str(ids[1])]]
                assert review.manifest['review_quota']['planner_calls'] == 1
                first, second = [await session.get(DocumentationReviewTask, i) for i in ids]
                assert first.status == 'completed' and second.status != 'completed'
                one = await session.get(DocumentationProviderQuota, 'mistral:1')
                two = await session.get(DocumentationProviderQuota, 'mistral:2')
                assert one.blocked_until == 0 and two.blocked_until > 0
                # Simulate expiry without sleeping, retaining the persisted plan and task states.
                two.blocked_until = two.next_call_at = 0
                manifest = dict(review.manifest); q = dict(manifest['review_quota']); q['blocked_until_2'] = 0
                manifest['review_quota'] = q; review.manifest = manifest
                state = dict(second.state); state['next_call_at'] = 0; second.state = state
                await session.commit()
            assert (await service.run_review(job))['status'] == 'completed'
            assert counts == {'planner': 1, str(ids[0]): 1, str(ids[1]): 2}
            assert 1 in calls and 2 in calls
        finally:
            async with sessions() as session:
                repo = await session.get(Repository, rid)
                if repo:
                    await session.delete(repo)
                for row in (await session.execute(select(DocumentationProviderQuota).with_for_update())).scalars():
                    row.next_call_at = row.blocked_until = row.window_start = 0
                    row.reserved_tokens = row.reserved_requests = 0
                await session.commit()
            await engine.dispose()
    asyncio.run(run())


def test_provider_switch_rejects_resume_and_worker_without_call(monkeypatch):
    import json
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from fastapi import HTTPException
    from app import db, model_connections
    from app.documentation import model, service
    from app.routers import documentation as routes
    from app.models import Repository, Job, DocumentationReview

    async def run():
        engine = create_async_engine(make_url(URL).set(drivername="postgresql+asyncpg"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(db, "engine", engine)
        monkeypatch.setattr(db, "SessionLocal", sessions)
        monkeypatch.setattr(service, "SessionLocal", sessions)
        monkeypatch.setattr(routes, "SessionLocal", sessions)
        monkeypatch.delenv("SENTRA_MODEL_CONNECTIONS", raising=False)
        monkeypatch.delenv("SENTRA_MODEL_WORKFLOWS", raising=False)
        monkeypatch.delenv("SENTRA_DOCUMENTATION_MODEL", raising=False)
        settings = model.review_settings()
        rid, jid, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        sent = []
        async def complete(*args, **kwargs):
            sent.append(True)
            raise AssertionError("provider must not be called")
        monkeypatch.setattr(model_connections, "complete", complete)
        try:
            async with sessions() as session:
                session.add(Repository(id=rid, name="model-switch", remote_url="https://example.invalid/repo", status="ready", commit_sha="abc"))
                await session.flush()
                session.add(Job(id=jid, repo_id=rid, kind="documentation_review", payload={"run_id": str(run_id)}, status="running", attempts=1, lease_token=uuid.uuid4(), lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=5)))
                await session.flush()
                session.add(DocumentationReview(id=run_id, repo_id=rid, job_id=jid, commit_sha="abc", status="paused", model="mistral-large-4", snapshot={}, manifest={"model_settings": settings, "documentation_files": [], "unreadable_files": [], "scope": "all"}))
                await session.commit()
                job = await session.get(Job, jid)
                from sqlalchemy import select
                match = (await session.execute(select(DocumentationReview).where(DocumentationReview.id == run_id, DocumentationReview.manifest["model_settings"] == settings))).scalar_one_or_none()
                assert match is not None
            monkeypatch.setenv("SENTRA_MODEL_CONNECTIONS", json.dumps({"alternative": {"adapter": "openai_compatible", "model": "mistral-large-4", "endpoint": "https://example.com/v1", "credential_env": "OTHER_KEY", "capabilities": ["tools"]}}))
            monkeypatch.setenv("SENTRA_MODEL_WORKFLOWS", json.dumps({"reviewer": "alternative"}))
            with pytest.raises(HTTPException) as error:
                await routes.resume_review(rid, run_id)
            assert error.value.status_code == 409
            assert (await service.run_review(job))["status"] == "failed"
            async with sessions() as session:
                review = await session.get(DocumentationReview, run_id)
                assert review.reason == "model_configuration_changed"
            assert sent == []
        finally:
            async with sessions() as session:
                repo = await session.get(Repository, rid)
                if repo: await session.delete(repo)
                await session.commit()
            await engine.dispose()
    asyncio.run(run())
