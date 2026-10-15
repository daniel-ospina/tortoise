"""T12 (#1349 PR2) — backfill ``--force-re-embed``.

The bge-small swap (T9) invalidated stored MiniLM vectors: T12's
``--force-re-embed`` re-embeds ALL rows (not just ``embedding IS NULL``) with
index-time-composition text so backfill vectors equal what the live index
path would produce under bge.

Covers (plan §Task 12 acceptance):
- WHERE-predicate flip: NULL-only vs all-rows for all 6 LABEL_CONFIG surfaces
  (Point/Subject/Object/Document/Event/Source). #5126: a SURFACE is not a node
  label — ``Document`` sweeps ``:Source`` with ``documentKind`` non-NULL while
  ``Source`` sweeps the provenance Source with ``documentKind`` NULL;
- idempotency on a partial re-run (new node lands, existing rows re-embed to
  identical deterministic vectors, no errors);
- composition parity ×3 — Event non-meeting (``subject + eventKind + object``,
  entities.py:484-488), Document (#5126 / D10: the node's OWN ``_searchText``,
  the surface the document fold composes with ``_build_search_text(title,
  summary, topics)`` — NEVER the retired ``content`` prop), AgentSession-with-
  summary (``session_embedding_text(name, summary, keywords,
  topics)`` with the summary PARSED from ``content_metadata`` — the pre-T12
  hardcoded ``summary=""`` downgrade is the regression under test);
- Source-label disjointness (#5126): a provenance Source and a document Source
  must not embed each other's text — before the fix ONE branch served both and
  a document was embedded as its own URL string;
- retired/unknown surface guards (#5126): ``_validate_config`` refuses a
  retired node label and an undiscriminated shared label; ``_require_surface``
  exits loudly on an unknown surface rather than reading the wrong column;
- meeting handling: purge of legacy #160 meeting embeddings, NULL-eventKind
  inclusion (Cypher NULL != false parity with index-time ``!= "meeting"``),
  post-purge search exclusion (``run_vector_query`` must NOT return purged
  meetings), repair-after-purge no-re-embed;
- per-label completeness marker (6 surfaces re-embedded, meeting purge count,
  repair skips) — machine-verifiable output;
- ``--dry-run`` counts rows that would be written and excludes unaffected
  rows (meetings / text-less), reports the purge count, writes nothing.

Harness conventions (mirrors test_session_semantic_search.py): the backfill
module is loaded via importlib from ``graph-scripts/`` and driven against a
FRESH embedded FalkorDBLite projection per test; embeddings are mocked via
``monkeypatch`` on ``tortoise.embeddings.compute_embedding`` to a
deterministic text-keyed fake — no real model, no network, no Docker.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from tortoise import search_engine
from tortoise.projection import FalkorProjection
from tortoise.session_indexer import session_embedding_text

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKFILL = _REPO_ROOT / "graph-scripts" / "backfill_embeddings.py"
_LABELS = ["Point", "Subject", "Object", "Document", "Event", "Source"]

#: #5126 / D10 (ONTOLOGY v3.15 §4.4): a document IS a ``:Source`` — the
#: ``:Document`` node label is RETIRED, so the Document SURFACE's nodes are
#: labelled Source (discriminated by the non-NULL ``documentKind``).
_NODE_LABEL = {label: ("Source" if label == "Document" else label)
               for label in _LABELS}


class _FakeEmbed:
    """Deterministic text→vector: same text ⇒ same vector; distinct ⇒ distinct.

    Mirrors the model contract of ``compute_embedding`` (str → 384-dim list).
    Recording ``calls`` lets tests assert the EXACT index-time-composition text
    each row was encoded with (composition parity).
    """

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, text: str) -> list[float]:
        self.calls.append(str(text))
        digest = hashlib.sha256(str(text).encode()).digest()
        return [((digest[i % 32] + i * 7) % 251) / 250.0 for i in range(384)]


def _load_backfill():
    spec = importlib.util.spec_from_file_location(
        "backfill_embeddings_t12", str(_BACKFILL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def proj():
    tmpdir = tempfile.mkdtemp(prefix="t12_force_")
    p = FalkorProjection(
        os.path.join(tmpdir, "t12.db"),
        graph_name="tortoise",
    )
    yield p
    p.close()
    # #4096: reclaim this fixture's temp tree on teardown.
    shutil.rmtree(tmpdir, ignore_errors=True)


def _g(proj):
    return proj.db.select_graph(proj.graph_name)


def _create(g, label: str, props: dict, emb: list[float] | None = None):
    """CREATE a node; ``emb`` is stored as vecf32 (legacy MiniLM-style)."""
    params = dict(props)
    clauses = ", ".join(f"{k}: ${k}" for k in props)
    if emb is not None:
        clauses += ", embedding: vecf32($emb)"
        params["emb"] = emb
    g.query(f"CREATE (n:{label} {{{clauses}}})", params=params)


def _embedding(g, label: str, id_prop: str, key: str):
    rows = g.query(
        f"MATCH (n:{label} {{{id_prop}: $key}}) RETURN n.embedding",
        params={"key": key},
    ).result_set
    return rows[0][0] if rows else None


def _fixture_id_prop(surface: str) -> str:
    """The node key for a SURFACE's fixture node (not the node label)."""
    if surface in ("Source", "Document"):
        return "url"
    return "eventId" if surface == "Event" else "id"


def _fixture_embedded_key(label: str) -> str:
    """Id of the already-embedded node ``_flip_fixture`` creates for ``label``."""
    if label == "Event":
        return "e-embedded"
    if label == "Source":
        return "https://a.example"
    if label == "Document":
        return "d-embedded"
    return f"{label}-embedded"


def _backfill_run(mod, proj, labels=None, *, force=False):
    """Drive the backfill's internal per-graph runner (NULL-only or force).

    The embedded fixture lane has no width-fixing index, so the store-declared
    width is ``None`` and the encoder's own width governs (#4280's
    dimension-agnostic brute-force lane) — stated explicitly, since
    ``_backfill_graph``'s ``required_dim`` is keyword-only and required.
    """
    return mod._backfill_graph(
        proj.db, proj.graph_name, labels or _LABELS, 0, 500, force=force,
        required_dim=None)


# ── WHERE-predicate flip (NULL-only vs all-rows) ────────────────────

def _flip_fixture(g, label: str) -> tuple[str, str]:
    """Create one embedded + one NULL-embedding node for ``label``.

    Returns (text_of_embedded, text_of_missing) — the texts the two rows
    compose to at index time.
    """
    if label == "Event":
        # non-meeting branch: subject + eventKind + object
        _create(g, "Event", {"eventId": "e-embedded", "subject": "alpha",
                             "eventKind": "review", "object": "omega"},
                emb=[0.1] * 384)
        _create(g, "Event", {"eventId": "e-missing", "subject": "beta",
                             "eventKind": "review", "object": "omega"})
        return "alpha review omega", "beta review omega"
    if label == "Document":
        # #5126 / D10: the document node is a ``:Source`` carrying
        # ``documentKind``; its text surface is ``_searchText``.
        _create(g, "Source", {"url": "d-embedded", "documentKind": "brief",
                               "title": "Title A", "_searchText": "Title A"},
                emb=[0.1] * 384)
        _create(g, "Source", {"url": "d-missing", "documentKind": "brief",
                               "title": "Title B", "_searchText": "Title B"})
        return "Title A", "Title B"
    if label == "Source":
        _create(g, "Source", {"url": "https://a.example", "sourceKind": "web"},
                emb=[0.1] * 384)
        _create(g, "Source", {"url": "https://b.example", "sourceKind": "web"})
        return "https://a.example", "https://b.example"
    text_prop = "name" if label in ("Subject", "Object") else "content"
    _create(g, label, {"id": f"{label}-embedded", text_prop: "alpha"},
            emb=[0.1] * 384)
    _create(g, label, {"id": f"{label}-missing", text_prop: "beta"})
    return "alpha", "beta"


@pytest.mark.parametrize("label", _LABELS)
def test_force_flips_null_only_predicate_for_label(proj, monkeypatch, label):
    """NULL-only embeds only the missing row; force re-embeds ALL rows."""
    g = _g(proj)
    embedded_text, missing_text = _flip_fixture(g, label)
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)

    # NULL-only (repair) run: only the missing row is encoded.
    stats = _backfill_run(mod, proj, [label])
    assert fake.calls == [missing_text], fake.calls
    assert stats["per_label"][label] == 1
    assert stats["updated"] == 1

    # Force run: the already-embedded row is re-embedded too.
    fake.calls.clear()
    stats = _backfill_run(mod, proj, [label], force=True)
    assert sorted(fake.calls) == sorted([embedded_text, missing_text]), fake.calls
    assert stats["per_label"][label] == 2
    assert stats["updated"] == 2


def test_force_reembeds_all_six_labels(proj, monkeypatch):
    """One force run covers all 6 LABEL_CONFIG labels (per-label counts)."""
    g = _g(proj)
    for label in _LABELS:
        _flip_fixture(g, label)
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    stats = _backfill_run(mod, proj, force=True)
    assert set(stats["per_label"]) == set(_LABELS), stats["per_label"]
    assert all(stats["per_label"][label] == 2 for label in _LABELS)
    assert stats["updated"] == 12
    assert stats["skipped"] == 0


# ── Idempotency ────────────────────────────────────────────────────

def test_force_reembed_idempotent_on_partial_rerun(proj, monkeypatch):
    """Re-running force is safe: existing rows re-embed to IDENTICAL vectors
    and a newly-added node is picked up (no drift, no errors)."""
    g = _g(proj)
    for label in _LABELS:
        _flip_fixture(g, label)
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)

    _first = _backfill_run(mod, proj, force=True)
    stored_before = {
        label: _embedding(g, _NODE_LABEL[label], _fixture_id_prop(label),
                          _fixture_embedded_key(label))
        for label in _LABELS
    }
    assert all(v is not None for v in stored_before.values())

    # Partial re-run: a new Point lands after the first force run.
    _create(g, "Point", {"id": "p-late", "content": "late arrival"})
    fake.calls.clear()
    second = _backfill_run(mod, proj, force=True)
    assert second["per_label"]["Point"] == 3  # 2 + the late arrival
    assert second["updated"] == 13  # 3 Points + 2 × the other 5 labels
    assert all(second["per_label"][label] == (3 if label == "Point" else 2)
               for label in _LABELS)
    assert fake.calls.count("late arrival") == 1

    stored_after = {
        label: _embedding(g, _NODE_LABEL[label], _fixture_id_prop(label),
                          _fixture_embedded_key(label))
        for label in _LABELS
    }
    # Deterministic embedder ⇒ re-embedding is a no-op on existing vectors.
    assert stored_after == stored_before


# ── Composition parity (backfill text == index-time composition) ───

def test_composition_parity_event_non_meeting(proj, monkeypatch):
    """Event non-meeting embeds ``subject + eventKind + object`` exactly like
    projection/entities.py:484-488 — a NULL eventKind row (NOT a meeting) is
    included and the NULL is dropped from composition, matching index-time."""
    g = _g(proj)
    _create(g, "Event", {"eventId": "e1", "subject": "deploy review",
                         "eventKind": "debrief", "object": "svc-api"})
    _create(g, "Event", {"eventId": "e2", "subject": "null-kind event",
                         "object": "target"})  # eventKind NULL — not a meeting
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    _backfill_run(mod, proj, ["Event"])
    assert fake.calls[0] == "deploy review debrief svc-api"
    assert "null-kind event target" in fake.calls  # NULL eventKind included
    # backfill-encoded vector == index-time-composition vector (same text;
    # stored vectors round-trip through vecf32 float32, hence approx)
    stored = _embedding(g, "Event", "eventId", "e1")
    assert stored == pytest.approx(fake("deploy review debrief svc-api"))


def test_composition_parity_document(proj, monkeypatch):
    """#5126: the Document surface embeds the document ``:Source``'s OWN
    ``_searchText`` — the field the document FTS leg reads — and never the
    retired ``content`` prop (ONTOLOGY §4.4: "``content`` is not a graph
    property").

    Not tautological: the node is written through the projection's OWN document
    fold (``_upsert_document``), so the assertion below pins the backfill to the
    index-time composition ``_build_search_text(title, summary, topics)``. The
    pre-fix composition (``title + content`` on a ``:Document`` node) is a
    different string AND a different label.
    """
    g = _g(proj)
    proj.apply({"type": "DocumentCreated", "id": "doc-1",
                "title": "Weekly report", "document_kind": "brief",
                "summary": "sprint findings", "topics": ["ops", "billing"],
                # the INDEX path's own flag (#900 T3 cycle-19): `_doc_write`
                # sets it, so the fold writes no vector — exactly the shape the
                # backfill exists to fill (and no real model in this test).
                "suppress_embedding": True})
    stored = g.query("MATCH (s:Source {url:'doc-1'}) "
                     "RETURN s._searchText, s.content").result_set[0]
    assert stored[1] is None, "the retired `content` prop must not exist"
    expected = "Weekly report sprint findings ops billing"
    assert stored[0] == expected
    # The surface is NOT title-only, so a title-only regression reds here.
    assert stored[0] != "Weekly report"
    assert g.query("MATCH (n:Document) RETURN count(n)").result_set[0][0] == 0

    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    _backfill_run(mod, proj, ["Document"])
    assert fake.calls == [expected], fake.calls
    assert _embedding(g, "Source", "url", "doc-1") == pytest.approx(fake(expected))


def test_source_and_document_surfaces_are_disjoint(proj, monkeypatch):
    """#5126: one node label, two surfaces. ``Source`` sweeps the provenance
    Source (``documentKind`` NULL, text = ``url`` per #448); ``Document`` sweeps
    the document Source (``documentKind`` non-NULL, text = ``_searchText``).
    Before the fix ONE branch served both, so a document was embedded as its own
    URL string.
    """
    g = _g(proj)
    _create(g, "Source", {"url": "https://a.example/paper",
                           "sourceKind": "web", "title": "A paper"})
    proj.apply({"type": "DocumentCreated", "id": "doc-1",
                "title": "Weekly report", "document_kind": "brief",
                "summary": "sprint findings", "suppress_embedding": True})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)

    stats = _backfill_run(mod, proj, ["Source", "Document"])
    assert sorted(fake.calls) == sorted([
        "https://a.example/paper",          # provenance: the url key (#448)
        "Weekly report sprint findings",    # document: its search surface
    ]), fake.calls
    assert stats["per_label"]["Source"] == 1
    assert stats["per_label"]["Document"] == 1
    assert stats["updated"] == 2
    # Neither surface's row was embedded as the other's text.
    assert _embedding(g, "Source", "url", "https://a.example/paper") is not None
    assert _embedding(g, "Source", "url", "doc-1") is not None


def test_document_surface_falls_back_to_title_and_skips_text_less(
        proj, monkeypatch):
    """A pre-#125 document Source (no ``_searchText``) is swept with the SAME
    ``title`` fallback the write path applies — never skipped; a text-less
    document Source is excluded rather than crashing on a NULL text."""
    g = _g(proj)
    _create(g, "Source", {"url": "legacy-doc", "documentKind": "brief",
                           "title": "Legacy title"})
    _create(g, "Source", {"url": "text-less", "documentKind": "brief"})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    _backfill_run(mod, proj, ["Document"])
    assert fake.calls == ["Legacy title"]
    assert _embedding(g, "Source", "url", "legacy-doc") is not None
    assert _embedding(g, "Source", "url", "text-less") is None


def test_document_surface_ignores_a_lingering_legacy_document_node(
        proj, monkeypatch):
    """A pre-migration ``:Document`` node still carrying the retired ``content``
    prop is NOT swept — the surface reads ``:Source`` only, and the provenance
    branch skips it (it owns no ``documentKind``). The pre-fix config is the
    mirror image of this: it read exactly this node and nothing else.
    """
    g = _g(proj)
    _create(g, "Document", {"id": "legacy-1", "title": "Old report",
                             "content": "Old body"})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    stats = _backfill_run(mod, proj, list(mod.LABEL_CONFIG))
    assert "Old report Old body" not in fake.calls, fake.calls
    assert stats["per_label"]["Document"] == 0
    assert _embedding(g, "Document", "id", "legacy-1") is None


def test_document_sweep_feeds_the_document_vector_leg(proj, monkeypatch):
    """The sweep's document predicate is the SAME axis the vector leg filters on
    (``run_vector_query(entity_type='document')`` → ``documentKind IS NOT NULL``),
    so a backfilled document is actually RETURNABLE by the read the sweep exists
    to feed, and the provenance Source does not leak into it.

    The assertions pin the TEXT each surface encoded, not just retrievability.
    Presence alone does not discriminate: pre-fix the undiscriminated ``Source``
    branch embedded the document as its own URL string, and ``doc-1`` is still
    returned because it is the only ``documentKind IS NOT NULL`` candidate and
    it carries a non-NULL embedding either way. The negative against
    ``fake("doc-1")`` is what turns this into a regression pin for #5126.
    """
    g = _g(proj)
    _create(g, "Source", {"url": "https://a.example/provenance",
                           "sourceKind": "web", "title": "Provenance source"})
    proj.apply({"type": "DocumentCreated", "id": "doc-1",
                "title": "Weekly report", "document_kind": "brief",
                "summary": "sprint findings", "suppress_embedding": True})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    _backfill_run(mod, proj, list(mod.LABEL_CONFIG))

    text = "Weekly report sprint findings"
    # The document was encoded from its SEARCH TEXT, never from its url key.
    assert fake.calls.count(text) == 1, fake.calls
    assert "doc-1" not in fake.calls, fake.calls
    # #448 kept: the provenance Source is still embedded from its url — once.
    assert fake.calls.count("https://a.example/provenance") == 1, fake.calls
    # The stored vector is the text vector — the pre-fix code stored
    # ``fake("doc-1")`` here.
    assert _embedding(g, "Source", "url", "doc-1") == pytest.approx(fake(text))
    assert _embedding(g, "Source", "url", "doc-1") != pytest.approx(fake("doc-1"))

    # …and the read the sweep exists to feed returns the document (secondary:
    # true either way, so it is not the regression pin).
    search_engine.reset_circuit_breakers()  # test isolation (#249)
    ids = [r[0] for r in search_engine.run_vector_query(
        g, fake(text), limit=10,
        entity_type="document", is_embedded=True)]
    assert "doc-1" in ids, ids
    assert "https://a.example/provenance" not in ids, ids


# ── #5126: the retired-label / unknown-surface guards ───────────────

def test_no_surface_targets_the_retired_document_node_label():
    """Regression pin: D10 retired the ``:Document`` NODE label, so no surface
    may MATCH it. Pre-fix it did — the MATCH returned 0 rows in every graph
    while the completeness marker reported ``Document: 0``, which reads as "no
    documents" rather than "this sweep cannot see documents".
    """
    mod = _load_backfill()
    labels = {cfg["label"] for cfg in mod.LABEL_CONFIG.values()}
    assert "Document" not in labels, labels
    assert mod.LABEL_CONFIG["Document"]["label"] == "Source"
    assert mod.LABEL_CONFIG["Source"]["label"] == "Source"
    # The retired label stays DECLARED so naming it exits loudly.
    assert "Document" in mod.RETIRED_NODE_LABELS
    # The document surface's text is the searchable field, never `content`.
    assert mod._text_expr("Document") == "coalesce(n._searchText, n.title)"
    assert "content" not in mod._text_expr("Document")


def test_unknown_surface_fails_loudly():
    """An unknown surface ABORTS instead of falling through to a generic text
    read — the pre-#5126 silent-wrong-column shape."""
    mod = _load_backfill()
    with pytest.raises(SystemExit, match="unknown embedding surface"):
        mod._require_surface("Bogus")


def test_validate_config_refuses_retired_label(monkeypatch):
    """A surface pointed back at the retired node label is refused BEFORE any
    connection — the zero-row MATCH cannot be configured back in."""
    mod = _load_backfill()
    mod._validate_config()  # the shipped config is valid
    bad = {k: dict(v) for k, v in mod.LABEL_CONFIG.items()}
    bad["Document"] = {"label": "Document", "text_prop": "title",
                       "id_prop": "id"}
    monkeypatch.setattr(mod, "LABEL_CONFIG", bad)
    with pytest.raises(SystemExit, match="retired node label"):
        mod._validate_config()


def test_validate_config_refuses_undiscriminated_shared_label(monkeypatch):
    """Two surfaces sharing one node label MUST carry disjoint discriminators —
    a branch that lost its discriminator would sweep the other surface's rows.
    """
    mod = _load_backfill()
    orig = {k: dict(v) for k, v in mod.LABEL_CONFIG.items()}
    no_kind = {k: dict(v) for k, v in orig.items()}
    del no_kind["Document"]["kind_pred"]
    monkeypatch.setattr(mod, "LABEL_CONFIG", no_kind)
    with pytest.raises(SystemExit, match="no kind_pred"):
        mod._validate_config()

    same_kind = {k: dict(v) for k, v in orig.items()}
    same_kind["Source"]["kind_pred"] = same_kind["Document"]["kind_pred"]
    monkeypatch.setattr(mod, "LABEL_CONFIG", same_kind)
    with pytest.raises(SystemExit, match="IDENTICAL kind_pred"):
        mod._validate_config()


def test_composition_parity_agent_session_with_summary(proj, monkeypatch):
    """AgentSession embeds name + SUMMARY + keywords + topics with the
    LLM-extracted summary PARSED FROM content_metadata — the pre-T12
    hardcoded summary=\"\" downgrade must not recur."""
    g = _g(proj)
    cm = json.dumps({"schema_version": 1,
                     "summary": "Port migration with rollback rehearsal",
                     "narrative_arc": [], "issues": [], "prs": [],
                     "critical_decisions": []})
    _create(g, "Event", {"eventId": "s1", "eventKind": "AgentSession",
                         "name": "Port migration",
                         "keywords": ["falkordb", "migration"],
                         "topics": ["infrastructure"],
                         "content_metadata": cm})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    _backfill_run(mod, proj, ["Event"])
    expected = session_embedding_text(
        "Port migration", "Port migration with rollback rehearsal",
        ["falkordb", "migration"], ["infrastructure"])
    assert fake.calls == [expected], fake.calls
    # The summary is genuinely in the composed text (non-tautological check
    # against the OLD broken composition):
    assert fake.calls[0] != session_embedding_text(
        "Port migration", "", ["falkordb", "migration"], ["infrastructure"])
    assert "Port migration with rollback rehearsal" in fake.calls[0]
    stored = _embedding(g, "Event", "eventId", "s1")
    assert stored == pytest.approx(fake(expected))


def test_composition_parity_agent_session_malformed_metadata(proj, monkeypatch):
    """Malformed content_metadata degrades to summary=\"\" (never crashes);
    the name/keywords/topics surface is still embedded."""
    g = _g(proj)
    _create(g, "Event", {"eventId": "s1", "eventKind": "AgentSession",
                         "name": "Robust session",
                         "keywords": ["kw"], "topics": ["tp"],
                         "content_metadata": "{not json"})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    _backfill_run(mod, proj, ["Event"])
    assert fake.calls == ["Robust session kw tp"]


# ── Meeting handling ───────────────────────────────────────────────

def test_force_purges_meeting_embeddings_and_excludes_meetings(proj, monkeypatch):
    """Force mode purges legacy #160 meeting embeddings AND never re-embeds
    meetings (force predicate ``eventKind IS NULL OR eventKind <> 'meeting'``
    — Cypher NULL != false parity: NULL-eventKind rows ARE re-embedded)."""
    g = _g(proj)
    _create(g, "Event", {"eventId": "meet1", "eventKind": "meeting",
                         "subject": "standup"}, emb=[0.5] * 384)  # legacy junk
    _create(g, "Event", {"eventId": "meet2", "eventKind": "meeting",
                         "subject": "retro"}, emb=[0.6] * 384)
    _create(g, "Event", {"eventId": "e-nullkind", "subject": "orphan event",
                         "object": "resolver"})  # eventKind NULL
    _create(g, "Event", {"eventId": "e-debrief", "subject": "deploy",
                         "eventKind": "debrief", "object": "svc-api"})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)

    stats = mod._force_reembed_graph(
        proj.db, proj.graph_name, _LABELS, 0, 500, required_dim=None)
    assert stats["meeting_purged"] == 2
    # Meetings were never encoded; NULL-kind + non-meeting rows were.
    assert "standup" not in fake.calls and "retro" not in fake.calls
    assert "orphan event resolver" in fake.calls          # NULL-kind INCLUDED
    assert "deploy debrief svc-api" in fake.calls         # non-meeting INCLUDED
    # Purged meetings have no embedding; eligible rows do.
    assert _embedding(g, "Event", "eventId", "meet1") is None
    assert _embedding(g, "Event", "eventId", "meet2") is None
    assert _embedding(g, "Event", "eventId", "e-nullkind") is not None
    assert _embedding(g, "Event", "eventId", "e-debrief") is not None
    # NULL-only repair pass after purge must NOT re-embed the meetings
    # (repair-after-purge no-re-embed).
    fake.calls.clear()
    _backfill_run(mod, proj, ["Event"])
    assert "standup" not in fake.calls and "retro" not in fake.calls
    assert _embedding(g, "Event", "eventId", "meet1") is None
    assert _embedding(g, "Event", "eventId", "meet2") is None


def test_post_purge_run_vector_query_excludes_meetings(proj, monkeypatch):
    """After the purge leg, Event vector search must NOT return meetings
    (their embedding is NULL — excluded from the vecf32 brute-force scan)."""
    g = _g(proj)
    _create(g, "Event", {"eventId": "meet1", "eventKind": "meeting",
                         "subject": "standup meeting"}, emb=[0.5] * 384)
    _create(g, "Event", {"eventId": "e-debrief", "subject": "deploy debrief",
                         "eventKind": "debrief", "object": "svc-api"})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    mod._force_reembed_graph(proj.db, proj.graph_name, _LABELS, 0, 500,
                             required_dim=None)

    search_engine.reset_circuit_breakers()  # test isolation (#249)
    qv = fake("deploy debrief svc-api")
    results = search_engine.run_vector_query(
        g, qv, limit=10, entity_type="event", is_embedded=True)
    ids = [r[0] for r in results]
    assert "meet1" not in ids, results
    assert "e-debrief" in ids, results


def test_plain_null_only_run_excludes_meetings_too(proj, monkeypatch):
    """The NULL-only (repair) predicate applies the SAME meeting exclusion —
    a meeting whose embedding is NULL (e.g. just purged) is never re-embedded
    by a plain backfill run."""
    g = _g(proj)
    _create(g, "Event", {"eventId": "meet1", "eventKind": "meeting",
                         "subject": "standup"})  # embedding NULL (post-purge)
    _create(g, "Event", {"eventId": "e1", "subject": "normal event",
                         "eventKind": "review", "object": "obj"})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    _backfill_run(mod, proj, ["Event"])
    assert "standup" not in fake.calls
    assert _embedding(g, "Event", "eventId", "meet1") is None
    assert _embedding(g, "Event", "eventId", "e1") is not None


# ── Completeness marker + CLI flow ─────────────────────────────────

def _run_main(mod, proj, *argv):
    """Drive mod.main() against the embedded DB with the CLI argv.

    main(argv) receives flag args only — argparse supplies its own prog name.

    Epic #1647 P4 (Task 10): inject the fixture projection's ACTUAL graph
    name (--graph) — on the embedded lane it is the literal "tortoise" (the
    script's default); under a docker session the URI-aware redirect derives
    per-path names (test_<stem>_<hash12>), so the script's literal default
    would scan an empty graph while the fixture's data lives on the derived
    one. The injected name is lane-agnostic and keeps this file docker-able
    (it leaves the embedded allowlist at P4).
    """
    mod._connect_falkordb = lambda uri: (
        proj.db, proj.graph_name, proj.required_embedding_dim)
    return mod.main([*argv, "--graph", proj.graph_name])


def test_completeness_marker_records_all_six_labels(proj, monkeypatch, capsys):
    """Force run emits a machine-verifiable COMPLETENESS line: per-label
    re-embedded counts for all 6 labels, AgentSession sub-breakdown, meeting
    purge count, repair skips."""
    g = _g(proj)
    for label in _LABELS:
        _flip_fixture(g, label)
    # An AgentSession (Event sub-kind) + a legacy meeting with an embedding.
    _create(g, "Event", {"eventId": "s1", "eventKind": "AgentSession",
                         "name": "Session alpha", "keywords": ["kw"],
                         "topics": ["tp"],
                         "content_metadata": json.dumps(
                             {"schema_version": 1, "summary": "Session summary"})})
    _create(g, "Event", {"eventId": "meet1", "eventKind": "meeting",
                         "subject": "standup"}, emb=[0.5] * 384)
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)

    rc = _run_main(mod, proj, "--force-re-embed", "--uri", "docker://dummy")
    assert rc == 0
    out = capsys.readouterr().out
    marker_line = next(line for line in out.splitlines()
                       if line.startswith("COMPLETENESS "))
    marker = json.loads(marker_line[len("COMPLETENESS "):])
    # All 6 labels re-embedded; Event = non-meeting + AgentSession rows.
    assert set(marker["labels"]) == set(_LABELS)
    assert marker["labels"]["Point"] == 2
    assert marker["labels"]["Subject"] == 2
    assert marker["labels"]["Object"] == 2
    assert marker["labels"]["Document"] == 2
    assert marker["labels"]["Event"] == 3  # e-embedded + e-missing + s1
    assert marker["labels"]["Source"] == 2
    assert marker["agent_sessions"] == 1
    assert marker["meeting_purged"] == 1
    assert marker["repair_skipped"] == 0


def test_dry_run_force_counts_affected_rows_and_reports_purge(
        proj, monkeypatch, capsys):
    """--dry-run --force-re-embed counts the rows a force run would write
    (excluding unaffected meetings / text-less rows), reports the purge
    count, and writes NOTHING."""
    g = _g(proj)
    _create(g, "Point", {"id": "p1", "content": "alpha"}, emb=[0.1] * 384)
    _create(g, "Point", {"id": "p2", "content": "beta"})
    _create(g, "Event", {"eventId": "meet1", "eventKind": "meeting",
                         "subject": "standup"}, emb=[0.5] * 384)
    _create(g, "Event", {"eventId": "e1", "subject": "deploy",
                         "eventKind": "debrief", "object": "svc"})
    # #5126: the document node is a :Source with a non-NULL documentKind.
    _create(g, "Source", {"url": "doc-1", "documentKind": "brief",
                           "title": "Title", "_searchText": "Title"})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)

    rc = _run_main(mod, proj, "--dry-run", "--force-re-embed",
                   "--uri", "docker://dummy")
    assert rc == 0
    out = capsys.readouterr().out
    # 2 Points + 1 non-meeting Event + 1 Document = 4 rows would be
    # re-embedded; the meeting is excluded from the count but reported as a
    # purge candidate.
    assert "4 nodes would be re-embedded" in out, out
    assert "1 meeting embeddings would be purged" in out, out
    assert "standup" not in fake.calls  # dry-run never computes embeddings
    # Nothing was written: p1 keeps its vector, the meeting keeps its junk
    # vector, p2/e1/d1 stay NULL.
    assert _embedding(g, "Point", "id", "p1") == pytest.approx([0.1] * 384)
    assert _embedding(g, "Point", "id", "p2") is None
    assert _embedding(g, "Event", "eventId", "meet1") == pytest.approx([0.5] * 384)
    assert _embedding(g, "Event", "eventId", "e1") is None
    assert _embedding(g, "Source", "url", "doc-1") is None


def test_dry_run_force_does_not_over_count_text_less_document(
        proj, monkeypatch, capsys):
    """#5126 review: ``--dry-run --force-re-embed`` counted a text-less document
    Source that the live force run then SKIPPED, so the operator's counts-first
    gate over-reported (and so did the ``N nodes would be re-embedded``
    headline). ``''`` is non-NULL in Cypher, so a bare ``IS NOT NULL`` counted
    `coalesce('', title)`; the sweep now uses ONE non-empty-text predicate for
    both legs.

    Pre-fix this fails twice: the dry run says 2 (not 1), and the live pass
    records ``skipped == 1`` (not 0).
    """
    g = _g(proj)
    # The repo's own text-less shape (`backfill_document_search_text` writes
    # `_searchText = coalesce(title, '')` on a title-less document): an EMPTY
    # (non-NULL) `_searchText`, no title. Plus one real document.
    _create(g, "Source", {"url": "empty-doc", "documentKind": "brief",
                           "_searchText": ""})
    _create(g, "Source", {"url": "real-doc", "documentKind": "brief",
                           "title": "Real title", "_searchText": "Real title"})
    # A provenance Source must be untouched by this (its text is the url).
    _create(g, "Source", {"url": "https://a.example/prov",
                           "sourceKind": "web"})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)

    rc = _run_main(mod, proj, "--dry-run", "--force-re-embed",
                   "--uri", "docker://dummy")
    assert rc == 0
    out = capsys.readouterr().out
    # 1 document (real-doc) + 1 provenance Source = 2; empty-doc is NOT counted.
    assert "2 nodes would be re-embedded" in out, out
    assert "Document [:Source]: 1 rows would be re-embedded" in out, out
    assert "Document [:Source]: 2 rows would be re-embedded" not in out, out

    # The live force run agrees with the dry run it preceded: nothing skipped.
    stats = _backfill_run(mod, proj, list(mod.LABEL_CONFIG), force=True)
    assert stats["per_label"]["Document"] == 1, stats
    assert stats["skipped"] == 0, stats
    assert stats["updated"] == stats["scanned"] == 2, stats
    assert _embedding(g, "Source", "url", "real-doc") is not None
    assert _embedding(g, "Source", "url", "empty-doc") is None
    assert "" not in fake.calls


def test_dry_run_plain_counts_missing_rows_only(proj, monkeypatch, capsys):
    """The plain (NULL-only) dry run keeps the historical contract: counts
    rows missing embeddings only — already-embedded rows are unaffected and
    excluded."""
    g = _g(proj)
    _create(g, "Point", {"id": "p1", "content": "alpha"}, emb=[0.1] * 384)
    _create(g, "Point", {"id": "p2", "content": "beta"})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    rc = _run_main(mod, proj, "--dry-run", "--uri", "docker://dummy")
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 nodes would be embedded" in out, out
    assert _embedding(g, "Point", "id", "p1") == pytest.approx([0.1] * 384)
    assert _embedding(g, "Point", "id", "p2") is None


# ── Store width guard (#4289) ───────────────────────────────────────

class _WrongWidthEmbed:
    """A non-384 encoder — the shape a rotated/foreign embedder produces."""

    def __init__(self, width: int):
        self.width = width
        self.calls: list[str] = []

    def __call__(self, text: str) -> list[float]:
        self.calls.append(str(text))
        return [0.25] * self.width


def test_indexed_store_declines_a_non_384_encoder(proj, monkeypatch):
    """#4289: the sweep applies the store's declared width like any writer.

    Before this fix the backfill called the raw ``compute_embedding`` seam and
    wrote whatever width came back, for an INDEXED Point — the one Point
    writer with no width guard (#4280 put the check at the store-scoped
    entry points). A store declaring ``EMBEDDING_DIM`` must decline the
    wrong-width vector (row skipped, nothing written); the SAME encoder into
    an index-less store (``required_dim=None`` — the brute-force read lane)
    must be KEPT, because dropping it there is the regression #4280 fixed.
    """
    from tortoise.embeddings import EMBEDDING_DIM

    g = _g(proj)
    _create(g, "Point", {"id": "p1", "content": "alpha"})
    mod = _load_backfill()
    wrong = _WrongWidthEmbed(EMBEDDING_DIM - 1)
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", wrong)

    # Indexed store: the wrong width is declined, nothing is stored.
    indexed = mod._backfill_graph(
        proj.db, proj.graph_name, ["Point"], 0, 500,
        required_dim=EMBEDDING_DIM)
    assert indexed["updated"] == 0, indexed
    assert indexed["skipped"] == 1, indexed
    assert indexed["per_label"]["Point"] == 0, indexed
    assert _embedding(g, "Point", "id", "p1") is None
    assert wrong.calls == ["alpha"], wrong.calls

    # Index-less store: the encoder's own width governs — kept, not dropped.
    indexless = mod._backfill_graph(
        proj.db, proj.graph_name, ["Point"], 0, 500, required_dim=None)
    assert indexless["updated"] == 1, indexless
    assert indexless["skipped"] == 0, indexless
    stored = _embedding(g, "Point", "id", "p1")
    assert stored is not None
    assert len(stored) == EMBEDDING_DIM - 1


def test_backfill_graph_requires_an_explicit_store_width(proj):
    """#4289 review (P3): the width must be STATED, never implicitly defaulted.

    ``required_dim=None`` means one measured thing — "this store has no
    width-fixing index" — and must not double as "the caller forgot". A
    default of ``None`` lets a future caller omit the width and silently write
    a wrong-width vector into an INDEXED store: the #4289 hole, re-opened.
    ``required_dim`` is therefore keyword-only and REQUIRED, so the omission
    is a ``TypeError`` at the call site.

    Pre-fix this fails: the call below returned normally (the default ``None``
    meant "no guard"), so no ``TypeError`` was raised.
    """
    mod = _load_backfill()
    with pytest.raises(TypeError):
        mod._backfill_graph(proj.db, proj.graph_name, ["Point"], 0, 500)


def test_cli_threads_the_store_width_into_the_sweep(
        proj, monkeypatch, capsys):
    """#4289: ``main`` must hand the STORE's declared width to the sweep.

    The unit test above calls ``_backfill_graph`` directly, so it cannot catch
    a wiring gap at the ``_connect_falkordb`` / ``main`` call sites — and the
    pre-fix call sites never consulted the projection at all. This drives the
    CLI: an indexed store + a wrong-width encoder must write no vector, which
    is only true if the declared width actually reached the row encoder.
    """
    from tortoise.embeddings import EMBEDDING_DIM
    from tortoise.projection import FalkorProjection

    g = _g(proj)
    _create(g, "Point", {"id": "p1", "content": "alpha"})
    mod = _load_backfill()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        _WrongWidthEmbed(EMBEDDING_DIM - 1))
    # The store DECLARES a width (an index exists) without needing a real one
    # on the embedded fixture lane — #4280's own test shape.
    monkeypatch.setattr(FalkorProjection, "required_embedding_dim",
                        property(lambda self: EMBEDDING_DIM))

    rc = _run_main(mod, proj, "--uri", "docker://dummy")
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 scanned, 0 embedded, 1 skipped" in out, out
    assert _embedding(g, "Point", "id", "p1") is None


def test_required_dim_for_graph_binds_the_projection_to_the_target(
        monkeypatch):
    """#4289 review: the width is resolved from the graph being WRITTEN.

    ``required_embedding_dim`` is read off the projection's OWN graph, so the
    resolver must open the projection bound to the target graph and release
    it — not reuse the URI graph's value.
    """
    from tortoise.projection import FalkorProjection

    mod = _load_backfill()
    seen: list[str | None] = []
    built: dict[str, object] = {}

    class _FakeProj:
        def __init__(self, graph_name):
            self.graph_name = graph_name
            # The per-graph fact under test: only org_acme is indexed.
            self.required_embedding_dim = 384 if graph_name == "org_acme" else None
            self.closed = False

        def close(self):
            self.closed = True

    def fake_from_uri(uri, graph_name=None):
        seen.append(graph_name)
        proj = _FakeProj(graph_name)
        built[graph_name] = proj
        return proj

    monkeypatch.setattr(FalkorProjection, "from_uri",
                        staticmethod(fake_from_uri))
    assert mod._required_dim_for_graph("docker://dummy", "org_acme") == 384
    assert seen == ["org_acme"], seen
    assert built["org_acme"].closed is True


def test_all_tenants_resolves_the_width_per_target_graph(
        proj, monkeypatch, capsys):
    """#4289 review: an ``--all-tenants`` sweep must ask each ``org_{id}``
    graph, not reuse the URI graph's width.

    URI graph index-less (``required_dim=None``) and tenant graph indexed
    (``EMBEDDING_DIM``): pre-fix ``main`` reused the URI graph's ``None``, so
    the wrong-width vector was written into an INDEXED tenant graph — the
    #4289 hole left open for every target that is not the URI's graph.
    """
    from tortoise.embeddings import EMBEDDING_DIM

    proj.db.select_graph("registry").query("CREATE (n:Team {id: 'acme'})")
    tenant_graph = "org_acme"
    tenant = proj.db.select_graph(tenant_graph)
    _create(tenant, "Point", {"id": "p1", "content": "alpha"})

    mod = _load_backfill()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        _WrongWidthEmbed(EMBEDDING_DIM - 1))
    # The URI's graph (what `_connect_falkordb` opens) is index-less.
    mod._connect_falkordb = lambda uri: (proj.db, proj.graph_name, None)
    asked: list[str] = []

    def fake_resolver(uri, graph_name, *, default_graph=None,
                      default_dim=None):
        asked.append(graph_name)
        return EMBEDDING_DIM

    # raising=False: pre-fix the resolver does not exist — the test must fail
    # on the assertion below (the vector WAS written), not on this setattr.
    monkeypatch.setattr(mod, "_required_dim_for_graph", fake_resolver,
                        raising=False)

    rc = mod.main(["--all-tenants", "--uri", "docker://dummy"])
    assert rc == 0
    out = capsys.readouterr().out
    assert asked == [tenant_graph], asked
    assert "0 scanned, 0 embedded, 1 skipped" in out, out
    assert _embedding(tenant, "Point", "id", "p1") is None
