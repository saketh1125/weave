import asyncio
import sys
import types
import uuid
import json
from types import SimpleNamespace

from app.routers import ask as ask_router
from app.routers.ask import _rank_candidates


def test_provider_context_budget_preserves_visible_source_line_ranges():
    from app.retrieval import budget_candidates
    source = {"content": "first\nsecond\nthird", "start_line": 10, "end_line": 12}
    result = budget_candidates([source], max_chars=9, per_source_chars=9)
    assert result[0]["content"] == "first"
    assert result[0]["start_line"] == result[0]["end_line"] == 10
    assert result[0]["excerpt_truncated"] is True


def candidate(file, kind, start, end, score=0.5, qualified_name="unit", byte_range=None):
    byte_start, byte_end = byte_range or ((start - 1) * 80, end * 80)
    return {
        "file": file,
        "kind": kind,
        "start_line": start,
        "end_line": end,
        "byte_start": byte_start,
        "byte_end": byte_end,
        "score": score,
        "qualified_name": qualified_name,
        "content": "evidence",
    }


def test_broad_product_question_prioritizes_readme_and_guide_over_code():
    results = _rank_candidates(
        [
            candidate("app/core.py", "function", 1, 8, 0.72, "run"),
            candidate("README.md", "markdown", 1, 20, 0.60, "README.md#purpose"),
            candidate("docs/guide.md", "markdown", 1, 14, 0.64, "docs/guide.md#workflow"),
        ],
        "What is the use of this product for a Developer maintaining or working on a repository?",
        3,
    )
    assert {r["file"] for r in results[:2]} == {"README.md", "docs/guide.md"}
    assert all(r["source_type"] == "documentation" for r in results[:2])


def test_implementation_query_keeps_code_relevant_and_spikes_lower():
    results = _rank_candidates(
        [
            candidate("spikes/old.py", "function", 1, 9, 0.80),
            candidate("app/auth.py", "function", 1, 9, 0.72),
            candidate("README.md", "markdown", 1, 9, 0.35),
        ],
        "How is authentication implemented?",
        3,
    )
    assert results[0]["file"] == "app/auth.py"
    assert results[0]["source_type"] == "code"


def test_overlapping_same_file_chunks_are_collapsed_and_code_only_still_works():
    results = _rank_candidates(
        [
            candidate("app/core.py", "function", 1, 10, 0.80, "core.a"),
            candidate("app/core.py", "function", 8, 16, 0.75, "core.b"),
            candidate("app/other.py", "class", 1, 12, 0.70, "Other"),
        ],
        "Explain this implementation",
        5,
    )
    assert [r["file"] for r in results] == ["app/core.py", "app/other.py"]
    assert all(r["source_type"] == "code" for r in results)


def test_distinct_markdown_passages_can_share_a_heading_and_line_number():
    results = _rank_candidates(
        [
            candidate("docs/guide.md", "markdown", 5, 5, 0.8, byte_range=(0, 1400)),
            candidate("docs/guide.md", "markdown", 5, 5, 0.7, byte_range=(1160, 2560)),
            candidate("docs/guide.md", "markdown", 5, 5, 0.6, byte_range=(100, 1300)),
        ],
        "What does the guide say?",
        5,
    )
    assert [r["byte_start"] for r in results] == [0, 1160]


def test_shared_retrieval_keeps_bounded_code_pool_and_exact_symbol_search(monkeypatch):
    from app import retrieval

    embedding_options = {}

    class TextEmbedding:
        def __init__(self, model_name, cache_dir):
            embedding_options.update(model_name=model_name, cache_dir=cache_dir)

        def query_embed(self, questions):
            assert questions == ["Where is parse_value implemented?"]
            return iter([[0.1, 0.2, 0.3]])

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=TextEmbedding))
    class CachePath:
        def mkdir(self, **kwargs):
            pass

        def __str__(self):
            return "test-fastembed-cache"

    monkeypatch.setattr(retrieval, "CACHE_DIR", CachePath())

    base = {
        "kind": "function", "name": "parse_value", "qualified_name": "parser.parse_value",
        "start_line": 10, "end_line": 15, "byte_start": 100, "byte_end": 250,
        "content": "def parse_value(...): ...", "file_path": "app/parser.py", "score": 0.8,
    }

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def __init__(self):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement, params):
            self.calls.append((statement, params))
            copies = [dict(base, start_line=index, end_line=index + 3) for index in range(1, params["lim"] + 1)]
            return Result(copies[:params["lim"]])

    session = Session()
    monkeypatch.setattr(retrieval, "SessionLocal", lambda: session)
    repo_id = uuid.uuid4()
    rows = asyncio.run(retrieval.retrieve_candidates(
        repo_id,
        "Where is parse_value implemented?",
        top_k=3,
        kinds=("code",),
        symbols=("parse_value",),
    ))

    assert embedding_options["model_name"] == "BAAI/bge-small-en-v1.5"
    assert embedding_options["cache_dir"] == "test-fastembed-cache"
    assert len(session.calls) == 2  # code pool plus exact-name pool
    assert all(params["kind"] == "code" and params["lim"] == 3 for _, params in session.calls)
    assert [params["only_symbols"] for _, params in session.calls] == [False, True]
    assert len(rows) == 6
    assert set(rows[0]) == {
        "file", "kind", "name", "qualified_name", "start_line", "end_line",
        "byte_start", "byte_end", "content", "score",
    }


def test_large_ascii_and_unicode_qa_context_fits_connection_and_keeps_citations(monkeypatch):
    from app import model_connections

    ranked_rows = [
        {
            "file": "app/ascii.py", "kind": "function", "name": "ascii_fn",
            "qualified_name": "ascii_fn", "start_line": 10, "end_line": 10,
            "byte_start": 200, "byte_end": 12200, "score": 0.9,
            "content": "a" * 12000,
        },
        {
            "file": "docs/unicode.md", "kind": "markdown", "name": "unicode",
            "qualified_name": "docs/unicode.md#unicode", "start_line": 30, "end_line": 30,
            "byte_start": 500, "byte_end": 24500, "score": 0.8,
            "content": "é" * 12000,
        },
    ]
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *_):
            return SimpleNamespace(status="ready")

    class Connection:
        model = "mock-model"
        context_tokens = 20000
        tpm = 20000
        max_output_tokens = 4096

    async def retrieve(*args, **kwargs):
        return ranked_rows

    seen = {}
    async def chat(**kwargs):
        seen.update(kwargs)
        return "Grounded answer [1].", {}, 0

    monkeypatch.setattr(ask_router, "SessionLocal", lambda: Session())
    monkeypatch.setattr(ask_router, "retrieve_candidates", retrieve)
    monkeypatch.setattr("app.model_text.configured", lambda workflow: True)
    monkeypatch.setattr("app.model_text.chat", chat)
    monkeypatch.setattr(model_connections, "resolve", lambda workflow: Connection())

    response = asyncio.run(ask_router.ask_repo(
        uuid.uuid4(), ask_router.AskBody(question="Explain the relevant behavior", top_k=2),
    ))

    messages = seen["messages"]
    reserved = (len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
                + len(json.dumps([], ensure_ascii=False).encode("utf-8")) + 800)
    assert reserved <= min(Connection.context_tokens, Connection.tpm)
    assert response["answer"] == "Grounded answer [1]."
    assert len(response["citations"]) == len(response["answer_candidates"]) == 2
    assert [citation["start_line"] for citation in response["citations"]] == [10, 30]
    assert all(item["excerpt_truncated"] for item in response["answer_candidates"])
