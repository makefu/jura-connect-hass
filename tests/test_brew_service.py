"""Tests for the product-aware brew service and the decalc->descale rename.

The ``jura.brew`` service gains a friendly ``product`` path (build the recipe
blob from the machine's product table via the ``jura_connect`` library) while
keeping the legacy raw ``recipe`` path intact. ``jura.descale`` is the
user-facing service name and dispatches the library's ``descale`` command; the
legacy ``jura.decalc`` service has been removed. No machine I/O — ``run_brew``
and ``run_command`` are mocked. Recipe payloads are computed from the library,
not hardcoded: the byte encoding is the library's concern, so these assert the
*wiring*.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol

jura_connect = pytest.importorskip("jura_connect")

from jura_connect import (  # noqa: E402
    KIND_COFFEE_STRENGTH,
    KIND_GRINDER_RATIO,
    KIND_MILK_FOAM_AMOUNT,
    KIND_TEMPERATURE,
    KIND_WATER_AMOUNT,
    load_profile,
)

from custom_components.jura import BREW_SCHEMA, _register_services  # noqa: E402
from custom_components.jura.const import CONF_CONN_ID, CONF_HOST, CONF_MACHINE_TYPE, DOMAIN  # noqa: E402

_PROFILE = load_profile("EF1091")
_DOPPIO = next(p for p in _PROFILE.products if p.name == "espresso_doppio")
_DEFAULT_RECIPE = _DOPPIO.build_recipe_hex({})
_OVERRIDE_RECIPE = _DOPPIO.build_recipe_hex({KIND_COFFEE_STRENGTH: 2, KIND_WATER_AMOUNT: 130, KIND_TEMPERATURE: 1})
_CAPPUCCINO = next(p for p in _PROFILE.products if p.name == "cappuccino")
_MILK_FOAM_RECIPE = _CAPPUCCINO.build_recipe_hex({KIND_MILK_FOAM_AMOUNT: 12})


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
    coordinator.run_brew = AsyncMock(return_value={"ack": "@tp", "frames": []})
    config_entry = MagicMock()
    config_entry.data = {CONF_MACHINE_TYPE: machine_type, CONF_HOST: "192.0.2.10", CONF_CONN_ID: "x"}
    coordinator.config_entry = config_entry
    return coordinator


def _brew_handler(hass):
    return hass.services._registered[(DOMAIN, "brew")]["handler"]


def _brew_call(hass, data: dict) -> MagicMock:
    """Build a ServiceCall whose data passes through the registered
    BREW_SCHEMA, like a real HA service call does."""
    call = MagicMock()
    call.data = BREW_SCHEMA(data)
    return call


# ---------------------------------------------------------------------------
# decalc -> descale rename
# ---------------------------------------------------------------------------


def test_descale_registered_and_decalc_removed():
    hass = _hass_with_coordinator(_mock_coordinator())
    _register_services(hass)
    assert (DOMAIN, "descale") in hass.services._registered
    assert (DOMAIN, "decalc") not in hass.services._registered


async def test_descale_dispatches_library_descale_command():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    handler = hass.services._registered[(DOMAIN, "descale")]["handler"]
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id"}
    await handler(call)
    coordinator.run_command.assert_awaited_once_with("descale", [], allow_destructive=True)


# ---------------------------------------------------------------------------
# brew by product name (friendly path)
# ---------------------------------------------------------------------------


async def test_brew_by_product_uses_xml_defaults():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "product": "espresso_doppio"}
    await _brew_handler(hass)(call)
    coordinator.run_brew.assert_awaited_once_with(_DEFAULT_RECIPE)


async def test_brew_by_product_with_overrides():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {
        "config_entry_id": "test_entry_id",
        "product": "ESPRESSO_DOPPIO",  # case-insensitive
        "strength": 2,
        "water_ml": 130,
        "temperature": 1,
    }
    await _brew_handler(hass)(call)
    coordinator.run_brew.assert_awaited_once_with(_OVERRIDE_RECIPE)


async def test_brew_by_product_with_milk_foam_override():
    """The milk_foam_s service field lands on the F6 recipe byte."""
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {
        "config_entry_id": "test_entry_id",
        "product": "cappuccino",
        "milk_foam_s": 12,
    }
    await _brew_handler(hass)(call)
    coordinator.run_brew.assert_awaited_once_with(_MILK_FOAM_RECIPE)


async def test_brew_by_product_with_grinder_ratio_override():
    """The service accepts an EF566 profile item and puts it on F2."""
    coordinator = _mock_coordinator("EF566")
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = _brew_call(
        hass,
        {
            "config_entry_id": "test_entry_id",
            "product": "espresso",
            "grinder_ratio": "0_100",
        },
    )
    await _brew_handler(hass)(call)

    espresso = load_profile("EF566").product_by_code[0x02]
    expected = espresso.build_recipe_hex({KIND_GRINDER_RATIO: "0_100"})
    coordinator.run_brew.assert_awaited_once_with(expected)


async def test_brew_schema_preserves_grinder_ratio_item_names():
    """vol.Any must not coerce documented item names like "100_0" to ints —
    PEP-515 digit separators would turn them into 1000 and the catalogue
    check would reject every documented value."""
    validated = BREW_SCHEMA({"config_entry_id": "e", "product": "espresso", "grinder_ratio": "100_0"})
    assert validated["grinder_ratio"] == "100_0"
    validated = BREW_SCHEMA({"config_entry_id": "e", "product": "espresso", "grinder_ratio": 2})
    assert validated["grinder_ratio"] == 2


async def test_brew_grinder_ratio_rejected_on_single_grinder_machine():
    """EF1091 products declare no F2 parameter: the service must raise
    rather than brew a wrong recipe."""
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = _brew_call(
        hass,
        {
            "config_entry_id": "test_entry_id",
            "product": "espresso_doppio",
            "grinder_ratio": "50_50",
        },
    )
    with pytest.raises(ValueError, match="grinder_ratio"):
        await _brew_handler(hass)(call)
    coordinator.run_brew.assert_not_awaited()


async def test_brew_by_product_code_resolves():
    """A 2-hex product Code also resolves (espresso_doppio is code 0x30)."""
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "product": "30"}
    await _brew_handler(hass)(call)
    coordinator.run_brew.assert_awaited_once_with(_DEFAULT_RECIPE)


async def test_brew_by_product_out_of_range_water_raises():
    """The library validates ranges and raises; the service propagates it
    rather than silently wrapping mod 256. Espresso Doppio water Max is 160."""
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "product": "espresso_doppio", "water_ml": 99999}
    with pytest.raises(ValueError):
        await _brew_handler(hass)(call)
    coordinator.run_brew.assert_not_awaited()


async def test_brew_unknown_product_raises():
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
    coordinator.run_brew.assert_awaited_once_with("01")


async def test_brew_rejects_product_and_recipe_together():
    coordinator = _mock_coordinator()
    hass = _hass_with_coordinator(coordinator)
    _register_services(hass)
    call = MagicMock()
    call.data = {"config_entry_id": "test_entry_id", "product": "espresso_doppio", "recipe": "01"}
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
