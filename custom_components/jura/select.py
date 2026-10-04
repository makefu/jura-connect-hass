"""Select platform: machine-setting dropdowns + the brew control panel.

Two families of selects live here:

* **Setting selects** — one dropdown per pick-from-N machine setting
  (SettingDef kinds ``switch``, ``combobox`` and ``item_slider``).
  ``step_slider`` settings (hardness) are handled by the ``number``
  platform instead.
* **Brew control panel** — a small, machine-wide set that stages the
  *next* brew: a product picker plus strength / water / temperature /
  grinder-ratio / milk / milk-foam selects. Each parameter select carries a
  translated ``factory_default`` option (meaning "let the recipe builder use the
  product's XML default" — it does
  NOT mean "use the machine's own configured setting", which JURA WiFi has
  no mechanism for) and recomputes its options from whichever product is
  currently selected. Per-product choices persist across restarts
  (``coordinator.brew_prefs``). None of them talk to the machine; the brew
  button reads the staged ``coordinator.brew_selection`` and builds the
  recipe via the ``jura_connect`` library.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from jura_connect import (
    KIND_COFFEE_STRENGTH,
    KIND_GRINDER_RATIO,
    KIND_MILK_AMOUNT,
    KIND_MILK_FOAM_AMOUNT,
    KIND_TEMPERATURE,
    KIND_WATER_AMOUNT,
    ProductParam,
    load_profile,
)

from .const import CONF_MACHINE_TYPE, DOMAIN
from .coordinator import JuraCoordinator
from .entity import JuraEntity

_LOGGER = logging.getLogger(__name__)

SELECT_KINDS = {"switch", "combobox", "item_slider"}

# Sentinel option meaning "don't override — let the recipe builder use the
# product's XML/factory default value". This is NOT "use the machine's own
# stored setting": JURA WiFi exposes no such mechanism.
FACTORY_DEFAULT = "factory_default"


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
    entities.extend(_brew_select_entities(coordinator, config_entry))
    async_add_entities(entities)


def _setting_select_entities(
    coordinator: JuraCoordinator, config_entry: ConfigEntry, machine_type: str
) -> list[SelectEntity]:
    """Build the writable machine-setting selects (combobox/switch/item_slider).

    Failing to load the profile is a configuration / version-skew error —
    log and skip rather than crashing the integration.
    """
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


def _brew_select_entities(coordinator: JuraCoordinator, config_entry: ConfigEntry) -> list[SelectEntity]:
    """Build the machine-wide brew control panel (product + parameter selects).

    A parameter select is created when *any* product on the machine exposes
    that recipe parameter; it then toggles availability per the currently
    selected product (e.g. strength is unavailable while hot water is selected).
    """
    profile = coordinator.brew_profile
    if profile is None or not profile.products:
        return []

    entities: list[SelectEntity] = [BrewProductSelect(coordinator, config_entry)]
    if any(product.param(KIND_COFFEE_STRENGTH) for product in profile.products):
        entities.append(BrewStrengthSelect(coordinator, config_entry))
    if any(product.param(KIND_WATER_AMOUNT) for product in profile.products):
        entities.append(BrewWaterSelect(coordinator, config_entry))
    if any(product.param(KIND_TEMPERATURE) for product in profile.products):
        entities.append(BrewTempSelect(coordinator, config_entry))
    if any(product.param(KIND_GRINDER_RATIO) for product in profile.products):
        entities.append(BrewGrinderRatioSelect(coordinator, config_entry))
    if any(product.param(KIND_MILK_AMOUNT) for product in profile.products):
        entities.append(BrewMilkSelect(coordinator, config_entry))
    if any(product.param(KIND_MILK_FOAM_AMOUNT) for product in profile.products):
        entities.append(BrewMilkFoamSelect(coordinator, config_entry))
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
        # Setting names come from the machine profile, not a fixed enum, so
        # only the "Setting" prefix is localisable: HA renders
        # entity.select.setting.name ("Setting {setting}") and fills the
        # placeholder with the profile setting name.
        self._attr_translation_key = "setting"
        self._attr_translation_placeholders = {"setting": setting_def.name.replace("_", " ")}
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


class BrewProductSelect(JuraEntity, SelectEntity):
    """Picks which product the brew button (and parameter selects) act on.

    Selecting a product makes its Code the staged product and hydrates
    strength/water/temperature from that product's *saved preferences*
    (each missing param falls back to the translated factory-default option).
    It then asks the
    coordinator to refresh listeners so the dependent parameter selects
    re-render their (product-specific) options and loaded values.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "brew_product"

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brew_product"

    @property
    def options(self) -> list[str]:
        profile = self.coordinator.brew_profile
        if profile is None:
            return []
        return [product.name for product in profile.products]

    @property
    def available(self) -> bool:
        return bool(self.options)

    @property
    def current_option(self) -> str | None:
        product = self.coordinator.selected_product()
        return product.name if product is not None else None

    async def async_select_option(self, option: str) -> None:
        profile = self.coordinator.brew_profile
        if profile is None:
            return
        for product in profile.products:
            if product.name == option:
                # Make this the current product and load its saved prefs into
                # the staged selection (keyed by the 2-hex Code string).
                self.coordinator.select_brew_product(f"{product.code:02X}")
                # Refresh the whole panel: the parameter selects' options and
                # values depend on the now-current product.
                self.coordinator.async_update_listeners()
                return


class _BrewParamSelect(JuraEntity, SelectEntity):
    """Base for a brew parameter select bound to the *currently-selected* product.

    Subclasses bind to one library recipe-param kind (strength / water /
    temperature / grinder ratio / milk / milk foam). Options always lead with
    :data:`FACTORY_DEFAULT`; the value lives on
    ``coordinator.brew_selection[<key>]`` where ``None`` means
    the translated factory-default option (send the product's XML default).
    Changing the value
    also remembers it for the current product (``coordinator.brew_prefs``) and
    schedules a persistent save. The select is unavailable while the selected
    product doesn't expose the parameter.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _param_kind: str
    _selection_key: str
    _name_suffix: str

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        # A translated subclass must leave _attr_name unset: Home Assistant
        # only consults translation_key when no explicit name is present.
        if getattr(self, "_attr_translation_key", None) is None:
            self._attr_name = f"Brew {self._name_suffix}"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brew_{self._selection_key}"

    def _param(self) -> ProductParam | None:
        product = self.coordinator.selected_product()
        return product.param(self._param_kind) if product is not None else None

    @property
    def available(self) -> bool:
        return self._param() is not None

    @property
    def options(self) -> list[str]:
        param = self._param()
        if param is None:
            return [FACTORY_DEFAULT]
        return [FACTORY_DEFAULT, *self._value_options(param)]

    @property
    def current_option(self) -> str | None:
        param = self._param()
        if param is None:
            return None
        value = self.coordinator.brew_selection.get(self._selection_key)
        if value is None:
            return FACTORY_DEFAULT
        return self._option_for_value(param, int(value))

    async def async_select_option(self, option: str) -> None:
        if option == FACTORY_DEFAULT:
            value: int | None = None
        else:
            param = self._param()
            if param is None:
                return
            value = self._value_for_option(param, option)
            if value is None:
                return
        # Stage + remember for the current product, then persist (debounced).
        self.coordinator.set_brew_param(self._selection_key, value)
        await self.coordinator.save_brew_prefs()
        self.async_write_ha_state()

    # --- per-kind value <-> option mapping -------------------------------
    def _value_options(self, param: ProductParam) -> list[str]:
        raise NotImplementedError

    def _value_for_option(self, param: ProductParam, option: str) -> int | None:
        raise NotImplementedError

    def _option_for_value(self, param: ProductParam, value: int) -> str | None:
        raise NotImplementedError


class _ItemBrewSelect(_BrewParamSelect):
    """Brew parameter backed by a fixed list of named ITEMs.

    ``ProductParam.items`` carries hex-string ``.value``s (e.g. ``"02"``); the
    staged selection stores the numeric form (``int(value, 16)``) so it round-
    trips as JSON and feeds ``build_recipe_hex`` directly.
    """

    def _value_options(self, param: ProductParam) -> list[str]:
        return [item.name for item in param.items]

    def _value_for_option(self, param: ProductParam, option: str) -> int | None:
        for item in param.items:
            if item.name == option:
                return int(item.value, 16)
        return None

    def _option_for_value(self, param: ProductParam, value: int) -> str | None:
        for item in param.items:
            if int(item.value, 16) == value:
                return item.name
        return None


class BrewStrengthSelect(_ItemBrewSelect):
    """Coffee strength for the next brew (e.g. 1..10), or Factory Default."""

    _param_kind = KIND_COFFEE_STRENGTH
    _selection_key = "strength"
    _name_suffix = "Strength"
    _attr_translation_key = "brew_strength"


class BrewTempSelect(_ItemBrewSelect):
    """Temperature for the next brew (e.g. Low/Normal/High), or Factory Default."""

    _param_kind = KIND_TEMPERATURE
    _selection_key = "temp"
    _name_suffix = "Temperature"
    _attr_translation_key = "brew_temperature"


class BrewGrinderRatioSelect(_ItemBrewSelect):
    """Profile-declared left:right bean split for the next brew."""

    _param_kind = KIND_GRINDER_RATIO
    _selection_key = "grinder_ratio"
    _name_suffix = "Grinder Ratio"
    _attr_translation_key = "brew_grinder_ratio"


class _RangeBrewSelect(_BrewParamSelect):
    """Brew parameter backed by a numeric ``min..max`` range (water/milk/foam).

    A select (rather than a number) so it can carry the ``Factory Default``
    sentinel; its options are the product's ``min..max`` range in ``step``
    increments.
    """

    @staticmethod
    def _value_range(param: ProductParam) -> range:
        low = param.minimum if param.minimum is not None else 0
        high = param.maximum if param.maximum is not None else low
        step = param.step or 1
        return range(low, high + 1, step)

    def _value_options(self, param: ProductParam) -> list[str]:
        return [str(value) for value in self._value_range(param)]

    def _value_for_option(self, param: ProductParam, option: str) -> int | None:
        try:
            return int(option)
        except ValueError:
            return None

    def _option_for_value(self, param: ProductParam, value: int) -> str | None:
        return str(value)


class BrewWaterSelect(_RangeBrewSelect):
    """Water amount (mL) for the next brew, or Factory Default."""

    _param_kind = KIND_WATER_AMOUNT
    _selection_key = "water_ml"
    _name_suffix = "Water"
    _attr_translation_key = "brew_water"


class BrewMilkSelect(_RangeBrewSelect):
    """Milk dispensing time (seconds) for the next brew, or Factory Default.

    JURA machines meter milk by pump time, not volume — the F5 recipe byte
    carries seconds, so that is the unit exposed here.
    """

    _param_kind = KIND_MILK_AMOUNT
    _selection_key = "milk_s"
    _name_suffix = "Milk"
    _attr_translation_key = "brew_milk"


class BrewMilkFoamSelect(_RangeBrewSelect):
    """Milk foam dispensing time (seconds) for the next brew, or Factory Default."""

    _param_kind = KIND_MILK_FOAM_AMOUNT
    _selection_key = "milk_foam_s"
    _name_suffix = "Milk Foam"
    _attr_translation_key = "brew_milk_foam"
