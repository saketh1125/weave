from types import SimpleNamespace

from app.documentation import model, drafts


def test_review_settings_detects_model_and_prompt_changes(monkeypatch):
    monkeypatch.delenv("SENTRA_MODEL_CONNECTIONS", raising=False)
    monkeypatch.delenv("SENTRA_MODEL_WORKFLOWS", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY2", raising=False)
    monkeypatch.delenv("SENTRA_DOCUMENTATION_MODEL", raising=False)
    saved = model.review_settings()
    run = SimpleNamespace(model="mistral-large-4", manifest={"model_settings": saved})
    assert model.compatible_review(run, saved)
    monkeypatch.setenv("SENTRA_DOCUMENTATION_MODEL", "different-model")
    assert not model.compatible_review(run, model.review_settings())


def test_old_reviewer_checkpoints_are_incompatible_without_removing_assessments(monkeypatch):
    monkeypatch.delenv("SENTRA_MODEL_CONNECTIONS", raising=False)
    monkeypatch.delenv("SENTRA_MODEL_WORKFLOWS", raising=False)
    current = model.review_settings()
    assessment = {"claim": "previously saved"}
    for settings in (None, {**current, "reviewer_version": 1}):
        manifest = {} if settings is None else {"model_settings": settings}
        run = SimpleNamespace(model=model.DEFAULT_MODEL, manifest=manifest, assessments=[assessment])
        assert not model.compatible_review(run, current)
        assert run.assessments == [assessment]


def test_draft_generation_identity_changes_with_prompt(monkeypatch):
    before = drafts.generation_settings()
    monkeypatch.setattr(drafts, "DRAFT_PROMPT", drafts.DRAFT_PROMPT + " changed")
    assert before != drafts.generation_settings()


@__import__("pytest").mark.asyncio
async def test_shared_quota_group_uses_same_durable_row(monkeypatch):
    import pytest
    from app import db, model_connections
    from app.documentation import quota
    from app import model_quota
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def no_slot(connection):
        yield
    monkeypatch.setattr(model_quota, "_concurrency_slot", no_slot)
    from sqlalchemy import Column, String
    from sqlalchemy.orm import declarative_base
    from dataclasses import replace
    import sys
    base = declarative_base()
    class Quota(base):
        __tablename__ = "test_quota"
        id = Column(String, primary_key=True)
    monkeypatch.setitem(sys.modules, "app.models", SimpleNamespace(DocumentationProviderQuota=Quota))
    row = SimpleNamespace(window_start=0.0, next_call_at=0.0, blocked_until=0.0, reserved_requests=0, reserved_tokens=0)
    keys = []
    class Result:
        def scalar_one(self): return row
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, statement):
            if statement.__visit_name__ == "select":
                keys.append(statement.compile().params["id_1"])
            return Result()
        async def commit(self): pass
    monkeypatch.setattr(db, "SessionLocal", Session)
    async def complete(*args, **kwargs): return {"role": "assistant"}, {}
    monkeypatch.setattr(__import__("app.model_connections", fromlist=["complete"]), "complete", complete)
    first = replace(model_connections.resolve("reviewer"), quota_group="shared", interval=0)
    second = replace(first, name="other", model="another")
    await quota.request([], [], model_name=first.model, max_tokens=1, connection=first)
    await quota.request([], [], model_name=second.model, max_tokens=1, connection=second)
    assert len(keys) == 2 and keys[0] == keys[1] and len(keys[0]) == 64
    assert row.reserved_requests == 2


def test_durable_shared_group_and_cancellation(monkeypatch):
    import asyncio
    import os
    import uuid
    import pytest
    from dataclasses import replace
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.engine import make_url
    from sqlalchemy import delete
    from app import db, model_connections, model_quota
    from app.documentation import quota
    url = os.environ.get("SENTRA_TEST_DATABASE_URL")
    if not url: pytest.skip("SENTRA_TEST_DATABASE_URL is not configured")
    from app.models import DocumentationProviderQuota
    async def run():
        engine = create_async_engine(make_url(url).set(drivername="postgresql+asyncpg"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(db, "engine", engine)
        monkeypatch.setattr(db, "SessionLocal", sessions)
        group = "test-" + uuid.uuid4().hex
        one = replace(model_connections.resolve("reviewer"), quota_group=group, interval=0, rpm=2)
        two = replace(one, name="other", credential_env="OTHER_KEY")
        row_id = "q:" + __import__("hashlib").sha256(group.encode()).hexdigest()[:62]
        started = asyncio.Event()
        async def slow(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(__import__("app.model_connections", fromlist=["complete"]), "complete", slow)
        try:
            first = asyncio.create_task(quota.request([], [], model_name=one.model, max_tokens=1, connection=one))
            await asyncio.wait_for(started.wait(), 5)
            with pytest.raises(model.ProviderError) as busy:
                await model_quota.request([], [], model_name=two.model, max_tokens=1, connection=two, workflow="qa")
            assert busy.value.reason == "rate_limited"
            first.cancel()
            with pytest.raises(asyncio.CancelledError): await first
            # Advisory lock is released even when the provider call is cancelled.
            async with model_quota._concurrency_slot(two): pass
            # Both workflows reserved against the same durable row and exhaust its RPM.
            with pytest.raises(model.ProviderError) as limited:
                await quota.request([], [], model_name=one.model, max_tokens=1, connection=one)
            assert limited.value.reason == "rate_limited"
            async with sessions() as session:
                row = await session.get(DocumentationProviderQuota, row_id, with_for_update=True)
                row.reserved_requests = row.reserved_tokens = 0
                row.next_call_at = row.blocked_until = 0
                await session.commit()
            async def throttled(*args, **kwargs):
                raise model.ProviderError("rate_limited", 30)
            monkeypatch.setattr(model_connections, "complete", throttled)
            with pytest.raises(model.ProviderError):
                await quota.request([], [], model_name=one.model, max_tokens=1, connection=one)
            with pytest.raises(model.ProviderError) as blocked:
                await model_quota.request([], [], model_name=two.model, max_tokens=1, connection=two, workflow="qa")
            assert blocked.value.reason == "rate_limited" and not blocked.value.request_sent
        finally:
            async with sessions() as session:
                await session.execute(delete(DocumentationProviderQuota).where(DocumentationProviderQuota.id == row_id))
                await session.commit()
            await engine.dispose()
    asyncio.run(run())


def test_same_model_different_adapter_is_incompatible(monkeypatch):
    import json
    monkeypatch.delenv("SENTRA_MODEL_CONNECTIONS", raising=False)
    monkeypatch.delenv("SENTRA_MODEL_WORKFLOWS", raising=False)
    monkeypatch.delenv("SENTRA_DOCUMENTATION_MODEL", raising=False)
    saved = model.review_settings()
    run = SimpleNamespace(model="mistral-large-4", manifest={"model_settings": saved})
    monkeypatch.setenv("SENTRA_MODEL_CONNECTIONS", json.dumps({"alternative": {"adapter": "openai_compatible", "model": "mistral-large-4", "endpoint": "https://example.com/v1", "credential_env": "OTHER_KEY", "capabilities": ["tools"]}}))
    monkeypatch.setenv("SENTRA_MODEL_WORKFLOWS", json.dumps({"reviewer": "alternative"}))
    assert not model.compatible_review(run, model.review_settings())


def test_draft_settings_are_bound_into_digest(monkeypatch):
    first = drafts.generation_settings()
    original = [{"path": "README.md", "generation_settings": first}]
    before = drafts.digest({"base_commit": "abc", "files": original, "diff": "diff"})
    monkeypatch.setattr(drafts, "DRAFT_PROMPT", drafts.DRAFT_PROMPT + " changed")
    updated = [{"path": "README.md", "generation_settings": drafts.generation_settings()}]
    assert drafts.digest({"base_commit": "abc", "files": updated, "diff": "diff"}) != before


def test_review_limits_are_bounded_and_bind_existing_runs(monkeypatch):
    import pytest
    monkeypatch.delenv("SENTRA_DOCUMENTATION_MAX_RUN_CALLS", raising=False)
    monkeypatch.delenv("SENTRA_DOCUMENTATION_MAX_RUN_TOKENS", raising=False)
    saved = model.review_settings()
    assert saved["limits"] == {"calls": 200, "tokens": 16_000_000}
    run = SimpleNamespace(model=model.DEFAULT_MODEL, manifest={"model_settings": saved})
    legacy = SimpleNamespace(model=model.DEFAULT_MODEL, manifest={"model_settings": {k: v for k, v in saved.items() if k != "limits"}})
    assert model.compatible_review(legacy, saved)
    monkeypatch.setenv("SENTRA_DOCUMENTATION_MAX_RUN_CALLS", "500")
    assert model.review_settings()["limits"]["calls"] == 500
    assert not model.compatible_review(run, model.review_settings())
    assert not model.compatible_review(legacy, model.review_settings())
    for invalid in ("0", "100001", "-1", "bad"):
        monkeypatch.setenv("SENTRA_DOCUMENTATION_MAX_RUN_CALLS", invalid)
        with pytest.raises(model.ProviderError, match="invalid_configuration"):
            model.review_limits()
    monkeypatch.setenv("SENTRA_DOCUMENTATION_MAX_RUN_CALLS", "1")
    for invalid in ("0", "1000000001", "bad"):
        monkeypatch.setenv("SENTRA_DOCUMENTATION_MAX_RUN_TOKENS", invalid)
        with pytest.raises(model.ProviderError, match="invalid_configuration"):
            model.review_limits()
