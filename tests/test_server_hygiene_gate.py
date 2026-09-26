"""#3634 Task 5 — the session-scoped E2E-7 gate on OWNED survivors.

Three properties are pinned here, and none is observable from the
whole-server graph count the gate it replaces used:

1. The ownership predicate (``_owned_survivors``). A preserved-but-journalled
   shared registry (``registry_*``) or the URI-path default graph must NOT
   count as a survivor — the ownership record is the only thing that makes a
   journalled name a leak, mirroring ``_sweep_drop``'s skips.
2. The capture-before-sweep ordering inside ``_server_graph_hygiene`` — the
   journal is DELETED by the sweep, so a name set read afterwards is empty
   and the gate would be vacuously green. The pin targets the GATE'S OWN
   capture (``journal_names = set(_read_journal())``), not merely some
   ``_read_journal`` call: the whole-server bound's ``journal_size =
   len(_read_journal())`` is a second read that must not satisfy the pin
   (#3634 Task 5 review P1-B).
3. The gate's own behaviour, driven hermetically through the real fixture
   generator with every server call faked: it RAISES on an owned survivor,
   stays silent when the own-sweep did not complete cleanly
   (``failed``/``error``/``skipped``), fires regardless of the leftover
   sweep's ``full_sweep`` flag (#3634 Task 5 review P1-A), and treats a
   probe failure as an infra skip rather than a leak (#3634 Task 5 review
   P1-C).

Reach (recorded, not a defect): the gate lives under ``if not others`` in
``tests/conftest.py`` — last-suite-standing only. This file does NOT and
cannot claim in-process observability of an actual leaking session; the
subprocess leg is skipped (see ``test_owned_survivors_*`` docstring in the
plan and the OVERRIDES comments on #3634).
"""
from __future__ import annotations

import ast
import contextlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

from tests._embedded import _owned_survivors  # noqa: E402

# ── The ownership predicate ────────────────────────────────────────────────

def test_owned_survivors_applies_the_ownership_predicate():
    """A preserved-but-journalled shared registry must NOT be a survivor."""
    journal = {"test_a", "registry_test_b_control_plane",
               "registry_control_plane", "registry_tortoise", "tortoise_test_matrix"}
    live = {"test_a", "registry_control_plane", "registry_tortoise", "tortoise_test_matrix"}
    assert _owned_survivors(journal, live, "tortoise_test_matrix") == {"test_a"}


def test_owned_survivors_ignores_foreign_graphs():
    assert _owned_survivors({"test_a"}, {"org_x", "t"}, None) == set()


# ── AST pin — the GATE'S capture must precede the sweep that deletes it ────
#
# AC4 is AST-pinned, NOT session-observed: the subprocess leg cannot seed a
# journal (tests/test_tripwire.py::_run_session pops
# TORTOISE_TEST_JOURNAL_FILE from the child env — `_CHILD_LANE_VARS`), so a
# live leaking-session test is unwritable, not merely unwritten. The reach of
# the gate itself (under `if not others` in the fixture, and NOT under the
# bound check's `full_sweep` condition) is recorded on #3634; these pins
# cover the ordering and placement the gate depends on.


def _fixture_body(name: str) -> ast.FunctionDef:
    tree = ast.parse((REPO / "tests" / "conftest.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"{name} not found in tests/conftest.py")


def _own_statement_calls(fn: ast.FunctionDef) -> list[ast.Call]:
    """Calls in the fixture's OWN statements — nested closures excluded.

    `_atexit_cleanup` (the abnormal-exit path) also calls
    `_session_end_own_sweep`, and it is DEFINED before the teardown capture.
    Including it would make this pin assert a false ordering for a path that
    never runs after a completed teardown; the pin is about the teardown path,
    which is the fixture's own statement sequence.
    """
    calls: list[ast.Call] = []
    for stmt in fn.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls.extend(n for n in ast.walk(stmt) if isinstance(n, ast.Call))
    return calls


def _callee(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _journal_names_capture(fn: ast.FunctionDef) -> ast.Assign | None:
    """The `journal_names = ...` assignment in the fixture's own statements.

    This is the GATE'S capture — the name set `_owned_survivors` compares
    against the live server. It is deliberately distinguished from the
    whole-server bound's `journal_size = len(_read_journal())` (a COUNT, and a
    read that may legitimately stay where it is): the pin must not be
    satisfiable by that other read (P1-B, Task 5 review).
    """
    for stmt in fn.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "journal_names"
                for t in stmt.targets):
            return stmt
    return None


def test_journal_capture_precedes_the_sweep_that_deletes_it():
    """A real ast.walk of the `_server_graph_hygiene` body.

    Discriminating property: the assignment whose TARGET is `journal_names`
    must contain the `_read_journal()` call AND lexically precede the
    `_session_end_own_sweep` call. The session-end sweep REMOVES the journal,
    so a name set read after it is empty and the gate is vacuously green.

    Falsified in a scratch copy (#3634 Task 5 review P1-B): (a) moving only
    `journal_names` after the sweep FAILS this pin, and (b) moving both reads
    after the sweep also FAILS; the old pin accepted BOTH because it matched
    any `_read_journal` call, including the bound's `journal_size` read.
    """
    fn = _fixture_body("_server_graph_hygiene")
    capture = _journal_names_capture(fn)
    assert capture is not None, (
        "no `journal_names = ...` assignment in _server_graph_hygiene — the "
        "E2E-7 gate would have no pre-sweep capture of the journal names")
    reads = [n for n in ast.walk(capture) if isinstance(n, ast.Call)
             and _callee(n) == "_read_journal"]
    assert reads, (
        "the `journal_names` assignment does not call _read_journal() — the "
        "name set is not the gate's pre-sweep capture")
    sweeps = [(c.lineno, c.col_offset) for c in _own_statement_calls(fn)
              if _callee(c) == "_session_end_own_sweep"]
    assert sweeps, "no _session_end_own_sweep call in _server_graph_hygiene"
    capture_pos = min((c.lineno, c.col_offset) for c in reads)
    assert capture_pos < min(sweeps), (
        "the `journal_names` name set is captured AFTER the session-end sweep "
        "that deletes the journal — the survivor gate would be vacuously green")


def _gate_raise(fn: ast.FunctionDef) -> ast.Raise:
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise) \
                and "owned journalled graph(s) survived" in ast.unparse(node):
            return node
    raise AssertionError("the E2E-7 gate Raise is not in the fixture")


def test_gate_raise_has_no_try_or_full_sweep_ancestor():
    """P1-A (Task 5 review): the gate is a SIBLING of the bound-check `if`, a
    direct child of `if not others`, and outside every `try`.

    Nested inside the bound-check's `if`, the gate inherited that `if`'s
    `full and full.get("full_sweep", False)` condition — disabled exactly when
    the leftover sweep failed or reported `full_sweep=False`, i.e. precisely
    when cleanup was incomplete and survivors are most likely. Inside any
    `try`, an `AssertionError` is swallowed and the gate is vacuous.
    """
    fn = _fixture_body("_server_graph_hygiene")
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    chain: list[ast.AST] = []
    node: ast.AST | None = _gate_raise(fn)
    while node is not None:
        chain.append(node)
        node = parents.get(node)

    assert not any(isinstance(n, ast.Try) for n in chain), (
        "the gate's Raise has a Try ancestor — AssertionError would be "
        "swallowed and the gate is vacuous")
    bad_full = [n for n in chain if isinstance(n, ast.If)
                and "full" in ast.unparse(n.test)]
    assert not bad_full, (
        "the gate's Raise is gated on the bound check's `full_sweep` "
        "condition — the gate is disabled exactly when cleanup was incomplete")
    others_if = [n for n in chain if isinstance(n, ast.If)
                 and "others" in ast.unparse(n.test)]
    flags_if = [n for n in chain if isinstance(n, ast.If)
                and "skipped" in ast.unparse(n.test)
                and "failed" in ast.unparse(n.test)
                and "error" in ast.unparse(n.test)]
    survivors_if = [n for n in chain if isinstance(n, ast.If)
                    and "survivors" in ast.unparse(n.test)]
    assert others_if, "the gate is no longer guarded by `if not others`"
    assert flags_if, "the gate's three-own-flag guard is gone"
    assert survivors_if, "the gate no longer tests `if survivors`"
    assert fn in chain and isinstance(chain[-1], ast.FunctionDef)


def test_gate_probe_is_wrapped_in_a_try():
    """P1-C (Task 5 review): `_live_graph_names` is the only server call on the
    teardown path that was not already guarded. It must sit inside a `try` so a
    connection/auth/maxmemory/stall failure prints-and-continues instead of
    reding the suite indistinguishably from a real leak."""
    fn = _fixture_body("_server_graph_hygiene")
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.body:
            for call in ast.walk(stmt):
                if isinstance(call, ast.Call) \
                        and _callee(call) == "_live_graph_names":
                    return
    raise AssertionError(
        "`_live_graph_names` is not wrapped in a try/except — a probe failure "
        "would propagate out of teardown and red the suite as if it leaked")


# ── The gate's behaviour, driven through the real fixture generator ────────
#
# Every server call the fixture makes is faked, so these tests are hermetic
# (no docker, no FalkorDB) and exercise the ACTUAL gate code — not a copy of
# its predicate. `_server_graph_hygiene` is a pytest fixture; its raw
# generator is `.__wrapped__` in pytest 9.

@contextlib.contextmanager
def _noop_proj(*_args, **_kwargs):
    class _DB:
        @staticmethod
        def list_graphs():
            return []

    class _Proj:
        db = _DB()

    yield _Proj()


def _start_teardown(monkeypatch, tmp_path, *, own, live, journal,
                    full=None, probe_raises=False, others=()):
    import tests.conftest as conftest
    import tortoise.embedded_reaper as reaper
    from tests import _embedded as emb

    monkeypatch.setenv(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix")
    monkeypatch.setenv("TORTOISE_TEST_SESSION", "0123456789ab")
    monkeypatch.setattr(conftest, "_ACTIVE_SUITES_DIR", str(tmp_path))
    monkeypatch.setattr(emb, "_JOURNAL_FILE", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(emb, "_stale_sweep", lambda *a, **k: {"stale": []})
    monkeypatch.setattr(emb, "_session_end_own_sweep", lambda *a, **k: own)
    monkeypatch.setattr(
        emb, "_leftover_sweep",
        lambda *a, **k: full if full is not None else {"full_sweep": True})
    monkeypatch.setattr(emb, "_read_journal", lambda: list(journal))

    def _probe(_uri):
        if probe_raises:
            raise RuntimeError("server unreachable (simulated infra failure)")
        return set(live)

    monkeypatch.setattr(emb, "_live_graph_names", _probe)
    monkeypatch.setattr(emb, "_uri_default_graph_name",
                        lambda: "tortoise_test_matrix")
    monkeypatch.setattr(emb, "_sweep_proj", _noop_proj)
    monkeypatch.setattr(reaper, "_process_start_time", lambda _pid: None)
    monkeypatch.setattr(reaper, "active_suite_markers", lambda *a, **k: list(others))

    gen = conftest._server_graph_hygiene.__wrapped__(None)
    next(gen)  # session-start half; returns a generator paused at `yield`
    return gen


def test_gate_raises_when_an_owned_journalled_name_survives(monkeypatch, tmp_path):
    gen = _start_teardown(monkeypatch, tmp_path, own={"dropped": [], "failed": []},
                          live={"test_leak"}, journal={"test_leak"})
    with pytest.raises(AssertionError, match=r"E2E-7: 1 owned journalled"):
        next(gen)


def test_gate_fires_even_when_the_leftover_sweep_did_not_run(monkeypatch, tmp_path):
    """P1-A regression: the gate must NOT inherit the bound check's
    `full_sweep` condition. Here `full` is an ERROR dict — the old nesting
    would have skipped the gate entirely."""
    gen = _start_teardown(monkeypatch, tmp_path, own={"dropped": []},
                          live={"test_leak"}, journal={"test_leak"},
                          full={"error": "leftover sweep down"})
    with pytest.raises(AssertionError, match=r"E2E-7: 1 owned journalled"):
        next(gen)


def test_gate_fires_when_full_sweep_is_false(monkeypatch, tmp_path):
    gen = _start_teardown(monkeypatch, tmp_path, own={"dropped": []},
                          live={"test_leak"}, journal={"test_leak"},
                          full={"full_sweep": False})
    with pytest.raises(AssertionError, match=r"E2E-7: 1 owned journalled"):
        next(gen)


@pytest.mark.parametrize("flag", ["failed", "error", "skipped"])
def test_gate_is_silent_when_the_own_sweep_did_not_complete(
        monkeypatch, tmp_path, flag):
    """A transient delete error / raised sweep / skip must keep the journal and
    NOT red the suite (epic cycle-8 P2-3/P2-4 log-and-continue)."""
    own = {flag: ["test_leak"] if flag == "failed" else "simulated"}
    gen = _start_teardown(monkeypatch, tmp_path, own=own,
                          live={"test_leak"}, journal={"test_leak"})
    with pytest.raises(StopIteration):
        next(gen)  # teardown completes with no raise


def test_gate_is_silent_on_preserved_and_default_names(monkeypatch, tmp_path):
    gen = _start_teardown(
        monkeypatch, tmp_path, own={"dropped": []},
        live={"registry_test_b", "tortoise_test_matrix", "foreign_x"},
        journal={"registry_test_b", "tortoise_test_matrix", "foreign_x"})
    with pytest.raises(StopIteration):
        next(gen)


def test_probe_failure_does_not_raise(monkeypatch, tmp_path, capsys):
    """P1-C (Task 5 review): an infra failure in the probe is an infra skip,
    NOT a leak signal."""
    gen = _start_teardown(monkeypatch, tmp_path, own={"dropped": []},
                          live={"test_leak"}, journal={"test_leak"},
                          probe_raises=True)
    with pytest.raises(StopIteration):
        next(gen)
    out = capsys.readouterr().out
    assert "E2E-7 survivor probe failed" in out
    assert "NOT a leak signal" in out
    assert "E2E-7: 1 owned journalled" not in out
