"""#4524 — the remaining ``vecf32`` overwrite seams on the EMBEDDED engine.

**The defect (settled, #4457).** On falkordblite/redislite — the DEFAULT
self-hosted backend — writing a vector onto a property that ALREADY holds a
``VectorF32`` can be **silently discarded**: the old vector survives and no
error is raised. The docker/server lane lands the identical write, so the same
commit can pass on the server lane and silently serve a STALE vector on the
default backend. PR #4517 fixed the Point seam (``_upsert_point_props``); this
file guards the four remaining seams in ``tortoise/projection/entities.py``:
``Subject``, ``Object``, ``Document`` and the plain ``Event`` MERGE.

**Why the guards are embedded-only.** A docker-lane run cannot observe the
no-op, so a server-lane guard would certify nothing — the module carries
``pytestmark = pytest.mark.embedded_only`` and is registered in
``config/ci-surfaces.yml``'s ``carve_out`` (and ``TEST_NO_REDIRECT_STEMS``), so
it executes URI-unset in the carve-out job and is excluded from every docker
leg. A guard that never runs is worse than none (#4047).

**Each guard is mutation-proven.** Reverting that seam's ``REMOVE`` reddens
exactly this test (the first overwrite that the engine drops leaves the
previous vector in place, so ``read-back != written``). A guard never watched
fail is not evidence — see #4524's own history.

**Hermetic.** Each test builds a fresh embedded projection on its own tmp path
(``tests._embedded.fresh_embedded_proj``) and installs a deterministic 384-dim
sequence encoder over the ``encode_for_store`` seam. No model, no network. The
vectors are the ones MEASURED to go stale on falkordblite: hashed unit-norm
384-dim rows whose successive per-component deltas are far below the engine's
(undocumented, not-modelled) discard threshold.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np
import pytest

from tests._embedded import fresh_embedded_proj
from tortoise.embeddings import EMBEDDING_DIM
from tortoise.sdk import _TURN_WRITE_CYPHER

pytestmark = pytest.mark.embedded_only

#: number of sequential writes to the SAME node; 1 create + 3 overwrites.
_WRITES = 4


def _vector(i: int) -> list[float]:
    """Deterministic 384-dim unit-norm vector, distinct for every ``i``.

    Distinctness is what makes each write an OVERWRITE: on the embedded engine
    a discarded overwrite leaves the *previous* row in place, so a guard using
    one constant vector could read it back and never fail.
    """
    raw = hashlib.sha256(f"tortoise-4524-{i}".encode()).digest()
    buf = (raw * (EMBEDDING_DIM // len(raw) + 1))[:EMBEDDING_DIM]
    vec = [(byte / 255.0) - 0.5 for byte in buf]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


class _SequenceEncoder:
    """``encode_for_store`` double returning a fresh vector on every call.

    ``Subject`` and ``Object`` MERGE on ``name`` — which is also the text their
    embedding derives from — so the only way the SAME node receives a
    *different* vector through those seams is a re-embed whose output changed
    (a model rotation, or a ``--force`` re-embed). That is the real scenario
    the engine defect harms (#4194's "stored vector disagrees with the query
    encoder" trap), and a sequence double is how it is simulated here without
    a model. ``Document``/``Event`` vary their own content, but the same double
    works uniformly.
    """

    def __init__(self, count: int):
        self.vectors = [_vector(i) for i in range(count)]
        self.calls = 0

    def __call__(self, content, expected_dim=None):
        assert self.calls < len(self.vectors), (
            "the seam encoded more texts than the guard staged — the write "
            "count drifted from _WRITES")
        vec = list(self.vectors[self.calls])
        self.calls += 1
        return vec


def _read_vector(proj, cypher: str, params: dict) -> list[float]:
    rows = proj.g.query(cypher, params=params).result_set
    assert rows, f"no node matched {cypher!r} — the guard cannot observe"
    stored = rows[0][0]
    assert stored is not None, (
        f"embedding read back NULL after {cypher!r} — the write did not land "
        "at all (a different failure than the stale-vector one)")
    return list(stored)


def _assert_landed(stored, expected, label: str, i: int) -> None:
    np.testing.assert_allclose(
        np.asarray(stored, dtype=float), np.asarray(expected, dtype=float),
        rtol=1e-5, atol=1e-6,
        err_msg=(
            f"{label} write #{i} did NOT land: read-back is not the vector "
            "that was written. On the embedded engine that means the "
            "``vecf32`` overwrite was silently discarded and the node kept "
            "its OLD vector (#4457/#4524)"))


def _run_seam(tmp_path, monkeypatch, *, label, read_cypher, read_params,
              write):
    """Apply ``write(i)`` ``_WRITES`` times, then assert EVERY read-back.

    The assertions run AFTER the full sequence so the guard necessarily
    performs its 3 overwrites (1 create + 3 overwrites) before it can fail — a
    dropped intermediate would otherwise abort the loop early and never reach
    the later writes this issue is about.
    """
    enc = _SequenceEncoder(_WRITES)
    monkeypatch.setattr("tortoise.embeddings.encode_for_store", enc)
    with fresh_embedded_proj(tmp_path) as proj:
        stored = []
        for i in range(_WRITES):
            write(proj, i)
            stored.append(_read_vector(proj, read_cypher, read_params))
    for i, (got, want) in enumerate(zip(stored, enc.vectors, strict=True)):
        _assert_landed(got, want, label, i)
    return enc.calls


# ── the four affected seams ─────────────────────────────────────────────────

def test_subject_embedding_overwrite_lands(tmp_path, monkeypatch):
    """``Subject`` — the ``vecf32`` write in ``_upsert_subject``'s
    ``ON MATCH SET s.embedding``, guarded by the pre-``REMOVE``."""
    calls = _run_seam(
        tmp_path, monkeypatch,
        label="Subject",
        read_cypher="MATCH (s:Subject {name:$name}) RETURN s.embedding",
        read_params={"name": "Acme Corp"},
        write=lambda proj, i: proj.apply(
            {"type": "SubjectAdded", "id": "subj-1", "name": "Acme Corp",
             "subject_kind": "org"}),
    )
    assert calls == _WRITES


def test_object_embedding_overwrite_lands(tmp_path, monkeypatch):
    """``Object`` — the ``vecf32`` write in ``_upsert_object``'s
    ``ON MATCH SET o.embedding``, guarded by the pre-``REMOVE``."""
    calls = _run_seam(
        tmp_path, monkeypatch,
        label="Object",
        read_cypher="MATCH (o:Object {name:$name}) RETURN o.embedding",
        read_params={"name": "Widget"},
        write=lambda proj, i: proj.apply(
            {"type": "ObjectRegistered", "id": "obj-1", "name": "Widget",
             "object_kind": "other"}),
    )
    assert calls == _WRITES


def test_document_embedding_overwrite_lands(tmp_path, monkeypatch):
    """``Document`` — the plain ``SET s.embedding`` in ``_upsert_document``,
    guarded by the conditional in-query ``REMOVE s.embedding``.

    D10 (#5026, ONTOLOGY v3.15 §4.4) RETIRED the ``:Document`` graph label:
    the document node is now ``(:Source {url: <document id>})``, so the guard
    reads that node. The SEAM is unchanged (same statement, same conditional
    in-query REMOVE) — only the label and MERGE key moved, and they moved
    together. Reading the old label would now match nothing and the guard
    would fail on absence rather than on the stale vector it exists to catch.
    """
    calls = _run_seam(
        tmp_path, monkeypatch,
        label="Document",
        read_cypher="MATCH (s:Source {url:$id}) RETURN s.embedding",
        read_params={"id": "doc-1"},
        write=lambda proj, i: proj.apply(
            {"type": "DocumentCreated", "id": "doc-1", "title": "Report",
             "content": f"revision {i}"}),
    )
    assert calls == _WRITES


def test_event_plain_merge_embedding_overwrite_lands(tmp_path, monkeypatch):
    """``Event`` plain path — ``_event_plain_merge``'s ``ON MATCH SET
    e.embedding``, guarded by the pre-``REMOVE``.

    The plain path consumes a journaled ``embedding`` when the payload carries
    one (epic #900's sanctioned replay carrier), so this guard passes the
    vectors explicitly rather than through the encoder seam.
    """
    vectors = [_vector(i) for i in range(_WRITES)]
    with fresh_embedded_proj(tmp_path) as proj:
        stored = []
        for vec in vectors:
            proj.apply({
                "type": "EventRecorded", "eventId": "evt-1",
                "eventKind": "click", "subject": "alice", "object": "widget",
                "embedding": list(vec),
            })
            stored.append(_read_vector(
                proj, "MATCH (e:Event {eventId:$eid}) RETURN e.embedding",
                {"eid": "evt-1"}))
    for i, (got, want) in enumerate(zip(stored, vectors, strict=True)):
        _assert_landed(got, want, "Event(plain)", i)


def test_turn_write_cypher_embedding_overwrite_lands(tmp_path):
    """``_TURN_WRITE_CYPHER`` — the ONE shared capture turn writer.

    Both capture lanes (``TortoiseSDK.capture_session`` and hosted
    ``_capture_session_impl``) write turns through this single statement, so a
    silently-discarded overwrite here has the widest blast radius of any seam in
    this file: the node keeps its OLD vector while holding the NEW text, and the
    rotation self-heal the statement exists to provide never runs.

    This guard drives the PRODUCTION statement constant rather than hand-written
    Cypher, so it binds the real seam. Vectors come from ``_vector(i)``, whose
    per-component deltas are small — that is the regime the engine discards. A
    full-magnitude change lands even unpatched, so a guard built on one would be
    vacuous: measured, ``[1,0,...] -> [0,1,...]`` lands with the clear removed,
    while ``[1,0,...] -> [0.95,0.05,...]`` goes stale.
    """
    vectors = [_vector(i) for i in range(_WRITES)]
    with fresh_embedded_proj(tmp_path) as proj:
        proj.g.query("CREATE (s:Session {id: 'turn-4524'})")
        stored = []
        for i, vec in enumerate(vectors):
            proj.g.query(_TURN_WRITE_CYPHER, params={
                "sid": "turn-4524",
                "now": "2026-01-01T00:00:00Z",
                # #4911: the statement carries a required `$redactions` param
                # (the per-session redaction count), so every driver of the
                # production constant must bind it.
                "redactions": 0,
                "turns": [{"id": "turn-1", "c": f"text-{i}", "k": "turn",
                           "speaker": "user", "s": "completed",
                           "ch": f"h-{i}", "emb": list(vec)}],
            })
            stored.append(_read_vector(
                proj, "MATCH (t:Point {id:'turn-1'}) RETURN t.embedding", {}))
    for i, (got, want) in enumerate(zip(stored, vectors, strict=True)):
        _assert_landed(got, want, "Turn(_TURN_WRITE_CYPHER)", i)
