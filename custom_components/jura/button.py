"""Button platform: a single "Brew" button driving the brew control panel.

Pressing the button PHYSICALLY brews a drink. It reads the machine-wide
``coordinator.brew_selection`` (product + optional strength/water/temperature
staged by the brew selects), encodes the 16-byte ``@TP:`` start command from
the product's definition — a ``None`` parameter falls back to that product's
XML default — and dispatches the library's ``brew`` command (with
``allow_destructive=True``; pressing the button is the explicit opt-in).
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .brew import build_start_command
from .const import DOMAIN
from .coordinator import JuraCoordinator
from .entity import JuraEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JuraCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    # No product table (missing jura_connect data / unknown machine type) ->
    # nothing brewable, so no button. Mirrors the select/number platforms.
    definition = coordinator.brew_definition
    if definition is None or not definition.products:
        return
    async_add_entities([JuraBrewButton(coordinator, config_entry)])


class JuraBrewButton(JuraEntity, ButtonEntity):
    """Brews the currently-selected product. **Pressing this physically brews.**

    Resolves ``coordinator.brew_selection`` to a product and parameter set,
    encodes the recipe (the 16-byte hex *without* the ``@TP:`` prefix — the
    library re-adds it) and dispatches the ``brew`` command.
    """

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_name = "Brew"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brew"

    @property
    def available(self) -> bool:
        return self.coordinator.selected_product() is not None

    async def async_press(self) -> None:
        product = self.coordinator.selected_product()
        if product is None:
            return
        selection = self.coordinator.brew_selection
        payload = build_start_command(
            product,
            strength=selection.get("strength"),
            water_ml=selection.get("water_ml"),
            temp=selection.get("temp"),
        )
        recipe = payload.removeprefix("@TP:")
        await self.coordinator.run_command("brew", [recipe], allow_destructive=True)
