"""Tests for the select + number platforms that drive machine settings."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

jura_connect = pytest.importorskip("jura_connect")
from jura_connect import load_profile  # noqa: E402

from custom_components.jura.number import SettingNumberEntity  # noqa: E402
from custom_components.jura.select import SettingSelectEntity  # noqa: E402


@pytest.fixture
def ef1091():
    """The S8 EB profile carries a known set of settings."""
    return load_profile("EF1091")


def _coordinator(snapshot, write_setting=None):
    c = MagicMock()
    c.data = snapshot
    c.last_update_success = True
    c.write_setting = write_setting or AsyncMock()
    return c


# ---------------------------------------------------------------------------
# SettingSelectEntity (combobox / switch / item_slider)
# ---------------------------------------------------------------------------


def _setting_by_name(profile, name):
    return profile.setting_by_name[name]


def test_select_entity_options_come_from_profile(ef1091, sample_snapshot, fake_config_entry):
    language = _setting_by_name(ef1091, "language")
    entity = SettingSelectEntity(_coordinator(sample_snapshot), fake_config_entry, language)
    assert "english" in entity.options
    assert "german" in entity.options
    assert entity.unique_id.endswith("setting_language")


def test_select_entity_current_option_decoded_from_hex(ef1091, sample_snapshot, fake_config_entry):
    """The coordinator stores raw hex; the entity translates it back to a name."""
    language = _setting_by_name(ef1091, "language")
    entity = SettingSelectEntity(_coordinator(sample_snapshot), fake_config_entry, language)
    # sample_snapshot.settings["language"] = "02" which maps to "english" in EF1091.
    assert entity.current_option == "english"


def test_select_entity_returns_none_for_unknown_hex(ef1091, sample_snapshot, fake_config_entry):
    from dataclasses import replace

    language = _setting_by_name(ef1091, "language")
    snap = replace(sample_snapshot, settings={"language": "FF"})
    entity = SettingSelectEntity(_coordinator(snap), fake_config_entry, language)
    assert entity.current_option is None


async def test_select_entity_writes_via_coordinator(ef1091, sample_snapshot, fake_config_entry):
    language = _setting_by_name(ef1091, "language")
    write = AsyncMock()
    entity = SettingSelectEntity(
        _coordinator(sample_snapshot, write_setting=write),
        fake_config_entry,
        language,
    )
    await entity.async_select_option("german")
    write.assert_awaited_once_with("language", "german")


# ---------------------------------------------------------------------------
# SettingNumberEntity (step_slider)
# ---------------------------------------------------------------------------


def test_number_entity_min_max_step_from_profile(ef1091, sample_snapshot, fake_config_entry):
    hardness = _setting_by_name(ef1091, "hardness")
    entity = SettingNumberEntity(_coordinator(sample_snapshot), fake_config_entry, hardness)
    assert entity.native_min_value == float(hardness.minimum)
    assert entity.native_max_value == float(hardness.maximum)
    assert entity.native_step == float(hardness.step or 1)


def test_number_entity_native_value_parses_hex(ef1091, sample_snapshot, fake_config_entry):
    hardness = _setting_by_name(ef1091, "hardness")
    entity = SettingNumberEntity(_coordinator(sample_snapshot), fake_config_entry, hardness)
    # sample_snapshot.settings["hardness"] = "10" hex -> 16 decimal.
    assert entity.native_value == 16.0


async def test_number_entity_writes_decimal_string(ef1091, sample_snapshot, fake_config_entry):
    hardness = _setting_by_name(ef1091, "hardness")
    write = AsyncMock()
    entity = SettingNumberEntity(
        _coordinator(sample_snapshot, write_setting=write),
        fake_config_entry,
        hardness,
    )
    await entity.async_set_native_value(18)
    write.assert_awaited_once_with("hardness", "18")
