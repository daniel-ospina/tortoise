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
``TORTOISE_TEST_DOCKER_PORT`` (default 6379) and a password, so a lane that
exported only the URI still addressed the SHARED instance and its graphs still
accumulated there — and the probe against THIS container could not even
authenticate, because it starts without ``requirepass`` while the seam's default
credential is ``falkordb`` (measured: `AuthenticationError: AUTH <password>
called without any password configured`, which a module's `except Exception`
turned into "Live FalkorDB (Docker) not available" → 14 skipped, rc=0).
``uri`` therefore exports BOTH seam variables as well:
``TORTOISE_TEST_DOCKER_PORT`` (the container's assigned port) and
``TORTOISE_TEST_DOCKER_PASSWORD=''`` (this container has no password configured,
and `tests/_live_utils.docker_password` reads that seam). Still out of reach:
sites that spell the shared URI LITERALLY (`docker://:falkordb@localhost:6379/…`)
instead of going through ``_live_utils``; the seam cannot reach a hardcoded
string, so those stay on the shared instance until they are converted.

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

MEMORY — TWO BOUNDS, IN ORDER (#7357)
------------------------------------
The container is created with BOTH a cgroup cap (``--memory`` and ``--memory-swap``
set to the same value, ``CONTAINER_MEMORY_ENV``, default ``2g``) and a REDIS-side
dataset ceiling BELOW it (``--maxmemory``, ``DATASET_CEILING_RATIO`` of the cap,
pinned ``--maxmemory-policy noeviction``). They are not alternatives; the ORDER
between them is the point.

``--maxmemory`` alone is the WRONG bound and the cgroup cap alone is the wrong
DEATH. ``--maxmemory`` is a REDIS-side limit: with ``HostConfig.Memory=0`` the
container's real ceiling is the HOST's, and the host's ceiling is swap — so one
runaway instance pages the whole box for minutes and starves every neighbour,
including the canonical graph instance, whose process then goes away and is
restarted by its own ``restart: always`` policy, forever (#7357 measured exactly
that). That is what the cgroup cap fixes: it converts a fleet-wide outage into
ONE container dying alone, in its own cgroup, in seconds. But a cgroup kill is a
``SIGKILL``: the container VANISHES mid-run, taking every in-flight read with it,
and the caller is left holding a bare transport error that says nothing about
memory — #7357's own defect ("the failure surfaces as … which misdescribes it")
reproduced by this tool's own defaults. MEASURED on this box 2026-10-21: TEN
lane containers had been killed at exactly their cap (``OOMKilled=true
ExitCode=137``), while ordinary lanes peak at 156 MiB / 390 MiB / 37 MiB
(``/sys/fs/cgroup/memory.peak``) — so the 2g default is NOT too small for a
normal suite run, and the defect is the failure MODE at the cap, not the value.

The dataset ceiling fires first, and it is an ANSWERED refusal: ``OOM command
not allowed when used memory > 'maxmemory'``, an ordinary ``ResponseError``
(``redis.exceptions.OutOfMemoryError`` on the pinned client) that
``retry.is_instance_unavailable`` correctly refuses to call an instance outage.
The instance SURVIVES with its DATA INTACT and the reason is IN the message.
⛔ MEASURED, and it corrects the obvious reading: a lane's READ path is
``GRAPH.QUERY``, which FalkorDB registers as a deny-OOM command, so a read at
the ceiling is refused TOO (a read-only ``MATCH (n) RETURN count(n)`` returned
``OutOfMemoryError`` against the pinned engine). Reads are NOT a safe haven at
the ceiling — the win is against the ``SIGKILL``, not for reads: the container
and its dataset are still there, and the message says why.
The cgroup cap stays as the hard backstop for what ``maxmemory`` does
not bound — ``used_memory`` is the dataset, while RSS adds allocator overhead,
fragmentation, client/replication buffers and fork copy-on-write, so RSS is
strictly larger. ``noeviction`` is pinned, not inherited: under an eviction
policy Redis would silently DROP graph keys to stay under the ceiling, which on
a graph instance is silent data loss — worse than any refusal.

It also makes a cgroup death LEGIBLE: a container the kernel (or this cap)
killed used to read as "it may be wedged: run down and retry", so the operator
retried into the same death and never learned the cause — ``_termination_report``
now names ``OOMKilled``/``ExitCode``/``RestartCount`` when docker reports them.
Two limits, stated: an EXISTING container keeps the limits it was created with —
docker cannot change them on ``start``, so the reuse path WARNS when that
container has no cap and ``down`` + ``start`` recreates it (as with the image
pin) — and a value above the host's real headroom still dies (it just dies
alone). A container that has already STOPPED has those facts read and reported
on stderr BEFORE it is removed: this tool sets no ``--restart`` policy, so a
kill leaves the container ``exited`` and the stale-container branch is the one
path a memory kill actually takes.

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
  load-bearing half of the guard in TWO cases: a FOREIGN container publishing
  on a NON-loopback interface, where the host probe still binds
  ``127.0.0.1:<port>`` freely while ``0.0.0.0:<port>`` is taken; and a STOPPED
  container, whose host port is free at the OS level (``bind`` succeeds) but
  which ``docker run -p`` refuses because docker still holds the mapping.
  A PEER's RUNNING container publishes on loopback
  (``-p 127.0.0.1:<port>:6379``), which the host probe refuses on its own
  (measured, Docker 29.4.0). ``-a`` is therefore NOT a hedge: a stopped
  container DOES report its ``host:port->6379/tcp`` mapping there (measured
  2026-10-20, Docker 29.8.2 — ````docker ps -a --format '{{.Names}}\t{{.State}}\t
  {{.Ports}}'`` showed `exited` containers with their published ports), which is
  the only thing that refuses the port and the reason a range full of stopped
  containers exhausts (67 of 110 held by EXITED containers on that reading);
* ``DOCKER_HOST`` is deliberately NOT scrubbed (unlike every ``GIT_*``):
  a remote daemon is addressed as-is, so the printed
  ``127.0.0.1:<port>`` URI then names THAT daemon's loopback, not this host's —
  a warning is printed when ``DOCKER_HOST`` is set;
* a FAILED ``docker ps -a`` scan ABORTS port selection instead of reporting
  "nothing is published" (a failure is never read as permission).

USAGE
-----
    uv run python tools/test_lane.py run -- tests/ -q   # start + export + run
    uv run python tools/test_lane.py down               # remove it

``run`` is the spelling to prefer: it is the one place the three seam variables
(#5084) are APPLIED rather than printed, so no caller has to remember them, and
bare ``run`` targets ``tests/`` rather than the rootdir. The two-step form below
is for a shell that wants the variables itself:

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
import json
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
#: #7357 — the KERNEL (cgroup) memory cap every lane container is created
#: with, as a docker memory spec. The DEFAULT is PROVISIONAL, exactly like the
#: CI seam's: it is taken from the SHARED instance's own ceiling (`used_memory
#: 2.0 GiB of maxmemory 2.0 GiB`), but that ceiling does not justify it.
#: `maxmemory` bounds the Redis DATASET, and the shared instance REFUSES WRITES
#: at it — the lane now does the same, at `DATASET_CEILING_RATIO` of this cap
#: (`container_dataset_ceiling`), so the cgroup cap is the BACKSTOP and not the
#: only ceiling: `HostConfig.Memory` bounds RSS, which is dataset plus allocator
#: overhead, fragmentation, client/replication buffers and fork copy-on-write.
#: RSS is strictly larger than `used_memory`, and `--memory-swap` is EQUAL, so
#: there is no swap headroom to absorb the margin — which is why the two bounds
#: are ORDERED (dataset ceiling first, cgroup cap second) and why a lane that
#: overshoots is REFUSED rather than killed.
#: SETTLED BY MEASUREMENT (done, 2026-10-21): cgroup v2 `memory.peak` on three
#: live lane containers read **156 MiB / 390 MiB / 37 MiB** against this 2g cap,
#: so this default is comfortably clear of a normal suite run and stands. What
#: the same measurement DID find is ten lane containers killed at exactly the
#: cap (`OOMKilled=true ExitCode=137`): the value is right, the failure MODE was
#: not — hence the dataset ceiling below it. Re-measure the peak (and re-derive
#: this number) if the suite's working set grows; do not raise the cap to fix a
#: runaway, because a higher cap only makes a bigger SIGKILL.
#:
#: WHY A KERNEL CAP AND NOT ONLY `maxmemory`: `--maxmemory` is a REDIS-side
#: limit. Without `HostConfig.Memory` the container's real ceiling is the
#: HOST's, and the host's ceiling is swap — so a runaway instance pages the
#: whole box for minutes and starves every neighbour, including the canonical
#: graph instance, whose process then goes away and is restarted by its
#: `restart: always` policy, forever (#7357: `ExitCode=0`, `OOMKilled=false`,
#: `Memory=0`, `RestartCount` climbing). A cgroup cap converts that fleet-wide
#: outage into ONE container dying alone, in seconds, inside its own cgroup.
#:
#: `--memory-swap` is set EQUAL to `--memory` on purpose: docker's default is
#: 2x memory, i.e. the container may swap, and a swapping FalkorDB is the
#: 96-146 s RDB reload the issue records rather than a fast failure.
CONTAINER_MEMORY_ENV = "FALKORDB_CONTAINER_MEMORY"
DEFAULT_CONTAINER_MEMORY = "2g"
#: #7357 — the REDIS-side dataset ceiling as a FRACTION of the cgroup cap.
#:
#: Three quarters, so the ceiling leaves a quarter of the cap for exactly what
#: ``maxmemory`` does NOT bound: allocator overhead and fragmentation (MEASURED:
#: ``used_memory`` stayed ~64 MiB above baseline after probe graphs were
#: deleted), plus client/replication buffers.
#: ⛔ NOT ``GRAPH.COPY``/``BGSAVE`` — a first draft of this note named them and
#: the measurement refutes it: ``GRAPH.COPY`` of a 200k-node graph added **+78 MB
#: to ``used_memory``** (vs +16.8 MB for the graph itself), i.e. it is an
#: in-process copy counted INSIDE the ceiling, and ``BGSAVE`` cannot fork at all
#: here because the container runs `--save ''`. They are still a reason to keep a
#: margin, but as headroom used *by this container*, not as RSS the ceiling
#: ignores. It must stay STRICTLY BELOW the cap: a ``--maxmemory`` at or above
#: it is no ceiling at all — RSS would reach the cgroup limit first and the kernel would
#: ``SIGKILL`` the container, which is the death this ordering exists to replace.
#: MEASURED ordinary peaks are 37-390 MiB against a 2g cap, so 1536 MiB is ~4x
#: headroom over a normal suite run and can only fire on a genuine runaway.
DATASET_CEILING_RATIO = 3 / 4
#: The docker memory-spec grammar (`512m`, `2g`, `1.5g`, `2gb`, `2048mb`,
#: `1t`, bytes) — the LONG form (`kb`/`mb`/`gb`) is valid to docker, so refusing it
#: would refuse the natural spelling of the very remedy these messages print
#: ("raise FALKORDB_CONTAINER_MEMORY"). Validated at the seam because the value
#: is interpolated into an argv: a typo must fail loudly here, not as an opaque
#: `docker run` refusal later. Group 1 is the NUMBER, so positivity is read
#: without re-deriving the unit (#7357 review).
_MEMORY_SPEC_RE = re.compile(r"\+?([0-9]+(?:\.[0-9]+)?)(?:[bkmgt]|[kmgt]b|[kmgt]ib)?\Z")


#: Byte multipliers for the units docker honours: `b`, `k`/`kb`/`kib`,
#: `m`/`mb`/`mib`, `g`/`gb`/`gib`, `t`/`tb`/`tib`. Positivity is decided on the
#: BYTE COUNT, not the number: docker truncates the spec to an int64, so
#: `0.0000000001g` and `0.1b` install as `HostConfig.Memory=0` — the UNCAPPED
#: container this seam exists to prevent, reached through a value that reads as
#: positive (#7357 review, measured).
_MEMORY_UNIT_BYTES = {
    "": 1, "b": 1,
    "k": 1 << 10, "kb": 1 << 10, "kib": 1 << 10,
    "m": 1 << 20, "mb": 1 << 20, "mib": 1 << 20,
    "g": 1 << 30, "gb": 1 << 30, "gib": 1 << 30,
    "t": 1 << 40, "tb": 1 << 40, "tib": 1 << 40,
}

#: Redis's spec grammar — a DIFFERENT grammar from docker's, and not only in
#: case. Group 1 is the number, group 2 the optional unit; there is NO fraction
#: and no leading `+`, because `memtoull` reads digits and then treats the rest as
#: a unit, so Redis itself refuses `1.5g` and `+2g`. Matching runs on the
#: normalised (stripped, unquoted, lowercased) token, so the unit can be sliced
#: from that same string.
_REDIS_SPEC_RE = re.compile(r"([0-9]+)(b|k|kb|m|mb|g|gb)?\Z")

#: Byte multipliers for the units Redis's `memtoull` honours (redis.conf
#: §Memory units): `1k` = **1000**, `1kb` = 1024, `1m` = 1000000, `1mb` =
#: 1024*1024, `1g` = 1000000000, `1gb` = 1024*1024*1024. The BARE letters are
#: DECIMAL; only the `b`-suffixed forms are binary. There is no `t` and no
#: `kib`/`mib`/`gib` — Redis refuses those, so they stay unreadable here.
#:
#: ⛔ THIS TABLE EXISTS because `_spec_bytes` (docker's grammar, 1024-based for
#: BOTH spellings) was reading a value out of a container's `REDIS_ARGS`
#: (#7357 review, round 5). `--memory 2g --maxmemory 2g` is CORRECTLY bounded —
#: Redis's 2,000,000,000 ceiling fires ~147 MiB before the 2,147,483,648 cgroup
#: cap — but docker's multipliers made that token 2,147,483,648, so the note
#: declared it "at or ABOVE its own cgroup cap" and prescribed `down`, which
#: DELETES that healthy container's graphs.
_REDIS_UNIT_BYTES = {
    "": 1, "b": 1,
    "k": 1000, "kb": 1 << 10,
    "m": 1000 ** 2, "mb": 1 << 20,
    "g": 1000 ** 3, "gb": 1 << 30,
}

def _spec_is_positive(spec: str) -> bool:
    """Whether *spec* is a POSITIVE byte count.

    `0` (and `0g`, `0.0g`, …) is not a small cap — to docker it means NO limit
    (`HostConfig.Memory=0`), i.e. exactly the uncapped container #7357 is
    about, reached by the one value a reader would type to "disable" the cap.
    The grammar alone accepts it, so the ZERO case is refused separately.

    The test is on BYTES, not on the number: docker truncates the parsed spec to
    an int64, so `0.0000000001g` (0.107 B) and `0.1b` install as Memory=0 —
    a positive NUMBER that becomes an UNCAPPED container (#7357 review).

    The unit is sliced from the SAME normalised string the regex matched, as in
    `_spec_bytes`: slicing the RAW spec made a padded spelling (`" 2g "`) match
    the grammar and then miss the unit table (#7357 review, nit).
    """
    normalised = spec.strip().lower()
    m = _MEMORY_SPEC_RE.match(normalised)
    if not m:
        return False
    return int(float(m.group(1)) * _MEMORY_UNIT_BYTES[normalised[m.end(1):]]) >= 1


def _spec_bytes(spec: str) -> int | None:
    """The BYTE count a DOCKER memory spec denotes, or None if it is not one.

    ⛔ DOCKER'S GRAMMAR ONLY. Every spelling is 1024-based to docker, but to
    Redis the bare `k`/`m`/`g` are DECIMAL — so a value read out of a
    container's `REDIS_ARGS` belongs in `_redis_spec_bytes`, not here (#7357
    review, round 5). BOTH call sites here pass docker specs:
    `container_dataset_ceiling` and `_termination_report`'s normalised
    `wanted`. `container_memory_cap`/`_memory_cap_args` validate the grammar
    with `_MEMORY_SPEC_RE`/`_spec_is_positive` and never count bytes, so they
    are not call sites (#7357 review, nit).

    CASE-INSENSITIVE on purpose: docker's grammar is lowercase, and the cap is
    normalised before it gets here (`container_memory_cap`), so a permissive
    read costs nothing and keeps a caller from re-deriving the rule.

    The unit is sliced from the SAME normalised string the regex matched —
    slicing the raw spec made every padded spelling (`" 2g"`) match the grammar
    and then miss the unit table, reporting a readable spec as "unreadable"
    (#7357 review).
    """
    normalised = spec.strip().lower()
    m = _MEMORY_SPEC_RE.match(normalised)
    if not m:
        return None
    return int(float(m.group(1)) * _MEMORY_UNIT_BYTES[normalised[m.end(1):]])


def _redis_spec_bytes(spec: str) -> int | None:
    """The BYTE count REDIS reads *spec* as, or None if it is not a Redis spec.

    This is what a `--maxmemory` token in a container's `REDIS_ARGS` must be
    judged with, and it is deliberately NOT `_spec_bytes`: docker's multipliers
    are 1024-based for both spellings, Redis's bare letters are decimal, so the
    two disagree by ~4.9% (`m`) / 7.4% (`g`) on the very spelling a human writes
    when making the numbers match (`--memory 2g --maxmemory 2g`). Read with
    docker's table, that healthy container computes a "ceiling" equal to its own
    cgroup cap and is reported as "the ceiling never fires … run `down`" — a
    false statement whose remedy DELETES its graphs (#7357 review, round 5).

    Case-INSENSITIVE (MEASURED on the pinned engine: `CONFIG SET maxmemory 1GB`
    → 1073741824) and QUOTE-TOLERANT: `REDIS_ARGS` reaches Redis through a word
    split, so `--maxmemory '1536mb'` is a plausible spelling whose quotes Redis's
    own `sdssplitargs` strips. Reporting either as "unreadable" is what sends an
    operator after `down` on a correctly bounded container.
    """
    normalised = spec.strip().strip("'\"").strip().lower()
    m = _REDIS_SPEC_RE.match(normalised)
    if not m:
        return None
    return int(m.group(1)) * _REDIS_UNIT_BYTES[m.group(2) or ""]


def container_memory_cap() -> str:
    """The kernel memory cap for the container this tool starts (#7357).

    Read at CALL time (not import time) so a lane can raise it per run, and
    validated so a malformed value cannot reach `docker run`. Fail-closed: an
    unusable value is refused rather than silently dropped, because a dropped
    cap is exactly the uncapped container this exists to prevent.
    """
    raw = (os.environ.get(CONTAINER_MEMORY_ENV)
           or DEFAULT_CONTAINER_MEMORY).strip().lower()
    if not _MEMORY_SPEC_RE.match(raw) or not _spec_is_positive(raw):
        raise SystemExit(
            f"test-lane: {CONTAINER_MEMORY_ENV}={raw!r} is not a POSITIVE "
            f"docker memory spec (e.g. 512m, 2g, 1.5g; `0` means UNLIMITED to "
            f"docker) — refusing to start the lane with an unusable cap "
            f"(default {DEFAULT_CONTAINER_MEMORY})"
        )
    return raw


def _memory_cap_args(cap: str | None = None) -> list[str]:
    """`--memory`/`--memory-swap` for `docker run` — the pair, never one.

    *cap* is the value `start()` already validated at the TOP of the call (a
    guard is only as good as its ordering — see the ordering note there); when
    omitted it is read and validated here, which is what a direct caller (and
    the seam test) wants.
    """
    cap = cap if cap is not None else container_memory_cap()
    return ["--memory", cap, "--memory-swap", cap]


def container_dataset_ceiling(cap: str | None = None) -> str:
    """The REDIS-side `--maxmemory` for the lane container (#7357).

    ``DATASET_CEILING_RATIO`` of the cgroup cap, floored to whole MiB so the
    value Redis receives is exactly the value this module computed. Fail-closed
    on the two values that would silently restore the defect this exists to
    remove:

    * a ceiling that is not POSITIVE — ``0`` is docker's/Redis's spelling for
      NO LIMIT, i.e. the unbounded dataset that reaches the cgroup cap and is
      ``SIGKILL``ed, which is the failure mode being replaced;
    * a ceiling at or ABOVE the cap — ``maxmemory`` would then never fire and
      the death would be the kernel's again. Refused rather than clamped: a
      caller that raised ``FALKORDB_CONTAINER_MEMORY`` to a value the derived
      ceiling cannot be ordered against is telling this seam something it must
      not paper over.

    The environment seam is the CAP, not a second variable: one knob cannot get
    the two bounds out of order, which is the whole contract.
    """
    cap = cap if cap is not None else container_memory_cap()
    cap_bytes = _spec_bytes(cap)
    if cap_bytes is None or cap_bytes < 1:
        # ``container_memory_cap`` already refuses these; a direct caller that
        # passed ``cap=`` bypassed it, so the belt is here on the value this
        # function actually computes from.
        raise SystemExit(
            f"test-lane: cannot derive a dataset ceiling from "
            f"{CONTAINER_MEMORY_ENV}={cap!r} — it is not a positive docker "
            f"memory spec (default {DEFAULT_CONTAINER_MEMORY})"
        )
    ceiling_bytes = int(cap_bytes * DATASET_CEILING_RATIO)
    ceiling_mib = ceiling_bytes // (1 << 20)
    if ceiling_mib < 1 or ceiling_mib * (1 << 20) >= cap_bytes:
        raise SystemExit(
            f"test-lane: {CONTAINER_MEMORY_ENV}={cap!r} is too small to hold a "
            f"dataset ceiling BELOW it (``--maxmemory`` must fire before the "
            f"cgroup cap, or the kernel SIGKILLs the container and the caller "
            f"is told nothing) — raise it or set it in whole MiB "
            f"(default {DEFAULT_CONTAINER_MEMORY})"
        )
    return f"{ceiling_mib}mb"


def _redis_args(cap: str | None = None, *, ceiling: str | None = None) -> str:
    """The `REDIS_ARGS` the lane container is created with (#7357).

    ONE builder, because ``REDIS_ARGS`` is a single ``-e`` value: a second
    ``-e REDIS_ARGS=…`` is not merged by docker, the LAST one wins, so a
    persistence pin added next to a ceiling pin would silently drop the other —
    a green argv assertion per flag would not notice.

    ``ceiling`` lets `start()` pass the value it ALREADY derived and validated at
    the top of the call (#7357 review: a derived-seam refusal must land before
    the stale-container `docker rm -f`, or it destroys the lane's graphs and then
    refuses to replace the container — the F8 defect this file already paid for).
    Omitting it derives the ceiling here, which is what a direct caller wants.

    ``--appendonly no --save ''`` (persistence OFF) is this tool's existing
    contract: a throwaway lane must not carry an RDB/AOF, which is also what
    makes a container restart cheap. ``noeviction`` is pinned beside the ceiling
    (see the module docstring: eviction on a graph instance is silent data
    loss).
    """
    ceiling = ceiling if ceiling is not None else container_dataset_ceiling(cap)
    return (f"--appendonly no --save '' --maxmemory {ceiling} "
            f"--maxmemory-policy noeviction")


#: `docker inspect --format` for the facts that separate "the server is wedged"
#: from "the kernel killed it" from "it is looping", in one call.
_TERMINATION_FORMAT = (
    "{{.State.OOMKilled}} {{.State.ExitCode}} {{.RestartCount}} "
    "{{.HostConfig.Memory}}")

#: `docker inspect --format` for the cgroup memory limit (#7357). `0` is
#: docker's spelling for NO LIMIT.
_MEMORY_LIMIT_FORMAT = "{{.HostConfig.Memory}}"


def _container_memory_limit(name: str) -> int | None:
    """The container's cgroup memory limit in BYTES, or None when unreadable.

    None is "could not read", never "it is capped": a failed `docker inspect` is
    not evidence about the limit (#7357).
    """
    r = _docker("inspect", "--format", _MEMORY_LIMIT_FORMAT, name)
    if r.returncode != 0:
        return None
    try:
        return int((r.stdout or "").strip())
    except ValueError:
        return None


_CONTAINER_ENV_FORMAT = "{{json .Config.Env}}"


def _container_redis_args(name: str) -> str | None:
    """The `REDIS_ARGS` a container was CREATED with, or None when unreadable.

    This is the **FALLBACK**, not the primary source: `_note_missing_dataset_ceiling`
    asks the LIVE instance first (`CONFIG GET maxmemory`) because a `CONFIG SET`
    after creation moves the ceiling without touching `Config.Env`. The creation
    env is used only when that live read fails, and it answers a DIFFERENT
    question — what the container was launched with, which is NOT what is in
    effect. Do not delete the live read as redundant (#7357 review, round 3).

    None is "could not read", never "it has none" (#7357: an unread fact must
    never be reported as a pass).
    """
    r = _docker("inspect", "--format", _CONTAINER_ENV_FORMAT, name)
    if r.returncode != 0:
        return None
    try:
        entries = json.loads((r.stdout or "").strip() or "[]")
    except ValueError:
        return None
    if not isinstance(entries, list):  # pragma: no cover - docker emits a list
        return None
    for entry in entries:
        if isinstance(entry, str) and entry.startswith("REDIS_ARGS="):
            return entry[len("REDIS_ARGS="):]
    return ""


def _container_live_maxmemory(name: str) -> int | None:
    """The LIVE `maxmemory` of a RUNNING container, in bytes, or None.

    The reuse path only ever asks about a container that is UP (the caller sits
    inside `if state == "running":`, and the very next call PINGs it), so the
    live value IS readable — and it is the only truthful one: a `CONFIG SET
    maxmemory …` after creation moves the ceiling without touching
    `Config.Env`, so the creation env would report a ceiling the instance no
    longer has (and, in the other direction, would stay silent about a runtime
    `CONFIG SET maxmemory 0` — the unbounded container this exists to catch).

    `docker exec` is bounded by `DOCKER_TIMEOUT` like every other docker call
    here, and any failure answers None — "could not read", never "it is fine".
    """
    r = _docker("exec", name, "redis-cli", "--raw", "CONFIG", "GET", "maxmemory")
    if r.returncode != 0:
        return None
    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    try:
        return int(lines[1])
    except ValueError:
        return None


def _note_missing_dataset_ceiling(name: str) -> None:
    """Say it out loud when a REUSED container has no dataset ceiling (#7357).

    `docker start` cannot change a limit, so a lane container created by an
    earlier version of this tool carries `HostConfig.Memory=2g` **and
    `REDIS_ARGS` with no `--maxmemory`** — the exact defect this change removes,
    kept for life by the only path that can hand such a container back. The
    kernel-cap note next to this one reads `HostConfig.Memory` and is therefore
    SILENT about it (measured on this box 2026-10-21: `fdb-lane-1555b57be7`
    reports `Memory=2147483648`, `maxmemory 0`, and the cap note printed
    nothing). Silence here is #7357's own defect — "the failure is not reported"
    — so the reuse path names it and gives the one remedy that applies.

    A ceiling that is unparseable or NOT strictly below the container's own
    `HostConfig.Memory` is reported too: `--maxmemory` at or above the cgroup cap
    never fires, so the death is the kernel's again.

    ⛔ THE CAP BASIS IS THE CONTAINER'S OWN `HostConfig.Memory`, never the value
    `FALKORDB_CONTAINER_MEMORY` holds now (#7357 review). A container keeps the
    limits it was BORN with, so comparing a 4g-born container against a lane
    running the default 2g reported "at or ABOVE its own cgroup cap" — and its
    remedy is `down`, which deletes that container's graphs — while the reverse
    (2g-born, ceiling 4gb, lane now at 8g) went silent on a ceiling that can
    never fire. The configured value therefore takes NO part in this function.

    THREE read outcomes of that limit, never collapsed: an int is the cap,
    `0` is "NO cgroup cap at all", and `None` is "could not be read". Every
    branch that names what happens on an overshoot derives that claim from this
    read (`fate`) — "SIGKILLed by the cgroup cap" is only true when a cap WAS
    read, and the env fallback used to assert it unconditionally (or, for
    `--maxmemory 0`, to name the cap and then call the overshoot uncaught)
    (#7357 review, round 6).

    ⛔ THE CEILING COMES FROM THE LIVE INSTANCE when it answers
    (`CONFIG GET maxmemory`), with the creation env as the fallback: a
    `CONFIG SET` after creation moves the ceiling without touching `Config.Env`,
    which in one direction invents a ceiling the instance no longer has and in
    the other stays silent about a runtime `CONFIG SET maxmemory 0`.

    ⛔ THE CEILING TOKEN IS READ WITH REDIS'S GRAMMAR (`_redis_spec_bytes`),
    never docker's: the bare `k`/`m`/`g` are DECIMAL to Redis and 1024-based to
    docker, so `--memory 2g --maxmemory 2g` is a HEALTHY pair (the 2,000,000,000
    ceiling fires ~147 MiB before the 2,147,483,648 cap) that docker's table
    computed as "at or ABOVE its own cgroup cap" — with `down`, which deletes
    the graphs, as the remedy (#7357 review, round 5).

    Prints to STDERR and never raises: this is a warning on a reuse path that
    must still hand back a working container.
    """
    try:
        # THREE distinct read outcomes, never collapsed (#7357 review, round 3):
        # `None` is "could not read", `0` is "NO cgroup cap at all", and a
        # positive int is the cap. Treating `0` or `None` as "use the configured
        # value" asserted a cap that was never read (or that does not exist) and
        # then prescribed `down` — which deletes the very graphs — for a container
        # whose ceiling was fine. `_termination_report` branches on these same
        # three outcomes for the same reason.
        limit = _container_memory_limit(name)
        if limit is None:
            cap_bytes = None
            cap_note = "its own cgroup cap could not be read"
        elif limit == 0:
            cap_bytes = None
            cap_note = "it has NO cgroup cap of its own"
        else:
            cap_bytes = limit
            cap_note = f"its own cgroup cap ({limit})"
        # ⛔ The SIGKILL claim needs a cap to make it true: when the cgroup cap
        # is unreadable or ABSENT (`_note_uncapped_existing` prints that beside
        # this note), saying "SIGKILLed by the cgroup cap" asserts a limit no
        # branch read. ONE spelling, used by EVERY branch that names the
        # overshoot's fate — the live branch had it first, and the two
        # env-fallback branches asserted the SIGKILL unconditionally (or denied
        # a cap they had just named), which is the same collapse (#7357 review,
        # round 6: round 5 fixed it for the live branch only).
        fate = ("an overshoot is SIGKILLed by the cgroup cap"
                if cap_bytes is not None else
                "an overshoot goes uncaught")
        live = _container_live_maxmemory(name)
        if live is not None:
            if live >= 1 and cap_bytes is not None and 0 < live < cap_bytes:
                return
            if live < 1:
                reason = (
                    "has NO dataset ceiling in effect (`CONFIG GET maxmemory` "
                    f"answers `{live}`, i.e. unbounded, so {fate} and the caller "
                    "is told nothing)"
                )
            elif cap_bytes is None:
                # A ceiling EXISTS — that is this note's whole question, and it
                # is answered. Whether the container has a cgroup cap at all is
                # `_note_uncapped_existing`'s finding, printed beside this one;
                # repeating it here would claim a problem we did not measure
                # (#7357 review, round 3).
                return
            else:
                reason = (
                    f"has `maxmemory {live}` at or ABOVE {cap_note}, so the "
                    f"ceiling never fires and an overshoot is SIGKILLed by the "
                    f"kernel"
                )
        else:
            raw = _container_redis_args(name)
            if raw is None:
                print(
                    f"test-lane: could not READ the dataset ceiling of {name} (the "
                    f"`docker exec … CONFIG GET maxmemory` and `docker inspect "
                    f"--format '{_CONTAINER_ENV_FORMAT}'` queries both failed), "
                    f"so whether `--maxmemory` is set cannot be confirmed; it is "
                    f"left alone. Check with `docker container inspect -f "
                    f"'{_CONTAINER_ENV_FORMAT}' {name}` (#7357)",
                    file=sys.stderr,
                )
                return
            m = re.search(r"--maxmemory(?:=|\s+)(\S+)", raw)
            if m is None:
                # ⛔ `fate`, never the bare SIGKILL claim: this fallback is
                # reached with `cap_bytes is None` too (the cap read failed, or
                # there is NO cgroup cap), and then there is no kernel limit for
                # a SIGKILL to come from — the overshoot pages the host, which
                # is #7357's own outage (#7357 review, round 6).
                reason = (
                    "was created with NO dataset ceiling (`REDIS_ARGS` carries no "
                    f"`--maxmemory`, so {fate} and the caller is told nothing)"
                )
            else:
                # ⛔ REDIS's grammar, not docker's: this token is Redis's to
                # interpret, and the two grammars disagree on the bare units
                # (#7357 review, round 5).
                ceiling_bytes = _redis_spec_bytes(m.group(1))
                known_zero = ceiling_bytes == 0
                if known_zero:
                    # ``--maxmemory 0`` is NOT unreadable: it is Redis's spelling
                    # for NO LIMIT, the one value this note exists to catch, and
                    # reporting it as "unreadable" understates it (#7357 review).
                    # ⛔ `fate`, not a hard-coded "uncaught": with a positive
                    # `HostConfig.Memory` the overshoot IS caught (by the
                    # kernel, just illegibly), and the old wording named the cap
                    # in `cap_note` and then denied it in the same sentence
                    # (#7357 review, round 6). "Uncaught" is true only when no
                    # cap was read.
                    reason = (
                        "was created with `--maxmemory 0`, which means NO LIMIT "
                        f"— {cap_note}, so {fate} and the caller is told nothing"
                    )
                elif ceiling_bytes is None or ceiling_bytes < 1:
                    reason = (
                        f"has an unreadable `--maxmemory {m.group(1)}`, so whether "
                        f"it bounds anything cannot be confirmed"
                    )
                elif cap_bytes is None:
                    # The ceiling was READ and is a real bound. Whether the
                    # container has a cgroup cap at all is
                    # `_note_uncapped_existing`'s finding, printed beside this one
                    # — claiming it here would assert a fact this branch did not
                    # read, and the remedy it prescribes (`down`) DELETES the
                    # container's graphs (#7357 review, round 4).
                    return
                elif ceiling_bytes >= cap_bytes:
                    reason = (
                        f"has `--maxmemory {m.group(1)}` at or ABOVE {cap_note}, "
                        f"so the ceiling never fires and an overshoot is "
                        f"SIGKILLed by the kernel"
                    )
                else:
                    return
        print(
            f"test-lane: WARNING — {name} {reason} (#7357; `docker start` cannot "
            f"change it). Run `uv run python tools/test_lane.py down` and retry "
            f"to recreate it with the derived ceiling "
            f"({DATASET_CEILING_RATIO:.0%} of the cgroup cap)",
            file=sys.stderr,
        )
    except Exception:  # noqa: BLE001, RUF100 — a warning must never break the reuse path
        print(
            f"test-lane: could not evaluate the dataset ceiling of {name} "
            f"(#7357) — leaving it alone",
            file=sys.stderr,
        )


def _note_uncapped_existing(name: str) -> None:
    """Say it out loud when the container about to be REUSED has no cap (#7357).

    `docker start` cannot add a limit, so a container created before this tool
    passed `--memory` keeps `HostConfig.Memory=0` for life. Those are precisely
    the neighbours the issue names as the pressure source, so handing one back
    silently is how they go on being uncapped: the whole point of the issue is
    that the failure is not reported. Prints to STDERR because it is a warning.

    `None` is "could not READ", and reporting it as SILENCE makes it
    indistinguishable from "it is capped" — the same unread fact that lets an
    uncapped container go unnoticed, and the reuse path is the ONLY place a
    pre-existing uncapped lane container can be caught (`docker start` cannot add
    a limit). `graph-scripts/setup.py::_note_uncapped_existing` says so for the
    same reason (#7357 review); this is the same arm.
    """
    limit = _container_memory_limit(name)
    if limit is None:
        print(
            f"test-lane: could not READ the kernel memory limit of {name} (the "
            f"`docker inspect --format '{_MEMORY_LIMIT_FORMAT}'` query failed — "
            f"a wedged daemon is one reason), so whether it is capped cannot be "
            f"confirmed; it is left alone. Check with `docker container inspect "
            f"-f '{_MEMORY_LIMIT_FORMAT}' {name}`, and recreate it with "
            f"{CONTAINER_MEMORY_ENV}="
            f"{os.environ.get(CONTAINER_MEMORY_ENV) or DEFAULT_CONTAINER_MEMORY} "
            f"(`uv run python tools/test_lane.py down` first) if it reports 0",
            file=sys.stderr,
        )
        return
    if limit <= 0:
        print(
            f"test-lane: WARNING — {name} was created WITHOUT a kernel memory "
            f"cap (HostConfig.Memory=0, #7357) and `docker start` cannot add "
            f"one; it can take the host down with it. Run `uv run python "
            f"tools/test_lane.py down` and retry to recreate it with "
            f"{CONTAINER_MEMORY_ENV}="
            f"{os.environ.get(CONTAINER_MEMORY_ENV) or DEFAULT_CONTAINER_MEMORY}",
            file=sys.stderr,
        )


def _termination_report(name: str, *, live: bool) -> str:
    """Explain a container that did not answer — or `""` when docker says nothing
    relevant, or could not be asked.

    #7357's second half is LEGIBILITY, not the cap. Three measured shapes used to
    read as the same sentence ("it may be wedged: run down and retry"), so the
    operator retried into the same death and never learned the cause:

    * **killed for memory** — `OOMKilled=true` or `ExitCode=137` (128+SIGKILL:
      the kernel OOM killer, or this tool's own cgroup cap);
    * **restarting** — `RestartCount > 0`. This is the CANONICAL instance's own
      signature in #7357 (`ExitCode=0`, `OOMKilled=false`, `RestartCount`
      climbing 8 -> 68 -> 150): a restart loop under host memory pressure. It is
      named as the leading HYPOTHESIS, not as a measurement, because docker
      exposes no reason for a restart;
    * **`live`** — the container is Up right now, so those facts describe a
      PREVIOUS termination and cannot explain a live server that does not answer.
      Saying "it did not hang" there would be a false diagnosis, and it also
      suppressed the correct remedy.

    A failed/unknown inspect reports NOTHING rather than guessing: this function
    exists so the failure names what was measured.
    """
    r = _docker("inspect", "--format", _TERMINATION_FORMAT, name)
    if r.returncode != 0:
        return ""
    parts = (r.stdout or "").strip().split()
    if len(parts) != 4:
        return ""
    oom, exit_code, restarts, cap_bytes = parts
    # The cap that ACTUALLY applies is docker's HostConfig.Memory, not the value
    # `FALKORDB_CONTAINER_MEMORY` holds now: a lane container outlives runs and
    # `docker start` cannot change its limit, so a container created at 2g and
    # OOM-killed while the env says 8g would otherwise be reported as killed by
    # "its own cgroup cap (…=8g)" — a cap it does not have (#7357 review).
    try:
        measured = int(cap_bytes)
    except ValueError:
        measured = None
    if measured == 0:
        cap_note = (
            "this container has NO cgroup cap at all (HostConfig.Memory=0) — it "
            "was created before this tool passed one, and `docker start` cannot "
            "add it"
        )
    elif measured is None:
        cap_note = ("its cgroup memory limit could not be read, so which cap "
                    "applies is unknown")
    else:
        cap_note = f"its cgroup cap is {measured} bytes"
        # NORMALIZED exactly as `container_memory_cap()` reads it: an env value
        # of `2G` or `" 2g "` is a cap this tool accepts, so comparing the RAW
        # string would report a mismatch — and recommend a destructive recreate —
        # for a cap that IS the one in effect (#7357 review).
        wanted = (os.environ.get(CONTAINER_MEMORY_ENV)
                  or DEFAULT_CONTAINER_MEMORY).strip().lower()
        if _spec_bytes(wanted) != measured:
            cap_note += (
                f", NOT the {CONTAINER_MEMORY_ENV}={wanted} in effect now "
                f"(`docker start` cannot change it: recreate the container to "
                f"move the cap)"
            )
    try:
        restarted = int(restarts) > 0
    except ValueError:
        return ""
    # 137 is 128+SIGKILL — the kernel OOM killer, or this very cgroup cap.
    # Anything else (0 = a clean exit, 143 = our own `docker stop`) is NOT a
    # memory kill and must not be reported as one.
    killed = oom.lower() == "true" or exit_code == "137"
    if live:
        if not (killed or restarted):
            return ""
        return (
            f" (Docker also reports a PREVIOUS termination for this container "
            f"— OOMKilled={oom}, ExitCode={exit_code}, RestartCount={restarts}. "
            f"It is Up now, so that history cannot explain an unanswered PING; "
            f"it does mean memory pressure has killed or restarted it before, "
            f"so check {CONTAINER_MEMORY_ENV} and the host before re-running.)"
        )
    if killed:
        if oom.lower() == "true":
            cause = (
                f"by the kernel under host memory pressure, or by its own "
                f"cgroup cap — {cap_note}"
            )
            verdict = "KILLED FOR MEMORY"
        else:
            # `ExitCode=137` WITHOUT `OOMKilled=true` is a plain SIGKILL, and a
            # cgroup-cap kill is exactly what DOES set OOMKilled — so naming the
            # cap here would contradict the evidence in the same sentence.
            cause = (
                "by a SIGKILL from outside the container — its own cgroup cap "
                "did not fire (that sets OOMKilled=true), so the pressure was "
                "host-level and never reached this container's OOM flag, or "
                "another process killed it"
            )
            verdict = "SIGKILLED (ExitCode=137)"
        return (
            f" The container was {verdict} (OOMKilled={oom}, "
            f"ExitCode={exit_code}, RestartCount={restarts}): it did not hang, "
            f"it was terminated {cause}. Nothing the client retried could have "
            f"succeeded, and this being invisible is what made it read as a "
            f"wedged database."
        )
    if restarted:
        return (
            f" Docker also reports RestartCount={restarts}: the container has "
            f"been RESTARTED, so this is not a slow start. The reason is not "
            f"reported by docker — host memory pressure is the leading "
            f"hypothesis (#7357: a killed process under an unconditional "
            f"restart policy comes back, repeatedly), so check free memory and "
            f"{CONTAINER_MEMORY_ENV} before re-running."
        )
    return ""


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
    # The SCAN is the load-bearing guard in TWO cases: a FOREIGN container that
    # publishes on a NON-loopback interface (`-p 16478:6379` -> `0.0.0.0:16478`),
    # where the host probe stays free to bind 127.0.0.1:16478 so only this table
    # shows it; and a STOPPED one. A PEER's RUNNING container publishes on
    # loopback (`-p 127.0.0.1:<port>:6379`, what `start()` asks for), which the
    # host probe DOES refuse — measured, Docker 29.4.0.
    #
    # `-a` is not a hedge: a STOPPED container's host port is FREE at the OS
    # level (`bind` succeeds) but docker still holds the mapping, and it DOES
    # report it here. Measured 2026-10-20 on Docker 29.8.2:
    #
    #   $ docker ps -a --format '{{.Names}}\t{{.State}}\t{{.Ports}}'
    #   fdb-7846-rdb  exited  3000/tcp, 127.0.0.1:16455->6379/tcp
    #
    # and `bind("127.0.0.1", 16455)` succeeded. So for that port `_container_
    # publishes` is the ONLY refusal — which is exactly how 67 of the range's 110
    # ports came to be held by EXITED containers, and why the `reap_orphans`
    # report exists. Reading `-a` as "adds nothing" would make that measurement
    # look impossible to the next reader.
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
        f"test-lane: no free port in {lo}-{hi}. A STOPPED lane container "
        f"keeps its published port reserved, so stopped containers are what "
        f"exhaust this range — `uv run python tools/test_lane.py list` shows "
        f"every {NAME_PREFIX}* container and its state. A stopped container "
        f"belongs to its own lane (#8180: reap only what is *yours*; the "
        f"automatic reap is authorised only when the owning worktree is GONE), "
        f"so release it from ITS worktree: `cd <that worktree> && uv run "
        f"python tools/test_lane.py down`. A fleet-wide reap of stopped "
        f"containers of LIVE owners exists but is OFF by default because it "
        f"deletes peer lanes' containers — `{STOPPED_REAP_ENV}=1` asks for it "
        f"(pending the owner ruling on #8180)"
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


#: Docker states that are NOT terminal: the container (or its graphs) is still
#: there and removing it would destroy work. `created` is included because the
#: user may be about to start it, and `dead`/`exited` are the only states this
#: tool may replace (#7357 review — a PAUSED container used to be `rm -f`ed).
_LIVE_ISH_STATES = frozenset({"paused", "restarting", "created", "removing"})

#: Docker states this tool may replace: the container has stopped and its
#: termination facts are what explain why.
_TERMINAL_STATES = frozenset({"exited", "dead"})

#: Opt-in that lets `reap_orphans` remove a TERMINAL container whose owning
#: worktree still EXISTS. OFF by default, and that default is the #8180 owner
#: ruling, not caution: the automatic delete is authorised "when the worktree
#: that owns it is gone" and the scope is "reap only what is *yours*", so a
#: container whose owner is alive is that lane's to remove. Measured cost of
#: keeping the boundary (2026-10-20): 67 of the 110 ports in ``PORT_RANGE``
#: were held by EXITED containers, so `uri` refused with `no free port`. The
#: reopen carrying that measurement is on #8180; until the owner rules, the
#: shipped behaviour REPORTS such a container (with docker's termination facts
#: and the one command that releases it from its own worktree) and removes
#: nothing. A lane may not flip that boundary on its own — an env opt-in is a
#: deliberate operator action, not a default, which is the same shape this
#: module already uses for the team-namespace sweep (#1884 / #4128).
STOPPED_REAP_ENV = "TORTOISE_TEST_LANE_REAP_STOPPED"


def _stopped_reap_enabled() -> bool:
    """Whether the OPERATOR asked for the withheld proof-2 reap (#8180).

    Strict ``"1"``: any other spelling is not consent for a deletion that
    crosses a recorded ownership boundary.
    """
    return os.environ.get(STOPPED_REAP_ENV, "0") == "1"


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
    all (a guard is only as good as its ordering). The memory cap is validated
    there too, for exactly the same reason (#7357 review F8): it used to be read
    at the `docker run` call site, i.e. AFTER a stopped lane container had
    already been `docker rm -f`'d — so a typo destroyed the lane's graphs and
    then refused to replace the container.
    """
    if port is not None and not (0 < port < 65536):
        raise SystemExit(f"test-lane: --port {port} is not a valid TCP port")
    # #8124: validated FIRST, for the same reason the port is — a floating tag
    # must fail before the orphan sweep and before any container is touched.
    image = assert_pinned_image(IMAGE)
    # #7357 review F8: validated FIRST — before the orphan sweep, before
    # `container_state`, and above all before the stale-container `docker rm -f`
    # below. An unusable cap must refuse while the container (and the graphs in
    # it) still exist, not after the removal it would never replace.
    cap = container_memory_cap()
    # #7357 review: the DERIVED ceiling is validated here too, beside the cap it
    # is derived from — not at the `docker run` argument tuple, which is where it
    # was first written. `container_dataset_ceiling` refuses a SUPERSET of what
    # `container_memory_cap` accepts (any cap under ~1.33 MiB passes the cap
    # grammar and cannot hold a strictly-smaller ceiling), so validating it at the
    # call site put the refusal AFTER the stale-container `docker rm -f` —
    # verbatim the F8 defect stated in the docstring above.
    ceiling = container_dataset_ceiling(cap)
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
        _note_uncapped_existing(name)
        # #7357: the SIBLING defect this change removes. A container created by
        # an earlier version of this tool has a cgroup cap AND no dataset ceiling,
        # so the kernel-cap note above is silent about it (measured: "2g" +
        # `maxmemory 0`, note printed nothing). Reusing it silently keeps the
        # SIGKILL death for life — the "failure is not reported" shape.
        _note_missing_dataset_ceiling(name)
        if not _wait_ready(name, published):
            # #7357: `live` is decided by a state read taken HERE, after the
            # wait — not by the one that opened this branch. `_wait_ready` waits
            # up to READY_TIMEOUT (60 s) of failed PINGs, and the container can
            # DIE inside that window (the memory-pressure case this issue is
            # about). Reporting the stale "it is Up now" would then invert the
            # diagnosis: docker's OOMKilled/ExitCode would be this failure's
            # CAUSE while the message insisted they were history.
            now = container_state(name)
            if now == "running":
                # Up, so docker's facts describe a PREVIOUS termination: they are
                # reported as history, never as this failure's cause, and the
                # "may be wedged" advice stays — for a live server that does not
                # answer, restarting it IS the remedy.
                raise SystemExit(
                    f"test-lane: {name} is running but never answered PING over "
                    f"its published host port {published} within "
                    f"{READY_TIMEOUT}s — it may be wedged: run `uv run python "
                    f"tools/test_lane.py down` and retry."
                    + _termination_report(name, live=True)
                )
            if now == "unknown":
                # NOTHING is asserted for an unreadable state — not even by
                # appending `_termination_report`. Two reasons, both measured:
                # `container_state` keeps "docker could not answer" separate from
                # "the container is gone" ON PURPOSE, and `down` is a `rm -f` of
                # the container AND its graphs, so guessing a death from a failed
                # read is the destructive mistake the absent-vs-unreadable rule
                # exists to prevent; and `_termination_report` issues its OWN
                # `docker inspect`, so it does not inherit this read's failure — a
                # daemon that recovers between the two calls would answer with the
                # container's PREVIOUS termination and the message would claim the
                # death it had just called UNKNOWN (#7357 review).
                raise SystemExit(
                    f"test-lane: {name} did not answer PING over its published "
                    f"host port {published} within {READY_TIMEOUT}s AND its "
                    f"state could not be READ (the `docker inspect` failed — a "
                    f"wedged or unreachable daemon is one reason), so whether it "
                    f"died or merely wedged is UNKNOWN. Check the daemon (`docker "
                    f"info`) before running `uv run python tools/test_lane.py "
                    f"down`, which would remove the container and its graphs."
                )
            raise SystemExit(
                f"test-lane: {name} STOPPED while waiting for its first PING "
                f"over its published host port {published} (within "
                f"{READY_TIMEOUT}s; docker now reports its state as {now!r}), "
                f"so this is a death, not a slow start — run `uv run python "
                f"tools/test_lane.py down` and retry."
                + _termination_report(name, live=False)
            )
        return name, published
    if state == "unknown":
        raise SystemExit(
            f"test-lane: cannot determine the state of {name} — is the docker "
            f"daemon running? (refusing to guess: removing a container that may "
            f"belong to another lane would be unforgivable)"
        )
    if state not in _TERMINAL_STATES and state != "absent":
        # #7357 review, and the worst of the class: this path used to `rm -f`
        # EVERY non-running container. But `paused` is a LIVE database —
        # `docker pause` is exactly what an operator may reach for to freeze a
        # runaway instance rather than kill it — and `restarting` is a container
        # docker is still cycling. Neither is TERMINAL, and their termination
        # facts read `OOMKilled=false ExitCode=0 RestartCount=0`, so the removal
        # printed NOTHING: measured, a paused lane container holding a graph was
        # silently deleted and `start()` returned the replacement as if nothing
        # had happened. Only a terminal container may be replaced; an
        # unrecognised state fails CLOSED to a refusal too, because docker may
        # add one and the default must never be "remove what we do not
        # understand".
        if state in _LIVE_ISH_STATES:
            why = (
                "its graphs are still there, and `docker pause`/`unpause` (or a "
                "`restarting` container settling) reaches the state you want "
                "without destroying anything"
            )
        else:
            why = (
                "this tool does not recognise that state as terminal, so it "
                "refuses to remove a container it cannot classify"
            )
        raise SystemExit(
            f"test-lane: {name} is in docker state {state!r}, which is NOT "
            f"terminal — {why}. Inspect it (`docker container inspect {name}`) "
            f"and run `uv run python tools/test_lane.py down` if you meant to "
            f"discard it."
        )
    if state != "absent":
        # A failed `rm` must be read and reported, not discarded: it leaves a
        # name that makes the `docker run` below fail with "name already in
        # use", which reads as a run problem rather than the removal problem
        # it is.
        # #7357 review: docker's termination facts are read BEFORE the removal.
        # `rm -f` destroys the only evidence of WHY the container stopped, and
        # an OOM-killed lane container takes exactly THIS path: the container is
        # created with no `--restart` policy, so a kill leaves it `exited`, never
        # `running` — the reuse branch above never sees it. Without this read the
        # next `start()` silently removed, recreated and continued, and the
        # operator never learned the cap (or host pressure) had killed it. Same
        # evidence-before-`rm -f` rule the fresh path below states for itself.
        # The ownership guard runs FIRST, so an unmanaged name is refused before
        # any docker call (read or removal).
        _assert_removal_allowed(name)
        prior = _termination_report(name, live=False)
        outcome = _remove_and_describe(name)
        if outcome.startswith("FAILED"):
            raise SystemExit(
                f"test-lane: {name} exists, is not running, and could not be "
                f"removed — refusing to start over it ({outcome})"
            )
        if prior:
            print(f"test-lane: {name} had stopped before this start{prior}",
                  file=sys.stderr)

    chosen = port if port is not None else pick_port()
    r = _docker(
        # #8180: `--init` puts a real init at container PID 1. Without it PID 1
        # IS redis, which never reaps its orphaned children — so a container can
        # reach a state where `docker rm -f` cannot kill it. Measured on #2653:
        # "container <id> is zombie and can not be killed. Use the `--init`
        # option", leaving a container no `down()` could remove.
        "run", "-d", "--init", "--name", name,
        # #7357: the KERNEL cap (see CONTAINER_MEMORY_ENV). `--init` and the
        # cap are independent: one reaps orphaned children, the other bounds
        # what a runaway instance can take from every neighbour on the host.
        # `cap` was validated at the TOP of this function (ordering note there).
        *_memory_cap_args(cap),
        "-p", f"127.0.0.1:{chosen}:6379",
        # #7357: the REDIS-side dataset ceiling, BELOW the cgroup cap above, so a
        # runaway instance is REFUSED legibly instead of being SIGKILLed by the
        # kernel and taking the container (and its dataset) with it. NOT a "reads
        # keep working" claim: a lane reads through `GRAPH.QUERY`, which FalkorDB
        # registers as deny-OOM, so reads are refused at the ceiling too (measured).
        # Built by `_redis_args` (one `-e`, never two: docker takes the last), and
        # `ceiling` was validated at the TOP of this function.
        "-e", f"REDIS_ARGS={_redis_args(cap, ceiling=ceiling)}",
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
        # #7357: read docker's termination facts BEFORE removing the container —
        # `rm -f` destroys the evidence the report is made of.
        killed = _termination_report(name, live=False)
        logs = _docker("logs", "--tail", "20", name)
        raise SystemExit(
            f"test-lane: {name} never answered PING; {_remove_and_describe(name)}. "
            f"Logs:\n{logs.stdout}{logs.stderr}{killed}"
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


def _assert_removal_allowed(name: str) -> None:
    """The ownership guard EVERY removal intent must pass, factored out so a
    read that precedes a removal cannot reach docker for a container this tool
    does not own (and so the rule lives in one place instead of at call sites).
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


def _remove_and_describe(name: str) -> str:
    """Remove a container this tool just created, and say what actually
    happened — a cleanup whose result is unread is how a running container with
    a reserved port survives to make every later `start()` refuse.

    Guarded like every other removal intent, so "no removal bypasses the
    ownership check" is true of the whole module and not just of `stop()`.
    """
    _assert_removal_allowed(name)
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
    """Remove `fdb-lane-*` containers that are PROVABLY out of service.

    ONE proof is armed, and it is OWNERSHIP — a recorded-worktree existence read
    (``Path(owner).is_dir()``), not a clock and not a container state:

    1. **The owning worktree is gone** (#8180). Ownership is recoverable from
       the container's own name (it is `slug_for()` of the worktree that owns
       it), so the reaping decision is a lookup rather than a guess. This tool
       can therefore only ever remove a container whose owner it has
       POSITIVELY established is gone — never a peer lane's, and never this
       worktree's own (`stop()` is the only thing that removes that one). It
       does not consult the container's STATE at all: an owner that is gone
       means nothing will start that container again, whatever docker last said
       about it (`paused`/`restarting`/`created` included — ``_LIVE_ISH_STATES``
       gates only the state arm below, not this one).

    A second proof — **the container is in a TERMINAL state** (`exited`/`dead`,
    ``_TERMINAL_STATES``) — is **WITHHELD BY DEFAULT** (`STOPPED_REAP_ENV`),
    because it is a DELETION of a container whose owner is alive and #8180's
    recorded ruling authorises a reap only "when the worktree that owns it is
    gone", scoped to "reap only what is *yours*". Every registered worktree on
    a real host still exists (measured 2026-10-20: 565 of 567), so a gate on
    "the owner is DETERMINED" is satisfied for essentially every lane container
    and does not narrow the boundary at all — which is why the boundary, not the
    gate, is what the default has to honour.

    What the withheld arm would buy, and does not spend by default: docker keeps
    a STOPPED container's published port reserved, so a range full of them makes
    every lane refuse with `no free port` (measured 2026-10-20: all 110 ports in
    ``PORT_RANGE`` published, **67 held by EXITED containers**). With the arm
    withheld the default REPORTS each such container — its owner worktree, its
    state and the ONE command that releases it from its own worktree — and
    `pick_port` reports the same remedy when the range is full. No `docker
    inspect` is spent there: nothing is destroyed, so there is no evidence to
    preserve, and this line runs on EVERY `up`/`uri`. Termination facts belong
    where a `rm -f` happens, and are read there.

    The arm is not merely inert when withheld: the reporting is the point. A
    dead lane's stopped container is invisible to every other instrument, and it
    is the population that exhausts the port range. The reopen carrying that
    measurement is on #8180; if the owner rules for it, `STOPPED_REAP_ENV=1`
    (or a new default) arms the deletion with no other change here.

    When the opt-in IS set, the arm keeps the two properties it was reviewed
    for: the state is re-read IMMEDIATELY before the removal (the sweep's
    `docker ps` snapshot can be stale, and a lane restarting reuses the same
    name), and docker's termination facts are read BEFORE the `rm` (#7357) —
    `rm -f` destroys the only evidence of WHY a container stopped, and that
    lane's own `start()` then reads `absent` and reports nothing. The armed
    ownership path above reads them the same way, and only for a container
    docker reports as STOPPED: for an owner-gone container that is still UP the
    facts describe a PREVIOUS termination, and `_termination_report(live=False)`
    would state it as this failure.

    Age is not consulted and there is deliberately NO age backstop here. The
    measured defect is a container that outlives its owner, and a clock firing on
    a live owner's database would be a far worse bug than the leak it fixes —
    the rule the whole module defends. `paused`/`restarting`/`created` are
    live-ish in exactly the same way (``_LIVE_ISH_STATES``) and never reach the
    STATE arm, which fires only on ``_TERMINAL_STATES``.

    Fail-closed: if the worktree list or `docker ps` cannot be read, nothing is
    removed and the reason is reported; a state this module does not recognise
    is reported, never inferred to be terminal. Returns one line per container
    this function ACTED on or decided to KEEP; a name refused by `is_managed()`
    is not reported (it was never a candidate), so a caller reports what
    actually happened instead of counting.
    """
    slugs = _worktree_slugs()
    if slugs is None:
        return ["reap: skipped — `git worktree list` failed, so ownership "
                "cannot be determined; NOTHING was removed (fail-closed)"]
    listed = _docker("ps", "-a", "--filter", f"name={NAME_PREFIX}",
                     "--format", "{{.Names}}\t{{.State}}")
    if listed.returncode != 0:
        return ["reap: skipped — `docker ps` failed; NOTHING was removed "
                "(fail-closed)"]
    states: dict[str, str] = {}
    for line in (listed.stdout or "").splitlines():
        name, _, state = line.partition("\t")
        name = name.strip()
        if name:
            # A missing state is `unknown`, never terminal: the state arm may
            # only fire on a state docker actually reported.
            states[name] = state.strip().lower() or "unknown"
    mine = lane_name()
    out: list[str] = []
    for name in sorted(states):
        if not is_managed(name):
            continue
        if name == mine:
            out.append(f"reap: kept {name} — this is THIS worktree's container")
            continue
        owner = slugs.get(name[len(NAME_PREFIX):])
        state = states[name]
        # Ownership is a DETERMINED owner plus what its recorded worktree says.
        # Resolved ONCE, before either proof, so no arm can report a worktree as
        # "exists" without having looked at it.
        present: bool | None = None
        if owner is not None:
            try:
                present = Path(owner).is_dir()
            except OSError:
                # pathlib suppresses ENOENT/ENOTDIR/EBADF/ELOOP but NOT EACCES,
                # so an inaccessible worktree path raises. "Cannot tell" is the
                # REPORT-ONLY case: it must never be read as "the owner is gone".
                present = None
        if owner is not None and present is None:
            out.append(f"reap: kept {name} — its owning worktree {owner} "
                       f"could not be examined (fail-closed, report only)")
            continue
        if owner is not None and present is False:
            # Proof 1, the ONLY armed proof: the worktree that owns this
            # container is gone, so this is what #8180 authorises. #7357: the
            # facts are read BEFORE the removal — `rm -f` destroys the only
            # evidence of WHY it stopped, and nobody else will report it now
            # that its owner is gone.
            #
            # The facts are a claim about WHY it stopped, so they may only be
            # read for a container docker actually reports as STOPPED: this
            # path also fires for a RUNNING container whose owner worktree was
            # deleted (a lane container outlives its worktree by design), and a
            # previous OOM kill reported there is HISTORY, not this failure —
            # `_termination_report(live=False)` states "it did not hang, it was
            # terminated", the false diagnosis its own `live` flag exists to
            # prevent. An unreadable state asserts nothing at all, exactly as
            # `start()` already refuses to append a report to `unknown`.
            prior = ("" if state not in _TERMINAL_STATES
                     else _termination_report(name, live=False))
            rm = _docker("rm", "-f", name)
            out.append(f"reap: {'removed' if rm.returncode == 0 else 'FAILED to remove'} "
                       f"{name} — its owning worktree {owner} is gone"
                       + (f"; it was {prior}" if prior else ""))
            continue
        if owner is not None and state in _TERMINAL_STATES:
            # The WITHHELD arm (`STOPPED_REAP_ENV`): a TERMINAL container whose
            # owning worktree still EXISTS. It is a deletion of a container
            # whose owner is ALIVE, which #8180's recorded ruling does not
            # authorise ("when the worktree that owns it is gone", "reap only
            # what is *yours*") — and a lane may not reverse that on its own. A
            # gate on "the owner is DETERMINED" does not narrow it: measured
            # 2026-10-20, 565 of 567 registered worktrees still exist, so nearly
            # every lane container passes that gate.
            #
            # So the DEFAULT reports, and the report is the load-bearing part:
            # this container holds a port out of PORT_RANGE until someone
            # removes it, and no other instrument on the host shows it. No
            # `docker inspect` is spent here — nothing is being destroyed, so
            # there is no evidence to preserve — and this line runs on EVERY
            # `up`/`uri`: spending it for every stopped peer container
            # (measured ~53 ms each; 67 of them) would add ~3.5 s to every lane
            # start, which is exactly why this arm does not.
            if not _stopped_reap_enabled():
                out.append(
                    f"reap: kept {name} — its owning worktree exists ({owner}) "
                    f"and it is {state}, so it is a PEER lane's container: "
                    f"#8180 authorises a reap only when the owning worktree is "
                    f"gone. It holds a port from {PORT_RANGE[0]}-{PORT_RANGE[1]} "
                    f"until it is removed, from its OWN worktree: `cd {owner} "
                    f"&& uv run python tools/test_lane.py down` (or rule on "
                    f"#8180 to arm `{STOPPED_REAP_ENV}=1`)")
                continue
            # Re-read the state AT THE MOMENT OF ACTION: the snapshot above was
            # one `docker ps`, and a lane restarting its own container reuses
            # the SAME name, so an `exited` read can be stale by the time the
            # `rm` lands. `unknown` keeps, per this module's absent-vs-
            # unreadable rule.
            now = container_state(name)
            if now not in _TERMINAL_STATES:
                out.append(f"reap: kept {name} — it read {state} in the sweep "
                           f"but is {now} at removal time (fail-closed)")
                continue
            # Facts are read BEFORE this removal (#7357): the owner's own
            # `start()` will now read `absent` and report nothing, so this is
            # the last moment the cause exists.
            facts = _termination_report(name, live=False)
            rm = _docker("rm", "-f", name)
            out.append(f"reap: {'removed' if rm.returncode == 0 else 'FAILED to remove'} "
                       f"{name} — it is {now} (a stopped container serves "
                       f"nothing and holds a port from {PORT_RANGE[0]}-{PORT_RANGE[1]}; "
                       f"reaped only because {STOPPED_REAP_ENV}=1 is set)"
                       + (f"; it was {facts}" if facts else ""))
            continue
        if owner is not None:
            # The owner IS determined and its worktree exists, so ownership
            # alone decides — and a live owner is never reaped, at any age.
            out.append(f"reap: kept {name} — its owning worktree exists ({owner})")
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
        out.append(f"reap: kept {name} — no owner here and it is {state}, so "
                   f"ownership is NOT determined ({age_txt}); report only, "
                   f"fail-closed")
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


def lane_seam_env(port: int, graph: str) -> dict[str, str]:
    """The THREE seam variables that actually make this lane the target.

    ONE definition, two consumers: ``cmd_up`` PRINTS these (so a shell can
    ``eval`` them) and ``cmd_run`` EXPORTS them to a pytest child. They must
    never drift apart — the tool's own SCOPE section records what happens when
    only the URI is exported: tests that build their target through
    ``tests/_live_utils.py`` key on ``TORTOISE_TEST_DOCKER_PORT`` and a
    password, so they silently address the SHARED instance instead, and the
    probe against a passwordless private container raises
    ``AuthenticationError`` (which an ``except Exception`` turns into "Live
    FalkorDB (Docker) not available" → 14 skipped, rc=0).

    ``''`` is a MEANINGFUL password there ("no ``requirepass`` configured"),
    distinct from ``None`` (no userinfo at all), which is why the value is a
    literal empty string and not an omission.
    """
    return {
        "TORTOISE_DB_URI": uri_for(port, graph),
        "TORTOISE_TEST_DOCKER_PORT": str(port),
        "TORTOISE_TEST_DOCKER_PASSWORD": "",
    }


def cmd_up(args: argparse.Namespace) -> int:
    _validate_graph(args.graph)      # refuse BEFORE a container exists
    name, port = start(args.port)
    graphs = _graph_count(name)
    shown = "unknown" if graphs is None else str(graphs)
    print(f"test-lane: {name} on 127.0.0.1:{port} (graphs={shown})",
          file=sys.stderr)
    for key, value in lane_seam_env(port, args.graph).items():
        print(f"export {key}='{value}'")
    return 0


def cmd_uri(args: argparse.Namespace) -> int:
    return cmd_up(args)


def cmd_run(args: argparse.Namespace) -> int:
    """Run pytest against THIS lane's container — the invocation that cannot
    be mis-spelled by hand.

    WHY (#10399, measured): a full-suite run on a cell is routinely started
    with the invocation AGENTS.md used to document
    (``TORTOISE_DB_URI='docker://:falkordb@localhost:6379/…' pytest tests/``),
    and on a cell nothing listens on 6379 — the services lanes publish on
    EPHEMERAL ports (#6673) and a private lane on its own. The session-start
    gate then refuses ONCE PER COLLECTED TEST, so a full run reports a wall of
    `E`s and no verdict (the population is not a fixed number: `tests/` collects
    41,842 tests at #10399's head, and a run that is killed mid-flight shows
    only as many as it reached — the batch's own log held 405 before its process
    died). Hand-``eval``-ing the three exports works but is a step every caller
    can forget (and one did).

    This runs the child with ``sys.executable -m pytest`` — the interpreter
    that is already RUNNING this tool, i.e. the lane's venv — rather than a
    bare ``pytest`` from PATH, which on a box with several worktrees resolves
    to whichever venv happens to be first (a wrong-venv run measures the wrong
    code, and #2985 shows how quietly that degrades: a venv without the
    ``embeddings`` extra silently runs a keyword-only product).

    The resolved lane is printed to STDERR BEFORE the child starts, so a run
    that is killed mid-flight still leaves its target behind in whatever log it
    was redirected to.

    A child killed by a signal is reported as ``128 + N`` (the shell convention)
    instead of the negative ``returncode`` — a lane needs to tell "tests
    failed" from "someone killed this run", and ``-9`` in an exit code reads as
    an argparse-ish usage error.
    """
    _validate_graph(args.graph)      # refuse BEFORE a container exists
    name, port = start(args.port)
    extra = list(args.pytest_args)
    if extra and extra[0] == "--":
        # `run -- -q tests/` is the documented spelling (it survives the same
        # way `cargo run --` and `docker run … --` do), so the separator is
        # consumed here rather than handed to pytest as an unknown argument.
        extra = extra[1:]
    if not extra:
        # Bare `run` means THE SUITE — `tests/`. This is not cosmetic: with no
        # path, pytest collects from the ROOTDIR (there is no `testpaths` in
        # pyproject.toml), which sweeps `validation/`, `graph-scripts/`,
        # `integrations/tests/` and emits a collection error — a run that tests
        # none of the suite and exits 2. The help text promises this default, so
        # the code has to BE the default.
        extra = ["tests/"]
    env = {**os.environ, **lane_seam_env(port, args.graph)}
    argv = [sys.executable, "-m", "pytest", *extra]
    print(
        f"test-lane: {name} on 127.0.0.1:{port} — "
        f"TORTOISE_DB_URI={env['TORTOISE_DB_URI']} "
        f"TORTOISE_TEST_DOCKER_PORT={env['TORTOISE_TEST_DOCKER_PORT']} "
        f"TORTOISE_TEST_DOCKER_PASSWORD='' — invoking: {' '.join(argv)}",
        file=sys.stderr,
    )
    sys.stderr.flush()
    rc = subprocess.run(argv, env=env).returncode
    if rc < 0:
        print(
            f"test-lane: pytest was KILLED by signal {-rc} (no pytest count "
            f"line exists for a killed run — this is NOT a test failure)",
            file=sys.stderr,
        )
        return 128 + (-rc)
    return rc


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
    runner = mapper   # `run` starts a container too, so it takes the same flags
    plain = argparse.ArgumentParser(add_help=False)
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn, help_, parent in (
        ("up", cmd_up, "start this lane's container and print the export line",
         mapper),
        ("uri", cmd_uri, "same as `up` (reads better inside $( ))", mapper),
        ("run", cmd_run, "run pytest against this lane's container "
                        "(run -- <pytest args>)", runner),
        ("down", cmd_down, "remove this lane's container", plain),
        ("status", cmd_status, "is it running, and how many graphs does it hold",
         graph_only),
        ("list", cmd_list, "list every fdb-lane-* container", plain),
    ):
        sp = sub.add_parser(name, help=help_, parents=[parent])
        if name == "run":
            # REMAINDER, not a named option: everything after the separator is
            # pytest's own argv and must reach it verbatim (`-q`, `-k expr`,
            # `--timeout=900`, a path…). argparse would otherwise consume the
            # pytest flags it recognises and reject those it does not.
            sp.add_argument("pytest_args", nargs=argparse.REMAINDER,
                            help="pytest argv, after `--` "
                                 "(default: tests/ — the suite)")
        sp.set_defaults(func=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
