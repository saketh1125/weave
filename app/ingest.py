from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import subprocess
import uuid
from itertools import chain
from pathlib import Path

from sqlalchemy import select

from app.chunker import EXCLUDE_DIRS, Chunker, ChunkerConfig, language_for_path
from app.db import SessionLocal
from app.models import Chunk as ChunkRow
from app.models import File as FileRow
from app.models import Repository
from app.config import settings
from app.markdown_chunker import chunk_markdown
from app.repo_security import validate_remote_url, read_checkout_files, validate_local_git_config

logger = logging.getLogger(__name__)

DATA_REPOS = Path(__file__).resolve().parents[1] / "data" / "repos"


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    if settings.sentra_gh_token:
        auth = base64.b64encode(f"x-access-token:{settings.sentra_gh_token}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}"})
    r = subprocess.run(
        ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
         "-c", "http.followRedirects=false", "-c", "submodule.recurse=false",
         "-c", "fetch.recurseSubmodules=false", "-c", "credential.helper=",
         "-c", f"core.hooksPath={os.devnull}", *args],
        cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=120, env=env,
    )
    if r.returncode != 0:
        raise RuntimeError("git operation failed")
    return r.stdout.strip()


def _markdown_paths(target: Path) -> list[Path]:
    # Tracked files define the corpus for Git checkouts. A failed Git command
    # must not silently turn an incomplete scan into deletions on re-ingest.
    if (target / '.git').exists():
        result = subprocess.run(
            ['git', 'ls-files', '-z', '--cached'], cwd=str(target),
            capture_output=True, check=True, timeout=120,
        )
        return [target / os.fsdecode(p) for p in result.stdout.split(b'\0')
                if p and Path(os.fsdecode(p)).suffix.lower() == '.md']
    return [p for p in target.rglob('*') if p.suffix.lower() == '.md'
            and not any(part in EXCLUDE_DIRS for part in p.relative_to(target).parts)]


def _require_complete_markdown(expected: set[str], ingested: set[str]) -> None:
    """Fail before database writes if any discovered Markdown was omitted."""
    missing = sorted(expected - ingested)
    if missing:
        sample = ', '.join(missing[:10])
        suffix = '…' if len(missing) > 10 else ''
        raise ValueError(f"tracked Markdown ingestion incomplete ({len(missing)} omitted): {sample}{suffix}")


def _source_paths(root: Path):
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDE_DIRS and not (parent / name).is_symlink())
        for name in sorted(files):
            path = parent / name
            if language_for_path(path) is not None:
                yield path


async def ingest_repo(repo_id: uuid.UUID, remote_url: str) -> dict:
    async with SessionLocal() as session:
        repo = await session.get(Repository, repo_id)
        if repo is None:
            return {"error": "repo_not_found", "repo_id": str(repo_id)}
        target = DATA_REPOS / str(repo_id)
        target.parent.mkdir(parents=True, exist_ok=True)

        def _clone_or_pull():
            validate_remote_url(remote_url, settings.sentra_git_allowed_hosts)
            branch = repo.branch or "main"
            if not branch or branch.startswith("-") or any(c.isspace() or ord(c) < 32 for c in branch):
                raise ValueError("invalid repository branch")
            if target.is_symlink() or (target / ".git").is_symlink():
                raise ValueError("checkout path cannot be a symlink")
            if target.exists() and (target / ".git").is_dir():
                validate_local_git_config(target / ".git" / "config")
                _run_git(["fetch", "--depth", "1", "--no-recurse-submodules", "--", remote_url, branch], cwd=target)
                _run_git(["checkout", "--detach", "FETCH_HEAD"], cwd=target)
                _run_git(["reset", "--hard", "FETCH_HEAD"], cwd=target)
            elif target.exists():
                raise ValueError("checkout target already exists and is not a Git repository")
            else:
                _run_git(["clone", "--depth", "1", "--no-recurse-submodules", "--branch", branch, "--", remote_url, str(target)])

        try:
            await asyncio.to_thread(_clone_or_pull)
        except Exception as e:
            repo.status = "failed"
            await session.commit()
            logger.exception("ingest clone failed for %s", repo_id)
            return {"error": str(e)[:500], "repo_id": str(repo_id)}

        try:
            commit_sha = await asyncio.to_thread(lambda: _run_git(["rev-parse", "HEAD"], cwd=target))
        except Exception:
            commit_sha = None

        repo.status = "ingesting"
        await session.commit()

        def _parse_files():
            chunker = Chunker(ChunkerConfig())
            files: dict[str, tuple[str | None, int, str, list]] = {}
            root = target.resolve()
            markdown_paths = sorted(_markdown_paths(root))
            markdown_expected = {path.relative_to(root).as_posix() for path in markdown_paths}
            source_paths = list(_source_paths(root))
            source_expected = {path.relative_to(root).as_posix() for path in source_paths}
            candidates = chain(source_paths, markdown_paths)
            bounded = read_checkout_files(root, candidates,
                max_file_bytes=settings.sentra_repo_max_file_bytes,
                max_total_bytes=settings.sentra_repo_max_total_bytes,
                max_files=settings.sentra_repo_max_files)
            total_chunks = 0
            markdown_ingested: set[str] = set()
            for path, raw in bounded:
                relative = path.relative_to(root).as_posix()
                try:
                    if path.suffix.lower() == ".md":
                        language, chunks = "markdown", chunk_markdown(raw, relative)
                    else:
                        language = language_for_path(path)
                        chunks, _ = chunker.chunk_source(raw, relative, language)
                except (OSError, UnicodeError, ValueError):
                    logger.warning("Skipping unsupported or unreadable source file %s", path)
                    continue
                total_chunks += len(chunks)
                if total_chunks > settings.sentra_repo_max_chunks:
                    raise ValueError("checkout chunk count limit exceeded")
                files[relative] = (language, len(raw), hashlib.sha256(raw).hexdigest(), chunks)
                if path.suffix.lower() == ".md":
                    markdown_ingested.add(relative)
            _require_complete_markdown(markdown_expected, markdown_ingested)
            missing_sources = sorted(source_expected - files.keys())
            if missing_sources:
                raise ValueError(f"source ingestion incomplete ({len(missing_sources)} omitted): {missing_sources[0]}")
            return files

        try:
            file_groups = await asyncio.to_thread(_parse_files)
        except ValueError as exc:
            repo.status = "failed"
            await session.commit()
            return {"error": str(exc), "repo_id": str(repo_id)}

        stats = {"files_seen": len(file_groups), "files_upserted": 0, "files_skipped": 0, "files_deleted": 0, "chunks_written": 0, "chunks_deleted": 0}
        existing_files = (await session.execute(select(FileRow).where(FileRow.repo_id == repo_id))).scalars().all()
        by_path: dict[str, FileRow] = {f.path: f for f in existing_files}

        for rel_path, (lang, byte_len, content_hash, chs) in file_groups.items():

            fr = by_path.get(rel_path)
            if fr is not None and fr.content_hash == content_hash:
                stats["files_skipped"] += 1
                continue

            if fr is None:
                fr = FileRow(repo_id=repo_id, path=rel_path, language=lang, byte_len=byte_len, content_hash=content_hash)
                session.add(fr)
                await session.flush()
            else:
                fr.language = lang
                fr.byte_len = byte_len
                fr.content_hash = content_hash
                await session.flush()
                await session.execute(
                    select(ChunkRow).where(ChunkRow.file_id == fr.id)
                )
                from sqlalchemy import delete
                del_res = await session.execute(delete(ChunkRow).where(ChunkRow.file_id == fr.id))
                stats["chunks_deleted"] += del_res.rowcount or 0

            stats["files_upserted"] += 1
            for ch in chs:
                oversized = ch.line_count > 200
                row = ChunkRow(
                    file_id=fr.id,
                    repo_id=repo_id,
                    kind=ch.kind,
                    name=ch.name,
                    qualified_name=ch.qualified_name,
                    start_line=ch.start_line,
                    end_line=ch.end_line,
                    byte_start=ch.byte_start,
                    byte_end=ch.byte_end,
                    content=ch.content,
                    content_hash=ch.content_hash,
                    parent_kind=ch.parent_kind,
                    line_count=ch.line_count,
                    oversized=oversized,
                )
                session.add(row)
                stats["chunks_written"] += 1

        removed_paths = set(by_path) - set(file_groups)
        if removed_paths:
            from sqlalchemy import delete
            removed_ids = [by_path[path].id for path in removed_paths]
            stats["files_deleted"] = len(removed_ids)
            deleted = await session.execute(delete(FileRow).where(FileRow.id.in_(removed_ids)))
            stats["chunks_deleted"] += deleted.rowcount or 0

        repo.commit_sha = commit_sha
        repo.status = "embedding"
        await session.commit()
        await session.close()
        from app.embed import embed_repo
        embedding_stats = await embed_repo(repo_id)
        async with SessionLocal() as final_session:
            final_repo = await final_session.get(Repository, repo_id)
            if final_repo is not None:
                final_repo.status = "ready"
                await final_session.commit()
        logger.info("ingest %s done: %s commit=%s", repo_id, stats, commit_sha)
        return {"repo_id": str(repo_id), "commit_sha": commit_sha, **stats, "embedding": embedding_stats}
