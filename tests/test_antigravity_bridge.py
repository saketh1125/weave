import asyncio
import json
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from app import antigravity_bridge as client
from app.model_connections import ProviderError
from scripts import antigravity_bridge as host

TOKEN = "t" * 40


def enabled(monkeypatch, url="http://127.0.0.1:9217"):
    monkeypatch.setenv("SENTRA_ANTIGRAVITY_ENABLED", "true")
    monkeypatch.setenv("SENTRA_AGENT_BRIDGE_URL", url)
    monkeypatch.setenv("SENTRA_AGENT_BRIDGE_TOKEN", TOKEN)


def test_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("SENTRA_ANTIGRAVITY_ENABLED", raising=False)
    assert client.configuration() is None
    enabled(monkeypatch, "http://example.com:9217")
    with pytest.raises(ProviderError): client.configuration()
    enabled(monkeypatch)
    monkeypatch.setenv("SENTRA_AGENT_BRIDGE_TOKEN", "short")
    with pytest.raises(ProviderError): client.configuration()
    with pytest.raises(ValueError): host.Bridge("short")


@pytest.mark.asyncio
async def test_redirects_are_not_followed_and_response_is_bounded(monkeypatch):
    enabled(monkeypatch)
    seen = []
    def redirect(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://example.com/stolen"})
    transport = httpx.MockTransport(redirect)
    original = httpx.AsyncClient
    monkeypatch.setattr(client, "_client", lambda token: original(transport=transport, follow_redirects=False))
    result = await client.status()
    assert result["enabled"] is False and len(seen) == 1
    async with original(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 100001))) as c:
        with pytest.raises(ProviderError): await client._json(c, "GET", "http://127.0.0.1:9217/status")


@pytest.mark.asyncio
async def test_cancellation_deletes_remote_job(monkeypatch):
    enabled(monkeypatch)
    deleted = asyncio.Event()
    polling = asyncio.Event()
    job_id = "a" * 32
    async def handle(request):
        if request.method == "POST": return httpx.Response(200, json={"id": job_id})
        if request.method == "DELETE":
            deleted.set()
            return httpx.Response(200, json={"status": "cancelled"})
        polling.set()
        return httpx.Response(200, json={"status": "running"})
    original = httpx.AsyncClient
    monkeypatch.setattr(client, "_client", lambda token: original(transport=httpx.MockTransport(handle)))
    task = asyncio.create_task(client._run({"kind": "test"}))
    await asyncio.wait_for(polling.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    await asyncio.wait_for(deleted.wait(), 1)


@pytest.mark.asyncio
async def test_polling_delivers_changed_progress_once_and_cleans_up(monkeypatch):
    enabled(monkeypatch)
    polls, deleted, updates = 0, False, []
    async def handle(request):
        nonlocal polls, deleted
        if request.method == 'POST': return httpx.Response(200, json={'id': 'a' * 32})
        if request.method == 'DELETE':
            deleted = True
            return httpx.Response(200, json={'status': 'cancelled'})
        polls += 1
        return httpx.Response(200, json={'status': 'passed'} if polls == 3 else
            {'status': 'running', 'progress': {'activity': {'steps': 2}}})
    original = httpx.AsyncClient
    monkeypatch.setattr(client, '_client', lambda token: original(transport=httpx.MockTransport(handle)))
    async def checkpoint(value): updates.append(value)
    assert (await client._run({'kind': 'review'}, checkpoint))['status'] == 'passed'
    assert updates == [{'activity': {'steps': 2}}] and deleted


@pytest.fixture
def bridge(tmp_path):
    instance = host.Bridge(TOKEN, tmp_path / "state.json")
    yield instance
    instance.close()


def test_http_requires_token_and_rejects_origins(bridge):
    server = ThreadingHTTPServer(("127.0.0.1", 0), host.Handler)
    server.bridge = bridge
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/status"
        assert httpx.get(url, trust_env=False).status_code == 401
        assert httpx.get(url, headers={"Authorization": "Bearer " + TOKEN, "Origin": "http://localhost"}, trust_env=False).status_code == 401
        response = httpx.get(url, headers={"Authorization": "Bearer " + TOKEN}, trust_env=False)
        assert response.status_code == 200
        assert TOKEN not in response.text
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_identity_invalidates_persisted_verification(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"identity": "old", "verified": True, "models": ["x"], "selected_model": "x"}))
    monkeypatch.setattr(host, "runtime_identity", lambda: "new")
    instance = host.Bridge(TOKEN, path)
    try:
        assert instance.status()["verified"] is False
        assert instance.status()["identity"] == "new"
        with pytest.raises(ValueError): instance.start({"kind": "review", "model": "x", "identity": "old"})
    finally:
        instance.close()


def test_failed_runner_keeps_requested_model_and_bridge_identity(bridge, monkeypatch, tmp_path):
    (tmp_path / 'work').mkdir()
    monkeypatch.setattr(host, 'ROOT', tmp_path)
    async def fail(**kwargs):
        return {'status': 'failed', 'reason': 'transport deadline exceeded', 'stage': 'acp_session'}
    monkeypatch.setattr(host.runner, 'run', fail)
    job = {'snapshot': {'README.md': 'Claim'}, 'section': {'id': 's', 'path': 'README.md',
        'start_line': 1, 'end_line': 1, 'content': 'Claim'}}
    result = asyncio.run_coroutine_threadsafe(bridge.execute({'kind': 'review', 'model': 'm', 'job': job}), bridge.loop).result(5)
    assert result['model'] == 'm' and result['identity'] == bridge.identity
    assert result['reason'] == 'transport deadline exceeded'


def test_single_job_and_stale_lease_cancels(bridge, monkeypatch):
    started = threading.Event()
    cancelled = threading.Event()
    async def run(**kwargs):
        started.set()
        try: await asyncio.Event().wait()
        finally: cancelled.set()
    monkeypatch.setattr(host.runner, "run", run)
    job = bridge.start({"kind": "test"})["id"]
    assert started.wait(2)
    with pytest.raises(ValueError): bridge.start({"kind": "test"})
    with bridge.lock: bridge.jobs[job]["seen"] -= 21
    assert cancelled.wait(3)
    with pytest.raises(ValueError): bridge.poll(job)

@pytest.mark.asyncio
async def test_host_failure_does_not_expose_exception_or_token(bridge, monkeypatch):
    async def run(**kwargs):
        raise ValueError("secret-provider-output " + TOKEN)
    monkeypatch.setattr(host.runner, "run", run)
    result = await bridge.execute({"kind": "test", "model": None})
    assert result["verified"] is False
    assert "secret-provider-output" not in json.dumps(result)
    assert TOKEN not in json.dumps(bridge.status())


def test_persisted_verification_is_restored_only_for_matching_identity(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"identity": "same", "verified": True, "models": ["x"], "selected_model": "x", "status": "passed", "message": "ok"}))
    monkeypatch.setattr(host, "runtime_identity", lambda: "same")
    instance = host.Bridge(TOKEN, path)
    try:
        assert instance.status()["verified"] is True
        assert instance.status()["selected_model"] == "x"
    finally:
        instance.close()


@pytest.mark.asyncio
async def test_profile_stays_exclusive_during_cancel_cleanup(bridge, monkeypatch):
    active, peak = 0, 0
    started = asyncio.Event()
    async def run(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.set()
        try:
            await asyncio.sleep(100)
        finally:
            await asyncio.sleep(.05)
            active -= 1
    monkeypatch.setattr(host.runner, 'run', run)
    first = asyncio.create_task(bridge.execute({'kind': 'test'}))
    await started.wait()
    first.cancel()
    second = asyncio.create_task(bridge.execute({'kind': 'test'}))
    await asyncio.sleep(.1)
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)
    assert peak == 1 and active == 0
