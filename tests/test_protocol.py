"""Protocol-model and wire-format tests."""

import json
from uuid import UUID

import pytest
from conftest import received_payload, state_payload
from pydantic import ValidationError

from arena_hero import (
    ChampionBeacon,
    CommandPlan,
    CoreState,
    CoreView,
    Direction,
    HarvestAction,
    PlayerState,
    ProtocolError,
    Received,
    Tick,
    UnitView,
)
from arena_hero._protocol import (
    api_error,
    encode_plan,
    parse_accepted,
    parse_stream_message,
)


def test_parses_all_stream_message_types() -> None:
    tick = parse_stream_message('{"type":"tick","data":9}')
    state = parse_stream_message(json.dumps({"type": "state", "data": state_payload()}))
    receipt = parse_stream_message(
        json.dumps({"type": "received", "data": received_payload()})
    )

    assert tick == Tick(tick=9)
    assert isinstance(state, PlayerState)
    assert state.events[0].event_type == "A_FUTURE_EVENT"
    assert state.events[0].reason_code == "A_FUTURE_REASON"
    assert isinstance(receipt, Received)
    assert receipt.plan.tick == 9
    assert receipt.received_at_datetime.tzinfo is not None


@pytest.mark.parametrize(
    "raw",
    [
        b'{"type":"tick","data":9}',
        '{"type":"unknown","data":9}',
        '{"type":"tick","data":0}',
        '{"type":"tick","data":"9"}',
        '{"type":"tick","data":9,"extra":true}',
        "not-json",
    ],
)
def test_rejects_invalid_stream_messages(raw: str | bytes) -> None:
    with pytest.raises(ProtocolError):
        parse_stream_message(raw)


def test_state_models_enforce_conditional_fields() -> None:
    with pytest.raises(ValidationError, match="carrier_id"):
        ChampionBeacon(position=(0, 0), status="CARRIED")

    with pytest.raises(ValidationError, match="movement fields"):
        CoreView(
            kind="CORE",
            id=UUID("00000000-0000-4000-8000-000000000001"),
            controlled=True,
            position=(0, 0),
            hp=5,
            shield=5,
            state=CoreState.NORMAL,
            move_direction="UP",
        )

    carrier_id = UUID("00000000-0000-4000-8000-000000000001")
    with pytest.raises(ValidationError, match="only valid"):
        ChampionBeacon(
            position=(0, 0),
            status="GROUND",
            carrier_id=carrier_id,
        )
    with pytest.raises(ValidationError, match="requires all movement"):
        CoreView(
            kind="CORE",
            id=carrier_id,
            controlled=True,
            position=(0, 0),
            hp=5,
            shield=5,
            state=CoreState.MOVING,
        )
    with pytest.raises(ValidationError, match="cargo"):
        UnitView(
            kind="UNIT",
            id=carrier_id,
            controlled=False,
            position=(0, 0),
            hp=2,
            unit_type="WORKER",
            cargo=1,
        )

    active_with_respawn = state_payload()
    active_with_respawn["respawn_at_tick"] = 10
    with pytest.raises(ValidationError, match="ACTIVE"):
        PlayerState.model_validate(active_with_respawn)

    respawning_without_tick = state_payload()
    respawning_without_tick["status"] = "RESPAWNING"
    with pytest.raises(ValidationError, match="RESPAWNING"):
        PlayerState.model_validate(respawning_without_tick)

    mismatched_receipt = received_payload()
    mismatched_receipt["plan"]["tick"] = 10
    with pytest.raises(ValidationError, match="does not match"):
        Received.model_validate(mismatched_receipt)


@pytest.mark.parametrize(
    ("direction", "delta"),
    [
        (Direction.UP, (0, -1)),
        (Direction.DOWN, (0, 1)),
        (Direction.LEFT, (-1, 0)),
        (Direction.RIGHT, (1, 0)),
    ],
)
def test_direction_deltas(
    direction: Direction,
    delta: tuple[int, int],
) -> None:
    assert direction.delta == delta


def test_plan_encoding_is_stable_compact_and_sorted() -> None:
    first_id = UUID("00000000-0000-4000-8000-000000000002")
    second_id = UUID("00000000-0000-4000-8000-000000000003")
    plan = CommandPlan(
        tick=9,
        unit_actions={
            second_id: HarvestAction(),
            first_id: HarvestAction(),
        },
    )

    encoded = encode_plan(plan)

    assert encoded == (
        b'{"tick":9,"unit_actions":{'
        b'"00000000-0000-4000-8000-000000000002":{"type":"HARVEST"},'
        b'"00000000-0000-4000-8000-000000000003":{"type":"HARVEST"}}}'
    )
    assert encode_plan(plan) == encoded


def test_parses_acceptance_and_safe_api_errors() -> None:
    accepted = parse_accepted(
        b'{"accepted":true,"tick":9,"source":"AGENT",'
        b'"received_at":"2026-07-28T12:00:00Z"}'
    )
    error = api_error(
        409,
        b'{"error":"TICK_MISMATCH","message":"wrong tick","expected_tick":10}',
    )

    assert accepted.tick == 9
    assert accepted.received_at.isoformat() == "2026-07-28T12:00:00+00:00"
    assert error.error == "TICK_MISMATCH"
    assert error.details == {"expected_tick": 10}
    assert str(error) == "409 TICK_MISMATCH: wrong tick"


def test_rejects_malformed_acceptance_and_normalizes_non_json_error() -> None:
    with pytest.raises(ProtocolError):
        parse_accepted(b'{"accepted":false}')

    error = api_error(502, b"<html>bad gateway</html>")

    assert error.error == "HTTP_ERROR"
    assert error.message is None
