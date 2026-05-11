"""Sensor platform: state + maintenance counters + maintenance percent."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .backends.base import MachineSnapshot
from .const import (
    COUNTER_KEYS,
    DOMAIN,
    PERCENT_KEYS,
    STATE_IDLE,
    STATE_PRIORITY,
)
from .coordinator import JuraCoordinator
from .entity import JuraEntity
from .serializers import percent_value, serialize_snapshot


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JuraCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[SensorEntity] = [StateSensor(coordinator, config_entry)]
    entities.extend(CounterSensor(coordinator, config_entry, key) for key in COUNTER_KEYS)
    entities.extend(PercentSensor(coordinator, config_entry, key) for key in PERCENT_KEYS)

    async_add_entities(entities)


def _snapshot(coordinator: JuraCoordinator) -> MachineSnapshot | None:
    return coordinator.data


def _derive_state(active_alerts: tuple[str, ...]) -> str:
    if not active_alerts:
        return STATE_IDLE
    alert_set = set(active_alerts)
    for name in STATE_PRIORITY:
        if name in alert_set:
            return name
    return next(iter(active_alerts))


class StateSensor(JuraEntity, SensorEntity):
    """Overall machine state derived from the active alert bits."""

    _attr_name = "State"
    _attr_icon = "mdi:coffee-maker"

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_state"

    @property
    def native_value(self) -> str | None:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return None
        return _derive_state(snapshot.active_alerts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return {}
        return serialize_snapshot(snapshot)


class CounterSensor(JuraEntity, SensorEntity):
    """One maintenance counter (cleaning / decalc / filter / cappu / coffee rinse)."""

    _attr_icon = "mdi:counter"
    _attr_state_class = "total_increasing"

    def __init__(
        self,
        coordinator: JuraCoordinator,
        config_entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._key = key
        self._attr_name = f"{key.replace('_', ' ').title()} counter"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_counter_{key}"

    @property
    def native_value(self) -> int | None:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return None
        return snapshot.counters.get(self._key)


class PercentSensor(JuraEntity, SensorEntity):
    """Maintenance percent indicator (0-100, or unavailable when sensor absent)."""

    _attr_icon = "mdi:percent-outline"
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self,
        coordinator: JuraCoordinator,
        config_entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._key = key
        self._attr_name = f"{key.replace('_', ' ').title()} percent"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_percent_{key}"

    @property
    def native_value(self) -> int | None:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return None
        raw = snapshot.percents.get(self._key)
        if raw is None:
            return None
        return percent_value(raw)
