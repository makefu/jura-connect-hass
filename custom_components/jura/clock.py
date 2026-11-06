"""GIGA 6 clock values, local-time guards and a bounded NTP check."""

from __future__ import annotations

import asyncio
import re
import secrets
import socket
import time as system_time
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

CLOCK_MODEL = "EF566"
CONF_CLOCK_SYNC = "clock_sync"
CONF_CLOCK_NTP_SERVER = "clock_ntp_server"
DEFAULT_CLOCK_NTP_SERVER = "pool.ntp.org"
CLOCK_SYNC_MODES = ("off", "manual", "automatic")
NTP_HOST_PATTERN = (
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?)?$"
)
TIMER_ADDRESSES = tuple(f"{address:02X}" for address in range(0x14, 0x1C))
CLOCK_CHECK_SECONDS = 65
CLOCK_AUTO_RETRY_SECONDS = 900
TIMER_MARGIN_MINUTES = 5
NTP_TOLERANCE_SECONDS = 5
NTP_EPOCH_DELTA = 2_208_988_800
NtpStatus = Literal["plausible", "unavailable", "disagrees"]
CLOCK_SYNC_STATES = ("not_run", "checking", "verifying", "already_aligned", "synchronized", "failed")


@dataclass(frozen=True, slots=True)
class ClockReference:
    """HA's wall clock in its configured zone, checked against elapsed time."""

    zone: ZoneInfo
    epoch: float
    monotonic: float

    @classmethod
    def capture(cls, zone: ZoneInfo) -> ClockReference:
        return cls(zone, system_time.time(), system_time.monotonic())

    def now(self) -> datetime:
        epoch = system_time.time()
        expected = self.epoch + system_time.monotonic() - self.monotonic
        if abs(epoch - expected) > NTP_TOLERANCE_SECONDS:
            raise ValueError("The Home Assistant clock changed during synchronization")
        return datetime.fromtimestamp(epoch, self.zone)


def _tagged_value(raw: str, tag: str, length: int) -> bytes:
    if not re.fullmatch(tag + rf"[0-9A-Fa-f]{{{2 * length}}}", raw):
        raise ValueError("Unexpected JURA clock, date or timer format")
    return bytes.fromhex(raw[2:])


def parse_clock(raw: str) -> time:
    hour, minute = _tagged_value(raw, "22", 2)
    return time(hour, minute)


def parse_date(raw: str) -> date:
    year, month, day = _tagged_value(raw, "23", 3)
    if year > 99:
        raise ValueError("Unexpected JURA clock year")
    return date(2000 + year, month, day)


def _minute(value: time | datetime) -> int:
    return value.hour * 60 + value.minute


def _timer_minutes(raw: str) -> tuple[int, ...]:
    on_hour, on_minute, off_hour, off_minute = _tagged_value(raw, "24", 4)
    return tuple(
        _minute(time(hour, minute))
        for hour, minute in ((on_hour, on_minute), (off_hour, off_minute))
        if (hour, minute) != (0x80, 0)
    )


@dataclass(frozen=True, slots=True)
class ClockReading:
    clock: time
    day: date
    timers: tuple[str, ...]
    observed_at: datetime

    def matches_ha(self) -> bool:
        # A read at the minute boundary can arrive just before or after it.
        reference = self.observed_at
        samples = (datetime.fromtimestamp(reference.timestamp() + delta, reference.tzinfo) for delta in (-2, 0, 2))
        return any(self.day == sample.date() and _minute(self.clock) == _minute(sample) for sample in samples)

    def same_schedule(self, other: ClockReading) -> bool:
        return self.day == other.day and self.timers == other.timers


@dataclass(frozen=True, slots=True)
class ClockUpdate:
    reading: ClockReading
    wrote: bool


@dataclass(frozen=True, slots=True)
class ClockSyncStatus:
    state: str = "not_run"
    ntp: NtpStatus | None = None
    reading: ClockReading | None = None
    detail: str | None = None


def clock_machine_ready(alerts: tuple[str, ...], errors: tuple[str, ...]) -> bool:
    busy = {
        "coffee_rinsing",
        "enjoy_product",
        "goodbye",
        "lockedkeys",
        "locked_keys",
        "program_mode_status",
        "remotescreen",
        "remote_screen",
        "system_emptying",
        "system_filling",
        "welcome",
    }
    return "coffee_ready" in alerts and not errors and not busy.intersection(alerts)


def clock_auto_period(local: datetime) -> str:
    """A fresh check is due after a date or UTC-offset change."""
    return f"{local.date()}/{local.tzinfo}/{local.utcoffset()}"


def validate_schedule(reading: ClockReading) -> tuple[int, ...]:
    if len(reading.timers) != len(TIMER_ADDRESSES):
        raise ValueError("Incomplete JURA timer block")
    return tuple(minute for raw in reading.timers for minute in _timer_minutes(raw))


def validate_write_window(reading: ClockReading, target: datetime) -> None:
    """Prevent date changes and crossing or approaching programmed timers."""
    if reading.day != target.date() or reading.day != reading.observed_at.date():
        raise ValueError("The JURA and Home Assistant dates differ; set the JURA date on the machine")
    later = datetime.fromtimestamp(target.timestamp() + CLOCK_CHECK_SECONDS + 15, target.tzinfo)
    if reading.observed_at.utcoffset() != target.utcoffset() or later.utcoffset() != target.utcoffset():
        raise ValueError("The Home Assistant time zone offset changed; try again later")
    low, high = sorted((_minute(reading.clock), _minute(target)))
    if low < 3 or high > 1436:
        raise ValueError("JURA clock synchronization is blocked near midnight")
    if any(
        low - TIMER_MARGIN_MINUTES <= timer + offset <= high + TIMER_MARGIN_MINUTES
        for timer in validate_schedule(reading)
        for offset in (-1440, 0, 1440)
    ):
        raise ValueError("The correction is too close to or crosses a programmed JURA timer")


def _ntp_offset(packet: bytes, nonce: bytes, epoch: float, round_trip: float) -> float:
    """Reject unsynchronised, unrelated and inconsistent NTP replies."""
    if (
        len(packet) < 48
        or packet[0] >> 6 == 3
        or (packet[0] >> 3) & 7 not in (3, 4)
        or packet[0] & 7 != 4
        or not 1 <= packet[1] <= 15
        or packet[24:32] != nonce
    ):
        raise ValueError("Unusable NTP reply")

    def stamp(raw: bytes) -> float:
        if not any(raw):
            raise ValueError("Empty NTP timestamp")
        base = int.from_bytes(raw, "big") / 2**32 - NTP_EPOCH_DELTA
        return base + round((epoch - base) / 2**32) * 2**32

    received, sent = stamp(packet[32:40]), stamp(packet[40:48])
    processing = sent - received
    if not 0 <= processing <= round_trip + 0.1:
        raise ValueError("Invalid NTP server timing")
    return sent + max(0, round_trip - processing) / 2 - epoch


async def check_ntp(server: str, reference: ClockReference) -> NtpStatus:
    """Attempt an NTP comparison in UTC; HA remains the time source."""
    server = server.strip() or DEFAULT_CLOCK_NTP_SERVER
    loop = asyncio.get_running_loop()
    packet = bytearray(48)
    packet[0] = 0x23
    nonce = secrets.token_bytes(8)
    packet[40:48] = nonce
    try:
        async with asyncio.timeout(3):
            addresses = await loop.getaddrinfo(server, 123, type=socket.SOCK_DGRAM)
            family, kind, protocol, _, address = addresses[0]
            with socket.socket(family, kind, protocol) as channel:
                channel.setblocking(False)
                await loop.sock_connect(channel, address)
                started = system_time.monotonic()
                await loop.sock_sendall(channel, packet)
                reply = await loop.sock_recv(channel, 512)
                arrived = system_time.monotonic()
        epoch = reference.epoch + arrived - reference.monotonic
        offset = _ntp_offset(reply, nonce, epoch, arrived - started)
    except (OSError, ValueError, IndexError):
        return "unavailable"
    return "plausible" if abs(offset) <= NTP_TOLERANCE_SECONDS else "disagrees"
