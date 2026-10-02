"""Jura coffee machine integration."""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

try:
    import voluptuous as vol
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import Platform
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
    from homeassistant.helpers import entity_registry as er
    from jura_connect import (
        KIND_COFFEE_STRENGTH,
        KIND_MILK_AMOUNT,
        KIND_MILK_FOAM_AMOUNT,
        KIND_TEMPERATURE,
        KIND_WATER_AMOUNT,
        ProductDef,
        load_profile,
    )

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
    SERVICE_FILTER_CHANGE = "filter_change"
    SERVICE_CAPPU_RINSE = "cappu_rinse"
    SERVICE_CAPPU_CLEAN = "cappu_clean"
    SERVICE_POWER_OFF = "power_off"
    SERVICE_RESTART = "restart"
    SERVICE_CANCEL = "cancel"
    SERVICE_SKIP_QUALITY_STEP = "skip_quality_step"
    SERVICE_MILK_COOLER_STATUS = "milk_cooler_status"
    SERVICE_RESTART_DONGLE = "restart_dongle"
    SERVICE_SPECIAL_COUNTERS = "special_counters"

    # Map HA service name -> (jura_connect command name, allow_destructive).
    # Gated entries run with allow_destructive=True because the user invoked
    # the dedicated service explicitly; the named service *is* the opt-in.
    # Ungated entries are read-only or harmless.
    _COMMAND_SERVICES: dict[str, tuple[str, bool]] = {
        SERVICE_LOCK_SCREEN: ("lock", True),
        SERVICE_UNLOCK_SCREEN: ("unlock", True),
        SERVICE_CLEAN: ("clean", True),
        SERVICE_DESCALE: ("descale", True),
        SERVICE_FILTER_CHANGE: ("filter-change", True),
        SERVICE_CAPPU_RINSE: ("cappu-rinse", True),
        SERVICE_CAPPU_CLEAN: ("cappu-clean", True),
        SERVICE_POWER_OFF: ("power-off", True),
        SERVICE_RESTART: ("restart", True),
        SERVICE_CANCEL: ("cancel", False),
        SERVICE_MILK_COOLER_STATUS: ("milk-cooler-status", False),
        SERVICE_RESTART_DONGLE: ("restart-dongle", True),
        SERVICE_SPECIAL_COUNTERS: ("special-counters", False),
    }

    # brew_service call-data axis -> library recipe-param kind.
    _BREW_SERVICE_KINDS: dict[str, str] = {
        "strength": KIND_COFFEE_STRENGTH,
        "water_ml": KIND_WATER_AMOUNT,
        "temperature": KIND_TEMPERATURE,
        "milk_s": KIND_MILK_AMOUNT,
        "milk_foam_s": KIND_MILK_FOAM_AMOUNT,
    }

    _BASE_TARGET_SCHEMA = vol.Schema(
        {
            vol.Optional("config_entry_id"): str,
            vol.Optional("entity_id"): str,
        }
    )

    # ``brew`` accepts either a friendly ``product`` (name or Code from the
    # machine's product table; strength/water_ml/temperature override the XML
    # defaults) or a raw ``recipe`` (the bare hex payload, legacy path).
    # Exactly one of ``product`` / ``recipe`` is required — enforced in the
    # handler.
    BREW_SCHEMA = _BASE_TARGET_SCHEMA.extend(
        {
            vol.Optional("recipe"): str,
            vol.Optional("product"): str,
            vol.Optional("strength"): vol.Coerce(int),
            vol.Optional("water_ml"): vol.Coerce(int),
            vol.Optional("temperature"): vol.Coerce(int),
            vol.Optional("milk_s"): vol.Coerce(int),
            vol.Optional("milk_foam_s"): vol.Coerce(int),
        }
    )

    SKIP_QUALITY_SCHEMA = _BASE_TARGET_SCHEMA.extend({vol.Optional("scope", default="one"): vol.In(["one", "all"])})


def _find_product(machine_type: str | None, name: str) -> ProductDef | None:
    """Resolve a product *name* (or 2-hex Code) to its :class:`ProductDef`.

    Products come from the ``jura_connect`` library's bundled profile for
    ``machine_type`` (matched case-insensitively by name, or by hex Code).
    Returns ``None`` when the machine type or product is unknown.
    """
    if not machine_type:
        return None
    try:
        profile = load_profile(machine_type)
    except KeyError:
        return None
    for product in profile.products:
        if product.name.casefold() == name.casefold():
            return product
    # Fall back to a hex Code match (e.g. "30" or "0x30").
    try:
        code = int(name, 16)
    except ValueError:
        return None
    return profile.product_by_code.get(code)


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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Jura from a config entry."""
    # Warm the jura_connect profile cache off the event loop. load_profile
    # reads the per-machine XML from disk and is @lru_cache'd; priming it in an
    # executor here means the coordinator's __init__ and the select/number/
    # button platforms all hit the cache instead of doing blocking disk I/O in
    # the event loop.
    machine_type = entry.data.get(CONF_MACHINE_TYPE)
    if machine_type:
        try:
            await hass.async_add_executor_job(load_profile, machine_type)
        except KeyError:
            pass  # unknown type -> coordinator disables the brew panel
    coordinator = JuraCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    # Restore persisted per-product brew preferences (strength/water/temp),
    # keyed by product Code, so the brew panel remembers them across restarts.
    await coordinator.async_load_brew_prefs()

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
            overrides: dict[str, int | str] = {}
            for axis, kind in _BREW_SERVICE_KINDS.items():
                value = call.data.get(axis)
                if value is not None:
                    overrides[kind] = value
            recipe = product.build_recipe_hex(overrides)
        elif not recipe:
            raise vol.Invalid("Provide either product or recipe")
        return await coordinator.run_brew(recipe)

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

    for service_name, (command_name, allow_destructive) in _COMMAND_SERVICES.items():
        _register_command_service(hass, service_name, command_name, allow_destructive=allow_destructive)

    _register_skip_quality_step_service(hass)


def _register_command_service(
    hass: HomeAssistant,
    service_name: str,
    command_name: str,
    *,
    allow_destructive: bool = True,
) -> None:
    """Register one config-entry-targeted command service (no call-data args)."""

    async def handler(call: ServiceCall) -> ServiceResponse:
        config_entry_id = _resolve_config_entry_id(hass, call.data)
        coordinator = _get_coordinator(hass, config_entry_id)
        return await coordinator.run_command(command_name, [], allow_destructive=allow_destructive)

    hass.services.async_register(
        DOMAIN,
        service_name,
        handler,
        schema=_BASE_TARGET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _register_skip_quality_step_service(hass: HomeAssistant) -> None:
    """Register ``skip_quality_step`` — the one command service with an argument."""

    async def handler(call: ServiceCall) -> ServiceResponse:
        config_entry_id = _resolve_config_entry_id(hass, call.data)
        coordinator = _get_coordinator(hass, config_entry_id)
        scope = str(call.data.get("scope", "one"))
        return await coordinator.run_command("skip-quality-step", [scope], allow_destructive=True)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SKIP_QUALITY_STEP,
        handler,
        schema=SKIP_QUALITY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
