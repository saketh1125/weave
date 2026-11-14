from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models import Job, Repository, DocumentationReview

MAX_ATTEMPTS = 3
LEASE = timedelta(minutes=2)
HEARTBEAT_SECONDS = 30
ACTIVE = ("queued", "running")


def repo_lock_key(repo_id: uuid.UUID) -> int:
    return int.from_bytes(hashlib.sha256(b"sentra-job:" + repo_id.bytes).digest()[:8], "big", signed=True)


async def enqueue(session, repo_id: uuid.UUID, kind: str, payload: dict | None = None):
    """Enqueue atomically; concurrent identical requests reuse the active job."""
    for _ in range(3):
        job_id = uuid.uuid4()
        statement = (insert(Job).values(id=job_id, repo_id=repo_id, kind=kind,
                     payload=payload or {}, status="queued", attempts=0)
                     .on_conflict_do_nothing(index_elements=[Job.repo_id],
                         index_where=text("status IN ('queued', 'running')")).returning(Job.id))
        inserted = (await session.execute(statement)).scalar_one_or_none()
        if inserted:
            return inserted
        existing = (await session.execute(select(Job).where(
            Job.repo_id == repo_id, Job.status.in_(ACTIVE)))).scalar_one_or_none()
        if existing is None:
            # The conflicting job may have finished between the two statements.
            continue
        if existing.kind != kind or existing.payload != (payload or {}):
            raise HTTPException(409, "Another job is already active for this repository")
        return existing.id
    raise HTTPException(409, "Repository job state changed; retry the request")


async def reconcile_review(session, job, status):
    if job.kind != "documentation_review":
        return
    review = (await session.execute(select(DocumentationReview).where(
        DocumentationReview.job_id == job.id, DocumentationReview.repo_id == job.repo_id).with_for_update())).scalar_one_or_none()
    if review and review.status not in ("completed", "failed", "stale", "paused"):
        review.status = status
        review.reason = "job_retry_limit" if status == "failed" else None


async def claim(session, lock_connection):
    """Claim a queued or expired job while retaining its repository advisory lock."""
    eligible = or_(Job.status == "queued", and_(Job.status == "running", Job.lease_expires_at <= func.now()))
    candidates = (await session.execute(select(Job).where(eligible).order_by(Job.created_at, Job.id)
                  .with_for_update(skip_locked=True).limit(20))).scalars().all()
    for job in candidates:
        acquired = await lock_connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"),
                                                {"key": repo_lock_key(job.repo_id)})
        if not acquired:
            continue
        if job.attempts >= MAX_ATTEMPTS:
            job.updated_at = func.now()
            job.status = "failed"
            job.lease_token = None
            job.lease_expires_at = None
            job.last_error = "Retry limit reached"
            await reconcile_review(session, job, "failed")
            repo = await session.get(Repository, job.repo_id)
            if repo and job.kind in ("ingest", "scan"):
                repo.status = "failed" if job.kind == "ingest" else "scan_failed"
            await session.commit()
            return None
        job.updated_at = func.now()
        job.status = "running"
        job.attempts += 1
        job.lease_token = uuid.uuid4()
        job.lease_expires_at = func.now() + LEASE
        job.last_error = None
        job.result = None
        await session.commit()
        return job
    await session.rollback()
    return None


async def heartbeat(job_id, token):
    async with SessionLocal() as session:
        result = await session.execute(update(Job).where(Job.id == job_id, Job.status == "running",
                                      Job.lease_token == token, Job.lease_expires_at > func.now()).values(lease_expires_at=func.now() + LEASE, updated_at=func.now())
                                      .returning(Job.id))
        if result.scalar_one_or_none() is None:
            await session.rollback()
            raise RuntimeError("Job lease lost")
        await session.commit()


async def finish(job_id, token, error=None, result=None):
    async with SessionLocal() as session:
        job = (await session.execute(select(Job).where(Job.id == job_id, Job.status == "running",
                 Job.lease_token == token, Job.lease_expires_at > func.now()).with_for_update())).scalar_one_or_none()
        if not job:
            return
        job.updated_at = func.now()
        job.status = "succeeded" if error is None else ("failed" if job.attempts >= MAX_ATTEMPTS else "queued")
        job.last_error = "Job execution failed" if error is not None else None
        if error is not None:
            job.result = None
        elif result is not None:
            job.result = result
        job.lease_token = None
        job.lease_expires_at = None
        if error is not None:
            await reconcile_review(session, job, "failed" if job.status == "failed" else "queued")
        if error is not None and job.status == "failed":
            repo = await session.get(Repository, job.repo_id)
            if repo and job.kind in ("ingest", "scan"):
                repo.status = "failed" if job.kind == "ingest" else "scan_failed"
        await session.commit()
