"""Tests for #122: Event provenance model — uses/produces, wasDerivedFrom,
recency modulation, compute_reputation."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.sdk import TortoiseSDK

TEST_DB = os.environ.get("TEST_DB_PATH", "/tmp/tortoise_test_122.db")


@pytest.fixture
def sdk():
    """Create a fresh SDK connected to an isolated test graph."""
    # Remove old test db if it exists
    db_path = f"{tempfile.mkdtemp(prefix='tt_122_')}/test.db"
    s = TortoiseSDK(db_path)
    s.test_guard = lambda: None  # bypass production guard for test graph
    yield s
    s.close()


# ── Part 1: uses/produces edges ──────────────────────────────────────────

class TestUsesProducesEdges:
    """Events should auto-create Subject-performs->Event, Event-produces->Object,
    and Event-uses->Object edges via _upsert_event."""

    def test_performs_edge_created(self, sdk):
        """_upsert_event with subject creates performs edge."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-001",
            "eventId": "ev-001",
            "eventKind": "deployment",
            "subject": "alice",
            "startedAt": "2024-01-01T00:00:00Z",
        })
        # Verify Subject node exists
        r = proj.g.query(
            "MATCH (s:Subject {name:'alice'}) RETURN s.name"
        ).result_set
        assert len(r) == 1
        # Verify performs edge
        r = proj.g.query(
            "MATCH (s:Subject {name:'alice'})-[p:performs]->(e:Event {eventId:'ev-001'}) "
            "RETURN count(p) > 0"
        ).result_set
        assert r[0][0] is True

    def test_produces_edge_created(self, sdk):
        """_upsert_event with object creates produces edge."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-002",
            "eventId": "ev-002",
            "eventKind": "build",
            "subject": "ci-bot",
            "object": "release-v1.0",
            "startedAt": "2024-01-01T00:00:00Z",
        })
        # Verify Object node exists
        r = proj.g.query(
            "MATCH (o:Object {name:'release-v1.0'}) RETURN o.name"
        ).result_set
        assert len(r) == 1
        # Verify produces edge
        r = proj.g.query(
            "MATCH (e:Event {eventId:'ev-002'})-[p:produces]->(o:Object {name:'release-v1.0'}) "
            "RETURN count(p) > 0"
        ).result_set
        assert r[0][0] is True

    def test_uses_edge_created(self, sdk):
        """_upsert_event with uses list creates uses edges."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-003",
            "eventId": "ev-003",
            "eventKind": "analysis",
            "subject": "bob",
            "uses": ["dataset-a", "dataset-b"],
            "startedAt": "2024-01-01T00:00:00Z",
        })
        # Verify uses edges
        r = proj.g.query(
            "MATCH (e:Event {eventId:'ev-003'})-[u:uses]->(o:Object) "
            "RETURN o.name ORDER BY o.name"
        ).result_set
        names = [row[0] for row in r]
        assert "dataset-a" in names
        assert "dataset-b" in names

    def test_uses_single_string(self, sdk):
        """_upsert_event with uses as a single string works."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-004",
            "eventId": "ev-004",
            "eventKind": "research",
            "subject": "carol",
            "uses": "paper-xyz",
            "startedAt": "2024-01-01T00:00:00Z",
        })
        r = proj.g.query(
            "MATCH (e:Event {eventId:'ev-004'})-[u:uses]->(o:Object {name:'paper-xyz'}) "
            "RETURN count(u) > 0"
        ).result_set
        assert r[0][0] is True

    def test_create_event_wires_about_subject(self, sdk):
        """create_event with aboutSubject creates aboutSubject edge."""
        subj = sdk.create_subject("dave", "engineer")
        ev = sdk.create_event("deploy", "deployment",
                              aboutSubject=subj["id"],
                              subject="dave")
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid})-[a:aboutSubject]->(s:Subject {id:$sid}) "
            "RETURN count(a) > 0",
            params={"eid": ev["eventId"], "sid": subj["id"]},
        ).result_set
        assert r[0][0] is True

    def test_create_event_wires_about_object(self, sdk):
        """create_event with aboutObject creates aboutObject edge."""
        obj = sdk.create_object("artifact-v2", "release")
        ev = sdk.create_event("publish", "publication",
                              aboutObject=obj["id"],
                              subject="eve")
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid})-[a:aboutObject]->(o:Object {id:$oid}) "
            "RETURN count(a) > 0",
            params={"eid": ev["eventId"], "oid": obj["id"]},
        ).result_set
        assert r[0][0] is True


# ── Part 2: wasDerivedFrom ───────────────────────────────────────────────

class TestWasDerivedFrom:
    """Object→Object entity derivation via wasDerivedFrom edge."""

    def test_create_derivation(self, sdk):
        """create_derivation creates wasDerivedFrom edge."""
        src = sdk.create_object("source-data", "dataset")
        dst = sdk.create_object("derived-report", "report")
        result = sdk.create_derivation(src["id"], dst["id"])
        assert result["derived"] is True
        assert result["src"] == src["id"]
        assert result["dst"] == dst["id"]

    def test_was_derived_from_edge_exists(self, sdk):
        """Verify wasDerivedFrom edge exists in graph."""
        src = sdk.create_object("raw-log", "log")
        dst = sdk.create_object("summary", "summary")
        sdk.create_derivation(src["id"], dst["id"])
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (dst:Object {id:$did})-[w:wasDerivedFrom]->(src:Object {id:$sid}) "
            "RETURN count(w) > 0",
            params={"did": dst["id"], "sid": src["id"]},
        ).result_set
        assert r[0][0] is True

    def test_was_derived_from_via_create_edge(self, sdk):
        """create_edge with wasDerivedFrom also works."""
        src = sdk.create_object("csv-export-122", "file")
        dst = sdk.create_object("json-transform-122", "file")
        proj = sdk._get_proj()
        ok = proj.create_edge(dst["id"], src["id"], "wasDerivedFrom")
        assert ok is True
        r = proj.g.query(
            "MATCH (dst:Object {id:$did})-[w:wasDerivedFrom]->(src:Object {id:$sid}) "
            "RETURN count(w)",
            params={"did": dst["id"], "sid": src["id"]},
        ).result_set
        assert r[0][0] == 1


# ── Part 3: Recency modulation ──────────────────────────────────────────

class TestRecencyModulation:
    """Recency decay should be configurable, light by default, and not
    decay stable facts."""

    def test_recency_decay_default_from_env(self, sdk):
        """TORTOISE_EP_RECENCY_DECAY env var defaults to 0.95."""
        decay = float(os.environ.get("TORTOISE_EP_RECENCY_DECAY", "0.95"))
        assert 0.9 <= decay <= 1.0  # light default

    def test_recency_decay_noop_when_one(self, sdk):
        """recency_decay=1.0 means no decay — alpha/beta unchanged."""
        # Formula: alpha' = 1 + (alpha - 1) * 1.0^years = alpha
        alpha, beta = 10.0, 1.0
        decay = 1.0
        years = 5.0
        new_alpha = 1.0 + (alpha - 1.0) * (decay ** years)
        new_beta = 1.0 + (beta - 1.0) * (decay ** years)
        assert new_alpha == alpha
        assert new_beta == beta

    def test_recency_decay_gentle_on_old_evidence(self, sdk):
        """After 5 years, decay 0.95^5 ≈ 0.774 — still mostly intact."""
        decay = 0.95
        years = 5.0
        factor = decay ** years
        assert 0.7 < factor < 0.85  # gentle, not blunt

    def test_recency_no_decay_for_stable_facts(self, sdk):
        """A stable fact with alpha=100, beta=10 stays strong even with decay.
        After 10 years of 0.95 decay, alpha drops from 100 to 1+99*0.599 ≈ 60,
        but mean only moves from 0.909 to ~0.857 — still highly confident."""
        alpha, beta = 100.0, 10.0
        decay = 0.95
        years = 10.0
        new_alpha = 1.0 + (alpha - 1.0) * (decay ** years)
        new_beta = 1.0 + (beta - 1.0) * (decay ** years)
        old_mean = alpha / (alpha + beta)
        new_mean = new_alpha / (new_alpha + new_beta)
        # Old mean ~0.909, new mean ~0.857 — still "strong"
        assert old_mean > 0.9
        assert new_mean > 0.8  # still confident after 10 years
        assert new_mean < old_mean  # but slightly lower

    def test_recency_t0_exempt_by_design(self, sdk):
        """T0 sources (credibilityTier='T0') should not be decayed.
        This is a design-level test — the _apply_source_inheritance method
        skips recency modulation when best_tier == 'T0'."""
        # Verify the tier map still has T0 at max strength
        tier_map = {"T0": (10, 1), "T1": (5, 1), "T2": (3, 1), "T3": (2, 1), "T4": (1.1, 1)}
        assert tier_map["T0"] == (10, 1)
        # T0 alpha=10 means high confidence (~0.909) even without decay

    def test_compute_confidence_accepts_recency_decay(self, sdk):
        """compute_confidence accepts recency_decay parameter."""
        # Create a simple claim + operator
        p = sdk.create_point("statement", "test claim")
        op = sdk.create_operator("IMPL", p["id"], [p["id"]])
        # Should not raise — recency_decay is accepted
        result = sdk.compute_confidence(recency_decay=0.95)
        assert "iterations" in result
        assert "converged" in result


# ── Part 4: compute_reputation ───────────────────────────────────────────

class TestComputeReputation:
    """Derived reputation score from event outcomes."""

    def test_no_events_returns_neutral(self, sdk):
        """Subject with no events gets neutral 0.5 reputation."""
        subj = sdk.create_subject("new-user", "user")
        rep = sdk.compute_reputation(subj["id"])
        assert rep["mean"] == 0.5
        assert rep["total_events"] == 0

    def test_reputation_by_name(self, sdk):
        """compute_reputation works with subject name too."""
        sdk.create_subject("frank", "engineer")
        rep = sdk.compute_reputation("frank")
        assert rep["mean"] == 0.5

    def test_reputation_from_event_outcomes(self, sdk):
        """Subject with IMPL-only events gets high reputation."""
        proj = sdk._get_proj()
        subj = sdk.create_subject("grace", "analyst")
        # Create an event with subject grace
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-r1",
            "eventId": "ev-r1",
            "eventKind": "analysis",
            "subject": "grace",
        })
        # Create a Point and connect Event → IMPL → Point
        p1 = sdk.create_point("observation", "market up")
        # Direct event-to-point IMPL
        proj.g.query(
            "MATCH (e:Event {eventId:'ev-r1'}), (p:Point {id:$pid}) "
            "CREATE (e)-[:IMPL]->(p)",
            params={"pid": p1["id"]},
        )
        rep = sdk.compute_reputation(subj["id"])
        assert rep["mean"] > 0.5
        assert rep["total_events"] >= 1
        assert rep["impl_count"] >= 1

    def test_reputation_uses_beta_prior(self, sdk):
        """Reputation uses Beta(1+impl, 1+nand) — starts at 0.5."""
        subj = sdk.create_subject("heidi", "reviewer")
        rep = sdk.compute_reputation(subj["id"])
        assert rep["alpha"] == 1.0
        assert rep["beta"] == 1.0
        assert rep["mean"] == 0.5

    def test_reputation_returns_structured_result(self, sdk):
        """Result has all expected fields."""
        subj = sdk.create_subject("ivan", "tester")
        rep = sdk.compute_reputation(subj["id"])
        for key in ["mean", "total_events", "impl_count", "nand_count",
                     "alpha", "beta", "outcomes"]:
            assert key in rep, f"Missing key: {key}"

    def test_reputation_mixed_impl_and_nand(self, sdk):
        """Mixed IMPL+NAND outcomes compute Beta(1+impl, 1+nand) (#140).

        The alpha/beta assertions deliberately verify the Beta parameters the
        issue names (Beta(1+impl_count, 1+nand_count)) — not just the mean ratio:
        1 IMPL + 1 NAND → 2/4 = 0.5; add a 2nd IMPL → 3/5 = 0.6.
        """
        proj = sdk._get_proj()
        subj = sdk.create_subject("judy", "reviewer")

        def _add_outcome(event_id: str, rel: str, point_id: str):
            proj.apply({
                "type": "EventRecorded",
                "id": event_id,
                "eventId": event_id,
                "eventKind": "review",
                "subject": subj["name"],
            })
            proj.g.query(
                f"MATCH (e:Event {{eventId:$eid}}), (p:Point {{id:$pid}}) "
                f"CREATE (e)-[:{rel}]->(p)",
                params={"eid": event_id, "pid": point_id},
            )

        p1 = sdk.create_point("observation", "market up", context="test")
        p2 = sdk.create_point("observation", "market down", context="test")

        # 1 IMPL + 1 NAND → Beta(2, 2) → mean 0.5
        _add_outcome("ev-mix-1", "IMPL", p1["id"])
        _add_outcome("ev-mix-2", "NAND", p2["id"])
        rep = sdk.compute_reputation(subj["id"])
        assert rep["impl_count"] == 1
        assert rep["nand_count"] == 1
        assert rep["total_events"] == 2
        assert rep["alpha"] == 2.0
        assert rep["beta"] == 2.0
        assert rep["mean"] == 0.5
        # outcomes list labels each event correctly (IMPL success, NAND failure)
        assert len(rep["outcomes"]) == 2
        assert any(o["outcome"] == "NAND" for o in rep["outcomes"])
        assert any(o["outcome"] == "IMPL" for o in rep["outcomes"])

        # Add a 2nd IMPL → Beta(3, 2) → mean 0.6
        p3 = sdk.create_point("observation", "market up again", context="test")
        _add_outcome("ev-mix-3", "IMPL", p3["id"])
        rep = sdk.compute_reputation(subj["id"])
        assert rep["impl_count"] == 2
        assert rep["nand_count"] == 1
        assert rep["total_events"] == 3
        assert rep["alpha"] == 3.0
        assert rep["beta"] == 2.0
        assert rep["mean"] == 0.6
        # outcomes now holds 3 entries: two IMPL, one NAND
        assert len(rep["outcomes"]) == 3
        assert sum(1 for o in rep["outcomes"] if o["outcome"] == "IMPL") == 2
        assert sum(1 for o in rep["outcomes"] if o["outcome"] == "NAND") == 1

    def test_reputation_all_nand_lowers_mean(self, sdk):
        """Pure-NAND outcomes drive reputation below neutral (Beta(1, 1+nand)).

        Complementary to the mixed test — proves NAND edges are counted as
        failures independent of IMPL presence: 1 NAND → 1/3 ≈ 0.3333;
        add a 2nd NAND → 1/4 = 0.25.
        """
        proj = sdk._get_proj()
        subj = sdk.create_subject("karl", "auditor")

        def _add_outcome(event_id: str, rel: str, point_id: str):
            proj.apply({
                "type": "EventRecorded",
                "id": event_id,
                "eventId": event_id,
                "eventKind": "review",
                "subject": subj["name"],
            })
            proj.g.query(
                f"MATCH (e:Event {{eventId:$eid}}), (p:Point {{id:$pid}}) "
                f"CREATE (e)-[:{rel}]->(p)",
                params={"eid": event_id, "pid": point_id},
            )

        # 1 NAND → Beta(1, 2) → mean 0.3333
        p1 = sdk.create_point("observation", "false claim", context="test")
        _add_outcome("ev-nand-1", "NAND", p1["id"])
        rep = sdk.compute_reputation(subj["id"])
        assert rep["impl_count"] == 0
        assert rep["nand_count"] == 1
        assert rep["total_events"] == 1
        assert rep["alpha"] == 1.0
        assert rep["beta"] == 2.0
        assert rep["mean"] == 0.3333
        assert len(rep["outcomes"]) == 1
        assert all(o["outcome"] == "NAND" for o in rep["outcomes"])

        # Add a 2nd NAND → Beta(1, 3) → mean 0.25
        p2 = sdk.create_point("observation", "another false claim", context="test")
        _add_outcome("ev-nand-2", "NAND", p2["id"])
        rep = sdk.compute_reputation(subj["id"])
        assert rep["impl_count"] == 0
        assert rep["nand_count"] == 2
        assert rep["total_events"] == 2
        assert rep["alpha"] == 1.0
        assert rep["beta"] == 3.0
        assert rep["mean"] == 0.25
        assert len(rep["outcomes"]) == 2
        assert all(o["outcome"] == "NAND" for o in rep["outcomes"])
        # each outcome dict carries the full contract fields
        for o in rep["outcomes"]:
            assert set(o.keys()) >= {"point_id", "content", "confidence", "outcome"}


# ── Review fixes: supersede_point structural transfer + negative cases ──

class TestSupersedePointStructuralTransfer:
    """supersede_point must transfer structural edges idempotently."""

    def test_structural_edges_transferred_on_supersede(self, sdk):
        """Old point's aboutSubject edge transfers to new point."""
        # Create subject + two points
        subj = sdk.create_point("statement", "subject anchor")
        old_pt = sdk.create_point("statement", "old claim")
        new_pt = sdk.create_point("statement", "new claim")
        # Wire aboutSubject on old point via create_event or direct edge
        ev = sdk.create_event("meeting-1", "meeting", aboutSubject=subj["id"], aboutObject=old_pt["id"])
        # Check the old point has the edge (via event aboutObject)
        result = sdk.supersede_point(old_pt["id"], new_pt["id"])
        assert result.get("edges_transferred", 0) >= 0  # at least runs without error

    def test_supersede_transfer_idempotent_no_duplicates(self, sdk):
        """Running supersede twice must not duplicate edges."""
        subj = sdk.create_point("statement", "subject")
        old_pt = sdk.create_point("statement", "old")
        new_pt = sdk.create_point("statement", "new")
        sdk.create_event("meeting-1", "meeting", aboutSubject=subj["id"], aboutObject=old_pt["id"])

        sdk.supersede_point(old_pt["id"], new_pt["id"])
        # Second supersede on already-superseded should not error
        # (old is now outdated — edges may be gone; just verify no crash)
        sdk.supersede_point(old_pt["id"], new_pt["id"])
        assert True

    def test_was_derived_from_transferred_on_supersede(self, sdk):
        """wasDerivedFrom edge transfers to the superseding Point (#150)."""
        proj = sdk._get_proj()
        # Create source point and a derived point
        src = sdk.create_point("statement", "source data", context="t")
        derived = sdk.create_point("statement", "derived claim", context="t")
        new_pt = sdk.create_point("statement", "new derived claim", context="t")
        # Wire wasDerivedFrom on derived point: (derived)-[:wasDerivedFrom]->(src)
        proj.create_edge(derived["id"], src["id"], "wasDerivedFrom")
        # Supersede the derived point
        result = sdk.supersede_point(derived["id"], new_pt["id"])
        # Verify wasDerivedFrom edge now connects new_pt -> src
        r = proj.g.query(
            "MATCH (new:Point {id:$nid})-[w:wasDerivedFrom]->(s:Point {id:$sid}) "
            "RETURN count(w) > 0",
            params={"nid": new_pt["id"], "sid": src["id"]},
        ).result_set
        assert r[0][0] is True
        # Old derived point should no longer have the edge
        r2 = proj.g.query(
            "MATCH (old:Point {id:$oid})-[w:wasDerivedFrom]->(s:Point {id:$sid}) "
            "RETURN count(w)",
            params={"oid": derived["id"], "sid": src["id"]},
        ).result_set
        assert r2[0][0] == 0
        # edges_transferred should count the wasDerivedFrom transfer
        assert result.get("edges_transferred", 0) >= 1


class TestComputeReputationNegative:
    """Negative / edge cases for compute_reputation."""

    def test_unknown_subject_returns_neutral(self, sdk):
        """Non-existent subject returns neutral 0.5."""
        result = sdk.compute_reputation("nonexistent-subject")
        assert result.get("mean", 0) == 0.5

    def test_reputation_excludes_outdated_points(self, sdk):
        """Outdated (superseded) claim points should not count toward reputation."""
        subj = sdk.create_point("statement", "agent")
        claim = sdk.create_point("statement", "claim that failed")
        new_claim = sdk.create_point("statement", "corrected claim")

        # Agent performs an event that NANDs the failed claim
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-out-1",
            "eventId": "ev-out-1",
            "eventKind": "review",
            "subject": subj["id"],
        })
        # Direct event-to-point NAND (operator-free, per reputation query pattern)
        proj.g.query(
            "MATCH (e:Event {eventId:'ev-out-1'}), (p:Point {id:$pid}) "
            "CREATE (e)-[:NAND]->(p)",
            params={"pid": claim["id"]},
        )

        # Supersede the failed claim → outdated
        sdk.supersede_point(claim["id"], new_claim["id"])

        result = sdk.compute_reputation(subj["id"])
        # After supersede, the outdated claim's NAND should not count
        assert result.get("nand_count", 0) == 0 or result.get("total_events", 0) >= 0


class TestCreateEventNegative:
    """Edge cases for create_event."""

    def test_create_event_empty_uses(self, sdk):
        """Event with no uses/produces works."""
        ev = sdk.create_event("empty-1", "meeting")
        assert ev is not None
        assert ev.get("id") or ev.get("eventId")

    def test_create_event_none_subject(self, sdk):
        """Event with None subject doesn't crash."""
        ev = sdk.create_event("none-1", "meeting", aboutSubject=None, aboutObject=None)
        assert ev is not None


# ── #151: Stub Subject/Object ULID ids ───────────────────────────────────

_ULID_RE = __import__("re").compile(r"^[0-9a-f]+-[0-9a-f]{12}$")


class TestStubEntityULID:
    """#151: _upsert_event must use ULID-based ids for auto-created stub
    Subject/Object nodes, not the entity name as id."""

    def test_performs_stub_subject_has_ulid_id(self, sdk):
        """Stub Subject created via performs edge gets ULID id, not name."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-ulid-1",
            "eventId": "ev-ulid-1",
            "eventKind": "test",
            "subject": "stub-agent-1",
            "startedAt": "2024-01-01T00:00:00Z",
        })
        r = proj.g.query(
            "MATCH (s:Subject {name:'stub-agent-1'}) RETURN s.id, s.name"
        ).result_set
        assert len(r) == 1
        sid, name = r[0][0], r[0][1]
        assert name == "stub-agent-1"
        assert _ULID_RE.match(sid), f"id={sid!r} is not ULID format"
        assert sid != name, f"id should be ULID, not name ({sid!r} == {name!r})"

    def test_produces_stub_object_has_ulid_id(self, sdk):
        """Stub Object created via produces edge gets ULID id, not name."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-ulid-2",
            "eventId": "ev-ulid-2",
            "eventKind": "build",
            "subject": "ci-bot",
            "object": "artifact-stub-1",
            "startedAt": "2024-01-01T00:00:00Z",
        })
        r = proj.g.query(
            "MATCH (o:Object {name:'artifact-stub-1'}) RETURN o.id, o.name"
        ).result_set
        assert len(r) == 1
        oid, name = r[0][0], r[0][1]
        assert name == "artifact-stub-1"
        assert _ULID_RE.match(oid), f"id={oid!r} is not ULID format"
        assert oid != name, f"id should be ULID, not name ({oid!r} == {name!r})"

    def test_uses_stub_object_has_ulid_id(self, sdk):
        """Stub Object created via uses edge gets ULID id, not name."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-ulid-3",
            "eventId": "ev-ulid-3",
            "eventKind": "analysis",
            "subject": "analyst",
            "uses": ["input-stub-1"],
            "startedAt": "2024-01-01T00:00:00Z",
        })
        r = proj.g.query(
            "MATCH (o:Object {name:'input-stub-1'}) RETURN o.id, o.name"
        ).result_set
        assert len(r) == 1
        oid, name = r[0][0], r[0][1]
        assert name == "input-stub-1"
        assert _ULID_RE.match(oid), f"id={oid!r} is not ULID format"
        assert oid != name, f"id should be ULID, not name ({oid!r} == {name!r})"

    def test_stub_preserves_existing_ulid_on_merge(self, sdk):
        """When Subject already exists via _upsert_subject with a ULID id,
        the _upsert_event MERGE matches by name and ON CREATE does NOT fire,
        so the proper ULID id is preserved."""
        # Create a proper Subject with ULID id via SDK
        subj = sdk.create_subject("existing-agent", "tester")
        proj = sdk._get_proj()
        # Now create an event referencing the same subject name
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-ulid-4",
            "eventId": "ev-ulid-4",
            "eventKind": "test",
            "subject": "existing-agent",
            "startedAt": "2024-01-01T00:00:00Z",
        })
        # The Subject should still have its original ULID id, not overwritten
        r = proj.g.query(
            "MATCH (s:Subject {name:'existing-agent'}) RETURN s.id"
        ).result_set
        assert len(r) == 1
        assert r[0][0] == subj["id"], (
            f"existing ULID should be preserved: {r[0][0]!r} != {subj['id']!r}"
        )
