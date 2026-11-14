from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select, text

from app.doc_generation import (GeneratedDoc, SOURCE_LIMIT, SECTION_OVERLAP, MAX_SECTIONS,
                                plan_sections, generate_summary, validate_cached_doc, MAX_SOURCE_CHARACTERS,
                                DocProvider, ProviderStopped)
from app.db import SessionLocal, engine
from app.models import Chunk, Document, File, Repository
from app.model_text import chat, configured, model_name
from app.model_connections import fingerprint, resolve

router = APIRouter(prefix="/repos", tags=["docs"])
MAX_CALLS_PER_REQUEST = 12
PROMPT_VERSION = "2"
SCHEMA_VERSION = "2"


def _generator_version():
    connection = resolve("summary")
    return json.dumps({"model": connection.model, "connection": fingerprint(connection),
                       "prompt": PROMPT_VERSION, "schema": SCHEMA_VERSION,
                       "source_limit": SOURCE_LIMIT, "section_overlap": SECTION_OVERLAP,
                       "max_sections": MAX_SECTIONS, "max_calls": MAX_CALLS_PER_REQUEST,
                       "window_seconds": 30.0, "request_interval_seconds": 1.1}, sort_keys=True)


def _lock_key(repo_id: uuid.UUID) -> int:
    from app.jobs import repo_lock_key
    return repo_lock_key(repo_id)


class DocsIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    force: bool = False


def _cached_doc(document: Document, chunk: Chunk):
    if (document.source_hash != chunk.content_hash
            or document.generator_version != _generator_version()):
        return None
    return validate_cached_doc(document.text, chunk.source_characters)


@router.post("/{repo_id}/docs")
async def generate_docs(repo_id: uuid.UUID, body: DocsIn):
    async with SessionLocal() as s:
        repo = await s.get(Repository, repo_id)
        if not repo:
            raise HTTPException(404, "repo not found")
        if repo.status not in {"ready", "scanning", "scan_failed"}:
            raise HTTPException(409, "Wait for repository indexing before generating documentation")
    if not configured("summary"):
        return {"status": "disabled", "reason": "No model connection configured for documentation", "model": model_name("summary")}
    try:
        async with engine.begin() as lock_connection:
            acquired = await lock_connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _lock_key(repo_id)}
            )
            if not acquired:
                raise HTTPException(409, "Documentation generation is already running for this repository")
            return await _generate_locked(repo_id, body)
    except HTTPException:
        raise
    except Exception:
        return {"status": "error", "error": "Documentation generation failed", "model": model_name("summary")}


async def _generate_locked(repo_id: uuid.UUID, body: DocsIn):
    async with SessionLocal() as session:
        # Stream only cache metadata first. Source bodies are loaded after the
        # bounded batch is selected, even for large repositories.
        metadata_query = (
            select(Chunk.id, Chunk.content_hash, func.length(Chunk.content), Document.id, Document.text,
                   Document.source_hash, Document.generator_version)
            .join(File, Chunk.file_id == File.id)
            .outerjoin(Document, Document.chunk_id == Chunk.id)
            .where(Chunk.repo_id == repo_id, Chunk.kind.not_in(("markdown", "module")))
            .order_by(File.path, Chunk.start_line, Chunk.end_line, Chunk.id)
            .execution_options(yield_per=100)
        )
        pending_ids, cached_ids = [], []
        result = await session.stream(metadata_query)
        try:
            async for chunk_id, source_hash, source_length, document_id, doc_text, doc_hash, version in result:
                document = SimpleNamespace(text=doc_text, source_hash=doc_hash, generator_version=version)
                if body.force or document_id is None or _cached_doc(document, SimpleNamespace(content_hash=source_hash, source_characters=source_length)) is None:
                    pending_ids.append(chunk_id)
                    if len(pending_ids) == body.limit:
                        break
                elif len(cached_ids) < body.limit:
                    cached_ids.append(chunk_id)
        finally:
            await result.close()
        selected_ids = (pending_ids + cached_ids)[:body.limit]
        if not selected_ids:
            return {"status": "empty", "count": 0, "generated": 0}
        query = (
            select(Chunk.id, Chunk.content_hash, Chunk.qualified_name, Chunk.start_line, Chunk.end_line,
                   func.substr(Chunk.content, 1, MAX_SOURCE_CHARACTERS), func.length(Chunk.content),
                   File.language, Document)
            .join(File, Chunk.file_id == File.id)
            .outerjoin(Document, Document.chunk_id == Chunk.id)
            .where(Chunk.id.in_(selected_ids), Chunk.repo_id == repo_id)
        )
        rows = (await session.execute(query)).all()
        by_id = {row[0]: row for row in rows}
        chunks = [by_id[chunk_id] for chunk_id in selected_ids if chunk_id in by_id]
        if not chunks:
            return {"status": "empty", "count": 0, "generated": 0}
        out = []
        generated = 0
        async def summary_chat(**kwargs):
            return await chat(workflow="summary", **kwargs)
        provider = DocProvider(summary_chat, max_attempts=MAX_CALLS_PER_REQUEST)
        for chunk_id, source_hash, name, start_line, end_line, source, source_length, language, existing in chunks:
            chunk = SimpleNamespace(id=chunk_id, content_hash=source_hash, source_characters=source_length)
            cached = _cached_doc(existing, chunk) if existing and not body.force else None
            if cached is not None:
                out.append({"chunk_id": str(chunk.id), "doc": cached, "cached": True, "context_complete": cached["context_complete"]})
                continue
            plan = plan_sections(source, source_length)
            if provider.attempts + plan.required_calls > MAX_CALLS_PER_REQUEST:
                return {"status": "budget_exhausted", "reason": "Provider call budget reached; retry to continue", "count": len(out), "docs": out, "generated": generated, "model": model_name("summary")}
            try:
                doc = await generate_summary(
                    plan, language=language, qualified_name=name,
                    start_line=start_line, end_line=end_line,
                    model=model_name("summary"), provider=provider,
                )
            except (ValueError, TypeError, ValidationError):
                out.append({"chunk_id": str(chunk.id), "error": "Model returned invalid documentation"})
                continue
            except ProviderStopped as exc:
                reason = ("Provider quota or billing limit reached" if exc.hard_quota else
                          "Provider limit reached; retry to continue")
                result = {"status": exc.status, "reason": reason, "count": len(out), "docs": out, "generated": generated, "model": model_name("summary")}
                if not exc.hard_quota and exc.retry_after_seconds is not None:
                    result["retry_after_seconds"] = exc.retry_after_seconds
                return result
            except Exception as exc:
                # Compatibility for older clients lacking structured metadata; never retry these.
                if not hasattr(exc, "retryable") and any(token in str(exc).lower() for token in ("429", "quota", "rate")):
                    return {"status": "rate_limited", "reason": "Model provider rate limit reached", "count": len(out), "docs": out, "generated": generated, "model": model_name("summary")}
                out.append({"chunk_id": str(chunk.id), "error": "Documentation generation failed"})
                continue
            text = json.dumps(doc)
            if existing:
                existing.text = text
                existing.source_hash = chunk.content_hash
                existing.generator_version = _generator_version()
                existing.generated_at = func.now()
            else:
                session.add(Document(chunk_id=chunk.id, text=text, source_hash=chunk.content_hash, generator_version=_generator_version()))
            # Commit each successful unit before requesting the next one.
            await session.commit()
            generated += 1
            out.append({"chunk_id": str(chunk.id), "doc": doc, "cached": False, "context_complete": doc["context_complete"]})
        failures = sum("error" in item for item in out)
        status = "partial" if failures and failures < len(out) else "error" if failures else "ok"
        result = {"status": status, "model": model_name("summary"), "count": len(out), "docs": out, "generated": generated}
        if failures:
            result.update(reason="Documentation generation failed for some units", failed=failures)
        return result
