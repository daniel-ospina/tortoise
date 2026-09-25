"""#1370 — write-time, confidence-gated, fail-closed Point→Subject binding.

Class B (mechanical architecture conformance). Every test docstring states
**(1) what value makes this test fail?** and **(2) does the fixture contain a
row where that value is reachable?** — the two questions that separate a real
test from an assertion that cannot fail.

The cardinal contract is the NEGATIVE half: **no subject > wrong subject**.
A below-threshold, unknown-kind, or unresolvable slot must leave the point
UNBOUND and must NOT invent a subject; the refusal must be recorded, never
silently dropped. The threshold must be LOAD-BEARING: moving it across a
slot's confidence must flip the outcome.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

import pytest

from tortoise.sdk import TortoiseSDK

CONV = [
    {"role": "user", "content": "the team approved the plan today"},
    {"role": "assistant", "content": "agreed"},
]


@pytest.fixture
def sdk():
    """A fresh SDK on an isolated graph (the #211 embedded/Docker-agnostic
    fixture pattern)."""
    db_path = f"{tempfile.mkdtemp(prefix='tt_1370_')}/test.db"
    s = TortoiseSDK(db_path)
    s.test_guard = lambda: None
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


@pytest.fixture
def journal_sdk(tmp_path):
    """An SDK with a JSONL journal so EntityLinked / EntityBindingRefused land
    and ``rebuild`` has a source of truth (the #3664 harness pattern)."""
    events = tmp_path / "events"
    events.mkdir()
    s = TortoiseSDK(str(tmp_path / "g.db"),
                    event_log_path=str(events / "events.jsonl"))
    s.test_guard = lambda: None
    yield s, events / "events.jsonl"
    s.close()


def _journal(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _edge(sdk, pid: str):
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$pid})-[r:aboutSubject]->(s:Subject) "
        "RETURN s.name, r.confidence",
        params={"pid": pid},
    ).result_set
    return rows


def _bind(sdk, *, pid, slots, tau_hi=None, tau_lo=None):
    from tortoise.subject_binding import bind_point_subjects
    kw = {}
    if tau_hi is not None:
        kw["tau_hi"] = tau_hi
    if tau_lo is not None:
        kw["tau_lo"] = tau_lo
    return bind_point_subjects(sdk._get_proj(), sdk, point_id=pid,
                               slots=slots, **kw)


# ══════════════════════════════════════════════════════════════════════════
# A. Pure decision core
# ══════════════════════════════════════════════════════════════════════════

class TestSubjectKindClassification:
    def test_declared_subject_kinds_are_recognised(self):
        """(1) Fails if ``is_subject_kind`` returns False for a declared kind.
        (2) The fixture rows below ARE the declared `extractor_v2.SUBJECTS`
        keys — the value is reachable by construction.
        """
        from tortoise.subject_binding import is_subject_kind
        for kind in ("core:organization", "core:team", "core:role",
                     "core:legalPerson", "core:naturalPerson"):
            assert is_subject_kind(kind) is True, kind
            assert is_subject_kind(kind.split(":", 1)[-1]) is True, kind

    def test_object_and_unknown_and_other_kinds_are_not_subject(self):
        """(1) Fails if an object kind, an unknown kind, ``core:other``, None
        or "" classifies as a Subject. (2) All five values are rows in this
        fixture — each is reachable.
        """
        from tortoise.subject_binding import is_subject_kind
        assert is_subject_kind("core:plan") is False
        assert is_subject_kind("core:strategy") is False
        assert is_subject_kind("core:other") is False   # the NIL bucket
        assert is_subject_kind("totally:invented") is False
        assert is_subject_kind(None) is False
        assert is_subject_kind("") is False


class TestDecideBinding:
    def test_bound_at_and_above_tau_hi(self):
        """(1) Fails if the outcome at exactly τ_hi, or above it, is not
        ``bound``. (2) Fixture rows: confidence 0.7 and 0.9 with default
        τ_hi=0.7 — reachable.
        """
        from tortoise.subject_binding import BOUND, decide_binding
        assert decide_binding(0.7, tau_hi=0.7, tau_lo=0.4) == BOUND
        assert decide_binding(0.9, tau_hi=0.7, tau_lo=0.4) == BOUND

    def test_suspected_in_band(self):
        """(1) Fails if a confidence inside [τ_lo, τ_hi) is not ``suspected``.
        (2) Fixture rows: 0.4 (τ_lo) and 0.69 — reachable.
        """
        from tortoise.subject_binding import SUSPECTED, decide_binding
        assert decide_binding(0.4, tau_hi=0.7, tau_lo=0.4) == SUSPECTED
        assert decide_binding(0.69, tau_hi=0.7, tau_lo=0.4) == SUSPECTED

    def test_unbound_below_tau_lo(self):
        """(1) Fails if a below-τ_lo confidence is not ``unbound``.
        (2) Fixture rows: 0.39, 0.0 — reachable.
        """
        from tortoise.subject_binding import UNBOUND, decide_binding
        assert decide_binding(0.39, tau_hi=0.7, tau_lo=0.4) == UNBOUND
        assert decide_binding(0.0, tau_hi=0.7, tau_lo=0.4) == UNBOUND

    @pytest.mark.parametrize("bad", [None, "high", float("nan"), True, [], {}])
    def test_non_numeric_confidence_is_unbound(self, bad):
        """(1) Fails if a non-numeric / NaN / bool confidence is coerced
        upward (the fail-OPEN direction). (2) Each ``bad`` value is the row
        under test — reachable by construction.
        """
        from tortoise.subject_binding import UNBOUND, decide_binding
        assert decide_binding(bad, tau_hi=0.7, tau_lo=0.4) == UNBOUND

    def test_threshold_is_load_bearing(self):
        """(1) Fails if the SAME confidence yields the same outcome under two
        different τ_hi — i.e. if the threshold is decorative. (2) The fixture
        row confidence=0.6 sits between τ_hi=0.5 (bound) and τ_hi=0.7
        (suspected) — both values are reachable on that one row.
        """
        from tortoise.subject_binding import BOUND, SUSPECTED, decide_binding
        assert decide_binding(0.6, tau_hi=0.5, tau_lo=0.3) == BOUND
        assert decide_binding(0.6, tau_hi=0.7, tau_lo=0.3) == SUSPECTED


# ══════════════════════════════════════════════════════════════════════════
# B. Binder (real graph + journal)
# ══════════════════════════════════════════════════════════════════════════

class TestBindFailClosed:
    """The NEGATIVE half of the contract — no subject > wrong subject."""

    def test_unbound_when_no_slots(self, sdk):
        """(1) Fails if a point with no slots (or slots=None) gains an edge.
        (2) Fixture rows: slots=None and slots={} — both reachable.
        """
        pid = sdk.create_point("statement", "no slots here")["id"]
        _bind(sdk, pid=pid, slots=None)
        _bind(sdk, pid=pid, slots={})
        assert _edge(sdk, pid) == []

    def test_bound_subject_writes_edge_with_confidence(self, journal_sdk):
        """(1) Fails if a ≥τ_hi subject slot writes no edge, or the edge
        carries no confidence. (2) Fixture row: confidence 0.9 with τ_hi=0.7
        — reachable.
        """
        sdk, jpath = journal_sdk
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "the team shipped")["id"]
        out = _bind(sdk, pid=pid, slots={
            "subject": [{"name": "the team", "kind": "core:team",
                         "confidence": 0.9}]})
        assert out[0]["outcome"] == "bound"
        rows = _edge(sdk, pid)
        assert len(rows) == 1 and rows[0][0] == "the team"
        assert abs(float(rows[0][1]) - 0.9) < 1e-9
        links = [e for e in _journal(jpath) if e.get("type") == "EntityLinked"]
        assert links and abs(float(links[0]["confidence"]) - 0.9) < 1e-9

    def test_below_threshold_writes_no_edge_and_journals_refusal(self, journal_sdk):
        """(1) Fails if a below-τ_lo slot writes an edge (fail-OPEN) OR leaves
        no journal record (a silent drop). (2) Fixture row: confidence 0.1 —
        reachable.
        """
        sdk, jpath = journal_sdk
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "the team maybe shipped")["id"]
        out = _bind(sdk, pid=pid, slots={
            "subject": [{"name": "the team", "kind": "core:team",
                         "confidence": 0.1}]})
        assert out[0]["outcome"] == "unbound"
        assert _edge(sdk, pid) == []
        refused = [e for e in _journal(jpath)
                   if e.get("type") == "EntityBindingRefused"]
        assert refused and refused[0]["confidence"] == 0.1
        assert refused[0]["outcome"] == "unbound"

    def test_suspected_band_writes_no_edge(self, journal_sdk):
        """(1) Fails if the suspected band [τ_lo, τ_hi) writes an edge (it must
        be tracked, not bound). (2) Fixture row: confidence 0.5 with defaults —
        reachable.
        """
        sdk, jpath = journal_sdk
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "the team probably shipped")["id"]
        out = _bind(sdk, pid=pid, slots={
            "subject": [{"name": "the team", "kind": "core:team",
                         "confidence": 0.5}]})
        assert out[0]["outcome"] == "suspected"
        assert _edge(sdk, pid) == []
        refused = [e for e in _journal(jpath)
                   if e.get("type") == "EntityBindingRefused"]
        assert refused and refused[0]["outcome"] == "suspected"

    def test_moved_threshold_flips_the_outcome(self, sdk):
        """(1) Fails if changing τ_hi does not change the recorded outcome —
        the load-bearing check at the BINDER level, not just the pure
        function. (2) Fixture row: confidence 0.6, two τ_hi values around it.
        """
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "the team shipped")["id"]
        low = _bind(sdk, pid=pid, slots={
            "subject": [{"name": "the team", "kind": "core:team",
                         "confidence": 0.6}]}, tau_hi=0.5, tau_lo=0.3)
        assert low[0]["outcome"] == "bound"
        assert _edge(sdk, pid)  # edge written at the lower threshold
        pid2 = sdk.create_point("statement", "the team shipped again")["id"]
        high = _bind(sdk, pid=pid2, slots={
            "subject": [{"name": "the team", "kind": "core:team",
                         "confidence": 0.6}]}, tau_hi=0.7, tau_lo=0.3)
        assert high[0]["outcome"] == "suspected"
        assert _edge(sdk, pid2) == []  # no edge at the higher threshold

    def test_unknown_kind_never_mints_a_subject(self, sdk):
        """(1) Fails if an invented/non-subject kind produces a Subject edge
        or a Subject node (the "never invent" rule). (2) Fixture rows:
        ``totally:invented`` and ``core:plan`` — both reachable.
        """
        pid = sdk.create_point("statement", "an invented subject")["id"]
        out = _bind(sdk, pid=pid, slots={
            "subject": [{"name": "ghost", "kind": "totally:invented",
                         "confidence": 0.99}]})
        assert out[0]["outcome"] == "unbound"
        assert _edge(sdk, pid) == []
        r = sdk._get_proj().g.query(
            "MATCH (s:Subject {name:'ghost'}) RETURN count(s)").result_set
        assert r[0][0] == 0

    def test_unresolved_name_mints_no_stub(self, sdk):
        """(1) Fails if a subject-kind slot naming an entity that does not
        exist mints a Subject stub (the D8 fail-open path). (2) Fixture row:
        kind=core:team, name "nobody" absent from the graph — reachable.
        """
        pid = sdk.create_point("statement", "nobody shipped")["id"]
        out = _bind(sdk, pid=pid, slots={
            "subject": [{"name": "nobody", "kind": "core:team",
                         "confidence": 0.99}]})
        assert out[0]["outcome"] == "unbound"
        assert out[0]["reason"] == "unresolved"
        assert _edge(sdk, pid) == []
        r = sdk._get_proj().g.query(
            "MATCH (s:Subject {name:'nobody'}) RETURN count(s)").result_set
        assert r[0][0] == 0

    @pytest.mark.parametrize("slots", [
        {"subject": "not-a-list"},
        {"subject": [None, "x", 42]},
        {"subject": [{"name": "", "kind": "core:team", "confidence": 0.9}]},
        {"subject": [{"name": "the team", "confidence": 0.9}]},
        [1, 2, 3],
    ])
    def test_malformed_slots_never_raise(self, sdk, slots):
        """(1) Fails if a malformed slots payload raises (sinking the commit).
        (2) Each parametrised value IS the malformed row — reachable.
        """
        pid = sdk.create_point("statement", "malformed")["id"]
        out = _bind(sdk, pid=pid, slots=slots)
        assert isinstance(out, list)
        assert _edge(sdk, pid) == []

    def test_event_slot_is_not_bound(self, sdk):
        """(1) Fails if the ``event`` role produces an ``aboutSubject`` (or any)
        edge from the binder. (2) Fixture row: an event slot with high
        confidence — reachable.
        """
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "the meeting happened")["id"]
        _bind(sdk, pid=pid, slots={
            "event": [{"name": "the Aug 3 meeting", "kind": "core:meeting",
                       "confidence": 0.99}]})
        assert _edge(sdk, pid) == []
        r = sdk._get_proj().g.query(
            "MATCH (:Point {id:$p})-[r]->() RETURN count(r) > 0",
            params={"p": pid}).result_set
        assert r[0][0] is False

    def test_object_slot_binds_about_object_not_subject(self, sdk):
        """(1) Fails if an object slot writes aboutSubject instead of
        aboutObject (the D6 entity-type-agnostic claim). (2) Fixture row:
        core:plan object slot at 0.9 — reachable.
        """
        sdk.create_entity("object", "the plan", objectKind="core:plan")
        pid = sdk.create_point("statement", "the plan shipped")["id"]
        out = _bind(sdk, pid=pid, slots={
            "object": [{"name": "the plan", "kind": "core:plan",
                        "confidence": 0.9}]})
        assert out[0]["outcome"] == "bound"
        assert _edge(sdk, pid) == []
        rows = sdk._get_proj().g.query(
            "MATCH (:Point {id:$p})-[:aboutObject]->(o:Object {name:'the plan'}) "
            "RETURN count(o) > 0", params={"p": pid}).result_set
        assert rows[0][0] is True


class TestReplayParity:
    def test_rebuild_reproduces_edge_confidence(self, journal_sdk):
        """(1) Fails if a wipe+rebuild loses the edge OR its confidence — the
        live==rebuild contract. (2) Fixture row: one bound slot at 0.9 on a
        journal-configured SDK — reachable.
        """
        sdk, jpath = journal_sdk
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "the team shipped")["id"]
        _bind(sdk, pid=pid, slots={
            "subject": [{"name": "the team", "kind": "core:team",
                         "confidence": 0.9}]})
        before = _edge(sdk, pid)
        assert before
        sdk._get_proj().rebuild_all(str(jpath.parent))
        after = _edge(sdk, pid)
        assert after == before  # name + confidence byte-identical

    def test_confidence_not_cleared_by_a_later_record_without_it(self, journal_sdk):
        """(1) Fails if a second EntityLinked for the same edge that omits
        confidence clears it on replay (unconditional SET). (2) Fixture: the
        same edge is linked twice, the second time with confidence=None —
        reachable.
        """
        from tortoise.session_link import link_entity
        sdk, jpath = journal_sdk
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "the team shipped")["id"]
        proj = sdk._get_proj()
        rows = proj.g.query("MATCH (s:Subject {name:'the team'}) RETURN s.id").result_set
        sid = rows[0][0]
        link_entity(proj, "Point", pid, sid, "aboutSubject", "Subject",
                    sdk=sdk, confidence=0.9)
        link_entity(proj, "Point", pid, sid, "aboutSubject", "Subject",
                    sdk=sdk)  # no confidence
        sdk._get_proj().rebuild_all(str(jpath.parent))
        rows = _edge(sdk, pid)
        assert rows and abs(float(rows[0][1]) - 0.9) < 1e-9


# ══════════════════════════════════════════════════════════════════════════
# C. Seam integration (the v2 capture write path)
# ══════════════════════════════════════════════════════════════════════════

_SLOTS_PAYLOAD = {
    "entities": [
        {"name": "the team", "kind": "core:team", "lifecycle": "created",
         "supersedes": None, "note": None},
        {"name": "the plan", "kind": "core:plan", "lifecycle": "created",
         "supersedes": None, "note": None},
    ],
    "events": [],
    "points": [{
        "content": "the team approved the plan", "pointKind": "statement",
        "about_entities": ["the team", "the plan"],
        "slots": {"subject": [{"name": "the team", "kind": "core:team",
                               "confidence": 0.9}],
                  "object": [{"name": "the plan", "kind": "core:plan",
                              "confidence": 0.9}]},
    }],
    "operators": [], "chain_notes": [], "link_before_create": [],
}


class _SlotsMock:
    """Offline v2 extractor stand-in that emits participant slots (#1370)."""

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        if "STORY SUMMARIZER" in system:
            return "The session revealed a team decision."
        return json.dumps(_SLOTS_PAYLOAD)


class TestCaptureSeam:
    @pytest.fixture(autouse=True)
    def _slots_extractor(self, monkeypatch):
        import tortoise.sdk as sdk_mod
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        monkeypatch.setattr(sdk_mod, "_V2SessionMock", _SlotsMock)

    def test_capture_binds_subject_and_routes_kind_to_subject(self, journal_sdk):
        """(1) Fails if the capture seam drops the slots (no aboutSubject
        edge), or if the subject-kind entity is minted as an Object. (2)
        Fixture: the payload above (core:team subject slot at 0.9) — reachable.
        """
        sdk, jpath = journal_sdk
        r = sdk.capture_session(CONV, session_id="s1370")
        assert r["ok"] is True, r
        proj = sdk._get_proj()
        pid = proj.g.query(
            "MATCH (p:Point) WHERE p.content = 'the team approved the plan' "
            "RETURN p.id").result_set[0][0]
        rows = _edge(sdk, pid)
        assert rows and rows[0][0] == "the team", rows
        # kind routing: the team is a :Subject, not an :Object
        r1 = proj.g.query(
            "MATCH (s:Subject {name:'the team'}) RETURN count(s)").result_set
        r2 = proj.g.query(
            "MATCH (o:Object {name:'the team'}) RETURN count(o)").result_set
        assert r1[0][0] == 1 and r2[0][0] == 0
        # the plan stays an :Object
        r3 = proj.g.query(
            "MATCH (o:Object {name:'the plan'}) RETURN count(o)").result_set
        assert r3[0][0] == 1
        # binding is journaled
        links = [e for e in _journal(jpath)
                 if e.get("type") == "EntityLinked"
                 and e.get("edge_type") == "aboutSubject"]
        assert links

    def test_about_entities_subject_name_writes_no_ungated_subject_edge(
            self, journal_sdk):
        """(1) Fails if the legacy about_entities channel emits aboutSubject
        (un-gated) or mints an id-less Object stub for a subject-kind name.
        (2) Fixture: the payload's about_entities includes "the team"
        (core:team) — reachable.
        """
        sdk, _ = journal_sdk
        r = sdk.capture_session(CONV, session_id="s1370b")
        assert r["ok"] is True, r
        proj = sdk._get_proj()
        # exactly ONE aboutSubject edge (from the binder), and no aboutObject
        # edge pointing at a stub named "the team"
        n1 = proj.g.query(
            "MATCH (:Point)-[:aboutSubject]->(:Subject {name:'the team'}) "
            "RETURN count(*)").result_set[0][0]
        assert n1 == 1
        n2 = proj.g.query(
            "MATCH (:Point)-[:aboutObject]->(o:Object {name:'the team'}) "
            "RETURN count(*)").result_set[0][0]
        assert n2 == 0

    def test_read_surface_surfaces_the_bound_subject(self, journal_sdk):
        """(1) Fails if #1353's decoration does not surface the newly-written
        edge as ``subject`` (or still emits the unavailable marker). (2)
        Fixture: the payload above — reachable.
        """
        from tortoise.search_engine import fetch_point_epistemic_state
        sdk, _ = journal_sdk
        sdk.capture_session(CONV, session_id="s1370c")
        proj = sdk._get_proj()
        pid = proj.g.query(
            "MATCH (p:Point) WHERE p.content = 'the team approved the plan' "
            "RETURN p.id").result_set[0][0]
        state = fetch_point_epistemic_state(proj.g, [pid])
        assert state[pid]["subject"]["name"] == "the team"


# ══════════════════════════════════════════════════════════════════════════
# D. Audit tooling + quality gate
# ══════════════════════════════════════════════════════════════════════════

GOLD = os.path.join(os.path.dirname(__file__), "fixtures",
                    "subject_binding_gold.jsonl")


class TestQualityGate:
    def test_fixture_has_rows_that_can_fail(self):
        """(1) Fails if the fixture is empty or has no SHOULD-STAY-UNBOUND row
        (a fixture that can only pass cannot fail). (2) The fixture file is
        the artifact under test.
        """
        with open(GOLD) as fh:
            rows = [json.loads(ln) for ln in fh if ln.strip()]
        assert rows
        assert any(r.get("gold_subject") is None for r in rows)
        assert any(r.get("gold_subject") for r in rows)

    def test_default_thresholds_meet_the_misattribution_target(self):
        """(1) Fails if the documented misattribution rate exceeds 10% at the
        shipped default thresholds. (2) The fixture's wrong/unbound rows make
        the rate non-zero and reachable.
        """
        from tortoise.subject_binding import quality_gate_metrics
        metrics = quality_gate_metrics(gold_path=GOLD)
        assert metrics["misattribution_rate"] <= 0.10, metrics

    def test_tightening_the_threshold_cannot_increase_misattribution(self):
        """(1) Fails if raising τ_hi makes misattribution WORSE — the
        monotonic fail-closed property. (2) Fixture rows at 0.5/0.6 sit
        between the two thresholds — reachable.
        """
        from tortoise.subject_binding import quality_gate_metrics
        low = quality_gate_metrics(gold_path=GOLD, tau_hi=0.4, tau_lo=0.3)
        high = quality_gate_metrics(gold_path=GOLD, tau_hi=0.9, tau_lo=0.3)
        assert high["misattribution_rate"] <= low["misattribution_rate"]
        assert high["unbound_rate"] >= low["unbound_rate"]


class TestGraphAudit:
    def test_audit_reports_bound_and_unbound(self, sdk):
        """(1) Fails if the audit reports a wrong bound count or omits the
        journal-derived unbound fraction. (2) Fixture: one bound + one
        refused slot — both reachable.
        """
        from tortoise.subject_binding import audit_subject_binding
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        p1 = sdk.create_point("statement", "bound one")["id"]
        p2 = sdk.create_point("statement", "unbound one")["id"]
        _bind(sdk, pid=p1, slots={"subject": [
            {"name": "the team", "kind": "core:team", "confidence": 0.9}]})
        _bind(sdk, pid=p2, slots={"subject": [
            {"name": "the team", "kind": "core:team", "confidence": 0.1}]})
        rep = audit_subject_binding(sdk._get_proj().g)
        assert rep["bound"] == 1
        assert rep["points_total"] >= 2


# ══════════════════════════════════════════════════════════════════════════
# E. Hosted commit seam parity + the entity-supersession guard
# ══════════════════════════════════════════════════════════════════════════

def _hosted_payload_and_plan(content: str, sid: str):
    """A minimal valid CommitPayload/CommitPlan carrying one point with a
    subject+object slot pair, mirroring tests/test_commit_supersession_parity.
    """
    from tortoise.commit_schema import (
        BudgetDecision,
        CommitPayload,
        CommitPlan,
        Entity,
        EntityReconcile,
        ExtractorInfo,
        Point,
        PointReconcile,
        ReconcileResult,
        Telemetry,
        TelemetryCounts,
        TelemetryExtractor,
        TelemetryModel,
        point_content_id,
    )

    pid = point_content_id(content)
    pt = Point(
        id=pid, content=content, pointKind="statement", reason="NEW",
        confidence=0.9, c_cal=0.8, about_entities=["the team", "the plan"],
        source_ref="session.md", quote="", status="live",
        slots={"subject": [{"name": "the team", "kind": "core:team",
                            "confidence": 0.9}],
               "object": [{"name": "the plan", "kind": "core:plan",
                           "confidence": 0.9}]},
    )
    ent1 = Entity(name="the team", kind="core:team", passes_frequency_gate=True)
    ent2 = Entity(name="the plan", kind="core:plan", passes_frequency_gate=True)
    payload = CommitPayload(
        schema_version="1", session_id=sid, client_commit_id="ccid-1370",
        captured_at="2026-09-24T00:00:00Z",
        extractor=ExtractorInfo(version="value@1.0.0", mode="byok",
                                calibration_version="v3"),
        summary="s", story_arc="", provenance_refs=[], sources=[], events=[],
        entities=[ent1, ent2], points=[pt], operators=[], supersessions=[],
        telemetry=Telemetry(
            extractor=TelemetryExtractor(version="value@1.0.0", mode="byok",
                                         calibration_version="v3"),
            model=TelemetryModel(provider="x", id="y", cfg_hash="h"),
            counts=TelemetryCounts(kept=1, candidate=1, segment=1, window=1,
                                   empty_windows=0),
            keep_ratio=1.0, dedup_hits=0),
    )
    reconcile = ReconcileResult(
        points=[PointReconcile(point=pt, action="new")],
        entities=[EntityReconcile(entity=ent1, action="new"),
                  EntityReconcile(entity=ent2, action="new")],
    )
    plan = CommitPlan(payload=payload, duplicate=False, first_adjudication=True,
                      reconcile=reconcile,
                      budget=BudgetDecision(outcome="ok", cumulative_after=1))
    return pid, payload, plan


class TestHostedCommitSeam:
    def test_hosted_commit_binds_and_routes_like_the_local_seam(self, sdk):
        """(1) Fails if the hosted commit seam drops the slots (no edge), or
        does NOT route the core:team entity to :Subject, or re-emits the
        un-gated aboutSubject from about_entities. (2) The payload above
        carries a subject slot at 0.9 AND "the team" in about_entities — both
        reachable.
        """
        from tortoise import hosted_api
        pid, payload, plan = _hosted_payload_and_plan(
            "the team approved the plan", "s-hosted-1370")
        hosted_api._execute_commit_writes(sdk, payload, plan)
        g = sdk._get_proj().g
        rows = g.query(
            "MATCH (:Point {id:$p})-[r:aboutSubject]->(s:Subject) "
            "RETURN s.name, r.confidence", params={"p": pid}).result_set
        assert len(rows) == 1 and rows[0][0] == "the team", rows
        assert abs(float(rows[0][1]) - 0.9) < 1e-9
        assert g.query("MATCH (s:Subject {name:'the team'}) RETURN count(s)"
                       ).result_set[0][0] == 1
        assert g.query("MATCH (o:Object {name:'the team'}) RETURN count(o)"
                       ).result_set[0][0] == 0
        # the object slot binds as aboutObject, never aboutSubject
        assert g.query(
            "MATCH (:Point {id:$p})-[:aboutObject]->(o:Object {name:'the plan'}) "
            "RETURN count(o) > 0", params={"p": pid}).result_set[0][0] is True


class TestEntitySupersessionGuard:
    def test_subject_kind_successor_is_reported_accurately(self, sdk):
        """(1) Fails if a subject-kind successor produces the generic
        "dangling successor" message (misdiagnosing a correctly-typed node as
        missing) or is silently dropped. (2) Fixture: a supersession record
        whose successor IS a :Subject — reachable.
        """
        from tortoise.commit_ops import apply_supersessions
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        msgs: list[str] = []
        apply_supersessions(
            sdk._get_proj(), sdk,
            [{"superseded": "old thing", "supersedes_by": "the team",
              "evidence": "test"}],
            session_id="s-sup-1370",
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        assert any(":Subject" in m for m in msgs), msgs
        assert not any("dangling successor" in m for m in msgs), msgs
