"""#4311 — the CI typecheck for `website/functions/**` stays wired.

WHY THIS EXISTS
---------------
#4311 is a defect about an ABSENT gate: `website/functions/**` is the production
edge surface (agent write API, admin gate, cache purge, contact form) and nothing
typechecked it — no CI job, and the blog-admin SPA's `tsc --noEmit` does not
include the tree. The strict project already failed on `main` (TS2322 in
`validateCreate`), invisible until runtime. This file guards the gate that fixes
that, because a gate is worth exactly as much as its wiring:

  * a `tsconfig` whose `include` no longer matches the Functions tree would
    typecheck fewer files (tsc fails loudly on an EMPTY project, so this is belt
    and braces — but a narrowed glob silently drops files it used to check);
  * a `strict` flag turned off, or the Workers globals swapped for the DOM lib,
    silently WEAKENS the check while it keeps passing;
  * a de-floated toolchain (a `^range` in place of the lockfile-pinned revision)
    lets the strictness of the gate change with no reviewed diff;
  * a job whose `if:` names an output the `changes` job never emits is skipped
    for ever — a green surface that runs nothing (the #2055 trap);
  * a path predicate that does not cover the guarded tree, or the tsc project
    itself, leaves the job asleep exactly when the change disarms it (the
    #1349/#3524/#8334 silent-drop class).

The workflow half is verified by EXECUTING the `changes` job's real gate script
against synthetic changed-file sets (the convention of
`tests/test_docs_lint_gate.py` / `tests/test_ci_guard_invocation.py`): a text scan
cannot distinguish a live predicate from a commented-out one, and this step is the
one whose size is capped by GitHub (#6253), so its shape is load-bearing.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_selection import load_manifest, select

REPO_ROOT = Path(__file__).resolve().parent.parent
TSCONFIG = REPO_ROOT / "tools" / "ts" / "tsconfig.website-functions.json"
PACKAGE = REPO_ROOT / "tools" / "ts" / "package.json"
LOCKFILE = REPO_ROOT / "tools" / "ts" / "package-lock.json"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

JOB = "website-functions-typecheck"
#: The job rides the existing `website` gate, so no new output key is needed. That
#: gate script carries no `${{ }}` spans, so GitHub does not compile it and the
#: 21000-byte cap does not reach it (#8734: at 20944 raw + 66 expression bytes it
#: was over, and GitHub refused to load ci.yml). The compiled-size cap is pinned
#: in tests/test_ci_selection.py — measure there, not here.
#: `website` already covers `website/functions/**`; `tools/ts/` is added below.
GATE_OUTPUT = "website"

#: A real file of the guarded tree, and the project's own config.
FUNCTIONS_SAMPLE = "website/functions/blog/api/posts/[[path]].ts"
TSCONFIG_SAMPLE = "tools/ts/tsconfig.website-functions.json"


def _ci() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


# ── the tsc project ─────────────────────────────────────────────────────────

def test_the_strict_project_covers_the_real_functions_tree() -> None:
    """`include` must still match the tree, not merely look like it does."""
    cfg = json.loads(TSCONFIG.read_text(encoding="utf-8"))
    opts = cfg["compilerOptions"]

    assert opts["strict"] is True, "the project is not strict — #4311's TS2322 class returns silently"
    assert opts["noEmit"] is True, "noEmit must stay on: this project typechecks, it must never emit"
    assert opts["types"] == ["@cloudflare/workers-types"], (
        "the Workers globals are the ONLY correct ambient types here — the DOM lib "
        "would let document/window typecheck into a Function that can never call them"
    )

    # Falsifiable against the real tree: resolve every `include` glob against the
    # project's OWN directory (tsconfig `include` is relative to the config, and a
    # worktree makes a cwd-relative glob silently match the main checkout's copy
    # instead) and require it to match exactly the Functions files.
    matched = {p.resolve() for pattern in cfg["include"] for p in TSCONFIG.parent.glob(pattern)}
    real = {p.resolve() for p in (REPO_ROOT / "website" / "functions").rglob("*.ts")}
    assert matched, f"the project's include globs match NOTHING: {cfg['include']}"
    assert matched == real, (
        "the typecheck no longer covers the whole Functions tree — "
        f"unchecked: {sorted(str(p.relative_to(REPO_ROOT)) for p in real - matched)}"
    )


def test_the_toolchain_is_pinned_and_locked() -> None:
    """No floating ranges: the gate's strictness is a reviewed artifact."""
    dev = json.loads(PACKAGE.read_text(encoding="utf-8"))["devDependencies"]
    assert set(dev) == {"typescript", "@cloudflare/workers-types"}, dev
    for name, spec in dev.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", spec), (
            f"{name} is pinned to {spec!r} — a range lets the compiler or the "
            "Workers types move under a green CI run with no reviewed diff"
        )
    assert LOCKFILE.exists(), "no package-lock.json — `npm ci` cannot be lockfile-pinned (#4311)"


# ── the workflow job ────────────────────────────────────────────────────────

def test_ci_has_the_job_and_runs_the_project() -> None:
    ci = _ci()
    assert JOB in ci["jobs"], f".github/workflows/ci.yml has no {JOB!r} job"
    job = ci["jobs"][JOB]
    assert job.get("needs") == "changes", job.get("needs")
    assert job["if"] == f"needs.changes.outputs.{GATE_OUTPUT} == 'true'", job["if"]
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "npm ci" in runs, f"{JOB} must install from the lockfile"
    assert "tsc -p tsconfig.website-functions.json" in runs, f"{JOB} does not run the project: {runs!r}"


def _gate_outputs(changed: list[str], tmp_path: Path) -> dict[str, str]:
    """Execute the `changes` job's REAL gate script for a synthetic changed set.

    The script is lifted out of the step's `run:` (the shell wrapper only feeds it
    a diff); running the Python directly means this test never touches the shared
    `/tmp/ci-gates.py` path the wrapper writes, so concurrent suites cannot race.
    """
    body = next(s for s in _ci()["jobs"]["changes"]["steps"] if s.get("id") == "gate")["run"]
    marker = "cat > /tmp/ci-gates.py <<'PY'\n"
    start = body.index(marker) + len(marker)
    script = body[start:body.index("\nPY\n", start)]

    src = tmp_path / "ci-gates.py"
    src.write_text(script, encoding="utf-8")
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(src), str(out)],
        input="\n".join(changed),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),  # the script shells out to client/shared_modules.sh
        check=False,
    )
    assert proc.returncode == 0, f"the changes gate script failed:\n{proc.stderr}"
    return dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)


def test_the_gate_the_job_rides_actually_fires_for_the_guarded_paths(tmp_path) -> None:
    """Executed, not scanned: the output the job's `if:` reads must go true.

    Falsifiable: drop `tools/ts/` from the `website` predicate and case 2 fails;
    narrow the `website/` lambda so it no longer covers `functions/` and case 1
    fails — either way the job would be skipped on the change that disarms it.
    """
    ci = _ci()
    assert GATE_OUTPUT in ci["jobs"]["changes"]["outputs"], (
        f"the `changes` job never emits {GATE_OUTPUT!r} — the job's `if:` would be "
        "an empty string and it would be SKIPPED for ever (a green surface running nothing)"
    )
    for path in (FUNCTIONS_SAMPLE, TSCONFIG_SAMPLE):
        assert _gate_outputs([path], tmp_path)[GATE_OUTPUT] == "true", (
            f"{path} does not set {GATE_OUTPUT}=true, so {JOB} never runs on it"
        )
    assert _gate_outputs(["docs/README.md"], tmp_path)[GATE_OUTPUT] == "false", (
        "the gate is true for an unrelated path — it is not measuring a changed set"
    )


# ── the guard must also run on the changes it guards (python-ci side) ───────

def _owning_surfaces() -> set[str]:
    name = Path(__file__).name
    return {s for s, files in load_manifest()["surfaces"].items() if name in files}


def test_the_project_is_a_selection_source() -> None:
    """Both the tree and the tsc project select this guard's surface (or FULL).

    Falsifiable: dropping `tools/ts/` from `SOURCE_PATTERNS` makes a
    tsconfig-only PR select no surface (`surfaces=[]`, `full=False`), so this
    guard would not run on the change that disarms it.
    """
    own = _owning_surfaces()
    assert own, f"{Path(__file__).name} is registered in NO surface of config/ci-surfaces.yml"
    for path in (FUNCTIONS_SAMPLE, TSCONFIG_SAMPLE, "tools/ts/package.json"):
        sel = select([path], "pull_request", load_manifest())
        assert sel["full"] or own <= set(sel["surfaces"]), (
            f"{path} selects {sel['surfaces']} (full={sel['full']}) — this guard would not "
            f"run on a PR touching it (owns: {sorted(own)})"
        )
