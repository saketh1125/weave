"""Focused tests for structured provider errors in the shared LLM client."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setenv("SENTRA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SENTRA_LLM_BASE_URL", "https://provider.invalid/v1")
    monkeypatch.setenv("SENTRA_LLM_MODEL", "test-model")
    spec = importlib.util.spec_from_file_location("sentra_test_llm", Path(__file__).parents[1] / "spikes" / "_llm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.STATS.update(http_calls=0, transient_retries=0, no_usable_content=0, quota_exhausted=False)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    return module


def response(status, message, retry_after="7"):
    return SimpleNamespace(
        status_code=status,
        headers={"Retry-After": retry_after} if retry_after else {},
        text="provider body must not leak into metadata",
        json=lambda: {"error": {"message": message, "code": status}},
    )


def test_transient_429_carries_request_rate_metadata_and_retries(llm, monkeypatch):
    calls = iter([response(429, "Please slow down"), response(429, "Please slow down")])
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: next(calls))

    with pytest.raises(llm.LLMError) as caught:
        llm.chat()

    err = caught.value
    assert str(err) == "upstream_429: Please slow down"
    assert (err.status_code, err.retry_after_seconds) == (429, 7)
    assert (err.kind, err.rate_limited, err.retryable, err.hard_quota) == ("request_rate_limit", True, True, False)
    assert llm.STATS["http_calls"] == 2


def test_daily_quota_is_structured_and_keeps_legacy_message(llm, monkeypatch):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: response(429, "free-models-per-day quota exceeded"))

    with pytest.raises(llm.LLMError) as caught:
        llm.chat()

    err = caught.value
    assert str(err) == "quota_exhausted_daily: free-models-per-day quota exceeded"
    assert (err.kind, err.hard_quota, err.retryable, err.status_code) == ("daily_quota", True, False, 429)
    assert llm.STATS["quota_exhausted"] is True


def test_5xx_exhaustion_has_retry_metadata(llm, monkeypatch):
    calls = iter([response(503, "upstream unavailable", retry_after=None)] * 2)
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: next(calls))

    with pytest.raises(llm.LLMError) as caught:
        llm.chat()

    err = caught.value
    assert str(err) == "upstream_503: upstream unavailable"
    assert (err.status_code, err.kind, err.retryable) == (503, "transient_upstream", True)
    assert err.retry_after_seconds is None


def test_mistral_http_429_uses_wire_status_over_embedded_provider_code(llm, monkeypatch):
    payload = {
        "object": "error",
        "type": "rate_limited",
        "code": "1300",
        "raw_status_code": 429,
        "message": "Rate limit exceeded",
    }
    resp = SimpleNamespace(status_code=429, headers={}, text="", json=lambda: payload)
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: resp)

    with pytest.raises(llm.LLMError) as caught:
        llm.chat()

    err = caught.value
    assert (err.status_code, err.retryable, err.kind) == (429, True, "request_rate_limit")
    assert str(err) == "quota_exhausted_daily: Rate limit exceeded"


def test_http_200_embedded_string_429_is_normalized_in_metadata(llm, monkeypatch):
    resp = SimpleNamespace(
        status_code=200,
        headers={},
        text="",
        json=lambda: {"error": {"code": "429", "message": "slow down"}},
    )
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: resp)

    with pytest.raises(llm.LLMError) as caught:
        llm.chat(max_attempts=1)

    assert str(caught.value) == "http_429: slow down"
    assert (caught.value.status_code, caught.value.kind, caught.value.retryable) == (429, "request_rate_limit", True)


def test_capacity_5xx_remains_transient_not_hard_quota(llm):
    metadata = llm._error_metadata(503, "capacity temporarily unavailable", None)
    assert metadata["kind"] == "transient_upstream"
    assert metadata["retryable"] is True
    assert metadata["hard_quota"] is False


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "3"])
def test_invalid_timeout_budgets_are_rejected(llm, value):
    with pytest.raises(ValueError):
        llm.chat(timeout_seconds=value)


@pytest.mark.parametrize("value", [0, 3, 1.0, True, "1"])
def test_invalid_attempt_counts_are_rejected(llm, value):
    with pytest.raises(ValueError):
        llm.chat(max_attempts=value)


def test_mistral_large4_rate_limit_does_not_switch_models(llm, monkeypatch):
    monkeypatch.setattr(llm, "resolve_provider", lambda: "mistral")
    sent_models = []

    def post(_url, **kwargs):
        sent_models.append(kwargs["json"]["model"])
        return response(429, "Rate limit exceeded", retry_after=None)

    monkeypatch.setattr(llm.requests, "post", post)
    with pytest.raises(llm.LLMError):
        llm.chat(model=llm._MISTRAL_DEFAULT_MODEL)
    assert sent_models == ["mistral-large-4"]
    assert llm.STATS["http_calls"] == 1


def test_http_date_retry_after_is_bounded_and_legacy_runtimeerror_is_plain(llm):
    assert 0 <= llm._retry_after_seconds("Wed, 21 Oct 2099 07:28:00 GMT") <= 3600
    legacy = RuntimeError("legacy caller error")
    assert str(legacy) == "legacy caller error"
    assert not hasattr(legacy, "retry_after_seconds")
    wrapped = llm.LLMError("legacy-shaped LLM error")
    assert str(wrapped) == "legacy-shaped LLM error"
    assert wrapped.status_code is None and wrapped.retryable is None


def test_http_date_retry_after_rounds_up_to_avoid_early_retry(llm, monkeypatch):
    fixed_now = llm.datetime(2020, 1, 1, 0, 0, 0, 200000, tzinfo=llm.timezone.utc)

    class FixedDateTime:
        @staticmethod
        def now(_tz=None):
            return fixed_now

    monkeypatch.setattr(llm, "datetime", FixedDateTime)
    assert llm._retry_after_seconds("Wed, 01 Jan 2020 00:00:01 GMT") == 1


def test_opt_in_attempt_and_timeout_budget_are_honored(llm, monkeypatch):
    seen = []
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: (seen.append(k["timeout"]) or response(503, "busy")))

    with pytest.raises(llm.LLMError):
        llm.chat(max_attempts=1, timeout_seconds=2.5)

    assert seen == [2.5]
    assert llm.STATS["http_calls"] == 1
    assert llm.STATS["transient_retries"] == 0


def test_metadata_does_not_include_provider_body_or_key(llm):
    metadata = llm._error_metadata(429, "free-models-per-day key=secret-value", "invalid")
    assert metadata["kind"] == "daily_quota"
    assert "secret-value" not in repr(metadata)
    assert "provider body" not in repr(metadata)
