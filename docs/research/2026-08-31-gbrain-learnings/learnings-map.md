---
title: "gbrain Learnings Map — adopt/adapt/skip verdicts + licensing gate (#2080 W1)"
type: synthesis
issue: "#2096"
date: 2026-09-01
created: 2026-09-01
domain: product
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-memory, tortoise-evals
aboutObjects: tortoise-write-path-eval, tortoise-volunteering-reflex, tortoise-why-recall, tortoise-longmemeval, tortoise-ask, tortoise-search
---

# gbrain Learnings Map — adopt/adapt/skip verdicts + licensing gate (epic #2080, W1)

> **Epic:** #2080 (complex, epistemic-team) — "adopt gbrain's measurable memory
> practices — write-path evals, EP-scored volunteering-memory reflex, opt-in
> auto-ingestion QUALITY"
> **Issue:** #2096 (W1, docs) — formalizes the epic's learnings map into a
> settled, auditable baseline: one verdict per gbrain practice, the licensing
> gate recorded, and the durable-vs-marketing separation made explicit.
> **Journey:** J8 (audit learnings map + licensing gate) — exit state:
> licensing question closed; adopt/adapt/skip trail auditable; baseline
> settled for future memory work.
> **Consumes:** epic #2080 body (learnings map, 5 numbered items) ·
> `docs/research/2026-08-31-gbrain-learnings/research-brief.md` ·
> `raw-notes.md` (2026-08-31) · plan
> `docs/planning/2026-09-01-2080-gbrain-plan.md` (§2.7 W1 row, J8 §1.2)
> **Produces:** the verdict baseline that W2-a (#2097) and the W7
> `comparison-systems.md` authoring consume (W7 consumes this doc's verdicts
> for its mechanism rows; W2-a's schema source is the research brief + plan
> DM-3/4/5 — this doc is a confirming cross-reference, not a structural source).

---

## 1. Purpose and scope

Every future memory decision in the epic should start from a settled,
auditable baseline: **what does gbrain actually prove, what do we adopt as-is,
what do we adapt to Tortoise's stack, and what do we skip?** This doc fixes
that baseline with one row per gbrain practice, a verdict, a rationale, and
evidence citations into the committed research (`research-brief.md` and
`raw-notes.md`).

**Boundaries (inherited from the epic, do not re-litigate):**

1. gbrain's **onboarding / UX patterns are SKIPPED entirely** — #1976 governs
   W6 toggle placement; gbrain is a single-user CLI with no onboarding
   precedent and contributes nothing new (research-brief §UX Pattern Research:
   "gbrain has no onboarding surface precedent … #1976's
   disclosure-checkpoint precedent remains the governing source — gbrain
   contributes nothing new here (SKIP)").
2. gbrain's **codex-fragment seam is SKIPPED** — no Tortoise codex
   integration exists or is planned; only the anti-gaming lesson is kept
   (preamble slugs deliberately not counted as injections — research-brief
   §UX Pattern Research / §W3 SKIP row).
3. **No gbrain runtime dependency** — gbrain is not on npm (the npm `gbrain`
   is an unrelated GPU JS library, v1.3.1 stormcolor); a git-pinned SHA is
   the only install path and is not recommended (research-brief §Licensing,
   raw-notes 09:00Z).
4. **No-new-tool constraint** — every adaptation lands in existing Tortoise
   surfaces (ask/analyze/search/MCP). The W4 fit audit concluded all four
   W4 context types are fillable in-place; a genuinely new surface requires
   Daniel's sign-off and is out of scope (research-brief §W4 fit audit, plan
   §2.3).

**Verdict rubric (how each verdict is assigned):**

| Verdict | Meaning | Applies to |
|---|---|---|
| **ADOPT** | Port the practice as-is (shape, discipline, or machinery); reimplementation of MIT ideas carries no license obligation | W2 eval runner, W3 harness schemas, W7 benchmark machinery |
| **ADAPT** | Port the idea with Tortoise-specific changes (EP scoring instead of regex arms, point-level survival instead of page-level, existing surfaces instead of new tools) | reflex, dream write-path, write verb, targets |
| **SKIP** | Not ported — boundary or no fit (onboarding/UX → #1976; codex seam; runtime dependency) | W6, codex seam, vendoring |

Two cross-cutting constraints sharpen the rubric:

- **Benchmark-first (2026-09-01, Daniel, plan gate 6):** NO preset quality
  bar. W2 runs the planted-gold benchmark, publishes the baseline, compares
  directionally with competitors (gbrain 88.1% / Mem0 42.9% / Supermemory
  41.5%), THEN sets targets from data. The epic's literal ≥ 80% write-path
  survival is a hypothesis to validate, not a pre-set gate. This doc records
  no numeric adoption threshold — the W2 targets are data-set, and the
  rubric below reflects that (see verdict on the Cat 35 port).
- **No-new-tool (plan §2.3, epic constraint):** W4 enrichment is in-place
  across ask/analyze/search/MCP. Any adaptation that would need a new
  surface is out of scope by construction.

---

## 2. The learnings map — verdicts per practice

The epic body's learnings map (5 numbered items, order of portability) is the
source material; the research brief's Adopt/Adapt/Skip map per workstream is
the verdict table. **Every row has a verdict — zero undecided practices.**

### 2.1 Verdict table (all rows — the J8 audit surface)

| # | gbrain practice | Verdict | Workstream | Rationale (summary) | Evidence (research-brief / raw-notes) |
|---|---|---|---|---|---|
| 1 | **Cat 35 write-path eval** (planted-gold salient-unit survival) | **ADOPT** | W2 | The write path is where memory systems die and nobody else measures it. Cat 35's fixture/gold/baseline machinery (verbatim anchors, distractors, hazards, sealed gold, `fixtures_hash`, judge-blindness, mechanical checks authoritative, cost-bounded runner, fix-wave protocol) ports wholesale. **Targets are benchmark-first — no preset bar.** Adapt only the unit of survival (point-level + REPHRASE-linked, per A1) and the lanes (session→graph write-back instead of pages). | Brief §Strategy 1, §W2 rows, §Adversarial "Cat 35 61.5%→88.1%"; raw-notes 10:10Z (runner + judges), 10:30Z (result verification), 11:50Z (corpus provenance); line-ref: Cat 35 gold shape / mechanical checks / judge blindness |
| 2 | **BrainBench Cat 34 harness** (know-to-ask / false-fire / push / write-back / continuity / isolation) | **ADOPT** | W3 | Fixture/gold/baseline schema shapes + sealed-gold discipline + the know-to-ask/false-fire anti-gaming pair + push precision/recall under a pointer budget + source-isolation gate at zero port directly (MIT ideas). Harness seams = Tortoise's REAL integration points (MCP tools, claude-hooks, session_import) — never abstract contracts (the codex cautionary tale). | Brief §W3 rows, §Workflow Pattern items 1–6; raw-notes 09:45Z (Cat 34 harness), 10:00Z (runner + receipts), 10:40Z (result verification); line-ref: know-to-ask + false-fire, write-back fidelity, claude-code seam |
| 3 | **Reflex EP-scoring** (salience → resolve → gate → budget → suppress) | **ADAPT** | W3–W4 | The skeleton ports directly; the salience DECISION becomes EP support+contentiousness scoring instead of gbrain's hand-written arm-confidence table (alias 0.9 / title 0.8 / surname 0.72 / slug-suffix 0.6). Pointer budget (3/5) + re-mention suppression + fail-open + ≤300 ms envelope kept. **No new tool** — the reflex delivers through existing surfaces and the `/v1/context`-shaped contract. | Brief §W3/W4 rows, §Tech Stack "Reflex" row; raw-notes 09:15Z (reflex + resolver arms); line-ref: pointer budget, volunteer gate, entity salience budgets |
| 4 | **Dream write-path** (triage → synthesis, facts fence, receipts) | **ADAPT** | W2/W5 | Tortoise already dreams (EP stabilization); the gap is triage-with-verified-segment rescue (anti-hallucination gate: only admit sub-gate content the judge's own quotes verify), content-hash dedup keys, and provenance stamping on every point. Ports into the existing session→graph write-back. | Brief §W5 row, §Tech Stack "Dream write path" row; raw-notes 09:25Z (dream cycle, extract receipts), 11:40Z (consolidation theory) |
| 5 | **MEMORY_VERBS_v1 write verb** (frozen, `protocol_version`, enumerated error codes) | **ADAPT** | W5 | Port the frozen version-stamped write verb shape (protocol_version REQUIRED, provenance REQUIRED, kind enum, status branch, enumerated error codes + suggestion) onto Tortoise's MCP write tools and the session capture path — same contract for harness and ingestion. | Brief §W5 row, §Tech Stack "MEMORY_VERBS_v1" row; raw-notes 09:35Z (verbs.ts); plan §4.4 DM-2 |
| 6 | **Receipts / baselines / CI gate** (sealed gold, receipts naming commit, committed baseline that can fail, corpus-bless, justification-to-bless) | **ADOPT** | W7 | The durable asset. Validated receipts (run_status, verdict, failure_origin, exact commit, hash, judge pin); committed baseline with `--compare` gate that CAN fail; `justification` REQUIRED to bless a regression; corpus-bless mode; config mismatch ⇒ inconclusive, never a rubber-stamp; "without that discipline the gate degrades to rubber-stamp within months" (their own README). | Brief §Workflow Pattern items 2–6, §W7 row; raw-notes 10:00Z (receipt schema), 10:40Z (first-run vs baseline cross-check); line-ref: CI gate, receipt schema, baseline refresh discipline |
| 7 | **comparison-systems.md rules** (neutral cited tables, mechanism-named rows, no win claims on benchmarks not run, metric-mixing flagged) | **ADOPT** | W7 | Adopt wholesale as the W7 content contract; a separate issue authors the file and consumes THIS doc's verdicts for its mechanism rows. Tortoise's own `dataset_audit.py` (4 documented LongMemEval divergences) must appear as labeled variants in every published report. | Brief §Workflow Pattern item 8, §W7 row; raw-notes 10:50Z; plan §6.7 DM-10 |
| 8 | **Errata policy** (annotate, never silently edit history; publish bad numbers on purpose) | **ADOPT** | W7 | Errors corrected with annotations, historical numbers preserved; the fix-wave protocol (publish the BAD number → name failure classes → fix → re-run same frozen corpus + pinned judge) is the epic's demonstration, not optional polish. | Brief §Workflow Pattern items 7, §Adversarial net; raw-notes 11:40Z (erratum verified verbatim), 10:40Z |
| 9 | **"Tortoise already has the harder half"** (learnings-map item 5) | **ADOPT (as premise)** | all | Tortoise has EP property tests P1–P10/G1–G8/R1–R8/B0–B2, the Tier-1/2/3 reasoning battery, and a complete LongMemEval runner — but only n=1 smoke reports are committed. The gap is published runs + sealed-key discipline, not machinery. This verdict is the epic's premise: adopt the benchmark-first posture, not new machinery. | Brief §Strategy 5, §W1 row; raw-notes 11:10Z (LME runner + n=1 smoke reports); plan §W7 |

### 2.2 Verdict → workstream handoff

| Verdict | Lands in | Consumed by |
|---|---|---|
| Cat 35 ADOPT | W2 write-path eval | #2097 (W2-a) — schema source is research brief + plan DM-3/4/5; this doc is a confirming cross-reference |
| Cat 34 ADOPT | W3 harness + why-suite | W3 child issues |
| Reflex ADAPT | W3–W4 | W4 enrichment + `/v1/context` delivery |
| Dream write-path ADAPT | W2/W5 | W5 write-path quality |
| MEMORY_VERBS_v1 ADAPT | W5 | W5 write verb (DM-2) |
| Receipts/baselines/CI ADOPT | W7 | W7 publication (DM-11) |
| comparison-systems.md ADOPT | W7 | W7 comparison table (DM-10) — **consumes this doc's verdicts** |
| Errata ADOPT | W7 | W7 errata discipline |
| Harder-half premise ADOPT | epic-wide | sequencing mandate (W2/W3 first) |

---

## 3. Licensing gate (COMPLETED 2026-08-31, re-confirmed 2026-09-01)

**Status: MIT/MIT — CLEAN.** No copyleft exposure. Gate re-confirmed at
raw-notes 11:50Z and research-brief §Licensing (re-confirmed 09-01).

1. **License + copyright:** `garrytan/gbrain` and `garrytan/gbrain-evals`
   are both **MIT, Copyright (c) 2026 Garry Tan** (LICENSE, 1-line MIT in
   both repos — raw-notes 09:00Z / 11:50Z).
2. **Dependency tree:** gbrain's direct dependency tree (38 deps) is all
   standard permissive (MIT/Apache): pglite, ai-sdk, express, MCP SDK,
   openai, zod, etc. No AGPL/GPL exposure (epic body §Licensing gate).
3. **Eval corpora:** gbrain-evals corpora are "fully made up and free to
   redistribute" (README, per raw-notes 11:50Z); vendored PrecisionMemBench
   artifacts are MIT (tenurehq) with ATTRIBUTION.md (raw-notes 11:50Z).
4. **Attribution obligation (named precisely):** IF we vendor actual code or
   corpus files (vs reimplementing ideas), we MUST (a) preserve the MIT
   copyright notice + license text (attribution) and (b) not imply
   endorsement. Reimplementing the IDEAS (fixture schema shape, gold
   conventions, metric formulas, receipts discipline) carries **no
   obligation** (raw-notes 11:50Z; research-brief §Licensing).
5. **No-vendoring statement (recorded):** Tortoise does NOT vendor gbrain or
   gbrain-evals code or corpus files. **Recommendation: reimplement ideas,
   do not vendor code** — the ideas are small (schemas, metrics) and
   Tortoise's stack (Python/FalkorDB vs TS/PGLite) makes vendoring pointless
   (research-brief §Licensing). Any corpus file ever copied verbatim (e.g. a
   Cat-35-style transcript corpus) MUST carry the MIT notice — none are
   planned. Verified in the repo tree: no gbrain/gbrain-evals source or
   corpus artifacts are committed (J8 exit condition, repo-tree check).
6. **Runtime dependency caution:** gbrain is NOT on npm (the npm `gbrain` is
   an unrelated GPU JS library, v1.3.1, stormcolor — raw-notes 09:00Z); a
   git-pinned SHA is the only install path if a runtime dependency ever
   appears — **not recommended, no runtime dependency in this epic**
   (research-brief §Licensing; epic boundary 3).

---

## 4. Durable vs marketing — auditable separation

The J8 audit must be able to tell apart what survives gbrain's own errata
from what does not. This section fixes that separation.

**Marketing (flagged, do not import):**

- **The 97.6% LongMemEval "SOTA" headline is the weak part** — it was the
  looser **any-hit R@5**, walked back via erratum (2026-08-31); the official
  `recall_all@5` re-measurement was still pending at read time. For the 133
  multi-session questions any-hit is strictly looser ("the most inflated"
  rows per their own erratum). Tortoise's W7 discipline: official
  `recall_all@5` semantics (NOT any-hit) + our own 4 documented divergences
  labeled as variants, never mixed (brief §Strategy 4, §Adversarial net;
  raw-notes 11:40Z — the erratum verified verbatim against the official
  evaluator).
- **"0 failures" Cat 34 framing** — true for the CURRENT committed CI
  baseline (0.000 kta, 0.000 false-fire) but the FIRST published run was
  0.150 know-to-ask failure; the zeros are post-fix-wave. Also, push RECALL
  is not 1.0 (0.906 OpenClaw / 0.552 Codex) — the README's "push precision
  1.0" omits recall (brief §Adversarial). Tortoise's takeaway: publish
  receipts, not headlines; cite the exact commit; name the metric variant.
- **"Graph +30pts precision"** — plausible but loosely sourced (in-house
  240-page benchmark, mechanism note in comparison-systems.md, NOT a public
  head-to-head); cite the exact benchmark doc before any Tortoise use (brief
  §Adversarial).

**Durable (the asset worth copying):**

- **The write-path measurement pattern** (Cat 35) — first of its kind, and
  the 61.5% → fix-wave → 88.1% demonstration is load-bearing ("benchmarks
  that bite back" is TRUE and durable) (brief §Adversarial, §Strategy 1).
- **The benchmark machinery** — sealed gold, `fixtures_hash`, receipts
  naming the commit, hermetic CI, corpus-bless mode, publish-bad-numbers-
  on-purpose, justification-to-bless (brief §Strategy 4, §Workflow Pattern).
- **The self-audit posture** — 35-agent audit, 239 findings / 17 critical,
  errata instead of silent edits: "the audit is the most credible artifact
  in the repos" (brief §Adversarial; raw-notes 10:20Z).
- **The reflex's deterministic design** — zero-LLM, fail-open, pointer
  budget, re-mention suppression (brief §Strategy 2).

---

## 5. Anti-gaming / anti-pattern lessons kept even where the seam is skipped

- **Preamble slugs don't count** (codex-fragment seam): even though the
  codex seam itself is SKIPPED (no Tortoise codex integration), the
  anti-gaming rule carries — injected preamble content must never be counted
  as a voluntary injection (brief §UX Pattern Research; plan §2.2/§2.6).
- **Seam-vs-contract drift**: contract rows do NOT measure third-party
  harness behavior — the W3 harness MUST grade real Tortoise seams (MCP
  tools, claude-hooks, session_import), never an abstract contract (brief
  §Workflow Pattern; raw-notes 09:45Z).
- **First-run numbers will be bad**: gbrain's 0.150 → 0.000 and 61.5% →
  88.1% are the model; the first published Tortoise write-path number is
  expected bad and must be published anyway (brief §Strategy 2/4; plan
  §2.1).

---

## 6. Consumers and cross-references

- **W7 `comparison-systems.md` (a separate issue)** consumes this doc's
  verdicts for its mechanism rows — the W7 authoring MUST read rows 6–9
  above before drafting (plan §6.7 DM-10, §2.6).
- **W2-a (#2097)** uses the research brief + plan DM-3/4/5 as its schema
  source; this doc is a confirming cross-reference for the Cat 35 ADOPT
  verdict (row 1), not a structural source (issue #2096 Fractal Fields).
- **W3/W4/W5 child issues** reference rows 2–5 for their verdict baselines.
- **J8 audit (epic plan §1.2)** — this doc is the J8 audit surface: open
  learnings map → check per-workstream verdicts → confirm MIT/MIT licensing
  note + attribution obligations → verify no vendoring → exit with the
  baseline settled.

---

## 7. Verification (this doc's exit condition)

- ✅ Doc at planned path (`docs/research/2026-08-31-gbrain-learnings/learnings-map.md`).
- ✅ Every gbrain practice has an adopt/adapt/skip verdict + rationale
  (9/9 rows in §2.1; onboarding/UX + codex seam SKIPPED in §1 boundaries;
  runtime dependency excluded) — **zero undecided practices**.
- ✅ Licensing gate recorded: MIT/MIT, copyright named (Copyright 2026 Garry
  Tan), attribution obligations named, no-vendoring statement recorded
  (§3).
- ✅ "Durable vs marketing" separation explicit and auditable (§4).
- ✅ Verdict rubric reflects the benchmark-first decision (no preset bar —
  rows 1 + 9) and the no-new-tool constraint (§1 rubric).
- ✅ Repo-tree check (J8): no copied gbrain/gbrain-evals source or corpus
  artifacts in the tree; no MIT notice required because nothing is vendored
  (§3.5).
