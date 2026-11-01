"""Regression tests for the session gap; all machine I/O is mocked.

The clock and sleep belong only to the coordinator module; the event loop's
clock and asyncio scheduling are never patched globally.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.jura import coordinator as coordinator_module
from custom_components.jura.backends.base import JuraBackendError
from custom_components.jura.coordinator import JuraCoordinator


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []
        self.asyncio_proxy = None

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


class AsyncioProxy:
    def __init__(self, sleep):
        self.sleep = sleep

    def __getattr__(self, name):
        return getattr(asyncio, name)


@pytest.fixture
def session_clock(monkeypatch):
    # Override the upstream legacy-suite fixture's zero gap for THESE tests.
    clock = FakeClock()
    clock.asyncio_proxy = AsyncioProxy(clock.sleep)
    monkeypatch.setattr(coordinator_module, "SESSION_COOLDOWN_SECONDS", 10)
    monkeypatch.setattr(coordinator_module, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(coordinator_module, "asyncio", clock.asyncio_proxy)
    return clock


def make_coordinator(mock_backend, fake_config_entry):
    coordinator = JuraCoordinator(AsyncMock(), fake_config_entry, backend=mock_backend)
    # Refresh-after-command is tested separately; do not add a hidden extra I/O.
    coordinator.async_request_refresh = AsyncMock()
    mock_backend.follow_brew = AsyncMock(return_value={"ack": "@tp", "frames": []})
    return coordinator


async def invoke(coordinator, pathway):
    if pathway == "fetch":
        return await coordinator._async_update_data()
    if pathway == "run_named":
        return await coordinator.run_command("test-noop", allow_destructive=False)
    if pathway == "follow_brew":
        return await coordinator.run_brew("mock-recipe", follow_seconds=1)
    if pathway == "write_setting":
        return await coordinator.write_setting("test-setting", "00")
    raise AssertionError(pathway)


async def test_first_session_has_no_artificial_delay(mock_backend, fake_config_entry, session_clock):
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    await coordinator._async_update_data()
    assert session_clock.sleeps == []
    assert coordinator._last_session_end == 100.0
    mock_backend.fetch.assert_awaited_once()


async def test_poll_to_brew_waits_ten_seconds_after_fetch_finishes(
    mock_backend, fake_config_entry, sample_snapshot, session_clock
):
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    calls = []

    async def fetch():
        calls.append(("fetch_start", session_clock.now))
        session_clock.now += 3
        return sample_snapshot

    async def brew(*args, **kwargs):
        calls.append(("brew_start", session_clock.now))
        return {"ack": "@tp", "frames": []}

    mock_backend.fetch.side_effect = fetch
    mock_backend.follow_brew.side_effect = brew
    await coordinator._async_update_data()
    await coordinator.run_brew("mock-recipe")
    assert calls == [("fetch_start", 100.0), ("brew_start", 113.0)]
    assert session_clock.sleeps == [10.0]
    mock_backend.follow_brew.assert_awaited_once()


@pytest.mark.parametrize("pathway", ["fetch", "run_named", "follow_brew", "write_setting"])
async def test_every_io_path_obeys_remaining_gap(mock_backend, fake_config_entry, session_clock, pathway):
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    coordinator._last_session_end = 96.0
    await invoke(coordinator, pathway)
    assert session_clock.sleeps == [6.0]
    assert coordinator._last_session_end == 106.0
    assert not coordinator._session_lock.locked()


async def test_elapsed_gap_does_not_sleep_again(mock_backend, fake_config_entry, session_clock):
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    coordinator._last_session_end = 80.0
    await coordinator._async_update_data()
    assert session_clock.sleeps == []
    assert coordinator._last_session_end == 100.0


@pytest.mark.parametrize("pathway", ["fetch", "run_named", "follow_brew", "write_setting"])
async def test_error_stamps_end_and_is_never_retried(mock_backend, fake_config_entry, session_clock, pathway):
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    operation = getattr(mock_backend, pathway)

    async def fail(*args, **kwargs):
        session_clock.now += 2
        raise JuraBackendError("invalid transport response")

    operation.side_effect = fail
    with pytest.raises((UpdateFailed, JuraBackendError)):
        await invoke(coordinator, pathway)
    assert operation.await_count == 1
    assert coordinator._last_session_end == 102.0
    assert not coordinator._session_lock.locked()
    assert coordinator._follow_brew_active is False
    # A later explicit operation still has to wait after this failed session.
    mock_backend.fetch.side_effect = None
    await coordinator._async_update_data()
    assert session_clock.sleeps == [10.0]


async def test_cancel_during_cooldown_opens_nothing_and_keeps_previous_end(
    mock_backend, fake_config_entry, session_clock
):
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    coordinator._last_session_end = 95.0
    sleeping = asyncio.Event()
    release = asyncio.Event()

    async def blocked_sleep(seconds):
        sleeping.set()
        await release.wait()

    session_clock.asyncio_proxy.sleep = blocked_sleep
    task = asyncio.create_task(coordinator._async_update_data())
    await sleeping.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mock_backend.fetch.await_count == 0
    assert coordinator._last_session_end == 95.0
    assert not coordinator._session_lock.locked()


async def test_cancel_inside_io_stamps_end_releases_lock_and_never_retries(
    mock_backend, fake_config_entry, session_clock
):
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    started = asyncio.Event()
    release = asyncio.Event()

    async def waiting_fetch():
        started.set()
        await release.wait()

    mock_backend.fetch.side_effect = waiting_fetch
    task = asyncio.create_task(coordinator._async_update_data())
    await started.wait()
    session_clock.now = 103.0
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mock_backend.fetch.await_count == 1
    assert coordinator._last_session_end == 103.0
    assert not coordinator._session_lock.locked()
    mock_backend.fetch.side_effect = None
    await coordinator._async_update_data()
    assert session_clock.sleeps == [10.0]


async def test_competing_operation_waits_for_lock_then_for_full_gap(
    mock_backend, fake_config_entry, sample_snapshot, session_clock
):
    coordinator = make_coordinator(mock_backend, fake_config_entry)
    fetch_started = asyncio.Event()
    finish_fetch = asyncio.Event()
    brew_starts = []

    async def fetch():
        fetch_started.set()
        await finish_fetch.wait()
        session_clock.now = 104.0
        return sample_snapshot

    async def brew(*args, **kwargs):
        brew_starts.append(session_clock.now)
        return {"ack": "@tp", "frames": []}

    mock_backend.fetch.side_effect = fetch
    mock_backend.follow_brew.side_effect = brew
    poll = asyncio.create_task(coordinator._async_update_data())
    await fetch_started.wait()
    command = asyncio.create_task(coordinator.run_brew("mock-recipe"))
    await asyncio.sleep(0)
    assert mock_backend.follow_brew.await_count == 0
    finish_fetch.set()
    await asyncio.gather(poll, command)
    assert brew_starts == [114.0]
    assert session_clock.sleeps == [10.0]
    assert mock_backend.fetch.await_count == 1
    assert mock_backend.follow_brew.await_count == 1
