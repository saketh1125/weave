import asyncio
import json

import pytest

from app.behavioral_alignment import MAX_INPUT_CHARS, review_passages
from app.doc_alignment import SourceFile

DOC = SourceFile("docs/guide.md", "\n\nThe service retries failed jobs.\n")
CODE = SourceFile("app/jobs.py", "\n\n\nif failed:\n    retry()\n")


def response(**changes):
    claim = {"text": "Failed jobs are retried", "verdict": "supported", "doc_start_line": 3,
             "doc_end_line": 3, "code_citations": [{"file": "app/jobs.py", "start_line": 4, "end_line": 5}],
             "explanation": "The failure branch retries the job."}
    claim.update(changes)
    return json.dumps({"claims": [claim]})


def run(raw, passages=None, evidence=None):
    async def chat(prompt):
        assert "untrusted data" in prompt and '"line": 4' in prompt
        return raw
    return asyncio.run(review_passages(passages or [DOC], evidence or [CODE], chat))


def test_supported_is_unverified_with_valid_citations():
    item = run(response())[0]
    assert item["status"] == "unverified" and item["assessment"]["verdict"] == "supported"
    assert item["doc"]["start_line"] == 3 and item["code"]["start_line"] == 4
    assert item["assessment"]["citations"] == [item["code"]]


@pytest.mark.parametrize("updates", [
    {"doc_start_line": 2}, {"doc_end_line": 4},
    {"code_citations": []},
    {"code_citations": [{"file": "invented.py", "start_line": 4, "end_line": 5}]},
    {"code_citations": [{"file": "app/jobs.py", "start_line": 3, "end_line": 5}]},
    {"code_citations": [{"file": "app/jobs.py", "start_line": 4, "end_line": 6}]},
    {"verdict": "verified"}, {"doc_start_line": "3"},
])
def test_invalid_model_output_is_coverage_not_fact(updates):
    item = run(response(**updates))[0]
    assert item["status"] == "unverified" and item["assessment"]["verdict"] == "insufficient"
    assert item["message"] == "Behavioral review could not be completed"


def test_provider_error_and_empty_claims_are_explicit():
    async def failing(prompt):
        raise RuntimeError("secret provider error")
    result = asyncio.run(review_passages([DOC], [CODE], failing))
    assert "secret" not in str(result) and result[0]["assessment"]["verdict"] == "insufficient"
    assert run('{"claims": []}')[0]["message"] == "No behavioral claims were assessed"


def test_bounds_and_context_budget_do_not_call_provider():
    async def fail(prompt):
        pytest.fail("provider called")
    with pytest.raises(ValueError):
        asyncio.run(review_passages([DOC] * 4, [CODE], fail))
    large = SourceFile("large.md", "x" * (MAX_INPUT_CHARS + 1))
    result = asyncio.run(review_passages([large], [], fail))
    assert result[0]["status"] == "unverified" and "budget" in result[0]["message"]
