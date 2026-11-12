"""#5084: `tools/test_lane.py` — the private per-lane FalkorDB.

These are pure unit tests: nothing here talks to Docker (every docker-touching
function is monkeypatched), because the properties that must hold are the
tool's CONTRACT, not Docker's behaviour:

  * the container it acts on is ALWAYS derived from the worktree — there is no
    argument, flag, or function parameter that can name another lane's
    container (the whole point: a wrong `down` in the wrong lane would take out
    another lane's private test DB, and the shared dev/test instances must not
    be removable by it either), and
  * ``uri`` prints an ``eval``-able export line on stdout, with diagnostics on
    stderr only — anything that leaked onto stdout would corrupt the ``eval``-ed
    value, which silently breaks the lane's target rather than aborting it.

Where a function's real body is the safety property (``container_state``,
``_graph_count``, ``_container_publishes``, ``repo_root``), it is tested
DIRECTLY. Stubbing such a function to test its caller pins the caller and
leaves the property itself free to regress.
"""
from __future__ import annotations

import inspect
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import _live_utils  # noqa: E402
from tools import test_lane as tl  # noqa: E402

#: The REAL lease writer, captured before the autouse fixture stubs it. Tests that
#: exercise the lease mechanism itself restore this.
_REAL_TOUCH_LEASE = tl._touch_lease


class _R:
    """Just enough of a CompletedProcess for the docker seam."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


LANE_FIXTURE_ROOT = Path("/tmp/fdb-lane-fixture-worktree")
#: The root that fixture name is DERIVED FROM, not a literal pinned beside it: a
#: `start()`-created container carries `container_name(slug_for(root))` as its
#: NAME and the same pair as its ownership LABEL, and the removal guard compares
#: the two (see `_assert_removal_allowed`). A hand-written name would therefore
#: be a state the tool cannot produce — it refused to clean up after itself, and
#: the suite said nothing because the guard used to re-derive the name instead of
#: taking the caller's.
LANE_FIXTURE_NAME = tl.container_name(tl.slug_for(LANE_FIXTURE_ROOT))


@pytest.fixture
def lane(monkeypatch):
    """Pin the lane's container name so tests do not depend on the CWD."""
    monkeypatch.setattr(tl, "_lane_identity",
                        lambda: (LANE_FIXTURE_NAME, LANE_FIXTURE_ROOT))
    return LANE_FIXTURE_NAME


@pytest.fixture(autouse=True)
def _reap_state_dir_is_isolated(tmp_path, monkeypatch):
    """The lease/marker registry is REAL box state, so no test may touch it.

    `REAP_STATE_DIR` defaults to `~/.pi/agent/state/lane-reap/`, and the hook
    tests drive the REAL `_peer_run_is_live` / `_touch_lease` (a live peer marker
    is exactly what one of them asserts). Left pointing at the home directory
    they wrote markers and leases into the developer's own registry and read
    whatever a LIVE peer lane had left there — a test whose result depended on the
    box, on a file registered to a docker-free CI surface (#10813 review).
    """
    monkeypatch.setattr(tl, "REAP_STATE_DIR", tmp_path / "lane-reap")


@pytest.fixture(autouse=True)
def _host_probe_and_sweep_are_stubbed(monkeypatch):
    """#8180: `_ping_over_host` is a REAL TCP connect, and `start()` now sweeps
    orphans with real git/docker calls. Unit tests stub `_docker` only, so
    without this every `start()` would look wedged and the sweep would touch the
    real box. Both are stubbed here and each has its own explicit test below —
    the gate itself is not relaxed."""
    monkeypatch.setattr(tl, "_ping_over_host", lambda port, timeout: True)
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "0")
    # The withheld proof-2 reap (`STOPPED_REAP_ENV`) is OFF unless an operator
    # asks for it, and a test must not inherit that choice from the shell it was
    # launched in — a lane that exported it would otherwise green the default-
    # keeps test and red the opt-in one. Spelled literally so this fixture also
    # works against the code that predates the constant.
    monkeypatch.delenv("TORTOISE_TEST_LANE_REAP_STOPPED", raising=False)
    # #8124: the lane's image comes from `TORTOISE_TEST_LANE_IMAGE` read at
    # IMPORT time, and a lane that exports it (to compare engines) must not be
    # able to move these tests — `start()` now refuses a floating tag, so an
    # inherited `...:latest` would red the whole file for an environment
    # reason. Pin the module attribute too, since deleting the env var does not
    # undo an import that already happened.
    monkeypatch.delenv("TORTOISE_TEST_LANE_IMAGE", raising=False)
    monkeypatch.setattr(tl, "IMAGE", tl.LANE_IMAGE_PIN)


# ── #8180: the three defects, as regressions ─────────────────────────────

def test_start_refuses_a_container_that_answers_INSIDE_but_not_on_the_host_port(
        lane, monkeypatch):
    """Defect 2 in its MEASURED shape: the container is Up and `docker exec ...
    PING` answers PONG from inside, while the published host port is dead. That
    container used to be handed to a lane as usable; the host path is the one a
    lane actually uses, so it is the one readiness must mean."""
    # #8124: the reuse path now verifies the container's CREATED image
    # (`_created_image`) before returning it; pinned here so the test
    # exercises its own property rather than the engine check.
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "_ping_over_host", lambda port, timeout: False)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "PONG\n", ""))
    monkeypatch.setattr(tl, "READY_TIMEOUT", 1)
    with pytest.raises(SystemExit) as e:
        tl.start()
    msg = str(e.value)
    assert "published host port" in msg and "16390" in msg, msg


def test_stop_escalates_a_zombie_and_reports_it_precisely(lane, monkeypatch):
    """Defect 1 in its measured shape (#2653): `rm -f` reports that the
    container's PID 1 is a zombie. Removal must escalate, and when even that
    fails the report must say WHY — the remedy is the runtime, not a retry, and a
    generic "failed to remove" hides that."""
    real = ("could not kill container: container e93b6466ba18 PID 11035 is "
            "zombie and can not be killed. Use the `--init` option")
    calls: list = []

    def fake(*a, **k):
        calls.append(a)
        if a[0] == "inspect":
            return _R(0, "running\n", "")
        if a[0] == "rm":
            return _R(1, "", f"failed to remove {lane}: {real}")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    with pytest.raises(SystemExit) as e:
        tl.stop()
    msg = str(e.value)
    assert "zombie" in msg and "--init" in msg, msg
    assert "runtime restarted" in msg, msg
    assert [c[0] for c in calls][:3] == ["inspect", "rm", "kill"], calls


def test_reaper_reaps_an_unowned_container_and_keeps_a_live_owner(
        tmp_path, monkeypatch):
    """Task 1, where the NEGATIVE case is the one that matters: a container whose
    owner is live must never be reaped, however old it is, and an unreadable
    ownership record must remove nothing at all (fail-closed). The age is stubbed
    ancient precisely to prove that ownership, not a clock, decides."""
    dead, alive = tmp_path / "dead-worktree", tmp_path / "alive-worktree"
    alive.mkdir()
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(
        0, "fdb-lane-dead\texited\nfdb-lane-alive\texited\nfdb-lane-mine\texited\n", "")
        if a[0] == "ps"
        else _R(0, "", ""))
    monkeypatch.setattr(tl, "_worktree_slugs",
                        lambda: {"dead": str(dead), "alive": str(alive)})
    # LIVENESS is a live PROCESS in the worktree, not the directory's existence
    # (the measured conflation: 68 containers outlived their lanes by days).
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: {str(alive)})
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: 0.0)
    # The client probe is a SEPARATE gate (#10806) and has its own test below;
    # stating it here keeps this test about ownership.
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    out = tl.reap_orphans()
    joined = "\n".join(out)
    assert "removed fdb-lane-dead" in joined, out
    assert "kept fdb-lane-alive" in joined, out
    assert "kept fdb-lane-mine" in joined, out
    assert "removed fdb-lane-alive" not in joined, out

    # Fail-closed: no ownership record -> no docker call at all, nothing removed.
    calls: list = []
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: None)
    monkeypatch.setattr(tl, "_docker",
                        lambda *a, **k: (calls.append(a), _R(0, "", ""))[1])
    out = tl.reap_orphans()
    assert calls == [], calls
    assert "NOTHING was removed" in out[0], out


def test_the_container_is_created_with_init_so_pid1_reaps_orphans(lane, monkeypatch):
    """Task 2 pinned at the ARGV. An error text that merely NAMES `--init` is not
    a fix: without this, dropping the flag from `docker run` reintroduces the
    zombie defect and the whole suite still passes."""
    seen: list = []

    def fake(*a, **k):
        seen.append(a)
        if a[0] == "inspect":
            return _R(0, "absent\n", "")
        if a[0] == "run":
            return _R(0, "deadbeef\n", "")
        if a[0] == "port":
            return _R(0, "0.0.0.0:16390\n", "")
        return _R(0, "PONG\n", "")

    monkeypatch.setattr(tl, "_docker", fake)
    tl.start()
    run_args = next(c for c in seen if c[0] == "run")
    assert "--init" in run_args, run_args


def test_a_failed_docker_run_is_cleaned_up_by_the_id_it_printed(lane, monkeypatch):
    """Task 3's documented `Created`-state leak: `docker run -d` can CREATE the
    container and then fail to start it. The ID it printed addresses exactly that
    container, so it is what the cleanup must target."""
    seen: list = []

    def fake(*a, **k):
        seen.append(a)
        if a[0] == "inspect":
            return _R(0, "absent\n", "")
        if a[0] == "run":
            return _R(1, "deadbeefcafe\n", "Error response from daemon: cannot start")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    with pytest.raises(SystemExit) as e:
        tl.start()
    assert "removed the container it created" in str(e.value), str(e.value)
    assert [c[2] for c in seen if c[0] == "rm"] == ["deadbeefcafe"], seen


def test_the_reaper_never_removes_a_container_no_worktree_here_claims(monkeypatch):
    """The central report-only rule, and the DEFAULT. An unowned slug may belong
    to a DIFFERENT clone of this repo, whose live lane this tool cannot see — so
    nothing about that container authorizes a removal, including its age (stubbed
    ancient here precisely to prove a clock is not the trigger).

    #10693 review F2: this docstring and `test_ORPHAN_MAX_AGE_H_gates_the_unowned_arm`
    used to contradict each other — that test removed the container on age alone.
    The ruling's letter is the default again: age+no-client only makes a container
    a CANDIDATE, and the removal additionally needs an explicit operator opt-in
    (`OWNER_INDETERMINATE_REAP_ENV`), which is the armed path that test exercises."""
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-abcdef0123\trunning\n", "")
        if a[0] == "rm":
            removed.append(a)
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    # Both arms below are decided by the sweep's /proc scan BEFORE the client
    # probe, so leaving it real hands this test's verdict to the host: on a box
    # without /proc `_live_lane_paths()` returns None, the sweep reports
    # "ownership is NOT determined" and `continue`s, and the "active client"
    # arm this test exists to pin is never reached (measured: red on macOS,
    # green on the Linux runner — the #5049 "verdicts from conditions, not
    # ambient machine state" defect, and the same fail-closed branch that made
    # this test red on every macOS checkout with `assert 'active client' in …`).
    # Pin the scan to a readable-and-empty answer so each arm asserts the rule
    # it names on every host.
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    # YOUNG: the backstop is a bound, not a hair trigger. An unowned container
    # inside ORPHAN_MAX_AGE_H is REPORTED and removed by nothing.
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time())
    out = tl.reap_orphans()
    assert removed == [], removed
    assert "report only" in out[0], out

    # OLD but IN USE: the container's own CLIENT LIST outranks the clock. A
    # cross-clone lane the /proc scan cannot name still spares its container,
    # because the container itself reports the live client.
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: 0.0)
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: True)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "active client" in "\n".join(out), out


# ── The port-exhaustion fix: a TERMINAL container is provably out of service ──

def test_reaper_KEEPS_a_TERMINAL_container_of_a_LIVE_owner_by_DEFAULT(
        tmp_path, monkeypatch):
    """#8180's recorded owner ruling, pinned at the behaviour that crossed it.

    The rule is: a `fdb-lane-*` container MAY be reaped **when the worktree that
    owns it is gone**, and the scope is "reap only what is *yours*". A container
    in a TERMINAL state whose worktree still EXISTS belongs to a lane that is
    still registered, so removing it is a peer's deletion — and gating that on
    "the owner is determined" narrows nothing: measured 2026-10-20, 565 of 567
    registered worktrees still exist on this host, so nearly every lane
    container passes the gate.

    This is the MUTATION TEST for the boundary: restore the arm (reap a terminal
    container of a live owner) and the `removed == []` assertion fails. What the
    default owes instead is the REPORT — this container holds a port out of
    ``PORT_RANGE`` and no other instrument shows it, so the line must name the
    owner worktree, the state and the one command that releases it from its OWN
    worktree.

    It must NOT spend a `docker inspect` doing it: this runs for every stopped
    peer container on EVERY `up`/`uri`, nothing is being destroyed so there is
    no evidence to preserve, and the read is ~53 ms each (67 of them, the
    measured population, is ~3.5 s added to every lane start). The
    no-inspect assertion below is the mutation test for that."""
    owner = tmp_path / "worktree-of-a-long-dead-lane"
    owner.mkdir()
    removed: list = []

    inspects: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-deadlane\texited\n", "")
        if a[0] == "inspect":
            inspects.append(a)
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"deadlane": str(owner)})
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    line = out[0]
    assert "kept fdb-lane-deadlane" in line, out
    assert str(owner) in line and "exited" in line, out
    assert "test_lane.py down" in line, out      # the owner's own remedy
    assert inspects == [], inspects              # nothing found, nothing spent


def test_reaper_reaps_a_TERMINAL_container_of_a_live_owner_only_on_OPT_IN(
        tmp_path, monkeypatch):
    """The measured port relief is PRESERVED, but only as an operator's explicit
    choice (`TORTOISE_TEST_LANE_REAP_STOPPED=1`) while #8180's reopen is pending:
    the shipped default may not reverse a recorded owner ruling, and a lane
    cannot obtain that ruling for itself. The line says which opt-in produced the
    deletion, so a later reader can tell an authorised reap from a default one.

    It is RED on the pre-fix code, but for the honest reason — that code has no
    opt-in to set — so the teeth here come from the POST-fix behaviour: make the
    opt-in a no-op and this fails, and make the reap unconditional (drop the
    `if not _stopped_reap_enabled()` gate) and
    `..._KEEPS_a_TERMINAL_container_of_a_LIVE_owner_by_DEFAULT` fails instead."""
    owner = tmp_path / "worktree-of-a-long-dead-lane"
    owner.mkdir()
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-deadlane\texited\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"deadlane": str(owner)})
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    monkeypatch.setenv(tl.STOPPED_REAP_ENV, "1")
    out = tl.reap_orphans()
    assert removed == [("-f", "fdb-lane-deadlane")], (removed, out)
    assert "removed fdb-lane-deadlane" in out[0], out
    assert tl.STOPPED_REAP_ENV in out[0], out


def test_reaper_treats_only_the_exact_opt_in_value_as_consent(
        tmp_path, monkeypatch):
    """A deletion that crosses a recorded ownership boundary must not be
    triggered by an inherited or nearly-right value: `true`, `yes`, `2` and ""
    are not the operator asking for it. Mutation: `strtobool`-style leniency in
    `_stopped_reap_enabled` and this fails."""
    owner = tmp_path / "live-worktree"
    owner.mkdir()
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-deadlane\texited\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"deadlane": str(owner)})
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    for value in ("true", "yes", "2", "1 ", ""):
        removed.clear()
        monkeypatch.setenv(tl.STOPPED_REAP_ENV, value)
        tl.reap_orphans()
        assert removed == [], (value, removed)


def test_reaper_reads_the_TERMINATION_FACTS_before_an_OWNER_GONE_removal(
        monkeypatch, tmp_path):
    """#7357's rule on the arm that is ARMED: `rm -f` destroys the only evidence
    of WHY the container stopped, and nobody else will report it — this
    container's owning worktree is gone. Mutation: drop the
    `_termination_report` read from that removal (or move it after the `rm`) and
    one of the two assertions fails; the pre-fix path removed with no facts read
    at all, so this is red on it."""
    order: list = []
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-gone\tdead\n", "")
        if a[0] == "inspect" and a[2] == tl._TERMINATION_FORMAT:
            order.append("facts")
            return _R(0, "true 137 0 2147483648\n", "")
        if a[0] == "rm":
            order.append("rm")
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs",
                        lambda: {"gone": str(tmp_path / "removed-worktree")})
    out = tl.reap_orphans()
    assert removed == [("-f", "fdb-lane-gone")], (removed, out)
    assert order == ["facts", "rm"], (order, out)   # read BEFORE the removal
    assert "KILLED FOR MEMORY" in out[0], out
    assert "OOMKilled=true" in out[0] and "ExitCode=137" in out[0], out


def test_reaper_reads_the_TERMINATION_FACTS_before_it_removes(monkeypatch,
                                                             tmp_path):
    """#7357's rule, applied to the OPT-IN arm that removes a live owner's
    container: `rm -f` destroys the only evidence of WHY it stopped, and this
    reap runs BEFORE that lane's own `start()` — whose termination report is the
    read #7357 added. After the removal, that lane reads `absent` and reports
    nothing, so if the facts are not read and printed HERE they are gone.

    Measured shape this protects: a Docker-daemon restart leaves every lane
    container `exited`, and one opt-in sweep removes the fleet's whole diagnostic
    in one pass. Mutation: drop the `_termination_report` read (or move it after
    the `rm`) and one of the two assertions fails."""
    owner = tmp_path / "worktree-of-a-dead-lane"
    owner.mkdir()
    order: list = []
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-deadlane\texited\n", "")
        if a[0] == "inspect" and a[2] == tl._TERMINATION_FORMAT:
            order.append("facts")
            # OOMKilled=true / ExitCode=137 / RestartCount=0 / 2 GiB cap
            return _R(0, "true 137 0 2147483648\n", "")
        if a[0] == "inspect":
            return _R(0, "exited\n", "")
        if a[0] == "rm":
            order.append("rm")
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"deadlane": str(owner)})
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: 0.0)
    monkeypatch.setenv(tl.STOPPED_REAP_ENV, "1")   # the arm this test pins
    out = tl.reap_orphans()
    assert removed == [("-f", "fdb-lane-deadlane")], (removed, out)
    assert order == ["facts", "rm"], (order, out)   # read BEFORE the removal
    line = out[0]
    assert "KILLED FOR MEMORY" in line, out
    assert "OOMKilled=true" in line and "ExitCode=137" in line, out


def test_reaper_still_never_reaps_a_RUNNING_container_of_a_live_owner(
        tmp_path, monkeypatch):
    """The NEGATIVE half, and the one the #5084 ruling protects: the new
    state arm must not reach a LIVE database. A running container of an
    existing worktree is kept at any age, and a running container nobody here
    claims is kept too (report only) — the mutation that must fail is widening
    the arm from `_TERMINAL_STATES` to "anything not running": that deletes
    `paused`/`restarting`/`created` databases, which `_LIVE_ISH_STATES` exists
    to protect."""
    owner = tmp_path / "live-worktree"
    owner.mkdir()
    removed: list = []
    states = {"owned": "running", "unowned": "running",
              "pausedone": "paused", "createdone": "created",
              "cycling": "restarting", "weird": "some-future-state"}

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "".join(f"fdb-lane-{k}\t{v}\n"
                                 for k, v in states.items()), "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    # EVERY named container has a DETERMINED, EXISTING owner, so the ownership
    # gate short-circuits nothing and the STATE is the only rule under test —
    # and the arm is ARMED, so widening it to `_LIVE_ISH_STATES` removes them.
    monkeypatch.setattr(tl, "_worktree_slugs",
                        lambda: {k: str(owner) for k in states})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: {str(owner)})
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: 0.0)
    monkeypatch.setattr(tl, "container_state",
                        lambda n: states[n[len("fdb-lane-"):]])
    monkeypatch.setenv(tl.STOPPED_REAP_ENV, "1")
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert len(out) == len(states), (out,)
    # The live-ish states must be KEPT BY THE OWNERSHIP BRANCH, not rescued by
    # the re-read later: their line has to be the ordinary keep, so a widening
    # of the terminal arm to `_LIVE_ISH_STATES` (the mutant this test exists
    # for) is visible as a different line rather than silently absorbed by the
    # second guard.
    for name in ("pausedone", "createdone", "cycling"):
        line = next(x for x in out if name in x)
        assert "owning lane is LIVE" in line, (name, line)
    for line in out:
        assert "FAILED" not in line, out


def test_reaper_never_reaps_a_TERMINAL_container_it_cannot_ATTRIBUTE(
        monkeypatch):
    """#5084's ruling, and the reason the state arm is gated on a DETERMINED
    owner: when this clone cannot name the worktree that owns a container, it
    is REPORT ONLY — however stopped it is. The measured fleet has such names
    (`fdb-lane-fix9326`, `fdb-lane-tm9190` — no 10-hex slug, so no worktree can
    ever claim them), and they may belong to another clone whose lane this tool
    cannot see. Mutation: drop the `owner is not None` guard and this fails —
    which is a silent reversal of a recorded owner ruling, not a bug fix."""
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-someoneelse\texited\n"
                         "fdb-lane-tm9190\tdead\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    # INSIDE the backstop: "cannot attribute" still removes nothing on its own.
    # (The backstop itself is proven by
    # test_reaper_backstop_mutant_without_it_a_cold_unowned_container_survives.)
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time())
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert all("report only" in line for line in out), out


def test_reaper_re_reads_the_state_before_removing(monkeypatch, tmp_path):
    """The sweep's state came from ONE `docker ps`, and a lane restarting its
    own container reuses the SAME name — so an `exited` snapshot can be stale
    by the time the `rm` lands, and `rm -f` would SIGKILL a live FalkorDB
    mid-suite. The removal is therefore decided by a state read taken at the
    moment of action (the rule `start()` already states for `_wait_ready`).
    This is the OPT-IN arm (the default removes nothing at all, see
    `..._KEEPS_a_TERMINAL_container_of_a_LIVE_owner_by_DEFAULT`).
    Mutation: remove the re-read and this fails."""
    owner = tmp_path / "live-worktree"
    owner.mkdir()
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-cameback\texited\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"cameback": str(owner)})
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setenv(tl.STOPPED_REAP_ENV, "1")
    out = tl.reap_orphans()
    assert removed == [], removed
    assert "at removal time" in out[0], out


def test_reaper_reports_NO_termination_facts_for_a_RUNNING_owner_gone_removal(
        monkeypatch):
    """Proof 1 also fires for a container that is UP — a lane container outlives
    its worktree by design. Its docker facts then describe a PREVIOUS
    termination, and `_termination_report(live=False)` would state "it did not
    hang, it was terminated", which is a FALSE diagnosis for a live server (its
    own `live` flag exists to prevent exactly that, and `start()` refuses to
    append any report to an unreadable state). Mutation: read the facts
    unconditionally and this fails; the terminal-state evidence test above still
    passes, so only this one pins the distinction.

    ⛔ The removal also needs the SECOND proof (#10841 review, P1): a running
    container is only removed once the gap proves nobody is using it. Both are
    stubbed here so the facts rule is what the assertion isolates.
    """
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-lively\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"lively": "/gone/worktree"})
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "_termination_report",
                        lambda _n, live: "KILLED FOR MEMORY (OOMKilled=true)")
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: 5.0)
    out = tl.reap_orphans()
    assert removed == [("-f", "fdb-lane-lively")], (removed, out)
    assert "KILLED FOR MEMORY" not in out[0], out
    assert "owning worktree" in out[0], out


def test_reaper_never_reaps_THIS_worktrees_own_container_however_stopped(
        monkeypatch):
    """`stop()` is the only thing that may remove this worktree's container.
    The new state arm must not become a second way to name it: a stopped
    container of MY OWN lane is left to `start()`, which replaces it with its
    own diagnosis. (Mutation: drop the `name == mine` guard and this fails.)"""
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-mine\texited\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    out = tl.reap_orphans()
    assert removed == [], removed
    assert "THIS worktree's container" in out[0], out


def test_reaper_carries_the_termination_evidence_into_its_report(
        monkeypatch, tmp_path):
    """#7357's evidence must survive a REMOVAL: after `rm -f` the facts are gone,
    and the owner lane's next `start()` reads `absent` and reports nothing, so
    an OOM-killed lane reads exactly like a clean exit. Mutation: drop
    `_termination_report` from the armed removal and this fails.

    (The WITHHELD default removal-free path deliberately spends no inspect —
    `test_reaper_KEEPS_a_TERMINAL_container_of_a_LIVE_owner_by_DEFAULT` pins
    that half.)"""
    owner = tmp_path / "live-worktree"
    owner.mkdir()
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-oomed\texited\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"oomed": str(owner)})
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    monkeypatch.setenv(tl.STOPPED_REAP_ENV, "1")   # the arm must be ARMED here
    monkeypatch.setattr(tl, "_termination_report",
                        lambda _n, live: "killed for memory (OOMKilled=true)")
    out = tl.reap_orphans()
    assert removed == [("-f", "fdb-lane-oomed")], (removed, out)
    assert "removed fdb-lane-oomed" in out[0], out
    assert "OOMKilled" in out[0], out


def test_reaper_reads_the_STATE_from_docker_ps_not_from_its_own_head(
        monkeypatch):
    """The state arm is meaningless unless the state is actually READ. Pinned
    at the argv: with `--format "{{.Names}}"` the parse yields no state, every
    container reads `unknown`, the arm never fires, and the port exhaustion
    returns with a fully green suite — the exact measured bug, silently.

    The two OWNERSHIP columns are pinned for the same reason and against the
    same failure mode. `reap_orphans` reads them by INDEX (`parts[2]` owner,
    `parts[3]` slug) and hands them to `_trusted_label_owner`, where an empty
    string means "no label at all: absence is not a contradiction" — and docker
    renders a key it cannot find as `""` rather than erroring
    (`formatter.ContainerContext.Label` returns `""` for a nil/absent key). So
    dropping or reordering either placeholder is a one-character edit that
    turns every labelled container into the INDETERMINATE population — #8180's
    leak, back again, with a green suite. The exact column SEQUENCE is asserted
    because the order is what makes `parts[2]`/`parts[3]` the right fields."""
    seen: list = []

    def fake(*a, **k):
        seen.append(a)
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    tl.reap_orphans()
    ps = [a for a in seen if a[0] == "ps"]
    assert ps, seen
    argv = list(ps[0])
    fmt = argv[argv.index("--format") + 1]
    assert "{{.Names}}" in fmt and "{{.State}}" in fmt, fmt
    cols = fmt.split("\t")
    assert len(cols) == 4, fmt
    assert cols[0] == "{{.Names}}" and cols[1] == "{{.State}}", fmt
    assert cols[2] == f'{{{{.Label "{tl.LABEL_OWNER}"}}}}', fmt
    assert cols[3] == f'{{{{.Label "{tl.LABEL_SLUG}"}}}}', fmt


def test_reaper_reads_no_state_as_UNKNOWN_not_as_terminal(tmp_path, monkeypatch):
    """Fail-closed on the parse: a `docker ps` line with no state (a daemon
    that answered in another shape, an older format) must never be read as
    "stopped", because that would authorize a removal from a failed read — the
    one rule this module applies everywhere else.

    The fixture is built so ONLY the state rule can decide it: the owner EXISTS
    (proof 1 cannot fire) and the stopped arm is ARMED (so a terminal parse
    WOULD remove). Mutation: `state.strip().lower() or "unknown"` ->
    `or "exited"` and this fails; with a non-existent owner it would pass
    whatever the parse did, because proof 1 removes it first."""
    owner = tmp_path / "existing-worktree"
    owner.mkdir()
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-nostate\n", "")   # no tab, no state
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"nostate": str(owner)})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: {str(owner)})
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: 0.0)
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    monkeypatch.setenv(tl.STOPPED_REAP_ENV, "1")
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "kept fdb-lane-nostate" in out[0], out


@pytest.fixture(autouse=True)
def _isolated_reap_state(tmp_path, monkeypatch):
    """NEVER let a test write the LIVE fleet reap state, and never let a test's
    fake `_docker` be surprised by the lease's `inspect`.

    The production sweep reads `REAP_STATE_DIR`, so a test that recorded a real
    container's name would make that container reapable by a later sweep. Caught
    in review cycle 3: this suite had left a real
    `state/lane-reap/fdb-lane-tm9190.unused` behind on this box.

    `_touch_lease` is stubbed NO-OP by default because it spends one `docker
    inspect` on `start()`, and most tests here pin an exact docker call shape;
    the two tests below un-stub it to prove the call sites exist.
    """
    monkeypatch.setattr(tl, "REAP_STATE_DIR", tmp_path / "reap-state")
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)


def test_START_records_a_lease_on_both_paths(lane, monkeypatch):
    """Review cycle 4, P1: the lease's whole value is that the LANE writes it, and
    nothing tested that `start()` ever does — so deleting either call left the
    suite green (which is how an unrefreshed lease reached a live `run`).

    Mutation: delete `_touch_lease(name)` from either `start()` exit and one of
    these two asserts fails."""
    touched: list = []
    monkeypatch.setattr(tl, "_touch_lease", lambda n: touched.append(n))
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})

    # CREATE path: no container yet.
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "deadbeef\n", "") if a[0] == "run" else _R(0, "0.0.0.0:16390\n" if a[0] == "port" else "", ""))
    monkeypatch.setattr(tl, "_wait_ready", lambda _n, _p=None: True)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 0)
    tl.start()
    assert touched, "the CREATE path must record a lease"

    # REUSE path: a running container.
    touched.clear()
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_container_memory_limit", lambda _n: 2 << 30)
    monkeypatch.setattr(tl, "_container_live_maxmemory", lambda _n: 1536 << 20)
    monkeypatch.setattr(tl, "_container_redis_args", lambda _n: "")
    tl.start()
    assert touched, "the REUSE path must record a lease"


def test_a_failed_lease_write_leaves_NO_record(monkeypatch):
    monkeypatch.setattr(tl, "_touch_lease", _REAL_TOUCH_LEASE)
    """Review cycle 4, P1: a stale record still MATCHES the live container's id,
    so a failed write left `_unused_gap` ageing the old stamp — reading "this lane
    just used its DB" as "it has not used it for hours". Mutation: skip the
    drop on failure and the last assert fails."""
    monkeypatch.setattr(tl, "_container_id", lambda _n: "eeeeeeeeeeee")
    f = tl._reap_state_path("fdb-lane-w0123456789")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"eeeeeeeeeeee {time.time() - 9 * 3600:.0f}\n")

    def _no_write(_self, *_a, **_k):
        raise OSError("read-only state dir")
    monkeypatch.setattr(type(f), "write_text", _no_write, raising=False)
    tl._touch_lease("fdb-lane-w0123456789")
    assert not f.exists(), "a failed write must leave no record to age"
    assert tl._unused_gap("fdb-lane-w0123456789") is None


def test_unused_gap_is_keyed_to_the_INSTANCE_not_the_name(monkeypatch):
    monkeypatch.setattr(tl, "_touch_lease", _REAL_TOUCH_LEASE)
    """REGRESSION for review cycle 3's P0: a lane's own `down`, or the next
    `start()` replacing a stopped container, keeps the NAME. A name-keyed record
    handed the REPLACEMENT the dead container's gap, and a fresh container was
    deleted at the first moment it was between steps.

    Mutation: drop the `prior[0] != cid` test and the last assertion fails."""
    ids = iter(["aaaaaaaaaaaa", "bbbbbbbbbbbb"])
    monkeypatch.setattr(tl, "_container_id", lambda _n: next(ids, "bbbbbbbbbbbb"))
    assert tl._unused_gap("fdb-lane-x0123456789") is None
    f = tl._reap_state_path("fdb-lane-x0123456789")
    f.write_text(f"aaaaaaaaaaaa {time.time() - 9 * 3600:.0f}\n")
    # SAME name, DIFFERENT container -> first sighting again, never a 9h gap
    assert tl._unused_gap("fdb-lane-x0123456789") is None


def test_touch_lease_restarts_the_clock(monkeypatch):
    monkeypatch.setattr(tl, "_touch_lease", _REAL_TOUCH_LEASE)
    """REGRESSION for review cycle 3's second P0: the clock must restart when the
    LANE uses the container, not when a peer sweep happens to observe a hold — and
    not on the reaper's own probe (the reason the `INFO stats` counter arm was
    inert; its own read incremented it, proved by the real-container run).

    Mutation: remove `_touch_lease` from `start()` and a live lane that steps
    between two peer sweeps is reaped by the gap its own sweeps never reset."""
    monkeypatch.setattr(tl, "_container_id", lambda _n: "cccccccccccc")
    f = tl._reap_state_path("fdb-lane-y0123456789")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"cccccccccccc {time.time() - 9 * 3600:.0f}\n")
    assert tl._unused_gap("fdb-lane-y0123456789") > 8
    tl._touch_lease("fdb-lane-y0123456789")          # the lane used it
    assert tl._unused_gap("fdb-lane-y0123456789") < 0.1


def test_unused_gap_reads_a_TERMINAL_container(monkeypatch):
    """A stopped container cannot be touched by a lane, so its lease stops
    advancing and its gap is pure wall time — that is the population holding
    ports out of PORT_RANGE, and it must stay reclaimable."""
    monkeypatch.setattr(tl, "_container_id", lambda _n: "dddddddddddd")
    f = tl._reap_state_path("fdb-lane-z0123456789")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"dddddddddddd {time.time() - 30 * 3600:.0f}\n")
    gap = tl._unused_gap("fdb-lane-z0123456789")
    assert gap is not None and gap > 29, gap


def test_arm_3_re_checks_liveness_at_the_moment_of_action(tmp_path, monkeypatch):
    """Review cycle 5, P1: arm 3 re-read only docker's STATE, which carries no
    liveness. The sweep-wide `/proc` scan is taken at the TOP of a sweep that on
    a loaded host is many seconds of docker calls, so a lane appearing mid-sweep
    was invisible — and arm 3 is the arm a cross-`CELL_HOME` lane depends on,
    because its lease lives elsewhere.

    Mutation: drop the `now_live` re-read in arm 3 and this container is removed
    while a fresh `/proc` scan names the lane that just took it.

    Also pins the LEASE stamp on that observed use (#10693 review F1): a lane
    that adopts the container mid-sweep must not keep the epoch from before its
    own `start()`, or the NEXT sweep could find the gap already expired during a
    normal between-steps window."""
    wt = tmp_path / ".worktrees" / "late-lane"
    wt.mkdir(parents=True)
    slug = tl.slug_for(wt)
    removed: list = []
    touched: list = []
    reads = {"n": 0}

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, f"fdb-lane-{slug}\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    def live():
        reads["n"] += 1
        return set() if reads["n"] == 1 else {str(wt)}   # lane appears mid-sweep

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda n: touched.append(n))
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})   # owner indeterminate
    monkeypatch.setattr(tl, "_live_lane_paths", live)
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 40 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: 40.0)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "became LIVE" in "\n".join(out), out
    # The observed use at the moment of action refreshed the lease.
    assert touched == [f"fdb-lane-{slug}"], (touched, out)


def test_arm_3_spares_a_container_by_its_CLIENT_at_the_moment_of_action(
        tmp_path, monkeypatch):
    """Review cycle 6, P1: the sibling test patches `_container_in_use` to a
    constant, so it says nothing about the client clause — setting
    `now_busy = False` left the suite green.

    The clause is load-bearing, not redundant: a client exec'd from a cwd that is
    NOT the worktree (e.g. `docker exec` from `/tmp`, whose cmdline carries the
    container NAME and no worktree path) is invisible to `_live_lane_paths` and is
    spared only by the client probe.

    Mutation: set `now_busy = False` at the moment of action and this fails.

    Also pins the LEASE stamp on that observed use (#10693 review F1)."""
    wt = tmp_path / ".worktrees" / "client-lane"
    wt.mkdir(parents=True)
    slug = tl.slug_for(wt)
    removed: list = []
    touched: list = []
    probes = {"n": 0}

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, f"fdb-lane-{slug}\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    def in_use(_n):
        probes["n"] += 1
        return probes["n"] > 1      # quiet at the sweep, a client attaches after

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda n: touched.append(n))
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", in_use)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 40 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: 40.0)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "became LIVE" in "\n".join(out), out
    # The observed client at the moment of action refreshed the lease.
    assert touched == [f"fdb-lane-{slug}"], (touched, out)


def test_arm_2_refreshes_the_lease_on_every_OBSERVED_use(tmp_path, monkeypatch):
    """#10693 review F1 at `74bf1d99e`: the sweep's POSITIVE observations of use
    stamped nothing, so the lease measured "time since the lane last called
    `start()`/`run`", not "time since the container was demonstrably in use".

    That gap is reachable by the command `AGENTS.md` tells lanes to use —
    `uri="$(... test_lane.py uri)"; eval "$uri"` calls `start()` ONCE, and every
    later `uv run pytest` against the exported URI writes no stamp. One
    full-suite step is measured at ~2 h wall in this file, so the lease reaches
    `OWNER_GRACE_H` at the end of NORMAL work and the next between-steps instant
    (no process in the worktree, no client) completes arm 2's evidence chain on a
    lane that is alive and working.

    Mutation: drop `_touch_lease(name)` from arm 2's `held` branch, or from its
    `busy` branch, and one of these asserts fails."""
    owner = tmp_path / "live-lane-worktree"
    owner.mkdir()
    removed: list = []
    touched: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-observedlane\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda n: touched.append(n))
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"observedlane": str(owner)})
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 5 * 3600)

    # (1) a live process HOLDS the owning worktree (the documented eval form).
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: {str(owner)})
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "owning lane is LIVE" in "\n".join(out), out
    assert touched == ["fdb-lane-observedlane"], (touched, out)

    # (2) no holder right now, but the container reports an ACTIVE CLIENT.
    touched.clear()
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: True)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "HAS an active client" in "\n".join(out), out
    assert touched == ["fdb-lane-observedlane"], (touched, out)


def test_arm_2_re_reads_liveness_and_STAMPS_the_lease_at_the_moment_of_action(
        tmp_path, monkeypatch):
    """Arm 2's moment-of-action re-read had no test of its own (arm 3's has two),
    and it is the read that spares a lane which adopted the container mid-sweep —
    the `/proc` scan was taken at the TOP of a sweep that on a loaded host is
    many seconds of docker calls.

    It also pins the LEASE stamp on that observed use (#10693 review F1): without
    it the adopting lane keeps an epoch from before its own `start()`, and the
    NEXT sweep can find the gap already expired in a normal between-steps window.

    Mutation: drop the moment-of-action `now_live` re-read, or the
    `_touch_lease(name)` that follows it, and this fails."""
    owner = tmp_path / "live-lane-worktree"
    owner.mkdir()
    removed: list = []
    touched: list = []
    reads = {"n": 0}

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-latelane\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    def live():
        reads["n"] += 1
        return set() if reads["n"] == 1 else {str(owner)}   # lane adopts mid-sweep

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda n: touched.append(n))
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"latelane": str(owner)})
    monkeypatch.setattr(tl, "_live_lane_paths", live)
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 5 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 40.0)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "became live mid-sweep" in "\n".join(out), out
    assert touched == ["fdb-lane-latelane"], (touched, out)


def test_arm_2_fails_CLOSED_when_the_moment_of_action_liveness_goes_UNREADABLE(
        tmp_path, monkeypatch):
    """`None` is NOT "nothing is alive", and it must never fall through the
    moment-of-action `or` chain to a `docker rm -f`.

    Everything else in this sweep says the lane is gone: the owning worktree
    exists, the container is hours old, the gap is far past `OWNER_GRACE_H`, no
    client is attached, and the operator has ARMED the removal. The only
    difference is that the second liveness read returns `None` — `/proc` became
    unreadable between the sweep snapshot and the moment of action. The
    container must be KEPT and reported: an unreadable liveness surface is the
    one fail-open step a destructive path must not have.

    Mutation: drop `now_live is None or` from the moment-of-action chain in
    `reap_orphans` and this reaps — red."""
    owner = tmp_path / "live-lane-worktree"
    owner.mkdir()
    removed: list = []
    reads = {"n": 0}

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-losinglane\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    def live():
        reads["n"] += 1
        # (1) readable with no holder, (2) the surface goes away mid-sweep.
        return set() if reads["n"] == 1 else None

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"losinglane": str(owner)})
    monkeypatch.setattr(tl, "_live_lane_paths", live)
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 5 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 40.0)
    monkeypatch.setenv(tl.LANE_GONE_REAP_ENV, "1")
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "could not be re-established at the moment of action" in "\n".join(out), out


def test_arm_3_fails_CLOSED_when_the_moment_of_action_liveness_goes_UNREADABLE(
        tmp_path, monkeypatch):
    """Arm 3's moment-of-action chain carries the SAME fail-closed term as arm 2's,
    and until this test existed nothing pinned it.

    Measured, not assumed: deleting `now_live is None or` from arm 3's chain left
    the whole suite GREEN (the sibling test one screen up does not arm the
    consent switch, so `removed == []` there is held by the report-only gate that
    runs *after* this one). With the term gone and the operator's switch ARMED,
    an unreadable `/proc` at the moment of action makes `now_named` False by
    construction — `now_live is not None and ...` is the term that produces it —
    so the chain falls through to `docker rm -f` on a container the sweep can no
    longer establish is unheld. That is the one fail-open step a destructive path
    must not have, on the arm a cross-`CELL_HOME` lane depends on.

    Mutation: drop `now_live is None or` from arm 3's moment-of-action chain in
    `reap_orphans` and this reaps — red (`removed == []` fails)."""
    wt = tmp_path / ".worktrees" / "lost-lane"
    wt.mkdir(parents=True)
    slug = tl.slug_for(wt)          # 10 hex, so `_HEX_SLUG` admits the name
    removed: list = []
    reads = {"n": 0}

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, f"fdb-lane-{slug}\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    def live():
        reads["n"] += 1
        # (1) readable with no holder, (2) the surface goes away mid-sweep.
        return set() if reads["n"] == 1 else None

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})   # owner indeterminate
    monkeypatch.setattr(tl, "_live_lane_paths", live)
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 40 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: 40.0)
    monkeypatch.setenv(tl.OWNER_INDETERMINATE_REAP_ENV, "1")
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "liveness could not be re-established" in "\n".join(out), out


@pytest.mark.parametrize("unreadable_at", ["sweep", "moment_of_action"])
def test_arm_2_fails_CLOSED_when_the_CLIENT_probe_goes_UNREADABLE(
        tmp_path, monkeypatch, unreadable_at):
    """`_container_in_use` returning `None` is not "no client attached".

    Both places arm 2 consults the container for an attached client carry the
    same fail-closed term — `if busy is None:` at the sweep snapshot and
    `busy_now is not False` at the moment of action — and until this test
    existed NEITHER was pinned. Measured: replacing `busy_now is not False` with
    the truthiness test leaves the whole suite green, and so does deleting the
    `busy is None` branch (with the second probe answering `False`, which is the
    only state in which the earlier guard is the deciding one). An unreadable
    probe then falls straight through to `docker rm -f` — the one fail-open step
    a destructive path must not have, and the rule the source comment states
    verbatim ("None (unreadable) must never fall through the `or` chain to the
    removal").

    The stub counts calls: probe #1 is the sweep snapshot, probe #2 is the
    moment-of-action re-read, so each parametrisation makes exactly ONE of them
    unreadable and leaves the other `False`.

    Mutation: delete the arm-2 `if busy is None:` branch (`sweep`) or replace
    `busy_now is not False` with `busy_now` (`moment_of_action`) and the matching
    case reaps — red."""
    owner = tmp_path / "live-lane-worktree"
    owner.mkdir()
    removed: list = []
    probes = {"n": 0}

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-losinglane\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    def in_use(_name):
        probes["n"] += 1
        return None if probes["n"] == (1 if unreadable_at == "sweep" else 2) else False

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"losinglane": str(owner)})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", in_use)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 5 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 40.0)
    monkeypatch.setenv(tl.LANE_GONE_REAP_ENV, "1")
    out = tl.reap_orphans()
    assert removed == [], (unreadable_at, removed, out)
    assert probes["n"] == (1 if unreadable_at == "sweep" else 2), (unreadable_at, probes)
    expected = ("could not be asked whether it is in use"
                if unreadable_at == "sweep"
                else "could not be re-established at the moment of action")
    assert expected in "\n".join(out), (unreadable_at, out)


@pytest.mark.parametrize("unreadable_at", ["sweep", "moment_of_action"])
def test_arm_3_fails_CLOSED_when_the_CLIENT_probe_goes_UNREADABLE(
        tmp_path, monkeypatch, unreadable_at):
    """Arm 3's two client probes carry the same unpinned fail-closed terms.

    Same measured gap as arm 2: neither `if busy is None:` (sweep) nor
    `now_busy is not False` (moment of action) was pinned, so either could be
    deleted or turned into a truthiness test with the suite still green — and an
    unreadable `CLIENT LIST` (an `ERR`/`LOADING`/`NOAUTH` reply, or a failed
    `docker exec`) would then be read as "nothing is using it" on the arm a
    cross-`CELL_HOME` lane depends on, because its lease lives elsewhere.

    Mutation: delete arm 3's `if busy is None:` branch (`sweep`) or replace
    `now_busy is not False` with `now_busy` (`moment_of_action`) and the matching
    case reaps — red."""
    wt = tmp_path / ".worktrees" / "clientless-lane"
    wt.mkdir(parents=True)
    slug = tl.slug_for(wt)
    removed: list = []
    probes = {"n": 0}

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, f"fdb-lane-{slug}\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    def in_use(_name):
        probes["n"] += 1
        return None if probes["n"] == (1 if unreadable_at == "sweep" else 2) else False

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})   # owner indeterminate
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", in_use)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 40 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 40.0)
    monkeypatch.setenv(tl.OWNER_INDETERMINATE_REAP_ENV, "1")
    out = tl.reap_orphans()
    assert removed == [], (unreadable_at, removed, out)
    assert probes["n"] == (1 if unreadable_at == "sweep" else 2), (unreadable_at, probes)
    expected = ("could not be asked whether it is in use"
                if unreadable_at == "sweep"
                else "liveness could not be re-established")
    assert expected in "\n".join(out), (unreadable_at, out)


def test_arm_2_reports_an_UNREADABLE_proc_as_UNKNOWN_not_as_unheld(
        tmp_path, monkeypatch):
    """The report must not state an unreadable read AS A RESULT (#10693 review).

    Arm 2's `held` is `live_paths is not None and any(...)`, so when
    `_live_lane_paths()` returns `None` (it could not read `/proc`) `held` is
    False **by construction, never by a read** — yet this branch used to print
    "…and no live process holds it, but the liveness surface (/proc) could not
    be read", asserting the conclusion and then retracting it in the same
    sentence. `_live_lane_paths`' own docstring forbids that reading ("an
    unreadable liveness surface must never be read as 'nothing is alive'"), and
    arm 3 states the same situation correctly as UNKNOWN.

    It is not hypothetical: on a host with no `/proc` (macOS) EVERY ownered peer
    container lands here, so a live lane mid-run was reported as unheld with a
    `rm -f` remedy one line below — fail-closed in ACTION, misleading in REPORT,
    and the report is what this default ships.

    Mutation: restore the shared "and no live process holds it, but …" wording
    for the `live_paths is None` case and the first assert fails."""
    owner = tmp_path / "live-lane-worktree"
    owner.mkdir()
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-unread0001\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"unread0001": str(owner)})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: None)   # /proc unreadable
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 9 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 40.0)
    # ARMED, so the only thing keeping it is the fail-closed guard itself.
    monkeypatch.setenv(tl.LANE_GONE_REAP_ENV, "1")
    out = tl.reap_orphans()
    joined = "\n".join(out)
    assert removed == [], (removed, out)
    assert "no live process holds it" not in joined, (
        f"an unreadable /proc was reported as 'no live process holds it' — the "
        f"result of a read that never happened: {joined}")
    assert "whether a live process holds it is UNKNOWN" in joined, out
    assert "/proc could not be read" in joined, out
    assert "report only, fail-closed" in joined, out


def test_arm_2_keeps_its_accurate_claim_when_only_the_AGE_is_unreadable(
        tmp_path, monkeypatch):
    """The other half of the branch: `/proc` WAS read and reported no holder, so
    "no live process holds it" is a measured fact there — only the start time
    failed. The fix must not swing to "UNKNOWN" for a fact that was read.

    This pins the pair, so a future edit cannot collapse the two cases into one
    sentence again in either direction."""
    owner = tmp_path / "live-lane-worktree"
    owner.mkdir()
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-ageless001\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"ageless001": str(owner)})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())   # READ, nothing alive
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: None)   # age unreadable
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 40.0)
    monkeypatch.setenv(tl.LANE_GONE_REAP_ENV, "1")
    out = tl.reap_orphans()
    joined = "\n".join(out)
    assert removed == [], (removed, out)
    assert "no live process holds it" in joined, out
    assert "its start time could not be read" in joined, out
    assert "UNKNOWN" not in joined, out
    assert "report only, fail-closed" in joined, out


def test_arm_3_fails_CLOSED_when_proc_cannot_be_read(tmp_path, monkeypatch):
    """Arm 3's cheap first guard, pinned where it is load-bearing.

    A container no worktree in this clone claims, hours old, no client, and the
    operator has ARMED `OWNER_INDETERMINATE_REAP_ENV` — arm 3 would remove it.
    The one fact standing in the way is that `/proc` could not be read, so
    ownership is NOT determined: the recorded #5084/#8180 ruling is to report
    and delete nothing.

    Until now this branch had no test of its own: it was reached only by
    accident on a host without `/proc` (macOS), which read as a different
    test's failure. Mutation: delete the `live_slugs is None` guard and this
    test no longer keeps the container (the later `slug_for_name in live_slugs`
    faults on `None`) — red."""
    wt = tmp_path / ".worktrees" / "cross-cell-lane"
    wt.mkdir(parents=True)
    name = f"fdb-lane-{tl.slug_for(wt)}"
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, f"{name}\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})   # owner indeterminate
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: None)
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 40 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: 40.0)
    monkeypatch.setenv(tl.OWNER_INDETERMINATE_REAP_ENV, "1")
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "/proc could not be read" in "\n".join(out), out


def test_arm_3_refreshes_the_lease_on_every_OBSERVED_use(tmp_path, monkeypatch):
    """The same rule as arm 2 (#10693 review F1), and more load-bearing here:
    arm 3 is the arm a cross-`CELL_HOME` lane depends on, and that lane's lease is
    written on the OTHER CELL_HOME — so a positive observation by THIS sweep (a
    live process naming the worktree, or an attached client) is the only fresh
    evidence this box ever gets about it.

    Mutation: drop `_touch_lease(name)` from arm 3's live-slug branch, or from its
    `busy` branch, and one of these asserts fails."""
    wt = tmp_path / ".worktrees" / "cross-cell-lane"
    wt.mkdir(parents=True)
    slug = tl.slug_for(wt)
    name = f"fdb-lane-{slug}"
    removed: list = []
    touched: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, f"{name}\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_touch_lease", lambda n: touched.append(n))
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})   # owner indeterminate
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 40 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: 40.0)
    monkeypatch.setenv(tl.OWNER_INDETERMINATE_REAP_ENV, "1")

    # (1) a live process elsewhere on the host NAMES the worktree.
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: {str(wt)})
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "LIVE process elsewhere" in "\n".join(out), out
    assert touched == [name], (touched, out)

    # (2) nobody names it, but the container reports an ACTIVE CLIENT.
    touched.clear()
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: True)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "HAS an active client" in "\n".join(out), out
    assert touched == [name], (touched, out)


def test_ancestors_terminates_on_a_double_slash_path():
    """Review cycle 6, P2: `os.path.dirname("//") == "//"` (POSIX keeps a
    leading double slash), so a path like `//a` — reachable as a cmdline token,
    or as a concatenated prefix plus relative path where the prefix is a single
    slash — spun forever. `reap_orphans` runs on EVERY lane start, so that is a
    hang for every lane on the box.

    Mutation: drop the `nxt == cur` break and this test never returns."""
    for path in ("//a", "//", "/", "", "a", "/home//x/.worktrees/y"):
        assert isinstance(list(tl._ancestors(path)), list)


def test_a_STOPPED_ownerless_orphan_is_reclaimed(monkeypatch):
    """The population that exhausts the port range: a STOPPED container nobody
    owns keeps its published port reserved (measured: 67 of 110 ports held by
    EXITED containers). `docker exec` into a stopped container always fails, so
    `_container_in_use` is None there — and if None kept the container, the
    backstop could never reclaim them. A terminal container cannot hold a client,
    which is positive evidence from docker's own state, not an absence.

    Mutation: read `busy` from the probe even when the state is terminal (i.e.
    drop the short-circuit) and this fails."""
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-0fedcba987\texited\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    # Exactly what a real `docker exec` into a stopped container does: fails.
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: None)
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 40 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: 40.0)
    # The removal itself is an operator opt-in (#10693 review F2): the default
    # REPORTS this container (see test_arm_3_defaults_to_REPORT_ONLY).
    monkeypatch.setenv(tl.OWNER_INDETERMINATE_REAP_ENV, "1")
    out = tl.reap_orphans()
    assert removed == [("-f", "fdb-lane-0fedcba987")], (removed, out)


def test_arm_3_defaults_to_REPORT_ONLY_and_names_the_opt_in(monkeypatch):
    """#10693 review F2, the DEFAULT half. The evidence is complete — no owner in
    this clone, no live process, no client, unused 40 h > the 24 h backstop — and
    the container is still NOT removed, because #8180's ruling says both "an age
    backstop for owner-INDETERMINATE containers" (the issue's Outcome) and "if
    ownership cannot be determined, do NOT delete — report only" (the owner's
    boundaries) about this exact population. A lane does not pick between two
    owner sentences; the report tells the operator what it may release.

    Mutation: drop the `_env_consent(OWNER_INDETERMINATE_REAP_ENV)` gate and this
    container is removed — the unruled departure the review refused."""
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, f"{LANE_FIXTURE_NAME}\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.delenv(tl.OWNER_INDETERMINATE_REAP_ENV, raising=False)
    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 30 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 40.0)
    out = tl.reap_orphans()
    joined = "\n".join(out)
    assert removed == [], (removed, out)
    assert tl.OWNER_INDETERMINATE_REAP_ENV in joined, out
    assert "40.0h" in joined and "report only" in joined, out
    # #10693 review ISSUE 2 (P3): the doc promises the evidence chain names the
    # container's AGE as well as the measured gap, and the two are deliberately
    # DIFFERENT measures (`StartedAt` is not idleness — a lane reuses the running
    # container). This container is 30 h old with a 40 h gap, so an assertion on
    # the age cannot be satisfied by the gap: delete `{age_txt}` from the report
    # and this line reds.
    assert "30.0h old" in joined, out


def test_arm_2_defaults_to_REPORT_ONLY_and_names_the_remedy(tmp_path, monkeypatch):
    """#10693 review F2, the DEFAULT half of arm 2. Every positive proof is in
    place — the owner worktree exists, no live process names it, no client, the
    lease is 3 h old — and the container is STILL kept, because #8180 authorises a
    reap "when the worktree that owns it is gone" and this worktree is not.

    The report is the deliverable here: it carries the evidence chain and the ONE
    command that releases the container from its own worktree, so an operator does
    not have to re-derive the attribution by hand.

    Mutation: drop the `_env_consent(LANE_GONE_REAP_ENV)` gate and the container
    is removed — the departure from the recorded ruling the review refused."""
    owner = tmp_path / "stranded-lane"
    owner.mkdir()
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-strand0001\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.delenv(tl.LANE_GONE_REAP_ENV, raising=False)
    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs",
                        lambda: {"strand0001": str(owner)})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 9 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 3.0)
    out = tl.reap_orphans()
    joined = "\n".join(out)
    assert removed == [], (removed, out)
    assert f"cd {owner} && uv run python tools/test_lane.py down" in joined, out
    assert tl.LANE_GONE_REAP_ENV in joined, out
    assert "still EXISTS" in joined, out
    # #10693 review ISSUE 2 (P3): same contract as arm 3 — the evidence chain
    # carries the container's age AND the lease gap. Here they differ on purpose
    # (9 h old, 3 h unused gap), so dropping the age from the report reds this
    # and cannot be masked by the gap string.
    assert "9.0h old" in joined and "3.0h" in joined, out


def test_worktree_of_extracts_the_root_from_a_shell_wrapper_cmdline(tmp_path):
    """The cwd-only test calls a live lane dead; so does a NAIVE cmdline test.

    MEASURED DEFECT (review cycle 1, P0): `_worktree_of` spliced the whole
    command-line PREFIX onto the mark (`text[:i] + "/.worktrees/"`), which yields
    `bash -c 'cd <WT>/...` — never a directory — so the cmdline proof was inert
    and a lane whose only reference was in its wrapper was reaped. Every shape
    below is one the docstring names, and the shell separators (`<WT>;`) are
    glued straight onto the path."""
    wt = tmp_path / "repo" / ".worktrees" / "some-lane"
    wt.mkdir(parents=True)
    for shape in (f"bash -c 'cd {wt} && uv run pytest'",
                  f"bash -c 'cd {wt};uv run pytest'",
                  f"bash -c 'cd {wt}/tests && uv run pytest'",
                  f"cd {wt} && make",
                  str(wt)):
        assert tl._worktree_of(shape) == str(wt), (shape, tl._worktree_of(shape))
    # A name in prose that is NOT a directory is not an owner.
    assert tl._worktree_of("/x/.worktrees/does-not-exist") is None


def test_live_lane_paths_is_NONE_when_no_process_can_be_read(monkeypatch):
    """`None` is "unreadable" and authorises no removal; an EMPTY set is "nothing
    is alive" and would authorise removing everything. Conflating them is the
    fail-open this module forbids everywhere else."""
    monkeypatch.setattr(tl.os, "listdir", lambda _p: [])
    assert tl._live_lane_paths() is None
    monkeypatch.setattr(tl.os, "listdir", lambda _p: ["1", "2"])
    monkeypatch.setattr(tl.os, "readlink", lambda _p: (_ for _ in ()).throw(OSError()))
    assert tl._live_lane_paths() is None


def test_container_in_use_passes_CLIENT_and_LIST_as_SEPARATE_argv(monkeypatch):
    """MEASURED BUG (2026-11-03): `redis-cli "CLIENT LIST"` is ONE argument, and
    the server replies ``ERR unknown command 'CLIENT LIST'`` while `docker exec`
    still exits **0**. The arm therefore answered "nobody is connected" for every
    container — a FALSE NEGATIVE that reaps a live lane's database.

    Mutation: join the two argv entries (or drop the ERR check) and this fails.
    Both halves are pinned: the ARGV shape, and the refusal to read a non-listing
    reply as "unused"."""
    seen: list = []

    def fake(*a, **k):
        seen.append(a)
        return _R(0, "ERR unknown command 'CLIENT LIST'\n", "")

    monkeypatch.setattr(tl, "_docker", fake)
    assert tl._container_in_use("fdb-lane-x") is None, (
        "an ERR reply is UNREADABLE, never 'unused'")
    argv = list(seen[0])
    assert argv[:3] == ["exec", "fdb-lane-x", "redis-cli"], argv
    assert "CLIENT" in argv and "LIST" in argv, argv
    assert not any("CLIENT LIST" in a for a in argv), argv

    # A real listing: the blocking client is activity, and the PROBE itself is
    # not (otherwise every container reports itself busy).
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(
        0, "id=7 cmd=blpop idle=2\nid=8 cmd=client|list idle=0\n", ""))
    assert tl._container_in_use("fdb-lane-x") is True
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(
        0, "id=8 cmd=client|list idle=0\n", ""))
    assert tl._container_in_use("fdb-lane-x") is False

    # Any reply that is not a client LISTING is unreadable -> None. `redis-cli`
    # prints a bare error line for these and still exits 0, so a two-string
    # blocklist would answer "unused" and authorise a kill.
    for reply in ("LOADING Redis is loading the dataset in memory\n",
                  "BUSY Redis is busy running a script\n",
                  "NOAUTH Authentication required.\n",
                  "WRONGTYPE Operation against a key holding the wrong kind\n"):
        monkeypatch.setattr(tl, "_docker",
                            lambda *a, _r=reply, **k: _R(0, _r, ""))
        assert tl._container_in_use("fdb-lane-x") is None, reply


def test_pick_port_names_the_stopped_container_remedy_when_the_range_is_full(
        monkeypatch):
    """The operator-facing half of the measured failure: the message must name
    the cause (a STOPPED container keeps its port) AND a remedy that respects
    #8180 — the owner's own `down`, or the explicit opt-in — so the next reader
    does not go looking for a wider range, a leaked socket, or a reap that the
    default deliberately does not perform. The earlier wording promised that
    `up`/`uri` reaped these containers, which was true only while the withdrawn
    arm was armed, so asserting the opt-in's NAME is what keeps the message
    honest."""
    monkeypatch.setattr(tl, "_published_scan", lambda: {})
    monkeypatch.setattr(tl, "port_is_free", lambda _p: False)
    with pytest.raises(SystemExit) as e:
        tl.pick_port()
    msg = str(e.value)
    assert "no free port" in msg and "stopped" in msg.lower(), msg
    assert "test_lane.py down" in msg, msg          # the owner's own remedy
    assert "TORTOISE_TEST_LANE_REAP_STOPPED=1" in msg, msg
    assert "reaps the ones whose owning worktree this clone can name" not in msg, msg


def test_ping_over_host_is_false_when_nothing_is_listening(monkeypatch):
    """Pins the probe's REAL body. It is stubbed for the whole module, so this
    test must UNDO that stub — otherwise it asserts the stub and `return True`
    inside the real probe would satisfy every test in the file."""
    monkeypatch.undo()
    assert tl._ping_over_host(1, 0.5) is False


def test_ping_over_host_is_true_when_something_answers(monkeypatch):
    """The POSITIVE half of the same REAL body (#8180 review). The negative test
    above only closes one direction: with the real body replaced by
    `return False` — a probe that can never succeed, so every container is
    declared wedged and torn down — the whole file still passed. This binds a
    loopback socket that answers the RESP PING so the probe is pinned BOTH ways."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve() -> None:
        conn, _addr = srv.accept()
        with conn:
            conn.recv(64)
            conn.sendall(b"+PONG\r\n")

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        monkeypatch.undo()   # the module-wide stub must not satisfy this test
        assert tl._ping_over_host(port, 2.0) is True
    finally:
        srv.close()
        worker.join(timeout=2)


def test_the_age_backstop_is_READ_not_an_inert_claim(tmp_path, monkeypatch):
    """#8180 review's property, preserved through the fix: an age constant that
    nothing reads advertises a bound the code does not keep, and on a
    DESTRUCTIVE sweep that is a silent lie. The pre-fix code therefore carried no
    constant at all — and, as a result, a container whose lane was gone but whose
    worktree survived was reaped by NOTHING (measured: 68 on cell-1, oldest 4
    days, load 639.88 on 8 cores, every lane on the box stopped working).

    The gap is measured by `REAP_STATE_DIR`'s stamp, so the bound is proven by
    moving the STAMP, not the container's age. Mutation: delete either comparison
    and this fails."""
    owner = tmp_path / "worktree-of-a-dead-lane"
    owner.mkdir()
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-deadlane\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"deadlane": str(owner)})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "OWNER_GRACE_H", 2.0)
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 3 * 3600)
    # The bound is proven on the ARMED path: removing this container is an
    # operator opt-in (#10693 review F2), so the removal case sets it. The
    # default keeps it and reports (test_arm_2_defaults_to_REPORT_ONLY).
    monkeypatch.setenv(tl.LANE_GONE_REAP_ENV, "1")

    # 1 h of the gap, grace 2 h -> KEPT. Proves OWNER_GRACE_H gates the removal.
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 1.0)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "grace has passed" in "\n".join(out), out

    # 3 h of the gap -> REMOVED. The bound is read, so crossing it reaps.
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 3.0)
    out = tl.reap_orphans()
    assert removed == [("-f", "fdb-lane-deadlane")], (removed, out)


def test_a_LIVE_lane_between_steps_is_NOT_reaped(tmp_path, monkeypatch):
    """REGRESSION for review cycle 2's P0, and the whole point of the stamp.

    `start()` REUSES a running container, so `StartedAt` is hours old for a lane
    that has been up for hours. Measuring the grace against `StartedAt` meant a
    live lane was reaped at the FIRST instant it happened to be between steps (no
    process in the worktree, its client idle past `ACTIVE_CLIENT_IDLE_S`) — data
    loss for a live lane, the exact bug this arm exists to prevent.

    Mutation: compare against `_started_epoch` instead of the unheld stamp and
    this fails — a 5 h old container that has never been observed unheld is
    removed."""
    owner = tmp_path / "live-lane-worktree"
    owner.mkdir()
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-busylane\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"busylane": str(owner)})
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)  # quiet client
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())      # between steps
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 5 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: None)   # first sighting
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "has not yet been observed unused" in "\n".join(out), out


def test_holds_decides_whether_arm_2_may_delete(tmp_path):
    """`_holds` is the guard that decides a real `docker rm -f`, and it had no
    direct test at all. Mutation: an exact-path-only comparison breaks the
    subdirectory case; dropping the nested-worktree exclusion makes a plain repo
    checkout permanently un-held by every lane beneath it."""
    repo = tmp_path / "repo"
    lane = repo / ".worktrees" / "lane-a"
    sibling = repo / ".worktrees" / "lane-a-sibling"
    for d in (repo, lane, sibling):
        d.mkdir(parents=True)
    assert tl._holds(str(repo), str(repo)) is True
    assert tl._holds(str(repo / "tests"), str(repo)) is True
    assert tl._holds(str(lane), str(lane)) is True
    assert tl._holds(str(lane / "tools"), str(lane)) is True     # subdirectory
    assert tl._holds(str(lane), str(repo)) is False              # nested worktree
    assert tl._holds(str(sibling), str(lane)) is False           # sibling prefix
    assert tl._holds("/elsewhere", str(repo)) is False


def test_a_name_with_no_worktree_slug_is_REPORT_ONLY(monkeypatch):
    """Review cycle 1, P0: the ownerless arm's spare was exact slug equality, which
    is STRUCTURALLY unreachable for names this tool did not mint (`fdb-lane-tm9190`
    has no 10-hex slug, so no `/proc`-derived slug can ever equal it). The
    backstop then removed them, silently reversing #5084's report-only ruling for
    that class. #5084 stands: an owner this tool cannot NAME is not destroyed."""
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-tm9190\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: 0.0)   # ancient
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "carries no worktree slug" in "\n".join(out), out


def test_ORPHAN_MAX_AGE_H_gates_the_unowned_arm(tmp_path, monkeypatch):
    """The owner-INDETERMINATE half (#8180's Outcome: "an age backstop for
    containers whose owner can no longer be determined").

    Mutation: drop the `age_h < ORPHAN_MAX_AGE_H` comparison and this fails. The
    third case below is what makes that true (#10693 review F2): case 1 is PAST
    the age floor, so it pins the GAP gate and would stay green without the age
    comparison. Case 3 is the input only the age comparison can keep — a YOUNG
    container (1 h) with an ancient LEASE GAP (40 h), reachable because the two
    reads are independent (a rebuilt container keeps the name, or the lease file
    was carried over), so the floor is the only thing standing between it and a
    `rm -f`. Without the comparison that mutant reaps it."""
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, f"{LANE_FIXTURE_NAME}\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "ORPHAN_MAX_AGE_H", 24.0)
    # Removal is an operator opt-in (#10693 review F2); the default for this
    # population is the report, pinned by test_arm_3_defaults_to_REPORT_ONLY.
    monkeypatch.setenv(tl.OWNER_INDETERMINATE_REAP_ENV, "1")

    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 25 * 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 1.0)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    # Pin the REASON: inside the backstop the container is kept, naming
    # ORPHAN_MAX_AGE_H. Without this the guard could go inert unnoticed.
    assert "backstop has passed" in "\n".join(out), out

    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 25.0)
    out = tl.reap_orphans()
    assert removed == [("-f", LANE_FIXTURE_NAME)], (removed, out)

    # Case 3 — the AGE FLOOR, isolated from the gap gate. The container is 1 h
    # old (inside the 24 h backstop) while its lease gap is 40 h: only the
    # `age_h < ORPHAN_MAX_AGE_H` comparison keeps it. Delete that comparison and
    # this case reaps, so the mutation claim above is now true of the comparison
    # itself, not merely of the constant.
    removed.clear()
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: time.time() - 3600)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n, **k: 40.0)
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    joined = "\n".join(out)
    assert "ownership is NOT determined" in joined, out
    assert "backstop is 24h" in joined, out


def test_a_live_cross_clone_lane_spares_its_container_by_SLUG(tmp_path, monkeypatch):
    """The git blind spot, closed by /proc. A container owned by ANOTHER clone on
    the box (measured: `/tmp/cell-t3/.worktrees/...`) is absent from THIS clone's
    `git worktree list`, so the pre-fix code could not attribute it and removed
    nothing forever. `_live_lane_paths` names the worktree from /proc regardless
    of clone, and the SLUG match is what spares it.

    Mutation: use `_worktree_slugs` (the git list) instead of the /proc-derived
    slugs for the liveness check, and this container is reaped — the live-lane
    kill this test exists to prevent."""
    wt = tmp_path / ".worktrees" / "cross-clone-lane"
    wt.mkdir(parents=True)
    slug = tl.slug_for(wt)
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, f"fdb-lane-{slug}\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[1:])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})   # git cannot see it
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: {str(wt)})
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: 0.0)  # ancient
    out = tl.reap_orphans()
    assert removed == [], (removed, out)
    assert "LIVE process elsewhere" in "\n".join(out), out


def test_wait_ready_does_not_overshoot_its_budget_when_the_host_probe_black_holes(
        lane, monkeypatch):
    """A host port that BLACK-HOLES (filtered/DROP) makes the probe consume its
    whole timeout. `remaining` was captured once per iteration and then reused
    for the exec call, so one iteration could spend 2x READY_TIMEOUT (measured
    6.0s against READY_TIMEOUT=3). The probe's slice must be charged to the
    budget, not added on top of it."""
    now = [100.0]
    monkeypatch.setattr(tl, "READY_TIMEOUT", 3)
    monkeypatch.setattr(tl.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(tl.time, "sleep", lambda _s: None)

    def black_hole(_port, timeout):
        now[0] += timeout          # the probe burns its entire slice
        return False

    seen: list = []

    def fake(*_a, **k):
        seen.append(k.get("timeout"))
        now[0] += k.get("timeout") or 0    # a docker call blocks for its timeout
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_ping_over_host", black_hole)
    monkeypatch.setattr(tl, "_docker", fake)
    assert tl._wait_ready("fdb-lane-x", 16390) is False
    # deadline was 100.0 + 3.0 = 103.0; the re-derived remaining is 0 after the
    # probe, so the exec call must not have been issued with a stale 3s slice.
    assert now[0] <= 103.05, f"_wait_ready overshot its 3s budget: {now[0]}"
    assert seen == [], f"the exec probe was issued past the deadline: {seen}"


def test_proof_1_SPARES_a_container_a_live_client_is_using(monkeypatch):
    """#10806 review: "the owning worktree is gone" is a fact about a DIRECTORY.

    Proof 1 fired without ever asking the container, so a lane whose worktree was
    reaped out from under it — or a cross-clone reader — had its live database
    deleted on the 15-minute clock. `_container_in_use` is the one liveness datum
    that does not need to know the owner, and `None` (unreadable) must spare too:
    unreadable is not "unused".

    ⛔ The PROBE is this test's subject and it is unchanged. The REMOVAL rung below
    it is not: #10806 landed this probe on `origin/main` while the fold landed
    `0abaa51a5` ("the ownership label needs USE proof"), which widened the
    owner-gone arm's second proof to both owner sources — docker's own TERMINAL
    state, OR no attached client ACROSS `OWNER_GRACE_H`. The two never saw each
    other, and a bare "answered `False`" is no longer sufficient proof on its own,
    because a lane between two steps answers exactly that. So the probe assertions
    are kept verbatim and the removal rung now proves BOTH halves: first sighting
    keeps, a full client-free grace removes.
    """
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-lively\trunning\n", "")
        if a[0] == "rm":
            removed.append(a[2])
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"lively": "/gone/wt"})
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: {"/somewhere/else"})
    for answer in (True, None):
        removed.clear()
        monkeypatch.setattr(tl, "_container_in_use", lambda _n, a=answer: a)
        out = "\n".join(tl.reap_orphans())
        assert "removed fdb-lane-lively" not in out, out
        assert removed == [], (
            f"a container reporting {answer!r} for its clients was removed")
    # …and with no client it IS removable, so the gate has not disabled proof 1 —
    # but only on the SECOND rung of the ladder the fold added (0abaa51a5): the
    # owner being gone is evidence about OWNERSHIP, never about USE.
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)

    # No client, but nothing observed unused yet: a lane between steps.
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: None)
    out = "\n".join(tl.reap_orphans())
    assert removed == [], out
    assert "first sighting" in out, out

    # …and now BOTH proofs hold: owner gone AND no client across the full grace.
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: tl.OWNER_GRACE_H + 1.0)
    out = "\n".join(tl.reap_orphans())
    assert "removed fdb-lane-lively" in out, out
    assert removed == ["fdb-lane-lively"], removed


def test_start_sweeps_orphans_and_reports_them_on_stderr(
        tmp_path, lane, monkeypatch, capsys):
    """The call site that makes ownership reaping BITE (#8180). Every reaper
    test calls `reap_orphans()` directly and the module-wide fixture sets
    `TORTOISE_TEST_LANE_REAP=0`, so deleting the call from `start()` left the
    suite green and the whole mechanism dead — the predicate is not the fix.
    This enables the sweep and asserts the removal HAPPENED, and that its report
    goes to STDERR: stdout is the `eval`-able export line, so a reap line there
    would corrupt a lane's target instead of aborting it."""
    dead = tmp_path / "dead-worktree"
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "1")
    # `start()` is stubbed, so state its CREATE/REUSE verdict explicitly: the exit
    # hook only removes a container this `run` created.
    monkeypatch.setattr(tl, "_LAST_START_CREATED", True)
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {"dead": str(dead)})
    # The client probe is its own gate (#10806) with its own test below.
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, "fdb-lane-dead\texited\n", "")
        if a[0] == "run":
            return _R(0, "deadbeef\n", "")
        if a[0] == "port":
            return _R(0, "0.0.0.0:16390\n", "")
        if a[0] == "exec":
            return _R(0, "PONG\n", "")
        if a[0] == "rm":
            removed.append(a)
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    tl.start(port=16390)
    assert ("rm", "-f", "fdb-lane-dead") in removed, removed
    out, err = capsys.readouterr()
    assert "removed fdb-lane-dead" in err, err
    assert "reap:" not in out, out


# ── the ownership guard (the part that must never be wrong) ────────────────

@pytest.mark.parametrize("name", [
    LANE_FIXTURE_NAME,
    "fdb-lane-abcdef",
])
def test_is_managed_accepts_this_tool_s_containers(name):
    assert tl.is_managed(name) is True


@pytest.mark.parametrize("name", [
    "falkordb",            # the shared dev/test instance (127.0.0.1:6379)
    "falkordb-16379",      # the orchestration graph instance
    "fdb-6599",            # another lane's private container (not ours)
    "w6213-fdb",           # ditto
    "fdb-5084-e",          # ditto
    "myfdb-lane-x",        # the prefix must start the name
    "fdb-lane",            # no slug
    "",                    # never treat the empty name as managed
])
def test_is_managed_refuses_every_other_container(name):
    assert tl.is_managed(name) is False


def test_a_protected_name_sharing_the_prefix_is_still_refused(monkeypatch):
    """`PROTECTED_NAMES` must be load-bearing, not decoration: if a shared
    instance ever acquired the `fdb-lane-` prefix, the guard still has to
    refuse it."""
    monkeypatch.setattr(tl, "PROTECTED_NAMES", tl.PROTECTED_NAMES | {"fdb-lane-shared"})
    assert tl.is_managed("fdb-lane-shared") is False
    assert tl.is_managed("fdb-lane-other") is True


def test_the_protected_names_are_the_shared_instances_this_lane_must_never_touch():
    """`PROTECTED_NAMES` documents the shared instances; today it is the PREFIX
    rule that refuses them, which is why the discriminating test is the
    prefix-collision one above (this one pins the constant's contents, and that
    the refusal holds however it is achieved)."""
    for shared in ("falkordb", "falkordb-16379", "fdb-6599", "w6213-fdb"):
        assert shared in tl.PROTECTED_NAMES
        assert tl.is_managed(shared) is False


def test_stop_refuses_an_unmanaged_lane_name_and_never_calls_docker(monkeypatch):
    """The guard must fire BEFORE any docker call — a refusal that had already
    inspected (or worse, removed) the container would defeat the point."""
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("stop() reached docker for a protected name")

    monkeypatch.setattr(tl, "lane_name", lambda: "falkordb")
    monkeypatch.setattr(tl, "container_state", _boom)
    with pytest.raises(SystemExit) as exc:
        tl.stop()
    assert "refusing to remove" in str(exc.value)


def test_stop_reports_absent_for_a_managed_container_that_is_not_running(lane, monkeypatch):
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    assert tl.stop() == "absent"


# ── no name is a parameter: the promise is structural, not argparse-deep ────

def test_no_function_parameter_can_select_a_container():
    """The CLI being locked is not enough: `stop(name)`/`start(slug)` accepted
    a peer's container name too (`is_managed` passes it — a peer's name IS
    `fdb-lane-<sha1(path)[:10]>`). The fix is structural: the target is
    derived inside the function, so the only parameter left is a property of
    THIS lane. Re-adding a name/slug/image parameter fails here."""
    assert list(inspect.signature(tl.stop).parameters) == []
    assert list(inspect.signature(tl.start).parameters) == ["port"]
    assert list(inspect.signature(tl.lane_name).parameters) == []


def test_no_flag_can_target_another_lanes_container():
    """Removing only `--name` left `--slug`, which still mapped to
    `fdb-lane-<peer>` (a peer's slug is a computable `sha1(path)[:10]`). Both
    overrides are gone, so neither spelling can name another lane's container."""
    for flag, value in (("--name", "fdb-lane-peer-slug"),
                        ("--slug", "peer-slug")):
        with pytest.raises(SystemExit) as exc:
            tl.main(["down", flag, value])
        assert exc.value.code == 2, f"argparse must reject {flag} (usage error)"


@pytest.mark.parametrize("argv", [
    ["down", "--port", "16390"],
    ["down", "--graph", "g"],
    ["list", "--port", "16390"],
    ["list", "--graph", "g"],
    ["status", "--port", "16390"],   # reads a port, never chooses one
])
def test_a_command_that_ignores_a_flag_must_not_accept_it(argv):
    """`down --port 16390` exited 0, which reads as "down that port". A command
    that does not use a flag must reject it loudly. (`status --graph` IS used,
    and is asserted accepted below.)"""
    with pytest.raises(SystemExit) as exc:
        tl.main(argv)
    assert exc.value.code == 2


def test_status_still_accepts_the_flag_it_does_use(monkeypatch, capsys):
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 1)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    assert tl.main(["status", "--graph", "other_graph"]) == 0
    _out, err = capsys.readouterr()
    assert "other_graph" in err


def test_lane_name_is_derived_from_this_worktree(monkeypatch):
    monkeypatch.setattr(tl, "repo_root", lambda: "/tmp/wt-under-test")
    assert tl.lane_name() == tl.container_name(tl.slug_for("/tmp/wt-under-test"))


def test_repo_root_ignores_an_inherited_git_work_tree(tmp_path, monkeypatch):
    """`rev-parse --show-toplevel` honours `GIT_WORK_TREE`, so an inherited env
    var silently retargets the tool at a peer worktree's container. The scrub is
    the fix, and this pins it.

    The decoy is a REAL repository: a non-existent path makes git fail and fall
    back to the CWD, which would let the unscrubbed code pass this test.
    """
    decoy = tmp_path / "decoy-worktree"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True)
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    assert tl.repo_root().resolve() == ROOT.resolve()
    assert tl.lane_name() == tl.container_name(tl.slug_for(ROOT))


@pytest.mark.parametrize("retarget", ["work_tree", "common_dir"])
def test_worktree_slugs_reads_this_repo_not_an_inherited_git_env(
        tmp_path, monkeypatch, retarget):
    """`_worktree_slugs` is the ownership record a delete is AUTHORIZED from, and
    it shells out to `git worktree list` — the same hazard `repo_root()` scrubs
    the ambient `GIT_*` for. Unscrubbed, an inherited git env var makes the
    reaper read a PEER clone's worktrees, so every real `fdb-lane-*` container
    resolves to `owner is None` (silent no-op) and a foreign record can
    authorize a removal this worktree never established (#8180 review).

    Both redirections are pinned: `GIT_WORK_TREE`/`GIT_DIR` together, and
    `GIT_COMMON_DIR` ALONE — the hole an enumerated two-variable scrub left
    open one variable over (measured: `GIT_COMMON_DIR` retargets the read on its
    own, with no `GIT_DIR` set).

    The decoy is a REAL repository (a non-existent path makes git fail and
    `_worktree_slugs` return None, which would let the unscrubbed code pass).
    """
    decoy = tmp_path / f"decoy-{retarget}"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True)
    if retarget == "common_dir":
        monkeypatch.setenv("GIT_COMMON_DIR", str(decoy / ".git"))
    else:
        monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
        monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    slugs = tl._worktree_slugs()
    assert slugs is not None
    assert tl.slug_for(ROOT) in slugs, (
        f"this worktree must be in the ownership map; got {len(slugs)} entries")
    assert tl.slug_for(decoy) not in slugs, (
        "the decoy repository's worktree must NOT appear in the ownership map")


# ── naming / URI shape ─────────────────────────────────────────────────────

def test_slug_is_deterministic_and_worktree_specific(tmp_path):
    a, b = tmp_path / "wt-a", tmp_path / "wt-b"
    a.mkdir()
    b.mkdir()
    assert tl.slug_for(a) == tl.slug_for(a), "same worktree must reuse ONE container"
    assert tl.slug_for(a) != tl.slug_for(b)
    assert len(tl.slug_for(a)) == 10


def test_container_name_is_prefixed_and_derived_from_the_slug():
    assert tl.container_name("0123456789") == "fdb-lane-0123456789"
    assert tl.is_managed(tl.container_name("0123456789"))


def test_uri_matches_the_shape_the_docker_lane_expects():
    """The lane parses `docker://` URIs, so the shape is load-bearing."""
    assert tl.uri_for(16399) == (
        "docker://:@127.0.0.1:16399/tortoise_test_matrix"
    )
    assert tl.uri_for(16399, "some_graph") == (
        "docker://:@127.0.0.1:16399/some_graph"
    )


@pytest.mark.parametrize("bad", [0, -1, 65536, "16399"])
def test_uri_for_refuses_a_port_that_is_not_a_port(bad):
    """`uri_for` builds the eval-ed string, so its contract must be as strict as
    the CLI's (`cmd_up` range-checks `--port`; a library caller does not)."""
    with pytest.raises(SystemExit) as exc:
        tl.uri_for(bad)
    assert "not a valid TCP port" in str(exc.value)


# ── docker-state classification (tested directly — see the module docstring) ─

@pytest.mark.parametrize("rc,stdout,stderr,expected", [
    (0, "running\n", "", "running"),
    (0, "exited\n", "", "exited"),
    (0, "", "", "unknown"),                      # no status is not a status
    (1, "", "Error: No such object: fdb-lane-x", "absent"),
    (1, "", "Error: No such container: fdb-lane-x", "absent"),
    (1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
     "unknown"),                                 # daemon down != no container
    (1, "", "permission denied while trying to connect", "unknown"),
    (1, "", "error: context \"ops\" not found", "unknown"),   # a broken
    (1, "", 'error during connect: dial tcp: lookup host: no such host',
     "unknown"),
    (1, "", "open /etc/docker/ca.pem: no such file or directory", "unknown"),
    # context is not an absent container, and neither is a DNS or TLS failure:
    # only Docker's "no such container"/"no such object" phrasings are read as
    # absence, so anything unrecognised fails CLOSED to `unknown`.
])
def test_container_state_distinguishes_absent_from_unknown(monkeypatch, rc, stdout, stderr, expected):
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(rc, stdout, stderr))
    assert tl.container_state("fdb-lane-x") == expected


def test_graph_count_returns_none_when_docker_cannot_be_asked(monkeypatch):
    """`graphs=0` is a claim about the container; a failed exec is not evidence
    for it. `status`/`up` must print `unknown` instead."""
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "boom"))
    assert tl._graph_count("fdb-lane-x") is None
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "g1\ng2\ng3\n", ""))
    assert tl._graph_count("fdb-lane-x") == 3


def test_published_scan_asks_for_all_containers(monkeypatch):
    """The scan must ask for the full list (`-a`/`--all`): a container this tool
    did not start is still an owner of the port, and `-a` is NOT a hedge — a
    STOPPED container keeps its host port reserved by docker while that port is
    free at the OS level, so this table is the ONLY thing that refuses it.
    Measured 2026-10-20 (Docker 29.8.2): `docker ps -a --format '{{.Names}}\t
    {{.State}}\t{{.Ports}}'` reports `fdb-7846-rdb  exited
    3000/tcp, 127.0.0.1:16455->6379/tcp` while `bind("127.0.0.1", 16455)`
    succeeds — which is how 67 of the range's 110 ports came to be held by
    EXITED containers. (Reading `-a` as "running only" is what would make that
    measurement look impossible.)"""
    seen = {}

    def _fake(*args, **kwargs):
        seen["args"] = args
        # The second line is a STOPPED container, in docker's real shape: if this
        # table were read as "running only", 16455 would look free (it IS free at
        # the OS level) and `docker run -p 16455` would then refuse it.
        return _R(0, "fdb-lane-x  127.0.0.1:16390->6379/tcp\n"
                    "fdb-lane-exited  exited  127.0.0.1:16455->6379/tcp", "")

    monkeypatch.setattr(tl, "_docker", _fake)
    assert tl._container_publishes(16390) is True
    assert set(seen["args"]) & {"-a", "--all"}, "must not scan running-only"
    assert tl._container_publishes(16455) is True, (
        "a stopped container's mapping must still refuse its port")
    assert tl._container_publishes(16490) is False


def test_published_port_reads_the_mapping_docker_reports(monkeypatch):
    """`_published_port` produces the port in the eval-ed URI, so its real body
    is exercised directly rather than stubbed at every call site. The first
    element of the result says whether docker ANSWERED at all — a failed query
    and "no mapping" are different facts, and only the second licenses treating
    a container as unaddressable."""
    cases = [
        (_R(0, "127.0.0.1:16390\n", ""), (True, 16390)),
        (_R(0, "[::1]:16391\n", ""), (True, 16391)),    # IPv6, same suffix parse
        (_R(0, "", ""), (True, None)),                  # answered: no mapping
        (_R(1, "", "No such container"), (False, None)),  # could not ask
        (_R(1, "", "docker port timed out after 60s"), (False, None)),
    ]
    for result, expected in cases:
        monkeypatch.setattr(tl, "_docker", lambda *a, _r=result, **k: _r)
        assert tl._published_port("fdb-lane-x") == expected


def test_docker_calls_time_out_instead_of_hanging(monkeypatch):
    """`subprocess.run` without a timeout can block forever, and a wedged daemon
    is this tool's expected failure mode. A timeout must come back as the same
    non-zero shape every caller already handles."""
    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1)

    monkeypatch.setattr(tl.subprocess, "run", _timeout)
    r = tl._docker("inspect", "fdb-lane-x")
    assert r.returncode == 1
    assert "timed out" in r.stderr
    # ... and the callers read that as "unknown", never as "absent".
    assert tl.container_state("fdb-lane-x") == "unknown"


def test_docker_absence_is_not_inferred_from_a_missing_binary(monkeypatch):
    """The `FileNotFoundError` handler used to forward `str(exc)`, which for the
    REAL shape is "[Errno 2] No such file or directory: 'docker'" — its
    "no such" made `container_state` answer `absent`, so `down` exited 0 and
    reported no container while the lane's container was still running. The stub
    raises the real three-argument shape on purpose: a
    `FileNotFoundError("docker")` has no "no such" in it and would let the
    broken code pass."""
    def _missing(*_a, **_k):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    monkeypatch.setattr(tl.subprocess, "run", _missing)
    r = tl._docker("inspect", "fdb-lane-x")
    assert r.returncode != 0
    # The INVARIANT is the classification, not the wording of the synthetic
    # stderr: with the absence matcher narrowed to Docker's own
    # "no such container/object" phrasings, even forwarding the raw errno text
    # ("[Errno 2] No such file or directory: 'docker'") no longer misclassifies.
    # The matcher itself is pinned by the "no such host"/"no such file" cases in
    # test_container_state_distinguishes_absent_from_unknown.
    assert tl.container_state("fdb-lane-x") == "unknown"


def test_an_unrunnable_docker_binary_also_fails_closed(monkeypatch):
    """`OSError` is broader than `FileNotFoundError`: a docker that exists but
    cannot be executed (permissions) must not escape as a traceback either."""
    def _denied(*_a, **_k):
        raise PermissionError(13, "Permission denied", "docker")

    monkeypatch.setattr(tl.subprocess, "run", _denied)
    r = tl._docker("ps")
    assert r.returncode != 0 and "could not run docker" in r.stderr
    assert tl.container_state("fdb-lane-x") == "unknown"


def test_git_failure_is_not_read_as_a_worktree(monkeypatch, capsys):
    """`repo_root` was the last unbounded `subprocess.run` on the hot path — a
    hung or missing git must fall back, not hang the lane."""
    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(tl.subprocess, "run", _timeout)
    assert tl.repo_root() == Path.cwd()
    assert "did not answer" in capsys.readouterr().err


def test_the_git_fallback_message_names_the_actual_cause(monkeypatch, capsys):
    """A guard's text must be followable at the moment it trips: "did not answer
    within 30s" is wrong (and misleading) when git is simply not installed."""
    def _missing(*_a, **_k):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(tl.subprocess, "run", _missing)
    assert tl.repo_root() == Path.cwd()
    err = capsys.readouterr().err
    assert "not available on PATH" in err
    assert "did not answer" not in err


def test_wait_ready_derives_each_call_timeout_from_the_remaining_budget(lane, monkeypatch):
    """The clock is driven explicitly, so the remaining budget is a KNOWN value
    at each docker call: 2.9 s (=> timeout 2) and then 0.4 s (=> timeout 1).
    `seen == [2, 1]` admits ONLY a derived timeout — a constant 1 would give
    `[1, 1]`, and a per-call `timeout=READY_TIMEOUT` would give `[3, 3]`.
    """
    clock = iter([100.0, 100.1, 100.1, 102.6, 102.6, 105.2])
    seen = []

    def _never(*_a, **kwargs):
        seen.append(kwargs.get("timeout"))
        return _R(0, "", "")

    monkeypatch.setattr(tl, "READY_TIMEOUT", 3)
    monkeypatch.setattr(tl.time, "monotonic", lambda: next(clock, 999.0))
    monkeypatch.setattr(tl.time, "sleep", lambda _s: None)
    monkeypatch.setattr(tl, "_docker", _never)
    assert tl._wait_ready("fdb-lane-x") is False
    assert seen == [2, 1], f"expected the 2.9s then 0.4s budgets, got {seen}"


def test_list_reports_a_failed_docker_ps_instead_of_claiming_none_exist(monkeypatch, capsys):
    """A failed `docker ps` yields empty stdout, which must NOT be reported as
    "no fdb-lane-* containers" — absence inferred from a failure, the exact
    inference the rest of this module refuses to make."""
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "daemon down"))
    assert tl.main(["list"]) == 1
    _out, err = capsys.readouterr()
    assert "docker ps failed" in err
    assert "no fdb-lane-* containers" not in err


# ── port selection ─────────────────────────────────────────────────────────

def test_pick_port_skips_ports_already_taken(monkeypatch):
    monkeypatch.setattr(tl, "port_is_free", lambda p: p != tl.PORT_RANGE[0])
    monkeypatch.setattr(tl, "_published_scan", lambda: "")
    monkeypatch.setattr(tl, "_container_publishes", lambda _p, _s=None: False)
    assert tl.pick_port(*tl.PORT_RANGE) == tl.PORT_RANGE[0] + 1


def test_pick_port_skips_ports_published_by_a_container(monkeypatch):
    """A port can be free on the HOST yet already taken by a FOREIGN container
    publishing on a non-loopback interface, so the host probe alone would hand out
    a port already in use. What `docker run -p` would then do is not what this pins."""
    monkeypatch.setattr(tl, "port_is_free", lambda _p: True)
    monkeypatch.setattr(tl, "_published_scan", lambda: "")
    monkeypatch.setattr(tl, "_container_publishes",
                        lambda p, _s=None: p == tl.PORT_RANGE[0])
    assert tl.pick_port(*tl.PORT_RANGE) == tl.PORT_RANGE[0] + 1


def test_pick_port_raises_when_the_range_is_exhausted(monkeypatch):
    monkeypatch.setattr(tl, "port_is_free", lambda _p: False)
    monkeypatch.setattr(tl, "_published_scan", lambda: "")
    monkeypatch.setattr(tl, "_container_publishes", lambda _p, _s=None: False)
    with pytest.raises(SystemExit) as exc:
        tl.pick_port(16390, 16392)
    assert "no free port" in str(exc.value)


def test_published_scan_is_none_when_docker_cannot_be_asked(monkeypatch):
    """`_published_scan` must not collapse "docker failed" into an empty table:
    the empty-table claim is exactly what fail-open is made of."""
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "daemon down"))
    assert tl._published_scan() is None


def test_container_publishes_fails_closed_when_it_takes_the_scan(monkeypatch):
    """The NO-SCAN path must not turn a failed `docker ps -a` into "not
    published". Leaving it permissive for callers that omit the table was the
    P1 fail-open still reachable by default."""
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "daemon down"))
    with pytest.raises(SystemExit) as exc:
        tl._container_publishes(16390)
    assert "refusing to guess" in str(exc.value)


def test_pick_port_takes_the_docker_scan_once(monkeypatch):
    """One `docker ps -a` per selection, not one per candidate: the range is 110
    ports wide, and a per-candidate scan is 110 docker invocations on a host
    this tool exists because it is overloaded."""
    calls = {"n": 0}

    def _scan():
        calls["n"] += 1
        return ""
    monkeypatch.setattr(tl, "port_is_free", lambda _p: True)
    monkeypatch.setattr(tl, "_published_scan", _scan)
    monkeypatch.setattr(tl, "_container_publishes",
                        lambda p, _s=None: p < tl.PORT_RANGE[0] + 5)
    assert tl.pick_port(16390, 16399) == 16395
    assert calls["n"] == 1, "the docker scan must be taken once per selection"


def test_repo_root_warns_and_falls_back_when_git_fails(monkeypatch, capsys):
    """A non-zero `rev-parse` is the SAME fact as a timeout (git could not
    answer), so it must warn rather than silently yield a cwd-derived slug."""
    monkeypatch.setattr(tl.subprocess, "run",
                        lambda *a, **k: _R(128, "", "fatal: not a git repository"))
    monkeypatch.chdir(tl.Path.cwd())
    root = tl.repo_root()
    _out, err = capsys.readouterr()
    assert root == tl.Path.cwd()
    assert "falling back to the current directory" in err


def test_pick_port_fails_closed_when_the_docker_scan_fails(monkeypatch):
    """A FAILED `docker ps -a` must NOT be read as "nothing is published".

    Ignoring the return code yields `False` for every port on a timed-out scan,
    and `pick_port` then returns a port `docker run -p` refuses."""
    monkeypatch.setattr(tl, "port_is_free", lambda _p: True)
    monkeypatch.setattr(tl, "_published_scan", lambda: None)
    with pytest.raises(SystemExit) as exc:
        tl.pick_port(16390, 16392)
    assert "docker ps -a" in str(exc.value)
    assert "refusing to guess" in str(exc.value)


def test_start_refuses_when_a_stale_container_cannot_be_removed(monkeypatch):
    """A failed `rm` on a stale container must surface as a REMOVAL failure —
    not as `docker run`'s "name already in use", which reads as a run problem
    rather than the removal problem it is."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    monkeypatch.setattr(tl, "_remove_and_describe",
                        lambda _n, **_k: "FAILED to remove it — check `docker ps -a`")
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "could not be removed" in str(exc.value)


def test_remove_and_describe_refuses_a_protected_name(monkeypatch):
    """`_remove_and_describe` takes an arbitrary name, so its guard has to be on
    the NAME — call-site discipline is not an invariant the suite can check."""
    def _must_not_run(*a, **k):          # a removal must not reach docker here
        raise AssertionError("_docker called for a protected name")
    monkeypatch.setattr(tl, "_docker", _must_not_run)
    with pytest.raises(SystemExit) as exc:
        tl._remove_and_describe("falkordb-16379")
    assert "refusing to remove" in str(exc.value)


def test_remove_and_describe_refuses_a_peer_lane_container(monkeypatch):
    """Ownership is an INVARIANT, not call-site discipline: even another lane's
    `fdb-lane-*` is not this worktree's to delete, however it is spelled."""
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-aaaaaaaaaa")

    def _must_not_run(*a, **k):
        raise AssertionError("_docker called for a peer lane's container")
    monkeypatch.setattr(tl, "_docker", _must_not_run)
    with pytest.raises(SystemExit) as exc:
        tl._remove_and_describe("fdb-lane-bbbbbbbbbb")
    assert "not ours to delete" in str(exc.value)


# ── the eval contract ──────────────────────────────────────────────────────

def test_uri_command_prints_only_the_export_line_on_stdout(monkeypatch, capsys):
    """The stdout of `uri` is exactly the eval-able export block — all four
    seam variables and nothing else (#5084 remainder: the port and password
    seams are what let a probe aimed at THIS container authenticate and
    address it, instead of falling back to the shared 6379; the legacy port is
    the same gap on the passwordless axis, and without it a `legacy_uri()`
    site dials the literal 16379 — measured `5 failed` in
    tests/test_directional_impl.py on main at e4703a286a)."""
    monkeypatch.setattr(tl, "start", lambda port=None, **k: ("fdb-lane-x", 16399))
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 0)
    rc = tl.main(["uri"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == (
        "export TORTOISE_DB_URI='docker://:@127.0.0.1:16399/tortoise_test_matrix'\n"
        "export TORTOISE_TEST_DOCKER_PORT='16399'\n"
        "export TORTOISE_TEST_DOCKER_PASSWORD=''\n"
        "export TORTOISE_TEST_LEGACY_PORT='16399'\n"
    )
    assert "fdb-lane-x" in err, "the human-readable diagnostic belongs on stderr"


def test_uri_command_says_unknown_rather_than_zero_graphs(monkeypatch, capsys):
    monkeypatch.setattr(tl, "start", lambda port=None, **k: ("fdb-lane-x", 16399))
    monkeypatch.setattr(tl, "_graph_count", lambda _n: None)
    assert tl.main(["uri"]) == 0
    _out, err = capsys.readouterr()
    assert "graphs=unknown" in err


def test_flags_are_accepted_after_the_subcommand(monkeypatch, capsys):
    """`uri --port N` is the natural spelling.

    The flags must be declared on the SUBcommand: declared on the root parser
    they die in argparse before reaching any guard.
    """
    monkeypatch.setattr(tl, "start", lambda port=None, **k: ("fdb-lane-x", port))
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 0)
    assert tl.main(["uri", "--port", "16401"]) == 0
    out, _err = capsys.readouterr()
    assert "127.0.0.1:16401" in out


def test_start_refuses_when_docker_state_is_unknown(monkeypatch):
    """A docker failure is not evidence that no container exists: guessing
    `absent` there would remove a container that may belong to another lane."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "unknown")

    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("docker was called despite an unknown state")

    monkeypatch.setattr(tl, "_docker", _boom)
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "cannot determine the state" in str(exc.value)


def test_an_invalid_graph_name_is_refused_before_a_container_exists(monkeypatch):
    """Ordering again: `uri --graph 'bad name'` must not leave a running
    container (and a claimed port) behind."""
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("start() ran before the graph name was validated")

    monkeypatch.setattr(tl, "start", _boom)
    with pytest.raises(SystemExit) as exc:
        tl.main(["uri", "--graph", "bad name"])
    assert "refusing graph name" in str(exc.value)


def test_status_reports_the_published_port(monkeypatch, capsys):
    """`status` must state the port the container actually publishes (the one a
    lane needs for its URI), never a placeholder."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 3)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    assert tl.main(["status"]) == 0
    _out, err = capsys.readouterr()
    assert "127.0.0.1:16390" in err
    assert ":0/" not in err, "a placeholder port must never be printed"


def test_status_fails_closed_when_it_cannot_ask(monkeypatch, capsys):
    """A script checking the exit code must be able to tell "could not ask"
    from an answer, and a failed `docker port` must not read as "no port"."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "unknown")
    assert tl.main(["status"]) == 1
    assert "unknown" in capsys.readouterr().err

    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 0)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (False, None))
    # A FAILED `docker port` is not an answer either: without this, a caller
    # keying the exit code reads "could not ask" as success.
    assert tl.main(["status"]) == 1
    err = capsys.readouterr().err
    assert "port=unknown" in err
    assert ":0/" not in err


def test_status_fails_closed_when_the_graph_count_fails(monkeypatch, capsys):
    """`status`'s whole job is the answer, so BOTH of its queries must fail
    closed — a failed `GRAPH.LIST` used to print `graphs=unknown` and exit 0."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_graph_count", lambda _n: None)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    assert tl.main(["status"]) == 1
    assert "graphs=unknown" in capsys.readouterr().err


def test_status_fails_when_a_running_container_publishes_no_port(monkeypatch, capsys):
    """`start()` calls this identical state a hard error; `status` must not call
    it success, and it cannot report a URI it does not have."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 0)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, None))
    assert tl.main(["status"]) == 1
    assert "port=none" in capsys.readouterr().err


def test_start_reuses_a_running_container_without_removing_it(lane, monkeypatch):
    """The reuse path must RETURN, not fall through: falling through would
    `docker rm -f` + recreate a live lane container, destroying the test graphs
    of the run that is using it — the opposite of what reuse is for."""
    # #8124: the reuse path now verifies the container's CREATED image
    # (`_created_image`) before returning it; pinned here so the test
    # exercises its own property rather than the engine check.
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    removed = []
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_wait_ready", lambda _n, _host_port=None: True)
    # #7357: the reuse path also READS the container's memory limit (and warns
    # when it has none). Pinned here so this test keeps exercising its own
    # property — "the reuse path never runs `docker run`" — instead of the read,
    # whose own behaviour is covered below.
    monkeypatch.setattr(tl, "_container_memory_limit", lambda _n: 2 << 30)
    # …and so do the TWO reads `_note_missing_dataset_ceiling` makes (the live
    # ceiling and the creation env). They must be stubbed too, or the `_boom`
    # below stops meaning what it says: that note wraps its whole body in
    # `except Exception`, so an `AssertionError` raised by `_boom` is caught and
    # the test stays green while the reuse path DOES reach docker (#7357 review,
    # round 6). The ceiling is stubbed as healthy, so the note returns early.
    monkeypatch.setattr(tl, "_container_live_maxmemory", lambda _n: 1536 << 20)
    monkeypatch.setattr(tl, "_container_redis_args", lambda _n: "")
    monkeypatch.setattr(tl, "_remove_and_describe",
                        lambda n, **_k: removed.append(n) or "removed it")

    # The reuse path now also records a LEASE (one `inspect` for the instance
    # id) so the reaper can tell a live lane from a leaked one. That read is
    # orthogonal to the property here, which is that the reuse path never runs
    # `docker run` and never removes the container.
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)

    def _boom(*_a, **_k):
        raise AssertionError("`docker run` must not be reached on the reuse path")
    monkeypatch.setattr(tl, "_docker", _boom)
    assert tl.start() == (LANE_FIXTURE_NAME, 16390)
    assert removed == [], "a running lane container must never be removed"


def test_created_image_reads_the_image_the_container_was_CREATED_from(monkeypatch):
    """The property itself, tested DIRECTLY — this file's rule for a function
    whose real body IS the safety property (`container_state`, `_graph_count`,
    `_published_port`). #8124: the reuse path must ask what the container was
    born from, and `{{.Config.Image}}` is the only field that answers it."""
    seen: list = []

    def fake(*a, **_k):
        seen.append(a)
        return _R(0, "falkordb/falkordb:latest\n", "")

    monkeypatch.setattr(tl, "_docker", fake)
    assert tl._created_image("fdb-lane-x") == "falkordb/falkordb:latest"
    assert seen and "{{.Config.Image}}" in seen[0], (
        f"the inspect must ask for Config.Image, got {seen}"
    )


def test_created_image_returns_None_when_docker_cannot_answer(monkeypatch):
    """None, never `""` and never a guessed pin: a failed query is not evidence
    about the container, so the caller must not read it as either "pinned" (reuse
    an unknown engine) or "not pinned" (destroy a live one)."""
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "daemon down"))
    assert tl._created_image("fdb-lane-x") is None
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "   \n", ""))
    assert tl._created_image("fdb-lane-x") is None


def test_start_refuses_to_reuse_a_running_container_born_from_a_floating_engine(
        lane, monkeypatch):
    """#8124 / #9085 review: the pin must reach an ALREADY-RUNNING container.

    `docker run` is the ONLY path that names an engine, and Docker never
    re-resolves an image for an existing container — so a lane container created
    before the pin, from the then-floating `falkordb/falkordb:latest`, keeps the
    panicking 6.0.x engine for life. The health gate cannot see it (6.0.x answers
    `PING`; only the aggregate+property projection kills it), so this container
    used to be handed to the lane as a "ready" DB that exits mid-run as
    `Connection closed by server` — the exact #8124 symptom. It must be REFUSED,
    and nothing destructive may touch it: recreating it would destroy the test
    graphs of the run that is using it.
    """
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("a running lane container must not be touched")

    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_created_image",
                        lambda _n: "falkordb/falkordb:latest")
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_wait_ready", lambda _n, _host_port=None: True)
    monkeypatch.setattr(tl, "_docker", _boom)
    monkeypatch.setattr(tl, "_remove_and_describe", _boom)

    with pytest.raises(SystemExit) as e:
        tl.start()
    msg = str(e.value)
    assert "#8124" in msg, msg
    assert "falkordb/falkordb:latest" in msg and tl.LANE_IMAGE_PIN in msg, (
        f"the refusal must name BOTH engines — the one it found and the pin: {msg}"
    )
    assert "down" in msg, (
        f"the refusal must say how to resolve it (recreate on the pin): {msg}"
    )


def test_start_refuses_to_reuse_a_running_container_of_unknown_engine(
        lane, monkeypatch):
    """A failed query is never read as permission — the rule `_published_port`
    and `container_state` already follow in this file.

    When the created image cannot be read, the pin is UNCONFIRMED: handing the
    container back would reuse an engine nobody verified (the #8124 symptom),
    and destroying it would delete a container whose provenance is unknown. So
    it refuses, names the failed query, and touches nothing.
    """
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("an unreadable container must not be touched")

    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_created_image", lambda _n: None)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_wait_ready", lambda _n, _host_port=None: True)
    monkeypatch.setattr(tl, "_docker", _boom)
    monkeypatch.setattr(tl, "_remove_and_describe", _boom)

    with pytest.raises(SystemExit) as e:
        tl.start()
    msg = str(e.value)
    assert "could not be read" in msg, msg
    assert "#8124" in msg and "down" in msg, msg


def test_uri_for_warns_when_docker_host_is_set(monkeypatch, capsys):
    """Every printed URI goes through `uri_for`, which is why the remote-daemon
    warning lives there and not in `pick_port` — a future direct print of a
    loopback URI would otherwise drop the warning silently."""
    monkeypatch.setenv("DOCKER_HOST", "ssh://build-host")
    uri = tl.uri_for(16390)
    _out, err = capsys.readouterr()
    assert "DOCKER_HOST is set" in err
    assert uri == "docker://:@127.0.0.1:16390/tortoise_test_matrix"


def test_start_refuses_a_port_it_cannot_move_a_live_lane_to(lane, monkeypatch):
    """Honour-or-refuse: a running container's published port cannot be changed,
    so an explicit `--port` that disagrees must FAIL — silently ignoring it would
    make the flag a lie, and recreating the container would destroy the graphs of
    the run using it."""
    # #8124: the reuse path now verifies the container's CREATED image
    # (`_created_image`) before returning it; pinned here so the test
    # exercises its own property rather than the engine check.
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))

    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("no docker call may touch a live lane container")
    monkeypatch.setattr(tl, "_docker", _boom)
    monkeypatch.setattr(tl, "_remove_and_describe", _boom)

    with pytest.raises(SystemExit) as e:
        tl.start(port=16391)
    assert "already running on port 16390" in str(e.value)


def test_start_accepts_an_explicit_port_that_matches(lane, monkeypatch):
    """The guard must be an EQUALITY, not a refusal of the flag: asking for the
    port the lane already publishes is not a conflict."""
    # #8124: the reuse path now verifies the container's CREATED image
    # (`_created_image`) before returning it; pinned here so the test
    # exercises its own property rather than the engine check.
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_wait_ready", lambda _n, _host_port=None: True)
    monkeypatch.setattr(tl, "_remove_and_describe",
                        lambda n, **_k: (_ for _ in ()).throw(
                            AssertionError("must not remove a live lane")))
    assert tl.start(port=16390) == (LANE_FIXTURE_NAME, 16390)


def test_the_documented_shell_contract_aborts_on_failure():
    """The USAGE block is the entry point every lane copies, and the one-line form
    `eval "$(...)" || exit 1` CANNOT abort: a failure prints nothing on stdout and
    `eval ""` returns 0, so the shell runs on with a STALE ``TORTOISE_DB_URI``
    (commonly the shared lane) — the cross-lane contamination this tool exists to
    prevent. The documented TWO-step form must abort."""
    script = (
        'export TORTOISE_DB_URI="docker://:pw@127.0.0.1:6379/stale_shared_lane"\n'
        f'uri="$(DOCKER_HOST=tcp://127.0.0.1:1 {tl.sys.executable} {tl.__file__} uri)" || exit 1\n'
        'eval "$uri"\n'
        'echo REACHED\n'
    )
    r = tl.subprocess.run(["/bin/bash", "-c", script], capture_output=True,
                          text=True, timeout=120)
    assert r.returncode == 1, f"the documented contract must abort: {r}"
    assert r.stdout.strip() == "", f"nothing reaches stdout on failure: {r.stdout!r}"
    assert "REACHED" not in r.stdout + r.stderr, "the shell ran on with a stale URI"
    doc = tl.__doc__ or ""
    assert 'uri="$(uv run python tools/test_lane.py uri)" || exit 1' in doc, (
        "USAGE must document the two-step form: assigning first is what puts the "
        "tool's exit status on the `||`"
    )
    assert 'eval "$uri"' in doc, (
        "USAGE must document evaluating the captured value as its own step"
    )
    # AGENTS.md carries the SAME documented command and is the copy lanes actually
    # read, so pinning only the module docstring would leave that copy free to
    # regress on its own.
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert 'uri="$(uv run python tools/test_lane.py uri)" || exit 1' in agents, (
        "AGENTS.md must carry the two-step form too, not just the module docstring"
    )
    assert 'eval "$uri"' in agents, (
        "AGENTS.md must show evaluating the captured value as its own step"
    )


def test_docker_forwards_its_timeout_to_subprocess(monkeypatch):
    """Every docker call must stay BOUNDED — the module exists because a wedged
    daemon must not hang the lane. Nothing pinned the forwarding, so dropping
    `timeout=timeout` would make every call unbounded with the suite green."""
    seen = {}

    def _fake(cmd, **kwargs):
        seen.update(kwargs)
        return _R(0, "ok", "")
    monkeypatch.setattr(tl.subprocess, "run", _fake)
    tl._docker("ps")
    assert seen.get("timeout") == tl.DOCKER_TIMEOUT


def test_repo_root_bounds_the_git_call(monkeypatch):
    """A hung `git` must not hang the lane: the timeout forwarding IS the bound,
    and nothing pinned it either."""
    seen = {}

    def _fake(cmd, **kwargs):
        seen.update(kwargs)
        return _R(0, "/tmp/wt\n", "")
    monkeypatch.setattr(tl.subprocess, "run", _fake)
    tl.repo_root()
    assert seen.get("timeout") == tl.GIT_TIMEOUT


def test_repo_root_falls_back_when_git_cannot_be_executed(monkeypatch, capsys):
    """git present but unrunnable (PermissionError/ENOEXEC) is the SAME fact as
    absent. It used to escape as a traceback, contradicting the fallback the
    docstring documents (and which `_docker` already handles for its own calls)."""
    def _boom(*_a, **_k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(tl.subprocess, "run", _boom)
    root = tl.repo_root()
    _out, err = capsys.readouterr()
    assert root == tl.Path.cwd()
    assert "could not be run" in err


# ── start(): the port handed to the caller must be the PUBLISHED one ───────

def test_start_returns_the_published_port_not_the_requested_one(lane, monkeypatch):
    """The published host port is ephemeral and authoritative, so echoing the
    requested value would print a URI pointing at nothing."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "pick_port", lambda *a, **k: 16399)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16400))
    seen = []

    def _fake(*args, **kwargs):
        seen.append((args, kwargs))
        return _R(0, "PONG")
    monkeypatch.setattr(tl, "_docker", _fake)
    assert tl.start() == (LANE_FIXTURE_NAME, 16400)

    runs = [(argv, kw) for argv, kw in seen if argv[:2] == ("run", "-d")]
    assert runs, "`docker run` must have been invoked"
    argv, kwargs = runs[0]
    # The two fail-closed properties the module ADVERTISES, pinned to the argv
    # rather than to the prose: bind LOOPBACK only, and leave persistence OFF.
    # Without these, `-p 127.0.0.1:{port}:6379` could become `-p {port}:6379`
    # (all interfaces) with the whole suite still green.
    assert "127.0.0.1:16399:6379" in argv
    redis_args = next(a for a in argv if a.startswith("REDIS_ARGS="))
    assert "--appendonly no --save ''" in redis_args, (
        f"persistence must stay OFF on a throwaway lane: {redis_args}"
    )
    # Positional pins are not enough on their own: `docker run` must also keep
    # the image-pull budget. Dropping it re-bounds a cold pull to the 60 s
    # DOCKER_TIMEOUT, so a first run with no local image fails as
    # "docker run failed: timed out after 60s" while the suite stays green.
    assert kwargs.get("timeout") == tl.IMAGE_PULL_TIMEOUT


#: #8071: the canonical SHARED docker-lane container's engine args (eldato
#: ``operations/memory/docker-compose.yml``). Spelled out HERE rather than read
#: back from ``tl.LANE_FALKORDB_ARGS`` — re-reading the module's own constant
#: would pass for ANY value it was changed to, and parity with the shared
#: service is the property being pinned.
_CANONICAL_FALKORDB_ARGS = "THREAD_COUNT 8 TIMEOUT_MAX 30000"


def test_start_passes_the_canonical_engine_args(lane, monkeypatch):
    """#8071: the private lane is a DROP-IN for the shared instance, and a lane
    that is merely ISOLATED is not one — its engine args are the shared compose
    service's own value.

    MEASURED (``falkordb/falkordb:latest``, 2026-10-16, ``GRAPH.CONFIG GET``,
    same image started each way): with NO ``FALKORDB_ARGS`` the module's query
    ``TIMEOUT`` is 1000 ms and a ~3.9 s query dies ``Query timed out``; with the
    value pinned below ``TIMEOUT`` is 0 and the same query completes. So the
    missing flag made the private lane STRICTER than the instance it replaces —
    it refused queries the shared lane answered, which is what read as a
    "wedged" lane under load.

    ``FALKORDB_ARGS`` is asserted on the ARGV (the docker seam), not on the
    constant: the failure this guards is the flag not reaching ``docker run``.
    """
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "pick_port", lambda *a, **k: 16399)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16400))
    seen = []

    def _fake(*args, **kwargs):
        seen.append((args, kwargs))
        return _R(0, "PONG")
    monkeypatch.setattr(tl, "_docker", _fake)
    tl.start()

    runs = [argv for argv, _kw in seen if argv[:2] == ("run", "-d")]
    assert runs, "`docker run` must have been invoked"
    argv = runs[0]
    assert f"FALKORDB_ARGS={_CANONICAL_FALKORDB_ARGS}" in argv, argv
    # ... and the module must agree with the literal above, so a change to
    # either side fails (the shared value is not a free parameter).
    assert tl.LANE_FALKORDB_ARGS == _CANONICAL_FALKORDB_ARGS


def test_start_rejects_an_invalid_port_before_touching_docker(lane, monkeypatch):
    """The ordering IS the guard: validation must precede `container_state` (a
    docker call) and the stale-container `docker rm`, or an invalid value still
    reaches docker. Both seams are booby-trapped here, so this test fails if the
    check is ever moved below them again."""
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("docker was called before port validation")

    monkeypatch.setattr(tl, "container_state", _boom)
    monkeypatch.setattr(tl, "_docker", _boom)
    for bad in (0, -1, 65536):
        with pytest.raises(SystemExit) as exc:
            tl.start(port=bad)
        assert "not a valid TCP port" in str(exc.value)


def test_start_refuses_a_floating_image_before_touching_docker(lane, monkeypatch):
    """#8124: the image pin is validated FIRST, for the same ordering reason as
    the port — and it is a REFUSAL, not a warning.

    `falkordb/falkordb:latest` now resolves to engine 6.0.x, whose
    `reply_compact` panics the server on `OPTIONAL MATCH (s) RETURN count(s) AS
    n, s.harness AS h` and exits it: the lane then reports `Connection closed by
    server` instead of a test result, and on a shared instance it takes every
    peer lane down. The seam stays (a digest or an explicit version is fine) so
    this must fail on the floating value, not by forbidding the override.
    """
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("docker was called before image validation")

    monkeypatch.setattr(tl, "container_state", _boom)
    monkeypatch.setattr(tl, "_docker", _boom)
    monkeypatch.setattr(tl, "reap_orphans", _boom)
    monkeypatch.setattr(tl, "IMAGE", "falkordb/falkordb:latest")
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "#8124" in str(exc.value)
    assert "falkordb/falkordb:latest" in str(exc.value)
    # ... and the pinned value still passes the validator (the guard did not
    # become a ban on configuring the lane at all).
    assert tl.assert_pinned_image(tl.LANE_IMAGE_PIN) == tl.LANE_IMAGE_PIN


def test_start_removes_only_a_container_this_tool_manages(lane, monkeypatch):
    """The stale-container `rm` must satisfy the ownership guard (a real
    refusal, not an `assert` — `python -O` strips those).

    `_docker` is stubbed: with the guard reverted this test must fail as an
    ASSERTION, not by reaching a real `docker rm` in the operator's daemon.
    """
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("docker was reached for an unmanaged container")

    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    monkeypatch.setattr(tl, "is_managed", lambda _n, _host_port=None: False)
    monkeypatch.setattr(tl, "_docker", _boom)
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "refusing to remove unmanaged" in str(exc.value)


def test_start_refuses_to_reuse_a_running_container_that_is_wedged(lane, monkeypatch):
    """The reuse path must not hand back a URI without checking the server
    answered, or a wedged long-lived container would be reused indefinitely."""
    # #8124: the reuse path now verifies the container's CREATED image
    # (`_created_image`) before returning it; pinned here so the test
    # exercises its own property rather than the engine check.
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_wait_ready", lambda _n, _host_port=None: False)
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "never answered PING" in str(exc.value)


def test_a_failed_port_query_never_removes_a_container(lane, monkeypatch):
    """A failed QUERY is not evidence that a container is broken. On the reuse
    path the container may be perfectly healthy and is not ours to delete, so
    the tool must refuse instead of reaping it."""
    # #8124: the reuse path now verifies the container's CREATED image
    # (`_created_image`) before returning it; pinned here so the test
    # exercises its own property rather than the engine check.
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("a failed port query led to a removal")

    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_published_port", lambda _n: (False, None))
    monkeypatch.setattr(tl, "_remove_and_describe", _boom)
    monkeypatch.setattr(tl, "_docker", _boom)
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "could not determine which port" in str(exc.value)


def test_a_failed_port_query_on_a_fresh_container_says_so_and_cleans_up(lane, monkeypatch):
    """The other half: a container we JUST created and cannot address has
    nothing to hand back, so it is removed — and the message names the failed
    query rather than claiming it publishes no port."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "pick_port", lambda *a, **k: 16399)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (False, None))
    monkeypatch.setattr(tl, "_remove_and_describe",
                        lambda _n, **_k: "removed it")
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "PONG"))
    with pytest.raises(SystemExit) as exc:
        tl.start()
    msg = str(exc.value)
    assert "could not report its published port" in msg
    assert "publishes no port" not in msg
    assert "removed it" in msg


def test_wait_ready_is_bounded_in_wall_time(lane, monkeypatch):
    """A PING loop bounded in iterations is unbounded if each iteration can
    block; the bound is the deadline."""
    calls = []

    def _never(*_a, **_k):
        calls.append(1)
        return _R(0, "", "")

    monkeypatch.setattr(tl, "READY_TIMEOUT", 0)
    monkeypatch.setattr(tl, "_docker", _never)
    assert tl._wait_ready("fdb-lane-x") is False
    assert calls == [], "a zero deadline must not issue a single docker call"


def test_cleanup_reports_a_failed_removal_instead_of_claiming_success(lane, monkeypatch):
    """`_remove_and_describe`'s whole point is that the cleanup's result is READ
    — reverting it to an unverified "removed it" must not leave the suite green."""
    monkeypatch.setattr(tl, "is_managed", lambda _n, _host_port=None: True)
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "container busy"))
    assert "FAILED to remove it" in tl._remove_and_describe(LANE_FIXTURE_NAME)
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "", ""))
    assert tl._remove_and_describe(LANE_FIXTURE_NAME) == "removed it"


def test_cleanup_refuses_an_unmanaged_name(lane, monkeypatch):
    """Every removal intent passes the ownership check."""
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("cleanup reached docker for an unmanaged name")

    monkeypatch.setattr(tl, "_docker", _boom)
    with pytest.raises(SystemExit) as exc:
        tl._remove_and_describe("falkordb")
    assert "refusing to remove" in str(exc.value)


def test_stop_refuses_on_an_unknown_daemon_state(lane, monkeypatch):
    """`start` refused on `unknown` but `stop` went straight to `rm -f` — which
    pays two 60s timeouts to learn nothing on a wedged daemon."""
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("stop() reached docker with an unknown state")

    monkeypatch.setattr(tl, "container_state", lambda _n: "unknown")
    monkeypatch.setattr(tl, "_docker", _boom)
    with pytest.raises(SystemExit) as exc:
        tl.stop()
    assert "cannot determine the state" in str(exc.value)


def test_start_cleans_up_when_the_container_answers_but_publishes_nothing(lane, monkeypatch):
    """A container that cannot be addressed must not be left running."""
    calls = []

    def _fake(*args, **kwargs):
        calls.append(args)
        if args[0] == "exec":
            return _R(0, "PONG", "")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "pick_port", lambda *a, **k: 16399)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, None))
    monkeypatch.setattr(tl, "_docker", _fake)
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "it publishes no port" in str(exc.value)
    assert ("rm", "-f", LANE_FIXTURE_NAME) in calls


def test_a_DEGRADED_later_root_read_cannot_strand_a_half_started_container(
        monkeypatch, tmp_path):
    """`start()` reads `repo_root()` ONCE and THREADS it into its removal guards.

    The guard re-derived this worktree's container name from a FRESH
    `repo_root()`, and that read has a documented PER-CALL degraded mode
    (`Path.cwd()` on a git timeout — the overloaded hosts this tool exists for).
    A degraded read therefore REFUSED to clean up the container `start()` had
    just created, on exactly the two paths that exist to stop it holding a
    reserved port: the message names a peer lane that does not exist, and the
    container survives (#8180's leak, produced by the guard meant to prevent it).

    Mutation: drop `root=root` from the `_remove_and_describe(...)` calls in
    `start()` — the flipping root below then refuses the removal.
    """
    real, degraded = tmp_path / "real-root", tmp_path / "degraded-root"
    real.mkdir()
    degraded.mkdir()
    reads: list[Path] = []

    def flipping_root():
        reads.append(real if not reads else degraded)
        return reads[-1]

    calls: list = []

    def fake(*a, **k):
        calls.append(a)
        if a[0] == "exec":
            return _R(0, "PONG", "")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "repo_root", flipping_root)
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "pick_port", lambda *a, **k: 16399)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, None))
    monkeypatch.setattr(tl, "_wait_ready", lambda _n, _host_port=None: True)
    monkeypatch.setattr(tl, "_docker", fake)
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "it publishes no port" in str(exc.value)
    # The container THIS call created is removed by name, from the same read.
    assert ("rm", "-f", tl.container_name(tl.slug_for(real))) in calls, calls
    assert len(reads) == 1, reads


def test_the_orphan_sweep_uses_the_NAME_the_caller_already_derived(monkeypatch):
    """`reap_orphans` must take the caller's name (`own_name=`) instead of
    deriving its own.

    The sweep's "this is THIS worktree's container" test is what stops a lane
    deleting its own database. Deciding it from a SECOND `repo_root()` read meant
    a read that degraded inside one `start()` could make `mine` disagree with the
    container `start()` was about to create — the same divergence the label pair
    exists to prevent, one record over.

    This test pins the SWEEP's half: `lane_name` and `repo_root` both raise, so a
    sweep that re-derives reaches the boom. #10712 review P2: that is not the
    same claim as "`start()` passes it", which an earlier docstring also made — a
    mutation dropping `own_name=name` at the call site leaves this test green,
    because this test never enters `start()`. The forwarding half is pinned
    separately, by `test_start_threads_its_ONE_read_into_the_orphan_sweep`.
    """
    name = "fdb-lane-0123456789"

    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("the sweep re-derived this lane's identity")

    monkeypatch.setattr(tl, "lane_name", _boom)
    monkeypatch.setattr(tl, "repo_root", _boom)
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    monkeypatch.setattr(tl, "_docker",
                        lambda *a, **k: _R(0, f"{name}\trunning\t\t\n", ""))
    out = tl.reap_orphans(own_name=name)
    assert f"kept {name} — this is THIS worktree's container" in "\n".join(out), out


def test_start_threads_its_ONE_read_into_the_orphan_sweep(lane, monkeypatch):
    """`start()` must PASS the name it derived (`own_name=`) into the sweep.

    #10712 review P2: the sweep-side test above pins that `reap_orphans` USES the
    name it is given, but not that `start()` gives it one — dropping
    `own_name=name` at the call site left the whole file green, so the exact
    regression the change claims to close ("the sweep takes the caller's name so
    `mine` cannot diverge") was unprotected. This drives the real `start()` and
    captures what the sweep actually received.

    MUTATION: change the call site to `reap_orphans()` and this goes red with
    `{}` captured.
    """
    captured: dict = {}

    class _Stop(Exception):
        """Sentinel: stop `start()` the moment the sweep has been observed."""

    def _stop(_n):
        raise _Stop

    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "1")
    monkeypatch.setattr(tl, "reap_orphans", lambda **kw: captured.update(kw) or [])
    monkeypatch.setattr(tl, "container_state", _stop)
    with pytest.raises(_Stop):
        tl.start()
    assert captured.get("own_name") == lane, (
        "start() did not thread its ONE `_lane_identity()` read into the sweep, "
        f"so the sweep would re-derive it: {captured}")


def test_stop_surfaces_a_failed_removal(lane, monkeypatch):
    """`stop` must not return "removed" when `docker rm` failed."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "boom"))
    with pytest.raises(SystemExit) as exc:
        tl.stop()
    assert "failed to remove" in str(exc.value)


# ── the eval contract must not be injectable ───────────────────────────────

@pytest.mark.parametrize("bad", [
    "o'brien",            # would close the single-quoted export line
    "a b",
    "x; rm -rf /",
    "$(whoami)",
    "graf\nh",
    "",
])
def test_uri_for_refuses_a_graph_name_that_would_break_the_eval_line(bad):
    with pytest.raises(SystemExit) as exc:
        tl.uri_for(16399, bad)
    assert "refusing graph name" in str(exc.value)


# ── #7357: the KERNEL memory cap, and naming a memory kill ────────────────

def test_the_container_is_created_with_a_kernel_memory_cap(lane, monkeypatch):
    """#7357 pinned at the ARGV — the flag that was missing everywhere.

    `--maxmemory` is a REDIS-side limit; without `HostConfig.Memory` the
    container's real ceiling is the HOST's, and the host's ceiling is swap. So a
    runaway lane instance pages the whole box and the canonical graph instance is
    killed and restarted by its own policy — the fleet-wide outage #7357
    measured, which is why this is a LANE-tool guard and not a comment.
    """
    monkeypatch.delenv(tl.CONTAINER_MEMORY_ENV, raising=False)
    seen: list = []

    def fake(*a, **k):
        seen.append(a)
        if a[0] == "inspect":
            return _R(0, "absent\n", "")
        if a[0] == "run":
            return _R(0, "deadbeef\n", "")
        if a[0] == "port":
            return _R(0, "0.0.0.0:16390\n", "")
        return _R(0, "PONG\n", "")

    monkeypatch.setattr(tl, "_docker", fake)
    tl.start()
    run_args = next(c for c in seen if c[0] == "run")
    assert "--memory" in run_args, run_args
    cap = run_args[run_args.index("--memory") + 1]
    assert cap == tl.DEFAULT_CONTAINER_MEMORY, run_args
    assert run_args[run_args.index("--memory-swap") + 1] == cap, (
        f"swap must EQUAL the memory cap (docker's default is 2x, which lets "
        f"the container page the host instead of dying alone): {run_args}"
    )


def test_the_lane_also_sets_a_dataset_ceiling_below_its_kernel_cap(
        lane, monkeypatch):
    """#7357 — TWO bounds, ORDERED: Redis refuses FIRST, the kernel last.

    The cgroup cap alone converts a fleet-wide outage into one container dying
    alone — but it dies by `SIGKILL`: the container VANISHES mid-run, taking
    every in-flight read with it, and the caller is handed a bare transport
    error that says nothing about memory. That is #7357's own defect ("the
    failure surfaces as … which misdescribes it") reproduced by this tool's own
    defaults, and MEASURED on this box 2026-10-21: ten lane containers had been
    killed at exactly their cap (`OOMKilled=true ExitCode=137`) while ordinary
    lanes peak at 37-390 MiB (`/sys/fs/cgroup/memory.peak`).

    A `--maxmemory` BELOW the cap makes the same overshoot an ANSWERED refusal
    (`OOM command not allowed when used memory > 'maxmemory'`, an ordinary
    `ResponseError` that `retry.is_instance_unavailable` correctly refuses to
    read as an instance outage) with the instance still UP and its reason in
    the message. Pinned to the ARGV because a comment cannot enforce ordering.
    """
    monkeypatch.delenv(tl.CONTAINER_MEMORY_ENV, raising=False)
    seen: list = []

    def fake(*a, **k):
        seen.append(a)
        if a[0] == "inspect":
            return _R(0, "absent\n", "")
        if a[0] == "run":
            return _R(0, "deadbeef\n", "")
        if a[0] == "port":
            return _R(0, "0.0.0.0:16390\n", "")
        return _R(0, "PONG\n", "")

    monkeypatch.setattr(tl, "_docker", fake)
    tl.start()
    run_args = next(c for c in seen if c[0] == "run")
    # ⛔ EXACTLY ONE `-e REDIS_ARGS=`. `next()` would silently take the FIRST
    # while docker applies the LAST, so a second `REDIS_ARGS=` appended after the
    # builder (a "just add the policy here" edit) would leave every per-flag
    # assertion green and hand the container the *other* value (#7357 review).
    redis_entries = [a for a in run_args if a.startswith("REDIS_ARGS=")]
    assert len(redis_entries) == 1, (
        f"docker keeps only the LAST `-e REDIS_ARGS=`, so exactly one must be "
        f"passed: {redis_entries}"
    )
    redis_args = redis_entries[0]
    assert "--maxmemory " in redis_args, (
        f"the lane must carry a REDIS-side dataset ceiling, not only the "
        f"cgroup cap: {redis_args}"
    )
    ceiling = redis_args.split("--maxmemory ", 1)[1].split()[0]
    cap = run_args[run_args.index("--memory") + 1]
    cap_bytes = tl._spec_bytes(cap)          # a DOCKER spec
    ceiling_bytes = tl._redis_spec_bytes(ceiling)   # a REDIS spec — different grammar
    assert ceiling_bytes is not None and cap_bytes is not None, redis_args
    assert 0 < ceiling_bytes < cap_bytes, (
        f"the dataset ceiling must fire BEFORE the cgroup cap — at or above "
        f"it, `maxmemory` never fires, RSS reaches the cgroup limit and the "
        f"kernel SIGKILLs the container (ceiling={ceiling} cap={cap})"
    )
    # `noeviction` is pinned, not inherited: under an eviction policy Redis
    # silently DROPS graph keys to stay under the ceiling, which on a graph
    # instance is silent data loss — strictly worse than any refusal.
    assert "--maxmemory-policy noeviction" in redis_args, redis_args


def test_one_redis_args_value_carries_every_engine_pin(monkeypatch):
    """`REDIS_ARGS` is ONE `-e` value; docker does not merge a second one.

    A ceiling pin added as its own `-e REDIS_ARGS=…` would make docker keep the
    LAST value and silently drop persistence-off (or the ceiling, depending on
    order) — and a per-flag argv assertion stays green while the container gets
    one of them. PyTest-free on purpose: this is the builder's contract.
    """
    monkeypatch.delenv(tl.CONTAINER_MEMORY_ENV, raising=False)
    args = tl._redis_args()
    assert args.count("--maxmemory ") == 1, args
    assert "--appendonly no --save ''" in args, args
    assert "--maxmemory-policy noeviction" in args, args


def test_the_derived_ceiling_is_refused_before_any_container_is_removed(
        lane, monkeypatch, capsys):
    """#7357 review P1 — the F8 ordering defect, for the DERIVED seam.

    `container_dataset_ceiling` refuses a SUPERSET of what
    `container_memory_cap` accepts: any cap under ~1.33 MiB passes the cap
    grammar and cannot hold a strictly-smaller ceiling. While the ceiling was
    derived at the `docker run` argument tuple, `FALKORDB_CONTAINER_MEMORY=1m`
    passed the early validation and then raised AFTER the stale-container
    `docker rm -f` — destroying the lane's graphs and refusing to replace the
    container, which is verbatim the failure the F8 note in `start()` says is
    fixed. Every seam below the validation is booby-trapped, so this test fails
    if the derivation is ever moved back down to the call site.
    """
    monkeypatch.setenv(tl.CONTAINER_MEMORY_ENV, "1m")
    monkeypatch.setattr(tl, "reap_orphans", lambda *a, **k: [])
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")

    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("docker was called before ceiling validation")

    monkeypatch.setattr(tl, "_remove_and_describe", _boom)
    monkeypatch.setattr(tl, "_docker", _boom)
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert tl.CONTAINER_MEMORY_ENV in str(exc.value), str(exc.value)
    assert "test-lane:" in str(exc.value), str(exc.value)


def test_a_reused_container_with_no_dataset_ceiling_is_reported(
        monkeypatch, capsys):
    """#7357 review P2 — the reuse path is the ONLY route that keeps the defect.

    `docker start` cannot add a limit, and the kernel-cap note beside this one
    reads `HostConfig.Memory` only — so a container created by an earlier version
    of this tool (`Memory=2g`, `REDIS_ARGS` with no `--maxmemory`, measured as
    `fdb-lane-1555b57be7` on this box) is handed back SILENTLY, keeping the
    SIGKILL death for life. That is #7357's own defect ("the failure is not
    reported"), so the warning must name the container and the remedy.
    """
    monkeypatch.setenv(tl.CONTAINER_MEMORY_ENV, "2g")

    def fake(*a, **k):
        if a[0] == "inspect" and a[1] == "--format" and "Config.Env" in a[2]:
            return _R(0, '["REDIS_ARGS=--appendonly no --save \'\'"]\n', "")
        if a[0] == "inspect" and a[1] == "--format" and a[2] == tl._MEMORY_LIMIT_FORMAT:
            return _R(0, "2147483648\n", "")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef01")
    err = capsys.readouterr().err
    assert "fdb-lane-deadbeef01" in err, err
    assert "NO dataset ceiling" in err, err
    assert "--maxmemory" in err, err
    assert "down" in err, err


def test_a_reused_container_whose_ceiling_is_above_its_cap_is_reported(
        monkeypatch, capsys):
    """`maxmemory` at or ABOVE the cgroup cap never fires — same silence.

    A ceiling that cannot fire is not a ceiling: RSS reaches the cgroup limit
    first and the kernel SIGKILLs the container, which is the death the ordering
    exists to replace. Reported on the same reuse path rather than trusted.
    """
    def fake(*a, **k):
        if a[0] == "inspect" and a[1] == "--format" and "Config.Env" in a[2]:
            return _R(0, '["REDIS_ARGS=--appendonly no --maxmemory 4gb"]\n', "")
        return _R(0, "2147483648\n", "")

    monkeypatch.setattr(tl, "_docker", fake)
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef02")
    err = capsys.readouterr().err
    assert "at or ABOVE its own cgroup cap" in err, err


def test_a_reused_container_with_a_valid_ceiling_says_nothing(
        monkeypatch, capsys):
    """The warning must be ACTIONABLE, so a correctly bounded container is silent.

    A note that fires on a healthy container is noise, and noise is how the real
    one stops being read. This drives the HEALTHY comparison (`0 < live < cap`,
    the branch that returns silently on a live read) rather than an unreadable
    one: a stubbed `exec` that answers a single line made the note silent through
    the `cap_bytes is None` early return instead, so the docstring's property was
    not the one exercised (#7357 review, nit).
    """
    monkeypatch.setattr(tl, "_docker", _exec_fake("1610612736"))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef03")
    assert capsys.readouterr().err == ""


def test_the_live_ceiling_read_answers_None_when_the_reply_is_unusable(
        monkeypatch):
    """`None` is "could not read", never a ceiling (#7357).

    The property of `_container_live_maxmemory` itself, tested directly: the
    caller branches on `is not None`, so a failed exec, a truncated `--raw`
    reply or a non-numeric value must all be indistinguishable from "no answer"
    — and must never be read as "it is fine". These are also the branches the
    `# pragma: no cover` markers used to hide while a test DID reach one of them
    (#7357 review, nit).
    """
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "daemon down"))
    assert tl._container_live_maxmemory("fdb-lane-x") is None
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "", ""))
    assert tl._container_live_maxmemory("fdb-lane-x") is None
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "maxmemory\n", ""))
    assert tl._container_live_maxmemory("fdb-lane-x") is None
    monkeypatch.setattr(tl, "_docker",
                        lambda *a, **k: _R(0, "maxmemory\nnot-a-number\n", ""))
    assert tl._container_live_maxmemory("fdb-lane-x") is None
    # …and a real reply is the byte count, so the distinguishing half is pinned
    # too (an all-None function would satisfy every assertion above).
    monkeypatch.setattr(tl, "_docker",
                        lambda *a, **k: _R(0, "maxmemory\n1610612736\n", ""))
    assert tl._container_live_maxmemory("fdb-lane-x") == 1610612736


def _exec_fake(live_maxmemory: str, *, limit: str = "2147483648",
               env: str = '["REDIS_ARGS=--appendonly no --maxmemory 1536mb"]'):
    """A `_docker` stub: live CONFIG GET, the creation env, and the cap."""
    def fake(*a, **k):
        if a[0] == "exec":
            return _R(0, f"maxmemory\n{live_maxmemory}\n", "")
        if a[0] == "inspect" and a[1] == "--format":
            if "Config.Env" in a[2]:
                return _R(0, env + "\n", "")
            return _R(0, limit + "\n", "")
        return _R(0, "", "")
    return fake


def test_the_reuse_note_reads_the_LIVE_ceiling_not_the_creation_env(
        monkeypatch, capsys):
    """#7357 review P2 — a `CONFIG SET` moves the ceiling without touching env.

    The reuse path only ever asks about a container that is UP (it sits inside
    `if state == "running":` and the next call PINGs it), so the creation env is
    the wrong source: a runtime `CONFIG SET maxmemory 0` leaves `REDIS_ARGS`
    reading `--maxmemory 1536mb` while the instance is UNBOUNDED — exactly the
    container this note exists to catch, going silent.
    """
    monkeypatch.setattr(tl, "_docker", _exec_fake("0"))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef05")
    err = capsys.readouterr().err
    assert "NO dataset ceiling in effect" in err, err


def test_the_reuse_note_compares_against_the_containers_OWN_cap(
        monkeypatch, capsys):
    """#7357 review P2 — a container keeps the cap it was BORN with.

    A 4g-born container with a 3072mb ceiling is correctly bounded, but a lane
    now running the default 2g would have judged it "at or ABOVE its own cgroup
    cap" and printed a remedy of `down` — which DELETES that container's graphs.
    Lowering `DEFAULT_CONTAINER_MEMORY` later would have made that fire
    fleet-wide without anyone touching a container.
    """
    monkeypatch.setattr(tl, "_docker",
                        _exec_fake("3221225472", limit="4294967296"))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef06")
    assert capsys.readouterr().err == ""


def test_a_ceiling_spelled_the_way_REDIS_accepts_it_is_not_unreadable(
        monkeypatch, capsys):
    """#7357 review P2 — Redis's `memtoull` is case-insensitive, docker's is not.

    MEASURED on the pinned engine: `CONFIG SET maxmemory 1GB` is accepted.
    Parsing the creation env with a lowercase-only grammar reported a perfectly
    good `1GB` ceiling as "unreadable" and prescribed `down`.
    """
    # Live is unreadable (`0` is a legitimate value, so force the fallback path
    # by making `exec` fail) — the case under test is the ENV parser.
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: (
        _R(1, "", "no exec") if a[0] == "exec"
        else _R(0, '["REDIS_ARGS=--maxmemory 1GB"]\n', "")
        if "Config.Env" in a[2] else _R(0, "2147483648\n", "")))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef07")
    err = capsys.readouterr().err
    assert "unreadable" not in err, err
    assert err == "", err


def test_the_redis_spec_parser_maps_bare_units_decimally():
    """#7357 review round 5 (P2) — Redis's `memtoull`, pinned value by value.

    redis.conf §Memory units: the bare `k`/`m`/`g` are DECIMAL and the
    `b`-suffixed forms are binary, which is NOT docker's table (1024-based for
    both). MEASURED on the pinned engine: `CONFIG SET maxmemory 2g` then
    `CONFIG GET maxmemory` answers 2000000000, not 2147483648.
    """
    assert tl._redis_spec_bytes("2g") == 2_000_000_000
    assert tl._redis_spec_bytes("2048m") == 2_048_000_000
    assert tl._redis_spec_bytes("1g") == 1_000_000_000
    assert tl._redis_spec_bytes("1k") == 1_000
    assert tl._redis_spec_bytes("1gb") == 1 << 30
    assert tl._redis_spec_bytes("1536mb") == 1536 << 20
    assert tl._redis_spec_bytes("1GB") == 1 << 30       # case-insensitive
    assert tl._redis_spec_bytes("0") == 0               # NO LIMIT, not unreadable
    # `REDIS_ARGS` reaches Redis through a word split, so quotes are a plausible
    # spelling that Redis's own `sdssplitargs` strips.
    assert tl._redis_spec_bytes("'1536mb'") == 1536 << 20
    assert tl._redis_spec_bytes('"1gb"') == 1 << 30
    # What Redis REFUSES stays unreadable rather than being guessed at:
    # `memtoull` reads digits then a unit, so a fraction or an unknown unit (`t`,
    # `kib`) is an error to Redis too.
    for not_redis in ("1.5g", "+2g", "1t", "1kib", "2 gigs", ""):
        assert tl._redis_spec_bytes(not_redis) is None, not_redis
    # …and the two grammars really do disagree on the spelling that matters,
    # which is the whole reason the Redis table exists.
    assert tl._redis_spec_bytes("2g") != tl._spec_bytes("2g")


def test_a_reuse_ceiling_is_judged_with_REDIS_grammar_not_dockers(
        monkeypatch, capsys):
    """#7357 review round 5 (P2) — the grammars differ in UNITS, not only case.

    `--memory 2g --maxmemory 2g` is a CORRECTLY bounded pair: Redis reads the
    bare `g` as DECIMAL, so its 2,000,000,000 ceiling fires ~147 MiB BEFORE the
    2,147,483,648 cgroup cap. Judged with docker's 1024-based table the same
    token computes 2,147,483,648, the comparison flips (`>=`), and this note told
    the operator the ceiling "never fires" and to run `down` — which DELETES that
    healthy container's graphs.
    """
    def docker_grammar_trap(*a, **k):
        if a[0] == "exec":
            return _R(1, "", "redis wedged")   # force the creation-env fallback
        if a[0] == "inspect" and "Config.Env" in a[2]:
            return _R(0, '["REDIS_ARGS=--appendonly no --maxmemory 2g"]\n', "")
        return _R(0, "2147483648\n", "")        # the 2g cap docker actually set

    monkeypatch.setattr(tl, "_docker", docker_grammar_trap)
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef13")
    assert capsys.readouterr().err == ""


def test_an_env_ceiling_that_really_cannot_fire_is_still_reported(
        monkeypatch, capsys):
    """The unit fix must not swallow a genuinely unfireable ceiling.

    `3g` is 3,000,000,000 to Redis — still at or above a 2,147,483,648 cap, so
    it can never fire and the operator has to be told (the remedy `down` is
    destructive, which is why the warning may only fire on a broken container).
    """
    def over_cap(*a, **k):
        if a[0] == "exec":
            return _R(1, "", "redis wedged")
        if a[0] == "inspect" and "Config.Env" in a[2]:
            return _R(0, '["REDIS_ARGS=--maxmemory 3g"]\n', "")
        return _R(0, "2147483648\n", "")

    monkeypatch.setattr(tl, "_docker", over_cap)
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef17")
    err = capsys.readouterr().err
    assert "at or ABOVE its own cgroup cap" in err, err


def test_a_quoted_reuse_ceiling_is_read_not_called_unreadable(
        monkeypatch, capsys):
    """A quote is Redis's to strip, not a parser failure (#7357 review, round 5).

    `REDIS_ARGS` reaches Redis through a word split, so `--maxmemory '1536mb'`
    is a real ceiling that Redis's own `sdssplitargs` strips the quotes from. The
    note answered "has an unreadable `--maxmemory '1536mb'`" and prescribed
    `down`, which deletes that correctly bounded container's graphs.
    """
    def quoted(*a, **k):
        if a[0] == "exec":
            return _R(1, "", "redis wedged")
        if a[0] == "inspect" and "Config.Env" in a[2]:
            return _R(0, '["REDIS_ARGS=--maxmemory \'1536mb\'"]\n', "")
        return _R(0, "2147483648\n", "")

    monkeypatch.setattr(tl, "_docker", quoted)
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef14")
    err = capsys.readouterr().err
    assert "unreadable" not in err, err
    assert err == "", err


def test_the_start_reuse_path_reports_a_container_with_no_dataset_ceiling(
        lane, monkeypatch, capsys):
    """#7357 review round 6 P2 — the note's only PRODUCTION wiring.

    Every other test of `_note_missing_dataset_ceiling` calls it DIRECTLY, so the
    single line that reaches it in `start()` was unpinned: deleting it left the
    whole suite green while the reuse path went silent again — #7357's own defect
    ("the failure is not reported") restored with no failing test. The counter
    makes that deletion fail; `capsys` makes the warning's presence the property,
    not the call.
    """
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_wait_ready", lambda _n, _host_port=None: True)
    monkeypatch.setattr(tl, "_remove_and_describe",
                        lambda n, **_k: "removed it")
    # The container is 2g-born and UNBOUNDED — the measured shape of the
    # container this note exists for (`fdb-lane-1555b57be7`).
    monkeypatch.setattr(tl, "_container_memory_limit", lambda _n: 2 << 30)
    read: list[str] = []

    def unbounded(name):
        read.append(name)
        return 0                     # `CONFIG GET maxmemory` -> 0, NO LIMIT

    monkeypatch.setattr(tl, "_container_live_maxmemory", unbounded)
    monkeypatch.setattr(tl, "_container_redis_args", lambda _n: "")

    assert tl.start() == (LANE_FIXTURE_NAME, 16390)
    err = capsys.readouterr().err
    assert "NO dataset ceiling in effect" in err, err
    assert "down" in err, err
    assert read == [LANE_FIXTURE_NAME], (
        "start() must ASK the reused container for its live ceiling: without that "
        f"call the note is dead code on the only path that needs it, got {read}"
    )


def test_the_docker_spec_parser_reads_a_padded_spec():
    """The unit is sliced from the SAME normalised string the regex matched.

    Slicing the RAW spec made `" 2g"` match the grammar and then miss the unit
    table, so a readable spec was reported as "unreadable" — the same class of
    defect that prescribes `down` for a healthy container (#7357 review).
    """
    assert tl._spec_bytes(" 2g ") == 2 << 30
    assert tl._spec_bytes("2G") == 2 << 30
    assert tl._spec_bytes("2g") == 2 << 30
    # `_spec_is_positive` slices the RAW spec unless it normalises first — the
    # same defect one function over, harmless only because its one caller
    # pre-normalises (#7357 review, nit).
    assert tl._spec_is_positive(" 2g ") is True
    assert tl._spec_is_positive("2G") is True
    assert tl._spec_is_positive(" 0.0g ") is False


def test_an_unbounded_ceiling_does_not_invent_a_cgroup_cap_to_blame(
        monkeypatch, capsys):
    """`live < 1` used to end "SIGKILLed by the cgroup cap" unconditionally.

    When the SAME container has no cap (`HostConfig.Memory=0`, which
    `_note_uncapped_existing` reports beside this note) or the cap read failed,
    there is no cgroup limit for a SIGKILL to come from — the note asserted one
    anyway (#7357 review, round 5).
    """
    monkeypatch.setattr(tl, "_docker", _exec_fake("0", limit="0"))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef15")
    err = capsys.readouterr().err
    assert "NO dataset ceiling in effect" in err, err
    assert "SIGKILLed by the cgroup cap" not in err, err

    # …and where a cap WAS read, the claim is true and stays.
    monkeypatch.setattr(tl, "_docker", _exec_fake("0"))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef16")
    err = capsys.readouterr().err
    assert "SIGKILLed by the cgroup cap" in err, err


def test_an_unreadable_reuse_ceiling_is_named_rather_than_assumed(monkeypatch,
                                                                 capsys):
    """An unread fact must never be reported as a pass (#7357)."""
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "boom"))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef04")
    err = capsys.readouterr().err
    assert "could not READ the dataset ceiling" in err, err


def test_the_dataset_ceiling_seam_refuses_a_cap_it_cannot_stay_below():
    """Fail-closed on the two values that would silently restore the defect.

    A ceiling of ZERO is docker's/Redis's spelling for NO LIMIT — the unbounded
    dataset that reaches the cgroup cap and is SIGKILLed. A cap too small to
    hold a strictly-smaller ceiling cannot be ordered against at all, and
    CLAMPING it would report a bound the container does not have (the exact
    class of defect #9204's review found in the cap seam).
    """
    assert tl.container_dataset_ceiling("2g") == "1536mb"
    assert tl.container_dataset_ceiling("4g") == "3072mb"
    # Every cap it ACCEPTS must yield a ceiling strictly below that cap.
    for cap in ("2g", "4g", "1536m", "1g", "2048mb", "512m", "3m"):
        ceiling = tl.container_dataset_ceiling(cap)
        assert (0 < tl._spec_bytes(ceiling)
                < tl._spec_bytes(cap)), (cap, ceiling)
    for bad in ("1m", "0", "2 gigs"):
        with pytest.raises(SystemExit) as exc:
            tl.container_dataset_ceiling(bad)
        assert "test-lane:" in str(exc.value), (bad, str(exc.value))


def test_the_memory_cap_seam_refuses_a_malformed_or_zero_value(monkeypatch):
    """A dropped cap is the uncapped container this exists to prevent, so an
    unusable value is REFUSED with the variable named — never ignored.

    `0` is the case that matters most: it is VALID docker syntax meaning NO
    LIMIT, so a grammar-only check accepts the one value that reproduces #7357
    while every message claims the cap was validated (#7357 review).
    """
    monkeypatch.setenv(tl.CONTAINER_MEMORY_ENV, "1536m")
    assert tl.container_memory_cap() == "1536m"
    for bad in ("2 gigs", "0", "0g", "0.0g"):
        monkeypatch.setenv(tl.CONTAINER_MEMORY_ENV, bad)
        with pytest.raises(SystemExit) as e:
            tl.container_memory_cap()
        assert tl.CONTAINER_MEMORY_ENV in str(e.value), (bad, str(e.value))


def test_the_memory_cap_is_validated_before_any_container_is_touched(
        lane, monkeypatch):
    """#7357 review F8: a guard is only as good as its ORDERING.

    `_memory_cap_args()` raises `SystemExit` on a bad value, and it used to be
    evaluated at the `docker run` call site — i.e. AFTER the stale-container
    `docker rm -f` (`_remove_and_describe`). So
    `FALKORDB_CONTAINER_MEMORY=0` (docker's spelling for UNLIMITED) REMOVED the
    lane's stopped container, destroying its graphs, and only then refused to
    start a replacement. Same failure class as the `--port` one this file
    already paid for: the refusal is correct, the ordering made it destructive.
    Every seam below the validation is booby-trapped, so this test fails if the
    check is ever moved back down.
    """
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("docker was called before cap validation")

    monkeypatch.setattr(tl, "reap_orphans", _boom)
    monkeypatch.setattr(tl, "container_state", _boom)
    monkeypatch.setattr(tl, "_remove_and_describe", _boom)
    monkeypatch.setattr(tl, "_docker", _boom)
    for bad in ("0", "0g", "0.0m", "2 gigs"):
        monkeypatch.setenv(tl.CONTAINER_MEMORY_ENV, bad)
        with pytest.raises(SystemExit) as exc:
            tl.start()
        assert tl.CONTAINER_MEMORY_ENV in str(exc.value), (bad, str(exc.value))


def test_a_container_killed_for_memory_is_reported_not_called_wedged(
        lane, monkeypatch):
    """#7357's legibility half: the same failure used to read "it may be wedged:
    run down and retry", which sends the operator to retry into the same death.
    The facts come from docker (`OOMKilled`/`ExitCode`), never from the
    connection error."""
    def fake(*a, **k):
        if a[0] == "inspect":
            if any("OOMKilled" in str(part) for part in a):
                return _R(0, "true 137 3 2147483648\n", "")
            return _R(0, "absent\n", "")
        if a[0] == "run":
            return _R(0, "deadbeef\n", "")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_wait_ready", lambda *a, **k: False)
    with pytest.raises(SystemExit) as e:
        tl.start()
    msg = str(e.value)
    assert "KILLED FOR MEMORY" in msg, msg
    assert "OOMKilled=true" in msg and "ExitCode=137" in msg, msg
    assert "RestartCount=3" in msg, msg


def test_a_stale_killed_container_is_named_BEFORE_it_is_removed(
        lane, monkeypatch, capsys):
    """#7357 review: the evidence must be read before the ONLY path an OOM-killed
    lane container takes is allowed to destroy it.

    The lane container is created with no `--restart` policy, so a kill leaves
    it `exited` — not `running` — i.e. on the stale-container branch, which used
    to `docker rm -f` it with no read at all. The next `start()` then silently
    recreated it and continued, and the operator never learned the cap (or host
    pressure) had killed it: the legibility half this change exists for, absent
    on the path the kill actually leaves.
    """
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    monkeypatch.setattr(tl, "pick_port", lambda *a, **k: 16390)
    monkeypatch.setattr(tl, "_wait_ready", lambda *a, **k: False)
    seen: list = []

    def fake(*a, **k):
        seen.append(a)
        if a[0] == "inspect":
            return _R(0, "true 137 2 2147483648\n", "")
        return _R(0, "deadbeef\n", "")

    monkeypatch.setattr(tl, "_docker", fake)
    with pytest.raises(SystemExit):
        tl.start()
    kinds = [a[0] for a in seen]
    assert kinds.index("inspect") < kinds.index("rm"), (
        f"docker's termination facts must be read BEFORE `docker rm -f` — "
        f"`rm` destroys them: {seen}"
    )
    err = capsys.readouterr().err
    assert "had stopped before this start" in err, err
    assert "KILLED FOR MEMORY" in err, err
    assert "OOMKilled=true" in err and "ExitCode=137" in err, err

    # …and a container that merely stopped cleanly says nothing (no false alarm)
    # while still being replaced.
    monkeypatch.setattr(tl, "_docker",
                        lambda *a, **k: _R(0, "false 0 0 2147483648\n", ""))
    with pytest.raises(SystemExit):
        tl.start()
    assert "had stopped before this start" not in capsys.readouterr().err


def test_a_reused_container_reports_prior_termination_as_history_not_cause(
        lane, monkeypatch):
    """The REUSE branch (#7357 review): for a container that is Up, docker's
    `OOMKilled`/`ExitCode`/`RestartCount` describe a PREVIOUS termination — the
    live server did not hang because it was killed earlier. So the facts are
    reported as HISTORY (with the genuine hypothesis: memory pressure) and the
    "may be wedged: down and retry" advice MUST stay: for a live server that does
    not answer, restarting it is the remedy. Calling this failure a kill was the
    false diagnosis the review measured on a live lane container."""
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_wait_ready", lambda *a, **k: False)
    monkeypatch.setattr(tl, "_container_memory_limit", lambda _n: 2 << 30)
    monkeypatch.setattr(tl, "READY_TIMEOUT", 1)

    def fake(*a, **k):
        if a[0] == "inspect" and any("OOMKilled" in str(part) for part in a):
            return _R(0, "true 137 4 2147483648\n", "")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    with pytest.raises(SystemExit) as e:
        tl.start()
    msg = str(e.value)
    assert "PREVIOUS termination" in msg, msg
    assert "OOMKilled=true" in msg and "ExitCode=137" in msg, msg
    assert "may be wedged" in msg, (
        f"a live server that does not answer is still a wedge — the history is "
        f"context, not the cause: {msg}"
    )
    assert "it did not hang" not in msg, (
        f"a stale fact must never be asserted as this failure's cause: {msg}"
    )


def test_a_restart_loop_is_named_as_a_restart_loop(lane, monkeypatch):
    """#7357 review F2: the CANONICAL instance's own signature is
    `ExitCode=0`, `OOMKilled=false`, `RestartCount` climbing (8 -> 68 -> 150).
    A report keyed only on OOMKilled/137 stayed silent on exactly the restart
    loop the issue is about, so `RestartCount > 0` is its own trigger — with
    memory pressure named as the HYPOTHESIS, because docker reports no reason."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "_wait_ready", lambda *a, **k: False)

    def fake(*a, **k):
        if a[0] == "inspect" and any("OOMKilled" in str(part) for part in a):
            return _R(0, "false 0 150 2147483648\n", "")
        if a[0] == "run":
            return _R(0, "deadbeef\n", "")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    with pytest.raises(SystemExit) as e:
        tl.start()
    msg = str(e.value)
    assert "RestartCount=150" in msg, msg
    assert "RESTARTED" in msg, msg
    assert "hypothesis" in msg, (
        f"docker reports no restart reason, so it must be named as a "
        f"hypothesis, not a finding: {msg}"
    )


def test_reusing_an_uncapped_container_warns_instead_of_handing_it_back(
        lane, monkeypatch, capsys):
    """#7357 review F10: `docker start` cannot add a limit, so a lane container
    created before this cap keeps `Memory=0` for life. Those are exactly the
    neighbours the issue names as the pressure source — handing one back in
    silence is how they stay uncapped."""
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_wait_ready", lambda *a, **k: True)
    monkeypatch.setattr(tl, "_container_memory_limit", lambda _n: 0)
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "", ""))
    name, port = tl.start()
    err = capsys.readouterr().err
    assert "WITHOUT a kernel memory cap" in err, err
    assert "down` and retry" in err, err
    assert (name, port) == (tl.lane_name(), 16390)

    # …an UNREADABLE limit is not silence either (`None` is "could not read",
    # never "it is capped"), because this reuse path is the only place a
    # pre-existing uncapped lane container can be caught (#7357 review — the
    # same arm `graph-scripts/setup.py::_note_uncapped_existing` carries).
    monkeypatch.setattr(tl, "_container_memory_limit", lambda _n: None)
    tl.start()
    err = capsys.readouterr().err
    assert "could not READ the kernel memory limit" in err, err
    assert "HostConfig.Memory" in err, (
        f"the message must name the query that failed, so the reader can run "
        f"it: {err}"
    )
    assert "WITHOUT a kernel memory cap" not in err, (
        f"an unreadable limit must not be reported as an absent one: {err}"
    )

    # A capped container warns about nothing.
    monkeypatch.setattr(tl, "_container_memory_limit", lambda _n: 2 << 30)
    tl.start()
    assert "kernel memory cap" not in capsys.readouterr().err


def test_a_container_that_dies_during_the_wait_is_named_a_death_not_a_wedge(
        lane, monkeypatch):
    """#7357 review: `live` used to be decided by the state read that OPENED the
    reuse branch — i.e. BEFORE `_wait_ready`, which then spends up to
    READY_TIMEOUT (60 s) of failed PINGs failing. A container killed INSIDE that
    window (the memory-pressure case this issue is about) is `exited` by the
    time the message is built, so the stale "it is Up now, so that history
    cannot explain an unanswered PING" inverted the diagnosis: docker's
    OOMKilled/ExitCode were the CURRENT cause while the sentence insisted they
    were history. The state is therefore re-read after the wait."""
    states = iter(["running", "exited"])

    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "container_state", lambda _n: next(states, "exited"))
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16390))
    monkeypatch.setattr(tl, "_wait_ready", lambda *a, **k: False)
    monkeypatch.setattr(tl, "_container_memory_limit", lambda _n: 2 << 30)
    monkeypatch.setattr(tl, "READY_TIMEOUT", 1)

    def fake(*a, **k):
        if a[0] == "inspect" and any("OOMKilled" in str(part) for part in a):
            return _R(0, "true 137 3 2147483648\n", "")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    with pytest.raises(SystemExit) as e:
        tl.start()
    msg = str(e.value)
    assert "STOPPED while waiting" in msg, msg
    assert "PREVIOUS termination" not in msg, (
        f"the container is not Up any more, so its facts are this failure's "
        f"cause, not history: {msg}"
    )
    assert "KILLED FOR MEMORY" in msg, (
        f"a death inside the wait window is still a death and must be named: "
        f"{msg}"
    )

    # …and an UNREADABLE state is NOT a death. `container_state` keeps "docker
    # could not answer" apart from "the container is gone" on purpose, and the
    # `down` remedy REMOVES the container and its graphs — so guessing a death
    # from a failed read is the mistake the absent-vs-unreadable rule exists to
    # prevent (#7357 review).
    states2 = iter(["running", "unknown"])
    monkeypatch.setattr(tl, "container_state", lambda _n: next(states2, "unknown"))

    def termination_answers(*a, **k):
        # The STATE read failed, but docker ANSWERS the termination inspect: this
        # is the dangerous shape, because `_termination_report` issues its own
        # call and does NOT inherit the state read's failure. A daemon that
        # recovers between the two calls answers with the container's PREVIOUS
        # termination — so if the `unknown` arm appended that report, the message
        # would claim the death it had just called UNKNOWN.
        if a[0] == "inspect" and any("OOMKilled" in str(part) for part in a):
            return _R(0, "true 137 3 2147483648\n", "")
        return _R(1, "", "deadline exceeded")

    monkeypatch.setattr(tl, "_docker", termination_answers)
    with pytest.raises(SystemExit) as e:
        tl.start()
    unknown = str(e.value)
    assert "could not be READ" in unknown, unknown
    assert "UNKNOWN" in unknown, unknown
    assert "is a death" not in unknown, (
        f"a failed state read is not evidence of a death, and the advice it "
        f"carries (`down`) would destroy the container: {unknown}"
    )
    assert "KILLED FOR MEMORY" not in unknown, (
        f"nothing may be asserted about a state that could not be read — the "
        f"termination report is a SECOND docker call and can succeed even when "
        f"the state read failed: {unknown}"
    )


def test_the_long_memory_unit_spellings_docker_accepts_are_accepted(monkeypatch):
    """#7357 review: docker accepts `2gb` and `2048mb` (the LONG unit forms).

    Every seam refused them while telling the operator to "raise
    FALKORDB_CONTAINER_MEMORY" — i.e. the one message that gives the remedy
    refused the natural spelling of the remedy, and called a docker-valid spec
    "not a POSITIVE docker memory spec". One grammar, and it is docker's."""
    from tools import edge_census as ec

    for good in ("2gb", "2048mb", "512mb", "2g", "1536m", "2kb", "2b",
                 "1t", "2tb", "1.5gb"):
        monkeypatch.setenv(tl.CONTAINER_MEMORY_ENV, good)
        assert tl.container_memory_cap() == good, good
        monkeypatch.setenv(ec.CONTAINER_MEMORY_ENV, good)
        assert ec.container_memory_cap() == good, good
    # The ZERO arm is unchanged at every seam: `0gb` is still UNLIMITED.
    monkeypatch.setenv(tl.CONTAINER_MEMORY_ENV, "0gb")
    with pytest.raises(SystemExit):
        tl.container_memory_cap()
    monkeypatch.setenv(ec.CONTAINER_MEMORY_ENV, "0.0mb")
    with pytest.raises(ec.CensusError):
        ec.container_memory_cap()


def test_a_plain_sigkill_is_not_called_a_memory_kill(monkeypatch):
    """#7357 review: `ExitCode=137` alone is 128+SIGKILL WITHOUT a container OOM
    event, and a cgroup-cap kill is precisely what DOES set `OOMKilled=true`. The
    message used to print "KILLED FOR MEMORY (OOMKilled=false, … by its own
    cgroup cap …)" — naming the cause its own evidence excludes. The trigger
    stays (a death is a death); the WORDING now follows the evidence."""
    from tools import edge_census as ec

    def fake(*_a, **_k):
        return _R(0, "false 137 false\n", "")

    monkeypatch.setattr(ec, "_docker", fake)
    msg = ec._memory_kill_report("probe-x")
    assert "SIGKILLED" in msg, msg
    assert "KILLED FOR MEMORY" not in msg, (
        f"OOMKilled=false excludes a container OOM, so the message must not "
        f"name one: {msg}"
    )
    assert "did NOT fire" in msg, (
        f"the cgroup cap is what did NOT fire here, and saying so is the fact "
        f"that keeps the operator from raising a cap that was never hit: {msg}"
    )

    monkeypatch.setattr(ec, "_docker",
                        lambda *a, **k: _R(0, "true 137 false\n", ""))
    oom = ec._memory_kill_report("probe-x")
    assert "KILLED FOR MEMORY" in oom and "cgroup cap" in oom, oom

    # A container that is STILL RUNNING reports nothing: docker keeps
    # `OOMKilled`/`ExitCode` from the container's PREVIOUS termination, so for a
    # live probe those facts are history, and a query error is not explained by
    # them (#7357 review — the mirror of the `live` rule in `test_lane`).
    monkeypatch.setattr(ec, "_docker",
                        lambda *a, **k: _R(0, "true 137 true\n", ""))
    assert ec._memory_kill_report("probe-x") == "", (
        "a running probe's termination facts are HISTORY, never this query "
        "error's cause"
    )


def test_a_kill_names_the_MEASURED_cap_not_the_current_env(lane, monkeypatch):
    """#7357 review: the message printed `FALKORDB_CONTAINER_MEMORY` — the value
    the tool WOULD use now — as "its own cgroup cap". A lane container outlives
    runs and `docker start` cannot change its limit, so a container created at 2g
    and OOM-killed while the env says 8g was reported as killed by a cap it does
    not have. The cap that applies is docker's `HostConfig.Memory`."""
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setenv(tl.CONTAINER_MEMORY_ENV, "8g")

    def fake(*a, **k):
        if a[0] == "inspect" and any("OOMKilled" in str(part) for part in a):
            # killed, restart count 0, and the cap docker really holds: 2 GiB
            return _R(0, "true 137 0 2147483648\n", "")
        if a[0] == "run":
            return _R(0, "deadbeef\n", "")
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_wait_ready", lambda *a, **k: False)
    with pytest.raises(SystemExit) as e:
        tl.start()
    msg = str(e.value)
    assert "2147483648 bytes" in msg, (
        f"the MEASURED cap must be named, not the env value: {msg}"
    )
    assert "NOT the FALKORDB_CONTAINER_MEMORY=8g in effect now" in msg, (
        f"a cap that differs from the env must be called out, or the operator "
        f"raises a cap that was never in play: {msg}"
    )

    # …and a SPELLING that means the same cap is not a mismatch: the env value is
    # read the way `container_memory_cap()` reads it (`.strip().lower()`), so a
    # cap this tool would itself accept never reports "recreate the container".
    for same in ("2g", "2G", " 2g ", "2048m", "2gb"):
        monkeypatch.setenv(tl.CONTAINER_MEMORY_ENV, same)
        with pytest.raises(SystemExit) as e:
            tl.start()
        assert "in effect now" not in str(e.value), (
            f"{same!r} is the same 2 GiB the container holds, so recommending a "
            f"recreate for it is a false alarm: {e.value}"
        )


def test_a_paused_container_is_never_removed(lane, monkeypatch):
    """#7357 review, silent destruction of work: this path used to `rm -f` EVERY
    non-running container. `paused` is a LIVE database — `docker pause` is what an
    operator reaches for to freeze a runaway instance rather than kill it — and
    its termination facts read `OOMKilled=false ExitCode=0 RestartCount=0`, so the
    removal printed nothing: a paused container holding a graph was silently
    deleted, and `start()` returned the replacement as if nothing had happened."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "paused")
    monkeypatch.setattr(tl, "_assert_removal_allowed", lambda _n: None)

    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("a paused container must never be removed")

    monkeypatch.setattr(tl, "_remove_and_describe", _boom)
    monkeypatch.setattr(tl, "_docker", _boom)
    with pytest.raises(SystemExit) as e:
        tl.start()
    msg = str(e.value)
    assert "'paused'" in msg and "NOT terminal" in msg, msg
    assert "unpause" in msg, f"the non-destructive way out must be named: {msg}"

    # An UNRECOGNISED state is not permission to delete either: docker may add
    # one, and the default must be "keep the container".
    monkeypatch.setattr(tl, "container_state", lambda _n: "frobnicating")
    with pytest.raises(SystemExit) as e:
        tl.start()
    assert "does not recognise" in str(e.value), str(e.value)


def test_the_reuse_note_never_invents_a_cap_it_did_not_read(
        monkeypatch, capsys):
    """#7357 review round 3: three read outcomes, never collapsed.

    `_container_memory_limit` answers `None` (could not read) or `0` (readable,
    NO cgroup cap) — and collapsing either into "use the configured cap" asserts
    a cap the container does not have, then prescribes `down`, which DELETES that
    container's graphs, for a container whose ceiling was fine.
    """
    # (1) the cap could not be read; the container is in fact 4g-born and bounded.
    def unreadable_cap(*a, **k):
        if a[0] == "exec":
            return _R(0, "maxmemory\n4294967296\n", "")
        if a[0] == "inspect" and "Config.Env" in a[2]:
            return _R(0, '["REDIS_ARGS=--maxmemory 1536mb"]\n', "")
        return _R(1, "", "inspect failed")
    monkeypatch.setattr(tl, "_docker", unreadable_cap)
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef08")
    assert capsys.readouterr().err == ""

    # (2) readable, and it means NO cgroup cap: the ceiling is the ONLY bound and
    # it does fire, so nothing should be said here at all — the missing cgroup cap
    # is `_note_uncapped_existing`'s finding, printed beside this one.
    def zero_cap(*a, **k):
        if a[0] == "exec":
            return _R(0, "maxmemory\n4294967296\n", "")
        return _R(0, "0\n", "")
    monkeypatch.setattr(tl, "_docker", zero_cap)
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef09")
    assert capsys.readouterr().err == ""


def test_an_env_ceiling_of_zero_is_reported_as_unbounded_not_unreadable(
        monkeypatch, capsys):
    """`--maxmemory 0` is Redis's spelling for NO LIMIT, not an unparsed value.

    Reporting the one value this note exists to catch as "unreadable" understates
    it — and "unreadable" describes the PARSER, not the container.
    """
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: (
        _R(1, "", "no exec") if a[0] == "exec"
        else _R(0, '["REDIS_ARGS=--maxmemory 0"]\n', "")
        if "Config.Env" in a[2] else _R(0, "2147483648\n", "")))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef10")
    err = capsys.readouterr().err
    assert "means NO LIMIT" in err, err
    assert "unreadable" not in err, err
    # ⛔ …and the FATE half is read from the cap: with a 2g cap the overshoot IS
    # caught (by the kernel, illegibly). The old wording named that cap in
    # `cap_note` and then denied it — "so an overshoot goes uncaught" — in the
    # same sentence (#7357 review, round 6).
    assert "SIGKILLed by the cgroup cap" in err, err
    assert "goes uncaught" not in err, err


def test_a_zero_env_ceiling_with_no_cap_is_called_uncaught(monkeypatch, capsys):
    """The other half of the same branch: NO cap read means "uncaught" is true.

    Without it the fix above could be "always claim the SIGKILL", which is the
    mirror-image error: with `HostConfig.Memory=0` there is no kernel limit, and
    an overshoot pages the host — #7357's own outage (#7357 review, round 6).
    """
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: (
        _R(1, "", "no exec") if a[0] == "exec"
        else _R(0, '["REDIS_ARGS=--maxmemory 0"]\n', "")
        if "Config.Env" in a[2] else _R(0, "0\n", "")))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef18")
    err = capsys.readouterr().err
    assert "means NO LIMIT" in err, err
    assert "goes uncaught" in err, err
    assert "SIGKILLed by the cgroup cap" not in err, err


def test_the_env_fallback_does_not_invent_a_cgroup_cap_to_blame(
        monkeypatch, capsys):
    """#7357 review round 6 P2 — the env-fallback sibling of the live-branch fix.

    With the live read failing, the "no `--maxmemory` at all" branch asserted
    "an overshoot is SIGKILLed by the cgroup cap" UNCONDITIONALLY — naming a cap
    that the same run's three-way read had just found absent or unreadable. The
    consequence is not cosmetic: with no cgroup cap the overshoot is not caught,
    it pages the host ("#7357's fleet outage), so the message understated the
    defect in the one place it is supposed to name it.
    """
    def env_with_no_ceiling(limit):
        def fake(*a, **k):
            if a[0] == "exec":
                return _R(1, "", "redis wedged")   # force the env fallback
            if a[0] == "inspect" and "Config.Env" in a[2]:
                return _R(0, '["REDIS_ARGS=--appendonly no --save \'\'"]\n', "")
            return _R(0, f"{limit}\n", "")
        return fake

    # (1) NO cgroup cap at all: there is no kernel limit for a SIGKILL to come
    # from, and the note is beside `_note_uncapped_existing`'s own report.
    monkeypatch.setattr(tl, "_docker", env_with_no_ceiling("0"))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef19")
    err = capsys.readouterr().err
    assert "NO dataset ceiling" in err, err
    assert "goes uncaught" in err, err
    assert "SIGKILLed by the cgroup cap" not in err, err

    # (2) …and where a cap WAS read, the claim is true and stays.
    monkeypatch.setattr(tl, "_docker", env_with_no_ceiling("2147483648"))
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef20")
    err = capsys.readouterr().err
    assert "NO dataset ceiling" in err, err
    assert "SIGKILLed by the cgroup cap" in err, err
    assert "goes uncaught" not in err, err


def test_the_env_fallback_does_not_call_a_readable_ceiling_unreadable(
        monkeypatch, capsys):
    """#7357 review round 4 P1: the sibling branch of the live-vs-env fix.

    With the LIVE read failing, an unreadable/absent CGROUP CAP used to be
    collapsed into "unreadable `--maxmemory`" — a claim about a ceiling that was
    read perfectly well — and then prescribed `down`, which DELETES that
    container's graphs. The question this function owns is the CEILING; the
    container's cgroup cap is `_note_uncapped_existing`'s finding.
    """
    def env_and_no_cap(*a, **k):
        if a[0] == "exec":
            return _R(1, "", "redis wedged")
        if a[0] == "inspect" and "Config.Env" in a[2]:
            return _R(0, '["REDIS_ARGS=--maxmemory 1gb"]\n', "")
        return _R(0, "0\n", "")   # readable: NO cgroup cap of its own
    monkeypatch.setattr(tl, "_docker", env_and_no_cap)
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef11")
    assert capsys.readouterr().err == ""

    # …and the same when the cap read FAILS outright.
    def env_and_unreadable_cap(*a, **k):
        if a[0] == "exec":
            return _R(1, "", "redis wedged")
        if a[0] == "inspect" and "Config.Env" in a[2]:
            return _R(0, '["REDIS_ARGS=--maxmemory 1gb"]\n', "")
        return _R(1, "", "inspect failed")
    monkeypatch.setattr(tl, "_docker", env_and_unreadable_cap)
    tl._note_missing_dataset_ceiling("fdb-lane-deadbeef12")
    assert capsys.readouterr().err == ""


# ── #10399: `run` — the invocation cannot be mis-spelled ───────────────────
#
# WHAT THESE PIN. A full suite started with the invocation AGENTS.md documented
# (`TORTOISE_DB_URI='docker://:falkordb@localhost:6379/…' pytest tests/`) cannot
# run on a cell: the services lanes publish on EPHEMERAL ports (#6673) and a
# private lane on its own, so nothing answers on 6379. The session-start gate
# then refuses once per COLLECTED TEST, and the run reports a wall of `E`s and no
# verdict (the population is the suite's size — 41,842 tests at #10399's head;
# the batch's log held 405 because its process died mid-run). `run` removes the
# step a caller can forget, so what it must be pinned on is the exact child
# ENVIRONMENT and ARGV — not Docker's behaviour, which is why `start` is
# stubbed here like everywhere else in this file.

def _stub_start(monkeypatch, *, port: int = 16406):
    """Pin the resolved lane, so the seam under test is the child env, not Docker.

    `_container_in_use` is pinned to "no live client" because the exit hook reads
    it before reaping (#10806 review): left real, it would issue a docker call in
    every one of these tests and answer `None` (unreadable), which fails closed
    and suppresses the reap the hook tests assert. Its own behaviour has a test.
    """
    monkeypatch.setattr(tl, "start", lambda _p=None: (LANE_FIXTURE_NAME, port))
    # `**kwargs`: the hook asks the probe a STRICTER question than the sweep does
    # (`idle_within_s=None` — "is anything attached at all", see `cmd_run`), so a
    # stub that only accepts the name would raise TypeError and the hook's own
    # `except Exception` would swallow it as "could not reap".
    monkeypatch.setattr(tl, "_container_in_use", lambda _n, **_k: False)
    # The hook reads the container's STATE before it decides whether the client
    # probe is meaningful at all (a TERMINAL container cannot have a client, and
    # `docker exec` into it fails — #10813 review P2-3). Left real, that is a
    # `docker inspect` in every one of these tests. Pinned to `running`, the state
    # in which the question is askable; the terminal case has its own test.
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    # The module-wide fixture disables automatic reaping (`TORTOISE_TEST_LANE_REAP=0`)
    # so no test touches the real box's containers; `run` now HONOURS that opt-out
    # (#10813 review P2-4), so the run tests have to re-enable it for themselves.
    # `start()` is stubbed above, so enabling it here cannot trigger a real sweep.
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "1")
    # `start()` is stubbed, so state its CREATE/REUSE verdict explicitly: the exit
    # hook only removes a container this `run` created.
    monkeypatch.setattr(tl, "_LAST_START_CREATED", True)


def _capture_run(monkeypatch, *, returncode: int = 0) -> dict:
    """Capture the child invocation. Returns the record the assertions read.

    `stop` is stubbed here because every test below pins the CHILD INVOCATION
    (argv, env, exit-code mapping), not the exit path — and the exit hook
    (#8180) would otherwise reach the real docker on every one of them. The
    hook's own tests, further down, pin the hook through this same stub: what
    they assert is that `stop` is CALLED (and when), never what `stop` does —
    `stop()` is tested directly, elsewhere in this file.
    """
    seen: dict = {"stopped": []}

    def fake_run(argv, env=None, **kwargs):
        seen["argv"] = list(argv)
        seen["env"] = dict(env or {})
        seen["kwargs"] = kwargs
        return _R(returncode, "", "")

    monkeypatch.setattr(tl.subprocess, "run", fake_run)
    monkeypatch.setattr(tl, "stop",
                        lambda: (seen["stopped"].append("stop"), "removed")[1])
    return seen


def test_run_exports_ALL_FOUR_seam_vars_to_the_child(lane, monkeypatch):
    """The whole point of the subcommand: the private lane must be the SESSION's
    target for BOTH resolution paths.

    Exporting only `TORTOISE_DB_URI` is the measured failure this tool's SCOPE
    section records: tests that build their target through
    `tests/_live_utils.py` key on the port and password seams too, so a URI-only
    export leaves them on the SHARED instance — and against a passwordless
    private container the probe raises AuthenticationError, which an
    `except Exception` turns into "not available" → skips, rc=0.

    The LEGACY port is the fourth row of the same table, and it was the one
    still missing: `_live_utils.legacy_port()` falls back to `FALKORDB_PORT`
    and then to the legacy family's historical literal 16379 — NOT where CI's
    passwordless service listens (the provision action assigns it an ephemeral
    port and exports this very variable). So a site that resolves the legacy port
    WITHOUT first consulting `TORTOISE_DB_URI` hit that literal on a lane whose
    container is ephemeral: the 5 direct dials in `tests/test_directional_impl.py`
    failed `Error 111 connecting to localhost:16379` (measured `5 failed` on main
    at e4703a286a, `5 passed` with this export), and
    probe-gated sites skipped the class instead. Asserting it here is what stops
    the omission returning.
    """
    _stub_start(monkeypatch, port=16406)
    seen = _capture_run(monkeypatch)
    rc = tl.main(["run", "--", "-q", "tests/test_guard.py"])
    assert rc == 0
    assert seen["env"]["TORTOISE_DB_URI"] == \
        "docker://:@127.0.0.1:16406/tortoise_test_matrix"
    assert seen["env"]["TORTOISE_TEST_DOCKER_PORT"] == "16406"
    # `''` is a REAL value ("no requirepass"), not a missing one — `None` is the
    # different spelling (no userinfo at all) the seam also has to distinguish.
    assert seen["env"]["TORTOISE_TEST_DOCKER_PASSWORD"] == ""
    # The passwordless axis, pointed at the SAME container: this one has no
    # `requirepass`, so it IS the legacy service — the export names the target,
    # it does not widen what the tool isolates.
    assert seen["env"]["TORTOISE_TEST_LEGACY_PORT"] == "16406"


def test_run_names_EVERY_seam_variable_on_the_stderr_target_line(
        lane, monkeypatch, capsys):
    """The stderr target line is the ONLY record a killed run leaves behind.

    It is also the line that stayed at three variables after the legacy axis was
    added (review P2): a hand-written key list cannot notice a new key, so the
    fixed tool renders it from `lane_seam_env(...)`. This pins the EFFECT: every
    key the child receives appears on the line, exactly once — it fails for a
    re-typed list, and it does not care about
    separator/formatting choices. Seam GROWTH is caught by the sibling
    `test_run_and_up_emit_the_SAME_four_seam_vars` (`len(exports) == 4`), because
    this test re-reads the same dict at assertion time and therefore cannot see a
    key the line renders correctly.

    `DOCKER_HOST` is set so `uri_for`'s warning fires: `cmd_run` must call
    `lane_seam_env` exactly ONCE (a second call printed that warning twice —
    round-2 review), and every other test stays green when it is duplicated
    because the existing `uri_for` pin only asserts presence and this one filters
    on `invoking:`. Asserting `== 1` here is what makes the single-call fix
    non-vacuous.
    """
    monkeypatch.setenv("DOCKER_HOST", "ssh://build-host")
    _stub_start(monkeypatch, port=16406)
    _capture_run(monkeypatch)
    assert tl.main(["run", "--", "-q", "tests/test_guard.py"]) == 0
    err = capsys.readouterr().err
    assert err.count("DOCKER_HOST is set") == 1, err
    lines = [ln for ln in err.splitlines() if "invoking:" in ln]
    assert len(lines) == 1, err
    for key, value in tl.lane_seam_env(16406, "tortoise_test_matrix").items():
        assert f"{key}=" in lines[0], lines[0]
        assert lines[0].count(f"{key}=") == 1, lines[0]
        if value:
            assert f"{key}={value}" in lines[0], lines[0]


def test_run_invokes_the_RUNNING_venv_and_passes_pytest_argv_verbatim(
        lane, monkeypatch):
    """`sys.executable -m pytest`, never a bare `pytest` from PATH.

    On a box with several worktrees a PATH `pytest` is whichever venv happens to
    be first — a wrong-venv run measures the wrong code, and a venv missing the
    `embeddings` extra silently runs a keyword-only product (#2985). The pytest
    argv after the separator is the caller's, unmodified.
    """
    _stub_start(monkeypatch)
    seen = _capture_run(monkeypatch)
    tl.main(["run", "--", "-q", "-k", "seam or not", "tests/"])
    assert seen["argv"] == [sys.executable, "-m", "pytest",
                            "-q", "-k", "seam or not", "tests/"]


def test_run_does_not_leak_the_lane_target_into_the_PARENT_environment(
        lane, monkeypatch):
    """A `run` must not arm the calling shell's env.

    The caller is often a lane's own driver, which then runs other commands
    (a `down`, a second subset). Mutating `os.environ` would silently aim those
    at a container that may already be gone — the same class the tool refuses
    everywhere else by deriving its target from the worktree instead of the env.
    The assertion is on the four SEAM keys specifically (not on a whole-environ
    identity), so an unrelated env write by a future callee cannot red it.
    """
    seam = ("TORTOISE_DB_URI", "TORTOISE_TEST_DOCKER_PORT",
            "TORTOISE_TEST_DOCKER_PASSWORD", "TORTOISE_TEST_LEGACY_PORT")
    before = {key: os.environ.get(key) for key in seam}
    _stub_start(monkeypatch, port=16411)
    _capture_run(monkeypatch)
    tl.main(["run", "--", "-q"])
    assert {key: os.environ.get(key) for key in seam} == before


def test_run_propagates_pytest_failure_unchanged(lane, monkeypatch):
    """pytest's own exit code IS the answer — 1 = tests failed, not a tool error."""
    _stub_start(monkeypatch)
    _capture_run(monkeypatch, returncode=1)
    assert tl.main(["run", "--", "-q", "tests/"]) == 1


def test_run_re_stamps_the_lease_AFTER_the_child(lane, monkeypatch):
    """#10693 review F1: the lease epoch must be the END of the last use.

    With the stamp written only in `start()`, a run longer than `OWNER_GRACE_H`
    ends with an already-expired gap, and the next peer sweep may reap the
    container in the between-steps window the grace exists to cover. The ORDER is
    the assertion: the stamp must land after the child returns.

    Mutation: drop `_touch_lease(name)` from `cmd_run` and the events list is
    `[("child", None)]` alone."""
    _stub_start(monkeypatch, port=16412)
    events: list = []

    def fake_run(argv, env=None, **kwargs):
        events.append(("child", None))
        return _R(0, "", "")

    monkeypatch.setattr(tl.subprocess, "run", fake_run)
    monkeypatch.setattr(tl, "_touch_lease", lambda n: events.append(("lease", n)))
    assert tl.main(["run", "--keep-container", "--", "-q", "tests/"]) == 0
    assert events == [("child", None), ("lease", LANE_FIXTURE_NAME)], events


def test_a_run_LONGER_than_the_grace_ends_with_a_FRESH_lease(lane, monkeypatch):
    """The measured shape behind F1: the container is USED for the whole run, so
    the gap after the run must be ~0 — never the run's own wall time.

    Mutation: remove the post-child `_touch_lease` and `_unused_gap` still
    reports the 3 h old stamp from before the run, i.e. a gap already past the
    2 h grace at the exact moment the lane is between steps."""
    monkeypatch.setattr(tl, "_touch_lease", _REAL_TOUCH_LEASE)
    monkeypatch.setattr(tl, "_container_id", lambda _n: "ffffffffffff")
    name = LANE_FIXTURE_NAME
    f = tl._reap_state_path(name)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"ffffffffffff {time.time() - 3 * 3600:.0f}\n")
    assert tl._unused_gap(name) > 2.9          # the pre-run lease, past the grace
    _stub_start(monkeypatch, port=16413)
    _capture_run(monkeypatch)                  # a child that ran and returned
    assert tl.main(["run", "--keep-container", "--", "-q", "tests/"]) == 0
    gap = tl._unused_gap(name)
    assert gap is not None and gap < 0.1, gap


def test_run_reports_a_SIGNAL_kill_as_128_plus_N_not_a_negative_code(
        lane, monkeypatch, capsys):
    """A killed run is NOT a test failure, and it has no count line.

    The batch's `E=405` investigation began with a log that just ENDED: the
    process was killed (this box OOM-killed five python processes in 23 minutes
    at load 15 on 15 GB), so no `N passed` line existed. `subprocess` reports
    that as a NEGATIVE returncode, and returning it made the shell see 256-N —
    which every caller read as an argparse-ish usage error. 128+N is the shell
    convention, and the stderr line names the kill so the next reader does not
    have to guess.
    """
    _stub_start(monkeypatch)
    _capture_run(monkeypatch, returncode=-9)
    assert tl.main(["run", "--", "-q", "tests/"]) == 137
    err = capsys.readouterr().err
    assert "KILLED by signal 9" in err
    assert "NOT a test failure" in err


# ── THE EXIT HOOK (#8180's actuator half) ───────────────────────────────────
# `run` is where a lane ENDS, so it is where its container goes. These pin the
# three properties that make the hook safe to be the DEFAULT on every lane:
# it runs on the failure path too, it can be escaped, and it never speaks for
# the run's verdict.


def test_run_REAPS_its_own_container_on_exit_BY_DEFAULT(lane, monkeypatch):
    """The primary fix for the leak: a normal `run` leaves NO container.

    Measured before this hook: every lane that ran the suite and was then closed
    (`cmux workspace close` never calls `down`) left exactly one `fdb-lane-*`
    container behind, forever — 189 of them across three cells, each reaped by
    hand. Mutation: delete the `finally:` reap and the container survives every
    normal run."""
    _stub_start(monkeypatch, port=16412)
    events: list = []

    def fake_run(argv, env=None, **kwargs):
        events.append("child")
        return _R(0, "", "")

    monkeypatch.setattr(tl.subprocess, "run", fake_run)
    monkeypatch.setattr(tl, "stop",
                        lambda: (events.append("reap"), "removed")[1])
    assert tl.main(["run", "--", "-q", "tests/"]) == 0
    assert events == ["child", "reap"], events


def test_run_REAPS_on_the_FAILURE_path_too(lane, monkeypatch):
    """A RED run is when nobody runs `down`, so it is the path that leaks most."""
    _stub_start(monkeypatch, port=16412)
    reaped: list = []
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(1, "", ""))
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    assert tl.main(["run", "--", "-q", "tests/"]) == 1   # the verdict survives
    assert reaped == [1], "a failing run left its container behind"


def test_keep_container_ESCAPES_the_hook_and_still_stamps_the_lease(
        lane, monkeypatch):
    """A post-mortem needs the DB. The escape must be explicit, and it must
    still write the lease — a container deliberately kept for inspection is
    exactly the one a peer's sweep must not take."""
    _stub_start(monkeypatch, port=16412)
    leases: list = []
    reaped: list = []
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(0, "", ""))
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    monkeypatch.setattr(tl, "_touch_lease", lambda n: leases.append(n))
    assert tl.main(["run", "--keep-container", "--", "-q", "tests/"]) == 0
    assert reaped == [], "--keep-container still reaped"
    assert leases == [LANE_FIXTURE_NAME], leases


def test_the_hook_NEVER_takes_a_container_a_peer_is_using(lane, monkeypatch, capsys):
    """Two `run`s in one worktree (a lane plus a `task` child) share ONE container.

    Without the in-use guard the one that exits first removes the other's live
    target, and its connection errors are reported as test verdicts — a leaked
    container traded for a false red. `None` (unreadable) fails closed the same
    way: this hook is not the decision-maker of last resort, the timer is.
    """
    _stub_start(monkeypatch, port=16412)
    reaped: list = []
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(0, "", ""))
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    leases: list = []
    monkeypatch.setattr(tl, "_touch_lease", lambda n: leases.append(n))
    for answer, why in ((True, "a live client is using it"),
                        (None, "could not be read")):
        reaped.clear()
        leases.clear()
        monkeypatch.setattr(tl, "_container_in_use",
                            lambda _n, _a=answer, **_k: _a)
        assert tl.main(["run", "--", "-q", "tests/"]) == 0
        assert why in capsys.readouterr().err
        assert reaped == [], f"reaped while the client list said {answer!r}"
        # The container SURVIVED, so its lease must be re-stamped at the END of
        # the use — otherwise a container spared here enters the next sweep
        # already past OWNER_GRACE_H (#10806 review P2-4).
        assert leases == [LANE_FIXTURE_NAME], (
            f"a spared container was left with a stale lease: {leases}")


def test_the_hook_asks_whether_ANYTHING_is_ATTACHED_not_whether_it_is_RECENT(
        lane, monkeypatch, capsys):
    """The reverse ordering: the consumer is the one that did NOT create it.

    A consumer that opened the container with `uri` writes no `run` marker, so when
    it sits idle through a heavy non-DB test file its ONLY signal is the socket it
    still holds. The sweep's `ACTIVE_CLIENT_IDLE_S` (300 s) measure calls that
    "unused" — which made the hook `rm -f` a live peer consumer's target, and its
    connection errors were then reported as test verdicts (#10813 review P2).

    The hook removes only AFTER its own child exits, so nothing of its own can
    still be attached: a remaining client is someone else's, however long it has
    been quiet. Mutation: pass the sweep's default here and this test reds.
    """
    _stub_start(monkeypatch, port=16412)
    reaped: list = []
    seen: list = []
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(0, "", ""))
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)

    def probe(_name, **kw):
        seen.append(kw)
        # Attached, but quiet for far longer than the sweep's window.
        return kw.get("idle_within_s") is None

    monkeypatch.setattr(tl, "_container_in_use", probe)
    assert tl.main(["run", "--", "-q", "tests/"]) == 0
    assert seen == [{"idle_within_s": None}], seen
    assert reaped == [], (
        "the 300 s 'unused' measure took a live consumer's database")
    assert "a live client is using it" in capsys.readouterr().err


def test_container_in_use_can_answer_ANY_attached_client(monkeypatch):
    """The hook's question, pinned at the probe itself (not just at its caller).

    `idle_within_s=None` = "is anything attached at all": a client that has been
    quiet for hours still holds the socket, and a client with no `idle=` field at
    all (a pub/sub or MONITOR client) is attached just as much. The DEFAULT is
    unchanged, so the sweep's unused-lease measure keeps its semantics.
    """
    quiet = "id=7 cmd=lrange idle=4200\nid=8 cmd=client|list idle=0\n"
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, quiet, ""))
    assert tl._container_in_use("fdb-lane-x") is False, (
        "the sweep's 300 s measure must still call a long-idle client 'unused'")
    assert tl._container_in_use("fdb-lane-x", idle_within_s=None) is True

    # A subscriber carries no `idle=` field at all.
    sub = "id=7 cmd=subscribe\nid=8 cmd=client|list idle=0\n"
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, sub, ""))
    assert tl._container_in_use("fdb-lane-x") is False
    assert tl._container_in_use("fdb-lane-x", idle_within_s=None) is True

    # …and the probe's own line is never the client it reports.
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(
        0, "id=8 cmd=client|list idle=0\n", ""))
    assert tl._container_in_use("fdb-lane-x", idle_within_s=None) is False


def test_SIGTERM_reaches_the_hook_and_the_run_exits_143(lane, monkeypatch):
    """A lane stopped by its supervisor gets SIGTERM, and the DEFAULT disposition
    terminates without unwinding — so without a handler the hook never runs and
    the container leaks. This is the only net-new behaviour in the hook with no
    other test (#10806 review P2).

    The assertion is on the SIGNAL, not on the reap: the run raises `SystemExit`
    from the handler, which means the `finally` unwound.
    """
    import signal as _signal
    _stub_start(monkeypatch, port=16412)
    reaped: list = []
    seen: list = []

    def fake_run(argv, env=None, **kwargs):
        seen.append(_signal.getsignal(_signal.SIGTERM))
        os.kill(os.getpid(), _signal.SIGTERM)      # what a supervisor sends
        return _R(0, "", "")

    monkeypatch.setattr(tl.subprocess, "run", fake_run)
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    rc = tl.main(["run", "--", "-q", "tests/"])
    assert rc == 143, rc                            # 128 + SIGTERM
    assert seen and seen[0] is not _signal.SIG_DFL, (
        "SIGTERM was still at its default disposition when the child started")
    assert reaped == [1], "the hook did not run on the SIGTERM path"


def test_START_records_whether_it_CREATED_or_REUSED(lane, monkeypatch):
    """The exit hook's whole safety argument rests on this one datum.

    Every hook test stubs `start()` and sets `_LAST_START_CREATED` by hand, so
    inverting the line in `start()` left the entire safety suite green — while
    making the hook reap REUSED containers (the #10813 P1 returning) and never
    reap what it created (the leak returning). Pinned here against the real
    `start()`, both branches.
    """
    # REUSE: the container is already up.
    monkeypatch.setattr(tl, "_LAST_START_CREATED", True)      # stale on purpose
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_created_image", lambda _n: tl.IMAGE)
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16406))
    monkeypatch.setattr(tl, "_wait_ready", lambda *a, **k: True)
    monkeypatch.setattr(tl, "_touch_lease", lambda _n: None)
    monkeypatch.setattr(tl, "_container_memory_limit", lambda _n: None)
    monkeypatch.setattr(tl, "_note_missing_dataset_ceiling", lambda _n: None)
    monkeypatch.setattr(tl, "_note_uncapped_existing", lambda _n: None)
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "0")
    tl.start()
    assert tl._LAST_START_CREATED is False, (
        "a REUSED container was recorded as created — the hook would reap a "
        "container another consumer owns")

    # CREATE: nothing there, so this call makes it.
    monkeypatch.setattr(tl, "_LAST_START_CREATED", False)     # stale on purpose
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "pick_port", lambda: 16407)
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "cid\n", ""))
    monkeypatch.setattr(tl, "_published_port", lambda _n: (True, 16407))
    monkeypatch.setattr(tl, "_note_uncapped_existing", lambda _n: None)
    tl.start()
    assert tl._LAST_START_CREATED is True, (
        "a container this run CREATED was recorded as reused — the hook would "
        "never reap it and the leak returns")


def test_a_PEER_run_that_holds_NOTHING_still_blocks_the_reap(lane, monkeypatch,
                                                            capsys):
    """The ordering that matters: the peer is LIVE but is NOT the lock holder.

    A single exclusive lock cannot express "one OR more peers" — the second run
    holds nothing, so its own exit probe answers "no peer" and it removes the
    first run's live container. Verified by experiment on the first version of
    this guard (#10813 review P1); this test uses a real second PROCESS whose
    marker is present while a `run` exits, which is that exact shape.
    """
    _stub_start(monkeypatch, port=16412)
    reaped: list = []
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(0, "", ""))
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    name = LANE_FIXTURE_NAME
    assert tl._peer_run_is_live(name) is False
    peer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        tl.REAP_STATE_DIR.mkdir(parents=True, exist_ok=True)
        marker = tl._run_marker_path(name, peer.pid)
        marker.write_text(f"{peer.pid}\n")
        assert tl._peer_run_is_live(name) is True, "a live peer run was not seen"
        assert tl.main(["run", "--", "-q", "tests/"]) == 0
        assert reaped == [], "a live peer run's container was removed"
        assert "another `run` holds this worktree" in capsys.readouterr().err
        # …and a STALE marker (the peer was killed) does not block forever.
        peer.kill()
        peer.wait(timeout=10)
        assert tl._peer_run_is_live(name) is False
        tl.main(["run", "--", "-q", "tests/"])
        assert reaped == [1], "a stale marker permanently disabled the hook"
    finally:
        if peer.poll() is None:
            peer.kill()
            peer.wait(timeout=10)


def test_an_UNREADABLE_peer_state_fails_CLOSED(lane, monkeypatch, capsys):
    """`None` is falsy — a truthiness test re-opens the hole (#10813 review P2)."""
    _stub_start(monkeypatch, port=16412)
    reaped: list = []
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(0, "", ""))
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    monkeypatch.setattr(tl, "_peer_run_is_live", lambda _n: None)
    assert tl.main(["run", "--", "-q", "tests/"]) == 0
    assert reaped == [], "an unreadable peer state reaped anyway"


def test_the_hook_NEVER_removes_a_container_this_run_REUSED(lane, monkeypatch,
                                                            capsys):
    """The `uri` route creates the container and writes no `run` marker.

    A `run` that finds it already up must leave it alone: the previous hook
    removed it mid-suite and the `uri` consumer reported connection errors as test
    verdicts (#10813 review P1). Reuse is the normal case for a lane that started
    its DB with `uri` and then ran pytest.
    """
    _stub_start(monkeypatch, port=16412)
    monkeypatch.setattr(tl, "_LAST_START_CREATED", False)
    reaped: list = []
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(0, "", ""))
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    assert tl.main(["run", "--", "-q", "tests/"]) == 0
    assert reaped == [], "a REUSED container was removed"
    assert "REUSED it" in capsys.readouterr().err


def test_the_box_wide_opt_out_ALSO_keeps_the_container(lane, monkeypatch, capsys):
    """`TORTOISE_TEST_LANE_REAP=0` gates `start()`'s sweep — it must gate the hook
    too, or a lane that disabled automatic reaping on a box still loses its
    container at the end of every `run` (#10813 review P2)."""
    _stub_start(monkeypatch, port=16412)
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "0")
    reaped: list = []
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(0, "", ""))
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    assert tl.main(["run", "--", "-q", "tests/"]) == 0
    assert reaped == [], "the box-wide opt-out did not reach the exit hook"


def test_a_FAILING_reap_never_changes_the_runs_verdict(lane, monkeypatch, capsys):
    """`stop()` raises `SystemExit` for the unremovable/zombie cases. A hook that
    let that escape would replace a real pytest result with a traceback, and a
    lane would report a removal problem as a test failure."""
    _stub_start(monkeypatch, port=16412)
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(0, "", ""))

    def boom():
        raise SystemExit("could not remove it")

    monkeypatch.setattr(tl, "stop", boom)
    assert tl.main(["run", "--", "-q", "tests/"]) == 0
    err = capsys.readouterr().err
    assert f"could not reap {LANE_FIXTURE_NAME} on exit" in err
    assert "container_reaper.py" in err      # names the backstop that will


def test_run_names_the_target_BEFORE_the_child_starts(
        lane, monkeypatch, capsys):
    """The resolved lane is on stderr before pytest runs, so a run that is killed
    mid-flight still leaves its target in whatever log it was redirected to.

    The ORDER is the assertion: stderr is read from INSIDE the stub, at the
    moment the child would have started. Reading it afterwards would pass even if
    the line were printed only after the child returned — which is exactly the
    case a killed run cannot report.
    """
    _stub_start(monkeypatch, port=16406)
    at_child_start: list = []

    def fake_run(argv, env=None, **kwargs):
        at_child_start.append(capsys.readouterr().err)
        return _R(0, "", "")

    monkeypatch.setattr(tl.subprocess, "run", fake_run)
    # `--keep-container`: this test counts CHILD invocations, and the exit hook
    # reaches docker through the same `subprocess.run` seam it just faked — so
    # the reap would register as a second "child". The hook's own behaviour is
    # pinned by the tests below.
    tl.main(["run", "--keep-container", "--", "-q"])
    assert len(at_child_start) == 1
    assert LANE_FIXTURE_NAME in at_child_start[0]
    assert "TORTOISE_DB_URI=docker://:@127.0.0.1:16406/" in at_child_start[0]


def test_flag_only_argv_still_targets_the_SUITE(lane, monkeypatch, capsys):
    """A FLAG-ONLY call must not fall back to rootdir collection.

    `run -- -k foo` / `run -- --collect-only -q` carry flags but no target, so
    a default applied only to an EMPTY argv would hand pytest a pathless command
    and pytest would collect from the ROOTDIR (no `testpaths` in pyproject.toml)
    — measured at this head: `42099 tests collected, 1 error`, `Interrupted: 1
    error during collection` on `graph-scripts/test_6707_shock.py`, exit 2, i.e.
    zero of the suite's tests and no verdict. The default is therefore applied
    whenever no token is a TARGET, and the call says so on stderr.
    """
    _stub_start(monkeypatch)
    for argv in (["run", "--", "-k", "foo"],
                 ["run", "--", "--collect-only", "-q"],
                 ["run", "--", "--timeout=900"]):
        seen = _capture_run(monkeypatch)
        tl.main(argv)
        assert seen["argv"] == [sys.executable, "-m", "pytest",
                                "tests/", *argv[2:]], argv
        assert "adding `tests/`" in capsys.readouterr().err, argv


def test_a_PATH_SHAPED_OPTION_VALUE_is_not_a_target(lane, monkeypatch, capsys):
    """The fix for the fix: an option EATS the next token.

    `--deselect tests/x.py::t`, `-k tests`, `-W error::DeprecationWarning` all
    carry a token that LOOKS like a path but is a flag VALUE. Reading it as a
    target suppressed the default and handed pytest a pathless command — the
    same "zero tests, no verdict" wall, one level deeper. None of these may
    suppress `tests/`.
    """
    _stub_start(monkeypatch)
    for argv in (
        ["run", "--", "--deselect", "tests/test_guard.py::test_x"],
        ["run", "--", "-k", "tests"],
        ["run", "--", "-W", "error::DeprecationWarning"],
        ["run", "--", "-k", "test_guard.py"],
    ):
        seen = _capture_run(monkeypatch)
        tl.main(argv)
        assert seen["argv"] == [sys.executable, "-m", "pytest",
                                "tests/", *argv[2:]], argv
        assert "adding `tests/`" in capsys.readouterr().err, argv


def test_an_explicit_target_is_never_retargeted(lane, monkeypatch, capsys):
    """A caller who NAMED a target keeps it — a file, a node id, a subdirectory.

    The documented spelling puts the path FIRST, where nothing can eat it.
    """
    _stub_start(monkeypatch)
    for argv in (
        ["run", "--", "tests/test_guard.py", "-q"],
        ["run", "--", "tests/test_guard.py::test_x"],
        ["run", "--", "tests/eval"],
    ):
        seen = _capture_run(monkeypatch)
        tl.main(argv)
        assert seen["argv"] == [sys.executable, "-m", "pytest", *argv[2:]], argv
        assert "adding `tests/`" not in capsys.readouterr().err, argv


def test_a_bare_boolean_flag_before_a_path_over_runs_rather_than_under_runs(
        lane, monkeypatch, capsys):
    """The ONE documented asymmetry: a path after a boolean flag is read as that
    flag's value, so `tests/` is appended and the run is a SUPERSET (announced).

    This is the right way round and must stay pinned: over-running costs wall
    time, under-running costs the verdict — and a run that tested nothing reads
    exactly like a run where nothing failed.
    """
    _stub_start(monkeypatch)
    seen = _capture_run(monkeypatch)
    tl.main(["run", "--", "-q", "tests/test_guard.py"])
    assert seen["argv"] == [sys.executable, "-m", "pytest",
                            "tests/", "-q", "tests/test_guard.py"]
    assert "adding `tests/`" in capsys.readouterr().err


def test_bare_run_targets_the_SUITE_not_the_rootdir(lane, monkeypatch):
    """`run` with no pytest argv means `tests/` — the help text's own promise.

    With NO path pytest collects from the ROOTDIR (there is no `testpaths` in
    pyproject.toml), which sweeps `validation/`, `graph-scripts/` and
    `integrations/tests/` and dies on a module-level assertion there: a run that
    tests none of the suite and exits 2. Both spellings a caller can type for
    "the default" must land on `tests/`.
    """
    _stub_start(monkeypatch)
    for argv in (["run"], ["run", "--"]):
        seen = _capture_run(monkeypatch)
        tl.main(argv)
        assert seen["argv"] == [sys.executable, "-m", "pytest", "tests/"], argv


def test_the_export_REDIRECTS_every_env_name_live_utils_resolves(monkeypatch):
    """The CONTRACT, not the dict: the names `tools/test_lane.py` exports must be
    the names `tests/_live_utils.py` actually resolves.

    This is the regression that produced the phantom red. `_live_utils` gained a
    passwordless-port axis in #6673 (`_LEGACY_PORT_ENV`), `lane_seam_env` was
    never revisited, and no test could see the drift — so the export dict stayed
    green while the legacy sites dialled 16379.

    The enumeration is DERIVED from `_live_utils`, and the EXPECTATION is written
    out beside it, so a FIFTH axis reds here first with a message that forces the
    question "does the private lane have to export it?". Writing the expectation
    out is the mechanism, not a contradiction: the point is that it is an
    expectation a new axis BREAKS, instead of three names asserted against a
    three-name export (which is what stayed green through #6673).

    `_HOST_ENV` is deliberately NOT exported (`_live_utils.service_host()` must
    keep following its own seam), so it is the one named exclusion rather than
    an absent assertion.
    """
    env = tl.lane_seam_env(16406, "tortoise_test_matrix")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert _live_utils.docker_port() == 16406
    assert _live_utils.legacy_port() == 16406, (
        "without the legacy export this resolves 16379 and a lane's "
        "`legacy_uri()` site dials a port nothing serves")
    assert _live_utils.docker_password() == ""
    assert ":16406/" in _live_utils.legacy_uri("g")
    resolved = {name for name in vars(_live_utils) if name.endswith("_ENV")}
    assert resolved == {"_PORT_ENV", "_LEGACY_PORT_ENV", "_PASSWORD_ENV",
                        "_HOST_ENV"}, (
        "`_live_utils` gained an env axis — decide whether the private lane must "
        f"export it, then update this pin: {sorted(resolved)}")
    assert {getattr(_live_utils, name) for name in resolved} - {
        _live_utils._HOST_ENV} <= set(env)
    assert _live_utils._HOST_ENV not in env


def test_run_and_up_emit_the_SAME_four_seam_vars(lane, monkeypatch, capsys):
    """`up` (printed for `eval`) and `run` (exported to the child) come from ONE
    definition. If they could drift, a lane that eval-ed `uri` and a lane that
    ran `run` would be testing against different variables — the split this
    function exists to remove.
    """
    _stub_start(monkeypatch, port=16406)
    seen = _capture_run(monkeypatch)
    tl.main(["run", "--", "-q"])
    child_env = seen["env"]

    monkeypatch.setattr(tl, "start", lambda _p=None: (LANE_FIXTURE_NAME, 16406))
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 0)
    tl.main(["up"])
    printed = capsys.readouterr().out.splitlines()
    exports = dict(
        line[len("export "):].split("=", 1) for line in printed
        if line.startswith("export ")
    )
    # Strip the shell quoting `up` prints for `eval`.
    assert {k: v.strip("'") for k, v in exports.items()} == {
        k: child_env[k] for k in exports
    }
    assert len(exports) == 4


def test_the_default_target_cannot_itself_be_eaten_by_an_option(
        lane, monkeypatch, capsys):
    """The default is PREPENDED, so no option can swallow it.

    The rule the classification rests on is "an option eats the next token", and
    a default APPENDED at the end is subject to that same rule. Measured in
    review: `run -- -q -k "$EXPR"` with EXPR unset expands to a trailing `-k`
    (every bash tool call is a FRESH SHELL, so this is a live shape, not a typo),
    the appended `tests/` became -k's VALUE, and pytest got no positional target
    at all — 60/42103 collected, `Interrupted: 1 error during collection` from
    `graph-scripts/`, exit 2 with zero of the suite run, while stderr claimed the
    suite was added. In FIRST position nothing can consume it.
    """
    _stub_start(monkeypatch)
    seen = _capture_run(monkeypatch)
    tl.main(["run", "--", "-q", "-k"])
    assert seen["argv"] == [sys.executable, "-m", "pytest", "tests/", "-q", "-k"]
    assert seen["argv"][3] == "tests/"        # the target is the first pytest arg
    assert "adding `tests/`" in capsys.readouterr().err


# ── OWNERSHIP IS STAMPED INTO THE CONTAINER, not only into its name ────────
#
# The name is `fdb-lane-<sha1(worktree path)[:10]>` — a one-way digest of the
# very path teardown DELETES, so `_worktree_slugs()` cannot resolve it once
# `git worktree remove`/prune drops the entry. The container then leaves the
# "owner determined" population and joins the INDETERMINATE one, whose default
# #8180's owner has not ruled on. The label pair stamped at creation survives
# that teardown, so the container stays DETERMINED — and it can only SPARE a
# container or move it onto the one proof the ruling already authorises armed.

def _labelled_row(name, state, owner):
    """A `docker ps` row shaped exactly like `reap_orphans()`'s projection:
    name, state, ``LABEL_OWNER``, ``LABEL_SLUG``.

    The slug is DERIVED from ``owner`` the way `_label_args()` writes it, and
    callers derive ``name`` from the same path — because that is the ONLY pair
    this tool produces. A hand-written slug would exercise a shape that never
    occurs and would hide a broken corroboration.
    """
    return f"{name}\t{state}\t{owner}\t{tl.slug_for(owner)}"


def test_the_label_writer_cannot_read_the_root_itself():
    """The single-read invariant is only true if `_label_args` cannot read the
    root itself: a REQUIRED `root` parameter is what makes "ONE `repo_root()`
    read" a property of the code instead of a comment.

    #10712 review P2: asserting only `list(parameters) == ["root"]` does not
    test requiredness — `def _label_args(root=None)` (which re-reads when it is
    None) passes that assertion and restores the defect. The DEFAULT is the
    thing that has to be absent, so the default is what is asserted."""
    sig = inspect.signature(tl._label_args)
    assert list(sig.parameters) == ["root"]
    assert sig.parameters["root"].default is inspect.Parameter.empty, (
        "a default (even None) lets `_label_args` re-read `repo_root()` itself, "
        "which is what let the label name a different worktree than the name")


def test_the_NAME_and_the_ownership_LABEL_come_from_ONE_root_read(monkeypatch,
                                                                 tmp_path):
    """ONE degraded `repo_root()` read must not weld a container shut forever.

    `repo_root()` has a documented PER-CALL degraded mode (`Path.cwd()` on a git
    timeout — the overloaded hosts this tool exists for). The container NAME is
    `slug_for(root)` and the label pair is `<root>` + `slug_for(root)`; while
    those came from TWO reads one of them could degrade, and the label then names
    a different worktree than the container. `_trusted_label_owner()` vetoes that
    pair as non-evidence, and after the worktree is gone the name does not
    resolve either — the #8180 leak, re-entered through the door built to close
    it. So this flips the root BETWEEN calls, which is what a degraded read looks
    like from inside `start()`.
    """
    first, second = tmp_path / "read-one", tmp_path / "read-two"
    first.mkdir()
    second.mkdir()
    reads: list[Path] = []

    def flipping_root():
        reads.append(first if not reads else second)
        return reads[-1]

    monkeypatch.setattr(tl, "repo_root", flipping_root)
    calls: list = []

    def fake(*a, **k):
        calls.append(a)
        if a[0] == "inspect":
            return _R(0, "absent\n", "")
        if a[0] == "run":
            return _R(0, "deadbeef\n", "")
        if a[0] == "port":
            return _R(0, "0.0.0.0:16390\n", "")
        return _R(0, "PONG\n", "")

    monkeypatch.setattr(tl, "_docker", fake)
    name, _port = tl.start(port=16390)

    assert len(reads) == 1, (
        f"`start()` must derive the container identity from ONE `repo_root()` "
        f"read, not {len(reads)}: {reads}")
    argv = list(next(c for c in calls if c[0] == "run"))
    labels = {argv[i + 1].split("=", 1)[0]: argv[i + 1].split("=", 1)[1]
              for i, a in enumerate(argv) if a == "--label"}
    assert labels.get(tl.LABEL_OWNER) == str(first.resolve()), labels
    assert labels.get(tl.LABEL_SLUG) == tl.slug_for(first), labels
    assert labels.get(tl.LABEL_CREATED), labels
    # The pair must describe the SAME worktree the NAME came from, and the slug
    # is what corroborates the owner label to the container it is stamped on.
    assert name == tl.container_name(labels[tl.LABEL_SLUG]), (name, labels)


def test_an_ownership_LABEL_makes_a_DEREGISTERED_worktree_reapable_ON_PROOF(
        monkeypatch, tmp_path):
    """THE LEAK, closed at the root — without firing on a live database.

    `git worktree remove` destroys the only record `_worktree_slugs()` has, so
    the container used to be INDETERMINATE — reported and kept, for days. The
    label says which worktree created it, so the owner is DETERMINED and GONE,
    which is the ownership proof #8180 authorises armed.

    ⛔ That proof is about OWNERSHIP, not about USE (#10841 review, P1). A lane
    container OUTLIVES its worktree by design, so a RUNNING container whose
    worktree directory was deleted can still have a pytest running against the
    URI it exported — and the promotion out of the report-only INDETERMINATE
    bucket must not turn into a bare `rm -f`. So the label buys the SECOND
    proof ladder the INDETERMINATE arm already demands (docker's own TERMINAL
    state, or no attached client across `OWNER_GRACE_H`), never a removal on
    ownership alone. One arm per rung, in order, and the removal is LAST.
    """
    gone = tmp_path / "deleted-worktree"
    name = tl.container_name(tl.slug_for(gone))
    assert not gone.exists()

    def sweep(state, busy, gap):
        removed: list = []
        touched: list = []

        def fake(*a, **k):
            if a[0] == "ps":
                return _R(0, _labelled_row(name, state, gone), "")
            if a[0] == "rm":
                removed.append(a)
            return _R(0, "", "")

        monkeypatch.setattr(tl, "_docker", fake)
        # git answered: nothing claims this container any more (the teardown).
        monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
        monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
        monkeypatch.setattr(tl, "_termination_report", lambda _n, live: "")
        monkeypatch.setattr(tl, "_container_in_use", lambda _n: busy)
        monkeypatch.setattr(tl, "_unused_gap", lambda _n: gap)
        monkeypatch.setattr(tl, "_touch_lease", touched.append)
        return removed, "\n".join(tl.reap_orphans()), touched

    # 1. A live client: the database is being used RIGHT NOW by whatever pytest
    #    the deleted worktree left running. Deleting it is the module's stated
    #    worst bug, and it is the exact pre-fix behaviour this arm pins AGAINST.
    removed, out, touched = sweep("running", True, None)
    assert removed == [], out
    assert "HAS an active client" in out, out
    assert touched == [name], (touched, out)   # observed use refreshes the lease

    # 1b. An UNREADABLE client answer is not "no client". `busy is None` means the
    #     probe could not ask, so the leg must report and stop — the same
    #     fail-closed direction the INDETERMINATE arm takes, with its own
    #     message. P3 from the #10841 review: the sweep matrix covered True,
    #     False+no-gap, False+short-gap, False+full-gap and `exited`, but not
    #     `None`, so this branch (and its wording) had no pin at all.
    #     Mutation: fold `None` into the `if busy:` / `if gap_h is None:` path —
    #     with no gap either it reports "first sighting" instead, and this fails.
    removed, out, touched = sweep("running", None, None)
    assert removed == [], out
    assert "could not be asked whether a client is using it" in out, out
    assert "fail-closed" in out, out
    assert touched == [], (touched, out)   # an unreadable answer is not observed use

    # 2. No client, but nothing observed unused yet — a lane between steps.
    removed, out, _ = sweep("running", False, None)
    assert removed == [], out
    assert "first sighting" in out, out

    # 3. A client-free gap shorter than the grace is still not proof.
    removed, out, _ = sweep("running", False, 0.5)
    assert removed == [], out
    assert "0.5h" in out and "grace" in out, out

    # 4. …and now BOTH proofs hold: owner gone AND no client across a full
    #    grace. This is the removal the label earns.
    removed, out, _ = sweep("running", False, 5.0)
    assert [c[1:3] for c in removed] == [("-f", name)], (removed, out)
    assert "owning worktree" in out and "is gone" in out, out
    assert "served no client for 5.0h" in out, out

    # 5. Docker's own state is proof on its own: a stopped container cannot be a
    #    live client (`docker exec` into it always fails), so it needs no gap.
    removed, out, _ = sweep("exited", True, None)
    assert [c[1:3] for c in removed] == [("-f", name)], (removed, out)
    assert "owning worktree" in out and "is gone" in out, out


def test_a_LABEL_whose_SLUG_disagrees_with_the_container_is_not_evidence(
        monkeypatch, tmp_path):
    """A label is a fact written by SOMEONE — a predecessor version, a copied
    `docker run`, a hand-written `--label` — so it is an ownership record only
    when it corroborates the container it sits on. Here it names a LIVE path that
    is not this container's origin: believing it would assert "its owning
    worktree exists" about a container that path never owned, and keep it
    forever. It is reported and IGNORED instead.
    """
    live = tmp_path / "someone-elses-clone"
    live.mkdir()
    gone = tmp_path / "this-containers-real-owner"
    name = tl.container_name(tl.slug_for(gone))       # the container's real id
    removed: list = []

    def fake(*a, **k):
        if a[0] == "ps":
            # Self-consistent pair — but for a DIFFERENT worktree.
            return _R(0, _labelled_row(name, "running", live), "")
        if a[0] == "rm":
            removed.append(a)
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    out = tl.reap_orphans()
    joined = "\n".join(out)
    assert "not evidence" in joined, out
    assert "owning worktree exists" not in joined, (
        f"the label must not be reported as this container's owner: {out}")
    # Ignored is not believed, and it is not a deletion either: with no name
    # record and no trusted label, the owner is INDETERMINATE, which is the
    # DEFAULT-report population — untouched, exactly as on main.
    assert removed == [], out
    assert "report only" in joined, out


def test_a_corroborated_label_whose_owner_EXISTS_beats_the_age_backstop(
        monkeypatch, tmp_path):
    """The other direction: a labelled container whose worktree is STILL THERE is
    DETERMINED and PRESENT, so the INDETERMINATE age arm can never reach it —
    however ancient the container is. Without the label it is only a candidate
    for that arm; with it, the container is spared by OWNERSHIP, not by age.

    The age arm's deletion is ARMED here (`OWNER_INDETERMINATE_REAP_ENV=1`), so
    "not removed" cannot be explained by the report-only default: the same
    fixture without the label reaches `rm -f`. And the probe seams are stubbed
    rather than left to the host — a running container must still be ASKED
    whether a client is attached (an unreadable probe keeps it for the wrong
    reason, which is how this assertion could pass on nothing), and `/proc` is a
    platform surface a unit test must not depend on.

    Mutation: stop consulting the label (drop the `_trusted_label_owner` branch
    in `reap_orphans`) and the container is REMOVED by the armed age arm."""
    live = tmp_path / "live-lane"
    live.mkdir()
    removed: list = []
    name = tl.container_name(tl.slug_for(live))

    def fake(*a, **k):
        if a[0] == "ps":
            return _R(0, _labelled_row(name, "running", live), "")
        if a[0] == "rm":
            removed.append(a)
        return _R(0, "", "")

    monkeypatch.setattr(tl, "_docker", fake)
    monkeypatch.setattr(tl, "_worktree_slugs", lambda: {})
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-mine")
    monkeypatch.setattr(tl, "_started_epoch", lambda _n: 0.0)      # ancient
    # A live lane's container on the `start()`-shape evidence: no process holds
    # it, no client is attached, and it has been unused for longer than either
    # bound. Every one of those is stubbed, so the ONLY thing keeping it is the
    # label resolving to a worktree that exists.
    monkeypatch.setattr(tl, "_live_lane_paths", lambda: set())
    monkeypatch.setattr(tl, "_container_in_use", lambda _n: False)
    monkeypatch.setattr(tl, "_unused_gap", lambda _n: 40.0)
    monkeypatch.setenv(tl.OWNER_INDETERMINATE_REAP_ENV, "1")   # age arm ARMED
    out = tl.reap_orphans()
    joined = "\n".join(out)
    assert removed == [], out
    # The DETERMINED-and-PRESENT arm reports the OWNER worktree, not the clock.
    assert "its owning worktree still EXISTS" in joined, out
    assert str(live) in joined, out
    # …and the INDETERMINATE arm — the only bucket this PR's label can move a
    # container OUT of — never sees it.
    assert "no worktree in this clone names it" not in joined, out


def test_the_PEER_marker_exists_BEFORE_start_runs(lane, monkeypatch):
    """The marker has to cover `start()`, not only the child.

    `start()` sweeps orphans, reads state, resolves image/port and may wait
    `READY_TIMEOUT` for the first PING. A peer `run` inside that window holds no
    marker and no socket, so a concurrently EXITING run sees "no peer, nothing
    attached" and `docker rm -f`s the container `start()` just handed to it; the
    peer then reports connection errors as test verdicts (#10813 review P2-2).

    The assertion is the ORDER, observed from inside `start()` itself: if the
    marker is opened after `start()` returns, this fails with nothing found.

    Mutation: hoist `name, port = start(...)` back above `with _run_marker(name)`
    and `during_start` is empty.
    """
    _stub_start(monkeypatch)
    found: list = []
    real_start = tl.start

    def start_that_looks(_p=None):
        found.extend(p.name for p in tl.REAP_STATE_DIR.glob("*.run.*"))
        return real_start(_p)

    monkeypatch.setattr(tl, "start", start_that_looks)
    _capture_run(monkeypatch)
    assert tl.main(["run", "--", "-q", "tests/"]) == 0
    mine = f"{LANE_FIXTURE_NAME}.run.{__import__('os').getpid()}"
    assert mine in found, (
        f"no run marker existed while start() ran (saw {found}); a peer that "
        f"is inside start() cannot be seen, so an exiting run may remove the "
        f"container it was just handed")


def test_a_run_whose_container_DIED_is_still_reaped(lane, monkeypatch, capsys):
    """A container docker reports as TERMINAL cannot have a client, so the hook
    must not ask — and must not treat the unanswerable question as "in use".

    `_container_in_use` answers `None` for anything `docker exec` cannot reach,
    and the hook keeps on `None` (correctly: unreadable is not "unused"). But a
    container that died during the run — the OOM-kill population #7357 documents
    — is exactly what the hook should clean up, and asking it would answer
    `None` and leave it behind for a timer that withholds proof 2 while this
    worktree still exists (#10813 review P2-3).

    The sweep already states this rule ("a TERMINAL one cannot have one, so the
    probe is skipped"); this pins that the hook mirrors it."""
    _stub_start(monkeypatch)
    reaped: list = []
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(0, "", ""))
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")

    def _never(*_a, **_k):     # pragma: no cover - asserted not to run
        raise AssertionError("a TERMINAL container was probed for clients")

    monkeypatch.setattr(tl, "_container_in_use", _never)
    assert tl.main(["run", "--", "-q", "tests/"]) == 0
    assert reaped == [1], "a container that DIED during the run was left behind"
    assert "reaping" in capsys.readouterr().err


def test_pid_reuse_is_decided_by_WHEN_the_process_started_not_by_an_age():
    """The three directions of `_pid_started_after`, on a REAL child process.

    The direction that matters is the one that must NOT fire: declaring a genuine
    peer's pid reused removes a LIVE run's container (#10813 review P3-8). The
    other two are the reasons the rule exists — a marker older than the process
    that now wears its pid is stale, and a question the platform cannot answer
    must not be read as an answer.
    """
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        import time as _time
        _time.sleep(0.2)
        now = _time.time()
        # A marker just written by THIS live peer: it started before it.
        assert tl._pid_started_after(child.pid, now) is False
        # A marker two hours OLD: this process is too young to have written it.
        assert tl._pid_started_after(child.pid, now - 7200) is True
        # Unreadable (no such pid): unknown, which the caller must not read as
        # stale — only a positive `True` clears a marker.
        assert tl._pid_started_after(999_999, now) is None
        # A clock that disagrees the other way must not manufacture "reused".
        assert tl._pid_started_after(child.pid, now + 99999) is False
    finally:
        child.kill()
        child.wait()


def test_an_EMPTY_opt_out_disables_reaping(monkeypatch):
    """`TORTOISE_TEST_LANE_REAP=` is what `export X=${SOMETHING_UNSET}` produces.

    `start()` used to enable only on a literal `"1"`, so an empty value meant OFF;
    reading it as ON would silently ARM a deletion switch for an operator whose
    "off" was spelled with an empty value — the fail-open direction, on the one
    variable whose job is to disarm this tool (#10813 round-3 review P3-4).

    Mutation: drop `""` from `_REAP_OFF` and this reds.
    """
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "")
    assert tl._reap_enabled() is False
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "   ")
    assert tl._reap_enabled() is False          # whitespace-only is the same word
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "1")
    assert tl._reap_enabled() is True
    monkeypatch.delenv("TORTOISE_TEST_LANE_REAP")
    assert tl._reap_enabled() is True


def test_a_DISAGREEING_container_name_refuses_the_run(lane, monkeypatch, capsys):
    """The marker opens before `start()`, so the two names must be the SAME one.

    `name` comes from `_lane_identity()` and `start()` derives the container
    again; if a `repo_root()` read degrades between them the two records describe
    different worktrees, and the peer guard would then protect the wrong
    container. Refusing is the only honest answer (#10813 review P2-2).

    Mutation: delete the `started_name != name` arm and this run proceeds.
    """
    _stub_start(monkeypatch)
    reaped: list = []
    monkeypatch.setattr(tl.subprocess, "run", lambda *a, **k: _R(0, "", ""))
    monkeypatch.setattr(tl, "stop", lambda: (reaped.append(1), "removed")[1])
    monkeypatch.setattr(tl, "start", lambda _p=None: ("fdb-lane-somethingelse", 16412))
    with pytest.raises(SystemExit) as exc:
        tl.main(["run", "--", "-q", "tests/"])
    assert "refusing to run" in str(exc.value)
    assert reaped == [], "a run that could not name its own container reaped anyway"
    assert not list(tl.REAP_STATE_DIR.glob("*.run.*")), "the marker was left behind"


def test_a_start_that_RAISES_stamps_no_lease(lane, monkeypatch):
    """The `finally` now runs on paths it never reached before (the marker opens
    before `start()`), so it must not re-stamp a lease for a container that was
    never created — a write into the real `~/.pi/agent/state/lane-reap/` naming
    nothing (#10813 review P3-4).

    Mutation: drop `and started` from the `kept and started` guard and this reds
    (the box-wide opt-out makes `kept` True).
    """
    monkeypatch.setenv("TORTOISE_TEST_LANE_REAP", "0")     # keeps `keep` True
    leases: list = []
    monkeypatch.setattr(tl, "_touch_lease", lambda n: leases.append(n))

    def _boom(_p=None):
        raise SystemExit("test-lane: docker is not answering")

    monkeypatch.setattr(tl, "start", _boom)
    with pytest.raises(SystemExit):
        tl.main(["run", "--", "-q", "tests/"])
    assert leases == [], f"a lease was stamped for a container start() never made: {leases}"


def test_a_live_peer_older_than_the_age_bound_is_STILL_a_peer(lane, monkeypatch):
    """The age is a FALLBACK for a platform that cannot answer, not a second vote.

    `_pid_started_after` can PROVE a pid was not reused (the process started
    before the marker was written). An unconditional age term let the clock
    override that proof, so a genuine live `run` older than 24 h was declared
    stale, its marker unlinked, and an exiting peer's hook then `docker rm -f`'d
    the container under it — on exactly the population this change exists for (a
    wedged, long-lived lane). #10813 round-3 review P2-1.

    The two axes are separated on purpose: `_pid_started_after`'s own answers are
    pinned on a REAL child (see
    `test_pid_reuse_is_decided_by_WHEN_the_process_started_not_by_an_age`), so
    here it is stubbed to the one answer that matters — a POSITIVE "not reused" —
    while the marker is 25 h old. A 25 h-old marker beside a 1 s-old process
    would be pid reuse, which is why this cannot be built from a live child.

    Mutation: make `aged` unconditional again and this reds.
    """
    monkeypatch.setattr(tl, "_pid_started_after", lambda pid, when: False)
    monkeypatch.setattr(tl, "_pid_alive", lambda pid: True)
    tl.REAP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    marker = tl._run_marker_path(LANE_FIXTURE_NAME, 424242)
    marker.write_text("424242\n")
    old = time.time() - 25 * 3600
    os.utime(marker, (old, old))
    assert tl._peer_run_is_live(LANE_FIXTURE_NAME) is True, (
        "a peer the tool PROVED was not pid-reused was declared stale by the clock")
    assert marker.exists(), "a live peer's marker was deleted"


def test_a_marker_naming_pid_0_or_negative_is_not_a_live_peer(lane):
    """`os.kill(0, 0)` and `os.kill(-1, 0)` SUCCEED — they address a process
    GROUP, not a process — and `/proc/0` does not exist, so a marker named
    `<name>.run.0` (a truncated write, or a hand-edited state dir) read as "peer
    live" forever with no self-healing path. Same permanent-block class as pid
    reuse. #10813 round-3 review P3-5.

    Mutation: drop the `pid <= 0` arm and this reds (and the marker survives).
    """
    tl.REAP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    for bogus in (0, -1):
        marker = tl.REAP_STATE_DIR / f"{LANE_FIXTURE_NAME}.run.{bogus}"
        marker.write_text(f"{bogus}\n")
        assert tl._peer_run_is_live(LANE_FIXTURE_NAME) is False, bogus
        assert not marker.exists(), f"a pid-{bogus} marker blocked reaping forever"
