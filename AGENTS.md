# Agent Development Guide

**Jura Connect** — Home Assistant custom component for JURA coffee
machines, talking to the WiFi dongle over TCP/51515 via the
reverse-engineered [`jura-connect`](https://github.com/makefu/jura-connect)
library.

## Project Structure

```
custom_components/jura/
  __init__.py          # Integration setup, service registration
  config_flow.py       # HA config flow: user choice -> discovery|manual -> pair -> machine_type
  const.py             # Domain, config keys, alert/counter/percent/setting maps
  coordinator.py       # JuraCoordinator (polling + command + write_setting dispatch)
  entity.py            # JuraEntity base (DeviceInfo + available override)
  sensor.py            # Status/brew/counter/percent/machine-type/setting-value sensors
  binary_sensor.py     # Per-alert + connectivity binary sensors
  select.py            # Writable setting entities (switch/combobox/item_slider)
  number.py            # Writable setting entities (step_slider — hardness etc.)
  serializers.py       # MachineSnapshot -> JSON-safe dict helpers
  cli.py               # Standalone CLI (just --version + discover)
  services.yaml        # HA service definitions
  manifest.json        # HA component metadata (version is source of truth)
  backends/
    base.py            # JuraBackend ABC + MachineSnapshot dataclass
    jura.py            # JuraConnectBackend wrapping jura_connect.JuraClient
tests/
  conftest.py          # HA module stubs + shared fixtures
  test_sensor.py
  test_binary_sensor.py
  test_coordinator.py
  test_config_flow.py
  test_services.py
  test_serializers.py
  test_backends/
    test_base.py
    test_jura.py       # End-to-end with jura_connect.simulator.Simulator
```

## Development Environment

All development uses Nix. Enter the dev shell before running anything:

```sh
nix develop
```

This provides Python 3.13 with `jura_connect`, plus `pytest`, `pytest-asyncio`,
`ruff`, `mypy`, `voluptuous`, `freezegun`.

## Testing

### Unit Tests

```sh
nix develop -c pytest tests/ -v
```

The unit-test suite uses `tests/conftest.py` to stub Home Assistant modules so
code can be imported without HA installed. `tests/test_backends/test_jura.py`
spins up `jura_connect.simulator.Simulator` and exercises the real wire
protocol through `JuraConnectBackend` — that's the closest you can get to a
hardware test without a real machine.

When fixing a bug, start with a failing regression test.

### NixOS VM Test

```sh
pueue add -- 'nix build .#checks.x86_64-linux.vm-test 2>&1'
pueue follow <task-id> | tail -n 20
```

Boots a NixOS VM with Home Assistant + this custom component installed and
asserts HA loads it without import errors.

## Linting and Formatting

All code must pass before committing:

```sh
nix develop -c ruff check
nix develop -c ruff format --check
```

Rules: line-length=120, target Python 3.13. Fix root causes of lint errors —
never silence them.

## Building

```sh
pueue add -- 'nix build 2>&1'
pueue follow <task-id> | tail -n 20
```

After building, verify the CLI:

```sh
./result/bin/jura-connect-ha --version
```

## Versioning

Version lives in two places — both must be updated together:
- `custom_components/jura/manifest.json` (source of truth, read by flake.nix)
- `pyproject.toml`

## Adding New Files

Always track new files for Nix flakes before building:

```sh
git add -AN
```

Non-Python files in the package (`manifest.json`, `services.yaml`,
`strings.json`) must be listed under `[tool.setuptools.package-data]` in
`pyproject.toml`.

## Architecture Notes

- **`jura_connect` is synchronous.** All blocking I/O is funnelled through
  `asyncio.to_thread()` in `backends/jura.py`. Do not call `JuraClient`
  directly from coordinator/config-flow code — go through the backend.
- **One TCP session at a time.** The dongle accepts only one connection;
  a second one gets `WRONG_HASH`. The backend opens a fresh `JuraClient`
  per call and closes it again, so the coordinator's polling and any
  service-triggered commands never race on the wire.
- **Pairing needs a human.** The user must press OK on the physical
  machine during the first pair. The config-flow surfaces this via the
  dedicated `pair` step; the resulting auth-hash is stored on the entry
  and reused on subsequent runs.
- **Discovery is best-effort.** UDP broadcast is unreliable on some
  firmwares (notably TT237W). The flow falls back to a TCP /24 sweep and
  ultimately to manual IP entry.
- **Named services pass `allow_destructive=True`** for destructive ops
  (clean, decalc, brew, …). The user opting into a documented service is
  the explicit gate that the library would otherwise require via flag.
  Genuinely dangerous registry entries (`reset-counters`, `set-pin`,
  `set-ssid`, `set-password`, `raw`) are *not* exposed as HA services in
  v1.

## Commit Messages

Use kernel-mailing-list style. Focus on WHY, not WHAT. Always test/lint/format
before committing.
