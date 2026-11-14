"""Repository checkouts are isolated by database identity."""
from pathlib import Path
import subprocess

DATA_REPOS = Path(__file__).resolve().parents[1] / "data" / "repos"


def repo_path(repo) -> Path:
    target = DATA_REPOS / str(repo.id)
    if target.exists():
        if target.is_symlink() or not target.resolve().is_relative_to(DATA_REPOS.resolve()):
            raise ValueError("Repository checkout is outside its storage directory")
        return target
    # Older installations used display names. Read them only when Git confirms
    # the remote and indexed revision; new indexing always uses the UUID path.
    name = repo.name
    if not name or Path(name).name != name or name in {".", ".."}:
        return target
    legacy = DATA_REPOS / name
    if not (legacy / ".git").is_dir() or legacy.is_symlink():
        return target
    try:
        remote = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"], cwd=legacy,
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=legacy,
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return target
    return legacy if remote == repo.remote_url and revision == repo.commit_sha else target
