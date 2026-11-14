from __future__ import annotations
import uuid
import time
import math
import hashlib
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from app.config import settings
from app.db import SessionLocal
from app.jobs import enqueue
from app.models import Repository, DocumentationReview, DocumentationReviewTask
from app.documentation.snapshot import capture
from app.documentation.sections import sections
from app.documentation import model as doc_model, hybrid
from app.documentation.native_result import digest
from app import model_connections

router = APIRouter(prefix='/repos', tags=['documentation'])
WORKFLOW_VERSION = 4


class DraftRequest(BaseModel):
    allow_partial: bool = False


class ReviewSelection(BaseModel):
    paths: list[str] | None = Field(default=None, max_length=10000)


def render(run, tasks):
    completed = sum(t.status == 'completed' for t in tasks)
    failed = sum(t.status == 'failed' for t in tasks)
    retry = None
    if run.status == 'paused' and run.reason == 'rate_limited':
        quota = run.manifest.get('review_quota', {})
        until = max([quota.get('blocked_until_1', 0), quota.get('blocked_until_2', 0)] + [(t.state or {}).get('next_call_at', 0) for t in tasks])
        retry = min(3600, max(0, math.ceil(until - time.time())))
    groups = (run.manifest.get('review_plan') or {}).get('assignments', [])
    plan = {'reviewer_count': len(groups), 'assignments': [{'reviewer': i + 1, 'task_ids': group} for i, group in enumerate(groups)]} if isinstance(groups, list) else None
    activity = None
    candidates = []
    for task in tasks:
        if task.status == 'completed':
            continue
        saved = (task.state or {}).get('activity')
        failure = (task.state or {}).get('failure', {})
        if not isinstance(saved, dict) and type(failure.get('steps')) is int:
            saved = {'steps': failure['steps'], 'errors': failure.get('errors', 0),
                     'claims_recorded': len(task.assessments), 'updated_at': 0}
        if isinstance(saved, dict):
            candidates.append((task, saved))
    if candidates:
        task, saved = max(candidates, key=lambda item: item[1].get('updated_at', 0))
        activity = {'path': task.path, 'start_line': task.start_line, 'end_line': task.end_line,
            **{key: saved.get(key, 0) for key in ('steps', 'errors', 'claims_recorded', 'updated_at')},
            'phase': 'investigating' if run.status == 'running' else 'paused'}
    response = {'activity': activity, 'source_coverage': run.manifest.get('source_coverage'), 'plan': plan, 'run_id': str(run.id), 'job_id': str(run.job_id) if run.job_id else None,
            'commit_sha': run.commit_sha, 'status': run.status, 'model': run.model,
            'coverage': {'files_total': len(run.manifest['documentation_files']), 'sections_total': len(tasks),
                         'sections_completed': completed, 'sections_pending': len(tasks)-completed-failed,
                         'sections_failed': failed, 'claims_assessed': sum(len(t.assessments) for t in tasks), 'unreadable_files': run.manifest['unreadable_files'],
                         'scope': run.manifest['scope']},
            'assessments': [a for t in tasks for a in t.assessments], 'reason': run.reason,
            'retry_after_seconds': retry}
    if run.manifest.get('model_settings', {}).get('workflow') == 'file_agents_v1':
        native_plan = run.manifest.get('native_plan')
        by_path = {task.path: task for task in tasks}
        agents = []
        for reviewer, paths in enumerate((native_plan or {}).get('assignments', []), 1):
            agents.append({'reviewer': reviewer, 'files': [
                {'path': path, 'status': by_path[path].status, 'claims_assessed': len(by_path[path].assessments)}
                for path in paths]})
        phase = ('static_checks' if not run.manifest.get('static_review') else 'planning' if native_plan is None else 'reviewing') if run.status in ('queued', 'running') else run.status
        response['plan'] = None
        response['activity'] = None
        response['native_progress'] = {'phase': 'failed' if phase == 'stale' else phase,
            'files_total': len(tasks), 'files_completed': completed, 'files_pending': len(tasks)-completed-failed,
            'files_failed': failed, 'claims_assessed': sum(len(task.assessments) for task in tasks), 'agents': agents}
    static = run.manifest.get('static_review')
    response['static_review'] = static
    response['merged_findings'] = hybrid.merge_findings(static['items'], response['assessments']) if static else None
    if run.manifest.get('model_settings', {}).get('workflow') == 'file_agents_v1':
        native_plan = run.manifest.get('native_plan') or {}
        if native_plan.get('static_triage'):
            total = (static or {}).get('counts', {}).get('unverified', 0)
            response['native_progress']['triage'] = {
                'reviewer': len(native_plan['assignments']) + 1,
                'status': 'completed' if run.manifest.get('native_triage_finished') else 'running' if run.status == 'running' else 'failed' if run.status == 'failed' else 'pending',
                'findings_total': total, 'findings_processed': len(run.manifest.get('completed_triage_ids', []))}
        cost = run.manifest.get('native_cost')
        if cost is not None:
            saved = run.manifest['model_settings']
            response['cost'] = {**cost, 'budget': {'max_prompt_calls': saved['max_prompt_calls'],
                                                   'deadline_seconds': saved['deadline_seconds']},
                                'incomplete': run.status != 'completed'}
    return response

async def get_run(session, repo_id, run_id):
    run = (await session.execute(select(DocumentationReview).where(DocumentationReview.repo_id == repo_id, DocumentationReview.id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, 'Review not found')
    tasks = (await session.execute(select(DocumentationReviewTask).where(DocumentationReviewTask.run_id == run.id).order_by(DocumentationReviewTask.path, DocumentationReviewTask.start_line))).scalars().all()
    return run, tasks

@router.post('/{repo_id}/documentation/reviews')
async def create_review(repo_id: uuid.UUID, body: ReviewSelection | None = None):
    paths = body.paths if body else None
    if paths is not None and (not paths or len(set(paths)) != len(paths) or any(not p.lower().endswith('.md') for p in paths)):
        raise HTTPException(422, 'Select distinct existing Markdown paths')
    model_settings = doc_model.review_settings()
    review_model = model_settings['model'] if model_settings.get('kind') == 'antigravity' else model_connections.resolve('reviewer', 1).model
    selection_key = hashlib.sha256('\0'.join(sorted(paths)).encode()).hexdigest() if paths else 'all'
    async with SessionLocal() as session:
        await session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        repo = await session.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(404, 'Repository not found')
        if not repo.commit_sha or repo.status != 'ready':
            raise HTTPException(409, 'Repository must have a ready indexed commit')
        snapshot, manifest = await capture(session, repo_id)
        manifest['model_settings'] = model_settings
        manifest['workflow_version'] = WORKFLOW_VERSION
        manifest['selection_key'] = selection_key
        if paths is not None:
            if not set(paths).issubset(manifest['documentation_files']):
                raise HTTPException(422, 'Selected Markdown is not indexed')
            manifest['all_documentation_files'] = manifest['documentation_files']
            manifest['documentation_files'] = sorted(paths)
            manifest['unreadable_files'] = [p for p in manifest['unreadable_files'] if not p.lower().endswith('.md') or p in paths]
            manifest['scope'] = 'Selected Markdown files only: ' + ', '.join(sorted(paths))
        manifest['snapshot_signature'] = digest({'snapshot': snapshot, 'documentation_files': manifest['documentation_files'],
                                                 'unreadable_files': manifest['unreadable_files'], 'source_coverage': manifest.get('source_coverage')})
        existing = (await session.execute(select(DocumentationReview).where(
            DocumentationReview.repo_id == repo_id, DocumentationReview.commit_sha == repo.commit_sha,
            DocumentationReview.model == review_model,
            DocumentationReview.manifest['snapshot_signature'].as_string() == manifest['snapshot_signature'],
            DocumentationReview.status.in_(('queued', 'running', 'paused', 'completed')),
            DocumentationReview.manifest['workflow_version'].as_integer() == WORKFLOW_VERSION,
            DocumentationReview.manifest['selection_key'].as_string() == selection_key,
            DocumentationReview.manifest['model_settings'] == model_settings,
        ).order_by(DocumentationReview.created_at.desc(), DocumentationReview.id.desc()).limit(1))).scalar_one_or_none()
        if existing is not None:
            run, tasks = await get_run(session, repo_id, existing.id)
            return render(run, tasks)
        manifest['section_context'] = {}
        run = DocumentationReview(id=uuid.uuid4(), repo_id=repo_id, commit_sha=repo.commit_sha, status='queued',
                                  model=review_model, snapshot=snapshot, manifest=manifest)
        session.add(run)
        await session.flush()
        tasks = []
        for path in manifest['documentation_files']:
            if path not in snapshot:
                continue
            try:
                parts = ([{'start_line': 1, 'end_line': max(1, len(snapshot[path].splitlines())),
                           'content': snapshot[path], 'heading_context': []}]
                         if model_settings.get('workflow') == 'file_agents_v1' else list(sections(snapshot[path])))
            except ValueError:
                manifest['unreadable_files'] = sorted(set(manifest['unreadable_files'] + [path]))
                continue
            for part in parts:
                task = DocumentationReviewTask(id=uuid.uuid4(), run_id=run.id, path=path,
                    start_line=part['start_line'], end_line=part['end_line'], content=part['content'],
                    status='pending', state={}, assessments=[])
                manifest['section_context'][str(task.id)] = part['heading_context']
                session.add(task)
                tasks.append(task)
        run.manifest = dict(manifest)
        run.job_id = await enqueue(session, repo_id, 'documentation_review', {'run_id': str(run.id)})
        await session.commit()
        return render(run, tasks)

@router.get('/{repo_id}/documentation/reviews/latest')
async def latest_review(repo_id: uuid.UUID):
    async with SessionLocal() as session:
        run = (await session.execute(select(DocumentationReview).where(DocumentationReview.repo_id == repo_id).order_by(DocumentationReview.created_at.desc(), DocumentationReview.id.desc()).limit(1))).scalar_one_or_none()
        if not run:
            return None
        run, tasks = await get_run(session, repo_id, run.id)
        return render(run, tasks)

@router.get('/{repo_id}/documentation/reviews/{run_id}')
async def read_review(repo_id: uuid.UUID, run_id: uuid.UUID):
    async with SessionLocal() as session:
        run, tasks = await get_run(session, repo_id, run_id)
        return render(run, tasks)

@router.post('/{repo_id}/documentation/reviews/{run_id}/resume')
async def resume_review(repo_id: uuid.UUID, run_id: uuid.UUID):
    async with SessionLocal() as session:
        run, tasks = await get_run(session, repo_id, run_id)
        await session.refresh(run, with_for_update=True)
        try:
            compatible = doc_model.compatible_review(run, doc_model.review_settings())
        except doc_model.ProviderError:
            compatible = False
        if not compatible:
            raise HTTPException(409, 'Review model configuration changed; create a new review')
        if run.status != 'paused':
            raise HTTPException(409, 'Only paused reviews can be resumed')
        if run.reason == 'rate_limited' and max([run.manifest.get('review_quota', {}).get('blocked_until_1', 0), run.manifest.get('review_quota', {}).get('blocked_until_2', 0)] + [(t.state or {}).get('next_call_at', 0) for t in tasks]) > time.time():
            raise HTTPException(429, 'Review cooldown has not expired')
        run.job_id = await enqueue(session, repo_id, 'documentation_review', {'run_id': str(run.id)})
        run.status, run.reason, run.retry_after_seconds = 'queued', None, None
        await session.commit()
        return render(run, tasks)

# Draft approval binds all persisted content and the displayed diff.
from app.models import DocumentationDraft
from app.documentation import drafts, publisher, model as documentation_model
from app.db import engine

class DraftApproval(BaseModel):
    draft_hash: str = Field(pattern=r'^[0-9a-f]{64}$')

def draft_render(d):
    return {'id': str(d.id), 'review_id': str(d.review_id), 'base_commit': d.base_commit,
            'draft_hash': d.draft_hash, 'status': d.status, 'diff': d.diff,
            'files': d.files, 'branch': d.branch, 'pr_url': d.pr_url}

def draft_digest(base_commit, files, diff):
    return drafts.digest({'base_commit': base_commit, 'files': files, 'diff': diff})

async def get_draft(session, repo_id, draft_id):
    d = (await session.execute(select(DocumentationDraft).where(DocumentationDraft.repo_id == repo_id, DocumentationDraft.id == draft_id))).scalar_one_or_none()
    if d is None: raise HTTPException(404, 'Draft not found')
    return d

async def _create_draft(repo_id: uuid.UUID, run_id: uuid.UUID, *, allow_partial: bool = False):
    async with SessionLocal() as session:
        run, tasks = await get_run(session, repo_id, run_id)
        repo = await session.get(Repository, repo_id)
        eligible_status = run.status == 'completed' or (allow_partial and run.status == 'paused')
        if not repo or repo.status != 'ready' or repo.commit_sha != run.commit_sha or not eligible_status:
            raise HTTPException(409, 'A completed current review, or explicit allow_partial for a paused current review, is required')
        try:
            tasks = drafts.review_tasks(run, tasks)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        await session.commit()  # Release the transaction before provider calls.
        existing = (await session.execute(select(DocumentationDraft).where(DocumentationDraft.repo_id == repo_id, DocumentationDraft.review_id == run_id).order_by(DocumentationDraft.created_at.desc()).limit(1))).scalar_one_or_none()
        identity = drafts.generation_settings()
        if existing and all(f.get('assessment_snapshot') == drafts.assessment_signature(tasks) and f.get('generation_settings') == identity for f in existing.files):
            return draft_render(existing)
        await session.commit()
        try:
            files, diff = await drafts.generate(run, tasks)
        except documentation_model.ProviderError as exc:
            raise HTTPException(429 if exc.reason == 'rate_limited' else 502, 'Draft generation unavailable') from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        await session.refresh(repo)
        if repo.status != 'ready' or repo.commit_sha != run.commit_sha:
            raise HTTPException(409, 'Repository changed during draft generation')
        d = DocumentationDraft(id=uuid.uuid4(), repo_id=repo_id, review_id=run_id, base_commit=run.commit_sha,
            files=files, diff=diff, draft_hash=draft_digest(run.commit_sha, files, diff), status='draft')
        session.add(d); await session.commit()
        return draft_render(d)

@router.post('/{repo_id}/documentation/reviews/{run_id}/drafts')
async def create_draft(repo_id: uuid.UUID, run_id: uuid.UUID, body: DraftRequest | None = None):
    key = int.from_bytes(hashlib.sha256(b'draft-create' + repo_id.bytes + run_id.bytes).digest()[:8], 'big', signed=True)
    async with engine.connect() as connection:
        locked = (await connection.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': key})).scalar_one()
        await connection.commit()
        if not locked:
            raise HTTPException(409, 'Draft generation is already in progress')
        try:
            return await _create_draft(repo_id, run_id, allow_partial=bool(body and body.allow_partial))
        finally:
            await connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
            await connection.commit()

@router.get('/{repo_id}/documentation/reviews/{run_id}/drafts/latest')
async def latest_draft(repo_id: uuid.UUID, run_id: uuid.UUID):
    async with SessionLocal() as session:
        await get_run(session, repo_id, run_id)
        d = (await session.execute(select(DocumentationDraft).where(DocumentationDraft.repo_id == repo_id, DocumentationDraft.review_id == run_id).order_by(DocumentationDraft.created_at.desc(), DocumentationDraft.id.desc()).limit(1))).scalar_one_or_none()
        return draft_render(d) if d else None

@router.get('/{repo_id}/documentation/drafts/{draft_id}')
async def read_draft(repo_id: uuid.UUID, draft_id: uuid.UUID):
    async with SessionLocal() as session:
        return draft_render(await get_draft(session, repo_id, draft_id))

@router.post('/{repo_id}/documentation/drafts/{draft_id}/publish')
async def publish_draft(repo_id: uuid.UUID, draft_id: uuid.UUID, approval: DraftApproval):
    # A session-level advisory lock remains held across commits and network calls.
    lock_key = int.from_bytes(draft_id.bytes[:8], 'big', signed=True)
    async with engine.connect() as connection:
        locked = (await connection.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': lock_key})).scalar_one()
        await connection.commit()
        if not locked:
            raise HTTPException(409, 'Publication is already in progress')
        try:
            async with SessionLocal(bind=connection) as session:
                d = await get_draft(session, repo_id, draft_id)
                if approval.draft_hash != d.draft_hash or d.draft_hash != draft_digest(d.base_commit, d.files, d.diff):
                    raise HTTPException(409, 'Draft approval does not match current contents')
                if d.status == 'published': return draft_render(d)
                repo = await session.get(Repository, repo_id)
                if not repo or repo.status != 'ready' or repo.commit_sha != d.base_commit:
                    raise HTTPException(409, 'Repository changed; create a new review')
                if d.status not in ('draft', 'publishing'): raise HTTPException(409, 'Draft cannot be published')
                if d.status == 'draft':
                    review, tasks = await get_run(session, repo_id, d.review_id)
                    try:
                        tasks = drafts.review_tasks(review, tasks)
                    except ValueError as exc:
                        raise HTTPException(409, str(exc)) from None
                    signature = drafts.assessment_signature(tasks)
                    if not d.files or any(f.get('assessment_snapshot') != signature for f in d.files):
                        raise HTTPException(409, 'Review findings changed; create a new draft')
                if not d.branch: d.branch = 'sentra/docs-' + str(d.id)
                d.status = 'publishing'
                await session.commit()
                async def save_commit():
                    await session.commit()
                try:
                    d.pr_url = await publisher.publish(repo, d, save_commit)
                except publisher.PublicationError as exc:
                    raise HTTPException(409, str(exc)) from None
                d.status = 'published'
                await session.commit()
                return draft_render(d)
        finally:
            await connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': lock_key})
            await connection.commit()
