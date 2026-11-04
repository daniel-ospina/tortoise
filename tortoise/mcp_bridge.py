"""stdio ↔ Streamable-HTTP bridge for the Tortoise MCP endpoint (#10605).

WHY THIS EXISTS. A harness config that carries an `Authorization` header is a
CAPTURE, not a lookup — and the capture has TWO halves, only one of which is
about the `${VAR}` syntax.

  * The `${VAR}` EXPANSION is not the stale part. On the harness that reported
    this (#10605, measured twice from its source and once on the wire: 3/3
    `initialize` POSTs) `expandExpr()` RE-RUNS AT EVERY CONNECT. Re-expanding a
    frozen `process.env` is still a capture, but the name is the defect that
    produced the reported 401: `process.env` is captured when the harness
    process starts, and an UNSET variable expands silently to `""` — so
    `${TORTOISE_MCP_API_KEY}`, a name the client did not read, sent
    `Authorization: Bearer ` (empty) on every connect of a session that was
    otherwise correctly configured.
  * What is frozen is `process.env` itself (and, for a bridge child, the
    snapshot it is spawned with), plus the per-CONNECTION capture: the header a
    config yields is resolved once per connection and then replayed for that
    connection's life, so a rotation mid-connection is invisible until the next
    one.

So the server can only answer `Unauthorized: invalid or missing Bearer token`.
That message is accurate and useless: it points at the credential's VALUE when
the client never looked at the source again after the process started — and,
in the reported shape, when the NAME it looked for was never set at all. The
design consequence is a CONFIG-NAME fix, not a cache-expiry fix, which is why
the bridge moves the name out of the config entirely rather than trying to
refresh an expansion.

The bridge moves the credential out of the config and into a process that reads
it PER REQUEST. The harness declares a local command instead of a header:

    {"mcpServers": {"tortoise": {
        "command": "python3", "args": ["-m", "tortoise.mcp_bridge"],
        "env": {"TORTOISE_MCP_URL": "https://api.premiselabs.co/mcp/"}}}}

No key in the config, no `${VAR}` to expand, nothing to go stale. The bridge
resolves the credential from the LIVE environment (`TORTOISE_API_KEY`, or the
`TORTOISE_MCP_API_KEY` alias recorded on #5200) and then from the stored
credentials — the PROJECT-LOCAL `./.tortoise` that `tortoise init --api-key`
writes, then `~/.tortoise/credentials.json` (#1708 D5 order: the first key found
wins) — on EVERY forwarded request, so a rotation, a refresh, or a key added
after the session started takes effect on the next call. ONE exception, and it is
a trust boundary rather than a precedence rule: a store that ARRIVES WITH a
repository is IGNORED — git reports it tracked, or git cannot be asked and a
`.git` sits above it — because both halves of the identity come from that one file
(`api_key` and `api_url`), so a repository that commits it would choose the key
AND the host — the shape `#3660` fails closed on.

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
**credential FILE** (either store — `./.tortoise`, then
`~/.tortoise/credentials.json`), which is re-read from disk on every request and
is therefore the source that genuinely rotates. Rotating the GLOBAL file while an
UNTRACKED project-local store supplies the key changes nothing: the project-local
one wins (#1708 D5) — unless that store is git-tracked, in which case it is
ignored and the global one wins after all (see the trust boundary above).

CO-SOURCE (#2369 D1.1): a key taken from the credential file resolves its
endpoint from that SAME file (its `api_url`), never from a `TORTOISE_MCP_URL`
supplied by the environment — a harness config can be committed to a repository,
so letting it choose the host for a stored credential is an exfiltration path.

Named limitations (deliberate, not accidental):

* The server→client GET stream is not opened, so server-initiated messages
  (sampling/elicitation/logging notifications) are not surfaced. Tortoise's tool
  surface is request/response, so this is a non-issue today; a server that starts
  pushing would need a second stream wired to stdout.
* A batch reply is emitted element-by-element, one line each, whichever media
  type carried it (a JSON body or an event-stream `data:` frame) — the stdio
  transport is one message per line.

Redirects are followed ONLY when they stay on the endpoint's own ORIGIN (same
scheme, host and port) AND preserve the request (307/308) — the `/mcp` → `/mcp/`
hop the endpoint answers (#2864) is exactly that shape. A redirect to a DIFFERENT
origin is refused BEFORE the body is re-sent: httpx re-issues a 307/308 with the
same bytes, so following one would replay the JSON-RPC payload (tool arguments,
`tortoise_search` queries, `create_point` content) and the session headers to
whatever host the `Location` names, while `Authorization` is deliberately dropped
across origins — the leak GHSA-5h93-6whr-6q8j was filed for, and the policy every
MCP SDK now implements (same-origin 307/308 only, `python-sdk` #3397). A
`301`/`302`/`303` is not followed either: re-POSTing a JSON-RPC body on a status
that means "moved" would be a policy the endpoint did not state, and turning the
POST into a GET cannot carry a request at all.

A request that comes back with no JSON-RPC reply is answered with a named error
rather than with silence: a bridge that writes nothing leaves the harness waiting
forever, which is indistinguishable from a slow call.

All diagnostics go to STDERR: stdout carries JSON-RPC and nothing else.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from typing import Any

import httpx

from tortoise.mcp_client import (
    REFUSAL_CODE,
    Connection,
    _store_base,
    redact,
    resolve_connection,
)

#: the named code for "a credential resolved but this request carried none".
NOT_CARRIED_CODE = "MCP-CREDENTIAL-NOT-CARRIED"

#: Seconds per forwarded request. A `tortoise_search` carries the server's wait
#: budget, so this is deliberately generous; raise it with
#: `TORTOISE_MCP_BRIDGE_TIMEOUT` when a call is legitimately slow.
_DEFAULT_TIMEOUT = 120.0

_AUTH_STATUSES = (401, 403)

#: Every redirect status the bridge must make a DECISION about (never silently
#: follow, never silently ignore).
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: The two statuses that mean "same request, different URL". Only these preserve
#: a JSON-RPC POST; `301`/`302`/`303` do not, and are refused by name.
_METHOD_PRESERVING_REDIRECTS = frozenset({307, 308})

#: How many same-origin hops one forwarded message may take before the bridge
#: reports a loop. `/mcp` → `/mcp/` is one; three is already generous.
_MAX_REDIRECT_HOPS = 3


def _log(message: str) -> None:
    """Diagnostics — stderr only, never a credential value, never the wire, never a raise.

    Two guards, both load-bearing:

    * `sys.stderr is None` when fd 2 is closed, and `print(..., file=None)` then
      falls back to **`sys.stdout`** — which in this process IS the JSON-RPC
      protocol pipe. A harness reading stdout as MCP would see
      `[mcp-bridge] credential: …` as a parse error and kill the session.
    * the write itself can fail — ENOSPC under `2>/dev/full`, BrokenPipe when the
      collector goes away mid-session — and this writer is reached from
      `main()`'s `except Exception` handler. A `_log` that raises there re-enters
      the handler, escapes `main()`, and leaves the next request in the batch
      unanswered: the exact hang the handler exists to prevent.

    A dropped diagnostic is always cheaper than a corrupted or unanswered frame.
    """
    if sys.stderr is None:
        return
    with contextlib.suppress(OSError, ValueError):
        print(f"[mcp-bridge] {message}", file=sys.stderr, flush=True)


def _emit(message: Any) -> None:
    """Write one JSON-RPC message to stdout (one message per line).

    `json.dumps` default `ensure_ascii=True` is deliberate, not a formatting
    choice. The protocol stream's ENCODING is not ours to assume: a harness may
    run the child with an ASCII stdout (`PYTHONIOENCODING`, a container locale
    with no `C.UTF-8`, Windows ANSI), and `ensure_ascii=False` then raises
    `UnicodeEncodeError` on any non-ASCII reply — Tortoise content is
    multilingual, so that is ordinary content, not exotic input. This write is
    the one call in `main()`'s emit loop that is NOT inside a `try`, so the raise
    escapes, the bridge writes NOTHING for that frame, and the harness waits
    forever — the silence this module promises never to emit. Escaping to
    `\\uXXXX` keeps the wire bytes ASCII by construction and is decoded
    identically by the peer, so nothing about the protocol changes.
    """
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _id_key(value: Any) -> str:
    """A hashable, type-preserving key for a JSON-RPC id.

    JSON-RPC 2.0 says String/Number/NULL, but a peer may send an array or an
    object, and those are UNHASHABLE — so any set/dict-keyed comparison over ids
    raises `TypeError`, which `main()` turns into a fabricated "internal failure"
    that discards answers the server did give. Worse, filtering such ids out of
    the comparison entirely (as `isinstance(i, (str, int))` did) leaves a request
    with no answer and no error: the silence this module promises never to emit.

    `json.dumps` keeps `1` and `"1"` distinct while making every JSON value usable
    as a key, so ONE helper serves `pending`, the answered-set and the relay
    filter instead of three hand-rolled conventions.
    """
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover - non-JSON value from a bug
        return repr(value)


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
    return _errors_for_ids(ids, code, message)


def _errors_for_ids(ids: list[Any], code: int, message: str) -> list[dict]:
    """A JSON-RPC error for EACH given id, and only those.

    Split out from `_errors_for` so the no-reply guard can answer exactly the ids
    that went unanswered. `_errors_for(payload, …)` derives its ids from the
    payload, so answering a PARTIAL batch with it would emit an error for the id
    the server DID answer — inventing a failure for a request that succeeded.
    """
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
    pending = {_id_key(request_id) for request_id in expected_ids}
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
        # FLATTEN at ingestion (#10727 review, P2): a JSON-RPC BATCH reply arrives
        # as a list in ONE event, while the non-SSE path above already returns a
        # flat list of messages. Appending the list would leave `messages` mixing
        # shapes, and every consumer below — the no-reply guard, the `result`
        # capture and `_emit` — reads per-message dicts. Appending made a batch
        # close `pending` (the loop two lines down unwraps it) and then be
        # declared "no JSON-RPC reply" with the answer in hand. One shape out.
        messages.extend(message if isinstance(message, list) else [message])
        for item in (message if isinstance(message, list) else [message]):
            if isinstance(item, dict) and "id" in item:
                pending.discard(_id_key(item["id"]))
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


def _origin(url: str) -> tuple[str, str, int | None]:
    """(scheme, host, port) for a URL — the ORIGIN, not the endpoint.

    Deliberately coarser than `mcp_client._same_endpoint`: the PATH may differ,
    because `/mcp` → `/mcp/` is the same-origin hop this bridge must keep
    following (#2864). `httpx.URL.port` fills in the scheme's default port, so
    `http://h` and `http://h:80` are one origin.
    """
    parsed = httpx.URL(url)
    return (parsed.scheme.lower(), parsed.host.lower(), parsed.port)


def _same_origin(a: str, b: str) -> bool:
    """True when two URLs share scheme, host and port (path ignored)."""
    try:
        return _origin(a) == _origin(b)
    except (httpx.InvalidURL, ValueError):  # malformed: never assume it is ours
        return False


def _redirect_target(url: str, location: str) -> str | None:
    """The absolute URL a `Location` header names, or None if unusable.

    Relative locations are resolved against the URL that was asked, which is what
    makes a same-origin `/mcp/` hop (a bare path) land on the right host.
    """
    location = (location or "").strip()
    if not location:
        return None
    try:
        return str(httpx.URL(url).join(location))
    except (httpx.InvalidURL, ValueError):
        return None


def _redirect_remedy(target: str, sent: Connection) -> str:
    """Advice that can work for the SOURCE the credential came from."""
    if sent.source and "/" in str(sent.source):
        # A FILE-sourced credential decides its OWN endpoint (co-source, #2369
        # D1.1), so "point TORTOISE_MCP_URL at it" is advice that cannot work: the
        # store's `api_url` wins and the operator repeats the failure forever.
        return (
            f"record {_store_base(target)} as `api_url` in {sent.source} — a stored "
            f"credential takes its endpoint from that same file — or run the bridge "
            f"from a directory whose store already names {target} (that store must "
            f"not be committed: a git-tracked store is IGNORED as repo-controlled, "
            f"#10796)."
        )
    return (
        f"point TORTOISE_MCP_URL at {target} directly so the credential goes out "
        f"on the first hop: the next request picks that up without a restart."
    )


def _redirect_refusal(
    response: httpx.Response, body: str, sent: Connection, target: str,
) -> str:
    """The named refusal for a CROSS-ORIGIN redirect the bridge refused to follow.

    A DISTINCT state, not a variant of the value fault: the credential resolved,
    it was simply never sent, so the value is not what got refused. Asserting
    otherwise is the #10605 defect in mirror image — a message that is accurate
    about the status and useless about the cause.

    AND: the refusal names the delivery that did NOT happen. Following the hop is
    what would have re-sent the JSON-RPC body to `target` (httpx re-issues 307/308
    with the same bytes); refusing it before the second request is what keeps the
    payload and the session headers off a foreign host.
    """
    final = redact(target, sent.key)
    detail = f"HTTP {response.status_code}"
    if body.strip():
        # REDACT FIRST, TRUNCATE SECOND — the order is the property (#1092).
        # Slicing first would cut a credential in half, and `str.replace` cannot
        # match a value it can no longer see whole, so up to `len(key) - 1`
        # characters would reach the harness transcript and the CI log. This site
        # is the reason the rule is stated as a rule and not as a habit: the
        # redirect refusal was added after every sibling site was repaired.
        detail += f": {redact(body.strip(), sent.key)[:400]}"
    return (
        f"{NOT_CARRIED_CODE}: the Tortoise MCP endpoint answered this request "
        f"({detail}) with a redirect to {final}, a DIFFERENT origin, so the bridge "
        f"DID NOT follow it: the JSON-RPC payload and the session headers are never "
        f"replayed to another host, and an Authorization header is deliberately not "
        f"carried across origins — the credential resolved from {sent.source} was "
        f"never sent to {final} and is not what was refused. Fix: "
        f"{_redirect_remedy(final, sent)}"
    )


def _method_redirect_refusal(
    response: httpx.Response, body: str, sent: Connection, target: str,
) -> str:
    """The refusal for a redirect that cannot carry a JSON-RPC POST (301/302/303).

    Not the credential class: nothing was refused, and on a same-origin hop the
    credential WOULD have been carried. Replaying the body on a "moved" status
    would also follow a policy the endpoint never stated — 307/308 is how a server
    says "same request, new URL" — so the hop is refused and named.
    """
    final = redact(target, sent.key)
    detail = f"HTTP {response.status_code}"
    if body.strip():
        # REDACT FIRST, TRUNCATE SECOND — the rule stated on `_redirect_refusal`.
        detail += f": {redact(body.strip(), sent.key)[:400]}"
    return (
        f"MCP endpoint answered {detail} with a redirect to {final}, which this "
        f"bridge does not follow: a JSON-RPC request is a POST, and only 307/308 "
        f"mean 'the same request, at another URL'. Nothing was re-sent. Fix: "
        f"{_redirect_remedy(final, sent)}"
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


def _redact_any(text: str, *keys: str | None) -> str:
    """Redact against every key that could have been on the wire."""
    for key in keys:
        text = redact(text, key)
    return text


def _redact_message(message: Any, key: str | None) -> Any:
    """`message` with any credential VALUE replaced, at any depth.

    A reply that ANSWERS the request id is relayed to stdout as-is, and a server
    is free to quote the key it rejected inside `error.message`. Every other path
    in this module redacts; this is the one that writes to the harness, so it is
    the one that matters most.
    """
    if not key:
        return message
    if isinstance(message, str):
        return redact(message, key)
    if isinstance(message, dict):
        return {k: _redact_message(v, key) for k, v in message.items()}
    if isinstance(message, list):
        return [_redact_message(item, key) for item in message]
    return message


def _protocol_version_from(result: Any) -> str | None:
    """The protocol version an `initialize` result negotiated, if this is one."""
    if isinstance(result, dict):
        version = result.get("protocolVersion")
        if isinstance(version, str) and version:
            return version
    return None


def _capture_protocol_version(messages: list[Any], state: dict) -> None:
    """Record the protocol version an `initialize` result negotiated.

    Called on EVERY path that relays a reply, including the partial-batch early
    return in `forward()`. That path bypasses the tail of the function, and
    without this call a batched `initialize` whose sibling id went unanswered
    would leave `state["protocol_version"]` unset for the rest of the session, so
    every later request would omit `MCP-Protocol-Version` and the server would
    fall back to its default revision. Idempotent — the tail calls it again.
    """
    for message in messages:
        if isinstance(message, dict) and "result" in message:
            version = _protocol_version_from(message["result"])
            if version:
                state["protocol_version"] = version


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
    body = json.dumps(payload).encode()
    hops = 0
    try:
        while True:
            with client.stream(
                "POST", url, content=body,
                headers=_headers(connection, state, payload),
            ) as response:
                session_id = response.headers.get("mcp-session-id")
                if session_id:
                    state["session_id"] = session_id

                if response.status_code in _REDIRECT_STATUSES:
                    # DECIDED HERE, BEFORE A SECOND REQUEST EXISTS. httpx with
                    # `follow_redirects=True` re-issues a 307/308 with the same
                    # body to whatever host the `Location` names (dropping only
                    # `Authorization`), which delivers the JSON-RPC payload and
                    # the session headers to a foreign origin and classifies the
                    # 401 it gets back AFTER the fact — the leak is the delivery,
                    # not the wording (#10727 review, P2; GHSA-5h93-6whr-6q8j).
                    # The client is built with `follow_redirects=False`, so this
                    # is the only place a hop can happen: same-origin 307/308 is
                    # followed, everything else is refused without being sent.
                    text = redact(
                        response.read().decode("utf-8", "replace"), connection.key)
                    target = _redirect_target(
                        url, response.headers.get("location", ""))
                    if target is None:
                        return _errors_for(
                            payload, -32603,
                            f"MCP endpoint answered HTTP {response.status_code} with "
                            f"no usable Location header — the bridge does not follow "
                            f"a redirect it cannot resolve. Nothing was re-sent.",
                        )
                    if response.status_code not in _METHOD_PRESERVING_REDIRECTS:
                        return _errors_for(
                            payload, -32603,
                            _method_redirect_refusal(
                                response, text, connection, target),
                        )
                    if not _same_origin(url, target):
                        return _errors_for(
                            payload, -32001,
                            _redirect_refusal(
                                response, text, connection, target),
                        )
                    if hops >= _MAX_REDIRECT_HOPS:
                        return _errors_for(
                            payload, -32603,
                            f"MCP endpoint answered {hops + 1} same-origin "
                            f"redirects in a row (last: {redact(target, connection.key)}) "
                            f"— refusing to follow a loop.",
                        )
                    hops += 1
                    url = target
                    continue

                if response.status_code in _AUTH_STATUSES:
                    rejected = response.read().decode("utf-8", "replace")
                    return _errors_for(
                        payload, -32001,
                        _refusal_message(response.status_code, rejected, connection),
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
                # ANSWERED AS A SET, not as "any". `not any(...)` treated one answered
                # id as a complete answer, so a BATCH whose second id went unanswered
                # produced no output for it at all — the harness then waits on that id
                # forever, which is the exact silence this guard exists to prevent.
                # HASHABLE IDS, WITHOUT DROPPING ANY. A JSON-RPC `id` may be an array or
                # an object; a set-keyed comparison over those raises `TypeError` (a
                # fabricated "internal failure" that discards real answers), and
                # filtering them out of the comparison leaves the request with no
                # answer AND no error — the silence this guard exists to prevent.
                # `_id_key` makes every JSON value a key and keeps `1` distinct from
                # `"1"`, so no id can fall through either hole.
                answered = {_id_key(m["id"]) for m in messages
                            if isinstance(m, dict) and m.get("id") is not None}
                expected_keys = {_id_key(i) for i in expected_ids}
                # Dedupe on the KEY, not the raw id: `dict.fromkeys(expected_ids)`
                # raises on exactly the unhashable ids this helper exists to support.
                seen: set[str] = set()
                missing: list[Any] = []
                for request_id in expected_ids:
                    key = _id_key(request_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    if key not in answered:
                        missing.append(request_id)
                if missing:
                    # A request MUST be answered. A reply with no JSON-RPC message for
                    # it (odd status, unparseable body, an SSE stream carrying only a
                    # notification) would otherwise be written back as NOTHING — the
                    # harness waits forever and the bridge looks merely slow.
                    #
                    # The ANSWERED messages are kept, in order: a batch where id 1 was
                    # answered and id 2 was not must deliver id 1's result AND an error
                    # for id 2, or fixing one silence creates another. Only messages
                    # carrying one of THIS request's ids are relayed, so an id-less
                    # server notification does not lead a reply a request/response
                    # harness has no way to correlate.
                    #
                    # ⛔ REDACTED HERE EXPLICITLY. This early return bypasses the
                    # `_redact_message` sweep at the end of `forward()` — the sweep that
                    # exists BECAUSE a relayed reply may quote the credential the server
                    # rejected. Applying the guard without it traded a silence for a
                    # leak on the busiest relay path, which is strictly worse.
                    relayed = [_redact_message(m, connection.key) for m in messages
                               if isinstance(m, dict) and m.get("id") is not None
                               and _id_key(m["id"]) in expected_keys]
                    # ⛔ AND SO IS THE PROTOCOL-VERSION CAPTURE. It lives in the tail,
                    # which this return skips, so a batched `initialize` whose sibling
                    # id went unanswered would negotiate a version and then never send
                    # `MCP-Protocol-Version` again for the life of the session.
                    _capture_protocol_version(relayed, state)
                    return [
                        *relayed,
                        *_errors_for_ids(
                            missing, -32603,
                            f"the MCP endpoint answered HTTP {response.status_code} with no "
                            f"JSON-RPC reply for id(s) {missing}"
                            + (f": {snippet[:300]}" if snippet.strip() else ""),
                        ),
                    ]
                break
    except httpx.HTTPError as exc:
        return _errors_for(
            payload, -32603,
            f"MCP transport failure talking to {url}: {redact(str(exc), connection.key)}",
        )

    messages = [_redact_message(message, connection.key) for message in messages]
    _capture_protocol_version(messages, state)
    return messages


def _live_key() -> str | None:
    """The credential that resolves RIGHT NOW, or `None` if resolution fails.

    The FAULT path must not be able to fault. `resolve_connection()` is
    never-raise by construction — `_credential_paths()` swallows an unlocatable
    store, the store read/parse catches EVERY failure (including the
    `RecursionError` a deeply nested store raises), and every warning goes through
    `_warn` — but a handler that calls it unguarded is one future addition away
    from re-raising out of `main()`, and a bridge that dies with a traceback
    writes no JSON-RPC error, so the harness waits forever for an answer it will
    never get. Suppressing here costs a redaction key at worst; it never costs the
    frame.
    """
    try:
        return resolve_connection().key
    except Exception:  # the fault path must not raise
        return None


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

    # `follow_redirects=False` IS the security property, not a default: httpx
    # re-issues a cross-origin 307/308 with the SAME body and drops only
    # `Authorization`, so letting the client follow one delivers the JSON-RPC
    # payload and the session headers to whatever host the `Location` names
    # (GHSA-5h93-6whr-6q8j). `forward()` decides each hop itself — same-origin
    # 307/308 only — so the redirect the endpoint really needs (`/mcp` → `/mcp/`,
    # #2864) still works.
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError as exc:
                _emit({"jsonrpc": "2.0", "id": None,
                       "error": {"code": -32700,
                                 "message": f"Parse error: "
                                            f"{_redact_any(str(exc), connection.key)}"}})
                continue
            try:
                messages = forward(client, state, payload)
            except Exception as exc:
                # ANY internal fault is answered, not raised: a bridge that exits
                # on a bad frame leaves the harness waiting with no diagnosis, and
                # the exit is indistinguishable from a crash loop.
                # The START key and the LIVE one: a rotation between the
                # send and the fault means either could be in the text. The live
                # one comes from `_live_key()`, never from a bare
                # `resolve_connection()`: a fault handler that can itself fault
                # re-raises out of `main()`, and the frame it was supposed to
                # answer is answered with a traceback on stderr instead.
                detail = _redact_any(repr(exc), connection.key, _live_key())
                # REDACT FIRST, TRUNCATE SECOND — the sibling line below already
                # does. Slicing the raw stdin line first cut a credential that
                # straddled char 120 in half, and neither `redact` nor
                # `_redact_any` can match a value it can no longer see whole, so
                # up to `len(key) - 1` characters reached stderr.
                quoted = _redact_any(line.strip(), connection.key, _live_key())
                _log(f"internal failure forwarding {quoted[:120]!r}: "
                     f"{detail}")
                messages = _errors_for(
                    payload, -32603, f"MCP bridge internal failure: {detail}")
            for message in messages:
                _emit(message)
    _log("stdin closed — bridge exiting")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
