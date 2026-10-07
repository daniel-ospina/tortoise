"""#5084: `tools/test_lane.py` — the private per-lane FalkorDB.

These are pure unit tests: nothing here talks to Docker (every docker-touching
function is monkeypatched), because the properties that must hold are the
tool's CONTRACT, not Docker's behaviour:

  * it only ever acts on ``fdb-lane-*`` containers — the shared dev/test
    instances must not be removable by it under any argument (the fail-closed
    half of the design; the whole point, since a wrong `down` in the wrong
    lane would take out every other lane's test target), and
  * ``uri`` prints an ``eval``-able export line on stdout, with diagnostics on
    stderr only — the documented ``eval "$(uv run python tools/test_lane.py
    uri)"`` usage silently breaks if a diagnostic lands on stdout.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import test_lane as tl  # noqa: E402

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


def test_stop_refuses_an_unmanaged_name_and_never_calls_docker(monkeypatch):
    """The guard must fire BEFORE any docker call — a refusal that had already
    inspected (or worse, removed) the container would defeat the point."""
    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("stop() reached docker for a protected name")

    monkeypatch.setattr(tl, "container_state", _boom)
    for protected in ("falkordb", "falkordb-16379", "w6213-fdb"):
        with pytest.raises(SystemExit) as exc:
            tl.stop(protected)
        assert "refusing to remove" in str(exc.value)


def test_stop_reports_absent_for_a_managed_container_that_is_not_running(monkeypatch):
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    assert tl.stop("fdb-lane-0123456789") == "absent"


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
    monkeypatch.setattr(tl, "start", lambda slug, port, **k: ("fdb-lane-x", 16399))
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 0)
    rc = tl.main(["uri"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out == "export TORTOISE_DB_URI='docker://:@127.0.0.1:16399/tortoise_test_matrix'\n"
    assert "fdb-lane-x" in err, "the human-readable diagnostic belongs on stderr"


def test_flags_are_accepted_after_the_subcommand(monkeypatch, capsys):
    """`uri --port N` is the natural spelling.

    Regression (#5084 self-review): the flags were declared on the ROOT parser,
    so post-subcommand flags died in argparse before reaching any guard.
    """
    monkeypatch.setattr(tl, "start", lambda slug, port, **k: ("fdb-lane-x", port))
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 0)
    assert tl.main(["uri", "--port", "16401"]) == 0
    out, _err = capsys.readouterr()
    assert "127.0.0.1:16401" in out


def test_no_flag_can_target_another_lanes_container():
    """Review round 2: removing only `--name` left `--slug`, which still mapped
    to `fdb-lane-<peer>` (a peer's slug is a computable `sha1(path)[:10]`). Both
    overrides are gone, so neither spelling can name another lane's container."""
    for flag, value in (("--name", "fdb-lane-peer-slug"),
                        ("--slug", "peer-slug")):
        with pytest.raises(SystemExit) as exc:
            tl.main(["down", flag, value])
        assert exc.value.code == 2, f"argparse must reject {flag} (usage error)"


def test_target_name_is_always_derived_from_this_worktree(monkeypatch):
    monkeypatch.setattr(tl, "repo_root", lambda: "/tmp/wt-under-test")
    args = argparse.Namespace(graph=tl.DEFAULT_GRAPH)
    assert tl._target_name(args) == tl.container_name(tl.slug_for("/tmp/wt-under-test"))


def test_start_refuses_when_docker_state_is_unknown(monkeypatch):
    """A docker failure is not evidence that no container exists: guessing
    `absent` there would remove a container that may belong to another lane."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "unknown")

    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("docker was called despite an unknown state")

    monkeypatch.setattr(tl, "_docker", _boom)
    with pytest.raises(SystemExit) as exc:
        tl.start("slug")
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


def test_status_reports_the_published_port_not_the_requested_one(monkeypatch, capsys):
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

class _R:
    """Just enough of a CompletedProcess for the docker seam."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_start_returns_the_published_port_not_the_requested_one(monkeypatch):
    """Review P2-1: `-p 0:6379` lets Docker choose the port, so echoing the
    requested value would print a URI pointing at nothing."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "absent")
    monkeypatch.setattr(tl, "pick_port", lambda *a, **k: 16399)
    monkeypatch.setattr(tl, "_published_port", lambda _n: 16400)
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(0, "PONG"))
    assert tl.start("slug") == ("fdb-lane-slug", 16400)


def test_start_rejects_an_invalid_port_before_touching_docker(monkeypatch):
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
            tl.start("slug", port=bad)
        assert "not a valid TCP port" in str(exc.value)


def test_stop_surfaces_a_failed_removal(monkeypatch):
    """Review P3: `stop` returned "removed" even when `docker rm` failed."""
    monkeypatch.setattr(tl, "container_state", lambda _n: "running")
    monkeypatch.setattr(tl, "_docker", lambda *a, **k: _R(1, "", "boom"))
    with pytest.raises(SystemExit) as exc:
        tl.stop("fdb-lane-0123456789")
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
