"""The x86_64 runner installer's two invariants: it sizes against measured headroom, and it
never lets a runner claim an architecture (or a hosted version pin) that is not its own.

WHY BEHAVIOURAL, NOT TEXT-SCRAPED
    A guard that greps the script for a string passes when the string moves and fails when the
    code is merely reformatted (the class agent-infra#1426 records: semantics-preserving edits win
    against text readers). So both invariants are exercised by RUNNING the installer:
      * the capacity guard, through `--dry-run` (which computes every headroom bound and stops
        before any network, token, download, or registration);
      * the label invariant, through `--print-labels`, which calls the SAME `resolve_labels` the
        install path feeds to `config.sh`.
    ⛔ THE LABEL TESTS MUST NEVER SKIP. They used to skip whenever the headroom guard refused
    (this box's memory available hovers around the reserve), which left the one invariant the file
    exists to protect untested on a loaded host — a false PASS. `--print-labels` sits after every
    guard precisely so asking "which labels would this runner get" cannot depend on headroom.

WHAT IS BEING PINNED
  1. `--count N` is refused (exit 2) when N runners would eat the cores the agent lanes need. This
     box is a CI runner AND an agent host; capacity that starves the lanes is not capacity (#5215).
  2. The labels are `self-hosted-x64` plus `ubuntu-latest` ONLY when the fallback is engaged:
     `self-hosted-arm64` would let an x86 box serve an arm64-specific job (a false PASS, not a
     delay), and `ubuntu-<version>` is the version-pinned HOSTED label five legs need. The
     fallback label is never permanent — it follows the gate's state (`--fallback on|off|auto`),
     and `auto` must NOT silently equal `on`.
  3. A dry run decides nothing and calls nothing: no token, no download, no label read.
  4. No job name, no `runs-on:`, and no required check is touched by this change. That is a
     property of the PR diff (tools/, docs/ops/, docs/00_index.md), which no test of this file can
     see — the diff is the artifact.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "tools" / "install-ci-runner-x64.sh"

pytestmark = pytest.mark.skipif(not INSTALLER.exists(), reason="installer not present")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        capture_output=True,
        text=True,
        env=dict(os.environ, CI_RUNNER_REPO="daniel-ospina/tortoise"),
    )


def _nproc() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # pragma: no cover - non-Linux
        return os.cpu_count() or 1


def test_labels_are_this_archs_own_and_follow_the_fallback():
    """`--fallback on` adds ONLY the gate's fallback label; `off` leaves the arch marker alone.

    No skip path: this is the invariant the file exists for, and it must hold on a loaded host.
    """
    on = _run("--print-labels", "--fallback", "on")
    off = _run("--print-labels", "--fallback", "off")
    auto = _run("--print-labels")
    for r, why in ((on, "--fallback on"), (off, "--fallback off"), (auto, "auto")):
        assert r.returncode == 0, f"{why}: {r.stderr}"
        assert r.stdout.strip(), f"{why} printed no label set: {r.stderr}"

    assert on.stdout.strip() == "self-hosted-x64,ubuntu-latest"
    assert off.stdout.strip() == "self-hosted-x64"
    # `auto` decides at install from the pool's state; it must never silently equal `on`
    assert auto.stdout.strip() == "self-hosted-x64", (
        "auto must not attach the fallback label without reading the gate's state")

    for r in (on, off, auto):
        for alien in ("self-hosted-arm64", "ARM64", "ubuntu-24.04", "ubuntu-22.04"):
            assert alien not in r.stdout, f"installer would hand config.sh {alien!r}"


def test_count_is_refused_when_it_would_starve_the_agent_lanes():
    """4 runners on an 8-core box = 4x2 cores + 2 reserved = 10 > 8 → REFUSED, exit 2.

    A box with >=10 cores legitimately allows it, so the assertion is on the RELATIONSHIP
    (the guard's own arithmetic), not on a magic count. This check does not depend on memory or
    load, so it cannot skip: the cpu bound is a pure function of the core count.
    """
    ncpu = _nproc()
    too_many = (ncpu - 2) // 2 + 1          # first count the guard must refuse
    assert too_many >= 1, "host has no core to spend on CI"
    r = _run("--count", str(too_many), "--dry-run")
    assert r.returncode == 2, f"count={too_many} on {ncpu} cores must be refused: {r.stderr}"
    assert "REFUSED" in r.stderr and "cpu headroom" in r.stderr
    # and the refusal names the arithmetic, so an operator can re-size rather than guess
    assert f"{too_many}x2" in r.stderr and f"this box has {ncpu}" in r.stderr


def test_dry_run_decides_nothing_and_calls_nothing():
    """A plan is the operator's step: no token, no download, no registration — refusal or not."""
    r = _run("--count", "1", "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode in (0, 2), r.stderr
    if r.returncode == 2:
        # a headroom refusal is a legitimate answer on a loaded host, but it must be a NAMED
        # refusal — and it still must not have touched a mutation surface.
        assert "REFUSED" in r.stderr and "headroom" in r.stderr, r.stderr
    else:
        assert "dry-run: would" in out
    for forbidden in ("registration-token", "remove-token", "downloading", "sha256 verified"):
        assert forbidden not in out, f"a dry run reached {forbidden!r}: {out}"
