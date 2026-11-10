"""Pin the hosted suite's PER-REQUEST budget against the measured floor (#11576).

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

Non-vacuity (measured): with `timeout=30_000` restored in the fixture's
`new_context(...)` call, the floor arm reds; with the literal restored and the
constant left defined, the "fixture uses the constant" arm reds — a dead constant
is exactly how this pin would otherwise be satisfied by nothing.
"""
from __future__ import annotations

from pathlib import Path

from conftest import REQUEST_TIMEOUT_MS, skip_unless_hosted_e2e

skip_unless_hosted_e2e()

CONFTEST = Path(__file__).with_name("conftest.py")

# The slowest measured HEALTHY request chain is 21.06s (`test_01`, green run
# 42704270992) and the observed red point is ~31.4s (the same chain on a 1.49x
# slower runner). 30s — the budget this PR replaced — is between the two, which
# is why a healthy suite redded. The floor is set at 2x the observed red point.
MEASURED_HEALTHY_CHAIN_S = 21.06
OBSERVED_RED_POINT_S = 31.4
FLOOR_MS = 60_000


def test_the_request_budget_leaves_headroom_over_the_measured_healthy_chain():
    assert REQUEST_TIMEOUT_MS >= FLOOR_MS, (
        "the hosted suite's per-request budget dropped to "
        f"{REQUEST_TIMEOUT_MS}ms, under the {FLOOR_MS}ms floor derived from "
        f"measurement: a healthy chain takes {MEASURED_HEALTHY_CHAIN_S}s and the "
        f"observed red point (a 1.49x-slower runner) is ~{OBSERVED_RED_POINT_S}s, "
        "so a budget below that reds a HEALTHY suite. Raise the budget rather "
        "than waiting for the next slow runner."
    )
    assert REQUEST_TIMEOUT_MS > OBSERVED_RED_POINT_S * 1000, (
        "the budget must clear the point at which a healthy run was already "
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
