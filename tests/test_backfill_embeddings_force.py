"""T12 (#1349 PR2) — backfill ``--force-re-embed``.

The bge-small swap (T9) invalidated stored MiniLM vectors: T12's
``--force-re-embed`` re-embeds ALL rows (not just ``embedding IS NULL``) with
index-time-composition text so backfill vectors equal what the live index
path would produce under bge.

Covers (plan §Task 12 acceptance):
- WHERE-predicate flip: NULL-only vs all-rows for all 6 LABEL_CONFIG labels
  (Point/Subject/Object/Document/Event/Source);
- idempotency on a partial re-run (new node lands, existing rows re-embed to
  identical deterministic vectors, no errors);
- composition parity ×3 — Event non-meeting (``subject + eventKind + object``,
  entities.py:484-488), Document (``title + content``, entities.py:363),
  AgentSession-with-summary (``session_embedding_text(name, summary, keywords,
  topics)`` with the summary PARSED from ``content_metadata`` — the pre-T12
  hardcoded ``summary=""`` downgrade is the regression under test);
- meeting handling: purge of legacy #160 meeting embeddings, NULL-eventKind
  inclusion (Cypher NULL != false parity with index-time ``!= "meeting"``),
  post-purge search exclusion (``run_vector_query`` must NOT return purged
  meetings), repair-after-purge no-re-embed;
- per-label completeness marker (6 labels re-embedded, meeting purge count,
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
import tempfile
from pathlib import Path

import pytest

from tortoise import search_engine
from tortoise.projection import FalkorProjection
from tortoise.session_indexer import session_embedding_text

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKFILL = _REPO_ROOT / "graph-scripts" / "backfill_embeddings.py"
_LABELS = ["Point", "Subject", "Object", "Document", "Event", "Source"]


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
    p = FalkorProjection(
        os.path.join(tempfile.mkdtemp(prefix="t12_force_"), "t12.db"),
        graph_name="tortoise",
    )
    yield p
    p.close()


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


def _fixture_id_prop(label: str) -> str:
    return "url" if label == "Source" else ("eventId" if label == "Event" else "id")


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
    """Drive the backfill's internal per-graph runner (NULL-only or force)."""
    return mod._backfill_graph(
        proj.db, proj.graph_name, labels or _LABELS, 0, 500, force=force)


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
        _create(g, "Document", {"id": "d-embedded", "title": "Title A",
                                "content": "Body A"}, emb=[0.1] * 384)
        _create(g, "Document", {"id": "d-missing", "title": "Title B",
                                "content": "Body B"})
        return "Title A Body A", "Title B Body B"
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
        label: _embedding(g, label, _fixture_id_prop(label),
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
        label: _embedding(g, label, _fixture_id_prop(label),
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
    """Document embeds ``title + content`` (entities.py:363), not title-only."""
    g = _g(proj)
    _create(g, "Document", {"id": "d1", "title": "Weekly report",
                            "content": "Findings from the sprint"})
    mod = _load_backfill()
    fake = _FakeEmbed()
    monkeypatch.setattr("tortoise.embeddings.compute_embedding", fake)
    _backfill_run(mod, proj, ["Document"])
    assert fake.calls == ["Weekly report Findings from the sprint"]
    stored = _embedding(g, "Document", "id", "d1")
    assert stored == pytest.approx(fake("Weekly report Findings from the sprint"))


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
        proj.db, proj.graph_name, _LABELS, 0, 500)
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
    mod._force_reembed_graph(proj.db, proj.graph_name, _LABELS, 0, 500)

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
    mod._connect_falkordb = lambda uri: (proj.db, proj.graph_name)
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
    _create(g, "Document", {"id": "d1", "title": "Title", "content": "Body"})
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
    assert _embedding(g, "Document", "id", "d1") is None


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
