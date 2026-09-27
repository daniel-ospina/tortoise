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
2. an unreadable census / fork counter is INCONCLUSIVE — never a PASS, or the
   non-vacuity proof would be void;
3. a NON-timeout error is a product verdict and FAILs even alongside a load
   event — the file's own producer analysis says a wedge surfaces as a
   refusal/timeout, so a later timeout must not mask the refusal;
4. a client socket-budget expiry is INCONCLUSIVE (evidence about the host);
5. only a fully-observed healthy race PASSes.
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
    # 2a. the census could not be READ — never "no children" (a wedge would
    #     look green).
    if result["sampling_unavailable"]:
        inconclusive(
            "the daemon child census (`ps`) could not be read, so a wedge "
            "cannot be ruled out",
            deadline_s=race_deadline_s,
            diagnosis=f"the census failure is not 'no children': {result}",
        )
    # 2b. the fork counter could not be read, so the race is vacuous.
    if result["forks"] is None:
        inconclusive(
            "the fork counter (`INFO`) could not be read, so non-vacuity is "
            "unproven (a PASS here would prove nothing)",
            deadline_s=socket_budget_s,
            diagnosis=f"last error {result['last_err']!r}",
        )
    # 2c. the shared client socket budget expired before the graph could even
    #     be set up — the same load class as any other budget expiry.
    if result.get("setup_timeout_err"):
        inconclusive(
            "the fixture's setup query exceeded the client socket budget "
            "under load",
            deadline_s=socket_budget_s,
            diagnosis=result["setup_timeout_err"],
        )
    # 3. a non-timeout error is a product verdict, BEFORE any timeout skip: a
    #    refusal (the wedge's own signature) must never be masked by a later
    #    socket-budget expiry.
    assert result["non_timeout_errors"] == 0, (
        f"{result['non_timeout_errors']} non-timeout error(s) under load: "
        f"{result['last_non_timeout_err']!r}; full: {result}"
    )
    # 4. a client socket call exceeded its budget under load — the host, not
    #    the property.
    if result["timeout_errors"]:
        inconclusive(
            "a client socket call exceeded its budget under load",
            deadline_s=socket_budget_s,
            diagnosis=(
                f"{result['timeout_errors']} timeout(s) of "
                f"{result['attempts']} attempts: {result['last_err']!r}"
            ),
        )
    # 5. non-vacuity: this asserts the fork path RAN, from redis's own counter,
    #    so a green result cannot come from a copy that never forked.
    assert result["forks"] >= result["copies"] > 0, (
        f"the fork path did not run: {result}"
    )
    if result["verify_timeout"]:
        inconclusive(
            "the DR-restore verification copy exceeded the client socket "
            "budget under load",
            deadline_s=socket_budget_s,
            diagnosis=result["verify_err"],
        )
    assert result["last_err"] is None, result
    assert result["verify_err"] is None, result
    assert result["clone_nodes"] == 1, result
    assert result["swapped_nodes"] == 1, result


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
