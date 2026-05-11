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
# Earlier entries win when multiple alerts are active. The order roughly
# follows: critical errors first, then in-process states, then info / ready
# states. Names match jura_connect 0.9.0's _STATUS_BITS map; in particular
# `energy_safe` is a real alert now (the MSB-first decoding fix in 0.9.0
# made it surface as a distinct bit instead of being shadowed by
# `cappu_rinse_alert`).
STATE_PRIORITY: tuple[str, ...] = (
    # critical / blocking errors
    "please_wait",
    "fill_water",
    "fill_system",
    "fill_powder",
    "insert_tray",
    "insert_coffee_bin",
    "empty_grounds",
    "empty_tray",
    "close_powder_cover",
    "outlet_missing",
    "rear_cover_missing",
    "remove_water_tank",
    "error_status",
    "program_mode_status",
    # maintenance reminders (process severity)
    "filter_alert",
    "decalc_alert",
    "cleaning_alert",
    "cappu_rinse_alert",
    # in-progress
    "heating_up",
    "system_filling",
    "system_emptying",
    "ventilation_closed",
    "coffee_rinsing",
    "press_rinse",
    # alerts informational
    "milk_alert",
    "milk_sensor_error",
    "milk_sensor_no_signal",
    "no_milk_sensor",
    "no_beans",
    "not_enough_powder",
    "periphery_alert",
    # ready / idle states
    "coffee_ready",
    "enjoy_product",
    "energy_safe",
    "welcome",
    "goodbye",
)
STATE_IDLE = "idle"

# Alerts that we expose as dedicated binary_sensor entities. The value is the
# Home Assistant device_class for the entity. Problem-class alerts stay in
# the default category; running and informational alerts move to
# DIAGNOSTIC so the main device card surfaces only "needs attention" items.
ALERT_BINARY_SENSORS: dict[str, str | None] = {
    # blocking errors — main device-card section
    "fill_water": "problem",
    "fill_system": "problem",
    "fill_powder": "problem",
    "empty_grounds": "problem",
    "empty_tray": "problem",
    "insert_tray": "problem",
    "insert_coffee_bin": "problem",
    "outlet_missing": "problem",
    "rear_cover_missing": "problem",
    "close_powder_cover": "problem",
    "remove_water_tank": "problem",
    "error_status": "problem",
    "program_mode_status": "problem",
    "milk_alert": "problem",
    "milk_sensor_error": "problem",
    "no_milk_sensor": "problem",
    "milk_sensor_no_signal": "problem",
    # maintenance reminders (process severity in the library) — also problems
    # from the user's POV because they prompt action
    "filter_alert": "problem",
    "decalc_alert": "problem",
    "cleaning_alert": "problem",
    "cappu_rinse_alert": "problem",
    # in-progress / running
    "heating_up": "running",
    "coffee_rinsing": "running",
    "system_filling": "running",
    "system_emptying": "running",
    "please_wait": "running",
    "press_rinse": "running",
    "ventilation_closed": "running",
    # informational, low priority
    "no_beans": "problem",
    "not_enough_powder": "problem",
    "periphery_alert": None,
    "powder_product": None,
    "enjoy_product": None,
    "coffee_ready": None,
    "energy_safe": None,
    "welcome": None,
    "goodbye": None,
    "active_rf_filter": None,
    "remote_screen": None,
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
