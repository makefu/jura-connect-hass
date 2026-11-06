"""GIGA 6 weekday switch-on settings, separate from the clock and coffee timer."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import time
from typing import Literal

TIMER_MODEL = "EF566"
CONF_WEEKDAY_TIMERS = "weekday_timers"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAY_SLOTS = dict(zip(WEEKDAYS, (f"{n:02X}" for n in range(0x14, 0x1B)), strict=True))
ALL_DAYS_SLOT = "1B"
TIMER_SLOTS = (*WEEKDAY_SLOTS.values(), ALL_DAYS_SLOT)
TimerAction = Literal["set_time", "enable", "disable"]


def timer_minute(value: time) -> time:
    """Require a local time the machine can store without losing precision."""
    if value.second or value.microsecond or value.tzinfo is not None:
        raise ValueError("Select a local time with hours and minutes only")
    return value


def _parse_pair(pair: bytes) -> time | None:
    if pair == b"\x80\x00":
        return None
    return time(pair[0], pair[1])


def _encode_pair(value: time | None) -> bytes:
    if value is None:
        return b"\x80\x00"
    timer_minute(value)
    return bytes((value.hour, value.minute))


@dataclass(frozen=True, slots=True)
class TimerSlot:
    """The machine stores separate switch-on and switch-off pairs."""

    on: time | None
    off: time | None

    @classmethod
    def parse(cls, raw: str) -> TimerSlot:
        if not re.fullmatch(r"24[0-9a-fA-F]{8}", raw):
            raise ValueError("Invalid JURA timer payload")
        data = bytes.fromhex(raw)
        return cls(_parse_pair(data[1:3]), _parse_pair(data[3:5]))

    def encode(self) -> str:
        return "24" + (_encode_pair(self.on) + _encode_pair(self.off)).hex().upper()

    def with_on(self, on: time | None) -> TimerSlot:
        return replace(self, on=on)


@dataclass(frozen=True, slots=True)
class TimerUpdate:
    """Verified state with the previously active time for disabling."""

    slots: dict[str, TimerSlot]
    previous_on: time | None


def target_timer(current: TimerSlot, action: TimerAction, value: time | None) -> TimerSlot:
    """Derive a write from live state, preserving the switch-off pair."""
    if action == "disable":
        return current.with_on(None)
    if action == "enable" and current.on is not None:
        return current
    if action not in ("enable", "set_time"):
        raise ValueError("Unknown JURA timer action")
    if value is None:
        raise ValueError("Set a switch-on time before enabling this timer")
    timer_minute(value)
    # Editing a disabled timer only stages its time in HA.
    if action == "set_time" and current.on is None:
        return current
    return current.with_on(value)
