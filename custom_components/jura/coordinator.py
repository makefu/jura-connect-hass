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
from jura_connect import MachineProfile, ProductDef, load_profile

from .backends.base import JuraAuthError, JuraBackend, JuraBackendError, MachineSnapshot
from .backends.jura import JuraConnectBackend
from .brew import remember_param, selection_for_product
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

# The Jura dongle serves only ONE TCP session at a time. A poll that races
# a brew/command session — or hits the machine while it is busy dispensing —
# gets a transient connection refusal. Tolerate this many consecutive failed
# polls before surfacing OFFLINE: below the threshold we keep serving the last
# good snapshot (connectivity stays on), so a single blip during brewing does
# not flap the connectivity sensor. A real outage still flips after N polls.
OFFLINE_TOLERANCE = 2

# A followed brew keeps its TCP session open for the whole dispense, so
# polls during that window hit a busy dongle and fail — that is our own
# session, not an outage. See _handle_offline_poll.
FOLLOW_BREW_SECONDS = 120.0

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
    "no pushed @tf:",
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
        # The machine's brewable-product table, resolved once from the
        # jura_connect library so the brew control-panel entities and the brew
        # button can enumerate products + parameter ranges without re-parsing.
        # ``async_setup_entry`` primes load_profile's cache in an executor
        # first, so this @lru_cache'd call is a pure cache hit — no blocking
        # disk I/O in the event loop. (Tests warm the cache at import.)
        self.brew_profile: MachineProfile | None = self._load_brew_profile(config_entry)
        # The single staged "next brew" selection shared by the whole machine.
        # ``product`` is a product Code as a 2-hex-uppercase string (JSON-safe;
        # ``ProductDef.code`` is an int, so convert on lookup). ``None`` for a
        # parameter means "Factory Default" — the recipe builder falls back to
        # that product's XML default. It does NOT mean "use whatever the machine
        # currently has stored": JURA WiFi exposes no such mechanism. The brew
        # control-panel selects write here; the brew button reads from here.
        # Purely local — nothing is sent to the machine until the user presses
        # the button or calls the brew service.
        self.brew_selection: dict[str, int | str | None] = {
            "product": None,
            "strength": None,
            "water_ml": None,
            "temp": None,
            "milk_s": None,
            "milk_foam_s": None,
        }
        if self.brew_profile is not None and self.brew_profile.products:
            self.brew_selection["product"] = f"{self.brew_profile.products[0].code:02X}"
        # Persistent per-product brew preferences: product Code (2-hex string) ->
        # {"strength"|"water_ml"|"temp"|"milk_s"|"milk_foam_s": int | None},
        # where ``None`` == Factory
        # Default. Loaded from disk by ``async_load_brew_prefs`` at setup and
        # written back (debounced) by ``save_brew_prefs``.
        self.brew_prefs: dict[str, dict[str, int | None]] = {}
        self._brew_prefs_store: Store | None = None
        # Serialise all machine I/O (polls + commands + setting writes) so the
        # coordinator never opens a second TCP session while one is in flight —
        # the dongle only serves one at a time. Held only for the duration of a
        # single backend call (a brew @TP: returns as soon as the machine
        # accepts it; the physical dispense continues after the session closes).
        self._session_lock = asyncio.Lock()
        # Consecutive offline-classified poll failures; see OFFLINE_TOLERANCE.
        self._consecutive_offline = 0
        # True while a followed brew holds the session; suppresses offline
        # classification for polls that collide with it.
        self._follow_brew_active = False

    @staticmethod
    def _load_brew_profile(config_entry: ConfigEntry) -> MachineProfile | None:
        """Resolve the machine's product profile from the library, or ``None``.

        An unknown / unset machine type is a configuration condition, not a
        hard error: the brew control panel simply isn't created (mirrors the
        select / number platforms).
        """
        machine_type = config_entry.data.get(CONF_MACHINE_TYPE)
        if not machine_type:
            return None
        try:
            return load_profile(machine_type)
        except KeyError:
            _LOGGER.warning("no profile for machine_type %s; brew panel disabled", machine_type)
            return None

    def selected_product(self) -> ProductDef | None:
        """Return the currently-staged :class:`ProductDef`, or ``None``."""
        if self.brew_profile is None:
            return None
        code = self.brew_selection.get("product")
        if code is None:
            return None
        return self.brew_profile.product_by_code.get(int(str(code), 16))

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
            self.brew_selection.update(selection_for_product(self.brew_prefs, str(code)))

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
        when one exists so last-known values are retained internally, and
        flips ``handshake_state`` to the OFFLINE sentinel: entities render
        that as ``unavailable`` (never as zeros), while the connectivity
        binary_sensor surfaces the reachability flip. On the very first
        poll (no prior data) we return a minimal snapshot keyed by the
        configured address + conn_id — with ``brews_total`` left at its
        ``None`` default rather than a fabricated 0.
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
            async with self._session_lock:
                snapshot = await self.backend.fetch()
        except JuraAuthError as err:
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except JuraBackendError as err:
            if _is_offline_error(err):
                return self._handle_offline_poll(err)
            raise UpdateFailed(f"backend error: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"unexpected error: {err}") from err
        self._consecutive_offline = 0
        return snapshot

    def _handle_offline_poll(self, err: BaseException) -> MachineSnapshot:
        """Classify an offline poll: tolerate a transient blip, else go OFFLINE.

        While our own followed-brew session owns the socket, a failed poll
        is expected — keep serving the last snapshot (or synthesise one if
        there is none yet) instead of counting it toward the outage streak.

        The dongle serves one TCP session at a time, so a poll that races a
        brew/command session (or hits the machine mid-dispense) fails even
        though the machine is reachable. Below OFFLINE_TOLERANCE we keep serving
        the last good snapshot so connectivity does not flap; only a sustained
        failure (or no prior data) surfaces the OFFLINE snapshot.
        """
        if self._follow_brew_active:
            if self.data is not None:
                return self.data
            return self._offline_snapshot()
        self._consecutive_offline += 1
        if self._consecutive_offline < OFFLINE_TOLERANCE and self.data is not None:
            _LOGGER.debug(
                "transient machine blip (%d/%d), keeping last snapshot: %s",
                self._consecutive_offline,
                OFFLINE_TOLERANCE,
                err,
            )
            return self.data
        _LOGGER.debug(
            "machine unreachable (%d consecutive), surfacing OFFLINE snapshot: %s",
            self._consecutive_offline,
            err,
        )
        return self._offline_snapshot()

    async def run_command(
        self,
        name: str,
        args: list[str] | tuple[str, ...] = (),
        *,
        allow_destructive: bool,
    ) -> dict[str, Any]:
        """Dispatch a named command, then refresh state so HA reflects the change."""
        try:
            async with self._session_lock:
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

    async def run_brew(self, recipe: str, *, follow_seconds: float = FOLLOW_BREW_SECONDS) -> dict[str, Any]:
        """Start a brew and follow its progress stream, then refresh state.

        Blocks for the duration of the dispense (bounded by
        ``follow_seconds``) because the ``@TV:`` stream needs the session
        open. Polls colliding with it are not treated as an outage.
        """
        async with self._session_lock:
            self._follow_brew_active = True
            try:
                result = await self.backend.follow_brew(recipe, follow_seconds=follow_seconds)
            finally:
                self._follow_brew_active = False
        await self.async_request_refresh()
        frames = result.get("frames") or []
        if self.data is not None:
            self.async_set_updated_data(dataclasses.replace(self.data, progress=frames[-1] if frames else None))
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
            async with self._session_lock:
                new_hex = await self.backend.write_setting(name, value)
        except JuraAuthError as err:
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except JuraBackendError as err:
            raise UpdateFailed(f"backend error: {err}") from err

        if self.data is not None and new_hex:
            updated_settings = {**self.data.settings, name: new_hex}
            self.async_set_updated_data(dataclasses.replace(self.data, settings=updated_settings))
