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
    MachineProfile,
    PairingTimeout,
    discover,
    load_profile,
    lookup_by_article_number,
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


def _resolve_profile_and_name(machine_type: str | None) -> tuple[MachineProfile | None, str | None]:
    """Load the per-machine profile + look up its friendly name.

    Returns ``(None, None)`` for an unset or unknown machine_type so the
    backend silently falls through to the EF536 baseline.
    """
    if not machine_type:
        return None, None
    try:
        profile = load_profile(machine_type)
    except KeyError:
        _LOGGER.warning("unknown machine_type %r, falling through to baseline", machine_type)
        return None, None
    # Friendly-name lookup: walk the catalogue once.
    friendly: str | None = None
    from jura_connect import known_machine_names

    for name, code in known_machine_names():
        if code == machine_type:
            friendly = name
            break
    return profile, friendly


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
        machine_type: str | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
    ) -> None:
        self.address = address
        self.port = port
        self.pin = pin
        self.conn_id = conn_id
        self.auth_hash = auth_hash
        self.machine_type = machine_type or None
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._profile, self._machine_type_name = _resolve_profile_and_name(self.machine_type)

    def _make_client(self) -> JuraClient:
        return JuraClient(
            self.address,
            self.port,
            pin=self.pin,
            conn_id=self.conn_id,
            auth_hash=self.auth_hash,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            profile=self._profile,
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
                # Brew counters are optional — older firmware doesn't
                # populate the @TR:32 statistics bank, and per the
                # library docs returns empty pages. Tolerate any
                # failure here and ship the rest of the snapshot.
                try:
                    counters = client.read_product_counters()
                    brews_total = counters.total
                    brews = dict(counters.by_name)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("product counters unavailable: %s", err)
                    brews_total = 0
                    brews = {}
                # Settings (per profile). Each read is one extra round
                # trip — 7 settings on EF1091 add ~350ms. Skip cleanly
                # when no profile is configured. ``list_settings`` and
                # ``get_setting`` were added in jura_connect 0.9.4 and
                # are the canonical name-based read path; both refuse
                # to run if no profile is loaded.
                settings_values: dict[str, str] = {}
                if self._profile is not None:
                    for setting in client.list_settings():
                        try:
                            settings_values[setting.name] = client.get_setting(setting.name).raw
                        except Exception as err:  # noqa: BLE001
                            _LOGGER.debug("setting %s unavailable: %s", setting.name, err)
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
                errors=info.status.errors,
                info=info.status.info,
                process=info.status.process,
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
                brews=brews,
                brews_total=brews_total,
                machine_type=self.machine_type,
                machine_type_name=self._machine_type_name,
                settings=settings_values,
            )

        return await asyncio.to_thread(_do)

    async def lock(self) -> None:
        await self.run_named("lock")

    async def unlock(self) -> None:
        await self.run_named("unlock")

    async def write_setting(self, name: str, value: str) -> str:
        """Write a setting and return the post-write stored hex.

        Validation happens inside :meth:`JuraClient.set_setting` —
        the library accepts either an ITEM name (``"30min"``,
        ``"english"``) or the wire-format hex, refuses anything else
        before the request hits the wire, and raises :class:`ValueError`
        on rejection.

        Returns the canonical read-back hex so the coordinator can
        update the in-memory snapshot without waiting for the next
        poll. For ItemSlider settings the dongle stores a stripped
        suffix (writing ``211E`` reads back ``1E``); the caller's
        :meth:`SettingDef.item_from_hex` resolves it for display.
        """
        if self._profile is None:
            raise JuraBackendError("no machine profile loaded; cannot write settings")
        if self._profile.setting_by_name.get(name) is None:
            raise JuraBackendError(f"setting {name!r} not in profile {self._profile.code}")

        def _do() -> str:
            client = self._make_client()
            try:
                handshake = client.connect()
                if handshake.state != "CORRECT":
                    raise JuraAuthError(f"handshake state {handshake.state}")
                try:
                    client.set_setting(name, value)
                except ValueError as err:
                    raise JuraBackendError(f"setting {name!r}: {err}") from err
                # Read back via the name-based API for the canonical
                # stored form. ``get_setting`` returns a SettingValue
                # whose ``raw`` is what's actually on the dongle
                # (post type-tag strip on AutoOFF etc.).
                stored = client.get_setting(name).raw
            except HandshakeError as err:
                raise JuraAuthError(str(err)) from err
            except OSError as err:
                raise JuraBackendError(f"I/O error talking to machine: {err}") from err
            finally:
                client.close()
            return stored

        return await asyncio.to_thread(_do)

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
                DiscoveredMachine(
                    address=m.address,
                    name=m.name,
                    fw=m.fw,
                    via="udp",
                    article_number=m.article_number or None,
                )
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


def machine_type_from_article(article_number: int | None) -> str | None:
    """Map a UDP-discovered article number to an EF code, if known."""
    if not article_number:
        return None
    entry = lookup_by_article_number(article_number)
    return entry.ef_code if entry else None
