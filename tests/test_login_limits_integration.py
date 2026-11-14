import asyncio
import hashlib
import os
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("SENTRA_TEST_DATABASE_URL"), reason="No isolated test database"),
]


def test_login_limit_is_shared_across_concurrent_database_connections(monkeypatch):
    from app import login_limits
    from app.models import LoginBucket

    async def run():
        url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "+asyncpg")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine)
        monkeypatch.setattr(login_limits, "SessionLocal", factory)
        host = "fixture-" + str(uuid.uuid4())
        try:
            results = await asyncio.gather(*(login_limits.check_login_limit(host) for _ in range(12)))
            assert sum(allowed for allowed, _ in results) == 10
            assert all(1 <= retry <= 60 for _, retry in results)
        finally:
            async with factory() as session:
                await session.execute(delete(LoginBucket).where(LoginBucket.key == hashlib.sha256(host.encode()).hexdigest()))
                await session.commit()
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["reset", "expire"])
def test_waiting_statement_uses_time_after_the_row_lock(monkeypatch, mode):
    """A waiting statement must not use a timestamp older than the window."""
    from app import login_limits

    async def run():
        url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "+asyncpg")
        name = "limiter-" + uuid.uuid4().hex
        engine = create_async_engine(url)
        worker_engine = create_async_engine(url, connect_args={"server_settings": {"application_name": name}})
        factory = async_sessionmaker(engine)
        monkeypatch.setattr(login_limits, "SessionLocal", async_sessionmaker(worker_engine))
        monkeypatch.setattr(login_limits.secrets, "randbelow", lambda _: 1)
        host = "fixture-" + str(uuid.uuid4())
        key = hashlib.sha256(host.encode()).hexdigest()
        task = None
        try:
            async with factory() as holder:
                await holder.execute(text("INSERT INTO login_buckets (key, window_start, attempts) VALUES (:key, clock_timestamp(), 1)"), {"key": key})
                await holder.commit()
                await holder.execute(text("SELECT key FROM login_buckets WHERE key = :key FOR UPDATE"), {"key": key})
                task = asyncio.create_task(login_limits.check_login_limit(host))

                async def wait_for_lock():
                    while True:
                        async with factory() as observer:
                            started = await observer.scalar(text("SELECT query_start FROM pg_stat_activity WHERE application_name = :name AND wait_event_type = 'Lock'"), {"name": name})
                        if started is not None:
                            return started
                        await asyncio.sleep(0.01)

                started = await asyncio.wait_for(wait_for_lock(), timeout=10)
                # This window starts after the blocked statement began, just as
                # when another request wins the race to insert or reset it.
                if mode == "reset":
                    await holder.execute(text("UPDATE login_buckets SET window_start = clock_timestamp(), attempts = 1 WHERE key = :key"), {"key": key})
                else:
                    # The window expires strictly between query start and this
                    # update, without relying on a sleep or a minute boundary.
                    await holder.execute(text("""UPDATE login_buckets
                        SET window_start = CAST(:started AS timestamptz) - interval '60 seconds'
                            + (clock_timestamp() - CAST(:started AS timestamptz)) / 2,
                            attempts = 10 WHERE key = :key"""), {"key": key, "started": started})
                await holder.commit()
            allowed, retry = await asyncio.wait_for(task, timeout=10)
            assert allowed
            assert 1 <= retry <= 60
            async with factory() as session:
                attempts = await session.scalar(text("SELECT attempts FROM login_buckets WHERE key = :key"), {"key": key})
            assert attempts == (2 if mode == "reset" else 1)
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            async with factory() as session:
                await session.execute(text("DELETE FROM login_buckets WHERE key = :key"), {"key": key})
                await session.commit()
            await worker_engine.dispose()
            await engine.dispose()
    asyncio.run(run())


def test_existing_and_expired_window_boundaries(monkeypatch):
    from app import login_limits

    async def run():
        url = os.environ["SENTRA_TEST_DATABASE_URL"].replace("+psycopg", "+asyncpg")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine)
        monkeypatch.setattr(login_limits, "SessionLocal", factory)
        monkeypatch.setattr(login_limits.secrets, "randbelow", lambda _: 1)
        host = "fixture-" + str(uuid.uuid4())
        key = hashlib.sha256(host.encode()).hexdigest()
        try:
            async with factory() as session:
                await session.execute(text("INSERT INTO login_buckets (key, window_start, attempts) VALUES (:key, clock_timestamp() - interval '30 seconds', 10)"), {"key": key})
                await session.commit()
            allowed, retry = await login_limits.check_login_limit(host)
            assert not allowed
            assert 1 <= retry <= 30
            async with factory() as session:
                await session.execute(text("UPDATE login_buckets SET window_start = clock_timestamp() - interval '61 seconds' WHERE key = :key"), {"key": key})
                await session.commit()
            allowed, retry = await login_limits.check_login_limit(host)
            assert allowed
            assert 1 <= retry <= 60
        finally:
            async with factory() as session:
                await session.execute(text("DELETE FROM login_buckets WHERE key = :key"), {"key": key})
                await session.commit()
            await engine.dispose()
    asyncio.run(run())
