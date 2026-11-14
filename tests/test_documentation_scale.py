"""Provider-free persisted review rehearsal on an explicitly disposable database."""
import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
import pytest

URL = os.environ.get('SENTRA_TEST_DATABASE_URL')
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason='SENTRA_TEST_DATABASE_URL is not configured')]


def test_persisted_sections_resume(monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app import db, model_connections
    from app.documentation import model, reviewer, service
    from app.models import Repository, Job, DocumentationReview, DocumentationReviewTask, DocumentationProviderQuota

    monkeypatch.setenv('SENTRA_DOCUMENTATION_MAX_RUN_CALLS', '1000')
    monkeypatch.setenv('SENTRA_DOCUMENTATION_MAX_RUN_TOKENS', '16000000')
    group = 'rehearsal:' + str(uuid.uuid4())
    monkeypatch.setenv('SENTRA_MODEL_CONNECTIONS', json.dumps({'rehearsal': {'adapter': 'mistral', 'model': 'test', 'endpoint': 'https://example.invalid/v1', 'credential_env': 'REHEARSAL_KEY', 'quota_group': group, 'tpm': 10000000, 'rpm': 100000, 'context_tokens': 100000, 'interval': 0}}))
    monkeypatch.setenv('REHEARSAL_KEY', 'test')
    monkeypatch.setenv('SENTRA_MODEL_WORKFLOWS', json.dumps({'planner': 'rehearsal', 'reviewer': 'rehearsal'}))
    monkeypatch.setattr(reviewer, 'MAX_CALLS_PER_INVOCATION', 1)
    ids = [uuid.uuid4() for _ in range(3)]
    rid, jid, run_id = (uuid.uuid4() for _ in range(3))
    planner_calls = []

    def call(name, args):
        return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': str(uuid.uuid4()), 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, {}

    async def complete(messages, tools, **kwargs):
        if tools[0]['function']['name'] == 'assign_sections':
            inventory = json.loads(messages[1]['content'])
            planner_calls.append(inventory)
            return call('assign_sections', {'assignments': [[s[0] for s in inventory['sections']]]})
        section = json.loads(messages[1]['content'])['assigned_documentation_section']
        previous = [c['function']['name'] for m in messages if m.get('role') == 'assistant' for c in m.get('tool_calls', [])]
        citation = {'path': section['path'], 'start_line': 1, 'end_line': 1}
        if not previous:
            return call('record_assessment', {'claim': section['content'], 'documentation_citation': citation, 'verdict': 'insufficient', 'code_citations': [], 'suggested_update': '', 'rationale': 'No source evidence was supplied.'})
        return call('finish_review', {'reason': 'all_claims_reviewed'})

    monkeypatch.setattr(model_connections, 'complete', complete)

    async def run():
        engine = create_async_engine(make_url(URL).set(drivername='postgresql+asyncpg'), poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(db, 'engine', engine)
        monkeypatch.setattr(db, 'SessionLocal', sessions)
        monkeypatch.setattr(service, 'SessionLocal', sessions)
        try:
            async with sessions() as session:
                session.add(Repository(id=rid, name='workload-rehearsal', remote_url='https://example.invalid/test', status='ready', commit_sha='abc'))
                await session.flush()
                session.add(Job(id=jid, repo_id=rid, kind='documentation_review', payload={'run_id': str(run_id)}, status='running', attempts=1, lease_token=uuid.uuid4(), lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
                await session.flush()
                session.add(DocumentationReview(id=run_id, repo_id=rid, job_id=jid, commit_sha='abc', status='queued', model='test', snapshot={}, manifest={'unreadable_files': [], 'model_settings': model.review_settings()}))
                await session.flush()
                for i, task_id in enumerate(ids):
                    session.add(DocumentationReviewTask(id=task_id, run_id=run_id, path=f'{i}.md', start_line=1, end_line=1, content=f'Service {i} uses port {8000+i}.', status='pending', state={}, assessments=[]))
                await session.commit()
                job = await session.get(Job, jid)
            pauses = 0
            for _ in range(len(ids) + 1):
                result = await service.run_review(job)
                if result['status'] == 'completed':
                    break
                assert result['status'] == 'paused', result
                pauses += 1
            assert result['status'] == 'completed', result
            assert pauses == len(ids)
            async with sessions() as session:
                review = await session.get(DocumentationReview, run_id)
                assignments = [item for lane in review.manifest['review_plan']['assignments'] for item in lane]
                assert len(assignments) == len(set(assignments)) == len(ids)
                assert set(assignments) == {str(i) for i in ids}
                tasks = (await session.execute(select(DocumentationReviewTask).where(DocumentationReviewTask.run_id == run_id))).scalars().all()
                assert all(t.status == 'completed' and len(t.assessments) == 1 and t.assessments[0]['claim'] == t.content for t in tasks)
                assert len(planner_calls) == 1
        finally:
            async with sessions() as session:
                repo = await session.get(Repository, rid)
                if repo:
                    await session.delete(repo)
                row = await session.get(DocumentationProviderQuota, 'q:' + hashlib.sha256(group.encode()).hexdigest()[:62])
                if row:
                    await session.delete(row)
                await session.commit()
            await engine.dispose()
    asyncio.run(run())



def test_165_section_orchestration_with_configured_budget(monkeypatch):
    """Exercise persistence and budget accounting, not a model or reviewer loop."""
    from sqlalchemy import select
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.documentation import model, planner, reviewer, service
    from app.models import Repository, Job, DocumentationReview, DocumentationReviewTask

    monkeypatch.setenv('SENTRA_DOCUMENTATION_MAX_RUN_CALLS', '1000')
    monkeypatch.setenv('SENTRA_DOCUMENTATION_MAX_RUN_TOKENS', '16000000')
    rid, jid, run_id = (uuid.uuid4() for _ in range(3))
    ids = [uuid.uuid4() for _ in range(165)]
    reviewed = []

    async def plan(tasks, *args, **kwargs):
        return {'version': 1, 'assignments': [[str(task.id) for task in tasks]]}

    async def review(section, tools, *, save_state, **kwargs):
        reviewed.append(section['id'])
        assessment = {'claim': section['content'], 'documentation_citation': {'path': section['path'], 'start_line': 1, 'end_line': 1}, 'verdict': 'insufficient', 'code_citations': [], 'suggested_update': '', 'rationale': 'Synthetic rehearsal.'}
        state = {'calls': 4, 'tokens': 100, 'assessments': [assessment]}
        await save_state(state)
        return {'status': 'completed', 'assessments': [assessment], 'state': state}

    monkeypatch.setattr(planner, 'create_plan', plan)
    monkeypatch.setattr(reviewer, 'review_section', review)

    async def run():
        engine = create_async_engine(make_url(URL).set(drivername='postgresql+asyncpg'), poolclass=NullPool)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(service, 'SessionLocal', sessions)
        try:
            async with sessions() as session:
                session.add(Repository(id=rid, name='scale-rehearsal', remote_url='https://example.invalid/test', status='ready', commit_sha='abc'))
                await session.flush()
                session.add(Job(id=jid, repo_id=rid, kind='documentation_review', payload={'run_id': str(run_id)}, status='running', attempts=1, lease_token=uuid.uuid4(), lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
                await session.flush()
                session.add(DocumentationReview(id=run_id, repo_id=rid, job_id=jid, commit_sha='abc', status='queued', model=model.DEFAULT_MODEL, snapshot={}, manifest={'unreadable_files': [], 'model_settings': model.review_settings()}))
                await session.flush()
                for i, task_id in enumerate(ids):
                    session.add(DocumentationReviewTask(id=task_id, run_id=run_id, path=f'{i:03}.md', start_line=1, end_line=1, content=f'Service {i} uses port {8000+i}.', status='pending', state={}, assessments=[]))
                await session.commit()
                job = await session.get(Job, jid)
            assert (await service.run_review(job))['status'] == 'completed'
            async with sessions() as session:
                review_row = await session.get(DocumentationReview, run_id)
                assigned = [item for group in review_row.manifest['review_plan']['assignments'] for item in group]
                assert len(assigned) == len(set(assigned)) == 165
                assert set(assigned) == {str(i) for i in ids}
                tasks = (await session.execute(select(DocumentationReviewTask).where(DocumentationReviewTask.run_id == run_id))).scalars().all()
                assert len(tasks) == 165
                assert all(t.status == 'completed' and len(t.assessments) == 1 and t.assessments[0]['claim'] == t.content for t in tasks)
                assert sum(t.state['calls'] for t in tasks) == 660
            assert (await service.run_review(job))['status'] == 'completed'
            assert len(reviewed) == len(set(reviewed)) == 165
        finally:
            async with sessions() as session:
                repo = await session.get(Repository, rid)
                if repo:
                    await session.delete(repo)
                    await session.commit()
            await engine.dispose()
    asyncio.run(run())
