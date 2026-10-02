"""#5049 — tests for the verdict contract (`tests/_verdict.py`).

These pin the contract's discriminating power:

* a deadline expiry is INCONCLUSIVE and *names the deadline* — the old
  fixed-budget shape (a bare `assert` on the same input) FAILs, which is the
  sabotage leg proving the conversion changes the verdict;
* a *recorded* margin is what `Deadline` judges — never a live measurement;
* a host-capability gap SKIPs;
* process globals are reset before every test (order-independence is the
  property, and its removal goes RED).
"""

from __future__ import annotations

import os
import re
import time

import pytest

from tests import _verdict
from tests._fork_safety_verdict import (
    assert_fixed_race_verdict,
    healthy_result,
)
from tests._verdict import (
    AMBIENT_ENV_GLOBALS,
    PROCESS_GLOBALS,
    Deadline,
    HarnessDefect,
    ProcessGlobal,
    host_capability,
    inconclusive_on_timeout,
    node_meets,
    require_node_floor,
    require_within,
    reset_process_globals,
    wait_for,
)

_ATEXIT = "tortoise.embedded_lifecycle._atexit_deadline"


class _ClientTimeout(TimeoutError):
    """Stands in for redis's client socket-budget expiry (#4742)."""


# ── rule 1: a wall-clock expiry is INCONCLUSIVE, never FAIL ────────────────
def test_wait_for_returns_false_at_the_deadline():
    assert wait_for(lambda: False, timeout_s=0.02, interval_s=0.005) is False


def test_wait_for_returns_true_when_the_condition_becomes_true():
    calls = {"n": 0}

    def predicate() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    assert wait_for(predicate, timeout_s=1.0, interval_s=0.001) is True


def test_deadline_expiry_skips_and_names_the_deadline():
    with pytest.raises(pytest.skip.Exception) as excinfo:
        require_within(lambda: False, timeout_s=0.05, what="the probe")
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "0.05" in message, "the deadline must be named"


def test_sabotage_the_old_fixed_budget_shape_fails_the_same_input():
    """The shape #5049 replaces: `assert <condition>` after a fixed budget.

    The same never-true predicate that now SKIPs used to FAIL. This is the
    pin's must-fail leg — it proves the conversion changes the verdict, not
    just the wording.
    """
    with pytest.raises(AssertionError):
        assert wait_for(lambda: False, timeout_s=0.02, interval_s=0.005), (
            "the property was not observed within 0.02s")


def test_narrow_timeout_becomes_inconclusive_and_names_the_budget():
    with pytest.raises(pytest.skip.Exception) as excinfo, inconclusive_on_timeout(
        what="the probe", deadline_s=4.0, exceptions=(_ClientTimeout,)
    ):
        raise _ClientTimeout("Timeout reading from socket")
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "4" in message


def test_a_non_timeout_error_is_not_swallowed():
    """A defect that merely runs inside the block must still FAIL."""
    with pytest.raises(ValueError), inconclusive_on_timeout(
        what="the probe", deadline_s=4.0, exceptions=(_ClientTimeout,)
    ):
        raise ValueError("a real product defect")


# ── rule 2: sufficiency is a RECORDED property, never a live measurement ───
def test_deadline_sufficient_returns_itself():
    deadline = Deadline("probe", budget_s=100.0, recorded_s=17.0)
    assert deadline.assert_sufficient() is deadline


def test_deadline_insufficient_recorded_margin_is_a_harness_defect():
    # 20 s budget against a 17 s recorded endpoint is the #4736 counter-example.
    with pytest.raises(HarnessDefect):
        Deadline("probe", budget_s=20.0, recorded_s=17.0).assert_sufficient()


def test_deadline_never_measures_the_endpoint_live(monkeypatch):
    class _FakeTime:
        @staticmethod
        def monotonic() -> float:  # pragma: no cover - only fires on a live measurement
            raise AssertionError("assert_sufficient must not measure the endpoint live")

    # patch the module REFERENCE, not the stdlib module (which pytest itself uses)
    monkeypatch.setattr(_verdict, "time", _FakeTime)
    Deadline("probe", budget_s=100.0, recorded_s=17.0).assert_sufficient()


# ── rule 4: process globals reset per test ─────────────────────────────────
def test_process_globals_registry_is_pinned():
    """A shrinking registry must go RED — parametrizing over it would not."""
    assert {spec.dotted for spec in PROCESS_GLOBALS} == {_ATEXIT}


def test_reset_process_globals_clears_an_armed_budget():
    from tortoise import embedded_lifecycle

    embedded_lifecycle._atexit_deadline = time.monotonic() + 30
    reset = reset_process_globals()
    assert embedded_lifecycle._atexit_deadline is None
    assert _ATEXIT in reset


def test_reset_process_globals_fails_loud_on_a_renamed_global():
    """A rename must fail the harness, not silently no-op."""
    bogus = (ProcessGlobal("tortoise.embedded_lifecycle._no_such_deadline", None, "test"),)
    with pytest.raises(HarnessDefect):
        reset_process_globals(bogus)


# Order-independence pin: this test runs after the one above in definition
# order (the suite has no pytest-randomly). The autouse
# `_process_global_isolation` reset must have cleared what was armed — an
# ordered outcome here is a HARNESS defect (#5049 rule 4), not the change.
def test_zz_armed_global_does_not_leak_into_the_next_test_part_1():
    from tortoise import embedded_lifecycle

    embedded_lifecycle._atexit_deadline = time.monotonic() + 30


def test_zz_armed_global_does_not_leak_into_the_next_test_part_2():
    from tortoise import embedded_lifecycle

    assert embedded_lifecycle._atexit_deadline is None, (
        "the suite-wide per-test process-global reset did not run — an armed "
        "exit-seam budget leaked into a later test (#4913). This is a HARNESS "
        "defect, not a product one.")


@pytest.fixture(scope="session", autouse=True)
def _seed_ambient_api_url():
    """Seed the ambient delegation var so its per-test deletion is non-vacuous.

    The fleet shell exports `TORTOISE_API_URL`; CI does not. Seeding it here
    means the assertion below is meaningful on a clean host too, and the
    conftest reset must remove it before every test body.
    """
    previous = os.environ.get("TORTOISE_API_URL")
    os.environ["TORTOISE_API_URL"] = "https://prod.invalid"
    yield
    if previous is None:
        os.environ.pop("TORTOISE_API_URL", None)
    else:
        os.environ["TORTOISE_API_URL"] = previous


def test_ambient_delegation_var_is_cleared_per_test():
    assert "TORTOISE_API_URL" in AMBIENT_ENV_GLOBALS
    assert os.environ.get("TORTOISE_API_URL") is None, (
        "the ambient TORTOISE_API_URL reached a test body — the suite is not "
        "hermetic (#4017); a seeded value must be deleted before every test")


# ── rule 5: a host-capability gap is a SKIP ────────────────────────────────
def test_host_capability_skips_when_absent():
    with pytest.raises(pytest.skip.Exception):
        host_capability(lambda: False, what="Node", remedy="install Node")


def test_host_capability_is_a_noop_when_present():
    host_capability(lambda: True, what="Node", remedy="install Node")


@pytest.mark.parametrize(
    "version,ok",
    [((22, 6), False), ((22, 7), True), ((23, 6), True), ((24, 1), True), (None, False)],
)
def test_node_meets_compares_the_major_minor_floor(monkeypatch, version, ok):
    monkeypatch.setattr(_verdict, "node_version", lambda node="node": version)
    assert node_meets((22, 7)) is ok


def test_node_version_parses_the_runtime(monkeypatch):
    """The `node_version` regex — the one probe the node tests above stub out.

    A prerelease is compared AT its numeric floor (``v22.7.0-rc.1`` -> (22, 7)),
    which is what makes `node_meets` a capability-band test rather than a
    build-identity test; garbage, a non-zero exit, and an unspawnable binary are
    all host-capability gaps (None), never a product verdict.
    """

    class _Completed:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    seen = {"stdout": "v22.7.0\n", "rc": 0}
    monkeypatch.setattr(
        _verdict.subprocess,
        "run",
        lambda *a, **k: _Completed(seen["stdout"], seen["rc"]),
    )
    for stdout, expected in [
        ("v22.7.0\n", (22, 7)),
        ("v22.7\n", (22, 7)),
        ("v22.7.0-rc.1\n", (22, 7)),
        ("v24.1.0\n", (24, 1)),
        ("garbage\n", None),
        ("\n", None),
    ]:
        seen["stdout"], seen["rc"] = stdout, 0
        assert _verdict.node_version() == expected, stdout
    seen["stdout"], seen["rc"] = "v22.7.0\n", 1
    assert _verdict.node_version() is None, "a non-zero --version is unprobeable"

    def _unspawnable(*a, **k):
        raise OSError("no node")

    monkeypatch.setattr(_verdict.subprocess, "run", _unspawnable)
    assert _verdict.node_version() is None, "an unspawnable node is unprobeable"


def test_require_node_floor_skips_below_the_floor(monkeypatch):
    monkeypatch.setattr(_verdict.shutil, "which", lambda _n: "/usr/bin/node")
    monkeypatch.setattr(_verdict, "node_version", lambda node="node": (22, 6))
    with pytest.raises(pytest.skip.Exception):
        require_node_floor((22, 7), what="a driver")


def test_require_node_floor_skips_an_unprobeable_node(monkeypatch):
    monkeypatch.setattr(_verdict.shutil, "which", lambda _n: "/usr/bin/node")
    monkeypatch.setattr(_verdict, "node_version", lambda node="node": None)
    with pytest.raises(pytest.skip.Exception):
        require_node_floor((22, 7), what="a driver")


def test_require_node_floor_skips_an_absent_node_by_default(monkeypatch):
    monkeypatch.setattr(_verdict.shutil, "which", lambda _n: None)
    with pytest.raises(pytest.skip.Exception):
        require_node_floor((22, 7), what="a driver")


def test_require_node_floor_fails_an_absent_node_when_the_site_says_so(monkeypatch):
    """A security guard's recorded `absent=fail` policy is preserved."""
    monkeypatch.setattr(_verdict.shutil, "which", lambda _n: None)
    with pytest.raises(pytest.fail.Exception):
        require_node_floor((22, 7), what="the blog guard", absent="fail")


def test_node_driver_site_gates_on_the_floor(monkeypatch):
    """Wiring pin: the converted admin-origin site SKIPs on a too-old Node."""
    monkeypatch.setattr(_verdict.shutil, "which", lambda _n: "/usr/bin/node")
    monkeypatch.setattr(_verdict, "node_version", lambda node="node": (22, 6))
    from tests import test_admin_origin_redirect as admin

    with pytest.raises(pytest.skip.Exception):
        admin._run([])


# ── the #3845 fork-safety classifier (#4742/#5049), extracted for #5049 ─────
# It lives in `tests/_fork_safety_verdict.py` rather than in the platform-gated
# evidence file because that file's def count is pinned in
# `config/ci-expected-nodeids/platform-gated.txt`. These pin its POLARITY, which
# is the whole point of the extraction: structural evidence beats a clock, and
# an unobservable property is INCONCLUSIVE — never a pass.
def _must_fail(result: dict, *, match: str) -> None:
    """Assert the classifier FAILs — never PASSes, and never SKIPs.

    The INCONCLUSIVE exit is a `pytest.skip.Exception`, which is NOT an
    `AssertionError`: under the regressed branch ordering the classifier SKIPs,
    so a bare `pytest.raises(AssertionError)` would let that escape as a *green*
    skip — the very regression these pins exist to catch would read as "not
    applicable", and no skip budget covers this file. A skip here is a failure.
    """
    try:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    except pytest.skip.Exception as exc:
        pytest.fail(
            "the classifier reported INCONCLUSIVE where a FAIL is required — "
            f"the regression this pins: {exc}"
        )
    except AssertionError as exc:
        assert re.search(match, str(exc)), str(exc)
        return
    pytest.fail("the classifier PASSED where a FAIL is required")


def test_fixed_race_healthy_result_is_the_pass_shape():
    assert_fixed_race_verdict(
        healthy_result(), race_deadline_s=15.0, socket_budget_s=4.0
    )


def test_fixed_race_parked_child_fails_even_alongside_a_timeout():
    """A parked child is a FAIL, and a client timeout in the SAME run must not
    downgrade it to INCONCLUSIVE (the wedge is structural, not a clock)."""
    result = healthy_result()
    result["hung"] = [(4242, 6.0, 0.0)]
    result["errors"] = 3
    result["timeout_errors"] = 3
    result["last_err"] = "Timeout reading from socket"
    _must_fail(result, match=r"parked.*4242")


def test_fixed_race_non_timeout_refusal_fails_even_alongside_a_timeout():
    """A refusal (the wedge's own signature) is a product FAIL; a later
    timeout in the same run must not mask it as a load-class skip."""
    result = healthy_result()
    result["errors"] = 2
    result["non_timeout_errors"] = 1
    result["last_non_timeout_err"] = "ConnectionError: the fork slot is wedged"
    result["timeout_errors"] = 1
    result["last_err"] = "Timeout reading from socket"
    _must_fail(result, match="non-timeout")


@pytest.mark.parametrize(
    "unobservable", [{"sampling_unavailable": True}, {"forks": None}]
)
def test_fixed_race_refusal_fails_despite_an_unreadable_metric(unobservable):
    """A refusal is read from the CLIENT, so an unusable harness metric (`ps`
    census or `INFO` counter) must not downgrade it to INCONCLUSIVE — the same
    masking the timeout branch is forbidden from doing."""
    result = healthy_result()
    result.update(unobservable)
    result["errors"] = 1
    result["non_timeout_errors"] = 1
    result["last_non_timeout_err"] = "ConnectionError: the fork slot is wedged"
    _must_fail(result, match="non-timeout")


def test_fixed_race_unreadable_census_is_inconclusive_never_a_pass():
    result = healthy_result()
    result["sampling_unavailable"] = True
    with pytest.raises(pytest.skip.Exception) as excinfo:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "census" in message


def test_fixed_race_unreadable_fork_counter_is_inconclusive_never_a_pass():
    result = healthy_result()
    result["forks"] = None
    with pytest.raises(pytest.skip.Exception) as excinfo:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    assert "fork counter" in str(excinfo.value)


def test_fixed_race_setup_budget_expiry_names_the_setup_not_the_counter():
    """A setup-budget expiry leaves ``forks`` at its initial None. If the
    counter branch were read first the cause would be misreported as an
    unreadable counter with ``last error None``; the setup budget must be named.
    """
    result = healthy_result()
    result["forks"] = None
    result["setup_timeout_err"] = "TimeoutError: Timeout reading from socket"
    with pytest.raises(pytest.skip.Exception) as excinfo:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    message = str(excinfo.value)
    assert "setup query" in message
    assert "fork counter" not in message


def test_fixed_race_client_timeout_is_inconclusive():
    result = healthy_result()
    result["errors"] = 1
    result["timeout_errors"] = 1
    result["last_err"] = "Timeout reading from socket"
    with pytest.raises(pytest.skip.Exception) as excinfo:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    assert "INCONCLUSIVE [#5049]" in str(excinfo.value)


@pytest.mark.parametrize(
    "load_event",
    [
        {
            "errors": 1,
            "timeout_errors": 1,
            "last_err": "Timeout reading from socket",
        },
        {"sampling_unavailable": True},
        {"forks": None},
    ],
)
def test_fixed_race_verify_refusal_fails_despite_a_load_event(load_event):
    """The DR-restore leg carries the wedge's own signature too, so a
    non-timeout refusal there FAILs — a race timeout or an unreadable harness
    metric must not mask it."""
    result = healthy_result()
    result.update(load_event)
    result["verify_err"] = "ResponseError: could not fork"
    _must_fail(result, match="verify leg refused")


def test_fixed_race_verify_timeout_is_inconclusive_never_a_fail():
    result = healthy_result()
    result["verify_err"] = "TimeoutError: Timeout reading from socket"
    result["verify_timeout"] = True
    result["clone_nodes"] = None
    with pytest.raises(pytest.skip.Exception) as excinfo:
        assert_fixed_race_verdict(
            result, race_deadline_s=15.0, socket_budget_s=4.0
        )
    assert "INCONCLUSIVE [#5049]" in str(excinfo.value)


def test_fixed_race_wrong_clone_count_fails_despite_a_race_timeout():
    """The clone counts are observed whenever the verify leg completed, so a
    wrong count is a data-integrity FAIL even alongside a load event."""
    result = healthy_result()
    result["errors"] = 1
    result["timeout_errors"] = 1
    result["last_err"] = "Timeout reading from socket"
    result["clone_nodes"] = 0
    _must_fail(result, match="clone_nodes")
