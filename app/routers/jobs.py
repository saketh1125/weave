import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job, Repository

router = APIRouter(prefix="/repos", tags=["jobs"])


@router.get("/{repo_id}/jobs")
async def list_jobs(repo_id: uuid.UUID):
    async with SessionLocal() as session:
        if await session.get(Repository, repo_id) is None:
            raise HTTPException(404, "repo not found")
        jobs = (await session.scalars(
            select(Job).where(Job.repo_id == repo_id)
            .order_by(Job.created_at.desc(), Job.id.desc()).limit(50)
        )).all()
    return [{
        "id": str(job.id), "kind": job.kind, "status": job.status,
        "attempts": job.attempts, "error": job.last_error,
        "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat(),
    } for job in jobs]
