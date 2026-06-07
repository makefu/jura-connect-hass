"""Service handler tests: target resolution + dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol

from custom_components.jura import (
    _COMMAND_SERVICES,
    _get_coordinator,
    _register_services,
    _resolve_config_entry_id,
)
from custom_components.jura.const import DOMAIN
from tests.conftest import _entity_registry_instance  # type: ignore[attr-defined]


def _hass_with_coordinator(coordinator) -> MagicMock:
    """Build a MagicMock hass exposing data[DOMAIN][entry_id] and a service registry."""
    hass = MagicMock()
    hass.data = {DOMAIN: {"test_entry_id": coordinator}}
    services: dict[tuple[str, str], dict] = {}

    def has_service(domain: str, name: str) -> bool:
        return (domain, name) in services

    def async_register(domain, name, handler, schema=None, supports_response=None):
        services[(domain, name)] = {
            "handler": handler,
            "schema": schema,
            "supports_response": supports_response,
        }

    hass.services.has_service = has_service
    hass.services.async_register = async_register
    hass.services._registered = services
    return hass


def _mock_coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.run_command = AsyncMock(return_value={"name": "test", "value": "ok"})
    return coordinator


def test_resolve_by_config_entry_id():
    hass = MagicMock()
    assert _resolve_config_entry_id(hass, {"config_entry_id": "abc"}) == "abc"


def test_resolve_by_entity_id():
    _entity_registry_instance.add("sensor.test", config_entry_id="resolved")
    hass = MagicMock()
    assert _resolve_config_entry_id(hass, {"entity_id": "sensor.test"}) == "resolved"


def test_resolve_rejects_both():
    hass = MagicMock()
    with pytest.raises(vol.Invalid):
        _resolve_config_entry_id(hass, {"config_entry_id": "a", "entity_id": "b"})


def test_resolve_rejects_neither():
    hass = MagicMock()
    with pytest.raises(vol.Invalid):
        _resolve_config_entry_id(hass, {})


def test_get_coordinator_missing_entry():
    hass = MagicMock()
    hass.data = {DOMAIN: {}}
    with pytest.raises(ValueError):
        _get_coordinator(hass, "nonexistent")


def test_register_services_is_idempotent():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    count_first = len(hass.services._registered)
    _register_services(hass)  # second call should be a no-op
    assert len(hass.services._registered) == count_first


def test_register_services_registers_every_named_service():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    registered = hass.services._registered

    # force_update and brew always registered
    assert (DOMAIN, "force_update") in registered
    assert (DOMAIN, "brew") in registered

    # Every command service
    for service_name in _COMMAND_SERVICES:
        assert (DOMAIN, service_name) in registered


async def test_force_update_handler_triggers_refresh():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)

    handler = hass.services._registered[(DOMAIN, "force_update")]["handler"]
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id"}
    await handler(call)

    coordinator.async_request_refresh.assert_awaited_once()


async def test_brew_handler_passes_recipe_and_allow_destructive():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)

    handler = hass.services._registered[(DOMAIN, "brew")]["handler"]
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "recipe": "01"}
    result = await handler(call)

    coordinator.run_command.assert_awaited_once_with("brew", ["01"], allow_destructive=True)
    assert result == {"name": "test", "value": "ok"}


async def test_brew_handler_passes_recipe_overrides_in_cli_form():
    """Optional recipe parameters become param=value args for the lib's
    brew command (jura_connect >= 0.10 builds the @TP recipe blob)."""
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)

    handler = hass.services._registered[(DOMAIN, "brew")]["handler"]
    call = MagicMock()
    call.data = {
        "config_entry_id": "test_entry_id",
        "recipe": "espresso",
        "water": 35,
        "strength": 7,
        "temperature": "high",
    }
    await handler(call)

    coordinator.run_command.assert_awaited_once_with(
        "brew",
        ["espresso", "water=35", "strength=7", "temp=high"],
        allow_destructive=True,
    )


async def test_brew_handler_passes_milk_and_bypass_overrides():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)

    handler = hass.services._registered[(DOMAIN, "brew")]["handler"]
    call = MagicMock()
    call.data = {
        "config_entry_id": "test_entry_id",
        "recipe": "cappuccino",
        "milk_foam": 20,
        "milk_break": 5,
        "bypass": 45,
    }
    await handler(call)

    coordinator.run_command.assert_awaited_once_with(
        "brew",
        ["cappuccino", "milk=20", "milk_break=5", "bypass=45"],
        allow_destructive=True,
    )


def test_brew_schema_validates_temperature_values():
    from custom_components.jura import BREW_SCHEMA

    ok = BREW_SCHEMA(
        {"config_entry_id": "x", "recipe": "hotwater", "temperature": "high"}
    )
    assert ok["temperature"] == "high"
    with pytest.raises(vol.Invalid):
        BREW_SCHEMA(
            {"config_entry_id": "x", "recipe": "hotwater", "temperature": "boiling"}
        )
    with pytest.raises(vol.Invalid):
        BREW_SCHEMA({"config_entry_id": "x", "recipe": "hotwater", "water": "veel"})


async def test_command_services_dispatch_with_destructive_allowed():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)

    for service_name, command_name in _COMMAND_SERVICES.items():
        handler = hass.services._registered[(DOMAIN, service_name)]["handler"]
        call = MagicMock()
        call.data = {"config_entry_id": "test_entry_id"}
        await handler(call)

    # The last call should be the last service handler invocation
    assert coordinator.run_command.await_count == len(_COMMAND_SERVICES)
    # Every call must have allow_destructive=True
    for call_args in coordinator.run_command.await_args_list:
        assert call_args.kwargs["allow_destructive"] is True
