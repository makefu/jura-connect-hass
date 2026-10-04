"""Pure (framework-free) per-product brew-preference helpers.

A *brew preference* is the remembered strength / water / temperature /
grinder ratio / milk / milk-foam for a single product, keyed by the product's
hex Code (e.g. ``"03"``).
The on-disk / in-memory shape is::

    brew_prefs: dict[str, dict] = {
        "03": {
            "strength": 2,
            "water_ml": 130,
            "temp": None,
            "grinder_ratio": None,
            "milk_s": None,
            "milk_foam_s": 12,
        },
        ...
    }

``None`` for any parameter means **Factory Default**: send that product's
XML/factory default value (pass ``None`` to the ``jura_connect`` library's
``ProductDef.build_recipe_hex`` builder). It does NOT mean "use whatever the
machine currently has stored" — JURA WiFi exposes no such mechanism (truncated
start payloads are rejected and per-byte sentinels are taken literally), so we
always send an explicit, clamped value.

These helpers only manipulate plain JSON-safe dicts so they can be unit-tested
without Home Assistant or a Store, and reused by the coordinator (which adds
the actual persistence + staged-selection wiring on top).

All brew *protocol* logic — product tables, XML decoding, ``@TP:`` payload
construction — lives in the ``jura_connect`` backend library, not here. This
module only models the component's remembered-preference shape.
"""

from __future__ import annotations

# The adjustable recipe axes a preference can pin per product. Milk and milk
# foam are dispensing times in seconds — that is the unit the machines expose
# (MinMax args on the F5/F6 recipe bytes), not millilitres.
BREW_PARAMS: tuple[str, ...] = (
    "strength",
    "water_ml",
    "temp",
    "grinder_ratio",
    "milk_s",
    "milk_foam_s",
)


def product_prefs(brew_prefs: dict[str, dict], code: str) -> dict[str, int | None]:
    """Return ``code``'s stored prefs, with every missing param as ``None``.

    Both an absent product and an absent / explicitly-``None`` parameter map to
    ``None`` (Factory Default), so callers get a uniform full dict.
    """
    stored = brew_prefs.get(code) or {}
    return {param: stored.get(param) for param in BREW_PARAMS}


def selection_for_product(brew_prefs: dict[str, dict], code: str) -> dict[str, int | str | None]:
    """Build a ``brew_selection`` dict for ``code`` from its saved prefs.

    The returned dict carries the product ``code`` plus each remembered
    parameter (``None`` == Factory Default), ready to merge into the
    coordinator's staged ``brew_selection``.
    """
    return {"product": code, **product_prefs(brew_prefs, code)}


def remember_param(brew_prefs: dict[str, dict], code: str, param: str, value: int | None) -> None:
    """Persist ``value`` for ``param`` of product ``code`` (mutates ``brew_prefs``).

    ``value`` may be ``None`` to explicitly record Factory Default for that
    parameter.
    """
    brew_prefs.setdefault(code, {})[param] = value
