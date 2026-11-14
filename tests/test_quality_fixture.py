import ast
import json
from pathlib import Path

import pytest

from scripts.evaluate_quality import _safe_error, build_manifest, load_fixture

FIXTURE = Path(__file__).parent / "fixtures" / "documentation_quality.json"


def _source_text(source):
    if "content" in source:
        return source["content"]
    recipe = source["construction"]
    return (
        recipe["prefix"]
        + recipe["repeat_text"] * recipe["repeat_count"]
        + recipe["suffix"]
    )


def test_documentation_quality_fixture_is_self_consistent():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = data["cases"]
    assert data["version"] == 1
    assert len(cases) == 8
    assert len({case["id"] for case in cases}) == len(cases)

    for case in cases:
        sources = case["sources"]
        paths = [source["path"] for source in sources]
        assert len(paths) == len(set(paths)), case["id"]
        by_path = {source["path"]: _source_text(source) for source in sources}
        assert case["expected_facts"] is not None
        assert case["forbidden_claims"]

        for citation in case["citations"]:
            assert citation["path"] in by_path, case["id"]
            line_count = len(by_path[citation["path"]].splitlines())
            assert 1 <= citation["start_line"] <= citation["end_line"] <= line_count, case["id"]

    late = next(case for case in cases if case["id"] == "summary-fact-after-source-boundary")
    source = _source_text(late["sources"][0])
    citation = late["citations"][0]
    lines = source.splitlines()
    assert len(source) > 3000
    assert source.index("return 9") > 3000
    assert lines[citation["start_line"] - 1].strip() == "return 9"
    ast.parse(source)

    excerpt_case = next(case for case in cases if case["id"] == "summary-evaluator-excerpt")
    excerpt = excerpt_case["evaluation_context"]["content"]
    full_source = _source_text(excerpt_case["sources"][0])
    assert "return 7" in full_source
    assert "return 7" not in excerpt


def test_provider_free_manifest_expands_sources_and_has_no_scores():
    fixture, digest = load_fixture()
    manifest = build_manifest(fixture, digest)
    late = next(case for case in manifest["cases"] if case["id"] == "summary-fact-after-source-boundary")
    source = late["sources"][0]["content"]
    assert len(source) > 3000 and "return 9" in source
    assert manifest["fixture_sha256"] == digest
    assert manifest["quality_scores"] is None
    assert len(manifest["cases"]) == 8


def test_fixture_recipe_has_a_repeat_bound():
    fixture, _ = load_fixture()
    case = next(case for case in fixture["cases"] if case["id"] == "summary-fact-after-source-boundary")
    case["sources"][0]["construction"]["repeat_count"] = 10001
    with pytest.raises(ValueError, match="repeat_count exceeds fixture bound"):
        build_manifest(fixture, "unused")


def test_provider_error_details_are_not_written_to_quality_artifacts():
    error = _safe_error(RuntimeError("429 private-token-without-a-recognizable-prefix"))
    assert error == {"type": "RuntimeError", "reason": "provider_rate_or_quota_limit"}


def test_live_runner_uses_single_attempts_and_stops_at_shared_budget(monkeypatch):
    from app import doc_generation
    from app import model_text
    from scripts.evaluate_quality import run_live

    calls = []
    monkeypatch.setattr(model_text, "configured", lambda workflow: True)

    async def chat(**kwargs):
        calls.append(kwargs)
        return json.dumps({"summary": "Synthetic", "key_behaviors": [], "complexity_note": ""}), {}, 0

    monkeypatch.setattr(model_text, "chat", chat)
    scheduler = doc_generation.DocProvider
    monkeypatch.setattr(doc_generation, "DocProvider", lambda *args, **kwargs: scheduler(*args, **kwargs, interval=0))
    fixture, digest = load_fixture()
    result = run_live(fixture, digest, max_calls=1)
    assert result["provider_call_count"] == len(calls) == 1
    assert calls[0]["max_attempts"] == 1
    assert result["results"][0]["error"]["reason"] == "call_budget_exhausted"
