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

Graceful degradation: `status` never raises. The LIBRARY keeps reporting its
payload and never raising (script callers skip cleanly), while the CLI PROBE
presents ONE recorded status vocabulary (#3805 / roadmap §7 item 9) under a
distinct process exit code (#3832 D5), superseding #526's exit-0 clause for
this surface only:

    available      reached and returned content      -> 0
    empty          reached and returned nothing      -> 0
    degraded       configured but could NOT be reached -> 3  (off by outage)
    unconfigured   no endpoint/key configured        -> 4  (off by policy)
    1 = a query/tool call that genuinely fails (kept)
    2 = argparse usage errors (argparse owns it)

The four terms are `tortoise.status_vocabulary` — the ONE recorded
client-boundary term set, imported rather than redeclared (the client wheel
stages that module as a shared file). The driver (`tortoise/mcp_client.py`) has
two outcomes of its own: the pre-#3805 outage word `tortoise_unavailable` (kept
verbatim, translated by `status_vocabulary.resolve`) and — since #10605 — the
recorded `unconfigured` term for a credential-less auth refusal, because "this
client has no key" is a set-up gap rather than an outage. Everything else the
probe reports is derived here: the driver still has no notion of a missing
ENDPOINT. The pre-#3805 words (`ok` / `tortoise_unavailable` /
`not_configured`) are translated, never re-minted — see
`status_vocabulary.resolve` (and `LEGACY_WORDS` for the flat table).

Only the `status` PROBE emits 3 and 4. `list-tools` and `call` are operations
against a known endpoint, so any failure there (including an unreachable
daemon) keeps the generic code 1.
"""

from __future__ import annotations

import argparse
import json
import sys

from tortoise import status_vocabulary as vocab
from tortoise.mcp_client import (
    call_tool,
    describe_failure,
    endpoint_declared,
    list_tools,
    refusal_message,
    status,
)

# ── Exit codes (#3832 / D5, vocabulary #3805) ───────────
# Supersedes #526's exit-0 clause FOR THE CLI PROBE ONLY. The library below is
# unchanged (never raises, returns the status payload), so script callers keep
# skipping cleanly; the exit code is the reporting concern of the surface a
# human or an agent harness actually checks.
#
# The D5 codes are preserved VERBATIM; only the status WORDS are now the four
# recorded terms, because a caller that branches on the word (the normal
# contract) was previously unable to tell an empty store (`ok`) from a reachable
# one, and had only a two-outcome driver word to branch on.
_EXIT_OK = 0
_EXIT_ERR = 1  # a query/tool call that genuinely fails (kept, never widened)
_EXIT_UNAVAILABLE = 3  # configured, but we can't reach it
_EXIT_NOT_CONFIGURED = 4  # never set up (no endpoint configured)

_STATUS_EXIT_CODES = {
    vocab.STATUS_AVAILABLE: _EXIT_OK,
    vocab.STATUS_EMPTY: _EXIT_OK,
    vocab.STATUS_DEGRADED: _EXIT_UNAVAILABLE,
    vocab.STATUS_UNCONFIGURED: _EXIT_NOT_CONFIGURED,
}


def _probe_payload() -> dict:
    """The probe payload, carrying the recorded client-boundary term (#3805).

    The driver (`tortoise.mcp_client.status`) has no notion of a missing
    ENDPOINT (an unset TORTOISE_MCP_URL falls back to the self-host default), so
    `configured` below is this probe's contribution, and it decides the driver's
    two-outcome outage word only. It is asked of the RESOLUTION
    (`mcp_client.endpoint_declared`), not of the environment: since #10605 an
    endpoint can also be DECLARED by a store's `api_url`, and deriving it from
    `TORTOISE_MCP_URL` alone reported a store-configured, genuinely-down server
    as `unconfigured` (exit 4, a policy gap) with `url: None` — inverting the
    load-bearing property that `degraded` and `unconfigured` are never reported as
    each other (#3805). The one term the DRIVER owns is the credential-less
    refusal (`unconfigured`, #10605): it already arrives here as a recorded term
    and is passed through untouched, which is why this probe never has to guess
    whether a missing key was the cause.

    BOUNDARY, stated rather than implied: for the UNAVAILABLE word, "no endpoint
    that this client will USE is declared" IS the definition of unconfigured on
    this probe — `declared` describes the RESOLVED connection, so it is false when
    neither `TORTOISE_MCP_URL` nor the store that supplies the credential records
    an `api_url`. (A store may record an `api_url` and still yield `declared:
    False`: with no usable `api_key` in it the store is not a credential source and
    its endpoint is never read.) A self-hoster who runs the default daemon having
    declared nothing, and then loses that daemon, reads as `unconfigured`, not
    `degraded` — the probe cannot tell "intended the default" from "never set
    up" and does not pretend to. Declare an endpoint (even the default) to move a
    down daemon into the can't-reach-it state.

    ⛔ The rewrite applies to a word OUTSIDE the recorded term set
    (`CLIENT_STATUS_TERMS`) — in practice the `tortoise_unavailable` outage word
    and the flat legacy `not_configured` (both resolve to `unconfigured` and
    neither can be translated without the configuration fact). It is NOT keyed on
    `LEGACY_WORDS_NEEDING_CONFIGURATION`, which is narrower and would pass
    `not_configured` through verbatim. A word the DRIVER already reports inside the recorded
    term set passes through VERBATIM, url included (#10605 review, P1): the
    credential-less refusal arrives as the recorded `unconfigured` carrying the
    endpoint it actually resolved and contacted, and rewriting THAT to
    `{url: None, configured: false}` told a caller to declare an endpoint it
    already had — the misattribution #10605 exists to remove, and the opposite of
    what the paragraph above promises.

    The rewritten payload (legacy word + no declared endpoint) drops `url` (the
    driver filled in the UNCONFIGURED default, so keeping it would advertise an
    endpoint the probe just said was never configured) and adds
    `configured: false`.
    """
    payload = status()
    raw = payload.get("status")
    configured = endpoint_declared()
    term = vocab.resolve(raw, configured=configured)
    if (term == vocab.STATUS_UNCONFIGURED and not configured
            and raw not in vocab.CLIENT_STATUS_TERMS):
        return {**payload, "status": term, "configured": False, "url": None}
    return {**payload, "status": term}


def _exit_code(term: str | None) -> int:
    """Map a recorded term to the CLI exit code (#3832 / D5).

    An unrecognised term degrades to the unavailable code — fail loud, never
    silently report success for a state we do not know. `vocabulary.resolve`
    makes this unreachable for a driver word, which is the point: the fail-loud
    default is a backstop, not a live path.
    """
    return _STATUS_EXIT_CODES.get(term, _EXIT_UNAVAILABLE)


def _cmd_status(_args: argparse.Namespace) -> int:
    payload = _probe_payload()
    print(json.dumps(payload, indent=2))
    return _exit_code(payload.get("status"))


def _cmd_list_tools(_args: argparse.Namespace) -> int:
    try:
        tools = list_tools()
    except Exception as exc:  # noqa: BLE001, RUF100
        # The server may echo the credential it rejected; print evidence, never
        # the VALUE (#10605).
        print(f"tortoise unavailable: {describe_failure(exc)}", file=sys.stderr)
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
        # A REFUSAL is not a crash (#11153). The driver's client raises on a
        # tool-level failure (`raise_on_error=True`), so a designed refusal — the
        # server's `{"error": …}` envelope, or the transport wait-bound prose —
        # arrives HERE, not as a returned `is_error` result. Reporting it under the
        # documented refusal shape keeps the two distinguishable for a scripted
        # caller: rc is 1 either way, but stdout carries the refusal and stderr
        # carries nothing, while a genuine failure stays on stderr with rc 1.
        refusal = refusal_message(exc)
        if refusal is not None:
            print(json.dumps({"is_error": True, "result": refusal}, indent=2))
        else:
            print(f"call failed: {describe_failure(exc)}", file=sys.stderr)
        return _EXIT_ERR
    if getattr(result, "is_error", None):
        # Defence in depth: reachable only for a client built with
        # `raise_on_error=False` (the default is True), where an error result is
        # RETURNED instead of raised. Kept so the documented shape holds for both
        # client configurations rather than only the one this module builds.
        print(json.dumps({"is_error": True, "result": str(result)}, indent=2))
        return _EXIT_ERR
    # The PAYLOAD is `content[0]`, and extra blocks are appended LAST by the
    # dispatch seam (a retired-name warning, the #10084 graph disclosure). Joining
    # EVERY block and parsing the result is not a contract: it turns valid JSON
    # into "Extra data: line 1 …" for every scripted caller of a write tool.
    #
    # `content[0]` and NOT `structured_content`: for a tool declaring
    # `x-fastmcp-wrap-result` the structured payload is the ENVELOPE
    # (`{"result": …}`) while `content[0]` is the tool's own answer — preferring
    # the former would silently change this command's output shape for 12 read
    # tools that never receive a disclosure block at all.
    blocks = list(getattr(result, "content", None) or [])
    text = getattr(blocks[0], "text", "") if blocks else ""
    try:
        print(json.dumps(json.loads(text), indent=2))
    except json.JSONDecodeError:
        print(text)
    # Every block AFTER the payload is a disclosure the caller must not lose —
    # a retired-name warning (#3883), the #10084 graph resolution. stdout stays
    # payload-only so `| jq` keeps working; the rest goes to stderr, which is
    # what a human reads and what a pipeline can still capture with 2>&1.
    for extra in blocks[1:]:
        tail = getattr(extra, "text", "")
        if tail:
            print(tail, file=sys.stderr)
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
