# Changelog

All notable changes to this integration are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the
version follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.2] - 2026-11-03

### Fixed

- After switching the machine off, the Coffee Button tiles stayed
  pressable for minutes while the status showed
  `switch_off_delay_active`, and a press failed only after 30 s. That
  status was not treated as "cannot brew", and `jura.brew` (used by the
  cards) did not check which drinks the machine blocks; only the HA Brew
  button did, for its own selected drink.
  - `switch_off_delay_active` blocks brewing, ranks above the ready
    states on the status sensor and shows as *Switching off* /
    *Schaltet aus* on the cards.
  - Every brew (button, card, `jura.brew`) checks the requested drink
    against the machine's blocked products and is refused right away with
    *not ready* plus the reason instead of being sent.
  - The status sensor exposes `brew_ready`, `brew_blocker`, `brewing` and
    `blocked_products`. The Coffee Button decides per drink from them
    (the HA Brew button's availability reflects the brew panel's product,
    not the tile's) and translates the common machine states itself.

## [1.5.1] - 2026-11-03

### Fixed

- Integration hung after pressing Brew while the machine was heating up
  (status stuck on `heating_up`, power-off not noticed, only a reload
  helped). The machine ACKs a recipe while heating but never brews; the
  integration then followed that phantom brew for the whole follow window
  (120 s) holding the dongle's only session, and every further press
  queued another one. Now:
  - the Brew button is unavailable while the machine cannot brew (heating
    up, rinsing, filling, *please wait*, water/grounds/tray/cover/error
    alerts); brews are refused with *The coffee machine is not ready yet*
    (`machine_not_ready`) without touching the machine;
  - a second brew is refused (`brew_in_progress`) instead of queued, and
    the button is unavailable while a brew runs;
  - a brew without progress 30 s after the ACK ends with *The coffee
    machine did not start brewing…* (`brew_not_started`) and frees the
    session at once; a session still stuck after its time budget is
    closed;
  - while heating up / rinsing the machine is polled every 15 s, so the
    button returns soon after it is ready.
- Coffee Button: follows the integration's Brew button (*Not ready* plus
  the reason, e.g. *Heating up*), also after a dashboard reload during a
  brew; the new errors are shown in the user's language.
- Deprecation warning about the device registry: the integration no longer
  uses it; `jura/machines` takes the device id from the entity registry
  and now also returns the `brew_button` entity.

## [1.5.0] - 2026-11-03

### Changed

- The **Brew button** entity is unavailable while the machine is offline,
  also with *Retain last values when offline* (which keeps readings, not
  actions, alive). The Jura Brew Card and the Coffee Button follow.

### Added

- Brew failures are translated Home Assistant errors: an unreachable
  machine raises *The coffee machine is not reachable…*
  (`machine_unreachable`) and is marked offline at once instead of after
  the poll tolerance; other failures raise *Brewing failed: …*
  (`brew_failed`). English and German.
- `jura/machines` also lists machines whose setup is still running or
  being retried (new field `state`), with the product table from the
  bundled profile. The Coffee Button shows the drink plus *Machine not
  reachable, retrying…* instead of "no machine found", re-checks every 30 s
  and turns live without a page reload.
- Coffee Button: *Machine not reachable* on the tile when a brew finds the
  machine gone.

### Fixed

- Jura Brew Card treated an `unavailable` connectivity sensor (integration
  not loaded) as online.

## [1.4.2] - 2026-11-03

### Fixed

- Cards missing on Shelly Wall Displays and other slow or older clients
  ("Custom element doesn't exist"). Three causes, the same that hit the
  DSB Vertretungsplan card:
  - The cards were only extra script tags, which the frontend loads without
    waiting; slow devices built the dashboard first. Each card is now a
    Lovelace dashboard resource (storage mode), created, updated and
    de-duplicated automatically by path, so a manual `/jura_connect/…`
    workaround entry is merged. With YAML resources the extra script tags
    remain, now for both frontend builds.
  - Older WebViews get the legacy frontend build, which only loads
    resources registered for it (`es5`); the cards were not.
  - The legacy build runs cards as classic scripts in one global scope,
    where both Jura cards' top-level names collided and the second card
    failed to parse. Each card now has its own scope.
- Coffee Button: plain colour fallbacks before every `color-mix()`, margins
  instead of flex `gap`, no `inset` shorthand, so older WebViews show the
  brown cup and correct spacing. Render errors stay inside the tile instead
  of replacing it with Home Assistant's error card.

### Changed

- Manifest dependency on `lovelace` (for the resource registration).

## [1.4.1] - 2026-11-03

### Fixed

- Dashboard cards were not loaded automatically ("Custom element not
  found: jura-coffee-button-card" / "jura-brew-card"), although
  `/jura_connect/*.js` was served. They were registered from the config
  entry setup, i.e. only after the first machine poll (session quiet
  period, or a setup retry while the dongle sleeps); frontends loaded in
  the meantime never picked them up. The cards are now registered once in
  the integration's `async_setup`, early in startup and without any
  machine I/O. If you added `/jura_connect/…` as a dashboard resource as a
  workaround, remove it again.
- The card picker lists each Jura card only once, even when a module is
  evaluated twice (auto-load plus a leftover manual resource).

## [1.4.0] - 2026-11-03

### Added

- **Jura Coffee Button** dashboard card (`custom:jura-coffee-button-card`):
  a 2×2 tile for one favourite drink with personal parameters and a Brew
  button. A visual editor lists the machine's products and offers only the
  chosen product's parameters with their profile ranges; empty fields brew
  the factory default. Shows machine status, brew progress and errors on
  the tile, disables brewing while offline, optional tap-twice
  confirmation, English and German UI. The drink is always explicit: the
  card picker and the editor write `product` into the card config, and a
  card without one asks for a drink instead of brewing the profile's first
  product. The editor loads Home Assistant's lazy `ha-form` itself, so the
  drink picker is there even when no built-in editor was opened before.
- Websocket command `jura/machines`: every configured machine with its
  device id, status / connectivity / brew-progress entity ids and the
  product table (parameters mapped to `jura.brew` fields, with ranges,
  items and defaults).

### Changed

- The dashboard cards ship inside the integration
  (`custom_components/jura/www/`) and are registered automatically as
  frontend modules under `/jura_connect/…?v=<hash>`. Copying
  `jura-brew-card.js` to `config/www/` and adding a dashboard resource is no
  longer needed; remove an old manual `/local/jura-brew-card.js` resource
  when upgrading. Requires Home Assistant 2024.7 or newer.

## [1.3.0] — 2026-11-01

### Added

- Per-machine tunables under **Configure → Advanced settings**: *Quiet
  period between sessions (seconds)* (default 10, `0` disables) gates the
  minimum gap between dongle TCP sessions, and *Follow brew progress up
  to (seconds)* (default 120) caps how long a running brew's progress
  stream holds the session. Both were hard-coded before; values persist on
  the config entry (options, YAML-era data fallback) and applying them
  reloads the integration.

### Fixed

- Pressing **Configure** returned a 500: the options form embedded
  `BooleanSelector`/`NumberSelector` *instances* in the voluptuous
  `description`, which Home Assistant copies verbatim into the JSON
  field list it sends over the websocket API
  (`Unable to serialize to JSON. Bad data found at
  $.data_schema[0].description.selector=...`). Descriptions now carry the
  JSON-native selector config dicts. Broken since 1.0.0 (the
  `retain_when_offline` toggle alone triggered it).

## [1.2.0] — 2026-11-01

### Added

- The status sensor now exposes poll health via two always-present
  attributes: `last_successful_update` (UTC ISO timestamp, `null` before
  the first successful read) and `last_poll_success` (boolean). Retained
  last-known values used to be indistinguishable from a fresh read during
  a tolerated offline poll; now only a successful backend fetch renews the
  timestamp, so automations can gate on real freshness. Command or brew
  acknowledgements alone do not renew poll health. The timestamp is a
  string — compare it in templates via `as_timestamp`.

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
