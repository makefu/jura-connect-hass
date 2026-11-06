"""Switch-on time controls for the seven weekday timers."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import JuraCoordinator
from .entity import JuraEntity
from .timers import WEEKDAY_SLOTS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.timers_enabled:
        async_add_entities(WeekdayTimerTime(coordinator, entry, day) for day in WEEKDAY_SLOTS)


class WeekdayTimerTime(JuraEntity, TimeEntity):
    """Edit an active time or stage a time for a disabled weekday."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: JuraCoordinator, entry: ConfigEntry, day: str) -> None:
        super().__init__(coordinator, entry)
        self._slot = WEEKDAY_SLOTS[day]
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_weekday_timer_{day}_time"
        self._attr_translation_key = f"weekday_timer_{day}"
        self._update_value()

    @property
    def available(self) -> bool:
        return self.coordinator.weekday_timers_available

    def _update_value(self) -> None:
        snapshot = self.coordinator.data
        timer = snapshot.timers.get(self._slot) if snapshot is not None else None
        self._attr_native_value = None if timer is None else timer.on or self.coordinator.timer_times.get(self._slot)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_value()
        super()._handle_coordinator_update()

    async def async_set_value(self, value: time) -> None:
        await self.coordinator.update_weekday_timer(self._slot, "set_time", value)
