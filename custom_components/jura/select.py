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

from .brew import ProductArg, ProductDef, jura_connect_xml_dir, load_definition
from .const import CONF_MACHINE_TYPE, DOMAIN
from .coordinator import JuraCoordinator
from .entity import JuraEntity

_LOGGER = logging.getLogger(__name__)

SELECT_KINDS = {"switch", "combobox", "item_slider"}

# Brew-parameter SelectEntity kinds: which product argument maps to which
# coordinator.brew_params key and entity-name suffix.
_BREW_SELECT_PARAMS = {
    "COFFEE_STRENGTH": ("strength", "strength"),
    "TEMPERATURE": ("temp", "temperature"),
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JuraCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    machine_type = config_entry.data.get(CONF_MACHINE_TYPE)
    if not machine_type:
        return

    entities: list[SelectEntity] = []
    entities.extend(_setting_select_entities(coordinator, config_entry, machine_type))
    entities.extend(_brew_param_select_entities(coordinator, config_entry, machine_type))
    async_add_entities(entities)


def _setting_select_entities(
    coordinator: JuraCoordinator, config_entry: ConfigEntry, machine_type: str
) -> list[SelectEntity]:
    """Build the writable machine-setting selects (combobox/switch/item_slider).

    Failing to load the profile is a configuration / version-skew error —
    log and skip rather than crashing the integration.
    """
    try:
        from jura_connect import load_profile
    except ImportError:  # pragma: no cover
        return []
    try:
        profile = load_profile(machine_type)
    except KeyError:
        _LOGGER.warning("no profile for machine_type %s; skipping setting selects", machine_type)
        return []

    entities: list[SelectEntity] = []
    for setting in profile.settings:
        if setting.kind not in SELECT_KINDS:
            continue
        if not setting.items:
            continue
        entities.append(SettingSelectEntity(coordinator, config_entry, setting))
    return entities


def _brew_param_select_entities(
    coordinator: JuraCoordinator, config_entry: ConfigEntry, machine_type: str
) -> list[SelectEntity]:
    """Build per-product strength + temperature selects from the machine def."""
    base_dir = jura_connect_xml_dir()
    if base_dir is None:
        return []
    definition = load_definition(machine_type, base_dir=base_dir)
    if definition is None:
        _LOGGER.warning("no machine definition for %s; skipping brew param selects", machine_type)
        return []

    entities: list[SelectEntity] = []
    for product in definition.products:
        for kind in _BREW_SELECT_PARAMS:
            arg = product.arg(kind)
            if arg is not None and arg.items:
                entities.append(BrewParamSelect(coordinator, config_entry, product, arg))
    return entities


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


class BrewParamSelect(JuraEntity, SelectEntity):
    """Stages a per-product brew parameter (coffee strength or temperature).

    A CONFIG entity that holds the value to use on the *next* brew of its
    product; it never talks to the machine. The chosen byte value lives on
    ``coordinator.brew_params[product.code]`` so the brew button can read
    it. Options and the seeded default come from the product's XML argument.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JuraCoordinator,
        config_entry: ConfigEntry,
        product: ProductDef,
        arg: ProductArg,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._product = product
        self._arg = arg
        self._param_key, label = _BREW_SELECT_PARAMS[arg.kind]
        self._value_by_name = {name: value for name, value in arg.items}
        self._name_by_value = {value: name for name, value in arg.items}
        self._attr_name = f"{product.name} {label}"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brew_{product.code}_{self._param_key}"
        self._attr_options = [name for name, _ in arg.items]
        coordinator.brew_params.setdefault(product.code, {}).setdefault(self._param_key, arg.default)

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.brew_params.get(self._product.code, {}).get(self._param_key, self._arg.default)
        return self._name_by_value.get(value)

    async def async_select_option(self, option: str) -> None:
        value = self._value_by_name.get(option)
        if value is None:
            return
        self.coordinator.brew_params.setdefault(self._product.code, {})[self._param_key] = value
        self.async_write_ha_state()
