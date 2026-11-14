from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from threading import Lock
import uuid

from sqlalchemy import text

from app.db import SessionLocal

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache" / "fastembed"
_EMBED_LOCK = Lock()

def budget_candidates(candidates: list[dict], max_chars: int = 24000, per_source_chars: int = 6000) -> list[dict]:
    """Bound provider context and keep each citation attached to visible lines."""
    result = []
    remaining = max_chars
    for candidate in candidates:
        if remaining <= 0:
            break
        content = candidate["content"][:min(remaining, per_source_chars)]
        if not content:
            continue
        trimmed = len(content) < len(candidate["content"])
        if trimmed and "\n" in content:
            content = content.rsplit("\n", 1)[0]
        excerpt = {
            **candidate,
            "content": content,
            "end_line": min(candidate["end_line"], candidate["start_line"] + content.rstrip("\r\n").count("\n")),
            "excerpt_truncated": trimmed,
        }
        if "byte_start" in candidate:
            excerpt["byte_end"] = candidate["byte_start"] + len(content.encode("utf-8"))
        result.append(excerpt)
        remaining -= len(content)
    return result


_CANDIDATE_SQL = text("""
    SELECT c.kind, c.name, c.qualified_name, c.start_line, c.end_line,
           c.byte_start, c.byte_end, c.content,
           f.path as file_path,
           1 - (e.embedding <=> CAST(:qvec AS vector)) as score
    FROM embeddings e
    JOIN chunks c ON c.id = e.chunk_id
    JOIN files f ON f.id = c.file_id
    WHERE c.repo_id = :repo_id AND e.model = :model
      AND ((:kind = 'markdown' AND c.kind = 'markdown')
        OR (:kind = 'code' AND c.kind NOT IN ('markdown', 'module')))
      AND (NOT :only_symbols OR c.name = ANY(CAST(:symbols AS text[])))
    ORDER BY e.embedding <=> CAST(:qvec AS vector)
    LIMIT :lim
""")


@lru_cache(maxsize=1)
def _embedder():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=DEFAULT_MODEL, cache_dir=str(CACHE_DIR))


def _embed_question(question: str) -> list[float]:
    # Reuse one model and bound concurrent inference memory inside each API process.
    with _EMBED_LOCK:
        return list(next(iter(_embedder().query_embed([question]))))


async def retrieve_candidates(
    repo_id: uuid.UUID,
    question: str,
    top_k: int = 5,
    kinds: tuple[str, ...] = ("markdown", "code"),
    symbols: tuple[str, ...] = (),
) -> list[dict]:
    """Embed a question and retrieve ranked-by-vector raw code/doc candidates.

    `top_k` is the SQL limit for each independent kind pool. Exact symbol
    lookup is an additional code-only pool, matching Ask's existing behavior.
    """
    qvec = await asyncio.to_thread(_embed_question, question)
    vector_literal = "[" + ",".join(str(float(value)) for value in qvec) + "]"
    kinds = tuple(kind for kind in dict.fromkeys(kinds) if kind in {"markdown", "code"})
    symbol_names = list(dict.fromkeys(symbols))
    limit = max(1, min(40, int(top_k)))

    searches = [(kind, False) for kind in kinds]
    if "code" in kinds and symbol_names:
        searches.append(("code", True))

    rows = []
    async with SessionLocal() as session:
        for kind, only_symbols in searches:
            result = await session.execute(_CANDIDATE_SQL, {
                "qvec": vector_literal,
                "repo_id": str(repo_id),
                "model": DEFAULT_MODEL,
                "kind": kind,
                "lim": limit,
                "symbols": symbol_names,
                "only_symbols": only_symbols,
            })
            rows.extend(result.mappings().all())

    return [
        {
            "file": row["file_path"],
            "kind": row["kind"],
            "name": row["name"],
            "qualified_name": row["qualified_name"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "byte_start": row["byte_start"],
            "byte_end": row["byte_end"],
            "content": row["content"],
            "score": float(row["score"]) if row["score"] is not None else 0.0,
        }
        for row in rows
    ]
