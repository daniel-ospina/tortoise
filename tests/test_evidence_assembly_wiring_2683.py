"""#2683 (Slice A) — the eval-lane arm WIRING: the package IS the reader window.

WHY THIS FILE EXISTS (the hole it closes)
-----------------------------------------
The issue's `O` indicator promises hermetic tests *and* that the eval seam's arm
markers on ``retrieve_for_question`` outcomes are exercised. The hermetic suite
(``tests/test_evidence_assembly.py``) covers the PURE function and pins the seam's
signature default with ``inspect.signature`` — but nothing drove
``tools/longmem_eval/retrieve.py::retrieve_for_question`` with the arm ON, so the
one line that makes the mechanism reach the reader was unpinned. Measured on
``main`` (``e1ba0974e``): deleting the single rebind

    context_candidates = _packaged        # tools/longmem_eval/retrieve.py

leaves ``tests/test_evidence_assembly.py`` (30 passed) and
``tests/longmem_eval/test_slice_a_flood_bound.py`` (22 passed) fully GREEN, while
the eval lane silently measures OFF-vs-OFF: ``evidence_assembly_stats`` is still
computed and recorded (``applied: True``, ``collapsed_duplicates: N``) and the
run would report "Slice A changed nothing". That is a false null on the very arm
the affected-set gate (#11081's ruling, 2026-11-07) lists #2683 for — the wasted
eval the owner forbade.

LAYER
-----
Docker lane (FTS-capable FalkorDB), on a dedicated per-test graph — the same
layer as the sibling eval-lane arm pins (``test_time_aware_eval_2520.py``,
``test_entity_key_expansion.py``). ``hybrid_search`` is stubbed to a DETERMINISTIC
pool (a source chunk ranked ABOVE its own distilled point, plus a cross-session
restatement of the same fact) so the differential isolates the arm: everything
after the fetch — annotation, per-session dedup, boost, packaging, assembly — is
the real path.

Each test names its FAIL VALUE.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _live_utils
from tortoise.sdk import TortoiseSDK


def _falkordb_available() -> bool:
    """Probe a live FalkorDB; reads ``TORTOISE_DB_URI`` at CALL time (#221)."""
    uri = os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")
    old = os.environ.get("TORTOISE_DB_URI")
    try:
        os.environ["TORTOISE_DB_URI"] = f"{uri}_probe2683w"
        from tortoise.sdk import TortoiseSDK as _ProbeSDK
        _probe = _ProbeSDK()
        _probe._get_proj().g.query("RETURN 1")
        _probe.close()
        return True
    except Exception:
        return False
    finally:
        if old is not None:
            os.environ["TORTOISE_DB_URI"] = old
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


requires_falkordb = pytest.mark.skipif(
    not _falkordb_available(),
    reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane)")


def _uri() -> str:
    return os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")


SESSION_1 = "sess2683tea"
SESSION_2 = "sess2683tea2"
QUOTE = "the tea set cost 300 dollars"
#: The distilled point, its OWN source chunk, a cross-session restatement of the
#: SAME fact, and an unrelated statement that must survive untouched.
POINT_ID = "p2683point"
CHUNK_ID = "p2683chunk"
DUP_ID = "p2683dupe"
OTHER_ID = "p2683other"
QUESTION_DATE = "2026-09-25"

#: The DISTILLED statements the package must end up as anchors of.
_STATEMENT_IDS = {POINT_ID, DUP_ID, OTHER_ID}


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT — this module is about assembly wiring, not which
    leg produced a hit (no model load, no network)."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get", staticmethod(lambda: None))


@pytest.fixture
def seeded_sdk(monkeypatch):
    """A dedicated per-test graph: one provenanced point (+ its own verbatim
    source chunk), a cross-session restatement of that same fact, and an
    unrelated statement."""
    monkeypatch.setenv("TORTOISE_DB_URI", f"{_uri()}_{uuid.uuid4().hex[:10]}")
    for var in ("TORTOISE_LME_EVIDENCE_ASSEMBLY",
                "TORTOISE_LME_EVIDENCE_BOOST"):
        monkeypatch.delenv(var, raising=False)
    sdk = TortoiseSDK()
    try:
        sdk.create_point(
            "statement", QUOTE, id=POINT_ID, session_id=SESSION_1,
            lme_session_index=0, status="draft",
            quote=QUOTE, source_turn_id=2)
        # The windowed verbatim transcript that turn lives in — it holds the
        # quote verbatim, which is the containment leg ``_is_own_source`` reads.
        sdk.create_point(
            "session-transcript", f"user: {QUOTE}\nthe buyer paid cash.",
            id=CHUNK_ID, session_id=SESSION_1, lme_session_index=0,
            status="draft")
        # The same fact distilled again in a LATER session (the cross-item
        # near-dupe the window must not pay two slots for).
        sdk.create_point(
            "statement", QUOTE, id=DUP_ID, session_id=SESSION_2,
            lme_session_index=1, status="draft")
        sdk.create_point(
            "statement", "i live in lisbon", id=OTHER_ID,
            session_id=SESSION_1, lme_session_index=0, status="draft")
        yield sdk
    finally:
        sdk.close()


def _question(*, qtype: str = "multi-session", qid: str = "q2683w") -> dict:
    return {
        "question_id": qid,
        "question": "how much did the tea set cost",
        "question_type": qtype,
        "answer_session_ids": [SESSION_1],
        "haystack_session_ids": [SESSION_1, SESSION_2],
        "haystack_dates": ["2024-03-01", QUESTION_DATE],
        "haystack_sessions": [],
        "answer": "",
        "question_date": QUESTION_DATE,
    }


def _stub_pool(monkeypatch, monkeypatch_target) -> list[dict]:
    """A DETERMINISTIC pool: the raw source chunk ranked ABOVE its own distilled
    point (so the collapse must be order-independent, not arrival-ordered), the
    cross-session restatement second, the unrelated statement last."""
    chunk = {"id": CHUNK_ID, "content": f"user: {QUOTE}\nthe buyer paid cash.",
             "match_source": "fts"}
    point = {"id": POINT_ID, "content": QUOTE, "match_source": "fts"}
    dup = {"id": DUP_ID, "content": QUOTE, "match_source": "fts"}
    other = {"id": OTHER_ID, "content": "i live in lisbon",
             "match_source": "fts"}
    pool = [dict(chunk), dict(point), dict(dup), dict(other)]
    calls: list[dict] = []

    def _fake(sdk, query, **kw):
        calls.append(kw)
        return [dict(h) for h in pool]

    monkeypatch.setattr(monkeypatch_target, "hybrid_search", _fake)
    return calls


def _ids(items: list[dict]) -> list[str]:
    return [h["id"] for h in items]


# ── 1. the arm REBINDS the reader window (the mechanism) ─────────────────

@requires_falkordb
def test_the_arm_rebinds_the_reader_window_to_the_package(
        seeded_sdk, monkeypatch):
    """FAIL VALUE: the ON arm leaving the reader window equal to the pool (the
    rebind absent — the measured hole: 52 sibling tests stay green), or an arm
    that computes the stats but keeps ``context_candidates = pool``.

    Reachable: the pool is stubbed with the chunk above its point and the
    duplicate in a second session, so the package boundary is known exactly."""
    import tools.longmem_eval.retrieve as _r
    _stub_pool(monkeypatch, _r)
    q = _question()

    off = _r.retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        evidence_assembly=False)
    on = _r.retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        evidence_assembly=True)

    # the arm marker + the census the run config records (D2: ON only)
    assert on["evidence_assembly"] is True
    stats = on["evidence_assembly_stats"]
    assert stats["applied"] is True, stats
    assert stats["tr_excluded"] is False, stats
    assert stats["pool_items"] == 4, stats
    # the point + its own chunk = ONE slot; the cross-session restatement
    # merges into that same package ⇒ 2 packages over 4 pool items.
    assert stats["packages"] == 2, stats
    assert stats["collapsed_duplicates"] == 1, stats
    assert stats["verbatim_refs_kept"] == 1, stats
    assert stats["package_items"] == 3, stats

    # the READER WINDOW is the package: the duplicate is gone and the point's
    # own chunk sits immediately after its anchor (a flat anchor-then-refs
    # rendering), not at its pool rank.
    on_ids = _ids(on["context_points"])
    assert DUP_ID not in on_ids, (
        "the cross-session restatement must not consume a second window slot")
    assert set(on_ids) == {POINT_ID, CHUNK_ID, OTHER_ID}, on_ids
    assert on_ids.index(POINT_ID) < on_ids.index(CHUNK_ID), on_ids
    assert on["context_point_count"] == 3, on["context_point_count"]

    # …and the OFF window really was different (so the assertion above is a
    # differential, not a coincidence of this pool).
    off_ids = _ids(off["context_points"])
    assert set(off_ids) == {CHUNK_ID, POINT_ID, DUP_ID, OTHER_ID}, off_ids

    # the RECALL SURFACE is untouched: packaging shapes the window, never the
    # pool the retrieval metrics are computed over.
    assert _ids(on["hits"]) == _ids(off["hits"]), (
        "the pool (recall surface) must be byte-identical ON vs OFF")
    assert {POINT_ID, CHUNK_ID, DUP_ID, OTHER_ID} <= set(_ids(on["hits"]))


# ── 2. the off-switch: OFF is the untouched historical path ──────────────

@requires_falkordb
def test_the_off_switch_leaves_the_old_path_unchanged(seeded_sdk, monkeypatch):
    """FAIL VALUE: a default that is ON (the #1745 fail-safe decision is OFF),
    an OFF path that emits the arm's keys (D2: the off-path outcome keeps
    today's exact shape), or a switch that only reads the explicit kwarg and
    ignores the env the run config uses.

    The switch is ``TORTOISE_LME_EVIDENCE_ASSEMBLY`` (tri-state, only
    1/true/yes/on enables)."""
    import tools.longmem_eval.retrieve as _r
    _stub_pool(monkeypatch, _r)
    q = _question()

    monkeypatch.delenv("TORTOISE_LME_EVIDENCE_ASSEMBLY", raising=False)
    default = _r.retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60)
    explicit_off = _r.retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        evidence_assembly=False)

    assert "evidence_assembly" not in default, (
        "the OFF path must keep today's exact outcome shape (D2): the arm key "
        "is ON-only, not a False marker")
    assert "evidence_assembly_stats" not in default, (
        "the OFF path must keep today's exact outcome shape (D2)")
    assert _ids(default["context_points"]) == _ids(
        explicit_off["context_points"])

    # garbage never arms (fail-safe, no raising)
    monkeypatch.setenv("TORTOISE_LME_EVIDENCE_ASSEMBLY", "maybe")
    garbage = _r.retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60)
    assert "evidence_assembly" not in garbage
    assert _ids(garbage["context_points"]) == _ids(
        explicit_off["context_points"])

    # …and the env the run config will actually set DOES arm it
    monkeypatch.setenv("TORTOISE_LME_EVIDENCE_ASSEMBLY", "1")
    armed = _r.retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60)
    assert armed["evidence_assembly"] is True
    assert armed["evidence_assembly_stats"]["applied"] is True
    assert DUP_ID not in _ids(armed["context_points"])


# ── 3. TR questions are excluded (the R5 date machinery owns their window) ─

@requires_falkordb
def test_a_tr_question_is_excluded_and_leaves_the_window_untouched(
        seeded_sdk, monkeypatch):
    """FAIL VALUE: the D3 hit-type sort reordering a temporal-reasoning window
    that the R5 time-ascending machinery owns — the arm records
    ``tr_excluded`` and the window is identical ON vs OFF."""
    import tools.longmem_eval.retrieve as _r
    _stub_pool(monkeypatch, _r)
    q = _question(qtype="temporal-reasoning", qid="q2683wtr")

    off = _r.retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        evidence_assembly=False)
    on = _r.retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        evidence_assembly=True)

    assert on["evidence_assembly"] is True
    stats = on["evidence_assembly_stats"]
    assert stats["applied"] is False, stats
    assert stats["tr_excluded"] is True, stats
    assert _ids(on["context_points"]) == _ids(off["context_points"]), (
        "the TR window is the R5 date machinery's — the assembly arm must not "
        "touch it")
