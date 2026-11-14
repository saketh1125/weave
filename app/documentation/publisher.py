"""GitHub publication using immutable Git objects and a dedicated branch."""
import base64
import hashlib
import re
from urllib.parse import urlsplit, quote
from pathlib import PurePosixPath
import httpx
from app.config import settings

class PublicationError(Exception):
    pass


def git_blob(content):
    raw = content.encode('utf-8')
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def repo_name(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.netloc.lower() != 'github.com' or p.query or p.fragment or not re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?', p.path):
        raise PublicationError('Only GitHub HTTPS repositories are supported')
    owner, name = p.path.strip('/').split('/')
    if name.endswith('.git'): name = name[:-4]
    if owner in ('.','..') or name in ('.','..'): raise PublicationError('Invalid repository')
    return owner, name


async def publish(repo, draft, save_commit):
    if not settings.sentra_gh_token: raise PublicationError('GitHub credentials are not configured')
    owner, name = repo_name(repo.remote_url)
    root = f'https://api.github.com/repos/{owner}/{name}'
    headers = {'Authorization': f'Bearer {settings.sentra_gh_token}', 'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28'}
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=False) as client:
        async def req(method, path, *, payload=None, params=None, missing=False):
            try:
                response = await client.request(method, root + path, json=payload, params=params)
                if missing and response.status_code == 404: return None
                if response.status_code >= 400: raise PublicationError('GitHub request failed')
                return response.json()
            except (httpx.HTTPError, ValueError):
                raise PublicationError('GitHub request failed') from None
        try:
            branch = draft.branch
            existing = await req('GET', '/git/ref/heads/' + quote(branch, safe=''), missing=True)
            if existing:
                if not draft.publication_commit or existing['object']['sha'] != draft.publication_commit:
                    raise PublicationError('Publication branch does not match the approved draft')
                pulls = await req('GET', '/pulls', params={'state':'all','head':owner+':'+branch,'base':repo.branch,'per_page':100})
                if len(pulls) > 1: raise PublicationError('Multiple publication pull requests found')
                if pulls:
                    if pulls[0]['head']['sha'] != draft.publication_commit: raise PublicationError('Pull request head changed')
                    return pulls[0]['html_url']
            head = await req('GET', '/git/ref/heads/' + quote(repo.branch, safe=''))
            if head['object']['sha'] != draft.base_commit: raise PublicationError('Repository head changed; create a new review')
            if not existing:
                base = await req('GET', '/git/commits/' + draft.base_commit)
                tree_sha = base['tree']['sha']
                tree = await req('GET', '/git/trees/' + tree_sha, params={'recursive':'1'})
                if tree.get('truncated'): raise PublicationError('Repository tree is incomplete')
                entries = {item['path']:item for item in tree['tree']}
                changes = []
                for f in draft.files:
                    path = f['path']
                    if (not isinstance(path, str) or not path.lower().endswith('.md') or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or '\\' in path or hashlib.sha256(f['original'].encode()).hexdigest() != f['old_hash'] or hashlib.sha256(f['content'].encode()).hexdigest() != f['new_hash']):
                        raise PublicationError('Invalid approved documentation contents')
                    item = entries.get(f['path'])
                    if not item or item.get('type') != 'blob' or item.get('mode') not in ('100644','100755') or item.get('sha') != git_blob(f['original']):
                        raise PublicationError('Documentation no longer matches the indexed commit')
                    blob = await req('POST', '/git/blobs', payload={'content':base64.b64encode(f['content'].encode()).decode(), 'encoding':'base64'})
                    changes.append({'path':f['path'],'mode':item['mode'],'type':'blob','sha':blob['sha']})
                new_tree = await req('POST', '/git/trees', payload={'base_tree':tree_sha, 'tree':changes})
                commit = await req('POST', '/git/commits', payload={'message':'Update documentation from review', 'tree':new_tree['sha'], 'parents':[draft.base_commit]})
                draft.publication_commit = commit['sha']
                await save_commit()
                # Check again immediately before branch creation; no default-branch write occurs.
                head = await req('GET', '/git/ref/heads/' + quote(repo.branch, safe=''))
                if head['object']['sha'] != draft.base_commit: raise PublicationError('Repository head changed; create a new review')
                await req('POST', '/git/refs', payload={'ref':'refs/heads/'+branch,'sha':draft.publication_commit})
            pulls = await req('GET', '/pulls', params={'state':'all','head':owner+':'+branch,'base':repo.branch,'per_page':100})
            if len(pulls) > 1: raise PublicationError('Multiple publication pull requests found')
            if pulls:
                pr = pulls[0]
                if pr['head']['sha'] != draft.publication_commit: raise PublicationError('Pull request head changed')
            else:
                pr = await req('POST', '/pulls', payload={'title':'Update documentation from review','body':'Documentation changes generated from a reviewed repository snapshot. Please review the diff.','head':branch,'base':repo.branch})
            return pr['html_url']
        except (KeyError, TypeError, IndexError):
            raise PublicationError('Invalid GitHub response') from None
