"""Synchronous and asynchronous client behavior tests."""

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from conftest import received_payload, state_payload
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

from arena_hero import (
    APIError,
    ArenaHeroClient,
    AsyncArenaHeroClient,
    AsyncTurn,
    AuthenticationError,
    CommandPlan,
    ConfigurationError,
    ProtocolError,
    Received,
    Tick,
    TransportError,
    Turn,
    TurnClosedError,
)
from arena_hero._client_common import (
    check_close_exception,
    check_handshake_exception,
    close_code,
    jitter,
)
from arena_hero.errors import PolicyViolationError


class FakeSyncSocket:
    """Small sync WebSocket stand-in for the public event loop."""

    def __init__(self, messages: list[str], close_code: int = 1000) -> None:
        self.messages = messages
        self.close_code = close_code

    def __enter__(self) -> "FakeSyncSocket":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def __iter__(self) -> Iterator[str]:
        return iter(self.messages)

    def close(self) -> None:
        self.close_code = 1000


class FakeAsyncSocket:
    """Small async WebSocket stand-in for the public event loop."""

    def __init__(self, messages: list[str], close_code: int = 1000) -> None:
        self.messages = messages
        self.close_code = close_code

    async def __aenter__(self) -> "FakeAsyncSocket":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def __aiter__(self) -> AsyncIterator[str]:
        for message in self.messages:
            yield message

    async def close(self) -> None:
        self.close_code = 1000


def stream_messages() -> list[str]:
    """Return one complete actionable stream sequence."""

    return [
        '{"type":"tick","data":9}',
        json.dumps({"type": "state", "data": state_payload()}),
        json.dumps({"type": "received", "data": received_payload()}),
    ]


def replace_sync_http(
    client: ArenaHeroClient,
    handler: httpx.MockTransport,
) -> None:
    """Replace the real sync pool with an in-memory transport."""

    client._http.close()
    client._http = httpx.Client(transport=handler)


async def replace_async_http(
    client: AsyncArenaHeroClient,
    handler: httpx.MockTransport,
) -> None:
    """Replace the real async pool with an in-memory transport."""

    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=handler)


def test_sync_event_loop_exposes_tick_turn_and_external_receipt(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_connect(url: str, **kwargs: Any) -> FakeSyncSocket:
        captured["url"] = url
        captured["headers"] = kwargs["additional_headers"]
        return FakeSyncSocket(stream_messages())

    monkeypatch.setattr("arena_hero.client.connect", fake_connect)
    with ArenaHeroClient(api_key="explicit-key") as client:
        events = list(client.events())

        assert isinstance(events[0], Tick)
        assert isinstance(events[1], Turn)
        assert isinstance(events[2], Received)
        assert client.latest_receipts[events[2].source] == events[2]

    assert captured["url"] == "wss://api.arenahero.io/api/v1/game/ws"
    assert captured["headers"]["Authorization"] == "Bearer explicit-key"


def test_sync_iterator_alias_and_turn_filter(monkeypatch) -> None:
    calls = 0

    def fake_connect(_url: str, **_kwargs: Any) -> FakeSyncSocket:
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeSyncSocket(stream_messages()[:2], close_code=1001)
        return FakeSyncSocket(stream_messages()[:2])

    monkeypatch.setattr("arena_hero.client.connect", fake_connect)
    monkeypatch.setattr("arena_hero.client.time.sleep", lambda _delay: None)
    with ArenaHeroClient(api_key="explicit-key") as client:
        turns = list(client.turns())

    assert len(turns) == 1
    assert turns[0].tick == 9


def test_sync_reconnects_after_network_failure(monkeypatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def fake_connect(_url: str, **_kwargs: Any) -> FakeSyncSocket:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("offline")
        return FakeSyncSocket(['{"type":"tick","data":9}'])

    monkeypatch.setattr("arena_hero.client.connect", fake_connect)
    monkeypatch.setattr("arena_hero.client.time.sleep", sleeps.append)
    monkeypatch.setattr("arena_hero.client.jitter", lambda delay: delay)
    with ArenaHeroClient(api_key="explicit-key") as client:
        events = list(iter(client))

    assert events == [Tick(tick=9)]
    assert sleeps == [0.25]


async def test_async_event_loop_matches_sync_surface(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_connect(url: str, **kwargs: Any) -> FakeAsyncSocket:
        captured["url"] = url
        captured["headers"] = kwargs["additional_headers"]
        return FakeAsyncSocket(stream_messages())

    monkeypatch.setattr("arena_hero.async_client.connect", fake_connect)
    async with AsyncArenaHeroClient(api_key="explicit-key") as client:
        events = [event async for event in client.events()]

        assert isinstance(events[0], Tick)
        assert isinstance(events[1], AsyncTurn)
        assert isinstance(events[2], Received)
        assert client.latest_receipts[events[2].source] == events[2]

    assert captured["url"] == "wss://api.arenahero.io/api/v1/game/ws"
    assert captured["headers"]["Authorization"] == "Bearer explicit-key"


async def test_async_iterator_alias_turn_filter_and_reconnect(monkeypatch) -> None:
    calls = 0

    def fake_connect(_url: str, **_kwargs: Any) -> FakeAsyncSocket:
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeAsyncSocket(stream_messages()[:2], close_code=1001)
        return FakeAsyncSocket(stream_messages()[:2])

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("arena_hero.async_client.connect", fake_connect)
    monkeypatch.setattr("arena_hero.async_client.asyncio.sleep", no_sleep)
    async with AsyncArenaHeroClient(api_key="explicit-key") as client:
        assert client.__aiter__() is not None
        turns = [turn async for turn in client.turns()]

    assert len(turns) == 1
    assert turns[0].tick == 9


def test_sync_submit_retries_the_exact_body_and_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, json={"error": "UNAVAILABLE"})
        return httpx.Response(
            202,
            json={
                "accepted": True,
                "tick": 9,
                "source": "AGENT",
                "received_at": "2026-07-28T12:00:00Z",
            },
        )

    client = ArenaHeroClient(api_key="explicit-key", request_retries=1)
    replace_sync_http(client, httpx.MockTransport(handler))
    response = client.submit(
        CommandPlan(tick=9),
        idempotency_key="sync-submit-0001",
    )
    client.close()

    assert response.accepted is True
    assert requests[0].content == requests[1].content
    assert requests[0].headers["Idempotency-Key"] == "sync-submit-0001"
    assert requests[1].headers["Idempotency-Key"] == "sync-submit-0001"


def test_sync_submit_generates_a_valid_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "accepted": True,
                "tick": 9,
                "source": "AGENT",
                "received_at": "2026-07-28T12:00:00Z",
            },
        )

    client = ArenaHeroClient(api_key="explicit-key")
    replace_sync_http(client, httpx.MockTransport(handler))
    client.submit(CommandPlan(tick=9))
    client.close()

    generated = requests[0].headers["Idempotency-Key"]
    assert generated.startswith("arena-9-")
    assert len(generated) >= 8


async def test_async_submit_matches_sync_behavior() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "accepted": True,
                "tick": 9,
                "source": "AGENT",
                "received_at": "2026-07-28T12:00:00Z",
            },
        )

    client = AsyncArenaHeroClient(api_key="explicit-key")
    await replace_async_http(client, httpx.MockTransport(handler))
    response = await client.submit(
        CommandPlan(tick=9),
        idempotency_key="async-submit-0001",
    )
    await client.close()

    assert response.accepted is True
    assert requests[0].headers["Idempotency-Key"] == "async-submit-0001"


async def test_async_submit_surfaces_api_and_transport_errors() -> None:
    api_client = AsyncArenaHeroClient(api_key="explicit-key", request_retries=0)
    await replace_async_http(
        api_client,
        httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={"error": "TICK_MISMATCH"},
            )
        ),
    )
    with pytest.raises(APIError, match="TICK_MISMATCH"):
        await api_client.submit(CommandPlan(tick=9))
    await api_client.close()

    def failed_transport(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    transport_client = AsyncArenaHeroClient(
        api_key="explicit-key",
        request_retries=1,
    )
    await replace_async_http(
        transport_client,
        httpx.MockTransport(failed_transport),
    )
    with pytest.raises(TransportError, match="safe retries"):
        await transport_client.submit(CommandPlan(tick=9))
    await transport_client.close()


def test_submit_surfaces_api_and_exhausted_transport_errors() -> None:
    api_client = ArenaHeroClient(api_key="explicit-key", request_retries=0)
    replace_sync_http(
        api_client,
        httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={"error": "TICK_MISMATCH", "expected_tick": 10},
            )
        ),
    )
    with pytest.raises(APIError, match="TICK_MISMATCH"):
        api_client.submit(CommandPlan(tick=9))
    api_client.close()

    def failed_transport(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    transport_client = ArenaHeroClient(api_key="explicit-key", request_retries=1)
    replace_sync_http(
        transport_client,
        httpx.MockTransport(failed_transport),
    )
    with pytest.raises(TransportError, match="safe retries"):
        transport_client.submit(CommandPlan(tick=9))
    transport_client.close()


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("", "api_key"),
        ("   ", "api_key"),
        ("bad\nkey", "api_key"),
        ("密钥", "api_key"),
    ],
)
def test_requires_explicit_nonempty_api_key(key: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        ArenaHeroClient(api_key=key)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"request_timeout": 0}, "request_timeout"),
        ({"request_retries": -1}, "request_retries"),
        ({"reconnect_min_delay": 0}, "reconnect_min_delay"),
        (
            {"reconnect_min_delay": 2, "reconnect_max_delay": 1},
            "reconnect_max_delay",
        ),
        ({"max_message_size": 0}, "max_message_size"),
        ({"base_url": "ftp://api.example.com"}, "base_url"),
        ({"base_url": "https://user:pass@example.com"}, "base_url"),
        ({"websocket_url": "https://api.example.com/ws"}, "websocket_url"),
        ({"websocket_url": "wss://user:pass@example.com/ws"}, "websocket_url"),
    ],
)
def test_validates_client_options(
    kwargs: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        ArenaHeroClient(api_key="explicit-key", **kwargs)


def test_custom_backend_urls_are_derived_without_environment(monkeypatch) -> None:
    monkeypatch.setenv("ARENA_HERO_API_KEY", "ignored-key")
    client = ArenaHeroClient(
        api_key="explicit-key",
        base_url="http://localhost:8080/custom/",
    )

    assert client._config.command_url == (
        "http://localhost:8080/custom/api/v1/game/commands"
    )
    assert client._config.websocket_url == ("ws://localhost:8080/custom/api/v1/game/ws")
    assert "explicit-key" not in repr(client._config)
    client.close()


@pytest.mark.parametrize(
    "key",
    ["short", "contains space", "x" * 129, "not-ascii-你好"],
)
def test_validates_caller_idempotency_keys(key: str) -> None:
    client = ArenaHeroClient(api_key="explicit-key")
    with pytest.raises(ConfigurationError):
        client.submit(CommandPlan(tick=9), idempotency_key=key)
    client.close()


def test_state_before_tick_is_a_protocol_error() -> None:
    client = ArenaHeroClient(api_key="explicit-key")
    with pytest.raises(ProtocolError, match="before tick"):
        client._materialize(
            parse_state(),
        )
    client.close()


def test_closed_and_double_iteration_guards() -> None:
    client = ArenaHeroClient(api_key="explicit-key")
    client._iterating = True
    with pytest.raises(ConfigurationError, match="only one"):
        next(client.events())
    client._iterating = False
    client.close()
    with pytest.raises(ConfigurationError, match="closed"):
        client.submit(CommandPlan(tick=9))


async def test_async_closed_and_double_iteration_guards() -> None:
    client = AsyncArenaHeroClient(api_key="explicit-key")
    client._iterating = True
    iterator = client.events()
    with pytest.raises(ConfigurationError, match="only one"):
        await anext(iterator)
    client._iterating = False
    await client.close()
    await client.close()
    with pytest.raises(ConfigurationError, match="closed"):
        await client.submit(CommandPlan(tick=9))


def test_materialization_seals_old_turn_and_clears_old_receipts() -> None:
    client = ArenaHeroClient(api_key="explicit-key")
    client._materialize(Tick(tick=9))
    turn = client._materialize(parse_state())
    receipt = Received.model_validate(received_payload())
    client._materialize(receipt)
    assert isinstance(turn, Turn)
    assert client.latest_receipts

    client._materialize(Tick(tick=10))

    with pytest.raises(TurnClosedError, match="no longer current"):
        _ = turn.plan
    assert client.latest_receipts == {}
    client.close()
    client.close()


def test_websocket_close_and_handshake_policies() -> None:
    no_close = ConnectionClosedError(None, None)
    policy = ConnectionClosedError(Close(1008, "policy"), None)
    retryable = ConnectionClosedError(Close(1013, "retry"), None)

    assert close_code(no_close) is None
    assert close_code(retryable) == 1013
    check_close_exception(retryable)
    with pytest.raises(PolicyViolationError, match="Policy Violation"):
        check_close_exception(policy)

    unauthorized = InvalidStatus(Response(401, "Unauthorized", Headers()))
    not_ready = InvalidStatus(Response(409, "Conflict", Headers({"Retry-After": "2"})))
    rate_limited = InvalidStatus(
        Response(429, "Too Many Requests", Headers({"Retry-After": "invalid"}))
    )
    server_error = InvalidStatus(Response(503, "Unavailable", Headers()))
    bad_request = InvalidStatus(Response(400, "Bad Request", Headers()))

    with pytest.raises(AuthenticationError, match="authorization"):
        check_handshake_exception(unauthorized)
    assert check_handshake_exception(not_ready) == 2.0
    assert check_handshake_exception(rate_limited) == 1.0
    assert check_handshake_exception(server_error) is None
    with pytest.raises(TransportError, match="HTTP 400"):
        check_handshake_exception(bad_request)
    assert 0.8 <= jitter(1.0) <= 1.2


def parse_state():
    """Return a validated state without a preceding Tick."""

    from arena_hero import PlayerState

    return PlayerState.model_validate(state_payload())
