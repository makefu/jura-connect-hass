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
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .backends.base import JuraAuthError, JuraBackend, JuraBackendError, MachineSnapshot
from .backends.jura import JuraConnectBackend
from .brew import (
    MachineDefinition,
    ProductDef,
    jura_connect_xml_dir,
    load_definition,
    remember_param,
    selection_for_product,
)
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

# Persisted brew-preferences store. Bump the version only on an incompatible
# on-disk schema change. Saves are debounced by this many seconds so a flurry
# of select changes collapses into a single write.
BREW_PREFS_STORAGE_VERSION = 1
BREW_PREFS_SAVE_DELAY = 1.0

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
        # Backend construction loads the per-machine XML profile (a blocking
        # filesystem read), so it is deferred to ``_async_setup`` (executor).
        # Tests inject a pre-built backend and skip ``_async_setup``.
        if backend is not None:
            self.backend = backend
        # The machine's brewable-product table (from jura_connect's bundled
        # XMLs), loaded once so the brew control-panel entities and the brew
        # button can resolve products + argument ranges without re-parsing.
        self.brew_definition: MachineDefinition | None = self._load_brew_definition(config_entry)
        # The single staged "next brew" selection shared by the whole machine.
        # ``product`` is a product Code; ``None`` for a parameter means "Factory
        # Default" — send that product's XML/factory default value (i.e. pass
        # ``None`` to ``build_start_command``). It does NOT mean "use whatever
        # the machine currently has stored": JURA WiFi exposes no such
        # mechanism, so an explicit, clamped value is always sent. The brew
        # control-panel selects write here; the brew button reads from here.
        # Purely local — nothing is sent to the machine until the user presses
        # the button or calls the brew service.
        self.brew_selection: dict[str, int | str | None] = {
            "product": None,
            "strength": None,
            "water_ml": None,
            "temp": None,
        }
        if self.brew_definition is not None and self.brew_definition.products:
            self.brew_selection["product"] = self.brew_definition.products[0].code
        # Persistent per-product brew preferences: product Code (hex) ->
        # {"strength"|"water_ml"|"temp": int | None}, where ``None`` == Factory
        # Default. Loaded from disk by ``async_load_brew_prefs`` at setup and
        # written back (debounced) by ``save_brew_prefs``.
        self.brew_prefs: dict[str, dict[str, int | None]] = {}
        self._brew_prefs_store: Store | None = None

    @staticmethod
    def _load_brew_definition(config_entry: ConfigEntry) -> MachineDefinition | None:
        """Resolve + parse the machine's product definition, or ``None``.

        A missing ``jura_connect`` package / unknown machine type is a
        configuration / version-skew condition, not a hard error: the brew
        control panel simply isn't created.
        """
        machine_type = config_entry.data.get(CONF_MACHINE_TYPE)
        if not machine_type:
            return None
        base_dir = jura_connect_xml_dir()
        if base_dir is None:
            return None
        return load_definition(machine_type, base_dir=base_dir)

    def selected_product(self) -> ProductDef | None:
        """Return the currently-selected :class:`ProductDef`, or ``None``."""
        if self.brew_definition is None:
            return None
        code = self.brew_selection.get("product")
        if code is None:
            return None
        for product in self.brew_definition.products:
            if product.code == code:
                return product
        return None

    # --- persistent per-product brew preferences -------------------------
    def _brew_prefs_storage_key(self) -> str:
        return f"{DOMAIN}.brew_prefs.{self.config_entry.entry_id}"

    async def async_load_brew_prefs(self) -> None:
        """Wire the Store and load persisted brew prefs into ``brew_prefs``.

        Called once at config-entry setup. After loading, the staged selection
        is re-hydrated from the currently-selected (first) product's prefs so a
        fresh Home Assistant start shows remembered values rather than blanks.
        """
        self._brew_prefs_store = Store(self.hass, BREW_PREFS_STORAGE_VERSION, self._brew_prefs_storage_key())
        data = await self._brew_prefs_store.async_load()
        self.brew_prefs = data or {}
        code = self.brew_selection.get("product")
        if code is not None:
            self.brew_selection.update(selection_for_product(self.brew_prefs, code))

    async def save_brew_prefs(self) -> None:
        """Schedule a debounced write of ``brew_prefs`` to disk.

        No-op until ``async_load_brew_prefs`` has wired the Store. The data
        callback returns the live dict so the most recent edits are captured
        when the delayed save fires.
        """
        if self._brew_prefs_store is None:
            return
        self._brew_prefs_store.async_delay_save(lambda: self.brew_prefs, BREW_PREFS_SAVE_DELAY)

    def select_brew_product(self, code: str) -> None:
        """Make ``code`` the staged product, hydrating its saved prefs.

        Each parameter is loaded from the product's saved prefs (missing ->
        ``None`` == Factory Default), replacing whatever was staged for the
        previous product.
        """
        self.brew_selection.update(selection_for_product(self.brew_prefs, code))

    def set_brew_param(self, param: str, value: int | None) -> None:
        """Stage ``value`` for ``param`` and remember it for the current product.

        ``value`` is ``None`` for Factory Default, else the chosen value. Both
        the live selection and the persisted prefs for the current product are
        updated; callers schedule the disk write via :meth:`save_brew_prefs`.
        """
        self.brew_selection[param] = value
        code = self.brew_selection.get("product")
        if code is not None:
            remember_param(self.brew_prefs, str(code), param, value)

    async def _async_setup(self) -> None:
        # Build the backend off the event loop (blocking XML profile load);
        # honour a test-injected backend assigned eagerly in __init__.
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
