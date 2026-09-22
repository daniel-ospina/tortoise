# Battery Grounding — competitor & best-practice research per task family (#1402 epics)

> Purpose: every #2291/#2292/#2284-Task-8-10/#1416 task carries an explicit
> best-practice + competitor-research grounding, drawn from the in-repo
> landscape briefs plus this 2026-09 refresh. Each task section in the plan
> docs points here. In-repo primary sources (read first):
> `docs/research/2026-08-14-agentic-eval-landscape.md`,
> `docs/research/2026-09-02-temporal-reasoning-competitors.md`,
> `docs/epics/1402-eval-battery/02-research-brief.md` (§Strategy/UX/Workflow +
> raw notes), and the gbrain brief
> (`docs/research/2026-08-31-gbrain-learnings/research-brief.md`).

## Cross-cutting principle (the epistemic-eval niche)
The market evaluates agent memory on **recall / staleness / persistence**
(mem0 "State of AI Agent Memory 2026": LoCoMo, LongMemEval, BEAM are the
comparison set; vendor guidance = third-party recall@10, persistence SLAs,
TCO) — **no standard benchmark exists for the REASONING axis** (02-research-brief
§19). That is the gap this battery owns. Vendors also warn that published
benchmarks are **not universal leaderboards** (hydradb 2026) → the battery's
[cal]/parity discipline (measured, reviewable, reproducible; never vendor
numbers, never silent re-tunes) is the direct methodological answer.

## #2291 (A4 product-semantics + EP path)
- **Best practice — measure the product, not a mimicry:** the vectorize/AMB
  manifesto (02-brief §Landscape) and MemoryArena's
  Memory-Agent-Environment loop (2602.16313; 02-brief §35) both mandate a
  FIXED ingest→retrieve→generate→judge pipeline over the system's OWN
  memory surfaces — never a reimplementation. This task family is the graph
  arm's instance of that rule (product SDK verbs only; lane-matrix audit).
- **Competitor — Zep/Graphiti (closest architecture):** temporal knowledge
  graph, bi-temporal facts, invalidation-not-deletion, "contradiction
  handling + temporal accuracy" cited as the KG-memory eval criteria
  (hydradb 2026; o-mega 2026). Tortoise's contested-surfaced / superseded-
  excluded / NAND-variance semantics (Task 4's ep_outcome terminal table)
  are the graph-epistemic counterparts of those criteria — the arm must
  express them through the PRODUCT read surface (recall_state
  contested/counter_evidence attachments) so the R1-R5 metrics measure the
  mechanism Zep-comparators claim, not a bespoke mock (02-brief §44:
  "Zep must be an arm" framing — the differential leg).
- **Eval-surface design:** LongMemEval's per-construct question style
  (contextual memory of claims/contradictions) and AMB's fixed-pipeline
  judges ground the per-construct probe split (R1..R5) and the
  decisive-outcome-only R3 semantics (judge-free objective EP read-out).

## #2292 (rubric / model / budget)
- **Judge rubric best practices (2026 refresh — all decision-relevant):**
  - *Binary (anchored yes/no) criteria are the most reliable automated
    judgments*; ordinal scales need careful design, better coarsened —
    Autorubric 2603.00077 → the R2 arm-neutral anchored-yes/no design
    (already adopted; this confirms it).
  - *Evidence scrubbing + grounding:* checklist items tied to EXPLICIT
    evidence expectations ("evidence scrubbing", grounding-based rubric
    checks) — 2601.08654 → R2's tool-stripped/arm-normalized evidence
    rendering (never tool names/edge counts/arm id).
  - *Validation must be chance-corrected, span ≥2 contrasting label
    structures, measure consistency AND bias jointly; test-retest
    reliability can MASK position-invariance* — 2606.19544 → the D1 gate's
    κ≥0.70 (chance-corrected) + AB/BA swapped-position retest + gold-anchor
    expected-"no" block (a degenerate all-yes judge fails) + the
    vocabulary/position seams; and the rationale for rejecting holistic
    pairwise better/worse/tie (low-reliability shape, 2606.29920 in-brief).
  - *Rubric validity does not transfer across models; validate empirically*
    — 2026.findings-eacl.335 → validate on REAL deliberation text from the
    ACTUAL pinned model (Task-3 probe → Task-4 validation run), never on
    synthetic probes or another model's text.
  - *κ/Krippendorff + human-labeled calibration sets + drift monitoring* —
    futureagi 2026 → the κ inter-judge leg (second judge config) + judge
    drift monitoring over the exposure.
- **Competitor:** AMB/vectorize + LongMemEval judges are the comparison
  (per-construct yes/no accuracy); no competitor publishes a validated
  deliberation rubric — a first, buyer-visible (02-brief §19 gap).

## #2284 Tasks 8-10 (executor / exposure / streams) — advance grounding
- **Executor v1 (TVDE single-session):** AMB's fixed
  ingest→retrieve→generate→judge pipeline = the exposure skeleton; the
  emission-loss-proof executor requirement (PR #2341 second-model P1) maps
  to AMB's "fixed pipeline" honesty — measured outputs only from the real
  product seam, never fabricated turns.
- **Executor v2 (streams/differential):** MemoryArena's cross-session
  learn-now-use-later loop (2602.16313) is the L4 load-bearing surfacing
  design (session-1 content absent pre-k in session-2, then surfaced) —
  in-brief canonical multi-session workflow; LoCoMo (long multi-session
  conversations) supplies the session-length/drift shape for the stream
  episodes; ForgetEval / Memora+FAMA / AMB (2026 roundup, 02-brief §20)
  supply the staleness/drift/differential probes the Tier-3 parity leg
  aligns with.
- **Differential:** Zep/Graphiti head-to-head is the strongest realistic
  comparator (02-brief §44); the D2/D4 differential cells measure the graph
  arm vs the no-memory/naive baselines under byte-identical scaffolds.

## #1416 (verdict) — advance prescope
- Buyers evaluate persistence/staleness/recall@10 and REASONING is the
  unmeasured differentiator (02-brief §19; agentmarketcap/sparkco 2026):
  the verdict must map measured deltas to buyer-visible claims (staleness,
  drift, contradiction handling) — not just internal metrics.
- Verdict thresholds should be **pre-registered + measured-provenance**
  (parity protocol hash + [cal] rows + judge ValidationRecord), never
  post-hoc: benchmark-literate buyers + reviewers treat post-hoc numbers as
  vendor claims (hydradb "not universal leaderboards"; 02-brief §54).
- Epistemic-mechanism angle: belief-propagation-over-KG + contradiction
  handling are the axes no comparator publishes — the honest-UNDEC /
  contested-non-decisive semantics (R3, #2291 Task 4) are the
  differentiator evidence the verdict cites.

## Source register (2026-09-07 sweep)
- mem0.ai/blog/state-of-ai-agent-memory-2026 — LoCoMo/LongMemEval/BEAM comparison set.
- arxiv.org/abs/2602.05665 — graph-based agent memory survey (libraries + benchmarks, taxonomy).
- o-mega.ai/articles/ai-agent-memory-in-2026 — Zep/Graphiti eval dims (recall, ingest cost, temporal behavior).
- hydradb.com/blog/knowledge-graph-memory-systems — KG-memory eval criteria + "not universal leaderboards".
- arxiv 2601.08654 — evidence scrubbing / grounding-based rubric checks.
- arxiv 2606.19544 — LLM-judge validation: chance-corrected, contrast labels, consistency+bias jointly, retest-masks-position.
- arxiv 2603.00077 (Autorubric) — binary criteria reliability.
- 2026.findings-eacl.335 — rubric design must be validated empirically; weak cross-model transfer.
- futureagi.com/blog/llm-as-judge-best-practices-2026 — κ/α + calibration sets + drift monitoring.
- In-repo: 02-research-brief (MemoryArena 2602.16313; AMB/vectorize; 2606.29920 task-rubric reliability; gbrain Cat-35), temporal-reasoning-competitors.md, agentic-eval-landscape.md.
