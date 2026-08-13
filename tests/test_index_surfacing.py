"""E2E-6 surfacing round-trip verification suite (epic #900 T5, #1041).

PURE VERIFICATION — zero read-surface code changes. Proves indexed knowledge
is discoverable through the read surfaces (list_sources, tortoise_fts_query
kind-scoped structural leg, recall_subgraph, recall_state) exactly as the
plan's §7/E2E-6 contract pins (S7 row: verification-only).

Harness conventions (§7): fresh embedded DB per test via
TortoiseSDK(tempdir/t.db, namespace="e2e-900"); extract_metadata=False
(NO-NETWORK mode — LLM tier disabled AND session embeddings short-circuited);
graph assertions via raw Cypher on sdk._get_proj().g; the REQUIRED-set
invariant sweep runs after every test.

E2E-6 assertion groups:
  1. list_sources returns 4 FLAT rows; consumer-side grouping by sourceKind
     == the run's by_kind counter, same registry vocabulary
     (agentSession/meeting_summary/document — CYCLE-25 v3.6 #6 spelling).
  2. Embedded KIND-SCOPED STRUCTURAL FTS leg: run_structural_query matches
     the stored sourceKind field EXACTLY, so the query-side kind value is
     the REGISTRY spelling ``agentSession`` (the classifier ``agent_session``
     is the WRITE-side vocabulary — CLASSIFIER_TO_SOURCE_KIND maps it; the
     structural leg filters on the stored field, verified in search_engine).
     s2.md forces disambiguation WITHIN the kind: the result must be exactly
     {s1, s2} urls, never the meeting/doc Sources. True-FTS text
     disambiguation is SERVER-MODE-ONLY (bolt://, skip-if-unavailable marker,
     creates the Source FTS index on _searchText in setup).
  3. recall_subgraph seeded by Source url: meeting Source → its Event with
     the references edge; doc Source → its Document.
  4. UC1 negative regression (OQ-2 lock): recall_state rows all have
     entity_type in {point, object} — no source/event rows leak into UC1's
     pool (protects shipped #898 semantics).
"""
from __future__ import annotations

import os
import tempfile
from collections import Counter
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK


def _db() -> str:
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _sdk(db: str | None = None) -> TortoiseSDK:
    return TortoiseSDK(db or _db(), namespace="e2e-900")


def _required_sweep(g) -> int:
    """§7 harness pin: no ontology-REQUIRED violation on any Source."""
    return g.query(
        "MATCH (s:Source) WHERE s.url IS NULL OR s.url='' OR s.sourceKind IS NULL "
        "OR s.contentHash IS NULL OR s.contentHash='' OR s.ingestedAt IS NULL "
        "RETURN count(s)").result_set[0][0]


def _write_corpus(c: Path) -> None:
    """The E2E-6 fixture corpus: one file of each type PLUS a second
    same-kind (agent_session) file (plan §7 E2E-6 setup, cycle-3 pin)."""
    (c / "s1.md").write_text(
        "---\nsessionId: abc123\nagent: pi\n"
        "title: \"Auth refactor session\"\n"
        "startedAt: \"2026-08-10T09:00:00+00:00\"\n"
        "---\n## Summary\nRefactored the auth middleware; decided to keep "
        "JWT rotation.\n")
    (c / "s2.md").write_text(
        "---\nsessionId: def456\nagent: pi\n"
        "title: \"Billing migration session\"\n"
        "startedAt: \"2026-08-11T09:00:00+00:00\"\n"
        "---\n## Summary\nMigrated billing to the new ledger.\n")
    (c / "meeting-2026-08-05.md").write_text(
        "---\nfileType: meeting\ntitle: \"Team Sync\"\n"
        "date: \"2026-08-05T14:00:00+00:00\"\nparticipants: [alice, bob]\n"
        "decisions: [\"Adopt contentHash-gated indexing\"]\n"
        "topics: [planning, indexing]\n"
        "---\nMet to review the indexing epic.\n")
    (c / "strategy.md").write_text(
        "---\ntitle: \"GTM Strategy\"\ntype: strategyDoc\n"
        "domain: product\ncreated: \"2026-08-01T00:00:00+00:00\"\n"
        "authoredBy: daniel\n---\nStrategy body text.\n")


@pytest.fixture
def corpus(tmp_path):
    c = tmp_path / "corpus"
    c.mkdir()
    _write_corpus(c)
    return c


# ── E2E-6.1: flat list_sources + by_kind vocabulary agreement ──────────

def test_e2e6_list_sources_flat_rows_and_by_kind(corpus):
    """E2E-6.1: list_sources returns 4 FLAT rows; consumer-side grouping by
    sourceKind == the run's by_kind counter (same registry vocabulary —
    agentSession/meeting_summary/document, CYCLE-25 v3.6 #6 spelling)."""
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["indexed"] == 4 and r["failed"] == 0 and r["skipped"] == 0

        rows = sdk.list_sources()
        assert len(rows) == 4
        # Flat rows: every row carries url + sourceKind + points.
        assert all({"url", "sourceKind", "points"} <= set(row) for row in rows)
        assert all(row["points"] == 0 for row in rows)

        # Consumer-side grouping over the FLAT rows (list_sources itself is
        # flat — grouping is consumer-side aggregation, §3.4/S7).
        grouping = dict(Counter(row["sourceKind"] for row in rows))
        assert grouping == {"agentSession": 2, "meeting_summary": 1,
                            "document": 1}

        # The run's own by_kind counter reports the SAME registry vocabulary.
        assert r["by_kind"] == {"agentSession": 2, "meeting_summary": 1,
                                "document": 1}
        # A classifier label (agent_session/meeting/doc) leaking into either
        # side fails the vocabulary pin.
        assert set(grouping) == set(r["by_kind"])

        # Every indexed file appears exactly once; urls are the canonical
        # corpus:// permalinks.
        urls = {row["url"] for row in rows}
        assert urls == {
            f"corpus://{corpus.name}/s1.md",
            f"corpus://{corpus.name}/s2.md",
            f"corpus://{corpus.name}/meeting-2026-08-05.md",
            f"corpus://{corpus.name}/strategy.md",
        }
        assert _required_sweep(sdk._get_proj().g) == 0
    finally:
        sdk.close()


# ── E2E-6.2: KIND-SCOPED STRUCTURAL FTS leg (embedded) ─────────────────

def test_e2e6_kind_scoped_structural_fts_leg(corpus):
    """E2E-6.2 embedded-harness leg (PINNED, cycle-2): the embedded harness
    has NO fulltext index (run_fts_query returns [] deterministically), so
    this leg asserts the KIND-SCOPED STRUCTURAL leg — an explicit PRESENCE +
    KIND-SCOPING check. s2.md (second same-kind file) forces disambiguation
    WITHIN the kind.

    The query-side kind value is the REGISTRY spelling ``agentSession``
    (CYCLE-25 v3.6 #6): run_structural_query filters on the stored
    ``sourceKind`` field EXACTLY (search_engine.py — no text matching, score
    1.0); the classifier ``agent_session`` is the WRITE-side vocabulary,
    mapped by CLASSIFIER_TO_SOURCE_KIND (file_indexer). The meeting Event
    analog (kind="meeting", entity_type="event") uses the classifier spelling
    because Event nodes store eventKind="meeting" directly."""
    from tortoise.file_indexer import CLASSIFIER_TO_SOURCE_KIND
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        s1 = f"corpus://{corpus.name}/s1.md"
        s2 = f"corpus://{corpus.name}/s2.md"
        meeting = f"corpus://{corpus.name}/meeting-2026-08-05.md"
        doc = f"corpus://{corpus.name}/strategy.md"

        # Vocabulary pin: the write-side classifier maps to the registry value
        # written on the Source node (CYCLE-25 note in the plan).
        assert CLASSIFIER_TO_SOURCE_KIND["agent_session"] == "agentSession"
        stored = g.query(
            "MATCH (s:Source) RETURN s.url, s.sourceKind").result_set
        assert {url: sk for url, sk in stored}[s1] == "agentSession"
        assert {url: sk for url, sk in stored}[s2] == "agentSession"
        assert {url: sk for url, sk in stored}[meeting] == "meeting_summary"
        assert {url: sk for url, sk in stored}[doc] == "document"

        # KIND-SCOPED STRUCTURAL leg for agentSession Sources: exactly the
        # {s1, s2} urls — s1 PRESENT (presence), every row an agentSession
        # Source, NEVER the meeting/doc Sources (kind scoping).
        res = sdk.tortoise_fts_query(
            "Auth refactor", kind="agentSession", entity_type="source")
        got = [row["id"] for row in res]          # id == url for sources
        assert set(got) == {s1, s2}
        # Every returned row is an agentSession Source (the structural leg
        # filters on the stored sourceKind field — kind-scoping guarantee).
        assert all(row["point_kind"] == "agentSession" for row in res)

        # Meeting/doc kinds stay scoped out of the agentSession query.
        assert all(row["id"] not in (meeting, doc) for row in res)

        # Kind-scoping for the OTHER kinds (structural leg is kind-field
        # driven — each kind query returns exactly its own Source; these two
        # legs are contract-aligned SUPERSETS beyond the pinned agentSession
        # leg — they strengthen falsifiability and match S7's "grouping by
        # sourceKind" verification point).
        res_meeting = sdk.tortoise_fts_query(
            "Team Sync", kind="meeting_summary", entity_type="source")
        assert [row["id"] for row in res_meeting] == [meeting]
        res_doc = sdk.tortoise_fts_query(
            "GTM Strategy", kind="document", entity_type="source")
        assert [row["id"] for row in res_doc] == [doc]

        # Analog: meeting Event via the kind-scoped structural leg (Event
        # nodes store eventKind="meeting" — classifier spelling).
        ev = sdk.tortoise_fts_query(
            "Team Sync", kind="meeting", entity_type="event")
        assert [row["id"] for row in ev] == ["meeting_2026-08-05-team-sync"]

        assert _required_sweep(g) == 0
    finally:
        sdk.close()


# ── E2E-6.2 server-mode leg (bolt:// only, skip-if-unavailable) ─────────

def _server_uri_or_skip():
    """Skip-if-unavailable marker for the server-mode (bolt://) leg (#942
    convention): requires TORTOISE_DB_URI pointing at a live FalkorDB."""
    from tests._live_utils import _skip_unless_live_uri
    _skip_unless_live_uri()


def test_e2e6_server_mode_fts_text_disambiguation(tmp_path):
    """E2E-6.2 server-mode leg (bolt:// only — skip-if-unavailable marker).
    CREATES the Source FTS index on _searchText in setup (no Source FTS index
    exists anywhere in non-test code), then asserts the TRUE FTS leg:
    tortoise_fts_query("Auth refactor", entity_type="source") returns s1 —
    and NOT s2 — by title match (_searchText=title write-path pin, T3). With
    the second same-kind file (s2.md) this leg is the text-disambiguation
    authority."""
    _server_uri_or_skip()
    c = tmp_path / "corpus"
    c.mkdir()
    _write_corpus(c)
    sdk = TortoiseSDK(namespace="test_index_surfacing")
    try:
        g = sdk._get_proj().g
        # Destructive reset is scoped to THIS test-only graph (dedicated
        # namespace "test_index_surfacing" — no other test uses it; the
        # _assert_test_graph guard passes; index dropped in teardown).
        g.query("MATCH (n) DETACH DELETE n")
        try:
            g.query("CALL db.idx.fulltext.createNodeIndex('Source', '_searchText')")
        except Exception as e:
            pytest.skip(f"Source FTS index creation unsupported: {e}")

        r = sdk.index_directory(str(c), extract_metadata=False)
        assert r["indexed"] == 4
        s1 = f"corpus://{c.name}/s1.md"
        s2 = f"corpus://{c.name}/s2.md"

        res = sdk.tortoise_fts_query("Auth refactor", entity_type="source")
        got = [row["id"] for row in res]
        # True text disambiguation: s1's _searchText is "Auth refactor
        # session" (matches); s2's is "Billing migration session" (no match).
        assert s1 in got, f"s1 not in FTS results: {got}"
        assert s2 not in got, f"s2 leaked into FTS results: {got}"

        # The registry sourceKind is what got indexed (vocabulary consistency
        # with E2E-6.1).
        rows = sdk.list_sources()
        assert dict(Counter(row["sourceKind"] for row in rows)) == {
            "agentSession": 2, "meeting_summary": 1, "document": 1}
        assert _required_sweep(g) == 0
    finally:
        try:
            sdk._get_proj().g.query("CALL db.idx.fulltext.dropIndex('Source')")
        except Exception:
            pass
        sdk.close()


# ── E2E-6.3: recall_subgraph seed-by-url ───────────────────────────────

def test_e2e6_recall_subgraph_seed_by_url(corpus):
    """E2E-6.3: recall_subgraph seeded with a Source url resolves the node
    and returns its references neighbor. Meeting Source → its Event (edge
    references present in edges); doc Source → its Document."""
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        meeting = f"corpus://{corpus.name}/meeting-2026-08-05.md"
        doc = f"corpus://{corpus.name}/strategy.md"

        # Meeting Source seed → Source + its Event, references edge present.
        sub = sdk.recall_subgraph(seed=meeting, completeness="full")
        nodes = sub.get("nodes", [])
        assert any(n.get("id") == meeting and n.get("type") == "source"
                   for n in nodes)
        assert any(n.get("type") == "event"
                   and n.get("id") == "meeting_2026-08-05-team-sync"
                   for n in nodes)
        assert any(
            e.get("source") == meeting
            and e.get("type") == "references"
            and e.get("target") == "meeting_2026-08-05-team-sync"
            for e in sub.get("edges", []))

        # Doc Source seed → Source + its Document.
        sub2 = sdk.recall_subgraph(seed=doc, completeness="full")
        nodes2 = sub2.get("nodes", [])
        assert any(n.get("id") == doc and n.get("type") == "source"
                   for n in nodes2)
        assert any(n.get("type") == "document"
                   and n.get("id") == "doc_strategy.md" for n in nodes2)

        # The seed-by-url resolution is exact: a Source url never resolves to
        # a Point/event pool — the returned node IS the Source itself.
        assert sub["stats"]["node_count"] >= 2
        assert sub2["stats"]["node_count"] >= 2
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e6_recall_subgraph_encoded_url_seed(tmp_path):
    """E2E-6.3 encoded-url variant (indicator 2): recall_subgraph seeded with
    an ENCODED corpus:// url resolves the Source node (the encoded form is
    the canonical stored url — no decode needed)."""
    c = tmp_path / "my corpus#1"
    c.mkdir()
    (c / "my notes.md").write_text(
        "---\nsessionId: sp1\nagent: pi\ntitle: Notes\n---\nBody")
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        seed = "corpus://my%20corpus%231/my%20notes.md"
        sub = sdk.recall_subgraph(seed=seed, completeness="full")
        nodes = sub.get("nodes", [])
        assert any(n.get("id") == seed and n.get("type") == "source"
                   for n in nodes)
        # Session Source carries its Event neighbor via references.
        assert any(
            e.get("source") == seed and e.get("type") == "references"
            for e in sub.get("edges", []))
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


# ── E2E-6.4: UC1 pool negative regression (OQ-2 lock) ──────────────────

def test_e2e6_uc1_pool_negative_regression(corpus):
    """E2E-6.4 UC1 negative regression (OQ-2 lock): recall_state result rows
    all have entity_type in {"point", "object"} — no source/event rows leak
    into UC1's pool (protects shipped #898 semantics). The graph holds
    Sources/Events/Documents from the index run; a matching Point seeded via
    raw Cypher makes the pool non-empty so the negative assertion is
    falsifiable, not vacuous."""
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        # Seed a matching Point so the UC1 pool is non-empty (raw Cypher —
        # §7 harness convention; no write-path involvement).
        g.query(
            "CREATE (p:Point {id:'e2e900-point-1', content:'Refactored the "
            "auth middleware to use JWT rotation', pointKind:'decision'})")

        rows = sdk.recall_state(query="auth refactor")
        # Non-vacuous: the seeded Point IS returned.
        assert any(r.get("id") == "e2e900-point-1" for r in rows), \
            f"seeded point missing from UC1 pool: {rows}"
        # Negative: every row is point/object — never source/event.
        assert rows, "UC1 pool must be non-empty for the negative assertion"
        assert all(r.get("entity_type") in {"point", "object"} for r in rows)
        assert all(r.get("entity_type") not in {"source", "event"}
                   for r in rows)
        # Raw sweep: no Source/Event node is reachable via the pool.
        assert all(r.get("id") not in {row["url"] for row in sdk.list_sources()}
                   for r in rows)
        assert _required_sweep(g) == 0
    finally:
        sdk.close()
