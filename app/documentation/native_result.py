"""Validate snapshot-bound hybrid file review results before persistence."""
import hashlib
import json
import math

from .assessment import validate_assessment
from .native_plan import validate_plan


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def job_identity(snapshot, paths, static_review=None):
    value = {'snapshot': snapshot, 'documentation_files': sorted(paths)}
    if static_review is not None:
        value['static_review'] = static_review
    return digest(value)


def validate_ranges(ranges, snapshot):
    if not isinstance(ranges, list) or len(ranges) > 4096:
        raise ValueError('invalid_agent_result')
    for item in ranges:
        if not isinstance(item, dict) or set(item) != {'path', 'start_line', 'end_line'}:
            raise ValueError('invalid_agent_result')
        path, start, end = item['path'], item['start_line'], item['end_line']
        if (not isinstance(path, str) or path not in snapshot or type(start) is not int or type(end) is not int
                or not 1 <= start <= end <= len(snapshot[path].splitlines())):
            raise ValueError('invalid_agent_result')


def validate_activity(activity):
    if not isinstance(activity, dict):
        raise ValueError('invalid_agent_result')
    for key, maximum in (('steps', 1024), ('errors', 10000), ('claims_recorded', 500)):
        if type(activity.get(key)) is not int or not 0 <= activity[key] <= maximum:
            raise ValueError('invalid_agent_result')


def validate_metrics(metrics, max_prompt_calls):
    if (not isinstance(metrics, dict) or type(metrics.get('prompt_calls')) is not int
            or not 0 <= metrics['prompt_calls'] <= max_prompt_calls
            or type(metrics.get('elapsed_seconds')) not in (int, float)
            or not math.isfinite(metrics['elapsed_seconds']) or metrics['elapsed_seconds'] < 0):
        raise ValueError('invalid_agent_result')
    for key in ('model_calls', 'input_tokens', 'output_tokens'):
        value = metrics.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError('invalid_agent_result')


def validate_result(result, snapshot, paths, model, identity, deadline, completed=(), *,
                    static_review=None, completed_triage_ids=(), max_prompt_calls=100):
    if not isinstance(result, dict) or result.get('model') != model or result.get('identity') != identity:
        raise ValueError('invalid_agent_result')
    if result.get('status') not in ('passed', 'incomplete', 'failed'):
        raise ValueError('invalid_agent_result')
    offered = result.get('models')
    if not isinstance(offered, list) or len(offered) > 100 or not all(isinstance(item, str) for item in offered) or model not in offered:
        raise ValueError('invalid_agent_result')
    if static_review is not None or result.get('metrics') is not None:
        validate_metrics(result.get('metrics'), max_prompt_calls)
    workflow = result.get('workflow')
    if (not isinstance(workflow, dict) or type(workflow.get('version')) is not int or workflow['version'] != 1
            or workflow.get('job_identity_sha256') != job_identity(snapshot, paths, static_review)):
        raise ValueError('invalid_agent_result')
    items = {item['id']: item for item in (static_review or {}).get('items', [])}
    leads = {key: item for key, item in items.items() if item['status'] == 'unverified'}
    plan = workflow.get('plan')
    if plan is not None:
        if not isinstance(plan, dict) or type(plan.get('version')) is not int or plan['version'] != 2:
            raise ValueError('invalid_agent_result')
        expected = validate_plan({key: plan[key] for key in ('assignments', 'static_triage') if key in plan}, paths,
                                 len(leads), (static_review or {}).get('triage_threshold', 20))
        if plan != expected:
            raise ValueError('invalid_agent_result')
    agents = workflow.get('agents')
    if not isinstance(agents, list) or len(agents) != (len(plan['assignments']) if plan else 0):
        raise ValueError('invalid_agent_result')
    execution = result.get('execution')
    if not isinstance(execution, dict):
        raise ValueError('invalid_agent_result')
    binding = {key: value for key, value in execution.items() if key != 'sha256'}
    if (set(binding) != {'adapter', 'runtime', 'model', 'job_sha256', 'prompt_sha256', 'settings_sha256', 'deadline_seconds', 'native_session_resumable', 'plan_sha256'}
            or binding['adapter'] != 'antigravity-native-workflow-v1' or binding['runtime'] != '1.5.8'
            or binding['model'] != model or binding['job_sha256'] != workflow['job_identity_sha256']
            or type(binding['deadline_seconds']) is not int or binding['deadline_seconds'] != deadline
            or binding['native_session_resumable'] is not False or binding['plan_sha256'] != digest(plan)
            or execution.get('sha256') != digest(binding)):
        raise ValueError('invalid_agent_result')
    for key in ('prompt_sha256', 'settings_sha256'):
        value = binding[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('invalid_agent_result')
    finished = set()
    for index, agent in enumerate(agents, 1):
        if not isinstance(agent, dict) or type(agent.get('reviewer')) is not int or agent['reviewer'] != index:
            raise ValueError('invalid_agent_result')
        files, ranges = agent.get('files'), agent.get('read_ranges')
        owned = plan['assignments'][index-1]
        if not isinstance(files, list) or not all(isinstance(file, dict) for file in files) or [file.get('path') for file in files] != owned:
            raise ValueError('invalid_agent_result')
        validate_ranges(ranges, snapshot)
        validate_activity(agent.get('activity'))
        reused = agent.get('reused_static_ids', [])
        if (not isinstance(reused, list) or not all(isinstance(key, str) for key in reused)
                or len(set(reused)) != len(reused) or any(key not in items or items[key]['status'] not in ('aligned', 'outdated')
                    or items[key]['doc']['file'] not in owned for key in reused)):
            raise ValueError('invalid_agent_result')
        count = 0
        for file in files:
            path = file['path']
            assessments = file.get('assessments')
            if file.get('status') not in ('pending', 'completed') or not isinstance(assessments, list):
                raise ValueError('invalid_agent_result')
            count += len(assessments)
            if count > 500:
                raise ValueError('invalid_agent_result')
            section = {'path': path, 'start_line': 1, 'end_line': max(1, len(snapshot[path].splitlines())), 'content': snapshot[path]}
            for assessment in assessments:
                validate_assessment(assessment, section, ranges)
            if file['status'] == 'completed':
                reason = file.get('reason')
                reused_file = any(items[key]['doc']['file'] == path for key in reused)
                if not assessments and path not in completed and not reused_file and not (
                        file.get('no_factual_claims') is True and isinstance(reason, str) and 0 < len(reason.strip()) <= 1024):
                    raise ValueError('invalid_agent_result')
                finished.add(path)
    triage = workflow.get('static_triage')
    if plan and plan['static_triage']:
        if not isinstance(triage, dict) or type(triage.get('reviewer')) is not int or triage['reviewer'] != len(agents)+1:
            raise ValueError('invalid_agent_result')
        ids, processed, assessments = triage.get('finding_ids'), triage.get('processed_ids'), triage.get('assessments')
        if (not isinstance(ids, list) or not all(isinstance(key, str) for key in ids) or len(ids) != len(leads) or set(ids) != set(leads)
                or not isinstance(processed, list) or not all(isinstance(key, str) for key in processed) or len(processed) != len(set(processed))
                or not set(processed).issubset(leads) or not isinstance(assessments, list) or len(assessments) > 500):
            raise ValueError('invalid_agent_result')
        validate_ranges(triage.get('read_ranges'), snapshot)
        validate_activity(triage.get('activity'))
        recorded = set()
        for entry in assessments:
            if not isinstance(entry, dict) or set(entry) != {'finding_id', 'assessment'} or not isinstance(entry.get('finding_id'), str):
                raise ValueError('invalid_agent_result')
            key, assessment = entry['finding_id'], entry['assessment']
            if key not in leads or key in recorded:
                raise ValueError('invalid_agent_result')
            citation = leads[key]['doc']
            actual = assessment.get('documentation_citation') if isinstance(assessment, dict) else None
            if actual != {'path': citation['file'], 'start_line': citation['start_line'], 'end_line': citation['end_line']} or ' '.join(assessment.get('claim', '').split()) != ' '.join(leads[key]['claim'].split()):
                raise ValueError('invalid_agent_result')
            section = {'path': citation['file'], 'start_line': citation['start_line'], 'end_line': citation['end_line'],
                       'content': '\n'.join(snapshot[citation['file']].splitlines()[citation['start_line']-1:citation['end_line']])}
            validate_assessment(assessment, section, triage['read_ranges'])
            recorded.add(key)
        if set(processed) != recorded | set(completed_triage_ids) or type(triage.get('finished')) is not bool:
            raise ValueError('invalid_agent_result')
        if triage['finished'] and set(processed) != set(leads):
            raise ValueError('invalid_agent_result')
    elif triage is not None:
        raise ValueError('invalid_agent_result')
    if result['status'] == 'passed' and (plan is None or finished != set(paths) or (plan['static_triage'] and not triage['finished'])):
        raise ValueError('invalid_agent_result')
    return workflow
