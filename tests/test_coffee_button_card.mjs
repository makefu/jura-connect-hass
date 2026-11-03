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
}

function load() {
  const defined = {};
  const module = { exports: {} };
  const sandbox = {
    HTMLElement: FakeHTMLElement,
    CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
    document: { createElement: () => new FakeEl() },
    customElements: { get: () => undefined, define: (n, cls) => { defined[n] = cls; } },
    window: {},
    console: { info() {}, warn() {} },
    setTimeout: () => 0,
    clearTimeout: () => {},
    module,
  };
  vm.runInNewContext(src, sandbox, { filename: "jura-coffee-button-card.js" });
  return { Card: defined["jura-coffee-button-card"], Editor: defined["jura-coffee-button-card-editor"], api: module.exports, window: sandbox.window };
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
