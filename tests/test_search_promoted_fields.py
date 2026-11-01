"""SDK-level tests for #1353 — bounded decoration + promoted fields in tortoise_fts_query.

Uses FULL-SCAN mode (query=None, kind=...) deliberately: in embedded
FalkorDBLite, text queries degrade to the TF-IDF fallback which returns raw
undecorated points (pre-existing behavior, unchanged by #1353). Full-scan
runs the real decoration path (steps 4-9), so it exercises the bounded
relationships + promoted epistemic state deterministically.
"""
from __future__ import annotations

import os
import sys
import tempfile  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK

TEST_GRAPH = "tortoise_test_1353_search_promoted"


@pytest.fixture
def sdk(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "search.db"), namespace=TEST_GRAPH)
    yield sdk
    try:
        sdk.test_guard()
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


def _scan(sdk, kind="statement", limit=50, include_terminal=False):
    return sdk.tortoise_fts_query(query=None, kind=kind, entity_type="point",
                                  limit=limit, include_terminal=include_terminal)


def test_full_scan_decorated_bounded(sdk):
    """Full-scan results carry bounded relationships (legacy keys, no content) + status."""
    a = sdk.create_point("statement", "alpha claim target")
    peers = [sdk.create_point("statement", f"peer {i} claim") for i in range(3)]
    sdk.create_operator("IMPL", a["id"], [p["id"] for p in peers])

    results = _scan(sdk)
    hit = next(r for r in results if r["id"] == a["id"])
    rels = hit["relationships"]
    peer_entries = [e for e in rels if "peer" in e]
    assert 0 < len(peer_entries) <= 10, "bounded per-point cap"
    assert all({"predicate", "mechanism", "operator_id", "related_id", "direction"} <= set(e) for e in peer_entries)
    assert all("related_content" not in e for e in peer_entries), "no content in list view"
    # family_size discloses the true operator family (a + 3 peers = 4 endpoints)
    assert any(e.get("family_size", 0) == 4 for e in peer_entries), "family_size disclosed"
    # status promoted
    assert "status" in hit, "status promoted on every point result"


def test_full_scan_promoted_superseded_and_subject(sdk):
    """Promoted fields: superseded_by (CORRECTS) + subject (own aboutSubject)."""
    old = sdk.create_point("statement", "obsolete claim")
    new = sdk.create_point("statement", "replacement claim")
    sdk.supersede_point(old["id"], new["id"])
    subj = sdk.create_subject("Epistemic Team", subjectKind="team")
    sdk._get_proj().create_about_edge(new["id"], subj["id"], "aboutSubject")

    results = _scan(sdk, include_terminal=True)
    old_hit = next(r for r in results if r["id"] == old["id"])
    new_hit = next(r for r in results if r["id"] == new["id"])
    assert old_hit["status"] == "superseded"
    assert old_hit["superseded_by"]["id"] == new["id"]
    assert "replacement" in old_hit["superseded_by"]["content_snippet"]
    assert any(s["id"] == old["id"] for s in new_hit["supersedes"])
    assert new_hit["subject"]["name"] == "Epistemic Team"
    # CORRECTS structure surfaces in the relationships list too
    assert any(e["mechanism"] == "CORRECTS" for e in old_hit["relationships"])


def test_retrieval_ranking_unchanged(sdk, monkeypatch):
    """Guardrail D1: decoration never reorders — identical result-id sequence."""
    pts = [sdk.create_point("statement", f"claim {i}") for i in range(5)]
    sdk.create_operator("IMPL", pts[0]["id"], [p["id"] for p in pts[1:]])

    baseline = _scan(sdk)
    baseline_ids = [r["id"] for r in baseline]

    import tortoise.search_engine as se
    monkeypatch.setattr(se, "get_relationships_bounded", lambda *a, **k: {})
    monkeypatch.setattr(se, "fetch_point_epistemic_state", lambda *a, **k: {})

    stripped = _scan(sdk)
    assert [r["id"] for r in stripped] == baseline_ids, "decoration must not reorder results"


def test_relationships_empty_when_no_operators(sdk):
    p = sdk.create_point("statement", "lonely claim")
    results = _scan(sdk)
    hit = next(r for r in results if r["id"] == p["id"])
    assert hit["relationships"] == []
    assert "status" in hit
    # #6971: empty BECAUSE the point is sparse — and that is said by the
    # ABSENCE of the failure marker, not by an empty list alone.
    assert "relationships_unavailable" not in hit


def test_failed_decoration_is_labelled_on_the_wire(sdk, monkeypatch):
    """#6971 — a decoration query that RAISED must not read as 'no edges'.

    The all-empty seed used to be returned verbatim from the ``except``, so
    ``relationships: []`` meant both "this point is genuinely sparse" and "the
    read failed", and only a ``logger.warning`` (below the API surface) said
    which. The batch now carries ``relationships_unavailable``, so the two
    states are distinguishable on the wire.
    """
    import tortoise.search_engine as se

    p = sdk.create_point("statement", "claim whose decoration read failed")

    def failing(graph, point_ids, **kw):
        rels = se.RelationshipDecoration({pid: [] for pid in point_ids})
        rels.decoration_error = "RuntimeError: boom"
        return rels

    monkeypatch.setattr(se, "get_relationships_bounded", failing)
    results = _scan(sdk)
    hit = next(r for r in results if r["id"] == p["id"])
    assert hit["relationships"] == []
    assert hit["relationships_unavailable"].startswith(
        se.RELATIONSHIPS_UNAVAILABLE), hit["relationships_unavailable"]
    assert "RuntimeError: boom" in hit["relationships_unavailable"], (
        "the marker must carry the actual failure, not just a generic label"
    )


def test_failed_decoration_does_not_break_retrieval(sdk, monkeypatch):
    """D1: decoration is default-on and must never turn a search into a failure.

    The fix labels the failure rather than raising it, so the hits still come
    back — with the same order the healthy decoration produced.
    """
    import tortoise.search_engine as se

    pts = [sdk.create_point("statement", f"claim {i}") for i in range(5)]
    healthy = [r["id"] for r in _scan(sdk)]

    def failing(graph, point_ids, **kw):
        rels = se.RelationshipDecoration({pid: [] for pid in point_ids})
        rels.decoration_error = "RuntimeError: boom"
        return rels

    monkeypatch.setattr(se, "get_relationships_bounded", failing)
    degraded = _scan(sdk)
    assert [r["id"] for r in degraded] == healthy, (
        "labelling the failure must not change which hits are returned"
    )
    assert {r["id"] for r in degraded} == {p["id"] for p in pts}
    assert all("relationships_unavailable" in r for r in degraded)


# ── Task 4: expand_relationships (SDK + MCP + registry) ─────────────────

def test_expand_relationships_full_content(sdk):
    """The expand side returns the FULL unbounded payload incl. related_content."""
    a = sdk.create_point("statement", "alpha claim")
    b = sdk.create_point("statement", "beta claim with unique content payload")
    sdk.create_operator("IMPL", a["id"], [b["id"]])

    expanded = sdk.expand_relationships(a["id"])
    assert len(expanded) == 1
    assert "related_content" in expanded[0]
    assert "beta claim" in expanded[0]["related_content"]
    # #689/#898 posture: full-fidelity view is not blind to terminal state
    assert "related_status" in expanded[0]


def test_expand_relationships_missing_point(sdk):
    assert sdk.expand_relationships("does-not-exist") == []


def test_expand_mcp_tool(sdk, monkeypatch):
    """MCP surface routes to the SDK method and returns the full payload (isolated DB)."""
    from tortoise.mcp_auth import _transport_mode  # noqa: I001
    from tortoise.mcp_server import tortoise_expand_relationships
    from tortoise import mcp_server as mcp_mod
    assert callable(tortoise_expand_relationships)
    # repo convention: swap _get_org_sdk for the isolated fixture SDK
    monkeypatch.setattr(mcp_mod, "_get_org_sdk", lambda: sdk)
    token = _transport_mode.set("stdio")
    try:
        a = sdk.create_point("statement", "alpha claim")
        b = sdk.create_point("statement", "beta claim with unique content payload")
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        out = tortoise_expand_relationships(a["id"])
        assert isinstance(out, list), out
        assert any("related_content" in e and "beta claim" in e["related_content"] for e in out)
        # related_status annotated on the expand payload
        assert any(e.get("related_status") is not None for e in out)
    finally:
        _transport_mode.reset(token)


def test_expand_tool_registered(sdk):
    from tortoise.tool_registry import TOOL_REGISTRY
    names = {t.name for t in TOOL_REGISTRY}
    assert "tortoise_expand_relationships" in names
    entry = next(t for t in TOOL_REGISTRY if t.name == "tortoise_expand_relationships")
    assert entry.sdk_method == "expand_relationships"
    assert entry.group == "memory"


# ── E6 (#1538) Task 4 — window fields + marker golden shapes ─────────────

def test_promoted_window_fields_and_default_live_lock(sdk):
    """T4: superseded hit (include_terminal) carries valid_from/valid_to/
    expired_at + status; DEFAULT search excludes it (D5 regression lock)."""
    old = sdk.create_point("statement", "gym at 6pm", validFrom="2026-06-10")
    new = sdk.create_point("statement", "gym at 5pm", validFrom="2026-06-14")
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-14")

    # default retrieval: superseded old excluded (live preference unchanged)
    default_ids = [r["id"] for r in _scan(sdk)]
    assert old["id"] not in default_ids
    assert new["id"] in default_ids

    # include_terminal surfaces the superseded hit WITH window fields
    all_results = _scan(sdk, include_terminal=True)
    old_hit = next(r for r in all_results if r["id"] == old["id"])
    assert old_hit["status"] == "superseded"
    assert old_hit["valid_from"] == "2026-06-10"
    assert old_hit["valid_to"] == "2026-06-14"
    assert old_hit["expired_at"], "expiredAt promoted on superseded hit"

    new_hit = next(r for r in all_results if r["id"] == new["id"])
    assert new_hit["valid_from"] == "2026-06-14"
    # live point — open window end: additive-only rule means NO valid_to key
    assert "valid_to" not in new_hit


def test_undated_hits_have_no_window_keys(sdk):
    """Undated points: to_dict emits NO valid_* keys (additive-only, #1353
    D8 rule — byte-identical output for legacy graphs)."""
    p = sdk.create_point("statement", "timeless belief")
    results = _scan(sdk)
    hit = next(r for r in results if r["id"] == p["id"])
    assert "valid_from" not in hit
    assert "valid_to" not in hit
    assert "expired_at" not in hit


def test_marker_strings_via_full_scan_decoration(sdk):
    """T4: the real decoration path surfaces window fields so the reader's
    [valid …] marker renders from SearchResult-promoted state."""
    from tools.longmem_eval.retrieve import _validity_marker

    old = sdk.create_point("statement", "gym at 6pm", validFrom="2026-06-10")
    new = sdk.create_point("statement", "gym at 5pm", validFrom="2026-06-14")
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-14")

    results = _scan(sdk, include_terminal=True)
    old_hit = next(r for r in results if r["id"] == old["id"])
    assert old_hit["valid_from"] == "2026-06-10"
    marker = _validity_marker(old_hit)
    assert "[valid 2026-06-10 → 2026-06-14; expired " in marker
    assert marker.endswith("]")

    new_hit = next(r for r in results if r["id"] == new["id"])
    assert "[valid since 2026-06-14]" in _validity_marker(new_hit)


def test_marker_distinguishes_still_open_from_ended_unknown(sdk):
    """#2350: the reader-facing ``[valid …]`` marker must not relabel an
    ended-unknown window as still-true.

    Both points share a start; one is still open, the other is closed at an
    un-datable time (``validToPrecision='unknown'``, no ``validTo``). The
    marker is interpolated into the agent's evidence, so byte-identical
    markers would assert the ended claim is still true — the fabricated-
    coverage anti-pattern the 3-state end exists to prevent.
    """
    from tools.longmem_eval.retrieve import _validity_marker

    still = sdk.create_point("statement", "still true",
                             validFrom="2026-06-14")
    ended = sdk.create_point("statement", "ended, un-datable",
                             validFrom="2026-06-14",
                             validToPrecision="unknown")

    hits = {r["id"]: r for r in _scan(sdk)}
    still_hit, ended_hit = hits[still["id"]], hits[ended["id"]]
    # non-vacuous: the distinction is on the promoted hit dict
    assert still_hit["end_state"] == "open"
    assert ended_hit["end_state"] == "unknown"
    assert ended_hit["valid_to_precision"] == "unknown"
    assert "valid_to" not in ended_hit

    assert _validity_marker(still_hit) == "[valid since 2026-06-14]"
    assert _validity_marker(ended_hit) == \
        "[valid since 2026-06-14; end unknown]"
    assert _validity_marker(ended_hit) != _validity_marker(still_hit)


def test_eval_annotation_forwards_the_end_state_for_the_marker(sdk):
    """#2350: the eval lane rebuilds each hit into a fixed key set, so
    ``_annotate_hits`` must forward the precision/end-state keys or
    ``render_context`` cannot render the distinction on that surface."""
    from tools.longmem_eval.retrieve import _annotate_hits, _validity_marker

    ended = sdk.create_point("statement", "ended, un-datable",
                             validFrom="2026-06-14",
                             validToPrecision="unknown")
    hits = _scan(sdk, include_terminal=True)
    annotated = _annotate_hits(hits, {}, [])
    entry = next(e for e in annotated if e["id"] == ended["id"])
    assert entry["end_state"] == "unknown"
    assert entry["valid_to_precision"] == "unknown"
    assert _validity_marker(entry) == \
        "[valid since 2026-06-14; end unknown]"


def test_eval_annotation_keeps_an_absent_end_absent(sdk):
    """#9202 review: the annotation surface forwards absence as absence.

    FAIL VALUE: ``_annotate_hits`` spelled every missing window field as ``""``
    (``h.get("valid_to") or ""``). ``""`` is NOT this codebase's spelling of
    "no end" — since #9202 ``validity.end_state`` reads a STORED ``""`` as an
    ASSERTED, un-datable end (``END_UNKNOWN``; owner ruling 2026-10-25,
    ONTOLOGY §4.7), and ``time_aware.is_stale_entry`` now honours that state.
    So the annotation minted an end the graph never wrote: every UNDATED eval
    hit ranked STALE at every ``question_date``, and the C6 ``prefer_latest_-
    order`` arm degraded to ``all-stale-never-starve`` — a silent no-op whose
    stats reason reads as a legitimate never-starve. Fixed on the PRODUCER
    side (absence travels as ``None``); the predicate is untouched, so the
    ruling that a stored un-datable end is ENDED is not re-opened.
    """
    from tools.longmem_eval.retrieve import _annotate_hits, _validity_marker
    from tortoise.time_aware import is_stale_entry

    timeless = sdk.create_point("statement", "i live in lisbon")
    hits = _scan(sdk)
    # non-vacuous premise: the search payload carries NO window key for an
    # undated point (pinned by test_undated_hits_have_no_window_keys).
    raw = next(h for h in hits if h["id"] == timeless["id"])
    for key in ("valid_from", "valid_to", "expired_at", "end_state"):
        assert key not in raw, (key, raw)

    entry = next(e for e in _annotate_hits(hits, {}, [])
                 if e["id"] == timeless["id"])

    # THE FIX: an absent end is not an end, at any question_date. (RED on the
    # pre-fix producer: ``entry["valid_to"]`` was ``""`` ⇒ ``is_stale_entry``
    # returned True for every ``qd`` below.)
    for qd in ("1990-01-01", "2026-09-25", "2050-01-01", None):
        assert is_stale_entry(entry, question_date=qd) is False, qd

    # the MECHANISM: the annotated key set stays the documented 21 — absence is
    # a ``None`` VALUE, never a dropped key (the C3-1/C4 driver golden pins the
    # count) and never a minted ``""``.
    assert len(entry) == 21
    for key in ("valid_from", "valid_to", "expired_at",
                "valid_from_precision", "valid_to_precision", "end_state"):
        assert key in entry, key
        assert entry[key] is None, (key, entry[key])

    # The two halves that keep this honest — neither is re-opened by the fix:
    # (i) the PRE-FIX spelling really was read as ENDED (so this pins the
    #     producer, not a blanket "never demote");
    # (ii) a legacy STORED un-datable end still reads ENDED, which is the
    #     #9202 ruling itself.
    assert is_stale_entry({**entry, "valid_to": ""},
                          question_date="2050-01-01") is True
    assert is_stale_entry({**entry, "end_state": "unknown"},
                          question_date="2050-01-01") is True
    # and the reader is byte-identical: no window ⇒ no [valid …] marker.
    assert _validity_marker(entry) == ""


def test_eval_annotation_of_a_legacy_undatable_end_still_reads_ended(sdk):
    """The #9202 ruling survives the producer fix (end-to-end, not by dict)."""
    from tools.longmem_eval.retrieve import _annotate_hits
    from tortoise.time_aware import is_stale_entry

    ended = sdk.create_point("statement", "ended, un-datable",
                             validFrom="2026-06-14",
                             validToPrecision="unknown")
    entry = next(e for e in _annotate_hits(_scan(sdk), {}, [])
                 if e["id"] == ended["id"])
    # the payload normalises the stored end to ``None`` + ``end_state``
    # ``unknown`` (search_engine uses ``or None``); the annotation forwards
    # BOTH, so the state — not a minted ``""`` — is what carries the end.
    assert entry["valid_to"] is None
    assert entry["end_state"] == "unknown"
    for qd in ("2026-09-25", "2050-01-01", None):
        assert is_stale_entry(entry, question_date=qd) is True, qd


def test_provenance_flag_is_absent_by_default_and_additive_when_on(sdk, monkeypatch):
    """Objectives 5/9 + owner decision #3837 (source + when learned + confidence).

    The additive ``provenance`` block (source + captured_at) is OFF by default —
    a default full-scan result is byte-identical to pre-change output (no key).
    With ``TORTOISE_SEARCH_PROVENANCE`` set, every other key/value is unchanged
    and exactly the ``provenance`` block is added.
    """
    p = sdk.create_point(
        "statement",
        "beta ships in October",
        extractedFrom="https://example.com/beta-plan",
    )

    monkeypatch.delenv("TORTOISE_SEARCH_PROVENANCE", raising=False)
    off = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert "provenance" not in off, "flag OFF must not add a response field"

    monkeypatch.setenv("TORTOISE_SEARCH_PROVENANCE", "1")
    on = next(r for r in _scan(sdk) if r["id"] == p["id"])
    prov = on.pop("provenance")
    assert prov["source"] == "https://example.com/beta-plan"
    assert prov["captured_at"], "capture time must be present"
    assert on == off, "the flag must be purely additive — every other key is byte-identical"


def test_provenance_flag_on_still_carries_capture_time_for_unlinked_points(sdk, monkeypatch):
    """An unlinked point has no SOURCE to add, but its capture time is always
    present — so with the flag ON the block IS present and omits only ``source``
    (never an empty shell).

    The flag is additive, not conditional on the point being linked: it emits
    whatever provenance the point actually has, and a point is always created
    with a ``createdAt``.
    """
    p = sdk.create_point("statement", "orphan claim with no source")
    monkeypatch.setenv("TORTOISE_SEARCH_PROVENANCE", "1")
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert "provenance" in hit
    assert "source" not in hit["provenance"]
    assert hit["provenance"]["captured_at"]


def test_provenance_flag_is_point_only(sdk, monkeypatch):
    """The flag enriches POINT searches only — the other documented boundary.

    The flag-gated fetch and the two columns it reads live in the project
    search's ``if entity_type == "point":`` branch (``tortoise/sdk.py``), while
    ``SearchResult`` is constructed for every entity type.  So a non-degraded
    ``document``/``event``/``subject`` search carries no ``source_ref`` /
    ``captured_at`` and ``to_dict`` emits no ``provenance`` block, however the
    flag is set.  This is the second HALF of the boundary documented on
    ``search_engine.search_provenance_enabled``; the degraded-fallback half is
    the other.  Both directions are asserted, so the test cannot pass vacuously
    on an empty result set.
    """
    monkeypatch.setenv("TORTOISE_SEARCH_PROVENANCE", "1")
    p = sdk.create_point("statement", "beta ships in October",
                         extractedFrom="https://example.com/beta-plan")
    doc = sdk.create_document("Beta plan", "memo", content="beta memo body")

    point_hits = sdk.tortoise_fts_query(query=None, kind="statement",
                                        entity_type="point", limit=10)
    point_hit = next(r for r in point_hits if r["id"] == p["id"])
    assert "provenance" in point_hit, "the point leg must be enriched (non-vacuous)"

    doc_hits = sdk.tortoise_fts_query(query=None, kind="memo",
                                      entity_type="document", limit=10)
    doc_hit = next(r for r in doc_hits if r["id"] == doc["id"])
    assert "provenance" not in doc_hit, (
        "a non-Point hit gained a provenance block — the flag is documented as "
        "point-only; update the boundary note with the change")
