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
_MAX_BYTE = 0xFF


def _clamp_to_items(value: int, items: tuple[tuple[str, int], ...]) -> int:
    """Clamp ``value`` to the [min, max] span of an argument's ITEM byte values.

    Used for strength / temperature, whose valid values are an explicit set of
    named ITEMs. No-op when the argument carries no items.
    """
    if not items:
        return value
    values = [byte for _, byte in items]
    return max(min(values), min(value, max(values)))


def _encode_water(arg: ProductArg, water_ml: int | None) -> int:
    """Resolve the water byte: ``ml // step``, with ml clamped to [min, max].

    A ``None`` override means Factory Default -> the product's XML default ml,
    which is authoritative and encoded as-is. An explicit override is clamped to
    the product's [Min, Max] range first; JURA WiFi treats the water byte as a
    literal ``byte * step`` ml, so an unclamped value would over/under-fill.
    """
    step = arg.step or 1
    if water_ml is None:
        return arg.default // step
    ml = water_ml
    if arg.min is not None:
        ml = max(arg.min, ml)
    if arg.max is not None:
        ml = min(arg.max, ml)
    return ml // step


def _arg_value(
    arg: ProductArg,
    *,
    strength: int | None,
    water_ml: int | None,
    temp: int | None,
) -> int | None:
    """Resolve one argument's byte value, honouring (clamped) overrides then defaults.

    A ``None`` override == Factory Default: the product's XML/factory default
    byte is emitted unchanged (it is authoritative and already valid). There is
    deliberately no "use the machine's own stored setting" path — JURA WiFi has
    no such mechanism, so explicit overrides are clamped into range instead.
    """
    if arg.kind == "COFFEE_STRENGTH":
        if strength is None:
            return arg.default
        return _clamp_to_items(strength, arg.items)
    if arg.kind == "TEMPERATURE":
        if temp is None:
            return arg.default
        return _clamp_to_items(temp, arg.items)
    if arg.kind == "WATER_AMOUNT":
        return _encode_water(arg, water_ml)
    return None


def build_start_command(
    product: ProductDef,
    *,
    strength: int | None = None,
    water_ml: int | None = None,
    temp: int | None = None,
) -> str:
    """Encode the ``@TP:`` start command for ``product``.

    Unset keyword overrides (``None``) fall back to each argument's XML/factory
    default (Factory Default); explicit overrides are clamped to the argument's
    valid range/ITEM set. Water is encoded as ``ml // step``. Every byte is
    finally capped at ``0xFF`` so the payload can never overflow. Returns
    ``"@TP:" + <32 uppercase hex>``.
    """
    payload = ["00"] * _PAYLOAD_BYTES
    payload[0] = f"{int(product.code, 16):02X}"

    for arg in product.args:
        if not 0 <= arg.index < _PAYLOAD_BYTES:
            continue
        value = _arg_value(arg, strength=strength, water_ml=water_ml, temp=temp)
        if value is None:
            continue
        payload[arg.index] = f"{max(0, min(value, _MAX_BYTE)):02X}"

    payload[_FIXED_INDEX] = "01"
    return PREFIX + "".join(payload).upper()
