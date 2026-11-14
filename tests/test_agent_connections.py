import pytest
from pydantic import ValidationError

from app.agent_connections import AgentPolicy, AgentResult, AgentSession, resolve_review_agent


def test_no_cli_connection_can_execute_implicitly():
    from app.model_connections import ProviderError
    with pytest.raises(ProviderError, match="unsupported_agent_connection"):
        resolve_review_agent("antigravity")


@pytest.mark.parametrize("override", [{"source_writes": True}, {"publication": True},
    {"filesystem": "host"}, {"timeout_seconds": 0}, {"max_steps": 101}, {"network": "all"}])
def test_agent_policy_rejects_unbounded_or_write_access(override):
    with pytest.raises(ValidationError):
        AgentPolicy(**override)


def test_result_requires_session_binding_and_bounded_structure():
    session = AgentSession(adapter_identity="fixture:v1", snapshot_digest="a" * 64,
                           policy_digest="b" * 64, prompt_version="1")
    assert AgentResult(status="cancelled", session=session).assessments == []
    with pytest.raises(ValidationError):
        AgentResult(status="completed", session=session, assessments=[{}] * 101)
    with pytest.raises(ValidationError):
        AgentSession(adapter_identity="fixture:v1", snapshot_digest="bad", policy_digest="b" * 64, prompt_version="1")
