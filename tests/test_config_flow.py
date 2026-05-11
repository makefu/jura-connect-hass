"""Config-flow tests covering user / manual / pair branches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jura.backends.base import JuraAuthError, JuraBackendError
from custom_components.jura.config_flow import JuraConfigFlow
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


async def test_user_step_shows_form_with_discovered_machines(flow, discovered_machines):
    with patch(
        "custom_components.jura.config_flow.discover_machines",
        AsyncMock(return_value=discovered_machines),
    ):
        result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_user_step_falls_through_to_manual_when_discovery_empty(flow):
    with patch(
        "custom_components.jura.config_flow.discover_machines",
        AsyncMock(return_value=[]),
    ):
        result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "manual"


async def test_user_step_selecting_discovered_jumps_to_pair(flow, discovered_machines):
    with patch(
        "custom_components.jura.config_flow.discover_machines",
        AsyncMock(return_value=discovered_machines),
    ):
        await flow.async_step_user()
        result = await flow.async_step_user({"selection": "192.0.2.10"})
    # We're now in the pair step, awaiting user submission of the (empty) form.
    assert result["type"] == "form"
    assert result["step_id"] == "pair"
    assert flow._connection[CONF_HOST] == "192.0.2.10"


async def test_user_step_manual_choice_goes_to_manual(flow, discovered_machines):
    with patch(
        "custom_components.jura.config_flow.discover_machines",
        AsyncMock(return_value=discovered_machines),
    ):
        await flow.async_step_user()
        result = await flow.async_step_user({"selection": SELECTION_MANUAL})
    assert result["type"] == "form"
    assert result["step_id"] == "manual"


async def test_manual_step_collects_connection_and_advances(flow):
    result = await flow.async_step_manual(
        {
            CONF_HOST: "192.0.2.30",
            CONF_PORT: 51515,
            CONF_PIN: "",
            CONF_CONN_ID: "homeassistant-abcdef",
        }
    )
    assert result["step_id"] == "pair"
    assert flow._connection[CONF_HOST] == "192.0.2.30"
    assert flow._connection[CONF_CONN_ID] == "homeassistant-abcdef"


async def test_pair_step_creates_entry_on_success(flow):
    flow._connection = {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 51515,
        CONF_PIN: "",
        CONF_CONN_ID: "homeassistant-test",
    }
    backend = MagicMock()
    backend.pair = AsyncMock(return_value="a" * 64)
    with patch("custom_components.jura.config_flow.JuraConnectBackend", return_value=backend):
        result = await flow.async_step_pair({})
    assert result["type"] == "create_entry"
    assert result["data"][CONF_AUTH_HASH] == "a" * 64
    assert result["data"][CONF_HOST] == "192.0.2.10"


async def test_pair_step_handles_timeout(flow):
    flow._connection = {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 51515,
        CONF_PIN: "",
        CONF_CONN_ID: "homeassistant-test",
    }
    backend = MagicMock()
    backend.pair = AsyncMock(side_effect=JuraAuthError("pairing timed out"))
    with patch("custom_components.jura.config_flow.JuraConnectBackend", return_value=backend):
        result = await flow.async_step_pair({})
    assert result["type"] == "form"
    assert result["errors"]["base"] == "pairing_timeout"


async def test_pair_step_handles_connection_failure(flow):
    flow._connection = {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 51515,
        CONF_PIN: "",
        CONF_CONN_ID: "homeassistant-test",
    }
    backend = MagicMock()
    backend.pair = AsyncMock(side_effect=JuraBackendError("connection refused"))
    with patch("custom_components.jura.config_flow.JuraConnectBackend", return_value=backend):
        result = await flow.async_step_pair({})
    assert result["errors"]["base"] == "cannot_connect"


async def test_pair_step_first_call_just_shows_instructions(flow):
    flow._connection = {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 51515,
        CONF_PIN: "",
        CONF_CONN_ID: "homeassistant-test",
    }
    result = await flow.async_step_pair()  # no user_input
    assert result["type"] == "form"
    assert result["step_id"] == "pair"
    assert result["errors"] == {}
