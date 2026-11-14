import hashlib
from types import SimpleNamespace
import pytest
from app.documentation.snapshot import reconstruct, SnapshotTools


def chunk(start, raw):
    return SimpleNamespace(byte_start=start, byte_end=start+len(raw), content=raw.decode(), content_hash=hashlib.sha256(raw).hexdigest())


def file(raw):
    return SimpleNamespace(byte_len=len(raw), content_hash=hashlib.sha256(raw).hexdigest())


def test_reconstruction_requires_exact_complete_bytes_and_hash():
    raw = b'first\nsecond\n'
    assert reconstruct(file(raw), [chunk(0, raw[:8]), chunk(6, raw[6:])]) == raw.decode()
    assert reconstruct(file(raw), [chunk(6, raw[6:])]) is None
    assert reconstruct(file(raw), [chunk(0, b'first\n'), chunk(6, b'wrong!\n')]) is None


@pytest.mark.asyncio
async def test_snapshot_tools_bound_paths_and_ranges():
    tools = SnapshotTools({'src/a.py': 'one\ntwo\n'})
    assert await tools.read_source('src/a.py', 2, 2) == {'path': 'src/a.py', 'start_line': 2, 'end_line': 2, 'content': 'two\n'}
    with pytest.raises(ValueError):
        await tools.read_source('../a.py', 1, 1)
    with pytest.raises(ValueError):
        await tools.read_source('src/a.py', 1, 201)


@pytest.mark.asyncio
async def test_read_source_clamps_valid_ranges_to_eof():
    tools = SnapshotTools({'src/cache.py': 'one\ntwo\n'})
    assert await tools.read_source('src/cache.py', 1, 20) == {
        'path': 'src/cache.py', 'start_line': 1, 'end_line': 2, 'content': 'one\ntwo\n'
    }
    with pytest.raises(ValueError):
        await tools.read_source('src/cache.py', 3, 20)
    with pytest.raises(ValueError):
        await tools.read_source('src/cache.py', True, 2)
    with pytest.raises(ValueError):
        await tools.read_source('src/cache.py', 1, 201)
    with pytest.raises(ValueError):
        await SnapshotTools({'src/empty.py': ''}).read_source('src/empty.py', 1, 1)


@pytest.mark.asyncio
async def test_semantic_search_only_returns_snapshot_locations(monkeypatch):
    import uuid
    from app import retrieval
    repo_id = uuid.uuid4()
    calls = []
    async def candidates(repo, query, top_k, kinds):
        calls.append((repo, query, top_k, kinds))
        return [
            {'file': 'src/a.py', 'start_line': 1, 'end_line': 2, 'content': 'untrusted index text'},
            {'file': 'missing.py', 'start_line': 1, 'end_line': 1},
            {'file': 'src/a.py', 'start_line': 1, 'end_line': 3},
            {'file': '../a.py', 'start_line': 1, 'end_line': 1},
        ]
    monkeypatch.setattr(retrieval, 'retrieve_candidates', candidates)
    tools = SnapshotTools({'src/a.py': 'one\ntwo\n'}, repo_id)
    assert await tools.semantic_search('where is the port?', 5) == [{'path': 'src/a.py', 'start_line': 1, 'end_line': 2}]
    assert calls == [(repo_id, 'where is the port?', 5, ('code',))]
    with pytest.raises(ValueError):
        await tools.semantic_search('test', 11)


@pytest.mark.asyncio
async def test_literal_source_search_excludes_documentation_echoes():
    tools = SnapshotTools({'README.md': 'CACHE_TTL = 60', 'AGENTS.md': 'CACHE_TTL is 60', 'cache.py': 'CACHE_TTL = 90'})
    hits = await tools.search_source('CACHE_TTL', limit=1)
    assert hits == [{'path': 'cache.py', 'line': 1, 'content': 'CACHE_TTL = 90'}]


@pytest.mark.asyncio
async def test_literal_source_search_retains_build_manifest_evidence_and_guides_empty_queries():
    tools = SnapshotTools({'requirements.txt': 'FastAPI==0.115', 'notes.txt': 'FastAPI is planned'})
    assert (await tools.search_source('FastAPI'))[0]['path'] == 'requirements.txt'
    assert 'Literal' in (await tools.search_source('conceptual multi term query'))['hint']
