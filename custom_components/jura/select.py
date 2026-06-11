"""Select platform: machine-setting dropdowns + the brew control panel.

Two families of selects live here:

* **Setting selects** — one dropdown per pick-from-N machine setting
  (SettingDef kinds ``switch``, ``combobox`` and ``item_slider``).
  ``step_slider`` settings (hardness) are handled by the ``number``
  platform instead.
* **Brew control panel** — a small, machine-wide set that stages the
  *next* brew: a product picker plus strength / water / temperature
  selects. Each parameter select carries a ``"Factory Default"`` option
  (meaning "send that product's XML/factory default value" — i.e. pass
  ``None`` to ``build_start_command``; it does NOT mean "use the machine's
  own configured setting", which JURA WiFi has no mechanism for) and
  recomputes its options from whichever product is currently selected.
  Per-product choices persist across restarts (``coordinator.brew_prefs``):
  picking a product hydrates the param selects from its saved prefs, and
  editing a param remembers it for that product. None of them talk to the
  machine; the brew button reads the staged ``coordinator.brew_selection``
  and encodes the ``@TP:`` start command.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .brew import ProductArg
from .const import CONF_MACHINE_TYPE, DOMAIN
from .coordinator import JuraCoordinator
from .entity import JuraEntity

_LOGGER = logging.getLogger(__name__)

SELECT_KINDS = {"switch", "combobox", "item_slider"}

# Sentinel option meaning "don't override — send the product's XML/factory
# default value" (i.e. pass ``None`` to ``build_start_command``). This is NOT
# "use the machine's own stored setting": JURA WiFi exposes no such mechanism,
# so an explicit, clamped value is always sent.
FACTORY_DEFAULT = "Factory Default"


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


def _brew_select_entities(coordinator: JuraCoordinator, config_entry: ConfigEntry) -> list[SelectEntity]:
    """Build the machine-wide brew control panel (product + parameter selects).

    A parameter select is created when *any* product on the machine exposes
    that argument; it then toggles availability per the currently-selected
    product (e.g. strength is unavailable while hot water is selected).
    """
    definition = coordinator.brew_definition
    if definition is None or not definition.products:
        return []

    entities: list[SelectEntity] = [BrewProductSelect(coordinator, config_entry)]
    if any(product.arg("COFFEE_STRENGTH") for product in definition.products):
        entities.append(BrewStrengthSelect(coordinator, config_entry))
    if any(product.arg("WATER_AMOUNT") for product in definition.products):
        entities.append(BrewWaterSelect(coordinator, config_entry))
    if any(product.arg("TEMPERATURE") for product in definition.products):
        entities.append(BrewTempSelect(coordinator, config_entry))
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


class BrewProductSelect(JuraEntity, SelectEntity):
    """Picks which product the brew button (and parameter selects) act on.

    Selecting a product makes its Code the staged product and hydrates
    strength/water/temperature from that product's *saved preferences*
    (each missing param falls back to "Factory Default"). It then asks the
    coordinator to refresh listeners so the dependent parameter selects
    re-render their (product-specific) options and loaded values.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_name = "Brew Product"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brew_product"

    @property
    def options(self) -> list[str]:
        definition = self.coordinator.brew_definition
        if definition is None:
            return []
        return [product.name for product in definition.products]

    @property
    def available(self) -> bool:
        return bool(self.options)

    @property
    def current_option(self) -> str | None:
        product = self.coordinator.selected_product()
        return product.name if product is not None else None

    async def async_select_option(self, option: str) -> None:
        definition = self.coordinator.brew_definition
        if definition is None:
            return
        for product in definition.products:
            if product.name == option:
                # Make this the current product and load its saved prefs into
                # the staged selection.
                self.coordinator.select_brew_product(product.code)
                # Refresh the whole panel: the parameter selects' options and
                # values depend on the now-current product.
                self.coordinator.async_update_listeners()
                return


class _BrewParamSelect(JuraEntity, SelectEntity):
    """Base for a brew parameter select bound to the *currently-selected* product.

    Subclasses bind to one product argument kind (strength / water /
    temperature). Options always lead with :data:`FACTORY_DEFAULT`; the
    value lives on ``coordinator.brew_selection[<key>]`` where ``None``
    means "Factory Default" (send the product's XML default). Changing the
    value also remembers it for the current product (``coordinator.brew_prefs``)
    and schedules a persistent save. The select is unavailable while the
    selected product doesn't expose the argument.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _arg_kind: str
    _selection_key: str
    _name_suffix: str

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_name = f"Brew {self._name_suffix}"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brew_{self._selection_key}"

    def _arg(self) -> ProductArg | None:
        product = self.coordinator.selected_product()
        return product.arg(self._arg_kind) if product is not None else None

    @property
    def available(self) -> bool:
        return self._arg() is not None

    @property
    def options(self) -> list[str]:
        arg = self._arg()
        if arg is None:
            return [FACTORY_DEFAULT]
        return [FACTORY_DEFAULT, *self._value_options(arg)]

    @property
    def current_option(self) -> str | None:
        arg = self._arg()
        if arg is None:
            return None
        value = self.coordinator.brew_selection.get(self._selection_key)
        if value is None:
            return FACTORY_DEFAULT
        return self._option_for_value(arg, value)

    async def async_select_option(self, option: str) -> None:
        if option == FACTORY_DEFAULT:
            value: int | None = None
        else:
            arg = self._arg()
            if arg is None:
                return
            value = self._value_for_option(arg, option)
            if value is None:
                return
        # Stage + remember for the current product, then persist (debounced).
        self.coordinator.set_brew_param(self._selection_key, value)
        await self.coordinator.save_brew_prefs()
        self.async_write_ha_state()

    # --- per-kind value <-> option mapping -------------------------------
    def _value_options(self, arg: ProductArg) -> list[str]:
        raise NotImplementedError

    def _value_for_option(self, arg: ProductArg, option: str) -> int | None:
        raise NotImplementedError

    def _option_for_value(self, arg: ProductArg, value: int) -> str | None:
        raise NotImplementedError


class _ItemBrewSelect(_BrewParamSelect):
    """Brew parameter backed by a fixed list of named ITEMs (strength/temperature)."""

    def _value_options(self, arg: ProductArg) -> list[str]:
        return [name for name, _ in arg.items]

    def _value_for_option(self, arg: ProductArg, option: str) -> int | None:
        for name, value in arg.items:
            if name == option:
                return value
        return None

    def _option_for_value(self, arg: ProductArg, value: int) -> str | None:
        for name, candidate in arg.items:
            if candidate == value:
                return name
        return None


class BrewStrengthSelect(_ItemBrewSelect):
    """Coffee strength for the next brew (e.g. 1..10), or Factory Default."""

    _arg_kind = "COFFEE_STRENGTH"
    _selection_key = "strength"
    _name_suffix = "Strength"


class BrewTempSelect(_ItemBrewSelect):
    """Temperature for the next brew (e.g. Low/Normal/High), or Factory Default."""

    _arg_kind = "TEMPERATURE"
    _selection_key = "temp"
    _name_suffix = "Temperature"


class BrewWaterSelect(_BrewParamSelect):
    """Water amount (mL) for the next brew, or Factory Default.

    A select (rather than a number) so it can carry the ``Factory Default``
    sentinel; its options are the product's ``min..max`` range in ``step``
    increments.
    """

    _arg_kind = "WATER_AMOUNT"
    _selection_key = "water_ml"
    _name_suffix = "Water"

    @staticmethod
    def _ml_range(arg: ProductArg) -> range:
        low = arg.min if arg.min is not None else 0
        high = arg.max if arg.max is not None else low
        step = arg.step or 1
        return range(low, high + 1, step)

    def _value_options(self, arg: ProductArg) -> list[str]:
        return [str(ml) for ml in self._ml_range(arg)]

    def _value_for_option(self, arg: ProductArg, option: str) -> int | None:
        try:
            return int(option)
        except ValueError:
            return None

    def _option_for_value(self, arg: ProductArg, value: int) -> str | None:
        return str(value)
