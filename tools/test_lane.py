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
from ``TORTOISE_DB_URI``: the graphs a lane mints live in a container this tool
refuses to address from any other lane, so nothing it does can contaminate
another lane and no peer's leftovers can contaminate it. The container is NOT self-cleaning — it is a
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

ENGINE PARITY — WHY THE ARGS ARE NOT COSMETIC (#8071)
-----------------------------------------------------
A private lane has to be a DROP-IN for the shared instance, not merely an
isolated one. It therefore starts its container with the SAME engine args the
canonical shared service uses (``LANE_FALKORDB_ARGS``, below: the value in
eldato ``operations/memory/docker-compose.yml``, the compose file the docker
lane's own docs name).

MEASURED (``falkordb/falkordb:latest``, 2026-10-16, ``GRAPH.CONFIG GET`` on a
container started each way, same image):

  * no ``FALKORDB_ARGS``: ``TIMEOUT=1000``, ``THREAD_COUNT=5`` — and a query
    taking ~3.9 s (``UNWIND range(1,200000000) AS x RETURN count(x)``) dies with
    ``redis.exceptions.ResponseError: Query timed out`` after 1.07 s;
  * ``FALKORDB_ARGS=THREAD_COUNT 8 TIMEOUT_MAX 30000``: ``TIMEOUT=0`` (no
    default per-query timeout), ``THREAD_COUNT=8`` — the same query completes
    in 3.87 s.

Re-verified on ``falkordb/falkordb-server:v4.20.6`` — the engine CI pins —
with the same three values, so the parity holds on both engines in play.

The server-side 1000 ms default is the reported symptom: on a loaded host
every index-birth query died as "timed out" while the CLIENT bound
(``TORTOISE_DB_SOCKET_TIMEOUT``, raised to 600 s) had no effect at all.
Without these args the private lane is STRICTER than the instance it replaces —
it fails queries the shared lane answers, which reads as a wedged lane when it
is only differently configured.

Note what the parity buys and costs, measured above: ``TIMEOUT=0`` means no
DEFAULT per-query timeout (the shared instance's own posture — this is not a
relaxation invented here), while ``TIMEOUT_MAX=30000`` still caps a timeout a
query explicitly requests. There is deliberately no env seam for it (unlike
``TORTOISE_TEST_LANE_IMAGE``): the args ARE the parity contract, and a per-lane
override is exactly how the private lane stops being a drop-in. Pinned by
``tests/test_test_lane_tool.py::test_start_passes_the_canonical_engine_args``.

IMAGE PIN — WHY THE DEFAULT IS A PINNED SERVER IMAGE (#8124)
--------------------------------------------------------------
The default used to be ``falkordb/falkordb:latest``. That floating tag now
resolves to engine 6.0.x, whose ``reply_compact`` PANICS the server on a query
shape this suite really issues: the process exits, so the lane does not fail
like a bad assertion — it fails like ``Connection closed by server``, which the
DB-unreachable session guard then reports as "no TORTOISE_DB_URI database could
be reached" (the #6073 wedge class), not as a server panic.

MEASURED (2026-10-19, one disposable container per reading, ``--compact`` — the
reply mode the client actually uses):

  * ``falkordb/falkordb:latest`` (module 60002) and ``falkordb/falkordb:6.0.1``
    (module 60001) — BOTH panic ``FalkorDB panic: panicked at
    src/reply.rs:655:58: called `Option::unwrap()` on a `None` value``, frames
    ``reply::reply_compact`` -> ``graph_core::execute_query``; container
    ``Exited (1)``.
  * the minimum trigger needs no data (the graph need not even exist):
    ``OPTIONAL MATCH (s:Session) RETURN count(s) AS n, s.harness AS h`` — an
    OPTIONAL MATCH whose projection mixes an AGGREGATE column with a node
    PROPERTY column. Each half alone is fine (``RETURN count(s)``,
    ``RETURN s.harness``, and ``MATCH ... RETURN count(s), s.harness`` all
    answer normally).
  * ``falkordb/falkordb-server:v4.20.6`` (module 42006) and ``v4.20.4``
    (module 42004) — answer it normally and stay ``Up``; the shipped
    reproducer ``tests/test_onboarding_w6_capture_disclosure.py::
    TestFirstCaptureDisclosure::test_off_switch_409_never_writes_the_checkpoint``
    PASSES (215.07 s) on v4.20.6 where it FAILS in ~5 s on both 6.0.x builds.

So the pin is ``falkordb/falkordb-server:v4.20.6`` — the SAME engine
``.github/actions/falkordb-provision`` gives the CI gate (#6386), which is the
other half of this tool's drop-in argument: a lane that runs the gate's engine
predicts the gate. Pinning the APP-image family alone was measured NOT to be
enough: every 6.0.x tag available carries the panic, so the issue body's
"pin the app image" remedy would have shipped a crashing default anyway.

WHAT THIS DOES AND DOES NOT PROTECT. It protects every lane that uses THIS tool
(the container is private, so a panic here kills only its own DB). That includes
a lane whose container ALREADY exists: the pin is checked against the image the
container was CREATED from, not only against the image a new one would use, so
an `Up` container born from a floating tag is refused rather than reused
(`_created_image`). It does NOT
protect a lane pointed at the long-lived SHARED ``falkordb`` container: that one
is DECLARED ``falkordb/falkordb:latest`` in eldato
``operations/memory/docker-compose.yml`` (out of this repo), and only its
CREATION TIME keeps 6.0.x away — measured 2026-10-19 it runs image
``d9d844a6c58c``, created 2026-08-03, graph module **42001**: what ``latest``
resolved to THEN, not the ``de76956472bb`` / module 60002 build the tag resolves
to now. The next ``docker compose up --force-recreate`` on that file arms the
panic for every peer lane, so the ``TORTOISE_TEST_LANE_IMAGE`` seam is
validated (``assert_pinned_image``) and a floating tag is REFUSED rather than
copy-pasted back in.

CONTRACT — THE TARGET IS NOT A PARAMETER
----------------------------------------
The container this tool acts on is ALWAYS ``fdb-lane-<sha1(worktree)[:10]>``,
derived inside ``start()``/``stop()`` from the worktree the command runs in.
There is no ``--name``, no ``--slug``, and no function argument that selects a
container: any override, however spelled, would be a way for one lane to delete
or silently adopt another lane's isolated DB, because a peer's slug is a
computable ``sha1(path)[:10]``. Every git call scrubs the whole ``GIT_*``
prefix (``_git_env``) for the same reason — ``GIT_DIR``, ``GIT_WORK_TREE`` or
``GIT_COMMON_DIR`` inherited from a git-hook or wrapper context would otherwise
retarget the CLI at a peer worktree with no flag involved.

Also fail-closed by design:
* the container is bound to ``127.0.0.1`` only;
* persistence is OFF (``--appendonly no --save ''``) — the data is disposable;
* ``is_managed()`` (the ``fdb-lane-*`` prefix, minus the protected shared
  instances) is what a removal intent is keyed on, and it is pinned by tests;
* ````docker ps -a`` is consulted for port collisions, and THAT SCAN is the
  load-bearing half of the guard for a FOREIGN container publishing on a
  NON-loopback interface: the host probe still binds ``127.0.0.1:<port>`` freely
  while ``0.0.0.0:<port>`` is taken. A PEER's container publishes on loopback
  (``-p 127.0.0.1:<port>:6379``), which the host probe refuses on its own
  (measured, Docker 29.4.0).
  ``-a`` adds nothing measured — neither a stopped nor a created container
  reports or reserves its published port (Docker 29.4.0) — and is kept only as a
  cheap hedge;
* ``DOCKER_HOST`` is deliberately NOT scrubbed (unlike every ``GIT_*``):
  a remote daemon is addressed as-is, so the printed
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
``eval``-ed; every diagnostic goes to stderr, so the captured value stays clean.
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
from datetime import datetime
from pathlib import Path

#: The lane's engine. Overridable so a lane can name a DIGEST or another
#: EXPLICIT version — never a floating tag (``assert_pinned_image`` refuses one;
#: #8124). See "IMAGE PIN" in the module docstring for the measurement that
#: moved this off ``falkordb/falkordb:latest``; the old comment here justified
#: the floating tag by a ``scripts/restore-smoke.sh`` precedent that does not
#: exist in this repo (``git ls-files`` matches no ``scripts/`` path).
#: Default = the engine the CI gate itself runs (#6386).
LANE_IMAGE_PIN = "falkordb/falkordb-server:v4.20.6"
IMAGE = os.environ.get("TORTOISE_TEST_LANE_IMAGE", LANE_IMAGE_PIN)

#: A tag that means "whatever upstream published last" (#8124). The rule is
#: the floating FAMILY, not an enumerated allow-list: upstream publishes
#: ``latest``, ``latest-6.0.1``, ``latest-alpine``, ``edge``, ``edge-c``,
#: ``edge-rs``, ``edge-ts``, ``edge-x64``, ``edge-rhel9-x64``,
#: ``edge-alpine-arm64v8`` … — and an enumerated set is always one publication
#: behind, so a lane exporting ``edge-ts`` slipped the seam that exists to stop
#: exactly that (measured 2026-10-19 on Docker Hub). ONE predicate owns the
#: verdict here and in ``tests/test_runner_ports_6673.py``'s lane sweep, which
#: classifies the same family; a second, divergent definition is how the two
#: guards disagree.
_FLOATING_TAG_RE = re.compile(r"(?:latest|edge)[a-z0-9._-]*\Z", re.IGNORECASE)


def is_floating_image(ref: str) -> bool:
    """True for ``latest*``, an ``edge*`` build, or NO tag at all.

    The refusal is the floating FAMILY — the spellings that mean "whatever
    upstream published last" — not an allow-list of literal tags: a tag that
    does not begin with ``latest``/``edge`` is an explicit selection. Untagged
    is included deliberately because Docker reads it as ``:latest``, and a
    digest is always a pin.
    """
    ref = ref.strip()
    if "@sha256:" in ref:               # a digest IS the pin
        return False
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last:
        return True                     # no tag = implicitly :latest
    return bool(_FLOATING_TAG_RE.match(last.rsplit(":", 1)[-1]))


def assert_pinned_image(image: str) -> str:
    """Return ``image`` unchanged if it is a PIN; refuse a floating tag.

    #8124: the seam exists so CI can pin a digest or compare two explicit
    versions — not so a lane can re-select ``latest`` by accident. A floating
    tag is not a cosmetic risk here: it resolves to an engine whose
    ``reply_compact`` panics the server on a query this suite issues, so the
    lane reports ``Connection closed by server`` instead of a test result (see
    "IMAGE PIN" in the module docstring). Refused, so the crash cannot be
    reintroduced by exporting the old default from a shell profile. The test
    is the floating FAMILY (see ``is_floating_image``): an explicit version —
    any tag that is not ``latest*``/``edge*`` — and a digest both pass.
    """
    if is_floating_image(image):
        raise SystemExit(
            f"test-lane: refusing the floating image {image!r} — "
            f"TORTOISE_TEST_LANE_IMAGE must name an explicit version tag or an "
            f"@sha256: digest (#8124). `latest` resolves to engine 6.0.x, whose "
            f"reply_compact PANICS the server on `OPTIONAL MATCH ... RETURN "
            f"count(s), s.harness` and exits it, so the lane reports "
            f"\"Connection closed by server\" instead of a test result. The "
            f"pinned default is {LANE_IMAGE_PIN} (the engine the CI gate runs)."
        )
    return image


#: #8071: the engine args the canonical SHARED docker-lane container runs with
#: (eldato ``operations/memory/docker-compose.yml`` -> ``FALKORDB_ARGS``). The
#: private lane REPLACES that instance for a concurrent lane, so it must be
#: configured like it — see "ENGINE PARITY" in the module docstring for the
#: measurement (without these the module's query TIMEOUT stays at 1000 ms and
#: the lane fails queries the shared instance answers).
LANE_FALKORDB_ARGS = "THREAD_COUNT 8 TIMEOUT_MAX 30000"
NAME_PREFIX = "fdb-lane-"

#: NOTE (#8180 review): there is deliberately NO age backstop constant here.
#: `TORTOISE_TEST_LANE_ORPHAN_MAX_AGE_H` used to exist and was read by nothing,
#: so it advertised a bound the code does not implement (and contradicted
#: `reap_orphans`'s docstring). #5084's owner ruling rejects "delete by age" and
#: requires fail-closed — when ownership cannot be determined, REPORT the age
#: and delete nothing — so the age arm is deliberately not implemented and the
#: dead knob is gone rather than left as a silent lie
#: (pinned by tests/test_test_lane_tool.py::test_no_inert_age_backstop_knob_exists).
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
#: particular peer lanes' private containers that must never be removed. It is
#: HAND-MAINTAINED, and NONE of its entries carries the NAME_PREFIX — so the
#: prefix rule refuses them anyway, and the list is what refuses a FUTURE name
#: that both carries the prefix and must be protected (pinned by
#: tests/test_test_lane_tool.py::test_a_protected_name_sharing_the_prefix_is_still_refused).
#: A container THIS tool creates does carry the prefix and reads as managed; the
#: list does not cover it, and its protection is `_remove_and_describe`'s refusal
#: unless the name equals `lane_name()`.
PROTECTED_NAMES = frozenset({
    "falkordb", "falkordb-16379", "fdb-6599", "w6213-fdb",
})
GRAPH_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+\Z")


def _git_env() -> dict[str, str]:
    """The environment every git call in this module must run under.

    The WHOLE ``GIT_*`` prefix is SCRUBBED rather than inherited: ``GIT_DIR``,
    ``GIT_WORK_TREE`` and ``GIT_COMMON_DIR`` each redirect which repository git
    addresses, so with one set (git-hook contexts, wrapper scripts) git answers
    about the OTHER tree — the tool would compute a peer lane's container name,
    or read a peer clone's worktree list, and act on it with no flag involved.
    Both git call sites (`repo_root`, `_worktree_slugs`) go through this ONE
    helper so the scrub cannot be applied to one and forgotten on the other
    (#8180 review: the new `_worktree_slugs` call had skipped it, and the
    ownership answer a delete is authorized from was being read from the wrong
    repository; scrubbing only ``GIT_DIR``/``GIT_WORK_TREE`` then left the same
    hole one variable over — ``GIT_COMMON_DIR`` retargets the read on its own).

    Scrubbing the prefix rather than an enumerated list matches the in-repo
    precedent (`tools/mergify_config_guard.py::_git_env`) and is deliberate:
    the cost is fail-CLOSED (the read fails, `_worktree_slugs` returns ``None``,
    and ``None`` authorizes no removal), never a silent retarget.
    """
    return {k: v for k, v in os.environ.items()
            if not k.startswith("GIT_")}


def repo_root() -> Path:
    """The git worktree this invocation belongs to.

    Every ``GIT_*`` is SCRUBBED (via `_git_env`) rather than inherited: with
    one set (git-hook contexts, wrapper scripts)
    `rev-parse` reports the OTHER tree, so the tool would compute a peer lane's
    container name and act on it with no flag involved.

    The fallback to ``Path.cwd()`` (git timed out, or is absent from PATH) is
    deliberate but DEGRADES the contract: invoked from a subdirectory it yields
    a different slug than the worktree root, so ``down`` would report ``absent``
    while the real lane container stays behind (and ``uri`` would start a second
    one). The stderr warning is the signal, not a detail — run from the worktree
    root, or restore git, and the derived name is the worktree's again.
    """
    env = _git_env()
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
    # The SCAN is the load-bearing guard, but for FOREIGN containers: one that
    # publishes on a NON-loopback interface (`-p 16478:6379` -> `0.0.0.0:16478`)
    # leaves the host probe free to bind 127.0.0.1:16478, so only this table shows
    # it. A PEER's container publishes on loopback (`-p 127.0.0.1:<port>:6379`,
    # what `start()` asks for), which the host probe DOES refuse — measured,
    # Docker 29.4.0. `-a` adds nothing measured — a stopped or created container
    # reports no `Ports` — so this covers RUNNING containers, and `-a` is kept only
    # as a cheap hedge.
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


def _ping_over_host(port: int, timeout: float) -> bool:
    """PING the server through the container's PUBLISHED HOST PORT (#8180).

    `docker exec` asks the server from INSIDE the container, which answers even
    when the host mapping is unusable — so a container whose published port was
    unreachable was handed to a lane, and the lane then failed against a URI
    that looked usable. Measured shape: `PING` timing out from the host while
    the exec probe still returned `PONG`. A lane uses the HOST path, so the host
    path is what "ready" has to mean.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"*1\r\n$4\r\nPING\r\n")
            return b"PONG" in s.recv(64)
    except OSError:
        return False


def _wait_ready(name: str, host_port: int | None = None) -> bool:
    """Wait for the container's server to answer PING — bounded in WALL TIME.

    Seconds, not iterations: an iteration count is not a bound when each
    iteration awaits a docker call of its own. Every call's timeout comes from
    the REMAINING budget, so the deadline is not overshot by a full per-call
    timeout.

    When `host_port` is given the PING must ALSO succeed over the published host
    port (#8180). Both are required: the host probe is the one that matters to a
    caller, and the exec probe reports whether the server or the mapping is the
    broken half (a caller may ignore the distinction; the refusal message in
    `start()` is what carries it).

    The host probe's own timeout comes OUT of the remaining budget, and the
    budget is re-derived after it: a host port that black-holes (a filtered/DROP
    mapping) burns its whole slice, so reusing the pre-probe `remaining` for the
    exec call charged one iteration up to 2x the deadline (#8180 review).
    """
    deadline = time.monotonic() + READY_TIMEOUT
    host_ok = host_port is None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if not host_ok:
            host_ok = _ping_over_host(host_port, max(1.0, remaining))
            # Re-derive (and re-check) after the probe, so its slice is charged
            # to this iteration's budget instead of being spent on top of it.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
        ping = _docker("exec", name, "redis-cli", "--no-auth-warning", "PING",
                       timeout=max(1, int(remaining)))
        if host_ok and "PONG" in (ping.stdout or ""):
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


def _created_image(name: str) -> str | None:
    """The image the container was CREATED from, or None if it cannot be read.

    #8124: `docker run` is the ONLY path that names an engine, and `docker start`
    never re-resolves an image — so a container created from the old floating
    `falkordb/falkordb:latest` keeps the panicking 6.0.x engine for life. The
    reuse path could not see that: it asked for the container's *state*, which
    cannot answer "which engine is this?". The health gate cannot see it
    either: 6.0.x answers `PING` fine and only dies on the aggregate+property
    projection, so the lane would be handed a "ready" DB that exits mid-run as
    `Connection closed by server` — the exact #8124 symptom.

    None rather than "" for the unreadable case, for the same reason
    `_graph_count` returns None: a failed query is not evidence about the
    container, and callers must not read it as one.
    """
    r = _docker("inspect", "--format", "{{.Config.Image}}", name)
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


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
    # #8124: validated FIRST, for the same reason the port is — a floating tag
    # must fail before the orphan sweep and before any container is touched.
    image = assert_pinned_image(IMAGE)
    # #8180: the call site that makes ownership reaping actually BITE. Every lane
    # that starts sweeps containers whose owning worktree is gone, so a lane
    # closed without `down` (measured: `cmux workspace close` never runs it)
    # leaves a container that dies at the NEXT start instead of surviving until
    # someone notices. Reported, and never fatal: a sweep that cannot run must
    # not stop a lane from starting.
    if os.environ.get("TORTOISE_TEST_LANE_REAP", "1") == "1":
        for _line in reap_orphans():
            print(_line, file=sys.stderr)
    name = lane_name()
    state = container_state(name)
    if state == "running":
        # #8124: the reuse path must not hand back an engine the pin never
        # approved. `assert_pinned_image` above validates what we WOULD create;
        # an existing container keeps whatever image it was created from, and
        # Docker never re-resolves it. Refused rather than recreated: recreating
        # would destroy the test graphs of the run that is using the container
        # (the same reason the `--port` branch below refuses instead of
        # recreating), and a lane container is disposable only by its OWNER's
        # decision — `down`, then retry.
        created = _created_image(name)
        if created is None:
            raise SystemExit(
                f"test-lane: {name} is running but the image it was created "
                f"from could not be read (the `docker inspect "
                f"{{{{.Config.Image}}}}` query failed or returned nothing), so "
                f"the pinned engine {image!r} cannot be confirmed — refusing to "
                f"hand back a database of unknown engine (#8124). Inspect it "
                f"with `docker container inspect -f '{{{{.Config.Image}}}}' "
                f"{name}`, or run `uv run python tools/test_lane.py down` and "
                f"retry"
            )
        if created != image:
            raise SystemExit(
                f"test-lane: {name} was created from {created!r}, not the "
                f"pinned {image!r} (#8124) — `docker run` is the only path that "
                f"names an engine and `docker start` never re-resolves an "
                f"image, so this container keeps the engine it was born with "
                f"for life, and a floating tag resolves to engine 6.0.x whose "
                f"reply_compact PANICS the server mid-run (`Connection closed "
                f"by server`, not a test result). This container is disposable "
                f"— run `uv run python tools/test_lane.py down` and retry to "
                f"recreate it on the pin"
            )
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
        if not _wait_ready(name, published):
            raise SystemExit(
                f"test-lane: {name} is running but never answered PING over its "
                f"published host port {published} within {READY_TIMEOUT}s — it "
                f"may be wedged: run `uv run python "
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
        # #8180: `--init` puts a real init at container PID 1. Without it PID 1
        # IS redis, which never reaps its orphaned children — so a container can
        # reach a state where `docker rm -f` cannot kill it. Measured on #2653:
        # "container <id> is zombie and can not be killed. Use the `--init`
        # option", leaving a container no `down()` could remove.
        "run", "-d", "--init", "--name", name,
        "-p", f"127.0.0.1:{chosen}:6379",
        "-e", "REDIS_ARGS=--appendonly no --save ''",
        # #8071: engine parity with the SHARED instance (see the module
        # docstring's ENGINE PARITY section).
        "-e", f"FALKORDB_ARGS={LANE_FALKORDB_ARGS}",
        image,
        timeout=IMAGE_PULL_TIMEOUT,   # the image may need pulling first
    )
    if r.returncode != 0:
        # #8180 task 3: a failed `docker run` can still have CREATED a
        # container — `-d` creates it, then starting it can fail — and it then
        # sits in `Created` holding a reserved port for good. `docker run`
        # prints the container ID on success, so RETAIN it and clean up by ID
        # when we have one: the ID addresses exactly the container this call
        # created, even if something else raced the name. When no ID was
        # reported there is nothing to target by ID, so fall back to the name
        # we asked for, which is ours by construction.
        cid = (r.stdout or "").strip()
        detail = (r.stderr or "").strip() or (r.stdout or "").strip()
        if cid:
            gone = _docker("rm", "-f", cid)
            outcome = ("removed the container it created" if gone.returncode == 0
                       else f"could NOT remove {cid} — check `docker ps -a`")
        elif is_managed(name):
            gone = _docker("rm", "-f", name)
            outcome = ("removed the container it created (by name)"
                       if gone.returncode == 0
                       else f"could NOT remove {name} — check `docker ps -a`")
        else:
            outcome = "no container ID was reported, so there was nothing to clean up"
        raise SystemExit(f"test-lane: docker run failed: {detail} ({outcome})")

    if not _wait_ready(name, chosen):
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


def _worktree_slugs() -> dict[str, str] | None:
    """`slug -> worktree path` for every worktree git still knows about.

    `None` means the ownership record could NOT be read. That is deliberately
    distinct from `{}` ("git answered, and nothing claims any container"): the
    first must never authorize a delete, the second may. #8180 fail-closed rule.
    """
    try:
        r = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_root()), capture_output=True, text=True,
            timeout=GIT_TIMEOUT, env=_git_env(),
        )
    except (OSError, subprocess.SubprocessError):
        # `repo_root()` already treats these two conditions this way: git may be
        # absent (OSError) or hung (TimeoutExpired, a SubprocessError). The sweep
        # runs on EVERY `start()` by default, so an exception here would make a
        # lane unable to start because a bookkeeping read failed — the opposite
        # of the contract ("a sweep that cannot run must not stop a lane from
        # starting"). Unreadable is `None`, and `None` authorizes no removal.
        return None
    if r.returncode != 0:
        return None
    found: dict[str, str] = {}
    for line in (r.stdout or "").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
            found[slug_for(path)] = path
    return found


def _started_epoch(name: str) -> float | None:
    """When the container last started, in epoch seconds; `None` if unreadable.

    Unreadable must not read as "old": the age is REPORTED only and is never on
    its own a reason to delete (see `reap_orphans`), so a failed query must not
    be rendered as an age that a reader could mistake for one.
    """
    r = _docker("inspect", "--format", "{{.State.StartedAt}}", name)
    if r.returncode != 0:
        return None
    raw = (r.stdout or "").strip()
    if not raw:
        return None
    try:
        # Docker emits RFC3339 with nanoseconds, e.g. 2026-10-11T12:00:00.123456789Z
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def reap_orphans() -> list[str]:
    """Remove `fdb-lane-*` containers whose OWNING WORKTREE IS GONE (#8180).

    Ownership is the mechanism, not age: a lane container is named after
    `slug_for()` of the worktree that owns it, so the container's owner is
    recoverable from its own name and the reaping decision is a lookup rather
    than a guess. This tool can therefore only ever remove a container whose
    owner it has POSITIVELY established is gone — never a peer lane's, and never
    this worktree's own (`stop()` is the only thing that removes that one).

    Age is not consulted and there is deliberately NO age backstop here. The
    measured defect is a container that outlives its owner, and a clock firing on
    a live owner's database would be a far worse bug than the leak it fixes —
    the rule the whole module defends.

    Fail-closed: if the worktree list or `docker ps` cannot be read, nothing is
    removed and the reason is reported. Returns one line per container this
    function ACTED on or decided to KEEP; a name refused by `is_managed()` is not
    reported (it was never a candidate), so a caller reports what actually
    happened instead of counting.
    """
    slugs = _worktree_slugs()
    if slugs is None:
        return ["reap: skipped — `git worktree list` failed, so ownership "
                "cannot be determined; NOTHING was removed (fail-closed)"]
    listed = _docker("ps", "-a", "--filter", f"name={NAME_PREFIX}",
                     "--format", "{{.Names}}")
    if listed.returncode != 0:
        return ["reap: skipped — `docker ps` failed; NOTHING was removed "
                "(fail-closed)"]
    mine = lane_name()
    out: list[str] = []
    for name in sorted(n for n in (listed.stdout or "").split() if n):
        if not is_managed(name):
            continue
        if name == mine:
            out.append(f"reap: kept {name} — this is THIS worktree's container")
            continue
        owner = slugs.get(name[len(NAME_PREFIX):])
        if owner is not None:
            # The owner IS determined, so ownership alone decides — and a live
            # owner is never reaped, at any age.
            try:
                present: bool | None = Path(owner).is_dir()
            except OSError:
                # pathlib suppresses ENOENT/ENOTDIR/EBADF/ELOOP but NOT EACCES,
                # so an inaccessible worktree path raises. "Cannot tell" is the
                # REPORT-ONLY case: it must never be read as "the owner is gone".
                present = None
            if present is None:
                out.append(f"reap: kept {name} — its owning worktree {owner} "
                           f"could not be examined (fail-closed, report only)")
                continue
            if present:
                out.append(f"reap: kept {name} — its owning worktree exists ({owner})")
                continue
            rm = _docker("rm", "-f", name)
            out.append(f"reap: {'removed' if rm.returncode == 0 else 'FAILED to remove'} "
                       f"{name} — its owning worktree {owner} is gone")
            continue
        # Owner INDETERMINATE: no worktree in THIS clone claims it. That is not
        # proof of death — the container may belong to a different clone of the
        # repo, whose live lane this tool cannot see — so ownership cannot
        # authorize a delete here.
        #
        # #5084 owner ruling: fail-closed means REPORT ONLY. When ownership
        # cannot be determined, do NOT delete. The age is reported and is NEVER
        # on its own a reason to delete — applying a clock to a slug nothing
        # claims is exactly the "delete by age" option that was rejected.
        age = _started_epoch(name)
        age_txt = ("start time unreadable" if age is None
                   else f"{(time.time() - age) / 3600.0:.1f}h old")
        out.append(f"reap: kept {name} — no owner here, so ownership is NOT "
                   f"determined ({age_txt}); report only, fail-closed")
    return out


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
        detail = (r.stderr or r.stdout).strip()
        # #8180 defect 1: a container whose PID 1 is a zombie cannot be killed —
        # measured on #2653: "container <id> is zombie and can not be killed.
        # Use the `--init` option", after which no `down()` could remove it.
        # Escalate ONCE (a kill then a remove clears the remaining state on most
        # daemons), and if that fails report the ZOMBIE case precisely: the
        # remedy is not "try again", it is the container runtime, and a generic
        # "failed to remove" hides that.
        if "zombie" in detail.lower():
            _docker("kill", name)
            again = _docker("rm", "-f", name)
            if again.returncode == 0:
                return "removed after a kill (its PID 1 was a zombie)"
            detail = (again.stderr or again.stdout).strip() or detail
            raise SystemExit(
                f"test-lane: {name} is UNREMOVABLE — its PID 1 is a zombie, "
                f"which `docker rm -f` cannot kill (#8180, #2653), and a kill "
                f"did not clear it either. New containers cannot reach this "
                f"state (creation now passes `--init`), but this one needs the "
                f"container runtime restarted before anything can remove it. "
                f"Reported, not worked around. Last error: {detail}"
            )
        # Never report a removal that did not happen.
        raise SystemExit(
            f"test-lane: failed to remove {name}: {detail}"
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
