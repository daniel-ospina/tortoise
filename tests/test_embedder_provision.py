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
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import embedder_provision as ep

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "tools" / "embedder_provision.py"
_WORKFLOWS = _ROOT / ".github" / "workflows"
# Every workflow that provisions the embedder. The same heredoc lived in five
# jobs across these two files; a fix that lands in only one of them is the drift
# this list exists to catch.
_PROVISIONING_WORKFLOWS = ("python-ci.yml", "post-merge-validation.yml")
# The four python-ci jobs that provision the embedder for the dense leg.
_EMBEDDER_JOBS = ("test", "test-slow", "test-concurrency-falkor", "test-carve-out")


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
# `exec` is part of the pin (#7359): actions/runner sends the cancel signal to
# the step's DIRECT CHILD with `killProcessOnCancel: false`, so without `exec`
# bash stays in the middle, Python is a grandchild, and no signal — and therefore
# no stack dump — ever reaches the script.
_GATE_COMMAND_RE = re.compile(
    r"^exec python3 tools/embedder_provision\.py --attempts \d+ --backoff \d+$"
)


def test_python_ci_embedder_jobs_call_the_gate():
    jobs = _workflow("python-ci.yml")["jobs"]
    for name in _EMBEDDER_JOBS:
        assert len(_gate_steps(jobs[name])) == 1, f"{name}: expected exactly one embedder gate step"


def test_every_provisioning_workflow_routes_through_the_gate():
    """The same heredoc lived in five jobs across two files. A workflow that
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


# ── #7359: the stall is diagnosed by the run that hits it ─────────────────

_HANGS_FOREVER = (
    "import time\n"
    "class SentenceTransformer:\n"
    "    def __init__(self, *a, **k):\n"
    "        print('FAKE_HANG_ENTERED', flush=True)\n"
    "        time.sleep(600)\n"
)

# The #7359 class: the main thread blocked inside NATIVE code, where a
# Python-level signal handler can never run (no eval-loop point is reached).
# `pthread_mutex_lock` on a mutex held by ANOTHER thread is a genuinely
# uninterruptible C block, and unlike a self-relock its behaviour is well
# defined on every libc (PTHREAD_MUTEX_DEFAULT may return EDEADLK instead of
# blocking, which would make the fake not block at all).
_HANGS_IN_NATIVE_CODE = (
    "import ctypes, threading\n"
    "\n"
    "_libc = ctypes.CDLL(None)\n"
    "_buf = ctypes.create_string_buffer(64)\n"
    "_libc.pthread_mutex_init(ctypes.byref(_buf), None)\n"
    "_held = threading.Event()\n"
    "\n"
    "def _hold():\n"
    "    _libc.pthread_mutex_lock(ctypes.byref(_buf))\n"
    "    _held.set()\n"
    "    threading.Event().wait()  # hold it forever\n"
    "\n"
    "threading.Thread(target=_hold, daemon=True).start()\n"
    "assert _held.wait(10), 'helper never took the mutex'\n"
    "\n"
    "class SentenceTransformer:\n"
    "    def __init__(self, *a, **k):\n"
    "        print('FAKE_NATIVE_HANG_ENTERED', flush=True)\n"
    "        _libc.pthread_mutex_lock(ctypes.byref(_buf))  # held by the helper\n"
)


def _run_until_hang(tmp_path, fake_source, marker, env_extra=None):
    """Start the script against a fake, wait until it is INSIDE the hang, return proc."""
    fake = tmp_path / f"fake-{abs(hash(fake_source)) % 10**8}"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / "sentence_transformers.py").write_text(fake_source, encoding="utf-8")
    env = _base_env(tmp_path)
    env["PYTHONPATH"] = str(fake) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    proc = subprocess.Popen(
        [sys.executable, str(_SCRIPT), "--attempts", "1", "--backoff", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    # Positive evidence it is inside the hang, rather than a fixed sleep guess.
    ready, _, _ = select.select([proc.stdout], [], [], 60)
    assert ready, "script never reached the fake hanging constructor"
    assert marker in proc.stdout.readline(), marker
    return proc


def test_a_cancelled_step_dumps_every_thread_stack(tmp_path):
    """A DELIVERED signal must produce a dump.

    SCOPE, stated because the first version of this test overclaimed: it proves
    the C-level signal handler dumps when a signal is DELIVERED TO THIS PROCESS.
    It does not prove the runner delivers it — that is the `exec` pin in
    `_GATE_COMMAND_RE` — and it does not cover the native-blocked class, which a
    Python-level handler cannot reach at all and which
    `test_the_watchdog_dumps_a_native_blocked_thread` covers.

    What this test actually pins is the narrower, still-real contract: a
    DELIVERED signal produces a dump carrying real frames. The native-blocked
    case, the one that discriminates, is the watchdog test below.

    Termination is asserted for SIGTERM only. `chain=True` chains to whatever
    disposition the process INHERITED, and SIGINT is commonly inherited as
    SIG_IGN (a backgrounded job from a non-interactive shell, `trap '' INT`, a
    `nohup`-style harness) — the chain preserves the ignore, the process dumps
    and then SURVIVES, and a `communicate(timeout=...)` here raised
    TimeoutExpired and reddened the suite after a 60s stall. That is the module's
    own documented contract for SIGINT, so requiring an exit was the test
    contradicting the implementation (P1, seventh review). The dump is read from
    the raw fd with a deadline instead, and the process is killed.
    """
    for sig in (signal.SIGTERM, signal.SIGINT):
        proc = _run_until_hang(tmp_path, _HANGS_FOREVER, "FAKE_HANG_ENTERED")
        try:
            proc.send_signal(sig)
            seen, deadline = b"", time.monotonic() + 30
            fd = proc.stderr.fileno()
            while time.monotonic() < deadline and b"sentence_transformers" not in seen:
                ready, _, _ = select.select([fd], [], [], 5)
                if not ready:
                    continue
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                seen += chunk
            # faulthandler writes its own header; the fake's frame must be in
            # the body or the dump is not the one we think it is.
            assert b"Current thread" in seen, (
                f"no faulthandler dump on {sig!r}:\n" + seen.decode(errors="replace")
            )
            assert b"sentence_transformers" in seen, (
                "dump had no useful frames:\n" + seen.decode(errors="replace")
            )
            if sig is signal.SIGTERM:
                # SIGTERM chains to SIG_DFL, which inheritance cannot change.
                # WAIT, do not poll: the dump is written from inside the C signal
                # trampoline, so the exit has not completed at the moment the
                # last frame lands in the pipe.
                assert proc.wait(timeout=30) != 0, "SIGTERM must terminate the process"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


def test_the_watchdog_dumps_a_native_blocked_thread(tmp_path):
    """#7359's ACTUAL failure class: the main thread blocked in native code.

    This is the case that made the previous `signal.signal` version inert — a
    Python-level handler needs an eval-loop point the thread never reaches, and
    replacing the disposition means the process no longer even dies on the
    signal. The watchdog thread needs no signal delivery and no eval-loop point,
    so it is the mechanism that answers this issue.

    The watchdog dumps WITHOUT exiting (`exit=False`), so this asserts the dump
    is produced and then kills the still-blocked process — asserting a non-zero
    exit here would pin the very `exit=True` behaviour that was killing working
    runs (P1, fifth review).

    Load-bearing: delete the `dump_traceback_later` arm and no dump ever appears.

    NOTE the two diagnostic tests are NOT symmetric, and the asymmetry is the
    point (P2, third review): this one discriminates the round-2 fix, whereas
    the signal test above does not — it was rebuilt against the round-2
    `signal.signal` implementation and PASSED, which is how an inert handler
    survived two reviews. Only a native-blocked main thread separates a
    Python-level handler from a C-level one.
    """
    proc = _run_until_hang(
        tmp_path, _HANGS_IN_NATIVE_CODE, "FAKE_NATIVE_HANG_ENTERED",
        {"TORTOISE_EMBEDDER_WATCHDOG_S": "1"},
    )
    try:
        # Read the RAW fd, not `proc.stderr`. This reader must NEVER use
        # `proc.stderr.read()`/`.readline()`: those drain bytes into the
        # TextIOWrapper's userspace buffer, after which `select` reports the fd
        # empty forever and the loop spins to its deadline (P1, sixth review: 1
        # failed / 21 passed). `os.read` bypasses the wrapper entirely — it does
        # NOT see bytes the wrapper already swallowed, which is precisely why
        # nothing here may hand any to it.
        fd = proc.stderr.fileno()
        seen = b""
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 5)
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            seen += chunk
            # `embedder_provision.py` appears only on the MAIN thread's stack;
            # the helper thread carries the fake module alone. Asserting on
            # "sentence_transformers" alone was satisfied by the HELPER's frame,
            # so a green run did not prove the blocked main thread was captured.
            # `Thread 0x` anchors it to a faulthandler dump, so an ordinary
            # traceback mentioning the script path cannot satisfy it either.
            if b"embedder_provision.py" in seen and b"Thread 0x" in seen:
                break
        assert b"Thread 0x" in seen, (
            "no faulthandler dump:\n" + seen.decode(errors="replace")
        )
        assert b"embedder_provision.py" in seen, (
            "no dump of the MAIN thread:\n" + seen.decode(errors="replace")
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def test_the_print_pins_are_byte_exact_and_survive_abbreviation(tmp_path):
    """--print-model/--print-revision emit a machine-read pin, so
    the #7359 completion marker must not contaminate them.

    The abbreviation cases are the ones that matter and the ones the FIRST
    version missed: argparse has `allow_abbrev=True`, so `--print-m` is a valid
    unambiguous spelling that returns the pin early. The first implementation
    gated the marker on `{"--print-model", "--print-revision"} & set(sys.argv[1:])`,
    which no abbreviation matches — so `--print-m` printed the pin AND the
    marker. Emitting from control flow in `main()` is what closes it, and these
    cases are what keeps it closed.
    """
    env = _base_env(tmp_path)
    cases = (
        ("--print-model", ep.MODEL),
        ("--print-revision", ep.REVISION),
        ("--print-m", ep.MODEL),      # abbreviation — defeated the first gate
        ("--print-r", ep.REVISION),   # abbreviation
    )
    for flag, expected in cases:
        got = subprocess.run(
            [sys.executable, str(_SCRIPT), flag],
            capture_output=True, text=True, env=env, timeout=120,
        )
        assert got.returncode == 0, f"{flag}: rc={got.returncode} {got.stderr!r}"
        assert got.stdout == expected + "\n", (
            f"{flag} stdout must be EXACTLY the pin and a newline — a consumer of "
            f"check parses it; got {got.stdout!r}"
        )
        assert ep.DONE_MARKER not in got.stdout, (
            f"{flag}: the completion marker leaked into machine-read output"
        )


def test_the_completion_marker_is_emitted_on_success_only(tmp_path):
    """The marker is the diagnostic that tells a future reader whether a stall
    is inside the interpreter or outside it, so its PRESENCE on success and its
    ABSENCE on failure are both load-bearing. Without the first assertion,
    deleting the marker would go unnoticed and the diagnostic would silently
    stop existing.
    """
    env = _base_env(tmp_path)

    # Success: the cache probe hits.
    fake_ok = tmp_path / "fake-ok"
    fake_ok.mkdir(parents=True, exist_ok=True)
    (fake_ok / "sentence_transformers.py").write_text(_FAITHFUL_CACHED, encoding="utf-8")
    ok_env = dict(env)
    ok_env["PYTHONPATH"] = str(fake_ok) + os.pathsep + env.get("PYTHONPATH", "")
    ok = subprocess.run(
        [sys.executable, str(_SCRIPT), "--attempts", "1", "--backoff", "0"],
        capture_output=True, text=True, env=ok_env, timeout=120,
    )
    assert ok.returncode == 0, ok.stderr
    assert ep.DONE_MARKER in ok.stdout, (
        "the success path must emit the marker — it is the only evidence that a "
        f"later stall is OUTSIDE the interpreter; got {ok.stdout!r}"
    )

    # Failure: the model is unobtainable. A "complete" line here would let a
    # grep read a failed provision as a finished one.
    fake_bad = tmp_path / "fake-bad"
    fake_bad.mkdir(parents=True, exist_ok=True)
    (fake_bad / "sentence_transformers.py").write_text(_ALWAYS_RAISES, encoding="utf-8")
    bad_env = dict(env)
    bad_env["PYTHONPATH"] = str(fake_bad) + os.pathsep + env.get("PYTHONPATH", "")
    bad = subprocess.run(
        [sys.executable, str(_SCRIPT), "--attempts", "1", "--backoff", "0"],
        capture_output=True, text=True, env=bad_env, timeout=120,
    )
    assert bad.returncode == 1, bad.stderr
    assert ep.DONE_MARKER not in bad.stdout, (
        f"the marker must NOT appear on the failure path; got {bad.stdout!r}"
    )


def test_the_process_never_runs_atexit_teardown(tmp_path):
    """The PR's CENTRAL fix, which had no test at all (P2, third review).

    Swapping `os._exit(_rc)` back to `sys.exit(_rc)` left all 20 tests green
    (identical stdout, identical exit codes on success, failure and both print
    flags), so the teardown-skip this whole change is about was unpinned.

    The fake registers an `atexit` hook and drops a sentinel file. `sys.exit`
    runs it during normal interpreter shutdown; `os._exit` never does. So the
    sentinel's ABSENCE is the assertion, and the assertion can fail.

    Load-bearing: change the `__main__` exit to `sys.exit(_rc)` and this fails.
    """
    fake = tmp_path / "fake-atexit"
    fake.mkdir(parents=True, exist_ok=True)
    sentinel = tmp_path / "atexit-ran"
    (fake / "sentence_transformers.py").write_text(
        "import pathlib\n"
        "class SentenceTransformer:\n"
        "    def __init__(self, *a, **k):\n"
        "        import atexit\n"
        f"        atexit.register(lambda: pathlib.Path({str(sentinel)!r}).write_text('ran'))\n",
        encoding="utf-8",
    )
    env = _base_env(tmp_path)
    env["PYTHONPATH"] = str(fake) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--attempts", "1", "--backoff", "0"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert not sentinel.exists(), (
        "atexit teardown ran — the process left via sys.exit, so the unbounded "
        "interpreter shutdown #7359 measured is back"
    )


def test_every_watchdog_margin_sits_between_the_backoff_budget_and_the_step_cap():
    """A per-site invariant, because a flat default regressed (P1, third review).

    Sitting below the site's own worst-case retry sleep produces a premature
    dump while the script is still legitimately sleeping between retries;
    sitting at or above `timeout-minutes` makes the dump miss its window (the
    runner's own kill arrives first). Both bounds are per site, so the check is
    too — and both are derived from the formula rather than restated, because
    restating the budget is exactly what went wrong here.

    Enumerated with `_all_gate_steps`, the SAME selector the wiring tests use,
    not a name-prefix split: the earlier version matched only steps literally
    named `Embedding model REQUIRED`, so a gate step named differently escaped
    the check entirely — the reviewer added one to a scratch copy and the suite
    stayed green (P2, fifth review).

    Load-bearing: a 10-minute site's watchdog set to 60 (below the real 100s
    budget) or to 600 (= the cap) both fail; verified by mutation.
    """
    checked = 0
    for name in ("python-ci.yml", "post-merge-validation.yml"):
        for job, step in _all_gate_steps(name):
            where = f"{name}:{job}"
            assert "TORTOISE_EMBEDDER_WATCHDOG_S" in (step.get("env") or {}), (
                f"{where}: the gate step sets no watchdog margin"
            )
            raw = str(step["env"]["TORTOISE_EMBEDDER_WATCHDOG_S"])
            assert raw.isdigit(), f"{where}: watchdog {raw!r} is not a plain integer"
            # Assert on the CLAMPED runtime value, not the YAML literal: a site
            # setting `0` would arm a 1s watchdog and still pass a
            # literal-vs-literal comparison.
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("TORTOISE_EMBEDDER_WATCHDOG_S", raw)
                armed = ep._watchdog_seconds()
            cap = int(step["timeout-minutes"]) * 60
            attempts, backoff = re.search(
                r"--attempts (\d+) --backoff (\d+)", step["run"]
            ).groups()
            # WORST-CASE retry sleep: attempts sleep `backoff * attempt` for
            # attempt 1..n-1, so they SUM to backoff * n*(n-1)/2. `(n-1)*backoff`
            # is wrong by 2.5x at the 10-minute sites and let a 60s watchdog
            # pass at a 100s-budget site (P1, fourth review).
            n, b = int(attempts), int(backoff)
            budget = b * n * (n - 1) // 2
            assert budget < armed < cap, (
                f"{where}: watchdog {armed}s is outside ({budget}s, {cap}s)"
            )
            checked += 1
    assert checked == 5, f"expected 5 embedder gate steps, found {checked}"
