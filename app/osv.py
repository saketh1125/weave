from __future__ import annotations
import json
import subprocess
import uuid
from pathlib import Path

from app.repo_storage import DATA_REPOS, repo_path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER_CANDIDATES = [REPO_ROOT / ".venv" / "bin" / "osv-scanner", Path("/usr/local/bin/osv-scanner"), Path("osv-scanner")]

def _scanner_bin() -> str | None:
    for p in SCANNER_CANDIDATES:
        if Path(p).exists():
            return str(p)
    import shutil
    w = shutil.which("osv-scanner")
    return w

def _repo_path(repo) -> Path:
    return repo_path(repo)

def run_osv_scanner(repo_path: Path) -> dict:
    bin_path = _scanner_bin()
    if not bin_path:
        return {"results": [], "_error": "osv-scanner not found"}
    if not repo_path.exists():
        return {"_error": "repository path not found"}
    # Source scans must not compile repositories or run dependency build scripts.
    cmd = [bin_path, "scan", "source", "--no-call-analysis=all", "--no-ignore", "--format", "json", "-r", str(repo_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode not in (0, 1) or not proc.stdout or not proc.stdout.strip():
            return {"_error": "osv-scanner did not complete successfully"}
        data = json.loads(proc.stdout)
        if (not isinstance(data, dict) or not isinstance(data.get("results"), list)
                or any(not isinstance(result, dict) for result in data["results"])
                or data.get("errors")):
            return {"_error": "osv-scanner returned incomplete results"}
        return data
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return {"_error": "osv-scanner did not complete successfully"}


def scan_repo(repo) -> list:
    rp = _repo_path(repo)
    data = run_osv_scanner(rp)
    if data.get("_error") or data.get("_stderr"):
        raise RuntimeError(data.get("_error") or data.get("_stderr"))
    import sys
    sys.path.insert(0, str(REPO_ROOT / "spikes" / "s3_osv"))
    from normalizer import normalize_scanner_output
    return normalize_scanner_output(data, root=str(rp))

async def upsert_osv_findings(repo_id: uuid.UUID, session):
    from app.models import Repository
    repo = await session.get(Repository, repo_id)
    if not repo:
        return {"error": "repo not found"}
    findings = scan_repo(repo)
    from app.findings import reconcile_scanner_findings
    normalized = []
    legacy_keys: dict[str, set[str]] = {}
    for f in findings:
        sev = f.severity if f.severity in ("critical", "high", "medium", "low", "unknown") else "unknown"
        stable = f"{repo_id}::{f.ecosystem}::{f.canonical_id}::{f.package}@{f.version}"
        legacy = f"{repo_id}::{f.canonical_id}::{f.package}@{f.version}"
        legacy_keys.setdefault(legacy, set()).add(stable)
        loc = {"path": f.lockfile_path}
        detail = {"ecosystem": f.ecosystem, "package": f.package, "version": f.version, "fixed_version": f.fixed_version, "affected_range": f.affected_range, "cvss_vector": f.cvss_vector, "cvss_base_score": f.cvss_base_score, "severity_source": f.severity_source, "advisory_ids": f.advisory_ids, "cve_id": f.cve_id}
        normalized.append({"severity": sev, "location": loc, "source": f.package, "explanation": f.summary[:500], "stable_key": stable, "detail": detail, "canonical_id": f.canonical_id})
    normalized = await reconcile_scanner_findings(repo_id, "m4", normalized, session, legacy_keys=legacy_keys)
    out = [{"canonical_id": row["canonical_id"], "package": row["source"], "version": row["detail"]["version"], "severity": row["severity"], "fixed_version": row["detail"]["fixed_version"]} for row in normalized]
    return {"findings": out, "count": len(out)}
