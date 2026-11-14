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


def review_settings():
    """Non-secret configuration bound to persisted review messages."""
    from app import model_connections as connections
    from . import reviewer, planner
    from app.model_preferences import get_review_agent_selection
    selection = get_review_agent_selection()
    if selection is not None:
        from . import agent_review
        from app import antigravity_bridge
        return {"kind": "antigravity", "model": selection["model"],
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
    return {"planner": describe(plan), "reviewers": [describe(c) for c in lanes],
            "review_prompt": hashlib.sha256(reviewer.SYSTEM.encode()).hexdigest(),
            "review_tools": hashlib.sha256(json.dumps(reviewer.TOOLS, sort_keys=True).encode()).hexdigest(),
            "planner_tools": hashlib.sha256(json.dumps(planner.TOOLS, sort_keys=True).encode()).hexdigest(),
            "planner_prompt": hashlib.sha256(inspect.getsource(planner.create_plan).encode()).hexdigest(),
            "planner_version": 1, "reviewer_version": 1}


def compatible_review(run, current):
    saved = run.manifest.get("model_settings")
    if saved is not None:
        return saved == current
    if current.get("kind") == "antigravity":
        return False
    # Old messages have Mistral-specific semantics and used fixed defaults.
    return (not os.environ.get("SENTRA_MODEL_CONNECTIONS") and not os.environ.get("SENTRA_MODEL_WORKFLOWS")
            and current["review_prompt"] == "b0da876f9f9e4dd8801b26af9b538f9ffa4236ee019e7d1b60c57f64e2011e5b"
            and current["review_tools"] == "e83cedbd7a1b63a7cc1b4a8f06678a590c1b7f08a6966e03df64ad16d3a7b894"
            and current["planner_tools"] == "c19380bfe31c62a4d7b7728f8cef34d1092d1f9f9e5dc168836ab4cd81f2eaa6"
            and run.model == DEFAULT_MODEL and all(c["max_output_tokens"] == 4096 and c["context_tokens"] == 20000 and c["timeout_seconds"] == 60 and c["max_attempts"] == 2 and c["rpm"] == 50 and c["tpm"] == 20000 and c["interval"] == 1.25 and c["concurrency"] == 1 for c in [current["planner"], *current["reviewers"]]) and all(c["adapter"] == "mistral" and c["model"] == DEFAULT_MODEL
                for c in [current["planner"], *current["reviewers"]]))
