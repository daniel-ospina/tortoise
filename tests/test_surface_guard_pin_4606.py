"""#4606 — the surface gate must not be neuterable by the PR it measures.

`surface-guard` runs the CHECKOUT'S OWN `tools/surface-guard.py`. Without a pin, a
PR can expand the surface and stub the gate out **in the same commit**: the stub
prints `OK`, exits 0, and the REQUIRED `python-ci-gate` goes green on a rewritten
gate.

The workflow pins `tools/surface-guard.py` — the gate, which is self-contained —
to the base ref before running it. `tools/surface_manifest.py` is deliberately NOT
pinned: the steps that use it would then run the BASE ref's copy, so a PR that
FIXES that tool could never pass them (the deadlock measured on #5192), and those
steps are an ordering lint and an artifact-consistency check, not the expansion
gate.

These tests exercise the **actual step text extracted from the workflow** — never a
hand-copied duplicate — against the filesystem mutations a hostile PR could
commit in that same push.

Scope: `tests/test_surface_guard_pin_4606.py` guards the pin's own mechanism.
The declared threat surface is *modifying the pinned tool path* (and the `tools/`
container that resolves it). Making `.github/workflows/python-ci.yml` itself
unmodifiable is a separate, still-open decision on #4606 and is deliberately
NOT asserted here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "python-ci.yml"
JOB = "surface-guard"
PIN_PREFIX = "Pin the gate's tooling"
GUARD_STEP = "The surface cannot expand"
LINT_STEP = "The ordering lint"

GUARD_SRC = 'print("REAL GUARD")\n'
MANIFEST_SRC = 'print("REAL MANIFEST")\n'


def _pin_body(base_ref: str = "") -> str:
    """The step's shell, read from the workflow (single source of truth)."""
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"][JOB]["steps"]
    matching = [s for s in steps if str(s.get("name", "")).startswith(PIN_PREFIX)]
    assert len(matching) == 1, f"expected exactly one pin step, found {len(matching)}"
    # `${{ github.base_ref }}` is empty on a push/schedule run; substituting it is
    # what makes the step runnable outside Actions (base becomes HEAD).
    return matching[0]["run"].replace("${{ github.base_ref }}", base_ref)


def _scratch(tmp_path: Path) -> Path:
    """A minimal git repo holding the two tools, committed as the base ref."""
    d = tmp_path / "repo"
    tools = d / "tools"
    tools.mkdir(parents=True)
    (tools / "surface-guard.py").write_text(GUARD_SRC)
    (tools / "surface_manifest.py").write_text(MANIFEST_SRC)
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "commit", "-qm", "base"],
        cwd=d,
        check=True,
    )
    return d


def _run_pin(d: Path, base_ref: str = "") -> subprocess.CompletedProcess[str]:
    script = d.parent / "pin-step.sh"
    script.write_text(_pin_body(base_ref))
    return subprocess.run(
        ["bash", str(script)], cwd=d, capture_output=True, text=True, check=False
    )


def test_the_extracted_step_is_the_real_one() -> None:
    """Guard the extractor: an empty/renamed step would make every case vacuous."""
    body = _pin_body()
    assert "surface-guard.py" in body, body
    assert "mv -f" in body, body


def test_the_gate_is_pinned_and_the_generator_is_not() -> None:
    """Pin the ENFORCEMENT, not the GENERATOR — pinning the generator deadlocks it.

    The two steps that use `tools/surface_manifest.py` run the BASE ref's copy of
    it, so a PR that fixes that tool can never make them pass; measured on #5192.
    It cannot influence the gate either — `surface-guard.py` carries its own
    `_read_manifest` and imports nothing from it. Asserted on the EXECUTED list,
    not on the prose, so a comment naming the file cannot satisfy it.
    """
    body = _pin_body()
    loops = [ln for ln in body.splitlines() if ln.strip().startswith("for f in")]
    assert len(loops) == 1, body
    assert "surface-guard.py" in loops[0], loops[0]
    assert "surface_manifest.py" not in loops[0], loops[0]


def test_the_pin_runs_before_both_guard_steps() -> None:
    """Order matters: pinning after the run would guard nothing."""
    wf = yaml.safe_load(WORKFLOW.read_text())
    names = [str(s.get("name", "")) for s in wf["jobs"][JOB]["steps"]]
    pin = next(i for i, n in enumerate(names) if n.startswith(PIN_PREFIX))
    guard = next(i for i, n in enumerate(names) if n.startswith(GUARD_STEP))
    lint = next(i for i, n in enumerate(names) if n.startswith(LINT_STEP))
    assert pin < guard < lint, names


def test_a_stubbed_tool_is_replaced(tmp_path: Path) -> None:
    d = _scratch(tmp_path)
    (d / "tools" / "surface-guard.py").write_text('print("OK")\n')
    r = _run_pin(d)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (d / "tools" / "surface-guard.py").read_text() == GUARD_SRC


def test_a_symlinked_tool_is_replaced_not_written_through(tmp_path: Path) -> None:
    """`> $f` follows a symlink; the pin must land the content at the PATH.

    A `tools/surface-guard.py -> /dev/null` link used to swallow the pin and
    leave an empty program that exits 0 — the gate would report success.
    """
    d = _scratch(tmp_path)
    target = d / "tools" / "surface-guard.py"
    target.unlink()
    target.symlink_to("/dev/null")

    r = _run_pin(d)

    assert r.returncode == 0, r.stdout + r.stderr
    assert not target.is_symlink(), "the pin wrote THROUGH the link"
    assert target.read_text() == GUARD_SRC


def test_a_symlinked_tools_directory_fails_closed(tmp_path: Path) -> None:
    """The container is the attack: a symlinked `tools/` re-roots each tool's ROOT.

    `ROOT = Path(__file__).resolve().parent.parent`, so if `tools/` resolves into
    a PR-authored tree, both pinned tools measure that decoy tree — and every
    per-file assertion still passes.
    """
    d = _scratch(tmp_path)
    decoy = d / "decoy"
    decoy.mkdir()
    (decoy / "surface-guard.py").write_text('print("DECOY")\n')
    (decoy / "surface_manifest.py").write_text('print("DECOY")\n')
    (d / "tools").rename(d / "tools-real")
    (d / "tools").symlink_to("decoy")

    r = _run_pin(d)

    assert r.returncode != 0, "the pin accepted a symlinked tools/ — ROOT is re-rootable"
    assert "not a real directory" in (r.stdout + r.stderr)


def test_a_directory_in_place_of_a_tool_fails_closed(tmp_path: Path) -> None:
    """`mv -f` into a directory returns 0 (it moves INTO it), so the test chain must catch it."""
    d = _scratch(tmp_path)
    target = d / "tools" / "surface-guard.py"
    target.unlink()
    target.mkdir()

    r = _run_pin(d)

    assert r.returncode != 0, "the pin accepted a directory in place of the tool"
    assert "not a non-empty regular file" in (r.stdout + r.stderr)
