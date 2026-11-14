from app.documentation.hybrid import merge_findings, static_review

SNAPSHOT = {'docs.md': 'GET /ok returns 200\nGET /missing returns 200\n',
            'app.py': 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/ok")\ndef ok():\n    return {}\n'}


def test_static_instances_and_fingerprint():
    report = static_review(SNAPSHOT, ['docs.md'], 'commit', {})
    assert report['counts'] == {'aligned': 1, 'outdated': 0, 'unverified': 1}
    assert len({item['id'] for item in report['items']}) == 2
    changed = static_review({**SNAPSHOT, 'app.py': SNAPSHOT['app.py'] + '# changed\n'}, ['docs.md'], 'commit', {})
    assert changed['fingerprint'] != report['fingerprint'] and changed['items'][0]['id'] != report['items'][0]['id']
    assert static_review(SNAPSHOT, ['docs.md'], 'new-commit', {})['fingerprint'] != report['fingerprint']
    assert static_review({**SNAPSHOT, 'docs.md': SNAPSHOT['docs.md'] + 'changed'}, ['docs.md'], 'commit', {})['fingerprint'] != report['fingerprint']


def test_merge_retains_distinct_instances_and_duplicate_evidence():
    report = static_review(SNAPSHOT, ['docs.md'], 'commit', {})
    first, second = report['items']
    ai = {'claim': first['claim'], 'doc': first['doc'], 'verdict': 'supported'}
    merged = merge_findings(report['items'], [ai])
    assert len(merged) == 2 and [e['origin'] for e in merged[0]['evidence']] == ['static', 'ai']
    assert merged[1]['evidence'][0]['finding']['status'] == 'unverified'


def test_labeled_fixture_measures_static_coverage_without_claiming_model_accuracy():
    # Labels describe the fixture's known claims, not model accuracy.
    report = static_review(SNAPSHOT, ['docs.md'], 'commit', {})
    labels = {('docs.md', 1): 'aligned', ('docs.md', 2): 'unverified'}
    observed = {(item['doc']['file'], item['doc']['start_line']): item['status'] for item in report['items']}
    static_correct = sum(observed.get(location) == label for location, label in labels.items())
    assert (static_correct, len(labels), report['counts']['unverified']) == (2, 2, 1)
    assert len(merge_findings(report['items'], [])) == 2  # Hybrid retains both without invented AI results.


def test_same_line_static_instances_are_not_lost_in_merge():
    source = {'docs.md': 'GET /a returns 200 and GET /b returns 200\n', 'app.py': SNAPSHOT['app.py']}
    report = static_review(source, ['docs.md'], 'commit', {})
    assert len(report['items']) == 2 and len({item['id'] for item in report['items']}) == 2
    merged = merge_findings(report['items'], [])
    assert sum(len(group['evidence']) for group in merged) == 2
