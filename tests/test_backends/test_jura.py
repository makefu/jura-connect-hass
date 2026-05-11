"""End-to-end backend tests using the jura_connect in-process simulator.

These exercise the real wire protocol — encoding, framing, handshake,
maintenance reads — without needing a physical coffee machine. If you
break the protocol contract between this integration and the library
these tests will catch it.
"""

from __future__ import annotations

import pytest

jura_connect = pytest.importorskip("jura_connect")
simulator = pytest.importorskip("jura_connect.simulator")

from custom_components.jura.backends.jura import JuraConnectBackend  # noqa: E402


@pytest.fixture
def running_simulator():
    cfg = simulator.SimulatorConfig(
        name="SimMachine",
        require_user_accept=False,
    )
    with simulator.run_in_thread(cfg) as sim:
        yield sim


async def test_fetch_round_trip(running_simulator):
    host, port = running_simulator.address
    backend = JuraConnectBackend(
        host,
        port,
        conn_id="ha-test",
        # auth_hash is empty but simulator allows it when require_user_accept=False
    )

    # First: pair to establish credentials (no human required in this config).
    new_hash = await backend.pair()
    assert len(new_hash) == 64

    snapshot = await backend.fetch()

    assert snapshot.address == host
    assert snapshot.handshake_state == "CORRECT"
    # Simulator's DEFAULT_MAINT_COUNTERS = "001500010008015 8 0E21 005B"
    assert snapshot.counters["cleaning"] == 0x0015
    assert snapshot.counters["filter_change"] == 0x0001
    assert snapshot.counters["decalc"] == 0x0008
    # MAINT_PERCENT = "50FF1E" -> cleaning=80, filter=255, decalc=30
    assert snapshot.percents["cleaning"] == 0x50
    assert snapshot.percents["filter_change"] == 0xFF
    assert snapshot.percents["decalc"] == 0x1E


async def test_lock_unlock_round_trip(running_simulator):
    host, port = running_simulator.address
    backend = JuraConnectBackend(host, port, conn_id="ha-test")
    await backend.pair()

    await backend.lock()
    await backend.unlock()


async def test_run_named_status(running_simulator):
    host, port = running_simulator.address
    backend = JuraConnectBackend(host, port, conn_id="ha-test")
    await backend.pair()

    result = await backend.run_named("status")
    assert result["name"] == "status"
    assert "active_alerts" in result["value"]
