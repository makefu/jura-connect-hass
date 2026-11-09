// Tests for custom_components/jura/www/jura-coffee-button-card.js, run with
// `node --test tests/`. The card is a plain browser script; it is evaluated
// in a vm with a minimal DOM stub (no bundler, no npm deps).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync(
  new URL("../custom_components/jura/www/jura-coffee-button-card.js", import.meta.url),
  "utf8",
);

class FakeEl {
  constructor() {
    this.style = {};
    this.className = "";
    this.attrs = {};
    this.listeners = {};
    this.textContent = "";
    this.classList = {
      add: (c) => { this.className = `${this.className} ${c}`.trim(); },
      remove: (c) => { this.className = this.className.split(" ").filter((x) => x !== c).join(" "); },
    };
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  appendChild() {}
  querySelector(sel) {
    this._q ??= new Map();
    if (!this._q.has(sel)) this._q.set(sel, new FakeEl());
    return this._q.get(sel);
  }
}

class FakeHTMLElement {
  attachShadow() { this.shadowRoot ??= new FakeEl(); return this.shadowRoot; }
  dispatchEvent(ev) { (this.events ??= []).push(ev); }
  appendChild(child) { (this.children ??= []).push(child); }
}

// Records setTimeout/clearTimeout calls so tests can inspect and fire timers.
function makeTimers() {
  const live = [];
  let seq = 0;
  return {
    set: (fn, ms) => { const id = ++seq; live.push({ id, fn, ms, cleared: false }); return id; },
    clear: (id) => { const t = live.find((x) => x.id === id); if (t) t.cleared = true; },
    armed: () => live.filter((t) => !t.cleared),
    fire: () => { const due = live.filter((t) => !t.cleared); due.forEach((t) => { t.cleared = true; t.fn(); }); },
  };
}

const drain = () => new Promise((r) => setImmediate(r));

function load({ haForm = true, window = {}, timers = null } = {}) {
  const defined = {};
  const module = { exports: {} };
  const created = [];
  // Mutable so tests can make <ha-form> appear after the card loaded.
  const state = { haForm };
  const waiters = {};
  const defineHaForm = () => {
    state.haForm = true;
    (waiters["ha-form"] || []).forEach((r) => r());
    waiters["ha-form"] = [];
  };
  const sandbox = {
    HTMLElement: FakeHTMLElement,
    CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
    document: { createElement: (tag) => { const el = new FakeEl(); el.tag = tag; created.push(el); return el; } },
    customElements: {
      get: (n) => (n === "ha-form" ? (state.haForm ? class {} : defined[n]) : defined[n]),
      define: (n, cls) => { defined[n] = cls; (waiters[n] || []).forEach((r) => r()); waiters[n] = []; },
      whenDefined: (n) =>
        n === "ha-form" && state.haForm
          ? Promise.resolve()
          : new Promise((res) => { (waiters[n] ??= []).push(res); }),
    },
    window,
    console: { info() {}, warn() {} },
    setTimeout: timers ? timers.set : () => 0,
    clearTimeout: timers ? timers.clear : () => {},
    module,
  };
  vm.runInNewContext(src, sandbox, { filename: "jura-coffee-button-card.js" });
  return {
    Card: defined["jura-coffee-button-card"],
    Editor: defined["jura-coffee-button-card-editor"],
    api: module.exports,
    window: sandbox.window,
    created,
    defineHaForm,
  };
}

const ITEMS_1_10 = Array.from({ length: 10 }, (_, i) => ({ name: `${i + 1}`, value: i + 1 }));
const TEMPS = [{ name: "low", value: 0 }, { name: "normal", value: 1 }, { name: "high", value: 2 }];
const MACHINE = {
  entry_id: "e1",
  title: "Kaffeebert",
  device_id: "dev-1",
  entities: {
    status: "sensor.kaffeebert_status",
    connectivity: "binary_sensor.kaffeebert_connectivity",
    progress: "sensor.kaffeebert_brew_progress",
  },
  products: [
    {
      code: 2, name: "espresso", label: "Espresso",
      params: [
        { field: "strength", kind: "coffee_strength", default: 8, items: ITEMS_1_10 },
        { field: "water_ml", kind: "water_amount", default: 45, min: 15, max: 80, step: 5 },
        { field: "temperature", kind: "temperature", default: 2, items: TEMPS },
      ],
    },
    {
      code: 4, name: "cappuccino", label: "Cappuccino",
      params: [
        { field: "strength", kind: "coffee_strength", default: 6, items: ITEMS_1_10 },
        { field: "milk_foam_s", kind: "milk_foam_amount", default: 14, min: 3, max: 120, step: 1 },
      ],
    },
  ],
};
const OTHER = { ...MACHINE, entry_id: "e2", title: "Büro", device_id: "dev-2" };

const DE = {
  "component.jura.entity.select.brew_product.state.espresso": "Espresso",
  "component.jura.entity.select.brew_product.state.cappuccino": "Cappuccino",
  "component.jura.entity.select.brew_temperature.state.high": "Hoch",
  "component.jura.entity.select.brew_temperature.state.low": "Niedrig",
};

function hass(states = {}, { language = "de", machines = [MACHINE] } = {}) {
  const calls = [];
  return {
    language,
    locale: { language },
    localize: (k) => (language === "de" ? DE[k] || "" : ""),
    states: {
      "binary_sensor.kaffeebert_connectivity": { state: "on", attributes: {} },
      "sensor.kaffeebert_status": { state: "ready", attributes: {} },
      ...states,
    },
    callWS: async () => ({ machines }),
    callService: async (...args) => { calls.push(args); },
    calls,
  };
}

async function rendered(config, h = hass()) {
  const { Card } = load();
  const card = new Card();
  card.setConfig(config);
  card.hass = h;
  await new Promise((r) => setImmediate(r));
  return { card, root: card.shadowRoot, h };
}

test("registers card + editor in the card picker, 2x2 footprint", () => {
  const { Card, Editor, window } = load();
  assert.ok(Card && Editor);
  assert.equal(window.customCards[0].type, "jura-coffee-button-card");
  const card = new Card();
  assert.equal(card.getCardSize(), 2);
  assert.deepEqual({ ...card.getGridOptions() }, { columns: 6, rows: 2, min_columns: 4, min_rows: 2, max_rows: 2 });
  assert.equal(card.getLayoutOptions().grid_columns, 2);
  assert.equal(card.getLayoutOptions().grid_rows, 2);
});

test("machine resolution: device pin, entry pin, first machine", () => {
  const { api } = load();
  const machines = [MACHINE, OTHER];
  assert.equal(api.resolveMachine(machines, {}).entry_id, "e1");
  assert.equal(api.resolveMachine(machines, { device_id: "dev-2" }).entry_id, "e2");
  assert.equal(api.resolveMachine(machines, { entry_id: "e2" }).entry_id, "e2");
  // A pinned machine never silently falls back to another one.
  assert.equal(api.resolveMachine(machines, { device_id: "gone" }), undefined);
});

test("service data carries only the product's own personal parameters", () => {
  const { api } = load();
  const espresso = MACHINE.products[0];
  const data = api.buildServiceData(
    { product: "espresso", strength: 9, water_ml: "40", temperature: 0, milk_foam_s: 10 },
    MACHINE,
    espresso,
  );
  assert.deepEqual({ ...data }, { config_entry_id: "e1", product: "espresso", strength: 9, water_ml: 40, temperature: 0 });
  // Nothing personal -> factory recipe.
  assert.deepEqual({ ...api.buildServiceData({ product: "espresso" }, MACHINE, espresso) }, {
    config_entry_id: "e1",
    product: "espresso",
  });
});

test("tile shows translated product + personal summary, brews via jura.brew", async () => {
  const { card, root, h } = await rendered({ product: "espresso", strength: 9, water_ml: 40 });
  assert.equal(root.querySelector(".name").textContent, "Espresso");
  // Personal values, profile default for the rest (temperature 2 = Hoch).
  assert.equal(root.querySelector(".sub").textContent, "Stärke 9 · 40 ml · Hoch");
  assert.equal(root.querySelector(".label").textContent, "Brühen");
  assert.equal(root.querySelector("button").disabled, false);

  await card._onPress();
  assert.deepEqual(h.calls[0][0], "jura");
  assert.deepEqual(h.calls[0][1], "brew");
  assert.deepEqual({ ...h.calls[0][2] }, { config_entry_id: "e1", product: "espresso", strength: 9, water_ml: 40 });
});

test("English UI labels", async () => {
  const { root } = await rendered({ product: "espresso" }, hass({}, { language: "en" }));
  assert.equal(root.querySelector(".label").textContent, "Brew");
  assert.equal(root.querySelector(".sub").textContent, "Strength 8 · 45 ml · High");
});

test("offline machine disables the button and never brews", async () => {
  const h = hass({ "binary_sensor.kaffeebert_connectivity": { state: "off", attributes: {} } });
  const { card, root } = await rendered({ product: "espresso" }, h);
  assert.equal(root.querySelector("button").disabled, true);
  assert.equal(root.querySelector(".label").textContent, "Offline");
  assert.match(root.querySelector(".dot").className, /offline/);
  await card._onPress();
  assert.equal(h.calls.length, 0);
});

test("confirm: first tap arms, second tap brews", async () => {
  const { card, root, h } = await rendered({ product: "espresso", confirm: true });
  await card._onPress();
  assert.equal(h.calls.length, 0);
  assert.equal(root.querySelector(".label").textContent, "Nochmal tippen");
  await card._onPress();
  assert.equal(h.calls.length, 1);
});

test("brew error is surfaced on the tile", async () => {
  const h = hass();
  h.callService = async () => { throw new Error("no_beans"); };
  const { card, root } = await rendered({ product: "espresso" }, h);
  await card._onPress();
  assert.equal(root.querySelector(".label").textContent, "Fehler");
  assert.equal(root.querySelector(".sub").textContent, "no_beans");
});

test("unknown product shows a hint instead of brewing something else", async () => {
  const { card, root, h } = await rendered({ product: "ristretto" });
  assert.equal(root.querySelector(".sub").textContent, "Getränk nicht verfügbar");
  assert.equal(root.querySelector("button").disabled, true);
  await card._onPress();
  assert.equal(h.calls.length, 0);
});

test("editor offers only the chosen product's parameters", () => {
  const { api } = load();
  const h = hass();
  const fields = (schema) => [...schema.flatMap((s) => (s.schema ? fields(s.schema) : [s.name]))];

  const espresso = fields(api.editorSchema(h, [MACHINE], MACHINE, MACHINE.products[0]));
  assert.deepEqual(espresso, ["product", "name", "icon", "strength", "water_ml", "temperature", "confirm"]);
  const cappu = fields(api.editorSchema(h, [MACHINE, OTHER], MACHINE, MACHINE.products[1]));
  assert.deepEqual(cappu, ["device_id", "product", "name", "icon", "strength", "milk_foam_s", "confirm"]);

  const water = api.editorSchema(h, [MACHINE], MACHINE, MACHINE.products[0])
    .find((s) => s.type === "expandable").schema.find((s) => s.name === "water_ml");
  assert.deepEqual({ ...water.selector.number }, { min: 15, max: 80, step: 5, mode: "slider", unit_of_measurement: "ml" });
});

test("editor round-trip: numbers back, cleared + foreign params dropped", () => {
  const { api } = load();
  const form = api.toFormData({ product: "espresso", temperature: 2 }, MACHINE.products[0]);
  assert.equal(form.temperature, "2");
  assert.equal(form.strength, "");

  // Switching to cappuccino drops water/temperature; "" = factory default.
  const config = api.fromFormData(
    { product: "cappuccino", strength: "", water_ml: 40, temperature: "2", milk_foam_s: 20, name: "", confirm: false },
    MACHINE,
  );
  assert.deepEqual({ ...config }, { product: "cappuccino", milk_foam_s: 20 });
});

// --- Regression: an unconfigured tile always brewed an espresso -------------
// The card picker created `{}`; the card fell back to the first profile
// product (espresso) and the editor -- with ha-form not yet loaded by Home
// Assistant -- showed no drink picker, so the drink could not be changed.

test("tile without a drink never guesses one (no espresso fallback)", async () => {
  const { card, root, h } = await rendered({});
  assert.equal(root.querySelector(".sub").textContent, "Getränk im Karteneditor wählen");
  assert.equal(root.querySelector("button").disabled, true);
  await card._onPress();
  assert.equal(h.calls.length, 0);
});

test("chosen drink is the one that gets brewed", async () => {
  const { card, root, h } = await rendered({ product: "cappuccino", milk_foam_s: 18 });
  assert.equal(root.querySelector(".name").textContent, "Cappuccino");
  assert.equal(root.querySelector(".sub").textContent, "Stärke 6 · Schaum 18 s");
  await card._onPress();
  assert.deepEqual({ ...h.calls[0][2] }, { config_entry_id: "e1", product: "cappuccino", milk_foam_s: 18 });
});

test("card picker stub names the drink explicitly", async () => {
  const { Card } = load();
  assert.deepEqual({ ...(await Card.getStubConfig(hass())) }, { product: "espresso" });
  // Integration not answering: still a valid (empty) stub, no crash.
  const broken = hass();
  broken.callWS = async () => { throw new Error("unknown command"); };
  const fresh = load().Card;
  assert.deepEqual({ ...(await fresh.getStubConfig(broken)) }, {});
});

async function openEditor(config, opts) {
  const env = load(opts);
  const editor = new env.Editor();
  const changes = [];
  editor.dispatchEvent = (ev) => changes.push(ev.detail.config);
  editor.hass = hass();
  editor.setConfig(config);
  await new Promise((r) => setImmediate(r));
  return { ...env, editor, changes };
}

test("editor writes the drink into a config that has none", async () => {
  const { changes, created } = await openEditor({});
  assert.equal(changes.at(-1).product, "espresso");
  // ...and renders the form with the drink picker first.
  const form = created.find((el) => el.tag === "ha-form");
  assert.ok(form, "ha-form rendered");
  assert.equal(form.schema[0].name, "product");
  assert.deepEqual([...form.schema[0].selector.select.options.map((o) => o.value)], ["espresso", "cappuccino"]);
  assert.equal(form.data.product, "espresso");
});

test("editor drink change sticks and drops the old drink's parameters", async () => {
  const { editor, changes, created } = await openEditor({ product: "espresso", water_ml: 40 });
  const form = created.find((el) => el.tag === "ha-form");
  form.listeners["value-changed"]({ detail: { value: { ...form.data, product: "cappuccino" } } });
  assert.deepEqual({ ...changes.at(-1) }, { product: "cappuccino" });
  editor.setConfig(changes.at(-1));
  assert.equal(form.data.product, "cappuccino");
});

test("editor loads ha-form via the card helpers before rendering", async () => {
  const loaded = [];
  const window = {
    loadCardHelpers: async () => ({
      createCardElement: async (cfg) => {
        loaded.push(cfg.type);
        return { constructor: { getConfigElement: async () => loaded.push("editor") } };
      },
    }),
  };
  const { changes, created } = await openEditor({}, { haForm: false, window });
  assert.deepEqual(loaded, ["button", "editor"]);
  // The drink is saved even though ha-form is still unknown here.
  assert.equal(changes.at(-1).product, "espresso");
  assert.equal(created.filter((el) => el.tag === "ha-form").length, 0);
});

test("card picker lists the card once even if a stale entry exists", () => {
  const window = { customCards: [{ type: "jura-coffee-button-card", name: "Jura Coffee Button" }] };
  load({ window });
  assert.equal(window.customCards.filter((c) => c.type === "jura-coffee-button-card").length, 1);
});

// --- Offline handling --------------------------------------------------------

test("connectivity unavailable disables brewing", async () => {
  const h = hass({ "binary_sensor.kaffeebert_connectivity": { state: "unavailable", attributes: {} } });
  const { card, root } = await rendered({ product: "espresso" }, h);
  assert.equal(root.querySelector("button").disabled, true);
  await card._onPress();
  assert.equal(h.calls.length, 0);
});

test("machine unreachable at HA start (setup retry): drink shown, clear hint, no brew", async () => {
  const retrying = { ...MACHINE, state: "setup_retry" };
  // Entities exist in the registry but are unavailable while setup retries.
  const h = hass(
    {
      "binary_sensor.kaffeebert_connectivity": { state: "unavailable", attributes: {} },
      "sensor.kaffeebert_status": { state: "unavailable", attributes: {} },
    },
    { machines: [retrying] },
  );
  const { card, root } = await rendered({ product: "espresso" }, h);
  assert.equal(root.querySelector(".name").textContent, "Espresso");
  assert.equal(root.querySelector(".sub").textContent, "Maschine nicht erreichbar, neuer Versuch läuft…");
  assert.equal(root.querySelector(".label").textContent, "Offline");
  assert.equal(root.querySelector("button").disabled, true);
  await card._onPress();
  assert.equal(h.calls.length, 0);
});

test("brew on a machine that just went away shows a translated hint", async () => {
  const h = hass();
  h.callService = async () => {
    const err = new Error("The coffee machine is not reachable. Is it switched on and connected to Wi-Fi?");
    err.translation_key = "machine_unreachable";
    throw err;
  };
  const { card, root } = await rendered({ product: "espresso" }, h);
  await card._onPress();
  assert.equal(root.querySelector(".label").textContent, "Fehler");
  assert.equal(root.querySelector(".sub").textContent, "Maschine nicht erreichbar");
});

// --- Not ready (heating up) -----------------------------------------------
// Regression: after power-on the tile offered "Brühen" while the machine was
// heating up; the press brewed nothing and blocked the integration.

const WITH_BUTTON = {
  ...MACHINE,
  entities: { ...MACHINE.entities, brew_button: "button.kaffeebert_brew" },
};

test("heating up: tile says not ready, shows why, never brews", async () => {
  const h = hass(
    {
      "sensor.kaffeebert_status": { state: "heating_up", attributes: {} },
      "button.kaffeebert_brew": { state: "unavailable", attributes: {} },
    },
    { machines: [WITH_BUTTON] },
  );
  h.formatEntityState = (st) => ({ heating_up: "Heizt auf" })[st.state] || st.state;
  const { card, root } = await rendered({ product: "espresso" }, h);
  assert.equal(root.querySelector(".label").textContent, "Nicht bereit");
  assert.equal(root.querySelector(".sub").textContent, "Heizt auf");
  assert.match(root.querySelector(".dot").className, /busy/);
  assert.equal(root.querySelector("button").disabled, true);
  await card._onPress();
  assert.equal(h.calls.length, 0);
});

test("ready again: the integration's brew button decides, tile brews", async () => {
  const h = hass(
    { "button.kaffeebert_brew": { state: "2026-11-03T08:00:00+00:00", attributes: {} } },
    { machines: [WITH_BUTTON] },
  );
  const { card, root } = await rendered({ product: "espresso" }, h);
  assert.equal(root.querySelector(".label").textContent, "Brühen");
  assert.equal(root.querySelector("button").disabled, false);
  await card._onPress();
  assert.equal(h.calls.length, 1);
});

test("a brew running elsewhere (e.g. before a dashboard reload) blocks the tile", async () => {
  const h = hass(
    { "button.kaffeebert_brew": { state: "unavailable", attributes: {} } },
    { machines: [WITH_BUTTON] },
  );
  const { card, root } = await rendered({ product: "espresso" }, h);
  assert.equal(root.querySelector("button").disabled, true);
  await card._onPress();
  assert.equal(h.calls.length, 0);
});

for (const [key, text] of [
  ["machine_not_ready", "Maschine noch nicht bereit"],
  ["brew_in_progress", "Es wird bereits gebrüht"],
  ["brew_not_started", "Brühen nicht gestartet, heizt sie noch auf?"],
]) {
  test(`integration error ${key} is shown in the user's language`, async () => {
    const h = hass();
    h.callService = async () => {
      const err = new Error("english text from HA");
      err.translation_key = key;
      throw err;
    };
    const { card, root } = await rendered({ product: "espresso" }, h);
    await card._onPress();
    assert.equal(root.querySelector(".sub").textContent, text);
  });
}

// --- Switching off / per-drink readiness ------------------------------------
// Regression: after power-off the status stayed switch_off_delay_active for
// minutes; the tiles stayed pressable and only failed after a while.

function statusWith(state, attrs) {
  return {
    "sensor.kaffeebert_status": {
      state,
      attributes: { brew_ready: true, brew_blocker: null, brewing: false, blocked_products: [], ...attrs },
    },
  };
}

test("switching off: not ready, says so in German, busy dot, never brews", async () => {
  const h = hass(statusWith("switch_off_delay_active", { brew_ready: false, brew_blocker: "switch_off_delay_active" }));
  const { card, root } = await rendered({ product: "espresso" }, h);
  assert.equal(root.querySelector(".label").textContent, "Nicht bereit");
  assert.equal(root.querySelector(".sub").textContent, "Schaltet aus");
  assert.match(root.querySelector(".dot").className, /busy/);
  assert.equal(root.querySelector("button").disabled, true);
  await card._onPress();
  assert.equal(h.calls.length, 0);
});

test("a drink the machine blocks is disabled, other drinks still brew", async () => {
  const states = statusWith("cappu_clean_alert", { blocked_products: ["cappuccino"] });
  const cappu = await rendered({ product: "cappuccino" }, hass(states));
  assert.equal(cappu.root.querySelector("button").disabled, true);
  await cappu.card._onPress();
  assert.equal(cappu.h.calls.length, 0);

  const espresso = await rendered({ product: "espresso" }, hass(states));
  assert.equal(espresso.root.querySelector("button").disabled, false);
  await espresso.card._onPress();
  assert.equal(espresso.h.calls.length, 1);
});

test("the brew panel's own product does not decide for this tile", async () => {
  // HA Brew button unavailable because the panel's product is blocked;
  // the status sensor says this tile's drink can be brewed.
  const h = hass(
    { ...statusWith("coffee_ready", {}), "button.kaffeebert_brew": { state: "unavailable", attributes: {} } },
    { machines: [WITH_BUTTON] },
  );
  const { root } = await rendered({ product: "espresso" }, h);
  assert.equal(root.querySelector("button").disabled, false);
  assert.equal(root.querySelector(".label").textContent, "Brühen");
});

test("a brew running elsewhere blocks via the status sensor too", async () => {
  const h = hass(statusWith("coffee_ready", { brew_ready: false, brewing: true }));
  const { root } = await rendered({ product: "espresso" }, h);
  assert.equal(root.querySelector("button").disabled, true);
});

// --- Detach lifecycle / timer hygiene ---------------------------------------
// Regression: navigating away left detached tiles re-fetching jura/machines
// every 30 s forever (and holding their hass reference via the closure).

test("disconnect clears the retry and phase timers; nothing fires afterwards", async () => {
  const timers = makeTimers();
  const starting = { ...MACHINE, state: "setup_retry" };
  const { Card } = load({ timers });
  const card = new Card();
  card.setConfig({ product: "espresso" });
  const h = hass(undefined, { machines: [starting] });
  const wsCalls = [];
  const callWS = h.callWS;
  h.callWS = (msg) => { wsCalls.push(msg); return callWS(msg); };
  card.hass = h;
  // Arm a transient phase timer next to the 30 s state poll. (A real confirm
  // tap would be blocked here: the machine is offline while starting up.)
  card._setPhase("confirm", 3000);
  await drain();
  assert.deepEqual(timers.armed().map((t) => t.ms).sort((a, b) => a - b), [3000, 30000]);

  card.disconnectedCallback();
  assert.equal(card._retryTimer, null);
  assert.equal(card._phaseTimer, null);
  assert.deepEqual(timers.armed(), []);

  timers.fire(); // the "30 s" poll must be gone
  await drain();
  assert.equal(wsCalls.length, 1, "only the initial load fetched");
});

test("an in-flight fetch resolving after disconnect does not re-arm polling", async () => {
  const timers = makeTimers();
  const starting = { ...MACHINE, state: "setup_retry" };
  const { Card } = load({ timers });
  const card = new Card();
  card.setConfig({ product: "espresso" });
  let release;
  const h = hass();
  h.callWS = () => new Promise((r) => { release = r; });
  card.hass = h;
  await drain();
  card.disconnectedCallback();
  release({ machines: [starting] });
  await drain();
  assert.deepEqual(timers.armed(), [], "detached card must not arm the 30 s retry");
});

// --- ha-form availability -----------------------------------------------------
// Regression: a rejected loadCardHelpers() stayed memoized, so every later
// editor reused the dead promise and the visual editor was permanently blank.

test("ensureHaForm does not memoize a rejected load", async () => {
  let attempts = 0;
  const window = {
    loadCardHelpers: async () => {
      attempts += 1;
      if (attempts <= 2) throw new Error("chunk load failed");
      return { createCardElement: async () => ({ constructor: { getConfigElement: async () => {} } }) };
    },
  };
  const env = load({ haForm: false, window });
  await assert.rejects(env.api.ensureHaForm());
  await assert.rejects(env.api.ensureHaForm()); // must retry, not reuse the dead promise
  assert.equal(attempts, 2);
  env.defineHaForm();
  await env.api.ensureHaForm();
  await env.api.ensureHaForm();
  assert.equal(attempts, 2, "no reload once ha-form is defined");
});

test("editor retries a failed ha-form load and renders once it exists", async () => {
  let attempts = 0;
  const window = {
    loadCardHelpers: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("chunk load failed");
      return { createCardElement: async () => ({ constructor: { getConfigElement: async () => {} } }) };
    },
  };
  const timers = makeTimers();
  const env = load({ haForm: false, window, timers });
  const editor = new env.Editor();
  editor.hass = hass();
  editor.setConfig({ product: "espresso" });
  await drain();
  assert.equal(attempts, 1, "first attempt rejected");
  assert.equal(timers.armed().length, 1, "backoff timer armed");
  assert.equal(editor._form, undefined, "blank until ha-form exists");

  timers.fire(); // backoff expiry -> retry; it now hangs on whenDefined("ha-form")
  await drain();
  assert.equal(attempts, 2, "retry ran");

  // A built-in editor defines ha-form later; the open editor must fill itself in.
  env.defineHaForm();
  await drain();
  const form = env.created.find((el) => el.tag === "ha-form");
  assert.ok(form, "ha-form rendered after the element became available");
  assert.equal(form.schema[0].name, "product");
});

test("editor stops retrying ha-form after the cap and on disconnect", async () => {
  const window = { loadCardHelpers: async () => { throw new Error("offline"); } };
  const timers = makeTimers();
  const env = load({ haForm: false, window, timers });
  const editor = new env.Editor();
  editor.hass = hass();
  editor.setConfig({});
  await drain();
  for (let i = 0; i < 10; i += 1) {
    timers.fire();
    await drain();
  }
  assert.ok(timers.armed().length <= 1, "retry chain is bounded");

  const timers2 = makeTimers();
  const env2 = load({ haForm: false, window, timers: timers2 });
  const editor2 = new env2.Editor();
  editor2.hass = hass();
  editor2.setConfig({});
  await drain();
  assert.equal(timers2.armed().length, 1, "backoff armed before disconnect");
  editor2.disconnectedCallback();
  assert.equal(editor2._haFormTimer, null);
  assert.deepEqual(timers2.armed(), [], "disconnect clears the pending retry");
});
