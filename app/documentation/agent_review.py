"""Validation of a bounded native-agent review result."""
import hashlib
import json
import time

from .assessment import validate_assessment


async def review_section(snapshot, section, model, identity, save_state=None):
    from app import antigravity_bridge
    async def checkpoint(update):
        if (not isinstance(update, dict) or update.get('model') != model or update.get('identity') != identity
                or not isinstance(update.get('activity'), dict)):
            raise antigravity_bridge.ProviderError('invalid_agent_result')
        activity = update['activity']
        if any(type(activity.get(key)) is not int or not 0 <= activity[key] <= limit
               for key, limit in (('steps', 64), ('errors', 10000), ('claims_recorded', 30))):
            raise antigravity_bridge.ProviderError('invalid_agent_result')
        validated = validate_result(update, snapshot, section, model, identity)
        if validated.get('reason') == 'invalid_agent_result' or 'assessments' not in validated:
            raise antigravity_bridge.ProviderError('invalid_agent_result')
        await save_state({**validated['state'], 'activity': {
            **{key: activity[key] for key in ('steps', 'errors', 'claims_recorded')},
            'phase': 'investigating', 'updated_at': time.time()}})
    options = {'expected_identity': identity}
    if save_state is not None:
        options['on_progress'] = checkpoint
    result = await antigravity_bridge.review(snapshot, section, model, **options)
    return validate_result(result, snapshot, section, model, identity)


def validate_result(result, snapshot, section, model, identity):
    """Validate both final results and unfinished evidence checkpoints identically."""
    if not isinstance(result, dict) or result.get('model') != model or result.get('identity') != identity:
        return {'status': 'failed', 'reason': 'invalid_agent_result'}
    failure = None
    if result.get('status') == 'failed':
        # Only fixed diagnostic codes cross into persistence; never native error text.
        category = result.get('category') if isinstance(result.get('category'), str) else None
        reason = {'rate_limited': 'rate_limited',
                  'provider_connection_failed': 'agent_connection_failed',
                  'credential_store_unavailable': 'agent_credentials_unavailable'}.get(category, 'agent_failed')
        if result.get('reason') == 'transport deadline exceeded':
            reason = 'agent_timeout'
        state = {'agent_version': 1, 'failure': {'reason': reason}}
        if result.get('stage') in ('proxy', 'container', 'native_interactive_setup', 'acp_session', 'validate_evidence'):
            state['failure']['stage'] = result['stage']
        if type(result.get('rpcErrorCode')) is int:
            state['failure']['rpc_error_code'] = result['rpcErrorCode']
        if result.get('operation') in ('initialize', 'session_new', 'configure', 'session_prompt'):
            state['failure']['operation'] = result['operation']
        progress = result.get('progress')
        if isinstance(progress, dict):
            state['failure'].update({key: progress[key] for key in ('steps', 'errors')
                if type(progress.get(key)) is int and 0 <= progress[key] <= 10000})
        failure = {'status': 'paused' if reason in ('rate_limited', 'agent_timeout') else 'failed',
                   'reason': reason, 'state': state}
    elif result.get('status') != 'passed':
        failure = {'status': 'paused' if result.get('status') == 'incomplete' else 'failed',
                   'reason': 'agent_incomplete' if result.get('status') == 'incomplete' else 'agent_failed'}
    if failure and not all(key in result for key in ('review', 'execution', 'models')):
        return failure
    try:
        review = result['review']
        ranges = review['read_ranges']
        assessments = review['assessments']
        core = {key: section[key] for key in ('id', 'path', 'start_line', 'end_line', 'content')}
        canonical = json.dumps({'snapshot': snapshot, 'section': core, 'limits': {'max_steps': 64, 'max_output_bytes': 65536}}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        execution = result['execution']
        claimless = (assessments == [] and review.get('no_factual_claims') is True
                     and type(review.get('errors')) is int and review['errors'] == 0
                     and isinstance(review.get('reason'), str) and 0 < len(review['reason'].strip()) <= 1024)
        if (type(review['finished']) is not bool or (failure is None and review['finished'] is not True)
                or review.get('section_id') != section['id']
                or review.get('job_identity_sha256') != digest or not isinstance(ranges, list)
                or len(ranges) > 512 or not isinstance(assessments, list) or not 0 <= len(assessments) <= 30
                or (failure is None and not assessments and not claimless)
                or not isinstance(execution, dict) or not isinstance(result.get('models'), list)
                or model not in result['models']):
            raise ValueError('invalid result')
        binding = {k: v for k, v in execution.items() if k != 'sha256'}
        if (set(binding) != {'adapter', 'runtime', 'model', 'job_sha256', 'prompt_sha256', 'settings_sha256', 'deadline_seconds', 'native_session_resumable'}
                or binding['adapter'] != 'antigravity-native-acp-v1' or binding['runtime'] != '1.5.8'
                or binding['model'] != model or binding['job_sha256'] != digest
                or type(binding['deadline_seconds']) is not int or not 1 <= binding['deadline_seconds'] <= 300
                or binding['native_session_resumable'] is not False):
            raise ValueError('invalid execution binding')
        for key in ('prompt_sha256', 'settings_sha256'):
            value = binding[key]
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('invalid execution hash')
        expected = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
        if execution.get('sha256') != expected:
            raise ValueError('invalid execution hash')
        for item in ranges:
            if not isinstance(item, dict) or set(item) != {'path', 'start_line', 'end_line'}:
                raise ValueError('invalid range')
            path, start, end = item['path'], item['start_line'], item['end_line']
            if (not isinstance(path, str) or path not in snapshot or type(start) is not int or type(end) is not int
                    or start < 1 or end < start or end > len(snapshot[path].splitlines())):
                raise ValueError('invalid range')
        for index, assessment in enumerate(assessments):
            validate_assessment(assessment, section, ranges, index)
    except (KeyError, TypeError, ValueError):
        return {'status': 'failed', 'reason': 'invalid_agent_result'}
    if failure:
        failure['assessments'] = assessments
        failure.setdefault('state', {'agent_version': 1}).update(
            assessments=assessments, job_identity_sha256=review['job_identity_sha256'])
        return failure
    return {'status': 'completed', 'assessments': assessments,
            'state': {'agent_version': 1, 'job_identity_sha256': review['job_identity_sha256']}}
