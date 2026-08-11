# Telemetry design

This document defines the optional telemetry capture layer shipped by this SDK fork.
Telemetry is disabled by default and must never change game decisions or command delivery.

## Activation

| Environment variable | Purpose | Default |
| --- | --- | --- |
| `ARENA_HERO_TELEMETRY_ENDPOINT` | HTTP endpoint receiving `POST {"events": [...]}` | empty (disabled) |
| `ARENA_HERO_TENANT` | Caller-provided tenant label | `unknown` |
| `ARENA_HERO_MODE` | `production` or `simulation` | `production` |

The sink adds `tenant`, `instance`, `ts`, and `mode` to every event. The instance value
is a short API-key suffix for correlation; the complete credential is never emitted.

## Failure isolation

- Emission uses a bounded in-memory queue and never waits for network I/O.
- A full queue drops the new event and increments an internal counter.
- HTTP failures are reported to stderr and do not escape the telemetry thread.
- A stopped telemetry thread is restarted on the next emission.
- Shutdown waits for a bounded interval and never blocks the game loop indefinitely.

## Events

### `register`

Emitted when a client is created. Fields include `api_key_tail`, `base_url`,
`sdk_version`, `pid`, and `platform`.

### `connection`

Emitted for WebSocket connection state. `status` is `up` or `error`; failures include a
short error description.

### `tick_summary`

Emitted after a player state is materialized. Existing fields are additive and optional so
older consumers can ignore newer data.

```text
tick, status, resources, population,
core: [x, y] | null,
units: int,
controlled_by_type: {TYPE: n, ...},
visible_enemies: int,
state_bytes, parse_ms, prev_decision_ms,
resource_cells: [[x, y], ...],
obstacle_cells: [[x, y], ...],
units_seen: [[id, type, controlled, x, y, hp], ...],
enemy_cores: [[x, y, owner], ...]
```

### `disconnected`

Emitted when the client closes.

## Compatibility

- Existing field meanings are stable; new fields are optional and additive.
- Missing `controlled_by_type` means the older client did not report composition, not that
  every count is zero.
- Telemetry consumers must not feed reporting success or failure back into decisions.
