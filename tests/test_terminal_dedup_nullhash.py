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

    The direct SET is the deterministic stand-in for the two real-world
    causes of a hash-less point: a ``rebuild_all`` replay (journal strips the
    hash) or a crash between the node CREATE and the props SET.
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
