"""Button platform: a single "Brew" button driving the brew control panel.

Pressing the button PHYSICALLY brews a drink. It reads the machine-wide
``coordinator.brew_selection`` (product plus optional recipe parameters staged
by the brew selects), builds the bare recipe blob from the product's
definition via the ``jura_connect`` library — a ``None`` parameter falls back
to that product's XML default — and dispatches it through
``coordinator.run_brew``, which follows the ``@TV:`` progress stream until
the machine reports ``ENJOY``.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from jura_connect import (
    KIND_COFFEE_STRENGTH,
    KIND_GRINDER_RATIO,
    KIND_MILK_AMOUNT,
    KIND_MILK_FOAM_AMOUNT,
    KIND_TEMPERATURE,
    KIND_WATER_AMOUNT,
)

from .const import DOMAIN
from .coordinator import JuraCoordinator
from .entity import JuraEntity

_LOGGER = logging.getLogger(__name__)

# Maps the component's brew_selection axis keys -> library recipe-param kinds.
_SELECTION_KINDS: dict[str, str] = {
    "strength": KIND_COFFEE_STRENGTH,
    "water_ml": KIND_WATER_AMOUNT,
    "temp": KIND_TEMPERATURE,
    "grinder_ratio": KIND_GRINDER_RATIO,
    "milk_s": KIND_MILK_AMOUNT,
    "milk_foam_s": KIND_MILK_FOAM_AMOUNT,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JuraCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    # No product table (missing jura_connect data / unknown machine type) ->
    # nothing brewable, so no button. Mirrors the select/number platforms.
    profile = coordinator.brew_profile
    if profile is None or not profile.products:
        return
    async_add_entities([JuraBrewButton(coordinator, config_entry)])


class JuraBrewButton(JuraEntity, ButtonEntity):
    """Brews the currently-selected product. **Pressing this physically brews.**

    Resolves ``coordinator.brew_selection`` to a product and parameter set,
    builds the bare recipe blob (no ``@TP:`` prefix — the backend's
    follow-brew session adds it) and runs it via ``coordinator.run_brew``.
    The button goes unavailable while the machine blocks the product.
    """

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_name = "Brew"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brew"

    @property
    def available(self) -> bool:
        product = self.coordinator.selected_product()
        if product is None:
            return False
        data = self.coordinator.data
        # The machine refuses blocked products right now; go unavailable
        # instead of erroring when the user presses brew.
        return data is None or product.name not in data.blocked_products

    async def async_press(self) -> None:
        product = self.coordinator.selected_product()
        if product is None:
            return
        selection = self.coordinator.brew_selection
        overrides: dict[str, int | str] = {}
        for axis, kind in _SELECTION_KINDS.items():
            value = selection.get(axis)
            if value is not None:
                overrides[kind] = value
        recipe = product.build_recipe_hex(overrides)
        await self.coordinator.run_brew(recipe)
