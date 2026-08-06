"""Stable public helpers for current Arena Hero game rules."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from .enums import UnitType

CORE_RESOURCE_CAPACITY_PER_UNIT: Final = 5
CORE_RESOURCE_MINIMUM_CAPACITY: Final = 10
UNIT_BASE_COSTS: Final[Mapping[UnitType, int]] = MappingProxyType(
    {
        UnitType.WORKER: 5,
        UnitType.VANGUARD: 10,
        UnitType.RANGER: 12,
    }
)


def core_resource_capacity(population: int) -> int:
    """Return the Core storage capacity for a living Unit population."""

    if population < 0:
        raise ValueError("population must not be negative")
    return max(
        CORE_RESOURCE_MINIMUM_CAPACITY,
        population * CORE_RESOURCE_CAPACITY_PER_UNIT,
    )


def unit_cost(unit_type: UnitType, population: int) -> int:
    """Return the exact production price at the current living population.

    Units 1-20 use the base price. Units 21-25 cost 30% more, and the multiplier
    increases once more after every additional five Units. The final value is
    rounded once, with halves rounded up.
    """

    if population < 0:
        raise ValueError("population must not be negative")
    exponent = 0 if population < 20 else (population - 20) // 5 + 1
    numerator = UNIT_BASE_COSTS[unit_type] * 13**exponent
    denominator = 10**exponent
    return (2 * numerator + denominator) // (2 * denominator)
