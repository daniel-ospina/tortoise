"""The #2353 semantic change feed — per-claim deltas over the events family.

WHAT THIS PINS
--------------
1. **Off by default.** With `TORTOISE_CHANGE_FEED` unset, `poll_events`' response
   carries no `changes` key and `update_point`'s content edit writes
   NO `:GraphEvent` — the store's claim-transition contract
   (`tests/test_event_store.py::test_content_edit_emits_nothing`) is intact. The
   response SHAPE is unchanged; it is NOT byte-identical, because the hard-delete
   `PointRetracted` payload carries the `deleted` marker unconditionally (see
   `tortoise/change_feed.py`'s module docstring).
2. **PointRevised reaches the store when the feed is on** — the source half of
   indicator (1), drawn as an opt-in so the default emit SET is unchanged (no
   `PointRevised` row). The stream itself is not byte-identical; see 1.
3. **The semantic feed is per-claim deltas** — one `poll_events` call returns
   per-claim deltas with before→after for content, validity and status,
   aggregated from the SAME page `poll_events` already returns, rather than a
   client-side replay of raw events. The delivered surface is the
   `changes` key on `poll_events`' reply (`TortoiseSDK.poll_events` /
   `tortoise_events_poll`), folded by `tortoise/change_feed.py::aggregate_changes`
   — there is no `what_changed` symbol, and no new SDK verb or MCP tool.
4. **The type the switch writes is NAMEABLE** (review P2): `PointRevised` is a
   registered claim type, so `poll_events(types=["PointRevised"])` and a
   `PointRevised` webhook subscription work instead of raising
   `ValueError: unknown event type`.
5. **A non-content revision reports no `content`** (review P1): the store carries
   `new_content: null` for a validity-only edit (`update_point` always passes
   `new_content=props.get("content")`), and the fold must not turn that into an
   invented content CLEAR — nor OBSERVE it, which would fabricate the next
   delta's `before`.
6. **`PointRetracted`'s two producers are told apart** (review P2, then P1): the
   hard-delete emit marks its payload UNCONDITIONALLY — the marker records what
   the write path did, so gating it on the READER's switch let an unswitched
   writer's deletes fold as tombstones. A `delete_point` is reported as
   `deleted` with no fabricated `status`/`precondition`; `retract_point` keeps
   its tombstone delta. `PointPromoted`'s precondition is
   the union every producer enforces (`draft` or unset), not the CAS alone.
7. **No dead enumeration survives** (review P3): the dedupe `after` mapping that
   could never fire (the store payload is the bare `{"id": ...}` those emits
   write) is removed rather than left reading as a contract the feed does not
   honour.

NOTE ON THE E2E FIXTURE. The issue's E2E names "supersede + retract + evidence-add
on ONE point". That exact triple is UNREACHABLE: retract and supersede are both
terminal (`_ALLOWED_TRANSITIONS`; the shared lifecycle guard refuses the second),
so one point can carry at most one terminal transition. The fixture therefore
drives the reachable sequence on one point (create → revise → evidence-add →
supersede) and asserts the `retracted` delta on its own point, so every semantic
kind the E2E names is covered by a real write.

Runnable (embedded carve-out lane):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_change_feed_2353.py -q
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _graph_events(proj):
    rows = proj.g.query(
        "MATCH (e:GraphEvent) RETURN properties(e) ORDER BY e.seq").result_set
    return [r[0] for r in rows]


def _group(changes, claim_id):
    for g in changes:
        if g.get("id") == claim_id:
            return g
    return None


def _kinds(changes, claim_id):
    g = _group(changes, claim_id)
    return [c["change"] for c in (g["changes"] if g else [])]


# ── 1. the off switch adds no `changes` key ────────────────────────────────────

def test_off_by_default_adds_no_changes_key(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "off")
        sdk.update_point(p["id"], content="off-v2")
        resp = sdk.poll_events()
        assert set(resp) == {"events", "next_cursor"}, resp
        assert "changes" not in resp
    finally:
        sdk.close()


def test_off_by_default_keeps_revisions_out_of_the_store(monkeypatch, sdk_factory,
                                                         tmp_path):
    """The recorded decision, re-pinned: a content edit emits NOTHING to the
    :GraphEvent store unless the change feed is switched on."""
    monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "before")
        sdk.update_point(p["id"], content="after")
        assert [e["type"] for e in _graph_events(sdk._get_proj())] == ["PointAdded"]
    finally:
        sdk.close()


def test_falsey_spellings_are_off(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "0")
    sdk = sdk_factory(tmp_path)
    try:
        sdk.create_point("statement", "x")
        assert "changes" not in sdk.poll_events()
    finally:
        sdk.close()


# ── 2. PointRevised reaches the store when the feed is on ──────────────────

def test_revision_reaches_the_store_when_enabled(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "v1")
        sdk.update_point(p["id"], content="v2")
        sdk.update_point(p["id"], validTo="2030-01-01T00:00:00+00:00")
        rows = [e for e in _graph_events(sdk._get_proj())
                if e["type"] == "PointRevised"]
        assert len(rows) == 2, rows
        payloads = [json.loads(r["payload"]) for r in rows]
        assert all(pl.get("id") == p["id"] for pl in payloads), payloads
        # the revision payload rides the store, so a poll sees the new content
        assert any(pl.get("new_content") == "v2" for pl in payloads), payloads
    finally:
        sdk.close()


# ── 2b. the type the switch writes is NAMEABLE (review P2) ────────────────

def test_revision_type_is_filterable_when_enabled(monkeypatch, sdk_factory, tmp_path):
    """The store holds `PointRevised` while the switch is on, so the read side
    must let a caller FILTER for it — a hard `ValueError` would make the very
    type the feature exists to surface unnameable (#2353 review P2)."""
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "v1")
        sdk.update_point(p["id"], content="v2")
        assert "PointRevised" in [e["type"] for e in sdk.poll_events()["events"]]
        resp = sdk.poll_events(types=["PointRevised"])
        assert [e["type"] for e in resp["events"]] == ["PointRevised"]
        assert resp["events"][0]["payload"]["id"] == p["id"]
        # the fold reads the SAME (filtered) page — same assembly site
        assert _kinds(resp["changes"], p["id"]) == ["revised"]
    finally:
        sdk.close()


def test_revision_type_is_nameable_even_when_off(monkeypatch, sdk_factory, tmp_path):
    """Registration is NOT conditional even though the emit is: with the switch
    off the type is accepted and the page is simply empty (no row can exist) —
    never a `ValueError` that reads like a typo — and the reply's SHAPE is
    unchanged (no `changes` key). Not byte-identical: the hard-delete marker is
    unconditional (see the module docstring)."""
    monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
    sdk = sdk_factory(tmp_path)
    try:
        sdk.create_point("statement", "v1")
        resp = sdk.poll_events(types=["PointRevised"])
        assert set(resp) == {"events", "next_cursor"}, resp
        assert resp["events"] == []
    finally:
        sdk.close()


def test_revision_type_is_subscribable(monkeypatch, sdk_factory, tmp_path):
    """The OTHER read-side validator, driven through the REAL call the review
    measured: `create_subscription` gates on the same registry
    (`webhook_delivery._validated_types`), so a revisions subscription must be
    legal — an unfiltered subscription already delivers these rows."""
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    from tortoise import webhook_delivery as wd

    # the validator itself: registered vs unknown
    assert wd._validated_types(["PointRevised"]) == ["PointRevised"]
    assert wd._validated_types(["PointAdded", "PointRevised"]) == [
        "PointAdded", "PointRevised",
    ]
    with pytest.raises(ValueError, match="unknown event type"):
        wd._validated_types(["NotAType"])

    monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-static-pepper")
    monkeypatch.setenv("TORTOISE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    sdk = sdk_factory(tmp_path)
    try:
        sub = wd.create_subscription(
            sdk._get_proj(),
            url="https://hooks.example.com/revisions",
            types=["PointRevised"],
        )
        assert sub["types"] == ["PointRevised"]
        assert sub["secret"].startswith("whsec_")
    finally:
        sdk.close()


# ── 2c. a non-content revision invents no `content` (review P1) ─────────────

def test_non_content_revision_reports_no_fabricated_content():
    """`update_point` always ships `new_content=props.get("content")`, so a
    validity-only edit reaches the store with `new_content: None`. `None` means
    "no content edit" (#5240), so the delta must neither report `content: null`
    (a CLEAR nobody performed) nor observe it (which would fabricate the NEXT
    delta's `before`). Pure fold — fails on the pre-fix code both ways."""
    from tortoise.change_feed import aggregate_changes

    events = [
        {"seq": 1, "ts": "t1", "event_id": "e1", "type": "PointRevised",
         "payload": {"id": "p", "new_content": "v2"}},
        {"seq": 2, "ts": "t2", "event_id": "e2", "type": "PointRevised",
         "payload": {"id": "p", "new_content": None,
                     "validTo": "2030-01-01T00:00:00+00:00"}},
        {"seq": 3, "ts": "t3", "event_id": "e3", "type": "PointRevised",
         "payload": {"id": "p", "new_content": "v3"}},
    ]
    deltas = aggregate_changes(events)[0]["changes"]
    # the validity-only edit: no content claim at all
    assert deltas[1]["after"] == {"validTo": "2030-01-01T00:00:00+00:00"}
    assert "content" not in deltas[1]["after"]
    # and it no longer poisons the next `before` with the fabricated null
    assert deltas[2]["before"] == {"content": "v2"}
    assert deltas[2]["after"] == {"content": "v3"}


def test_validity_only_revision_reports_no_content(monkeypatch, sdk_factory, tmp_path):
    """The same contract driven through the REAL write path: a `validTo`-only
    edit between two content edits must not report a content CLEAR."""
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "v1")
        sdk.update_point(p["id"], content="v2")
        sdk.update_point(p["id"], validTo="2030-01-01T00:00:00+00:00")
        sdk.update_point(p["id"], content="v3")
        deltas = _group(sdk.poll_events()["changes"], p["id"])["changes"]
        assert [d["change"] for d in deltas] == [
            "created", "revised", "revised", "revised"]
        assert "content" not in deltas[2]["after"], deltas[2]
        assert deltas[2]["after"]["validTo"]
        assert deltas[3]["before"] == {"content": "v2"}, deltas[3]
        assert deltas[3]["after"] == {"content": "v3"}
    finally:
        sdk.close()


# ── 3. the feed is semantic ────────────────────────────────────────────────

def test_poll_events_changes_returns_per_claim_deltas(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "v1")
        sdk.update_point(p["id"], content="v2")
        sdk.update_point(p["id"], content="v3")
        ev = sdk.create_point("statement", "supporting")
        sdk.create_operator("IMPL", p["id"], [ev["id"]])
        succ = sdk.create_point("statement", "successor")
        sdk.supersede_point(p["id"], succ["id"])

        resp = sdk.poll_events()
        assert "changes" in resp
        deltas = _group(resp["changes"], p["id"])["changes"]
        assert [c["change"] for c in deltas] == [
            "created", "revised", "revised", "evidence-added", "superseded",
        ]

        # before→after for CONTENT: from the SECOND revision on, the window
        # holds the previously observed content. The creation record carries
        # only `content_hash` (the store's PointAdded payload), so the FIRST
        # in-window revision reports no `before` rather than an invented one.
        assert "before" not in deltas[1]
        assert deltas[2]["before"] == {"content": "v2"}
        assert deltas[2]["after"] == {"content": "v3"}

        # STATUS: nothing in the window observed the pre-transition status
        # (the PointAdded payload carries no status), so the delta reports the
        # write path's PRECONDITION under its own key — never a fabricated
        # `before.status`. The transition itself is the `change` name.
        superseded = deltas[4]
        assert superseded["after"]["status"] == "superseded"
        assert superseded["after"]["supersededBy"] == succ["id"]
        assert superseded["precondition"] == {"terminal": False}
        assert "before" not in superseded

        # before→after for VALIDITY: the supersede's recorded window close.
        assert superseded["after"]["validTo"]
    finally:
        sdk.close()


def test_retraction_reports_its_own_kind(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "doomed")
        sdk.retract_point(p["id"])
        g = _group(sdk.poll_events()["changes"], p["id"])
        assert [c["change"] for c in g["changes"]] == ["created", "retracted"]
        assert g["changes"][1]["after"] == {"status": "retracted"}
    finally:
        sdk.close()


def test_hard_delete_is_not_reported_as_a_retraction(monkeypatch, sdk_factory,
                                                      tmp_path):
    """`PointRetracted` has two producers (#3300). The hard delete DESTROYS the
    node, so the delta must not claim `status: retracted` (fabricated) nor the
    tombstone's `terminal: False` precondition (no guard exists on that path —
    the delete of an already-retracted point proves it). Reported as `deleted`.
    """
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "doomed")
        sdk.retract_point(p["id"])
        assert sdk.delete_point(p["id"]) is True
        deltas = _group(sdk.poll_events()["changes"], p["id"])["changes"]
        assert [d["change"] for d in deltas] == [
            "created", "retracted", "deleted"]
        # the TOMBSTONE keeps its own end-state
        assert deltas[1]["after"] == {"status": "retracted"}
        assert deltas[1]["precondition"] == {"terminal": False}
        # the DELETE states no status and carries no guard it does not have
        delete = deltas[2]
        assert delete["after"] == {}
        assert "precondition" not in delete, delete
        assert "status" not in delete["after"]
    finally:
        sdk.close()


def test_hard_delete_of_a_live_point_keeps_the_deleted_kind(monkeypatch,
                                                            sdk_factory, tmp_path):
    """The live-point hard delete (the other producer repro): same honest delta."""
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "live then gone")
        sdk.delete_point(p["id"])
        deltas = _group(sdk.poll_events()["changes"], p["id"])["changes"]
        assert [d["change"] for d in deltas] == ["created", "deleted"]
        assert deltas[1]["after"] == {}
        assert "precondition" not in deltas[1]
    finally:
        sdk.close()


def test_hard_delete_marker_records_the_write_not_the_reader(
        monkeypatch, sdk_factory, tmp_path):
    """The `deleted` marker is UNCONDITIONAL; the RESPONSE half is not.

    The marker states what the WRITE PATH did, so it cannot be a function of
    whether the READING process has the feed switched on. Gating it on the
    switch let a deployment whose writer has the feed off and whose reader has
    it on fold every hard delete as a tombstone — a fabricated
    `status: retracted` plus a `precondition` the delete path never enforces (a
    review P1), and it mislabelled every row written before the switch existed.
    The response half stays off-contract: no `changes` key.
    """
    monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "gone")
        assert sdk.delete_point(p["id"]) is True
        rows = [e for e in _graph_events(sdk._get_proj())
                if e["type"] == "PointRetracted"]
        assert len(rows) == 1, rows
        assert json.loads(rows[0]["payload"]) == {"id": p["id"], "deleted": True}
        assert "changes" not in sdk.poll_events()
    finally:
        sdk.close()


def test_evidence_add_is_news_for_both_endpoints(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        src = sdk.create_point("statement", "claim")
        tgt = sdk.create_point("statement", "evidence")
        op = sdk.create_operator("IMPL", src["id"], [tgt["id"]])
        changes = sdk.poll_events()["changes"]
        for cid in (src["id"], tgt["id"], op["id"]):
            assert "evidence-added" in _kinds(changes, cid), cid
    finally:
        sdk.close()


def test_handler_annotations_report_before_after(monkeypatch, sdk_factory, tmp_path):
    """`OperatorAnnotated` names the EP dims — the delta compares observed values."""
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        a = sdk.create_point("statement", "a")
        b = sdk.create_point("statement", "b")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        sdk.annotate_operator(op["id"], 0.5, 0.5, 0.5, 0.5)
        sdk.annotate_operator(op["id"], 0.9, 0.5, 0.5, 0.5)
        ann = [c for c in _group(sdk.poll_events()["changes"], op["id"])["changes"]
               if c["change"] == "annotated"]
        assert len(ann) == 2
        assert ann[1]["before"]["bias"] == 0.5
        assert ann[1]["after"]["bias"] == 0.9
        assert "precondition" not in ann[1]
    finally:
        sdk.close()


# ── 4. the cursor scopes the feed ─────────────────────────────────────────

def test_cursor_scopes_the_feed(monkeypatch, sdk_factory, tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "v1")
        cursor = sdk.poll_events()["next_cursor"]     # consumed the create
        sdk.update_point(p["id"], content="v2")
        resp = sdk.poll_events(after=cursor)
        assert [c["change"] for c in _group(resp["changes"], p["id"])["changes"]] == [
            "revised",
        ]
        # the create is behind the cursor and is NOT re-reported
        assert all(c["change"] != "created" for c in resp["changes"][0]["changes"])
    finally:
        sdk.close()


# ── 5. a stream is allowed to grow types this build does not know ───────────

def test_unknown_type_and_shapeless_payload_are_skipped(monkeypatch, sdk_factory,
                                                        tmp_path):
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        from tortoise import event_store
        proj = sdk._get_proj()
        sdk.create_point("statement", "known")
        event_store.ensure_event_schema(proj)
        event_store.append_event(proj, event_store.next_seq(proj),
                                 "SomeFutureEvent", {"id": "future"}, "ev-future")
        # a shaped row whose payload decoded to a non-mapping must not raise
        proj.g.query(
            "CREATE (e:GraphEvent {seq: 9900, ts: 'x', type: 'PointRevised', "
            "payload: '[1,2,3]', event_id: 'ev-list'})")
        changes = sdk.poll_events()["changes"]
        assert all(g["id"] != "future" for g in changes)
        assert changes, "the feed must still describe the rows it does understand"
    finally:
        sdk.close()


def test_pure_fold_needs_no_graph(monkeypatch):
    """`aggregate_changes` is a pure function over rows — assertable with no DB,
    which is what keeps the field cheap enough to sit on a read path."""
    from tortoise.change_feed import aggregate_changes
    events = [
        {"seq": 2, "ts": "t2", "event_id": "e2", "type": "PointSuperseded",
         "payload": {"id": "p1", "new_id": "p2", "valid_to": "2030-01-01"}},
        {"seq": 1, "ts": "t1", "event_id": "e1", "type": "PointAdded",
         "payload": {"id": "p1", "kind": "statement"}},
    ]
    out = aggregate_changes(events)
    # `p2` is the SUCCESSOR (named in `after.supersededBy`), not a subject: the
    # supersede is news about `p1`, and labelling `p2` "superseded" would state
    # the opposite of what happened.
    assert [g["id"] for g in out] == ["p1"]
    # seq order, not input order
    assert [c["seq"] for c in out[0]["changes"]] == [1, 2]
    assert out[0]["changes"][1]["precondition"] == {"terminal": False}
    assert "before" not in out[0]["changes"][1]
    assert out[0]["changes"][1]["after"]["supersededBy"] == "p2"


def test_revision_status_before_after_is_observed_only(monkeypatch):
    """Status before→after is reported when the window OBSERVED the prior
    status (a promote is a PointRevised carrying `status`), and omitted — never
    fabricated — for the first sighting.

    The second row IS the promote door. `update_point(status='live')` emits
    `PointRevised`, not `PointPromoted`, and CASes the same draft-or-unset
    precondition, so it is named `promoted` and carries that precondition —
    otherwise a consumer counting promotions from the feed misses every one made
    through the ordinary update path (a review P2).
    """
    from tortoise.change_feed import aggregate_changes
    events = [
        {"seq": 1, "ts": "t1", "event_id": "e1", "type": "PointRevised",
         "payload": {"id": "p", "new_content": "a", "status": "draft"}},
        {"seq": 2, "ts": "t2", "event_id": "e2", "type": "PointRevised",
         "payload": {"id": "p", "status": "live"}},
    ]
    deltas = aggregate_changes(events)[0]["changes"]
    assert "before" not in deltas[0]
    assert deltas[0]["change"] == "revised"
    assert deltas[1]["before"] == {"status": "draft"}
    assert deltas[1]["after"] == {"status": "live"}
    assert deltas[1]["change"] == "promoted"
    assert deltas[1]["precondition"] == {"status": ["draft", None]}


def test_dedupe_delta_reports_the_event_without_inventing_fields():
    """Every in-tree dedupe emit uses the `point=` form, so the store payload
    is the bare `{"id": ...}` — the candidate's outcome fields are the caller's
    return value and never ride the store. The fold reports the event by name
    and invents nothing (the pre-fix arm enumerated keys that could not appear).
    """
    from tortoise.change_feed import aggregate_changes

    events = [
        {"seq": 1, "ts": "t1", "event_id": "e1", "type": "DedupeRecorded",
         "payload": {"id": "cand"}},
        {"seq": 2, "ts": "t2", "event_id": "e2", "type": "DedupeRejected",
         "payload": {"id": "cand"}},
    ]
    deltas = aggregate_changes(events)[0]["changes"]
    assert [d["change"] for d in deltas] == ["dedupe-recorded", "dedupe-rejected"]
    assert deltas[0]["after"] == {}
    assert deltas[1]["after"] == {}


def test_only_a_hard_delete_clears_the_observation():
    """A dedupe row folds to `{}` but the subject is still there.

    The hard-delete path drops the claim's observed state, so the re-create that
    follows reports no `before`. A `DedupeRecorded` / `DedupeRejected` row also
    folds to an empty `after` and must NOT — keying the drop on `not after`
    instead of `_is_hard_delete` would silently forget a live claim's history and
    nothing else here would notice, because for a dedupe row the two rules are
    indistinguishable in the reply. Pure fold: fails if the pop is keyed on an
    empty `after`.
    """
    from tortoise.change_feed import aggregate_changes

    events = [
        {"seq": 1, "ts": "t1", "event_id": "e1", "type": "PointAdded",
         "payload": {"id": "p"}},
        {"seq": 2, "ts": "t2", "event_id": "e2", "type": "PointRevised",
         "payload": {"id": "p", "new_content": "v1"}},
        {"seq": 3, "ts": "t3", "event_id": "e3", "type": "DedupeRecorded",
         "payload": {"id": "p"}},
        {"seq": 4, "ts": "t4", "event_id": "e4", "type": "PointRevised",
         "payload": {"id": "p", "new_content": "v2"}},
    ]
    deltas = aggregate_changes(events)[0]["changes"]
    assert [d["change"] for d in deltas] == [
        "created", "revised", "dedupe-recorded", "revised"
    ]
    assert deltas[3]["before"] == {"content": "v1"}


def test_promoted_precondition_admits_an_unset_prior_status():
    """`PointPromoted` has two producers: `promote_point` CASes `status='draft'`
    and capture auto-promotion admits an UNSET status too (sdk.py:3990-3995). A
    precondition is what the write path refuses WITHOUT, so it must be the union
    — `draft` alone overstates the capture leg. Pure fold: fails pre-fix."""
    from tortoise.change_feed import aggregate_changes
    events = [
        {"seq": 1, "ts": "t1", "event_id": "e1", "type": "PointPromoted",
         "payload": {"id": "p"}},
    ]
    delta = aggregate_changes(events)[0]["changes"][0]
    assert delta["change"] == "promoted"
    assert delta["after"] == {"status": "live"}
    assert delta["precondition"] == {"status": ["draft", None]}


def test_capture_auto_promotion_of_an_unset_status_claim_is_reported(
        monkeypatch, sdk_factory, tmp_path):
    """The capture leg admits an UNSET prior status (`status IS NULL OR
    'draft'`) and emits the SAME `PointPromoted` type `promote_point` does, so
    the reported precondition must be the union both producers enforce — pinned
    through the REAL capture producer, not only the fold."""
    from tortoise.sdk import _apply_capture_ingest_ep

    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "unset then promoted")
        proj = sdk._get_proj()
        # the capture writer mints claims WITHOUT a status on the legacy path
        proj.g.query("MATCH (n:Point {id:$id}) REMOVE n.status",
                     params={"id": p["id"]})
        _apply_capture_ingest_ep(sdk, [p["id"]], promotion_ids=[p["id"]],
                                 operator_ids=[])
        promoted = [d for d in _group(sdk.poll_events()["changes"], p["id"])["changes"]
                    if d["change"] == "promoted"]
        assert len(promoted) == 1, promoted
        assert promoted[0]["after"] == {"status": "live"}
        assert promoted[0]["precondition"] == {"status": ["draft", None]}
    finally:
        sdk.close()


def test_supersede_is_news_about_the_superseded_claim(monkeypatch, sdk_factory,
                                                      tmp_path):
    """The successor is named, never mislabelled: it must NOT appear as a
    `superseded` group of its own."""
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        old = sdk.create_point("statement", "old")
        new = sdk.create_point("statement", "new")
        sdk.supersede_point(old["id"], new["id"])
        changes = sdk.poll_events()["changes"]
        assert _kinds(changes, old["id"])[-1] == "superseded"
        assert _kinds(changes, new["id"]) == ["created"]
    finally:
        sdk.close()


def test_existing_mcp_tool_relays_the_field_without_a_surface_change(
        monkeypatch, tmp_path):
    """The field is reachable through the EXISTING `tortoise_events_poll` tool:
    the tool's declared parameters are unchanged (MCP tool count +0), and the
    off switch leaves the reply's SHAPE unchanged (no `changes` key; not
    byte-identical, see the module docstring).

    That the served handler fingerprint is untouched is pinned separately by
    `tools/surface-guard.py` / `tools/surface_manifest.py check` (which compare
    the frozen baseline against a fresh derivation from the code) — the point
    HERE is that the capability is reachable at all, on the approved read.
    """
    import inspect

    from tortoise.mcp_server import _transport_mode, tortoise_events_poll
    from tortoise.sdk import TortoiseSDK

    assert list(inspect.signature(tortoise_events_poll).parameters) == [
        "after", "types", "limit",
    ]
    sdk = TortoiseSDK(os.path.join(str(tmp_path), "evt-mcp.db"))
    monkeypatch.setattr("tortoise.mcp_server._get_org_sdk", lambda: sdk)
    token = _transport_mode.set("stdio")
    try:
        p = sdk.create_point("statement", "mcp relay")
        monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
        assert "changes" not in tortoise_events_poll()
        monkeypatch.setenv("TORTOISE_CHANGE_FEED", "on")
        out = tortoise_events_poll()
        assert _kinds(out["changes"], p["id"]) == ["created"]
    finally:
        _transport_mode.reset(token)
        sdk.close()


def test_enabled_reads_the_declared_truthy_contract(monkeypatch):
    from tortoise.change_feed import CHANGE_FEED_ENV, enabled
    for raw, want in (("1", True), ("TRUE", True), ("on", True), ("yes", True),
                      ("0", False), ("off", False), ("", False), ("garbage", False)):
        monkeypatch.setenv(CHANGE_FEED_ENV, raw)
        assert enabled() is want, raw
    monkeypatch.delenv(CHANGE_FEED_ENV, raising=False)
    assert enabled() is False


def test_a_hard_delete_written_unswitched_still_folds_as_a_delete(
        monkeypatch, sdk_factory, tmp_path):
    """The P1's exact scenario: the WRITER has the feed off, the READER on.

    Gating the marker on the emitting process's switch made the distinction a
    property of a process rather than of the row, so this fold reported
    `retracted` — with a fabricated `after.status` and a `precondition` the
    delete path never enforces — for a node that is provably gone.
    """
    monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "gone")
        assert sdk.delete_point(p["id"]) is True
        # …now the READER switches the feed on over the same store.
        monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
        assert _kinds(sdk.poll_events()["changes"], p["id"]) == ["created", "deleted"]
    finally:
        sdk.close()


def test_the_update_promote_door_is_reported_as_a_promotion(
        monkeypatch, sdk_factory, tmp_path):
    """`update_point(status='live')` IS a promote door but emits `PointRevised`.

    Pinned end-to-end through the REAL producer rather than a hand-built
    payload: the fold reads `status` out of the emitted props, so if
    `update_point` ever stops putting it there every promotion silently reverts
    to `revised` with no precondition — and the whole suite would stay green (a
    review P2).
    """
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "drafty", status="draft")
        sdk.update_point(p["id"], status="live")
        groups = {g["id"]: g["changes"] for g in sdk.poll_events()["changes"]}
        promote = [d for d in groups[p["id"]] if d["change"] == "promoted"]
        assert promote, groups[p["id"]]
        assert promote[0]["after"] == {"status": "live"}
        assert promote[0]["precondition"] == {"status": ["draft", None]}
    finally:
        sdk.close()


def test_one_annotation_call_is_one_delta(monkeypatch, sdk_factory, tmp_path):
    """`annotate_operator` emits `OperatorAnnotated` AND a JSONL-parity
    `PointRevised` carrying the same numbers under the same folded keys.

    The second is not a revision. Reporting it made every annotation count as an
    extra `revised` whose `after` equalled its own `before` (a review P2); a
    delta that changes nothing is not news.
    """
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        src = sdk.create_point("statement", "claim")
        tgt = sdk.create_point("statement", "evidence")
        op = sdk.create_operator("IMPL", src["id"], [tgt["id"]])
        sdk.annotate_operator(op["id"], 0.9, 0.8, 0.7, 0.6)
        kinds = _kinds(sdk.poll_events()["changes"], op["id"])
        assert kinds.count("annotated") == 1, kinds
        assert "revised" not in kinds, kinds
    finally:
        sdk.close()


def test_a_recreated_point_is_reported_as_a_second_creation(monkeypatch, sdk_factory, tmp_path):
    """A hard delete CLEARS the subject's observed state, so a re-create is news.

    The no-op suppression drops a delta that repeats the IMMEDIATELY PRECEDING
    one for its subject. A hard delete carries `after == {}` and clears both
    that slot and the subject's observed state, which is what keeps
    delete-then-re-create from being swallowed — the re-created point's `after`
    repeats its pre-delete values, so suppressing on "we have seen these values
    before" would report `['created', 'deleted']` for a claim that is present in
    the graph (a review P2 on this PR's own no-op fix).

    Clearing the OBSERVED state too is what makes the re-create a birth rather
    than a content transition: `delete_point` destroyed the old incarnation, so
    reporting its fields as the new node's `before` would claim a change that
    never happened (and, on a same-content re-create, `before == after` on a
    `created`). A review P2.
    """
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        first = sdk.create_point("statement", "resurrect-me")
        sdk.delete_point(first["id"])
        sdk.create_point("statement", "resurrect-me", id=first["id"])
        changes = sdk.poll_events()["changes"]
        kinds = _kinds(changes, first["id"])
        assert kinds == ["created", "deleted", "created"], kinds
        recreated = next(
            c for c in changes if c["id"] == first["id"])["changes"][-1]
        assert "before" not in recreated, recreated
    finally:
        sdk.close()


def test_operator_promote_precondition_admits_an_unset_status():
    """`OperatorPromoted` has two producers with different admission predicates.

    `_promote_incident_operators` CASes `status='draft'`; the capture
    `_apply_capture_ingest_ep` admits an UNSET status too. A precondition is the
    predicate the write path refuses WITHOUT, so it is the union — `draft` alone
    claims a prior status the capture leg never required (a review P2).
    """
    from tortoise.change_feed import aggregate_changes
    events = [{"seq": 1, "ts": "t", "event_id": "e", "type": "OperatorPromoted",
               "payload": {"id": "op"}}]
    delta = aggregate_changes(events)[0]["changes"][0]
    assert delta["precondition"] == {"status": ["draft", None]}
    assert delta["after"] == {"status": "live"}


def test_envelope_actor_is_not_claim_state():
    """`_emit_event` merges `actor_user_id` for the STORE, so a catch-all
    revision arm would report it as claim state — and change the delta's field
    set depending on whether a human was attached (a review P2)."""
    from tortoise.change_feed import aggregate_changes
    events = [{"seq": 1, "ts": "t", "event_id": "e", "type": "PointRevised",
               "payload": {"id": "p", "new_content": "v2",
                           "actor_user_id": "daniel-ospina"}}]
    delta = aggregate_changes(events)[0]["changes"][0]
    assert delta["after"] == {"content": "v2"}


def test_annotation_dimensions_use_one_spelling():
    """`annotate_operator` emits `OperatorAnnotated` (short aliases) and then
    `update_point(..., annotator_bias=...)`, which emits `PointRevised`. One EP
    dimension must not reach the feed under two names — a consumer folding
    `after` would hold both for one number (a review P2)."""
    from tortoise.change_feed import aggregate_changes
    events = [{"seq": 1, "ts": "t", "event_id": "e", "type": "PointRevised",
               "payload": {"id": "op", "annotator_bias": 0.5,
                           "annotator_precision": 0.4}}]
    delta = aggregate_changes(events)[0]["changes"][0]
    assert delta["after"] == {"bias": 0.5, "precision": 0.4}


def test_superseded_reports_the_legacy_outdated_flag():
    """`supersede_point` writes the legacy `outdated` flag alongside the status,
    and the `PointInvalidated` arm reports its own — a consumer folding `after`
    must not see one terminal transition carry it and the other omit it, on the
    same write (a review P2)."""
    from tortoise.change_feed import aggregate_changes
    events = [{"seq": 1, "ts": "t", "event_id": "e", "type": "PointSuperseded",
               "payload": {"id": "p", "new_id": "p2"}}]
    delta = aggregate_changes(events)[0]["changes"][0]
    assert delta["after"] == {"status": "superseded", "outdated": True,
                              "supersededBy": "p2"}


def test_the_delta_and_the_raw_page_do_not_share_objects():
    """`after` is built from the payload's own objects and the SAME response
    carries that payload back under `events`; without a copy, normalising one
    delta's `after` mutates the raw page — and every sibling delta for the same
    event (a review P2)."""
    from tortoise.change_feed import aggregate_changes
    payload = {"id": "op", "op_type": "IMPL", "target_ids": ["t1"]}
    events = [{"seq": 1, "ts": "t", "event_id": "e", "type": "OperatorAdded",
               "payload": payload}]
    delta = aggregate_changes(events)[0]["changes"][0]
    assert delta["after"]["target_ids"] == ["t1"]
    delta["after"]["target_ids"].append("t2")
    assert payload["target_ids"] == ["t1"]


def test_precondition_objects_are_not_shared_between_calls():
    """A shallow `dict(...)` leaves the value OBJECT shared with the module
    constant, so a consumer mutating what it was handed corrupts the
    precondition every later call in this long-lived process reports (a review
    P2)."""
    from tortoise.change_feed import _PRECONDITIONS, aggregate_changes
    events = [{"seq": 1, "ts": "t", "event_id": "e", "type": "PointPromoted",
               "payload": {"id": "p"}}]
    delta = aggregate_changes(events)[0]["changes"][0]
    delta["precondition"]["status"].append("CORRUPTED")
    assert _PRECONDITIONS["PointPromoted"]["status"] == ["draft", None]
    again = aggregate_changes(events)[0]["changes"][0]
    assert again["precondition"] == {"status": ["draft", None]}

# ── 6. the issue's indicators, as executable closing evidence ────────────────
#
# #8705 (MERGED) landed the implementation. This section is the CLOSING
# EVIDENCE for the O/I/T the issue #2353 states, so "the indicators hold on this
# tree" is a test run rather than a reading of the sections above. Nothing here
# adds behaviour: every assertion drives the REAL write path or reads a
# committed record, and no production code is touched.

def test_indicator_1_point_revised_has_store_parity_under_the_switch(
        monkeypatch, sdk_factory, tmp_path):
    """Indicator (1): `PointRevised` lands in the `:GraphEvent` store with
    PARITY with PointAdded/PointSuperseded/PointRetracted — under
    `TORTOISE_CHANGE_FEED`, the recorded opt-in (widening the DEFAULT emit set
    would reverse the plan-review decision this module's docstring names).

    Both halves matter, so both are measured: ON, all four transitions are in the
    same store page; OFF, the three unconditional ones are still there and ONLY
    the revision is absent.
    """
    def _types(sdk):
        return [e["type"] for e in _graph_events(sdk._get_proj())]

    def _drive(sdk):
        claim = sdk.create_point("statement", "v1")
        sdk.update_point(claim["id"], content="v2")   # the gated record
        succ = sdk.create_point("statement", "successor")
        sdk.supersede_point(claim["id"], succ["id"])
        doomed = sdk.create_point("statement", "doomed")
        sdk.retract_point(doomed["id"])

    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    on = sdk_factory(tmp_path)
    try:
        _drive(on)
        on_types = _types(on)
    finally:
        on.close()

    for t in ("PointAdded", "PointRevised", "PointSuperseded",
              "PointRetracted"):
        assert t in on_types, f"{t} missing from the store: {sorted(set(on_types))}"

    monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
    off = sdk_factory(tmp_path)
    try:
        _drive(off)
        off_types = _types(off)
    finally:
        off.close()

    # PARITY BY MULTISET DIFFERENCE, not three membership checks: the OFF emit
    # list must be the ON one MINUS exactly one `PointRevised` and nothing
    # else. A second gated type would slip past a membership list; a DUPLICATE
    # emit under the switch — the regression class this feature is most exposed
    # to — would slip past a set comparison, so the multiset is the assertion.
    assert sorted(on_types) == sorted([*off_types, "PointRevised"]), (
        f"ON={sorted(on_types)} OFF={sorted(off_types)}")


def test_indicator_2_one_call_reports_content_validity_status_and_ep(
        monkeypatch, sdk_factory, tmp_path):
    """Indicator (2): ONE what-changed read returns per-point deltas carrying
    before→after for all four field families the issue names — content,
    validity, status and the EP dimensions — aggregated, not a raw replay.
    """
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        # A DRAFT claim, so the promote door is reachable at all: a point is
        # created `live`, and `update_point(status="live")` on an already-live
        # point is a recorded no-op (it emits nothing).
        p = sdk.create_point("statement", "v1", status="draft")
        sdk.update_point(p["id"], content="v2")
        sdk.update_point(p["id"], content="v3")
        sdk.update_point(p["id"], validTo="2030-01-01T00:00:00+00:00")
        sdk.update_point(p["id"], status="live")     # the promote door
        ev = sdk.create_point("statement", "evidence")
        op = sdk.create_operator("IMPL", p["id"], [ev["id"]])
        sdk.annotate_operator(op["id"], 0.5, 0.5, 0.5, 0.5)
        sdk.annotate_operator(op["id"], 0.9, 0.5, 0.5, 0.5)
        succ = sdk.create_point("statement", "successor")
        sdk.supersede_point(p["id"], succ["id"])

        resp = sdk.poll_events()                     # ONE call
        deltas = _group(resp["changes"], p["id"])["changes"]

        # CONTENT: observed before→after.
        assert [d for d in deltas
                if d.get("before", {}).get("content") == "v2"
                and d["after"].get("content") == "v3"]
        # VALIDITY: the revision that ONLY sets a window (no content edit).
        validity_only = next(d for d in deltas if set(d["after"]) == {"validTo"})
        assert validity_only["after"]["validTo"] == "2030-01-01T00:00:00+00:00"
        # STATUS: the promote door reports the value AND its own precondition.
        promoted = [d for d in deltas if d["change"] == "promoted"]
        assert promoted and promoted[0]["after"]["status"] == "live"
        assert promoted[0]["precondition"] == {"status": ["draft", None]}
        # STATUS: terminal transition, named and guarded — never a fake before.
        terminal = next(d for d in deltas if d["change"] == "superseded")
        assert terminal["after"]["status"] == "superseded"
        assert terminal["after"]["supersededBy"] == succ["id"]
        assert terminal["precondition"] == {"terminal": False}
        # ...and BOTH of those families report before→after here, because the
        # window observed the promote (status) and the window edit (validTo).
        assert terminal["before"]["status"] == "live"
        assert terminal["before"]["validTo"] == "2030-01-01T00:00:00+00:00"
        assert terminal["after"]["validTo"] != terminal["before"]["validTo"]
        # EP-RELEVANT FIELDS: observed before→after on the operator.
        ann = [d for d in _group(resp["changes"], op["id"])["changes"]
               if d["change"] == "annotated"]
        assert len(ann) == 2, [d["change"] for d in ann]
        assert set(ann[1]["after"]) == {"bias", "precision", "consistency",
                                       "directness"}
        assert ann[1]["before"]["bias"] == 0.5
        assert ann[1]["after"]["bias"] == 0.9

        # AGGREGATED, not raw replay, MEASURED: the SAME single OperatorAdded
        # row is reported as news for all three claims it names (the operator,
        # the point it supports, the point it relates). A raw replay yields one
        # row; the fold yields three groups — that is the work the fold does.
        op_seq = next(e["seq"] for e in resp["events"]
                      if e["type"] == "OperatorAdded")
        named_by_the_one_row = {g["id"] for g in resp["changes"]
                                if any(d["seq"] == op_seq for d in g["changes"])}
        assert named_by_the_one_row == {p["id"], ev["id"], op["id"]}
    finally:
        sdk.close()


def test_indicator_3_a_consumer_answers_the_audit_question_from_changes_only(
        monkeypatch, sdk_factory, tmp_path):
    """Indicator (3): a consumer drives off the semantic feed without
    re-deriving intent — the supersession-history/audit answer is reconstructed
    from `changes` ALONE, never from the raw `events` page.
    """
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    sdk = sdk_factory(tmp_path)
    try:
        p = sdk.create_point("statement", "v1")
        sdk.update_point(p["id"], content="v2")
        succ = sdk.create_point("statement", "successor")
        sdk.supersede_point(p["id"], succ["id"])

        changes = sdk.poll_events()["changes"]       # the ONLY input read

        def history(claim_id):
            g = _group(changes, claim_id) or {"changes": []}
            return [d["change"] for d in g["changes"]]

        def belief(claim_id):
            """Current status + why — folded from `change`/`after`, no raw replay.

            The `deleted` kind ENDS the incarnation, so it clears the answer
            instead of carrying the pre-delete status forward: reporting "still
            superseded" for a DESTROYED node is the fabricated reading the
            module docstring calls load-bearing (`test_hard_delete_is_not_
            reported_as_a_retraction` pins that such a row's `after` is `{}`).
            """
            g = _group(changes, claim_id) or {"changes": []}
            status, superseded_by = None, None
            for d in g["changes"]:
                if d["change"] == "deleted":
                    status, superseded_by = None, None
                    continue
                after = d["after"]
                status = after.get("status", status)
                superseded_by = after.get("supersededBy", superseded_by)
            return status, superseded_by

        assert history(p["id"]) == ["created", "revised", "superseded"]
        assert belief(p["id"]) == ("superseded", succ["id"])
        # the successor's own history is NOT mislabelled as the superseded one
        assert "superseded" not in history(succ["id"])

        # ...and a destroyed claim is reported GONE, not as its last status.
        destroyed = sdk.create_point("statement", "gone")
        sdk.update_point(destroyed["id"], content="gone-v2")
        sdk.retract_point(destroyed["id"])
        sdk.delete_point(destroyed["id"])
        changes = sdk.poll_events()["changes"]
        assert history(destroyed["id"])[-1] == "deleted"
        assert belief(destroyed["id"]) == (None, None)
    finally:
        sdk.close()


def test_indicator_3_cost_is_a_recorded_response_field_not_a_tool_or_verb():
    """The issue's surface target — **MCP tool count +0, SDK verb count +0 (no
    new public method)** — measured against the committed records: `changes`
    rides TWO `response_fields` rows on the EXISTING poll surface (the #3863
    carve-out), and the fold is neither a tool nor an SDK verb.
    """
    from pathlib import Path

    import yaml

    from tortoise.sdk import TortoiseSDK
    from tortoise.tool_registry import TOOL_REGISTRY

    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load(
        (root / "config" / "surface-manifest.yml").read_text())
    rows = {(r["response"], r["field"]) for r in manifest["response_fields"]}
    assert ("tortoise_events_poll", "changes") in rows
    assert ("sdk:poll_events", "changes") in rows

    # NAMED symbols, not a name-substring sweep: a sweep for "change" is a
    # false green for exactly the addition it claims to prevent (`what_changed`
    # / `delta_for_point` contain no "change" prefix match failure the sweep
    # would catch) and a false red for any unrelated future `exchange_*` name.
    assert [t.name for t in TOOL_REGISTRY
            if t.sdk_method == "poll_events"] == ["tortoise_events_poll"]
    assert not [t for t in TOOL_REGISTRY
                if t.sdk_method in {"aggregate_changes", "what_changed"}]
    for sym in ("what_changed", "aggregate_changes", "aggregate_change_feed"):
        assert not hasattr(TortoiseSDK, sym), f"{sym} must not be an SDK verb"
    assert "poll_events" in {m for m in dir(TortoiseSDK)
                             if not m.startswith("_")}
