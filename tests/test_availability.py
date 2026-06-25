"""Availability behaviour: entities keep last values across transient failures.

The coffee machine is regularly offline (powered down, dongle asleep). We
want HA to keep showing the last-known counters/percents/state instead of
flipping every entity to ``unavailable`` on every failed poll. The
``binary_sensor.connectivity`` entity is the single source of truth for
"is the machine reachable right now".
"""

from __future__ import annotations

from unittest.mock import MagicMock

import dataclasses

from custom_components.jura.binary_sensor import AlertBinarySensor, ConnectivityBinarySensor
from custom_components.jura.coordinator import HANDSHAKE_STATE_OFFLINE
from custom_components.jura.sensor import CounterSensor, PercentSensor, StateSensor


def _coordinator(*, data, last_update_success: bool = True) -> MagicMock:
    c = MagicMock()
    c.data = data
    c.last_update_success = last_update_success
    return c


def test_sensors_stay_available_when_poll_fails(sample_snapshot, fake_config_entry):
    """A failed poll must NOT mark sensors unavailable as long as we have a snapshot."""
    coordinator = _coordinator(data=sample_snapshot, last_update_success=False)

    state = StateSensor(coordinator, fake_config_entry)
    counter = CounterSensor(coordinator, fake_config_entry, "cleaning")
    percent = PercentSensor(coordinator, fake_config_entry, "cleaning")

    assert state.available is True
    assert counter.available is True
    assert percent.available is True

    # And critically — they still return the last-known value.
    assert state.native_value == "heating_up"
    assert counter.native_value == 21
    assert percent.native_value == 80


def test_alert_binary_sensors_stay_available_when_poll_fails(sample_snapshot, fake_config_entry):
    coordinator = _coordinator(data=sample_snapshot, last_update_success=False)
    sensor = AlertBinarySensor(coordinator, fake_config_entry, "heating_up", "running")
    assert sensor.available is True
    assert sensor.is_on is True


def test_entities_unavailable_only_before_first_successful_update(fake_config_entry):
    """If we never got data, entities are correctly unavailable (no value to show)."""
    coordinator = _coordinator(data=None, last_update_success=False)

    state = StateSensor(coordinator, fake_config_entry)
    counter = CounterSensor(coordinator, fake_config_entry, "cleaning")
    alert = AlertBinarySensor(coordinator, fake_config_entry, "fill_water", "problem")

    assert state.available is False
    assert counter.available is False
    assert alert.available is False


def test_entities_available_when_data_present_and_poll_succeeded(sample_snapshot, fake_config_entry):
    coordinator = _coordinator(data=sample_snapshot, last_update_success=True)
    sensor = StateSensor(coordinator, fake_config_entry)
    assert sensor.available is True


def test_connectivity_sensor_tracks_last_update_success(sample_snapshot, fake_config_entry):
    """The connectivity binary_sensor is the single source of truth for reachability."""
    online = _coordinator(data=sample_snapshot, last_update_success=True)
    offline = _coordinator(data=sample_snapshot, last_update_success=False)

    assert ConnectivityBinarySensor(online, fake_config_entry).is_on is True
    assert ConnectivityBinarySensor(offline, fake_config_entry).is_on is False


def test_connectivity_sensor_is_always_available(fake_config_entry, sample_snapshot):
    """Connectivity must report itself even when the rest of the integration is stale."""
    for data in (None, sample_snapshot):
        for success in (True, False):
            coordinator = _coordinator(data=data, last_update_success=success)
            sensor = ConnectivityBinarySensor(coordinator, fake_config_entry)
            assert sensor.available is True, f"data={data!r} success={success}"


def test_connectivity_sensor_device_class_is_connectivity(fake_config_entry, sample_snapshot):
    sensor = ConnectivityBinarySensor(_coordinator(data=sample_snapshot), fake_config_entry)
    assert sensor.device_class == "connectivity"


def test_connectivity_sensor_reports_off_when_snapshot_handshake_offline(sample_snapshot, fake_config_entry):
    """When the coordinator surfaces an OFFLINE snapshot, connectivity must be off.

    The poll succeeded (last_update_success=True) — but the *machine* is
    unreachable. Connectivity is the canonical signal for reachability,
    so it must key off handshake_state too.
    """
    offline_snapshot = dataclasses.replace(sample_snapshot, handshake_state=HANDSHAKE_STATE_OFFLINE)
    coordinator = _coordinator(data=offline_snapshot, last_update_success=True)
    assert ConnectivityBinarySensor(coordinator, fake_config_entry).is_on is False


def test_connectivity_sensor_unique_id_distinct_from_alerts(fake_config_entry, sample_snapshot):
    sensor = ConnectivityBinarySensor(_coordinator(data=sample_snapshot), fake_config_entry)
    assert sensor.unique_id.endswith("connectivity")
    assert "alert_" not in sensor.unique_id
