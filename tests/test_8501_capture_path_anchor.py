"""#8501 — the CAPTURE path anchors every create-path ``extractedFrom`` link.

The defect this pins: ``capture_session`` ran extraction (which creates the
Points and MERGEs their ``extractedFrom`` links) BEFORE
``_materialize_session_source`` (which is what SETS the session ``:Source``'s
``contentHash``). So at link time the Source was still the empty-hash stub,
``resolve_source_versions`` returned ``{}``, ``$v`` bound nothing, and
``_anchor_on_create`` wrote NULL — every Point of every capture landed
unanchored, and a revised transcript could never mark them stale. Measured on
the pre-fix tree: ``references`` 1/1 anchored, ``extractedFrom`` 0/1.

The fix moves the MINT + MATERIALIZE half of the capture above extraction (both
lanes: ``sdk.capture_session`` and ``hosted_api._capture_session_impl``), so the
ordinary link-time read returns the version and the anchor lands in the Point's
own journaled ``sourceVersionTransit`` snapshot — which is what makes it survive
a rebuild.

Every test names the mutation that makes it FAIL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._provider_env import pin_provider_routing_env
from tortoise.sdk import TortoiseSDK

CONV = [
    {"role": "user", "content": "I think the auth dead-end is the top issue. "
                                "We decided to ship serve --http first."},
    {"role": "assistant", "content": "Agreed. Evidence suggests the website "
                                     "config is the root cause."},
    {"role": "user", "content": "ok"},
]

#: The session ``:Source`` property set a capture produced BEFORE the fix. The
#: early materialization must not change the node the capture lands: it now
#: often CREATES the stub (rather than upgrading ``_link_source``'s), so it has
#: to reproduce that stub's create-time shape (`title=url`, `canonicalUrl`,
#: `urlAliases`, `is_episodic=true` for `session:` refs, ...) exactly.
_PRE_FIX_SOURCE_KEYS = {
    "_searchText", "canonicalUrl", "capturedAt", "contentHash", "eventId",
    "ingestedAt", "is_episodic", "sessionId", "sourceKind", "summary", "title",
    "topics", "updatedAt", "url", "urlAliases",
}


@pytest.fixture()
def prov(tmp_path, monkeypatch):
    """(sdk, events_dir) — one journal-backed embedded SDK, offline extractor."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    db = tmp_path / "t.db"
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(db), event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _keyless(monkeypatch) -> None:
    """Make the capture keyless — no LLM extraction runs (no network, no mock).

    The turns are still written by the mechanical loop under test; clearing the
    mock seam AND every provider key makes any extraction attempt fail loudly
    instead of quietly serving a mock.

    ⚠️ The provider set is DERIVED (``pin_provider_routing_env``), never a
    literal list — the #3695 / #5477 class. The hand-list this replaces omitted
    ``GEMINI_API_KEY`` (already red on a gemini-configured box) and, once #8133
    registered ``venice`` in ``ingest._PROVIDERS``, ``VENICE_API_KEY`` too — an
    ambient one built a real ``OpenAICompatModel`` against
    ``https://api.venice.ai/api/v1`` and issued a BILLED request inside tests
    whose docstring promises the keyless lane, while staying green in CI, which
    has no such key. The assertion makes the docstring true.
    """
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    pin_provider_routing_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from tortoise.sdk import _build_session_llm_extractor
    assert _build_session_llm_extractor() is None, "keys leaked into the test"


def _capture_links(sdk):
    """Every ``extractedFrom`` link of this capture, as
    ``[(point_id, source_url, recorded_version, source_content_hash)]``."""
    return sdk._get_proj().g.query(
        "MATCH (p:Point)-[r:extractedFrom]->(s:Source) "
        "RETURN p.id, s.url, r.sourceVersion, s.contentHash, p.sourceVersionTransit"
    ).result_set


def _transit(sdk, pid):
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) RETURN p.sourceVersionTransit",
        params={"id": pid}).result_set
    assert rows, f"no Point {pid} in the graph"
    return rows[0][0]


def test_capture_anchors_every_extractedfrom_link(prov):
    """FAILS BEFORE: every counted link had ``sourceVersion = NULL`` (0/1).

    FAILS IF the materialization moves back after extraction (the anchor binds
    no ``$v``), or if the link-time read stops happening at all.
    """
    sdk, _events = prov
    res = sdk.capture_session(CONV)
    assert res["ok"] is True and res["extracted"] >= 1

    links = _capture_links(sdk)
    assert links, "the capture produced no extractedFrom link — guard failed"
    unanchored = [row for row in links if not row[2]]
    assert not unanchored, (
        "every captured Point must record the version it was read from "
        f"(#8501); unanchored rows: {unanchored!r}")

    # The anchor IS the session Source's contentHash — not some other value.
    for pid, _url, recorded, current, _transit in links:
        assert current, f"the session Source has no contentHash: {pid}"
        assert recorded == current, (
            f"{pid} recorded {recorded!r} but was read from {current!r}")

    # ... and it rides the Point's OWN journaled snapshot, which is what the
    # replay uses (`_link_source` never re-reads the Source at rebuild time).
    for pid, _url, recorded, _current, transit in links:
        assert transit == [[f"session:{res['session_id']}", recorded]] or (
            transit and transit[0][1] == recorded), (
            f"{pid} carries no sourceVersionTransit pair for {recorded!r}: "
            f"{transit!r}")


def test_the_anchor_survives_a_full_rebuild(prov):
    """FAILS IF the anchor is a live-only write (a re-stamp after extraction
    would produce exactly that: an unjournalled second write whose value the
    Point's snapshot never saw, so ``rebuild_all`` drops it)."""
    sdk, events = prov
    res = sdk.capture_session(CONV)
    assert res["ok"] is True

    live = sorted((pid, recorded) for pid, _u, recorded, _c, _t in
                  _capture_links(sdk))
    assert live and all(recorded for _pid, recorded in live), (
        f"guard: the live anchor is already missing: {live!r}")

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = sorted((pid, recorded) for pid, _u, recorded, _c, _t in
                     _capture_links(sdk))
    assert rebuilt == live, (
        "the capture's version anchor did not survive a rebuild — it is not "
        f"carried by the journal: live={live!r} rebuilt={rebuilt!r}")


def test_capture_path_source_shape_is_unchanged_by_the_early_materialization(prov):
    """FAILS IF the materializer stops reproducing the stub it now often
    CREATES: the pre-fix capture landed a Source built by ``_link_source``'s
    stub mint (title=url, canonicalUrl, urlAliases, is_episodic for session
    refs), and materializing FIRST must not change one property of it."""
    sdk, _events = prov
    res = sdk.capture_session(CONV)
    assert res["ok"] is True

    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN keys(s)",
        params={"u": f"session:{res['session_id']}"}).result_set
    assert rows, "the session Source did not land"
    keys = set(rows[0][0])
    assert keys == _PRE_FIX_SOURCE_KEYS, (
        "the early materialization changed the session Source's shape — "
        f"missing={sorted(_PRE_FIX_SOURCE_KEYS - keys)} "
        f"extra={sorted(keys - _PRE_FIX_SOURCE_KEYS)}")


def test_a_recapture_neither_advances_nor_drops_the_anchor(prov):
    """``sourceVersion`` is ``ON CREATE`` only (#5199): re-capturing the same
    session must not re-stamp a Point that already carries a version, and the
    replay branch must leave the anchor standing.

    FAILS IF a re-link (or a second materialization) overwrites the recorded
    version, or if the replay path drops the link."""
    sdk, _events = prov
    first = sdk.capture_session(CONV)
    before = sorted((pid, recorded) for pid, _u, recorded, _c, _t in
                    _capture_links(sdk))
    assert before and all(recorded for _pid, recorded in before)

    second = sdk.capture_session(CONV, session_id=first["session_id"])
    assert second["session_id"] == first["session_id"]

    after = sorted((pid, recorded) for pid, _u, recorded, _c, _t in
                   _capture_links(sdk))
    assert after == before, (
        f"a re-capture changed the recorded read version: {before!r} → {after!r}")


def test_the_m2_lane_anchors_too(prov, monkeypatch):
    """The other extraction lane writes its Points through ``EventAPI``, but the
    anchor is resolved by the same link-time read — so the ordering fix must
    cover it as well.

    FAILS IF the materialization is moved inside one extractor lane instead of
    before the extraction dispatch (the m2 lane would then stay unanchored)."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    sdk, _events = prov
    res = sdk.capture_session(CONV)
    assert res["ok"] is True and res["extracted"] >= 1

    links = _capture_links(sdk)
    assert links
    unanchored = [row for row in links if not row[2]]
    assert not unanchored, (
        f"the m2 lane's Points are unanchored (#8501): {unanchored!r}")


def test_the_point_added_record_carries_the_anchor(prov):
    """The journal payload is the snapshot the replay folds: it must carry the
    ``sourceVersionTransit`` pair, not just the edge (which pass 2 re-derives).

    FAILS IF the anchor is stamped onto the edge outside ``create_point``'s
    create map — i.e. if a fix "repairs" the edge after the write."""
    sdk, events = prov
    res = sdk.capture_session(CONV)
    assert res["ok"] is True
    ref = f"session:{res['session_id']}"

    payloads = []
    for line in (events / "events.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("type") == "PointAdded":
            payloads.append(ev.get("point") or {})
    assert payloads, "no PointAdded record — the capture journaled nothing"

    carried = [p for p in payloads if p.get("sourceVersionTransit")]
    assert carried, (
        "no PointAdded payload carries sourceVersionTransit — the anchor is "
        "not in the journaled snapshot, so it cannot survive a rebuild")
    for p in carried:
        assert [pair[0] for pair in p["sourceVersionTransit"]] == [ref], (
            f"{p.get('id')} carries the wrong ref key: "
            f"{p['sourceVersionTransit']!r}")


def test_a_failed_mint_reports_one_warning_not_two(prov, monkeypatch):
    """The mint's failure shapes are distinct: a RAISE and a create that
    returns no id. Splitting them across two blocks (phase 1 mints, the stamp
    block below consumes ``event_id``) must not make one failure report both.

    FAILS IF phase 2's "returned no id/eventId" arm is not gated on the mint
    having SUCCEEDED — the receipt would then claim a shape that did not
    happen (and the extraction warnings would double-count one failure)."""
    sdk, _events = prov

    def _boom(*_a, **_kw):
        raise RuntimeError("mint down")

    monkeypatch.setattr(sdk, "create_event", _boom)
    res = sdk.capture_session(CONV)
    assert res["ok"] is True, "a mint failure must never fail the capture"

    mint_warnings = [w for w in res["warnings"]
                     if "sessionCaptured Event write" in w]
    assert len(mint_warnings) == 1, (
        f"one mint failure reported {len(mint_warnings)} warnings: "
        f"{mint_warnings!r}")
    assert "returned no id/eventId" not in mint_warnings[0], (
        f"the receipt names a shape that did not happen: {mint_warnings[0]!r}")


def test_the_stub_the_early_materialization_mints_survives_a_rebuild(prov):
    """The fix makes `_materialize_session_source` the writer that MINTS the
    session stub (it now runs before any Point exists), through
    `_mint_source_stub`. That mint's fields are the ones a replay reconstructs
    from `SourceCreated` — so they must agree live vs rebuilt.

    FAILS IF the stub mint's create-time fields (`title=url`, `canonicalUrl`,
    `urlAliases`, `is_episodic`, `ingestedAt`) are live-only."""
    sdk, events = prov
    res = sdk.capture_session(CONV)
    assert res["ok"] is True
    url = f"session:{res['session_id']}"

    query = ("MATCH (s:Source {url:$u}) RETURN s.title, s.canonicalUrl, "
             "s.urlAliases, s.is_episodic, s.ingestedAt")

    def _fields():
        rows = sdk._get_proj().g.query(query, params={"u": url}).result_set
        assert rows, f"no session Source {url}"
        return rows[0]

    live = _fields()
    assert live[0] == url, f"the stub's title=url default is gone: {live!r}"
    assert live[3] is True, f"the session stub is not episodic: {live!r}"

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _fields() == live, (
        f"the early-minted session stub does not replay: live={live!r} "
        f"rebuilt={_fields()!r}")


def test_a_keyless_capture_stamps_its_stub_with_the_capture_instant(prov, monkeypatch):
    """A capture that mints NO Point never reaches `_link_source`, so the early
    stub mint is the ONLY writer of the Source's `ingestedAt` — and its
    `SourceCreated` record carries the capture instant.

    FAILS IF the mint reads a fresh clock (`_now_iso()`) instead of the
    capture's own `now`: the live node would then carry a later stamp than the
    replay folds in — a live≠replay divergence on a path that previously had
    none."""
    _keyless(monkeypatch)
    sdk, _events = prov

    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    assert res["extraction_mode"] == "no-provider", res["extraction_mode"]
    url = f"session:{res['session_id']}"

    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.ingestedAt, s.capturedAt",
        params={"u": url}).result_set
    assert rows, "a keyless capture must still materialize its Source"
    ingested_at, captured_at = rows[0]
    assert ingested_at == captured_at, (
        "the stub's ingestedAt must be the capture's own instant, not a later "
        f"clock read: ingestedAt={ingested_at!r} capturedAt={captured_at!r}")


def test_the_extracting_lane_stamps_its_stub_with_the_capture_instant(prov):
    """#8501 corrected the ``ingestedAt`` note inside
    ``_materialize_session_source``: on the capture path the field is now the
    CAPTURE's own instant, not a later clock read. The lane where the old
    premise lived is THIS one — a capture that DOES mint Points, whose stub
    used to be minted by ``_link_source`` during extraction, so ``ingestedAt``
    was that writer's ``_now_iso()`` while ``capturedAt`` (and the
    ``SourceCreated`` record) carried the capture instant. The rebuild pin
    (``test_the_stub_the_early_materialization_mints_survives_a_rebuild``)
    compares live vs rebuilt but cannot say WHICH instant either holds; this
    one pins the value, which is what the corrected note claims.

    FAILS ON THE PRE-FIX CODE: the two stamps then come from two different
    clock reads (the capture's and the extraction's), so they are not equal.
    FAILS IF the early mint stops carrying ``now=`` (a fresh ``_now_iso()``
    stamp again) or if the materialization moves back below extraction."""
    sdk, _events = prov
    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    # PRE-CONDITION: this is the lane that extracts, i.e. the one where the
    # stub used to be minted by `_link_source` after the capture began.
    assert res["extracted"] >= 1, (
        f"guard: this test is about the EXTRACTING lane, got {res['extracted']}"
    )

    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.capturedAt, s.ingestedAt",
        params={"u": f"session:{res['session_id']}"}).result_set
    assert rows and rows[0][0] and rows[0][1], f"no stamps on the Source: {rows!r}"
    captured_at, ingested_at = rows[0]
    assert ingested_at == captured_at, (
        "the session Source's ingest instant is not the capture's own — the "
        "stub was minted from a later clock read: "
        f"capturedAt={captured_at!r} ingestedAt={ingested_at!r}")


def test_a_keyless_capture_lands_the_full_stub_shape(prov, monkeypatch):
    """A capture that extracts NOTHING never reaches ``_link_source``, so before
    #8501 its session ``:Source`` was created by ``_materialize_session_source``'s
    bare ``MERGE ... SET`` and carried ONLY those SET properties. The early mint
    now creates it, so the keyless node gains the stub's create-time shape —
    ``title``, ``canonicalUrl``, ``urlAliases`` and ``is_episodic``.

    That is the better shape (it is what the extracting lane has always had, and
    ``is_episodic`` is the #1486 discriminator), but it is a REAL behaviour
    change on a lane, and the shape pin above only exercises the lane that
    extracts — where the pre-fix stub came from ``_link_source``, i.e. from this
    same mint. So this test is the one that can see the difference.

    FAILS ON THE PRE-FIX CODE (measured: dropping the early ``_mint_source_stub``
    call reds it with exactly those four keys missing), and FAILS IF the keyless
    lane starts minting a SECOND ``:Source`` instead of the node its capture
    materializes."""
    _keyless(monkeypatch)
    sdk, _events = prov

    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    assert res["extraction_mode"] == "no-provider", res["extraction_mode"]
    url = f"session:{res['session_id']}"

    # PRE-CONDITION: this lane really is the keyless one — no Point was created,
    # so nothing but the early materialization can have shaped this node.
    points = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u})<-[:extractedFrom]-(p:Point) "
        "RETURN count(p)", params={"u": url}).result_set
    assert points and points[0][0] == 0, (
        "precondition: a no-provider capture must create no Point, else this "
        f"test is not exercising the keyless lane (found {points!r})")

    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN keys(s)", params={"u": url}).result_set
    assert rows, "a keyless capture must still materialize its Source"
    assert len(rows) == 1, (
        "a keyless capture minted more than one :Source — the early mint and "
        "the materializer are addressing different nodes (the "
        "`resolve_source_key` contract, #8501 review P3)")
    keys = set(rows[0][0])
    assert keys == _PRE_FIX_SOURCE_KEYS, (
        "the keyless capture's Source shape diverged from the stub shape the "
        f"extracting lane lands — missing={sorted(_PRE_FIX_SOURCE_KEYS - keys)} "
        f"extra={sorted(keys - _PRE_FIX_SOURCE_KEYS)}. Before #8501 this lane "
        "carried no title/canonicalUrl/urlAliases/is_episodic at all; the "
        "change is intended, so this pin (not silence) is what keeps it")
