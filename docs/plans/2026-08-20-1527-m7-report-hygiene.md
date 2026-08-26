---
title: "Plan — #1527 M7: self-explanatory report + run hygiene (leg-mix, cost, workers, checkpoint fingerprint, py≥3.12, dataset recall re-validation)"
type: plan
domain: capability
doc_status: planned
created: 2026-08-20
ownedBy: epistemic-team
governingAgreement: "#1527 (epic #1509, M7)"
---

<!-- research-path: docs/epics/2026-08-20-1509-extractor-v3/02-research-brief.md -->

# Plan — #1527 M7: Self-explanatory report + run hygiene

## Context

- **Issue:** #1527 — M7 (epic #1509 Extractor V3). Contract: `docs/epics/2026-08-20-1509-extractor-v3/03-scope.md` M7 + `05-detailed-e2e.md` Preconditions 2 + E2E-2/E2E-3; `04-plan.md` §6 interface rows "Eval report" (integrity block, leg-mix, pool size, evidence written/retrieved, write-path cost, vacuity) and "Checkpoint" (code fingerprint `git_sha + config + prompt`, refuse stale resume, flock); architecture component 4 (Harness).
- **Test alignment:** test-design #1515 surfaces **11** (FTS-vs-TFIDF dual stack → leg-mix), **14** (checkpoint file → fingerprint + flock), **20** (`--workers` parallelism → checkpoint race), **22** (Layer-1 payload → report projection), **27** (dataset fixture → recall-semantics audit). Verification checklist (issue body): integration+unit.
- **Complexity:** `complexity:standard` → **Standard tier** (condensed plan, no worktree creation needed — writing into the provided `.worktrees/1509-plans`).
- **Dependencies:** M1 (#1522, dead-code fix) — **landed** on this worktree's HEAD (21970c6c, PR #1554/#1552). P2 (#1530) defines the `LlmErrorClass` taxonomy in `tortoise/model_adapters.py` — NOT yet landed on HEAD; M7's census aligns to its vocabulary with a local fallback (see Cross-lane interfaces).
- **Scope guard:** M7 is report + harness hygiene only. It does **not** implement M6's N/A-not-0.0 evidence semantics (owned by M6) and does **not** change retrieval semantics — it audits and *records* them.
- ⛔ **Prerequisite note:** the issue body carries the scoping content (verification checklist, test-design reference, review-gate fixes) but no `<!-- issue-scoping:` signature comment on #1527. The epic 03-scope/04-plan/05-detailed-e2e docs are the architecture contract and are followed here; flagging the missing signature for the record, not blocking (owner-directed).

## Current state (verified on this worktree HEAD)

| Surface | Today | Problem (M7) |
|---|---|---|
| `tools/longmem_eval/report.py::build_report` | accuracy/retrieval/latency/methodology/failures/outcomes keys only; no integrity block; no leg-mix, pool size, evidence, write-path cost | Report is not self-explanatory; integrity is never asserted |
| `tools/longmem_eval/run.py::_print_summary` | prints accuracy **before** anything else | M4/M7: integrity block + error census must print **BEFORE** score |
| `tools/longmem_eval/run.py::_save_checkpoint`/`_load_checkpoint` | stores `{outcomes, failures, updated_at_utc}`; no fingerprint; atomic `os.replace` but no cross-process lock | Stale-resume hazard (config drift silently reuses old results); two run processes → lost updates |
| `tools/longmem_eval/run.py::run_evaluation` | workers=1 sequential or ThreadPoolExecutor; single in-process `threading.Lock`; `total_ms` covers ingest+retrieve+reader+judge lumped | write-path cost not isolated; no pool-size/leg-mix/evidence per outcome |
| `tools/longmem_eval/retrieve.py::retrieve_for_question` | hits carry `match_source` (fts/vector/structural/rrf/tfidf) but it is never aggregated; evidence counts implicit only | leg-mix, evidence-written/retrieved not persisted |
| `tools/longmem_eval/run.py::run_main` | no Python guard (pyproject `requires-python >=3.12` exists but nothing refuses a 3.11 env at runtime) | run hygiene: refuse <3.12 with a clear message |
| `tools/longmem_eval/dataset.py` + `report.py` | recall aggregates computed over **all** questions incl. `_abs`; no dataset-semantics audit | E2E-3 Precondition 2: no `turn_recall`/`evidence_recall` published before the audit |

### Pattern Research

> **Findings date:** 2026-08-20
> **Gate skipped:** plan touches zero new third-party dependencies — `fcntl` (stdlib, in-repo pattern `tortoise/shared_state/concurrency.py::locked_append`), `hashlib`, `sys`, `json` (stdlib); all other surfaces are in-repo modules already used by the runner. Step B (Perplexity verification gate) does not fire per the zero-deps skip rule. Step A (prior research intake) ran: epic 02-research-brief (dataset/recall + eval-discipline sections), 03-scope M7/M8, 04-plan §6, 05-detailed-e2e E2E-2/E2E-3.

**Canonical + paper-verification (conducted during intake — verifiable, cited):**
- LongMemEval official repo `src/evaluation/print_retrieval_metrics.py` **excludes `_abs` questions** from all retrieval metrics (`in_data = [x for x in in_data if '_abs' not in x['question_id']]`). Our aggregates include them → divergence vs the paper.
- LongMemEval official `src/retrieval/run_retrieval.py` builds the **turn corpus from `role == 'user'` turns only** and asserts `'has_answer' in turn` on every user turn; evidence turns become positive corpus ids. Our deterministic leg indexes all turns regardless of role.
- Official recall is `recall_any` / `recall_all` (binary) + `ndcg` per `src/retrieval/eval_utils.py::evaluate_retrieval`. Ours is a per-question **fraction** of evidence in top-k — a documented variant, not the paper's binary.
- Paper/README semantics: `answer_session_ids` = evidence sessions (session-level recall); `has_answer` turns = evidence turns (turn-level recall). There is **no `answer_turn` field** in the cleaned S split (verified by direct census, below).

### Dataset census (measured 2026-08-20 on `~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json` — the audit's expected-baseline)

| Metric | Value | Implication |
|---|---|---|
| instances | 500 | full S split |
| instances with `answer_turn` field | **0** | `answer_turn` does not exist in the cleaned split; turn ground truth = `has_answer` turns (paper-aligned) |
| qids with ≥1 `answer_session_id` | 500/500 | all non-empty, incl. every `_abs` qid |
| `answer_session_ids ⊆ haystack_session_ids` | 500/500 | id namespace consistent — no silent-zero session recall |
| qids with ≥1 `has_answer` turn | 479/500 | 21 without — **all `_abs`** (legitimate evidence-absent abstentions) |
| `has_answer`-turn sessions ⊆ `answer_session_ids` | 479/479 | no violations |
| `has_answer` turns (total) | 896 = 842 user + **54 assistant** | 54 assistant-role evidence turns are out-of-corpus for the official metric; ours include them |
| turns carrying the `has_answer` key | 10,960 of 246,750 (896 True) | sparse marking — official code's per-user-turn assert does **not** hold on the cleaned split |
| `_abs` qids | 30; 9 with `has_answer` turns; 21 evidence-absent; 0 with empty `answer_session_ids` | recall aggregates over `_abs` differ from paper (paper excludes them entirely) |

**Fixture consistency caveat:** the committed MINI fixture's abstention (`mini_abs_005_abs`) has `answer_session_ids: []` — the real data has **none** empty. The audit surfaces this as a fixture-vs-real divergence (informational; mini is a pipeline smoke, not a metric source).

### Integration Surface Map (test-design #1515, M7-owned subset)

| Surface | Boundary | Bug pattern | Test layer |
|---|---|---|---|
| 11 — FTS-vs-TFIDF dual stack | `search_engine` emits `match_source` (`rrf`/`tfidf` embedded) → annotated hits | silent leg skew embedded-vs-real; leg-mix never persisted | integration+unit (`test_leg_mix_recorded`) |
| 14 — checkpoint file | `run.py::_save/_load_checkpoint` (JSON, atomic replace) | corrupt JSON crash; stale resume; last-writer-wins under workers | integration+unit (fingerprint refuse, flock merge) |
| 20 — `--workers` parallelism | ThreadPoolExecutor per question; shared checkpoint | checkpoint race, lost updates across processes | integration (two-process flock test) |
| 22 — Layer-1 payload projection | `outcomes_to_report` extra projection | golden report-shape drift (M1 regression class — pinned) | unit (`test_outcomes_to_report_golden_shape`) |
| 27 — dataset fixture | `dataset.load_dataset` → audit census | semantics mismatch vs paper → untrustworthy recall | unit (synthetic audit fixture) + @slow (real S split census) |

### UX Design Decisions

**Skipped** — pure eval-tooling/data-surface change, zero UI files, no reader/UX surface touched (the reader prompt is M5's lane). UX_RATING for the epic is medium but this issue's diff is all harness/report. The report's printed summary IS an operator UX — the only "UX" decision here is print ordering (integrity before score), made by the M4/M7 contract, not a design gate.

---

## Design decisions

### D1 — Integrity block is a first-class report key, printed before score
`report["integrity"] = {valid, threshold, n_attempted, n_valid, n_invalid, invalid_rate, error_census{...}, checks[...]}`.
- `n_attempted = n_completed + n_failed` (dedup by qid across outcomes+failures); `invalid` = a failed question **or** a completed question with `n_ingest_errors > 0` (per-question `valid` flag, D4); `invalid_rate = invalid / n_attempted`; `valid = invalid_rate <= threshold`.
- Threshold default **0.0**, overridable via `--integrity-threshold <float>` (effective value recorded in `integrity.threshold`); when an override is used, `integrity.justified=True` and `integrity.threshold_violation_justification` carries the free text from `--integrity-justification`. `valid = invalid_rate <= threshold` with the **effective** threshold — a violated override (`invalid_rate > threshold`) still yields `valid=false`; the report always records the numbers and the reason, so no degraded run can masquerade as clean (E2E-2).
- `checks` is a fixed list of self-describing strings (python guard, dataset loaded, audit present, fingerprint matched, census computed). `_print_summary` prints the integrity block + error census **first**, then accuracy/retrieval/latency.

### D2 — Leg-mix is a per-hit `match_source` aggregation, never re-derived
`match_source` already lands on annotated hits (D8 field passthrough in `retrieve.py::_annotate_hits`). No new engine work: per outcome compute `leg_mix` = Counter of `h["match_source"] or "unknown"` over the **top_k context points** (what the reader saw) **and** `leg_mix@k` per k in `ks` (over `hits[:k]`). Report aggregate: `report["leg_mix"] = {total_counts, mean_share, unknown_count, n_questions}`. Embedded mode will legitimately show `{"tfidf": n}`; real mode `{"rrf": n}` (+ per-leg when the engine emits it) — E2E-1's "never null" is asserted at the hit level in `retrieve_for_question` (missing → `"unknown"`, never `""`).

### D3 — Pool size is a live graph count, not derived from stats
After ingest (before retrieval), one authoritative Cypher: `MATCH (p:Point {lme_question_id:$qid}) RETURN count(*)`. Per outcome `pool_size`; report `pool_size = {mean, p50, p95}`. This is the retrieval-pool denominator the existing `methodology.retrieval_scope` paragraph documents ("question-scoped corpus") — now quantified. Single query, ~1ms, no N+1.

### D4 — Evidence written/retrieved + vacuity are explicit numbers
- `evidence_written` per outcome: deterministic leg = `ingest_stats["evidence_turns"]`; v2 leg = `ingest_stats["evidence_points"]` (already produced by `ingest_v2._write_payload`).
- `evidence_retrieved@k` per outcome: count of annotated hits with `has_answer` in `hits[:k]` (already computable; the `turn_recall` numerator — now persisted).
- Report `evidence = {written_mean, retrieved_mean@k, evidence_bearing_n, evidence_absent_n, vacuity_rate}` where **vacuity is computed over evidence-bearing questions only** (`evidence_written > 0`): vacuity_rate = share of those with `evidence_retrieved@k == 0` at the design-locked k (top_k). Evidence-absent questions (the 21 `_abs`) are counted separately (`evidence_absent_n`) and excluded — E2E-3's "ground-truth-absent abstentions do not drag the denominator". M6 owns the N/A-not-0.0 *per-question* semantics; M7's vacuity aggregate assumes it lands (Cross-lane gate ⛔-2).

### D5 — Write-path cost is an isolated `ingest_latency_ms`
In `_run_one`, time the ingest call (`time.monotonic()` around `ingest_haystack`/`ingest_haystack_v2`) → `ingest_latency_ms` per outcome; report `latency_ms["ingest"] = {mean_ms, p50_ms, p95_ms}` alongside retrieval/reader/judge. `total_ms` stays the wall-clock question total (ingest is a component of it, not double-counted). This makes the per-question LLM-vs-write attribution visible: ingest (extractor) vs retrieve vs reader vs judge.

### D6 — Error census uses a documented eval taxonomy, aligned to the P2 contract
New `tools/longmem_eval/errors.py`:
```python
EVAL_ERROR_CLASSES = ("fatal", "fatal_config", "transient", "retries_exhausted", "ingest", "parse", "unknown")
def classify_eval_error(exc: BaseException, *, site: str) -> str: ...
```
- Classification reuses the P2 status-code semantics (401/402/403→`fatal`, 400/404+other-4xx→`fatal_config`, 408/425/429/500/502/503/504+5xx→`transient`, connection/timeout/URLError/OSError→`transient`, non-HTTP parse/KeyError→`parse`, else `unknown`). `site ∈ {"reader", "judge", "ingest"}` recorded with the class (e.g. `reader:transient`).
- **P2 alignment:** when `tortoise/model_adapters.py::classify_llm_error` lands (#1530), `classify_eval_error` delegates to it for the coarse class and keeps `site` as the eval's own dimension. Until then it uses its own frozensets — documented, not forked silently (Cross-lane note).
- Per-failure entries gain `error_class`; per-outcome `error_classes` lists classified classes for `ingest_stats["errors"]` (v2) + the failure class. Report `integrity.error_census` = Counter over all failure classes + ingest error classes.

### D7 — Checkpoint fingerprint: refuse stale resume by construction
Checkpoint schema v2:
```json
{
  "fingerprint": {
    "git_sha": "21970c6c…",
    "python": "3.12.7",
    "dataset_fingerprint": "sha256[:16] of dataset file (or \"unknown\")",
    "split": "s", "ks": [5, 10, 20], "top_k": 20,
    "ingest_mode": "deterministic", "extractor_model": null,
    "reader_model": "openrouter:deepseek/deepseek-chat",
    "judge_model": "openai:gpt-4o-2024-08-06",
    "max_retries": 3,
    "reader_prompt_hash": "<_sha16(reader_prompt_source())>",
    "judge_rubric_id_hash": "<_sha16(JUDGE_RUBRIC_ID)>"
  },
  "outcomes": [...], "failures": [...], "updated_at_utc": "..."
}
```
- `_build_fingerprint(...)` computes the live fingerprint from the effective run config (reader/judge `model_id`, resolved extractor spec, `reader_prompt_source()`, `JUDGE_RUBRIC_ID`, ks/top_k/split/ingest_mode/max_retries, `git_sha()`, `sys.version`, dataset file hash).
- `_load_checkpoint(path, expected)` compares; on mismatch raises `CheckpointStaleError(RuntimeError)` listing the differing fields — **refuse stale resume** (E2E-2 owned negative: "stale resume → clear abort"). A legacy v1 checkpoint (no `fingerprint` key) is **also refused** with "checkpoint predates the fingerprint contract — delete or re-fingerprint".
- `workers` is deliberately **excluded** from the fingerprint (per-question isolation ⇒ results are workers-invariant) but recorded in `methodology.workers`.
- `dataset_fingerprint` is computed in `run_main` (where the file/cache path is known) and threaded into `run_evaluation`; programmatic/test callers pass `"unknown"` (stable within a run).
- The fingerprint fields are also persisted in `report["methodology"]` (git_sha already there; add python/workers/dataset_fingerprint) — a report always says *what code and dataset produced it*.
- Extractor identity (M7 #1739): `extractor_model` discriminates **both** wire id and tuning. Bare adapters fingerprint as `wire-id` plus a tuning suffix (`|max_tokens=…|temperature=…|thinking_budget=…|disable_reasoning=…`) when non-default, so registry entries sharing a wire id but differing in tuning (`deepseek-v4-pro-xhigh` vs `deepseek-v4-pro-noreason`) differ and a cross-tuning resume is refused. The default CLI wrapper path (`RoutingModel`/`RotatingModel` — neither exposes `.model_id`/`.id`) is composed structurally as `provider:wire-id` per member adapter joined by `+` — deterministic across processes, never an address-bearing repr. `RoutingModel` members join in (primary, fallback) order (order is effective config — the primary serves); `RotatingModel` members are sorted by provider (pool order is a routing detail — weights are per-provider — so a reorder keeps checkpoints valid while a membership change invalidates them). Provider is a routing detail for single adapters (wire-id-only, the #1732 cross-provider contract) but part of the wrapper identity (a pool change is an effective-config change).
- WIRE-ID MUTABILITY (documented hazard, #1706): wire ids are API-facing and mutable — #1706 renamed the direct flash id `deepseek-v4-flash` → `deepseek-chat`. A future rename **loudly invalidates all existing checkpoints** (`CheckpointStaleError` on `extractor_model`): safe by design, expected, and the correct direction (refuse, never silently reuse).

### D8 — Checkpoint flock: merge-under-lock, no lost updates
- `_save_checkpoint` becomes: acquire an exclusive **flock on `<checkpoint>.lock`** (reuse the `fcntl` mechanics of `tortoise/shared_state/concurrency.py::locked_append` — extract a small `flock_exclusive(path)` context manager into `tools/longmem_eval/errors.py`-adjacent `_checkpoint.py` or reuse `tortoise/shared_state/concurrency.py` directly), then **re-read the file under the lock**, merge disk outcomes/failures with the in-memory snapshot (dict-by-qid for outcomes, list-merge for failures), write tmp, `os.replace`, release.
- Re-read-under-lock makes two concurrent run **processes** on one checkpoint lose nothing (each merge adds its qids). The in-process `threading.Lock` stays (guards the shared `done`/`failures` state between worker threads); flock adds the cross-process layer. `_load_checkpoint` also takes the lock (short) so a reader never sees a mid-merge file (os.replace already makes the final file atomic).
- Contract note: a checkpoint is per-run (one dataset+config). Two processes with *different* configs sharing a file → fingerprint mismatch aborts (D7). Same-config/disjoint-subset sharing is supported by the merge.
- `fcntl` is POSIX (macOS/Linux eval env) — documented; no Windows story needed.

### D9 — Python ≥3.12 runtime guard
`run_main` starts with:
```python
if sys.version_info < (3, 12):
    raise SystemExit("longmem_eval requires Python >= 3.12 (got %d.%d) — pyproject requires-python >=3.12; the eval graph write path is 3.12-only" % sys.version_info[:2])
```
Factored into `_assert_python_version()` for unit testing (monkeypatch `sys.version_info`). Guard is **before** dataset load / key checks — refuse fast. `methodology.python_version` records the actual.

### D10 — Dataset recall-semantics audit + publication gate (E2E-3 Precondition 2)
New `tools/longmem_eval/dataset_audit.py`:
```python
def audit_dataset(instances: list[dict]) -> dict: ...  # census + consistency + divergences + verdict
def semantics_baseline() -> dict: ...                  # the 2026-08-20 expected values (D11 table)
```
Audit record (persisted under `methodology.dataset_semantics_audit`):
```json
{
  "findings_date": "2026-08-20",
  "n_instances": 500,
  "fields": {"answer_session_ids": "present", "answer_turn": "absent", "has_answer": "sparse-present"},
  "coverage": {"with_answer_session_ids": 500, "with_has_answer_turns": 479, "with_answer_turn_field": 0},
  "consistency": {"answer_session_ids_subset_haystack": 500, "has_answer_sessions_subset_answer_session_ids": 479, "violations": 0},
  "has_answer_roles": {"user": 842, "assistant": 54},
  "abstentions": {"n": 30, "with_has_answer": 9, "evidence_absent": 21, "empty_answer_session_ids": 0},
  "paper_divergences": [
    "official print_retrieval_metrics.py excludes _abs questions from retrieval aggregates — legacy aggregates include them (paper-aligned _paper@k keys added)",
    "official turn corpus indexes role=='user' turns only — 54 assistant-role evidence turns are out-of-corpus; legacy turn_recall includes them",
    "official metrics are binary recall_any/recall_all + ndcg; ours is a per-question fraction (documented variant)",
    "official code asserts has_answer on every user turn; the cleaned split marks it sparsely (10,960 of 246,750 turns)"
  ],
  "verdict": "trusted-as-documented-variant",
  "gate": "no turn_recall/evidence_recall number is published unless this record is present in the report methodology"
}
```
- **Paper-aligned aggregates:** `retrieval.session_recall_paper@k` / `turn_recall_paper@k` / `evidence_recall_paper@k` = the same fraction metric computed over **non-`_abs` questions only** (the official exclusion), keeping legacy keys for backward compatibility; both definitions recorded in `methodology.recall_definition`. (User-role-only turn denominators are recorded as a known residual divergence — v2-leg evidence points carry no role; see Open questions.)
- **Publication gate (enforced by construction):** `build_report(...)` gains a required `dataset_semantics_audit` argument — `ValueError` if absent. `run_evaluation` computes the audit from the loaded instances and threads it through `outcomes_to_report → build_report`. There is no flag to skip it. This *is* E2E-3 Precondition 2: a report containing recall numbers provably contains the audit record.
- The audit also runs on the MINI fixture in CI — it doubles as a fixture-consistency check (surfacing `mini_abs_005_abs`'s empty `answer_session_ids` as a recorded divergence from real data).

### D11 — Report contract: additive top-level keys only
New top-level keys: `integrity`, `leg_mix`, `pool_size`, `evidence`; `latency_ms.ingest`; `methodology` gains `python_version`, `workers`, `dataset_fingerprint`, `dataset_semantics_audit`, `integrity_threshold`. Per-outcome gains: `valid`, `error_classes`, `leg_mix`, `leg_mix@k`, `pool_size`, `evidence_written`, `evidence_retrieved@k`, `ingest_latency_ms` (all persisted in the `outcomes` projection — the Layer-1 payload, surface 22). The `test_outcomes_to_report_golden_shape` exact key-set pin **must** be updated to the new contract (this is the M1-regression-class guard, not a freeze). The #1414 parity battery compares methodology *hashes* (`reader_prompt_hash`/`judge_rubric_id_hash`), not the shape — additive keys are safe (verified: `battery/parity/runner.py:80-84`).

---

## Implementation steps

### Task 1: Python ≥3.12 guard + methodology env fields

**Intent:** refuse a <3.12 eval env fast and record what code/version produced every report (run-hygiene half of M7).
**Acceptance:** `run_main` exits nonzero with a clear message on 3.11; report methodology carries `python_version`, `workers`, `dataset_fingerprint`.
**Files:**
- Modify: `tools/longmem_eval/run.py` (`run_main`, new `_assert_python_version`, `run_evaluation` signature, `outcomes_to_report` signature)
- Modify: `tools/longmem_eval/report.py` (`build_report` methodology block)
- Test: `tests/test_longmem_runner.py`

**Step 1 (test):** `test_python_guard_refuses_lt_312` — monkeypatch `sys.version_info` to `(3, 11, 9)`; assert `_assert_python_version()` raises `SystemExit` with "3.12" in the message; `(3, 12, 0)` passes.
**Step 2 (implement):** add `_assert_python_version()` in `run.py`; call as the first statement of `run_main`.
**Step 3 (test):** extend `test_outcomes_to_report_golden_shape` (or a new `test_report_methodology_env_fields`) to assert `methodology.python_version`, `methodology.workers` are present and non-empty.
**Step 4 (implement):** add `python_version`/`workers` params to `outcomes_to_report`/`build_report`; thread `workers` from `run_evaluation`; compute python version once (`"%(major)d.%(minor)d.%(micro)d"`).
**Step 5 (verify):** `uv run pytest tests/test_longmem_runner.py -v`; **Step 6:** commit `feat(1527): python>=3.12 guard + methodology env fields`.

### Task 2: Checkpoint code fingerprint + stale-resume refusal

**Intent:** a resumed checkpoint is only trustworthy if the effective run config is byte-identical to what produced it (E2E-2: stale resume → clear abort).
**Acceptance:** checkpoint writes `fingerprint`; resume with a different reader/judge/ks/top_k/split/ingest_mode/max_retries/prompt-hash/git-sha/dataset raises `CheckpointStaleError` with the differing fields named; legacy v1 checkpoints are refused.
**Files:**
- Modify: `tools/longmem_eval/run.py` (`_load_checkpoint`, `_save_checkpoint`, new `_build_fingerprint`, new `CheckpointStaleError`, `run_evaluation` gets `dataset_fingerprint` param)
- Modify: `tools/longmem_eval/run.py::run_main` (compute dataset file sha256 → pass through)
- Test: `tests/test_longmem_runner.py`

**Step 1 (test):** `test_checkpoint_fingerprint_refuses_stale_resume` — run mini with `checkpoint=cp`; re-run with `reader_model`-changed kwargs → `pytest.raises(CheckpointStaleError)` and the message names `reader_model`. `test_checkpoint_fingerprint_legacy_refused` — write a v1 checkpoint (no fingerprint) → same refusal. `test_checkpoint_fingerprint_matching_resumes` — identical kwargs resume cleanly (existing `test_checkpoint_resume_skips_completed_questions` extends to assert the fingerprint key exists).
**Step 2 (implement):** `CheckpointStaleError(RuntimeError)`; `_build_fingerprint(...)` returns the dict (D7 schema; `_sha16(reader_prompt_source())`, `_sha16(JUDGE_RUBRIC_ID)` already exist); `_load_checkpoint(path, expected)` compares field-by-field and raises with a diff list; `_save_checkpoint` writes `fingerprint`.
**Step 3 (implement):** `run_main` computes `dataset_fingerprint` = `hashlib.sha256(file).hexdigest()[:16]` over the resolved dataset file (`data_path` or cache file); pass to `run_evaluation` (default `"unknown"` for programmatic callers).
**Step 4 (verify):** pytest green; **Step 5:** commit `feat(1527): checkpoint code fingerprint refuses stale resume`.

### Task 3: Checkpoint flock — cross-process merge-under-lock

**Intent:** two run processes sharing one checkpoint must not lose each other's results (surface 20, checkpoint race; E2E-2 owned negative).
**Acceptance:** concurrent `_save_checkpoint` calls from separate processes merge outcomes/failures without loss; `_load_checkpoint` is lock-guarded.
**Files:**
- Modify: `tools/longmem_eval/run.py` (flock around load + save; re-read-and-merge inside `_save_checkpoint`)
- Modify: `tortoise/shared_state/concurrency.py` (extract `flock_exclusive(path) -> fd` context-manager helper reused by `locked_append` and the runner — small refactor, backward-compatible) — *alternatively* keep the helper private in `run.py`; prefer the shared helper (single flock implementation, tested once).
- Test: `tests/test_longmem_runner.py` + `tortoise/shared_state/tests/test_concurrency.py`

**Step 1 (test):** `test_checkpoint_two_processes_no_lost_updates` — `multiprocessing.Process` × 2, each runs `run_evaluation` over a disjoint half of the mini set with the same `checkpoint` path and identical kwargs; join; assert the final checkpoint file's outcomes contain **all 5 qids** (read under lock). Also extend the concurrency self-test for `flock_exclusive` reentrancy/EX semantics.
**Step 2 (implement):** add `flock_exclusive` to `tortoise/shared_state/concurrency.py` (yield fd after `LOCK_EX`; `LOCK_UN`+close on exit; timeout spin like `locked_append`); refactor `locked_append` to use it.
**Step 3 (implement):** `_load_checkpoint` wraps the read in `flock_exclusive(path.with_suffix(".lock"))`; `_save_checkpoint` acquires the lock, **re-reads** the file (if it exists), merges `done`-by-qid (outcomes dict wins on tie) and failures (append-only by qid), writes tmp, `os.replace`, releases.
**Step 4 (verify):** `uv run pytest tests/test_longmem_runner.py tests/test_concurrency.py -v`; **Step 5:** commit `feat(1527): checkpoint flock — cross-process merge under lock`.

### Task 4: Per-question instrumentation — leg-mix, pool size, evidence written/retrieved, ingest latency

**Intent:** the report can answer "which leg found what, how big was the pool, how much evidence was written vs retrieved, and what did ingestion cost" per question and per run (E2E-2 report contents).
**Acceptance:** every outcome carries `leg_mix`, `leg_mix@k`, `pool_size`, `evidence_written`, `evidence_retrieved@k`, `ingest_latency_ms`; the report aggregates them.
**Files:**
- Modify: `tools/longmem_eval/run.py` (`_run_one` — time ingest, query pool size, compute evidence counters; `outcomes_to_report` projection)
- Modify: `tools/longmem_eval/retrieve.py` (`retrieve_for_question` returns `match_source_counts`, `match_source_counts@k`, `evidence_retrieved@k`; ensure `match_source` is never `""` on annotated hits)
- Modify: `tools/longmem_eval/ingest.py` / `ingest_v2.py` (no change needed — `evidence_turns`/`evidence_points` already in stats; verify only)
- Test: `tests/test_longmem_runner.py`

**Step 1 (test):** `test_retrieval_leg_mix_and_evidence_counts` — after `retrieve_for_question` on mini: `match_source_counts` totals == len(hits) and `"tfidf" in match_source_counts` (embedded mode); `evidence_retrieved@k` ≤ evidence turns; `evidence_retrieved@k["5"] >= 1` for `mini_ie_user_001`. `test_outcome_instrumentation_fields` — run mini pipeline: every outcome has the 6 new keys; `pool_size == 8` for the 2-session mini question (2 sessions × 3 turns + 1 raw); `evidence_written == ingest_stats.evidence_turns`; `ingest_latency_ms > 0`.
**Step 2 (implement retrieve):** compute `match_source_counts` (over top_k context) + `match_source_counts@k` (over `hits[:k]`) + `evidence_retrieved@k`; `_annotate_hits` maps missing match_source to `"unknown"`.
**Step 3 (implement run):** in `_run_one`, wrap ingest in timing → `ingest_latency_ms`; after ingest, `pool_size` via the D3 Cypher; copy the new retrieve counters into the outcome; extend `outcomes_to_report`'s projection key list.
**Step 4 (verify):** pytest; **Step 5:** commit `feat(1527): per-question leg-mix/pool/evidence/cost instrumentation`.

### Task 5: Error census + integrity block + report aggregates + print-before-score

**Intent:** the report proves the run wasn't silently degraded and its failures are explainable (E2E-2: `integrity.valid`, `invalid_rate`, per-question error census; M4 print-before-score).
**Acceptance:** `report["integrity"]` present with valid/threshold/rates/census/checks; `report["leg_mix"]`, `report["pool_size"]`, `report["evidence"]`, `latency_ms.ingest` aggregates present; `_print_summary` prints integrity first; golden-shape test updated to the new contract.
**Files:**
- Create: `tools/longmem_eval/errors.py` (taxonomy + `classify_eval_error` + `census_classes`)
- Modify: `tools/longmem_eval/run.py` (`_run_one` valid/error_classes; `_print_summary` reorder; `--integrity-threshold`/`--integrity-justification` CLI)
- Modify: `tools/longmem_eval/report.py` (`build_report` aggregates + integrity)
- Test: `tests/test_longmem_runner.py`, new `tests/test_longmem_errors.py`

**Step 1 (test):** `tests/test_longmem_errors.py::test_classify_eval_error` — table-driven: HTTP 401/402/403→`fatal`; 400/404/422→`fatal_config`; 429/500/503→`transient`; `requests.ConnectionError`/`TimeoutError`→`transient`; `KeyError` on body→`parse`; everything else→`unknown`; site prefix present. `test_integrity_block_and_error_census` — exploding reader (existing pattern): `integrity.valid is False`, `invalid_rate == 1.0`, `error_census` contains `reader:transient`-classified class, `checks` includes the census entry. `test_integrity_prints_before_score` — `capsys` on `_print_summary` with a failing run: "INTEGRITY"/"error census" appears before "accuracy".
**Step 2 (implement errors.py):** taxonomy frozensets mirroring P2's contract (D6); `classify_eval_error(exc, *, site)`; `census_classes(errors)` counter helper.
**Step 3 (implement run.py):** per-outcome `valid` (`completed and n_ingest_errors == 0`), `error_classes`; failures gain `error_class`; `_print_summary` prints `report["integrity"]` first; CLI flags.
**Step 4 (implement report.py):** integrity/leg_mix/pool_size/evidence/latency.ingest aggregation (D1–D5); update `test_outcomes_to_report_golden_shape` exact key-set + outcome projection to the new contract.
**Step 5 (verify):** full pytest; **Step 6:** commit `feat(1527): integrity block, error census, leg-mix/pool/evidence/cost aggregates`.

### Task 6: Dataset recall-semantics audit + publication gate

**Intent:** no `turn_recall`/`evidence_recall` number leaves the harness until the dataset semantics it stands on are verified against the paper (E2E-3 Precondition 2).
**Acceptance:** `methodology.dataset_semantics_audit` present on every report; `build_report` raises `ValueError` without it; `retrieval.*_paper@k` keys present; the @slow real-S-split audit test matches the measured baseline (or the test is updated to the new census with a review note).
**Files:**
- Create: `tools/longmem_eval/dataset_audit.py`
- Modify: `tools/longmem_eval/run.py` (`run_evaluation` computes audit; `outcomes_to_report` passes it)
- Modify: `tools/longmem_eval/report.py` (`build_report` requires `dataset_semantics_audit`; paper-aligned aggregates; `recall_definition` methodology text)
- Test: `tests/test_longmem_runner.py`, new `tests/test_dataset_audit.py`

**Step 1 (test):** `tests/test_dataset_audit.py::test_audit_on_mini` — 5 instances, 4 with `has_answer` turns, 1 abstention evidence-absent, 0 `answer_turn` fields, `answer_session_ids ⊆ haystack_session_ids` (4/4 non-empty), fixture divergence recorded. `test_build_report_requires_audit` — `build_report` without the audit arg raises `ValueError`. `test_paper_aligned_aggregates_exclude_abs` — feed outcomes incl. an `_abs` outcome with recall 0.0: `session_recall_paper@k` excludes it, legacy `session_recall@k` includes it. @slow: `test_audit_on_real_s_split` — loads the cached real dataset (skip if absent): asserts the D11 baseline numbers verbatim.
**Step 2 (implement dataset_audit.py):** census per D10 (fields/coverage/consistency/roles/abstentions/divergences/verdict); `semantics_baseline()` for the test.
**Step 3 (implement run.py/report.py):** `run_evaluation` computes `audit_dataset(instances)` once, threads through; `build_report(..., dataset_semantics_audit=...)` required; paper aggregates over non-`_abs` outcomes; methodology `recall_definition` updated to state both definitions + the exclusion rule.
**Step 4 (verify):** pytest incl. @slow against the cached dataset; **Step 5:** commit `feat(1527): dataset recall-semantics audit + publication gate`.

### Task 7: Docs + full verification

**Intent:** the harness is operable and the report contract is documented for downstream consumers (M8, the run protocol).
**Acceptance:** README documents the new flags/keys/audit; full test suite green; report example regenerated.
**Files:**
- Modify: `tools/longmem_eval/README.md`
- Modify: `tests/test_longmem_runner.py` (any final pins)
- Verify: `tools/longmem_eval/report.py` docstring + module docstrings updated (report contract)

**Step 1:** update `tools/longmem_eval/README.md` — new flags (`--integrity-threshold`, `--integrity-justification`), checkpoint fingerprint/flock semantics, report key additions, the audit gate, the Python guard.
**Step 2:** `uv run pytest tests/test_longmem_runner.py tests/test_longmem_errors.py tests/test_dataset_audit.py -v` + the concurrency suite → all green.
**Step 3:** regenerate a mock report to eyeball the new shape (`python -m tools.longmem_eval.run --data tests/fixtures/longmemeval_mini.json --limit 5 --mock`).
**Step 4:** commit `docs(1527): longmem_eval README + report contract`.

---

## Tests

### Verification Plan (test-routing, complexity: UX n/a / Architecture high / Ontology low / Accessibility n/a)

| Layer | Depth | Applies | Notes |
|---|---|---|---|
| Unit | full | ✅ | taxonomy, fingerprint compare, audit census, aggregates |
| Integration | full | ✅ | checkpoint flock (2-process), resume semantics, CLI smoke, golden shape |
| E2E smoke | — | ⛔ not in CI | the 500-Q E2E-2/E2E-3 run is @slow, gated on dataset+keys (run protocol steps 3/5) |
| UX / content / config / research domains | — | skipped | no UI, no content pipeline, no config surface, no external research domain |

Surface→test-layer mapping: see Integration Surface Map above. Every M7-owned surface (11/14/20/22/27) has ≥1 integration+unit test. No DB-migration surface, no SQL, no RLS — pgTAP/DB-layer not applicable.

### Test list (new/updated)

`tests/test_longmem_runner.py`:
- `test_python_guard_refuses_lt_312` (Task 1)
- `test_report_methodology_env_fields` (Task 1)
- `test_checkpoint_fingerprint_refuses_stale_resume` / `_legacy_refused` / `_matching_resumes` (Task 2)
- `test_checkpoint_two_processes_no_lost_updates` (Task 3)
- `test_retrieval_leg_mix_and_evidence_counts`, `test_outcome_instrumentation_fields` (Task 4)
- `test_integrity_block_and_error_census`, `test_integrity_prints_before_score` (Task 5)
- updated `test_outcomes_to_report_golden_shape` (new key contract — **intentional contract change**, pinned as the M1-regression guard)
- updated `test_checkpoint_resume_skips_completed_questions` (fingerprint key asserted)

`tests/test_longmem_errors.py` (new): `test_classify_eval_error` table (Task 5).

`tests/test_dataset_audit.py` (new): `test_audit_on_mini`, `test_build_report_requires_audit`, `test_paper_aligned_aggregates_exclude_abs`, `@slow test_audit_on_real_s_split` (Task 6).

`tortoise/shared_state/tests/test_concurrency.py`: extend for `flock_exclusive` (Task 3).

**Negative-case ownership (05-detailed-e2e):** corrupt checkpoint → fingerprint mismatch → clear abort (`test_checkpoint_fingerprint_legacy_refused`); workers race → no lost updates (`test_checkpoint_two_processes_no_lost_updates`); true-abstention evidence-absent → excluded from vacuity denominator (`test_audit_on_mini` + D4 vacuity logic).

---

## Cross-lane interfaces

| Lane | Interface | Contract |
|---|---|---|
| **P2 (#1530)** | `tortoise/model_adapters.py::LlmErrorClass / classify_llm_error / is_transient` | M7's `classify_eval_error` delegates to it when present; until P2 lands, local frozensets with identical semantics (documented fallback, not a fork). **Merge order:** either order works (fallback is explicit). |
| **M6** | evidence N/A-not-0.0 semantics (`retrieve.py`/`report.py` empty-denominator) | M7 computes vacuity over evidence-bearing questions only and does **not** implement N/A itself. ⛔ Gate 2 below. |
| **M8** | report dict shape (stats/deltas on top of M7's per-question + integrity) | M7 extends the report contract; M8 consumes `integrity`/`failures`/per-question outcomes. Golden-shape test is the shared pin. |
| **M2** | pre-flight | fingerprint excludes keys (env) — routing identity is via model ids; M2's fatal-4xx classes feed the same census taxonomy (`fatal`). |
| **#1414 parity battery** | `battery/parity/runner.py` unchanged-check | compares methodology hashes only; additive report keys are safe (verified). Golden-shape test update must land in the same PR as the schema change. |
| **M1 (#1522)** | `outcomes_to_report` return contract | M7 extends the projection; the golden test stays the M1-regression guard with the new key set. |
| **E2E-1/E2E-2/E2E-3** | leg-mix never-null; integrity.valid; evidence non-vacuous + audit | M7's deliverables are the assertion targets of E2E-1 (leg-mix), E2E-2 (integrity/report/census), E2E-3 (audit + vacuity). |
| **Run protocol** | step 3 pilot / step 5 500 | integrity block readable (step 3) and `integrity.valid=true` gate (step 5) ride on this issue landing first. |

---

## ⛔ Conditional-gate notes

**Gate 1 — No ontology / architecture / new-field change needed.** All M7 fields are **eval-instrumentation only**: `match_source` (existing engine field), `has_answer` (existing eval-instrumentation property, M6's lane), pool size (queried, not stored), and every new field lives in the report/checkpoint JSON. **No new kinds, edge types, expansion packs, or Point properties.** If a later request asks to persist per-run metrics *into the graph* for cross-run comparison, that requires a separate ontology proposal — out of scope here.

**Gate 2 — No `turn_recall`/`evidence_recall` published before the dataset-semantics audit (E2E-3 Precondition 2, review-gate P2).** Enforced **by construction** in Task 6: `build_report` raises without `dataset_semantics_audit`; `run_evaluation` always computes it. No opt-out flag. The audit's `verdict` flips to `"not-trusted"` and the recall keys serialize to `null` if the live census diverges from the recorded semantics (e.g., a HF dataset refresh changes field presence or consistency) — the report then contains **no** recall numbers until re-audited.

**Gate 3 — M6 ordering:** the report's `evidence.vacuity_rate` and `evidence_recall` N/A behavior assume M6's N/A-not-0.0 semantics. If M6 lands **after** M7, M7's vacuity section must be merged with `retrieve.py`/`report.py`'s pre-M6 behavior (forced 0.0 on empty denominators still excluded from vacuity by the `evidence_written > 0` filter — no incorrect claim of N/A). Flag for the merge-order coordinator; the audit records the semantics as of the run.

**Gate 4 — Golden-shape contract change is intentional.** `test_outcomes_to_report_golden_shape`'s exact key-set assertion changes in Task 5 — this is a planned report-contract extension (M7 deliverable), not a regression. Code-review must accept the new pinned shape; the #1414 parity battery's unchanged-check (hash-based) must stay green in the same CI run.

**Gate 5 — Real-run publication:** `turn_recall`/`evidence_recall` numbers from the pilot/500 runs (run protocol steps 3/5) may only be *published* (shared as results) after the audit record exists in the report with `verdict: trusted-as-documented-variant` and the paper-aligned `_paper@k` keys are reported alongside the legacy ones.

---

## Open questions

1. **Legacy recall aggregates:** keep `session_recall@k`/`turn_recall@k` (non-paper, incl. `_abs`) forever for back-compat, or drop once the V3 baseline lands? **Recommendation:** keep through V3 (the V3 baseline is the comparison point; its numbers use the legacy keys), revisit at V5. No code impact now — both key families coexist.
2. **Assistant-role evidence (54 turns):** include in the `turn_recall` denominator (current behavior) or exclude to match the paper's user-turn corpus? **Recommendation:** keep including (the v2 leg's evidence points have no role to filter by anyway); record the residual divergence in the audit. A user-role-only turn denominator is a follow-up if turn_recall becomes decision-critical.
3. **`answer_turn` field:** verified absent from the cleaned S split (the paper's "answer turn" == `has_answer` turn). If a future split refresh adds the field, the audit records it and the reconciliation rule is: `has_answer` turns remain the canonical turn-level ground truth unless the refresh's README changes that — flagged for re-audit rather than silent adoption.
4. **`invalid_rate` threshold:** default 0.0 with `--integrity-threshold` override + recorded justification. Should the threshold instead be dataset-split-dependent (e.g., M split's larger corpus → higher transient-failure risk)? **Recommendation:** keep 0.0; the run protocol's retry-then-fix (M4) already keeps mechanical failures near zero, and a justified override is the escape hatch.
5. **`workers` in the fingerprint:** excluded (per-question isolation makes results workers-invariant). Confirm this stays true once M8's stats (deltas/CIs) land — if any aggregate becomes order-dependent, `workers` must join the fingerprint.
6. **Cross-process checkpoint sharing semantics:** out of contract to share one checkpoint across *different* runs (configs) — fingerprint mismatch aborts. Sharing the same config across disjoint subsets (resume-partition) is supported by the merge; is that a real workflow the run protocol needs, or just defensive? **Recommendation:** defensive; no protocol change.

---

*Status: draft — awaiting plan-review gate (writing-plans workflow step 5) before execution.*
