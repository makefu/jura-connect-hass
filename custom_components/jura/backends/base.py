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
    # jura_connect 0.7+ splits the active alerts by severity (lifted
    # from the per-machine ALERT.Type XML attribute). Surface them so
    # entities can drive state and grouping off the cleaner split.
    errors: tuple[str, ...] = field(default_factory=tuple)
    info: tuple[str, ...] = field(default_factory=tuple)
    process: tuple[str, ...] = field(default_factory=tuple)
    # Per-product brew counts. ``brews`` is keyed by product name (e.g.
    # "espresso"); ``brews_total`` is the global counter from slot 0.
    # Empty dict if the machine doesn't support the @TR:32 statistics
    # bank (e.g. TT237W V06.11 reports zero pages).
    brews: dict[str, int] = field(default_factory=dict)
    brews_total: int = 0
    # Machine variant identification. ``machine_type`` is the EF code
    # ("EF1091" for an S8 EB) configured for the integration;
    # ``machine_type_name`` is the catalogue's friendly name
    # ("S8 (EB)"). Both are ``None`` when the user picked the baseline.
    machine_type: str | None = None
    machine_type_name: str | None = None
    # Current value of each machine setting, keyed by setting name.
    # The value is the raw hex string read from the wire — entities
    # decode it back to a friendly name via the profile's SettingDef.
    # Empty if no profile is configured or the machine doesn't expose
    # the requested setting.
    settings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveredMachine:
    """A machine surfaced by discovery (UDP broadcast or TCP fallback)."""

    address: str
    name: str
    fw: str = ""
    via: str = "udp"  # "udp" | "tcp"
    article_number: int | None = None  # from UDP broadcast; None for TCP-only hits


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

    @abstractmethod
    async def write_setting(self, name: str, value: str) -> str:
        """Write one machine setting by snake_case name + user-facing value.

        Implementations are responsible for validating ``value`` against
        the active machine profile and translating to the wire-format
        hex string. Returns the canonical stored value as read back
        from the dongle after the write — for most settings this is
        the same hex that was written, but for ItemSlider settings
        like AutoOFF the dongle stores a stripped form (``211E`` ->
        ``1E``) and the caller needs the stored form to update the
        in-memory snapshot.
        """
