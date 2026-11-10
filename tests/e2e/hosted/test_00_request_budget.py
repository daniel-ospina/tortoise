"""Pin the `api` fixture's request budget against the measured floor (#11576).

The `api` fixture in `conftest.py` used to hard-code `timeout=30_000`. That value
was chosen to fail fast on a degraded server, but it sat inside the variance band
of a healthy run: the same tree measured 21.06s for `test_01`'s whole four-request
chain on a green run (42704270992 @ `10638ca7`, suite 357.46s) and blew the 30s
budget on a 1.49x-slower runner (42711667194 @ `9c64675`, suite 532.67s, POST
/v1/points timed out). A red on a healthy suite is a false red on a check the
fleet reads; the fix is a budget above the measurement, not a re-run.

This pin is what stops the number from being tightened back by feel — it asserts
the FLOOR and that the fixture still passes the constant (so a dead constant
cannot satisfy it).

Non-vacuity (measured, one mutation per arm — they are NOT the same edit, and
this docstring said they were until #11576's review, P3):
  * restoring the LITERAL in the fixture — `timeout=30_000` in `new_context(...)`
    while `REQUEST_TIMEOUT_MS` stays 60_000 — fails
    `test_the_api_fixture_passes_the_budget_and_not_its_own_literal` (its
    `timeout=REQUEST_TIMEOUT_MS` arm); the FLOOR arm below stays GREEN, because it
    reads the constant, not the fixture's argument.
  * setting the CONSTANT to 30_000 — the fixture still passes it, so the
    fixture arm stays green — fails the FLOOR arm here.
So neither arm alone pins both directions: a defined-but-unused constant is
caught by the fixture arm, and a constant lowered under the measurement is caught
by the floor arm.
"""
from __future__ import annotations

from pathlib import Path

from conftest import REQUEST_TIMEOUT_MS, skip_unless_hosted_e2e

skip_unless_hosted_e2e()

CONFTEST = Path(__file__).with_name("conftest.py")

# The slowest measured HEALTHY chain is 21.06s (`test_01`'s FOUR-REQUEST call,
# green run 42704270992) and the observed red point is ~31.4s (the same chain on
# a 1.49x slower runner). 30s — the budget this PR replaced — is between the two,
# which is why a healthy suite redded. The floor is set at 2x the observed red
# point.
MEASURED_HEALTHY_CHAIN_S = 21.06
OBSERVED_RED_POINT_S = 31.4
FLOOR_MS = 60_000


def test_the_request_budget_leaves_headroom_over_the_measured_healthy_chain():
    assert REQUEST_TIMEOUT_MS >= FLOOR_MS, (
        "the `api` fixture's per-request budget dropped to "
        f"{REQUEST_TIMEOUT_MS}ms, under the {FLOOR_MS}ms floor derived from "
        f"measurement: a healthy CHAIN takes {MEASURED_HEALTHY_CHAIN_S}s and the "
        f"observed red point (a 1.49x-slower runner) is ~{OBSERVED_RED_POINT_S}s, "
        "so a budget below that reds a HEALTHY suite. Raise the budget rather "
        "than waiting for the next slow runner."
    )
    # #11576 review (mutation note): this arm used to compare the CONSTANT against
    # `OBSERVED_RED_POINT_S`, which the assertion above already implies — unreachable
    # while `REQUEST_TIMEOUT_MS >= 60_000`, so it added no second bound. It now
    # bounds the FLOOR itself, which no other arm reads: lowering `FLOOR_MS` under
    # the red point (leaving the constant at 60_000) would otherwise red nothing and
    # let the next re-measurement re-derive the budget from a point already observed
    # to time out.
    assert MEASURED_HEALTHY_CHAIN_S < OBSERVED_RED_POINT_S, (
        "the observed red point must sit ABOVE the healthy measurement — that "
        f"gap IS the variance band ({MEASURED_HEALTHY_CHAIN_S}s healthy vs "
        f"{OBSERVED_RED_POINT_S}s red); a re-derivation that inverted them would "
        "size the budget from a healthy figure"
    )
    assert FLOOR_MS > OBSERVED_RED_POINT_S * 1000, (
        "the FLOOR no longer clears the point at which a healthy run was already "
        f"observed to time out ({OBSERVED_RED_POINT_S}s), not just a round number"
    )


def test_the_api_fixture_passes_the_budget_and_not_its_own_literal():
    """A defined-but-unused constant satisfies nothing — the fixture must USE it."""
    source = CONFTEST.read_text()
    assert "timeout=REQUEST_TIMEOUT_MS" in source, (
        "`api`'s `new_context(...)` no longer passes REQUEST_TIMEOUT_MS — the "
        "budget this file pins is now dead code and the fixture may be passing "
        "a literal of its own (the 30_000 this change replaced)"
    )
    assert "timeout=30_000" not in source, (
        "the 30_000 literal is back in conftest.py; that is the budget measured "
        "too tight for a healthy run (see this module's docstring)"
    )


def test_the_budget_comment_attributes_the_measurement_it_actually_has():
    """#11576 review (P3, two findings): the DERIVATION's prose is pinned, not just its value.

    `conftest.py`'s comment is where the next budget is re-derived from, so a
    sentence that overstates what was measured propagates into the next number.
    Two defects shipped in the head commit and are pinned here:

      * it called the floor "the slowest measured healthy **request**" while every
        figure it cites is a CHAIN total (`--durations` reports per-test CALL
        time), and
      * it never named the command those figures come from, so the arithmetic was
        not re-derivable from the repo.

    Both arms are non-vacuous against the pre-fix comment: it contained the word
    "request" in the derivation sentence and no `--durations`.
    """
    source = CONFTEST.read_text()
    assert "slowest measured healthy request" not in source, (
        "conftest.py's budget comment claims a PER-REQUEST measurement again. "
        "`--durations` reports per-test CALL wall time, so the 21.06s / 31.4s "
        "figures are `test_01`'s whole four-request chain — call it a chain, or "
        "commit a per-request artifact that says otherwise (#11576 review P3)"
    )
    assert "chain" in source, (
        "the budget comment no longer names the unit it measured (a request "
        "CHAIN) — that attribution is what the 60s floor is derived from"
    )
    assert "--durations" in source, (
        "the comment states figures without naming the command that produces "
        "them. The budget's derivation must be re-derivable: name the "
        "`--durations` table (and the run id), or commit the raw artifact "
        "(#11576 review P3)"
    )
    # and the scope claim: this change moves ONE budget, not the suite's.
    assert "SCOPE" in source and "test_03_billing_upgrade.py" in source, (
        "the comment must state that the other request sites keep their own "
        "budgets (eight of them, at 10s and 45s) — otherwise the fix reads as "
        "\"the whole hosted suite is now above the variance band\" (#11576 P3)"
    )
