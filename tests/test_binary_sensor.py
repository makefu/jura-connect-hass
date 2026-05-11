"""Binary-sensor platform tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.jura.binary_sensor import AlertBinarySensor, ConnectivityBinarySensor
from custom_components.jura.const import ALERT_BINARY_SENSORS
from homeassistant.const import EntityCategory


def _coordinator(snapshot):
    c = MagicMock()
    c.data = snapshot
    return c


def test_alert_is_on_when_in_active_alerts(sample_snapshot, fake_config_entry):
    sensor = AlertBinarySensor(_coordinator(sample_snapshot), fake_config_entry, "heating_up", "running")
    assert sensor.is_on is True


def test_alert_is_off_when_not_in_active_alerts(sample_snapshot, fake_config_entry):
    sensor = AlertBinarySensor(_coordinator(sample_snapshot), fake_config_entry, "fill_water", "problem")
    assert sensor.is_on is False


def test_alert_returns_none_without_data(fake_config_entry):
    sensor = AlertBinarySensor(_coordinator(None), fake_config_entry, "fill_water", "problem")
    assert sensor.is_on is None


def test_one_sensor_per_known_alert(sample_snapshot, fake_config_entry):
    # We don't drive HA's platform setup directly, but we can assert the
    # exposed map covers what we want and the entities can be constructed.
    for alert, device_class in ALERT_BINARY_SENSORS.items():
        sensor = AlertBinarySensor(_coordinator(sample_snapshot), fake_config_entry, alert, device_class)
        assert sensor.unique_id.endswith(f"alert_{alert}")


def test_problem_alerts_stay_in_default_category(sample_snapshot, fake_config_entry):
    sensor = AlertBinarySensor(_coordinator(sample_snapshot), fake_config_entry, "fill_water", "problem")
    assert sensor.entity_category is None


def test_running_alerts_are_diagnostic(sample_snapshot, fake_config_entry):
    sensor = AlertBinarySensor(_coordinator(sample_snapshot), fake_config_entry, "heating_up", "running")
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC


def test_alerts_without_device_class_are_diagnostic(sample_snapshot, fake_config_entry):
    # coffee_ready / welcome have device_class=None — informational, diagnostic.
    sensor = AlertBinarySensor(_coordinator(sample_snapshot), fake_config_entry, "coffee_ready", None)
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC


def test_alerts_share_a_common_prefix(sample_snapshot, fake_config_entry):
    sensor = AlertBinarySensor(_coordinator(sample_snapshot), fake_config_entry, "fill_water", "problem")
    assert sensor.name == "Alert fill water"


def test_connectivity_sensor_is_diagnostic(sample_snapshot, fake_config_entry):
    sensor = ConnectivityBinarySensor(_coordinator(sample_snapshot), fake_config_entry)
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
