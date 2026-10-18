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
`RENAME` has no such window, so this module contains no `GRAPH.DELETE` at all -- neither of
the live graph nor of any clone. Every failed repair is dirty, and a failed repair is never
cleaned up: the clone is parked at a DETERMINISTIC quarantine key (`__gwr_q_<graph>`, one per
source, REPLACED in one atomic `RENAME` rather than accumulated) so a standing failure cannot
leak one full-graph copy per scheduled run -- and a clone strictly smaller than the copy
already retained never replaces it (`retain_clone`). `graph_delete_guard.safe_graph_delete` is
deliberately NOT used because the module never deletes anything -- and a delete could not be
justified anyway: the clone may be the ONLY copy of data the source no longer has. Presence
is always established with `graph_exists` -- never by querying a graph whose existence is
assumed.

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
* **The RDB-load cause is not pinned.** `GRAPH.COPY` producing a good graph localises it to
  the reload path, but not further.
* **A retained clone is a full second copy of the graph until a human clears it.** Bounded
  by design to ONE per source (the deterministic `__gwr_q_<graph>` key, replaced on the next
  failure), but it is still a full copy on a `maxmemory=0` container for as long as it is
  retained. Clearing it (`GRAPH.DELETE __gwr_q_<graph>` once its data is confirmed redundant)
  is an operator decision, never this tool's.
  * **The one-per-source bound has a deliberate exception.** When this run's clone is
    strictly smaller on BOTH counts than the copy already retained, the retained copy is
    KEPT and the smaller clone is left at its run-unique `__gwr_<uuid>` name instead of
    replacing it (`retain_clone`). A count is not containment, so neither copy is deleted;
    in that case a source can briefly hold two `__gwr_*` keys, both operator-cleared.
* **The pre-RENAME count gate is equality, not identity.** A concurrent churn that nets to
  zero on BOTH counts is invisible to it, so the swap is licensed only while the source is
  still REFUSING writes (the source is re-probed immediately before the swap). A net-zero
  churn can therefore only be discarded while the source is unwritable -- the very state in
  which the engine refuses every write, so no such churn can land.

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
import os
import uuid

DEFAULT_GRAPH = "tortoise"
PROBE_LABEL = "GraphWriteRepairProbe"

#: A failed repair parks its clone here -- one key per source graph, REPLACED each failure by
#: an atomic `RENAME`, so retention is bounded by design (see `retain_clone`).
QUARANTINE_PREFIX = "__gwr_q_"

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

    from tortoise.projection import _bounded_server_socket_kwargs

    # Pin the loopback address, never `localhost` (#6666) -- the same HOST the per-DB scan
    # dials, so a repair and its diagnosis cannot disagree about which server they mean.
    #
    # #8118: bounded on the product's ONE resolver. This client was the file's outlier —
    # `_raw_client` above already carried 3s/3s — and `falkordb.FalkorDB` defaults
    # `socket_timeout=None`, which it hands to redis-py explicitly. A repair run against the
    # very wedged server it exists to repair would otherwise park in `recv()` forever at 0%
    # CPU with no error: alive, and indistinguishable from slow work.
    return falkordb.FalkorDB(host=HOST, port=port,
                             **_bounded_server_socket_kwargs())


def node_count(graph) -> int:
    return int(graph.query("MATCH (n) RETURN count(n)").result_set[0][0])


def edge_count(graph) -> int:
    """Relied on by the copy gate. A node count alone cannot see relationship loss -- the
    live graph carries ~25967 edges, and `RENAME` is irreversible, so a clone that lost every
    edge while keeping every node would previously have been swapped in."""
    return int(graph.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0])


def fingerprint(graph) -> tuple[int, int]:
    return (node_count(graph), edge_count(graph))


def _strictly_dominates(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """True when ``a`` is at least as large as ``b`` on BOTH count axes, and larger on one.

    Used by ``retain_clone`` to stop a lossier clone from overwriting a more complete
    retained copy. It is deliberately a COUNT comparison and therefore NOT proof of
    containment (see ``retain_clone``): it only decides which of the two copies is smaller,
    never that the larger one holds everything the smaller one does -- which is why the
    smaller copy is still preserved on disk rather than deleted.
    """
    return a[0] >= b[0] and a[1] >= b[1] and a != b


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

    ⛔ AND A COUNT BELOW ``MIN_NODES`` IS NOT A VERDICT. The presence check and the count read
    below are separate statements, so a graph deleted BETWEEN them is auto-created EMPTY by
    the read -- making presence TRUE while the graph holds nothing. Every check after that
    then passes: the CREATE is accepted, the post-write re-check sees the phantom this call
    just made, and ``probe`` returns "ok" on an empty graph. On the clone path that empty
    phantom is RENAMEd over the live graph (measured 1500 -> 0 in review cycle 5); on the
    ``main()`` path the tool prints HEALTHY with exit 0 for a zero-node graph, and the
    scheduled job keys on that exit code. Every graph this function is asked about holds at
    least ``MIN_NODES`` -- the faulted live graph does, and so does a faithful clone of it --
    so a count below the floor means the key was re-created, which is not a fact about the
    graph that was verified.
    """
    from tortoise.graph_delete_guard import graph_exists

    try:
        if not graph_exists(db, name):
            return "absent", f"graph {name!r} does not exist"
        graph = db.select_graph(name)
        before = node_count(graph)
    except Exception as exc:
        return "unknown", f"presence/count read failed: {type(exc).__name__}: {exc}"

    # ⛔ REFUSE AN EMPTY RE-MATERIALISED GRAPH. See the docstring: a graph deleted between the
    # presence check above and this read is auto-created EMPTY by the read, so presence comes
    # back TRUE and every later check passes on a graph that holds nothing -- the empty
    # phantom then gets swapped over the live graph on the clone path, or reported HEALTHY
    # with exit 0 by `main()`. An auto-created graph holds ZERO nodes, so that is the exact
    # signature, and it is the one to test: a small graph is still a real graph with real
    # nodes, and `repair` applies its own MIN_NODES floor to decide whether it is the shared
    # graph at all. This check answers a narrower question -- "is this the graph I verified, "
    # or a key that was re-created after it vanished?"
    if before == 0:
        return "unknown", ("the graph holds 0 nodes -- a count this low means the key was "
                            "re-created empty (a read on an absent graph auto-creates one), "
                            "so this is NOT a verdict about the graph that was verified")

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
    """COPY -> verify clone -> atomic RENAME. Deletes NOTHING; a failed repair retains it."""
    from tortoise.graph_delete_guard import graph_delete_lock, graph_exists
    from tortoise.hosted_backup import _audit_copied_boolean_indexes

    def say(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    # The run-unique scratch name exists so two concurrent runs (the timer and a hand-run)
    # cannot clean up each other's clone DURING the repair.
    scratch = f"__gwr_{uuid.uuid4().hex[:12]}"
    # The retained clone is parked at a DETERMINISTIC key, one per source graph. Retaining
    # under the run-unique name is unbounded: the scheduled job retries every StartInterval,
    # so a standing failure leaked one full copy of the graph per run (#8451 review, P2).
    quarantine = f"{QUARANTINE_PREFIX}{name}"
    clone_db = db.select_graph(scratch)

    def _graph_counts(gname: str) -> tuple[int, int] | None:
        """(nodes, edges) of an EXISTING graph, or None when it is absent or unreadable.

        Presence FIRST, always: `fingerprint()` READS, and a read on an absent graph
        AUTO-CREATES an empty one, so this must never be called without the guard.
        """
        try:
            if not graph_exists(db, gname):
                return None
            return fingerprint(db.select_graph(gname))
        except Exception:
            return None

    def retain_clone(reason: str) -> str:
        """Park this run's clone at the source's quarantine key. ⛔ NEVER DELETES.

        Every call site is a FAILED repair, and a failed repair is never cleaned up: the
        clone may be the only copy of data the source no longer has. Three review cycles
        tried to find a count predicate that would license a delete and each closed one case
        and left the next; cycle 5 reproduced the end of that road -- a source churning
        NET-ZERO (losing node `n0` while gaining another) dominated the clone on every count
        axis, so the count test deleted the clone holding the ONLY copy of `n0`. Count
        equality is not containment and no count test can be sound, so the clone is retained
        unconditionally.

        ⛔ PARKED AT THE DETERMINISTIC KEY, NOT THE RUN-UNIQUE SCRATCH NAME. `RENAME`
        REPLACES the previous retained copy in one atomic step, so a standing failure keeps
        ONE clone per source instead of accumulating one per run. No delete is involved. If
        the park itself fails the clone stays under its run-unique name -- reported, never
        silent.

        ⛔ BUT NEVER DOWNGRADE A MORE COMPLETE RETAINED COPY. The park is an unconditional
        `RENAME`, so the NEWEST clone always wins -- and the "incomplete copy" call site
        retains a clone that is KNOWN to be missing data relative to the source. A later,
        lossier clone therefore destroyed an earlier, more complete retained copy while the
        log still asserted the survivor "may be the only copy of data the source no longer
        has". When the retained copy strictly dominates this run's clone on (nodes, edges),
        the retained copy is KEPT and this run's clone is left at its run-unique scratch key
        -- the smaller copy is PRESERVED, never deleted, because a count is not containment.
        """
        existing_fp = _graph_counts(quarantine)
        this_fp = _graph_counts(scratch)
        if (existing_fp is not None and this_fp is not None
                and _strictly_dominates(existing_fp, this_fp)):
            say(f"  note: KEPT the retained clone {quarantine} {existing_fp} -- this run's "
                f"clone {scratch} {this_fp} is smaller; not downgrading it. Both are kept "
                f"(the smaller copy is never deleted); an operator clears them.")
            return quarantine

        parked = True
        try:
            db.connection.execute_command("RENAME", scratch, quarantine)
        except Exception as exc:
            parked = False
            say(f"  note: could not park the clone at {quarantine} ({exc}); it remains at "
                f"{scratch} (unbounded until cleared)")
        where = quarantine if parked else scratch
        say(f"  note: RETAINED clone {where} ({reason}) -- a failed repair is never cleaned "
            f"up; this clone may be the only copy of data the source no longer has")
        return where

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
                retain_clone("incomplete copy")
                return False, (f"clone {clone_fp} != source {(before_nodes, before_edges)} "
                               f"- refusing to swap; clone {quarantine} RETAINED")

            kind, detail = probe(db, scratch)
            if kind != "ok":
                # RETAIN the clone: it may be the only copy. Never clean up a failed repair.
                retain_clone(f"the clone is not usable ({kind})")
                return False, (f"the clone is not usable ({kind}: {detail}) - refusing to "
                               f"swap; clone {quarantine} retained")

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
                retain_clone("clone carries an unrepairable boolean index (#3154)")
                return False, (f"the clone's #3154 boolean-index audit failed ({exc}) - "
                               f"refusing to swap; clone {quarantine} RETAINED")

            say("  [3/4] clone is complete (nodes AND edges), writable, and index-audited")

            # Re-read immediately before the irreversible step: the section may have outlived
            # the lock's TTL, and a second run's stale clone must not overwrite a graph the
            # fleet has written to since.
            #
            # ⛔ PRESENCE FIRST -- BEFORE THE READ, AND THE CLONE IS RETAINED WHEN THE SOURCE
            # IS GONE. `fingerprint()` READS, and a read on an ABSENT graph AUTO-CREATES an
            # empty one (the module's own premise, and the hazard `tortoise/hosted_backup.py`
            # documents). Ordering the read first is therefore not merely useless -- it is
            # DESTRUCTIVE: the read materialises the absent graph, `now_fp` becomes (0, 0),
            # that differs from `(before_nodes, before_edges)`, and on `main` the old
            # `drop_clone` then DELETED THE CLONE -- leaving an empty graph and NO COPY
            # ANYWHERE. Measured: 1500 nodes
            # before, 0 after, clone gone. That directly contradicts the rule the COPY step
            # already states ("RETAIN the clone: it may be the only copy").
            # The source can genuinely vanish in this window: the graph-delete lock's TTL is
            # NOT renewed, this section contains a full `GRAPH.COPY`, and the container is
            # `restartpolicy=always`.
            if not graph_exists(db, name):
                retain_clone("the source disappeared during the repair")
                return False, (f"the source {name!r} disappeared during the repair -- clone "
                               f"{quarantine} RETAINED (it may be the only copy)")
            now_fp = fingerprint(db.select_graph(name))
            # A source that GAINED makes the clone stale, and the swap is REFUSED: a
            # concurrent ADDITION passes a `< before_nodes` test, the older clone is then
            # swapped in, and the write that landed between COPY and RENAME is silently
            # discarded -- the post-swap `after >= before` check cannot see it.
            #
            # ⛔ BUT A LOSS IS NOT STALENESS -- IT IS THE CASE WHERE THE CLONE MAY BE THE
            # ONLY COPY, SO IT IS RETAINED. The presence check above is not sufficient
            # on its own: `fingerprint()` READS, and a read auto-creates, so the destructive
            # path survives it in two ways. (1) TOCTOU -- the source can be deleted between
            # the presence check and this read, and the read then materialises an empty
            # graph. (2) PRESENT BUT EMPTIED -- presence is genuinely true and the read
            # still yields (0, 0). Both reached the old `drop_clone` and deleted the only
            # surviving copy.
            # The distinction that matters is DIRECTION: a source that GAINED makes the
            # clone stale; a source that LOST nodes or edges, or that reads empty, may mean
            # the clone is all that is left.
            if now_fp != (before_nodes, before_edges):
                if now_fp[0] < before_nodes or now_fp[1] < before_edges:
                    retain_clone("the source lost data during the repair")
                    return False, (f"the source LOST data during the repair ({now_fp} vs "
                                   f"{(before_nodes, before_edges)}) -- clone {quarantine} "
                                   f"RETAINED (it may be the only copy)")
                retain_clone("source grew under us")
                return False, (f"the source changed during the repair ({now_fp} vs "
                               f"{(before_nodes, before_edges)}) - refusing to swap over "
                               f"it; clone {quarantine} RETAINED")

            # ⛔ THE COUNT GATE IS EQUALITY, NOT IDENTITY -- SO RE-PROBE THE SOURCE.
            # `now_fp == (before_nodes, before_edges)` cannot see a NET-ZERO churn: an
            # operation that removes one node/edge and adds another (a supersede is exactly
            # that shape) leaves both counts unchanged, the gate passes, and the older clone
            # is RENAMEd in -- discarding the concurrent write while reporting success. The
            # false negative only matters while the source ACCEPTS WRITES, because an
            # unwritable source refuses every write and no churn can land. So the swap is
            # licensed ONLY while the source is STILL refusing writes with the defect this
            # repair targets; anything else (healthy, or state not established) retains the
            # clone and refuses. An identity check over ~20k nodes is not cheap; re-using the
            # probe's write test is the cheap sufficient one.
            src_kind, src_detail = probe(db, name)
            if src_kind != "defect":
                retain_clone(f"the source is no longer refusing writes ({src_kind})")
                return False, (f"the source {name!r} is no longer in the repair's defect "
                               f"state ({src_kind}: {src_detail}) -- the clone may be stale "
                               f"and concurrent writes may be landing; refusing to swap; "
                               f"clone {quarantine} RETAINED")

            # ⛔ RE-VERIFY THE CLONE IMMEDIATELY BEFORE THE SWAP. Everything checked about the
            # clone happened earlier -- its copy, its fingerprint, its probe, its index audit
            # -- and this is the last moment a check can still run BEFORE the irreversible
            # step. The clone is STATIC after its audit (nothing writes to it), so this
            # comparison is exact, not a heuristic: it is what stops an empty phantom from
            # being swapped over the live graph when the clone was deleted and then
            # auto-created empty by someone's read in the meantime.
            if not graph_exists(db, scratch):
                retain_clone("the clone vanished before the swap")
                return False, (f"the clone {scratch} is GONE before the swap -- it was "
                               f"deleted mid-run and any read of its key would auto-create "
                               f"an EMPTY graph, which must never be swapped over {name!r}")
            if fingerprint(db.select_graph(scratch)) != (before_nodes, before_edges):
                retain_clone("the clone no longer matches the source snapshot")
                return False, (f"the clone {scratch} no longer matches the "
                               f"{(before_nodes, before_edges)} snapshot -- refusing the swap")

            # ATOMIC. `RENAME` overwrites in one step, so there is never a window in which
            # `name` is absent -- that window is what auto-created an empty graph and
            # destroyed this graph on 2026-10-13.
            say(f"  [4/4] RENAME {scratch} -> {name}   (atomic; no delete window)")
            try:
                db.connection.execute_command("RENAME", scratch, name)
            except Exception as exc:
                present = graph_exists(db, name)
                # ⛔ PARK THE CLONE HERE TOO. This is the one failure the scheduled job is
                # GUARANTEED to retry (the probe still classes the graph as the defect), so
                # leaving the clone under its run-unique name leaked a full copy per run
                # and no `__gwr_q_*` ever appeared (#8451 review cycle 5, P2). The park is
                # an atomic RENAME of a graph this run owns; if it fails, `retain_clone`
                # reports the clone is still at `scratch`.
                where = retain_clone("the swap RENAME failed")
                return False, (f"RENAME failed: {exc}; source present={present}, "
                               f"clone {where} retained as the fallback")
    except Exception as exc:
        # ⛔ THE OUTER FAILURE PATH RETAINS TOO. Anything raised around the lock (or inside
        # it before the branches above) used to return with the clone still at its
        # run-unique name, so a standing failure accumulated one full copy per run. Park it
        # when a clone exists; if even this fails, the message still names where it was left.
        where = None
        try:
            clone_present = graph_exists(db, scratch)
        except Exception:
            clone_present = False
        if clone_present:
            try:
                where = retain_clone("the repair aborted")
            except Exception:
                where = scratch
        tail = f"; clone {where} retained as the fallback" if where else ""
        return False, f"repair aborted: {type(exc).__name__}: {exc}{tail}"

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
