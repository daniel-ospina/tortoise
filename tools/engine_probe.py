#!/usr/bin/env python3
"""tools/engine_probe.py — tell "the container engine is wedged" apart from "the graph is down".

WHY THIS EXISTS
---------------
A wedged OrbStack/Docker control plane presents as a *graph* failure. The
symptom seen twice (2026-09-29 on #4844, 2026-10-03 on #7017) is:

    redis.exceptions.TimeoutError: Timeout reading from socket

which names Redis, points at the graph, and sends the diagnosis somewhere the
problem is not. The engine is what is stuck: `orb status` answers a trivial
request while every `docker` call blocks to its kill bound, and a bare
`docker ps` costs its full timeout before returning nothing.

The cost is the misdiagnosis, not just the wait. Two questions that look
identical from the graph error have different recoveries — restart the engine,
or fix the graph — and nothing in the repo could tell them apart before this
probe.

THE ANSWERS, AND WHY UNMEASURABLE IS ONE OF THEM
-----------------------------------------------
    ENGINE_WEDGED  — the socket exists, `docker version` did not answer inside
                     the bound. Everything that shells out to docker will hang,
                     so the graph's state is NOT MEASURABLE. Do not report
                     `GRAPH_DOWN` here: that is the defect this tool exists to
                     prevent.
    GRAPH_DOWN     — the engine answered (or no socket is configured, i.e.
                     embedded/remote use), and the graph port refused or timed
                     out. This is a graph problem.
    GRAPH_WEDGED   — the port ANSWERS (`+PONG`) but the graph ENGINE does not
                     complete a trivial read (`GRAPH.RO_QUERY <graph> "RETURN
                     1"`) inside the same bound. This is the #7235 signature:
                     measured 2026-10-04/05, PING returned True in 0.48 s while
                     `MATCH (n) RETURN count(n)` timed out. A PING-only probe
                     reports this state as GRAPH_UP — the false green this
                     verdict exists to remove. Recovery is an operator restart
                     of the instance, NOT of the client.
    GRAPH_UP       — a PING on the graph port returned PONG, the engine is not
                     wedged, AND — when the read was actually ISSUED — a real
                     read completed inside the bound. A credential-bearing URI
                     (`.env.example`) admits the probe, so the read IS issued
                     there. A `-NOAUTH` PING with no usable credential, an
                     `AUTH` the instance REFUSED, and an error reply to the
                     read (an unknown graph) stay green deliberately: the probe
                     could not be admitted, and a missing graph is not a wedge.
                     A graph name the caller NAMED that cannot be addressed is
                     UNMEASURABLE, never green — that read never verified it.

EVERY PROBE IS BOUNDED. A probe that can hang is not a probe: the 2026-10-03
occurrence cost 900s to a single unbounded `docker ps`. `--timeout` (default
3s) bounds EACH socket operation and the docker subprocess. It is not a total
budget: a connect and a read are bounded separately, and the engine probe adds
its own, so the worst case is a small multiple of `--timeout`, not one.

THE ENGINE VOCABULARY, AND WHY EACH STATE HAS ITS OWN RECOVERY
-------------------------------------------------------------
    ENGINE_OK          `docker version` answered. The graph result is
                       trustworthy.
    ENGINE_WEDGED      nothing came back inside the bound. Every docker call
                       will HANG, so a graph timeout cannot be attributed — the
                       graph's state is NOT MEASURABLE.
    ENGINE_ABSENT      no docker CLI and no engine socket: no engine is in play
                       (embedded/remote use). There was nothing to wedge, so
                       the graph result IS trustworthy.
    ENGINE_DAEMON_DOWN the CLI answered, with an error: docker will fail FAST
                       rather than hang (an unreachable daemon returns rc=1 in
                       ~0.1s), so the graph result is still trustworthy. The
                       recovery depends on the error, so the tool reports the
                       rc it measured rather than guessing at the cause.
    ENGINE_UNMEASURED  no CLI, but an engine socket EXISTS. Something is trying
                       to be an engine and cannot be probed; treat as wedged.

    UNMEASURABLE       the probe shapes themselves are unusable (a malformed
                       result). No confident verdict is possible.

    ENGINE_MEASURABLE — the states in which the graph result can be BELIEVED —
    is ENGINE_OK, ENGINE_ABSENT and ENGINE_DAEMON_DOWN. ENGINE_WEDGED and
    ENGINE_UNMEASURED are not in it, and neither is any status this module has
    never heard of: the test is an allow-list, so a new probe state cannot
    silently become a confident wrong answer.

USAGE
    uv run python tools/engine_probe.py            # human summary, exit 0/2/3
    uv run python tools/engine_probe.py --json     # machine-readable verdict
    uv run python tools/engine_probe.py --timeout 2
    uv run python tools/engine_probe.py --no-docker   # skip the engine probe

EXIT CONTRACT
    0  GRAPH_UP (and, when probed, the engine answered; and, when a graph name
       is known, a real read completed inside the bound — EXCEPT the named
       credential carve-outs where the instance refused admission to the
       probe: a `-NOAUTH` PING with no usable password, or an `AUTH` the
       instance rejected. The report names that skipped read; a caller that
       requires a VERIFIED read must read the report, not only the exit code)
    2  not usable: ENGINE_WEDGED, GRAPH_DOWN or GRAPH_WEDGED — a caller that
       must not proceed on a broken substrate can gate on this without parsing
       prose
    3  UNMEASURABLE: the probes returned nothing usable (a malformed result, a
       rediss:// target this plaintext probe cannot address, or a graph name
       the caller NAMED that cannot be addressed in the inline protocol, so
       the read was skipped). Distinct from 2 on purpose: "broken" and "could
       not tell" are different recoveries.

    1  is reserved for a rejected `--timeout` (must be > 0). Note that argparse
       itself exits 2 on a malformed argument (`--timeout abc`), so the two
       meanings share a code; callers that must distinguish them should read
       stderr, and the reserve exists for the case this file controls.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/engine_probe.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/engine_probe.py`"
    )

import argparse
import contextlib
import json
import math
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
#: The canonical graph port. NOT 6379 (`localhost` resolves `::1` first and a
#: second, near-empty FalkorDB has been observed there — #6666), and NOT a
#: hostname: `127.0.0.1` is what the map's connection string pins.
DEFAULT_PORT = 16379
DEFAULT_TIMEOUT = 3.0

#: OrbStack/Docker sockets. The engine is the thing that wedges; a socket that
#: exists but does not answer is the signature. The list covers the common
#: desktop and rootless layouts, because `docker_socket()` is the SOLE
#: discriminator between ENGINE_ABSENT (believable) and ENGINE_UNMEASURED
#: (treated as wedged) — missing a live socket there endorses a graph result that
#: may be attributable to a wedged engine.
DOCKER_SOCKETS = (
    Path.home() / ".orbstack" / "run" / "docker.sock",
    Path("/var/run/docker.sock"),
    Path.home() / ".docker" / "run" / "docker.sock",  # Docker Desktop
    Path.home() / ".colima" / "default" / "docker.sock",  # Colima
    Path("/run/user") / str(os.getuid()) / "docker.sock",  # rootless
)

ENGINE_WEDGED = "ENGINE_WEDGED"
GRAPH_DOWN = "GRAPH_DOWN"
GRAPH_UP = "GRAPH_UP"
#: The #7235 wedge (2026-10-04/05): the port ANSWERS a PING but the graph
#: ENGINE does not complete a trivial read inside the bound. Distinct from
#: GRAPH_DOWN on purpose — the substrate is not down — and distinct from
#: GRAPH_UP because a PING-only probe reports it as healthy. Measured: PING
#: returned `True (0.48 s)` while a single-node read and a `count(n)` both
#: timed out, for ~7 h, with no operator action in between.
GRAPH_WEDGED = "GRAPH_WEDGED"
ENGINE_OK = "ENGINE_OK"
ENGINE_ABSENT = "ENGINE_ABSENT"
ENGINE_DAEMON_DOWN = "ENGINE_DAEMON_DOWN"
ENGINE_UNMEASURED = "ENGINE_UNMEASURED"

#: The ONLY engine states in which the graph probe can be believed. An
#: allow-list, not an enumeration of the bad states: a status this module has
#: never heard of must not fall through to a confident verdict.
ENGINE_MEASURABLE = (ENGINE_OK, ENGINE_ABSENT, ENGINE_DAEMON_DOWN)

#: No confident verdict is possible. Reported instead of guessing.
UNMEASURABLE = "UNMEASURABLE"

#: WHY a real read was not issued — recorded in `read_error` on EVERY skip, so
#: the report can always say that the read did not happen. A skip with no trace
#: is indistinguishable from a completed read once only the exit code is read.
READ_NO_PING = "ping-failed"
READ_NO_GRAPH_NAME = "no-graph-name"
READ_PRE_AUTH_PING = "pre-auth-ping"
#: The caller NAMED a graph that cannot go into the inline protocol unmangled.
#: This one skip is a refusal to verify the graph the caller asked about, so
#: `classify` returns UNMEASURABLE for it — never the green a nameless read
#: keeps. A skipped read of the caller's own graph is the #7235 false green one
#: layer up, and `--graph 'bad name'` reaches it.
READ_UNADDRESSABLE = "unaddressable-graph-name"
#: The instance refused the AUTH the URI's credential was presented for. The
#: probe cannot be admitted, so the read is skipped — the documented pre-auth
#: carve-out, green. Recorded as the exact `read_error` so a consumer keying on
#: the constant MATCHES it (the instance's reply text is carried separately in
#: `read_reply`), and the report NAMES the refusal rather than leaving a read
#: that never happened looking like a completed one.
READ_AUTH_REFUSED = "auth-refused"


def _resolve_within_bound(host: str, port: int, timeout: float):
    """`getaddrinfo` inside the bound, or None on timeout.

    `socket.create_connection` resolves the name with NO timeout before any
    connect bound applies, so a slow or hanging resolver blew straight through
    `--timeout` — measured 5.05s elapsed for a 0.5s bound, 10x over — and
    "EVERY PROBE IS BOUNDED" is this tool's central promise. A resolver that
    hangs IS the wedged-substrate case the tool exists for, so it is bounded
    here rather than documented away. The worker is a daemon thread: a resolver
    call cannot be interrupted, and the point is to stop WAITING for it.
    """
    done = []

    def resolve():
        try:
            done.append(socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP))
        except Exception as exc:
            done.append(exc)

    worker = threading.Thread(target=resolve, daemon=True)
    worker.start()
    worker.join(timeout)
    if not done:
        return None
    if isinstance(done[0], BaseException):
        raise done[0]
    return done[0]


def probe_graph(host: str, port: int, timeout: float,
                graph: str | None = None,
                username: str | None = None,
                password: str | None = None) -> dict:
    """Bounded TCP connect + Redis PING, then a REAL graph read. Never raises.

    `ok` is True only when the endpoint SPEAKS REDIS — a `+PONG`, or any other
    protocol line. That second half matters: a server that answers
    `-NOAUTH Authentication required.` is demonstrably UP, and this repo's own
    canonical URI is credential-bearing (`.env.example`), so requiring `PONG`
    reported a reachable graph as GRAPH_DOWN — the misdiagnosis inverted.

    `graph` names the graph the READ is issued against. When it is given, a
    successful PING is NOT sufficient for `ok`: the probe then sends
    ``GRAPH.RO_QUERY <graph> "RETURN 1"`` on the SAME connection and records
    whether the engine COMPLETED it inside the same bound, as `read_ok`:

      * `True`  — a non-error reply came back inside the bound.
      * `False` — no reply inside the bound (or the peer closed without one).
                  This is #7235's wedge, and it makes `classify` return
                  GRAPH_WEDGED, never GRAPH_UP.
      * `None`  — no read was issued, so it verifies nothing: no graph name was
                  known, the graph name is not safely addressable in the inline
                  protocol, or the instance refused admission (a `-` PING that
                  no supplied credential could get past, or an `AUTH` the
                  instance rejected). `read_error` ALWAYS records which of
                  these it was, so a skipped read is visible in the report
                  rather than reading as a green. The unaddressable case is
                  UNMEASURABLE in `classify` — the caller named a graph that
                  was never read; a refused admission stays GRAPH_UP
                  deliberately. A server that refused the PING cannot be asked
                  to run a query without credentials, and an error reply can
                  never be read as a completed read.

    When the caller supplies a `password` (the URI carried one), a PING the
    auth gate refused (`-NOAUTH`) is followed by `AUTH`, and the read is issued
    only once the instance admits the probe. A REFUSED `AUTH` is the same
    documented pre-auth carve-out as no credential at all (green); an `AUTH`
    that gets NO answer is the #7235 wedge one command earlier. Without a
    credential the probe does not AUTH: it asks whether the substrate answers,
    not whether this client is authorised.

    A `read_ok` of False deliberately does NOT propagate to this function's
    `ok`/`error` fields: `ok=False` means "the port is unreachable" (recovery:
    the graph container), while "the port answers and the engine does not" has
    a different recovery (restart the instance). Collapsing them re-creates the
    misdiagnosis this tool exists to remove (#7235 vs GRAPH_DOWN).

    RO_QUERY, not QUERY, is deliberate: QUERY MINTS the graph when it does not
    exist (measured — a probe against a missing name created it and it appeared
    in `GRAPH.LIST`), so a diagnostic must not write; RO_QUERY answers
    `-ERR Invalid graph operation on empty key` and creates nothing.

    never raises: a connect that succeeds but yields no reply inside the bound is
    `ok=False` with `error='timeout'` — the shape a wedged engine produces, which
    must not be read as a successful probe. A non-finite timeout does NOT raise
    either: `Thread.join` rejects it inside `_resolve_within_bound` and the
    result is `ok=None` with a `resolve-failed` error. (`main` rejects those
    before calling, so `--timeout` cannot reach here as nan/inf.)
    """
    started = time.monotonic()
    sock = None
    try:
        # Resolution failures report `ok=None`, NOT `ok=False`. `ok=False`
        # asserts a measured endpoint that refused or timed out; none of these
        # touched the endpoint at all, so they must classify as UNMEASURABLE
        # rather than becoming a confident GRAPH_DOWN with prose that blames a
        # port the probe never dialled. `classify` already routes a non-bool
        # `ok` there.
        try:
            addrinfo = _resolve_within_bound(host, port, timeout)
        except Exception as exc:
            return {"ok": None, "reply": None,
                    "error": f"resolve-failed: {type(exc).__name__}: {exc}",
                    "elapsed_s": round(time.monotonic() - started, 3)}
        if addrinfo is None:
            return {"ok": None, "reply": None, "error": "resolve-timeout",
                    "elapsed_s": round(time.monotonic() - started, 3)}
        if not addrinfo:
            return {"ok": None, "reply": None, "error": "no-addresses",
                    "elapsed_s": round(time.monotonic() - started, 3)}
        # EVERY resolved address is tried, not just the first: a dual-stack host
        # whose AAAA is unreachable while its A answers would otherwise be
        # reported GRAPH_DOWN. And the socket is built from the family's
        # SOCKADDR — `create_connection` unpacks it as a 2-tuple, which an
        # AF_INET6 sockaddr (a 4-tuple) makes a ValueError, i.e. a crash instead
        # of a verdict exactly when an operator is diagnosing a wedge.
        last_error = None
        attempted = False
        for family, socktype, proto, _canon, sockaddr in addrinfo:
            try:
                sock = socket.socket(family, socktype, proto)
                sock.settimeout(timeout)
                # From here we are actually DIAL; a failure above this line
                # (fd exhaustion, a sandbox refusing the family) means no
                # address was ever tried, which is unmeasured, not a verdict.
                attempted = True
                sock.connect(sockaddr)
                break
            except OSError as exc:
                last_error = exc
                if sock is not None:
                    sock.close()
                sock = None
        else:
            detail = (f"{type(last_error).__name__}: {last_error}"
                      if last_error else "no-addresses")
            if not attempted:
                return {"ok": None, "reply": None, "error": detail,
                        "elapsed_s": round(time.monotonic() - started, 3)}
            # Every address was dialled and every one failed — a MEASURED
            # failure, so `ok=False` is right here.
            return {"ok": False, "reply": None, "error": detail,
                    "elapsed_s": round(time.monotonic() - started, 3)}
        sock.sendall(b"PING\r\n")
        reply = sock.recv(64)
        text = reply.decode("utf-8", "replace").strip()
        #: Redis replies are one of +, -, :, $, *. Anything else is some other
        #: service on the port — reachable, but not the graph.
        speaks_redis = bool(text) and text[0] in "+-:$*"
        ok = b"PONG" in reply or speaks_redis
        result = {
            "ok": ok,
            "reply": text,
            "error": None if ok else "unexpected-reply",
            "read_graph": graph,
            "read_ok": None,
            "read_reply": None,
            "read_error": None,
        }
        # The read is issued only when the PING itself was not an ERROR reply:
        # a `-NOAUTH` server has not admitted us, so it cannot be asked to run
        # a query, and a command-level error is not a completed read. EVERY
        # skip records WHY in `read_error` (never left None) so the report can
        # always name the skipped check.
        if not ok:
            result["read_error"] = READ_NO_PING
        elif graph is None:
            result["read_error"] = READ_NO_GRAPH_NAME
        elif not _addressable_graph_name(graph):
            # The caller's OWN name is unaddressable: the read could not verify
            # it whatever the credential state, so this outranks the pre-auth
            # skip — a caller that named an unreadable graph must not be told
            # "green" just because the instance also refused admission.
            result["read_error"] = READ_UNADDRESSABLE
        elif text.startswith("-"):
            # The auth gate refused the PING. The URI carried the caller's
            # credential, so being admitted is possible — and only an admitted
            # connection can say anything about the engine. A REFUSED AUTH is
            # the documented pre-auth carve-out (green — the probe cannot be
            # admitted); an AUTH that gets NO answer is the #7235 wedge one
            # command earlier, because the loop that just answered the PING
            # stopped answering.
            if not password:
                result["read_error"] = READ_PRE_AUTH_PING
            else:
                auth = _authenticate(sock, username, password)
                if auth["ok"]:
                    result.update(_probe_graph_read(sock, graph))
                elif auth["answered"]:
                    # The refusal code is the CONSTANT, so a consumer can match
                    # `read_error == READ_AUTH_REFUSED`; the instance's own reply
                    # text rides in `read_reply` and is shown in the report.
                    result["read_error"] = auth["error"]
                    result["read_reply"] = auth.get("reply")
                else:
                    result["read_ok"] = False
                    result["read_error"] = auth["error"]
        else:
            result.update(_probe_graph_read(sock, graph))
        result["elapsed_s"] = round(time.monotonic() - started, 3)
        return result
    except TimeoutError:
        return {"ok": False, "reply": None, "error": "timeout",
                "elapsed_s": round(time.monotonic() - started, 3)}
    except OSError as exc:
        return {"ok": False, "reply": None, "error": type(exc).__name__,
                "elapsed_s": round(time.monotonic() - started, 3)}
    except Exception as exc:
        # An exception before any connect is an UNMEASURED endpoint: `ok=None`,
        # not a verdict. `ok=False` would become a confident GRAPH_DOWN for an
        # endpoint that was never dialled. Reporting a cause the probe did not
        # observe is the defect this tool exists to remove, and crashing out of a
        # diagnostic tool is worse: the operator gets no verdict at all.
        return {"ok": None, "reply": None,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_s": round(time.monotonic() - started, 3)}
    finally:
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.close()


def _addressable_graph_name(graph: str) -> bool:
    """Can `graph` go into the inline `GRAPH.RO_QUERY` command unmangled?

    The inline Redis protocol splits the command on whitespace and treats `"`,
    `'` AND `\\` specially (`sdssplitargs` quotes on BOTH quote characters), so
    a name carrying any of those would silently address a DIFFERENT graph (or a
    second command) rather than the one the URI named. A probe must not invent
    an endpoint, so an unaddressable name skips the read
    (`read_error=READ_UNADDRESSABLE`, i.e. unverified) instead of guessing —
    and `classify` reports that skip as UNMEASURABLE, never GRAPH_UP, because
    the graph the caller NAMED was not verified.

    The apostrophe was MISSED here once, and the miss re-created the #7235
    false green one layer down: `--graph "it's"` is a protocol error (the
    unclosed quote rejects the whole request), the reply's `-ERR` is read as
    an `unverified:` reply, and the probe certified exit 0 for a graph it never
    read. A fully-quoted name (`--graph "'team_graph'"`) is worse the same way:
    the engine STRIPS the quotes, so the probe would read the different graph
    `team_graph` and certify it with a COMPLETED read of a name never asked
    for. Both are unaddressable and must be treated as such.
    """
    return bool(graph) and not any(
        ch.isspace() or ch in '"\'\\' for ch in graph
    )


def _authenticate(sock: socket.socket, username: str | None,
                  password: str) -> dict:
    """Send `AUTH` on an already-connected socket. Never raises.

    RESP framing (`*N\\r\\n$<len>\\r\\n<field>\\r\\n`) rather than the inline
    protocol the PING and the read use: a password may contain whitespace,
    quotes or backslashes, and the inline protocol would split or re-parse it
    into a DIFFERENT credential. The exact bytes the caller's URI carried are
    the bytes presented to the instance.

    ``answered`` separates "the instance REFUSED us" (an error reply — the
    documented pre-auth carve-out, green) from "the instance did not answer"
    (a timeout or a bare close — the #7235 wedge signature, never green). A
    refusal is not a completed command, and silence is not a refusal.
    """
    fields = [b"AUTH"]
    if username:
        fields.append(username.encode())
    fields.append(password.encode())
    payload = (b"*%d\r\n" % len(fields)
               + b"".join(f"${len(f)}\r\n".encode() + f + b"\r\n"
                          for f in fields))
    try:
        sock.sendall(payload)
        reply = sock.recv(256)
    except TimeoutError:
        return {"ok": False, "answered": False, "reply": None,
                "error": "auth-timeout"}
    except OSError as exc:
        return {"ok": False, "answered": False, "reply": None,
                "error": type(exc).__name__}
    text = reply.decode("utf-8", "replace").strip()
    if not text:
        return {"ok": False, "answered": False, "reply": None,
                "error": "empty-reply"}
    if text[0] == "+":
        return {"ok": True, "answered": True, "reply": text[:160],
                "error": None}
    # `error` is the CONSTANT (so `read_error == READ_AUTH_REFUSED` is a match a
    # consumer can key on); the instance's reply text is `reply`, carried
    # separately rather than smuggled into the machine-readable code.
    return {"ok": False, "answered": True, "reply": text[:160],
            "error": READ_AUTH_REFUSED}


def _probe_graph_read(sock: socket.socket, graph: str) -> dict:
    """Issue ONE real graph read on an already-connected socket.

    Returns only ``read_ok`` / ``read_reply`` / ``read_error`` — never a
    verdict, and never raising. The read's timeout is handled HERE rather than
    by `probe_graph`'s outer handler on purpose: that handler reports
    `ok=False` ("the port is unreachable"), which is the wrong recovery for a
    port that just answered a PING.
    """
    try:
        sock.sendall(f'GRAPH.RO_QUERY {graph} "RETURN 1"\r\n'.encode())
        reply = sock.recv(4096)
    except TimeoutError:
        return {"read_ok": False, "read_reply": None,
                "read_error": "read-timeout"}
    except OSError as exc:
        return {"read_ok": False, "read_reply": None,
                "read_error": type(exc).__name__}
    text = reply.decode("utf-8", "replace").strip()
    if not text:
        # The peer closed without answering the read. Same meaning as a
        # timeout: the engine did not complete the command.
        return {"read_ok": False, "read_reply": None,
                "read_error": "empty-reply"}
    if text[0] == "-":
        # It ANSWERED — just not with a result. `-NOAUTH`, `-ERR Invalid graph
        # operation on empty key` (an unknown graph), and `-ERR unknown
        # command` (a plain Redis) all prove the command loop is running but
        # verify nothing about this graph's engine. `None`, never True: reading
        # an error reply as a completed read is a green with no read behind it.
        return {"read_ok": None, "read_reply": text[:160],
                "read_error": f"unverified:{text[:80]}"}
    return {"read_ok": True, "read_reply": text[:160], "read_error": None}


def docker_socket() -> Path | None:
    """The first configured engine socket that exists, else None.

    `DOCKER_HOST` is consulted first: a `unix://` DOCKER_HOST names the socket
    the CLI would actually use, and ignoring it made `ENGINE_ABSENT` ("no engine
    is in play") assert something the environment contradicts.
    """
    docker_host = os.environ.get("DOCKER_HOST", "")
    if docker_host.startswith("unix://"):
        path = Path(docker_host[len("unix://"):])
        if path.exists():
            return path
    for path in DOCKER_SOCKETS:
        if path.exists():
            return path
    return None


def probe_engine(timeout: float) -> dict:
    """Bounded `docker version`. The subprocess timeout IS the probe.

    Without the bound this call hangs for the full kill interval when the
    control plane is wedged, which is exactly the 900s burn this tool removes.

    The failure modes are kept DISTINCT, because each has a different recovery
    and a different meaning for the graph result: a timeout means callers hang
    (unmeasurable), a nonzero rc means the daemon is down and callers fail fast
    (measurable), and a missing CLI means there is no engine at all — unless a
    socket exists, in which case something is trying to be one and cannot be
    probed (unmeasurable).
    """
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": ENGINE_WEDGED, "detail": f"no answer in {timeout}s",
                "elapsed_s": round(time.monotonic() - started, 3)}
    except FileNotFoundError:
        sock = docker_socket()
        if sock is not None:
            return {"status": ENGINE_UNMEASURED,
                    "detail": f"no docker CLI, but an engine socket exists at {sock}",
                    "elapsed_s": round(time.monotonic() - started, 3)}
        return {"status": ENGINE_ABSENT,
                "detail": "no docker CLI and no engine socket — no engine in play",
                "elapsed_s": round(time.monotonic() - started, 3)}
    except OSError as exc:
        return {"status": ENGINE_WEDGED, "detail": f"{type(exc).__name__}: {exc}",
                "elapsed_s": round(time.monotonic() - started, 3)}
    elapsed = round(time.monotonic() - started, 3)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:160]
        return {"status": ENGINE_DAEMON_DOWN,
                "detail": f"rc={proc.returncode} {stderr}", "elapsed_s": elapsed}
    return {"status": ENGINE_OK, "detail": (proc.stdout or "").strip(),
            "elapsed_s": elapsed}


def classify(graph: dict, engine: dict | None) -> str:
    """The verdict. Pure, so the answers are testable without a machine.

    Order matters: a wedged engine makes the graph's state UNMEASURABLE, so it
    is reported as such rather than as GRAPH_DOWN. That inversion is the whole
    point of this tool — see the module docstring.

    BOTH sides are allow-listed. The engine test accepts only the states that
    mean "the graph's state is measurable" (ENGINE_MEASURABLE), so a wedged
    engine, an unprobeable one, or a status this module has never heard of all
    report ENGINE_WEDGED rather than falling through to a confident verdict —
    enumerating the *bad* states instead is how a shape nobody anticipated
    becomes the misdiagnosis this tool exists to remove. The graph side is
    symmetric: `ok` must be a real bool, and a malformed probe result yields
    UNMEASURABLE rather than a confident GRAPH_DOWN.
    """
    if not isinstance(graph.get("ok"), bool):
        return UNMEASURABLE
    if engine is not None and engine.get("status") not in ENGINE_MEASURABLE:
        return ENGINE_WEDGED
    # The port answered but the engine did not complete a read: NOT healthy.
    # This branch is what makes a PING-only "is it back yet?" poll wrong — see
    # #7235, where PING kept returning True while every read timed out.
    if graph["ok"] and graph.get("read_ok") is False:
        return GRAPH_WEDGED
    # A read the probe REFUSED to issue because the caller's own graph name is
    # not safely addressable verifies nothing about that graph, so it is
    # UNMEASURABLE — NOT the green a nameless probe keeps. Leaving it green is
    # the #7235 false green reachable through `--graph 'bad name'` (measured:
    # PING `+PONG` -> `GRAPH_UP`/exit 0 with no read line and no trace that the
    # graph the caller named was never read). Keyed on the ONE skip reason that
    # names a graph the caller asked about: `-NOAUTH` and an error reply stay
    # green deliberately (see `probe_graph`).
    if (graph.get("read_ok") is None
            and graph.get("read_error") == READ_UNADDRESSABLE):
        return UNMEASURABLE
    return GRAPH_UP if graph["ok"] else GRAPH_DOWN


def graph_target_from_env() -> dict | None:
    """The resolved graph endpoint from TORTOISE_DB_URI, or None when UNSET.

    None means exactly one thing: no URI is configured, so the caller's own
    defaults apply. Every OTHER state — an unsupported scheme, an embedded
    path, a malformed or out-of-range port — raises ValueError, and the caller
    must refuse rather than probe somewhere else. Collapsing those four states
    into None was a real defect: `main` read them all as "no URI" and probed the
    DEFAULT endpoint, so a `rediss://` target silently bypassed its own TLS
    refusal, a portless URI lost its host, and a typo'd port produced exit 0
    against a graph the tool never reached (a false green — the one direction a
    caller gating on the exit code cannot detect).

    The parse is `tortoise.projection.resolve_db_endpoint`, the ONE canonical
    URI -> endpoint derivation (documented "so a backup can never dial a
    different instance than the product it is backing up"), imported lazily so
    this module's probes stay stdlib-only. Hand-rolling it here is how the
    caller and the product drift apart.
    """
    uri = os.environ.get("TORTOISE_DB_URI", "").strip()
    if not uri:
        return None
    from tortoise.projection import resolve_db_endpoint
    ep = resolve_db_endpoint(uri)
    # #6666: `localhost` resolves `::1` FIRST, and a second, near-empty
    # FalkorDB has been observed there. The canonical instance is 127.0.0.1.
    host = "127.0.0.1" if ep.host in ("localhost", "::1") else ep.host
    return {"host": host, "port": ep.port, "ssl": ep.ssl, "uri": uri,
            "username": ep.username, "password": ep.password,
            "graph_name": ep.graph_name}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=None,
                    help=f"graph host (default {DEFAULT_HOST}, or TORTOISE_DB_URI's)")
    ap.add_argument("--port", type=int, default=None,
                    help=f"graph port (default {DEFAULT_PORT}, or TORTOISE_DB_URI's)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"bound for EVERY probe (default {DEFAULT_TIMEOUT}s)")
    ap.add_argument("--graph", default=None,
                    help="graph name the real-read check queries (default: "
                         "TORTOISE_DB_URI's path, else `tortoise`)")
    ap.add_argument("--no-docker", action="store_true",
                    help="skip the engine probe (graph reachability only)")
    ap.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    args = ap.parse_args(argv)

    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print("engine_probe: --timeout must be a finite value > 0", file=sys.stderr)
        return 1

    # The URI supplies host AND port; the flags override it field by field, so
    # `--port` alone still probes the URI's host and vice versa. Both flags
    # default to None so "not given" is distinguishable from "given the default".
    try:
        target = graph_target_from_env()
    except (ValueError, ImportError) as exc:
        # Set but unresolvable (or the resolver itself is not importable).
        # Probing DEFAULT_HOST instead is how a typo'd port or a TLS URI came
        # back exit 0 against a graph the tool never reached — but a stale URI
        # must NOT veto an endpoint the caller named explicitly.
        if args.host is not None and args.port is not None:
            target = None
        else:
            return _refuse(args, "unresolvable-uri", exc,
                           "TORTOISE_DB_URI cannot be resolved, so refusing to "
                           "probe the default endpoint instead. Pass --host AND "
                           "--port to probe an endpoint explicitly.")

    # The refusal is about a URI-supplied endpoint that cannot be spoken to. A
    # caller asks for a plaintext endpoint only when it names BOTH fields: with
    # one flag the other still comes from the TLS URI, and probing that mixture
    # produced a confident GRAPH_DOWN for an endpoint the tool says it cannot
    # address.
    flags_override = args.host is not None and args.port is not None
    if target is not None and target["ssl"] and not flags_override:
        return _refuse(args, "tls-not-probed",
                       f"TORTOISE_DB_URI names a rediss:// (TLS) target "
                       f"({target['host']}:{target['port']})",
                       "This probe speaks plaintext only, so it cannot tell you "
                       "whether that graph is up. Pass --host AND --port to "
                       "probe a plaintext endpoint explicitly.",
                       target["host"], target["port"])

    env_host = target["host"] if target else DEFAULT_HOST
    env_port = target["port"] if target else DEFAULT_PORT
    host = args.host if args.host is not None else env_host
    port = args.port if args.port is not None else env_port

    # The read names the graph the URI named (its path), defaulting to the
    # canonical `tortoise` — the same assumption the DEFAULT_PORT already makes.
    # An explicit `--graph` is ALWAYS used, including an EMPTY one: the trailing
    # `or "tortoise"` this replaces swallowed `--graph ''` (and the URI path
    # with it), so `--graph "$UNSET_VAR"` silently read `tortoise` — a name
    # neither the URI nor the flag named — and certified it GRAPH_UP. An empty
    # flag now reaches `_addressable_graph_name("")` → UNMEASURABLE, and the
    # hard-coded default applies ONLY when nothing named a graph at all.
    if args.graph is not None:
        graph_name = args.graph
    elif target is not None:
        graph_name = target["graph_name"] or "tortoise"
    else:
        graph_name = "tortoise"

    # The URI's credential admits the probe to the read leg, so a
    # credential-bearing instance (`.env.example`) is actually READ rather than
    # reported green off a PING (#7235). It is presented ONLY to the endpoint
    # the URI names: a `--host`/`--port` override points at a DIFFERENT
    # endpoint, and sending the URI's password there would leak a credential to
    # a host the caller never configured it for.
    username = password = None
    if target is not None and host == target["host"] and port == target["port"]:
        username, password = target["username"], target["password"]

    graph = probe_graph(host, port, args.timeout, graph=graph_name,
                        username=username, password=password)
    engine = None if args.no_docker else probe_engine(args.timeout)
    verdict = classify(graph, engine)
    # A non-bool `ok` means the endpoint was never reached, so the report must
    # not claim it was probed.
    _report(args, verdict, graph, host, port, engine,
            probed=graph.get("ok") is not None)
    if verdict == GRAPH_UP:
        return 0
    if verdict == UNMEASURABLE:
        return 3
    return 2


def _refuse(args, error: str, exc, message: str,
            host: str | None = None, port: int | None = None) -> int:
    """Report a refusal through `_report` so `--json` still yields JSON, and
    return the UNMEASURABLE code. "Could not tell" is not "broken".

    `host`/`port` are the endpoint the URI NAMED, when one is known — the
    default is a placeholder for a probe that never happened and must not be
    read as "this is the endpoint that failed".
    """
    print(f"engine_probe: {exc}; {message}", file=sys.stderr)
    _report(args, UNMEASURABLE,
            # `ok=None`, not False: this endpoint was never reached either, and
            # `ok=False` is reserved for a MEASURED failure (every address
            # dialled and refused). A consumer reading `graph.ok` must not be
            # told "measured down" for a refusal.
            {"ok": None, "reply": None, "error": error, "elapsed_s": 0.0},
            host or "(not probed)", port if port is not None else 0, None,
            probed=False)
    return 3


def _report(args, verdict: str, graph: dict, host: str, port: int,
            engine: dict | None, probed: bool = True) -> None:
    """Print the human summary or the JSON for a probe result.

    Every exit path AFTER argument validation reports through here — including
    the refusals in `main` — so `--json` cannot emit nothing on one path while
    the others emit JSON. (A rejected `--timeout` is a usage error and returns
    before this.) `probed` says whether an endpoint was actually contacted, so
    a consumer can tell "we looked and it was down" from "we never looked".
    """
    sock = docker_socket()
    result = {
        "verdict": verdict,
        "graph": {"host": host, "port": port, "probed": probed, **graph},
        "engine": engine,
        "docker_socket": str(sock) if sock else None,
        "timeout_s": args.timeout,
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return
    print(f"graph  {host}:{port} -> "
          f"{graph.get('reply') or graph.get('error') or graph.get('ok')} "
          f"({graph.get('elapsed_s')}s)")
    # ALWAYS surface the read leg, including a skipped one. A probe that
    # verified nothing must SAY so — a skip that prints no trace is
    # indistinguishable from a completed read to anyone reading the output, and
    # the exit code alone cannot tell them apart.
    if graph.get("read_graph") or graph.get("read_error"):
        read_ok = graph.get("read_ok")
        if read_ok is True:
            outcome = "completed"
        elif read_ok is False:
            outcome = graph.get("read_error") or "no reply"
        else:
            reason = graph.get("read_error") or "no read issued"
            # The refusal's machine code is the constant READ_AUTH_REFUSED;
            # the instance's own reply text rides in `read_reply`, so a human
            # still sees WHY admission was refused (`-WRONGPASS …`) rather
            # than a bare code.
            if reason == READ_AUTH_REFUSED and graph.get("read_reply"):
                reason = f"{reason}: {graph['read_reply']}"
            outcome = f"skipped ({reason})"
        target = graph.get("read_graph") or "(no graph name)"
        print(f"read   GRAPH.RO_QUERY {target} "
              f'"RETURN 1" -> {outcome}')
    if engine is None:
        print("engine not probed"
              + (" (--no-docker)" if args.no_docker
                 else " (the probe was refused before the engine was reached)"))
    else:
        print(f"engine -> {engine.get('status')} "
              f"({engine.get('detail')}) ({engine.get('elapsed_s')}s)"
              + (f" socket={sock}" if sock else ""))
    print(f"VERDICT: {verdict}")
    if verdict == ENGINE_WEDGED:
        # Word this from the MEASURED detail, never from an asserted mechanism:
        # a timeout, a missing CLI with a live socket, an OSError and an
        # unrecognised status all arrive here, and only one of them "did not
        # answer inside the bound". Asserting a cause the probe did not observe
        # is the misdiagnosis this tool exists to remove.
        cause = (engine or {}).get("detail", "unknown")
        print(f"  The container engine is not usable ({cause}), so a graph "
              f"result cannot be\n  trusted here. Every docker call may hang, "
              f"and a graph timeout cannot be\n  attributed — this is not "
              f"necessarily a graph failure. Do NOT stand up a\n  second graph "
              f"instance and do NOT point at the hosted API (see #7017).")
    elif verdict == GRAPH_WEDGED:
        # Word this from the MEASURED read state, never from an asserted cause:
        # the only thing observed is that a PING came back and a read did not.
        print("  The port answered a PING, but the graph engine did not complete "
              "a read\n"
              f"  (GRAPH.RO_QUERY {graph.get('read_graph')} \"RETURN 1\" -> "
              f"{graph.get('read_error') or 'no reply'}). A PING is NOT a health "
              "check\n  on this engine: during the #7235 wedge it kept answering "
              "while every read\n  timed out. The graph is not usable — do not "
              "retry a write against it, and\n  do not stand up a second "
              "instance. Recovery is an operator restart of the\n  FalkorDB "
              "instance.")
    elif verdict == UNMEASURABLE:
        print("  The probes did not return usable results, so no verdict is "
              "possible. Re-read the\n  raw output above rather than acting on "
              "a guess.")
    elif verdict == GRAPH_DOWN:
        if engine is None:
            print("  The graph port is unreachable and the engine was NOT "
                  "probed (--no-docker),\n  so a wedged engine cannot be "
                  "ruled out. Re-run without --no-docker before\n  treating "
                  "this as a graph problem.")
        elif engine.get("status") == ENGINE_ABSENT:
            print("  No engine is in play (no docker CLI and no engine socket), "
                  "so the graph port\n  itself is unreachable. Check the graph "
                  "container and the canonical instance\n  (127.0.0.1, never "
                  "`localhost` — #6666).")
        elif engine.get("status") == ENGINE_DAEMON_DOWN:
            # Only the MEASURED thing: nonzero rc covers permission-denied,
            # TLS/context errors and plugin failures as well as a stopped
            # daemon, and asserting "not running" would name a cause the probe
            # never observed (with `docker start` as the wrong recovery).
            print(f"  The docker CLI answered with an error "
                  f"({engine.get('detail')}), so docker calls fail fast rather\n"
                  f"  than hang and the graph port itself is unreachable. Read "
                  f"the error above for\n  the cause — it is not necessarily a "
                  f"stopped daemon.")
        else:
            print("  The engine answered, so the graph port itself is "
                  "unreachable. Check the\n  graph container and the "
                  "canonical instance (127.0.0.1, never `localhost` — #6666).")
    if probed and host not in ("127.0.0.1", "localhost") and verdict != GRAPH_UP:
        print("  NOTE: this probed a non-loopback host; the #6666 warning about "
              "`localhost` does not apply.")


if __name__ == "__main__":
    sys.exit(main())
