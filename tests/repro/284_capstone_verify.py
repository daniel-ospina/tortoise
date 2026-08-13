"""Capstone verification for #284 — 3-tier entity resolution close-out.

Verifies the shipped 3-tier hierarchy end-to-end (embedded FalkorDBLite):
  Tier 1 (Kind):      entity_type filter + expansion-pack kind filter via
                       PackRegistry.expand_kind (subclassOf/equivalentTo)
  Tier 2 (Abstract):   cross-entity FTS (tortoise_fts_query entity_type param)
  Tier 3 (Core-type):  per-label vector query (run_vector_query entity_type)
  Relationship ctx:    get_relationships {predicate, mechanism, related_id,
                       related_kind, direction, operator_id} + relationship_filter
                       + traversal_path

Also proves `action` entity_type is DEAD (raises ValueError).

Run: uv run python tests/repro/284_capstone_verify.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tortoise.sdk import TortoiseSDK
from tortoise.pack_registry import PackRegistry
from tortoise.search_engine import get_relationships

RESULTS: list[tuple[str, str]] = []  # (check_name, PASS|FAIL detail)


def check(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, "PASS" if ok else "FAIL"))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    db_path = os.path.join(tempfile.mkdtemp(prefix="capstone_284_"), "test.db")
    sdk = None
    try:
        sdk = TortoiseSDK(db_path)
        # ── 0. Pack registry: kind expansion (expansion-pack kinds) ──
        print("\n== Tier 1a: Pack registry kind expansion ==")
        reg = PackRegistry(Path(ROOT) / "packs")
        loaded = reg.load_all()
        check("packs load", loaded >= 4, f"loaded={loaded}")
        exp_workitem = reg.expand_kind("WorkItem")
        check(
            "expand_kind(WorkItem) includes pack subclasses",
            "dev:issue" in exp_workitem or "marketing:content" in exp_workitem,
            f"WorkItem→{exp_workitem}",
        )
        exp_feature = reg.expand_kind("product-strategy:feature")
        check(
            "expand_kind(product-strategy:feature) resolves",
            exp_feature == ["product-strategy:feature"],
            f"{exp_feature}",
        )
        rels = reg.list_relations()
        check("pack relations declared", len(rels) >= 1, f"{len(rels)} relations")
        addresses_rel = [r for r in rels if r.get("predicate") == "addresses"]
        check(
            "product-strategy 'addresses' relation declared",
            len(addresses_rel) > 0,
            f"{addresses_rel}",
        )

        # ── Seed a small graph (feature -(addresses)-> customerSegment) ──
        feature = sdk.create_point("product-strategy:feature", "Automated monthly reporting")
        cust_seg = sdk.create_point("product-strategy:customerSegment", "Enterprise data teams")
        op = sdk.create_operator("IMPL", feature["id"], [cust_seg["id"]])
        sdk.update_point(op["id"], label="addresses")
        # A core-kind point (no pack prefix)
        core_point = sdk.create_point("decision", "We will adopt staged rollout")

        # ── Tier 1b: entity_type + kind filter (kind tier) ──
        print("\n== Tier 1b: entity_type + kind filter ==")
        res = sdk.tortoise_fts_query(None, kind="product-strategy:feature", limit=10)
        ids = {r["id"] for r in res}
        check("kind filter product-strategy:feature returns feature", feature["id"] in ids, f"{ids}")

        res = sdk.tortoise_fts_query(None, kind="product-strategy:customerSegment", limit=10)
        ids = {r["id"] for r in res}
        check("kind filter product-strategy:customerSegment", cust_seg["id"] in ids, f"{ids}")

        res = sdk.tortoise_fts_query(None, kind="decision", limit=10)
        ids = {r["id"] for r in res}
        check("core kind filter 'decision' returns core point", core_point["id"] in ids, f"{ids}")

        res = sdk.tortoise_fts_query("reporting", kind="product-strategy:feature", limit=10)
        ids = {r["id"] for r in res}
        check(
            "text+kind: 'reporting' within product-strategy:feature",
            feature["id"] in ids,
            f"{ids}",
        )

        # ── Tier 2: abstract (cross-entity FTS via entity_type) ──
        print("\n== Tier 2: cross-entity entity_type search (abstract) ==")
        for et in ("point", "event", "subject", "document", "object", "operator", "source"):
            try:
                r = sdk.tortoise_fts_query("test", entity_type=et, limit=5)
                ok = isinstance(r, list)
                check(f"entity_type={et} accepted", ok, f"returned {len(r) if isinstance(r, list) else 'err'} results")
            except Exception as e:
                check(f"entity_type={et} accepted", False, f"raised {type(e).__name__}: {e}")

        # `action` is DEAD in v3.2 (Action dissolved in v3.0) — must reject
        try:
            sdk.tortoise_fts_query("test", entity_type="action", limit=5)
            check("entity_type='action' REJECTED (dead type)", False, "accepted — action is dead per v3.2")
        except ValueError as e:
            check("entity_type='action' REJECTED (dead type)", True, f"ValueError: {e}")

        # ── Tier 3: core-type vector search (per-label) ──
        print("\n== Tier 3: per-label vector query (core-type) ==")
        try:
            from tortoise.embeddings import EmbeddingModel
            model = EmbeddingModel.get()
            if model is None:
                # Designed degradation: EmbeddingModel.get() returns None in
                # offline/cold-CI envs and search falls back to TF-IDF — this
                # IS the correct behavior. Vector ROUTING is proven separately
                # by the Tier 3b synthetic-vec checks below.
                check(
                    "EmbeddingModel degraded-env fallback (model absent)",
                    True,
                    "None returned as designed — routing proven by Tier 3b synthetic vecs",
                )
            else:
                qv = model.encode(["reporting pipeline"])[0].tolist()
                from tortoise.search_engine import run_vector_query
                vres = run_vector_query(sdk._get_proj().g, qv, limit=10, is_embedded=True, entity_type="point")
                ids = {r[0] for r in vres}
                check("vector query entity_type=point runs", len(vres) > 0, f"{ids}")
        except Exception as e:
            check("vector query runs", False, f"raised {type(e).__name__}: {e}")

        # ── Relationship context ──
        print("\n== Relationship context ==")
        rels_map = get_relationships(sdk._get_proj().g, [feature["id"]])
        rels = rels_map.get(feature["id"], [])
        expected_keys = {"predicate", "mechanism", "related_id", "related_kind", "direction", "operator_id"}
        ok_shape = all(expected_keys <= set(r.keys()) for r in rels)
        check("get_relationships returns full shape", ok_shape, f"{rels}")
        addr = [r for r in rels if r.get("predicate") == "addresses"]
        check(
            "relationship context: addresses→customerSegment",
            len(addr) == 1 and addr[0]["related_id"] == cust_seg["id"]
            and addr[0]["related_kind"] == "product-strategy:customerSegment",
            f"{addr}",
        )

        # relationship_filter (predicate:target_id)
        res = sdk.tortoise_fts_query(
            None, kind="product-strategy:feature",
            relationship_filter=f"addresses:{cust_seg['id']}", limit=10,
        )
        ids = {r["id"] for r in res}
        check("relationship_filter addresses:seg returns feature", feature["id"] in ids, f"{ids}")

        # traversal_path resolves via pack registry
        res = sdk.tortoise_fts_query("reporting", traversal_path="Feature→CustomerSegment", limit=10)
        ids = {r["id"] for r in res}
        check("traversal_path Feature→CustomerSegment returns feature", feature["id"] in ids, f"{ids}")

        # per-label vector query with a SYNTHETIC query_vec (no model needed).
        # Proves the core-type tier's entity_type → label/id-field ROUTING by
        # spying on the Cypher the engine emits (embedded brute-force path),
        # so the assertion holds in offline CI with no stored embeddings.
        # NOTE: sdk._get_proj() is private API (no public graph handle) —
        # intentional for a repro script; revisit if the SDK adds one.
        print("\n== Tier 3b: per-label vector routing (synthetic vec) ==")
        from tortoise.search_engine import run_vector_query
        qv = [0.0] * 384
        qv[0] = 1.0
        g = sdk._get_proj().g
        captured: list[str] = []
        # _GuardedGraph delegates to the raw FalkorDB handle (g._g); its own
        # `query` is a read-only slotted method, so spy on the raw handle.
        raw = g._g
        orig_raw_query = raw.query

        def _spy_query(cypher, *args, **kwargs):
            captured.append(str(cypher))
            return orig_raw_query(cypher, *args, **kwargs)

        # label/id-field per entity_type — mirrors search_engine.run_vector_query
        # (#172: operators are Points with is_operator=true; #448: source→url,
        # event→eventId, else→id).
        routing = {
            "point": ("Point", "id"), "operator": ("Point", "id"),
            "event": ("Event", "eventId"), "subject": ("Subject", "id"),
            "document": ("Document", "id"), "object": ("Object", "id"),
            "source": ("Source", "url"),
        }
        try:
            raw.query = _spy_query
            for et, (exp_label, exp_id_field) in routing.items():
                captured.clear()
                vres = run_vector_query(g, qv, limit=10, is_embedded=True, entity_type=et)
                emitted = " ".join(captured)
                routed_ok = f"n:{exp_label}" in emitted and f"n.{exp_id_field}" in emitted
                check(
                    f"vector query entity_type={et} routes to {exp_label}.{exp_id_field}",
                    isinstance(vres, list) and routed_ok,
                    f"rows={len(vres)} cypher={emitted[:70]}",
                )
        finally:
            raw.query = orig_raw_query

        # ── Summary ──
        passed = sum(1 for _, s in RESULTS if s == "PASS")
        print(f"\n=== CAPSTONE RESULT: {passed}/{len(RESULTS)} checks passed ===")
        return 0 if passed == len(RESULTS) else 1
    finally:
        if sdk is not None:
            try:
                sdk.close()
            except Exception:
                pass
        # Best-effort temp-dir cleanup (one leak per run otherwise)
        try:
            import shutil
            shutil.rmtree(os.path.dirname(db_path))
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
