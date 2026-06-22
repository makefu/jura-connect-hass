"""Internationalization (i18n) support for entity names and alert states."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Fallback English strings — used when no translation file is available.
_ENGLISH_STRINGS: dict[str, Any] = {
    "entities": {
        "sensor": {
            "status": "Status",
            "brew_total": "Brew total",
            "machine_type": "Machine type",
        },
        "binary_sensor": {
            "connectivity": "Connectivity",
        },
    },
    "alerts": {
        "fill_water": "Fill water",
        "fill_system": "Fill system",
        "fill_powder": "Fill powder",
        "insert_tray": "Insert tray",
        "insert_coffee_bin": "Insert coffee bin",
        "empty_grounds": "Empty grounds",
        "empty_tray": "Empty tray",
        "close_powder_cover": "Close powder cover",
        "outlet_missing": "Outlet missing",
        "rear_cover_missing": "Rear cover missing",
        "remove_water_tank": "Remove water tank",
        "error_status": "Error status",
        "program_mode_status": "Program mode status",
        "filter_alert": "Filter alert",
        "decalc_alert": "Decalc alert",
        "cleaning_alert": "Cleaning alert",
        "cappu_rinse_alert": "Cappu rinse alert",
        "heating_up": "Heating up",
        "coffee_rinsing": "Coffee rinsing",
        "system_filling": "System filling",
        "system_emptying": "System emptying",
        "ventilation_closed": "Ventilation closed",
        "press_rinse": "Press rinse",
        "milk_alert": "Milk alert",
        "milk_sensor_error": "Milk sensor error",
        "milk_sensor_no_signal": "Milk sensor no signal",
        "no_milk_sensor": "No milk sensor",
        "no_beans": "No beans",
        "not_enough_powder": "Not enough powder",
        "periphery_alert": "Periphery alert",
        "powder_product": "Powder product",
        "enjoy_product": "Enjoy product",
        "coffee_ready": "Coffee ready",
        "energy_safe": "Energy safe",
        "welcome": "Welcome",
        "goodbye": "Goodbye",
        "active_rf_filter": "Active RF filter",
        "remote_screen": "Remote screen",
        "please_wait": "Please wait",
    },
    "counters": {
        "cleaning": "Cleaning",
        "filter_change": "Filter change",
        "decalc": "Decalc",
        "cappu_rinse": "Cappu rinse",
        "coffee_rinse": "Coffee rinse",
        "cappu_clean": "Cappu clean",
    },
    "percents": {
        "cleaning": "Cleaning",
        "filter_change": "Filter change",
        "decalc": "Decalc",
    },
}

# Translation cache per locale. Lazy-initialized on first use.
_translators: dict[str, Translator] = {}


class Translator:
    """Handles translation lookups with fallback to English."""

    def __init__(self, locale: str = "en") -> None:
        self.locale = locale
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load translation file for the locale.

        Falls back to English if locale file not found or locale == 'en'.
        """
        if self.locale == "en":
            self._data = _ENGLISH_STRINGS
            return

        # Try to load locale-specific JSON file from translations/ directory.
        translation_file = Path(__file__).parent / "translations" / f"{self.locale}.json"
        if not translation_file.exists():
            _LOGGER.debug("Translation file not found: %s, using English", translation_file)
            self._data = _ENGLISH_STRINGS
            return

        try:
            with open(translation_file, encoding="utf-8") as f:
                self._data = json.load(f)
            _LOGGER.debug("Loaded translations from %s", translation_file)
        except (json.JSONDecodeError, OSError) as e:
            _LOGGER.warning("Failed to load translations from %s: %s, using English", translation_file, e)
            self._data = _ENGLISH_STRINGS

    @lru_cache(maxsize=512)
    def get(self, key: str, default: str | None = None) -> str:
        """Get translation for a key, with fallback to default or English."""
        # Navigate nested dictionary: "alerts.fill_water" -> self._data["alerts"]["fill_water"]
        parts = key.split(".")
        value = self._data
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                # Key not found, try English fallback
                if self.locale != "en":
                    value = _ENGLISH_STRINGS
                    for p in parts:
                        if isinstance(value, dict) and p in value:
                            value = value[p]
                        else:
                            return default or key
                    return str(value)
                return default or key
        return str(value)

    def get_entity_name(self, entity_type: str, entity_id: str, template: str | None = None) -> str:
        """Get translated entity name.

        Args:
            entity_type: "sensor", "binary_sensor", etc.
            entity_id: "status", "connectivity", etc.
            template: Optional f-string template if not using direct lookup.

        Returns:
            Translated entity name or template with fallback to template/default.
        """
        # Try direct lookup first: "entities.sensor.status"
        key = f"entities.{entity_type}.{entity_id}"
        result = self.get(key)
        if result != key:  # Found translation
            return result
        # Fall back to provided template
        return template or key

    def format_entity_name(self, template: str, **kwargs: str) -> str:
        """Format entity name template with translated values.

        Args:
            template: e.g. "Cycles {key}" or "Alert {alert}"
            **kwargs: Values to substitute, e.g. key="cleaning" or alert="fill_water"

        Returns:
            Formatted string with translations applied to kwargs.
        """
        # Translate any keyword arguments
        translated_kwargs = {}
        for key, value in kwargs.items():
            # Try to translate the value. For example:
            # - translate_key_name("cleaning") -> looks up "counters.cleaning"
            # - translate_alert_name("fill_water") -> looks up "alerts.fill_water"
            if key == "key":
                # Key could be a counter or percent key
                translated = self.get(f"counters.{value}") or self.get(f"percents.{value}")
                if translated not in [f"counters.{value}", f"percents.{value}"]:
                    translated_kwargs[key] = translated
                else:
                    translated_kwargs[key] = value.replace("_", " ")
            elif key == "alert":
                translated = self.get(f"alerts.{value}")
                if translated != f"alerts.{value}":
                    translated_kwargs[key] = translated
                else:
                    translated_kwargs[key] = value.replace("_", " ")
            elif key == "product":
                # Recipe names like "espresso", "cappuccino" are translated inline
                translated = self.get(f"recipes.{value}")
                if translated != f"recipes.{value}":
                    translated_kwargs[key] = translated
                else:
                    translated_kwargs[key] = value.replace("_", " ")
            elif key == "name":
                # Setting names from the machine profile
                translated_kwargs[key] = value.replace("_", " ")
            else:
                translated_kwargs[key] = value
        return template.format(**translated_kwargs)

    def get_alert_name(self, alert: str) -> str:
        """Get translated alert state name (e.g. 'fill_water' -> 'Fill water')."""
        result = self.get(f"alerts.{alert}")
        if result != f"alerts.{alert}":
            return result
        return alert.replace("_", " ").title()

    def get_counter_name(self, key: str) -> str:
        """Get translated counter key name (e.g. 'cleaning' -> 'Cleaning')."""
        result = self.get(f"counters.{key}")
        if result != f"counters.{key}":
            return result
        return key.replace("_", " ").title()

    def get_percent_name(self, key: str) -> str:
        """Get translated percent key name (e.g. 'cleaning' -> 'Cleaning')."""
        result = self.get(f"percents.{key}")
        if result != f"percents.{key}":
            return result
        return key.replace("_", " ").title()


def get_translator(locale: str = "en") -> Translator:
    """Get or create a translator instance for the given locale.

    Instances are cached — multiple calls with the same locale return the same object.

    Args:
        locale: Language code (e.g., "en", "de", "fr").

    Returns:
        Translator instance for the locale.
    """
    if locale not in _translators:
        _translators[locale] = Translator(locale)
    return _translators[locale]
