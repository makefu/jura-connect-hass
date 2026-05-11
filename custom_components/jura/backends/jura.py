"""Concrete backend bridging the integration to the ``jura_connect`` library.

The library is synchronous and serves one TCP session at a time. We open
a fresh :class:`jura_connect.JuraClient` for every operation and ferry
all blocking I/O through :func:`asyncio.to_thread` so the coordinator
keeps running on the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from jura_connect import (
    DestructiveCommandError,
    HandshakeError,
    JuraClient,
    PairingTimeout,
    discover,
    run_named,
    scan_tcp,
    tcp_probe,
)

from .base import (
    DiscoveredMachine,
    JuraAuthError,
    JuraBackend,
    JuraBackendError,
    MachineSnapshot,
)

_LOGGER = logging.getLogger(__name__)


class JuraConnectBackend(JuraBackend):
    """Async wrapper around ``jura_connect.JuraClient``."""

    def __init__(
        self,
        address: str,
        port: int = 51515,
        *,
        pin: str = "",
        conn_id: str,
        auth_hash: str = "",
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
    ) -> None:
        self.address = address
        self.port = port
        self.pin = pin
        self.conn_id = conn_id
        self.auth_hash = auth_hash
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    def _make_client(self) -> JuraClient:
        return JuraClient(
            self.address,
            self.port,
            pin=self.pin,
            conn_id=self.conn_id,
            auth_hash=self.auth_hash,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
        )

    async def pair(self, on_prompt: Callable[[str], None] | None = None) -> str:
        def _do() -> str:
            client = self._make_client()
            try:
                result = client.pair(on_user_prompt=on_prompt)
            except PairingTimeout as err:
                raise JuraAuthError(f"pairing timed out: {err}") from err
            except HandshakeError as err:
                raise JuraAuthError(f"handshake failed: {err}") from err
            except OSError as err:
                raise JuraBackendError(f"connection failed: {err}") from err
            finally:
                client.close()
            if result.state != "CORRECT" or not result.new_hash:
                raise JuraAuthError(f"pairing rejected: {result.state}")
            return result.new_hash

        new_hash = await asyncio.to_thread(_do)
        self.auth_hash = new_hash
        return new_hash

    async def fetch(self) -> MachineSnapshot:
        def _do() -> MachineSnapshot:
            client = self._make_client()
            try:
                handshake = client.connect()
                if handshake.state != "CORRECT":
                    raise JuraAuthError(f"handshake state {handshake.state}")
                info = client.read_machine_info()
            except HandshakeError as err:
                raise JuraAuthError(str(err)) from err
            except OSError as err:
                raise JuraBackendError(f"I/O error talking to machine: {err}") from err
            finally:
                client.close()
            return MachineSnapshot(
                address=self.address,
                conn_id=info.conn_id,
                handshake_state=info.handshake_state,
                active_alerts=info.status.active_alerts,
                counters={
                    "cleaning": info.maintenance_counters.cleaning,
                    "filter_change": info.maintenance_counters.filter_change,
                    "decalc": info.maintenance_counters.decalc,
                    "cappu_rinse": info.maintenance_counters.cappu_rinse,
                    "coffee_rinse": info.maintenance_counters.coffee_rinse,
                    "cappu_clean": info.maintenance_counters.cappu_clean,
                },
                percents={
                    "cleaning": info.maintenance_percent.cleaning,
                    "filter_change": info.maintenance_percent.filter_change,
                    "decalc": info.maintenance_percent.decalc,
                },
                raw_status_hex=info.status.raw.hex().upper(),
            )

        return await asyncio.to_thread(_do)

    async def lock(self) -> None:
        await self.run_named("lock")

    async def unlock(self) -> None:
        await self.run_named("unlock")

    async def run_named(
        self,
        name: str,
        args: list[str] | tuple[str, ...] = (),
        *,
        allow_destructive: bool = False,
    ) -> dict[str, Any]:
        arg_tuple = tuple(args)

        def _do() -> dict[str, Any]:
            client = self._make_client()
            try:
                handshake = client.connect()
                if handshake.state != "CORRECT":
                    raise JuraAuthError(f"handshake state {handshake.state}")
                result = run_named(
                    client,
                    name,
                    arg_tuple,
                    allow_destructive=allow_destructive,
                )
            except DestructiveCommandError:
                raise
            except HandshakeError as err:
                raise JuraAuthError(str(err)) from err
            except OSError as err:
                raise JuraBackendError(f"I/O error talking to machine: {err}") from err
            finally:
                client.close()
            return result.to_dict()

        return await asyncio.to_thread(_do)


# --------------------------------------------------------------------- #
# Discovery helpers (async-friendly wrappers around the library)
# --------------------------------------------------------------------- #


async def discover_machines(
    *,
    udp_timeout: float = 3.0,
    tcp_timeout: float = 0.4,
) -> list[DiscoveredMachine]:
    """Run UDP broadcast first, fall back to a TCP sweep.

    Returns the union of both, deduplicated by address. Empty list if
    nothing was found or discovery raised — discovery should never break
    the config flow.
    """

    def _udp() -> list[DiscoveredMachine]:
        try:
            return [
                DiscoveredMachine(address=m.address, name=m.name, fw=m.fw, via="udp")
                for m in discover(timeout=udp_timeout)
            ]
        except OSError as err:
            _LOGGER.debug("UDP discovery failed: %s", err)
            return []

    def _tcp(skip: set[str]) -> list[DiscoveredMachine]:
        try:
            return [
                DiscoveredMachine(address=addr, name=addr, via="tcp")
                for addr in scan_tcp(timeout=tcp_timeout)
                if addr not in skip
            ]
        except OSError as err:
            _LOGGER.debug("TCP discovery failed: %s", err)
            return []

    udp_hits = await asyncio.to_thread(_udp)
    skip = {m.address for m in udp_hits}
    tcp_hits = await asyncio.to_thread(_tcp, skip)
    return udp_hits + tcp_hits


async def probe_address(address: str, port: int = 51515) -> bool:
    """Return True if ``address`` accepts TCP/51515."""

    def _do() -> bool:
        try:
            return tcp_probe(address, port=port)
        except OSError:
            return False

    return await asyncio.to_thread(_do)
