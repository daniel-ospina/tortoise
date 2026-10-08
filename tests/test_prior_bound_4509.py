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
submitted and ranking is a pure FTS ordering, and ``TORTOISE_POOL_FLOOR`` is
deleted so a caller's environment cannot move the window these bounds are
measured against.
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

    ``prior_id`` must sort AFTER the echo ids (``<session_id>_t<i>``): every row
    in this fixture scores alike, so the fused order is the ``id`` tie-break
    (#3019), and only a late-sorting prior sits outside a window the echoes
    fill. An earlier revision relied on the prior being seeded after the hot
    echoes, i.e. on the opaque DB row order this tie-break replaces."""
    for i in range(_HOT):
        _seed_echo(s, session_id, i, f"[user] {QUERY} {QUERY} {QUERY} turn {i}")
    s.create_point("statement", f"{QUERY} {QUERY}", id=prior_id)
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
    after the cut returns two echoes (or nothing after a drop). (2) *reachable?*
    Yes: the three echoes sort ahead of the two priors and the window is 2, so
    the ordering is forced. The priors are named ``zz_pt_*`` precisely so they
    sort after the ``s1_t*`` echoes — with earlier-sorting priors the window
    would hold the priors either way and the test would not discriminate.
    """
    for i in range(3):
        _seed_echo(sdk, "s1", i, f"[user] {QUERY} {QUERY} turn {i}")
    sdk.create_point("statement", f"{QUERY} {QUERY}", id="zz_pt_a")
    sdk.create_point("statement", f"{QUERY} {QUERY}", id="zz_pt_b")

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
    caller under test, so the fused pool is the product default. Every row
    scores alike, so the ``id`` tie-break decides: ``prior_id`` must sort after
    the echo ids for the echoes to fill the pool ahead of it."""
    for i in range(n_echoes):
        _seed_echo(sdk, session_id, i, f"[user] {QUERY} {QUERY} {QUERY} turn {i}")
    sdk.create_point("statement", f"{QUERY} {QUERY}", id=prior_id)


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

    rows = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=_OLD_LIMIT,
        exclude_turn_echo_session="s1"))
    assert "zz_pt_real" in rows, (
        f"within the pool the exclusion must surface the prior, got {rows}")
    assert not any(i.startswith("s1_t") for i in rows), rows
