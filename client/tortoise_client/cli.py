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
{"status": "tortoise_unavailable", ...}. The LIBRARY still reports the payload
and never raises (script callers skip cleanly), but the CLI PROBE now carries
a distinct process exit code (#3832 D5), superseding #526's exit-0 clause for
this surface only:

    0 = ok · 3 = can't reach it · 4 = not set up (no endpoint configured)
    1 = a query/tool call that genuinely fails (kept)
    2 = argparse usage errors (argparse owns it)

Only the `status` PROBE emits 3 and 4. `list-tools` and `call` are operations
against a known endpoint, so any failure there (including an unreachable
daemon) keeps the generic code 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from tortoise.mcp_client import call_tool, list_tools, status

# ── Exit codes (#3832 / D5) ─────────────────────────────
# Supersedes #526's exit-0 clause FOR THE CLI PROBE ONLY. The library below is
# unchanged (never raises, returns the status payload), so script callers keep
# skipping cleanly; the exit code is the reporting concern of the surface a
# human or an agent harness actually checks.
_EXIT_OK = 0
_EXIT_ERR = 1  # a query/tool call that genuinely fails (kept, never widened)
_EXIT_UNAVAILABLE = 3  # configured, but we can't reach it
_EXIT_NOT_CONFIGURED = 4  # never set up (no endpoint configured)

_STATUS_EXIT_CODES = {
    "ok": _EXIT_OK,
    "tortoise_unavailable": _EXIT_UNAVAILABLE,
    "not_configured": _EXIT_NOT_CONFIGURED,
}


def _probe_payload() -> dict:
    """The probe payload, with the never-configured case split out (#3832).

    The driver (`tortoise.mcp_client.status`) is deliberately UNCHANGED: it has
    no notion of a missing endpoint (an unset TORTOISE_MCP_URL falls back to
    the self-host default) and reports `tortoise_unavailable` for any failure
    to answer. Splitting 'we were never pointed at a memory' from 'the memory
    is down' is a presentation decision of this probe — the exact boundary the
    D5 landing draws — so the driver keeps its contract and its callers keep
    skipping cleanly, while the human/agent surface gets a distinct signal.

    BOUNDARY, stated rather than implied: on THIS probe, 'TORTOISE_MCP_URL is
    unset' IS the definition of not-configured. A self-hoster who runs the
    default daemon without setting the variable and then loses that daemon
    reads as `not_configured`, not `tortoise_unavailable` — the endpoint was
    never declared, so the probe cannot tell 'intended the default' from 'never
    set up' and does not pretend to. Set TORTOISE_MCP_URL (even to the default)
    to move a down daemon into the can't-reach-it state.

    The rewritten payload drops `url` (the driver filled in the UNCONFIGURED
    default, so keeping it would advertise an endpoint the probe just said was
    never configured) and adds `configured: false`.
    """
    payload = status()
    if payload.get("status") == "tortoise_unavailable" and not os.environ.get("TORTOISE_MCP_URL"):
        payload = {**payload, "status": "not_configured", "configured": False, "url": None}
    return payload


def _exit_code(word: str | None) -> int:
    """Map the driver's machine-readable state to the CLI exit code (#3832).

    An unrecognised word degrades to the unavailable code — fail loud, never
    silently report success for a state we do not know.
    """
    return _STATUS_EXIT_CODES.get(word, _EXIT_UNAVAILABLE)


def _cmd_status(_args: argparse.Namespace) -> int:
    payload = _probe_payload()
    print(json.dumps(payload, indent=2))
    return _exit_code(payload.get("status"))


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
