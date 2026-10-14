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
from pathlib import Path

import psycopg2

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


def main() -> int:
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
                return {declared_key(n.label, n.key): {} for n in v.nodes}
            finally:
                c2.close()

        def _cache_keys() -> dict:
            out = {}
            for lab, tbl in cp.NODE_TABLES.items():
                kp = cp.KEY_PROPERTY[lab]
                rows = proj.query(
                    f"MATCH (n:{lab}) RETURN n.{kp}").result_set
                for r in rows:
                    if r and r[0]:
                        out[declared_key(lab, r[0])] = {}
            return out

        # full rebuild first, so the two sides should agree
        proj.rebuild_from_record(view, confirm_destructive=True)
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

        print("CHAIN RESULT: journal -> record -> cache COMPLETED with the shipped modules")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
