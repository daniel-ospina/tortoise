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
    FALKORDB_HOST:PORT (default localhost:16379). Embedded CI has no
    container — probe before connecting so the suite skips instead of
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
        # Document exists with fields (discover by sessionId — doc_id may differ)
        rows = proj.g.query(
            "MATCH (d:Document) WHERE d.sessionId = 's1' "
            "RETURN d.topics, d.summary, d.eventId"
        ).result_set
        assert rows, "Document not created"
        assert rows[0][0] == ["licensing", "AGPL"], rows[0][0]
        assert rows[0][1] == "Compared"
        # sessionCaptured Event + produces→Document + uses→Skill
        ev = proj.g.query(
            "MATCH (e:Event {eventKind:'sessionCaptured'})-[:produces]->(d:Document) "
            "WHERE d.sessionId = 's1' RETURN count(e)"
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
def test_capture_defaults_doc_status_captured():
    """#133 P0: --capture-metadata with NO doc_status in frontmatter
    must default the Document to doc_status='captured' (not 'draft')."""
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
            "MATCH (d:Document) WHERE d.title = 'CapDefault' "
            "RETURN d.doc_status"
        ).result_set
        assert rows, "Document not created"
        assert rows[0][0] == "captured", f"expected captured, got {rows[0][0]!r}"
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
        "---\ntitle: NeedsExtract\ntopics: a\ndoc_status: captured\n"
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
            "MATCH (d:Document) WHERE d.title = 'NeedsExtract' "
            "RETURN d.needs_extraction"
        ).result_set
        assert rows and rows[0][0] is True, f"needs_extraction not stored: {rows}"
    finally:
        proj.close()


@_live_db
def test_upgrade_on_already_extracted_is_noop():
    """#133: --upgrade on a Document already doc_status='extracted' → no-op
    'doc already extracted, skipped' (idempotency)."""
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
    # The Document id MUST equal the file path so _do_upgrade finds it and
    # exercises the "already extracted → skip" path (review P1).
    t = _tmp("already.md")
    Path(t).write_text("---\ntitle: X\n---\n\n## User\nhi\n", encoding="utf-8")
    # Real convention: Document id = filename (args.transcript.name), sourcePath = full path
    doc_id = Path(t).name
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(uri)
    try:
        proj.g.query(
            "CREATE (d:Document {id:$id, title:'X', doc_status:'extracted', sourcePath:$sp})",
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
            "MATCH (d:Document {id:$id}) RETURN d.doc_status",
            params={"id": doc_id},
        ).result_set
        assert rows[0][0] == "extracted", rows[0][0]
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
        "---\ntitle: A\ndoc_status: captured\n---\n\n## User\nhi\n",
        encoding="utf-8")
    proj = FalkorProjection(db)
    try:
        proj.g.query(
            "CREATE (d:Document {id:$id, title:'A', doc_status:'captured', "
            "sourcePath:$sp})",
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
            "MATCH (d:Document) WHERE d.sourcePath = $sp RETURN d.doc_status",
            params={"sp": str(real_file)},
        ).result_set
        assert rows and rows[0][0] == "extracted", f"expected extracted, got {rows}"
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
    # Tenant-crafted Document pointing at a path outside the base
    proj = FalkorProjection(db)
    try:
        proj.g.query(
            "CREATE (d:Document {id:'doc-evil', title:'Evil', "
            "doc_status:'captured', sourcePath:$sp})",
            params={"sp": "/etc/passwd"},
        )
    finally:
        proj.close()
    args = ["ingest", "--db", db, "--log", log, "--upgrade-all",
            "--point-model", "mock:cheap", "--relation-model", "mock:reason"]
    with patch("sys.argv", args):
        _run_main(None)
    # Doc NOT extracted — the file was never read
    proj = FalkorProjection(db)
    try:
        rows = proj.g.query(
            "MATCH (d:Document {id:'doc-evil'}) RETURN d.doc_status",
        ).result_set
        assert rows[0][0] == "captured", f"expected captured (fail-closed), got {rows[0][0]}"
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
        "---\ntitle: Real\ndoc_status: captured\n---\n\n## User\nreal content\n",
        encoding="utf-8")
    proj = FalkorProjection(db)
    try:
        proj.g.query(
            "CREATE (d:Document {id:'doc-x', title:'X', doc_status:'captured', "
            "sourcePath:$sp})",
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
            "MATCH (d:Document {id:'doc-x'}) RETURN d.doc_status",
        ).result_set
        assert rows[0][0] == "captured"
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
