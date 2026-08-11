---
title: "Probe Extraction v2 (R9 rubric) — Window #1"
type: probe-extraction
domain: operations
doc_status: probe
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#753, #312"
extractor_version: "value@0.1.0-draft (probe run 2 — R1-R9 rubric)"
source: "pi session 019fe4bd-d84f-7eed-ae34-ccc6edaa30a2 (daniel-ospina, tortoise repo)"
window: "full session — infra verification → issues/pipeline → NAND fix → design exploration → capture architecture"
purpose: "EXTRACTOR PROBE iteration 2 — re-extraction of the same window with R9 (MITIGATES) added to the rubric. Iteration-1 output (2026-08-09-probe-extraction-window1.md) emitted 0 MITIGATES; this run re-extracts with R9 and reports the delta. Comparison targets: gold window (sections A-G) + iteration-1 probe."
---

> **Probe extraction v2.** Same conversation, same rubric (R1-R8) PLUS **R9 — the
> owner correction: the extractor must emit MITIGATES (relevance reduction on the
> IMPL edge), and NAND ≠ MITIGATE.** This document is a DELTA over iteration 1:
> items unchanged from v1 are referenced by id, not re-copied. The new material is
> §4 (T1/T2 claims), §6 (the MITIGATES relations), §7 (the canonical test case),
> and §9/§10 (R9 discrimination log). Schema still conforms to R8 Layer-1: typed
> stream, closed kind vocabulary, `source_ref` everywhere, atomic content.

---

## §0 — Probe report (the numbers, per the task)

| Metric | Iteration 1 (R1-R8) | **Iteration 2 (R1-R9)** |
|---|---|---|
| IMPL relations | 19 (incl. 2 to work items) | **20** (added B2 → T2, the canonical Y→Z) |
| NAND relations | 1 (C2 → D2) | 1 (unchanged — see §10.1 correction of v1's wording) |
| **MITIGATES relations** | **0** | **4** |
| Edges targeted by MITIGATES | — | **[C8→D4]**, **[D6→B1]** (×2), **[B3→D5b]** |
| Canonical test case (X IMPL A; Z MITIGATES [X→A]; Y IMPL Z) | not applicable | **handled** — abstract exhibit + live instantiation (§7) |
| Layer-1 schema conformance | pass | pass (MITIGATES added to the closed relation vocabulary) |

**Verdict: the R9-corrected system now emits MITIGATES.** All 4 target the IMPL
connections identified in the conversation's own relevance-tempering language;
none attacks a point's truth (no NAND/mitigate confusion); the canonical structure
is emitted in both its abstract and its in-conversation form.

---

## §1 — What changed vs iteration 1 (the delta at a glance)

1. **Relation vocabulary:** `IMPL / NAND / MITIGATES` (R9). MITIGATES = relevance
   reduction on an operator edge, bias 0.10–0.50 (ontology `mitigate_operator`;
   skill ranges: 0.10 minor caveat / 0.30 significant / 0.50 major).
2. **Three new points:** T1 ("the GM figures are estimates"), T2 ("the pricing
   tension stays deliberately open — decide with real telemetry"), and B2's role
   is extended (was evidence → now ALSO a mitigator + evidence for T2).
3. **Four MITIGATES emitted** targeting three v1 IMPL edges — all edges the v1
   graph already contained; R9 adds the relevance operators the conversation
   actually asserted (v1 had folded them into prose or left them implicit).
4. **One v1 wording correction (§10.1):** v1's C2 row said "the directed opt-in is
   the mitigation" — under R9 that clause is part of decision D2's content, NOT a
   mitigation relation; no MITIGATES is emitted there (the target would be a NAND
   edge; R9 restricts MITIGATES to IMPL connections).
5. **Nothing-list additions (§9):** engineering "Risk & mitigations" tables are NOT
   argument-graph mitigations; the S3 constraint "never auto-wired mitigations"
   governs cross-session auto-wiring, not explicit in-conversation tempering.

Everything else (decisions, events, entities, sources, rejections) is unchanged
from v1 and referenced by id below.

---

## §2 — decisions[] (UNCHANGED from v1 — D1, D2, D3, D4, D5a, D5b, D5c, D6, D7, D8, D9a, D9b)

Same ids, contents, confidences, source_refs as iteration 1 §1. R9 adds nothing
here: the canonical "we can raise the price" (Z) is NOT a decision in this
conversation — the owner explicitly deferred the pricing lever ("Decide with real
telemetry"), so Z is emitted as the claim T2, not as a commitment (R1/R2 honored).
D10 (evaluation gating) remains R3-routed to #753/#312, not a graph point.

## §3 — events[] (UNCHANGED from v1 — E1–E5)

E1–E5 as iteration 1 §2. No R9 effect on the occurrence layer.

## §4 — claims[] (v1's C1–C18, B1–B3 UNCHANGED; NEW: T1, T2; B2 role extended)

| # | Claim (delta only) | Conf | Source (Source node) | Role |
|---|---|---|---|---|
| T1 | **The 85–96% GM / $4-COGS-target figures are COMPUTED ESTIMATES, not measurements** — S5 states "all numbers below are computed, not asserted"; launch gate G1 requires *measured* per-session cost from the 2-week pilot (median ≤$0.05, p95 ≤$0.15), and the margin target is re-scoped to **GM ≥35% at median utilization** (≥60% in 2 quarters), not 84–96% | 0.9 | S5 (§0, §4 verdicts, §7.1 G1) | **MITIGATES [C8→D4]** |
| T2 | **The capture-unit price vs marginal cost is near break-even — a deliberate positioning tension, not a structural problem; the pricing lever stays OPEN and must be decided with real telemetry from the pilot, not from the computed model** ("Decide with real telemetry; the important fact is the cost side is now tiny and bounded"; "the remaining tension to resolve deliberately is the capture unit price vs marginal cost (F4)") | 0.85 | S4 (pricing-tension note); S5 (F4, G1) | **MITIGATES [D6→B1] and [B3→D5b]** — the canonical Z, live instantiation |
| B2 | *(v1 content unchanged: p90 $0.063 / p95 $0.092 = 2–3× median; the flat 5-window table understates p90+)* | 0.9 | S5 (§1.2, §1.3) | role extended: was `IMPL → D6`; **now ALSO MITIGATES [D6→B1] and IMPL → T2** (canonical Y) |

**Extraction note (R9):** T1 and T2 are ordinary claims that PLAY a mitigating role
via a MITIGATES relation — a mitigation is not a new point kind; it is an operator
on an edge, fed by claims (R9: "Supporting evidence for a mitigation is a normal
claim IMPL-ing the mitigation").

## §5 — entities[] (UNCHANGED from v1 — with one pack-proposal note)

v1 §4 stands. Add to the `operator` kind pack-proposal note: the operator entity
now covers **IMPL / NAND / MITIGATES (`mitigate_operator`)** — the proposal text
should list all three (v1 listed two).

## §6 — relations[] — IMPL / NAND / MITIGATES (the R9 core)

### 6.1 IMPL (v1's 19, all unchanged; one new)

| From | To | Type | Why (delta rows only) |
|---|---|---|---|
| **B2** (p90 is 2–3× median) | **T2** (tension stays open) | **IMPL** | canonical **Y → Z**: the tail-cost measurement is why the pricing lever cannot be declared settled from the model |
| *(all other v1 IMPL rows unchanged: C1/C10/C13→D1; C3/C4→D2; C6→D3; C8/C17→D4; C15/C16→D9a; C11/C14→D6; B2→D6; D6→B1; C7→B1; C7→D5c; B3→D5b; C9/C18→work items)* | | | |

### 6.2 NAND (unchanged)

| From | To | Type | Why |
|---|---|---|---|
| C2 (agreement-coupling inversion) | D2 (bidirectional default) | NAND | truth/behavior attack on the symmetric default; stands as-is (§10.1) |

### 6.3 MITIGATES — the new emissions (R9)

| Mitigating claim | Target edge | Bias | Why (conversation evidence) |
|---|---|---|---|
| **T1** ("GM figures are computed estimates; G1 requires pilot measurement; target re-scoped to ≥35%") | **[C8→D4]** (85–96% GM IMPL local-intelligence ruling) | **0.30** | C8 stays TRUE (local extraction does flip the margin sign); but its weight as the architecture's economic support is tempered — it is a *model*, the launch gate demands *measured* telemetry, and the accepted launch target is ≥35%, not 84–96%. "It's an estimate" = relevance attack on the edge, not a truth attack on C8 |
| **T2** ("pricing tension deliberately open; decide with real telemetry") | **[D6→B1]** (warrant-deferral IMPL break-even-without-pricing-change) | **0.35** | B1's conclusion "break-even **without pricing change**" is TRUE at the median but its relevance to settling the pricing question is tempered — the capture-unit vs marginal-cost tension is explicitly LEFT OPEN pending telemetry. The canonical Z, live instantiation |
| **T2** (same claim) | **[B3→D5b]** (overage unit honesty IMPL metered-usage decision) | **0.30** | B3 stays true ($5/10k ≈ $0.0275/capture ≈ marginal cost — honest unit); but "capture needs either its own usage line or a write-op price that reflects LLM cost — decide with real telemetry" tempers how much the unit-integrity finding settles the metering/pricing design |
| **B2** (p90/p95 = 2–3× median; flat table understates the tail) | **[D6→B1]** (same break-even edge) | **0.30** | independent second attack on the same entailment: the "$0.02–0.05/session" premise "holds at the median and breaks above p90" — the break-even claim matters less than it seems for heavy sessions. (Two mitigations on one edge = two independent relevance findings; bias composition is an open research question, §10.4) |

**Where the MITIGATES did NOT fire (discrimination log, §10.2):** C2→D2 (NAND edge,
out of R9 scope), the eval-spec "E019 would fail today" caveat, the
"caching is NOT the cost fix" note, and the capture-architecture "hosted recall is
of the derived layer only" trade-off — none had a kept IMPL edge to attack, so none
was emitted (no dangling operators; R8 Layer-1).

---

## §7 — THE CANONICAL TEST CASE (deterministic probe case, R9)

### Exhibit A — abstract form (the owner's example, emitted verbatim as the probe case)

```
X   : Point "it's cheap"
Option A : Point "Option A"                      (the option X argues for)
Z   : Point "we can raise the price"
Y   : Point "customers aren't price-sensitive"

X        -[:IMPL]->     Option A                 (cheap ⇒ choose A)
Z        -[:MITIGATES]-> (X→A edge)  bias 0.35   (Z targets the OPERATOR EDGE id, not X, not A)
Y        -[:IMPL]->     Z                        (the price-insensitivity evidence is why Z holds)
```

**Decision trace (why MITIGATES, not NAND):** Z does not say "X is false" (X stays
true — it IS cheap) and does not attack Option A's truth. Z says the *connection*
matters less: cheapness is less decisive because the price lever exists. → relevance
attack on the edge, bias within 0.10–0.50. Y IMPL Z is emitted as ordinary support
(R9's canonical wiring). **All three relations emitted; MITIGATES targets the edge
id [X→A].** ✓

### Exhibit B — live instantiation (the same structure, found in THIS conversation)

The conversation's pricing/break-even cluster is the owner's example in disguise:

| Canonical role | Live point (probe id) | Conversation text |
|---|---|---|
| X ("it's cheap") | C10 / B1's cost side ("the cost side is now tiny and bounded"; LLM ~$0.011–0.032/capture at Flash) | S1, S4 |
| Option A ("pick the cheap option") | B1's conclusion "break-even without pricing change" — the option under consideration (gold's D6b; the probe correctly did NOT emit it as a decision, R1) | S4, S5 |
| Z ("we can raise the price") | **T2** ("the capture-unit price vs marginal cost is near break-even — tension deliberately open; decide with real telemetry") | S4 pricing-tension note; S5 F4 |
| Y ("customers aren't price-sensitive") | **B2** ("p90 is 2–3× the median; the flat 5-window table understates the tail") | S5 §1.2 |

```
C10/B1-cost-side  -[:IMPL]->  B1 ("break-even without pricing change")     [= X → Option A]
T2                -[:MITIGATES]->  [D6→B1]  bias 0.35                     [= Z → (X→A) edge]
B2                -[:IMPL]->  T2                                          [= Y → Z]
B2                -[:MITIGATES]->  [D6→B1]  bias 0.30                     (independent tail finding)
```

**Canonical verdict: handled correctly** — all three required relations are present,
MITIGATES is on the edge (dst = the IMPL edge id), and the live instance matches the
owner's semantics (the cheap claim stays true; its decisiveness drops).

---

## §8 — sources[] (UNCHANGED from v1 — S0–S16)

S0 (agentSession) through S16 (pricing.json) as iteration 1 §6. T1/T2 carry
`extractedFrom → S0` with `references → S4/S5` (R4 chain).

## §9 — nothing[] (v1's 12 items UNCHANGED; R9-related additions)

| # | What | Why rejected (logged) |
|---|---|---|
| 13 | The engineering "Risk & mitigations" tables (S1 pipeline risks, S5 guardrail tables) | **R9 discrimination:** those are implementation risk-mitigations (controls), NOT argument-graph mitigations (edge-relevance attacks). No `mitigate_operator` semantics; kept as artifact content (S1/S5 indexed), nothing extracted. |
| 14 | "Never auto-wired mitigations" (S3 constraint regime) | Governance constraint about CROSS-SESSION auto-wiring, not about extracting explicit in-conversation tempering. R9's MITIGATES are explicit, quoted, in-window — no conflict; the constraint itself is design content (S3), not a graph claim. |
| 15 | The owner's pricing-deferral stance as a decision | R1/R3: "decide with real telemetry" is a deferral, not a commitment — emitted instead as the claim T2 + MITIGATES (the R9-correct shape). |

## §10 — R9 compliance notes

### 10.1 Correction to v1 (NAND ≠ MITIGATE)
v1's C2 row: "NAND → D2 … mitigation = directed opt-in". Under R9 this wording is
wrong in both directions: (a) the directed-opt-in is a clause INSIDE decision D2
(the ruling's escape hatch), not a graph mitigation; (b) emitting a MITIGATES there
would target a NAND edge, which R9 reserves for IMPL connections. v2 keeps
`C2 → D2 : NAND` and emits no MITIGATES on it. The inversion finding attacks the
symmetric default's behavior; the opt-in bounds the damage as decision content.

### 10.2 Discrimination log (where MITIGATES did NOT fire, with reasons)
- **C2 → D2**: NAND edge — out of R9's scope (MITIGATES targets IMPL).
- **"E019 a_drop>0.03 would fail against today's code (suite Docker-skipped)"** (eval-spec session): a caveat on the spec's assertions, not on a kept IMPL edge → not emitted.
- **"Caching is NOT the cost fix; warrant discipline is"** (S5 §1.4): tempers a cost-reduction claim that was never emitted as a point → no target edge → not emitted.
- **"Hosted recall is of the derived layer + summaries, not the literal transcript"** (S2 trade-offs): tempers the architecture's *value proposition*, but no kept point asserted full hosted recall → no edge to attack → not emitted (logged for the semantic eval set: a near-miss the extractor must be able to explain).

### 10.3 Bias assignment (consistent with ontology ranges)
0.30 = significant limitation (estimate-vs-measurement; tail-cost break); 0.35 =
significant-to-major for the canonical Z (the pricing lever genuinely re-opens the
settled conclusion). All within 0.10–0.50; nothing >0.50 (which would invert and
become a NAND).

### 10.4 Open research questions (feeds requirements framing question 0)
1. **Bias composition:** two independent mitigations on [D6→B1] (T2 0.35, B2 0.30) —
   how does EP compose edge mitigations? (sum with cap / max / multiplicative) —
   needs the engine semantics before bias values are load-bearing.
2. **Cue taxonomy:** the four cues observed (estimate-language, telemetry-deferral,
   "tension deliberately open", percentile-tail findings) as the seed few-shot set
   for the MITIGATES classifier; measure recall on the probe set (R8 Layer 2).
3. **Near-miss policy:** §10.2's "no target edge" class — should the extractor
   surface a *proposed* edge for review (hold queue) or stay silent? Scoping decision.

---

*Probe v2 output — R1-R9 rubric, same window as iteration 1. The R9 delta: 4
MITIGATES relations on 3 v1 IMPL edges, canonical case emitted in abstract + live
form. Compare against 2026-08-09-probe-extraction-window1.md (v1) and the gold
window sections A-G.*
