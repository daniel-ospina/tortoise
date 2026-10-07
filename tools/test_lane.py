#!/usr/bin/env python3
"""test-lane — a private, throwaway FalkorDB for the docker test lane (#5084).

WHY THIS EXISTS (measured, 2026-10-06)
--------------------------------------
The documented docker lane points every lane at ONE long-lived container
(``127.0.0.1:6379``). Test graphs are minted per run and reaped by a
session-end sweep whose server-global pass is deferred while ANY peer session
is live (``tests/_embedded.py::wipe_server`` spares live peers' journaled
graphs — the #3074 cross-session flake contract). With the fleet running
concurrently that deferral never resolves into a quiet moment, so the shared
instance accumulates per-run graphs:

  * ``docker inspect falkordb`` -> ``restarts=213`` (it was **46** when #5084
    was filed on 2026-09-24) — the load -> restart -> longer-load loop.
  * A 40-minute aborted run in this lane left **13** graphs behind, and a
    single-file run while other lanes worked showed graphs appearing from
    THEIR sessions (each journaled under its own session nonce).

A private instance removes the class by construction: the graphs a lane mints
live in a container that dies with the lane, so nothing it does can contaminate
another lane and no peer's leftovers can contaminate it.

MEASURED TRADE-OFF (do not oversell this tool)
----------------------------------------------
Isolation, NOT speed. The same single test on the docker lane took **90.59 s**
on a private container versus **92.66 s** on the shared one — the wall time is
dominated by per-session work (embedder load), not by the container's state.

CONTRACT — THE TARGET IS NOT A PARAMETER
----------------------------------------
The container this tool acts on is ALWAYS ``fdb-lane-<sha1(worktree)[:10]>``,
derived inside ``start()``/``stop()`` from the worktree the command runs in.
There is no ``--name``, no ``--slug``, and no function argument that selects a
container: two review rounds showed that each override, however spelled, is a
way for one lane to delete or silently adopt another lane's isolated DB (a
peer's slug is a computable ``sha1(path)[:10]``). ``repo_root()`` scrubs
``GIT_DIR``/``GIT_WORK_TREE`` for the same reason — an inherited git env var
would otherwise retarget the CLI at a peer worktree with no flag involved.

Also fail-closed by design:
* the container is bound to ``127.0.0.1`` only;
* persistence is OFF (``--appendonly no --save ''``) — the data is disposable;
* ``is_managed()`` (the ``fdb-lane-*`` prefix, minus the protected shared
  instances) is what a removal intent is keyed on, and it is pinned by tests;
* ````docker ps -a`` is consulted for port collisions, because a stopped
  container still reserves its published port.

USAGE
-----
    eval "$(uv run python tools/test_lane.py uri)"   # start if needed + export
    uv run pytest tests/ -q
    uv run python tools/test_lane.py down            # remove it

``uri`` prints ``export TORTOISE_DB_URI='...'`` on stdout so it can be
``eval``-ed; every diagnostic goes to stderr, so ``eval "$(...)"`` stays clean.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

# #5128 shape: refuse an old interpreter before module-level 3.12+ constructs.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/test_lane.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/test_lane.py`"
    )

#: Overridable so CI can pin a digest/version; `latest` matches the existing
#: in-repo precedent (scripts/restore-smoke.sh also runs falkordb/falkordb:latest).
IMAGE = os.environ.get("TORTOISE_TEST_LANE_IMAGE", "falkordb/falkordb:latest")
NAME_PREFIX = "fdb-lane-"
PORT_RANGE = (16390, 16499)
DEFAULT_GRAPH = "tortoise_test_matrix"
#: Docker calls are bounded: this tool exists for an overloaded host, so a
#: wedged daemon must fail diagnosably instead of hanging the lane forever.
DOCKER_TIMEOUT = 60
#: `docker run` may have to pull the image first.
IMAGE_PULL_TIMEOUT = 600
#: How long a fresh or reused container gets to answer PING.
READY_TIMEOUT = 60
#: The shared instances every lane and the orchestration graph depend on. They
#: do not carry NAME_PREFIX, so the prefix rule already refuses them; this
#: constant documents them AND is what `is_managed` consults, so a future shared
#: container that DID share the prefix would still be refused (pinned by
#: tests/test_test_lane_tool.py::test_is_managed_refuses_a_protected_name_that_shares_the_prefix).
PROTECTED_NAMES = frozenset({
    "falkordb", "falkordb-16379", "fdb-6599", "w6213-fdb",
})
GRAPH_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+\Z")


def repo_root() -> Path:
    """The git worktree this invocation belongs to.

    ``GIT_DIR``/``GIT_WORK_TREE`` are SCRUBBED rather than inherited: with
    ``GIT_WORK_TREE`` set (git-hook contexts, wrapper scripts) `rev-parse`
    reports the OTHER tree, so the tool would compute a peer lane's container
    name and act on it with no flag involved (review round 3, P2).
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=False, env=env)
    return Path(out.stdout.strip()) if out.stdout.strip() else Path.cwd()


def slug_for(path: Path | str) -> str:
    """A short, stable, filesystem-safe slug for a worktree path.

    Deterministic, so two invocations in the same worktree reuse ONE container
    instead of racing to create two.
    """
    digest = hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()
    return digest[:10]


def container_name(slug: str) -> str:
    return f"{NAME_PREFIX}{slug}"


def lane_name() -> str:
    """The one container this tool can act on. Never a parameter."""
    return container_name(slug_for(repo_root()))


def is_managed(name: str) -> bool:
    """True only for names this tool may remove.

    TRIPWIRE, not a live filter: with the target now derived internally,
    ``lane_name()`` is always ``fdb-lane-<10 hex>``, so in production this can
    only return True. It is kept — one line, no cost — because EVERY removal
    intent still has to pass it, so re-introducing a way to name a container
    (the round-2/3 peer-deletion defect) does not silently regain the ability
    to delete a shared instance.
    """
    return name.startswith(NAME_PREFIX) and name not in PROTECTED_NAMES


def _validate_graph(graph: str) -> str:
    """Reject a graph name that would corrupt the eval-ed export line.

    Called before a container is created (an invalid name must not leave a
    running container behind) and again inside `uri_for`, so the contract holds
    for every caller and not just the CLI.
    """
    if not GRAPH_NAME_RE.match(graph):
        raise SystemExit(
            f"test-lane: refusing graph name {graph!r} — it must match "
            f"{GRAPH_NAME_RE.pattern} (it is interpolated into an eval-ed "
            f"export line)"
        )
    return graph


def uri_for(port: int, graph: str = DEFAULT_GRAPH) -> str:
    """The URI shape the docker lane's tests expect (`docker://` + loopback).

    No password: this tool starts the container without `requirepass` (it is
    loopback-bound and disposable), so the URI carries an empty password. Both
    fields are validated because this string is printed into an eval-ed line.
    """
    if not isinstance(port, int) or not (0 < port < 65536):
        raise SystemExit(f"test-lane: {port!r} is not a valid TCP port")
    return f"docker://:@127.0.0.1:{port}/{_validate_graph(graph)}"


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _docker(*args: str,
            timeout: int = DOCKER_TIMEOUT) -> subprocess.CompletedProcess:
    """Run docker — never raising, never hanging.

    A failure comes back as a non-zero result so every caller's "a failure is
    not evidence of absence" rule applies uniformly, and a TIMEOUT is converted
    into that same shape with a self-describing stderr rather than blocking
    forever (round 4, P2): this tool exists for an overloaded host, so a wedged
    daemon is an expected failure, not an exotic one.
    """
    try:
        return subprocess.run(["docker", *args], capture_output=True,
                              text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            ["docker", *args], 1, "",
            f"docker {' '.join(args[:1])} timed out after {timeout}s")
    except FileNotFoundError as exc:      # no docker binary on PATH
        return subprocess.CompletedProcess(["docker", *args], 1, "", str(exc))


def _container_publishes(port: int) -> bool:
    # `-a`: a STOPPED container still reserves its published host port, so a
    # running-only scan can hand back a port `docker run -p` will then refuse.
    r = _docker("ps", "-a", "--format", "{{.Names}} {{.Ports}}")
    return f":{port}->" in (r.stdout or "")


def pick_port(lo: int = PORT_RANGE[0], hi: int = PORT_RANGE[1]) -> int:
    for port in range(lo, hi + 1):
        if port_is_free(port) and not _container_publishes(port):
            return port
    raise SystemExit(
        f"test-lane: no free port in {lo}-{hi}; remove stale fdb-lane-* "
        f"containers (`uv run python tools/test_lane.py list`)"
    )


def _wait_ready(name: str) -> bool:
    """Wait for the container's server to answer PING — bounded in WALL TIME.

    Seconds, not iterations: an iteration count is not a bound when each
    iteration awaits an unbounded docker call (round 4, P2).
    """
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        ping = _docker("exec", name, "redis-cli", "--no-auth-warning", "PING",
                       timeout=READY_TIMEOUT)
        if "PONG" in (ping.stdout or ""):
            return True
        time.sleep(1)
    return False


def container_state(name: str) -> str:
    """`absent`, `unknown`, or docker's own state string (`running`, `exited`, ...).

    `absent` (the container does not exist) is deliberately distinguished from
    `unknown` (docker itself failed — daemon down, permission, timeout):
    reporting the first when the second is true is how `status` would claim a
    lane has no container when it merely could not ask, and `start()` would go
    on to `docker rm` a container that may belong to another lane.
    """
    r = _docker("inspect", "--format", "{{.State.Status}}", name)
    if r.returncode == 0:
        return (r.stdout or "").strip() or "unknown"
    err = f"{r.stderr or ''}{r.stdout or ''}".lower()
    return "absent" if ("no such" in err or "not found" in err) else "unknown"


def _graph_count(name: str) -> int | None:
    """Graphs in a container, or None when docker could not be asked.

    None rather than 0: `graphs=0` is a claim about the container, and a failed
    `docker exec` is not evidence for it.
    """
    r = _docker("exec", name, "redis-cli", "--no-auth-warning", "GRAPH.LIST")
    if r.returncode != 0:
        return None
    return len([ln for ln in (r.stdout or "").splitlines() if ln.strip()])


def _published_port(name: str) -> int | None:
    """The loopback port a container publishes for 6379, or None."""
    r = _docker("port", name, "6379/tcp")
    if r.returncode != 0:
        return None
    for token in (r.stdout or "").replace("\n", " ").split():
        if ":" in token:
            try:
                return int(token.rsplit(":", 1)[1])
            except ValueError:
                pass
    return None


def start(port: int | None = None) -> tuple[str, int]:
    """Start (or reuse) THIS LANE's container. Returns (name, published port).

    There is no container parameter — see the module docstring's CONTRACT. The
    image is not a parameter either: `TORTOISE_TEST_LANE_IMAGE` is the seam, so
    a caller cannot smuggle in a different runtime.

    The published port is returned rather than the requested one because it is
    the only authoritative value: it is what the lane's URI must name, and the
    two can differ whenever Docker resolves the mapping itself.

    A requested port is validated FIRST — before `container_state` and before
    the stale-container `docker rm` — so an invalid value cannot reach docker at
    all (a guard is only as good as its ordering).
    """
    if port is not None and not (0 < port < 65536):
        raise SystemExit(f"test-lane: --port {port} is not a valid TCP port")
    name = lane_name()
    state = container_state(name)
    if state == "running":
        published = _published_port(name)
        if published is None:
            raise SystemExit(f"test-lane: {name} is running but publishes no port")
        # A long-lived container can be wedged: the fresh path refuses to hand
        # back a URI to a server that never answers, and so must this one.
        if not _wait_ready(name):
            raise SystemExit(
                f"test-lane: {name} is running but never answered PING within "
                f"{READY_TIMEOUT}s — it may be wedged: run `uv run python "
                f"tools/test_lane.py down` and retry"
            )
        return name, published
    if state == "unknown":
        raise SystemExit(
            f"test-lane: cannot determine the state of {name} — is the docker "
            f"daemon running? (refusing to guess: removing a container that may "
            f"belong to another lane would be unforgivable)"
        )
    if state != "absent":
        if not is_managed(name):
            raise SystemExit(f"test-lane: refusing to remove unmanaged {name!r}")
        _docker("rm", "-f", name)

    chosen = port if port is not None else pick_port()
    r = _docker(
        "run", "-d", "--name", name,
        "-p", f"127.0.0.1:{chosen}:6379",
        "-e", "REDIS_ARGS=--appendonly no --save ''",
        IMAGE,
        timeout=IMAGE_PULL_TIMEOUT,   # the image may need pulling first
    )
    if r.returncode != 0:
        raise SystemExit(f"test-lane: docker run failed: {r.stderr.strip()}")

    if not _wait_ready(name):
        logs = _docker("logs", "--tail", "20", name)
        raise SystemExit(
            f"test-lane: {name} never answered PING; {_remove_and_describe(name)}. "
            f"Logs:\n{logs.stdout}{logs.stderr}"
        )
    published = _published_port(name)
    if published is None:
        # Never leave behind a container nobody can address.
        raise SystemExit(
            f"test-lane: {name} answered PING but publishes no port; "
            f"{_remove_and_describe(name)}"
        )
    return name, published


def _remove_and_describe(name: str) -> str:
    """Remove a container this tool just created, and say what actually
    happened — a cleanup whose result is unread is how a running container with
    a reserved port survives to make every later `start()` refuse."""
    rm = _docker("rm", "-f", name)
    return ("removed it" if rm.returncode == 0
            else "FAILED to remove it — check `docker ps -a`")


def stop() -> str:
    """Remove THIS LANE's container. Takes no argument, by design.

    Two review rounds established that any way to name another container —
    `--name`, `--slug`, or a parameter — is a way for one lane to delete
    another lane's isolated DB, so there is nothing to name. `is_managed()`
    remains the prefix rule that a removal must satisfy.
    """
    name = lane_name()
    if not is_managed(name):
        raise SystemExit(
            f"test-lane: refusing to remove {name!r} — not a test-lane "
            f"container (this tool can only remove {NAME_PREFIX}*; the shared "
            f"instances are protected by design)"
        )
    state = container_state(name)
    if state == "absent":
        return "absent"
    r = _docker("rm", "-f", name)
    if r.returncode != 0:
        # Never report a removal that did not happen.
        raise SystemExit(
            f"test-lane: failed to remove {name}: "
            f"{(r.stderr or r.stdout).strip()}"
        )
    return "removed"


def cmd_up(args: argparse.Namespace) -> int:
    _validate_graph(args.graph)      # refuse BEFORE a container exists
    name, port = start(args.port)
    graphs = _graph_count(name)
    shown = "unknown" if graphs is None else str(graphs)
    print(f"test-lane: {name} on 127.0.0.1:{port} (graphs={shown})",
          file=sys.stderr)
    print(f"export TORTOISE_DB_URI='{uri_for(port, args.graph)}'")
    return 0


def cmd_uri(args: argparse.Namespace) -> int:
    return cmd_up(args)


def cmd_down(args: argparse.Namespace) -> int:
    print(f"test-lane: {lane_name()} -> {stop()}", file=sys.stderr)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    name = lane_name()
    state = container_state(name)
    print(f"{name}: {state}", file=sys.stderr)
    if state == "running":
        graphs = _graph_count(name)
        print(f"  graphs={'unknown' if graphs is None else graphs}",
              file=sys.stderr)
        published = _published_port(name)
        if published is not None:
            print(f"  uri={uri_for(published, args.graph)}", file=sys.stderr)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    r = _docker("ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}")
    if r.returncode != 0:
        # The rest of this module refuses to infer absence from a failed call;
        # `list` must not be the exception.
        print(f"test-lane: docker ps failed: "
              f"{(r.stderr or r.stdout).strip()}", file=sys.stderr)
        return 1
    managed = [ln for ln in (r.stdout or "").splitlines()
               if ln.startswith(NAME_PREFIX)]
    print("\n".join(managed) if managed else "no fdb-lane-* containers",
          file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="test_lane.py",
        description="Private throwaway FalkorDB for the docker test lane (#5084)",
    )
    # Flags live on the SUBcommands so the natural spelling works
    # (`uri --port 16390`), and ONLY on the commands that use them: `down` takes
    # no target and no mapping, and `status` reads a port rather than choosing
    # one, so accepting a flag either ignores would read as "do that" (round 3
    # P3 + round 4 P3).
    graph_only = argparse.ArgumentParser(add_help=False)
    graph_only.add_argument("--graph", default=DEFAULT_GRAPH,
                            help=f"graph/database name for the URI "
                                 f"(default {DEFAULT_GRAPH})")
    mapper = argparse.ArgumentParser(add_help=False, parents=[graph_only])
    mapper.add_argument("--port", type=int, default=None,
                        help="publish on this loopback port (default: first free)")
    plain = argparse.ArgumentParser(add_help=False)
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn, help_, parent in (
        ("up", cmd_up, "start this lane's container and print the export line",
         mapper),
        ("uri", cmd_uri, "same as `up` (reads better inside $( ))", mapper),
        ("down", cmd_down, "remove this lane's container", plain),
        ("status", cmd_status, "is it running, and how many graphs does it hold",
         graph_only),
        ("list", cmd_list, "list every fdb-lane-* container", plain),
    ):
        sp = sub.add_parser(name, help=help_, parents=[parent])
        sp.set_defaults(func=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
