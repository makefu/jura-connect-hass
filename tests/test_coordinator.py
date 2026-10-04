"""Coordinator tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.jura.backends.base import JuraAuthError, JuraBackendError
from custom_components.jura.coordinator import (
    HANDSHAKE_STATE_OFFLINE,
    OFFLINE_TOLERANCE,
    JuraCoordinator,
    _is_offline_error,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed


def _wrap_backend_error(cause: BaseException, msg: str = "I/O error talking to machine") -> JuraBackendError:
    """Mirror jura.py's ``raise JuraBackendError(...) from err`` wrap."""
    try:
        raise cause
    except BaseException as exc:  # noqa: BLE001 — explicit re-wrap of arbitrary exception
        try:
            raise JuraBackendError(f"{msg}: {exc}") from exc
        except JuraBackendError as wrapped:
            return wrapped


def _make_coordinator(backend, fake_config_entry) -> JuraCoordinator:
    hass = AsyncMock()
    return JuraCoordinator(hass, fake_config_entry, backend=backend)


async def test_async_update_data_returns_snapshot(mock_backend, fake_config_entry, sample_snapshot):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    data = await coordinator._async_update_data()
    assert data == sample_snapshot
    mock_backend.fetch.assert_awaited_once()


async def test_async_update_data_auth_error_raises_auth_failed(mock_backend, fake_config_entry):
    mock_backend.fetch.side_effect = JuraAuthError("bad hash")
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_async_update_data_backend_error_raises_update_failed(mock_backend, fake_config_entry):
    # A plain JuraBackendError with no cause chain and no offline-class
    # message should still surface as UpdateFailed.
    mock_backend.fetch.side_effect = JuraBackendError("setting 'foo': out of range")
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_async_update_data_offline_after_successful_poll_returns_offline_snapshot(
    mock_backend, fake_config_entry, sample_snapshot
):
    """Regression: machine goes offline AFTER a successful poll.

    Coordinator must surface an OFFLINE snapshot every cycle the machine
    is unreachable, not just the first one. Previously this raised
    UpdateFailed and flooded the log every 30s. With single-session
    debounce, OFFLINE surfaces after OFFLINE_TOLERANCE consecutive failures.
    """
    coordinator = _make_coordinator(mock_backend, fake_config_entry)

    # First poll: succeeds, populates coordinator.data.
    first = await coordinator._async_update_data()
    coordinator.data = first  # mirror what DataUpdateCoordinator does

    # Subsequent polls: backend raises the library's TimeoutError, wrapped
    # exactly the way backends/jura.py wraps OSError. It takes
    # OFFLINE_TOLERANCE consecutive failures to flip to OFFLINE.
    mock_backend.fetch.side_effect = _wrap_backend_error(
        TimeoutError("no reply to '@HU?' within 6.0s"),
    )
    snapshot = None
    for _ in range(OFFLINE_TOLERANCE):
        snapshot = await coordinator._async_update_data()
        coordinator.data = snapshot

    assert snapshot.handshake_state == HANDSHAKE_STATE_OFFLINE
    # Last-known counters / brews / identity preserved so entities don't
    # flip to None.
    assert snapshot.counters == sample_snapshot.counters
    assert snapshot.brews == sample_snapshot.brews
    assert snapshot.address == sample_snapshot.address
    assert snapshot.machine_type == sample_snapshot.machine_type
    # Brew total carried through the OFFLINE replace — no 0 spike mid-outage.
    assert snapshot.brews_total == sample_snapshot.brews_total


async def test_async_update_data_transient_blip_keeps_last_snapshot(mock_backend, fake_config_entry, sample_snapshot):
    """A SINGLE failed poll (single-session race during a brew) must NOT flip
    the machine OFFLINE — the last good snapshot is served so connectivity
    doesn't flap."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = sample_snapshot
    mock_backend.fetch.side_effect = _wrap_backend_error(ConnectionRefusedError(111, "Connection refused"))

    snapshot = await coordinator._async_update_data()

    # Still the last good snapshot, NOT the OFFLINE sentinel.
    assert snapshot is sample_snapshot
    assert snapshot.handshake_state != HANDSHAKE_STATE_OFFLINE


async def test_async_update_data_offline_recovers_and_resets_debounce(mock_backend, fake_config_entry, sample_snapshot):
    """A successful poll after a blip resets the consecutive-failure counter,
    so the next blip is again tolerated rather than immediately OFFLINE."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = sample_snapshot
    err = _wrap_backend_error(ConnectionRefusedError(111, "Connection refused"))

    # Blip 1 -> tolerated.
    mock_backend.fetch.side_effect = err
    assert (await coordinator._async_update_data()) is sample_snapshot
    # Recovery -> resets the counter.
    mock_backend.fetch.side_effect = None
    mock_backend.fetch.return_value = sample_snapshot
    await coordinator._async_update_data()
    assert coordinator._consecutive_offline == 0
    # Blip 2 (isolated) -> tolerated again, not OFFLINE.
    mock_backend.fetch.side_effect = err
    snapshot = await coordinator._async_update_data()
    assert snapshot.handshake_state != HANDSHAKE_STATE_OFFLINE


async def test_async_update_data_offline_socket_timeout_classifies_offline(
    mock_backend, fake_config_entry, sample_snapshot
):
    """Socket-level ``timed out`` (TimeoutError, no errno) classifies as offline
    once the debounce threshold is crossed."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = sample_snapshot
    coordinator._consecutive_offline = OFFLINE_TOLERANCE - 1  # next failure flips
    mock_backend.fetch.side_effect = _wrap_backend_error(TimeoutError("timed out"))
    snapshot = await coordinator._async_update_data()
    assert snapshot.handshake_state == HANDSHAKE_STATE_OFFLINE


async def test_async_update_data_offline_connection_refused_classifies_offline(
    mock_backend, fake_config_entry, sample_snapshot
):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = sample_snapshot
    coordinator._consecutive_offline = OFFLINE_TOLERANCE - 1  # next failure flips
    mock_backend.fetch.side_effect = _wrap_backend_error(ConnectionRefusedError(111, "Connection refused"))
    snapshot = await coordinator._async_update_data()
    assert snapshot.handshake_state == HANDSHAKE_STATE_OFFLINE


async def test_session_lock_held_during_fetch_and_commands(mock_backend, fake_config_entry, sample_snapshot):
    """The dongle serves one TCP session at a time: both the poll and named
    commands must run while holding the shared session lock so they can never
    open two sockets at once."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.async_request_refresh = AsyncMock()
    held: dict[str, bool] = {}

    async def _fetch(*_a, **_k):
        held["fetch"] = coordinator._session_lock.locked()
        return sample_snapshot

    async def _run(*_a, **_k):
        held["command"] = coordinator._session_lock.locked()
        return {"ok": 1}

    mock_backend.fetch.side_effect = _fetch
    mock_backend.run_named.side_effect = _run

    await coordinator._async_update_data()
    await coordinator.run_command("status", (), allow_destructive=False)

    assert held["fetch"] is True
    assert held["command"] is True
    # Lock is released again once the calls return.
    assert coordinator._session_lock.locked() is False


async def test_async_update_data_offline_on_first_poll_returns_minimal_snapshot(mock_backend, fake_config_entry):
    """First poll, machine already off: no prior data to preserve."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    mock_backend.fetch.side_effect = _wrap_backend_error(TimeoutError("no reply to '@HU?' within 6.0s"))
    snapshot = await coordinator._async_update_data()
    assert snapshot.handshake_state == HANDSHAKE_STATE_OFFLINE
    assert snapshot.address == "192.0.2.10"
    assert snapshot.machine_type == "EF1091"
    assert snapshot.brews_total is None


async def test_outage_never_exposes_zero_brew_total(mock_backend, fake_config_entry, sample_snapshot):
    """End-to-end proof of the reported bug: good poll (brews_total=809) ->
    sustained outage -> the snapshot the entities see keeps 809 and is flagged
    OFFLINE. Since 1.0.0 the entity keeps rendering 809 through the outage
    (retain-when-offline is the default) instead of going unavailable."""
    from custom_components.jura.sensor import BrewTotalSensor

    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    good = await coordinator._async_update_data()
    coordinator.data = good
    entity = BrewTotalSensor(coordinator, fake_config_entry)
    assert entity.available is True
    assert entity.native_value == 809

    err = _wrap_backend_error(TimeoutError("no reply to '@HU?' within 6.0s"))
    mock_backend.fetch.side_effect = err
    for _ in range(OFFLINE_TOLERANCE):
        coordinator.data = await coordinator._async_update_data()
        # At no step during the outage does the counter read 0.
        assert coordinator.data.brews_total == 809
        assert entity.native_value == 809

    assert coordinator.data.handshake_state == HANDSHAKE_STATE_OFFLINE
    assert entity.available is True


def test_is_offline_error_classifies_library_timeout():
    err = _wrap_backend_error(TimeoutError("no reply to '@HU?' within 6.0s"))
    assert _is_offline_error(err) is True


def test_is_offline_error_classifies_socket_timeout():
    err = _wrap_backend_error(TimeoutError("timed out"))
    assert _is_offline_error(err) is True


def test_is_offline_error_classifies_connection_refused():
    err = _wrap_backend_error(ConnectionRefusedError(111, "Connection refused"))
    assert _is_offline_error(err) is True


def test_is_offline_error_rejects_non_io_backend_error():
    # A bare JuraBackendError (e.g. profile rejection) is NOT offline.
    assert _is_offline_error(JuraBackendError("setting 'foo': out of range")) is False


async def test_async_update_data_unexpected_error_raises_update_failed(mock_backend, fake_config_entry):
    mock_backend.fetch.side_effect = RuntimeError("boom")
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_run_command_dispatches_and_refreshes(mock_backend, fake_config_entry):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    mock_backend.run_named.return_value = {"name": "brew", "value": "ok"}

    result = await coordinator.run_command("brew", ["01"], allow_destructive=True)

    assert result == {"name": "brew", "value": "ok"}
    mock_backend.run_named.assert_awaited_once_with("brew", ["01"], allow_destructive=True)
    # The refresh path calls fetch via _async_update_data.
    mock_backend.fetch.assert_awaited()


async def test_run_command_auth_error_raises_auth_failed(mock_backend, fake_config_entry):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    mock_backend.run_named.side_effect = JuraAuthError("expired")
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.run_command("lock", [], allow_destructive=False)


async def test_run_command_backend_error_raises_update_failed(mock_backend, fake_config_entry):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    mock_backend.run_named.side_effect = JuraBackendError("timeout")
    with pytest.raises(UpdateFailed):
        await coordinator.run_command("clean", [], allow_destructive=True)


# ---------------------------------------------------------------------------
# run_brew — followed brew session
# ---------------------------------------------------------------------------


async def test_run_brew_refreshes_and_publishes_last_progress_frame(mock_backend, fake_config_entry, sample_snapshot):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    mock_backend.follow_brew.return_value = {
        "ack": "@tp",
        "frames": [{"state": "GRINDING_COFFEE", "percent": 30}],
    }
    # Prime the coordinator with a first snapshot so replace() has a base.
    coordinator.data = sample_snapshot

    result = await coordinator.run_brew("01020304")

    assert result["ack"] == "@tp"
    mock_backend.follow_brew.assert_awaited_once_with("01020304", follow_seconds=120.0)
    # The refresh ran, then the last frame landed in data.progress.
    mock_backend.fetch.assert_awaited()
    assert coordinator.data.progress == {"state": "GRINDING_COFFEE", "percent": 30}


async def test_run_brew_clears_follow_flag_and_offline_suppression(mock_backend, fake_config_entry, sample_snapshot):
    """While the brew session is open, a failed poll keeps the last snapshot
    (our own session owns the socket — not an outage)."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = sample_snapshot

    async def _follow(*_args, **_kwargs):
        assert coordinator._follow_brew_active is True
        # Simulate a colliding poll: offline-classified failure.
        snapshot = coordinator._handle_offline_poll(
            _wrap_backend_error(TimeoutError("no pushed @TF: status frame within 10s"))
        )
        assert snapshot is mock_backend.fetch.return_value or snapshot.handshake_state != HANDSHAKE_STATE_OFFLINE
        return {"ack": "@tp", "frames": []}

    mock_backend.follow_brew.side_effect = _follow
    await coordinator.run_brew("0102")
    assert coordinator._follow_brew_active is False


async def test_offline_poll_during_follow_brew_never_counts_toward_streak(
    mock_backend, fake_config_entry, sample_snapshot
):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = sample_snapshot
    coordinator._follow_brew_active = True
    err = _wrap_backend_error(TimeoutError("no pushed @TF: status frame within 10s"))
    snapshot = coordinator._handle_offline_poll(err)
    assert snapshot is sample_snapshot
    assert coordinator._consecutive_offline == 0


def test_is_offline_error_classifies_pushed_tf_timeout():
    """jura_connect 0.13's read_status timeout text classifies as offline."""
    err = _wrap_backend_error(TimeoutError("no pushed @TF: status frame within 10s"))
    assert _is_offline_error(err) is True


# ---------------------------------------------------------------------------
# write_setting — the bit that makes select/number entities reactive
# ---------------------------------------------------------------------------


async def test_write_setting_pushes_new_value_into_coordinator_data(mock_backend, fake_config_entry, sample_snapshot):
    """After write_setting returns, coordinator.data must reflect the new
    value immediately — entities read from coordinator.data, so any
    debounced refresh would leave them showing stale state."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = sample_snapshot
    mock_backend.write_setting.return_value = "12"  # 18 decimal, the canonical stored form

    await coordinator.write_setting("hardness", "18")

    assert coordinator.data.settings["hardness"] == "12"
    # Existing settings are preserved.
    assert coordinator.data.settings["language"] == sample_snapshot.settings["language"]


async def test_write_setting_no_op_on_data_when_data_is_none(mock_backend, fake_config_entry):
    """First-poll-not-yet-completed case: write succeeds, but there's no
    snapshot to update; coordinator must not crash."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = None
    mock_backend.write_setting.return_value = "12"

    await coordinator.write_setting("hardness", "18")  # must not raise

    assert coordinator.data is None


async def test_write_setting_auth_error_raises_auth_failed(mock_backend, fake_config_entry):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    mock_backend.write_setting.side_effect = JuraAuthError("expired")
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.write_setting("hardness", "18")


async def test_write_setting_backend_error_raises_update_failed(mock_backend, fake_config_entry):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    mock_backend.write_setting.side_effect = JuraBackendError("rejection")
    with pytest.raises(UpdateFailed):
        await coordinator.write_setting("hardness", "18")


# ---------------------------------------------------------------------------
# Persistent per-product brew preferences
# ---------------------------------------------------------------------------


async def test_async_load_brew_prefs_starts_empty(mock_backend, fake_config_entry):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    await coordinator.async_load_brew_prefs()
    assert coordinator.brew_prefs == {}


async def test_save_brew_prefs_is_noop_without_a_store(mock_backend, fake_config_entry):
    """Before async_load_brew_prefs wires a Store, saving must not raise."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    await coordinator.save_brew_prefs()  # must not raise
    assert coordinator.brew_prefs == {}


async def test_set_brew_param_updates_selection_and_prefs(mock_backend, fake_config_entry):
    """A param change stages the value AND records it for the current product."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    await coordinator.async_load_brew_prefs()
    coordinator.select_brew_product("03")
    coordinator.set_brew_param("water_ml", 130)
    assert coordinator.brew_selection["water_ml"] == 130
    assert coordinator.brew_prefs["03"]["water_ml"] == 130


def test_set_brew_param_factory_default_records_none(mock_backend, fake_config_entry):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.select_brew_product("03")
    coordinator.set_brew_param("water_ml", 130)
    coordinator.set_brew_param("water_ml", None)  # Factory Default
    assert coordinator.brew_selection["water_ml"] is None
    assert coordinator.brew_prefs["03"]["water_ml"] is None


def test_select_brew_product_loads_saved_prefs_into_selection(mock_backend, fake_config_entry):
    """Switching product hydrates the staged selection from that product's prefs."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.brew_prefs["03"] = {"strength": 2, "water_ml": 130, "temp": 1}
    coordinator.select_brew_product("03")
    assert coordinator.brew_selection == {
        "product": "03",
        "strength": 2,
        "water_ml": 130,
        "temp": 1,
        "grinder_ratio": None,
        "milk_s": None,
        "milk_foam_s": None,
    }


def test_select_brew_product_without_prefs_is_all_factory_default(mock_backend, fake_config_entry):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.brew_selection.update(strength=4, water_ml=120, temp=2)
    coordinator.select_brew_product("03")  # no saved prefs for 03
    assert coordinator.brew_selection == {
        "product": "03",
        "strength": None,
        "water_ml": None,
        "temp": None,
        "grinder_ratio": None,
        "milk_s": None,
        "milk_foam_s": None,
    }


async def test_brew_prefs_persist_across_coordinator_restarts(mock_backend, fake_config_entry):
    """The net contract: set a pref, restart (new coordinator), it's remembered."""
    c1 = _make_coordinator(mock_backend, fake_config_entry)
    await c1.async_load_brew_prefs()
    c1.select_brew_product("03")
    c1.set_brew_param("water_ml", 130)
    await c1.save_brew_prefs()

    c2 = _make_coordinator(mock_backend, fake_config_entry)
    await c2.async_load_brew_prefs()
    assert c2.brew_prefs["03"]["water_ml"] == 130
    c2.select_brew_product("03")
    assert c2.brew_selection["water_ml"] == 130
