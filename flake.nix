{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    jura-connect = {
      url = "github:makefu/jura-connect";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { nixpkgs, jura-connect, self, ... }:
    let
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs [
          "x86_64-linux"
          "aarch64-linux"
        ] (system: f nixpkgs.legacyPackages.${system} system);

      version =
        (builtins.fromJSON (builtins.readFile ./custom_components/jura/manifest.json)).version;
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
          # Use HA's python so the type checker can see homeassistant + its deps.
          haPython = pkgs.home-assistant.python;
          pythonEnv = haPython.withPackages (ps: [
            ps.homeassistant
            ps.voluptuous
            ps.pytest
            ps.pytest-asyncio
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
                ty check --python ${pythonEnv}
                touch $out
              '';
          pytest =
            pkgs.runCommand "pytest"
              {
                nativeBuildInputs = [ pythonEnv ];
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
