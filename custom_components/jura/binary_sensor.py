"""Binary-sensor platform: one entity per well-known machine alert."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALERT_BINARY_SENSORS, DIAGNOSTIC_ALERT_DEVICE_CLASSES, DOMAIN
from .coordinator import HANDSHAKE_STATE_OFFLINE, JuraCoordinator
from .entity import JuraEntity
from .i18n import get_translator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JuraCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[BinarySensorEntity] = [ConnectivityBinarySensor(coordinator, config_entry)]
    entities.extend(
        AlertBinarySensor(coordinator, config_entry, alert, device_class)
        for alert, device_class in ALERT_BINARY_SENSORS.items()
    )
    async_add_entities(entities)


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
        translator = get_translator()
        # "Alert" prefix groups every alert binary_sensor together on the
        # device card. Sort order within the group is then alphabetical
        # by alert name.
        self._attr_name = translator.format_entity_name("Alert {alert}", alert=alert)
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_alert_{alert}"
        if device_class is not None:
            try:
                self._attr_device_class = BinarySensorDeviceClass(device_class)
            except ValueError:
                self._attr_device_class = None
        # Push everything that isn't a "problem" (running/info/unknown)
        # into DIAGNOSTIC so the main device card surfaces only entries
        # that ask the user to do something.
        if device_class in DIAGNOSTIC_ALERT_DEVICE_CLASSES:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return self._alert in snapshot.active_alerts


class ConnectivityBinarySensor(JuraEntity, BinarySensorEntity):
    """Tracks whether the last coordinator poll succeeded.

    Unlike alert entities this one is *always* available — it's the
    canonical signal automations should consult to decide whether the
    machine is reachable right now. Categorised as DIAGNOSTIC: it
    belongs next to the machine-type entity, not in the "needs
    attention" main section.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        translator = get_translator()
        self._attr_name = translator.get_entity_name("binary_sensor", "connectivity", "Connectivity")
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_connectivity"

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        snapshot = self.coordinator.data
        if snapshot is not None and snapshot.handshake_state == HANDSHAKE_STATE_OFFLINE:
            return False
        return True
