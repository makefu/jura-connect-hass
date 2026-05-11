"""Shared fixtures for the Jura HA integration tests.

We stub the bits of Home Assistant we use so the integration code can
be imported and exercised without a real HA installation. This mirrors
the approach used by the ha_stadtbibliothek_opus template.
"""

from __future__ import annotations

import sys
from enum import Enum
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub homeassistant modules
# ---------------------------------------------------------------------------


def _make_module(name: str, **attrs: object) -> ModuleType:
    mod = ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# --- homeassistant.const ---
class Platform:
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"


_make_module("homeassistant.const", Platform=Platform)


# --- homeassistant.core ---
HomeAssistant = MagicMock
ServiceCall = MagicMock
ServiceResponse = dict | None


class SupportsResponse:
    NONE = "none"
    ONLY = "only"
    OPTIONAL = "optional"


_make_module(
    "homeassistant.core",
    HomeAssistant=HomeAssistant,
    ServiceCall=ServiceCall,
    ServiceResponse=ServiceResponse,
    SupportsResponse=SupportsResponse,
)


# --- homeassistant.config_entries ---
class ConfigFlowResult(dict):
    pass


class _ConfigFlowMeta(type):
    DOMAIN: str | None

    def __new__(mcs, name, bases, namespace, domain=None, **kwargs):
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        if domain is not None:
            cls.DOMAIN = domain
        return cls


class _FakeHass:
    """Hass stub with just the bits the config flow needs."""

    def __init__(self) -> None:
        self.data: dict = {}

    def async_create_task(self, coro):
        import asyncio

        return asyncio.ensure_future(coro)


class ConfigFlow(metaclass=_ConfigFlowMeta):
    DOMAIN: str | None = None
    hass = _FakeHass()

    async def async_set_unique_id(self, unique_id: str) -> None:
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self) -> None:
        pass

    def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None):
        return ConfigFlowResult(
            type="form",
            step_id=step_id,
            data_schema=data_schema,
            errors=errors or {},
            description_placeholders=description_placeholders or {},
        )

    def async_show_progress(self, *, step_id, progress_action, progress_task, description_placeholders=None):
        return ConfigFlowResult(
            type="progress",
            step_id=step_id,
            progress_action=progress_action,
            progress_task=progress_task,
            description_placeholders=description_placeholders or {},
        )

    def async_show_progress_done(self, *, next_step_id):
        return ConfigFlowResult(type="progress_done", next_step_id=next_step_id)

    def async_create_entry(self, *, title, data):
        return ConfigFlowResult(type="create_entry", title=title, data=data)

    def async_abort(self, *, reason):
        return ConfigFlowResult(type="abort", reason=reason)


class ConfigEntry:
    def __init__(self, entry_id="test_entry_id", data=None):
        self.entry_id = entry_id
        self.data = data or {}


_make_module(
    "homeassistant.config_entries",
    ConfigEntry=ConfigEntry,
    ConfigFlow=ConfigFlow,
    ConfigFlowResult=ConfigFlowResult,
)


# --- homeassistant.exceptions ---
class HomeAssistantError(Exception):
    pass


class ConfigEntryAuthFailed(HomeAssistantError):
    pass


_make_module(
    "homeassistant.exceptions",
    HomeAssistantError=HomeAssistantError,
    ConfigEntryAuthFailed=ConfigEntryAuthFailed,
)


# --- homeassistant.helpers ---
_make_module("homeassistant.helpers")
_make_module("homeassistant.helpers.typing", ConfigType=dict)


# --- homeassistant.helpers.entity ---
class DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for k, v in kwargs.items():
            setattr(self, k, v)


_make_module("homeassistant.helpers.entity", DeviceInfo=DeviceInfo)


# --- homeassistant.helpers.device_registry ---
class DeviceEntryType:
    SERVICE = "service"


_make_module("homeassistant.helpers.device_registry", DeviceEntryType=DeviceEntryType)


# --- homeassistant.helpers.entity_registry ---
class _EntityRegistryEntry:
    def __init__(self, config_entry_id=None):
        self.config_entry_id = config_entry_id


class _EntityRegistry:
    def __init__(self):
        self._entries: dict[str, _EntityRegistryEntry] = {}

    def async_get(self, entity_id: str) -> _EntityRegistryEntry | None:
        return self._entries.get(entity_id)

    def add(self, entity_id: str, config_entry_id: str | None = None) -> None:
        self._entries[entity_id] = _EntityRegistryEntry(config_entry_id)


_entity_registry_instance = _EntityRegistry()


def _er_async_get(hass):
    return _entity_registry_instance


_make_module(
    "homeassistant.helpers.entity_registry",
    async_get=_er_async_get,
)

_make_module("homeassistant.helpers.entity_platform", AddEntitiesCallback=list)


# --- homeassistant.helpers.update_coordinator ---
class UpdateFailed(Exception):
    pass


class DataUpdateCoordinator:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, update_interval, config_entry=None):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.config_entry = config_entry
        self.data = None
        self.last_update_success = True

    async def async_config_entry_first_refresh(self):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except Exception:
            self.last_update_success = False
            raise

    async def async_request_refresh(self):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except Exception:
            self.last_update_success = False
            raise

    async def _async_update_data(self):
        raise NotImplementedError


class CoordinatorEntity:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator


_make_module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=DataUpdateCoordinator,
    UpdateFailed=UpdateFailed,
    CoordinatorEntity=CoordinatorEntity,
)


# --- homeassistant.components.sensor ---
class SensorEntity:
    @property
    def unique_id(self):
        return getattr(self, "_attr_unique_id", None)

    @property
    def name(self):
        return getattr(self, "_attr_name", None)

    @property
    def icon(self):
        return getattr(self, "_attr_icon", None)

    @property
    def native_unit_of_measurement(self):
        return getattr(self, "_attr_native_unit_of_measurement", None)

    @property
    def device_class(self):
        return getattr(self, "_attr_device_class", None)


class SensorDeviceClass:
    MONETARY = "monetary"


_make_module(
    "homeassistant.components.sensor",
    SensorEntity=SensorEntity,
    SensorDeviceClass=SensorDeviceClass,
)


# --- homeassistant.components.binary_sensor ---
class BinarySensorEntity:
    @property
    def unique_id(self):
        return getattr(self, "_attr_unique_id", None)

    @property
    def name(self):
        return getattr(self, "_attr_name", None)

    @property
    def device_class(self):
        return getattr(self, "_attr_device_class", None)


class BinarySensorDeviceClass(str, Enum):
    PROBLEM = "problem"
    RUNNING = "running"
    CONNECTIVITY = "connectivity"


_make_module(
    "homeassistant.components.binary_sensor",
    BinarySensorEntity=BinarySensorEntity,
    BinarySensorDeviceClass=BinarySensorDeviceClass,
)


# ---------------------------------------------------------------------------
# Now we can import our integration code
# ---------------------------------------------------------------------------

from custom_components.jura.backends.base import (  # noqa: E402
    DiscoveredMachine,
    JuraBackend,
    MachineSnapshot,
)
from custom_components.jura.const import (  # noqa: E402
    CONF_AUTH_HASH,
    CONF_CONN_ID,
    CONF_HOST,
    CONF_PIN,
    CONF_PORT,
)


@pytest.fixture
def sample_snapshot() -> MachineSnapshot:
    return MachineSnapshot(
        address="192.0.2.10",
        conn_id="homeassistant-test",
        handshake_state="CORRECT",
        active_alerts=("heating_up",),
        counters={
            "cleaning": 21,
            "filter_change": 1,
            "decalc": 8,
            "cappu_rinse": 344,
            "coffee_rinse": 3617,
            "cappu_clean": 91,
        },
        percents={
            "cleaning": 80,
            "filter_change": 255,
            "decalc": 30,
        },
        raw_status_hex="0010000000000000",
    )


@pytest.fixture
def empty_snapshot() -> MachineSnapshot:
    return MachineSnapshot(
        address="192.0.2.10",
        conn_id="homeassistant-test",
        handshake_state="CORRECT",
        active_alerts=(),
        counters=dict.fromkeys(
            ("cleaning", "filter_change", "decalc", "cappu_rinse", "coffee_rinse", "cappu_clean"),
            0,
        ),
        percents={"cleaning": 100, "filter_change": 100, "decalc": 100},
        raw_status_hex="0000000000000000",
    )


@pytest.fixture
def mock_backend(sample_snapshot) -> JuraBackend:
    backend = AsyncMock(spec=JuraBackend)
    backend.fetch = AsyncMock(return_value=sample_snapshot)
    backend.pair = AsyncMock(return_value="a" * 64)
    backend.lock = AsyncMock()
    backend.unlock = AsyncMock()
    backend.run_named = AsyncMock(return_value={"name": "test", "value": "ok"})
    return backend


@pytest.fixture
def config_entry_data() -> dict:
    return {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 51515,
        CONF_PIN: "",
        CONF_CONN_ID: "homeassistant-test",
        CONF_AUTH_HASH: "a" * 64,
    }


@pytest.fixture
def fake_config_entry(config_entry_data) -> ConfigEntry:
    return ConfigEntry(entry_id="test_entry_id", data=config_entry_data)


@pytest.fixture
def discovered_machines() -> list[DiscoveredMachine]:
    return [
        DiscoveredMachine(address="192.0.2.10", name="Kitchen Jura", fw="TT237W V06.11", via="udp"),
        DiscoveredMachine(address="192.0.2.11", name="192.0.2.11", via="tcp"),
    ]
