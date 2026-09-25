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
import logging
import os
import shutil
import tempfile

import pytest

from tortoise.sdk import TortoiseSDK

CONV = [
    {"role": "user", "content": "the team approved the plan today"},
    {"role": "assistant", "content": "agreed"},
]


def _make_slots_mock(payload: dict):
    """Offline v2 extractor stand-in returning ``payload`` from a factory."""
    class _Mock:
        def complete(self, *, system: str, user: str,
                     max_tokens: int | None = None) -> str:
            if "STORY SUMMARIZER" in system:
                return "The session revealed a team decision."
            return json.dumps(payload)
    return _Mock


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

    @pytest.mark.parametrize("kind", [
        "acme:team", "pack:role", "x:organization",
        "evil.org:naturalPerson", ":team",
    ])
    def test_foreign_namespace_kinds_are_not_subject(self, kind):
        """(1) Fails if an arbitrary namespace is stripped to a bare declared
        name and accepted — the T2 bypass (a foreign kind minted into a
        Subject). (2) No fixture row is needed: ``kind`` IS the adversarial
        value, reachable by construction.
        """
        from tortoise.subject_binding import is_subject_kind
        assert is_subject_kind(kind) is False, kind

    @pytest.mark.parametrize("kind", [" core:team ", "core:team\n", " team "])
    def test_whitespace_variants_are_not_subject(self, kind):
        """(1) Fails if ``is_subject_kind`` normalizes whitespace (a
        `kind.strip()` before the exact-set test): the module docstring and
        scoping D2 declare a case/WHITESPACE variant a non-subject, and
        fail-closed is this module's contract. (2) Each ``kind`` IS the
        adversarial value — reachable by construction.
        """
        from tortoise.subject_binding import is_subject_kind
        assert is_subject_kind(kind) is False, kind
        assert is_subject_kind("core:team") is True
        assert is_subject_kind("team") is True

    def test_subject_kind_vocabulary_is_pinned_to_the_extractor(self):
        """(1) Fails if ``subject_kind_names()`` drifts from the declared
        ``extractor_v2.SUBJECTS`` vocabulary, if ``other`` becomes bindable,
        or if a foreign namespace is accepted (F12/F2). (2) The extractor's own
        declaration is the artifact under test — reachable by construction.
        """
        from tortoise.extractor_v2 import SUBJECTS
        from tortoise.subject_binding import is_subject_kind, subject_kind_names
        declared = {k for k in SUBJECTS
                    if k.strip().split(":", 1)[-1].lower() != "other"}
        assert subject_kind_names() == declared
        assert "other" not in subject_kind_names()
        assert is_subject_kind("core:other") is False
        assert is_subject_kind("other") is False


class TestThresholdValidation:
    @pytest.mark.parametrize("bad", ["0", "-1", "nan", "inf"])
    def test_out_of_range_env_tau_hi_fails_closed(self, monkeypatch, bad):
        """(1) Fails if a parseable but out-of-range/non-finite
        ``TORTOISE_SUBJECT_TAU_HI`` is accepted (``0`` makes a zero-confidence
        slot bound — the F11 fail-open). (2) ``bad`` is the value under test.
        """
        from tortoise.subject_binding import (
            DEFAULT_TAU_HI,
            DEFAULT_TAU_LO,
            resolve_thresholds,
        )
        monkeypatch.setenv("TORTOISE_SUBJECT_TAU_HI", bad)
        assert resolve_thresholds() == (DEFAULT_TAU_HI, DEFAULT_TAU_LO)

    def test_inverted_pair_fails_closed(self):
        """(1) Fails if ``tau_lo > tau_hi`` is accepted (collapsing the
        suspected band). (2) The inverted pair is the value under test.
        """
        from tortoise.subject_binding import (
            DEFAULT_TAU_HI,
            DEFAULT_TAU_LO,
            resolve_thresholds,
        )
        assert resolve_thresholds(0.3, 0.6) == (DEFAULT_TAU_HI, DEFAULT_TAU_LO)
        assert resolve_thresholds(float("nan"), 0.4) == (
            DEFAULT_TAU_HI, DEFAULT_TAU_LO)

    def test_zero_env_tau_hi_cannot_bind_a_zero_confidence_slot(
            self, monkeypatch, sdk):
        """(1) Fails if ``TAU_HI=0`` (rejected to the default) still lets a
        zero-confidence subject slot write an edge — the fail-OPEN
        direction F11 names. (2) Fixture row: confidence 0.0 on a resolvable
        subject — reachable.
        """
        from tortoise.subject_binding import BOUND
        monkeypatch.setenv("TORTOISE_SUBJECT_TAU_HI", "0")
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "zero confidence")["id"]
        out = _bind(sdk, pid=pid, slots={"subject": [
            {"name": "the team", "kind": "core:team", "confidence": 0.0}]})
        assert out[0]["outcome"] != BOUND
        assert _edge(sdk, pid) == []

    def test_all_zero_threshold_pair_fails_closed(self, monkeypatch, sdk):
        """(1) Fails if the `(0.0, 0.0)` pair passes the range check: under it
        a 0.0-confidence subject slot becomes `bound` and writes an edge — the
        gate fails OPEN (G3). (2) The env pair is the value under test, and
        the seeded Subject + 0.0 slot is the reachable binding.
        """
        from tortoise.subject_binding import (
            BOUND,
            DEFAULT_TAU_HI,
            DEFAULT_TAU_LO,
            resolve_thresholds,
        )
        monkeypatch.setenv("TORTOISE_SUBJECT_TAU_HI", "0.0")
        monkeypatch.setenv("TORTOISE_SUBJECT_TAU_LO", "0.0")
        assert resolve_thresholds() == (DEFAULT_TAU_HI, DEFAULT_TAU_LO)
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "zero pair")["id"]
        out = _bind(sdk, pid=pid, slots={"subject": [
            {"name": "the team", "kind": "core:team", "confidence": 0.0}]})
        assert out[0]["outcome"] != BOUND
        assert _edge(sdk, pid) == []


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

    def test_unbound_when_no_slots(self, sdk, caplog):
        """(1) Fails if the ``slots is None`` guard is removed: without it a
        None payload falls through to the wrong-container warning (the
        observable the guard suppresses), while an empty dict is a valid
        container and warns nothing. (2) The values under test are ``None``
        and ``{}`` — both reachable.
        """
        pid = sdk.create_point("statement", "no slots here")["id"]
        with caplog.at_level(logging.WARNING, logger="tortoise.subject_binding"):
            assert _bind(sdk, pid=pid, slots=None) == []
            assert _bind(sdk, pid=pid, slots={}) == []
        assert _edge(sdk, pid) == []
        assert not any(
            "neither a dict nor a slot model" in r.getMessage()
            for r in caplog.records), [r.getMessage() for r in caplog.records]

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
        """(1) Fails if the binder's D2 kind gate is disabled: the seeded
        RESOLVABLE ``:Subject`` named exactly as the slot means the only thing
        keeping this slot unbound is the undeclared kind — the refusal reason
        becomes ``kind_mismatch`` (or the slot even binds), never
        ``kind_not_subject``. The old test could not fail by mutation because
        its name resolved to nothing (unbound via ``unresolved``). (2) The
        seeded ``ghost`` Subject and the slot name agree, so disabling the
        gate flips the reason — mutation-verified.
        """
        sdk.create_entity("subject", "ghost", subjectKind="core:team")
        pid = sdk.create_point("statement", "an invented subject")["id"]
        out = _bind(sdk, pid=pid, slots={
            "subject": [{"name": "ghost", "kind": "totally:invented",
                         "confidence": 0.99}]})
        assert out[0]["outcome"] == "unbound"
        assert out[0]["reason"] == "kind_not_subject"
        assert _edge(sdk, pid) == []
        r = sdk._get_proj().g.query(
            "MATCH (s:Subject {name:'ghost'}) RETURN count(s)").result_set
        assert r[0][0] == 1  # the seeded node, not an invented one

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

    def test_nonexistent_point_id_cannot_emit_a_phantom_link(self, journal_sdk):
        """(1) Fails if `link_entity(...) == 0` is read as "already present":
        with a Point id that addresses NOTHING the edge exists nowhere and the
        journal must carry a REFUSAL, not an EntityLinked (the phantom the F7
        branch emitted, G1). (2) The bogus id is the value under test; the
        resolvable Subject is the reachable target.
        """
        sdk, jpath = journal_sdk
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        out = _bind(sdk, pid="pt_does_not_exist", slots={"subject": [
            {"name": "the team", "kind": "core:team", "confidence": 0.9}]})
        assert out[0]["outcome"] == "unbound"
        assert out[0]["reason"] == "unresolved"
        assert [e for e in _journal(jpath)
                if e.get("type") == "EntityLinked"] == []
        refused = [e for e in _journal(jpath)
                   if e.get("type") == "EntityBindingRefused"]
        assert refused and refused[0]["reason"] == "unresolved"

    def test_event_id_only_subject_stub_cannot_produce_a_phantom_link(
            self, journal_sdk):
        """(1) Fails if `_resolve_target` advertises an id-less stub's
        `eventId` as a bindable target while `link_entity` matches on
        `{id:...}`: `link_entity` then finds no endpoint and the F7 branch
        emitted a phantom EntityLinked + `bound`. The stub must be unresolved.
        (2) The eventId-only `:Subject` stub is the value under test; the slot
        names it.
        """
        sdk, jpath = journal_sdk
        from tortoise.subject_binding import _resolve_target
        sdk._get_proj().g.query(
            "MERGE (s:Subject {name:'legacy team'}) "
            "SET s.subjectKind='core:team', s.eventId='ev_legacy'")
        # The addressability contract itself: naming an `eventId` as a target
        # is what produced the phantom link, so restoring that fallback must
        # fail HERE and not only in the sibling test.
        assert _resolve_target(sdk._get_proj(), "Subject",
                               "legacy team")[0] is None
        pid = sdk.create_point("statement", "legacy stub")["id"]
        out = _bind(sdk, pid=pid, slots={"subject": [
            {"name": "legacy team", "kind": "core:team", "confidence": 0.9}]})
        assert out[0]["outcome"] == "unbound"
        assert out[0]["reason"] == "unresolved"
        assert _edge(sdk, pid) == []
        assert [e for e in _journal(jpath)
                if e.get("type") == "EntityLinked"] == []

    def test_whitespace_variant_kind_cannot_bind_at_the_boundary(self, sdk):
        """(1) Fails if the slot reader normalizes the kind before the D2 gate
        (a `kind.strip()` in `_entry_fields`): `is_subject_kind` compares the
        RAW declared key, so stripping upstream hands the gate a value the
        payload never carried and a declared T2 whitespace variant binds an
        `aboutSubject` edge anyway. (2) The seeded resolvable `:Subject` named
        exactly as the slot means only the raw-kind gate keeps this unbound —
        the value under test is the slot's kind.
        """
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "ws team shipped")["id"]
        out = _bind(sdk, pid=pid, slots={"subject": [
            {"name": "the team", "kind": " core:team ",
             "confidence": 0.99}]})
        assert out[0]["outcome"] == "unbound"
        assert out[0]["reason"] == "kind_not_subject"
        assert _edge(sdk, pid) == []

    def test_resolve_target_refuses_an_unaddressable_event_id(self, sdk):
        """(1) Fails if `_resolve_target` returns an id-less stub's `eventId`
        — an id `link_entity` cannot match on `{id:...}`, so naming it as a
        target is what produced the phantom link (G1). (2) The stub is created
        below and IS the value under test.
        """
        from tortoise.subject_binding import _resolve_target
        proj = sdk._get_proj()
        proj.g.query(
            "MERGE (s:Subject {name:'legacy team'}) "
            "SET s.subjectKind='core:team', s.eventId='ev_legacy'")
        tid, _stored = _resolve_target(proj, "Subject", "legacy team")
        assert tid is None

    @pytest.mark.parametrize("slots", [
        {"subject": "not-a-list"},
        {"subject": [None, "x", 42]},
        {"subject": [{"name": "", "kind": "core:team", "confidence": 0.9}]},
        {"subject": [{"name": "the team", "confidence": 0.9}]},
        [1, 2, 3],
    ])
    def test_malformed_slots_never_raise_and_are_not_silent(
            self, journal_sdk, caplog, slots):
        """(1) Fails if a malformed slots payload raises (sinking the commit)
        OR is dropped with neither a warning nor a refusal record — T4/D6
        forbid the silent drop. (2) Each parametrised value IS the malformed
        row — reachable.
        """
        sdk, jpath = journal_sdk
        pid = sdk.create_point("statement", "malformed")["id"]
        with caplog.at_level(logging.WARNING, logger="tortoise.subject_binding"):
            out = _bind(sdk, pid=pid, slots=slots)
        assert isinstance(out, list)
        assert _edge(sdk, pid) == []
        warned = any(r.name == "tortoise.subject_binding"
                     and r.levelno >= logging.WARNING for r in caplog.records)
        refused = [e for e in _journal(jpath)
                   if e.get("type") == "EntityBindingRefused"]
        assert warned or refused, (caplog.records, refused)

    def test_malformed_entry_is_refused_with_a_warning(self, journal_sdk, caplog):
        """(1) Fails if a non-dict entry or a blank name is silently skipped
        (no warning, no journaled refusal) — the F15 dead-except/silent-drop
        defect. (2) The four malformed entries below are the values under test
        — reachable by construction.
        """
        sdk, jpath = journal_sdk
        pid = sdk.create_point("statement", "malformed entry")["id"]
        with caplog.at_level(logging.WARNING, logger="tortoise.subject_binding"):
            out = _bind(sdk, pid=pid, slots={"subject": [
                None, "x", 42,
                {"name": "", "kind": "core:team", "confidence": 0.9}]})
        assert len(out) == 4
        assert all(o["reason"] == "malformed_entry" for o in out)
        refusals = [e for e in _journal(jpath)
                    if e.get("type") == "EntityBindingRefused"
                    and e.get("reason") == "malformed_entry"]
        assert len(refusals) == 4
        assert sum(1 for r in caplog.records
                   if r.name == "tortoise.subject_binding") >= 4

    def test_slot_kind_mismatch_with_the_stored_kind_is_refused(self, sdk):
        """(1) Fails if a slot whose declared kind disagrees with the resolved
        node's stored ``subjectKind`` still binds (F10) — the read surface
        would then report a ``kind`` contradicting the edge. (2) Fixture row
        g23 (slot ``core:organization`` against a ``core:naturalPerson`` node)
        is exactly this value.
        """
        sdk.create_entity("subject", "casey", subjectKind="core:naturalPerson")
        pid = sdk.create_point("statement", "casey the org")["id"]
        out = _bind(sdk, pid=pid, slots={"subject": [
            {"name": "casey", "kind": "core:organization",
             "confidence": 0.99}]})
        assert out[0]["outcome"] == "unbound"
        assert out[0]["reason"] == "kind_mismatch"
        assert _edge(sdk, pid) == []
        # The normalized forms (`core:naturalPerson` vs bare `naturalPerson`)
        # are NOT a mismatch and must still bind.
        pid2 = sdk.create_point("statement", "casey the person")["id"]
        out2 = _bind(sdk, pid=pid2, slots={"subject": [
            {"name": "casey", "kind": "naturalPerson",
             "confidence": 0.99}]})
        assert out2[0]["outcome"] == "bound", out2
        assert _edge(sdk, pid2)

    def test_binder_reports_already_present_and_sets_confidence(self, journal_sdk):
        """(1) Fails if an edge already written by the legacy ``about_entities``
        channel (no confidence) is short-circuited by ``link_entity``'s
        pre-probe and the object slot's confidence is silently discarded (F7);
        and if the outcome is mislabelled ``linked`` when nothing was created.
        (2) Fixture row: object slot 0.9 with the edge pre-written — reachable.
        """
        sdk, jpath = journal_sdk
        sdk.create_entity("object", "the plan", objectKind="core:plan")
        pid = sdk.create_point("statement", "the plan shipped")["id"]
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (o:Object {name:'the plan'}) RETURN o.id").result_set
        oid = rows[0][0]
        from tortoise.session_link import link_entity
        assert link_entity(proj, "Point", pid, oid, "aboutObject", "Object",
                           sdk=sdk) == 1
        out = _bind(sdk, pid=pid, slots={
            "object": [{"name": "the plan", "kind": "core:plan",
                        "confidence": 0.9}]})
        assert out[0]["outcome"] == "bound"
        assert out[0]["reason"] == "already_present"
        assert out[0]["created"] == 0
        rows = proj.g.query(
            "MATCH (:Point {id:$p})-[r:aboutObject]->(o:Object {name:'the plan'}) "
            "RETURN r.confidence", params={"p": pid}).result_set
        assert rows and abs(float(rows[0][0]) - 0.9) < 1e-9
        # The refresh is journaled with the confidence so live == rebuild.
        recs = [e for e in _journal(jpath)
                if e.get("type") == "EntityLinked"
                and e.get("edge_type") == "aboutObject"
                and e.get("confidence") is not None]
        assert recs and abs(float(recs[-1]["confidence"]) - 0.9) < 1e-9

    def test_event_slot_is_not_bound(self, sdk):
        """(1) Fails if the ``event`` role is added to ``_ROLE_TARGET``: a
        RESOLVABLE ``:Subject`` named exactly like the event slot means an
        added role would return a non-empty outcome list (and mint an edge).
        (2) The seeded same-named Subject is the value under test; the event
        slot carries it at 0.99.
        """
        sdk.create_entity("subject", "the Aug 3 meeting",
                          subjectKind="core:team")
        pid = sdk.create_point("statement", "the meeting happened")["id"]
        out = _bind(sdk, pid=pid, slots={
            "event": [{"name": "the Aug 3 meeting", "kind": "core:meeting",
                       "confidence": 0.99}]})
        assert out == []  # the event role is never processed
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
        """(1) Fails if a LATER no-confidence ``EntityLinked`` for the same
        edge clears the confidence on replay (an unconditional SET in the
        fold). (2) The journal is built BY HAND with a confidenced record
        FOLLOWED BY a no-confidence record for the same triple — the pair the
        previous version of this test never actually journaled (the second
        ``link_entity`` short-circuited at the pre-probe), so it could not
        fail (F16).
        """
        sdk, jpath = journal_sdk
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "the team shipped")["id"]
        _bind(sdk, pid=pid, slots={
            "subject": [{"name": "the team", "kind": "core:team",
                         "confidence": 0.9}]})
        rows = sdk._get_proj().g.query(
            "MATCH (s:Subject {name:'the team'}) RETURN s.id").result_set
        sid = rows[0][0]
        # The no-confidence record the live writer never emits once the edge
        # exists — appended by hand so the FOLD is the thing under test.
        with open(jpath, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "EntityLinked", "id": pid, "source_id": pid,
                "source_label": "Point", "target_id": sid,
                "target_label": "Subject", "edge_type": "aboutSubject",
            }) + "\n")
        sdk._get_proj().rebuild_all(str(jpath.parent))
        rows = _edge(sdk, pid)
        assert rows and abs(float(rows[0][1]) - 0.9) < 1e-9

    def test_tampered_confidence_does_not_abort_rebuild_or_clear_it(
            self, journal_sdk):
        """(1) Fails if a journal's ``EntityLinked`` record with a non-finite/
        overflowing confidence (``NaN``/``Infinity``/``1e400``/``10**400``)
        raises during ``rebuild_all`` — which runs AFTER
        ``MATCH (n) DETACH DELETE n``, so the raise is data loss (F3) — or
        clears a previously-set confidence. (2) The journal below carries all
        four tampered values for the same triple, after a good 0.9 record —
        each value is reachable.
        """
        sdk, jpath = journal_sdk
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "the team shipped")["id"]
        _bind(sdk, pid=pid, slots={
            "subject": [{"name": "the team", "kind": "core:team",
                         "confidence": 0.9}]})
        rows = sdk._get_proj().g.query(
            "MATCH (s:Subject {name:'the team'}) RETURN s.id").result_set
        sid = rows[0][0]
        with open(jpath, "a", encoding="utf-8") as fh:
            for bad in (float("nan"), float("inf"), 1e400, 10 ** 400):
                fh.write(json.dumps({
                    "type": "EntityLinked", "id": pid, "source_id": pid,
                    "source_label": "Point", "target_id": sid,
                    "target_label": "Subject", "edge_type": "aboutSubject",
                    "confidence": bad,
                }) + "\n")
        # Must not raise; must not lose the node/edge to a post-wipe abort.
        sdk._get_proj().rebuild_all(str(jpath.parent))
        rows = _edge(sdk, pid)
        assert rows and abs(float(rows[0][1]) - 0.9) < 1e-9

    def test_fold_ignores_a_no_confidence_record_against_a_confidenced_edge(
            self, sdk):
        """(1) Fails if ``_fold_entity_linked_reason`` unconditionally SETs
        (a no-confidence record then clears an existing confidence) or raises
        on a non-finite confidence. (2) The pre-existing confidenced edge and
        the no-confidence record are the values under test — reachable.
        """
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "fold unit")["id"]
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (s:Subject {name:'the team'}) RETURN s.id").result_set
        sid = rows[0][0]
        base = {"id": pid, "source_id": pid, "source_label": "Point",
                "target_id": sid, "target_label": "Subject",
                "edge_type": "aboutSubject"}
        assert proj._fold_entity_linked_reason(
            {**base, "confidence": 0.9}) == (1, "ok")
        assert proj._fold_entity_linked_reason(dict(base)) == (1, "ok")
        assert proj._fold_entity_linked_reason(
            {**base, "confidence": float("nan")}) == (1, "ok")
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


#: A foreign-namespace entity/slot (T2): neither the entity nor the slot may
#: become a Subject, and the binder must refuse it.
_FOREIGN_KIND_PAYLOAD = {
    "entities": [
        {"name": "acme", "kind": "acme:team", "lifecycle": "created",
         "supersedes": None, "note": None},
    ],
    "events": [],
    "points": [{
        "content": "acme approved the plan", "pointKind": "statement",
        "about_entities": ["acme"],
        "slots": {"subject": [{"name": "acme", "kind": "acme:team",
                               "confidence": 0.99}]},
    }],
    "operators": [], "chain_notes": [], "link_before_create": [],
}

#: Case-only divergence (F6): subject entity "The Team" and a legitimately
#: cased Object "the team"; the exact-name skip must NOT drop the latter.
_CASE_PAYLOAD = {
    "entities": [
        {"name": "The Team", "kind": "core:team", "lifecycle": "created",
         "supersedes": None, "note": None},
        {"name": "the team", "kind": "core:plan", "lifecycle": "created",
         "supersedes": None, "note": None},
    ],
    "events": [],
    "points": [{
        "content": "the lower-case object shipped", "pointKind": "statement",
        "about_entities": ["the team"],
        "slots": {},
    }],
    "operators": [], "chain_notes": [], "link_before_create": [],
}


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
        """(1) Fails if the legacy ``about_entities``/topic channel is not
        skipped for a subject-kind name: a pre-seeded LEGACY
        ``:Object {name:'the team'}`` means removing the skip attaches an
        un-gated ``aboutObject`` edge to it. (2) The seeded same-named Object
        is the value under test; the payload's ``about_entities`` includes
        "the team" (core:team) — reachable.
        """
        sdk, _ = journal_sdk
        proj = sdk._get_proj()
        # A LEGACY :Object sharing the subject's name (the #4934 label-leak
        # shape). With the skip removed, the topic channel attaches an
        # aboutObject edge to THIS node.
        proj.g.query(
            "MERGE (o:Object {name:'the team'}) SET o.objectKind='core:team'")
        r = sdk.capture_session(CONV, session_id="s1370b")
        assert r["ok"] is True, r
        # exactly ONE aboutSubject edge (from the binder)...
        n1 = proj.g.query(
            "MATCH (:Point)-[:aboutSubject]->(:Subject {name:'the team'}) "
            "RETURN count(*)").result_set[0][0]
        assert n1 == 1
        # ...and NO aboutObject edge to the legacy same-named Object.
        n2 = proj.g.query(
            "MATCH (:Point)-[:aboutObject]->(o:Object {name:'the team'}) "
            "RETURN count(*)").result_set[0][0]
        assert n2 == 0

    def test_foreign_namespace_kind_never_mints_a_subject(
            self, journal_sdk, monkeypatch):
        """(1) Fails if a foreign-namespace kind (``acme:team``) is minted
        into a ``:Subject`` or writes an ``aboutSubject`` edge through the
        seam — the T2 bypass (F2). (2) The payload's entity and slot both
        carry ``acme:team`` with confidence 0.99 — reachable.
        """
        import tortoise.sdk as sdk_mod
        monkeypatch.setattr(sdk_mod, "_V2SessionMock",
                            _make_slots_mock(_FOREIGN_KIND_PAYLOAD))
        sdk, _ = journal_sdk
        r = sdk.capture_session(CONV, session_id="s1370-foreign")
        assert r["ok"] is True, r
        proj = sdk._get_proj()
        assert proj.g.query(
            "MATCH (s:Subject {name:'acme'}) RETURN count(s)"
        ).result_set[0][0] == 0
        assert proj.g.query(
            "MATCH (:Point)-[:aboutSubject]->(:Subject {name:'acme'}) "
            "RETURN count(*)").result_set[0][0] == 0

    def test_case_only_mismatch_still_gets_its_about_object_edge(
            self, journal_sdk, monkeypatch):
        """(1) Fails if the ``about_entities`` skip uses a lower-cased
        name-key while the ``MATCH (o:Object {name:$n})`` resolution is
        case-sensitive — dropping a legitimately-cased Object (F6). (2) The
        payload has subject entity ``The Team`` and Object ``the team``, and
        ``about_entities: ["the team"]`` — the value is reachable.
        """
        import tortoise.sdk as sdk_mod
        monkeypatch.setattr(sdk_mod, "_V2SessionMock",
                            _make_slots_mock(_CASE_PAYLOAD))
        sdk, _ = journal_sdk
        r = sdk.capture_session(CONV, session_id="s1370-case")
        assert r["ok"] is True, r
        proj = sdk._get_proj()
        # The subject-kind entity is a :Subject; the Object keeps its case.
        assert proj.g.query(
            "MATCH (s:Subject {name:'The Team'}) RETURN count(s)"
        ).result_set[0][0] == 1
        assert proj.g.query(
            "MATCH (o:Object {name:'the team'}) RETURN count(o)"
        ).result_set[0][0] == 1
        assert proj.g.query(
            "MATCH (:Point)-[:aboutObject]->(:Object {name:'the team'}) "
            "RETURN count(*)").result_set[0][0] == 1

    def test_capture_object_slot_confidence_survives_the_legacy_edge(
            self, journal_sdk):
        """(1) Fails if the object slot's confidence is discarded because the
        legacy ``about_entities`` channel already wrote the ``aboutObject``
        edge before the binder ran (F7). (2) Fixture: ``_SLOTS_PAYLOAD``
        lists "the plan" in BOTH ``about_entities`` and the object slot at
        0.9 — the value is reachable.
        """
        sdk, jpath = journal_sdk
        r = sdk.capture_session(CONV, session_id="s1370-conf")
        assert r["ok"] is True, r
        proj = sdk._get_proj()
        pid = proj.g.query(
            "MATCH (p:Point) WHERE p.content = 'the team approved the plan' "
            "RETURN p.id").result_set[0][0]
        rows = proj.g.query(
            "MATCH (:Point {id:$p})-[r:aboutObject]->(o:Object {name:'the plan'}) "
            "RETURN r.confidence", params={"p": pid}).result_set
        assert len(rows) == 1 and rows[0][0] is not None
        assert abs(float(rows[0][0]) - 0.9) < 1e-9
        recs = [e for e in _journal(jpath)
                if e.get("type") == "EntityLinked"
                and e.get("edge_type") == "aboutObject"
                and e.get("confidence") is not None]
        assert recs

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
        """(1) Fails if the measure is not load-bearing (the previous version
        was a static flag over authored labels — structurally ``1/13`` and
        unable to fail for any binder behaviour) OR if the real
        resolution-path rate exceeds the committed 10% target. (2) The
        fixture's ``g13`` (a name resolving to a node that is NOT the gold
        subject) makes the rate non-zero; ``g22`` (unresolvable name, non-null
        gold) and ``g23`` (kind-mismatched target) can fail for reasons other
        than τ — all reachable. MEASURED at the shipped default thresholds:
        ``1/13 = 0.077``.
        """
        from tortoise.subject_binding import quality_gate_metrics
        metrics = quality_gate_metrics(gold_path=GOLD)
        assert metrics["bound"] == 13, metrics
        assert metrics["misattributed"] == 1, metrics
        assert metrics["misattribution_rate"] <= 0.10, metrics

    def test_flipping_graph_nodes_changes_the_misattribution_rate(self, tmp_path):
        """(1) Fails if the metric ignores each row's ``graph_nodes`` — i.e.
        if a row's resolution is not what decides its outcome (the F9
        static-label defect). (2) Removing ``g13``'s node makes that
        misattributed row unresolvable, so the rate must FALL to 0.0 from the
        measured ``1/13``.
        """
        from tortoise.subject_binding import quality_gate_metrics
        base = quality_gate_metrics(gold_path=GOLD)
        with open(GOLD) as fh:
            rows = [json.loads(ln) for ln in fh if ln.strip()]
        for row in rows:
            if row["id"] == "g13":
                row["graph_nodes"] = []
        flipped_path = tmp_path / "flipped.jsonl"
        flipped_path.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n")
        flipped = quality_gate_metrics(gold_path=str(flipped_path))
        assert flipped["misattribution_rate"] < base["misattribution_rate"]
        assert flipped["misattribution_rate"] == 0.0, flipped

    def test_tightening_the_threshold_binds_strictly_fewer(self):
        """(1) Fails if raising τ_hi stops being load-bearing: a
        ``decide_binding`` that always binds (or never binds) makes the two
        runs EQUAL, which the strict `<`/`>` catch while the previous `<=`
        accepted. (2) The fixture's rows at 0.4–0.9 straddle the two
        thresholds and the misattributed row ``g13`` (0.72) is bound at the
        low threshold and dropped at the high — reachable.
        """
        from tortoise.subject_binding import quality_gate_metrics
        low = quality_gate_metrics(gold_path=GOLD, tau_hi=0.4, tau_lo=0.3)
        high = quality_gate_metrics(gold_path=GOLD, tau_hi=0.9, tau_lo=0.3)
        assert high["bound"] < low["bound"]
        assert high["misattribution_rate"] < low["misattribution_rate"]
        assert high["unbound_rate"] > low["unbound_rate"]


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

    def test_audit_fraction_cannot_exceed_one_and_scopes_the_journal(
            self, journal_sdk):
        """(1) Fails if ``bound_fraction`` divides an EDGE count by a POINT
        count (3 edges / 2 points = 1.5 — the reproduced F8 defect), or if a
        permitted ``Document→Subject`` journal record inflates the
        Point-sourced denominator. (2) Two subject edges on ONE point, plus a
        hand-written Document-sourced record — both reachable.
        """
        from tortoise.subject_binding import audit_subject_binding
        sdk, jpath = journal_sdk
        sdk.create_entity("subject", "team a", subjectKind="core:team")
        sdk.create_entity("subject", "team b", subjectKind="core:team")
        pid = sdk.create_point("statement", "two subjects")["id"]
        _bind(sdk, pid=pid, slots={"subject": [
            {"name": "team a", "kind": "core:team", "confidence": 0.9},
            {"name": "team b", "kind": "core:team", "confidence": 0.9}]})
        rep = audit_subject_binding(sdk._get_proj().g, jpath)
        assert rep["bound"] == 2          # edges
        assert rep["bound_points"] == 1   # distinct points
        assert rep["bound_fraction"] <= 1.0
        assert rep["bound_fraction"] == pytest.approx(1.0)
        # A permitted Document→Subject record must not inflate the
        # Point-sourced denominator.
        with open(jpath, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "EntityLinked", "id": "doc-x", "source_id": "doc-x",
                "source_label": "Document", "target_id": "subj-x",
                "target_label": "Subject", "edge_type": "aboutSubject",
                "confidence": 0.9}) + "\n")
        rep2 = audit_subject_binding(sdk._get_proj().g, jpath)
        assert rep2["attempted"] == rep["attempted"]

    def test_audit_reports_no_journal_when_the_path_does_not_exist(self, sdk):
        """(1) Fails if a non-existent ``journal_path`` yields a non-None
        ``journal`` (the value the CLI F14 crash consumed). (2) The missing
        path is the value under test — reachable.
        """
        from tortoise.subject_binding import audit_subject_binding
        rep = audit_subject_binding(sdk._get_proj().g,
                                    journal_path="/nonexistent/journal.jsonl")
        assert rep["journal"] is None
        assert rep["unbound_fraction"] is None


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


def _hosted_event_payload_and_plan(sid: str):
    """A hosted payload carrying one Event whose ``about_entities`` names a
    declared subject kind — the F5 drop path."""
    from tortoise.commit_schema import (
        BudgetDecision,
        CommitEvent,
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
    from tortoise.ids import content_hash

    content = "the Aug 3 meeting happened"
    pid = point_content_id(content)
    pt = Point(
        id=pid, content=content, pointKind="statement", reason="NEW",
        confidence=0.9, c_cal=0.8, about_entities=[], source_ref="session.md",
        quote="", status="live", slots=None,
    )
    ent = Entity(name="the team", kind="core:team", passes_frequency_gate=True)
    ev = CommitEvent(
        id="ev_" + content_hash(content), eventKind="core:meeting",
        content=content, about_entities=["the team"], source_ref="session.md",
    )
    payload = CommitPayload(
        schema_version="1", session_id=sid, client_commit_id="ccid-1370-ev",
        captured_at="2026-09-24T00:00:00Z",
        extractor=ExtractorInfo(version="value@1.0.0", mode="byok",
                                calibration_version="v3"),
        summary="s", story_arc="", provenance_refs=[], sources=[],
        events=[ev], entities=[ent], points=[pt], operators=[],
        supersessions=[],
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
        entities=[EntityReconcile(entity=ent, action="new")],
    )
    plan = CommitPlan(payload=payload, duplicate=False, first_adjudication=True,
                      reconcile=reconcile,
                      budget=BudgetDecision(outcome="ok", cumulative_after=1))
    return pid, ev.id, payload, plan


class TestHostedCommitSeam:
    def test_hosted_event_subject_kind_drops_without_edge_or_stub(self, sdk):
        """(1) Fails if the hosted Event resolver emits an un-gated
        ``aboutSubject`` for a subject-kind name, or mints an id-less
        ``:Object`` stub for it (F5). (2) The Event's ``about_entities``
        contains ``the team`` (``core:team``) — reachable.
        """
        from tortoise import hosted_api
        _pid, eid, payload, plan = _hosted_event_payload_and_plan(
            "s-hosted-ev-1370")
        hosted_api._execute_commit_writes(sdk, payload, plan)
        g = sdk._get_proj().g
        # The subject-kind entity WAS routed to a :Subject...
        assert g.query("MATCH (s:Subject {name:'the team'}) RETURN count(s)"
                       ).result_set[0][0] == 1
        # ...but the Event resolves to NO aboutSubject edge and NO Object stub.
        assert g.query(
            "MATCH (:Event {eventId:$e})-[:aboutSubject]->() RETURN count(*)",
            params={"e": eid}).result_set[0][0] == 0
        assert g.query("MATCH (o:Object {name:'the team'}) RETURN count(o)"
                       ).result_set[0][0] == 0

    def test_hosted_commit_binds_and_routes_like_the_local_seam(self, sdk):
        """(1) Fails if the hosted commit seam drops the slots (no edge), does
        NOT route the core:team entity to :Subject, re-emits the un-gated
        aboutSubject from about_entities, or — with a pre-seeded LEGACY
        ``:Object {name:'the team'}`` — removes the subject-name skip so an
        aboutObject edge attaches to it. (2) The payload carries a subject
        slot at 0.9 AND "the team" in about_entities; the legacy Object is
        seeded before the commit — all reachable.
        """
        from tortoise import hosted_api
        proj = sdk._get_proj()
        # A LEGACY :Object sharing the subject's name (#4934 leak shape).
        proj.g.query(
            "MERGE (o:Object {name:'the team'}) SET o.objectKind='core:team'")
        pid, payload, plan = _hosted_payload_and_plan(
            "the team approved the plan", "s-hosted-1370")
        hosted_api._execute_commit_writes(sdk, payload, plan)
        g = proj.g
        rows = g.query(
            "MATCH (:Point {id:$p})-[r:aboutSubject]->(s:Subject) "
            "RETURN s.name, r.confidence", params={"p": pid}).result_set
        assert len(rows) == 1 and rows[0][0] == "the team", rows
        assert abs(float(rows[0][1]) - 0.9) < 1e-9
        assert g.query("MATCH (s:Subject {name:'the team'}) RETURN count(s)"
                       ).result_set[0][0] == 1
        # The legacy same-named Object is NOT the target of an aboutObject
        # edge — the subject-name skip is load-bearing.
        assert g.query(
            "MATCH (:Point {id:$p})-[:aboutObject]->(o:Object {name:'the team'}) "
            "RETURN count(*)", params={"p": pid}).result_set[0][0] == 0
        # the object slot binds as aboutObject, never aboutSubject
        assert g.query(
            "MATCH (:Point {id:$p})-[:aboutObject]->(o:Object {name:'the plan'}) "
            "RETURN count(o) > 0", params={"p": pid}).result_set[0][0] is True

    def test_hosted_seam_binds_the_resolved_point_id_not_the_payload_id(
            self, sdk):
        """(1) Fails if the hosted binder is given `pr.point.id` instead of
        the id `create_point` resolved the write to: the pre-created canonical
        has a DIFFERENT (auto) id, so the payload id addresses no Point and
        the aboutSubject edge is never written (the phantom case G1 closes).
        (2) The pre-created same-content Point is the value under test; the
        payload's subject slot at 0.9 is the reachable binding.
        """
        from tortoise import hosted_api
        existing = sdk.create_point("statement", "the team approved the plan",
                                    is_episodic=False)
        existing_id = existing["id"]
        pid, payload, plan = _hosted_payload_and_plan(
            "the team approved the plan", "s-hosted-resolved")
        assert pid != existing_id
        hosted_api._execute_commit_writes(sdk, payload, plan)
        g = sdk._get_proj().g
        rows = g.query(
            "MATCH (:Point {id:$p})-[r:aboutSubject]->(s:Subject) "
            "RETURN s.name, r.confidence", params={"p": existing_id}).result_set
        assert len(rows) == 1 and rows[0][0] == "the team", rows
        assert abs(float(rows[0][1]) - 0.9) < 1e-9
        # No aboutSubject edge exists on the never-created payload id.
        assert g.query(
            "MATCH (:Point {id:$p})-[:aboutSubject]->() RETURN count(*)",
            params={"p": pid}).result_set[0][0] == 0


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


# ══════════════════════════════════════════════════════════════════════════
# F. Audit CLI construction + missing-journal report
# ══════════════════════════════════════════════════════════════════════════

class TestAuditCli:
    def test_uri_mode_constructs_the_sdk_without_a_positional_db_path(
            self, monkeypatch):
        """(1) Fails if ``--uri`` passes a positional ``db_path`` — that makes
        the SDK take the embedded branch, ignore ``TORTOISE_DB_URI`` and audit
        an empty local graph (F4). (2) The captured constructor args are the
        value under test.
        """
        from tools import subject_binding_audit as tool
        captured: dict = {}

        class _FakeGraph:
            def query(self, *a, **k):
                class _R:
                    result_set: tuple = ()
                return _R()

        class _FakeProj:
            g = _FakeGraph()

        class _FakeSDK:
            def __init__(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs

            def _get_proj(self):
                return _FakeProj()

            def close(self):
                pass

        monkeypatch.setenv("TORTOISE_DB_URI",
                           "docker://:pw@localhost:16610/g")
        monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)

        class _Args:
            uri = True
            db = None
            namespace = None
            journal = None

        report = tool._graph_report(_Args())
        assert captured["args"] == ()          # no positional db_path
        assert captured["kwargs"].get("namespace") is None
        assert report["journal"] is None

    def test_missing_journal_path_prints_unknown_without_crashing(
            self, tmp_path, capsys):
        """(1) Fails if EITHER half of the F14 fix is reverted: the
        `journal_exists` gating (a missing path then reports the wrong
        "unreadable" reason) or the tool's `unbound_fraction is None` guard
        (an EXISTING-but-unreadable path then formats a `None` metric and
        raises `TypeError`). (2) Both the missing path and the existing
        directory are the values under test.
        """
        from tools.subject_binding_audit import main
        db = tmp_path / "audit.db"
        rc = main(["--gold", GOLD, "--db", str(db),
                   "--journal", str(tmp_path / "nope.jsonl")])
        out = capsys.readouterr().out
        assert rc == 0
        assert "UNKNOWN" in out
        assert "no existing --journal given" in out
        # A path that EXISTS but is unreadable (a directory): the crash
        # precondition `NoneType.__format__` on `unbound_fraction`.
        unreadable = tmp_path / "journal_dir"
        unreadable.mkdir()
        rc = main(["--gold", GOLD, "--db", str(db),
                   "--journal", str(unreadable)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "UNKNOWN" in out
        assert "journal unreadable" in out

    def test_audit_reports_unknown_for_an_unreadable_journal(self, sdk):
        """(1) Fails if `audit_subject_binding` raises on a path that exists
        but cannot be read, instead of leaving `unbound_fraction` None. (2) A
        directory as the journal path IS the value under test.
        """
        import tempfile

        from tortoise.subject_binding import audit_subject_binding
        with tempfile.TemporaryDirectory() as td:
            rep = audit_subject_binding(sdk._get_proj().g, journal_path=td)
        assert rep["journal"] == td
        assert rep["unbound_fraction"] is None
