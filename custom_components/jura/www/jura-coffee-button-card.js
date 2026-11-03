/*
 * jura-coffee-button-card
 * A compact (2x2) "favourite drink" button for the makefu/jura-connect-hass
 * integration: one product with your personal parameters and a Brew button.
 *
 * Ships with the integration and is loaded automatically -- pick
 * "Jura Coffee Button" in the dashboard card picker and configure it in the
 * visual editor. YAML mirrors the `jura.brew` service fields:
 *
 *   type: custom:jura-coffee-button-card
 *   device_id: 0123abcd...      # optional, first machine when omitted
 *   product: espresso           # product name from the machine profile
 *   name: Morning espresso      # optional, defaults to the product name
 *   icon: mdi:coffee            # optional
 *   strength: 8                 # optional -- omitted = product default
 *   water_ml: 45
 *   temperature: 2              # 0 low, 1 normal, 2 high (profile items)
 *   grinder_ratio: ...          # twin-grinder profiles only
 *   milk_s: 12
 *   milk_foam_s: 8
 *   confirm: false              # true = tap twice to brew
 *
 * Product table, parameter ranges and the status / connectivity / progress
 * entities come from the integration's `jura/machines` websocket command, so
 * the editor only offers what the chosen product supports.
 */

// Own scope: the legacy frontend build (older WebViews such as the Shelly
// Wall Display) loads cards as classic scripts that share one global scope,
// where both Jura cards' top-level names (ATTENTION, statusMood, …) collide.
(function () {
  const CARD_TYPE = "jura-coffee-button-card";
  const EDITOR_TYPE = "jura-coffee-button-card-editor";

  // Order = display order in the summary line and the editor.
  const PARAM_FIELDS = ["strength", "water_ml", "temperature", "grinder_ratio", "milk_s", "milk_foam_s"];
  const PARAM_UNITS = { water_ml: "ml", milk_s: "s", milk_foam_s: "s" };
  // Summary entries that need their name to be readable.
  const PREFIXED = new Set(["strength", "milk_s", "milk_foam_s"]);
  // Translation keys of the matching select entities (state names live there).
  const PARAM_TRANSLATION = {
    temperature: "brew_temperature",
    grinder_ratio: "brew_grinder_ratio",
  };

  const STRINGS = {
    en: {
      brew: "Brew",
      brewing: "Brewing…",
      confirm: "Tap again",
      done: "Enjoy!",
      error: "Failed",
      offline: "Offline",
      loading: "Loading…",
      no_machine: "No JURA machine found",
      no_product: "Product not available",
      choose_product: "Choose a drink in the card editor",
      unreachable: "Machine not reachable",
      setup_retry: "Machine not reachable, retrying…",
      not_loaded: "Integration not loaded",
      default_recipe: "Factory recipe",
      strength: "Strength",
      water_ml: "Water",
      temperature: "Temperature",
      grinder_ratio: "Grinders",
      milk_s: "Milk",
      milk_foam_s: "Foam",
      // editor
      machine: "Machine",
      product: "Drink",
      name: "Name",
      icon: "Icon",
      confirm_label: "Tap twice to brew",
      params: "Personal parameters",
      factory_default: "Factory default",
      default_hint: "Empty = factory default",
    },
    de: {
      brew: "Brühen",
      brewing: "Brüht…",
      confirm: "Nochmal tippen",
      done: "Genießen!",
      error: "Fehler",
      offline: "Offline",
      loading: "Lädt…",
      no_machine: "Keine JURA-Maschine gefunden",
      no_product: "Getränk nicht verfügbar",
      choose_product: "Getränk im Karteneditor wählen",
      unreachable: "Maschine nicht erreichbar",
      setup_retry: "Maschine nicht erreichbar, neuer Versuch läuft…",
      not_loaded: "Integration nicht geladen",
      default_recipe: "Standardrezept",
      strength: "Stärke",
      water_ml: "Wasser",
      temperature: "Temperatur",
      grinder_ratio: "Mahlwerke",
      milk_s: "Milch",
      milk_foam_s: "Schaum",
      machine: "Maschine",
      product: "Getränk",
      name: "Name",
      icon: "Symbol",
      confirm_label: "Zum Brühen zweimal tippen",
      params: "Persönliche Parameter",
      factory_default: "Standard",
      default_hint: "Leer = Werkseinstellung",
    },
  };

  function strings(hass) {
    const lang = (hass && ((hass.locale && hass.locale.language) || hass.language)) || "en";
    return STRINGS[lang.split("-")[0]] || STRINGS.en;
  }

  function prettify(raw) {
    return `${raw}`.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  // Status sensor value -> mood (same buckets as jura-brew-card).
  const ATTENTION = /alert|error|fill_|empty_|insert_|remove_|missing|close_|no_beans|not_enough|please_wait|program_mode/;
  const BUSY = /heating|rinsing|filling|emptying|ventilation|press_rinse|welcome|goodbye/;
  function statusMood(raw) {
    if (raw == null || raw === "unavailable" || raw === "unknown") return "offline";
    if (ATTENTION.test(raw)) return "attention";
    if (BUSY.test(raw)) return "busy";
    return "ready";
  }

  function productIcon(product) {
    const n = (product && product.name) || "";
    if (/hotwater|tea/.test(n)) return "mdi:tea";
    if (/milk_foam|milk_portion/.test(n)) return "mdi:cup";
    if (/latte|cappu|milk|flat_white|cortado|macchiato/.test(n)) return "mdi:coffee-outline";
    if (/pot/.test(n)) return "mdi:coffee-maker";
    return "mdi:coffee";
  }

  // --- shared machine table ---------------------------------------------------
  // One websocket round-trip per page load, shared by every card + editor.
  let machinesPromise = null;
  function fetchMachines(hass, force = false) {
    if (!machinesPromise || force) {
      machinesPromise = hass.callWS({ type: "jura/machines" }).then(
        (res) => (res && res.machines) || [],
        (err) => {
          machinesPromise = null; // retry on the next render
          throw err;
        },
      );
    }
    return machinesPromise;
  }

  function resolveMachine(machines, config) {
    if (!machines || !machines.length) return undefined;
    if (config.device_id) return machines.find((m) => m.device_id === config.device_id);
    if (config.entry_id) return machines.find((m) => m.entry_id === config.entry_id);
    return machines[0];
  }

  // No `product` -> no product: the card must never guess a drink (it used to
  // fall back to the first profile product, so every unconfigured tile brewed
  // an espresso).
  function resolveProduct(machine, config) {
    if (!machine || !machine.products.length || !config.product) return undefined;
    const wanted = `${config.product}`.toLowerCase();
    return machine.products.find((p) => p.name.toLowerCase() === wanted || (p.label || "").toLowerCase() === wanted);
  }

  // `jura.brew` call data: product + only the parameters this product has.
  function buildServiceData(config, machine, product) {
    const data = { config_entry_id: machine.entry_id, product: product.name };
    for (const param of product.params) {
      const value = config[param.field];
      if (value !== undefined && value !== null && value !== "") data[param.field] = Number(value);
    }
    return data;
  }

  function productLabel(hass, product) {
    const key = `component.jura.entity.select.brew_product.state.${product.name}`;
    const translated = hass && hass.localize ? hass.localize(key) : "";
    return translated || product.label || prettify(product.name);
  }

  function itemLabel(hass, field, item) {
    const key = PARAM_TRANSLATION[field];
    const translated = key && hass && hass.localize
      ? hass.localize(`component.jura.entity.select.${key}.state.${item.name}`)
      : "";
    if (translated) return translated;
    if (field === "grinder_ratio") return item.name.replace("_", ":");
    return prettify(item.name);
  }

  // Human-readable value of one parameter (configured, else profile default).
  function paramText(hass, param, value) {
    if (param.items) {
      const item = param.items.find((it) => it.value === Number(value));
      return item ? itemLabel(hass, param.field, item) : `${value}`;
    }
    const unit = PARAM_UNITS[param.field];
    return unit ? `${value} ${unit}` : `${value}`;
  }

  // "8 · 45 ml · High" -- strength first, effective values (personal or default).
  function summaryText(hass, config, product) {
    const t = strings(hass);
    const parts = [];
    let personal = false;
    for (const field of PARAM_FIELDS) {
      const param = product.params.find((p) => p.field === field);
      if (!param) continue;
      const own = config[field] !== undefined && config[field] !== null && config[field] !== "";
      personal = personal || own;
      const value = own ? config[field] : param.default;
      if (value === undefined || value === null) continue;
      const text = paramText(hass, param, value);
      // Bare "12 s" is ambiguous (milk or foam?), so those keep their label.
      parts.push(PREFIXED.has(field) ? `${t[field]} ${text}` : text);
    }
    if (!personal && !parts.length) return t.default_recipe;
    return parts.join(" · ");
  }

  class JuraCoffeeButtonCard extends HTMLElement {
    static getConfigElement() {
      return document.createElement(EDITOR_TYPE);
    }

    // The card picker awaits this: start with an explicit drink so the saved
    // YAML always names the product that will be brewed.
    static async getStubConfig(hass) {
      try {
        const machine = resolveMachine(await fetchMachines(hass), {});
        if (machine && machine.products.length) return { product: machine.products[0].name };
      } catch (_err) {
        // Integration not loaded yet: the editor fills the product in later.
      }
      return {};
    }

    setConfig(config) {
      if (!config) throw new Error("Invalid configuration");
      this._config = { ...config };
      this._phase = "idle"; // idle | confirm | brewing | done | error
      if (this._els) this._render();
    }

    getCardSize() {
      return 2;
    }

    // Sections view: 2x2 in the old 4-column grid, half a section in the new
    // 12-column grid -- the same footprint as a default button card.
    getLayoutOptions() {
      return { grid_columns: 2, grid_rows: 2, grid_min_columns: 2, grid_min_rows: 2, grid_max_rows: 2 };
    }

    getGridOptions() {
      return { columns: 6, rows: 2, min_columns: 4, min_rows: 2, max_rows: 2 };
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      if (!this._config) return;
      if (!this._els) this._build();
      if (first) this._load();
      this._render();
    }

    _load() {
      this._loading = true;
      fetchMachines(this._hass)
        .then((machines) => {
          this._machines = machines;
          this._loadError = undefined;
          // Machine still starting up (setup retry): look again shortly, so
          // the tile turns live without a page reload once it is reachable.
          const machine = resolveMachine(machines, this._config || {});
          if (machine && machine.state && machine.state !== "loaded") {
            clearTimeout(this._retryTimer);
            this._retryTimer = setTimeout(() => {
              fetchMachines(this._hass, true).catch(() => {});
              this._load();
            }, 30000);
          }
        })
        .catch((err) => {
          this._loadError = (err && err.message) || `${err}`;
          // Integration still starting (or reloading): try again shortly.
          clearTimeout(this._retryTimer);
          this._retryTimer = setTimeout(() => this._load(), 30000);
        })
        .finally(() => {
          this._loading = false;
          this._render();
        });
    }

    _build() {
      const root = this.shadowRoot || this.attachShadow({ mode: "open" });
      root.innerHTML = `
        <style>
          /* Compatible with older WebViews (Shelly Wall Display): every
             color-mix() has a plain fallback declared first, spacing uses
             margins instead of flex gap, no inset shorthand. */
          :host { display: block; height: 100%; }
          ha-card {
            --jura-accent: var(--jura-accent-color, #8b5a3c);
            height: 100%;
            box-sizing: border-box;
            padding: 12px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            overflow: hidden;
            background: var(--ha-card-background, var(--card-background-color, #fff));
            background:
              radial-gradient(circle at 0% 0%, color-mix(in srgb, var(--jura-accent) 18%, transparent), transparent 70%),
              var(--ha-card-background, var(--card-background-color, #fff));
          }
          .head { display: flex; align-items: center; min-width: 0; margin-bottom: 10px; cursor: pointer; }
          .cup {
            position: relative;
            flex: 0 0 auto;
            width: 40px; height: 40px;
            border-radius: 50%;
            display: grid; place-items: center;
            color: #fff;
            background: var(--jura-accent);
            background: linear-gradient(145deg, color-mix(in srgb, var(--jura-accent) 80%, #fff), var(--jura-accent));
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
            box-shadow: 0 2px 6px color-mix(in srgb, var(--jura-accent) 40%, transparent);
          }
          .cup ha-icon { --mdc-icon-size: 22px; }
          .dot {
            position: absolute; right: -1px; bottom: -1px;
            width: 11px; height: 11px; border-radius: 50%;
            border: 2px solid var(--ha-card-background, var(--card-background-color, #fff));
            background: var(--success-color, #43a047);
          }
          .dot.busy { background: var(--warning-color, #ff9800); }
          .dot.attention { background: var(--error-color, #f44336); }
          .dot.offline { background: var(--disabled-text-color, #9e9e9e); }
          /* Steam while brewing. */
          .steam { position: absolute; top: -9px; left: 50%; display: none; transform: translateX(-50%); }
          .steam i {
            width: 2px; height: 8px; margin: 0 1.5px; border-radius: 2px;
            opacity: 0.6;
            background: var(--jura-accent);
            background: color-mix(in srgb, var(--jura-accent) 60%, transparent);
            animation: steam 1.4s ease-in-out infinite;
          }
          .steam i:nth-child(2) { animation-delay: 0.25s; }
          .steam i:nth-child(3) { animation-delay: 0.5s; }
          @keyframes steam {
            0% { opacity: 0; transform: translateY(4px) scaleY(0.6); }
            50% { opacity: 1; }
            100% { opacity: 0; transform: translateY(-6px) scaleY(1.1); }
          }
          ha-card.brewing .steam { display: flex; }
          .text { min-width: 0; flex: 1 1 auto; margin-left: 10px; }
          .name, .sub { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .name { font-size: 1rem; font-weight: 600; line-height: 1.3; color: var(--primary-text-color); }
          .sub { font-size: 0.8rem; line-height: 1.3; color: var(--secondary-text-color); }
          .sub.attention { color: var(--error-color, #f44336); }
          button {
            position: relative;
            overflow: hidden;
            width: 100%;
            min-height: 38px;
            padding: 0 12px;
            border: none;
            border-radius: 12px;
            display: flex; align-items: center; justify-content: center;
            font: inherit; font-size: 0.95rem; font-weight: 600;
            color: #fff;
            background: var(--jura-accent);
            cursor: pointer;
            transition: filter 0.15s, transform 0.1s, background 0.2s;
          }
          button ha-icon { --mdc-icon-size: 20px; margin-right: 8px; }
          button:hover:not(:disabled) { filter: brightness(1.08); }
          button:active:not(:disabled) { transform: scale(0.98); }
          button:disabled { cursor: default; opacity: 0.45; }
          button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
          button .fill {
            position: absolute; top: 0; bottom: 0; left: 0; width: 0;
            background: rgba(255, 255, 255, 0.22);
            transition: width 0.6s ease;
          }
          button span, button ha-icon { position: relative; }
          ha-card.confirm button { background: var(--warning-color, #ff9800); }
          ha-card.error button { background: var(--error-color, #f44336); }
          ha-card.done button { background: var(--success-color, #43a047); }
          ha-card.brewing button { cursor: progress; }
          ha-card.brewing button:disabled { opacity: 1; }
        </style>
        <ha-card>
          <div class="head" role="button" tabindex="0">
            <div class="cup">
              <div class="steam"><i></i><i></i><i></i></div>
              <ha-icon></ha-icon>
              <span class="dot"></span>
            </div>
            <div class="text">
              <div class="name"></div>
              <div class="sub"></div>
            </div>
          </div>
          <button type="button"><span class="fill"></span><ha-icon icon="mdi:coffee"></ha-icon><span class="label"></span></button>
        </ha-card>
      `;
      this._els = {
        card: root.querySelector("ha-card"),
        head: root.querySelector(".head"),
        icon: root.querySelector(".cup ha-icon"),
        dot: root.querySelector(".dot"),
        name: root.querySelector(".name"),
        sub: root.querySelector(".sub"),
        button: root.querySelector("button"),
        btnIcon: root.querySelector("button ha-icon"),
        fill: root.querySelector(".fill"),
        label: root.querySelector(".label"),
      };
      this._els.button.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._onPress();
      });
      const moreInfo = () => this._moreInfo();
      this._els.head.addEventListener("click", moreInfo);
      this._els.head.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") moreInfo();
      });
    }

    _context() {
      const machine = resolveMachine(this._machines, this._config);
      const product = resolveProduct(machine, this._config);
      return { machine, product };
    }

    _moreInfo() {
      const { machine } = this._context();
      const entityId = machine && (machine.entities.status || machine.entities.connectivity);
      if (!entityId) return;
      this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
    }

    _isOffline(machine) {
      if (machine.state && machine.state !== "loaded") return true;
      const hass = this._hass;
      const conn = machine.entities.connectivity && hass.states[machine.entities.connectivity];
      if (conn) return conn.state !== "on";
      const status = machine.entities.status && hass.states[machine.entities.status];
      return !status || status.state === "unavailable";
    }

    _setPhase(phase, ms) {
      clearTimeout(this._phaseTimer);
      this._phase = phase;
      if (ms) this._phaseTimer = setTimeout(() => this._setPhase("idle"), ms);
      this._render();
    }

    async _onPress() {
      const { machine, product } = this._context();
      if (!machine || !product || this._phase === "brewing" || this._isOffline(machine)) return;
      if (this._config.confirm && this._phase !== "confirm") {
        this._setPhase("confirm", 3000);
        return;
      }
      this._error = undefined;
      this._setPhase("brewing");
      try {
        // Resolves once the integration stopped following the brew (ENJOY).
        await this._hass.callService("jura", "brew", buildServiceData(this._config, machine, product));
        this._setPhase("done", 2500);
      } catch (err) {
        // The integration raises translation_key "machine_unreachable" and
        // marks the machine offline at once; show it in the user's language.
        const key = err && (err.translation_key || (err.error && err.error.translation_key));
        this._error = key === "machine_unreachable"
          ? strings(this._hass).unreachable
          : (err && err.message) || `${err}`;
        this._setPhase("error", 4000);
      }
    }

    // A render error must stay inside the tile: escaping into Home
    // Assistant it replaces the whole card with an opaque error card.
    _render() {
      try {
        this._renderUnsafe();
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("jura-coffee-button-card render failed", err);
        if (!this._els) return;
        this._els.sub.textContent = (err && err.message) || String(err);
        this._els.sub.classList.add("attention");
        this._els.button.disabled = true;
      }
    }

    _renderUnsafe() {
      const els = this._els;
      const hass = this._hass;
      if (!els || !hass || !this._config) return;
      const t = strings(hass);
      const { machine, product } = this._context();
      const phase = this._phase;

      els.card.className = phase === "idle" ? "" : phase;
      els.fill.style.width = "0";
      els.btnIcon.setAttribute("icon", "mdi:coffee");
      els.sub.classList.remove("attention");
      els.button.title = "";

      if (!machine || !product) {
        els.icon.setAttribute("icon", this._config.icon || "mdi:coffee");
        els.dot.className = "dot offline";
        els.name.textContent = this._config.name || (this._config.product ? prettify(this._config.product) : "JURA");
        els.sub.textContent = this._loading
          ? t.loading
          : this._loadError || (!machine ? t.no_machine : this._config.product ? t.no_product : t.choose_product);
        els.label.textContent = t.brew;
        els.button.disabled = true;
        return;
      }

      els.icon.setAttribute("icon", this._config.icon || productIcon(product));
      els.name.textContent = this._config.name || productLabel(hass, product);

      const offline = this._isOffline(machine);
      const status = machine.entities.status && hass.states[machine.entities.status];
      const mood = offline ? "offline" : statusMood(status && status.state);
      els.dot.className = `dot ${mood}`;

      if (mood === "attention" && phase === "idle") {
        els.sub.textContent = hass.formatEntityState ? hass.formatEntityState(status) : prettify(status.state);
        els.sub.classList.add("attention");
      } else if (phase === "error" && this._error) {
        els.sub.textContent = this._error;
        els.sub.classList.add("attention");
      } else if (machine.state && machine.state !== "loaded") {
        els.sub.textContent = machine.state === "setup_retry" ? t.setup_retry : t.not_loaded;
        els.sub.classList.add("attention");
      } else {
        els.sub.textContent = summaryText(hass, this._config, product);
      }

      let label = t.brew;
      if (offline && phase !== "brewing") {
        label = t.offline;
        els.btnIcon.setAttribute("icon", "mdi:cloud-off-outline");
      } else if (phase === "confirm") {
        label = t.confirm;
        els.btnIcon.setAttribute("icon", "mdi:gesture-tap");
      } else if (phase === "brewing") {
        label = t.brewing;
        els.btnIcon.setAttribute("icon", "mdi:coffee-maker-outline");
        const progress = machine.entities.progress && hass.states[machine.entities.progress];
        const pct = progress && Number(progress.attributes && progress.attributes.percent);
        if (Number.isFinite(pct) && pct > 0) {
          els.fill.style.width = `${Math.min(100, pct)}%`;
          label = `${t.brewing} ${Math.round(pct)} %`;
        }
      } else if (phase === "done") {
        label = t.done;
        els.btnIcon.setAttribute("icon", "mdi:check");
      } else if (phase === "error") {
        label = t.error;
        els.btnIcon.setAttribute("icon", "mdi:alert-circle-outline");
        els.button.title = this._error || "";
      }
      els.label.textContent = label;
      els.button.disabled = (offline && phase !== "brewing") || phase === "brewing";
    }
  }

  // --- visual editor ------------------------------------------------------------

  function editorSchema(hass, machines, machine, product) {
    const t = strings(hass);
    const schema = [];
    if (machines.length > 1) {
      schema.push({ name: "device_id", selector: { device: { integration: "jura" } } });
    }
    schema.push({
      name: "product",
      required: true,
      selector: {
        select: {
          mode: "dropdown",
          options: machine ? machine.products.map((p) => ({ value: p.name, label: productLabel(hass, p) })) : [],
        },
      },
    });
    schema.push({
      type: "grid",
      name: "",
      schema: [
        { name: "name", selector: { text: {} } },
        { name: "icon", selector: { icon: {} } },
      ],
    });
    if (product && product.params.length) {
      const params = [];
      for (const field of PARAM_FIELDS) {
        const param = product.params.find((p) => p.field === field);
        if (!param) continue;
        if (param.items) {
          params.push({
            name: field,
            selector: {
              select: {
                mode: "dropdown",
                options: [
                  { value: "", label: t.factory_default },
                  ...param.items.map((it) => ({ value: `${it.value}`, label: itemLabel(hass, field, it) })),
                ],
              },
            },
          });
        } else {
          params.push({
            name: field,
            selector: {
              number: {
                min: param.min,
                max: param.max,
                step: param.step,
                mode: "slider",
                unit_of_measurement: PARAM_UNITS[field],
              },
            },
          });
        }
      }
      schema.push({ type: "expandable", name: "", flatten: true, title: t.params, icon: "mdi:tune", expanded: true, schema: params });
    }
    schema.push({ name: "confirm", selector: { boolean: {} } });
    return schema;
  }

  // Editor form data: item params as strings (select values must be strings).
  function toFormData(config, product) {
    const data = { ...config };
    for (const param of (product && product.params) || []) {
      if (param.items) data[param.field] = config[param.field] == null ? "" : `${config[param.field]}`;
    }
    return data;
  }

  // Form data -> card config: numbers back, unset/foreign params removed.
  function fromFormData(data, machine) {
    const config = { ...data };
    const product = resolveProduct(machine, config);
    const fields = new Set(product ? product.params.map((p) => p.field) : []);
    for (const field of PARAM_FIELDS) {
      const value = config[field];
      if (!fields.has(field) || value === undefined || value === null || value === "") delete config[field];
      else config[field] = Number(value);
    }
    for (const key of ["name", "icon", "device_id"]) if (!config[key]) delete config[key];
    if (!config.confirm) delete config.confirm;
    return config;
  }

  // Home Assistant lazy-loads <ha-form>; a custom editor that creates it before
  // any built-in editor did gets an inert unknown element (an empty editor --
  // no drink picker). Loading a built-in card editor pulls ha-form in.
  let haFormPromise = null;
  function ensureHaForm() {
    if (customElements.get("ha-form")) return Promise.resolve();
    if (!haFormPromise) {
      haFormPromise = (async () => {
        const helpers = window.loadCardHelpers ? await window.loadCardHelpers() : undefined;
        if (helpers) {
          const card = await helpers.createCardElement({ type: "button", entity: "sun.sun" });
          if (card && card.constructor.getConfigElement) await card.constructor.getConfigElement();
        }
        await customElements.whenDefined("ha-form");
      })();
    }
    return haFormPromise;
  }

  class JuraCoffeeButtonCardEditor extends HTMLElement {
    setConfig(config) {
      this._config = { ...config };
      this._render();
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      if (first) {
        // Independent: the drink is written into the config even if ha-form
        // never loads (the YAML editor still works then).
        fetchMachines(hass, true)
          .then(
            (machines) => {
              this._machines = machines;
            },
            () => {
              this._machines = [];
            },
          )
          .then(() => {
            this._ensureProduct();
            this._render();
          });
        ensureHaForm()
          .then(() => this._render())
          .catch(() => {});
      }
      if (this._form) this._form.hass = hass;
    }

    // Make a card without a (known) drink concrete: write the machine's first
    // product into the config, so the dropdown, the preview and the saved
    // YAML all show the drink that will really be brewed.
    _ensureProduct() {
      const machine = resolveMachine(this._machines, this._config || {});
      if (machine && machine.products.length && !resolveProduct(machine, this._config)) {
        this._emit({ ...this._config, product: machine.products[0].name });
      }
    }

    _emit(config) {
      this._config = fromFormData(config, resolveMachine(this._machines, config));
      this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
      this._render();
    }

    _render() {
      if (!this._hass || !this._config || !this._machines || !customElements.get("ha-form")) return;
      const t = strings(this._hass);
      if (!this._form) {
        this._form = document.createElement("ha-form");
        this._form.computeLabel = (s) => t[s.name === "confirm" ? "confirm_label" : s.name] || s.title || s.name;
        this._form.computeHelper = (s) => (PARAM_FIELDS.includes(s.name) ? this._defaultHint(s.name) : undefined);
        this._form.addEventListener("value-changed", (ev) => this._onChange(ev.detail.value));
        this.appendChild(this._form);
      }
      const machine = resolveMachine(this._machines, this._config);
      const product = resolveProduct(machine, this._config);
      this._form.hass = this._hass;
      this._form.schema = editorSchema(this._hass, this._machines, machine, product);
      this._form.data = toFormData(this._config, product);
    }

    _defaultHint(field) {
      const t = strings(this._hass);
      const machine = resolveMachine(this._machines, this._config);
      const product = resolveProduct(machine, this._config);
      const param = product && product.params.find((p) => p.field === field);
      if (!param || param.default == null) return t.default_hint;
      return `${t.default_hint} (${paramText(this._hass, param, param.default)})`;
    }

    _onChange(value) {
      const next = { ...value };
      // A new machine may not have the chosen product; a new product may not
      // have the old parameters -- fromFormData drops what no longer applies.
      const machine = resolveMachine(this._machines, next);
      if (machine && !resolveProduct(machine, next)) next.product = machine.products[0] && machine.products[0].name;
      this._emit(next);
    }
  }

  if (!customElements.get(CARD_TYPE)) {
    customElements.define(CARD_TYPE, JuraCoffeeButtonCard);
    customElements.define(EDITOR_TYPE, JuraCoffeeButtonCardEditor);
    window.customCards = window.customCards || [];
    // One picker entry, however often the module is evaluated (auto-loaded
    // module plus a leftover manual dashboard resource or import()).
    if (!window.customCards.some((c) => c.type === CARD_TYPE)) window.customCards.push({
      type: CARD_TYPE,
      name: "Jura Coffee Button",
      description: "Your favourite JURA drink with personal parameters and a Brew button, in a compact 2x2 tile.",
      preview: true,
      documentationURL: "https://github.com/makefu/jura-connect-hass#lovelace-cards",
    });
    // eslint-disable-next-line no-console
    console.info("%c JURA-COFFEE-BUTTON-CARD ", "background:#8b5a3c;color:#fff;border-radius:3px", "loaded");
  }

  // Test hook (node --test); harmless in the browser.
  if (typeof module !== "undefined") {
    module.exports = {
      resolveMachine,
      resolveProduct,
      buildServiceData,
      summaryText,
      editorSchema,
      fromFormData,
      toFormData,
      ensureHaForm,
    };
  }
})();
