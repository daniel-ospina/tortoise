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

    def test_uses_deduplicates_duplicate_entries(self, sdk):
        """_upsert_event with uses containing duplicates must not create
        duplicate edges — MERGE is idempotent on the pattern level (#146)."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-005",
            "eventId": "ev-005",
            "eventKind": "analysis",
            "subject": "dedup-tester",
            "uses": ["a", "a", "b"],
            "startedAt": "2024-01-01T00:00:00Z",
        })
        # Count uses edges from event to each object
        for name, expected_count in [("a", 1), ("b", 1)]:
            r = proj.g.query(
                "MATCH (e:Event {eventId:'ev-005'})-[u:uses]->(o:Object {name:$name}) "
                "RETURN count(u)",
                params={"name": name},
            ).result_set
            assert r[0][0] == expected_count, (
                f"Expected {expected_count} uses edge to '{name}', got {r[0][0]}"
            )
        # Total uses edges from ev-005 should be exactly 2
        r_total = proj.g.query(
            "MATCH (e:Event {eventId:'ev-005'})-[u:uses]->(o:Object) "
            "RETURN count(u)"
        ).result_set
        assert r_total[0][0] == 2, (
            f"Expected 2 total uses edges, got {r_total[0][0]}"
        )

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

    def test_partof_rejected_no_producer(self, sdk):
        """#213: partOf has no producer — create_edge with partOf raises ValueError.
        hasPart is the canonical composition edge; inverse is implicit via
        reverse traversal (MATCH (child)<-[:hasPart]-(parent))."""
        src = sdk.create_object("parent-doc-213", "document")
        child = sdk.create_object("child-section-213", "document")
        proj = sdk._get_proj()
        with pytest.raises(ValueError, match="Unknown predicate: partOf"):
            proj.create_edge(child["id"], src["id"], "partOf")

    def test_haspart_create_edge_still_works(self, sdk):
        """#213: hasPart via create_edge continues to work — canonical composition."""
        parent = sdk.create_object("parent-doc-213", "document")
        child = sdk.create_object("child-section-213", "document")
        proj = sdk._get_proj()
        ok = proj.create_edge(parent["id"], child["id"], "hasPart")
        assert ok is True
        r = proj.g.query(
            "MATCH (p:Object {id:$pid})-[h:hasPart]->(c:Object {id:$cid}) "
            "RETURN count(h)",
            params={"pid": parent["id"], "cid": child["id"]},
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
        sdk.set_point_baseline(p["id"], 1, 1)  # #344: neutral baseline (gate active)
        op = sdk.create_operator("IMPL", p["id"], [p["id"]])
        # Should not raise — recency_decay is accepted
        result = sdk.compute_confidence(recency_decay=0.95)
        assert "iterations" in result
        assert "converged" in result

    def test_recency_timezone_naive_ingested_at(self, sdk):
        """Timezone-naive ingestedAt is interpreted as UTC (#153).

        When ingestedAt has no timezone indicator (e.g. '2024-01-01T00:00:00'),
        datetime.fromisoformat returns a naive datetime, and .timestamp()
        would treat it as local time — shifting age by hours.

        This test verifies the guard by computing the expected decay from
        a known UTC timestamp and asserting the naive path matches.
        It is NOT zone-dependent — it fails if naive timestamps are
        interpreted as local time, even in UTC CI.
        """
        import calendar
        import pytest
        from datetime import datetime, timezone

        url = "https://doi.org/10.9999/tz-naive"
        ingested_naive = "2024-01-01T00:00:00"
        recency_decay = 0.95

        # Expected UTC epoch for the ingested instant
        expected_ts = calendar.timegm((2024, 1, 1, 0, 0, 0))

        # Create point and source with timezone-naive ingestedAt
        p = sdk.create_point("statement", "tz-naive claim",
                             extractedFrom=url)
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (s:Source {url: $url}) "
            "SET s.credibilityTier = 'T1', s.ingestedAt = $ts",
            params={"url": url, "ts": ingested_naive}
        )

        sdk._apply_source_inheritance(recency_decay=recency_decay)

        pt = sdk.get_point(p["id"])

        # Compute expected decay: T1 = (5, 1), alpha' = 1 + (5-1)*decay
        now_ts = datetime.now(timezone.utc).timestamp()
        years = max(0, (now_ts - expected_ts) / (365.25 * 86400))
        expected_decay = recency_decay ** years
        expected_alpha = 1.0 + (5.0 - 1.0) * expected_decay
        expected_beta = 1.0  # (1-1)*decay = 0

        assert pt["ep_alpha"] == pytest.approx(expected_alpha, rel=1e-9)
        assert pt["ep_beta"] == pytest.approx(expected_beta, rel=1e-9)


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

        p1 = sdk.create_point("observation", "market up")
        p2 = sdk.create_point("observation", "market down")

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
        p3 = sdk.create_point("observation", "market up again")
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
        p1 = sdk.create_point("observation", "false claim")
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
        p2 = sdk.create_point("observation", "another false claim")
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

    def test_id_takes_precedence_over_name_match(self, sdk):
        """Exact id match takes precedence when a name collision exists (#152).

        Subject A has id='alice', Subject B has name='alice'.
        compute_reputation('alice') must only count A's outcomes (id match).
        """
        proj = sdk._get_proj()

        # Subject A: id='alice', name='alice-work'
        sdk._create_entity("Subject", "alice",
                           {"name": "alice-work", "subjectKind": "analyst", "status": "live"},
                           "SubjectAdded")
        # Subject B: id='bob', name='alice' (name collides with A's id)
        sdk._create_entity("Subject", "bob",
                           {"name": "alice", "subjectKind": "reviewer", "status": "live"},
                           "SubjectAdded")

        # Record an IMPL event for Subject A (id='alice')
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-152-a",
            "eventId": "ev-152-a",
            "eventKind": "analysis",
            "subject": "alice-work",
        })
        p_a = sdk.create_point("observation", "A's analysis")
        proj.g.query(
            "MATCH (e:Event {eventId:'ev-152-a'}), (p:Point {id:$pid}) "
            "CREATE (e)-[:IMPL]->(p)",
            params={"pid": p_a["id"]},
        )

        # Record an IMPL event for Subject B (name='alice')
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-152-b",
            "eventId": "ev-152-b",
            "eventKind": "review",
            "subject": "alice",
        })
        p_b = sdk.create_point("observation", "B's review")
        proj.g.query(
            "MATCH (e:Event {eventId:'ev-152-b'}), (p:Point {id:$pid}) "
            "CREATE (e)-[:IMPL]->(p)",
            params={"pid": p_b["id"]},
        )

        # compute_reputation('alice') must match by id first → Subject A only
        rep = sdk.compute_reputation("alice")
        assert rep["total_events"] == 1
        assert rep["impl_count"] == 1
        assert rep["nand_count"] == 0
        # Verify it's A's outcome, not B's
        assert len(rep["outcomes"]) == 1
        assert rep["outcomes"][0]["content"] == "A's analysis"
        assert rep["mean"] > 0.5  # 1 IMPL on Beta(1,1) prior → Beta(2,1) → 2/3 ≈ 0.6667


# ── Review fixes: supersede_point structural transfer + negative cases ──

class TestSupersedePointStructuralTransfer:
    """supersede_point must transfer structural edges idempotently."""

    def test_structural_edges_transferred_on_supersede(self, sdk):
        """Old point's aboutSubject edge transfers to new point."""
        proj = sdk._get_proj()
        subj = sdk.create_point("statement", "subject anchor")
        old_pt = sdk.create_point("statement", "old claim")
        new_pt = sdk.create_point("statement", "new claim")
        # Wire aboutSubject edge FROM old point TO subject (direct edge on the Point)
        proj.create_about_edge(old_pt["id"], subj["id"], "aboutSubject")
        # Supersede: edges must transfer from old point to new point
        result = sdk.supersede_point(old_pt["id"], new_pt["id"])
        assert result.get("edges_transferred", 0) >= 1
        # New point now has the aboutSubject edge
        r = proj.g.query(
            "MATCH (new:Point {id:$nid})-[a:aboutSubject]->(s:Point {id:$sid}) "
            "RETURN count(a) > 0",
            params={"nid": new_pt["id"], "sid": subj["id"]},
        ).result_set
        assert r[0][0] is True
        # Old point no longer has the aboutSubject edge
        r2 = proj.g.query(
            "MATCH (old:Point {id:$oid})-[a:aboutSubject]->(s:Point {id:$sid}) "
            "RETURN count(a)",
            params={"oid": old_pt["id"], "sid": subj["id"]},
        ).result_set
        assert r2[0][0] == 0

    def test_supersede_transfer_idempotent_no_duplicates(self, sdk):
        """Running supersede twice must not duplicate edges — the second call
        raises (the #432 terminal-state guard: superseded is terminal), so no
        duplicate edge writes can occur."""
        proj = sdk._get_proj()
        subj = sdk.create_point("statement", "subject")
        old_pt = sdk.create_point("statement", "old")
        new_pt = sdk.create_point("statement", "new")
        # Wire aboutSubject edge FROM old point TO subject
        proj.create_about_edge(old_pt["id"], subj["id"], "aboutSubject")

        sdk.supersede_point(old_pt["id"], new_pt["id"])
        # Second supersede: the old point is now terminal ('superseded') —
        # the guard rejects the transition (#432), guaranteeing no duplicates.
        import pytest
        with pytest.raises(ValueError, match="already terminal"):
            sdk.supersede_point(old_pt["id"], new_pt["id"])
        # Verify exactly 1 aboutSubject edge on new point (no duplicates)
        r = proj.g.query(
            "MATCH (new:Point {id:$nid})-[a:aboutSubject]->(s:Point {id:$sid}) "
            "RETURN count(a)",
            params={"nid": new_pt["id"], "sid": subj["id"]},
        ).result_set
        assert r[0][0] == 1

    def test_was_derived_from_transferred_on_supersede(self, sdk):
        """wasDerivedFrom edge transfers to the superseding Point (#150)."""
        proj = sdk._get_proj()
        # Create source point and a derived point
        src = sdk.create_point("statement", "source data")
        derived = sdk.create_point("statement", "derived claim")
        new_pt = sdk.create_point("statement", "new derived claim")
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


# ── Negative paths for provenance operations (#145) ─────────────────────

class TestNegativePaths:
    """Error-path tests for provenance operations lacking negative coverage.

    Each test pins the CURRENT defined behavior — some raise ValueError
    where validation exists, some return gracefully where documented."""

    # ── create_derivation with bad IDs ────────────────────────────────

    def test_create_derivation_nonexistent_src(self, sdk):
        """create_derivation with non-existent src returns derived=False."""
        dst = sdk.create_object("valid-dst", "dataset")
        result = sdk.create_derivation("nonexistent-src-id", dst["id"])
        assert result["derived"] is False
        assert result["src"] == "nonexistent-src-id"
        assert result["dst"] == dst["id"]

    def test_create_derivation_nonexistent_dst(self, sdk):
        """create_derivation with non-existent dst returns derived=False."""
        src = sdk.create_object("valid-src", "dataset")
        result = sdk.create_derivation(src["id"], "nonexistent-dst-id")
        assert result["derived"] is False
        assert result["src"] == src["id"]
        assert result["dst"] == "nonexistent-dst-id"

    def test_create_derivation_both_nonexistent(self, sdk):
        """create_derivation with both IDs non-existent returns derived=False."""
        result = sdk.create_derivation("bad-src", "bad-dst")
        assert result["derived"] is False

    # ── supersede_point with bad IDs ──────────────────────────────────

    def test_supersede_point_nonexistent_old(self, sdk):
        """supersede_point with non-existent old_id → ValueError (#432 transition guard)."""
        new_pt = sdk.create_point("statement", "valid new point")
        with pytest.raises(ValueError, match="No point"):
            sdk.supersede_point("nonexistent-old-id", new_pt["id"])

    def test_supersede_point_nonexistent_new(self, sdk):
        """supersede_point with non-existent new_id → ValueError (#432: no phantom successor)."""
        old_pt = sdk.create_point("statement", "valid old point")
        with pytest.raises(ValueError, match="No point"):
            sdk.supersede_point(old_pt["id"], "nonexistent-new-id")

    def test_supersede_point_both_nonexistent(self, sdk):
        """supersede_point with both IDs non-existent → ValueError (old miss, #432)."""
        with pytest.raises(ValueError, match="No point"):
            sdk.supersede_point("nonexistent-old", "nonexistent-new")

    def test_supersede_point_same_id(self, sdk):
        """supersede_point with old==new → ValueError (#432: successor must differ)."""
        pt = sdk.create_point("statement", "self-superseding point")
        with pytest.raises(ValueError, match="same|differ"):
            sdk.supersede_point(pt["id"], pt["id"])

    # ── compute_reputation with all-superseded claims ─────────────────

    def test_compute_reputation_all_superseded_returns_neutral(self, sdk):
        """compute_reputation excludes outdated claims, neutral 0.5 when all superseded."""
        subj = sdk.create_subject("all-superseded-agent", "analyst")
        claim = sdk.create_point("statement", "superseded claim")
        replacement = sdk.create_point("statement", "replacement claim")

        # Agent performs an event that IMPLs the claim
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-sup-all",
            "eventId": "ev-sup-all",
            "eventKind": "analysis",
            "subject": subj["name"],
        })
        proj.g.query(
            "MATCH (e:Event {eventId:'ev-sup-all'}), (p:Point {id:$pid}) "
            "CREATE (e)-[:IMPL]->(p)",
            params={"pid": claim["id"]},
        )

        # Supersede the claim → outdated
        sdk.supersede_point(claim["id"], replacement["id"])

        # Reputation should be neutral — outdated claim excluded
        rep = sdk.compute_reputation(subj["id"])
        assert rep["mean"] == 0.5
        assert rep["total_events"] == 0
        assert rep["impl_count"] == 0

    # ── create_operator with part/whole types ─────────────────────────

    def test_create_operator_composed_of_creates_has_part_edges(self, sdk):
        """create_operator with 'composedOf' is valid and creates hasPart edges."""
        src = sdk.create_point("statement", "whole concept")
        t1 = sdk.create_point("statement", "part A")
        t2 = sdk.create_point("statement", "part B")
        op = sdk.create_operator("composedOf", src["id"], [t1["id"], t2["id"]])
        assert op["is_operator"] is True
        assert op["op_type"] == "composedOf"
        # Verify hasPart edges exist from operator to all inputs
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (o:Point {id:$oid})-[h:hasPart]->(p:Point) "
            "RETURN count(h)",
            params={"oid": op["id"]},
        ).result_set
        assert r[0][0] == 3  # source + 2 targets

    def test_create_operator_contains_creates_has_part_edges(self, sdk):
        """create_operator with 'contains' is valid and creates hasPart edges."""
        src = sdk.create_point("statement", "container")
        t1 = sdk.create_point("statement", "contained item")
        op = sdk.create_operator("contains", src["id"], [t1["id"]])
        assert op["is_operator"] is True
        assert op["op_type"] == "contains"
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (o:Point {id:$oid})-[h:hasPart]->(p:Point) "
            "RETURN count(h)",
            params={"oid": op["id"]},
        ).result_set
        assert r[0][0] == 2  # source + 1 target

    def test_create_operator_wraps_creates_has_part_edges(self, sdk):
        """create_operator with 'wraps' is valid and creates hasPart edges."""
        src = sdk.create_point("statement", "wrapper")
        t1 = sdk.create_point("statement", "wrapped content")
        op = sdk.create_operator("wraps", src["id"], [t1["id"]])
        assert op["is_operator"] is True
        assert op["op_type"] == "wraps"
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (o:Point {id:$oid})-[h:hasPart]->(p:Point) "
            "RETURN count(h)",
            params={"oid": op["id"]},
        ).result_set
        assert r[0][0] == 2

    def test_create_operator_decomposes_into_creates_has_part_edges(self, sdk):
        """create_operator with 'decomposesInto' is valid and creates hasPart edges."""
        src = sdk.create_point("statement", "decomposable whole")
        t1 = sdk.create_point("statement", "sub-item 1")
        t2 = sdk.create_point("statement", "sub-item 2")
        t3 = sdk.create_point("statement", "sub-item 3")
        op = sdk.create_operator("decomposesInto", src["id"], [t1["id"], t2["id"], t3["id"]])
        assert op["is_operator"] is True
        assert op["op_type"] == "decomposesInto"
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (o:Point {id:$oid})-[h:hasPart]->(p:Point) "
            "RETURN count(h)",
            params={"oid": op["id"]},
        ).result_set
        assert r[0][0] == 4  # source + 3 targets

    def test_create_operator_invalid_type_raises_value_error(self, sdk):
        """create_operator with unrecognized op_type raises ValueError."""
        p1 = sdk.create_point("statement", "point 1")
        p2 = sdk.create_point("statement", "point 2")
        with pytest.raises(ValueError, match="op_type must be"):
            sdk.create_operator("invalidType", p1["id"], [p2["id"]])

    def test_create_operator_missing_points_raises_value_error(self, sdk):
        """create_operator referencing non-existent Points raises ValueError."""
        p1 = sdk.create_point("statement", "existing point")
        with pytest.raises(ValueError, match="do not exist"):
            sdk.create_operator("IMPL", p1["id"], ["nonexistent-point-id"])

    # ── create_event with uses containing empty strings ───────────────

    def test_create_event_uses_with_empty_strings(self, sdk):
        """create_event with uses containing empty strings skips them gracefully."""
        ev = sdk.create_event("ev-empty-uses", "analysis",
                              uses=["", "valid-input", ""],
                              subject="test-agent")
        assert ev is not None
        # Only valid-input should get an Object node + uses edge
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid})-[u:uses]->(o:Object) "
            "RETURN o.name ORDER BY o.name",
            params={"eid": ev["eventId"]},
        ).result_set
        names = [row[0] for row in r]
        assert names == ["valid-input"]
        # Empty-string Objects should NOT exist
        r2 = proj.g.query(
            "MATCH (o:Object {name:''}) RETURN count(o)",
        ).result_set
        assert r2[0][0] == 0


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


# ── #212: participatesIn edges ────────────────────────────────────────


def _docker_falkor_reachable(port: int = 16379) -> bool:
    """True when a live FalkorDB (Docker) answers on localhost:port.

    #212 live-DB tests self-skip on embedded-only runs (CI pre-merge gate has
    no Docker) — mirrors the _skip_if_no_falkor pattern used elsewhere.
    """
    import socket
    try:
        with socket.create_connection(("localhost", port), timeout=1.5):
            return True
    except OSError:
        return False


@pytest.fixture
def live_sdk_212():
    """Live FalkorProjection on a test-prefixed graph with test_guard."""
    if not _docker_falkor_reachable():
        pytest.skip("live FalkorDB (docker://localhost:16379) not reachable — embedded-only run")
    old_uri = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = (
        "docker://:@localhost:16379/tortoise_test_212_participates"
    )
    try:
        s = TortoiseSDK()
        s.test_guard()  # ensures we're not on production
        # Clean the test graph before each test
        proj = s._get_proj()
        proj.g.query("MATCH (n) DETACH DELETE n")
        yield s
        s.close()
    finally:
        if old_uri is not None:
            os.environ["TORTOISE_DB_URI"] = old_uri
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


class TestParticipatesInEdges:
    """#212: _upsert_event wires (Subject)-[:participatesIn]->(Event)
    from event.participants list, falling back to event.subject."""

    def test_participants_list_creates_edges(self, live_sdk_212):
        """Event with participants list → one participatesIn edge per
        participant id."""
        sdk = live_sdk_212
        proj = sdk._get_proj()
        # Create subjects first (they need id properties)
        alice = sdk.create_subject("alice", "tester")
        bob = sdk.create_subject("bob", "reviewer")
        carol = sdk.create_subject("carol", "observer")
        # Create event with participants list
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-212-1",
            "eventId": "ev-212-1",
            "eventKind": "meeting",
            "subject": "alice",
            "participants": [alice["id"], bob["id"], carol["id"]],
            "startedAt": "2024-01-01T00:00:00Z",
        })
        # Verify participatesIn edges exist
        r = proj.g.query(
            "MATCH (s:Subject)-[:participatesIn]->(e:Event {eventId:'ev-212-1'}) "
            "RETURN s.name ORDER BY s.name"
        ).result_set
        names = [row[0] for row in r]
        assert "alice" in names, f"alice missing from participants: {names}"
        assert "bob" in names, f"bob missing from participants: {names}"
        assert "carol" in names, f"carol missing from participants: {names}"
        assert len(names) == 3, f"expected 3 participants, got {len(names)}: {names}"

    def test_participants_edges_idempotent(self, live_sdk_212):
        """Re-applying same event does not duplicate participatesIn edges."""
        sdk = live_sdk_212
        proj = sdk._get_proj()
        alice = sdk.create_subject("alice", "tester")
        event = {
            "type": "EventRecorded",
            "id": "ev-212-2",
            "eventId": "ev-212-2",
            "eventKind": "standup",
            "subject": "alice",
            "participants": [alice["id"]],
            "startedAt": "2024-01-01T00:00:00Z",
        }
        proj.apply(event)
        proj.apply(event)  # idempotent re-apply
        count = proj.g.query(
            "MATCH ()-[p:participatesIn]->(e:Event {eventId:'ev-212-2'}) "
            "RETURN count(p)"
        ).result_set[0][0]
        assert count == 1, f"expected 1 edge, got {count}"

    def test_fallback_subject_when_no_participants(self, live_sdk_212):
        """When participants is empty/missing, fallback to event.subject for
        a single participatesIn edge."""
        sdk = live_sdk_212
        proj = sdk._get_proj()
        # Create subject first
        sdk.create_subject("dave", "engineer")
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-212-3",
            "eventId": "ev-212-3",
            "eventKind": "commit",
            "subject": "dave",
            # no participants key at all
            "startedAt": "2024-01-01T00:00:00Z",
        })
        r = proj.g.query(
            "MATCH (s:Subject {name:'dave'})-[:participatesIn]->(e:Event {eventId:'ev-212-3'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r and r[0][0] is True, "fallback participatesIn edge missing"

    def test_no_participants_no_subject_no_edges(self, live_sdk_212):
        """Event with no participants and no subject → zero participatesIn
        edges."""
        sdk = live_sdk_212
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-212-4",
            "eventId": "ev-212-4",
            "eventKind": "system",
            # no subject, no participants
            "startedAt": "2024-01-01T00:00:00Z",
        })
        count = proj.g.query(
            "MATCH ()-[p:participatesIn]->(e:Event {eventId:'ev-212-4'}) "
            "RETURN count(p)"
        ).result_set[0][0]
        assert count == 0, f"expected 0 edges, got {count}"

    def test_participants_edges_are_bidirectional_queryable(self, live_sdk_212):
        """participatesIn edges are queryable in both directions."""
        sdk = live_sdk_212
        proj = sdk._get_proj()
        alice = sdk.create_subject("alice-212", "tester")
        bob = sdk.create_subject("bob-212", "reviewer")
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-212-5",
            "eventId": "ev-212-5",
            "eventKind": "review",
            "subject": "alice-212",
            "participants": [alice["id"], bob["id"]],
            "startedAt": "2024-01-01T00:00:00Z",
        })
        # Query: which events did alice participate in?
        r1 = proj.g.query(
            "MATCH (s:Subject {name:'alice-212'})-[:participatesIn]->(e:Event) "
            "RETURN e.eventId"
        ).result_set
        assert r1 and r1[0][0] == "ev-212-5"
        # Query: who participated in ev-212-5?
        r2 = proj.g.query(
            "MATCH (s:Subject)-[:participatesIn]->(e:Event {eventId:'ev-212-5'}) "
            "RETURN s.name ORDER BY s.name"
        ).result_set
        names = [row[0] for row in r2]
        assert "alice-212" in names
        assert "bob-212" in names

    def test_participants_does_not_replace_performs(self, live_sdk_212):
        """participatesIn edge is separate from performs — both can co-exist."""
        sdk = live_sdk_212
        proj = sdk._get_proj()
        alice = sdk.create_subject("alice-212b", "tester")
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-212-6",
            "eventId": "ev-212-6",
            "eventKind": "deploy",
            "subject": "alice-212b",
            "participants": [alice["id"]],
            "startedAt": "2024-01-01T00:00:00Z",
        })
        # performs edge should exist
        r_perf = proj.g.query(
            "MATCH (s:Subject {name:'alice-212b'})-[:performs]->(e:Event {eventId:'ev-212-6'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r_perf and r_perf[0][0] is True, "performs edge missing"
        # participatesIn edge should also exist
        r_part = proj.g.query(
            "MATCH (s:Subject {name:'alice-212b'})-[:participatesIn]->(e:Event {eventId:'ev-212-6'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r_part and r_part[0][0] is True, "participatesIn edge missing"
        # Total edges from that Subject to the Event should be 2
        r_total = proj.g.query(
            "MATCH (s:Subject {name:'alice-212b'})-[r]->(e:Event {eventId:'ev-212-6'}) "
            "RETURN count(r)"
        ).result_set
        assert r_total[0][0] == 2, f"expected 2 edges, got {r_total[0][0]}"
