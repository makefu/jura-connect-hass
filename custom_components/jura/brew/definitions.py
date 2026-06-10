"""Parse JURA machine-definition XMLs (J.O.E. ``joe_control`` schema).

Each machine ships a definition under ``<GROUP>/<version>.xml`` that
describes its products (and how to encode a product-start command), its alert
bit -> human-name map, and the product-counter slot names. This module turns one
of those XMLs into plain dataclasses; it is framework-free (stdlib ``xml.etree``
only) so the protocol logic can be used without Home Assistant installed.

Ported verbatim from the ha-jura-wifi project. The XMLs themselves are NOT
bundled with this integration; the caller supplies ``base_dir`` (see
:func:`custom_components.jura.brew.jura_connect_xml_dir`, which points at the
``jura_connect`` package's vendored data).

Argument indices (used by :mod:`.products`): a product argument's ``Argument``
attribute is ``F<n>`` and maps to byte index ``n - 1`` of the 16-byte start
payload -- COFFEE_STRENGTH ``F3`` -> 2, WATER_AMOUNT ``F4`` -> 3, TEMPERATURE
``F7`` -> 6. Counter slots map ``counter index = PosCSV - 1`` (validated against
live ``@TR:32`` captures).
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# Fallback definition root (unused in this integration: the caller always
# passes ``base_dir`` resolved from the jura_connect package data).
DEFINITIONS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "definitions"))

# Product child tags we encode into the start command.
ARG_KINDS = ("COFFEE_STRENGTH", "WATER_AMOUNT", "TEMPERATURE")

_TYPE_RE = re.compile(r"(EF\d+\w*)")
_BASE_RE = re.compile(r"(EF\d+)")


@dataclass(frozen=True)
class ProductArg:
    """One adjustable product argument (strength / water / temperature).

    ``index`` is the byte position in the 16-byte ``@TP:`` payload. ``default``
    is the byte value for strength/temperature (parsed from the hex ``Default``)
    or the default millilitres for water (parsed from decimal ``Value``); water
    also carries ``min``/``max``/``step`` and is encoded as ``ml // step``.
    """

    kind: str
    argument: str
    index: int
    default: int
    min: int | None = None
    max: int | None = None
    step: int | None = None
    items: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ProductDef:
    """A single brewable product and its adjustable arguments."""

    code: str
    name: str
    picture: str | None
    pos_csv: int | None
    args: tuple[ProductArg, ...] = ()

    def arg(self, kind: str) -> ProductArg | None:
        """Return the argument of ``kind`` for this product, or ``None``."""
        for candidate in self.args:
            if candidate.kind == kind:
                return candidate
        return None


@dataclass(frozen=True)
class AlertDef:
    """A status-bit alert: bit index -> human-readable name.

    ``type`` mirrors the XML ``Type`` attribute (``"block"``, ``"info"``,
    ``"ip"`` or ``None``) and lets the binary-sensor platform pick a sensible
    device class without inventing a classification.
    """

    bit: int
    name: str
    type: str | None = None


@dataclass
class MachineDefinition:
    """Everything we decode from one machine-definition XML."""

    products: tuple[ProductDef, ...]
    alerts: tuple[AlertDef, ...]
    counters_by_poscsv: dict[int, str] = field(default_factory=dict)
    machine_type: str | None = None


def _local(tag: str) -> str:
    """Strip an XML namespace, returning the bare local tag name."""
    return tag.rsplit("}", 1)[-1]


def _int(value: str | None) -> int | None:
    """Parse a decimal integer attribute, tolerating absence/garbage."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_arg(kind: str, element: ET.Element) -> ProductArg:
    """Parse a COFFEE_STRENGTH / WATER_AMOUNT / TEMPERATURE element."""
    argument = element.get("Argument") or ""
    index = int(argument[1:]) - 1 if argument[1:].isdigit() else -1

    items: list[tuple[str, int]] = []
    for item in element:
        if _local(item.tag) != "ITEM":
            continue
        raw = item.get("Value")
        try:
            value = int(raw, 16) if raw else 0
        except ValueError:
            value = 0
        items.append((item.get("Name") or "", value))

    if kind == "WATER_AMOUNT":
        default = _int(element.get("Value")) or _int(element.get("Default")) or 0
        return ProductArg(
            kind=kind,
            argument=argument,
            index=index,
            default=default,
            min=_int(element.get("Min")),
            max=_int(element.get("Max")),
            step=_int(element.get("Step")),
            items=tuple(items),
        )

    # strength / temperature: the Default attribute is a single hex byte.
    try:
        default = int(element.get("Default") or "00", 16)
    except ValueError:
        default = 0
    return ProductArg(
        kind=kind,
        argument=argument,
        index=index,
        default=default,
        items=tuple(items),
    )


def _parse_product(element: ET.Element) -> ProductDef:
    """Parse one ``<PRODUCT>`` element and its encodable arguments."""
    pos = element.get("PosCSV")
    pos_csv = int(pos) if pos is not None and pos.isdigit() else None
    args = [_parse_arg(_local(child.tag), child) for child in element if _local(child.tag) in ARG_KINDS]
    return ProductDef(
        code=element.get("Code") or "",
        name=element.get("Name") or "",
        picture=element.get("PictureIdle"),
        pos_csv=pos_csv,
        args=tuple(args),
    )


def parse_definition_xml(path: str) -> MachineDefinition:
    """Parse a machine-definition XML into a :class:`MachineDefinition`.

    Only ``Active="true"`` products are included. Counter names are collected
    from the ``TOTALCOUNTER`` plus each active product's ``PosCSV``. Alerts map
    each ``<ALERT Bit Name>`` to its human-readable name.
    """
    root = ET.parse(path).getroot()

    products: list[ProductDef] = []
    alerts: list[AlertDef] = []
    counters: dict[int, str] = {}

    for element in root.iter():
        tag = _local(element.tag)
        if tag == "TOTALCOUNTER":
            pos = element.get("PosCSV")
            if pos is not None and pos.isdigit():
                counters[int(pos)] = element.get("Name") or "Total Products"
        elif tag == "PRODUCT":
            if (element.get("Active") or "").lower() != "true":
                continue
            product = _parse_product(element)
            products.append(product)
            if product.pos_csv is not None:
                counters[product.pos_csv] = product.name
        elif tag == "ALERT":
            bit = element.get("Bit")
            if bit is not None and bit.lstrip("-").isdigit():
                alerts.append(
                    AlertDef(
                        bit=int(bit),
                        name=element.get("Name") or "",
                        type=element.get("Type"),
                    )
                )

    return MachineDefinition(
        products=tuple(products),
        alerts=tuple(alerts),
        counters_by_poscsv=counters,
        machine_type=root.get("Group"),
    )


def definition_dir_for_type(machine_type: str | None, base_dir: str = DEFINITIONS_DIR) -> str | None:
    """Resolve a reported machine type to a bundled definition directory name.

    Takes the leading ``EF<digits><letters>`` token (e.g. ``"EF1030M"`` from
    ``"EF1030M V01.06"``), tries it as an exact directory, then falls back to the
    numeric base (``"EF1030"``). Returns the directory name that exists, else
    ``None``.
    """
    if not machine_type:
        return None
    match = _TYPE_RE.match(machine_type.strip()) or _TYPE_RE.search(machine_type)
    if not match:
        return None
    token = match.group(1)
    if os.path.isdir(os.path.join(base_dir, token)):
        return token
    base = _BASE_RE.match(token)
    if base:
        base_name = base.group(1)
        if base_name != token and os.path.isdir(os.path.join(base_dir, base_name)):
            return base_name
    return None


def _version_key(stem: str) -> tuple[int, ...]:
    """Sort key for a ``<major>.<minor>`` version filename stem."""
    parts: list[int] = []
    for piece in stem.split("."):
        parts.append(int(piece) if piece.isdigit() else -1)
    return tuple(parts) if parts else (-1,)


def _highest_version_xml(dir_path: str) -> str | None:
    """Return the path of the highest-version ``*.xml`` file in ``dir_path``."""
    best: str | None = None
    best_key: tuple[int, ...] | None = None
    for fname in os.listdir(dir_path):
        if not fname.lower().endswith(".xml"):
            continue
        key = _version_key(fname[:-4])
        if best_key is None or key > best_key:
            best_key = key
            best = os.path.join(dir_path, fname)
    return best


def load_definition(machine_type: str | None, base_dir: str = DEFINITIONS_DIR) -> MachineDefinition | None:
    """Resolve ``machine_type`` to its bundled definition and parse it.

    Returns ``None`` when no directory matches the type or the directory holds no
    XML file.
    """
    dir_name = definition_dir_for_type(machine_type, base_dir)
    if dir_name is None:
        return None
    xml_path = _highest_version_xml(os.path.join(base_dir, dir_name))
    if xml_path is None:
        return None
    return parse_definition_xml(xml_path)
