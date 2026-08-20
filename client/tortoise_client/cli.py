"""tortoise-client CLI — minimal probe/query surface (#526).

Connectivity + tool-surface helpers for hosted and self-hosted servers.
Thin by design: every subcommand maps 1:1 onto the mcp_client driver; the
daemon tool surface itself is the source of truth (tool_registry #510).

Usage:
    tortoise-client status              # connectivity + tool-count probe
    tortoise-client list-tools          # tool names exposed by the server
    tortoise-client call <tool> [json]  # call an MCP tool (args as JSON)

Connection: TORTOISE_MCP_URL (default http://localhost:8000/mcp) and
TORTOISE_API_KEY (optional; unset -> no auth).

Graceful degradation: `status` never raises — a down server reports
{"status": "tortoise_unavailable", ...} (exit 0), mirroring the driver.
"""
from __future__ import annotations

import argparse
import json
import sys

from tortoise.mcp_client import call_tool, list_tools, status

_EXIT_OK = 0
_EXIT_ERR = 1


def _cmd_status(_args: argparse.Namespace) -> int:
    print(json.dumps(status(), indent=2))
    return _EXIT_OK


def _cmd_list_tools(_args: argparse.Namespace) -> int:
    try:
        tools = list_tools()
    except Exception as exc:  # noqa: BLE001, RUF100
        print(f"tortoise unavailable: {exc}", file=sys.stderr)
        return _EXIT_ERR
    for name in tools:
        print(name)
    return _EXIT_OK


def _cmd_call(args: argparse.Namespace) -> int:
    arguments: dict = {}
    if args.json_args:
        try:
            arguments = json.loads(args.json_args)
        except json.JSONDecodeError as exc:
            print(f"invalid JSON arguments: {exc}", file=sys.stderr)
            return _EXIT_ERR
    try:
        result = call_tool(args.tool, arguments)
    except Exception as exc:  # noqa: BLE001, RUF100
        print(f"call failed: {exc}", file=sys.stderr)
        return _EXIT_ERR
    if getattr(result, "is_error", None):
        print(json.dumps({"is_error": True, "result": str(result)}, indent=2))
        return _EXIT_ERR
    text = "".join(getattr(b, "text", "") for b in (result.content or []))
    try:
        print(json.dumps(json.loads(text), indent=2))
    except json.JSONDecodeError:
        print(text)
    return _EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tortoise-client",
        description="Thin Tortoise network driver CLI (connect to a Tortoise graph server over MCP).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="connectivity + tool-count probe (never raises)")
    p_status.set_defaults(func=_cmd_status)

    p_list = sub.add_parser("list-tools", help="tool names exposed by the server")
    p_list.set_defaults(func=_cmd_list_tools)

    p_call = sub.add_parser("call", help="call an MCP tool")
    p_call.add_argument("tool", help="tool name (e.g. tortoise_create_point)")
    p_call.add_argument("json_args", nargs="?", default="{}", help="arguments as a JSON object")
    p_call.set_defaults(func=_cmd_call)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
