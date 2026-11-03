"""Dashboard plumbing: bundled cards are served + auto-loaded, websocket API."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

jura_connect = pytest.importorskip("jura_connect")

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from jura_connect import load_profile

from custom_components.jura import lovelace
from custom_components.jura.const import CONF_CONN_ID, DOMAIN

_PROFILE = load_profile("EF1091")


async def _executor(func, *args):
    return func(*args)


def _hass() -> MagicMock:
    hass = MagicMock()
    hass.data = {}
    hass.async_add_executor_job = _executor
    hass.http.async_register_static_paths = AsyncMock()
    return hass


def test_bundled_cards_ship_inside_the_component():
    """HACS installs only custom_components/jura -- the cards must live there."""
    for name in lovelace.CARD_FILES:
        assert (lovelace.WWW_DIR / name).is_file(), name


async def test_setup_serves_and_autoloads_every_card():
    hass = _hass()
    await lovelace.async_setup_lovelace(hass)

    (configs,), _ = hass.http.async_register_static_paths.await_args
    served = {cfg.url_path: cfg.path for cfg in configs}
    assert set(served) == {f"/jura_connect/{name}" for name in lovelace.CARD_FILES}
    for url, path in served.items():
        assert path.endswith(url.rsplit("/", 1)[1])

    extra = hass.data["frontend_extra_module_url"]
    assert len(extra) == len(lovelace.CARD_FILES)
    # Content hash for cache busting: HA serves static files with long caching.
    for url in extra:
        base, _, version = url.partition("?v=")
        assert base in served
        assert len(version) == 8

    assert "jura/machines" in hass.data["websocket_api"]


async def test_setup_runs_once_for_several_machines():
    hass = _hass()
    await lovelace.async_setup_lovelace(hass)
    await lovelace.async_setup_lovelace(hass)
    assert hass.http.async_register_static_paths.await_count == 1
    assert len(hass.data["frontend_extra_module_url"]) == len(lovelace.CARD_FILES)


async def test_setup_failure_does_not_break_the_integration():
    hass = _hass()
    hass.http.async_register_static_paths = AsyncMock(side_effect=RuntimeError("boom"))
    await lovelace.async_setup_lovelace(hass)  # must not raise
    assert "frontend_extra_module_url" not in hass.data


def test_products_payload_maps_params_to_brew_service_fields():
    products = lovelace.products_payload(_PROFILE)
    assert [p["name"] for p in products] == [p.name for p in _PROFILE.products]

    espresso = next(p for p in products if p["name"] == "espresso")
    params = {param["field"]: param for param in espresso["params"]}
    assert set(params) == {"strength", "water_ml", "temperature"}

    # Ranged parameter: min/max/step straight from the profile.
    assert params["water_ml"] == {
        "field": "water_ml",
        "kind": "water_amount",
        "default": 45,
        "min": 15,
        "max": 80,
        "step": 5,
    }
    # Item parameter: hex catalogue values become the integers jura.brew takes.
    temps = params["temperature"]["items"]
    assert temps == [{"name": "low", "value": 0}, {"name": "normal", "value": 1}, {"name": "high", "value": 2}]
    assert params["strength"]["items"][-1] == {"name": "10", "value": 10}


def test_products_payload_round_trips_through_the_brew_recipe_builder():
    """Every value the editor can offer must be accepted by build_recipe_hex."""
    products = {p.name: p for p in _PROFILE.products}
    kinds = {field: kind for kind, field in lovelace.PARAM_FIELDS.items()}
    for product in lovelace.products_payload(_PROFILE):
        for param in product["params"]:
            values = (
                [item["value"] for item in param["items"]]
                if "items" in param
                else list(range(param["min"], param["max"] + 1, param["step"]))
            )
            for value in (values[0], values[-1]):
                products[product["name"]].build_recipe_hex({kinds[param["field"]]: value})


def test_products_payload_without_profile_is_empty():
    assert lovelace.products_payload(None) == []


def test_ws_machines_lists_machines_with_device_and_entities():
    hass = MagicMock()
    entry = SimpleNamespace(entry_id="e1", title="Kaffeebert", data={CONF_CONN_ID: "Abc-123"})
    hass.data = {DOMAIN: {"e1": SimpleNamespace(config_entry=entry, brew_profile=_PROFILE)}}

    dr.async_get(hass).add({(DOMAIN, "e1")}, "dev-1")
    registry = er.async_get(hass)
    registry.add("sensor.kaffeebert_status", "e1", unique_id=f"{DOMAIN}_abc_123_state")
    registry.add("binary_sensor.kaffeebert_connectivity", "e1", unique_id=f"{DOMAIN}_abc_123_connectivity")
    registry.add("sensor.kaffeebert_brew_progress", "e1", unique_id=f"{DOMAIN}_abc_123_brew_progress")

    connection = MagicMock()
    lovelace.ws_machines(hass, connection, {"id": 7, "type": "jura/machines"})

    msg_id, result = connection.send_result.call_args.args
    assert msg_id == 7
    (machine,) = result["machines"]
    assert machine["entry_id"] == "e1"
    assert machine["title"] == "Kaffeebert"
    assert machine["device_id"] == "dev-1"
    assert machine["entities"] == {
        "status": "sensor.kaffeebert_status",
        "connectivity": "binary_sensor.kaffeebert_connectivity",
        "progress": "sensor.kaffeebert_brew_progress",
    }
    assert machine["products"] == lovelace.products_payload(_PROFILE)


def test_ws_machines_without_machines():
    hass = MagicMock()
    hass.data = {}
    connection = MagicMock()
    lovelace.ws_machines(hass, connection, {"id": 1, "type": "jura/machines"})
    connection.send_result.assert_called_once_with(1, {"machines": []})
