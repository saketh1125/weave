from types import SimpleNamespace
import pytest
from app.documentation.drafts import make_draft, assessment_id, digest
from app.documentation.publisher import git_blob, repo_name, PublicationError


def fixture():
    from app.documentation.service import assessment_rows
    a = assessment_rows([{'verdict':'conflicting', 'code_citations':[{'path':'app.py','start_line':1,'end_line':1}], 'documentation_citation':{'path':'README.md','start_line':2,'end_line':2}, 'claim':'Old', 'suggested_update':'Change the claim', 'rationale':'Source differs'}])[0]
    task = SimpleNamespace(path='README.md', start_line=1, end_line=3, assessments=[a])
    return a, task


def test_draft_preserves_unrelated_text_and_hashes():
    a, t = fixture()
    files, diff = make_draft({'README.md':'# title\nOld\nfooter\n'}, [t], [{'assessment_id':assessment_id(a), 'old':'Old', 'new':'New'}])
    assert files[0]['content'] == '# title\nNew\nfooter\n'
    assert '-Old' in diff and '+New' in diff
    assert digest(files) != digest([])


@pytest.mark.parametrize('old,new', [('title','New'), ('missing','New'), ('Old','Old')])
def test_reject_unreviewed_edits(old, new):
    a, t = fixture()
    with pytest.raises(ValueError):
        make_draft({'README.md':'# title\nOld\nfooter\n'}, [t], [{'assessment_id':assessment_id(a), 'old':old, 'new':new}])


def test_git_blob_and_remote_validation():
    assert git_blob('test\n') == '9daeafb9864cf43055ae93beb0afd6c7d144bfa4'
    assert repo_name('https://github.com/owner/repo.git') == ('owner','repo')
    with pytest.raises(PublicationError): repo_name('https://github.com.evil/owner/repo')

@pytest.mark.asyncio
async def test_recover_existing_branch_and_pr(monkeypatch):
    from app.documentation import publisher
    calls = []
    class Response:
        status_code = 200
        def __init__(self, data): self.data = data
        def json(self): return self.data
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, method, url, **kwargs):
            calls.append((method,url))
            if url.endswith('/git/ref/heads/main'): return Response({'object': {'sha':'base'}})
            if '/git/ref/heads/sentra' in url: return Response({'object': {'sha':'commit'}})
            if url.endswith('/pulls'): return Response([{'head':{'sha':'commit'},'html_url':'https://github.com/o/r/pull/1'}])
            raise AssertionError(url)
    monkeypatch.setattr(publisher.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(publisher.settings, 'sentra_gh_token', 'test')
    repo = SimpleNamespace(remote_url='https://github.com/o/r', branch='main')
    draft = SimpleNamespace(base_commit='base', branch='sentra/docs-x', publication_commit='commit')
    async def save(): raise AssertionError('Existing commit must be reused')
    assert await publisher.publish(repo, draft, save) == 'https://github.com/o/r/pull/1'
    assert all(method == 'GET' for method, _ in calls)

def test_multiline_claim_preserves_exact_span():
    a, t = fixture()
    a['claim'] = 'Old text'
    files, _ = make_draft({'README.md':'# title\nOld\ntext\nfooter\n'}, [SimpleNamespace(path='README.md', start_line=1, end_line=4, assessments=[{**a, 'doc':{'file':'README.md','start_line':2,'end_line':3}}])], [{'assessment_id':a['id'], 'old':'Old\ntext', 'new':'New text'}])
    assert files[0]['content'] == '# title\nNew text\nfooter\n'



def test_draft_cache_provenance_tracks_actionable_assessments():
    from app.documentation.drafts import assessment_signature
    a, task = fixture()
    signature = assessment_signature([task])
    files, _ = make_draft({'README.md': '# title\nOld\nfooter\n'}, [task], [{'assessment_id': a['id'], 'old': 'Old', 'new': 'New'}])
    assert files[0]['assessment_snapshot'] == signature
    task.assessments.append({**a, 'id': 'another-reviewed-claim'})
    assert assessment_signature([task]) != signature


def test_static_outdated_draft_is_exact_and_does_not_mutate_tasks():
    import copy
    from app.documentation.drafts import review_tasks
    from app.documentation.hybrid import review_fingerprint
    snapshot = {'README.md': 'Old\nAligned\nUnknown\n', 'app.py': 'new = True\nother = True\n'}
    settings = {'workflow': 'file_agents_v1'}
    report = {'fingerprint': review_fingerprint(snapshot, ['README.md'], 'abc', settings), 'items': [
        {'id': 'a', 'status': 'outdated', 'claim': 'Old', 'doc': {'file': 'README.md', 'start_line': 1, 'end_line': 1}, 'code': {'file': 'app.py', 'start_line': 1, 'end_line': 1}, 'message': 'Source differs.'},
        {'id': 'b', 'status': 'outdated', 'claim': 'Old', 'doc': {'file': 'README.md', 'start_line': 1, 'end_line': 1}, 'code': {'file': 'app.py', 'start_line': 2, 'end_line': 2}, 'message': 'Another source.'},
        {'id': 'c', 'status': 'aligned', 'claim': 'Aligned', 'doc': {'file': 'README.md', 'start_line': 2, 'end_line': 2}, 'code': {'file': 'app.py', 'start_line': 1, 'end_line': 1}, 'message': 'Aligned.'},
        {'id': 'd', 'status': 'unverified', 'claim': 'Unknown', 'doc': {'file': 'README.md', 'start_line': 3, 'end_line': 3}, 'code': {'file': 'app.py', 'start_line': 1, 'end_line': 1}, 'message': 'Unknown.'}]}
    run = SimpleNamespace(snapshot=snapshot, commit_sha='abc', manifest={'static_review': report, 'model_settings': settings})
    task = SimpleNamespace(path='README.md', start_line=1, end_line=3, assessments=[])
    before = copy.deepcopy(report)
    tasks = review_tasks(run, [task])
    assert task.assessments == [] and report == before
    assert len(tasks[0].assessments) == 1
    candidate = tasks[0].assessments[0]
    assert candidate['source'] == 'static' and candidate['static_item_ids'] == ['a', 'b'] and len(candidate['code_citations']) == 2
    files, _ = make_draft(snapshot, tasks, [{'assessment_id': candidate['id'], 'old': 'Old', 'new': 'New'}])
    assert files[0]['content'] == 'New\nAligned\nUnknown\n'
    run.snapshot = {**snapshot, 'app.py': 'changed\n'}
    with pytest.raises(ValueError, match='stale'):
        review_tasks(run, [task])


def test_static_conflict_not_duplicated_when_ai_already_confirms():
    from app.documentation.drafts import review_tasks
    from app.documentation.hybrid import review_fingerprint
    assessment = {'id': 'ai', 'verdict': 'conflicting', 'claim': 'Old', 'doc': {'file': 'README.md', 'start_line': 2, 'end_line': 2}, 'code_citations': [{'file': 'app.py', 'start_line': 1, 'end_line': 1}], 'suggested_update': 'Update.'}
    task = SimpleNamespace(path='README.md', start_line=1, end_line=3, assessments=[assessment])
    snapshot = {'README.md': '# title\nOld\nfooter\n', 'app.py': 'changed\n'}
    settings = {}
    item = {'id': 'static', 'status': 'outdated', 'claim': 'Old', 'doc': assessment['doc'], 'code': {'file': 'app.py', 'start_line': 1, 'end_line': 1}, 'message': 'Changed.'}
    run = SimpleNamespace(snapshot=snapshot, commit_sha='abc', manifest={'model_settings': settings, 'static_review': {'fingerprint': review_fingerprint(snapshot, ['README.md'], 'abc', settings), 'items': [item]}})
    assert review_tasks(run, [task])[0].assessments == [assessment]
