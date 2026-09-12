---
title: "Tortoise vs Memory Systems — mechanism-named comparison table + publication/errata discipline (epic #2080, W7)"
type: product
issue: "#2106"
date: 2026-09-05
created: 2026-09-05
domain: product
doc_status: live
subjects.team: epistemic-team
ownedBy: epistemic-team
aboutSubjects: tortoise-memory, tortoise-evals, tortoise-longmemeval
aboutObjects: tortoise-write-path-eval, tortoise-volunteering-reflex, tortoise-why-recall, tortoise-ask, tortoise-search
extends: docs/planning/2026-09-01-2080-gbrain-plan.md (§2.6 W7, §6.7 row contract, E2E-8), docs/research/2026-08-31-gbrain-learnings/research-brief.md (W7 ADOPT + adversarial), docs/research/2026-08-31-gbrain-learnings/learnings-map.md (W1 verdicts)
---

# Tortoise vs Memory Systems — comparison table + publication/errata discipline

> **Epic:** #2080 (W7 capstone — publication half). **Issue:** #2106.
> **Consumes:** W2-b write-path receipts (#2098, merged), W3-a harness
> receipts (#2099, merged), W7-a 500-Q sealed run (#2105, **OPEN**), W3-b
> why-layer suite (#2100, **OPEN**), W1 learnings verdicts (#2096, merged).
> **Extends (this revision — #2523):** reserved agent-reasoning-battery row
> contract (§0 status row, §2 semantics, §3.1 row, §3.2 block, §5, §10).
> Additive — no published number changes; battery-row fills are gated on
> #1416 verdict receipts (epic #1402) and land as annotated updates (§7).
> **Audience:** P4 eval-consumer / benchmark auditor (J7) — an external
> reader must be able to verify or refute every number in this file from the
> cited receipt alone.
> **Rule of the file:** mechanism-named rows, neutral cited tables, **no win
> claims on benchmarks not run**. Every row carries a citation: Tortoise rows
> → receipt naming commit + corpus hash + judge pin; vendor rows →
> published-source link + a `⚠️ not re-run by us — vendor claim` note.

## 0. Publication status (read first)

Rows below are **sealed to the committed receipts on `main` at base
`b9d3471c` (2026-09-04)**. Nothing in this file is a mid-epic snapshot or a
projection.

| Row | Status | Evidence |
|---|---|---|
| Tortoise — LongMemEval 500-Q `recall_all@5` | **PENDING — W7-a sealed run (#2105, OPEN). Not yet measured. No number exists.** | Receipt to be committed by #2105 |
| Tortoise — why-layer suite (planted-conflict gold, A4 A/B) | **PENDING — W3-b (#2100, OPEN). Not yet measured.** | — |
| Tortoise — agent-reasoning battery differentiation profile (R1–R5, L1–L6, D1–D4, all arms A0–A4) | **PENDING — #1416 verdict receipts (epic #1402, OPEN). Row contract pre-registered (#2523, §3.2); no number exists.** | Receipt = `profile.json` + `matched_recall` block (commit + corpus hash + judge pin) to be committed by #1416 |
| Tortoise — W2 write-path survival (product lane) | ✅ sealed | `tests/eval/write_path/baselines/main.json` + receipt `w2b-2026-09-03-first.json` |
| Tortoise — W2 write-path survival (deterministic CI lane) | ✅ sealed | `baselines/m2.json` + receipt `w2b-m2-lane-2026-09-03.json` |
| Tortoise — W3 harness 7-metric snapshot (product lane) | ✅ sealed | `tests/eval/harness/baselines/main.json` + receipt `w3h-llm-bpre-2026-09-04.json` |
| Tortoise — W3 harness 7-metric snapshot (deterministic CI lane) | ✅ sealed | `baselines/m2.json` + receipt `w3h-m2-bpre-2026-09-05.json` |
| Vendor rows (MemPalace, gbrain, Supermemory, Mem0) | ⚠️ vendor-published; **not re-run by us** | published-source links |

**Sequencing caveat (epic §2.6 ordering note):** the epic requires W7
publication to describe the **epic-shipped system (post-W4-gate state)**,
and the W4 user-exposure gate needs the write-path survival target pass on
the frozen corpus after ≤ 2 fix-waves as attached evidence. As of base
`b9d3471c`, the W2-b baseline is published with its failure classes named
(`content_missing`, `ep_update_missing` — baseline history + receipt), but
the **post-fix-wave survival-target pass has NOT yet been demonstrated on
`main`** (the W5 ingestion-quality re-run on the frozen corpus is a pending
handoff). The Tortoise rows below therefore state what is measured and
sealed, and **flag the gate evidence as pending** — no row claims a
survival-target pass that has not been committed. Fills land via the errata
trail (§7), never silently.

**Handoff to the orchestrator (post-merge):** when #2105 lands, replace the
PENDING 500-Q row value + receipt link via an annotated fill (§7). When
#2100 lands, add the why-layer rows the same way. When #1416 lands, fill
the reserved battery rows of §3.2 the same way — annotated fill (§7) + the
matched-recall citation rule (§3.2.1); #2523 only pre-registers the row
shape. When the W5 fix-wave re-run on the frozen corpus is blessed, add
the re-run receipt to the W2 row's citation and annotate.

## 1. Rules of the table

1. **Mechanism-named rows.** Each row names what the system *actually does*
   (retrieval architecture, graph semantics, write path), not what it claims
   in marketing. Mechanism comes first; numbers are read through the
   mechanism.
2. **Neutral cited tables.** Numbers are presented side by side with the
   mechanism named and a citation per row. Where a system loses or scores
   low, the low number and the reason stay in the table (see §8).
3. **No win claims on benchmarks not run.** A row whose benchmark we have
   not run carries `⚠️ not re-run by us — vendor claim`. **This file FAILS
   content validation if any row claims a win on a benchmark not run** — the
   negative case of E2E-8. Tortoise rows that are pending carry `PENDING —
   not yet measured`, never a placeholder number.
4. **Metric-mixing explicitly flagged.** `QA-acc ≠ R@k ≠ recall_all@k`, and
   read-path ≠ write-path. Every value's semantics are named in the row;
   nothing is compared head-to-head across metric families (§2).
5. **Every published number traceable.** Tortoise rows cite the committed
   baseline + receipt naming exact commit + corpus hash + judge pin. Vendor
   rows cite the published source.

## 2. Metric-semantics key (read before the table)

| Term | Definition | Trap |
|---|---|---|
| `recall_all@5` (official LongMemEval) | ALL of a question's ground-truth sessions must land in top-5: `all(doc in recalled_docs for doc in correct_docs)` | The official evaluator's semantics. **NOT any-hit.** |
| any-hit R@5 | ANY of the ground-truth sessions in top-5 | Looser than `recall_all` for the 133 multi-session questions; **gbrain's 97.6% headline was this variant — walked back via erratum** (§7 cautionary example). |
| QA-acc / QA accuracy | end-to-end question-answering accuracy over retrieved context | Measures the *answer model*, not retrieval. A system can have 100% retrieval recall and 60% QA-acc. **Do not compare QA-acc against R@k.** |
| Write-path extraction recall / survival | fraction of planted salient units that survive session→memory write-back | Measures the *write path*. Read-path self-reports ≫ write-path measured everywhere this table covers (Mem0 42.9%, Supermemory 41.5% on HaluMem vs 90%+ read-side self-reports). |
| Battery `surfaced-within-1-turn` rate (R1) | fraction of planted mid-session contradictions the arm surfaces (NAND filed / flag / explicit position change) within 1 turn of the planting turn — an **agent-behavior rubric rate over the scenario battery** | Measures agent *behavior on planted reasoning scenarios*, not retrieval. Generic memory stores score ~0 by construction (they store, they do not detect contradiction). **NOT retrieval recall, NOT QA-acc** (#1509). |
| Battery `coverage-subscore` (R2) | judged deliberation-coverage subscore — anchored YES/NO rubric (`r2-coverage.json`) over decision memos, arm-neutral constructs | Measures the *deliberation content* (counter-argument weighing, what-could-be-wrong specificity, support-vs-oppose, revision). Rubric is **validated on real text before any scoring** (2026-09-08: retest 1.00, raw agreement 0.886, Gwet AC1 0.849 — `docs/epics/1402-eval-battery/benchmarks/2026-09-08-r2-pre-exposure/records.json`); judge-reliability bar **AC1 ≥ 0.70 on real text** (`battery/judge/gate.py`). |
| Battery Brier (R3) | mean squared confidence–outcome error over knowable-outcome scenarios (EP-derived confidence vs ground truth) | Measures the *confidence source's calibration*; contested scenarios score honest-UNDEC (≥ 80%), never a fabricated number. `ep-variance` ≥ 0.04 ⇒ claim reads CONTESTED — convergence ≠ decisiveness. |
| Matched-recall factual F1 (K=5) | per-arm top-5 factual retrieval F1 over the recall probe subset, measured **before** the battery runs (`battery/recall/matcher.py`, #1413) | **The control line, not a success metric.** Any arm ≥ 0.10 F1 short of corpus-best fires the symmetric trigger → recall-matched balanced subset; subset < 50% ⇒ INCONCLUSIVE. A differential cell without its matched-recall block is un-auditable (§3.2.1). |

**LongMemEval semantics record (Tortoise):** Tortoise's LongMemEval runner
records 4 documented divergences from the official evaluator
(`tools/longmem_eval/dataset_audit.py` — `_abs` inclusion, assistant-role
turns, per-question-fraction, sparse `has_answer`). **Every published
Tortoise LongMemEval report must state which semantics its numbers stand
on** (report gate — the audit record is mandatory). The 500-Q row below is
the official-`recall_all@5` assertion pending #2105; labeled variants are
published alongside, never mixed into the official number.

## 3. Comparison table

Columns: System | Mechanism (what it actually does) | Benchmark | Metric |
Value | Notes / citation.

### 3.1 Tortoise (this repo — mechanism: graph + EP + why-context assembly)

| System | Mechanism (what it actually does) | Benchmark | Metric | Value | Notes / citation |
|---|---|---|---|---|---|
| Tortoise | graph (FalkorDB) + EP belief propagation (support chains, NANDs, contestation, supersession) + hybrid retrieval; session→graph write-back via LLM extraction with provenance stamping | LongMemEval 500-Q (official set, sealed keys, pinned judge — W7-a) | `recall_all@5` (official) | **PENDING — W7-a sealed run (#2105, OPEN). Not yet measured. No number exists.** | Receipt (commit + corpus hash + judge pin + `dataset_audit.py` divergence record) to be committed by #2105. Pre-epic n=1 smoke runs existed (wide CI ~[0.207, 1.0] at n=1 — research brief §5); their successor is this run, with honest semantics. **No number is claimed here.** |
| Tortoise | session→graph write-back (LLM extraction posture, BPRE, 5 sessions / 72 planted units / verbatim anchors + distractors + attribution hazards) | planted-gold write-path survival (Cat-35-style, Tortoise corpus — **not** gbrain's Cat 35 corpus) | salient-unit survival **macro** (point-level, REPHRASE-linked dedup) | **0.25** (current blessed product-lane baseline) | `tests/eval/write_path/baselines/main.json` + receipt `w2b-2026-09-03-first.json`: commit `20d3c4ba`, corpus `sha256:52d4a742…d90dc8`, judge `w2-write-path-mechanical-v1`. **First-published 0.3056 (2026-09-03T12:45:44Z) preserved in baseline history**; re-run at the clean head measured 0.25 (LLM extraction jitter, same code/corpus/pin — disclosed, receipt pins the runner commit). This low number is published on purpose (§8). |
| Tortoise | same write path — deterministic echo lane (extractor posture `m2`) | same planted-gold corpus | salient-unit survival macro / strict | 0.9722 / 0.0 | CI **can-fail determinism gate only** — leakage 11/run is structural on the echo extractor and the product leakage bar does **not** apply to m2 (baseline justification). Receipt `w2b-m2-lane-2026-09-03.json`: commit `99324400`, same corpus + judge pin. Not a product-surface number. |
| Tortoise | write path (same as above) | same planted-gold corpus | salient-unit survival **strict** (full-credit only) / leakage / sessions emitting | **0.0** / 0 / 1.0 | Strict 0.0 is the honest low number — **published, not suppressed** (§8). Receipt `w2b-2026-09-03-first.json`. |
| Tortoise | Cat-34-style volunteering-memory harness: hermetic real-seam replay, BPRE 15-session gate corpus, product lane (LLM extraction), **null-reflex** | know-to-ask / false-fire / push / write-back / continuity / isolation | `know_to_ask_failure_rate` / `false_fire_rate` / `push_precision` / `push_recall` / `write_back_fidelity` / `continuity_recall` / `source_isolation_violations` | **1.0 / 0.0 / 0.0 / 0.0 / 0.25 / 0.3333 / 0** | `tests/eval/harness/baselines/main.json` + receipt `w3h-llm-bpre-2026-09-04.json`: commit `e50ae316…`, corpus `sha256:ded9706426…`, judge `w3-volunteering-memory-mechanical-v1`. **NULL-reflex honest numbers**: the standing kta/false-fire/push bars only gate under `config.reflex: "graded"` — the graded EP-scored reflex seam is W4 (#2103, OPEN) and will re-bless. `write_back_fidelity` 0.25 is a REAL finding (echo lane's 1.0 was the deterministic artifact). Continuity 0.3333 (1 of 3 BPRE readers) with LLM-jitter disclosed (a prior run measured 0.667 — unreachable on the 3-reader BPRE corpus). Verdict `inconclusive` on the receipt = reproducibility disclosure, not a pass. |
| Tortoise | same harness — deterministic echo lane (posture `m2`) | same gate corpus | `know_to_ask_failure_rate` / `false_fire_rate` / `push_precision` / `push_recall` / `write_back_fidelity` / `continuity_recall` / `source_isolation_violations` | **1.0 / 0.0 / 0.0 / 0.0 / 1.0 / 1.0 / 0** | Deterministic CI lane (byte-reproducible PASS at clean replay). Echo-lane artifact disclosed: `write_back_fidelity`/`continuity_recall` 1.0 are deterministic-lane values (echo extractor), not product claims; kta-failure 1.0 / push 0.0 are the same honest NULL-reflex rows as the product lane. Receipt `w3h-m2-bpre-2026-09-05.json`: commit `16d67cab` (filename dated 2026-09-05; internal date 2026-09-04T09:09Z — filename date is the lane label, internal date authoritative). |
| Tortoise | why-layer (planted-conflict gold: NANDs / supersession surfaced at recall time) | W3-b why-suite | why-layer rates | **PENDING — W3-b (#2100, OPEN). Not yet measured.** | A4 ranking A/B (measured by W3-b) also pending. No number is claimed here. |
| Tortoise | gbrain-evals adapter scorecard (a Tortoise row through *their* harness) | — | — | **NOT adopted — feasibility assessed, not cheap** | See §9. If adopted later it is an explicit ADDITION, mechanism-named + cited, **never a substitute** for the in-repo numbers. |
| Tortoise | epistemic graph + Decide workflow (A4) vs no-memory / long-context / generic-memory / RAG arms (A0–A3) on the same scenario battery (#1402) | agent-reasoning battery — differentiation profile | R1–R5, L1–L6, D1–D4 cells (§3.2) | **PENDING — #1416 verdict receipts (epic #1402, OPEN). Contract pre-registered (#2523); no number exists.** | Row contract + per-family semantics: §3.2. Fills land as annotated updates (§7) citing `profile.json` + the `matched_recall` block (#1413). Boundary (§1/§2/§4; #1509): every cell is a reasoning-outcome rate over the scenario battery — **never QA-acc, never retrieval recall, never wire-correctness**; recall is the control variable, held constant per run, never a success metric. |

### 3.2 Reserved battery rows — differentiation-profile contract (#2523)

**Status: contract only — no cell is filled.** The #1416 verdict producer
(epic #1402) publishes one row per family below (all arms A0–A4, no
exclusions — owner decision 2026-08-14). Each cell lands as an annotated
fill (§7) carrying its receipt: commit + corpus hash + judge pin + the
`matched_recall` block (§3.2.1). Until a receipt exists the cell stays
**PENDING — no placeholder number** (§1 rule 3; §10 negative case). This
block does **not** fill the sibling PENDING rows (#2105 500-Q;
why-layer #2100, publish issue #2522) — it pre-registers the battery row
shape only.

**Boundary (inherited §1/§2/§4; #1509):** every battery cell is a
reasoning-outcome rate over the scenario battery — **never QA-acc, never
retrieval recall, never wire-correctness**. Retrieval recall is the
**control variable** — held constant per run (§3.2.1), never a success
metric (battery draft §0 principle 2; D1 sweep: retrieval metrics are
diagnostics only). A battery row that reports QA-acc, R@k, or
`recall_all@k` as a cell value fails content validation.

#### 3.2.1 Matched-recall citation rule (#1413) — every cell carries the control line

Every differential cell (R1–R5, L1–L6, D2–D4) cites the run's
matched-recall block — the recall-held-constant evidence **is part of the
row, not optional**. Contract constants live in
`battery/recall/matcher.py` (#1413) and the block is recorded verbatim in
`profile.json` `matched_recall`:

| Field | Contract (matcher.py) | Meaning |
|---|---|---|
| `f1_by_arm` | top-K factual retrieval F1 per arm, K=5 (`TOP_K`) | per-arm factual retrieval on the recall probe subset, measured **before** the battery runs |
| `trigger_population` ⚠️ **amended 2026-09-12 (#3327)** | the retrieval-capable comparators `{a1, a2, a2b, a3, a4}` — contract text; **no constant yet** in `matcher.py` (which holds `TOP_K`, `F1_TOLERANCE`, `SUBSET_FLOOR`); wiring tracked by #3327 | the arms the divergent trigger is defined over. `a0` (no-memory control) is **excluded** — **not a participant**: it is retained as the trigger's **positive control** and as a published profile row annotated *recall-confounded*. Reason: a0's recall is 0.0 by construction, so an a0-inclusive trigger fires on every run and makes every differential run INCONCLUSIVE |
| tolerance | `F1_TOLERANCE` = 0.10 | any arm ≥ 0.10 F1 short of the corpus-best factual retrieval fires the trigger — *“any arm” is now read as `trigger_population` (amended 2026-09-12, #3327; original wording preserved — see §7)* |
| `trigger_fired` | symmetric trigger | ≥ 1 arm out of match → rerun on a recall-matched balanced subset |
| `subset_pct` | `SUBSET_FLOOR` = 0.50 | rerun subset < 50% of probes ⇒ matching is not meaningful |
| `outcome` | `matched` \| `inconclusive` | immutable per run — never re-interpreted post-hoc |

Consequences: (a) a cell whose receipt lacks the matched-recall block
fails review (J7-audit standard); (b) `trigger_fired` AND `subset_pct <
0.50` ⇒ verdict **INCONCLUSIVE** (`battery/report/verdict.py`) — no cell
publishes as a differential win; the block states INCONCLUSIVE (reported,
not re-interpreted); (c) **#2513/#2514 mid-epic recall improvements are a
named confound** — each run pins corpus (commit + hash) + retrieval
config; a different corpus/retrieval state is a new receipt, never a
silent re-label of an old cell.

#### 3.2.2 Reserved family rows (PENDING until #1416)

| Family (AC) | The number IS — row semantics (per §2) | Value slot | [cal] key in `battery/config/thresholds.yaml` | Cell classification + load-bearing contract (`battery/report/classify.py`) |
|---|---|---|---|---|
| R1 (AC-R1) | surfaced-within-1-turn rate — agent-behavior rubric rate over the scenario battery (fraction of planted mid-session contradictions surfaced via NAND/flag/explicit position change within 1 turn). Co-rows: explicit-resolution rate; silent-flip-flop (≤ 10%); false-positive on matched non-contradictory controls (≤ 5%). **NOT retrieval recall, NOT QA-acc.** | A4 vs best comparator, per arm | `surfaced-rate` {a4 0.92, a0 0.00}; `flip-flop-rate` {a4 0.10}; `false-positive-rate` {a4 0.05} | STRUCTURAL family (`STRUCTURAL_FAMILIES` {R1, R4}) — a4 loss must surface WEAK, never masked; load-bearing |
| R2 (AC-R2) | judged coverage subscore — anchored YES/NO deliberation rubric (`r2-coverage.json`) over decision memos; constructs arm-neutral. Rubric validated on real text before scoring (2026-09-08: retest 1.00, raw agreement 0.886, Gwet AC1 0.849 — `docs/epics/1402-eval-battery/benchmarks/2026-09-08-r2-pre-exposure/records.json`); judge bar **AC1 ≥ 0.70 on real text** (`battery/judge/gate.py`); scoring blocked without a validated rubric. Gate: ratio ≥ 1.5× vs a0 with the a0=0 floor. | A4 vs best comparator, per arm | `coverage-subscore` {a4 0.50, a0 0.00} | Load-bearing. Process-fidelity (≥ 80% reach 3+ cycles) is a diagnostic only — excluded from the verdict (AC-D1 counts the judged subscore only). |
| R3 (AC-R3) | Brier — mean squared confidence–outcome error over knowable-outcome scenarios; contested scenarios score honest-undecided (≥ 80%), confident-wrong ≤ 10%. Confidence source is EP-derived (graph arm) vs verbal (control). | A4 vs best comparator, per arm | `brier` {a4 0.25, a0 0.30}; `ep-variance` {a4 0.04} (posterior-variance threshold above which a claim reads CONTESTED — convergence ≠ decisiveness; drives the honest-UNDEC path) | Load-bearing |
| R4 (AC-R4) | defeat-condition precision — fraction of stated "what would change your mind" conditions matching the graph's real NAND/mitigation structure (≥ 70%); completeness ≥ 1 real defeat condition per decision. | A4 vs best comparator, per arm | `defeat-precision` {a4 0.70, a0 0.00} | STRUCTURAL family — a4 loss must surface WEAK, never masked; load-bearing |
| R5 (AC-R5) | correct-direction belief-update rate on evidence retraction/undercut (rebut-vs-undercut respected; over-reaction ≤ 10%). Contested leg: flat-store "0% responsive" is an empirical expectation verified in the differential, never a by-construction win. | A4 vs best comparator, per arm | `correct-direction-rate` {a4 0.90, a0 0.50} | Load-bearing |
| L1 (AC-L1) | dependent-subtask composite success over an interdependent stream (fresh context per session; only arm difference is memory) — ≥ 0.85 graph vs ≤ 0.5 no-memory [cal]. LoCoMo check: strong long-context recall without a graph must still fail the stream. | trajectory value, per arm | AC-L1 [cal] gate; thresholds.yaml row lands with #1411 instrumentation | Load-bearing |
| L2 (AC-L2) | token-trajectory convergence per task repetition (SEA-Eval pseudo-evolution gate) — ≥ 30% reduction by rep 3 [cal]; flat tokens while the graph grows = FAIL. ⚠️ Provisional: single-source gate (SEA-Eval 2604.08988), corroboration sought. | trajectory value, per arm | AC-L2 [cal] gate; thresholds.yaml row lands with #1411 instrumentation | Load-bearing |
| L3 (AC-L3) | reasoning-quality slope across ≥ 3 waves (blind-judge rubric, held-out harder variants per wave) — > 0 with graph, ≈ 0 for no-memory. | slope per arm | AC-L3 [cal] gate; thresholds.yaml row lands with #1411 instrumentation | Load-bearing |
| L4 (AC-L4) | cross-session contradiction surfacing — 100% of A/¬A-session conflicts surfaced by session N+1 [cal]; surfacing latency ↓ as the graph grows. | surfacing rate / latency, per arm | AC-L4 [cal] gate; thresholds.yaml row lands with #1411 instrumentation | Load-bearing |
| L5 (AC-L5) | decision-drift resistance — same-option decision consistency ≥ 90% + rationale consistency ≥ 80% at t+21d (≥ 10 interleaved sessions); control drift ≥ 30%. | consistency per arm | AC-L5 [cal] gate; thresholds.yaml row lands with #1411 instrumentation | Load-bearing |
| L6 (AC-L6) | distillation reasoning-fidelity — distilled-graph-arm score / raw-session-arm score on the Tier-1 rubric (≥ 0.95 [cal]); no contradiction pair dropped below surfacing threshold after consolidation. | fidelity ratio per arm | AC-L6 [cal] gate; thresholds.yaml row lands with #1411 instrumentation | **Not load-bearing** (absent from `LOAD_BEARING_FAMILIES`) |
| D2 (AC-D2) | pseudo-evolution spread — A2/A2b/A3 token-trajectory divergence (memory grows, behavior does not) vs A4 convergence, ≥ 2× [cal]; saturation-parity bonus scores (LongMemEval/LoCoMo/ForgetEval-class staleness) show the reasoning delta is not explained by raw recall. | spread per arm pair | AC-D2 [cal] gate; thresholds.yaml row lands with #1412 instrumentation | **Not load-bearing** (absent from `LOAD_BEARING_FAMILIES`) |
| D3 (AC-D3) | feedback-integration fix-rate — A4 ≥ A0 by a calibrated margin across ≥ 5 iterations (OPT-BENCH-style loop); per-iteration improvement monotone. | fix-rate per arm | AC-D3 [cal] gate; thresholds.yaml row lands with #1412 instrumentation | Load-bearing |
| D4 (AC-D4) | adversarial robustness — poisoned-claim rejection ≥ 80% at high confidence; T0 > 10×T4 rank ordering survives EP; anchored-but-superseded beliefs abandoned, not persisted. | rejection / rank-survival per arm | AC-D4 [cal] gate; thresholds.yaml row lands with #1412 instrumentation | Load-bearing |
| D1 (AC-D1) | **verdict row (no scalar cell)** — the block's headline: outcome ∈ UNIQUE / MECHANISM-NOT-UNIQUE / WEAK-UNMITIGATED / INCONCLUSIVE + differentiators + weaknesses + mitigation paths (`battery/report/verdict.py`). Subsumes the 14 scalar cells above. | verdict outcome + lists | — (consumes classification-delta 0.10 for the STRONG/WEAK margin) | n/a — the profile-level AC (battery draft §6 AC table lists R1–R5, L1–L6, D2–D4 scalar families + this verdict AC) |

Each row is a reserved PENDING slot; a fill replaces the PENDING line
with a dated note naming the receipt (commit + corpus hash + judge pin +
`matched_recall` block) — the pre-fill PENDING state stays visible in git
history (§7, never force-pushed away).

#### 3.2.3 Verdict look-up — AC-R1…AC-D4 → row values → pre-committed outcomes

Consumed from `battery/report/classify.py` / `battery/report/verdict.py` so
the docs row and the code classifier cannot drift (acceptance 3):

| Verdict outcome (pre-committed — battery draft §6; vocabulary from `verdict.py` `VERDICTS`) | Look-up over cells | This file's state |
|---|---|---|
| UNIQUE | ≥ 1 STRONG on a load-bearing axis (empirically won, not structural) AND no load-bearing WEAK lacking a documented mitigation path | profile publishes; the "graph improves reasoning; other memory systems don't" claim ships with the full profile as evidence |
| MECHANISM-NOT-UNIQUE | no STRONG-on-load-bearing (≥ 1 STRUCTURAL) | uniqueness claim dropped permanently until a new mechanism is built and re-validated; retention (Tier-1/2) positioning survives; profile still published |
| WEAK-UNMITIGATED | a load-bearing WEAK lacks a mitigation path | claim gated until fixed and a re-run shows it below the serious threshold; WEAK cell + mitigation path recorded; re-run loop |
| INCONCLUSIVE | matched-recall `trigger_fired` AND `subset_pct` < 0.50 | no differential number publishes; block states INCONCLUSIVE; epic re-scopes the comparator or reports "not demonstrated" |

The full profile is reported in **all** outcomes — the diagnostic value is
the profile itself.

#### 3.2.4 Cell vocabulary — pointer asserted (no drift)

| Vocabulary | Source of truth | Contract notes |
|---|---|---|
| Cell classes | `battery/report/classify.py` `CLASSIFICATIONS` | STRONG / STRUCTURAL / PARITY / WEAK / `insufficient_n`. STRONG/WEAK margin = `classification-delta` 0.10 (thresholds.yaml cal) vs the best comparator arm. STRUCTURAL only where family ∈ `STRUCTURAL_FAMILIES` {R1, R4} **and** a4 actually won (a4 loss surfaces WEAK, never masked). `insufficient_n` = attempted-but-unmeasured cell (probe no-data sentinel) — reported, never dropped, never enters the verdict (no vacuous pass). |
| Load-bearing flag | `battery/report/classify.py` `LOAD_BEARING_FAMILIES` | R1–R5, L1–L5, D3, D4 are load-bearing; L6, D2 are not (by code). The flag rides with each classified cell — never asserted in prose alone. |
| Verdict outcomes | `battery/report/verdict.py` `VERDICTS` | UNIQUE / MECHANISM-NOT-UNIQUE / WEAK-UNMITIGATED / INCONCLUSIVE. INCONCLUSIVE branch is driven by `matched_recall` (`trigger_fired` AND `subset_pct` < 0.5) — §3.2.1. |
| Report status | `profile.json` (plan §4 Data Model + §6 Interfaces) | `complete` \| `incomplete_missing_metrics` — incomplete blocks claim shipping; missing metrics are flagged, never fabricated. |

Doc rows consume these vocabularies verbatim; any mismatch between this
file and classify.py / verdict.py is drift and fails review.

### 3.3 Vendor rows (⚠️ not re-run by us — vendor claim)

| System | Mechanism (what it actually does) | Benchmark | Metric | Value | Notes / citation |
|---|---|---|---|---|---|
| MemPalace | graph/vector hybrid retrieval; held-out hybrid v4 adds a **Haiku reranker**; raw mode = semantic search, zero API calls | LongMemEval (their published run) | R@5 retrieval recall (as published by vendor) | 96.6% raw / 98.4% held-out | ⚠️ not re-run by us — vendor claim. Published-source link: https://github.com/mempalace/mempalace (benchmarks), https://www.mempalace.net/benchmarks. Variant semantics not confirmed by us (see §2 — do not head-to-head against any-hit numbers without confirming). |
| MemPalace | same | LoCoMo | QA / retrieval (as published) | 100% (vendor: "structurally guaranteed — top-k > sessions") | ⚠️ vendor claim. The "structurally guaranteed" framing needs the caveat that it follows from their design, not a measured head-to-head (research brief competitive position). |
| gbrain | hybrid FTS + vector + RRF + typed graph (people/companies/concepts namespaces, wikilinks, facts fences — **zero belief semantics**); local, single-user, markdown-first | LongMemEval `_s` (their run) | **any-hit R@5** (NOT official `recall_all@5`) | 97.6% (any-hit) | ⚠️ not re-run by us — vendor claim. **ERRATUM published 2026-08-31**: the headline used any-hit, not the official `recall_all@5`; corrected full-500 number not re-measured yet ("expect the corrected headline to be equal or lower"); gbrain-vector 97.4%, keyword/BM25 19.8%, hybrid+expansion 97.6% (Haiku query expansion = clean null result). Source: https://github.com/garrytan/gbrain-evals — `docs/benchmarks/2026-05-07-longmemeval-s.md` erratum block. **This erratum is the cautionary example for §7.** |
| gbrain | same hybrid+graph write path (own planted-gold corpus, Cat 35) | Cat 35 (gbrain's own corpus — **different corpus from Tortoise's W2 row; not head-to-head**) | salient-unit recall macro (as published) | 61.5% first-run → 70.2% re-anchored pre-wave → **88.1%** post-fix-wave (strict 82.1%) | ⚠️ not re-run by us — vendor claim. Fix-wave demonstration (their receipts preserve the bad first number). Honest delta 70.2→88.1 (published 61.5% baseline was 62 commits stale). Source: https://github.com/garrytan/gbrain-evals — `docs/benchmarks/2026-08-16-brainbench-cat35-transcript-distill.md`. |
| gbrain | Cat-34 harness (kta/false-fire/push/continuity) | Cat 34 (their corpus) | kta failure / false fire / push recall | 0.000 baseline post-fix-wave; **first run was 0.150 kta-failure, 0.023 claude-code false fire**; push recall 0.906 openclaw / 0.552 codex (not 1.0) | ⚠️ not re-run by us — vendor claim. First-run numbers kept in their receipts (README "0 failures" omits push recall + first-run history). Source: https://github.com/garrytan/gbrain-evals — `docs/benchmarks/2026-06-12-brainbench-memory.md`. |
| gbrain | PrecisionMemBench (vendored, own run) | PrecisionMemBench | first published default | **0.076** — low number stays in their table with a scores-drop-on-re-run banner | ⚠️ not re-run by us. **The "bad number published on purpose" precedent this repo adopts** (§8). 0.076 figure per epic #2080 context + gbrain-evals PrecisionMemBench row (external); in-repo evidence: raw-notes 10:20Z — PrecisionMemBench row carries a scores-drop-on-re-run banner and the audit removed a seed-time shortcut (research-brief adversarial verdict). Source: https://github.com/garrytan/gbrain-evals. |
| Supermemory | agent-memory server; ASMR self-repairing memory; publishes read-path **QA-acc ~99%** (ASMR flagged experimental-not-production by its own authors) | HaluMem-Medium (as cited by gbrain's HaluMem table) | **write-path** extraction recall | **41.5%** | ⚠️ not re-run by us. QA-acc ≠ R@k ≠ write-path recall — **the ~99% QA-acc number must never be compared head-to-head against retrieval recall rows** (§2). Source: HaluMem (arXiv 2511.03506) as cited in https://github.com/garrytan/gbrain-evals; research-brief metric-mixing note. |
| Mem0 | vector+graph hybrid memory (open-source agent memory); read-path self-reports | HaluMem-Medium (as cited by gbrain's table) | **write-path** extraction recall | **42.9%** | ⚠️ not re-run by us. Source: HaluMem (arXiv 2511.03506) as cited in gbrain-evals; research-brief. |
| Mem0 | same | LoCoMo (self-report) | QA accuracy, LLM-judged | 92.5% | ⚠️ vendor self-report, not re-run by us. QA-acc ≠ retrieval recall. Source: arXiv 2504.19413. |

## 4. What the table deliberately does NOT say

- **No "we beat X" anywhere.** Tortoise's sealed rows (write-path survival
  0.25/0.0, harness snapshot) are published as-is — they are **low** numbers
  relative to vendor write-path self-reports, and the reason (LLM extraction
  posture, null-reflex seam, strict semantics) is named in the row. There is
  no benchmark where a Tortoise win is claimed on a run that has not
  happened.
- **No cross-corpus head-to-heads.** gbrain's Cat 35 is not Tortoise's W2
  corpus; HaluMem is not LongMemEval; QA-acc is not R@k. Each row names its
  own benchmark and metric family (§2).
- **No 500-Q number until #2105 seals it.** The epic's headline assertion
  (`recall_all@5`, official semantics) is the W7-a deliverable; a placeholder
  number would violate the content validation and repeat gbrain's
  any-hit/recall_all mistake.
- **No why-layer claim until #2100 measures it.**

## 5. Tortoise receipts index (self-verification for the J7 auditor)

| Published number | Baseline file | Receipt file | Commit | Corpus hash | Judge pin |
|---|---|---|---|---|---|
| W2 write-path survival macro 0.25 / strict 0.0 (product lane) | `tests/eval/write_path/baselines/main.json` | `tests/eval/write_path/receipts/w2b-2026-09-03-first.json` | `20d3c4ba` | `sha256:52d4a742e7cea21ce9f1147cc3ec46a10436a6dd9e7fbc7ad22701d851d90dc8` | `w2-write-path-mechanical-v1` |
| W2 m2 CI lane macro 0.9722 / leakage 11 | `tests/eval/write_path/baselines/m2.json` | `tests/eval/write_path/receipts/w2b-m2-lane-2026-09-03.json` | `99324400` | same | `w2-write-path-mechanical-v1` |
| W3 harness product lane 7-metric snapshot | `tests/eval/harness/baselines/main.json` | `tests/eval/harness/receipts/w3h-llm-bpre-2026-09-04.json` | `e50ae316` | `sha256:ded9706426ba6b9ae541c8753ca5c6fbc22ed0555b559af2cd153fcf25ddff21` | `w3-volunteering-memory-mechanical-v1` |
| W3 harness m2 CI lane snapshot | `tests/eval/harness/baselines/m2.json` | `tests/eval/harness/receipts/w3h-m2-bpre-2026-09-05.json` | `16d67cab` | same | `w3-volunteering-memory-mechanical-v1` |
| W7 500-Q `recall_all@5` | — | **pending #2105** | — | — | — |
| Battery differentiation profile (§3.2) | `profile.json` (assembled by `battery/report/assemble.py`; producer #1416) | **pending #1416** | — | — | — |

The harness product-lane receipt carries a **reproducibility disclosure**:
LLM-lane runs do not reproduce byte-for-byte (re-runs jitter and are
compared directionally vs the committed snapshot); the aggregate metrics +
committed baseline are the audit surface. W2-b's receipt pins the runner
commit so the number reproduces (review F1 fix). Battery rows (when #1416
lands) cite `profile.json` + the `matched_recall` block — the receipt's
`matched_recall` fields are the §3.2.1 control line and are part of the
row's citation, not optional extras.

## 6. Where the discipline comes from

- **Benchmark discipline is the durable asset** (epic learnings item 4):
  sealed keys at the boundary, deterministic/offline arms, pinned judges,
  receipts, committed baselines gated in CI, corpus-bless, publishing bad
  numbers on purpose. The 97.6% "SOTA" headline is the **weak** part of
  gbrain's corpus (any-hit R@5 vs official `recall_all@5`) — never
  reproduced here.
- **R12 (metric confusion):** official `recall_all@5` semantics (NOT
  any-hit) is the assertion; this file must not mix variants either.
- gbrain's own comparison table rules (adopted verbatim in §1): neutral
  cited tables; mechanism-named analysis; where the system loses, the loss
  and the reason stay in.

## 7. Errata policy (repo convention — annotate, never silently edit)

1. **Annotate, never silently edit.** Any correction to a published number,
   a receipt, or this file is an *annotated update*: a dated block naming
   the change, the reason, and the divergence record. Historical numbers are
   preserved — in baselines via the `history` array, in receipts by
   immutability, in this file via the errata log below. **A silent edit to a
   published number is a process violation** (E2E-8 negative edge).
2. **Cautionary example (gbrain):** the 97.6% LongMemEval "SOTA" headline
   used any-hit R@5; the official `recall_all@5` is stricter (`all(doc in
   recalled_docs …)`). gbrain published an erratum block (2026-08-31) and
   preserved the historical claim; the corrected number is still pending
   re-measurement. Tortoise's own LongMemEval reports publish their
   semantics divergences (`dataset_audit.py`, 4 recorded) alongside every
   number — **the any-hit vs `recall_all@5` distinction is called out
   explicitly in every report and in this table** (§2, §3.1 500-Q row).
3. **Metric-variant mixing is an erratum trigger.** QA-acc ≠ R@k ≠
   `recall_all@k`; a report or row that mixes variants without labeling
   them must be corrected by annotation, not by editing the number.
4. **Filing:** open an issue or annotate in place with a dated block +
   citation. When a pending row above is filled (#2105/#2100 receipts, W5
   fix-wave re-run), the fill is an annotated update — the PENDING status
   line is replaced by a dated fill note that names the receipt, and the
   pre-fill state remains visible in the git history (never force-pushed
   away).

### Errata log

| Date | Change | Reason | Divergence record |
|---|---|---|---|
| 2026-09-08 | Added reserved agent-reasoning-battery row contract (#2523): §0 status row, §2 battery metric-semantics rows, §3.1 reserved PENDING row, §3.2 reserved-row block (family rows + matched-recall rule + verdict look-up + vocabulary pointer), §5 receipts pending row, §10 checklist rows. Vendor rows renumbered §3.2 → §3.3. | Pre-register the battery differentiation-profile row shape before #1416 verdict receipts exist (issue #2523 O/I/T) — no post-hoc metric sculpting at fill time. | Additive; no published number changed; no row filled. Fills gated on #1416 receipts (annotated update per §7). |
| 2026-09-12 | **Amended §3.2.1**: added the `trigger_population` contract row — `trigger_population` = `{a1, a2, a2b, a3, a4}` (retrieval-capable comparators); `a0` excluded from the trigger, retained as its positive control and as a published profile row annotated *recall-confounded*. The prior §3.2.1 wording ("any arm ≥ 0.10 F1 short … fires the trigger") is **annotated, not rewritten**: the original text is preserved above and the `tolerance` row now carries an inline "amended 2026-09-12, #3327" marker. | Record the trigger-population decision (#3327; research brief `docs/research/2026-09-12-matched-recall-a0-control.md`, confidence 0.85) as a pre-registered amendment declared **before any comparator leg runs**. An a0-inclusive trigger fires on every run (a0 recall is 0.0 by construction, `battery/arms/a0_plain.py:26-27`), making every differential run INCONCLUSIVE and leaving a four-verdict contract able to express exactly one verdict. | Additive contract-clarification; **no published number changed**; no row filled; no run executed (declared ex ante). |

## 8. Publishing bad numbers on purpose

- **Precedent (gbrain):** the 0.076 PrecisionMemBench default stays in their
  table with a scores-drop-on-re-run banner. **Precedent (gbrain Cat 34):**
  first-run 0.150 kta-failure / 0.023 false fire are preserved in receipts
  under the "0 failures" baseline.
- **Tortoise W2:** the product-lane write-path survival baseline is
  **macro 0.25 / strict 0.0** — a low first number, committed with its
  receipt (§5) and the first-published 0.3056 preserved in baseline history.
  It is **not suppressed and not variant-cherry-picked**: no "macro with
  echo lane" or "0.3056 with an unreproducible pin" is substituted.
- **Tortoise harness:** NULL-reflex rows (kta-failure 1.0, push 0.0) are
  published as-is with the seam state named — they do not claim a graded
  reflex that does not exist yet.
- **n=1 smoke successor:** the pre-epic n=1 smoke reports (wide CI at n=1)
  are superseded by the W7-a sealed run, which publishes with honest
  semantics and a validated receipt — not by re-using the smoke number as a
  headline.
- **No bad number is ever deleted.** Fix-waves produce new rows + receipts;
  history keeps the old ones.

## 9. gbrain-evals adapter scorecard — feasibility assessment

**Question (issue indicator 4):** can Tortoise publish a scorecard row
*through gbrain-evals' own harness* ("their moat becomes our yardstick")?

**Their interface (from research brief/raw-notes, 2026-08-31):** gbrain-evals
is a TypeScript harness. LongMemEval runner: `eval/runner/longmemeval.ts`
(embed cache, aggregate, batch shell). Cat-34 brainbench: per-seam adapters
(`adapters/openclaw.ts`, `adapters/claude-code.ts` driving the real
`UserPromptSubmit` hook over real IPC, `adapters/codex.ts`) over a
one-DB-per-run hermetic replay with sealed gold, ~15% holdout, BPRE, and
receipts.

**Feasibility verdict: NOT cheap today → NOT adopted (kept as an ADDITION —
Alignment Alternative 3 — never a substitute for the in-repo numbers).**

- A Tortoise row through their harness means implementing their
  TS-adapter contract against Tortoise's Python/FalkorDB stack over a real
  seam — a cross-stack driver, not a config change.
- Their own 35-agent audit (239 findings, 17 critical) found shared metric
  helpers wrong for everyone (recall > 1.0, precision over returned length,
  LLM-judge renormalization), four runners crashing against a pinned gbrain,
  and confounded A/B cells — the harness machinery is still under
  remediation; building on it now inherits that churn.
- Their Cat-34 adapters are product-seam-specific (their IPC hooks); there
  is no documented neutral "bring your own memory" adapter contract to
  target.

**If adopted later:** the row is mechanism-named + cited, explicitly an
ADDITION ("scorecard through their adapter, for yardstick/marketing"), and
the in-repo numbers remain the canonical evidence. Re-assess when (a) their
runner stabilizes post-audit and (b) the W7-a receipt exists to mirror.

## 10. Content-validation checklist (S14 / E2E-8 publication half)

| Rule | State |
|---|---|
| Mechanism-named rows (what each system DOES) | ✅ §3 |
| Neutral cited tables | ✅ §3, §5 |
| NO win claims on benchmarks not run | ✅ (checked at review: vendor rows ⚠️, Tortoise pending rows say PENDING) |
| Metric-mixing explicitly flagged (QA-acc ≠ R@k ≠ recall_all@k) | ✅ §2 |
| Numbers linked to validated receipts naming commit + corpus hash + judge pin | ✅ §5 (W7-a pending #2105) |
| Bad numbers published on purpose, never suppressed | ✅ §8 (0.25/0.0 with receipt; NULL-reflex rows named) |
| Errata policy documented (annotate, never silently edit) | ✅ §7 |
| Reserved battery-row contract (#2523) — §3.2 rows all PENDING, 0 win claims on a benchmark not run; fills gated on #1416 receipts + matched-recall block | ✅ (checked at review) |
| Battery cell/verdict vocabulary asserted to `battery/report/classify.py` + `verdict.py` (STRONG/STRUCTURAL/PARITY/WEAK/`insufficient_n`; UNIQUE/MECHANISM-NOT-UNIQUE/WEAK-UNMITIGATED/INCONCLUSIVE) — no drift (§3.2.4) | ✅ pointer asserted |
| gbrain-evals adapter scorecard feasibility assessed | ✅ §9 |
| **Negative case: a row claiming a win on a benchmark not run FAILS validation** | ✅ — no such row exists; any future one fails review (E2E-8 negative edge) |
