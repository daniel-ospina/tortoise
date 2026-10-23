"""#2976 — TR session-level admission + context rendering (hermetic).

Pins the admission rule that fixes the residual defect in
`daniel-ospina/tortoise#2976`, and the rendering contract the reader sees.

⛔ READ THIS FIRST — the issue's own premise is quoted, not asserted. #2976
measured (2026-09, PRE-#2978) a 12-item window of ~8-token fragments (~97
tokens) with the gold at pool ranks 41–120 and a 0/52 answerable result. That
does NOT reproduce on today's main: #2978 stopped content-less operator nodes
consuming reader slots, so the same 12 slots now carry ~2 171 tokens of real
evidence and admit the gold on 51/52 questions (measured, see
`docs/runbook/2976-temporal-admission-arms.md` §2). The residual defect is
VOLUME — the item cap spends 27% of the 8 000-token budget.

The fixtures here are therefore SYNTHETIC PROPERTY PINS, not a reproduction of
today's numbers: they pin the rule (a bounded window must exclude deep gold;
token-budgeted session admission must include it; a session is completed whole
or not at all), which stays true whatever the per-item token size happens to
be.

Half (a) — ADMISSION. `retrieval.assemble_session_context` makes the TOKEN
budget the binding bound and the SESSION the admission unit
(`retrieval.session_blocks`): blocks appear in best-pool-rank order, a block is
admitted whole or not at all, and a block that does not fit is skipped rather
than terminating the walk.

Half (b) — GRANULARITY. `render_context` is the reader's only view of the
admitted evidence, and it is what makes an admitted session readable: the
`Current Date:` header plus a per-block session date. These tests pin that the
session blocks admitted by (a) reach the reader as *dated, contiguous session
blocks* — not as a scatter of fragments — because "gold point admitted" is not
"enough evidence to answer" (#2976's second defect).

Everything here is a pure function over annotated hit dicts: no DB, no reader,
no judge. Run:

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_2976_session_admission.py -v
"""
from __future__ import annotations

from tortoise.retrieval import (
    assemble_context,
    assemble_session_context,
    estimate_tokens,
    render_context,
    session_blocks,
)

# ── fixtures ────────────────────────────────────────────────────────────────

#: The measured fragment size on this lane: the admitted window's blocks ARE
#: ~8 whitespace words each, largely the `[session N] (session date …)` prefix.
FRAGMENT = "yes"


def _hit(session: int, sid: str, content: str, *, date: str = "2023-01-08",
         idx: int | None = None, has_answer: bool = False) -> dict:
    h = {
        "id": f"lme:q:s{session}:t{idx if idx is not None else 0}",
        "content": content,
        "session_id": sid,
        "lme_session_index": session,
        "session_date": date,
        "has_answer": has_answer,
        "match_source": "fts",
    }
    return h


def _frag_pool(n_sessions: int, *, per_session: int = 3,
               gold_session: int | None = None) -> list[dict]:
    """A pool shaped like the measured one: many tiny fragments, one session
    carrying the gold, ordered so the gold session sits DEEP (rank 41+)."""
    pool: list[dict] = []
    for s in range(n_sessions):
        for i in range(per_session):
            is_gold = gold_session is not None and s == gold_session
            pool.append(_hit(
                s, f"sid-{s}",
                "the museum visit was on this day" if is_gold else FRAGMENT,
                date=f"2023-01-{(s % 27) + 1:02d}",
                idx=i, has_answer=is_gold))
    return pool


# ── (a) the admission defect, and the mechanism that fixes it ───────────────


def test_item_cap_is_the_defect_and_the_token_budget_is_unused():
    """The shipped window: 12 items ≈ 1.2% of an 8 000-token budget.

    The shape #2976 reports (97 tokens for 12 items) is pinned as a PROPERTY,
    not a magic number: an item cap binds while >95% of the token budget is
    untouched. Today's real window is larger per item (#2978) but the property
    is the same one that makes the cap the binding constraint.
    """
    pool = _frag_pool(54)          # 162 fragments — the measured median pool
    window = assemble_context(
        pool, top_k=12, max_context_tokens=8000, context_item_cap=12,
        question_date="2023-06-01")
    assert len(window) == 12
    stats: dict = {}
    assemble_context(pool, top_k=12, max_context_tokens=8000,
                     context_item_cap=12, question_date="2023-06-01",
                     stats=stats)
    assert stats["stopped_by"] == "item_cap"
    assert stats["dropped_by_token_cap"] == 0
    assert estimate_tokens(render_context(window, question_date="2023-06-01")) < 200
    assert estimate_tokens(
        render_context(window, question_date="2023-06-01")) / 8000 < 0.05


def test_gold_at_pool_rank_45_is_excluded_by_the_item_cap_and_admitted_by_session_admission():
    """THE regression this change exists for.

    The gold sits at pool rank 45 (the measured band is 41–120). A 12-item
    window cannot reach it — `gold_admitted == 0`, the measured result. Token-
    budgeted session admission admits it, because the whole pool of fragments
    costs well under the budget.
    """
    pool = _frag_pool(54, gold_session=15)      # gold starts at rank 45
    gold = [h for h in pool if h["has_answer"]]
    assert gold, "fixture must carry gold"
    gold_rank = min(pool.index(h) for h in gold)
    assert gold_rank == 45, gold_rank

    old = assemble_context(pool, top_k=12, max_context_tokens=8000,
                           context_item_cap=12, question_date="2023-06-01")
    assert not [h for h in old if h["has_answer"]], (
        "the 12-item window must NOT reach rank-45 gold (the measured 0/52)")

    stats: dict = {}
    new = assemble_session_context(pool, max_context_tokens=8000,
                                   question_date="2023-06-01", stats=stats)
    assert [h for h in new if h["has_answer"]], (
        "session admission must admit the rank-45 gold")
    assert stats["stopped_by"] is None           # the pool ended, not a cap
    assert stats["sessions_admitted"] == 54
    # and the budget was nowhere near binding
    assert estimate_tokens(
        render_context(new, question_date="2023-06-01")) < 8000


def test_session_blocks_are_ordered_by_best_rank_and_keep_pool_order_inside():
    pool = [
        _hit(7, "sid-7", "alpha", date="2023-02-01", idx=0),
        _hit(3, "sid-3", "beta", date="2023-02-02", idx=0),
        _hit(7, "sid-7", "gamma", date="2023-02-01", idx=1),
        _hit(3, "sid-3", "delta", date="2023-02-02", idx=1),
    ]
    blocks = session_blocks(pool)
    assert [[h["content"] for h in b] for b in blocks] == [
        ["alpha", "gamma"], ["beta", "delta"]]
    assert [b[0]["session_id"] for b in blocks] == ["sid-7", "sid-3"]


def test_session_completion_is_whole_or_none_never_partial():
    """A session's REMAINING material (the depth half) is admitted as one block
    or not at all: half a completed session is how one half of a comparison
    reaches the reader alone."""
    admitted = _hit(0, "sid-a", "lead " * 10, idx=0)            # breadth hit
    extra_fits = _hit(0, "sid-a", "detail " * 200, idx=1)
    extra_huge = _hit(0, "sid-a", "word " * 400, idx=2)
    pool = [admitted, extra_fits, extra_huge]
    stats: dict = {}
    out = assemble_session_context(pool, max_context_tokens=400,
                                   breadth_share=0.25, stats=stats)
    # the breadth hit is in; the depth block does NOT fit → NEITHER extra hit
    # is added, not even the one that would have fit on its own.
    ids = [h["id"] for h in out]
    assert admitted["id"] in ids
    assert extra_fits["id"] not in ids, (
        "a partially completed session is not a session block")
    assert extra_huge["id"] not in ids
    assert stats["sessions_completed"] == 0
    assert stats["dropped_sessions_by_token_cap"] == 1


def test_skip_not_starve_a_block_that_does_not_fit_does_not_end_the_walk():
    """An oversized higher-ranked session must not starve a later smaller one
    (the `assemble_context` oversized-hit contract, lifted to the block grain)."""
    big = [_hit(0, "sid-big", "word " * 400, idx=i) for i in range(3)]
    late = [_hit(1, "sid-late", "the answer is seven days", idx=0)]
    out = assemble_session_context(big + late, max_context_tokens=500)
    assert [h["session_id"] for h in out] == ["sid-late"]
    assert "seven days" in out[0]["content"]


def test_a_claim_text_less_hit_never_opens_a_session_block():
    """#2978 at the session grain: a hit that renders decorations only cannot
    seed a block (it would spend an admission slot and a budget share)."""
    pool = [
        _hit(0, "sid-empty", ""),                 # content-less → skipped
        _hit(1, "sid-real", "the real evidence"),
    ]
    # the pure grouping helper applies NO predicate unless the caller passes
    # one (it is a general grouping primitive)…
    assert [b[0]["session_id"] for b in session_blocks(pool)] == [
        "sid-empty", "sid-real"]
    # …while the ASSEMBLER applies the reader's own #2978 predicate, so the
    # content-less hit never opens a block.
    assert [h["session_id"] for h in
            assemble_session_context(pool, max_context_tokens=8000)] == ["sid-real"]


def test_hits_without_a_session_identity_are_singletons_never_merged():
    a = {"id": "a", "content": "first", "session_date": "", "session_id": ""}
    b = {"id": "b", "content": "second", "session_date": "", "session_id": ""}
    blocks = session_blocks([a, b])
    assert len(blocks) == 2, "two identity-less hits must not become one session"


def test_item_cap_still_available_as_an_explicit_opt_in():
    pool = _frag_pool(10, per_session=3)
    out = assemble_session_context(pool, max_context_tokens=8000, item_cap=6)
    assert len(out) == 6
    stats: dict = {}
    assemble_session_context(pool, max_context_tokens=8000, item_cap=3,
                             stats=stats)
    assert stats["stopped_by"] == "item_cap"


def test_a_depth_block_refused_by_the_item_bound_is_counted_as_item_cap():
    """Each depth refusal keeps its own counter, and ``item_cap`` is reported
    only for a block the ITEM bound refused — not for every depth drop."""
    pool = [
        _hit(0, "sid-a", "alpha"),
        _hit(1, "sid-b", "beta"),
        _hit(0, "sid-a", "gamma", idx=1),
        _hit(1, "sid-b", "delta", idx=1),
    ]
    stats: dict = {}
    out = assemble_session_context(pool, max_context_tokens=8000, item_cap=3,
                                   stats=stats)
    # breadth admitted three hits (a0, b0, a1 — pool rank order), then the
    # breadth walk broke on the cap; sid-b's remaining block does not fit the
    # cap either, so it is refused by the ITEM bound.
    assert [h["content"] for h in out] == ["alpha", "beta", "gamma"]
    assert stats["dropped_sessions_by_item_cap"] == 1
    assert stats["dropped_sessions_by_token_cap"] == 0
    assert stats["dropped_sessions_by_byte_cap"] == 0
    assert stats["stopped_by"] == "item_cap"


def test_a_depth_block_refused_by_the_byte_cap_is_counted_as_byte_cap():
    """A byte-ceiling refusal must never be reported as a token-cap drop: that
    is the mislabel this census exists to prevent (the #2976 argument is "the
    ITEM cap binds and the token budget is unused", so a census that cannot
    name the bound that fired cannot support it)."""
    big = "zebra " * 200                     # 200 words ≈ 1200 rendered bytes
    pool = [
        _hit(0, "sid-a", "alpha"),
        _hit(0, "sid-a", big, idx=1),
    ]
    stats: dict = {}
    # breadth_share 0.02 of 2000 tokens = a 40-token breadth budget, so the big
    # hit is refused in breadth; the DEPTH pass then reaches it with the full
    # 2000-token budget (the token bound does NOT refuse it) and the byte
    # ceiling does.
    out = assemble_session_context(pool, max_context_tokens=2000,
                                   breadth_share=0.02, byte_cap=800,
                                   stats=stats)
    assert [h["content"] for h in out] == ["alpha"]
    assert stats["dropped_sessions_by_byte_cap"] == 1
    assert stats["dropped_sessions_by_token_cap"] == 0
    assert stats["dropped_sessions_by_item_cap"] == 0
    assert stats["stopped_by"] == "byte_cap"


def test_accounting_matches_render_context():
    """`words_used` must be the words the reader actually consumes — the
    R1 #1540 accounting invariant, kept by the session path."""
    pool = _frag_pool(5, per_session=2)
    stats: dict = {}
    out = assemble_session_context(pool, max_context_tokens=8000,
                                   question_date="2023-06-01", stats=stats)
    rendered = render_context(out, question_date="2023-06-01")
    assert stats["words_used"] == len(rendered.split())
    assert estimate_tokens(rendered) == int(len(rendered.split()) * 1.1)


# ── (b) the granularity half: what the reader actually reads ────────────────


def test_admitted_session_blocks_render_contiguous_and_dated():
    """The oracle's win is that it serves whole DATED sessions; the reader must
    see a session's date on every block and read its completed material as ONE
    contiguous unit adjacent to the session's last breadth hit (an interval
    needs both dated instances)."""
    a0 = _hit(0, "sid-a", "I visited the MoMA today",
              date="2023-01-08", idx=0)
    b0 = _hit(1, "sid-b", "we saw the Ancient Civilizations exhibit",
              date="2023-01-15", idx=0)
    # big enough that the breadth pass (5% of 8 000) cannot take it — it enters
    # only through the DEPTH pass, adjacent to sid-a's last breadth hit.
    a1 = _hit(0, "sid-a", "crowded galleries " * 180,
              date="2023-01-08", idx=1)
    out = assemble_session_context([a0, b0, a1], max_context_tokens=8000,
                                   breadth_share=0.05,
                                   question_date="2023-06-01")
    # rank order with sid-a's depth block adjacent to its last breadth hit
    assert [h["id"] for h in out] == [a0["id"], a1["id"], b0["id"]]
    text = render_context(out, question_date="2023-06-01")
    assert text.startswith("Current Date: 2023-06-01")
    assert "(session date 2023-01-08)" in text
    assert "(session date 2023-01-15)" in text
    assert "MoMA" in text and "Ancient Civilizations" in text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0].startswith("Current Date:")
    assert "[session 0]" in lines[1] and "[session 0]" in lines[2]
    assert "[session 1]" in lines[3]


def test_deterministic_same_input_same_output():
    pool = _frag_pool(12, per_session=3, gold_session=4)
    a = assemble_session_context(pool, max_context_tokens=8000,
                                 question_date="2023-06-01")
    b = assemble_session_context(pool, max_context_tokens=8000,
                                 question_date="2023-06-01")
    assert [h["id"] for h in a] == [h["id"] for h in b]


def test_mixed_granularity_keeps_fragment_coverage_when_the_head_is_deepened():
    """The shape the completion pass produces: a few TOP sessions are deep
    (~1 700 tokens each, whole sessions), the rest are still cheap fragments.

    Breadth is spent first in RANK order (so a cheap fragment deep in the pool
    still enters), and depth then completes the sessions breadth touched until
    the budget runs out. Deepening the head must not cost the coverage the
    breadth pass buys — measured on the real lane, whole-block-first admission
    dropped gold that the rank walk had reached.
    """
    deep = []
    for s in range(4):                        # four whole sessions (~1 700 tok)
        deep += [_hit(s, f"deep-{s}", "word " * 150, date=f"2023-03-0{s + 1}",
                      idx=i) for i in range(12)]
    frags = _frag_pool(47, gold_session=15)
    for h in frags:
        h["session_id"] = f"frag-{h['session_id']}"
    pool = deep + frags
    stats: dict = {}
    out = assemble_session_context(pool, max_context_tokens=8000,
                                   question_date="2023-06-01", stats=stats)
    # no session is ever PARTIALLY completed: every session's admitted count is
    # either 0, its breadth prefix only, or its full block
    sizes = {}
    for h in pool:
        sizes[h["session_id"]] = sizes.get(h["session_id"], 0) + 1
    for sid, total in sizes.items():
        n = sum(1 for h in out if h["session_id"] == sid)
        assert n in (0, total), (sid, n, total)
    # the breadth pass spends the unused half of the budget the 12-item cap
    # left on the table — the admission half of #2976
    assert stats["breadth_items"] > 12, stats
    assert stats["words_used"] <= 8000 / 1.1
    assert stats["sessions_completed"] >= 1


# ── review round 2: identity-less hits, depth order, census truthfulness ────


def test_identity_less_hits_are_singletons_and_are_still_completed():
    """A hit with no `id` must still be selectable and must not fuse unrelated
    material into one fabricated session.

    Before the fix `selected_ids` held `None`, so every later id-less hit read as
    already-selected: the depth pass completed NOTHING while the census reported
    no cap dropping anything — the operator would blame the budget for a depth
    pass that never ran.
    """
    a0 = {"content": "alpha " * 20, "session_id": "s1",
          "lme_session_index": 0, "session_date": "2023-01-01"}
    a1 = {"content": "alpha detail " * 20, "session_id": "s1",
          "lme_session_index": 0, "session_date": "2023-01-01"}
    b0 = {"content": "beta " * 20, "session_id": "s2",
          "lme_session_index": 1, "session_date": "2023-01-02"}
    # breadth budget (60 est. tokens) takes a0+b0 but not a1, so a1 can only
    # arrive through the DEPTH pass — which is the half the id-less bug killed.
    stats: dict = {}
    out = assemble_session_context([a0, b0, a1], max_context_tokens=120,
                                   breadth_share=0.5, stats=stats)
    assert [h["content"].split()[0] for h in out] == ["alpha", "alpha", "beta"]
    assert stats["sessions_completed"] == 1
    assert out[1]["content"].startswith("alpha detail")

    # no session identity at all → each hit is its OWN session block
    x = {"content": "x", "session_date": ""}
    y = {"content": "y", "session_date": ""}
    assert len(session_blocks([x, y])) == 2, "id-less hits must not be fused"


def test_depth_visits_sessions_by_first_admitted_breadth_rank():
    """Depth order is FIRST ADMITTED breadth rank, not best pool rank.

    `_try_admit` is skip-and-continue, so session A's best-ranked hit can be
    refused by the breadth budget while a later A hit is admitted AFTER B's.
    Visiting by best-pool-rank would then complete A instead of B — a different
    reader window than the documented rule promises, and the delivered numbers
    would describe an algorithm the code does not implement.
    """
    # Pool order: A's best hit (rank 0, too big for the breadth share), B's
    # lead (rank 1), A's later hit (rank 2), B's detail (rank 3, too big for
    # breadth). Only ONE of the two depth blocks can fit the total budget.
    a_best = _hit(0, "sid-a", "alpha lead " * 95, idx=0)      # ~209 tok
    b0 = _hit(1, "sid-b", "beta lead", idx=0)
    a1 = _hit(0, "sid-a", "alpha later", idx=1)
    b1 = _hit(1, "sid-b", "beta detail " * 100, idx=1)        # ~220 tok
    stats: dict = {}
    out = assemble_session_context(
        [a_best, b0, a1, b1], max_context_tokens=400, breadth_share=0.5,
        stats=stats)
    contents = [h["content"][:11] for h in out]
    # B's lead was the FIRST hit admitted in breadth (A's best was refused), so
    # B's block is attempted first and completes; A's block is the one dropped.
    assert any(c.startswith("beta detail") for c in contents), (contents, stats)
    assert not any(c.startswith("alpha lead") for c in contents), (contents, stats)
    assert stats["sessions_completed"] == 1
    assert stats["dropped_sessions_by_token_cap"] == 1


def test_a_cap_that_is_reached_but_dropped_nothing_is_not_reported():
    """`stopped_by` must name a bound that DROPPED material — never one that
    was merely reached (a false `item_cap` would mimic the issue's own defect
    signature on a window where the whole pool was admitted)."""
    pool = [_hit(0, "sid-a", "alpha", idx=0),
            _hit(1, "sid-b", "beta", idx=1)]
    stats: dict = {}
    out = assemble_session_context(pool, max_context_tokens=8000, item_cap=2,
                                   stats=stats)
    assert len(out) == 2
    assert stats["dropped_sessions_by_item_cap"] == 0
    assert stats["stopped_by"] is None, stats


def test_tr_complete_sessions_splices_adjacent_and_never_mutates_the_pool(monkeypatch):
    """The round-1 HIGH, pinned as a SUCCESS-path regression.

    A successful completion must (a) splice the session's material adjacent to
    that session's last base hit, (b) return a NEW list, and (c) leave the input
    pool untouched — rebinding the pool is what made `ret["hits"]` (and every
    pool-derived metric) count injected material as retrieved.
    """
    from tools.longmem_eval import retrieve as R
    from tortoise import session_reinjection as SR

    base = [_hit(0, "sid-a", "a0", idx=0), _hit(1, "sid-b", "b0", idx=0),
            _hit(0, "sid-a", "a1", idx=1)]
    injected = {"lme:q:s0:t9": {"content": "a2-injected", "session_id": "sid-a",
                                "lme_session_index": 0}}

    class _Seed:
        session_id = "sid-a"
        point_id = "lme:q:s0:t0"

    monkeypatch.setattr(SR, "seeded_sessions", lambda *a, **k: [_Seed()])
    monkeypatch.setattr(
        SR, "source_session_chunk_pass",
        lambda *a, **k: {"ok": True, "dropped_by_cap": 0, "total_cap_hit": False,
                         "by_session": {"sid-a": [{"id": "lme:q:s0:t9"}]}})
    monkeypatch.setattr(R, "point_props_for_hits",
                        lambda proj, ids: {i: injected[i] for i in ids})
    monkeypatch.setattr(R, "_speaker_for_turns", lambda proj, ids: {})

    class _Sdk:
        def _get_proj(self):
            return object()

    out, report = R._tr_complete_sessions(
        _Sdk(), list(base), ["2023-01-01", "2023-01-02"], seed_window=2,
        seed_sessions=1, total_cap=5, per_session_cap=5)
    assert report["ok"] is True and report["applied"] is True
    assert report["injected"] == 1
    # (b)+(c): new list, input untouched
    assert out is not base and base == [_hit(0, "sid-a", "a0", idx=0),
                                        _hit(1, "sid-b", "b0", idx=0),
                                        _hit(0, "sid-a", "a1", idx=1)]
    # (a): adjacent to sid-a's LAST base hit (index 2), so the block is whole
    assert [h["content"] for h in out] == ["a0", "b0", "a1", "a2-injected"]


def test_a_breadth_token_drop_is_reported_as_the_token_cap():
    """The breadth budget is a real drop counter: reporting `None` there said
    "no bound dropped anything" on a window the token budget had just narrowed,
    which is the opposite of what the census is for."""
    pool = [_hit(i, f"sid-{i}", "word " * 60, idx=0) for i in range(30)]
    stats: dict = {}
    out = assemble_session_context(pool, max_context_tokens=600,
                                   breadth_share=0.5, stats=stats)
    assert len(out) < 30
    assert stats["dropped_by_breadth_bound"] > 0
    assert stats["stopped_by"] == "token_cap", stats


def test_a_breadth_byte_refusal_is_reported_as_the_byte_cap():
    """A breadth refusal by the BYTE ceiling must not be reported as
    `token_cap`: a caller tuning the token budget while the byte ceiling keeps
    material out is the false positive this census exists to prevent."""
    small = _hit(0, "sid-a", "alpha", idx=0)
    big = _hit(1, "sid-b", "word " * 2000, idx=0)
    stats: dict = {}
    out = assemble_session_context([small, big], max_context_tokens=20000,
                                   byte_cap=800, stats=stats)
    assert [h["id"] for h in out] == [small["id"]]
    assert stats["dropped_by_breadth_byte_cap"] == 1
    assert stats["dropped_by_breadth_token_cap"] == 0
    assert stats["stopped_by"] == "byte_cap", stats


def test_a_breadth_refusal_that_depth_recovers_is_not_a_drop():
    """The breadth phase is capped at `breadth_share`, so it refuses routinely —
    and depth then re-admits those hits from the same session blocks. A census
    that counted the phase-1 refusal reported `stopped_by="token_cap"` on a
    window where the whole pool was admitted and the budget never bound.

    The fixture MUST make breadth actually refuse (several hits per session, so
    depth has something to recover): one hit per session refuses nothing and the
    test would pass on the unfixed code.
    """
    pool = [_hit(s, f"sid-{s}", "alpha beta gamma delta " * 3, idx=i)
            for i in range(5) for s in range(4)]
    stats: dict = {}
    out = assemble_session_context(pool, max_context_tokens=600,
                                   breadth_share=0.2, stats=stats)
    # breadth refused the tail of every session …
    assert stats["breadth_items"] < len(pool), stats
    # … and depth put every one of those hits back, so nothing was dropped
    assert len(out) == len(pool), (len(out), stats)
    assert stats["dropped_by_breadth_bound"] == 0, stats
    assert stats["stopped_by"] is None, stats

    # CONTROL: tighten the budget so depth cannot recover the refusals — now the
    # budget really did drop material and the census must say so.
    tight: dict = {}
    out2 = assemble_session_context(pool, max_context_tokens=400,
                                    breadth_share=0.1, stats=tight)
    assert len(out2) < len(pool)
    assert tight["dropped_by_breadth_bound"] > 0, tight
    assert tight["stopped_by"] == "token_cap", tight
