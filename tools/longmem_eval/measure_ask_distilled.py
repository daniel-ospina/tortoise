"""#9097 — the ask lane's SEALED exit measurement (epic #2281 Q3 exit bar).

`tools/ask_spotcheck.py`'s ``--distilled`` leg (#9095) is an *instrument*: it
prints a number. This module is the *benchmark* — it turns that instrument into
a publishable claim, which needs four things the instrument did not have:

1. **A pre-registration** (:func:`write_preregistration`) written and committed
   BEFORE any arm runs: corpus identity + hash, classes, split, scoring, the
   decision rule, the reader and the judge. The bar is fixed in advance, so the
   report cannot be a number chosen after seeing the result.
2. **A sealed gold** (``tests/eval/ask/gold/``) that the RUN step cannot read.
   :func:`run_sealed` is handed ``corpus.load_fixtures`` — fixtures only — and
   writes OBSERVATIONS (answer + evidence). :func:`score` is the separate step
   that reads the sealed key and applies the judge. Blindness is therefore a
   property of the API, not of a reviewer's promise.
3. **An exit bar that can say NO** (:func:`evaluate_exit_bar`). Two failure
   modes, both pre-registered:

   * **the D8 product gate** — no superseded claim may be rendered to the
     reader as a live claim, and every planted claim must be OBSERVED (an
     unmeasured claim is never a pass);
   * **no verbatim/raw regression** — the same case runs in BOTH arms of one
     invocation (the D4 paired read), and a distilled arm that loses a case the
     raw arm commits is a FAIL, not a trade.

   ⛔ **The knowledge-update rate is reported and NEVER thresholded.** That is
   the epic's D8 rule: the benchmark-side X set comes from the first published
   run. The two failures above need no number — one is a comparison internal to
   the run, the other is a product promise.
4. **A publication receipt** (:func:`build_receipt` + :func:`validate_ask_receipt`)
   in the W7 vocabulary: exact commit, corpus hash, judge pin, reader model,
   cost. It reuses ``w7_publish.build_receipt`` and ``VERDICT_VALUES`` (both
   benchmark-generic) but NOT ``w7_publish.validate_receipt``, which is
   LongMemEval-gated: it demands ``official_recall_all@k`` and the
   ``CATEGORY_GATE_TYPES`` rows, metrics an ask run cannot have. Passing an ask
   receipt through it would either fail or force LongMemEval metric names onto
   an ask run — so this module validates the ASK metric vocabulary instead.

### What "sealed" and "pre-registered" do and do not prove

Two limits are worth stating where they are implemented, because both look
stronger than they are:

* **Sealing is structural, not cryptographic.** The RUN step is handed fixtures
  only, so the runner cannot *grade itself* — that is the property under test.
  It is **not** a secret: for a knowledge-update case the answer is already one
  of the planted claims (or in the turn store), and the full corpus hash digests
  ``gold/``, so handing the runner that digest would be a weak oracle over an
  answer whose shape is otherwise known. So the run records
  :func:`corpus.compute_fixtures_hash` and the SCORE step computes the full
  corpus hash; a reader that could see both would not gain much, but there is no
  reason to hand it over.
* **``written_before_any_arm_run`` is a declaration, verified by history, not
  by this module.** The ordering claim ("the protocol commit is an ancestor of
  the run commit") lives in git, and the run report's Reproduction section
  points at ``git log -- tests/eval/ask/preregistration.json``. What the bar
  DOES enforce is the content: corpus hash, case and class coverage, reader and
  judge spec, judge pin and rubric hash, seed flags, arms, and the gate arm —
  every field the protocol fixes is a field the bar refuses to run without, so
  the protocol cannot be quietly re-worded after the fact and still pass.

5. **A verified commit binding** (:func:`verify_commit_binding`) — the run's
   ``--commit`` is not free text. At publish time the repository itself is
   asked three questions the issue's targets assert in prose:

   * does that sha RESOLVE (:func:`_resolve_commit`)? A report naming a sha
     nothing can resolve is not "reproducible from the recorded sha alone",
     it is a claim with a hole in it;
   * does the corpus AT THAT SHA hash to the published ``corpus_hash``, and is
     the pre-registration blob at that sha byte-identical to the published
     one? Otherwise `git checkout <sha>` reproduces a different corpus, or a
     different protocol, than the numbers came from;
   * is the pre-registration's own commit a PROPER ANCESTOR of the run commit?
     That is target 1's *"precedes … (verifiable, not asserted)"*: ancestry is
     a property of the commit graph, so it cannot be satisfied by a lane
     writing "written_before_any_arm_run: true" into a file.

   ⛔ **This is a publishing precondition, NOT a protocol amendment.** It adds
   no clause to ``decision_rule``, changes no corpus, no scoring and no
   verdict: it refuses to *emit the report* when the report's own
   reproducibility sentence would be false. The precedent is in-tree —
   ``tools/capstone_preregistration_check.py`` ("the pre-registration's earliest
   add-commit reachable from the BASE ref must be older than the evidence log's
   add-commit … an ordering that cannot be shown is not an ordering"; #10463 moved
   the comparison onto the base branch, because a squash merge rewrites the
   contribution's own sibling commits) and ``tools/finding_provenance.py`` (an
   ancestry test, never an equality test).
   Amending the pre-registration itself to name this check would be a lane
   editing a protocol that already has a run behind it — out of scope here.

Sibling module, not an extension of ``run_protocol.py``: that protocol's steps
are LongMemEval-shaped (500-Q run, vs-V3-baseline delta, 1k benchmark, its own
step numbering). Extending it would couple two benchmarks' pre-registrations.
``measure_temporal.py`` is the sibling precedent.

CLI:

    # 1. pre-register (committed BEFORE the run — the protocol commit)
    uv run python -m tools.longmem_eval.measure_ask_distilled preregister \\
        --out tests/eval/ask/preregistration.json

    # 2. run the sealed corpus (fixtures only; no gold, no grading)
    #    -B is REQUIRED, not decorative: importing this module CACHES it under
    #    the executed surface before any identity is taken, so `run` refuses an
    #    interpreter that writes byte-caches (`_require_byte_code_free`), and
    #    the tree must carry none from an earlier launch either.
    uv run python -B -m tools.longmem_eval.measure_ask_distilled run \\
        --out docs/benchmarks/ask-distilled/run-observations.json

    # 3. score against the sealed key and evaluate the exit bar
    uv run python -m tools.longmem_eval.measure_ask_distilled score \\
        --run <observations> --out <scored>

    # 4. publish the receipt + the run report
    #    --commit is RESOLVED and VERIFIED against the repository (see
    #    `verify_commit_binding`): the corpus and the pre-registration at that
    #    sha must be the ones published, and the pre-registration's commit must
    #    be a proper ancestor of it. A sha that does not vouch for the run is
    #    refused, never published.
    uv run python -m tools.longmem_eval.measure_ask_distilled publish \\
        --scored <scored> --commit <sha> --cost-usd 0.02 \\
        --out docs/benchmarks/ask-distilled/run-report.md \\
        --receipt docs/benchmarks/ask-distilled/receipt.json
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/measure_ask_distilled.py requires Python >= 3.12 "
        f"(got {sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python -m tools.longmem_eval.measure_ask_distilled`"
    )

import argparse
import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.eval.ask import corpus  # noqa: E402
from tools.ask_spotcheck import (  # noqa: E402
    SEED_SEARCH_KEYS_BY_DEFAULT,
    SEED_TURNS_EMBEDDED_BY_DEFAULT,
    _default_sdk_factory,
    _seed_distilled_claim,
    _seed_memory,
    _superseded_live_state,
    _to_iso_date,
)

# The noise-suffix policy is IMPORTED, not re-listed: the guard and this record
# must excuse exactly the same set, and a second copy is a copy that drifts.
#
# ⛔ BOTH halves of the guard's split, not just the constant. The guard's
# ``NOISE_SUFFIXES`` names the suffix-excused set, but ``.pyc``/``.pyo`` are
# members of that tuple only so the ``--allow-bytecode`` opt-out has something
# to name: ``_is_bytecode`` is what the guard uses to REFUSE them by default
# (#3712), over every set (untracked, tracked, present at the rev). Importing
# the tuple alone imported the excuse while the comment promised the policy —
# and a byte-cache is executable code (PEP 552: a hash-based pyc whose
# ``check_source`` flag is unset is loaded WITHOUT the source being hashed, and
# a forged header matches its source by construction), so the excuse made a
# forged byte-cache read as a clean tree in a record whose whole purpose is a
# sound attestation.
from tools.longmem_eval.guard_measured_revision import (  # noqa: E402
    _is_bytecode,
    _is_noise,
)
from tools.longmem_eval.judge import (  # noqa: E402
    DEFAULT_JUDGE_MODEL,
    MockJudge,
    build_judge,
)
from tools.longmem_eval.w7_publish import (  # noqa: E402
    FAILURE_ORIGIN_VALUES,
    RUN_STATUS_VALUES,
    VERDICT_VALUES,
)
from tools.longmem_eval.w7_publish import (  # noqa: E402
    build_receipt as _w7_build_receipt,
)
from tortoise.ask_lane import run_ask_lane  # noqa: E402

# ── protocol constants (the pre-registration fixes these) ─────────────────

PREREG_SCHEMA = "ask-distilled-preregistration/v1"
RUN_SCHEMA = "ask-distilled-observations/v1"
SCORED_SCHEMA = "ask-distilled-scored/v1"
COMMIT_BINDING_SCHEMA = "ask-distilled-commit-binding/v1"
#: What the RUN records about the tree it executed from. The publish-time
#: binding (:func:`verify_commit_binding`) makes the named sha vouch for the
#: corpus and the protocol; this makes the run say WHICH tree produced the
#: numbers, so publish can refuse a run from a different one.
CODE_IDENTITY_SCHEMA = "ask-distilled-code-identity/v1"

#: The surface a measurement run can EXECUTE: the SDK under measurement, the
#: measurement tooling itself, and the corpus loader that tooling imports. It
#: mirrors ``DEFAULT_PATHS`` in :mod:`tools.longmem_eval.guard_measured_revision`
#: — that guard and this record ask different questions about the SAME surface,
#: so a surface named in one and not the other would be a hole shaped like the
#: difference.
#:
#: ⛔ ``tests/eval/ask/`` is IN it, and that entry is not decoration: the runner
#: imports ``tests.eval.ask.corpus`` at module load (``from tests.eval.ask import
#: corpus``, :148), so a patched corpus loader EXECUTES on every run while
#: producing no corpus-hash difference — the hash digests the corpus FILES, not
#: the code that reads them. Measured: with ``tortoise/`` + ``tools/`` only, an
#: ``--assume-unchanged`` patch to ``tests/eval/ask/corpus.py`` left
#: ``dirty_paths == []`` and publish ACCEPTED it. The list is therefore PINNED
#: BY MEASUREMENT, not by hand —
#: ``test_the_recorded_surface_COVERS_every_module_the_run_imports`` imports the
#: runner in a fresh interpreter and fails, naming the module, if anything it
#: loads sits outside these prefixes.
EXECUTED_SURFACE: tuple[str, ...] = ("tortoise/", "tools/", "tests/eval/ask/")

ISSUE = "9097"
EPIC = "2281"

#: The two arms, in pre-registered order. ``raw`` = the capture-shaped turn
#: store (the pre-D7 instrument's shape); ``distilled`` = the same store PLUS
#: the distilled claims and the planted supersession.
ARMS: tuple[str, ...] = ("raw", "distilled")

#: The arm the D8 product gate is evaluated on. The promise — *"0% superseded
#: points appear as live answers"* — is about DISTILLED points: a raw-turn
#: store holds no distilled claim to flag.
GATE_ARM = "distilled"

#: The product's own ask-lane reader default (``build_reader_model``'s final
#: fallback). Pre-registered so the run's reader is fixed before the number.
READER_MODEL_DEFAULT = "deepseek/deepseek-v4-flash"
READER_TEMPERATURE = 0.0
READER_MAX_TOKENS = 500
READER_PINNED = True
#: Recorded when the run was handed a reader factory (tests, wiring runs): such
#: a run did NOT use the pre-registered product reader, and the bar's reader
#: clause must be able to say so.
INJECTED_READER_SPEC = "injected"

#: The judge pin for this benchmark: the OFFICIAL LongMemEval anscheck judge
#: (``tools/longmem_eval/judge.py`` → ``build_judge()``), the same grading
#: discipline the published LongMemEval numbers use. The rubric id is the same
#: one the graded eval carries (``JUDGE_RUBRIC_ID`` in
#: ``tools/longmem_eval/run.py``); the hash is computed with the same 16-hex
#: convention so the two records are comparable.
JUDGE_PIN = "ask-exit-anscheck-official-v1"
#: The pin a MOCK-graded run carries. The pin is derived from the judge spec
#: (never passed in independently), so a receipt cannot claim the official pin
#: while a stub graded the answers — the two travel together.
MOCK_JUDGE_PIN = "ask-exit-anscheck-mock"
JUDGE_RUBRIC_ID = "longmemeval-official"
MOCK_JUDGE_SPEC = "mock"


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def judge_model_id(spec: str) -> str:
    """``provider:model`` → ``model`` (a bare id is its own model id)."""
    return (spec or "").strip().split(":", 1)[-1]


def judge_pin_for(spec: str) -> str:
    """The pin belonging to a judge spec — an ALLOW-LIST, never a default.

    ``JUDGE_PIN`` used to be the answer for every spec except the literal
    ``"mock"``, so a stub recorded as ``"mock-judge"`` carried the OFFICIAL pin
    and a receipt could claim the official judge while a stub graded every
    answer. A spec that is neither the mock nor the pre-registered model now
    gets ``unpinned:<spec>`` — an honest record that travels with the receipt.
    """
    spec = (spec or "").strip()
    if spec == MOCK_JUDGE_SPEC:
        return MOCK_JUDGE_PIN
    model = judge_model_id(spec)
    # The official judge is the MODEL, not the transport: a routed provider that
    # serves the same model id (openrouter:openai/gpt-4o-2024-08-06) grades with
    # the same judge, so it keeps the official pin.
    official = judge_model_id(DEFAULT_JUDGE_MODEL)
    if model == official or model.endswith("/" + official):
        return JUDGE_PIN
    return f"unpinned:{spec}"


def judge_rubric_id_hash(rubric_id: str | None = None) -> str:
    """Hash of the rubric id (the official anscheck template name by default).

    When the judge object declares its OWN ``rubric_id``, that is what gets
    hashed: comparing the module constant to itself on both sides could never
    fail, so the clause only caught a hand-edited pre-registration. A judge that
    declares no rubric still records the official id — that fallback cannot
    differ, and the docstring says so rather than implying a guard it is not.
    """
    return _sha16(rubric_id or JUDGE_RUBRIC_ID)


def resolve_reader_spec(override: str | None = None) -> str:
    """The reader model spec the run will use (env → the product default)."""
    return (override or os.environ.get("TORTOISE_ASK_MODEL", "").strip()
            or READER_MODEL_DEFAULT)


def resolve_judge_spec(override: str | None = None,
                       *, mock: bool = False) -> str:
    """The judge spec the SCORE step will use (mock → env → official default)."""
    if mock:
        return MOCK_JUDGE_SPEC
    return (override or os.environ.get("TORTOISE_LME_JUDGE_MODEL", "").strip()
            or DEFAULT_JUDGE_MODEL)


def protocol(*, reader_spec: str | None = None,
             judge_spec: str | None = None,
             corpus_root: Path | None = None) -> dict:
    """The pre-registered protocol body (no run data, no numbers).

    Everything a re-run must reproduce, and everything a reviewer needs to
    FALSIFY the claim: corpus identity, classes, split, scoring, and the
    decision rule — including the parts that are deliberately not thresholds.
    """
    root = Path(corpus_root) if corpus_root is not None else corpus.ASK_DIR
    case_ids = corpus.case_ids(root)
    # Derived from the corpus ITSELF (never from the module constant): the
    # pre-registration must pin what it verified, or a corpus that lost a class
    # would be pre-registered as still having it.
    classes = sorted({fixture["class"]
                      for fixture in corpus.load_fixtures(root)}) \
        if case_ids else list(corpus.CLASSES)
    return {
        "schema": PREREG_SCHEMA,
        "issue": ISSUE,
        "epic": EPIC,
        "epic_exit": "Q3 exit bar — a published, sealed-benchmark measurement "
                     "of the ask lane over a distilled + planted-supersession "
                     "corpus",
        "written_before_any_arm_run": True,
        "corpus": {
            "path": "tests/eval/ask",
            "fixture_glob": corpus.FIXTURE_GLOB,
            "sealed_gold_glob": corpus.GOLD_GLOB,
            "hash_scope": "fixtures/*.json + gold/*.gold.json (a gold-only "
                          "edit changes the hash and voids this "
                          "pre-registration)",
            "cases": case_ids,
            "classes": classes,
            "authored_by": "hand-authored, hermetic, row-by-row reviewable "
                           "(tests/eval/ask/generate_corpus.py) — the #9095 "
                           "method (run it small and by hand first)",
        },
        "arms": list(ARMS),
        "gate_arm": GATE_ARM,
        "split": "every case runs in BOTH arms inside ONE invocation (the D4 "
                 "paired read: extend W2-b, never a parallel benchmark). No "
                 "train/test split — the corpus is hand-authored and every case "
                 "is scored, so there is nothing to fit.",
        "scoring": {
            "committed": "the official anscheck semantic judge "
                         "(build_judge) grades the reader's answer against the "
                         "SEALED gold answer, per case per arm, using the "
                         "official template the sealed gold names "
                         "(knowledge-update | single-session-user)",
            "superseded_live": "the assembled READER EVIDENCE is searched for "
                               "the superseded claim's own text "
                               "(ask_spotcheck._superseded_live_state): "
                               "seen / rendered_live / flagged, attribution by "
                               "the validity window so a raw turn is never "
                               "mistaken for a distilled claim",
            "verbatim_regression": "for a verbatim-class case: "
                                   "raw.committed AND NOT "
                                   "distilled.committed",
        },
        "decision_rule": {
            "kind": "paired within-run comparison + product gate; NO pre-set "
                    "quality threshold",
            "inconclusive": [
                "the corpus manifest does not verify (missing/extra/"
                "mismatched file, or corpus hash drift)",
                "this pre-registration's corpus_hash does not match the corpus "
                "under test",
                "this file names no cases or no classes (a protocol with no "
                "case or class list cannot certify a run)",
                "the scored run does not cover exactly the pre-registered "
                "cases, or does not cover exactly the pre-registered classes "
                "(a subset run, or a run with a graded class stripped, is not "
                "this measurement)",
                "the run's fixtures_hash does not match the scored corpus's "
                "(the run was over different fixtures than the gold grades)",
                "the run's resolved reader spec, judge spec, judge pin, judge "
                "rubric hash, sampling seed flags, arms, or gate arm differ "
                "from this pre-registration",
                "the metrics block disagrees with the per-case rows it "
                "summarises (a summary is not a result)",
                "the run is a falsification probe (it measures the gate, not "
                "the product)",
                "the scored corpus plants NO superseded claim, has NO verbatim "
                "case, or the raw arm committed no verbatim case (an empty "
                "measurement is not a green one)",
                "any case failed to run in either arm (an unmeasured case is "
                "never a pass)",
            ],
            "regression": [
                "D8: any superseded claim RENDERED LIVE on the distilled arm",
                "D8: any planted claim not OBSERVED (seen < n) on the "
                "distilled arm",
                "verbatim/raw: a verbatim-class case committed on the raw arm "
                "and NOT on the distilled arm",
            ],
            "pass": "the corpus verifies, the config matches this "
                    "pre-registration, every case ran in both arms, and "
                    "neither regression clause fired",
            "reported_not_thresholded": "the knowledge-update current-state "
                                        "rate. D8 is explicit — the "
                                        "benchmark-side X set comes from the "
                                        "first published run, so no pass "
                                        "threshold is pre-set here (and no "
                                        "competing threshold is invented: the "
                                        "product gate lives at "
                                        "docs/product-success-eval.md)",
        },
        "reader": {
            "model_spec": resolve_reader_spec(reader_spec),
            "temperature": READER_TEMPERATURE,
            "max_tokens": READER_MAX_TOKENS,
            "pinned": READER_PINNED,
            "lane": "run_ask_lane (the eval-only ask lane, EVAL-ONLY per "
                    "#3849) — the product's own retrieval + render + reader "
                    "path, not a re-implementation",
        },
        "judge": {
            "model_spec": resolve_judge_spec(judge_spec),
            "pin": judge_pin_for(resolve_judge_spec(judge_spec)),
            "rubric_id": JUDGE_RUBRIC_ID,
            "rubric_id_hash": judge_rubric_id_hash(),
        },
        "seed": {
            "turns_embedded": SEED_TURNS_EMBEDDED_BY_DEFAULT,
            "search_keys": SEED_SEARCH_KEYS_BY_DEFAULT,
            "note": "the #9095 seeder's own defaults — the instrument is not "
                    "re-tuned here",
        },
        # ⛔ This template's ``run`` spelling is deliberately UNCHANGED even
        # though ``run`` now REFUSES to start without ``-B``
        # (:func:`_require_byte_code_free`). The committed
        # ``tests/eval/ask/preregistration.json`` is a hash-bound historical
        # record — the report's binding re-hashes it — so amending it is the
        # owner's call on #9097, not a side effect of this change. The
        # refusal names the working invocation (fail-closed), and §6 of the
        # rendered report — the copy a reader actually reproduces from — is a
        # literal in :func:`render_report` and DOES carry ``-B``.
        "reproduce": {
            "preregister": "uv run python -m "
                           "tools.longmem_eval.measure_ask_distilled "
                           "preregister --out "
                           "tests/eval/ask/preregistration.json",
            "run": "uv run python -m tools.longmem_eval.measure_ask_distilled "
                   "run --out <observations.json>",
            "score": "uv run python -m tools.longmem_eval.measure_ask_distilled"
                     " score --run <observations.json> --out <scored.json>",
            "publish": "uv run python -m "
                       "tools.longmem_eval.measure_ask_distilled publish "
                       "--scored <scored.json> --commit <sha> --cost-usd <usd> "
                       "--out docs/benchmarks/ask-distilled/run-report.md "
                       "--receipt docs/benchmarks/ask-distilled/receipt.json",
        },
    }


def write_preregistration(path: str | Path, *,
                          corpus_root: Path = corpus.ASK_DIR,
                          reader_spec: str | None = None,
                          judge_spec: str | None = None) -> dict:
    """Write the pre-registration record BEFORE any arm runs.

    Refuses to pre-register over a corpus that does not verify: a
    pre-registration whose hash does not describe the corpus under test is the
    stale-flag failure mode (it records a past state and is never
    revalidated), so it is not written at all.
    """
    manifest = corpus.verify_manifest(corpus_root)
    if not manifest["ok"]:
        raise ValueError(
            f"refusing to pre-register: the corpus does not verify ({manifest}) "
            "— a pre-registration must pin the corpus it will be run against")
    record = protocol(reader_spec=reader_spec, judge_spec=judge_spec,
                      corpus_root=corpus_root)
    record["corpus"]["hash"] = manifest["corpus_hash"]
    record["corpus"]["manifest_verified"] = True
    record["corpus"]["n_cases"] = len(record["corpus"]["cases"])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(corpus.dump_json(record), encoding="utf-8")
    return record


def load_preregistration(path: str | Path = corpus.PREREGISTRATION_PATH) -> dict:
    return corpus.read_json(path)


def preregistration_sha256(path: str | Path = corpus.PREREGISTRATION_PATH) -> str:
    return corpus.sha256_file(path)


# ── 0. the COMMIT BINDING — the sha must vouch for the run ────────────────
#
# The issue's targets 1 and 2 are both claims about history:
#   (1) "pre-registration committed BEFORE the run … verifiable, not asserted"
#   (2) "the run is reproducible from the recorded sha alone"
#
# Both are satisfied by prose today: `--commit` is a free-text field, so a
# report could name a sha that does not exist, a sha whose tree carries a
# DIFFERENT corpus or a DIFFERENT protocol, or a sha at which the
# pre-registration was introduced in that very commit (i.e. possibly written
# after the numbers). The report would still render, and its §6 would still
# tell a reader to `git checkout <sha>`. This section makes the repository
# answer those questions instead of the report asserting them.

def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root), *args],
                          capture_output=True, text=True, check=False)


def _git_out(repo_root: Path, *args: str) -> str | None:
    """stdout of a git command, or None when it fails (a failure is not ''.)."""
    proc = _git(repo_root, *args)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _resolve_commit(repo_root: Path, rev: str) -> str | None:
    """Full sha of ``rev`` when it is a commit in this repository, else None."""
    return _git_out(repo_root, "rev-parse", "--verify", "--quiet",
                    f"{(rev or '').strip()}^{{commit}}") or None


def _commit_epoch(repo_root: Path, commit: str) -> int | None:
    """The committer date as epoch seconds — the INSTANT, not the wall clock.

    ``%cI`` renders each commit in the committer's OWN UTC offset
    (``2026-10-24T13:16:58+00:00`` and ``2026-10-24T09:08:02-05:00`` are 8
    minutes apart, not 4 hours), so a ``<``/``>`` on two ``%cI`` strings
    compares wall clocks: it orders instants only when both commits carry the
    same offset. Mixed offsets are the norm in this repository (lane commits
    land ``-05:00``, others ``+00:00``), so the ordering cross-check compares
    ``%ct`` — epoch seconds, one integer per instant — and keeps the ISO string
    beside it only for a human reader. Same datum, same reason, as the recorded
    lesson in ``tools/lane_stash.py`` ("`epoch` (`%ct`) is carried beside the
    ISO date because `%cI` strings from different UTC offsets do not sort
    chronologically as text") and as
    ``tools/capstone_preregistration_check.py`` compares its commit ordering.
    """
    raw = _git_out(repo_root, "show", "-s", "--format=%ct", commit)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    """`git merge-base --is-ancestor` (reflexive: A is an ancestor of A).

    Ancestry, never equality: an equality test passes only when the two shas
    are identical, so it cannot express "the protocol came earlier in this
    history" for the case that actually matters (the protocol commit is behind
    the run commit, not equal to it).
    """
    return _git(repo_root, "merge-base", "--is-ancestor", ancestor,
                descendant).returncode == 0


def _rel_inside(repo_root: Path, path: Path) -> str | None:
    """Repo-relative posix path, or None when ``path`` is outside the repo."""
    try:
        return Path(path).resolve().relative_to(
            Path(repo_root).resolve()).as_posix()
    except ValueError:
        return None


def _corpus_hash_at_commit(repo_root: Path, commit: str,
                           corpus_rel: str) -> str | None:
    """The corpus hash of the corpus AS COMMITTED at ``commit``.

    Materialised through ``git archive`` and hashed with the SAME
    :func:`corpus.compute_corpus_hash` the rest of the pipeline uses — one
    implementation, so "the corpus at that sha" and "the corpus on disk"
    cannot drift apart through a second, subtly different hasher.
    """
    proc = subprocess.run(["git", "-C", str(repo_root), "archive",
                           "--format=tar", commit, "--", corpus_rel],
                          capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout:
        return None
    with tempfile.TemporaryDirectory(prefix="ask_commit_corpus_") as tmp:
        try:
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
                tar.extractall(tmp, filter="data")
        except tarfile.TarError:
            return None
        root = Path(tmp) / corpus_rel
        if not (root / "fixtures").is_dir() or not (root / "gold").is_dir():
            return None
        return corpus.compute_corpus_hash(root)


def _blob_sha256_at_commit(repo_root: Path, commit: str,
                           rel: str) -> str | None:
    """sha256 of the blob at ``commit:rel`` (None when the path is absent)."""
    proc = subprocess.run(["git", "-C", str(repo_root), "show",
                           f"{commit}:{rel}"], capture_output=True, check=False)
    if proc.returncode != 0:
        return None
    return corpus.sha256_bytes(proc.stdout)


def _shallow_boundary(repo_root: Path) -> set[str] | None:
    """The grafted boundary commits of a shallow clone.

    ``git rev-parse --is-shallow-repository`` alone is the wrong question: it is
    true whenever the clone was EVER truncated, even if the boundary sits 500
    commits behind HEAD — which is the normal state of a lane worktree here, and
    in that shape history is perfectly usable. What makes an ordering UNPROVABLE
    is a boundary commit that IS the one being dated, because git reports a
    grafted commit's parents as none and every path then looks added by it.

    The return distinguishes three answers, and the third is why the type is not
    ``set[str]``: an ABSENT shallow file is ``set()`` (a complete clone), the
    boundaries are that set, and a shallow file that exists but cannot be READ
    is ``None``. An unreadable boundary is an UNKNOWN boundary, not an absent
    one — folding ``OSError`` into ``set()`` made a read failure disengage the
    guard, and on a clone whose boundary IS the pre-registration commit every
    ordering check downstream then passes trivially (a false PASS, which is the
    one outcome this branch exists to prevent).
    """
    git_path = _git_out(repo_root, "rev-parse", "--git-path", "shallow")
    if not git_path:
        # Cannot even resolve where the boundary would live: unknown, not none.
        return None
    path = Path(git_path)
    if not path.is_absolute():
        path = repo_root / path
    try:
        return {line.strip() for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()}
    except FileNotFoundError:
        return set()
    except OSError:
        return None


#: git's C-style escapes, as `quote.c` emits them when a path needs quoting.
#: Every KEY here is ONE character — the byte after the backslash — because that
#: is what the decoder looks up. The backslash's own entry is therefore the
#: single character `\\`, not the two-character `\\\\` a C-style rendering would
#: suggest: keying it as two characters silently failed to match, so `"a\\\\b.py"`
#: decoded back to the doubled spelling and the dedupe missed again.
_C_ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
    "t": "\t", "v": "\v", "\\": "\\", '"': '"',
}


def _c_unquote(path: str) -> str:
    """Decode ONE C-quoted path ``git status`` printed, or return it unchanged.

    ``git status --porcelain`` quotes a path carrying non-ASCII or control
    bytes (``core.quotePath`` defaults to true) and spells the bytes as OCTAL
    escapes — ``"tortoise/\303\2512.py"`` for ``tortoise/é2.py``. Stripping
    only the surrounding quotes leaves the escaped spelling, which never equals
    the RAW path ``git ls-files -z`` emits (``-z`` disables quoting), so the
    dedupe missed and ONE file was recorded twice — with the false reason "an
    ignore rule hides it" — the inflation :func:`_status_covered` exists to
    prevent, and the recorded path kept its quotes.

    The octal escapes are BYTES (git writes the path's own bytes), so they are
    accumulated as bytes and decoded as UTF-8 at the end; ``surrogateescape``
    keeps a path that is not valid UTF-8 comparable, which is what git handed
    us rather than a silent replacement character.
    """
    if len(path) < 2 or not (path.startswith('"') and path.endswith('"')):
        return path
    body, out, i = path[1:-1], bytearray(), 0
    while i < len(body):
        char = body[i]
        if char != "\\":
            out.extend(char.encode("utf-8", "surrogateescape"))
            i += 1
            continue
        escaped = body[i + 1] if i + 1 < len(body) else ""
        if escaped in _C_ESCAPES:
            out.extend(_C_ESCAPES[escaped].encode("utf-8"))
            i += 2
            continue
        octal = body[i + 1:i + 4]
        if len(octal) == 3 and all(digit in "01234567" for digit in octal):
            out.append(int(octal, 8))
            i += 4
            continue
        # An escape git does not emit: keep the byte rather than drop it, so a
        # path it never matches stays visible instead of vanishing.
        out.extend(body[i:i + 2].encode("utf-8", "surrogateescape"))
        i += 2
    return out.decode("utf-8", "surrogateescape")


def _status_paths(stdout: str) -> list[str]:
    """The PATH(s) a ``git status --porcelain`` answer names, decoded.

    ONE parse site, because there were two and they disagreed: ``_status_covered``
    split a rename/copy row on ``" -> "`` BEFORE unquoting it, while
    ``code_identity`` unquoted the WHOLE row — so ``R  "tortoise/\303\251.py"
    -> "tortoise/\303\274.py"`` was recorded as ``tortoise/é.py" ->
    "tortoise/ü.py``, a string no ``git`` resolves, and the dirty path echoed in
    the publish refusal named no path in the tree. Both sides of a rename are
    reported: the source is gone from the worktree, the destination is new, and
    each is a path git answers for (``git show <sha>:<src>`` / on disk).

    The rename marker is read from the TWO-CHARACTER status prefix, not inferred
    from the presence of ``" -> "`` in the body: a path may legitimately
    CONTAIN that substring (``?? tools/a -> b.py`` is one file), and splitting
    it would invent a second path that exists nowhere.

    A collapsed untracked directory keeps its trailing ``/``
    (``?? tools/newdir/``), which is what lets :func:`_status_covered` match the
    files ``--untracked-files=normal`` did not list.
    """
    paths: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        code, body = line[:2], line[3:]
        parts = body.split(" -> ") if ("R" in code or "C" in code) else [body]
        for part in parts:
            decoded = _c_unquote(part.strip())
            if decoded:
                paths.append(decoded)
    return paths


def _status_covered(rel: str, status_paths: tuple[str, ...] | list[str]) -> bool:
    """Whether ``git status`` ALREADY reported ``rel`` (so re-reporting it
    would inflate the count and misname the reason).

    ``git ls-files --others`` (no ``--exclude-standard``) returns EVERY
    untracked file under the surface, ignored or not — so for an ordinary
    untracked module the surface scan and the status scan both see it, and
    reporting both made one file read as "2 uncommitted path(s)" whose reason
    pointed the operator at an ignore rule that does not exist.

    ``status_paths`` are the paths ``git status`` NAMED, already decoded by
    :func:`_status_paths` — every side of a rename/copy row as its own entry.
    This function therefore only MATCHES; it does not parse. Two shapes have to
    match, or the dedupe would still double-report:

    * the plain row (``?? tools/x.py``);
    * a COLLAPSED untracked directory — ``--untracked-files=normal`` prints
      ``?? tools/newdir/`` and not its contents, so a file inside it is
      covered by that row.
    """
    for raw in status_paths:
        path = raw.strip()
        if not path:
            continue
        if path == rel or (path.endswith("/") and rel.startswith(path)):
            return True
    return False


def _paths_hidden_from_status(
        repo_root: Path,
        surface: tuple[str, ...] = EXECUTED_SURFACE,
        *,
        status_paths: tuple[str, ...] | list[str] = ()) -> list[str] | None:
    """Paths a ``git status`` answer cannot see, enumerated from git's records.

    ``git status`` is an oracle that takes advice, and both of its advisers can
    be told to say "clean":

    * ``git update-index --assume-unchanged`` / ``--skip-worktree`` record that
      git must not look at a file's working-tree content, so a MODIFIED,
      executed module prints nothing while the file on disk is the patched one
      (reproduced: status ``[]``, ``ls-files -v`` shows the ``h``/``S`` bit).
    * an ignore rule (``.gitignore``, ``.git/info/exclude``, a global
      ``core.excludesFile``) hides an untracked file — an importable module
      that never even has to be committed to run.

    Both are recorded defeats of a diff-and-status check in this package
    (``guard_measured_revision``:22-32, ``:481``), which is why the question is
    put to git here with no filter in it: the untracked set WITHOUT
    ``--exclude-standard``, and the index TAG of every tracked file (``H``
    ordinary; a lowercase letter is assume-unchanged, ``S`` skip-worktree,
    anything else is not ordinary staging).

    ``surface`` bounds the walk because this repo — unlike the throwaway repos
    the tests build — carries ignored trees (``.venv/``) that are not execution
    surface, so an unbounded no-ignore enumeration would refuse every real run.

    ⛔ BYTE-CACHES ARE REFUSED, never excused — the guard's own default
    (#3712, ``_is_bytecode``) over all the sets it checks: untracked here,
    tracked here (a committed/staged ``.pyc`` is just as loadable), and by the
    guard at the revision. ``_is_noise`` still carries ``.pyc``/``.pyo`` as
    members, so the byte-cache test runs FIRST — otherwise the noise excuse
    would swallow exactly the case the guard refuses. The refusal is one
    aggregated row rather than one per file: a checkout easily carries a
    hundred byte-caches, and a hundred rows would bury the reason.

    ``status_paths`` are the paths ``git status`` already reported, used ONLY
    to dedupe: a path status saw needs no second row — of ANY kind, byte-cache
    included, or one untracked ``.pyc`` in a repository that does not ignore
    caches would be "2 uncommitted path(s)". The ignore-rule wording is
    reserved for a path status did NOT list.

    Returns ``None`` when a git call FAILS: a failed ``ls-files`` has empty
    stdout, and empty stdout must never read as "nothing hidden".
    """
    tagged = _git(repo_root, "ls-files", "-z", "-v", "--", *surface)
    if tagged.returncode != 0:
        return None
    # NO --exclude-standard: an ignore rule must not hide a file from the check.
    others = _git(repo_root, "ls-files", "-z", "--others", "--", *surface)
    if others.returncode != 0:
        return None
    hidden: list[str] = []
    byte_caches: list[str] = []
    for entry in tagged.stdout.split("\0"):
        if not entry:
            continue
        tag, _, rel = entry.partition(" ")
        if _is_bytecode(rel):
            # Tracked (committed/staged) byte-cache: git calls it clean, and
            # the index tag says "ordinary", so neither oracle below nor the
            # status answer can see that CPython may load it over the source.
            # ... unless it is MODIFIED, in which case status did list it and
            # one file must still be one row.
            if not (status_paths and _status_covered(rel, status_paths)):
                byte_caches.append(rel)
        elif tag != "H":
            hidden.append(
                f"{rel} (index flag {tag!r} tells git not to look at this "
                f"file's content, so the dirty state is suppressed)")
    for rel in others.stdout.split("\0"):
        if not rel:
            continue
        if _is_bytecode(rel):
            # Same dedupe as the ordinary branch below: `ls-files --others`
            # without `--exclude-standard` also returns caches `git status`
            # already printed, and a second row inflates the count.
            if not (status_paths and _status_covered(rel, status_paths)):
                byte_caches.append(rel)
            continue
        # Excused by SUFFIX only (editor/VCS droppings) — the same suffix-only
        # excusal ``guard_measured_revision`` applies, taken from its own
        # predicate so the two cannot drift. Byte-caches are NOT in this set
        # however ``NOISE_SUFFIXES`` lists them: they were taken above.
        if _is_noise(rel):
            continue
        # Already reported by the status oracle: one file, one row.
        if status_paths and _status_covered(rel, status_paths):
            continue
        hidden.append(
            f"{rel} (untracked under the executed surface and NOT listed by "
            f"`git status` — an ignore rule hides it)")
    if byte_caches:
        listed = sorted(byte_caches)
        examples = ", ".join(listed[:3]) + (" …" if len(listed) > 3 else "")
        hidden.append(
            f"{len(listed)} byte-cache file(s) under the executed surface "
            f"that `git status` does not list (e.g. {examples}) — a "
            f"``.pyc``/``.pyo`` is executable code that "
            f"this record does not verify, and a forged byte-cache's header "
            f"matches its source by construction (PEP 552), so the recorded "
            f"sha cannot vouch for what CPython loads. Re-run over a "
            f"byte-code-free tree (remove them first: `{_BYTECODE_REMOVAL}`)")
    return hidden


#: The removal command every byte-cache refusal names. ONE spelling, so the
#: refusal raised at run start and the row ``code_identity`` records cannot tell
#: an operator two different things.
_BYTECODE_REMOVAL = ("find tortoise tools tests/eval/ask -name __pycache__ "
                     "-type d -prune -exec rm -rf {} +")

#: The invocation that satisfies the byte-code-free requirement, named by the
#: run-start refusal. ``uv run python -B`` is equivalent and named beside it.
_BYTECODE_FREE_RUN = ("PYTHONDONTWRITEBYTECODE=1 uv run python -m "
                      "tools.longmem_eval.measure_ask_distilled run --out "
                      "<observations.json>")


def _is_byte_cache_row(dirty: str) -> bool:
    """Whether one ``dirty_paths`` row names a byte-cache (or their aggregate).

    Three shapes reach here: a raw path ``git status`` printed (``?? x.pyc``,
    or a collapsed ``?? tools/__pycache__/``), a ``_paths_hidden_from_status``
    row (``<path> (reason)\u2026``), and that function's single aggregate row
    (``\u201c12 byte-cache file(s) \u2026\u201d``). All three mean the same thing to the
    run guard: this tree cannot be published.
    """
    if "byte-cache file(s)" in dirty:
        return True
    path = dirty.split(" (", 1)[0]
    return _is_bytecode(path) or "__pycache__" in path


def _refuse_byte_caches(why: str, examples: list[str]) -> SystemExit:
    """The ONE run-start refusal, so both halves cannot drift apart."""
    shown = ", ".join(examples[:3]) + (" \u2026" if len(examples) > 3 else "")
    return SystemExit(
        f"refusing to start the run: {why}\n"
        "This module is NOT launched by `tools/longmem_eval/run_protocol.py` "
        "(that launches `tools.longmem_eval.run` and `full_context` with "
        "`-B` and `PYTHONDONTWRITEBYTECODE=1`); the byte-code-free "
        "requirement is this command's own.\n"
        f"  {len(examples)} byte-cache path(s) the record cannot vouch for: "
        f"{shown}\n"
        "Re-run byte-code-free over a tree with no byte-caches left in it:\n"
        f"  {_BYTECODE_REMOVAL}\n"
        f"  {_BYTECODE_FREE_RUN}\n"
        "(equivalently `uv run python -B -m "
        "tools.longmem_eval.measure_ask_distilled run --out "
        "<observations.json>`)")


def _require_byte_code_free(repo_root: str | Path) -> None:
    """Refuse to START a measured run that could never be published.

    Two independent halves, and BOTH are needed — either one alone lets the run
    measure for minutes and then be refused at publish:

    1. **The interpreter.** ⛔ ``tools/longmem_eval/run_protocol.py`` does NOT
       launch this module. It launches ``tools.longmem_eval.run``
       (:func:`run_protocol._run_cmd`) and ``tools.longmem_eval.full_context``
       (:func:`run_protocol._cell_cmd`) with ``-B`` and
       ``PYTHONDONTWRITEBYTECODE=1``, and this module's own header says it is a
       sibling of that protocol, not an extension of it. If the interpreter
       writes byte-caches it caches this module **and its imports** before this
       function can run — before the first identity is taken — so
       :func:`code_identity` (correctly, #3712) records them. Worse, the
       refusal's own remedy — delete the caches — would be undone by the next
       identical launch, so an operator following it loops with no exit
       signposted.
    2. **The tree.** ``-B``/``PYTHONDONTWRITEBYTECODE=1`` stop caches being
       WRITTEN and remove none, so a byte-code-free launch over a tree that
       already carries caches (any earlier launch or test run leaves them) is
       still unpublishable. Checking the flag alone passed a tree it claimed to
       protect.

    Both checks are read-only and cost three git calls.
    """
    if not sys.dont_write_bytecode:
        raise _refuse_byte_caches(
            "this interpreter WRITES byte-caches "
            "(`sys.dont_write_bytecode` is False), so importing the runner has "
            "already put `.pyc` files under the executed surface — before the "
            "first code identity was taken — and `code_identity` refuses a "
            "tree carrying them (#3712: a byte-cache is executable code this "
            "record does not verify)",
            ["<the interpreter has already written them>"])
    # `python -B` and `PYTHONDONTWRITEBYTECODE=1` both set the flag above; on
    # its own that stops caches being written, so the tree is asked separately.
    identity = code_identity(repo_root)
    caches = [row for row in identity["dirty_paths"] or []
              if _is_byte_cache_row(row)]
    if caches:
        raise _refuse_byte_caches(
            f"the tree at {repo_root} ALREADY carries byte-caches in its "
            "executed surface. `-B`/`PYTHONDONTWRITEBYTECODE=1` stop caches "
            "being WRITTEN and remove none, so this run would measure for "
            "minutes and then be refused at publish (#3712: a byte-cache is "
            "executable code the record does not verify)",
            caches)


def code_identity(repo_root: str | Path | None,
                  *, surface: tuple[str, ...] = EXECUTED_SURFACE) -> dict:
    """The tree the RUN was made from, as the repository reports it.

    The publish-time binding makes the named sha vouch for the corpus and the
    protocol. That is only half of "reproducible from the recorded sha alone":
    nothing tied the NUMBERS to that sha, so a publisher could run the corpus
    at tree A, then name a commit B whose corpus and protocol are byte-identical
    (any merge of ``main`` between the run and the publish produces exactly
    that) and still emit a report telling a reader to ``git checkout B`` — where
    a re-run would execute different code. The corpus the numbers came from is
    not the only input to those numbers.

    So the run records the commit it ran at **and** whether the tree was clean.
    A dirty tree is the same defect in another form: the sha then reproduces
    *sha plus an uncommitted patch*, which is not the recorded sha alone. Both
    are recorded here and refused at publish (:func:`_validate_code_identity`),
    never guessed.

    "Clean" is answered by TWO oracles, because the first one can be silenced:
    the pinned ``git status`` (every tracked modification and every untracked
    file), plus git's own records over the EXECUTED SURFACE — untracked files
    enumerated WITHOUT ``--exclude-standard`` and the index tag of every
    tracked file — which is the only place an ``assume-unchanged`` /
    ``skip-worktree`` modification and an ignored, importable file are visible
    (:func:`_paths_hidden_from_status`).

    Byte-caches are REFUSED, not excused, which is the same default the sibling
    guard enforces (#3712) over the same surface: a ``.pyc``/``.pyo`` is
    executable code, this record does not verify its contents, and a forged
    one's header matches its source by construction — so a tree carrying one is
    not a tree the recorded sha reproduces, even though ``git status`` calls it
    clean. Because a byte-cache is what an ordinary interpreter launch
    CREATES, the refusal is only reachable if the run is launched
    byte-code-free — and that requirement is **this command's own**, not an
    inherited property: ``run_protocol`` does not launch this module
    (:func:`_require_byte_code_free`), so the CLI ``run`` REFUSES to start
    rather than measuring for minutes and then refusing at publish, and it
    names the working invocation.

    The remaining suffix exemptions are the editor/VCS droppings
    (``.orig``/``.rej``/``.swp``/``.swo``/``.DS_Store``) — taken from
    ``guard_measured_revision._is_noise`` so the two cannot drift.
    """
    if repo_root is None:
        return {
            "schema": CODE_IDENTITY_SCHEMA,
            "commit": None,
            "dirty_paths": [],
            "repo": None,
            "reason": "no repository was supplied to the run — a run that does "
                      "not record the tree it executed from cannot be "
                      "reproduced from a sha, so publish refuses it",
        }
    repo_root = Path(repo_root)
    commit = _resolve_commit(repo_root, "HEAD")
    # ⛔ ``--untracked-files=normal`` is PINNED, not inherited: the question is
    # "was the tree clean?", and without the flag the answer comes from the
    # reader's git config. ``status.showUntrackedFiles=no`` is a common setting
    # on a repository this size, and under it an untracked, importable file is
    # invisible — so ``dirty_paths`` is ``[]`` and the report publishes "clean at
    # run time" over a tree that includes code nobody recorded.
    status = _git(repo_root, "status", "--porcelain",
                  "--untracked-files=normal")
    # A status that FAILS is not a clean tree: a non-zero exit (not a
    # repository, unreadable index) leaves stdout empty, which would otherwise
    # read as "nothing modified" — a fail-open on the one question this asks.
    if status.returncode != 0:
        dirty_paths = ["<git status failed: the tree is not provably clean>"]
    else:
        dirty_paths = _status_paths(status.stdout)
    # ⛔ The status answer is NOT the whole of "was the tree clean?": an index
    # flag or an ignore rule makes a modified/untracked executed file print
    # nothing at all. Both are asked of git's records directly, and a git
    # failure here is recorded as not-provably-clean for the same reason a
    # failed status is — empty stdout is not a clean tree.
    hidden = _paths_hidden_from_status(repo_root, surface,
                                       status_paths=dirty_paths)
    if hidden is None:
        dirty_paths.append(
            "<git ls-files failed: the tree is not provably clean>")
    else:
        dirty_paths.extend(hidden)
    return {
        "schema": CODE_IDENTITY_SCHEMA,
        "commit": commit,
        "dirty_paths": dirty_paths,
        "repo": str(repo_root),
        "reason": None if commit else f"{repo_root} is not a git repository",
    }


def verify_commit_binding(*, commit: str, repo_root: str | Path,
                          corpus_root: str | Path = corpus.ASK_DIR,
                          prereg: str | Path = corpus.PREREGISTRATION_PATH,
                          ) -> tuple[dict | None, list[str]]:
    """Bind the published artifacts to the commit they claim to come from.

    Returns ``(binding, reasons)``: ``binding`` is None when ANY check fails,
    and ``reasons`` then names every failure (not just the first) — a
    publishable claim has to be reproducible in every part, and fixing them one
    round trip at a time is how a lane talks itself into publishing the first
    two.

    The binding records, so a reviewer can re-verify without re-deriving them:
    the resolved run commit, the pre-registration's own commit and both commit
    times (ISO for a human reader, epoch seconds for the ordering comparison),
    the corpus hash AT the run commit, and the pre-registration blob's sha256
    AT the run commit.
    """
    repo_root = Path(repo_root)
    corpus_root = Path(corpus_root)
    prereg = Path(prereg)
    reasons: list[str] = []

    run_commit = _resolve_commit(repo_root, commit)
    if run_commit is None:
        return None, [
            f"the recorded commit {commit!r} is not a commit in {repo_root} — "
            f"'reproducible from the recorded sha alone' is exactly what the "
            f"sha has to support, and this one resolves to nothing"]

    corpus_rel = _rel_inside(repo_root, corpus_root)
    prereg_rel = _rel_inside(repo_root, prereg)
    if corpus_rel is None:
        reasons.append(
            f"the corpus {corpus_root} is outside the repository {repo_root}, "
            f"so the recorded commit cannot vouch for it")
    if prereg_rel is None:
        reasons.append(
            f"the pre-registration {prereg} is outside the repository "
            f"{repo_root}, so the recorded commit cannot vouch for it")
    if reasons:
        return None, reasons

    # (a) the corpus AT the recorded sha is the corpus that was scored.
    committed_corpus_hash = _corpus_hash_at_commit(
        repo_root, run_commit, corpus_rel)
    local_corpus_hash = corpus.compute_corpus_hash(corpus_root)
    if committed_corpus_hash is None:
        reasons.append(
            f"{corpus_rel} does not exist at {run_commit} — `git checkout "
            f"{run_commit[:12]}` cannot reproduce the corpus these numbers "
            f"came from")
    elif committed_corpus_hash != local_corpus_hash:
        reasons.append(
            f"the corpus at {run_commit[:12]} hashes to "
            f"{committed_corpus_hash}, but the scored corpus hashes to "
            f"{local_corpus_hash} — the recorded sha reproduces a DIFFERENT "
            f"corpus than the one these numbers came from")

    # (b) the pre-registration AT the recorded sha is the protocol published.
    committed_prereg_sha = _blob_sha256_at_commit(
        repo_root, run_commit, prereg_rel)
    local_prereg_sha = corpus.sha256_file(prereg)
    if committed_prereg_sha is None:
        reasons.append(
            f"{prereg_rel} does not exist at {run_commit} — the protocol that "
            f"fixed the bar was not committed at the sha the run records")
    elif committed_prereg_sha != local_prereg_sha:
        reasons.append(
            f"the pre-registration at {run_commit[:12]} hashes to "
            f"{committed_prereg_sha}, but the published one hashes to "
            f"{local_prereg_sha} — the report would cite a sha carrying a "
            f"different protocol than the one published")

    # (c) the pre-registration PRECEDES the run in the commit graph.
    prereg_commit = None
    prereg_time = run_time = None
    prereg_epoch = run_epoch = None
    if committed_prereg_sha is not None:
        prereg_commit = _git_out(repo_root, "rev-list", "-1", run_commit,
                                 "--", prereg_rel)
        boundary = _shallow_boundary(repo_root)
        if not prereg_commit:
            reasons.append(
                f"cannot determine which commit introduced {prereg_rel} at or "
                f"before {run_commit[:12]} — an ordering that cannot be "
                f"established is not an ordering")
        elif boundary is None:
            reasons.append(
                f"ordering UNPROVABLE: cannot READ this clone's shallow "
                f"boundary, so whether {prereg_rel} is grafted is unknown — an "
                f"unreadable boundary is not an "
                f"absent one, and assuming it is absent is the false pass this "
                f"check exists to prevent. Run `git fetch --unshallow` (or fix "
                f"the shallow file's permissions) and re-publish")
        elif prereg_commit in boundary:
            reasons.append(
                f"ordering UNPROVABLE: {prereg_commit[:12]} (the commit the "
                f"pre-registration dates to) is this clone's SHALLOW boundary "
                f"— git grafts its parents away, so 'the protocol came first' "
                f"cannot be shown here. UNPROVABLE is never a pass: run "
                f"`git fetch --unshallow` and re-publish")
        elif prereg_commit == run_commit:
            reasons.append(
                f"ordering NOT proven: the pre-registration enters history in "
                f"{run_commit[:12]}, the same commit recorded as the run — the "
                f"protocol could have been written after the numbers were "
                f"seen. Record the run at a COMMIT THAT CONTAINS the "
                f"pre-registration commit as an ancestor")
        elif not _is_ancestor(repo_root, prereg_commit, run_commit):
            reasons.append(
                f"ordering NOT proven: {prereg_commit[:12]} (the "
                f"pre-registration's commit) is not an ancestor of "
                f"{run_commit[:12]} (the run's commit)")
        else:
            prereg_time = _git_out(repo_root, "show", "-s", "--format=%cI",
                                   prereg_commit)
            run_time = _git_out(repo_root, "show", "-s", "--format=%cI",
                                run_commit)
            prereg_epoch = _commit_epoch(repo_root, prereg_commit)
            run_epoch = _commit_epoch(repo_root, run_commit)
            # Ancestry is the proof (a property of the graph); the clock is a
            # cross-check, and it is compared on the EPOCH (one integer per
            # instant), never on the `%cI` text: those strings carry their own
            # offsets, so text order is wall-clock order and inverts across an
            # offset change (see `_commit_epoch`). `prereg_epoch <= run_epoch`
            # non-strict: a scripted lane can land two commits inside one
            # second, and refusing that would be a stuck light rather than a
            # finding.
            if prereg_epoch is None or run_epoch is None:
                reasons.append(
                    f"ordering UNPROVABLE: cannot read the commit times of "
                    f"{prereg_commit[:12]} / {run_commit[:12]} (epochs "
                    f"{prereg_epoch!r} / {run_epoch!r}) — an ordering that "
                    f"cannot be shown is not an ordering, and a clock check "
                    f"that silently skips itself is not a cross-check")
            elif prereg_epoch > run_epoch:
                reasons.append(
                    f"ordering NOT proven: the pre-registration's commit time "
                    f"({prereg_time}, epoch {prereg_epoch}) is LATER than the "
                    f"run commit's ({run_time}, epoch {run_epoch}) — history "
                    f"was rewritten (or the clock is wrong), so precedence is "
                    f"not shown")

    if reasons:
        return None, reasons

    return {
        "schema": COMMIT_BINDING_SCHEMA,
        "run_commit": run_commit,
        "run_commit_requested": str(commit),
        "corpus_path": corpus_rel,
        "corpus_hash": committed_corpus_hash,
        "prereg_path": prereg_rel,
        "prereg_sha256": committed_prereg_sha,
        "prereg_commit": prereg_commit,
        "prereg_commit_time": prereg_time,
        "run_commit_time": run_time,
        # Carried beside the ISO dates because `%cI` text does not sort
        # chronologically across UTC offsets: these two ARE the comparison, and
        # they let a receipt-only auditor re-check the polarity without git.
        "prereg_commit_epoch": prereg_epoch,
        "run_commit_epoch": run_epoch,
        "ordering": "proven",
        "ordering_proof": (
            f"{prereg_commit} (the commit that last touched {prereg_rel} at or "
            f"before the run commit) is a proper ancestor of {run_commit}; "
            f"ancestry verified with `git merge-base --is-ancestor`"),
    }, []


# ── 1. the RUN step — fixtures only, observations only ────────────────────

def _seed_distilled(sdk, fixture: dict) -> dict[str, str]:
    """Seed ONE case's distilled corpus ADDITIVELY (turn store + claims + supersession).

    The claims go through the product's own distilled-claim write
    (``create_point(is_episodic=False, validFrom=…, session_id=…)`` + the
    ``CONTAINS`` edge) and the supersession through the product's own
    ``supersede_point`` — this module never teaches a write shape the product
    cannot produce. Additive by contract: the turn store is already seeded, so
    the raw and distilled arms differ ONLY by this block.
    """
    ids: dict[str, str] = {}
    for claim in fixture["distilled"]["claims"]:
        ids[claim["text"]] = _seed_distilled_claim(sdk, claim)
    for sup in fixture["distilled"]["supersessions"]:
        sdk.supersede_point(ids[sup["old"]], ids[sup["new"]])
    return ids


def _run_arm(*, fixture: dict, arm: str, sdk_factory, reader_factory,
             mutate=None) -> dict:
    """One case through ONE arm. Returns OBSERVATIONS only (no grading)."""
    sdk = sdk_factory()
    try:
        _seed_memory(sdk, fixture)
        if arm == GATE_ARM:
            _seed_distilled(sdk, fixture)
            if mutate is not None:
                mutate(sdk)
        try:
            result = run_ask_lane(
                sdk, fixture["question"],
                question_type=fixture.get("question_type"),
                question_date=_to_iso_date(fixture.get("question_date") or ""),
                _reader_factory=reader_factory,
            )
        except Exception as exc:  # noqa: BLE001, RUF100 — a per-case reader or
            # retrieval fault is an UNMEASURED case, recorded as such. It is
            # never a miss (which would silently count as a regression) and
            # never a pass: the bar turns an unmeasured case into
            # ``inconclusive``.
            return {"ok": False, "answer": "", "evidence": "",
                    "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "answer": result.get("answer") or "",
                "evidence": result.get("evidence") or ""}
    finally:
        sdk.close()


def run_sealed(*, corpus_root: Path = corpus.ASK_DIR,
               arms: tuple[str, ...] = ARMS,
               reader_factory=None, sdk_factory=None, mutate=None,
               mutate_label: str | None = None,
               repo_root: str | Path | None = None) -> dict:
    """Run the sealed corpus: both arms per case, fixtures ONLY.

    ⛔ The inputs are ``corpus.load_fixtures`` — ``fixtures/*.json``. The gold
    is not loaded here, not passed here and not reachable from here; the
    grading step (:func:`score`) is where the sealed key is opened. That is
    what "the sealed gold is never read by the runner" means mechanically.

    ``mutate`` is the falsification hook (applied to the distilled arm after
    seeding): drop the supersession and the D8 gate must go RED, which is what
    proves the gate can say no.

    The tree is recorded **before** the first case runs, and re-read after the
    last one: a run whose ``HEAD`` or cleanliness changed while it was running
    straddled two trees, and no single sha reproduces it (see
    :func:`code_identity`).
    """
    fixtures = corpus.load_fixtures(corpus_root)
    if not fixtures:
        raise ValueError(f"sealed corpus at {corpus_root} has no fixtures")
    # ⛔ BEFORE the case loop, not after it. The measurement takes minutes and a
    # real reader call takes seconds-to-minutes; a checkout that moves while the
    # run is in flight would otherwise be recorded as the tree the NUMBERS came
    # from, which is the false PASS this record exists to prevent. The library
    # accepts a repository because the tests exercise real git repos in tmp
    # directories; the CLI always passes this module's own tree.
    identity = code_identity(repo_root)
    sdk_factory = sdk_factory or _default_sdk_factory
    records: list[dict] = []
    for fixture in fixtures:
        case = {"case_id": fixture["case_id"], "class": fixture["class"],
                "arms": {}}
        for arm in arms:
            case["arms"][arm] = _run_arm(
                fixture=fixture, arm=arm, sdk_factory=sdk_factory,
                reader_factory=reader_factory, mutate=mutate)
        records.append(case)
    # The same questions again, AFTER the run: a commit that landed mid-run (or a
    # path dirtied by the run itself) means these numbers came from no single
    # tree, and that is a refusal rather than a footnote — recorded here because
    # only the run knows when it started.
    identity["at_end"] = code_identity(repo_root)
    # No separate "drifted" flag is recorded: :func:`_validate_code_identity`
    # and :func:`render_report` both decide from the two records, so a third,
    # inert field would be a second verdict nothing reads — the shape this
    # repo has been burned by before.
    report = {
        "schema": RUN_SCHEMA,
        # FIXTURES ONLY — deliberately NOT the full corpus hash, which digests
        # the sealed gold and would be a (weak) oracle over the answer key.
        # The SCORE step computes the full corpus hash, because it is the step
        # allowed to open the key.
        "fixtures_hash": corpus.compute_fixtures_hash(corpus_root),
        # The INJECTED reader when a factory was supplied: recording the env
        # default made every stub-reader wiring run publish as the pinned
        # product reader, and the bar's reader clause passed against it.
        "reader_model_spec": (INJECTED_READER_SPEC if reader_factory is not None
                              else resolve_reader_spec()),
        "reader_injected": reader_factory is not None,
        "arms": list(arms),
        "seed": {"turns_embedded": SEED_TURNS_EMBEDDED_BY_DEFAULT,
                 "search_keys": SEED_SEARCH_KEYS_BY_DEFAULT},
        # WHICH TREE produced these observations, recorded by the step that ran
        # before it ran, rather than supplied later by whoever writes the
        # report: a publisher that could NAME the commit would be back to
        # asserting it.
        "code_identity": identity,
        "cases": records,
    }
    if mutate is not None:
        # Recorded by the RUN step, not by whichever caller remembered to: a
        # probe whose flag depends on the caller is a probe that gets scored as
        # a measurement the first time someone forgets.
        report["mutated"] = (mutate_label
                             or f"falsification probe: {getattr(mutate, '__name__', 'mutate hook')}")
    return report


# ── 2. the SCORE step — the sealed key is opened HERE ─────────────────────

def _judge_committed(gold: dict, fixture: dict, answer: str, judge) -> bool:
    """Grade one answer with the official anscheck template the gold names.

    The template lives in the SEALED gold (grading configuration): the
    knowledge-update class is graded with the knowledge-update template, a plain
    verbatim look-up with ``single-session-user``. The FIXTURE's
    ``question_type`` is a different thing — it selects the reader prompt and
    must be a type the ask lane accepts.
    """
    return bool(judge.judge(
        question_type=gold.get("judge_template") or "knowledge-update",
        question=fixture.get("question") or "",
        answer=gold.get("answer") or "",
        hypothesis=answer or "",
        abstention=False,
    ))


def score(*, run_report: dict, corpus_root: Path = corpus.ASK_DIR,
          judge=None, judge_spec: str | None = None,
          mock: bool = False) -> dict:
    """Grade the observations against the SEALED gold and evaluate the bar.

    Reads ``gold/`` (this is the scorer, not the runner). Emits one scored row
    per case plus the corpus-level aggregates, and the exit-bar verdict.
    """
    fixtures = {f["case_id"]: f for f in corpus.load_fixtures(corpus_root)}
    golds = corpus.load_golds(corpus_root)
    if judge is None:
        judge = MockJudge() if mock else build_judge(judge_spec)
        spec = resolve_judge_spec(judge_spec, mock=mock)
    elif isinstance(judge, MockJudge):
        # UNCONDITIONAL: a mock is a mock. Checking a caller-declared spec first
        # let `score(judge=MockJudge(), judge_spec="mock-judge")` record the
        # stub's own id and collect the OFFICIAL pin with it.
        spec = MOCK_JUDGE_SPEC
    else:
        # The recorded identity must describe the OBJECT that graded the run,
        # not a spec string a caller hopes it used: `score(judge=SomeStub())`
        # with the official spec in scope would otherwise satisfy the
        # pre-registration's judge check while the stub graded every answer. The
        # declared spec is honoured only on an EXACT model-id match (substring
        # containment let `model_id="gpt-4o"` claim the official spec).
        object_id = getattr(judge, "model_id", "") or type(judge).__name__
        declared = judge_spec or resolve_judge_spec()
        if object_id and object_id == judge_model_id(declared):
            spec = declared
        else:
            spec = f"injected:{object_id}"

    # Refuse a report the bar has a clause for, LOUDLY, instead of dying with a
    # bare KeyError a caller has to decode: the clause cannot fire if the scorer
    # crashes first, and a traceback is not a diagnosis.
    arms_in_report = list(run_report.get("arms") or ())
    missing_arms = [arm for arm in ("raw", GATE_ARM) if arm not in arms_in_report]
    if missing_arms:
        raise ValueError(
            f"the run report has arm(s) {arms_in_report}, so arm(s) "
            f"{missing_arms} were never measured — score it only if BOTH arms "
            f"ran (the exit bar's paired read needs them)")
    report_ids = [case.get("case_id") for case in run_report.get("cases") or ()]
    unknown = [cid for cid in report_ids
               if cid not in fixtures or cid not in golds]
    if unknown:
        raise ValueError(
            f"the run report names case(s) {unknown} that this sealed corpus "
            f"does not carry (fixtures: {sorted(fixtures)}, gold: "
            f"{sorted(golds)}) — a run over other cases cannot be graded by "
            f"this gold")

    rows: list[dict] = []
    for record in run_report["cases"]:
        cid = record["case_id"]
        fixture = fixtures[cid]
        gold = golds[cid]
        arms = {}
        for arm in run_report["arms"]:
            obs = record["arms"][arm]
            arms[arm] = {
                "ok": obs["ok"],
                "answer": obs["answer"],
                "committed": (bool(obs["ok"]) and _judge_committed(
                    gold, fixture, obs["answer"], judge)),
            }
        superseded = []
        gate_obs = record["arms"][GATE_ARM]
        # EVERY planted supersession is gated. Gating only the first pair while
        # counting CASES in the denominator would leave a second planted dead
        # claim ungated and still publish a green D8 row.
        for planted in fixture["distilled"]["supersessions"]:
            seen, rendered_live, flagged = _superseded_live_state(
                gate_obs.get("evidence") or "", planted["old"])
            superseded.append({"claim": planted["old"],
                               "seen": bool(seen),
                               "rendered_live": bool(rendered_live),
                               "flagged": bool(flagged)})
        verbatim_regression = bool(
            fixture["class"] == corpus.CLASS_VERBATIM
            and arms["raw"]["committed"]
            and not arms[GATE_ARM]["committed"])
        rows.append({
            "case_id": cid,
            "class": fixture["class"],
            "arms": arms,
            "superseded": superseded,
            "verbatim_regression": verbatim_regression,
        })

    scored = {
        "schema": SCORED_SCHEMA,
        # Computed HERE (the scorer may open the sealed key), never taken from
        # the run report — the run does not carry a digest of the gold.
        "corpus_hash": corpus.compute_corpus_hash(corpus_root),
        "fixtures_hash": corpus.compute_fixtures_hash(corpus_root),
        # The RUN's own fixture digest: the linkage the bar checks, so a run
        # over different fixtures can never be scored against this gold.
        "run_fixtures_hash": run_report.get("fixtures_hash"),
        # Recorded so a later audit/bar evaluation re-verifies the manifest of
        # the corpus this run actually used — never the committed one by
        # default (that default is how a tmp-corpus run reads as drifted).
        "corpus_root": str(corpus_root),
        "reader_model_spec": run_report["reader_model_spec"],
        "judge_model_spec": spec,
        "judge_pin": judge_pin_for(spec),
        "judge_model_id": getattr(judge, "model_id", ""),
        "judge_rubric_id_hash": judge_rubric_id_hash(
            getattr(judge, "rubric_id", None)),
        "arms": run_report["arms"],
        "seed": run_report.get("seed") or {},
        "cases": rows,
        "metrics": aggregate(rows),
        "bar": OUTCOME_PENDING,  # filled by evaluate_exit_bar
    }
    if run_report.get("mutated"):
        scored["mutated"] = run_report["mutated"]
    # Carried through the score step so PUBLISH can compare it with the commit
    # it is about to name. ``copy.deepcopy``: the scored artifact must not alias
    # the run's record, or an audit that edits one silently rewrites the other.
    if "code_identity" in run_report:
        scored["code_identity"] = copy.deepcopy(run_report["code_identity"])
    return scored


def _rate_block(flags) -> dict:
    flags = list(flags)
    n = len(flags)
    committed = sum(1 for f in flags if f)
    return {"committed": committed, "n": n,
            "rate": (committed / n) if n else 0.0}


def aggregate(rows: list[dict]) -> dict:
    """The metric block, derived from the per-case ROWS (one implementation).

    The bar re-runs this over ``scored["cases"]`` and refuses a metrics block
    that disagrees with the rows it summarises — a summary is not a result, and
    a forged/edited summary must not be able to carry a verdict on its own.
    """
    ku_rows = [r for r in rows if r["class"] == corpus.CLASS_KNOWLEDGE_UPDATE]
    verbatim_rows = [r for r in rows if r["class"] == corpus.CLASS_VERBATIM]
    claims = [claim for r in ku_rows for claim in r.get("superseded") or []]
    return {
        f"current_state@{GATE_ARM}": _rate_block(
            r["arms"][GATE_ARM]["committed"] for r in ku_rows),
        "current_state@raw": _rate_block(
            r["arms"]["raw"]["committed"] for r in ku_rows),
        # The denominator is planted CLAIMS, not cases: a case may plant more
        # than one old→new pair and each one must be observed and unflagged.
        f"superseded_live@{GATE_ARM}": {
            "seen": sum(1 for claim in claims if claim["seen"]),
            "rendered_live": sum(1 for claim in claims
                                 if claim["rendered_live"]),
            "n": len(claims),
            "n_cases": len(ku_rows),
        },
        "verbatim_no_regression": {
            "raw_committed": sum(1 for r in verbatim_rows
                                 if r["arms"]["raw"]["committed"]),
            "distilled_committed": sum(1 for r in verbatim_rows
                                       if r["arms"][GATE_ARM]["committed"]),
            "n": len(verbatim_rows),
            "regressions": [r["case_id"] for r in verbatim_rows
                            if r["verbatim_regression"]],
        },
    }


#: Marker for "the bar has not been evaluated yet" — never a verdict value.
OUTCOME_PENDING = "pending-evaluation"


# ── 3. the EXIT BAR ───────────────────────────────────────────────────────

def evaluate_exit_bar(*, scored: dict, manifest: dict | None = None,
                      prereg: dict | None = None) -> dict:
    """Decide PASS / REGRESSION / INCONCLUSIVE from the scored run.

    The order is deliberate and matches ``protocol()["decision_rule"]``:
    **integrity first** (a verdict about a corpus that does not verify, or a
    configuration the pre-registration does not describe, is not a measurement),
    then **coverage and denominator floors** (an unmeasured case, class, claim or
    verbatim case is never a pass), then the two regression clauses.
    """
    if manifest is None:
        manifest = corpus.verify_manifest(
            Path(scored["corpus_root"]) if scored.get("corpus_root")
            else corpus.ASK_DIR)
    reasons: list[str] = []

    def _inconclusive(origin: str, reason: str) -> dict:
        reasons.append(reason)
        return {"verdict": "inconclusive", "failure_origin": origin,
                "run_status": "failed" if origin == "runner_error"
                              else "skipped",
                "reasons": reasons}

    # (1) corpus + pre-registration integrity
    if not manifest.get("ok"):
        detail = {k: manifest.get(k) for k in
                  ("missing", "extra", "mismatched", "malformed")
                  if manifest.get(k)}
        return _inconclusive(
            "hash_mismatch",
            f"corpus manifest does not verify ({detail}) — the corpus under "
            f"test is not the corpus the hash describes")
    if scored.get("corpus_hash") != manifest.get("corpus_hash"):
        return _inconclusive(
            "hash_mismatch",
            "the scored run's corpus_hash differs from the corpus on disk — "
            "the run was not over this corpus")
    if prereg is None:
        return _inconclusive(
            "config_mismatch",
            "no pre-registration: without a protocol committed BEFORE the run "
            "the bar was not fixed in advance, so no claim can be published")
    # A falsification PROBE measures the gate, not the product: it plants a
    # known defect to prove the gate can see it. Publishing its verdict would
    # report a deliberately broken run as the measurement.
    if scored.get("mutated"):
        return _inconclusive(
            "config_mismatch",
            f"this run is a falsification probe ({scored['mutated']}) — the "
            f"gate readings it produces are evidence ABOUT the gate, never a "
            f"measurement that can satisfy the exit bar")
    if prereg.get("corpus", {}).get("hash") != scored.get("corpus_hash"):
        return _inconclusive(
            "hash_mismatch",
            "the pre-registration's corpus_hash does not match this corpus — "
            "the protocol describes a corpus that no longer exists (re-keyed "
            "gold, or an edited fixture)")
    # (1b) the scored run must COVER the pre-registered corpus. Without this,
    # an empty (or class-stripped) result set is a NO-OP GATE: every downstream
    # clause divides by a denominator it never measures — `seen == n` is 0 == 0,
    # and a corpus with no verbatim case trivially has "no verbatim regression".
    # The corpus hash already catches an edited corpus, but a caller can score a
    # tabulated subset, so the coverage is checked explicitly.
    if not scored.get("cases"):
        return _inconclusive(
            "config_mismatch",
            "the scored run carries NO cases — an empty result set cannot "
            "satisfy a bar whose clauses are all per-case")
    covered = {row["case_id"] for row in scored["cases"]}
    expected_cases = set(prereg["corpus"].get("cases") or ())
    # No short-circuit on an empty expectation: a pre-registration that names no
    # cases describes no measurement, so it is a mismatch, not a free pass.
    if not expected_cases:
        return _inconclusive(
            "config_mismatch",
            "the pre-registration names no cases — a protocol with no case list "
            "cannot certify any run")
    if covered != expected_cases:
        return _inconclusive(
            "config_mismatch",
            f"the scored run covers {sorted(covered)}, but the "
            f"pre-registration names {sorted(expected_cases)} — a subset run "
            f"is not the pre-registered measurement")
    classes = {row["class"] for row in scored["cases"]}
    expected_classes = set(prereg["corpus"].get("classes") or ())
    if not expected_classes:
        return _inconclusive(
            "config_mismatch",
            "the pre-registration names no classes — a protocol with no class "
            "list cannot certify that both halves of the bar were measured")
    if classes != expected_classes:
        return _inconclusive(
            "config_mismatch",
            f"the scored run covers class(es) {sorted(classes)}, but the "
            f"pre-registration names {sorted(expected_classes)} — a missing "
            f"class silently empties the half of the bar that grades it")

    # (1c) the run's fixture digest must be THIS corpus's fixtures. The corpus
    # hash binds the corpus on disk to the pre-registration; this binds the RUN
    # to the corpus it was scored against (a run over other fixtures cannot be
    # graded by this gold just because both trees were re-hashed together).
    if scored.get("run_fixtures_hash") != scored.get("fixtures_hash"):
        return _inconclusive(
            "hash_mismatch",
            "the run's fixtures_hash does not match the scored corpus's — the "
            "run was over different fixtures than the gold grades")

    # (1d) EVERY field the pre-registration fixes must actually be enforced.
    # A pre-registered field that is written and never read is documentation,
    # not a pre-registration: the protocol's authority is exactly the set of
    # fields the bar refuses to run without.
    mismatches = []
    if prereg["reader"]["model_spec"] != scored.get("reader_model_spec"):
        mismatches.append(f"reader {prereg['reader']['model_spec']!r} != "
                          f"{scored.get('reader_model_spec')!r}")
    if prereg["judge"]["model_spec"] != scored.get("judge_model_spec"):
        mismatches.append(f"judge {prereg['judge']['model_spec']!r} != "
                          f"{scored.get('judge_model_spec')!r}")
    if prereg["judge"].get("pin") != scored.get("judge_pin"):
        mismatches.append(f"judge pin {prereg['judge'].get('pin')!r} != "
                          f"{scored.get('judge_pin')!r}")
    if prereg["judge"].get("rubric_id_hash") != scored.get(
            "judge_rubric_id_hash"):
        mismatches.append("judge rubric_id_hash differs from the "
                          "pre-registration (the anscheck template moved)")
    # The seeder flags are the enforced part; the pre-registration also carries
    # a prose `note`, which describes the choice rather than configuring it.
    for flag in ("turns_embedded", "search_keys"):
        if prereg["seed"].get(flag) != (scored.get("seed") or {}).get(flag):
            mismatches.append(
                f"seed {flag} {prereg['seed'].get(flag)!r} != "
                f"{(scored.get('seed') or {}).get(flag)!r}")
    if prereg["arms"] != scored.get("arms"):
        mismatches.append(f"arms {prereg['arms']} != {scored.get('arms')}")
    if prereg.get("gate_arm") != GATE_ARM:
        mismatches.append(
            f"pre-registered gate_arm {prereg.get('gate_arm')!r} is not the arm "
            f"this bar grades ({GATE_ARM!r})")
    if mismatches:
        return _inconclusive(
            "config_mismatch",
            "the run's resolved configuration differs from the "
            "pre-registration: " + "; ".join(mismatches))

    # (2) completeness — an unmeasured case is never a pass
    unmeasured = [row["case_id"] for row in scored["cases"]
                  if not all(row["arms"][arm]["ok"]
                             for arm in scored["arms"])]
    if unmeasured:
        return _inconclusive(
            "runner_error",
            f"case(s) {sorted(unmeasured)} did not run in both arms — an "
            f"unmeasured case is never a pass")

    # (2b) THE METRICS BLOCK IS NOT THE RESULT. Recompute it from the per-case
    # rows and refuse a summary that disagrees with the rows it summarises: a
    # metrics dict is editable from outside the audit trail (a forged artifacts
    # file, a "fix up the numbers" patch), and a bar that reads only the summary
    # cannot tell a real 0-of-3 from an edited 0-of-0.
    recomputed = aggregate(scored["cases"])
    if recomputed != scored.get("metrics"):
        return _inconclusive(
            "config_mismatch",
            "the metrics block disagrees with the per-case rows it summarises "
            "— the summary was edited (or produced by a different scorer), so "
            "neither reading can be published")

    # (2c) DENOMINATOR FLOORS. Both regression clauses are satisfied by an empty
    # measurement — `seen != n` is 0 != 0 and a raw arm that committed nothing
    # has nothing to lose — so a corpus with no planted claim, a corpus with no
    # verbatim case, or a reader that committed nothing anywhere would publish a
    # green bar over a measurement it never made.
    live = scored["metrics"][f"superseded_live@{GATE_ARM}"]
    verbatim_metrics = scored["metrics"]["verbatim_no_regression"]
    if not live["n"]:
        return _inconclusive(
            "config_mismatch",
            "the scored corpus plants NO superseded claim, so the D8 gate "
            "observed nothing — an empty gate is not a green gate")
    if not verbatim_metrics["n"]:
        return _inconclusive(
            "config_mismatch",
            "the scored corpus has NO verbatim case, so the no-regression half "
            "of the exit bar measured nothing")
    if not verbatim_metrics["raw_committed"]:
        return _inconclusive(
            "config_mismatch",
            "the raw arm committed NO verbatim case, so there is nothing for "
            "the distilled arm to regress FROM — 'no regression' would be "
            "vacuous")

    # (3) the two regression clauses
    regressions = verbatim_metrics["regressions"]
    if live["rendered_live"]:
        reasons.append(
            f"D8: {live['rendered_live']}/{live['n']} superseded claims were "
            f"RENDERED LIVE to the reader")
    if live["seen"] != live["n"]:
        reasons.append(
            f"D8: {live['seen']}/{live['n']} planted superseded claims were "
            f"OBSERVED — an unobserved claim was never shown to be flagged")
    if regressions:
        reasons.append(
            f"verbatim/raw: distilled arm lost case(s) {sorted(regressions)} "
            f"that the raw arm committed — a KU win with a verbatim loss is a "
            f"FAIL, not a trade")
    if reasons:
        return {"verdict": "regression", "failure_origin": "gate_regression",
                "run_status": "completed", "reasons": reasons}
    return {"verdict": "pass", "failure_origin": None, "run_status": "completed",
            "reasons": ["the corpus verifies, the config matches the "
                        "pre-registration, every case ran in both arms, and "
                        "neither the D8 gate nor the verbatim/raw comparison "
                        "regressed"]}


# ── 4. publication: receipt + report ──────────────────────────────────────

#: The ask-lane metric keys a completed receipt must carry. These are the ask
#: vocabulary — deliberately NOT ``official_recall_all@k`` /
#: ``official_recall_all@5_by_category`` (the LongMemEval gate surface, which
#: ``w7_publish.validate_receipt`` demands and an ask run cannot have).
REQUIRED_METRICS: tuple[str, ...] = (
    f"current_state@{GATE_ARM}",
    "current_state@raw",
    f"superseded_live@{GATE_ARM}",
    "verbatim_no_regression",
)


def build_receipt(*, scored: dict, commit: str, cost_usd: float,
                  run_id: str | None = None,
                  date: str | None = None,
                  prereg_sha256: str | None = None,
                  commit_binding: dict | None = None,
                  notes: list[str] | None = None) -> dict:
    """The validated receipt (W7 vocabulary, ask metric set).

    ``commit_binding`` is the record :func:`verify_commit_binding` produced at
    publish time. It is not derivable here (this function is pure), which is
    why :func:`validate_ask_receipt` treats a receipt without one as invalid:
    the published claim's reproducibility has to come from the repository, and
    a receipt that merely *states* a sha is the defect this replaces.

    The run's own ``code_identity`` (:func:`code_identity`) travels with the
    scored artifact and is recorded here too: the binding proves the sha carries
    this corpus and this protocol, the identity proves the numbers were produced
    by that tree.
    """
    bar = scored["bar"]
    note_list = [
        "ask lane (run_ask_lane, EVAL-ONLY per #3849) — the product's own "
        "retrieval + render + reader path",
        "sealed gold: the RUN step reads fixtures/*.json only; the sealed key "
        "was opened by the SCORE step",
        "no knowledge-update threshold is claimed: D8 says the first published "
        "run sets the benchmark-side X set (the KU rate is reported, not gated)",
        "the pre-registered bars that need no number: the D8 superseded-live "
        "gate and the paired verbatim/raw comparison",
    ]
    if prereg_sha256:
        note_list.append(f"pre-registration sha256: {prereg_sha256}")
    note_list.extend(notes or [])
    receipt = _w7_build_receipt(
        run_id=run_id or f"ask-distilled-{scored['corpus_hash'][7:19]}",
        date=date or datetime.now(UTC).strftime("%Y-%m-%d"),
        commit=commit,
        corpus_hash=scored["corpus_hash"],
        judge_pin=scored.get("judge_pin") or judge_pin_for(
            scored["judge_model_spec"]),
        judge_model=scored.get("judge_model_id") or scored["judge_model_spec"],
        judge_rubric_id_hash=scored.get("judge_rubric_id_hash")
        or judge_rubric_id_hash(),
        reader_model_spec=scored.get("reader_model_spec") or "",
        reader_pinned=READER_PINNED,
        ingest_mode="capture-shaped turn store + distilled claims "
                    "(create_point is_episodic=False, validFrom) + "
                    "supersede_point",
        run_status=bar["run_status"],
        verdict=bar["verdict"],
        failure_origin=bar["failure_origin"],
        cost_usd=float(cost_usd),
        # Deep-copied: a receipt must not alias the scored artifact, or an
        # audit that edits one silently rewrites the other (and a
        # "does this receipt overstate its evidence?" check would mutate the
        # very evidence it is checking).
        metrics=copy.deepcopy(scored["metrics"]),
        notes=note_list,
        resolved_config={
            "arms": copy.deepcopy(scored["arms"]),
            "gate_arm": GATE_ARM,
            "judge_model_spec": scored["judge_model_spec"],
            "reader_model_spec": scored.get("reader_model_spec"),
            "reader_temperature": READER_TEMPERATURE,
            "reader_max_tokens": READER_MAX_TOKENS,
            "seed": {"turns_embedded": SEED_TURNS_EMBEDDED_BY_DEFAULT,
                     "search_keys": SEED_SEARCH_KEYS_BY_DEFAULT},
            "preregistration_sha256": prereg_sha256,
        },
    )
    # Recorded verbatim (deep-copied, like the metrics): the binding is evidence
    # produced by the repository, and a receipt must not alias it either.
    if commit_binding is not None:
        receipt["commit_binding"] = copy.deepcopy(commit_binding)
    if scored.get("code_identity") is not None:
        receipt["code_identity"] = copy.deepcopy(scored["code_identity"])
    return receipt


def _validate_code_identity(receipt: dict) -> list[str]:
    """The receipt must carry the tree the RUN recorded, and that tree must be
    the receipt's own commit, clean.

    ``commit_binding`` (publish time) answers *"does this sha carry the corpus
    and the protocol these numbers came from?"*. This answers the question it
    leaves open: *"were these numbers produced by that tree?"* Without it a    publisher can run the corpus at tree A and name a commit B whose corpus and
    protocol are byte-identical — any ``main`` merge between the run and the
    publish produces exactly that shape — and the report's §6 would tell a
    reader to `git checkout B`, where a re-run executes different code.
    """
    issues: list[str] = []
    identity = receipt.get("code_identity")
    if not isinstance(identity, dict) or not identity:
        return ["receipt.code_identity: missing — a published run must record "
                "the tree it executed from (the commit, and whether that tree "
                "was clean). Without it the recorded sha says where the CORPUS "
                "came from, never where the numbers did"]
    if identity.get("schema") != CODE_IDENTITY_SCHEMA:
        issues.append(
            f"receipt.code_identity.schema: unexpected "
            f"{identity.get('schema')!r} (expected {CODE_IDENTITY_SCHEMA!r})")
    run_tree = str(identity.get("commit") or "")
    if len(run_tree) != 40 or not all(c in "0123456789abcdef"
                                      for c in run_tree):
        issues.append(
            f"receipt.code_identity.commit: {run_tree!r} is not a full 40-hex "
            f"sha — the run did not record the tree it executed from, so no "
            f"sha can be said to reproduce these numbers")
    elif run_tree != str(receipt.get("commit") or ""):
        issues.append(
            f"receipt.code_identity.commit: {run_tree!r} is not the receipt's "
            f"own commit ({receipt.get('commit')!r}) — the run executed a "
            f"DIFFERENT tree than the one this report names, so `git checkout "
            f"<sha>` would re-run code these numbers did not come from")
    dirty = identity.get("dirty_paths")
    if not isinstance(dirty, list):
        issues.append(
            f"receipt.code_identity.dirty_paths: expected a list, got "
            f"{dirty!r}")
    elif dirty:
        issues.append(
            f"receipt.code_identity.dirty_paths: the run executed from a tree "
            f"with {len(dirty)} uncommitted path(s) {dirty[:5]}" +
            (" …" if len(dirty) > 5 else "") +
            " — 'reproducible from the recorded sha alone' is false over an "
            "uncommitted patch: commit the tree and re-run")
    # The run records the tree at BOTH ends. A commit that landed mid-run (or a
    # path the run itself dirtied) means these numbers came from no single tree,
    # so no sha reproduces them — and the run is the only witness that it
    # started before the change. A missing ``at_end`` is not "unchanged": a run
    # from an older record cannot show it stayed put, so it is refused too.
    at_end = identity.get("at_end")
    if not isinstance(at_end, dict) or not at_end:
        issues.append(
            "receipt.code_identity.at_end: missing — the run did not record the "
            "tree AFTER it finished, so nothing shows the measurement stayed on "
            "one tree; a sha cannot reproduce a run that may have straddled two")
    elif not isinstance(at_end.get("dirty_paths"), list):
        issues.append(
            f"receipt.code_identity.at_end.dirty_paths: expected a list, got "
            f"{at_end.get('dirty_paths')!r} — a record that cannot say whether "
            f"the END tree was clean is not evidence it was")
    elif (at_end.get("commit") != run_tree
            or at_end["dirty_paths"] != (dirty if isinstance(dirty, list)
                                         else [])):
        issues.append(
            f"receipt.code_identity.at_end: the tree CHANGED while the run was "
            f"in progress ({run_tree!r}/{dirty!r} at start → "
            f"{at_end.get('commit')!r}/{at_end.get('dirty_paths')!r} at end) — "
            f"these numbers came from no single tree, so `git checkout <sha>` "
            f"reproduces neither half: commit first, then re-run")
    return issues


def _validate_commit_binding(receipt: dict) -> list[str]:
    """The receipt must carry the binding the PUBLISHER verified, and agree
    with it.

    This half cannot re-run git (a receipt is read without a checkout), so it
    checks the properties that make a hand-edited or hand-assembled receipt
    visible: the binding is present, its run commit IS the receipt's commit and
    is a full sha, its corpus hash and pre-registration sha ARE the receipt's,
    the ordering is recorded as proven, and the pre-registration's commit is
    not the run commit. A receipt that states a sha without a binding is the
    exact shape of the old defect ("trust the free-text commit").
    """
    issues: list[str] = []
    binding = receipt.get("commit_binding")
    if not isinstance(binding, dict) or not binding:
        return ["receipt.commit_binding: missing — a published run must carry "
                "the commit binding verified at publish time (the resolved "
                "run sha, the corpus + pre-registration AT that sha, and the "
                "ordering proof). A receipt that only NAMES a commit asserts "
                "the reproducibility it is supposed to demonstrate"]
    # A `proven` ordering is only re-checkable if the receipt carries what a
    # re-verifier needs: both repo-relative paths, the sha that was asked for,
    # and the proof sentence §6 renders. Required CONDITIONALLY on `proven`, so
    # a binding cannot be trimmed down to the fields that happen to pass.
    required = ["schema", "run_commit", "prereg_commit", "corpus_hash",
                "prereg_sha256", "ordering"]
    if binding.get("ordering") == "proven":
        required += ["run_commit_requested", "corpus_path", "prereg_path",
                     "ordering_proof"]
    for key in required:
        value = binding.get(key)
        if key == "ordering":
            if value != "proven":
                issues.append(
                    f"receipt.commit_binding.ordering: {value!r} — only a "
                    f"PROVEN ordering may be published (the report claims the "
                    f"protocol was committed before the run)")
        elif not isinstance(value, str) or not value.strip():
            issues.append(
                f"receipt.commit_binding.{key}: expected a non-empty string, "
                f"got {value!r}")
    run_commit = str(binding.get("run_commit") or "")
    if run_commit and (len(run_commit) != 40
                       or not all(c in "0123456789abcdef" for c in run_commit)):
        issues.append(
            f"receipt.commit_binding.run_commit: {run_commit!r} is not a full "
            f"40-hex sha — a placeholder is not a resolved commit")
    if run_commit and run_commit != str(receipt.get("commit") or ""):
        issues.append(
            f"receipt.commit_binding.run_commit: {run_commit!r} is not the "
            f"receipt's own commit ({receipt.get('commit')!r}) — the binding "
            f"must describe the sha the receipt publishes")
    # The same 40-hex rule as the run commit, for the same reason: the report's
    # §6 hands THIS value to a re-verifier as
    # `git merge-base --is-ancestor <prereg_commit> <run_commit>`, so a
    # placeholder here makes the emitted verification unrunnable while the
    # receipt still reads `ordering: proven`.
    prereg_commit = str(binding.get("prereg_commit") or "")
    if prereg_commit and (len(prereg_commit) != 40
                          or not all(c in "0123456789abcdef"
                                     for c in prereg_commit)):
        issues.append(
            f"receipt.commit_binding.prereg_commit: {prereg_commit!r} is not a "
            f"full 40-hex sha — the report publishes it as the sha a reviewer "
            f"re-runs `git merge-base --is-ancestor` against, and a placeholder "
            f"there cannot resolve")
    if binding.get("corpus_hash") != receipt.get("corpus_hash"):
        issues.append(
            f"receipt.commit_binding.corpus_hash: {binding.get('corpus_hash')!r}"
            f" is not the receipt's corpus hash "
            f"({receipt.get('corpus_hash')!r}) — the sha does not vouch for "
            f"the corpus these numbers came from")
    resolved = receipt.get("resolved_config") or {}
    prereg_sha = resolved.get("preregistration_sha256")
    # The cross-check is UNCONDITIONAL once a binding is present. Guarding it on
    # `prereg_sha` truthiness made DELETING the datum the way past it: the
    # binding's pre-registration hash then travelled with nothing to corroborate
    # it, and the receipt validated clean. A forger who removes the field he is
    # compared against must fail, not skip.
    if not isinstance(prereg_sha, str) or not prereg_sha.strip():
        issues.append(
            f"receipt.resolved_config.preregistration_sha256: missing — the "
            f"commit binding's pre-registration hash "
            f"({binding.get('prereg_sha256')!r}) cannot be corroborated "
            f"against the receipt without it, so its absence invalidates the "
            f"receipt rather than skipping the check")
    elif binding.get("prereg_sha256") != prereg_sha:
        issues.append(
            f"receipt.commit_binding.prereg_sha256: "
            f"{binding.get('prereg_sha256')!r} is not the published "
            f"pre-registration's sha256 ({prereg_sha!r})")
    if binding.get("prereg_commit") and (
            binding.get("prereg_commit") == run_commit):
        issues.append(
            "receipt.commit_binding.prereg_commit: equals the run commit — the "
            "protocol and the run share a commit, so precedence is not shown")
    # The ordering is a claim about INSTANTS, so the receipt has to carry the
    # comparable datum and it has to point the right way. Checked here (no git
    # needed) because the ISO strings beside them cannot decide it: they carry
    # their own UTC offsets, so their text order is wall-clock order.
    for key in ("prereg_commit_epoch", "run_commit_epoch"):
        value = binding.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            issues.append(
                f"receipt.commit_binding.{key}: expected epoch seconds, got "
                f"{value!r} — the ordering cross-check compares instants, and "
                f"the `%cI` strings beside them do not order chronologically "
                f"across UTC offsets")
    prereg_epoch = binding.get("prereg_commit_epoch")
    run_epoch = binding.get("run_commit_epoch")
    if (isinstance(prereg_epoch, int) and not isinstance(prereg_epoch, bool)
            and isinstance(run_epoch, int) and not isinstance(run_epoch, bool)
            and prereg_epoch > run_epoch):
        issues.append(
            f"receipt.commit_binding: the pre-registration's commit epoch "
            f"({prereg_epoch}) is LATER than the run commit's ({run_epoch}) — "
            f"the protocol could have been written after the numbers were seen")
    return issues


def validate_ask_receipt(receipt: dict) -> list[str]:
    """Validate an ASK receipt (the S13 check an auditor re-runs).

    Shared W7 vocabulary (``run_status`` / ``verdict`` / ``failure_origin``),
    ask metric set. It ALSO re-derives the verdict from the metrics: a receipt
    that says ``pass`` while its own numbers show a live superseded claim, an
    unobserved claim, or a verbatim regression is rejected — the receipt is not
    allowed to disagree with the evidence it carries.
    """
    issues: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt: not an object"]
    for key in ("run_id", "date", "commit", "corpus_hash", "judge_pin"):
        value = receipt.get(key)
        if key == "judge_pin":
            if receipt.get("run_status") == "completed" and (
                    not isinstance(value, str) or not value.strip()):
                issues.append("receipt.judge_pin: completed run requires a "
                              "pinned judge")
        elif not isinstance(value, str) or not value.strip():
            issues.append(
                f"receipt.{key}: expected a non-empty string, got {value!r}")
    if receipt.get("run_status") not in RUN_STATUS_VALUES:
        issues.append(
            f"receipt.run_status: unexpected {receipt.get('run_status')!r}")
    if receipt.get("verdict") not in VERDICT_VALUES:
        issues.append(
            f"receipt.verdict: unexpected {receipt.get('verdict')!r}")
    if receipt.get("failure_origin") not in FAILURE_ORIGIN_VALUES:
        issues.append(
            f"receipt.failure_origin: unexpected {receipt.get('failure_origin')!r}")
    if not isinstance(receipt.get("resolved_config"), dict):
        issues.append("receipt.resolved_config: expected an object")
    issues.extend(_validate_commit_binding(receipt))
    issues.extend(_validate_code_identity(receipt))
    if isinstance(receipt.get("cost_usd"), bool) or not isinstance(
            receipt.get("cost_usd"), (int, float)):
        issues.append("receipt.cost_usd: expected a number")
    elif receipt["cost_usd"] < 0:
        issues.append(
            f"receipt.cost_usd: expected >= 0, got {receipt['cost_usd']!r}")
    metrics = receipt.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return [*issues, "receipt.metrics: expected a non-empty object"]
    missing = [key for key in REQUIRED_METRICS if key not in metrics]
    if missing:
        issues.append(
            f"receipt.metrics: missing the ask-lane metric key(s) {missing} — "
            f"an ask receipt carries {list(REQUIRED_METRICS)} (NOT the "
            f"LongMemEval official_recall_all@k surface)")
        return issues
    if receipt.get("run_status") != "completed":
        return issues
    for key in ("judge_model", "judge_rubric_id_hash"):
        if not isinstance(receipt.get(key), str) or not (
                receipt.get(key) or "").strip():
            issues.append(f"receipt.{key}: completed run requires the resolved "
                          f"judge identity")
    if not str(receipt.get("corpus_hash") or "").startswith("sha256:"):
        issues.append("receipt.corpus_hash: expected a sha256:… corpus hash")
    # ── the judge identity must be internally consistent ──
    # A pin/spec pair that cannot both be true is how a stub-graded wiring check
    # is published under the official judge pin. The pin is DERIVED from the
    # spec in this module; a receipt that pairs them differently was not built
    # by this module (or was edited afterwards).
    resolved = receipt.get("resolved_config") or {}
    spec = str(resolved.get("judge_model_spec") or "")
    model = str(receipt.get("judge_model") or "")
    pin = str(receipt.get("judge_pin") or "")
    if spec and pin and pin != judge_pin_for(spec):
        issues.append(
            f"receipt.judge_pin: {pin!r} does not belong to judge spec "
            f"{spec!r} (expected {judge_pin_for(spec)!r})")
    if spec and model and model not in spec and spec not in model:
        issues.append(
            f"receipt.judge_model: {model!r} is inconsistent with the resolved "
            f"judge spec {spec!r}")
    if receipt.get("verdict") == "pass" and pin == MOCK_JUDGE_PIN:
        issues.append(
            "receipt.verdict: a MOCK-graded run cannot publish 'pass' — a mock "
            "judge proves the wiring, never the measurement")
    # ── the receipt must not disagree with its own evidence ──
    live = metrics[f"superseded_live@{GATE_ARM}"]
    verbatim = metrics["verbatim_no_regression"]
    # `n` is the denominator the claim depends on: grosser numbers are only
    # meaningful over a measured population, so a zero denominator is a
    # malformed receipt rather than a clean one.
    for name, block in ((f"superseded_live@{GATE_ARM}", live),
                        ("verbatim_no_regression", verbatim)):
        n = block.get("n")
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
            issues.append(f"receipt.metrics.{name}.n: expected a count, got {n!r}")
    live_unknown = any(live.get(k) is None for k in ("seen", "rendered_live"))
    gate_red = live_unknown or bool(live.get("rendered_live")) or (
        live.get("seen") != live.get("n"))
    verbatim_red = bool(verbatim.get("regressions")) or (
        isinstance(verbatim.get("raw_committed"), int)
        and isinstance(verbatim.get("distilled_committed"), int)
        and verbatim["raw_committed"] > verbatim["distilled_committed"])
    if receipt.get("verdict") == "pass" and (gate_red or verbatim_red):
        issues.append(
            "receipt.verdict: 'pass' contradicts the receipt's own metrics "
            f"(superseded_live={live}, verbatim={verbatim}) — a receipt may not "
            "overstate its evidence")
    if receipt.get("verdict") == "regression" and not (gate_red or verbatim_red):
        issues.append(
            "receipt.verdict: 'regression' with no regression in the metrics "
            f"(superseded_live={live}, verbatim={verbatim})")
    return issues


def render_report(*, receipt: dict, scored: dict, prereg: dict,
                  prereg_sha256: str) -> str:
    """Render ``docs/benchmarks/ask-distilled/run-report.md`` from the artifacts.

    Generated, never hand-written: every number below comes from the scored
    run, which came from the sealed observations. The report states the
    pre-registered bar and says, in the same breath, which numbers are NOT
    thresholds.
    """
    m = receipt["metrics"]
    ku = m[f"current_state@{GATE_ARM}"]
    ku_raw = m["current_state@raw"]
    live = m[f"superseded_live@{GATE_ARM}"]
    verbatim = m["verbatim_no_regression"]
    def _claim_cell(claims: list[dict]) -> str:
        # Every planted pair is reported, not just the first: the denominator is
        # claims, so a second ungated pair would be invisible in this table.
        if not claims:
            return "—"
        return ", ".join(
            ("seen" if c["seen"] else "unseen")
            + " / "
            + ("LIVE" if c["rendered_live"]
               else ("flagged" if c["flagged"]
                     else ("live-unflagged" if c["seen"] else "—")))
            for c in claims)

    rows = "\n".join(
        f"| `{row['case_id']}` | {row['class']} | "
        f"{'✅' if row['arms']['raw']['committed'] else '❌'} | "
        f"{'✅' if row['arms'][GATE_ARM]['committed'] else '❌'} | "
        f"{_claim_cell(row['superseded'])} |"
        for row in scored["cases"])
    notes = "\n".join(f"- {note}" for note in receipt["notes"])
    # The binding is rendered from the receipt, never re-derived here: what a
    # reader audits is what the publisher verified.
    binding = receipt.get("commit_binding") or {}
    run_commit = binding.get("run_commit") or receipt["commit"]
    identity = receipt.get("code_identity") or {}
    run_tree = identity.get("commit") or "—"
    dirty_paths = identity.get("dirty_paths")
    # ⛔ "clean" is claimed only on a RECORDED clean tree. `(identity or {}).get(
    # 'dirty_paths') or []` made an absent record render identically to a
    # verified clean one — a fail-open default in the function the README calls
    # the audit surface, which is the one place it must not be.
    if not isinstance(dirty_paths, list) or not identity.get("commit"):
        # ⛔ BOTH halves are required to say "clean": a record whose
        # `dirty_paths` is an empty list but whose COMMIT is null (the shape
        # `code_identity(None)` produces) rendered as "clean at run time"
        # beside a commit column of "—", which asserts a tree that was never
        # named. Publish refuses such a receipt first, but this is the audit
        # surface — the one place a tidy sentence must not outrun its record.
        tree_state = ("NOT RECORDED — the run did not say which tree it "
                      "executed from")
    elif dirty_paths:
        tree_state = (f"DIRTY at run time: {len(dirty_paths)} uncommitted "
                      f"path(s) {dirty_paths[:5]}")
    else:
        tree_state = "clean at run time (no uncommitted paths)"
    # A run that straddled two trees renders the fact rather than a tidy
    # sentence: publish refuses it, and a receipt rendered by anything else
    # still has to say so.
    at_end = identity.get("at_end")
    if isinstance(at_end, dict) and at_end.get("commit"):
        end_dirty = at_end.get("dirty_paths")
        if (at_end.get("commit") != identity.get("commit")
                or end_dirty != dirty_paths):
            tree_state += (f" — ⛔ the tree CHANGED during the run (at end: "
                           f"{at_end.get('commit')!r}, uncommitted "
                           f"{end_dirty!r})")
    prereg_commit = binding.get("prereg_commit") or "—"
    committed_corpus_hash = binding.get("corpus_hash") or "—"
    committed_prereg_sha = binding.get("prereg_sha256") or "—"
    ordering = (binding.get("ordering") or "—").upper()
    ordering_proof = binding.get("ordering_proof") or "—"
    corpus_rel = binding.get("corpus_path") or "tests/eval/ask"
    prereg_rel = binding.get("prereg_path") or "tests/eval/ask/preregistration.json"
    return f"""---
title: "Ask lane: sealed distilled + planted-supersession exit measurement (epic #2281 Q3 exit bar)"
type: benchmarks
issue: "#{ISSUE}"
epic: "#{EPIC}"
doc_status: live
ownedBy: epistemic-team
aboutObjects: tortoise-ask-lane
extends: tools/longmem_eval/measure_ask_distilled.py, tests/eval/ask/README.md
---

# Ask lane: sealed exit measurement (issue #{ISSUE}, epic #{EPIC})

> **Generated file — do not hand-edit.** `uv run python -m
> tools.longmem_eval.measure_ask_distilled publish …` renders every number
> below from the scored run, which was computed from the sealed observations
> against the committed sealed gold. The pre-registration that fixed the bar
> was committed BEFORE the run (`tests/eval/ask/preregistration.json`, sha256
> `{prereg_sha256}`).

## 1. Run summary

| Field | Value |
|---|---|
| Benchmark | ask lane over a distilled + planted-supersession corpus (`tests/eval/ask`, {prereg['corpus']['n_cases']} hand-authored cases) |
| Cases scored | {len(scored["cases"])} of {prereg["corpus"]["n_cases"]} pre-registered |
| Classes | {", ".join(f"`{c}`" for c in sorted({row["class"] for row in scored["cases"]}))} |
| Arms | {", ".join(f"`{a}`" for a in prereg["arms"])} — one invocation, paired read (D4) |
| Gate arm | `{prereg["gate_arm"]}` |
| Reader | `{receipt["reader_model_spec"]}` (temperature {prereg["reader"]["temperature"]}, max_tokens {prereg["reader"]["max_tokens"]}, pinned: {receipt["reader_pinned"]}) |
| Judge | `{receipt["judge_model"]}` — pin `{receipt["judge_pin"]}`, rubric id `{prereg["judge"]["rubric_id"]}` (hash `{receipt["judge_rubric_id_hash"]}`) |
| Commit (exact) | `{run_commit}` — **commit binding verified at publish time** (§6) |
| Tree the run executed from | `{run_tree}` — {tree_state} |
| Corpus hash | `{receipt["corpus_hash"]}` (re-hashed from the tree AT that commit: `{committed_corpus_hash}`) |
| Pre-registration | `{prereg_rel}` — sha256 `{prereg_sha256}` (re-hashed from the tree AT that commit: `{committed_prereg_sha}`) |
| Verdict | **{receipt["verdict"].upper()}** (run_status `{receipt["run_status"]}`, failure_origin `{receipt["failure_origin"]}`) |
| Cost | ${receipt["cost_usd"]:.4f} |

## 2. Exit-bar result

| Bar | Pre-registered rule | Measured | Outcome |
|---|---|---|---|
| D8 — no superseded claim rendered live | `rendered_live == 0` | {live["rendered_live"]}/{live["n"]} | {"PASS" if live["rendered_live"] == 0 else "FAIL"} |
| D8 — every planted claim observed | `seen == n` (an unobserved claim is never a pass) | {live["seen"]}/{live["n"]} | {"PASS" if live["seen"] == live["n"] else "FAIL"} |
| No verbatim/raw regression | paired within-run: no case committed by `raw` and lost by `{GATE_ARM}` | {verbatim["distilled_committed"]}/{verbatim["n"]} distilled vs {verbatim["raw_committed"]}/{verbatim["n"]} raw; regressions: {verbatim["regressions"] or "none"} | {"PASS" if not verbatim["regressions"] else "FAIL"} |

**Verdict: {receipt["verdict"].upper()}**

{chr(10).join("- " + reason for reason in scored["bar"]["reasons"])}

## 3. Reported, NOT thresholded — the knowledge-update rate

| Arm | Current state committed | n | Rate |
|---|---|---|---|
| `{GATE_ARM}` | {ku["committed"]} | {ku["n"]} | {ku["rate"]:.2f} |
| `raw` | {ku_raw["committed"]} | {ku_raw["n"]} | {ku_raw["rate"]:.2f} |

⛔ **No pass threshold is claimed for these numbers.** The epic's D8 rule is
explicit: the benchmark-side X set comes from the first published run, so
pre-setting one here would be inventing a competing threshold. The product
gate stays where it is (`docs/product-success-eval.md`: *"0% of superseded
points appear as live answers"*), and this run's job is to be the sealed,
reproducible record that the *other* half of the exit bar ("no regression on
verbatim/raw") held.

## 4. Per-case readings

| Case | Class | `raw` committed | `{GATE_ARM}` committed | Superseded claim(s) |
|---|---|---|---|---|
{rows}

## 5. Receipt (validated)

```json
{json.dumps(receipt, indent=2, sort_keys=True)}
```

Validated by `tools/longmem_eval/measure_ask_distilled.validate_ask_receipt`
(shared W7 vocabulary; ask metric set). It is **not** run through
`w7_publish.validate_receipt`, which is LongMemEval-gated (it demands
`official_recall_all@k` + the `CATEGORY_GATE_TYPES` rows an ask run cannot
have) — the reuse boundary is recorded in the module docstring.

## 6. Reproduction

The commit binding below was **verified against the repository at publish
time** (`verify_commit_binding`) — not asserted by this document. Three things
were established: the recorded sha resolves to a commit; the corpus and the
pre-registration **at that sha** hash to the ones published above (so
`git checkout <sha>` reproduces this corpus and this protocol); and the
pre-registration's own commit is a **proper ancestor** of the run commit
(ordering `{ordering}`).

A fourth fact comes from the RUN step, not from this publishing pass: the run
recorded that it executed from tree `{run_tree}` and that the tree was
{tree_state}. That is what makes the re-run below reproduce *these numbers*
rather than merely the same corpus — a report whose numbers came from one tree
and whose sha names another would pass every corpus check and still be false.

| Binding | Value |
|---|---|
| Run commit (resolved) | `{run_commit}` |
| Tree the run executed from | `{run_tree}` — {tree_state} |
| Corpus at that commit (`{corpus_rel}`) | `{committed_corpus_hash}` |
| Pre-registration at that commit (`{prereg_rel}`) | `{committed_prereg_sha}` |
| Pre-registration commit | `{prereg_commit}` |
| Ordering | **{ordering}** — {ordering_proof} |

```bash
# 1. the run commit, and the protocol commit that must precede it
#    (both recorded in the receipt's commit_binding — re-verify with
#     `git merge-base --is-ancestor <prereg_commit> <run_commit>`)
git checkout {run_commit}
git merge-base --is-ancestor {prereg_commit} {run_commit} && echo "ordering: proven"

# 2. re-run the sealed corpus from the recorded commit
#    -B: the run REFUSES an interpreter that writes byte-caches, and it refuses
#    a tree that already carries one (#3712) — see `_require_byte_code_free`
uv run python -B -m tools.longmem_eval.measure_ask_distilled run \\
    --out /tmp/ask-observations.json
uv run python -m tools.longmem_eval.measure_ask_distilled score \\
    --run /tmp/ask-observations.json --out /tmp/ask-scored.json
```

`provider` keys are the caller's: the reader is
`{receipt["reader_model_spec"]}` and the judge `{receipt["judge_model"]}`. A
re-run on a different reader or judge is `inconclusive` by construction — the
pre-registration fixes both.

## 7. Notes

{notes}
"""


# ── CLI ───────────────────────────────────────────────────────────────────

def _load(path: str | Path) -> dict:
    return corpus.read_json(path)


def _dump(path: str | Path, obj: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(corpus.dump_json(obj), encoding="utf-8")


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.measure_ask_distilled",
        description="Sealed ask-lane exit measurement: pre-register, run "
                    "(fixtures only), score against sealed gold, evaluate the "
                    "exit bar, publish (issue #9097).")
    sub = p.add_subparsers(dest="cmd", required=True)

    pre = sub.add_parser("preregister",
                         help="write the protocol record (BEFORE any run)")
    pre.add_argument("--out", default=str(corpus.PREREGISTRATION_PATH))
    pre.add_argument("--corpus-root", default=str(corpus.ASK_DIR),
                     help="corpus tree (default: the committed one)")
    pre.add_argument("--reader", default=None,
                     help="reader model spec (default: TORTOISE_ASK_MODEL or "
                          "the product default)")
    pre.add_argument("--judge", default=None,
                     help="judge model spec (default: TORTOISE_LME_JUDGE_MODEL "
                          "or the official judge)")

    run = sub.add_parser("run", help="run the sealed corpus (fixtures only)")
    run.add_argument("--out", required=True)
    run.add_argument("--corpus-root", default=str(corpus.ASK_DIR),
                     help="corpus tree (default: the committed one)")
    run.add_argument("--mutate", action="store_true",
                     help="falsification probe: drop the planted supersessions "
                          "on the gate arm (the D8 gate must go RED)")

    sc = sub.add_parser("score", help="score against the sealed gold")
    sc.add_argument("--run", required=True)
    sc.add_argument("--out", required=True)
    sc.add_argument("--corpus-root", default=str(corpus.ASK_DIR),
                    help="corpus tree (default: the committed one)")
    sc.add_argument("--judge", default=None)
    sc.add_argument("--mock", action="store_true",
                    help="offline MockJudge (wiring checks only, never a "
                         "published number)")
    sc.add_argument("--prereg", default=str(corpus.PREREGISTRATION_PATH))

    pub = sub.add_parser("publish", help="render the receipt + run report")
    pub.add_argument("--scored", required=True)
    pub.add_argument("--commit", required=True,
                     help="the exact commit the run was made from — RESOLVED "
                          "and verified against the repository (the corpus and "
                          "pre-registration at that sha must match, and the "
                          "pre-registration's commit must be a proper "
                          "ancestor of it)")
    pub.add_argument("--repo", default=str(_REPO_ROOT),
                     help="repository the commit is resolved in (default: this "
                          "checkout's root)")
    pub.add_argument("--cost-usd", type=float, required=True)
    pub.add_argument("--out", required=True)
    pub.add_argument("--receipt", required=True)
    pub.add_argument("--prereg", default=str(corpus.PREREGISTRATION_PATH))
    pub.add_argument("--corpus-root", default=str(corpus.ASK_DIR))

    args = p.parse_args(argv)

    if args.cmd == "preregister":
        record = write_preregistration(
            args.out, corpus_root=Path(args.corpus_root),
            reader_spec=args.reader, judge_spec=args.judge)
        print(f"pre-registered {record['corpus']['n_cases']} cases "
              f"(corpus {record['corpus']['hash']}) → {args.out}")
        return 0

    if args.cmd == "run":
        # ⛔ BEFORE the measurement, not after it: a run that cannot be
        # published is refused at the start rather than after minutes of work,
        # and the refusal names the invocation that works
        # (:func:`_require_byte_code_free`).
        _require_byte_code_free(_REPO_ROOT)
        report = run_sealed(
            corpus_root=Path(args.corpus_root),
            mutate=(_drop_supersession if args.mutate else None),
            mutate_label="planted supersessions dropped on the gate arm",
            # ⛔ NOT a flag. The numbers come from THIS module's tree, so the
            # run records THIS module's tree — a `--repo` flag could only ever
            # name a different one, which is the false PASS the record exists
            # to close (`run --repo <main checkout>` would be the one-flag way
            # around the refusal it is supposed to trip).
            repo_root=_REPO_ROOT)
        _dump(args.out, report)
        measured = sum(1 for c in report["cases"]
                       for arm in report["arms"] if c["arms"][arm]["ok"])
        total = len(report["cases"]) * len(report["arms"])
        identity = report["code_identity"]
        print(f"ran {measured}/{total} case-arms over {len(report['cases'])} "
              f"cases from {(identity.get('commit') or 'NO COMMIT')[:12]} "
              f"({len(identity.get('dirty_paths') or [])} uncommitted path(s)) "
              f"→ {args.out}")
        return 0

    if args.cmd == "score":
        root = Path(args.corpus_root)
        scored = score(run_report=_load(args.run),
                       corpus_root=root, judge_spec=args.judge, mock=args.mock)
        scored["bar"] = evaluate_exit_bar(
            scored=scored, manifest=corpus.verify_manifest(root),
            prereg=_load(args.prereg))
        _dump(args.out, scored)
        print(f"scored → verdict {scored['bar']['verdict'].upper()}")
        for reason in scored["bar"]["reasons"]:
            print(f"  - {reason}")
        return 0 if scored["bar"]["verdict"] != "regression" else 1

    if args.cmd == "publish":
        scored = _load(args.scored)
        prereg = _load(args.prereg)
        prereg_sha = corpus.sha256_file(args.prereg)
        root = Path(args.corpus_root)
        # (0) THE COMMIT BINDING — asked of the repository, before anything is
        # published: a report whose "reproducible from the recorded sha alone"
        # sentence is false is worse than no report, because it reads as
        # evidence. Checked FIRST so the refusal names the binding failure
        # rather than a receipt field that is merely downstream of it.
        binding, binding_reasons = verify_commit_binding(
            commit=args.commit, repo_root=Path(args.repo),
            corpus_root=root, prereg=Path(args.prereg))
        if binding is None:
            print("refusing to publish — the recorded commit does not vouch "
                  "for this run:", file=sys.stderr)
            for reason in binding_reasons:
                print(f"  - {reason}", file=sys.stderr)
            return 2
        # (0b) THE OTHER HALF — "the run is reproducible from the recorded sha
        # alone" — is enforced by `_validate_code_identity` inside the receipt
        # validation below, not by a second check here: the run's own
        # `code_identity` record either agrees with the commit this receipt
        # names and is clean, or the receipt is invalid. ONE implementation, so
        # the two cannot drift; the binding above is separate because it asks
        # the repository a question the receipt cannot answer.
        # Re-bind the verdict to the CURRENT corpus: a report published over a
        # drifted corpus, or against a pre-registration that no longer
        # describes it, is refuse-to-publish, not a footnote.
        bar = evaluate_exit_bar(
            scored=scored, manifest=corpus.verify_manifest(root),
            prereg=prereg)
        scored["bar"] = bar
        receipt = build_receipt(scored=scored, commit=binding["run_commit"],
                                cost_usd=args.cost_usd,
                                prereg_sha256=prereg_sha,
                                commit_binding=binding)
        issues = validate_ask_receipt(receipt)
        if issues:
            print("refusing to publish — receipt invalid:", file=sys.stderr)
            for issue in issues:
                print(f"  - {issue}", file=sys.stderr)
            return 2
        _dump(args.receipt, receipt)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            render_report(receipt=receipt, scored=scored, prereg=prereg,
                          prereg_sha256=prereg_sha),
            encoding="utf-8")
        print(f"published {args.out} + {args.receipt} "
              f"(verdict {bar['verdict'].upper()})")
        return 0

    raise AssertionError(args.cmd)  # pragma: no cover


def _drop_supersession(sdk) -> None:
    """Falsification probe: lose the supersession (the shape of 'it was never
    recorded'). The D8 gate MUST go red — that is what proves it can say no."""
    sdk._get_proj().g.query(
        "MATCH (a:Point)-[r:CORRECTS]->(b:Point) DELETE r "
        "WITH b SET b.status = 'live', b.validTo = null, b.expiredAt = null")


if __name__ == "__main__":
    raise SystemExit(_main())
