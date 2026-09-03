"""#2002 (W6, epic #1976): first-capture disclosure + Settings view/delete —
docker-lane integration tests (the DE2E-11 self-use surface).

W6 (session-capture disclosure, self-use path) owns:

- the FIRST-capture in-conversation announcement: a 2xx capture response
  carries `first_capture` exactly once per org — the capture pipeline writes
  the graph-held `capture-disclosed` checkpoint (write_completed_step FWW,
  so the disclosure can never re-fire), UNLESS #1927's session_recording
  off-switch quieted the request first (409, never a checkpoint write);
- the Settings → Captured-sessions VIEW/DELETE leg (W4's Home 4 seam): GET
  /v1/sessions/{id} (dual-auth, #1828) renders the transcript panel, and
  DELETE /v1/sessions/{id} removes the Session + its owned subgraph AND
  cleans the per-harness capture receipt by recompute (a receipt is an
  orphan iff zero Sessions remain in its harness bucket — T1-P12's
  receipt↔Session invariant, the epic's "delete orphans graph data" risk);
- delete-during-capture safety: the capture path re-verifies the Session
  before/after its receipt write (skips or compensates an orphaned receipt)
  and the delete's recompute is the delete-side half — the race tests pin
  the invariant under the real interleaving.

Runs in the docker lane (TORTOISE_DB_URI) — mirrors
test_onboarding_w4_settings.py / test_onboarding_state_split.py. URI-less
runs (tier-2 embedded legs, carve-out) SKIP at module level.
"""

from __future__ import annotations

import os
import threading
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY",
                      "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

import pytest

# docker-lane gate (epic #1647 P4 / #1997): URI-less embedded legs cannot run
# these server-mode assertions — skip cleanly instead of failing.
from tortoise.config import is_db_uri as _is_db_uri

if not _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
    pytest.skip(
        "docker-lane W6 capture-disclosure tests require TORTOISE_DB_URI "
        "(tier-2 embedded legs skip)",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient

from tortoise import hosted_api as _ha
from tortoise.hosted_api import (
    _get_onboarding_state,
    _make_sdk,
    app,
)
from tortoise.onboarding import state as onboarding_state

CONV = [
    {"role": "user", "content": "We decided to ship the disclosure slice "
                                "first and keep capture default-on."},
    {"role": "assistant", "content": "Agreed — the delete path lands in the "
                                     "same slice so nothing orphans."},
    {"role": "user", "content": "ok"},
]


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """Offline MockModel session extractor (#822) — capture runs with zero
    network regardless of ambient provider keys."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


def _registered_client():
    """TestClient + a freshly registered team's key (registry lane)."""
    tc = TestClient(app)
    tc.__enter__()
    email = f"w6-{uuid.uuid4().hex[:10]}@example.com"
    r = tc.post("/v1/register", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text
    tc.headers.update({"Authorization": f"Bearer {r.json()['api_key']}"})
    return tc, r.json()["team_id"]


def _proj(team_id):
    return _make_sdk(namespace=team_id)._get_proj()


def _count(proj, query, params=None):
    rows = proj.g.query(query, params=params or {}).result_set
    return int(rows[0][0]) if rows else 0


def _capture(tc, session_id, harness=None):
    body = {"conversation": CONV, "session_id": session_id}
    if harness:
        body["harness"] = harness
    r = tc.post("/v1/sessions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _disclosed_edges(team_id):
    """Count of capture-disclosed COMPLETED_STEP edges for the org (FWW
    writers never duplicate the edge — the disclosure fires exactly once)."""
    return _count(
        _proj(team_id),
        f"MATCH (n:{onboarding_state.ONBOARDING_NODE_LABEL} {{org_id: $tid}})"
        f"-[:{onboarding_state.COMPLETED_STEP_EDGE}]->"
        f"(s:{onboarding_state.ONBOARDING_STEP_LABEL} "
        "{step_id: 'capture-disclosed'}) RETURN count(*)",
        {"tid": team_id},
    )


class TestFirstCaptureDisclosure:
    def test_first_capture_writes_capture_disclosed_checkpoint(self):
        """The org's FIRST capture writes the graph-held capture-disclosed
        checkpoint + answers first_capture=true (the MCP/agent caller fires
        its in-conversation one-liner on that flag — SKILL.md §6 contract:
        W6 implements the TRIGGER, the agent owns the line)."""
        tc, team_id = _registered_client()
        try:
            resp = _capture(tc, f"s1-{uuid.uuid4().hex[:8]}")
            assert resp["first_capture"] is True
            steps = onboarding_state.completed_steps(_proj(team_id), team_id)
            assert "capture-disclosed" in steps
            assert _disclosed_edges(team_id) == 1
        finally:
            tc.__exit__(None, None, None)

    def test_second_capture_never_reannounces(self):
        """A later capture answers first_capture=false and cannot duplicate
        the checkpoint edge (FWW writer) — the announcement fires exactly
        once per org, even across re-captures of deleted session ids."""
        tc, team_id = _registered_client()
        try:
            assert _capture(tc, f"a-{uuid.uuid4().hex[:8]}")["first_capture"] is True
            assert _capture(tc, f"b-{uuid.uuid4().hex[:8]}")["first_capture"] is False
            assert _disclosed_edges(team_id) == 1
        finally:
            tc.__exit__(None, None, None)

    def test_legacy_no_harness_capture_triggers_disclosure(self):
        """A legacy no-harness hook (bare receipt key) is still a capture:
        first_capture fires and the bare session_capture_receipt lands."""
        tc, team_id = _registered_client()
        try:
            resp = _capture(tc, f"l-{uuid.uuid4().hex[:8]}")
            assert resp["first_capture"] is True
            raw = _get_onboarding_state(team_id)
            assert raw.get("session_capture_receipt"), "bare legacy receipt must land"
            assert "capture-disclosed" in onboarding_state.completed_steps(
                _proj(team_id), team_id)
        finally:
            tc.__exit__(None, None, None)

    def test_off_switch_409_never_writes_the_checkpoint(self):
        """#1927: session_recording off is the DEFAULT-ON off-switch, never a
        re-gate — capture 409s quiet, and the 409 must NOT write the
        capture-disclosed checkpoint (no disclosure for a capture that never
        happened; the flag stays untouched so a later on-flag capture still
        announces once)."""
        tc, team_id = _registered_client()
        try:
            r = tc.patch("/v1/onboarding/state", json={"session_recording": False})
            assert r.status_code == 200, r.text
            r = tc.post("/v1/sessions", json={"conversation": CONV,
                                              "session_id": f"q-{uuid.uuid4().hex[:8]}"})
            assert r.status_code == 409, r.text
            assert _disclosed_edges(team_id) == 0
            assert "capture-disclosed" not in onboarding_state.completed_steps(
                _proj(team_id), team_id)
            # back ON: the (first real) capture announces exactly once
            r = tc.patch("/v1/onboarding/state", json={"session_recording": True})
            assert r.status_code == 200, r.text
            resp = _capture(tc, f"r-{uuid.uuid4().hex[:8]}")
            assert resp["first_capture"] is True
            assert _disclosed_edges(team_id) == 1
        finally:
            tc.__exit__(None, None, None)


class TestSessionView:
    def test_view_roundtrip_lists_turns_and_extracted(self):
        """GET /v1/sessions/{id} (dual-auth) returns the transcript the
        Settings panel renders: turn_points (episodic events) + extracted
        points + the list-row counts."""
        tc, _team_id = _registered_client()
        try:
            sid = f"v-{uuid.uuid4().hex[:8]}"
            cap = _capture(tc, sid)
            det = tc.get(f"/v1/sessions/{sid}").json()
            assert det["id"] == sid
            assert det["turns"] == cap["turns"] >= 3
            assert len(det["turn_points"]) == cap["turns"]
            assert len(det["extracted_points"]) == det["extracted"]
            assert all(p["kind"] in ("decision", "statement")
                       for p in det["extracted_points"])
        finally:
            tc.__exit__(None, None, None)

    def test_view_missing_session_404(self):
        tc, _team_id = _registered_client()
        try:
            r = tc.get("/v1/sessions/does-not-exist-xyz")
            assert r.status_code == 404
        finally:
            tc.__exit__(None, None, None)


class TestSessionDelete:
    def _delete_asserts(self, tc, team_id, sid, cleanup_checks):
        """Run DELETE /v1/sessions/{sid}, assert the full graph census went
        to zero (Session + its owned turns/extracted/Source) + the re-delete
        404s, then hand the cleaned_receipts payload to cleanup_checks."""
        proj = _proj(team_id)

        def owned():
            return {
                "sessions": _count(proj,
                    "MATCH (s:Session {id:$sid}) RETURN count(s)", {"sid": sid}),
                "turns": _count(proj,
                    "MATCH (:Session {id:$sid})-[:CONTAINS]->(p:Point) "
                    "WHERE p.pointKind = 'event' RETURN count(p)", {"sid": sid}),
                "extracted": _count(proj,
                    "MATCH (:Session {id:$sid})-[:CONTAINS]->(p:Point) "
                    "WHERE p.pointKind <> 'event' RETURN count(p)", {"sid": sid}),
                # the sessionCaptured provenance Event (eventId-stamped onto
                # the extracted points; name = session_<sid>) — the P1 census
                # that pins the event-gather-before-point-delete ordering
                "events": _count(proj,
                    "MATCH (e:Event) WHERE e.eventKind = 'sessionCaptured' "
                    "AND e.sessionId = $sid RETURN count(e)", {"sid": sid}),
                "sources": _count(proj,
                    "MATCH (src:Source {url:$url}) RETURN count(src)",
                    {"url": f"session:{sid}"}),
            }

        before = owned()
        assert before["sessions"] == 1, "precondition: the session exists"
        assert before["turns"] > 0, "precondition: turn points exist"
        assert before["events"] == 1, "precondition: the provenance Event exists"
        r = tc.delete(f"/v1/sessions/{sid}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deleted"] is True
        assert isinstance(body["cleaned_receipts"], list)
        after = owned()
        assert after["sessions"] == 0
        assert after["turns"] == 0
        assert after["extracted"] == 0
        assert after["events"] == 0, "the sessionCaptured Event must not orphan"
        assert after["sources"] == 0
        # re-delete 404s (idempotent, honest)
        assert tc.delete(f"/v1/sessions/{sid}").status_code == 404
        cleanup_checks(body["cleaned_receipts"])
        return before, body

    def test_delete_removes_subgraph_and_keeps_sibling_sessions(self):
        """DELETE removes the Session + its OWNED nodes (turns, extracted
        points, the agentSession Source) and leaves a same-harness sibling
        Session + its receipt intact (bucket not empty → no cleanup)."""
        tc, team_id = _registered_client()
        try:
            sid_a = f"d1-{uuid.uuid4().hex[:8]}"
            sid_b = f"d2-{uuid.uuid4().hex[:8]}"
            _capture(tc, sid_a, harness="pi")
            _capture(tc, sid_b, harness="pi")
            assert _get_onboarding_state(team_id).get("session_capture_receipt_pi")

            def check(cleaned):
                assert cleaned == []
                assert _get_onboarding_state(team_id).get(
                    "session_capture_receipt_pi"), \
                    "receipt survives while a pi Session remains"
                proj = _proj(team_id)
                assert _count(proj, "MATCH (s:Session {id:$sid}) RETURN count(s)",
                              {"sid": sid_b}) == 1
                # the sibling's OWN turn points survive untouched
                assert _count(proj,
                    "MATCH (:Session {id:$sid})-[:CONTAINS]->(p:Point) "
                    "RETURN count(p)", {"sid": sid_b}) >= 3

            self._delete_asserts(tc, team_id, sid_a, check)
        finally:
            tc.__exit__(None, None, None)

    def test_delete_of_last_session_clears_the_harness_receipt(self):
        """Receipt cleanup by recompute: deleting the bucket's LAST session
        clears the per-harness receipt (jsonb) — T1-P12 receipt↔Session
        convergence; nothing orphans."""
        tc, team_id = _registered_client()
        try:
            sid = f"e-{uuid.uuid4().hex[:8]}"
            _capture(tc, sid, harness="pi")
            assert _get_onboarding_state(team_id).get("session_capture_receipt_pi")

            def check(cleaned):
                assert "session_capture_receipt_pi" in cleaned
                raw = _get_onboarding_state(team_id)
                assert not raw.get("session_capture_receipt_pi")

            self._delete_asserts(tc, team_id, sid, check)
        finally:
            tc.__exit__(None, None, None)

    def test_delete_cleans_only_its_own_harness_bucket(self):
        """Receipts are per-harness — deleting the LAST pi session clears
        only session_capture_receipt_pi; a sibling cursor session keeps its
        own receipt."""
        tc, team_id = _registered_client()
        try:
            sid_pi = f"f1-{uuid.uuid4().hex[:8]}"
            sid_cur = f"f2-{uuid.uuid4().hex[:8]}"
            _capture(tc, sid_pi, harness="pi")
            _capture(tc, sid_cur, harness="cursor")
            assert _get_onboarding_state(team_id).get("session_capture_receipt_pi")
            assert _get_onboarding_state(team_id).get("session_capture_receipt_cursor")

            def check(cleaned):
                assert cleaned == ["session_capture_receipt_pi"]
                raw = _get_onboarding_state(team_id)
                assert not raw.get("session_capture_receipt_pi")
                assert raw.get("session_capture_receipt_cursor"), \
                    "cursor bucket untouched"

            self._delete_asserts(tc, team_id, sid_pi, check)
        finally:
            tc.__exit__(None, None, None)

    def test_delete_unknown_session_404(self):
        tc, _team_id = _registered_client()
        try:
            assert tc.delete("/v1/sessions/never-existed-abc").status_code == 404
        finally:
            tc.__exit__(None, None, None)


class TestDeleteDuringCapture:
    def test_no_orphaned_receipt_under_concurrent_delete(self):
        """DE2E-11 negative (epic risk 'capture delete orphans graph data'):
        a delete that lands WHILE a capture of the same session_id is in
        flight must never leave an orphaned receipt. Invariant under EVERY
        interleaving (capture pre-check/post-compensation + delete recompute):
        a pi receipt may exist only while a pi Session survives."""
        tc, team_id = _registered_client()
        proj = _proj(team_id)
        try:
            sid = f"race-{uuid.uuid4().hex[:8]}"
            statuses: list = []

            def run_capture():
                try:
                    r = tc.post("/v1/sessions", json={
                        "conversation": CONV, "harness": "pi", "session_id": sid})
                    statuses.append(r.status_code)
                except Exception as exc:  # pragma: no cover — lands in statuses
                    statuses.append(exc)

            t = threading.Thread(target=run_capture)
            t.start()
            # once the capture MERGEs its Session, delete it from under the
            # in-flight capture (the real Settings user action)
            deleted = False
            while t.is_alive():
                if not deleted and _count(proj,
                        "MATCH (s:Session {id:$sid}) RETURN count(s)",
                        {"sid": sid}) > 0:
                    r = tc.delete(f"/v1/sessions/{sid}")
                    assert r.status_code == 200, r.text
                    deleted = True
                t.join(timeout=0.05)
            assert statuses and statuses[0] == 200, \
                "the capture itself still 200s (it was valid)"
            sess = _count(proj, "MATCH (s:Session {id:$sid}) RETURN count(s)",
                          {"sid": sid})
            receipt = bool(_get_onboarding_state(team_id).get(
                "session_capture_receipt_pi"))
            assert not (receipt and sess == 0), \
                "orphaned receipt: capture receipt with no surviving Session"
            if sess > 0 and receipt:
                # delete landed AFTER the full capture: receipt + Session both
                # survive — consistent. Tidy up.
                r = tc.delete(f"/v1/sessions/{sid}")
                assert r.status_code == 200, r.text
        finally:
            tc.__exit__(None, None, None)

    def test_guard_skips_receipt_when_session_gone_before_write(self, monkeypatch):
        """White-box pin of the capture-side guard (deterministic): when the
        Session vanishes between the turn-loop MERGE and the receipt write,
        the receipt is skipped — the response still 200s and carries the
        additive warning, and NO orphan receipt lands."""
        tc, team_id = _registered_client()
        real_make_sdk = _ha._make_sdk
        real_sdk = real_make_sdk(namespace=team_id)
        real_proj = real_sdk._get_proj()
        real_query = real_proj.g.query
        sweep_hits: list[str] = []

        def fake_query(query, params=None, **kw):
            # the delete "already won": every Session-existence re-check the
            # capture makes sees zero Sessions from the moment the guard runs
            merged = dict(params or {})
            merged.update(kw)  # state helpers pass params as **kwargs
            if "RETURN count(s)" in query and "{id:$sid}" in query:
                return type("Rows", (), {"result_set": [[0]]})()
            # the dead-session SWEEP (exact-ids point/Event/Source removal)
            # must fire in this branch — intercept it so the test fixture
            # graph stays intact, and record that it ran
            if ("p.id IN $ids DETACH DELETE p" in query
                    or "e.eventId = $eid" in query
                    or "src:Source {url:$url}) DETACH DELETE src" in query):
                sweep_hits.append(query)
                return type("Rows", (), {"result_set": []})()
            return real_query(query, params=merged)

        class _FakeProj:
            """The real project exposes the graph as BOTH .g.query (hosted
            impl) and .query (onboarding state helpers) — mirror both, and
            delegate every other attribute to the real project."""
            g = type("G", (), {"query": staticmethod(fake_query)})()
            query = staticmethod(fake_query)

            def __getattr__(self, name):
                return getattr(real_proj, name)

        class _GuardSdk:
            """Team-scoped SDK proxy: _get_proj returns the guard-triggering
            fake graph; every OTHER sdk call (create_event, extraction,
            materialize…) delegates to the real SDK so the capture pipeline
            runs against the real tenant graph."""
            def _get_proj(self):
                return _FakeProj()
            def __getattr__(self, name):
                return getattr(real_sdk, name)

        def patched_make_sdk(namespace=None, **kw):
            # team-scoped calls (the capture pipeline) hit the guard proxy;
            # anything else (registry auth resolution mid-request) delegates
            # so the request authenticates normally
            if namespace == team_id:
                return _GuardSdk()
            return real_make_sdk(namespace=namespace, **kw)

        monkeypatch.setattr(_ha, "_make_sdk", patched_make_sdk)
        resp = _capture(tc, f"guard-{uuid.uuid4().hex[:8]}", harness="pi")
        assert resp["first_capture"] is True  # the capture itself was valid
        assert any("deleted during capture" in w for w in resp["warnings"]), \
            "the skip must be visible (additive warning)"
        assert not _get_onboarding_state(team_id).get(
            "session_capture_receipt_pi"), "orphan receipt must never land"
        # and the dead-session sweep fired: the writes this capture landed
        # after the (simulated) removal were cleaned by exact-id point/Event/
        # Source deletes, not left to orphan
        assert any("DETACH DELETE p" in q for q in sweep_hits), \
            "dead-session sweep must remove this capture's point writes"
        assert any("DETACH DELETE src" in q for q in sweep_hits), \
            "dead-session sweep must remove the agentSession Source"
        # and the disclosure checkpoint DID land (a valid capture discloses)
        assert "capture-disclosed" in onboarding_state.completed_steps(
            _proj(team_id), team_id)
        tc.__exit__(None, None, None)
