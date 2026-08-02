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
    HarvestSource,
    PlayerState,
    ProtocolError,
    Received,
    ResolutionEvent,
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
            owner_username="arena_hero",
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
            owner_username="arena_hero",
            position=(0, 0),
            hp=5,
            shield=5,
            state=CoreState.MOVING,
        )

    invalid_username = state_payload()
    invalid_username["objects"][2]["owner_username"] = "@Arena Hero"
    with pytest.raises(ValidationError, match="owner_username"):
        PlayerState.model_validate(invalid_username)

    missing_username = state_payload()
    del missing_username["objects"][2]["owner_username"]
    with pytest.raises(ValidationError, match="owner_username"):
        PlayerState.model_validate(missing_username)
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


def test_resource_event_helpers() -> None:
    dropped = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000010"),
        tick=9,
        event_type="WORKER_CARGO_DROPPED",
        position=(4, 5),
        values={"amount": 2},
    )
    recovered = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000011"),
        tick=9,
        event_type="HARVEST_SUCCEEDED",
        position=(4, 5),
        values={"amount": 1, "source": "DROPPED_CARGO"},
    )
    deposited = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000013"),
        tick=9,
        event_type="DEPOSIT_SUCCEEDED",
        position=(4, 5),
        values={"amount": 1, "capacity": 15, "remaining": 1},
    )
    destroyed = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000014"),
        tick=9,
        event_type="CORE_RESOURCE_OVERFLOW_DESTROYED",
        position=(0, 0),
        values={"amount": 5, "capacity": 10},
    )
    captured = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000015"),
        tick=9,
        event_type="CORE_RESOURCES_CAPTURED",
        position=(0, 0),
        values={"amount": 1, "available": 6, "destroyed": 5, "capacity": 10},
    )

    assert dropped.resource_amount == 2
    assert dropped.harvest_source is None
    assert recovered.resource_amount == 1
    assert recovered.harvest_source is HarvestSource.DROPPED_CARGO
    assert deposited.resource_amount == 1
    assert deposited.harvest_source is None
    assert destroyed.resource_amount == 5
    assert destroyed.harvest_source is None
    assert captured.resource_amount == 1
    assert captured.core_resource_capture is not None
    assert captured.core_resource_capture.amount == 1
    assert captured.core_resource_capture.available == 6
    assert captured.core_resource_capture.destroyed == 5
    assert captured.core_resource_capture.capacity == 10


def test_core_resource_capture_helper_accepts_zero_and_rejects_bad_accounting() -> None:
    full = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000016"),
        tick=9,
        event_type="CORE_RESOURCES_CAPTURED",
        values={"amount": 0, "available": 5, "destroyed": 5, "capacity": 10},
    )
    malformed = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000017"),
        tick=9,
        event_type="CORE_RESOURCES_CAPTURED",
        values={"amount": 1, "available": 5, "destroyed": 5, "capacity": 10},
    )

    assert full.resource_amount is None
    assert full.core_resource_capture is not None
    assert full.core_resource_capture.amount == 0
    assert malformed.core_resource_capture is None


def test_healing_result_helper_validates_cost() -> None:
    healed = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000031"),
        tick=9,
        event_type="UNIT_HEAL_SUCCEEDED",
        values={"amount": 2, "hp": 4, "cost": 2},
    )
    malformed = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000032"),
        tick=9,
        event_type="CORE_HEAL_SUCCEEDED",
        values={"amount": 2, "hp": 5, "cost": 1},
    )
    assert healed.healing is not None
    assert healed.healing.amount == 2
    assert healed.healing.hp == 4
    assert malformed.healing is None


def test_upkeep_damage_event_preserves_forward_compatible_values() -> None:
    event = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000033"),
        tick=9,
        event_type="UNIT_DAMAGED",
        reason_code="UPKEEP_DEFICIT",
        target_id=UUID("00000000-0000-4000-8000-000000000034"),
        position=(12, -3),
        values={"damage": 2, "hp": 0},
    )

    assert event.reason_code == "UPKEEP_DEFICIT"
    assert event.values == {"damage": 2, "hp": 0}
    assert event.resource_amount is None
    assert event.healing is None


@pytest.mark.parametrize(
    ("values", "amount", "source"),
    [
        (None, None, None),
        ({"amount": True, "source": 1}, None, None),
        ({"amount": 0, "source": "A_FUTURE_SOURCE"}, None, None),
    ],
)
def test_resource_event_helpers_ignore_missing_or_future_values(
    values: dict[str, object] | None,
    amount: int | None,
    source: HarvestSource | None,
) -> None:
    event = ResolutionEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000012"),
        tick=9,
        event_type="HARVEST_SUCCEEDED",
        values=values,
    )

    assert event.resource_amount == amount
    assert event.harvest_source is source


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
