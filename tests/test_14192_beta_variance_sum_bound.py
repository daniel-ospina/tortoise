"""#14192 — the variance sum bound the Q2-crit selection leans on.

``get_relationships_bounded``'s critical-class read admits a peer when its
persisted Beta width exceeds ``CONTESTED_VARIANCE_THRESHOLD``. That width is a
division over ``α+β``, evaluated once per candidate row, and it was the cost this
change targeted. #14192 put a NECESSARY precondition in front of it, so that the
division and the band checks are skipped on the rows that were always going to be
rejected. ⛔ **BUT NOT ALL OF THE COERCION, AND THE GUARD IS NOT FREE** — the
bound is built from the same ``numeric_operand_cypher`` expressions the division
uses, so coercion ALSO runs on every candidate row INSIDE the guard itself. On
the emitted predicate: 8 coercion ``CASE WHEN`` before ``$contested_sum_bound``
against 73 between it and ``$contested_threshold`` (72 coercion + the division's
own selector), so a rejected row pays 8 coercion cases to skip 72.
BOTH halves are graded below — and so is the 72/1 split, because the share the
caveat quotes (72 of 80 coercion, 90%, 64 net) is arithmetic over that split and
over neither total alone. The guard's own 8 are its price, not a saving —
which is a CAVEAT on the saving, never a claim that no coercion is spared:

    α+β < 1/(4·threshold) − 1        (`beta_variance_sum_bound`)

⛔ **NO MILLISECOND FIGURE IS CLAIMED HERE.** The committed artifacts
(`docs/evidence/14192-read-wave/`) measure the WHOLE function and its isolated
A/B, never this expression alone. An earlier cut of this docstring claimed "160 ms
of a 231 ms search" — both numbers appear HERE only because this note withdraws
them: neither is a cell of either JSON, and `160 ms` could not have been right
anyway, because it exceeded the `159 ms` wave that was supposed to contain it.
Both are WITHDRAWN rather than restated, which is the same rule
`beta_variance_sum_bound`'s own docstring applies to its own earlier figures.

The guard is only sound if it can NEVER reject a pair whose width clears the
threshold — otherwise it silently drops critical peers, which are cap-exempt
by design (D3/D4) and must never be filtered. This module pins exactly that,
against the PYTHON twin of the same formula (``_beta_variance``), because the
Cypher expression is generated from the same guard chain and a re-derivation
that disagreed with the twin is the #11702 failure class this codebase has
already paid for once.
"""
from __future__ import annotations

import inspect
import re

import pytest

from tortoise.graph_ops import (
    GraphOpsCounter,
    capture_phase,
    count_graph_ops,
    record_graph_op,
)
from tortoise.search_engine import (
    CONTESTED_VARIANCE_THRESHOLD,
    VARIANCE_SUM_MAX,
    VARIANCE_SUM_MIN,
    _beta_variance,
    beta_variance_sum_bound,
    get_relationships_bounded,
    numeric_operand_cypher,
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


def _guard_admits(alpha: float, beta: float) -> bool:
    """The guard's own predicate, evaluated the way the generated Cypher does.

    Both operands are readable numbers here, so the query's
    ``numeric_operand_cypher`` cannot NULL the sum and the arm reduces to the
    strict comparison against the bound. A non-numeric or absent operand drops
    the row in the ``WHERE`` (NULL, not ``True``) — that is #11702's territory,
    pinned against the twin, not this bound.
    """
    return alpha + beta < beta_variance_sum_bound(CONTESTED_VARIANCE_THRESHOLD)


def test_the_guard_admits_exactly_the_pairs_the_bound_allows() -> None:
    """The guard's OWN verdict, on pairs whose sum can go either way.

    A shape the twin answers ``0.0`` for cannot say anything about this bound:
    ``not (admitted and width > threshold)`` is then ``True`` whatever the bound
    is, because the right conjunct is already false. So both directions are
    pinned on shapes whose width CAN cross the threshold:

    - ADMITTED, and it must be: ``(2.6, 2.6)`` sums to ``5.2 < 5.25`` and its
      width is ``0.04032``, i.e. the twin calls it CONTESTED. A guard that
      refused it would drop a cap-exempt critical peer (D3/D4) — the P0-class
      false rejection this file exists to prevent.
    - REFUSED, and it must be: ``(3.0, 3.0)`` sums to ``6.0 >= 5.25`` and its
      width is ``0.03571``, so the division it spares could only reject it.
    - THE BOUNDARY: at ``s == bound`` the widest pair computes EXACTLY the
      threshold, and the query's test is strict, so the guard's refusal and the
      twin's exclusion agree there.

    MUTANTS, and what a run actually reds -- stated as MEASURED, because an
    earlier cut of this block named three arms that never execute:

    - ``beta_variance_sum_bound``'s ``- 1.0`` to ``- 2.0`` (bound 4.25),
      ``- 1.0`` to ``+ 2.0`` (bound 8.25), and ``- 1.0`` to ``- 0.5``
      (bound 5.75): ALL THREE stop at the antecedent pin two lines below
      (``assert bound == pytest.approx(5.25)``, e.g.
      ``assert 4.25 == 5.25 +- 5.2e-06``), so no arm below it runs. That is
      the correct polarity -- the arms are written against the SHIPPED
      bound, and a changed bound is a changed CONTRACT, not a boundary
      case -- but it means this test's guard-arithmetic coverage comes from
      the antecedent, and the per-arm ``<``/``>`` assertions only execute
      while the bound is 5.25.
    - A mutant that exercises an ARM has to leave the bound alone and move
      the PAIR. The end-to-end arm (a contested peer surviving the real
      query) is
      ``tests/test_relationships_bounded.py::test_contested_peer_at_the_sum_bound_always_survives``.

    OBSERVED at this head (bound ``5.25``): ``(2.6, 2.6)`` → ``5.2``,
    admitted, width ``0.0403225806``; ``(3.0, 3.0)`` → ``6.0``, refused, width
    ``0.0357142857``; ``(2.625, 2.625)`` → ``5.25``, refused, width ``0.04``.
    All three are exact in float, so no ``approx`` hides a boundary slip.
    """
    bound = beta_variance_sum_bound(CONTESTED_VARIANCE_THRESHOLD)
    assert bound == pytest.approx(5.25)

    a, b = 2.6, 2.6
    assert a + b < bound
    assert _guard_admits(a, b)
    assert _beta_variance(a, b) > CONTESTED_VARIANCE_THRESHOLD

    a, b = 3.0, 3.0
    assert a + b >= bound
    assert not _guard_admits(a, b)
    assert _beta_variance(a, b) <= CONTESTED_VARIANCE_THRESHOLD

    half = bound / 2.0
    assert _guard_admits(half, half) is False
    assert _beta_variance(half, half) <= CONTESTED_VARIANCE_THRESHOLD


# ── the two pins that make everything above load-bearing ──────────────────────
#
# ⛔ Both exist because the arithmetic above is NOT sufficient on its own. Every
# case above re-derives the predicate in Python against the twin; NONE of them
# reads the query. Measured before these were added: deleting the three
# `AND … < $contested_sum_bound` lines from `get_relationships_bounded`, or moving
# them out of the contested OR-branch, left all five cases GREEN — the guard
# simply never ran, so a perfectly sound formula sat beside a query that had
# stopped using it.

_Q1A_ROW = ("p1", "IMPL", 0, "op1", "mechanism", "pred", "2026-01-01")


class _Result:
    """What FalkorDB's ``query()`` returns: an object carrying ``result_set``."""

    __slots__ = ("result_set",)

    def __init__(self, rows):
        self.result_set = rows


class _RecordingGraph:
    """A graph double with the choke point's shape: it records AND meters.

    ``record_graph_op`` is exactly what ``_GuardedGraph.query`` calls on every
    access, so a double that omits it cannot observe the metering defect the
    test below pins — it would pass no matter what the wave does.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def query(self, cypher, **kwargs):
        record_graph_op(cypher)
        self.calls.append((cypher, kwargs))
        # Q1a is the only read whose projection is the operator-edge row; it
        # answers ONE operator edge, which is what names ``op_ids`` and so what
        # makes the four operator reads (and the crit query) namable at all.
        if "type(r) AS et, r.idx AS n_idx" in cypher:
            return _Result([_Q1A_ROW])
        return _Result([])


def _decorate_under_capture():
    graph = _RecordingGraph()
    counter = GraphOpsCounter()
    with count_graph_ops(counter), capture_phase("session_store"):
        get_relationships_bounded(graph, ["p1"])
    return graph, counter


def _paren_depth(text: str, index: int) -> int:
    """Nesting depth at ``index`` (no parens appear inside string literals)."""
    return text[:index].count("(") - text[:index].count(")")


def test_every_wave_read_is_visible_to_the_capture_counter() -> None:
    """The wave's WORKERS must inherit the metering context.

    MUTANT: revert ``_ReadWave.submit`` to ``pool.submit(thunk)``. A NEW thread
    does not inherit ContextVars (CPython) and ``record_graph_op`` early-returns
    when ``_ACTIVE`` is unset, so the reads vanish from the counter SILENTLY.
    REDDENED ROW: this one — 6 counted reads becomes 1.
    OBSERVED both ways on this stub graph: mutant in place, six ``graph.query``
    calls and ``counter.total == 1`` (the Q1a read, which stays on the caller
    thread); fixed, ``counter.total == 6``.
    """
    graph, counter = _decorate_under_capture()
    assert len(graph.calls) == 6, (
        f"the read count moved ({len(graph.calls)}); this pin is written "
        "against one Q1a + five wave members")
    assert counter.by_phase["session_store"]["read"] == len(graph.calls)
    assert counter.total == len(graph.calls)


def test_the_guard_is_inside_the_contested_arm_and_ahead_of_the_division() \
        -> None:
    """The query — not just the formula — carries the guard, in the right arm.

    Three mutants, and what each reddens:

    - DELETE the three ``AND … < $contested_sum_bound`` lines → the parameter goes
      with them, so ``$contested_sum_bound`` appears in no query at all.
    - Move them to the top level of the ``WHERE`` (ANDed AFTER the ``OR`` group)
      → the guard's paren depth stops being greater than the arm's, and the
      guard would prune NAND/terminal peers, which are cap-exempt by design
      (D3/D4) — the P0-class false rejection this file exists to prevent.
    - Move them ANYWHERE after the width test → ``$contested_threshold`` would
      precede the guard, i.e. the division would run first and the guard would
      spare nothing, which is the entire point on the measured read.

    OBSERVED on ``origin/main`` (`70fbd1a27d`, re-measured, not carried): one
    crit query, one occurrence, arm at offset 579 / guard at 2136, depth 1 → 3,
    ``$contested_threshold`` at 13864, and ``contested_sum_bound == 5.25`` — the
    same value the twin returns. (An earlier cut of this docstring quoted guard
    offset 2163 at depth ``1 → 2``; the emitted predicate measures 2136 at
    ``1 → 3``. The assertion reads the offsets rather than the prose, so nothing
    depended on the stale pair — but nothing recomputed it either. The ``CASE
    WHEN`` numerals quoted in ``beta_variance_sum_bound``'s docstring ARE
    recomputed in this test, and this module's own copy of them is graded too
    (round-17 review P2 3, round-20 review — the sentence used to name "the
    module docstring" while grading the FUNCTION docstring, and the module
    docstring's copy was ungraded.)
    """
    graph, _ = _decorate_under_capture()
    crits = [(c, kw) for c, kw in graph.calls
             if "contested_sum_bound" in (kw.get("params") or {})]
    assert len(crits) == 1, (
        f"expected exactly one guarded read, found {len(crits)}")
    crit, kwargs = crits[0]

    assert crit.count("$contested_sum_bound") == 1, (
        "the guard must appear once — twice would mean it was copied into "
        "another arm")
    # `OR ((other.posterior_alpha …` is the contested alternative's opener: the
    # only place the query ORs into `ep_measured_cypher`, whose expansion begins
    # with `(other.posterior_alpha`. The NAND and terminal alternatives open
    # their own `OR ((other.status …`.
    arm = crit.index("OR ((other.posterior_alpha")
    guard = crit.index("< $contested_sum_bound")
    division = crit.index("$contested_threshold")

    assert crit.index("type(r2) = 'NAND'") < arm, (
        "the contested arm must still be the LAST alternative")
    assert arm < guard < division, (
        "the guard must sit inside the contested arm and in FRONT of the "
        "division")
    assert _paren_depth(crit, guard) > _paren_depth(crit, arm), (
        "the guard is not enclosed by the contested arm — it would prune "
        "NAND/terminal peers")
    # ⛔ THE DOCSTRING'S "8 ``CASE WHEN`` BEFORE ``$contested_sum_bound``" IS A
    # MEASURED NUMERAL, so it is GRADED here rather than trusted (round-17
    # review, P2 3). Without this the figure rots silently the moment
    # `numeric_operand_cypher` or the guard's operand set changes — the same
    # ungraded-numeral class the docstring's own OBSERVED paragraph apologises
    # for. Both bounds are claims, so both are asserted:
    #   * BEFORE THE ARM, 0 — the coercion is the price of the GUARD. A
    #     `CASE WHEN` ahead of the arm would mean the coercion is paid on rows
    #     the arm then discards, which is the opposite of the correction this
    #     docstring makes (the arm's alternatives use `coalesce(col, literal)`
    #     comparisons and one BARE `other.status IS NOT NULL`; none emits a
    #     `CASE WHEN`, measured 0 across `crit[:arm]`).
    #   * BEFORE THE GUARD, 8 — attributable, not coincidental: 4 `CASE WHEN`
    #     per `numeric_operand_cypher` operand x the guard's TWO operands.
    assert crit[:arm].count("CASE WHEN") == 0, (
        "a coercion `CASE WHEN` precedes the contested arm, so the docstring's "
        "claim that the coercion is the GUARD's price (and no saving) is no "
        "longer true")
    guard_cases = crit[:guard].count("CASE WHEN")
    assert guard_cases == 8, (
        f"{guard_cases} coercion `CASE WHEN` precede "
        "`$contested_sum_bound`, but the docstring promises 8 (4 per "
        "`numeric_operand_cypher` operand x the guard's two operands) — "
        "re-derive the docstring's numeral against THIS predicate")
    # ⛔ AND THE HALF THE GUARD SPARES, so "the guard is its own price" can never
    # be read as "no coercion is spared". Measured at the same head: 73
    # `CASE WHEN` sit BETWEEN the guard and `$contested_threshold` — the
    # ~11.7k-char coercion `CASE` the division carries, which the file's own
    # `beta_variance_gt_cypher` note names. If that expression ever collapsed, the
    # guard's saving would vanish while the `8` above stayed true, so BOTH
    # directions are pinned rather than only the one an earlier cut of this PR
    # asserted.
    spared = crit[guard:division].count("CASE WHEN")
    assert spared == 73, (
        f"{spared} `CASE WHEN` sit between the guard and "
        "`$contested_threshold`, but the docstring promises 73 (72 coercion + "
        "the division's own selector) — re-derive BOTH "
        "numerals against THIS predicate")
    # ⛔ AND THE DOCSTRING ITSELF IS GRADED, not just the emission. The two
    # numerals above are compared against LITERALS, so on their own they pin the
    # predicate but NOT the prose: a docstring could be inverted to "73 before
    # the guard / 8 spared" — the exact false claim this PR exists to correct —
    # and both assertions above would stay green. That is the very
    # ungraded-numeral class the module docstring apologises for, so the
    # docstring's own numerals are read OUT OF THE DOCSTRING and compared to the
    # same measurements. Precedent for reading a prose figure in a test:
    # `test_sdk_privatise_sweep.py::test_the_abbreviation_length_claim_reproduces`.
    doc = inspect.getdoc(beta_variance_sum_bound) or ""
    doc_before = re.search(r"\*\*(\d+)\*\* coercion ``CASE WHEN`` before", doc)
    doc_spared = re.search(r"against \*\*(\d+)\*\* between it and", doc)
    assert doc_before and doc_spared, (
        "`beta_variance_sum_bound`'s docstring no longer states both CASE WHEN "
        "numerals in the graded form ('**N** coercion ``CASE WHEN`` before' / "
        "'against **N** between it and') — restore the wording or re-point this "
        "test, but do not drop the coupling")
    assert int(doc_before.group(1)) == crit[:guard].count("CASE WHEN"), (
        f"the docstring says {doc_before.group(1)} coercion `CASE WHEN` precede "
        f"the guard, but THIS predicate measures "
        f"{crit[:guard].count('CASE WHEN')} — the prose has rotted off the emission")
    assert int(doc_spared.group(1)) == spared, (
        f"the docstring says {doc_spared.group(1)} `CASE WHEN` are spared, but "
        f"THIS predicate measures {spared} — the prose has rotted off the emission")
    # ⛔ AND THIS FILE'S OWN MODULE DOCSTRING, which quotes the same two numerals
    # and WAS ungraded (round-20 review). A copy that nothing reads is the same
    # ungraded-figure class as the totals above, one level out: the sentence that
    # justified this block named "the module docstring" while reading the
    # FUNCTION docstring, so the module's copy could rot and every row stay green.
    # Read from the module object rather than the source text, so the letter of
    # how the docstring is spelled does not matter — only the numerals do.
    module_doc = " ".join((__doc__ or "").split())
    module_numerals = re.search(
        r"(\d+) coercion ``CASE WHEN`` before ``\$contested_sum_bound`` "
        r"against (\d+) between it and", module_doc)
    assert module_numerals, (
        "this module's docstring no longer states the two ``CASE WHEN`` numerals "
        "in the graded form — restore the wording or re-point this test, but do "
        "not leave the copy ungraded")
    assert (int(module_numerals.group(1)), int(module_numerals.group(2))) == (
        crit[:guard].count("CASE WHEN"), spared), (
        f"this module's docstring quotes ({module_numerals.group(1)}, "
        f"{module_numerals.group(2)}) for the guard and the spared slice, but "
        f"THIS predicate measures ({crit[:guard].count('CASE WHEN')}, {spared}) "
        "— the module's copy has rotted off the emission")
    # ⛔ AND THE SPLIT THE DOCSTRING DERIVES THREE MORE NUMERALS FROM. The rows
    # above grade the two TOTALS — the emission against literals, and the prose
    # against the emission — and NEITHER grades their split. The caveat quotes
    # "spares **72 of the predicate's 80 coercion `CASE WHEN`** … 90% of the
    # coercion … net of the 8 it pays, a rejected row still skips 64" — and 72
    # is NOT `73 - 8`: it is `73 - the division's own selector`, and a selector
    # is not a coercion. So a coercion that became a selector leaves BOTH graded
    # totals untouched and moves the quoted share, which is the same
    # ungraded-figure class this file already closed for the `8` and the `73`.
    # The count is measured from the emission's own structure rather than
    # restated, so the row cannot drift with the coercion's shape: one
    # `numeric_operand_cypher` emission is `emission_cases` `CASE WHEN`, and it
    # spells the finite-range bound twice (a `<=` and a `>=`), so half the
    # bound's occurrences is the number of emissions in the slice.
    emission = numeric_operand_cypher("tortoise_probe_operand")
    emission_cases = emission.count("CASE WHEN")
    range_lit = emission.split("<= ")[1].split(" ")[0]
    coercions = (crit[guard:division].count(range_lit) // 2) * emission_cases
    assert spared - coercions == 1, (
        f"{spared - coercions} `CASE WHEN` in the spared slice are not "
        "`numeric_operand_cypher` coercions, but the docstring derives its "
        "share as `72 coercion + the division's own selector` — exactly one. "
        "With a different split the quoted 72 / 80 / 64 are all wrong while "
        "the 8 and the 73 above stay green: re-derive the share")
    # ⛔ AND THE TWO FIGURES DERIVED FROM THAT SPLIT ARE NOT ASSERTED, ON PURPOSE.
    # `coercions == 72` is `spared - 1` and `8 + coercions == 80` is `8 + 72`:
    # both follow from rows asserted above, so asserting them would be an
    # assertion NO MUTANT CAN REDDEN — dead weight by the standard this repo
    # already applies (`tests/test_rerank_pool.py`, agent-infra#3442 rule 3). An
    # earlier cut of this block carried both rows; they were removed rather than
    # kept for the look of coverage (round-20 review). The claim they were meant
    # to pin — that the SHARE rests on the split, not on the totals — is pinned
    # by the row above, which is the one a re-split actually reds.
    # ⛔ AND THE SHARE ITSELF, IN EVERY COPY — not just the four headline
    # numerals. Arithmetic cannot rot on its own but PROSE still does: measured at
    # `de4cab96`, inverting any of `72 of 80` / `90%` / `99%` / `64` left every
    # row green, and at `21f9da1a` the DERIVATION copies were still ungraded —
    # `(72 coercion + the division's own selector)`, `pays 8 coercion ``CASE``s to
    # skip 72`, and `(the 8 above + the 72 below)`, the last being the
    # paragraph's own derivation of 80, so inverting it stated a false derivation
    # while the graded headline stayed true (round-21 and round-22 reviews).
    #
    # So the rows below are built FROM the emission and demanded as literal
    # substrings of each docstring that quotes them. ⛔ NO COMPLETENESS CLAIM IS
    # MADE HERE, on purpose: two earlier cuts of this comment asserted that a
    # copy outside the tuple "is either absent from both docstrings or a new copy
    # this tuple must learn about", and both times several copies were present
    # and ungraded — including the headline `(72 of 80)` restatement — so the
    # sentence was the same ungraded-claim defect one level up (rounds 22 and
    # 23). The list is what it is; a copy not in it is simply not graded by it.
    coercions_total = guard_cases + coercions
    pct_coercion = round(100 * coercions / coercions_total)
    pct_slice = round(100 * coercions / spared, 1)
    net_cases = coercions - guard_cases
    selectors = spared - coercions
    function_doc_copies = (
        f"{coercions} of the predicate's {coercions_total} coercion",
        f"{pct_coercion}% of the coercion",
        f"{coercions} of the {spared} ``CASE WHEN`` it spares",
        f"{pct_slice}%, a different number",
        f"still skips {net_cases} coercion",
        f"({coercions} coercion + the division's own selector)",
        f"pays {guard_cases} coercion ``CASE``s to skip {coercions}",
        f"(the {guard_cases} above + the {coercions} below)",
        f"({coercions} of {coercions_total})",
        f"That {guard_cases} is the guard's own",
        f"net of the {guard_cases} it pays",
        f"({coercions} of {spared} — {pct_slice}%",
    )
    module_doc_copies = (
        f"({coercions} of {coercions_total} coercion, {pct_coercion}%, "
        f"{net_cases} net)",
        f"({coercions} coercion + the division's own selector)",
        f"pays {guard_cases} coercion cases to skip {coercions}",
        f"the {coercions}/{selectors} split",
        f"The guard's own {guard_cases} are its price",
    )
    for where, text, rows in (
            ("`beta_variance_sum_bound`'s docstring", doc, function_doc_copies),
            ("this module's docstring", module_doc, module_doc_copies)):
        missing = [row for row in rows if row not in text]
        assert not missing, (
            f"{where} no longer states {missing!r} — these are the share numerals "
            "derived from THIS emission (spared "
            f"{spared}, coercions {coercions}, guard {guard_cases}), so a copy "
            "that drifts off them re-inverts the claim this PR exists to keep "
            "true. Restore the wording or re-point this test; do not leave a "
            "copy ungraded")
    assert 11_000 < len(crit[guard:division]) < 12_500, (
        "the docstring calls the spared span a ~11.7k-char coercion `CASE` and "
        f"THIS predicate measures {len(crit[guard:division])} chars. This is a "
        "MAGNITUDE tripwire for a figure the prose quotes to one significant "
        "figure, NOT a byte pin, and its real tolerance is what it actually "
        "implements: the band reds AT OR BELOW 11,000 (−728, −6.2%) and AT OR "
        "ABOVE 12,500 (+772, +6.6%) — the edges themselves red, because both "
        "comparisons are strict."
        " ⛔ A semantics-preserving rename inside the slice REDS "
        "IT — measured, `other` -> `other_peer` (216 occurrences) moves the span "
        "by +1,080 to 12,808 — and that false block is the ACCEPTED cost of "
        "grading an approximate figure at all, not a bug to be explained away. "
        "Earlier cuts of this message claimed an order-of-magnitude tolerance "
        "over a ±7% band, i.e. told the first reader who hit the red something "
        "false about the row (rounds 21 and 22).")
    assert kwargs["params"]["contested_sum_bound"] == beta_variance_sum_bound(
        CONTESTED_VARIANCE_THRESHOLD)

    for cypher, other in graph.calls:
        if other is kwargs:
            continue
        assert "contested_sum_bound" not in cypher, (
            "the guard leaked into a read that must keep its full population")
        assert "contested_sum_bound" not in (other.get("params") or {})
