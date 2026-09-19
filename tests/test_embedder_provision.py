"""Tests for tools/embedder_provision.py — the #2573 loud-degrade gate.

`python-ci.yml` (and `post-merge-validation.yml`) pre-cached the embedding model
in an inline heredoc with `continue-on-error: true`: a cache miss plus an
unreachable `huggingface.co` was swallowed, the suite fell back to keyword-only
TF-IDF retrieval (`tortoise/embeddings.py`), and the run reported GREEN. A green
run that never exercised the dense leg is not hybrid-retrieval evidence, and it
is indistinguishable from one that did.

These tests pin BOTH halves of the fix:

1. **Behaviour** — the provisioning script is EXECUTED, not string-matched. A
   fake `sentence_transformers` on `PYTHONPATH` drives the real failure and
   success paths, so the loud marker and the exit code are observed end to end.
   (Fake-module injection is the established pattern: tests/test_embedder_probe.py.)
2. **Wiring** — the workflows are parsed and every job that needs the embedder
   must call the script from a step that OWNS the requirement, with
   `continue-on-error` absent and a non-vacuous invocation. Removing that wiring
   must fail here, not in a silent CI degrade.

The fakes are faithful to the REAL library in one respect that matters: the
replaced heredocs set `HF_HUB_OFFLINE=1` before importing, and huggingface_hub
freezes that variable into a module constant AT IMPORT, so deleting it afterwards
could not re-enable the network. `test_download_path_survives_the_offline_env_latch`
reproduces that latch and fails against the old implementation.

Plus the lockstep guard: the model/revision the CI provisions must be the exact
pair `tortoise/embeddings.py` ships (#1349 T10 — a drifted pin would serve the
WRONG embedder from the cache).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import embedder_provision as ep

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "tools" / "embedder_provision.py"
_WORKFLOWS = _ROOT / ".github" / "workflows"
# Every workflow that provisions the embedder. The same heredoc lived in four
# jobs across these two files; a fix that lands in only one of them is the drift
# this list exists to catch.
_PROVISIONING_WORKFLOWS = ("python-ci.yml", "post-merge-validation.yml")
# The three python-ci jobs that provision the embedder for the dense leg.
_EMBEDDER_JOBS = ("test", "test-slow", "test-concurrency-falkor")


# ── Faithful fakes ───────────────────────────────────────────────────────
# `_OFFLINE_AT_IMPORT` mirrors huggingface_hub.constants: the variable is read
# ONCE, when the module is imported. A later `del os.environ[...]` cannot lift it.
_LATCH_PREAMBLE = (
    "import os\n"
    "\n"
    "_OFFLINE_AT_IMPORT = os.environ.get('HF_HUB_OFFLINE') == '1'\n"
    "\n"
)

# Cache probe raises; the download succeeds — UNLESS offline mode latched at
# import, which is the pre-fix behaviour this test exists to keep dead.
_FAITHFUL_DOWNLOAD = _LATCH_PREAMBLE + (
    "class SentenceTransformer:\n"
    "    def __init__(self, *a, **k):\n"
    "        if k.get('local_files_only'):\n"
    "            raise OSError('not in the local cache')\n"
    "        if _OFFLINE_AT_IMPORT:\n"
    "            raise OSError(\"offline mode is enabled (latched at import) — \"\n"
    "                          \"cannot reach https://huggingface.co\")\n"
)

# Cache probe hits.
_FAITHFUL_CACHED = _LATCH_PREAMBLE + (
    "class SentenceTransformer:\n"
    "    def __init__(self, *a, **k):\n"
    "        return\n"
)

# The REAL transformers/huggingface_hub failure is TWO lines (verbatim from the
# issue's evidence). The fake must reproduce that, not a tidy one-liner: an
# unsanitised multi-line message truncates the ::error:: annotation in the run UI.
_ALWAYS_RAISES = (
    "class SentenceTransformer:\n"
    "    def __init__(self, *a, **k):\n"
    "        raise OSError(\n"
    "            \"We couldn't connect to 'https://huggingface.co' to load the files, \"\n"
    "            \"and couldn't find them in the cached files.\\n\"\n"
    "            \"Check your internet connection or see how to run the library in \"\n"
    "            \"offline mode at 'https://huggingface.co/docs/transformers/\"\n"
    "            \"installation#offline-mode'.\"\n"
    "        )\n"
)


def _base_env(tmp_path: Path, *, summary: bool = False) -> dict:
    """A clean env for the subprocess: no HF_* leak, optional summary file."""
    env = dict(os.environ)
    # The parent's own env must not pre-latch offline mode for the fake.
    env.pop("HF_HUB_OFFLINE", None)
    env.pop("TRANSFORMERS_OFFLINE", None)
    if summary:
        env["GITHUB_STEP_SUMMARY"] = str(tmp_path / "summary.md")
    else:
        # NEVER inherit the real summary path: a degraded-path test would append
        # a false "DEGRADED" block to the summary of a run whose gate PASSED.
        env.pop("GITHUB_STEP_SUMMARY", None)
    return env


def _run(tmp_path: Path, module_source: str, *, attempts: int = 1, summary: bool = False):
    """Run the script against a fake sentence_transformers; return CompletedProcess."""
    fake = tmp_path / "fake"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / "sentence_transformers.py").write_text(module_source, encoding="utf-8")
    env = _base_env(tmp_path, summary=summary)
    env["PYTHONPATH"] = str(fake) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--attempts", str(attempts), "--backoff", "0"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


# ── Behaviour: the two paths, executed ───────────────────────────────────


def test_unobtainable_model_fails_loudly(tmp_path):
    """#2573 indicator 3 — an unavailable model must never look like a clean pass."""
    proc = _run(tmp_path, _ALWAYS_RAISES, summary=True)

    assert proc.returncode == 1, f"must fail, not degrade silently:\n{proc.stdout}"
    # The named annotation is the visible marker in the run UI. Both levels are
    # emitted: ::warning:: is the issue's literal ask, ::error:: is what marks
    # the check red.
    assert "::error::" in proc.stdout
    assert "::warning::" in proc.stdout
    assert ep.MODEL in proc.stdout
    assert ep.REVISION[:12] in proc.stdout
    # It names the consequence, so the failure cannot be misread as a code regression.
    assert "TF-IDF" in proc.stdout
    assert "#2573" in proc.stdout
    # And it appends the job-summary block a human reads to triage the run.
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "DEGRADED" in summary
    assert ep.MODEL in summary
    assert "hybrid-retrieval evidence" in summary


def test_annotations_are_two_complete_single_lines(tmp_path):
    """A newline inside an ::error:: annotation truncates it in the run UI (GH
    takes the first line only), and the REAL transformers error IS multi-line —
    the fake reproduces it verbatim. Pinning the exact count also stops a future
    edit from emitting a bare `::error::` with no actionable message.

    This is the test the raw implementation FAILED against the real library: the
    fake-based version passed because the fake's message was single-line.
    """
    proc = _run(tmp_path, _ALWAYS_RAISES)
    annotations = [ln for ln in proc.stdout.splitlines() if ln.startswith("::")]

    assert len(annotations) == 2, annotations
    levels = {ln.split("::", 2)[1] for ln in annotations}
    assert levels == {"warning", "error"}, levels
    for line in annotations:
        message = line.split("::", 2)[2]
        assert message, f"empty annotation message: {line!r}"
        assert "UNAVAILABLE" in message and "TF-IDF" in message, line
        # The whole failure text must survive on the annotation's ONE line —
        # including the tail of the real multi-line message.
        assert "offline-mode" in message, f"annotation truncated mid-message: {line!r}"
    # (The raw multi-line text still reaching the step LOG is intended — that is
    # where full triage detail belongs; only the annotation must be one line.)


def test_cached_model_succeeds_without_download(tmp_path):
    """The cache-hit path keeps the acceptance-criteria marker verbatim."""
    proc = _run(tmp_path, _FAITHFUL_CACHED)

    assert proc.returncode == 0, proc.stdout
    assert ep.CACHED_MARKER in proc.stdout
    assert ep.DOWNLOADED_MARKER not in proc.stdout
    assert "::error::" not in proc.stdout


def test_download_path_survives_the_offline_env_latch(tmp_path):
    """REGRESSION (the defect this change also fixes): the replaced heredocs set
    `HF_HUB_OFFLINE=1` BEFORE importing, and huggingface_hub freezes that into a
    module constant at import — so `del os.environ[...]` could not re-enable the
    network and EVERY retry failed without a packet leaving the process. A cache
    miss was therefore unrecoverable by construction.

    The fake latches at import, exactly like the real library: against the old
    implementation this test sees `_OFFLINE_AT_IMPORT is True` and fails.
    """
    proc = _run(tmp_path, _FAITHFUL_DOWNLOAD)

    assert proc.returncode == 0, (
        "the download path must actually run — an import-time offline latch would "
        f"make it dead code:\n{proc.stdout}"
    )
    assert ep.PROBE_FAILED_MARKER in proc.stdout
    assert ep.DOWNLOADED_MARKER in proc.stdout
    assert "::error::" not in proc.stdout


def test_exhausted_retries_report_attempt_count(tmp_path):
    """Every retry is attempted before the gate fails (the budget is real)."""
    proc = _run(tmp_path, _ALWAYS_RAISES, attempts=3)

    assert proc.returncode == 1
    assert "download attempt 1/3 failed" in proc.stdout
    assert "download attempt 3/3 failed" in proc.stdout


def test_missing_dependency_is_attributed_not_a_traceback(tmp_path):
    """A broken env must fail with the named marker, not a bare ImportError.

    The env is built by the shared helper, which strips `GITHUB_STEP_SUMMARY`:
    without that, this degraded-path test appends a false DEGRADED block to the
    summary of the real CI run that executes it.
    """
    fake = tmp_path / "fake"
    fake.mkdir(parents=True, exist_ok=True)
    # An empty module: `from sentence_transformers import SentenceTransformer` raises.
    (fake / "sentence_transformers.py").write_text("", encoding="utf-8")
    env = _base_env(tmp_path)
    env["PYTHONPATH"] = str(fake) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--attempts", "1", "--backoff", "0"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 1
    assert "::error::" in proc.stdout
    assert "could not import sentence_transformers" in proc.stdout


# ── Behaviour: pin exposure + lockstep ───────────────────────────────────


@pytest.mark.parametrize(
    ("flag", "expected"),
    [("--print-model", ep.MODEL), ("--print-revision", ep.REVISION)],
)
def test_print_pins(flag, expected):
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), flag], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == expected


def test_pins_match_the_shipped_embedder():
    """#1349 T10 — a drifted CI pin would cache and exercise the WRONG model."""
    from tortoise import embeddings

    assert ep.MODEL == embeddings.EMBEDDING_MODEL
    assert ep.REVISION == embeddings.EMBEDDING_MODEL_REVISION


# ── Wiring: the workflows cannot silently go back to a quiet degrade ─────


def _workflow(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))


def _gate_steps(job: dict) -> list[dict]:
    return [
        step for step in job.get("steps", []) if "embedder_provision.py" in str(step.get("run", ""))
    ]


def _all_gate_steps(name: str) -> list[tuple[str, dict]]:
    jobs = _workflow(name)["jobs"]
    return [(job, step) for job, jd in jobs.items() for step in _gate_steps(jd)]


# The exact provisioning invocation. Pinning the whole command (rather than
# checking a few tokens) is what closes the zero-exit escapes: `--help`/`-h`
# exit 0 via argparse, and an expansion can hide a flag from `shlex.split`.
_GATE_COMMAND_RE = re.compile(
    r"^python3 tools/embedder_provision\.py --attempts \d+ --backoff \d+$"
)


def test_python_ci_embedder_jobs_call_the_gate():
    jobs = _workflow("python-ci.yml")["jobs"]
    for name in _EMBEDDER_JOBS:
        assert len(_gate_steps(jobs[name])) == 1, f"{name}: expected exactly one embedder gate step"


def test_every_provisioning_workflow_routes_through_the_gate():
    """The same heredoc lived in four jobs across two files. A workflow that
    still provisions inline is a silent-degrade site the fix missed."""
    for name in _PROVISIONING_WORKFLOWS:
        assert _all_gate_steps(name), f"{name}: no embedder gate step — did provisioning stay inline?"
        text = (_WORKFLOWS / name).read_text(encoding="utf-8")
        assert "from sentence_transformers import SentenceTransformer" not in text, (
            f"{name}: an inline sentence_transformers load is back — route it through "
            "tools/embedder_provision.py so the loud-degrade contract is single-sourced"
        )
        assert "Pre-cache embedding model" not in text, f"{name}: the old inline step is back"


def test_gate_step_is_fail_closed():
    """The gate must not re-acquire `continue-on-error` — that IS the silent degrade."""
    for name in _PROVISIONING_WORKFLOWS:
        for job, step in _all_gate_steps(name):
            assert step.get("continue-on-error") in (None, False), (
                f"{name}:{job}: the embedder gate carries continue-on-error="
                f"{step.get('continue-on-error')!r} — a failure would be swallowed again (#2573)"
            )
            # A step-level timeout bounds a hung download; without one the step can
            # burn the whole job cap and take the run down with no summary.
            assert step.get("timeout-minutes"), f"{name}:{job}: gate has no timeout-minutes"


def test_gate_invocation_is_not_vacuous():
    """Pin the provisioning INVOCATION, not a substring of it.

    Two edits restore the silent degrade while every other wiring test stays
    green:
      * `--print-model` (plus `--attempts`, so the retry-budget assertion is
        satisfied), which exits 0 without provisioning anything; and
      * shell masking — `… --backoff 5 || true`, `… && true`, `; exit 0`,
        `… | tee f`, `… &` — which makes the step GREEN on failure. A GitHub
        `::error::` annotation is display-only and does NOT fail a step, so
        masking defeats the whole gate.

    The masking scan is a RAW-TEXT operator search, not a token search:
    `shlex.split('--backoff 5||true')` yields the single token `5||true`, so an
    operator glued to its argument (no surrounding spaces) escapes any
    token-level check. Every evasion found in review is covered by the tests
    below. The gate must be exactly one unmasked command, so a multi-line block
    (`set +e`, a wrapper) fails the head check on its first line.
    """
    # Shell control operators that can turn a non-zero exit into a zero one.
    masking_operators = ("||", "&&", ";", "|", "&", "`", "$(")
    for name in _PROVISIONING_WORKFLOWS:
        for job, step in _all_gate_steps(name):
            commands = [ln.strip() for ln in str(step["run"]).splitlines() if ln.strip()]
            assert len(commands) == 1, (
                f"{name}:{job}: the gate run block must be exactly one command; got "
                f"{commands!r} — a wrapper line can mask the gate's exit code"
            )
            command = commands[0]
            found = [op for op in masking_operators if op in command]
            assert not found, (
                f"{name}:{job}: the gate command is shell-masked by {found} ({command!r}) — "
                "a failure would leave the step GREEN and restore the silent degrade"
            )
            # Pin the WHOLE command, not a couple of tokens. A whitelist is what
            # closes the remaining zero-exit escapes: argparse's `--help`/`-h`
            # print usage and exit 0 without provisioning anything, and any
            # expansion (`${x:---print-model}`, `$IFS--print-model`) puts a
            # forbidden flag past `shlex.split` so a token check cannot see it.
            # An exact-match pin can only be changed deliberately.
            assert _GATE_COMMAND_RE.match(command), (
                f"{name}:{job}: the gate command is not the pinned provisioning "
                f"invocation ({command!r}) — an extra flag (`--help` exits 0 without "
                "provisioning), a wrapper or an expansion would leave the step GREEN"
            )


def test_gate_step_owns_the_requirement_by_name():
    """Attribution (#2898): the step NAME must say the embedder is required, so a
    red run points at the cause instead of at unrelated assertions."""
    for name in _PROVISIONING_WORKFLOWS:
        for job, step in _all_gate_steps(name):
            step_name = step["name"]
            assert "REQUIRED" in step_name.upper(), f"{name}:{job}: name does not own the requirement"
            assert ep.MODEL in step_name, f"{name}:{job}: name does not name the model"


def _suite_steps(job: dict) -> list[dict]:
    """The steps that actually run the test suite (pytest) in a job."""
    return [s for s in job.get("steps", []) if "pytest" in str(s.get("run", ""))]


def _default_shell(container: dict | None) -> object:
    """`defaults.run.shell` from a workflow or job mapping, or None."""
    run_defaults = ((container or {}).get("defaults") or {}).get("run") or {}
    return run_defaults.get("shell") if isinstance(run_defaults, dict) else None


def test_carrier_jobs_and_steps_cannot_mask_the_gate():
    """The `run:` block is not the only way to defeat the gate without touching
    it. Checked here, because each of these leaves the gate GREEN with the dense
    leg unprovisioned and every other wiring test passing:

    * a job-level `continue-on-error`, which swallows the step failure;
    * a step-level `shell:` override;
    * `defaults.run.shell` at workflow or job level — one level up from the step
      override and able to make every `run:` in the job exit 0;
    * an `if:` on the gate step that the suite does NOT share — then the gate is
      SKIPPED (green) while the suite runs. This is a real false green in
      `test-concurrency-falkor`, whose gate is the job's only embedder signal
      (its live tests swallow an embedding failure and pass). post-merge's gate
      legitimately carries the dedup `if:`, and its suite step carries the
      identical expression, so the invariant holds without an exemption.
    """
    for name in _PROVISIONING_WORKFLOWS:
        wf = _workflow(name)
        assert _default_shell(wf) in (None, ""), (
            f"{name}: workflow-level defaults.run.shell ({_default_shell(wf)!r}) can make "
            "every run: in the workflow exit 0"
        )
        for job_name, job in wf["jobs"].items():
            gates = _gate_steps(job)
            if not gates:
                continue
            assert job.get("continue-on-error") in (None, False), (
                f"{name}:{job_name}: job-level continue-on-error="
                f"{job.get('continue-on-error')!r} would swallow the embedder gate's failure"
            )
            assert _default_shell(job) in (None, ""), (
                f"{name}:{job_name}: job-level defaults.run.shell ({_default_shell(job)!r}) "
                "can make every run: in the job exit 0"
            )
            suite = _suite_steps(job)
            for step in gates:
                assert step.get("shell") in (None, ""), (
                    f"{name}:{job_name}: the gate overrides `shell:` ({step.get('shell')!r}) — "
                    "the shell can change how the exit code is interpreted"
                )
                gate_if = step.get("if")
                if gate_if is None:
                    continue
                assert suite, (
                    f"{name}:{job_name}: the gate is conditional (`if: {gate_if}`) but the job "
                    "has no pytest step — a skipped gate would be green with nothing run"
                )
                gated = [s for s in suite if s.get("if") == gate_if]
                assert gated, (
                    f"{name}:{job_name}: the gate's `if:` ({gate_if!r}) is shared by NO suite "
                    "step — the gate could be SKIPPED while the suite runs, leaving the job "
                    "green with the dense leg unprovisioned"
                )
                # A suite step that runs REGARDLESS of the gate's condition is only
                # acceptable when it is `always()`-guarded reconciliation (the
                # skip-fail guards parse an existing log; they measure nothing new
                # and no-op on a missing log). Anything else could run while the
                # gate is skipped.
                for step_ in suite:
                    if step_.get("if") == gate_if:
                        continue
                    assert str(step_.get("if", "")).startswith("always()"), (
                        f"{name}:{job_name}: a pytest step with `if: {step_.get('if')!r}` "
                        f"neither shares the gate's condition ({gate_if!r}) nor is "
                        "always()-guarded — it could run while the gate is skipped"
                    )


def test_gate_tool_has_a_ci_carveout():
    """#3261 silent-drop class: without a TOOL_CARVEOUTS entry a gate-only change
    classifies as docs-only, and this very test file never runs on the PR that
    edits the gate — so assert the SELECTION behaviour, not just membership."""
    from tools.ci_selection import TOOL_CARVEOUTS, load_manifest, select

    assert "tools/embedder_provision.py" in TOOL_CARVEOUTS
    result = select(["tools/embedder_provision.py"], "pull_request", load_manifest())
    assert result.get("full") is True, (
        "a gate-only change must fail closed to the full matrix; got "
        f"surfaces={result.get('surfaces')!r} full={result.get('full')!r}"
    )
