"""Translation wiring tests.

These guard the HA-native translation setup: every entity that sets a
``translation_key`` must have a matching name in ``strings.json`` (the
English source HA renders from) and in ``translations/de.json`` (the
German mirror). Placeholder-using keys must keep their ``{...}`` tokens
in every language so HA can substitute the machine-supplied value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jura_connect import load_profile

from custom_components.jura.const import ALERT_BINARY_SENSORS, COUNTER_KEYS, PERCENT_KEYS

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "jura"


def _load(name: str) -> dict:
    return json.loads((_COMPONENT / name).read_text(encoding="utf-8"))


@pytest.fixture
def strings() -> dict:
    return _load("strings.json")


@pytest.fixture
def de() -> dict:
    return _load("translations/de.json")


@pytest.fixture
def en() -> dict:
    return _load("translations/en.json")


# Translation keys the code registers, grouped by platform. Keep this in
# lock-step with the *_attr_translation_key* values set in the platforms.
def _expected_keys() -> dict[str, set[str]]:
    sensor = {"status", "machine_type", "brew_total", "brew_counter", "brew_progress"}
    sensor |= {f"counter_{k}" for k in COUNTER_KEYS}
    sensor |= {f"percent_{k}" for k in PERCENT_KEYS}
    binary_sensor = {"connectivity", *ALERT_BINARY_SENSORS.keys()}
    return {
        "sensor": sensor,
        "binary_sensor": binary_sensor,
        "select": {
            "setting",
            "brew_product",
            "brew_strength",
            "brew_water",
            "brew_temperature",
            "brew_grinder_ratio",
            "brew_milk",
            "brew_milk_foam",
        },
        "number": {"setting"},
    }


# Keys whose name carries an HA placeholder that must survive translation.
_PLACEHOLDERS = {
    ("sensor", "brew_counter"): "{product}",
    ("select", "setting"): "{setting}",
    ("number", "setting"): "{setting}",
}


def _names(catalog: dict, platform: str) -> dict[str, str]:
    return {key: spec["name"] for key, spec in catalog["entity"][platform].items()}


@pytest.mark.parametrize("catalog_name", ["strings.json", "translations/en.json", "translations/de.json"])
def test_every_translation_key_has_a_name(catalog_name):
    catalog = _load(catalog_name)
    for platform, keys in _expected_keys().items():
        names = _names(catalog, platform)
        missing = keys - names.keys()
        assert not missing, f"{catalog_name}: {platform} missing names for {sorted(missing)}"


@pytest.mark.parametrize("catalog_name", ["strings.json", "translations/en.json", "translations/de.json"])
def test_no_orphan_entity_names(catalog_name):
    """No translation entry without a matching entity in code (dead keys)."""
    catalog = _load(catalog_name)
    expected = _expected_keys()
    for platform, keys in expected.items():
        names = set(_names(catalog, platform))
        orphans = names - keys
        assert not orphans, f"{catalog_name}: {platform} has unused keys {sorted(orphans)}"


@pytest.mark.parametrize("catalog_name", ["strings.json", "translations/en.json", "translations/de.json"])
def test_placeholders_preserved(catalog_name):
    catalog = _load(catalog_name)
    for (platform, key), token in _PLACEHOLDERS.items():
        name = _names(catalog, platform)[key]
        assert token in name, f"{catalog_name}: {platform}.{key} dropped placeholder {token}"


def test_german_actually_translates(strings, de):
    """German names must differ from English where a real translation exists.

    A handful (e.g. Status) are legitimately identical; assert the bulk of
    alert names and the maintainer-reviewed strings are genuinely German.
    """
    en_alerts = _names(strings, "binary_sensor")
    de_alerts = _names(de, "binary_sensor")
    translated = [k for k in en_alerts if en_alerts[k] != de_alerts[k]]
    # The vast majority of alerts have a distinct German rendering.
    assert len(translated) >= 35

    # Spot-check the maintainer's specific corrections landed.
    assert _names(de, "sensor")["brew_total"] == "Bezüge gesamt"
    assert de_alerts["press_rinse"] == "Spültaste drücken"
    assert de_alerts["fill_water"] == "Wasser nachfüllen"
    grinder = de["entity"]["select"]["brew_grinder_ratio"]
    assert grinder["name"] == "Mahlwerkverhältnis"
    assert grinder["state"]["factory_default"] == "Standard"
    assert grinder["state"]["100_0"] == "100 % links : 0 % rechts"
    assert grinder["state"]["0_100"] == "0 % links : 100 % rechts"

    product = de["entity"]["select"]["brew_product"]
    assert product["name"] == "Getränk"
    assert product["state"]["coffee"] == "Kaffee"
    assert product["state"]["hotwater_portion"] == "Heisswasser"

    temperature = de["entity"]["select"]["brew_temperature"]
    assert temperature["name"] == "Temperatur"
    assert temperature["state"]["factory_default"] == "Standard"
    assert temperature["state"]["low"] == "Niedrig"
    assert temperature["state"]["high"] == "Hoch"


@pytest.mark.parametrize("catalog_name", ["strings.json", "translations/en.json", "translations/de.json"])
def test_ef566_product_options_have_translations(catalog_name):
    """Every product exposed by the tested twin-grinder profile is translated."""
    catalog = _load(catalog_name)
    translated = catalog["entity"]["select"]["brew_product"]["state"]
    profile = load_profile("EF566")
    missing = {product.name for product in profile.products} - translated.keys()
    assert not missing, f"{catalog_name}: EF566 products missing translations {sorted(missing)}"


def test_strings_and_en_mirror_match(strings, en):
    assert strings["entity"] == en["entity"]
