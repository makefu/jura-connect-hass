// Regression tests for www/jura-brew-card.js, run with `node --test tests/`.
// The card is a plain browser script, so it is evaluated in a vm with a
// minimal DOM stub; no bundler or npm deps.
//
// Bug regression: the entity registry reassigns entity_ids on rename, so the
// brew entities (select.kuche_kaffeebert_brew_*) and the connectivity sensor
// (binary_sensor.kaffeebert_connectivity) can end up on different slugs for
// the same device. The card derived one slug from the brew select, missed the
// connectivity sensor, and rendered a dead machine as "Online".

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync(new URL("../www/jura-brew-card.js", import.meta.url), "utf8");

class FakeEl {
  constructor() {
    this.dataset = {};
    this.style = {};
    this.children = [];
    this._cls = new Set();
    this.classList = {
      toggle: (name, force) => {
        const on = force === undefined ? !this._cls.has(name) : !!force;
        on ? this._cls.add(name) : this._cls.delete(name);
      },
      add: (name) => this._cls.add(name),
      contains: (name) => this._cls.has(name),
    };
  }
  set className(v) {
    this._cls = new Set(String(v).split(/\s+/).filter(Boolean));
  }
  get className() {
    return [...this._cls].join(" ");
  }
  set innerHTML(v) {
    this._html = v;
  }
  get innerHTML() {
    return this._html;
  }
  appendChild(c) {
    this.children.push(c);
  }
  remove() {}
  addEventListener() {}
  setAttribute(k, v) {
    this[k] = v;
  }
  // Return one cached fake per selector so repeated queries see the same node.
  querySelector(sel) {
    this._q ??= new Map();
    if (!this._q.has(sel)) this._q.set(sel, new FakeEl());
    return this._q.get(sel);
  }
}

class FakeHTMLElement {
  attachShadow() {
    this.shadowRoot ??= new FakeEl();
    return this.shadowRoot;
  }
}

function loadCard() {
  let Card = null;
  const sandbox = {
    HTMLElement: FakeHTMLElement,
    document: { createElement: () => new FakeEl() },
    customElements: { get: () => undefined, define: (_n, cls) => (Card = cls) },
    window: {},
    console: { warn() {}, info() {} },
  };
  vm.runInNewContext(src, sandbox, { filename: "jura-brew-card.js" });
  return Card;
}

// Real-world registry drift from the live install (verified via ha-mcp):
// brew entities under `kuche_kaffeebert`, connectivity/status under
// `kaffeebert`, machine offline (connectivity = off, status = unavailable).
const driftedStates = {
  "select.kuche_kaffeebert_brew_product": { entity_id: "select.kuche_kaffeebert_brew_product", state: "espresso", attributes: { options: ["Factory Default", "espresso"] } },
  "button.kuche_kaffeebert_brew": { entity_id: "button.kuche_kaffeebert_brew", state: "2026-07-12T09:26:16+00:00", attributes: {} },
  "binary_sensor.kaffeebert_connectivity": { entity_id: "binary_sensor.kaffeebert_connectivity", state: "off", attributes: {} },
  "sensor.kaffeebert_status": { entity_id: "sensor.kaffeebert_status", state: "unavailable", attributes: {} },
};

test("machine: pin resolves drifted ids by slug token", () => {
  const Card = loadCard();
  const card = new Card();
  card.setConfig({ machine: "kaffeebert" });
  const ids = card._resolveEntities({ states: driftedStates });
  // kaffeebert shares a token with the drifted kuche_kaffeebert_* ids, so the
  // whole machine resolves even though no exact select.kaffeebert_brew_product exists.
  assert.equal(ids.product, "select.kuche_kaffeebert_brew_product");
  assert.equal(ids.button, "button.kuche_kaffeebert_brew");
  assert.equal(ids.connectivity, "binary_sensor.kaffeebert_connectivity");
  assert.equal(ids.status, "sensor.kaffeebert_status");
});

test("grinder ratio resolves with the same slug-drift rules", () => {
  const Card = loadCard();
  const card = new Card();
  card.setConfig({ machine: "kaffeebert" });
  const states = {
    ...driftedStates,
    "select.kuche_kaffeebert_brew_grinder_ratio": {
      entity_id: "select.kuche_kaffeebert_brew_grinder_ratio",
      state: "100_0",
      attributes: { options: ["Factory Default", "100_0", "0_100"] },
    },
  };
  const ids = card._resolveEntities({ states });
  assert.equal(ids.grinder_ratio, "select.kuche_kaffeebert_brew_grinder_ratio");
  assert.equal(card._formatValue({ key: "grinder_ratio" }, "75_25"), "75:25");
});

test("machine: pin never drives another machine's entities", () => {
  const Card = loadCard();
  const card = new Card();
  card.setConfig({ machine: "saugbert" });
  const states = {
    ...driftedStates,
    "select.saugbert_brew_product": { entity_id: "select.saugbert_brew_product", state: "espresso", attributes: { options: [] } },
    "binary_sensor.other_connectivity": { entity_id: "binary_sensor.other_connectivity", state: "on", attributes: {} },
  };
  const ids = card._resolveEntities({ states });
  assert.equal(ids.product, "select.saugbert_brew_product");
  // No saugbert connectivity/status and no exact ids -> must NOT fall back to
  // the kaffeebert/other entities.
  assert.equal(ids.connectivity, undefined);
  assert.equal(ids.status, undefined);
});

function render(Card, states, config = { title: "Coffee" }) {
  const card = new Card();
  card.setConfig(config);
  card.hass = { states, callService: () => {} };
  return card;
}

function pillAndButton(card) {
  const root = card.shadowRoot;
  return {
    pill: root.querySelector(".pill"),
    pillText: root.querySelector(".pill-text"),
    brew: root.querySelector("button.brew"),
  };
}

test("offline machine renders Offline pill despite slug-drifted connectivity entity", () => {
  const Card = loadCard();
  const { pill, pillText, brew } = pillAndButton(render(Card, driftedStates));
  assert.equal(pillText.textContent, "Offline");
  assert.ok(pill._cls.has("offline"));
  assert.equal(brew.disabled, true);
});

test("resolves drifted status + connectivity ids; prefers exact slug match", () => {
  const Card = loadCard();
  const card = new Card();
  card.setConfig({});
  const ids = card._resolveEntities({ states: driftedStates });
  assert.equal(ids.connectivity, "binary_sensor.kaffeebert_connectivity");
  assert.equal(ids.status, "sensor.kaffeebert_status");

  // When both a slug-matching and a foreign-slug sensor exist, the exact
  // slug match must win (two machines on one HA instance).
  const twoMachines = {
    ...driftedStates,
    "binary_sensor.kuche_kaffeebert_connectivity": { entity_id: "binary_sensor.kuche_kaffeebert_connectivity", state: "on", attributes: {} },
  };
  const ids2 = card._resolveEntities({ states: twoMachines });
  assert.equal(ids2.connectivity, "binary_sensor.kuche_kaffeebert_connectivity");
});

test("ambiguous foreign candidates without slug token overlap are not picked", () => {
  const Card = loadCard();
  const card = new Card();
  card.setConfig({});
  const states = {
    ...driftedStates,
    "binary_sensor.other_connectivity": { entity_id: "binary_sensor.other_connectivity", state: "on", attributes: {} },
    "binary_sensor.kaffeebert_connectivity": { entity_id: "binary_sensor.kaffeebert_connectivity", state: "off", attributes: {} },
  };
  delete states["select.kuche_kaffeebert_brew_product"];
  states["select.zzz_brew_product"] = { entity_id: "select.zzz_brew_product", state: "espresso", attributes: { options: [] } };
  const ids = card._resolveEntities({ states });
  // slug is "zzz": neither candidate shares a token -> no pick, no crash.
  assert.equal(ids.connectivity, undefined);
});

test("online machine renders status pill, not Offline", () => {
  const Card = loadCard();
  const states = {
    ...driftedStates,
    "binary_sensor.kaffeebert_connectivity": { entity_id: "binary_sensor.kaffeebert_connectivity", state: "on", attributes: {} },
    "sensor.kaffeebert_status": { entity_id: "sensor.kaffeebert_status", state: "ready", attributes: {} },
  };
  const { pill, pillText, brew } = pillAndButton(render(Card, states));
  assert.equal(pillText.textContent, "Ready");
  assert.ok(pill._cls.has("ready"));
  assert.equal(brew.disabled, false);
});
