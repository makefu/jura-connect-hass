"""Tests for the brew button + per-product brew-parameter entities.

The parameter entities (strength/temperature selects, water number) stage
the "next brew" values on the coordinator; the brew button reads them and
encodes the ``@TP:`` start command. None of this talks to a machine — the
button's ``run_command`` is mocked, so no live brew happens in tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jura.brew import ProductArg, ProductDef
from custom_components.jura.button import JuraBrewButton
from custom_components.jura.const import DOMAIN
from custom_components.jura.number import BrewWaterNumber
from custom_components.jura.select import BrewParamSelect

# Default payload for the product below (validated shape):
#   code 30, strength 8 (idx2=08), water 90//5=18=0x12 (idx3=12),
#   temp 2 (idx6=02), idx8=01 -> "30000812000002000100000000000000"
_DEFAULT_RECIPE = "30000812000002000100000000000000"
# After staging strength 2, water 130 (->0x1A), temp Normal(1):
_STAGED_RECIPE = "3000021A000001000100000000000000"


def _product() -> ProductDef:
    return ProductDef(
        code="30",
        name="Espresso Doppio",
        picture=None,
        pos_csv=17,
        args=(
            ProductArg(
                kind="COFFEE_STRENGTH",
                argument="F3",
                index=2,
                default=8,
                items=tuple((str(i), i) for i in range(1, 11)),
            ),
            ProductArg(kind="WATER_AMOUNT", argument="F4", index=3, default=90, min=30, max=160, step=5),
            ProductArg(
                kind="TEMPERATURE",
                argument="F7",
                index=6,
                default=2,
                items=(("Normal", 1), ("High", 2)),
            ),
        ),
    )


def _coordinator() -> MagicMock:
    c = MagicMock()
    c.brew_params = {}
    c.run_command = AsyncMock(return_value={"name": "brew", "value": "ok"})
    c.data = None
    return c


# ---------------------------------------------------------------------------
# Strength select
# ---------------------------------------------------------------------------


def test_strength_select_options_and_seeded_default(fake_config_entry):
    coordinator = _coordinator()
    product = _product()
    entity = BrewParamSelect(coordinator, fake_config_entry, product, product.arg("COFFEE_STRENGTH"))
    assert entity.options == [str(i) for i in range(1, 11)]
    assert entity.current_option == "8"  # default byte 8 -> item name "8"
    assert coordinator.brew_params["30"]["strength"] == 8
    assert entity.entity_category == "config"
    assert entity.unique_id.endswith("brew_30_strength")


async def test_strength_select_set_stages_value(fake_config_entry):
    coordinator = _coordinator()
    product = _product()
    entity = BrewParamSelect(coordinator, fake_config_entry, product, product.arg("COFFEE_STRENGTH"))
    await entity.async_select_option("2")
    assert coordinator.brew_params["30"]["strength"] == 2
    assert entity.current_option == "2"


# ---------------------------------------------------------------------------
# Temperature select
# ---------------------------------------------------------------------------


def test_temperature_select_options_and_default(fake_config_entry):
    coordinator = _coordinator()
    product = _product()
    entity = BrewParamSelect(coordinator, fake_config_entry, product, product.arg("TEMPERATURE"))
    assert entity.options == ["Normal", "High"]
    assert entity.current_option == "High"  # default byte 2
    assert coordinator.brew_params["30"]["temp"] == 2


async def test_temperature_select_set_stages_value(fake_config_entry):
    coordinator = _coordinator()
    product = _product()
    entity = BrewParamSelect(coordinator, fake_config_entry, product, product.arg("TEMPERATURE"))
    await entity.async_select_option("Normal")
    assert coordinator.brew_params["30"]["temp"] == 1


# ---------------------------------------------------------------------------
# Water number
# ---------------------------------------------------------------------------


def test_water_number_range_and_default(fake_config_entry):
    coordinator = _coordinator()
    product = _product()
    entity = BrewWaterNumber(coordinator, fake_config_entry, product, product.arg("WATER_AMOUNT"))
    assert entity.native_min_value == 30.0
    assert entity.native_max_value == 160.0
    assert entity.native_step == 5.0
    assert entity.native_value == 90.0
    assert entity.entity_category == "config"
    assert coordinator.brew_params["30"]["water_ml"] == 90


async def test_water_number_set_stages_value(fake_config_entry):
    coordinator = _coordinator()
    product = _product()
    entity = BrewWaterNumber(coordinator, fake_config_entry, product, product.arg("WATER_AMOUNT"))
    await entity.async_set_native_value(130)
    assert coordinator.brew_params["30"]["water_ml"] == 130
    assert entity.native_value == 130.0


# ---------------------------------------------------------------------------
# Brew button
# ---------------------------------------------------------------------------


def test_button_name_and_unique_id(fake_config_entry):
    coordinator = _coordinator()
    product = _product()
    button = JuraBrewButton(coordinator, fake_config_entry, product)
    assert button.name == "Brew Espresso Doppio"
    assert button.unique_id.endswith("brew_30")


async def test_button_press_uses_xml_defaults_when_unstaged(fake_config_entry):
    coordinator = _coordinator()
    product = _product()
    button = JuraBrewButton(coordinator, fake_config_entry, product)
    await button.async_press()
    coordinator.run_command.assert_awaited_once_with("brew", [_DEFAULT_RECIPE], allow_destructive=True)
    # The recipe must NOT carry the @TP: prefix (the library re-adds it).
    sent = coordinator.run_command.await_args.args[1][0]
    assert not sent.startswith("@TP:")


async def test_button_press_reads_staged_param_entities(fake_config_entry):
    coordinator = _coordinator()
    product = _product()
    # Build the param entities so they seed + accept staged values, sharing
    # the same coordinator the button reads from.
    strength = BrewParamSelect(coordinator, fake_config_entry, product, product.arg("COFFEE_STRENGTH"))
    water = BrewWaterNumber(coordinator, fake_config_entry, product, product.arg("WATER_AMOUNT"))
    temperature = BrewParamSelect(coordinator, fake_config_entry, product, product.arg("TEMPERATURE"))
    await strength.async_select_option("2")
    await water.async_set_native_value(130)
    await temperature.async_select_option("Normal")

    button = JuraBrewButton(coordinator, fake_config_entry, product)
    await button.async_press()
    coordinator.run_command.assert_awaited_once_with("brew", [_STAGED_RECIPE], allow_destructive=True)


# ---------------------------------------------------------------------------
# Platform setup against the real EF1091 (S8) bundled definition
# ---------------------------------------------------------------------------


async def test_button_setup_creates_one_button_per_product(fake_config_entry):
    pytest.importorskip("jura_connect")
    from custom_components.jura.button import async_setup_entry

    coordinator = _coordinator()
    hass = MagicMock()
    hass.data = {DOMAIN: {fake_config_entry.entry_id: coordinator}}
    added: list = []
    await async_setup_entry(hass, fake_config_entry, added.extend)
    assert added
    assert all(e.name.startswith("Brew ") for e in added)


async def test_select_setup_includes_brew_param_selects(fake_config_entry):
    pytest.importorskip("jura_connect")
    from custom_components.jura.select import async_setup_entry

    coordinator = _coordinator()
    hass = MagicMock()
    hass.data = {DOMAIN: {fake_config_entry.entry_id: coordinator}}
    added: list = []
    await async_setup_entry(hass, fake_config_entry, added.extend)
    names = {e.name for e in added}
    assert any("strength" in n for n in names)
    assert any("temperature" in n for n in names)


async def test_number_setup_includes_brew_water_number(fake_config_entry):
    pytest.importorskip("jura_connect")
    from custom_components.jura.number import async_setup_entry

    coordinator = _coordinator()
    hass = MagicMock()
    hass.data = {DOMAIN: {fake_config_entry.entry_id: coordinator}}
    added: list = []
    await async_setup_entry(hass, fake_config_entry, added.extend)
    assert any("water" in e.name for e in added)
