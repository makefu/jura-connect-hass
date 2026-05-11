"""Snapshot serializer tests."""

from __future__ import annotations

from custom_components.jura.serializers import percent_value, serialize_snapshot


def test_serialize_snapshot_round_trip(sample_snapshot):
    data = serialize_snapshot(sample_snapshot)
    assert data["address"] == "192.0.2.10"
    assert data["conn_id"] == "homeassistant-test"
    assert data["handshake_state"] == "CORRECT"
    assert data["active_alerts"] == ["heating_up"]
    assert data["counters"]["cleaning"] == 21
    # 0xFF percent is translated to None on serialize
    assert data["percents"]["filter_change"] is None
    assert data["percents"]["cleaning"] == 80
    assert data["raw_status_hex"] == "0010000000000000"


def test_percent_value_translates_absent_sentinel():
    assert percent_value(255) is None
    assert percent_value(0) == 0
    assert percent_value(80) == 80
    assert percent_value(100) == 100


def test_serialize_empty_snapshot(empty_snapshot):
    data = serialize_snapshot(empty_snapshot)
    assert data["active_alerts"] == []
    assert all(v == 0 for v in data["counters"].values())
    assert all(v == 100 for v in data["percents"].values())
