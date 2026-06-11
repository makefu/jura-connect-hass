"""Tests for the product-aware brew service and the decalc->descale rename.

The ``jura.brew`` service gains a friendly ``product`` path (build the
``@TP:`` payload from the machine's product table) while keeping the
legacy raw ``recipe`` path intact. ``jura.descale`` is the user-facing
service name and maps to the library's ``decalc`` command; the legacy
``jura.decalc`` service alias has been removed. No machine I/O —
run_command is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol

from custom_components.jura import _register_services
from custom_components.jura.const import CONF_CONN_ID, CONF_HOST, CONF_MACHINE_TYPE, DOMAIN

_DEFAULT_RECIPE = "30000812000002000100000000000000"
_OVERRIDE_RECIPE = "3000021A000001000100000000000000"


def _hass_with_coordinator(coordinator) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: {"test_entry_id": coordinator}}
    services: dict[tuple[str, str], dict] = {}

    def has_service(domain: str, name: str) -> bool:
        return (domain, name) in services

    def async_register(domain, name, handler, schema=None, supports_response=None):
        services[(domain, name)] = {"handler": handler, "schema": schema, "supports_response": supports_response}

    hass.services.has_service = has_service
    hass.services.async_register = async_register
    hass.services._registered = services
    return hass


def _mock_coordinator(machine_type: str = "EF1091") -> MagicMock:
    coordinator = MagicMock()
    coordinator.run_command = AsyncMock(return_value={"name": "brew", "value": "ok"})
    config_entry = MagicMock()
    config_entry.data = {CONF_MACHINE_TYPE: machine_type, CONF_HOST: "192.0.2.10", CONF_CONN_ID: "x"}
    coordinator.config_entry = config_entry
    return coordinator


def _brew_handler(hass):
    return hass.services._registered[(DOMAIN, "brew")]["handler"]


# ---------------------------------------------------------------------------
# decalc -> descale rename
# ---------------------------------------------------------------------------


def test_descale_registered_and_decalc_removed():
    hass = _hass_with_coordinator(_mock_coordinator())
    _register_services(hass)
    assert (DOMAIN, "descale") in hass.services._registered
    assert (DOMAIN, "decalc") not in hass.services._registered


async def test_descale_dispatches_library_decalc_command():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    handler = hass.services._registered[(DOMAIN, "descale")]["handler"]
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id"}
    await handler(call)
    coordinator.run_command.assert_awaited_once_with("decalc", [], allow_destructive=True)


# ---------------------------------------------------------------------------
# brew by product name (friendly path)
# ---------------------------------------------------------------------------


async def test_brew_by_product_uses_xml_defaults():
    pytest.importorskip("jura_connect")
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "product": "Espresso Doppio"}
    await _brew_handler(hass)(call)
    coordinator.run_command.assert_awaited_once_with("brew", [_DEFAULT_RECIPE], allow_destructive=True)


async def test_brew_by_product_with_overrides():
    pytest.importorskip("jura_connect")
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {
        "config_entry_id": "test_entry_id",
        "product": "espresso doppio",  # case-insensitive
        "strength": 2,
        "water_ml": 130,
        "temperature": 1,
    }
    await _brew_handler(hass)(call)
    coordinator.run_command.assert_awaited_once_with("brew", [_OVERRIDE_RECIPE], allow_destructive=True)


async def test_brew_by_product_clamps_out_of_range_water():
    """An absurd water_ml clamps to the product's Max, never wrapping mod 256.

    Espresso Doppio water Max 160 / step 5 -> 32 == 0x20. The pre-clamp code
    masked with ``& 0xFF`` and would have sent 99999 // 5 == 19999 -> 0x1F.
    """
    pytest.importorskip("jura_connect")
    from custom_components.jura.brew import jura_connect_xml_dir, load_definition

    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "product": "Espresso Doppio", "water_ml": 99999}
    await _brew_handler(hass)(call)

    sent = coordinator.run_command.await_args.args[1][0]
    definition = load_definition("EF1091", base_dir=jura_connect_xml_dir())
    product = next(p for p in definition.products if p.name.casefold() == "espresso doppio")
    water = product.arg("WATER_AMOUNT")
    expected = f"{water.max // (water.step or 1):02X}"
    water_byte = sent[6:8]  # byte index 3 -> hex chars [6:8]
    assert water_byte == expected
    assert water_byte not in ("FF", "1F")


async def test_brew_unknown_product_raises():
    pytest.importorskip("jura_connect")
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "product": "Nonexistent Drink"}
    with pytest.raises(ValueError):
        await _brew_handler(hass)(call)


# ---------------------------------------------------------------------------
# brew legacy recipe path + mutual exclusion
# ---------------------------------------------------------------------------


async def test_brew_legacy_recipe_path_preserved():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "recipe": "01"}
    await _brew_handler(hass)(call)
    coordinator.run_command.assert_awaited_once_with("brew", ["01"], allow_destructive=True)


async def test_brew_rejects_product_and_recipe_together():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "product": "Espresso Doppio", "recipe": "01"}
    with pytest.raises(vol.Invalid):
        await _brew_handler(hass)(call)


async def test_brew_rejects_neither_product_nor_recipe():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id"}
    with pytest.raises(vol.Invalid):
        await _brew_handler(hass)(call)
