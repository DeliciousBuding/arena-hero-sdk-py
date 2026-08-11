"""Length-framed byte transport for arena.agent.io.v1.

Framing is a transport concern only and never appears in the semantic message
model. Each frame is ``4-byte big-endian length + payload`` where the length
counts payload bytes. Payloads are UTF-8 JSON produced by the semantic
protocol; this module stays generic over bytes so transport policy (limits,
diagnostics, process lifecycle) can be layered separately.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator

from arena_hero.errors import ProtocolError

FRAME_HEADER_SIZE = 4
"""Length-prefix bytes preceding every frame payload."""

DEFAULT_MAX_FRAME_SIZE = 1_048_576
"""Default maximum accepted frame payload size (1 MiB)."""


class FrameTooLargeError(ProtocolError):
    """A frame payload exceeded the negotiated maximum size."""


def encode_frame(payload: bytes) -> bytes:
    """Prefix ``payload`` with a 4-byte big-endian length header."""

    return struct.pack(">I", len(payload)) + payload


def frame_length(header: bytes | bytearray) -> int:
    """Decode a 4-byte big-endian length header."""

    return struct.unpack(">I", header)[0]


class FrameDecoder:
    """Incremental decoder that yields complete frames from a byte stream.

    Feed any chunk sizes; complete frames are emitted in order. A frame whose
    length exceeds ``max_frame_size`` fails closed with
    :class:`FrameTooLargeError` and permanently poisons the decoder.
    """

    def __init__(self, *, max_frame_size: int = DEFAULT_MAX_FRAME_SIZE) -> None:
        if max_frame_size <= 0:
            raise ValueError("max_frame_size must be positive")
        self._max_frame_size = max_frame_size
        self._buffer = bytearray()
        self._poisoned: FrameTooLargeError | None = None

    @property
    def max_frame_size(self) -> int:
        """Maximum accepted frame payload size in bytes."""

        return self._max_frame_size

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        """Consume ``chunk`` and yield any frames it completes."""

        if self._poisoned is not None:
            raise self._poisoned
        if not chunk:
            return
        self._buffer.extend(chunk)
        while True:
            frame = self._next_frame()
            if frame is None:
                return
            yield bytes(frame)

    def finish(self) -> None:
        """Signal end of stream; raises if a frame was truncated mid-wire."""

        if self._poisoned is not None:
            raise self._poisoned
        if self._buffer:
            raise ProtocolError("truncated arena.agent.io.v1 frame")
        self._buffer.clear()

    def _next_frame(self) -> bytearray | None:
        if len(self._buffer) < FRAME_HEADER_SIZE:
            return None
        length = frame_length(self._buffer[:FRAME_HEADER_SIZE])
        if length > self._max_frame_size:
            error = FrameTooLargeError(
                f"arena.agent.io.v1 frame exceeds {self._max_frame_size} bytes"
            )
            self._poisoned = error
            raise error
        if len(self._buffer) < FRAME_HEADER_SIZE + length:
            return None
        payload = self._buffer[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + length]
        del self._buffer[: FRAME_HEADER_SIZE + length]
        return payload


__all__ = [
    "DEFAULT_MAX_FRAME_SIZE",
    "FRAME_HEADER_SIZE",
    "FrameDecoder",
    "FrameTooLargeError",
    "encode_frame",
    "frame_length",
]
