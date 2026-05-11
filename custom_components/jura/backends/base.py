"""Abstract backend interface for talking to a Jura machine.

A backend hides the synchronous nature of the ``jura_connect`` library
from the rest of the integration; concrete implementations are
responsible for marshalling blocking I/O onto a thread executor so the
coordinator/config-flow code stays naturally async.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MachineSnapshot:
    """One snapshot of all read-only state we pull on each poll."""

    address: str
    conn_id: str
    handshake_state: str
    active_alerts: tuple[str, ...]
    counters: dict[str, int] = field(default_factory=dict)
    percents: dict[str, int] = field(default_factory=dict)
    raw_status_hex: str = ""


@dataclass(frozen=True)
class DiscoveredMachine:
    """A machine surfaced by discovery (UDP broadcast or TCP fallback)."""

    address: str
    name: str
    fw: str = ""
    via: str = "udp"  # "udp" | "tcp"


class JuraBackendError(Exception):
    """Base for backend-level failures."""


class JuraAuthError(JuraBackendError):
    """Pairing / handshake rejected by the machine (re-auth required)."""


class JuraBackend(ABC):
    """Async-facing protocol used by the coordinator and config flow."""

    @abstractmethod
    async def pair(self, on_prompt: Callable[[str], None] | None = None) -> str:
        """Run the pair flow. Returns the new auth-hash to persist."""

    @abstractmethod
    async def fetch(self) -> MachineSnapshot:
        """Read status + maintenance counters + percents in one round."""

    @abstractmethod
    async def lock(self) -> None:
        """Lock the front-panel display."""

    @abstractmethod
    async def unlock(self) -> None:
        """Unlock the front-panel display."""

    @abstractmethod
    async def run_named(
        self,
        name: str,
        args: list[str] | tuple[str, ...] = (),
        *,
        allow_destructive: bool = False,
    ) -> dict[str, Any]:
        """Dispatch a named command from the library's registry."""
