"""Dashboard (Lovelace) support: bundled cards + a small websocket API.

The integration ships its dashboard cards inside the component directory
(``custom_components/jura/www/``) and registers them itself, so they show up
in the dashboard card picker right after installing the integration -- no
copying to ``config/www/`` and no manual *Resources* entry.

* :func:`async_setup_lovelace` serves the card modules under
  :data:`URL_BASE` and registers each one as a Lovelace dashboard resource
  (storage mode), falling back to frontend extra modules for both frontend
  builds when resources live in YAML. The URL carries a content hash
  (``?v=<md5>``) so browsers pick up a new card version immediately despite
  Home Assistant's long-lived cache headers.
* ``jura/machines`` (websocket) lists the configured machines with their
  product table (per-product parameters, ranges and items), the device id
  and the status / connectivity / brew-progress entity ids. The card editor
  uses it to offer only the parameters a product really has.

Both run once per Home Assistant instance, however many machines are set up.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from jura_connect import (
    KIND_COFFEE_STRENGTH,
    KIND_GRINDER_RATIO,
    KIND_MILK_AMOUNT,
    KIND_MILK_FOAM_AMOUNT,
    KIND_TEMPERATURE,
    KIND_WATER_AMOUNT,
    MachineProfile,
    load_profile,
)

from .const import CONF_CONN_ID, CONF_MACHINE_TYPE, DOMAIN

_LOGGER = logging.getLogger(__name__)

URL_BASE = "/jura_connect"
WWW_DIR = Path(__file__).parent / "www"
CARD_FILES: tuple[str, ...] = ("jura-brew-card.js", "jura-coffee-button-card.js")

DATA_LOVELACE = f"{DOMAIN}_lovelace_registered"

# Library recipe-param kind -> ``jura.brew`` service field. The card stores
# its parameters under these keys, so a card config reads exactly like a
# ``jura.brew`` service call.
PARAM_FIELDS: dict[str, str] = {
    KIND_COFFEE_STRENGTH: "strength",
    KIND_WATER_AMOUNT: "water_ml",
    KIND_TEMPERATURE: "temperature",
    KIND_GRINDER_RATIO: "grinder_ratio",
    KIND_MILK_AMOUNT: "milk_s",
    KIND_MILK_FOAM_AMOUNT: "milk_foam_s",
}


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()[:8]


async def async_setup_lovelace(hass: HomeAssistant) -> None:
    """Serve and auto-load the bundled cards; register the websocket API.

    Idempotent (runs from the domain's ``async_setup``). A failure here never
    blocks the integration itself -- the entities and services keep working,
    the user just has to add the card resource by hand.
    """
    if hass.data.get(DATA_LOVELACE):
        return
    try:
        websocket_api.async_register_command(hass, ws_machines)
        files = [name for name in CARD_FILES if (WWW_DIR / name).is_file()]
        if files:
            hashes = {name: await hass.async_add_executor_job(_file_hash, WWW_DIR / name) for name in files}
            # Cache headers on: the ?v=<hash> query changes with every card
            # change, and slow clients (wall displays, phone apps) must not
            # re-download the cards on every reload.
            await hass.http.async_register_static_paths(
                [StaticPathConfig(f"{URL_BASE}/{name}", str(WWW_DIR / name), True) for name in files]
            )
            urls = {f"{URL_BASE}/{name}": f"{URL_BASE}/{name}?v={hashes[name]}" for name in files}
            if not await _async_register_resources(hass, urls):
                # Resources managed in YAML: extra script tags for both frontend
                # builds. Older WebViews (Shelly Wall Display, old tablets) get the
                # legacy build, which only loads the es5 list.
                for url in urls.values():
                    add_extra_js_url(hass, url)
                    add_extra_js_url(hass, url, es5=True)
        else:
            _LOGGER.warning("Jura dashboard cards missing in %s; is the integration installed completely?", WWW_DIR)
    except Exception:
        # DATA_LOVELACE stays unset: the entry can only be re-set up via a
        # restart otherwise, since card setup runs from the domain's
        # async_setup, which an entry reload does not re-run.
        _LOGGER.exception("Could not register the Jura dashboard cards; add them as a dashboard resource instead")
        return
    hass.data[DATA_LOVELACE] = True


async def _async_register_resources(hass: HomeAssistant, urls: dict[str, str]) -> bool:
    """Create / update one Lovelace *resource* per card (storage mode).

    Same mechanism HACS uses: the frontend loads dashboard resources before
    it builds the cards, so slow devices do not hit "Custom element doesn't
    exist" -- an extra script tag is loaded without the frontend waiting for
    it. Entries are matched by path, so a stale ``?v=`` or a manually added
    ``/jura_connect/...`` resource is updated in place and duplicates are
    removed. Returns False when resources are managed in YAML.
    """
    data = hass.data.get("lovelace")
    resources = getattr(data, "resources", None)
    if resources is None and isinstance(data, dict):  # HA < 2025.2 kept a plain dict
        resources = data.get("resources")
    if resources is None or not hasattr(resources, "async_create_item"):
        return False
    if not getattr(resources, "loaded", True):
        await resources.async_load()
        resources.loaded = True
    items = list(resources.async_items())
    for base, url in urls.items():
        ours = [item for item in items if str(item.get("url", "")).split("?", 1)[0] == base]
        if not ours:
            _LOGGER.info("Registering dashboard resource %s", url)
            await resources.async_create_item({"res_type": "module", "url": url})
            continue
        first, *duplicates = ours
        # Stored items carry the type as "type"; create/update take "res_type".
        if first.get("url") != url or first.get("type") != "module":
            _LOGGER.info("Updating dashboard resource to %s", url)
            await resources.async_update_item(first["id"], {"res_type": "module", "url": url})
        for item in duplicates:
            await resources.async_delete_item(item["id"])
    for item in items:
        base = str(item.get("url", "")).split("?", 1)[0]
        if base.startswith(f"{URL_BASE}/") and base not in urls:
            _LOGGER.info("Removing stale dashboard resource %s", item.get("url"))
            await resources.async_delete_item(item["id"])
    return True


def _param_payload(param: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "field": PARAM_FIELDS[param.kind],
        "kind": param.kind,
        "default": param.default,
    }
    if param.items:
        # Item values are the catalogue hex bytes; jura.brew takes the
        # integer form (strength 10 == "0A", temperature high == "02").
        payload["items"] = [{"name": item.name, "value": int(item.value, 16)} for item in param.items]
    else:
        payload["min"] = param.minimum
        payload["max"] = param.maximum
        payload["step"] = param.step or 1
    return payload


def products_payload(profile: MachineProfile | None) -> list[dict[str, Any]]:
    """JSON-safe product table for the card editor.

    Only products the panel can brew are listed, and only the parameters the
    ``jura.brew`` service can override.
    """
    if profile is None:
        return []
    return [
        {
            "code": product.code,
            "name": product.name,
            "label": product.raw_name,
            "params": [_param_payload(param) for param in product.params if param.kind in PARAM_FIELDS],
        }
        for product in profile.products
    ]


def _machine_entities(hass: HomeAssistant, slug: str) -> tuple[dict[str, str | None], str | None]:
    """Key entity ids of one machine, plus its device id.

    The device id comes from the entities' registry entries, so the device
    registry is not touched at all (its lookup API keeps changing; HA logged
    a deprecation for this integration).
    """
    registry = er.async_get(hass)
    entities = {
        "status": registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{slug}_state"),
        "connectivity": registry.async_get_entity_id("binary_sensor", DOMAIN, f"{DOMAIN}_{slug}_connectivity"),
        "progress": registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{slug}_brew_progress"),
        "brew_button": registry.async_get_entity_id("button", DOMAIN, f"{DOMAIN}_{slug}_brew"),
    }
    device_id = None
    for entity_id in entities.values():
        entry = registry.async_get(entity_id) if entity_id else None
        if entry is not None and getattr(entry, "device_id", None):
            device_id = entry.device_id
            break
    return entities, device_id


def _load_profile(machine_type: str | None) -> MachineProfile | None:
    if not machine_type:
        return None
    try:
        return load_profile(machine_type)
    except KeyError:
        return None


@websocket_api.websocket_command({vol.Required("type"): "jura/machines"})
@websocket_api.async_response
async def ws_machines(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """Return every configured machine with its product table and key entities.

    Lists machines whose setup is still running or being retried too: the
    product table comes from the bundled profile of the configured machine
    type, not from the machine, so cards can show the drink and a clear
    "not reachable" state instead of "no machine found". ``state`` is the
    config entry state (``loaded``, ``setup_retry``, ...).
    """
    coordinators = hass.data.get(DOMAIN, {})
    machines = []
    for entry in sorted(hass.config_entries.async_entries(DOMAIN), key=lambda e: e.entry_id):
        coordinator = coordinators.get(entry.entry_id)
        if coordinator is not None:
            profile = coordinator.brew_profile
        else:
            # Disk read (lru-cached in jura_connect): keep it off the loop.
            profile = await hass.async_add_executor_job(_load_profile, entry.data.get(CONF_MACHINE_TYPE))
        slug = str(entry.data.get(CONF_CONN_ID, "")).replace("-", "_").lower()
        entities, device_id = _machine_entities(hass, slug)
        state = getattr(entry.state, "value", entry.state)
        machines.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "state": str(state),
                "device_id": device_id,
                "entities": entities,
                "products": products_payload(profile),
            }
        )
    connection.send_result(msg["id"], {"machines": machines})
