from __future__ import annotations

import asyncio
import re
import uuid
from collections import Counter
from itertools import groupby
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from app.db import SessionLocal
from app.alignment_coverage import alignment_coverage
from app.doc_alignment import SourceFile, check_alignment
from app.models import Chunk, DriftRun, File, Repository

router = APIRouter(prefix="/repos", tags=["drift"])


class DriftIn(BaseModel):
    behavioral: bool = False
    passage_offset: int = Field(default=0, ge=0)
    expected_commit: str | None = Field(default=None, max_length=64)


async def _behavioral_review(repo_id, rows, offset):
    from app.behavioral_alignment import PROMPT_VERSION, SCHEMA_VERSION, review_passages
    from app.retrieval import retrieve_candidates
    from app.model_text import chat as model_chat, configured, model_name

    if not configured("drift"):
        raise HTTPException(400, "Configure a model connection before requesting a behavioral review")
    passages = [row for row in rows if row.language == "markdown" and row.content.strip()]
    selected = passages[offset:offset + 1]
    items = []
    async def chat(prompt):
        answer, _, _ = await model_chat(
            workflow="drift",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=1200,
            temperature=0.1,
        )
        return answer

    for row in selected:
        try:
            candidates = await retrieve_candidates(repo_id, row.content[:1500], top_k=3, kinds=("code",))
        except Exception as exc:
            raise HTTPException(503, "Could not retrieve source evidence for behavioral review") from exc
        candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
        evidence = [
            SourceFile(candidate["file"], "\n" * (candidate["start_line"] - 1) + candidate["content"][:4000])
            for candidate in candidates[:3]
        ]
        passage = SourceFile(row.path, "\n" * (row.start_line - 1) + row.content)
        items.extend(await review_passages([passage], evidence, chat))
    return items, {
        "passages_total": len(passages),
        "passages_processed": len(selected),
        "next_offset": offset + len(selected) if offset + len(selected) < len(passages) else None,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "model": model_name("drift"),
    }


def _indexed_source(parts) -> str:
    """Remove Markdown chunk overlap while preserving original source lines."""
    source = bytearray()
    previous_end = 0
    next_line = 1
    for part in parts:
        if part.byte_end <= previous_end:
            continue
        gap = part.byte_start - previous_end
        if gap > 0:
            missing_lines = part.start_line - next_line
            if missing_lines > 0:
                source.extend(b"\n" * missing_lines)
            elif source:
                source.extend(b" ")
        source.extend(part.content.encode("utf-8")[max(0, -gap):])
        previous_end = part.byte_end
        next_line = part.end_line + part.content.endswith((
            "\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029",
        ))
    return source.decode("utf-8")


def _github_blob_base(remote_url: str, commit_sha: str | None) -> str | None:
    if not commit_sha or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit_sha):
        return None
    try:
        remote = urlsplit(remote_url)
        if remote.scheme != "https" or remote.hostname not in {"github.com", "www.github.com"}:
            return None
    except ValueError:
        return None
    path = remote.path.strip("/").removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", path) or path.endswith(("/.", "/..")):
        return None
    return f"https://github.com/{path}/blob/{commit_sha}/"


def _run_response(run: DriftRun) -> dict:
    return {"run_id": str(run.id), "commit_sha": run.commit_sha, **run.result}


@router.post("/{repo_id}/drift")
async def drift(repo_id: uuid.UUID, body: DriftIn | None = None):
    options = body or DriftIn()
    # A single statement reads the commit and both evidence types from one DB snapshot.
    sources = (
        select(File.path, File.language, File.repo_id, Chunk.content,
               Chunk.start_line, Chunk.end_line, Chunk.byte_start, Chunk.byte_end)
        .join(Chunk, and_(Chunk.file_id == File.id, Chunk.repo_id == repo_id))
        .where(File.repo_id == repo_id)
        .where(
            ((File.language == "markdown") & (Chunk.kind == "markdown"))
            | (File.language.in_(("python", "javascript", "typescript", "tsx")) & (Chunk.kind == "module"))
        )
        .subquery()
    )
    query = (
        select(Repository.status, Repository.commit_sha, Repository.remote_url, sources)
        .outerjoin(sources, sources.c.repo_id == Repository.id)
        .where(Repository.id == repo_id)
        .order_by(sources.c.path, sources.c.byte_start, sources.c.byte_end)
    )
    async with SessionLocal() as session:
        rows = (await session.execute(query)).all()
    if not rows:
        raise HTTPException(404, "Repository not found")
    snapshot = rows[0]
    if options.expected_commit is not None and options.expected_commit != snapshot.commit_sha:
        raise HTTPException(409, "The index changed since the previous batch; restart the check")
    if snapshot.status not in {"ready", "scanning", "scan_failed"}:
        raise HTTPException(409, "Wait for repository indexing to finish before checking documentation")

    markdown = []
    code = []
    for (path, language), parts in groupby(rows, key=lambda row: (row.path, row.language)):
        if path is not None:
            target = markdown if language == "markdown" else code
            target.append(SourceFile(path, _indexed_source(parts)))
    items = await asyncio.to_thread(check_alignment, markdown, code)
    behavioral_coverage = None
    if options.behavioral:
        extra, behavioral_coverage = await _behavioral_review(repo_id, rows, options.passage_offset)
        items.extend(extra)
        async with SessionLocal() as session:
            current = await session.get(Repository, repo_id)
            if current is None or current.commit_sha != snapshot.commit_sha or current.status not in {"ready", "scanning", "scan_failed"}:
                raise HTTPException(409, "Repository index changed during review; run the check again")

    base = _github_blob_base(snapshot.remote_url, snapshot.commit_sha)
    if base:
        for item in items:
            for citation in (item["doc"], item["code"]):
                if citation:
                    path = quote(citation["file"], safe="/")
                    citation["url"] = f'{base}{path}#L{citation["start_line"]}-L{citation["end_line"]}'

    counts = Counter(item["status"] for item in items)
    run = DriftRun(
        id=uuid.uuid4(),
        repo_id=repo_id,
        commit_sha=snapshot.commit_sha,
        result={
            "checked": counts["aligned"] + counts["outdated"],
            "aligned": counts["aligned"],
            "drifted": counts["outdated"],
            "unverified": counts["unverified"],
            "items": items,
            "coverage": alignment_coverage(items),
            "behavioral_coverage": behavioral_coverage,
        },
    )
    async with SessionLocal() as session:
        session.add(run)
        await session.commit()
    return _run_response(run)


@router.get("/{repo_id}/drift")
async def latest_drift(repo_id: uuid.UUID):
    async with SessionLocal() as session:
        if await session.get(Repository, repo_id) is None:
            raise HTTPException(404, "Repository not found")
        run = await session.scalar(
            select(DriftRun).where(DriftRun.repo_id == repo_id)
            .order_by(DriftRun.created_at.desc(), DriftRun.id.desc()).limit(1)
        )
    return _run_response(run) if run else None
