"""Entry point runner tests for arena.agent.io.v1 (P1-5)."""

from __future__ import annotations

import sys
import uuid

import pytest
from conftest import child_env, python_command

from arena_hero.agent.io.v1.conformance import ConformanceAgent, record_transcript
from arena_hero.agent.io.v1.messages import (
    AgentMessage,
    HelloMessage,
    ReadyMessage,
)
from arena_hero.agent.io.v1.replay import transcript_digest
from arena_hero.agent.io.v1.runner import (
    _CHILD_BOOTSTRAP,
    CHILD_MODULE,
    RoundResult,
    RoundStatus,
    _main,
    run_round_in_process,
    run_round_subprocess,
)
from arena_hero.errors import ProtocolError

HANG_SCRIPT = """
import os, sys, time
sys.path.insert(0, os.environ["PYTHONPATH"])
from arena_hero.agent.io.v1.child import serve_stdio
from arena_hero.agent.io.v1.conformance import ConformanceAgent

class HangingAgent(ConformanceAgent):
    def handle(self, message):
        if getattr(message, "type", "") == "decide":
            time.sleep(60)
        return super().handle(message)

raise SystemExit(serve_stdio(HangingAgent()))
"""

CRASH_SCRIPT = """
import os, sys
sys.path.insert(0, os.environ["PYTHONPATH"])
from arena_hero.agent.io.v1.child import serve_stdio
from arena_hero.agent.io.v1.conformance import ConformanceAgent

class CrashAgent(ConformanceAgent):
    def handle(self, message):
        raise RuntimeError("boom crash")

raise SystemExit(serve_stdio(CrashAgent()))
"""

EXIT_SCRIPT = "import sys; sys.exit(7)"

GARBAGE_SCRIPT = """
import sys
sys.stdout.buffer.write(b"definitely not a length framed json payload")
sys.stdout.buffer.flush()
"""

WRONG_FIRST_SCRIPT = """
import os, sys, uuid
sys.path.insert(0, os.environ["PYTHONPATH"])
from arena_hero.agent.io.v1.framing import encode_frame
from arena_hero.agent.io.v1.messages import DecisionMessage
from arena_hero.agent.io.v1.protocol import encode_agent_message

msg = DecisionMessage(
    type="decision", schema_version=1, message_id=uuid.UUID(int=1),
    request_id=uuid.UUID(int=2), decision_id=uuid.UUID(int=3),
    tenant_id="t1", tick=1,
)
sys.stdout.buffer.write(encode_frame(encode_agent_message(msg)))
sys.stdout.buffer.flush()
"""

WRONG_REPLY_SCRIPT = """
import os, sys, uuid
sys.path.insert(0, os.environ["PYTHONPATH"])
from arena_hero.agent.io.v1.child import serve_stdio
from arena_hero.agent.io.v1.conformance import ConformanceAgent
from arena_hero.agent.io.v1.messages import ReadyMessage

class WrongReplyAgent(ConformanceAgent):
    def handle(self, message):
        if getattr(message, "type", "") == "decide":
            return [ReadyMessage(
                type="ready", schema_version=1, message_id=uuid.UUID(int=9),
                request_id=message.request_id, tenant_id=message.tenant_id,
            )]
        return super().handle(message)

raise SystemExit(serve_stdio(WrongReplyAgent()))
"""


def fixed_contestant_command() -> list[str]:
    """Command running the fixed conformance contestant without runpy noise."""

    return [sys.executable, "-c", _CHILD_BOOTSTRAP]


def test_in_process_round_succeeds() -> None:
    result = run_round_in_process(ConformanceAgent())
    assert result.status == "ok"
    assert result.ok
    assert result.error is None
    assert result.stderr == ""
    assert result.digest is not None
    assert len(result.digest) == 64
    assert result.transcript is not None
    assert [record.message.type for record in result.transcript] == [
        "hello",
        "episode_start",
        "ready",
        "decide",
        "decision",
    ]


def test_in_process_round_matches_conformance_prefix() -> None:
    result = run_round_in_process(ConformanceAgent())
    prefix = record_transcript(ConformanceAgent())[:5]
    assert result.digest == transcript_digest(prefix)
    assert result.transcript == tuple(prefix)


def test_in_process_protocol_violation_fails_closed() -> None:
    class RaisingAgent:
        def hello(self) -> HelloMessage:
            return ConformanceAgent().hello()

        def handle(self, message: AgentMessage) -> list[AgentMessage]:
            raise ProtocolError("child protocol failure")

    result = run_round_in_process(RaisingAgent())
    assert result.status == "protocol"
    assert not result.ok
    assert result.transcript is None
    assert result.digest is None
    assert "child protocol failure" in (result.error or "")


def test_in_process_agent_error_fails_closed() -> None:
    class BrokenAgent:
        def hello(self) -> HelloMessage:
            return ConformanceAgent().hello()

        def handle(self, message: AgentMessage) -> list[AgentMessage]:
            raise RuntimeError("boom crash")

    result = run_round_in_process(BrokenAgent())
    assert result.status == "error"
    assert not result.ok
    assert result.transcript is None
    assert result.digest is None


def test_in_process_wrong_reply_count_fails_closed() -> None:
    class SilentAgent:
        def hello(self) -> HelloMessage:
            return ConformanceAgent().hello()

        def handle(self, message: AgentMessage) -> list[AgentMessage]:
            return []

    result = run_round_in_process(SilentAgent())
    assert result.status == "protocol"
    assert not result.ok
    assert result.transcript is None


def test_in_process_wrong_reply_type_fails_closed() -> None:
    class WrongReplyAgent(ConformanceAgent):
        def handle(self, message: AgentMessage) -> list[AgentMessage]:
            if message.type == "decide":
                return [
                    ReadyMessage(
                        type="ready",
                        schema_version=1,
                        message_id=uuid.UUID(int=9),
                        request_id=message.request_id,
                        tenant_id=message.tenant_id,
                    )
                ]
            return super().handle(message)

    result = run_round_in_process(WrongReplyAgent())
    assert result.status == "protocol"
    assert not result.ok


def test_subprocess_round_succeeds() -> None:
    result = run_round_subprocess(fixed_contestant_command())
    assert result.status == "ok"
    assert result.ok
    assert result.stderr == ""
    assert result.transcript is not None
    assert len(result.transcript) == 5


def test_subprocess_round_matches_in_process_digest() -> None:
    in_process = run_round_in_process(ConformanceAgent())
    subprocess = run_round_subprocess(fixed_contestant_command())
    assert subprocess.status == "ok"
    assert subprocess.digest == in_process.digest


def test_two_subprocess_runs_share_digest() -> None:
    first = run_round_subprocess(fixed_contestant_command())
    second = run_round_subprocess(fixed_contestant_command())
    assert first.status == "ok"
    assert second.status == "ok"
    assert first.digest == second.digest
    assert first.transcript == second.transcript


def test_subprocess_timeout_fails_closed() -> None:
    result = run_round_subprocess(
        python_command(HANG_SCRIPT),
        env=child_env(),
        io_timeout_ms=300,
    )
    assert result.status == "timeout"
    assert not result.ok
    assert result.transcript is None
    assert result.digest is None


def test_subprocess_crash_fails_closed() -> None:
    result = run_round_subprocess(
        python_command(CRASH_SCRIPT),
        env=child_env(),
    )
    assert result.status == "crash"
    assert not result.ok
    assert result.transcript is None
    assert "boom crash" in result.stderr


def test_subprocess_startup_crash_fails_closed() -> None:
    result = run_round_subprocess(python_command(EXIT_SCRIPT))
    assert result.status == "crash"
    assert not result.ok
    assert "code 7" in (result.error or "")


def test_subprocess_garbage_protocol_fails_closed() -> None:
    result = run_round_subprocess(python_command(GARBAGE_SCRIPT))
    assert result.status == "protocol"
    assert not result.ok
    assert result.transcript is None


def test_subprocess_wrong_first_message_fails_closed() -> None:
    result = run_round_subprocess(
        python_command(WRONG_FIRST_SCRIPT),
        env=child_env(),
    )
    assert result.status == "protocol"
    assert not result.ok


def test_subprocess_wrong_reply_type_fails_closed() -> None:
    result = run_round_subprocess(
        python_command(WRONG_REPLY_SCRIPT),
        env=child_env(),
    )
    assert result.status == "protocol"
    assert not result.ok


def test_two_independent_in_process_runs_are_equal() -> None:
    first = run_round_in_process(ConformanceAgent())
    second = run_round_in_process(ConformanceAgent())
    assert isinstance(first, RoundResult)
    assert first == second
    status: RoundStatus = "ok"
    assert first.status == status


def test_child_module_constant_points_at_serving_module() -> None:
    assert CHILD_MODULE == "arena_hero.agent.io.v1.child"
    assert CHILD_MODULE in sys.modules


def test_cli_in_process_exit_zero() -> None:
    assert _main([]) == 0


def test_cli_subprocess_default_exit_zero() -> None:
    assert _main(["--mode", "subprocess"]) == 0


def test_cli_failure_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    code = _main(["--mode", "subprocess", "--command", *python_command(CRASH_SCRIPT)])
    assert code == 1
    captured = capsys.readouterr()
    assert "status=crash" in captured.err


def test_cli_command_requires_subprocess() -> None:
    with pytest.raises(SystemExit):
        _main(["--command", sys.executable])
