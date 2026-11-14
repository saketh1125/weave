"""Authenticated model settings; endpoints inherit the application auth middleware."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app import antigravity_bridge, model_connections
from app.db import SessionLocal
from app.model_preferences import Preferences, load_preferences, use_preferences
from app.models import ModelPreferences

router = APIRouter(prefix="/settings/models", tags=["model settings"])
WORKFLOWS = frozenset({"planner", "reviewer", "draft", "qa"})
AG = "antigravity_local"


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflows: dict[str, str] | None = None
    antigravity_model: str | None = Field(default=None, min_length=1, max_length=200)


async def _locked_row(session):
    await session.execute(insert(ModelPreferences).values(id=1, workflows={}).on_conflict_do_nothing(index_elements=["id"]))
    result = await session.execute(select(ModelPreferences).where(ModelPreferences.id == 1).with_for_update())
    return result.scalar_one()


def _registry():
    # The registry here describes available connections, independent of the
    # current request's stored workflow overrides.
    with use_preferences(Preferences()):
        return model_connections._registry()


async def _response(preferences):
    registry, defaults = _registry()
    status = await antigravity_bridge.status()
    selected = preferences.antigravity_model
    verified = bool(status.get("enabled") and selected and selected == preferences.antigravity_verified_model and preferences.antigravity_identity and preferences.antigravity_identity == status.get("identity") and selected in status.get("models", []))
    connections = [{"id": c.name, "label": c.name.replace("_", " ").title(), "adapter": c.adapter,
                    "model": c.model, "configured": bool(model_connections._credential(c)),
                    "capabilities": list(c.capabilities)} for c in registry.values()]
    connections.append({"id": AG, "label": "Antigravity Local", "adapter": "antigravity_local",
                        "model": selected or "", "configured": verified, "capabilities": ["reviewer"]})
    workflows = {}
    for workflow in WORKFLOWS:
        value = preferences.workflows.get(workflow, defaults[workflow])
        workflows[workflow] = value[0] if isinstance(value, list) else value
    return {"connections": connections, "workflows": workflows,
            "antigravity": {"enabled": bool(status.get("enabled")), "status": status.get("status", "unavailable"),
                            "message": status.get("message", "Local bridge unavailable."),
                            "models": status.get("models", []), "selected_model": selected, "verified": verified}}


@router.get("")
async def get_model_settings():
    return await _response(await load_preferences())


@router.patch("")
async def patch_model_settings(body: SettingsPatch):
    updates = body.workflows or {}
    if set(updates) - WORKFLOWS:
        raise HTTPException(422, "Unknown workflow")
    registry, _ = _registry()
    if any(value not in registry and value != AG for value in updates.values()):
        raise HTTPException(422, "Unknown model connection")
    if any(value == AG and key != "reviewer" for key, value in updates.items()):
        raise HTTPException(422, "Antigravity is available only for reviews")
    status = await antigravity_bridge.status()
    async with SessionLocal() as session:
        row = await _locked_row(session)
        if body.antigravity_model is not None:
            if not status.get("enabled") or body.antigravity_model not in status.get("models", []):
                raise HTTPException(422, "Antigravity model is unavailable")
            if body.antigravity_model != row.antigravity_model:
                row.antigravity_model = body.antigravity_model
                row.antigravity_verified_model = None
                row.antigravity_identity = None
        workflows = dict(row.workflows or {})
        workflows.update(updates)
        if workflows.get("reviewer") == AG and not (status.get("enabled") and row.antigravity_model
                and row.antigravity_model == row.antigravity_verified_model
                and row.antigravity_model in status.get("models", []) and row.antigravity_identity
                and row.antigravity_identity == status.get("identity")):
            raise HTTPException(422, "Verify the selected Antigravity model first")
        row.workflows = workflows
        await session.commit()
        preferences = await load_preferences(session)
    return await _response(preferences)


@router.post("/antigravity/test")
async def test_antigravity():
    async with SessionLocal() as session:
        preferences = await load_preferences(session)
    result = await antigravity_bridge.test(preferences.antigravity_model)
    async with SessionLocal() as session:
        row = await _locked_row(session)
        # A concurrent selection change must not inherit a previous test result.
        if row.antigravity_model == preferences.antigravity_model:
            tested_model = result.get("selected_model")
            valid = bool(result.get("verified") and tested_model and result.get("identity")
                         and tested_model in result.get("models", [])
                         and (row.antigravity_model is None or tested_model == row.antigravity_model))
            if valid:
                row.antigravity_model = tested_model
            row.antigravity_verified_model = tested_model if valid else None
            row.antigravity_identity = result.get("identity") if valid else None
            await session.commit()
    return {"status": result.get("status", "unavailable"), "message": result.get("message", "Local bridge unavailable.")}
