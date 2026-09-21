"""#2971 — `_find_terminal_dedup_hit` must keep matching on a hash-less
(rebuilt) graph.

``_find_terminal_dedup_hit`` is the Phase-1 mechanism behind the cycle-17/18
"bundle-local refs resolving to terminal points" ingest guard. #2971 was the
bug where ``rebuild_all`` left every Point with ``content_hash = NULL``
(``_upsert_point_props``'s fixed SET list omitted it and ``_emit_event``
stripped it from the journal), so the helper's content-hash MATCH missed for
EVERY point and an ingest bundle could wire a direct edge to a
superseded/retracted Point with the guard silently passing.

The root cause is fixed: ``_upsert_point_props`` now RE-DERIVES
``content_hash`` from the replayed content (#2795), so a rebuilt graph keeps
its hashes and the primary hash MATCH fires exactly as it does on the
incrementally-applied graph. The A10 hash-less ``content+kind`` fallback
(the #2892 sibling) remains as defence for points that are genuinely
hash-less — a crash between the node CREATE and the props SET, a hand-edited
journal, or a graph rebuilt before the fix landed.

These tests pin BOTH paths of that helper:

  (a) terminal + NULL hash      -> found via the fallback,
  (b) terminal + hash present   -> found exactly as before (unchanged path),
  (c) non-terminal + NULL hash  -> NOT returned (terminal scoping preserved),
  (d) end-to-end: after ``rebuild_all`` the ingest guard still rejects a
      bundle-local ref that resolves to a terminal point, via the PRIMARY
      hash path (the rebuild re-derives the hash — issue Indicator 2),
  (e) end-to-end: the same guard still rejects a DELIBERATELY hash-less
      terminal point, proving the fallback path (issue Indicator 2).

Runnable embedded (with the carve-out opt-in):

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_terminal_dedup_nullhash.py -v

or on the default docker lane (TORTOISE_DB_URI set — the test redirect maps
the per-test db_path onto a per-path test graph).
"""
from __future__ import annotations

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
    """SDK with a temp database. Closed after the test."""
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_termdedup_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _make_point(sdk: TortoiseSDK, content: str, kind: str = "statement", *,
                status: str | None = None, null_hash: bool = False) -> str:
    """Create a point, then force ``status``/NULL ``content_hash`` directly.

    The direct SET is the deterministic stand-in for the real-world causes of
    a hash-less point: a crash between the node CREATE and the props SET (a
    partial write), or a graph written before #2795 D2. NOT a
    ``rebuild_all`` replay — #2795 D2 recomputes ``content_hash`` on replay,
    so a rebuild is self-healing (issue Indicator 2 / criterion (d) pins
    that).
    """
    pid = sdk.create_point(kind, content)["id"]
    sets, params = [], {"id": pid}
    if status is not None:
        sets.append("n.status = $status")
        params["status"] = status
    if null_hash:
        sets.append("n.content_hash = NULL")
    if sets:
        sdk._get_proj().g.query(
            f"MATCH (n:Point {{id:$id}}) SET {', '.join(sets)}", params=params)
    return pid


class TestFindTerminalDedupHitFallback:
    def test_terminal_point_with_null_hash_found(self, sdk):
        """(a) #2971: a terminal point whose hash was lost (rebuild/crash) is
        still resolved by the content+kind fallback."""
        pid = _make_point(sdk, "the retracted claim", status="retracted",
                          null_hash=True)
        assert sdk._find_terminal_dedup_hit(
            "the retracted claim", "statement") == pid

    def test_terminal_point_with_hash_found(self, sdk):
        """(b) the hash-present path is unchanged."""
        pid = _make_point(sdk, "hash stays", status="superseded")
        assert sdk._find_terminal_dedup_hit(
            "hash stays", "statement") == pid

    def test_non_terminal_point_with_null_hash_not_returned(self, sdk):
        """(c) terminal scoping is preserved: a live null-hash point is NOT a
        terminal dedup hit."""
        _make_point(sdk, "still alive", status="live", null_hash=True)
        assert sdk._find_terminal_dedup_hit("still alive", "statement") is None

    def test_draft_point_with_null_hash_not_returned(self, sdk):
        """(c, cont.) a draft null-hash point is likewise not returned."""
        _make_point(sdk, "still draft", null_hash=True)
        assert sdk._find_terminal_dedup_hit("still draft", "statement") is None

    def test_outdated_flag_scoping_preserved(self, sdk):
        """The pre-existing ``coalesce(n.outdated, false) = false`` scoping is
        preserved on the fallback path: a terminal-status point carrying the
        legacy ``outdated=true`` flag is not a dedup hit."""
        pid = _make_point(sdk, "flagged stale", status="superseded",
                          null_hash=True)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.outdated = true", params={"id": pid})
        assert sdk._find_terminal_dedup_hit("flagged stale", "statement") is None

    def test_kind_scoping_preserved(self, sdk):
        """The fallback keeps the ``pointKind`` scope: same content, different
        kind, terminal + null hash -> no hit."""
        _make_point(sdk, "same text different kind", kind="hypothesis",
                    status="retracted", null_hash=True)
        assert sdk._find_terminal_dedup_hit(
            "same text different kind", "statement") is None
        assert sdk._find_terminal_dedup_hit(
            "same text different kind", "hypothesis") is not None


class TestIngestGuardAfterRebuild:
    @staticmethod
    def _assert_bundle_local_ref_rejected(sdk: TortoiseSDK, content: str) -> None:
        """Ingest a bundle whose ``pTerm`` local ref resolves to an existing
        TERMINAL point holding ``content``, and assert the Phase-1 guard
        rejects it.

        Phase-1 (``BundleValidationError``): if the dedup guard misses, the
        failure surfaces later as a Phase-2 error instead — so the exception
        TYPE is the assertion, not merely "some error"."""
        bundle = {
            "points": [
                {"ref": "pTerm", "kind": "statement", "content": content},
                {"ref": "pB", "kind": "statement", "content": "a live claim"},
            ],
            "connections": [
                {"from": "pTerm", "to": "pB", "operator": "IMPL"},
            ],
        }
        with pytest.raises(BundleValidationError, match="dedup hit"):
            sdk.ingest(bundle)

    def test_bundle_local_ref_to_rebuilt_terminal_point_rejected(self, tmp_path):
        """(d) Indicator 2, PRIMARY path: create a terminal point, rebuild the
        graph, then assert a bundle whose local ref resolves to that point is
        rejected at Phase-1 (the cycle-17/18 dedup-hit guard).

        ``rebuild_all`` now re-derives ``content_hash`` (#2795), so this
        exercises the helper's primary content-hash MATCH — the path the guard
        took before the #2971 regression."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "rebuilt.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            content = "the retracted claim"
            pid = sdk.create_point("statement", content)["id"]
            sdk.retract_point(pid)
            g = sdk._get_proj().g
            assert g.query(
                "MATCH (n:Point {id:$id}) RETURN n.content_hash",
                params={"id": pid}).result_set[0][0] is not None

            sdk._get_proj().rebuild_all(str(events))

            # Premise (fixed by the #2795 re-derivation): the rebuild
            # PRESERVES the hash while preserving the terminal status, so the
            # guard's primary content-hash MATCH is the path under test.
            row = g.query(
                "MATCH (n:Point {id:$id}) RETURN n.content_hash, n.status",
                params={"id": pid}).result_set[0]
            assert row[0] is not None, (
                "rebuild_all must re-derive content_hash (#2795)")
            assert row[1] == "retracted"

            self._assert_bundle_local_ref_rejected(sdk, content)
        finally:
            sdk.close()

    def test_bundle_local_ref_to_hashless_terminal_point_rejected(self, tmp_path):
        """(e) Indicator 2, FALLBACK path: the #2971 hash-less fallback is
        still exercised end-to-end. After the rebuild, ``content_hash`` is
        nulled DIRECTLY — the deliberate stand-in for a crash between the node
        CREATE and the props SET (or a graph rebuilt before the fix) — and the
        guard must still reject the bundle-local ref via the content+kind
        fallback scan."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "hashless.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            content = "the hash-less retracted claim"
            pid = sdk.create_point("statement", content)["id"]
            sdk.retract_point(pid)

            sdk._get_proj().rebuild_all(str(events))

            # Deliberately construct the NULL-hash condition the fallback
            # exists for — the rebuild itself no longer produces it (#2795).
            g = sdk._get_proj().g
            g.query("MATCH (n:Point {id:$id}) SET n.content_hash = NULL",
                    params={"id": pid})
            row = g.query(
                "MATCH (n:Point {id:$id}) RETURN n.content_hash, n.status",
                params={"id": pid}).result_set[0]
            assert row[0] is None, "the deliberate null-hash seed must apply"
            assert row[1] == "retracted"

            self._assert_bundle_local_ref_rejected(sdk, content)
        finally:
            sdk.close()


class TestLegacyPropertyAbsentShape:
    """#2949 (review F1): the ingest guard and the writer share ONE predicate.

    A legacy plain Point written before ``is_operator:false`` was stamped
    carries NEITHER ``is_operator`` NOR ``op_type``. Before the unification
    ``_find_terminal_dedup_hit`` kept its own narrow ``n.is_operator = false``
    copy, so that shape was resolved by ``_find_point_by_content`` (the
    absence-or-false form) but MISSED by the guard — which then failed OPEN
    and let a bundle wire a direct edge onto a superseded/retracted Point (the
    #2062/#2971 hazard). These tests pin the two sides to the same answer.
    """

    @staticmethod
    def _create_legacy_point(sdk: TortoiseSDK, pid: str, content: str, *,
                             op_type: str | None = None,
                             status: str = "retracted") -> None:
        """Create a Point WITHOUT the modern ``is_operator:false`` stamp (and
        no ``content_hash``), optionally as the legacy operator shape."""
        op_clause = "op_type:$op_type, " if op_type is not None else ""
        params = {"id": pid, "content": content, "status": status}
        if op_type is not None:
            params["op_type"] = op_type
        sdk._get_proj().g.query(
            f"CREATE (n:Point {{id:$id, content:$content, "
            f"pointKind:'statement', {op_clause}status:$status}})",
            params=params)

    def test_legacy_property_absent_terminal_point_resolved_by_both(self, sdk):
        """The property-absent legacy shape (no is_operator, no op_type, NULL
        content_hash) is resolved by the WRITER and now by the GUARD too.

        MUTATION THAT REDS THIS TEST: narrow the shared predicate back to
        ``n.is_operator = false`` (or fork it in ``_find_terminal_dedup_hit``)
        — the guard then returns None here while ``_find_point_by_content``
        still resolves."""
        content = "legacy property-absent terminal claim"
        pid = "legacy-plain-2949"
        self._create_legacy_point(sdk, pid, content)
        # Precondition: the legacy property-absent + hash-less shape is real.
        row = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.op_type, "
            "n.content_hash", params={"id": pid}).result_set[0]
        assert row == [None, None, None], row
        # Writer resolution (unchanged by this fix).
        assert sdk._find_point_by_content(content, pointKind="statement") == pid
        # Guard resolution (the F1 fix): pre-fix this returned None.
        assert sdk._find_terminal_dedup_hit(content, "statement") == pid

    def test_legacy_operator_shape_still_excluded_by_both(self, sdk):
        """The legacy OPERATOR shape (``op_type`` set, ``is_operator`` ABSENT)
        stays excluded on BOTH sides: widening the guard must not make it
        resolve a node the writer would never dedup onto."""
        content = "legacy operator shape probe"
        pid = "legacy-op-2949"
        self._create_legacy_point(sdk, pid, content, op_type="NAND")
        assert sdk._find_point_by_content(
            content, pointKind="statement") is None
        assert sdk._find_terminal_dedup_hit(content, "statement") is None

    def test_ingest_guard_rejects_legacy_property_absent_terminal_point(
            self, sdk):
        """End-to-end: the guard now rejects (Phase-1) a bundle whose
        local ref resolves to a legacy property-absent terminal point — the
        exact state that failed OPEN before F1."""
        content = "legacy property-absent ingest claim"
        self._create_legacy_point(sdk, "legacy-plain-ingest-2949", content)
        TestIngestGuardAfterRebuild._assert_bundle_local_ref_rejected(
            sdk, content)


class TestDedupClauseSeam:
    """The shared-predicate seam (``_dedup_match_clauses``) exists so that no
    dedup path can re-narrow the non-operator predicate. Cycle 2 of the #2949
    review found the seam still ADMITTED a caller-supplied re-narrowing clause
    — which would silently re-create the guard/writer drift the seam was
    extracted to remove, one radius smaller. It now fails fast."""

    def test_extra_clauses_may_not_redefine_the_predicate(self, sdk):
        for clause in ("n.is_operator = false",
                       "(n.op_type IS NULL OR n.is_operator = false)",
                       "n.op_type IS NULL"):
            with pytest.raises(ValueError, match="may not redefine"):
                sdk._dedup_match_clauses(point_kind="statement",
                                         extra_clauses=(clause,))

    def test_the_legitimate_extra_clauses_are_still_accepted(self, sdk):
        """The resolver's own fallback extras — the seam's intended use —
        must keep working, or the guard above would have broken the fix."""
        clauses, params = sdk._dedup_match_clauses(
            point_kind="statement",
            extra_clauses=("n.content_hash IS NULL", "n.content = $content"))
        assert "n.content_hash IS NULL" in clauses
        assert "n.content = $content" in clauses
        assert params["kind"] == "statement"
        assert not any("is_operator" in c for c in clauses[2:])
