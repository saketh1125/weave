"""Synthetic request-field alignment regression corpus.

The fixture is intentionally constructed and must not be read as a quality
estimate for real repositories or documentation.
"""
import hashlib
import json
from pathlib import Path

from app.doc_alignment import SourceFile
from app.request_alignment import check_request_alignment
from scripts.evaluate_request_alignment import _canonical_utf8_lf_bytes, evaluate


CORPUS = Path(__file__).parent / "fixtures" / "request_field_alignment.json"
FROZEN_CORPUS_SHA256 = "7D094E70EAE3516C6A92B77F5F6A6DE3375761BC8E986285D4AB2B90679677FF"


def _source(items):
    return [SourceFile(item["path"], item["content"]) for item in items]


def _reason_category(reason):
    if reason is None:
        return None
    lowered = reason.lower()
    if "duplicate" in lowered or "ambiguous" in lowered:
        return "duplicate_routes"
    if "alias" in lowered:
        return "alias_unsupported"
    if "inheritance" in lowered:
        return "inheritance_unsupported"
    if "dynamic" in lowered or "factory" in lowered:
        return "dynamic_default"
    if "example" in lowered:
        return "example_context"
    if "historical" in lowered:
        return "historical_context"
    if "conditional" in lowered or "qualified" in lowered:
        return "conditional_context"
    return lowered.replace(" ", "_")


def test_request_field_corpus_has_separate_development_and_evaluation_cases():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8-sig"))
    fixture_bytes = CORPUS.read_bytes()
    canonical = _canonical_utf8_lf_bytes(fixture_bytes)
    assert hashlib.sha256(canonical).hexdigest().upper() == FROZEN_CORPUS_SHA256
    assert _canonical_utf8_lf_bytes(canonical.replace(b"\n", b"\r\n")) == canonical
    cases = corpus["cases"]
    assert len(cases) >= 16
    assert {case["split"] for case in cases} == {"development", "evaluation"}
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(case["markdown"] and case["code"] for case in cases)
    assert all(
        all(item["claim_type"].startswith("request_body_") for item in
            (case["expected"] if isinstance(case["expected"], list) else [case["expected"]]))
        for case in cases
    )


def test_synthetic_request_field_corpus_expected_results():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8-sig"))
    for case in corpus["cases"]:
        actual = check_request_alignment(_source(case["markdown"]), _source(case["code"]))
        expected_items = case["expected"] if isinstance(case["expected"], list) else [case["expected"]]
        assert len(actual) == len(expected_items), case["id"]
        for expected in expected_items:
            matching = [result for result in actual if result["claim_type"] == expected["claim_type"]
                        and result["doc"] == expected["doc"]]
            assert len(matching) == 1, case["id"]
            result = matching[0]
            assert result["status"] == expected["status"], case["id"]
            assert result["code"] == expected["code"], case["id"]
            if expected["reason_category"] is None:
                assert result["context"]["reason"] is None, case["id"]
            else:
                assert _reason_category(result["context"]["reason"]) == expected["reason_category"], case["id"]


def test_provider_free_report_labels_synthetic_scope_and_splits():
    report = evaluate()
    assert report["corpus_version"] == 2
    assert report["split_counts"]["evaluation"]["cases"] == 2
    assert "Synthetic regression evidence only" in report["scope"]
    assert "accuracy" in report["scope"]
    assert report["fixture_sha256"] == FROZEN_CORPUS_SHA256.lower()
    assert report["hash_encoding"] == "UTF-8 with LF newlines"
