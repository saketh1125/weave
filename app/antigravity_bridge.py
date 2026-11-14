"""Authenticated local agent bridge client; API containers never receive Docker access."""
import asyncio
import json
import os
import re
from urllib.parse import urlsplit

import httpx

from app.model_connections import ProviderError


def configuration():
    if os.environ.get("SENTRA_ANTIGRAVITY_ENABLED", "").lower() != "true":
        return None
    url = os.environ.get("SENTRA_AGENT_BRIDGE_URL", "http://host.docker.internal:9217")
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
    messages = {"passed": "Live documentation fixture passed.",
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


async def _json(client, method, url, **kwargs):
    async with client.stream(method, url, **kwargs) as response:
        if response.status_code != 200:
            raise ProviderError("agent_bridge_unavailable", request_sent=False)
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > 100_000:
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
            async with asyncio.timeout(400 if payload.get('kind') == 'review' else 160):
                while True:
                    result = await _json(client, "GET", url + "/jobs/" + job_id)
                    if result.get("status") != "running":
                        return result
                    if on_progress is not None and result.get('progress') is not None and result['progress'] != last_progress:
                        await on_progress(result['progress'])
                        last_progress = result['progress']
                    await asyncio.sleep(1)
        except (httpx.HTTPError, TimeoutError):
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
        "context": {k: section[k] for k in ("heading_context", "source_coverage", "reviewed_claims") if k in section}}, on_progress)
