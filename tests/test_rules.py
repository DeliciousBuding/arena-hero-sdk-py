"""Public game-rule helper tests."""

from typing import Any

import pytest

from arena_hero import (
    CORE_RESOURCE_CAPACITY_PER_UNIT,
    CORE_RESOURCE_MINIMUM_CAPACITY,
    UNIT_BASE_COSTS,
    UnitType,
    core_resource_capacity,
    unit_cost,
)


def test_core_resource_capacity_has_minimum_then_scales_per_unit() -> None:
    assert CORE_RESOURCE_CAPACITY_PER_UNIT == 5
    assert CORE_RESOURCE_MINIMUM_CAPACITY == 10
    assert core_resource_capacity(0) == 10
    assert core_resource_capacity(1) == 10
    assert core_resource_capacity(2) == 10
    assert core_resource_capacity(3) == 15
    assert core_resource_capacity(20) == 100


def test_core_resource_capacity_rejects_negative_population() -> None:
    with pytest.raises(ValueError, match="population must not be negative"):
        core_resource_capacity(-1)


@pytest.mark.parametrize(
    ("population", "worker", "vanguard", "ranger"),
    [
        (0, 5, 10, 12),
        (19, 5, 10, 12),
        (20, 7, 13, 16),
        (24, 7, 13, 16),
        (25, 8, 17, 20),
        (30, 11, 22, 26),
        (100, 433, 865, 1038),
    ],
)
def test_unit_cost_uses_exact_tiered_growth_and_half_up_rounding(
    population: int,
    worker: int,
    vanguard: int,
    ranger: int,
) -> None:
    assert unit_cost(UnitType.WORKER, population) == worker
    assert unit_cost(UnitType.VANGUARD, population) == vanguard
    assert unit_cost(UnitType.RANGER, population) == ranger


def test_unit_cost_constants_are_read_only_and_reject_negative_population() -> None:
    assert UNIT_BASE_COSTS == {
        UnitType.WORKER: 5,
        UnitType.VANGUARD: 10,
        UnitType.RANGER: 12,
    }
    mutable_costs: Any = UNIT_BASE_COSTS
    with pytest.raises(TypeError):
        mutable_costs[UnitType.WORKER] = 1
    with pytest.raises(ValueError, match="population"):
        unit_cost(UnitType.WORKER, -1)
