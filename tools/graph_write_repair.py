#!/usr/bin/env python3
"""Detect and repair the un-writable `tortoise` graph condition.

THE FAILURE THIS GUARDS AGAINST (measured, reproducible)
--------------------------------------------------------
FalkorDB 6.0.1, `falkordb-16379`, the shared `tortoise` graph:

* READS succeed -- every query, all nodes, consistently.
* EVERY WRITE is refused with:
      ResponseError: graph was deleted or replaced while the query was running, aborting
  from the SDK, from raw Cypher, and from `redis-cli` inside the container -- so it is
  not a client artefact.
* It survives a container restart, so the fault is in what the RDB reload produces. It is
  NOT a transient during load: measured still-refused 155 s after `loading:0` with the
  container stable.
* It is SPECIFIC TO THIS GRAPH. Measured 2026-10-13 across one restart: `tortoise`,
  `org_tortoise`, `registry_tortoise`, `test_test_9ac6a1580cd5` and `org_team_s1_aed9f79d`
  were ALL writable before it, and after it the other four were writable and ONLY
  `tortoise` was broken.
* Not eviction (`maxmemory=0`, `noeviction`, 386 MB used of no limit) and not a concurrent
  deleter (a `MONITOR` capture taken during a refused write showed NO `GRAPH.DELETE` of
  `tortoise`; the only deletes were unrelated test-graph sweeps).

Every container restart therefore takes the whole fleet's graph writes down, and the lanes
that would notice are the lanes that are blocked.

THE REPAIR: COPY -> VERIFY -> RENAME. NEVER DELETE.
---------------------------------------------------
A freshly `GRAPH.COPY`-ed graph IS complete and writable (verified: 20300 nodes, matching
point-kind counts, spot-checked ids present, first write accepted).

The swap is a single atomic `RENAME`, which OVERWRITES in one step. That is the fix for
the failure that destroyed this graph once already: `GRAPH.DELETE` + copy-back leaves a
window in which a Cypher read on the missing graph AUTO-CREATES an empty one (so the
copy-back then fails with `destination key already exists`), and the graph is left EMPTY.
`RENAME` has no such window, so this module contains no `GRAPH.DELETE` of the live graph at
all. The run-unique scratch clone is the ONLY key this module deletes, and it is dropped with
a raw `GRAPH.DELETE` -- the same form `hosted_backup`'s restore cleanup uses for its own
`GRAPH.COPY` staging copies. `graph_delete_guard.safe_graph_delete` is deliberately NOT used
for it: that primitive re-acquires the non-reentrant delete lock this run already holds on
its failure paths, cannot acquire it, and refuses after its full wait (the clone then leaks
and the run stalls while the caller believes it cleaned up). Presence is always established
with `graph_exists` -- never by querying a graph whose existence is assumed.

THE ROOT CAUSE, PINNED: A SAME-NAMED GRAPH IN ANOTHER LOGICAL DB (upstream #2782)
--------------------------------------------------------------------------------
The defect is NOT an interrupted RESTORE, NOT the rebuild's wipe, and NOT memory. It is an
UPSTREAM FalkorDB Rust-core defect, and it needs no corruption to trigger:

    FalkorDB/FalkorDB#2782 -- "[Rust] Same graph name in two Redis databases ...
    permanently write-locks the first graph (release)" (OPEN, unfixed by the maintainers)

The Rust core's `GRAPH_REGISTRY` is keyed by graph NAME ONLY, with no Redis logical-database
qualifier (`src/graph_core.rs`). Registering the name in a second DB therefore DISPLACES the
first graph from the registry, `graph_is_registered` then returns false for it, and it is
write-locked until its key is replaced. The engine reports that as the defect sentence above
-- which is why the message misdescribes it: nothing was deleted, no race occurred.

REPRODUCED HERE, 2026-10-13, on this container (`falkordb/falkordb:6.0.1`, `MODULE LIST`
ver 60001) with a throwaway name -- never on `tortoise`:

    SELECT 0; GRAPH.QUERY zz_probe "CREATE (n:T)"          -> Nodes created: 1
    SELECT 1; GRAPH.QUERY zz_probe "CREATE (n:T)"          -> Nodes created: 1
    SELECT 0; GRAPH.QUERY zz_probe "CREATE (n:T)"          -> graph was deleted or replaced... aborting
    SELECT 0; GRAPH.QUERY zz_probe "MATCH (n) RETURN count(n)" -> 1   (reads still fine)
    SELECT 1; DEL zz_probe ; SELECT 0; GRAPH.QUERY zz_probe "CREATE..." -> STILL aborts

That last line matches the upstream report exactly: deleting the OTHER database's key does
NOT heal the displaced graph. Only REPLACING the displaced key does -- which is precisely
why `GRAPH.COPY` + `RENAME` (and the older COPY/DELETE/COPY) is the one thing that works.

**This is why the defect recurs on every container restart, and it is measurable on this
host.** The RDB holds the same graph name in MORE THAN ONE keyspace, so every reload
re-registers the name repeatedly and whichever registration lands last wins:

    INFO keyspace -> db0:keys=632, db1..db9:keys=1 each   (at measurement)
    db0 tortoise = 20307 nodes (the real graph)   db1..db9 tortoise = 0 nodes (empty)

`cross_db_duplicates()` below reports exactly this, and it is the durable half: with the
colliding keys gone the reload can only register the real one. This check uses
`INFO`/`EXISTS`/`TYPE` ONLY -- a Cypher read (`GRAPH.QUERY`/`GRAPH.RO_QUERY`) against the
name in another DB OPENS that graph and is itself the displacement, so a probe that read
the duplicate's node count would brick the live graph on the spot.

REPAIR ONLY ON THE SPECIFIC DEFECT
----------------------------------
The probe classifies the failure, and only the exact defect authorises a repair. This is
not defensiveness for its own sake: an earlier version caught bare `Exception`, so a
`BusyLoadingError` (the server still loading its RDB) was classified as "the graph is
broken" and it ran the destructive swap against a graph that was merely not ready. That is
the incident this file exists to make unrepeatable. `BusyLoadingError` and connection
failures are UNKNOWN -- exit 2 -- never "broken".

KNOWN RESIDUALS (declared, not hidden)
--------------------------------------
* **The probe mutates.** Verifying that a graph accepts writes requires attempting a write,
  so `--check` writes one node and removes it. An earlier revision claimed "never mutate"
  and did; the claim is dropped rather than the behaviour dressed up.
* **The defect is not fixed upstream.** It recurs on every container restart until the
  colliding keys are gone (see the root-cause section). The `RENAME` repair buys the fleet
  working writes; removing the duplicate keys is what makes it durable, and
  `cross_db_duplicates()` now names both instead of leaving a bare "recurs" note.
* **The root cause is pinned; the FIX is not ours.** Upstream FalkorDB/FalkorDB#2782 keys
  `GRAPH_REGISTRY` by name only. Nothing in this repo can repair the registry; until the
  maintainers key it by `(db_index, name)`, the collision must not be re-created.
* **The duplicate keys are not removed for you.** Deleting a key in ANOTHER logical DB is a
  global, irreversible operator action (the repo's `poisoned_aof_recovery_hint` sets the same
  boundary), and a duplicate that holds data is not ours to destroy sight-unseen. The tool
  prints the exact command and refuses to run it.

Usage:
    uv run python tools/graph_write_repair.py            # check, repair if broken
    uv run python tools/graph_write_repair.py --check    # report only; never repairs
    uv run python tools/graph_write_repair.py --graph X  # a different graph

Exit codes: 0 healthy or repaired | 2 cannot connect or state UNKNOWN | 3 broken/absent NOT repaired
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/graph_write_repair.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/graph_write_repair.py`"
    )

import argparse
import contextlib
import os
import uuid

DEFAULT_GRAPH = "tortoise"
PROBE_LABEL = "GraphWriteRepairProbe"

# The tool is local-only by construction (`connect()` pins it, #6666): every socket this
# module opens -- the graph handle AND the per-DB collision scan -- must agree on the host.
HOST = "127.0.0.1"

#: The upstream defect that CAUSES this write-lock (see the module docstring): the Rust
#: core's `GRAPH_REGISTRY` is keyed by graph name only, with no logical-DB qualifier, so a
#: same-named graph in another DB displaces this one. OPEN and unfixed.
COLLISION_UPSTREAM = "FalkorDB/FalkorDB#2782"

# A swap below this many nodes is refused outright. The graph is the fleet's collective
# memory (~20300 nodes); an EMPTIED graph must never be "repaired" into place. This is what
# makes the `0 < 0` false-success impossible even if a presence check is somehow missed.
MIN_NODES = 1000

#: Bound on the per-DB scan. The server's `databases` setting is 16 by default; the scan
#: iterates the DBs `INFO keyspace` actually names, so this is a runaway guard, not a
#: functional limit.
_MAX_DBS = 64


def _raw_client(port: int, db_index: int):
    """A client pinned to ONE logical DB, on a connection it HOLDS.

    ⛔ ONE LEDGER PER DB, NOT ONE `SELECT` COMMAND. `falkordb`'s `execute_command` checks a
    pooled connection out per call, so `SELECT 1` and the `EXISTS` that follows it would run
    on DIFFERENT connections and the `EXISTS` would silently answer for db0 -- reporting "no
    collision" on the very host that has one. A client constructed with `db=` owns its DB.
    """
    import redis

    return redis.Redis(host=HOST, port=port, db=db_index,
                       socket_connect_timeout=3, socket_timeout=3)


def cross_db_duplicates(port: int, name: str, *, home_db: int = 0,
                        ) -> list[tuple[int, bytes | str]] | None:
    """Same-named keys in OTHER logical DBs -- the upstream cause of this write-lock.

    Returns ``(db_index, key_type)`` for every DB (other than *home_db*) whose `INFO
    keyspace` shows keys AND that holds a key called *name*, or ``None`` when the scan could
    not be performed at all (unreachable server, unreadable directory). ``None`` is NOT "no
    collision": the caller must say the check did not run rather than report clean.

    ⛔ SIDE-EFFECT FREE BY CONSTRUCTION -- `INFO`/`EXISTS`/`TYPE` ONLY. A Cypher read against
    *name* in another DB (`GRAPH.QUERY`/`GRAPH.RO_QUERY`) OPENS that graph, which IS the
    registry displacement this function exists to find: a scan that read the duplicate's node
    count would brick the live graph it was diagnosing. That is why no node count is
    reported here -- the count is exactly the thing that cannot be read safely.
    """
    try:
        home = _raw_client(port, home_db)
        keyspace = home.execute_command("INFO", "keyspace")
        # redis-py parses `INFO <section>` into a dict (`{'db0': {'keys': 632, ...}}`); a
        # transport that does not strips it to text -- handle both rather than silently
        # reporting "no collision" for every DB.
        if isinstance(keyspace, dict):
            db_names = list(keyspace)
        else:
            text = keyspace.decode() if isinstance(keyspace, bytes) else str(keyspace)
            db_names = [line.split(":", 1)[0] for line in text.splitlines()
                        if line.startswith("db")]
    except Exception:
        return None

    duplicates: list[tuple[int, bytes | str]] = []
    try:
        for db_name in db_names[:_MAX_DBS]:
            try:
                index = int(str(db_name).removeprefix("db"))
            except ValueError:
                continue
            if index == home_db:
                continue
            try:
                client = _raw_client(port, index)
                if client.execute_command("EXISTS", name):
                    duplicates.append((index, client.execute_command("TYPE", name)))
            except Exception:
                # A single unreadable DB does not invalidate the others: report what was
                # found and let the caller's wording stay a report, not a guarantee.
                continue
    except Exception:
        return None
    return duplicates


def collision_message(name: str, duplicates: list[tuple[int, bytes | str]]) -> str:
    """The loud, actionable report for a same-name collision (empty string if none).

    Pure, so the wording -- the artifact an operator actually reads -- is pinned by a test.
    """
    if not duplicates:
        return ""
    dbs = ", ".join(
        f"db{index} (type={kind.decode() if isinstance(kind, bytes) else kind})"
        for index, kind in duplicates)
    return (
        f"\n⚠  SAME-NAME GRAPH IN OTHER LOGICAL DBs: {name!r} also exists in {dbs}.\n"
        f"   This is the CAUSE of the write-lock, not a symptom: {COLLISION_UPSTREAM} --\n"
        f"   the Rust core keys GRAPH_REGISTRY by graph NAME only, so the other DB's\n"
        f"   registration displaces this graph. Each container restart re-registers the\n"
        f"   name repeatedly, which is why the defect keeps coming back.\n"
        f"   DURABLE CLEAR (operator action; this tool will not delete in another DB):\n"
        f"       # after the live graph has been replaced (below) or a repair has landed,\n"
        f"       for db in {' '.join(str(i) for i, _ in duplicates)}; do\n"
        f"           redis-cli -p <port> -n $db DEL {name}      # ONLY if that key is a\n"
        f"       done                                            # leftover, never real data\n"
        f"       redis-cli -p <port> SAVE                       # persist the clean keyspace\n"
        f"   Verify a duplicate holds nothing BEFORE deleting it: a Cypher read against it\n"
        f"   opens it, so check it on a throwaway copy or accept that removing a NON-empty\n"
        f"   duplicate destroys that data.\n"
    )


class Unreachable(RuntimeError):
    """Cannot determine the graph's state -- never a licence to repair."""


class Defect(RuntimeError):
    """The specific, measured reload defect -- the only condition that repairs."""


def connect(port: int):
    import falkordb

    # Pin the loopback address, never `localhost` (#6666) -- the same HOST the per-DB scan
    # dials, so a repair and its diagnosis cannot disagree about which server they mean.
    return falkordb.FalkorDB(host=HOST, port=port)


def node_count(graph) -> int:
    return int(graph.query("MATCH (n) RETURN count(n)").result_set[0][0])


def edge_count(graph) -> int:
    """Relied on by the copy gate. A node count alone cannot see relationship loss -- the
    live graph carries ~25967 edges, and `RENAME` is irreversible, so a clone that lost every
    edge while keeping every node would previously have been swapped in."""
    return int(graph.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0])


def fingerprint(graph) -> tuple[int, int]:
    return (node_count(graph), edge_count(graph))


def counts(msg: str) -> None:
    """Print a COUNT unconditionally -- ``--quiet`` suppresses NARRATION, not numbers.

    The scheduled job is armed with ``--quiet`` (see the installer's ``ProgramArguments``),
    so its log is the only artifact an unattended repair produces. `say()` is gated by
    ``verbose``, and routing the counts through it is what made the ``--quiet`` help text
    false: the log showed the post-repair count with no pre-repair baseline to compare it
    against, so it could not answer the one question it exists for -- "did the repair
    preserve the data?". Counts therefore bypass `say()`. Pinned by
    tests/test_graph_write_repair.py::test_quiet_repair_still_logs_before_and_after_counts.
    """
    print(msg, flush=True)


def is_defect(db, name: str, exc: BaseException) -> bool:
    """Is this exception the reload defect -- via the REPO'S classifier, not a new one.

    ⛔ DO NOT hand-roll a substring test for this sentence. `tortoise/retry.py` already
    measured and rejected that: its graph-abort pattern anchors the clause (and requires
    the trailing `, aborting`), specifically because a MID-MESSAGE occurrence must not
    match -- an unanchored `in` test (this module's first version) matched the sentence
    inside `could not parse query: <sentence>`.

    ⛔ AND DO NOT use `retryable_aborted_write` here. That is the SDK's RETRY predicate, and
    it returns True for its STATE-INDEPENDENT arms as well -- the engine's write-slot refusal
    (`another write is in progress, retry the query` / `Write query aborted: ...`) and
    `MISCONF`/`Can't persist` -- WITHOUT consulting the graph at all. A repair gate built on
    it classifies a HEALTHY graph that merely lost one write race as the reload defect and
    swaps it for a stale COPY, silently discarding every fleet write that landed between the
    COPY and the RENAME; on `MISCONF` it additionally issues a whole-graph write against a
    server that is refusing to persist. That is the 2026-10-13 incident class (a non-defect
    failure authorising the destructive swap). The narrow question here is "is this the
    GRAPH-ABORT family?", which is exactly `graph_abort_family`, gated on presence.
    """
    from tortoise.graph_delete_guard import graph_exists
    from tortoise.retry import graph_abort_family

    try:
        if not graph_abort_family(exc):
            return False
        return bool(graph_exists(db, name))
    except Exception:
        return False


def probe(db, name: str) -> tuple[str, str]:
    """Return ("ok"|"defect"|"unknown"|"absent", detail). Never raises.

    Establishes PRESENCE FIRST, because a Cypher read on a missing graph auto-creates an
    empty one -- and then reports a healthy zero-node graph. Presence is RE-CHECKED after the
    write, because the graph can be deleted between the two, in which case the write
    auto-creates an empty graph whose write is ACCEPTED and would read as healthy.
    """
    from tortoise.graph_delete_guard import graph_exists

    try:
        if not graph_exists(db, name):
            return "absent", f"graph {name!r} does not exist"
        graph = db.select_graph(name)
        before = node_count(graph)
    except Exception as exc:
        return "unknown", f"presence/count read failed: {type(exc).__name__}: {exc}"

    # Scope the probe to THIS run: a bare `MATCH (t:Label) DELETE t` would destroy a node
    # left by an earlier failed cleanup, or any pre-existing node carrying the label.
    probe_id = f"probe-{uuid.uuid4().hex}"
    try:
        graph.query(f"CREATE (t:{PROBE_LABEL} {{id:$pid}})", {"pid": probe_id})
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if is_defect(db, name, exc):
            return "defect", detail
        return "unknown", f"unclassified failure (refusing to repair): {detail}"

    # The write was ACCEPTED. Only now is it meaningful to look further.
    try:
        after = node_count(graph)
    except Exception as exc:
        return "unknown", f"write accepted but the count read failed ({exc})"
    if not graph_exists(db, name):
        return "unknown", ("the write was accepted but the graph is now ABSENT -- it was "
                           "deleted during the probe and the write auto-created an empty one")
    if after != before + 1:
        return "unknown", (f"the write was accepted but the count moved {before} -> {after} "
                           f"(expected +1) -- the graph is not behaving like a healthy one")
    try:
        graph.query(f"MATCH (t:{PROBE_LABEL} {{id:$pid}}) DELETE t", {"pid": probe_id})
    except Exception as exc:
        return "unknown", (f"the probe write was ACCEPTED but cleanup failed ({exc}) -- the "
                           f"graph writes; leaving one {PROBE_LABEL} node behind")
    return "ok", "writes accepted"


def repair(db, name: str, verbose: bool = True) -> tuple[bool, str]:
    """COPY -> verify clone -> atomic RENAME. No GRAPH.DELETE of the live graph, ever."""
    from tortoise.graph_delete_guard import graph_delete_lock, graph_exists
    from tortoise.hosted_backup import _audit_copied_boolean_indexes

    def say(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    # A per-run scratch name: a FIXED name lets two runs (the timer and a hand-run) delete
    # each other's clone.
    scratch = f"__gwr_{uuid.uuid4().hex[:12]}"
    clone_db = db.select_graph(scratch)

    def drop_clone(reason: str) -> None:
        # The scratch clone is the ONLY key this module deletes, it is run-unique (never the
        # live graph), and this process's own `GRAPH.COPY` created it -- so it is dropped with
        # a raw `GRAPH.DELETE` (`clone_db.delete()`), the same form `hosted_backup`'s restore
        # cleanup uses for its own staging copies. `safe_graph_delete` is deliberately NOT
        # used: it re-acquires the non-reentrant delete lock this run already holds on the
        # failure paths, cannot acquire it, and refuses after its full wait -- the clone
        # leaked and the run stalled while the caller believed it had cleaned up (measured by
        # review: `inner safe_graph_delete returned=False`). A leaked clone is not worth
        # failing the run over.
        with contextlib.suppress(Exception):
            clone_db.delete()
        say(f"  note: dropped clone {scratch} ({reason})")

    try:
        with graph_delete_lock(db) as acquired:
            if not acquired:
                return False, "could not take the graph-delete lock (another repair is running)"

            # ⛔ PRESENCE INSIDE THE LOCK, IMMEDIATELY BEFORE ANY READ. A Cypher read on a
            # missing graph AUTO-CREATES an empty one, so this read without the guard is how
            # `before=0` -> clone 0 -> RENAME -> `after=0` -> `0 < 0` is False -> exit 0
            # "repaired: 0 nodes": a FALSE SUCCESS on a destroyed graph.
            if not graph_exists(db, name):
                return False, (f"graph {name!r} is ABSENT inside the repair -- refusing to "
                               f"create or repair it")

            before_nodes, before_edges = fingerprint(db.select_graph(name))
            counts(f"  fingerprint before repair: {before_nodes} nodes / {before_edges} edges")
            if before_nodes < MIN_NODES:
                return False, (f"refusing to repair a graph of {before_nodes} nodes "
                               f"(< MIN_NODES={MIN_NODES}) -- this is not the shared graph, "
                               f"and 'repairing' it would put an empty one in its place")

            say(f"  [1/4] GRAPH.COPY {name} -> {scratch}")
            try:
                db.connection.execute_command("GRAPH.COPY", name, scratch)
            except Exception as exc:
                return False, f"GRAPH.COPY failed: {exc}"

            clone_fp = fingerprint(clone_db)
            say(f"  [2/4] clone fingerprint: {clone_fp[0]} nodes / {clone_fp[1]} edges")
            if clone_fp != (before_nodes, before_edges):
                # A count cannot catch property/label/type loss, but node AND edge equality is
                # the strongest check available cheaply, and RENAME is irreversible.
                drop_clone("incomplete copy")
                return False, (f"clone {clone_fp} != source {(before_nodes, before_edges)} "
                               f"- refusing to swap")

            kind, detail = probe(db, scratch)
            if kind != "ok":
                # RETAIN the clone: it may be the only copy. Never clean up a failed repair.
                return False, (f"the clone is not usable ({kind}: {detail}) - refusing to "
                               f"swap; clone {scratch} retained")

            # #3154: GRAPH.COPY is the CORRUPTING step for a boolean `is_operator` index -- it
            # can copy the index WITHOUT its `false` posting entries, after which
            # `n.is_operator = false` matches ZERO rows while the data stays intact. Every
            # other copy-then-swap site in the repo audits for it (hosted restore/import); this
            # is the third GRAPH.COPY site and it must not be the one that skips the audit.
            # The shared primitive drops any boolean index it finds and RAISES if one survives
            # -- a survivor means we must not swap in a graph whose `= false` predicate is
            # silently dead.
            try:
                _audit_copied_boolean_indexes(
                    clone_db, graph_name=scratch, stage="graph-write-repair clone")
            except RuntimeError as exc:
                drop_clone("clone carries an unrepairable boolean index (#3154)")
                return False, (f"the clone's #3154 boolean-index audit failed ({exc}) - "
                               f"refusing to swap")

            say("  [3/4] clone is complete (nodes AND edges), writable, and index-audited")

            # Re-read immediately before the irreversible step: the section may have outlived
            # the lock's TTL, and a second run's stale clone must not overwrite a graph the
            # fleet has written to since.
            now_fp = fingerprint(db.select_graph(name))
            # ANY change is staleness, not just a node LOSS: a concurrent ADDITION passes a
            # `< before_nodes` test, the older clone is then swapped in, and the write that
            # landed between COPY and RENAME is silently discarded -- the post-swap
            # `after >= before` check cannot see it.
            if now_fp != (before_nodes, before_edges) or not graph_exists(db, name):
                drop_clone("source changed under us")
                return False, (f"the source changed during the repair ({now_fp} vs "
                               f"{(before_nodes, before_edges)}) - refusing to swap over it")

            # ATOMIC. `RENAME` overwrites in one step, so there is never a window in which
            # `name` is absent -- that window is what auto-created an empty graph and
            # destroyed this graph on 2026-10-13.
            say(f"  [4/4] RENAME {scratch} -> {name}   (atomic; no delete window)")
            try:
                db.connection.execute_command("RENAME", scratch, name)
            except Exception as exc:
                present = graph_exists(db, name)
                return False, (f"RENAME failed: {exc}; source present={present}, "
                               f"clone {scratch} retained as the fallback")
    except Exception as exc:
        return False, f"repair aborted: {type(exc).__name__}: {exc}"

    # ⛔ NO COPY IS RETAINED AFTER A SUCCESSFUL SWAP: `RENAME` MOVES the clone, it does not
    # copy it. If the source still exists the swap did not happen, and that IS a failure.
    if graph_exists(db, scratch):
        return False, (f"the scratch graph {scratch} still exists after RENAME -- the swap "
                       f"did not complete; NOT reporting success")

    try:
        after = node_count(db.select_graph(name))
    except Exception as exc:
        return False, (f"the repair ran but the post-repair count could not be read ({exc}) "
                       f"- the outcome is NOT verified")
    counts(f"  node count after repair: {after}")
    # Only a LOSS is a failure. A gain is a concurrent fleet write landing on a now-healthy
    # graph, which is the outcome we wanted.
    if after < before_nodes:
        return False, f"post-repair count {after} < pre-repair {before_nodes} - DATA LOSS"
    if after < MIN_NODES:
        return False, f"post-repair count {after} < MIN_NODES={MIN_NODES} - refusing to call this repaired"

    kind, detail = probe(db, name)
    if kind != "ok":
        return False, f"still not writable after repair ({kind}: {detail})"
    return True, f"repaired: {after} nodes, writes restored"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", default=DEFAULT_GRAPH)
    ap.add_argument("--port", type=int, default=int(os.environ.get("FALKORDB_PORT", "16379")))
    ap.add_argument("--check", action="store_true", help="report only; never repairs")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress step narration (the verdict and every count still print)")
    args = ap.parse_args()

    def say(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    try:
        db = connect(args.port)
        from tortoise.graph_delete_guard import graph_exists
        if not graph_exists(db, args.graph):
            print(f"⛔ graph '{args.graph}' is ABSENT on {HOST}:{args.port} -- "
                  f"this is not a repair case (the defect leaves the key present and "
                  f"readable). Do not create it here: investigate why it is gone.",
                  file=sys.stderr)
            return 3
    except Exception as exc:
        print(f"ERROR: cannot reach FalkorDB on {HOST}:{args.port}: {exc}", file=sys.stderr)
        return 2

    # ⛔ THE CAUSE IS CHECKED BEFORE THE SYMPTOM, AND REPORTED ON EVERY PATH -- including
    # HEALTHY. A healthy graph that still holds a same-name key in another DB is one restart
    # (or one Cypher read in that DB) away from the write-lock, because the collision IS the
    # displacement mechanism (upstream #2782), and it is what makes the repair non-durable.
    # Side-effect free: `INFO`/`EXISTS`/`TYPE` only (see `cross_db_duplicates`).
    duplicates = cross_db_duplicates(args.port, args.graph)
    collision = "" if duplicates is None else collision_message(args.graph, duplicates)
    if duplicates is None:
        # ⛔ OUTSIDE THE `--quiet` GATE, like the collision and the counts. "The scan did
        # not run" is a FACT about the graph, not step narration: under the scheduled job's
        # `--quiet` an unrun scan and a clean scan would otherwise produce byte-identical
        # logs, and `None` is expressly NOT "no collision" (see `cross_db_duplicates`).
        # The same claim/narration line the counts() docstring draws, one item over.
        print(f"note: could not scan other logical DBs for the {COLLISION_UPSTREAM} "
              f"collision - the collision check did NOT run", file=sys.stderr)
    if collision:
        print(collision, file=sys.stderr)

    say(f"graph '{args.graph}': probing the write path...")
    try:
        kind, detail = probe(db, args.graph)
    except Exception as exc:
        print(f"ERROR: the probe could not complete: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    if kind == "ok":
        # ⛔ PRINT THE COUNTS ON THE HEALTHY PATH TOO. A bare "HEALTHY" cannot answer the one
        # question the log exists for -- "did the repair preserve the data?" -- and a
        # writable-but-emptied graph would read HEALTHY with nothing to notice. This is also
        # what makes the `--quiet` help text true: counts print regardless.
        try:
            nodes, edges = fingerprint(db.select_graph(args.graph))
            print(f"graph '{args.graph}': HEALTHY - {detail} "
                  f"({nodes} nodes / {edges} edges)")
            if collision:
                print(f"   the graph writes, but the {COLLISION_UPSTREAM} collision above "
                      f"means the defect WILL return; clear the duplicate keys.",
                      file=sys.stderr)
        except Exception as exc:
            print(f"graph '{args.graph}': HEALTHY - {detail} (counts unreadable: {exc})")
        return 0

    if kind == "absent":
        # UNREACHABLE in the ordinary case: main established presence above and returned 3
        # there. Handled EXPLICITLY because the graph can be deleted in the window between
        # the two reads -- folding it into the "unknown -> 2" arm made a SECOND absent path
        # whose exit code disagreed with the presence check's.
        print(f"⛔ graph '{args.graph}' is ABSENT on re-read ({detail}) -- it was deleted "
              f"during the probe; not a repair case.", file=sys.stderr)
        return 3

    if kind == "unknown":
        print(f"⚠  graph '{args.graph}': state UNKNOWN - {detail}\n"
              f"   Not repairing. Only the specific reload defect authorises a repair; "
              f"anything else is an unestablished read.", file=sys.stderr)
        return 2

    print(f"⚠  graph '{args.graph}' is UNWRITABLE: {detail}\n"
          f"   This is the #7762 write-lock: READS work and the fleet cannot write. Its "
          f"cause is a same-name key in another logical DB ({COLLISION_UPSTREAM}), reported "
          f"above when present (see the module docstring).", file=sys.stderr)
    if args.check:
        print("   --check given: not repairing.", file=sys.stderr)
        return 3

    say(f"graph '{args.graph}': repairing...")
    try:
        ok, msg = repair(db, args.graph, verbose=not args.quiet)
    except Exception as exc:
        print(f"⛔ REPAIR RAISED for graph '{args.graph}': "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    if ok:
        print(f"graph '{args.graph}': REPAIRED - {msg}")
        if collision:
            print(
                f"⚠  TEMPORARY: the {COLLISION_UPSTREAM} collision above will re-break this "
                f"graph on the next container restart (the RDB re-registers the name in "
                f"every DB it holds). Clear the duplicate keys to make the repair durable.",
                file=sys.stderr)
        else:
            print("note: this recurs on the next container restart (upstream, RDB load path).")
        return 0

    print(f"⛔ REPAIR FAILED for graph '{args.graph}': {msg}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
