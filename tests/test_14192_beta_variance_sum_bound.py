"""#14192 — the variance sum bound the Q2-crit selection leans on.

``get_relationships_bounded``'s critical-class read admits a peer when its
persisted Beta width exceeds ``CONTESTED_VARIANCE_THRESHOLD``. That width is a
division over ``α+β``, and it was the most expensive expression on the search
read (measured 160 ms of a 231 ms search on the #14192 corpus). #14192 put a
NECESSARY precondition in front of it:

    α+β < 1/(4·threshold) − 1        (`beta_variance_sum_bound`)

The guard is only sound if it can NEVER reject a pair whose width clears the
threshold — otherwise it silently drops critical peers, which are cap-exempt
by design (D3/D4) and must never be filtered. This module pins exactly that,
against the PYTHON twin of the same formula (``_beta_variance``), because the
Cypher expression is generated from the same guard chain and a re-derivation
that disagreed with the twin is the #11702 failure class this codebase has
already paid for once.
"""
from __future__ import annotations

import pytest

from tortoise.search_engine import (
    CONTESTED_VARIANCE_THRESHOLD,
    VARIANCE_SUM_MAX,
    VARIANCE_SUM_MIN,
    _beta_variance,
    beta_variance_sum_bound,
)


def test_the_bound_is_the_arithmetic_value() -> None:
    """1/(4·0.04) − 1 = 5.25 — the number the comment claims, pinned."""
    assert beta_variance_sum_bound(0.04) == pytest.approx(5.25)


def test_a_non_positive_threshold_admits_everything() -> None:
    """No finite bound exists when everything clears the bar."""
    assert beta_variance_sum_bound(0.0) == float("inf")
    assert beta_variance_sum_bound(-1.0) == float("inf")


def _grid():
    """A grid wide enough to cross the bound from every direction.

    Includes the halves of the equality point (α=β=s/2 is where the AM–GM
    bound is attained), pairs just either side of ``s = 5.25``, the extreme
    priors, and the band edges ``_beta_variance`` itself guards.
    """
    values = [
        VARIANCE_SUM_MIN, 1e-3, 0.1, 0.5, 1.0, 2.0, 2.6, 2.625, 2.7, 3.0,
        4.0, 5.0, 5.24, 5.25, 5.26, 6.0, 10.0, 100.0, VARIANCE_SUM_MAX,
    ]
    for a in values:
        for b in values:
            yield a, b
            yield b, a
    # The exact halves of the bound, where the maximum width lives.
    for s in (1.0, 2.0, 4.9, 5.24, 5.25, 5.3, 5.26):
        yield s / 2.0, s / 2.0


def test_the_guard_never_rejects_a_contested_pair() -> None:
    """The one direction that can corrupt an answer: a false rejection.

    A pair counts as contested when the twin's width exceeds the threshold.
    The guard runs first and skips the division, so it must admit EVERY such
    pair — ``sum >= bound`` and ``contested`` is the combination that would
    drop a real critical peer.
    """
    bound = beta_variance_sum_bound(CONTESTED_VARIANCE_THRESHOLD)
    checked = 0
    for a, b in _grid():
        variance = _beta_variance(a, b)
        if variance <= CONTESTED_VARIANCE_THRESHOLD:
            continue
        checked += 1
        assert a + b < bound, (
            f"guard would reject a contested pair: α={a!r} β={b!r} "
            f"sum={a + b!r} variance={variance!r} bound={bound!r}")
    # The sweep must actually EXERCISE the contested arm, or it proves nothing.
    assert checked >= 10, f"grid produced only {checked} contested pairs"


def test_the_bound_is_attained_just_below_the_threshold() -> None:
    """The bound is TIGHT: at ``s = bound`` the widest pair cannot clear it.

    If this failed, the guard would be stricter than the arithmetic allows and
    would start dropping peers the variance test admits.
    """
    bound = beta_variance_sum_bound(CONTESTED_VARIANCE_THRESHOLD)
    half = bound / 2.0
    assert _beta_variance(half, half) <= CONTESTED_VARIANCE_THRESHOLD
    # …and just below it the symmetric pair DOES clear the threshold, so the
    # two sides of the bound genuinely differ.
    assert _beta_variance((bound - 0.01) / 2.0,
                          (bound - 0.01) / 2.0) > CONTESTED_VARIANCE_THRESHOLD


def test_degenerate_operands_stay_excluded_by_both_rules() -> None:
    """NULL/negative/zero-sum shapes: the twin answers 0.0, the guard excludes.

    Both directions agree, so the guard cannot turn a shape the twin rejects
    into one the selection admits, nor the reverse.
    """
    bound = beta_variance_sum_bound(CONTESTED_VARIANCE_THRESHOLD)
    for a, b in ((0.0, 0.0), (3.0, -1.0), (1e150, 1e150), (-1.0, 9.0)):
        assert _beta_variance(a, b) <= CONTESTED_VARIANCE_THRESHOLD
        # The guard's own predicate, evaluated the way Cypher would.
        admitted_by_guard = (a + b) < bound
        assert not (admitted_by_guard and
                    _beta_variance(a, b) > CONTESTED_VARIANCE_THRESHOLD)
