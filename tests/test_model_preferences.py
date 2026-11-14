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
async def test_rejects_unknown_and_nonreviewer_local_connection():
    from app.routers import model_settings as api
    with pytest.raises(HTTPException) as exc:
        await api.patch_model_settings(api.SettingsPatch(workflows={"planner": "antigravity_local"}))
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await api.patch_model_settings(api.SettingsPatch(workflows={"reviewer": "unknown"}))
    assert exc.value.status_code == 422


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
