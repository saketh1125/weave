"""Lease-fenced whole-Markdown native planning and parallel review persistence."""
import asyncio
import time

from sqlalchemy import select

from app import antigravity_bridge
from app.db import SessionLocal
from app.models import DocumentationReview, DocumentationReviewTask
from . import model, hybrid
from .native_result import validate_result, validate_metrics
from .service import fenced, merge_assessments, assessment_rows


def failure_reason(result):
    if result.get('reason') == 'transport deadline exceeded' or result.get('category') == 'timeout':
        return 'agent_timeout'
    if result.get('rpcErrorCode') == -32000:
        return 'agent_credentials_unavailable'
    return {'model_limit_reported': 'agent_model_limit_reported', 'no_progress': 'agent_no_progress', 'refusal': 'agent_refused', 'budget_exhausted': 'native_prompt_budget_exhausted', 'rate_limited': 'rate_limited', 'provider_connection_failed': 'agent_connection_failed',
            'credential_store_unavailable': 'agent_credentials_unavailable'}.get(result.get('category'), 'agent_failed')


async def run_native_review(job, run_id, snapshot, tasks, manifest):
    settings = manifest['model_settings']
    paths = sorted(task.path for task in tasks)
    if len(paths) != len(set(paths)) or any(task.start_line != 1 or task.content != snapshot.get(task.path) for task in tasks):
        raise RuntimeError('Native workflow requires whole Markdown file tasks')
    async with SessionLocal() as session:
        await fenced(session, job)
        run = await session.get(DocumentationReview, run_id)
        commit_sha = run.commit_sha
        await session.commit()
    static = manifest.get('static_review')
    expected = hybrid.review_fingerprint(snapshot, paths, commit_sha, settings)
    if static is not None and static.get('fingerprint') != expected:
        raise model.ProviderError('model_configuration_changed', request_sent=False)
    static_elapsed = 0
    if static is None:
        started = time.monotonic()
        static = await asyncio.to_thread(hybrid.static_review, snapshot, paths, commit_sha, settings)
        static_elapsed = time.monotonic() - started
        async with SessionLocal() as session:
            await fenced(session, job)
            run = await session.get(DocumentationReview, run_id, with_for_update=True)
            data = dict(run.manifest)
            data['static_review'] = static
            data['static_elapsed_seconds'] = static_elapsed
            prior = data.get('native_cost') or {}
            data['native_cost'] = {'prompt_calls': prior.get('prompt_calls', 0),
                                   'elapsed_seconds': prior.get('elapsed_seconds', 0) + static_elapsed,
                                   **{key: prior.get(key) for key in ('model_calls', 'input_tokens', 'output_tokens')}}
            run.manifest = data
            await session.commit()
    baseline = manifest.get('native_cost') or {}
    remaining = settings['max_prompt_calls'] - baseline.get('prompt_calls', 0)
    previous_triage = set(manifest.get('completed_triage_ids', []))

    def apply_cost(data, metrics):
        validate_metrics(metrics, max(0, remaining))
        value = {'prompt_calls': baseline.get('prompt_calls', 0) + metrics['prompt_calls'],
                 'elapsed_seconds': baseline.get('elapsed_seconds', 0) + static_elapsed + metrics['elapsed_seconds']}
        for key in ('model_calls', 'input_tokens', 'output_tokens'):
            before, current = baseline.get(key, 0), metrics.get(key)
            value[key] = before + current if before is not None and current is not None else None
        data['native_cost'] = value

    previous_completed = {task.path for task in tasks if task.status == 'completed'}
    previous_claims = {task.path: [{'claim': a['claim'], 'documentation_citation': a['documentation_citation']}
                                  for a in (task.state or {}).get('assessments', [])]
                       for task in tasks if task.path not in previous_completed}

    async def checkpoint(result):
        workflow = validate_result(result, snapshot, paths, settings['model'], settings['identity'],
                                   settings['deadline_seconds'], previous_completed, static_review=static,
                                   completed_triage_ids=previous_triage, max_prompt_calls=max(0, remaining))
        async with SessionLocal() as session:
            await fenced(session, job)
            run = await session.get(DocumentationReview, run_id, with_for_update=True)
            if not model.compatible_review(run, model.review_settings()):
                raise model.ProviderError('model_configuration_changed', request_sent=False)
            data = dict(run.manifest)
            if data.get('native_plan') is not None and workflow['plan'] != data['native_plan']:
                raise ValueError('invalid_agent_result')
            if workflow['plan'] is not None:
                data['native_plan'] = workflow['plan']
            apply_cost(data, result['metrics'])
            runtime = {'planner': workflow.get('runtime_activity'), 'agents': [
                {'reviewer': agent['reviewer'], **agent['runtime_activity']}
                for agent in workflow['agents'] if agent.get('runtime_activity') is not None]}
            triage_runtime = workflow.get('static_triage')
            if triage_runtime and triage_runtime.get('runtime_activity') is not None:
                runtime['agents'].append({'reviewer': triage_runtime['reviewer'], **triage_runtime['runtime_activity']})
            data['native_runtime'] = runtime
            current = (await session.execute(select(DocumentationReviewTask).where(
                DocumentationReviewTask.run_id == run_id).with_for_update())).scalars().all()
            by_path = {task.path: task for task in current}
            for agent in workflow['agents']:
                for file in agent['files']:
                    task = by_path[file['path']]
                    state = dict(task.state or {})
                    state['assessments'] = merge_assessments(state.get('assessments', []), file['assessments'])
                    state['native_reviewer'] = agent['reviewer']
                    state['reused_static_ids'] = sorted(set(state.get('reused_static_ids', [])) | {
                        key for key in agent.get('reused_static_ids', [])
                        if any(item['id'] == key and item['doc']['file'] == file['path'] for item in static['items'])})
                    prior_activity = state.get('activity') or {}
                    changed = any(prior_activity.get(key, 0) != agent['activity'].get(key, 0) for key in ('steps', 'errors', 'claims_recorded'))
                    state['activity'] = {**agent['activity'], 'phase': 'investigating',
                        'updated_at': time.time() if changed else prior_activity.get('updated_at', 0)}
                    if file['status'] == 'completed':
                        task.status = 'completed'
                        if file['path'] not in previous_completed:
                            state['no_factual_claims'] = file.get('no_factual_claims') is True
                            state['completion_reason'] = file.get('reason')
                    task.state = state
                    task.assessments = assessment_rows(state['assessments'])
            triage = workflow.get('static_triage')
            if triage is not None:
                for entry in triage['assessments']:
                    assessment = entry['assessment']
                    task = by_path[assessment['documentation_citation']['path']]
                    state = dict(task.state or {})
                    state['assessments'] = merge_assessments(state.get('assessments', []), [assessment])
                    task.state = state
                    task.assessments = assessment_rows(state['assessments'])
                data['completed_triage_ids'] = sorted(set(triage['processed_ids']))
                data['native_triage_finished'] = triage['finished']
            run.manifest = data
            await session.commit()

    result = None
    reason = None
    try:
        async with SessionLocal() as session:
            await fenced(session, job)
            await session.commit()
        if remaining <= 0:
            reason = 'native_prompt_budget_exhausted'
        elif paths:
            result = await antigravity_bridge.workflow(snapshot, paths, settings['model'], settings['identity'],
                plan=manifest.get('native_plan'), completed_files=sorted(previous_completed),
                reviewed_claims=previous_claims, static_review=static, max_prompt_calls=remaining,
                completed_triage_ids=sorted(previous_triage), on_progress=checkpoint)
            if result.get('workflow') is not None:
                await checkpoint(result)
            elif result.get('status') != 'failed':
                raise ValueError('invalid_agent_result')
            elif result.get('metrics') is not None:
                async with SessionLocal() as session:
                    await fenced(session, job)
                    run = await session.get(DocumentationReview, run_id, with_for_update=True)
                    data = dict(run.manifest)
                    apply_cost(data, result['metrics'])
                    run.manifest = data
                    await session.commit()
            if result.get('category') in {'budget_exhausted', 'no_progress', 'refusal', 'timeout', 'model_limit_reported'}:
                reason = failure_reason(result)
            if result.get('cleanup_failed') is True:
                reason = 'agent_cleanup_failed'
    except model.ProviderError as exc:
        reason = exc.reason
    except (ValueError, TypeError, KeyError, AttributeError):
        reason = 'invalid_agent_result'
    async with SessionLocal() as session:
        await fenced(session, job)
        run = await session.get(DocumentationReview, run_id, with_for_update=True)
        if not model.compatible_review(run, model.review_settings()):
            reason = 'model_configuration_changed'
        current = (await session.execute(select(DocumentationReviewTask).where(
            DocumentationReviewTask.run_id == run_id))).scalars().all()
        missing = any(path.lower().endswith('.md') for path in run.manifest['unreadable_files'])
        if reason is not None:
            run.status = 'paused' if reason in ('rate_limited', 'agent_timeout', 'agent_bridge_unavailable', 'native_prompt_budget_exhausted', 'agent_no_progress', 'agent_model_limit_reported') else 'failed'
            run.reason = reason
        elif missing:
            run.status, run.reason = 'failed', 'indexed_markdown_unavailable'
        elif not paths or (result.get('status') == 'passed' and all(task.status == 'completed' for task in current)):
            run.status, run.reason = 'completed', None
        elif result.get('status') == 'failed':
            run.reason = failure_reason(result)
            run.status = 'paused' if run.reason in ('rate_limited', 'agent_timeout', 'agent_connection_failed', 'native_prompt_budget_exhausted') else 'failed'
        else:
            run.status, run.reason = 'paused', 'agent_incomplete'
        run.retry_after_seconds = None
        await session.commit()
        return {'run_id': str(run_id), 'status': run.status}
