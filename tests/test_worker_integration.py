import asyncio
import os
import sys
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("SENTRA_TEST_DATABASE_URL") or sys.platform != "linux",
        reason="Requires isolated PostgreSQL and a Linux worker",
    ),
]


def test_worker_executes_a_persisted_job_in_a_child_process(monkeypatch):
    from app import jobs, worker
    from app.models import Job, Repository

    async def run():
        url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "+asyncpg")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(worker, "engine", engine)
        monkeypatch.setattr(worker, "SessionLocal", factory)
        monkeypatch.setattr(jobs, "SessionLocal", factory)
        monkeypatch.setenv("DATABASE_URL", url)
        repo_id = uuid.uuid4()
        try:
            async with factory() as session:
                session.add(Repository(id=repo_id, name="worker-fixture", remote_url="https://github.com/example/fixture", status="ready"))
                await session.flush()
                job_id = await jobs.enqueue(session, repo_id, "embed")
                await session.commit()
            assert await worker.run_once() is True
            async with factory() as session:
                job = await session.scalar(select(Job).where(Job.id == job_id))
                assert job.status == "succeeded"
                assert job.attempts == 1 and job.lease_token is None
                assert job.result["embedded"] == 0
                assert job.result["chunks"] == 0
        finally:
            async with factory() as session:
                repo = await session.get(Repository, repo_id)
                if repo is not None:
                    await session.delete(repo)
                    await session.commit()
            await engine.dispose()
    asyncio.run(run())


def test_expired_lease_cannot_be_renewed_or_finished(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import jobs
    from app.models import Job, Repository

    async def run():
        url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "+asyncpg")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(jobs, "SessionLocal", factory)
        repo_id, job_id, token = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        try:
            async with factory() as session:
                session.add(Repository(id=repo_id, name="expired-lease", remote_url="https://github.com/example/fixture", status="ready"))
                await session.flush()
                session.add(Job(id=job_id, repo_id=repo_id, kind="embed", payload={}, status="running", attempts=1,
                                lease_token=token, lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
                await session.commit()
            with pytest.raises(RuntimeError, match="Job lease lost"):
                await jobs.heartbeat(job_id, token)
            await jobs.finish(job_id, token)
            async with factory() as session:
                job = await session.get(Job, job_id)
                assert job.status == "running" and job.lease_token == token
        finally:
            async with factory() as session:
                repo = await session.get(Repository, repo_id)
                if repo is not None:
                    await session.delete(repo)
                    await session.commit()
            await engine.dispose()
    asyncio.run(run())
