from __future__ import annotations
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Mapping

from app.repo_storage import DATA_REPOS, repo_path
from app.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
SEMGREP_BIN = REPO_ROOT / ".venv" / "bin" / "semgrep"
SEV_MAP = {"ERROR": "high", "WARNING": "medium", "INFO": "low", "error": "high", "warning": "medium", "info": "low"}
FALLBACK_SEV = "unknown"

def _sev(s: str) -> str:
    return SEV_MAP.get(s, SEV_MAP.get(s.upper(), FALLBACK_SEV))

def _repo_path(repo) -> Path:
    return repo_path(repo)


def _semgrep_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Pass only runtime, locale, and network trust settings to Semgrep."""
    source = os.environ if source is None else source
    allowed = {
        "PATH", "HOME", "TMP", "TEMP", "TMPDIR", "LANG", "LANGUAGE",
        "LC_ALL", "LC_CTYPE", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        # Windows process creation and executable lookup may require these.
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
    }
    env = {
        key: value for key, value in source.items()
        if key.upper() in allowed or key.upper().startswith("LC_")
    }
    # The CLI argument also disables metrics; pin the environment setting so
    # inherited configuration cannot turn them back on in related code paths.
    env["SEMGREP_SEND_METRICS"] = "off"
    return env

def run_semgrep(repo_path: Path) -> dict:
    bin_path = str(SEMGREP_BIN) if SEMGREP_BIN.exists() else "semgrep"
    import shutil
    if not Path(bin_path).exists() and not shutil.which("semgrep"):
        return {"results": [], "_error": "semgrep not found"}
    cmd = [bin_path, "scan", "--json", "--quiet", "--metrics=off", "--no-git-ignore", "--timeout", "60", "--jobs", str(settings.sentra_scan_jobs), "--config", "p/default", str(repo_path)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            cwd=REPO_ROOT, env=_semgrep_env(),
        )
        if proc.returncode not in (0, 1) or not proc.stdout or not proc.stdout.strip():
            return {"_error": "semgrep did not complete successfully"}
        data = json.loads(proc.stdout)
        if (not isinstance(data, dict) or not isinstance(data.get("results"), list)
                or any(not isinstance(result, dict) for result in data["results"])
                or data.get("errors")):
            return {"_error": "semgrep returned incomplete results"}
        return data
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return {"_error": "semgrep did not complete successfully"}


def normalize_semgrep(data: dict, repo_path: Path) -> list[dict]:
    out = []
    for r in data.get("results", []) or []:
        path = r.get("path") or ""
        try:
            rel = str(Path(path).relative_to(repo_path)) if Path(path).is_absolute() else path
        except Exception:
            rel = path
        start = r.get("start") or {}
        end = r.get("end") or {}
        extra = r.get("extra") or {}
        check_id = r.get("check_id") or "unknown"
        sev_raw = extra.get("severity") or "WARNING"
        out.append({
            "path": rel,
            "check_id": check_id,
            "severity": _sev(sev_raw),
            "severity_raw": sev_raw,
            "start_line": start.get("line"),
            "start_col": start.get("col"),
            "start_offset": start.get("offset"),
            "end_line": end.get("line"),
            "end_offset": end.get("offset"),
            "message": (extra.get("message") or "")[:500],
            "fingerprint": extra.get("fingerprint"),
        })
    return out

def scan_repo(repo) -> list[dict]:
    rp = _repo_path(repo)
    data = run_semgrep(rp)
    if data.get("_error") or data.get("_stderr"):
        raise RuntimeError(data.get("_error") or data.get("_stderr"))
    return normalize_semgrep(data, rp)

async def upsert_semgrep_findings(repo_id: uuid.UUID, session):
    from app.models import Repository
    repo = await session.get(Repository, repo_id)
    if not repo:
        return {"error": "repo not found"}
    findings = scan_repo(repo)
    from app.findings import reconcile_scanner_findings
    normalized = []
    for f in findings:
        sl = f.get("start_line") or 0
        el = f.get("end_line") or sl
        stable = f"{repo_id}::{f['path']}::{f['check_id']}::{sl}-{el}"
        loc = {"path": f["path"], "start_line": sl, "end_line": el, "start_col": f.get("start_col"), "start_offset": f.get("start_offset"), "end_offset": f.get("end_offset")}
        detail = {"check_id": f["check_id"], "severity_raw": f["severity_raw"], "fingerprint": f.get("fingerprint")}
        normalized.append({"severity": f.get("severity") or "unknown", "location": loc, "source": f["check_id"], "explanation": f["message"], "stable_key": stable, "detail": detail, "canonical_id": f["check_id"]})
    normalized = await reconcile_scanner_findings(repo_id, "m2", normalized, session)
    persisted = [{"path": row["location"]["path"], "check_id": row["source"], "severity": row["severity"], "line": row["location"]["start_line"]} for row in normalized]
    return {"findings": persisted, "count": len(persisted)}
