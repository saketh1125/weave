"""Durable model selections and per-operation preference snapshots."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.models import ModelPreferences


@dataclass(frozen=True)
class Preferences:
    workflows: dict[str, str] = field(default_factory=dict)
    antigravity_model: str | None = None
    antigravity_verified_model: str | None = None
    antigravity_identity: str | None = None


_current = ContextVar("model_preferences", default=Preferences())


def get_overrides():
    return dict(_current.get().workflows)


def get_review_agent_selection():
    prefs = _current.get()
    if prefs.workflows.get("reviewer") != "antigravity_local":
        return None
    if not prefs.antigravity_model or prefs.antigravity_model != prefs.antigravity_verified_model or not prefs.antigravity_identity:
        from app.model_connections import ProviderError
        raise ProviderError("connection_unavailable", request_sent=False)
    return {"model": prefs.antigravity_model, "identity": prefs.antigravity_identity}


def get_preferences():
    return _current.get()


@contextmanager
def use_preferences(preferences):
    token = _current.set(preferences)
    try:
        yield
    finally:
        _current.reset(token)


async def load_preferences(session=None):
    if session is None:
        from app.db import SessionLocal
        async with SessionLocal() as session:
            return await load_preferences(session)
    row = await session.get(ModelPreferences, 1)
    if row is None:
        return Preferences()
    return Preferences(dict(row.workflows or {}), row.antigravity_model, row.antigravity_verified_model, row.antigravity_identity)
