"""Boundaries for repository URLs and checkout files."""
from pathlib import Path
import configparser
import re
from urllib.parse import urlsplit


def validate_remote_url(url: str, allowed_hosts: str) -> str:
    """Accept only explicit HTTPS repository URLs on an exact allowlisted host."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError):
        raise ValueError("invalid repository URL") from None
    hosts = {h.strip().lower() for h in allowed_hosts.split(",") if h.strip()}
    if (not isinstance(url, str) or not url.startswith("https://") or
        parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in hosts or
        parsed.netloc.lower() != parsed.hostname.lower() or port is not None or
        parsed.username is not None or parsed.password is not None or "?" in url or "#" in url or
        not re.fullmatch(r"/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?", parsed.path) or
        any(part in (".", "..") for part in parsed.path.split("/"))):
        raise ValueError("repository URL must use HTTPS on an allowed host with a valid repository path")
    return url


def read_checkout_files(root: Path, paths, *, max_file_bytes: int, max_total_bytes: int, max_files: int):
    """Yield bounded files; fail the scan on limits to avoid partial re-ingestion."""
    root = root.resolve(strict=True)
    count = total = 0
    for path in paths:
        path = Path(path)
        try:
            relative = path.relative_to(root)
            if any((root.joinpath(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
                continue
            if not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
                continue
            count += 1
            if count > max_files:
                raise ValueError("checkout file count limit exceeded")
            with path.open("rb") as handle:
                raw = handle.read(max_file_bytes + 1)
            if len(raw) > max_file_bytes:
                raise ValueError("checkout file size limit exceeded")
            total += len(raw)
            if total > max_total_bytes:
                raise ValueError("checkout total size limit exceeded")
            yield path, raw
        except (FileNotFoundError, PermissionError, OSError):
            continue


def validate_local_git_config(path: Path):
    """Reject local Git settings that could rewrite or execute a fetch."""
    parser = configparser.RawConfigParser(strict=True, interpolation=None)
    try:
        with path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
        for section in parser.sections():
            keys = set(parser[section])
            if section == "core" and keys <= {"repositoryformatversion", "filemode", "bare", "logallrefupdates", "symlinks", "ignorecase"}:
                continue
            if section == 'remote "origin"' and keys <= {"url", "fetch"}:
                continue
            if re.fullmatch(r'branch "[^"\\]+"', section) and keys <= {"remote", "merge"}:
                continue
            raise ValueError("unsupported local Git configuration")
    except (OSError, UnicodeError, configparser.Error):
        raise ValueError("unsupported local Git configuration") from None
