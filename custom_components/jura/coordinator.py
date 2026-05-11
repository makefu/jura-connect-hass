"""DataUpdateCoordinator for the Jura integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .backends.base import JuraAuthError, JuraBackend, JuraBackendError, MachineSnapshot
from .backends.jura import JuraConnectBackend
from .const import (
    CONF_AUTH_HASH,
    CONF_CONN_ID,
    CONF_HOST,
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

    @staticmethod
    def _build_backend(config_entry: ConfigEntry) -> JuraBackend:
        data = config_entry.data
        return JuraConnectBackend(
            address=data[CONF_HOST],
            port=data.get(CONF_PORT, DEFAULT_PORT),
            pin=data.get(CONF_PIN, ""),
            conn_id=data[CONF_CONN_ID],
            auth_hash=data.get(CONF_AUTH_HASH, ""),
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
