"""Coordinator tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.jura.backends.base import JuraAuthError, JuraBackendError
from custom_components.jura.coordinator import (
    HANDSHAKE_STATE_OFFLINE,
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
    UpdateFailed and flooded the log every 30s.
    """
    coordinator = _make_coordinator(mock_backend, fake_config_entry)

    # First poll: succeeds, populates coordinator.data.
    first = await coordinator._async_update_data()
    coordinator.data = first  # mirror what DataUpdateCoordinator does

    # Second poll: backend raises the library's TimeoutError, wrapped
    # exactly the way backends/jura.py wraps OSError.
    mock_backend.fetch.side_effect = _wrap_backend_error(
        TimeoutError("no reply to '@HU?' within 6.0s"),
    )
    snapshot = await coordinator._async_update_data()

    assert snapshot.handshake_state == HANDSHAKE_STATE_OFFLINE
    # Last-known counters / brews / identity preserved so entities don't
    # flip to None.
    assert snapshot.counters == sample_snapshot.counters
    assert snapshot.brews == sample_snapshot.brews
    assert snapshot.address == sample_snapshot.address
    assert snapshot.machine_type == sample_snapshot.machine_type


async def test_async_update_data_offline_socket_timeout_returns_offline_snapshot(
    mock_backend, fake_config_entry, sample_snapshot
):
    """Socket-level ``timed out`` (TimeoutError, no errno) also classifies as offline."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = sample_snapshot
    mock_backend.fetch.side_effect = _wrap_backend_error(TimeoutError("timed out"))
    snapshot = await coordinator._async_update_data()
    assert snapshot.handshake_state == HANDSHAKE_STATE_OFFLINE


async def test_async_update_data_offline_connection_refused_returns_offline_snapshot(
    mock_backend, fake_config_entry, sample_snapshot
):
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = sample_snapshot
    mock_backend.fetch.side_effect = _wrap_backend_error(ConnectionRefusedError(111, "Connection refused"))
    snapshot = await coordinator._async_update_data()
    assert snapshot.handshake_state == HANDSHAKE_STATE_OFFLINE


async def test_async_update_data_offline_on_first_poll_returns_minimal_snapshot(mock_backend, fake_config_entry):
    """First poll, machine already off: no prior data to preserve."""
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    mock_backend.fetch.side_effect = _wrap_backend_error(TimeoutError("no reply to '@HU?' within 6.0s"))
    snapshot = await coordinator._async_update_data()
    assert snapshot.handshake_state == HANDSHAKE_STATE_OFFLINE
    assert snapshot.address == "192.0.2.10"
    assert snapshot.machine_type == "EF1091"


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
