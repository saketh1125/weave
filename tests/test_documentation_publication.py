"""Publication recovery and draft API checks; no real external requests."""
import json
import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest

@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get('SENTRA_TEST_DATABASE_URL'), reason='No isolated test database')
def test_draft_api_reuses_and_rejects_concurrent_and_unapproved(monkeypatch):
    import psycopg
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import settings
    from app.auth import issue_session_token
    from app.documentation import quota
    from app.documentation.service import assessment_rows
    entered, release = Event(), Event()
    a = assessment_rows([{'verdict':'conflicting', 'code_citations':[{'path':'app.py','start_line':1,'end_line':1}], 'documentation_citation':{'path':'README.md','start_line':1,'end_line':1}, 'claim':'Old', 'suggested_update':'Use new', 'rationale':'Changed'}])[0]
    calls = []
    async def complete(*args, **kwargs):
        calls.append(1); entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return {'tool_calls':[{'function':{'name':'propose_edits','arguments':json.dumps({'edits':[{'assessment_id':a['id'],'old':'Old','new':'New'}]})}}]}, {}
    monkeypatch.setattr(quota, 'request', complete)
    monkeypatch.setattr(settings, 'sentra_access_token', 'integration-docs-token-0123456789abcdef')
    repo, review, task = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    dburl = os.environ['SENTRA_TEST_DATABASE_URL'].replace('+psycopg', '').replace('+asyncpg','')
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer ' + issue_session_token(settings.sentra_access_token)
        with psycopg.connect(dburl) as db:
            db.execute("INSERT INTO repositories(id,name,remote_url,status,commit_sha) VALUES (%s,'draft-test','https://github.com/o/r','ready','abc')", (repo,))
            db.execute("INSERT INTO documentation_reviews(id,repo_id,commit_sha,status,model,snapshot,manifest) VALUES (%s,%s,'abc','completed','mock',%s,%s)", (review,repo,psycopg.types.json.Jsonb({'README.md':'Old\n','app.py':'New\n'}),psycopg.types.json.Jsonb({'documentation_files':['README.md'],'unreadable_files':[],'scope':'test'})))
            db.execute("INSERT INTO documentation_review_tasks(id,run_id,path,start_line,end_line,content,status,state,assessments) VALUES (%s,%s,'README.md',1,1,'Old','completed',%s,%s)", (task,review,psycopg.types.json.Jsonb({}),psycopg.types.json.Jsonb([a])))
        try:
            url=f'/api/repos/{repo}/documentation/reviews/{review}/drafts'
            with ThreadPoolExecutor(max_workers=1) as pool:
                first=pool.submit(client.post,url)
                assert entered.wait(10)
                assert client.post(url).status_code == 409
                release.set(); result=first.result(timeout=15)
            assert result.status_code == 200, result.text
            draft=result.json()
            assert draft['files'][0]['content'] == 'New\n'
            assert client.post(url).json()['id'] == draft['id']
            assert len(calls) == 1
            with psycopg.connect(dburl) as db:
                db.execute("UPDATE documentation_reviews SET status='paused' WHERE id=%s", (review,))
            assert client.post(url).status_code == 409
            assert client.post(url, json={'allow_partial': True}).json()['id'] == draft['id']
            assert client.get(url+'/latest').json()['id'] == draft['id']
            bad=client.post(f'/api/repos/{repo}/documentation/drafts/{draft["id"]}/publish', json={'draft_hash':'0'*64})
            assert bad.status_code == 409
        finally:
            release.set()
            with psycopg.connect(dburl) as db: db.execute('DELETE FROM repositories WHERE id=%s', (repo,))

@pytest.mark.asyncio
@pytest.mark.parametrize('lost', ['branch', 'pull'])
async def test_ambiguous_publication_recovers_without_duplicate(monkeypatch, lost):
    import hashlib
    import httpx
    from types import SimpleNamespace
    from app.documentation import publisher
    state={'branch':None,'pull':None,'fail':True,'posts':[],'base':'base'}
    original='Old\n'; content='New\n'
    class Response:
        def __init__(self, data, status=200): self.data=data; self.status_code=status
        def json(self): return self.data
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, method, url, **kwargs):
            if method == 'POST': state['posts'].append(url)
            if url.endswith('/git/ref/heads/main'): return Response({'object':{'sha':state['base']}})
            if '/git/ref/heads/sentra' in url: return Response({'object':{'sha':state['branch']}}) if state['branch'] else Response({},404)
            if url.endswith('/git/commits/base'): return Response({'tree':{'sha':'tree'}})
            if url.endswith('/git/trees/tree'): return Response({'tree':[{'path':'README.md','mode':'100644','type':'blob','sha':publisher.git_blob(original)}]})
            if url.endswith('/git/blobs'): return Response({'sha':'blob'})
            if url.endswith('/git/trees'): return Response({'sha':'newtree'})
            if url.endswith('/git/commits'): return Response({'sha':'commit'})
            if url.endswith('/git/refs'):
                state['branch']='commit'
                if lost == 'branch' and state['fail']:
                    state['fail']=False; raise httpx.ReadTimeout('ambiguous')
                return Response({})
            if url.endswith('/pulls') and method == 'GET': return Response([state['pull']] if state['pull'] else [])
            if url.endswith('/pulls') and method == 'POST':
                state['pull']={'head':{'sha':'commit'},'html_url':'https://github.com/o/r/pull/1'}
                if lost == 'pull' and state['fail']:
                    state['fail']=False; raise httpx.ReadTimeout('ambiguous')
                return Response(state['pull'])
            raise AssertionError((method,url))
    monkeypatch.setattr(publisher.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(publisher.settings, 'sentra_gh_token', 'test')
    f={'path':'README.md','original':original,'content':content,'old_hash':hashlib.sha256(original.encode()).hexdigest(),'new_hash':hashlib.sha256(content.encode()).hexdigest()}
    draft=SimpleNamespace(base_commit='base',branch='sentra/docs-x',publication_commit=None,files=[f])
    repo=SimpleNamespace(remote_url='https://github.com/o/r',branch='main')
    persisted=[]
    async def save(): persisted.append(draft.publication_commit)
    with pytest.raises(publisher.PublicationError): await publisher.publish(repo,draft,save)
    assert persisted == ['commit']
    if lost == 'pull': state['base'] = 'advanced'
    assert await publisher.publish(repo,draft,save) == 'https://github.com/o/r/pull/1'
    assert len([x for x in state['posts'] if x.endswith('/git/refs')]) == 1
    assert len([x for x in state['posts'] if x.endswith('/pulls')]) == 1
