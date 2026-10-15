"""#5141 — ``graph-scripts/backfill_references.py`` must RUN, key by the D10
label's key, and never report an edge it did not create.

Two defects, both measured on ``origin/main``'s copy of the script:

1. **It crashed before it could do anything.** ``_ENTITY_FROM_POINT`` /
   ``_MERGE_REFERENCE`` are Cypher carrying literal map literals
   (``{id:$pid}``) and were driven through ``str.format``, so the braces parsed
   as REPLACEMENT FIELDS and the first Source with an extracted Point raised
   ``KeyError: 'id'`` — MID-RUN, after earlier Sources had already been written.
   Existing coverage could not see it: ``tests/test_references_edge.py`` inlines
   a MIRROR of the loop (in f-strings) instead of running the script.
2. **The D10 retarget moved the label and left the key.** ``aboutDocument`` was
   retargeted to ``:Source``, which is addressed by ``url`` (``stub_key``,
   ``tortoise/projection/edges.py``; ONTOLOGY §3.1) — but the fragment still read
   ``e.id`` and matched ``{id:$eid}``. A document Source minted without an ``id``
   yields NULL, matches nothing, and the script still incremented
   ``references_created``: a durability tool failing OPEN on its own report.

Harness conventions (mirrors ``tests/test_backfill_embeddings_force.py``): the
module is loaded via importlib from ``graph-scripts/`` and driven against a
FRESH embedded projection per test through the script's own per-graph runner —
no Docker, no network. Every test below names the input that makes it FAIL.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from tortoise.projection import FalkorProjection

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "graph-scripts" / "backfill_references.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "backfill_references_5141", str(_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def proj():
    tmpdir = tempfile.mkdtemp(prefix="t5141_refs_")
    p = FalkorProjection(os.path.join(tmpdir, "t5141.db"),
                         graph_name="tortoise")
    yield p
    p.close()
    # #4096: reclaim this fixture's temp tree on teardown.
    shutil.rmtree(tmpdir, ignore_errors=True)


def _corpus_source(g, url: str) -> None:
    """The corpus ``#205`` Source a Point is extracted from."""
    g.query(
        "CREATE (s:Source {url:$url, sourceKind:'document', title:$url, "
        "contentHash:'h', ingestedAt:'2024-01-01'})",
        params={"url": url},
    )


def _point_from(g, source_url: str, pid: str) -> None:
    g.query(
        "CREATE (p:Point {id:$pid, content:'c', context:'t', "
        "is_operator:false})",
        params={"pid": pid},
    )
    g.query(
        "MATCH (p:Point {id:$pid}), (s:Source {url:$url}) "
        "MERGE (p)-[:extractedFrom]->(s)",
        params={"pid": pid, "url": source_url},
    )


def _count(g, cypher: str) -> int:
    return g.query(cypher).result_set[0][0]


# ── the crash (#5141 finding 1) + the key (#5141 finding 2) ────────────────

def test_backfill_runs_and_links_a_document_source_by_url(proj):
    """FAILS IF the fragments are driven through ``str.format`` (``KeyError:
    'id'`` before any edge is written), and FAILS IF the ``:Source`` target is
    addressed by ``id``: this document carries ``url`` and NO ``id``, so the old
    key read NULL and the MERGE matched nothing.
    """
    g = proj.g
    mod = _load_script()
    _corpus_source(g, "corpus://d1")
    # D10 (ONTOLOGY v3.15 §4.4): a document IS a :Source, keyed by url.
    g.query("CREATE (d:Source {url:'doc-1', documentKind:'report', title:'Doc'})")
    _point_from(g, "corpus://d1", "p1")
    g.query("MATCH (p:Point {id:'p1'}), (d:Source {url:'doc-1'}) "
            "MERGE (p)-[:aboutDocument]->(d)")

    stats = mod.backfill_graph(proj)  # <- the run that used to raise KeyError

    assert stats["references_created"] == 1, stats
    assert _count(
        g, "MATCH (:Source {url:'corpus://d1'})-[:references]->"
           "(:Source {url:'doc-1'}) RETURN count(*)"
    ) == 1, "the D10 target was not linked by url — the label moved, the key did not"


def test_backfill_does_not_count_an_unaddressable_target(proj):
    """FAILS IF a miss is counted as a created edge (the fail-open #5141 named).

    The ``:Subject`` here is the id-less legacy/stub holder shape — the about
    edge exists, but the target carries no usable MERGE key, so no edge can be
    written and none may be reported.
    """
    g = proj.g
    mod = _load_script()
    _corpus_source(g, "corpus://d2")
    g.query("CREATE (sub:Subject {name:'Ghost', subjectKind:'other'})")
    _point_from(g, "corpus://d2", "p2")
    g.query("MATCH (p:Point {id:'p2'}), (sub:Subject {name:'Ghost'}) "
            "MERGE (p)-[:aboutSubject]->(sub)")

    stats = mod.backfill_graph(proj)

    assert stats["references_created"] == 0, (
        f"a miss was counted as a created edge — the fail-open #5141 reported: {stats}")
    assert stats["entities_unresolved"] == 1, stats
    assert stats["sources_without_entity"] == 1, stats
    assert _count(
        g, "MATCH (:Source {url:'corpus://d2'})-[:references]->() RETURN count(*)"
    ) == 0


def test_backfill_counts_each_target_once_and_is_idempotent(proj):
    """FAILS IF two Points sharing one target are reported as two created edges
    (the MERGE matching the first one), or IF a re-run re-writes anything.
    """
    g = proj.g
    mod = _load_script()
    _corpus_source(g, "corpus://d3")
    g.query("CREATE (sub:Subject {id:'sub-3', name:'Widget', subjectKind:'other'})")
    for pid in ("p3a", "p3b"):
        _point_from(g, "corpus://d3", pid)
        g.query("MATCH (p:Point {id:$pid}), (sub:Subject {id:'sub-3'}) "
                "MERGE (p)-[:aboutSubject]->(sub)", params={"pid": pid})

    first = mod.backfill_graph(proj)
    assert first["sources_processed"] == 1, first
    assert first["references_created"] == 1, first  # two Points, ONE edge
    assert _count(
        g, "MATCH (:Source {url:'corpus://d3'})-[:references]->() RETURN count(*)"
    ) == 1

    # Idempotent: the Source now HAS a reference, so a re-run selects nothing.
    second = mod.backfill_graph(proj)
    assert second == {"sources_processed": 0, "references_created": 0,
                      "sources_without_entity": 0, "entities_unresolved": 0}, second


# ── main(): the CLI's handle budget ────────────────────────────────────────

def _spy_projection(proj, opened: list, closed: list):
    """A ``FalkorProjection`` stand-in that counts opens/closes and answers with
    the REAL embedded projection, so the CLI's own queries still execute.

    ``close`` is a no-op counter, not ``proj.close()``: the fixture's store must
    outlive the call so the test can keep asserting on it.
    """
    class _Handle:
        def __init__(self):
            self.g = proj.g

        def close(self):
            closed.append(1)

    class _Spy:
        @staticmethod
        def from_uri(uri):
            opened.append(uri)
            return _Handle()

    return _Spy


def test_main_owns_exactly_one_handle_on_the_backfill_path(proj, monkeypatch, capsys):
    """FAILS IF the CLI opens a handle it never closes (the pre-existing shape
    opened one BEFORE the ``--total-sources`` branch, then opened a second in
    ``backfill`` and closed only that one: two opens, one close, one leak).
    """
    mod = _load_script()
    opened, closed = [], []
    monkeypatch.setattr(mod, "FalkorProjection",
                        _spy_projection(proj, opened, closed))
    monkeypatch.setattr(sys, "argv", ["backfill_references.py", "--db-uri",
                                       "docker://:x@127.0.0.1:1/g"])

    mod.main()

    out = capsys.readouterr().out
    assert len(opened) == 1, opened
    assert len(closed) == 1, closed
    assert "Sources without references edges: 0" in out, out
    assert "Unlinkable targets:      0" in out, out


def test_main_total_sources_reports_and_closes(proj, monkeypatch, capsys):
    """The report path: one handle, closed, and the reported split is real."""
    mod = _load_script()
    _corpus_source(proj.g, "corpus://t1")
    opened, closed = [], []
    monkeypatch.setattr(mod, "FalkorProjection",
                        _spy_projection(proj, opened, closed))
    monkeypatch.setattr(sys, "argv", ["backfill_references.py", "--db-uri",
                                       "docker://:x@127.0.0.1:1/g",
                                       "--total-sources"])

    mod.main()

    out = capsys.readouterr().out
    assert "Total Sources: 1" in out, out
    assert "With references: 0" in out, out
    assert "Without references: 1" in out, out
    assert len(opened) == 1 and len(closed) == 1, (opened, closed)
