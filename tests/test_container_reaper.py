"""The test-lane container REAPER'S ACTUATOR — `tools/container_reaper.py`.

WHY THIS FILE EXISTS, AND WHY IT IS NOT ANOTHER REAPER
------------------------------------------------------
`tools/test_lane.py::reap_orphans()` already decides, correctly, which
`fdb-lane-*` containers are out of service (three ownership proofs plus an age
backstop; #8180, #10693, #10712). What it never had is a **caller**: it is
invoked from `start()` and from nothing else, so **no scheduler on any box could
call it at all**. Measured on the cell `cmux` (2026-11-03): the only scheduled
sweep is `eldato-disk-reaper.timer`, whose classes are `uv / venv / worktree /
duplicate-install` — a case-insensitive `grep` for `docker` or `container` over the
reaper and its delegates returns **0**.

So the leak was never a decision defect. It was the **actuator** the owner named:
*"an actuator that cannot deliver is a defect, not a status."* This module is the
entry point a timer can call, and these tests pin the three properties that make
it safe to put on a timer:

  * **dry-run is the DEFAULT** — only `--apply` may remove anything, and the dry
    run is not a parallel reimplementation: it runs the REAL `reap_orphans()`
    with the removal syscall intercepted, so a dry run can never disagree with
    what `--apply` would do;
  * **exactly the provably-orphaned container is removed**, and a container with
    a live owning lane is never touched — no matter how old it is (age is a
    backstop, never the proof);
  * **it is idempotent** — a second `--apply` over the same population removes
    nothing, so a 15-minute timer cannot churn.

Nothing here talks to Docker: `_docker` is the tool's single docker chokepoint
(`tools/test_lane.py`), and every test stubs it, because the properties that must
hold are the ACTUATOR'S CONTRACT, not Docker's behaviour.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import container_reaper as cr  # noqa: E402
from tools import test_lane as tl  # noqa: E402


class _R:
    """Just enough of a CompletedProcess for the docker seam."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


#: The orphan: its owning worktree no longer exists on disk (proof 1 — the one
#: proof #8180 authorises unconditionally).
ORPHAN = "fdb-lane-aaaaaaaaaa"


@pytest.fixture(autouse=True)
def _reap_state_dir_is_isolated(tmp_path, monkeypatch):
    """The lease/marker registry is REAL box state, so no test here may touch it.

    `REAP_STATE_DIR` defaults to `~/.pi/agent/state/lane-reap/`. The sweeping
    tests drive the real `reap_orphans()`, and its proofs 2/3 consult and REWRITE
    that registry (`_touch_lease` / `_drop_lease`) — so without this fixture a
    test both reads whatever a LIVE peer lane left behind and writes into the
    developer's own state. That is a test whose verdict depends on the box, on a
    file registered to a docker-free CI surface (#10813 review P3-7).

    The sibling file (`tests/test_test_lane_tool.py`) has carried the identical
    fixture since the same review; this file drives the REAL sweep, so it needs
    it at least as much.
    """
    monkeypatch.setattr(tl, "REAP_STATE_DIR", tmp_path / "lane-reap")
#: A container whose owning lane is LIVE, and one whose owner worktree exists but
#: whose lane holds nothing — the two populations a timer must never remove.
LIVE = "fdb-lane-bbbbbbbbbb"
IDLE = "fdb-lane-cccccccccc"

_PS_HEADER = ("{{.Names}}\t{{.State}}\t{{.CreatedAt}}\t"
              '{{.Label "tortoise.test-lane.created"}}\t'
              '{{.Label "tortoise.test-lane.worktree"}}')

#: A real `docker ps -a` rendering, including the `CreatedAt` shape Docker
#: actually prints ("2026-11-03 22:28:01 +0000 UTC") — the parser is pinned
#: against the real format rather than a convenient ISO string.
#: ⛔ The LIVE row is the OLDEST on purpose. `test_an_OLD_container_…` used to
#: stamp it the YOUNGEST, so its "age is never the proof" claim was vacuous — and
#: a reviewer proved the assertion passed under a broken liveness proof
#: (#10806 review P2-8). The ages are RELATIVE TO NOW (Docker prints an absolute
#: stamp), so the rows are rendered per run by `_rows()`.
def _rows(now: float | None = None) -> str:
    import time as _t
    now = _t.time() if now is None else now
    def stamp(seconds_ago: float) -> str:
        return _t.strftime("%Y-%m-%d %H:%M:%S +0000 UTC",
                           _t.gmtime(now - seconds_ago))
    return (
        f"{ORPHAN}\trunning\t{stamp(6 * 3600)}\t\t\n"     # a young orphan
        f"{LIVE}\trunning\t{stamp(30 * 3600)}\t\t\n"      # 30 h old, owner LIVE
        # RUNNING, not `exited`: an exited container hits the WITHHELD
        # stopped-arm first, so the lane-gone proof (the arm under test) is never
        # reached and the liveness assertion would be spared for the wrong reason.
        f"{IDLE}\trunning\t{stamp(20 * 3600)}\t\t\n"      # 20 h, owner idle
    )


PS_ROWS = _rows()
SIZE_ROWS = (
    f"{ORPHAN}\t5.78MB (virtual 252MB)\n"
    f"{LIVE}\t5.78MB (virtual 252MB)\n"
    f"{IDLE}\t5.78MB (virtual 252MB)\n"
)


class _Docker:
    """A docker seam that records every call and lets a test kill one surface."""

    def __init__(self, *, ps_rc=0, sizes_rc=0):
        self.calls: list[tuple[str, ...]] = []
        self.ps_rc, self.sizes_rc = ps_rc, sizes_rc
        self.rows = PS_ROWS
        self.size_rows = SIZE_ROWS

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("ps", "-a") and "-s" in args:
            return _R(self.sizes_rc, self.size_rows if self.sizes_rc == 0 else "",
                      "" if self.sizes_rc == 0 else "docker ps -s failed")
        if args[:2] == ("ps", "-a"):
            # `reap_orphans` asks for `{{.Names}}\t{{.State}}`; the actuator asks
            # for the richer template. Answer both from the same population so a
            # disagreement between them cannot be what a test passes on.
            if args[-1] == _PS_HEADER:
                return _R(self.ps_rc, self.rows if self.ps_rc == 0 else "",
                          "" if self.ps_rc == 0 else "docker ps failed")
            rows = "".join(
                f"{line.split(chr(9))[0]}\t{line.split(chr(9))[1]}\n"
                for line in self.rows.splitlines())
            return _R(self.ps_rc, rows if self.ps_rc == 0 else "",
                      "" if self.ps_rc == 0 else "docker ps failed")
        if args[:2] == ("rm", "-f"):
            return _R(0, args[2] + "\n", "")
        return _R(0, "", "")

    @property
    def removals(self) -> list[str]:
        return [a[2] for a in self.calls if a[:2] == ("rm", "-f")]


@pytest.fixture
def box(monkeypatch, tmp_path):
    """A host with one orphan, one live lane and one idle-but-present worktree."""
    orphan_wt = tmp_path / "gone" / "orphan-wt"      # deliberately NOT created
    live_wt = tmp_path / "live-wt"
    idle_wt = tmp_path / "idle-wt"
    live_wt.mkdir()
    idle_wt.mkdir()

    docker = _Docker()
    monkeypatch.setattr(tl, "_docker", docker)
    # Ownership is `slug -> worktree path` as `reap_orphans` reads it. The slugs
    # are the 10-hex digests the container NAMES carry, so the reaper resolves
    # each of these three containers to its owner by the same lookup it uses in
    # production.
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {
        "aaaaaaaaaa": str(orphan_wt),
        "bbbbbbbbbb": str(live_wt),
        "cccccccccc": str(idle_wt),
    })
    # Only the LIVE lane holds a process. `_live_lane_paths` reads /proc in
    # production; here it is the one datum each test varies.
    monkeypatch.setattr(tl, "_live_lane_paths",
                        lambda: {str(live_wt), str(live_wt / "tools")})
    monkeypatch.setattr(tl, "_container_in_use", lambda name: False)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-0123456789")
    # The sweep must not consult the lease registry of the real box.
    monkeypatch.setattr(tl, "_started_epoch", lambda name: None)
    # ⛔ THE ORPHAN IS COLD, NOT MERELY CLIENT-FREE. The owner-gone arm demands
    # docker's own TERMINAL state, OR no attached client ACROSS `OWNER_GRACE_H`,
    # for BOTH owner sources — the fold's `0abaa51a5` (#10841 review P1), landed
    # concurrently with this file and covering the name-record path too. A bare
    # "answered False" was enough on the pre-fold arm, whose only act was
    # `docker rm -f`; on the merged tree it is not, and `None` reads as "first
    # sighting" — the lane-between-steps shape, deliberately KEPT. So this box
    # models the population the deployed reaper is actually armed for: a
    # container nothing has touched for longer than the grace.
    monkeypatch.setattr(
        tl, "_unused_gap",
        lambda name: tl.OWNER_GRACE_H + 1.0 if name == ORPHAN else None)
    monkeypatch.setattr(tl, "_touch_lease", lambda name: None)
    # The withheld proofs 2/3 stay withheld: this test is about the DEFAULT.
    monkeypatch.delenv(tl.LANE_GONE_REAP_ENV, raising=False)
    monkeypatch.delenv(tl.OWNER_INDETERMINATE_REAP_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "0")
    monkeypatch.setenv("TORTOISE_CONTAINER_REAPER_SHA256", "")
    return docker




def test_DRY_RUN_is_the_default_and_removes_NOTHING(box, capsys):
    """A timer's first run must be free.

    RED-FIRST ANCHOR: today there is no CLI at all, so this cannot pass — the
    sweep is reachable only from `start()`, which nobody schedules.
    """
    rc = cr.main(["reap"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert box.removals == [], (
        f"a dry run removed {box.removals}; the default must never act")
    assert ORPHAN in out.out, out.out
    assert "would remove" in out.out.lower(), out.out


def test_APPLY_removes_EXACTLY_the_orphan_and_never_the_live_lane(box, capsys):
    rc = cr.main(["reap", "--apply"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert box.removals == [ORPHAN], (
        f"expected exactly the orphan to be removed, got {box.removals}")
    assert LIVE not in box.removals
    assert IDLE not in box.removals, (
        "a container whose owning worktree exists must never be removed by the "
        "default sweep (#8180)")


def test_apply_is_IDEMPOTENT_so_a_15_minute_timer_cannot_churn(box, capsys):
    cr.main(["reap", "--apply"])
    first = list(box.removals)
    box.rows = "".join(
        line + "\n" for line in PS_ROWS.splitlines()
        if not line.startswith(ORPHAN))
    cr.main(["reap", "--apply"])
    assert box.removals == first, (
        f"the second pass removed {box.removals[len(first):]} — not idempotent")


def test_an_OLD_container_with_a_LIVE_owner_is_KEPT(box, capsys):
    """Age is a BACKSTOP, never the proof.

    The LIVE row is the OLDEST container in the fixture (30 h — past
    `ORPHAN_MAX_AGE_H`), and its lane holds a live process, so nothing about its
    age may remove it. The age is also asserted from the REPORT, so the fixture
    cannot silently stop being old.
    """
    cr.main(["reap", "--apply"])
    out = capsys.readouterr().out
    live_line = next(ln for ln in out.splitlines() if LIVE in ln)
    assert "age=1.2d" in live_line or "age=1.3d" in live_line, live_line
    assert LIVE not in box.removals, (
        "an age threshold removed a container whose lane is live")


def test_the_LIVENESS_proof_is_what_spares_the_live_lane(box, monkeypatch, capsys):
    """The one input that decides every deletion must have a discriminating test.

    With proofs 2/3 withheld the live container is spared by a *different*
    branch (its start time is unreadable, so the arm reports), so a broken
    liveness proof would go unnoticed. This test ARMS the lane-gone proof — the
    configuration in which `_holds` is load-bearing — and asserts that an idle,
    long-unused owner IS removed while a live one is NOT.

    Mutation (proved by the reviewer, #10806 P2-7): replace `_holds` with
    `lambda p, o: False` and the LIVE container is removed here, failing this
    test — which the old fixture could not detect.
    """
    monkeypatch.setenv(tl.LANE_GONE_REAP_ENV, "1")
    # The idle owner's container is past the grace, and the container reports no
    # client; the live owner has a process, which is the ONLY thing sparing it.
    box.rows = _rows()
    monkeypatch.setattr(tl, "_started_epoch",
                        lambda n: 1_700_000_000.0)          # long ago
    monkeypatch.setattr(tl, "_unused_gap", lambda n: 9999.0)
    cr.main(["reap", "--apply"])
    assert IDLE in box.removals, (
        "the armed lane-gone proof did not remove an idle, long-unused owner")
    assert LIVE not in box.removals, (
        "a container whose owning lane holds a live process was removed")


def test_a_sweep_that_cannot_DECIDE_is_exit_2_not_a_clean_sweep(monkeypatch, capsys):
    """The fail-closed path has no other test, and it is the dangerous one.

    `reap_orphans()` fail-closes by RETURNING `reap: skipped — …` instead of
    raising, so an actuator that only counts removals reports `ok, removed: 0`
    every 15 minutes on a box where `git worktree list` cannot run — a green
    light over the leak (#10806 review P1-1).
    """
    docker = _Docker()
    monkeypatch.setattr(tl, "_docker", docker)
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: None)   # unreadable
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: None)
    monkeypatch.setenv("TORTOISE_CONTAINER_REAPER_SHA256", "")
    rc = cr.main(["reap", "--apply", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2, out
    assert out["ok"] is False and out["decided"] is False, out
    assert any("skipped" in n for n in out["notes"]), out
    assert docker.removals == []


def test_an_UNDECIDABLE_orphan_is_exit_2_not_a_clean_sweep(box, monkeypatch,
                                                           capsys):
    """The fail-closed shape that is NOT a `reap: skipped` line.

    When the container's own client list cannot be read, the sweep keeps it and
    says "report only, fail-closed" — a container it COULD have removed and could
    not decide about. Counting that as decided made the only population the
    deployed unit is armed for a silent no-op that exits green every 15 minutes
    (#10813 review P1-2).
    """
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: None)
    rc = cr.main(["reap", "--apply", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2, out
    assert out["ok"] is False and out["decided"] is False, out
    # Either spelling of the SAME undecided shape: `container_reaper.undecided`
    # matches "unreadable" OR "fail-closed" on a `… is gone` line, so the
    # predicate is what is under test here, not one arm's wording — the pre-fold
    # probe said "(unreadable)", the merged ladder says "report only,
    # fail-closed" (#10841 review P1).
    assert any("unreadable" in n or "fail-closed" in n for n in out["notes"]), out
    assert box.removals == []


def test_an_UNEXAMINABLE_owner_is_exit_2_too(monkeypatch, capsys):
    """`EACCES` on the owning worktree is the third shape of the same class.

    `pathlib` suppresses ENOENT/ENOTDIR/ELOOP but NOT EACCES, and a worktree under
    another user's `CELL_HOME` is unexaminable for the timer's user — so "kept,
    could not be examined (fail-closed, report only)" is an undecided proof-1
    candidate, not a clean sweep (#10813 review). Driven from the sweep's OWN line
    so the predicate is what is under test, not pathlib's error taxonomy.
    """
    monkeypatch.setattr(tl, "_docker", _Docker())
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"aaaaaaaaaa": "/x/y"})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setenv("TORTOISE_CONTAINER_REAPER_SHA256", "")
    monkeypatch.setenv("TORTOISE_TEST_LANE_POLICY_SHA256", "")
    monkeypatch.setattr(tl, "reap_orphans", lambda: [
        f"reap: kept {ORPHAN} — its owning worktree /x/y could not be examined "
        f"(fail-closed, report only)"])
    rc = cr.main(["reap", "--apply", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2, out
    assert out["ok"] is False and out["decided"] is False, out
    assert any("could not be examined" in n for n in out["notes"]), out


def test_a_FAILED_removal_is_not_reported_as_a_clean_sweep(box, monkeypatch, capsys):
    """`docker rm` refusing must surface, not read as `removed: 0` (#10806 P1-2)."""
    def refusing(*args, **kwargs):
        if args[:1] == ("rm",):
            return _R(1, "", "container is zombie and can not be killed")
        return box(*args, **kwargs)
    monkeypatch.setattr(tl, "_docker", refusing)
    rc = cr.main(["reap", "--apply"])
    assert rc == 5, rc
    assert "PARTIAL" in capsys.readouterr().err


def test_a_DRY_RUN_that_cannot_prove_itself_is_NOT_green(box, monkeypatch, capsys):
    """The one guard whose whole job is refusing a green light.

    It was dead code in the first version of this change — `unproven` was folded
    into `skipped` while still empty, so a disagreement reported `ok: true, exit
    0` (#10813 review P1). Forcing the disagreement proves the guard is wired.
    """
    monkeypatch.setattr(cr, "_verdicts", lambda lines, dry_run: {})
    rc = cr.main(["reap", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2, out
    assert out["ok"] is False and out["decided"] is False, out


def test_every_line_carries_name_age_owner_and_bytes(box, capsys):
    cr.main(["reap"])
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ORPHAN in ln)
    assert "age=" in line, line
    assert "owner=" in line, line
    assert "bytes=" in line, line


def _stamp(seconds_ago: float) -> str:
    """Docker's own `ps` creation rendering, relative to now."""
    import time as _t
    return _t.strftime("%Y-%m-%d %H:%M:%S +0000 UTC",
                       _t.gmtime(_t.time() - seconds_ago))


class _Two(_Docker):
    """Two owner-gone orphans: one TERMINAL, one RUNNING.

    The population the deployed unit is armed for. The terminal one is removed
    without any probe (`busy = False`); the running one can only be removed if its
    `CLIENT LIST` answers, so a probe failure leaves exactly one removal and one
    undecidable candidate in ONE sweep.
    """

    TERMINAL = "fdb-lane-dddddddddd"
    RUNNING = "fdb-lane-eeeeeeeeee"

    def __init__(self):
        super().__init__()
        self.rows = (f"{self.TERMINAL}\texited\t{_stamp(6 * 3600)}\t\t\n"
                     f"{self.RUNNING}\trunning\t{_stamp(6 * 3600)}\t\t\n")
        self.size_rows = (f"{self.TERMINAL}\t1MB (virtual 2MB)\n"
                          f"{self.RUNNING}\t1MB (virtual 2MB)\n")


def _two_orphan_box(monkeypatch, docker):
    monkeypatch.setattr(tl, "_docker", docker)
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {
        _Two.TERMINAL[len(tl.NAME_PREFIX):]: "/gone/one",
        _Two.RUNNING[len(tl.NAME_PREFIX):]: "/gone/two",
    })
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: None)
    monkeypatch.setattr(tl, "_termination_report", lambda _n, **_k: "")
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-0123456789")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: None)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: None)
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "0")
    monkeypatch.setenv("TORTOISE_CONTAINER_REAPER_SHA256", "")


def test_an_INCOMPLETE_tick_that_ALSO_removed_one_never_says_NOTHING_was(
        monkeypatch, capsys):
    """The summary must not contradict its own body (#10813 review issue 1).

    `undecided` is a PER-CONTAINER list, so one sweep can remove a terminal orphan
    and be unable to decide about a running one. The verdict used to print
    "NOTHING was decided or removed" on exactly that sweep — a false
    operator-facing claim from a destructive tool, and the same
    summary-contradicts-its-body class this module was written to refuse (see the
    `acted` comment in `main()`). Exit 2 is still right: the tick is INCOMPLETE.
    """
    docker = _Two()
    _two_orphan_box(monkeypatch, docker)
    rc = cr.main(["reap", "--apply"])
    out = capsys.readouterr()
    assert rc == 2, (rc, out.out, out.err)
    assert docker.removals == [_Two.TERMINAL], docker.removals
    assert "1 removal(s) DID complete" in out.err, out.err
    assert "NOTHING was decided or removed" not in out.err, out.err

    # …and a sweep that removed NOTHING keeps the absolute claim, so the
    # conditional did not just delete the sentence.
    nothing = _Two()
    nothing.rows = f"{_Two.RUNNING}\trunning\t{_stamp(3600)}\t\t\n"
    nothing.size_rows = f"{_Two.RUNNING}\t1MB (virtual 2MB)\n"
    _two_orphan_box(monkeypatch, nothing)
    rc = cr.main(["reap", "--apply"])
    out = capsys.readouterr()
    assert rc == 2, (rc, out.out, out.err)
    assert nothing.removals == [], nothing.removals
    assert "NOTHING was removed" in out.err, out.err


def test_a_DRY_RUN_INCOMPLETE_tick_never_claims_a_removal_COMPLETED(
        monkeypatch, capsys):
    """#10813 review P2: the summary lied on the tool's DEFAULT path.

    `acted` is derived from `would remove` verdicts under a dry run, so the
    did-clause printed "1 removal(s) DID complete in this sweep" beside a body
    that said `would remove` — on a host where no `rm` was issued at all, in the
    mode `container_reaper.py` runs when invoked without `--apply` (the
    documented default, i.e. the first thing an operator sees). Exit 2 was right;
    the sentence was the summary-contradicts-its-body class this module exists to
    refuse, inverted into the safe mode.

    MUTATION: drop the `apply` argument from `_exit_for` and this reds with
    "DID complete" present in a dry run.
    """
    docker = _Two()
    _two_orphan_box(monkeypatch, docker)
    rc = cr.main(["reap"])                      # DRY RUN — no --apply
    out = capsys.readouterr()
    assert rc == 2, (rc, out.out, out.err)
    assert docker.removals == [], docker.removals
    assert "1 removal(s) WOULD complete" in out.err, out.err
    assert "DID complete" not in out.err, out.err
    assert "DRY RUN" in out.err, out.err


def test_a_PAUSED_orphan_is_REPORTED_never_probed_and_never_reds_the_timer(
        monkeypatch, capsys):
    """`docker exec` cannot reach a container docker is not RUNNING.

    MEASURED (docker 27, exit 1 both): `created` -> "container … is not running";
    `paused` -> "Container … is paused, unpause the container before exec". The
    proof-1 probe therefore came back `None` for those states, which (a) kept the
    container from EVERY arm — it could never be reaped — and (b) matched the
    (unreadable) shape the actuator classifies as `undecided`, i.e. a 15-minute
    timer red FOREVER over a question no retry can answer (#10813 review P2).

    It must also spend no `exec` on it: 60 s per wedged probe is load a loaded
    cell does not have.
    """
    paused = "fdb-lane-ffffffffff"
    docker = _Two()
    docker.rows = f"{paused}\tpaused\t{_stamp(3600)}\t\t\n"
    docker.size_rows = f"{paused}\t1MB (virtual 2MB)\n"
    _two_orphan_box(monkeypatch, docker)
    monkeypatch.setattr(tl, "_worktree_slugs",
                        lambda: {paused[len(tl.NAME_PREFIX):]: "/gone/wt"})

    def _never(_n):        # pragma: no cover - asserted not to run
        raise AssertionError("a container docker is not RUNNING was probed")

    monkeypatch.setattr(tl, "_container_in_use", _never)
    rc = cr.main(["reap", "--apply"])
    out = capsys.readouterr()
    # A permanent "cannot be asked" is a DECISION to keep, not an undecided tick.
    assert rc == 0, (rc, out.out, out.err)
    assert docker.removals == []
    assert not [c for c in docker.calls if c[:1] == ("exec",)], docker.calls
    lines = "\n".join([out.out, out.err])
    assert paused in lines
    assert "cannot be asked about its clients" in lines, lines
    assert "docker rm -f" in lines, lines          # the remedy is named
    # ⛔ and it must not wear the shape the actuator reads as INCOMPLETE.
    assert "unreadable" not in lines, lines
    assert "fail-closed" not in lines, lines


def test_a_LABELLED_orphan_in_a_state_docker_cannot_exec_is_NEVER_a_red_tick(
        monkeypatch, capsys, tmp_path):
    """#10813 review P2: the stalled-timer case, in the population #10712 exists for.

    The name-derived arm classifies `_LIVE_ISH_STATES` as a DECISION to keep —
    `docker exec` reaches a container only while docker is RUNNING it (MEASURED
    docker 27: `created` → "is not running", `paused` → "is paused, unpause the
    container before exec", both exit 1). The LABEL-derived arm did not: it asked
    anyway, got `None`, and emitted the `fail-closed` line the ACTUATOR reads as
    an `undecided` tick — so the deployed unit returned exit 2 on EVERY tick,
    forever, over a question no retry can answer. A `paused` container cannot be
    removed by any arm, so that is not a transient INCOMPLETE: it is a permanent
    false alarm beside a container that stays put, and it destroys the one
    signal the stale-copy pin depends on (a FAILED unit).

    The label arm is the one that matters here: `labelled_owner` is set exactly
    when `_worktree_slugs()` no longer names the worktree, i.e. the STRANDED
    population — `git worktree remove` keeps the label and destroys the name's
    meaning (#10712).

    MUTATION: drop the `_LIVE_ISH_STATES` branch from the labelled arm (return to
    probing unconditionally) and this reds with rc == 2 and `fail-closed` in the
    text.
    """
    gone = tmp_path / "torn-down-worktree"
    assert not gone.exists()
    name = tl.container_name(tl.slug_for(gone))
    stamp = _stamp(3 * 3600)

    def fake(*args, **kwargs):
        if args[:2] == ("ps", "-a"):
            if "-s" in args:
                return _R(0, f"{name}\t1MB (virtual 2MB)\n", "")
            if args[-1] == _PS_HEADER:
                # The actuator's own census projection (5 columns).
                return _R(0, f"{name}\tpaused\t{stamp}\t\t{gone}\n", "")
            # `reap_orphans()`'s projection: name, state, LABEL_OWNER, LABEL_SLUG.
            # A corroborated pair (`slug_for(owner) == slug`) — that is the only
            # shape `_label_args()` writes, so it is the only one that can reach
            # the labelled arm.
            return _R(0, f"{name}\tpaused\t{gone}\t{tl.slug_for(gone)}\n", "")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    # The teardown: git no longer names this worktree, so the NAME record is
    # silent and only the stamped label can carry ownership.
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-0123456789")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: None)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: None)
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "0")
    monkeypatch.setenv("TORTOISE_CONTAINER_REAPER_SHA256", "")
    monkeypatch.setenv("TORTOISE_TEST_LANE_POLICY_SHA256", "")

    probed: list[str] = []
    monkeypatch.setattr(tl, "_container_in_use",
                        lambda n: probed.append(n) or None)

    rc = cr.main(["reap", "--apply"])
    out = capsys.readouterr()
    lines = "\n".join([out.out, out.err])
    # The timer must be GREEN about a state it can never change.
    assert rc == 0, (rc, out.out, out.err)
    # …while the container is KEPT (kept is the decision, not a deletion) and the
    # operator is given the one command that releases it.
    assert name in lines, lines
    assert "cannot be asked about its clients" in lines, lines
    assert "docker rm -f" in lines, lines
    # ⛔ and it must not wear the shape the actuator reads as INCOMPLETE, nor
    # spend a 60 s `exec` on a container docker has told us is paused.
    assert "fail-closed" not in lines, lines
    assert "unreadable" not in lines, lines
    assert probed == [], f"a container docker is not RUNNING was probed: {probed}"


def test_a_PER_CONTAINER_undecided_tick_does_not_claim_the_sweep_NEVER_RAN(
        box, monkeypatch, capsys):
    """`skipped` carries two different facts; the census line must not merge them.

    A whole-sweep refusal means NO decision was taken about any row — that is what
    "NOT DECIDED (the sweep did not run)" is for. A per-container `undecided` keep
    means the sweep DID run: every other row has its own verdict, and a row with
    none was simply not a candidate (#10813 review issue 1).
    """
    monkeypatch.setattr(tl, "reap_orphans", lambda: [
        f"reap: kept {ORPHAN} — its owning worktree /x/y is gone, but the "
        f"container reports nothing about its clients (unreadable); not "
        f"removing a database that may be in use"])
    rc = cr.main(["reap", "--apply"])
    out = capsys.readouterr()
    assert rc == 2, (rc, out.out, out.err)
    assert "NOT DECIDED (the sweep did not run)" not in out.out, out.out
    assert "kept (not a candidate)" in out.out, out.out


def test_the_dry_run_REFUSES_a_writing_verb_the_sweep_might_issue(monkeypatch,
                                                                 capsys):
    """The interception is a deny-list; its refusals had no test at all.

    A reviewer deleted BOTH `raise SystemExit` arms and the suite stayed green —
    so a dry run that silently executed `docker kill` or an arbitrary
    `docker exec` would have shipped (#10813 review P2).
    """
    monkeypatch.setenv("TORTOISE_CONTAINER_REAPER_SHA256", "")
    monkeypatch.setenv("TORTOISE_TEST_LANE_POLICY_SHA256", "")

    def sweep_issues(*verbs):
        def fake(*args, **kwargs):
            if args[:1] == ("ps",):
                return _R(0, PS_ROWS if args[-1] == _PS_HEADER else "x\tRunning\n")
            if args[:1] in verbs:
                raise AssertionError(f"the dry run executed {args}")
            return _R(0, "", "")
        return fake

    for verb in (("kill",), ("volume",), ("system",), ("image",), ("stats",)):
        monkeypatch.setattr(tl, "_docker", sweep_issues(*[verb]))
        monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
        monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
        monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
        # The sweep must actually reach the verb for this to prove anything:
        # drive the interception directly through a stub sweep.
        monkeypatch.setattr(tl, "reap_orphans",
                            lambda v=verb: (tl._docker(*v, "fdb-lane-x"), [])[1])
        rc = cr.main(["reap"])
        assert rc in (2, 3), (verb, rc)


def test_an_unreadable_docker_is_exit_2_and_removes_NOTHING(monkeypatch, capsys):
    docker = _Docker(ps_rc=1)
    monkeypatch.setattr(tl, "_docker", docker)
    rc = cr.main(["reap", "--apply"])
    assert rc == 2, "a failed `docker ps` is not 'no containers'"
    assert docker.removals == []


def test_a_STALE_POLICY_copy_refuses_to_run(monkeypatch, capsys):
    """The decision half is `tools/test_lane.py`; a drifted copy must refuse too."""
    monkeypatch.setenv(cr.POLICY_ENV, "0" * 64)
    monkeypatch.setenv(cr.STALE_ENV, "")
    rc = cr.main(["reap"])
    r = capsys.readouterr()
    assert rc == 4, r.err
    assert "DECISION half" in r.err, r.err


def test_a_STALE_deployed_copy_refuses_to_run(monkeypatch, capsys, tmp_path):
    """A timer must never run a copy that is not the one in the repo.

    The disk-reaper precedent pins its delegates for exactly this reason; a unit
    whose `ExecStart` still points at a moved/deleted worktree would otherwise
    sweep with old policy and look healthy.
    """
    monkeypatch.setenv("TORTOISE_CONTAINER_REAPER_SHA256", "0" * 64)
    rc = cr.main(["reap"])
    assert rc == 4, capsys.readouterr().err
    assert "stale" in capsys.readouterr().err.lower()


def test_the_age_parser_accepts_the_shape_DOCKER_actually_prints():
    """Docker prints `2026-11-03 22:28:01 +0000 UTC`, not ISO-8601."""
    epoch = cr._ps_created_epoch("2026-11-03 22:28:01 +0000 UTC")
    assert epoch is not None and epoch > 1_700_000_000
    assert cr._ps_created_epoch("") is None
    assert cr._ps_created_epoch("not a date") is None


def test_the_size_parser_reads_the_WRITABLE_layer_not_the_virtual_size():
    """"5.78MB (virtual 252MB)": the shared image is not freed, 5.78MB is."""
    got = cr._ps_size_bytes("5.78MB (virtual 252MB)")
    assert got is not None
    assert 5_000_000 < got < 6_500_000, got
    assert cr._ps_size_bytes("") is None


# ── the SCHEDULER, and the deployed-vs-source check it must carry ───────────
# The brief's requirement is not "a unit exists" but "a stale copy cannot be what
# runs". These pin the WIRING of that check rather than its prose: the rendered
# unit's pin is literally the sha256 of the reaper, and the reaper refuses a
# mismatch (proved above by `test_a_STALE_deployed_copy_refuses_to_run`).

INSTALLER = ROOT / "tools" / "install-container-reaper-timer.sh"


def _render(env_extra: dict | None = None) -> subprocess.CompletedProcess:
    # ROOT is a lane worktree in a lane run: the pin refusal is correct there, and
    # is proved by its own test below — so it is explicitly waived here.
    # `PYTHON_BIN` pins the interpreter the installer validates: without it the
    # installer prefers `/usr/bin/python3`, which is < 3.12 on Debian 12 / Ubuntu
    # 22.04 and would make this test red for a reason unrelated to the property
    # under test (#10813 review). The repo's own 3.12 is what the unit runs in CI.
    env = {**os.environ, "REAPER_REPO": str(ROOT),
           "REAPER_ALLOW_WORKTREE_PIN": "1", "PYTHON_BIN": sys.executable,
           **(env_extra or {})}
    return subprocess.run(["bash", str(INSTALLER), "--dry-run"], env=env,
                          capture_output=True, text=True, timeout=120)


def test_the_rendered_unit_pins_the_sha256_of_the_reaper_it_installs():
    """Deployed-vs-source, wired — not merely documented.

    RED-FIRST shape: without the pin, the unit would run whatever copy happened to
    sit at that path, and the reaper's own staleness check would stay inert
    (`TORTOISE_CONTAINER_REAPER_SHA256` unset ⇒ no check).
    """
    r = _render()
    assert r.returncode == 0, r.stderr
    pin = hashlib.sha256((ROOT / "tools" / "container_reaper.py").read_bytes()).hexdigest()
    policy = hashlib.sha256((ROOT / "tools" / "test_lane.py").read_bytes()).hexdigest()
    assert f"Environment=TORTOISE_CONTAINER_REAPER_SHA256={pin}" in r.stdout, r.stdout
    # The DECISION half is pinned too — pinning only the enumerator would make
    # editing the file that decides what gets deleted invisible (#10806 P1-3).
    assert f"Environment=TORTOISE_TEST_LANE_POLICY_SHA256={policy}" in r.stdout, r.stdout
    # Quoted, so a checkout path containing a space does not split argv.
    assert "ExecStart=" in r.stdout, r.stdout
    assert '" --apply --json' in r.stdout, r.stdout
    assert str(ROOT / "tools" / "container_reaper.py") in r.stdout, r.stdout
    # The timer must name the service, or it schedules nothing.
    assert "Unit=tortoise-container-reaper.service" in r.stdout, r.stdout
    # The service bound must sit ABOVE the work, not at the alarm: every probe the
    # sweep issues is bounded by DOCKER_TIMEOUT (60 s), and systemd cannot tell a
    # mid-pass kill apart from a stale pin (both read FAILED) — so 300 s was a red
    # tick waiting for a loaded cell, not a defect signal (#10813 review).
    bounds = [int(ln.split("=", 1)[1]) for ln in r.stdout.splitlines()
              if ln.startswith("TimeoutStartSec=")]
    assert bounds and bounds[0] >= 600, bounds
    # ⛔ THE SCHEDULING DIRECTIVES THEMSELVES (#10813 review P2-4). Nothing above
    # pins them, so a replace of `OnCalendar=` with `OnUnitActiveSec=` — the exact
    # mistake the unit's own comment warns about, and one that silently loses "a
    # rebooted cell catches up" — kept every test in this file green.
    assert "OnCalendar=*:0/15" in r.stdout, r.stdout
    assert "Persistent=true" in r.stdout, r.stdout
    # …and the arming guard: a manager-level `DefaultEnvironment=` must not be
    # able to arm a deletion proof behind the installer's back.
    # The box-wide switch is cleared too, or an ambient `TORTOISE_TEST_LANE_REAP`
    # would silently disarm (or re-arm) a destructive unit that never reads it —
    # making the documented "set this and nothing is reaped" false for the clock
    # (#10813 round-3 review P2-2).
    assert ("UnsetEnvironment=TORTOISE_TEST_LANE_REAP "
            "TORTOISE_TEST_LANE_REAP_LANE_GONE "
            "TORTOISE_TEST_LANE_REAP_OWNER_INDETERMINATE "
            "TORTOISE_TEST_LANE_REAP_STOPPED") in r.stdout, r.stdout


def test_the_rendered_units_COMMENTARY_keeps_the_directive_names_it_names():
    """The unit heredoc is UNQUOTED and must stay so — it interpolates `$SHA`,
    `$POLICY_SHA`, `$REPO`, `$PY` and `$SOURCE`. The cost is that a bare backtick
    in a COMMENT is command substitution, not markup.

    Measured before the fix, on the rendered unit: three directive names collapsed
    to an empty pair — `# \\`DefaultEnvironment=\\` would arm a proof…` rendered as
    `#  would arm a proof…` — so the deployed comment named not one of the three
    directives it exists to warn about. Directives themselves were intact; the
    loss was exactly the names a reader needs (#10697/#10799 review, P2-1).
    """
    r = _render()
    assert r.returncode == 0, r.stderr
    for name in ("`DefaultEnvironment=`", "`WorkingDirectory=`", "`ExecStart=`"):
        assert name in r.stdout, (name, r.stdout)
    # And no OTHER comment lost its content the same way.
    assert "``" not in r.stdout, r.stdout


def test_the_installer_REFUSES_to_pin_a_lane_worktree_without_the_override(tmp_path):
    """A worktree is deleted when its lane lands; a unit pinned to one fails forever.

    The refusal is the safe default; the override exists only so a lane can prove
    the mechanism end-to-end before its change merges.
    """
    fake = tmp_path / ".worktrees" / "lane"
    (fake / "tools").mkdir(parents=True)
    (fake / "tools" / "container_reaper.py").write_text("# placeholder\n")
    env = {**os.environ, "REAPER_REPO": str(fake)}
    env.pop("REAPER_ALLOW_WORKTREE_PIN", None)
    r = subprocess.run(["bash", str(INSTALLER), "--dry-run"], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "refusing to pin" in r.stderr.lower()


def test_the_PLATFORM_gate_is_on_the_VERBS_that_touch_the_box_not_on_the_file(tmp_path):
    """macOS has no systemd: `--install`/`--uninstall`/`--status` must refuse there.

    RED-FIRST, and the reason this test exists: at module scope the same gate also
    refused `--dry-run`, which renders text and installs NOTHING. That is what put
    a `sys.platform != "linux"` skip on the three rendered-unit tests above — a
    platform gate that hides tests from every CI job, the exact shape
    `tests/test_markers.py::PLATFORM_GATED_TESTS` exists to red (#4162). The gate
    belongs to the verbs that touch the box, the same way `--help` was already
    answered before it.

    `--status` is the verb driven here instead of `--install` on purpose: it only
    READS systemd, so a regression that drops `require_linux()` from it queries the
    box rather than mutating it. Both sides are exercised on one Linux runner
    through the installer's own `PLATFORM_SEAM` (the shape
    `tools/install-ci-runner-x64.sh` already uses as `OS_NAME_SEAM`).
    """
    # A path that is NOT a lane worktree, so the pin refusal (exit 3, module scope)
    # does not answer first and mask the platform verdict.
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    env = {**os.environ, "PLATFORM_SEAM": "Darwin", "REAPER_REPO": str(repo),
           "PYTHON_BIN": sys.executable}
    env.pop("REAPER_ALLOW_WORKTREE_PIN", None)
    r = subprocess.run(["bash", str(INSTALLER), "--status"], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "Darwin has no systemd" in r.stderr, r.stderr
    # …and the OTHER side: the render still works off Linux, so a dev box can
    # inspect the unit before installing it on a cell.
    rendered = _render({"PLATFORM_SEAM": "Darwin"})
    assert rendered.returncode == 0, (rendered.returncode, rendered.stderr)
    assert "ExecStart=" in rendered.stdout, rendered.stdout


def test_the_pin_survives_a_SHA256SUM_that_cannot_digest(tmp_path):
    """macOS ships `shasum` (perl), never coreutils' `sha256sum`.

    Both spellings print `<hex>  <path>`, so `cut -d' ' -f1` reads either; what
    must not happen is an EMPTY pin, which silently disables the reaper's
    staleness check (`TORTOISE_CONTAINER_REAPER_SHA256` unset ⇒ no check). The
    fallback is proved here by shadowing the first spelling with a `sha256sum`
    that is present but answers nothing — the shape a busybox/PATH hazard
    produces — and a `shasum` stub that computes the real digest, so what is
    under test is the SCRIPT'S resolution, not the stub's format.
    """
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "sha256sum").write_text("#!/bin/sh\nexit 1\n")
    (shim / "sha256sum").chmod(0o755)
    impl = tmp_path / "shasum_impl.py"
    impl.write_text(
        "import hashlib, sys\n"
        "p = sys.argv[-1]\n"
        'print(hashlib.sha256(open(p, "rb").read()).hexdigest() + "  " + p)\n'
    )
    (shim / "shasum").write_text(
        f"#!/bin/sh\nexec {sys.executable!r} {str(impl)!r} \"$@\"\n")
    (shim / "shasum").chmod(0o755)
    r = _render({"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"})
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    pin = hashlib.sha256(
        (ROOT / "tools" / "container_reaper.py").read_bytes()).hexdigest()
    assert f"Environment=TORTOISE_CONTAINER_REAPER_SHA256={pin}" in r.stdout, r.stdout
