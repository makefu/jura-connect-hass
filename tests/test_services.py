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
    coordinator.run_brew = AsyncMock(return_value={"ack": "@tp", "frames": []})
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


async def test_brew_handler_follows_brew_progress():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)

    handler = hass.services._registered[(DOMAIN, "brew")]["handler"]
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "recipe": "01"}
    result = await handler(call)

    coordinator.run_brew.assert_awaited_once_with("01")
    assert result == {"ack": "@tp", "frames": []}


async def test_command_services_dispatch_respect_gate_flags():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)

    for service_name, (command_name, allow_destructive) in _COMMAND_SERVICES.items():
        handler = hass.services._registered[(DOMAIN, service_name)]["handler"]
        call = MagicMock()
        call.data = {"config_entry_id": "test_entry_id"}
        await handler(call)
        coordinator.run_command.assert_awaited_with(command_name, [], allow_destructive=allow_destructive)

    assert coordinator.run_command.await_count == len(_COMMAND_SERVICES)
    ungated = {name for name, (_cmd, gate) in _COMMAND_SERVICES.items() if not gate}
    assert {"cancel", "milk_cooler_status", "special_counters"} <= ungated


async def test_skip_quality_step_passes_scope():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)

    entry = hass.services._registered[(DOMAIN, "skip_quality_step")]
    call = MagicMock()
    call.data = entry["schema"]({"config_entry_id": "test_entry_id", "scope": "all"})
    await entry["handler"](call)
    coordinator.run_command.assert_awaited_once_with("skip-quality-step", ["all"], allow_destructive=True)

    # default scope is "one"
    call.data = entry["schema"]({"config_entry_id": "test_entry_id"})
    await entry["handler"](call)
    coordinator.run_command.assert_awaited_with("skip-quality-step", ["one"], allow_destructive=True)

    with pytest.raises(vol.Invalid):
        entry["schema"]({"config_entry_id": "test_entry_id", "scope": "both"})


def test_new_services_registered():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    for service_name in ("cancel", "skip_quality_step", "milk_cooler_status", "restart_dongle", "special_counters"):
        assert (DOMAIN, service_name) in hass.services._registered


async def test_async_setup_entry_prewarms_profile_off_event_loop(monkeypatch):
    """Regression: load_profile (blocking XML read) must be primed via the
    executor, not called inline in the event loop, so the coordinator's cached
    read in __init__ never blocks. HA flags an in-loop disk read otherwise."""
    import custom_components.jura as jura
    from custom_components.jura.const import CONF_MACHINE_TYPE

    executor_calls: list = []

    async def fake_executor(func, *args):
        executor_calls.append((func, args))
        return func(*args)

    hass = MagicMock()
    hass.data = {}
    hass.async_add_executor_job = fake_executor
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_load_brew_prefs = AsyncMock()
    monkeypatch.setattr(jura, "JuraCoordinator", lambda *a, **k: coordinator)
    monkeypatch.setattr(jura, "_register_services", lambda _hass: None)

    seen: dict = {}

    def fake_load_profile(machine_type):
        seen["machine_type"] = machine_type
        return object()

    monkeypatch.setattr(jura, "load_profile", fake_load_profile)

    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {CONF_MACHINE_TYPE: "EF1091"}

    assert await jura.async_setup_entry(hass, entry) is True
    # load_profile was invoked through the executor with the machine type.
    assert any(func is fake_load_profile for func, _ in executor_calls)
    assert seen["machine_type"] == "EF1091"


async def test_async_setup_entry_skips_prewarm_without_machine_type(monkeypatch):
    """No machine type -> no profile read at all (brew panel just stays off)."""
    import custom_components.jura as jura

    executor_calls: list = []

    async def fake_executor(func, *args):
        executor_calls.append((func, args))
        return func(*args)

    hass = MagicMock()
    hass.data = {}
    hass.async_add_executor_job = fake_executor
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_load_brew_prefs = AsyncMock()
    monkeypatch.setattr(jura, "JuraCoordinator", lambda *a, **k: coordinator)
    monkeypatch.setattr(jura, "_register_services", lambda _hass: None)

    called = {"n": 0}

    def fake_load_profile(_machine_type):
        called["n"] += 1
        return object()

    monkeypatch.setattr(jura, "load_profile", fake_load_profile)

    entry = MagicMock()
    entry.entry_id = "e2"
    entry.data = {}

    assert await jura.async_setup_entry(hass, entry) is True
    assert called["n"] == 0
