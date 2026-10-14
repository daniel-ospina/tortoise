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
    "20261014000001_record_subject_props_no_derived.sql",
]
DSN = os.environ.get(
    "CAPSTONE_PG_DSN", "postgresql://postgres:cap@127.0.0.1:15499/capstone")
ORG, GRAPH = "cap7950-org", "cap7950-graph"
CACHE_GRAPH = "test_cap7950_e2e"


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


def main() -> int:
    import psycopg2  # the optional `postgres` extra; the probe runs with it
    conn = psycopg2.connect(DSN)
    try:
        # ── 1. journal -> Postgres ─────────────────────────────────────────
        records = [json.loads(ln) for ln in FIXTURE.read_text().splitlines() if ln.strip()]
        print(f"journal records read: {len(records)}")

        from tortoise import record_projection as rp
        report = rp.replay(records, conn, org_id=ORG, graph_id=GRAPH)
        conn.commit()
        print(f"record projection: {report}")

        cur = conn.cursor()
        counts = {}
        for table in ("journal_event", "source", "event", "session", "object",
                      "subject", "point", "relation", "node_identity"):
            cur.execute(f"SELECT count(*) FROM public.{table} "
                        "WHERE org_id = %s AND graph_id = %s", (ORG, GRAPH))
            counts[table] = cur.fetchone()[0]
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
            c2 = psycopg2.connect(DSN)
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
        from tortoise.projection import DirectCacheWriteRefused
        try:
            proj.apply({"type": "PointAdded", "id": "p-fold-7950"})
            print("ARMED FOLD (proj.apply):          NO RAISE")
        except DirectCacheWriteRefused:
            print("ARMED FOLD (proj.apply):          refused (DirectCacheWriteRefused)")
        proj.g.query("CREATE (:Point {id:'p-direct-write-7950'})")
        print("ARMED RAW HANDLE (proj.g.query):  SUCCEEDED (direct cache write)")
        rep2 = reconcile(_record_keys, _cache_keys)
        print(f"RECONCILE (after a DIRECT raw-handle cache write): {rep2.render()}")
        print(f"second-writer signature -> extra_in_cache={rep2.extra_in_cache}")

        # ── 5c. a second writer that MUTATES an existing node ─────────────
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
        print(f"ARMED RAW HANDLE MUTATION:        {declared_key(tgt.label, tgt.key)} "
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
