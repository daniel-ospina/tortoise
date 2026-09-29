"""#4509 — the S3 link-before-create prior lookup must exclude a capture's own
turn echoes BEFORE ``limit`` truncates, not after it.

Defect: ``tortoise_fts_query`` truncates internally (``result_ids[:limit]``)
and applies its one exclusion (``exclude_status``) BEFORE that cut — documented
that way so filtering cannot silently shrink the result count (epic #898). The
extractor's #2552 fix (``extractor_v2._fts_rows``) dropped the capture's own
turn-echo rows AFTER the cut and refilled from a finite over-fetch window
(``_PRIOR_OVERFETCH``). A session can hold ``MAX_SESSION_TURNS`` (500) turns, so
whenever the echoes outnumber ``limit + _PRIOR_OVERFETCH`` and outrank a real
prior, the prior is silently absent from the S3 prior set — the extracted claim
is ADDed instead of folded, producing a duplicate memory Point.

Fix: the exclusion is a caller-supplied, OPT-IN pre-truncation filter in the
retrieval layer (``exclude_turn_echo_session``, same seam as
``exclude_status``), and ``_PRIOR_OVERFETCH`` / the manual refill are deleted.

These tests are HERMETIC (per-test embedded store, no provider keys, no
network): the query embedder is pinned to None so the sparse leg is the only
one submitted and ranking is a pure FTS ordering.
"""
from __future__ import annotations

import contextlib
import os
import tempfile

import pytest

from tortoise.retrieval import is_turn_echo_row
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


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the sparse leg: with ``EmbeddingModel.get`` → None the vector
    strategy is never submitted, so FTS is the only leg and the ordering is
    deterministic without a model download."""
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get",
                        staticmethod(lambda load_timeout=None: None))


@pytest.fixture
def sdk():
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_prior_bound_4509_"), "test.db")
    s = TortoiseSDK(db_path)
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
    real prior. The echoes contain the query tokens; the prior does too (twice,
    so it lands inside the fused candidate pool rather than at the very tail)."""
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
    ``pt_real`` in the exclusion call. (2) *does the fixture contain a row where
    that value is reachable?* Yes: ``pt_real`` is seeded and asserted to be a
    candidate of the same fused pool (``pool_size=_POOL``) BEFORE the
    exclusion — so the only thing that can remove it from the pre-exclusion
    window is the new pre-truncation filter, and the only thing that can fail
    to surface it is the OLD after-the-cut drop.

    The OLD algorithm is reproduced inline: over-fetch ``limit + 12``, drop the
    session's echoes, refill. It returns NOTHING here — the exact starvation the
    issue names — while the new opt-in call returns the prior.
    """
    _seed_echo_wall(sdk, "s1", "pt_real")

    # The prior IS a candidate of the fused pool — not merely absent because the
    # pool was too shallow. (Same call shape as the OLD over-fetch, wide pool.)
    deep = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=_POOL, pool_size=_POOL))
    assert "pt_real" in deep, (
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
    assert "pt_real" not in _ids(old_fetch), "OLD window leaked the prior"

    # NEW path: one opt-in argument, and the prior comes back.
    new = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=_OLD_LIMIT, pool_size=_POOL,
        exclude_turn_echo_session="s1"))
    assert "pt_real" in new, f"prior starved after the fix: {new}"
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
    Yes: three echoes outrank the two priors and the window is 2, so the
    ordering is forced.
    """
    for i in range(3):
        _seed_echo(sdk, "s1", i, f"[user] {QUERY} {QUERY} turn {i}")
    sdk.create_point("statement", f"{QUERY} {QUERY}", id="pt_a")
    sdk.create_point("statement", f"{QUERY} {QUERY}", id="pt_b")

    rows = _ids(sdk.tortoise_fts_query(
        QUERY, entity_type="point", limit=2, exclude_turn_echo_session="s1"))
    assert len(rows) == 2, rows
    assert set(rows) == {"pt_a", "pt_b"}, rows


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
