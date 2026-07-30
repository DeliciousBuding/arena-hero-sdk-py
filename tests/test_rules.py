"""Public game-rule helper tests."""

import pytest

from arena_hero import (
    CORE_RESOURCE_CAPACITY_PER_UNIT,
    CORE_RESOURCE_MINIMUM_CAPACITY,
    core_resource_capacity,
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
