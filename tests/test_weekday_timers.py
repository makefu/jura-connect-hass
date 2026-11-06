"""Weekday timer safety and real wire round trips."""

from datetime import time, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from jura_connect.simulator import SimulatorConfig, run_in_thread

from custom_components.jura.backends.base import JuraBackendError, MachineSnapshot
from custom_components.jura.backends.jura import JuraConnectBackend
from custom_components.jura.const import CONF_CONN_ID, CONF_HOST, CONF_MACHINE_TYPE
from custom_components.jura.coordinator import JuraCoordinator
from custom_components.jura.switch import WeekdayTimerSwitch
from custom_components.jura.time import WeekdayTimerTime
from custom_components.jura.timers import CONF_WEEKDAY_TIMERS, TIMER_SLOTS, TimerSlot, TimerUpdate

RAW = dict(
    zip(
        TIMER_SLOTS,
        (
            "24061E8000",
            "24061E8000",
            "2406328000",
            "2406328000",
            "2406328000",
            "2407288000",
            "2407288000",
            "2480008000",
        ),
        strict=True,
    )
)


def timer_entry(model="EF566", enabled=True):
    return ConfigEntry(
        data={CONF_HOST: "127.0.0.1", CONF_CONN_ID: "giga", CONF_MACHINE_TYPE: model},
        options={CONF_WEEKDAY_TIMERS: enabled, "session_cooldown_seconds": 0},
    )


def snapshot(timers):
    return MachineSnapshot("127.0.0.1", "giga", "CORRECT", (), timers=timers)


def test_slot_codec_and_minute_precision():
    raw = "240000173B"
    slot = TimerSlot.parse(raw)
    assert slot.on == time(0) and slot.off == time(23, 59)
    assert slot.with_on(None).encode() == "248000173B"
    assert slot.with_on(time(6, 35)).encode() == "240623173B"
    for malformed in ("", "240623", "2206238000", "2480018000", "2418008000", "24063C8000"):
        with pytest.raises(ValueError):
            TimerSlot.parse(malformed)
    for invalid in (time(6, 35, 1), time(6, 35, tzinfo=timezone.utc)):
        with pytest.raises(ValueError):
            slot.with_on(invalid).encode()


async def test_wire_roundtrip_keeps_other_days_and_switch_off():
    raw = {**RAW, "14": "24061E1405"}  # The existing 20:05 switch-off must survive.
    config = SimulatorConfig(require_user_accept=False)
    config.settings.update(raw)
    with run_in_thread(config) as sim:
        backend = JuraConnectBackend(*sim.address, conn_id="timer-test", machine_type="EF566", weekday_timers=True)
        await backend.pair()
        assert (await backend.fetch()).timers["14"] == TimerSlot(time(6, 30), time(20, 5))
        await backend.update_timer("14", "set_time", time(6, 35))
        assert {s: config.settings[s] for s in TIMER_SLOTS} == {**raw, "14": "2406231405"}
        disabled = await backend.update_timer("14", "disable", None)
        assert disabled.previous_on == time(6, 35) and config.settings["14"] == "2480001405"
        await backend.update_timer("14", "set_time", time(6, 40))
        assert config.settings["14"] == "2480001405"
        await backend.update_timer("14", "enable", time(6, 40))
        assert {s: config.settings[s] for s in TIMER_SLOTS} == {**raw, "14": "2406281405"}
        config.settings["1B"] = "2407008000"
        with pytest.raises(JuraBackendError, match="all-days"):
            await backend.update_timer("14", "disable", None)
        assert config.settings["14"] == "2406281405"


@pytest.mark.parametrize(
    "model,enabled,slot",
    [
        ("EF1091", True, "14"),
        ("EF566", False, "14"),
        ("EF566", True, "1B"),
        ("EF566", True, "05"),
        ("EF566", True, "06"),
    ],
)
async def test_wrong_model_disabled_feature_and_nonweekday_addresses_never_connect(monkeypatch, model, enabled, slot):
    backend = JuraConnectBackend("127.0.0.1", conn_id="timer-test", machine_type=model, weekday_timers=enabled)
    connect = MagicMock()
    monkeypatch.setattr(backend, "_make_client", connect)
    with pytest.raises(JuraBackendError, match="not enabled"):
        await backend.update_timer(slot, "enable", time(6, 35))
    connect.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["lock_rejected", "nack", "unlock_timeout", "unlock_rejected", "old_readback", "different_other_day"]
)
async def test_rejected_unverified_and_partial_writes_are_not_retried(monkeypatch, failure):
    backend = JuraConnectBackend("127.0.0.1", conn_id="timer-test", machine_type="EF566", weekday_timers=True)
    client = MagicMock()
    client.connect.return_value.state = "CORRECT"
    sent = []

    def request(cmd, **_kwargs):
        sent.append(cmd)
        if cmd.startswith("@TM:"):
            return "@an:error" if failure == "nack" else "@tm:14"
        if cmd == "@TS:00" and failure == "unlock_timeout":
            raise TimeoutError
        if (cmd == "@TS:01" and failure == "lock_rejected") or (cmd == "@TS:00" and failure == "unlock_rejected"):
            return "@ts:error"
        return "@ts"

    def read(slot, **_kwargs):
        if sent and failure == "different_other_day":
            return {"14": "2406238000", "15": "2407008000"}.get(slot, RAW[slot])
        return RAW[slot]

    client.request.side_effect = request
    client.read_setting.side_effect = read
    monkeypatch.setattr(backend, "_make_client", lambda: client)
    monkeypatch.setattr(
        "custom_components.jura.backends.jura.wall_time", SimpleNamespace(monotonic=lambda: 0, sleep=lambda _: None)
    )
    with pytest.raises(JuraBackendError):
        await backend.update_timer("14", "set_time", time(6, 35))
    expected_writes = [] if failure == "lock_rejected" else ["@TM:14,240623800075"]
    assert sent == ["@TS:01", *expected_writes, "@TS:00"]
    client.close.assert_called_once()


async def test_unsupported_timer_reply_does_not_break_regular_poll():
    config = SimulatorConfig(require_user_accept=False)
    config.settings.update({**RAW, "1A": "bad"})
    with run_in_thread(config) as sim:
        backend = JuraConnectBackend(*sim.address, conn_id="timer-test", machine_type="EF566", weekday_timers=True)
        await backend.pair()
        result = await backend.fetch()
        assert result.handshake_state == "CORRECT"
        assert result.timers == {}
        assert result.counters


async def test_staged_time_survives_reload_and_invalidates_stale_snapshot():
    entry = timer_entry()
    backend = MagicMock()
    backend.update_timer = AsyncMock()
    c = JuraCoordinator(MagicMock(), entry, backend=backend)
    await c.async_load_timer_times()
    timers = {slot: TimerSlot.parse(raw) for slot, raw in RAW.items()}
    timers["14"] = TimerSlot(None, time(20, 5))
    c.data = snapshot(timers)
    c.last_poll_success = True
    c.last_update_success = True
    clock = WeekdayTimerTime(c, entry, "monday")
    switch = WeekdayTimerSwitch(c, entry, "monday")
    c.async_add_listener(clock._handle_coordinator_update)
    c.async_add_listener(switch._handle_coordinator_update)
    assert clock.available and switch.is_on is False and clock.native_value is None
    with pytest.raises(HomeAssistantError, match="Set a switch-on time"):
        await switch.async_turn_on()
    backend.update_timer.assert_not_awaited()

    backend.update_timer.return_value = TimerUpdate(timers, None)
    await clock.async_set_value(time(7, 15))
    backend.update_timer.assert_awaited_once_with("14", "set_time", time(7, 15))
    assert clock.native_value == time(7, 15) and switch.is_on is False
    restored = JuraCoordinator(c.hass, entry, backend=backend)
    await restored.async_load_timer_times()
    assert restored.timer_times["14"] == time(7, 15)

    backend.update_timer.return_value = TimerUpdate({**timers, "14": TimerSlot(time(7, 15), time(20, 5))}, None)
    await switch.async_turn_on()
    backend.update_timer.assert_awaited_with("14", "enable", time(7, 15))
    assert switch.is_on and clock.native_value == time(7, 15)

    backend.update_timer.side_effect = JuraBackendError("readback not confirmed")
    with pytest.raises(HomeAssistantError, match="readback"):
        await switch.async_turn_off()
    assert not clock.available and not switch.available and not c.data.timers
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await switch.async_turn_on()
