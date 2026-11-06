"""Enable or disable a weekday's JURA switch-on time."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
        async_add_entities(WeekdayTimerSwitch(coordinator, entry, day) for day in WEEKDAY_SLOTS)


class WeekdayTimerSwitch(JuraEntity, SwitchEntity):
    """Change only the switch-on pair, leaving the switch-off time alone."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: JuraCoordinator, entry: ConfigEntry, day: str) -> None:
        super().__init__(coordinator, entry)
        self._slot = WEEKDAY_SLOTS[day]
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_weekday_timer_{day}_enabled"
        self._attr_translation_key = f"weekday_timer_{day}"
        self._update_value()

    @property
    def available(self) -> bool:
        return self.coordinator.weekday_timers_available

    def _update_value(self) -> None:
        snapshot = self.coordinator.data
        timer = snapshot.timers.get(self._slot) if snapshot is not None else None
        self._attr_is_on = None if timer is None else timer.on is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_value()
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.update_weekday_timer(self._slot, "enable")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.update_weekday_timer(self._slot, "disable")
