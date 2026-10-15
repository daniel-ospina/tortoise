"""#1423 — Subject supersession: mirror the Object-side status projector.

**The defect, measured on the base commit.** The extractor's supersession
producer (`extractor_v2._supersession_records`) is kind-AGNOSTIC, and #1370
routes the declared §5 Subject kinds (team / role / organization /
naturalPerson / legalPerson) to `:Subject` — so a conversation saying "team Y
replaced by team Z" produces a record whose successor resolves to a Subject.
`commit_ops.apply_supersessions` then warned:

    entity supersession 'Y' skipped — successor 'Z' is a :Subject
    (a declared §5 subject kind, #1370); entity supersession is Object-only

and left Y `live` **and search-visible** forever. ONTOLOGY §4.2 recorded the
same gap ("projection-owned status is planned, NOT yet implemented on
Subject").

**What the fix is, and what it is NOT.** No new event type: the projection-
owned status is already the generic entity surface's job. The fold
(`FalkorProjection._fold_entity_mutation`) applies `SET n += $state` on EVERY
canonical label in all four replay engines, and `_update_entity`'s non-Point
branch already journaled a replayable record for a `:Subject` status write.
Measured before this landed: `update_entity(subject, status='superseded',
supersededBy=..., supersededAt=...)` round-tripped byte-identically through
`rebuild_all`. Only the PRODUCER and the read filter were missing.

Three halves are therefore asserted here:

1. **Write path** — a Subject-addressed supersession applies, journals an
   `EntityMutated op="supersede"` record carrying the applied map and the
   provenance, and replays byte-identically (including the ORIGINAL
   `supersededAt`, not a rebuild-time one).
2. **Clobber guard** — the #1350/#2194 Subject twin. `_SUBJECT_HANDLED` lacked
   `status`, so it rode the extras lane (which applies ON MATCH): a re-mention
   of a superseded Subject reset it to `live` LIVE while replay kept
   `superseded` — the #2193 live≠replay class, reproduced before the fix.
   `status` is now projection-owned (ON CREATE only) and this is pinned on the
   live-vs-replay pair, not on one side.
3. **Read side** — a superseded Subject is excluded from the retrieval legs by
   default (asserted on the ACTUAL returned ids of legs that need no search
   index), and the audit opt-in still sees it.

Gates mirrored from the Object lane so the two families cannot drift:
keep-first (a terminal target is never re-folded), never-guess (an ambiguous
ref is refused, not guessed), and visible-distinct-successor.
"""
from __future__ import annotations

import contextlib
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _live_utils
from tortoise.embeddings import EMBEDDING_DIM
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK, _entity_name_id
from tortoise.search_engine import (
    reset_circuit_breakers,
    run_fts_query,
    run_structural_query,
    run_vector_query,
)

#: The canonical Subject terminal vocabulary, HARD-CODED (an expectation
#: imported from the code under test is not an expectation). ``retracted`` is
#: a member even though ONTOLOGY §4.2 does not declare it — the Object
#: family's own precedent (#2977): a READ vocabulary names every stored state
#: that is dead, including one a legacy/pre-fix SDK or a raw producer can have
#: written, regardless of whether the (now guarded) generic write surface may
#: mint it. The write guard is asserted separately below.
CANONICAL_SUBJECT_TERMINAL = frozenset(
    {"superseded", "deprecated", "archived", "retracted"})
_VEC = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
_SB_PREFIX = "sub-"


# ── helpers ───────────────────────────────────────────────────────────────

def _subject_row(proj, name, *props):
    cols = ", ".join(f"s.{p}" for p in (props or ("status", "supersededBy",
                                                  "supersededAt")))
    return proj.g.query(
        f"MATCH (s:Subject {{name:$n}}) RETURN {cols}",
        params={"n": name}).result_set


def _journal(path):
    return EventLog(str(path)).read_all()


def _superseded_records(path):
    return [r for r in _journal(path)
            if r.get("type") == "EntityMutated" and r.get("op") == "supersede"]


def _apply(proj, sdk, records, *, session_id="sess-1423"):
    from tortoise.commit_ops import apply_supersessions
    warns: list[str] = []
    skips: list[dict] = []
    applied = apply_supersessions(
        proj, sdk, records, session_id=session_id,
        warn=warns.append, skipped=skips)
    return applied, warns, skips


@pytest.fixture()
def env(tmp_path):
    """A journaled embedded SDK with two Subjects: Team Alpha, Team Beta."""
    events = tmp_path / "events"
    events.mkdir()
    journal = events / "events.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "s.db"), event_log_path=str(journal))
    try:
        proj = sdk._get_proj()
        sdk.create_entity("subject", "Team Alpha", subjectKind="core:team")
        sdk.create_entity("subject", "Team Beta", subjectKind="core:team")
        yield sdk, proj, events, journal
    finally:
        sdk.close()


RECORD = {"superseded": "Team Alpha", "supersedes_by": "Team Beta",
          "evidence": "entity lifecycle supersedes (conversation-driven)"}


# ── 1. the write path ─────────────────────────────────────────────────────

class TestSubjectSupersessionWritePath:
    def test_subject_successor_is_applied_not_skipped(self, env):
        """The #1370 warn-and-skip is gone: a Subject successor APPLIES."""
        sdk, proj, _events, _journal_path = env
        applied, warns, skips = _apply(proj, sdk, [RECORD])
        assert applied == 1, (warns, skips)
        assert skips == [], (
            "a Subject successor must not be reported as skipped")
        assert not any("Object-only" in w for w in warns), warns

        (status, by, _at), = _subject_row(proj, "Team Alpha")
        assert status == "superseded"
        assert by == "Team Beta"

    def test_write_is_journaled_as_a_supersede_record_with_provenance(
            self, env):
        """`evidence` is the documented reachability channel for terminal
        entity ids (#3301's prior leg passes it) — it must be an APPLIED
        supersession once the successor is a Subject, not a skipped one."""
        sdk, proj, _events, journal_path = env
        _apply(proj, sdk, [RECORD], session_id="sess-abc")
        recs = _superseded_records(journal_path)
        assert len(recs) == 1, recs
        rec = recs[0]
        assert rec["label"] == "Subject"
        assert rec["id"] == _entity_name_id("Subject", "Team Alpha")
        assert rec["state"]["status"] == "superseded"
        assert rec["state"]["supersededBy"] == "Team Beta"
        assert rec["state"].get("supersededAt"), rec
        # provenance rides the record beside `state` (never inside it — a key
        # in `state` would be re-applied as a node property by the fold).
        assert rec["session_id"] == "sess-abc"
        assert rec["evidence"] == RECORD["evidence"]

    def test_rebuild_all_reproduces_it_byte_identically(self, env):
        sdk, proj, events, _journal_path = env
        _apply(proj, sdk, [RECORD])
        live = _subject_row(proj, "Team Alpha")
        proj.rebuild_all(str(events), confirm_destructive=True)
        rebuilt = _subject_row(proj, "Team Alpha")
        assert rebuilt == live, (
            f"replay must reproduce the supersession exactly: "
            f"live={live!r} rebuilt={rebuilt!r}")

    def test_rebuilt_superseded_at_is_the_original_not_rebuild_time(self, env):
        """The stamp is carried in the journaled `state` map, so replay
        restores the ORIGINAL instant — the #2164-P4 drift class (the Object
        twin's live fold falls back to `now`; here both sides read one value).

        A rebuild can only run after the write, so a re-minted "now" would be
        strictly LATER than the journaled one; equality is the contract."""
        sdk, proj, events, journal_path = env
        _apply(proj, sdk, [RECORD])
        journaled = _superseded_records(journal_path)[0]["state"]["supersededAt"]
        proj.rebuild_all(str(events), confirm_destructive=True)
        (_status, _by, at), = _subject_row(proj, "Team Alpha")
        assert at == journaled, (at, journaled)


# ── 2. keep-first / never-guess (mirrored from the Object lane) ───────────

class TestKeepFirstAndNeverGuess:
    def test_reingest_does_not_refold_or_duplicate_the_record(self, env):
        sdk, proj, _events, journal_path = env
        _apply(proj, sdk, [RECORD])
        first = _subject_row(proj, "Team Alpha")
        applied, _warns, _skips = _apply(proj, sdk, [RECORD])
        assert applied == 0, "a terminal target must not be re-folded"
        assert _subject_row(proj, "Team Alpha") == first
        assert len(_superseded_records(journal_path)) == 1

    def test_conflicting_successor_does_not_overwrite_keep_first(self, env):
        sdk, proj, _events, _journal_path = env
        _apply(proj, sdk, [RECORD])
        _apply(proj, sdk, [{"superseded": "Team Alpha",
                            "supersedes_by": "Team Gamma"}])
        (_status, by, _at), = _subject_row(proj, "Team Alpha")
        assert by == "Team Beta", (
            "keep-first: the fold never blind-overwrites a live terminal row")

    def test_ambiguous_name_ref_is_refused_never_guess(self, env):
        """Two Subjects carrying the SAME name are ambiguous — the resolver
        must refuse rather than pick one by LIMIT 1."""
        sdk, proj, _events, _journal_path = env
        proj.g.query(
            "CREATE (s:Subject {id:'sub-dup-1423', name:'Team Alpha', "
            "subjectKind:'core:team', status:'live'})")
        applied, warns, skips = _apply(proj, sdk, [RECORD])
        assert applied == 0
        assert any("never-guess" in w for w in warns), warns
        assert skips and "ambiguous" in skips[0]["reason"], skips

    def test_dangling_successor_is_refused(self, env):
        sdk, proj, _events, _journal_path = env
        applied, warns, skips = _apply(
            proj, sdk, [{"superseded": "Team Alpha",
                         "supersedes_by": "Team Nowhere"}])
        assert applied == 0
        assert any("dangling successor" in w for w in warns), warns
        assert skips[0]["reason"] == "dangling successor"

    def test_cross_family_record_is_refused_not_half_folded(self, env):
        """A Subject SUCCESSOR with a ref that exists only as an `:Object`
        is not a Subject supersession. The ref is resolved INSIDE the Subject
        family, so the record is refused rather than folding half a
        replacement onto one node."""
        sdk, proj, _events, _journal_path = env
        proj.g.query(
            "CREATE (o:Object {id:'obj-only-1423', name:'An Object Only', "
            "objectKind:'other', status:'live'})")
        applied, warns, skips = _apply(
            proj, sdk, [{"superseded": "An Object Only",
                         "supersedes_by": "Team Beta"}])
        assert applied == 0, (
            f"an Object ref + Subject successor is not representable: "
            f"{warns!r} {skips!r}")
        assert skips and skips[0]["reason"] == "ref is not a Subject", skips

    def test_a_supersession_that_resolves_within_one_family_applies(
            self, env):
        """The counterpart of the cross-family refusal: a name carried by
        BOTH an Object and a Subject is NOT ambiguous — the family is decided
        by the successor, and the ref is resolved inside it."""
        sdk, proj, _events, _journal_path = env
        proj.g.query(
            "CREATE (o:Object {id:'obj-shadow-1423', name:'Team Alpha', "
            "objectKind:'other', status:'live'})")
        applied, warns, skips = _apply(proj, sdk, [RECORD])
        assert applied == 1, (warns, skips)
        assert skips == []
        (_status, by, _at), = _subject_row(proj, "Team Alpha")
        assert by == "Team Beta"

    def test_self_supersession_is_refused(self, env):
        sdk, proj, _events, _journal_path = env
        applied, _warns, skips = _apply(
            proj, sdk, [{"superseded": "Team Alpha",
                         "supersedes_by": "Team Alpha"}])
        assert applied == 0
        assert skips[0]["reason"] == "self-supersession"

    def test_object_shadow_of_a_subject_successor_routes_to_the_subject_lane(
            self, env):
        """#1423 review P2 (#5365): the SUCCESSOR probe is Object-first, so a
        successor name carried by BOTH families routed a Subject-ref record
        into the Object lane. The Object-only ref probe then found nothing and
        the pre-fix code warned "ref not found" and `continue`d — DROPPING a
        fold that IS representable and reporting it NOWHERE in `skipped`.

        MUTATION: reverting the Subject-lane dispatch at that site reds this
        on `applied` (0, not 1) and on the empty `skips`.
        """
        sdk, proj, _events, _journal_path = env
        proj.g.query(
            "CREATE (o:Object {id:'obj-shadow-beta', name:'Team Beta', "
            "objectKind:'other', status:'live'})")
        applied, warns, skips = _apply(proj, sdk, [RECORD])
        assert applied == 1, (warns, skips)
        assert skips == [], skips
        (_status, by, _at), = _subject_row(proj, "Team Alpha")
        assert (_status, by) == ("superseded", "Team Beta"), (warns, skips)

    def test_unresolvable_object_ref_is_reported_in_skipped(self, env):
        """The other half of that site: when the successor routes the record
        into the Object lane but the ref is not a Subject either, the drop must
        still reach `skipped` (one entry per unapplied record — the #5365
        contract), not only the warn stream.

        MUTATION: reverting the Subject-lane fallback at that site reds this —
        the pre-fix code `continue`d with `skips == []`.
        """
        sdk, proj, _events, _journal_path = env
        # The OBJECT-lane site is reached only when the successor probe hits
        # an Object (a Subject-only successor takes the Subject lane first).
        proj.g.query(
            "CREATE (o:Object {id:'obj-beta-1423', name:'Team Beta', "
            "objectKind:'other', status:'live'})")
        applied, warns, skips = _apply(
            proj, sdk, [{"superseded": "Nobody At All",
                         "supersedes_by": "Team Beta"}])
        assert applied == 0, warns
        assert [s["reason"] for s in skips] == ["ref is not a Subject"], skips
        assert any("as a :Subject" in w for w in warns), warns

    def test_ambiguous_subject_name_records_a_non_folded_entry(self, env):
        """#1423 review P2: the Object twin records the refusal durably
        (`entity_identity.record_non_folded` — the ENTRY is the record, the
        log line is only visibility); the Subject read only warned.

        MUTATION: removing the `record_non_folded` call reds this on the
        empty entry tuple.
        """
        from tortoise.entity_identity import clear_non_folded_entries, non_folded_entries
        sdk, proj, _events, _journal_path = env
        clear_non_folded_entries()
        try:
            proj.g.query(
                "CREATE (s:Subject {id:'sub-dup-1423', name:'Team Alpha', "
                "subjectKind:'core:team', status:'live'})")
            applied, _warns, _skips = _apply(proj, sdk, [RECORD])
            assert applied == 0
            entries = non_folded_entries()
            assert [e.shape for e in entries] == ["ambiguous-name"], entries
            assert entries[0].label == "Subject", entries
        finally:
            clear_non_folded_entries()

    def test_duplicate_subject_id_records_a_non_folded_entry(self, env):
        """The id-form twin of the entry above (`shape="duplicate-id"`)."""
        from tortoise.entity_identity import clear_non_folded_entries, non_folded_entries
        sdk, proj, _events, _journal_path = env
        clear_non_folded_entries()
        try:
            for suffix in ("A", "B"):
                proj.g.query(
                    "CREATE (s:Subject {id:'sub-dup-1423', "
                    f"name:'Team Alpha {suffix}', "
                    "subjectKind:'core:team', status:'live'})")
            applied, _warns, _skips = _apply(
                proj, sdk, [{"superseded": "sub-dup-1423",
                             "supersedes_by": "Team Beta"}])
            assert applied == 0
            entries = non_folded_entries()
            assert [e.shape for e in entries] == ["duplicate-id"], entries
            assert entries[0].label == "Subject", entries
        finally:
            clear_non_folded_entries()

    def test_successor_that_is_itself_terminal_is_refused(self, env):
        """No VISIBLE successor ⇒ burying the target would leave nothing a
        recall surface can show. Mirrors the Object lane's gate."""
        sdk, proj, _events, _journal_path = env
        sdk._update_entity(_entity_name_id("Subject", "Team Beta"),
                           status="superseded")
        applied, warns, skips = _apply(proj, sdk, [RECORD])
        assert applied == 0
        assert any("no visible successor" in w for w in warns), warns
        assert skips[0]["reason"] == "no visible successor"


# ── 1b. the write guard: an undeclared Subject status must not be STORABLE ─

class TestSubjectStatusWriteIsValidated:
    """#1423 review P1 — the ROOT hole, not just the word ``retracted``.

    The `status` guard in `_update_entity` was Point-PREDICATED, so any value
    reached a `:Subject` and the read vocabulary (a denylist) then read an
    unknown one as CURRENT. MUTATION: reverting the two guards reds every case
    here — the write succeeds where a `ValueError` is required — for
    ``retracted`` (the instance the review found) and ``dead`` (the general
    class). The declared lifecycle still writes.
    """

    @pytest.mark.parametrize("bad", ["retracted", "dead"])
    def test_update_entity_refuses_an_undeclared_subject_status(
            self, env, bad):
        sdk, proj, _events, _journal_path = env
        sid = _entity_name_id("Subject", "Team Alpha")
        with pytest.raises(ValueError, match="Invalid status"):
            sdk.update_entity(sid, status=bad)
        # refused BEFORE any write: the stored value is untouched.
        (status,), = _subject_row(proj, "Team Alpha", "status")
        assert status == "live", status

    @pytest.mark.parametrize("bad", ["retracted", "dead"])
    def test_create_entity_refuses_an_undeclared_subject_status(
            self, env, bad):
        """The SECOND door: `status` reaches `_upsert_subject` through
        `**props`, overriding the `'live'` default — so a guard on
        `_update_entity` alone leaves this writable."""
        sdk, _proj, _events, _journal_path = env
        with pytest.raises(ValueError, match="Invalid status"):
            sdk.create_entity("subject", "Team New", subjectKind="core:team",
                              status=bad)

    def test_the_declared_subject_lifecycle_still_writes(self, env):
        """Anti-vacuity: the guard refuses only UNDECLARED values, so the
        §4.2 lifecycle (including the terminal members) still writes."""
        sdk, proj, _events, _journal_path = env
        sid = _entity_name_id("Subject", "Team Alpha")
        for st in ("draft", "live", "superseded", "deprecated", "archived"):
            sdk.update_entity(sid, status=st)
            (got,), = _subject_row(proj, "Team Alpha", "status")
            assert got == st, (st, got)


# ── 3. the clobber guard (#1350/#2194 ported to Subject) ─────────────────

class TestStatusIsProjectionOwnedOnSubject:
    def test_re_mention_does_not_resurrect_a_superseded_subject(self, env):
        """MEASURED on the base commit: after the supersession, a re-mention
        of the same Subject left LIVE `status='live'` (with a stale
        `supersededBy`) while a rebuild kept `superseded` — the #2193
        live≠replay class. `status` is now written ON CREATE only and is
        excluded from the extras lane, so both sides agree."""
        sdk, proj, events, _journal_path = env
        _apply(proj, sdk, [RECORD])
        sdk.create_entity("subject", "Team Alpha", subjectKind="core:team")
        live = _subject_row(proj, "Team Alpha")
        proj.rebuild_all(str(events), confirm_destructive=True)
        rebuilt = _subject_row(proj, "Team Alpha")
        assert live == rebuilt, (live, rebuilt)
        (_status, by, _at), = live
        from tortoise.commit_ops import SUBJECT_TERMINAL_STATUSES
        assert (live[0][0] or "") in SUBJECT_TERMINAL_STATUSES, (
            f"a re-mention must not resurrect a superseded Subject: {live!r}")
        assert by == "Team Beta"

    def test_re_created_subject_is_born_live_not_status_less(self, env):
        """The ON CREATE half of the guard: a NEW incarnation (hard-deleted,
        then created again) must carry `status='live'` rather than the
        `status`-less node the extras-only design would leave."""
        sdk, proj, _events, _journal_path = env
        sdk.delete_entity(_entity_name_id("Subject", "Team Alpha"))
        sdk.create_entity("subject", "Team Alpha", subjectKind="core:team")
        (status,), = _subject_row(proj, "Team Alpha", "status")
        assert status == "live", status


# ── 5. the consistency projection-owned-status leg ──────────────────────

class TestConsistencyStatusLeg:
    """`consistency._compare_entities` bounded its status comparison to
    `:Object` ("`status` is only a NODE PROPERTY for Object" — a claim #1423
    falsified). A Subject buried behind a live status must now be reported."""

    @staticmethod
    def _compare(rec, graph_status):
        from tortoise.consistency import _compare_entities
        journal = {("Subject", "sub-x"): {"name": "Team X", **rec}}
        graph = {("Subject", "sub-x"): {"name": "Team X",
                                        "status": graph_status}}
        mismatches, count, _excluded = _compare_entities(
            journal, graph, {}, set(), set())
        return mismatches, count

    def test_a_buried_subject_status_is_a_divergence(self):
        mismatches, count = self._compare(
            {"status": "superseded"}, "live")
        assert count == 1, "a superseded Subject reported as live is a burial"
        assert mismatches[0]["field"] == "status"
        assert mismatches[0]["expected"] == "superseded"
        assert mismatches[0]["found"] == "live"

    def test_a_faithful_subject_status_is_not_a_divergence(self):
        mismatches, count = self._compare(
            {"status": "superseded"}, "superseded")
        assert count == 0 and mismatches == []

    def test_presence_is_still_compared_for_a_subject(self):
        from tortoise.consistency import _compare_entities
        mismatches, count, _excluded = _compare_entities(
            {("Subject", "sub-gone"): {"name": "Team Gone",
                                        "status": "live"}},
            {}, {}, set(), set())
        assert count == 1 and mismatches[0]["field"] == "presence"


# ── 6. provenance plumbing ──────────────────────────────────────────────

def test_provenance_keys_cannot_corrupt_the_record_envelope(tmp_path):
    """The optional `provenance` channel must never rewrite the envelope's own
    keys — the reason the EntityMutated payload is nested under `state` at
    all. A reserved key is DROPPED, a benign one rides."""
    journal_path = tmp_path / "events.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "p.db"), event_log_path=str(journal_path))
    try:
        sdk.create_entity("subject", "Team Gamma", subjectKind="core:team")
        sdk._journal_entity_mutation(
            "Subject", "sub-gamma", "supersede",
            state={"status": "superseded"},
            provenance={"type": "CORRUPTED", "id": "hijacked",
                        "state": {"status": "nope"},
                        "session_id": "sess-1"})
        rec = [r for r in _journal(journal_path)
               if r.get("type") == "EntityMutated"][-1]
        assert rec["type"] == "EntityMutated", rec
        assert rec["id"] == "sub-gamma", rec
        assert rec["state"] == {"status": "superseded"}, rec
        assert rec["session_id"] == "sess-1"
    finally:
        sdk.close()


def test_provenance_chain_surfaces_the_subject_status(env):
    """`sdk.provenance(point)` — "who decided this?" — carries the deciding
    Subject's status so a since-superseded role/team is visible there too.

    The chain resolves the subject from the point's `authoredBy` (`#3633`: id
    first, then one live case-insensitive name), so the author is set by ID —
    a NAME-keyed resolution is deliberately refused once the Subject is no
    longer live (`shape=no-live-holder`), which is #3633's own route-then-
    refuse discipline and not this lane's."""
    sdk, proj, _events, _journal_path = env
    sid = _entity_name_id("Subject", "Team Alpha")
    pt = sdk.create_point("statement", "Rollout is approved by the team",
                          authoredBy=sid)
    chain = sdk.provenance(pt["id"])
    assert chain["subject"]["name"] == "Team Alpha", chain
    assert chain["subject"]["status"] == "live", chain
    _apply(proj, sdk, [RECORD])
    assert sdk.provenance(pt["id"])["subject"]["status"] == "superseded"


# ── 4. the read side ─────────────────────────────────────────────────────

def test_subject_family_has_its_own_declared_terminal_vocabulary():
    from tortoise import commit_ops
    from tortoise.search_engine import (
        _STATUS_FILTERED_LABELS,
        _status_vocab_for,
    )

    assert commit_ops.SUBJECT_TERMINAL_STATUSES == CANONICAL_SUBJECT_TERMINAL
    vocab, include_outdated_flag = _status_vocab_for("Subject")
    assert vocab is commit_ops.SUBJECT_TERMINAL_STATUSES
    assert include_outdated_flag is False
    # the vocabulary is the TERMINAL set for the Subject read surface. `draft`
    # is retrievable (not terminal); `retracted` IS terminal by the Object
    # family's precedent (#2977) — a READ vocabulary must hold a dead state the
    # graph can contain even when the write guard refuses to mint it.
    assert "draft" not in vocab and "retracted" in vocab
    # ONE declaration wide, not two drifting copies: the writable lifecycle
    # (ONTOLOGY §4.2) plus the read-only legacy member IS the terminal set plus
    # the current members. `retracted` is read-terminal but NOT writable.
    assert commit_ops.SUBJECT_STATUS_VALUES | {"retracted"} == (
        commit_ops.SUBJECT_TERMINAL_STATUSES | {"draft", "live"})
    assert "retracted" not in commit_ops.SUBJECT_STATUS_VALUES
    # the gate that was the actual defect: every retrieval leg read a per-site
    # `("Point", "Object")` literal, so :Subject was never filtered at all.
    assert "Subject" in _STATUS_FILTERED_LABELS


@pytest.fixture()
def subject_graph(tmp_path):
    """A graph with one Subject per status, built with raw Cypher (no search
    index needed, so the structural/brute-force legs run on any backend)."""
    reset_circuit_breakers()
    sdk = TortoiseSDK(str(tmp_path / "read.db"))
    try:
        proj = sdk._get_proj()
        g = proj.g
        for st in [*sorted(CANONICAL_SUBJECT_TERMINAL), "live"]:
            g.query(
                "CREATE (s:Subject {id:$id, name:$name, subjectKind:'core:team',"
                " status:$st, embedding:vecf32($vec)})",
                params={"id": f"sub-{st}", "name": f"roster {st}",
                        "st": st, "vec": _VEC})
        yield g
    finally:
        sdk.close()
        reset_circuit_breakers()


def _ids(rows) -> set:
    return {str(r[0]) for r in rows}


def test_structural_leg_hides_every_terminal_subject(subject_graph):
    """BEHAVIOUR, on the ACTUAL returned ids — with the live control present
    so a filter that hides everything cannot pass vacuously."""
    assert _ids(run_structural_query(
        subject_graph, "core:team", entity_type="subject",
        limit=50)) == {"sub-live"}


def test_bruteforce_vector_leg_hides_every_terminal_subject(subject_graph):
    assert _ids(run_vector_query(
        subject_graph, _VEC, entity_type="subject", is_embedded=True,
        limit=50)) == {"sub-live"}


def test_retracted_subject_is_hidden_like_a_retracted_object(subject_graph):
    """The P2 defect: a ``retracted`` Subject leaked on every leg while a
    ``retracted`` Object was hidden. Hard-coded ids (not the constant under
    test), with the live control present so a filter that hides everything
    cannot pass vacuously."""
    ids = _ids(run_structural_query(
        subject_graph, "core:team", entity_type="subject", limit=50))
    assert "sub-retracted" not in ids, ids
    assert "sub-live" in ids, ids
    vids = _ids(run_vector_query(
        subject_graph, _VEC, entity_type="subject", is_embedded=True,
        limit=50))
    assert "sub-retracted" not in vids, vids
    assert "sub-live" in vids, vids


def test_audit_opt_out_still_sees_the_terminal_subjects(subject_graph):
    """``excluded_statuses=()`` (the audit/history opt-in) must still return
    them — the exclusion is a per-leg DEFAULT, not a data filter."""
    expected = {f"sub-{s}" for s in sorted(CANONICAL_SUBJECT_TERMINAL)} | {
        "sub-live"}
    assert _ids(run_structural_query(
        subject_graph, "core:team", entity_type="subject", limit=50,
        excluded_statuses=())) == expected
    assert _ids(run_vector_query(
        subject_graph, _VEC, entity_type="subject", is_embedded=True,
        limit=50, excluded_statuses=())) == expected


# The FTS + vector-index legs need a real FalkorDB index. The probe resolves
# its candidates (never a hardcoded port — CI provisions FalkorDB on ephemeral
# host ports, #6673/#6902), and the skip-guard (#1436) ignores a skip whose
# reason names FalkorDB — the same contract
# test_object_search_visibility_3301.py relies on.
def _probe(candidates):
    from tortoise.projection import FalkorProjection
    env_uri = os.environ.get("TORTOISE_DB_URI")
    for uri in candidates:
        if not uri:
            continue
        proj = None
        try:
            proj = FalkorProjection.from_uri(uri)
            proj.g.query("RETURN 1")
            return True, uri
        except Exception:
            if uri == env_uri:
                break
            continue
        finally:
            if proj is not None:
                with contextlib.suppress(Exception):
                    proj.close()
    return False, None


FALKORDB_AVAILABLE, _WORKING_URI = _probe([
    os.environ.get("TORTOISE_DB_URI"),
    _live_utils.docker_uri("tortoise_test_1423"),
    _live_utils.legacy_uri("tortoise_test_1423"),
])

_docker_only = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="live FalkorDB (needed for the Subject FTS/vector indexes) "
           "not available")


@_docker_only
def test_fts_and_index_legs_hide_every_terminal_subject(tmp_path):
    from tortoise.projection import FalkorProjection
    reset_circuit_breakers()
    proj = FalkorProjection.from_uri(
        _WORKING_URI, graph_name=f"test_subj1423_{uuid.uuid4().hex[:10]}")
    try:
        g = proj.g
        with contextlib.suppress(Exception):
            g.query("CALL db.idx.fulltext.createNodeIndex('Subject', 'name')")
        # Subject vector index — see the balanced-brace note in
        # tests/test_object_search_visibility_3301.py: the trailing literal is
        # NOT an f-string, so ONE closing brace. It carried two until
        # 2026-10-12, so the index was never created and `suppress` hid it —
        # invisible on 4.20.6 (the scan fallback covers a RAISING
        # `queryNodes`) but fatal on 6.0.x, where `queryNodes` returns EMPTY
        # and the leg below then found nothing.
        with contextlib.suppress(Exception):
            g.query(
                "CREATE VECTOR INDEX FOR (s:Subject) ON (s.embedding) "
                f"OPTIONS {{dimension: {EMBEDDING_DIM}, "
                "similarityFunction: 'cosine'}")
        for st in [*sorted(CANONICAL_SUBJECT_TERMINAL), "live"]:
            g.query(
                "CREATE (s:Subject {id:$id, name:$name, subjectKind:'core:team',"
                " status:$st, embedding:vecf32($vec)})",
                params={"id": f"sub-{st}", "name": f"roster {st}",
                        "st": st, "vec": _VEC})
        assert _ids(run_fts_query(g, "roster", entity_type="subject",
                                  limit=50)) == {"sub-live"}
        assert _ids(run_vector_query(
            g, _VEC, entity_type="subject", is_embedded=False,
            limit=50)) == {"sub-live"}
    finally:
        proj.close()
        reset_circuit_breakers()
