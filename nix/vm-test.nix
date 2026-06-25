{ pkgs, juraConnectPkg, ... }:
let
  version = (builtins.fromJSON (builtins.readFile ../custom_components/jura/manifest.json)).version;

  # Validates that the German translation HA serves is structurally
  # complete: every entity translation key declared in const.py has a
  # German name, placeholders survive translation, and the maintainer's
  # reviewed strings landed. Runs inside the booted VM against the files
  # HA actually loaded from /var/lib/hass.
  checkGerman = pkgs.writeText "check-german-translations.py" ''
    import importlib.util
    import json

    base = "/var/lib/hass/custom_components/jura"

    # Load const.py by path: importing the ``jura`` package would run its
    # __init__ (which pulls in Home Assistant) — const itself is dependency
    # free and is the source of truth for the entity key sets.
    spec = importlib.util.spec_from_file_location("jura_const", f"{base}/const.py")
    const = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(const)
    ALERT_BINARY_SENSORS = const.ALERT_BINARY_SENSORS
    COUNTER_KEYS = const.COUNTER_KEYS
    PERCENT_KEYS = const.PERCENT_KEYS


    def load(name):
        with open(f"{base}/{name}", encoding="utf-8") as f:
            return json.load(f)


    strings = load("strings.json")
    de = load("translations/de.json")
    en = load("translations/en.json")

    expected = {
        "sensor": {"status", "machine_type", "brew_total", "brew_counter"}
        | {f"counter_{k}" for k in COUNTER_KEYS}
        | {f"percent_{k}" for k in PERCENT_KEYS},
        "binary_sensor": {"connectivity", *ALERT_BINARY_SENSORS.keys()},
        "select": {"setting"},
        "number": {"setting"},
    }

    for catalog_name, catalog in (("strings.json", strings), ("en.json", en), ("de.json", de)):
        for platform, keys in expected.items():
            have = set(catalog["entity"][platform])
            missing = keys - have
            assert not missing, f"{catalog_name}: {platform} missing {sorted(missing)}"


    def name(catalog, platform, key):
        return catalog["entity"][platform][key]["name"]


    # German must actually be German for the maintainer-reviewed strings.
    assert name(de, "sensor", "machine_type") == "Maschinentyp"
    assert name(de, "sensor", "brew_total") == "Bezüge gesamt"
    assert name(de, "binary_sensor", "fill_water") == "Wasser nachfüllen"
    assert name(de, "binary_sensor", "press_rinse") == "Spültaste drücken"

    # Placeholders survive translation so HA can substitute machine data.
    assert "{product}" in name(de, "sensor", "brew_counter")
    assert "{setting}" in name(de, "select", "setting")
    assert "{setting}" in name(de, "number", "setting")

    # The bulk of alerts have a distinct German rendering (sanity that the
    # German file is not just a copy of English).
    translated = sum(
        1
        for k in en["entity"]["binary_sensor"]
        if name(en, "binary_sensor", k) != name(de, "binary_sensor", k)
    )
    assert translated >= 35, f"only {translated} alerts translated"

    print(f"OK: {translated} German alert names verified")
  '';
in
pkgs.testers.nixosTest {
  name = "jura-connect-ha-integration";

  nodes.machine =
    { pkgs, ... }:
    {
      environment.systemPackages = [ pkgs.python3 ];
      services.home-assistant = {
        enable = true;
        config = {
          homeassistant = {
            name = "Test";
            unit_system = "metric";
            # Drive the frontend in German so HA loads the de translations.
            language = "de";
          };
        };
        customComponents = [
          (pkgs.stdenvNoCC.mkDerivation {
            pname = "ha-jura-connect";
            inherit version;
            src = ../.;
            installPhase = ''
              mkdir -p $out/custom_components
              cp -r custom_components/jura $out/custom_components/
            '';
            passthru = {
              isHomeAssistantComponent = true;
              domain = "jura";
            };
          })
        ];
        extraPackages = _ps: [
          juraConnectPkg
        ];
      };
    };

  testScript = ''
    machine.wait_for_unit("home-assistant.service")
    machine.wait_for_open_port(8123)

    # Verify the custom_components directory has our component
    machine.succeed("test -f /var/lib/hass/custom_components/jura/manifest.json")

    # Check that HA discovered our custom integration (no import errors)
    machine.wait_until_succeeds(
        "journalctl -u home-assistant.service | grep -q 'We found a custom integration jura'"
    )

    # Verify no import errors for our component
    machine.fail("journalctl -u home-assistant.service | grep -q 'Error loading.*jura'")
    machine.fail("journalctl -u home-assistant.service | grep -q 'ImportError.*jura'")

    # Translation files HA serves must be installed alongside the component.
    machine.succeed("test -f /var/lib/hass/custom_components/jura/strings.json")
    machine.succeed("test -f /var/lib/hass/custom_components/jura/translations/de.json")
    machine.succeed("test -f /var/lib/hass/custom_components/jura/translations/en.json")

    # End-to-end German translation check: every entity key has a German
    # name, placeholders survive, maintainer-reviewed strings landed.
    print(machine.succeed("python3 ${checkGerman}"))
  '';
}
