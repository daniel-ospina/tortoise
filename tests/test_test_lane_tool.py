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
    stderr only — the documented ``eval "$(uv run python tools/test_lane.py
    uri)"`` usage silently breaks if a diagnostic lands on stdout.

Where a function's real body is the safety property (``container_state``,
``_graph_count``, ``_container_publishes``, ``repo_root``), it is tested
DIRECTLY. Stubbing such a function to test its caller pins the caller and
leaves the property itself free to regress — review round 3 caught exactly
that: the suite stayed green with the ``absent``/``unknown`` classifier
reverted.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import test_lane as tl  # noqa: E402


class _R:
    """Just enough of a CompletedProcess for the docker seam."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture
def lane(monkeypatch):
    """Pin the lane's container name so tests do not depend on the CWD."""
    monkeypatch.setattr(tl, "lane_name", lambda: "fdb-lane-0123456789")
    return "fdb-lane-0123456789"


# ── the ownership guard (the part that must never be wrong) ────────────────

@pytest.mark.parametrize("name", [
    "fdb-lane-0123456789",
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
    """Review round 3, P1: the CLI was locked but `stop(name)`/`start(slug)`
    still accepted a peer's container name (`is_managed` passes it — a peer's
    name IS `fdb-lane-<sha1(path)[:10]>`). The fix is structural: the target is
    derived inside the function, so the only parameter left is a property of
    THIS lane. Re-adding a name/slug/image parameter fails here."""
    assert list(inspect.signature(tl.stop).parameters) == []
    assert list(inspect.signature(tl.start).parameters) == ["port"]
    assert list(inspect.signature(tl.lane_name).parameters) == []


def test_no_flag_can_target_another_lanes_container():
    """Review round 2: removing only `--name` left `--slug`, which still mapped
    to `fdb-lane-<peer>` (a peer's slug is a computable `sha1(path)[:10]`). Both
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
    """Round 3 P3, extended in round 4: `down --port 16390` exited 0, which
    reads as "down that port". A command that does not use a flag must reject
    it loudly. (`status --graph` IS used, and is asserted accepted below.)"""
    with pytest.raises(SystemExit) as exc:
        tl.main(argv)
    assert exc.value.code == 2


def test_status_still_accepts_the_flag_it_does_use(monkeypatch, capsys):
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 1)
    monkeypatch.setattr(tl, "_published_port", lambda _n: 16390)
    assert tl.main(["status", "--graph", "other_graph"]) == 0
    _out, err = capsys.readouterr()
    assert "other_graph" in err


def test_lane_name_is_derived_from_this_worktree(monkeypatch):
    monkeypatch.setattr(tl, "repo_root", lambda: "/tmp/wt-under-test")
    assert tl.lane_name() == tl.container_name(tl.slug_for("/tmp/wt-under-test"))


def test_repo_root_ignores_an_inherited_git_work_tree(tmp_path, monkeypatch):
    """Round 3, P2: `rev-parse --show-toplevel` honours `GIT_WORK_TREE`, so an
    inherited env var silently retargeted the tool at a peer worktree's
    container. The scrub is the fix, and this pins it.

    The decoy is a REAL repository: a non-existent path makes git fail and fall
    back to the CWD, which would let the unscrubbed code pass this test.
    """
    decoy = tmp_path / "decoy-worktree"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True)
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    assert tl.repo_root().resolve() == ROOT.resolve()
    assert tl.lane_name() == tl.container_name(tl.slug_for(ROOT))


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


def test_container_publishes_consults_stopped_containers(monkeypatch):
    """A stopped container still reserves its published host port, so the scan
    must ask for the full list (`-a`/`--all`); without it `pick_port` returns a
    port `docker run -p` then refuses."""
    seen = {}

    def _fake(*args, **kwargs):
        seen["args"] = args
        return _R(0, "fdb-lane-x  127.0.0.1:16390->6379/tcp", "")

    monkeypatch.setattr(tl, "_docker", _fake)
    assert tl._container_publishes(16390) is True
    assert set(seen["args"]) & {"-a", "--all"}, "must not scan running-only"
    assert tl._container_publishes(16490) is False


def test_published_port_reads_the_mapping_docker_reports(monkeypatch):
    """Round 4, P3: `_published_port` produces the port in the eval-ed URI and
    was stubbed at every call site, so its real body — the sole mechanism behind
    the "published, not requested" fix — was never exercised."""
    cases = [
        (_R(0, "127.0.0.1:16390\n", ""), 16390),
        (_R(0, "[::1]:16391\n", ""), 16391),      # IPv6 form, same suffix parse
        (_R(0, "", ""), None),                    # no mapping is not a port
        (_R(1, "", "No such container"), None),   # a failure is not a port
    ]
    for result, expected in cases:
        monkeypatch.setattr(tl, "_docker", lambda *a, _r=result, **k: _r)
        assert tl._published_port("fdb-lane-x") == expected


def test_docker_calls_time_out_instead_of_hanging(monkeypatch):
    """Round 4, P2: `subprocess.run` without a timeout can block forever, and a
    wedged daemon is this tool's expected failure mode. A timeout must come back
    as the same non-zero shape every caller already handles."""
    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1)

    monkeypatch.setattr(tl.subprocess, "run", _timeout)
    r = tl._docker("inspect", "fdb-lane-x")
    assert r.returncode == 1
    assert "timed out" in r.stderr
    # ... and the callers read that as "unknown", never as "absent".
    assert tl.container_state("fdb-lane-x") == "unknown"


def test_docker_absence_is_not_inferred_from_a_missing_binary(monkeypatch):
    def _missing(*_a, **_k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(tl.subprocess, "run", _missing)
    assert tl.container_state("fdb-lane-x") == "unknown"


def test_list_reports_a_failed_docker_ps_instead_of_claiming_none_exist(monkeypatch, capsys):
    """Round 3, P3: a failed `docker ps` yielded empty stdout, which was
    reported as "no fdb-lane-* containers" — absence inferred from a failure,
    the exact inference the rest of this module refuses to make."""
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "daemon down"))
    assert tl.main(["list"]) == 1
    _out, err = capsys.readouterr()
    assert "docker ps failed" in err
    assert "no fdb-lane-* containers" not in err


# ── port selection ─────────────────────────────────────────────────────────

def test_pick_port_skips_ports_already_taken(monkeypatch):
    monkeypatch.setattr(tl, "port_is_free", lambda p: p != tl.PORT_RANGE[0])
    monkeypatch.setattr(tl, "_container_publishes", lambda _p: False)
    assert tl.pick_port(*tl.PORT_RANGE) == tl.PORT_RANGE[0] + 1


def test_pick_port_skips_ports_published_by_a_container(monkeypatch):
    """A port can be free on the host yet already published (another lane's
    container), which would make `docker run -p` fail."""
    monkeypatch.setattr(tl, "port_is_free", lambda _p: True)
    monkeypatch.setattr(tl, "_container_publishes",
                        lambda p: p == tl.PORT_RANGE[0])
    assert tl.pick_port(*tl.PORT_RANGE) == tl.PORT_RANGE[0] + 1


def test_pick_port_raises_when_the_range_is_exhausted(monkeypatch):
    monkeypatch.setattr(tl, "port_is_free", lambda _p: False)
    monkeypatch.setattr(tl, "_container_publishes", lambda _p: False)
    with pytest.raises(SystemExit) as exc:
        tl.pick_port(16390, 16392)
    assert "no free port" in str(exc.value)


# ── the eval contract ──────────────────────────────────────────────────────

def test_uri_command_prints_only_the_export_line_on_stdout(monkeypatch, capsys):
    monkeypatch.setattr(tl, "start", lambda port=None, **k: ("fdb-lane-x", 16399))
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 0)
    rc = tl.main(["uri"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == "export TORTOISE_DB_URI='docker://:@127.0.0.1:16399/tortoise_test_matrix'\n"
    assert "fdb-lane-x" in err, "the human-readable diagnostic belongs on stderr"


def test_uri_command_says_unknown_rather_than_zero_graphs(monkeypatch, capsys):
    monkeypatch.setattr(tl, "start", lambda port=None, **k: ("fdb-lane-x", 16399))
    monkeypatch.setattr(tl, "_graph_count", lambda _n: None)
    assert tl.main(["uri"]) == 0
    _out, err = capsys.readouterr()
    assert "graphs=unknown" in err


def test_flags_are_accepted_after_the_subcommand(monkeypatch, capsys):
    """`uri --port N` is the natural spelling.

    Regression (#5084 self-review): the flags were declared on the ROOT parser,
    so post-subcommand flags died in argparse before reaching any guard.
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
    monkeypatch.setattr(tl, "_published_port", lambda _n: 16390)
    assert tl.main(["status"]) == 0
    _out, err = capsys.readouterr()
    assert "127.0.0.1:16390" in err
    assert ":0/" not in err, "a placeholder port must never be printed"


# ── start(): the port handed to the caller must be the PUBLISHED one ───────

def test_start_returns_the_published_port_not_the_requested_one(lane, monkeypatch):
    """Review P2-1: `-p 0:6379` lets Docker choose the port, so echoing the
    requested value would print a URI pointing at nothing."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "pick_port", lambda *a, **k: 16399)
    monkeypatch.setattr(tl, "_published_port", lambda _n: 16400)
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "PONG"))
    assert tl.start() == ("fdb-lane-0123456789", 16400)


def test_start_rejects_an_invalid_port_before_touching_docker(lane, monkeypatch):
    """The ordering IS the guard: validation must precede `container_state` (a
    docker call) and the stale-container `docker rm`, or an invalid value still
    reaches docker. Both seams are booby-trapped here, so this test fails if the
    check is ever moved below them again (VGATE caught exactly that)."""
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("docker was called before port validation")

    monkeypatch.setattr(tl, "container_state", _boom)
    monkeypatch.setattr(tl, "_docker", _boom)
    for bad in (0, -1, 65536):
        with pytest.raises(SystemExit) as exc:
            tl.start(port=bad)
        assert "not a valid TCP port" in str(exc.value)


def test_start_removes_only_a_container_this_tool_manages(lane, monkeypatch):
    """The stale-container `rm` must satisfy the ownership guard (a real
    refusal, not an `assert` — `python -O` strips those).

    `_docker` is stubbed: with the guard reverted this test must fail as an
    ASSERTION, not by reaching a real `docker rm` in the operator's daemon.
    """
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("docker was reached for an unmanaged container")

    monkeypatch.setattr(tl, "container_state", lambda _n: "exited")
    monkeypatch.setattr(tl, "is_managed", lambda _n: False)
    monkeypatch.setattr(tl, "_docker", _boom)
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "refusing to remove unmanaged" in str(exc.value)


def test_start_refuses_to_reuse_a_running_container_that_is_wedged(lane, monkeypatch):
    """Round 4, P3: the reuse path handed back a URI without checking the server
    answered, so a wedged long-lived container kept being reused indefinitely."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_published_port", lambda _n: 16390)
    monkeypatch.setattr(tl, "_wait_ready", lambda _n: False)
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "never answered PING" in str(exc.value)


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
    monkeypatch.setattr(tl, "_published_port", lambda _n: None)
    monkeypatch.setattr(tl, "_docker", _fake)
    with pytest.raises(SystemExit) as exc:
        tl.start()
    assert "publishes no port" in str(exc.value)
    assert ("rm", "-f", "fdb-lane-0123456789") in calls


def test_stop_surfaces_a_failed_removal(lane, monkeypatch):
    """Review P3: `stop` returned "removed" even when `docker rm` failed."""
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
