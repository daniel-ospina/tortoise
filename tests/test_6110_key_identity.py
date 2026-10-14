"""#6110 — ``create_point(key=K, dedup=True)`` treats ``key`` as an IDENTITY.

BEFORE: a differing content under an existing key silently minted a SECOND
point carrying the same key (measured on the live graph 2026-09-28: 22
duplicate points across 21 keys, one key split three ways). ``dedup=True`` DID
fold byte-identical content, but a re-run whose content was recomputed slightly
differently — the exact path a timed-out write forces a caller down — produced a
sibling instead.

AFTER: when a ``key`` is supplied with ``dedup=True`` it is an identity
(scoped per-``pointKind``, mirroring the content identity):
  * a re-run under the same key FOLDS onto the point already holding it,
    whatever the content;
  * a pre-existing key collision (a graph written before the fix) is WARNED
    about and folded onto, never silently duplicated further;
  * a content-dedup hit never silently REASSIGNS an existing point's key;
  * the ingest dedup counter shares the same resolution, so a key-bearing item
    cannot report "created" for a fold.

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_6110_key_identity.py -q
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.exceptions import BundleValidationError
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with a temp embedded database. Closed after the test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_key_id_test_"),
                           "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _points_with_key(sdk, key: str, kind: str | None = None) -> list[str]:
    """Every Point id holding ``key`` (optionally scoped by kind), via raw
    Cypher — the ground truth, independent of the SDK's own resolution."""
    if kind is None:
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point {key:$key}) RETURN n.id",
            params={"key": key},
        ).result_set
    else:
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point {key:$key, pointKind:$kind}) RETURN n.id",
            params={"key": key, "kind": kind},
        ).result_set
    return [r[0] for r in rows]


class TestKeyIsAnIdentity:
    """The #6110 core: one key (per kind) is ONE point."""

    def test_same_key_different_content_yields_one_point(self, sdk):
        """The filed reproduction — must be ONE point, not two."""
        k = "PROBE-key-identity"
        a = sdk.create_point("hypothesis", "AAAA", key=k, dedup=True)
        b = sdk.create_point("hypothesis", "BBBB", key=k, dedup=True)
        assert a["id"] == b["id"], (
            "#6110: a differing content under an existing key minted a sibling"
        )
        assert _points_with_key(sdk, k, "hypothesis") == [a["id"]]
        # Content dedup must not have been applied — the key hit is the
        # identity, so the FIRST writer's content is the one retained.
        assert b["content"] == "AAAA"

    def test_key_collision_with_differing_content_is_audible(self, sdk, caplog):
        """(b) of the fix: the fold is never silent — the caller is told the
        incoming content was not stored."""
        k = "#6110 audible collision"
        sdk.create_point("hypothesis", "FIRST", key=k, dedup=True)
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            sdk.create_point("hypothesis", "SECOND", key=k, dedup=True)
        assert any("DIFFERENT content" in r.getMessage()
                   for r in caplog.records), [r.getMessage() for r in caplog.records]

    def test_same_key_same_content_stays_quiet(self, sdk, caplog):
        """The healthy idempotent re-run must not warn — a warning on the
        normal path is how a warning stops being read."""
        k = "#6110 quiet idempotent rerun"
        sdk.create_point("hypothesis", "SAME", key=k, dedup=True)
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            sdk.create_point("hypothesis", "SAME", key=k, dedup=True)
        assert not [r for r in caplog.records
                    if "DIFFERENT content" in r.getMessage()]

    def test_three_writes_one_key_one_point(self, sdk):
        """A crash-resume/retry loop under one key stays exactly-once."""
        k = "#6110 retry loop"
        ids = {sdk.create_point("hypothesis", c, key=k, dedup=True)["id"]
               for c in ("A", "B", "C")}
        assert len(ids) == 1
        assert len(_points_with_key(sdk, k, "hypothesis")) == 1

    def test_identical_content_retry_is_idempotent(self, sdk):
        """The timeout-retry shape: N byte-identical writes → one point."""
        k = "#6110 byte-identical retry"
        ids = [sdk.create_point("hypothesis", "SAME", key=k, dedup=True)["id"]
               for _ in range(4)]
        assert len(set(ids)) == 1
        assert len(_points_with_key(sdk, k, "hypothesis")) == 1

    def test_query_by_key_returns_the_single_point(self, sdk):
        """The documented lookup idiom resolves the one point for the key."""
        k = "#6110 query by key"
        sdk.create_point("hypothesis", "ONE", key=k, dedup=True)
        sdk.create_point("hypothesis", "TWO", key=k, dedup=True)
        hits = sdk.query(kind="hypothesis", key=k)
        assert [h["id"] for h in hits] == _points_with_key(sdk, k, "hypothesis")
        assert len(hits) == 1

    def test_key_is_scoped_per_kind(self, sdk):
        """#6110 asks the fix to PIN the scope: key is unique PER-KIND (the
        same scope as the content identity), so the same string may name a
        distinct point in a different kind."""
        k = "#6110 per-kind scope"
        h = sdk.create_point("hypothesis", "H", key=k, dedup=True)
        d = sdk.create_point("decision", "D", key=k, dedup=True)
        assert h["id"] != d["id"]
        assert len(_points_with_key(sdk, k)) == 2
        assert len(_points_with_key(sdk, k, "hypothesis")) == 1
        assert len(_points_with_key(sdk, k, "decision")) == 1

    def test_dedup_false_still_mints_a_second_point(self, sdk):
        """The key identity is a DEDUP rule, not a unique constraint: an
        explicit dedup=False still creates (the documented escape hatch)."""
        k = "#6110 explicit bypass"
        a = sdk.create_point("hypothesis", "A", key=k, dedup=False)
        b = sdk.create_point("hypothesis", "B", key=k, dedup=False)
        assert a["id"] != b["id"]
        assert len(_points_with_key(sdk, k, "hypothesis")) == 2

    def test_create_or_update_point_inherits_key_identity(self, sdk):
        """`create_or_update_point` delegates with dedup=True, so it inherits
        the key identity rather than minting a sibling under one key."""
        k = "#6110 create_or_update"
        a = sdk.create_or_update_point("hypothesis", "X", key=k)
        b = sdk.create_or_update_point("hypothesis", "Y", key=k)
        assert a["id"] == b["id"]
        assert len(_points_with_key(sdk, k, "hypothesis")) == 1


class TestKeyIsNeverSilentlyReassigned:
    """The same-seam sibling defect measured while fixing #6110: a
    content-dedup hit used to overwrite the matched point's key with the
    caller's — silently reassigning an identity the first caller owns."""

    def test_same_content_different_key_keeps_the_first_key(self, sdk):
        k1, k2 = "#6110-owner", "#6110-thief"
        a = sdk.create_point("statement", "SHARED CONTENT", key=k1, dedup=True)
        b = sdk.create_point("statement", "SHARED CONTENT", key=k2, dedup=True)
        assert a["id"] == b["id"], "content dedup still folds"
        assert b["key"] == k1, (
            "#6110: a content-dedup hit overwrote the existing key"
        )
        assert _points_with_key(sdk, k2, "statement") == []

    def test_key_reassignment_is_audible(self, sdk, caplog):
        k1, k2 = "#6110-keep", "#6110-drop"
        sdk.create_point("statement", "SAME", key=k1, dedup=True)
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            sdk.create_point("statement", "SAME", key=k2, dedup=True)
        assert any("not applied" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records]

    def test_content_hit_on_unkeyed_point_adopts_the_key(self, sdk):
        """Adoption is an ADD, not an overwrite — an unkeyed point gains the
        supplied key (so the caller's handle exists)."""
        k = "#6110 adopted"
        p = sdk.create_point("statement", "ADOPT ME", dedup=True)
        assert not p.get("key")
        p2 = sdk.create_point("statement", "ADOPT ME", key=k, dedup=True)
        assert p2["id"] == p["id"]
        assert p2["key"] == k
        assert _points_with_key(sdk, k, "statement") == [p["id"]]


class TestPreexistingDuplicateKey:
    """A graph written before the fix can already hold several points under
    one key; the write path must surface that, not fold silently."""

    def test_preexisting_duplicate_key_is_warned_and_folds(self, sdk, caplog):
        k = "#6110 preexisting dup"
        p1 = sdk.create_point("hypothesis", "ONE", key=k, dedup=False)
        p2 = sdk.create_point("hypothesis", "TWO", key=k, dedup=False)
        assert len(_points_with_key(sdk, k, "hypothesis")) == 2
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            p3 = sdk.create_point("hypothesis", "THREE", key=k, dedup=True)
        assert any("already share key" in r.getMessage()
                   for r in caplog.records), [r.getMessage() for r in caplog.records]
        # No THIRD point minted — the write folded onto one of the existing two.
        assert p3["id"] in {p1["id"], p2["id"]}
        assert len(_points_with_key(sdk, k, "hypothesis")) == 2

    def test_no_warning_when_the_key_is_clean(self, sdk, caplog):
        """The normal idempotent re-run must stay quiet — a warning on the
        healthy path is how a warning stops being read."""
        k = "#6110 clean key"
        sdk.create_point("hypothesis", "A", key=k, dedup=True)
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            sdk.create_point("hypothesis", "B", key=k, dedup=True)
        assert not [r for r in caplog.records if "share key" in r.getMessage()]


class TestBlankKeyIsAbsent:
    """A blank/None key is not an identity — it falls back to the historic
    content-only path (which must be unchanged)."""

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_key_uses_content_dedup(self, sdk, blank):
        a = sdk.create_point("statement", "BLANK KEY CONTENT", key=blank, dedup=True)
        b = sdk.create_point("statement", "BLANK KEY CONTENT", key=blank, dedup=True)
        assert a["id"] == b["id"]

    def test_keyless_content_dedup_unchanged(self, sdk):
        """The pre-existing contract (#49/#80) is untouched: without a key,
        dedup is content_hash + pointKind."""
        a = sdk.create_point("statement", "NO KEY", dedup=True)
        b = sdk.create_point("statement", "NO KEY", dedup=True)
        assert a["id"] == b["id"]
        # and a DIFFERENT kind does not suppress
        c = sdk.create_point("hypothesis", "NO KEY", dedup=True)
        assert c["id"] != a["id"]


class TestIngestCounterParity:
    """`ingest_bundle`'s dedup counter shares the writer's resolution, so a
    key-bearing item cannot report "created" for a fold (#2892 invariant)."""

    def test_ingest_key_bearing_item_dedups_against_a_key_hit(self, sdk):
        k = "#6110 ingest parity"
        first = sdk.create_point("statement", "ORIGINAL", key=k, dedup=True)
        # DIFFERENT content, same key: the writer folds by key, so the counter
        # must count a duplicate, not a creation.
        res = sdk.ingest({"points": [
            {"ref": "p1", "kind": "statement", "content": "RESTATED", "key": k},
        ]})
        assert res["created"]["points"] == 0, (
            "counter reported a creation the writer did not perform"
        )
        assert res["deduped"]["points"] == 1
        assert res["ids"]["points"] == [first["id"]]
        assert len(_points_with_key(sdk, k, "statement")) == 1

    def test_ingest_nested_props_key_parity(self, sdk):
        """Same parity through the nested `props` form (flattened by
        `_coerce_props` on the writer, mirrored by the counter)."""
        k = "#6110 ingest nested parity"
        first = sdk.create_point("statement", "ORIGINAL", key=k, dedup=True)
        res = sdk.ingest({"points": [
            {"ref": "p1", "kind": "statement", "content": "RESTATED",
             "props": {"key": k}},
        ]})
        assert res["created"]["points"] == 0
        assert res["deduped"]["points"] == 1
        assert res["ids"]["points"] == [first["id"]]

    def test_top_level_none_key_shadows_nested_props_key(self, sdk):
        """#8400 P2: ``_coerce_props`` gives the top-level ``key`` precedence
        by PRESENCE, not truthiness — a top-level ``key: None`` SHADOWS
        ``props["key"]``, so the writer mints a new point. The counter must
        not consult the shadowed nested key and report a fold for a create."""
        k = "#8400 shadowed nested key"
        first = sdk.create_point("statement", "ORIGINAL", key=k, dedup=True)["id"]
        res = sdk.ingest({"points": [
            {"ref": "p1", "kind": "statement", "content": "RESTATED",
             "key": None, "props": {"key": k}},
        ]})
        assert res["created"]["points"] == 1, (
            "the shadowed nested key made the counter report a fold (#8400)"
        )
        assert res["deduped"]["points"] == 0
        assert res["ids"]["points"] != [first]

    def test_ingest_keyless_content_dedup_unchanged(self, sdk):
        """Without a key the counter is exactly the old content path."""
        first = sdk.ingest({"points": [
            {"ref": "p1", "kind": "statement", "content": "SAME"},
        ]})
        second = sdk.ingest({"points": [
            {"ref": "p1", "kind": "statement", "content": "SAME"},
        ]})
        assert first["created"]["points"] == 1
        assert second["created"]["points"] == 0
        assert second["deduped"]["points"] == 1


class TestPhase1GuardSeesTheKeyIdentity:
    """#8400 P1: the Phase-1 bundle-local guard must resolve a key-bearing ref
    with the SAME key-first order the writer uses. Otherwise a ref whose key is
    held by a TERMINAL point slips past Phase-1 and becomes a Phase-2 raise on
    an already partially-mutated graph."""

    def test_key_held_by_terminal_point_is_a_phase1_violation(self, sdk):
        k = "#8400 terminal key holder"
        terminal = sdk.create_point("statement", "ORIGINAL", key=k,
                                    dedup=True)["id"]
        sdk.retract_point(terminal)
        other = sdk.create_point("statement", "LIVE")["id"]
        before = sdk.get_point(terminal)

        with pytest.raises(BundleValidationError, match="dedup hit"):
            sdk.ingest({
                "points": [{"ref": "p1", "kind": "statement",
                            "content": "CHANGED", "key": k}],
                "connections": [{"from": "p1", "to": other,
                                 "operator": "IMPL"}],
            })

        after = sdk.get_point(terminal)
        assert after.get("batch_id") is None, (
            "Phase-1 must reject BEFORE any write — the terminal point was "
            "mutated by the Phase-2 path (#8400)"
        )
        assert after.get("updatedAt") == before.get("updatedAt")

    def test_key_held_by_live_point_folds_and_wires(self, sdk):
        """The guard must NOT over-reject: a key holder that is still live is
        a legal dedup hit — the writer folds onto it and the edge is wired."""
        k = "#8400 live key holder"
        holder = sdk.create_point("statement", "ORIGINAL", key=k,
                                  dedup=True)["id"]
        other = sdk.create_point("statement", "OTHER")["id"]
        res = sdk.ingest({
            "points": [{"ref": "p1", "kind": "statement",
                        "content": "CHANGED", "key": k}],
            "connections": [{"from": "p1", "to": other,
                             "operator": "IMPL"}],
        })
        assert res["deduped"]["points"] == 1
        assert res["created"]["points"] == 0
        assert res["ids"]["points"] == [holder]


class TestKeyLookupIsIndexBacked:
    """#8400 review P2: the key leg resolves FIRST on every key-bearing write,
    so an unindexed label scan there makes the keyed path ~40x slower than the
    index-backed content-hash lookup it precedes (measured on embedded
    FalkorDB at 60k Points: 8.11 ms/op without the index, 0.20 ms/op with it).
    These pin the ``Point.key`` RANGE index that removes the scan."""

    def test_point_key_has_a_range_index(self, sdk):
        """Pre-fix this FAILS: ``key`` was not in ``_POINT_RANGE_INDEX_PROPS``,
        so ``_find_points_by_key``'s predicate had no index to seek."""
        rows = sdk._get_proj().g.query("CALL db.indexes()").result_set
        hits = []
        for row in rows:
            label, fields, types = row[0], row[1], row[2]
            if label != "Point":
                continue
            if isinstance(types, dict):
                kinds = dict(types)
            elif isinstance(types, (list, tuple)):
                kinds = {f: [t] for f, t in zip(fields, types)}  # noqa: B905
            else:
                kinds = {f: [types] for f in fields}
            if "key" in list(fields) and "RANGE" in kinds.get("key", []):
                hits.append(row)
        assert hits, f"Point.key is not RANGE-indexed: {rows}"

    def test_the_key_predicate_plans_as_an_index_seek(self, sdk):
        """Pre-fix this FAILS with ``Node By Label Scan``.

        The real ``_find_points_by_key`` query also carries ``n.pointKind``,
        which is itself indexed — so the plan string reads ``Node By Index
        Scan`` with OR without a ``key`` index and cannot discriminate.
        Dropping the ``pointKind`` conjunct isolates the key leg; the
        non-operator predicate is kept so the shape is the shared
        ``_dedup_match_clauses``. (The real query's measured speed-up — 8.11
        -> 0.20 ms/op at 60k — is what this index buys; this pins that the
        index is actually PLANNED AGAINST.)
        """
        plan = str(sdk._get_proj().g.explain(
            "MATCH (n:Point {key:'#8400-plan-probe'}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "AND (n.op_type IS NULL OR n.is_operator = false) RETURN n.id"))
        assert "Node By Index Scan" in plan, plan


def test_key_is_declared_for_point_replay():
    """#8400 review observation: ``key`` rides the PointAdded snapshot and is
    restored through the open-set passthrough, so it must be DECLARED —
    otherwise the shared live+replay writer logs a false `"Point prop %r is
    not declared"` drift warning for every keyed point on every rebuild.
    Declaration is warning-suppression only (the passthrough persists the prop
    either way), so this pins the declaration, not the persistence."""
    from tortoise.projection.entities import _EntityHandlers
    assert "key" in _EntityHandlers._POINT_DECLARED_PROPS
