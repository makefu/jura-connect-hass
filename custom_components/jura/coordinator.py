"""DataUpdateCoordinator for the Jura integration."""

from __future__ import annotations

import dataclasses
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .backends.base import JuraAuthError, JuraBackend, JuraBackendError, MachineSnapshot
from .backends.jura import JuraConnectBackend
from .brew import MachineDefinition, ProductDef, jura_connect_xml_dir, load_definition
from .const import (
    CONF_AUTH_HASH,
    CONF_CONN_ID,
    CONF_HOST,
    CONF_MACHINE_TYPE,
    CONF_PIN,
    CONF_PORT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class JuraCoordinator(DataUpdateCoordinator[MachineSnapshot]):
    """Polls one Jura machine and serves snapshots to entities."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        *,
        backend: JuraBackend | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            config_entry=config_entry,
        )
        self.backend: JuraBackend = backend or self._build_backend(config_entry)
        # The machine's brewable-product table (from jura_connect's bundled
        # XMLs), loaded once so the brew control-panel entities and the brew
        # button can resolve products + argument ranges without re-parsing.
        self.brew_definition: MachineDefinition | None = self._load_brew_definition(config_entry)
        # The single staged "next brew" selection shared by the whole machine.
        # ``product`` is a product Code; ``None`` for a parameter means "use
        # the machine's own default for the selected product". The brew
        # control-panel selects write here; the brew button reads from here.
        # Purely local — nothing is sent to the machine until the user presses
        # the button or calls the brew service.
        self.brew_selection: dict[str, int | str | None] = {
            "product": None,
            "strength": None,
            "water_ml": None,
            "temp": None,
        }
        if self.brew_definition is not None and self.brew_definition.products:
            self.brew_selection["product"] = self.brew_definition.products[0].code

    @staticmethod
    def _load_brew_definition(config_entry: ConfigEntry) -> MachineDefinition | None:
        """Resolve + parse the machine's product definition, or ``None``.

        A missing ``jura_connect`` package / unknown machine type is a
        configuration / version-skew condition, not a hard error: the brew
        control panel simply isn't created.
        """
        machine_type = config_entry.data.get(CONF_MACHINE_TYPE)
        if not machine_type:
            return None
        base_dir = jura_connect_xml_dir()
        if base_dir is None:
            return None
        return load_definition(machine_type, base_dir=base_dir)

    def selected_product(self) -> ProductDef | None:
        """Return the currently-selected :class:`ProductDef`, or ``None``."""
        if self.brew_definition is None:
            return None
        code = self.brew_selection.get("product")
        if code is None:
            return None
        for product in self.brew_definition.products:
            if product.code == code:
                return product
        return None

    @staticmethod
    def _build_backend(config_entry: ConfigEntry) -> JuraBackend:
        data = config_entry.data
        return JuraConnectBackend(
            address=data[CONF_HOST],
            port=data.get(CONF_PORT, DEFAULT_PORT),
            pin=data.get(CONF_PIN, ""),
            conn_id=data[CONF_CONN_ID],
            auth_hash=data.get(CONF_AUTH_HASH, ""),
            machine_type=data.get(CONF_MACHINE_TYPE),
        )

    async def _async_update_data(self) -> MachineSnapshot:
        try:
            return await self.backend.fetch()
        except JuraAuthError as err:
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except JuraBackendError as err:
            raise UpdateFailed(f"backend error: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"unexpected error: {err}") from err

    async def run_command(
        self,
        name: str,
        args: list[str] | tuple[str, ...] = (),
        *,
        allow_destructive: bool,
    ) -> dict[str, Any]:
        """Dispatch a named command, then refresh state so HA reflects the change."""
        try:
            result = await self.backend.run_named(
                name,
                args,
                allow_destructive=allow_destructive,
            )
        except JuraAuthError as err:
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except JuraBackendError as err:
            raise UpdateFailed(f"backend error: {err}") from err

        await self.async_request_refresh()
        return result

    async def write_setting(self, name: str, value: str) -> None:
        """Write a machine setting (validated by the profile) and reflect it
        in coordinator state immediately.

        ``async_request_refresh`` is debounced — relying on it alone
        leaves the writable entities showing the stale value for up to
        the polling interval. The backend's post-write read-back gives
        us the canonical stored form, so we push it into ``self.data``
        via ``async_set_updated_data`` and notify listeners in one
        shot.
        """
        try:
            new_hex = await self.backend.write_setting(name, value)
        except JuraAuthError as err:
            raise ConfigEntryAuthFailed(f"authentication failed: {err}") from err
        except JuraBackendError as err:
            raise UpdateFailed(f"backend error: {err}") from err

        if self.data is not None and new_hex:
            updated_settings = {**self.data.settings, name: new_hex}
            self.async_set_updated_data(dataclasses.replace(self.data, settings=updated_settings))
