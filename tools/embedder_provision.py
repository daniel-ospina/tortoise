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

import argparse
import os
import sys
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


if __name__ == "__main__":
    sys.exit(main())
