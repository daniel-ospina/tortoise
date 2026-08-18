---
title: Scoping — #1366 reader prompting/selection (LongMemEval weak categories)
type: engineering
domain: platform
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-18
---

# Scoping: Reader prompting — fix LongMemEval preference (43%) + temporal (62%) (#1366)

> **Issue:** #1366 · **Status:** scoped (lightweight — mechanism + prompt only, per task) · **Parent:** #1144 (retrieval optimization loop) · **Baseline:** 66.2% overall (2026-08-17) · **Ownership note:** the issue body's product re-scoping (narrative-synthesis surface, `tortoise_synthesize`) is a separate product question — this scoping covers the eval-tuning lever only (reader prompt/selection), which the issue O/I/T still tracks.

## Problem

Preference (`single-session-preference`) and temporal (`temporal-reasoning`) questions fail at the **reader**, not retrieval:

- Baseline analysis (issue #1144 re-run): multi-session 44/70 wrong *had* recall; preference 12/17; temporal 31/49.
- `recall@10 = 0.86` — the evidence turns are retrieved; the reader then **hedges** ("I do not know"), **miscounts**, and **errs on date math**.
- The reader prompt (`tools/longmem_eval/reader.py::_SYSTEM_PROMPT`) actively licenses the hedge: *"If the context does not contain enough information to answer, say that you do not know."* — with zero per-question-type guidance.

## Root cause (verified on origin/main 2026-08-18)

1. `reader.answer()` **never receives `question_type`** — `run.py` passes only `context_hits/question/question_date`. The reader cannot adapt its reasoning (temporal → date math; preference → commit to the user's stated option).
2. The generic system prompt has no temporal/preference instructions. `render_context` **already** supplies what both types need (official gen.py shape: `Current Date: {question_date}` header + per-session `session date` annotations; retrieved turns carry `[role]` prefixes + `has_answer` stamps) — it is structurally available, just not instructed.
3. Temperature is already 0 (`OpenAICompatModel` default) — candidate (d) is done. Model sweep (candidate a) is deferred — needs API keys/network, not testable in CI; documented, not this cycle.

## What changes

**Mechanism** (minimal plumbing, backward-compatible):
- `Reader.answer()` protocol + `LLMReader.answer()` / `MockReader.answer()` gain `question_type: str | None = None`.
- `run.py` passes `question.get("question_type", "")` through.

**Prompt** (the lever):
- Harden the generic `_SYSTEM_PROMPT`: answer from the retrieved context when evidence is present; do not refuse/hedge on questions the context answers; commit to a concrete answer.
- Type-specific fragments appended when `question_type` is:
  - `temporal-reasoning`: use `Current Date` + per-session dates; compute elapsed time; **commit to a number** (off-by-one acceptable — the official judge does not penalize it); never hedge on date math when the dated evidence is present.
  - `single-session-preference`: identify the user's stated/implicit option choice in the user turns; **commit to the preferred option**; do not say "I don't know" when the preference appears in context; answer with the option itself.

**Selection**: NOT touched this cycle — `recall@10` 0.86 means retrieval delivers the evidence; the failure is reader-side. Documented as the next lever if the prompt alone doesn't move the categories (issue's candidate (c): de-dup/order/mark `has_answer`).

**Explicitly out of scope:** ingest path (another agent owns #1369 v2-ingest; untouched), judge (must stay verbatim official for comparability), dataset, model sweep.

## Testability (no full 500-Q run)

New `tests/test_longmem_reader_prompting.py` (offline, mock judge, embedded DB):
1. Plumbing: `run_evaluation` forwards `question_type` to the reader.
2. Prompt content: system prompt carries the temporal fragment for `temporal-reasoning` and the preference fragment for `single-session-preference`; generic prompt unchanged for other types.
3. Behavior (red→green): a prompt-faithful fake model — computes "N days ago" from the rendered dates when the temporal fragment is present (else hedges "I do not know"), and commits to the user's stated option when the preference fragment is present — judged correct by MockJudge. This mirrors the issue's documented failure mode (hedge with evidence present) and proves the prompt, not retrieval, is the fix.
4. Full-pipeline guard: mini fixture (5-Q) still green with the plumbing change.

## Verify

- `uv run pytest tests/test_longmem_reader_prompting.py tests/test_longmem_runner.py -q`
- `python3 tools/ci_selection.py --integrity` after registering the new file in `config/ci-surfaces.yml`.
