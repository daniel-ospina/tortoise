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

Sibling module, not an extension of ``run_protocol.py``: that protocol's steps
are LongMemEval-shaped (500-Q run, vs-V3-baseline delta, 1k benchmark, its own
step numbering). Extending it would couple two benchmarks' pre-registrations.
``measure_temporal.py`` is the sibling precedent.

CLI:

    # 1. pre-register (committed BEFORE the run — the protocol commit)
    uv run python -m tools.longmem_eval.measure_ask_distilled preregister \\
        --out tests/eval/ask/preregistration.json

    # 2. run the sealed corpus (fixtures only; no gold, no grading)
    uv run python -m tools.longmem_eval.measure_ask_distilled run \\
        --out docs/benchmarks/ask-distilled/run-observations.json

    # 3. score against the sealed key and evaluate the exit bar
    uv run python -m tools.longmem_eval.measure_ask_distilled score \\
        --run <observations> --out <scored>

    # 4. publish the receipt + the run report
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
import json
import os
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
               mutate_label: str | None = None) -> dict:
    """Run the sealed corpus: both arms per case, fixtures ONLY.

    ⛔ The inputs are ``corpus.load_fixtures`` — ``fixtures/*.json``. The gold
    is not loaded here, not passed here and not reachable from here; the
    grading step (:func:`score`) is where the sealed key is opened. That is
    what "the sealed gold is never read by the runner" means mechanically.

    ``mutate`` is the falsification hook (applied to the distilled arm after
    seeding): drop the supersession and the D8 gate must go RED, which is what
    proves the gate can say no.
    """
    fixtures = corpus.load_fixtures(corpus_root)
    if not fixtures:
        raise ValueError(f"sealed corpus at {corpus_root} has no fixtures")
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
                  notes: list[str] | None = None) -> dict:
    """The validated receipt (W7 vocabulary, ask metric set)."""
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
    return _w7_build_receipt(
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
| Commit (exact) | `{receipt["commit"]}` |
| Corpus hash | `{receipt["corpus_hash"]}` |
| Pre-registration | `tests/eval/ask/preregistration.json` — sha256 `{prereg_sha256}` |
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

```bash
# 1. the protocol was committed BEFORE the run (this is the ordering claim)
git log --oneline -- tests/eval/ask/preregistration.json

# 2. re-run the sealed corpus from the recorded commit
git checkout {receipt["commit"]}
uv run python -m tools.longmem_eval.measure_ask_distilled run \\
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
    pub.add_argument("--commit", required=True)
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
        report = run_sealed(
            corpus_root=Path(args.corpus_root),
            mutate=(_drop_supersession if args.mutate else None),
            mutate_label="planted supersessions dropped on the gate arm")
        _dump(args.out, report)
        measured = sum(1 for c in report["cases"]
                       for arm in report["arms"] if c["arms"][arm]["ok"])
        total = len(report["cases"]) * len(report["arms"])
        print(f"ran {measured}/{total} case-arms over {len(report['cases'])} "
              f"cases → {args.out}")
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
        # Re-bind the verdict to the CURRENT corpus: a report published over a
        # drifted corpus, or against a pre-registration that no longer
        # describes it, is refuse-to-publish, not a footnote.
        root = Path(args.corpus_root)
        bar = evaluate_exit_bar(
            scored=scored, manifest=corpus.verify_manifest(root),
            prereg=prereg)
        scored["bar"] = bar
        receipt = build_receipt(scored=scored, commit=args.commit,
                                cost_usd=args.cost_usd,
                                prereg_sha256=prereg_sha)
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
