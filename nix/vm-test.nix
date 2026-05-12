{ pkgs, juraConnectPkg, ... }:
pkgs.testers.nixosTest {
  name = "jura-connect-ha-integration";

  nodes.machine =
    { pkgs, ... }:
    {
      services.home-assistant = {
        enable = true;
        config = {
          homeassistant = {
            name = "Test";
            unit_system = "metric";
          };
        };
        customComponents = [
          (pkgs.stdenvNoCC.mkDerivation {
            pname = "ha-jura-connect";
            version = "0.7.4";
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
  '';
}
