# Changelog

## Unreleased

- Add length-framed stdin/stdout subprocess transport for arena.agent.io.v1 (ADR-0004):
  hello version negotiation, bounded recv deadlines, per-frame payload limits, bounded
  stderr diagnostics, protocol-only stdout, private temporary-directory ownership,
  environment allowlists, crash isolation, process-tree termination, and bounded cleanup.
- Add a shared conformance scenario so the trusted in-memory adapter and the subprocess
  transport produce the identical canonical semantic transcript with a stable SHA-256.
- Add a versioned replay/record envelope for transcripts (hello/episode_start/ready/decide/
  decision/error/episode_end) with deterministic replay and fail-closed parsing.

## 0.3.0a2 - 2026-08-11

- Add arena.agent.io.v1 semantic message models (hello/ready/episode_start/decide/decision/episode_end/error)
  with camelCase wire keys, strict TypeAdapter round-trip, and fail-closed unknown type/version handling.
- Add deterministic JSON Schema export to generated/agent-io/ and a schema manifest with SHA-256.
- Add deterministic TypeScript type/guard generation (generated/typescript/agent-io-v1.ts) driven by the
  Pydantic schema, with a --check mode that fails on stale artifacts.

## 0.3.0a1 - 2026-08-11

- Add optional, bounded HTTP telemetry for connection and tick summaries.
- Add external configuration overrides with no-op defaults.
- Preserve retry, idempotency, and structured error behavior across sync and async clients.
- Unify runtime version reporting and user-agent metadata.
- Add failure-isolation tests for telemetry queue pressure, network errors, thread recovery, and bounded shutdown.
