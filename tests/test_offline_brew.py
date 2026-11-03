"""Brewing never depends on luck: offline machines are reported, not tried blindly.

* The HA brew button is unavailable while the machine is offline, even with
  "retain last values when offline" (which keeps *readings* visible).
* A brew that finds the machine unreachable fails with a translated
  ``machine_unreachable`` error and flips the machine to OFFLINE at once,
  without waiting for the poll tolerance.
* Other brew failures are translated too (``brew_failed``).
"""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("jura_connect")

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from custom_components.jura.backends.base import JuraAuthError, JuraBackendError
from custom_components.jura.binary_sensor import ConnectivityBinarySensor
from custom_components.jura.button import JuraBrewButton
from custom_components.jura.const import DOMAIN
from custom_components.jura.coordinator import HANDSHAKE_STATE_OFFLINE, JuraCoordinator
from tests.test_brew_platform import _coordinator, _entry


def _unreachable() -> JuraBackendError:
    try:
        raise TimeoutError("timed out")
    except TimeoutError as cause:
        try:
            raise JuraBackendError("I/O error talking to machine: timed out") from cause
        except JuraBackendError as err:
            return err


def _real_brew_coordinator(sample_snapshot, error: BaseException | None) -> JuraCoordinator:
    backend = AsyncMock()
    backend.follow_brew = AsyncMock(side_effect=error, return_value={"ack": "@tp", "frames": []})
    coordinator = JuraCoordinator(AsyncMock(), _entry(), backend=backend)
    coordinator.async_request_refresh = AsyncMock()
    coordinator.data = sample_snapshot
    return coordinator


# --- brew button availability ----------------------------------------------


def test_brew_button_available_while_online(sample_snapshot):
    coordinator = _coordinator()
    coordinator.data = sample_snapshot
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is True


def test_brew_button_unavailable_while_offline_even_when_retaining_values(sample_snapshot):
    coordinator = _coordinator()
    coordinator.data = dataclasses.replace(sample_snapshot, handshake_state=HANDSHAKE_STATE_OFFLINE)
    button = JuraBrewButton(coordinator, coordinator.config_entry)
    assert button._retain_when_offline() is True  # the default
    assert button.available is False


def test_brew_button_unavailable_after_failed_update(sample_snapshot):
    coordinator = _coordinator()
    coordinator.data = sample_snapshot
    coordinator.last_update_success = False
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is False


# --- run_brew failure handling ----------------------------------------------


async def test_unreachable_machine_raises_translated_error_and_goes_offline_now(sample_snapshot):
    coordinator = _real_brew_coordinator(sample_snapshot, _unreachable())
    connectivity = ConnectivityBinarySensor(coordinator, coordinator.config_entry)
    assert connectivity.is_on is True

    with pytest.raises(HomeAssistantError) as exc:
        await coordinator.run_brew("02000809000002000100000000000000")
    assert exc.value.translation_domain == DOMAIN
    assert exc.value.translation_key == "machine_unreachable"

    # No waiting for OFFLINE_TOLERANCE polls: connectivity + button follow now.
    assert coordinator.data.handshake_state == HANDSHAKE_STATE_OFFLINE
    assert connectivity.is_on is False
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is False
    # Retained readings survive (the offline snapshot reuses the last one).
    assert coordinator.data.counters == sample_snapshot.counters


async def test_other_brew_failure_is_translated_and_keeps_machine_online(sample_snapshot):
    coordinator = _real_brew_coordinator(sample_snapshot, JuraBackendError("machine refused brew: @tp:00"))
    with pytest.raises(HomeAssistantError) as exc:
        await coordinator.run_brew("02000809000002000100000000000000")
    assert exc.value.translation_key == "brew_failed"
    assert exc.value.translation_placeholders == {"error": "machine refused brew: @tp:00"}
    assert coordinator.data.handshake_state != HANDSHAKE_STATE_OFFLINE


async def test_brew_auth_failure_triggers_reauth(sample_snapshot):
    coordinator = _real_brew_coordinator(sample_snapshot, JuraAuthError("handshake state REJECTED"))
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.run_brew("02000809000002000100000000000000")


async def test_successful_brew_unchanged(sample_snapshot):
    coordinator = _real_brew_coordinator(sample_snapshot, None)
    result = await coordinator.run_brew("02000809000002000100000000000000")
    assert result == {"ack": "@tp", "frames": []}
    coordinator.async_request_refresh.assert_awaited_once()
