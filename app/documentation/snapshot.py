"""Immutable, verified indexed content for a documentation review."""
import asyncio
import hashlib
from pathlib import PurePosixPath
from sqlalchemy import select
from app.models import File, Chunk, Repository
from .checkout_sources import collect
from app.config import settings


def reconstruct(file, chunks):
    if file.byte_len is not None and file.byte_len > settings.sentra_repo_max_file_bytes:
        return None
    if file.byte_len is None or not file.content_hash or file.byte_len < 0:
        return None
    data = bytearray(file.byte_len)
    seen = bytearray(file.byte_len)
    for c in chunks:
        raw = c.content.encode('utf-8')
        if c.byte_start < 0 or c.byte_end > len(data) or c.byte_end - c.byte_start != len(raw):
            return None
        if hashlib.sha256(raw).hexdigest() != c.content_hash:
            return None
        for i, b in enumerate(raw, c.byte_start):
            if seen[i] and data[i] != b:
                return None
            data[i], seen[i] = b, 1
    if not all(seen) or hashlib.sha256(data).hexdigest() != file.content_hash:
        return None
    try:
        return bytes(data).decode('utf-8')
    except UnicodeDecodeError:
        return None


async def capture(session, repo_id):
    files = (await session.execute(select(File).where(File.repo_id == repo_id).order_by(File.path))).scalars().all()
    chunks = (await session.execute(select(Chunk).where(Chunk.repo_id == repo_id).order_by(Chunk.byte_start))).scalars().all()
    grouped = {}
    for c in chunks:
        grouped.setdefault(c.file_id, []).append(c)
    snapshot, unreadable = {}, []
    remaining = settings.sentra_repo_max_total_bytes
    for f in files:
        if f.byte_len is None or f.byte_len < 0 or f.byte_len > remaining:
            unreadable.append(f.path)
            remaining = 0
            continue
        remaining -= f.byte_len
        path = PurePosixPath(f.path)
        if path.is_absolute() or '..' in path.parts or '\\' in f.path:
            unreadable.append(f.path)
            continue
        content = reconstruct(f, grouped.get(f.id, []))
        if content is None:
            unreadable.append(f.path)
        else:
            snapshot[f.path] = content
    docs = [f.path for f in files if f.path.lower().endswith('.md')]
    repo = await session.get(Repository, repo_id)
    supplemental, coverage = await asyncio.to_thread(collect, repo, {f.path for f in files}, remaining,
                                     max(0, settings.sentra_repo_max_files - len(files)),
                                     settings.sentra_repo_max_file_bytes)
    snapshot.update(supplemental)
    missing = coverage.get('missing_indexed_markdown', [])
    docs = sorted(set(docs + missing))
    unreadable = sorted(set(unreadable + missing))
    return snapshot, {'indexed_files': len(files), 'documentation_files': docs, 'unreadable_files': unreadable,
                      'source_coverage': coverage,
                      'scope': 'indexed files plus bounded tracked Go, Dart and build manifests at the indexed commit; supplemental source coverage: ' + coverage['status'] + '; other omitted files are not represented'}


class SnapshotTools:
    def __init__(self, snapshot, repo_id=None):
        self.snapshot = snapshot
        self.repo_id = repo_id

    async def list_files(self, query=None):
        return [p for p in sorted(self.snapshot) if not query or query.lower() in p.lower()][:500]

    async def read_source(self, path, start_line, end_line):
        if (not isinstance(path, str) or not path or path not in self.snapshot
                or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or '\\' in path
                or type(start_line) is not int or type(end_line) is not int
                or start_line < 1 or end_line < start_line or end_line - start_line >= 200):
            raise ValueError('Invalid source range or unavailable indexed path')
        lines = self.snapshot[path].splitlines(keepends=True)
        if not lines or start_line > len(lines):
            raise ValueError('Source range exceeds file')
        actual_end_line = min(end_line, len(lines))
        return {'path': path, 'start_line': start_line, 'end_line': actual_end_line, 'content': ''.join(lines[start_line-1:actual_end_line])}

    async def search_source(self, query, limit=20):
        if not isinstance(query, str) or not query or len(query) > 200 or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError('Invalid search')
        hits = []
        for path, content in sorted(self.snapshot.items()):
            if path.lower().endswith(('.md', '.markdown', '.mdx', '.rst')) or (path.lower().endswith('.txt') and PurePosixPath(path).name != 'requirements.txt'):
                continue
            for n, line in enumerate(content.splitlines(), 1):
                if query.lower() in line.lower():
                    hits.append({'path': path, 'line': n, 'content': line[:1000]})
                    if len(hits) >= limit:
                        return hits
        return hits if hits else {'matches': [], 'hint': 'Literal case-insensitive substring search found no exact matches. Try a short identifier, or use semantic_search for a conceptual question. No matches do not prove absence.'}

    async def semantic_search(self, query, limit=5):
        """Return index locations as discovery hints, never as source evidence."""
        if self.repo_id is None or not isinstance(query, str) or not 1 <= len(query) <= 200 or type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('Invalid semantic search')
        from app.retrieval import retrieve_candidates
        candidates = await retrieve_candidates(self.repo_id, query, top_k=limit, kinds=('code',))
        hits = []
        for candidate in candidates:
            path = candidate.get('file')
            start, end = candidate.get('start_line'), candidate.get('end_line')
            if not isinstance(path, str) or path not in self.snapshot or type(start) is not int or type(end) is not int:
                continue
            if start < 1 or end < start or end > len(self.snapshot[path].splitlines()):
                continue
            hits.append({'path': path, 'start_line': start, 'end_line': end})
        return hits[:limit]
