"""#11494 — the #10538 non-eviction receipt, pinned by OBSERVATION.

`#11450` shipped the non-eviction guard (`_non_eviction_filter`) for the
DATE-WINDOW temporal arm together with its receipt (`non_eviction`,
`non_eviction_applied`, `non_eviction_targeting`, `non_eviction_rejected`) and
the `non_eviction_guard` empty-leg reason. Fourteen review cycles pinned that
receipt **statically**, because no test in the repo drove
`retrieve_for_question` with the temporal leg armed: the whole block was
defended by AST assertions over `retrieve.py`'s source text.

That ceiling was measured, not assumed: five one-line edits survived every
gate, each a *self-defeating slice inserted inside an already-pinned
expression* —

* ``list(...)[:1]``            (refusals under-reported),
* ``len(...[:0])``             (every guard refusal reverts to
                                ``no_window_candidates``),
* ``... or "none"[:0]``        (traceless rows emit ``""``),
* ``"accepted": accepted[:1]`` (accepted list truncated), and
* the inert branch's ``"on": True`` (wrong only for inert + switch-OFF).

None changes which picks ship, and none is a shape a plausible refactor
produces — so an AST pin cannot catch them: it pins the TEXT, and the mutant
is the text plus a slice. The only pin a slice trick cannot defeat is
**observation**, which is what this module is.

Hermetic: the pool arrives through a stubbed ``hybrid_search``, the graph
double answers ``[]``, and the props arrive through stubbed annotators. No DB,
no embedder, no reader, no judge.

Run: TORTOISE_TEST_CARVE_OUT=1 uv run pytest \\
       tests/longmem_eval/test_temporal_leg_non_eviction_receipt_11494.py -q
"""
from __future__ import annotations

import types
from typing import ClassVar

import pytest

from tools.longmem_eval import retrieve as R

#: Session A is the semantically nearest one and owns TEN of the twelve
#: reader-visible slots; the four other sessions hold one slot each, the last
#: two of them BELOW the reader window. This is the measured failure shape
#: ``#10538`` exists for: the window starves every session but A.
#:
#: Geometry, PINNED EXPLICITLY rather than inherited: ``tr_top_k=12`` ⇒
#: ``window=12``, ``temporal_leg_limit=2`` ⇒ ``ceiling=10``. The two promotable
#: buckets are ``sD``/``d1`` and ``sE``/``e1`` — the only sessions the reader
#: does not already hold — and each is paid for by evicting ``c1``/``sC``.
#:
#: ⛔ Every other hit is ``point_kind="event"``, NOT a raw
#: ``session-transcript`` chunk, so ``temporal_leg._reader_order`` short-circuits
#: on `not any(is_raw_chunk(h))` and the per-session CHUNK cap never applies
#: here. This fixture therefore pins the WINDOW half of the guard's wiring and
#: says nothing about the cap half — recorded as a residual on #11494 rather
#: than claimed.
GEOMETRY = {"tr_top_k": 12, "temporal_leg_limit": 2, "temporal_leg_weight": 1.0,
            "temporal_leg_placement": "tail"}

CLUSTERED = (
    [{"id": f"a{i}", "session_id": "sA", "idx": 0, "content": "alpha"}
     for i in range(10)]
    + [{"id": "b1", "session_id": "sB", "idx": 1, "content": "alpha"},
       {"id": "c1", "session_id": "sC", "idx": 2, "content": "alpha"},
       {"id": "d1", "session_id": "sD", "idx": 3, "content": "alpha"},
       {"id": "e1", "session_id": "sE", "idx": 4, "content": "alpha"}]
)

#: The positive/negative control pool: every visible slot is a DIFFERENT
#: session, so the spread declines to fire (the −3.3 pt shape ``#2976``
#: measured). The guard is never reached, which is what makes the
#: ``non_eviction`` vs ``non_eviction_applied`` distinction observable.
UNCLUSTERED = [
    {"id": f"x{i}", "session_id": f"s{i}", "idx": 0, "content": "alpha"}
    for i in range(12)
]

#: One date per ``lme_session_index`` — ``_annotate_hits`` reads
#: ``dates[props["lme_session_index"]]``, so the surface under test resolves
#: ``session_date`` exactly as production does. Every date is inside the
#: question's own range, so ``_apply_time_window`` keeps the whole pool and the
#: ONLY selection signal left is the clustering condition (#10538's honest
#: scope note: the wired caller derives the bounds the filter already applied).
DATES = ["2023-04-11", "2023-04-12", "2023-04-13", "2023-04-14",
         "2023-04-15"]

QUESTION = "what did I do with the puppy between 2023-04-10 and 2023-04-20?"


def _question() -> dict:
    return {
        "question_id": "q-11494",
        "question": QUESTION,
        "question_type": "temporal-reasoning",
        "question_date": "2023-04-21",
        "haystack_dates": list(DATES),
        "haystack_sessions": [],
        "answer": "",
    }


class _Empty:
    result_set: ClassVar[list] = []


class _Graph:
    def query(self, cypher, params=None):
        del cypher, params
        return _Empty()


class _Sdk:
    """The graph double: answers ``[]`` to every query, so no DB is needed."""

    def _get_proj(self):
        return types.SimpleNamespace(g=_Graph())


#: Every env seam ``retrieve_for_question`` reads for this arm. ``GEOMETRY``
#: passes the first three as explicit args, and ``_read`` passes the fourth, so
#: no assertion depends on these — the scrub exists so the REASON a row moved
#: is always visible: an exported counterfactual knob (`PLACEMENT=head` is a
#: knob a lane exports on purpose for the documented A/B) would otherwise turn
#: a correct arm into a false red that looks like a broken guard.
_ENV_SEAMS = (
    "TORTOISE_LME_TEMPORAL_LEG_NON_EVICTION",
    "TORTOISE_LME_TEMPORAL_LEG_PLACEMENT",
    "TORTOISE_LME_TEMPORAL_LEG_LIMIT",
    "TORTOISE_LME_TEMPORAL_LEG_WEIGHT",
)


@pytest.fixture(autouse=True)
def _scrub_temporal_env(monkeypatch):
    for name in _ENV_SEAMS:
        monkeypatch.delenv(name, raising=False)


def _install_pool(monkeypatch, pool: list[dict]) -> None:
    """Wire ``pool`` into the ONE seam every read reaches, plus the annotators.

    Stubbing ``hybrid_search`` (not the leg, not the receipt) is what makes
    this an END-TO-END test: ``retrieve_for_question`` runs its real
    annotation, its real constraint detection, its real window filter, its real
    fusion and its real receipt building.
    """
    hits = [{"id": c["id"], "content": c["content"]} for c in pool]
    props = {
        c["id"]: {"session_id": c["session_id"],
                  "lme_session_index": c["idx"],
                  "point_kind": "event"}
        for c in pool
    }
    monkeypatch.setattr(R, "hybrid_search", lambda *a, **kw: list(hits))
    monkeypatch.setattr(R, "point_props_for_hits", lambda *a, **kw: dict(props))
    monkeypatch.setattr(R, "event_props_for_hits", lambda *a, **kw: {})
    monkeypatch.setattr(R, "_speaker_for_turns", lambda *a, **kw: {})


def _read(monkeypatch, pool: list[dict] | None = None, *,
          enved: str | None = None) -> dict:
    """One wired read at the shipped TR geometry.

    ``pool=None`` resolves to :data:`CLUSTERED` rather than binding the module
    list as a default: a mutable default is the shape a later edit turns into
    cross-test coupling, and nothing here needs the identity.
    """
    _install_pool(monkeypatch, CLUSTERED if pool is None else pool)
    if enved is not None:
        monkeypatch.setenv("TORTOISE_LME_TEMPORAL_LEG_NON_EVICTION", enved)
    else:
        monkeypatch.delenv("TORTOISE_LME_TEMPORAL_LEG_NON_EVICTION",
                           raising=False)
    # ``temporal_leg=True`` is the point of this module: the leg is tri-state
    # and fail-safe OFF, so without an explicit arm there is no receipt to
    # observe at all (the shipped default leaves the block on the OFF shape).
    #
    # The geometry is passed EXPLICITLY: ``temporal_leg_limit``, ``_weight`` and
    # ``_placement`` each fall back to ``os.environ`` inside
    # ``retrieve_for_question``, and the repo's conftest restores ``TORTOISE_*``
    # but does not CLEAR it — so a lane with ``TORTOISE_LME_TEMPORAL_LEG_PLACEMENT``
    # exported for the documented counterfactual A/B would turn this module's
    # pinned geometry into a false red. ``_read`` scrubs them too (belt and
    # braces: the args win, the scrub keeps the reason legible).
    return R.retrieve_for_question(_Sdk(), _question(), ks=(5,), top_k=20,
                                   pool_size=60, temporal_leg=True, **GEOMETRY)


def _stats(ret: dict) -> dict:
    """The receipt block, or a failure that says WHICH way it is missing.

    ``retrieve.py`` always emits a ``temporal_leg_stats`` mapping — the OFF
    shape (``{"on": False, "applied": False, ...}``) is constructed
    unconditionally and emitted on every row — so a bare truthiness check
    cannot tell "the leg never ran" from "someone broke the receipt": both
    would surface as ``KeyError: 'non_eviction'`` a few lines later, which is
    exactly the wrong message. Assert the two facts the guard needs.
    """
    stats = ret.get("temporal_leg_stats")
    assert stats, f"no temporal_leg_stats block at all: {sorted(ret)}"
    assert stats.get("on") is True and "non_eviction" in stats, (
        "the leg did not reach the armed block, so every assertion below would "
        f"be vacuous — the receipt is the OFF shape: {stats}")
    return stats


def _leg(ret: dict) -> dict:
    legs = [leg for leg in ret.get("legs") or [] if leg.get("leg") == "temporal"]
    assert legs, f"no temporal leg in the trace: {ret.get('legs')}"
    return legs[0]


# ── (1) the receipt is built from a REAL guard run, with exact refusals ──

def test_the_receipt_reports_the_guard_run_and_its_exact_refusals(monkeypatch):
    """THE accepted shape of #11494: drive the wired reader with the leg armed
    and read the receipt OFF THE RUN.

    The assertions that survive only by OBSERVATION — each one is a mutant the
    source-text pins could not see:

    * ``rejected`` must be the EXACT list — id, session AND reason — so
      ``[:1]`` (under-reporting the refusals) fails;
    * the empty-leg ``reason`` must be ``non_eviction_guard``, so
      ``len(...[:0])`` (which drives ``guard_rejected`` back to 0 and the
      reason back to ``no_window_candidates``) fails;
    * ``applied`` must be ``True``, read from the guard's own trace, and
      ``False`` on the inert control below — so a value derived from the knobs
      instead of the trace fails there.

    ⛔ Not claimed here, because they were measured as unobservable: the
    ``targeting or "none"`` fallback is unreachable (``temporal_leg_order``
    always stamps ``"none"`` up front) and ``trace["accepted"]`` is never read
    by ``retrieve.py``. Both are dead writes on the wired path — see the
    residual note on #11494.
    """
    ret = _read(monkeypatch)
    stats = _stats(ret)

    # the stated GEOMETRY, pinned from the run rather than asserted in prose:
    # ``tr_top_k=12`` ⇒ window 12, ``temporal_leg_limit=2`` ⇒ ceiling 10.
    assert stats["ceiling"] == 10, (
        f"the documented ceiling (window 12 - budget 2) is not what ran: {stats}")
    # the switch resolved ON (the ruling's default; #10538 owner decision)
    assert stats["non_eviction"] is True, stats
    # the guard actually RAN on this row — not merely "the rule is on"
    assert stats["non_eviction_applied"] is True, (
        f"the guard did not run on a clustered window arm: {stats}")
    # WHICH target selected the picks — the arm the guard governs
    assert stats["non_eviction_targeting"] == "window", (
        f"the window arm must be the one reporting: {stats}")
    # the arm had real work to do, so an empty pick set is the GUARD's doing
    assert stats["placement"] == "tail", stats

    # ── the refusals, EXACTLY ──
    rejected = stats["non_eviction_rejected"]
    assert [r["id"] for r in rejected] == ["d1", "e1"], (
        f"the guard's refusal list is the receipt's evidence — an "
        f"under-reported list is a silent under-count of what the ruling "
        f"cost: {rejected}")
    assert [r["session"] for r in rejected] == ["sD", "sE"], rejected
    # condition 3 of the ruling: the pick would delete a VISIBLE session
    assert [r["reason"] for r in rejected] == ["evicts:sC", "evicts:sC"], (
        f"each refusal must name the session it would have cost: {rejected}")

    # ── and the run is honest about WHY the leg is empty ──
    assert _leg(ret)["reason"] == "non_eviction_guard", (
        f"a guard-refused arm must not launder itself as a window miss — that "
        f"is the reason field's entire purpose: {_leg(ret)}")
    assert _leg(ret)["count"] == 0, _leg(ret)

    # the ruling held end-to-end: no pick shipped, and the reader window kept
    # every session it started with (the union GREW by nothing and LOST
    # nothing — c1/sC is still visible).
    hit_ids = [h["id"] for h in ret["hits"]]
    assert "d1" not in hit_ids[:12] and "e1" not in hit_ids[:12], (
        f"a refused pick reached the reader window: {hit_ids[:12]}")
    assert "c1" in hit_ids[:12], (
        f"the session the pick would have cost is still visible: {hit_ids[:12]}")


# ── (2) the off-switch reaches the ARM, not just the resolver ──

def test_the_off_switch_reaches_the_arm_end_to_end(monkeypatch):
    """``TORTOISE_LME_TEMPORAL_LEG_NON_EVICTION=0`` must waive the ruling on the
    WIRED path.

    This is the assertion that makes the receipt a live surface rather than a
    stamp: the same fixture that refuses everything above must now PROMOTE, so
    a mutant that hardwires the guard on (or reports the switch without
    routing it) fails here while every ``resolver``-level unit test — which
    calls ``resolve_temporal_leg_non_eviction`` directly — stays green.
    """
    on_ret = _read(monkeypatch)
    off_ret = _read(monkeypatch, enved="0")

    off = _stats(off_ret)
    assert off["non_eviction"] is False, (
        f"the explicit falsy value must waive the rule end-to-end: {off}")
    # the guard did not run, is honest about it, and refused nothing
    assert off["non_eviction_applied"] is False, (
        f"a waived run must not claim enforcement: {off}")
    assert off["non_eviction_rejected"] == [], off
    # …and the leg actually did the work it was blocked from doing
    assert _leg(off_ret)["count"] >= 1, (
        f"with the guard waived the arm must promote: {_leg(off_ret)}")
    assert _leg(off_ret)["reason"] is None, (
        f"a promoting leg has no empty-leg reason: {_leg(off_ret)}")
    assert off_ret["temporal_leg_stats"]["picks"] >= 1, off

    # the differential is the SWITCH's, not the pool's: the SAME fixture, one
    # env value apart, ships different windows.
    on_stats = _stats(on_ret)
    assert on_stats["non_eviction"] is True
    assert _leg(on_ret)["count"] == 0
    assert [r["id"] for r in on_stats["non_eviction_rejected"]] == ["d1", "e1"]

    # the waived arm's promoted ids are the ones the guard refused above.
    off_ids = [h["id"] for h in off_ret["hits"]]
    promoted = [pid for pid in ("d1", "e1") if pid in off_ids[:12]]
    assert promoted, (
        f"the waived arm promoted neither refused pick: {off_ids[:12]}")


# ── (3) "the rule is ON" and "the rule RAN" are different facts ──

def test_an_inert_arm_reports_the_switch_on_but_not_applied(monkeypatch):
    """The ``non_eviction`` / ``non_eviction_applied`` split, observed.

    On a pool whose visible slots are already spread across sessions the spread
    correctly declines, so the guard is never REACHED — the rule is ON, and it
    did not run. That is the shape ``non_eviction_applied`` exists to name, and
    the one a knob-derived implementation ("switch ON and placement is not
    head") gets backwards: it would report ``True`` here.

    ⛔ Not claimed: this does NOT kill the inert branch's ``"on": True``
    mutant. Measured — stamping ``trace["on"] = True`` after every fusion
    leaves all four tests green, because the receipt's ``non_eviction`` reads
    the RESOLVED switch (``retrieve.py``: ``"non_eviction": _tl_non_eviction``)
    and ``non_eviction_applied`` reads only ``trace["applied"]``; nothing on
    the wired path reads ``trace["on"]``. It is a dead write, recorded as a
    residual on #11494 — not a pin this module makes.
    """
    ret = _read(monkeypatch, UNCLUSTERED)
    stats = _stats(ret)
    assert stats["non_eviction"] is True, (
        f"the switch resolves ON regardless of whether the arm fires: {stats}")
    assert stats["non_eviction_applied"] is False, (
        f"an inert arm must not claim the guard was enforced: {stats}")
    assert stats["non_eviction_targeting"] == "none", (
        f"an inert leg has no target — the documented three-value domain must "
        f"hold at this surface, not a blank string: {stats}")
    assert stats["non_eviction_rejected"] == [], stats
    assert _leg(ret)["reason"] == "no_window_candidates", _leg(ret)

    # …and the switch resolution is still reported honestly on an inert arm,
    # where nothing else changes. (This pins the RESOLVED SWITCH, not the
    # trace's ``on`` — see the note above on why the latter is unobservable.)
    off = _stats(_read(monkeypatch, UNCLUSTERED, enved="0"))
    assert off["non_eviction"] is False, (
        f"the resolved switch must follow the env on an inert arm too: {off}")
    assert off["non_eviction_applied"] is False, off


# ── (4) the guard's own gate: it must never admit a session-deleting pick ──

def test_the_wired_arm_never_admits_a_session_deleting_pick(monkeypatch):
    """The ruling measured on the WIRED surface, across both switch positions.

    ``_non_eviction_filter`` is unit-tested elsewhere; what was missing is
    the question asked of the FINISHED read rather than of the filter's inputs.

    ⛔ Scope of this pin, stated because an earlier draft of this docstring
    overclaimed it: it pins the **window** half of the guard's wiring. It says
    NOTHING about the chunk-cap half — every ``CLUSTERED`` hit is
    ``point_kind="event"``, so ``_reader_order`` short-circuits and the
    per-session chunk cap never applies, which means a wiring mutant that
    dropped ``max_chunks_per_session`` would survive here (measured). That is
    the documented false-PASS shape in ``temporal_leg_fusion_order``'s
    docstring, and it is recorded as a residual on #11494, not claimed.

    The observable is the one the ruling is written in: the set of evidence
    sessions the reader can see BEFORE any promotion. ``window=12`` holds
    ``sA``\u00d710 + ``sB`` + ``sC``; the two promotable buckets (``sD``, ``sE``)
    sit below it. The unguarded tail splice buys them by evicting BOTH ``sB``
    and ``sC`` — two sessions in, TWO out, so the arm is net-zero on distinct
    evidence and trades real base evidence for it. The guarded arm must keep
    the base set whole and promote nothing.
    """
    base_sessions = {c["session_id"] for c in CLUSTERED[:12]}
    assert base_sessions == {"sA", "sB", "sC"}, base_sessions

    # ── the defect the ruling forbids, measured on the wired path ──
    unguarded_ret = _read(monkeypatch, enved="0")
    unguarded_ids = [h["id"] for h in unguarded_ret["hits"][:12]]
    unguarded_sessions = {c["session_id"] for c in CLUSTERED
                          if c["id"] in unguarded_ids}
    lost = base_sessions - unguarded_sessions
    # BOTH base sessions, not one: the arm pays two sessions for two picks.
    # Pinned exactly, because the weaker `lost` non-empty check would still
    # pass if a later fixture change quietly halved the measured cost.
    assert lost == {"sB", "sC"}, (
        f"this fixture is supposed to reproduce the eviction the ruling "
        f"forbids (both sB and sC lost); it no longer does, so the assertion "
        f"below would be vacuous: kept {sorted(unguarded_sessions)}")

    # ── the guarded arm keeps every base evidence session ──
    guarded_ret = _read(monkeypatch)
    guarded_ids = [h["id"] for h in guarded_ret["hits"][:12]]
    guarded_sessions = {c["session_id"] for c in CLUSTERED
                        if c["id"] in guarded_ids}
    assert base_sessions <= guarded_sessions, (
        f"the guard removed a base evidence session from the reader window: "
        f"lost {sorted(base_sessions - guarded_sessions)}")
    # and it promoted nothing at all — the widening was refused, not traded
    assert guarded_sessions == base_sessions, (
        f"a fully-refused arm must leave the evidence-session set exactly as "
        f"the base window had it: {sorted(guarded_sessions)}")
