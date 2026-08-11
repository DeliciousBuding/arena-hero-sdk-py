"""Replay and record envelope tests for arena.agent.io.v1."""

from __future__ import annotations

import pytest

from arena_hero.agent.io.v1.conformance import ConformanceAgent, record_transcript
from arena_hero.agent.io.v1.messages import (
    AgentMessage,
    DecideMessage,
    DecisionMessage,
    EpisodeStartMessage,
    ErrorMessage,
    HelloMessage,
    ReadyMessage,
)
from arena_hero.agent.io.v1.replay import (
    REPLAY_FORMAT_VERSION,
    ReplayEnvelope,
    ReplayMismatchError,
    TranscriptRecord,
    encode_replay,
    parse_replay,
    replay_transcript,
    transcript_digest,
)
from arena_hero.errors import ProtocolError


def canonical_records() -> list[TranscriptRecord]:
    return record_transcript(ConformanceAgent())


def test_canonical_transcript_covers_all_message_types() -> None:
    records = canonical_records()
    types = [record.message.type for record in records]
    assert types == [
        "hello",
        "episode_start",
        "ready",
        "decide",
        "decision",
        "decide",
        "error",
        "episode_end",
    ]
    assert len(records) == 8


def test_request_ids_are_echoed_in_transcript() -> None:
    records = canonical_records()
    by_type = {record.message.type: record.message for record in records}
    ready = by_type["ready"]
    decision = by_type["decision"]
    error = by_type["error"]
    episode_start = by_type["episode_start"]
    decides = [r.message for r in records if r.message.type == "decide"]
    assert isinstance(ready, ReadyMessage)
    assert isinstance(decision, DecisionMessage)
    assert isinstance(error, ErrorMessage)
    assert isinstance(episode_start, EpisodeStartMessage)
    assert isinstance(decides[0], DecideMessage)
    assert isinstance(decides[1], DecideMessage)
    assert ready.request_id == episode_start.message_id
    assert decision.request_id == decides[0].request_id
    assert error.request_id == decides[1].request_id


def test_digest_is_deterministic() -> None:
    records = canonical_records()
    assert transcript_digest(records) == transcript_digest(records)
    assert transcript_digest(records) == transcript_digest(canonical_records())


def test_digest_changes_with_order() -> None:
    records = canonical_records()
    reordered = records[:1] + records[2:] + records[1:2]
    assert transcript_digest(reordered) != transcript_digest(records)


def test_digest_of_empty_transcript() -> None:
    digest = transcript_digest([])
    assert len(digest) == 64
    assert digest == transcript_digest([])


def test_envelope_round_trip() -> None:
    envelope = ReplayEnvelope(
        format_version=REPLAY_FORMAT_VERSION,
        tenant_id="tenant-conformance",
        records=canonical_records(),
    )
    parsed = parse_replay(encode_replay(envelope))
    assert parsed == envelope
    assert parsed.records == canonical_records()


def test_envelope_encoding_is_stable() -> None:
    envelope = ReplayEnvelope(
        format_version=REPLAY_FORMAT_VERSION,
        tenant_id="tenant-conformance",
        records=canonical_records(),
    )
    assert encode_replay(envelope) == encode_replay(envelope)


def test_replay_reproduces_exact_digest() -> None:
    records = canonical_records()
    replayed = replay_transcript(records, ConformanceAgent())
    assert replayed == records
    assert transcript_digest(replayed) == transcript_digest(records)


def test_replay_mismatch_fails_closed() -> None:
    records = canonical_records()

    class SilentAgent:
        def hello(self) -> HelloMessage:
            return ConformanceAgent().hello()

        def handle(self, message: AgentMessage) -> list[AgentMessage]:
            return []

    with pytest.raises(ReplayMismatchError):
        replay_transcript(records, SilentAgent())


def test_replay_non_strict_returns_produced() -> None:
    records = canonical_records()

    class SilentAgent:
        def hello(self) -> HelloMessage:
            return ConformanceAgent().hello()

        def handle(self, message: AgentMessage) -> list[AgentMessage]:
            return []

    produced = replay_transcript(records, SilentAgent(), strict=False)
    assert len(produced) == 5
    assert produced[0].direction == "out"
    assert produced[0].message.type == "hello"
    assert [record.direction for record in produced[1:]] == ["in", "in", "in", "in"]


def test_parse_replay_rejects_future_version() -> None:
    envelope = ReplayEnvelope(
        format_version=REPLAY_FORMAT_VERSION,
        tenant_id="tenant-conformance",
        records=canonical_records(),
    )
    raw = (
        encode_replay(envelope)
        .decode()
        .replace('"formatVersion":1', '"formatVersion":2')
    )
    with pytest.raises(
        ProtocolError, match=r"invalid arena\.agent\.io\.v1 replay envelope"
    ):
        parse_replay(raw)


def test_parse_replay_rejects_string_version() -> None:
    envelope = ReplayEnvelope(
        format_version=REPLAY_FORMAT_VERSION,
        tenant_id="tenant-conformance",
        records=canonical_records(),
    )
    raw = (
        encode_replay(envelope)
        .decode()
        .replace('"formatVersion":1', '"formatVersion":"1"')
    )
    with pytest.raises(ProtocolError):
        parse_replay(raw)


def test_parse_replay_rejects_unknown_direction() -> None:
    envelope = ReplayEnvelope(
        format_version=REPLAY_FORMAT_VERSION,
        tenant_id="tenant-conformance",
        records=canonical_records(),
    )
    raw = (
        encode_replay(envelope)
        .decode()
        .replace('"direction":"in"', '"direction":"sideways"')
    )
    with pytest.raises(ProtocolError):
        parse_replay(raw)


def test_parse_replay_rejects_unknown_message_type() -> None:
    envelope = ReplayEnvelope(
        format_version=REPLAY_FORMAT_VERSION,
        tenant_id="tenant-conformance",
        records=canonical_records(),
    )
    raw = encode_replay(envelope).decode().replace('"type":"hello"', '"type":"pong"')
    with pytest.raises(ProtocolError):
        parse_replay(raw)


def test_parse_replay_rejects_invalid_utf8() -> None:
    with pytest.raises(ProtocolError):
        parse_replay(b"\xff\xfe")


def test_transcript_record_is_strict() -> None:
    records = canonical_records()
    with pytest.raises(ValueError):
        TranscriptRecord.model_validate(
            {
                "direction": "in",
                "message": records[0].message.model_dump(mode="json", by_alias=True),
                "surprise": True,
            }
        )


def test_decision_message_round_trip_within_envelope() -> None:
    records = canonical_records()
    decision = next(
        record.message
        for record in records
        if isinstance(record.message, DecisionMessage)
    )
    assert isinstance(decision, DecisionMessage)
    assert isinstance(records[0].message, HelloMessage)
    decide = next(
        record.message
        for record in records
        if isinstance(record.message, DecideMessage)
    )
    assert decision.request_id == decide.request_id
