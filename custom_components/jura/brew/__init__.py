"""Framework-free brew payload + machine-definition helpers.

Ported verbatim from the ha-jura-wifi project (pure stdlib, no Home
Assistant imports) so the ``@TP:`` start-command encoding can be unit
tested without HA and reused by the button / select / number platforms
and the ``jura.brew`` service.

The machine-definition XMLs are NOT re-bundled here. makefu's
``jura_connect`` package already vendors them as package data under
``jura_connect/data/xml/<EF>/<ver>.xml``; :func:`jura_connect_xml_dir`
resolves that directory with :mod:`importlib.resources` and the parser
in :mod:`.definitions` reads from it.
"""

from __future__ import annotations

import os

from .definitions import (
    AlertDef,
    MachineDefinition,
    ProductArg,
    ProductDef,
    definition_dir_for_type,
    load_definition,
    parse_definition_xml,
)
from .prefs import BREW_PARAMS, product_prefs, remember_param, selection_for_product
from .products import build_start_command

__all__ = [
    "BREW_PARAMS",
    "AlertDef",
    "MachineDefinition",
    "ProductArg",
    "ProductDef",
    "build_start_command",
    "definition_dir_for_type",
    "jura_connect_xml_dir",
    "load_definition",
    "parse_definition_xml",
    "product_prefs",
    "remember_param",
    "selection_for_product",
]


def jura_connect_xml_dir() -> str | None:
    """Return the path to jura_connect's bundled machine-definition XMLs.

    Returns the filesystem path of ``jura_connect/data/xml`` (suitable as
    ``base_dir`` for :func:`load_definition` / :func:`definition_dir_for_type`),
    or ``None`` when the ``jura_connect`` package — or its data dir — is
    unavailable.
    """
    from importlib.resources import files

    try:
        base = files("jura_connect") / "data" / "xml"
    except ModuleNotFoundError:
        return None
    path = str(base)
    return path if os.path.isdir(path) else None
