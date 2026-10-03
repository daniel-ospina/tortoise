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

THE THREE ANSWERS, AND WHY UNMEASURABLE IS ONE OF THEM
-----------------------------------------------------
    ENGINE_WEDGED  — the socket exists, `docker version` did not answer inside
                     the bound. Everything that shells out to docker will hang,
                     so the graph's state is NOT MEASURABLE. Do not report
                     `GRAPH_DOWN` here: that is the defect this tool exists to
                     prevent.
    GRAPH_DOWN     — the engine answered (or no socket is configured, i.e.
                     embedded/remote use), and the graph port refused or timed
                     out. This is a graph problem.
    GRAPH_UP       — a PING on the graph port returned PONG and the engine is
                     not wedged.

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
    python3 tools/engine_probe.py            # human summary, exit 0/2/3
    python3 tools/engine_probe.py --json     # machine-readable verdict
    python3 tools/engine_probe.py --timeout 2
    python3 tools/engine_probe.py --no-docker   # skip the engine probe

EXIT CONTRACT
    0  GRAPH_UP (and, when probed, the engine answered)
    2  not usable: ENGINE_WEDGED or GRAPH_DOWN — a caller that must not proceed
       on a broken substrate can gate on this without parsing prose
    3  UNMEASURABLE: the probes returned nothing usable (a malformed result, or
       a rediss:// target this plaintext probe cannot address). Distinct from 2
       on purpose: "broken" and "could not tell" are different recoveries.

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
import json
import math
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

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
        except Exception as exc:  # noqa: BLE001 — the worker must always report
            done.append(exc)

    worker = threading.Thread(target=resolve, daemon=True)
    worker.start()
    worker.join(timeout)
    if not done:
        return None
    if isinstance(done[0], BaseException):
        raise done[0]
    return done[0]


def probe_graph(host: str, port: int, timeout: float) -> dict:
    """Bounded TCP connect + Redis PING. Never raises; always returns a dict.

    `ok` is True only when the endpoint SPEAKS REDIS — a `+PONG`, or any other
    protocol line. That second half matters: a server that answers
    `-NOAUTH Authentication required.` is demonstrably UP, and this repo's own
    canonical URI is credential-bearing (`.env.example`), so requiring `PONG`
    reported a reachable graph as GRAPH_DOWN — the misdiagnosis inverted. The
    probe does not AUTH: it is asking whether the substrate answers, not whether
    this client is authorised.

    Never raises ON A REACHABLE-then-silent endpoint: a connect that succeeds but
    yields no reply inside the bound is `ok=False` with `error='timeout'` — the
    shape a wedged engine produces, which must not be read as a successful probe.
    (A caller that passes a non-finite timeout still gets a ValueError from the
    socket layer; `main` rejects those before calling.)
    """
    started = time.monotonic()
    sock = None
    try:
        addrinfo = _resolve_within_bound(host, port, timeout)
        if addrinfo is None:
            return {"ok": False, "reply": None, "error": "resolve-timeout",
                    "elapsed_s": round(time.monotonic() - started, 3)}
        if not addrinfo:
            return {"ok": False, "reply": None, "error": "no-addresses",
                    "elapsed_s": round(time.monotonic() - started, 3)}
        # EVERY resolved address is tried, not just the first: a dual-stack host
        # whose AAAA is unreachable while its A answers would otherwise be
        # reported GRAPH_DOWN. And the socket is built from the family's
        # SOCKADDR — `create_connection` unpacks it as a 2-tuple, which an
        # AF_INET6 sockaddr (a 4-tuple) makes a ValueError, i.e. a crash instead
        # of a verdict exactly when an operator is diagnosing a wedge.
        last_error = None
        for family, socktype, proto, _canon, sockaddr in addrinfo:
            try:
                sock = socket.socket(family, socktype, proto)
                sock.settimeout(timeout)
                sock.connect(sockaddr)
                break
            except OSError as exc:
                last_error = exc
                if sock is not None:
                    sock.close()
                sock = None
        else:
            return {"ok": False, "reply": None,
                    "error": type(last_error).__name__ if last_error else "no-addresses",
                    "elapsed_s": round(time.monotonic() - started, 3)}
        sock.sendall(b"PING\r\n")
        reply = sock.recv(64)
        elapsed = time.monotonic() - started
        text = reply.decode("utf-8", "replace").strip()
        #: Redis replies are one of +, -, :, $, *. Anything else is some other
        #: service on the port — reachable, but not the graph.
        speaks_redis = bool(text) and text[0] in "+-:$*"
        ok = b"PONG" in reply or speaks_redis
        return {
            "ok": ok,
            "reply": text,
            "error": None if ok else "unexpected-reply",
            "elapsed_s": round(elapsed, 3),
        }
    except socket.timeout:
        return {"ok": False, "reply": None, "error": "timeout",
                "elapsed_s": round(time.monotonic() - started, 3)}
    except OSError as exc:
        return {"ok": False, "reply": None, "error": type(exc).__name__,
                "elapsed_s": round(time.monotonic() - started, 3)}
    except Exception as exc:  # noqa: BLE001 — "Never raises" is the contract
        # A resolver that raises something unexpected is an UNMEASURED endpoint,
        # not a timeout and not a verdict. Reporting it as a cause the probe did
        # not observe is the defect this whole tool exists to remove, and
        # crashing out of a diagnostic tool is worse: the operator gets no
        # verdict at all.
        return {"ok": False, "reply": None,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_s": round(time.monotonic() - started, 3)}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def docker_socket() -> Optional[Path]:
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


def classify(graph: dict, engine: Optional[dict]) -> str:
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
    return GRAPH_UP if graph["ok"] else GRAPH_DOWN


def graph_target_from_env() -> Optional[dict]:
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
    return {"host": host, "port": ep.port, "ssl": ep.ssl, "uri": uri}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=None,
                    help=f"graph host (default {DEFAULT_HOST}, or TORTOISE_DB_URI's)")
    ap.add_argument("--port", type=int, default=None,
                    help=f"graph port (default {DEFAULT_PORT}, or TORTOISE_DB_URI's)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"bound for EVERY probe (default {DEFAULT_TIMEOUT}s)")
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

    graph = probe_graph(host, port, args.timeout)
    engine = None if args.no_docker else probe_engine(args.timeout)
    verdict = classify(graph, engine)
    _report(args, verdict, graph, host, port, engine)
    if verdict == GRAPH_UP:
        return 0
    if verdict == UNMEASURABLE:
        return 3
    return 2


def _refuse(args, error: str, exc, message: str,
            host: Optional[str] = None, port: Optional[int] = None) -> int:
    """Report a refusal through `_report` so `--json` still yields JSON, and
    return the UNMEASURABLE code. "Could not tell" is not "broken".

    `host`/`port` are the endpoint the URI NAMED, when one is known — the
    default is a placeholder for a probe that never happened and must not be
    read as "this is the endpoint that failed".
    """
    print(f"engine_probe: {exc}; {message}", file=sys.stderr)
    _report(args, UNMEASURABLE,
            {"ok": False, "reply": None, "error": error, "elapsed_s": 0.0},
            host or "(not probed)", port if port is not None else 0, None,
            probed=False)
    return 3


def _report(args, verdict: str, graph: dict, host: str, port: int,
            engine: Optional[dict], probed: bool = True) -> None:
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
    if engine is None:
        print("engine not probed (--no-docker)")
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
