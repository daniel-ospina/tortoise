"""#5516 — a write-time notice when the Source being written against has changed.

The write-time half of #5038. When a fact is written against a ``:Source`` whose
current ``contentHash`` no longer matches **the version the writer last read** —
the version recorded on the ``extractedFrom`` link of the last fact written from
that Source (#5256's ``sourceVersion`` anchor) — the write result carries an
advisory notice naming the Source and the version change, so the writer can
decide what to do instead of discovering the staleness only at search.

The three indicators, each pinned by its own test:

1. a write against a CHANGED Source produces a visible, non-fatal notice naming
   the Source and the version change;
2. the notice is ADVISORY — it never blocks, mutates, or fails the write, and a
   broken notice computation cannot take the write down with it;
3. a write against an UNCHANGED Source produces NO notice.

Design notes (settled here, not by an owner decision — no recorded decision is
contradicted, so no reopen is owed):

* The comparison is a READ (Policy B, #5038 comment ``5814346672``): the
  recorded read version is read off the existing ``extractedFrom`` link and
  compared with the Source's current ``contentHash``. No stored status field is
  written, and the write path is not changed.
* The notice rides the **write result** the writer sees and NOWHERE else: not
  the node, not the journal, not the Source. It is transient by construction.
  The write result is ``create_point``'s return (the write door the
  capture/extraction paths use) AND the capture receipt — the v2 point loop
  discards the per-point result, so the receipt aggregates it; otherwise the
  notice would reproduce the #4041 "written-but-unread" class.
* The fire/silence DECISION is exact and order-independent: the Source has
  changed iff its current ``contentHash`` is **not recorded as read** on any of
  its ``extractedFrom`` links. Once the current version has been read, the notice
  cannot re-fire — including when a Source reverts to a previously-read version,
  because that exact content HAS been read (see
  ``test_revert_to_a_previously_read_version_is_silent``).
* ``previousVersion`` is a *best-effort representative* prior read, not provably
  "the last read": the create seam records no writer identity or read instant on
  the link, and ``createdAt`` is a caller-owned logical date. When several prior
  versions are recorded, the greatest ``createdAt`` is named; the decision above
  never depends on it.
* No per-writer scoping is derivable at this seam (no writer identity on the
  link), so the notice answers "has this Source changed since its last recorded
  read" — the stronger, still-true statement.
* Absent/blank on either side is honest-absent: a Source never read before, or a
  Source without a hash, produces NO notice — never a false "changed".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK

DOC = "https://doc.example/5516"
OTHER = "https://doc.example/5516-other"

#: The write-result key the notice rides. Absent (not empty) when silent.
NOTICE_KEY = "source_change_notices"


@pytest.fixture()
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    yield s
    s.close()


def _notices(result: dict) -> list[dict]:
    return result.get(NOTICE_KEY) or []


def _source_hash(sdk, url: str):
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$url}) RETURN s.contentHash",
        params={"url": url}).result_set
    return rows[0][0] if rows else "NO_SOURCE"


def _recorded_version(sdk, point_id: str):
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id})-[r:extractedFrom]->(:Source) "
        "RETURN r.sourceVersion",
        params={"id": point_id}).result_set
    return rows[0][0] if rows else "NO_EDGE"


# ── indicator 1 — a changed Source produces a notice ───────────────────────


def test_changed_source_write_emits_a_notice(sdk):
    """Indicator 1: the write against a changed Source names it and the change.

    FAILS IF the second write's result carries no notice, does not name the
    Source, or reports anything other than the prior recorded read (``h1``) and
    the Source's current version (``h2``).
    """
    sdk.create_source(DOC, "document", contentHash="h1")
    first = sdk.create_point("statement", "the first fact", extractedFrom=DOC)
    # Nothing recorded yet to compare against: no notice on the first read.
    assert _notices(first) == []

    sdk.create_source(DOC, "document", contentHash="h2")
    second = sdk.create_point("statement", "the second fact", extractedFrom=DOC)

    notices = _notices(second)
    assert len(notices) == 1, notices
    assert notices[0]["source"] == DOC
    assert notices[0]["previousVersion"] == "h1"
    assert notices[0]["currentVersion"] == "h2"


def test_notice_compares_against_the_most_recent_recorded_read(sdk):
    """The prior version is the LAST recorded read, not the first.

    FAILS IF the notice compares the Source against an older read (``h1``)
    instead of the most recent one (``h2``).
    """
    sdk.create_source(DOC, "document", contentHash="h1")
    sdk.create_point("statement", "a", extractedFrom=DOC)
    sdk.create_source(DOC, "document", contentHash="h2")
    sdk.create_point("statement", "b", extractedFrom=DOC)  # notice h1 -> h2

    sdk.create_source(DOC, "document", contentHash="h3")
    third = sdk.create_point("statement", "c", extractedFrom=DOC)

    notices = _notices(third)
    assert len(notices) == 1, notices
    assert notices[0]["previousVersion"] == "h2"
    assert notices[0]["currentVersion"] == "h3"


# ── indicator 3 — an unchanged Source is silent ────────────────────────────


def test_unchanged_source_write_produces_no_notice(sdk):
    """Indicator 3: no notice at all when the Source has not changed.

    FAILS IF the key is present on an unchanged-source write (an empty-list
    notice is still a notice), or if a Source never read before is reported.
    """
    sdk.create_source(DOC, "document", contentHash="h1")
    sdk.create_point("statement", "one", extractedFrom=DOC)
    again = sdk.create_point("statement", "two", extractedFrom=DOC)
    assert NOTICE_KEY not in again

    # A never-before-read Source has no recorded read version -> silent.
    sdk.create_source(OTHER, "document", contentHash="hZ")
    uncited = sdk.create_point("statement", "three", extractedFrom=OTHER)
    assert NOTICE_KEY not in uncited


def test_source_without_a_hash_is_silent(sdk):
    """Honest-absent: a Source with no ``contentHash`` can never read "changed".

    FAILS IF a blank/absent hash on either side is coerced into a comparison
    (the false-current class #5256 guards against).
    """
    sdk.create_source(DOC, "document")  # no hash
    sdk.create_point("statement", "first", extractedFrom=DOC)
    again = sdk.create_point("statement", "second", extractedFrom=DOC)
    assert NOTICE_KEY not in again


# ── indicator 2 — the notice is advisory ───────────────────────────────────


def test_notice_never_blocks_or_mutates_the_write(sdk):
    """Indicator 2: the write lands, and the notice changes nothing durable.

    FAILS IF the notice (a) prevents the write, (b) is persisted onto the Point
    node (so a later read sees it), (c) changes the Source's stored version, or
    (d) changes the version the new Point records as its own read.
    """
    sdk.create_source(DOC, "document", contentHash="h1")
    sdk.create_point("statement", "first", extractedFrom=DOC)
    sdk.create_source(DOC, "document", contentHash="h2")

    before = sdk._get_proj().g.query(
        "MATCH (p:Point) RETURN count(p)").result_set[0][0]
    second = sdk.create_point("statement", "second", extractedFrom=DOC)
    after = sdk._get_proj().g.query(
        "MATCH (p:Point) RETURN count(p)").result_set[0][0]

    # (a) the write landed and is readable...
    assert sdk.get_point(second["id"])["content"] == "second"
    # ...as exactly ONE new Point (the notice wrote nothing — no extra node,
    # and no extra Source either: the edge MERGEs the existing Source).
    assert after == before + 1
    assert sdk._get_proj().g.query(
        "MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
    # (b) the notice is not stored on the node...
    assert NOTICE_KEY not in sdk.get_point(second["id"])
    assert _notices(second), "precondition: this write must produce a notice"
    # (c) the Source still holds its current version...
    assert _source_hash(sdk, DOC) == "h2"
    # (d) ...and the new Point records the version it was actually read from.
    assert _recorded_version(sdk, second["id"]) == "h2"


def test_notice_is_not_journaled_with_the_point(sdk, monkeypatch):
    """Indicator 2: the notice is transient — the journal snapshot is clean.

    FAILS IF the notice is attached to the snapshot handed to ``_emit_event``
    instead of to a copy: it would enter the ``PointAdded`` payload and risk a
    replayed point property (a live != replay divergence). A graph re-read
    (``get_point``) cannot catch that, so this pins the JOURNAL argument itself.
    """
    captured: list = []
    real_emit = sdk._emit_event

    def _spy(type_, payload=None, **kw):
        if type_ == "PointAdded":
            captured.append(kw.get("point"))
        return real_emit(type_, payload, **kw)

    monkeypatch.setattr(sdk, "_emit_event", _spy)

    sdk.create_source(DOC, "document", contentHash="h1")
    sdk.create_point("statement", "first", extractedFrom=DOC)
    sdk.create_source(DOC, "document", contentHash="h2")
    second = sdk.create_point("statement", "second", extractedFrom=DOC)

    assert _notices(second), "precondition: this write must produce a notice"
    assert captured, "no PointAdded snapshot was captured"
    assert NOTICE_KEY not in captured[-1]


def test_unchanged_source_with_inverted_createdat_is_silent(sdk):
    """Indicator 3, the ordering trap: the fire/silence decision must not
    depend on ``createdAt`` (a CALLER-owned logical date).

    A read of the CURRENT version recorded LATER but dated EARLIER still proves
    the current version has been read — so the Source has not changed. FAILS IF
    the decision picks its comparison row by ``createdAt`` alone: with ``h1``
    dated 2024 and ``h2`` dated 2015 while the Source holds ``h2``, a
    ``createdAt``-ordered choice reads ``h1`` and falsely reports a change.
    """
    sdk.create_source(DOC, "document", contentHash="h1")
    sdk.create_point("statement", "older-read", extractedFrom=DOC,
                     createdAt="2024-01-01T00:00:00+00:00")
    sdk.create_source(DOC, "document", contentHash="h2")
    sdk.create_point("statement", "newer-read", extractedFrom=DOC,
                     createdAt="2015-01-01T00:00:00+00:00")

    third = sdk.create_point("statement", "third", extractedFrom=DOC)
    assert NOTICE_KEY not in third


def test_revert_to_a_previously_read_version_is_silent(sdk):
    """A revert to a version that HAS been read is correctly silent.

    Pins the documented semantics: the decision is "the current content has been
    read", not "the current content equals the most recent read". A Source
    reverted to ``h1`` after ``h1`` was already read holds content the writer
    HAS seen, so there is no staleness to disclose.

    FAILS IF the decision is re-derived from a "most recent read" ordering
    instead of the order-independent recorded-set membership.
    """
    sdk.create_source(DOC, "document", contentHash="h1")
    sdk.create_point("statement", "read h1", extractedFrom=DOC)
    sdk.create_source(DOC, "document", contentHash="h2")
    second = sdk.create_point("statement", "read h2", extractedFrom=DOC)
    assert _notices(second), "precondition: h1 -> h2 is a change"

    # The Source reverts to the version already recorded on the first read.
    sdk.create_source(DOC, "document", contentHash="h1")
    third = sdk.create_point("statement", "after revert", extractedFrom=DOC)
    assert NOTICE_KEY not in third


def test_previous_version_label_is_best_effort(sdk):
    """``previousVersion`` is a best-effort label; the decision is exact.

    ``createdAt`` is caller-owned (ingest passes document frontmatter dates), so
    the LABEL may name an older-dated prior read. This test pins that documented
    behaviour so a future change to it is deliberate — and pins that the
    DECISION is unaffected: once ``h2`` is recorded, later writes are silent even
    though the greatest-``createdAt`` prior read is ``h1``.
    """
    sdk.create_source(DOC, "document", contentHash="h1")
    sdk.create_point("statement", "h1 read, dated later", extractedFrom=DOC,
                     createdAt="2024-01-01T00:00:00+00:00")
    sdk.create_source(DOC, "document", contentHash="h2")
    second = sdk.create_point("statement", "h2 read, dated earlier",
                              extractedFrom=DOC,
                              createdAt="2015-01-01T00:00:00+00:00")
    assert _notices(second)[0]["previousVersion"] == "h1"

    # h2 is recorded -> unchanged -> silent, regardless of the createdAt labels.
    sdk.create_point("statement", "still h2a", extractedFrom=DOC)
    third = sdk.create_point("statement", "still h2b", extractedFrom=DOC)
    assert NOTICE_KEY not in third


def test_capture_receipt_carries_the_notice(sdk, monkeypatch):
    """The notice reaches the CAPTURE writer, not only ``create_point``'s caller.

    Drives the real v2 capture commit with the extractor seam monkeypatched to a
    fixed payload (no LLM, no network). FAILS IF the capture receipt omits the
    notice — the per-point ``create_point`` result is otherwise discarded
    (the #4041 written-but-unread class).
    """
    import tortoise.extractor_v2 as ev2

    # The v2 point loop links each extracted point to the SESSION Source, so
    # that Source is the one whose change we stage.
    session_url = "session:s5516"
    sdk.create_source(session_url, "agentSession", contentHash="h1")
    sdk.create_point("statement", "a prior read of the session",
                     extractedFrom=session_url)
    sdk.create_source(session_url, "agentSession", contentHash="h2")

    content = "the session source changed since it was last read"
    pid = ev2._content_id("pt", content)
    payload = {
        "session_id": "s5516", "client_commit_id": "",
        "captured_at": "2026-10-07T00:00:00Z",
        "extractor": {"version": "t", "mode": "byok",
                      "calibration_version": "v2"},
        "summary": "", "story_arc": "", "provenance_refs": [],
        "sources": [], "entities": [],
        "points": [{
            "id": pid, "content": content, "pointKind": "statement",
            "reason": "NEW", "confidence": 0.5, "c_cal": 0.5,
            "about_entities": [], "source_ref": "session.md", "quote": "",
            "status": "draft", "search_keys": []}],
        "events": [], "operators": [], "supersessions": [],
        "telemetry": {"counts": {"kept": 1}},
    }

    def _run(_model, _conversation=None, *, session_id=None, **_kw):
        return {"payload": payload, "errors": [], "warnings": [],
                "stats": {}, "noops": [], "chain_notes": [],
                "link_before_create": [], "supersessions": [],
                "minted_kinds": [], "story_arc": "", "search": {},
                "error_census": {}}

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.setattr(ev2, "extract_session_v2", _run)

    conv = [{"role": "user", "content": "a session"},
            {"role": "assistant", "content": "noted"}]
    receipt = sdk.capture_session(conv, session_id="s5516")

    assert receipt.get("ok") is True, receipt
    notices = receipt.get(NOTICE_KEY) or []
    assert len(notices) == 1, receipt
    assert notices[0]["source"] == session_url
    assert notices[0]["previousVersion"] == "h1"
    assert notices[0]["currentVersion"] == "h2"


def test_capture_receipt_notice_helper_is_additive_only():
    """The shared receipt helper is silent unless a Source changed.

    Both capture lanes call this ONE function (the SDK and hosted receipts are
    byte-identical by contract), so this pins the condition in a single place
    instead of relying on a hand-mirrored ``if`` in each — the SDK capture test
    and this one together cover the receipt surface without a transport.

    FAILS IF the key is emitted for an empty/absent notice list (which would
    put a notice, or an always-present empty key, on every ordinary capture).
    """
    from tortoise.sdk import _attach_source_change_notices

    resp: dict = {"ok": True}
    _attach_source_change_notices(resp, {})
    _attach_source_change_notices(resp, {"source_change_notices": []})
    assert NOTICE_KEY not in resp

    _attach_source_change_notices(
        resp, {"source_change_notices": [{"source": DOC}]})
    assert resp[NOTICE_KEY] == [{"source": DOC}]


def test_a_failing_notice_computation_never_fails_the_write(
        sdk, monkeypatch):
    """Indicator 2, hardest form: the write survives a broken notice read.

    FAILS IF an exception from the notice computation propagates out of
    ``create_point`` (the notice must be unable to take a committed write down).
    """
    sdk.create_source(DOC, "document", contentHash="h1")
    sdk.create_point("statement", "first", extractedFrom=DOC)
    sdk.create_source(DOC, "document", contentHash="h2")

    import tortoise.projection.edges as edges

    def _boom(*_a, **_kw):
        raise RuntimeError("notice read exploded")

    monkeypatch.setattr(edges, "source_change_notices", _boom)

    second = sdk.create_point("statement", "second", extractedFrom=DOC)
    assert sdk.get_point(second["id"])["content"] == "second"
    assert _notices(second) == []
