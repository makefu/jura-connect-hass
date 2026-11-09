"""Constants for the Jura integration."""

from __future__ import annotations

DOMAIN = "jura"
NAME = "Jura Connect"

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
# Config-entry option (hidden "Advanced" field in the options flow): when
# true (the default since 1.0.0) entities keep showing their last-known
# value while the machine is unreachable instead of going "unavailable".
# Set it to false to restore the pre-1.0.0 behavior where every poll
# confirmed OFFLINE flipped all entities except the connectivity sensor.
CONF_RETAIN_WHEN_OFFLINE = "retain_when_offline"
DEFAULT_RETAIN_WHEN_OFFLINE = True

# Config-entry options for the two long coordinator sleeps. Both are also
# accepted in entry.data (YAML-era overrides); entry.options wins.
# Quiet period the coordinator leaves after each dongle TCP session before
# opening the next one; Wi-Fi Connect can reset an immediately reopened
# session. 0 disables the gap.
CONF_SESSION_COOLDOWN_SECONDS = "session_cooldown_seconds"
# Upper bound for following the @TV: progress stream of a dispatched brew;
# the session stays open for the whole dispense up to this many seconds.
CONF_BREW_FOLLOW_SECONDS = "brew_follow_seconds"

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
    "descale_alert",
    "cleaning_alert",
    "cappu_rinse_alert",
    # in-progress
    "heating_up",
    "system_filling",
    "system_emptying",
    "ventilation_closed",
    "coffee_rinsing",
    "press_rinse",
    "switch_off_delay_active",
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

# Active alerts during which the machine cannot start any drink: it is busy
# (heating, rinsing, filling) or waits for the user (water, tray, grounds,
# covers, errors). While any is active the Brew button is unavailable and
# brew requests are refused with ``machine_not_ready`` instead of being sent
# -- the machine ACKs a recipe while heating but never brews it, and
# following that phantom brew held the single dongle session for minutes.
# Maintenance reminders (descale/filter/cleaning alerts) and bean/milk hints
# do not block: the machine still brews. On top of this list, the profile's
# per-product blocking (MachineSnapshot.blocked_products) is always checked
# for the drink actually requested.
BREW_BLOCKING_ALERTS: frozenset[str] = frozenset(
    {
        "heating_up",
        "system_filling",
        "system_emptying",
        "coffee_rinsing",
        "press_rinse",
        "please_wait",
        "goodbye",
        "switch_off_delay_active",
        "fill_water",
        "fill_system",
        "empty_grounds",
        "empty_tray",
        "insert_tray",
        "insert_coffee_bin",
        "outlet_missing",
        "rear_cover_missing",
        "remove_water_tank",
        "error_status",
        "program_mode_status",
        # profile Type="block" alerts: the library documents these as stopping
        # every product; omitting them re-opens the ACK-but-never-dispense
        # phantom brew for drinks involving the powder chute / covers.
        "close_powder_cover",
        "fill_powder",
        "close_tab",
        "close_front_cover",
    }
)

# Poll faster while the machine is in a transient busy state (heating up,
# rinsing, ...), so the Brew button comes back soon after it is ready.
BUSY_SCAN_INTERVAL = 15  # seconds
BUSY_ALERTS: frozenset[str] = frozenset(
    {"heating_up", "system_filling", "system_emptying", "coffee_rinsing", "press_rinse", "please_wait", "goodbye"}
)

# A brew whose recipe the machine ACKed must show progress within this many
# seconds, else the integration stops following it (the machine did not
# start, e.g. still heating up) instead of holding the dongle session for
# the whole follow window.
BREW_START_TIMEOUT = 30.0  # seconds

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
    "descale_alert": "problem",
    "cleaning_alert": "problem",
    "cappu_rinse_alert": "problem",
    # "Clean milk system" prompt (bit 41; J.O.E. shows it after every milk
    # drink). The library emits it as cappu_clean_alert from both the profile
    # XML and the 0.14.0 fallback codebook.
    "cappu_clean_alert": "problem",
    # in-progress / running
    "switch_off_delay_active": "running",
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
    "descale",
    "cappu_rinse",
    "coffee_rinse",
    "cappu_clean",
)

PERCENT_KEYS: tuple[str, ...] = (
    "cleaning",
    "filter_change",
    "descale",
)

# 0xFF is the library's sentinel for "this percent indicator is absent / unknown".
PERCENT_ABSENT_SENTINEL = 0xFF
