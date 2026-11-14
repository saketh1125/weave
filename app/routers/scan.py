from __future__ import annotations
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Finding, Repository

router = APIRouter(prefix="/repos", tags=["scan", "findings", "score"])

class ScanIn(BaseModel):
    modules: Optional[list[str]] = None

@router.post("/{repo_id}/scan")
async def trigger_scan(repo_id: uuid.UUID, body: ScanIn):
    from app.jobs import enqueue

    modules = list(dict.fromkeys(module for module in (body.modules or ["m4", "m2"]) if module in {"m4", "m2"}))
    if not modules:
        raise HTTPException(400, "modules must include m2 and/or m4")
    async with SessionLocal() as session:
        repo = await session.get(Repository, repo_id)
        if repo is None:
            raise HTTPException(404, "repo not found")
        job_id = await enqueue(session, repo_id, "scan", {"modules": modules})
        repo.status = "scanning"
        await session.commit()
    return {"status": "scanning", "modules": modules, "job_id": str(job_id)}

@router.get("/{repo_id}/findings")
async def list_findings(repo_id: uuid.UUID, module: Optional[str] = None, severity: Optional[str] = None):
    async with SessionLocal() as s:
        repo = await s.get(Repository, repo_id)
        if not repo:
            raise HTTPException(404, "repo not found")
        q = select(Finding).where(Finding.repo_id == repo_id)
        if module:
            q = q.where(Finding.module == module)
        if severity:
            q = q.where(Finding.severity == severity.lower())
        rows = (await s.execute(q)).scalars().all()
        return [{"id": str(r.id), "module": r.module, "severity": r.severity, "location": r.location, "source": r.source, "explanation": r.explanation, "stable_key": r.stable_key, "canonical_id": r.canonical_id, "detail": r.detail, "status": r.status} for r in rows]

@router.get("/{repo_id}/score")
async def get_score(repo_id: uuid.UUID):
    from sqlalchemy import select as sel
    from app.models import ScoreSnapshot
    async with SessionLocal() as s:
        repo = await s.get(Repository, repo_id)
        if not repo:
            raise HTTPException(404, "repo not found")
        snap = (await s.execute(sel(ScoreSnapshot).where(ScoreSnapshot.repo_id == repo_id).order_by(ScoreSnapshot.created_at.desc()).limit(1))).scalars().first()
        from app.score import compute_score, load_score_inputs, snapshot_is_fresh, load_analysis_coverage
        rows, revision = await load_score_inputs(repo_id, s)
        coverage = await load_analysis_coverage(repo_id, revision, s)
        if snap and snapshot_is_fresh(snap, rows, revision):
            return {"id": str(snap.id), "repo_id": str(snap.repo_id), "score": snap.score, "label": snap.label, "breakdown": snap.breakdown, "config_snapshot": snap.config_snapshot, "created_at": snap.created_at.isoformat() if snap.created_at else None, "snapshot": True, "stale": False, "analysis_coverage": coverage}
        live = compute_score(rows)
        return {"repo_id": str(repo_id), "score": live["score"], "label": live["label"], "breakdown": live["breakdown"], "config_snapshot": live["config_snapshot"], "snapshot": False, "stale": bool(snap), "analysis_coverage": coverage}

@router.post("/{repo_id}/score")
async def post_score(repo_id: uuid.UUID):
    async with SessionLocal() as s:
        repo = await s.get(Repository, repo_id)
        if not repo:
            raise HTTPException(404, "repo not found")
        from app.score import snapshot_score
        return await snapshot_score(repo_id, s)
