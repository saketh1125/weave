"""Documentation model compatibility wrapper and review identity checks."""
import os
import hashlib
import json
import inspect

DEFAULT_MODEL = "mistral-large-4"

from app.model_connections import ProviderError

async def complete(messages, tools, model=DEFAULT_MODEL, max_tokens=1024, credential_slot=1, force_assessment=False, connection=None, workflow="reviewer", temperature=None, response_format=None, timeout_seconds=None, prompt_tokens_upper_bound=None):
    """Compatibility wrapper around the shared provider transport."""
    from app import model_connections
    return await model_connections.complete(messages, tools, connection=connection, workflow=workflow,
        slot=credential_slot, model=model, max_tokens=max_tokens, force_assessment=force_assessment, temperature=temperature, response_format=response_format, timeout_seconds=timeout_seconds, prompt_tokens_upper_bound=prompt_tokens_upper_bound)


def review_limits():
    limits = {}
    for name, default, maximum in (("calls", 200, 100_000), ("tokens", 16_000_000, 1_000_000_000)):
        raw = os.environ.get(f"SENTRA_DOCUMENTATION_MAX_RUN_{name.upper()}", str(default))
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 10 or not 1 <= int(raw) <= maximum:
            raise ProviderError("invalid_configuration", request_sent=False)
        limits[name] = int(raw)
    return limits


def review_settings():
    """Non-secret configuration bound to persisted review messages."""
    from app import model_connections as connections
    from . import reviewer, planner
    from app.model_preferences import get_review_agent_selection
    selection = get_review_agent_selection()
    if selection is not None:
        from . import agent_review
        from app import antigravity_bridge
        return {"kind": "antigravity", "limits": review_limits(), "model": selection["model"],
                "identity": selection["identity"],
                "agent_review": hashlib.sha256(inspect.getsource(agent_review).encode()).hexdigest(),
                "bridge": hashlib.sha256(inspect.getsource(antigravity_bridge).encode()).hexdigest()}
    lanes = [connections.resolve("reviewer", 1)]
    try:
        lanes.append(connections.resolve("reviewer", 2))
    except ProviderError as exc:
        if exc.reason != "connection_unavailable":
            raise
    plan = connections.resolve("planner", 1)
    def describe(conn):
        return {"fingerprint": connections.fingerprint(conn), "adapter": conn.adapter, "model": conn.model,
                "max_output_tokens": conn.max_output_tokens, "context_tokens": conn.context_tokens,
                "timeout_seconds": conn.timeout_seconds, "max_attempts": conn.max_attempts,
                "rpm": conn.rpm, "tpm": conn.tpm, "interval": conn.interval, "concurrency": conn.concurrency}
    return {"limits": review_limits(), "planner": describe(plan), "reviewers": [describe(c) for c in lanes],
            "review_prompt": hashlib.sha256(reviewer.SYSTEM.encode()).hexdigest(),
            "review_tools": hashlib.sha256(json.dumps(reviewer.TOOLS, sort_keys=True).encode()).hexdigest(),
            "planner_tools": hashlib.sha256(json.dumps(planner.TOOLS, sort_keys=True).encode()).hexdigest(),
            "planner_prompt": hashlib.sha256(inspect.getsource(planner.create_plan).encode()).hexdigest(),
            "planner_version": 1, "reviewer_version": 2}


def compatible_review(run, current):
    saved = run.manifest.get("model_settings")
    if saved is not None:
        return {**saved, "limits": saved.get("limits", {"calls": 200, "tokens": 16_000_000})} == current
    # Legacy conversations can contain unread source ranges; keep their saved results but do not resume them.
    return False
