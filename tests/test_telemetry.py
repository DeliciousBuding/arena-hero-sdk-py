"""Telemetry sink 的 mode 行为(agent-ecosystem-v1 P1:register 事件带 mode)。"""

import threading
import time
from typing import cast

import httpx
import pytest

from arena_hero.telemetry import (
    TELEMETRY_ENDPOINT_ENV,
    TELEMETRY_MODE_ENV,
    HttpTelemetrySink,
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


def test_read_mode_accepts_simulation_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        sink.emit(
            identity_event(api_key="abc123def456", base_url="wss://example", pid=1)
        )
        # 同步构造的 payload 已带 mode(后台线程被替换为 no-op,无竞态)
        payload = cast(HttpTelemetrySink, sink)._queue.get_nowait()
        assert payload is not None
        assert payload["mode"] == "simulation"
        assert payload["tenant"] == "unknown"
        assert payload["instance"] == "def456"
    finally:
        sink.close()


def test_http_sink_flushes_with_ten_second_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WIP 交接:HTTP 上报超时 2.0→10.0,后台批量 flush 真实生效。"""
    recorded: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object | None,
        ) -> None:
            return None

        def post(self, url: str, **kwargs: object) -> None:
            recorded["posted"] = True

    monkeypatch.setattr("arena_hero.telemetry.httpx.Client", FakeClient)
    monkeypatch.setattr("arena_hero.telemetry._FLUSH_INTERVAL_SECONDS", 0.05)
    sink = HttpTelemetrySink("http://example/ingest", "tenant", "inst", "production")
    try:
        sink.emit({"event": "connection", "status": "up"})
        deadline = time.monotonic() + 2.0
        while not recorded.get("posted") and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        sink.close()
    timeout = recorded.get("timeout")
    assert isinstance(timeout, float)
    assert timeout == 10.0
    assert recorded.get("posted") is True


def test_flush_failure_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """上报失败只记 stderr,绝不向调用方抛错(遥测不阻断决策)。"""
    monkeypatch.setattr(HttpTelemetrySink, "_run", lambda self: None)
    sink = HttpTelemetrySink("http://example/ingest", "tenant", "inst", "production")

    class ExplodingClient:
        def post(self, url: str, **kwargs: object) -> None:
            raise RuntimeError("network down")

    try:
        sink._flush(
            cast(httpx.Client, ExplodingClient()),
            [{"event": "connection", "status": "up"}],
        )
    finally:
        sink.close()


def test_emit_never_raises_when_queue_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """队列满时丢事件并计数,不抛错(遥测不阻断决策)。"""
    monkeypatch.setattr(HttpTelemetrySink, "_run", lambda self: None)
    sink = HttpTelemetrySink("http://example/ingest", "tenant", "inst", "production")
    try:
        for _ in range(200):
            sink.emit({"event": "x"})
        assert sink._queue.full()
        sink.emit({"event": "y"})
        assert sink._dropped == 1
    finally:
        sink.close()


def test_emit_restarts_dead_background_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上报线程意外退出后,下一次 emit 自愈重启,不抛错。"""

    def exiting_run(self: HttpTelemetrySink) -> None:
        return None

    monkeypatch.setattr(HttpTelemetrySink, "_run", exiting_run)
    sink = HttpTelemetrySink("http://example/ingest", "tenant", "inst", "production")
    try:
        first_thread = sink._thread
        deadline = time.monotonic() + 2.0
        while first_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not first_thread.is_alive()
        sink.emit({"event": "connection", "status": "up"})
        assert sink._thread is not first_thread
        sink.emit({"event": "connection", "status": "up"})
    finally:
        sink.close()


def test_close_is_bounded_when_thread_hung(monkeypatch: pytest.MonkeyPatch) -> None:
    """close() 有界等待:后台线程卡死时也按时返回,不抛错。"""
    monkeypatch.setattr("arena_hero.telemetry._CLOSE_JOIN_TIMEOUT_SECONDS", 0.05)
    entered = threading.Event()

    def hung_run(self: HttpTelemetrySink) -> None:
        entered.set()
        threading.Event().wait(5)

    monkeypatch.setattr(HttpTelemetrySink, "_run", hung_run)
    sink = HttpTelemetrySink("http://example/ingest", "tenant", "inst", "production")
    assert entered.wait(1.0)
    started = time.monotonic()
    sink.close()
    assert time.monotonic() - started < 1.0
