import asyncio
import json
import sys
from types import SimpleNamespace

from app.routers.drift import _behavioral_review


def test_behavioral_review_processes_one_passage_and_reports_continuation(monkeypatch):
    async def retrieve(*args, **kwargs):
        assert kwargs["kinds"] == ("code",)
        return [{
            "file": "app.py", "start_line": 3, "content": "return 202", "score": 0.9,
        }]

    prompts = []
    async def chat(**kwargs):
        prompts.append(kwargs)
        return json.dumps({"claims": [{
            "text": "The request is accepted asynchronously.",
            "verdict": "supported",
            "doc_start_line": 4,
            "doc_end_line": 4,
            "code_citations": [{"file": "app.py", "start_line": 3, "end_line": 3}],
            "explanation": "The code returns the acceptance status.",
        }]}), {}, 0

    monkeypatch.setitem(sys.modules, "app.retrieval", SimpleNamespace(retrieve_candidates=retrieve))
    monkeypatch.setattr("app.model_text.chat", chat)
    monkeypatch.setattr("app.model_text.configured", lambda workflow: True)
    monkeypatch.setattr("app.model_text.model_name", lambda workflow: "mock-model")
    rows = [
        SimpleNamespace(language="markdown", path="README.md", content="Requests are accepted asynchronously.", start_line=4),
        SimpleNamespace(language="markdown", path="README.md", content="Another passage.", start_line=8),
    ]
    items, coverage = asyncio.run(_behavioral_review("repo", rows, 0))
    assert len(prompts) == coverage["passages_processed"] == 1
    assert coverage["passages_total"] == 2 and coverage["next_offset"] == 1
    assert items[0]["status"] == "unverified"
    assert items[0]["assessment"]["verdict"] == "supported"
    assert items[0]["doc"]["start_line"] == 4
