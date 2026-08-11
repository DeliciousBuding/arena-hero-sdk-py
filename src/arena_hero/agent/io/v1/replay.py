"""Replay and record envelope for arena.agent.io.v1 transcripts.

A transcript is an ordered list of direction-tagged semantic messages with no
wall-clock data, so identical runs serialize to identical bytes and produce a
stable SHA-256 digest. The envelope is a recording concern only: it never
changes the semantic message model or its wire keys.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from pydantic.alias_generators import to_camel

from arena_hero.errors import ProtocolError

from .handler import AgentHandler
from .messages import AgentMessage
from .protocol import encode_agent_message

REPLAY_FORMAT_VERSION: Literal[1] = 1
"""Current replay envelope format version. Unknown versions fail closed."""

Direction = Literal["in", "out"]
"""Wire direction of a transcript record relative to the runner."""


class TranscriptRecord(BaseModel):
    """One direction-tagged semantic message in a recorded transcript."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    direction: Direction
    message: AgentMessage


class ReplayEnvelope(BaseModel):
    """Versioned, deterministic record of a full agent I/O round."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    format_version: Literal[1]
    tenant_id: str
    records: list[TranscriptRecord]


class ReplayMismatchError(ProtocolError):
    """A replayed transcript diverged from the recorded transcript."""


_ENVELOPE_ADAPTER = TypeAdapter(ReplayEnvelope)


def encode_replay(envelope: ReplayEnvelope) -> bytes:
    """Serialize an envelope into stable, compact UTF-8 JSON bytes."""

    data = envelope.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def parse_replay(raw: str | bytes) -> ReplayEnvelope:
    """Parse a replay envelope, failing closed on unknown format or shape."""

    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("invalid arena.agent.io.v1 replay envelope") from exc
    try:
        return _ENVELOPE_ADAPTER.validate_json(raw, strict=True)
    except ValidationError as exc:
        raise ProtocolError("invalid arena.agent.io.v1 replay envelope") from exc


def transcript_digest(records: Sequence[TranscriptRecord]) -> str:
    """Deterministic SHA-256 over the canonical transcript bytes.

    The same records in the same order always produce the same digest; no
    wall-clock data participates so replays are reproducible.
    """

    digest = hashlib.sha256()
    for record in records:
        digest.update(b"i" if record.direction == "in" else b"o")
        digest.update(encode_agent_message(record.message))
    return digest.hexdigest()


def replay_transcript(
    records: Sequence[TranscriptRecord],
    handler: AgentHandler,
    *,
    strict: bool = True,
) -> list[TranscriptRecord]:
    """Replay recorded inputs through ``handler`` and verify the outputs.

    In strict mode (default) the reproduced transcript must equal the recorded
    transcript or :class:`ReplayMismatchError` is raised. Deterministic
    handlers reproduce the exact recorded digest.
    """

    produced: list[TranscriptRecord] = []
    if records and records[0].direction == "out":
        produced.append(TranscriptRecord(direction="out", message=handler.hello()))
    for record in records:
        if record.direction != "in":
            continue
        produced.append(record)
        produced.extend(
            TranscriptRecord(direction="out", message=reply)
            for reply in handler.handle(record.message)
        )
    if strict and produced != list(records):
        raise ReplayMismatchError(
            "replayed transcript diverges from recorded transcript"
        )
    return produced


__all__ = [
    "REPLAY_FORMAT_VERSION",
    "Direction",
    "ReplayEnvelope",
    "ReplayMismatchError",
    "TranscriptRecord",
    "encode_replay",
    "parse_replay",
    "replay_transcript",
    "transcript_digest",
]
