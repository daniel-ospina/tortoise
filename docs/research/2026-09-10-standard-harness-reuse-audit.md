---
title: "Standard-harness reuse audit — do we need to invent the battery's key decisions?"
type: research
date: 2026-09-10
created: 2026-09-10
domain: engineering
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-evals, tortoise-battery, tortoise-longmemeval
aboutObjects: tortoise-write-path-eval, tortoise-why-recall
---

# Standard-harness reuse audit (epic #1402 / #1416)

**Question (owner, 2026-09-10):** for the battery's key decisions, does a
working implementation already exist (gbrain or another source) that we can
measure against and learn from — rather than reinventing the wheel?

**Method:** internal read of the in-tree harnesses + the epic's own plans
(primary), then targeted external checks on the standard benchmarks'
protocols (MemoryAgentBench repo + paper, gbrain-evals repo + docs, LongMemEval
paper/repo already grounded in `docs/research/2026-09-09-longmemeval-internals.md`).
External claims carry confidence tags; every in-repo claim was verified by
reading the file named next to it.

## Answer in one line

**Most of it already exists — including in our own tree. The pieces with no
standard implementation are calibration over retrieved memory (an open gap
per the surveys cited below — ⚠️ medium confidence, they agree but are not
primary sources) and our matched-recall control. The real problem is not
invention: the comparison leg we built to touch the standard benchmarks
never executes one of them (#2797).**

## Decision-by-decision: standard implementation → verdict

| Battery decision | Existing standard implementation | Verdict |
|---|---|---|
| **R1/R4** contradiction surfaced / defeat precision | **MemoryAgentBench Conflict Resolution (CR)** — `FactConsolidation-SH`/`-MH`, a *counterfactual edit pair* benchmark where the rewritten contradictory fact appears **after** the original fact; scored as **accuracy = `substring_exact_match`**; published multi-hop baselines are unsaturated — standard RAG/memory agents at most ~6% (paper §4), GPT-4o 28% on FactCon-MH @6K, O4-mini 80% on the same multi-hop row (single-hop @6K is 100%) [High — repo + ICLR 2026 paper (v1 & v4 tables) + OpenReview PDF] | **ADOPT as the comparable number.** Keep `surfaced-within-1-turn` as a *mechanism diagnostic* (it is already documented as not-retrieval-recall in `docs/benchmarks/comparison-systems.md` §8), but stop treating it as the family's headline. |
| **R5** belief update | **LongMemEval `knowledge-update`** (78 of 500 questions, 2 evidence sessions: old + updated) with the **official answer-check judge** — already reimplemented verbatim in this repo (`tools/longmem_eval/judge.py`, MIT, env-configurable, near-miss policy pinned strict) | **ADOPT** — this is our R5 with published GPT-4o baselines and a judge we already own. |
| **R2** coverage / recall | **LongMemEval** turn-level `has_answer` + session-level `answer_session_ids` (`recall_all@5` official semantics) and MemoryAgentBench **Accurate Retrieval (AR)** | **ADOPT for every retrieval-shaped claim.** Our `coverage-subscore` measures *deliberation content*, which is a different object — keep it, never present it as "coverage/recall" (`comparison-systems.md` §8 already draws this line). |
| **Judge protocol** (the #2780 blocker) | **LongMemEval official anscheck**: `gpt-4o-2024-08-06`, temp 0, max_tokens 10, label = `'yes' in response`. In-tree, tested. **Runnable with our existing `OPENROUTER_API_KEY`** via `TORTOISE_LME_JUDGE_MODEL=openrouter:openai/gpt-4o-2024-08-06` (the judge module documents this path). MemoryAgentBench uses a gpt-4o judge **for its LongMemEval(S\*) and InfBench-Sum subsets only** — its CR family uses `substring_exact_match` and needs no judge. gbrain-evals **reuses the official LongMemEval question-type prompts for its LongMemEval leg (with disclosed deviations: 16 output tokens, an added abstention instruction) while its in-house BrainBench suite ships its own judge** (Claude Haiku tool-use, `eval/runner/judge.ts`) — i.e. the standard leg reuses the standard judge, and a bespoke suite carries a bespoke judge (which is exactly our `r2-coverage` situation) [High — repo + gbrain-evals source] | **ADOPT for comparable legs.** This retires the premise of #2780 for those families: no new `r3-`/`r5-` rubric authoring and **no mandatory two-model judge pair**. Keep the two-model AC1/κ gate for our *bespoke* `r2-coverage` rubric — that is stricter than the standard, and defensible as such. |
| **Answer / envelope contract** (#2702) | Standard practice is a **free-text answer + judge**; MemoryAgentBench's README explicitly warns that its own `exact_match` parsing is strict and *recommends flexible parsing* when adapting it to your own pipeline [High — repo README] | **ADAPT.** Free text is the primary channel; the structured envelope stays only where a scalar is required (R3 confidence). A missing optional `position` must not fail an episode that the standard would have accepted — this is the direct cause of the 2 remaining `cal` exclusions and of #2702's 22-episode empty-position class. |
| **R3** calibration | **No standard benchmark.** 2026 surveys/position papers describe agent confidence calibration over retrieved memory as an open gap; the nearest partials are ConfidenceBench (verbalized confidence + Brier, MCQ) and AbstentionBench; LongMemEval carries an abstention overlay (30 questions) [⚠️ medium — several secondary surveys agree (arXiv 2603.07670; the 2026 agent-memory landscape surveys) but no primary/benchmark-owner source states the gap] | **ORIGINAL — keep it, and say so.** Use standard *scoring* (Brier/ECE, verbalized confidence) and adopt LongMemEval abstention as the standard abstention leg. This is the differentiator worth publishing, precisely because nothing standard exists. |
| Robustness discipline (sealed gold, pinned judge versions, receipts, mechanical-over-LLM grading hierarchy, anti-gaming) | The **gbrain-evals pattern** — already mapped in `docs/epics/1402-eval-battery/public-evals.md` and already implemented across `tests/eval/{write_path,why_suite,harness}` (sealed gold, Cat-35 no-gold-in-fixture rule, pinned judge ids, baselines + receipts) | **ALREADY ADOPTED.** No work needed; cite it as the public-defense posture. |
| Matched-recall control line (#1413/#2525) | No standard analogue (it is our control design: recall-matched subsets so a differential is auditable) | **KEEP** — genuinely ours, and the §3.2.1 contract is pre-registered. |

## What the "fully standard test" actually requires from us

The released standard runners already exist as third-party code
(LongMemEval — already wrapped in-tree; LoCoMo, MemoryArena, MemoryAgentBench,
ForgetEval-class — released repos/datasets). Our work is **integration, not
invention**: run those runners per arm behind the existing parity scaffold.

The scaffold is already there and is good: `battery/parity/runner.py` pins
dataset versions (`longmemeval-2025.3`, `locomo-v1`,
`memoryarena-hf-rev-2026.02`, `memoryagentbench-2025.4`), refuses on version
mismatch, and compares three methodology hashes (reader prompt + judge rubric
id + PROTOCOL: seed/model_pin/temperature/event_schema/tool_surface) against
the #1144 baseline record — including the `protocol_unknown` path for old
2-tuple baselines and placeholder-pinned arms.

**The missing half:** `battery/cli.py::_cmd_parity` never executes a
benchmark — it calls `run_parity(..., accuracy=0.5, samples=0)` for all four
benchmarks. Verified precisely (#2797): the constant is **inert today** — it
lands on the returned `ParityRun` but `parity_record.json` serializes only
`arm / seed / protocol_hash / protocol_unknown / benchmarks{version,
methodology_matched}`, and nothing reads `ParityRun.accuracy`, so no receipt
currently carries a fabricated number. The defect is that the parameter
invites a future consumer to read a constant as a measurement, and that the
record certifies `methodology_matched` cells for benchmarks that were never
run. Wiring the real runners is what turns the parity table from a
placeholder into the standard measurement the owner is asking for.

## Recommendation

1. **Stop planning to hand-author R3/R5 rubrics for comparable families.**
   Adopt the official judge protocol (R5/R2 judging) and FactConsolidation's
   `substring_exact_match` (R1/R4 — *no judge, no per-episode LLM cost*).
   R1/R4/R5 become measurable against published baselines **now**, with the
   key we already have.
2. **Make the parity leg real** (#2797 then the runner wiring): LongMemEval
   first (in-tree, official judge), then MemoryAgentBench (CR is the family
   that maps to our differentiator claim), then LoCoMo/MemoryArena.
3. **Keep R3 calibration as the original contribution** — with the hedge
   intact: no standard benchmark exists for it as far as the (secondary)
   sources agree, so we state the gap as "no standard benchmark found" and
   carry the standard scoring (Brier/ECE, verbalized confidence). An honest
   novelty is stronger than a bespoke number dressed as a standard one.
4. **Adapt the envelope** (#2702): free text primary, structured scalar only
   where required, missing optional fields non-fatal.

## Open questions for the owner

1. **Headline comparable:** MemoryAgentBench (CR/FactConsolidation — closest
   to our contradiction/defeat claim) or LongMemEval (broader, already
   in-tree) as the primary standard row? Both are cheap; the choice is which
   one the public claim leans on.
2. **Judge spend:** the official judge is one pinned model (gpt-4o) rather
   than our two-model pair — accept the standard's single-judge protocol for
   comparable legs while keeping the AC1/κ validation for the bespoke rubric?

## Sources

- MemoryAgentBench — `github.com/HUST-AI-HYZ/MemoryAgentBench` (README:
  four competencies AR/TTL/LRU/CR; `accuracy` → `substring_exact_match` for
  `fact_sh`/`fact_mh`); arXiv 2507.05257; ICLR 2026 (OpenReview
  `DT7JyQC3MR`). Companion: MemoryArena (ICML 2026).
- gbrain-evals — `github.com/garrytan/gbrain-evals` (BrainBench + LongMemEval
  adapters; sealed answer keys, pinned judge versions, seeded randomization);
  `gbrain.fun/docs/eval/BRAINBENCH.md`; repo-local
  `docs/research/2026-08-31-gbrain-learnings/`.
- LongMemEval — arXiv 2410.10813; repo `xiaowu0162/LongMemEval`; repo-local
  `docs/research/2026-09-09-longmemeval-internals.md`.
- Calibration gap — 2026 surveys/position papers on agentic uncertainty
  (single-source, tagged above) + ConfidenceBench / AbstentionBench.
- In-repo: `battery/parity/runner.py`, `battery/cli.py` (`_cmd_parity`),
  `tools/longmem_eval/{judge,run}.py`, `tests/eval/*`,
  `docs/benchmarks/comparison-systems.md`,
  `docs/epics/1402-eval-battery/{public-evals,03-scope,04-plan}.md`.
