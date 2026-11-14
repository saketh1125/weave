#!/usr/bin/env python3
"""Measure retrieval against the small, pinned synthetic fixture corpus.

This is a fixture contract baseline, not a representative repository benchmark.
It uses the application chunkers, embedding render format, query model, and Ask
ranker. It does not connect to the database or call an answer provider.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no resource module
    resource = None

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "retrieval"
SUPPORTED_EXTENSIONS = {".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".md", ".markdown"}
LOCAL_CORPUS_PREFIXES = ("app/", "frontend/src/", "docs/")
EXCLUDED_PARTS = {".git", "work", "data", "node_modules", "dist", "build", ".venv", "venv", "__pycache__"}
sys.path.insert(0, str(REPO_ROOT))

from app.chunker import Chunker, language_for_path  # noqa: E402
from app.embed import CACHE_DIR as DEFAULT_CACHE_DIR, DEFAULT_MODEL, SLICE, _render  # noqa: E402
from app.markdown_chunker import chunk_markdown  # noqa: E402
from app.routers.ask import _rank_candidates  # noqa: E402

_SYMBOL = re.compile(r"\b[a-zA-Z]\w*_[a-zA-Z0-9_]\w*\b")


def _read_json(path: Path):
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def _verify_fixture(manifest: dict) -> None:
    expected = manifest["file_sha256"]
    actual_paths = sorted(
        path.relative_to(FIXTURE_ROOT).as_posix()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    )
    if actual_paths != sorted(expected):
        raise RuntimeError("retrieval fixture file inventory does not match its pinned manifest")
    for relpath, expected_hash in expected.items():
        actual_hash = hashlib.sha256((FIXTURE_ROOT / relpath).read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError(f"retrieval fixture hash mismatch: {relpath}")


def _load_model(cache_dir: Path):
    from fastembed import TextEmbedding

    parameters = inspect.signature(TextEmbedding).parameters.values()
    has_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters)
    explicit_offline = "local_files_only" in inspect.signature(TextEmbedding).parameters
    docs = inspect.getdoc(TextEmbedding.__init__) or ""
    if not explicit_offline and not (has_kwargs and "local_files_only" in docs):
        raise RuntimeError(
            "Installed fastembed does not advertise local_files_only support; refusing to load a model that could download"
        )
    if not cache_dir.is_dir():
        raise RuntimeError(f"offline model cache directory does not exist: {cache_dir}")
    try:
        return TextEmbedding(
            model_name=DEFAULT_MODEL,
            cache_dir=str(cache_dir),
            local_files_only=True,
        )
    except Exception as exc:
        raise RuntimeError(
            f"could not load {DEFAULT_MODEL} from the local cache in offline mode: {type(exc).__name__}: {exc}"
        ) from exc


def _chunks_from_paths(repo_path: Path, paths: list[Path]):
    syntax_chunker = Chunker()
    chunks = []
    diagnostics = {"parse_errors": [], "unsupported_files": []}
    for path in sorted(paths):
        relpath = path.relative_to(repo_path).as_posix()
        if path.suffix.lower() in {".md", ".markdown"}:
            chunks.extend(chunk_markdown(path.read_bytes(), relpath))
            continue
        language = language_for_path(path)
        if language is None:
            diagnostics["unsupported_files"].append(relpath)
            continue
        file_chunks, parse_info = syntax_chunker.chunk_source(path.read_bytes(), relpath, language)
        if parse_info.get("has_error"):
            diagnostics["parse_errors"].append(relpath)
        chunks.extend(chunk for chunk in file_chunks if chunk.kind != "module")
    chunks.sort(key=lambda chunk: (chunk.file_path, chunk.start_line, chunk.end_line, chunk.kind))
    return chunks, diagnostics


def _repo_chunks(repo_name: str):
    repo_path = FIXTURE_ROOT / repo_name
    return _chunks_from_paths(repo_path, [path for path in repo_path.rglob("*") if path.is_file()])


def _git_output(repo_root: Path, *args: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=False,
    )
    return [item.decode("utf-8", errors="surrogateescape") for item in completed.stdout.split(b"\0") if item]


def _local_repository_inventory(repository_root: Path):
    requested_root = repository_root.resolve()
    try:
        git_root = Path(_git_output(requested_root, "rev-parse", "--show-toplevel")[0].rstrip("\r\n")).resolve()
        tracked = set(_git_output(requested_root, "ls-files", "-z", "--cached"))
        untracked = set(_git_output(requested_root, "ls-files", "-z", "--others", "--exclude-standard"))
        head = _git_output(requested_root, "rev-parse", "HEAD")[0].strip()
    except (OSError, subprocess.CalledProcessError, IndexError) as exc:
        raise RuntimeError(f"could not enumerate a Git working tree at {requested_root}: {exc}") from exc
    if git_root != requested_root:
        raise RuntimeError(f"--repository-root must be the Git root ({git_root}), not a subdirectory")

    included = {}
    for relpath in sorted(tracked | untracked):
        path = requested_root / relpath
        normalized = relpath.replace("\\", "/")
        parts = set(Path(normalized).parts)
        if parts.intersection(EXCLUDED_PARTS) or path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if not (normalized == "README.md" or normalized.startswith(LOCAL_CORPUS_PREFIXES)):
            continue
        state = "tracked" if relpath in tracked else "untracked_source_or_docs"
        included[normalized] = {"path": path, "tracking": state}

    if not included:
        raise RuntimeError("no tracked or allowed untracked source/documentation files were found")
    manifest_files = []
    for relpath, item in sorted(included.items()):
        digest = hashlib.sha256(item["path"].read_bytes()).hexdigest()
        manifest_files.append({"path": relpath, "tracking": item["tracking"], "sha256": digest})
    canonical = json.dumps(manifest_files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    corpus_manifest = {
        "git_head": head,
        "files": manifest_files,
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }
    return requested_root, [item["path"] for item in included.values()], corpus_manifest


def _cosine_scores(vectors: np.ndarray, query_vector: np.ndarray) -> np.ndarray:
    query_norm = float(np.linalg.norm(query_vector))
    vector_norms = np.linalg.norm(vectors, axis=1)
    denominator = vector_norms * query_norm
    scores = np.zeros(len(vectors), dtype=np.float32)
    valid = denominator > 0
    scores[valid] = (vectors[valid] @ query_vector) / denominator[valid]
    return scores


def _raw_pool(chunks, vectors, question: str, query_vector, limit: int):
    symbols = tuple(dict.fromkeys(_SYMBOL.findall(question)))[:12]
    raw = []
    pools = [("markdown", [index for index, chunk in enumerate(chunks) if chunk.kind == "markdown"], False)]
    pools.append(("code", [index for index, chunk in enumerate(chunks) if chunk.kind != "markdown"], False))
    if symbols:
        pools.append(("code", [index for index, chunk in enumerate(chunks)
                                if chunk.kind != "markdown" and chunk.name in symbols], True))

    scores = _cosine_scores(vectors, query_vector)
    for _, indexes, _ in pools:
        selected = sorted(indexes, key=lambda index: (-float(scores[index]), chunks[index].file_path,
                                                       chunks[index].start_line))[:limit]
        for index in selected:
            chunk = chunks[index]
            raw.append({
                "file": chunk.file_path,
                "kind": chunk.kind,
                "name": chunk.name,
                "qualified_name": chunk.qualified_name,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "byte_start": chunk.byte_start,
                "byte_end": chunk.byte_end,
                "content": chunk.content,
                "score": float(scores[index]),
            })
    return raw, symbols


def _peak_rss_mb() -> float | None:
    if resource is None or not sys.platform.startswith("linux"):
        return None
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)


def evaluate_repo(model, repo_name: str, questions: list[dict], top_k: int,
                  repository_root: Path | None = None, repository_paths: list[Path] | None = None):
    started = time.perf_counter()
    if repository_root is None:
        chunks, diagnostics = _repo_chunks(repo_name)
    else:
        chunks, diagnostics = _chunks_from_paths(repository_root, repository_paths or [])
    if not chunks:
        raise RuntimeError(f"no retrievable chunks were produced for fixture {repo_name}")

    passage_texts = [_render(chunk, chunk.file_path) for chunk in chunks]
    passage_vectors = np.asarray([
        vector
        for offset in range(0, len(passage_texts), SLICE)
        for vector in model.passage_embed(passage_texts[offset:offset + SLICE])
    ], dtype=np.float32)
    if passage_vectors.ndim != 2 or passage_vectors.shape[0] != len(chunks):
        raise RuntimeError(f"passage embedding count mismatch for fixture {repo_name}")
    query_vectors = list(model.query_embed([item["question"] for item in questions]))
    if len(query_vectors) != len(questions):
        raise RuntimeError(f"query embedding count mismatch for fixture {repo_name}")

    query_results = []
    supported_hits = 0
    supported_total = 0
    negative_with_candidates = 0
    negative_empty = 0
    pool_limit = min(40, max(1, top_k * 2))
    for question, query_vector in zip(questions, query_vectors, strict=True):
        raw_candidates, symbols = _raw_pool(chunks, passage_vectors, question["question"],
                                            np.asarray(query_vector, dtype=np.float32), pool_limit)
        ranked = _rank_candidates(raw_candidates, question["question"], top_k, symbols)
        retrieved_files = list(dict.fromkeys(candidate["file"] for candidate in ranked))
        unsupported = bool(question.get("unsupported", False))
        if unsupported:
            if ranked:
                negative_with_candidates += 1
            else:
                negative_empty += 1
            hit = None
        else:
            supported_total += 1
            expected = set(question["expected_files"])
            hit = bool(expected.intersection(retrieved_files))
            supported_hits += int(hit)
        query_results.append({
            "id": question["id"],
            "category": question["category"],
            "question": question["question"],
            "unsupported": unsupported,
            "expected_files": question["expected_files"],
            "retrieved_files_at_5": retrieved_files,
            "recall_at_5_hit": hit,
            "returned_candidate_count": len(ranked),
        })

    duration = time.perf_counter() - started
    return {
        "repo": repo_name,
        "fixture_chunks": len(chunks),
        "questions": query_results,
        "recall_at_5": {"hits": supported_hits, "total": supported_total,
                        "value": round(supported_hits / supported_total, 4) if supported_total else None},
        "unsupported_retrieval": {
            "questions": negative_with_candidates + negative_empty,
            "empty_result_abstentions": negative_empty,
            "returned_candidates": negative_with_candidates,
            "semantic_answer_abstention_measured": False,
        },
        "duration_seconds": round(duration, 4),
        "peak_rss_mb": _peak_rss_mb(),
        "diagnostics": diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                        help="existing FastEmbed cache directory (loaded with local_files_only=True)")
    parser.add_argument("--repository-root", type=Path,
                        help="evaluate a local Git working tree instead of the pinned synthetic fixtures")
    parser.add_argument("--questions", type=Path,
                        help="JSON question labels for --repository-root mode")
    parser.add_argument("--output", type=Path, help="optional path for the JSON report; stdout is used otherwise")
    args = parser.parse_args()
    top_k = 5

    if bool(args.repository_root) != bool(args.questions):
        parser.error("--repository-root and --questions must be supplied together")

    local_mode = args.repository_root is not None
    if local_mode:
        repository_root, repository_paths, corpus_manifest = _local_repository_inventory(args.repository_root)
        questions_path = args.questions.resolve()
        questions_data = _read_json(questions_path)
        questions = questions_data.get("questions")
        if questions_data.get("split") != "development":
            raise RuntimeError("real repository retrieval questions must be labeled as the development split")
        if not isinstance(questions, list) or len(questions) != 10:
            raise RuntimeError("the real repository development baseline requires exactly 10 labeled questions")
        included_paths = {item["path"] for item in corpus_manifest["files"]}
        for question in questions:
            if not isinstance(question.get("expected_files"), list):
                raise RuntimeError(f"question {question.get('id')} must list expected_files")
            if question.get("unsupported") or not question["expected_files"]:
                raise RuntimeError("real repository baseline questions must be supported and have expected files")
            missing = sorted(set(question["expected_files"]) - included_paths)
            if missing:
                raise RuntimeError(f"question {question.get('id')} expects files outside the evaluated corpus: {missing}")
        selected_corpora = [(repository_root.name, questions, repository_root, repository_paths)]
    else:
        manifest = _read_json(FIXTURE_ROOT / "manifest.json")
        _verify_fixture(manifest)
        questions_path = FIXTURE_ROOT / "questions.json"
        questions_data = _read_json(questions_path)
        questions = questions_data["questions"]
        if len(questions) != 12 or any(sum(item["repo"] == repo["id"] for item in questions) != 4
                                       for repo in manifest["repositories"]):
            raise RuntimeError("fixture contract must contain 12 questions, four for each repository")
        selected_corpora = [
            (repo["id"], [item for item in questions if item["repo"] == repo["id"]], None, None)
            for repo in manifest["repositories"]
        ]

    model_start = time.perf_counter()
    model = _load_model(args.cache_dir)
    model_load = time.perf_counter() - model_start

    report_start = time.perf_counter()
    repos = []
    for repo_name, repo_questions, repo_root, repo_paths in selected_corpora:
        repos.append(evaluate_repo(model, repo_name, repo_questions, top_k, repo_root, repo_paths))
    supported_hits = sum(repo["recall_at_5"]["hits"] for repo in repos)
    supported_total = sum(repo["recall_at_5"]["total"] for repo in repos)
    unsupported_count = sum(repo["unsupported_retrieval"]["questions"] for repo in repos)
    unsupported_empty = sum(repo["unsupported_retrieval"]["empty_result_abstentions"] for repo in repos)
    unsupported_candidates = sum(repo["unsupported_retrieval"]["returned_candidates"] for repo in repos)
    report = {
        "baseline": "local repository development retrieval baseline" if local_mode else "synthetic retrieval fixtures only",
        "model": DEFAULT_MODEL,
        "embedding_batch_size": SLICE,
        "cache_dir": str(args.cache_dir),
        "offline_mode": True,
        "top_k": top_k,
        "recall_at_5": {"hits": supported_hits, "total": supported_total,
                        "value": round(supported_hits / supported_total, 4) if supported_total else None},
        "unsupported_retrieval": {
            "questions": unsupported_count,
            "empty_result_abstentions": unsupported_empty,
            "returned_candidates": unsupported_candidates,
            "semantic_answer_abstention_measured": False,
            "note": "No answer model is called; empty retrieval is only a retrieval-stage abstention proxy.",
        },
        "model_load_seconds": round(model_load, 4),
        "duration_seconds": round(time.perf_counter() - report_start, 4),
        "rss_measurement": "Linux process high-water mark (ru_maxrss), sampled after each repository; cumulative in this process",
        "repositories": repos,
    }
    if local_mode:
        report.update({
            "split": "development",
            "repository_root_name": repository_root.name,
            "question_file": str(questions_path),
            "question_set_sha256": hashlib.sha256(questions_path.read_bytes()).hexdigest(),
            "corpus_manifest": corpus_manifest,
        })
    else:
        report.update({
            "fixture_version": manifest["version"],
            "fixture_manifest_sha256": hashlib.sha256((FIXTURE_ROOT / "manifest.json").read_bytes()).hexdigest(),
        })
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
