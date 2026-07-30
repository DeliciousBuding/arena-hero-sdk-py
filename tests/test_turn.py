"""Per-Unit control-interface tests."""

from uuid import UUID

import pytest
from conftest import state_payload

from arena_hero import (
    Accepted,
    AsyncTurn,
    Direction,
    InvalidActionError,
    PlayerState,
    Turn,
    TurnClosedError,
    UnitType,
)


def make_state() -> PlayerState:
    """Validate the representative test state."""

    return PlayerState.model_validate(state_payload())


def accepted() -> Accepted:
    """Return a successful command acknowledgement."""

    return Accepted(
        accepted=True,
        tick=9,
        source="AGENT",
        received_at="2026-07-28T12:00:00Z",
    )


def test_exposes_every_control_type_and_state_query() -> None:
    turn = Turn(tick=9, state=make_state(), submitter=lambda _plan, _key: accepted())

    assert turn.resources == 15
    assert turn.resource_capacity == 15
    assert turn.resource_space == 0
    assert len(turn.units) == 3
    assert len(turn.workers) == 1
    assert len(turn.vanguards) == 1
    assert len(turn.rangers) == 1
    assert turn.core is not None
    assert turn.core.id == turn.core.view.id
    assert turn.core.position == (0, 0)
    assert turn.core.hp == 5
    assert turn.core.shield == 5
    assert turn.core.owner_username == "arena_hero"
    assert turn.workers[0].cargo == 0
    assert turn.workers[0].position == (1, 0)
    assert turn.workers[0].hp == 2
    assert turn.workers[0].unit_type is UnitType.WORKER
    assert turn.visible_enemies[0].controlled is False
    assert len(turn.visible_enemies) == 2
    assert len(turn.terrain) == 2
    assert turn.resource_cells == frozenset({(1, 0)})
    assert turn.obstacle_cells == frozenset({(2, 2), (2, 3)})
    assert turn.events[0].event_type == "A_FUTURE_EVENT"
    assert turn.beacon.position == (0, 0)
    assert turn.unit(turn.workers[0].id) is turn.workers[0]
    assert turn.unit(str(turn.rangers[0].id)) is turn.rangers[0]


def test_unit_and_core_methods_build_one_complete_plan() -> None:
    submitted = []

    def submitter(plan, _key):
        submitted.append(plan)
        return accepted()

    turn = Turn(tick=9, state=make_state(), submitter=submitter)
    worker = turn.workers[0]
    vanguard = turn.vanguards[0]
    ranger = turn.rangers[0]
    enemy = turn.visible_enemies[0]

    worker.harvest()
    vanguard.sweep(Direction.LEFT)
    ranger.shoot(enemy)
    assert turn.core is not None
    turn.core.spawn(UnitType.WORKER)
    response = turn.submit(idempotency_key="turn-test-0001")

    assert response.accepted is True
    assert submitted[0].tick == 9
    assert submitted[0].unit_actions[worker.id].type == "HARVEST"
    assert submitted[0].unit_actions[vanguard.id].type == "SWEEP"
    assert submitted[0].unit_actions[ranger.id].type == "SHOOT"
    assert submitted[0].core_action is not None
    assert submitted[0].core_action.type == "SPAWN"


def test_all_unit_and_core_actions_can_be_queued_or_cleared() -> None:
    turn = Turn(tick=9, state=make_state(), submitter=lambda _plan, _key: accepted())
    worker = turn.workers[0]
    vanguard = turn.vanguards[0]
    ranger = turn.rangers[0]
    assert turn.core is not None

    worker.move(Direction.RIGHT)
    worker.deposit()
    worker.pickup_beacon()
    worker.drop_beacon()
    worker.self_destruct()
    worker.wait()
    worker.clear_action()
    vanguard.move(Direction.UP)
    vanguard.pickup_beacon()
    vanguard.drop_beacon()
    vanguard.self_destruct()
    vanguard.wait()
    ranger.move(Direction.DOWN)
    ranger.pickup_beacon()
    ranger.drop_beacon()
    ranger.self_destruct()
    ranger.wait()
    ranger.shoot(
        UUID("00000000-0000-4000-8000-000000000005"),
        expected_cell=(-2, 0),
    )
    turn.core.repair_shield()
    turn.core.start_move(Direction.LEFT)
    turn.core.cancel_move()
    turn.core.pickup_beacon()
    turn.core.drop_beacon()
    turn.core.wait()
    turn.core.clear_action()

    assert worker.id not in turn.plan.unit_actions
    assert turn.plan.unit_actions[vanguard.id].type == "WAIT"
    assert turn.plan.unit_actions[ranger.id].type == "SHOOT"
    assert turn.plan.core_action is None

    turn.clear()
    assert turn.plan.unit_actions == {}


def test_rejects_unknown_units_bad_targets_and_closed_turns() -> None:
    turn = Turn(tick=9, state=make_state(), submitter=lambda _plan, _key: accepted())

    with pytest.raises(InvalidActionError, match="unknown owned Unit"):
        turn.unit("00000000-0000-4000-8000-000000000099")
    with pytest.raises(InvalidActionError, match="expected_cell"):
        turn.rangers[0].shoot(UUID("00000000-0000-4000-8000-000000000005"))
    with pytest.raises(InvalidActionError, match="valid UUID"):
        turn.rangers[0].shoot("not-a-uuid", expected_cell=(0, 0))

    turn._seal()
    with pytest.raises(TurnClosedError):
        turn.workers[0].harvest()
    with pytest.raises(TurnClosedError):
        _ = turn.plan


async def test_async_turn_submits_with_same_control_interface() -> None:
    submitted = []

    async def submitter(plan, key):
        submitted.append((plan, key))
        return accepted()

    turn = AsyncTurn(tick=9, state=make_state(), submitter=submitter)
    turn.workers[0].harvest()
    response = await turn.submit(idempotency_key="async-turn-0001")

    assert response.accepted is True
    assert submitted[0][0].unit_actions[turn.workers[0].id].type == "HARVEST"
    assert submitted[0][1] == "async-turn-0001"
