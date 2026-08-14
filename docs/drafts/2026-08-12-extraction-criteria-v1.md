---
title: "Extraction criteria v1 — audited against ONTOLOGY v3.6 + the expansion packs (epic #909)"
type: spec
domain: engineering
doc_status: for-owner-review
created: 2026-08-12
ownedBy: epistemic-team
governingAgreement: "#909, #753, #312"
inputs: ONTOLOGY.md v3.6, merged pack manifests (#986/#988), spec-classification-model.md, window-1 loop, extraction-research (12 sources), optimization-research (14 sources)
---

# Extraction Criteria v1 — audited

> The criteria the extraction system runs on. Audited against ONTOLOGY v3.6 + the
> merged expansion packs, refined through review+fix loops (window-1 evidence +
> two research passes), now submitted for the owner's double-check. Changes vs
> the pre-audit version are marked **AUDIT** / **RESEARCH**.

## 1. Classification axis (what each utterance IS)

| Class | Ontology mapping | Definition (criteria) | Research notes |
|---|---|---|---|
| **decision** | **Event NODE** (eventKind `decision` — the timeline record of the commitment) + state changes on the option objects (promoted/deprecated) + the criteria claims IMPL-ing them. **NOT a Point object** (state-centric model, 2026-08-12) | COMMISSIVE ∧ product-knowledge-bearing (R1∧R3) — "decided / chose / we're going with / the ruling is / default to / ship X first / reject Y" | **RESEARCH:** neighbor-turn agreement ("okay", restatement) supports the decision reading — weight the surrounding turns, not just the EDU; "should" = recommendation NEVER decision |
| **claim** | Point, pointKind **`statement`** (THE ONLY extraction point kind — option B, 2026-08-12: a point is an asserted belief; hypothesis folded into CONFIDENCE semantics — a conjecture is a low-confidence statement; observation removed — anything can be called one) | ASSERTIVE stative/gnomic — "is / costs / fails / implies / means / requires / depends on" + quantified facts | **RESEARCH:** stative-vs-dynamic is near the human ceiling (79–82% agreement) — expect ~0.85, not 0.90, on this axis; do NOT trust surface copulas alone |
| **event** | **Event NODE** (eventKind vocabulary: deployment, review, extraction, decision, + `occurrence`/`turn` to register — issue #1013) — NEVER a Point | ASSERTIVE past-perfective — "fixed / shipped / merged / deployed / ran / measured / filed / created" | The record-keeping layer: occurrences go on the timeline as Event nodes. `pointKind: event` is REMOVED (issue #1013). The decision-as-event ("we decided X on date Y") is an Event node alongside the decision-as-commitment Point |
| **process** | NOT a graph point (R3) — dropped with logged reason; work-item routing later | Work/governance commitment — "let me X / I'll fix X now / validate on 2 windows first" | The R1∧R3 conjunction's main filtering surface (measured 15×+ in operational sessions) |
| **nothing** | no item | Boilerplate, dispatch/instruction text, tool dumps, headers, paths, HTTP codes | **RESEARCH:** explicit rejection WITH a reason — never silent |

**AUDIT fixes applied:**
- event class → **Event NODE** (issue #1013) — the pre-audit criteria carried the legacy `pointKind: event` into the payload; reverted.
- claim class kinds restricted to {statement, observation, hypothesis} (ONTOLOGY §5) — `requirement` removed from the claim-kind list (it is a dev pointKind, entity-side).
- decision kinds = DECISION_POINT_KINDS (pack_registry's canonical set).

### 1.5 The epistemic semantics (owner model — STATE-CENTRIC, 2026-08-12)

**The graph stores STATE, not decisions.** Competitors store decision objects
("Decision X was made because of Reasons"); we do NOT. The record is:

1. **State** — objects (options: JTBDs, features, requirements...) with their
   **lifecycle events** (promoted / deprecated / superseded — queryable, so
   context is reconstructable) and their **confidence** (how strongly held,
   moved by the points attached).
2. **Points** — the logic: claims (criteria, arguments for/against) connected
   to the state objects via aboutObject; IMPL/NAND/MITIGATES among them encode
   the argument structure and move the object's confidence.
3. **Events** — what happened, for context: occurrences AND the
   **decision-as-event** (eventKind `decision`, aboutObject → the object(s) it
   resolved). The decision dimension is preserved as a QUERYABLE TIMELINE
   dimension — never as the structural object.

The graph therefore says "**this state is based on these reasons**", never
"this decision was made because of these reasons". Deriving state is harder
than querying a decision object (you read state lifecycle + state confidence +
the points) — but the product optimizes for the LOGIC BEHIND STATE over the
TRACK RECORD of decisions. The operationalisation (chosen option promoted,
alternatives deprecated) is expressed as lifecycle writes on the objects —
the extraction records the event + the structure; the write path applies the
state change.

## 2. Entity axis (what entities are mentioned) — closed vocab WITH semantics

> **RESEARCH:** bare kind names fail (window-1's own finding); descriptions +
> confusable-sibling (nearMisses) pairs are the proven mechanism. The prompt now
> carries each kind's kindDefs semantics from the merged packs (25 kinds).

### 2.1 Compiled vocabulary (namespaced, from the merged packs + ONTOLOGY §5)

- **core objectKinds:** Project, WorkItem, document, tag, user, skill, tool, agent, workflow, agreement, standard, other
- **core subjectKinds:** organization, team, role, legalPerson, naturalPerson
- **product-strategy:** product, feature, customer, competitor, customerSegment, market, requirement, architecture
- **dev:** epic, issue, code, api, database, software, infrastructure, deployment, indicator
- **marketing:** campaign, content, channel, audience, keyword, competitorContent
- **pm:** issue, sprint, kanbanBoard, card, milestone

### 2.2 Typing rules (R6: read + soft-enforce; never mint)

1. **Semantics first:** match against the kindDefs descriptions + examples + nearMisses (compiled into the prompt) — never name-matching alone.
2. **Near-miss (⚠) convention:** a mention closest to a kind with a flagged sibling (nearMisses) or a pointKind pressed into entity service (useCase, userJourney, jobToBeDone, valueProposition, requirement, bug, technicalDebt, contentBrief, contentPerformance, estimate, retrospective) → emit the kind WITH the ⚠ flag + the confusable sibling named. **RESEARCH:** this is the validated pattern (confusable-sibling resolution).
3. **Uncertain/other:** low-confidence or no-fit mentions → `core:other` EXPLICITLY, with the pack-proposal note — never forced nearest-kind (**RESEARCH:** forced assignment is the precision trap; coarse-parent fallback beats fine-forcing).
4. **Boilerplate exclusion (S0):** file paths, module names, git refs, HTTP codes, branch names → not entities (window-1 + audit).
5. **Relations are relations, not entities:** IMPL/NAND/MITIGATES, operators, edges → never entity rows.
6. **Pack proposals:** recurring mentions with no kind (e.g., `test`, `model`, `session`) are collected as proposals in the stream (window-1 convention) — they feed pack amendments AFTER validation, never minted inline.

## 3. Relation axis (IMPL / NAND / MITIGATES)

- **IMPL** (support) + conversation quote.
- **NAND** (attack) — extraction-emitted: direction `unidirectional` by default; `bidirectional` only for explicit mutual restatement (addendum §1).
- **MITIGATES** (R9 — PRIMARY target): edge-targeted {src, dst, op_type: IMPL}, bias 0.10–0.50, quote; support edges FIRST (deep-miss convention); cue taxonomy = the 14-cue audit list (estimate, decide with real telemetry, positioning tension not structural, the caveat is, only if, gated on, the one swing variable, only achievable because, the leading indicator is, preliminary, watch-gate not a statistical test, none would let it be built as-is, still to run before the gate is green, real but not transformative).
- **Canonical case** (X IMPL A; Z MITIGATES [X→A]; Y IMPL Z) checked every run.

## 4. The optimization loop (protocol v2 — research-upgraded)

| Element | v1 (pre-research) | v2 (RESEARCH) |
|---|---|---|
| Windows discipline | tune on the review window | **tuning / regression / fresh-test pools**; a window whose errors drive an update is contaminated forever; window-1 (tuned 3×) retired from convergence evidence |
| Loss signal | owner reviews the whole stream | owner reviews the **delta vs the previous run** + uncertainty-flagged items + random sample (full-window review early; anchoring guard: owner checks against the raw transcript — **RESEARCH:** showing the system's stream first biases the review) |
| Diagnosis | fix each flagged item | **error-cluster taxonomy** with prevalence; update ONE parameter for the MOST PREVALENT cluster; filter single-occurrence categories (never add a rule for one instance) |
| Update | single parameter change | keep single-change discipline (validated) but instruction-level rewrites, not example-level; **run each candidate ≥3×** (stochasticity); **regression set**: rerun prior windows after each change, revert unless aggregate improves |
| Stopping | zero mistakes on 2 runs | **dual-gate**: (a) 2 consecutive windows with no NEW error categories + error rate below threshold; (b) §6.3 statistical bands on the POOLED per-class N≥30; plus train/dev-gap overfit detector |
| κ gate (DE2E-1) | single-window κ ≥0.60 | **RESEARCH:** κ on one 25-EDU window is statistically fragile — accumulate labels across ≥2–3 windows; κ<0.40 → rubric revision, 0.40–0.60 → spot-check, ≥0.61 → monitor |

## 5. For the owner's double-check (the open judgment calls)

1. **The class↔write mapping** (§1, state-centric): decision→Event node (eventKind `decision`) + lifecycle writes on the option objects; claim→Point `statement` (the ONLY point kind); event→Event node (occurrence). No decision Points. Correct?
2. **The entity vocab** (§2.1): complete for the packs you care about? Any kind missing that your sessions keep producing (`test`, `model`, `session` are the current proposals)?
3. **Near-miss conventions** (§2.2): right strictness — flag+accept vs demote?
4. **Claim/event axis target** (§1 research note): accept ~0.85 as the ceiling on that axis (human agreement is 79–82%), rather than the plan's 0.90?
5. **The loop protocol v2** (§4): tuning/regression/fresh pools + dual-gate stopping — agree?
