"""Snapshot-bound static documentation checks and conservative evidence merging."""
import hashlib
import json
from pathlib import Path

from app.doc_alignment import SourceFile, check_alignment


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def engine_fingerprint():
    module_root = Path(__file__).resolve().parent.parent
    return _digest({name: hashlib.sha256((module_root / name).read_bytes()).hexdigest()
                    for name in ('doc_alignment.py', 'request_alignment.py', 'express_alignment.py')})


def review_fingerprint(snapshot, paths, commit_sha, review_config):
    if (not isinstance(snapshot, dict) or not isinstance(paths, list)
            or any(not isinstance(p, str) or p not in snapshot or not p.lower().endswith('.md') for p in paths)
            or len(set(paths)) != len(paths) or not isinstance(commit_sha, str) or not isinstance(review_config, dict)):
        raise ValueError('invalid_static_review_input')
    return _digest({'engine_hash': engine_fingerprint(), 'snapshot': snapshot, 'documentation_files': sorted(paths),
                    'commit_sha': commit_sha, 'engine': 'doc_alignment_v1', 'review_config': review_config})


def static_review(snapshot, paths, commit_sha, review_config):
    fingerprint = review_fingerprint(snapshot, paths, commit_sha, review_config)
    docs = [SourceFile(path, snapshot[path]) for path in sorted(paths)]
    code = [SourceFile(path, text) for path, text in sorted(snapshot.items()) if path.lower().endswith(('.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'))]
    items = []
    for index, finding in enumerate(check_alignment(docs, code)):
        doc = finding['doc']
        claim = '\n'.join(snapshot[doc['file']].splitlines()[doc['start_line'] - 1:doc['end_line']])
        item = {**finding, 'claim': claim}
        item['id'] = _digest({'fingerprint': fingerprint, 'index': index, 'finding': item})
        items.append(item)
    counts = {status: sum(item['status'] == status for item in items) for status in ('aligned', 'outdated', 'unverified')}
    threshold = review_config.get('static_triage_threshold', 20)
    if type(threshold) is not int or threshold < 1:
        raise ValueError('invalid_static_review_input')
    return {'version': 1, 'fingerprint': fingerprint, 'items': items, 'counts': counts, 'triage_threshold': threshold}


def merge_findings(static_items, ai_rows):
    """Group only identical normalized claims with identical document citations."""
    merged = []
    index = {}
    for origin, findings in (('static', static_items), ('ai', ai_rows)):
        for finding in findings:
            doc = finding.get('doc') or finding.get('documentation_citation')
            claim = finding.get('claim')
            if not isinstance(claim, str) or not isinstance(doc, dict):
                raise ValueError('invalid_finding')
            path = doc.get('file', doc.get('path'))
            start, end = doc.get('start_line'), doc.get('end_line')
            if not isinstance(path, str) or type(start) is not int or type(end) is not int or not 1 <= start <= end:
                raise ValueError('invalid_finding')
            key = (' '.join(claim.split()), path, start, end)
            if key not in index:
                index[key] = len(merged)
                merged.append({'claim': claim, 'doc': {'file': path, 'start_line': start, 'end_line': end}, 'evidence': []})
            merged[index[key]]['evidence'].append({'origin': origin, 'finding': finding})
    return merged
