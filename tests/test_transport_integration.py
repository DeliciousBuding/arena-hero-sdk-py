"""Real local HTTP and WebSocket transport integration tests."""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from conftest import received_payload, state_payload
from websockets.sync.server import ServerConnection, serve

from arena_hero import (
    ArenaHeroClient,
    AsyncArenaHeroClient,
    AsyncTurn,
    Turn,
)


@contextmanager
def local_servers() -> Iterator[dict[str, Any]]:
    """Run real local HTTP and WebSocket servers for one test."""

    captured: dict[str, Any] = {
        "http_authorization": [],
        "http_bodies": [],
        "websocket_authorization": [],
    }

    class CommandHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            captured["http_authorization"].append(self.headers["Authorization"])
            captured["http_bodies"].append(self.rfile.read(length))
            body = json.dumps(
                {
                    "accepted": True,
                    "tick": 9,
                    "source": "AGENT",
                    "received_at": "2026-07-28T12:00:00Z",
                }
            ).encode()
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(
            self,
            format: str,  # noqa: A002
            *args: Any,
        ) -> None:
            return None

    def stream_handler(connection: ServerConnection) -> None:
        assert connection.request is not None
        captured["websocket_authorization"].append(
            connection.request.headers["Authorization"]
        )
        connection.send('{"type":"tick","data":9}')
        connection.send(json.dumps({"type": "state", "data": state_payload()}))
        connection.send(json.dumps({"type": "received", "data": received_payload()}))
        connection.close(1000)

    http_server = ThreadingHTTPServer(("127.0.0.1", 0), CommandHandler)
    websocket_server = serve(
        stream_handler,
        "127.0.0.1",
        0,
        compression=None,
    )
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    websocket_thread = threading.Thread(
        target=websocket_server.serve_forever,
        daemon=True,
    )
    http_thread.start()
    websocket_thread.start()

    http_port = http_server.server_address[1]
    websocket_port = websocket_server.socket.getsockname()[1]
    captured["base_url"] = f"http://127.0.0.1:{http_port}"
    captured["websocket_url"] = f"ws://127.0.0.1:{websocket_port}/api/v1/game/ws"
    try:
        yield captured
    finally:
        http_server.shutdown()
        websocket_server.shutdown()
        http_server.server_close()
        http_thread.join(timeout=5)
        websocket_thread.join(timeout=5)


def test_sync_client_uses_real_http_and_websocket_transports() -> None:
    with (
        local_servers() as servers,
        ArenaHeroClient(
            api_key="integration-key",
            base_url=servers["base_url"],
            websocket_url=servers["websocket_url"],
        ) as game,
    ):
        events = list(game.events())
        turn = next(event for event in events if isinstance(event, Turn))
        turn.workers[0].harvest()
        accepted = turn.submit(idempotency_key="integration-sync")

    assert accepted.tick == 9
    assert servers["websocket_authorization"][0] == "Bearer integration-key"
    assert servers["http_authorization"][0] == "Bearer integration-key"
    assert b'"type":"HARVEST"' in servers["http_bodies"][0]


async def test_async_client_uses_real_http_and_websocket_transports() -> None:
    with local_servers() as servers:
        async with AsyncArenaHeroClient(
            api_key="integration-key",
            base_url=servers["base_url"],
            websocket_url=servers["websocket_url"],
        ) as game:
            events = [event async for event in game.events()]
            turn = next(event for event in events if isinstance(event, AsyncTurn))
            turn.workers[0].harvest()
            accepted = await turn.submit(idempotency_key="integration-async")

    assert accepted.tick == 9
    assert servers["websocket_authorization"][0] == "Bearer integration-key"
    assert servers["http_authorization"][0] == "Bearer integration-key"
    assert b'"type":"HARVEST"' in servers["http_bodies"][0]
