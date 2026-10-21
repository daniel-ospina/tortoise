"""#5199 — the derivation anchor through the PRODUCTION mining path.

WHAT THIS SUITE IS FOR. The anchor itself landed (`projection/edges.py::
_DERIVATION_ANCHOR_SET`, owner-approved option A 2026-09-25): a DERIVATION
``references`` link records the source version it was read at, and
``search_engine.read_reference_currency_links`` compares that note with the
source's current ``contentHash``. Every existing test of that machinery
(`test_source_version_references_5199.py`,
`test_source_version_entity_currency_5199.py`) links through
``TortoiseSDK.link_source_to_entity`` against a Source the test built WITH a
``contentHash``.

The corpus miner does not. ``ConversationMiner._reify_entities`` completes the
provenance chain with ``link_source_to_entity(session_id, meeting_event,
"Event")``, and that Source does not exist yet: ``link_source_to_entity``
auto-creates it (#205) with ``contentHash=''`` — while the version of the
content actually mined is recorded on the session ``:Event``'s ``file_hash``
(written by ``ingest_corpus``). The anchor is stamped from
``s.contentHash``, so it is stamped from the empty stub, and the derived
meeting ``:Event`` is **permanently unversioned**: ``entity_currency`` answers
``unknown`` even before anything is revised, so a later revision can never
stale it. That is the gap #5199 names — *"a revised source can neither mark
them stale nor say which version they were read from"* — surviving the
landing of the anchor because the production writer had no version to give.

Every test names the input that makes it FAIL.

SCOPE, STATED SO IT IS NOT MISREAD. These tests are about the **Event**
derivation half, which the owner's ruling anchors. They also pin that the fix
does NOT reach the ``:Object`` half: an identity/mention link stays
property-free (option A), so no Object is anchored here and none may be.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.source_currency import entity_currency, point_currency, read_entity_links
from tortoise.mining import mine_corpus_with_sdk
from tortoise.sdk import TortoiseSDK

TRANSCRIPT = """# Weekly sync

Decision: we should move the launch to March.
Alice decided we should move the launch to March. Bob agreed.
The team confirmed the pricing number.
"""


def _tmp(name: str) -> str:
    """A per-test store path, in a directory removed when the run ends."""
    d = tempfile.mkdtemp(prefix="tortoise_test_")
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return os.path.join(d, name)


def _corpus_dir() -> str:
    d = tempfile.mkdtemp(prefix="tortoise_corpus_")
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d


def _sha(text: str) -> str:
    """The version `ingest_corpus` records on the session Event.

    Deliberately re-derived here (not read off the graph) so the tests compare
    the anchor against an INDEPENDENT computation of the version that was read:
    asserting the note equals the graph's own `file_hash` would pass for any
    self-consistent nonsense.
    """
    return hashlib.sha256(text.encode()).hexdigest()


def _mine(sdk: TortoiseSDK, directory: str) -> dict:
    return mine_corpus_with_sdk(sdk, directory, extract_entities=True)


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _meeting_event(proj) -> str:
    """The id of the meeting `:Event` the miner derives for the one session."""
    rows = proj.g.query(
        "MATCH (e:Event) WHERE e.eventKind = 'meeting' RETURN e.eventId"
    ).result_set
    assert len(rows) == 1, f"expected exactly one derived meeting Event; got {rows!r}"
    return rows[0][0]


def _link(proj, event_id: str):
    """`(source url, recorded note, source current hash)` for the Event's
    derivation link — the three operands the currency verdict is made of."""
    rows = proj.g.query(
        "MATCH (s:Source)-[r:references]->(e:Event {eventId:$eid}) "
        "RETURN s.url, r.sourceVersion, s.contentHash",
        params={"eid": event_id},
    ).result_set
    assert len(rows) == 1, (
        f"the derived Event must have exactly one derivation link; got {rows!r}")
    return rows[0]


# ── the write half: the mined Source carries the version it was read at ───

def test_mined_source_carries_the_version_the_transcript_was_read_at():
    """Input: one corpus file mined through the production path.

    FAILS IF the session `:Source` the derivation link hangs off has no
    ``contentHash`` — which is what the writer auto-created before this
    change, and what made the anchor (`s.contentHash` at link time) empty.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    directory = _corpus_dir()
    _write(os.path.join(directory, "sess1.md"), TRANSCRIPT)
    try:
        _mine(sdk, directory)
        proj = sdk._get_proj()
        source, _recorded, _current = _link(proj, _meeting_event(proj))
        rows = proj.g.query(
            "MATCH (s:Source {url:$u}) RETURN s.contentHash",
            params={"u": source},
        ).result_set
        assert rows == [[_sha(TRANSCRIPT)]], (
            "the mined Source must carry the version of the content that was "
            f"mined; got {rows!r}")
    finally:
        sdk.close()


def test_mined_meeting_event_records_the_version_read():
    """Input: one corpus file mined through the production path.

    FAILS IF the derived meeting Event's derivation link carries no
    ``sourceVersion`` — the anchor exists in code (`_DERIVATION_ANCHOR_SET`)
    but had nothing to stamp, so the Event was unversioned and
    ``entity_currency`` could only ever answer ``unknown``.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    directory = _corpus_dir()
    _write(os.path.join(directory, "sess1.md"), TRANSCRIPT)
    try:
        _mine(sdk, directory)
        proj = sdk._get_proj()
        _source, recorded, current = _link(proj, _meeting_event(proj))
        assert recorded == _sha(TRANSCRIPT), (
            "the derivation link must record the version the Event was read "
            f"from; got {recorded!r} (current source hash {current!r})")
        assert current == recorded, (
            "until the transcript is revised, the recorded and current "
            f"versions are the same content; got {recorded!r} vs {current!r}")
    finally:
        sdk.close()


def test_mined_meeting_event_reads_current_before_any_revision():
    """Input: a freshly mined session, nothing revised.

    FAILS IF the entity-addressed currency read cannot answer for a mined
    Event (``unknown``), which is the state that leaves a revision with nothing
    to be stale against.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    directory = _corpus_dir()
    _write(os.path.join(directory, "sess1.md"), TRANSCRIPT)
    try:
        _mine(sdk, directory)
        proj = sdk._get_proj()
        verdict = entity_currency(proj.g, _meeting_event(proj))
        assert verdict["status"] == "current", verdict
        assert verdict["links"][0]["recorded"] == _sha(TRANSCRIPT), verdict["links"]
    finally:
        sdk.close()


# ── the read half: a revised transcript stales it, and deletes nothing ────

def test_revised_transcript_stales_the_mined_event_without_deleting_it():
    """Input: mine a session, revise the file, mine again.

    FAILS IF a revised source leaves the derived Event reading ``current`` (the
    false-current §4.6 exists to prevent), or if a stale verdict deletes /
    supersedes the Event (``stale != wrong``), or if the anchor is silently
    refreshed so the drift disappears.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    directory = _corpus_dir()
    path = os.path.join(directory, "sess1.md")
    _write(path, TRANSCRIPT)
    try:
        _mine(sdk, directory)
        proj = sdk._get_proj()
        event_id = _meeting_event(proj)
        assert entity_currency(proj.g, event_id)["status"] == "current"

        revised = TRANSCRIPT.replace("March", "April")
        assert revised != TRANSCRIPT, "the premise: the content really changed"
        _write(path, revised)
        _mine(sdk, directory)

        # The premise: the source really moved.
        _source, recorded, current = _link(proj, event_id)
        assert current == _sha(revised), (
            f"the second mine must record the new version; got {current!r}")
        assert recorded == _sha(TRANSCRIPT), (
            "the note is written ON CREATE only — a re-link must not advance "
            f"the recorded version; got {recorded!r}")

        verdict = entity_currency(proj.g, event_id)
        assert verdict["status"] == "stale", verdict
        assert [lk["currency"] for lk in verdict["links"]] == ["stale"], verdict

        # §4.6: stale is not wrong — the node and its link both survive, and
        # the note still names the version the Event was actually built from.
        assert proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN count(e)",
            params={"eid": event_id},
        ).result_set == [[1]], "a stale verdict must not delete the derived Event"
        assert read_entity_links(proj.g, event_id), "the link must survive too"
    finally:
        sdk.close()


def test_second_mine_of_an_unchanged_transcript_keeps_it_current():
    """Input: mine the same unchanged content twice.

    FAILS IF re-mining an unchanged file manufactures staleness (the anchor or
    the source hash drifting without a content change) — a read that calls
    every re-index a revision is not a currency read.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    directory = _corpus_dir()
    _write(os.path.join(directory, "sess1.md"), TRANSCRIPT)
    try:
        _mine(sdk, directory)
        proj = sdk._get_proj()
        event_id = _meeting_event(proj)
        _mine(sdk, directory)
        verdict = entity_currency(proj.g, event_id)
        assert verdict["status"] == "current", verdict
    finally:
        sdk.close()


# ── the same defect one class up: the mined POINTS ───────────────────────

def test_mined_points_carry_the_version_they_were_read_at():
    """Input: a session mined through the production path, producing a Point.

    FAILS IF the mined Point's ``extractedFrom`` link carries no recorded
    version. The Point's link is written during extraction, i.e. BEFORE the
    Event link — so a version stamped only at the Event link leaves every
    mined Point reading ``unknown`` even though the source now holds a hash.
    The capture path records the version BEFORE extraction for exactly this
    reason (sdk.py #8501); the miner has to do the same.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    directory = _corpus_dir()
    _write(os.path.join(directory, "sess1.md"), TRANSCRIPT)
    try:
        _mine(sdk, directory)
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (:Point)-[r:extractedFrom]->(s:Source) "
            "RETURN r.sourceVersion, s.contentHash"
        ).result_set
        assert rows, "the premise: this transcript must produce at least one Point"
        for recorded, current in rows:
            assert recorded == _sha(TRANSCRIPT), (
                "a mined Point must record the version it was read from; "
                f"got {recorded!r} (source hash {current!r})")
            assert current == recorded, (
                f"the source holds the same content; got {current!r} vs {recorded!r}")
    finally:
        sdk.close()


def test_revised_transcript_stales_the_mined_points():
    """Input: mine a session, revise the file, mine again.

    FAILS IF a revised source leaves the mined Points reading ``current`` —
    the false-current §4.6 exists to prevent — or reads ``unknown`` because the
    recorded version was never written.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    directory = _corpus_dir()
    path = os.path.join(directory, "sess1.md")
    _write(path, TRANSCRIPT)
    try:
        _mine(sdk, directory)
        proj = sdk._get_proj()
        point_id = proj.g.query("MATCH (p:Point) RETURN p.id LIMIT 1").result_set[0][0]
        assert point_currency(proj.g, point_id)["status"] == "current"

        _write(path, TRANSCRIPT.replace("March", "April"))
        _mine(sdk, directory)

        verdict = point_currency(proj.g, point_id)
        assert verdict["status"] == "stale", verdict
    finally:
        sdk.close()


# ── the ruling this must NOT reach: Objects stay property-free ────────────

def test_the_fix_does_not_anchor_an_identity_object_link():
    """Input: a session whose derived entities include an Object.

    FAILS IF the version-stamping fix is generalised onto the ``:Object``
    half. Option A (2026-09-25) anchors DERIVATION links only: every in-repo
    ``Source -> Object`` writer is identity/mention, so an Object link carries
    no note and reads ``unknown`` — honestly, never ``current``.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    directory = _corpus_dir()
    _write(os.path.join(directory, "sess1.md"), TRANSCRIPT)
    try:
        _mine(sdk, directory)
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (:Source)-[r:references]->(:Object) RETURN keys(r)"
        ).result_set
        for (keys,) in rows:
            assert "sourceVersion" not in keys, (
                f"an identity/mention Object link must carry no anchor; got {keys!r}")
    finally:
        sdk.close()
