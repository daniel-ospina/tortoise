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
import socket
import subprocess
import sys
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


def uri_for(port: int, graph: str = DEFAULT_GRAPH) -> str:
    """The URI shape the docker lane's tests expect (`docker://` + loopback).

    No password: this tool starts the container without `requirepass` (it is
    loopback-bound and disposable), so the URI carries an empty password.
    """
    return f"docker://:@127.0.0.1:{port}/{graph}"


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
    r = _docker("ps", "--format", "{{.Names}} {{.Ports}}", check=False)
    return f":{port}->" in (r.stdout or "")


def container_state(name: str) -> str:
    r = _docker("inspect", "--format", "{{.State.Status}}", name, check=False)
    return (r.stdout or "").strip() if r.returncode == 0 else "absent"


def _graph_count(name: str) -> int:
    r = _docker("exec", name, "redis-cli", "--no-auth-warning", "GRAPH.LIST",
                check=False)
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
    """Start (or reuse) this lane's container. Returns (name, port)."""
    name = container_name(slug)
    state = container_state(name)
    if state == "running":
        published = _published_port(name)
        if published is None:
            raise SystemExit(f"test-lane: {name} is running but publishes no port")
        return name, published
    if state != "absent":
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
            return name, chosen
        import time
        time.sleep(1)
    logs = _docker("logs", "--tail", "20", name, check=False)
    _docker("rm", "-f", name, check=False)
    raise SystemExit(
        f"test-lane: {name} never answered PING; removed it. Logs:\n"
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
    _docker("rm", "-f", name, check=False)
    return "removed"


def cmd_up(args: argparse.Namespace) -> int:
    slug = args.slug or slug_for(repo_root())
    name, port = start(slug, args.port)
    print(f"test-lane: {name} on 127.0.0.1:{port} "
          f"(graphs={_graph_count(name)})", file=sys.stderr)
    print(f"export TORTOISE_DB_URI='{uri_for(port, args.graph)}'")
    return 0


def cmd_uri(args: argparse.Namespace) -> int:
    return cmd_up(args)


def _target_name(args: argparse.Namespace) -> str:
    """The container this invocation acts on: explicit --name wins, else the
    slug derived from the worktree. Both are validated by `stop`/`up`."""
    if getattr(args, "name", None):
        return args.name
    return container_name(args.slug or slug_for(repo_root()))


def cmd_down(args: argparse.Namespace) -> int:
    name = _target_name(args)
    print(f"test-lane: {name} -> {stop(name)}", file=sys.stderr)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    name = _target_name(args)
    state = container_state(name)
    print(f"{name}: {state}", file=sys.stderr)
    if state == "running":
        print(f"  graphs={_graph_count(name)}", file=sys.stderr)
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
    # The flags live on the SUBcommands, so both the documented and the natural
    # spellings work: `uri --port 16390`, `down --name falkordb`. (Defining them
    # on the root only accepts them BEFORE the subcommand, which silently made
    # `down --name <shared>` fail on argument parsing instead of on the guard.)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--slug", default=None,
                        help="lane slug (default: derived from this worktree path)")
    common.add_argument("--port", type=int, default=None,
                        help="publish on this loopback port (default: first free)")
    common.add_argument("--graph", default=DEFAULT_GRAPH,
                        help=f"graph/database name for the URI (default {DEFAULT_GRAPH})")
    common.add_argument("--name", default=None,
                        help="explicit container name (refused unless it starts "
                             f"with {NAME_PREFIX})")
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
