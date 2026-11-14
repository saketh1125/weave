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
