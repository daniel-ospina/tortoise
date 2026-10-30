"""#3653: the CI redislite cleanup must never reap a live peer's server.

The defect these pin: `python-ci.yml` reclaimed stale embedded-redis litter by
running a host-global, unconditional ``pkill -f "redis-server unixsocket"`` in
the setup of every embedded leg. `pkill -f` matches *every* process on the box,
and this repo's own orphan gate measured ten other live pytest suites on the
same host (`other_suites=10`), so a leg that started later killed a concurrent
leg's live instance. redislite unlinks its socket on that SIGTERM, so the
victim's next connect raised ``Error 2 connecting to <socket>. No such file or
directory`` — ENOENT, not ECONNREFUSED — and the victim leg reddened with
assertions that had nothing to do with its diff.

Two things are pinned here:

* the DECISION TABLE of the replacement (``.github/scripts/reap-redislite-orphans.py``):
  a server is reaped only on a proof that no running leg can own it, and every
  unmeasurable case keeps it;
* the WORKFLOW SHAPE — no leg may reap by process name again. That half fails
  on the pre-fix tree, which is what makes the guard non-vacuous rather than a
  restatement of the fix.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "reap-redislite-orphans.py"
PYTHON_CI = REPO_ROOT / ".github" / "workflows" / "python-ci.yml"

# The legs that install the package and then clean up before their suite — the
# four that carried the blind `pkill`.
EMBEDDED_LEGS = ("test", "test-slow", "test-concurrency-falkor", "test-carve-out")


def _load_module():
    spec = importlib.util.spec_from_file_location("reap_redislite_orphans", SCRIPT)
    assert spec is not None and spec.loader is not None, SCRIPT
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def reaper():
    return _load_module()


# ── the decision table ────────────────────────────────────────────────────


def test_a_live_peers_young_server_is_kept(reaper, tmp_path):
    """THE #3653 REGRESSION: a young server with a live socket dir is a peer's."""
    row = {"pid": 1, "socket_dir": str(tmp_path), "age_seconds": 90}
    reap, reason = reaper.classify(row, max_age_s=45 * 60)
    assert reap is False, reason
    assert "concurrent leg" in reason


def test_an_aged_server_is_reaped(reaper, tmp_path):
    """Past the widest leg watchdog nothing running can own it."""
    row = {"pid": 1, "socket_dir": str(tmp_path), "age_seconds": 46 * 60}
    reap, reason = reaper.classify(row, max_age_s=45 * 60)
    assert reap is True, reason
    assert "watchdog" in reason


def test_a_server_whose_socket_dir_is_gone_is_reaped(reaper, tmp_path):
    """The #1005 residue: the dir is gone, so the instance can serve nothing."""
    gone = tmp_path / "vanished"
    row = {"pid": 1, "socket_dir": str(gone), "age_seconds": 1}
    reap, reason = reaper.classify(row, max_age_s=45 * 60)
    assert reap is True, reason
    assert "is gone" in reason


def test_an_unmeasurable_age_on_a_live_dir_is_kept(reaper, tmp_path):
    """Fail CLOSED: no age is not evidence of abandonment."""
    row = {"pid": 1, "socket_dir": str(tmp_path), "age_seconds": None}
    reap, reason = reaper.classify(row, max_age_s=45 * 60)
    assert reap is False, reason
    assert "fail closed" in reason


def test_undeterminable_ownership_is_kept(reaper):
    """An argv this code cannot read must never authorise a kill."""
    row = {"pid": 1, "socket_dir": None, "age_seconds": 10 * 60 * 60}
    reap, reason = reaper.classify(row, max_age_s=45 * 60)
    assert reap is False, reason
    assert "undeterminable" in reason


@pytest.mark.parametrize(
    "argv,expected",
    [
        # inline colon form (macOS / redislite config directive)
        (["redis-server", "unixsocket:/tmp/tt_ab/tmpcd/redis.socket"], "/tmp/tt_ab/tmpcd"),
        # long-form flag with the path as the next argv element
        (["redis-server", "--unixsocket", "/tmp/tt_ab/tmpcd/redis.socket"], "/tmp/tt_ab/tmpcd"),
        # space-separated directive (no dashes)
        (["redis-server", "unixsocket", "/tmp/tt_ab/tmpcd/redis.socket"], "/tmp/tt_ab/tmpcd"),
        # config-file form redislite uses on macOS
        (["redis-server", "/tmp/tt_ab/tmpcd/redis.config", "--port", "0"], "/tmp/tt_ab/tmpcd"),
        # no socket at all -> unknown, never a kill
        (["redis-server", "/etc/redis/redis.conf"], None),
        ([], None),
    ],
)
def test_socket_dir_from_argv_covers_every_emitter_shape(reaper, argv, expected):
    assert reaper.socket_dir_from_argv(argv) == expected


# ── the action: dry run signals nothing, apply signals only the reaped rows ──


def test_a_dry_run_never_signals(reaper, tmp_path, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(reaper.os, "kill", lambda pid, sig: killed.append(pid))
    rows = [{"pid": 4242, "socket_dir": str(tmp_path / "gone"), "age_seconds": 1}]
    assert reaper.run(rows, max_age_s=45 * 60, apply=False) == 0
    assert killed == [], "a dry run must never signal"


def test_apply_signals_exactly_the_reapable_rows(reaper, tmp_path, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(reaper.os, "kill", lambda pid, sig: killed.append(pid))
    rows = [
        {"pid": 11, "socket_dir": str(tmp_path), "age_seconds": 60},  # live peer
        {"pid": 22, "socket_dir": str(tmp_path / "gone"), "age_seconds": 60},  # litter
        {"pid": 33, "socket_dir": str(tmp_path), "age_seconds": 99 * 60},  # aged
    ]
    assert reaper.run(rows, max_age_s=45 * 60, apply=True) == 0
    assert killed == [22, 33], killed


def test_the_cli_reads_its_inventory_from_the_proc_json_seam(tmp_path):
    """End-to-end through `main()`: the seam is what CI-side tests can drive."""
    gone = tmp_path / "gone"
    inventory = tmp_path / "proc.json"
    inventory.write_text(
        json.dumps(
            [
                {"pid": 7, "socket_dir": str(tmp_path), "age_seconds": 60},
                {"pid": 8, "socket_dir": str(gone), "age_seconds": 60},
            ]
        )
    )
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--proc-json", str(inventory)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert re.search(r"^KEEP pid=7\b", out.stdout, re.M), out.stdout
    assert re.search(r"^REAP pid=8\b", out.stdout, re.M), out.stdout
    assert "dry-run" in out.stdout


# ── the workflow shape: the blind, host-global reap must not come back ──────


def test_no_ci_step_reaps_embedded_redis_by_process_name():
    """NON-VACUOUS: this reds on the pre-fix workflow.

    `pkill -f "redis-server unixsocket"` cannot distinguish a leaked server
    from a concurrent leg's live one — that indistinguishability IS #3653. It is
    scanned across EVERY workflow, because the same step was parity-ported
    (`post-merge-validation.yml`, #1439) and a fix to one copy alone would leave
    the other killing peers.
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"pkill\b.*redis-server", line):
                offenders.append(f"{path.name}:{i}")
    assert offenders == [], (
        'the host-global `pkill -f "redis-server unixsocket"` reaps a concurrent '
        "leg's live instance (#3653); use .github/scripts/reap-redislite-orphans.py: "
        + ", ".join(offenders)
    )


def test_every_cleaning_job_invokes_the_scoped_reaper():
    """Every workflow that reaps redislite litter must call the scoped tool."""
    for name in ("python-ci.yml", "post-merge-validation.yml"):
        text = (REPO_ROOT / ".github" / "workflows" / name).read_text()
        assert "reap-redislite-orphans.py --apply" in text, (
            f"{name} no longer reclaims redislite litter at all — the #1005 "
            "accumulation guard is gone with the blind pkill"
        )


@pytest.mark.parametrize("leg", EMBEDDED_LEGS)
def test_every_embedded_leg_invokes_the_scoped_reaper(leg):
    """Each of the four cleaning legs must call the scoped tool, not nothing."""
    text = PYTHON_CI.read_text()
    start = text.index(f"\n  {leg}:\n")
    # The next top-level job key, or end of file.
    nxt = re.search(r"\n  [A-Za-z0-9_-]+:\n", text[start + 1 :])
    block = text[start : start + 1 + nxt.start()] if nxt else text[start:]
    assert "reap-redislite-orphans.py" in block, (
        f"{leg} no longer reclaims redislite litter at all — the #1005 "
        "accumulation guard is gone with the blind pkill"
    )
