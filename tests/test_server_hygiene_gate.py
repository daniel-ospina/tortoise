"""#3634 Task 5 — the session-scoped E2E-7 gate on OWNED survivors.

Two properties are pinned here, and neither is observable from the
whole-server graph count the gate it replaces used:

1. The ownership predicate (``_owned_survivors``). A preserved-but-journalled
   shared registry (``registry_*``) or the URI-path default graph must NOT
   count as a survivor — the ownership record is the only thing that makes a
   journalled name a leak, mirroring ``_sweep_drop``'s skips.
2. The capture-before-sweep ordering inside ``_server_graph_hygiene`` — the
   journal is DELETED by the sweep, so a name set read afterwards is empty
   and the gate would be vacuously green.

Reach (recorded, not a defect): the gate lives under ``if not others`` in
``tests/conftest.py`` — last-suite-standing only. This file does NOT and
cannot claim in-process observability of an actual leaking session; the
subprocess leg is skipped (see ``test_owned_survivors_*`` docstring in the
plan and the OVERRIDES comments on #3634).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

from tests._embedded import _owned_survivors  # noqa: E402


def test_owned_survivors_applies_the_ownership_predicate():
    """A preserved-but-journalled shared registry must NOT be a survivor."""
    journal = {"test_a", "registry_test_b_control_plane",
               "registry_control_plane", "registry_tortoise", "tortoise_test_matrix"}
    live = {"test_a", "registry_control_plane", "registry_tortoise", "tortoise_test_matrix"}
    assert _owned_survivors(journal, live, "tortoise_test_matrix") == {"test_a"}


def test_owned_survivors_ignores_foreign_graphs():
    assert _owned_survivors({"test_a"}, {"org_x", "t"}, None) == set()


# ── Step 5: AST pin — the capture must precede the sweep that deletes it ──
#
# AC4 is AST-pinned, NOT session-observed: the subprocess leg cannot seed a
# journal (tests/test_tripwire.py::_run_session pops
# TORTOISE_TEST_JOURNAL_FILE from the child env — `_CHILD_LANE_VARS`), so a
# live leaking-session test is unwritable, not merely unwritten. The reach of
# the gate itself (under `if not others` in the fixture) is recorded on
# #3634; this pin covers the ordering the gate depends on.


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


def test_journal_capture_precedes_the_sweep_that_deletes_it():
    """A real ast.walk of the `_server_graph_hygiene` body.

    The session-end sweep REMOVES the journal, so a name set read after it is
    empty and the gate is vacuously green. Falsify by swapping the two calls.
    """
    fn = _fixture_body("_server_graph_hygiene")
    calls = _own_statement_calls(fn)

    reads = [(c.lineno, c.col_offset) for c in calls
             if _callee(c) == "_read_journal"]
    sweeps = [(c.lineno, c.col_offset) for c in calls
              if _callee(c) == "_session_end_own_sweep"]
    assert reads, "no _read_journal call in _server_graph_hygiene"
    assert sweeps, "no _session_end_own_sweep call in _server_graph_hygiene"
    assert min(reads) < min(sweeps), (
        "the journal name set is captured AFTER the session-end sweep that "
        "deletes the journal — the survivor gate would be vacuously green")
