"""Serialization helpers turning MachineSnapshot fields into HA-attribute dicts."""

from __future__ import annotations

from typing import Any

from .backends.base import MachineSnapshot
from .const import PERCENT_ABSENT_SENTINEL


def serialize_snapshot(snapshot: MachineSnapshot) -> dict[str, Any]:
    """Render a snapshot as a JSON-safe dict for entity attributes."""
    return {
        "address": snapshot.address,
        "conn_id": snapshot.conn_id,
        "handshake_state": snapshot.handshake_state,
        "active_alerts": list(snapshot.active_alerts),
        "counters": dict(snapshot.counters),
        "percents": {k: percent_value(v) for k, v in snapshot.percents.items()},
        "raw_status_hex": snapshot.raw_status_hex,
    }


def percent_value(raw: int) -> int | None:
    """Translate the library's 0xFF 'absent' sentinel to ``None``."""
    if raw == PERCENT_ABSENT_SENTINEL:
        return None
    return raw
