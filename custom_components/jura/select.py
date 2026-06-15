"""Select platform: one dropdown per pick-from-N machine setting.

Covers SettingDef kinds ``switch``, ``combobox`` and ``item_slider`` —
all of which present a fixed list of named choices on the machine.
``step_slider`` settings (hardness) are handled by the ``number``
platform instead.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MACHINE_TYPE, DOMAIN
from .coordinator import JuraCoordinator
from .entity import JuraEntity

_LOGGER = logging.getLogger(__name__)

SELECT_KINDS = {"switch", "combobox", "item_slider"}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JuraCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    machine_type = config_entry.data.get(CONF_MACHINE_TYPE)
    if not machine_type:
        return

    # Load the profile to enumerate settings. Failing to load is a
    # configuration / version-skew error — log and bail rather than
    # crashing the integration.
    try:
        from jura_connect import load_profile
    except ImportError:  # pragma: no cover
        return
    try:
        # load_profile reads bundled XML off disk — keep it off the loop.
        profile = await hass.async_add_executor_job(load_profile, machine_type)
    except KeyError:
        _LOGGER.warning("no profile for machine_type %s; skipping select setup", machine_type)
        return

    entities: list[SelectEntity] = []
    for setting in profile.settings:
        if setting.kind not in SELECT_KINDS:
            continue
        if not setting.items:
            continue
        entities.append(SettingSelectEntity(coordinator, config_entry, setting))
    async_add_entities(entities)


class SettingSelectEntity(JuraEntity, SelectEntity):
    """Drives one combobox / switch / item_slider machine setting.

    Reads the current value out of ``coordinator.data.settings``;
    writes go through ``coordinator.write_setting`` which validates via
    the profile before talking to the machine.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry, setting_def) -> None:
        super().__init__(coordinator, config_entry)
        self._setting = setting_def
        self._attr_name = f"Setting {setting_def.name.replace('_', ' ')}"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_setting_{setting_def.name}"
        self._attr_options = [item.name for item in setting_def.items]

    @property
    def current_option(self) -> str | None:
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        raw = snapshot.settings.get(self._setting.name)
        if not raw:
            return None
        # SettingDef.item_from_hex (jura_connect 0.9.4+) handles both
        # the exact-match case and the AutoOFF-style stripped-suffix
        # read-back (writing `211E` for 30min reads back as `1E`).
        item = self._setting.item_from_hex(raw)
        return item.name if item is not None else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.write_setting(self._setting.name, option)
