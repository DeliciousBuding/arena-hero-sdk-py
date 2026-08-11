"""Isolated subprocess transport for arena.agent.io.v1.

The parent (runner) side of the ADR-0004 subprocess transport: spawns a child
with an allowlisted environment and a private temporary directory, exchanges
length-framed semantic messages over stdin/stdout, and owns process-tree
termination with bounded cleanup. Framing and process policy live here, never
in the semantic message model.
"""

from __future__ import annotations

import contextlib
import os
import queue
import shutil
import signal
import subprocess  # nosec B404
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import NoReturn

from arena_hero.errors import ProtocolError, TransportError

from .framing import (
    DEFAULT_MAX_FRAME_SIZE,
    FrameDecoder,
    encode_frame,
)
from .messages import AGENT_IO_SCHEMA_VERSION, AgentMessage, HelloMessage
from .protocol import encode_agent_message, parse_agent_message

DEFAULT_IO_TIMEOUT_MS = 30_000
"""Default per-recv wait before raising :class:`AgentDeadlineError`."""

DEFAULT_STDERR_LIMIT = 65_536
"""Bytes of child stderr retained for bounded diagnostics."""

DEFAULT_ENV_ALLOWLIST: tuple[str, ...] = (
    "LANG",
    "LC_ALL",
    "PATH",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
)
"""Environment variables passed through from the parent by default."""

_TEMP_PREFIX = "arena-agent-"
_READ_CHUNK_SIZE = 65_536
_STDERR_CHUNK_SIZE = 4_096


class SubprocessTransportError(TransportError):
    """The subprocess transport failed at the process or framing layer."""


class AgentDeadlineError(SubprocessTransportError):
    """A reply did not arrive within the transport deadline."""


class AgentProcessCrashedError(SubprocessTransportError):
    """The child exited before the transport was closed."""


class AgentProtocolViolationError(SubprocessTransportError):
    """The child violated the arena.agent.io.v1 wire contract."""


def build_child_env(
    *,
    allowlist: Sequence[str] = DEFAULT_ENV_ALLOWLIST,
    extra: Mapping[str, str] | None = None,
    temp_dir: str,
) -> dict[str, str]:
    """Build the child environment from an explicit allowlist.

    Only allowlisted variables pass through from the parent environment;
    ``extra`` values are always added because they are explicit, never
    ambient. ``temp_dir`` is injected as the platform temporary directory so
    the child owns a private scratch space. On Windows, ``SYSTEMROOT`` is
    always injected because the interpreter's socket provider requires it;
    the allowlist governs everything else.
    """

    env: dict[str, str] = {}
    for key in allowlist:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    if extra:
        env.update(extra)
    if sys.platform == "win32":
        env["TEMP"] = temp_dir
        env["TMP"] = temp_dir
        system_root = os.environ.get("SYSTEMROOT")
        if system_root is not None:
            env["SYSTEMROOT"] = system_root
    else:
        env["TMPDIR"] = temp_dir
    return env


class SubprocessAgentTransport:
    """Length-framed semantic message transport over a child process.

    ``command`` is executed with ``shell=False`` against an environment built
    from :func:`build_child_env`: only allowlisted parent variables plus the
    explicit ``env`` overrides pass through, and the child's temporary
    directory is owned by this transport and removed on close. ``stdout``
    carries only protocol frames; ``stderr`` is drained for bounded
    diagnostics. On close, on protocol violation, or after an optional hard
    ``deadline_ms``, the whole child process tree is terminated and resources
    are released with bounded cleanup.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        env_allowlist: Sequence[str] = DEFAULT_ENV_ALLOWLIST,
        cwd: str | os.PathLike[str] | None = None,
        max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
        stderr_limit: int = DEFAULT_STDERR_LIMIT,
        io_timeout_ms: int = DEFAULT_IO_TIMEOUT_MS,
        deadline_ms: int | None = None,
    ) -> None:
        """Initialize the transport and spawn the child process."""

        if max_frame_size <= 0:
            raise ValueError("max_frame_size must be positive")
        if stderr_limit <= 0:
            raise ValueError("stderr_limit must be positive")
        if io_timeout_ms <= 0:
            raise ValueError("io_timeout_ms must be positive")
        if deadline_ms is not None and deadline_ms <= 0:
            raise ValueError("deadline_ms must be positive")
        self._max_frame_size = max_frame_size
        self._stderr_limit = stderr_limit
        self._io_timeout_ms = io_timeout_ms
        self._temp_dir = tempfile.mkdtemp(prefix=_TEMP_PREFIX)
        child_env = build_child_env(
            allowlist=env_allowlist,
            extra=env,
            temp_dir=self._temp_dir,
        )
        self._frames: queue.Queue[bytes | BaseException | None] = queue.Queue()
        self._send_lock = threading.Lock()
        self._closed = threading.Event()
        self._stderr: bytearray = bytearray()
        self._stderr_truncated = False
        try:
            if sys.platform == "win32":
                self._proc = subprocess.Popen(  # noqa: S603 - explicit argv list
                    list(command),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=child_env,
                    cwd=cwd,
                    text=False,
                    shell=False,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )  # nosec B603
            else:
                self._proc = subprocess.Popen(  # noqa: S603 - explicit argv list
                    list(command),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=child_env,
                    cwd=cwd,
                    text=False,
                    shell=False,
                    start_new_session=True,
                )  # nosec B603
        except BaseException:
            self._cleanup_temp_dir()
            raise
        self._decoder = FrameDecoder(max_frame_size=max_frame_size)
        self._reader = threading.Thread(
            target=self._read_loop,
            name="arena-io-reader",
            daemon=True,
        )
        self._stderr_reader = threading.Thread(
            target=self._stderr_loop,
            name="arena-io-stderr",
            daemon=True,
        )
        self._watchdog: threading.Timer | None = None
        if deadline_ms is not None:
            self._watchdog = threading.Timer(
                deadline_ms / 1000,
                self.close,
            )
            self._watchdog.daemon = True
        self._reader.start()
        self._stderr_reader.start()
        if self._watchdog is not None:
            self._watchdog.start()

    @property
    def pid(self) -> int:
        """Child process id."""

        return self._proc.pid

    @property
    def max_frame_size(self) -> int:
        """Maximum accepted frame payload size in bytes."""

        return self._max_frame_size

    @property
    def temp_dir(self) -> str:
        """Private temporary directory owned by this transport."""

        return self._temp_dir

    @property
    def closed(self) -> threading.Event:
        """Set once the transport is closed and the child is terminated."""

        return self._closed

    def poll(self) -> int | None:
        """Return the child exit code, or None while it is still running."""

        return self._proc.poll()

    def send(self, message: AgentMessage) -> None:
        """Write one semantic message as a length-framed stdout payload."""

        self._ensure_open()
        stdin = self._proc.stdin
        if stdin is None:
            raise SubprocessTransportError("arena.agent.io.v1 transport is closed")
        frame = encode_frame(encode_agent_message(message))
        with self._send_lock:
            try:
                stdin.write(frame)
                stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._terminate()
                raise AgentProcessCrashedError("child stdin closed") from exc

    def recv(self, timeout_ms: int | None = None) -> AgentMessage:
        """Receive one semantic message within the bounded deadline."""

        timeout = self._io_timeout_ms if timeout_ms is None else timeout_ms
        if timeout <= 0:
            raise ValueError("timeout_ms must be positive")
        self._ensure_open()
        try:
            item = self._frames.get(timeout=timeout / 1000)
        except queue.Empty as exc:
            raise AgentDeadlineError(
                f"no arena.agent.io.v1 reply within {timeout} ms"
            ) from exc
        if item is None:
            self._raise_crash()
        if isinstance(item, BaseException):
            if isinstance(item, ProtocolError):
                self.close()
                raise AgentProtocolViolationError(str(item)) from item
            raise item
        try:
            return parse_agent_message(item)
        except ProtocolError as exc:
            self.close()
            raise AgentProtocolViolationError(str(exc)) from exc

    def recv_hello(self, timeout_ms: int | None = None) -> HelloMessage:
        """Receive and validate the mandatory startup handshake."""

        message = self.recv(timeout_ms=timeout_ms)
        if not isinstance(message, HelloMessage):
            self.close()
            raise AgentProtocolViolationError("first child message must be hello")
        if message.schema_version != AGENT_IO_SCHEMA_VERSION:
            self.close()
            raise AgentProtocolViolationError(
                f"unsupported arena.agent.io.v1 schema version {message.schema_version}"
            )
        return message

    def stderr_text(self) -> str:
        """Bounded child stderr diagnostics, truncated at the configured limit."""

        data = bytes(self._stderr)
        if not data:
            return ""
        text = data.decode("utf-8", errors="replace")
        if self._stderr_truncated:
            text = f"{text}\n[stderr truncated after {self._stderr_limit} bytes]"
        return text.strip()

    def close(self) -> None:
        """Terminate the child process tree and release all resources."""

        if self._closed.is_set():
            return
        if self._watchdog is not None:
            self._watchdog.cancel()
        self._terminate()
        self._reader.join(timeout=5)
        self._stderr_reader.join(timeout=5)
        self._cleanup_temp_dir()
        self._closed.set()

    def __enter__(self) -> SubprocessAgentTransport:
        """Support ``with`` usage."""

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the transport on scope exit."""

        self.close()

    def _ensure_open(self) -> None:
        if self._closed.is_set():
            raise SubprocessTransportError("arena.agent.io.v1 transport is closed")

    def _read_loop(self) -> None:
        stdout = self._proc.stdout
        if stdout is None:
            self._frames.put(None)
            return
        fd = stdout.fileno()
        try:
            while not self._closed.is_set():
                chunk = os.read(fd, _READ_CHUNK_SIZE)
                if not chunk:
                    break
                for frame in self._decoder.feed(chunk):
                    self._frames.put(frame)
            self._decoder.finish()
            self._frames.put(None)
        except BaseException as exc:
            self._frames.put(exc)

    def _stderr_loop(self) -> None:
        stderr = self._proc.stderr
        if stderr is None:
            return
        fd = stderr.fileno()
        try:
            while True:
                chunk = os.read(fd, _STDERR_CHUNK_SIZE)
                if not chunk:
                    return
                remaining = self._stderr_limit - len(self._stderr)
                if remaining > 0:
                    self._stderr.extend(chunk[:remaining])
                if len(self._stderr) >= self._stderr_limit:
                    self._stderr_truncated = True
        except OSError:
            return

    def _raise_crash(self) -> NoReturn:
        code = self._proc.poll()
        if code is None:
            try:
                code = self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                raise AgentProcessCrashedError(
                    "child stdout closed unexpectedly"
                ) from None
        stderr = self.stderr_text()
        detail = f": {stderr}" if stderr else ""
        raise AgentProcessCrashedError(f"child exited with code {code}{detail}")

    def _terminate(self) -> None:
        if self._proc.poll() is not None:
            return
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            if sys.platform == "win32":
                subprocess.run(  # noqa: S603 - fixed argv list
                    [_windows_taskkill_path(), "/PID", str(self._proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    timeout=10,
                    check=False,
                )  # nosec B603
            else:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._proc.wait(timeout=5)
        if sys.platform != "win32":
            with contextlib.suppress(OSError):
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._proc.wait(timeout=5)

    def _cleanup_temp_dir(self) -> None:
        temp_root = Path(tempfile.gettempdir()).resolve()
        target = Path(self._temp_dir).resolve()
        if target.parent != temp_root or not target.name.startswith(_TEMP_PREFIX):
            return
        shutil.rmtree(target, ignore_errors=True)


def _windows_taskkill_path() -> str:
    """Absolute taskkill path so process-tree scans see a full executable."""

    system_root = os.environ.get("SYSTEMROOT")
    if system_root:
        candidate = os.path.join(system_root, "System32", "taskkill.exe")
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(r"C:\Windows", "System32", "taskkill.exe")


__all__ = [
    "DEFAULT_ENV_ALLOWLIST",
    "DEFAULT_IO_TIMEOUT_MS",
    "DEFAULT_STDERR_LIMIT",
    "AgentDeadlineError",
    "AgentProcessCrashedError",
    "AgentProtocolViolationError",
    "SubprocessAgentTransport",
    "SubprocessTransportError",
    "build_child_env",
]
