"""Shared entity base for the Jura integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CONN_ID, CONF_HOST, CONF_RETAIN_WHEN_OFFLINE, DEFAULT_RETAIN_WHEN_OFFLINE, DOMAIN
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
        # Since 1.0.0 entities keep their last-known value through an
        # outage: the OFFLINE snapshot still renders (counters/percents
        # stay visible) and the connectivity binary_sensor is the sole
        # reachability signal. Automations that need the old gate can
        # template on it, or flip the hidden "Retain values when offline"
        # option back to false to restore per-entity unavailable.
        # ConnectivityBinarySensor overrides this: it is the reachability signal.
        if self._retain_when_offline():
            return self.coordinator.data is not None
        snapshot = self.coordinator.data
        return snapshot is not None and snapshot.handshake_state != HANDSHAKE_STATE_OFFLINE

    def _retain_when_offline(self) -> bool:
        # Options flow writes entry.options; the label-based lookup in the
        # coordinator keeps options authoritative. Fall back to data (then
        # the default) so entries that never saw the options flow behave
        # like the new default.
        options = getattr(self._config_entry, "options", None) or {}
        if CONF_RETAIN_WHEN_OFFLINE in options:
            return bool(options[CONF_RETAIN_WHEN_OFFLINE])
        data = self._config_entry.data
        return bool(data.get(CONF_RETAIN_WHEN_OFFLINE, DEFAULT_RETAIN_WHEN_OFFLINE))
