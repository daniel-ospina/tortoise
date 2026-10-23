#!/usr/bin/env python3
"""End-to-end leg: journal -> Postgres (record) -> FalkorDB (cache), with the
record read back through the SHIPPED `read_record_view`.

This is the composite run the epic's own "the run that would settle it" clause
describes, minus the chokepoint (which is #7951, still open). It uses:

  * the SHIPPED journal fixture  supabase/tests/fixtures/record_projection_journal.jsonl
  * the SHIPPED projection       tortoise/record_projection.py::replay
  * the SHIPPED reader           tortoise/cache_projection.py::read_record_view
  * the SHIPPED cache rebuild    FalkorProjection.rebuild_from_record

Requires:  a Postgres with the record migrations applied
           (started by docs/evidence/7950-capstone/probes/e2e_postgres_bootstrap.py)
           and TORTOISE_DB_URI pointing at a FalkorDB.

The probe CONNECTS as `postgres` (that is who the bootstrap applies the
migrations as, so it owns every record table) and then `SET ROLE`s into
`tortoise_record_app` before it touches a record door. Both record doors refuse
a bypassing role — superuser, `BYPASSRLS`, a member of either, or a table OWNER
— because RLS is the record's tenancy boundary (R6, #9554): as `postgres` the
probe is refused before its first plan statement (#9600 review, Finding 1).
`SET ROLE` is the available route because the app role is `NOLOGIN` until ops
grants LOGIN (R1, docs/infra-runbook.md) and `postgres` is its creator.

Run:
  TORTOISE_DB_URI=... uv run --with psycopg2-binary \
    python docs/evidence/7950-capstone/probes/e2e_chain_probe.py
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from tortoise.cache_projection import KEY_PROPERTY, RecordView, declared_key

ROOT = Path(__file__).resolve().parents[4]  # repo root
FIXTURE = ROOT / "supabase/tests/fixtures/record_projection_journal.jsonl"
MIG_DIR = ROOT / "supabase/migrations"
RECORD_MIGRATIONS = [
    "20261012000001_record_event_stream.sql",
    "20261012000002_record_semantic.sql",
    "20261012000003_record_epistemic.sql",
    "20261012000004_record_procedural.sql",
    # The erasure RPC — a FUNCTION, so it contributes no column (#9550: listed
    # because `RECORD_MIGRATIONS` is the record's migration REGISTRY, and this
    # probe's list is pinned to it in EQUALITY, not containment).
    "20261012000005_record_org_purge_rpc.sql",
    "20261014000001_record_subject_props_no_derived.sql",
    # Kept in step with `e2e_postgres_bootstrap.py` (whose list actually applies
    # them) even though nothing here reads this constant today: a dead copy that
    # is allowed to drift becomes a live lie the moment it is re-lit (#8976 P3).
    # Pinned by `tests/test_7950_capstone_probe.py`.
    "20261018000001_record_point_money_value_2782.sql",
    # #9264's forward migration, kept in step with the list that applies them.
    "20261022000001_record_session_capture_redactions_integer.sql",
    # §9.1's narrative home (#9555 / R7): the shipped `SessionRecorded` replay
    # emits an INSERT into `public.session_narrative`, so the probe's database
    # must carry the table it writes to. Pinned by
    # `tests/test_7950_capstone_probe.py`.
    "20261023000001_record_session_narrative.sql",
    # The TENANCY POSTURE (#9554, epic #7879): the app role, its grants (and
    # `relation_id_seq`'s USAGE) and the `*_app_tenant` policies, one per record
    # table (ten, since #9555's `session_narrative`). No column
    # — kept in step with `e2e_postgres_bootstrap.py` for the same reason.
    "20261025000001_record_tenancy_role_and_guc.sql",
]
DSN = os.environ.get(
    "CAPSTONE_PG_DSN", "postgresql://postgres:cap@127.0.0.1:15499/capstone")

#: The record's APPLICATION role (R6, epic #7879) — the only role
#: `record_projection.scope_connection()` admits. It is pinned to the role the
#: tenancy migration creates by `tests/test_7950_capstone_probe.py`, so a rename
#: on either side reds instead of leaving this probe dead.
APP_ROLE = "tortoise_record_app"

#: The record tables this probe counts after the replay (all of them the
#: projection writes, minus the narrative home it does not exercise).
COUNTED_TABLES = ("journal_event", "source", "event", "session", "object",
                  "subject", "point", "relation", "node_identity")

ORG, GRAPH = "cap7950-org", "cap7950-graph"
CACHE_GRAPH = "test_cap7950_e2e"


def record_connection(psycopg2_mod, dsn: str = DSN):
    """Connect, then take on :data:`APP_ROLE` — the only way the doors open.

    ⛔ THE ROLE IS A SESSION-SCOPED `SET ROLE`; THE TENANT IS A TRANSACTION-LOCAL GUC.
    They answer different questions and neither substitutes for the other: the
    role decides whether the record policies are EVALUATED at all (a bypassing
    role skips them, which `scope_connection()` refuses), the GUC decides which
    TENANT they admit (and is re-installed inside every transaction by
    `scope_connection()`, because `set_config(..., true)` does not survive a
    commit — see :func:`record_row_counts`).

    The commit is deliberate: `SET ROLE` is undone by a ROLLBACK, so taking it
    outside a transaction is what makes it stick for the whole session. A
    failure leaves no half-scoped connection behind.
    """
    conn = psycopg2_mod.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute(f"SET ROLE {APP_ROLE}")
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


def record_row_counts(conn, org_id: str = ORG, graph_id: str = GRAPH) -> dict:
    """Per-table row counts for one tenant, READ UNDER THE TENANT SCOPE.

    ⛔ AN UNSCOPED COUNT IS NOT AN ERROR, IT IS ZERO ROWS (#9600 review).
    `replay()`'s prologue is transaction-LOCAL, so the caller's `commit()` ends
    it; under the app role the policies then see a NULL GUC and `SELECT
    count(*)` returns 0 for a record the replay just filled. The probe would
    print `CHAIN STOP: the record holds no nodes` on a COMPLETE chain — a
    harness defect wearing the verdict's clothes. `scope_connection()` is the
    same prologue the shipped doors install, so the count is read exactly the
    way the record is read.
    """
    from tortoise import record_projection as rp

    cursor = rp.scope_connection(conn, org_id)
    counts: dict = {}
    for table in COUNTED_TABLES:
        cursor.execute(f"SELECT count(*) FROM public.{table} "
                       "WHERE org_id = %s AND graph_id = %s", (org_id, graph_id))
        counts[table] = cursor.fetchone()[0]
    return counts


def record_key_map(view: RecordView) -> dict[str, dict]:
    """`{declared key: {property: value}}` — the record's REAL property maps.

    A reader that returned `{}` for every key makes `field_mismatches`
    STRUCTURALLY zero: `reconcile_views` compares two per-key MAPPINGS field by
    field, and two empty mappings always agree. A second writer that modified
    an existing node's property would then be invisible, and the count would
    prove key-set agreement only. The maps must be the actual field sets.
    """
    return {declared_key(n.label, n.key): dict(n.props) for n in view.nodes}


def cache_key_map(
        rows_by_label: Mapping[str, Iterable[Mapping]]) -> dict[str, dict]:
    """`{declared key: {property: value}}` from the cache's own property maps.

    ``rows_by_label`` is ``{label: [properties(n), ...]}`` — the caller runs
    the read, this function does the keying, so the values are what the cache
    actually holds rather than a placeholder.
    """
    out: dict[str, dict] = {}
    for label, rows in rows_by_label.items():
        key_prop = KEY_PROPERTY[label]
        for props in rows:
            props = dict(props or {})
            key = props.get(key_prop)
            if key:
                out[declared_key(label, key)] = props
    return out


#: The token the issue's acceptance command greps for, and the two values it may
#: take. Pinned as constants because the LINE is the verdict (#9079).
RAW_HANDLE_REFUSED = "refused"
RAW_HANDLE_LANDED = "SUCCEEDED (direct cache write)"


def armed_raw_handle_write(proj) -> str:
    """Attempt §17.1's second-writer write and report what ACTUALLY happened.

    ⛔ THE VERDICT IS MEASURED, NEVER PRINTED FROM A LITERAL (#9079). The caller
    used to `print("ARMED RAW HANDLE (proj.g.query):  SUCCEEDED (direct cache
    write)")` unconditionally, one line after the write: the string was the same
    whether the statement landed, was REFUSED, or never ran. Worse, on an armed
    projection the refusal *raised out of the probe*, so the acceptance command

    ``uv run python docs/evidence/7950-capstone/probes/e2e_chain_probe.py |
    grep "ARMED RAW HANDLE"``

    could never read `refused` — the instrument could not express the verdict
    its own issue requires. The S1 verdict in the README rests on this line, so a
    hardcoded one is a false PASS in the epic's evidence.

    Returns ``RAW_HANDLE_REFUSED`` when the handle refuses the statement (a
    ``DirectCacheWriteRefused`` in flight — the §17.1 door doing its job) and
    ``RAW_HANDLE_LANDED`` when the write REACHED THE CACHE, which is CHECKED: a
    return without an exception only proves the statement was dispatched, so the
    landed branch reads the node back and refuses to call a vanished write a
    success. Any OTHER exception is a broken probe, not a verdict, and
    propagates: a silently-swallowed error here would report a refusal the guard
    never made.
    """
    from tortoise.projection import DirectCacheWriteRefused

    try:
        proj.g.query("CREATE (:Point {id:'p-direct-write-7950'})")
    except DirectCacheWriteRefused:
        return RAW_HANDLE_REFUSED
    # The statement was ACCEPTED — now prove it LANDED. Without this read the
    # token would answer "was it refused?" and be PRINTED as "did it reach the
    # cache?", which are different questions (a no-op MATCH, a rolled-back
    # transaction, or a different graph would answer them differently).
    rows = proj.query(
        "MATCH (n:Point {id:'p-direct-write-7950'}) RETURN count(n)"
    ).result_set
    if not rows or not rows[0][0]:
        raise RuntimeError(
            "the §17.1 probe write was neither blocked nor landed: `CREATE "
            "(:Point {id:'p-direct-write-7950'})` returned without raising and "
            "the node is absent. That is a broken probe, not a verdict")
    return RAW_HANDLE_LANDED


def armed_fold_line(proj) -> str:
    """The FOLD lane's §17.1 line, from the OUTCOME of ``proj.apply``.

    ⛔ THE EXCEPTION NAME IS READ OFF THE EXCEPTION, NEVER SPELLED. The line
    used to be the literal ``"… refused (DirectCacheWriteRefused)"`` — accurate,
    but it is ALSO the spelling of a verdict, because ``re``-free substring
    matching on ``"refused"`` hits the class name inside it. That made a
    correct, informative line a false positive of the TOTAL verdict rule below
    and it was replaced with ``"blocked (§17.1 refusal)"``, which dropped the
    one place the caught exception's type was recorded (#9079 review, P3).
    Taking ``type(exc).__name__`` keeps the fact AND keeps the source
    verdict-free: the emitted name cannot disagree with what was caught, and no
    value constant spells either token.
    """
    from tortoise.projection import DirectCacheWriteRefused

    try:
        proj.apply({"type": "PointAdded", "id": "p-fold-7950"})
    except DirectCacheWriteRefused as exc:
        return ("ARMED FOLD (proj.apply):          blocked "
                f"(§17.1 {type(exc).__name__})")
    return "ARMED FOLD (proj.apply):          NO RAISE"


def s1_report_text(proj) -> tuple[bool, str]:
    """The COMPLETE §17.1 report for an armed projection, from ONE measurement.

    Returns ``(refused, report)``. ``report`` is every line the probe emits about
    §17.1 — the guard MODE, the acceptance token and the S1 verdict — as one
    string, and ``refused`` is the measurement's own branch signal, so `main`
    never has to read a verdict constant to decide whether to run the
    demonstration.

    ⛔ THE MODE IS READ FROM THE GUARD, NOT SUPPLIED BY THE CALLER. This used to
    take ``guard_enforces=``, and `main` passed `_HANDLE_GUARD_ENFORCES` — a
    correct argument nobody pinned, so spelling ``guard_enforces=True`` at the
    one call site made the epic's deciding artifact claim the door was armed
    while it was not (a `True` literal is invisible to the value-string rule
    below, which sees only `str`/`bytes`). The flag is now read here, from
    `tortoise.projection`, so there is no argument to falsify; the acceptance
    verdict and the S1 summary still follow the MEASUREMENT (#9079 review, P3).

    ⛔ WHY ONE STRING. The pins are on EMITTED TEXT, not on the shape of the
    source: a test drives this function with a stub projection in both modes and
    compares the whole report, so a verdict that was not measured cannot be
    printed by anything this function returns. Returning lines and having
    `main` print them one by one left "add one hardcoded line after the loop"
    invisible; one value written once has no such seam (`tests/…
    test_the_report_is_one_measured_text`).

    ⛔ ONE ACCEPTANCE LINE, ON PURPOSE. The issue's acceptance command is
    ``… | grep "ARMED RAW HANDLE"`` and it must read ``refused``. The
    demonstration lines further down used to share that prefix, so the grep
    matched them too and "the line reads refused" was not literally true of the
    command; they are now labelled ``SECOND-WRITER MUTATION``.
    """
    from tortoise import projection as _projection

    #: The guard's own switch, read at CALL time (a module attribute, never a
    #: captured copy), so the MODE follows the flag the door actually consults.
    guard_enforces = _projection._HANDLE_GUARD_ENFORCES

    raw_handle = armed_raw_handle_write(proj)
    refused = raw_handle == RAW_HANDLE_REFUSED
    report = "\n".join([
        "HANDLE GUARD MODE:               "
        + ("enforces" if guard_enforces
           else "measures only (refusal NOT armed, #9124 part 2)"),
        f"ARMED RAW HANDLE (proj.g.query):  {raw_handle}",
        "S1 (second writer on a journal-backed cache): "
        + ("CLOSED (the second writer was blocked)" if refused
           else "OPEN (the write reached the cache with no journal record "
                "behind it)"),
    ]) + "\n"
    return refused, report


def main() -> int:
    import psycopg2  # the optional `postgres` extra; the probe runs with it
    # ⛔ NOT a bare `psycopg2.connect`: this DSN is `postgres`, the owner of every
    # record table, which both shipped doors refuse (R6, #9554). See
    # `record_connection`.
    conn = record_connection(psycopg2)
    try:
        # ── 1. journal -> Postgres ─────────────────────────────────────────
        records = [json.loads(ln) for ln in FIXTURE.read_text().splitlines() if ln.strip()]
        print(f"journal records read: {len(records)}")

        from tortoise import record_projection as rp
        report = rp.replay(records, conn, org_id=ORG, graph_id=GRAPH)
        conn.commit()
        print(f"record projection: {report}")

        counts = record_row_counts(conn)
        print(f"record row counts: {counts}")
        if counts["point"] == 0 and counts["object"] == 0:
            print("CHAIN STOP: the record holds no nodes; nothing to project")
            return 2

        # ── 2. Postgres -> RecordView (the seam that has no production wire) ─
        from tortoise import cache_projection as cp
        view = cp.read_record_view(conn, org_id=ORG, graph_id=GRAPH)
        print(f"read_record_view -> {len(view.nodes)} nodes, "
              f"{len(view.relations)} relations")
        by_label = {}
        for n in view.nodes:
            by_label[n.label] = by_label.get(n.label, 0) + 1
        print(f"record view by label: {by_label}")

        # ── 3. RecordView -> FalkorDB (cache projection) ───────────────────
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection.from_uri(os.environ["TORTOISE_DB_URI"],
                                         graph_name=CACHE_GRAPH)
        proj._wipe_all_nodes(confirm_destructive=True, operation="capstone-e2e")
        out = proj.rebuild_from_record(view, confirm_destructive=True)
        print(f"cache projection report: {out}")

        hot = proj.query(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c "
            "ORDER BY label").result_set
        print(f"cache node counts (live read): {[(r[0], r[1]) for r in hot]}")

        # a Point from the journal must now be in the cache, by its contract key
        point_ids = [n.key for n in view.nodes if n.label == "Point"]
        if point_ids:
            pid = point_ids[0]
            got = proj.query(
                f"MATCH (n:Point {{id:'{pid}'}}) RETURN n.id").result_set
            print(f"hot read for Point {pid!r}: {got}")
            if not got:
                print("CHAIN STOP: the record key did not materialize in the cache")
                return 3

        # ── 4. a BOUNDED rebuild → a partial declaration → not-hot ────────
        from tortoise.cache_projection import declared_key
        from tortoise.residency import RESIDENCY_NOT_HOT, read_declaration
        proj.rebuild_from_record(view, confirm_destructive=True, limit=1)
        decl = read_declaration(proj.g)
        print(f"bounded rebuild declaration: coverage={decl.coverage}")
        declared = set(decl.hot_keys or ())
        unwritten = [(lab, k) for (lab, k) in view.record_keys
                     if (lab, k) not in declared]
        if decl.coverage == "partial" and unwritten:
            label, key = unwritten[0]
            print(f"unwritten record key {declared_key(label, key)!r} "
                  f"covered-by-declaration={((label, key) in declared)} "
                  f"→ residency={RESIDENCY_NOT_HOT}")

        # ── 5. reconcile the two stores (S2 on real data) ──────────────────
        from tortoise.reconciliation import reconcile

        def _record_keys() -> dict:
            # A fresh connection is a fresh session, so it needs the role again;
            # `read_record_view` installs the tenant scope itself.
            c2 = record_connection(psycopg2)
            try:
                v = cp.read_record_view(c2, org_id=ORG, graph_id=GRAPH)
                return record_key_map(v)
            finally:
                c2.close()

        def _cache_keys() -> dict:
            rows_by_label: dict[str, list] = {}
            for lab in cp.NODE_TABLES:
                rows = proj.query(
                    f"MATCH (n:{lab}) RETURN properties(n)").result_set
                rows_by_label[lab] = [r[0] for r in rows if r]
            return cache_key_map(rows_by_label)

        # full rebuild first, so the two sides should agree
        proj.rebuild_from_record(view, confirm_destructive=True)
        # Non-vacuity guard: `reconcile_views` compares two per-key MAPPINGS
        # field by field, and two `{}` values always agree — so a reader that
        # returned a placeholder for every key would make `field_mismatches`
        # structurally zero and the count would prove key-set agreement only
        # (the #8609 review, finding 3). Refuse to report such a comparison.
        if not any(_record_keys().values()) or not any(_cache_keys().values()):
            print("RECONCILE STOP: a reader returned EMPTY property maps — the "
                  "field comparison would be structurally zero, so the count "
                  "proves key-set agreement only")
            return 4
        rep = reconcile(_record_keys, _cache_keys)
        print(f"RECONCILE (after a record-sourced rebuild): {rep.render()}")

        # now simulate the §17.1 violation S1 says is possible: a DIRECT cache
        # write on the raw handle, no journal, no record. First ARM the guard
        # (§17.1 with a journal bound) so the demonstration is on the guarded
        # lane, not an unguarded one.
        proj._bind_journal("/tmp/cap7950-journal.jsonl")
        print(armed_fold_line(proj))
        # ⛔ MEASURED, NOT ASSERTED (#9079): these lines are the issue's
        # acceptance token and S1's verdict, so they are printed from the
        # measurement — never spelled out beside it. No keyword: the MODE is
        # read from the guard inside the builder, so `main` cannot state one.
        refused, s1_report = s1_report_text(proj)
        # ONE write, ONE value: the report is emitted as the text the
        # measurement produced, so there is no per-line seam a second,
        # unmeasured line could be inserted into.
        sys.stdout.write(s1_report)
        if refused:
            # §17.1 held: there is no divergence to reconcile and no property to
            # mark, so the demonstrations below are NOT ATTEMPTED rather than
            # reported as if they had run. This is the S1-GREEN path; it is
            # unreachable while `_HANDLE_GUARD_ENFORCES` is False (the
            # measure-mode default, #9124 part 2), and the MODE line above says
            # which run this is instead of leaving the reader to infer it.
            print("SECOND-WRITER MUTATION:           not attempted (the write "
                  "did not land, so no node was marked)")
        else:
            rep2 = reconcile(_record_keys, _cache_keys)
            print(f"RECONCILE (after a DIRECT raw-handle cache write): {rep2.render()}")
            print(f"second-writer signature -> extra_in_cache={rep2.extra_in_cache}")

            # ── 5c. a second writer that MUTATES an existing node ─────────
            # A key-set comparison cannot see this at all, and a `{}`-valued map
            # makes it structurally invisible even to the field comparison — so
            # this is the signature the REAL property maps exist to surface
            # (finding 3: a value-only divergence was previously unmeasurable).
            tgt = view.nodes[0]
            kp = cp.KEY_PROPERTY[tgt.label]
            proj.g.query(
                f"MATCH (n:{tgt.label} {{{kp}: $key}}) SET n += $props",
                {"key": tgt.key,
                 "props": {"second_writer_marker": "direct-cache-write"}})
            print(f"SECOND-WRITER MUTATION:          {declared_key(tgt.label, tgt.key)} "
                  f"gained an unauthorized property "
                  f"({tgt.label}.second_writer_marker)")
            rep3 = reconcile(_record_keys, _cache_keys)
            print("RECONCILE (after a DIRECT raw-handle property WRITE): "
                  f"{rep3.render()}")
            print("field-mutation signature -> "
                  f"field_mismatches={rep3.field_mismatches}")

        print("CHAIN RESULT: journal -> record -> cache COMPLETED with the shipped modules")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
