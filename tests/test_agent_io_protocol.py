"""Round-trip, strictness, and wire-shape tests for arena.agent.io.v1."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from arena_hero import (
    AgentCapabilities,
    AgentMessage,
    DecideMessage,
    DecisionMessage,
    EpisodeEndMessage,
    ErrorMessage,
    HelloMessage,
    ReadyMessage,
    encode_agent_message,
    parse_agent_message,
)
from arena_hero.agent.io.v1 import (
    AGENT_IO_SCHEMA_VERSION,
    MESSAGE_TYPES,
    EpisodeStartMessage,
)
from arena_hero.errors import ProtocolError

UUID1 = "00000000-0000-0000-0000-000000000001"
UUID2 = "00000000-0000-0000-0000-000000000002"
UUID3 = "00000000-0000-0000-0000-000000000003"


def hello(**overrides: Any) -> HelloMessage:
    values: dict[str, Any] = {
        "type": "hello",
        "schema_version": 1,
        "message_id": UUID1,
        "contestant": "arena-hero-smoke@0.1.0",
        "capabilities": AgentCapabilities(cancel=True),
    }
    values.update(overrides)
    return HelloMessage(**values)


def decision(**overrides: Any) -> DecisionMessage:
    values: dict[str, Any] = {
        "type": "decision",
        "schema_version": 1,
        "message_id": UUID1,
        "request_id": UUID2,
        "decision_id": UUID3,
        "tenant_id": "t1",
        "tick": 7,
    }
    values.update(overrides)
    return DecisionMessage(**values)


@pytest.mark.parametrize(
    "message",
    [
        hello(),
        hello(
            payload={"build": {"name": "smoke", "score": 1.5, "flags": [True, None]}}
        ),
        ReadyMessage(
            type="ready",
            schema_version=1,
            message_id=UUID1,
            request_id=UUID2,
            tenant_id="t1",
            note="episode opened",
        ),
        EpisodeStartMessage(
            type="episode_start",
            schema_version=1,
            message_id=UUID1,
            tenant_id="t1",
            rules_version="v0.14",
            seed=1234,
            deadline_ms=300_000,
            schema_digests={"arena.agent.io.v1": "abc"},
            payload={"map": {"size": 64}},
        ),
        DecideMessage(
            type="decide",
            schema_version=1,
            message_id=UUID1,
            request_id=UUID2,
            tenant_id="t1",
            tick=7,
            deadline_ms=500,
            payload={"state": {"resources": 10}},
        ),
        decision(payload={"plan": {"unitActions": []}}),
        EpisodeEndMessage(
            type="episode_end",
            schema_version=1,
            message_id=UUID1,
            tenant_id="t1",
            outcome="completed",
            payload={"summary": {"ticks": 400}},
        ),
        ErrorMessage(
            type="error",
            schema_version=1,
            message_id=UUID1,
            request_id=UUID2,
            code="protocol_error",
            message="unexpected frame",
            details={"frame": "x"},
        ),
        ErrorMessage(
            type="error",
            schema_version=1,
            message_id=UUID1,
            code="timeout",
            message="deadline",
        ),
    ],
)
def test_round_trip_all_message_types(message: AgentMessage) -> None:
    parsed = parse_agent_message(encode_agent_message(message))
    assert parsed == message


def test_wire_keys_are_camel_case() -> None:
    raw = encode_agent_message(decision()).decode()
    assert '"schemaVersion":1' in raw
    assert '"messageId":"' in raw
    assert '"requestId":"' in raw
    assert '"decisionId":"' in raw
    assert '"tenantId":"t1"' in raw
    assert '"tick":7' in raw
    assert "schema_version" not in raw
    assert "message_id" not in raw


def test_parse_accepts_camel_case_wire() -> None:
    wire = encode_agent_message(decision()).decode()
    assert parse_agent_message(wire) == decision()


def test_encode_is_stable_and_compact() -> None:
    first = encode_agent_message(decision())
    second = encode_agent_message(decision())
    assert first == second
    assert b" " not in first


def test_parse_rejects_invalid_utf8() -> None:
    with pytest.raises(ProtocolError, match=r"invalid arena\.agent\.io\.v1 message"):
        parse_agent_message(b"\xff\xfe")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (
            b'{"type":"pong","schemaVersion":1,"messageId":"' + UUID1.encode() + b'"}',
            "unknown type",
        ),
        (
            b'{"type":"hello","schemaVersion":2,"messageId":"'
            + UUID1.encode()
            + b'","contestant":"x"}',
            "future version",
        ),
        (
            b'{"type":"hello","schemaVersion":"1","messageId":"'
            + UUID1.encode()
            + b'","contestant":"x"}',
            "string version",
        ),
        (b'{"type":"hello","schemaVersion":1,"contestant":"x"}', "missing messageId"),
        (
            b'{"type":"hello","schemaVersion":1,"messageId":"' + UUID1.encode() + b'"}',
            "missing contestant",
        ),
        (
            b'{"type":"hello","schemaVersion":1,"messageId":"'
            + UUID1.encode()
            + b'","contestant":"x","surprise":1}',
            "extra field",
        ),
        (
            b'{"type":"decision","schemaVersion":1,"messageId":"'
            + UUID1.encode()
            + b'","requestId":"'
            + UUID2.encode()
            + b'","decisionId":"'
            + UUID3.encode()
            + b'","tenantId":"t1","tick":0}',
            "tick below minimum",
        ),
        (
            b'{"type":"decision","schemaVersion":1,"messageId":"'
            + UUID1.encode()
            + b'","requestId":"'
            + UUID2.encode()
            + b'","decisionId":"'
            + UUID3.encode()
            + b'","tenantId":"t1","tick":"7"}',
            "tick type coercion",
        ),
        (
            b'{"type":"episode_end","schemaVersion":1,"messageId":"'
            + UUID1.encode()
            + b'","tenantId":"t1","outcome":"forfeited"}',
            "unknown outcome",
        ),
    ],
)
def test_fail_closed_on_unknown_or_invalid(raw: bytes, reason: str) -> None:
    with pytest.raises(ProtocolError, match=r"invalid arena\.agent\.io\.v1 message"):
        parse_agent_message(raw)


def test_validation_error_is_wrapped_not_leaked() -> None:
    with pytest.raises(ProtocolError):
        parse_agent_message(
            b'{"type":"hello","schemaVersion":1,"messageId":"not-a-uuid","contestant":"x"}'
        )


def test_model_construction_is_strict() -> None:
    with pytest.raises(ValidationError):
        HelloMessage.model_validate(
            {
                "type": "hello",
                "schema_version": 1,
                "message_id": UUID1,
                "contestant": "x",
                "capabilities": AgentCapabilities(),
                "extra_field": True,
            }
        )
    with pytest.raises(ValidationError):
        DecideMessage(
            type="decide",
            schema_version=1,
            message_id=UUID1,
            request_id=UUID2,
            tenant_id="t1",
            tick=0,
        )


def test_union_discriminates_on_type() -> None:
    parsed = parse_agent_message(encode_agent_message(decision()))
    assert isinstance(parsed, DecisionMessage)
    assert not isinstance(parsed, HelloMessage)


def test_capability_defaults() -> None:
    capabilities = AgentCapabilities()
    assert capabilities.cancel is False


def test_contract_constants() -> None:
    assert AGENT_IO_SCHEMA_VERSION == 1
    assert MESSAGE_TYPES == (
        "hello",
        "ready",
        "episode_start",
        "decide",
        "decision",
        "episode_end",
        "error",
    )
    assert len(MESSAGE_TYPES) == 7
