"""Tests for the button platform: one brew button per profile product."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

jura_connect = pytest.importorskip("jura_connect")
from jura_connect import load_profile  # noqa: E402

from custom_components.jura.button import ProductButtonEntity, _brewable_products  # noqa: E402


@pytest.fixture
def ef538():
    """The E8 (EB) profile — 15 products, including hot water."""
    return load_profile("EF538")


def _coordinator(run_command=None):
    c = MagicMock()
    c.data = MagicMock()
    c.last_update_success = True
    c.run_command = run_command or AsyncMock(return_value={"name": "brew", "value": "@tp"})
    return c


def test_brewable_products_covers_the_profile(ef538):
    names = {p.name for p in _brewable_products(ef538)}
    assert "espresso" in names
    assert "hotwater_portion_normal" in names
    assert "cappuccino" in names
    # The full EF538 catalogue is exposed — nothing silently dropped.
    assert len(names) == len(ef538.products)


def test_button_entity_naming_and_unique_id(ef538, fake_config_entry):
    espresso = ef538.product_by_code[0x02]
    entity = ProductButtonEntity(_coordinator(), fake_config_entry, espresso)
    assert entity.name == "Brew espresso"
    assert entity.unique_id.endswith("brew_espresso")


async def test_button_press_runs_brew_with_product_name(ef538, fake_config_entry):
    hot_water = ef538.product_by_code[0x0D]
    run = AsyncMock(return_value={"name": "brew", "value": "@tp"})
    entity = ProductButtonEntity(_coordinator(run_command=run), fake_config_entry, hot_water)
    await entity.async_press()
    run.assert_awaited_once_with("brew", ["hotwater_portion_normal"], allow_destructive=True)


def test_buttons_have_distinct_unique_ids(ef538, fake_config_entry):
    coordinator = _coordinator()
    ids = [ProductButtonEntity(coordinator, fake_config_entry, p).unique_id for p in _brewable_products(ef538)]
    assert len(ids) == len(set(ids))
