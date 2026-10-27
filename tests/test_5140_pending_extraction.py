"""#5140 P1 (finding 3) — the pending-extraction signal's default is inverted.

D10 (#5026) retired ``doc_status`` and left ``needs_extraction`` as the only
stored extraction signal — but it also moved the DEFAULT. Pre-D10, the ABSENCE
of a signal meant *captured (pending)*: ``_do_upgrade`` read
``coalesce(d.doc_status, 'captured')`` and skipped only on ``'extracted'``, and
``_do_upgrade_all`` discovered ``doc_status='captured' OR needs_extraction=true``.
The D10 code half replaced those with ``coalesce(s.needs_extraction, false)``
and ``= true``, so every pre-D10 metadata-only capture — stamped
``doc_status='captured'`` beside the old writer's boilerplate
``needs_extraction=false`` — became invisible to BOTH paths, and ``--upgrade``
reported *"doc already extracted"* for a document that was never extracted.

The seeded shapes below are the production legacy population, not synthetic
edge cases: ``--capture-metadata`` (the #125 capture writer) stamped exactly
``doc_status='captured'``, and the deleted ``graph-scripts/backfill_doc_status.py``
existed to stamp it on nodes that predated the capture path.
"""
from __future__ import annotations  # noqa: I001

import json
import os
import sys
import tempfile

from pathlib import Path
from unittest.mock import patch

import pytest

from tests import _live_utils

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.ingest import main  # noqa: E402, RUF100
from tortoise.projection import FalkorProjection  # noqa: E402, RUF100

# ── Live-FalkorDB availability (probes the backend this file dials) ────────
FALKORDB_AVAILABLE = False
try:
    _probe_uri = os.environ.get("TORTOISE_DB_URI") or _live_utils.legacy_uri(
        "tortoise_test_5140")
    _probe = FalkorProjection.from_uri(_probe_uri)
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False

_live_db = pytest.mark.skipif(not FALKORDB_AVAILABLE,
                              reason="Live FalkorDB (Docker) not available")


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_5140_"), name)


def _live_uri(test_graph: str) -> str:
    """Per-test graph on the live backend (same shape as tests/test_ingest.py)."""
    from urllib.parse import urlsplit, urlunsplit
    env = os.environ.get("TORTOISE_DB_URI")
    if env:
        parts = urlsplit(env)
        return urlunsplit((parts.scheme, parts.netloc, f"/{test_graph}", "", ""))
    return _live_utils.legacy_uri(test_graph)


def _require_live_falkor() -> bool:
    """Probe the backend THIS file will dial (the env URI's own host:port).

    `_live_utils.legacy_port()` resolves the provisioned CI service, which is
    not necessarily the lane's private container (#5084); probing a port we are
    not going to use would skip a run that can actually execute.
    """
    from urllib.parse import urlsplit
    parts = urlsplit(os.environ.get("TORTOISE_DB_URI", ""))
    host = parts.hostname or _live_utils.service_host()
    port = parts.port or _live_utils.legacy_port()
    if _live_utils.tcp_reachable(port, host=host):
        return True
    if "pytest" in sys.modules:
        pytest.skip(f"live FalkorDB ({host}:{port}) not reachable")
    return False


def _reset(uri: str) -> None:
    f = FalkorProjection.from_uri(uri)
    try:
        f.g.query("MATCH (n) DETACH DELETE n")
    finally:
        f.close()


def _read(uri: str, doc_id: str):
    """(needs_extraction, doc_status) for one document Source, as stored."""
    proj = FalkorProjection.from_uri(uri)
    try:
        rows = proj.g.query(
            "MATCH (s:Source {url:$id}) RETURN s.needs_extraction, s.doc_status",
            params={"id": doc_id},
        ).result_set
    finally:
        proj.close()
    assert rows, f"no document Source seeded at {doc_id!r}"
    return rows[0][0], rows[0][1]


def _seed_document(uri: str, doc_id: str, source_path: str,
                   document_kind: str = "research",
                   *, doc_status=None, needs_extraction=None) -> None:
    """Seed a document ``:Source``. Omitted signals are OMITTED, not null —
    Cypher ``CREATE`` cannot express "property absent" through a parameter, so
    the property list is assembled literally (this is the legacy shape)."""
    props = ["url:$id", "id:$id", "title:'Seeded'",
             "documentKind:$dk", "sourceKind:'document'", "sourcePath:$sp"]
    params = {"id": doc_id, "dk": document_kind, "sp": source_path}
    if doc_status is not None:
        props.append("doc_status:$ds")
        params["ds"] = doc_status
    if needs_extraction is not None:
        props.append("needs_extraction:$nx")
        params["nx"] = needs_extraction
    proj = FalkorProjection.from_uri(uri)
    try:
        proj.g.query(
            "CREATE (s:Source {" + ", ".join(props) + "})", params=params)
    finally:
        proj.close()


def _transcript(text: str, name: str) -> Path:
    p = Path(_tmp(name))
    p.write_text(text, encoding="utf-8")
    return p


TEXT = "---\ntitle: Seeded\n---\n\n## User\nImportant decision\n"


def _run(argv, *, capture: bool = False):
    if capture:
        from io import StringIO
        with patch("sys.stdout", new_callable=StringIO) as buf:
            main(argv)
        return buf.getvalue()
    main(argv)
    return ""


# ── the predicate: one rule, two readings ──────────────────────────────────
#
# `_do_upgrade` (one document the operator named) reads `_document_is_pending`;
# the corpus sweep reads its Cypher mirror `_discover_pending_documents`, because
# it asks the question of the whole graph in one query. They are NOT one code
# path — the parity test below (`test_python_rule_and_cypher_twin_agree`) is what
# keeps them arm-for-arm, and a divergence is therefore a red test rather than a
# silent skew. The
# sweep additionally refuses to treat an entirely unmarked document as pending
# unless the operator opts in (`--include-unstamped`) — see
# `test_upgrade_all_discovers_the_legacy_capture_marker` (the transcript-left-
# alone assertion) and `test_upgrade_all_include_unstamped_is_the_backfill_replacement`
# (the unmarked document is NOT selected by the default run) — because the
# sanctioned session-capture writer mints transcript Sources that store no signal
# at all.

def test_pending_predicate_boundaries():
    """The rule, pinned at its boundaries.

    Arm 1 is what makes the *legacy shape* pending: the pre-D10 capture writer
    stored ``doc_status='captured'`` **and** ``needs_extraction=false`` (its
    default), so a rule that let the stored ``false`` win would leave the exact
    backlog this fix exists for invisible. Arm 2 is the matching
    non-over-reach: a completion record must not be re-discovered forever.
    """
    from tortoise.ingest import _document_is_pending

    # A STORED signal alone decides when there is no legacy marker…
    assert _document_is_pending(True, None) is True
    assert _document_is_pending(False, None) is False
    # …but the legacy marker OUTRANKS the pre-D10 boilerplate `false`: the old
    # `--capture-metadata` writer stamped BOTH `doc_status='captured'` and its
    # `needs_extraction=False` default, and that pair IS the backlog.
    assert _document_is_pending(None, "captured") is True
    assert _document_is_pending(False, "captured") is True
    # A completion record outranks a stale `true` (otherwise a sweep would
    # re-discover a document the per-document path always skips).
    assert _document_is_pending(None, "extracted") is False
    assert _document_is_pending(True, "extracted") is False
    # `draft` (the pre-D10 full-ingest default) is NOT a pending claim for the
    # SWEEP (pre-D10 discovery never selected it) …
    assert _document_is_pending(False, "draft") is False
    assert _document_is_pending(None, "draft") is False
    # … but it IS for the explicit command: pre-D10 `--upgrade` skipped on
    # `'extracted'` alone, so a document whose post-extraction flip never ran
    # must still be upgradeable.
    assert _document_is_pending(False, "draft", other_legacy_is_pending=True) is True
    assert _document_is_pending(None, "draft", other_legacy_is_pending=True) is True
    # A stored signal or a completion record still outranks that policy.
    assert _document_is_pending(True, "draft", other_legacy_is_pending=True) is True
    assert _document_is_pending(
        False, "extracted", other_legacy_is_pending=True) is False
    # Unmarked: pending for the explicitly named document, not for the sweep.
    assert _document_is_pending(None, None) is True
    assert _document_is_pending(None, None, unmarked_is_pending=False) is False
    # A truthy NON-boolean is not "explicitly pending": the Cypher twin asks
    # `s.needs_extraction = true`, so `bool("false")` must not answer differently
    # here than the graph does there.
    assert _document_is_pending("false", None) is False
    assert _document_is_pending(1, None) is False


# ── `--upgrade` (single, explicitly named document) ────────────────────────

@_live_db
def test_upgrade_sees_a_legacy_capture_beside_its_boilerplate_false():
    """REPRODUCED at HEAD: ``_do_upgrade`` printed *"doc already extracted,
    skipped"* for a legacy capture that was never extracted.

    Seed the legacy shape (``doc_status='captured'`` beside the pre-D10
    ``needs_extraction=false`` default) and run the CLI ``--upgrade``. Pre-fix
    the command was a no-op and left the node exactly as seeded; the fix
    extracts and clears the signal to ``false``.

    NOTE the ``needs_extraction is False`` assertion below is NOT the
    discriminator — the node was seeded ``False`` — the ``doc_status is None``
    is (``add_document`` has already scrubbed the retired key on this path, so
    what proves the upgrade ran is that it got that far at all).
    """
    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_up_{os.urandom(4).hex()}")
    _reset(uri)
    t = _transcript(TEXT, "legacy_pending.md")
    doc_id = t.name  # `source_id` is the transcript's name (ingest.py)
    # The EXACT pre-D10 `--capture-metadata` shape: `doc_status='captured'`
    # beside its own `needs_extraction=False` default.
    _seed_document(uri, doc_id, str(t),
                   doc_status="captured", needs_extraction=False)

    out = _run([str(t), "--db", uri,
                "--log", _tmp("events.jsonl"), "--out", _tmp("graph.html"),
                "--upgrade", "--point-model", "mock:cheap",
                "--relation-model", "mock:reason"], capture=True)

    assert "doc already extracted, skipped" not in out, (
        f"`--upgrade` reported a legacy capture as already extracted; output:\n{out}")
    nx, ds = _read(uri, doc_id)
    assert nx is False, (
        f"`--upgrade` did not upgrade the legacy capture (needs_extraction={nx!r}, "
        f"doc_status={ds!r}); output was:\n{out}")
    assert ds is None, (
        "the completed upgrade must clear the retired marker, not leave "
        f"doc_status={ds!r} behind — the sweep selects on it, so the document "
        f"would be re-upgraded on every run; output was:\n{out}")


@_live_db
def test_upgrade_extracts_a_document_with_no_signal_at_all():
    """The ``unmarked_is_pending=True`` arm — the pre-D10 default for the
    explicitly named document.

    No ``doc_status`` and no ``needs_extraction`` is the shape EVERY Source
    minted by a writer that sets neither carries (the two transcript writers),
    and for ``--upgrade`` it is the pre-D10 ``coalesce(d.doc_status,
    'captured')`` answer: pending. Pre-fix this path reported "already
    extracted" and the flag stayed absent.
    """
    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_unmarked_{os.urandom(4).hex()}")
    _reset(uri)
    t = _transcript(TEXT, "unmarked.md")
    doc_id = t.name
    _seed_document(uri, doc_id, str(t))  # no doc_status, no needs_extraction
    assert _read(uri, doc_id) == (None, None)

    out = _run([str(t), "--db", uri,
                "--log", _tmp("events.jsonl"), "--out", _tmp("graph.html"),
                "--upgrade", "--point-model", "mock:cheap",
                "--relation-model", "mock:reason"], capture=True)

    nx, _ds = _read(uri, doc_id)
    assert nx is False, (
        f"`--upgrade` did not extract a signal-less document "
        f"(needs_extraction={nx!r}); output was:\n{out}")


@_live_db
def test_upgrade_clears_the_marker_even_when_there_is_nothing_to_extract():
    """Pins the ``REMOVE s.doc_status`` in the completion SET.

    On the ``is_doc`` path ``add_document`` scrubs the retired key itself, so
    that path cannot prove this clause. This one uses content with no ``##``
    sections: no ``add_document`` runs, and only the completion SET can clear the
    marker. The pre-D10 code flipped this property to ``'extracted'`` here, so
    leaving ``'captured'`` behind would re-select the node on every sweep.
    """
    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_nodoc_{os.urandom(4).hex()}")
    _reset(uri)
    t = _transcript("no sections here, just prose.\n", "no_sections.md")
    doc_id = t.name
    _seed_document(uri, doc_id, str(t),
                   doc_status="captured", needs_extraction=False)

    out = _run([str(t), "--db", uri,
                "--log", _tmp("events.jsonl"), "--out", _tmp("graph.html"),
                "--upgrade", "--point-model", "mock:cheap",
                "--relation-model", "mock:reason"], capture=True)

    nx, ds = _read(uri, doc_id)
    assert ds is None, (
        f"the completion SET did not clear the retired marker (doc_status={ds!r}); "
        f"a sweep would re-select this document forever; output was:\n{out}")
    assert nx is False, f"needs_extraction not cleared (got {nx!r}); output:\n{out}"


@_live_db
def test_upgrade_still_skips_an_explicitly_extracted_document():
    """Non-over-reach: a completion record still means extracted.

    The pre-D10 `_do_upgrade` flipped the property to `doc_status='extracted'`
    (and post-D10 the clear removes it) — a node carrying that value must be a
    no-op for BOTH paths, or every sweep would re-extract a finished document.
    """
    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_noop_{os.urandom(4).hex()}")
    _reset(uri)
    t = _transcript(TEXT, "already_done.md")
    doc_id = t.name
    _seed_document(uri, doc_id, str(t),
                   doc_status="extracted", needs_extraction=False)

    out = _run([str(t), "--db", uri,
                "--log", _tmp("events.jsonl"), "--out", _tmp("graph.html"),
                "--upgrade", "--point-model", "mock:cheap",
                "--relation-model", "mock:reason"], capture=True)

    assert "doc already extracted, skipped" in out, out
    nx, _ds = _read(uri, doc_id)
    assert nx is False


# ── `--upgrade-all` (corpus sweep) ─────────────────────────────────────────

@_live_db
def test_upgrade_all_discovers_the_legacy_capture_marker(monkeypatch):
    """REPRODUCED at HEAD: ``--upgrade-all`` reported *"no document Sources found
    with needs_extraction=true"* while a legacy capture sat in the graph.

    Seeds the legacy capture (``doc_status='captured'`` + its boilerplate
    ``needs_extraction=false``) beside a session-capture transcript that stores NO signal at all — the
    sanctioned transcript writer shape. The sweep must pick up the first and
    leave the second alone (that is the no-over-reach half: the deleted backfill
    stamped *every* unstamped node, which would queue every captured session for
    LLM extraction).
    """
    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_ua_{os.urandom(4).hex()}")
    _reset(uri)
    base = tempfile.mkdtemp(prefix="tortoise_5140_corpus_")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)

    legacy = _transcript(TEXT, "legacy_capture.md")
    # keep the corpus under the configured base (#329 fail-closed)
    legacy = Path(base) / legacy.name
    legacy.write_text(TEXT, encoding="utf-8")
    transcript = Path(base) / "session_transcript.md"
    transcript.write_text(TEXT, encoding="utf-8")

    _seed_document(uri, legacy.name, str(legacy),
                   doc_status="captured", needs_extraction=False)
    _seed_document(uri, "session_transcript.md", str(transcript),
                   document_kind="transcript")

    before = _read(uri, "session_transcript.md")
    out = _run(["--db", uri,
                "--log", _tmp("events.jsonl"), "--out", _tmp("graph.html"),
                "--upgrade-all", "--point-model", "mock:cheap",
                "--relation-model", "mock:reason"], capture=True)

    nx, ds = _read(uri, legacy.name)
    assert nx is False, (
        f"`--upgrade-all` missed the legacy capture (needs_extraction={nx!r}, "
        f"doc_status={ds!r}); output was:\n{out}")
    # The seeded `needs_extraction=False` is the legacy boilerplate, so the
    # completion record is the ONLY pre/post discriminator: a sweep that never
    # selected the document leaves the retired marker exactly where it was.
    assert ds is None, (
        f"the legacy capture was never upgraded (doc_status={ds!r}); a seeded "
        f"`needs_extraction=false` cannot tell 'not selected' from 'upgraded'. "
        f"output was:\n{out}")
    # The transcript stores no signal AND carries no legacy marker: untouched.
    assert _read(uri, "session_transcript.md") == before, (
        "the sweep treated an unmarked transcript as awaiting extraction; the "
        "sanctioned session-capture writer stores no signal, so that would queue "
        f"every captured session for LLM extraction; output was:\n{out}")


@_live_db
def test_upgrade_all_include_unstamped_is_the_backfill_replacement(monkeypatch):
    """The deleted ``graph-scripts/backfill_doc_status.py`` made documents that
    carried NO signal at all discoverable (it stamped them). Its replacement is
    the same discoverability as a READ: ``--upgrade-all --include-unstamped``.

    Without the flag the document stays invisible (first run asserts that, so
    the flag is the thing that changes the outcome — not some other fix); with
    it, the sweep upgrades it.
    """
    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_bf_{os.urandom(4).hex()}")
    _reset(uri)
    base = tempfile.mkdtemp(prefix="tortoise_5140_corpus_")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)

    doc = Path(base) / "unstamped.md"
    doc.write_text(TEXT, encoding="utf-8")
    _seed_document(uri, doc.name, str(doc))  # no doc_status, no needs_extraction

    common = ["--db", uri, "--log", _tmp("events.jsonl"),
              "--out", _tmp("graph.html"), "--upgrade-all",
              "--point-model", "mock:cheap", "--relation-model", "mock:reason"]

    plain = _run(list(common), capture=True)
    assert _read(uri, doc.name)[0] is None, (
        f"an unmarked document must stay out of the default sweep; output:\n{plain}")

    out = _run([*common, "--include-unstamped"], capture=True)
    nx, _ds = _read(uri, doc.name)
    assert nx is False, (
        f"`--include-unstamped` did not select the unmarked document "
        f"(needs_extraction={nx!r}); output:\n{out}")


def test_include_unstamped_requires_upgrade_all():
    """The flag is a discovery modifier, not a mode: it cannot do anything
    without ``--upgrade-all``, so it refuses rather than silently no-op."""
    with pytest.raises(SystemExit) as exc:
        main(["--db", _tmp("unused.db"), "--log", _tmp("e.jsonl"),
              "--out", _tmp("g.html"), "--include-unstamped"])
    assert "--include-unstamped requires --upgrade-all" in str(exc.value)


# ── the Python rule and its Cypher twin, pinned against EACH OTHER ─────────
#
# `_discover_pending_documents` claims to mirror `_document_is_pending` "term
# for term". Two readers of one rule drift unless something compares them, and
# the sweep is the only caller of the Cypher half — so a divergence would show
# up as documents silently missing from `--upgrade-all`, the exact defect this
# PR fixes. This test seeds the whole grid and asserts the two answer alike.

_GRID = [
    # (label, needs_extraction, doc_status)
    ("absent/absent", None, None),
    ("absent/captured", None, "captured"),
    ("absent/extracted", None, "extracted"),
    ("absent/draft", None, "draft"),
    ("true/absent", True, None),
    ("true/captured", True, "captured"),
    ("true/extracted", True, "extracted"),
    ("true/draft", True, "draft"),
    ("false/absent", False, None),
    ("false/captured", False, "captured"),
    ("false/extracted", False, "extracted"),
    ("false/draft", False, "draft"),
    # Non-boolean stored values: the Cypher twin asks `= true`, which is false
    # for each of these on FalkorDB (measured), and the Python arm asks `is
    # True`. Pinned so a coercion on either side is a red test, not a silent
    # selection difference.
    ("int-one/absent", 1, None),
    ("str-false/absent", "false", None),
]


@_live_db
@pytest.mark.parametrize("include_unstamped", [False, True])
def test_python_rule_and_cypher_twin_agree(include_unstamped):
    """Same grid through both readings; the selected sets must be identical."""
    from tortoise.ingest import _discover_pending_documents, _document_is_pending

    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_parity_{os.urandom(4).hex()}")
    _reset(uri)
    for label, nx, ds in _GRID:
        _seed_document(uri, f"doc-{label}", f"/corpus/{label}.md",
                       doc_status=ds, needs_extraction=nx)

    proj = FalkorProjection.from_uri(uri)
    try:
        selected = {row[0] for row in _discover_pending_documents(
            proj, include_unstamped=include_unstamped)}
    finally:
        proj.close()

    expected = {f"doc-{label}" for label, nx, ds in _GRID
                if _document_is_pending(nx, ds,
                                        unmarked_is_pending=include_unstamped)}
    assert selected == expected, (
        "the sweep's Cypher predicate and `_document_is_pending` disagree "
        f"(include_unstamped={include_unstamped}).\n"
        f"  only in Cypher: {sorted(selected - expected)}\n"
        f"  only in Python: {sorted(expected - selected)}")


@_live_db
def test_upgrade_still_reaches_a_document_left_at_draft():
    """Pre-D10 parity for the ``other_legacy_is_pending`` arm.

    ``draft`` is the pre-D10 full-ingest DEFAULT, and only the post-extraction
    flip moved it to ``'extracted'`` — so a document still at ``draft`` is one
    whose flip never ran. Pre-D10 ``--upgrade`` skipped on ``'extracted'``
    alone; a rule that treated ``draft`` as done would refuse to upgrade it.
    The SWEEP deliberately does not select it (pre-D10 discovery never did).
    """
    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_draft_{os.urandom(4).hex()}")
    _reset(uri)
    t = _transcript(TEXT, "draft_doc.md")
    doc_id = t.name
    _seed_document(uri, doc_id, str(t),
                   doc_status="draft", needs_extraction=False)

    out = _run([str(t), "--db", uri,
                "--log", _tmp("events.jsonl"), "--out", _tmp("graph.html"),
                "--upgrade", "--point-model", "mock:cheap",
                "--relation-model", "mock:reason"], capture=True)

    assert "doc already extracted, skipped" not in out, (
        f"`--upgrade` refused a document the pre-D10 command upgraded; "
        f"output:\n{out}")
    nx, _ds = _read(uri, doc_id)
    assert nx is False, (
        f"`--upgrade` did not reach the draft document (needs_extraction={nx!r}); "
        f"output:\n{out}")


@_live_db
def test_upgrade_all_not_a_document_bucket_is_stable_across_runs(monkeypatch):
    """The skip bucket must not depend on whether a PREVIOUS run saw the file.

    `begin_ingest` MINTS a claim, so claiming before deciding "does this content
    have sections to extract?" made the second run report *already processed*
    (a different bucket) and let the claim supersede/retract a prior run's
    points for work the sweep then declined to do. Review round 2 measured that;
    this pins the fix: two runs report the SAME bucket, and no claim was minted.
    """
    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_bucket_{os.urandom(4).hex()}")
    _reset(uri)
    base = tempfile.mkdtemp(prefix="tortoise_5140_corpus_")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    doc = Path(base) / "no_sections.md"
    doc.write_text("prose with no headings\n", encoding="utf-8")
    _seed_document(uri, doc.name, str(doc),
                   doc_status="captured", needs_extraction=False)

    argv = ["--db", uri, "--log", _tmp("events.jsonl"),
            "--out", _tmp("graph.html"), "--upgrade-all",
            "--point-model", "mock:cheap", "--relation-model", "mock:reason"]
    first = _run(list(argv), capture=True)
    second = _run(list(argv), capture=True)

    assert "not a Document (no ## headers)" in first, first
    assert "already processed" not in first, (
        f"first run claimed the content before checking for sections:\n{first}")
    assert "not a Document (no ## headers)" in second, (
        "the second run reports a different bucket for the same content — the "
        f"idempotency claim was minted before the section check:\n{second}")
    assert "already processed" not in second, second
    # The document is still pending (the sweep never completed it) …
    assert _read(uri, doc.name) == (False, "captured")
    # … and the first run did not CLAIM the content, which is what the two
    # bucket assertions above prove: a minted claim is exactly what makes the
    # second run say "already processed". (An earlier version of this test also
    # counted nodes whose `content` contains the path — vacuous, because
    # `IngestStarted` has no projection effect at all: `record_projection.py`
    # folds it to a no-op. Review round 3, P2.)


# ── what this sweep CANNOT reach — pinned, so it is not re-claimed ─────────
#
# Review round 3 measured both of these and refused to accept a green suite as
# proof of coverage. They are boundaries of the FIX, not of the bug: reaching
# them is a product decision. Pinning them here means the next lane that
# changes either has to change a test whose docstring says why, instead of
# discovering the gap from a corpus that silently never got upgraded.
#
# ⛔ The FIRST boundary (the un-relabelled `:Document` population) is no longer
# pinned here: it is CLOSED for the foldable part by
# `migrate_kinds.relabel_legacy_documents` (#5140 finding 8), and the before/
# after pair lives in
# `tests/test_5140_d10_relabel_migration.py::
# test_relabel_makes_the_pre_d10_backlog_discoverable` — where a change to it
# moves the test of the finding that owns the behaviour, not a boundary note
# two files away. What remains unreached is narrower and stays a product
# decision: a document `_event_plain_merge` minted without a clock (the relabel
# refuses it as `undated` rather than invent one) and the REBUILT-graph shape
# below.
@_live_db
def test_include_unstamped_does_not_reach_a_replayed_legacy_false(monkeypatch):
    """A REPLAYED pre-D10 record folds a boilerplate ``needs_extraction=false``
    onto a ``:Source`` and scrubs ``doc_status``, so it reads as completed.

    ``--include-unstamped`` widens only the no-property arm, so it does not
    select this shape either. Distinguishing it from a REAL completion needs
    extraction evidence (``contentHash``, #5422 — absent for extractions that
    predate it, and absent for transcripts too), which is a product decision
    recorded on #5140, not a default this command flips.
    """
    from tortoise.ingest import _discover_pending_documents

    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_replayed_{os.urandom(4).hex()}")
    _reset(uri)
    base = tempfile.mkdtemp(prefix="tortoise_5140_corpus_")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    doc = Path(base) / "replayed_legacy.md"
    doc.write_text(TEXT, encoding="utf-8")
    # No doc_status (fold scrubbed it), boilerplate false from the old payload.
    _seed_document(uri, doc.name, str(doc), needs_extraction=False)

    proj = FalkorProjection.from_uri(uri)
    try:
        assert _discover_pending_documents(proj) == []
        assert _discover_pending_documents(proj, include_unstamped=True) == [], (
            "--include-unstamped is documented as selecting documents with NO "
            "needs_extraction PROPERTY; a stored false must not be swept. If "
            "this changes, the evidence-based rule landed and this test (and "
            "the help text) must be updated deliberately")
    finally:
        proj.close()
    # … and the EXPLICIT command does not reach it either: the replayed
    # boilerplate `false` is read as a completion by every reader, including the
    # operator naming the file. That is the second half of the open decision —
    # ignoring a stored `false` here would reverse the D10 expectation pinned by
    # `tests/test_ingest.py::test_upgrade_on_already_extracted_is_noop`, so it is
    # recorded on #5140 rather than flipped in a P1 bugfix.
    out = _run([str(doc), "--db", uri,
                "--log", _tmp("events.jsonl"), "--out", _tmp("graph.html"),
                "--upgrade", "--point-model", "mock:cheap",
                "--relation-model", "mock:reason"], capture=True)
    assert "doc already extracted, skipped" in out, (
        "the replayed legacy shape became reachable — if that is deliberate, "
        "the discriminator is now extraction evidence and BOTH this test and the "
        f"help text must change deliberately:\n{out}")


@_live_db
def test_sweeping_a_transcript_does_not_move_it_into_the_documents_meter(monkeypatch):
    """#5140 review round 5, P1: the arms this PR widens can select a transcript
    Source, and the upgrade then re-ingests it — which used to overwrite its
    genre with ``''``.

    ``quota.DOCUMENT_METER_PREDICATE`` is ``documentKind IS NOT NULL AND
    documentKind <> 'transcript'``, so ``''`` is METERED and ``'transcript'`` is
    exempt: the genre overwrite moved a captured session INTO the documents cap
    (the inverse of the demotion #9536 closed, and just as wrong — the genre is
    the identity key). Cause: ``document_kind=fm.get("type", "")`` passes ``''``,
    which is a VALUE to the fold's ``coalesce($dk, s.documentKind, '')``; it must
    pass ``None`` so a stored genre survives.
    """
    from tortoise.quota import is_metered_document_kind

    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_genre_{os.urandom(4).hex()}")
    _reset(uri)
    base = tempfile.mkdtemp(prefix="tortoise_5140_corpus_")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    # A transcript as the capture writer mints it: genre set, no signal, and a
    # section so the sweep actually extracts it.
    doc = Path(base) / "session_transcript.md"
    doc.write_text("## Session\nwe decided to ship it\n", encoding="utf-8")
    _seed_document(uri, doc.name, str(doc), document_kind="transcript")
    assert is_metered_document_kind("transcript") is False
    assert is_metered_document_kind("") is True

    out = _run(["--db", uri, "--log", _tmp("events.jsonl"),
                "--out", _tmp("graph.html"), "--upgrade-all",
                "--include-unstamped", "--point-model", "mock:cheap",
                "--relation-model", "mock:reason"], capture=True)

    proj = FalkorProjection.from_uri(uri)
    try:
        rows = proj.g.query(
            "MATCH (s:Source {url:$u}) RETURN s.documentKind, s.needs_extraction",
            params={"u": doc.name},
        ).result_set
    finally:
        proj.close()
    kind, nx = rows[0]
    assert kind == "transcript", (
        f"the upgrade overwrote the transcript's genre with {kind!r}, which "
        f"moves it into the `documents` meter (metered={is_metered_document_kind(kind)});"
        f" output was:\n{out}")
    assert nx is False, f"the sweep did not complete the document (got {nx!r}):\n{out}"


@_live_db
def test_upgrade_makes_no_claim_for_a_document_with_nothing_to_extract():
    """Review round 7: ``--upgrade`` must decide "is there anything here?"
    BEFORE ``begin_ingest`` claims the content.

    ``begin_ingest`` MINTS a claim, and at a bumped extractor version it takes
    the reprocess path and supersedes/retracts that source's earlier Points.
    Claiming first therefore did destructive work for a file the same call then
    declines to extract, and it printed ``upgrading …`` immediately before
    saying the file is not a Document. The sweep was hoisted in round 2 for the
    same reason; this pins the ``--upgrade`` half.

    Observable: the ``upgrading`` banner is emitted only on the extracting path,
    AND the run's own event log carries no ``IngestStarted`` for the document.
    ⛔ The banner alone does NOT prove no claim was minted (round 8: a partial
    revert that claims while keeping the banner inside the branch leaves the
    banner absent and the claim present — measured, banner-only pin passed). The
    event log is the claim record, and it is the right observation because it
    names the claim directly, without a second mutating run: ``api.begin_ingest``
    emits ``IngestStarted`` on every call that does not skip. The completion marker still clears, which is pinned
    separately — that is the one thing ``--upgrade`` keeps from pre-D10 for a
    no-sections document.
    """
    if not _require_live_falkor():
        return
    uri = _live_uri(f"test_5140_noclaim_{os.urandom(4).hex()}")
    _reset(uri)
    t = _transcript("prose with no headers at all.\n", "no_claim.md")
    doc_id = t.name
    _seed_document(uri, doc_id, str(t),
                   doc_status="captured", needs_extraction=False)

    log_path = _tmp("events.jsonl")
    out = _run([str(t), "--db", uri,
                "--log", log_path, "--out", _tmp("graph.html"),
                "--upgrade", "--point-model", "mock:cheap",
                "--relation-model", "mock:reason"], capture=True)

    assert "upgrading" not in out, (
        f"`--upgrade` announced an upgrade (and so claimed the content via "
        f"begin_ingest) for a document with no ## sections, which it then "
        f"declines to extract; output was:\n{out}")
    assert "no claim made" in out, (
        f"expected the no-sections warning to say no claim was made; "
        f"output was:\n{out}")
    nx, ds = _read(uri, doc_id)
    assert (nx, ds) == (False, None), (
        f"the completion transition must still run for a no-sections document "
        f"named explicitly (got needs_extraction={nx!r}, doc_status={ds!r}); "
        f"output was:\n{out}")

    # The claim control — the event log IS the claim record: `begin_ingest`
    # emits `IngestStarted` for every call that does not skip.
    claims = []
    log_file = Path(log_path)
    # The log file is created on the FIRST append, so its absence is itself
    # evidence of no claim: a no-sections `--upgrade` writes no event at all (the
    # completion transition goes straight to the projection).
    if log_file.exists():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("type") == "IngestStarted" and ev.get("source_id") == doc_id:
                claims.append(ev)
    assert not claims, (
        f"`--upgrade` minted an extraction claim for a document it then declined "
        f"to extract: {claims}. At a bumped extractor version that claim "
        f"supersedes/retracts the source's earlier Points.")
