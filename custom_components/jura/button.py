"""Button platform: one "Brew <product>" button per machine product.

Pressing a button PHYSICALLY brews a drink on the machine. The button
encodes the full 16-byte ``@TP:`` start command from the machine's
product definition (sourced from the ``jura_connect`` package data) and
the per-product parameters staged on the coordinator by the brew
strength/water/temperature entities, then dispatches the library's
``brew`` command (with ``allow_destructive=True`` — pressing the button
is the explicit opt-in).
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .brew import ProductDef, build_start_command, jura_connect_xml_dir, load_definition
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

    # Products come from jura_connect's bundled machine-definition XMLs.
    # A missing package / profile is a configuration / version-skew issue —
    # log and skip rather than crash the integration (mirrors select.py).
    base_dir = jura_connect_xml_dir()
    if base_dir is None:
        _LOGGER.warning("jura_connect XML data unavailable; skipping brew button setup")
        return
    definition = load_definition(machine_type, base_dir=base_dir)
    if definition is None:
        _LOGGER.warning("no machine definition for %s; skipping brew button setup", machine_type)
        return

    entities = [JuraBrewButton(coordinator, config_entry, product) for product in definition.products]
    async_add_entities(entities)


class JuraBrewButton(JuraEntity, ButtonEntity):
    """Brews one product. Pressing this **physically brews a drink.**

    Reads the staged strength/water/temperature from
    ``coordinator.brew_params`` (falling back to the XML defaults baked
    into :func:`build_start_command`) and dispatches the library ``brew``
    command with the recipe payload (the 16-byte hex, *without* the
    ``@TP:`` prefix — the library re-adds it).
    """

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry, product: ProductDef) -> None:
        super().__init__(coordinator, config_entry)
        self._product = product
        self._attr_name = f"Brew {product.name}"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brew_{product.code}"

    async def async_press(self) -> None:
        params = self.coordinator.brew_params.get(self._product.code, {})
        payload = build_start_command(
            self._product,
            strength=params.get("strength"),
            water_ml=params.get("water_ml"),
            temp=params.get("temp"),
        )
        recipe = payload.removeprefix("@TP:")
        await self.coordinator.run_command("brew", [recipe], allow_destructive=True)
