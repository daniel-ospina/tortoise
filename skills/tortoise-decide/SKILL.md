---
name: tortoise-decide
title: "tortoise-decide"
description: Decision engine — research a decision with the graph as a thinking aid. Multi-cycle, multi-angle, adversarial by default. Files everything to the graph so the reasoning chain is preserved forever.
type: capability
domain: capability
status: live
doc_status: live
subjects.team: epistemic-team
created: 2026-07-18
allowed-tools: read write edit bash grep find web_search web_fetch todo_write task, mcp__tortoise__tortoise_create_point, mcp__tortoise__tortoise_create_operator, mcp__tortoise__tortoise_query, mcp__tortoise__tortoise_get_point, mcp__tortoise__tortoise_check_structure, mcp__tortoise__tortoise_compute_confidence, mcp__tortoise__tortoise_set_point_baseline, mcp__tortoise__tortoise_get_confidence, mcp__tortoise__tortoise_annotate_operator, mcp__tortoise__tortoise_mitigate_operator, mcp__tortoise__tortoise_calibrate_summary
---

# tortoise:decide

Decision engine. Takes "I want to decide X" and runs the full decision workflow through the graph. The graph is the medium for reasoning — not a document, not a checklist, a thinking aid that challenges you and never forgets.

**Announce at start:** "I'm using the tortoise-decide skill to run the decision workflow."

## ⛔ HARD RULE: EP Calibration

EP calibration is MANDATORY before CONVERGE. Three primitives:
- `tortoise_set_point_baseline` — encode source credibility as Beta prior
- `tortoise_annotate_operator` — set bias/precision/consistency/directness on edges
- `tortoise_mitigate_operator` — weaken edges without full contradiction

Default T4 applies if unset. The CALIBRATE gate blocks CONVERGE if >50% of evidence points are uncalibrated.
→ Read `/skill:how-to-use-tortoise` for tier tables and annotation semantics.

Note: T0-T4 tier names are shared between two systems — operator annotation weights (how-to-use-tortoise: 0.2-1.0 multipliers) and Point Beta priors (credibility kwarg: Beta distributions). The CALIBRATE gate operates on Point priors; operator annotation is separate.

## When to Use

- Making an architecture or strategy decision with real stakes
- Evaluating options where the trade-offs are not obvious
- Building a case that needs to hold up to scrutiny months later
- Any decision where "what are we missing?" is the most important question

## Workflow

### 1. ACCEPT — Frame the decision

User states the decision topic. Unpack:
- What criteria matter for this decision?
- What assumptions are we making? File them explicitly.
- What are the options on the table?

File everything as Points in the graph. Each criterion, assumption, and option gets its own Point.

### 2. RESEARCH — Multi-angle, multi-source

For each criterion and option, run multi-angle research following the research skill's methodology:
- **canonical** — mainstream consensus, best practices
- **critical** — blind spots, limitations, failure modes
- **systems** — causal feedback loops, second-order effects
- **outlier** — fringe, emerging, contrarian perspectives
- **practitioner** — real-world experience, not just theory
- **contemporary** — last 12-24 months, what's changing now

File all findings as Points with provenance (source URL, date, author). Connect findings to the options and criteria they support or contradict via IMPL/NAND edges. → Use /skill:how-to-use-tortoise for proper edge annotation and veracity assessment.

For every evidence Point, set credibility via `tortoise_create_point` with `credibility` kwarg:
- T0 "gold" (meta-analysis): credibility="gold"
- T1 "high" (peer-reviewed): credibility="high"
- T2 "medium" (expert): credibility="medium"
- T3 "low" (anecdotal): credibility="low"
- T4 "unverified" (blog/default): credibility="unverified"
For every IMPL/NAND edge, annotate with `tortoise_annotate_operator`.
Prefer `tortoise_mitigate_operator` over NAND for counter-arguments that weaken but don't fully contradict.

### 3. CHALLENGE — Find the weak spots

After the first research pass, identify gaps:
- Which claims have no counter-arguments? File counter-arguments:
  - **Mitigation** if the counter-claim weakens but doesn't fully contradict
  - **NAND** only if the counter-claim logically contradicts
- Which assumptions are untested? Flag as needing evidence.
- Which criteria have no data? Flag as needing research.
- Which options have fewer than 3 supporting claims? They are weakly grounded.

Surface these as gap Points connected to their parent claims.

### 4. DEEPEN — Research the gaps

For each gap identified, research specifically:
- "What is the strongest argument AGAINST [claim]?"
- "What evidence supports or refutes [assumption]?"
- "What data exists for [criterion]?"

File counter-evidence. Connect with NAND edges where appropriate.

### 5. REPEAT — 2+ more cycles

Run at least 2 more cycles of Challenge → Deepen. Each cycle should:
- Strengthen weak claims with new evidence
- Add counter-arguments to untested assumptions
- Fill data gaps for criteria
- Surface new gaps discovered during deepening

Stop when: every assumption has been challenged, every option has both supporting and opposing evidence, and new research cycles produce diminishing returns (no new significant findings).

### 6. CALIBRATE — Verify EP readiness

Before converging, run the calibration gate:

1. Run `tortoise_calibrate_summary` to audit the graph
2. Fix uncalibrated evidence points (statement/observation/hypothesis): use `tortoise_set_point_baseline` or recreate with `credibility` kwarg
3. For Source-based points: set `credibilityTier` on the Source node once, all Points inherit
4. Run `tortoise_check_structure` — no orphans or dangling refs

**Gate rule:** CONVERGE is blocked until `calibrate_summary` shows ≤50% of evidence-type points are uncalibrated.

→ See `/skill:how-to-use-tortoise` for tier tables. Note: T0-T4 tier names refer to two different systems — operator annotation weights (how-to-use-tortoise, 0.2-1.0 multipliers) and Point Beta priors (credibility kwarg, Beta distributions). The CALIBRATE gate checks Point priors; operator annotation is separate.

### 7. CONVERGE — The decision becomes clear

Present the decision with:
- **What we decided** and why
- **What supports it** — evidence chain with provenance
- **What contradicts it** — counter-arguments considered and why they were rejected
- **What we still do not know** — remaining uncertainties with confidence levels
- **Alternatives considered** — options explored and why not chosen
- **Calibration** — source quality distribution (T0/T1/T2/T3/T4 counts), edge annotation coverage, inherited vs explicit baselines

The graph now holds the full reasoning chain, queryable forever.

### 8. PRESERVE — Everything in the graph

Run `tortoise_compute_confidence` with `require_calibration=True` to propagate belief scores. Verify chain integrity with `tortoise_check_structure`. The decision and its full calibrated reasoning chain are now preserved — future you or your team can query not just what was decided, but why the evidence was credible.

## Anti-Patterns

- Running fewer than 3 cycles — the first pass finds obvious things, the third pass finds what you would miss
- Skipping adversarial queries — confirming what you already believe is not research
- Making the decision before the third cycle — convergence means the graph tells you the answer
- Not filing counter-arguments — the NAND edges are what make this different from a research doc
- Running EP on an uncalibrated graph — converging on topology produces confident-looking but untrustworthy results
- Using NAND where mitigation applies — blanket contradiction loses nuance
- Leaving sources at T4 (default ungraded) — a graph of ungraded sources is a connectedness counter
