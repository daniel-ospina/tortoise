"""#5049 — the verdict contract: a test verdict is a function of the code under test.

The parent issue's refactor direction, encoded once so no site re-invents it:

1. **No fixed wall-clock budget may produce FAIL.** A wait becomes an event/condition
   wait (`wait_for`); where a deadline is still needed, its expiry is an explicit
   INCONCLUSIVE skip that *names the deadline* (`inconclusive`, `require_within`,
   `inconclusive_on_timeout`) — never a product-defect FAIL.
2. **Sufficiency of a deadline is a measured property, not a guess.** `Deadline`
   compares a budget against a *recorded* (checked-in) endpoint time and raises
   `HarnessDefect` when the margin is absent. It never measures the endpoint live:
   a live sample on a loaded host would itself manufacture the FAIL rule 1 forbids.
3. Exact equality on a derived quantity is a numerical-tolerance surface and is
   deliberately **not** expressed here (see the plan's Remaining members).
4. **Process globals reset per test** (`PROCESS_GLOBALS`, `reset_process_globals`,
   `AMBIENT_ENV_GLOBALS`) — an order-dependent outcome is a harness defect.
5. **A host capability gap is a SKIP, not a FAIL** (`host_capability`,
   `node_meets`, `require_node_floor`).

Design constraints:

* **Stdlib + pytest only** at module import. `tortoise.*` targets are resolved
  lazily inside `reset_process_globals`, so this module can be imported at
  `tests/conftest.py` module level without perturbing the #1686 test-mode import
  ordering.
* The INCONCLUSIVE skip reason is a **declared family** (`INCONCLUSIVE_PREFIX`)
  that `tools/skip-guard.py` exempts, so a load-induced skip cannot be
  re-classified as an availability regression by the skip guard.
"""

from __future__ import annotations

import dataclasses
import importlib
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, NoReturn

import pytest

#: The declared reason family for "the property could not be observed in the
#: deadline". `tools/skip-guard.py` exempts this prefix; the wording is part of
#: the contract, not a per-site string.
INCONCLUSIVE_PREFIX = "INCONCLUSIVE [#5049]"

#: A harness (test-substrate) defect — the harness is at fault, not the change.
#: This is deliberately a FAIL: rule 4 says an order-dependent / under-specified
#: outcome must fail the *harness*, and this is how it is attributed.
_HARNESS_PREFIX = "HARNESS DEFECT [#5049]"


class HarnessDefect(AssertionError):
    """The test substrate itself is defective (rename, under-specified budget)."""


def inconclusive(what: str, *, deadline_s: float, diagnosis: str | None = None) -> NoReturn:
    """Skip with the declared INCONCLUSIVE reason, naming the deadline.

    ``what`` names the property that was not observed; ``deadline_s`` is the
    budget that expired. This is the ONLY exit for a wall-clock expiry (rule 1).
    """
    detail = (
        f"{INCONCLUSIVE_PREFIX}: {what} — not observed within the "
        f"{deadline_s:g}s deadline."
    )
    if diagnosis:
        detail = f"{detail} {diagnosis}"
    detail = (
        f"{detail} A deadline expiry is evidence about machine load, not about "
        f"the property under test (#5049 rule 1)."
    )
    pytest.skip(detail)


def wait_for(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    interval_s: float = 0.02,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Event/condition wait: True as soon as ``predicate()`` holds, else False at the deadline.

    This is the rule-1 primitive. It never raises on timeout — the caller decides
    the verdict (normally `require_within`, which skips).
    """
    deadline = clock() + timeout_s
    while True:
        if predicate():
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleep(min(interval_s, remaining))


def require_within(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    what: str,
    diagnosis: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """`wait_for`, but a deadline expiry is INCONCLUSIVE (rule 1)."""
    if not wait_for(predicate, timeout_s=timeout_s, clock=clock):
        inconclusive(what, deadline_s=timeout_s, diagnosis=diagnosis)


@contextmanager
def inconclusive_on_timeout(
    *,
    what: str,
    deadline_s: float,
    exceptions: Sequence[type[BaseException]] = (TimeoutError,),
    diagnosis: str | None = None,
) -> Iterator[None]:
    """Convert a *deadline-bearing* timeout into INCONCLUSIVE, never FAIL.

    ``exceptions`` must name the *narrow* class that the callee's own deadline
    raises (e.g. ``redis.exceptions.TimeoutError``). Do **not** catch a broad
    programming error class here — a defect that merely happens to surface as a
    timeout is a product verdict, not a machine one, and wrapping it would turn a
    regression into a skip. Prefer `require_within` where a condition is
    observable; use this only for an API whose own timeout IS the deadline.
    """
    try:
        yield
    except tuple(exceptions) as exc:  # type: ignore[misc]
        inconclusive(
            what,
            deadline_s=deadline_s,
            diagnosis=diagnosis or f"({type(exc).__name__}: {exc})",
        )


@dataclasses.dataclass(frozen=True)
class Deadline:
    """A named budget with its *recorded* endpoint time (rule 2).

    ``recorded_s`` is a static, checked-in measurement of the endpoint/service
    the site observes — never a live sample. ``assert_sufficient`` raises
    `HarnessDefect` when the budget lacks the required margin over it, so an
    under-specified deadline fails the harness at the point it is declared
    rather than flaking under load.
    """

    name: str
    budget_s: float
    recorded_s: float
    margin_factor: float = 2.0

    def assert_sufficient(self) -> Deadline:
        floor = self.recorded_s * self.margin_factor
        if self.budget_s < floor:
            raise HarnessDefect(
                f"{_HARNESS_PREFIX}: deadline {self.name!r} budget "
                f"{self.budget_s:g}s has no measured margin over the recorded "
                f"endpoint {self.recorded_s:g}s (needs >= {floor:g}s, "
                f"margin x{self.margin_factor:g}). A budget chosen without a "
                f"recorded margin is what #5049 rule 2 rejects."
            )
        return self


# ── host capability (rule 5) ──────────────────────────────────────────────
def host_capability(probe: Callable[[], bool], *, what: str, remedy: str) -> None:
    """Skip when a host capability is absent — never FAIL."""
    if not probe():
        pytest.skip(f"HOST CAPABILITY [#5049]: {what} unavailable — {remedy}")


# Node floors are PER INVOCATION (a shared constant would be wrong for one of
# them): a `--experimental-strip-types` driver needs the flag (22.7, the floor
# `tests/test_capture_spool.py::_node_that_can_strip_ts` already records),
# while a bare `node --test <file>.ts` relies on type stripping being default-on.
NODE_FLOOR_STRIP_TYPES: tuple[int, int] = (22, 7)


def node_version(node: str = "node") -> tuple[int, int] | None:
    """``(major, minor)`` for the node on PATH, or None when absent/unprobeable.

    None covers absent, a non-zero `--version` exit, empty/garbage output — all
    host-capability gaps, never product verdicts.
    """
    try:
        proc = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    m = re.match(r"v(\d+)\.(\d+)(?:\.|$)", proc.stdout.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def node_meets(floor: tuple[int, int], node: str = "node") -> bool:
    """True when the node on PATH is at or above ``floor`` (a prerelease below the floor is not)."""
    version = node_version(node)
    return version is not None and version >= floor


def require_node_floor(
    floor: tuple[int, int],
    *,
    what: str,
    node: str = "node",
    absent: str = "skip",
) -> None:
    """Skip when node is absent or older than ``floor``.

    ``absent="fail"`` preserves a site's recorded decision that a *missing* node
    must fail loudly (a skipped guard can look like a passing one); even then a
    present-but-too-old node SKIPs, because it is a host-capability gap (rule 5).
    """
    if shutil.which(node) is None:
        if absent == "fail":
            pytest.fail(
                f"no node runtime for {what} — need node >= "
                f"{floor[0]}.{floor[1]}; refusing to skip, because a skipped "
                f"guard looks like a passing one"
            )
        host_capability(
            lambda: False,
            what=f"a Node runtime for {what}",
            remedy=f"install Node >= {floor[0]}.{floor[1]}",
        )
        return
    version = node_version(node)
    if version is None:
        # present but unprobeable (garbage/non-zero --version) — a capability gap
        host_capability(
            lambda: False,
            what=f"a probeable Node runtime for {what}",
            remedy=f"ensure `node --version` reports >= {floor[0]}.{floor[1]}",
        )
        return
    if version < floor:
        pytest.skip(
            f"HOST CAPABILITY [#5049]: node v{version[0]}.{version[1]} is below "
            f"the {floor[0]}.{floor[1]} floor required by {what} — a host "
            f"capability gap is a SKIP, not a FAIL (rule 5)"
        )


# ── process globals (rule 4) ──────────────────────────────────────────────
@dataclasses.dataclass(frozen=True)
class ProcessGlobal:
    """A process-global attribute and the value it must be reset to per test."""

    dotted: str
    clean: Any
    why: str


#: Module attributes reset before AND after every test. Add here — not as a
#: second per-module fixture — so one declaration owns the reset.
PROCESS_GLOBALS: tuple[ProcessGlobal, ...] = (
    ProcessGlobal(
        "tortoise.embedded_lifecycle._atexit_deadline",
        None,
        "#4913/#4879: the exit-seam budget is once-armed and never re-armed, so "
        "a mid-run seam call would otherwise arm a 30 s budget for every later test",
    ),
)

#: Ambient env vars deleted per test (via monkeypatch, which restores them).
#: `TORTOISE_API_URL` makes the suite non-hermetic on a fleet shell: the ask lane
#: fails loud on it (`ask_lane.py:438,848`) and the commit client routes to it
#: (`sdk.py` `_post_commit`). Clearing it makes a local run match CI.
AMBIENT_ENV_GLOBALS: tuple[str, ...] = ("TORTOISE_API_URL",)


def reset_process_globals(
    globals_: Sequence[ProcessGlobal] | None = None,
) -> list[str]:
    """Reset every declared process global; return the names reset.

    ``globals_`` defaults to `PROCESS_GLOBALS`; passing an explicit sequence
    keeps the rename-must-fail test from having to mutate module state.

    A declared attribute that does not exist raises `HarnessDefect` — a rename
    must fail loud, never no-op into a silently-created attribute.
    """
    reset: list[str] = []
    for spec in (PROCESS_GLOBALS if globals_ is None else globals_):
        module_path, _, attr = spec.dotted.rpartition(".")
        module = importlib.import_module(module_path)
        if not hasattr(module, attr):
            raise HarnessDefect(
                f"{_HARNESS_PREFIX}: process-global {spec.dotted!r} does not "
                f"exist — the declared name was renamed or removed, so the per-test "
                f"reset would silently no-op ({spec.why})."
            )
        setattr(module, attr, spec.clean)
        reset.append(spec.dotted)
    return reset
