"""Subprocess transport tests for arena.agent.io.v1 (P1-6)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from uuid import UUID

import pytest
from conftest import child_command, child_env, python_command

from arena_hero.agent.io.v1.messages import DecideMessage
from arena_hero.agent.io.v1.transport import (
    AgentDeadlineError,
    AgentProcessCrashedError,
    AgentProtocolViolationError,
    SubprocessAgentTransport,
    SubprocessTransportError,
)

DECIDE_REQUEST = UUID("00000000-0000-0000-0000-000000000022")


def decide_message() -> DecideMessage:
    return DecideMessage(
        type="decide",
        schema_version=1,
        message_id=UUID("00000000-0000-0000-0000-000000000011"),
        request_id=DECIDE_REQUEST,
        tenant_id="t1",
        tick=3,
    )


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

FUTURE_VERSION_SCRIPT = """
import json, os, sys, uuid
sys.path.insert(0, os.environ["PYTHONPATH"])
from arena_hero.agent.io.v1.framing import encode_frame

payload = json.dumps(
    {
        "type": "hello",
        "schemaVersion": 2,
        "messageId": str(uuid.UUID(int=1)),
        "contestant": "future-agent",
        "capabilities": {},
    }
).encode()
sys.stdout.buffer.write(encode_frame(payload))
sys.stdout.buffer.flush()
"""

GARBAGE_SCRIPT = """
import sys
sys.stdout.buffer.write(b"definitely not a length framed json payload")
sys.stdout.buffer.flush()
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

PROTO_ERROR_SCRIPT = """
import os, sys, uuid
sys.path.insert(0, os.environ["PYTHONPATH"])
from arena_hero.agent.io.v1.child import serve_stdio
from arena_hero.agent.io.v1.messages import AgentCapabilities, HelloMessage
from arena_hero.errors import ProtocolError

class RaisingAgent:
    def hello(self):
        return HelloMessage(
            type="hello", schema_version=1, message_id=uuid.UUID(int=1),
            contestant="raising", capabilities=AgentCapabilities(),
        )
    def handle(self, message):
        raise ProtocolError("child protocol failure")

raise SystemExit(serve_stdio(RaisingAgent()))
"""

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

TREE_SCRIPT = """
import os, subprocess, sys
sys.path.insert(0, os.environ["PYTHONPATH"])
from arena_hero.agent.io.v1.child import serve_stdio
from arena_hero.agent.io.v1.conformance import ConformanceAgent

temp_dir = os.environ.get("TMPDIR") or os.environ.get("TEMP")
watchdog = os.path.join(temp_dir, "arena-tree-watchdog")
survived = os.environ["SURVIVED_FILE"]
with open(watchdog, "w") as handle:
    handle.write("watch")
grandchild = (
    "import os,time; wd=os.environ['WATCHDOG_FILE']; "
    "sv=os.environ['SURVIVED_FILE']; "
    "while os.path.exists(wd): time.sleep(0.05); "
    "open(sv,'w').write('survived')"
)
env = dict(os.environ)
env["WATCHDOG_FILE"] = watchdog
env["SURVIVED_FILE"] = survived
subprocess.Popen([sys.executable, "-c", grandchild], env=env)
raise SystemExit(serve_stdio(ConformanceAgent()))
"""

STDERR_FLOOD_SCRIPT = """
import os, sys
sys.path.insert(0, os.environ["PYTHONPATH"])
from arena_hero.agent.io.v1.child import serve_stdio
from arena_hero.agent.io.v1.conformance import ConformanceAgent

class FloodAgent(ConformanceAgent):
    def handle(self, message):
        for index in range(20000):
            print("diagnostic line %d" % index, file=sys.stderr)
        return super().handle(message)

raise SystemExit(serve_stdio(FloodAgent()))
"""

ENV_SNAPSHOT_SCRIPT = """
import json, os, sys
sys.path.insert(0, os.environ["PYTHONPATH"])
from arena_hero.agent.io.v1.child import serve_stdio
from arena_hero.agent.io.v1.conformance import ConformanceAgent

with open(os.environ["ENV_SNAPSHOT_FILE"], "w") as handle:
    handle.write(json.dumps(dict(os.environ), sort_keys=True))
raise SystemExit(serve_stdio(ConformanceAgent()))
"""


def test_transport_round_trip_conformance_child() -> None:
    transport = SubprocessAgentTransport(child_command(), env=child_env())
    with transport:
        hello = transport.recv_hello()
        assert hello.type == "hello"
        transport.send(decide_message())
        reply = transport.recv()
        assert reply.type == "decision"
        assert reply.request_id == DECIDE_REQUEST


@pytest.mark.parametrize(
    "script",
    [WRONG_FIRST_SCRIPT, FUTURE_VERSION_SCRIPT],
)
def test_recv_hello_fails_closed_on_bad_handshake(script: str) -> None:
    transport = SubprocessAgentTransport(python_command(script), env=child_env())
    with pytest.raises(AgentProtocolViolationError), transport:
        transport.recv_hello()
    assert transport.poll() is not None


def test_stdout_garbage_is_protocol_violation() -> None:
    transport = SubprocessAgentTransport(
        python_command(GARBAGE_SCRIPT), env=child_env()
    )
    with pytest.raises(AgentProtocolViolationError), transport:
        transport.recv()
    assert transport.poll() is not None


def test_crash_isolation_reports_bounded_diagnostics() -> None:
    transport = SubprocessAgentTransport(python_command(CRASH_SCRIPT), env=child_env())
    with transport:
        hello = transport.recv_hello()
        assert hello.type == "hello"
        transport.send(decide_message())
        with pytest.raises(AgentProcessCrashedError) as excinfo:
            transport.recv()
    assert "child exited with code 3" in str(excinfo.value)
    assert "boom crash" in transport.stderr_text()


def test_child_protocol_error_reports_diagnostics() -> None:
    transport = SubprocessAgentTransport(
        python_command(PROTO_ERROR_SCRIPT),
        env=child_env(),
    )
    with transport:
        hello = transport.recv_hello()
        assert hello.type == "hello"
        transport.send(decide_message())
        with pytest.raises(AgentProcessCrashedError) as excinfo:
            transport.recv()
    assert "child exited with code 2" in str(excinfo.value)
    assert "protocol error" in transport.stderr_text()


def test_send_after_child_crash_raises() -> None:
    transport = SubprocessAgentTransport(python_command(CRASH_SCRIPT), env=child_env())
    with transport:
        transport.recv_hello()
        transport.send(decide_message())
        with pytest.raises(AgentProcessCrashedError):
            transport.recv()
        with pytest.raises(SubprocessTransportError):
            transport.send(decide_message())


def test_deadline_triggers_process_tree_termination(tmp_path: Path) -> None:
    survived = tmp_path / "survived"
    env = child_env({"SURVIVED_FILE": str(survived)})
    transport = SubprocessAgentTransport(
        python_command(HANG_SCRIPT),
        env=env,
        deadline_ms=2000,
        io_timeout_ms=30_000,
    )
    try:
        hello = transport.recv_hello()
        assert hello.type == "hello"
        transport.send(decide_message())
        with pytest.raises(AgentDeadlineError):
            transport.recv(timeout_ms=300)
        assert transport.closed.wait(10)
        assert transport.poll() is not None
        time.sleep(1.0)
        assert not survived.exists()
    finally:
        transport.close()


def test_close_terminates_process_tree(tmp_path: Path) -> None:
    survived = tmp_path / "survived"
    env = child_env({"SURVIVED_FILE": str(survived)})
    transport = SubprocessAgentTransport(python_command(TREE_SCRIPT), env=env)
    try:
        hello = transport.recv_hello()
        assert hello.type == "hello"
        transport.close()
        assert transport.closed.wait(5)
        assert transport.poll() is not None
        time.sleep(1.5)
        assert not survived.exists()
    finally:
        transport.close()


def test_env_allowlist_blocks_ambient_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "env.json"
    monkeypatch.setenv("ARENA_HERO_API_KEY_1", "super-secret-value")
    env = child_env({"ENV_SNAPSHOT_FILE": str(snapshot)})
    transport = SubprocessAgentTransport(
        python_command(ENV_SNAPSHOT_SCRIPT),
        env=env,
        env_allowlist=("PATH",),
    )
    with transport:
        hello = transport.recv_hello()
        assert hello.type == "hello"
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    assert "ARENA_HERO_API_KEY_1" not in data
    assert "super-secret-value" not in " ".join(data.values())
    assert data.get("PATH") == os.environ.get("PATH")


def test_temp_dir_owned_by_transport_and_removed(tmp_path: Path) -> None:
    snapshot = tmp_path / "env.json"
    env = child_env({"ENV_SNAPSHOT_FILE": str(snapshot)})
    transport = SubprocessAgentTransport(python_command(ENV_SNAPSHOT_SCRIPT), env=env)
    temp_dir = transport.temp_dir
    assert Path(temp_dir).is_dir()
    with transport:
        hello = transport.recv_hello()
        assert hello.type == "hello"
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    child_temp = data.get("TEMP") or data.get("TMPDIR")
    assert child_temp == temp_dir
    assert not Path(temp_dir).exists()


def test_stderr_diagnostics_are_bounded() -> None:
    transport = SubprocessAgentTransport(
        python_command(STDERR_FLOOD_SCRIPT),
        env=child_env(),
        stderr_limit=4096,
    )
    with transport:
        hello = transport.recv_hello()
        assert hello.type == "hello"
        transport.send(decide_message())
        reply = transport.recv()
        assert reply.type == "decision"
    text = transport.stderr_text()
    assert "truncated after 4096 bytes" in text
    assert len(text) <= 4096 + 64


def test_close_is_idempotent_and_recv_after_close_fails() -> None:
    transport = SubprocessAgentTransport(child_command(), env=child_env())
    transport.recv_hello()
    transport.close()
    transport.close()
    with pytest.raises(SubprocessTransportError):
        transport.recv()
    with pytest.raises(SubprocessTransportError):
        transport.send(decide_message())


def test_invalid_constructor_arguments() -> None:
    with pytest.raises(ValueError):
        SubprocessAgentTransport(["true"], max_frame_size=0)
    with pytest.raises(ValueError):
        SubprocessAgentTransport(["true"], stderr_limit=0)
    with pytest.raises(ValueError):
        SubprocessAgentTransport(["true"], io_timeout_ms=0)
    with pytest.raises(ValueError):
        SubprocessAgentTransport(["true"], deadline_ms=0)


def test_recv_rejects_non_positive_timeout() -> None:
    transport = SubprocessAgentTransport(child_command(), env=child_env())
    with transport:
        transport.recv_hello()
        with pytest.raises(ValueError):
            transport.recv(timeout_ms=0)
