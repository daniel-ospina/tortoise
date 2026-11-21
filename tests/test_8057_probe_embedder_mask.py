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
#: direction — so a new module-level point-write probe that the census can SEE
#: cannot ride along unseen.
#: ⛔ The bound is what the census implements, not everything a probe could be
#: (review P2): it scans `tests/**/test_*.py` (so a probe in a `conftest.py` or a
#: non-test helper module is outside it), and it matches `ast.Attribute` CALLS
#: whose name is in `_PROBE_WRITE_DOORS` (so an aliased door — `f = sdk.create_entity;
#: f(…)` — is too). Stated, not hidden: the clause above used to claim a probe
#: "cannot ride along unseen" without either bound named.
PROBE_MODULES = (
    "tests/test_assembly_sdk.py",
    "tests/test_assembly_fixtures.py",
    "tests/longmem_eval/test_assembly_arm.py",
)

#: The doors a module-level point-write probe can be spelled with, so the
#: census below survives an SDK-surface rename instead of reporting the probes
#: as gone. `create_entity("point", …)` is the consolidated door (#888 W2) and
#: RELAYS to `create_point` (#13899 retired the public spelling; the private
#: `_create_point` remains) — both reach `encode_for_store`.
#: ⛔ `_create_point` IS IN THE TUPLE (review P2). The comment above already said
#: the private spelling remains, and it is by far the most common spelling a
#: migrating lane reaches for (thousands of sites across the suite), so leaving
#: it out meant a module-level `sdk._create_point(…)` probe was invisible to a
#: census whose whole job is to notice exactly that.
_PROBE_WRITE_DOORS = ("create_point", "create_entity", "_create_point")

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

#: Import each declared probe in a FRESH interpreter with the doors in its
#: `_PROBE_DOORS` DELETED from `TortoiseSDK` — the #13899 rename REPLAYED.
#: One process for all three modules: the guard fires before the probe's own
#: `try` can reach storage (the URI is assigned inside it), so this arm needs
#: neither a live FalkorDB nor an embedder, and it pays one `tortoise.sdk`
#: import instead of three. Each module's outcome is reported separately, so a
#: probe that lost its guard is NAMED rather than hidden behind a sibling.
_DOOR_DELETION_SCRIPT = textwrap.dedent(
    '''
    import importlib.util
    import json
    import os
    import sys
    import traceback

    root, payload = sys.argv[1], sys.argv[2]
    targets = json.loads(payload)
    sys.path.insert(0, root)
    os.environ["TORTOISE_EMBEDDER_WARMUP"] = "0"

    from tortoise.sdk import TortoiseSDK

    out = {}
    gone = set()
    # Measured BEFORE anything is deleted, across every module: "absent after
    # the replay" is satisfied TRIVIALLY by a name that never existed, so the
    # replay alone cannot tell a deletion from a typo (review P2). The parent
    # pins this set, and that is the arm which reddens on a `_PROBE_DOORS`
    # mistake instead of proving a deletion that never happened.
    pristine_missing = {
        rel: [d for d in doors if not hasattr(TortoiseSDK, d)]
        for rel, doors in targets
    }
    for rel, doors in targets:
        for door in doors:
            if hasattr(TortoiseSDK, door):
                delattr(TortoiseSDK, door)
        gone.update(doors)
        # Cumulative on purpose: the deletion is process-wide, so by the second
        # module its doors are already gone. What must hold for EVERY module is
        # that the doors IT asserts are absent when IT imports.
        absent = sorted(d for d in gone if not hasattr(TortoiseSDK, d))
        missing_before = pristine_missing[rel]
        spec = importlib.util.spec_from_file_location(
            "_probe_renamed_" + rel.replace("/", "_").replace(".py", ""),
            os.path.join(root, rel))
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except BaseException:
            out[rel] = {"raised": traceback.format_exc(), "available": None,
                        "absent": absent, "missing_before": missing_before}
        else:
            out[rel] = {"raised": None,
                        "available": bool(module.FALKORDB_AVAILABLE),
                        "absent": absent, "missing_before": missing_before}
    print(json.dumps(out))
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


def _declared_probe_doors(rel: str) -> tuple[str, ...]:
    """The `_PROBE_DOORS` a probe module asserts, read WITHOUT importing it.

    Parsed, not executed: importing the module here would run the probe in the
    PARENT process, which is the one thing this file exists to keep out of an
    import path.
    """
    import ast

    for node in ast.parse((ROOT / rel).read_text()).body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(t, ast.Name) and t.id == "_PROBE_DOORS"
               for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError(
        f"{rel} declares no module-level `_PROBE_DOORS`: the probe's doors must "
        "be asserted BY NAME (#14114), or a renamed door is read as 'no "
        "storage' by the broad `except Exception` again.")




def _module_level_write_probe_lines(path: Path) -> list[int]:
    """Line numbers of probe WRITES executed at MODULE IMPORT.

    Import-time means "inside no def/class" — the probe shape. Walked with
    `ast`, not a text match, so a call inside a fixture (already masked by
    `_no_embedder`) is correctly NOT counted. The substring pre-filter below
    is a COST gate, not the test: parsing every file in `tests/` to ask
    "module-level?" measured 8.3s of this file's own duration, for a question
    only the handful of files that mention a probe door at all can answer
    yes to. It can only skip files that CANNOT match.

    The doors are BOTH spellings of the point write, because the SDK surface
    is not fixed: #13899 retired the public `create_point` in favour of
    `create_entity("point", …)`, which RELAYS to the same write and therefore
    pays the same `encode_for_store` -> `EmbeddingModel.get` cost at import.
    A census that knew only one spelling would report the three probes as gone
    (a silent green) the moment they migrated — which is exactly what happened
    on #13971's `test (a)`: the probes were still unmasked-in-spirit while the
    registry claimed to cover them.
    """
    import ast

    text = path.read_text()
    if not any(door in text for door in _PROBE_WRITE_DOORS):
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
        and n.func.attr in _PROBE_WRITE_DOORS
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
        if _module_level_write_probe_lines(path):
            found.add(str(path.relative_to(ROOT)))
    assert found == declared, (
        "the module-level point-write probe set drifted — add the new module "
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


# ── the guard's own failure path: a renamed door must NAME itself ─────────


def test_a_renamed_probe_door_names_itself_instead_of_skipping():
    """THE guard's failure path, made a row instead of a measurement (#14114).

    `_PROBE_DOORS` + the `(AttributeError, TypeError, NameError)` re-raise are
    the right SHAPE — pytest narrows `importorskip` to the missing-dependency
    class for exactly this reason, a skip being for an unavailable ENVIRONMENT
    and not for a programming defect — but until this arm existed nothing in the
    tree moved when either was removed: the failure path needs an SDK-surface
    change, so `test_probe_import_never_reaches_the_embedder` stays green while
    the guard that names a renamed door is deleted. That is the non-vacuity gap
    (the commit's evidence was a manual measurement with no artifact).

    Here the rename is REPLAYED: the doors are removed from `TortoiseSDK` in a
    child interpreter and each probe must fail at IMPORT with its OWN message.

    Two mutants redden here, which is why the assertions are on the guard's
    MESSAGE and not merely on a non-zero exit:

    * delete the `_missing` block → the import blows up at the call site on a
      live lane, and is swallowed into `FALKORDB_AVAILABLE = False` on a
      no-storage lane; the guard's message is absent in BOTH;
    * revert the narrowed catch to `except Exception` → nothing raises at all.

    ⛔ The replay is split into TWO arms, because "absent afterwards" alone is
    inert (review P2): a door NAME that never existed on the SDK is trivially
    absent after a deletion loop that skipped it, so the row would stay green
    while the deletion it exists to prove never happened. The child therefore
    reports `missing_before` — the declared doors it could not find on a
    PRISTINE `TortoiseSDK`, measured before any deletion — and the parent pins
    that it is empty. Third mutant, which only this arm catches: add a
    never-existent door (e.g. `create_entty`) to a probe's `_PROBE_DOORS`.
    """
    targets = [[rel, list(_declared_probe_doors(rel))] for rel in PROBE_MODULES]
    proc = subprocess.run(
        [sys.executable, "-c", _DOOR_DELETION_SCRIPT, str(ROOT),
         json.dumps(targets)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=600,
        env={**os.environ},
    )
    assert proc.returncode == 0, (
        "the door-deletion child failed — that is this harness, not a probe:\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")

    report = json.loads(proc.stdout.strip().splitlines()[-1])
    for rel, doors in targets:
        entry = report[rel]
        assert not entry["missing_before"], (
            f"{rel}: {entry['missing_before']} is declared as a probe door but "
            "does not exist on a pristine TortoiseSDK — the mask cannot remove a "
            "door that is not there, so every assertion below would pass without "
            "proving the replay (#14114 review P2). Fix `_PROBE_DOORS` in that "
            "module, or the SDK-surface name it should be.")
        assert set(doors) <= set(entry["absent"]), (
            f"{rel}: {sorted(set(doors) - set(entry['absent']))} still present "
            "on TortoiseSDK when the module imported, so the replay proves "
            "nothing. Keep _PROBE_DOORS in step with the SDK surface.")
        raised = entry["raised"]
        assert raised is not None, (
            f"{rel}: importing with {doors} REMOVED from TortoiseSDK did NOT "
            "raise — that is the #14114 defect back. A renamed door read as "
            f"'no storage' (FALKORDB_AVAILABLE={entry['available']}): a silent "
            "skip locally and a hard CI fail naming no door.")
        assert "RENAMED/REMOVED door, not missing storage (#14114)" in raised, (
            f"{rel}: the import failed, but not with the guard's OWN message — "
            "the `_missing` block is gone, so the renamed door is no longer "
            f"named for whoever has to fix it:\n{raised}")
        for door in doors:
            assert door in raised, (
                f"{rel}: the failure names no `{door}` — the message must say "
                f"WHICH door moved:\n{raised}")
        # …and it must say where to fix it: the census file AND all three probe
        # modules, because `_PROBE_WRITE_DOORS` lists only the WRITE spelling
        # and a reader sent there for a READ door is sent to a tuple that does
        # not contain it.
        assert "tests/test_8057_probe_embedder_mask.py" in raised, (
            f"{rel}: the failure does not say where `_PROBE_WRITE_DOORS` "
            f"lives:\n{raised}")
        for other in PROBE_MODULES:
            assert other in raised, (
                f"{rel}: the failure does not name `{other}` — every one of "
                "the three carries its own `_PROBE_DOORS`, and the reader is "
                f"being told to fix exactly this one:\n{raised}")
