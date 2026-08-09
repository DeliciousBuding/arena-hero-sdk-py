"""Telemetry sink for the Arena Hero SDK (local fork: telemetry-sink branch).

默认 no-op：SDK 官方行为零变化。只有设置 ``ARENA_HERO_TELEMETRY_ENDPOINT``
环境变量后才启用 HTTP 上报（fire-and-forget，后台线程批量发送，失败静默
丢弃——绝不阻塞、绝不抛错影响游戏决策循环）。

事件类型：
- ``register``       client 创建（agent 身份注册）
- ``connection``     WebSocket 握手成功（status=up）或失败/重连（status=error）
- ``tick_summary``   每 tick 玩家状态摘要（资源/人口/核心位置/单位数）
- ``disconnected``   client 正常关闭
"""

from __future__ import annotations

import json
import os
import platform
import queue
import threading
import time
from typing import Any

import httpx

TELEMETRY_ENDPOINT_ENV = "ARENA_HERO_TELEMETRY_ENDPOINT"
TELEMETRY_TENANT_ENV = "ARENA_HERO_TENANT"
SDK_VERSION = "0.2.9-telemetry.1"

_FLUSH_INTERVAL_SECONDS = 5.0
_FLUSH_BATCH_SIZE = 20


class TelemetrySink:
    """默认 sink：什么都不做（官方 SDK 行为）。"""

    def emit(self, event: dict[str, Any]) -> None:  # noqa: ARG002
        pass

    def close(self) -> None:
        pass


class HttpTelemetrySink(TelemetrySink):
    """后台线程批量上报到 ingest 端点。"""

    def __init__(self, endpoint: str, tenant: str, instance_id: str) -> None:
        self._endpoint = endpoint
        self._tenant = tenant
        self._instance_id = instance_id
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
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
            **event,
        }
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            self._dropped += 1

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
        except Exception:  # 上报失败静默：遥测绝不能影响游戏
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
        except httpx.HTTPError:
            pass


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
    return HttpTelemetrySink(endpoint, tenant, instance_id)


def identity_event(*, api_key: str, base_url: str, pid: int) -> dict[str, Any]:
    """client 创建时的身份注册事件。"""

    return {
        "event": "register",
        "api_key_tail": api_key[-6:] if api_key else "",
        "base_url": base_url,
        "sdk_version": SDK_VERSION,
        "pid": pid,
        "platform": platform.platform(),
    }
