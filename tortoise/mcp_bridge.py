"""stdio ↔ Streamable-HTTP bridge for the Tortoise MCP endpoint (#10605).

WHY THIS EXISTS. A harness config that carries an `Authorization` header is a
CAPTURE, not a lookup: `${TORTOISE_API_KEY}` is expanded once, when the harness
process starts, and the resulting string is replayed for the life of the
process. A long-lived session (an agent that has been up for hours, a CI lane, a
desktop app that stays open for days) therefore keeps sending whatever it
expanded at start-up — empty if the key was not in the environment yet, stale if
it has since rotated — and the server can only answer
`Unauthorized: invalid or missing Bearer token`. That message is accurate and
useless: it points at the credential's VALUE when the client never looked again.

The bridge moves the credential out of the config and into a process that reads
it PER REQUEST. The harness declares a local command instead of a header:

    {"mcpServers": {"tortoise": {
        "command": "python3", "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": "https://api.premiselabs.co/mcp/"}}}}

No key in the config, no `${VAR}` to expand, nothing to go stale. The bridge
resolves the credential from the LIVE environment (`TORTOISE_API_KEY`, or the
`TORTOISE_MCP_API_KEY` alias recorded on #5200) and then from
`~/.tortoise/credentials.json`, on EVERY forwarded request — so a rotation, a
refresh, or a key added after the session started takes effect on the next call.

A blank resolution is never sent as an empty header. It sends nothing, and if
the endpoint rejects the call the bridge answers with the named
`MCP-KEY-EMPTY` refusal — naming every source it read — instead of relaying the
server's "invalid or missing Bearer token". A key that IS configured but
rejected gets the other, equally specific message (the credential itself is the
problem).

Transport shape: newline-delimited JSON-RPC on stdin/stdout (the MCP stdio
transport), forwarded to the endpoint over Streamable HTTP. Requests are handled
SEQUENTIALLY — the stdio spec permits pipelining, and a bridge that reordered
responses would corrupt the session it is meant to preserve.

WHICH SOURCE IS ACTUALLY LIVE, because "reads the live environment" is too
generous: the bridge is a CHILD of the harness, so its `os.environ` is a snapshot
of the harness's at spawn — a key exported in some other shell after the session
started is not visible here. Re-reading buys two real things: a value this
process sets mid-run, and (the case that matters for a long-lived session) the
**credential FILE** (`~/.tortoise/credentials.json`), which is re-read from disk
on every request and is therefore the source that genuinely rotates.

CO-SOURCE (#2369 D1.1): a key taken from the credential file resolves its
endpoint from that SAME file (its `api_url`), never from a `TORTOISE_MCP_URL`
supplied by the environment — a harness config can be committed to a repository,
so letting it choose the host for a stored credential is an exfiltration path.

Named limitations (deliberate, not accidental):

* The server→client GET stream is not opened, so server-initiated messages
  (sampling/elicitation/logging notifications) are not surfaced. Tortoise's tool
  surface is request/response, so this is a non-issue today; a server that starts
  pushing would need a second stream wired to stdout.
* A tool result that arrives as a batch is emitted element-by-element, one line
  each — the stdio transport is one message per line.

Redirects are followed (the endpoint answers `/mcp` with `/mcp/` — #2864), and a
request that comes back with no JSON-RPC reply is answered with a named error
rather than with silence: a bridge that writes nothing leaves the harness waiting
forever, which is indistinguishable from a slow call.

All diagnostics go to STDERR: stdout carries JSON-RPC and nothing else.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

from tortoise.mcp_client import (
    REFUSAL_CODE,
    Connection,
    redact,
    resolve_connection,
)

#: Seconds per forwarded request. A `tortoise_search` carries the server's wait
#: budget, so this is deliberately generous; raise it with
#: `TORTOISE_MCP_BRIDGE_TIMEOUT` when a call is legitimately slow.
_DEFAULT_TIMEOUT = 120.0

_AUTH_STATUSES = (401, 403)


def _log(message: str) -> None:
    """Diagnostics — stderr only, never a credential value."""
    print(f"[mcp-bridge] {message}", file=sys.stderr, flush=True)


def _emit(message: Any) -> None:
    """Write one JSON-RPC message to stdout (one message per line)."""
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _request_ids(payload: Any) -> list[Any]:
    """The ids an error response must be correlated to — REQUESTS only.

    Empty for a notification (no id, no reply expected) and for a *response* the
    client is sending upstream (a reply to a server-initiated request): a message
    with no `method` asks nothing of us, and echoing an error under that id would
    corrupt the peer's correlation table rather than report a failure.
    """
    items = payload if isinstance(payload, list) else [payload]
    return [m["id"] for m in items
            if isinstance(m, dict) and m.get("id") is not None and "method" in m]


def _errors_for(payload: Any, code: int, message: str) -> list[dict]:
    """A JSON-RPC error per correlatable id — nothing for a notification.

    A notification has no id and expects no response; emitting one would be a
    protocol error rather than a report.
    """
    ids = _request_ids(payload)
    if not ids:
        _log(f"{message} (no response: the message was a notification)")
        return []
    return [{"jsonrpc": "2.0", "id": request_id,
             "error": {"code": code, "message": message}} for request_id in ids]


def _response_messages(
    response: httpx.Response, expected_ids: list, sent: Connection,
) -> tuple[list[Any], str]:
    """The JSON-RPC messages the response carries, read WITHOUT waiting for EOF.

    The Streamable-HTTP server answers a POST with `text/event-stream` by
    default (the MCP python SDK's `is_json_response_enabled=False`), and that
    stream stays open after the reply — so a plain `post()` blocks until the
    read timeout. We therefore stream, collect events, and stop as soon as every
    id we asked about has been answered (which also closes the connection, the
    same way fastmcp's own transport does it).

    Returns `(messages, snippet)` — `snippet` is the raw body when it was small
    enough to read whole, so a caller can quote it. It is empty for a streamed
    body: the body has already been consumed and MUST NOT be read again
    (`httpx.StreamConsumed`), which is why the caller never re-reads `response`.
    """
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "text/event-stream":
        raw = response.read()
        # REDACT FIRST, then truncate: a cut that lands INSIDE the credential
        # leaves a fragment `str.replace` can no longer match, so redacting after
        # the slice leaks the value in parts.
        snippet = redact(raw.decode("utf-8", "replace"), sent.key)[:400]
        if not raw:
            return [], ""  # 202 Accepted: a notification/response was accepted, no reply
        try:
            body = json.loads(raw)
        except ValueError:
            _log(f"endpoint answered {response.status_code} with a non-JSON body: "
                 f"{redact(raw.decode('utf-8', 'replace'), sent.key)[:200]!r}")
            return [], snippet
        # REDACT on this path too: the snippet is quoted into the no-reply error
        # below, and a 200 body can quote the credential as easily as a 401 can.
        return (body if isinstance(body, list) else [body]), snippet

    messages: list[Any] = []
    pending = {request_id for request_id in expected_ids if isinstance(request_id, (str, int))}
    if not pending:
        # Nothing to correlate: a notification/response POST is answered 202, and
        # reading an event stream we can stop at no event would block until the
        # read timeout.
        return [], ""
    data: list[str] = []
    for line in response.iter_lines():
        if line.strip():
            if line.startswith("data:"):
                data.append(line[len("data:"):].lstrip())
            continue
        if not data:
            continue
        chunk = "".join(data)
        data = []
        try:
            message = json.loads(chunk)
        except ValueError:
            _log(f"ignoring non-JSON event-stream payload: {redact(chunk, sent.key)[:120]}")
            continue
        messages.append(message)
        for item in (message if isinstance(message, list) else [message]):
            if isinstance(item, dict) and "id" in item:
                pending.discard(item["id"])
        if not pending:
            break
    return messages, ""


def _refusal_message(status: int, body: str, sent: Connection) -> str:
    """The named, actionable message for an auth rejection.

    Two distinct states, never conflated: NO credential configured (a client
    fault, fixable without a restart) versus a credential that the server refused
    (a value fault). Classified from `sent` — what THIS request actually carried
    — never from a fresh resolution: a key rotated away between the send and the
    reply must not produce a claim about a request it was not on.
    """
    detail = f"HTTP {status}"
    if body.strip():
        # REDACT before quoting: a server may echo the key it rejected, and this
        # message is written to the harness's transcript and logs. Quoting is the
        # evidence; the VALUE is never part of it.
        detail += f": {redact(body.strip(), sent.key)[:400]}"
    if sent.key is None:
        return (
            f"{REFUSAL_CODE}: the Tortoise MCP endpoint rejected this request "
            f"({detail}) and NO credential resolved in the bridge (checked "
            f"{', '.join(sent.sources)}). The credential is read on EVERY request, "
            f"so writing {sent.sources[-1]} takes effect on the next call: no "
            f"bridge restart, no harness restart. (An exported variable is read "
            f"from the bridge's own environment, which is a snapshot of the "
            f"harness's at spawn — the credential FILE is the live source.)"
        )
    return (
        f"MCP auth rejected ({detail}). The bridge DID send a credential "
        f"resolved from {sent.source} — so the value itself was refused (expired, "
        f"revoked, or for another organization), not missing. Fix the credential "
        f"at its source; the next request picks it up without a restart."
    )


def _headers(connection: Connection, state: dict, payload: Any) -> dict[str, str]:
    """Per-request headers built from the credential resolved for THIS request."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if connection.key:  # a blank resolution is absent, never `Bearer ` + nothing
        headers["Authorization"] = f"Bearer {connection.key}"
    # `initialize` STARTS a session: carrying a previous id would make the server
    # answer 404 for a session we just asked to replace, so the one recovery a
    # harness can perform would be permanently impossible.
    starting_session = isinstance(payload, dict) and payload.get("method") == "initialize"
    if state.get("session_id") and not starting_session:
        headers["Mcp-Session-Id"] = state["session_id"]
    if state.get("protocol_version") and not starting_session:
        headers["MCP-Protocol-Version"] = state["protocol_version"]
    return headers


def _protocol_version_from(result: Any) -> str | None:
    """The protocol version an `initialize` result negotiated, if this is one."""
    if isinstance(result, dict):
        version = result.get("protocolVersion")
        if isinstance(version, str) and version:
            return version
    return None


def forward(client: httpx.Client, state: dict, payload: Any) -> list[Any]:
    """Send one client message upstream and return the messages to write back.

    Sequential by design (see the module docstring): one request is fully
    answered before the next line is read, so responses cannot be reordered.

    The credential is resolved ONCE per forwarded message and used for that whole
    exchange — the header AND the refusal's evidence — so the two can never
    describe different resources.
    """
    expected_ids = _request_ids(payload)
    connection = resolve_connection()
    url = connection.url
    try:
        with client.stream(
            "POST", url, content=json.dumps(payload).encode(),
            headers=_headers(connection, state, payload),
        ) as response:
            session_id = response.headers.get("mcp-session-id")
            if session_id:
                state["session_id"] = session_id

            if response.status_code in _AUTH_STATUSES:
                return _errors_for(
                    payload, -32001,
                    _refusal_message(response.status_code,
                                     response.read().decode("utf-8", "replace"),
                                     connection),
                )
            if response.status_code == 404 and state.get("session_id"):
                expired = state["session_id"]
                # CLEAR it: the recovery this message asks for is a fresh
                # `initialize`, and a re-sent dead id would 404 forever.
                state["session_id"] = None
                state["protocol_version"] = None
                return _errors_for(
                    payload, -32002,
                    f"HTTP 404 with Mcp-Session-Id {expired} — the MCP session "
                    f"expired or the endpoint moved. The bridge has dropped that id, "
                    f"so an `initialize` from the harness starts a fresh session.",
                )
            if response.status_code >= 400:
                return _errors_for(
                    payload, -32603,
                    f"MCP endpoint returned HTTP {response.status_code}: "
                    f"{redact(response.read().decode('utf-8', 'replace'), connection.key)[:400]}",
                )

            messages, snippet = _response_messages(response, expected_ids, connection)
            if expected_ids and not any(
                isinstance(m, dict) and m.get("id") in expected_ids for m in messages
            ):
                # A request MUST be answered. A reply with no JSON-RPC message for
                # it (odd status, unparseable body, an SSE stream carrying only a
                # notification) would otherwise be written back as NOTHING — the
                # harness waits forever and the bridge looks merely slow.
                return _errors_for(
                    payload, -32603,
                    f"the MCP endpoint answered HTTP {response.status_code} with no "
                    f"JSON-RPC reply for id(s) {expected_ids}"
                    + (f": {snippet[:300]}" if snippet.strip() else ""),
                )
    except httpx.HTTPError as exc:
        return _errors_for(
            payload, -32603,
            f"MCP transport failure talking to {url}: {redact(str(exc), connection.key)}",
        )

    for message in messages:
        if isinstance(message, dict) and "result" in message:
            version = _protocol_version_from(message["result"])
            if version:
                state["protocol_version"] = version
    return messages


def main(argv: list[str] | None = None) -> int:
    """Run the bridge until stdin closes. Returns a process exit code."""
    try:
        timeout = float(os.environ.get("TORTOISE_MCP_BRIDGE_TIMEOUT", _DEFAULT_TIMEOUT))
    except ValueError:
        timeout = _DEFAULT_TIMEOUT
    connection = resolve_connection()
    state: dict[str, Any] = {"session_id": None, "protocol_version": None}
    _log(f"forwarding stdio → {connection.url}")
    _log(
        f"credential: {connection.source}" if connection.key else
        f"credential: NONE configured (checked {', '.join(connection.sources)}) — "
        f"requests go out unauthenticated and a 401 becomes a named "
        f"{REFUSAL_CODE} refusal"
    )

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError as exc:
                _emit({"jsonrpc": "2.0", "id": None,
                       "error": {"code": -32700, "message": f"Parse error: {exc}"}})
                continue
            try:
                messages = forward(client, state, payload)
            except Exception as exc:
                # ANY internal fault is answered, not raised: a bridge that exits
                # on a bad frame leaves the harness waiting with no diagnosis, and
                # the exit is indistinguishable from a crash loop.
                _log(f"internal failure forwarding {line[:120]!r}: {exc!r}")
                messages = _errors_for(
                    payload, -32603, f"MCP bridge internal failure: {exc!r}")
            for message in messages:
                _emit(message)
    _log("stdin closed — bridge exiting")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
