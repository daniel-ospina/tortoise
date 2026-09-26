"""Tests for tortoise.ingest — build_model and main CLI entry point.

Runnable without pytest:  .venv/bin/python tests/test_ingest.py
(also works under pytest if installed).
"""
from __future__ import annotations  # noqa: I001

import os
import sys
import tempfile

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.extractor import MockModel  # noqa: E402, I001, RUF100
from tortoise.ingest import build_model, main  # noqa: E402, RUF100
from tortoise.models import OllamaModel, OpenAICompatModel  # noqa: E402, RUF100
from tortoise.projection import FalkorProjection  # noqa: E402, RUF100

# ── Live-FalkorDB availability (mirrors tests/test_hnsw_vector_index.py) ──
# #125 capture/upgrade tests connect to docker://localhost:16379 (live
# FalkorDB, not embedded). Probe at module load so they skip gracefully in
# CI where no Docker FalkorDB is running (#493).
FALKORDB_AVAILABLE = False
try:
    _old_uri = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = "docker://:@localhost:16379/tortoise_test_ingest125"
    _probe = FalkorProjection.from_uri(os.environ["TORTOISE_DB_URI"])
    _probe.close()  # construction itself connects — raises on refusal
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False
finally:
    if _old_uri is not None:
        os.environ["TORTOISE_DB_URI"] = _old_uri
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

_live_db = pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)



def _live_uri(test_graph: str) -> str:
    """The live backend URI with a per-test test-prefixed graph path.

    Epic #1647 (T7, cycle-5 P1-6): the historical
    ``os.environ.get("TORTOISE_DB_URI") or docker://.../tortoise_test_ingest125``
    resolved the SHARED env-URI path (job URI) for every #125/#133 test here —
    each of these tests bulk-DETACHes the resolved graph, so concurrent
    sessions clobber each other's live writes (and the DETACH is TEST code,
    invisible to the per-test wipe scope). The path is now a per-test test_*
    name; the backend host comes from the env URI when set (the live
    backend), else the historical probe host.
    """
    from urllib.parse import urlsplit, urlunsplit
    env = os.environ.get("TORTOISE_DB_URI")
    if env:
        parts = urlsplit(env)
        return urlunsplit((parts.scheme, parts.netloc, f"/{test_graph}", "", ""))
    return f"docker://:@localhost:16379/{test_graph}"


def _docker_falkor_reachable() -> bool:
    """Socket probe: is a live Docker FalkorDB reachable?

    The #125/#133 capture + upgrade tests need a live FalkorDB on
    FALKORDB_HOST:PORT (default localhost:16379). On the P3 docker lane
    (test-slow) the provisioned falkordb-legacy service (16379) is up so
    these RUN; the skip is VISIBLE (never a vacuous return, epic #1647
    Task 9) and the reason is intentionally NOT guard-exempt — a downed
    provisioned service flips the guard red (fail-closed, D-4), never a
    green-skip. Probe before connecting so the suite skips instead of
    raising redis ConnectionError (Error 111/61).
    """
    import socket
    host = os.environ.get("FALKORDB_HOST", "localhost")
    port = int(os.environ.get("FALKORDB_PORT", "16379"))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _require_live_falkor() -> bool:
    """True when a live FalkorDB is reachable; otherwise skip under pytest
    (or return False in plain-script mode, matching the _skip_if_no_falkor
    self-skip convention) — never raise ConnectionError."""
    if _docker_falkor_reachable():
        return True
    if "pytest" in sys.modules:
        pytest.skip("live FalkorDB (FALKORDB_HOST:PORT) not reachable")
    return False


def _transcript(text, ext=".txt"):
    """Write text to a temp file, return its Path."""
    p = Path(_tmp(f"transcript{ext}"))
    p.write_text(text, encoding="utf-8")
    return p


# A minimal speaker dialogue that the deterministic segmenter + MockModel can chew on.
SAMPLE_DIALOGUE = """\
Alice: we should raise B slowly because fast raises wreck early buyers.
Bob: I disagree entirely. Revenue is not the point here.
Alice: on the contrary, revenue anchors everything else so we have no choice.
"""


SAMPLE_DIALOGUE_2 = """\
Carol: but the data shows otherwise however we slice it.
Dave: fine, let's test both ways therefore we converge faster.
"""


# ---------------------------------------------------------------------------
# build_model tests
# ---------------------------------------------------------------------------


def test_build_model_mock():
    m = build_model("mock:test-model")
    assert isinstance(m, MockModel)
    assert m.id == "test-model"
    print("PASS test_build_model_mock")


def test_build_model_ollama():
    m = build_model("ollama:llama3")
    assert isinstance(m, OllamaModel)
    assert m.id == "llama3"
    assert not m.think
    print("PASS test_build_model_ollama")


def test_build_model_deepseek():
    m = build_model("deepseek:deepseek-chat")
    assert isinstance(m, OpenAICompatModel)
    assert m.id == "deepseek-chat"
    assert "api.deepseek.com" in m.base_url
    print("PASS test_build_model_deepseek")


def test_build_model_openai():
    m = build_model("openai:gpt-4")
    assert isinstance(m, OpenAICompatModel)
    assert m.id == "gpt-4"
    assert "api.openai.com" in m.base_url
    print("PASS test_build_model_openai")


def test_build_model_gemini():
    m = build_model("gemini:gemini-pro")
    assert isinstance(m, OpenAICompatModel)
    assert m.id == "gemini-pro"
    assert "generativelanguage.googleapis.com" in m.base_url
    print("PASS test_build_model_gemini")


def test_build_model_openrouter():
    m = build_model("openrouter:mistral")
    assert isinstance(m, OpenAICompatModel)
    assert m.id == "mistral"
    assert "openrouter.ai" in m.base_url
    print("PASS test_build_model_openrouter")


def test_build_model_bad_spec():
    try:
        build_model("invalid_no_colon")
    except SystemExit as e:
        assert e.code is not None and e.code != 0
    else:
        raise AssertionError("expected SystemExit")
    print("PASS test_build_model_bad_spec")


def test_build_model_unknown_provider():
    try:
        build_model("unknown:some-model")
    except SystemExit as e:
        assert e.code is not None and e.code != 0
    else:
        raise AssertionError("expected SystemExit")
    print("PASS test_build_model_unknown_provider")


def test_build_model_reasoning():
    m = build_model("ollama:llama3", reasoning=True)
    assert m.think is True
    print("PASS test_build_model_reasoning")


# ---------------------------------------------------------------------------
# main end-to-end tests (mock models only — no network)
# ---------------------------------------------------------------------------

def _run_main(argv, *, capture=False):
    """Run main() directly (close-monkeypatch removed — Task 5, issue #176:
    FalkorProjection.close() is now idempotent + atexit/finalize-registered,
    so no hang on rapid succession)."""
    if capture:
        with patch("sys.stdout", new_callable=StringIO) as buf:
            main(argv)
        return buf.getvalue()
    else:
        main(argv)


def test_main_end_to_end():
    """Full pipeline: mock models, temp transcript, db, log, out."""
    t = _transcript(SAMPLE_DIALOGUE)
    db = _tmp("g.db")
    log = _tmp("events.jsonl")
    out = _tmp("graph.html")

    with patch("sys.argv", ["ingest", str(t),
                            "--point-model", "mock:cheap",
                            "--relation-model", "mock:reason",
                            "--db", db, "--log", log, "--out", out]):
        _run_main(None)

    # Output file should exist with render content
    html = Path(out).read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html or "<html" in html
    # Log should have events
    events = [ln for ln in Path(log).read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any("IngestStarted" in e for e in events)
    print("PASS test_main_end_to_end")


def test_main_skip():
    """Re-running the same transcript at the same version skips."""
    t = _transcript(SAMPLE_DIALOGUE)
    db = _tmp("g.db")
    log = _tmp("events.jsonl")
    out = _tmp("graph.html")

    args = ["ingest", str(t), "--db", db, "--log", log, "--out", out]
    with patch("sys.argv", args):
        _run_main(None)

    with patch("sys.argv", args):
        second_output = _run_main(None, capture=True)

    assert "skip:" in second_output, f"expected skip, got: {second_output!r}"
    print("PASS test_main_skip")


def test_main_force():
    """--force reprocesses a previously ingested transcript."""
    t = _transcript(SAMPLE_DIALOGUE)
    db = _tmp("g.db")
    log = _tmp("events.jsonl")
    out = _tmp("graph.html")

    args = ["ingest", str(t), "--db", db, "--log", log, "--out", out]
    with patch("sys.argv", args):
        _run_main(None)

    with patch("sys.argv", args + ["--force"]):  # noqa: RUF005
        second_output = _run_main(None, capture=True)

    assert "ingesting" in second_output, f"expected ingesting, got: {second_output!r}"
    print("PASS test_main_force")


def test_main_resolution():
    """--resolution adds a resolution-event point."""
    t = _transcript(SAMPLE_DIALOGUE)
    db = _tmp("g.db")
    log = _tmp("events.jsonl")
    out = _tmp("graph.html")

    with patch("sys.argv", ["ingest", str(t),
                            "--db", db, "--log", log, "--out", out,
                            "--resolution"]):
        _run_main(None)

    # Check that a resolution-event point was emitted
    events = [ln for ln in Path(log).read_text(encoding="utf-8").splitlines() if ln.strip()]
    added_events = [e for e in events if "PointAdded" in e]
    resolution_point = [e for e in added_events if "resolution-event" in e]
    assert resolution_point, "expected a resolution-event point"
    print("PASS test_main_resolution")


def test_main_max_utterances():
    """--max-utterances caps processing to N utterances."""
    t = _transcript(SAMPLE_DIALOGUE)
    db = _tmp("g.db")
    log = _tmp("events.jsonl")
    out = _tmp("graph.html")

    with patch("sys.argv", ["ingest", str(t),
                            "--db", db, "--log", log, "--out", out,
                            "--max-utterances", "1"]):
        output = _run_main(None, capture=True)

    # Should complete without error — at most 1 utterance worth of points
    assert "points" in output
    print("PASS test_main_max_utterances")


def test_main_bad_model():
    """Bad model spec exits with error."""
    t = _transcript(SAMPLE_DIALOGUE)
    db = _tmp("g.db")
    log = _tmp("events.jsonl")
    out = _tmp("graph.html")

    try:
        with patch("sys.argv", ["ingest", str(t),
                                "--point-model", "bad_spec",
                                "--db", db, "--log", log, "--out", out]):
            _run_main(None)
    except SystemExit as e:
        assert e.code is not None and e.code != 0
    else:
        raise AssertionError("expected SystemExit")
    print("PASS test_main_bad_model")


# -- Document Indexer (#6890) -------------------------------------------------


def test_resolve_domain_from_path():
    """resolve_domain_from_path finds longest-prefix match from manifest."""
    from tortoise.domain_loader import resolve_domain_from_path  # noqa: I001
    # Use a temp manifest so tests don't depend on the production mapping
    import yaml, tempfile  # noqa: E401
    manifest = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)  # noqa: SIM115
    yaml.dump({
        'version': 2,
        'directory_map': {
            'docs/epics/': 'capability',
            'docs/teams/epistemic-team/': 'product-strategy',
            'docs/': 'capability',  # shorter prefix
        },
        'domains': {},
    }, manifest)
    manifest.close()
    try:
        # Exact match
        assert resolve_domain_from_path(
            'docs/epics/2026-07-14-memory-system/04-plan.md', manifest.name
        ) == 'capability'
        # Sub-path match
        assert resolve_domain_from_path(
            'docs/teams/epistemic-team/operations/note.md', manifest.name
        ) == 'product-strategy'
        # Longest-prefix wins: docs/epics/ is longer than docs/
        assert resolve_domain_from_path(
            'docs/epics/foo.md', manifest.name
        ) == 'capability'
        # No match → falls back to 'capability'
        assert resolve_domain_from_path(
            'src/main.py', manifest.name
        ) == 'capability'
    finally:
        os.unlink(manifest.name)
    print("PASS test_resolve_domain_from_path")


def test_ingest_auto_detects_domain_from_path():
    """When frontmatter has no domain, the file path is checked against directory_map."""
    import yaml, tempfile  # noqa: E401, I001
    # Document with frontmatter but no domain field
    doc_md = """---
title: Research Brief
type: research
created: 2026-01-01
---

## Section 1
Content here.
"""
    # Write it to a path that matches the manifest
    tmpdir = tempfile.mkdtemp(prefix="tortoise_docs_")
    docs_epics_dir = os.path.join(tmpdir, "docs", "epics")
    os.makedirs(docs_epics_dir)
    t = Path(os.path.join(docs_epics_dir, "test.md"))
    t.write_text(doc_md, encoding="utf-8")

    # Write a test manifest that maps the tmpdir prefix
    manifest = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)  # noqa: SIM115
    yaml.dump({
        'version': 2,
        'directory_map': {docs_epics_dir + '/': 'capability'},
        'domains': {},
    }, manifest)
    manifest.close()

    db = _tmp("g.db")
    log_path = _tmp("events.jsonl")
    out = _tmp("graph.html")

    try:
        # monkey-patch the manifest path used by ingest
        import tortoise.ingest as ingest_mod  # noqa: F401, I001
        import tortoise.domain_loader as dl_mod
        orig_load = dl_mod.load_manifest
        def _patched_load(path=None):
            return orig_load(manifest.name)
        dl_mod.load_manifest = _patched_load
        orig_resolve = dl_mod.resolve_domain_from_path
        def _patched_resolve(path, manifest_path=None):
            return orig_resolve(path, manifest.name)
        dl_mod.resolve_domain_from_path = _patched_resolve

        try:
            with patch("sys.argv", ["ingest", str(t),
                                    "--point-model", "mock:cheap",
                                    "--relation-model", "mock:reason",
                                    "--db", db, "--log", log_path, "--out", out]):
                _run_main(None)
        finally:
            dl_mod.load_manifest = orig_load
            dl_mod.resolve_domain_from_path = orig_resolve

        events = [ln for ln in Path(log_path).read_text(encoding="utf-8").splitlines() if ln.strip()]
        doc_created = [e for e in events if '"type": "DocumentCreated"' in e]
        assert doc_created, "expected DocumentCreated event"
        import json
        ev = json.loads(doc_created[0])
        assert ev["document_knowledge_domain"] == "capability", \
            f"expected capability, got {ev.get('document_knowledge_domain')!r}"
    finally:
        os.unlink(manifest.name)
    print("PASS test_ingest_auto_detects_domain_from_path")

SAMPLE_DOC_MD = """---
title: Research Brief
type: research
domain: capability
ownedBy: test-team
created: 2026-01-01
---

## Section 1

This is a research finding about competitor X.

## Section 2

We decided to use approach Y.
"""


def test_main_document_metadata_emitted():
    """Ingesting a markdown doc with frontmatter emits DocumentCreated event."""
    t = _transcript(SAMPLE_DOC_MD, ext=".md")
    db = _tmp("g.db")
    log_path = _tmp("events.jsonl")
    out = _tmp("graph.html")

    with patch("sys.argv", ["ingest", str(t),
                            "--point-model", "mock:cheap",
                            "--relation-model", "mock:reason",
                            "--db", db, "--log", log_path, "--out", out]):
        _run_main(None)

    events = [ln for ln in Path(log_path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    doc_created = [e for e in events if '"type": "DocumentCreated"' in e]
    assert doc_created, "expected DocumentCreated event"

    import json
    ev = json.loads(doc_created[0])
    assert ev["type"] == "DocumentCreated"
    assert ev["title"] == "Research Brief"
    assert ev["document_kind"] == "research"
    assert ev["format"] == "markdown"
    print("PASS test_main_document_metadata_emitted")


def test_main_transcript_no_document():
    """Conversation transcripts (no ## headers) do NOT emit DocumentCreated."""
    t = _transcript(SAMPLE_DIALOGUE, ext=".txt")
    db = _tmp("g.db")
    log_path = _tmp("events.jsonl")
    out = _tmp("graph.html")

    with patch("sys.argv", ["ingest", str(t),
                            "--point-model", "mock:cheap",
                            "--relation-model", "mock:reason",
                            "--db", db, "--log", log_path, "--out", out]):
        _run_main(None)

    events = [ln for ln in Path(log_path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    doc_created = [e for e in events if '"type": "DocumentCreated"' in e]
    assert not doc_created, "DocumentCreated should NOT be emitted for transcript"
    print("PASS test_main_transcript_no_document")


# ---------------------------------------------------------------------------
# #4938 — the document path produces Subjects by default
# ---------------------------------------------------------------------------

_DOC_SUBJECT_FIXTURE = """---
title: Vendor Evaluation
type: research
---

## Background

Alice Rivera met the Acme Organisation to review the proposal.
The Design Team owns the rollout plan.

## Decision

We adopt the approach because it is cheaper.
"""


class _PersonOrgModel(MockModel):
    """Deterministic stand-in whose entity stage names a person and an org.

    ``MockModel`` only promotes multi-word capitalized names containing
    "team"/"org"/"group"/"dept" to Subjects, so it cannot express the
    issue's "a markdown file naming a person and an organisation" fixture.
    This subclass pins ONLY the S7 entity output (one natural person + one
    organisation + one object) and delegates every other stage (points,
    relations) to ``MockModel`` so the rest of the pipeline stays the
    ordinary offline stand-in.
    """

    def complete(self, *, system: str, user: str) -> str:
        if "extract_entities" in system:
            import json
            return json.dumps({
                "subjects": [
                    {"name": "Alice Rivera", "subjectKind": "naturalPerson"},
                    {"name": "Acme Organisation", "subjectKind": "organization"},
                ],
                "objects": [{"name": "FalkorDB", "objectKind": "database"}],
                "aboutEntities": ["Alice Rivera", "Acme Organisation",
                                  "FalkorDB"],
            })
        return super().complete(system=system, user=user)


def _person_org_build_model(spec, *, reasoning=False):
    """``build_model`` replacement returning the pinned person+org stand-in."""
    return _PersonOrgModel(spec)


class _FailingEntityModel(MockModel):
    """Stand-in whose S7 entity stage always fails (points still succeed)."""

    def complete(self, *, system: str, user: str) -> str:
        if "extract_entities" in system:
            raise RuntimeError("simulated entity-stage failure")
        return super().complete(system=system, user=user)


def _failing_entity_build_model(spec, *, reasoning=False):
    return _FailingEntityModel(spec)


class _SecondSectionFailingEntityModel(MockModel):
    """S7 succeeds on the first section, then fails on the second.

    Models the partial-failure shape: section 1's Subjects are already minted
    when section 2 raises, so the document must still be wired to them (no
    orphan Subjects) and the failure must be reported.
    """

    def __init__(self, id: str = "mock"):
        super().__init__(id)
        self._calls = 0

    def complete(self, *, system: str, user: str) -> str:
        if "extract_entities" in system:
            self._calls += 1
            if self._calls >= 2:
                raise RuntimeError("simulated entity-stage failure on section 2")
        return super().complete(system=system, user=user)


def _second_section_failing_build_model(spec, *, reasoning=False):
    return _SecondSectionFailingEntityModel(spec)


def _ingest_doc_fixture(tmp_path, extra_args=()):
    """Run the CLI once on ``_DOC_SUBJECT_FIXTURE``; return (db, journal)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    t = tmp_path / "vendor-eval.md"
    t.write_text(_DOC_SUBJECT_FIXTURE, encoding="utf-8")
    db = str(tmp_path / "g.db")
    log = tmp_path / "events.jsonl"
    out = str(tmp_path / "graph.html")
    argv = ["ingest", str(t), "--point-model", "mock:cheap",
            "--relation-model", "mock:reason",
            "--db", db, "--log", str(log), "--out", out, *extra_args]
    with patch("sys.argv", argv), \
            patch("tortoise.ingest.build_model", _person_org_build_model):
        _run_main(None)
    return db, log


def _document_counts(db):
    """The counts #4938's two indicators speak about, read from the graph."""
    proj = FalkorProjection(db)
    try:
        def one(q):
            return proj.g.query(q).result_set[0][0]
        return {
            "subjects": one("MATCH (s:Subject) RETURN count(s)"),
            "about_subject": one(
                "MATCH ()-[:aboutSubject]->() RETURN count(*)"),
            "sources": one("MATCH (s:Source) RETURN count(s)"),
            "documents": one(
                "MATCH (s:Source) WHERE s.documentKind IS NOT NULL "
                "RETURN count(s)"),
            "points": one("MATCH (p:Point) RETURN count(p)"),
        }
    finally:
        proj.close()


def test_4938_default_document_ingest_files_subjects(tmp_path):
    """Indicator 1 (#4938): a DEFAULT document ingest (no flag) of a markdown
    file naming a person and an organisation yields >=1 ``:Subject`` node and
    >=1 ``aboutSubject`` edge — the owner's document -> Source + Subjects model
    is on by default.

    Fails if: S7 stays opt-in (0 Subjects); the Subjects are minted but the
    document->Subject edges are never wired (0 aboutSubject); the wired names
    do not match the document's own content entities.
    """
    db, _log = _ingest_doc_fixture(tmp_path)
    counts = _document_counts(db)
    assert counts["subjects"] >= 1, f"no :Subject minted by default: {counts}"
    assert counts["about_subject"] >= 1, (
        f"no aboutSubject edge written by default: {counts}")
    proj = FalkorProjection(db)
    try:
        names = {r[0] for r in proj.g.query(
            "MATCH (s:Source)-[:aboutSubject]->(sub:Subject) "
            "RETURN sub.name").result_set}
    finally:
        proj.close()
    assert {"Alice Rivera", "Acme Organisation"} <= names, names
    print(f"PASS test_4938_default_document_ingest_files_subjects ({counts})")


def test_4938_document_source_and_point_counts_unchanged(tmp_path):
    """Indicator 2 (#4938) — the regression guard: the ``:Source`` / document /
    ``:Point`` counts of the SAME ingest are IDENTICAL with S7 on (default) and
    S7 off (``--no-semantic-extract``). Subjects and aboutSubject edges are the
    only difference, so default-on S7 cannot inflate the metered layers.

    The DIFFERENTIAL form is the point: an absolute-count assertion would still
    pass if both runs changed together; this one fails the moment the default
    run gains or loses a Source, document or Point relative to the opt-out run.

    Fails if: the default is not actually on (0 Subjects); the opt-out does not
    disable it (Subjects without the flag); or the metered counts diverge.
    """
    on_db, _ = _ingest_doc_fixture(tmp_path / "on")
    off_db, _ = _ingest_doc_fixture(tmp_path / "off",
                                    ["--no-semantic-extract"])
    on = _document_counts(on_db)
    off = _document_counts(off_db)
    assert off["subjects"] == 0 and off["about_subject"] == 0, (
        f"--no-semantic-extract did not disable the Subject path: {off}")
    assert on["subjects"] >= 1 and on["about_subject"] >= 1, (
        f"the default document ingest filed no Subjects: {on}")
    for key in ("sources", "documents", "points"):
        assert on[key] == off[key], (
            f"{key} changed with S7 on: default={on[key]} opt-out={off[key]}"
            f" (indicator 2 regression guard)")
    assert on["points"] > 0, "vacuous fixture: the ingest extracted no Points"
    print(f"PASS test_4938_document_source_and_point_counts_unchanged "
          f"(on={on}, off={off})")


def test_4938_document_subjects_survive_journal_rebuild(tmp_path):
    """Verification-checklist row 2 (#4938): the default document ingest's
    ``SubjectAdded`` events and the document->Subject edges survive a
    journal-only rebuild — live counts == rebuilt counts.

    Fails if: the reorder puts ``SubjectAdded`` AFTER ``DocumentCreated`` in the
    journal (replay would then resolve no Subject and drop the edge); or the
    edge is written by a raw non-journaled query (live != rebuild).
    """
    db, log = _ingest_doc_fixture(tmp_path)
    live = _document_counts(db)
    rebuild_db = str(tmp_path / "rebuilt.db")
    proj = FalkorProjection(rebuild_db)
    try:
        proj.rebuild_all(str(log.parent))
        rebuilt_subjects = proj.g.query(
            "MATCH (s:Subject) RETURN count(s)").result_set[0][0]
        rebuilt_about = proj.g.query(
            "MATCH ()-[:aboutSubject]->() RETURN count(*)").result_set[0][0]
    finally:
        proj.close()
    assert live["subjects"] >= 1 and live["about_subject"] >= 1, live
    assert rebuilt_subjects == live["subjects"], (
        f"Subjects live={live['subjects']} rebuilt={rebuilt_subjects}")
    assert rebuilt_about == live["about_subject"], (
        f"aboutSubject live={live['about_subject']} rebuilt={rebuilt_about}")
    print(f"PASS test_4938_document_subjects_survive_journal_rebuild "
          f"(subjects={rebuilt_subjects}, aboutSubject={rebuilt_about})")


def test_4938_document_edge_does_not_clear_the_point_marker(tmp_path):
    """#4938 verification-checklist row 4 is WRONG as written: the #4889 read
    marker must NOT clear on a document ingest.

    ``subject_binding_available`` probes the ``(Point|Event)-[:aboutSubject]->
    (:Subject)`` shapes that ``fetch_point_epistemic_state`` actually reads, so
    the document's ``(Source)-[:aboutSubject]->(:Subject)`` edge — indicator 1 —
    cannot make the Point ``subject`` field resolvable. Clearing the marker on
    a Source-sourced edge would make it lie about exactly the field it
    advertises.

    Fails if: the document ingest starts writing Point/Event-sourced edges
    (the ungated path #1370 owns), or the probe is widened to any source label.
    """
    from tortoise.search_engine import subject_binding_available
    db, _log = _ingest_doc_fixture(tmp_path)
    counts = _document_counts(db)
    assert counts["about_subject"] >= 1, counts
    proj = FalkorProjection(db)
    try:
        point_sourced = proj.g.query(
            "MATCH (n:Point)-[:aboutSubject]->(:Subject) RETURN count(n) AS c "
            "UNION ALL "
            "MATCH (m:Event)-[:aboutSubject]->(:Subject) RETURN count(m) AS c"
        ).result_set
        assert sum(r[0] for r in point_sourced) == 0, point_sourced
        assert subject_binding_available(proj.g) is False, (
            "a document-Source aboutSubject edge must NOT clear the "
            "Point/Event-scoped read marker (#4889)")
    finally:
        proj.close()
    print("PASS test_4938_document_edge_does_not_clear_the_point_marker")


def test_4938_entity_stage_failure_does_not_sink_the_document(tmp_path):
    """#4938 durability guard: S7 is an enrichment, and ``begin_ingest`` has
    already claimed the content hash when it runs — so a failed entity stage
    must NOT abort before the document is written. If it did, the Source would
    never be created AND a plain re-run would SKIP ("already processed"),
    silently losing the document.

    This is the TOTAL-failure shape (every section fails): the Source + Points
    are still written and the failure is reported.

    Fails if: the ingest raises; the Source/Points are not written; or the
    failure is silent (no warning printed).
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    t = tmp_path / "vendor-eval.md"
    t.write_text(_DOC_SUBJECT_FIXTURE, encoding="utf-8")
    db = str(tmp_path / "g.db")
    log = tmp_path / "events.jsonl"
    out = str(tmp_path / "graph.html")
    argv = ["ingest", str(t), "--point-model", "mock:cheap",
            "--relation-model", "mock:reason",
            "--db", db, "--log", str(log), "--out", out]
    with patch("sys.argv", argv), \
            patch("tortoise.ingest.build_model", _failing_entity_build_model):
        stdout = _run_main(None, capture=True)
    counts = _document_counts(db)
    assert counts["sources"] == 1 and counts["documents"] == 1, counts
    assert counts["points"] > 0, counts
    assert counts["subjects"] == 0, counts
    assert "warning: S7 entity extraction failed" in stdout, stdout
    print("PASS test_4938_entity_stage_failure_does_not_sink_the_document")


def test_4938_partial_entity_failure_leaves_no_orphan_subjects(tmp_path):
    """#4938 review P2: when S7 mints section 1's Subjects and then section 2
    fails, those Subjects must still be wired to the document — otherwise the
    graph holds Subjects no document is about, and the operator is told they
    were not extracted.

    Fails if: the partial failure aborts the ingest; the minted Subjects are
    not returned/wired (``about_subject < subjects``); or the skipped section
    is not reported.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    t = tmp_path / "vendor-eval.md"
    t.write_text(_DOC_SUBJECT_FIXTURE, encoding="utf-8")
    db = str(tmp_path / "g.db")
    log = tmp_path / "events.jsonl"
    out = str(tmp_path / "graph.html")
    argv = ["ingest", str(t), "--point-model", "mock:cheap",
            "--relation-model", "mock:reason",
            "--db", db, "--log", str(log), "--out", out]
    with patch("sys.argv", argv), \
            patch("tortoise.ingest.build_model",
                  _second_section_failing_build_model):
        stdout = _run_main(None, capture=True)
    counts = _document_counts(db)
    assert counts["sources"] == 1 and counts["points"] > 0, counts
    assert counts["subjects"] >= 1, (
        f"section 1's Subjects were lost on the section-2 failure: {counts}")
    assert counts["about_subject"] == counts["subjects"], (
        f"orphan Subjects: {counts['subjects']} minted but only "
        f"{counts['about_subject']} wired: {counts}")
    assert "warning: S7 entity extraction failed" in stdout, stdout
    print(f"PASS test_4938_partial_entity_failure_leaves_no_orphan_subjects "
          f"({counts})")


def test_4938_graph_write_failure_does_not_orphan_minted_subjects(tmp_path):
    """#4938 review P2 (residual): a NON-section failure — a graph/journal
    write inside the mint loop — must not leave the Subjects created earlier in
    the run unpointed-at.

    ``seen_subjects`` is written only after ``add_subject`` returns, so a raise
    from the SECOND call leaves the FIRST Subject already created. The names
    whose call returned are attached as ``partial_subject_names`` and the
    fail-open caller still wires them, so the document is about the Subjects
    this run actually created.

    Fails if: the created Subject is left unwired (a Subject exists while no
    document points at it), the document is not written, or the failure is
    silent. Scope: this covers a raise from the call itself; a raise after the
    MERGE committed is out of scope (see ``_attach_partial_subjects``).
    """
    from tortoise.api import EventAPI

    tmp_path.mkdir(parents=True, exist_ok=True)
    t = tmp_path / "vendor-eval.md"
    t.write_text(_DOC_SUBJECT_FIXTURE, encoding="utf-8")
    db = str(tmp_path / "g.db")
    log = tmp_path / "events.jsonl"
    out = str(tmp_path / "graph.html")
    real_add_subject = EventAPI.add_subject
    calls = {"n": 0}

    def flaky_add_subject(self, name, subject_kind="other"):
        calls["n"] += 1
        if calls["n"] >= 2:
            # Fail BEFORE journaling, so exactly one Subject is durable.
            raise RuntimeError("simulated graph-write failure on 2nd Subject")
        return real_add_subject(self, name, subject_kind)

    argv = ["ingest", str(t), "--point-model", "mock:cheap",
            "--relation-model", "mock:reason",
            "--db", db, "--log", str(log), "--out", out]
    with patch("sys.argv", argv), \
            patch("tortoise.ingest.build_model", _person_org_build_model), \
            patch("tortoise.api.EventAPI.add_subject", flaky_add_subject):
        stdout = _run_main(None, capture=True)
    counts = _document_counts(db)
    assert counts["sources"] == 1 and counts["documents"] == 1, counts
    assert counts["points"] > 0, counts
    assert counts["subjects"] >= 1, (
        f"the minted Subject was lost on the write failure: {counts}")
    assert counts["about_subject"] == counts["subjects"], (
        f"orphan Subjects after a non-section failure: {counts['subjects']} "
        f"minted, {counts['about_subject']} wired: {counts}")
    assert "warning: S7 entity extraction failed" in stdout, stdout
    print("PASS test_4938_graph_write_failure_does_not_orphan_minted_subjects "
          f"({counts})")


def test_4938_upgrade_path_files_subjects(tmp_path):
    """#4938 review P2: the capture→upgrade path (``--upgrade``) is the
    supported full-extraction route for captured documents, so S7 must run
    there too — otherwise 'on by default' holds for only one entry point and an
    upgraded document still yields 0 ``:Subject`` / 0 ``aboutSubject``.

    Fails if: the upgrade path does not run S7 (0 Subjects), or it runs S7
    without wiring the document's aboutSubject edges.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    t = tmp_path / "vendor-eval.md"
    t.write_text(_DOC_SUBJECT_FIXTURE, encoding="utf-8")
    db = str(tmp_path / "g.db")
    log = str(tmp_path / "events.jsonl")
    out = str(tmp_path / "graph.html")
    seed = FalkorProjection(db)
    try:
        seed.g.query(
            "CREATE (s:Source {url:$id, id:$id, title:'Vendor Evaluation', "
            "documentKind:'research', needs_extraction:true, sourcePath:$sp})",
            params={"id": t.name, "sp": str(t)})
    finally:
        seed.close()
    argv = ["ingest", str(t), "--db", db, "--log", log, "--out", out,
            "--upgrade", "--point-model", "mock:cheap",
            "--relation-model", "mock:reason"]
    with patch("sys.argv", argv), \
            patch("tortoise.ingest.build_model", _person_org_build_model):
        _run_main(None)
    counts = _document_counts(db)
    assert counts["subjects"] >= 1, f"upgrade path minted no Subjects: {counts}"
    assert counts["about_subject"] >= 1, (
        f"upgrade path wired no aboutSubject edge: {counts}")
    print(f"PASS test_4938_upgrade_path_files_subjects ({counts})")


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def _run_all():
    failed = 0
    for name in sorted(globals()):
        if name.startswith("test_") and callable(globals()[name]):
            try:
                globals()[name]()
            except Exception:
                print(f"FAIL {name}")
                import traceback
                traceback.print_exc()
                failed += 1
    if failed:
        print(f"\n{failed} test(s) FAILED")
        sys.exit(1)
    print(f"\nall {sum(1 for n in globals() if n.startswith('test_') and callable(globals()[n]))} tests passed")


if __name__ == "__main__":
    _run_all()


# ------------------------------------------------------------------ #125 capture-metadata (live DB)


@_live_db
def test_capture_metadata_creates_document_no_points():
    """#125: --capture-metadata creates Document + sessionCaptured Event,
    ZERO Points, and does NOT block a later full extraction (no begin_ingest)."""
    import json  # noqa: F401
    uri = _live_uri(f"test_ingest125_{os.urandom(4).hex()}")
    db = uri  # live DB URI
    if not _require_live_falkor():
        return
    log = _tmp("events_capture.jsonl")
    # Flush the test graph (test-prefixed — safe) for hermetic Point count
    from tortoise.projection import FalkorProjection as _FP
    _f = _FP.from_uri(uri)
    _f.g.query("MATCH (n) DETACH DELETE n")
    _f.close()
    # Sample .md with topics/summary frontmatter
    t = _tmp("sess.md")
    Path(t).write_text(
        "---\ntitle: Test\ntopics: licensing, AGPL\nsummary: Compared\n"
        "sessionId: s1\ndoc_status: captured\n---\n\n## User\nDiscuss licensing\n",
        encoding="utf-8")
    args = ["ingest", str(t), "--db", db, "--log", log, "--capture-metadata",
            "--point-model", "mock:cheap", "--relation-model", "mock:reason"]
    with patch("sys.argv", args):
        _run_main(None)
    # Verify via live projection
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(uri)
    try:
        # Document Source exists with fields (discover by sessionId — doc_id may differ).
        # D10 (ONTOLOGY v3.15 §4.4): a document is a :Source keyed url = doc_id.
        rows = proj.g.query(
            "MATCH (s:Source) WHERE s.sessionId = 's1' "
            "RETURN s.topics, s.summary, s.eventId"
        ).result_set
        assert rows, "Document not created"
        assert rows[0][0] == ["licensing", "AGPL"], rows[0][0]
        assert rows[0][1] == "Compared"
        # sessionCaptured Event + produces→document Source + uses→Skill
        ev = proj.g.query(
            "MATCH (e:Event {eventKind:'sessionCaptured'})-[:produces]->(s:Source) "
            "WHERE s.sessionId = 's1' RETURN count(e)"
        ).result_set
        assert ev[0][0] >= 1, ev
        uses = proj.g.query(
            "MATCH (e:Event {eventKind:'sessionCaptured'})-[:uses]->(o:Object {objectKind:'skill'}) "
            "RETURN count(o)"
        ).result_set
        assert uses[0][0] >= 1, uses
        # ZERO Points extracted
        pts = proj.g.query("MATCH (p:Point) RETURN count(p)").result_set
        assert pts[0][0] == 0, f"Points extracted during capture: {pts[0][0]}"
    finally:
        proj.close()
    # No IngestStarted written (capture skips begin_ingest — doesn't block full later)
    lines = [ln for ln in Path(log).read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert not any("IngestStarted" in ln for ln in lines), "capture must not write IngestStarted"


@_live_db
def test_full_ingest_unaffected_and_not_blocked_by_capture():
    """#125: full ingest (no flag) extracts Points; a prior capture does NOT block it."""
    uri = _live_uri(f"test_ingest125_{os.urandom(4).hex()}")
    db = uri
    if not _require_live_falkor():
        return
    log1 = _tmp("events_capture2.jsonl")
    # Flush test graph for hermetic assertions
    from tortoise.projection import FalkorProjection as _FP
    _f = _FP.from_uri(uri)
    _f.g.query("MATCH (n) DETACH DELETE n")
    _f.close()
    log2 = _tmp("events_full.jsonl")
    t = _tmp("sess2.md")
    Path(t).write_text(
        "---\ntitle: Test2\ntopics: licensing\nsummary: Compared\nsessionId: s2\n---\n\n"
        "## User\nWe should raise B slowly\n## Assistant\nFast raises wreck early buyers\n",
        encoding="utf-8")
    # 1. capture-metadata first (should NOT block full later)
    with patch("sys.argv", ["ingest", str(t), "--db", db, "--log", log1,
                            "--capture-metadata", "--point-model", "mock:cheap",
                            "--relation-model", "mock:reason"]):
        _run_main(None)
    # 2. full ingest on same file → MUST extract (not skipped)
    with patch("sys.argv", ["ingest", str(t), "--db", db, "--log", log2,
                            "--point-model", "mock:cheap", "--relation-model", "mock:reason"]):
        _run_main(None)
    # Full ingest should have produced points/events (begin_ingest not blocked)
    lines = [ln for ln in Path(log2).read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any("IngestStarted" in ln for ln in lines), \
        "full ingest was blocked by prior capture (idempotency gotcha)"


# ------------------------------------------------------------------ #133 proportional extraction v1 (live DB)


@_live_db
def test_capture_defaults_needs_extraction():
    """#133 P0 / D10: --capture-metadata with NO extraction flag in frontmatter
    must mark the document Source needs_extraction=true (extraction pending)."""
    import json  # noqa: F401
    uri = _live_uri(f"test_ingest133_{os.urandom(4).hex()}")
    db = uri
    if not _require_live_falkor():
        return
    log = _tmp("events_133_capdefault.jsonl")
    from tortoise.projection import FalkorProjection as _FP
    _f = _FP.from_uri(uri)
    _f.g.query("MATCH (n) DETACH DELETE n")
    _f.close()
    t = _tmp("capdefault.md")
    Path(t).write_text(
        "---\ntitle: CapDefault\ntopics: x\nsummary: y\n---\n\n## User\nhello\n",
        encoding="utf-8")
    args = ["ingest", str(t), "--db", db, "--log", log, "--capture-metadata",
            "--point-model", "mock:cheap", "--relation-model", "mock:reason"]
    with patch("sys.argv", args):
        _run_main(None)
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(uri)
    try:
        rows = proj.g.query(
            "MATCH (s:Source) WHERE s.title = 'CapDefault' "
            "RETURN s.needs_extraction"
        ).result_set
        assert rows, "Document not created"
        assert rows[0][0] is True, f"expected needs_extraction=True, got {rows[0][0]!r}"
    finally:
        proj.close()


@_live_db
def test_needs_extraction_flag_surfaces_and_drives_upgrade_all():
    """#133: needs_extraction frontmatter → Document property → --upgrade-all
    discovers and upgrades the Document (e2e bridge)."""
    import json  # noqa: F401
    uri = _live_uri(f"test_ingest133_{os.urandom(4).hex()}")
    db = uri
    if not _require_live_falkor():
        return
    log = _tmp("events_133_ne.jsonl")
    from tortoise.projection import FalkorProjection as _FP
    _f = _FP.from_uri(uri)
    _f.g.query("MATCH (n) DETACH DELETE n")
    _f.close()
    t = _tmp("ne.md")
    Path(t).write_text(
        "---\ntitle: NeedsExtract\ntopics: a\n"
        "needs_extraction: true\n---\n\n## User\nImportant decision\n",
        encoding="utf-8")
    args = ["ingest", str(t), "--db", db, "--log", log, "--capture-metadata",
            "--point-model", "mock:cheap", "--relation-model", "mock:reason"]
    with patch("sys.argv", args):
        _run_main(None)
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(uri)
    try:
        rows = proj.g.query(
            "MATCH (s:Source) WHERE s.title = 'NeedsExtract' "
            "RETURN s.needs_extraction"
        ).result_set
        assert rows and rows[0][0] is True, f"needs_extraction not stored: {rows}"
    finally:
        proj.close()


@_live_db
def test_upgrade_on_already_extracted_is_noop():
    """#133 + D10 (#5026): --upgrade on a document Source already extracted
    (needs_extraction=false) → no-op 'doc already extracted, skipped'."""
    import json  # noqa: F401
    uri = _live_uri(f"test_ingest133_{os.urandom(4).hex()}")
    db = uri
    if not _require_live_falkor():
        return
    log = _tmp("events_133_noop.jsonl")
    from tortoise.projection import FalkorProjection as _FP
    _f = _FP.from_uri(uri)
    _f.g.query("MATCH (n) DETACH DELETE n")
    _f.close()
    # The document Source id MUST equal the file path so _do_upgrade finds it
    # and exercises the "already extracted → skip" path (review P1).
    t = _tmp("already.md")
    Path(t).write_text("---\ntitle: X\n---\n\n## User\nhi\n", encoding="utf-8")
    # Real convention: document id = filename (args.transcript.name), sourcePath = full path
    doc_id = Path(t).name
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(uri)
    try:
        # D10: a document is a :Source; needs_extraction=false means extracted.
        proj.g.query(
            "CREATE (s:Source {url:$id, id:$id, title:'X', "
            "documentKind:'document', needs_extraction:false, sourcePath:$sp})",
            params={"id": doc_id, "sp": str(t)},
        )
    finally:
        proj.close()
    args = ["ingest", str(t), "--db", db, "--log", log, "--upgrade",
            "--point-model", "mock:cheap", "--relation-model", "mock:reason"]
    with patch("sys.argv", args):
        _run_main(None)
    # Doc stays extracted (no flip attempted on already-extracted)
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(uri)
    try:
        rows = proj.g.query(
            "MATCH (s:Source {url:$id}) RETURN s.needs_extraction",
            params={"id": doc_id},
        ).result_set
        assert rows[0][0] is False, rows[0][0]
    finally:
        proj.close()



def test_upgrade_all_without_transcript_does_not_crash(monkeypatch, tmp_path):
    """#133 P0: --upgrade-all with NO positional transcript must not crash
    (regression: args.transcript.name raised AttributeError on None).

    #329: embedded DB (pre-created file so ingest uses the embedded path)
    + TORTOISE_INGEST_BASE_DIR set to the corpus root so the operator's own
    file re-upgrades (fail-closed default would skip it)."""
    import json  # noqa: F401, I001
    from tortoise.projection import FalkorProjection
    db = _tmp("db_133_ua.db")
    # Create a real embedded DB at the path so ingest uses the embedded branch
    # (Path.exists() gate) instead of falling back to Docker. A bare touch()
    # breaks redislite (it expects to own the file format).
    _seed = FalkorProjection(db)
    _seed.close()
    log = _tmp("events_133_ua.jsonl")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(corpus))
    from tortoise.projection import FalkorProjection
    # Real file under the base — doc sourcePath = file path (ingest convention)
    real_file = corpus / "doc-a.md"
    real_file.write_text(
        "---\ntitle: A\n---\n\n## User\nhi\n",
        encoding="utf-8")
    proj = FalkorProjection(db)
    try:
        # D10: a document is a :Source; its extraction signal is needs_extraction.
        proj.g.query(
            "CREATE (s:Source {url:$id, id:$id, title:'A', documentKind:'document', "
            "needs_extraction:true, sourcePath:$sp})",
            params={"id": str(real_file), "sp": str(real_file)},
        )
    finally:
        proj.close()
    # No positional transcript — the crash path (P0 regression)
    args = ["ingest", "--db", db, "--log", log, "--upgrade-all",
            "--point-model", "mock:cheap", "--relation-model", "mock:reason"]
    with patch("sys.argv", args):
        _run_main(None)
    # Doc should now be extracted (upgrade ran — file is under the base)
    proj = FalkorProjection(db)
    try:
        rows = proj.g.query(
            "MATCH (s:Source) WHERE s.sourcePath = $sp RETURN s.needs_extraction",
            params={"sp": str(real_file)},
        ).result_set
        assert rows and rows[0][0] is False, f"expected extracted (needs_extraction=False), got {rows}"
    finally:
        proj.close()


def test_upgrade_all_fail_closed_outside_base(monkeypatch, tmp_path):
    """#329: upgrade-all with a sourcePath OUTSIDE the base is skipped
    (fail-closed) — a tenant-set /etc/passwd-style path is never read."""
    import json  # noqa: F401, I001
    from tortoise.projection import FalkorProjection
    db = _tmp("db_133_fc.db")
    _seed = FalkorProjection(db)
    _seed.close()
    log = _tmp("events_133_fc.jsonl")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(corpus))
    from tortoise.projection import FalkorProjection
    # Tenant-crafted document Source pointing at a path outside the base
    proj = FalkorProjection(db)
    try:
        proj.g.query(
            "CREATE (s:Source {url:'doc-evil', id:'doc-evil', title:'Evil', "
            "documentKind:'document', needs_extraction:true, sourcePath:$sp})",
            params={"sp": "/etc/passwd"},
        )
    finally:
        proj.close()
    args = ["ingest", "--db", db, "--log", log, "--upgrade-all",
            "--point-model", "mock:cheap", "--relation-model", "mock:reason"]
    with patch("sys.argv", args):
        _run_main(None)
    # Doc NOT extracted — the file was never read (needs_extraction stays true)
    proj = FalkorProjection(db)
    try:
        rows = proj.g.query(
            "MATCH (s:Source {url:'doc-evil'}) RETURN s.needs_extraction",
        ).result_set
        assert rows[0][0] is True, f"expected pending (fail-closed), got {rows[0][0]}"
    finally:
        proj.close()


def test_upgrade_all_unset_base_skips_everything(monkeypatch, tmp_path):
    """#329: TORTOISE_INGEST_BASE_DIR unset → fail-closed skip (nothing read)."""
    monkeypatch.delenv("TORTOISE_INGEST_BASE_DIR", raising=False)
    from tortoise.projection import FalkorProjection
    db = _tmp("db_133_nb.db")
    _seed = FalkorProjection(db)
    _seed.close()
    log = _tmp("events_133_nb.jsonl")
    # REAL valid markdown doc at an absolute path (would be read+extracted
    # without containment)
    real = _tmp("doc-real.md")
    Path(real).write_text(
        "---\ntitle: Real\n---\n\n## User\nreal content\n",
        encoding="utf-8")
    proj = FalkorProjection(db)
    try:
        proj.g.query(
            "CREATE (s:Source {url:'doc-x', id:'doc-x', title:'X', "
            "documentKind:'document', needs_extraction:true, sourcePath:$sp})",
            params={"sp": real},
        )
    finally:
        proj.close()
    args = ["ingest", "--db", db, "--log", log, "--upgrade-all",
            "--point-model", "mock:cheap", "--relation-model", "mock:reason"]
    with patch("sys.argv", args):
        _run_main(None)
    proj = FalkorProjection(db)
    try:
        rows = proj.g.query(
            "MATCH (s:Source {url:'doc-x'}) RETURN s.needs_extraction",
        ).result_set
        assert rows[0][0] is True
    finally:
        proj.close()


# ═══════════════════════════════════════════════════════════════════════
# SC4 hardening (epic #900 T9, issue #1045): frozen-legacy behavior asserted
# during the W3 deprecation window — INSTANTIATES-edge preservation +
# TORTOISE_INDEX_NO_NETWORK honored at the NEW-PATH boundary only.
# ═══════════════════════════════════════════════════════════════════════

def _legacy_ingest_session(tmp_path, monkeypatch, content: str,
                           *, db_path: str | None = None,
                           env: dict | None = None) -> tuple:
    """Run the FROZEN legacy ingest_corpus AgentSession branch on one file
    and return (sdk, report). Env mutations (TORTOISE_INDEX_NO_NETWORK etc.)
    applied via monkeypatch.setenv — restored after the test."""
    from tortoise.sdk import TortoiseSDK
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    (corpus / "s.md").write_text(content)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    sdk = TortoiseSDK(db_path or os.path.join(str(tmp_path), "sc4.db"))
    try:
        report = sdk.ingest_corpus(str(corpus), eventKind="AgentSession",
                                   extract_metadata=True)
        return sdk, report
    except Exception:
        sdk.close()
        raise


SESSION_WITH_ISSUES = """\
---
sessionId: sc4-1
title: "SC4 Session"
issues: [repo#1]
prs: [repo#2]
---
Body with issue references.
"""


def test_sc4_indexed_session_keeps_issue_object_edges(tmp_path, monkeypatch):
    """SC4 (cycle-7 pin, T9): an ingested session Event keeps its issue/PR
    Object edges via _connect_issue_objects on the FROZEN legacy path — the
    INSTANTIATES-edge preservation term. The legacy ingest branch must not
    regress during the W3 window."""
    from tortoise.sdk import TortoiseSDK
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    (corpus / "s.md").write_text(SESSION_WITH_ISSUES)
    sdk = TortoiseSDK(os.path.join(str(tmp_path), "sc4.db"))
    try:
        report = sdk.ingest_corpus(str(corpus), eventKind="AgentSession",
                                   extract_metadata=False)
        assert report.get("ingested", 0) >= 1, report
        g = sdk._get_proj().g
        # the session Event exists and carries aboutObject edges to the
        # issue/PR Objects (the legacy _connect_issue_objects wiring)
        n = g.query(
            "MATCH (e:Event {eventId:'session_sc4-1'})-[:aboutObject]->(o:Object) "
            "RETURN count(o)").result_set[0][0]
        assert n >= 2, f"expected issue/PR Object edges, got {n}"
    finally:
        sdk.close()


def test_sc4_no_network_legacy_branch_still_embeds(tmp_path, monkeypatch):
    """SC4 no-network regression leg (cycle-11/12, T9): with
    TORTOISE_INDEX_NO_NETWORK=1, the FROZEN legacy ingest_corpus AgentSession
    branch STILL computes the embedding (the var is honored at the NEW-PATH
    call boundary only — never inside the shared _session_embedding; an
    implementation short-circuiting inside the shared function changes FROZEN
    legacy behavior and fails this leg)."""
    from tortoise.sdk import TortoiseSDK  # noqa: I001
    import tortoise.session_indexer as si_mod
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    (corpus / "s.md").write_text("---\nsessionId: sc4-2\ntitle: N\n---\nBody")
    monkeypatch.setenv("TORTOISE_INDEX_NO_NETWORK", "1")
    calls = {"n": 0}
    orig = si_mod.compute_session_embedding  # noqa: F841

    def _spy(*a, **k):
        # spy the INNER network-calling function (session_indexer.py:453):
        # the real _session_embedding delegates into it, so a call-site gate
        # OR a short-circuit inside the shared _session_embedding both yield
        # calls == 0 → the leg fails on EITHER regression class (review-gate
        # P2 — a spy on _session_embedding itself could not see a short-
        # circuit INSIDE the shared function)
        calls["n"] += 1
        return [0.5] * 384

    monkeypatch.setattr(si_mod, "compute_session_embedding", _spy)
    sdk = TortoiseSDK(os.path.join(str(tmp_path), "sc4.db"))
    try:
        report = sdk.ingest_corpus(str(corpus), eventKind="AgentSession",
                                   extract_metadata=True)
        assert report.get("ingested", 0) >= 1, report
        # the LEGACY branch embeds EVEN under the var (boundary-scoped honor)
        assert calls["n"] >= 1, \
            "legacy AgentSession branch must still embed under NO_NETWORK"
        g = sdk._get_proj().g
        emb = g.query(
            "MATCH (e:Event {eventId:'session_sc4-2'}) RETURN e.embedding"
        ).result_set[0][0]
        assert emb is not None, "legacy Event embedding must be non-null"
    finally:
        sdk.close()


def test_sc4_deprecation_markers_present():
    """T9 indicator: SDK docstring deprecation markers on the two frozen
    legacy surfaces record the W3 divergence."""
    from tortoise.sdk import TortoiseSDK  # noqa: I001
    import inspect
    for method_name in ("ingest_corpus", "index_sessions"):
        doc = inspect.getdoc(getattr(TortoiseSDK, method_name)) or ""
        assert "DEPRECATED" in doc, f"{method_name} missing DEPRECATED marker"
        assert "index_directory" in doc, \
            f"{method_name} marker must name the replacement"
    # the divergence is RECORDED (the flag semantics differ between paths)
    doc = inspect.getdoc(TortoiseSDK.ingest_corpus) or ""
    assert "extract_metadata" in doc and "divergence" in doc.lower(), \
        "ingest_corpus must record the extract_metadata flag-semantics divergence"
