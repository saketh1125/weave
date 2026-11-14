import asyncio
import os
import sys
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("SENTRA_TEST_DATABASE_URL"), reason="No isolated test database"),
]


def test_model_or_render_changes_refresh_embeddings_without_duplicate_rows(monkeypatch):
    from app import embed
    from app.models import Chunk, Embedding, File, Repository

    calls = []
    class FakeEmbedding:
        def __init__(self, **kwargs):
            pass

        def passage_embed(self, texts):
            calls.append(texts)
            return [[0.1] * 384 for _ in texts]

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FakeEmbedding))

    async def run():
        url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "+asyncpg")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(embed, "SessionLocal", factory)
        repo_id, file_id, chunk_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        try:
            async with factory() as session:
                session.add(Repository(id=repo_id, name="embedding-fixture", remote_url="https://example.invalid/repo"))
                await session.flush()
                session.add(File(id=file_id, repo_id=repo_id, path="app.py", language="python"))
                await session.flush()
                session.add(Chunk(
                    id=chunk_id, repo_id=repo_id, file_id=file_id, kind="function",
                    name="f", qualified_name="f", start_line=1, end_line=1,
                    byte_start=0, byte_end=16, content="def f(): pass", content_hash="hash",
                ))
                await session.flush()
                session.add(Embedding(chunk_id=chunk_id, embedding=[0.0] * 384, model="older-model", render_mode="name_and_content"))
                await session.commit()
            assert (await embed.embed_repo(repo_id))["embedded"] == 1
            assert (await embed.embed_repo(repo_id))["embedded"] == 0
            async with factory() as session:
                row = await session.get(Embedding, chunk_id)
                assert row.model == embed.DEFAULT_MODEL
                row.render_mode = "older-render"
                await session.commit()
            assert (await embed.embed_repo(repo_id))["embedded"] == 1
            assert len(calls) == 2
        finally:
            async with factory() as session:
                repo = await session.get(Repository, repo_id)
                if repo is not None:
                    await session.delete(repo)
                    await session.commit()
            await engine.dispose()
    asyncio.run(run())
