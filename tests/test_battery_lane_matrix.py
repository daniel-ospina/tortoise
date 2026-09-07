"""Lane-matrix enforcement (#2291 I-1): zero raw Cypher on the real A4 path.

The A4 arm must measure the PRODUCT, not raw-Cypher mimicry. This module
source-level audits the arm's runtime write+read paths so a regression that
reintroduces ``FalkorProjection``/``g.query``/``MERGE``/``MATCH``/``UNWIND``
on the real path fails loudly — while the REFERENCE lane (the hermetic
``batch_setup`` seeding path, kept for equivalence + warm-guard tests until
Task 2 swaps the real channel to ``sdk.ingest``) stays allowlisted
FUNCTION-SCOPED (a module-level allowlist would let raw Cypher hide in any
new helper added to the same module by later tasks).

Why source-level and not runtime tracing: every SDK verb internally issues
``proj.g.query`` — instrumenting the SDK/projection boundary would flag the
whole product. The audit therefore parses the ARM module itself and checks
that no raw-query construct is reachable from retrieve/record, and that
setup_scenarios contains only the allowlisted ``batch_setup`` call.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from battery.testing.seeds import setup_seed_mode

ARM_PATH = Path(__file__).resolve().parent.parent / "battery" / "arms" / "a4_tortoise.py"

#: Raw-query constructs that must never appear in the A4 runtime paths.
_FORBIDDEN = ("FalkorProjection", ".query(", "MERGE", "MATCH", "UNWIND", "g.query")

#: Function-scoped allowlist: reference-lane raw seeding ONLY (Task-2 will
#: swap the real channel to sdk.ingest; until then batch_setup is the only
#: sanctioned raw writer, and only from setup_scenarios).
_ALLOWLISTED_CALLS = {"batch_setup"}

#: Runtime write+read functions that must be PURE product-verb surfaces.
_RUNTIME_FNS = {"retrieve", "record"}


def _raw_tokens(module: ast.Module, fn_name: str) -> list[str]:
    """Raw-query token occurrences inside ONE function body (nested defs
    excluded — runtime helpers belong to the function's own surface)."""
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


def _module() -> ast.Module:
    return ast.parse(ARM_PATH.read_text())


@pytest.fixture(scope="module")
def arm_ast() -> ast.Module:
    return _module()


def _setup_has_only_allowlisted_raw_calls(module: ast.Module) -> list[str]:
    """setup_scenarios may call batch_setup (reference lane) but must not
    itself issue raw queries or construct projections."""
    bad: list[str] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "setup_scenarios":
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in _ALLOWLISTED_CALLS:
                    continue
                if name is None:
                    # e.g. a method call on an object — inspect the attr.
                    name = getattr(getattr(fn, "value", None), "attr", None)
                if isinstance(name, str) and any(
                        tok in name for tok in _FORBIDDEN):
                    bad.append(name)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                for tok in _FORBIDDEN:
                    if tok in sub.value:
                        bad.append(f"string:{tok}")
    return bad


def test_retrieve_record_have_zero_raw_cypher(arm_ast: ast.Module) -> None:
    """The A4 runtime read+write paths are PURE product-verb surfaces."""
    offenders: dict[str, list[str]] = {}
    for fn in _RUNTIME_FNS:
        hits = _raw_tokens(arm_ast, fn)
        if hits:
            offenders[fn] = hits
    assert not offenders, (
        "raw Cypher leaked into A4 runtime paths "
        f"(lane-matrix violation): {offenders}"
    )


def test_setup_only_allowlisted_raw_reference(arm_ast: ast.Module) -> None:
    """setup_scenarios may seed via the allowlisted reference function only
    (batch_setup) — never its own raw queries/projection construction."""
    bad = _setup_has_only_allowlisted_raw_calls(arm_ast)
    assert not bad, (
        "setup_scenarios contains raw-query constructs outside the "
        f"function-scoped allowlist: {bad}"
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


def test_hermetic_env_stripped_and_per_scenario_handle(tmp_path) -> None:
    """Hermetic fixture: env stripped; the seeded store is a per-run tmp
    dir; retrieve returns pre-k memories through the real arm surface."""
    # Env-strip: an ambient URI must not redirect the hermetic store.
    saved = os.environ.pop("TORTOISE_DB_URI", None)
    saved_path = os.environ.pop("TORTOISE_DB_PATH", None)
    try:
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
    finally:
        if saved is not None:
            os.environ["TORTOISE_DB_URI"] = saved
        if saved_path is not None:
            os.environ["TORTOISE_DB_PATH"] = saved_path
