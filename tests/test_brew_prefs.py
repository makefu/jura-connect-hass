"""Pure (framework-free) per-product brew-preference helpers.

A *brew preference* is the remembered strength / water / temperature for one
product, keyed by its hex Code. ``None`` for a parameter means Factory Default
(send the product's XML default; pass ``None`` to ``build_start_command``).
These helpers only touch plain dicts, so no Home Assistant / Store is needed.
"""

from __future__ import annotations

from custom_components.jura.brew import (
    BREW_PARAMS,
    product_prefs,
    remember_param,
    selection_for_product,
)


def test_brew_params_are_the_three_recipe_axes():
    assert BREW_PARAMS == ("strength", "water_ml", "temp")


def test_product_prefs_missing_product_is_all_factory_default():
    assert product_prefs({}, "03") == {"strength": None, "water_ml": None, "temp": None}


def test_product_prefs_fills_missing_params_with_none():
    prefs = {"03": {"water_ml": 130}}
    assert product_prefs(prefs, "03") == {"strength": None, "water_ml": 130, "temp": None}


def test_remember_then_load_reproduces_value():
    """The core persistence contract: store a pref, read it back unchanged."""
    prefs: dict[str, dict] = {}
    remember_param(prefs, "03", "water_ml", 130)
    assert product_prefs(prefs, "03")["water_ml"] == 130


def test_remember_none_persists_factory_default_explicitly():
    prefs: dict[str, dict] = {"03": {"water_ml": 130}}
    remember_param(prefs, "03", "water_ml", None)
    assert prefs["03"]["water_ml"] is None
    assert product_prefs(prefs, "03")["water_ml"] is None


def test_remember_is_per_product_isolated():
    prefs: dict[str, dict] = {}
    remember_param(prefs, "03", "water_ml", 130)
    remember_param(prefs, "02", "water_ml", 45)
    assert product_prefs(prefs, "03")["water_ml"] == 130
    assert product_prefs(prefs, "02")["water_ml"] == 45


def test_selection_for_product_includes_product_code():
    prefs = {"03": {"strength": 2, "water_ml": 130, "temp": 1}}
    assert selection_for_product(prefs, "03") == {
        "product": "03",
        "strength": 2,
        "water_ml": 130,
        "temp": 1,
    }
