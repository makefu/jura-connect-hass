/*
 * jura-brew-card
 * A single Lovelace custom card for the makefu/jura-connect-hass integration.
 * Shows the machine status, the brew Product picker plus Strength / Water /
 * Temperature sliders, and a Brew button — all in one card.
 *
 * Zero-config: drop `type: custom:jura-brew-card` on a dashboard and it
 * auto-discovers the jura brew + status entities. With more than one machine
 * it auto-picks the first (alphabetically) and logs a warning — pin one with a
 * single `machine:` slug:
 *
 *   type: custom:jura-brew-card
 *   machine: kaffeebert   # matches entities by slug TOKEN, so it keeps
 *                         # working even when the registry assigned parts of
 *                         # the machine's entity set different prefixes
 *                         # (select.kuche_kaffeebert_* + binary_sensor.kaffeebert_*)
 *
 * or override any individual entity explicitly:
 *
 *   type: custom:jura-brew-card
 *   title: Kaffeebert
 *   product: select.kuche_kaffeebert_brew_product
 *   strength: select.kuche_kaffeebert_brew_strength
 *   water: select.kuche_kaffeebert_brew_water
 *   temperature: select.kuche_kaffeebert_brew_temperature
 *   grinder_ratio: select.kuche_kaffeebert_brew_grinder_ratio
 *   milk: select.kuche_kaffeebert_brew_milk
 *   milk_foam: select.kuche_kaffeebert_brew_milk_foam
 *   button: button.kuche_kaffeebert_brew
 *   status: sensor.kuche_kaffeebert_status
 *   connectivity: binary_sensor.kuche_kaffeebert_connectivity
 *
 * The Strength / Water / Temperature sliders sit at their leftmost notch when
 * the parameter is "Factory Default" (the backend's option[0]) — reading that
 * back correctly and, when the user never touches the slider, sending it back
 * unchanged so the machine brews the product's own default recipe.
 *
 * A just-changed value is held "pending" until the entity state catches up, so
 * the slider never snaps back to the old value during the service round-trip.
 */

// The backend prepends this sentinel as option[0] on every parameter select.
const FACTORY_DEFAULT = "Factory Default";

const PARAM_ROWS = [
  { key: "strength", label: "Strength", icon: "mdi:coffee" },
  { key: "water", label: "Water", icon: "mdi:cup-water", unit: " mL" },
  { key: "temperature", label: "Temperature", icon: "mdi:thermometer" },
  { key: "grinder_ratio", label: "Grinders (left:right)", icon: "mdi:chart-donut" },
  { key: "milk", label: "Milk", icon: "mdi:beer-outline", unit: " s" },
  { key: "milk_foam", label: "Milk Foam", icon: "mdi:chart-bubble", unit: " s" },
];

// Map a raw status string to a coffee-machine "mood": drives the banner colour
// and icon. Ordering matters — attention beats busy beats ready.
const ATTENTION = /alert|error|fill_|empty_|insert_|remove_|missing|close_|no_beans|not_enough|please_wait|program_mode/;
const BUSY = /heating|rinsing|filling|emptying|ventilation|press_rinse|welcome|goodbye/;
const READY = /ready|idle|enjoy|energy_safe/;

function statusMood(raw) {
  if (raw === "unavailable" || raw === "unknown" || raw == null) return "offline";
  if (ATTENTION.test(raw)) return "attention";
  if (BUSY.test(raw)) return "busy";
  if (READY.test(raw)) return "ready";
  return "busy";
}

const MOOD_ICON = {
  offline: "mdi:cloud-off-outline",
  attention: "mdi:alert-circle",
  busy: "mdi:progress-clock",
  ready: "mdi:check-circle",
};

function prettify(raw) {
  return `${raw}`.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

class JuraBrewCard extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._built = false;
    // entityId -> option the user just picked, held until the state confirms.
    this._pending = {};
    // entityId -> true while the user is actively dragging that slider; blocks
    // re-renders from snapping it back to the (stale) entity state.
    this._dragging = {};
  }

  getCardSize() {
    return 5;
  }

  static getStubConfig() {
    return { title: "Coffee" };
  }

  // Fill in any entity the user did not configure by sniffing the states.
  _resolveEntities(hass) {
    const cfg = this._config;
    const allIds = Object.keys(hass.states);
    const toksOf = (id, domain, suffix) =>
      new Set(id.slice(domain.length + 1, -suffix.length).split("_"));

    // Candidate entities for one role. The entity registry reassigns
    // entity_ids on rename/migration, so the parts of one machine's entity
    // set can sit under different slug prefixes (product under
    // select.kuche_<name>_* while connectivity kept binary_sensor.<name>_
    // connectivity). Matching on slug TOKENS instead of the exact prefix
    // keeps a machine's entities together across such drift.
    const search = (domain, suffix, slug) => {
      const cands = allIds.filter(
        (id) => id.startsWith(`${domain}.`) && id.endsWith(suffix),
      );
      if (!slug) return cands;
      const toks = new Set(slug.split("_"));
      return cands.filter((id) => [...toksOf(id, domain, suffix)].some((t) => toks.has(t)));
    };

    // Exact `domain.<slug><suffix>` wins; otherwise the candidate whose id
    // shares the most tokens with the machine slug. No token overlap -> no
    // pick, so a pinned `machine:` never silently drives another machine.
    const pick = (domain, suffix, explicit, slug) => {
      if (explicit) return explicit;
      if (!slug) return undefined;
      const exact = `${domain}.${slug}${suffix}`;
      if (hass.states[exact]) return exact;
      const cands = search(domain, suffix, slug);
      if (!cands.length) return undefined;
      const toks = new Set(slug.split("_"));
      return cands.sort(
        (a, b) => [...toksOf(b, domain, suffix)].filter((t) => toks.has(t)).length
          - [...toksOf(a, domain, suffix)].filter((t) => toks.has(t)).length,
      )[0];
    };

    let product = pick("select", "_brew_product", cfg.product, cfg.machine || null);
    if (!product) {
      // Zero-config auto-pick: deterministic (sorted) first candidate; warn
      // once so the user knows to pin one with `machine:`.
      const candidates = allIds
        .filter((id) => id.startsWith("select.") && id.endsWith("_brew_product"))
        .sort();
      product = candidates[0];
      if (candidates.length > 1 && !this._warnedMulti) {
        this._warnedMulti = true;
        // eslint-disable-next-line no-console
        console.warn(
          `jura-brew-card: ${candidates.length} JURA machines found (${candidates.join(", ")}); ` +
            `showing "${product}". Pin one with "machine: <slug>" or "product: <entity_id>".`,
        );
      }
    }
    // Without an explicit machine, derive one from the product id so status/
    // connectivity can still be token-matched to the same machine.
    const derived = product ? product.slice("select.".length, -"_brew_product".length) : null;

    // Connectivity/status fallback for the unconfigured single-machine case:
    // a lone *_<suffix> entity cannot be ambiguous.
    const lone = (domain, suffix) => {
      const cands = search(domain, suffix, null);
      return cands.length === 1 ? cands[0] : undefined;
    };

    return {
      product,
      strength: pick("select", "_brew_strength", cfg.strength, cfg.machine || derived),
      water: pick("select", "_brew_water", cfg.water, cfg.machine || derived),
      temperature: pick("select", "_brew_temperature", cfg.temperature, cfg.machine || derived),
      grinder_ratio: pick("select", "_brew_grinder_ratio", cfg.grinder_ratio, cfg.machine || derived),
      milk: pick("select", "_brew_milk", cfg.milk, cfg.machine || derived),
      milk_foam: pick("select", "_brew_milk_foam", cfg.milk_foam, cfg.machine || derived),
      button: pick("button", "_brew", cfg.button, cfg.machine || derived),
      status:
        pick("sensor", "_status", cfg.status, cfg.machine || derived) ||
        (cfg.machine ? undefined : lone("sensor", "_status")),
      connectivity:
        pick("binary_sensor", "_connectivity", cfg.connectivity, cfg.machine || derived) ||
        (cfg.machine ? undefined : lone("binary_sensor", "_connectivity")),
    };
  }

  _build() {
    const root = this.attachShadow ? this.shadowRoot || this.attachShadow({ mode: "open" }) : this;
    root.innerHTML = `
      <style>
        ha-card { padding: 16px; display: flex; flex-direction: column; gap: 16px; }

        .header { display: flex; align-items: center; gap: 12px; }
        .header ha-icon.brand { color: var(--primary-color); --mdc-icon-size: 30px; flex: 0 0 auto; }
        .header .title {
          font-size: 1.3rem;
          font-weight: 600;
          color: var(--primary-text-color);
          flex: 1 1 auto;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .pill {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 6px 12px;
          border-radius: 999px;
          font-size: 0.85rem;
          font-weight: 600;
          color: #fff;
          flex: 0 0 auto;
          --st: var(--disabled-text-color, #9e9e9e);
          background: var(--st);
        }
        .pill ha-icon { --mdc-icon-size: 18px; }
        .pill.ready { --st: var(--success-color, #43a047); }
        .pill.busy { --st: var(--warning-color, #ff9800); }
        .pill.attention { --st: var(--error-color, #f44336); }
        .pill.offline { --st: var(--disabled-text-color, #9e9e9e); }
        .pill.busy ha-icon { animation: spin 2s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }

        .offline-note {
          display: none;
          align-items: center;
          gap: 8px;
          padding: 10px 12px;
          border-radius: 10px;
          font-size: 0.9rem;
          color: var(--error-color, #f44336);
          background: rgba(244, 67, 54, 0.1);
        }
        .offline-note ha-icon { --mdc-icon-size: 20px; }
        ha-card.is-offline .offline-note { display: flex; }

        .product-row { display: flex; align-items: center; gap: 10px; }
        .product-row ha-icon { color: var(--primary-color); --mdc-icon-size: 24px; flex: 0 0 auto; }
        .product-row label { flex: 0 0 auto; color: var(--secondary-text-color); font-size: 0.9rem; }
        select {
          flex: 1 1 auto;
          padding: 10px 12px;
          border-radius: 12px;
          border: 1px solid var(--divider-color);
          background: var(--secondary-background-color);
          color: var(--primary-text-color);
          font-size: 1rem;
          font-weight: 500;
        }
        select:disabled { opacity: 0.5; }

        .sliders { display: flex; flex-direction: column; gap: 18px; }
        .slider-row { display: flex; flex-direction: column; gap: 8px; }
        .slider-head { display: flex; align-items: center; gap: 8px; }
        .slider-head ha-icon { color: var(--primary-color); --mdc-icon-size: 20px; }
        .slider-head .name { color: var(--primary-text-color); font-size: 0.95rem; font-weight: 500; }
        .slider-head .val {
          margin-left: auto;
          padding: 3px 12px;
          border-radius: 999px;
          font-size: 0.9rem;
          font-weight: 600;
          color: var(--text-primary-color, #fff);
          background: var(--primary-color);
          /* Transparent border keeps the box height identical to the dashed
             "Factory Default" variant so the pill doesn't wobble on change. */
          border: 1px solid transparent;
          white-space: nowrap;
        }
        .slider-row.is-default .slider-head .val {
          background: var(--secondary-background-color);
          color: var(--secondary-text-color);
          border: 1px dashed var(--divider-color);
        }
        .slider-row.is-default .slider-head ha-icon { opacity: 0.5; }

        input[type="range"] {
          -webkit-appearance: none;
          appearance: none;
          width: 100%;
          height: 10px;
          border-radius: 999px;
          background: var(--divider-color);
          outline: none;
          cursor: pointer;
          margin: 2px 0;
        }
        input[type="range"]:disabled { opacity: 0.4; cursor: not-allowed; }
        input[type="range"]::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 22px; height: 22px;
          border-radius: 50%;
          background: var(--primary-color);
          border: 3px solid var(--card-background-color, #fff);
          box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
        }
        input[type="range"]::-moz-range-thumb {
          width: 22px; height: 22px;
          border-radius: 50%;
          background: var(--primary-color);
          border: 3px solid var(--card-background-color, #fff);
          box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
        }
        .slider-row.is-default input[type="range"]::-webkit-slider-thumb { background: var(--secondary-text-color); }
        .slider-row.is-default input[type="range"]::-moz-range-thumb { background: var(--secondary-text-color); }

        button.brew {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 10px;
          padding: 15px;
          border: none;
          border-radius: 14px;
          background: var(--primary-color);
          color: var(--text-primary-color, #fff);
          font-size: 1.1rem;
          font-weight: 700;
          cursor: pointer;
        }
        button.brew ha-icon { --mdc-icon-size: 24px; }
        button.brew:disabled { opacity: 0.45; cursor: not-allowed; }
        button.brew:not(:disabled):active { transform: translateY(1px); filter: brightness(0.94); }

        .missing { color: var(--error-color); font-size: 0.9rem; }
      </style>
      <ha-card>
        <div class="header">
          <ha-icon class="brand" icon="mdi:coffee-maker"></ha-icon>
          <span class="title"></span>
          <span class="pill"><ha-icon></ha-icon><span class="pill-text"></span></span>
        </div>
        <div class="offline-note">
          <ha-icon icon="mdi:cloud-off-outline"></ha-icon>
          <span>Machine offline — brewing unavailable until it reconnects.</span>
        </div>
        <div class="product-row" hidden>
          <ha-icon icon="mdi:coffee-outline"></ha-icon>
          <label>Product</label>
          <select class="product"></select>
        </div>
        <div class="sliders"></div>
        <button class="brew" type="button"><ha-icon icon="mdi:coffee"></ha-icon><span>Brew</span></button>
        <div class="missing" hidden>No Jura brew entities found. Configure them in the card.</div>
      </ha-card>
    `;
    this._els = {
      card: root.querySelector("ha-card"),
      title: root.querySelector(".title"),
      pill: root.querySelector(".pill"),
      pillIcon: root.querySelector(".pill ha-icon"),
      pillText: root.querySelector(".pill-text"),
      productRow: root.querySelector(".product-row"),
      product: root.querySelector("select.product"),
      sliders: root.querySelector(".sliders"),
      brew: root.querySelector("button.brew"),
      missing: root.querySelector(".missing"),
    };
    this._els.brew.addEventListener("click", () => this._onBrew());
    this._els.product.addEventListener("change", (e) => {
      if (this._ids && this._ids.product) this._onSelect(this._ids.product, e.target.value);
    });
    this._rowEls = {};
    this._built = true;
  }

  _onBrew() {
    const ids = this._ids;
    if (!ids || !ids.button) return;
    this._hass.callService("button", "press", { entity_id: ids.button });
  }

  _onSelect(entityId, value) {
    // Remember the pick so re-renders don't snap the control back to the old
    // value while the select service round-trips.
    this._pending[entityId] = value;
    this._hass.callService("select", "select_option", { entity_id: entityId, option: value });
  }

  // Paint the range track: filled portion up to the thumb.
  _paintSlider(input, isDefault) {
    const max = Number(input.max) || 1;
    const pct = max ? (Number(input.value) / max) * 100 : 0;
    const fill = isDefault ? "var(--divider-color)" : "var(--primary-color)";
    input.style.background = `linear-gradient(90deg, ${fill} 0%, ${fill} ${pct}%, var(--divider-color) ${pct}%)`;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    if (!this._built) this._build();
    const ids = this._resolveEntities(hass);
    this._ids = ids;

    this._els.title.textContent = this._config.title || "Coffee";

    const anyFound = ids.product || ids.button;
    this._els.missing.hidden = !!anyFound;

    // --- Online / status banner --------------------------------------------
    const conn = ids.connectivity ? hass.states[ids.connectivity] : undefined;
    const statusState = ids.status ? hass.states[ids.status] : undefined;
    const btnState = ids.button ? hass.states[ids.button] : undefined;

    // Offline if the connectivity sensor says so, or (no sensor) the brew
    // button went unavailable.
    const offline =
      (conn && conn.state === "off") ||
      (!conn && (!btnState || btnState.state === "unavailable"));

    let mood;
    let text;
    if (offline) {
      mood = "offline";
      text = "Offline";
    } else if (statusState) {
      mood = statusMood(statusState.state);
      text = prettify(statusState.state);
    } else {
      mood = "ready";
      text = "Online";
    }
    this._els.pill.className = `pill ${mood}`;
    this._els.pillIcon.setAttribute("icon", MOOD_ICON[mood]);
    this._els.pillText.textContent = text;
    this._els.card.classList.toggle("is-offline", offline);

    // --- Product picker ----------------------------------------------------
    const prodState = ids.product ? hass.states[ids.product] : undefined;
    if (prodState) {
      this._els.productRow.hidden = false;
      const options = prodState.attributes.options || [];
      const wanted = options.join("");
      if (this._els.product.dataset.opts !== wanted) {
        this._els.product.innerHTML = options.map((o) => `<option value="${o}">${o}</option>`).join("");
        this._els.product.dataset.opts = wanted;
      }
      this._els.product.disabled = prodState.state === "unavailable";
      const shown = this._syncValue(ids.product, prodState.state);
      if (shown != null && shown !== this._els.product.value) this._els.product.value = shown;
    } else {
      this._els.productRow.hidden = true;
    }

    // --- Strength / Water / Temperature sliders ----------------------------
    for (const row of PARAM_ROWS) {
      const entityId = ids[row.key];
      const st = entityId ? hass.states[entityId] : undefined;
      let rowEl = this._rowEls[row.key];

      // Drop the row when the param is absent/unavailable for this product.
      if (!st || st.state === "unavailable" || !(st.attributes.options || []).length) {
        if (rowEl) {
          rowEl.remove();
          delete this._rowEls[row.key];
        }
        continue;
      }

      const options = st.attributes.options;
      if (!rowEl) {
        rowEl = document.createElement("div");
        rowEl.className = "slider-row";
        rowEl.innerHTML =
          `<div class="slider-head">` +
          `<ha-icon icon="${row.icon}"></ha-icon>` +
          `<span class="name">${row.label}</span>` +
          `<span class="val"></span></div>` +
          `<input type="range" min="0" step="1">`;
        const input = rowEl.querySelector("input");
        const valEl = rowEl.querySelector(".val");
        // Freeze the slider against background re-renders for the whole grab.
        input.addEventListener("pointerdown", () => { this._dragging[entityId] = true; });
        // Live feedback while dragging; commit only on release.
        input.addEventListener("input", () => {
          this._dragging[entityId] = true;
          const opts = JSON.parse(input.dataset.options || "[]");
          const idx = Number(input.value);
          const isDef = idx === 0;
          rowEl.classList.toggle("is-default", isDef);
          valEl.textContent = isDef ? FACTORY_DEFAULT : this._formatValue(row, opts[idx]);
          this._paintSlider(input, isDef);
        });
        // Release: commit the value (which sets a pending hold) and end the
        // drag. `change` fires after `pointerup` in the same dispatch, so no
        // background re-render can sneak in between them.
        input.addEventListener("change", () => {
          delete this._dragging[entityId];
          const opts = JSON.parse(input.dataset.options || "[]");
          this._onSelect(entityId, opts[Number(input.value)]);
        });
        // Safety net: a grab that ends without a value change (no move) must
        // still clear the flag so re-renders resume.
        input.addEventListener("pointerup", () => { delete this._dragging[entityId]; });
        input.addEventListener("blur", () => { delete this._dragging[entityId]; });
        this._els.sliders.appendChild(rowEl);
        this._rowEls[row.key] = rowEl;
      }

      const input = rowEl.querySelector("input");
      const valEl = rowEl.querySelector(".val");
      input.dataset.options = JSON.stringify(options);

      // Never move the thumb out from under an active drag.
      if (this._dragging[entityId]) continue;

      input.max = String(options.length - 1);
      input.disabled = offline;

      const shown = this._syncValue(entityId, st.state);
      let idx = options.indexOf(shown);
      if (idx < 0) idx = 0; // fall back to Factory Default if unknown
      input.value = String(idx);
      const isDef = idx === 0;
      rowEl.classList.toggle("is-default", isDef);
      valEl.textContent = isDef ? FACTORY_DEFAULT : this._formatValue(row, options[idx]);
      this._paintSlider(input, isDef);
    }

    // --- Brew button -------------------------------------------------------
    this._els.brew.disabled = offline || !btnState || btnState.state === "unavailable";
  }

  // Resolve the value to display for an entity, honouring an in-flight pick.
  // Returns the pending option until the real state matches it, then clears it.
  _syncValue(entityId, stateValue) {
    const pending = this._pending[entityId];
    if (pending === undefined) return stateValue;
    if (stateValue === pending) {
      delete this._pending[entityId];
      return stateValue;
    }
    return pending;
  }

  _formatValue(row, value) {
    if (row.key === "grinder_ratio") return `${value}`.replace("_", ":");
    return `${value}${row.unit || ""}`;
  }
}

if (!customElements.get("jura-brew-card")) {
  customElements.define("jura-brew-card", JuraBrewCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "jura-brew-card",
    name: "Jura Brew Card",
    description: "Brew a coffee from your JURA machine: product + profile-backed recipe controls + Brew button.",
    preview: false,
  });
  // eslint-disable-next-line no-console
  console.info("%c JURA-BREW-CARD ", "background:#6f4e37;color:#fff;border-radius:3px", "loaded");
}
