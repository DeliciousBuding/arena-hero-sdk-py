"""Public game-rule helper tests."""

import pytest

from arena_hero import CORE_RESOURCE_CAPACITY_PER_UNIT, core_resource_capacity


def test_core_resource_capacity_uses_five_resources_per_unit() -> None:
    assert CORE_RESOURCE_CAPACITY_PER_UNIT == 5
    assert core_resource_capacity(0) == 0
    assert core_resource_capacity(1) == 5
    assert core_resource_capacity(20) == 100


def test_core_resource_capacity_rejects_negative_population() -> None:
    with pytest.raises(ValueError, match="population must not be negative"):
        core_resource_capacity(-1)
