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

import ast
import inspect
import logging
import os
import shutil
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.exceptions import BundleValidationError
from tortoise.sdk import (
    _POINT_ITEM_MINT_STATUS_DEPTH,
    TortoiseSDK,
    _content_candidate_resolution,
    _content_hash,
    _item_dedup_key,
    _item_effective_status,
    _item_stored_key,
    _item_stored_point_kind,
)


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

    def test_the_key_refusal_does_not_offer_a_supersede_as_the_remedy(
            self, sdk):
        """#9819 review P2 (round 3), key-leg half: the KEY refusal carried the
        same dead clause (``Supersede-transfer the key first``, and
        ``docs/INGEST_CONTRACT.md`` row 7's ``or a supersede-transfer that moves
        the key first``).

        No Point ``key`` transfer exists on that door: ``supersede_point``
        transfers edges and stamps terminal status/validity and never writes
        ``key``, and ``create_point`` explicitly refuses to reassign a key on a
        dedup hit (#6110). So the retired holder keeps the key and the key-first
        fold resolves it again — superseding is not an escape.
        """
        k = "#9819-terminal-key-retired"
        terminal = sdk.create_point("statement", "KEY-ORIGINAL", key=k,
                                    dedup=True)["id"]
        successor = sdk.create_point("statement", "KEY-SUCCESSOR")["id"]
        sdk.supersede_point(terminal, successor)
        assert sdk.get_point(terminal).get("key") == k, (
            "supersede_point must not move the key off the retired row")
        before = sdk.get_point(terminal)

        with pytest.raises(BundleValidationError) as excinfo:
            sdk.ingest({"points": [{"ref": "p1", "kind": "statement",
                                     "content": "KEY-CHANGED", "key": k}]})

        message = str(excinfo.value)
        assert "dedup hit" in message
        assert "supersede-transfer" not in message.lower(), (
            "the key refusal still offers a supersede-transfer as the remedy, "
            "but no Point key transfer exists (#9819 review P2)")
        assert "FRESH key" in message, (
            "the refusal must still name the remedy that DOES work")

        after = sdk.get_point(terminal)
        assert after.get("batch_id") is None
        assert after.get("updatedAt") == before.get("updatedAt")

    def test_key_held_by_terminal_point_is_a_violation_with_no_connection(self, sdk):
        """#8400/#9173: step 3 writes EVERY `points` item, so the fold onto a
        terminal key holder happens with NO connection in the bundle.

        This is the arm that keeps the refusal where it belongs. #9173 scoped
        every surviving instance inside a per-CONNECTION, `route == "direct"`
        leg — which is dead for any `points` ref by construction — so an
        assertion that only ever installs a connection (the sibling above)
        cannot tell the unconditional guard from the dead one.
        """
        k = "#8400 terminal key holder, no connection"
        terminal = sdk.create_point("statement", "ORIGINAL-NOCONN", key=k,
                                    dedup=True)["id"]
        sdk.retract_point(terminal)
        before = sdk.get_point(terminal)

        with pytest.raises(BundleValidationError, match="dedup hit"):
            sdk.ingest({"points": [{"ref": "p1", "kind": "statement",
                                     "content": "CHANGED-NOCONN", "key": k}]})

        after = sdk.get_point(terminal)
        assert after.get("batch_id") is None, (
            "the fold stamped the retracted point with no connection in the "
            "bundle — the guard is still connection-scoped (#8400)"
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

    def test_intra_bundle_key_fold_onto_a_self_minted_terminal_point(self, sdk):
        """#8400 review P2: the graph read alone cannot see a terminal point
        the SAME bundle is about to mint.

        Phase 1 runs before any item is written, so
        ``{"points": [{"key": K, "status": "retracted"}, {"key": K}]}``
        used to pass validation (the graph holds no ``K``), then step 3 minted
        ``points[0]`` terminal and folded ``points[1]`` onto it —
        ``_stamp_batch_id`` has no terminal exclusion — stamping ``batch_id``
        on a node born terminal. That is the exact mutation the graph-held arm
        above stops, one statement inside the bundle.

        Discriminating: on the pre-fix tree this bundle is ACCEPTED and both
        points exist, so the ``pytest.raises`` fails on the missing raise.
        """
        k = "#8400 intra-bundle terminal key"
        with pytest.raises(BundleValidationError, match="FRESH key"):
            sdk.ingest(
                {"points": [
                    {"ref": "p1", "kind": "statement", "content": "A",
                     "key": k, "status": "retracted"},
                    {"ref": "p2", "kind": "statement", "content": "B",
                     "key": k},
                ]},
                promotion_policy="auto",
            )
        assert _points_with_key(sdk, k) == [], (
            "Phase-1 must reject BEFORE any write — the bundle minted points "
            "and folded onto the terminal one it had just created (#8400)"
        )

    def test_intra_bundle_guard_does_not_over_reject_a_distinct_key(self, sdk):
        """The bundle-order simulation refuses ONE shape: a later item reusing
        a key an earlier item mints terminal. A terminal item followed by an
        item under a DIFFERENT key is the writer's legal path and must commit.

        Without this arm a ``return`` on "this bundle declares any terminal
        status" would still pass the test above.
        """
        k1 = "#8400 intra-bundle terminal key, distinct 1"
        k2 = "#8400 intra-bundle terminal key, distinct 2"
        res = sdk.ingest(
            {"points": [
                {"ref": "p1", "kind": "statement", "content": "A",
                 "key": k1, "status": "retracted"},
                {"ref": "p2", "kind": "statement", "content": "B",
                 "key": k2},
            ]},
            promotion_policy="auto",
        )
        assert res["created"]["points"] == 2
        assert len(_points_with_key(sdk, k1)) == 1
        assert len(_points_with_key(sdk, k2)) == 1
        assert sdk.get_point(res["ids"]["points"][0]).get("status") == "retracted"


class TestPhase1GuardSeesTheContentIdentity:
    """#9819: the Phase-1 ingest guard must resolve the CONTENT identity with
    the SAME unconditional order the writer uses. ``create_point``'s dedup is
    key-first-then-content, so an item with no key (or a key MISS) folds onto a
    point that matches by CONTENT — on every route, and whether or not any
    connection references it. Before #9819 that fold stamped ``batch_id`` on a
    retracted/superseded point (and moved ``updatedAt`` when the item carried
    props), leaving ``status`` terminal — the #8400 "terminal point mutated"
    hazard, reached through the content half the #9818 key guard did not cover.
    """

    def test_content_matching_a_terminal_point_is_a_phase1_violation(self, sdk):
        terminal = sdk.create_point("statement", "ORIGINAL-CONTENT")["id"]
        sdk.retract_point(terminal)
        before = sdk.get_point(terminal)

        with pytest.raises(BundleValidationError, match="dedup hit"):
            sdk.ingest({"points": [{"ref": "p1", "kind": "statement",
                                     "content": "ORIGINAL-CONTENT"}]})

        after = sdk.get_point(terminal)
        assert after.get("batch_id") is None, (
            "the content fold stamped the retracted point (#9819)")
        assert after.get("updatedAt") == before.get("updatedAt")

    def test_content_matching_a_terminal_point_with_a_fresh_key_is_a_violation(
            self, sdk):
        """A fresh key does NOT avoid the content fold: the writer's key-first
        resolution MISSES the fresh key and falls through to content (#6110),
        so the fold still lands on the terminal point."""
        terminal = sdk.create_point("statement", "ORIGINAL-FRESHKEY")["id"]
        sdk.retract_point(terminal)
        before = sdk.get_point(terminal)

        with pytest.raises(BundleValidationError, match="dedup hit"):
            sdk.ingest({"points": [{"ref": "p1", "kind": "statement",
                                     "content": "ORIGINAL-FRESHKEY",
                                     "key": "brand-new-key-9819"}]})

        after = sdk.get_point(terminal)
        assert after.get("batch_id") is None
        assert after.get("updatedAt") == before.get("updatedAt")

    def test_content_matching_a_live_point_folds(self, sdk):
        """The guard must NOT over-reject: content matching a LIVE point is a
        legal dedup hit — the writer folds onto it and the bundle commits."""
        live = sdk.create_point("statement", "LIVE-CONTENT-9819")["id"]
        res = sdk.ingest({"points": [{"ref": "p1", "kind": "statement",
                                       "content": "LIVE-CONTENT-9819"}]})
        assert res["deduped"]["points"] == 1
        assert res["created"]["points"] == 0
        assert res["ids"]["points"] == [live]

    def test_intra_bundle_content_fold_onto_a_self_minted_terminal_point(
            self, sdk):
        """The graph read alone cannot see a terminal point the SAME bundle is
        about to mint by CONTENT (the sibling of the key-order arm). Bundle
        order: item 0 mints content "A" terminal, item 1 reuses content "A"
        and would content-fold onto it — ``_stamp_batch_id`` has no terminal
        exclusion. Refused by ``minted_terminal_content`` (bundle-order, not a
        graph read)."""
        with pytest.raises(BundleValidationError, match="reuses content"):
            sdk.ingest(
                {"points": [
                    {"ref": "p1", "kind": "statement", "content": "A",
                     "status": "retracted"},
                    {"ref": "p2", "kind": "statement", "content": "A"},
                ]},
                promotion_policy="auto",
            )
        assert sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content = 'A' RETURN n.id"
        ).result_set == [], (
            "Phase-1 must reject BEFORE any write — the bundle minted a point "
            "and folded onto the terminal one it had just created (#9819)")

    def test_an_item_whose_KEY_folds_mints_nothing_on_the_content_axis(
            self, sdk):
        """#9819 review P1: the bundle-order records must describe the WRITE, and
        the writer resolves the KEY **first**.

        Bundle order: points[0] mints key K live with content "A"; points[1]
        re-uses K (legal — the key leg refuses only a key minted TERMINAL) and
        carries fresh content "B", but ``create_point`` resolves K BEFORE
        content, so points[1] FOLDS onto points[0] by key and never writes
        "B". The simulation's ``minted_content`` record for points[1] was
        therefore a phantom, and that phantom then suppressed the terminal
        record of points[2] — the item that really mints "B" — so points[3]
        content-folded onto points[2]'s RETRACTED node in Phase 2, with Phase 1
        certifying the bundle. Measured on the pre-fix head: the bundle
        COMMITTED (created=2, deduped=2) and points[2]'s node came back with
        ``batch_id`` stamped and ``confidence`` written, status still
        ``retracted`` — the exact #8400/#9819 mutation, one statement inside the
        bundle.

        Discriminating: pre-fix no ``BundleValidationError`` is raised (the
        bundle commits and mutates), so the ``pytest.raises`` fails on the
        missing raise and the zero-write assertion fails too.
        """
        k = "#9819 key-first phantom mint"
        with pytest.raises(BundleValidationError, match="reuses content"):
            sdk.ingest(
                {"points": [
                    {"ref": "p0", "kind": "statement", "content": "PH-A",
                     "key": k},
                    {"ref": "p1", "kind": "statement", "content": "PH-B",
                     "key": k},
                    {"ref": "p2", "kind": "statement", "content": "PH-B",
                     "status": "retracted"},
                    {"ref": "p3", "kind": "statement", "content": "PH-B",
                     "props": {"confidence": 0.9}},
                ]},
                promotion_policy="auto",
            )
        assert sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content IN ['PH-A','PH-B'] "
            "OR n.key = $k RETURN n.id",
            params={"k": k},
        ).result_set == [], (
            "Phase-1 must reject BEFORE any write — the phantom content mint "
            "certified a fold onto a terminal node this bundle minted (#9819)"
        )

    def test_a_key_fold_declaring_a_terminal_status_is_not_a_terminal_mint(
            self, sdk):
        """…and the MIRROR, from the same root cause — an over-reject.

        points[1] re-uses items[0]'s key and declares ``retracted``, but
        ``create_point`` pops the declared status BEFORE its dedup branch
        (#1905) and the key hits, so it mints NOTHING terminal: points[0]'s node
        stays ``live``. Recording ``minted_terminal`` for it refused points[2]
        (which also key-folds) with *"reuses key K, which points[1] in THIS
        bundle mints with status 'retracted'"* — a statement that is false about
        the write, on a bundle ``origin/main`` commits.

        Discriminating: pre-fix this raises ``BundleValidationError`` matching
        "reuses key" and nothing is written; post-fix it commits with one live
        carrier of the key.
        """
        k = "#9819 key-first phantom terminal"
        res = sdk.ingest(
            {"points": [
                {"ref": "p0", "kind": "statement", "content": "PT-A",
                 "key": k},
                {"ref": "p1", "kind": "statement", "content": "PT-B",
                 "key": k, "status": "retracted"},
                {"ref": "p2", "kind": "statement", "content": "PT-C",
                 "key": k},
            ]},
            promotion_policy="auto",
        )
        assert res["created"]["points"] == 1, (
            f"the first item mints and both re-uses key-fold onto it (got {res})")
        assert res["deduped"]["points"] == 2
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point {key:$k}) RETURN n.status, n.content, n.id",
            params={"k": k},
        ).result_set
        assert len(rows) == 1, (
            f"one carrier of the key — every item folded onto points[0] (got {rows})")
        assert rows[0][0] == "live", (
            "the key fold pops the declared status, so no terminal mint ever "
            f"happens and points[0]'s node stays live (got {rows})")

    def test_an_item_that_key_folds_in_bundle_does_not_run_the_content_leg(
            self, sdk):
        """#9819 review P2 (OVER-REJECT): the writer resolves the KEY first, so an
        item whose key an EARLIER item of THIS bundle minted folds onto that
        node by key and NEVER reaches its content leg — even when its content
        happens to match a STANDING terminal point it would never touch.

        The graph read behind the committed-holder arm is a snapshot taken
        before ANY item is written, so it cannot see points[0]'s mint: that arm
        is empty, the content leg ran anyway, and the bundle was REFUSED with
        *"content matches terminal point … (dedup hit)"* — plus remediation
        advice ("mint the point OUTSIDE the bundle with DIFFERENT content") that
        is wrong for this shape, since points[1] never writes its content at
        all. ``origin/main`` commits this bundle.

        Discriminating: pre-fix this raises ``BundleValidationError`` matching
        "dedup hit" and nothing is written.
        """
        terminal = sdk.create_point(
            "statement", "KF-TERM", dedup=False)["id"]
        sdk.retract_point(terminal)
        before = sdk.get_point(terminal)
        res = sdk.ingest(
            {"points": [
                {"ref": "p0", "kind": "statement", "content": "KF-FRESH",
                 "key": "KF-K"},
                {"ref": "p1", "kind": "statement", "content": "KF-TERM",
                 "key": "KF-K"},
            ]},
            promotion_policy="auto",
        )
        assert res["created"]["points"] == 1
        assert res["deduped"]["points"] == 1
        assert res["ids"]["points"][1] == res["ids"]["points"][0], (
            "points[1] folds onto points[0] BY KEY — the key is the identity "
            f"(#6110) and its content is never written (got {res['ids']})")
        holders = sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content = 'KF-TERM' RETURN n.id"
        ).result_set
        assert [r[0] for r in holders] == [terminal], (
            "the item's content was never written — the key hit returns before "
            "the content leg, so the ONLY carrier of 'KF-TERM' is the standing "
            f"terminal point (got {holders})")
        after = sdk.get_point(terminal)
        assert after.get("batch_id") is None, (
            "the terminal point this item would never touch must stay "
            "UNSTAMPED (#9819)")
        assert after.get("updatedAt") == before.get("updatedAt")
        assert _points_with_key(sdk, "KF-K") == [res["ids"]["points"][0]], (
            "one carrier of the key — points[1] folded onto it")

    def test_intra_bundle_content_guard_does_not_over_reject(self, sdk):
        """A terminal item followed by an item with DIFFERENT content is the
        writer's legal path and must commit."""
        res = sdk.ingest(
            {"points": [
                {"ref": "p1", "kind": "statement", "content": "A-DISTINCT",
                 "status": "retracted"},
                {"ref": "p2", "kind": "statement", "content": "B-DISTINCT"},
            ]},
            promotion_policy="auto",
        )
        assert res["created"]["points"] == 2
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content IN ['A-DISTINCT','B-DISTINCT'] "
            "RETURN n.status"
        ).result_set
        assert {r[0] for r in rows} == {"retracted", "live"}

    def test_a_live_twin_of_a_terminal_point_is_refused(self, sdk):
        """fix-round-2 P2.3 (the behaviour ``docs/INGEST_CONTRACT.md`` row 7
        now DISCLOSES, and which nothing pinned): when two points share one
        content and only ONE is terminal, the guard hits the TERMINAL one while
        the writer could resolve the LIVE one — so the bundle is refused though
        the fold it predicts would have been harmless.

        Kept fail-closed on purpose: the writer's pick among duplicates carries
        no ``ORDER BY`` (``_find_point_by_content`` returns its first row), so
        WHICH twin a fold lands on is not decidable from the bundle. Pinning it
        here means a later narrowing is a decision, not a silent drift.
        """
        live = sdk.create_point("statement", "TWIN-CONTENT")["id"]
        terminal = sdk.create_point("statement", "TWIN-CONTENT")["id"]
        assert live != terminal
        sdk.retract_point(terminal)
        before_terminal = sdk.get_point(terminal)
        before_live = sdk.get_point(live)

        with pytest.raises(BundleValidationError, match="dedup hit"):
            sdk.ingest({"points": [{"ref": "p1", "kind": "statement",
                                     "content": "TWIN-CONTENT"}]})

        after_terminal = sdk.get_point(terminal)
        assert after_terminal.get("batch_id") is None
        assert after_terminal.get("updatedAt") == before_terminal.get("updatedAt")
        assert sdk.get_point(live).get("batch_id") is None, (
            "Phase-1 must reject before ANY write — neither twin was touched "
            f"(live point was {before_live.get('batch_id')!r})")

    def test_empty_content_matching_a_terminal_point_is_a_phase1_violation(
            self, sdk):
        """#9819 review P1: the content leg must NOT gate on TRUTHINESS.

        ``""`` is CONTENT to the writer, not "absent": ``_check_item_shape``
        admits it (only ``None``/non-str is refused) and ``create_point``'s only
        content boundary is ``isinstance(content, str)`` — so
        ``create_point("statement", "")`` mints a real point with
        ``content_hash = sha256("")``, and once retracted it is a terminal
        point like any other. Reading the item's content with ``and content``
        skipped the graph read AND the ``minted_terminal_content`` record, so
        this bundle FOLDED onto the retracted point and ``_stamp_batch_id``
        rewrote it — the #9819 hazard through a legal bundle item.

        Discriminating: pre-fix the ``pytest.raises`` fails (the ingest is
        ACCEPTED and the assertions below see a stamped ``batch_id``).
        """
        terminal = sdk.create_point("statement", "")["id"]
        sdk.retract_point(terminal)
        before = sdk.get_point(terminal)

        with pytest.raises(BundleValidationError, match="dedup hit"):
            sdk.ingest({"points": [{"ref": "p1", "kind": "statement",
                                     "content": ""}]})

        after = sdk.get_point(terminal)
        assert after.get("batch_id") is None, (
            "the content fold stamped the retracted empty-content point "
            "(#9819 P1 — the leg gated on truthiness)")
        assert after.get("updatedAt") == before.get("updatedAt")

    def test_retiring_the_collided_row_does_not_clear_the_content_identity(
            self, sdk):
        """#9819 review P2 (round 3): the refusal must not name a remediation
        that does not remediate.

        The content-leg message used to offer ``or supersede-transfer it first``
        (and ``docs/INGEST_CONTRACT.md`` row 7 the same). It is dead: the dedup
        predicate (``_dedup_match_clauses``) carries NO status/validity
        conjunct, and ``supersede_point`` transfers EDGES and stamps
        ``status``/``outdated``/``validTo``/``expiredAt``/``decay`` — it never
        rewrites ``content`` or ``content_hash``. So after a supersede the old
        row is still the hash-present holder of that content identity, still
        terminal, and the very next ingest of the same content is refused
        again: the caller follows the advice, gets the identical refusal, and
        is told the same dead remedy.

        Asserted in both directions, so this is a behaviour pin and a message
        pin at once: the re-ingest is STILL refused, the collided row is
        unmoved, and the message no longer advertises ``supersede-transfer``
        while still naming the remedy that does work (DIFFERENT content).
        Discriminating: pre-fix the message assertion fails (the refusal said
        ``or supersede-transfer it first``).
        """
        content = "RETIRED-BUT-STILL-THE-HOLDER-9819"
        terminal = sdk.create_point("statement", content)["id"]
        successor = sdk.create_point("statement",
                                     "SUCCESSOR-OF-THE-RETIRED-9819")["id"]
        sdk.supersede_point(terminal, successor)
        before = sdk.get_point(terminal)

        # The premise of the finding, asserted rather than assumed: the
        # supersede did NOT move the content identity off the retired row.
        assert before.get("status") == "superseded"
        assert before.get("content") == content
        assert before.get("content_hash") == _content_hash(content)

        with pytest.raises(BundleValidationError) as excinfo:
            sdk.ingest({"points": [{"ref": "p1", "kind": "statement",
                                     "content": content}]})

        message = str(excinfo.value)
        assert "dedup hit" in message
        assert "supersede-transfer" not in message, (
            "the refusal still advertises supersede-transfer as the remedy, "
            "but superseding does not clear the collided row's content — the "
            "caller would get this same refusal again (#9819 review P2)")
        assert "DIFFERENT content" in message, (
            "the refusal must still name the remedy that DOES work")

        after = sdk.get_point(terminal)
        assert after.get("batch_id") is None, (
            "Phase-1 must reject before ANY write — the post-supersede fold "
            "stamped the retired point (#9819)")
        assert after.get("updatedAt") == before.get("updatedAt")

    def test_double_wrapped_status_is_not_recorded_as_a_terminal_mint(self, sdk):
        """#9819 review P2: the bundle-order record predicts a MINT, and a mint
        stores the status after ``create_point``'s ONE fold.

        ``create_point`` pops ``status`` BEFORE its dedup branch (a dedup hit
        must never forward the caller's status into ``update_point``, #1905),
        so a status nested TWO ``props`` deep is never stored: measured,
        ``{"props": {"props": {"status": "retracted"}}}`` lands ``status='live'``.
        Reading the recorded status at ``_POINT_ITEM_FLATTEN_DEPTH`` (2) saw the
        unwritten ``retracted`` and refused the NEXT item under the same content
        with "mints with status 'retracted'" — a wrong message on a bundle the
        writer completes.

        Discriminating: pre-fix the bundle is REFUSED with `reuses content`.
        """
        res = sdk.ingest(
            {"points": [
                {"ref": "p1", "kind": "statement", "content": "DW-STATUS",
                 "props": {"props": {"status": "retracted"}}},
                {"ref": "p2", "kind": "statement", "content": "DW-STATUS"},
            ]},
            promotion_policy="auto",
        )
        assert res["created"]["points"] == 1
        assert res["deduped"]["points"] == 1
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content = 'DW-STATUS' RETURN n.status"
        ).result_set
        assert [r[0] for r in rows] == ["live"], (
            "the double-wrapped status was never stored, so nothing minted "
            "terminal (#9819 P2)")

    def test_the_mint_depth_is_the_depth_the_write_door_folds(self, sdk):
        """WRITER PARITY for ``_POINT_ITEM_MINT_STATUS_DEPTH`` (#9819 review
        P1, round 8 — reported as a fail-open, MEASURED as already correct).

        The round-8 finding argued the constant must be 2, on the model that the
        item's ``props`` value reaches ``create_point`` as ``props=`` and that
        one ``_coerce_props`` fold then surfaces ``item["props"]["props"]``.
        The second step is wrong by ONE level: ``**props`` in the signature means
        the ``props=`` kwarg becomes a KEY called ``props`` INSIDE the
        passthrough dict, so the fold surfaces ``item["props"]``'s own keys —
        depth 1 — and the dict one level deeper is stored as a literal ``props``
        property (which FalkorDB cannot write, so it is degraded to null, #7369).

        Asserted as a WRITE, not as arithmetic on the helper: the call below is
        the ingest loop's own shape (``create_point(kind, content, dedup=True,
        **write_item)``), and the node it returns is the writer's answer.

        Discriminating: a lane that "fixes" the constant to 2 to make
        ``test_double_wrapped_status_is_not_recorded_as_a_terminal_mint`` pass
        by another route reds the last assertion here, and reads the door's own
        verdict in the failure message instead of re-deriving the fold model.
        """
        item = {"kind": "statement", "content": "MINT-DEPTH-PIN",
                "props": {"props": {"status": "retracted"}}}
        # The read the round-8 finding proposed — depth 2 DOES see the inner
        # spelling, which is exactly why reading it would predict a terminal
        # mint the writer never performs.
        assert _item_effective_status(item, 2) == "retracted"
        # The door's own answer, on the ingest loop's own call shape.
        minted = sdk.create_point(dedup=True, **item)
        assert minted["status"] == "live", (
            "the double-wrapped status is NOT stored: **props puts the item's "
            "props value one level deeper than the item, so one fold reaches "
            f"only item['props']'s keys (got {minted['status']!r})")
        # ONE wrap is one fold deep, so it IS stored — the record must exist.
        assert sdk.create_point(
            "statement", "MINT-DEPTH-PIN-1",
            props={"status": "retracted"})["status"] == "retracted"
        assert _item_effective_status(
            item, _POINT_ITEM_MINT_STATUS_DEPTH) is None, (
            "the guard's mint read must agree with the door: depth "
            f"{_POINT_ITEM_MINT_STATUS_DEPTH} sees no declared status, and the "
            "writer stores none — raising the depth re-opens the round-5 "
            "over-reject (a wrong 'mints with status retracted' refusal on a "
            "bundle the writer completes)")

    def test_the_review_p1_reproducers_are_not_bundle_order_terminal_mints(
            self, sdk):
        """The two bundles a round-9 review named as a fail-open, MEASURED as
        bundles the writer completes — pinned at the INGEST level so the
        question is settled on the caller's own surface, not on ``create_point``.

        The finding's model was that ``create_point`` receives the item's
        ``props`` VALUE as ``props=``, so one ``_coerce_props`` fold would
        surface ``item["props"]["props"]`` (depth 2) and store ``retracted``.
        It does not: ``create_point`` has no ``props`` PARAMETER — it is
        ``**props`` — so passing ``props={...}`` puts that dict under a KEY
        named ``props`` and the single fold surfaces ``item["props"]``'s own
        keys. A double-wrapped spelling therefore stores NOTHING terminal, and
        the depth-1 mint read is the writer-parity answer.

        Both arms of the finding's reproducer are driven here, because the
        claim was that the KEY arm REGRESSED against ``origin/main`` (which
        read this record at depth 2): item 0 mints under key ``K`` with the
        double-wrapped status, item 1 re-states key ``K``. The writer mints
        item 0 LIVE and folds item 1 onto it by key, so Phase 1 must CERTIFY
        this bundle — ``origin/main`` refuses it with a wrong "mints with
        status 'retracted'".

        Discriminating: on ``origin/main`` both ingests raise
        ``BundleValidationError``.
        """
        # ── CONTENT arm: the sibling pin lives in
        # ``test_double_wrapped_status_is_not_recorded_as_a_terminal_mint``;
        # asserted again here so the pair reads as one settling of one report.
        content_res = sdk.ingest(
            {"points": [
                {"ref": "p1", "kind": "statement", "content": "P1-DW-C",
                 "props": {"props": {"status": "retracted"}}},
                {"ref": "p2", "kind": "statement", "content": "P1-DW-C"},
            ]},
            promotion_policy="auto",
        )
        assert content_res["created"]["points"] == 1
        assert content_res["deduped"]["points"] == 1
        assert len(set(content_res["ids"]["points"])) == 1
        # ── KEY arm: the regression the finding claimed against origin/main.
        key_res = sdk.ingest(
            {"points": [
                {"ref": "p1", "kind": "statement", "content": "P1-DW-K",
                 "key": "P1-DW-KEY",
                 "props": {"props": {"status": "retracted"}}},
                {"ref": "p2", "kind": "statement", "content": "P1-DW-K2",
                 "key": "P1-DW-KEY"},
            ]},
            promotion_policy="auto",
        )
        assert key_res["created"]["points"] == 1
        assert key_res["deduped"]["points"] == 1, (
            "item 1 re-states a key item 0 MINTED LIVE, so it folds by key")
        assert len(set(key_res["ids"]["points"])) == 1
        # The premise, read off the graph rather than argued: nothing in either
        # bundle was stored terminal, which is why neither refusal is owed.
        # Two rows, not three: item 1 of the KEY arm re-states item 0's key, so
        # it FOLDS (keeping item 0's content — the key is the identity, #6110)
        # and mints nothing.
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content IN $c "
            "RETURN n.content, n.status, n.key",
            params={"c": ["P1-DW-C", "P1-DW-K", "P1-DW-K2"]},
        ).result_set
        assert sorted(r[0] for r in rows) == ["P1-DW-C", "P1-DW-K"], (
            f"the two minted points and nothing else: {rows}")
        assert [r[1] for r in rows] == ["live"] * 2, (
            "the double-wrapped status is stored by NO door here — the mint "
            f"record must therefore not call either item terminal: {rows}")

    def test_the_stored_kind_read_is_at_the_same_writer_depth(self, sdk):
        """The ``pointKind`` arm of the same constant (``_item_stored_point_kind``
        uses ``_POINT_ITEM_MINT_STATUS_DEPTH``): it must be read at the SAME
        depth as the status, because both answer "what does the MINT store".

        A double-wrapped ``pointKind`` is not stored either — the node keeps the
        ITEM's positional kind — so keying the minted-node record by the inner
        spelling would attribute the mint to a ``(kind, content)`` identity no
        dedup query can resolve (the fail-open direction
        ``TestTheMintRecordIsKeyedByTheSTOREDKind`` exists to prevent).

        Discriminating: at depth 2 the helper answers ``'decision'`` for this
        item while the door stores ``'statement'``.
        """
        item = {"kind": "statement", "content": "MINT-KIND-DEPTH-PIN",
                "props": {"props": {"pointKind": "decision"}}}
        stored = sdk.create_point(dedup=True, **item)
        assert stored["pointKind"] == "statement", (
            f"the double-wrapped kind is not stored (got {stored['pointKind']!r})")
        assert _item_stored_point_kind(item) == stored["pointKind"], (
            "the record's kind must be the kind the door STORED")
        # …and the single-wrapped spelling, one fold deep, really does win.
        single = sdk.create_point("statement", "MINT-KIND-DEPTH-PIN-1",
                                  props={"pointKind": "decision"})
        assert single["pointKind"] == "decision"
        assert _item_stored_point_kind(
            {"kind": "statement", "props": {"pointKind": "decision"}}
        ) == "decision"

    def test_a_real_one_fold_terminal_mint_is_still_recorded(self, sdk):
        """NEGATIVE CONTROL for the test above: reading the recorded status at
        the MINT depth (1) must not have dropped the record for a status the
        writer really stores. A single-wrapped (`props: {"status": ...}`) or a
        top-level terminal declaration IS one fold, so the next same-content
        item is still refused — without this pair, `test_double_wrapped...`
        would also pass if the record had been deleted outright."""
        for j, extra in enumerate(({"props": {"status": "retracted"}},
                                   {"status": "retracted"})):
            content = f"ONE-FOLD-{j}"
            with pytest.raises(BundleValidationError, match="mints with status"):
                sdk.ingest(
                    {"points": [
                        {"ref": "p1", "kind": "statement",
                         "content": content, **extra},
                        {"ref": "p2", "kind": "statement", "content": content},
                    ]},
                    promotion_policy="auto",
                )
            assert sdk._get_proj().g.query(
                "MATCH (n:Point) WHERE n.content = $c RETURN n.id",
                params={"c": content},
            ).result_set == [], (
                f"Phase-1 must reject before any write for {extra!r}")

    def test_a_terminal_declaration_after_an_in_bundle_live_mint_is_not_a_mint(
            self, sdk):
        """#9819 review P2 (round 4): ``_mints`` is a GRAPH read, so it cannot
        see a point an EARLIER item of the same bundle mints.

        Bundle order: item 0 mints content "SEQ-LIVE" LIVE; item 1 re-declares
        the same content with ``status='retracted'``; item 2 re-states it once
        more. The writer content-folds items 1 and 2 onto item 0's LIVE node
        (``create_point`` pops the declared status BEFORE its dedup branch, so
        the fold stores nothing terminal), i.e. item 1 MINTS NOTHING — yet the
        record saw it as a terminal mint and refused item 2 with "reuses
        content that points[1] … mints with status 'retracted'", a statement
        that is false about the write. ``origin/main`` commits this bundle.

        Discriminating: pre-fix this raises ``BundleValidationError`` and no
        point is written at all; post-fix it commits (created=1, deduped=2).
        The sibling arm — an earlier item that mints TERMINAL — is still
        refused, pinned by
        ``test_intra_bundle_content_fold_onto_a_self_minted_terminal_point``
        and ``test_a_real_one_fold_terminal_mint_is_still_recorded`` above.
        """
        res = sdk.ingest(
            {"points": [
                {"ref": "p1", "kind": "statement", "content": "SEQ-LIVE"},
                {"ref": "p2", "kind": "statement", "content": "SEQ-LIVE",
                 "status": "retracted"},
                {"ref": "p3", "kind": "statement", "content": "SEQ-LIVE"},
            ]},
            promotion_policy="auto",
        )
        assert res["created"]["points"] == 1
        assert res["deduped"]["points"] == 2
        assert len(set(res["ids"]["points"])) == 1, (
            "both re-statements must fold onto the ONE point the bundle minted")
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content = 'SEQ-LIVE' "
            "RETURN n.status, n.id"
        ).result_set
        assert len(rows) == 1, (
            f"exactly one point — the two later items FOLD, they do not mint "
            f"(got {rows})")
        assert rows[0][0] == "live", (
            "the declared 'retracted' was popped by the dedup fold, so nothing "
            "in this bundle mounts a terminal mint")


class TestAFreeKeyIsNotAKeyThisItemWrites:
    """#9819 review P1 (round 9): the bundle-order KEY record must describe the
    write, and "the key is FREE" is not the same fact as "this item WRITES
    it".

    ``create_point`` resolves the key first, and on a MISS falls through to
    CONTENT — where the fold applies the supplied ``key`` through
    ``update_point`` **only if the resolved point holds no other key**; a point
    that already holds one keeps it and the supplied key is POPPED
    (``props.pop("key", None)``). So an item can leave the key unheld while
    the simulation had already recorded it as ``minted_keys`` — and that record
    is what every later item keys its ``_key_folds_here`` decision off. A
    phantom record therefore suppresses EVERY later record for that key (no key
    mint, no content mint, no ``minted_terminal``/``minted_terminal_content``),
    so a later item's real terminal mint goes unrecorded and a third item
    key-folds onto it with Phase 1 certifying the bundle.

    ⛔ The mirror is why this cannot be fixed by simply dropping the record: a
    content-folding item whose fold target holds NO key DOES write it, so
    treating that key as free makes the NEXT item's real fold look like a mint
    and over-rejects the item after it. Both directions are driven below — the
    fail-open pair and the over-reject controls — and the two in-bundle arms
    (a fold onto a node an EARLIER item minted) are driven too, because that
    node is invisible to the graph read.
    """

    def test_the_three_item_bundle_that_key_folded_onto_a_self_minted_terminal(
            self, sdk):
        """FAIL-OPEN. The reviewer's reproducer, end to end through ``ingest``.

        ``main`` already holds a LIVE point with content ``C0`` under a
        DIFFERENT key. points[0] declares the free key ``K`` with content
        ``C0``: its key misses, its content hits, and the writer keeps the
        existing key and pops ``K`` — so points[0] holds NOTHING. points[1]
        then really MINTS a retracted point under ``K``, and points[2] really
        key-folds onto it. The simulation recorded ``minted_keys[K]`` for
        points[0] anyway, so points[1]'s terminal mint was never recorded and
        points[2] was certified.

        Discriminating: pre-fix no ``BundleValidationError`` is raised — the
        bundle COMMITS and points[1]'s retracted node comes back with
        ``batch_id`` stamped / ``confidence`` written (the #8400/#9819
        mutation, over a node this same bundle minted).
        """
        live_id = sdk.create_point(
            "statement", "FK-C0", key="FK-OTHER")["id"]
        before = sdk.get_point(live_id)
        with pytest.raises(BundleValidationError, match="reuses key"):
            sdk.ingest(
                {"points": [
                    {"ref": "p0", "kind": "statement", "content": "FK-C0",
                     "key": "FK-K"},
                    {"ref": "p1", "kind": "statement", "content": "FK-C1",
                     "key": "FK-K", "status": "retracted"},
                    {"ref": "p2", "kind": "statement", "content": "FK-C2",
                     "key": "FK-K", "props": {"confidence": 0.9}},
                ]},
                promotion_policy="auto",
            )
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content IN ['FK-C1','FK-C2'] "
            "OR n.key = 'FK-K' RETURN n.id"
        ).result_set
        assert rows == [], (
            "Phase-1 must reject BEFORE any write — the phantom key record "
            f"certified a fold onto a terminal node (got {rows})")
        assert sdk.get_point(live_id) == before, (
            "the standing point must be untouched as well")

    def test_the_content_identical_form_of_the_same_bundle_is_refused(self, sdk):
        """FAIL-OPEN, the other identity. Same head bundle, but points[2]
        carries points[1]'s CONTENT instead of its key — so the fold lands on
        the retracted node through ``minted_terminal_content``, which the
        phantom key record had suppressed just the same (``_mints_here`` is
        gated on ``_key_folds_here``).

        Discriminating: pre-fix the bundle COMMITS and the retracted node comes
        back with ``batch_id`` stamped.
        """
        sdk.create_point("statement", "FKC-C0", key="FKC-OTHER")
        with pytest.raises(BundleValidationError, match="reuses content"):
            sdk.ingest(
                {"points": [
                    {"ref": "p0", "kind": "statement", "content": "FKC-C0",
                     "key": "FKC-K"},
                    {"ref": "p1", "kind": "statement", "content": "FKC-C1",
                     "key": "FKC-K", "status": "retracted"},
                    {"ref": "p2", "kind": "statement", "content": "FKC-C1",
                     "props": {"confidence": 0.9}},
                ]},
                promotion_policy="auto",
            )
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content = 'FKC-C1' RETURN n.id"
        ).result_set
        assert rows == [], (
            f"Phase-1 must reject before any write (got {rows})")

    def test_a_content_fold_onto_an_UNKEYED_point_adopts_the_key(self, sdk):
        """OVER-REJECT CONTROL for the fix, and it reds the NAIVE fix.

        points[0]'s key misses while its content hits a LIVE point that holds
        NO key — so the fold WRITES the key onto that point ("a dedup hit on an
        unkeyed point adopts the key", #6110, pinned by
        ``test_content_hit_on_unkeyed_point_adopts_the_key``). Every later item
        under that key then key-folds onto a LIVE point and mints nothing: the
        bundle is legal and ``origin/main`` commits it.

        ⛔ Dropping the record for every content-folding item — the one-line
        "fix" — makes points[1]'s real key fold look like a terminal mint and
        refuses points[2] with *"reuses key … mints with status 'retracted'"*, a
        statement that is false about the write. So this test passes pre-fix and
        FAILS on that shortcut, which is the half of the pair the reviewer's
        "the two arms have to be settled together" warns about.
        """
        holder = sdk.create_point("statement", "UK-C0")["id"]
        res = sdk.ingest(
            {"points": [
                {"ref": "p0", "kind": "statement", "content": "UK-C0",
                 "key": "UK-K"},
                {"ref": "p1", "kind": "statement", "content": "UK-C1",
                 "key": "UK-K", "status": "retracted"},
                {"ref": "p2", "kind": "statement", "content": "UK-C2",
                 "key": "UK-K"},
            ]},
            promotion_policy="auto",
        )
        assert res["created"]["points"] == 0, (
            f"every item folds — nothing mints (got {res})")
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point {key:'UK-K'}) RETURN n.content, n.status, n.id"
        ).result_set
        assert [list(r) for r in rows] == [["UK-C0", "live", holder]], (
            "the ORIGINAL point keeps its content and its live status, and is "
            f"the only carrier of the key (got {rows})")

    def test_an_in_bundle_UNKEYED_mint_hands_the_key_on_the_same_way(self, sdk):
        """OVER-REJECT CONTROL for the IN-BUNDLE arm — the fold target is a
        node an EARLIER item of this bundle minted, which the graph read cannot
        see, so the key it stores has to be carried through the simulation
        (``minted_content_keys``).

        points[0] mints content ``IK-C0`` with NO key; points[1] supplies key
        ``IK-K`` and the same content, so it folds onto that node and the fold
        WRITES the key; points[2] re-stating the key is a plain key fold onto a
        LIVE, in-bundle node — legal. A fix that treats "the fold target is an
        in-bundle mint" as "the key may be dropped" refuses points[3] for a
        terminal mint that never happens.

        Passes pre-fix (the phantom record produced the same accept), so it is
        not discriminating against the head — it is discriminating against the
        incomplete fix, which is what it is here for.
        """
        res = sdk.ingest(
            {"points": [
                {"ref": "p0", "kind": "statement", "content": "IK-C0"},
                {"ref": "p1", "kind": "statement", "content": "IK-C0",
                 "key": "IK-K"},
                {"ref": "p2", "kind": "statement", "content": "IK-C1",
                 "key": "IK-K", "status": "retracted"},
                {"ref": "p3", "kind": "statement", "content": "IK-C2",
                 "key": "IK-K"},
            ]},
            promotion_policy="auto",
        )
        assert res["created"]["points"] == 1, (
            f"only points[0] mints — the rest fold (got {res})")
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point {key:'IK-K'}) RETURN n.content, n.status"
        ).result_set
        assert [list(r) for r in rows] == [["IK-C0", "live"]], (
            "the in-bundle mint is the one carrier, still live (the declared "
            f"'retracted' was popped by the fold) — got {rows}")

    def test_an_in_bundle_mint_holding_ANOTHER_key_does_not_hand_the_key_on(
            self, sdk):
        """FAIL-OPEN, the in-bundle arm: the fold target is a node an EARLIER
        item minted under a DIFFERENT key, so the supplied key is popped and
        the key stays free. points[2] therefore really MINTS a retracted point
        under it, and points[3] really folds onto that.

        Discriminating: pre-fix the bundle COMMITS and points[2]'s retracted
        node comes back with ``batch_id`` stamped.
        """
        with pytest.raises(BundleValidationError, match="reuses key"):
            sdk.ingest(
                {"points": [
                    {"ref": "p0", "kind": "statement", "content": "BK-C0",
                     "key": "BK-FIRST"},
                    {"ref": "p1", "kind": "statement", "content": "BK-C0",
                     "key": "BK-K"},
                    {"ref": "p2", "kind": "statement", "content": "BK-C1",
                     "key": "BK-K", "status": "retracted"},
                    {"ref": "p3", "kind": "statement", "content": "BK-C2",
                     "key": "BK-K", "props": {"confidence": 0.9}},
                ]},
                promotion_policy="auto",
            )
        assert sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content IN ['BK-C0','BK-C1','BK-C2'] "
            "RETURN n.id"
        ).result_set == [], "Phase-1 must reject before any write"

    def test_duplicate_content_with_conflicting_keys_fails_closed(self, sdk):
        """The RESIDUAL, disclosed rather than hidden: when SEVERAL live points
        share one content identity and they DISAGREE about the key, which node
        the writer folds onto is not decidable at Phase-1 time —
        ``_find_point_by_content`` takes ``rows[0]`` of a query with no
        ``ORDER BY``. The answer the guard gives is the fail-closed one ("the
        key may be dropped"), so this bundle is refused for a fold that MAY not
        happen — the same conservative posture, for the same reason, as the
        content leg's "ANY terminal holder" rule.

        What it must never do is the opposite: betting that the key landed is
        the direction that masks a later terminal fold. Pre-fix this bundle
        COMMITS (the phantom record), so the two directions are distinguishable
        right here.
        """
        sdk.create_point("statement", "DUP-C0")
        twin = sdk.create_point("statement", "DUP-C0", dedup=False)["id"]
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.key = 'DUP-OTHER'",
            params={"id": twin})
        with pytest.raises(BundleValidationError, match="reuses key"):
            sdk.ingest(
                {"points": [
                    {"ref": "p0", "kind": "statement", "content": "DUP-C0",
                     "key": "DUP-K"},
                    {"ref": "p1", "kind": "statement", "content": "DUP-C1",
                     "key": "DUP-K", "status": "retracted"},
                    {"ref": "p2", "kind": "statement", "content": "DUP-C2",
                     "key": "DUP-K"},
                ]},
                promotion_policy="auto",
            )
        assert sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content IN ['DUP-C1','DUP-C2'] "
            "RETURN n.id").result_set == [], (
            "Phase-1 must reject before any write")

    def test_a_second_fold_under_the_same_content_sees_the_key_the_first_one_wrote(
            self, sdk):
        """FAIL-OPEN on the SNAPSHOT arm: the fold target is a point already in
        the graph, so `_fold_keys` is a read taken BEFORE any item was written —
        and it says "no key" for an item that will hold one by the time the next
        item runs.

        points[0] folds onto the unkeyed live point ``SG-C0`` and writes
        ``SG-K1`` onto it; points[1] supplies a DIFFERENT key for the same
        content, so the fold keeps ``SG-K1`` and pops ``SG-K2`` — the key stays
        FREE. Judging points[1] by the stale read ("the target has no key, so
        the key lands") recorded it, which suppressed points[2]'s real terminal
        mint and certified points[3]'s fold onto that retracted node.

        Discriminating: pre-fix no ``BundleValidationError`` is raised and the
        bundle COMMITS over a live point (measured), and it also reds a fix that
        reads `_fold_keys` once without tracking the key an in-bundle fold
        writes.
        """
        sdk.create_point("statement", "SG-C0")
        with pytest.raises(BundleValidationError, match="reuses key"):
            sdk.ingest(
                {"points": [
                    {"ref": "p0", "kind": "statement", "content": "SG-C0",
                     "key": "SG-K1"},
                    {"ref": "p1", "kind": "statement", "content": "SG-C0",
                     "key": "SG-K2"},
                    {"ref": "p2", "kind": "statement", "content": "SG-C1",
                     "key": "SG-K2", "status": "retracted"},
                    {"ref": "p3", "kind": "statement", "content": "SG-C2",
                     "key": "SG-K2", "props": {"confidence": 0.9}},
                ]},
                promotion_policy="auto",
            )
        assert sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content IN ['SG-C1','SG-C2'] "
            "OR n.key IN ['SG-K1','SG-K2'] RETURN n.id"
        ).result_set == [], (
            "Phase-1 must reject before any write — not even points[0]'s key "
            "may be applied")

    def test_the_resolver_hands_back_the_fold_targets_stored_keys(self, sdk):
        """UNIT: ``_content_candidate_resolution``'s third answer is the
        evidence the fix rests on, so it is pinned directly rather than only
        through a bundle verdict — a resolver that returned an empty set
        everywhere would let the key look "free" on every content fold.

        Also pins that the WRITER's arm decides it: a hash-present candidate
        suppresses the A10 arm, so the hash-less row's key must not be read.
        """
        sdk.create_point("statement", "RK-HELD", key="RK-KEY")
        sdk.create_point("statement", "RK-FREE")
        # a hash-less terminal twin under a content that ALSO has a hashed row
        hashed = sdk.create_point("statement", "RK-SHADOW")["id"]
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.content_hash = NULL, "
            "n.key = 'RK-SHADOW-KEY'", params={"id": hashed})
        sdk.create_point("statement", "RK-SHADOW")
        sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content = 'RK-SHADOW' "
            "AND n.content_hash IS NOT NULL SET n.key = 'RK-HASH-KEY'")

        cands = sdk._terminal_content_candidates([
            ("RK-HELD", "statement"), ("RK-FREE", "statement"),
            ("RK-SHADOW", "statement"), ("RK-ABSENT", "statement")])
        res = {c: _content_candidate_resolution(cands, c, "statement")
               for c in ("RK-HELD", "RK-FREE", "RK-SHADOW", "RK-ABSENT")}
        assert res["RK-HELD"][2] == frozenset({"RK-KEY"}), (
            f"the stored key of the fold target, got {res['RK-HELD']}")
        assert res["RK-FREE"][2] == frozenset(), (
            "an unkeyed holder answers the empty set — the fold ADOPTS the key, "
            f"which is why empty is not the same as 'no target' (got {res['RK-FREE']})")
        assert res["RK-SHADOW"][2] == frozenset({"RK-HASH-KEY"}), (
            "the hash arm is the writer's resolution, so the hash-LESS twin's "
            f"key must not be read (got {res['RK-SHADOW']})")
        assert res["RK-ABSENT"][1] is True and res["RK-ABSENT"][2] == frozenset(), (
            f"a mint has no fold target at all (got {res['RK-ABSENT']})")


    def test_a_whitespace_only_STORED_key_is_not_a_free_key(self, sdk):
        """FAIL-OPEN, re-entered through the ``.strip()``/truthiness mismatch
        the round-9 fix left in the in-bundle record.

        ``create_point`` asks two DIFFERENT questions about a key, with two
        different tests: the key it RESOLVES is a ``.strip()`` test
        (``_has_key``), so ``key="  "`` never reaches ``_resolve_dedup_hit`` —
        but the props bag carries the value into the CREATE map verbatim, so the
        node HOLDS ``key="  "``, and the key a FOLD meets is tested for plain
        TRUTHINESS (``isinstance(_existing_key, str) and _existing_key``), so a
        stored ``"  "`` DOES pop the next item's supplied key.

        Reading the STORED side with the RESOLVER's test recorded "the mint
        wrote no key" (``None`` = "accepts any supplied one"): points[1]'s key
        looked applied while ``create_point`` popped it, points[2]'s real
        retracted mint went unrecorded, and points[3] folded onto it with Phase
        1 certifying the bundle. The graph-read side is pinned to truthiness
        too (``keys_by_hash`` says so in place), so this was the asymmetry that
        function's own docstring forbids, one function away from the record.

        Discriminating: pre-fix the bundle COMMITS (measured: created=2,
        deduped=2) and points[2]'s retracted node comes back with
        ``confidence`` written by points[3]'s fold.
        """
        with pytest.raises(BundleValidationError, match="reuses key"):
            sdk.ingest(
                {"points": [
                    {"ref": "p0", "kind": "statement", "content": "WS-C1",
                     "key": "  "},
                    {"ref": "p1", "kind": "statement", "content": "WS-C1",
                     "key": "WS-K"},
                    {"ref": "p2", "kind": "statement", "content": "WS-C2",
                     "key": "WS-K", "status": "retracted"},
                    {"ref": "p3", "kind": "statement", "content": "WS-C3",
                     "key": "WS-K", "props": {"confidence": 0.9}},
                ]},
                promotion_policy="auto",
            )
        assert sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content IN ['WS-C1','WS-C2','WS-C3'] "
            "OR n.key = 'WS-K' RETURN n.id"
        ).result_set == [], (
            "Phase-1 must reject BEFORE any write — the phantom key record "
            "certified a fold onto a retracted node this bundle mints (#9819)")

    def test_create_point_stores_a_whitespace_only_key(self, sdk):
        """The PREMISE of the arm above, pinned on its own: the node really
        HOLDS ``"  "``, because nothing on the write path strips or refuses it
        (``_sanitize_props`` passes it through and ``key`` is a declared Point
        prop).

        If a later change strips — or rejects — a blank key at the write
        boundary, this reds, which is the signal that ``_item_stored_key``'s
        truthiness test must be revisited WITH it (that change would collapse
        the two questions into one, and this test would be the tripwire).
        """
        pid = sdk.create_point("statement", "WS-STORED", key="  ")["id"]
        assert sdk.get_point(pid).get("key") == "  "

    def test_the_stored_key_and_the_resolver_key_answer_different_questions(
            self, sdk):
        """UNIT: the two readers of an item's ``key``, pinned side by side.

        They exist because the writer asks two questions with two tests, and
        every fail-open in this area has been one of them answering the other's
        question. Both still read the SAME value (``_item_supplied_key``'s
        presence precedence: a top-level ``key`` of ``None`` SHADOWS a nested
        ``props["key"]``, exactly as ``_coerce_props`` does).
        """
        assert _item_dedup_key({"key": "  "}) is None, (
            "the RESOLVER never receives a blank key (create_point's .strip() "
            "test)")
        assert _item_stored_key({"key": "  "}) == "  ", (
            "…but the CREATE stores it verbatim, and truthiness is what the "
            "fold tests it with")
        assert _item_stored_key({"key": "K"}) == "K"
        assert _item_stored_key({"props": {"key": "K"}}) == "K"
        assert _item_stored_key({"key": None, "props": {"key": "K"}}) is None, (
            "presence, not truthiness: the top-level None SHADOWS the nested "
            "key — the #8400 precedence both readers share")
        assert _item_stored_key({"key": 7}) is None, (
            "a non-str stored key is not one a fold can pop")
        assert _item_stored_key({"key": ""}) is None


class TestTheMintRecordIsKeyedByTheSTOREDKind:
    """#9819 review P2 (round 5): the bundle-order mint record must be keyed by
    the ``pointKind`` the node is STORED with — which is NOT always the item's
    positional ``kind``.

    ``create_point``'s free-form props passthrough can carry ``pointKind``
    itself and it WINS over the positional kind: the create map's
    ``"pointKind": "$k"`` entry is overwritten by the props loop
    (``_create_map[_key] = f"${_pname}"``), which
    ``tests/test_kind_layer_partition_9964.py`` pins as a fact. So a ``points``
    item that expresses its kind through ``props`` mints a node whose graph
    identity is ``(props pointKind, content_hash)``.

    The writer's DEDUP query is the mirror case: ``_resolve_dedup_hit(content,
    pointKind=kind)`` scopes by the POSITIONAL kind — so the guard's two sides
    are deliberately ASYMMETRIC. The mint RECORD must carry the STORED kind,
    because that is the value a LATER item's dedup query compares against; the
    LOOKUP must use the later item's POSITIONAL kind, because that is the
    value the writer passes. A guard that used one kind for both failed open
    (record by positional, a later item whose positional kind equals the first
    item's STORED kind resolves a node the simulation never recorded) and
    over-rejected the mirror (record by positional, a later item whose
    positional kind differs folds onto nothing but is refused anyway).

    Both directions are driven below, on the CONTENT and the KEY identity:
    every existing arm of these identities used items whose positional and
    stored kinds agree, so the drift was invisible to them.
    """

    def test_a_content_mint_under_a_props_kind_is_still_refused(self, sdk):
        """FAIL-OPEN direction. points[0] STORES ``pointKind='decision'``
        (props wins) and is born ``retracted``; points[1]'s positional kind is
        ``decision``, so the writer's content dedup resolves points[0] and
        folds — stamping ``batch_id`` on a terminal point. Keying the record by
        the positional ``statement`` made points[1] miss it and Phase 1 certify
        the bundle.

        Discriminating: pre-fix the ingest is ACCEPTED (no
        ``BundleValidationError``) and the point is written.
        """
        with pytest.raises(BundleValidationError, match="reuses content"):
            sdk.ingest(
                {"points": [
                    {"ref": "p1", "kind": "statement",
                     "content": "PROPS-KIND-CONTENT-9819",
                     "status": "retracted",
                     "props": {"pointKind": "decision"}},
                    {"ref": "p2", "kind": "decision",
                     "content": "PROPS-KIND-CONTENT-9819"},
                ]},
                promotion_policy="auto",
            )
        assert sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content = 'PROPS-KIND-CONTENT-9819' "
            "RETURN n.id"
        ).result_set == [], (
            "Phase-1 must reject BEFORE any write — the props-kind mint was "
            "recorded under the positional kind, so points[1] folded onto the "
            "terminal node it had just created (#9819)")

    def test_a_content_mint_whose_positional_kind_differs_is_not_over_rejected(
            self, sdk):
        """the MIRROR direction: recorded by the positional kind, this bundle
        is refused for a fold the writer never performs.

        points[0] STORES ``pointKind='core:deployment'`` (props wins) and is
        ``retracted``; points[1]'s positional kind is ``statement``, so the
        writer's dedup query (``n.pointKind = 'statement'``) MISSES points[0]'s
        node and points[1] mints a fresh point. ``origin/main`` commits this
        bundle.

        Discriminating: pre-fix this raises ``BundleValidationError`` matching
        "reuses content" and no point is written at all.
        """
        res = sdk.ingest(
            {"points": [
                {"ref": "p1", "kind": "statement",
                 "content": "PROPS-KIND-MIRROR-9819",
                 "status": "retracted",
                 "props": {"pointKind": "core:deployment"}},
                {"ref": "p2", "kind": "statement",
                 "content": "PROPS-KIND-MIRROR-9819"},
            ]},
            promotion_policy="auto",
        )
        assert res["created"]["points"] == 2, (
            "both items mint — the second item's dedup query scopes by its "
            f"POSITIONAL kind and cannot resolve the first (got {res})")
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.content = 'PROPS-KIND-MIRROR-9819' "
            "RETURN n.pointKind, n.status, n.id"
        ).result_set
        assert len(rows) == 2, f"two distinct points, got {rows}"
        assert {(r[0], r[1]) for r in rows} == {
            ("core:deployment", "retracted"), ("statement", "live")}, (
            "the props spelling WINS on the stored node, and the second item "
            f"mints under its own positional kind (got {rows})")

    def test_a_key_mint_under_a_props_kind_is_still_refused(self, sdk):
        """the KEY identity has the same asymmetry (``_find_points_by_key``
        scopes by ``pointKind`` too), so the bundle-order key record needs the
        same correction.

        points[0] mints key K with a STORED ``pointKind='decision'`` and is
        ``retracted``; points[1] re-uses K under the positional kind
        ``decision``, so the writer's key-first fold lands on the terminal
        node. Recorded by the positional ``statement``, the guard missed it.

        Discriminating: pre-fix the ingest is ACCEPTED and the terminal point
        comes back with ``batch_id`` stamped.
        """
        with pytest.raises(BundleValidationError, match="reuses key"):
            sdk.ingest(
                {"points": [
                    {"ref": "p1", "kind": "statement", "key": "K-PROPS-9819",
                     "content": "KEY-PROPS-CONTENT-A",
                     "status": "retracted",
                     "props": {"pointKind": "decision"}},
                    {"ref": "p2", "kind": "decision", "key": "K-PROPS-9819",
                     "content": "KEY-PROPS-CONTENT-B"},
                ]},
                promotion_policy="auto",
            )
        assert sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.key = 'K-PROPS-9819' RETURN n.id"
        ).result_set == [], (
            "Phase-1 must reject BEFORE any write — the key-first fold would "
            "have landed on a terminal point this bundle mints (#8400/#9819)")

    def test_a_key_mint_whose_positional_kind_differs_is_not_over_rejected(
            self, sdk):
        """the KEY mirror: points[0] stores ``pointKind='core:deployment'``
        under key K, points[1] re-uses K with the positional kind
        ``statement`` — the writer's key lookup is per-``pointKind`` (#6110),
        so it MISSES and points[1] mints its own point.

        Discriminating: pre-fix this raises ``BundleValidationError`` matching
        "reuses key" and nothing is written.
        """
        res = sdk.ingest(
            {"points": [
                {"ref": "p1", "kind": "statement", "key": "K-MIRROR-9819",
                 "content": "KEY-MIRROR-CONTENT-A", "status": "retracted",
                 "props": {"pointKind": "core:deployment"}},
                {"ref": "p2", "kind": "statement", "key": "K-MIRROR-9819",
                 "content": "KEY-MIRROR-CONTENT-B"},
            ]},
            promotion_policy="auto",
        )
        assert res["created"]["points"] == 2, (
            f"both items mint their own point (got {res})")
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.key = 'K-MIRROR-9819' "
            "RETURN n.pointKind, n.status"
        ).result_set
        assert len(rows) == 2, f"two carriers, one per pointKind — got {rows}"
        assert {r[0] for r in rows} == {"core:deployment", "statement"}, (
            f"each point keeps its own kind scope (got {rows})")


def _leg_kind_comes_from_the_single_home(source: str) -> bool:
    """True when every ``_find_terminal_dedup_hit`` call in ``source`` is handed
    a NAME whose assignment is a ``_item_point_kind(...)`` call (#9819 P3).

    ``_item_point_kind`` exists so the Phase-1 readers of an item\'s
    ``pointKind`` cannot drift apart; a reader that re-derives the default
    inline is a third copy. This predicate is the structural form of that
    claim: it looks at the ARGUMENT the call is handed and at the assignment
    that produced it, so prose, comments and docstrings mentioning the helper
    prove nothing. No call site at all returns ``False`` — a pin that cannot
    find what it pins must red, not pass.
    """
    tree = ast.parse(source)
    homes = {
        target.id
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        if isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_item_point_kind"
        for target in node.targets if isinstance(target, ast.Name)
    }
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_find_terminal_dedup_hit"
    ]
    if not calls:
        return False
    for call in calls:
        if len(call.args) < 2 or not isinstance(call.args[1], ast.Name):
            return False
        if call.args[1].id not in homes:
            return False
    return True


class TestTheRetainedConnectionLegReadsTheSingleKindHome:
    """#9819 review P3: the retained per-connection content leg must not be a
    THIRD, drifted reader of an item\'s ``pointKind``.

    The leg is the pre-#9173 ``create_direct_edge`` prediction. It is DEAD by
    construction today — a bundle-local ``points`` ref makes the pair
    Point-bearing, so ``_effective_connection_routes`` answers ``operator`` and
    the leg\'s ``route != "direct"`` skip keeps it out — which is exactly why a
    private copy of the kind default survived there: nothing exercises it.
    ``_item_point_kind`` is the declared single home for that value (its own
    docstring says so, and the write loop reads the item\'s ``kind`` the same
    way), so the leg must resolve it there rather than re-deriving
    ``item.get("kind") or "statement"`` inline.

    ⚠️ WHY A SOURCE PIN AND NOT A BEHAVIOURAL ONE, measured rather than
    assumed: the two spellings differ ONLY for a truthy non-``str`` kind, and
    such a kind is itself a Phase-1 violation (``_check_kind``) that
    ``create_point`` refuses — so no bundle can commit through either spelling,
    and the live `points` loop refuses it on its own. A behavioural test is
    therefore vacuous here: it passes on BOTH spellings (measured — the driver
    accepts the dict as a bound parameter and simply matches no row, so even
    the "raw driver error" shape does not surface). The drift is a STRUCTURAL
    claim, so it is pinned structurally, with a negative control that reds on
    the pre-fix spelling.
    """

    def test_the_leg_resolves_kind_through_the_single_home(self):
        source = textwrap.dedent(inspect.getsource(TortoiseSDK._check_endpoints))
        assert _leg_kind_comes_from_the_single_home(source), (
            "the per-connection content leg resolves ``pointKind`` inline "
            "instead of through ``_item_point_kind`` — a third reader of a "
            "value that already has one home, so a #8499 narrowing that "
            "re-arms the leg would resolve a different kind than the write "
            "loop (#9819 review P3)")

    def test_the_pin_reads_the_call_site_not_the_docstring(self):
        """NON-VACUITY: the pre-fix spelling must fail the pin.

        The predicate looks at the ARGUMENT the retained
        ``_find_terminal_dedup_hit`` call is handed and at the assignment that
        produced it — not at prose. Driven here on the exact drifted shape, so
        a pin that quietly stopped looking (or that accepted any mention of the
        helper) would red rather than pass.
        """
        drifted = textwrap.dedent(
            """
            def _check_endpoints(self, item):
                global _item_point_kind
                kind = item.get("kind") or "statement"
                hit = self._find_terminal_dedup_hit(content, kind, key=None)
                return hit
            """
        )
        assert not _leg_kind_comes_from_the_single_home(drifted), (
            "the pin accepts the pre-fix spelling (`item.get(\"kind\") or "
            "\"statement\"`), so it proves nothing")


class TestContentIdentityReadIsBatched:
    """#9819 review P2: the content identity must not cost a graph read PER
    ITEM.

    ``_find_terminal_dedup_hit`` is THREE queries (terminal-hash,
    unfiltered-hash, hash-less A10 fallback) and ``_check_endpoints`` called it
    once per ``points`` item, so ``_validate_bundle`` went linear in bundle
    size on the common ingest path — measured on the pre-batch head: 200
    fresh-content points 3.01 s vs 0.00 s on ``origin/main``; 200 live-content
    hits 2.08 s; a 200-item ingest 19.89 s.

    Pinned as a COUNT, not a timing: a duration assertion on embedded FalkorDB
    is machine-dependent and would flake, while the query count IS the defect.
    """

    @staticmethod
    def _count_queries(sdk, bundle):
        """(all graph queries, content-identity queries) for one bundle.

        Patches the ``_GuardedGraph`` CLASS, not the instance: the handle
        declares ``__slots__``, so an instance attribute is read-only.
        """
        import unittest.mock as mock

        proj = sdk._get_proj()
        calls: list[str] = []
        real = type(proj.g).query

        def spy(self, q, **kwargs):
            calls.append(q)
            return real(self, q, **kwargs)

        with mock.patch.object(type(proj.g), "query", spy):
            sdk._validate_bundle(bundle)
        return len(calls), sum(1 for q in calls if "content_hash" in q)

    def test_bundle_query_count_does_not_grow_with_item_count(self, sdk):
        one = {"points": [{"ref": "p1", "kind": "statement",
                           "content": "BATCH-FRESH-1"}]}
        many = {"points": [{"ref": f"p{i}", "kind": "statement",
                            "content": f"BATCH-FRESH-{i}"} for i in range(6)]}
        one_total, one_content = self._count_queries(sdk, one)
        many_total, many_content = self._count_queries(sdk, many)
        assert many_content == one_content, (
            "the content identity is read per item, not once per bundle: "
            f"1 item -> {one_content}, 6 items -> {many_content} (#9819 P2)")
        assert one_content <= 2, (
            "the content identity is at most the hash arm plus the GATED "
            f"legacy arm, never more (got {one_content}) — see "
            "`TestTheBatchedReadPlansAsASeek` for the plan this protects")
        assert many_total == one_total, (
            f"_validate_bundle cost scales with bundle size: {one_total} -> "
            f"{many_total} queries (#9819 P2)")


class TestTheBatchedReadPlansAsASeek:
    """#9819 review UNVERIFIED 1, SETTLED by measurement: the batched content
    read must not plan as a scan of the whole ``:Point`` label.

    The single statement this replaced joined the hash identity to the A10
    hash-less fallback with ``OR``, and the ``OR`` defeated the
    ``Point.content_hash`` index — ``g.explain`` on an embedded graph returned
    ``Node By Label Scan | (n:Point)``, i.e. O(all Points) on EVERY
    ``_validate_bundle``, which is a worse regression than the per-item cost the
    batching fixed. ``content`` is deliberately NOT indexed (only the identity
    props are: ``_POINT_RANGE_INDEX_PROPS``), so the fix is not "add an index"
    — it is two statements, the hash arm index-anchored and the legacy arm
    GATED to the only case that can read it.

    These pin the SHAPE the method actually issues (captured from the real
    call, never re-spelled here) so a future "simplification" back into one
    ``OR`` reds here instead of shipping a graph-sized scan.
    """

    @staticmethod
    def _content_identity_statements(sdk, bundle):
        """``[(cypher, params)]`` for the content-identity reads the REAL
        ``_validate_bundle`` issues for ``bundle``."""
        import unittest.mock as mock

        proj = sdk._get_proj()
        calls: list[tuple[str, dict]] = []
        real = type(proj.g).query

        def spy(self, q, **kwargs):
            calls.append((q, kwargs.get("params") or {}))
            return real(self, q, **kwargs)

        with mock.patch.object(type(proj.g), "query", spy):
            sdk._validate_bundle(bundle)
        return [(q, p) for q, p in calls if "content_hash" in q]

    def test_the_hash_arm_is_an_index_seek(self, sdk):
        """Discriminating: with the single ``OR`` statement this FAILS — the
        captured plan is ``Node By Label Scan``."""
        stmts = self._content_identity_statements(
            sdk, {"points": [{"ref": "p1", "kind": "statement",
                              "content": "PLAN-SEEK-9819"}]})
        assert stmts, "no content-identity read was issued at all"
        plan = str(sdk._get_proj().g.explain(stmts[0][0], params=stmts[0][1]))
        assert "Node By Index Scan" in plan, (
            "the batched content read does not seek Point.content_hash — a "
            f"graph-sized label scan per _validate_bundle:\n{plan}")

    def test_the_legacy_arm_is_skipped_when_every_hash_is_present(self, sdk):
        """The gated arm must not run on the common re-ingest shape.

        Every wanted hash row exists, so ``_content_candidate_resolution`` can
        never read the hash-less maps — and the arm is a label scan.
        Discriminating: pre-fix (the single ``OR`` statement) there is exactly
        ONE statement here and this asserts one, so this arm needs the
        ``content_hash IS NULL`` spelling to tell the two apart; the assertion
        below is that the ONE statement issued is the HASH arm, not the
        fallback.
        """
        sdk.create_point("statement", "PLAN-HIT-9819")
        stmts = self._content_identity_statements(
            sdk, {"points": [{"ref": "p1", "kind": "statement",
                              "content": "PLAN-HIT-9819"}]})
        assert len(stmts) == 1, (
            "the hash-less fallback ran although every wanted hash has a row "
            f"-> {len(stmts)} content-identity statement(s)")
        assert "n.content_hash IS NULL" not in stmts[0][0], (
            "the ONE statement issued is the label-scanning legacy arm: "
            f"{stmts[0][0]}")

    def test_a_fresh_bundle_still_reads_the_legacy_arm_once(self, sdk):
        """The gate must stay FAIL-CLOSED-safe: fresh content has no hash row,
        so the writer's own ``_find_point_by_content`` fallback can match a
        hash-less legacy twin and the guard must see it — one scan for the
        BUNDLE, not one per item."""
        stmts = self._content_identity_statements(
            sdk, {"points": [{"ref": f"p{i}", "kind": "statement",
                              "content": f"PLAN-FRESH-{i}"}
                             for i in range(5)]})
        assert len(stmts) == 2, (
            f"expected the hash arm + ONE gated legacy arm, got {len(stmts)}")
        assert sum("n.content_hash IS NULL" in q for q, _ in stmts) == 1


class TestNoInBundleDedupEscape:
    """#8400 review P2: the refusal told callers to "pass a fresh key with
    dedup=False", which is not reachable through ``ingest``.

    The points loop writes ``create_point(kind, content, dedup=True,
    **write_item)``, so a top-level ``dedup`` on a bundle item binds the same
    keyword twice: the caller who follows the old message gets a RAW
    ``TypeError`` from inside the write loop, after step 1 and any earlier
    point item committed. It is refused at shape time instead — a
    zero-mutation Phase-1 verdict, and the message names a route that exists
    (an out-of-bundle ``create_point(..., dedup=False)``).
    """

    def test_top_level_dedup_on_a_points_item_is_a_phase1_violation(self, sdk):
        """Discriminating: pre-fix this raises ``TypeError`` (not the
        ``BundleValidationError`` Phase 1 contract), after the source item
        already committed."""
        with pytest.raises(BundleValidationError, match="not a bundle item field"):
            sdk.ingest({
                "sources": [{"ref": "s1", "url": "https://example.test/s",
                             "sourceKind": "web"}],
                "points": [{"ref": "p1", "kind": "statement",
                            "content": "DEDUP-ESCAPE", "dedup": False,
                            "extractedFrom": "s1"}],
            })
        assert sdk._get_proj().g.query(
            "MATCH (n:Point {content:'DEDUP-ESCAPE'}) RETURN n.id"
        ).result_set == [], "the bundle wrote an item before refusing"


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


class TestContentCandidateReadMatchesThePerItemHelpers:
    """#9819 review P2 — the SAFETY NET for the batched content read.

    ``_terminal_content_candidates`` + ``_content_candidate_resolution``
    replaced, in at most TWO queries (the hash arm, plus the GATED legacy arm),
    what two per-item helpers answered with four graph reads (``_find_terminal_dedup_hit``'s three tiers, and
    ``_find_point_by_content``'s "does this item mint?"). ``TestContentIdentity-
    ReadIsBatched`` pins the COST (the query count) and therefore says nothing
    about the ANSWERS; a batched read that resolved a different node set would
    silently move the guard's radius — fail-open on one shape, over-reject on
    another.

    So this class pins EQUALITY, on the shapes each tier and each arm exists
    for, against the per-item helpers themselves. `per-item` is the definition;
    `batched` must agree with it on every pair. Mutation-checked: dropping the
    #2971 "no hash-present sibling" gate from ``_content_candidate_resolution``
    reds both tests, naming the disagreeing pair (``T2-shadows-T3``).
    """

    def _states(self, sdk) -> list[tuple[str, str]]:
        """One point per tier/leg shape, then the pairs to compare."""
        # tier 1 — terminal + hash present, in both terminal vocabularies.
        sdk.create_point("statement", "T1-retracted", status="retracted")
        sdk.create_point("statement", "T1-superseded", status="superseded")
        flagged = sdk.create_point("statement", "T1-invalidated")["id"]
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.outdated = true", params={"id": flagged})
        # tier 2 — a LIVE hash-present point: no hit, and it must also SUPPRESS
        # the hash-less scan for the same content (#2971's gate).
        sdk.create_point("statement", "T2-live")
        # tier 2 shadowing tier 3: a live hashed point AND a terminal hash-less
        # twin — the writer resolves the hashed one, so NO hit.
        sdk.create_point("statement", "T2-shadows-T3")
        hashless_twin = sdk.create_point("statement", "T2-shadows-T3")["id"]
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'retracted', "
            "n.content_hash = NULL", params={"id": hashless_twin})
        # tier 3 — hash-less terminal, no hashed sibling at all; and a hash-less
        # LIVE one that must stay a miss.
        for content, status in (("T3-hashless-retracted", "retracted"),
                                ("T3-hashless-live", "live")):
            pid = sdk.create_point("statement", content)["id"]
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) SET n.status = $st, "
                "n.content_hash = NULL", params={"id": pid, "st": status})
        # kind scoping — same content, terminal point in ANOTHER kind.
        sdk.create_point("decision", "KIND-scoped", status="retracted")
        # an operator is not a point-dedup candidate.
        sdk._get_proj().g.query(
            "CREATE (n:Point {id:'op-9819-cand', content:'OP-scoped', "
            "content_hash:$ch, pointKind:'statement', is_operator:true, "
            "op_type:'IMPL', status:'retracted'})",
            params={"ch": _content_hash("OP-scoped")})

        pairs = [("statement", c) for c in
                 ("T1-retracted", "T1-superseded", "T1-invalidated", "T2-live",
                  "T2-shadows-T3", "T3-hashless-retracted", "T3-hashless-live",
                  "ABSENT-9819", "KIND-scoped", "OP-scoped")]
        pairs.append(("decision", "KIND-scoped"))
        pairs.append(("observation", "T1-retracted"))
        return pairs

    def _batched(self, sdk, pairs):
        cands = sdk._terminal_content_candidates(
            [(content, kind) for kind, content in pairs])
        return {pair: _content_candidate_resolution(
            cands, pair[1], pair[0])[:2] for pair in pairs}

    def test_batched_resolution_equals_the_per_item_helpers(self, sdk):
        pairs = self._states(sdk)
        batched = self._batched(sdk, pairs)
        per_item = {
            pair: (sdk._find_terminal_dedup_hit(pair[1], pair[0]),
                   sdk._find_point_by_content(pair[1], pointKind=pair[0]) is None)
            for pair in pairs
        }
        diff = {p: (per_item[p], batched[p]) for p in per_item
                if per_item[p] != batched[p]}
        assert not diff, (
            "the batched content read disagrees with the per-item helpers it "
            f"replaced — the guard's radius moved (per-item, batched): {diff}")

    def test_the_comparison_is_discriminating(self, sdk):
        """NEGATIVE CONTROL: the equality above would also hold if BOTH sides
        returned "no hit, mints" everywhere. Pin the answers the shapes exist
        to produce."""
        pairs = self._states(sdk)
        batched = self._batched(sdk, pairs)
        must_miss = [("statement", c) for c in
                     ("T2-live", "T2-shadows-T3", "T3-hashless-live",
                      "ABSENT-9819", "OP-scoped", "KIND-scoped")]
        for pair in must_miss:
            assert batched[pair][0] is None, f"{pair} must not be a terminal hit"
        for pair in [("statement", c) for c in
                     ("T1-retracted", "T1-superseded", "T1-invalidated",
                      "T3-hashless-retracted")] + [("decision", "KIND-scoped")]:
            assert isinstance(batched[pair][0], str), (
                f"{pair} must resolve to a terminal point id")
        # `would_mint` — the writer's own answer, which gates the bundle-order
        # record. Only ABSENT mints; every shape with a hash-present or
        # hash-less holder does not.
        assert batched[("statement", "ABSENT-9819")][1] is True
        for pair in [("statement", c) for c in
                     ("T1-retracted", "T2-live", "T3-hashless-retracted",
                      "T3-hashless-live", "T2-shadows-T3")]:
            assert batched[pair][1] is False, f"{pair} must not mint"

    def test_a_stored_non_string_kind_is_skipped_not_keyed(self, sdk):
        """#9819 review round 6 — the ROW side's unhashable-kind guard.

        The item side is normalised by ``_item_point_kind`` (a non-string kind
        falls back to the default), but ``kind`` in the row loop is the node's
        STORED ``n.pointKind``, which FalkorDB may return as a **list** — and a
        list is unhashable, so keying the identity maps by it raised a raw
        ``TypeError: unhashable type: 'list'`` out of Phase 1, bypassing the
        aggregated ``BundleValidationError`` contract. The state is reachable:
        ``_refuse_invisible_kind`` admits a non-string kind **by documented
        decision** (padded strings only are refused), and this tree already
        seeds an array ``pointKind`` on a ``:Point`` by plain Cypher
        (``tests/test_consistency_divergence_5011.py``).

        Writer parity — why SKIPPING is the right answer and not a shrug:
        ``n.pointKind = $kind`` compares a STRING, so an array kind matches no
        item's dedup query. The row is invisible to the writer, so the item
        MINTS. Pre-fix this test errors inside
        ``_terminal_content_candidates``, before it can assert anything.
        """
        content = "ARRAY-KIND-9819"
        pid = sdk.create_point("statement", content)["id"]
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.pointKind = ['x']",
            params={"id": pid})

        cands = sdk._terminal_content_candidates([(content, "statement")])
        assert _content_candidate_resolution(
            cands, content, "statement") == (None, True, frozenset()), (
            "an array-kind row must read as NO terminal hit and MINT — the "
            "writer's `n.pointKind = $kind` cannot match an array")
        # …and it agrees with the per-item helpers this batched read replaced.
        assert sdk._find_terminal_dedup_hit(content, "statement") is None
        assert sdk._find_point_by_content(
            content, pointKind="statement") is None
        # The Phase-1 CONTRACT holds end to end — a zero-violation verdict,
        # not the raw TypeError a keyed list raised.
        assert sdk._validate_bundle(
            {"points": [{"ref": "p1", "kind": "statement",
                         "content": content}]}) == []

    def test_the_narrow_kind_skip_does_not_blind_the_read(self, sdk):
        """NEGATIVE CONTROL for the skip above — and the end-to-end ingest leg.

        Two things that test cannot show, and that a lazier fix would fail:

        1. the skip must neither turn an array-kind row into a HIT (a fix that
           COERCED it — ``str(kind)``, or a fallback to the item's own kind when
           the stored one is unhashable — would resolve the wrong node and
           over-block a legal bundle) nor suppress the STRING-kind TERMINAL twin
           of the same content. That twin must remain the hit and the bundle
           must still be refused: "skip non-str kinds" must not quietly disable
           the content leg, which is the fail-open the guard exists to close.
           Pre-fix this errors (the array-kind row is keyed before the twin is
           ever reached).
        2. the defect surfaced through the PUBLIC door, not only in the read:
           the review's repro was ``sdk.ingest(...)`` raising a bare
           ``TypeError``. So the legal bundle is committed here, and the
           collided one raises the STRUCTURED verdict.
        """
        hash_ = _content_hash("ARRAY-AND-STRING-9819")
        # The array-kind row this guard must skip …
        sdk._get_proj().g.query(
            "CREATE (n:Point {id:'arraykind-NEG-9819', "
            "content:'ARRAY-AND-STRING-9819', content_hash:$ch, "
            "pointKind:['x'], status:'retracted'})", params={"ch": hash_})
        # … and a STRING-kind, TERMINAL twin of the SAME content, which is a
        # real hit (`create_point` cannot fold onto the array-kind node — its
        # dedup query scopes by the string kind — so this is a second node).
        live_id = sdk.create_point("statement", "ARRAY-AND-STRING-9819")["id"]
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'retracted'",
            params={"id": live_id})

        cands = sdk._terminal_content_candidates(
            [("ARRAY-AND-STRING-9819", "statement")])
        hit, mints, _fold_keys = _content_candidate_resolution(
            cands, "ARRAY-AND-STRING-9819", "statement")
        assert hit == live_id, (
            "the STRING-kind terminal twin must still be the hit — the skip "
            f"must not blind the read (got {hit!r})")
        assert mints is False
        with pytest.raises(BundleValidationError) as ei:
            sdk.ingest({"points": [{"content": "ARRAY-AND-STRING-9819"}]})
        assert "content matches terminal point" in str(ei.value)

        # A bundle whose content's ONLY holder is the array-kind row is legal
        # and must COMMIT through the public door — the exact shape the review
        # measured raising a bare TypeError out of `ingest`.
        only = "ARRAY-ONLY-INGEST-9819"
        sdk._get_proj().g.query(
            "CREATE (n:Point {id:'arraykind-ONLY-9819', content:$c, "
            "content_hash:$ch, pointKind:['x'], status:'retracted'})",
            params={"c": only, "ch": _content_hash(only)})
        assert sdk._validate_bundle({"points": [{"content": only}]}) == []
        out = sdk.ingest({"points": [{"content": only}]})
        assert out, "the ingest must commit, not raise"
