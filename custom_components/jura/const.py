"""Constants for the Jura integration."""

from __future__ import annotations

DOMAIN = "jura"
NAME = "Jura Coffee"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_PIN = "pin"
CONF_CONN_ID = "conn_id"
CONF_AUTH_HASH = "auth_hash"
CONF_MACHINE_TYPE = "machine_type"

# Sentinel for "leave machine_type unset" in the machine_type config-flow step.
MACHINE_TYPE_NONE = "_none_"

DEFAULT_PORT = 51515
DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_PAIR_TIMEOUT = 60.0
DEFAULT_REQUEST_TIMEOUT = 6.0
DISCOVERY_UDP_TIMEOUT = 3.0
DISCOVERY_TCP_TIMEOUT = 0.4

# Selection-step sentinel for "skip discovery, type the IP myself".
SELECTION_MANUAL = "manual"

# Active alert -> human-readable state when picking a single overall machine state.
# Earlier entries win when multiple alerts are active. ``_NO_ALERT`` is the
# fallback when the alert tuple is empty.
STATE_PRIORITY: tuple[str, ...] = (
    "please_wait",
    "heating_up",
    "system_filling",
    "fill_system",
    "ventilation_closed",
    "rear_cover_missing",
    "outlet_missing",
    "close_powder_cover",
    "insert_tray",
    "insert_coffee_bin",
    "fill_water",
    "no_beans",
    "empty_grounds",
    "empty_tray",
    "milk_alert",
    "milk_sensor_error",
    "milk_sensor_no_signal",
    "no_milk_sensor",
    "coffee_rinsing",
    "coffee_ready",
    "welcome",
)
STATE_IDLE = "idle"

# Alerts that we expose as dedicated binary_sensor entities. The value is the
# Home Assistant device_class for the entity. Problem-class alerts stay in
# the default category; running and informational alerts move to
# DIAGNOSTIC so the main device card surfaces only "needs attention" items.
ALERT_BINARY_SENSORS: dict[str, str | None] = {
    "fill_water": "problem",
    "no_beans": "problem",
    "empty_grounds": "problem",
    "empty_tray": "problem",
    "insert_tray": "problem",
    "insert_coffee_bin": "problem",
    "outlet_missing": "problem",
    "rear_cover_missing": "problem",
    "close_powder_cover": "problem",
    "milk_alert": "problem",
    "milk_sensor_error": "problem",
    "heating_up": "running",
    "coffee_rinsing": "running",
    "system_filling": "running",
    "please_wait": "running",
    "coffee_ready": None,
    "welcome": None,
}

# Alerts whose device class is "running" or `None` are diagnostic by nature
# (heating up, ready, welcome) — they describe state rather than something
# the user must act on. Keep them off the main device card.
DIAGNOSTIC_ALERT_DEVICE_CLASSES: frozenset[str | None] = frozenset({"running", None})

# Maintenance counters & percent: shape of MaintenanceCounters / MaintenancePercent
# dataclass fields. Sensors are created for each key listed.
COUNTER_KEYS: tuple[str, ...] = (
    "cleaning",
    "filter_change",
    "decalc",
    "cappu_rinse",
    "coffee_rinse",
    "cappu_clean",
)

PERCENT_KEYS: tuple[str, ...] = (
    "cleaning",
    "filter_change",
    "decalc",
)

# 0xFF is the library's sentinel for "this percent indicator is absent / unknown".
PERCENT_ABSENT_SENTINEL = 0xFF
