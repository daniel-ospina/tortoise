"""#3263 — Point provenance edge is written on every write path that HAS a
source, and survives rebuild (live == rebuild).

Bug: ``(Point)-[:extractedFrom]->(:Source)`` was never written for
session-context writes. The eval ingest paths (``ingest.py`` /
``ingest_v2.py``) pass ``session_id`` but no ``extractedFrom``, so every
extracted claim landed as an orphan Point with no Source — making source-tier
calibration (``credibilityTier`` → inherited prior) unsatisfiable BY
CONSTRUCTION (#3263; the downstream no-op is #3139).

Fix: ``create_point`` INFERS the provenance ref from the write context —
``session:<session_id>``, the SAME ref the capture path wires explicitly
(#1350) — when the caller does not supply one. The inferred ref rides the
SAME ``extractedFrom`` prop path as an explicit one, so it is journaled on
PointAdded and replayed by ``_upsert_point_edges`` (live == rebuild).

DB-lane tests (live FalkorDB under TORTOISE_DB_URI; also embedded-safe — no
FTS/vector leg needed).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def prov(tmp_path):
    """(sdk, events_dir) with the JSONL journal wired for rebuild."""
    db = os.path.join(str(tmp_path), "prov.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _provenance(proj) -> set[tuple[str, str, str]]:
    """The full live provenance surface: (point id, source url, sourceKind)."""
    return {
        (r[0], r[1], r[2])
        for r in proj.g.query(
            "MATCH (p:Point)-[:extractedFrom]->(s:Source) "
            "RETURN p.id, s.url, s.sourceKind").result_set
    }


def _sources_of(proj, pid: str) -> set[str]:
    return {
        r[0]
        for r in proj.g.query(
            "MATCH (p:Point {id:$id})-[:extractedFrom]->(s:Source) "
            "RETURN s.url",
            params={"id": pid}).result_set
    }


# ── control: the explicit path (unchanged) ────────────────────────────────


def test_explicit_extractedfrom_still_wires_the_edge(prov):
    sdk, _events = prov
    p = sdk.create_point("statement", "explicit source claim",
                         extractedFrom="https://doc.example/a")
    assert _sources_of(sdk._get_proj(), p["id"]) == {"https://doc.example/a"}


# ── the fix: session context INFERS the source ────────────────────────────


def test_session_context_wires_extractedfrom(prov):
    """A write with a session context has a derivable Source — it must be
    wired, not left orphaned."""
    sdk, _events = prov
    p = sdk.create_point("statement", "session-context claim",
                         session_id="sess-abc")
    assert _sources_of(sdk._get_proj(), p["id"]) == {"session:sess-abc"}


def test_eval_ingest_shape_wires_extractedfrom(prov):
    """The exact call shape the eval ingest paths use (status=draft,
    is_episodic, session_id, NO extractedFrom) — the reported failing
    path."""
    sdk, _events = prov
    sid = "lme:q42:s0"
    p = sdk.create_point("statement", "eval ingested claim",
                         session_id=sid, lme_question_id="q42",
                         lme_session_index=0, is_episodic=True,
                         status="draft")
    assert _sources_of(sdk._get_proj(), p["id"]) == {f"session:{sid}"}


def test_real_ingest_v2_write_payload_wires_extractedfrom(prov):
    """End-to-end through the REAL eval writer (``_write_payload``) — the
    module the issue measured as containing no ``extractedFrom`` /
    ``:Source`` reference at all."""
    from tools.longmem_eval.ingest_v2 import _write_payload

    sdk, _events = prov
    sid = "lme:q7:s1"
    turns = [{"role": "user", "content": "I bought the grey couch in March."}]
    payload = {
        "points": [{"id": "pt_eval_1",
                    "content": "bought the grey couch in March",
                    "about_entities": []}],
        "entities": [], "events": [], "operators": [],
    }
    _write_payload(sdk, payload, sid=sid, qid="q7", si=1,
                   evidence_turns=[], turns=turns, ev_sessions=set(),
                   n_turns=1)
    assert _sources_of(sdk._get_proj(), "pt_eval_1") == {f"session:{sid}"}


def test_explicit_extractedfrom_wins_over_session_inference(prov):
    sdk, _events = prov
    p = sdk.create_point("statement", "both contexts",
                         session_id="sess-abc",
                         extractedFrom="https://doc.example/explicit")
    assert _sources_of(sdk._get_proj(), p["id"]) == {
        "https://doc.example/explicit"}


def test_write_without_source_context_stays_unlinked(prov):
    """No session, no explicit ref → nothing derivable → no edge (and no
    invented Source)."""
    sdk, _events = prov
    p = sdk.create_point("statement", "bare agent thought")
    assert _sources_of(sdk._get_proj(), p["id"]) == set()
    assert _provenance(sdk._get_proj()) == set()


# ── live == rebuild ───────────────────────────────────────────────────────


def test_live_and_rebuild_agree_on_inferred_extractedfrom(prov):
    """The inferred edge must survive the journal → wipe → replay cycle
    byte-for-byte (same url AND same sourceKind)."""
    sdk, events = prov
    sid = "lme:q99:s2"
    sdk.create_point("statement", "claim one", session_id=sid,
                     is_episodic=True, status="draft")
    sdk.create_point("statement", "claim two", session_id="sess-zzz",
                     is_episodic=True, status="draft")
    sdk.create_point("statement", "explicit claim",
                     extractedFrom="https://doc.example/keep")
    live = _provenance(sdk._get_proj())
    assert live, "guard: the live graph must have provenance to rebuild"

    sdk._get_proj().rebuild_all(str(events))
    assert _provenance(sdk._get_proj()) == live, \
        "extractedFrom diverged between live write and rebuild"


def test_rebuild_preserves_session_source_is_episodic(prov):
    """A ``session:`` ref mints an episodic Source on BOTH paths (the
    ``_mint_source_stub`` shared create path, #1486) — live == rebuild."""
    sdk, events = prov
    sdk.create_point("statement", "episodic claim", session_id="sess-ep",
                     is_episodic=True, status="draft")

    def _is_episodic(proj) -> bool:
        return bool(proj.g.query(
            "MATCH (s:Source {url:'session:sess-ep'}) "
            "RETURN coalesce(s.is_episodic, false)").result_set[0][0])

    assert _is_episodic(sdk._get_proj()) is True, "live Source not episodic"
    sdk._get_proj().rebuild_all(str(events))
    assert _is_episodic(sdk._get_proj()) is True, \
        "rebuilt Source lost is_episodic"


# ── review P1: the dedup path must not leave a prop-only lie ───────────────


def test_dedup_hit_does_not_leave_prop_only_provenance(prov):
    """A dedup hit used to forward the inferred ref into ``update_point``,
    which writes the node PROPERTY but never calls ``_link_source``.

    Net effect was the opposite of the fix: the Point carried
    ``extractedFrom`` with no ``:Source`` edge (still an orphan, but now
    reported as having provenance by ``list_drafts``, which reads the prop),
    while a rebuild dropped the prop entirely — so ``live != rebuild`` for the
    very property this fix introduced.
    """
    sdk, events = prov
    content = "identical claim text"
    first = sdk.create_point("statement", content, dedup=True)
    second = sdk.create_point("statement", content,
                              session_id="sess-dedup", dedup=True)
    assert second["id"] == first["id"], "guard: this must be a dedup hit"

    proj = sdk._get_proj()
    # No prop-without-edge: the reported provenance must be truthful.
    got = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.extractedFrom",
        params={"id": first["id"]}).result_set
    assert (not got) or got[0][0] in (None, [], ""), \
        f"prop-only provenance survived the dedup path: {got}"
    assert _sources_of(proj, first["id"]) == set()

    # And whatever the surface is, live and rebuild must still agree.
    live = _provenance(proj)
    proj.rebuild_all(str(events))
    assert _provenance(proj) == live


# ── review P2: the session Source kind is agentSession, not document ──────


def test_session_source_kind_is_agentsession(prov):
    """Ontology §4.6 + #909 §4.3 #6 register **agentSession** as the source-kind
    value for session Sources. The old hard-coded ``document`` default
    mislabelled every session Source, put it in ``document``-scoped queries,
    and was invisible to ``audit.missing_sourceKind`` (which passes).
    """
    sdk, events = prov
    p = sdk.create_point("statement", "kind claim", session_id="sess-kind",
                         is_episodic=True, status="draft")

    def _kinds(proj) -> set[str]:
        return {
            r[0]
            for r in proj.g.query(
                "MATCH (p:Point {id:$id})-[:extractedFrom]->(s:Source) "
                "RETURN s.sourceKind",
                params={"id": p["id"]}).result_set
        }

    assert _kinds(sdk._get_proj()) == {"agentSession"}
    sdk._get_proj().rebuild_all(str(events))
    assert _kinds(sdk._get_proj()) == {"agentSession"}, \
        "replay minted the session Source with a different sourceKind"


def test_non_session_source_kind_still_defaults_to_document(prov):
    """Resolving the session default must not re-label ordinary sources."""
    sdk, _events = prov
    p = sdk.create_point("statement", "doc kind claim",
                         extractedFrom="https://doc.example/kind")
    kinds = {
        r[0]
        for r in sdk._get_proj().g.query(
            "MATCH (p:Point {id:$id})-[:extractedFrom]->(s:Source) "
            "RETURN s.sourceKind",
            params={"id": p["id"]}).result_set
    }
    assert kinds == {"document"}


# ── review P2: many-to-many, and never invent a Source ────────────────────


def test_multiple_source_refs_get_one_edge_each(prov):
    """Ontology §3.3 amended to many→many: a claim extracted from several
    sources carries one edge per source. A list used to be accepted silently
    and mint a SINGLE Source whose ``url`` was an ARRAY — a corrupt
    provenance node, with no error raised.
    """
    sdk, _events = prov
    refs = ["https://a.example", "https://b.example", "https://c.example"]
    p = sdk.create_point("statement", "multi-source claim", extractedFrom=refs)
    assert _sources_of(sdk._get_proj(), p["id"]) == set(refs)

    corrupt = sdk._get_proj().g.query(
        "MATCH (s:Source) WHERE s.url STARTS WITH '[' RETURN s.url").result_set
    assert corrupt == [], f"array-valued Source url minted: {corrupt}"


def test_multi_source_survives_rebuild(prov):
    """The fan-out must be reproducible from the journal (live == rebuild)."""
    sdk, events = prov
    refs = ["https://a.example", "https://b.example"]
    p = sdk.create_point("statement", "multi-source rebuild",
                         extractedFrom=refs)
    live = _sources_of(sdk._get_proj(), p["id"])
    assert live == set(refs), "guard: the live fan-out must exist to rebuild"
    sdk._get_proj().rebuild_all(str(events))
    assert _sources_of(sdk._get_proj(), p["id"]) == set(refs)


def test_non_scalar_session_id_does_not_invent_a_source(prov):
    """Design contract #3 — never fabricate provenance. A non-scalar
    ``session_id`` used to stringify into ``session:['s1', 's2']`` and mint
    that bogus Source."""
    sdk, _events = prov
    p = sdk.create_point("statement", "list session id",
                         session_id=["s1", "s2"])
    assert _sources_of(sdk._get_proj(), p["id"]) == set()
    invented = sdk._get_proj().g.query(
        "MATCH (s:Source) WHERE s.url CONTAINS \"['\" RETURN s.url").result_set
    assert invented == [], f"invented Source minted: {invented}"
