# Arena Hero Python

The official typed Python SDK for [Arena Hero](https://doc.arenahero.io/).

You own the game loop. The SDK connects to the HTTP and WebSocket APIs, parses
authoritative state, exposes control methods for every Unit type and Core, and
submits one complete plan when you call `submit()`.

## Documentation

- [Quickstart](https://github.com/arena-hero/arena-hero-python/blob/main/docs/quickstart.md):
  installation, synchronous and asynchronous loops, state access, Unit control,
  and local development.
- [API reference](https://github.com/arena-hero/arena-hero-python/blob/main/docs/api-reference.md):
  every client option, Turn field, control method, model, event, enum, and
  exception.
- [Game rules and wire API](https://doc.arenahero.io/): authoritative gameplay,
  HTTP, and WebSocket behavior.

## Install

Python 3.11 or newer is required.

Install the published release:

```bash
pip install arena-hero
```

## Synchronous game loop

```python
from getpass import getpass

from arena_hero import ArenaHeroClient, Direction


api_key = getpass("Arena Hero API key: ")

with ArenaHeroClient(api_key=api_key) as game:
    for turn in game.turns():
        for worker in turn.workers:
            if worker.position in turn.resource_cells:
                worker.harvest()
            else:
                worker.move(Direction.RIGHT)

        turn.submit()
```

`worker.harvest()` and `worker.move()` only queue actions on the current
`Turn`. They do not make network requests. `turn.submit()` sends the complete
queued plan in one HTTP request.

## Asynchronous game loop

```python
import asyncio
from getpass import getpass

from arena_hero import AsyncArenaHeroClient, Direction


async def play(api_key: str) -> None:
    async with AsyncArenaHeroClient(api_key=api_key) as game:
        async for turn in game.turns():
            for vanguard in turn.vanguards:
                vanguard.sweep(Direction.LEFT)

            await turn.submit()


asyncio.run(play(getpass("Arena Hero API key: ")))
```

The synchronous and asynchronous clients use the same models and control
methods:

- `ArenaHeroClient` with `for` and `turn.submit()`
- `AsyncArenaHeroClient` with `async for` and `await turn.submit()`

Every Core state includes its owner's public `owner_username` without a leading
`@`. Display it as `f"@{core.owner_username}"`; Unit owners remain private.

## Control interfaces

Every controlled object exposes its authoritative state through `.view`.
Calling another method for the same object replaces its earlier queued action
on that Turn.

| Object | Available methods |
| --- | --- |
| `Worker` | `move`, `harvest`, `deposit`, `pickup_beacon`, `drop_beacon`, `heal`, `self_destruct`, `wait`, `clear_action` |
| `Vanguard` | `move`, `sweep`, `pickup_beacon`, `drop_beacon`, `heal`, `self_destruct`, `wait`, `clear_action` |
| `Ranger` | `move`, `shoot`, `pickup_beacon`, `drop_beacon`, `heal`, `self_destruct`, `wait`, `clear_action` |
| `Core` | `spawn`, `heal`, `repair_shield`, `start_move`, `cancel_move`, `pickup_beacon`, `drop_beacon`, `wait`, `clear_action` |

Useful Turn data:

```python
turn.tick
turn.state
turn.resources
turn.resource_capacity
turn.resource_space
turn.core
turn.units
turn.workers
turn.vanguards
turn.rangers
turn.visible_enemies
turn.resource_cells
turn.obstacle_cells
turn.beacon
turn.events
turn.plan
```

Core storage has a minimum capacity of 10, then accepts 5 resources per living
Unit: `resource_capacity` is `max(10, state.population * 5)`.
`resource_space` is the non-negative amount a Worker can still deposit. If
population falls, Core resources above the new capacity are destroyed
immediately.

`turn.state.upkeep_next_tick` is charged before movement. If the Core cannot
pay it all, each unpaid point deals one HP of damage to excess Units instead of
the Core. The 19 Units nearest the Core are protected; farther Units take the
damage first. Read `UNIT_DAMAGED` events whose `reason_code` is
`"UPKEEP_DEFICIT"` to see the affected Unit, damage, and remaining HP.

`turn.resource_cells` includes visible natural points and Worker cargo piles.
Pile amounts are not exposed; a partially recovered pile remains in the set.
Use `event.resource_amount` and `event.harvest_source` on `turn.events` to read
private cargo-drop, recovery, deposit, and overflow-destruction amounts without
unpacking `values` yourself.

### Worker

```python
worker = turn.workers[0]

worker.move(Direction.UP)
worker.harvest()
worker.deposit()  # stores what fits; any remainder stays on the Worker
worker.pickup_beacon()
worker.drop_beacon()
worker.heal()
worker.self_destruct()
worker.wait()
worker.clear_action()
```

If a Worker dies, including through unpaid upkeep or `self_destruct()`, its
complete cargo amount becomes a recoverable resource pile on its final cell.

### Vanguard

```python
vanguard = turn.vanguards[0]

vanguard.move(Direction.DOWN)
vanguard.sweep(Direction.RIGHT)
vanguard.pickup_beacon()
vanguard.drop_beacon()
vanguard.heal()
vanguard.self_destruct()
vanguard.wait()
```

### Ranger

Pass a visible Unit or Core to derive both the target UUID and expected cell:

```python
ranger = turn.rangers[0]
enemy = turn.visible_enemies[0]

ranger.shoot(enemy)
```

The server can hit a target 1-3 cells away on the same row, column, or exact
45-degree diagonal. A relative offset such as `(3, 3)` is in range; `(2, 1)`
is not. Only obstacles on the intermediate shot cells block fire. Units, Cores,
and obstacles beside a diagonal do not block it.

When you only have a UUID, provide the expected cell:

```python
from uuid import UUID

target_id = UUID("8d60b600-78d4-4aba-83fd-4e5e27b88c9d")
ranger.shoot(target_id, expected_cell=(120, 85))
```

### Core

The Core is absent only during initial admission or a failed-spawn retry. Core
destruction has no cooldown and normally creates a replacement in the same Tick.

```python
if turn.core is not None:
    turn.core.spawn(UnitType.WORKER)
    turn.core.heal()
    turn.core.repair_shield()
    turn.core.start_move(Direction.RIGHT)
    turn.core.cancel_move()
    turn.core.pickup_beacon()
    turn.core.drop_beacon()
    turn.core.wait()
```

`heal()` resolves after combat and spends one Core resource per HP actually
recovered, up to full HP. A Unit must still be alive on the same cell as its
own stationary Core. Unit heals spend resources before the Core action. It is
valid to queue a heal while HP is full or resources are currently empty:
damage and captured Core resources from that Tick are resolved first. A fatal
hit cannot be healed.

## Complete event stream

`game.turns()` yields each actionable Tick once. Use `game.events()` when the
program also needs `tick` notices and canonical plans submitted by this or
another client:

```python
from arena_hero import Received, Tick, Turn


with ArenaHeroClient(api_key=api_key) as game:
    for event in game.events():
        if isinstance(event, Tick):
            current_tick = event.tick
        elif isinstance(event, Turn):
            # Queue actions, then submit one complete plan.
            event.submit()
        elif isinstance(event, Received):
            latest_received = event
```

The latest current-Tick `AGENT` and `MANUAL` plans are also available through
`game.latest_receipts`. A new `Received` value replaces the earlier value for
that source.

Use either `events()` or `turns()` on a client, not both at the same time.

## Direct plan submission

Advanced callers can build and submit the exact public protocol model:

```python
from uuid import UUID

from arena_hero import CommandPlan, Direction, MoveAction


plan = CommandPlan(
    tick=turn.tick,
    unit_actions={
        UUID("9d3e4941-2816-4a39-a220-df8cd95e877d"): MoveAction(direction=Direction.UP)
    },
)

receipt = game.submit(plan)
```

For asynchronous code, use `await game.submit(plan)`.

## Connection behavior

The SDK:

- sends the API key only in the `Authorization` header;
- never reads credentials or endpoints from environment variables;
- disables WebSocket message compression to match the server contract;
- uses protocol Ping/Pong automatically;
- reconnects with jittered exponential backoff from 250 ms to 5 seconds;
- retries an uncertain HTTP submission with the same idempotency key and exact
  request bytes;
- stops on WebSocket close code `1008`;
- treats each `state` as a complete replacement;
- preserves unknown resolution event names and reason codes as strings.

The default backend is `https://api.arenahero.io`. Pass test endpoints
explicitly:

```python
client = ArenaHeroClient(
    api_key=api_key,
    base_url="http://localhost:8080",
    websocket_url="ws://localhost:8080/api/v1/game/ws",
)
```

## Errors

All SDK exceptions inherit from `ArenaHeroError`.

| Exception | Meaning |
| --- | --- |
| `ConfigurationError` | Invalid constructor option or idempotency key |
| `AuthenticationError` | WebSocket authentication was rejected |
| `PolicyViolationError` | WebSocket closed with code `1008` |
| `ProtocolError` | The server returned an invalid public-protocol message |
| `APIError` | The command API returned a structured rejection |
| `TransportError` | A network operation failed after safe retries |
| `TurnClosedError` | Code tried to change a Turn after a newer Tick arrived |
| `InvalidActionError` | A local action target or owned Unit was invalid |

Dynamic gameplay failures are not exceptions. They arrive in the next
`Turn.events` as normal resolution results.

## Development

This project uses `uv`, a `src/` layout, and a locked development environment.

```bash
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run bandit -r src -c pyproject.toml
uv run pytest
uv build
```

The public game protocol is documented at
[doc.arenahero.io](https://doc.arenahero.io/).

## License

[Apache License 2.0](LICENSE)
