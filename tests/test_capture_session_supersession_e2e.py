"""#2164 Task 6 — two-session contradiction e2e (indicator 2) + A→B→C chain pin.

End-to-end proof through the REAL conversational capture pipeline
(``sdk.capture_session`` → ``_extract_session_v2`` → the 5-stage v2
extractor, offline via a ``_V2SessionMock`` subclass):

  s1 "approach A chosen"  → Object approach-A created live
  s2 "we replaced A with B" → extractor EMITS entity approach-B with a
     ``supersedes: "approach-A"`` ref → S3 (faked real-mode search) resolves
     approach-A → a supersession record forms → the capture write path
     (``apply_supersessions``) folds Object approach-A to
     status='superseded' supersededBy='approach-B'; approach-B stays live;
     the s2 statement point ("approach B replaces approach A") is
     aboutObject-linked to approach-B (indicator 2's linking point).

  Chain pin: s3 "approach C replaces B" → approach-B folds to
     supersededBy='approach-C' while approach-A keeps supersededBy='approach-B'
     (nothing resurrects).

  No double-fold: re-running the s2-style capture forms NO new record (S3
  returns live items only — the terminal-exclusion contract #1391 — so the
  supersede ref no longer resolves) and the object state is byte-unchanged.

  Observed behavior note (flagged for T9): E7's deterministic paraphrase
  fold treats the s3 claim "approach C replaces approach B" as a
  paraphrase of the s2 claim "approach B replaces approach A" (0.75 token
  overlap — the shared "X replaces Y" frame) and mints no new statement
  point for the B→C step. The chain's durable truth still lands via the
  ObjectSuperseded folds; only the per-step replacement CLAIM point is
  consolidated.

This is the RECORD-FORMATION test the payload-seam Task 3/4 tests cannot
cover: the supersessions here are DERIVED by extractor_v2 from the mock's
S2 embed JSON (``_supersession_records`` against the S3 search results),
not hand-placed on the payload. The seam is the ``_V2SessionMock`` subclass
(monkeypatched onto ``tortoise.sdk`` — the tests/test_pack_manifest_store_
extraction.py:130 precedent) plus a ``search_graph`` fake (the
tests/test_lme_ingest_v2_supersession.py:87 pattern) so S3 resolves against
the live graph like a real backend.

Docker-lane file: requires TORTOISE_DB_URI (S3 real-mode + the SDK redirect;
the embedded carve-out list does not include this stem).
"""

from __future__ import annotations

import json

import pytest

from tortoise.sdk import TortoiseSDK

# ── The conversations ─────────────────────────────────────────────────────

CONV_S1 = [
    {"role": "user", "content": "we chose approach A for the rollout"},
    {"role": "assistant", "content": "approach A it is"},
]

CONV_S2 = [
    {"role": "user", "content": "we replaced approach A with approach B"},
    {"role": "assistant", "content": "agreed — B supersedes A"},
]

CONV_S3 = [
    {"role": "user", "content": "approach C replaces approach B now"},
    {"role": "assistant", "content": "C is the approach"},
]

#: Stable kind for the strategy objects (a real master-list object kind —
#: no minted-kind repair in execute_embed).
KIND = "core:strategy"


def _story_for(transcript: str) -> str:
    """S1 stand-in: deterministic narrative per conversation content."""
    if "replaced approach A with approach B" in transcript:
        return "The team replaced approach A with approach B."
    if "approach C replaces approach B" in transcript:
        return "The team replaced approach B with approach C."
    return "The team chose approach A for the rollout."


def _embed_for(story: str) -> dict:
    """S2 stand-in: the embed-list JSON the GRAPH MAPPER would emit for the
    compiled story. Session 1 emits approach-A (created); session 2 emits
    approach-B with ``supersedes: "approach-A"`` (never re-emits the old
    entity — the supersede discipline); session 3 emits approach-C with
    ``supersedes: "approach-B"``. Each session also emits ONE statement
    point capturing the state/change wired to the about-entities."""
    if "replaced approach A with approach B" in story:
        return {
            "entities": [
                {
                    "name": "approach-B",
                    "kind": KIND,
                    "lifecycle": "created",
                    "supersedes": "approach-A",
                    "note": None,
                }
            ],
            "events": [],
            "points": [
                {
                    "content": "approach B replaces approach A",
                    "pointKind": "statement",
                    "about_entities": ["approach-B", "approach-A"],
                }
            ],
            "operators": [],
            "chain_notes": [],
            "link_before_create": [],
        }
    if "replaced approach B with approach C" in story:
        return {
            "entities": [
                {
                    "name": "approach-C",
                    "kind": KIND,
                    "lifecycle": "created",
                    "supersedes": "approach-B",
                    "note": None,
                }
            ],
            "events": [],
            "points": [
                {
                    "content": "approach C replaces approach B",
                    "pointKind": "statement",
                    "about_entities": ["approach-C", "approach-B"],
                }
            ],
            "operators": [],
            "chain_notes": [],
            "link_before_create": [],
        }
    return {
        "entities": [
            {
                "name": "approach-A",
                "kind": KIND,
                "lifecycle": "created",
                "supersedes": None,
                "note": None,
            }
        ],
        "events": [],
        "points": [
            {
                "content": "the team chose approach A for the rollout",
                "pointKind": "statement",
                "about_entities": ["approach-A"],
            }
        ],
        "operators": [],
        "chain_notes": [],
        "link_before_create": [],
    }


def _mock_factory():
    """Build a fresh ``_V2SessionMock`` subclass instance. Imported lazily so
    the module import never touches the sdk module before conftest arms the
    test-mode redirect."""
    import tortoise.sdk as sdk_mod

    class _SupersessionConversationMock(sdk_mod._V2SessionMock):
        """Offline v2 mock dispatching on conversation content (per-capture
        instance — extract_session_v2 holds one model per session). S1 keys
        the story off the transcript; the S2 (GRAPH MAPPER) embed list keys
        off the compiled story; S4 (GAP REVIEWER) reports no gaps so the S2
        output stands; the D3 ENTITY RESOLUTION pass never guesses aliases
        (approach names are distinct entities)."""

        def __init__(self) -> None:
            super().__init__()
            self._story = ""

        def complete(self, *, system: str, user: str, max_tokens: int | None = None) -> str:
            if "STORY SUMMARIZER" in system:
                self._story = _story_for(user)
                return self._story
            if "GAP REVIEWER" in system:
                # no gaps — S2 output stands (kept by extract_session_v2)
                return json.dumps({"entities": [], "events": [], "points": [], "operators": []})
            if "ENTITY RESOLUTION" in system:
                # never guess an alias: approach-A/B/C are distinct entities
                return json.dumps({"resolutions": []})
            if "GRAPH MAPPER" in system:
                return json.dumps(_embed_for(self._story))
            return super().complete(system=system, user=user, max_tokens=max_tokens)

    return _SupersessionConversationMock


def _fake_search(sdk, embed_list, story, **kw):
    """S3 stand-in — simulates a real backend against the LIVE graph (the
    test_lme_ingest_v2_supersession.py pattern): live statement points +
    live Objects ride the S3 row shape {id, name, kind}; terminal Objects
    (superseded/retracted/archived) are excluded (#1391 terminal-exclusion
    contract), so a supersede ref pointing at an already-folded Object
    stops resolving on re-runs (record formation dies — the no-double-fold
    guard at the SOURCE)."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (p:Point {pointKind:'statement'}) "
        "WHERE (p.status IS NULL OR NOT (p.status IN $terminal)) "
        "RETURN p.id, p.content LIMIT 25",
        params={"terminal": ["retracted", "superseded", "archived", "outdated"]},
    ).result_set
    orows = proj.g.query(
        "MATCH (o:Object) "
        "WHERE (o.status IS NULL OR NOT (o.status IN $terminal)) "
        "RETURN o.id, o.name, o.objectKind LIMIT 25",
        params={"terminal": ["retracted", "superseded", "archived"]},
    ).result_set
    return {
        "mode": "real",
        "degraded": False,
        "reason": None,
        "entities": [{"id": r[0], "name": r[1], "kind": r[2]} for r in orows],
        "events": [],
        "points": [{"id": r[0], "content": r[1], "kind": "statement"} for r in rows],
        "queries_run": 1,
    }


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """Install the offline session-extractor seam (same as
    test_capture_session.py): capture_session's provider gate passes and the
    _V2SessionMock (subclass) runs with zero network."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)


@pytest.fixture()
def sdk(tmp_path, monkeypatch):
    import tortoise.extractor_v2 as ev2
    import tortoise.sdk as sdk_mod

    client = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    # the two seams: the conversation-aware mock + the real-mode S3 fake
    monkeypatch.setattr(sdk_mod, "_V2SessionMock", _mock_factory())
    monkeypatch.setattr(ev2, "search_graph", _fake_search)
    return client


def _object(proj, name: str) -> tuple | None:
    rows = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id, o.status, o.supersededBy",
        params={"n": name},
    ).result_set
    return rows[0] if rows else None


def test_two_session_contradiction_supersedes_object(sdk):
    """Indicator 2 e2e: capture s1 (approach A chosen) then s2 (we replaced
    A with B) through the real pipeline. The extractor DERIVES the entity
    supersession record from the s2 embed's ``supersedes`` ref (resolved
    against the faked S3) — Object approach-A must land
    status='superseded' supersededBy='approach-B', approach-B stays live,
    and the s2 statement point is aboutObject-linked to approach-B."""
    res1 = sdk.capture_session(CONV_S1, session_id="s1")
    assert res1["ok"] is True, res1
    assert res1["errors"] == [], res1

    proj = sdk._get_proj()
    a1 = _object(proj, "approach-A")
    assert a1 is not None, "s1 must create Object approach-A"
    assert (a1[1] or "live") == "live", a1

    res2 = sdk.capture_session(CONV_S2, session_id="s2")
    assert res2["ok"] is True, res2
    assert res2["errors"] == [], res2

    # Object approach-A folded to superseded by approach-B
    a = _object(proj, "approach-A")
    assert a is not None
    assert a[1] == "superseded", f"approach-A never folded: {a!r}"
    assert a[2] == "approach-B", f"wrong successor: {a!r}"

    # successor approach-B created and live
    b = _object(proj, "approach-B")
    assert b is not None, "successor approach-B missing from the graph"
    assert (b[1] or "live") == "live", f"successor must stay live: {b!r}"

    # indicator 2's linking point: the s2 statement about the replacement
    # is aboutObject-linked to the successor approach-B
    rows = proj.g.query(
        "MATCH (p:Point {content:'approach B replaces approach A'})"
        "-[:aboutObject]->(o:Object {name:'approach-B'}) "
        "RETURN count(p)",
    ).result_set
    assert rows and rows[0][0] == 1, (
        "s2 statement point must be aboutObject-linked to approach-B (indicator 2 linking point)"
    )
    # ... and to the superseded entity as well (both wired)
    rows2 = proj.g.query(
        "MATCH (p:Point {content:'approach B replaces approach A'})"
        "-[:aboutObject]->(o:Object {name:'approach-A'}) "
        "RETURN count(p)",
    ).result_set
    assert rows2 and rows2[0][0] == 1, rows2


def test_chain_pin_and_no_double_fold(sdk):
    """A→B→C chain pin + fold idempotence. s3 supersedes B with C:
    approach-B flips to supersededBy='approach-C', approach-A KEEPS
    supersededBy='approach-B' (nothing resurrects). Re-running the s2-style
    capture (fresh session, same conversation) forms no new record (the
    supersede ref no longer resolves — S3 returns live items only) and the
    object state is byte-unchanged: no double-fold, no successor clobber."""
    assert sdk.capture_session(CONV_S1, session_id="c1")["ok"] is True
    assert sdk.capture_session(CONV_S2, session_id="c2")["ok"] is True
    assert sdk.capture_session(CONV_S3, session_id="c3")["ok"] is True

    proj = sdk._get_proj()

    # chain: A superseded by B, B superseded by C, C live
    a = _object(proj, "approach-A")
    assert a and a[1] == "superseded" and a[2] == "approach-B", a
    b = _object(proj, "approach-B")
    assert b and b[1] == "superseded" and b[2] == "approach-C", b
    c = _object(proj, "approach-C")
    assert c is not None and (c[1] or "live") == "live", c

    # NOTE (observed, documented — not asserted): s3's own replacement claim
    # ("approach C replaces approach B") is folded by E7 as a PARAPHRASE of
    # the s2 claim (0.75 token overlap — same "X replaces Y" shape) so no
    # new statement point rides s3; the chain truth lives in the Object
    # folds above. The SURVIVING replacement claim must keep its link to the
    # now-superseded step (approach-B) — the fold never rewires or drops the
    # prior claim's aboutObject edge.
    rows = proj.g.query(
        "MATCH (p:Point {content:'approach B replaces approach A'})"
        "-[:aboutObject]->(o:Object {name:'approach-B'}) RETURN count(p)",
    ).result_set
    assert rows and rows[0][0] == 1, "surviving replacement claim must stay linked to approach-B"

    # ── no double-fold: re-run the s2-style capture on a fresh session ──
    res = sdk.capture_session(CONV_S2, session_id="c2-rerun")
    assert res["ok"] is True, res

    a_after = _object(proj, "approach-A")
    b_after = _object(proj, "approach-B")
    assert a_after == a, f"approach-A mutated by the re-run: {a_after!r}"
    assert b_after == b, f"approach-B mutated by the re-run: {b_after!r}"
