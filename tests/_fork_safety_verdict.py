"""#5049 — the verdict classifier for the #3845 fork-safety race.

Pure: it maps the race's observables to a verdict and imports only the contract.
It lives here rather than in ``tests/test_fork_safety_3845.py`` because that file
is a PLATFORM-GATED evidence file — ``tests/test_markers.py::PLATFORM_GATED_TESTS``
declares it as "the producer + MUTATION PROOF" and
``config/ci-expected-nodeids/*.txt`` PINS ITS DEF COUNT. A pure unit test of this
classifier is not platform-gated evidence, so adding one there inflates a frozen
manifest. Splitting the classifier out lets its unit tests live with the
contract instead.

Polarity, in this order (#5049 rule 1 — a fixed wall-clock budget never produces
a FAIL):

1. a parked child is a FAIL regardless of any timing (the wedge is structural);
2. a NON-timeout error at EITHER stage — the race or the DR-restore verify — is
   a product verdict read from the CLIENT, and FAILs even alongside a load event
   or an unusable harness metric (a refusal must never be downgraded to
   INCONCLUSIVE because a `ps`/`INFO` probe or a socket timeout also fired);
3. a fixture-SETUP socket-budget expiry is INCONCLUSIVE, read BEFORE the fork
   counter — because it is the *cause* of that counter being unreadable;
4. a verify-stage socket-budget expiry is INCONCLUSIVE, read BEFORE the clone
   counts it can leave unobserved;
5. an unreadable census / fork counter is INCONCLUSIVE — never a PASS, or the
   non-vacuity proof would be void;
6. a race-stage client socket-budget expiry is INCONCLUSIVE (the host, not the
   property);
7. only a fully-observed healthy race PASSes.
"""

from __future__ import annotations

from tests._verdict import inconclusive


def assert_fixed_race_verdict(
    result: dict,
    *,
    race_deadline_s: float,
    socket_budget_s: float,
) -> None:
    """Apply the polarity above to the observables ``_race`` collected."""
    # 1. structural evidence beats a clock: the un-fixed shape parks a child
    #    forever, and no timeout on the client changes what that means.
    assert result["hung"] == [], (
        f"module-fork child parked {result['hung']} with the fork-safe "
        f"config; last error {result['last_err']!r}"
    )
    # 2a. a RACE-stage non-timeout error is a product verdict, read from the
    #     CLIENT — so it FAILs regardless of timing AND regardless of any harness
    #     metric the branches below could not read. A refusal (the wedge's own
    #     signature) must never be downgraded to INCONCLUSIVE because a `ps`
    #     census or an `INFO` counter also failed in the same loaded run.
    assert result["non_timeout_errors"] == 0, (
        f"{result['non_timeout_errors']} non-timeout error(s) under load: "
        f"{result['last_non_timeout_err']!r}; full: {result}"
    )
    # 2b. a VERIFY-stage non-timeout error is the same product verdict. The
    #     DR-restore leg is the shape that "used to die with could not fork", so
    #     a refusal there must not be hidden by a race timeout, an unreadable
    #     metric, or any other load-class branch below.
    assert result["verify_err"] is None or result["verify_timeout"], (
        f"the DR-restore verify leg refused (non-timeout): "
        f"{result['verify_err']!r}; full: {result}"
    )
    # 3. the shared client socket budget expired before the graph could even be
    #    set up. `_race` returns before the race on this, leaving `forks` at its
    #    initial None — so this MUST be read before the counter branch below, or
    #    the setup expiry is misattributed to an unreadable counter ("last error
    #    None") instead of the budget that actually expired.
    if result.get("setup_timeout_err"):
        inconclusive(
            "the fixture's setup query exceeded the client socket budget "
            "under load",
            deadline_s=socket_budget_s,
            diagnosis=result["setup_timeout_err"],
        )
    # 4. the verify-stage copy exceeded its client socket budget: it can leave
    #    the clone counts unobserved, so it is read before the count asserts.
    if result["verify_timeout"]:
        inconclusive(
            "the DR-restore verification copy exceeded the client socket "
            "budget under load",
            deadline_s=socket_budget_s,
            diagnosis=result["verify_err"],
        )
    # 5. the clone counts are structural data-integrity evidence. They are
    #    observed whenever the verify leg completed (setup succeeded and verify
    #    neither refused nor timed out), so they FAIL even alongside a load
    #    event — a wrong count is a defect, not a machine condition.
    assert result["clone_nodes"] == 1, result
    assert result["swapped_nodes"] == 1, result
    # 6a. the census could not be READ — never "no children" (a wedge would
    #     look green).
    if result["sampling_unavailable"]:
        inconclusive(
            "the daemon child census (`ps`) could not be read, so a wedge "
            "cannot be ruled out",
            deadline_s=race_deadline_s,
            diagnosis=f"the census failure is not 'no children': {result}",
        )
    # 6b. the fork counter could not be read, so the race is vacuous.
    if result["forks"] is None:
        inconclusive(
            "the fork counter (`INFO`) could not be read, so non-vacuity is "
            "unproven (a PASS here would prove nothing)",
            deadline_s=socket_budget_s,
            diagnosis=f"last error {result['last_err']!r}",
        )
    # 6c. a race-stage client socket call exceeded its budget under load — the
    #     host, not the property.
    if result["timeout_errors"]:
        inconclusive(
            "a client socket call exceeded its budget under load",
            deadline_s=socket_budget_s,
            diagnosis=(
                f"{result['timeout_errors']} timeout(s) of "
                f"{result['attempts']} attempts: {result['last_err']!r}"
            ),
        )
    # 7. non-vacuity: this asserts the fork path RAN, from redis's own counter,
    #    so a green result cannot come from a copy that never forked.
    assert result["forks"] >= result["copies"] > 0, (
        f"the fork path did not run: {result}"
    )
    assert result["last_err"] is None, result


def healthy_result(*, copies: int = 5, forks: int = 6) -> dict:
    """A fully-observed healthy race result (the PASS shape)."""
    return {
        "copies": copies,
        "attempts": copies,
        "errors": 0,
        "non_timeout_errors": 0,
        "last_err": None,
        "last_non_timeout_err": None,
        "hung": [],
        "forks": forks,
        "clone_nodes": 1,
        "swapped_nodes": 1,
        "verify_err": None,
        "verify_timeout": False,
        "timeout_errors": 0,
        "sampling_unavailable": False,
        "setup_timeout_err": None,
    }
