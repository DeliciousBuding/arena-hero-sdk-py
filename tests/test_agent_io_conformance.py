"""Shared conformance tests: in-memory and subprocess transports agree."""

from __future__ import annotations

from conftest import child_command, child_env

from arena_hero.agent.io.v1.conformance import (
    ConformanceAgent,
    canonical_scenario,
    record_transcript,
    run_subprocess_transcript,
)
from arena_hero.agent.io.v1.messages import (
    MESSAGE_TYPES,
    AgentCapabilities,
    HelloMessage,
)
from arena_hero.agent.io.v1.replay import TranscriptRecord, transcript_digest


def test_in_memory_transcript_is_canonical() -> None:
    records = record_transcript(ConformanceAgent())
    assert len(records) == 8
    message_types = [record.message.type for record in records]
    assert set(message_types) == set(MESSAGE_TYPES)
    assert message_types[0] == "hello"
    assert message_types[-1] == "episode_end"


def test_subprocess_transcript_matches_in_memory() -> None:
    memory = record_transcript(ConformanceAgent())
    subprocess = run_subprocess_transcript(child_command(), env=child_env())
    assert subprocess == memory
    assert transcript_digest(subprocess) == transcript_digest(memory)


def test_subprocess_transcript_is_reproducible() -> None:
    first = run_subprocess_transcript(child_command(), env=child_env())
    second = run_subprocess_transcript(child_command(), env=child_env())
    assert first == second
    assert transcript_digest(first) == transcript_digest(second)


def test_canonical_scenario_reply_counts() -> None:
    counts = [count for _, count in canonical_scenario()]
    assert counts == [1, 1, 1, 0]


def test_conformance_agent_replies_to_every_input() -> None:
    agent = ConformanceAgent()
    for message, expected_count in canonical_scenario():
        replies = agent.handle(message)
        assert len(replies) == expected_count


def test_conformance_agent_rejects_unexpected_message() -> None:
    agent = ConformanceAgent()
    replies = agent.handle(
        HelloMessage(
            type="hello",
            schema_version=1,
            message_id=__import__("uuid").UUID(int=1),
            contestant="x",
            capabilities=AgentCapabilities(),
        )
    )
    assert len(replies) == 1
    assert replies[0].type == "error"


def test_record_transcript_returns_transcript_records() -> None:
    records = record_transcript(ConformanceAgent())
    assert all(isinstance(record, TranscriptRecord) for record in records)
    assert records[0].direction == "out"
    assert records[1].direction == "in"
