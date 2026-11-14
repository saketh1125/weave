"""Small application-facing adapter for shared model connections."""
from __future__ import annotations

import time
import asyncio

from app import model_connections
from app.model_connections import configured, resolve


async def chat(*, workflow: str, messages, tools=(), connection=None, slot=1,
               model=None, max_tokens=1024, temperature=None,
               response_format=None, force_assessment=False, timeout_seconds=None,
               max_attempts=None):
    """Return the legacy text/usage/latency tuple using the shared async client."""
    started = time.monotonic()
    connection = connection or resolve(workflow, slot=slot)
    from app.model_quota import request
    attempts = max_attempts if max_attempts is not None else connection.max_attempts
    if type(attempts) is not int or not 1 <= attempts <= connection.max_attempts:
        raise model_connections.ProviderError("invalid_request", request_sent=False)
    call_budget = min(connection.timeout_seconds, timeout_seconds) if timeout_seconds is not None else connection.timeout_seconds
    for attempt in range(attempts):
        remaining = call_budget - (time.monotonic() - started)
        if remaining <= 0:
            raise model_connections.ProviderError("provider_timeout")
        try:
            message, usage = await request(
                messages, tools, connection=connection, workflow=workflow,
                model_name=model or connection.model, max_tokens=max_tokens,
                temperature=temperature, response_format=response_format,
                timeout_seconds=remaining, force_assessment=force_assessment,
            )
            break
        except model_connections.ProviderError as exc:
            if exc.reason == "provider_timeout":
                exc.reason = "timeout"
                exc.args = ("timeout",)
            exc.rate_limited = exc.reason == "rate_limited"
            if not exc.retryable or attempt + 1 >= attempts:
                raise
            if exc.retry_after_seconds is not None:
                remaining = call_budget - (time.monotonic() - started)
                if exc.retry_after_seconds >= remaining:
                    raise
                await asyncio.sleep(exc.retry_after_seconds)
    if not isinstance(message, dict) or message.get("role") not in (None, "assistant"):
        raise model_connections.ProviderError("invalid_response")
    if message.get("refusal") or message.get("tool_calls") or message.get("function_call"):
        raise model_connections.ProviderError("invalid_response")
    content = message.get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise model_connections.ProviderError("invalid_response")
    return content, usage, time.monotonic() - started


def model_name(workflow: str, slot: int = 1) -> str:
    return resolve(workflow, slot=slot).model


__all__ = ["chat", "configured", "model_name", "resolve"]
