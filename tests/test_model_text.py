import asyncio
import json
import sys
from types import SimpleNamespace

from app import model_text


def test_chat_uses_shared_async_transport_and_returns_text(monkeypatch):
    seen = {}

    async def request(messages, tools=(), **kwargs):
        seen.update(messages=messages, tools=tools, **kwargs)
        return {"role": "assistant", "content": "answer"}, {"total_tokens": 3}

    monkeypatch.setitem(sys.modules, "app.model_quota", SimpleNamespace(request=request))
    result = asyncio.run(model_text.chat(
        workflow="qa", messages=[{"role": "user", "content": "question"}],
        max_tokens=80, temperature=0.1,
    ))

    assert result[0] == "answer"
    assert result[1] == {"total_tokens": 3}
    assert result[2] >= 0
    assert seen["workflow"] == "qa"
    assert seen["max_tokens"] == 80


def test_chat_preserves_provider_errors(monkeypatch):
    class ProviderError(Exception):
        reason = "missing_credentials"

    async def request(*args, **kwargs):
        raise ProviderError("missing_credentials")

    monkeypatch.setitem(sys.modules, "app.model_quota", SimpleNamespace(request=request))
    try:
        asyncio.run(model_text.chat(workflow="summary", messages=[]))
    except ProviderError:
        pass
    else:
        raise AssertionError("provider error should propagate")


def test_custom_workflow_connection_is_used_without_mistral_key(monkeypatch):
    connection_config = {
        "adapter": "openai_compatible", "model": "sentra-local-model",
        "endpoint": "http://127.0.0.1:9000/v1", "credential_env": "SENTRA_LOCAL_KEY",
        "allow_private": True,
    }
    monkeypatch.setenv("SENTRA_MODEL_CONNECTIONS", json.dumps({"local": connection_config}))
    monkeypatch.setenv("SENTRA_MODEL_WORKFLOWS", json.dumps({"qa": "local"}))
    monkeypatch.setenv("SENTRA_LOCAL_KEY", "mock-key")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    seen = {}

    async def request(messages, tools=(), **kwargs):
        seen.update(kwargs)
        return {"role": "assistant", "content": "local answer"}, {}

    monkeypatch.setitem(sys.modules, "app.model_quota", SimpleNamespace(request=request))
    assert model_text.configured("qa") is True
    answer, _, _ = asyncio.run(model_text.chat(workflow="qa", messages=[]))
    assert answer == "local answer"
    assert seen["connection"].model == "sentra-local-model"
    assert seen["workflow"] == "qa"


def test_chat_uses_canonical_text_content(monkeypatch):
    async def request(*args, **kwargs):
        return {"role": "assistant", "content": "visible"}, {}

    monkeypatch.setitem(sys.modules, "app.model_quota", SimpleNamespace(request=request))
    answer, _, _ = asyncio.run(model_text.chat(workflow="qa", messages=[]))
    assert answer == "visible"


def test_chat_rejects_empty_refusal_and_tool_replies(monkeypatch):
    async def request(messages, tools=(), **kwargs):
        return {"role": "assistant", "content": "", "tool_calls": [{"id": "x"}]}, {}

    monkeypatch.setitem(sys.modules, "app.model_quota", SimpleNamespace(request=request))
    try:
        asyncio.run(model_text.chat(workflow="qa", messages=[]))
    except Exception as exc:
        assert getattr(exc, "reason", None) == "invalid_response"
    else:
        raise AssertionError("invalid text completion should fail")


def test_chat_retries_transient_provider_error_with_connection_bound(monkeypatch):
    calls = []

    async def request(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise model_text.model_connections.ProviderError("rate_limited")
        return {"role": "assistant", "content": "recovered"}, {}

    monkeypatch.setitem(sys.modules, "app.model_quota", SimpleNamespace(request=request))
    answer, _, _ = asyncio.run(model_text.chat(workflow="qa", messages=[]))
    assert answer == "recovered"
    assert len(calls) == 2


def test_chat_does_not_sleep_past_remaining_call_budget(monkeypatch):
    calls = []
    connection = SimpleNamespace(model="m", max_attempts=2, timeout_seconds=0.01)

    async def request(*args, **kwargs):
        calls.append(1)
        raise model_text.model_connections.ProviderError("rate_limited", 1)

    async def sleep(_):
        raise AssertionError("retry delay must not exceed the call budget")

    monkeypatch.setitem(sys.modules, "app.model_quota", SimpleNamespace(request=request))
    monkeypatch.setattr(model_text.asyncio, "sleep", sleep)
    try:
        asyncio.run(model_text.chat(workflow="qa", connection=connection, messages=[]))
    except model_text.model_connections.ProviderError as exc:
        assert exc.reason == "rate_limited"
    else:
        raise AssertionError("request should stop when Retry-After exceeds its deadline")
    assert len(calls) == 1
