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
    """`uri --port N` and `down --name <shared>` are the natural spellings.

    Regression (#5084 self-review): the flags were declared on the ROOT parser,
    so `down --name falkordb` died in argparse and never reached the ownership
    guard — the guard was there but unreachable from the CLI.
    """
    monkeypatch.setattr(tl, "start", lambda slug, port, **k: ("fdb-lane-x", port))
    monkeypatch.setattr(tl, "_graph_count", lambda _n: 0)
    assert tl.main(["uri", "--port", "16401"]) == 0
    out, _err = capsys.readouterr()
    assert "127.0.0.1:16401" in out

    with pytest.raises(SystemExit) as exc:
        tl.main(["down", "--name", "falkordb"])
    assert "refusing to remove" in str(exc.value)


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
