import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.model_preferences import Preferences, get_overrides, use_preferences, load_preferences


@pytest.mark.asyncio
async def test_preference_snapshots_are_isolated_and_restored():
    async def task(value):
        with use_preferences(Preferences(workflows={"reviewer": value})):
            await asyncio.sleep(0)
            return get_overrides()
    assert await asyncio.gather(task("one"), task("two")) == [{"reviewer": "one"}, {"reviewer": "two"}]
    assert get_overrides() == {}


@pytest.mark.asyncio
async def test_load_preferences_preserves_default_when_unset():
    class Session:
        async def get(self, model, key):
            assert key == 1
            return None
    assert await load_preferences(Session()) == Preferences()


@pytest.mark.asyncio
async def test_rejects_unknown_connections_and_unverified_native_workflows():
    from app.routers import model_settings as api
    with pytest.raises(HTTPException) as exc:
        await api.patch_model_settings(api.SettingsPatch(workflows={"unknown": "mistral_primary"}))
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await api.patch_model_settings(api.SettingsPatch(workflows={"reviewer": "unknown"}))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_saves_other_changes_while_existing_antigravity_routes_are_unavailable(monkeypatch):
    from app.routers import model_settings as api

    row = SimpleNamespace(workflows={"reviewer": api.AG, "qa": api.AG}, antigravity_model="fixture",
                          antigravity_verified_model="fixture", antigravity_identity="digest")
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, model, key): return row
        async def commit(self): pass

    async def locked(session): return row
    async def unavailable(): return {"enabled": False, "models": []}
    monkeypatch.setattr(api, "SessionLocal", Session)
    monkeypatch.setattr(api, "_locked_row", locked)
    monkeypatch.setattr(api.antigravity_bridge, "status", unavailable)

    saved = await api.patch_model_settings(api.SettingsPatch(workflows={"qa": "mistral_primary", "drift": "mistral_secondary"}))
    assert saved["workflows"]["reviewer"] == api.AG
    assert saved["workflows"]["qa"] == "mistral_primary"
    assert saved["workflows"]["drift"] == "mistral_secondary"
    with pytest.raises(HTTPException, match="Verify the selected Antigravity model first"):
        await api.patch_model_settings(api.SettingsPatch(workflows={"draft": api.AG}))


@pytest.mark.asyncio
async def test_all_workflow_routes_are_returned_with_saved_values_and_existing_defaults(monkeypatch):
    from app.routers import model_settings as api
    async def status():
        return {"enabled": False, "status": "disabled", "message": "Disabled", "models": []}
    monkeypatch.setattr(api.antigravity_bridge, "status", status)
    response = await api._response(Preferences())
    assert set(response["workflows"]) == {"planner", "reviewer", "draft", "qa", "summary", "drift", "pr_summary", "pr_qa"}
    saved = await api._response(Preferences({"drift": "mistral_secondary"}))
    assert saved["workflows"]["drift"] == "mistral_secondary"
    assert saved["workflows"]["pr_qa"] == "mistral_primary"


@pytest.mark.asyncio
async def test_all_native_workflow_roles_are_advertised(monkeypatch):
    from app.routers import model_settings as api
    async def status():
        return {"enabled": True, "status": "ready", "message": "Ready", "models": ["fixture"], "identity": "digest"}
    monkeypatch.setattr(api.antigravity_bridge, "status", status)
    response = await api._response(Preferences({}, "fixture", "fixture", "digest"))
    native = next(item for item in response["connections"] if item["id"] == "antigravity_local")
    assert native["configured"]
    assert api.WORKFLOWS | {"tools", "json_object", "json_schema"} <= set(native["capabilities"])


@pytest.mark.asyncio
async def test_native_generation_check_uses_quota_and_hides_output(monkeypatch):
    from app.routers import model_settings as api
    connection = SimpleNamespace(model="fixture")
    async def load(*args): return Preferences({}, "fixture", "fixture", "digest")
    def native(preferences):
        assert preferences.antigravity_model == "fixture"
        return connection
    async def request(messages, tools, **kwargs):
        assert messages == [{"role": "user", "content": "Reply only with OK."}]
        assert tools == [] and kwargs == {"connection": connection, "workflow": "qa", "model_name": "fixture", "max_tokens": 16}
        return ({"role": "assistant", "content": "private output"}, {})
    monkeypatch.setattr(api, "load_preferences", load)
    monkeypatch.setattr(api.model_connections, "native_connection", native)
    monkeypatch.setattr(api.model_quota, "request", request)
    assert await api.test_antigravity_generation() == {"status": "failed", "message": "The model returned an unexpected response."}


@pytest.mark.asyncio
async def test_response_hides_credentials_and_keeps_default_reviewer_lanes(monkeypatch):
    from app.routers import model_settings as api
    monkeypatch.setenv("MISTRAL_API_KEY", "secret-value")
    monkeypatch.setenv("MISTRAL_API_KEY2", "second-secret")
    monkeypatch.delenv("SENTRA_MODEL_WORKFLOWS", raising=False)
    async def status():
        return {"enabled": False, "status": "disabled", "message": "Disabled", "models": []}
    monkeypatch.setattr(api.antigravity_bridge, "status", status)
    response = await api._response(Preferences())
    assert response["workflows"]["reviewer"] == "mistral_primary"
    assert response["connections"][0]["configured"]
    assert "secret-value" not in str(response)
    _, defaults = api._registry()
    assert defaults["reviewer"] == ["mistral_primary", "mistral_secondary"]

@pytest.mark.asyncio
async def test_bridge_test_bootstraps_and_persists_only_actual_verified_identity(monkeypatch):
    from app.routers import model_settings as api
    row = SimpleNamespace(workflows={}, antigravity_model=None, antigravity_verified_model=None, antigravity_identity=None)
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, model, key): return row
        async def commit(self): pass
    async def locked(session): return row
    async def test(model):
        assert model is None
        return {"status": "ok", "message": "Verified", "verified": True, "selected_model": "actual", "models": ["actual"], "identity": "digest"}
    monkeypatch.setattr(api, "SessionLocal", Session)
    monkeypatch.setattr(api, "_locked_row", locked)
    monkeypatch.setattr(api.antigravity_bridge, "test", test)
    assert await api.test_antigravity() == {"status": "ok", "message": "Verified"}
    assert (row.antigravity_model, row.antigravity_verified_model, row.antigravity_identity) == ("actual", "actual", "digest")


def test_review_agent_selection_fails_closed():
    from app.model_preferences import get_review_agent_selection
    from app.model_connections import ProviderError
    with use_preferences(Preferences({"reviewer": "antigravity_local"}, "model", "model", "digest")):
        assert get_review_agent_selection() == {"model": "model", "identity": "digest"}
    with use_preferences(Preferences({"reviewer": "antigravity_local"}, "model", None, None)):
        with pytest.raises(ProviderError):
            get_review_agent_selection()


@pytest.mark.asyncio
async def test_api_connection_probe_contract_and_single_quota_call(monkeypatch):
    from app.routers import model_settings as api
    connection = api._registry()[0]["mistral_primary"]
    calls = []
    async def request(messages, tools, **kwargs):
        calls.append((messages, tools, kwargs))
        return {"tool_calls": [{"function": {"name": "connection_probe", "arguments": '{"ok":true}'}}]}, {}
    monkeypatch.setattr(api.model_quota, "request", request)
    assert await api.test_api_connection(connection.name) == {"status": "passed", "reason": None, "status_code": None, "provider_code": None}
    assert len(calls) == 1 and calls[0][2]["connection"] == connection and calls[0][2]["max_tokens"] <= 128
    assert calls[0][1][0]["function"]["name"] == "connection_probe"
    async def invalid(*args, **kwargs):
        return {"tool_calls": [{"function": {"name": "connection_probe", "arguments": '{"ok":1}'}}]}, {}
    monkeypatch.setattr(api.model_quota, "request", invalid)
    assert (await api.test_api_connection(connection.name))["reason"] == "invalid_response"


@pytest.mark.asyncio
async def test_api_connection_failure_redacts_and_unknown_id_does_not_call(monkeypatch):
    from app.routers import model_settings as api
    async def request(*args, **kwargs):
        raise api.model_connections.ProviderError("model_access_denied", status_code=403, provider_code="tier_not_allowed")
    monkeypatch.setattr(api.model_quota, "request", request)
    result = await api.test_api_connection("mistral_primary")
    assert result == {"status": "failed", "reason": "model_access_denied", "status_code": 403, "provider_code": "tier_not_allowed"}
    with pytest.raises(HTTPException) as exc:
        await api.test_api_connection("unknown")
    assert exc.value.status_code == 404
    async def unsafe(*args, **kwargs):
        raise api.model_connections.ProviderError("secret", status_code=403, provider_code="secret")
    monkeypatch.setattr(api.model_quota, "request", unsafe)
    assert "secret" not in str(await api.test_api_connection("mistral_primary"))


@pytest.mark.asyncio
async def test_api_connection_text_only_requires_ok(monkeypatch):
    from dataclasses import replace
    from app.routers import model_settings as api
    connection = replace(api._registry()[0]["mistral_primary"], capabilities=())
    monkeypatch.setattr(api, "_registry", lambda: ({connection.name: connection}, {}))
    async def request(messages, tools, **kwargs):
        assert tools == []
        return {"content": "OK"}, {}
    monkeypatch.setattr(api.model_quota, "request", request)
    assert (await api.test_api_connection(connection.name))["status"] == "passed"
