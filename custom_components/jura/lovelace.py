"""Dashboard (Lovelace) support: bundled cards + a small websocket API.

The integration ships its dashboard cards inside the component directory
(``custom_components/jura/www/``) and registers them itself, so they show up
in the dashboard card picker right after installing the integration -- no
copying to ``config/www/`` and no manual *Resources* entry.

* :func:`async_setup_lovelace` serves the card modules under
  :data:`URL_BASE` and adds each one as a frontend extra module. The URL
  carries a content hash (``?v=<md5>``) so browsers pick up a new card
  version immediately despite Home Assistant's long-lived cache headers.
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from jura_connect import (
    KIND_COFFEE_STRENGTH,
    KIND_GRINDER_RATIO,
    KIND_MILK_AMOUNT,
    KIND_MILK_FOAM_AMOUNT,
    KIND_TEMPERATURE,
    KIND_WATER_AMOUNT,
    MachineProfile,
)

from .const import CONF_CONN_ID, DOMAIN

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

    Idempotent: only the first config entry does the work. A failure here
    never blocks the integration itself -- the entities and services keep
    working, the user just has to add the card resource by hand.
    """
    if hass.data.get(DATA_LOVELACE):
        return
    hass.data[DATA_LOVELACE] = True

    websocket_api.async_register_command(hass, ws_machines)

    try:
        files = [name for name in CARD_FILES if (WWW_DIR / name).is_file()]
        hashes = {name: await hass.async_add_executor_job(_file_hash, WWW_DIR / name) for name in files}
        await hass.http.async_register_static_paths(
            [StaticPathConfig(f"{URL_BASE}/{name}", str(WWW_DIR / name), True) for name in files]
        )
        for name in files:
            add_extra_js_url(hass, f"{URL_BASE}/{name}?v={hashes[name]}")
    except Exception:
        _LOGGER.exception("Could not register the Jura dashboard cards; add them as a dashboard resource instead")


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


def _machine_entities(hass: HomeAssistant, slug: str) -> dict[str, str | None]:
    registry = er.async_get(hass)
    return {
        "status": registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{slug}_state"),
        "connectivity": registry.async_get_entity_id("binary_sensor", DOMAIN, f"{DOMAIN}_{slug}_connectivity"),
        "progress": registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{slug}_brew_progress"),
    }


@websocket_api.websocket_command({vol.Required("type"): "jura/machines"})
@callback
def ws_machines(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """Return every loaded machine with its product table and key entities."""
    devices = dr.async_get(hass)
    machines = []
    for entry_id, coordinator in sorted(hass.data.get(DOMAIN, {}).items()):
        entry = coordinator.config_entry
        slug = str(entry.data.get(CONF_CONN_ID, "")).replace("-", "_").lower()
        device = devices.async_get_device(identifiers={(DOMAIN, entry_id)})
        machines.append(
            {
                "entry_id": entry_id,
                "title": entry.title,
                "device_id": device.id if device is not None else None,
                "entities": _machine_entities(hass, slug),
                "products": products_payload(coordinator.brew_profile),
            }
        )
    connection.send_result(msg["id"], {"machines": machines})
