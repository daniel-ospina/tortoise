"""#4509 — the S3 link-before-create prior lookup must exclude a capture's own
turn echoes BEFORE ``limit`` truncates, not after it.

Defect: ``tortoise_fts_query`` truncates internally (``result_ids[:limit]``)
and applies its one exclusion (``exclude_status``) BEFORE that cut — documented
that way so filtering cannot silently shrink the result count (epic #898). The
extractor's #2552 fix (``extractor_v2._fts_rows``) dropped the capture's own
turn-echo rows AFTER the cut and refilled from a finite over-fetch window
(``_PRIOR_OVERFETCH``). A session can hold ``MAX_SESSION_TURNS`` (500) turns, so
whenever the echoes outnumber ``limit + _PRIOR_OVERFETCH`` and sort ahead of a
real prior, the prior is silently absent from the S3 prior set — the extracted claim
is ADDed instead of folded, producing a duplicate memory Point.

Fix: the exclusion is a caller-supplied, OPT-IN pre-truncation filter in the
retrieval layer (``exclude_turn_echo_session``, same seam as
``exclude_status``), and ``_PRIOR_OVERFETCH`` / the manual refill are deleted.

The fix is PARTIAL, and the residual is pinned rather than asserted away: the
filter only reaches candidates already in the fused candidate set, so the
starved-prior bound MOVES to that set (each leg's ``DEFAULT_POOL_SIZE``, times the
live legs) instead of disappearing at 500 turns. See ``_DOCUMENTED_PROD_POOL``
and ``test_production_pool_bound_still_starves_the_prior`` below.

These tests are HERMETIC (per-test embedded store, no provider keys, no
network): the query embedder is pinned to None so the sparse leg is the only one
submitted and ranking is that leg's own ordering, and ``TORTOISE_POOL_FLOOR`` is
deleted so a caller's environment cannot move the window these bounds are
measured against.

#10405 / #10240 note — the ONE ordering note for this module: the sparse leg's
ordering is the BM25 lexical arm, which since #10240 is ON by default, and it is
a RELEVANCE ranking (k1=1.2, b=0.75) — not the tie-broken all-equal ordering the
pre-#10240 shipped scorer produced. So WHICH prior the bound starves is the
LEXICALLY WEAKEST row (``_PRIOR_TEXT``), not the oldest: before #10240 every row
tied and the shared recency-then-id tie (``search_engine.recency_tie_clause``)
decided it, so the starved row was the OLDEST (#10121; and #3019's ``id ASC``
before it) — against a relevance ranking that tie does not fire. Both bounds
below are measured against a prior that the echo wall OUTRANKS, so the fixture's
prior must be the WEAKEST lexical match, which is what ``_PRIOR_TEXT`` constructs
and documents. The assertions themselves are unchanged: what the arm re-ordered
is which prior sits outside the window, never the seam or the bound.
"""
from __future__ import annotations

import contextlib
import os
import tempfile

import pytest

from tortoise.retrieval import DEFAULT_POOL_SIZE, is_turn_echo_row
from tortoise.sdk import TortoiseSDK

QUERY = "zephyr launch date"

#: The deleted #2552 window: ``_PRIOR_OVERFETCH = 12``, with the extractor's
#: default ``limit=3``. Reproduced here ONLY to prove the OLD algorithm starves
#: the prior on this fixture — the source constant is gone (#4509).
_OLD_PRIOR_OVERFETCH = 12
_OLD_LIMIT = 3

#: 150 echoes is an ORDER OF MAGNITUDE above the old window (3 + 12 = 15).
_HOT = 50
_COLD = 100
_ECHOES = _HOT + _COLD
#: A pool wide enough that the real prior is provably a CANDIDATE — so what the
#: test measures is the pre-truncation exclusion, never pool depth.
_POOL = 200

#: The PRODUCTION pool bound this residual is documented against. A literal on
#: purpose: the comments in ``extractor_v2.py`` and ``sdk.py`` name this bound, so
#: a change to ``DEFAULT_POOL_SIZE`` must fail the pin below and force those
#: comments to be updated with it. This is the same convention the deleted
#: ``_PRIOR_OVERFETCH == 12`` pin used — a declared bound that cannot silently
#: drift out from under its own documentation.
#:
#: It is ONE LEG's window, not the fused set. The ``_hermetic_pool_and_legs``
#: autouse fixture makes FTS the only live leg, which is what fixes this bound to
#: a single number; in the hybrid shape the fused set is the UNION of the fts and
#: vector legs (~2x this), so this literal and the prose that names it both say
#: "per leg". That fixture also deletes ``TORTOISE_POOL_FLOOR``, which widens the
#: same window from the environment.
_DOCUMENTED_PROD_POOL = 120


def _weak_prior(tag: str) -> str:
    """A REAL prior that is the LEXICALLY WEAKEST match on its fixture.

    It carries the query tokens exactly once (so it is a genuine candidate of
    the fused pool) and is padded with ``40`` non-query tokens, so BM25's length
    normalisation (``b=0.75``) pushes it below every echo in the wall.

    WHY padding is needed at all (#10405, #10240): the sparse leg is the BM25
    arm, ON by default since #10240, and BM25 is a relevance ranking. Its length
    NORMALISATION (``b=0.75``) is what prices that length, so the query-twice
    prior this module used to seed scored 0.014157 and ranked FIRST of 151, ahead
    of every echo (11-token, query three times: 0.013835; 5-token, query once:
    0.011189 — token counts as the shipped ``keep_numeric=False`` tokeniser sees
    them, which drops the bare turn index). MEASURED with
    ``tortoise.bm25.bm25_rank`` on the ``_ECHOES`` fixture. ``k1=1.2``'s
    term-frequency SATURATION bounds how much a 3x repetition can earn, but it is
    NOT what decides this ordering: measured with ``k1`` effectively removed
    (1000) the query-twice prior still ranks 0 of 151, while with ``b=0`` it
    drops to 50 of 151 — so ``b`` alone produces the inversion, and the property
    that matters here is the padding, not the repetition.
    Diluting the prior (query once, then filler) puts it at rank 150 of 151, the
    last row — the wall outranks it on relevance, which is the shape these bounds
    are about (a prior captured earlier in the session, then the session keeps
    talking). A prior that ranks first cannot be starved by any window, so the
    old prior made the premise of every bound below unconstructible rather than
    merely stale — it relied on the pre-#10240 scorer ranking every row EQUAL,
    which is not a relevance decision at all. The filler is deliberately NOT
    query text: it must add length, never term frequency.
    """
    return f"{QUERY} " + " ".join(f"{tag}{i}" for i in range(40))


#: The REAL prior's text used by both wall fixtures.
_PRIOR_TEXT = _weak_prior("noted")


@pytest.fixture(autouse=True)
def _hermetic_pool_and_legs(monkeypatch):
    """Pin BOTH knobs this module's bound depends on.

    The sparse leg: with ``EmbeddingModel.get`` → None the vector strategy is
    never submitted, so FTS is the only leg and the ordering is deterministic
    without a model download — and the fused set is then one leg's window, which
    is what makes the residual pin's bound a single number.

    The pool floor: ``resolve_pool_size`` honours ``TORTOISE_POOL_FLOOR``, so an
    ambient value in a developer's shell moves the very window the residual pin
    measures — and it reds the pin for a CORRECT product, because a raised floor
    legitimately lets the prior into the pool. A verdict that depends on the
    caller's environment is a harness defect (#5049 doctrine), so the module
    deletes the variable and measures the product default it documents.
    """
    monkeypatch.delenv("TORTOISE_POOL_FLOOR", raising=False)
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get",
                        staticmethod(lambda load_timeout=None: None))


@pytest.fixture
def sdk():
    from tests._embedded import register_session_tmpdir

    tmpdir = tempfile.mkdtemp(prefix="tortoise_prior_bound_4509_")
    # #4096: the tree is registered for session-scoped reclamation rather than
    # removed by a local finalizer — a local `rmtree` would run before the session
    # teardown that keeps the tree as evidence, and could orphan a live
    # redislite server (the same reason `conftest.py`'s shared fixture registers
    # instead of deleting).
    register_session_tmpdir(tmpdir)
    s = TortoiseSDK(os.path.join(tmpdir, "test.db"))
    with contextlib.suppress(Exception):  # fresh store has nothing to clear
        s._get_proj().g.query("MATCH (n) DETACH DELETE n")
    try:
        yield s
    finally:
        s.close()


def _seed_echo(s, session_id: str, i: int, body: str) -> None:
    s.create_point("event", body, id=f"{session_id}_t{i}", is_episodic=True)


def _seed_echo_wall(s, session_id: str, prior_id: str) -> None:
    """The #4509 fixture: ``_ECHOES`` turn Points for ``session_id`` plus ONE
    real prior. The echoes contain the query tokens; the prior does too, so it
    is a genuine candidate of the fused pool.

    The rows are NOT tied against the shipped scorer: the sparse leg is the BM25
    arm (#10240, ON by default), so the fused order is a RELEVANCE order and the
    prior is deliberately the weakest match (``_PRIOR_TEXT``) — that is what puts
    it below the echo wall, and it is why seed ORDER no longer decides anything
    under the DEFAULT arm. Before #10240 every row scored alike and the order
    came from the recency-then-id tie (#10121), so this fixture placed the prior
    by seeding it between the two bands. The ``_HOT``/``_COLD`` banding is KEPT
    because that is still what places the prior when the arm is opted out
    (``TORTOISE_BM25_LEXICAL=0``): there the rows tie again, and the ``_COLD``
    band written after the prior holds the newer stamps that fill the over-fetch
    window ahead of it. The fixture therefore holds under BOTH arm states."""
    for i in range(_HOT):
        _seed_echo(s, session_id, i, f"[user] {QUERY} {QUERY} {QUERY} turn {i}")
    s.create_point("statement", _PRIOR_TEXT, id=prior_id)
    for i in range(_HOT, _ECHOES):
        _seed_echo(s, session_id, i, f"[user] {QUERY} turn {i}")


def _ids(rows):
    return [r["id"] for r in rows]


# ── The load-bearing test ──────────────────────────────────────────────────

def test_echoes_an_order_of_magnitude_above_the_old_window_still_return_the_prior(
        sdk):
    """#4509 acceptance 3: a session whose echoes outnumber the prior window by
    an order of magnitude still returns the real prior.

    FALSIFIER — (1) *what value makes this test fail?* The presence of
    ``zz_pt_real`` in the exclusion call. (2) *does the fixture contain a row
    where that value is reachable?* Yes: ``zz_pt_real`` is seeded and asserted
    to be a candidate of the same fused pool (``pool_size=_POOL``) BEFORE the
    exclusion — so the only thing that can remove it from the pre-exclusion
    window is the new pre-truncation filter, and the only thing that can fail
    to surface it is the OLD after-the-cut drop.

    The OLD algorithm is reproduced inline: over-fetch ``limit + 12``, drop the
    session's echoes, refill. It returns NOTHING here — the exact starvation the
    issue names — while the new opt-in call returns the prior.
    """
    _seed_echo_wall(sdk, "s1", "zz_pt_real")

    # The prior IS a candidate of the fused pool — not merely absent because the
    # pool was too shallow. (Same call shape as the OLD over-fetch, wide pool.)
    deep = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=_POOL, pool_size=_POOL))
    assert "zz_pt_real" in deep, (
        "fixture invalid: the prior must be a fused-pool candidate, "
        f"otherwise this test measures pool depth, not #4509 (depth={len(deep)})")
    assert len([i for i in deep if i.startswith("s1_t")]) == _ECHOES

    # OLD path (deleted code, reproduced): ask for limit + _PRIOR_OVERFETCH,
    # then drop this session's echoes and take the first `limit`.
    old_fetch = sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=_OLD_LIMIT + _OLD_PRIOR_OVERFETCH)
    assert len(old_fetch) == _OLD_LIMIT + _OLD_PRIOR_OVERFETCH, (
        "fixture invalid: the OLD window must be fully consumed by echoes so "
        "the refill has nothing left to surface")
    old_result = [r for r in old_fetch
                  if not is_turn_echo_row("s1", r)][:_OLD_LIMIT]
    assert old_result == [], (
        "the OLD algorithm must PROVABLY starve the prior on this fixture; "
        f"it returned {_ids(old_result)}")
    assert "zz_pt_real" not in _ids(old_fetch), "OLD window leaked the prior"

    # NEW path: one opt-in argument, and the prior comes back.
    new = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=_OLD_LIMIT, pool_size=_POOL,
        exclude_turn_echo_session="s1"))
    assert "zz_pt_real" in new, f"prior starved after the fix: {new}"
    assert not any(i.startswith("s1_t") for i in new), (
        f"an echo leaked into the prior set: {new}")
    assert len(new) <= _OLD_LIMIT


# ── Opt-in: unpassed callers are unchanged ─────────────────────────────────

def test_exclusion_is_opt_in_and_scoped_to_the_named_session(sdk):
    """#4509 acceptance 4: no behaviour change for callers that do not opt in.

    FALSIFIER — (1) *what value makes this test fail?* The id list of the
    default and other-session calls (they must both contain the three echoes);
    a default-on or over-broad exclusion drops them and fails. (2) *reachable?*
    Yes: three echo rows and one prior are seeded, all matching the query, and
    all four fit in the window.
    """
    for i in range(3):
        _seed_echo(sdk, "s1", i, f"[user] {QUERY} turn {i}")
    sdk.create_point("statement", QUERY, id="pt_real")

    default = _ids(sdk.tortoise_fts_query(QUERY, entity_type="point", limit=10))
    assert {"s1_t0", "s1_t1", "s1_t2"} <= set(default), default
    assert "pt_real" in default, default

    # naming ANOTHER session must not touch this capture's rows
    other = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=10, exclude_turn_echo_session="s2"))
    assert other == default, (default, other)

    # opting in drops exactly this session's echoes
    opted = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=10, exclude_turn_echo_session="s1"))
    assert set(opted) == set(default) - {"s1_t0", "s1_t1", "s1_t2"}, opted
    assert "pt_real" in opted


# ── limit applies to the FILTERED candidate set ────────────────────────────

def test_limit_applies_to_the_already_filtered_candidate_set(sdk):
    """#4509 acceptance 1: the filter runs before ``result_ids[:limit]``, so
    ``limit`` counts already-filtered candidates.

    FALSIFIER — (1) *what value makes this test fail?* The identity of the two
    returned rows: pre-truncation they are the two REAL priors; a filter applied
    after the cut finds the window full of echoes, drops them, and returns
    nothing. (2) *reachable?* Yes — the priors are seeded as the WEAKEST lexical
    matches (``_weak_prior``), so the three echoes outrank them on relevance and
    ``result_ids[:2]`` is entirely echoes. Under the BM25 arm (#10240, default
    ON) relevance decides that; with the arm opted out (``=0``) every row ties
    and the recency-then-id tie does, the echoes being seeded last and therefore
    newest. Either way only a pre-truncation filter can put the priors inside
    the page — and the DISCRIMINATOR assertion below states that requirement
    outright instead of leaving it implicit in the seeding.

    ⚠️ That is why the priors are lexically weak and NOT query-twice: #10240's
    relevance ranking made a query-twice prior outrank the echoes, so
    ``result_ids[:2]`` already held both priors and this test passed on the very
    after-the-cut code it exists to fail (measured: raw ``limit=2`` returned
    ``['zz_pt_b', 'zz_pt_a']`` with the exclusion DISABLED). Seeding it weak
    restores the discrimination, and the pinned discriminator keeps it from
    going vacuous again. (#10121's reversal of an earlier seeding order is the
    same defect one scorer earlier.)
    """
    sdk.create_point("statement", _weak_prior("aa"), id="zz_pt_a")
    sdk.create_point("statement", _weak_prior("bb"), id="zz_pt_b")
    for i in range(3):
        _seed_echo(sdk, "s1", i, f"[user] {QUERY} {QUERY} turn {i}")

    # DISCRIMINATOR, pinned so the fixture cannot silently go vacuous: the page
    # a NON-opting caller gets is entirely echoes, so an after-the-cut filter
    # would have nothing left to return.
    unfiltered = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=2))
    assert unfiltered, "fixture invalid: the unfiltered page is empty"
    assert not set(unfiltered) & {"zz_pt_a", "zz_pt_b"}, (
        "fixture invalid: a prior must NOT be in the unfiltered page, or an "
        f"after-the-cut filter would pass too — page={unfiltered}")

    rows = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=2, exclude_turn_echo_session="s1"))
    assert len(rows) == 2, rows
    assert set(rows) == {"zz_pt_a", "zz_pt_b"}, rows


def test_caller_minted_point_in_the_turn_namespace_survives(sdk):
    """A caller-minted Point whose id merely sits in the session's turn
    namespace, but carries no turn marker, is never excluded (the D3 decision:
    the shape of an id is not evidence that a capture happened).

    FALSIFIER — (1) *what value makes this test fail?* ``s1_t9``'s presence. (2)
    *reachable?* Yes: it is seeded with an id matching ``s1_t\\d+`` and a
    non-turn kind/content, so an id-shape-only predicate drops it.
    """
    _seed_echo(sdk, "s1", 0, f"[user] {QUERY} turn 0")
    sdk.create_point("statement", QUERY, id="s1_t9")
    sdk.create_point("statement", QUERY, id="pt_real")

    rows = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=10, exclude_turn_echo_session="s1"))
    assert "s1_t9" in rows, rows
    assert "s1_t0" not in rows, rows
    assert "pt_real" in rows, rows


# ── The RESIDUAL bound: the exclusion cannot reach past the fused pool ──────


def _seed_echo_wall_production(sdk, session_id: str, prior_id: str,
                               n_echoes: int) -> None:
    """The PRODUCTION call shape's fixture: ``n_echoes`` echoes plus ONE real
    prior, and — unlike ``_seed_echo_wall`` — no ``pool_size`` is passed by the
    caller under test, so the fused pool is the product default.

    The rows are not tied against the shipped scorer: the sparse leg is the BM25
    arm (#10240, ON by default), so who fills the pool is decided by RELEVANCE —
    length normalisation (``b=0.75``) is what prices the prior's padding and so
    favours the short echo over the long prior (``k1``'s saturation caps what a
    repetition can earn but is not what decides this ordering; see
    ``_weak_prior``). #10121's
    recency-then-id tie decided this when every row scored alike; against a
    relevance ranking the tie never fires, so the prior that starves is the
    LEXICALLY WEAKEST row, which ``_PRIOR_TEXT`` constructs. That is the
    production shape the residual is about: a prior captured earlier in the
    session, then the session keeps talking in text that matches the query more
    densely. Seeding the prior FIRST is kept so the fixture also holds when the
    arm is opted out (the tie fires again there and the oldest row starves)."""
    sdk.create_point("statement", _PRIOR_TEXT, id=prior_id)
    for i in range(n_echoes):
        _seed_echo(sdk, session_id, i, f"[user] {QUERY} {QUERY} {QUERY} turn {i}")


def test_production_pool_bound_still_starves_the_prior(sdk):
    """RESIDUAL PIN (#4509) — this test asserts the LIMIT of the fix, on purpose.

    The seam is sound (the filter really does run before the cut), but it can
    only drop echoes that are already IN the fused candidate set, and that set is
    the UNION of the live legs rather than one leg's window. ``_fts_rows`` passes
    no ``pool_size``, so each leg's window is ``retrieval.DEFAULT_POOL_SIZE``
    (120) — NOT ``MAX_SESSION_TURNS`` (500), which is what a capture can hold.
    This module pins FTS as the only live leg, so the fused set under test is
    that one window; in the hybrid shape it is the union of the fts and vector
    legs, about twice as wide. The bound this fix moves is 15 -> that fused set
    (~120 keyword-only, ~240 hybrid), NOT to 500 — so a session whose echoes fill
    it still starves a real prior ranked below them.

    #10121 moved the ORDER that decides which prior sits outside that window,
    not the bound. The tie was ``id ASC`` (#3019), then recency-then-id DESC. It
    is neither any more: since #10240 the sparse leg is the BM25 arm ON by
    default, so the order is a RELEVANCE ranking and the prior that starves is
    the LEXICALLY WEAKEST one, which ``_PRIOR_TEXT`` constructs. MEASURED with
    ``tortoise.bm25.bm25_rank`` on THIS fixture (130 echoes + 1 prior = 131
    rows): the query-twice prior ranks 0 of 131 — it cannot be starved at all —
    and the diluted prior ranks 130 of 131, i.e. the last row, outside the
    120-row pool. (The 0-of-151 / 150-of-151 figures quoted in ``_weak_prior``
    belong to the 150-echo ``_ECHOES`` fixture, not to this one.) The number
    this pin measures is unchanged: one leg's ``DEFAULT_POOL_SIZE``.

    This is the falsifier for any claim that #4509 removes starvation outright.
    The claim used to be written into the comments in ``extractor_v2.py`` and
    ``sdk.py``; it now states this bound, and this test is what keeps that
    statement honest. Raising the bound means passing an explicit ``pool_size`` on
    the point prior leg (or setting ``TORTOISE_POOL_FLOOR``, which widens the same
    window from the environment — this module deletes it so its verdict does not
    depend on the caller) — a retrieval-cost trade-off, deliberately not made
    here.

    FALSIFIER — (1) *what value makes this test fail?* Two of them. First,
    whether ``DEFAULT_POOL_SIZE`` is still the bound the comments name — asserted
    literally below, so a moved default reddens here and forces the documentation
    to move with it. Second, whether ``zz_pt_real`` is returned by the call that
    passes NO ``pool_size``; the echoes sort ahead of the prior and outnumber
    the pool,
    so the prior is outside the candidate set before any filter runs and no
    post-fetch exclusion can reach it.
    """
    assert DEFAULT_POOL_SIZE == _DOCUMENTED_PROD_POOL, (
        f"the production pool default moved ({DEFAULT_POOL_SIZE!r}, documented "
        f"as {_DOCUMENTED_PROD_POOL}) — the residual bound in "
        "tortoise/extractor_v2.py and tortoise/sdk.py names the old number, so "
        "update those comments (and this literal) together")
    _seed_echo_wall_production(sdk, "s1", "zz_pt_real", _DOCUMENTED_PROD_POOL + 10)

    prod = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=_OLD_LIMIT,
        exclude_turn_echo_session="s1"))
    assert "zz_pt_real" not in prod, (
        "this residual pin no longer holds — the prior came back through the "
        "PRODUCTION call shape, so either the fix now covers the worst case or "
        f"DEFAULT_POOL_SIZE changed. Update the bound documented in "
        f"tortoise/extractor_v2.py and tortoise/sdk.py. rows={prod}")
    assert prod == [], prod


def test_within_the_pool_the_exclusion_recovers_the_prior_in_production_shape(sdk):
    """The other half of the residual pin: with the SAME production call shape
    (no ``pool_size``) but an echo count BELOW the pool bound, the prior comes
    back. That is what makes the pin above a measurement of the POOL rather than
    of the exclusion — if the seam were broken, this test would fail too.

    FALSIFIER — (1) *what value makes this test fail?* ``zz_pt_real``'s absence.
    (2) *reachable?* Yes: half the default pool, ALL sorting ahead of the
    ``zz_pt_real`` prior, so the prior sits outside the pre-exclusion window and
    the exclusion is what surfaces it.
    """
    n_echoes = _DOCUMENTED_PROD_POOL // 2
    _seed_echo_wall_production(sdk, "s1", "zz_pt_real", n_echoes)

    # DISCRIMINATOR, pinned so this sibling cannot silently go vacuous the way
    # `test_limit_applies_...` did: the docstring's claim "if the seam were
    # broken, this test would fail too" holds ONLY while the prior ranks outside
    # the unfiltered page. If a future fixture change put it back inside, both
    # assertions below would pass against an after-the-cut filter and nothing
    # would red — so state the requirement instead of leaving it implicit.
    unfiltered = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=_OLD_LIMIT))
    assert unfiltered, "fixture invalid: the unfiltered page is empty"
    assert not set(unfiltered) & {"zz_pt_real"}, (
        "fixture invalid: the prior must NOT be in the unfiltered page, or an "
        f"after-the-cut filter would pass too — page={unfiltered}")

    rows = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=_OLD_LIMIT,
        exclude_turn_echo_session="s1"))
    assert "zz_pt_real" in rows, (
        f"within the pool the exclusion must surface the prior, got {rows}")
    assert not any(i.startswith("s1_t") for i in rows), rows
