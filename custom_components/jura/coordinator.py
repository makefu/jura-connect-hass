"""DataUpdateCoordinator for the Jura integration."""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .backends.base import JuraAuthError, JuraBackend, JuraBackendError, MachineSnapshot
from .backends.jura import JuraConnectBackend
from .const import (
    CONF_AUTH_HASH,
    CONF_CONN_ID,
    CONF_HOST,
    CONF_MACHINE_TYPE,
    CONF_PIN,
    CONF_PORT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Sentinel surfaced in MachineSnapshot.handshake_state when the machine
# is powered off / unreachable. Entities that care about reachability
# (ConnectivityBinarySensor) key off this value.
HANDSHAKE_STATE_OFFLINE = "OFFLINE"

# errno values that mean "the machine isn't answering" — connection
# refused, host/network unreachable, timeouts. Anything else (EACCES,
# EBADF, …) is a programming error and should still surface as
# UpdateFailed so HA logs and retries it loudly.
_OFFLINE_ERRNOS: frozenset[int] = frozenset(
    {
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.ECONNABORTED,
        errno.ETIMEDOUT,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.EHOSTDOWN,
        errno.ENETDOWN,
        errno.ENOTCONN,
    }
)

# Substrings used as a last-resort classifier when the cause chain has
# been stripped (defensive — the backend currently re-raises ``from err``
# so the typed path covers both library-raised TimeoutError and
# socket-level OSError).
_OFFLINE_MESSAGE_HINTS: tuple[str, ...] = (
    "no reply to",
    "timed out",
    "timeout",
    "connection refused",
    "no route to host",
    "host is down",
    "network is unreachable",
)


def _is_offline_error(err: BaseException) -> bool:
    """True if ``err`` represents an unreachable / powered-down machine.

    Walks the ``__cause__`` chain — the backend wraps OSError /
    TimeoutError (both the socket-level ``timed out`` and the
    library-level ``no reply to '@HU?' within 6.0s``) into a
    JuraBackendError via ``raise ... from err``, so the original type
    is preserved on ``__cause__``.
    """
    seen: set[int] = set()
    cur: BaseException | None = err
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, TimeoutError):
            return True
        if isinstance(cur, ConnectionError):
            return True
        if isinstance(cur, OSError) and cur.errno in _OFFLINE_ERRNOS:
            return True
        cur = cur.__cause__ or cur.__context__
    text = str(err).lower()
    return any(hint in text for hint in _OFFLINE_MESSAGE_HINTS)


class JuraCoordinator(DataUpdateCoordinator[MachineSnapshot]):
    """Polls one Jura machine and serves snapshots to entities."""

    config_entry: ConfigEntry
    backend: JuraBackend

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        *,
        backend: JuraBackend | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            config_entry=config_entry,
        )
        # JuraConnectBackend.__init__ loads the per-machine XML profile,
        # which is a blocking filesystem read. Defer construction to
        # _async_setup so it runs in an executor instead of the event loop.
        # Tests inject a pre-built backend and skip _async_setup; honor it
        # by assigning eagerly when provided.
        if backend is not None:
            self.backend = backend

    async def _async_setup(self) -> None:
        if hasattr(self, "backend"):
            return
        self.backend = await asyncio.to_thread(self._build_backend, self.config_entry)

    @staticmethod
    def _build_backend(config_entry: ConfigEntry) -> JuraBackend:
        data = config_entry.data
        return JuraConnectBackend(
            address=data[CONF_HOST],
            port=data.get(CONF_PORT, DEFAULT_PORT),
            pin=data.get(CONF_PIN, ""),
            conn_id=data[CONF_CONN_ID],
            auth_hash=data.get(CONF_AUTH_HASH, ""),
            machine_type=data.get(CONF_MACHINE_TYPE),
        )

    def _offline_snapshot(self) -> MachineSnapshot:
        """Synthesise an OFFLINE snapshot.

        Reuses the prior snapshot (counters, brews, settings, identity)
        when one exists so entities keep showing last-known values, and
        flips ``handshake_state`` to the OFFLINE sentinel so the
        connectivity binary_sensor can surface the reachability flip.
        On the very first poll (no prior data) we return a minimal
        snapshot keyed by the configured address + conn_id.
        """
        prior = self.data
        if prior is not None:
            return dataclasses.replace(prior, handshake_state=HANDSHAKE_STATE_OFFLINE)
        data = self.config_entry.data
        return MachineSnapshot(
            address=data.get(CONF_HOST, ""),
            conn_id=data.get(CONF_CONN_ID, ""),
            handshake_state=HANDSHAKE_STATE_OFFLINE,
            active_alerts=(),
            machine_type=data.get(CONF_MACHINE_TYPE),
        )

    async def _async_update_data(self) -> MachineSnapshot:
        try:
            return await self.backend.fetch()
        except JuraAuthError as err:
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except JuraBackendError as err:
            if _is_offline_error(err):
                _LOGGER.debug("machine unreachable, surfacing OFFLINE snapshot: %s", err)
                return self._offline_snapshot()
            raise UpdateFailed(f"backend error: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"unexpected error: {err}") from err

    async def run_command(
        self,
        name: str,
        args: list[str] | tuple[str, ...] = (),
        *,
        allow_destructive: bool,
    ) -> dict[str, Any]:
        """Dispatch a named command, then refresh state so HA reflects the change."""
        try:
            result = await self.backend.run_named(
                name,
                args,
                allow_destructive=allow_destructive,
            )
        except JuraAuthError as err:
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except JuraBackendError as err:
            raise UpdateFailed(f"backend error: {err}") from err

        await self.async_request_refresh()
        return result

    async def write_setting(self, name: str, value: str) -> None:
        """Write a machine setting (validated by the profile) and reflect it
        in coordinator state immediately.

        ``async_request_refresh`` is debounced — relying on it alone
        leaves the writable entities showing the stale value for up to
        the polling interval. The backend's post-write read-back gives
        us the canonical stored form, so we push it into ``self.data``
        via ``async_set_updated_data`` and notify listeners in one
        shot.
        """
        try:
            new_hex = await self.backend.write_setting(name, value)
        except JuraAuthError as err:
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except JuraBackendError as err:
            raise UpdateFailed(f"backend error: {err}") from err

        if self.data is not None and new_hex:
            updated_settings = {**self.data.settings, name: new_hex}
            self.async_set_updated_data(dataclasses.replace(self.data, settings=updated_settings))
