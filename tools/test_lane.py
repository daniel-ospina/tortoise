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
``uri`` therefore exports the port/password seams as well:
``TORTOISE_TEST_DOCKER_PORT`` (the container's assigned port) and
``TORTOISE_TEST_DOCKER_PASSWORD=''`` (this container has no password configured,
and `tests/_live_utils.docker_password` reads that seam).

It ALSO exports ``TORTOISE_TEST_LEGACY_PORT`` — the same class of gap on the
PASSWORDLESS axis. ``_live_utils.legacy_port()`` resolves
``TORTOISE_TEST_LEGACY_PORT`` → ``FALKORDB_PORT`` → the literal **16379**, the
legacy family's HISTORICAL port. That literal is not what CI uses: since #6673
the provision action starts the passwordless service on an EPHEMERAL port and
exports ``TORTOISE_TEST_LEGACY_PORT`` for it (two CI jobs pass ``legacy:
"false"`` and start no legacy service at all), so 16379 appears only as the
fallback. A private lane publishes ONE passwordless container on an ephemeral
port and used to name only the URI and the two docker-axis variables, so a site
that resolves the legacy port WITHOUT first consulting ``TORTOISE_DB_URI`` hit
the literal: where it dials directly it failed ``Error 111``, and where a module
probe gates it (``FALKORDB_AVAILABLE``) it SKIPPED instead — the same defect in
its silent form, and the arm this export also recovers. Measured on ``main`` at
``e4703a286a`` (2026-11-12):
``tests/test_directional_impl.py`` — ``5 failed``, every failure
``ConnectionError: Error 111 connecting to localhost:16379``, and the same 5 in
the same order alone and inside the shard; ``5 passed`` with the legacy
port pointed at this container. Counts, not clock readings: the durations live in
the lane's recorded ``WORKTREE-REPORT.md`` logs, where they belong. Since this
container has no password, it IS a
legacy service, so the extra export names the target rather than changing it.

Still out of reach:
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

``run`` is the spelling to prefer: it is the one place the four seam variables
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
import contextlib
import hashlib
import json
import os
import re
import signal
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

#: ── OWNERSHIP IS ALSO WRITTEN INTO THE CONTAINER, NOT ONLY INTO ITS NAME ────
#:
#: The name is `fdb-lane-<sha1(abs worktree path)[:10]>` — a ONE-WAY digest of
#: the very path that teardown deletes. So the record of ownership is destroyed
#: by the event that creates the orphan: once `git worktree remove`/prune drops
#: the entry, the slug can never be resolved again by `_worktree_slugs()`, the
#: owner reads as INDETERMINATE, and the container lands in the population that
#: `reap_orphans` may only REPORT (`OWNER_INDETERMINATE_REAP_ENV`). Measured on
#: the dev box 2026-11-03: 2 of 7 live `fdb-lane-*` containers already had no
#: worktree claiming them, at 6 minutes and 1.1 hours old.
#:
#: So the owner path is ALSO stamped on the container as a docker label at
#: creation — the mechanism Testcontainers' Ryuk and Kubernetes
#: `ownerReferences` both use — which survives the deletion of the worktree.
#: That does NOT settle #8180's open arm: it makes the owner DETERMINED, so the
#: container qualifies for the one proof the ruling already authorises armed
#: ("a `fdb-lane-*` container MAY be reaped when the worktree that owns it is
#: gone") instead of falling into the INDETERMINATE bucket whose default the
#: owner has not ruled on. It only promotes a container out of a REPORT-ONLY
#: bucket, never out of a protected one: the owner-gone arm carries the same
#: not-in-use proof the INDETERMINATE arm does (see its comment), so no default
#: destroys anything it did not destroy before (#10841 review, P1).
LABEL_OWNER = "tortoise.test-lane.worktree"
LABEL_SLUG = "tortoise.test-lane.slug"
LABEL_CREATED = "tortoise.test-lane.created"

#: ── OWNERSHIP LIVENESS, and the bound that makes it a reap instead of a report ──
#:
#: The measured kill (2026-11-03, cell-1): 68 `fdb-lane-*` containers alive, the
#: oldest FOUR DAYS old, alongside 96 containerd shims — load average **639.88**
#: on 8 cores ("~80x oversubscribed"), and every lane on the box stopped being
#: able to work. A hand-reap was the emergency relief. Measured on the dev box
#: the same morning: 40 `fdb-lane-*` containers, 2 with a live owning lane, 23
#: whose owning worktree still EXISTS but whose lane is gone, 15 whose owner is
#: not in this clone at all.
#:
#: WHY NOTHING WAS EVER REAPED — the owner was the DIRECTORY, not the LANE.
#: `reap_orphans` armed exactly one proof, "the owning worktree is gone", and
#: `cmux workspace close` (the measured lane-close path, #8180) does NOT remove
#: the worktree — it closes the LANE. So the single armed proof was almost never
#: true: the container's 400 MB and its containerd shim outlive the lane that
#: needed it, forever. That is the defect this bound closes, and it is the same
#: conflation #8180's own cited SOTA separates: Testcontainers' **Ryuk** reaps on
#: owner **PROCESS** death (including `SIGKILL`), Kubernetes `ownerReferences` on
#: owner **OBJECT** deletion, preview environments on `pull_request closed` — in
#: all three the owner is a living entity whose death is OBSERVED, never a
#: directory that persists.
#:
#: THE PROOF IS STILL OWNERSHIP, NOT A CLOCK (this is the #5084 ruling, and it
#: stands): a container is reaped only after POSITIVE evidence that its lane is
#: gone — no live process holds the worktree (cwd **or** cmdline), and the
#: container itself reports no client activity (`CLIENT LIST`, which is the
#: container's own statement that nobody is using it). The age below is a
#: BACKSTOP on top of that evidence, exactly as #8180's Outcome specifies ("an
#: age backstop for containers whose owner can no longer be determined"); it is
#: never the proof, and it can never fire on an owner this host can OBSERVE is
#: alive: a live process holding the owning worktree, or an attached client (both
#: positive reads), keeps the container AND refreshes the lease, so a lane a
#: sweep can see alive is spared at any age (#10693 review F1). The lease marks
#: the last OBSERVED use, not the last use — `REAP_STATE_DIR` states what that
#: leaves uncovered. The lease itself is stated in full there.
#:
#: ⛔ WHAT THE **DEFAULT** AUTHORISES (#10693 review F2) — the ownership evidence
#: above is necessary and NOT sufficient. #8180's owner ruling is the operative
#: text: "a `fdb-lane-*` container MAY be reaped **when the worktree that owns it
#: is gone**", "the age backstop applies only to owner-INDETERMINATE containers",
#: "fail-closed: if ownership cannot be determined, do NOT delete — report only",
#: "reap only what is *yours*". Those sentences do not agree about the
#: owner-indeterminate population, and they do not reach a container whose
#: owning worktree STILL EXISTS. A lane picking a side is not a decision, so the
#: two proofs built on top of that evidence are **armed machinery behind an
#: explicit operator action** (`LANE_GONE_REAP_ENV` / `OWNER_INDETERMINATE_REAP_ENV`)
#: and the DEFAULT is the ruling's letter plus a report that carries the whole
#: evidence chain and the one command that releases each container. Only the
#: worktree-gone proof fires unconditionally, because it is the one #8180
#: authorises. The reopen carrying cell-1's census, the `/proc` blind-spot
#: closure and the 15.6 h-vs-24 h bound is on #8180: if the owner rules for either
#: arm, that ruling becomes the default and the switch disappears.
#:
#: WHY 2 h — `OWNER_GRACE_H`: the owning worktree exists but nothing holds it.
#: A lane shells out per step, so the container is legitimately unheld only
#: BETWEEN two processes of the same lane: a single model turn, minutes. The
#: longest legitimate gap measured on this fleet is a full-suite step (~2 h wall
#: at 41,842 collected tests) — but that step HOLDS the worktree, so it is never
#: a gap. 2 h is therefore ~an order of magnitude above the largest observed
#: between-step gap, and it reclaims 21 of the 23 measured stranded containers
#: on the dev box at once.
#:
#: WHY 24 h — `ORPHAN_MAX_AGE_H`: ownership could not be determined from this
#: clone's `git worktree list` (the container may belong to ANOTHER clone on the
#: box, e.g. `/tmp/cell-t3/.worktrees/...`, whose lane this tool cannot see in
#: git). This case previously reported and deleted nothing, which is why it is
#: the population that survives for days. The `git` blind spot is closed by
#: reading `/proc` instead — every live process's cwd/cmdline names its worktree,
#: so the owning LANE is visible even when the clone is not (this is what spares
#: a live cross-clone lane; see `_live_lane_paths`). 24 h then exceeds the
#: longest live lane measured on this fleet (15.6 h) with margin, and a container
#: that has served no client for a full day with no process anywhere on the box
#: naming its worktree is out of service by any reading.
OWNER_GRACE_H = 2.0
ORPHAN_MAX_AGE_H = 24.0

#: A client whose `idle` is under this is USING the container right now. Read
#: from `CLIENT LIST` inside the container, so it does not depend on knowing the
#: owner at all — and it is the one datum a cross-clone ownerless container
#: still reports about itself. 5 min: a full-suite lane reconnects far more
#: often than that, so a live reader is never mistaken for an idle one.
ACTIVE_CLIENT_IDLE_S = 300

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


def _lane_identity() -> tuple[str, Path]:
    """THIS worktree's container name AND the root it was derived from, from ONE
    `repo_root()` read.

    Both ownership records this tool writes must describe the SAME worktree: the
    container NAME is ``fdb-lane-<slug(root)>``, and the label pair is
    ``<root>`` plus ``slug_for(root)``. `repo_root()` has a documented, PER-CALL
    degraded mode — ``Path.cwd()`` on a git timeout, on git being absent, on
    ``OSError``, or on a non-zero `rev-parse` — so reading it once for the name
    and again for the label lets exactly one of the two degrade, and the two
    records then name two different worktrees.

    That pair is not cosmetic: `reap_orphans()` treats a label whose slug
    disagrees with the container name as "no evidence about this container", and
    once the container's worktree is gone the name no longer resolves either —
    so a container created across ONE degraded read is kept for life and
    reported with a cause that blames the label. That is the #8180 leak
    ("reported on every `up` and reaped by nothing, forever") re-entered through
    the mechanism meant to close it. Deriving both records from THIS one read
    makes them agree by construction.
    """
    root = repo_root()
    return container_name(slug_for(root)), root


def lane_name() -> str:
    """The one container this tool can act on. Never a parameter."""
    return _lane_identity()[0]


def _label_args(root: Path | str) -> list[str]:
    """The ownership stamp written at creation (see `LABEL_OWNER`).

    ``root`` is REQUIRED and is the caller's single `repo_root()` read (see
    `_lane_identity`): reading it again here is what let the label name a
    different worktree than the container's own name.

    ``created`` is a DIAGNOSTIC for a human `docker inspect` (or a host-level
    reader) — nothing decides on it. ``slug`` is NOT: it is the CORROBORATING
    half of the pair, and `reap_orphans()` reads it back and DECIDES on it (a
    slug that disagrees with the container it sits on makes the whole label
    non-evidence), while ``worktree`` decides which owner is resolved.
    """
    return [
        "--label", f"{LABEL_OWNER}={Path(root).resolve()}",
        "--label", f"{LABEL_SLUG}={slug_for(root)}",
        "--label", f"{LABEL_CREATED}={datetime.now().isoformat()}",
    ]


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

#: Operator opt-ins for the two ownership proofs #8180's recorded ruling does NOT
#: settle (#10693 review F2). OFF by default, and the default is the ruling:
#:
#:   * the automatic delete is authorised "when the worktree that owns it is
#:     gone", so removing a container whose worktree STILL EXISTS (arm 2) is
#:     outside it by the ruling's own words;
#:   * the ruling says both "the age backstop applies only to
#:     owner-INDETERMINATE containers" and "if ownership cannot be determined,
#:     do NOT delete — report only" about the SAME population (arm 3), so a
#:     lane's pick between two contradictory owner sentences is not a decision.
#:
#: Both arms are therefore ARMED MACHINERY BEHIND AN EXPLICIT OPERATOR ACTION, and
#: the default REPORTS each candidate with the evidence it gathered and the one
#: command that releases it from its own worktree. This is the same shape — and
#: for the same ruling — as ``STOPPED_REAP_ENV`` above (#9338): the measured relief
#: is preserved, and nothing is adopted over the decision. The reopen carrying
#: cell-1's census, the /proc blind-spot closure and the 15.6 h-vs-24 h bound is on
#: #8180; if the owner rules for either arm, the default becomes that ruling and
#: these variables disappear.
LANE_GONE_REAP_ENV = "TORTOISE_TEST_LANE_REAP_LANE_GONE"
OWNER_INDETERMINATE_REAP_ENV = "TORTOISE_TEST_LANE_REAP_OWNER_INDETERMINATE"


#: Spellings that DISABLE automatic reaping. One predicate for both consumers —
#: `start()`'s sweep and the exit hook — because they used to read the same
#: variable two different ways (`start()` enabled only on a literal "1", so
#: `=false` disarmed the sweep and left the hook armed) (#10813 review).
#:
#: ⛔ THE EMPTY STRING IS IN HERE ON PURPOSE (#10813 review P3-5). `start()` used
#: to enable on a literal "1", so `TORTOISE_TEST_LANE_REAP=` — what
#: `export X=${SOMETHING_UNSET}` produces — meant OFF. Reading it as ON would
#: silently ARM a deletion switch for an operator whose "off" had just been
#: spelled with an empty value, which is the fail-open direction on the one
#: variable whose whole job is to disarm a tool that removes containers.
_REAP_OFF = frozenset({"", "0", "false", "no", "off"})


def _reap_enabled() -> bool:
    """Is automatic container reaping on for this process?"""
    return os.environ.get("TORTOISE_TEST_LANE_REAP", "1").strip().lower() \
        not in _REAP_OFF


def _env_consent(var: str) -> bool:
    """Whether the OPERATOR asked for a deletion that crosses a recorded boundary.

    Strict ``"1"``: any other spelling is not consent for a destructive default.
    """
    return os.environ.get(var, "0") == "1"


def _stopped_reap_enabled() -> bool:
    """Whether the OPERATOR asked for the withheld proof-2 reap (#8180)."""
    return _env_consent(STOPPED_REAP_ENV)


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


#: Did the most recent `start()` CREATE the container (True) or REUSE one that was
#: already up (False)?
#:
#: The exit hook may only remove a container **this `run` created**. A container
#: that was already running belongs to someone else — most importantly to the
#: DOCUMENTED `uri` route (a lane sources the export line and then runs pytest in
#: that shell), which writes no `run` marker and may hold no redis connection at
#: all while a heavy non-DB test file runs. Measured recipe against the previous
#: hook: shell B's `run` exiting reaped shell A's container mid-suite, and A
#: reported connection errors as test verdicts (#10813 review P1). `reap_orphans()`
#: would have kept it — so the hook must be at least as strict as the decision
#: function, and this is the cheapest way to be.
_LAST_START_CREATED = False


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
    #
    # ONE `repo_root()` read for the whole `start()`: the container NAME, the
    # ownership LABEL pair, the ORPHAN SWEEP's own identity (`own_name=`) and
    # every removal guard below (`root=`) are all derived from THIS read, and
    # `repo_root()` can degrade per call (see `_lane_identity`). Threaded in
    # explicitly so no later second read can disagree with the container this
    # call just created. That disagreement is not hypothetical: the guard used
    # to re-read `repo_root()` itself, and a degraded read there REFUSES to
    # clean up OUR OWN half-started container, leaving it holding a reserved
    # port — the #8180 leak, produced by the guard meant to prevent it.
    name, root = _lane_identity()
    if _reap_enabled():
        try:
            for _line in reap_orphans(own_name=name):
                print(_line, file=sys.stderr)
        except Exception as _exc:   # a sweep that cannot run must not stop a lane
            print(f"reap: the sweep failed and was skipped ({_exc!r}); "
                  f"nothing else is affected", file=sys.stderr)
    state = container_state(name)
    global _LAST_START_CREATED
    _LAST_START_CREATED = state != "running"
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
        _touch_lease(name)   # the lane has a usable DB: this is USE
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
        _assert_removal_allowed(name, root=root)
        prior = _termination_report(name, live=False)
        outcome = _remove_and_describe(name, root=root)
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
        # #8180: ownership stamped INTO the container so it outlives the
        # worktree teardown that destroys the name's meaning (see `LABEL_OWNER`).
        # `root` is `start()`'s single `repo_root()` read — the same one the name
        # came from — so the two records cannot name different worktrees.
        *_label_args(root),
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
            f"test-lane: {name} never answered PING; "
            f"{_remove_and_describe(name, root=root)}. "
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
            f"{_remove_and_describe(name, root=root)}"
        )
    _touch_lease(name)   # the lane has a usable DB: this is USE
    return name, published


def _assert_removal_allowed(name: str, root: Path | str | None = None) -> None:
    """The ownership guard EVERY removal intent must pass, factored out so a
    read that precedes a removal cannot reach docker for a container this tool
    does not own (and so the rule lives in one place instead of at call sites).

    ``root`` is the root the CALLER derived ``name`` from — `start()` takes
    exactly ONE `repo_root()` read (`_lane_identity`) and threads it into every
    removal that reaches this guard. It is not cosmetic: without it the guard
    compares
    against a FRESH `repo_root()`, and `repo_root()` has a documented PER-CALL
    degraded mode (`Path.cwd()` on a git timeout — the overloaded hosts this
    tool exists for). A read that degraded between the two would then refuse to
    clean up the container the SAME call just created, leaving it holding a
    reserved port: the #8180 leak, produced by the guard meant to prevent it.

    No production call site omits it. `start()` reads the root ONCE
    (`_lane_identity`) and threads it into the THREE removals that reach this
    guard: the stale-container replacement, the never-answered-PING cleanup and
    the publishes-no-port cleanup. `start()`'s fourth removal — the
    `docker run`-failed cleanup — never consults a root at all, and must not:
    it targets the container ID docker itself printed for the container THAT
    call just created (falling back to the name only when no ID was reported,
    under `is_managed`), while this guard compares NAMES, so an ID could never
    match. `stop()` removes directly rather than through this guard. The
    default is retained only so the guard stays callable without a prior read;
    a caller that HAS read the root passing `None` is the bug this parameter
    prevents.
    """
    if not is_managed(name):
        raise SystemExit(f"test-lane: refusing to remove unmanaged {name!r}")
    # The strongest form of "the target is not a parameter": this tool removes
    # exactly the container ITS OWN worktree derives, and nothing else — not
    # even a peer lane's `fdb-lane-*`. Ownership is then an invariant the code
    # enforces, instead of call-site discipline the suite cannot check.
    mine = lane_name() if root is None else container_name(slug_for(root))
    if name != mine:
        raise SystemExit(
            f"test-lane: refusing to remove {name!r} — this worktree's container "
            f"is {mine!r}; a peer lane's database is not ours to delete"
        )


def _remove_and_describe(name: str, root: Path | str | None = None) -> str:
    """Remove a container this tool just created, and say what actually
    happened — a cleanup whose result is unread is how a running container with
    a reserved port survives to make every later `start()` refuse.

    Guarded like every other removal intent, so "no removal bypasses the
    ownership check" is true of the whole module and not just of `stop()`.
    ``root`` is passed straight through to that guard (see
    `_assert_removal_allowed`): a caller that already read the root must not
    make the guard read it a second time.
    """
    _assert_removal_allowed(name, root=root)
    rm = _docker("rm", "-f", name)
    return ("removed it" if rm.returncode == 0
            else "FAILED to remove it — check `docker ps -a`")


def _worktree_slugs() -> dict[str, str] | None:
    """`slug -> worktree path` for every worktree git still knows about.

    `None` means the ownership record could NOT be read. That is deliberately
    distinct from `{}` ("git answered, and nothing claims any container"): the
    first must never authorize a delete, the second may. #8180 fail-closed rule.

    This is the only read in `reap_orphans()` that takes its own root, and it is
    safe to: it answers "which strangers does git still list", never "is the
    container this call created mine" (that identity is threaded in — see
    `reap_orphans`'s ``own_name``). A degraded cwd here is `Path.cwd()`, which is
    inside this very repo, so the list is unchanged; if git answers with FEWER
    worktrees (or fails, which is `None` and fail-closed) the affected container
    drops out of the DETERMINED bucket into INDETERMINATE — where the default
    REPORTS and removal needs an explicit operator switch
    (`OWNER_INDETERMINATE_REAP_ENV`), and where a live lane is still spared by
    the `/proc` arm.
    """
    try:
        r = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_root()),
            capture_output=True, text=True,
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


def _trusted_label_owner(name: str, stamped: str,
                         stamped_slug: str) -> tuple[str, str]:
    """``(owner, contradiction)`` — the label's owner path, ONLY if it describes
    the container it is stamped on.

    A label is a fact written by SOMEONE (possibly a predecessor version, a
    copied `docker run`, or a hand-written `--label`), so it is an ownership
    record only when it CORROBORATES the thing it labels:

      * the pair must be self-consistent — ``slug_for(worktree) == slug`` — which
        is the only reason `LABEL_SLUG` is written; and
      * that slug must be THIS container's own name slug, i.e. the label names
        the very worktree whose digest created this container.

    Anything else is reported and IGNORED as an ownership record: without this
    a stale or copied label (measured shape: ``worktree=/tmp``) keeps a
    container forever under a path it never came from. Ignored is not deleted:
    the container falls back to the name record and then to whatever arm its
    state licenses.

    ``("", "")`` when there is no label at all: absence is not a contradiction,
    it is the pre-label population, and it must stay silent.
    """
    if not stamped:
        return "", ""
    if not stamped_slug:
        return "", (f"it names {stamped} but carries no {LABEL_SLUG} label, so "
                     f"the pair cannot be corroborated")
    digest = slug_for(stamped)
    if digest != stamped_slug:
        return "", (f"its {LABEL_SLUG} label ({stamped_slug}) is not the digest "
                     f"of the worktree it names ({stamped} -> {digest})")
    if stamped_slug != name[len(NAME_PREFIX):]:
        return "", (f"its {LABEL_SLUG} label ({stamped_slug}) is not this "
                     f"container's own slug ({name[len(NAME_PREFIX):]}), so it "
                     f"describes a DIFFERENT worktree")
    return stamped, ""


_WORKTREE_MARK = "/.worktrees/"


#: A container name this tool could have created carries `slug_for()`'s 10-hex
#: digest. A name that does not (measured: `fdb-lane-fix9326`, `fdb-lane-tm9190`)
#: belongs to an older or different producer, so NOTHING on this host can ever
#: name its owner — and a container whose owner this tool cannot name is
#: REPORT-ONLY (#5084), never a backstop candidate.
_HEX_SLUG = re.compile(r"^[0-9a-f]{10}$")


def _worktree_of(text: str) -> str | None:
    """The worktree ROOT a string names, or None.

    A lane's process does not always have its worktree as its cwd — it may be a
    `bash -c 'cd <worktree> && ...'` wrapper (measured 2026-11-03: a live
    `/home/cmux/tortoise/.worktrees/quota-dims` lane had NO process whose cwd was
    in it, only one whose COMMAND LINE named it). A cwd-only liveness test
    therefore reports a live lane as dead, which is exactly how a reaper becomes
    an outage generator.

    The match is anchored ON the `/.worktrees/` token, not on "everything left of
    the mark": a command line is `bash -c 'cd <WT>; ...'`, and a prefix splice
    yields `bash -c 'cd <WT>/...` — never a directory. The terminator set
    deliberately includes `;`, `|`, `&`, quotes and parens, because a shell
    wrapper glues those straight onto the path (`cd <WT>;uv run pytest`).

    Only a path that RESOLVES TO A DIRECTORY is returned, so a worktree name that
    appears in a log line or a heredoc cannot be mistaken for a live owner.
    """
    for m in re.finditer(r"(/(?:[^\s'\"`;|&()]*?))/\.worktrees/([^\s/'\"`;|&()]+)",
                         text):
        root = m.group(1) + _WORKTREE_MARK + m.group(2)
        try:
            if os.path.isdir(root):
                return root
        except OSError:
            continue
    return None


def _holds(live_path: str, owner: str) -> bool:
    """Is a live process working in `owner`, the worktree that owns a container?

    A path UNDER the owner counts — but a plain repo checkout is an ANCESTOR of
    every worktree created inside it (`/home/x/repo` contains
    `/home/x/repo/.worktrees/a`), so a naive prefix test means every live lane in
    the repo holds the main clone's container forever, and that arm never fires.
    When the owner is not itself a worktree, a live path inside a nested worktree
    of it is therefore a different lane, not this owner.
    """
    o = owner.rstrip("/")
    if live_path.rstrip("/") == o:
        return True
    if not live_path.startswith(o + "/"):
        return False
    return not (_WORKTREE_MARK not in o and _WORKTREE_MARK in live_path)


def _live_lane_paths() -> set[str] | None:
    """Every directory a LIVE process on this host is working in.

    Read from ``/proc`` rather than from ``git worktree list``, and that is the
    point: `git worktree list` only knows the clone it is run in, so a container
    owned by ANOTHER clone on the same box (measured: `/tmp/cell-t3/.worktrees/...`)
    is invisible to it and reads as ownerless. ``/proc`` names the directory of
    every live process regardless of which clone — or which LAYOUT — it came
    from.

    Two sources, because neither alone is enough (both measured 2026-11-03):

    * **cwd** — the strongest signal and layout-independent, but a lane that
      shells out through `bash -c 'cd <wt> && ...'` has NO process whose cwd is
      in the worktree (a live `quota-dims` lane was exactly this, and a
      cwd-only test called it dead);
    * **cmdline** — catches that wrapper, but a command line names paths in many
      shapes, so a token only counts when it RESOLVES TO A DIRECTORY.

    The `/.worktrees/` root extraction is kept alongside the raw tokens because
    it yields the worktree ROOT for the container-name slug, which the raw cwd
    of a subdirectory process would not.

    ``None`` means ``/proc`` could not be read, which authorises NO removal: an
    unreadable liveness surface must never be read as "nothing is alive".
    """
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return None
    if not pids:
        return None
    found: set[str] = set()

    def keep(path: str) -> None:
        try:
            if os.path.isdir(path):
                found.add(path)
        except OSError:
            return

    for pid in pids:
        # A process that exited mid-scan has no `cwd` to read; that skip is the
        # whole body, so `contextlib.suppress` states it (ruff SIM105).
        with contextlib.suppress(OSError):
            keep(os.readlink(f"/proc/{pid}/cwd"))
        try:
            raw = (Path(f"/proc/{pid}/cmdline").read_bytes()
                   .decode("utf-8", "replace").replace("\0", "\n"))
        except OSError:
            # A process that exited mid-scan, or a pid this user may not read.
            # The scan is an ENUMERATION: a missed live lane only ever costs a
            # missed SPARE in the arm that reports rather than deletes, and a
            # wholly unreadable /proc is `None` above.
            continue
        for token in raw.split():
            if token.startswith("/"):
                keep(token)
        root = _worktree_of(raw)
        if root:
            found.add(root)
    if not found:
        # Not one path could be established from any pid. That is "unreadable",
        # not "nothing is alive": a sweep that sees no process at all must not
        # conclude that every lane is dead.
        return None
    return found


def _container_in_use(
        name: str,
        *,
        idle_within_s: float | None = ACTIVE_CLIENT_IDLE_S) -> bool | None:
    """Does the container itself say a client is using it right now?

    The one liveness datum that does not depend on knowing the owner at all: it
    is the container's own statement, so it still answers for a container whose
    owner this clone cannot resolve. ``CLIENT LIST`` includes the connection
    that asked (``cmd=client|list``), which is excluded or every container would
    report itself busy.

    ``idle_within_s`` is the QUESTION, and the two callers ask different ones:

      * the SWEEP passes the default — "has anyone attached *recently*", i.e.
        within ``ACTIVE_CLIENT_IDLE_S``. That is the unused-lease measure, and a
        client idle for a full suite step is the population arm 3 exists for.
      * the EXIT HOOK passes ``None`` — "is ANYTHING attached at all". A peer
        consumer that established the container with `uri` and went quiet for
        longer than ``ACTIVE_CLIENT_IDLE_S`` still holds an open socket, and
        removing the container under it fails ITS run with connection errors
        that read as test verdicts (#10813 review P2). The hook removes a
        container only AFTER its own child has exited, so nothing of its own can
        still be attached: any remaining client belongs to someone else.

    ``None`` when the container could not be asked (exec failed, no `redis-cli`,
    a wedged server). Unreadable is NOT "unused": it authorises no removal.

    ⚠️ `CLIENT` and `LIST` are passed as SEPARATE argv entries on purpose.
    `redis-cli "CLIENT LIST"` is ONE argument, and the server answers
    ``ERR unknown command 'CLIENT LIST'`` — while `docker exec` still exits **0**.
    Measured 2026-11-03: that made this arm silently answer "not in use" for every
    container (a false negative that reaps a live lane), so the ERR body is
    checked as well as the exit code.
    """
    r = _docker("exec", name, "redis-cli", "CLIENT", "LIST")
    if r.returncode != 0:
        return None
    body = r.stdout or ""
    # ALLOWLIST, not a blocklist: the reply counts as a client listing only when
    # at least one line carries `id=<n>`. A `LOADING`/`BUSY`/`NOAUTH`/`WRONGTYPE`
    # reply prints a bare error line and `redis-cli` still exits 0, so a
    # two-string blocklist would fall through to `return False` — the same
    # "unused" false negative this function exists to prevent. Polarity decides
    # here, exactly as the merge rail's verdict does.
    if not re.search(r"\bid=\d+", body):
        return None
    for line in body.splitlines():
        cmd = re.search(r"\bcmd=(\S+)", line)
        if cmd and cmd.group(1) == "client|list":
            continue  # this probe, not a client
        idle = re.search(r"\bidle=(\d+)", line)
        if idle_within_s is None:
            # "is ANYTHING attached?" — a client with no `idle=` field at all (a
            # pub/sub or MONITOR client) is attached just as much as a quiet one.
            return True
        if idle and int(idle.group(1)) < idle_within_s:
            return True
    return False


#: Where the "demonstrably served nobody" record for each container lives.
#:
#: Three wrong measures were tried and rejected, each caught by review:
#:   * `StartedAt` — `start()` REUSES a running container, so a lane that has
#:     been up for hours has an hours-old `StartedAt` even while it holds its
#:     database every minute. Using it as the grace deleted LIVE lanes at the
#:     first instant they were between steps (cycle 2, P0).
#:   * "last sweep that observed a hold" — a sweep only sees a hold if it happens
#:     to run during one, so the gap measured the PEER-SWEEP INTERVAL, not
#:     idleness; a lane holding its container for 3 h with no peer start was
#:     reaped at the next observation (cycle 3, P0).
#:   * a NAME-keyed stamp — a lane's own `down` or the next `start()` replaces a
#:     container and keeps the NAME, so the new instance inherited the dead one's
#:     gap (cycle 3, P0).
#:
#:   * the server's `INFO stats` counter — THE REAPER'S OWN PROBE INCREMENTED IT,
#:     so the gap could never elapse and the arm was inert (caught by the
#:     real-container proof, not by a unit test).
#:
#: What is implemented is a LEASE: `<container id> <epoch>`, refreshed by
#: `_touch_lease` at every successful `start()`, at the END of every `run` (the
#: lane just used the container for as long as the child ran), at a first
#: sighting, AND at every POSITIVE observation of use by a peer sweep (a live
#: process holding the owning worktree, or an attached client). The clock is the
#: wall time since that lease — "the last moment the container was demonstrably
#: in use", never "the last time the lane called `start()`". During a long `run`
#: the lane's own process holds the worktree, which is `_holds`' spare; the lease
#: covers the between-steps gaps.
#:
#: ⛔ THE REFRESH AT THE END OF `run` IS LOAD-BEARING, not decoration (#10693
#: review F1): with the epoch written only at `start()`, the gap is measured from
#: the BEGINNING of the last use, so a run longer than `OWNER_GRACE_H` ENDS
#: already expired and the next peer sweep may reap the container in the
#: between-steps window the grace exists to cover — and one full-suite step is
#: measured at ~2 h wall, i.e. the boundary is reached by normal work.
#:
#: Keyed to the INSTANCE id, so a replaced container is a first sighting and never
#: inherits a dead one's gap.
#:
#: The uncovered class, stated as a MECHANISM rather than an instance: a container
#: that (a) no process on this host names, (b) whose lane has neither `start()`ed,
#: nor `run` pytest against it, nor been OBSERVED using it (a hold on the worktree
#: or an attached client — either refreshes the lease) for the grace, and (c) has
#: no client attached within `ACTIVE_CLIENT_IDLE_S` — is reaped by design. A `run`
#: invoked from OUTSIDE its worktree for longer than the grace is one instance of
#: it.
#:
#: RESIDUAL (documented, not hidden): the lease lives under this box's
#: `CELL_HOME`, so a lane running under a DIFFERENT `CELL_HOME` writes its lease
#: elsewhere and this sweep sees only its own first sighting. That population is
#: spared by `_live_lane_paths`' `/proc` scan whenever a process names the
#: worktree, and otherwise bounded by `ORPHAN_MAX_AGE_H` in arm 3.
REAP_STATE_DIR = (Path(os.environ.get("CELL_HOME", os.path.expanduser("~")))
                  / ".pi" / "agent" / "state" / "lane-reap")


def _reap_state_path(name: str) -> Path:
    # Cannot traverse: docker container names may not contain `/` (the daemon's
    # name grammar is `[a-zA-Z0-9][a-zA-Z0-9_.-]*`), so the single path segment
    # is the whole invariant. `is_managed()` only checks the `fdb-lane-` PREFIX
    # and would not stop a slash on its own.
    return REAP_STATE_DIR / f"{name}.unused"


#: A `run` in progress records itself as `REAP_STATE_DIR/<container>.run.<pid>`.
#:
#: ⛔ NOT a single exclusive `flock`. The first version of this guard took one
#: exclusive lock, which is per-CONTAINER — so only one `run` could hold it and
#: the SECOND run held nothing at all, answering "no peer" from its own exit
#: probe. When the holder exited first it removed the non-holder's live
#: container: the exact failure the guard was written to prevent, green in the
#: tests because they only exercised the other ordering (#10813 review P1).
#: A per-PID marker expresses "one OR MORE peers", which is what the question
#: actually is.
def _run_marker_path(name: str, pid: int) -> Path:
    return REAP_STATE_DIR / f"{name}.run.{pid}"


def _pid_alive(pid: int) -> bool:
    """Is `pid` a live process? An EPERM probe still means it exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True          # EPERM: alive, just not ours to signal
    return True


@contextlib.contextmanager
def _run_marker(name: str):
    """Record this `run` as live for its whole child. Never raises.

    Yields whether the record could be written — an unwritable state dir means a
    peer's presence cannot be established, which is `None`, not `False`.
    """
    pid = os.getpid()
    path = _run_marker_path(name, pid)
    written = False
    try:
        REAP_STATE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{pid}\n")
        written = True
    except OSError:
        written = False
    try:
        yield written
    finally:
        if written:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)


def _peer_run_is_live(name: str) -> bool | None:
    """Is another `run` live on this container?

    ``None`` means the marker directory could not be read — the hook treats that
    as "a peer may be live" and declines to reap (fail-closed). The caller must
    therefore test ``is not False``, not truthiness: ``None`` is falsy and would
    silently re-open the hole (#10813 review P2).

    Stale records (a marker whose pid is gone — a `run` that was SIGKILLed) are
    removed as they are found, so the check is self-healing.
    """
    try:
        entries = list(REAP_STATE_DIR.glob(f"{name}.run.*"))
    except OSError:
        return None
    mine = os.getpid()
    for entry in entries:
        try:
            pid = int(entry.name.rsplit(".", 1)[1])
        except (IndexError, ValueError):
            continue
        if pid == mine:
            continue
        # PID REUSE: a marker left by a SIGKILLed run can name a pid the kernel
        # has since given to an unrelated process, which would read "peer live"
        # for as long as that process lives — a fail-closed leak with no
        # self-healing path.
        #
        # ⛔ ASK THE PROCESS ITSELF WHEN IT STARTED (#10813 review P3-8). The
        # first version answered with an AGE (`older than 24 h ⇒ stale`), which
        # is the opposite error: the longest-lived `run`s — a wedged lane, the
        # exact population this whole change exists for — lose their peer
        # protection to a clock, and a peer's hook may then remove their
        # container mid-suite. A pid the kernel has REUSED was necessarily
        # started AFTER the marker was written, so that comparison settles it
        # with no guessed bound.
        #
        # ⛔ …AND THE AGE IS THEN ONLY A FALLBACK (#10813 review round 3 P2-1).
        # It is a fallback for a platform that cannot answer (`/proc` absent —
        # macOS), NOT a second vote: an unconditional `not aged` term let the
        # clock OVERRIDE a positive "this pid was not reused" on Linux, so a
        # genuine live peer older than 24 h was still declared stale and its
        # container removed — the destructive direction, on the population the
        # bound was never meant to reach. `_pid_started_after` answers `None`
        # only when it cannot look, which is exactly when the age may speak.
        #
        # ⛔ A PID OF `0` OR LESS NEVER NAMES A RUN (#10813 review round 3 P3-5).
        # A truncated write or a hand-edited state dir can leave `<name>.run.0`;
        # `os.kill(0, 0)` and `os.kill(-1, 0)` succeed (they address a process
        # GROUP, not a process), and `/proc/0` does not exist, so the marker
        # would read "peer live" forever with no self-healing path — the same
        # permanent-block class the reuse check exists to remove.
        if pid <= 0:
            with contextlib.suppress(OSError):
                entry.unlink(missing_ok=True)
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            mtime = None
        started_after = None if mtime is None else _pid_started_after(pid, mtime)
        reused = started_after is True
        aged = (started_after is None and mtime is not None
                and (time.time() - mtime) > 24 * 3600)
        if not reused and not aged and _pid_alive(pid):
            return True
        with contextlib.suppress(OSError):
            entry.unlink(missing_ok=True)
    return False


def _pid_started_after(pid: int, when: float) -> bool | None:
    """Did `pid` start AFTER the wall-clock instant `when`?

    ``None`` when the platform cannot answer (`/proc` absent — macOS), which the
    caller must NOT read as "started before": the only consumer uses a positive
    `True` to declare a marker stale, so unknown has to leave the marker alone.

    `/proc/<pid>/stat` field 22 is the process start time in clock ticks since
    boot; `/proc/stat`'s `btime` is the boot instant. Both are needed, and the
    field is read from the LAST `)` onwards because field 2 (`comm`) is a
    free-form string that may itself contain spaces and parentheses.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        tail = raw[raw.rindex(")") + 1:].split()
        start_ticks = int(tail[19])          # field 22 overall = index 19 here
        btime = None
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                btime = float(line.split()[1])
                break
        if btime is None:                    # pragma: no cover - /proc is whole
            return None
        started = btime + start_ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None
    # A MARGIN, because the two answers are easy to mis-align by a tick: `btime`
    # is whole seconds and the tick count is 10 ms-grained, so this clock can sit
    # a fraction BEHIND the file's mtime. The asymmetry decides the margin — a
    # false "reused" removes the container of a LIVE peer `run` (the destructive
    # failure), while a missed reuse — a NEW process wearing the pid inside those
    # 2 s — leaves the marker reading "peer live" until that pid exits. It only
    # ever SPARES, so it cannot remove anything; and `aged` deliberately does not
    # collect it either, because `aged` applies only where this function answered
    # `None`, which is precisely not the case where it missed.
    return started > when + 2.0


def _drop_lease(name: str) -> None:
    """Best-effort removal of a lease record. Never raises.

    `unlink(missing_ok=True)` suppresses ONLY `FileNotFoundError`: a read-only
    parent raises `PermissionError`, and this is called immediately after a
    `docker rm -f` inside `reap_orphans`, which `start()` calls on EVERY lane
    start. An escape there would make every lane on the box unable to start —
    the contract this module states ("a sweep that cannot run must not stop a
    lane from starting").
    """
    try:
        _reap_state_path(name).unlink(missing_ok=True)
    except OSError as exc:
        print(f"reap: could not clear the lease for {name}: {exc}",
              file=sys.stderr)


def _container_id(name: str) -> str | None:
    """The container INSTANCE id, so a reused NAME is not mistaken for the same
    container. None when docker cannot be asked (never treated as equal)."""
    r = _docker("inspect", "--format", "{{.Id}}", name)
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip()[:12] or None


def _ancestors(path: str):
    """Every ancestor of a live path, so a lane in `<WT>/tools` still names `<WT>`.

    A worktree need not live under `/.worktrees/` at all (a cross-clone lane at
    `/tmp/cell-t3/wt-a`), so ancestors are walked to the filesystem root. An extra
    slug can only SPARE a container, never remove one.
    """
    cur = path.rstrip("/")
    while cur and cur != "/":
        yield cur
        nxt = os.path.dirname(cur)
        if nxt == cur:
            # POSIX keeps a leading double slash: `os.path.dirname("//") == "//"`,
            # so a path like `//a` (a cmdline token, or a `$PREFIX/$rel` with
            # PREFIX="/") would spin forever — and `reap_orphans` runs on EVERY
            # lane start, so that is a hang for every lane on the box.
            break
        cur = nxt


def _unused_gap(name: str) -> float | None:
    """Hours this container has gone without its LANE touching it; None on first look.

    The record is a LEASE the lane itself refreshes (`_touch_lease`), keyed to the
    container INSTANCE. Three earlier measures were tried and each was caught by
    review as a live-lane kill:
      * `StartedAt` — `start()` REUSES a running container, so uptime is not
        idleness (cycle 2, P0).
      * "time since a sweep happened to observe a hold" — a sweep sees a hold only
        if it runs during one, so that measured the PEER-SWEEP INTERVAL
        (cycle 3, P0).
      * the server's own `INFO stats` counter — THE REAPER'S OWN PROBE INCREMENTS
        IT, so the gap could never elapse and the arm was inert (caught by the
        real-container proof, not by a unit test).
    A lane-held lease has none of those properties: it is written by the party
    that USES the container, it is not disturbed by the sweeper, and it dies with
    the container because it is keyed to the instance id.

    None means "no observation of a full gap yet" — a first sighting, or a
    different container wearing the same name — and the caller keeps.
    """
    now = time.time()
    cid = _container_id(name)
    prior = None
    try:
        raw = _reap_state_path(name).read_text().split()
        if len(raw) == 2:
            prior = (raw[0], float(raw[1]))
    except (OSError, ValueError):
        prior = None
    if prior is None or cid is None or prior[0] != cid:
        _touch_lease(name)
        return None
    return (now - prior[1]) / 3600.0


def _touch_lease(name: str) -> None:
    """Record that the container is demonstrably IN USE right now.

    Called at every successful `start()`, at the END of `cmd_run`'s child, at a
    first sighting, and at every POSITIVE observation by a peer sweep: a live
    process holding the owning worktree (``held`` / ``live_slugs``) or an
    attached client (``_container_in_use`` — at the sweep and at the moment of
    action). Re-stamping at the end of the `run` is what makes the grace a
    BETWEEN-steps bound rather than an "age since the last `start()`" bound
    (#10693 review F1); without it a run longer than the grace ends with an
    already-expired lease.

    The sweep-side stamps are the other half of the same rule (#10693 review F1)
    and they are what makes the promise above (`REAP_STATE_DIR`) true: the
    documented `uri="$(... test_lane.py uri)"; eval "$uri"` form calls `start()`
    ONCE, so a lane that then runs pytest against the exported URI for hours
    would age its lease past `OWNER_GRACE_H` while being completely alive. A sweep
    that OBSERVES the hold or the client refreshes the epoch, so the gap is "time
    since the container was last demonstrably in use". Stamping only ever
    SHORTENS a gap — it can never enable a removal — so it is safe on every
    fail-closed/keep path. Cost: one `inspect` per observed-in-use container per
    sweep, bounded by the number of live lanes — paid only for containers the
    sweep observed IN USE, never on the common keep path.

    What it deliberately does NOT claim: that the lease follows every step of a
    live lane. No sweep can see a lane that names nothing on this host, so a
    container whose lane is invisible to `/proc` ages from its last observation —
    which is exactly what the age backstop is for. The same holds for a lane that
    keeps using an ALREADY-started container without a `run`-end stamp (the
    documented `uri` / `eval "$uri"` form): its lease advances only when some
    sweep OBSERVES that use, so if no sweep runs during the lane's final
    `OWNER_GRACE_H` the gap over-reports by the unobserved stretch. Under the
    DEFAULT that only mis-states a report; on the ARMED arms it advances the
    grace, so a lane that no sweep OBSERVED for `OWNER_GRACE_H` is a removal
    candidate while it is between steps — the class the block above already names
    as reaped by design, and what the opt-in accepts. It is never a mid-RUN
    removal: a sweep that can see the lane keeps it outright (`held` /
    `live_slugs`), and both reads are re-taken at the moment of action.

    RESIDUAL (documented in the PR): a `run` invoked from OUTSIDE its worktree is
    covered by the lease while it runs, and by nothing afterwards if the lane's
    remaining processes name no worktree. Filed rather than hidden.

    The instance id is stored beside it so a replaced container is a first
    sighting, never an inherited gap.
    """
    cid = _container_id(name)
    if cid is None:
        _drop_lease(name)
        return
    try:
        REAP_STATE_DIR.mkdir(parents=True, exist_ok=True)
        _reap_state_path(name).write_text(f"{cid} {time.time():.0f}\n")
    except OSError as exc:
        # A failure is never read as permission. Dropping the record is what
        # makes that TRUE: a surviving OLD record still matches this container's
        # id, so `_unused_gap` would go on ageing it — reading "this lane just
        # used its DB" as "this lane has not used it for hours".
        _drop_lease(name)
        print(f"reap: could not record the lease for {name}: {exc}",
              file=sys.stderr)


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


def reap_orphans(own_name: str | None = None) -> list[str]:
    """Remove `fdb-lane-*` containers that are PROVABLY out of service.

    The PROOF is never a clock — it is OWNERSHIP, established positively. A clock
    is only ever a BACKSTOP on top of it (see the three proofs below), because a
    clock firing on a live owner's database would be a worse bug than the leak it
    fixes. Proof 1 is a recorded-worktree existence read
    (``Path(owner).is_dir()``):

    1. **The owning worktree is gone** (#8180). Ownership is recoverable from
       the container's own name (it is `slug_for()` of the worktree that owns
       it), so the reaping decision is a lookup rather than a guess. This tool
       can therefore only ever remove a container whose owner it has
       POSITIVELY established is gone — never a peer lane's, and never this
       worktree's own (`stop()` and `cmd_run`'s exit hook are the two things
       that remove that one). Its STATE is consulted only to decide whether the
       container can be ASKED about its clients (``_container_in_use``):
       ``docker exec`` reaches a container only while docker is RUNNING it.
       MEASURED (docker 27): ``created`` answers *"Container … is not running"*
       and ``paused`` answers *"Container … is paused, unpause the container
       before exec"*, both exit 1 — so ``_LIVE_ISH_STATES``
       (``paused``/``created``/``restarting``/``removing``) is precisely the set
       that cannot answer. Those orphans are REPORTED with the one command that
       releases them and never removed, and the line is not an ``undecided``
       tick (``tools/container_reaper.py`` reds the timer on those, which for a
       state no retry can change would be a permanent false alarm).

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

    Age is a BACKSTOP, never the proof (see ``OWNER_GRACE_H`` /
    ``ORPHAN_MAX_AGE_H``). A runnable lane container is only ever removed after
    POSITIVE evidence its lane is gone; a clock can never fire on an owner THIS
    HOST CAN OBSERVE alive — an observed hold or an attached client keeps the
    container and refreshes the lease (``_touch_lease``), so a live lane's
    database is spared at any age, and for ``OWNER_GRACE_H`` after its last
    OBSERVED use (the uncovered population is stated on ``REAP_STATE_DIR``),
    which would be a far worse bug than the leak it fixes — the rule the whole
    module defends. `paused`/`restarting`/`created` are live-ish in exactly
    the same way (``_LIVE_ISH_STATES``) and never reach the STATE arm, which fires
    only on ``_TERMINAL_STATES``.

    THREE proofs are built, and every one of them is ownership of a LANE. Only
    proof 1 fires by default: #8180's ruling authorises a deletion "when the
    worktree that owns it is gone" and does not settle the other two (#10693
    review F2), so 2 and 3 are ARMED MACHINERY BEHIND AN EXPLICIT OPERATOR
    ACTION (``LANE_GONE_REAP_ENV`` / ``OWNER_INDETERMINATE_REAP_ENV``) and their
    default is a report carrying the whole evidence chain and the one command
    that releases the container.

    2. **The owning worktree exists but its LANE is gone** (``OWNER_GRACE_H``).
       Positive evidence, all of it: no live process on this host holds that
       worktree (cwd **or** command line — ``_live_lane_paths``), the container
       reports no client activity (``_container_in_use``), and nothing has held
       it for ``OWNER_GRACE_H``. This is the arm the measured leak needs: the
       worktree SURVIVES `cmux workspace close`, so "the directory is still
       there" was being read as "the lane is still alive" for days. Removal
       needs ``LANE_GONE_REAP_ENV=1`` — WITHOUT IT this arm reports the whole
       evidence chain plus the `down` command that releases the container from
       its own worktree, because the ruling's authority is scoped to a GONE
       worktree and this one exists.

    3. **Ownership cannot be determined from this clone, and the container is
       cold for ``ORPHAN_MAX_AGE_H``.** The `git worktree list` blind spot: the
       container may belong to another clone on the box. ``_live_lane_paths``
       reads ``/proc``, so a live cross-clone lane is still seen and spared; only
       a container that no process names AND that has served no client for the
       whole bound is a candidate. Unresolvable owner with a live process, or with
       unreadable evidence, is still REPORTED and never destroyed. Removal needs
       ``OWNER_INDETERMINATE_REAP_ENV=1``: the ruling says both "the age backstop
       applies only to owner-INDETERMINATE containers" and "if ownership cannot
       be determined, do NOT delete", so the default reports and the owner's
       ruling (reopened on #8180) decides.

    ``own_name`` is the container name the CALLER already derived (``start()``
    takes exactly one `repo_root()` read — see `_lane_identity`). It is not a
    parameter a peer could aim at another lane's container: it only ever names
    the container this sweep must SKIP as "this worktree's own", so a value that
    disagrees with a container merely stops that container being short-circuited
    and sends it through the ordinary ownership resolution below (which keeps it).
    Omitting it (`None`) resolves the name here instead, for the CLI paths.

    Fail-closed: if the worktree list or `docker ps` cannot be read, nothing is
    removed and the reason is reported; a state this module does not recognise
    is reported, never inferred to be terminal. Returns one line per container
    this function ACTED on or decided to KEEP; a name refused by `is_managed()`
    is not reported (it was never a candidate), so a caller reports what
    actually happened instead of counting.

    ONE container can produce TWO lines, and that is deliberate rather than a
    miscount: a label that is not evidence is reported first (``carries an
    ownership label that is not evidence`` — the reason the stamp was ignored)
    and then again by whatever arm the container's own state licenses (the
    outcome). The two lines answer different questions about one container, so
    a reader counting containers must count DISTINCT NAMES, not lines.
    """
    slugs = _worktree_slugs()
    if slugs is None:
        return ["reap: skipped — `git worktree list` failed, so ownership "
                "cannot be determined; NOTHING was removed (fail-closed)"]
    # Read ONCE for the whole sweep. `None` (unreadable) authorises no removal by
    # the LANE-liveness arms — an unreadable liveness surface is not "nothing is
    # alive".
    live_paths = _live_lane_paths()
    # Slugs of live worktree ROOTS: a process sitting in `<WT>/tools` must still
    # name `<WT>` (exact-path slugs missed that, review cycle 2 P1).
    # Slugs of live worktree roots. A live process may sit in a SUBDIRECTORY of
    # its worktree (`<WT>/tools`), and a worktree need not live under
    # `/.worktrees/` at all (a cross-clone lane at `/tmp/cell-t3/wt-a`), so every
    # ANCESTOR of a live path is a candidate root and all their slugs count. An
    # extra slug can only SPARE a container, never remove one.
    live_slugs = None
    if live_paths is not None:
        live_slugs = {slug_for(a) for p in live_paths for a in _ancestors(p)}
    listed = _docker("ps", "-a", "--filter", f"name={NAME_PREFIX}",
                     "--format",
                     '{{.Names}}\t{{.State}}'
                     '\t{{.Label "' + LABEL_OWNER + '"}}'
                     '\t{{.Label "' + LABEL_SLUG + '"}}')
    if listed.returncode != 0:
        return ["reap: skipped — `docker ps` failed; NOTHING was removed "
                "(fail-closed)"]
    states: dict[str, str] = {}
    stamped_labels: dict[str, dict[str, str]] = {}
    for line in (listed.stdout or "").splitlines():
        parts = line.split("\t")
        name = parts[0].strip()
        if not name:
            continue
        # A missing state is `unknown`, never terminal: the state arm may
        # only fire on a state docker actually reported.
        states[name] = ((parts[1].strip().lower() if len(parts) > 1 else "")
                        or "unknown")
        stamped_labels[name] = {
            "owner": parts[2].strip() if len(parts) > 2 else "",
            "slug": parts[3].strip() if len(parts) > 3 else "",
        }
    mine = lane_name() if own_name is None else own_name
    out: list[str] = []
    for name in sorted(states):
        if not is_managed(name):
            continue
        if name == mine:
            out.append(f"reap: kept {name} — this is THIS worktree's container")
            continue
        owner = slugs.get(name[len(NAME_PREFIX):])
        # The slug the container's OWN name encodes. `_live_lane_paths` gives the
        # slugs of every worktree a live process holds, in ANY clone on this host,
        # so this is how a cross-clone owner is still recognised as alive.
        slug_for_name = name[len(NAME_PREFIX):]
        # THE LABEL, when the name can no longer answer. `_worktree_slugs()` maps
        # a slug back to a path through `git worktree list`, so a container whose
        # worktree was REMOVED (the exact event that strands it) resolves to
        # nothing here — and lands in the INDETERMINATE bucket, whose default no
        # owner has ruled on (`OWNER_INDETERMINATE_REAP_ENV`). The label stamped
        # at creation survives that teardown, so it upgrades the container from
        # INDETERMINATE to DETERMINED-AND-GONE, i.e. the ONE proof #8180
        # authorises armed. It never widens WHAT is destroyed: the owner-gone arm
        # still demands the second proof the INDETERMINATE bucket demands
        # (docker's own TERMINAL state, or no attached client across
        # `OWNER_GRACE_H`), so a container a live lane is still using is spared on
        # BOTH paths — the label only decides which proof ladder applies (#10841
        # review, P1: promoting a RUNNING container into a bare `rm -f` was the
        # widening this comment used to deny).
        #
        # ⛔ The label reaches a population the NAME record cannot: a worktree
        # torn down (`git worktree remove --force` / `prune`) while the lane's
        # process is STILL RUNNING. That process's cwd is a deleted directory, so
        # `_live_lane_paths` cannot name it either — the container's own `CLIENT
        # LIST` is the only thing left that knows a test run is in progress. That
        # is why the second proof below is demanded of a label-derived owner as
        # well, and of a name-derived one too: the check is scoped to the ARM ("my
        # owner is gone"), not to how the owner was found (#10693 review F1,
        # #10841 review P1).
        if owner is None:
            stamped = stamped_labels.get(name, {})
            trusted, why_not = _trusted_label_owner(
                name, stamped.get("owner", ""), stamped.get("slug", ""))
            if trusted:
                owner = trusted
            elif why_not:
                # Reported, not believed. A label that does not describe the
                # container it sits on is not an ownership record (see
                # `_trusted_label_owner`); the name record and the age arm are
                # then all that is left, exactly as before this change.
                out.append(f"reap: {name} carries an ownership label that is "
                           f"not evidence — {why_not}")
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
            # ⛔ THE STATE GATE COMES BEFORE EVERY PROOF IN THIS ARM (#10813
            # review P2-1). A container docker reports as `paused`/`created`/
            # `restarting`/`removing` cannot answer `CLIENT LIST` at all
            # (`docker exec` reaches a container only while docker is RUNNING
            # it), so each probe below would return `None` — and a `None` from
            # this arm is reported in the "could not be asked … fail-closed"
            # shape, which is exactly what `container_reaper`'s `undecided`
            # predicate matches. That would make a paused orphan unreapable by
            # any arm AND redden the 15-minute timer on every tick, forever,
            # because no retry can change a paused state.
            #
            # ⛔ ONE gate for the WHOLE arm. Both owner sources funnel HERE
            # (#10712's label and the name record — see the unification above),
            # so a copy per owner source would reintroduce the very defect
            # #10813 review P2-1 fixed: a gate that covered only the
            # name-derived path, leaving the label-derived one to report a
            # permanent state as a transient read failure.
            if state in _LIVE_ISH_STATES:
                # MEASURED (docker 27, both exit 1): `exec` into `created` is
                # "container … is not running", into `paused` is "paused,
                # unpause the container before exec". Probing anyway returns
                # `None`, which keeps the container AND (via the "could not be
                # asked … fail-closed" shape below) marks it `undecided` — see
                # above.
                #
                # ⛔ The wording must NOT contain "unreadable" or
                # "fail-closed": those are exactly what
                # `container_reaper`'s `undecided` predicate matches, and this is
                # a PERMANENT state, not a transient read failure.
                out.append(
                    f"reap: kept {name} — its owning worktree "
                    f"{owner} is gone, but it is {state}, so it cannot be asked "
                    f"about its clients (docker exec only reaches a RUNNING "
                    f"container) and is not removed; release it with "
                    f"`docker rm -f {name}`")
                continue
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
            # ⛔ THE OWNER BEING GONE IS NOT PROOF THE DATABASE IS UNUSED.
            # #8180 authorises the reap "when the worktree that owns it is gone",
            # and this block is that proof — but a lane container OUTLIVES its
            # worktree by design, so "gone" is compatible with a pytest still
            # running against the URI it exported. Before the ownership label a
            # container whose worktree had been deregistered fell into the
            # REPORT-ONLY INDETERMINATE bucket; the label moves it HERE, which
            # would make this the one arm that destroys a RUNNING container with
            # no client probe, no grace and no consent (#10841 review, P1). This
            # module's own worst bug is "a clock can never fire on a live owner's
            # database", so the removal needs the SECOND proof the INDETERMINATE
            # arm already demands: docker's own state, OR no attached client
            # across a full `OWNER_GRACE_H` gap. Terminal is proof on its own
            # (`docker exec` into a stopped container cannot be a live client).
            # Every unreadable answer is report-only, and the gap can only ever
            # SPARE — the same ladder, one bucket over.
            served = ""
            if state not in _TERMINAL_STATES:
                busy = _container_in_use(name)
                if busy is None:
                    out.append(f"reap: kept {name} — its owning worktree "
                               f"{owner} is gone but it is {state} and could "
                               f"not be asked whether a client is using it; "
                               f"report only, fail-closed")
                    continue
                if busy:
                    _touch_lease(name)   # observed use: the epoch follows it
                    out.append(f"reap: kept {name} — its owning worktree "
                               f"{owner} is gone but it HAS an active client, "
                               f"so a live lane is still using it")
                    continue
                gap_h = _unused_gap(name)
                if gap_h is None or gap_h < OWNER_GRACE_H:
                    age_txt = ("first sighting" if gap_h is None
                               else f"only {gap_h:.1f}h of the "
                                    f"{OWNER_GRACE_H:g}h grace")
                    out.append(f"reap: kept {name} — its owning worktree "
                               f"{owner} is gone and no client is attached, "
                               f"but it shows no OBSERVED use ({age_txt}); a "
                               f"lane between steps looks exactly like this")
                    continue
                served = (f"; it is {state} and has served no client for "
                          f"{gap_h:.1f}h > {OWNER_GRACE_H:g}h")
            rm = _docker("rm", "-f", name)
            out.append(f"reap: {'removed' if rm.returncode == 0 else 'FAILED to remove'} "
                       f"{name} — its owning worktree {owner} is gone"
                       + served
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
            # The owner worktree EXISTS. That is not liveness: `cmux workspace
            # close` closes the LANE and leaves the directory behind, so
            # "the directory is still there" was read as "the lane is still
            # alive" for days (measured: 23 of 25 ownered containers on the dev
            # box, 68 on cell-1 outliving their lanes by up to 4 days).
            #
            # So the worktree only establishes WHOSE the container is. Whether
            # that owner is still ALIVE is a separate, positive read — and a
            # live owner a sweep can SEE is never reaped, at any age. A lane that
            # is between steps is bounded by the lease's `OWNER_GRACE_H`, which
            # measures the last OBSERVED use (`REAP_STATE_DIR`).
            held = live_paths is not None and any(
                _holds(p, owner) for p in live_paths)
            if held:
                # POSITIVE use observed: refresh the lease so the gap is "time
                # since the container was last demonstrably in use" — not "time
                # since the lane last called `start()`/`run`" (#10693 review
                # F1). Without this, the documented `eval "$uri"` form ages its
                # lease past `OWNER_GRACE_H` while pytest is running against it,
                # and the first between-steps instant would look like a dead
                # lane. Stamping only SHORTENS the gap: it can never enable a
                # removal.
                _touch_lease(name)
                out.append(f"reap: kept {name} — its owning lane is LIVE in "
                           f"{owner}")
                continue
            age = _started_epoch(name)
            if live_paths is None or age is None:
                # Unreadable liveness or age, and both are needed to prove the
                # lane is gone. REPORT, never destroy (fail-closed) — and the
                # line must not state an unreadable read AS A RESULT (#10693
                # review, P2). `held` above is
                # `live_paths is not None and any(...)`, so when `/proc` could
                # not be read it is False BY CONSTRUCTION, never by a read:
                # saying "no live process holds it" there is the exact reading
                # `_live_lane_paths`' docstring forbids ("an unreadable
                # liveness surface must never be read as 'nothing is alive'").
                # It is not hypothetical — on a host with no `/proc` EVERY
                # ownered peer container takes this branch, so a live lane
                # mid-run was reported as unheld. Arm 3 already words this
                # correctly; these are the same two shapes.
                if live_paths is None:
                    out.append(
                        f"reap: kept {name} — its owning worktree exists "
                        f"({owner}) and whether a live process holds it is "
                        f"UNKNOWN (the liveness surface /proc could not be "
                        f"read); report only, fail-closed")
                else:
                    out.append(
                        f"reap: kept {name} — its owning worktree exists "
                        f"({owner}) and no live process holds it, but its start "
                        f"time could not be read; report only, fail-closed")
                continue
            # The container must be older than the grace before its idleness is
            # even interesting: a container created moments ago belongs to a lane
            # that is still setting up, and `_started_epoch` is a FLOOR, never
            # the measure of the gap (see `REAP_STATE_DIR`).
            age_h = (time.time() - age) / 3600.0
            if age_h < OWNER_GRACE_H:
                out.append(f"reap: kept {name} — its owning lane holds nothing "
                           f"in {owner}, but the container is only "
                           f"{age_h:.1f}h old (< {OWNER_GRACE_H:g}h grace); the "
                           f"client probe has not run yet")
                continue
            busy = _container_in_use(name)
            if busy is None:
                out.append(f"reap: kept {name} — no live process holds {owner} "
                           f"but the container could not be asked whether it is "
                           f"in use; report only, fail-closed")
                continue
            if busy:
                _touch_lease(name)   # observed use: the epoch follows the proof
                out.append(f"reap: kept {name} — no live process holds {owner} "
                           f"but it HAS an active client, so something is using "
                           f"it")
                continue
            # PROOF 2: the owner worktree exists, its lane is gone, and the gap
            # has lasted. The gap is WALL TIME since the lane's own LEASE record
            # was written — not the container's age, and not "time since a peer
            # sweep happened to catch a hold".
            gap_h = _unused_gap(name)
            if gap_h is None:
                out.append(f"reap: kept {name} — the lane that owns {owner} "
                           f"holds nothing now, but the container has not yet "
                           f"been observed unused for {OWNER_GRACE_H:g}h "
                           f"(first sighting, or a replaced container "
                           f"instance); report only")
                continue
            if gap_h < OWNER_GRACE_H:
                out.append(f"reap: kept {name} — no live process holds {owner} "
                           f"and it shows no OBSERVED use, but only "
                           f"{gap_h:.1f}h of {OWNER_GRACE_H:g}h grace has passed")
                continue
            # Moment of action: re-read LIVENESS (not the age — re-reading the age
            # proves nothing, because a lane reuses the running container and
            # `StartedAt` never moves). A lane that began using the container
            # since the sweep snapshot is spared here.
            now_live = _live_lane_paths()
            busy_now = _container_in_use(name)
            now_held = now_live is not None and any(
                _holds(p, owner) for p in now_live)
            if busy_now is True or now_held:
                # The moment-of-action probe POSITIVELY observed use, so refresh
                # the lease here too: a lane that adopted this container mid-sweep
                # would otherwise keep the epoch from before its own `start()`,
                # and the NEXT sweep could find the gap expired during a normal
                # between-steps window (#10693 review F1).
                _touch_lease(name)
            if now_live is None or busy_now is not False or now_held:
                # `busy_now is not False`: None (unreadable) must never fall
                # through the `or` chain to the removal, or an unreadable probe
                # becomes the one fail-open step on a destructive path.
                out.append(f"reap: kept {name} — liveness could not be "
                           f"re-established at the moment of action, or the "
                           f"container became live mid-sweep")
                continue
            if not _env_consent(LANE_GONE_REAP_ENV):
                # The proof is complete and the lane really is gone — but #8180
                # authorises a deletion only "when the worktree that owns it is
                # gone", and this worktree still exists. A lane may not widen
                # that on its own (review F2), so the DEFAULT is the report: it
                # carries the whole evidence chain and the one command that
                # releases the container from its own worktree, and the armed
                # switch names the reopen that would make removal the default.
                out.append(
                    # The report must carry BOTH measures: the container's age
                    # (`age_h`) and the lease gap (`gap_h`) are deliberately
                    # different facts here, and the age is not implied by the
                    # gap — a lane reuses the running container, so a 9 h old
                    # container can have a 3 h unused gap (#10693 review, P3).
                    f"reap: kept {name} — nothing holds {owner} (no live process "
                    f"names it, no client attached within "
                    f"{ACTIVE_CLIENT_IDLE_S}s, {age_h:.1f}h old, no OBSERVED "
                    f"use for {gap_h:.1f}h > "
                    f"{OWNER_GRACE_H:g}h), but its owning worktree still EXISTS "
                    f"and #8180 authorises a reap only when that worktree is "
                    f"gone. Release it from its own worktree: `cd {owner} && uv "
                    f"run python tools/test_lane.py down` (or rule on #8180 to "
                    f"arm `{LANE_GONE_REAP_ENV}=1`)")
                continue
            rm = _docker("rm", "-f", name)
            _drop_lease(name)
            out.append(
                f"reap: {'removed' if rm.returncode == 0 else 'FAILED to remove'} "
                f"{name} — the lane that owns {owner} is GONE (no live process "
                f"holds it, no client attached within "
                f"{ACTIVE_CLIENT_IDLE_S}s, and it shows no OBSERVED use across "
                f"{gap_h:.1f}h > {OWNER_GRACE_H:g}h). The worktree still exists, "
                f"which is why this used to survive")
            continue
        # Owner INDETERMINATE: no worktree in THIS clone claims it. That is not
        # proof of death — the container may belong to a different clone of the
        # repo (measured: `/tmp/cell-t3/.worktrees/...` lanes share this host).
        #
        # The `git` blind spot is closed by reading `/proc` instead, so a live
        # cross-clone lane IS visible here and is spared below. Only a container
        # that NO process on the host names, that has served no client, and that
        # is older than ORPHAN_MAX_AGE_H is destroyed — and an unreadable
        # evidence surface is still REPORT ONLY (the #5084 fail-closed ruling:
        # the clock is a backstop, never the proof).
        # CHEAP GUARDS FIRST: a name this tool cannot have minted needs no docker
        # call at all, and an unreadable liveness surface needs no age read.
        if live_slugs is None:
            out.append(f"reap: kept {name} — no owner here and it is {state}; "
                       f"/proc could not be read, so ownership is NOT "
                       f"determined; report only, fail-closed")
            continue
        if not _HEX_SLUG.match(slug_for_name):
            # No worktree slug can ever spell this name, so its owner is not
            # recoverable by ANY reading on this host. REPORT (#5084); the
            # backstop may not stand in for an owner this tool cannot name.
            out.append(f"reap: kept {name} — its name carries no worktree slug, "
                       f"so its owner cannot be determined here at all; "
                       f"report only, fail-closed")
            continue
        if slug_for_name in live_slugs:
            # POSITIVE use observed (a cross-clone lane's own process names the
            # worktree): refresh the lease, exactly as arm 2 does for a hold
            # (#10693 review F1). This arm is the one a cross-`CELL_HOME` lane
            # depends on, and that lane's lease lives on the other CELL_HOME — so
            # this observation is the only fresh evidence the sweep ever gets.
            _touch_lease(name)
            out.append(f"reap: kept {name} — no worktree in THIS clone names it, "
                       f"but a LIVE process elsewhere on the host owns that "
                       f"worktree, so its lane is alive")
            continue
        age = _started_epoch(name)
        age_h = None if age is None else (time.time() - age) / 3600.0
        age_txt = ("start time unreadable" if age_h is None
                   else f"{age_h:.1f}h old")
        if age_h is None or age_h < ORPHAN_MAX_AGE_H:
            out.append(f"reap: kept {name} — no owner here and it is {state}, so "
                       f"ownership is NOT determined ({age_txt}; backstop is "
                       f"{ORPHAN_MAX_AGE_H:g}h); report only, fail-closed")
            continue
        # A container in a TERMINAL state cannot hold a client — that is positive
        # evidence from docker's own state, not an absence, and `docker exec` into
        # it always fails. Without this short-circuit the backstop could never
        # reclaim a STOPPED ownerless orphan, which is the population that holds
        # ports out of PORT_RANGE.
        busy = (False if state in _TERMINAL_STATES
                else _container_in_use(name))
        if busy is None:
            out.append(f"reap: kept {name} — no owner here and it is {state} "
                       f"({age_txt}) but the container could not be asked "
                       f"whether it is in use; report only, fail-closed")
            continue
        if busy:
            _touch_lease(name)   # observed use: the epoch follows the proof
            out.append(f"reap: kept {name} — no owner here but it HAS an active "
                       f"client, so something is using it")
            continue
        gap_h = _unused_gap(name)
        if gap_h is None:
            out.append(f"reap: kept {name} — no owner here and no client, but it "
                       f"has not yet been observed unused for "
                       f"{ORPHAN_MAX_AGE_H:g}h (first sighting, or a replaced "
                       f"container instance)")
            continue
        if gap_h < ORPHAN_MAX_AGE_H:
            out.append(f"reap: kept {name} — no owner here and no client, but "
                       f"only {gap_h:.1f}h of the {ORPHAN_MAX_AGE_H:g}h backstop "
                       f"has passed")
            continue
        # Moment of action: re-read the STATE *and* the LIVENESS. The `docker ps`
        # snapshot and the `/proc` scan were both taken at the top of this sweep,
        # which on a loaded host is many seconds of docker calls — and arm 3 is
        # the arm a cross-CELL_HOME lane depends on, because its lease lives
        # elsewhere. Re-reading only the state (what this arm used to do) carries
        # no liveness at all: a lane that appeared since the snapshot, or whose
        # client attached since the last probe, would never be seen.
        now_state = container_state(name)
        if now_state != state and now_state not in _TERMINAL_STATES:
            out.append(f"reap: kept {name} — it read {state} in the sweep but is "
                       f"{now_state} at removal time (fail-closed)")
            continue
        now_live = _live_lane_paths()
        now_busy = (False if now_state in _TERMINAL_STATES
                    else _container_in_use(name))
        now_named = (now_live is not None and slug_for_name in
                     {slug_for(a) for p in now_live for a in _ancestors(p)})
        if now_busy is True or now_named:
            # The moment-of-action probe POSITIVELY observed use: refresh the
            # lease, so a lane that adopted this container mid-sweep does not
            # keep an epoch from before its own `start()` (#10693 review F1).
            _touch_lease(name)
        if now_live is None or now_busy is not False or now_named:
            out.append(f"reap: kept {name} — it became LIVE at the moment of "
                       f"action (a lane appeared mid-sweep), or liveness could "
                       f"not be re-established (fail-closed)")
            continue
        if not _env_consent(OWNER_INDETERMINATE_REAP_ENV):
            # #8180's ruling is self-contradictory on exactly this population —
            # "the age backstop applies only to owner-INDETERMINATE containers"
            # and "if ownership cannot be determined, do NOT delete — report
            # only" — so the DEFAULT cannot be a lane's pick between them
            # (review F2). Report the whole chain instead: an operator can act
            # on it, and the armed switch is the operator's explicit widening.
            out.append(
                # `age_txt` (container uptime) is carried here for the same
                # reason as in arm 2: uptime and the lease gap are distinct
                # measures, and the doc promises the report names both
                # (#10693 review, P3).
                f"reap: kept {name} — no worktree in this clone names it, no "
                f"live process on this host owns it, no client attached within "
                f"{ACTIVE_CLIENT_IDLE_S}s, it is {age_txt}, and it shows no "
                f"OBSERVED use for {gap_h:.1f}h (> {ORPHAN_MAX_AGE_H:g}h) — but its "
                f"owner cannot "
                f"be determined HERE (#8180: report only, fail-closed). It may "
                f"belong to another clone/CELL_HOME on this box; remove it only "
                f"if you can attribute it, or rule on #8180 to arm "
                f"`{OWNER_INDETERMINATE_REAP_ENV}=1`")
            continue
        rm = _docker("rm", "-f", name)
        _drop_lease(name)
        out.append(
            f"reap: {'removed' if rm.returncode == 0 else 'FAILED to remove'} "
            f"{name} — no worktree in this clone or live process on this host "
            f"owns it, it has no client attached within "
            f"{ACTIVE_CLIENT_IDLE_S}s, and it shows no OBSERVED use across "
            f"{gap_h:.1f}h (> {ORPHAN_MAX_AGE_H:g}h backstop)")
    return out


def _raise_on_term(signum, frame):
    """Turn SIGTERM into an exception so `cmd_run`'s `finally` can reap.

    Without this a lane stopped the normal way (SIGTERM from its supervisor) ends
    the process without unwinding, so the exit hook never runs and the container
    leaks — exactly the population the hook exists to reclaim. Exit 143 is the
    shell convention for SIGTERM, matching `cmd_run`'s 128+N reporting.
    """
    raise SystemExit(128 + signum)


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
    """The FOUR seam variables that actually make this lane the target.

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

    ``TORTOISE_TEST_LEGACY_PORT`` is the FOURTH axis, and it is the same seam
    as the other three (#5084 remainder). ``_live_utils.legacy_port()`` falls
    back to ``FALKORDB_PORT`` and then to the literal **16379** — the legacy
    family's HISTORICAL port, and NOT where CI's passwordless service listens
    (since #6673 the provision action assigns it an ephemeral port and exports
    ``TORTOISE_TEST_LEGACY_PORT`` for it) — so without this export a test
    that builds its target through ``legacy_uri()`` dials a port nothing serves
    on a lane whose container is on an ephemeral port. Measured on ``main`` at
    ``e4703a286a``
    (2026-11-12): ``tests/test_directional_impl.py`` read
    ``5 failed`` — every one ``redis.exceptions.ConnectionError:
    Error 111 connecting to localhost:16379`` — and ``5 passed`` once
    the legacy port pointed at the SAME private container. The identical 5
    failed both alone and inside the shard, so it was never ordering: it is a
    target this tool did not name. This container is passwordless, which is
    exactly what the legacy family means, so pointing the legacy seam at it is
    the correct target rather than a widening of the tool's scope.
    """
    return {
        "TORTOISE_DB_URI": uri_for(port, graph),
        "TORTOISE_TEST_DOCKER_PORT": str(port),
        "TORTOISE_TEST_DOCKER_PASSWORD": "",
        "TORTOISE_TEST_LEGACY_PORT": str(port),
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


def _names_a_pytest_path(token: str) -> bool:
    """Does this token LOOK like a test path (a file, dir, or node id)?

    Deliberately syntactic and conservative — anything that could be a path
    counts as one:

      * starts with ``-``   -> a flag, not a path
      * contains ``::``     -> a node id
      * contains ``/``      -> a repo-relative path
      * ends with ``.py``   -> a file
      * EXISTS on disk      -> a path, whatever it looks like
    """
    if not token or token.startswith("-"):
        return False
    if "::" in token or "/" in token or token.endswith(".py"):
        return True
    try:
        return Path(token).exists()
    except OSError:      # a token too long/illegal to be a path is not one
        return False


def _argv_names_a_target(argv: list[str]) -> bool:
    """Would pytest treat any of these tokens as a positional TARGET?

    A token that LOOKS like a path is still not a target when the token BEFORE
    it is an option: pytest's grammar lets an option consume the next argument,
    so ``--deselect tests/x.py::t`` and ``-k tests`` carry a PATH-SHAPED VALUE,
    not a target. Treating those as targets handed pytest a pathless command ->
    pytest collects from the ROOTDIR (no ``testpaths`` in pyproject.toml) -> a
    module-level assertion in ``graph-scripts/test_6707_shock.py`` interrupts
    collection (``42099 tests collected, 1 error``, exit 2) -> zero of the
    suite's tests run. That is the exact failure this subcommand exists to
    remove, so the rule is "an option eats the next token", NOT "looks like a
    path".

    KNOWN, DELIBERATE over-inclusion: a BOOLEAN flag also "eats" its next token
    here, so ``run -- -q tests/foo.py`` appends ``tests/`` and runs the suite
    PLUS that file (a superset, announced on stderr). The documented spelling
    puts the target first (``run -- tests/foo.py -q``), and the asymmetry is the
    right way round: over-running costs wall time, under-running costs the
    verdict — a run that tested nothing reads exactly like a run where nothing
    failed.
    """
    previous: str | None = None
    for token in argv:
        if ((previous is None or not previous.startswith("-"))
                and _names_a_pytest_path(token)):
            return True
        previous = token
    return False


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
    died). Hand-``eval``-ing the four exports works but is a step every caller
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
    # ⛔ THE RUN MARKER OPENS BEFORE `start()`, NOT AFTER (#10813 review P2-2).
    #
    # `start()` is not instant: it sweeps orphans, reads the container state,
    # resolves the image and port, and may wait `READY_TIMEOUT` for the first
    # PING. A marker opened only around the CHILD leaves that whole window
    # invisible, and a peer `run` inside it holds no marker and no socket yet —
    # so a concurrently EXITING run's hook sees "no peer, nothing attached" and
    # `docker rm -f`s the container `start()` just handed to it. The peer then
    # runs pytest against a dead database and its connection errors are reported
    # as test verdicts: verbatim the harm this marker exists to prevent.
    #
    # The name is derived from `repo_root()`/`_lane_identity()` alone (no docker),
    # so it can be established first — and `start()` is then required to agree
    # with it. Disagreement means one of the two reads degraded (see
    # `_lane_identity`), and a run whose two ownership records name different
    # worktrees cannot say which container is its own: refuse rather than guard
    # the wrong one.
    name = lane_name()
    port: int | None = None
    created = False
    started = False
    extra = list(args.pytest_args)
    if extra and extra[0] == "--":
        # `run -- -q tests/` is the documented spelling (it survives the same
        # way `cargo run --` and `docker run … --` do), so the separator is
        # consumed here rather than handed to pytest as an unknown argument.
        extra = extra[1:]
    if not _argv_names_a_target(extra):
        # No path anywhere in the argv means THE SUITE — `tests/`. This is not
        # cosmetic: with no path pytest collects from the ROOTDIR (there is no
        # `testpaths` in pyproject.toml), which sweeps `validation/`,
        # `graph-scripts/`, `integrations/tests/` and emits a collection error —
        # a run that tests none of the suite and exits 2. Flag-only calls are the
        # common shape (`-k`, `--collect-only`, `--timeout=N`), so the check is on
        # the whole argv, not on emptiness.
        # PREPENDED, never appended: the rule above is "an option eats the next
        # token", and a default appended at the END is subject to that same rule.
        # `run -- -q -k "$EXPR"` with EXPR unset expands to a trailing `-k`, and
        # an appended `tests/` becomes -k's VALUE — pytest then gets no positional
        # target at all, sweeps the ROOTDIR and exits 2 having run none of the
        # suite, while this line claims it added the suite. First position cannot
        # be eaten by anything.
        extra = ["tests/", *extra]
        print(
            "test-lane: no test TARGET in the pytest argv — adding `tests/` (the "
            "suite). Put a path BEFORE the flags to override, e.g. "
            "`run -- tests/test_guard.py -q`.",
            file=sys.stderr,
        )
    # ── THE EXIT HOOK (#8180's ACTUATOR half — the tortoise side of the seam) ──
    # `run` is the ONE command every lane issues for a suite
    # (`uv run python tools/test_lane.py run -- tests/ -v`, the spelling AGENTS.md
    # documents), so it is where a lane ENDS — and therefore where its container
    # must go. Without this the container is left to the next lane's `start()`
    # sweep or to the scheduled reaper: two BACKSTOPS, not the primary. The
    # FAILURE path is the one that leaks most (a killed or red run is exactly when
    # nobody runs `down`), which is why the hook is in `finally`.
    #
    # Two rules it must not break:
    #   1. it must never change the run's VERDICT — a removal that fails is
    #      REPORTED and the pytest exit code stands (`stop()` raises `SystemExit`
    #      for the unremovable/deps cases, which would otherwise replace a real
    #      result with a traceback);
    #   2. it must be escapable — `--keep-container` for a post-mortem, because a
    #      red run is exactly when the DB is worth interrogating.
    #   3. it must not take the DB out from under a PEER process. Two `run`s in one
    #      worktree (a lane plus a `task` child) share ONE container, so the one that
    #      exits first would otherwise remove the other's live target and its
    #      connection errors would be reported as test verdicts.
    # A SIGKILL cannot be hooked — that is what the timer is for. Ctrl-C raises
    # `KeyboardInterrupt` and unwinds through `finally`. **SIGTERM does NOT**: the
    # default disposition terminates the process without unwinding, so this hook
    # would never run for a lane stopped the normal way — hence the handler below
    # (the child gets its own, since `exec` resets dispositions).
    # The box-wide opt-out (`TORTOISE_TEST_LANE_REAP=0`) must cover the HOOK as
    # well as `start()`'s sweep: a lane that disabled automatic reaping here
    # would otherwise still lose its container at the end of every `run`
    # (#10813 review P2).
    # Any falsy spelling disables it (`0`/`false`/`no`/`off`): the sweep's own
    # switch only reads a literal "1", so `=false` used to disable the sweep and
    # leave the HOOK armed — the same word with two meanings (#10813 review).
    keep = bool(getattr(args, "keep_container", False)) or not _reap_enabled()
    rc: int | None = None
    _prev_term = None
    try:
        _prev_term = signal.signal(signal.SIGTERM, _raise_on_term)
    except (ValueError, OSError):        # not the main thread / unsupported
        _prev_term = None
    try:
        with _run_marker(name) as _marker_written:
            if not _marker_written:
                # The marker dir is unwritable, so a PEER's presence cannot be
                # established at all — and `_peer_run_is_live` would answer "no
                # peer" for every concurrent run on the box, leaving only the
                # 300 s client probe between a peer's live DB and `docker rm -f`.
                # Unknown is not "none": keep the container and say so.
                keep = True
                print(f"test-lane: run marker could not be written under "
                      f"{REAP_STATE_DIR} — the peer guard cannot see a "
                      f"concurrent run, so {name} is NOT reaped on exit",
                      file=sys.stderr)
            # ⛔ INSIDE the marker (see the comment above `name = lane_name()`):
            # `start()` is the window in which a peer would otherwise be
            # invisible.
            started_name, port = start(args.port)
            if started_name != name:
                raise SystemExit(
                    f"test-lane: refusing to run — this worktree resolved to "
                    f"{name} but start() created/reused {started_name}. Two "
                    f"different containers were named by one run because a "
                    f"`repo_root()` read degraded; the ownership guard would "
                    f"then protect the wrong one. Re-run from the worktree.")
            started = True
            # …and a container this run did not CREATE is never its to remove.
            created = _LAST_START_CREATED
            # ONE call, both consumers: `uri_for()` emits a DOCKER_HOST warning
            # as a side effect, so rendering from a SECOND call printed it twice
            # (review P2).
            seam = lane_seam_env(port, args.graph)
            env = {**os.environ, **seam}
            argv = [sys.executable, "-m", "pytest", *extra]
            # The target line is RENDERED from the seam dict, never re-typed:
            # a hand-written list is what left this line naming three of the
            # four variables after the legacy axis was added (review P2), and
            # the line's whole purpose is being the only record a killed run
            # leaves behind — so it is PINNED by
            # `test_run_names_EVERY_seam_variable_on_the_stderr_target_line`.
            # `''` is the password's real value, so `repr` keeps it visible
            # instead of rendering an empty span.
            target = " ".join(
                f"{key}={value if value else repr(value)}"
                for key, value in seam.items()
            )
            print(
                f"test-lane: {name} on 127.0.0.1:{port} — {target} "
                f"— invoking: {' '.join(argv)}",
                file=sys.stderr,
            )
            sys.stderr.flush()
            rc = subprocess.run(argv, env=env).returncode
    finally:
        if _prev_term is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signal.SIGTERM, _prev_term)
        kept = keep          # set True below on every path that leaves it up
        # A `start()` that never completed created nothing to reap, and a
        # "REUSED it" message there would be a false statement about a run that
        # never got a container at all.
        if not keep and started:
            # The in-use guard, read from the CONTAINER's own statement: a peer
            # (a `task` child shelled into the same worktree) shares this
            # container, and removing it would fail ITS run with connection
            # errors that read as test verdicts. `None` (unreadable) fails closed.
            try:
                # Cheap check FIRST: on the reuse path (the commonest — `uri`
                # then `run`) the probe can cost a 60 s `docker exec` and the
                # answer is unused.
                #
                # `idle_within_s=None`: the hook asks "is ANYTHING attached?",
                # not "did anything attach in the last 5 minutes". A consumer
                # that opened this container through `uri` writes no `run`
                # marker, so when it is idle through a heavy non-DB test file its
                # ONLY signal is the socket it still holds — and the sweep's
                # `ACTIVE_CLIENT_IDLE_S` measure would call that "unused" and
                # `rm -f` its target (#10813 review P2).
                #
                # ⛔ A container docker reports as TERMINAL is not probed: it
                # cannot have a client, and `docker exec` into it fails, so the
                # probe would answer `None` and the hook would KEEP exactly the
                # container the run already lost (an OOM kill — the population
                # #7357 documents — or a crash). The sweep states the same rule
                # one function up ("a TERMINAL one cannot have one, so the probe
                # is skipped"); this mirrors it (#10813 review P2-3).
                in_use = (None if not created
                          else False if container_state(name) in _TERMINAL_STATES
                          else _container_in_use(name, idle_within_s=None))
                if not created:
                    kept = True
                    print(f"test-lane: NOT reaping {name} on exit — this run "
                          f"REUSED it (another consumer, e.g. the `uri` route, "
                          f"may be using it); the scheduled reaper decides",
                          file=sys.stderr)
                elif in_use is None:
                    kept = True
                    print(f"test-lane: NOT reaping {name} on exit — its client "
                          f"list could not be read (fail-closed; the scheduled "
                          f"reaper will decide)", file=sys.stderr)
                elif in_use:
                    kept = True
                    print(f"test-lane: NOT reaping {name} on exit — a live "
                          f"client is using it", file=sys.stderr)
                elif _peer_run_is_live(name) is not False:
                    kept = True
                    print(f"test-lane: NOT reaping {name} on exit — another "
                          f"`run` holds this worktree", file=sys.stderr)
                else:
                    print(f"test-lane: reaping {name} on exit -> {stop()}",
                          file=sys.stderr)
            except (SystemExit, Exception) as _exc:
                # The hook never speaks for the run. `SystemExit` covers
                # `stop()`'s refusals; `Exception` covers an OSError from a
                # removed cwd or a closed stderr — both would otherwise REPLACE a
                # real pytest verdict with a traceback (#10813 review P2).
                # `KeyboardInterrupt` is deliberately NOT caught: it propagates,
                # and the `finally` has already done its work.
                kept = True
                print(f"test-lane: could not reap {name} on exit ({_exc}); "
                      f"the scheduled reaper (tools/container_reaper.py) will "
                      f"collect it", file=sys.stderr)
        # `started` too: a run whose `start()` raised never had a container, and
        # re-stamping a lease for one that does not exist writes into the REAL
        # registry for no reason.
        if kept and started:
            # ⛔ The container SURVIVED this run, so its lease must be re-stamped
            # (the #10693 F1 rule: the epoch is the END of the last use). Putting
            # the stamp only on the `--keep-container` branch left every
            # not-reaped path (peer in use, unreadable client list, failed
            # removal) with a lease measured from the START of the run — already
            # past OWNER_GRACE_H after a full-suite step, so the next sweep could
            # reap a container this one deliberately spared (#10806 review).
            _touch_lease(name)
    # There is deliberately NO `rc is None` branch: `subprocess.run` returning
    # always sets it, and if it RAISED (Ctrl-C), the `finally` above has already
    # run and the exception propagates past this line. A branch here would be
    # unreachable code asserting a path that does not exist (#10806 review).
    if rc < 0:
        print(
            f"test-lane: pytest was KILLED by signal {-rc} (no pytest count "
            f"line exists for a killed run — this is NOT a test failure)",
            file=sys.stderr,
        )
        return 128 + (-rc)
    return rc


def cmd_down(args: argparse.Namespace) -> int:
    # ONE `repo_root()` read, inside `stop()`. Printing `lane_name()` here as
    # well was a second read, and `repo_root()` degrades PER CALL to `Path.cwd()`
    # on a git timeout — so the name printed could disagree with the container
    # actually removed. `stop()` takes no argument by design (naming a target is
    # how one lane would delete a peer's DB), so the fix is to print what it
    # ACTED ON, not a separately-derived name.
    print(f"test-lane: down -> {stop()}", file=sys.stderr)
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
            # #8180 actuator: `run` is where a lane ENDS, so its container is
            # reaped on the way out — on the failure path too. This flag is the
            # escape hatch for a post-mortem, never the default: a container left
            # up "in case" is the leak this closes.
            sp.add_argument("--keep-container", action="store_true",
                            help="leave this lane's container running after the "
                                 "run (default: reap it on exit, on the failure "
                                 "path too). Use it to interrogate a red run's "
                                 "DB, then `down`.")
            # REMAINDER, not a named option: everything after the separator is
            # pytest's own argv and must reach it verbatim (`-q`, `-k expr`,
            # `--timeout=900`, a path…). argparse would otherwise consume the
            # pytest flags it recognises and reject those it does not.
            sp.add_argument("pytest_args", nargs=argparse.REMAINDER,
                            help="pytest argv, after `--` "
                                 "(default target: tests/ — the suite). A "
                                 "LEADING flag needs the `--` separator "
                                 "(`run -- -q tests/`): without it argparse "
                                 "treats it as the tool's own option")
        sp.set_defaults(func=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except SystemExit as exc:
        # `_raise_on_term` raises `SystemExit(128 + SIGTERM)` so that `cmd_run`'s
        # `finally` can run — that exception must become this process's EXIT CODE,
        # not a traceback. A `SystemExit` carrying a STRING is a refusal message
        # (`stop()`, `start()`) and keeps its normal behaviour.
        if isinstance(exc.code, int):
            return exc.code
        raise


if __name__ == "__main__":
    raise SystemExit(main())
