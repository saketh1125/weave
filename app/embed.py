from __future__ import annotations

import os
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("OMP_THREAD_LIMIT", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.config import settings
from app.models import Chunk, Embedding, File

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache" / "fastembed"
SLICE = settings.sentra_embed_batch_size


def _render(c: Chunk, path: str) -> str:
    label = f"{path}\n{c.qualified_name}" if c.kind == "markdown" else c.qualified_name
    return f"{label}\n{c.content}"


async def embed_repo(repo_id: uuid.UUID, model: str = DEFAULT_MODEL) -> dict:
    async with SessionLocal() as session:
        q = (
            select(Chunk, File.path)
            .join(File, File.id == Chunk.file_id)
            .where(Chunk.repo_id == repo_id, Chunk.kind != "module")
            .order_by(Chunk.start_line)
        )
        rows = (await session.execute(q)).all()
        if not rows:
            return {"repo_id": str(repo_id), "model": model, "chunks": 0, "embedded": 0, "message": "no chunks to embed (kind != module)"}
        existing = {
            item.chunk_id: (item.model, item.render_mode)
            for item in (await session.execute(
                select(Embedding.chunk_id, Embedding.model, Embedding.render_mode)
                .join(Chunk, Chunk.id == Embedding.chunk_id)
                .where(Chunk.repo_id == repo_id, Chunk.kind != "module")
            )).all()
        }
        to_embed = [
            (chunk, path) for chunk, path in rows
            if existing.get(chunk.id) != (model, "path_name_and_content" if chunk.kind == "markdown" else "name_and_content")
        ]
        if not to_embed:
            return {"repo_id": str(repo_id), "model": model, "chunks": len(rows), "embedded": 0, "skipped_existing": len(existing)}

    def _embed_sync():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        from fastembed import TextEmbedding
        te = TextEmbedding(model_name=model, cache_dir=str(CACHE_DIR))
        texts = [_render(chunk, path) for chunk, path in to_embed]
        vecs: list[list[float]] = []
        for i in range(0, len(texts), SLICE):
            batch = texts[i:i+SLICE]
            batch_vecs = list(te.passage_embed(batch))
            vecs.extend([list(v) for v in batch_vecs])
            logger.info("embed %s %d/%d", repo_id, min(i+SLICE, len(texts)), len(texts))
        return vecs

    vecs = await asyncio.to_thread(_embed_sync)

    async with SessionLocal() as session:
        for (chunk, _), vec in zip(to_embed, vecs, strict=True):
            render_mode = "path_name_and_content" if chunk.kind == "markdown" else "name_and_content"
            statement = insert(Embedding).values(
                chunk_id=chunk.id, embedding=vec, model=model, render_mode=render_mode,
            )
            await session.execute(statement.on_conflict_do_update(
                index_elements=[Embedding.chunk_id],
                set_={"embedding": statement.excluded.embedding, "model": model, "render_mode": render_mode},
            ))
        await session.commit()

    logger.info("embed %s done: %d vectors model=%s", repo_id, len(vecs), model)
    return {"repo_id": str(repo_id), "model": model, "chunks": len(rows), "embedded": len(vecs)}
