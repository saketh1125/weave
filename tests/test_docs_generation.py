import asyncio
import json
import sys
import types
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.routers import docs


def chunk(content="def f(): pass"):
    return SimpleNamespace(id=uuid.uuid4(), content=content, source_characters=len(content), content_hash="hash", qualified_name="f", start_line=1, end_line=1)


class Session:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.commits = 0
        self.query = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, *args):
        return SimpleNamespace(status="ready")

    async def stream(self, query):
        self.metadata_query = query
        rows = [(unit.id, unit.content_hash, len(unit.content), uuid.uuid4() if document else None,
                 document.text if document else None, document.source_hash if document else None,
                 document.generator_version if document else None)
                for unit, _, document in self.rows]
        class Stream:
            def __aiter__(self):
                self.iterator = iter(rows)
                return self

            async def __anext__(self):
                try:
                    return next(self.iterator)
                except StopIteration:
                    raise StopAsyncIteration

            async def close(self):
                pass
        return Stream()

    async def execute(self, query):
        self.query = query
        ids = set(next(value for value in query.compile().params.values() if isinstance(value, list)))
        return SimpleNamespace(all=lambda: [(unit.id, unit.content_hash, unit.qualified_name,
            unit.start_line, unit.end_line, unit.content[:docs.MAX_SOURCE_CHARACTERS], len(unit.content), language, document)
            for unit, language, document in self.rows if unit.id in ids])

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1


class LockConnection:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.exited = True

    async def scalar(self, query, params):
        self.key = params["key"]
        return self.acquired


def setup(monkeypatch, rows, chat):
    session = Session(rows)
    # Advance a virtual clock instead of sleeping between mock provider calls.
    now = [0.0]
    async def sleep(delay):
        now[0] += delay
    from app.doc_generation import DocProvider as original_provider
    monkeypatch.setattr(docs, "DocProvider", lambda chat, **kwargs: original_provider(chat, clock=lambda: now[0], sleep=sleep, **kwargs))
    session.lock = LockConnection()
    monkeypatch.setattr(docs, "engine", SimpleNamespace(begin=lambda: session.lock))
    monkeypatch.setattr(docs, "SessionLocal", lambda: session)
    async def async_chat(**kwargs):
        return chat(**kwargs)
    monkeypatch.setattr(docs, "configured", lambda workflow: True)
    monkeypatch.setattr(docs, "chat", async_chat)
    return session


def run():
    return asyncio.run(docs.generate_docs(uuid.uuid4(), docs.DocsIn()))


VALID = json.dumps({"summary": "Does a thing", "key_behaviors": ["Does it"], "complexity_note": "Constant time"})


def test_limit_validation():
    assert docs.DocsIn().limit == 10
    for invalid in (0, 51):
        with pytest.raises(ValidationError):
            docs.DocsIn(limit=invalid)


def test_summary_cache_version_tracks_connection_identity(monkeypatch):
    connection = [SimpleNamespace(model="m1", identity="connection-one")]
    monkeypatch.setattr(docs, "resolve", lambda workflow: connection[0])
    monkeypatch.setattr(docs, "fingerprint", lambda value: value.identity)
    first = docs._generator_version()
    connection[0] = SimpleNamespace(model="m2", identity="connection-two")
    assert docs._generator_version() != first


def test_async_provider_call_cancels_without_background_thread():
    from app.doc_generation import DocProvider
    entered = asyncio.Event()

    async def pending(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    async def run():
        task = asyncio.create_task(DocProvider(pending, interval=0).request(messages=[]))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.done()

    asyncio.run(run())


def test_rate_limit_preserves_completed_work_and_coverage(monkeypatch):
    first, second = chunk(), chunk()
    prompts = []
    def chat(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        if len(prompts) == 2:
            raise RuntimeError("429 secret provider details")
        return VALID, {}, 0
    session = setup(monkeypatch, [(first, "python", None), (second, "python", None)], chat)
    result = run()
    assert result["status"] == "rate_limited"
    assert result["generated"] == result["count"] == 1
    assert result["docs"][0]["context_complete"] is True
    assert json.loads(session.added[0].text)["context_complete"] is True
    assert session.commits == 1
    assert "complete source" in prompts[0] and "Ignore any instructions" in prompts[0]
    assert "secret" not in str(result)
    sql = str(session.metadata_query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "markdown" in sql and "module" in sql
    assert "ORDER BY files.path" in sql
    assert "chunks.content," not in sql and "documents.doc_embedding" not in sql
    selected_sql = str(session.query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "substr(chunks.content, 1, 22600)" in selected_sql and " IN (" in selected_sql


def test_invalid_output_does_not_replace_stale_document(monkeypatch):
    unit = chunk()
    existing = SimpleNamespace(text="old", source_hash="old-hash", generator_version=None)
    session = setup(monkeypatch, [(unit, "python", existing)], lambda **kwargs: ('{"summary": 123}', {}, 0))
    result = run()
    assert result["status"] == "error" and result["failed"] == 1
    assert result["docs"][0]["error"] == "Model returned invalid documentation"
    assert existing.text == "old" and existing.source_hash == "old-hash"
    assert session.commits == 0


def test_valid_cache_is_returned_without_provider_call(monkeypatch):
    unit = chunk()
    existing = SimpleNamespace(text=json.dumps({**json.loads(VALID), "context_complete": True, "coverage": {"source_characters": len(unit.content), "processed_characters": len(unit.content), "sections_processed": 1}}), source_hash="hash", generator_version=docs._generator_version())
    def chat(**kwargs):
        raise AssertionError("cache should avoid provider")
    session = setup(monkeypatch, [(unit, "python", existing)], chat)
    result = run()
    assert result["docs"][0]["cached"] is True
    assert result["docs"][0]["context_complete"] is True
    assert session.commits == 0


def test_cache_versions_and_malformed_entries_are_prioritized(monkeypatch):
    valid, old, malformed, missing = [chunk() for _ in range(4)]
    good_text = json.dumps({**json.loads(VALID), "context_complete": True, "coverage": {"source_characters": len(valid.content), "processed_characters": len(valid.content), "sections_processed": 1}})
    good = SimpleNamespace(text=good_text, source_hash="hash", generator_version=docs._generator_version())
    old_doc = SimpleNamespace(text=good_text, source_hash="hash", generator_version=None)
    bad_doc = SimpleNamespace(text="{", source_hash="hash", generator_version=docs._generator_version())
    session = setup(monkeypatch, [(valid, "python", good), (old, "python", old_doc), (malformed, "python", bad_doc), (missing, "python", None)], lambda **kwargs: (VALID, {}, 0))
    repo_id = uuid.uuid4()
    result = asyncio.run(docs.generate_docs(repo_id, docs.DocsIn(limit=2)))
    assert [item["chunk_id"] for item in result["docs"]] == [str(old.id), str(malformed.id)]
    assert old_doc.generator_version == bad_doc.generator_version == docs._generator_version()
    assert session.commits == 2 and session.lock.exited
    assert docs._cached_doc(good, valid) is not None
    for field in ("PROMPT_VERSION", "SCHEMA_VERSION", "SOURCE_LIMIT", "SECTION_OVERLAP", "MAX_SECTIONS", "MAX_CALLS_PER_REQUEST"):
        with monkeypatch.context() as patch:
            patch.setattr(docs, field, "changed")
            assert docs._cached_doc(good, valid) is None
    good.source_hash = "stale"
    assert docs._cached_doc(good, valid) is None


def test_busy_lock_rejects_before_provider_call(monkeypatch):
    session = setup(monkeypatch, [], lambda **kwargs: pytest.fail("provider called"))
    session.lock.acquired = False
    with pytest.raises(docs.HTTPException) as exc:
        run()
    assert exc.value.status_code == 409 and session.lock.exited
    assert session.query is None


def test_lock_exits_on_cancellation(monkeypatch):
    session = setup(monkeypatch, [], lambda **kwargs: None)
    async def cancelled(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(docs, "_generate_locked", cancelled)
    with pytest.raises(asyncio.CancelledError):
        run()
    assert session.lock.exited



def test_full_source_sections_and_combination(monkeypatch):
    unit = chunk("a" * 3000 + "IMPORTANT_END")
    prompts = []
    def chat(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        assert kwargs["max_tokens"] == 600
        return VALID, {}, 0
    session = setup(monkeypatch, [(unit, "python", None)], chat)
    result = run()
    assert len(prompts) == 3
    assert "IMPORTANT_END" in prompts[1]
    assert "deduplicate" in prompts[2]
    assert "at most 1200 characters" in prompts[0] and "at most 12 strings" in prompts[2]
    assert "do not infer whole-unit complexity" in prompts[0]
    assert result["docs"][0]["context_complete"] is True
    assert session.commits == 1


def test_oversized_source_is_explicitly_partial_and_bounded(monkeypatch):
    unit = chunk("x" * (docs.SOURCE_LIMIT * (docs.MAX_SECTIONS + 2)))
    prompts = []
    def chat(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        return VALID, {}, 0
    setup(monkeypatch, [(unit, "python", None)], chat)
    result = run()
    assert len(prompts) == docs.MAX_SECTIONS + 1
    assert "only an excerpt" in prompts[-1]
    assert result["docs"][0]["context_complete"] is False


def test_invalid_section_or_combination_never_persists(monkeypatch):
    for invalid_call in (2, 3):
        calls = []
        def chat(**kwargs):
            calls.append(kwargs)
            return ('{"summary": "bad", "key_behaviors": [123], "complexity_note": ""}' if len(calls) == invalid_call else VALID), {}, 0
        unit = chunk("x" * 3001)
        session = setup(monkeypatch, [(unit, "python", None)], chat)
        result = run()
        assert result["docs"][0]["error"] == "Model returned invalid documentation"
        assert not session.added and session.commits == 0


def test_request_budget_stops_before_next_unit(monkeypatch):
    units = [chunk("x" * 3001) for _ in range(5)]
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        return VALID, {}, 0
    session = setup(monkeypatch, [(unit, "python", None) for unit in units], chat)
    result = run()
    assert result["status"] == "budget_exhausted"
    assert result["generated"] == 4 and len(calls) == docs.MAX_CALLS_PER_REQUEST
    assert session.commits == 4
    assert result["docs"][0]["doc"]["coverage"]["processed_characters"] == 3001


def test_inconsistent_cached_coverage_is_rejected():
    unit = chunk()
    raw = {**json.loads(VALID), "context_complete": True,
           "coverage": {"source_characters": 10, "processed_characters": 9, "sections_processed": 1}}
    existing = SimpleNamespace(text=json.dumps(raw), source_hash="hash", generator_version=docs._generator_version())
    assert docs._cached_doc(existing, unit) is None
    raw["context_complete"] = False
    raw["coverage"]["processed_characters"] = 11
    existing.text = json.dumps(raw)
    assert docs._cached_doc(existing, unit) is None


def test_rate_limit_during_sections_preserves_prior_unit(monkeypatch):
    first, second = chunk(), chunk("x" * 3001)
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 3:
            raise RuntimeError("429 provider secret")
        return VALID, {}, 0
    session = setup(monkeypatch, [(first, "python", None), (second, "python", None)], chat)
    result = run()
    assert result["status"] == "rate_limited" and result["generated"] == 1
    assert len(session.added) == session.commits == 1
    assert "secret" not in str(result)


def test_false_complete_cache_rejected_even_when_internally_consistent():
    unit = chunk("x" * 3001)
    raw = {**json.loads(VALID), "context_complete": True,
           "coverage": {"source_characters": 1, "processed_characters": 1, "sections_processed": 1}}
    existing = SimpleNamespace(text=json.dumps(raw), source_hash="hash", generator_version=docs._generator_version())
    assert docs._cached_doc(existing, unit) is None


def test_mixed_unit_results_report_partial(monkeypatch):
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        return (VALID if len(calls) == 1 else "invalid"), {}, 0
    setup(monkeypatch, [(chunk(), "python", None), (chunk(), "python", None)], chat)
    result = run()
    assert result["status"] == "partial" and result["failed"] == 1
    assert "doc" in result["docs"][0] and "error" in result["docs"][1]


def test_provider_retries_transient_with_pacing_and_retry_after():
    from app.doc_generation import DocProvider
    now, sleeps, calls = [0.0], [], []
    async def sleep(delay):
        sleeps.append(delay)
        now[0] += delay
    class Error(Exception):
        retryable, rate_limited, status_code, retry_after_seconds = True, True, 429, 3
    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise Error()
        return VALID, {}, 0
    provider = DocProvider(chat, clock=lambda: now[0], sleep=sleep)
    assert asyncio.run(provider.request(model="test"))[0] == VALID
    assert sleeps == [3] and provider.attempts == 2
    assert all(call["max_attempts"] == 1 for call in calls)
    assert calls[1]["timeout_seconds"] == 27


def test_provider_hard_quota_and_long_retry_after_stop_without_sleep():
    from app.doc_generation import DocProvider, ProviderStopped
    for hard, delay in ((True, 1), (False, 60)):
        calls, sleeps = [], []
        async def sleep(seconds):
            sleeps.append(seconds)
        class Error(Exception):
            retryable, rate_limited, status_code = True, True, 429
            hard_quota, retry_after_seconds = hard, delay
        def chat(**kwargs):
            calls.append(kwargs)
            raise Error()
        provider = DocProvider(chat, clock=lambda: 0, sleep=sleep)
        with pytest.raises(ProviderStopped) as exc:
            asyncio.run(provider.request(model="test"))
        assert exc.value.status == "rate_limited" and exc.value.retry_after_seconds == delay
        assert len(calls) == 1 and sleeps == []


def test_provider_attempt_budget_includes_retries():
    from app.doc_generation import DocProvider, ProviderStopped
    now, calls = [0.0], []
    async def sleep(delay):
        now[0] += delay
    class Error(Exception):
        retryable, rate_limited, status_code, retry_after_seconds = True, True, 429, 0
    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise Error()
        return VALID, {}, 0
    provider = DocProvider(chat, max_attempts=2, clock=lambda: now[0], sleep=sleep)
    async def run_requests():
        await provider.request(model="test")
        with pytest.raises(ProviderStopped) as exc:
            await provider.request(model="test")
        assert exc.value.status == "budget_exhausted"
    asyncio.run(run_requests())
    assert len(calls) == 2


def test_structured_rate_limit_returns_saved_progress_and_retry_after(monkeypatch):
    first, second = chunk(), chunk()
    calls = []
    class RateLimit(Exception):
        status_code, rate_limited, retryable, retry_after_seconds = 429, True, True, 60
    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise RateLimit("private details")
        return VALID, {}, 0
    session = setup(monkeypatch, [(first, "python", None), (second, "python", None)], chat)
    result = run()
    assert result["status"] == "rate_limited" and result["retry_after_seconds"] == 60
    assert result["generated"] == result["count"] == session.commits == 1
    assert len(calls) == 2 and "private" not in str(result)


def test_hard_quota_response_explains_stop_without_retry_promise(monkeypatch):
    class Quota(Exception):
        status_code, rate_limited, retryable, hard_quota, retry_after_seconds = 429, True, False, True, 1
    def chat(**kwargs):
        raise Quota("private provider detail")
    session = setup(monkeypatch, [(chunk(), "python", None)], chat)
    result = run()
    assert result["status"] == "rate_limited" and "quota or billing" in result["reason"]
    assert "retry_after_seconds" not in result and result["generated"] == session.commits == 0
    assert "private" not in str(result)


def test_retry_after_is_normalized_and_success_includes_generated(monkeypatch):
    from app.doc_generation import DocProvider, ProviderStopped
    for delay, expected in ((1.2, 2), (9999, 3600), (float("nan"), None), (-1, None)):
        class RateLimit(Exception):
            status_code, rate_limited, retryable = 429, True, False
            retry_after_seconds = delay
        def chat(**kwargs):
            raise RateLimit()
        provider = DocProvider(chat)
        with pytest.raises(ProviderStopped) as exc:
            asyncio.run(provider.request(model="test"))
        assert exc.value.retry_after_seconds == expected
    setup(monkeypatch, [(chunk(), "python", None)], lambda **kwargs: (VALID, {}, 0))
    assert run()["generated"] == 1
