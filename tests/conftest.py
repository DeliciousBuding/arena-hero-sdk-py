"""Reusable authoritative payloads for SDK tests."""

from typing import Any


def state_payload() -> dict[str, Any]:
    """Return a representative complete player state."""

    return {
        "status": "ACTIVE",
        "resources": 42,
        "population": 3,
        "population_tier": 0,
        "upkeep_next_tick": 0,
        "champion_beacon": {"position": [0, 0]},
        "objects": [
            {
                "kind": "OBSTACLE",
                "positions": [[2, 2], [2, 3]],
            },
            {
                "kind": "RESOURCE",
                "positions": [[1, 0]],
            },
            {
                "kind": "CORE",
                "id": "00000000-0000-4000-8000-000000000001",
                "controlled": True,
                "owner_username": "arena_hero",
                "position": [0, 0],
                "hp": 5,
                "shield": 5,
                "state": "NORMAL",
            },
            {
                "kind": "UNIT",
                "id": "00000000-0000-4000-8000-000000000002",
                "controlled": True,
                "position": [1, 0],
                "hp": 2,
                "unit_type": "WORKER",
                "cargo": 0,
            },
            {
                "kind": "UNIT",
                "id": "00000000-0000-4000-8000-000000000003",
                "controlled": True,
                "position": [0, 1],
                "hp": 4,
                "unit_type": "VANGUARD",
            },
            {
                "kind": "UNIT",
                "id": "00000000-0000-4000-8000-000000000004",
                "controlled": True,
                "position": [-1, 0],
                "hp": 2,
                "unit_type": "RANGER",
            },
            {
                "kind": "UNIT",
                "id": "00000000-0000-4000-8000-000000000005",
                "controlled": False,
                "position": [-2, 0],
                "hp": 2,
                "unit_type": "RANGER",
            },
            {
                "kind": "CORE",
                "id": "00000000-0000-4000-8000-000000000007",
                "controlled": False,
                "owner_username": "rival",
                "position": [-3, 0],
                "hp": 4,
                "shield": 2,
                "state": "NORMAL",
            },
        ],
        "events": [
            {
                "event_id": "00000000-0000-4000-8000-000000000006",
                "tick": 8,
                "event_type": "A_FUTURE_EVENT",
                "reason_code": "A_FUTURE_REASON",
                "values": {"amount": 1},
            }
        ],
    }


def received_payload() -> dict[str, Any]:
    """Return a canonical current-Tick receipt."""

    return {
        "tick": 9,
        "source": "AGENT",
        "received_at": "2026-07-28T12:00:00Z",
        "plan": {
            "tick": 9,
            "unit_actions": {
                "00000000-0000-4000-8000-000000000002": {"type": "HARVEST"}
            },
        },
    }
