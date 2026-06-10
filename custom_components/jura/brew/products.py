"""Build the ``@TP:`` product-start command for a JURA WiFi machine.

The command is ``"@TP:"`` followed by a 16-byte hex payload (uppercase). The
encoding was validated live on a JURA E6 (a real coffee brewed from it):

    byte 0          product Code (e.g. 0x03 Coffee, 0x02 Espresso)
    byte arg.index  each argument value, where index = Fnum - 1:
                      COFFEE_STRENGTH F3 -> 2   value = chosen level byte
                      WATER_AMOUNT    F4 -> 3   value = ml // step
                      TEMPERATURE     F7 -> 6   value = chosen item byte
    byte 8          0x01 (fixed)
    byte 15         0x00 (grinder default)
    all other bytes 0x00

Validated vector: Coffee(0x03), strength 2, 130 ml, temp Normal(0x01) ->
``@TP:0300021A000001000100000000000000`` (130 // 5 = 26 = 0x1A).

This module never sends anything; it only encodes the string. The actual brew is
triggered by an explicit Home Assistant button press / service call at runtime.
"""

from __future__ import annotations

from .definitions import ProductArg, ProductDef

PREFIX = "@TP:"
_PAYLOAD_BYTES = 16
_FIXED_INDEX = 8  # byte 8 is always 0x01 in a start command.


def _arg_value(
    arg: ProductArg,
    *,
    strength: int | None,
    water_ml: int | None,
    temp: int | None,
) -> int | None:
    """Resolve one argument's byte value, honouring overrides then defaults."""
    if arg.kind == "COFFEE_STRENGTH":
        return strength if strength is not None else arg.default
    if arg.kind == "TEMPERATURE":
        return temp if temp is not None else arg.default
    if arg.kind == "WATER_AMOUNT":
        ml = water_ml if water_ml is not None else arg.default
        step = arg.step or 1
        return ml // step
    return None


def build_start_command(
    product: ProductDef,
    *,
    strength: int | None = None,
    water_ml: int | None = None,
    temp: int | None = None,
) -> str:
    """Encode the ``@TP:`` start command for ``product``.

    Unset keyword overrides fall back to each argument's definition default;
    water is encoded as ``ml // step``. Returns ``"@TP:" + <32 uppercase hex>``.
    """
    payload = ["00"] * _PAYLOAD_BYTES
    payload[0] = f"{int(product.code, 16):02X}"

    for arg in product.args:
        if not 0 <= arg.index < _PAYLOAD_BYTES:
            continue
        value = _arg_value(arg, strength=strength, water_ml=water_ml, temp=temp)
        if value is None:
            continue
        payload[arg.index] = f"{value & 0xFF:02X}"

    payload[_FIXED_INDEX] = "01"
    return PREFIX + "".join(payload).upper()
