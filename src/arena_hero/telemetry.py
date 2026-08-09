"""Telemetry sink for the Arena Hero SDK (local fork: telemetry-sink branch).

默认 no-op：SDK 官方行为零变化。只有设置 ``ARENA_HERO_TELEMETRY_ENDPOINT``
环境变量后才启用 HTTP 上报（fire-and-forget，后台线程批量发送，失败静默
丢弃——绝不阻塞、绝不抛错影响游戏决策循环）。

事件类型：
- ``register``       client 创建（agent 身份注册）
- ``connection``     WebSocket 握手成功（status=up）或失败/重连（status=error）
- ``tick_summary``   每 tick 玩家状态摘要（资源/人口/核心位置/单位数 + 测绘
                     字段：resource_cells/obstacle_cells/units_seen/enemy_cores，
                     python-mapping-telemetry-v1，2026-08-09；+ telemetry-v2
                     字段：state_bytes/parse_ms/prev_decision_ms，2026-08-09）
- ``disconnected``   client 正常关闭

同步（client.py）与异步（async_client.py）客户端埋点一一对应。
"""

from __future__ import annotations

import json
import os
import platform
import queue
import sys
import threading
import time
from typing import Any

import httpx

TELEMETRY_ENDPOINT_ENV = "ARENA_HERO_TELEMETRY_ENDPOINT"
TELEMETRY_TENANT_ENV = "ARENA_HERO_TENANT"
TELEMETRY_MODE_ENV = "ARENA_HERO_MODE"
SDK_VERSION = "0.2.9-telemetry.2"

PRODUCTION_MODE = "production"
SIMULATION_MODE = "simulation"

_FLUSH_INTERVAL_SECONDS = 5.0
_FLUSH_BATCH_SIZE = 20


def _read_mode() -> str:
    """读取 agent 运行模式（production|simulation），缺省 production。

    只认两个合法值；非法/未设置一律回落 production（与 registry 的
    CHECK(mode IN ...) 对齐，避免脏值进台账）。
    """

    mode = os.environ.get(TELEMETRY_MODE_ENV, "").strip().lower()
    if mode not in (PRODUCTION_MODE, SIMULATION_MODE):
        return PRODUCTION_MODE
    return mode


class TelemetrySink:
    """默认 sink：什么都不做（官方 SDK 行为）。"""

    def emit(self, event: dict[str, Any]) -> None:  # noqa: ARG002
        pass

    def close(self) -> None:
        pass


class HttpTelemetrySink(TelemetrySink):
    """后台线程批量上报到 ingest 端点。"""

    def __init__(self, endpoint: str, tenant: str, instance_id: str, mode: str) -> None:
        self._endpoint = endpoint
        self._tenant = tenant
        self._instance_id = instance_id
        self._mode = mode
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=200)
        self._dropped = 0
        self._thread = threading.Thread(
            target=self._run,
            name="arena-hero-telemetry",
            daemon=True,
        )
        self._thread.start()

    def emit(self, event: dict[str, Any]) -> None:
        payload = {
            "tenant": self._tenant,
            "instance": self._instance_id,
            "ts": time.time(),
            "mode": self._mode,
            **event,
        }
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            self._dropped += 1
        if not self._thread.is_alive():
            # 自愈：上报线程意外退出时重启（不阻塞主循环）
            self._thread = threading.Thread(
                target=self._run,
                name="arena-hero-telemetry",
                daemon=True,
            )
            self._thread.start()

    def close(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=3.0)

    def _run(self) -> None:
        batch: list[dict[str, Any]] = []
        last_flush = time.monotonic()
        try:
            with httpx.Client(timeout=2.0) as client:
                while True:
                    try:
                        item = self._queue.get(timeout=0.5)
                    except queue.Empty:
                        item = None
                    if item is None:
                        self._flush(client, batch)
                        return
                    batch.append(item)
                    if len(batch) >= _FLUSH_BATCH_SIZE:
                        self._flush(client, batch)
                        batch = []
                        last_flush = time.monotonic()
                    elif (
                        batch
                        and time.monotonic() - last_flush >= _FLUSH_INTERVAL_SECONDS
                    ):
                        self._flush(client, batch)
                        batch = []
                        last_flush = time.monotonic()
        except Exception as exc:  # 遥测绝不能影响游戏：记录但不抛出
            print(
                f"telemetry_sink_thread_exited error={type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        finally:
            # 退出前把剩余事件清空，避免下次 flush 复用陈旧 batch
            try:
                while True:
                    self._queue.get_nowait()
            except queue.Empty:
                pass

    def _flush(
        self,
        client: httpx.Client,
        batch: list[dict[str, Any]],
    ) -> None:
        if not batch:
            return
        try:
            client.post(self._endpoint, json={"events": batch})
        except Exception as exc:  # 网络类错误全捕获（HTTPError/Timeout/RuntimeError）
            print(
                f"telemetry_flush_failed error={type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )


def build_telemetry(
    *,
    api_key: str,
    base_url: str,
) -> TelemetrySink:
    """按环境变量构造 sink；未配置端点时返回 no-op。"""

    endpoint = os.environ.get(TELEMETRY_ENDPOINT_ENV, "").strip()
    if not endpoint:
        return TelemetrySink()
    tenant = os.environ.get(TELEMETRY_TENANT_ENV, "unknown").strip() or "unknown"
    instance_id = api_key[-6:] if api_key else "unknown"
    mode = _read_mode()
    return HttpTelemetrySink(endpoint, tenant, instance_id, mode)


def identity_event(
    *,
    api_key: str,
    base_url: str,
    pid: int,
    mode: str | None = None,
) -> dict[str, Any]:
    """client 创建时的身份注册事件。

    ``mode``（production|simulation）可选：传入则随 register 事件上报；
    缺省省略（HttpTelemetrySink 会按 ``ARENA_HERO_MODE`` 注入同一字段）。
    """

    event = {
        "event": "register",
        "api_key_tail": api_key[-6:] if api_key else "",
        "base_url": base_url,
        "sdk_version": SDK_VERSION,
        "pid": pid,
        "platform": platform.platform(),
    }
    if mode:
        event["mode"] = mode
    return event
