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


async def test_fetch_includes_brews_and_machine_type(running_simulator):
    """Simulator returns Kaffeebert's realistic product counter table."""
    host, port = running_simulator.address
    backend = JuraConnectBackend(
        host,
        port,
        conn_id="ha-test",
        machine_type="EF1091",
    )
    await backend.pair()

    snapshot = await backend.fetch()

    assert snapshot.brews_total == 3229
    # The EF1091 profile names slot 0x02 as "espresso"
    assert snapshot.brews.get("espresso") == 78
    assert snapshot.brews.get("coffee") == 595
    # Machine-type fields are populated from the EF code
    assert snapshot.machine_type == "EF1091"
    assert snapshot.machine_type_name  # friendly name resolved


async def test_fetch_without_machine_type_still_succeeds(running_simulator):
    """No EF code => baseline behavior, machine_type fields are None."""
    host, port = running_simulator.address
    backend = JuraConnectBackend(host, port, conn_id="ha-test")
    await backend.pair()

    snapshot = await backend.fetch()

    assert snapshot.machine_type is None
    assert snapshot.machine_type_name is None
    # Brews still come back; names use the EF536 baseline map.
    assert snapshot.brews_total == 3229


async def test_fetch_exposes_severity_tuples(running_simulator):
    """The library splits @TF: bits into errors/info/process by ALERT.Type."""
    host, port = running_simulator.address
    backend = JuraConnectBackend(host, port, conn_id="ha-test", machine_type="EF1091")
    await backend.pair()

    snapshot = await backend.fetch()
    # Simulator's DEFAULT_STATUS_PAYLOAD (v0.9.0): one "info" + one "process"
    # bit are set, both severities should be non-empty.
    assert snapshot.errors == ()
    assert snapshot.info  # tuple non-empty
    assert snapshot.process  # tuple non-empty
    # And the union should match active_alerts.
    assert set(snapshot.active_alerts) == set(snapshot.errors + snapshot.info + snapshot.process)


async def test_fetch_reads_settings_when_profile_configured(running_simulator):
    """With EF1091 loaded, the backend reads all seven machine settings."""
    host, port = running_simulator.address
    backend = JuraConnectBackend(host, port, conn_id="ha-test", machine_type="EF1091")
    await backend.pair()

    snapshot = await backend.fetch()
    # EF1091 exposes seven settings; the simulator defaults them all.
    expected = {"hardness", "auto_off", "units", "language", "milk_rinsing", "frother_instructions"}
    assert expected.issubset(set(snapshot.settings.keys()))


async def test_fetch_no_profile_no_settings(running_simulator):
    host, port = running_simulator.address
    backend = JuraConnectBackend(host, port, conn_id="ha-test")
    await backend.pair()
    snapshot = await backend.fetch()
    assert snapshot.settings == {}


async def test_write_setting_round_trip(running_simulator):
    """Writes go through normalisation + checksum + handshake on a real client."""
    host, port = running_simulator.address
    backend = JuraConnectBackend(host, port, conn_id="ha-test", machine_type="EF1091")
    await backend.pair()

    # Hardness is a step_slider on EF1091 — value comes in as a decimal string.
    await backend.write_setting("hardness", "18")

    snapshot = await backend.fetch()
    # The simulator stores the written value; the raw hex is "12" (18).
    assert snapshot.settings.get("hardness", "").upper() == "12"


async def test_write_setting_rejects_unknown_value(running_simulator):
    host, port = running_simulator.address
    backend = JuraConnectBackend(host, port, conn_id="ha-test", machine_type="EF1091")
    await backend.pair()

    with pytest.raises(ValueError):
        await backend.write_setting("language", "klingon")


async def test_write_setting_requires_profile(running_simulator):
    from custom_components.jura.backends.base import JuraBackendError

    host, port = running_simulator.address
    backend = JuraConnectBackend(host, port, conn_id="ha-test")  # no machine_type
    await backend.pair()

    with pytest.raises(JuraBackendError):
        await backend.write_setting("hardness", "18")


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
