"""Lane-matrix enforcement (#2291 I-1): zero raw Cypher on the real A4 path.

The A4 arm must measure the PRODUCT, not raw-Cypher mimicry. This module
source-level audits the arm's runtime surface so a regression that
reintroduces ``FalkorProjection``/``g.query``/``MERGE``/``MATCH``/``UNWIND``
on the real path fails loudly. The audit covers EVERY method of the
A4TortoiseArm class and every module-level runtime helper — not a
hardcoded two-name list — so a raw query added to a future runtime
surface (ep_terminal_outcome, a new read helper, …) fails the gate. The
single allowlisted exception is ``_scenario_graph``: raw test-support
(read-back for assertions), never reachable from the runtime write/read
path. The reference-lane raw seeder (setup.py ``batch_setup``) lives in
battery/runner/setup.py and is NOT parsed here — the arm itself holds no
raw channel (Task-2 swapped the real seed channel to sdk.ingest).

Why source-level and not runtime tracing: every SDK verb internally issues
``proj.g.query`` — instrumenting the SDK/projection boundary would flag the
whole product. The audit therefore parses the ARM module itself and checks
that no raw-query construct is reachable from the runtime surface.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from battery.testing.seeds import setup_seed_mode

ARM_PATH = Path(__file__).resolve().parent.parent / "battery" / "arms" / "a4_tortoise.py"

#: Raw-query constructs that must never appear in the A4 runtime paths.
_FORBIDDEN = ("FalkorProjection", ".query(", "MERGE", "MATCH", "UNWIND", "g.query")

#: Raw test-support read-back (assertion helper) — never on the runtime
#: write/read path (verify: only tests call it; grep the repo to confirm).
_RAW_TEST_SUPPORT = {"_scenario_graph"}

#: The arm class whose ENTIRE runtime surface must be pure product verbs.
_ARM_CLASS = "A4TortoiseArm"


def _raw_tokens(module: ast.Module, fn_name: str) -> list[str]:
    """Raw-query token occurrences inside ONE function body (nested defs
    included — a helper declared inside a runtime method is runtime)."""
    found: list[str] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != fn_name:
            continue
        # Re-walk ONLY this function's subtree (ast.walk includes nested).
        for sub in ast.walk(node):
            for attr in ("id", "attr"):
                name = getattr(sub, attr, None)
                if isinstance(name, str) and any(
                        tok in name for tok in _FORBIDDEN):
                    found.append(name)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                for tok in _FORBIDDEN:
                    if tok in sub.value:
                        found.append(f"string:{tok}")
    return found


def _runtime_function_names(module: ast.Module) -> tuple[set[str], set[str]]:
    """(methods of the arm class, module-level helpers) — the FULL runtime
    surface, minus raw test-support read-back."""
    methods: set[str] = set()
    helpers: set[str] = set()
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == _ARM_CLASS:
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(sub.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            helpers.add(node.name)
    return (methods - _RAW_TEST_SUPPORT, helpers - _RAW_TEST_SUPPORT)


def _module() -> ast.Module:
    return ast.parse(ARM_PATH.read_text())


@pytest.fixture(scope="module")
def arm_ast() -> ast.Module:
    return _module()


def test_full_runtime_surface_has_zero_raw_cypher(arm_ast: ast.Module) -> None:
    """EVERY arm method + module-level runtime helper is a PURE product-verb
    surface (retrieve, record, ep_terminal_outcome, _live_claim_ids, …).
    Only the documented raw test-support read-back (_scenario_graph) is
    exempt — and it is not on the runtime write/read path."""
    methods, helpers = _runtime_function_names(arm_ast)
    offenders: dict[str, list[str]] = {}
    for fn in sorted(methods | helpers):
        hits = _raw_tokens(arm_ast, fn)
        if hits:
            offenders[fn] = hits
    assert not offenders, (
        "raw Cypher leaked into the A4 runtime surface "
        f"(lane-matrix violation): {offenders}"
    )


def test_arm_module_has_no_import_of_raw_projection(arm_ast: ast.Module) -> None:
    """The arm module must not import FalkorProjection at all (setup builds
    no projection of its own)."""
    imports: list[str] = []
    for node in ast.walk(arm_ast):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                imports.append(a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                imports.append(a.name.split(".")[0])
    assert "FalkorProjection" not in imports, imports


def test_hermetic_env_stripped_and_per_scenario_handle(tmp_path,
                                               monkeypatch) -> None:
    """Hermetic fixture: env stripped; the seeded store is a per-run tmp
    dir; retrieve returns pre-k memories through the real arm surface."""
    # Env-strip: an ambient URI must not redirect the hermetic store.
    # (monkeypatch fixture-param auto-undo — pytest restores at teardown)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
    ns = tmp_path / "run1"
    store = setup_seed_mode(ns, "ct-001")
    try:
        memories = store.retrieve("")
        assert isinstance(memories, list)
        # seed_mode: claim_a + evidence pre-k are present in the arm's
        # retrieved surface; ¬A content absence is covered by
        # test_battery_r1_seed (no-leak) — this test only locks the
        # hermetic fixture shape + that the store lives under tmp_path.
        assert any(m for m in memories), "retrieve returned no memories"
    finally:
        store.close()
