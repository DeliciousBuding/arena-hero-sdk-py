"""Canonical encode/parse helpers for arena.agent.io.v1 semantic messages."""

from __future__ import annotations

import json

from pydantic import TypeAdapter, ValidationError

from arena_hero.errors import ProtocolError

from .messages import AgentMessage

_MESSAGE_ADAPTER = TypeAdapter(AgentMessage)


def parse_agent_message(raw: str | bytes) -> AgentMessage:
    """Parse one semantic message, failing closed on unknown type or version."""

    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("invalid arena.agent.io.v1 message") from exc
    try:
        return _MESSAGE_ADAPTER.validate_json(raw, strict=True)
    except ValidationError as exc:
        raise ProtocolError("invalid arena.agent.io.v1 message") from exc


def encode_agent_message(message: AgentMessage) -> bytes:
    """Serialize a semantic message into stable, compact UTF-8 JSON bytes."""

    data = message.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


__all__ = ["encode_agent_message", "parse_agent_message"]
