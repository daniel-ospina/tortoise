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
    ENGINE_DAEMON_DOWN the CLI answered, with an error: the daemon is not
                       running. Docker calls FAIL FAST rather than hang, so the
                       graph result is trustworthy — and the recovery is
                       `docker start`, not "restart the engine".
    ENGINE_UNMEASURED  no CLI, but an engine socket EXISTS. Something is trying
                       to be an engine and cannot be probed; treat as wedged.

    UNMEASURABLE       the probe shapes themselves are unusable (a malformed
                       result). No confident verdict is possible.

The first three and ENGINE_DAEMON_DOWN are the states in which the graph probe
can be believed. Anything else — including a status this module has never heard
of, which is how a new probe state silently becomes a confident wrong answer —
is treated as unmeasurable.

USAGE
    python3 tools/engine_probe.py            # human summary, exit 0/2
    python3 tools/engine_probe.py --json     # machine-readable verdict
    python3 tools/engine_probe.py --timeout 2
    python3 tools/engine_probe.py --no-docker   # skip the engine probe

EXIT CONTRACT
    0  GRAPH_UP (and, when probed, the engine answered)
    2  not usable: ENGINE_WEDGED or GRAPH_DOWN — a caller that must not proceed
       on a broken substrate can gate on this without parsing prose

    1  is reserved for a rejected `--timeout` (must be > 0). Note that argparse
       itself exits 2 on a malformed argument (`--timeout abc`), so the two
       meanings share a code; callers that must distinguish them should read
       stderr, and the reserve exists for the case this file controls.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

DEFAULT_HOST = "127.0.0.1"
#: The canonical graph port. NOT 6379 (`localhost` resolves `::1` first and a
#: second, near-empty FalkorDB has been observed there — #6666), and NOT a
#: hostname: `127.0.0.1` is what the map's connection string pins.
DEFAULT_PORT = 16379
DEFAULT_TIMEOUT = 3.0

#: OrbStack/Docker socket. The engine is the thing that wedges; a socket that
#: exists but does not answer is the signature.
DOCKER_SOCKETS = (
    Path.home() / ".orbstack" / "run" / "docker.sock",
    Path("/var/run/docker.sock"),
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


def probe_graph(host: str, port: int, timeout: float) -> dict:
    """Bounded TCP connect + Redis PING. Never raises; always returns a dict.

    `ok` is True only on a real PONG — a connect that succeeds but yields no
    reply inside the bound is `ok=False` with `error='read-timeout'`, which is
    the shape the wedged-engine case produces and which must not be read as a
    successful probe.
    """
    started = time.monotonic()
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        sock.sendall(b"PING\r\n")
        reply = sock.recv(64)
        elapsed = time.monotonic() - started
        ok = b"PONG" in reply
        return {
            "ok": ok,
            "reply": reply.decode("utf-8", "replace").strip(),
            "error": None if ok else "unexpected-reply",
            "elapsed_s": round(elapsed, 3),
        }
    except socket.timeout:
        return {"ok": False, "reply": None, "error": "timeout",
                "elapsed_s": round(time.monotonic() - started, 3)}
    except OSError as exc:
        return {"ok": False, "reply": None, "error": type(exc).__name__,
                "elapsed_s": round(time.monotonic() - started, 3)}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def docker_socket() -> Optional[Path]:
    """The first configured engine socket that exists, else None."""
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


def redis_port_from_env() -> Optional[int]:
    """The graph port from TORTOISE_DB_URI, when it names a docker:// target.

    Parsed with `urlsplit` rather than a `:` split, because a colon in the URI
    need not be the port separator: `docker://:pw@::1/tortoise` has an IPv6
    host and NO port, and `rpartition(":")` would read the last `1` of `::1` as
    a port. `.port` returns None for a missing port and raises for a malformed
    one, so a typo yields None instead of a confident wrong number; the caller
    then falls back to DEFAULT_PORT, whose own hazard (`::1` hosts a second,
    near-empty FalkorDB — #6666) is why this probe defaults to `127.0.0.1`.
    """
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if not uri.startswith("docker://"):
        return None
    try:
        return urlsplit(uri).port
    except ValueError:
        return None


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=None,
                    help=f"graph port (default {DEFAULT_PORT}, or TORTOISE_DB_URI's)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"bound for EVERY probe (default {DEFAULT_TIMEOUT}s)")
    ap.add_argument("--no-docker", action="store_true",
                    help="skip the engine probe (graph reachability only)")
    ap.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    args = ap.parse_args(argv)

    if args.timeout <= 0:
        print("engine_probe: --timeout must be > 0", file=sys.stderr)
        return 1

    port = args.port if args.port is not None else (
        redis_port_from_env() or DEFAULT_PORT)
    graph = probe_graph(args.host, port, args.timeout)
    engine = None if args.no_docker else probe_engine(args.timeout)
    verdict = classify(graph, engine)

    result = {
        "verdict": verdict,
        "graph": {"host": args.host, "port": port, **graph},
        "engine": engine,
        "docker_socket": str(docker_socket()) if docker_socket() else None,
        "timeout_s": args.timeout,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        sock = result["docker_socket"]
        print(f"graph  {args.host}:{port} -> "
              f"{'PONG' if graph['ok'] else graph.get('error')} "
              f"({graph['elapsed_s']}s)")
        if engine is None:
            print("engine not probed (--no-docker)")
        else:
            print(f"engine -> {engine['status']} "
                  f"({engine['detail']}) ({engine['elapsed_s']}s)"
                  + (f" socket={sock}" if sock else ""))
        print(f"VERDICT: {verdict}")
        if verdict == ENGINE_WEDGED:
            # Word this from the MEASURED detail, never from an asserted
            # mechanism: a timeout, a missing CLI with a live socket, an OSError
            # and an unrecognised status all arrive here, and only one of them
            # "did not answer inside the bound". Asserting a cause the probe did
            # not observe is the misdiagnosis this tool exists to remove.
            cause = engine["detail"] if engine else "not probed"
            print(f"  The container engine is not usable ({cause}), so a graph "
                  f"result cannot be\n  trusted here. Every docker call may "
                  f"hang, and a graph timeout cannot be\n  attributed — this "
                  f"is not necessarily a graph failure. Do NOT stand up a\n  "
                  f"second graph instance and do NOT point at the hosted API "
                  f"(see #7017).")
        elif verdict == UNMEASURABLE:
            print("  The probes did not return usable results, so no verdict is "
                  "possible. Re-run and\n  read the raw output above rather "
                  "than acting on a guess.")
        elif verdict == GRAPH_DOWN:
            if engine is None:
                print("  The graph port is unreachable and the engine was NOT "
                      "probed (--no-docker),\n  so a wedged engine cannot be "
                      "ruled out. Re-run without --no-docker before\n  treating "
                      "this as a graph problem.")
            elif engine.get("status") == ENGINE_ABSENT:
                print("  No engine is in play (no docker CLI and no engine socket), "
                      "so the graph port\n  itself is unreachable. Check the "
                      "graph container and the canonical instance\n  (127.0.0.1, "
                      "never `localhost` — #6666).")
            elif engine.get("status") == ENGINE_DAEMON_DOWN:
                print("  The docker daemon is not running, so docker calls fail "
                      "fast rather than hang and\n  the graph port itself is "
                      "unreachable. Check the graph container and the\n  "
                      "canonical instance (127.0.0.1, never `localhost` — #6666).")
            else:
                print("  The engine answered, so the graph port itself is "
                      "unreachable. Check the\n  graph container and the "
                      "canonical instance (127.0.0.1, never `localhost` — "
                      "#6666).")
    return 0 if verdict == GRAPH_UP else 2


if __name__ == "__main__":
    sys.exit(main())
