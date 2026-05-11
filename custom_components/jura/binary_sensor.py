"""Binary-sensor platform: one entity per well-known machine alert."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALERT_BINARY_SENSORS, DOMAIN
from .coordinator import JuraCoordinator
from .entity import JuraEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JuraCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        AlertBinarySensor(coordinator, config_entry, alert, device_class)
        for alert, device_class in ALERT_BINARY_SENSORS.items()
    )


class AlertBinarySensor(JuraEntity, BinarySensorEntity):
    """``True`` while the corresponding alert bit is set in @TF: status frames."""

    def __init__(
        self,
        coordinator: JuraCoordinator,
        config_entry: ConfigEntry,
        alert: str,
        device_class: str | None,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._alert = alert
        self._attr_name = alert.replace("_", " ").capitalize()
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_alert_{alert}"
        if device_class is not None:
            try:
                self._attr_device_class = BinarySensorDeviceClass(device_class)
            except ValueError:
                self._attr_device_class = None

    @property
    def is_on(self) -> bool | None:
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return self._alert in snapshot.active_alerts
