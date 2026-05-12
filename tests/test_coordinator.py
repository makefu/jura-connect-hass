"""Coordinator tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.jura.backends.base import JuraAuthError, JuraBackendError
from custom_components.jura.coordinator import JuraCoordinator
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed


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
    mock_backend.fetch.side_effect = JuraBackendError("socket reset")
    coordinator = _make_coordinator(mock_backend, fake_config_entry)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


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
