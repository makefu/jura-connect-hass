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

// Strip comments and blank out string / template-literal / regex *contents*
// so the syntax gate below only sees real code. String-aware: a naive
// line-comment strip eats `https://` URLs, and a naive quote scan dies on
// apostrophes in comments. Also marks which characters sit directly in a
// class body (vs. method bodies, strings, comments), for the class-field
// check. No npm deps; a small hand-rolled scanner.
function stripNonCode(code) {
  const REGEX_OK = new Set(["(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*", "%", "<", ">", "~", "^", "\n"]);
  const REGEX_KEYWORDS = new Set(["return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "case", "do", "else", "yield", "await"]);
  const blank = (s) => s.replace(/[^\n]/g, " ");
  let out = "";
  let classOut = "";
  // Scanner frames: kind root|class|plain|expr|template, plus `cls` = the
  // characters emitted while this frame is on top sit directly in a class
  // body (method bodies, strings, comments and template text are not).
  const stack = [{ kind: "root", depth: 0, cls: false }];
  const top = () => stack[stack.length - 1];
  const emitCode = (s) => { out += s; classOut += top().cls ? s : blank(s); };
  const emitBlanked = (s) => { out += blank(s); classOut += blank(s); };
  let i = 0;
  let prevSig = "\n";
  let lastWord = "";
  let pendingClass = false;
  const afterChar = (c) => { prevSig = c; lastWord = ""; };
  while (i < code.length) {
    const frame = top();
    const c = code[i];
    if (frame.kind === "template") {
      if (c === "\\") { emitBlanked(code.slice(i, i + 2)); i += 2; continue; }
      if (c === "`") { emitCode(c); stack.pop(); i += 1; afterChar(c); continue; }
      if (c === "$" && code[i + 1] === "{") {
        emitCode("${"); i += 2; prevSig = "{"; lastWord = "";
        stack.push({ kind: "expr", depth: 0, cls: frame.cls });
        continue;
      }
      emitBlanked(c); i += 1; continue;
    }
    // --- code frame ---
    if (c === "/" && code[i + 1] === "/") {
      while (i < code.length && code[i] !== "\n") i += 1;
      continue; // keep the newline itself
    }
    if (c === "/" && code[i + 1] === "*") {
      i += 2;
      while (i < code.length && !(code[i] === "*" && code[i + 1] === "/")) {
        emitBlanked(code[i]); i += 1;
      }
      i += 2; continue;
    }
    if (c === '"' || c === "'") {
      let j = i + 1;
      let closed = false;
      while (j < code.length) {
        if (code[j] === "\\") { j += 2; continue; }
        if (code[j] === c) { closed = true; break; }
        if (code[j] === "\n") break;
        j += 1;
      }
      if (closed) { emitCode(c); emitBlanked(code.slice(i + 1, j)); emitCode(c); i = j + 1; afterChar(c); continue; }
      emitCode(c); i += 1; afterChar(c); continue; // stray quote: treat as code
    }
    if (c === "`") { emitCode(c); i += 1; afterChar(c); stack.push({ kind: "template", depth: 0, cls: frame.cls }); continue; }
    if (c === "/" && (REGEX_OK.has(prevSig) || REGEX_KEYWORDS.has(lastWord))) {
      // Regex literal; bail to division handling at an unclosed class or newline.
      let j = i + 1;
      let closed = false;
      while (j < code.length) {
        if (code[j] === "\\") { j += 2; continue; }
        if (code[j] === "[") {
          j += 1;
          while (j < code.length && code[j] !== "]") { if (code[j] === "\\") j += 1; j += 1; }
        } else if (code[j] === "/") { closed = true; break; } else if (code[j] === "\n") break;
        j += 1;
      }
      if (closed) {
        let k = j + 1;
        while (k < code.length && /[a-z]/i.test(code[k])) k += 1;
        emitCode("/"); emitBlanked(code.slice(i + 1, k)); i = k; afterChar("/"); continue;
      }
      // not a regex -> fall through and treat `/` as a plain operator
    }
    if (/[A-Za-z0-9_$]/.test(c)) {
      let j = i;
      while (j < code.length && /[A-Za-z0-9_$]/.test(code[j])) j += 1;
      lastWord = code.slice(i, j);
      if (lastWord === "class" && !/[A-Za-z0-9_$.]/.test(prevSig)) pendingClass = true;
      emitCode(lastWord); i = j; prevSig = lastWord[lastWord.length - 1]; continue;
    }
    if (c === "{") {
      emitCode(c); i += 1; afterChar(c);
      if (pendingClass) {
        pendingClass = false;
        stack.push({ kind: "class", depth: 0, cls: true });
      } else if (frame.kind === "expr" && frame.depth === 0) {
        frame.depth += 1; // block inside a template expression: not a closer
      } else if (frame.kind !== "root") {
        stack.push({ kind: "plain", depth: 0, cls: false });
      } else {
        frame.depth += 1;
      }
      continue;
    }
    if (c === "}") {
      emitCode(c); i += 1; afterChar(c);
      if (frame.depth > 0) frame.depth -= 1;
      else if (frame.kind !== "root") stack.pop();
      continue;
    }
    if (c === ";") { pendingClass = false; emitCode(c); i += 1; afterChar(c); continue; }
    if (!/\s/.test(c)) afterChar(c);
    emitCode(c); i += 1;
  }
  return { code: out, classCode: classOut };
}

test("no syntax newer than old Android WebViews parse", () => {
  // Each entry is a parse/runtime feature missing from the engines in scope
  // (Chromium 80-ish and older): a parse error loses the whole file.
  const BANNED = [
    ["optional chaining", /\?\.(?!\d)/],
    ["nullish coalescing", /\?\?/],
    ["logical assignment", /(\|\||&&)=/],
    ["Array.prototype.at", /\.at\(/],
    ["private class members", /#/],
    ["parameter-less catch", /\bcatch\s*\{/],
    ["Object.hasOwn", /\bObject\.hasOwn\s*\(/],
    ["String.prototype.replaceAll", /\.replaceAll\(/],
    ["Array.prototype.flatMap", /\.flatMap\(/],
    ["structuredClone", /\bstructuredClone\s*\(/],
  ];
  // Class fields (public/private, incl. static) need Chrome 72/74; match only
  // at class-body level so object literals and statements do not false-match.
  const CLASS_FIELD = /(?:^|\n)[ \t]*(?:static[ \t]+)?#?[A-Za-z_$][\w$]*(?:[ \t]*=[^;]*)?[ \t]*;/;
  for (const name of CARDS) {
    const { code, classCode } = stripNonCode(source(name));
    for (const [label, pattern] of BANNED) {
      assert.doesNotMatch(code, pattern, `${name}: ${label}`);
    }
    assert.doesNotMatch(classCode, CLASS_FIELD, `${name}: class field`);
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
    // Walk back to the line starting this declaration, stopping at the
    // nearest rule boundary so a stray value cannot spin past line 0 or
    // borrow a property from a previous rule.
    let start = i;
    while (start >= 0 && !/^\s*[a-z-]+:/.test(lines[start])) {
      if (lines[start].includes("{") || lines[start].includes("}")) break;
      start -= 1;
    }
    assert.ok(start >= 0 && /^\s*[a-z-]+:/.test(lines[start]), `line ${i + 1}: color-mix() value does not start at a declaration`);
    const prop = lines[start].trim().split(":")[0];
    const before = (lines[start - 1] || "").trim();
    assert.ok(
      before.startsWith(`${prop}:`) && !before.includes("color-mix("),
      `line ${i + 1}: ${prop} needs a fallback before color-mix()`,
    );
  });
});
