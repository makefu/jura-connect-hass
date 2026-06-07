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

    from .const import DOMAIN
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
    SERVICE_DECALC = "decalc"
    SERVICE_FILTER_CHANGE = "filter_change"
    SERVICE_CAPPU_RINSE = "cappu_rinse"
    SERVICE_CAPPU_CLEAN = "cappu_clean"
    SERVICE_POWER_OFF = "power_off"
    SERVICE_RESTART = "restart"

    # Map HA service name -> jura_connect command name. Every entry runs with
    # ``allow_destructive=True`` because the user invoked the dedicated service
    # explicitly; the named service *is* the opt-in.
    _COMMAND_SERVICES: dict[str, str] = {
        SERVICE_LOCK_SCREEN: "lock",
        SERVICE_UNLOCK_SCREEN: "unlock",
        SERVICE_CLEAN: "clean",
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

    # Optional recipe overrides, passed to jura_connect's brew command as
    # ``param=value`` args (the lib validates them against the machine XML
    # and builds the @TP recipe blob). Keys here -> lib CLI keys below.
    BREW_SCHEMA = _BASE_TARGET_SCHEMA.extend(
        {
            vol.Required("recipe"): str,
            vol.Optional("water"): vol.Coerce(int),
            vol.Optional("strength"): vol.Coerce(int),
            vol.Optional("temperature"): vol.In(["low", "normal", "high"]),
            vol.Optional("milk_foam"): vol.Coerce(int),
            vol.Optional("milk_break"): vol.Coerce(int),
            vol.Optional("bypass"): vol.Coerce(int),
        }
    )

    # HA service field -> jura_connect brew CLI key.
    _BREW_OVERRIDE_KEYS: dict[str, str] = {
        "water": "water",
        "strength": "strength",
        "temperature": "temp",
        "milk_foam": "milk",
        "milk_break": "milk_break",
        "bypass": "bypass",
    }


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
        args = [call.data["recipe"]]
        args += [
            f"{cli_key}={call.data[field]}" for field, cli_key in _BREW_OVERRIDE_KEYS.items() if field in call.data
        ]
        return await coordinator.run_command("brew", args, allow_destructive=True)

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
