"""Config flow for the Jura integration.

Step graph::

    user ─────► discovery_progress ─► select ─► pair_progress ─► finish
       │                │ (no hits)                  │ (fail)
       │                ▼                            ▼
       └────────────► manual ────────────────────► pair_failed ─► (retry)

Discovery and pairing are both background tasks shown via
``async_show_progress`` so the user sees a spinner and clear text while
the machine is being contacted instead of an empty submit-only form.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .backends.base import DiscoveredMachine, JuraAuthError, JuraBackendError
from .backends.jura import JuraConnectBackend, discover_machines, machine_type_from_article
from .const import (
    CONF_AUTH_HASH,
    CONF_CONN_ID,
    CONF_HOST,
    CONF_MACHINE_TYPE,
    CONF_PIN,
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
    MACHINE_TYPE_NONE,
    SELECTION_MANUAL,
)

_LOGGER = logging.getLogger(__name__)

MODE_DISCOVERY = "discovery"
MODE_MANUAL = "manual"


def _default_conn_id() -> str:
    return f"homeassistant-{uuid.uuid4().hex[:8]}"


class JuraConfigFlow(ConfigFlow, domain=DOMAIN):
    """UI-driven setup for one Jura machine."""

    # Bumped to 2 in v0.3.0: config entries gained a machine_type field
    # and there's deliberately no migration path — old entries will fail
    # to load and prompt the user to re-add.
    VERSION = 2

    def __init__(self) -> None:
        self._discovered: list[DiscoveredMachine] = []
        self._connection: dict[str, Any] = {}
        self._discover_task: asyncio.Task | None = None
        self._pair_task: asyncio.Task | None = None
        self._last_error_message: str = ""
        self._last_error_code: str = "unknown"
        self._auth_hash: str = ""
        self._auto_machine_type: str | None = None

    # ------------------------------------------------------------------ #
    # Step 1: choose discovery vs manual
    # ------------------------------------------------------------------ #
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input["mode"] == MODE_DISCOVERY:
                return await self.async_step_discovery_progress()
            return await self.async_step_manual()

        schema = vol.Schema(
            {
                vol.Required("mode", default=MODE_DISCOVERY): vol.In(
                    {
                        MODE_DISCOVERY: "Search for machines on the network",
                        MODE_MANUAL: "Enter the machine's IP address manually",
                    }
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    # ------------------------------------------------------------------ #
    # Step 2a: discovery with progress spinner
    # ------------------------------------------------------------------ #
    async def async_step_discovery_progress(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._discover_task is None:
            self._discover_task = self.hass.async_create_task(discover_machines())

        if not self._discover_task.done():
            return self.async_show_progress(
                step_id="discovery_progress",
                progress_action="discovering",
                progress_task=self._discover_task,
            )

        try:
            self._discovered = self._discover_task.result()
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("discovery failed")
            self._last_error_message = str(err)
            self._discover_task = None
            return self.async_show_progress_done(next_step_id="manual")

        self._discover_task = None
        if not self._discovered:
            return self.async_show_progress_done(next_step_id="no_machines_found")
        return self.async_show_progress_done(next_step_id="select")

    async def async_step_no_machines_found(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Tell the user discovery turned up nothing; offer manual fallback."""
        if user_input is not None:
            return await self.async_step_manual()
        return self.async_show_form(
            step_id="no_machines_found",
            data_schema=vol.Schema({}),
        )

    # ------------------------------------------------------------------ #
    # Step 2b: pick from the discovered list
    # ------------------------------------------------------------------ #
    async def async_step_select(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            choice = user_input["address"]
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
                    # Best-effort: derive the EF code from the discovery
                    # broadcast's article number so the machine_type
                    # step can default to the right value.
                    self._auto_machine_type = machine_type_from_article(machine.article_number)
                    return await self.async_step_pair_progress()
            return await self.async_step_manual()

        options = {m.address: f"{m.name} ({m.address}, via {m.via})" for m in self._discovered}
        options[SELECTION_MANUAL] = "None of these — enter manually"
        return self.async_show_form(
            step_id="select",
            data_schema=vol.Schema({vol.Required("address"): vol.In(options)}),
        )

    # ------------------------------------------------------------------ #
    # Step 3: manual IP entry
    # ------------------------------------------------------------------ #
    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._connection = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
                CONF_PIN: user_input.get(CONF_PIN, ""),
                CONF_CONN_ID: user_input.get(CONF_CONN_ID) or _default_conn_id(),
            }
            return await self.async_step_pair_progress()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_PIN, default=""): str,
                vol.Optional(CONF_CONN_ID, default=_default_conn_id()): str,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema)

    # ------------------------------------------------------------------ #
    # Step 4: pairing with progress spinner
    # ------------------------------------------------------------------ #
    async def async_step_pair_progress(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._pair_task is None:
            backend = JuraConnectBackend(
                address=self._connection[CONF_HOST],
                port=self._connection[CONF_PORT],
                pin=self._connection[CONF_PIN],
                conn_id=self._connection[CONF_CONN_ID],
            )
            self._pair_task = self.hass.async_create_task(backend.pair())

        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair_progress",
                progress_action="pairing",
                progress_task=self._pair_task,
                description_placeholders={"host": self._connection[CONF_HOST]},
            )

        # Task complete — capture the outcome before nulling out the slot
        # so the next attempt creates a fresh task.
        task = self._pair_task
        self._pair_task = None

        exc = task.exception()
        if exc is not None:
            self._classify_pair_error(exc)
            return self.async_show_progress_done(next_step_id="pair_failed")

        new_hash = task.result()
        if not new_hash:
            self._last_error_code = "unknown"
            self._last_error_message = "pairing returned no auth hash"
            return self.async_show_progress_done(next_step_id="pair_failed")
        self._auth_hash = new_hash
        return self.async_show_progress_done(next_step_id="machine_type")

    def _classify_pair_error(self, err: BaseException) -> None:
        """Map the backend exception to a user-facing error code + message.

        Note: JuraAuthError is a subclass of JuraBackendError, so it must
        be checked first.
        """
        self._last_error_message = str(err)
        if isinstance(err, JuraAuthError):
            msg = str(err).lower()
            if "timed out" in msg or "timeout" in msg:
                self._last_error_code = "pairing_timeout"
            elif "aborted" in msg or "rejected: aborted" in msg:
                self._last_error_code = "pairing_aborted"
            elif "wrong_pin" in msg:
                self._last_error_code = "wrong_pin"
            elif "wrong_hash" in msg:
                self._last_error_code = "wrong_hash"
            elif "rejected" in msg:
                self._last_error_code = "pairing_rejected"
            else:
                self._last_error_code = "unknown"
            return
        if isinstance(err, JuraBackendError):
            self._last_error_code = "cannot_connect"
            return
        self._last_error_code = "unknown"

    async def async_step_pair_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the failure with a descriptive message; submitting retries."""
        if user_input is not None:
            return await self.async_step_pair_progress()
        return self.async_show_form(
            step_id="pair_failed",
            data_schema=vol.Schema({}),
            errors={"base": self._last_error_code},
            description_placeholders={
                "error": self._last_error_message or "(no details)",
                "host": self._connection.get(CONF_HOST, "?"),
            },
        )

    async def async_step_machine_type(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask the user to pick a machine variant (or accept the auto-detected one)."""
        from jura_connect import known_machine_names

        if user_input is not None:
            choice = user_input[CONF_MACHINE_TYPE]
            self._connection[CONF_MACHINE_TYPE] = None if choice == MACHINE_TYPE_NONE else choice
            return await self.async_step_finish()

        options: dict[str, str] = {MACHINE_TYPE_NONE: "Use baseline (no profile)"}
        for friendly, code in known_machine_names():
            options[code] = f"{friendly} [{code}]"

        default = self._auto_machine_type if self._auto_machine_type in options else MACHINE_TYPE_NONE

        schema = vol.Schema(
            {
                vol.Required(CONF_MACHINE_TYPE, default=default): vol.In(options),
            }
        )
        return self.async_show_form(
            step_id="machine_type",
            data_schema=schema,
            description_placeholders={
                "detected": self._auto_machine_type or "none",
            },
        )

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Final transition: persist the entry."""
        unique_id = f"{self._connection[CONF_HOST]}|{self._connection[CONF_CONN_ID]}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        data = {**self._connection, CONF_AUTH_HASH: self._auth_hash}
        title = f"Jura {self._connection[CONF_HOST]}"
        return self.async_create_entry(title=title, data=data)
