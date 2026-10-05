#!/usr/bin/env python3
"""embedder_provision.py — obtain the pinned embedding model, LOUDLY (#2573).

Tortoise's core retrieval is HYBRID (dense + keyword). On a CI runner the dense
leg needs ``BAAI/bge-small-en-v1.5`` at the pinned revision, restored from the
``hf-embedding-cache-v*`` actions/cache entry or downloaded from
``huggingface.co``. When neither works, ``tortoise/embeddings.py`` degrades to
the documented keyword-only TF-IDF fallback and the suite reports GREEN — so a
run that never exercised the dense leg was indistinguishable from one that did,
and every "retrieval passes" claim made on that run was unproven.

This script is the single step that owns "the embedder is required", replacing
four near-identical inline heredocs in ``.github/workflows/``:

* exit **0** — the pinned revision loaded. The two stdout markers the issue's
  acceptance criteria name are preserved verbatim
  (``embedding model: cached, no download needed`` / ``embedding model: downloaded``).
* exit **1** — the model could not be obtained. It prints a GitHub
  ``::warning::`` AND an ``::error::`` annotation naming the consequence
  (keyword-only retrieval, NOT hybrid), appends a step-summary block, and exits
  non-zero so the job fails at a step whose NAME says what broke — instead of
  cascading into unrelated assertion failures plus a fail-closed skip-guard trip
  (#3760) that reads as a code regression.

THE DEFECT THIS ALSO FIXES — the download fallback never ran
------------------------------------------------------------
The heredocs this replaces set ``os.environ["HF_HUB_OFFLINE"] = "1"`` **before**
``from sentence_transformers import SentenceTransformer``, then did
``del os.environ["HF_HUB_OFFLINE"]`` to "re-enable" the network. That does not
work: ``huggingface_hub.constants`` reads the variable into a module-level
constant **at import time**, and every HTTP request is refused while it is set.
The delete could not lift offline mode, so **every retry failed without a packet
leaving the process** — a cache miss was unrecoverable by construction, and the
resulting ``We couldn't connect to 'https://huggingface.co'`` blamed the network
for a self-inflicted condition.

Measured on this machine (cold ``HF_HOME``, egress available):

* the pre-fix code → ``download attempt 1/1 failed: OSError: We couldn't connect
  to 'https://huggingface.co'`` — no download attempted;
* a plain load with no ``HF_HUB_OFFLINE`` set → **downloaded successfully**.

The probe therefore uses ``local_files_only=True`` instead of the env var, which
scopes "cache only" to the probe and leaves the download path genuinely live.
(This also removes the false premise the issue was bookkept on: python-ci run
35181657953 printed ``embedding model: cached, no download needed`` in all three
jobs and **never** printed ``downloaded`` — the "downloaded" log hits that were
read as evidence of runner egress were the shell ECHOING the heredoc source, not
the step's output. Runner egress is therefore UNPROVEN, which is exactly why a
real download path matters.)

The retry budget stays configurable so each job keeps the budget its replaced
heredoc had (``--attempts 3 --backoff 5`` in python-ci's ``test`` and in
post-merge's ``validate``; ``5``/``10`` in ``test-slow`` and
``test-concurrency-falkor``) — the tight budget is what makes an unreachable HF
fail fast instead of burning the leg's wall-clock (#1211).

``--print-model`` / ``--print-revision`` expose the pins so the workflow-side
lockstep with ``tortoise/embeddings.py`` is testable (see
``tests/test_embedder_provision.py``).
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/embedder_provision.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/embedder_provision.py`"
    )

import argparse
import contextlib
import faulthandler
import math
import os
import signal
import time

# Keep in lockstep with tortoise/embeddings.py (EMBEDDING_MODEL /
# EMBEDDING_MODEL_REVISION). tests/test_embedder_provision.py pins the lockstep:
# if the product moves the model or its revision and this does not follow, the
# CI cache would silently exercise the WRONG embedder (#1349 T10).
MODEL = "BAAI/bge-small-en-v1.5"
REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"  # pinned (VULN-001)

CACHED_MARKER = "embedding model: cached, no download needed"
DOWNLOADED_MARKER = "embedding model: downloaded"
PROBE_FAILED_MARKER = "embedding model: not cached — downloading (with retries)"


DONE_MARKER = "embedder provision: complete"


def _watchdog_seconds() -> float:
    """Seconds before the watchdog fires (and exits non-zero with a dump).

    Set PER SITE in the workflow, because the two bounds it must sit between are
    per site (P1, third review of #7364). A flat default was wrong: it killed a
    legitimately-progressing run at the three `--attempts 5 --backoff 10` sites,
    whose own declared budget is 10 minutes and whose comment says so — 100s of
    backoff alone before the final probe, plus a slow-but-working download. The
    invariant to preserve when editing a site's `timeout-minutes`:

        backoff budget  <  TORTOISE_EMBEDDER_WATCHDOG_S  <  timeout-minutes * 60

    where the budget is the WORST-CASE retry sleep, `backoff * n*(n-1)/2` — the
    attempts sleep `backoff * attempt` for attempt 1..n-1, so they SUM, and
    `(n-1)*backoff` understates the 10-minute sites by 2.5x (40s vs 100s). That
    error was in the first version of this comment AND in the test that guards
    the invariant, which let a 60s watchdog pass at a 100s-budget site (P1,
    fourth review) — state both bounds from the formula, not from memory.

    Sites set 330 (under the 6-minute `test`/pmv cap) and 570 (under the three
    10-minute caps). The fallback below is the tighter one on the principle that
    firing early yields a dump, whereas firing late yields nothing.
    """
    raw = os.environ.get("TORTOISE_EMBEDDER_WATCHDOG_S", "330")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 330.0
    if not math.isfinite(value):  # float('inf')/float('nan') -> OverflowError in dump_traceback_later
        return 330.0
    return min(3600.0, max(1.0, value))


def _install_termination_stack_dump() -> None:
    """#7359: make a stall dump its own stack, by two mechanisms, because one
    is not enough and the gap between them is the whole point.

    MEASURED: run 37239542471 spent 6.27m in this step against 0.13m on main,
    and its log ENDS with the success marker — so the six minutes are spent
    AFTER the last Python statement, in interpreter/native teardown
    (`sentence_transformers` imports torch, whose teardown joins thread pools
    and runs C++ destructors).

    A Python-level `signal.signal` handler CANNOT fire in that state. It runs
    only when the main thread reaches an eval-loop / `PyErr_CheckSignals` point,
    and a thread blocked inside native code never gets there — worse, replacing
    the disposition means the process no longer dies on the signal either, so it
    sits until the runner's SIGKILL. A previous version of this function used
    `signal.signal` and was therefore inert for exactly the failure class it was
    written for (P1, second review of #7364); the reviewer reproduced it against
    a fake blocking in a C `pthread_join`. Both mechanisms below avoid that:

    1. **`faulthandler.register`** — a C-LEVEL handler, so the dump happens
       inside the signal trampoline regardless of what the main thread is
       doing. Covers a runner cancel that is delivered here. `chain=True` is
       required (`chain=False` was measured to swallow BOTH signals, leaving the
       process alive), but it resolves differently per signal and the difference
       matters: for SIGTERM it chains to `SIG_DFL` and the process terminates; for
       SIGINT it chains to Python's own deferred `KeyboardInterrupt` handler,
       which a main thread blocked in C will never run — so the process dumps
       and SURVIVES until the runner's later SIGTERM. That is acceptable because
       the runner escalates on a fixed 7500ms/2500ms schedule, but it is not the
       blanket "chain=True still terminates" the first version of this comment
       claimed.
    2. **`faulthandler.dump_traceback_later`** — a WATCHDOG THREAD, needing no
       signal delivery at all, so it covers a main thread wedged in native code
       AND a cancel that never reaches this process. This is the one that
       answers the motivating failure.

    Signal DELIVERY is a separate precondition and is handled in the workflows:
    `actions/runner` sends the cancel signal to the step's direct child
    (`killProcessOnCancel: false`; SIGINT at 7500ms, then SIGTERM, then SIGKILL),
    which for a `run:` block is bash — and bash forks rather than exec'ing, so
    the five invocations carry `exec` to put this process on the receiving end.

    KNOWN LIMIT: `all_threads=True` lists PYTHON threads only. torch's pool and
    OpenMP workers are native threads and do not appear, and 3.12 has no
    `dump_c_stack` (3.14+). An apparently-sparse dump must not be read as
    "nothing was running".
    faulthandler.enable() is inside the suppression too: with an unusable
    stderr it raises `RuntimeError: sys.stderr is None`, which would propagate
    out of here and fail the step WITHOUT provisioning — a diagnostic that
    fails the gate it was added to diagnose (P2, fourth review).
    """
    with contextlib.suppress(ValueError, RuntimeError, OSError):
        faulthandler.enable()
    for _sig in (signal.SIGTERM, signal.SIGINT):
        # An unsupported platform or a non-main thread loses the diagnostic only.
        with contextlib.suppress(ValueError, RuntimeError, OSError):
            faulthandler.register(_sig, all_threads=True, chain=True)
    with contextlib.suppress(ValueError, RuntimeError, OSError, OverflowError):
        faulthandler.dump_traceback_later(_watchdog_seconds(), exit=True)


def _annotation(level: str, message: str) -> None:
    """Emit a GitHub Actions workflow annotation (single line — newlines break it)."""
    print(f"::{level}::{message}", flush=True)


def _one_line(text: str) -> str:
    """Collapse all whitespace runs to single spaces.

    GitHub takes only the FIRST line of an ``::error::``/``::warning::`` payload,
    and the REAL transformers/huggingface_hub failure is multi-line ("...couldn't
    find them in the cached files.\\nCheck your internet connection or see how to
    run the library in offline mode...").  Leaving it raw silently truncated the
    annotation to a dangling sentence — the annotation, not the fake, is what a
    human reads in the run UI.  The full text still reaches the step log via the
    ``download attempt N/M failed:`` line.
    """
    return " ".join(text.split())


def _step_summary(detail: str) -> None:
    """Append a degraded-mode block to the job summary, best-effort."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(
                "### ❌ Embedding model unavailable — this run is DEGRADED\n\n"
                f"`{MODEL}` @ `{REVISION}` could not be obtained "
                "(cache miss + download failed).\n\n"
                "The dense retrieval leg was **not exercised**: the suite can only "
                "run keyword-only (TF-IDF) retrieval, so this run must NOT be read as "
                "hybrid-retrieval evidence (#2573).\n\n"
                f"Last error: `{detail}`\n"
            )
    except OSError:
        pass  # a summary write failure must not mask the real failure below


def provision(*, attempts: int, backoff: float) -> tuple[bool, str]:
    """Load the pinned revision, preferring the actions-cache copy.

    Returns ``(ok, detail)``; ``detail`` carries the last error text when ``ok``
    is False. Never raises for an unavailable model — the caller decides the exit
    code, so the failure is reported by this script's own message, not a bare
    traceback.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # env fault (missing extra) — reported, not raised
        return False, f"could not import sentence_transformers: {exc!r}"

    # Cache probe first, via `local_files_only` — NOT `HF_HUB_OFFLINE`. Setting
    # the env var here would latch offline mode for the whole process (see the
    # module docstring): huggingface_hub freezes it at import, so the download
    # loop below would fail forever without attempting a request.
    try:
        SentenceTransformer(MODEL, revision=REVISION, local_files_only=True)
        print(CACHED_MARKER, flush=True)
        return True, ""
    except Exception:
        pass

    print(PROBE_FAILED_MARKER, flush=True)
    detail = "(no exception captured)"
    for attempt in range(1, attempts + 1):
        try:
            SentenceTransformer(MODEL, revision=REVISION)
            print(DOWNLOADED_MARKER, flush=True)
            return True, ""
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            print(f"download attempt {attempt}/{attempts} failed: {detail}", flush=True)
            if attempt < attempts:
                time.sleep(backoff * attempt)
    return False, detail


def main(argv: list[str] | None = None) -> int:
    _install_termination_stack_dump()
    parser = argparse.ArgumentParser(description="Provision the pinned embedding model, loudly.")
    parser.add_argument("--attempts", type=int, default=3, help="download attempts (>=1)")
    parser.add_argument("--backoff", type=float, default=5.0, help="base backoff seconds")
    parser.add_argument("--print-model", action="store_true", help="print MODEL and exit")
    parser.add_argument("--print-revision", action="store_true", help="print REVISION and exit")
    args = parser.parse_args(argv)

    if args.print_model:
        print(MODEL)
        return 0
    if args.print_revision:
        print(REVISION)
        return 0

    ok, detail = provision(attempts=max(1, args.attempts), backoff=max(0.0, args.backoff))
    if ok:
        # Emitted from CONTROL FLOW, not from a `sys.argv` scan. The first
        # attempt gated this in __main__ on `{"--print-model", "--print-revision"}
        # & set(sys.argv[1:])`, which argparse's default `allow_abbrev=True`
        # defeats: `--print-m` is a valid, unambiguous spelling that returns the
        # pin early, yet matched no string in that set — so the marker
        # machine-read output a consumer parses (P1, review
        # of #7364). Returning from here cannot be abbreviation-bypassed, and it
        # is also success-only, so `grep` for it never reads a failure as done.
        print(DONE_MARKER, flush=True)
        return 0

    detail = _one_line(detail)
    headline = (
        f"embedding model {MODEL}@{REVISION[:12]} UNAVAILABLE — the dense retrieval leg "
        "was NOT exercised: this run can only exercise keyword-only (TF-IDF) retrieval, so "
        "it is NOT hybrid-retrieval evidence (#2573). Last error: " + detail
    )
    _annotation("warning", headline)
    _annotation("error", headline)
    _step_summary(detail)
    return 1


# #7359: leave via os._exit, NOT sys.exit.
#
# MEASURED: run 37239542471 (PR #5339) spent 6.27m in this step, against 0.13m for
# the same step on main (37240061542, 37229990684). The step's log ends with the
# #2573 success marker and nothing after it:
#
#     Loading weights: 100%|##########| 199/199 [...]
#     embedding model: cached, no download needed
#     ##[error] ... has timed out after 6 minutes.
#
# Python runs interpreter shutdown AFTER the last user statement, so the marker
# being the final line places the six minutes in teardown, not in this script:
# `sentence_transformers` imports torch, whose teardown joins its thread pools
# and runs C++ destructors. That is normally milli­seconds of work, but it is
# not bounded and it is not this step's business — the step's contract is "is
# the pinned embedder obtainable", and that answer is already established and
# printed by the time we get here.
#
# `os._exit` skips atexit handlers and interpreter teardown and returns the code
# we already computed. It is safe on both paths: stdout/stderr are flushed
# explicitly below (every marker already flushes), the cached path performs no
# writes needing finalisation, and the download path writes to the HF cache
# during construction rather than at teardown.
#
# This is deliberately NOT `timeout-minutes` being raised: the step takes ~9s
# when it works, so a longer bound would convert a 6-minute stall into a longer
# one and hide it (#964's bound was written for a stalled download, which the
# log's `cached, no download needed` rules out).
if __name__ == "__main__":
    _rc = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(_rc)
