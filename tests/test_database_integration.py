"""Optional tests against an isolated PostgreSQL database with pgvector."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

DATABASE_URL = os.environ.get("SENTRA_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="SENTRA_TEST_DATABASE_URL is not configured"),
]


def _migration_config():
    from alembic.config import Config
    from alembic import command

    migration_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    migration_config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    return migration_config, command


def _async_engine():
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    url = make_url(DATABASE_URL)
    if url.drivername != "postgresql+asyncpg":
        url = url.set(drivername="postgresql+asyncpg")
    return create_async_engine(url, pool_pre_ping=True)


def test_alembic_full_upgrade_creates_latest_schema():
    from sqlalchemy import create_engine, inspect

    config, command = _migration_config()
    command.upgrade(config, "head")
    sync_engine = create_engine(DATABASE_URL)
    try:
        with sync_engine.connect() as connection:
            inspector = inspect(connection)
            assert {"repositories", "documents", "jobs", "findings"}.issubset(set(inspector.get_table_names()))
            document_columns = {column["name"] for column in inspector.get_columns("documents")}
            assert "generator_version" in document_columns
            indexes = {index["name"] for index in inspector.get_indexes("jobs")}
            assert "uq_jobs_active_repo" in indexes
    finally:
        sync_engine.dispose()


def test_testclient_readiness_and_authenticated_api():
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    previous = settings.sentra_access_token
    settings.sentra_access_token = "ci-integration-token-0123456789abcdef"
    try:
        with TestClient(app) as client:
            ready = client.get("/api/ready")
            assert ready.status_code == 200
            assert ready.json() == {"status": "ready", "db": "ok"}
            assert client.get("/api/openapi.json").status_code == 401
            login = client.post("/api/auth/token", json={"access_token": settings.sentra_access_token})
            assert login.status_code == 200
            token = login.json()["access_token"]
            assert client.get("/api/openapi.json", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    finally:
        settings.sentra_access_token = previous


def test_database_job_enqueue_is_idempotent_and_partial_unique_index_is_enforced():
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.jobs import enqueue
    from app.models import Job, Repository

    async def scenario():
        engine = _async_engine()
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        repo_id = uuid.uuid4()
        try:
            async with factory() as session:
                session.add(Repository(id=repo_id, name=f"ci-{repo_id}", remote_url="https://example.invalid/ci.git", status="ready"))
                await session.commit()
                first = await enqueue(session, repo_id, "scan", {"modules": ["m2", "m4"]})
                second = await enqueue(session, repo_id, "scan", {"modules": ["m2", "m4"]})
                assert first == second
                count = await session.scalar(select(func.count()).select_from(Job).where(Job.repo_id == repo_id))
                assert count == 1
                await session.commit()

                session.add(Job(repo_id=repo_id, kind="ingest", payload={}, status="queued", attempts=0))
                with pytest.raises(IntegrityError):
                    await session.commit()
                await session.rollback()
                count = await session.scalar(select(func.count()).select_from(Job).where(Job.repo_id == repo_id))
                assert count == 1

                await session.delete(await session.get(Repository, repo_id))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_generated_document_schema_and_version_metadata_round_trip():
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models import Document
    from app.routers.docs import GeneratedDoc, _cached_doc, _generator_version

    async def scenario():
        engine = _async_engine()
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        source = "x" * 100
        payload = {"summary": "Returns a parsed value", "key_behaviors": ["Validates the input"],
                   "complexity_note": "Linear in input length", "context_complete": True,
                   "coverage": {"source_characters": len(source), "processed_characters": len(source),
                                "sections_processed": 1}}
        parsed = GeneratedDoc.model_validate({key: value for key, value in payload.items() if key != "context_complete"})
        assert parsed.summary == payload["summary"]
        try:
            async with factory() as session:
                document = Document(text=json.dumps(payload), source_hash="source-sha", generator_version=_generator_version())
                session.add(document)
                await session.commit()
                loaded = await session.scalar(select(Document).where(Document.id == document.id))
                assert loaded is not None
                assert loaded.generator_version == _generator_version()
                assert _cached_doc(loaded, SimpleNamespace(content_hash="source-sha", source_characters=len(source))) == payload
                await session.delete(loaded)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_job_claim_lock_contention_and_expired_lease_recovery():
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.jobs import claim, repo_lock_key
    from app.models import Job, Repository

    async def scenario():
        engine = _async_engine()
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        repo_id = uuid.uuid4()
        try:
            async with factory() as session:
                repo = Repository(id=repo_id, name=f"ci-claim-{repo_id}", remote_url="https://example.invalid/claim.git")
                session.add(repo)
                await session.flush()
                job = Job(repo_id=repo_id, kind="embed", payload={}, status="queued", attempts=0)
                session.add(job)
                await session.commit()
                job_id = job.id

            async with engine.begin() as holder:
                assert await holder.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": repo_lock_key(repo_id)})
                async with engine.begin() as contender:
                    async with factory() as session:
                        assert await claim(session, contender) is None

            async with engine.begin() as lock_connection:
                async with factory() as session:
                    claimed = await claim(session, lock_connection)
                    assert claimed.id == job_id
                    assert claimed.status == "running" and claimed.attempts == 1
                    await session.refresh(claimed)
                    first_token = claimed.lease_token
                    assert claimed.lease_expires_at is not None

            async with factory() as session:
                expired = await session.get(Job, job_id)
                expired.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await session.commit()

            async with engine.begin() as lock_connection:
                async with factory() as session:
                    recovered = await claim(session, lock_connection)
                    assert recovered.id == job_id
                    assert recovered.status == "running" and recovered.attempts == 2
                    await session.refresh(recovered)
                    assert recovered.lease_token != first_token
                    assert recovered.lease_expires_at > datetime.now(timezone.utc)

            async with factory() as session:
                await session.delete(await session.get(Repository, repo_id))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_docs_advisory_lock_stays_held_across_document_commits():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models import Document
    from app.routers.docs import _lock_key

    async def scenario():
        engine = _async_engine()
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        repo_id = uuid.uuid4()
        try:
            async with engine.begin() as holder:
                assert await holder.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _lock_key(repo_id)})
                async with factory() as session:
                    document = Document(text="{}", source_hash="lock-test", generator_version="test")
                    session.add(document)
                    await session.commit()
                    document_id = document.id
                async with engine.begin() as contender:
                    assert not await contender.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _lock_key(repo_id)})

            async with engine.begin() as released:
                assert await released.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _lock_key(repo_id)})
            async with factory() as session:
                await session.delete(await session.get(Document, document_id))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
