"""Lease-fenced orchestration of immutable documentation review tasks."""
import hashlib
import json
import uuid
from sqlalchemy import func, select
from app.db import SessionLocal
from app.models import Job, Repository, DocumentationReview, DocumentationReviewTask
from .snapshot import SnapshotTools

class RunBudgetExhausted(Exception):
    pass


def merge_assessments(previous, current):
    # A fresh native session can rediscover claims saved before a pause.
    return list({json.dumps([a['claim'], a['documentation_citation']], sort_keys=True): a
                 for a in previous + current}.values())

def assessment_rows(assessments):
    return [dict(id=hashlib.sha256(json.dumps(a, sort_keys=True).encode()).hexdigest(),
        verdict=a['verdict'], claim=a['claim'], explanation=a['rationale'], suggested_update=a['suggested_update'],
        doc={'file': a['documentation_citation']['path'], 'start_line': a['documentation_citation']['start_line'], 'end_line': a['documentation_citation']['end_line']}, code_citations=[{'file': c['path'], 'start_line': c['start_line'], 'end_line': c['end_line']} for c in a['code_citations']]) for a in assessments]

async def fenced(session, job):
    current = (await session.execute(select(Job).where(Job.id == job.id, Job.repo_id == job.repo_id,
        Job.status == 'running', Job.lease_token == job.lease_token, Job.attempts == job.attempts,
        Job.lease_expires_at > func.now()).with_for_update())).scalar_one_or_none()
    if current is None or job.lease_token is None:
        raise RuntimeError('Job lease lost')
    run = (await session.execute(select(DocumentationReview).where(DocumentationReview.id == uuid.UUID(job.payload['run_id']), DocumentationReview.repo_id == job.repo_id))).scalar_one_or_none()
    repo = await session.get(Repository, job.repo_id)
    if run is None:
        raise RuntimeError('Review missing')
    if repo is None or repo.commit_sha != run.commit_sha:
        run.status, run.reason = 'stale', 'indexed_commit_changed'
        await session.commit()
        raise RuntimeError('Indexed commit changed')
    return current

async def run_review(job):
    import asyncio
    from app import model_connections
    import time
    from . import model as provider, planner, quota
    from .reviewer import review_section
    def compatible(run):
        try:
            return provider.compatible_review(run, provider.review_settings())
        except provider.ProviderError:
            return False
    run_id = uuid.UUID(job.payload['run_id'])
    async with SessionLocal() as session:
        await fenced(session, job)
        run = (await session.execute(select(DocumentationReview).where(DocumentationReview.id == run_id, DocumentationReview.repo_id == job.repo_id, DocumentationReview.job_id == job.id).with_for_update())).scalar_one_or_none()
        if run is None:
            raise RuntimeError('Review job mismatch')
        if not compatible(run):
            run.status, run.reason = 'failed', 'model_configuration_changed'
            await session.commit()
            return {'run_id': str(run_id), 'status': run.status}
        run.status = 'running'
        tasks = (await session.execute(select(DocumentationReviewTask).where(DocumentationReviewTask.run_id == run_id).order_by(DocumentationReviewTask.path, DocumentationReviewTask.start_line))).scalars().all()
        snapshot, model, manifest = run.snapshot, run.model, dict(run.manifest)
        limits = provider.review_limits()
        await session.commit()

    async def request(messages, tool_specs, *, model, max_tokens, credential_slot=1, planning=False, prompt_tokens_upper_bound=None, force_assessment=False):
        workflow = 'planner' if planning else 'reviewer'
        connection = model_connections.resolve(workflow, 1 if planning else credential_slot)
        model = connection.model
        max_tokens = min(max_tokens, connection.max_output_tokens)
        # Lock the same job row as every task checkpoint before reserving a call.
        async with SessionLocal() as session:
            await fenced(session, job)
            run = await session.get(DocumentationReview, run_id, with_for_update=True)
            if not compatible(run):
                raise provider.ProviderError('model_configuration_changed', request_sent=False)
            data = dict(run.manifest)
            q = dict(data.get('review_quota', {}))
            remaining = q.get(f'blocked_until_{credential_slot}', 0) - time.time()
            if remaining > 0:
                await session.commit()
                raise provider.ProviderError('rate_limited', max(1, int(remaining + .999)), request_sent=False)
            if planning:
                all_tasks = (await session.execute(select(DocumentationReviewTask).where(DocumentationReviewTask.run_id == run_id))).scalars().all()
                reserve = len(json.dumps(messages, ensure_ascii=False).encode()) + max_tokens
                if q.get('planner_calls', 0) + sum((t.state or {}).get('calls', 0) for t in all_tasks) >= limits['calls'] or q.get('planner_tokens', 0) + sum((t.state or {}).get('tokens', 0) for t in all_tasks) + reserve > limits['tokens']:
                    raise provider.ProviderError('run_budget_exhausted', request_sent=False)
                q['planner_calls'] = q.get('planner_calls', 0) + 1
                q['planner_tokens'] = q.get('planner_tokens', 0) + reserve
                data['review_quota'] = q
                run.manifest = data
            await session.commit()
        async def preflight():
            async with SessionLocal() as session:
                await fenced(session, job)
                current_run = await session.get(DocumentationReview, run_id)
                if not compatible(current_run):
                    raise provider.ProviderError('model_configuration_changed', request_sent=False)
                await session.commit()
        try:
            return await quota.request(messages, tool_specs, model_name=model, max_tokens=max_tokens, credential_slot=credential_slot, preflight=preflight, prompt_tokens_upper_bound=prompt_tokens_upper_bound, force_assessment=force_assessment, connection=connection, workflow=workflow)
        except provider.ProviderError as exc:
            if exc.reason == 'rate_limited' or (planning and not exc.request_sent):
                async with SessionLocal() as session:
                    await fenced(session, job)
                    run = await session.get(DocumentationReview, run_id, with_for_update=True)
                    data = dict(run.manifest); q = dict(data.get('review_quota', {}))
                    if exc.reason == 'rate_limited':
                        key = f'blocked_until_{credential_slot}'
                        q[key] = max(q.get(key, 0), time.time() + (exc.retry_after_seconds or 60))
                    if planning and not exc.request_sent:
                        q['planner_calls'] = max(0, q.get('planner_calls', 0) - 1)
                        q['planner_tokens'] = max(0, q.get('planner_tokens', 0) - reserve)
                    data['review_quota'] = q; run.manifest = data
                    await session.commit()
            raise

    agent_settings = manifest.get('model_settings', {})
    is_agent = agent_settings.get('kind') == 'antigravity'
    plan = manifest.get('review_plan')
    try:
        if is_agent and plan is None:
            plan = {'version': 1, 'assignments': [[str(t.id) for t in tasks]] if tasks else []}
            async with SessionLocal() as session:
                await fenced(session, job)
                run = await session.get(DocumentationReview, run_id, with_for_update=True)
                if not compatible(run):
                    raise provider.ProviderError('model_configuration_changed', request_sent=False)
                data = dict(run.manifest); data['review_plan'] = plan; run.manifest = data
                await session.commit()
        elif plan is None:
            async def plan_request(messages, tool_specs, *, model, max_tokens):
                return await request(messages, tool_specs, model=model, max_tokens=max_tokens, planning=True)
            plan = await planner.create_plan(tasks, model, plan_request, max_reviewers=len(provider.review_settings()['reviewers']), max_attempts=model_connections.resolve('planner').max_attempts)
            async with SessionLocal() as session:
                await fenced(session, job)
                run = await session.get(DocumentationReview, run_id, with_for_update=True)
                data = dict(run.manifest); data['review_plan'] = plan; run.manifest = data
                await session.commit()
        elif tasks:
            if plan.get('version') != 1:
                raise ValueError('invalid_plan')
            planner.validate_plan({'assignments': plan.get('assignments')}, [str(t.id) for t in tasks])
        elif plan != {'version': 1, 'assignments': []}:
            raise ValueError('invalid_plan')
        if is_agent and plan != {'version': 1, 'assignments': [[str(t.id) for t in tasks]] if tasks else []}:
            raise ValueError('invalid_plan')
        if not is_agent and len(plan['assignments']) > len(provider.review_settings()['reviewers']):
            raise provider.ProviderError('missing_credentials')
    except (ValueError, provider.ProviderError) as exc:
        reason = exc.reason if isinstance(exc, provider.ProviderError) else str(exc)
        async with SessionLocal() as session:
            await fenced(session, job)
            run = await session.get(DocumentationReview, run_id, with_for_update=True)
            run.status = 'paused' if isinstance(exc, provider.ProviderError) and exc.retryable else 'failed'
            if isinstance(exc, provider.ProviderError):
                data = dict(run.manifest); data['provider_failure'] = exc.diagnostic(); run.manifest = data
            run.reason = reason
            run.retry_after_seconds = getattr(exc, 'retry_after_seconds', None)
            await session.commit()
            return {'run_id': str(run_id), 'status': run.status}

    tools = SnapshotTools(snapshot, job.repo_id)
    by_id = {str(t.id): t for t in tasks}
    async def worker(group, slot):
        for task_id in group:
            task = by_id[task_id]
            if task.status in ('completed', 'failed'): continue
            section = {'id': task_id, 'path': task.path, 'start_line': task.start_line, 'end_line': task.end_line, 'content': task.content}
            if task_id in manifest.get('section_context', {}):
                section['heading_context'] = manifest['section_context'][task_id]
                coverage = manifest.get('source_coverage') or {}
                section['source_coverage'] = {key: coverage.get(key) for key in ('status', 'reason')}
                section['source_coverage']['unavailable_files'] = len(coverage.get('unavailable_paths', []))
            async def save_state(state):
                async with SessionLocal() as session:
                    await fenced(session, job)
                    run = await session.get(DocumentationReview, run_id, with_for_update=True)
                    if run.reason == 'run_budget_exhausted':
                        raise RunBudgetExhausted
                    locked = await session.get(DocumentationReviewTask, task.id, with_for_update=True)
                    all_tasks = (await session.execute(select(DocumentationReviewTask).where(DocumentationReviewTask.run_id == run_id))).scalars().all()
                    q = run.manifest.get('review_quota', {})
                    calls = q.get('planner_calls', 0) + sum((t.state or {}).get('calls', 0) for t in all_tasks if t.id != task.id) + state.get('calls', 0)
                    tokens = q.get('planner_tokens', 0) + sum((t.state or {}).get('tokens', 0) for t in all_tasks if t.id != task.id) + state.get('tokens', 0)
                    exceeded = calls > limits['calls'] or tokens > limits['tokens']
                    locked.state = state; locked.assessments = assessment_rows(state.get('assessments', []))
                    if exceeded:
                        run.status, run.reason = 'failed', 'run_budget_exhausted'
                    await session.commit()
                    if exceeded:
                        raise RunBudgetExhausted
            async def complete(messages, tool_specs, *, model, max_tokens, prompt_tokens_upper_bound=None, force_assessment=False):
                return await request(messages, tool_specs, model=model, max_tokens=max_tokens, credential_slot=slot, prompt_tokens_upper_bound=prompt_tokens_upper_bound, force_assessment=force_assessment)
            if is_agent:
                from . import agent_review
                import time
                agent_state = dict(task.state or {})
                section['reviewed_claims'] = []
                for assessment in agent_state.get('assessments', []):
                    previous = {'claim': assessment['claim'], 'documentation_citation': assessment['documentation_citation']}
                    context = {key: section[key] for key in ('heading_context', 'source_coverage', 'reviewed_claims') if key in section}
                    context['reviewed_claims'] = section['reviewed_claims'] + [previous]
                    if len(json.dumps(context).encode()) > 12000:
                        break
                    section['reviewed_claims'].append(previous)
                async def save_agent_state(update):
                    if 'assessments' in update:
                        update['assessments'] = merge_assessments(agent_state.get('assessments', []), update['assessments'])
                    agent_state.update(update)
                    await save_state(agent_state)
                await save_agent_state({'agent_version': 1, 'activity': {
                    'steps': 0, 'errors': 0, 'claims_recorded': 0,
                    'phase': 'investigating', 'updated_at': time.time()}})
                async with SessionLocal() as session:
                    await fenced(session, job)
                    run = await session.get(DocumentationReview, run_id)
                    if not compatible(run):
                        result = {'status': 'failed', 'reason': 'model_configuration_changed'}
                    else:
                        result = None
                    await session.commit()
                if result is None:
                    try:
                        result = await agent_review.review_section(snapshot, section, model, agent_settings['identity'], save_state=save_agent_state)
                    except provider.ProviderError as exc:
                        result = {'status': 'failed', 'reason': exc.reason}
            else:
                try:
                    result = await review_section(section, tools, model=model_connections.resolve('reviewer', slot).model, state=task.state or None, save_state=save_state, complete=complete)
                except RunBudgetExhausted:
                    return
            async with SessionLocal() as session:
                await fenced(session, job)
                run = await session.get(DocumentationReview, run_id, with_for_update=True)
                current = await session.get(DocumentationReviewTask, task.id, with_for_update=True)
                if not compatible(run):
                    result = {'status': 'failed', 'reason': 'model_configuration_changed'}
                if run.reason == 'run_budget_exhausted':
                    return
                if is_agent:
                    state = {**(current.state or {}), **result.get('state', {})}
                    if 'assessments' in result:
                        state['assessments'] = merge_assessments((current.state or {}).get('assessments', []), result['assessments'])
                        current.assessments = assessment_rows(state['assessments'])
                        result['assessments'] = state['assessments']
                    if 'activity' in state:
                        state['activity'] = {**state['activity'], 'phase': 'paused' if result['status'] != 'completed' else 'investigating'}
                    current.state = state
                if result['status'] == 'completed':
                    current.status = 'completed'; current.assessments = assessment_rows(result['assessments'])
                elif result['status'] == 'failed':
                    current.status = 'failed'; run.status, run.reason = 'failed', result.get('reason')
                elif run.status != 'failed':
                    run.status, run.reason = 'paused', result.get('reason')
                    run.retry_after_seconds = result.get('retry_after_seconds')
                await session.commit()
            if result['status'] != 'completed': return

    slots = [1, 2]
    await asyncio.gather(*(worker(group, slots[i]) for i, group in enumerate(plan['assignments'])))
    async with SessionLocal() as session:
        await fenced(session, job)
        run = await session.get(DocumentationReview, run_id, with_for_update=True)
        if run.status in ('paused', 'failed'):
            return {'run_id': str(run_id), 'status': run.status}
        failed = (await session.execute(select(DocumentationReviewTask.id).where(DocumentationReviewTask.run_id == run_id, DocumentationReviewTask.status == 'failed').limit(1))).scalar_one_or_none() is not None
        incomplete = any(p.lower().endswith('.md') for p in run.manifest['unreadable_files'])
        run.status = 'failed' if incomplete or failed else 'completed'
        run.reason = 'indexed_markdown_unavailable' if incomplete else ('section_failed' if failed else None)
        await session.commit()
        return {'run_id': str(run_id), 'status': run.status}
