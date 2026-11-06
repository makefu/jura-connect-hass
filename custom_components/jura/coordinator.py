"""DataUpdateCoordinator for the Jura integration."""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from jura_connect import MachineProfile, ProductDef, load_profile

from .backends.base import ClockWriteUnverified, JuraAuthError, JuraBackend, JuraBackendError, MachineSnapshot
from .backends.jura import JuraConnectBackend
from .brew import remember_param, selection_for_product
from .clock import (
    CLOCK_CHECK_SECONDS,
    CLOCK_AUTO_RETRY_SECONDS,
    CLOCK_MODEL,
    CONF_CLOCK_NTP_SERVER,
    CONF_CLOCK_SYNC,
    DEFAULT_CLOCK_NTP_SERVER,
    ClockReference,
    ClockSyncStatus,
    NtpStatus,
    check_ntp,
    clock_auto_period,
    clock_machine_ready,
)
from .const import (
    CONF_AUTH_HASH,
    CONF_BREW_FOLLOW_SECONDS,
    CONF_CONN_ID,
    CONF_HOST,
    CONF_MACHINE_TYPE,
    CONF_PIN,
    CONF_PORT,
    CONF_SESSION_COOLDOWN_SECONDS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

# Narrow seam so cooldown tests can fake sleeping without proxying asyncio.
_sleep = asyncio.sleep

_LOGGER = logging.getLogger(__name__)


def clock_sync_enabled(entry: ConfigEntry) -> bool:
    options = getattr(entry, "options", None) or {}
    return entry.data.get(CONF_MACHINE_TYPE) == CLOCK_MODEL and options.get(CONF_CLOCK_SYNC) in ("manual", "automatic")


def automatic_clock_sync_enabled(entry: ConfigEntry) -> bool:
    return clock_sync_enabled(entry) and entry.options.get(CONF_CLOCK_SYNC) == "automatic"


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

# Wi-Fi Connect may reject a new TCP session immediately after one closes.
# Apply the quiet period to every coordinator I/O path, including failures.
SESSION_COOLDOWN_SECONDS = 10.0

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
            "grinder_ratio": None,
            "milk_s": None,
            "milk_foam_s": None,
        }
        if self.brew_profile is not None and self.brew_profile.products:
            self.brew_selection["product"] = f"{self.brew_profile.products[0].code:02X}"
        # Persistent per-product brew preferences: product Code (2-hex string) ->
        # {"strength"|"water_ml"|"temp"|"grinder_ratio"|"milk_s"|
        #  "milk_foam_s": int | None},
        # where ``None`` == Factory
        # Default. Loaded from disk by ``async_load_brew_prefs`` at setup and
        # written back (debounced) by ``save_brew_prefs``.
        self.brew_prefs: dict[str, dict[str, int | None]] = {}
        self._brew_prefs_store: Store | None = None
        self.clock_sync_status = ClockSyncStatus()
        self.clock_last_check: datetime | None = None
        self._clock_last_check_store: Store[str] | None = None
        self._clock_sync_task: asyncio.Task[None] | None = None
        self._clock_auto_store: Store | None = None
        self._clock_auto_zone: ZoneInfo | None = None
        self._clock_auto_periods: list[str] = []
        self._clock_auto_retry_at = 0.0
        self._clock_shutting_down = False
        # Serialise all machine I/O (polls + commands + setting writes) so the
        # coordinator never opens a second TCP session while one is in flight —
        # the dongle only serves one at a time. The lock also covers the quiet
        # period before the next backend call. Followed brews hold their session
        # until the progress stream ends or times out. Both durations are
        # user-tunable via the hidden options-flow fields (entry.options wins
        # over entry.data); read once at setup, changes reload the entry.
        self._session_lock = asyncio.Lock()
        self._last_session_end: float | None = None
        self.session_cooldown_seconds = self._entry_seconds(
            config_entry, CONF_SESSION_COOLDOWN_SECONDS, SESSION_COOLDOWN_SECONDS
        )
        self.brew_follow_seconds = self._entry_seconds(config_entry, CONF_BREW_FOLLOW_SECONDS, FOLLOW_BREW_SECONDS)
        # Consecutive offline-classified poll failures; see OFFLINE_TOLERANCE.
        self._consecutive_offline = 0
        # True while a followed brew holds the session; suppresses offline
        # classification for polls that collide with it.
        self._follow_brew_active = False
        # Poll health is separate from the retained last-known snapshot: a
        # tolerated offline poll must not look like a fresh successful read.
        self.last_successful_update: str | None = None
        self.last_poll_success = False

    @staticmethod
    def _entry_seconds(config_entry: ConfigEntry, key: str, default: float) -> float:
        options = getattr(config_entry, "options", None) or {}
        if key in options:
            return float(options[key])
        return float(config_entry.data.get(key, default))

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

    @property
    def clock_sync_available(self) -> bool:
        return bool(
            clock_sync_enabled(self.config_entry)
            and self.last_poll_success
            and self.last_update_success
            and self.data is not None
            and self.data.handshake_state == "CORRECT"
            and clock_machine_ready(self.data.active_alerts, self.data.errors)
            and not self._follow_brew_active
            and not self._clock_shutting_down
            and (self._clock_sync_task is None or self._clock_sync_task.done())
        )

    async def async_load_clock_last_check(self) -> None:
        self._clock_last_check_store = Store(self.hass, 1, f"{DOMAIN}.clock_last_check.{self.config_entry.entry_id}")
        value = await self._clock_last_check_store.async_load()
        if isinstance(value, str):
            try:
                checked_at = datetime.fromisoformat(value)
            except ValueError:
                return
            if checked_at.utcoffset() is not None:
                self.clock_last_check = checked_at

    async def _async_record_clock_check(self, checked_at: datetime) -> None:
        self.clock_last_check = checked_at
        if self._clock_last_check_store is not None:
            await self._clock_last_check_store.async_save(checked_at.isoformat())

    async def async_load_clock_auto_state(self) -> None:
        self._clock_auto_zone = await self.hass.async_add_executor_job(ZoneInfo, self.hass.config.time_zone)
        self._clock_auto_store = Store(self.hass, 1, f"{DOMAIN}.clock_sync.{self.config_entry.entry_id}")
        data = await self._clock_auto_store.async_load()
        if isinstance(data, list):
            self._clock_auto_periods = [period for period in data[-4:] if isinstance(period, str)]

    @callback
    def maybe_auto_clock_sync(self) -> None:
        if not automatic_clock_sync_enabled(self.config_entry) or not self.clock_sync_available:
            return
        if time.monotonic() < self._clock_auto_retry_at:
            return
        zone = self._clock_auto_zone
        if zone is not None and zone.key == self.hass.config.time_zone:
            local = datetime.now(zone)
            if clock_auto_period(local) in self._clock_auto_periods:
                return
        self._clock_auto_retry_at = time.monotonic() + CLOCK_AUTO_RETRY_SECONDS
        self._start_clock_sync(automatic=True)

    @callback
    def start_clock_sync(self) -> None:
        """Start an explicit one-off sync, tied to the entry lifecycle."""
        if not self.clock_sync_available:
            raise HomeAssistantError("JURA clock synchronization is unavailable or already running")
        self._start_clock_sync(automatic=False)

    @callback
    def _start_clock_sync(self, *, automatic: bool) -> None:
        self.clock_sync_status = ClockSyncStatus("checking")
        self._clock_sync_task = self.config_entry.async_create_background_task(
            self.hass, self._async_clock_sync(automatic=automatic), "jura_clock_sync", eager_start=False
        )
        self._clock_sync_task.add_done_callback(self._clock_sync_done)
        self.async_update_listeners()

    @callback
    def _clock_sync_done(self, task: asyncio.Task[None]) -> None:
        if self._clock_sync_task is task:
            self._clock_sync_task = None
            self.async_update_listeners()

    async def async_cancel_clock_sync(self) -> None:
        self._clock_shutting_down = True
        task = self._clock_sync_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _async_clock_sync(self, *, automatic: bool = False) -> None:
        ntp: NtpStatus | None = None
        reserved_period: str | None = None
        wrote = False
        try:
            zone = await self.hass.async_add_executor_job(ZoneInfo, self.hass.config.time_zone)
            self._clock_auto_zone = zone
            reference = ClockReference.capture(zone)
            period = clock_auto_period(reference.now())
            if automatic and period in self._clock_auto_periods:
                self.clock_sync_status = ClockSyncStatus()
                return
            server = self.config_entry.options.get(CONF_CLOCK_NTP_SERVER, DEFAULT_CLOCK_NTP_SERVER)
            ntp = await check_ntp(server, reference)
            if ntp == "disagrees":
                raise ValueError("NTP disagrees with the Home Assistant clock; no JURA setting was written")
            async with self._machine_session():
                if automatic:
                    if not automatic_clock_sync_enabled(self.config_entry):
                        raise ValueError("Automatic JURA clock synchronization is disabled")
                    # Reserve before asking the backend to write. A shutdown
                    # or lost reply must not allow another write after reload.
                    reserved_period = period
                    await self._save_clock_auto_periods([*self._clock_auto_periods, period][-4:])
                update = await self.backend.start_clock_sync(reference)
                wrote = update.wrote
            if not update.wrote:
                self.clock_sync_status = ClockSyncStatus("already_aligned", ntp, update.reading)
                await self._async_record_clock_check(update.reading.observed_at)
                return

            self.clock_sync_status = ClockSyncStatus("verifying", ntp, update.reading)
            self.async_update_listeners()
            # Leave the dongle available for normal polling and brewing
            # between the settled readback and the running-clock check.
            await asyncio.sleep(CLOCK_CHECK_SECONDS)
            async with self._machine_session():
                running = await self.backend.read_clock(reference)
            if (
                not running.same_schedule(update.reading)
                or running.observed_at.utcoffset() != update.reading.observed_at.utcoffset()
                or running.clock <= update.reading.clock
                or not running.matches_ha()
            ):
                raise ValueError("The JURA clock did not pass the running check; inspect it before trying again")
            self.clock_sync_status = ClockSyncStatus("synchronized", ntp, running)
            await self._async_record_clock_check(running.observed_at)
        except (JuraBackendError, ValueError, KeyError) as err:
            self.clock_sync_status = ClockSyncStatus("failed", ntp, detail=str(err))
            if reserved_period is not None and not wrote and not isinstance(err, ClockWriteUnverified):
                # The backend failed before a possible write. Permit a
                # later ready poll to try after the conservative backoff.
                await self._save_clock_auto_periods([p for p in self._clock_auto_periods if p != reserved_period])
        finally:
            self.async_update_listeners()

    async def _save_clock_auto_periods(self, periods: list[str]) -> None:
        if self._clock_auto_store is not None:
            await self._clock_auto_store.async_save(periods)
        self._clock_auto_periods = periods

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
            clock_sync=clock_sync_enabled(config_entry),
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

    @asynccontextmanager
    async def _machine_session(self) -> AsyncIterator[None]:
        """Serialize I/O and leave the dongle quiet after the previous session.

        Cancellation during the cooldown opens no connection and does not move
        the previous end time. Once I/O starts, errors and cancellation still
        require a quiet period before the next attempt. No command is retried.
        """
        async with self._session_lock:
            if self._last_session_end is not None:
                remaining = self.session_cooldown_seconds - (time.monotonic() - self._last_session_end)
                if remaining > 0:
                    await _sleep(remaining)
            try:
                yield
            finally:
                self._last_session_end = time.monotonic()

    async def _async_update_data(self) -> MachineSnapshot:
        try:
            async with self._machine_session():
                snapshot = await self.backend.fetch()
        except JuraAuthError as err:
            self.last_poll_success = False
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except JuraBackendError as err:
            self.last_poll_success = False
            if _is_offline_error(err):
                return self._handle_offline_poll(err)
            raise UpdateFailed(f"backend error: {err}") from err
        except Exception as err:  # noqa: BLE001
            self.last_poll_success = False
            raise UpdateFailed(f"unexpected error: {err}") from err
        self.last_successful_update = datetime.now(UTC).isoformat()
        self.last_poll_success = True
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
            async with self._machine_session():
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

    async def run_brew(self, recipe: str, *, follow_seconds: float | None = None) -> dict[str, Any]:
        """Start a brew and follow its progress stream, then refresh state.

        Blocks for the duration of the dispense (bounded by
        ``follow_seconds``) because the ``@TV:`` stream needs the session
        open. Defaults to the ``brew_follow_seconds`` entry option. Polls
        colliding with it are not treated as an outage.
        """
        if follow_seconds is None:
            follow_seconds = self.brew_follow_seconds
        async with self._machine_session():
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
            async with self._machine_session():
                new_hex = await self.backend.write_setting(name, value)
        except JuraAuthError as err:
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except JuraBackendError as err:
            raise UpdateFailed(f"backend error: {err}") from err

        if self.data is not None and new_hex:
            updated_settings = {**self.data.settings, name: new_hex}
            self.async_set_updated_data(dataclasses.replace(self.data, settings=updated_settings))
