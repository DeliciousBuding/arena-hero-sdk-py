# Arena Hero SDK contributor rules

## Scope

This repository is the official Python SDK for Arena Hero: wire/Turn/Action contracts,
sync and async clients, telemetry, configuration overrides, and the Agent I/O subprocess
protocol (`arena_hero.agent.io.v1`). The decision engine belongs to `arena-hero-agent`;
simulation and benchmarking belong to `arena-hero-lab`.

## Architecture boundaries

- Keep protocol models (`models`, `actions`, `rules`, `turn`) authoritative and shared;
  downstream repositories must not duplicate them.
- Generated artifacts under `generated/` are produced by `scripts/generate_agent_io.py`
  from the JSON Schemas; regenerate instead of hand-editing.
- The Agent I/O runner/child pair communicates only through the versioned envelope
  protocol; do not add out-of-band channels.
- Telemetry emission must remain failure-isolated: SDK callers never crash on
  telemetry errors.

## Safety boundaries

- Never commit API keys, tokens, cookies, private endpoints, local absolute paths, or
  production runtime data.
- Tests and examples must run offline against fixtures; no live game endpoints.

## Quality gates

```bash
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run bandit -r src -c pyproject.toml
uv run pytest            # coverage gate: --cov-fail-under=90
```

Run all gates from a clean worktree before integration. Docs live in `docs/`
(`quickstart.md`, `api-reference.md`, `telemetry-design.md`); keep them in sync
with public API changes.
