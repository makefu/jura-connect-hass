"""Jura coffee machine integration."""

from __future__ import annotations

import logging

from .brew import build_start_command, jura_connect_xml_dir, load_definition

_LOGGER = logging.getLogger(__name__)

try:
    import voluptuous as vol
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import Platform
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
    from homeassistant.helpers import entity_registry as er

    from .const import CONF_MACHINE_TYPE, DOMAIN
    from .coordinator import JuraCoordinator

    _HAS_HOMEASSISTANT = True
except ImportError:
    _HAS_HOMEASSISTANT = False


if _HAS_HOMEASSISTANT:
    PLATFORMS = [
        Platform.SENSOR,
        Platform.BINARY_SENSOR,
        Platform.SELECT,
        Platform.NUMBER,
        Platform.BUTTON,
    ]

    SERVICE_FORCE_UPDATE = "force_update"
    SERVICE_LOCK_SCREEN = "lock_screen"
    SERVICE_UNLOCK_SCREEN = "unlock_screen"
    SERVICE_BREW = "brew"
    SERVICE_CLEAN = "clean"
    SERVICE_DESCALE = "descale"
    SERVICE_DECALC = "decalc"  # legacy alias of descale (back-compat)
    SERVICE_FILTER_CHANGE = "filter_change"
    SERVICE_CAPPU_RINSE = "cappu_rinse"
    SERVICE_CAPPU_CLEAN = "cappu_clean"
    SERVICE_POWER_OFF = "power_off"
    SERVICE_RESTART = "restart"

    # Map HA service name -> jura_connect command name. Every entry runs with
    # ``allow_destructive=True`` because the user invoked the dedicated service
    # explicitly; the named service *is* the opt-in. ``descale`` is the
    # user-facing name for the library's ``decalc`` command; ``decalc`` is
    # kept as a backwards-compatible alias so existing automations keep
    # working.
    _COMMAND_SERVICES: dict[str, str] = {
        SERVICE_LOCK_SCREEN: "lock",
        SERVICE_UNLOCK_SCREEN: "unlock",
        SERVICE_CLEAN: "clean",
        SERVICE_DESCALE: "decalc",
        SERVICE_DECALC: "decalc",
        SERVICE_FILTER_CHANGE: "filter-change",
        SERVICE_CAPPU_RINSE: "cappu-rinse",
        SERVICE_CAPPU_CLEAN: "cappu-clean",
        SERVICE_POWER_OFF: "power-off",
        SERVICE_RESTART: "restart",
    }

    _BASE_TARGET_SCHEMA = vol.Schema(
        {
            vol.Optional("config_entry_id"): str,
            vol.Optional("entity_id"): str,
        }
    )

    # ``brew`` accepts either a friendly ``product`` (name from the machine's
    # product table; strength/water_ml/temperature override the XML defaults)
    # or a raw ``recipe`` (the 16-byte hex payload, legacy path). Exactly one
    # of ``product`` / ``recipe`` is required — enforced in the handler.
    BREW_SCHEMA = _BASE_TARGET_SCHEMA.extend(
        {
            vol.Optional("recipe"): str,
            vol.Optional("product"): str,
            vol.Optional("strength"): vol.Coerce(int),
            vol.Optional("water_ml"): vol.Coerce(int),
            vol.Optional("temperature"): vol.Coerce(int),
        }
    )


def _resolve_config_entry_id(hass: HomeAssistant, call_data: dict) -> str:
    config_entry_id = call_data.get("config_entry_id")
    entity_id = call_data.get("entity_id")
    if config_entry_id and entity_id:
        raise vol.Invalid("Provide either config_entry_id or entity_id, not both")
    if config_entry_id:
        return config_entry_id
    if entity_id:
        registry = er.async_get(hass)
        entry = registry.async_get(entity_id)
        if entry is None:
            raise ValueError(f"Entity {entity_id} not found")
        if entry.config_entry_id is None:
            raise ValueError(f"Entity {entity_id} has no config entry")
        return entry.config_entry_id
    raise vol.Invalid("Provide either config_entry_id or entity_id")


def _get_coordinator(hass: HomeAssistant, config_entry_id: str) -> JuraCoordinator:
    if config_entry_id not in hass.data.get(DOMAIN, {}):
        raise ValueError(f"Config entry {config_entry_id} not found")
    return hass.data[DOMAIN][config_entry_id]


def _find_product(machine_type: str | None, name: str):
    """Resolve a product *name* to its definition for ``machine_type``.

    Products come from jura_connect's bundled XMLs (matched case-insensitively
    by name). Returns ``None`` when the data, definition or product is missing.
    """
    base_dir = jura_connect_xml_dir()
    if base_dir is None:
        return None
    definition = load_definition(machine_type, base_dir=base_dir)
    if definition is None:
        return None
    for product in definition.products:
        if product.name.casefold() == name.casefold():
            return product
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Jura from a config entry."""
    coordinator = JuraCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down a Jura config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


def _register_services(hass: HomeAssistant) -> None:
    """Register all named services. Idempotent — safe to call per entry."""
    if hass.services.has_service(DOMAIN, SERVICE_FORCE_UPDATE):
        return

    async def handle_force_update(call: ServiceCall) -> None:
        config_entry_id = _resolve_config_entry_id(hass, call.data)
        coordinator = _get_coordinator(hass, config_entry_id)
        await coordinator.async_request_refresh()

    async def handle_brew(call: ServiceCall) -> ServiceResponse:
        config_entry_id = _resolve_config_entry_id(hass, call.data)
        coordinator = _get_coordinator(hass, config_entry_id)
        product_name = call.data.get("product")
        recipe = call.data.get("recipe")
        if product_name and recipe:
            raise vol.Invalid("Provide either product or recipe, not both")
        if product_name:
            machine_type = coordinator.config_entry.data.get(CONF_MACHINE_TYPE)
            product = _find_product(machine_type, product_name)
            if product is None:
                raise ValueError(f"Unknown product {product_name!r} for machine {machine_type!r}")
            payload = build_start_command(
                product,
                strength=call.data.get("strength"),
                water_ml=call.data.get("water_ml"),
                temp=call.data.get("temperature"),
            )
            recipe = payload.removeprefix("@TP:")
        elif not recipe:
            raise vol.Invalid("Provide either product or recipe")
        return await coordinator.run_command("brew", [recipe], allow_destructive=True)

    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_UPDATE,
        handle_force_update,
        schema=_BASE_TARGET_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_BREW,
        handle_brew,
        schema=BREW_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    for service_name, command_name in _COMMAND_SERVICES.items():
        _register_command_service(hass, service_name, command_name)


def _register_command_service(hass: HomeAssistant, service_name: str, command_name: str) -> None:
    """Register one config-entry-targeted no-arg command service."""

    async def handler(call: ServiceCall) -> ServiceResponse:
        config_entry_id = _resolve_config_entry_id(hass, call.data)
        coordinator = _get_coordinator(hass, config_entry_id)
        return await coordinator.run_command(command_name, [], allow_destructive=True)

    hass.services.async_register(
        DOMAIN,
        service_name,
        handler,
        schema=_BASE_TARGET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
