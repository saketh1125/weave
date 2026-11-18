"""Authenticated local agent bridge client; API containers never receive Docker access."""
import asyncio
import json
import os
import re
from urllib.parse import urlsplit

import httpx

from app.model_connections import ProviderError


def review_timeout_seconds():
    value = os.environ.get("SENTRA_ANTIGRAVITY_REVIEW_TIMEOUT_SECONDS", "600")
    if not value.isascii() or not value.isdecimal() or len(value) > 3 or not 60 <= int(value) <= 900:
        raise ProviderError("invalid_agent_configuration", request_sent=False)
    return int(value)


def workflow_prompt_budget():
    value = os.environ.get("SENTRA_NATIVE_MAX_PROMPT_CALLS", "18")
    if not value.isascii() or not value.isdecimal() or len(value) > 3 or not 1 <= int(value) <= 100:
        raise ProviderError("invalid_agent_configuration", request_sent=False)
    return int(value)


def static_triage_threshold():
    value = os.environ.get("SENTRA_DOCUMENTATION_STATIC_TRIAGE_THRESHOLD", "20")
    if not value.isascii() or not value.isdecimal() or len(value) > 5 or not 1 <= int(value) <= 10000:
        raise ProviderError("invalid_agent_configuration", request_sent=False)
    return int(value)


def configuration():
    if os.environ.get("SENTRA_ANTIGRAVITY_ENABLED", "").lower() != "true":
        return None
    url = os.environ.get("SENTRA_AGENT_BRIDGE_URL", "http://host.docker.internal:9218")
    parsed = urlsplit(url)
    token = os.environ.get("SENTRA_AGENT_BRIDGE_TOKEN", "")
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"}
            or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or not parsed.port or not 1024 <= parsed.port <= 65535 or len(token) < 32
            or any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise ProviderError("invalid_agent_configuration", request_sent=False)
    return url.rstrip("/"), token


def unavailable(message="The local Antigravity bridge is not enabled."):
    return {"enabled": False, "status": "unavailable", "message": message, "models": [],
            "selected_model": None, "verified": False}


def public_state(data):
    """Allowlist metadata; no native output, URLs, or credential references."""
    messages = {"model_limit_reported": "The native runtime reported a model usage limit. Check the model allowance before retrying.", "passed": "Live documentation fixture passed.",
        "not_tested": "Run a connection test using the invented documentation fixture.",
        "authentication_required": "Complete native Antigravity setup in the server terminal, then test again.",
        "failed": "The native review did not pass. Check the local runtime and provider availability."}
    status = data.get("status")
    if status not in messages:
        return unavailable("The local Antigravity connection is unavailable.")
    models = data.get("models", [])
    if not isinstance(models, list) or len(models) > 100 or any(not isinstance(m, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", m) for m in models):
        return unavailable("The local Antigravity connection returned invalid model metadata.")
    identity, selected = data.get("identity"), data.get("selected_model")
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
        return unavailable("The local Antigravity connection returned invalid runtime metadata.")
    return {"enabled": True, "status": status, "message": messages[status], "models": models,
        "selected_model": selected if selected in models else None, "identity": identity,
        "verified": data.get("verified") is True and status == "passed" and selected in models}


async def _json(client, method, url, *, max_bytes=100_000, **kwargs):
    async with client.stream(method, url, **kwargs) as response:
        if response.status_code == 409:
            raise ProviderError("agent_busy", request_sent=False)
        if response.status_code != 200:
            raise ProviderError("agent_bridge_unavailable", request_sent=False)
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > max_bytes:
                raise ProviderError("invalid_agent_result")
    try:
        result = json.loads(raw)
    except ValueError:
        raise ProviderError("invalid_agent_result") from None
    if not isinstance(result, dict):
        raise ProviderError("invalid_agent_result")
    return result


def _client(token):
    return httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, trust_env=False,
        follow_redirects=False, timeout=httpx.Timeout(10, connect=3))


async def status():
    try:
        config = configuration()
        if config is None:
            return unavailable()
        url, token = config
        async with _client(token) as client:
            return public_state(await _json(client, "GET", url + "/status"))
    except (ProviderError, httpx.HTTPError, ValueError):
        return unavailable("Start the local Antigravity bridge on the server to connect.")


async def _run(payload, on_progress=None):
    config = configuration()
    if config is None:
        raise ProviderError("agent_bridge_unavailable", request_sent=False)
    url, token = config
    async with _client(token) as client:
        job_id = None
        last_progress = None
        try:
            started = await _json(client, "POST", url + "/jobs", json=payload)
            job_id = started.get("id")
            if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
                raise ProviderError("invalid_agent_result")
            timeout = payload['deadline_seconds'] + (360 if payload.get('kind') == 'workflow' else 100) if payload.get('kind') in ('review', 'workflow') else (payload['timeout_seconds'] + 60 if payload.get('kind') == 'generate' else 160)
            async with asyncio.timeout(timeout):
                while True:
                    result = await _json(client, "GET", url + "/jobs/" + job_id,
                        max_bytes=11_000_000 if payload.get("kind") == "workflow" else 100_000)
                    if result.get("status") != "running":
                        return result
                    if on_progress is not None and result.get('progress') is not None and result['progress'] != last_progress:
                        await on_progress(result['progress'])
                        last_progress = result['progress']
                    await asyncio.sleep(1)
        except TimeoutError:
            raise ProviderError("agent_timeout") from None
        except httpx.HTTPError:
            raise ProviderError("agent_bridge_unavailable") from None
        finally:
            if job_id:
                try:
                    await asyncio.shield(_json(client, "DELETE", url + "/jobs/" + job_id))
                except (ProviderError, httpx.HTTPError):
                    pass


async def test(model=None):
    try:
        return public_state(await _run({"kind": "test", "model": model}))
    except ProviderError:
        return unavailable("The local Antigravity test could not complete. Check the server connection.")


async def review(snapshot, section, model, expected_identity=None, on_progress=None):
    state = await status()
    if not expected_identity or state.get("identity") != expected_identity:
        raise ProviderError("agent_configuration_changed", request_sent=False)
    if not state.get("verified") or model not in state.get("models", []):
        raise ProviderError("agent_connection_unverified", request_sent=False)
    job = {"snapshot": snapshot, "section": {k: section[k] for k in ("id", "path", "start_line", "end_line", "content")},
           "limits": {"max_steps": 64, "max_output_bytes": 65536}}
    if len(json.dumps(job).encode()) > 50 * 1024 * 1024:
        raise ProviderError("agent_snapshot_too_large", request_sent=False)
    return await _run({"kind": "review", "model": model, "identity": expected_identity, "job": job,
        "deadline_seconds": review_timeout_seconds(),
        "context": {k: section[k] for k in ("heading_context", "source_coverage", "reviewed_claims") if k in section}}, on_progress)


async def workflow(snapshot, documentation_files, model, expected_identity, *, plan=None,
                   completed_files=(), reviewed_claims=None, static_review=None, max_prompt_calls=None, completed_triage_ids=(), on_progress=None):
    state = await status()
    if not expected_identity or state.get("identity") != expected_identity:
        raise ProviderError("agent_configuration_changed", request_sent=False)
    if not state.get("verified") or model not in state.get("models", []):
        raise ProviderError("agent_connection_unverified", request_sent=False)
    job = {"snapshot": snapshot, "documentation_files": sorted(documentation_files),
           "plan": plan, "completed_files": list(completed_files), "reviewed_claims": reviewed_claims or {}, "static_review": static_review, "completed_triage_ids": list(completed_triage_ids)}
    payload = {"kind": "workflow", "model": model, "identity": expected_identity, **job,
               "deadline_seconds": review_timeout_seconds(),
               "max_prompt_calls": workflow_prompt_budget() if max_prompt_calls is None else max_prompt_calls}
    if type(payload["max_prompt_calls"]) is not int or not 1 <= payload["max_prompt_calls"] <= 100:
        raise ProviderError("invalid_agent_configuration", request_sent=False)
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 50 * 1024 * 1024:
        raise ProviderError("agent_snapshot_too_large", request_sent=False)
    return await _run(payload, on_progress)


async def generate(messages, tools, model, expected_identity, *, response_format=None,
                   force_assessment=False, max_tokens=1024, timeout_seconds=120):
    state = await status()
    if not expected_identity or state.get('identity') != expected_identity:
        raise ProviderError('agent_configuration_changed', request_sent=False)
    if not state.get('verified') or state.get('selected_model') != model:
        raise ProviderError('agent_connection_unverified', request_sent=False)
    payload = {'kind': 'generate', 'model': model, 'identity': expected_identity,
        'messages': list(messages), 'tools': list(tools), 'response_format': response_format,
        'force_assessment': force_assessment, 'max_tokens': max_tokens,
        'timeout_seconds': max(60, min(180, int(timeout_seconds)))}
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 1_500_000:
        raise ProviderError('context_budget_exceeded', request_sent=False)
    result = await _run(payload)
    if result.get('identity') != expected_identity or result.get('model') != model:
        raise ProviderError('agent_configuration_changed')
    if result.get('status') != 'passed':
        reason = result.get('reason')
        raise ProviderError(reason if reason in {'agent_timeout', 'invalid_agent_result',
            'agent_execution_failed', 'agent_cleanup_failed'} else 'agent_execution_failed')
    return result
