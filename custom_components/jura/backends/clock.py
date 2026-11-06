"""Restricted wire protocol for reading and setting the EF566 clock."""

from __future__ import annotations

import time
from datetime import datetime
from threading import Event

from jura_connect import JuraClient

from ..clock import (
    TIMER_ADDRESSES,
    ClockReading,
    ClockReference,
    ClockUpdate,
    clock_machine_ready,
    parse_clock,
    parse_date,
    validate_schedule,
    validate_write_window,
)
from .base import ClockWriteUnverified, JuraBackendError

_KEYS_ACK = r"^@(ts|an)(?::|$)"
_READBACK_OFFSETS = (0.0, 3.0, 6.0, 10.0)


def read_clock(client: JuraClient, reference: ClockReference, stop: Event) -> ClockReading:
    def read(slot: str) -> str:
        if stop.is_set():
            raise JuraBackendError("JURA clock synchronization was cancelled")
        return client.read_setting(slot, timeout=1.5)

    raw = read("05")
    observed_at = reference.now()
    day = parse_date(read("06"))
    timers = tuple(read(slot).upper() for slot in TIMER_ADDRESSES)
    reading = ClockReading(parse_clock(raw), day, timers, observed_at)
    validate_schedule(reading)
    return reading


def _check_ready(client: JuraClient, *, timeout: float = 3) -> None:
    status = client.read_status(timeout=timeout)
    if not clock_machine_ready(status.active_alerts, status.errors) or any(
        frame.startswith("@TV:") for frame in client.status_history
    ):
        raise JuraBackendError("Leave the JURA on its ready screen before synchronizing the clock")


def _wait_until(reference: ClockReference, epoch: float, stop: Event) -> None:
    if stop.wait(max(0, epoch - reference.now().timestamp())):
        raise JuraBackendError("JURA clock synchronization was cancelled")


def _write_clock(client: JuraClient, reference: ClockReference, epoch: float, stop: Event) -> None:
    try:
        if client.request("@TS:01", match=_KEYS_ACK, timeout=3) != "@ts":
            raise JuraBackendError("JURA rejected the keypad lock")
        actual = reference.now()
        if stop.is_set() or not 0 <= actual.timestamp() - epoch <= 4:
            raise JuraBackendError("The safe JURA write window passed; press the button again")
        body = f"05,22{actual.hour:02X}{actual.minute:02X}"
        checksum = (-1 - sum(body.encode("ascii"))) & 0xFF
        reply = client.request(f"@TM:{body}{checksum:02X}", match=r"(?i)^@(tm|an):", timeout=3)
        if reply.lower() != "@tm:05":
            raise JuraBackendError("JURA did not acknowledge the clock write")
    finally:
        try:
            unlocked = client.request("@TS:00", match=_KEYS_ACK, timeout=3)
        except OSError as err:
            raise JuraBackendError("JURA clock result unknown: keypad unlock not confirmed") from err
        if unlocked != "@ts":
            raise JuraBackendError("JURA clock result unknown: keypad unlock not confirmed")


def start_clock_sync(client: JuraClient, reference: ClockReference, stop: Event) -> ClockUpdate:
    """Perform at most one correction, verifying it after releasing the keys."""
    _check_ready(client)
    before = read_clock(client, reference, stop)
    if before.day != before.observed_at.date():
        raise JuraBackendError("The JURA and Home Assistant dates differ; set the JURA date on the machine")
    if before.matches_ha():
        return ClockUpdate(before, wrote=False)
    now = reference.now()
    epoch = int(now.timestamp() // 60 + 1) * 60
    target = datetime.fromtimestamp(epoch, reference.zone)
    validate_write_window(before, target)
    # Re-read close to the write so a timer edited on the machine during the
    # wait cannot silently invalidate the schedule guard.
    _wait_until(reference, epoch - 10, stop)
    fresh = read_clock(client, reference, stop)
    if not fresh.same_schedule(before):
        raise JuraBackendError("The JURA date or timers changed during the check; no clock write performed")
    validate_write_window(fresh, target)
    if fresh.matches_ha():
        return ClockUpdate(fresh, wrote=False)
    _wait_until(reference, epoch + 0.15, stop)
    _check_ready(client, timeout=2)
    if not 0 <= reference.now().timestamp() - epoch <= 3:
        raise JuraBackendError("The safe JURA write window passed; press the button again")
    try:
        _write_clock(client, reference, epoch, stop)
        anchor = time.monotonic()
        for offset in _READBACK_OFFSETS:
            if stop.wait(max(0, anchor + offset - time.monotonic())):
                raise JuraBackendError("JURA clock verification was cancelled after the write")
            parse_clock(client.read_setting("05", timeout=1.5))
        settled = read_clock(client, reference, stop)
        if not before.same_schedule(settled) or not settled.matches_ha():
            raise JuraBackendError("JURA clock write could not be verified; check the machine before trying again")
    except (JuraBackendError, OSError, ValueError) as err:
        raise ClockWriteUnverified(str(err)) from err
    return ClockUpdate(settled, wrote=True)
