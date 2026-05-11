# Jura Coffee — Home Assistant Custom Component

[![CI](https://github.com/makefu/jura-connect-hass/actions/workflows/ci.yml/badge.svg)](https://github.com/makefu/jura-connect-hass/actions/workflows/ci.yml)
[![hacs_custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Home Assistant integration for WiFi-connected JURA coffee machines (S8, EB,
TT237W series). Built on the reverse-engineered
[`jura-connect`](https://github.com/makefu/jura-connect) library — talks
directly to the machine's WiFi dongle on TCP/51515. No cloud, no vendor
account.

## Features

- Automatic discovery of machines on the local network (UDP broadcast + TCP
  fallback for firmwares like TT237W that don't reply to UDP)
- One-shot pairing with the physical machine (press OK once, store the
  auth-hash, reconnect silently afterwards)
- Sensors:
  - **State** — overall machine state derived from the active-alert bits
  - **Maintenance counters** — cleaning / filter / decalc / cappu-rinse /
    coffee-rinse / cappu-clean
  - **Maintenance percent** — cleaning / filter / decalc (0–100 % or
    unavailable when the indicator is absent)
- Binary sensors for every well-known alert (water low, beans empty, drip
  tray, milk warning, …)
- Services for everything the library supports safely: lock/unlock screen,
  brew a recipe, run cleaning / descaling / filter-change / cappu-rinse /
  cappu-clean cycles, power off, restart the dongle

Destructive operations like `reset-counters`, `set-pin`, `set-ssid`, and
`set-password` are *not* exposed in v1 — they can lock you out of the machine
until a factory reset. Use the upstream `jura-connect` CLI if you need them.

## Installation

### HACS (recommended)

1. In HACS → Integrations → ⋮ → **Custom repositories**, add
   `https://github.com/makefu/jura-connect-hass` as type *Integration*.
2. Install **Jura Coffee** from the list.
3. Restart Home Assistant.

### Manual

Copy `custom_components/jura/` into your Home Assistant `config/custom_components/`
directory and restart.

## Configuration

1. Settings → Devices & Services → **Add Integration** → *Jura Coffee*.
2. Pick your machine from the discovered list, or choose *Enter manually* and
   type the IP.
3. When prompted, **press OK on the coffee machine** to accept the pairing.
   The integration stores the resulting auth-hash on the config entry; you
   only do this once per machine.

## Services

| Service                | Description                                           |
| ---------------------- | ----------------------------------------------------- |
| `jura.force_update`    | Poll the machine immediately                          |
| `jura.lock_screen`     | Lock the front-panel display                          |
| `jura.unlock_screen`   | Unlock the front-panel display                        |
| `jura.brew`            | Start brewing a recipe (`recipe: "01"` = espresso)    |
| `jura.clean`           | Start coffee-system cleaning cycle (~5 min)           |
| `jura.decalc`          | Start descaling cycle (30+ min, requires descaler)    |
| `jura.filter_change`   | Run water-filter change procedure                     |
| `jura.cappu_rinse`     | Rinse the milk system                                 |
| `jura.cappu_clean`     | Clean the milk system (requires cleaning tablet)      |
| `jura.power_off`       | Put the machine into standby                          |
| `jura.restart`         | Reboot the WiFi dongle                                |

All commands accept `entity_id` (any Jura entity) or `config_entry_id` to
target a specific machine.

### Example automation

```yaml
alias: "Morning espresso"
trigger:
  - platform: time
    at: "07:00:00"
condition:
  - condition: state
    entity_id: binary_sensor.jura_192_0_2_10_fill_water
    state: "off"
action:
  - service: jura.brew
    target:
      entity_id: sensor.jura_192_0_2_10_state
    data:
      recipe: "01"
```

## Development

See [AGENTS.md](AGENTS.md) for the full development guide. TL;DR:

```sh
nix develop -c pytest tests/ -v
nix develop -c ruff check
nix build
```

## Acknowledgements

- [`jura-connect`](https://github.com/makefu/jura-connect) — the reverse-
  engineered protocol library this integration wraps.
- The J.O.E. (Jura Operating Experience) Android app, from which the WiFi
  protocol was originally derived.
