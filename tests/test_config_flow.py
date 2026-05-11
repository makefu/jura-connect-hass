"""Config-flow tests covering the user-choice / discovery / manual / pair graph."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jura.backends.base import JuraAuthError, JuraBackendError
from custom_components.jura.config_flow import (
    MODE_DISCOVERY,
    MODE_MANUAL,
    JuraConfigFlow,
)
from custom_components.jura.const import (
    CONF_AUTH_HASH,
    CONF_CONN_ID,
    CONF_HOST,
    CONF_PIN,
    CONF_PORT,
    SELECTION_MANUAL,
)


@pytest.fixture
def flow() -> JuraConfigFlow:
    return JuraConfigFlow()


# ---------------------------------------------------------------------------
# Step 1: user-choice
# ---------------------------------------------------------------------------


async def test_user_step_shows_mode_choice(flow):
    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_user_step_discovery_choice_runs_discovery(flow, discovered_machines):
    async def fake_discover():
        return discovered_machines

    with patch("custom_components.jura.config_flow.discover_machines", fake_discover):
        # First call: progress (task not yet awaited)
        result = await flow.async_step_user({"mode": MODE_DISCOVERY})
        assert result["type"] == "progress"
        # Drive the task to completion, then re-enter the progress step.
        await result["progress_task"]
        result = await flow.async_step_discovery_progress()
        assert result["type"] == "progress_done"
        assert result["next_step_id"] == "select"


async def test_user_step_manual_choice_goes_to_manual_form(flow):
    result = await flow.async_step_user({"mode": MODE_MANUAL})
    assert result["type"] == "form"
    assert result["step_id"] == "manual"


# ---------------------------------------------------------------------------
# Step 2a: discovery progress
# ---------------------------------------------------------------------------


async def test_discovery_progress_finds_machines(flow, discovered_machines):
    async def fake_discover():
        return discovered_machines

    with patch("custom_components.jura.config_flow.discover_machines", fake_discover):
        first = await flow.async_step_discovery_progress()
        assert first["type"] == "progress"
        await first["progress_task"]
        second = await flow.async_step_discovery_progress()
        assert second["type"] == "progress_done"
        assert second["next_step_id"] == "select"
        assert flow._discovered == discovered_machines


async def test_discovery_progress_no_machines_routes_to_no_machines_found(flow):
    async def fake_discover():
        return []

    with patch("custom_components.jura.config_flow.discover_machines", fake_discover):
        first = await flow.async_step_discovery_progress()
        await first["progress_task"]
        second = await flow.async_step_discovery_progress()
        assert second["next_step_id"] == "no_machines_found"


async def test_discovery_progress_exception_routes_to_manual(flow):
    async def boom():
        raise RuntimeError("network down")

    with patch("custom_components.jura.config_flow.discover_machines", boom):
        first = await flow.async_step_discovery_progress()
        # Drive the failing task to completion (await captures the exception).
        try:
            await first["progress_task"]
        except RuntimeError:
            pass
        second = await flow.async_step_discovery_progress()
        assert second["next_step_id"] == "manual"


async def test_no_machines_found_advances_to_manual(flow):
    result = await flow.async_step_no_machines_found()
    assert result["type"] == "form"
    assert result["step_id"] == "no_machines_found"

    result = await flow.async_step_no_machines_found({})
    assert result["step_id"] == "manual"


# ---------------------------------------------------------------------------
# Step 2b: select
# ---------------------------------------------------------------------------


async def test_select_picking_discovered_starts_pair(flow, discovered_machines):
    flow._discovered = discovered_machines

    backend = MagicMock()
    backend.pair = AsyncMock(return_value="a" * 64)
    with patch("custom_components.jura.config_flow.JuraConnectBackend", return_value=backend):
        result = await flow.async_step_select({"address": "192.0.2.10"})
    assert result["type"] == "progress"
    assert result["step_id"] == "pair_progress"
    assert flow._connection[CONF_HOST] == "192.0.2.10"


async def test_select_manual_option_routes_to_manual(flow, discovered_machines):
    flow._discovered = discovered_machines
    result = await flow.async_step_select({"address": SELECTION_MANUAL})
    assert result["step_id"] == "manual"


async def test_select_form_lists_discovered_plus_manual_fallback(flow, discovered_machines):
    flow._discovered = discovered_machines
    result = await flow.async_step_select()
    assert result["type"] == "form"
    assert result["step_id"] == "select"


# ---------------------------------------------------------------------------
# Step 3: manual
# ---------------------------------------------------------------------------


async def test_manual_step_collects_connection_and_starts_pair(flow):
    backend = MagicMock()
    backend.pair = AsyncMock(return_value="a" * 64)
    with patch("custom_components.jura.config_flow.JuraConnectBackend", return_value=backend):
        result = await flow.async_step_manual(
            {
                CONF_HOST: "192.0.2.30",
                CONF_PORT: 51515,
                CONF_PIN: "",
                CONF_CONN_ID: "homeassistant-abcdef",
            }
        )
    assert result["type"] == "progress"
    assert result["step_id"] == "pair_progress"
    assert flow._connection[CONF_HOST] == "192.0.2.30"


# ---------------------------------------------------------------------------
# Step 4: pair progress + result handling
# ---------------------------------------------------------------------------


async def _drive_pair(flow, backend_factory):
    """Run pair_progress through the spinner -> task completion -> done flow."""
    with patch("custom_components.jura.config_flow.JuraConnectBackend", side_effect=backend_factory):
        first = await flow.async_step_pair_progress()
        if first["type"] == "progress":
            try:
                await first["progress_task"]
            except Exception:
                pass
        return await flow.async_step_pair_progress()


async def test_pair_progress_success_advances_to_finish(flow):
    flow._connection = {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 51515,
        CONF_PIN: "",
        CONF_CONN_ID: "homeassistant-test",
    }

    def factory(**_kw):
        backend = MagicMock()
        backend.pair = AsyncMock(return_value="a" * 64)
        return backend

    result = await _drive_pair(flow, factory)
    assert result["type"] == "progress_done"
    assert result["next_step_id"] == "finish"
    assert flow._auth_hash == "a" * 64


async def test_finish_step_creates_entry(flow):
    flow._connection = {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 51515,
        CONF_PIN: "",
        CONF_CONN_ID: "homeassistant-test",
    }
    flow._auth_hash = "a" * 64
    result = await flow.async_step_finish()
    assert result["type"] == "create_entry"
    assert result["data"][CONF_AUTH_HASH] == "a" * 64
    assert result["data"][CONF_HOST] == "192.0.2.10"


@pytest.mark.parametrize(
    ("err", "expected_code"),
    [
        (JuraAuthError("pairing timed out: no @hp4/@hp5 reply within 60.0s"), "pairing_timeout"),
        (JuraAuthError("pairing rejected: ABORTED"), "pairing_aborted"),
        (JuraAuthError("pairing rejected: WRONG_PIN"), "wrong_pin"),
        (JuraAuthError("pairing rejected: WRONG_HASH"), "wrong_hash"),
        (JuraAuthError("pairing rejected: SOMETHING_ELSE"), "pairing_rejected"),
        (JuraBackendError("connection refused"), "cannot_connect"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
async def test_pair_progress_classifies_errors(flow, err, expected_code):
    flow._connection = {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 51515,
        CONF_PIN: "",
        CONF_CONN_ID: "homeassistant-test",
    }

    def factory(**_kw):
        backend = MagicMock()
        backend.pair = AsyncMock(side_effect=err)
        return backend

    result = await _drive_pair(flow, factory)
    assert result["type"] == "progress_done"
    assert result["next_step_id"] == "pair_failed"
    assert flow._last_error_code == expected_code
    assert flow._last_error_message  # non-empty


async def test_pair_failed_form_surfaces_error_message(flow):
    flow._connection = {CONF_HOST: "192.0.2.10"}
    flow._last_error_code = "pairing_timeout"
    flow._last_error_message = "no @hp4/@hp5 reply within 60.0s"
    result = await flow.async_step_pair_failed()
    assert result["type"] == "form"
    assert result["step_id"] == "pair_failed"
    assert result["errors"]["base"] == "pairing_timeout"
    assert "no @hp4/@hp5 reply" in result["description_placeholders"]["error"]
    assert result["description_placeholders"]["host"] == "192.0.2.10"


async def test_pair_failed_submit_retries(flow):
    flow._connection = {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 51515,
        CONF_PIN: "",
        CONF_CONN_ID: "homeassistant-test",
    }
    backend = MagicMock()
    # Use a never-completing future so we capture the "progress" return.
    pending = asyncio.get_event_loop().create_future()
    backend.pair = AsyncMock(return_value=pending)
    with patch("custom_components.jura.config_flow.JuraConnectBackend", return_value=backend):
        result = await flow.async_step_pair_failed({})
    assert result["type"] == "progress"
    assert result["step_id"] == "pair_progress"
    pending.cancel()
