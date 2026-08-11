# Changelog

## 0.3.0a1 - 2026-08-11

- Add optional, bounded HTTP telemetry for connection and tick summaries.
- Add external configuration overrides with no-op defaults.
- Preserve retry, idempotency, and structured error behavior across sync and async clients.
- Unify runtime version reporting and user-agent metadata.
- Add failure-isolation tests for telemetry queue pressure, network errors, thread recovery, and bounded shutdown.
