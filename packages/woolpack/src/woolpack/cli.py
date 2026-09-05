from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version

from woolpack import lint, render, scaffold


def _package_version() -> str:
    try:
        return version("woolpack")
    except PackageNotFoundError:
        return "0.3.2"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="woolpack", description="Woolpack author tools")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_package_version()}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("new", add_help=False, help="scaffold a pack from the Pebble template")
    commands.add_parser("render", add_help=False, help="render a static visual review board")
    commands.add_parser("lint", add_help=False, help="validate and lint a pack")
    args, command_argv = parser.parse_known_args(argv)

    if args.command == "new":
        return scaffold.main(command_argv, prog="woolpack new")
    if args.command == "render":
        return render.main(command_argv, prog="woolpack render")
    if args.command == "lint":
        return lint.main(command_argv, prog="woolpack lint")
    parser.print_help()
    return 2


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
