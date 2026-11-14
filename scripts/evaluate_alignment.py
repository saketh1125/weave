"""Run the fixed alignment fixtures without models, credentials, or a database."""
from __future__ import annotations

import argparse
import importlib.util
import json
from itertools import zip_longest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def evaluate(checker, dataset: dict) -> dict:
    splits = {}
    for case in dataset["cases"]:
        markdown = [checker.SourceFile(**source) for source in case["markdown"]]
        code = [checker.SourceFile(**source) for source in case["code"]]
        items = checker.check_alignment(markdown, code)
        expected = case["expected_statuses"]
        actual = [item["status"] for item in items]
        failures = []
        if actual != expected:
            failures.append(f"Expected {expected}, received {actual}")
        sources = {source.path: source.content for source in markdown + code}
        for item in items:
            for citation in (item["doc"], item["code"]):
                if citation is None:
                    continue
                content = sources.get(citation["file"])
                if content is None or not 1 <= citation["start_line"] <= citation["end_line"] <= len(content.splitlines()):
                    failures.append("Citation does not identify a valid source range")
        splits.setdefault(case["split"], []).append({
            "id": case["id"], "passed": not failures, "expected": expected,
            "actual": actual, "failures": failures,
        })
    results = {}
    for split, cases in splits.items():
        comparisons = [
            pair for case in cases
            for pair in zip_longest(case["expected"], case["actual"])
        ]
        true_positive = sum(expected == actual == "outdated" for expected, actual in comparisons)
        predicted = sum(actual == "outdated" for _, actual in comparisons)
        expected_count = sum(expected == "outdated" for expected, _ in comparisons)
        results[split] = {
            "passed": sum(case["passed"] for case in cases), "total": len(cases),
            "mismatch_precision": true_positive / predicted if predicted else None,
            "mismatch_recall": true_positive / expected_count if expected_count else None,
            "false_mismatches": predicted - true_positive,
            "cases": cases,
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checker", type=Path, default=ROOT / "app" / "doc_alignment.py")
    parser.add_argument("--fixtures", type=Path, default=ROOT / "tests" / "fixtures" / "alignment_baseline.json")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("alignment_evaluation_checker", args.checker)
    checker = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = checker
    spec.loader.exec_module(checker)
    result = evaluate(checker, json.loads(args.fixtures.read_text(encoding="utf-8")))
    print(json.dumps(result, indent=2))
    return int(any(split["passed"] != split["total"] for split in result.values()))


if __name__ == "__main__":
    raise SystemExit(main())
