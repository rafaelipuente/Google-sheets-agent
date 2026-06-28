"""Command-line interface: chat loop (Method C) and direct tool invocation.

  python -m sheets_agent.cli whoami           # service account email to share with
  python -m sheets_agent.cli ping             # plumbing: read+write one cell
  python -m sheets_agent.cli structure        # pretty-print get_sheet_structure
  python -m sheets_agent.cli call <tool> '<json args>'  # hand-test any tool
  python -m sheets_agent.cli chat             # natural-language agent loop
  python -m sheets_agent.cli usage            # cost / LLM / tool observability view
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


def cmd_usage(args) -> None:
    from . import observability

    events = observability.load_events(args.path)
    summary = observability.summarize(events)
    if args.json:
        _print(summary)
        return

    print("Sheets Agent - usage & cost")
    print(f"  log: {args.path or config.USAGE_LOG_PATH}")
    if not events:
        print("  (no usage recorded yet)")
        return
    print(f"  sessions:          {summary['sessions']}")
    print(f"  LLM calls:         {summary['llm_calls']}")
    print(f"  model tool calls:  {summary['model_tool_calls']}")
    print(f"  system steps:      {summary['system_steps']}")
    print(
        f"  tokens:          {summary['total_prompt_tokens']} in / "
        f"{summary['total_completion_tokens']} out / {summary['total_tokens']} total"
    )
    print(f"  total cost:      ${summary['total_cost_usd']:.4f}")

    if summary["by_model"]:
        print("\n  by model:")
        print(f"    {'model':<16}{'calls':>7}{'in tok':>10}{'out tok':>10}{'cost $':>12}")
        for model, m in sorted(summary["by_model"].items()):
            print(
                f"    {model:<16}{m['calls']:>7}{m['prompt_tokens']:>10}"
                f"{m['completion_tokens']:>10}{m['cost_usd']:>12.4f}"
            )

    if summary["by_tool"]:
        print("\n  by model tool call:")
        print(f"    {'tool':<22}{'calls':>7}{'errors':>8}{'ms total':>12}")
        for tool, t in sorted(summary["by_tool"].items()):
            print(
                f"    {tool:<22}{t['calls']:>7}{t['errors']:>8}{t['duration_ms']:>12.1f}"
            )

    if summary["by_system_step"]:
        print("\n  by system step (run directly each turn, not model tool calls):")
        print(f"    {'step':<22}{'count':>7}{'ms total':>12}")
        for step, s in sorted(summary["by_system_step"].items()):
            print(f"    {step:<22}{s['count']:>7}{s['duration_ms']:>12.1f}")


def cmd_chat(_args) -> None:
    import logging

    from .agent import Agent, AgentError

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
        except AgentError as exc:
            print(exc.user_message)
        except Exception:
            logging.getLogger("sheets_agent").exception("Unexpected error in chat")
            print(
                "Something went wrong (details logged). No changes were saved — "
                "retry, or open the sheet to check."
            )


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

    usage_p = sub.add_parser("usage", help="cost / LLM / tool observability view")
    usage_p.add_argument("--path", default=None, help="usage log path (JSONL)")
    usage_p.add_argument("--json", action="store_true", help="print raw summary JSON")
    usage_p.set_defaults(func=cmd_usage)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
