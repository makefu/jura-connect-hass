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


def _real_brew_coordinator(ready_snapshot, error: BaseException | None) -> JuraCoordinator:
    backend = AsyncMock()
    backend.follow_brew = AsyncMock(side_effect=error, return_value={"ack": "@tp", "frames": []})
    coordinator = JuraCoordinator(AsyncMock(), _entry(), backend=backend)
    coordinator.async_request_refresh = AsyncMock()
    coordinator.data = ready_snapshot
    return coordinator


# --- brew button availability ----------------------------------------------


def test_brew_button_available_while_online(ready_snapshot):
    coordinator = _coordinator()
    coordinator.data = ready_snapshot
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is True


def test_brew_button_unavailable_while_offline_even_when_retaining_values(ready_snapshot):
    coordinator = _coordinator()
    coordinator.data = dataclasses.replace(ready_snapshot, handshake_state=HANDSHAKE_STATE_OFFLINE)
    button = JuraBrewButton(coordinator, coordinator.config_entry)
    assert button._retain_when_offline() is True  # the default
    assert button.available is False


def test_brew_button_unavailable_after_failed_update(ready_snapshot):
    coordinator = _coordinator()
    coordinator.data = ready_snapshot
    coordinator.last_update_success = False
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is False


# --- run_brew failure handling ----------------------------------------------


async def test_unreachable_machine_raises_translated_error_and_goes_offline_now(ready_snapshot):
    coordinator = _real_brew_coordinator(ready_snapshot, _unreachable())
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
    assert coordinator.data.counters == ready_snapshot.counters


async def test_other_brew_failure_is_translated_and_keeps_machine_online(ready_snapshot):
    coordinator = _real_brew_coordinator(ready_snapshot, JuraBackendError("machine refused brew: @tp:00"))
    with pytest.raises(HomeAssistantError) as exc:
        await coordinator.run_brew("02000809000002000100000000000000")
    assert exc.value.translation_key == "brew_failed"
    assert exc.value.translation_placeholders == {"error": "machine refused brew: @tp:00"}
    assert coordinator.data.handshake_state != HANDSHAKE_STATE_OFFLINE


async def test_brew_auth_failure_triggers_reauth(ready_snapshot):
    coordinator = _real_brew_coordinator(ready_snapshot, JuraAuthError("handshake state REJECTED"))
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.run_brew("02000809000002000100000000000000")


async def test_successful_brew_unchanged(ready_snapshot):
    coordinator = _real_brew_coordinator(ready_snapshot, None)
    result = await coordinator.run_brew("02000809000002000100000000000000")
    assert result == {"ack": "@tp", "frames": []}
    coordinator.async_request_refresh.assert_awaited_once()


async def test_offline_snapshot_still_attempts_the_brew(ready_snapshot):
    """The service path probes by attempting: a machine that woke since the
    last poll must be able to brew without waiting for the next poll."""
    coordinator = _real_brew_coordinator(
        dataclasses.replace(ready_snapshot, handshake_state=HANDSHAKE_STATE_OFFLINE), None
    )
    result = await coordinator.run_brew("02000809000002000100000000000000", product="espresso")
    assert result == {"ack": "@tp", "frames": []}
    coordinator.backend.follow_brew.assert_awaited_once()


async def test_offline_snapshot_brew_failure_marks_offline_immediately(ready_snapshot):
    """The genuinely-off case: the attempt pays once, reports the machine
    unreachable, and the snapshot flips without waiting for the polls."""
    offline = dataclasses.replace(ready_snapshot, handshake_state=HANDSHAKE_STATE_OFFLINE)
    coordinator = _real_brew_coordinator(offline, _unreachable())
    with pytest.raises(HomeAssistantError) as exc:
        await coordinator.run_brew("02000809000002000100000000000000")
    assert exc.value.translation_key == "machine_unreachable"
    assert coordinator.data.handshake_state == HANDSHAKE_STATE_OFFLINE


@pytest.mark.parametrize("alert", ["close_powder_cover", "fill_powder", "close_tab", "close_front_cover"])
def test_profile_block_alerts_stop_brewing(sample_snapshot, alert):
    """Profile Type=\"block\" alerts stop every product; they must gate the
    button like the other critical alerts, or the machine ACKs and never
    dispenses."""
    coordinator = _coordinator()
    coordinator.data = _with_alerts(sample_snapshot, alert)
    assert coordinator.brew_blocker() == alert
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is False


# --- not ready / already brewing (machine heating up after power-on) ---------
# Regression: the Brew button was enabled while the machine heated up. The
# machine ACKed the recipe and never brewed; the integration followed that
# phantom brew for the whole follow window holding the only dongle session,
# and every further press queued another one: status stuck on heating_up,
# power-off unnoticed until the integration was reloaded.


def _with_alerts(snapshot, *alerts):
    return dataclasses.replace(snapshot, active_alerts=tuple(alerts))


def test_brew_button_unavailable_while_heating_up(sample_snapshot):
    coordinator = _coordinator()
    coordinator.data = _with_alerts(sample_snapshot, "heating_up")
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is False


@pytest.mark.parametrize("alert", ["fill_water", "empty_grounds", "coffee_rinsing", "please_wait"])
def test_brew_button_unavailable_while_machine_waits(sample_snapshot, alert):
    coordinator = _coordinator()
    coordinator.data = _with_alerts(sample_snapshot, alert)
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is False


@pytest.mark.parametrize("alert", ["descale_alert", "filter_alert", "cleaning_alert", "no_beans", "energy_safe"])
def test_reminders_and_hints_do_not_block_brewing(sample_snapshot, alert):
    coordinator = _coordinator()
    coordinator.data = _with_alerts(sample_snapshot, alert)
    assert coordinator.brew_blocker() is None
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is True


def test_blocker_reports_the_highest_priority_alert(sample_snapshot):
    coordinator = _coordinator()
    # descale_alert outranks heating_up on the status sensor, but only
    # heating_up blocks: it must still be found.
    coordinator.data = _with_alerts(sample_snapshot, "descale_alert", "heating_up")
    assert coordinator.brew_blocker() == "heating_up"
    coordinator.data = _with_alerts(sample_snapshot, "heating_up", "fill_water")
    assert coordinator.brew_blocker() == "fill_water"


async def test_brew_refused_while_heating_up_without_touching_the_machine(sample_snapshot):
    coordinator = _real_brew_coordinator(_with_alerts(sample_snapshot, "heating_up"), None)
    with pytest.raises(HomeAssistantError) as exc:
        await coordinator.run_brew("02000809000002000100000000000000")
    assert exc.value.translation_key == "machine_not_ready"
    assert exc.value.translation_placeholders == {"state": "heating up"}
    coordinator.backend.follow_brew.assert_not_awaited()
    assert not coordinator._session_lock.locked()
    assert coordinator.brew_pending is False


async def test_second_brew_is_refused_not_queued(ready_snapshot):
    import asyncio

    coordinator = _real_brew_coordinator(ready_snapshot, None)
    release = asyncio.Event()

    async def slow_brew(*args, **kwargs):
        await release.wait()
        return {"ack": "@tp", "frames": []}

    coordinator.backend.follow_brew = AsyncMock(side_effect=slow_brew)
    first = asyncio.ensure_future(coordinator.run_brew("02000809000002000100000000000000"))
    await asyncio.sleep(0)
    assert coordinator.brew_pending is True
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is False

    with pytest.raises(HomeAssistantError) as exc:
        await coordinator.run_brew("02000809000002000100000000000000")
    assert exc.value.translation_key == "brew_in_progress"

    release.set()
    await first
    assert coordinator.brew_pending is False
    assert coordinator.backend.follow_brew.await_count == 1


async def test_brew_not_started_is_reported_and_frees_everything(ready_snapshot):
    from custom_components.jura.backends.base import JuraBrewNotStartedError

    coordinator = _real_brew_coordinator(ready_snapshot, JuraBrewNotStartedError("no brew progress within 30s"))
    with pytest.raises(HomeAssistantError) as exc:
        await coordinator.run_brew("02000809000002000100000000000000")
    assert exc.value.translation_key == "brew_not_started"
    assert coordinator.brew_pending is False
    assert not coordinator._session_lock.locked()
    coordinator.async_request_refresh.assert_awaited()  # status catches up
    # Not an outage: the machine answered, it just did not brew.
    assert coordinator.data.handshake_state != HANDSHAKE_STATE_OFFLINE


async def test_brew_passes_the_start_timeout(ready_snapshot):
    from custom_components.jura.const import BREW_START_TIMEOUT

    coordinator = _real_brew_coordinator(ready_snapshot, None)
    await coordinator.run_brew("02000809000002000100000000000000", follow_seconds=60)
    coordinator.backend.follow_brew.assert_awaited_once_with(
        "02000809000002000100000000000000", follow_seconds=60, start_timeout=BREW_START_TIMEOUT
    )


async def test_polls_faster_while_heating_up(sample_snapshot, ready_snapshot):
    from datetime import timedelta

    from custom_components.jura.const import BUSY_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL

    coordinator = _real_brew_coordinator(ready_snapshot, None)
    coordinator.backend.fetch = AsyncMock(return_value=_with_alerts(sample_snapshot, "heating_up"))
    await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(seconds=BUSY_SCAN_INTERVAL)

    coordinator.backend.fetch = AsyncMock(return_value=ready_snapshot)
    await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(seconds=DEFAULT_SCAN_INTERVAL)


# --- switching off / per-product blocking --------------------------------------
# Regression: after switching the machine off it reported
# switch_off_delay_active for minutes. The HA Brew button (whose staged
# product the machine reported as blocked) went unavailable, but jura.brew
# -- used by the Coffee Button cards -- checked neither that status nor the
# per-product blocking, sent the recipe, and only failed 30 s later.


def test_switch_off_delay_blocks_brewing(ready_snapshot):
    coordinator = _coordinator()
    coordinator.data = _with_alerts(ready_snapshot, "switch_off_delay_active")
    assert coordinator.brew_blocker() == "switch_off_delay_active"
    assert JuraBrewButton(coordinator, coordinator.config_entry).available is False


async def test_brew_refused_while_switching_off(ready_snapshot):
    coordinator = _real_brew_coordinator(_with_alerts(ready_snapshot, "switch_off_delay_active"), None)
    with pytest.raises(HomeAssistantError) as exc:
        await coordinator.run_brew("02000809000002000100000000000000", product="espresso")
    assert exc.value.translation_key == "machine_not_ready"
    assert exc.value.translation_placeholders == {"state": "switch off delay active"}
    coordinator.backend.follow_brew.assert_not_awaited()


def _milk_blocked(snapshot):
    return dataclasses.replace(
        snapshot,
        active_alerts=("coffee_ready", "cappu_clean_alert"),
        blocked_products=("cappuccino", "latte_macchiato"),
        blocking_alerts=("cappu_clean_alert",),
    )


def test_blocker_is_per_product(ready_snapshot):
    coordinator = _coordinator()
    coordinator.data = _milk_blocked(ready_snapshot)
    assert coordinator.brew_blocker() is None  # machine-wide: fine
    assert coordinator.brew_blocker("espresso") is None
    assert coordinator.brew_blocker("cappuccino") == "cappu_clean_alert"
    assert coordinator.brew_blocker("CAPPUCCINO") == "cappu_clean_alert"


def test_blocker_without_reported_reason(ready_snapshot):
    coordinator = _coordinator()
    coordinator.data = dataclasses.replace(ready_snapshot, blocked_products=("espresso",))
    assert coordinator.brew_blocker("espresso") == "product_blocked"


async def test_service_path_refuses_a_blocked_product_without_sending_it(ready_snapshot):
    coordinator = _real_brew_coordinator(_milk_blocked(ready_snapshot), None)
    with pytest.raises(HomeAssistantError) as exc:
        await coordinator.run_brew("04000809000002000100000000000000", product="cappuccino")
    assert exc.value.translation_key == "machine_not_ready"
    coordinator.backend.follow_brew.assert_not_awaited()

    # Another drink is still fine.
    await coordinator.run_brew("02000809000002000100000000000000", product="espresso")
    coordinator.backend.follow_brew.assert_awaited_once()


def test_status_sensor_reports_brew_readiness(ready_snapshot):
    from custom_components.jura.sensor import StateSensor

    coordinator = _coordinator()
    sensor = StateSensor(coordinator, coordinator.config_entry)

    coordinator.data = ready_snapshot
    attrs = sensor.extra_state_attributes
    assert attrs["brew_ready"] is True
    assert attrs["brew_blocker"] is None
    assert attrs["brewing"] is False

    coordinator.data = _with_alerts(ready_snapshot, "switch_off_delay_active")
    assert sensor.native_value == "switch_off_delay_active"
    attrs = sensor.extra_state_attributes
    assert attrs["brew_ready"] is False
    assert attrs["brew_blocker"] == "switch_off_delay_active"

    coordinator.data = _milk_blocked(ready_snapshot)
    attrs = sensor.extra_state_attributes
    assert attrs["brew_ready"] is True  # machine-wide
    assert attrs["blocked_products"] == ["cappuccino", "latte_macchiato"]
    assert attrs["blocking_alerts"] == ["cappu_clean_alert"]

    coordinator.data = dataclasses.replace(ready_snapshot, handshake_state=HANDSHAKE_STATE_OFFLINE)
    attrs = sensor.extra_state_attributes
    assert attrs["brew_ready"] is False
    # A retained OFFLINE snapshot must not claim the machine still blocks
    # those drinks -- it is stale data.
    assert attrs["blocked_products"] == []
    assert attrs["blocking_alerts"] == []

    coordinator.data = ready_snapshot
    coordinator.brew_pending = True
    attrs = sensor.extra_state_attributes
    assert attrs["brew_ready"] is False
    assert attrs["brewing"] is True


def test_status_prefers_switch_off_delay_over_ready(ready_snapshot):
    from custom_components.jura.sensor import StateSensor

    coordinator = _coordinator()
    coordinator.data = _with_alerts(ready_snapshot, "coffee_ready", "switch_off_delay_active")
    assert StateSensor(coordinator, coordinator.config_entry).native_value == "switch_off_delay_active"
