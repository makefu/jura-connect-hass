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
# item_slider read-back: the dongle strips the 1-byte type tag on store
# ---------------------------------------------------------------------------


def _ef1091_auto_off_entity(snapshot_settings, fake_config_entry, ef1091):
    from dataclasses import replace

    auto_off = ef1091.setting_by_name["auto_off"]
    snap = replace(_snap_with(snapshot_settings), settings=snapshot_settings)
    return SettingSelectEntity(_coordinator(snap), fake_config_entry, auto_off)


def _snap_with(settings):
    # Build a snapshot the entity can read. The other fields don't
    # matter for current_option testing.
    from custom_components.jura.backends.base import MachineSnapshot

    return MachineSnapshot(
        address="192.0.2.10",
        conn_id="x",
        handshake_state="CORRECT",
        active_alerts=(),
        settings=settings,
    )


def test_auto_off_30min_displays_after_stripped_readback(ef1091, fake_config_entry):
    """Writing `211E` (30min) stores `1E` on TT237W; the entity must
    still resolve `1E` to `30min` via suffix-matching the catalogue."""
    entity = _ef1091_auto_off_entity({"auto_off": "1E"}, fake_config_entry, ef1091)
    assert entity.current_option == "30min"


def test_auto_off_5h_displays_after_2byte_stripped_readback(ef1091, fake_config_entry):
    """The 2-byte ItemSlider entries (5h..9h) strip to 4 hex chars."""
    entity = _ef1091_auto_off_entity({"auto_off": "012C"}, fake_config_entry, ef1091)
    assert entity.current_option == "5h"


def test_auto_off_15min_displays_directly(ef1091, fake_config_entry):
    """The shortest entry (15min) has no type-tag prefix, so the read-back
    matches directly without needing suffix logic."""
    entity = _ef1091_auto_off_entity({"auto_off": "0F"}, fake_config_entry, ef1091)
    assert entity.current_option == "15min"


def test_auto_off_full_catalogue_value_still_matches(ef1091, fake_config_entry):
    """Defence in depth: if a firmware ever returns the full `211E` form,
    the exact-match path should still find `30min`."""
    entity = _ef1091_auto_off_entity({"auto_off": "211E"}, fake_config_entry, ef1091)
    assert entity.current_option == "30min"


def test_unknown_value_returns_none(ef1091, fake_config_entry):
    entity = _ef1091_auto_off_entity({"auto_off": "DEAD"}, fake_config_entry, ef1091)
    assert entity.current_option is None


def test_normalise_value_writes_full_211E_for_30min(ef1091):
    """The write side normalises by ITEM NAME, so picking '30min' must send
    the full type-tagged `211E` — NOT the stripped `1E`. The user reported
    seeing `30` (=0x1E) end up on the wire; this pins the correct value."""
    auto_off = ef1091.setting_by_name["auto_off"]
    assert auto_off.normalise_value("30min") == "211E"
    assert auto_off.normalise_value("5h") == "22012C"
    assert auto_off.normalise_value("15min") == "0F"


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
