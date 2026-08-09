"""Telemetry sink 的 mode 行为（agent-ecosystem-v1 P1：register 事件带 mode）。"""

import pytest

from arena_hero.telemetry import (
    HttpTelemetrySink,
    TELEMETRY_ENDPOINT_ENV,
    TELEMETRY_MODE_ENV,
    _read_mode,
    build_telemetry,
    identity_event,
)


def test_read_mode_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TELEMETRY_MODE_ENV, raising=False)
    assert _read_mode() == "production"


def test_read_mode_accepts_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TELEMETRY_MODE_ENV, "production")
    assert _read_mode() == "production"


def test_read_mode_accepts_simulation_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TELEMETRY_MODE_ENV, "SIMULATION")
    assert _read_mode() == "simulation"


def test_read_mode_rejects_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TELEMETRY_MODE_ENV, "bogus")
    assert _read_mode() == "production"


def test_identity_event_omits_mode_by_default() -> None:
    event = identity_event(api_key="abc123def456", base_url="wss://example", pid=1)
    assert event["event"] == "register"
    assert "mode" not in event


def test_identity_event_includes_mode_when_given() -> None:
    event = identity_event(
        api_key="abc123def456",
        base_url="wss://example",
        pid=1,
        mode="simulation",
    )
    assert event["mode"] == "simulation"


def test_build_telemetry_injects_mode_into_every_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TELEMETRY_ENDPOINT_ENV, "http://127.0.0.1:9/ingest")
    monkeypatch.setenv(TELEMETRY_MODE_ENV, "simulation")
    monkeypatch.setattr(HttpTelemetrySink, "_run", lambda self: None)  # 不真的发网络

    sink = build_telemetry(api_key="abc123def456", base_url="wss://example")
    try:
        sink.emit(identity_event(api_key="abc123def456", base_url="wss://example", pid=1))
        # 同步构造的 payload 已带 mode（后台线程被替换为 no-op，无竞态）
        payload = sink._queue.get_nowait()  # noqa: SLF001
        assert payload["mode"] == "simulation"
        assert payload["tenant"] == "unknown"
        assert payload["instance"] == "def456"
    finally:
        sink.close()
