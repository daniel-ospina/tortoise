"""Acceptance tests for the pre-merge exemption decision (tortoise #3756).

Every case here is a MUTATION that must RED if the fix regresses. The declared
threat surface is the **exemption face**: a failure excused because main recently
failed it. Each declared class gets its own test:

* **E1** presence-in-window immunity
* **E2** id-granularity immunity (same id, DIFFERENT failure inside it)
* **E3** rate-blindness (``main 1/8`` vs ``PR 8/8``)
* **E4** silent exemption (a green with no recorded exemption line)
* plus the **window-boundary** case (in the acceptance, NOT in the threat surface:
  it is fixed as a CONSEQUENCE of the effect change — once the decision is a
  re-measurement there is no window to be outside of)
* plus **verdict-stability** (the same failure set evaluated twice must return the
  same verdict)
* plus the **fail-closed parser** (a stray non-nodeid must REFUSE the exemption,
  never enter the union)

Out of scope and deliberately not claimed: over-block (face 1) is a COST, not a
bypass — it is fixed here, but it is not part of the exemption surface.
"""

from __future__ import annotations

from tools.ci_exemption import (
    Failure,
    Rate,
    decide,
    parse_failed_ids,
    parse_rates,
)

ID = "tests/test_dr_endpoints.py::TestDrDrill::test_restores_to_scratch"


def _f(failures: int, runs: int, *sigs: str) -> Failure:
    return Failure(rate=Rate(failures, runs), signatures=frozenset(sigs))


# ── the fail-closed parser ────────────────────────────────────────────────


def test_parser_rejects_a_stray_non_nodeid_line():
    """A bare `may` token must NOT enter the union (the measured artifact)."""
    parsed = parse_failed_ids(f"may\nFAILED {ID}\n")

    assert parsed.ids == [ID]
    assert "may" in parsed.rejected, "a stray token must be rejected AND counted"


def test_parser_rejects_a_failed_line_whose_payload_is_not_a_nodeid():
    """`FAILED may` is not a failure record — the payload is not a nodeid."""
    parsed = parse_failed_ids("FAILED may\n")

    assert parsed.ids == []
    assert parsed.rejected == ["FAILED may"]


def test_parser_is_fail_closed_when_nothing_parses():
    """An unreadable set must not present as an empty one."""
    parsed = parse_failed_ids("some prose\nanother line\n")

    assert parsed.ids == []
    assert parsed.ok is False, "zero parsed ids must not read as a valid empty set"
    assert len(parsed.rejected) == 2


def test_parser_accepts_both_FAILED_and_ERROR_and_dedupes():
    parsed = parse_failed_ids(f"FAILED {ID}\nERROR {ID}\n")

    assert parsed.ids == [ID], "one id, deduped"
    assert parsed.rejected == []


def test_stray_line_cannot_buy_an_exemption():
    """THE POINT OF THE PARSER RULE: junk must not weaken the gate.

    `may` must never end up in the set a PR is excused against.
    """
    parsed = parse_failed_ids("may\n")
    main_rates = {i: Rate(4, 4) for i in parsed.ids}

    decision = decide({ID: _f(4, 4, "AssertionError: x")}, main_rates,
                      main_signatures={ID: frozenset({"AssertionError: x"})},
                      k_main=4, k_pr=4)

    assert decision.any_blocked, "a junk union must not exempt anything"
    assert main_rates == {}, "the stray token bought no entry"


# ── E1 · presence-in-window immunity ──────────────────────────────────────


def test_E1_a_single_main_flake_does_not_grant_immunity():
    """One unrelated main failure must not excuse a PR that now breaks it.

    main failed it 1/8; the PR breaks it 8/8. Under a presence test ("it fails on
    main too") this was EXCUSED. Rate comparison must BLOCK it.
    """
    decision = decide(
        {ID: _f(8, 8, "AssertionError: boom")},
        {ID: Rate(1, 8)},
        main_signatures={ID: frozenset({"AssertionError: boom"})},
        k_main=8,
        k_pr=8,
    )

    assert decision.any_blocked
    assert not decision.exempt
    assert "materially higher" in decision.blocked[0].reason


# ── E2 · id-granularity immunity ──────────────────────────────────────────


def test_E2_same_id_different_failure_is_not_exempt():
    """A PR holding the RATE while breaking a DIFFERENT assertion must BLOCK.

    This is the case a rate comparison ALONE cannot catch: same id, same rate,
    different signature.
    """
    decision = decide(
        {ID: _f(4, 8, "AssertionError: the PR's new failure")},
        {ID: Rate(4, 8)},
        main_signatures={ID: frozenset({"ConnectionError: socket gone"})},
        k_main=8,
        k_pr=8,
    )

    assert decision.any_blocked
    assert "signature differs" in decision.blocked[0].reason


def test_E2_matching_signature_at_the_same_rate_is_exempt():
    """The complement: SAME signature and SAME rate IS the exemption."""
    decision = decide(
        {ID: _f(4, 8, "ConnectionError: socket gone")},
        {ID: Rate(4, 8)},
        main_signatures={ID: frozenset({"ConnectionError: socket gone"})},
        k_main=8,
        k_pr=8,
    )

    assert not decision.any_blocked
    assert len(decision.exempt) == 1


# ── E3 · rate-blindness ───────────────────────────────────────────────────


def test_E3_main_1_of_8_vs_pr_8_of_8_blocks():
    """The canonical counterexample, asserted directly."""
    decision = decide(
        {ID: _f(8, 8, "sg")}, {ID: Rate(1, 8)},
        main_signatures={ID: frozenset({"sg"})}, k_main=8, k_pr=8,
    )

    assert decision.any_blocked, "an 8x deterministic regression must not ship"


def test_E3_equivalent_rates_are_exempt():
    decision = decide(
        {ID: _f(3, 8, "sg")}, {ID: Rate(4, 8)},
        main_signatures={ID: frozenset({"sg"})}, k_main=8, k_pr=8,
    )

    assert not decision.any_blocked


def test_E3_small_k_cannot_excuse():
    """One main observation cannot establish a rate — fail closed."""
    decision = decide(
        {ID: _f(8, 8, "sg")}, {ID: Rate(1, 1)},
        main_signatures={ID: frozenset({"sg"})}, k_main=1, k_pr=8, min_runs=3,
    )

    assert decision.any_blocked
    assert any("insufficient evidence" in n for n in decision.notes)


# ── E4 · the exemption must be VISIBLE ────────────────────────────────────


def test_E4_an_exemption_is_recorded_with_both_rates():
    """A green with no exemption line FAILS this test, not passes it."""
    decision = decide(
        {ID: _f(4, 8, "sg")}, {ID: Rate(4, 8)},
        main_signatures={ID: frozenset({"sg"})}, k_main=8, k_pr=8,
    )

    lines = decision.visible_exemptions()
    assert len(lines) == 1, "an exemption must be recorded, never silent"
    assert lines[0].startswith("EXEMPT:")
    assert "4/8" in lines[0], "the exemption must carry the measured rates"
    assert "k_main=8" in lines[0] and "k_pr=8" in lines[0]
    assert lines[0] in decision.report()


# ── window-boundary · in the ACCEPTANCE, not the threat surface ───────────


def test_window_boundary_is_no_longer_a_distinction():
    """A main-side red at ANY position is exempt — there is no window to miss.

    Previously a red outside the sampled window was invisible BY CONSTRUCTION and
    the failure read as PR-unique. With a re-measurement the position is not an
    input at all: only the measured rate is. This case is in the acceptance
    precisely because it is fixed as a CONSEQUENCE of the effect change.
    """
    decision = decide(
        {ID: _f(4, 10, "sg")}, {ID: Rate(4, 10)},
        main_signatures={ID: frozenset({"sg"})}, k_main=10, k_pr=10,
    )

    assert not decision.any_blocked, "no window exists to be outside of"
    assert "Window" not in decision.report() and "position" not in decision.report()


def test_an_id_main_never_failed_is_always_pr_unique():
    decision = decide(
        {ID: _f(1, 10, "sg")}, {}, k_main=10, k_pr=10,
    )

    assert decision.any_blocked
    assert "no main-side measurement" in decision.blocked[0].reason


# ── verdict-stability · the ACCEPTANCE test ───────────────────────────────


def test_verdict_stability_same_input_twice_same_verdict():
    """THE acceptance: a verdict that changes on re-evaluation is not a verdict.

    This single test catches every instance of the family found so far — B3's case
    cleared on the rail's retry, and mine blocked three times, with the SAME
    relationship to main in both. Sampling alignment decided the outcome.
    """
    pr = {
        ID: _f(8, 8, "sg"),
        "tests/test_other.py::test_b": _f(4, 8, "sg2"),
    }
    main = {ID: Rate(1, 8), "tests/test_other.py::test_b": Rate(4, 8)}
    sigs = {ID: frozenset({"sg"}), "tests/test_other.py::test_b": frozenset({"sg2"})}

    first = decide(pr, main, main_signatures=sigs, k_main=8, k_pr=8)
    second = decide(pr, main, main_signatures=sigs, k_main=8, k_pr=8)

    assert first.report() == second.report(), "the verdict must not depend on the draw"
    assert [v.nodeid for v in first.blocked] == [v.nodeid for v in second.blocked]


def test_verdict_stability_across_a_wider_sample():
    """More evidence changes the MEASUREMENT, never the RULE for the same data."""
    pr = {ID: _f(8, 8, "sg")}
    sigs = {ID: frozenset({"sg"})}

    narrow = decide(pr, {ID: Rate(1, 8)}, main_signatures=sigs, k_main=8, k_pr=8)
    wide = decide(pr, {ID: Rate(1, 8)}, main_signatures=sigs, k_main=8, k_pr=8)

    assert narrow.any_blocked == wide.any_blocked


# ── substrate-misfire · the fix must be correct when the substrate lies ───


def test_substrate_misfire_does_not_launder_a_pr_caused_failure():
    """Flaky main AND a genuinely worse PR must still BLOCK.

    The fix must be correct WHEN THE SUBSTRATE LIES, not depend on it being
    healthy. Here main is flaky at 2/10 and the PR is 10/10.
    """
    decision = decide(
        {ID: _f(10, 10, "sg")}, {ID: Rate(2, 10)},
        main_signatures={ID: frozenset({"sg"})}, k_main=10, k_pr=10,
    )

    assert decision.any_blocked


# --------------------------------------------------------------------------
# The consumer: reading `ci-failure-set.sh --main-union-rates` (#3756)
# --------------------------------------------------------------------------


def test_parse_rates_reads_the_emitted_wire_format():
    """`<nodeid>\t<failures>\t<runs>` — exactly what the shell mode writes."""
    wire = "tests/a.py::T::t1\t8\t8\ntests/b.py::T::t2\t1\t8\n"
    r = parse_rates(wire)
    assert r.rejected == []
    assert r.runs == 8
    assert r.rates["tests/a.py::T::t1"] == Rate(8, 8)
    assert r.rates["tests/b.py::T::t2"] == Rate(1, 8)


def test_parse_rates_rejects_a_vacuous_run_count():
    """`0/0` must not read as "main never fails this".

    `Rate(0, 0).rate` is `0.0` — a zero-evidence pass. The line is rejected rather
    than trusted, and the id then has NO rate, which fails closed (no exemption).
    """
    r = parse_rates("tests/a.py::T::t1\t0\t0\n")
    assert r.rates == {}
    assert r.rejected == ["tests/a.py::T::t1\t0\t0"]
    assert r.runs == 0


def test_parse_rates_rejects_malformed_and_impossible_lines():
    r = parse_rates(
        "tests/a.py::T::t1\t3\n"              # too few fields
        "tests/a.py::T::t2\t3\t8\textra\n"  # too many
        "FAILED tests/a.py::T::t3\n"           # a union line, not a rate line
        "tests/a.py::T::t4\tx\t8\n"          # non-numeric
        "tests/a.py::T::t5\t9\t8\n"          # failures > runs
    )
    assert r.rates == {}
    assert len(r.rejected) == 5


def test_E3_via_the_real_wire_format_blocks_a_deterministic_regression():
    """The live case, driven through the format the shell actually emits.

    Main's own table says it failed the id in 1 of 8 runs; the PR fails it 8 of 8
    with the SAME signature. A presence test ("it fails on main too") exempts this.
    The rate comparison must not: an eight-fold jump is a regression the PR
    introduced, and the fix that matters is the one that gets this right.
    """
    main = parse_rates("tests/a.py::T::t1\t1\t8\n")
    pr = {"tests/a.py::T::t1": Failure(rate=Rate(8, 8), signatures=frozenset({"sg"}))}
    decision = decide(
        pr, main.rates,
        main_signatures={"tests/a.py::T::t1": frozenset({"sg"})},
        k_main=main.runs, k_pr=8, rate_tolerance=1.5,
    )
    assert [v.nodeid for v in decision.blocked] == ["tests/a.py::T::t1"]
    assert decision.visible_exemptions() == []
    assert "rates" in decision.report().lower() or "BLOCK" in decision.report()


def test_the_gate_does_not_loosen_as_the_substrate_degrades():
    """#3756's compounding finding, as a single assertion.

    Substrate flakiness puts more ids into main's union. Under presence-based
    subtraction each one becomes an exemption, so the gate's discriminating power
    FALLS as real health falls — confidence inversely coupled to health. Here main
    is BADLY broken (it fails 3 of 8 runs) and the SAME table must still block a PR
    at 8/8 — a 2.7x jump, far outside tolerance. The table cannot be read as
    "everything is excused".
    """
    main = parse_rates("tests/a.py::T::t1\t3\t8\n")
    innocent = {"tests/a.py::T::t1": Failure(rate=Rate(3, 8), signatures=frozenset({"sg"}))}
    d_ok = decide(innocent, main.rates, main_signatures={"tests/a.py::T::t1": frozenset({"sg"})},
                  k_main=8, k_pr=8)
    assert d_ok.visible_exemptions(), "the innocent PR must be exempt, visibly"

    worse = {"tests/a.py::T::t1": Failure(rate=Rate(8, 8), signatures=frozenset({"sg"}))}
    d_no = decide(worse, main.rates, main_signatures={"tests/a.py::T::t1": frozenset({"sg"})},
                  k_main=8, k_pr=8)
    assert not d_no.visible_exemptions(), "a PR worse than a broken main is still a regression"
