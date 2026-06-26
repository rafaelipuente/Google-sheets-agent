"""Command-line interface: chat loop (Method C) and direct tool invocation.

  python -m sheets_agent.cli whoami           # service account email to share with
  python -m sheets_agent.cli ping             # plumbing: read+write one cell
  python -m sheets_agent.cli structure        # pretty-print get_sheet_structure
  python -m sheets_agent.cli call <tool> '<json args>'  # hand-test any tool
  python -m sheets_agent.cli chat             # natural-language agent loop
"""

from __future__ import annotations

import argparse
import json
import sys

from . import auth, config
from .sheet import SheetClient
from .tools import SheetTools


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_whoami(_args) -> None:
    email = auth.service_account_email()
    print(f"Share the sheet (Editor) with: {email}")


def cmd_ping(_args) -> None:
    """Plumbing milestone: write then read a single scratch cell."""
    client = SheetClient()
    headers = client.headers()
    scratch_row = len(client.all_values()) + 2  # safely below existing data
    cell = f"A{scratch_row}"
    client.update_range(cell, [["ping-ok"]])
    read_back = client.read_range(cell)
    print(f"Connected. Tab '{config.WORKSHEET_NAME}', headers: {headers}")
    print(f"Wrote/read {cell}: {read_back}")
    print(f"Sheet: {config.sheet_url(client.sheet_id)}")


def cmd_structure(_args) -> None:
    _print(SheetTools().get_sheet_structure())


def cmd_call(args) -> None:
    tools = SheetTools()
    if not hasattr(tools, args.tool):
        sys.exit(f"Unknown tool: {args.tool}")
    payload = json.loads(args.json) if args.json else {}
    result = getattr(tools, args.tool)(**payload)
    _print(result)


def cmd_chat(_args) -> None:
    from .agent import Agent

    agent = Agent()
    print("Sheets agent ready. Type a prompt, or 'exit'.")
    while True:
        try:
            prompt = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if prompt.lower() in {"exit", "quit"}:
            break
        if not prompt:
            continue
        try:
            print(agent.send(prompt))
        except Exception as exc:
            print(f"Error: {exc}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="sheets_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("whoami", help="print the service account email to share with").set_defaults(func=cmd_whoami)
    sub.add_parser("ping", help="plumbing test: read+write a single cell").set_defaults(func=cmd_ping)
    sub.add_parser("structure", help="print get_sheet_structure").set_defaults(func=cmd_structure)

    call_p = sub.add_parser("call", help="invoke a tool by hand")
    call_p.add_argument("tool")
    call_p.add_argument("json", nargs="?", default="{}", help="JSON args object")
    call_p.set_defaults(func=cmd_call)

    sub.add_parser("chat", help="natural-language agent loop").set_defaults(func=cmd_chat)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
