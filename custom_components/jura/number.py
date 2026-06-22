"""Number platform: one slider per step_slider machine setting.

Covers SettingDef kind ``step_slider`` (Hardness on EF1091, etc.) —
integer-valued range with min/max/step from the profile XML.
"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MACHINE_TYPE, DOMAIN
from .coordinator import JuraCoordinator
from .entity import JuraEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JuraCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    machine_type = config_entry.data.get(CONF_MACHINE_TYPE)
    if not machine_type:
        return

    try:
        from jura_connect import load_profile
    except ImportError:  # pragma: no cover
        return
    try:
        profile = load_profile(machine_type)
    except KeyError:
        _LOGGER.warning("no profile for machine_type %s; skipping number setup", machine_type)
        return

    entities: list[NumberEntity] = []
    for setting in profile.settings:
        if setting.kind == "step_slider":
            entities.append(SettingNumberEntity(coordinator, config_entry, setting))
    async_add_entities(entities)


class SettingNumberEntity(JuraEntity, NumberEntity):
    """Drives one step_slider machine setting (e.g. water hardness)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = "slider"

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry, setting_def) -> None:
        super().__init__(coordinator, config_entry)
        self._setting = setting_def
        self._attr_name = f"Setting {setting_def.name.replace('_', ' ')}"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_setting_{setting_def.name}"
        self._attr_native_min_value = float(setting_def.minimum or 0)
        self._attr_native_max_value = float(setting_def.maximum or 0xFF)
        self._attr_native_step = float(setting_def.step or 1)

    @property
    def native_value(self) -> float | None:
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        raw = snapshot.settings.get(self._setting.name)
        if not raw:
            return None
        try:
            return float(int(raw, 16))
        except ValueError:
            return None

    async def async_set_native_value(self, value: float) -> None:
        # ``JuraClient.set_setting`` (the underlying write path)
        # parses step-slider input as **hex**, not decimal — matching
        # the wire format. Width comes from the SettingDef's mask so
        # both 1- and 2-byte sliders encode correctly.
        n = int(value)
        width = len(self._setting.mask) if self._setting.mask else 2
        await self.coordinator.write_setting(self._setting.name, f"{n:0{width}X}")
