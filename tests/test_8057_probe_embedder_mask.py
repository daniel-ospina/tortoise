"""#8057 — a module-level availability probe must not reach the embedder.

WHY THIS FILE EXISTS. The assembly modules resolve `FALKORDB_AVAILABLE` at
MODULE IMPORT by WRITING a probe point and querying it back (the FULLTEXT
round-trip is the gate, not a bare ping — embedded returns `[]` silently).
`create_point` derives an embedding on the way (`encode_for_store` ->
`EmbeddingModel.get`), so an unmasked probe pays a cold
sentence-transformers/torch load — measured 50-205s (#6960) — inside a gate
that only needs the text surface. A module-level probe cannot use the modules'
own autouse `_no_embedder` fixture: fixtures run per test, and this runs before
any of them.

RE-MEASURED 2026-10-16 (this lane: docker FalkorDB, `embeddings` extra
installed) — the issue body's stated FAILURE and MECHANISM do not reproduce:

* the unmasked probe SUCCEEDS on a healthy lane. The body's own command
  reported **33 passed, 0 skipped** — no silent-skip to remove. (The body's
  `24 skipped` was measured 2026-10-10 on a different worktree; the module has
  since grown to 33 tests.)
* the claimed mechanism — a cold load "inside the projection's 10 s socket read
  timeout" — is not how that timeout works. Redis-py applies `socket_timeout`
  to a socket OPERATION; the model load is CPU between two commands, and the
  server never closes an idle client (`config get timeout` -> `0`). Driven with
  a deterministic **45 s** embedder double (4.5x `_DB_SOCKET_TIMEOUT_DEFAULT`)
  the probe still returned `FALKORDB_AVAILABLE = True`.

What IS real, reproducible and worth pinning is the issue's SECOND indicator:
the probe's cost must be INDEPENDENT of whether an embedding model is
installed. Measured before the fix on this lane: importing the module paid the
cold load — `--collect-only` = 125.38 s with zero tests, the full file =
139.53 s for ~15 s of tests, and `EmbeddingModel.get()` alone = 57.15 s in a
fresh process. The contract below is behavioural, not a diff-size check: the
probe must never reach EITHER embedder seam.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import tests._live_utils as _live_utils
import tests.test_assembly_sdk as _assembly_sdk

ROOT = Path(__file__).resolve().parent.parent

#: The probe modules whose availability gate writes a point at import time.
#: A registry, not a claim: `test_probe_census_matches_the_declared_set` reads
#: the source and fails when this tuple and the code disagree in either
#: direction, so a new module-level `create_point` probe cannot ride along
#: unseen.
PROBE_MODULES = (
    "tests/test_assembly_sdk.py",
    "tests/test_assembly_fixtures.py",
    "tests/longmem_eval/test_assembly_arm.py",
)

#: Import a probe module in a FRESH interpreter with both embedder seams
#: replaced by doubles that RECORD the call and return None. A lane imports the
#: module exactly this way, so the probe is exercised as shipped — including the
#: module-level execution order a fixture cannot influence.
#: `TORTOISE_EMBEDDER_WARMUP=0` mirrors `tests/conftest.py`, the lane the probe
#: actually runs in; without it a background warm-up thread would add a seam
#: call of its own and blur what is being measured.
_CHILD_SCRIPT = textwrap.dedent(
    '''
    import importlib.util
    import json
    import os
    import sys

    root, rel, marker = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, root)
    os.environ["TORTOISE_EMBEDDER_WARMUP"] = "0"

    import tortoise.embeddings as emb


    def _recording(name):
        def _seam(*args, **kwargs):
            with open(marker, "a") as fh:
                fh.write(name + "\\n")
            return None
        return _seam


    emb.compute_embedding = _recording("compute_embedding")
    emb.EmbeddingModel.get = staticmethod(_recording("EmbeddingModel.get"))

    spec = importlib.util.spec_from_file_location(
        "_probe_under_test", os.path.join(root, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    print(json.dumps({"available": bool(module.FALKORDB_AVAILABLE)}))
    '''
)


# ── the mask itself: both seams, restored exactly ─────────────────────────


def test_mask_embedder_masks_both_seams():
    """Either seam alone still loads the model (#8057).

    `encode_for_store` calls the module-global `compute_embedding`; the
    `_encode` leg behind it calls `EmbeddingModel.get`. Masking one and not the
    other leaves the cold load reachable, so the helper pins both — and both
    must answer the call SHAPES the product makes, including the warm-up leg's
    `load_timeout=`: a double that raised on it would be swallowed by the write
    path's own `except Exception` into the silent no-vector degrade (#4280),
    i.e. the mask would cause the failure it exists to prevent.
    """
    from tortoise import embeddings as emb

    real_compute = emb.__dict__["compute_embedding"]
    real_get = emb.EmbeddingModel.__dict__["get"]

    with _live_utils.mask_embedder():
        # both attributes really were swapped, not shadowed somewhere else
        assert emb.__dict__["compute_embedding"] is not real_compute
        assert emb.EmbeddingModel.__dict__["get"] is not real_get
        # …and both answer "no embedder"
        assert emb.compute_embedding("probe zzqfulltext roundtrip token") is None
        assert emb.EmbeddingModel.get() is None
        assert emb.EmbeddingModel.get(load_timeout=5.0) is None


def test_mask_embedder_restores_the_exact_descriptor():
    """Restore must be structural, not merely callable.

    `EmbeddingModel.get` is a `classmethod`; `getattr` returns a new bound
    method object on every access, so restoring THAT would leave the class
    holding a plain method where a descriptor belongs — it would still call,
    and stop being a classmethod for every test after the mask.
    """
    from tortoise import embeddings as emb

    saved_compute = emb.__dict__["compute_embedding"]
    saved_get = emb.EmbeddingModel.__dict__["get"]
    assert type(saved_get) is classmethod

    with _live_utils.mask_embedder():
        assert emb.__dict__["compute_embedding"] is not saved_compute
        assert emb.EmbeddingModel.__dict__["get"] is not saved_get

    assert emb.__dict__["compute_embedding"] is saved_compute
    assert emb.EmbeddingModel.__dict__["get"] is saved_get
    assert type(emb.EmbeddingModel.__dict__["get"]) is classmethod


def test_mask_embedder_restores_when_the_block_raises():
    """A raising masked block must not leave the process pinned to the double."""
    from tortoise import embeddings as emb

    saved_compute = emb.__dict__["compute_embedding"]
    saved_get = emb.EmbeddingModel.__dict__["get"]

    with pytest.raises(RuntimeError, match="boom"), _live_utils.mask_embedder():
        raise RuntimeError("boom")

    assert emb.__dict__["compute_embedding"] is saved_compute
    assert emb.EmbeddingModel.__dict__["get"] is saved_get


# ── the census: no probe module can slip past the registry ───────────────


def _module_level_create_point_lines(path: Path) -> list[int]:
    """Line numbers of `create_point(...)` calls executed at MODULE IMPORT.

    Import-time means "inside no def/class" — the probe shape. Walked with
    `ast`, not a text match, so a call inside a fixture (already masked by
    `_no_embedder`) is correctly NOT counted. The substring pre-filter below
    is a COST gate, not the test: parsing every file in `tests/` to ask
    "module-level?" measured 8.3s of this file's own duration, for a question
    only the handful of files that mention `create_point` at all can answer
    yes to. It can only skip files that CANNOT match.
    """
    import ast

    text = path.read_text()
    if "create_point" not in text:
        return []
    tree = ast.parse(text)
    spans = [
        (n.lineno, n.end_lineno)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "create_point"
        and not any(lo <= n.lineno <= hi for lo, hi in spans)
    ]


def test_probe_census_matches_the_declared_set():
    """The declared registry IS the census, in both directions.

    Without this the behavioural guard below could cover three modules while a
    fourth module-level probe (same defect, unmasked) rode along unseen.
    """
    declared = set(PROBE_MODULES)
    found: set[str] = set()
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        if _module_level_create_point_lines(path):
            found.add(str(path.relative_to(ROOT)))
    assert found == declared, (
        "the module-level create_point probe set drifted — add the new module "
        "to PROBE_MODULES (and mask its probe):\n"
        f"  declared-only: {sorted(declared - found)}\n"
        f"  found-only:    {sorted(found - declared)}")


# ── the behavioural proof: a fresh import never reaches the seam ─────────


@pytest.mark.skipif(
    not _assembly_sdk.FALKORDB_AVAILABLE,
    reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane — tier-2 embedded legs skip)")
@pytest.mark.parametrize("rel", PROBE_MODULES)
def test_probe_import_never_reaches_the_embedder(rel, tmp_path):
    """THE regression guard (#8057): the probe's cost is model-independent.

    Before the fix every one of these modules drove `create_point` with the
    real seam, so on a cold lane importing it paid the torch load — 125.38 s to
    collect the file with zero tests run. The double records the call instead
    of loading, so the guard is deterministic and costs milliseconds: it does
    not measure elapsed time (which is what keeps it CI-safe) and does not need
    a model installed (which is the issue's own second indicator).
    """
    marker = tmp_path / "seam-calls.txt"
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, str(ROOT), rel, str(marker)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=600,
        env={**os.environ},
    )
    assert proc.returncode == 0, (
        f"{rel}: the probe import failed in a fresh interpreter:\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")

    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["available"] is True, (
        f"{rel}: the probe resolved NOT-available in the child while the "
        "parent's own probe resolved available — the lane was up for the "
        f"parent, so this is a real probe regression, not a skip:\n{proc.stdout}")

    assert not marker.exists(), (
        f"{rel}: the module-level probe reached the embedder seam "
        f"({marker.read_text().strip()!r}). On a cold lane that is the "
        "50-205s sentence-transformers/torch load paid at import (#8057); wrap "
        "the probe in `tests._live_utils.mask_embedder()`.")
