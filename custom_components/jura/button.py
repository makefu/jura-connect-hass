"""Button platform: one brew button per product from the machine profile.

Pressing a button starts the product with the machine XML's default
recipe (the same defaults the front panel uses). Parameterised brews —
custom water amount, strength, temperature — go through the
``jura.brew`` service instead; a stateless button can't carry fields.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MACHINE_TYPE, DOMAIN
from .coordinator import JuraCoordinator
from .entity import JuraEntity

_LOGGER = logging.getLogger(__name__)

# Icon per rough product family; default falls back to the coffee cup.
_PRODUCT_ICONS = {
    "hotwater": "mdi:kettle-steam",
    "milk_foam": "mdi:beer-outline",
    "cappuccino": "mdi:coffee",
    "latte": "mdi:glass-mug-variant",
    "macchiato": "mdi:glass-mug-variant",
}
_DEFAULT_ICON = "mdi:coffee"


def _brewable_products(profile):
    """Every product the profile exposes, in catalogue order.

    The machine XML's PRODUCTS section is exactly the set the front
    panel offers, so there is nothing to filter — powder products and
    milk-only programs included.
    """
    return tuple(profile.products)


def _icon_for(product_name: str) -> str:
    for fragment, icon in _PRODUCT_ICONS.items():
        if fragment in product_name:
            return icon
    return _DEFAULT_ICON


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JuraCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    machine_type = config_entry.data.get(CONF_MACHINE_TYPE)
    if not machine_type:
        # Without a profile there is no product catalogue to enumerate;
        # the jura.brew service (raw blob / code form) still works.
        return

    try:
        from jura_connect import load_profile
    except ImportError:  # pragma: no cover
        return
    try:
        profile = load_profile(machine_type)
    except KeyError:
        _LOGGER.warning("no profile for machine_type %s; skipping brew buttons", machine_type)
        return

    async_add_entities(
        ProductButtonEntity(coordinator, config_entry, product) for product in _brewable_products(profile)
    )


class ProductButtonEntity(JuraEntity, ButtonEntity):
    """Starts one product with its XML-default recipe when pressed.

    The press dispatches the lib's named ``brew`` command with just the
    product name — jura_connect 0.10+ builds and validates the 16-byte
    ``@TP:`` recipe blob from the machine profile's defaults.
    """

    def __init__(
        self,
        coordinator: JuraCoordinator,
        config_entry: ConfigEntry,
        product,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._product = product
        pretty = product.name.replace("_", " ")
        self._attr_name = f"Brew {pretty}"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brew_{product.name}"
        self._attr_icon = _icon_for(product.name)

    async def async_press(self) -> None:
        await self.coordinator.run_command("brew", [self._product.name], allow_destructive=True)
