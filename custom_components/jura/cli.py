"""Tiny stand-alone CLI bundled with the integration.

It only carries the bits that need to work outside Home Assistant: a
``--version`` flag (used by the Nix install-check) and a one-shot
``discover`` subcommand that prints any Jura machines reachable on the
local network. For full machine control use the upstream
``jura-connect`` CLI (``python -m jura_connect``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .backends.jura import discover_machines


def _read_version() -> str:
    import importlib.resources

    text = importlib.resources.files("custom_components.jura").joinpath("manifest.json").read_text(encoding="utf-8")
    return json.loads(text)["version"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jura-connect-ha", description="Jura Connect HA helper CLI")
    parser.add_argument("--version", action="store_true", help="print the integration version and exit")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("discover", help="list reachable Jura machines (UDP + TCP sweep)")

    args = parser.parse_args(argv)

    if args.version:
        print(_read_version())
        return 0

    if args.cmd == "discover":
        machines = asyncio.run(discover_machines())
        for m in machines:
            print(f"{m.address}\t{m.name}\tfw={m.fw or '?'}\tvia={m.via}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
