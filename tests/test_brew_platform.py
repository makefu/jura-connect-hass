"""Tests for the compact brew "control panel".

The brew UX is a compact set of entities shared across the whole machine (not
per product): a product select, profile-backed parameter selects (each carrying
a "Factory Default" sentinel), and a single brew button.
Selections are staged on ``coordinator.brew_selection``; the button reads
them and builds the recipe via the ``jura_connect`` library. Per-product
choices persist across restarts via ``coordinator.brew_prefs``. Nothing
talks to a machine — ``run_brew`` is mocked, so no live brew happens.

These exercise the real EF1091 (S8) bundled profile so the option lists,
defaults and payload vectors are pinned against actual machine data. The
recipe payloads are computed from the library itself (not hardcoded): the
byte encoding is the library's responsibility and is covered by its own
tests — here we assert the *wiring* funnels the staged selection into
``build_recipe_hex`` and dispatches it unchanged.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock

import pytest

jura_connect = pytest.importorskip("jura_connect")

from jura_connect import (  # noqa: E402
    KIND_COFFEE_STRENGTH,
    KIND_GRINDER_RATIO,
    KIND_MILK_FOAM_AMOUNT,
    KIND_TEMPERATURE,
    KIND_WATER_AMOUNT,
    load_profile,
)

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
    BrewGrinderRatioSelect,
    BrewMilkFoamSelect,
    BrewMilkSelect,
    BrewProductSelect,
    BrewStrengthSelect,
    BrewTempSelect,
    BrewWaterSelect,
)
from homeassistant.config_entries import ConfigEntry  # noqa: E402

FACTORY_DEFAULT = "Factory Default"

_PROFILE = load_profile("EF1091")
# EF1091 product names, in profile order (the brewable product table).
_PRODUCT_NAMES = [product.name for product in _PROFILE.products]


def _recipe(code: int, overrides: dict[str, int] | None = None) -> str:
    return _PROFILE.product_by_code[code].build_recipe_hex(overrides or {})


def _entry(machine_type: str = "EF1091") -> ConfigEntry:
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
    """A real coordinator (so brew_profile/brew_selection/selected_product
    come from the live EF1091 data) with the machine-touching surfaces mocked."""
    entry = entry or _entry()
    backend = AsyncMock()
    coordinator = JuraCoordinator(AsyncMock(), entry, backend=backend)
    coordinator.run_brew = AsyncMock(return_value={"ack": "@tp", "frames": []})
    coordinator.data = None
    return coordinator


# ---------------------------------------------------------------------------
# Coordinator brew_selection seeding
# ---------------------------------------------------------------------------


def test_coordinator_seeds_first_product_and_default_params():
    coordinator = _coordinator()
    assert coordinator.brew_selection == {
        "product": "02",  # espresso is the first EF1091 product
        "strength": None,
        "water_ml": None,
        "temp": None,
        "grinder_ratio": None,
        "milk_s": None,
        "milk_foam_s": None,
    }
    assert coordinator.selected_product().name == "espresso"


# ---------------------------------------------------------------------------
# Product select
# ---------------------------------------------------------------------------


def test_product_select_options_and_current(fake_config_entry):
    coordinator = _coordinator()
    entity = BrewProductSelect(coordinator, _entry())
    assert entity.options == _PRODUCT_NAMES
    assert entity.current_option == "espresso"
    assert entity.entity_category == "config"
    assert entity.unique_id.endswith("brew_product")


async def test_product_select_sets_code_and_loads_factory_default_params():
    """With nothing saved for coffee, switching to it loads all Factory Default."""
    coordinator = _coordinator()
    # Stage some non-default params first (for the previously selected product).
    coordinator.brew_selection.update(strength=4, water_ml=120, temp=2)
    entity = BrewProductSelect(coordinator, _entry())
    await entity.async_select_option("coffee")
    assert coordinator.brew_selection["product"] == "03"
    assert coordinator.brew_selection["strength"] is None
    assert coordinator.brew_selection["water_ml"] is None
    assert coordinator.brew_selection["temp"] is None
    assert entity.current_option == "coffee"


async def test_product_select_loads_saved_prefs_into_param_selects():
    """A product with saved prefs hydrates the param selects when chosen."""
    coordinator = _coordinator()
    coordinator.brew_prefs["03"] = {"strength": 2, "water_ml": 130, "temp": 1}
    entry = _entry()
    product = BrewProductSelect(coordinator, entry)
    strength = BrewStrengthSelect(coordinator, entry)
    water = BrewWaterSelect(coordinator, entry)
    temp = BrewTempSelect(coordinator, entry)

    await product.async_select_option("coffee")

    assert coordinator.brew_selection == {
        "product": "03",
        "strength": 2,
        "water_ml": 130,
        "temp": 1,
        "grinder_ratio": None,
        "milk_s": None,
        "milk_foam_s": None,
    }
    assert strength.current_option == "2"
    assert water.current_option == "130"
    assert temp.current_option == "normal"


async def test_param_select_remembers_and_persists_across_restart():
    """Net effect: set coffee water once, it survives a coordinator restart.

    Drives the real select entities (which call set_brew_param +
    save_brew_prefs), then rebuilds the coordinator and reloads from the
    (stubbed) Store to prove the value round-trips to "disk".
    """
    entry = _entry()
    coordinator = _coordinator(entry)
    await coordinator.async_load_brew_prefs()
    product = BrewProductSelect(coordinator, entry)
    water = BrewWaterSelect(coordinator, entry)

    await product.async_select_option("coffee")
    await water.async_select_option("130")
    assert coordinator.brew_prefs["03"]["water_ml"] == 130

    # Restart: a fresh coordinator + reload reproduces the saved pref.
    restarted = _coordinator(entry)
    await restarted.async_load_brew_prefs()
    assert restarted.brew_prefs["03"]["water_ml"] == 130
    restarted.select_brew_product("03")
    assert restarted.brew_selection["water_ml"] == 130


async def test_param_select_factory_default_persists_none():
    """Selecting Factory Default records None (and persists it)."""
    entry = _entry()
    coordinator = _coordinator(entry)
    await coordinator.async_load_brew_prefs()
    product = BrewProductSelect(coordinator, entry)
    water = BrewWaterSelect(coordinator, entry)

    await product.async_select_option("coffee")
    await water.async_select_option("130")
    await water.async_select_option(FACTORY_DEFAULT)

    assert coordinator.brew_prefs["03"]["water_ml"] is None
    restarted = _coordinator(entry)
    await restarted.async_load_brew_prefs()
    assert restarted.brew_prefs.get("03", {}).get("water_ml") is None


# ---------------------------------------------------------------------------
# Strength select
# ---------------------------------------------------------------------------


def test_strength_select_options_and_default():
    coordinator = _coordinator()  # espresso selected
    entity = BrewStrengthSelect(coordinator, _entry())
    assert entity.options == [FACTORY_DEFAULT, "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
    assert entity.current_option == FACTORY_DEFAULT
    assert entity.available is True
    assert entity.entity_category == "config"
    assert entity.unique_id.endswith("brew_strength")


async def test_strength_select_set_and_factory_default():
    coordinator = _coordinator()
    entity = BrewStrengthSelect(coordinator, _entry())
    await entity.async_select_option("2")
    assert coordinator.brew_selection["strength"] == 2
    assert entity.current_option == "2"
    await entity.async_select_option(FACTORY_DEFAULT)
    assert coordinator.brew_selection["strength"] is None
    assert entity.current_option == FACTORY_DEFAULT


async def test_strength_select_unavailable_without_strength_param():
    coordinator = _coordinator()
    product = BrewProductSelect(coordinator, _entry())
    strength = BrewStrengthSelect(coordinator, _entry())
    # hotwater_portion has no COFFEE_STRENGTH parameter.
    await product.async_select_option("hotwater_portion")
    assert strength.available is False
    assert strength.current_option is None
    assert strength.options == [FACTORY_DEFAULT]


# ---------------------------------------------------------------------------
# Water select
# ---------------------------------------------------------------------------


def test_water_select_options_from_range():
    coordinator = _coordinator()  # espresso: 15..80 step 5
    entity = BrewWaterSelect(coordinator, _entry())
    assert entity.options == [FACTORY_DEFAULT, *[str(v) for v in range(15, 81, 5)]]
    assert entity.current_option == FACTORY_DEFAULT
    assert entity.unique_id.endswith("brew_water_ml")


async def test_water_select_set_and_factory_default():
    coordinator = _coordinator()
    product = BrewProductSelect(coordinator, _entry())
    water = BrewWaterSelect(coordinator, _entry())
    await product.async_select_option("coffee")  # 25..240 step 5
    assert "130" in water.options
    await water.async_select_option("130")
    assert coordinator.brew_selection["water_ml"] == 130
    assert water.current_option == "130"
    await water.async_select_option(FACTORY_DEFAULT)
    assert coordinator.brew_selection["water_ml"] is None
    assert water.current_option == FACTORY_DEFAULT


# ---------------------------------------------------------------------------
# Temperature select
# ---------------------------------------------------------------------------


def test_temp_select_options_and_mapping():
    coordinator = _coordinator()
    entity = BrewTempSelect(coordinator, _entry())
    assert entity.options == [FACTORY_DEFAULT, "low", "normal", "high"]
    assert entity.current_option == FACTORY_DEFAULT
    assert entity.unique_id.endswith("brew_temp")


async def test_temp_select_set_and_factory_default():
    coordinator = _coordinator()
    entity = BrewTempSelect(coordinator, _entry())
    await entity.async_select_option("normal")
    assert coordinator.brew_selection["temp"] == 1
    assert entity.current_option == "normal"
    await entity.async_select_option(FACTORY_DEFAULT)
    assert coordinator.brew_selection["temp"] is None


# ---------------------------------------------------------------------------
# Twin-grinder ratio
# ---------------------------------------------------------------------------


def test_grinder_ratio_select_uses_ef566_profile_items():
    entry = _entry("EF566")
    coordinator = _coordinator(entry)
    entity = BrewGrinderRatioSelect(coordinator, entry)

    assert entity.options == [
        FACTORY_DEFAULT,
        "100_0",
        "75_25",
        "50_50",
        "25_75",
        "0_100",
    ]
    assert entity.current_option == FACTORY_DEFAULT
    assert entity.available is True
    assert entity.unique_id.endswith("brew_grinder_ratio")
    assert entity._attr_translation_key == "brew_grinder_ratio"


async def test_grinder_ratio_is_staged_persisted_and_sent_as_f2_override():
    entry = _entry("EF566")
    coordinator = _coordinator(entry)
    grinder = BrewGrinderRatioSelect(coordinator, entry)
    button = JuraBrewButton(coordinator, entry)

    await grinder.async_select_option("100_0")
    assert coordinator.brew_selection["grinder_ratio"] == 0
    assert coordinator.brew_prefs["02"]["grinder_ratio"] == 0
    await button.async_press()

    espresso = load_profile("EF566").product_by_code[0x02]
    expected = espresso.build_recipe_hex({KIND_GRINDER_RATIO: "100_0"})
    coordinator.run_brew.assert_awaited_once_with(expected)


async def test_grinder_ratio_is_unavailable_for_product_without_f2():
    entry = _entry("EF566")
    coordinator = _coordinator(entry)
    product = BrewProductSelect(coordinator, entry)
    grinder = BrewGrinderRatioSelect(coordinator, entry)

    await product.async_select_option("hotwater_portion")
    assert grinder.available is False
    assert grinder.current_option is None
    assert grinder.options == [FACTORY_DEFAULT]


# ---------------------------------------------------------------------------
# Milk / milk-foam selects
# ---------------------------------------------------------------------------


async def test_milk_foam_select_options_from_range():
    coordinator = _coordinator()
    entry = _entry()
    product = BrewProductSelect(coordinator, entry)
    foam = BrewMilkFoamSelect(coordinator, entry)
    # espresso (the seeded product) has no milk-foam parameter.
    assert foam.available is False
    assert foam.options == [FACTORY_DEFAULT]
    # cappuccino: 1..45 s step 1.
    await product.async_select_option("cappuccino")
    assert foam.available is True
    assert foam.options == [FACTORY_DEFAULT, *[str(v) for v in range(1, 46)]]
    assert foam.current_option == FACTORY_DEFAULT
    assert foam.unique_id.endswith("brew_milk_foam_s")


async def test_milk_selects_gate_per_product():
    """Milk vs foam availability tracks what the staged product exposes.

    On the EF1091, cappuccino has only a foam time and the plain milk
    product has only a milk time — each select must be available exactly
    where its parameter exists.
    """
    coordinator = _coordinator()
    entry = _entry()
    product = BrewProductSelect(coordinator, entry)
    milk = BrewMilkSelect(coordinator, entry)
    foam = BrewMilkFoamSelect(coordinator, entry)

    await product.async_select_option("cappuccino")
    assert milk.available is False
    assert foam.available is True

    await product.async_select_option("milk")
    assert milk.available is True
    assert foam.available is False
    assert milk.options == [FACTORY_DEFAULT, *[str(v) for v in range(1, 46)]]


async def test_milk_foam_set_and_factory_default():
    coordinator = _coordinator()
    entry = _entry()
    product = BrewProductSelect(coordinator, entry)
    foam = BrewMilkFoamSelect(coordinator, entry)
    await product.async_select_option("cappuccino")
    await foam.async_select_option("12")
    assert coordinator.brew_selection["milk_foam_s"] == 12
    assert foam.current_option == "12"
    await foam.async_select_option(FACTORY_DEFAULT)
    assert coordinator.brew_selection["milk_foam_s"] is None
    assert foam.current_option == FACTORY_DEFAULT


# ---------------------------------------------------------------------------
# Brew button
# ---------------------------------------------------------------------------


def test_button_name_and_unique_id():
    coordinator = _coordinator()
    button = JuraBrewButton(coordinator, _entry())
    assert button.name == "Brew"
    assert button.unique_id.endswith("homeassistant_test_brew")


def test_button_unavailable_while_product_blocked(sample_snapshot):
    """A machine-declared blocking alert for the staged product's kind
    makes the brew button unavailable instead of erroring on press."""
    coordinator = _coordinator()  # espresso (kind C) selected
    button = JuraBrewButton(coordinator, _entry())
    coordinator.data = sample_snapshot
    assert button.available is True
    coordinator.data = dataclasses.replace(sample_snapshot, blocked_products=("espresso",))
    assert button.available is False
    coordinator.data = dataclasses.replace(sample_snapshot, blocked_products=("latte_macchiato",))
    assert button.available is True


async def test_button_press_espresso_factory_default_vector():
    """espresso, all Factory Default -> the library's default recipe blob."""
    coordinator = _coordinator()  # espresso selected, all params None
    button = JuraBrewButton(coordinator, _entry())
    await button.async_press()
    expected = _recipe(0x02)
    coordinator.run_brew.assert_awaited_once_with(expected)
    # The recipe must NOT carry the @TP: prefix (the backend adds it).
    sent = coordinator.run_brew.await_args.args[0]
    assert not sent.startswith("@TP:")


async def test_button_press_coffee_override_vector_via_selection_path():
    """Select coffee + strength 2 + water 130 + temp normal -> override recipe.

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

    await product.async_select_option("coffee")
    await strength.async_select_option("2")
    await water.async_select_option("130")
    await temp.async_select_option("normal")
    await button.async_press()

    expected = _recipe(
        0x03,
        {KIND_COFFEE_STRENGTH: 2, KIND_WATER_AMOUNT: 130, KIND_TEMPERATURE: 1},
    )
    coordinator.run_brew.assert_awaited_once_with(expected)


async def test_button_press_cappuccino_milk_foam_override_vector():
    """Select cappuccino + foam 12 s -> the F6 override lands in the recipe."""
    coordinator = _coordinator()
    entry = _entry()
    product = BrewProductSelect(coordinator, entry)
    foam = BrewMilkFoamSelect(coordinator, entry)
    button = JuraBrewButton(coordinator, entry)

    await product.async_select_option("cappuccino")
    await foam.async_select_option("12")
    await button.async_press()

    expected = _recipe(0x04, {KIND_MILK_FOAM_AMOUNT: 12})
    coordinator.run_brew.assert_awaited_once_with(expected)


# ---------------------------------------------------------------------------
# Platform setup: the seven-entity control panel on a real EF1091
# ---------------------------------------------------------------------------


async def _setup(platform_module, machine_type: str = "EF1091"):
    from importlib import import_module

    entry = _entry(machine_type)
    coordinator = _coordinator(entry)
    hass = AsyncMock()
    hass.data = {DOMAIN: {"test_entry_id": coordinator}}
    added: list = []
    module = import_module(platform_module)
    await module.async_setup_entry(hass, entry, added.extend)
    return added


def _is_brew_select(entity) -> bool:
    return "_brew_" in (entity.unique_id or "")


def _is_setting_select(entity) -> bool:
    return "_setting_" in (entity.unique_id or "")


async def test_select_setup_builds_control_panel_not_per_product():
    added = await _setup("custom_components.jura.select")
    brew = [e for e in added if _is_brew_select(e)]
    brew_names = {e.name for e in brew}
    assert brew_names == {
        "Brew Product",
        "Brew Strength",
        "Brew Water",
        "Brew Temperature",
        "Brew Milk",
        "Brew Milk Foam",
    }
    # Setting selects are still present...
    assert any(_is_setting_select(e) for e in added)
    # ...but there is exactly one of each brew select (no per-product explosion).
    assert len(brew) == 6


async def test_grinder_entity_is_created_only_for_a_declaring_profile():
    single = await _setup("custom_components.jura.select", "EF1091")
    twin = await _setup("custom_components.jura.select", "EF566")

    assert not any(e.unique_id.endswith("brew_grinder_ratio") for e in single)
    assert sum(e.unique_id.endswith("brew_grinder_ratio") for e in twin) == 1


async def test_button_setup_creates_single_brew_button():
    added = await _setup("custom_components.jura.button")
    assert len(added) == 1
    assert added[0].name == "Brew"


async def test_number_setup_has_no_per_product_brew_water():
    added = await _setup("custom_components.jura.number")
    # Only machine-setting numbers remain (e.g. hardness); no brew water number.
    assert all("_setting_" in (e.unique_id or "") for e in added)
    assert not any("brew" in (e.unique_id or "") for e in added)


async def test_ef1091_creates_exactly_seven_brew_entities():
    selects = await _setup("custom_components.jura.select")
    buttons = await _setup("custom_components.jura.button")
    brew_selects = [e for e in selects if _is_brew_select(e)]
    assert len(brew_selects) + len(buttons) == 7
