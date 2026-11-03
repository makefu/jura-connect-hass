// Compatibility of the bundled cards with older WebViews (Shelly Wall
// Display, old tablets), run with `node --test tests/`. Those clients get
// Home Assistant's legacy frontend build, which loads cards as *classic*
// scripts sharing one global scope, and their engines lack recent syntax
// and CSS.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const CARDS = ["jura-brew-card.js", "jura-coffee-button-card.js"];
const source = (name) => readFileSync(new URL(`../custom_components/jura/www/${name}`, import.meta.url), "utf8");

function sharedGlobalContext() {
  const defined = {};
  const context = vm.createContext({
    HTMLElement: class {},
    CustomEvent: class {},
    document: { createElement: () => ({}) },
    customElements: { get: (n) => defined[n], define: (n, cls) => { defined[n] = cls; }, whenDefined: async () => {} },
    window: {},
    console: { info() {}, warn() {}, error() {} },
    setTimeout: () => 0,
    clearTimeout: () => {},
  });
  return { context, defined };
}

test("both cards load as classic scripts in one shared global scope", () => {
  // Regression: both files declared top-level ATTENTION / statusMood / …;
  // the second classic script died with "Identifier 'ATTENTION' has already
  // been declared", so its card never existed on legacy-build clients.
  const { context, defined } = sharedGlobalContext();
  for (const name of CARDS) vm.runInContext(source(name), context, { filename: name });
  assert.ok(defined["jura-brew-card"]);
  assert.ok(defined["jura-coffee-button-card"]);
  assert.ok(defined["jura-coffee-button-card-editor"]);
  assert.equal(context.window.customCards.length, 2);
});

test("no syntax newer than old Android WebViews parse", () => {
  // Optional chaining / nullish coalescing (Chrome 80) and logical
  // assignment (Chrome 85) are a parse error there: the whole file is lost.
  for (const name of CARDS) {
    const code = source(name).replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    for (const [label, pattern] of [
      ["optional chaining", /\?\.(?!\d)/],
      ["nullish coalescing", /\?\?/],
      ["logical assignment", /(\|\||&&)=/],
      ["Array.prototype.at", /\.at\(/],
    ]) {
      assert.doesNotMatch(code, pattern, `${name}: ${label}`);
    }
  }
});

test("every color-mix() has a plain fallback declared right before it", () => {
  // color-mix() needs Chrome 111; an engine that cannot parse it drops the
  // declaration and keeps the fallback (else: white cup icon on white).
  const lines = source("jura-coffee-button-card.js")
    .replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, " ")) // keep line numbers
    .split("\n");
  lines.forEach((line, i) => {
    if (!line.includes("color-mix(")) return;
    let start = i;
    while (!/^\s*[a-z-]+:/.test(lines[start])) start -= 1; // multi-line value
    const prop = lines[start].trim().split(":")[0];
    const before = lines[start - 1].trim();
    assert.ok(
      before.startsWith(`${prop}:`) && !before.includes("color-mix("),
      `line ${i + 1}: ${prop} needs a fallback before color-mix()`,
    );
  });
});
