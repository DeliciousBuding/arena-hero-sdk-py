"""Synchronous Arena Hero client."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Mapping
from types import MappingProxyType

import httpx
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect
from websockets.sync.connection import Connection

from ._client_common import (
    DEFAULT_BASE_URL,
    build_config,
    check_close_exception,
    check_handshake_exception,
    jitter,
    validate_idempotency_key,
)
from ._protocol import api_error, encode_plan, parse_accepted, parse_stream_message
from .actions import Accepted, CommandPlan
from .enums import CommandSource
from .errors import ConfigurationError, ProtocolError, TransportError
from .models import PlayerState, Received, Tick
from .telemetry import TelemetrySink, build_telemetry, identity_event
from .turn import Turn

SyncGameEvent = Tick | Turn | Received


class ArenaHeroClient:
    """Synchronous client for writing an explicit Arena Hero game loop."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        websocket_url: str | None = None,
        request_timeout: float = 5.0,
        request_retries: int = 2,
        reconnect_min_delay: float = 0.25,
        reconnect_max_delay: float = 5.0,
        max_message_size: int = 2 * 1024 * 1024,
    ) -> None:
        """Initialize the client from explicit parameters.

        The SDK never reads the API key or endpoint from environment variables.
        """

        self._config = build_config(
            api_key=api_key,
            base_url=base_url,
            websocket_url=websocket_url,
            request_timeout=request_timeout,
            request_retries=request_retries,
            reconnect_min_delay=reconnect_min_delay,
            reconnect_max_delay=reconnect_max_delay,
            max_message_size=max_message_size,
        )
        self._http = httpx.Client(
            headers=self._config.headers,
            timeout=self._config.request_timeout,
        )
        self._closed = False
        self._iterating = False
        self._socket: Connection | None = None
        self._current_tick: int | None = None
        self._active_turn: Turn | None = None
        self._latest_receipts: dict[CommandSource, Received] = {}
        self._telemetry: TelemetrySink = build_telemetry(
            api_key=api_key,
            base_url=base_url,
        )
        self._telemetry.emit(identity_event(api_key=api_key, base_url=base_url, pid=os.getpid()))

    def __enter__(self) -> ArenaHeroClient:
        """Enter the client context."""

        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Close network resources when leaving the context."""

        self.close()

    def __iter__(self) -> Iterator[SyncGameEvent]:
        """Iterate over the complete game event stream."""

        return self.events()

    @property
    def latest_receipts(self) -> Mapping[CommandSource, Received]:
        """Return the latest current-Tick plan received for each source."""

        return MappingProxyType(dict(self._latest_receipts))

    def events(self) -> Iterator[SyncGameEvent]:
        """Yield ``Tick``, actionable ``Turn``, and ``Received`` messages.

        The iterator reconnects with bounded exponential backoff. Only one
        event or Turn iterator may consume a client at a time.
        """

        self._start_iteration()
        delay = self._config.reconnect_min_delay
        try:
            while not self._closed:
                override_delay: float | None = None
                try:
                    with connect(
                        self._config.websocket_url,
                        additional_headers=self._config.headers,
                        compression=None,
                        ping_interval=20,
                        ping_timeout=60,
                        max_size=self._config.max_message_size,
                    ) as websocket:
                        self._socket = websocket
                        delay = self._config.reconnect_min_delay
                        self._telemetry.emit({"event": "connection", "status": "up"})
                        for raw_message in websocket:
                            # 遥测旁路（telemetry-v2）：状态消息体积 + 解析耗时
                            # 随 tick_summary 上报；不参与决策语义，纯计时零侵入。
                            parse_started = time.monotonic()
                            message = parse_stream_message(raw_message)
                            parse_ms = (time.monotonic() - parse_started) * 1000.0
                            raw_bytes = (
                                len(raw_message.encode("utf-8"))
                                if isinstance(raw_message, str)
                                else len(raw_message)
                            )
                            yield self._materialize(
                                message,
                                raw_bytes=raw_bytes,
                                parse_ms=parse_ms,
                            )
                        if websocket.close_code == 1000:
                            return
                except InvalidStatus as exc:
                    self._telemetry.emit(
                        {
                            "event": "connection",
                            "status": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    override_delay = check_handshake_exception(exc)
                except ConnectionClosed as exc:
                    self._telemetry.emit(
                        {
                            "event": "connection",
                            "status": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    check_close_exception(exc)
                except (OSError, TimeoutError) as exc:
                    self._telemetry.emit(
                        {
                            "event": "connection",
                            "status": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    if self._closed:
                        return
                finally:
                    self._socket = None
                if self._closed:
                    return
                sleep_for = override_delay or jitter(delay)
                time.sleep(sleep_for)
                delay = min(delay * 2, self._config.reconnect_max_delay)
        finally:
            self._iterating = False
            if self._closed and self._active_turn is not None:
                self._active_turn._seal()

    def turns(self) -> Iterator[Turn]:
        """Yield each actionable Tick once, while still processing receipts."""

        last_tick: int | None = None
        for event in self.events():
            if isinstance(event, Turn) and event.tick != last_tick:
                last_tick = event.tick
                yield event

    def submit(
        self,
        plan: CommandPlan,
        *,
        idempotency_key: str | None = None,
    ) -> Accepted:
        """Submit one complete AGENT plan with safe exact-body retries."""

        self._ensure_open()
        key = validate_idempotency_key(idempotency_key, plan.tick)
        body = encode_plan(plan)
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": key,
        }
        last_error: httpx.TransportError | None = None
        attempts = self._config.request_retries + 1
        for attempt in range(attempts):
            try:
                response = self._http.post(
                    self._config.command_url,
                    content=body,
                    headers=headers,
                )
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code == 202:
                    return parse_accepted(response.content)
                if response.status_code not in {502, 503, 504}:
                    raise api_error(response.status_code, response.content)
            if attempt + 1 < attempts:
                time.sleep(min(0.1 * (2**attempt), 0.5))
        raise TransportError(
            "command submission failed after safe retries"
        ) from last_error

    def close(self) -> None:
        """Close the active WebSocket and HTTP connection pool."""

        if self._closed:
            return
        self._closed = True
        if self._active_turn is not None:
            self._active_turn._seal()
        if self._socket is not None:
            self._socket.close()
        self._http.close()
        self._telemetry.emit({"event": "disconnected"})
        self._telemetry.close()

    def _start_iteration(self) -> None:
        self._ensure_open()
        if self._iterating:
            raise ConfigurationError("only one event iterator may run per client")
        self._iterating = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise ConfigurationError("the client is closed")

    def _materialize(
        self,
        message: Tick | PlayerState | Received,
        *,
        raw_bytes: int | None = None,
        parse_ms: float | None = None,
    ) -> SyncGameEvent:
        if isinstance(message, Tick):
            if self._current_tick != message.tick:
                self._latest_receipts.clear()
            self._current_tick = message.tick
            if self._active_turn is not None:
                self._active_turn._seal()
                self._active_turn = None
            return message
        if isinstance(message, Received):
            if self._current_tick == message.tick:
                self._latest_receipts[message.source] = message
            return message
        if self._current_tick is None:
            raise ProtocolError("state arrived before tick")
        if self._active_turn is not None:
            self._active_turn._seal()
        controlled_core = next(
            (o for o in message.objects if o.kind == "CORE" and o.controlled),
            None,
        )
        units = [o for o in message.objects if o.kind == "UNIT"]
        # 决策耗时（telemetry-v2）：上一 tick 的 Turn 创建 → plan 读取/submit。
        prev_decision_ms: float | None = None
        if self._active_turn is not None and self._active_turn.decision_ms is not None:
            prev_decision_ms = round(self._active_turn.decision_ms, 3)
        # 测绘字段（python-mapping-telemetry-v1）：与 turn.py 的解析同源，
        # 直接从 message.objects 提取坐标，供 command-center ingest 落 survey
        # 测绘表（resources/obstacles/units_seen/core_hunts），t2/t3/t4 与
        # t1（TS survey:sync）四线测绘对齐。字段可选：旧 ingest 忽略即可。
        self._telemetry.emit(
            {
                "event": "tick_summary",
                "tick": self._current_tick,
                "status": message.status.value,
                "resources": message.resources,
                "population": message.population,
                "core": list(controlled_core.position) if controlled_core else None,
                "units": len(units),
                "visible_enemies": len([u for u in units if not u.controlled]),
                # telemetry-v2（2026-08-09）：状态消息体积/解析耗时/上 tick
                # 决策耗时。向后兼容：旧 ingest 忽略新字段即可。
                "state_bytes": raw_bytes,
                "parse_ms": round(parse_ms, 3) if parse_ms is not None else None,
                "prev_decision_ms": prev_decision_ms,
                "resource_cells": [
                    list(pos)
                    for batch in message.objects
                    if batch.kind == "RESOURCE"
                    for pos in batch.positions
                ],
                "obstacle_cells": [
                    list(pos)
                    for batch in message.objects
                    if batch.kind == "OBSTACLE"
                    for pos in batch.positions
                ],
                "units_seen": [
                    [
                        str(u.id),
                        u.unit_type.value,
                        int(u.controlled),
                        u.position[0],
                        u.position[1],
                        u.hp,
                    ]
                    for u in units
                ],
                "enemy_cores": [
                    [o.position[0], o.position[1], o.owner_username]
                    for o in message.objects
                    if o.kind == "CORE" and not o.controlled
                ],
            }
        )
        turn = Turn(
            tick=self._current_tick,
            state=message,
            submitter=lambda plan, key: self.submit(
                plan,
                idempotency_key=key,
            ),
        )
        self._active_turn = turn
        return turn
