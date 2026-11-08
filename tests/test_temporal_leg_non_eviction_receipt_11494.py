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

* ``list(...)[:1]``            (refusals under-reported) — **killed here**
* ``len(...[:0])``             (every guard refusal reverts to
                                ``no_window_candidates``) — **killed here**
* ``... or "none"[:0]``        (traceless rows emit ``""``) — **NOT pinned
                                here**: the fallback is unreachable, because
                                ``temporal_leg_order`` stamps ``"none"`` up
                                front. Residual on #11494.
* ``"accepted": accepted[:1]`` (accepted list truncated) — **NOT pinned
                                here**: ``trace["accepted"]`` is never read
                                by ``retrieve.py``. Residual on #11494.
* the inert branch's ``"on": True`` — **NOT pinned here**: the receipt's
                                ``non_eviction`` reads the RESOLVED switch and
                                ``non_eviction_applied`` reads only
                                ``trace["applied"]``; nothing on the wired
                                path reads ``trace["on"]``. Residual on
                                #11494.

So this module closes TWO of the five, not all five. The other three are dead
writes or unreachable branches on the wired path: answering them means
widening the receipt for a diagnostic no decision asks for, and they are
recorded on #11494 instead of being papered over with a 15th narrower AST pin
— which is the family of pin that was defeated five times already. An AST pin
pins the TEXT and the mutant is the text plus a slice; **observation** is the
only pin a slice cannot defeat, and it is what kills the two receipt slices
this module is built around.

A SIXTH mutant belongs to a different class — a wiring forward rather than a
slice: ``retrieve.py`` dropping ``max_chunks_per_session`` from its
``temporal_leg_fusion_order`` call. The ``#10538`` pins that mention that cap
constrain the FORWARD only as TEXT or one call short: an AST assertion over
``retrieve.py``'s call site (which DOES redden on the deletion — measured), an
AST assertion over the fusion signature's DEFAULT, and the direct
``temporal_leg_fusion_order`` callers, which pass the cap themselves. None runs
``retrieve_for_question``, so none pins that the RUN's value reaches the
guard's VERDICT — which is what
`test_the_guard_judges_the_window_the_RUN_caps` pins against
:data:`CHUNK_BEARING`, where the literal mutant was measured red.

Also pinned here, because they were the two ways this module could pass
vacuously: the stated GEOMETRY (``ceiling == 10``) is asserted from the run
rather than in prose, and the ambient ``TORTOISE_LME_TEMPORAL_LEG_*`` seams
are scrubbed so an exported counterfactual knob cannot red a correct arm.

Hermetic: the pool arrives through a stubbed ``hybrid_search``, the graph
double answers ``[]``, and the props arrive through stubbed annotators. No DB,
no embedder, no reader, no judge.

Run: TORTOISE_TEST_CARVE_OUT=1 uv run pytest \\
       tests/test_temporal_leg_non_eviction_receipt_11494.py -q

⛔ THIS FILE LIVES AT `tests/`, NOT `tests/longmem_eval/` (#10194, owner decision
2026-10-27). The `eval/` and `longmem_eval/` subtrees are subtracted from every
GATING leg, so a pin placed there never runs on a PR: a mutant it kills still
merges green, which is the exact defect #11494 exists to remove. Relocated
here and registered in `config/ci-surfaces.yml` under `eval:` (the `#10538`
sibling pins are the precedent).
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
#: ⛔ Every hit here is ``point_kind="event"``, NOT a raw
#: ``session-transcript`` chunk, so ``temporal_leg._reader_order`` short-circuits
#: on `not any(is_raw_chunk(h))` and the per-session CHUNK cap never applies.
#: This fixture therefore pins the WINDOW half of the guard's wiring and says
#: nothing about the cap half — which :data:`CHUNK_BEARING` and
#: `test_the_guard_judges_the_window_the_RUN_caps` cover instead.
GEOMETRY = {"tr_top_k": 12, "temporal_leg_limit": 2, "temporal_leg_weight": 1.0,
            "temporal_leg_placement": "tail"}

#: The reader window every slice below indexes, DERIVED from the geometry above
#: rather than restated as a literal: a `tr_top_k` edit then moves this one row
#: instead of every slice site (measured: ten slice occurrences, on nine lines).
#: A geometry change still reddens loudly (`ceiling == 10`, and the
#: `base_sessions` fixture self-check), so deriving cannot hide a bad geometry.
WINDOW = GEOMETRY["tr_top_k"]

CLUSTERED = (
    [{"id": f"a{i}", "session_id": "sA", "idx": 0, "content": "alpha"}
     for i in range(10)]
    + [{"id": "b1", "session_id": "sB", "idx": 1, "content": "alpha"},
       {"id": "c1", "session_id": "sC", "idx": 2, "content": "alpha"},
       {"id": "d1", "session_id": "sD", "idx": 3, "content": "alpha"},
       {"id": "e1", "session_id": "sE", "idx": 4, "content": "alpha"}]
)

#: The CHUNK-BEARING pool: the shape that makes the RUN's per-session chunk cap
#: OBSERVABLE, and therefore the only fixture that can pin the
#: `max_chunks_per_session` forward at `retrieve.py`'s fusion call.
#:
#: Six raw chunks of ``sA`` (six of the first twelve candidates: the clustering
#: precondition's "one session owns half or more" holds) followed by ten
#: single-chunk sessions. The arithmetic that makes it discriminating, at the
#: pinned window 12:
#:
#: * ``sA`` is capped to ONE slot at ``cap=1`` and THREE at ``cap=3``, so every
#:   later session sits two slots EARLIER when the cap is tight — ``t9`` is
#:   INSIDE the 12-slot window at ``cap=1`` (0-based reader index 10) and just
#:   OUTSIDE it at ``cap=3`` (index 12);
#: * ``t9`` is a PICK (``temporal_leg_limit=4`` ⇒ ceiling 8, so ``promotable``
#:   starts at index 8 — ``p2..p9`` — and the BUCKET exclusion then drops
#:   ``p2..p5``: ``held_buckets`` is sampled over
#:   ``candidates[:max(head + 1, window)]``, the first TWELVE, i.e. ``sA``'s
#:   five dates plus ``t0..t5``, so those six sessions' buckets are held and the
#:   spread reaches ``p6..p9``), and its visibility decides WHICH ruling
#:   condition refuses it: already-visible (1) or would-evict (3).
#:
#: Both arms REFUSE all four picks (``p6..p9``), so ``applied``, ``picks`` and
#: the first THREE refusal rows are identical — the discriminator is the REASON
#: of the last row, condition (1) vs condition (3). That is why the assertion
#: below reads the reason rather than a count.
CHUNK_BEARING = (
    [{"id": f"a{i}", "session_id": "sA",
      "idx": [0, 1, 2, 3, 4, 0][i], "content": "alpha"}
     for i in range(6)]
    + [{"id": f"p{k}", "session_id": f"t{k}",
        "idx": (1 + k) % 5, "content": "alpha"}
       for k in range(10)]
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
    # A TUPLE, not a list: a mutable class attribute here would be one list
    # shared by every ``_Empty`` — and therefore by every ``_Graph``, every
    # ``_get_proj()`` call and every read in every test — so a later edit that
    # appended a result would leak across reads silently. This double answers
    # nothing, so there is nothing to append to.
    result_set: ClassVar[tuple] = ()


class _Graph:
    def query(self, cypher, params=None):
        del cypher, params
        return _Empty()


class _Sdk:
    """The graph double: answers ``[]`` to every query, so no DB is needed."""

    def _get_proj(self):
        return types.SimpleNamespace(g=_Graph())


#: Every env seam ``retrieve_for_question`` reads for this arm — the master
#: switch included. ``GEOMETRY`` passes the three knobs as explicit args and
#: ``_read`` passes the switch, so no assertion depends on these; the scrub
#: exists so the REASON a row moved is always visible: an exported
#: counterfactual knob (`PLACEMENT=head` is a knob a lane exports on purpose
#: for the documented A/B) would otherwise turn a correct arm into a false red
#: that looks like a broken guard. ``TORTOISE_LME_TEMPORAL_LEG`` is scrubbed
#: too: it is a no-op today because ``_read`` passes ``temporal_leg=True``
#: explicitly, but if that arg were ever dropped an ambient ``=1`` would
#: silently arm the leg and this comment would otherwise claim the seam was
#: covered.
_ENV_SEAMS = (
    "TORTOISE_LME_TEMPORAL_LEG",
    "TORTOISE_LME_TEMPORAL_LEG_NON_EVICTION",
    "TORTOISE_LME_TEMPORAL_LEG_PLACEMENT",
    "TORTOISE_LME_TEMPORAL_LEG_LIMIT",
    "TORTOISE_LME_TEMPORAL_LEG_WEIGHT",
)


@pytest.fixture(autouse=True)
def _scrub_temporal_env(monkeypatch):
    for name in _ENV_SEAMS:
        monkeypatch.delenv(name, raising=False)


def _install_pool(monkeypatch, pool: list[dict],
                  *, point_kind: str = "event") -> None:
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
                  "point_kind": point_kind}
        for c in pool
    }
    monkeypatch.setattr(R, "hybrid_search", lambda *a, **kw: list(hits))
    monkeypatch.setattr(R, "point_props_for_hits", lambda *a, **kw: dict(props))
    monkeypatch.setattr(R, "event_props_for_hits", lambda *a, **kw: {})
    monkeypatch.setattr(R, "_speaker_for_turns", lambda *a, **kw: {})


def _read(monkeypatch, pool: list[dict] | None = None, *,
          enved: str | None = None, cap: int | None = None,
          limit: int | None = None,
          point_kind: str = "event") -> dict:
    """One wired read at the shipped TR geometry, unless ``limit``/``cap``
    override ``GEOMETRY`` (as the cap-forward pin does, deliberately).

    ``pool=None`` resolves to :data:`CLUSTERED` rather than binding the module
    list as a default: a mutable default is the shape a later edit turns into
    cross-test coupling, and nothing here needs the identity.
    """
    _install_pool(monkeypatch, CLUSTERED if pool is None else pool,
                  point_kind=point_kind)
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
    geom = dict(GEOMETRY if limit is None
                else {**GEOMETRY, "temporal_leg_limit": limit})
    if cap is not None:
        # The RUN's per-session raw-chunk cap. Omitted (not defaulted) when the
        # test does not care, so the shared cases keep exercising the shipped
        # default rather than a number restated here.
        geom["max_chunks_per_session"] = cap
    return R.retrieve_for_question(_Sdk(), _question(), ks=(5,), top_k=20,
                                   pool_size=60, temporal_leg=True, **geom)


def _stats(ret: dict) -> dict:
    """The receipt block, or a failure that says WHICH way it is missing.

    ``retrieve.py`` emits a ``temporal_leg_stats`` mapping on EVERY row: the
    pre-block initialiser (``{"on": temporal_leg_on, "applied": False,
    "kind": None, ...}``) runs unconditionally, and the armed literal replaces
    it only when the leg gate admits the row. So a bare truthiness check — or
    even ``on`` — cannot tell the failure modes apart:

    * ``on`` is ``True`` from the INITIALISER whenever the caller armed the
      leg, even if the gate (``is_tr`` / constraint / ``annotated``) then
      admitted nothing. Reading it as "the armed block ran" misdirects the
      next reader to the receipt construction when the fault is the WIRING.
    * ``kind`` is the discriminator that actually works: ``None`` in the
      pre-block shape, the constraint kind (``"interval"`` here) once the
      armed block has run.

    Without these, a broken receipt surfaces as ``KeyError: 'non_eviction'`` a
    few lines later, which names neither fault.
    """
    stats = ret.get("temporal_leg_stats")
    assert stats, f"no temporal_leg_stats block at all: {sorted(ret)}"
    assert stats.get("on") is True, (
        f"the leg never armed — this is the pre-block shape, so every "
        f"assertion below would be vacuous: {stats}")
    # ``kind`` (not ``on``) proves the ARMED block ran: ``on`` comes from the
    # initialiser and is True for any armed caller whose gate admitted nothing.
    assert stats.get("kind") is not None, (
        f"the leg is armed but its gate admitted nothing (is_tr / constraint / "
        f"annotated) — the receipt block never ran, so look at the WIRING, not "
        f"the receipt: {stats}")
    assert "non_eviction" in stats, (
        f"the armed block ran but dropped the receipt field — look at the "
        f"receipt construction, not the wiring: {sorted(stats)}")
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
    assert "d1" not in hit_ids[:WINDOW] and "e1" not in hit_ids[:WINDOW], (
        f"a refused pick reached the reader window: {hit_ids[:WINDOW]}")
    assert "c1" in hit_ids[:WINDOW], (
        f"the session the pick would have cost is still visible: "
        f"{hit_ids[:WINDOW]}")


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
    # …and the leg actually did the work it was blocked from doing. EXACT, not
    # `>= 1`: the budget is 2 and both promotable buckets are refused under the
    # guard, so a waived arm that promoted only one of them is a regression the
    # weaker check would report as "the differential holds".
    assert _leg(off_ret)["count"] == 2, (
        f"with the guard waived the arm must promote both picks: {_leg(off_ret)}")
    assert _leg(off_ret)["reason"] is None, (
        f"a promoting leg has no empty-leg reason: {_leg(off_ret)}")
    assert off_ret["temporal_leg_stats"]["picks"] == 2, off

    # the differential is the SWITCH's, not the pool's: the SAME fixture, one
    # env value apart, ships different windows.
    on_stats = _stats(on_ret)
    assert on_stats["non_eviction"] is True
    assert _leg(on_ret)["count"] == 0
    assert [r["id"] for r in on_stats["non_eviction_rejected"]] == ["d1", "e1"]

    # the waived arm's promoted ids ARE the ones the guard refused above — both
    # of them. `assert promoted` (any one) would stay green on a regression
    # that promoted d1 and dropped e1 while reading as though both landed.
    off_ids = [h["id"] for h in off_ret["hits"]]
    promoted = {pid for pid in ("d1", "e1") if pid in off_ids[:WINDOW]}
    assert promoted == {"d1", "e1"}, (
        f"the waived arm must promote BOTH refused picks: {sorted(promoted)} "
        f"of the window {off_ids[:WINDOW]}")


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
    dropped ``max_chunks_per_session`` survives HERE (measured). The cap half
    is therefore pinned by `test_the_guard_judges_the_window_the_RUN_caps`
    against :data:`CHUNK_BEARING`, where the cap does apply — not claimed here.

    The observable is the one the ruling is written in: the set of evidence
    sessions the reader can see BEFORE any promotion. ``window=12`` holds
    ``sA``\u00d710 + ``sB`` + ``sC``; the two promotable buckets (``sD``, ``sE``)
    sit below it. The unguarded tail splice buys them by evicting BOTH ``sB``
    and ``sC`` — two sessions in, TWO out, so the arm is net-zero on distinct
    evidence and trades real base evidence for it. The guarded arm must keep
    the base set whole and promote nothing.
    """
    base_sessions = {c["session_id"] for c in CLUSTERED[:WINDOW]}
    assert base_sessions == {"sA", "sB", "sC"}, base_sessions

    # ── the defect the ruling forbids, measured on the wired path ──
    unguarded_ret = _read(monkeypatch, enved="0")
    unguarded_ids = [h["id"] for h in unguarded_ret["hits"][:WINDOW]]
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
    guarded_ids = [h["id"] for h in guarded_ret["hits"][:WINDOW]]
    guarded_sessions = {c["session_id"] for c in CLUSTERED
                        if c["id"] in guarded_ids}
    assert base_sessions <= guarded_sessions, (
        f"the guard removed a base evidence session from the reader window: "
        f"lost {sorted(base_sessions - guarded_sessions)}")
    # and it promoted nothing at all — the widening was refused, not traded
    assert guarded_sessions == base_sessions, (
        f"a fully-refused arm must leave the evidence-session set exactly as "
        f"the base window had it: {sorted(guarded_sessions)}")


# ── (5) the guard judges the window the RUN caps ──

def test_the_guard_judges_the_window_the_RUN_caps(monkeypatch):
    """The cap-forward pin: ``max_chunks_per_session`` reaches the GUARD.

    ``retrieve.py``'s fusion call passes the RUN's ``max_chunks_per_session``
    into ``temporal_leg_fusion_order``, whose docstring states why: the guard
    must judge the window the READER receives (post-chunk-cap), because
    judging the pre-cap slice space would let it clear a pick that deletes a
    session from the real window — a false PASS. Nothing observed that
    forward's EFFECT before this test: every OTHER read in this module WAS
    ``point_kind="event"`` (so ``_reader_order`` short-circuits on
    ``not any(is_raw_chunk(h))``, and ``window_arm_retention`` — which only
    delegates to it — sees an unchanged index space because of it), and the
    ``#10538`` pins that mention the cap constrain the FORWARD only as TEXT or
    one call short: ONE reddens on the deletion as TEXT (the AST assertion over
    ``retrieve.py``'s call site), the rest pass the cap to
    ``temporal_leg_fusion_order`` themselves or assert its signature DEFAULT by
    AST. None runs the harness call, so the deletion changed no observed
    VERDICT: the text pin reddened, the behaviour did not.

    ⛔ THE DISCRIMINATOR IS THE REASON VALUE, not a count. On this pool BOTH
    caps refuse the SAME four picks (``p6``..``p9``), with the same ``applied``,
    the same ``picks`` and the same leg reason, and the first THREE refusal
    rows are IDENTICAL. Exactly ONE row moves — ``p9`` — and the only field
    that moves in it is its REASON, from condition (1) to condition (3):

    * ``cap=1`` (a deliberately TIGHT run cap, well below the shipped
      ``DEFAULT_MAX_CHUNKS_PER_SESSION`` of 3 that the control arm uses):
      ``sA``'s six raw chunks collapse to ONE slot, so ``t9`` sits at 0-based
      reader index 10 — inside the 12-slot window, the reader already holds
      it: condition (1), reason ``session_already_visible``;
    * ``cap=3``: ``sA`` keeps three slots, ``t9`` falls to index 12 — OUTSIDE
      the window, so the guard runs its retention trial, which reports that
      the pick would delete ``t8``: condition (3), reason ``evicts:t8``.

    ``cap=3`` is run here to reproduce the DROPPED-FORWARD shape's guard
    input: a deleted kwarg resolves to
    :data:`tortoise.temporal_leg.DEFAULT_MAX_CHUNKS_PER_SESSION`, which the
    assertion below pins to the literal used here, so a change to that default
    reddens this control WITH a message naming the stale literal, rather than
    only as an unexplained reason mismatch. It is not the literal mutant — that
    one also leaves the run's
    own ``_dedup_pool`` at 1 — so the literal mutant was measured separately:
    with the kwarg deleted at ``retrieve.py``'s call site this test fails on
    the ``tight`` assertion below. The second arm is kept because it makes the
    fixture's discriminating power SELF-EVIDENT rather than asserted: an
    editor who changes the pool until nothing flips gets a red here, not a
    silently vacuous pin.

    ⛔ What the literal mutant does and does not redden, measured: across this
    module's five cases only this test fails (4 passed, 1 failed); across this
    module and ``test_temporal_leg_non_eviction_10538.py`` the pre-existing AST
    pin over ``retrieve.py``'s call site also fails (2 failed, 40 passed). The
    deletion was therefore never SILENTLY survivable — it was caught as text.
    What no test observed before this one is the mutant's behavioural
    consequence.
    """
    # The control arm below names the module default literally, so its expected
    # reason (``evicts:t8``) is only valid while that default is 3. Pin it: a
    # moved default still reproduces the mutant's guard input (the cap is
    # ``_D``), but leaves this control's reason literals stale — it then
    # reddens on its OWN assertions with no message saying why.
    from tortoise.temporal_leg import DEFAULT_MAX_CHUNKS_PER_SESSION as _D

    assert _D == 3, (
        f"the control arm passes cap=3 to reproduce a dropped forward, which "
        f"resolves to DEFAULT_MAX_CHUNKS_PER_SESSION={_D}; update the control "
        f"(and its expected reason) to the new default")

    def _reasons(stats: dict) -> list[tuple[str, str, str]]:
        return [(r["id"], r["session"], r["reason"])
                for r in stats["non_eviction_rejected"]]

    # Self-check on the fixture itself: the clustering precondition must hold
    # PRE-cap (6 of the first 12 candidates are sA), or the window arm declines
    # and every assertion below would be vacuous.
    assert sum(1 for c in CHUNK_BEARING[:GEOMETRY["tr_top_k"]]
               if c["session_id"] == "sA") * 2 >= GEOMETRY["tr_top_k"], (
        "this fixture no longer satisfies the clustering precondition, so the "
        "window arm will not fire and the assertions below prove nothing")

    tight_ret = _read(monkeypatch, CHUNK_BEARING, cap=1, limit=4,
                      point_kind="session-transcript")
    tight = _stats(tight_ret)
    assert tight["non_eviction_targeting"] == "window", (
        f"this pin is about the window arm's guard; the target must be the "
        f"date window, not the anchor path: {tight['non_eviction_targeting']}")
    assert _reasons(tight) == [
        ("p6", "t6", "session_already_visible"),
        ("p7", "t7", "session_already_visible"),
        ("p8", "t8", "session_already_visible"),
        ("p9", "t9", "session_already_visible"),
    ], (
        "at this arm's cap of 1 the reader already holds all four picks, so "
        "every refusal is condition (1). A guard judged on a different cap "
        f"moves p9 to condition (3): {_reasons(tight)}")

    # The control arm: the same pool, the same picks, the cap the DROPPED
    # forward silently uses. Only the LAST refusal moves — which is what makes
    # the assertion above a measurement of the forward rather than of the pool.
    loose_ret = _read(monkeypatch, CHUNK_BEARING, cap=_D, limit=4,
                      point_kind="session-transcript")
    loose = _stats(loose_ret)
    assert loose["non_eviction_targeting"] == "window", loose
    assert _reasons(loose)[:3] == _reasons(tight)[:3], (
        "only p9 is supposed to move between the caps; if more moved, the "
        f"fixture is measuring something other than the cap: {_reasons(loose)}")
    assert _reasons(loose)[3] == ("p9", "t9", "evicts:t8"), (
        "the loose-cap arm is the control that proves this fixture CAN flip: "
        f"p9 must land on condition (3) there: {_reasons(loose)}")

    # …and the two arms agree on the LEG's own empty-leg reason too, which the
    # docstring above claims and nothing in this test otherwise reads: a cap
    # that moved the leg out of ``non_eviction_guard`` would be a second
    # difference between the arms riding along with the one being measured.
    assert _leg(tight_ret)["reason"] == _leg(loose_ret)["reason"] == (
        "non_eviction_guard"), (
        "both caps must leave the leg in its empty-leg reason, so the only "
        f"difference measured here is p9's ruling condition: "
        f"tight={_leg(tight_ret)}, loose={_leg(loose_ret)}")

    # …and the two arms agree on everything else, so the flip is a REASON flip
    # and nothing here rides on a count that moved for another cause.
    for field in ("applied", "picks"):
        assert tight[field] == loose[field], (
            f"{field} must not differ between the caps — the discriminator is "
            f"the reason value, not a count: tight={tight[field]} "
            f"loose={loose[field]}")
    assert tight["applied"] is False and tight["picks"] == 0, tight
