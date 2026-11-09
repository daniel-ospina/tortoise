---
title: "Epic #7949 — Pipeline Verification (epic-verify)"
type: engineering
domain: platform
doc_status: live
created: 2026-11-07
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: extractor v4 capstone, epic pipeline, review gates
issue: daniel-ospina/tortoise#7949
stage: epic-verify
---

# Epic Verification — #7949

**Inputs:** `00-align.md` · `01-research-brief.md` · `02-scope.md` · `03-test-design.md` (#11454) ·
`04-plan.md` · children `#10982`–`#10990`, `#11484`, `#11496`.

---

## Step 1 — Artifact completeness

| artifact | present | note |
|---|---|---|
| strategy alignment decision (`epic-align`) | ✅ | `00-align.md`, **rev 9**, gate **`NO ISSUES FOUND`** |
| research brief (`epic-research`) | ✅ | `01-research-brief.md`, canonical 5 sections + `## Raw Notes`; **GATE B `NO ISSUES FOUND`**, GATE A cosmetic-only |
| scope + high-level E2E (`epic-scope`) | ✅ | `02-scope.md` — 9 in-scope items, 8 E2E cases, D-S1…D-S5, value map, complexity |
| integration-surface map (Test-Design Gate) | ✅ | `03-test-design.md`, filed as **#11454** (17 surfaces + the CI tier rule) |
| epic plan, 8 substeps (`epic-plan`) | ✅ | `04-plan.md` §1–§8 |
| dependency-ordered issue list (`epic-decompose`) | ✅ | `#10982`–`#10990` + `#11484` + `#11496`, all sub-issues of `#7949` |
| per-issue + MECE review records | ✅ | `#7949` comment (Stage 5), plus per-issue blocker corrections on `#10984`/`#10985`/`#10986` |
| capstone verification issue | ✅ | **#11496** |

**8/8 present.** ⚠️ One artifact is **superseded rather than deleted**: `00-plan.md` (the first cut, written before
research). Its §5 is a pointer to #11454 and its children table is stale; it is retained because deleting a plan
that a comment references would break the trail.

---

## Step 2 — Cross-phase coherence

| check | source → target | verdict |
|---|---|---|
| Strategy → Scope | `00-align` PROCEED + the widened bar → `02-scope` boundaries | ✅ the widening is explicit; the mechanism/evidence cut survived into scope |
| Scope → Plan | 9 in-scope items → `04-plan` §1–§7 | ✅ but **one drift**: the scope has **8** E2E cases, the plan **10** (E2E-9/E2E-10 added at the plan gate with no scope parent — recorded in §8.1, and the owner of both is `#10983` per the decompose gate) |
| High-level → Detailed E2E | `02-scope` E2E-1…8 → `04-plan` §7 | ⚠️ **1:1 for 1–8; 9–10 are plan-born.** The plan says so explicitly rather than hiding it |
| Plan → Issues | `04-plan` §8.4's A1–A9 → the nine amendments | ✅ applied to all nine bodies (prepended `## ⛔ AMENDMENT` blocks) — but the per-issue gate found **10 residual divergences** the amendments did not reach, recorded on `#7949` |

---

## Step 3 — E2E alignment

`02-scope` (8) → `04-plan` §7 (10) → issue references.

| case | detailed counterpart | issue |
|---|---|---|
| E2E-1 hierarchy + rollup | ✅ | #10984 (+ #11484 for the predicate) |
| E2E-2 attach-not-mint | ✅ | #10985 |
| E2E-3 chain-join + cross-batch | ✅ | #10986 |
| E2E-4 subjects | ✅ | #10987 |
| E2E-5 reproducible from the door | ✅ | #10982 + #10983 |
| E2E-6 volume without fact loss | ✅ | #10989 |
| E2E-7 closed claims re-derive | ✅ | #10990 |
| E2E-8 typed attribution | ✅ | #10988 |
| **E2E-9 retry/replay (money)** | ✅ | ⛔ **no owner** — decompose gap #1, assigned to #10983 |
| **E2E-10 `BLOCKED(402)`** | ✅ | ⛔ **no owner** — same gap |

**8/10 chained; 2 gaps named and assigned.** Every Tier-E case has a designated CI contract twin *in the plan*; the
twin **builders** for 9 and 10 are the two gaps.

---

## Step 4 — Review-gate audit (the honest table)

| phase | gate | status | evidence |
|---|---|---|---|
| `epic-align` | strategy review | ✅ **6 cycles** → `NO ISSUES FOUND` | `00-align.md` §6 (6×P1 + 13×P2 fixed) |
| `epic-research` | research brief review | ✅ **GATE B `NO ISSUES FOUND`**, GATE A cosmetic-only → fixed | `01-research-brief.md` |
| `epic-scope` | scope review | ✅ **3 cycles** → cosmetic-only, all fixed | `02-scope.md` §"review" |
| `epic-scope` | **`duplication-architecture`** | ✅ ran, **`ISSUES — DEGRADED`** (registry 401); verdicts recorded, **not** fix-looped | `02-scope.md` §"verdicts recorded" |
| Test-Design Gate | issue filed with the full map | ✅ **#11454** | — |
| UX Design Gate | `UX_RATING` determination | ✅ **low**, no routing (below the ≥ medium threshold) | `02-scope.md` §3 |
| `epic-plan` §1–4 | design gate | ✅ → 8 findings fixed | `04-plan.md` |
| `epic-plan` §5 | architecture gate | ✅ → **15** findings fixed | `04-plan.md` §5 |
| `epic-plan` §6–7 | contracts + detailed E2E gate | ✅ → **12** findings fixed | `04-plan.md` §6/§7 |
| `epic-plan` §8 | coherence + risk gate | ✅ → **5** findings fixed | `04-plan.md` §8 |
| `epic-plan` §5 | **`duplication-architecture`** | ✅ ran, `ISSUES` — **2×P0**, verdicts folded | `04-plan.md` §8.5 |
| `epic-plan` §8 | **`duplication-architecture` re-check** | ✅ ran, `ISSUES` — **1×P0 + 3×P1**, folded | `04-plan.md` §5.3a |
| `epic-decompose` | per-issue gate | ✅ ran — **5 issues failing**, table on `#7949` | `#7949` Stage-5 comment |
| `epic-decompose` | MECE gate | ✅ ran — **`ISSUES: 4`** (2 gaps, 1 overlap, 1 cycle) | same comment |
| Capstone Verification Gate | issue filed | ✅ **#11496** | — |

### ⚠️ Deviations, declared rather than buried

| # | deviation | why | what it costs |
|---|---|---|---|
| **D1** | **Scope and plan substeps were gate-grouped**, not one gate per substep (plan: 4 gates over 8 substeps; decompose: 1 gate over 10 issues) | each gate is a billed dispatch, and substeps 1–4 are thin here (no UI, no schema migration, no new journey) | a per-substep gate could catch drift *between* adjacent substeps that a grouped gate sees at once — **mitigated**: all four plan gates ran, and the decompose gate reviewed **every** issue in a table |
| **D2** | The **human approval gates** at scope were satisfied by this session's presentation + the owner's "proceed"/"ok"/"go", not by a `human_approval` step | enforcement was session-escaped earlier (recorded) and re-requesting approval for each re-entry would have stalled the lane | the approval is in the transcript, **not** in the enforcement audit — an auditor relying on `enforcement.jsonl` will not see it |
| **D3** | The **research stage's graph write-back** could not land | the hosted graph is at its **25,000-point cap** (`-32006`), and the CLI is out of sync with the tool schema (`#11076`) | the research findings are in the brief and on `#7949`, but **not** in the graph — so the next lane cannot find them by searching memory |
| **D4** | `00-plan.md` retained, not deleted | it is referenced by comments and by the children | a reader must follow the "SUPERSEDED" header at its §5 |

---

## Step 5 — Final decision

```text
## Epic Verification Result

Epic:          #7949 — extractor v4 CAPSTONE
Status:        READY FOR DECOMPOSITION WORK — NOT ready to close

Artifacts:     8/8 present
Coherence:     3 issues found (1 scope↔plan E2E count drift, 1 stale-blocker P0, 10 residual issue divergences)
E2E Alignment: 8/10 chained; 2 gaps (E2E-9, E2E-10) named and assigned to #10983
Review Gates:  15/15 ran (4 of them distinct duplication passes); 2 declared deviations (D1 gating granularity, D2 approval channel)
```

**Decision: PROCEED** — into the children, **not** into closing the epic. The planning pipeline is complete and
gated; the epic's ten rows are unproven, which is the whole point of a capstone.

⛔ **Four things must be true before the epic can close**, and none is a plan gap:

0. **E6 is carried** — the sixth criterion registered by **`#11083`** (per-pack `declared → writable → written`
   census, before-arm **0 written on Object→Object**). It was **missing from this pipeline's artifacts** (built on
   E1–E5 + A1–A5) and is now recorded in `04-plan.md` §7a and the scope's acceptance table. ⚠️ **E6 is `RED` by
   construction today**, and for the same reason as item 1 below — the wiring — **not** because of pack content.

1. The **wiring residual** (found at the decompose gate): `pack_hierarchy.py`'s applier ships and `extractor_v2.py`
   **never calls it** — no issue owns that call site. **This is the epic's real blocker**, not any closed issue.
2. The **quota** (`#10976`): the capture door returns 402 at the cap, so A1/A2/A3/A5 report `BLOCKED — capture
   refused 402` until it moves.
3. The **D3 reopen** (owner) decides whether A4 has a target at all.

### What this pipeline bought, stated plainly

Not "the plan". The **findings** it forced — a false premise that rejected the obvious alternative; an unrecorded
reversal of a decided ruling; a **fixture G1 that refuses its own committed record**; an enum invariant that was
arithmetically false; a retry rule that would have **double-minted**; a schema shared with a **second bundle**; a
derivation that would have landed on a set the verdict does not read, and would have produced **live-only edges
dropped by every rebuild**; and a **three-day-stale blocker set** that re-measured into the epic's true blocker.
Sixty-plus findings, none of them findable by re-reading the draft.
