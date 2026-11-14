from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Repository
from app.jobs import enqueue

router = APIRouter(prefix="/repos", tags=["repos"])


class CreateRepoBody(BaseModel):
    remote_url: str
    name: str | None = None
    branch: str = "main"


@router.post("")
async def create_repo(body: CreateRepoBody):
    from app.repo_security import validate_remote_url
    try:
        validate_remote_url(body.remote_url, settings.sentra_git_allowed_hosts)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    async with SessionLocal() as session:
        name = body.name or body.remote_url.rstrip("/").split("/")[-1].removesuffix(".git") or "repo"
        rid = uuid.uuid4()
        repo = Repository(id=rid, name=name, remote_url=body.remote_url, branch=body.branch, status="pending")
        session.add(repo)
        await session.flush()
        job_id = await enqueue(session, rid, "ingest")
        await session.commit()
    return {"id": str(rid), "name": name, "remote_url": body.remote_url, "branch": body.branch, "status": "pending", "job_id": str(job_id)}


@router.get("")
async def list_repos():
    async with SessionLocal() as session:
        rows = (await session.execute(select(Repository).order_by(Repository.created_at.desc()))).scalars().all()
        return [{"id": str(r.id), "name": r.name, "remote_url": r.remote_url, "branch": r.branch, "commit_sha": r.commit_sha, "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


@router.get("/{repo_id}")
async def get_repo(repo_id: uuid.UUID):
    async with SessionLocal() as session:
        r = await session.get(Repository, repo_id)
        if r is None:
            raise HTTPException(status_code=404, detail="repo not found")
        return {"id": str(r.id), "name": r.name, "remote_url": r.remote_url, "branch": r.branch, "commit_sha": r.commit_sha, "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else None}


@router.post("/{repo_id}/embed")
async def embed_repo_route(repo_id: uuid.UUID):
    async with SessionLocal() as session:
        repo = await session.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="repo not found")
        job_id = await enqueue(session, repo_id, "embed")
        await session.commit()
    return {"repo_id": str(repo_id), "status": "queued", "job_id": str(job_id)}


@router.post("/{repo_id}/ingest")
async def retry_ingest(repo_id: uuid.UUID):
    async with SessionLocal() as session:
        repo = await session.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="repo not found")
        job_id = await enqueue(session, repo_id, "ingest")
        repo.status = "pending"
        await session.commit()
    return {"repo_id": str(repo_id), "status": "pending", "job_id": str(job_id)}
