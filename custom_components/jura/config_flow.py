"""Config flow for the Jura integration.

Three-step flow:

1. ``user``   — runs discovery; offers a dropdown of found machines plus a
   literal "manual" option. With no hits, jumps straight to ``manual``.
2. ``manual`` — host / port / pin / conn_id text fields.
3. ``pair``   — calls :meth:`JuraConnectBackend.pair` and stores the
   resulting auth-hash on the config entry.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .backends.base import DiscoveredMachine, JuraAuthError, JuraBackendError
from .backends.jura import JuraConnectBackend, discover_machines
from .const import (
    CONF_AUTH_HASH,
    CONF_CONN_ID,
    CONF_HOST,
    CONF_PIN,
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
    SELECTION_MANUAL,
)

_LOGGER = logging.getLogger(__name__)


def _default_conn_id() -> str:
    return f"homeassistant-{uuid.uuid4().hex[:8]}"


class JuraConfigFlow(ConfigFlow, domain=DOMAIN):
    """UI-driven setup for one Jura machine."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: list[DiscoveredMachine] = []
        self._connection: dict[str, Any] = {}

    # -- user / discovery --------------------------------------------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self._discovered:
            self._discovered = await discover_machines()

        if not self._discovered:
            return await self.async_step_manual()

        if user_input is not None:
            choice = user_input["selection"]
            if choice == SELECTION_MANUAL:
                return await self.async_step_manual()
            for machine in self._discovered:
                if machine.address == choice:
                    self._connection = {
                        CONF_HOST: machine.address,
                        CONF_PORT: DEFAULT_PORT,
                        CONF_PIN: "",
                        CONF_CONN_ID: _default_conn_id(),
                    }
                    return await self.async_step_pair()
            # The chosen address is no longer in the list — re-run discovery.
            self._discovered = []
            return await self.async_step_user()

        options = {m.address: f"{m.name} ({m.address}, via {m.via})" for m in self._discovered}
        options[SELECTION_MANUAL] = "Enter manually"

        schema = vol.Schema({vol.Required("selection"): vol.In(options)})
        return self.async_show_form(step_id="user", data_schema=schema)

    # -- manual ------------------------------------------------------------
    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._connection = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
                CONF_PIN: user_input.get(CONF_PIN, ""),
                CONF_CONN_ID: user_input.get(CONF_CONN_ID) or _default_conn_id(),
            }
            return await self.async_step_pair()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_PIN, default=""): str,
                vol.Optional(CONF_CONN_ID, default=_default_conn_id()): str,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    # -- pair --------------------------------------------------------------
    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is None:
            # First entry: just show the "press OK on the machine" instruction.
            return self.async_show_form(step_id="pair", data_schema=vol.Schema({}))

        unique_id = f"{self._connection[CONF_HOST]}|{self._connection[CONF_CONN_ID]}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        backend = JuraConnectBackend(
            address=self._connection[CONF_HOST],
            port=self._connection[CONF_PORT],
            pin=self._connection[CONF_PIN],
            conn_id=self._connection[CONF_CONN_ID],
        )
        try:
            new_hash = await backend.pair()
        except JuraAuthError as err:
            _LOGGER.warning("pairing failed: %s", err)
            msg = str(err).lower()
            if "timed out" in msg or "timeout" in msg:
                errors["base"] = "pairing_timeout"
            elif "wrong_pin" in msg or "pin" in msg:
                errors["base"] = "wrong_pin"
            elif "wrong_hash" in msg:
                errors["base"] = "wrong_hash"
            else:
                errors["base"] = "unknown"
        except JuraBackendError as err:
            _LOGGER.warning("pairing connection failed: %s", err)
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("unexpected error during pairing")
            errors["base"] = "unknown"
        else:
            data = {**self._connection, CONF_AUTH_HASH: new_hash}
            title = f"Jura {self._connection[CONF_HOST]}"
            return self.async_create_entry(title=title, data=data)

        return self.async_show_form(step_id="pair", data_schema=vol.Schema({}), errors=errors)
