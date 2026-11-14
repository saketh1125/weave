#!/usr/bin/env python3
"""Bounded, provider-free workload baseline for Sentra's ingestion preparation.

This exercises the production enumeration, bounded file reader, and chunkers.
It does not clone Git repositories, touch the database, or create embeddings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.chunker import Chunker, ChunkerConfig, language_for_path  # noqa: E402
from app.ingest import _markdown_paths, _source_paths  # noqa: E402
from app.markdown_chunker import chunk_markdown  # noqa: E402
from app.repo_security import read_checkout_files  # noqa: E402

PROFILES = {"small": {"active_files": 120, "max_bytes": 8_000_000, "max_chunks": 20_000},
            "medium": {"active_files": 800, "max_bytes": 9_500_000, "max_chunks": 20_000}}
EXTENSIONS = (".py", ".js", ".ts", ".md")


def _peak_rss_mb():
    if resource is None or not sys.platform.startswith("linux"):
        return None
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)


def _make_source(ext: str, index: int, revision: int = 0) -> bytes:
    name = f"unit_{index}_{revision}"
    if ext == ".py":
        return f"def {name}(value):\n    return value + {index + revision}\n".encode()
    if ext == ".js":
        return f"export function {name}(value) {{ return value + {index + revision}; }}\n".encode()
    if ext == ".ts":
        return f"export function {name}(value: number): number {{ return value + {index + revision}; }}\n".encode()
    # Repeated bounded paragraphs force markdown chunker work without large fixtures.
    return (f"# Module {index}\n\n" + (f"Documented behavior for module {index}, revision {revision}.\n\n" * 24)).encode()


def _create_corpus(root: Path, active_files: int) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    created = []
    for i in range(active_files):
        ext = EXTENSIONS[i % len(EXTENSIONS)]
        rel = Path("src") / f"group_{i % 12:02d}" / f"module_{i:04d}{ext}"
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_make_source(ext, i))
        created.append(path)
    for directory in ("node_modules", "dist", ".venv", "vendor", "build", "__pycache__"):
        excluded_root = root / directory
        excluded_root.mkdir(parents=True, exist_ok=True)
        (excluded_root / "must_not_index.py").write_text("def excluded(): return True\n", encoding="utf-8")
        (excluded_root / "must_not_index.md").write_text("# excluded\n", encoding="utf-8")
    nested_git = root / "src" / "group_00" / ".git"
    nested_git.mkdir(parents=True, exist_ok=True)
    (nested_git / "must_not_index.md").write_text("# excluded git metadata\n", encoding="utf-8")
    (root / "ignored.txt").write_text("unsupported", encoding="utf-8")
    return created


def _inventory(root: Path, config: dict):
    root = root.resolve()
    candidates = [*_source_paths(root), *_markdown_paths(root)]
    bounded = read_checkout_files(root, candidates, max_file_bytes=1_000_000,
                                  max_total_bytes=config["max_bytes"], max_files=config["active_files"])
    chunker = Chunker(ChunkerConfig())
    records = []
    parse_failures = []
    for path, raw in bounded:
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() == ".md":
            chunks = chunk_markdown(raw, rel)
            has_error = False
        else:
            chunks, info = chunker.chunk_source(raw, rel, language_for_path(path))
            has_error = bool(info.get("has_error"))
        if has_error:
            parse_failures.append(rel)
        records.append({"path": rel, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "chunks": chunks})
    count = sum(len(item["chunks"]) for item in records)
    if count > config["max_chunks"]:
        raise ValueError("checkout chunk count limit exceeded")
    records.sort(key=lambda item: item["path"])
    canonical = [{"path": item["path"], "size": item["size"], "sha256": item["sha256"]} for item in records]
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return records, {"files": len(records), "chunks": count, "bytes": sum(x["size"] for x in records),
                     "parse_failures": parse_failures, "corpus_sha256": digest}


def evaluate(profile: str, root: Path | None = None):
    config = PROFILES[profile]
    owned = root is None
    temp_parent = Path(tempfile.gettempdir()).resolve()
    work = Path(tempfile.mkdtemp(prefix="sentra-workload-", dir=temp_parent)) if owned else root.resolve()
    owned_work = work.resolve()
    try:
        if not owned:
            work.mkdir(parents=True, exist_ok=True)
        if not owned and any(work.iterdir()):
            raise ValueError("--root must point to an empty directory")
        paths = _create_corpus(work, config["active_files"])
        started = time.perf_counter()
        initial, initial_summary = _inventory(work, config)
        initial_seconds = time.perf_counter() - started
        # Reproduce common incremental changes: content edit, rename, and delete.
        edit = paths[0]
        edit.write_bytes(_make_source(edit.suffix, 0, revision=1))
        renamed = paths[1]
        renamed_new = renamed.with_name("renamed_" + renamed.name)
        renamed.rename(renamed_new)
        deleted = paths[2]
        deleted.unlink()
        changed_start = time.perf_counter()
        updated, updated_summary = _inventory(work, config)
        changed_seconds = time.perf_counter() - changed_start
        old = {x["path"]: x["sha256"] for x in initial}
        new = {x["path"]: x["sha256"] for x in updated}
        added = set(new) - set(old)
        removed = set(old) - set(new)
        rename_pairs = []
        for added_path in sorted(added):
            matches = sorted(path for path in removed if old[path] == new[added_path])
            if matches:
                old_path = matches[0]
                rename_pairs.append({"from": old_path, "to": added_path})
                removed.remove(old_path)
        delta = {"edited": sorted(p for p in old.keys() & new.keys() if old[p] != new[p]),
                 "renamed": rename_pairs, "added": sorted(added - {pair["to"] for pair in rename_pairs}),
                 "deleted": sorted(removed), "removed_paths": sorted(set(old) - set(new))}
        return {"benchmark": "synthetic ingestion preparation", "profile": profile,
                "seed": 20261111, "limits": config, "provider_calls": 0, "embedding_calls": 0,
                "initial": {**initial_summary, "wall_seconds": round(initial_seconds, 4)},
                "after_changes": {**updated_summary, "wall_seconds": round(changed_seconds, 4)},
                "change_detection": delta, "peak_rss_mb_linux": _peak_rss_mb(),
                "scope": "production enumeration, bounded reads, source/Markdown chunkers; excludes Git, DB, embedding, provider"}
    finally:
        if owned:
            if (owned_work.parent == temp_parent and owned_work.name.startswith("sentra-workload-")
                    and owned_work == work.absolute()):
                shutil.rmtree(owned_work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="small")
    parser.add_argument("--root", type=Path, help="use this empty directory as the generated corpus root")
    args = parser.parse_args()
    print(json.dumps(evaluate(args.profile, args.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
