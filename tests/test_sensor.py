"""Sensor platform tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.jura.const import COUNTER_KEYS, PERCENT_KEYS, STATE_IDLE
from custom_components.jura.sensor import CounterSensor, PercentSensor, StateSensor


def _make_coordinator(snapshot):
    coordinator = MagicMock()
    coordinator.data = snapshot
    return coordinator


def test_state_sensor_priority(sample_snapshot, fake_config_entry):
    sensor = StateSensor(_make_coordinator(sample_snapshot), fake_config_entry)
    # heating_up is the only active alert in sample_snapshot
    assert sensor.native_value == "heating_up"


def test_state_sensor_priority_picks_first_match(fake_config_entry, sample_snapshot):
    from dataclasses import replace

    multi = replace(sample_snapshot, active_alerts=("coffee_ready", "fill_water", "please_wait"))
    sensor = StateSensor(_make_coordinator(multi), fake_config_entry)
    # please_wait wins over fill_water wins over coffee_ready (per STATE_PRIORITY)
    assert sensor.native_value == "please_wait"


def test_state_sensor_idle_when_no_alerts(empty_snapshot, fake_config_entry):
    sensor = StateSensor(_make_coordinator(empty_snapshot), fake_config_entry)
    assert sensor.native_value == STATE_IDLE


def test_state_sensor_none_without_data(fake_config_entry):
    sensor = StateSensor(_make_coordinator(None), fake_config_entry)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_state_sensor_attributes_include_snapshot(sample_snapshot, fake_config_entry):
    sensor = StateSensor(_make_coordinator(sample_snapshot), fake_config_entry)
    attrs = sensor.extra_state_attributes
    assert attrs["active_alerts"] == ["heating_up"]
    assert attrs["counters"]["cleaning"] == 21
    assert attrs["percents"]["filter_change"] is None


def test_counter_sensors_created_for_each_key(sample_snapshot, fake_config_entry):
    coordinator = _make_coordinator(sample_snapshot)
    for key in COUNTER_KEYS:
        s = CounterSensor(coordinator, fake_config_entry, key)
        assert s.native_value == sample_snapshot.counters[key]
        assert s.unique_id.endswith(f"counter_{key}")


def test_counter_sensor_none_without_data(fake_config_entry):
    s = CounterSensor(_make_coordinator(None), fake_config_entry, "cleaning")
    assert s.native_value is None


def test_percent_sensor_translates_absent_to_none(sample_snapshot, fake_config_entry):
    coordinator = _make_coordinator(sample_snapshot)
    s_filter = PercentSensor(coordinator, fake_config_entry, "filter_change")
    assert s_filter.native_value is None  # raw 0xFF -> None
    s_clean = PercentSensor(coordinator, fake_config_entry, "cleaning")
    assert s_clean.native_value == 80


def test_percent_sensor_full_set(sample_snapshot, fake_config_entry):
    coordinator = _make_coordinator(sample_snapshot)
    for key in PERCENT_KEYS:
        s = PercentSensor(coordinator, fake_config_entry, key)
        assert s.unique_id.endswith(f"percent_{key}")
        assert s.native_unit_of_measurement == "%"
