"""Adapter contract for arena.agent.io.v1 message handlers.

A handler is the minimal agent-side seam shared by the trusted in-memory
adapter, the isolated subprocess child, and replay. It is an orchestration
contract only: it never embeds framing or process policy.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .messages import AgentMessage


class AgentHandler(Protocol):
    """Minimal agent contract implemented by arena.agent.io.v1 handlers."""

    def hello(self) -> AgentMessage:
        """Return the agent startup handshake message."""

    def handle(self, message: AgentMessage) -> Sequence[AgentMessage]:
        """Return the replies to one runner message (possibly none)."""


__all__ = ["AgentHandler"]
