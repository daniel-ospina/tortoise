"""#7951 — a session DELETE must survive ``derived = replay(journal)``.

``hosted_api._delete_session_sync`` removed the ``:Session`` node, its
``CONTAINS`` Points, the ``sessionCaptured`` Event(s) and the agentSession
``:Source`` with four raw ``DETACH DELETE`` statements and **no journal
record** (plus the same class in ``_sweep_orphaned_writes``). Replay therefore
re-ran ``SessionRecorded`` / ``PointAdded`` / ``EventRecorded`` / pass-2's
``_link_source`` and **RESURRECTED everything the delete had removed** — the
deletion was invisible to the journal.

The fix journals the destruction at the surface that performs it:

* one ``EntityMutated`` op=delete per removed Point (``label="Point"``),
* one per removed Event (``label="Event"``), and
* one ``SessionDeleted`` for the ``:Session`` node itself — ``Session`` is NOT
  a canonical entity label, so no ``EntityMutated`` record can express its
  removal (the fold would fall back to the id-wide canonical delete and leave
  the node standing while ``SessionRecorded`` re-materialised it).

The canonical deletes reuse the existing #3299 fold; ``SessionDeleted`` is
folded by ``_fold_session_deleted`` in both replay engines, and that fold
removes the agentSession ``:Source`` stub (``url = session:{id}``) in the same
step — a second ``EntityMutated`` for the Source would be a peer record for
the same node, so the container owns it. ``rebuild_all``'s pass 2 re-derives
edges from every ``PointAdded`` — including ones the journal hard-deletes —
and its ``_link_source`` / ``_link_session`` side effects re-materialised the
very Source/Session a delete had removed. So the journal's ``SessionDeleted``
records are carried with their seq and re-applied AFTER every replay pass,
gated on a later same-id ``SessionRecorded`` (a re-capture supersedes the
delete).

This suite drives the REAL endpoint (``DELETE /v1/sessions/{id}``) on a
journal-backed hosted SDK, then replays the journal with BOTH engines.
"""

from __future__ import annotations

import contextlib
import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY",
                      "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi.testclient import TestClient

from tortoise import hosted_api as _ha
from tortoise.hosted_api import (
    app,
    get_current_org_gated,
    get_current_org_session_ungated,
)
from tortoise.log import EventLog

TEAM_ID = "team-7951"
TEAM = {
    "org_id": TEAM_ID,
    "key_id": None,
    "legacy_full_access": True,
    "tier": "free",
    "max_users": 1,
    "max_graphs": 1,
    "max_points": 10000,
    "max_api_keys": 2,
    "max_sessions": None,
    "scopes": [],
}
CONV = [
    {"role": "user", "content": "We decided to ship the delete-journal slice."},
    {"role": "assistant", "content": "Agreed — the journal carries it now."},
    {"role": "user", "content": "ok"},
]
SID = "s-7951"
SIBLING = "s-7951-sibling"


def _one(proj, cypher: str, **params):
    rows = proj.g.query(cypher, params=params or None).result_set
    return rows[0][0] if rows else 0


def _session_count(proj, sid: str) -> int:
    return _one(proj, "MATCH (s:Session {id:$sid}) RETURN count(s)", sid=sid)


def _contained_points(proj, sid: str) -> int:
    return _one(
        proj,
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) RETURN count(p)",
        sid=sid,
    )


def _source_count(proj, sid: str) -> int:
    return _one(proj, "MATCH (src:Source {url:$url}) RETURN count(src)",
                url=f"session:{sid}")


def _close_anchors() -> None:
    """Deterministically close every keepalive anchor (tempdir teardown)."""
    for ns in list(_ha._FALLBACK_KEEPALIVE):
        anchor = _ha._FALLBACK_KEEPALIVE.pop(ns, None)
        if anchor is not None:
            with contextlib.suppress(Exception):
                anchor.close()


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    """(TestClient, journaled SDK) on a temp embedded DB + temp journal."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "hosted.db"))
    events = tmp_path / "events"
    events.mkdir()
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(events))
    # Deterministic embedded lane: an ambient docker URI would divert the
    # graph (and the journal is what this suite is about).
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    _close_anchors()
    app.dependency_overrides[get_current_org_gated] = lambda: dict(TEAM)
    app.dependency_overrides[get_current_org_session_ungated] = lambda: dict(TEAM)
    try:
        with TestClient(app) as tc:
            sdk = _ha._make_sdk(namespace=TEAM_ID)
            yield tc, sdk
    finally:
        app.dependency_overrides.clear()
        _close_anchors()


def _capture(tc: TestClient, sid: str) -> None:
    r = tc.post("/v1/sessions", json={"conversation": CONV, "session_id": sid})
    assert r.status_code == 200, r.text


def _delete(tc: TestClient, sid: str) -> None:
    r = tc.delete(f"/v1/sessions/{sid}")
    assert r.status_code == 200, r.text


def _journal(sdk) -> list[dict]:
    return EventLog(sdk._get_event_log().path).read_all()


def _log_dir(sdk) -> str:
    return os.path.dirname(sdk._get_event_log().path)


# ═══════════════════════════════════════════════════════════════════════
# The defect: delete → replay resurrects (RED before the fix)
# ═══════════════════════════════════════════════════════════════════════

class TestDeletedSessionDoesNotResurrect:
    def test_rebuild_all_does_not_resurrect_deleted_session(self, hosted):
        """The primary engine: ``rebuild_all`` (wipe + replay)."""
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        assert _session_count(proj, SID) == 1
        assert _contained_points(proj, SID) >= 1
        assert _source_count(proj, SID) == 1

        _delete(tc, SID)
        # Live state after the delete — the state replay must reproduce.
        assert _session_count(proj, SID) == 0
        assert _contained_points(proj, SID) == 0
        assert _source_count(proj, SID) == 0

        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)

        assert _session_count(proj, SID) == 0, (
            "deleted Session RESURRECTED on rebuild_all — the delete left no "
            "journal record (#7951)")
        assert _contained_points(proj, SID) == 0, (
            "deleted Session's Points RESURRECTED on rebuild_all (#7951)")
        assert _source_count(proj, SID) == 0, (
            "deleted agentSession Source RESURRECTED on rebuild_all (#7951)")

    def test_rebuild_does_not_resurrect_deleted_session(self, hosted):
        """The apply()-based engine: ``rebuild(EventLog)``."""
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        _delete(tc, SID)
        assert _session_count(proj, SID) == 0

        proj.rebuild(EventLog(sdk._get_event_log().path),
                     confirm_destructive=True)

        assert _session_count(proj, SID) == 0, (
            "deleted Session RESURRECTED on rebuild() (#7951)")
        assert _contained_points(proj, SID) == 0, (
            "deleted Session's Points RESURRECTED on rebuild() (#7951)")
        assert _source_count(proj, SID) == 0, (
            "deleted agentSession Source RESURRECTED on rebuild() (#7951)")

    def test_delete_leaves_a_sibling_session_intact(self, hosted):
        """Fidelity: the fix must not 'cure' resurrection by dropping jar."""
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        _capture(tc, SIBLING)

        _delete(tc, SID)
        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)

        assert _session_count(proj, SID) == 0, "deleted session resurrected"
        assert _session_count(proj, SIBLING) == 1, (
            "an unrelated session must survive the replay (#7951)")
        assert _contained_points(proj, SIBLING) >= 1

    def test_recapture_after_delete_survives_rebuild(self, hosted):
        """A same-id re-capture AFTER a delete supersedes the delete: the
        re-applied `SessionDeleted` must not clobber the new incarnation (the
        deferred re-assert is gated on a later `SessionRecorded`)."""
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        _delete(tc, SID)
        _capture(tc, SID)  # re-capture the same session id

        assert _session_count(proj, SID) == 1
        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)

        assert _session_count(proj, SID) == 1, (
            "the re-captured session was clobbered by the earlier delete's "
            "deferred re-apply (#7951)")
        assert _source_count(proj, SID) == 1, (
            "the re-captured session's Source was clobbered by the earlier "
            "delete's deferred re-apply (#7951)")
        assert _contained_points(proj, SID) >= 1


class TestPostDeleteWriteReMaterialisesSource:
    """P1-a (review): the Session fold must not own the ``:Source``'s lifetime
    against an anchor it does not see.

    A post-delete write that names the deleted session re-mints
    ``Source{url:session:<id>}`` LIVE through the #3263 inferred ref — the
    journaled ``PointAdded`` carries ``extractedFrom=session:<id>``, which is
    exactly the record ``_link_source``/``_mint_source_stub`` re-materialises
    the stub from on replay. The ``SessionDeleted`` fold removes the Source as
    part of the container removal, so the deferred re-apply (``rebuild_all``)
    must NOT delete a Source a LATER journal record re-created. Before the
    fix, ``rebuild_all`` deleted it and the two replay engines DISAGREED on
    the same journal."""

    def test_point_written_after_delete_re_mints_source_and_survives_rebuild(
            self, hosted):
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        _delete(tc, SID)
        assert _source_count(proj, SID) == 0

        # Live re-mint: the write names the deleted session, so the #3263
        # inferred ref mints `session:<SID>` again and journals the PointAdded
        # that carries it.
        sdk.create_point("fact", "written after the delete", session_id=SID)
        assert _source_count(proj, SID) == 1, "live re-mint did not happen"

        # Engine 1: the apply()-based engine folds SessionDeleted INLINE, so
        # the later PointAdded legitimately re-materialises the Source.
        proj.rebuild(EventLog(sdk._get_event_log().path),
                     confirm_destructive=True)
        assert _source_count(proj, SID) == 1, (
            "rebuild() lost the Source a post-delete PointAdded "
            "re-materialised (#7951 P1-a)")

        # Engine 2: `rebuild_all` defers the SessionDeleted re-apply past pass
        # 2. It must reach the SAME verdict as engine 1.
        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)
        assert _source_count(proj, SID) == 1, (
            "rebuild_all() deleted a Source that a later PointAdded "
            "re-materialised — the deferred SessionDeleted re-apply is "
            "anchored to `SessionRecorded` only, but the Source has a second "
            "re-creation path (`_link_source` from any PointAdded naming "
            "session:<id>) (#7951 P1-a)")

    def test_both_replay_engines_agree_on_the_post_delete_write(self, hosted):
        """The two replay engines must not disagree on one journal."""
        tc, sdk = hosted
        proj = sdk._get_proj()
        _capture(tc, SID)
        _delete(tc, SID)
        sdk.create_point("fact", "post-delete", session_id=SID)

        proj.rebuild(EventLog(sdk._get_event_log().path),
                     confirm_destructive=True)
        via_rebuild = _source_count(proj, SID)
        proj.rebuild_all(_log_dir(sdk), confirm_destructive=True)
        via_rebuild_all = _source_count(proj, SID)

        assert via_rebuild == via_rebuild_all == 1, (
            f"the two replay engines disagree on one journal: "
            f"rebuild={via_rebuild} vs rebuild_all={via_rebuild_all} "
            f"(#7951 P1-a)")


class TestDeleteIsJournaled:
    def test_delete_journals_session_and_canonical_nodes(self, hosted):
        """Every node the delete removes has a journal record."""
        tc, sdk = hosted
        _capture(tc, SID)
        _delete(tc, SID)

        recs = _journal(sdk)

        assert any(e.get("type") == "SessionDeleted" and e.get("id") == SID
                   for e in recs), (
            "the :Session removal must be journaled as SessionDeleted — no "
            "EntityMutated record can express it (#7951)")
        point_deletes = [
            e for e in recs
            if e.get("type") == "EntityMutated" and e.get("op") == "delete"
            and e.get("label") == "Point"]
        assert point_deletes, (
            "every removed CONTAINS Point must be journaled as an "
            "EntityMutated op=delete (#7951)")
        assert any(
            e.get("type") == "EntityMutated" and e.get("op") == "delete"
            and e.get("label") == "Event"
            for e in recs), "the removed sessionCaptured Event must be journaled"

    def test_unknown_session_delete_journals_nothing(self, hosted):
        """A 404 (no such Session) must not mint a phantom delete record."""
        tc, sdk = hosted
        r = tc.delete("/v1/sessions/never-existed-7951")
        assert r.status_code == 404
        recs = _journal(sdk)
        assert not [e for e in recs if e.get("type") == "SessionDeleted"], (
            "a 404 delete minted a SessionDeleted record (#7951)")


# ══════════════════════════════════════════════════════════════════════
# The sweep half: `_sweep_orphaned_writes` (delete-during-capture race)
# ══════════════════════════════════════════════════════════════════════

class TestOrphanedWriteSweepIsJournaled:
    def test_sweep_journals_orphaned_writes(self, hosted, monkeypatch):
        """The delete-during-capture sweep removes this capture's orphaned
        turn Points / Event / Source and must journal each removal.

        Deterministic white-box trigger (mirroring
        ``test_onboarding_w6_capture_disclosure``'s guard test): every
        Session-existence probe reports the Session already gone (so the
        capture takes its dead branch and calls the sweep), and the sweep's
        guarded delete is answered with a matched count of 1 — the state the
        real race produces when the guard lets an orphan through. The
        assertion is on the EMISSION (the sweep's guard semantics are pinned
        separately by ``test_phase_f_sweep_event_delete_session_guard``)."""
        tc, sdk = hosted
        real_proj = sdk._get_proj()
        real_query = real_proj.g.query
        sid = "s-7951-sweep"

        def fake_query(query, params=None, **kw):
            merged = dict(params or {})
            merged.update(kw)  # state helpers pass params as **kwargs
            if ("DETACH DELETE" in query
                    and ("p.id IN $ids" in query
                         or "e.eventId = $eid" in query
                         or "src:Source {url:$url}" in query)):
                # the sweep's guard let the orphan through: one row removed.
                return type("Rows", (), {"result_set": [[1]]})()
            if "count(s)" in query and "{id:$sid}" in query:
                # "the delete already won": 0 Sessions, null state columns.
                return type("Rows", (), {
                    "result_set": [[0, None, None, None]]})()
            return real_query(query, params=merged)

        class _FakeProj:
            g = type("G", (), {"query": staticmethod(fake_query)})()
            query = staticmethod(fake_query)

            def __getattr__(self, name):  # delegate everything else
                return getattr(real_proj, name)

        class _GuardSdk:
            def _get_proj(self):
                return _FakeProj()

            def __getattr__(self, name):
                return getattr(sdk, name)

        real_make_sdk = _ha._make_sdk

        def patched_make_sdk(namespace=None, **kw):
            if namespace == TEAM_ID:
                return _GuardSdk()
            return real_make_sdk(namespace=namespace, **kw)

        monkeypatch.setattr(_ha, "_make_sdk", patched_make_sdk)
        r = tc.post("/v1/sessions", json={"conversation": CONV,
                                          "session_id": sid})
        assert r.status_code == 200, r.text

        recs = _journal(sdk)
        swept = [
            e for e in recs
            if e.get("type") == "EntityMutated" and e.get("op") == "delete"
            and e.get("label") == "Point"
            and isinstance(e.get("id"), str) and e["id"].startswith(f"{sid}_t")]
        assert swept, (
            "the orphaned-writes sweep deleted turn Points with no journal "
            "record — a replay resurrects them (#7951)")
        assert any(
            e.get("type") == "SessionDeleted" and e.get("id") == sid
            for e in recs), (
            "the sweep must journal the container removal (SessionDeleted "
            "removes the orphaned agentSession Source too) (#7951)")
