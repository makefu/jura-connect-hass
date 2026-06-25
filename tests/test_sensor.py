"""Sensor platform tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import MagicMock

from custom_components.jura import sensor as jura_sensor
from custom_components.jura.const import COUNTER_KEYS, DOMAIN, PERCENT_KEYS, STATE_IDLE
from custom_components.jura.sensor import (
    BrewCounterSensor,
    BrewTotalSensor,
    CounterSensor,
    MachineTypeSensor,
    PercentSensor,
    StateSensor,
)
from homeassistant.const import EntityCategory


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


# ---------------------------------------------------------------------------
# Naming + entity_category (device-card grouping)
# ---------------------------------------------------------------------------


def test_state_sensor_uses_status_translation_key(fake_config_entry, sample_snapshot):
    s = StateSensor(_make_coordinator(sample_snapshot), fake_config_entry)
    # Names are resolved by HA from translation strings; the entity only
    # carries has_entity_name + the translation key.
    assert s._attr_has_entity_name is True
    assert s._attr_translation_key == "status"
    assert s.entity_category is None


def test_counter_sensor_is_diagnostic_with_per_key_translation(sample_snapshot, fake_config_entry):
    s = CounterSensor(_make_coordinator(sample_snapshot), fake_config_entry, "cleaning")
    assert s.entity_category == EntityCategory.DIAGNOSTIC
    assert s._attr_translation_key == "counter_cleaning"


def test_percent_sensor_is_default_category_with_per_key_translation(sample_snapshot, fake_config_entry):
    s = PercentSensor(_make_coordinator(sample_snapshot), fake_config_entry, "decalc")
    assert s.entity_category is None
    assert s._attr_translation_key == "percent_decalc"


def test_brew_counter_uses_placeholder_for_product(sample_snapshot, fake_config_entry):
    s = BrewCounterSensor(_make_coordinator(sample_snapshot), fake_config_entry, "double_espresso")
    assert s.entity_category is None
    assert s._attr_translation_key == "brew_counter"
    # Underscores in the recipe code become spaces in the placeholder value.
    assert s._attr_translation_placeholders == {"product": "double espresso"}


def test_brew_total_uses_brew_total_translation_key(sample_snapshot, fake_config_entry):
    s = BrewTotalSensor(_make_coordinator(sample_snapshot), fake_config_entry)
    assert s.entity_category is None
    assert s._attr_translation_key == "brew_total"


def test_machine_type_is_diagnostic(sample_snapshot, fake_config_entry):
    s = MachineTypeSensor(_make_coordinator(sample_snapshot), fake_config_entry)
    assert s.entity_category == EntityCategory.DIAGNOSTIC


# ---------------------------------------------------------------------------
# Brew counters
# ---------------------------------------------------------------------------


def test_brew_counter_reports_per_product_count(sample_snapshot, fake_config_entry):
    coordinator = _make_coordinator(sample_snapshot)
    espresso = BrewCounterSensor(coordinator, fake_config_entry, "espresso")
    coffee = BrewCounterSensor(coordinator, fake_config_entry, "coffee")
    assert espresso.native_value == 412
    assert coffee.native_value == 287
    assert espresso.unique_id.endswith("brews_espresso")
    assert espresso.native_unit_of_measurement == "brews"


def test_brew_counter_none_for_unknown_product(sample_snapshot, fake_config_entry):
    coordinator = _make_coordinator(sample_snapshot)
    s = BrewCounterSensor(coordinator, fake_config_entry, "ristretto")
    assert s.native_value is None


def test_brew_counter_none_without_data(fake_config_entry):
    s = BrewCounterSensor(_make_coordinator(None), fake_config_entry, "espresso")
    assert s.native_value is None


def test_brew_total_sensor_reports_total(sample_snapshot, fake_config_entry):
    s = BrewTotalSensor(_make_coordinator(sample_snapshot), fake_config_entry)
    assert s.native_value == 809
    assert s.unique_id.endswith("brews_total")


def test_brew_total_zero_on_machines_without_statistics(empty_snapshot, fake_config_entry):
    s = BrewTotalSensor(_make_coordinator(empty_snapshot), fake_config_entry)
    assert s.native_value == 0


def test_setup_entry_spawns_brew_sensors_when_machine_comes_online(fake_config_entry, empty_snapshot, sample_snapshot):
    """Regression: when first poll returns an offline/empty snapshot,
    BrewCounterSensor entities must still be created later, once the
    machine wakes up and product names start arriving."""
    coordinator = _make_coordinator(empty_snapshot)
    listeners: list = []
    coordinator.async_add_listener = lambda cb, context=None: listeners.append(cb) or (lambda: listeners.remove(cb))

    hass = MagicMock()
    hass.data = {DOMAIN: {fake_config_entry.entry_id: coordinator}}

    added: list = []

    def _async_add_entities(it):
        added.extend(it)

    asyncio.run(jura_sensor.async_setup_entry(hass, fake_config_entry, _async_add_entities))

    assert not [e for e in added if isinstance(e, BrewCounterSensor)]

    coordinator.data = sample_snapshot
    for cb in listeners:
        cb()

    products = {e._product for e in added if isinstance(e, BrewCounterSensor)}
    assert products == set(sample_snapshot.brews)

    # Subsequent identical updates don't re-add the same sensors.
    added_before = len(added)
    for cb in listeners:
        cb()
    assert len(added) == added_before


# ---------------------------------------------------------------------------
# Machine type
# ---------------------------------------------------------------------------


def test_machine_type_sensor_shows_friendly_name(sample_snapshot, fake_config_entry):
    s = MachineTypeSensor(_make_coordinator(sample_snapshot), fake_config_entry)
    assert s.native_value == "S8 (EB)"
    attrs = s.extra_state_attributes
    assert attrs["machine_type"] == "EF1091"
    assert attrs["machine_type_name"] == "S8 (EB)"


def test_machine_type_sensor_falls_back_to_code_without_friendly(sample_snapshot, fake_config_entry):
    snap = replace(sample_snapshot, machine_type_name=None)
    s = MachineTypeSensor(_make_coordinator(snap), fake_config_entry)
    assert s.native_value == "EF1091"


def test_machine_type_sensor_shows_unconfigured_when_unset(empty_snapshot, fake_config_entry):
    s = MachineTypeSensor(_make_coordinator(empty_snapshot), fake_config_entry)
    assert s.native_value == "unconfigured"


def test_machine_type_sensor_none_without_data(fake_config_entry):
    s = MachineTypeSensor(_make_coordinator(None), fake_config_entry)
    assert s.native_value is None
    assert s.extra_state_attributes == {}
