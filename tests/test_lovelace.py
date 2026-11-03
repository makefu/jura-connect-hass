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
from custom_components.jura.const import CONF_CONN_ID, CONF_MACHINE_TYPE, DOMAIN

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


class FakeResources:
    """Minimal ResourceStorageCollection (Lovelace resources, storage mode)."""

    def __init__(self, items=None, loaded=False):
        self.items = {item["id"]: dict(item) for item in items or []}
        self.loaded = loaded
        self.load_calls = 0
        self._next = 100

    async def async_load(self):
        self.load_calls += 1

    def async_items(self):
        return list(self.items.values())

    async def async_create_item(self, data):
        self._next += 1
        item = {"id": str(self._next), "type": data["res_type"], "url": data["url"]}
        self.items[item["id"]] = item
        return item

    async def async_update_item(self, item_id, data):
        self.items[item_id].update({"type": data["res_type"], "url": data["url"]})

    async def async_delete_item(self, item_id):
        del self.items[item_id]


def _served(hass) -> dict[str, str]:
    (configs,), _ = hass.http.async_register_static_paths.await_args
    return {cfg.url_path: cfg.path for cfg in configs}


async def test_setup_serves_every_card_with_cache_headers():
    hass = _hass()
    await lovelace.async_setup_lovelace(hass)
    (configs,), _ = hass.http.async_register_static_paths.await_args
    assert {cfg.url_path for cfg in configs} == {f"/jura_connect/{name}" for name in lovelace.CARD_FILES}
    for cfg in configs:
        assert cfg.path.endswith(cfg.url_path.rsplit("/", 1)[1])
        assert cfg.cache_headers is True
    assert "jura/machines" in hass.data["websocket_api"]


async def test_cards_become_lovelace_resources_in_storage_mode():
    """Regression (Shelly Wall Display): extra script tags are loaded without
    the frontend waiting for them, so slow devices built the dashboard first
    and showed "Custom element doesn't exist". Resources are loaded first."""
    hass = _hass()
    resources = FakeResources()
    hass.data["lovelace"] = SimpleNamespace(resource_mode="storage", resources=resources)
    await lovelace.async_setup_lovelace(hass)

    assert resources.load_calls == 1
    items = resources.async_items()
    assert {item["url"].split("?")[0] for item in items} == set(_served(hass))
    for item in items:
        assert item["type"] == "module"
        version = item["url"].partition("?v=")[2]
        assert len(version) == 8  # content hash for cache busting
    # No second load path -> no double evaluation.
    assert "frontend_extra_module_url" not in hass.data
    assert "frontend_extra_js_url_es5" not in hass.data


async def test_resources_updated_in_place_and_duplicates_removed():
    hass = _hass()
    resources = FakeResources(
        [
            {"id": "1", "type": "module", "url": "/jura_connect/jura-coffee-button-card.js?v=old"},
            # Manual workaround entry from the 1.4.x days, plus a duplicate.
            {"id": "2", "type": "js", "url": "/jura_connect/jura-coffee-button-card.js"},
            {"id": "3", "type": "module", "url": "/local/other-card.js"},
        ],
        loaded=True,
    )
    hass.data["lovelace"] = {"mode": "storage", "resources": resources}  # HA < 2025.2 layout
    await lovelace.async_setup_lovelace(hass)

    assert resources.load_calls == 0
    urls = sorted(item["url"].split("?")[0] for item in resources.async_items())
    assert urls == [
        "/jura_connect/jura-brew-card.js",
        "/jura_connect/jura-coffee-button-card.js",
        "/local/other-card.js",
    ]
    coffee = resources.items["1"]
    assert coffee["type"] == "module" and not coffee["url"].endswith("?v=old")
    assert "2" not in resources.items
    assert resources.items["3"]["url"] == "/local/other-card.js"  # foreign entries untouched


async def test_yaml_resources_fall_back_to_extra_js_for_both_frontend_builds():
    """Legacy frontend build (older WebViews) only loads the es5 list."""
    hass = _hass()
    hass.data["lovelace"] = SimpleNamespace(resource_mode="yaml", resources=SimpleNamespace(async_items=list))
    await lovelace.async_setup_lovelace(hass)

    served = _served(hass)
    for key in ("frontend_extra_module_url", "frontend_extra_js_url_es5"):
        urls = hass.data[key]
        assert len(urls) == len(lovelace.CARD_FILES)
        assert {url.split("?v=")[0] for url in urls} == set(served)


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
    assert "frontend_extra_js_url_es5" not in hass.data


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


def _entry(entry_id="e1", title="Kaffeebert", state="loaded", machine_type="EF1091"):
    return SimpleNamespace(
        entry_id=entry_id,
        title=title,
        state=SimpleNamespace(value=state),
        data={CONF_CONN_ID: "Abc-123", CONF_MACHINE_TYPE: machine_type},
    )


def _ws_hass(entries, coordinators):
    hass = MagicMock()
    hass.data = {DOMAIN: coordinators}
    hass.async_add_executor_job = _executor
    hass.config_entries.async_entries = lambda domain: entries if domain == DOMAIN else []
    return hass


async def _ws_result(hass):
    connection = MagicMock()
    await lovelace.ws_machines(hass, connection, {"id": 7, "type": "jura/machines"})
    msg_id, result = connection.send_result.call_args.args
    assert msg_id == 7
    return result["machines"]


async def test_ws_machines_lists_machines_with_device_and_entities():
    entry = _entry()
    hass = _ws_hass([entry], {"e1": SimpleNamespace(config_entry=entry, brew_profile=_PROFILE)})

    dr.async_get(hass).add({(DOMAIN, "e1")}, "dev-1")
    registry = er.async_get(hass)
    registry.add("sensor.kaffeebert_status", "e1", unique_id=f"{DOMAIN}_abc_123_state")
    registry.add("binary_sensor.kaffeebert_connectivity", "e1", unique_id=f"{DOMAIN}_abc_123_connectivity")
    registry.add("sensor.kaffeebert_brew_progress", "e1", unique_id=f"{DOMAIN}_abc_123_brew_progress")

    (machine,) = await _ws_result(hass)
    assert machine["entry_id"] == "e1"
    assert machine["title"] == "Kaffeebert"
    assert machine["state"] == "loaded"
    assert machine["device_id"] == "dev-1"
    assert machine["entities"] == {
        "status": "sensor.kaffeebert_status",
        "connectivity": "binary_sensor.kaffeebert_connectivity",
        "progress": "sensor.kaffeebert_brew_progress",
    }
    assert machine["products"] == lovelace.products_payload(_PROFILE)


async def test_ws_machines_lists_machine_whose_setup_is_retrying():
    """Machine unreachable at HA start (setup retry): cards still get the
    machine and its drinks -- from the bundled profile, not the machine --
    plus the state, instead of "no machine found"."""
    hass = _ws_hass([_entry(state="setup_retry")], {})
    (machine,) = await _ws_result(hass)
    assert machine["state"] == "setup_retry"
    assert machine["products"] == lovelace.products_payload(_PROFILE)


async def test_ws_machines_without_machine_type_has_no_products():
    hass = _ws_hass([_entry(state="setup_error", machine_type=None)], {})
    (machine,) = await _ws_result(hass)
    assert machine["products"] == []


async def test_ws_machines_without_machines():
    assert await _ws_result(_ws_hass([], {})) == []
