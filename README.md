# Jura Connect — Home Assistant Integration

[![CI](https://github.com/makefu/jura-connect-hass/actions/workflows/ci.yml/badge.svg)](https://github.com/makefu/jura-connect-hass/actions/workflows/ci.yml)
[![hacs_custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Home Assistant integration for WiFi-connected JURA coffee machines (S8, EB,
TT237W series). Built on the reverse-engineered
[`jura-connect`](https://github.com/makefu/jura-connect) library — talks
directly to the machine's WiFi dongle on TCP/51515. No cloud, no vendor
account.

Requires `jura_connect>=0.9.1`. The integration ships the Python dependency
declaration; Nix users get it pinned via the flake input.

## Features

### Setup

- **Auto-discovery** on the local network (UDP broadcast first, with a TCP
  /24 fallback for firmwares like TT237W that don't reply to UDP); manual IP
  entry is always available.
- **One-shot pairing** — press OK on the machine once, the integration
  persists the resulting auth-hash on the config entry and reconnects
  silently afterwards.
- **Per-machine profiles** — pick your model from a dropdown of 88 known
  JURA variants (S8 EB, ENA 8, Z8, …). The profile drives the names of
  alerts, brew counters, and machine settings. Auto-detected from the UDP
  broadcast's article number when available.

### Sensors (main device-card section)

- **Status** — overall machine state derived from the active-alert bits.
- **Brew `<recipe>`** — one sensor per recipe the machine reports
  (espresso, coffee, cappuccino, macchiato, americano, …). Set is
  discovered from the machine; names from the profile when configured.
- **Brew total** — lifetime brews across all recipes.
- **Service `<kind>` level** — `cleaning`, `descale`, `filter change` —
  the percent-to-next-service indicators (0–100 %).
- **Setting `<name>`** — current value of each machine setting (language,
  hardness, auto-off, units, …); item-driven settings surface as the
  friendly catalogue name, sliders as the integer value.

### Binary sensors

- **Alert `<name>`** — one entity per status bit (`fill water`, `no beans`,
  `empty grounds`, drip tray, milk warning, heating up, coffee ready, …).
  Problem-class alerts live in the main view; running and informational
  bits move to the diagnostic section.

### Configuration controls (CONFIG section)

- **`select.*`** entities for switch / combobox / item-slider settings
  (language, units, auto-off delay, milk rinsing, frother instructions, …).
  Writes are validated against the profile before any TCP session opens, so
  invalid values surface a clear "klingon is not a recognised value.
  Allowed: german=01, english=02, …" message rather than a backend error.
- **`number.*`** entities for step-slider settings (currently water
  hardness on EF1091) with the min/max/step pulled from the profile XML.
- **Brew control panel** — a compact, machine-wide set of controls for the
  next brew: a **Brew Product** select, plus **Brew Strength**, **Brew Water**
  and **Brew Temperature** selects (each offering "Factory Default" alongside
  the product's own options/range), and a single **Brew** button. "Factory
  Default" sends that product's built-in (XML) default value — JURA's WiFi
  protocol has no way to reuse the machine's own configured value, so every
  brew sends explicit, range-clamped values. Picking a product re-scopes the
  parameter selects to that drink and loads your saved choices for it (these
  persist across restarts, per product); parameters a product doesn't support
  (e.g. strength for hot water) go unavailable. Pressing **Brew** physically
  brews the staged drink.

### Diagnostics (collapsed by default)

- **Machine type** — the EF code + friendly name (e.g. `S8 (EB)` with
  `machine_type=EF1091` on attributes).
- **Connectivity** — the canonical "is the machine reachable right now"
  signal (`binary_sensor` with `device_class=connectivity`). Wire
  automations against this rather than the per-entity `available` flag.
- **Cycles `<kind>`** — the six maintenance counters (cleaning, descale,
  filter change, cappu rinse, coffee rinse, cappu clean).
- Running / informational binary sensors (heating up, coffee ready,
  welcome, please wait, …).

### Resilience

Entities keep showing their last value when the machine is offline — JURA
dongles sleep regularly and v0.1 caused dashboards to flash with every
failed poll. The connectivity binary sensor is the single source of truth
for reachability.

### Services

| Service                | Description                                           |
| ---------------------- | ----------------------------------------------------- |
| `jura.force_update`    | Poll the machine immediately                          |
| `jura.lock_screen`     | Lock the front-panel display                          |
| `jura.unlock_screen`   | Unlock the front-panel display                        |
| `jura.brew`            | Brew by `product` name (+ optional strength/water/temp) |
| `jura.clean`           | Start coffee-system cleaning cycle (~5 min)           |
| `jura.descale`         | Start descaling cycle (30+ min, requires descaler)    |
| `jura.filter_change`   | Run water-filter change procedure                     |
| `jura.cappu_rinse`     | Rinse the milk system                                 |
| `jura.cappu_clean`     | Clean the milk system (requires cleaning tablet)      |
| `jura.power_off`       | Put the machine into standby (TT237W ignores this)    |
| `jura.restart`         | Reboot the WiFi dongle                                |

All services accept `entity_id` (any Jura entity) or `config_entry_id` to
target a specific machine. Brewing returns the raw command result as the
service response.

Destructive registry entries that can lock you out of the machine
(`reset-counters`, `set-pin`, `set-ssid`, `set-password`, `raw`) are
deliberately **not** exposed as HA services. Use the upstream
`jura-connect` CLI if you need them.

## Installation

### HACS (recommended)

1. In HACS → Integrations → ⋮ → **Custom repositories**, add
   `https://github.com/makefu/jura-connect-hass` as type *Integration*.
2. Install **Jura Connect** from the list.
3. Restart Home Assistant.

### Manual

Copy `custom_components/jura/` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

1. Settings → Devices & Services → **Add Integration** → *Jura Connect*.
2. Choose **Search for machines on the network** (recommended) or
   **Enter the IP address manually**. Discovery shows a progress
   spinner; empty results route to manual entry.
3. When prompted, **press OK on the coffee machine** to accept the pairing.
   The pairing dialog shows a spinner with a timeout of 60 seconds. The
   resulting auth-hash is persisted to the config entry; subsequent
   reconnects skip the on-machine confirmation.
4. Pick your **machine model** from the dropdown. Auto-detection
   pre-selects the right entry when the WiFi dongle replied to UDP
   discovery (the broadcast carries the article number, which maps to an
   EF code via the bundled catalogue). Otherwise choose your model — for
   example `S8 (EB) [EF1091]` — or pick *Use baseline (no profile)* for
   a generic EF536 fallback. Models without a profile won't get
   per-recipe brew counters or machine-setting entities.

## Example automations

### Wake me up with espresso (gated on reachability and water)

```yaml
alias: Morning espresso
trigger:
  - platform: time
    at: "07:00:00"
condition:
  - condition: state
    entity_id: binary_sensor.jura_connectivity
    state: "on"
  - condition: state
    entity_id: binary_sensor.jura_alert_fill_water
    state: "off"
action:
  - service: jura.brew
    target:
      entity_id: sensor.jura_status
    data:
      recipe: "01"
```

### Notify when cleaning is due

```yaml
alias: Coffee machine needs cleaning
trigger:
  - platform: numeric_state
    entity_id: sensor.jura_service_cleaning_level
    above: 95
action:
  - service: notify.mobile_app
    data:
      message: "Cleaning cycle due — service level {{ states('sensor.jura_service_cleaning_level') }}%"
```

### Reduce auto-off delay at night

```yaml
alias: Power-saving overnight
trigger:
  - platform: time
    at: "22:00:00"
action:
  - service: select.select_option
    target:
      entity_id: select.jura_setting_auto_off
    data:
      option: "15min"
```

(Exact entity_id slugs depend on your machine's host / conn-id — check
the device page after setup.)

## Development

See [AGENTS.md](AGENTS.md) for the full development guide. TL;DR:

```sh
nix develop -c pytest tests/ -v
nix develop -c ruff check
nix build
./result/bin/jura-connect-ha --version
```

## Acknowledgements

- [`jura-connect`](https://github.com/makefu/jura-connect) — the
  reverse-engineered protocol library this integration wraps. Most of
  the heavy lifting (handshake, status decoding, command registry,
  per-machine XML catalogue) lives upstream.
- The J.O.E. (Jura Operating Experience) Android app, from which the
  WiFi protocol was originally derived.
