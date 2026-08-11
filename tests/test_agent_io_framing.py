"""Length-framing codec tests for arena.agent.io.v1."""

from __future__ import annotations

import pytest

from arena_hero.agent.io.v1.framing import (
    FRAME_HEADER_SIZE,
    FrameDecoder,
    FrameTooLargeError,
    encode_frame,
    frame_length,
)
from arena_hero.errors import ProtocolError

PAYLOAD = b'{"type":"hello"}'


def test_encode_frame_prefixes_length() -> None:
    frame = encode_frame(PAYLOAD)
    assert frame_length(frame[:FRAME_HEADER_SIZE]) == len(PAYLOAD)
    assert frame[FRAME_HEADER_SIZE:] == PAYLOAD


def test_round_trip_single_frame() -> None:
    decoder = FrameDecoder()
    frames = list(decoder.feed(encode_frame(PAYLOAD)))
    assert frames == [PAYLOAD]


def test_multiple_frames_in_one_chunk() -> None:
    decoder = FrameDecoder()
    frames = list(decoder.feed(encode_frame(b"a") + encode_frame(b"bb")))
    assert frames == [b"a", b"bb"]


@pytest.mark.parametrize("split", [0, 1, 2, 3, 4, 5])
def test_split_chunks_yield_frame(split: int) -> None:
    decoder = FrameDecoder()
    frame = encode_frame(PAYLOAD)
    collected = list(decoder.feed(frame[:split])) + list(decoder.feed(frame[split:]))
    assert collected == [PAYLOAD]


def test_empty_payload_frame() -> None:
    decoder = FrameDecoder()
    assert list(decoder.feed(encode_frame(b""))) == [b""]


def test_feed_with_empty_chunk_yields_nothing() -> None:
    decoder = FrameDecoder()
    assert list(decoder.feed(b"")) == []


def test_oversized_frame_fails_closed() -> None:
    decoder = FrameDecoder(max_frame_size=8)
    with pytest.raises(FrameTooLargeError):
        list(decoder.feed(encode_frame(b"0123456789")))
    with pytest.raises(FrameTooLargeError):
        list(decoder.feed(b""))
    with pytest.raises(FrameTooLargeError):
        decoder.finish()


def test_truncated_frame_fails_at_finish() -> None:
    decoder = FrameDecoder()
    frame = encode_frame(PAYLOAD)
    list(decoder.feed(frame[: FRAME_HEADER_SIZE + 3]))
    with pytest.raises(ProtocolError, match=r"truncated arena\.agent\.io\.v1 frame"):
        decoder.finish()


def test_clean_finish_with_pending_header() -> None:
    decoder = FrameDecoder()
    list(decoder.feed(b""))
    decoder.finish()


def test_max_frame_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        FrameDecoder(max_frame_size=0)
