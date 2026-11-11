"""#2683 (Slice A) — the arm WIRING on BOTH lanes: the package IS the window.

Two seams carry Slice A to a reader, and each is ONE line that can go inert
without reddening anything: the eval seam
(``tools/longmem_eval/retrieve.py``, below — the arm reached through
``retrieve_for_question``) and the PRODUCT seam
(``tortoise/ask_lane.py``, the second half of this file). The eval seam's
rebind was pinned by #11448; the product seam's was not, and is pinned here.

WHY THIS FILE EXISTS (the hole it closes)
-----------------------------------------
The issue's `I` indicator promises hermetic tests (one-fact-one-slot, cross-item
dedup, ordering contract, OFF-by-default parity), and its `T` line requires the
arm to be re-enabled atop the fixed assembly in the eval lane — which needs the
seam's arm state to be observable there. Neither `O` nor `I` names the seam, so
nothing about the eval WIRING was promised *or* pinned: the hermetic suite
(``tests/test_evidence_assembly.py``) covers the PURE function and pins the seam's
signature default with ``inspect.signature`` — but nothing drove
``tools/longmem_eval/retrieve.py::retrieve_for_question`` with the arm ON, so the
one line that makes the mechanism reach the reader was unpinned. Measured on
``main`` (``e1ba0974e``): deleting the single rebind

    context_candidates = _packaged        # tools/longmem_eval/retrieve.py

leaves ``tests/test_evidence_assembly.py`` (30 passed) and
``tests/longmem_eval/test_slice_a_flood_bound.py`` (22 passed) fully GREEN, while
the eval lane silently measures OFF-vs-OFF: ``evidence_assembly_stats`` is still
computed (``applied: True``, ``collapsed_duplicates: N``) on the outcome the seam
RETURNS — the runner's per-outcome whitelist (the ``outcome = {`` dict in the
run loop) and its Layer-1 projection (the ``"outcomes"`` block inside
``run.py``'s ``outcomes_to_report``) carry four sibling arm-stat keys and not
this one (the ``tr_session`` ``_stats`` halves are dropped too — see the census
comment below), so nothing the run persists shows the arm state (``#11467``) —
and the run would report "Slice A changed nothing". That is a false null on the
very arm the affected-set gate (#8486's ruling, 2026-11-07, comment 6710955512)
lists #2683 for — the wasted eval the owner forbade.

LAYER
-----
TWO layers, one per seam — the layer belongs to the seam, not to the file:

* **the eval seam** (tests 1-3) — Docker lane (FTS-capable FalkorDB), on a
dedicated per-test graph, the same layer as the sibling eval-lane arm pins
(``test_time_aware_eval_2520.py``, ``test_entity_key_expansion.py``).
``hybrid_search`` is stubbed to a DETERMINISTIC pool (a source chunk ranked
ABOVE its own distilled point, plus a cross-session restatement of the same
fact) so the differential isolates the arm: everything after the fetch —
annotation, per-session dedup, boost, packaging, assembly — is the real path.
* **the product seam** (tests 4-6) — HERMETIC: no graph, no model, no
network. The ask lane's only graph-touching calls are
``sdk.tortoise_fts_query`` and ``sdk.annotate_ask_hits``; both are stubbed and
everything after them is the real product path, so these tests do not skip on
a box with no FalkorDB (the seam they pin has no graph in it).

Each test names its FAIL VALUE.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tortoise.retrieval as _retrieval
from tests import _live_utils
from tortoise import ask_lane as _ask_lane
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


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT — this module is about assembly wiring, not which
    leg produced a hit (no model load, no network)."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get", staticmethod(lambda: None))


#: Every env-gated knob ``retrieve_for_question`` reads ON ITS CALL PATH that
#: can move the POOL (membership/order), the WINDOW (item cap) or the ARM.
#: "Reads" is taken literally and broadly, because the criterion is what decides
#: membership: a bare ``os.environ.get`` (judged against the ``_TRUTHY`` token
#: set — a vocabulary, never a read path of its own), or one mediated by
#: ``_env_int`` / ``_env_float`` / ``_env_boost_float`` /
#: ``resolve_pool_size(env_name=…)``. A developer shell carrying any of them
#: would red a pinned count or flip the anchor — a spurious red, never a false
#: pass, but still a price for nothing. Scrubbed, not tolerated: the
#: pool/window/arm set is exactly this module's subject.
#: ⛔ Scope, stated so the claim stays checkable: the ``TORTOISE_LME_GATE_*``
#: singletons are read at MODULE import (``retrieve.py:3495-3511``), not on this
#: path, so they are out of scope — a helper-mediated read is IN scope, which is
#: why the ``TR_SESSION_COMPLETION_*`` sub-knobs below are listed alongside their
#: own parent. Those four are BELT-AND-BRACES, not load-bearing: their reads sit
#: inside ``if tr_session_completion_on and is_tr and pool``
#: (``retrieve.py:2962-2983``), and the parent is scrub-gated to unset, so no
#: extreme value on them can reach a pinned count today (measured: with the four
#: entries removed AND all four set to ``1``, this module is still 3 green).
#: ``TORTOISE_LME_RERANK_MODEL`` is the same shape: ``get_scorer`` reads it only
#: under ``rerank_on`` (``retrieve.py:1814-1815``), and ``TORTOISE_LME_RERANK``
#: — also scrubbed — is what arms that.
_ENV_SCRUB = (
    # the arm under test + its boost legs
    "TORTOISE_LME_EVIDENCE_ASSEMBLY",
    "TORTOISE_LME_EVIDENCE_BOOST",
    "TORTOISE_LME_EVIDENCE_BOOST_ANSWER_STRING",
    "TORTOISE_LME_EVIDENCE_BOOST_SOURCE",
    "TORTOISE_LME_EVIDENCE_BOOST_VERBATIM",
    # pool membership / order
    "TORTOISE_LME_RERANK",
    # its model choice — read by `_model_name` (`rerank.py:98`) via `get_scorer`,
    # gated by the parent above; same shape as the TR sub-knobs below
    "TORTOISE_LME_RERANK_MODEL",
    "TORTOISE_LME_RERANK_CAP",
    "TORTOISE_LME_RERANK_LAMBDA",
    "TORTOISE_LME_RERANK_POOL",
    "TORTOISE_LME_COVERAGE_LOOP",
    "TORTOISE_LME_POOL_SIZE",
    "TORTOISE_LME_SESSION_REINJECTION",
    "TORTOISE_LME_REINJECTION_TOTAL_CAP",
    "TORTOISE_LME_ENTITY_KEY_EXPANSION",
    "TORTOISE_LME_TIME_AWARE_QE",
    "TORTOISE_LME_TEMPORAL_LEG",
    "TORTOISE_LME_TEMPORAL_LEG_LIMIT",
    "TORTOISE_LME_TEMPORAL_LEG_PLACEMENT",
    "TORTOISE_LME_TEMPORAL_LEG_WEIGHT",
    "TORTOISE_LME_TEMPORAL_AGGREGATE_FLAG",
    "TORTOISE_LME_AGGREGATIVE_FLAG",
    "TORTOISE_LME_TR_SESSION_ADMISSION",
    "TORTOISE_LME_TR_SESSION_COMPLETION",
    # its gated sub-knobs, read through `_env_int` (`retrieve.py:2967-2982`),
    # reachable only once the parent above is armed (belt-and-braces; see scope
    # note above)
    "TORTOISE_LME_TR_SESSION_COMPLETION_TOTAL",
    "TORTOISE_LME_TR_SESSION_COMPLETION_WINDOW",
    "TORTOISE_LME_TR_SESSION_COMPLETION_SESSIONS",
    "TORTOISE_LME_TR_SESSION_COMPLETION_PER_SESSION",
    # the floor-term guard's lower bound, read at CALL time inside the SUT
    # (`retrieve.py:2262`) as the two pool-ORDER compositions receive it
    "TORTOISE_LME_FLOOR_TERM",
    # the reader-window item cap
    "TORTOISE_LME_CONTEXT_ITEMS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Clear the pool/window/arm knobs so a stray export cannot red a pinned
    count (an ambient ``RERANK``/``COVERAGE_LOOP``/``CONTEXT_ITEMS`` moves the
    very quantities this module asserts)."""
    for var in _ENV_SCRUB:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def seeded_sdk(monkeypatch):
    """A dedicated per-test graph: one provenanced point (+ its own verbatim
    source chunk), a cross-session restatement of that same fact, and an
    unrelated statement."""
    monkeypatch.setenv("TORTOISE_DB_URI", f"{_uri()}_{uuid.uuid4().hex[:10]}")
    for var in _ENV_SCRUB:
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


def _stub_pool(monkeypatch, target) -> None:
    """A DETERMINISTIC pool: the raw source chunk ranked ABOVE its own distilled
    point (so the collapse must be order-independent, not arrival-ordered), the
    cross-session restatement second, the unrelated statement last.

    ``target`` is the MODULE the caller patches (``tools.longmem_eval.retrieve``),
    never a monkeypatch object: the patch has to land on the name the SUT calls."""
    chunk = {"id": CHUNK_ID, "content": f"user: {QUOTE}\nthe buyer paid cash.",
             "match_source": "fts"}
    point = {"id": POINT_ID, "content": QUOTE, "match_source": "fts"}
    dup = {"id": DUP_ID, "content": QUOTE, "match_source": "fts"}
    other = {"id": OTHER_ID, "content": "i live in lisbon",
             "match_source": "fts"}
    pool = [dict(chunk), dict(point), dict(dup), dict(other)]

    def _fake(sdk, query, **kw):
        return [dict(h) for h in pool]

    monkeypatch.setattr(target, "hybrid_search", _fake)


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

    # the arm marker + the census (D2: ON only)
    assert on["evidence_assembly"] is True
    stats = on["evidence_assembly_stats"]
    # THE WHOLE CENSUS, not a sample of it: this dict IS the arm's record in the
    # ONLY place the run has one — `retrieve_for_question`'s own return.
    # ⛔ MEASURED, so the next reader does not assume it travels: it does NOT
    # reach the run's artifacts. `run.py` contains no `evidence_assembly`
    # reference at all (`rg -n evidence_assembly tools/longmem_eval/run.py` → 0
    # hits), so the per-outcome dict that mirrors four sibling arm-stat keys
    # (`temporal_leg_stats`, `time_aware_stats`, `coverage_loop_stats`,
    # `session_reinjection_stats`) omits this one, and the Layer-1 projection in
    # `outcomes_to_report` omits it too. The arm state is recorded NOWHERE the
    # eval lane can read it — not the checkpoint, not the Layer-1 report, and not
    # the fingerprint (the `tr_session_admission` / `tr_session_completion`
    # CONFIG FLAGS ride the fingerprint and `r5_knobs` instead; this arm has
    # neither). `run.py` drops the tr_session `_stats` halves too, but the
    # temporal-admission driver persists those in its own rows
    # (`temporal_admission_arms.py:217-218`) — a route this arm has no
    # equivalent of, which is the gap #11467 records.
    # ⛔ CITED by SYMBOL (`e1ba0974e`'s rule for code citations): the numbers
    # that once stood here had drifted out from under them. The numbers below
    # are a READING at `29d67f9cc`, not a citation — re-derive them from the
    # symbol when you need them current. At that commit the four keys sit at
    # `run.py:5396/5431/5440/5451` and its knobs at `:6048-6071` (the two
    # `tr_session_*` CONFIG-FLAG entries at `:1956-1959` in `run.py`, whose
    # block runs on to `:1975` with the four completion budgets). A key name
    # survives a rebase; a line number does not.
    # Hence this pin is the only guard the census has: a key that grows or
    # disappears is otherwise shipped unseen.
    assert set(stats) == {
        "on", "applied", "tr_excluded", "pool_items", "package_items",
        "packages", "collapsed_duplicates", "verbatim_refs_kept",
        "value_first_packages",
    }, sorted(stats)
    assert stats["on"] is True, stats
    assert stats["applied"] is True, stats
    assert stats["tr_excluded"] is False, stats
    assert stats["pool_items"] == 4, stats
    # the point + its own chunk = ONE slot; the cross-session restatement
    # merges into that same package ⇒ 2 packages over 4 pool items.
    assert stats["packages"] == 2, stats
    assert stats["collapsed_duplicates"] == 1, stats
    assert stats["verbatim_refs_kept"] == 1, stats
    assert stats["package_items"] == 3, stats
    # #9092's mark census (#1763/#1945 value classes): NONE of these four hits
    # carries a value mark, so the count is 0 — pinned rather than left
    # unasserted, or a regression that credited every anchor as value-first
    # would read as "census present" in the run record.
    assert stats["value_first_packages"] == 0, stats

    # the READER WINDOW is the package: the duplicate is gone and the point's
    # own chunk sits immediately after its anchor (a flat anchor-then-refs
    # rendering), not at its pool rank.
    on_ids = _ids(on["context_points"])
    assert DUP_ID not in on_ids, (
        "the cross-session restatement must not consume a second window slot")
    assert set(on_ids) == {POINT_ID, CHUNK_ID, OTHER_ID}, on_ids
    # ADJACENCY, not merely ordering: `index(POINT) < index(CHUNK)` is also
    # satisfied by `[POINT, OTHER, CHUNK]`, i.e. by a rendering that is NOT the
    # flat anchor-then-refs shape the comment above names.
    assert on_ids.index(CHUNK_ID) == on_ids.index(POINT_ID) + 1, on_ids
    assert on["context_point_count"] == 3, on["context_point_count"]

    # …and the OFF window really was different (so the assertion above is a
    # differential, not a coincidence of this pool).
    off_ids = _ids(off["context_points"])
    assert set(off_ids) == {CHUNK_ID, POINT_ID, DUP_ID, OTHER_ID}, off_ids

    # the RECALL SURFACE is untouched: packaging shapes the window, never the
    # pool the retrieval metrics are computed over — the SAME dicts, not merely
    # the same ids (a field rewrite would silently change the recorded outcome).
    assert on["hits"] == off["hits"], (
        "the pool (recall surface) must be byte-identical ON vs OFF")
    assert {POINT_ID, CHUNK_ID, DUP_ID, OTHER_ID} <= set(_ids(on["hits"]))


# ── 2. the off-switch: OFF is the untouched historical path ──────────────

@requires_falkordb
def test_the_off_switch_leaves_the_old_path_unchanged(seeded_sdk, monkeypatch):
    """FAIL VALUE: a default that is ON (the #1745 fail-safe decision is OFF),
    an OFF path that emits the arm's keys (D2: the off-path outcome keeps
    today's exact shape), or a switch that only reads the explicit kwarg and
    ignores the AMBIENT env — the only route the eval lane has today
    (`run.py` never sets, passes or fingerprints the arm; #11467).

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
    # the EXPLICIT-FALSE arm keeps today's shape too (D2): the arm keys are
    # ON-only, so a regression that emits them whenever the kwarg is PASSED
    # reds here instead of shipping under window-equality alone.
    assert "evidence_assembly" not in explicit_off, (
        "the OFF path must keep today's exact outcome shape (D2): the arm key "
        "is ON-only, not a False marker")
    assert "evidence_assembly_stats" not in explicit_off, (
        "the OFF path must keep today's exact outcome shape (D2)")

    # garbage never arms (fail-safe, no raising)
    monkeypatch.setenv("TORTOISE_LME_EVIDENCE_ASSEMBLY", "maybe")
    garbage = _r.retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60)
    assert "evidence_assembly" not in garbage
    assert _ids(garbage["context_points"]) == _ids(
        explicit_off["context_points"])

    # …and the AMBIENT env var — the ONLY route the eval lane has today: no
    # `evidence_assembly=` at `run.py`'s call site, no fingerprint entry (the
    # scoping doc records that one at
    # `docs/scoping/2026-09-09-evidence-assembly-wave.md:188`), and no run
    # artifact that records the arm (#11467: neither the checkpoint nor the
    # Layer-1 report) — DOES arm it.
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
    # …and the pin above is not satisfiable by two EMPTY windows: `[] == []`
    # would pass while the R5 machinery emptied the window it protects (the
    # reason `tr_window_fallback` exists), so pin the window's size too.
    assert on["context_point_count"] == 4, _ids(on["context_points"])


# ══════════════════════════════════════════════════════════════════════════
# THE PRODUCT ASK LANE (tortoise/ask_lane.py) — the SIBLING seam
# ══════════════════════════════════════════════════════════════════════════
#
# WHY THIS SECTION EXISTS (the hole #11448 left open on the OTHER seam)
# --------------------------------------------------------------------
# #11448 pinned the EVAL seam's rebind because deleting it left 52 sibling
# tests green. The PRODUCT seam carries the IDENTICAL single-line rebind —
#
#     if evidence_assembly:
#         deduped, _pkg_stats = package_evidence_pool(deduped, mark_for=None)
#         # tortoise/ask_lane.py, in run_ask_lane()
#
# — and nothing drove it: measured on `main` @ `3794677ee38a`, deleting the
# body of that `if` leaves `tests/test_evidence_assembly.py` (30 passed),
# `tests/longmem_eval/test_slice_a_flood_bound.py` (22 passed) and this file
# (3 passed, docker lane) fully GREEN, i.e. an ON-vs-OFF run of the PRODUCT
# lane reads as "Slice A changed nothing". The knob's default is the only
# thing the hermetic suite pinned for it
# (`test_ask_lane_knob_failsafe_off_by_default`, an ``ask_env_bool`` unit
# call — it never enters ``run_ask_lane``), so the arm the issue's `O`
# indicator is about was reachable-but-unpinned on the lane the product claim
# names.
#
# ⛔ The census is NOT the observable here. ``_pkg_stats`` is assigned and
# never read — ``run_ask_lane``'s response carries no assembly key — so a
# product-lane ON run cannot be told from OFF by any field of its outcome.
# The READER WINDOW is the only observable that moves, which is why every
# test below asserts on it: the pool handed to ``assemble_context`` (the
# real window-fill, spied) and, for the flooding test, the text the reader
# actually received. A pin that asserted the census instead would be pinning
# the one thing this lane throws away.
#
# LAYER: HERMETIC — no graph, no model, no network. The ask lane's only
# graph-touching calls are ``sdk.tortoise_fts_query`` and
# ``sdk.annotate_ask_hits``; both are stubbed, and everything after them
# (per-session dedup, A5 boost, A7 rerank, packaging, ``assemble_context``,
# ``render_context``, the reader call) is the REAL product path. Same
# ``_FakeSDK`` shape as ``tests/test_ask_retrieval_budget.py``.

ASK_QUOTE = "the tea set cost 300 dollars"
ASK_POINT_ID = "a2683point"
ASK_CHUNK_ID = "a2683chunk"
ASK_DUP_ID = "a2683dupe"
ASK_OTHER_ID = "a2683other"
ASK_SESSION_1 = "sess2683ask"
ASK_SESSION_2 = "sess2683ask2"
ASK_OTHER_FACT = "i live in lisbon"

#: Every env-gated knob ``run_ask_lane`` reads on its call path that can move
#: the POOL, the WINDOW or the ARM. Scrubbed, not tolerated: an ambient export
#: of ``TORTOISE_ASK_CONTEXT_ITEM_CAP`` would silently re-cut the flooding
#: window this section pins, and ``TORTOISE_ASK_RERANK`` would reorder the
#: pool before packaging — a developer shell carrying either would red a
#: pinned id list. (A spurious red, never a false pass — but a price for
#: nothing.)
_ASK_ENV_SCRUB = (
    # the arm under test
    "TORTOISE_ASK_EVIDENCE_ASSEMBLY",
    # the A5 evidence-mark boost (reorders the pool before packaging)
    "TORTOISE_ASK_EVIDENCE_BOOST",
    "TORTOISE_ASK_EVIDENCE_BOOST_ANSWER_STRING",
    "TORTOISE_ASK_EVIDENCE_BOOST_SOURCE",
    "TORTOISE_ASK_EVIDENCE_BOOST_VERBATIM",
    # the A7 rerank (reorders the pool before packaging; default OFF)
    "TORTOISE_ASK_RERANK",
    "TORTOISE_ASK_RERANK_MODEL",
    "TORTOISE_ASK_RERANK_CAP",
    "TORTOISE_ASK_RERANK_LAMBDA",
    # hosted client mode — `run_ask_lane` REFUSES it (the hosted /v1/ask
    # surface is gone), so an ambient export would red every test here with
    # AskRetrievalUnavailable instead of the behaviour under test
    "TORTOISE_API_URL",
    # the resolved caps — the window this section cuts on purpose
    "TORTOISE_ASK_CONTEXT_ITEM_CAP",
    "TORTOISE_ASK_CONTEXT_TOKEN_CAP",
    "TORTOISE_ASK_CONTEXT_BYTE_CAP",
    "TORTOISE_ASK_RETRIEVAL_LIMIT",
    "TORTOISE_ASK_POOL_SIZE",
)


def _ask_product_pool() -> list[dict]:
    """The PRODUCT lane's hit shape (the fields ``tortoise_fts_query`` emits
    after #8658: ``quote`` / ``source_turn_id`` / session / ``point_kind``),
    with the raw source chunk ranked ABOVE its own distilled point — the
    collapse must be order-independent, not arrival-ordered.

    Four pool items, TWO facts: the tea set (point + its own verbatim chunk +
    a cross-session restatement) and an unrelated statement.
    """
    return [
        {"id": ASK_CHUNK_ID,
         "content": f"user: {ASK_QUOTE}\nthe buyer paid cash.",
         "session_id": ASK_SESSION_1,
         "point_kind": "session-transcript",
         "match_source": "fts"},
        {"id": ASK_POINT_ID, "content": ASK_QUOTE,
         "quote": ASK_QUOTE, "source_turn_id": 2,
         "session_id": ASK_SESSION_1, "match_source": "fts"},
        {"id": ASK_DUP_ID, "content": ASK_QUOTE,
         "session_id": ASK_SESSION_2, "match_source": "fts"},
        {"id": ASK_OTHER_ID, "content": ASK_OTHER_FACT,
         "session_id": ASK_SESSION_1, "match_source": "fts"},
    ]


class _AskFakeSDK:
    """The two calls the ask lane makes against the graph are stubbed; every
    stage after them is the real one."""

    _namespace = "wiring-ask-2683"

    def __init__(self, pool: list[dict]):
        self._pool = pool
        self.query_kwargs: dict = {}

    def _get_proj(self):
        return object()

    def tortoise_fts_query(self, query, **kwargs):
        self.query_kwargs = kwargs
        return [dict(h) for h in self._pool]

    def annotate_ask_hits(self, hits, **kwargs):
        return [dict(h) for h in hits]


class _AskCaptureReader:
    model = "capture"
    provider = "capture"
    route = "capture"
    last_route = "capture"
    last_prompt_tokens = 0
    last_completion_tokens = 0
    last_finish_reason = "stop"

    def __init__(self):
        self.user = None
        self.calls = 0

    def complete(self, *, system, user, max_tokens=None):
        self.calls += 1
        self.user = user
        return "The tea set cost 300 dollars."

    def close(self):
        pass


def _run_ask(monkeypatch, *, item_cap: int | None = None, **env):
    """Drive the REAL ``run_ask_lane`` once, returning
    ``(response, reader, pool_handed_to_the_window_fill)``.

    The window is read at ``assemble_context`` — the lane's own call, on the
    pool it decided to fill from — so the assertion is the mechanism, not a
    mock of it.
    """
    for var in _ASK_ENV_SCRUB:
        monkeypatch.delenv(var, raising=False)
    # ``TORTOISE_ASK_CONTEXT_BYTE_CAP`` is DERIVED from the token cap unless
    # set, so the item cap alone decides what the window can hold here.
    if item_cap is not None:
        monkeypatch.setenv("TORTOISE_ASK_CONTEXT_ITEM_CAP", str(item_cap))
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))

    reader = _AskCaptureReader()
    monkeypatch.setattr(_ask_lane, "_default_ask_reader_factory",
                        lambda: reader)
    seen: dict = {}
    real_assemble = _retrieval.assemble_context

    def _spy(pool, **kwargs):
        seen["pool"] = [dict(h) for h in pool]
        return real_assemble(pool, **kwargs)

    monkeypatch.setattr(_retrieval, "assemble_context", _spy)
    _ask_lane._reset_ask_reader_cache_for_tests()
    try:
        resp = _ask_lane.run_ask_lane(
            _AskFakeSDK(_ask_product_pool()), "how much did the tea set cost",
            question_date="2026-09-25")
    finally:
        _ask_lane._reset_ask_reader_cache_for_tests()
    assert "pool" in seen, "assemble_context was never called"
    return resp, reader, seen["pool"]


# ── 4. the PRODUCT arm REBINDS the ask lane's reader window ──────────────

def test_the_product_arm_rebinds_the_ask_lane_reader_window(monkeypatch):
    """FAIL VALUE: the ON arm handing ``assemble_context`` the RAW pool —
    i.e. the packaging line's body deleted, or its result not rebound to
    ``deduped``. Measured on ``main`` @ `3794677ee38a`: with the body removed,
    this module's other three tests, ``tests/test_evidence_assembly.py``
    (30 passed) and ``tests/longmem_eval/test_slice_a_flood_bound.py``
    (22 passed) all stay GREEN while the product arm silently does nothing.

    Reachable: the pool is the exact four hits ``_ask_product_pool`` returns,
    and the package boundary is known from them (one fact = anchor + ≤ one
    verbatim ref, the cross-session restatement merges into it)."""
    _off_resp, _off_reader, off_pool = _run_ask(
        monkeypatch, TORTOISE_ASK_EVIDENCE_ASSEMBLY=0)
    on_resp, _on_reader, on_pool = _run_ask(
        monkeypatch, TORTOISE_ASK_EVIDENCE_ASSEMBLY=1)

    # OFF is the untouched historical path: the pool arrives as retrieved,
    # chunk still ABOVE its own point.
    assert _ids(off_pool) == [ASK_CHUNK_ID, ASK_POINT_ID, ASK_DUP_ID,
                              ASK_OTHER_ID], _ids(off_pool)

    # ON is the PACKAGE: the restatement loses its slot and the point's own
    # verbatim chunk sits IMMEDIATELY after its anchor (the flat
    # anchor-then-refs shape), not at its pool rank.
    assert _ids(on_pool) == [ASK_POINT_ID, ASK_CHUNK_ID, ASK_OTHER_ID], (
        _ids(on_pool))
    on_ids = _ids(on_pool)
    assert on_ids.index(ASK_CHUNK_ID) == on_ids.index(ASK_POINT_ID) + 1, (
        on_ids)

    # …and the consequence the product actually shows: the restated fact no
    # longer contributes a SECOND session to the window, because it no longer
    # has a slot of its own.
    assert on_resp["retrieved_session_ids"] == [ASK_SESSION_1], (
        on_resp["retrieved_session_ids"])
    assert _off_resp["retrieved_session_ids"] == [ASK_SESSION_1,
                                                  ASK_SESSION_2], (
        _off_resp["retrieved_session_ids"])


# ── 5. the flooding the issue exists to stop, measured on the product lane ─

def test_a_capped_window_holds_a_distinct_fact_where_the_flood_took_it_all(
        monkeypatch):
    """FAIL VALUE: the arm not reaching the reader, measured where it MATTERS
    — a window too small to hold the pool. OFF, the three leading slots are
    all ONE fact (chunk + point + restatement), so the unrelated statement
    starves out of the reader's context; ON, the package frees the slot and
    the distinct fact reaches the reader. That is Slice A's whole product
    claim on the lane the claim names, and it is invisible to any assertion
    that only compares pool ids."""
    _resp, off_reader, _off_pool = _run_ask(
        monkeypatch, item_cap=3, TORTOISE_ASK_EVIDENCE_ASSEMBLY=0)
    _on_resp, on_reader, _on_pool = _run_ask(
        monkeypatch, item_cap=3, TORTOISE_ASK_EVIDENCE_ASSEMBLY=1)

    assert ASK_OTHER_FACT not in off_reader.user, (
        "the OFF window is expected to be flooded by one fact (this is the "
        "pre-fix baseline, not an aspiration): "
        f"{off_reader.user!r}")
    assert ASK_OTHER_FACT in on_reader.user, (
        "the arm must free a window slot for a DISTINCT fact: "
        f"{on_reader.user!r}")
    # …and the one fact is still there — the point was collapsed, not dropped.
    assert ASK_QUOTE in on_reader.user


# ── 6. the product knob's off-switch: fail-safe, and the old path intact ──

def test_the_product_knob_is_fail_safe_and_leaves_the_old_path_unchanged(
        monkeypatch):
    """FAIL VALUE: a default that arms the arm (the #1745 fail-safe decision
    is OFF), or a garbage/zero value that flips it — on the PRODUCT lane the
    knob is the only switch there is (no kwarg, no caller that can pin it),
    so a typo must read as OFF and must leave the historical pool byte-for-
    byte."""
    baseline = None
    for value in (None, "", "0", "false", "no", "maybe"):
        kwargs = {} if value is None else {
            "TORTOISE_ASK_EVIDENCE_ASSEMBLY": value}
        _resp, _reader, pool = _run_ask(monkeypatch, **kwargs)
        ids = _ids(pool)
        assert ids == [ASK_CHUNK_ID, ASK_POINT_ID, ASK_DUP_ID, ASK_OTHER_ID], (
            f"value={value!r} must read as OFF: {ids}")
        if baseline is None:
            baseline = pool
        else:
            assert pool == baseline, (
                f"value={value!r} changed the OFF pool, not merely arming")

    for value in ("1", "true"):
        _resp, _reader, pool = _run_ask(
            monkeypatch, TORTOISE_ASK_EVIDENCE_ASSEMBLY=value)
        assert _ids(pool) == [ASK_POINT_ID, ASK_CHUNK_ID, ASK_OTHER_ID], (
            f"value={value!r} must ARM the arm: {_ids(pool)}")
