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

CONTRACT
--------
* The container is named ``fdb-lane-<slug>`` and bound to ``127.0.0.1`` only.
* Persistence is OFF (``--appendonly no --save ''``): the lane's data is
  disposable, which is the point.
* ``down`` refuses every name that is not ``fdb-lane-*``, so the shared
  dev/test instances (``falkordb``, ``falkordb-16379``, ...) can never be
  removed by this tool. This is the fail-closed half of the design and it is
  covered by ``tests/test_test_lane_tool.py``.

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

IMAGE = "falkordb/falkordb:latest"
NAME_PREFIX = "fdb-lane-"
PORT_RANGE = (16390, 16499)
DEFAULT_GRAPH = "tortoise_test_matrix"
#: Container names this tool must NEVER remove: the shared instances every lane
#: and the orchestration graph depend on.
PROTECTED_NAMES = frozenset({
    "falkordb", "falkordb-16379", "fdb-6599", "w6213-fdb", "fdb-5084-e",
})


def repo_root() -> Path:
    """The worktree this invocation belongs to (never the hub main checkout)."""
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=False)
    return Path(out.stdout.strip()) if out.stdout.strip() else Path.cwd()


def slug_for(path: Path | str) -> str:
    """A short, stable, filesystem-safe slug for a worktree path.

    Deterministic so two invocations in the same worktree reuse ONE container
    instead of racing to create two.
    """
    digest = hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()
    return digest[:10]


def container_name(slug: str) -> str:
    return f"{NAME_PREFIX}{slug}"


def is_managed(name: str) -> bool:
    """True only for names this tool owns. The guard `down` keys on."""
    return name.startswith(NAME_PREFIX) and name not in PROTECTED_NAMES


GRAPH_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+\Z")


def _validate_graph(graph: str) -> str:
    """Reject a graph name that would corrupt the eval-ed export line.

    Called BEFORE a container is created (an invalid name must not leave a
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
    loopback-bound and disposable), so the URI carries an empty password.
    """
    return f"docker://:@127.0.0.1:{port}/{_validate_graph(graph)}"


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pick_port(lo: int = PORT_RANGE[0], hi: int = PORT_RANGE[1]) -> int:
    for port in range(lo, hi + 1):
        if port_is_free(port) and not _container_publishes(port):
            return port
    raise SystemExit(
        f"test-lane: no free port in {lo}-{hi}; remove stale fdb-lane-* "
        f"containers (`uv run python tools/test_lane.py list`)"
    )


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          check=check)


def _container_publishes(port: int) -> bool:
    # `-a`: a STOPPED container still reserves its published host port, so a
    # running-only scan can hand back a port `docker run -p` will then refuse.
    r = _docker("ps", "-a", "--format", "{{.Names}} {{.Ports}}", check=False)
    return f":{port}->" in (r.stdout or "")


def container_state(name: str) -> str:
    """`running` | `stopped` | `absent` | `unknown`.

    `absent` (the container does not exist) is deliberately distinguished from
    `unknown` (docker itself failed — daemon down, permission): reporting the
    first when the second is true is how `status` would claim a lane has no
    container when it merely could not ask.
    """
    r = _docker("inspect", "--format", "{{.State.Status}}", name, check=False)
    if r.returncode == 0:
        return (r.stdout or "").strip() or "unknown"
    err = f"{r.stderr or ''}{r.stdout or ''}".lower()
    return "absent" if ("no such" in err or "not found" in err) else "unknown"


def _graph_count(name: str) -> int | None:
    """Graphs in a container, or None when docker could not be asked.

    None rather than 0: `graphs=0` is a claim about the container, and a failed
    `docker exec` is not evidence for it.
    """
    r = _docker("exec", name, "redis-cli", "--no-auth-warning", "GRAPH.LIST",
                check=False)
    if r.returncode != 0:
        return None
    return len([ln for ln in (r.stdout or "").splitlines() if ln.strip()])


def _published_port(name: str) -> int | None:
    """The loopback port a container publishes for 6379, or None."""
    r = _docker("port", name, "6379/tcp", check=False)
    for token in (r.stdout or "").replace("\n", " ").split():
        if ":" in token:
            try:
                return int(token.rsplit(":", 1)[1])
            except ValueError:
                pass
    return None


def start(slug: str, port: int | None = None, *, image: str = IMAGE) -> tuple[str, int]:
    """Start (or reuse) this lane's container. Returns (name, published port).

    The returned port is always the one Docker PUBLISHES, never the one that was
    requested: `-p 0:6379` makes Docker choose an ephemeral port, so echoing the
    requested value would hand the caller a URI pointing at nothing.

    A requested port is validated FIRST — before `container_state` and before
    the stale-container `docker rm` — so an invalid value cannot reach docker at
    all (the guard is only as good as its ordering).
    """
    if port is not None and not (0 < port < 65536):
        raise SystemExit(f"test-lane: --port {port} is not a valid TCP port")
    name = container_name(slug)
    state = container_state(name)
    if state == "running":
        published = _published_port(name)
        if published is None:
            raise SystemExit(f"test-lane: {name} is running but publishes no port")
        return name, published
    if state == "unknown":
        raise SystemExit(
            f"test-lane: cannot determine the state of {name} — is the docker "
            f"daemon running? (refusing to guess: removing a container that may "
            f"belong to another lane would be unforgivable)"
        )
    if state != "absent":
        # Defence in depth, and NOT an `assert`: this guard must survive
        # `python -O`, so it is a real refusal rather than a stripped one.
        if not is_managed(name):
            raise SystemExit(f"test-lane: refusing to remove unmanaged {name!r}")
        _docker("rm", "-f", name, check=False)

    chosen = port if port is not None else pick_port()
    r = _docker(
        "run", "-d", "--name", name,
        "-p", f"127.0.0.1:{chosen}:6379",
        "-e", "REDIS_ARGS=--appendonly no --save ''",
        image,
        check=False,
    )
    if r.returncode != 0:
        raise SystemExit(f"test-lane: docker run failed: {r.stderr.strip()}")

    for _ in range(30):
        ping = _docker("exec", name, "redis-cli", "--no-auth-warning", "PING",
                       check=False)
        if "PONG" in (ping.stdout or ""):
            published = _published_port(name)
            if published is None:
                # Never leave behind a container nobody can address.
                _docker("rm", "-f", name, check=False)
                raise SystemExit(
                    f"test-lane: {name} answered PING but publishes no port; "
                    f"removal attempted"
                )
            return name, published
        time.sleep(1)
    logs = _docker("logs", "--tail", "20", name, check=False)
    rm = _docker("rm", "-f", name, check=False)
    fate = ("removed it" if rm.returncode == 0
            else "FAILED to remove it — check `docker ps -a`")
    raise SystemExit(
        f"test-lane: {name} never answered PING; {fate}. Logs:\n"
        f"{logs.stdout}{logs.stderr}"
    )


def stop(name: str) -> str:
    """Remove a container, taking the FULL name. Refuses anything not
    `fdb-lane-*` — there is deliberately no force/bypass flag: the shared
    dev/test instances are not removable by this tool under any argument."""
    if not is_managed(name):
        raise SystemExit(
            f"test-lane: refusing to remove {name!r} — not a test-lane "
            f"container (this tool can only remove {NAME_PREFIX}*; the shared "
            f"instances are protected by design)"
        )
    state = container_state(name)
    if state == "absent":
        return "absent"
    r = _docker("rm", "-f", name, check=False)
    if r.returncode != 0:
        # Never report a removal that did not happen.
        raise SystemExit(
            f"test-lane: failed to remove {name}: "
            f"{(r.stderr or r.stdout).strip()}"
        )
    return "removed"


def cmd_up(args: argparse.Namespace) -> int:
    _validate_graph(args.graph)      # refuse BEFORE a container exists
    name, port = start(slug_for(repo_root()), args.port)
    graphs = _graph_count(name)
    shown = "unknown" if graphs is None else str(graphs)
    print(f"test-lane: {name} on 127.0.0.1:{port} (graphs={shown})",
          file=sys.stderr)
    print(f"export TORTOISE_DB_URI='{uri_for(port, args.graph)}'")
    return 0


def cmd_uri(args: argparse.Namespace) -> int:
    return cmd_up(args)


def _target_name(args: argparse.Namespace) -> str:
    """The container this invocation acts on.

    ALWAYS the worktree-derived name. There is no override — not `--name` and
    not `--slug`: a review round removed only `--name`, and the next one showed
    that `down --slug <peer>` still targeted another lane's live container, since
    a peer's slug is a computable `sha1(path)[:10]` and `is_managed()` can only
    reject the non-`fdb-lane-*` family. `stop()` keeps that family refusal as
    defence in depth, and it is unit-tested directly.
    """
    return container_name(slug_for(repo_root()))


def cmd_down(args: argparse.Namespace) -> int:
    name = _target_name(args)
    print(f"test-lane: {name} -> {stop(name)}", file=sys.stderr)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    name = _target_name(args)
    state = container_state(name)
    print(f"{name}: {state}", file=sys.stderr)
    if state == "running":
        graphs = _graph_count(name)
        print(f"  graphs={'unknown' if graphs is None else graphs}", file=sys.stderr)
        published = _published_port(name)
        if published is not None:
            print(f"  uri={uri_for(published, args.graph)}", file=sys.stderr)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    r = _docker("ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}",
                check=False)
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
    # The flags live on the SUBcommands so the natural spelling works:
    # `uri --port 16390`. There is deliberately NO override of WHICH container
    # this tool acts on — not `--name`, not `--slug`: either would let one lane
    # delete (or silently adopt) another lane's `fdb-lane-<slug>`, which is the
    # cross-lane destruction this whole tool exists to prevent. The target is
    # always derived from this worktree.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", type=int, default=None,
                        help="publish on this loopback port (default: first free)")
    common.add_argument("--graph", default=DEFAULT_GRAPH,
                        help=f"graph/database name for the URI (default {DEFAULT_GRAPH})")
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn, help_ in (
        ("up", cmd_up, "start this lane's container and print the export line"),
        ("uri", cmd_uri, "same as `up` (reads better inside $( ))"),
        ("down", cmd_down, "remove this lane's container"),
        ("status", cmd_status, "is it running, and how many graphs does it hold"),
        ("list", cmd_list, "list every fdb-lane-* container"),
    ):
        sp = sub.add_parser(name, help=help_, parents=[common])
        sp.set_defaults(func=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
