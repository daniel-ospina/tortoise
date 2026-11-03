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
    monkeypatch.setattr(tl, "_unused_gap", lambda name: None)
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


def test_every_line_carries_name_age_owner_and_bytes(box, capsys):
    cr.main(["reap"])
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ORPHAN in ln)
    assert "age=" in line, line
    assert "owner=" in line, line
    assert "bytes=" in line, line


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
    env = {**os.environ, "REAPER_REPO": str(ROOT),
           "REAPER_ALLOW_WORKTREE_PIN": "1", **(env_extra or {})}
    return subprocess.run(["bash", str(INSTALLER), "--dry-run"], env=env,
                          capture_output=True, text=True, timeout=120)


@pytest.mark.skipif(sys.platform != "linux", reason="cells are Linux; macOS has no systemd")
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


@pytest.mark.skipif(sys.platform != "linux", reason="cells are Linux; macOS has no systemd")
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
