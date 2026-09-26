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
import time

import pytest

from tests import _verdict
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
