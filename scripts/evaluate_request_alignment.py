"""Evaluate the synthetic request-body field alignment corpus without a provider."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.doc_alignment import SourceFile
from app.request_alignment import check_request_alignment


CORPUS_PATH = ROOT / "tests" / "fixtures" / "request_field_alignment.json"
CHECKER_FILES = (
    ROOT / "app" / "doc_alignment.py",
    ROOT / "app" / "request_alignment.py",
    ROOT / "app" / "request_claims.py",
    ROOT / "app" / "request_schema.py",
)


def _canonical_utf8_lf_bytes(raw: bytes) -> bytes:
    """Normalize text hashes across Windows autocrlf and LF checkouts."""
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return text.encode("utf-8")


def evaluate(corpus_path: Path = CORPUS_PATH) -> dict:
    raw = corpus_path.read_bytes()
    corpus = json.loads(raw.decode("utf-8-sig"))
    head_result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True,
    )
    git_available = head_result.returncode == 0
    head = head_result.stdout.strip() if git_available else None
    status_result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT,
        capture_output=True, text=True,
    ) if git_available else None
    dirty = bool(status_result.stdout.strip()) if status_result and status_result.returncode == 0 else None
    report = {
        "fixture": str(corpus_path.relative_to(ROOT)).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256(_canonical_utf8_lf_bytes(raw)).hexdigest(),
        "hash_encoding": "UTF-8 with LF newlines",
        "corpus_version": corpus.get("corpus_version"),
        "git_head": head,
        "git_metadata_available": git_available,
        "working_tree_dirty": dirty,
        "checker_sha256": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(_canonical_utf8_lf_bytes(path.read_bytes())).hexdigest()
            for path in CHECKER_FILES
        },
        "scope": "Synthetic regression evidence only; the evaluation split has two synthetic cases and does not estimate real-world accuracy or generalization.",
        "split_counts": {},
        "mismatches": [],
        "unexpected_claims": [],
    }
    for split in sorted({case["split"] for case in corpus["cases"]}):
        report["split_counts"][split] = {
            "cases": 0,
            "expected_claims": 0,
            "actual_claims": 0,
            "exact_status_and_claim_type": 0,
            "citation_matches": 0,
            "false_aligned": 0,
            "checked": 0,
            "unverified": 0,
        }
    for case in corpus["cases"]:
        expected_items = case["expected"] if isinstance(case["expected"], list) else [case["expected"]]
        actual = check_request_alignment(
            [SourceFile(item["path"], item["content"]) for item in case["markdown"]],
            [SourceFile(item["path"], item["content"]) for item in case["code"]],
        )
        counts = report["split_counts"][case["split"]]
        counts["cases"] += 1
        counts["expected_claims"] += len(expected_items)
        counts["actual_claims"] += len(actual)
        used: set[int] = set()
        for expected in expected_items:
            match_index = next((i for i, result in enumerate(actual)
                                if i not in used
                                and result["claim_type"] == expected["claim_type"]
                                and result["doc"] == expected["doc"]), None)
            result = actual[match_index] if match_index is not None else None
            if match_index is not None:
                used.add(match_index)
            exact = result is not None and result["status"] == expected["status"]
            citation_match = result is not None and result["doc"] == expected["doc"] and result["code"] == expected["code"]
            if exact:
                counts["exact_status_and_claim_type"] += 1
            if citation_match:
                counts["citation_matches"] += 1
            if result is not None and result["status"] == "aligned" and expected["status"] != "aligned":
                counts["false_aligned"] += 1
            if result is not None and result["status"] == "unverified":
                counts["unverified"] += 1
            elif result is not None:
                counts["checked"] += 1
            if not exact or not citation_match:
                report["mismatches"].append({
                    "case_id": case["id"],
                    "split": case["split"],
                    "expected_claim_type": expected["claim_type"],
                    "expected_status": expected["status"],
                    "actual_status": result["status"] if result else None,
                    "expected_doc": expected["doc"],
                    "actual_doc": result["doc"] if result else None,
                    "expected_code": expected["code"],
                    "actual_code": result["code"] if result else None,
                })
        for index, result in enumerate(actual):
            if index not in used:
                counts["unexpected_claims"] = counts.get("unexpected_claims", 0) + 1
                if result["status"] == "aligned":
                    counts["false_aligned"] += 1
                report["unexpected_claims"].append({
                    "case_id": case["id"],
                    "split": case["split"],
                    "actual_claim_type": result["claim_type"],
                    "actual_status": result["status"],
                    "actual_doc": result["doc"],
                    "actual_code": result["code"],
                })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Path for the JSON report")
    args = parser.parse_args()
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["mismatches"] or report["unexpected_claims"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
