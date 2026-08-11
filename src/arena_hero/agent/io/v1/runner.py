"""Entry point runner for arena.agent.io.v1 (P1-5).

Runs a fixed contestant through one canonical round -- hello, episode_start,
decide, decision -- over either the trusted in-process adapter or the isolated
length-framed subprocess transport, and classifies the outcome fail-closed:
only ``ok`` is success and any timeout, crash, protocol violation, or
unexpected error invalidates the round. Framing and process policy stay in
``transport.py``; this module is orchestration only and reuses the conformance
scenario and the replay digest.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from arena_hero.errors import ProtocolError

from .conformance import ConformanceAgent, canonical_scenario
from .handler import AgentHandler
from .messages import AgentMessage
from .replay import TranscriptRecord, transcript_digest
from .transport import (
    DEFAULT_ENV_ALLOWLIST,
    DEFAULT_IO_TIMEOUT_MS,
    DEFAULT_MAX_FRAME_SIZE,
    DEFAULT_STDERR_LIMIT,
    AgentDeadlineError,
    AgentProcessCrashedError,
    AgentProtocolViolationError,
    SubprocessAgentTransport,
)

CHILD_MODULE = "arena_hero.agent.io.v1.child"
"""Module serving the fixed contestant over length-framed stdin/stdout."""

_CHILD_BOOTSTRAP = (
    "import sys;"
    "from arena_hero.agent.io.v1.child import serve_stdio;"
    "from arena_hero.agent.io.v1.conformance import ConformanceAgent;"
    "raise SystemExit(serve_stdio(ConformanceAgent()))"
)
"""``-c`` bootstrap for the fixed contestant subprocess.

Equivalent to ``python -m arena_hero.agent.io.v1.child`` but avoids the runpy
warning emitted when the parent package eagerly imports the child module.
"""

RoundStatus = Literal["ok", "timeout", "crash", "protocol", "error"]
"""Fail-closed outcome classification; only ``ok`` is success."""


@dataclass(frozen=True)
class RoundResult:
    """Outcome of one runner round with bounded diagnostics.

    ``transcript`` and ``digest`` are populated only on success; every failure
    is fail-closed (``status != "ok"``) and carries a diagnostic instead.
    ``stderr`` is the bounded child diagnostic tail (empty for in-process).
    """

    status: RoundStatus
    transcript: tuple[TranscriptRecord, ...] | None
    digest: str | None
    stderr: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True only when the round completed with the expected exchange."""

        return self.status == "ok"


def _round_inputs() -> tuple[tuple[AgentMessage, str], ...]:
    """One canonical round: episode_start -> ready, decide -> decision.

    Reuses the canonical conformance scenario so the runner shares the same
    deterministic identifiers and transcript prefix as the conformance suite;
    the error-path decide and episode_end are outside one-round scope.
    """

    episode_start, _ = canonical_scenario()[0]
    decide, _ = canonical_scenario()[1]
    return ((episode_start, "ready"), (decide, "decision"))


def _classify(exc: BaseException) -> tuple[RoundStatus, str]:
    """Map an exception to a fail-closed outcome classification."""

    if isinstance(exc, AgentDeadlineError):
        return "timeout", str(exc)
    if isinstance(exc, AgentProcessCrashedError):
        return "crash", str(exc)
    if isinstance(exc, (AgentProtocolViolationError, ProtocolError)):
        return "protocol", str(exc)
    return "error", f"{type(exc).__name__}: {exc}"


def _ok_result(records: list[TranscriptRecord], stderr: str) -> RoundResult:
    """Build a successful round result with the deterministic digest."""

    return RoundResult(
        status="ok",
        transcript=tuple(records),
        digest=transcript_digest(records),
        stderr=stderr,
    )


def _failed_result(
    status: RoundStatus,
    error: str,
    *,
    stderr: str = "",
) -> RoundResult:
    """Build a fail-closed round result with no partial transcript."""

    return RoundResult(
        status=status,
        transcript=None,
        digest=None,
        stderr=stderr,
        error=error,
    )


def run_round_in_process(handler: AgentHandler) -> RoundResult:
    """Run one round against a trusted in-process handler (no isolation)."""

    try:
        records = [TranscriptRecord(direction="out", message=handler.hello())]
        for message, expected_type in _round_inputs():
            replies = tuple(handler.handle(message))
            records.append(TranscriptRecord(direction="in", message=message))
            if len(replies) != 1:
                raise ProtocolError(
                    f"expected one {expected_type} reply, got {len(replies)}"
                )
            reply = replies[0]
            if reply.type != expected_type:
                raise ProtocolError(f"expected {expected_type} reply, got {reply.type}")
            records.append(TranscriptRecord(direction="out", message=reply))
        return _ok_result(records, stderr="")
    except Exception as exc:  # fail-closed: never leak a partial round
        status, error = _classify(exc)
        return _failed_result(status, error)


def run_round_subprocess(
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    env_allowlist: Sequence[str] = DEFAULT_ENV_ALLOWLIST,
    cwd: str | os.PathLike[str] | None = None,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
    stderr_limit: int = DEFAULT_STDERR_LIMIT,
    io_timeout_ms: int = DEFAULT_IO_TIMEOUT_MS,
    deadline_ms: int | None = None,
) -> RoundResult:
    """Run one round over the isolated length-framed subprocess transport.

    The child runs with the allowlisted environment and a private temporary
    directory; stderr diagnostics are bounded to ``stderr_limit`` bytes.
    """

    transport: SubprocessAgentTransport | None = None
    try:
        with SubprocessAgentTransport(
            command,
            env=env,
            env_allowlist=env_allowlist,
            cwd=cwd,
            max_frame_size=max_frame_size,
            stderr_limit=stderr_limit,
            io_timeout_ms=io_timeout_ms,
            deadline_ms=deadline_ms,
        ) as transport:
            records = [
                TranscriptRecord(direction="out", message=transport.recv_hello())
            ]
            for message, expected_type in _round_inputs():
                records.append(TranscriptRecord(direction="in", message=message))
                transport.send(message)
                reply = transport.recv()
                if reply.type != expected_type:
                    raise AgentProtocolViolationError(
                        f"expected {expected_type} reply, got {reply.type}"
                    )
                records.append(TranscriptRecord(direction="out", message=reply))
        return _ok_result(records, stderr=transport.stderr_text())
    except Exception as exc:  # fail-closed: never leak a partial round
        status, error = _classify(exc)
        stderr = transport.stderr_text() if transport is not None else ""
        return _failed_result(status, error, stderr=stderr)


def _main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: run one round and exit 0 only on success."""

    parser = argparse.ArgumentParser(
        prog="python -m arena_hero.agent.io.v1.runner",
        description=(
            "Run one arena.agent.io.v1 round (hello/episode_start/decide/"
            "decision) against the fixed contestant."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("in_process", "subprocess"),
        default="in_process",
        help="trusted in-process adapter or isolated subprocess (default: in_process)",
    )
    parser.add_argument(
        "--command",
        nargs=argparse.REMAINDER,
        default=None,
        help=(
            "subprocess command tokens; consume every following token, so put "
            "other options before --command (default: fixed conformance contestant)"
        ),
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_IO_TIMEOUT_MS,
        help="per-recv timeout in milliseconds (default: %(default)s)",
    )
    parser.add_argument(
        "--deadline-ms",
        type=int,
        default=None,
        help="optional hard round deadline in milliseconds",
    )
    parser.add_argument(
        "--stderr-limit",
        type=int,
        default=DEFAULT_STDERR_LIMIT,
        help="bounded child stderr diagnostics in bytes (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    if args.mode == "in_process":
        if args.command is not None:
            parser.error("--command requires --mode subprocess")
        result = run_round_in_process(ConformanceAgent())
    else:
        command = args.command or [sys.executable, "-c", _CHILD_BOOTSTRAP]
        result = run_round_subprocess(
            command,
            io_timeout_ms=args.timeout_ms,
            deadline_ms=args.deadline_ms,
            stderr_limit=args.stderr_limit,
        )

    if result.ok:
        print(f"status=ok digest={result.digest}")
    else:
        print(f"status={result.status} error={result.error}", file=sys.stderr)
    if result.stderr:
        print(f"stderr: {result.stderr}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())


__all__ = [
    "CHILD_MODULE",
    "RoundResult",
    "RoundStatus",
    "run_round_in_process",
    "run_round_subprocess",
]
