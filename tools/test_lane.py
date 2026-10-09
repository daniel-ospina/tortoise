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

A private instance removes the class for every test that resolves its target
from ``TORTOISE_DB_URI``: the graphs a lane mints live in a container no OTHER
lane can name, so nothing it does can contaminate another lane and no peer's
leftovers can contaminate it. The container is NOT self-cleaning — it is a
``docker run -d`` with no ``--rm`` and nothing binds it to this session — so it
lives until an explicit ``down`` (or a reap), and a forgotten lane leaves one
container and its graphs behind.

SCOPE — NOT THE WHOLE SUITE (do not oversell)
----------------------------------------------------------------------------------
Tests that resolve their target from ``TORTOISE_DB_URI`` are isolated by exporting
it. Tests that build a URI through ``tests/_live_utils.py`` instead key on
``TORTOISE_TEST_DOCKER_PORT`` (default 6379) and a ``falkordb`` password, so they
still address the SHARED instance and their graphs still accumulate there; this
tool exports neither variable, and it starts the private container without
``requirepass``, so the seam's default password could not authenticate against it
even if its port were exported. Closing that seam is #5084's remaining work — this
tool is the isolation half of it, not the whole fix.

(No module counts appear here on purpose: a `grep` is not a count until its
pattern AND its population are pinned, so the MECHANISM above is the durable
part.)

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
container: any override, however spelled, would be a way for one lane to delete
or silently adopt another lane's isolated DB, because a peer's slug is a
computable ``sha1(path)[:10]``. ``repo_root()`` scrubs
``GIT_DIR``/``GIT_WORK_TREE`` for the same reason — an inherited git env var
would otherwise retarget the CLI at a peer worktree with no flag involved.

Also fail-closed by design:
* the container is bound to ``127.0.0.1`` only;
* persistence is OFF (``--appendonly no --save ''``) — the data is disposable;
* ``is_managed()`` (the ``fdb-lane-*`` prefix, minus the protected shared
  instances) is what a removal intent is keyed on, and it is pinned by tests;
* ````docker ps -a`` is consulted for port collisions, and THAT SCAN is the
  load-bearing half of the guard: a peer container can publish a port this host
  will still bind freely (measured), so the host probe alone is not enough.
  ``-a`` adds nothing measured — neither a stopped nor a created container
  reports or reserves its published port (Docker 29.4.0) — and is kept only as a
  cheap hedge;
* ``DOCKER_HOST`` is deliberately NOT scrubbed (unlike ``GIT_DIR``/
  ``GIT_WORK_TREE``): a remote daemon is addressed as-is, so the printed
  ``127.0.0.1:<port>`` URI then names THAT daemon's loopback, not this host's —
  a warning is printed when ``DOCKER_HOST`` is set;
* a FAILED ``docker ps -a`` scan ABORTS port selection instead of reporting
  "nothing is published" (a failure is never read as permission).

USAGE
-----
    uri="$(uv run python tools/test_lane.py uri)" || exit 1
    eval "$uri"
    uv run pytest tests/ -q
    uv run python tools/test_lane.py down            # remove it

The two steps are deliberate; do NOT collapse them into
``eval "$(...)" || exit 1``. A FAILED run prints nothing on stdout, and
``eval ""`` returns 0 — so the one-line form cannot abort at all: it leaves a
PREVIOUSLY exported ``TORTOISE_DB_URI`` (commonly the shared lane) in place while
the command looks like it succeeded. Assigning first is what puts the TOOL's exit
status on the ``||``, which is the only thing that can abort.

``uri`` prints ``export TORTOISE_DB_URI='...'`` on stdout so it can be
``eval``-ed; every diagnostic goes to stderr, so ``eval "$(...)"`` stays clean.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12+-only construct would otherwise fail first, and it would fail with a
# traceback instead of this message. The gate that enforces this shape is
# tests/test_entry_point_python_guard.py, not a style rule.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/test_lane.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/test_lane.py`"
    )

import argparse
import hashlib
import os
import re
import socket
import subprocess
import time
from pathlib import Path

#: Overridable so CI can pin a digest/version; `latest` matches the existing
#: in-repo precedent (scripts/restore-smoke.sh also runs falkordb/falkordb:latest).
IMAGE = os.environ.get("TORTOISE_TEST_LANE_IMAGE", "falkordb/falkordb:latest")
NAME_PREFIX = "fdb-lane-"
PORT_RANGE = (16390, 16499)
DEFAULT_GRAPH = "tortoise_test_matrix"
#: Docker calls are bounded: this tool exists for an overloaded host, so a
#: wedged daemon must fail diagnosably instead of hanging the lane forever.
DOCKER_TIMEOUT = 60
#: The other subprocess on the hot path — every command resolves the worktree
#: first, so a hung `git` would hang the lane the same way.
GIT_TIMEOUT = 30
#: `docker run` may have to pull the image first.
IMAGE_PULL_TIMEOUT = 600
#: How long a fresh or reused container gets to answer PING.
READY_TIMEOUT = 60
#: The shared instances every lane and the orchestration graph depend on, PLUS
#: the private containers of particular peer lanes that must never be removed.
#: It is HAND-MAINTAINED: a shared or peer container not listed here is protected
#: only by the NAME_PREFIX rule, which the truly shared names do not carry.
#: `is_managed` consults it, so a listed name is refused even if it did share the
#: prefix (pinned by
#: tests/test_test_lane_tool.py::test_a_protected_name_sharing_the_prefix_is_still_refused).
PROTECTED_NAMES = frozenset({
    "falkordb", "falkordb-16379", "fdb-6599", "w6213-fdb",
})
GRAPH_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+\Z")


def repo_root() -> Path:
    """The git worktree this invocation belongs to.

    ``GIT_DIR``/``GIT_WORK_TREE`` are SCRUBBED rather than inherited: with
    ``GIT_WORK_TREE`` set (git-hook contexts, wrapper scripts) `rev-parse`
    reports the OTHER tree, so the tool would compute a peer lane's container
    name and act on it with no flag involved.

    The fallback to ``Path.cwd()`` (git timed out, or is absent from PATH) is
    deliberate but DEGRADES the contract: invoked from a subdirectory it yields
    a different slug than the worktree root, so ``down`` would report ``absent``
    while the real lane container stays behind (and ``uri`` would start a second
    one). The stderr warning is the signal, not a detail — run from the worktree
    root, or restore git, and the derived name is the worktree's again.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=False, env=env,
                             timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"test-lane: `git rev-parse` did not answer within {GIT_TIMEOUT}s"
              f" — falling back to the current directory", file=sys.stderr)
        return Path.cwd()
    except FileNotFoundError:
        print("test-lane: `git` is not available on PATH — falling back to "
              "the current directory", file=sys.stderr)
        return Path.cwd()
    except OSError as exc:
        # git present but not runnable (PermissionError, ENOEXEC) is the SAME
        # fact as absent. `_docker` handles its identical case; letting this one
        # escape as a traceback would contradict the fallback contract above.
        print(f"test-lane: `git` could not be run ({exc}) — falling back to "
              f"the current directory", file=sys.stderr)
        return Path.cwd()
    root = out.stdout.strip()
    if out.returncode != 0 or not root:
        # A non-zero `rev-parse` (not a repository, dubious ownership, a bad
        # worktree) is the SAME FACT as a timeout: git could not answer. Falling
        # back silently here would contradict the warning contract above and
        # hand back a slug that is not this worktree's.
        print(f"test-lane: `git rev-parse` exited {out.returncode} with no "
              f"worktree — falling back to the current directory "
              f"({(out.stderr or '').strip()[:120]})", file=sys.stderr)
        return Path.cwd()
    return Path(root)


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
    does not silently regain the ability to delete a shared instance.
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
    if os.environ.get("DOCKER_HOST"):
        # Every printed URI comes through here — which is why the warning lives
        # here and not in `pick_port`: `start()`'s reuse path and `status` also
        # print a URI without ever selecting a port.
        print("test-lane: DOCKER_HOST is set — the printed 127.0.0.1 URI names "
              "that daemon's loopback, not this host's", file=sys.stderr)
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
    """Run docker — never raising for a failure it can convert, never hanging.

    A failure comes back as a non-zero result so every caller's "a failure is
    not evidence of absence" rule applies uniformly, and a TIMEOUT, a missing
    binary and an unrunnable binary are converted into that same shape with a
    self-describing stderr rather than blocking forever: this tool exists for an
    overloaded host, so a wedged daemon is an expected failure, not an exotic
    one.
    """
    try:
        return subprocess.run(["docker", *args], capture_output=True,
                              text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            ["docker", *args], 1, "",
            f"docker {' '.join(args[:1])} timed out after {timeout}s")
    except FileNotFoundError:
        # Deliberately NOT `str(exc)`: that is "[Errno 2] No such file or
        # directory: 'docker'". This branch is caught before `OSError` so the
        # message names the cause, and it carries no absence phrasing for
        # `container_state` to mistake for a missing container.
        return subprocess.CompletedProcess(
            ["docker", *args], 127, "",
            "docker executable is not available on PATH")
    except OSError as exc:          # e.g. docker present but not executable
        return subprocess.CompletedProcess(
            ["docker", *args], 126, "", f"could not run docker: {exc}")


def _published_scan() -> str | None:
    """The `docker ps -a` names+ports table, or None when docker could not be
    asked.

    None rather than "": an empty table is a CLAIM about docker, and a failed or
    timed-out call is not evidence for it. This tool exists for an overloaded
    host, so a failed scan is an expected case, not an exotic one.
    """
    # The SCAN is the load-bearing guard: a peer container can publish a port this
    # host still binds freely. `-a` adds nothing measured — a stopped or created
    # container reports no `Ports` (Docker 29.4.0) — so this covers RUNNING
    # containers, and `-a` is kept only as a cheap hedge.
    r = _docker("ps", "-a", "--format", "{{.Names}} {{.Ports}}")
    return None if r.returncode != 0 else (r.stdout or "")


def _container_publishes(port: int, scan: str | None = None) -> bool:
    """Whether `port` is already published, reusing a caller's scan when given.

    When it takes the scan itself, a FAILED scan RAISES rather than returning
    False: "not published" is a claim about docker's answer, and a failure is not
    one. The fail-open is therefore closed structurally, for every caller — not
    only for the one that remembers to pass a table in.
    """
    table = _published_scan() if scan is None else scan
    if table is None:
        raise SystemExit(
            "test-lane: `docker ps -a` failed — cannot tell which ports are "
            "already published; refusing to guess (check the docker daemon)"
        )
    return f":{port}->" in table


def pick_port(lo: int = PORT_RANGE[0], hi: int = PORT_RANGE[1]) -> int:
    """The first loopback port that is free AND not already published.

    The `docker ps -a` scan is taken ONCE and reused for every candidate, and a
    FAILED scan ABORTS rather than yielding an empty table: reading a failure as
    "nothing is published" would mark every candidate free and hand back a port
    `docker run -p` then refuses — the fail-open this module's rule ("a failure
    is never read as permission") exists to forbid.
    """
    # The scan is hoisted into `pick_port`: one `docker ps -a` per selection, not
    # one per candidate (the range is 110 ports wide).
    scan = _published_scan()
    if scan is None:
        raise SystemExit(
            "test-lane: `docker ps -a` failed — cannot tell which ports are "
            "already published; refusing to guess (check the docker daemon)"
        )
    for port in range(lo, hi + 1):
        if port_is_free(port) and not _container_publishes(port, scan):
            return port
    raise SystemExit(
        f"test-lane: no free port in {lo}-{hi}; remove stale fdb-lane-* "
        f"containers (`uv run python tools/test_lane.py list`)"
    )


def _wait_ready(name: str) -> bool:
    """Wait for the container's server to answer PING — bounded in WALL TIME.

    Seconds, not iterations: an iteration count is not a bound when each
    iteration awaits a docker call of its own. Every call's timeout comes from
    the REMAINING budget, so the deadline is not overshot by a full per-call
    timeout.
    """
    deadline = time.monotonic() + READY_TIMEOUT
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        ping = _docker("exec", name, "redis-cli", "--no-auth-warning", "PING",
                       timeout=max(1, int(remaining)))
        if "PONG" in (ping.stdout or ""):
            return True
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


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
    # Only Docker's own absence phrasings, with the NOUN matched rather than the
    # prefix: a bare "no such" also matches "no such host" (a bad DOCKER_HOST)
    # and "no such file or directory" (a missing TLS file), and reporting those
    # as absence is how `down` ends up exiting 0 while a live container keeps
    # its port. Anything unrecognised fails CLOSED to `unknown`.
    return "absent" if re.search(r"no such (container|object)\b", err) else "unknown"


def _graph_count(name: str) -> int | None:
    """Graphs in a container, or None when docker could not be asked.

    None rather than 0: `graphs=0` is a claim about the container, and a failed
    `docker exec` is not evidence for it.
    """
    r = _docker("exec", name, "redis-cli", "--no-auth-warning", "GRAPH.LIST")
    if r.returncode != 0:
        return None
    return len([ln for ln in (r.stdout or "").splitlines() if ln.strip()])


def _published_port(name: str) -> tuple[bool, int | None]:
    """`(asked, port)` for the container's 6379 mapping.

    `asked` separates the two reasons a port can be missing, which are different
    facts: docker could not be asked (a failed or timed-out `docker port`),
    versus docker answered and there is no mapping. Collapsing them let a failed
    QUERY destroy a container that had just answered PING — on exactly the
    overloaded host this tool is built for.
    """
    r = _docker("port", name, "6379/tcp")
    if r.returncode != 0:
        return False, None
    for token in (r.stdout or "").replace("\n", " ").split():
        if ":" in token:
            try:
                return True, int(token.rsplit(":", 1)[1])
            except ValueError:
                pass
    return True, None


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
        asked, published = _published_port(name)
        if not asked:
            # Never remove a container on the strength of a failed QUERY: this
            # one may be perfectly healthy.
            raise SystemExit(
                f"test-lane: could not determine which port {name} publishes "
                f"(the `docker port` query failed) — refusing to treat that as "
                f"a broken container; retry, or check the daemon"
            )
        if published is None:
            raise SystemExit(f"test-lane: {name} is running but publishes no port")
        if port is not None and port != published:
            # Honour-or-refuse: the help says this flag publishes the lane on the
            # port you name. A running container's mapping cannot be changed, so
            # silently returning a DIFFERENT port would make the flag a lie (and
            # could send a test run at another lane's container).
            raise SystemExit(
                f"test-lane: {name} is already running on port {published} — "
                f"--port {port} cannot be applied to it; run `uv run python "
                f"tools/test_lane.py down` first to move it"
            )
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
        # A failed `rm` must be read and reported, not discarded: it leaves a
        # name that makes the `docker run` below fail with "name already in
        # use", which reads as a run problem rather than the removal problem
        # it is.
        outcome = _remove_and_describe(name)
        if outcome.startswith("FAILED"):
            raise SystemExit(
                f"test-lane: {name} exists, is not running, and could not be "
                f"removed — refusing to start over it ({outcome})"
            )

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
    asked, published = _published_port(name)
    if not asked or published is None:
        # This container is OURS and seconds old: if it cannot be addressed
        # there is nothing to hand back, so remove it rather than leave it
        # holding a reserved port. The message names the actual cause — a failed
        # query is not the same fact as "publishes no port".
        why = ("docker could not report its published port" if not asked
               else "it publishes no port")
        raise SystemExit(
            f"test-lane: {name} answered PING but {why}; "
            f"{_remove_and_describe(name)}"
        )
    return name, published


def _remove_and_describe(name: str) -> str:
    """Remove a container this tool just created, and say what actually
    happened — a cleanup whose result is unread is how a running container with
    a reserved port survives to make every later `start()` refuse.

    Guarded like every other removal intent, so "no removal bypasses the
    ownership check" is true of the whole module and not just of `stop()`.
    """
    if not is_managed(name):
        raise SystemExit(f"test-lane: refusing to remove unmanaged {name!r}")
    # The strongest form of "the target is not a parameter": this tool removes
    # exactly the container ITS OWN worktree derives, and nothing else — not
    # even a peer lane's `fdb-lane-*`. Ownership is then an invariant the code
    # enforces, instead of call-site discipline the suite cannot check.
    if name != lane_name():
        raise SystemExit(
            f"test-lane: refusing to remove {name!r} — this worktree's container "
            f"is {lane_name()!r}; a peer lane's database is not ours to delete"
        )
    rm = _docker("rm", "-f", name)
    return ("removed it" if rm.returncode == 0
            else "FAILED to remove it — check `docker ps -a`")


def stop() -> str:
    """Remove THIS LANE's container. Takes no argument, by design.

    Any way to name another container — `--name`, `--slug`, or a parameter —
    would be a way for one lane to delete another lane's isolated DB, so there
    is nothing to name. `is_managed()`
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
    if state == "unknown":
        # Refuse BEFORE the `rm`, exactly as `start()` does: with a wedged
        # daemon a blind `rm -f` pays two 60 s timeouts to learn nothing, and
        # the module's rule is that a failure is never read as permission.
        raise SystemExit(
            f"test-lane: cannot determine the state of {name} — is the docker "
            f"daemon running? (refusing to remove a container blindly)"
        )
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
    if state == "unknown":
        # A non-zero exit lets a script tell "could not ask" from an answer —
        # the rest of the tool fails closed, and `status` must too.
        return 1
    if state == "running":
        graphs = _graph_count(name)
        print(f"  graphs={'unknown' if graphs is None else graphs}",
              file=sys.stderr)
        if graphs is None:
            # `status`'s whole job is the ANSWER, so a failed query is not one —
            # the same rule this function already applied to `docker port`
            # below. (`up` differs: there the URI is the deliverable.)
            return 1
        asked, published = _published_port(name)
        if not asked:
            print("  port=unknown (the `docker port` query failed)",
                  file=sys.stderr)
            # Same rule as the `unknown` STATE above: a failed query is not an
            # answer, so the CLI verdict must not read as success.
            return 1
        if published is None:
            # `start()` calls this identical state a hard error ("running but
            # publishes no port"), and `status` cannot report a URI it does not
            # have — so it must not report success either.
            print("  port=none (the container publishes no mapping)",
                  file=sys.stderr)
            return 1
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
    # one, so accepting a flag either ignores would read as "do that".
    graph_only = argparse.ArgumentParser(add_help=False)
    graph_only.add_argument("--graph", default=DEFAULT_GRAPH,
                            help=f"graph/database name for the URI "
                                 f"(default {DEFAULT_GRAPH})")
    mapper = argparse.ArgumentParser(add_help=False, parents=[graph_only])
    mapper.add_argument("--port", type=int, default=None,
                        help="publish on this loopback port (default: first "
                             "free). Refused, not ignored or adopted, when "
                             "this lane's container already runs elsewhere")
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
