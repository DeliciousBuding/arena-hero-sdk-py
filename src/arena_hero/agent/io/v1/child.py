"""Child side of the arena.agent.io.v1 subprocess transport.

Run ``python -m arena_hero.agent.io.v1.child`` to serve the canonical
conformance agent over length-framed stdin/stdout. Library consumers reuse
:func:`serve_stdio` with their own handler; stderr is reserved for bounded
diagnostics and stdout carries only protocol frames.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Sequence

from arena_hero.errors import ProtocolError

from .conformance import ConformanceAgent
from .framing import DEFAULT_MAX_FRAME_SIZE, FrameDecoder, encode_frame
from .handler import AgentHandler
from .protocol import encode_agent_message, parse_agent_message


def serve_stdio(
    handler: AgentHandler,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> int:
    """Serve ``handler`` over length-framed stdin/stdout until EOF.

    Returns the child exit code (0 on clean EOF). stdout carries only protocol
    frames; handlers must send diagnostics to stderr.
    """

    decoder = FrameDecoder(max_frame_size=max_frame_size)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    try:
        stdout.write(encode_frame(encode_agent_message(handler.hello())))
        stdout.flush()
        while True:
            chunk = os.read(stdin.fileno(), 65_536)
            if not chunk:
                decoder.finish()
                return 0
            for frame in decoder.feed(chunk):
                message = parse_agent_message(bytes(frame))
                for reply in handler.handle(message):
                    stdout.write(encode_frame(encode_agent_message(reply)))
                stdout.flush()
    except ProtocolError:
        print("arena.agent.io.v1 protocol error", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 3


def _main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Serve the arena.agent.io.v1 conformance agent over stdin/stdout."
    )
    parser.add_argument(
        "--max-frame-size",
        type=int,
        default=DEFAULT_MAX_FRAME_SIZE,
    )
    parser.add_argument(
        "--handler",
        choices=("conformance",),
        default="conformance",
    )
    args = parser.parse_args(argv)
    handler: AgentHandler = ConformanceAgent()
    if args.handler != "conformance":
        raise ValueError(f"unknown handler: {args.handler}")
    return serve_stdio(handler, max_frame_size=args.max_frame_size)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())


__all__ = ["serve_stdio"]
