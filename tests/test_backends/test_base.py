"""Backend dataclass tests."""

from __future__ import annotations

from custom_components.jura.backends.base import DiscoveredMachine, MachineSnapshot


def test_machine_snapshot_defaults():
    s = MachineSnapshot(
        address="10.0.0.1",
        conn_id="x",
        handshake_state="CORRECT",
        active_alerts=(),
    )
    assert s.counters == {}
    assert s.percents == {}
    assert s.raw_status_hex == ""


def test_discovered_machine_default_via():
    m = DiscoveredMachine(address="10.0.0.1", name="kitchen")
    assert m.via == "udp"
