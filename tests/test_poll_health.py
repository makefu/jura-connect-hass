"""Poll health must distinguish fresh reads from retained snapshots.

All machine I/O is mocked; these tests never contact a device.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.jura import coordinator as coordinator_module
from custom_components.jura.backends.base import JuraAuthError, JuraBackendError
from custom_components.jura.coordinator import HANDSHAKE_STATE_OFFLINE, JuraCoordinator
from custom_components.jura.sensor import StateSensor
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed


FIRST_READ = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
SECOND_READ = datetime(2026, 10, 17, 10, 1, tzinfo=UTC)


def make_coordinator(mock_backend, fake_config_entry):
    return JuraCoordinator(AsyncMock(), fake_config_entry, backend=mock_backend)


def freeze_clock(monkeypatch, value=FIRST_READ):
    """Freeze coordinator timestamps while keeping the rest of datetime usable."""
    real_datetime = coordinator_module.datetime

    class FrozenDatetime:
        now = Mock(return_value=value)
        fromisoformat = staticmethod(real_datetime.fromisoformat)

    monkeypatch.setattr(coordinator_module, "datetime", FrozenDatetime)
    return FrozenDatetime.now


def test_poll_health_starts_unknown(mock_backend, fake_config_entry):
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    assert coordinator.last_successful_update is None
    assert coordinator.last_poll_success is False


async def test_success_reports_utc_and_sensor_attributes(mock_backend, fake_config_entry, sample_snapshot, monkeypatch):
    clock = freeze_clock(monkeypatch)
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = await coordinator._async_update_data()

    assert coordinator.data is sample_snapshot
    assert coordinator.last_successful_update == FIRST_READ.isoformat()
    assert coordinator.last_poll_success is True
    clock.assert_called_once()
    attributes = StateSensor(coordinator, fake_config_entry).extra_state_attributes
    assert attributes["last_successful_update"] == FIRST_READ.isoformat()
    assert attributes["last_poll_success"] is True
    assert attributes["active_alerts"] == list(sample_snapshot.active_alerts)


@pytest.mark.parametrize(
    ("failure", "raised"),
    [
        (JuraAuthError("wrong hash"), ConfigEntryAuthFailed),
        (JuraBackendError("unsupported value"), UpdateFailed),
        (RuntimeError("unexpected parser error"), UpdateFailed),
        (JuraBackendError("connection refused"), None),
    ],
)
async def test_failed_read_never_renews_last_success(mock_backend, fake_config_entry, monkeypatch, failure, raised):
    clock = freeze_clock(monkeypatch)
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = await coordinator._async_update_data()
    previous = coordinator.data
    mock_backend.fetch.side_effect = failure
    clock.return_value = SECOND_READ

    if raised:
        with pytest.raises(raised):
            await coordinator._async_update_data()
    else:
        # The first offline-classified failure is deliberately tolerated.
        assert await coordinator._async_update_data() is previous

    assert coordinator.last_successful_update == FIRST_READ.isoformat()
    assert coordinator.last_poll_success is False
    clock.assert_called_once()
    attributes = StateSensor(coordinator, fake_config_entry).extra_state_attributes
    assert attributes["last_successful_update"] == FIRST_READ.isoformat()
    assert attributes["last_poll_success"] is False


async def test_sustained_offline_keeps_stale_poll_health(mock_backend, fake_config_entry, monkeypatch):
    # Second consecutive offline poll crosses OFFLINE_TOLERANCE and surfaces
    # the synthesised OFFLINE snapshot; poll health must stay stale.
    clock = freeze_clock(monkeypatch)
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = await coordinator._async_update_data()
    mock_backend.fetch.side_effect = JuraBackendError("connection refused")
    clock.return_value = SECOND_READ

    first = await coordinator._async_update_data()
    second = await coordinator._async_update_data()

    assert first is not second
    assert second.handshake_state == HANDSHAKE_STATE_OFFLINE
    assert coordinator.last_successful_update == FIRST_READ.isoformat()
    assert coordinator.last_poll_success is False
    clock.assert_called_once()
    attributes = StateSensor(coordinator, fake_config_entry).extra_state_attributes
    assert attributes["last_successful_update"] == FIRST_READ.isoformat()
    assert attributes["last_poll_success"] is False


async def test_offline_then_auth_failure_keeps_stale_poll_health(mock_backend, fake_config_entry, monkeypatch):
    clock = freeze_clock(monkeypatch)
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = await coordinator._async_update_data()
    clock.return_value = SECOND_READ
    mock_backend.fetch.side_effect = JuraBackendError("connection refused")
    await coordinator._async_update_data()
    mock_backend.fetch.side_effect = JuraAuthError("wrong hash")

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    assert coordinator.last_successful_update == FIRST_READ.isoformat()
    assert coordinator.last_poll_success is False
    clock.assert_called_once()


async def test_recovery_renews_timestamp_and_success(mock_backend, fake_config_entry, monkeypatch):
    clock = freeze_clock(monkeypatch)
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = await coordinator._async_update_data()
    mock_backend.fetch.side_effect = JuraBackendError("connection refused")
    await coordinator._async_update_data()
    mock_backend.fetch.side_effect = None
    clock.return_value = SECOND_READ

    await coordinator._async_update_data()

    assert coordinator.last_successful_update == SECOND_READ.isoformat()
    assert coordinator.last_poll_success is True


async def test_followed_brew_does_not_fabricate_a_successful_poll(mock_backend, fake_config_entry, monkeypatch):
    clock = freeze_clock(monkeypatch)
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    coordinator.data = await coordinator._async_update_data()
    coordinator.async_request_refresh = AsyncMock()
    mock_backend.follow_brew = AsyncMock(return_value={"ack": "@tp", "frames": []})
    # Advance past the last poll: any spurious renewal inside run_brew would
    # move the timestamp and fail the assertion below.
    clock.return_value = SECOND_READ

    await coordinator.run_brew("mock-recipe")

    assert coordinator.last_successful_update == FIRST_READ.isoformat()
    assert coordinator.last_poll_success is True
    assert coordinator._follow_brew_active is False
