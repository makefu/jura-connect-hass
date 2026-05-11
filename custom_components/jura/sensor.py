"""Sensor platform: state + maintenance counters + maintenance percent."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .backends.base import MachineSnapshot
from .const import (
    CONF_MACHINE_TYPE,
    COUNTER_KEYS,
    DOMAIN,
    PERCENT_KEYS,
    STATE_IDLE,
    STATE_PRIORITY,
)
from .coordinator import JuraCoordinator
from .entity import JuraEntity
from .serializers import percent_value, serialize_snapshot


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JuraCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities: list[SensorEntity] = [
        StateSensor(coordinator, config_entry),
        MachineTypeSensor(coordinator, config_entry),
        BrewTotalSensor(coordinator, config_entry),
    ]
    entities.extend(CounterSensor(coordinator, config_entry, key) for key in COUNTER_KEYS)
    entities.extend(PercentSensor(coordinator, config_entry, key) for key in PERCENT_KEYS)

    # Per-product brew counters are dynamic: the set of recipe codes a
    # machine exposes depends on its profile, so we create one entity
    # per name seen in the first snapshot. New recipes after setup are
    # rare; users can re-add the integration if they're added later.
    snapshot = coordinator.data
    if snapshot is not None:
        entities.extend(BrewCounterSensor(coordinator, config_entry, name) for name in snapshot.brews)

    # Read-only sensors for each machine setting (the writable select /
    # number entities live on their own platforms). Setup is gated on a
    # configured machine_type because settings come from the profile.
    machine_type = config_entry.data.get(CONF_MACHINE_TYPE)
    if machine_type:
        try:
            from jura_connect import load_profile
        except ImportError:  # pragma: no cover
            profile = None
        else:
            try:
                profile = load_profile(machine_type)
            except KeyError:
                profile = None
        if profile is not None:
            entities.extend(SettingValueSensor(coordinator, config_entry, setting) for setting in profile.settings)

    async_add_entities(entities)


def _snapshot(coordinator: JuraCoordinator) -> MachineSnapshot | None:
    return coordinator.data


def _derive_state(active_alerts: tuple[str, ...]) -> str:
    if not active_alerts:
        return STATE_IDLE
    alert_set = set(active_alerts)
    for name in STATE_PRIORITY:
        if name in alert_set:
            return name
    return next(iter(active_alerts))


class StateSensor(JuraEntity, SensorEntity):
    """Overall machine state derived from the active alert bits."""

    _attr_name = "Status"
    _attr_icon = "mdi:coffee-maker"

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_state"

    @property
    def native_value(self) -> str | None:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return None
        return _derive_state(snapshot.active_alerts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return {}
        return serialize_snapshot(snapshot)


class CounterSensor(JuraEntity, SensorEntity):
    """One maintenance counter (cleaning / decalc / filter / cappu / coffee rinse).

    Counters are diagnostic — they're useful for spotting "the machine has
    been cleaned 21 times" but not the day-to-day state most automations
    care about. Pushed to the DIAGNOSTIC section of the device card.
    """

    _attr_icon = "mdi:counter"
    _attr_state_class = "total_increasing"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: JuraCoordinator,
        config_entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._key = key
        # The "Cycles" prefix groups all counter entities together when
        # the device card sorts entities alphabetically within a category.
        self._attr_name = f"Cycles {key.replace('_', ' ')}"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_counter_{key}"

    @property
    def native_value(self) -> int | None:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return None
        return snapshot.counters.get(self._key)


class PercentSensor(JuraEntity, SensorEntity):
    """Maintenance percent indicator (0-100, or unavailable when sensor absent).

    These show "how soon does the machine need cleaning / descaling /
    filter change" — user-facing, so left in the default category.
    """

    _attr_icon = "mdi:percent-outline"
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self,
        coordinator: JuraCoordinator,
        config_entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._key = key
        # "Service" prefix clusters the three percent indicators together.
        self._attr_name = f"Service {key.replace('_', ' ')} level"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_percent_{key}"

    @property
    def native_value(self) -> int | None:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return None
        raw = snapshot.percents.get(self._key)
        if raw is None:
            return None
        return percent_value(raw)


class BrewCounterSensor(JuraEntity, SensorEntity):
    """One brew counter (espresso / coffee / cappuccino / …).

    The set of recipes the machine reports depends on its profile;
    instances are created dynamically from the first snapshot in
    ``async_setup_entry``.
    """

    _attr_icon = "mdi:coffee"
    _attr_native_unit_of_measurement = "brews"
    _attr_state_class = "total_increasing"

    def __init__(
        self,
        coordinator: JuraCoordinator,
        config_entry: ConfigEntry,
        product_name: str,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._product = product_name
        # "Brew" prefix clusters all per-recipe counters next to each other
        # and to the BrewTotalSensor on the device card.
        self._attr_name = f"Brew {product_name.replace('_', ' ')}"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brews_{product_name}"

    @property
    def native_value(self) -> int | None:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return None
        return snapshot.brews.get(self._product)


class BrewTotalSensor(JuraEntity, SensorEntity):
    """Lifetime total brews across all recipes (slot 0 of the @TR:32 table)."""

    # Named so it sorts at the end of the "Brew …" group on the device card.
    _attr_name = "Brew total"
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "brews"
    _attr_state_class = "total_increasing"

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_brews_total"

    @property
    def native_value(self) -> int | None:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return None
        return snapshot.brews_total


class SettingValueSensor(JuraEntity, SensorEntity):
    """Read-only display of one machine setting.

    Pairs with the writable :class:`SettingSelectEntity` / :class:`SettingNumberEntity`
    on the ``select`` / ``number`` platforms — those live in the
    configuration section and let the user change the value; this one
    sits in the main view as a plain sensor so the current value is
    always visible at a glance and friendly to template sensors,
    history graphs, and automation triggers.

    For item-driven settings (combobox / switch / item_slider) the
    state is the friendly name from the catalogue (``"english"``,
    ``"soft"``, ``"15min"``); for step_slider settings (water hardness)
    it's the decimal integer value.
    """

    _attr_icon = "mdi:cog-outline"

    def __init__(
        self,
        coordinator: JuraCoordinator,
        config_entry: ConfigEntry,
        setting_def,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._setting = setting_def
        # "Setting <name>" prefix groups all setting display sensors
        # together on the device card alongside the other prefix groups
        # ("Brew …", "Service …", "Cycles …").
        self._attr_name = f"Setting {setting_def.name.replace('_', ' ')}"
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_setting_value_{setting_def.name}"
        # For item-driven settings, prepare a hex->name lookup.
        self._value_to_name = {item.value.upper(): item.name for item in setting_def.items}

    @property
    def native_value(self) -> str | int | None:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return None
        raw = snapshot.settings.get(self._setting.name)
        if not raw:
            return None
        # Item-driven setting — look up the friendly name.
        if self._value_to_name:
            return self._value_to_name.get(raw.upper(), raw.upper())
        # step_slider — render the integer value.
        try:
            return int(raw, 16)
        except ValueError:
            return None


class MachineTypeSensor(JuraEntity, SensorEntity):
    """Reports the machine variant — the EF code + friendly name.

    The friendly name (e.g. "S8 (EB)") is the entity state; the EF
    code lives on ``machine_type`` in the attributes so automations can
    use either. Diagnostic by nature: set once at setup and never
    changes again.
    """

    _attr_name = "Machine type"
    _attr_icon = "mdi:coffee-maker-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: JuraCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_machine_type"

    @property
    def native_value(self) -> str | None:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return None
        if snapshot.machine_type_name:
            return snapshot.machine_type_name
        if snapshot.machine_type:
            return snapshot.machine_type
        return "unconfigured"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = _snapshot(self.coordinator)
        if snapshot is None:
            return {}
        return {
            "machine_type": snapshot.machine_type,
            "machine_type_name": snapshot.machine_type_name,
        }
