"""Semantic I/O models for the arena.agent.io.v1 contract."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

SchemaVersion = Literal[1]
"""Wire schema version accepted by this model set. Unknown versions fail closed."""

AGENT_IO_SCHEMA_VERSION: SchemaVersion = 1
"""Current wire schema version for the arena.agent.io.v1 contract."""

MESSAGE_TYPES: tuple[str, ...] = (
    "hello",
    "ready",
    "episode_start",
    "decide",
    "decision",
    "episode_end",
    "error",
)
"""Discriminator values in the canonical ADR-0004 order."""

EpisodeOutcome = Literal["completed", "aborted", "timeout", "invalidated"]
"""Outcome vocabulary for episode_end. Unknown outcomes fail closed."""


class AgentMessageBase(BaseModel):
    """Shared envelope shape for every semantic message.

    Wire keys are camelCase (``messageId``, ``schemaVersion``); Python code may
    construct with snake_case field names via ``populate_by_name``. Unknown
    fields are rejected so forward drift never passes silently.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    type: str
    schema_version: SchemaVersion
    message_id: UUID


class AgentCapabilities(BaseModel):
    """Capabilities negotiated by capability in ``hello``."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    cancel: bool = False


class HelloMessage(AgentMessageBase):
    """Agent startup handshake announcing contestant identity and capabilities."""

    type: Literal["hello"]
    contestant: str
    capabilities: AgentCapabilities
    rules_versions: list[str] = Field(default_factory=list)
    payload: dict[str, object] | None = None


class ReadyMessage(AgentMessageBase):
    """Agent acknowledgement after a runner request (for example episode_start)."""

    type: Literal["ready"]
    request_id: UUID
    tenant_id: str
    note: str | None = None


class EpisodeStartMessage(AgentMessageBase):
    """Runner request that opens a match episode for one tenant."""

    type: Literal["episode_start"]
    tenant_id: str
    rules_version: str
    seed: int | None = None
    deadline_ms: int | None = Field(default=None, ge=1)
    schema_digests: dict[str, str] | None = None
    payload: dict[str, object] | None = None


class DecideMessage(AgentMessageBase):
    """Runner request for one decision at a given tenant tick."""

    type: Literal["decide"]
    request_id: UUID
    tenant_id: str
    tick: int = Field(ge=1)
    deadline_ms: int | None = Field(default=None, ge=1)
    payload: dict[str, object] | None = None


class DecisionMessage(AgentMessageBase):
    """Agent reply to a DecideMessage, echoing its request id."""

    type: Literal["decision"]
    request_id: UUID
    decision_id: UUID
    tenant_id: str
    tick: int = Field(ge=1)
    payload: dict[str, object] | None = None


class EpisodeEndMessage(AgentMessageBase):
    """Runner notification that a match episode ended."""

    type: Literal["episode_end"]
    tenant_id: str
    outcome: EpisodeOutcome
    payload: dict[str, object] | None = None


class ErrorMessage(AgentMessageBase):
    """Typed diagnostic envelope; request_id echoes the failed request when known."""

    type: Literal["error"]
    request_id: UUID | None = None
    code: str
    message: str
    details: dict[str, object] | None = None


AgentMessage = Annotated[
    HelloMessage
    | ReadyMessage
    | EpisodeStartMessage
    | DecideMessage
    | DecisionMessage
    | EpisodeEndMessage
    | ErrorMessage,
    Field(discriminator="type"),
]
"""Discriminated union of every arena.agent.io.v1 message."""

__all__ = [
    "AGENT_IO_SCHEMA_VERSION",
    "MESSAGE_TYPES",
    "AgentCapabilities",
    "AgentMessage",
    "AgentMessageBase",
    "DecideMessage",
    "DecisionMessage",
    "EpisodeEndMessage",
    "EpisodeOutcome",
    "ErrorMessage",
    "HelloMessage",
    "ReadyMessage",
    "SchemaVersion",
]
