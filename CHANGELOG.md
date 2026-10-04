# Changelog

All notable changes to this integration are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the
version follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.1] — 2026-10-04

### Changed

- Brew control entities now use Home Assistant translation keys instead of
  hard-coded English names. The EF566 product names, temperature levels and
  the factory-default sentinel are translated in English and German
  (*Factory default* / *Standard*); unknown product names from other machine
  profiles stay usable as their raw profile value. The locale-neutral
  sentinel state value changed from `"Factory Default"` to
  `"factory_default"` — templates comparing the raw select state must use the
  new key (the Lovelace brew card shows the human label independently).

## [1.1.0] — 2026-10-04

### Added

- Profile-backed grinder ratio (F2) brew control:
  `select.<slug>_brew_grinder_ratio` with the product's left/right bean-mix
  options, keeping item names from being coerced to ints.

## [1.0.1] — 2026-10-04

### Fixed

- Options-flow translations (`retain_when_offline` toggle label) are now
  under the top-level `"options"` key in `strings.json` /
  `translations/en.json`. They were nested at `config.options`, which
  hassfest rejects, breaking CI for the 1.0.0 release.

## [1.0.0] — 2026-10-02

### Changed

- **BREAKING**: entities no longer flip to `unavailable` when the machine
  goes offline. All value-bearing entities (status, brew counters,
  brew total, maintenance counters, percents, machine type, alert
  binary sensors, settings selects/numbers) keep rendering their
  **last-known value** through an outage; the OFFLINE snapshot only
  drives `binary_sensor.<slug>_connectivity`. Previously a confirmed
  outage marked every entity unavailable, which blanked dashboards and
  broke templates reading `states('sensor.…_brews_total')`. Automations
  that gated on entity availability must now gate on the connectivity
  sensor instead (e.g.
  `is_state('binary_sensor.<slug>_connectivity', 'on')`).
  - **Escape hatch**: the previous behavior is restorable via the
    hidden *Advanced settings → Retain last values when offline* toggle
    in the integration's Configure dialog (stored as the
    `retain_when_offline` config-entry option; uncheck it to return to
    per-entity `unavailable` on outage).

### Added

- Hidden options flow (Configure → Advanced settings) exposing the
  `retain_when_offline` boolean; changing it reloads the entry.

## [0.11.0] — 2026-10-02

### Added

- Brew-progress sensor (`sensor.<slug>_brew_progress`): state is the
  latest `@TV:` progress state (`GRINDING_COFFEE` … `ENJOY`), attributes
  carry the whole decoded frame including `percent` and `product`.
- New services: `jura.cancel`, `jura.skip_quality_step` (scope `one`/`all`),
  `jura.milk_cooler_status`, `jura.restart_dongle`, `jura.special_counters`.
  The read-only ones return the library dict as the service response.
- Brew button goes unavailable while the machine blocks the selected
  product, instead of erroring on press.
- Binary sensor for the "Clean milk system" prompt
  (`binary_sensor.<slug>_alert_cappu_clean_alert`): the JURA shows it
  after every milk drink, J.O.E. mirrors it — it was missing here.
  Needs jura_connect 0.14.0, which decodes alert bit 41
  (`cappu_clean_alert`) even for machines paired without a
  `machine_type`. Fixes #17.

- Per-snapshot `blocked_products` + `progress` attributes on the status
  sensor.

### Changed

- `jura_connect >= 0.14.0`.
- Maintenance counter/percent maps now carry exactly the counters the
  machine reports; a missing counter means the entity is unavailable
  instead of showing a wrong zero.
- Settings are read via the batch `@TM:00,FC` bank with per-setting
  fallback — fewer round trips per poll.
- The brew service and button follow the `@TV:` progress stream until
  `ENJOY` (the session stays open up to 120 s; connectivity does not
  flap during that window).

### Fixed

- Milk-sensor alert binary sensors keep working with machine profiles
  (upstream jura_connect 0.13.1 restores the canonical profile alert
  names this integration keys on).
- Brew card offline state is now driven by the
  `binary_sensor.<machine>_connectivity` entity: when the machine is
  unreachable the card shows a grey *Offline* pill, a "brewing
  unavailable" note, and disables the sliders + Brew button, instead of
  wrongly reporting "Online" off the brew button's retained
  `last_press` timestamp. It no longer misreports when the entity
  registry assigned the connectivity/status entities a different slug
  than the brew entities (registry reassignment on rename): the card
  matches a machine's entities by slug token, so a lone `machine:
  <slug>` pin keeps working across such drift; zero-config mode falls
  back to suffix-matching.
- The card file is served with a long `Cache-Control`; bump the
  `/local/jura-brew-card.js?v=…` query in the dashboard resource after
  each card update, or browsers keep the stale copy for a month.

## Earlier history

Recorded highlights (see `git log` for the full history):

### [0.10.0]

- Milk and milk-foam brew axes (F5/F6) over jura_connect 0.11.0;
  redesigned brew card; serialized machine I/O with debounced offline
  handling.

### [0.8.1] and earlier

- HA-native translations + German locale; per-recipe brew counters;
  machine-settings entities; offline surfacing on every poll; initial
  release as a Home Assistant custom component.
