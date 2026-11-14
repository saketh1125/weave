import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import jobs, worker


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class Session:
    def __init__(self, results=(), repo=None):
        self.results = iter(results)
        self.repo = repo
        self.commits = 0
        self.rollbacks = 0
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, statement):
        self.statements.append(statement)
        return Result(next(self.results))

    async def get(self, *args):
        return self.repo

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class Lock:
    async def scalar(self, *args):
        return True


def fake_job(attempts=0):
    return SimpleNamespace(id=uuid.uuid4(), repo_id=uuid.uuid4(), kind="ingest", payload={}, status="queued",
                           attempts=attempts, lease_token=None, lease_expires_at=None, last_error=None)


def test_enqueue_reuses_matching_and_rejects_other_work():
    job = fake_job()
    session = Session([None, job])
    assert asyncio.run(jobs.enqueue(session, job.repo_id, "ingest")) == job.id
    assert "ON CONFLICT" in str(session.statements[0].compile(dialect=__import__("sqlalchemy").dialects.postgresql.dialect()))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(jobs.enqueue(Session([None, job]), job.repo_id, "scan", {"modules": ["m2"]}))
    assert exc.value.status_code == 409


def test_enqueue_retries_when_conflicting_job_finishes():
    new_id = uuid.uuid4()
    session = Session([None, None, new_id])
    assert asyncio.run(jobs.enqueue(session, uuid.uuid4(), "ingest")) == new_id
    assert len(session.statements) == 3


def test_claim_renews_lease_and_stops_after_three_attempts():
    job = fake_job(2)
    session = Session([[job]])
    claimed = asyncio.run(jobs.claim(session, Lock()))
    assert claimed is job and job.status == "running" and job.attempts == 3
    assert job.lease_token and job.lease_expires_at is not None and session.commits == 1
    repo = SimpleNamespace(status="pending")
    session = Session([[job]], repo=repo)
    assert asyncio.run(jobs.claim(session, Lock())) is None
    assert job.status == "failed" and repo.status == "failed" and session.commits == 1


def test_finish_retries_and_preserves_token_ownership(monkeypatch):
    job = fake_job(1)
    session = Session([job])
    monkeypatch.setattr(jobs, "SessionLocal", lambda: session)
    asyncio.run(jobs.finish(job.id, uuid.uuid4(), RuntimeError("private")))
    assert job.status == "queued" and job.last_error == "Job execution failed"
    assert job.lease_token is None and session.commits == 1
    absent = Session([None])
    monkeypatch.setattr(jobs, "SessionLocal", lambda: absent)
    asyncio.run(jobs.finish(job.id, uuid.uuid4()))
    assert absent.commits == 0


def test_lease_failure_terminates_process_group(monkeypatch):
    events = []
    class Process:
        pid = 1234
        def __init__(self):
            self.done = asyncio.Event()
        async def wait(self):
            await self.done.wait()
            return -15
    process = Process()
    async def spawn(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        return process
    def killpg(pid, sig):
        events.append((pid, sig))
        process.done.set()
    async def broken_heartbeat(*args):
        raise RuntimeError("lease failed")
    monkeypatch.setattr(worker.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(worker, "os", SimpleNamespace(name="posix", killpg=killpg))
    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(worker, "heartbeat", broken_heartbeat)
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.001)
    with pytest.raises(RuntimeError, match="lease failed"):
        asyncio.run(worker.run_job(fake_job()))
    assert events == [(1234, worker.signal.SIGTERM), (1234, worker.signal.SIGKILL)]


def test_scan_attempts_remaining_modules_after_failure(monkeypatch):
    import sys
    calls = []
    async def first(*args):
        calls.append("m4")
        raise RuntimeError("scanner failed")
    async def second(*args):
        calls.append("m2")
        return {}
    monkeypatch.setitem(sys.modules, "app.osv", SimpleNamespace(upsert_osv_findings=first))
    monkeypatch.setitem(sys.modules, "app.semgrep", SimpleNamespace(upsert_semgrep_findings=second))
    repo = SimpleNamespace(status="ready", commit_sha="fixture-revision")
    session = Session(repo=repo)
    monkeypatch.setattr(worker, "SessionLocal", lambda: session)
    job = fake_job()
    job.kind = "scan"
    job.payload = {"modules": ["m4", "m2"]}
    with pytest.raises(RuntimeError, match="m4"):
        asyncio.run(worker.execute_job(job))
    assert calls == ["m4", "m2"]
    assert session.rollbacks == 1 and repo.status == "scan_failed"
