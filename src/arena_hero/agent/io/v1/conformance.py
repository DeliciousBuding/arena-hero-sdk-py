"""Canonical arena.agent.io.v1 conformance scenario shared by transports.

Both the trusted in-memory adapter and the isolated subprocess transport run
this exact scenario; their transcripts must be identical so one conformance
suite guards both transports (ADR-0004). All identifiers are deterministic so
runs are reproducible byte-for-byte.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping, Sequence

from .framing import DEFAULT_MAX_FRAME_SIZE
from .handler import AgentHandler
from .messages import (
    AGENT_IO_SCHEMA_VERSION,
    AgentCapabilities,
    AgentMessage,
    DecideMessage,
    DecisionMessage,
    EpisodeEndMessage,
    EpisodeStartMessage,
    ErrorMessage,
    HelloMessage,
    ReadyMessage,
)
from .replay import TranscriptRecord
from .transport import (
    DEFAULT_ENV_ALLOWLIST,
    DEFAULT_IO_TIMEOUT_MS,
    DEFAULT_STDERR_LIMIT,
    SubprocessAgentTransport,
)

_UUID_NAMESPACE = uuid.UUID("7e63d3c6-9c3f-4a7e-9c1a-2f3a4b5c6d7e")

_TENANT_ID = "tenant-conformance"


def _deterministic_uuid(seed: str) -> uuid.UUID:
    return uuid.uuid5(_UUID_NAMESPACE, f"arena.agent.io.v1/conformance/{seed}")


def canonical_scenario() -> tuple[tuple[AgentMessage, int], ...]:
    """Scripted runner-to-agent inputs with their expected reply counts."""

    episode_start = EpisodeStartMessage(
        type="episode_start",
        schema_version=AGENT_IO_SCHEMA_VERSION,
        message_id=_deterministic_uuid("episode-start"),
        tenant_id=_TENANT_ID,
        rules_version="v1",
        seed=20260812,
        deadline_ms=30_000,
    )
    decide_ok = DecideMessage(
        type="decide",
        schema_version=AGENT_IO_SCHEMA_VERSION,
        message_id=_deterministic_uuid("decide-ok"),
        request_id=_deterministic_uuid("decide-ok-request"),
        tenant_id=_TENANT_ID,
        tick=1,
        deadline_ms=5_000,
        payload={"state": {"resources": 10}},
    )
    decide_fail = DecideMessage(
        type="decide",
        schema_version=AGENT_IO_SCHEMA_VERSION,
        message_id=_deterministic_uuid("decide-fail"),
        request_id=_deterministic_uuid("decide-fail-request"),
        tenant_id=_TENANT_ID,
        tick=2,
        deadline_ms=5_000,
        payload={"fail": True},
    )
    episode_end = EpisodeEndMessage(
        type="episode_end",
        schema_version=AGENT_IO_SCHEMA_VERSION,
        message_id=_deterministic_uuid("episode-end"),
        tenant_id=_TENANT_ID,
        outcome="completed",
    )
    return (
        (episode_start, 1),
        (decide_ok, 1),
        (decide_fail, 1),
        (episode_end, 0),
    )


class ConformanceAgent:
    """Deterministic agent used by the shared conformance suite."""

    def hello(self) -> HelloMessage:
        """Return the canonical startup handshake."""

        return HelloMessage(
            type="hello",
            schema_version=AGENT_IO_SCHEMA_VERSION,
            message_id=_deterministic_uuid("hello"),
            contestant="arena-hero-sdk@conformance",
            capabilities=AgentCapabilities(cancel=True),
        )

    def handle(self, message: AgentMessage) -> list[AgentMessage]:
        """Reply to one runner message following the canonical scenario."""

        if isinstance(message, EpisodeStartMessage):
            return [
                ReadyMessage(
                    type="ready",
                    schema_version=AGENT_IO_SCHEMA_VERSION,
                    message_id=_deterministic_uuid("ready"),
                    request_id=message.message_id,
                    tenant_id=message.tenant_id,
                    note="episode opened",
                )
            ]
        if isinstance(message, DecideMessage):
            if message.payload is not None and message.payload.get("fail") is True:
                return [
                    ErrorMessage(
                        type="error",
                        schema_version=AGENT_IO_SCHEMA_VERSION,
                        message_id=_deterministic_uuid("error-fail"),
                        request_id=message.request_id,
                        code="unsupported_decision",
                        message="fail marker requested",
                        details={"tick": message.tick},
                    )
                ]
            return [
                DecisionMessage(
                    type="decision",
                    schema_version=AGENT_IO_SCHEMA_VERSION,
                    message_id=_deterministic_uuid(f"decision-{message.tick}"),
                    request_id=message.request_id,
                    decision_id=_deterministic_uuid(f"decision-id-{message.tick}"),
                    tenant_id=message.tenant_id,
                    tick=message.tick,
                    payload={"plan": {"unitActions": []}},
                )
            ]
        if isinstance(message, (EpisodeEndMessage, ErrorMessage)):
            return []
        return [
            ErrorMessage(
                type="error",
                schema_version=AGENT_IO_SCHEMA_VERSION,
                message_id=_deterministic_uuid("error-unexpected"),
                request_id=message.message_id,
                code="unexpected_message",
                message=f"unexpected {message.type}",
            )
        ]


def record_transcript(handler: AgentHandler) -> list[TranscriptRecord]:
    """Run the canonical scenario in-memory and return the transcript."""

    records: list[TranscriptRecord] = [
        TranscriptRecord(direction="out", message=handler.hello())
    ]
    for message, _ in canonical_scenario():
        records.append(TranscriptRecord(direction="in", message=message))
        records.extend(
            TranscriptRecord(direction="out", message=reply)
            for reply in handler.handle(message)
        )
    return records


def run_subprocess_transcript(
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    env_allowlist: Sequence[str] = DEFAULT_ENV_ALLOWLIST,
    cwd: str | os.PathLike[str] | None = None,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
    stderr_limit: int = DEFAULT_STDERR_LIMIT,
    io_timeout_ms: int = DEFAULT_IO_TIMEOUT_MS,
    deadline_ms: int | None = None,
) -> list[TranscriptRecord]:
    """Run the canonical scenario over the subprocess transport."""

    with SubprocessAgentTransport(
        command,
        env=env,
        env_allowlist=env_allowlist,
        cwd=cwd,
        max_frame_size=max_frame_size,
        stderr_limit=stderr_limit,
        io_timeout_ms=io_timeout_ms,
        deadline_ms=deadline_ms,
    ) as transport:
        records: list[TranscriptRecord] = [
            TranscriptRecord(direction="out", message=transport.recv_hello())
        ]
        for message, reply_count in canonical_scenario():
            records.append(TranscriptRecord(direction="in", message=message))
            transport.send(message)
            records.extend(
                TranscriptRecord(direction="out", message=transport.recv())
                for _ in range(reply_count)
            )
        return records


__all__ = [
    "ConformanceAgent",
    "canonical_scenario",
    "record_transcript",
    "run_subprocess_transcript",
]
