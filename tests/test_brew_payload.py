"""Brew-payload encoding tests.

These pin the ``@TP:`` start-command encoding against two vectors that
were validated live on a JURA E6 (a real coffee was brewed from each).
The logic under test is framework-free (pure stdlib), so this module
imports nothing from Home Assistant and makes no network calls.
"""

from __future__ import annotations

import pytest

from custom_components.jura.brew import ProductArg, ProductDef, build_start_command


def _product(code: str, args: list[ProductArg]) -> ProductDef:
    return ProductDef(code=code, name="x", picture=None, pos_csv=None, args=tuple(args))


def test_vector_1_coffee_with_overrides() -> None:
    """Coffee(0x03), strength 2, 130 ml, temp Normal(0x01).

    130 // 5 == 26 == 0x1A.
    """
    product = _product(
        "03",
        [
            ProductArg(kind="COFFEE_STRENGTH", argument="F3", index=2, default=5),
            ProductArg(kind="WATER_AMOUNT", argument="F4", index=3, default=120, min=25, max=240, step=5),
            ProductArg(kind="TEMPERATURE", argument="F7", index=6, default=2),
        ],
    )
    assert build_start_command(product, strength=2, water_ml=130, temp=1) == "@TP:0300021A000001000100000000000000"


def test_vector_2_espresso_defaults() -> None:
    """Default Espresso: code 0x02, strength 8, 45 ml (-> 9), temp 0x02."""
    product = _product(
        "02",
        [
            ProductArg(kind="COFFEE_STRENGTH", argument="F3", index=2, default=8),
            ProductArg(kind="WATER_AMOUNT", argument="F4", index=3, default=45, min=25, max=240, step=5),
            ProductArg(kind="TEMPERATURE", argument="F7", index=6, default=2),
        ],
    )
    assert build_start_command(product) == "@TP:02000809000002000100000000000000"


def test_payload_is_uppercase_and_16_bytes() -> None:
    product = _product(
        "03",
        [ProductArg(kind="WATER_AMOUNT", argument="F4", index=3, default=255, step=1)],
    )
    out = build_start_command(product, water_ml=255)
    assert out.startswith("@TP:")
    hex_part = out.removeprefix("@TP:")
    assert len(hex_part) == 32
    assert hex_part == hex_part.upper()


def test_brew_xml_path_end_to_end() -> None:
    """The XML sourcing path: resolve jura_connect's bundled data and
    encode a real product. Validates importlib.resources wiring +
    encoding shape without asserting machine-specific defaults.
    """
    pytest.importorskip("jura_connect")
    from custom_components.jura.brew import jura_connect_xml_dir, load_definition

    base_dir = jura_connect_xml_dir()
    assert base_dir is not None
    definition = load_definition("EF1091", base_dir=base_dir)
    assert definition is not None
    assert definition.products
    for product in definition.products:
        payload = build_start_command(product)
        assert payload.startswith("@TP:")
        assert len(payload.removeprefix("@TP:")) == 32
