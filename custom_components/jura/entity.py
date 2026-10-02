"""Shared entity base for the Jura integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CONN_ID, CONF_HOST, DOMAIN
from .coordinator import HANDSHAKE_STATE_OFFLINE, JuraCoordinator


class JuraEntity(CoordinatorEntity[JuraCoordinator]):
    """Common base: groups all entities for one machine under one device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JuraCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        host = config_entry.data[CONF_HOST]
        conn_id = config_entry.data[CONF_CONN_ID]
        self._slug = conn_id.replace("-", "_").lower()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=f"Jura {host}",
            manufacturer="JURA",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        # Keep the last-known snapshot internally (counters resume at the same
        # value when the machine wakes), but surface "unavailable" once the
        # coordinator has confirmed an outage — otherwise total_increasing
        # counters look like they reset to 0 or freeze stale values as live.
        # ConnectivityBinarySensor overrides this: it is the reachability signal.
        snapshot = self.coordinator.data
        return snapshot is not None and snapshot.handshake_state != HANDSHAKE_STATE_OFFLINE
