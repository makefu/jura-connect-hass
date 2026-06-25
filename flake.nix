{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    jura-connect = {
      url = "github:makefu/jura-connect";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      nixpkgs,
      jura-connect,
      self,
      ...
    }:
    let
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs [
          "x86_64-linux"
          "aarch64-linux"
        ] (system: f nixpkgs.legacyPackages.${system} system);

      version = (builtins.fromJSON (builtins.readFile ./custom_components/jura/manifest.json)).version;
    in
    {
      packages = forAllSystems (
        pkgs: system: {
          default = pkgs.python313Packages.buildPythonApplication {
            pname = "ha-jura-connect";
            inherit version;
            src = ./.;
            format = "pyproject";

            build-system = [ pkgs.python313Packages.setuptools ];

            propagatedBuildInputs = [
              jura-connect.packages.${system}.default
            ];

            doInstallCheck = true;
            installCheckPhase = ''
              $out/bin/jura-connect-ha --version | grep -q "${version}"
            '';

            meta.mainProgram = "jura-connect-ha";

            passthru = {
              isHomeAssistantComponent = true;
              domain = "jura";
            };
          };
        }
      );

      devShells = forAllSystems (
        pkgs: system: {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python313.withPackages (ps: [
                ps.pytest
                ps.pytest-asyncio
                ps.ruff
                ps.mypy
                ps.voluptuous
                ps.freezegun
                jura-connect.packages.${system}.default
              ]))
            ];
          };
        }
      );

      checks = forAllSystems (
        pkgs: system:
        let
          # Home Assistant pins its own (newer) Python; the upstream
          # jura_connect package is built for pkgs.python313, so its module
          # is not importable under HA's interpreter. Rebuild it against
          # HA's Python purely so the type checker can resolve the import.
          haPython = pkgs.home-assistant.python;
          juraConnectHa = haPython.pkgs.buildPythonPackage {
            pname = "jura_connect";
            version = jura-connect.packages.${system}.default.version;
            src = jura-connect.packages.${system}.default.src;
            pyproject = true;
            build-system = [ haPython.pkgs.setuptools ];
            doCheck = false;
          };
          # ty needs HA's stubs + jura_connect visible together.
          tyEnv = haPython.withPackages (ps: [
            ps.homeassistant
            ps.voluptuous
            juraConnectHa
          ]);
          # pytest stubs Home Assistant in conftest.py, so the suite only
          # needs the test stack plus the real jura_connect — same env as
          # the dev shell (pkgs.python313), where jura_connect imports.
          testEnv = pkgs.python313.withPackages (ps: [
            ps.pytest
            ps.pytest-asyncio
            ps.voluptuous
            ps.freezegun
            jura-connect.packages.${system}.default
          ]);
        in
        {
          ruff =
            pkgs.runCommand "ruff-check"
              {
                nativeBuildInputs = [ pkgs.ruff ];
                RUFF_CACHE_DIR = "/tmp/ruff-cache";
              }
              ''
                cd ${self}
                ruff check .
                ruff format --check .
                touch $out
              '';
          ty =
            pkgs.runCommand "ty-check"
              {
                nativeBuildInputs = [ pkgs.ty ];
              }
              ''
                cd ${self}
                # Type-check the shipped integration only; the test suite
                # leans on MagicMock stubs that defeat static typing.
                ty check custom_components --python ${tyEnv}
                touch $out
              '';
          pytest =
            pkgs.runCommand "pytest"
              {
                nativeBuildInputs = [ testEnv ];
              }
              ''
                cd ${self}
                HOME=$TMPDIR PYTHONPATH=${self} pytest tests/ -v
                touch $out
              '';
        }
        // nixpkgs.lib.optionalAttrs (system == "x86_64-linux") {
          vm-test = import ./nix/vm-test.nix {
            inherit pkgs;
            juraConnectPkg = jura-connect.packages.${system}.default;
          };
        }
      );

      formatter = forAllSystems (pkgs: _: pkgs.nixfmt-rfc-style);
    };
}
