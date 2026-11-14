from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict

SEV_WEIGHT = {"critical": 10, "high": 5, "medium": 2, "low": 0.5, "unknown": 0.5}
CAPS = {"m2": 35, "m4": 30, "m5": 15, "m6": 20}
DEFAULT_CAPS = CAPS.copy()


def _value(finding, name, default=None):
    if isinstance(finding, dict):
        return finding.get(name, default)
    return getattr(finding, name, default)


def _normal_severity(value) -> str:
    if not isinstance(value, str):
        return "unknown"
    value = value.strip().lower()
    return value if value in SEV_WEIGHT else "unknown"


def _is_active(finding) -> bool:
    # Unknown statuses stay active: only explicit closed states stop contributing.
    return str(_value(finding, "status", "open") or "open").strip().lower() not in {"resolved", "ignored", "dismissed"}


def score_input_fingerprint(findings: list, repo_revision=None) -> str:
    """Hash all finding state that can change the meaning of a saved score."""
    fields = ("id", "module", "severity", "status", "location", "source", "explanation", "stable_key", "detail", "canonical_id")
    normalized = []
    for finding in findings:
        row = {field: _value(finding, field) for field in fields}
        if row["id"] is not None:
            row["id"] = str(row["id"])
        normalized.append(row)
    normalized.sort(key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"), default=str))
    payload = json.dumps({
        "findings": normalized,
        "repo_revision": repo_revision,
        "severity_weights": SEV_WEIGHT,
        "module_caps": CAPS,
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot_is_fresh(snapshot, findings: list, repo_revision=None) -> bool:
    config = _value(snapshot, "config_snapshot", {}) or {}
    saved_fingerprint = config.get("input_fingerprint") if isinstance(config, dict) else None
    return bool(saved_fingerprint) and saved_fingerprint == score_input_fingerprint(findings, repo_revision)


def score_label(s: int) -> str:
    if s >= 85:
        return "Ready"
    if s >= 60:
        return "Needs Attention"
    return "At Risk"


def compute_score(findings: list) -> dict:
    by_module = defaultdict(list)
    for finding in findings:
        if not _is_active(finding):
            continue
        module = _value(finding, "module") or "unknown"
        severity = _normal_severity(_value(finding, "severity"))
        by_module[module if module in CAPS else "other"].append(severity)

    breakdown = {}
    total_deduction = 0
    modules = list(CAPS) + sorted(module for module in by_module if module not in CAPS)
    for module in modules:
        severities = by_module.get(module, [])
        if module not in CAPS and not severities:
            continue
        raw = sum(SEV_WEIGHT[severity] for severity in severities)
        cap = CAPS.get(module)
        deduction = min(raw, cap) if cap is not None else raw
        item = {
            "count": len(severities),
            "raw": round(raw, 2),
            "deduction": round(deduction, 2),
            "by_severity": dict(sorted(Counter(severities).items())),
        }
        if cap is not None:
            item["cap"] = cap
        breakdown[module] = item
        total_deduction += deduction

    score = max(0, min(100, round(100 - total_deduction)))
    return {
        "score": score,
        "label": score_label(score),
        "deduction": round(total_deduction, 2),
        "breakdown": breakdown,
        "config_snapshot": {"sev_weight": SEV_WEIGHT, "caps": CAPS},
    }


async def load_score_inputs(repo_id: uuid.UUID, session):
    from sqlalchemy import select
    from app.models import Finding, Repository

    rows = (await session.execute(
        select(Repository.commit_sha, Finding)
        .outerjoin(Finding, Finding.repo_id == Repository.id)
        .where(Repository.id == repo_id)
    )).all()
    return ([row[1] for row in rows if row[1] is not None], rows[0][0] if rows else None)


SCANNER_MODULES = ("m2", "m4")


def analysis_coverage(revision, results: list) -> dict:
    """Summarize successful, revision-matched scanner provenance."""
    completed = set()
    if isinstance(revision, str) and revision:
        for result in results:
            if not isinstance(result, dict) or result.get("commit_sha") != revision:
                continue
            modules = result.get("modules")
            if isinstance(modules, list):
                completed.update(module for module in modules if isinstance(module, str) and module in SCANNER_MODULES)
    done = [module for module in SCANNER_MODULES if module in completed]
    missing = [module for module in SCANNER_MODULES if module not in completed]
    return {"revision": revision, "status": "complete" if not missing else "incomplete" if done else "unknown",
            "completed_modules": done, "missing_modules": missing}


async def load_analysis_coverage(repo_id: uuid.UUID, revision, session) -> dict:
    from sqlalchemy import select
    from app.models import Job

    if not isinstance(revision, str) or not revision:
        return analysis_coverage(revision, [])
    results = (await session.execute(
        select(Job.result).where(
            Job.repo_id == repo_id, Job.kind == "scan", Job.status == "succeeded",
            Job.result["commit_sha"].as_string() == revision,
        )
    )).scalars().all()
    return analysis_coverage(revision, results)


async def snapshot_score(repo_id: uuid.UUID, session) -> dict:
    from app.models import ScoreSnapshot

    rows, revision = await load_score_inputs(repo_id, session)
    result = compute_score(rows)
    coverage = await load_analysis_coverage(repo_id, revision, session)
    config = {**result["config_snapshot"], "input_fingerprint": score_input_fingerprint(rows, revision), "repo_revision": revision}
    snapshot = ScoreSnapshot(
        repo_id=repo_id,
        score=result["score"],
        label=result["label"],
        breakdown=result["breakdown"],
        config_snapshot=config,
    )
    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)
    return {"id": str(snapshot.id), **result, "config_snapshot": config,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
            "snapshot": True, "stale": False, "analysis_coverage": coverage}


async def latest_score(repo_id: uuid.UUID, session) -> dict | None:
    from sqlalchemy import select
    from app.models import ScoreSnapshot

    query = select(ScoreSnapshot).where(ScoreSnapshot.repo_id == repo_id).order_by(ScoreSnapshot.created_at.desc()).limit(1)
    snapshot = (await session.execute(query)).scalars().first()
    if not snapshot:
        return None
    findings, revision = await load_score_inputs(repo_id, session)
    coverage = await load_analysis_coverage(repo_id, revision, session)
    fresh = snapshot_is_fresh(snapshot, findings, revision)
    result = compute_score(findings) if not fresh else None
    return {
        "id": str(snapshot.id), "repo_id": str(snapshot.repo_id),
        "score": snapshot.score if fresh else result["score"],
        "label": snapshot.label if fresh else result["label"],
        "breakdown": snapshot.breakdown if fresh else result["breakdown"],
        "config_snapshot": snapshot.config_snapshot if fresh else result["config_snapshot"],
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        "snapshot": fresh, "stale": not fresh, "analysis_coverage": coverage,
    }
