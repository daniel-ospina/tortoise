"""JOINT-E2E (epic #900 #1032) — the cross-epic integration leg.

ONE graph + BOTH writers (index_directory + ingest): the shared Source
machinery converges on a single Source at a shared corpus:// url.

Plan JOINT-E2E (cycle-22/23): seed corpus → index_directory → ingest a
bundle whose source item REUSES a corpus:// url → exactly ONE Source, the
original sourceKind/contentHash/references SURVIVE, version UNCHANGED, the
sweep re-run all-skipped; ZERO mutation of Points/operators/direct edges by
the sweep; the stub-Source exception class is counted/reported by the
REQUIRED sweep, not failed (option 2 — completed by a later sweep).

The core convergence fix landed in projection/entities.py _upsert_source:
an ingest source item carrying NO contentHash ($hash IS NULL) PRESERVES the
stored hash/version/title on ON MATCH — the ingest never clobbers an
index-created Source's contentHash to '' or bumps its version. The joint
test asserts the STRONGER survivor property: an ingest item declaring a
DIFFERENT sourceKind ('report') does NOT overwrite the index-created
'agentSession' kind (the ON MATCH never SETs sourceKind).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_joint_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _query(sdk, cypher: str, params: dict | None = None):
    return sdk._get_proj().g.query(cypher, params=params or {}).result_set


def _count(sdk, cypher: str, params: dict | None = None) -> int:
    rows = _query(sdk, cypher, params)
    return int(rows[0][0]) if rows else 0


def _required_sweep(g) -> int:
    """REQUIRED-set invariant sweep (§7 I9)."""
    return g.query(
        "MATCH (s:Source) WHERE s.url IS NULL OR s.url='' OR s.sourceKind IS NULL "
        "OR s.contentHash IS NULL OR s.contentHash='' OR s.ingestedAt IS NULL "
        "RETURN count(s)").result_set[0][0]


SESSION_FIXTURE = """\
---
sessionId: {sid}
title: "{title}"
---
Body {sid}.
"""


def _corpus(tmp_path, name: str = "corpus") -> Path:
    c = tmp_path / name
    c.mkdir()
    (c / "s1.md").write_text(SESSION_FIXTURE.format(sid="j1", title="J1"))
    (c / "s2.md").write_text(SESSION_FIXTURE.format(sid="j2", title="J2"))
    return c


def test_joint_one_graph_both_writers_share_source(tmp_path, sdk):
    """JOINT-E2E indicator 1: seed corpus via index_directory → ingest a
    bundle whose source item REUSES a corpus:// url → exactly ONE Source,
    the ORIGINAL sourceKind/contentHash/references SURVIVE, version
    UNCHANGED, the sweep re-run all-skipped (no churn)."""
    c = _corpus(tmp_path)
    r1 = sdk.index_directory(str(c), extract_metadata=False)
    assert r1["indexed"] == 2
    url = f"corpus://{c.name}/s1.md"
    before = _query(sdk, "MATCH (s:Source {url:$u}) RETURN s.contentHash, "
                         "s.sourceKind, s.version, s.id, s.title, "
                         "s._searchText",
                    {"u": url})[0]
    content_hash, kind, version, sid, title, search_text = before  # noqa: RUF059
    assert content_hash and kind == "agentSession" and version == 1
    assert title and search_text, "the index path sets title/_searchText"
    # the index path's references edge (Source → AgentSession Event)
    assert _count(sdk, "MATCH (s:Source {url:$u})-[:references]->() "
                       "RETURN count(*)",
                  {"u": url}) == 1

    # ingest a bundle whose source item REUSES the url
    res = sdk.ingest({
        "points": [
            {"ref": "p1", "kind": "statement",
             "content": "Claim about the session."}],
        "entities": [],
        "sources": [{"ref": "src1", "url": url, "sourceKind": "report",
                     "tier": "T1"}],
        "connections": [{"ref": "c1", "from": "p1", "to": "src1",
                         "relation": "extractedFrom"}],
    })
    assert res["created"]["sources"] == 0, \
        "the reused url must MERGE, never create a second Source"
    assert _count(sdk, "MATCH (s:Source) RETURN count(s)") == 2  # s1 + s2
    after = _query(sdk, "MATCH (s:Source {url:$u}) RETURN s.contentHash, "
                        "s.sourceKind, s.version, s.title, s._searchText",
                   {"u": url})[0]
    assert after[0] == content_hash, \
        f"contentHash must survive the ingest merge: {after[0]} vs {content_hash}"
    assert after[1] == kind, f"sourceKind must survive: {after[1]}"
    assert after[2] == version, f"version must be UNCHANGED: {after[2]}"
    assert after[3] == title, "title must survive the no-hash merge"
    assert after[4] == search_text, "_searchText must survive the no-hash merge"
    assert res["deduped"]["sources"] == 1, \
        "the reused-url merge counts as a deduped source"
    # the index path's references edge survives (the merge never touches it)
    assert _count(sdk, "MATCH (s:Source {url:$u})-[:references]->() "
                       "RETURN count(*)",
                  {"u": url}) == 1
    # REQUIRED sweep clean (the indexed Sources carry real hashes)
    assert _required_sweep(sdk._get_proj().g) == 0

    # sweep re-run → all-skipped (no churn — the joint convergence)
    r2 = sdk.index_directory(str(c), extract_metadata=False)
    assert r2["skipped"] == 2 and r2["indexed"] == 0 and r2["updated"] == 0, r2


def test_joint_sweep_zero_point_mutation(tmp_path, sdk):
    """JOINT-E2E indicator 2: the SWEEP never mutates Points/operators/
    direct edges — ingest a bundle with points + a direct edge, then run
    index_directory over a corpus; the point/operator/direct-edge counts
    are IDENTICAL before and after (the sweep touches only Sources/Events)."""
    c = _corpus(tmp_path)
    # ingest a bundle with points + a plain IMPL direct edge
    res = sdk.ingest({  # noqa: F841
        "points": [
            {"ref": "p1", "kind": "statement", "content": "A implies B."},
            {"ref": "p2", "kind": "statement", "content": "B."},
        ],
        "entities": [], "sources": [],
        "connections": [{"ref": "c1", "from": "p1", "to": "p2",
                         "operator": "IMPL"}],
    })
    g = sdk._get_proj().g  # noqa: F841
    points_before = _count(sdk, "MATCH (n:Point) RETURN count(n)")
    edges_before = _count(sdk, "MATCH ()-[r:IMPL|NAND]->() RETURN count(r)")
    ops_before = _count(sdk, "MATCH (o:Point {is_operator:true}) "
                             "RETURN count(o)")
    assert points_before == 2 and edges_before == 1

    # the sweep over the corpus
    sdk.index_directory(str(c), extract_metadata=False)
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == points_before, \
        "the sweep must not create/mutate Points"
    assert _count(sdk, "MATCH ()-[r:IMPL|NAND]->() RETURN count(r)") == edges_before, \
        "the sweep must not create/mutate direct edges"
    assert _count(sdk, "MATCH (o:Point {is_operator:true}) RETURN count(o)") \
        == ops_before, "the sweep must not create operators"


def test_joint_stub_source_exception_class(tmp_path, sdk):
    """JOINT-E2E indicator 3 (option 2 — completed by a later sweep): a
    bundle source item with NO contentHash creates a STUB Source
    (contentHash='') — the REQUIRED sweep COUNTS it as the named exception
    class (never a hard failure / never a false-green), and a later forward
    index_directory over a matching corpus COMPLETES the stub (the
    conditional merge writes the real hash when it differs)."""
    c = _corpus(tmp_path)
    res = sdk.ingest({
        "points": [
            {"ref": "p1", "kind": "statement", "content": "Claim."}],
        "entities": [],
        "sources": [{"ref": "src1",
                     "url": "https://example.com/only-bundle",
                     "sourceKind": "report"}],
        "connections": [],
    })
    assert res["created"]["sources"] == 1
    g = sdk._get_proj().g
    # the stub Source carries an EMPTY contentHash (the exception class)
    stub = _query(sdk, "MATCH (s:Source {url:'https://example.com/only-bundle'}) "
                       "RETURN s.contentHash, s.ingestedAt")[0]
    assert stub[0] == "" and stub[1]
    # NOTE (P2-4, plan option-2 scope): "completed by a later sweep" is only
    # realizable for corpus:// urls — a non-corpus stub (this https url) is
    # a PERMANENT exception-class member (no sweep ever re-passes the url);
    # the REQUIRED sweep reports it indefinitely (counted, never failed).
    # the REQUIRED sweep counts EXACTLY the stub (the exception, not a
    # false-green and not a hard failure): no Sources indexed yet — the
    # sweep returns exactly the 1 bundle stub (the named exception class)
    viol = g.query(
        "MATCH (s:Source) WHERE s.url IS NULL OR s.url='' OR s.sourceKind IS NULL "
        "OR s.contentHash IS NULL OR s.contentHash='' OR s.ingestedAt IS NULL "
        "RETURN s.url").result_set
    assert [r[0] for r in viol] == ["https://example.com/only-bundle"], viol
    # OPTION-2 completion: a forward index_directory over a corpus whose
    # file matches the stub's url completes it — use a corpus url instead:
    # create a stub at a corpus:// url, then the sweep completes it
    corpus_url = f"corpus://{c.name}/s1.md"
    sdk.ingest({
        "points": [{"ref": "p1", "kind": "statement", "content": "C."}],
        "entities": [], "sources": [],
        "connections": [],
    })
    # (the stub at the corpus url is created by a direct create_source call —
    # the ingest's source item for a corpus url with no hash stays a stub)
    sdk.create_source(corpus_url, "agentSession")  # stub at the indexed url
    assert _query(sdk, "MATCH (s:Source {url:$u}) RETURN s.contentHash",
                  {"u": corpus_url})[0][0] == ""
    # the sweep COMPLETES the stub (option 2 — the conditional merge writes
    # the real hash when the stored one differs/NULL)
    r = sdk.index_directory(str(c), extract_metadata=False)  # noqa: F841
    completed = _query(sdk, "MATCH (s:Source {url:$u}) RETURN s.contentHash",
                       {"u": corpus_url})[0][0]
    assert completed and completed != "", \
        "the forward sweep must COMPLETE the stub Source (option 2)"
