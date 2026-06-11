"""Tests for the compact brew "control panel".

The redesigned brew UX is five entities shared across the whole machine
(not per product): a product select, strength/water/temperature selects
(each carrying a "Machine Default" sentinel), and a single brew button.
Selections are staged on ``coordinator.brew_selection``; the button reads
them and encodes the ``@TP:`` start command. Nothing talks to a machine —
``run_command`` is mocked, so no live brew happens in tests.

These exercise the real EF1030 (E6) bundled definition so the option
lists, defaults and the two live-validated payload vectors are pinned
against actual machine data.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

jura_connect = pytest.importorskip("jura_connect")

from custom_components.jura.button import JuraBrewButton  # noqa: E402
from custom_components.jura.const import (  # noqa: E402
    CONF_AUTH_HASH,
    CONF_CONN_ID,
    CONF_HOST,
    CONF_MACHINE_TYPE,
    CONF_PIN,
    CONF_PORT,
    DOMAIN,
)
from custom_components.jura.coordinator import JuraCoordinator  # noqa: E402
from custom_components.jura.select import (  # noqa: E402
    BrewProductSelect,
    BrewStrengthSelect,
    BrewTempSelect,
    BrewWaterSelect,
)
from homeassistant.config_entries import ConfigEntry  # noqa: E402

MACHINE_DEFAULT = "Machine Default"

# EF1030 (E6) product names, in definition order.
_EF1030_PRODUCTS = [
    "Espresso",
    "Coffee",
    "Cappuccino",
    "Espresso Macchiato",
    "Hotwater Portion(normal)",
    "2 Espressi",
    "2 Coffee",
]

# Live-validated payload vectors (a real coffee was brewed from each):
#   default Espresso (code 02, strength 8, 45ml->9, temp 2)
_ESPRESSO_DEFAULT = "02000809000002000100000000000000"
#   Coffee (code 03), strength 2, 130ml (->0x1A), temp Normal(1)
_COFFEE_OVERRIDE = "0300021A000001000100000000000000"


def _entry(machine_type: str = "EF1030") -> ConfigEntry:
    return ConfigEntry(
        entry_id="test_entry_id",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 51515,
            CONF_PIN: "",
            CONF_CONN_ID: "homeassistant-test",
            CONF_AUTH_HASH: "a" * 64,
            CONF_MACHINE_TYPE: machine_type,
        },
    )


def _coordinator(entry: ConfigEntry | None = None) -> JuraCoordinator:
    """A real coordinator (so brew_definition/brew_selection/selected_product
    come from the live EF1030 data) with the machine-touching surfaces mocked."""
    entry = entry or _entry()
    backend = AsyncMock()
    coordinator = JuraCoordinator(AsyncMock(), entry, backend=backend)
    coordinator.run_command = AsyncMock(return_value={"name": "brew", "value": "ok"})
    coordinator.data = None
    return coordinator


# ---------------------------------------------------------------------------
# Coordinator brew_selection seeding
# ---------------------------------------------------------------------------


def test_coordinator_seeds_first_product_and_default_params():
    coordinator = _coordinator()
    assert coordinator.brew_selection == {
        "product": "02",  # Espresso is the first EF1030 product
        "strength": None,
        "water_ml": None,
        "temp": None,
    }
    assert coordinator.selected_product().name == "Espresso"


# ---------------------------------------------------------------------------
# Product select
# ---------------------------------------------------------------------------


def test_product_select_options_and_current(fake_config_entry):
    coordinator = _coordinator()
    entity = BrewProductSelect(coordinator, _entry())
    assert entity.options == _EF1030_PRODUCTS
    assert entity.current_option == "Espresso"
    assert entity.entity_category == "config"
    assert entity.unique_id.endswith("brew_product")


async def test_product_select_sets_code_and_resets_params():
    coordinator = _coordinator()
    # Stage some non-default params first.
    coordinator.brew_selection.update(strength=4, water_ml=120, temp=2)
    entity = BrewProductSelect(coordinator, _entry())
    await entity.async_select_option("Coffee")
    assert coordinator.brew_selection["product"] == "03"
    assert coordinator.brew_selection["strength"] is None
    assert coordinator.brew_selection["water_ml"] is None
    assert coordinator.brew_selection["temp"] is None
    assert entity.current_option == "Coffee"


# ---------------------------------------------------------------------------
# Strength select
# ---------------------------------------------------------------------------


def test_strength_select_options_and_default():
    coordinator = _coordinator()  # Espresso selected
    entity = BrewStrengthSelect(coordinator, _entry())
    assert entity.options == [MACHINE_DEFAULT, "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
    assert entity.current_option == MACHINE_DEFAULT
    assert entity.available is True
    assert entity.entity_category == "config"
    assert entity.unique_id.endswith("brew_strength")


async def test_strength_select_set_and_machine_default():
    coordinator = _coordinator()
    entity = BrewStrengthSelect(coordinator, _entry())
    await entity.async_select_option("2")
    assert coordinator.brew_selection["strength"] == 2
    assert entity.current_option == "2"
    await entity.async_select_option(MACHINE_DEFAULT)
    assert coordinator.brew_selection["strength"] is None
    assert entity.current_option == MACHINE_DEFAULT


async def test_strength_select_unavailable_without_strength_arg():
    coordinator = _coordinator()
    product = BrewProductSelect(coordinator, _entry())
    strength = BrewStrengthSelect(coordinator, _entry())
    # Hotwater has no COFFEE_STRENGTH argument.
    await product.async_select_option("Hotwater Portion(normal)")
    assert strength.available is False
    assert strength.current_option is None
    assert strength.options == [MACHINE_DEFAULT]


# ---------------------------------------------------------------------------
# Water select
# ---------------------------------------------------------------------------


def test_water_select_options_from_range():
    coordinator = _coordinator()  # Espresso: 15..80 step 5
    entity = BrewWaterSelect(coordinator, _entry())
    assert entity.options == [MACHINE_DEFAULT, *[str(v) for v in range(15, 81, 5)]]
    assert entity.current_option == MACHINE_DEFAULT
    assert entity.unique_id.endswith("brew_water_ml")


async def test_water_select_set_and_machine_default():
    coordinator = _coordinator()
    product = BrewProductSelect(coordinator, _entry())
    water = BrewWaterSelect(coordinator, _entry())
    await product.async_select_option("Coffee")  # 25..240 step 5
    assert "130" in water.options
    await water.async_select_option("130")
    assert coordinator.brew_selection["water_ml"] == 130
    assert water.current_option == "130"
    await water.async_select_option(MACHINE_DEFAULT)
    assert coordinator.brew_selection["water_ml"] is None
    assert water.current_option == MACHINE_DEFAULT


# ---------------------------------------------------------------------------
# Temperature select
# ---------------------------------------------------------------------------


def test_temp_select_options_and_mapping():
    coordinator = _coordinator()
    entity = BrewTempSelect(coordinator, _entry())
    assert entity.options == [MACHINE_DEFAULT, "Low", "Normal", "High"]
    assert entity.current_option == MACHINE_DEFAULT
    assert entity.unique_id.endswith("brew_temp")


async def test_temp_select_set_and_machine_default():
    coordinator = _coordinator()
    entity = BrewTempSelect(coordinator, _entry())
    await entity.async_select_option("Normal")
    assert coordinator.brew_selection["temp"] == 1
    assert entity.current_option == "Normal"
    await entity.async_select_option(MACHINE_DEFAULT)
    assert coordinator.brew_selection["temp"] is None


# ---------------------------------------------------------------------------
# Brew button
# ---------------------------------------------------------------------------


def test_button_name_and_unique_id():
    coordinator = _coordinator()
    button = JuraBrewButton(coordinator, _entry())
    assert button.name == "Brew"
    assert button.unique_id.endswith("homeassistant_test_brew")


async def test_button_press_espresso_machine_default_vector():
    """Espresso, all Machine Default -> the live-validated default vector."""
    coordinator = _coordinator()  # Espresso selected, all params None
    button = JuraBrewButton(coordinator, _entry())
    await button.async_press()
    coordinator.run_command.assert_awaited_once_with("brew", [_ESPRESSO_DEFAULT], allow_destructive=True)
    # The recipe must NOT carry the @TP: prefix (the library re-adds it).
    sent = coordinator.run_command.await_args.args[1][0]
    assert not sent.startswith("@TP:")


async def test_button_press_coffee_override_vector_via_selection_path():
    """Select Coffee + strength 2 + water 130 + temp Normal -> override vector.

    Drives the full selection path through the shared coordinator: every
    entity reads/writes the same ``brew_selection``.
    """
    coordinator = _coordinator()
    entry = _entry()
    product = BrewProductSelect(coordinator, entry)
    strength = BrewStrengthSelect(coordinator, entry)
    water = BrewWaterSelect(coordinator, entry)
    temp = BrewTempSelect(coordinator, entry)
    button = JuraBrewButton(coordinator, entry)

    await product.async_select_option("Coffee")
    await strength.async_select_option("2")
    await water.async_select_option("130")
    await temp.async_select_option("Normal")
    await button.async_press()

    coordinator.run_command.assert_awaited_once_with("brew", [_COFFEE_OVERRIDE], allow_destructive=True)


# ---------------------------------------------------------------------------
# Platform setup: the five-entity control panel on a real EF1030
# ---------------------------------------------------------------------------


async def _setup(platform_module):
    from importlib import import_module

    coordinator = _coordinator()
    hass = AsyncMock()
    hass.data = {DOMAIN: {"test_entry_id": coordinator}}
    added: list = []
    module = import_module(platform_module)
    await module.async_setup_entry(hass, _entry(), added.extend)
    return added


async def test_select_setup_builds_control_panel_not_per_product():
    added = await _setup("custom_components.jura.select")
    names = [e.name for e in added]
    assert "Brew Product" in names
    assert "Brew Strength" in names
    assert "Brew Water" in names
    assert "Brew Temperature" in names
    # Setting selects are still present...
    assert any(n.startswith("Setting ") for n in names)
    # ...but there is exactly one of each brew select (no per-product explosion).
    assert sum(n.startswith("Brew ") for n in names) == 4


async def test_button_setup_creates_single_brew_button():
    added = await _setup("custom_components.jura.button")
    assert len(added) == 1
    assert added[0].name == "Brew"


async def test_number_setup_has_no_per_product_brew_water():
    added = await _setup("custom_components.jura.number")
    # Only machine-setting numbers remain (e.g. hardness); no brew water number.
    assert all(e.name.startswith("Setting ") for e in added)
    assert not any("Water" in e.name or "water" in e.name for e in added)


async def test_ef1030_creates_exactly_five_brew_entities():
    selects = await _setup("custom_components.jura.select")
    buttons = await _setup("custom_components.jura.button")
    brew_selects = [e for e in selects if e.name.startswith("Brew ")]
    assert len(brew_selects) + len(buttons) == 5
