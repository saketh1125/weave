"""Extension contract for isolated coding agents, separate from model APIs.

No production CLI adapter is registered. Implementations must enforce the
policy in their isolation backend; a prompt or a CLI permission flag alone is
not an isolation boundary. Publication remains exclusively in Sentra.
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class AgentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_output_bytes: int = Field(default=64_000, gt=0, le=1_000_000)
    max_steps: int = Field(default=32, gt=0, le=100)
    filesystem: Literal["isolated_read_only_snapshot"] = "isolated_read_only_snapshot"
    network: Literal["provider_only"] = "provider_only"
    source_writes: Literal[False] = False
    publication: Literal[False] = False


class AgentSession(BaseModel):
    """An adapter must reject resumption if any binding changes."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    adapter_identity: str = Field(min_length=1, max_length=200)
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str = Field(min_length=1, max_length=80)
    opaque_session_id: str | None = Field(default=None, max_length=200)


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "cancelled", "budget_exhausted", "failed"]
    assessments: list[dict] = Field(default_factory=list, max_length=100)
    session: AgentSession


class ReviewAgent(Protocol):
    """Contract only; implementing this protocol does not grant authorization.

    run must consume an immutable snapshot, enforce AgentPolicy outside the
    child process, and return bounded schema-validated output. It must validate
    assessments with the same citation/read-evidence checks as the API reviewer.
    Cancellation must terminate the entire process group and release resources.
    No inherited GitHub credentials, host home, source write mounts or arbitrary
    egress are permitted. Stderr and native session state never leave the server.
    A supported provider route and verified isolation are registration gates.
    """
    async def run(self, *, snapshot: dict[str, str], section: dict,
                  policy: AgentPolicy, session: AgentSession) -> AgentResult: ...

    async def cancel(self, session: AgentSession) -> None: ...


def resolve_review_agent(name: str) -> ReviewAgent:
    """Fail closed until a provider-authorized, isolated adapter is verified."""
    from app.model_connections import ProviderError
    raise ProviderError("unsupported_agent_connection", request_sent=False)
