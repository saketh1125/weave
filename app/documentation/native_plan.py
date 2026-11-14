"""Validate native planner assignments against the immutable file inventory."""


def validate_plan(value, paths, unverified_count=0, triage_threshold=20):
    if (not isinstance(paths, list) or any(not isinstance(path, str) or not path for path in paths)
            or len(set(paths)) != len(paths)):
        raise ValueError('invalid_plan')
    if not isinstance(value, dict) or set(value) - {'assignments', 'static_triage'} or 'assignments' not in value:
        raise ValueError('invalid_plan')
    triage = value.get('static_triage', False)
    if type(triage) is not bool or type(unverified_count) is not int or type(triage_threshold) is not int or triage_threshold < 1 or (triage and unverified_count < triage_threshold):
        raise ValueError('invalid_plan')
    groups = value['assignments']
    if not isinstance(groups, list) or not 1 <= len(groups) <= min(5 - int(triage), len(paths)):
        raise ValueError('invalid_plan')
    if any(not isinstance(group, list) or not group
           or any(not isinstance(path, str) for path in group) for group in groups):
        raise ValueError('invalid_plan')
    assigned = [path for group in groups for path in group]
    if len(assigned) != len(paths) or len(set(assigned)) != len(assigned) or set(assigned) != set(paths):
        raise ValueError('invalid_plan')
    return {'version': 2, 'assignments': groups, 'static_triage': triage}
