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
| research brief (`epic-research`) | ✅ | `01-research-brief.md`, canonical 5 sections + `## Raw Notes`; **GATE B `NO ISSUES FOUND`**, GATE A cosmetic-only — ⚠️ those verdicts are recorded in the **`#7949` pipeline comment of 2026-11-08T00:17:36Z**, not in the brief (whose only in-file trace is its own *"[updated 2026-11-05 — appended, review cycles 2–3]"* line) |
| scope + high-level E2E (`epic-scope`) | ✅ | `02-scope.md` — 10 in-scope items, 8 E2E cases, D-S1…D-S5, value map, complexity |
| integration-surface map (Test-Design Gate) | ✅ | `03-test-design.md`, filed as **#11454** (18 surfaces — 1–17 plus `10a` — + the CI tier rule). ⚠️ The issue's own body still says **17 / 8 CI / 9 Tier E** and names neither `10a` nor E6; the correction (18 rows = 7 CI + 10 Tier E + 1 split, `10a` = E6, and the re-derived 155.5 s family row) is **relayed on the issue** rather than silently assumed |
| epic plan, 8 substeps (`epic-plan`) | ✅ | `04-plan.md` §1–§8 |
| dependency-ordered issue list (`epic-decompose`) | ✅ | `#10982`–`#10990` + `#11484` + `#11496`, all sub-issues of `#7949` |
| per-issue + MECE review records | ✅ | `#7949` comment (Stage 5), plus per-issue blocker corrections on `#10984`/`#10985`/`#10986` |
| capstone verification issue | ✅ | **#11496** |

**8/8 present.** ⚠️ One artifact is **superseded rather than deleted**: `00-plan.md` (the first cut, written before
research). The supersession is **document-level** — `doc_status: superseded`, a banner under its title, and its
A4 passages rewritten as pointers — because a §5-only marker left the rest of the file reading as current. It is
retained because deleting a plan that comments reference would break the trail.

---

## Step 2 — Cross-phase coherence

| check | source → target | verdict |
|---|---|---|
| Strategy → Scope | `00-align` PROCEED + the widened bar → `02-scope` boundaries | ✅ the widening is explicit; the mechanism/evidence cut survived into scope |
| Scope → Plan | 10 in-scope items → `04-plan` §1–§7 (item 10 → §7a) | ✅ but **one drift**: the scope has **8** E2E cases, the plan **10** (E2E-9/E2E-10 added at the plan gate with no scope parent — recorded in §8.1, and the owner of both is `#10983` per the decompose gate) |
| High-level → Detailed E2E | `02-scope` E2E-1…8 → `04-plan` §7 | ⚠️ **1:1 for 1–8; 9–10 are plan-born.** The plan says so explicitly rather than hiding it |
| Plan → Issues | `04-plan` §8.4's AM1–AM9 → the nine amendments | ✅ applied to all nine bodies (prepended `## ⛔ AMENDMENT` blocks) — but the per-issue gate found **10 residual divergences** the amendments did not reach, recorded on `#7949` |

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
| E2E-8 things and events carry their declared labels (per layer) | ✅ | #10988 |
| **E2E-9 retry/replay (money)** | ✅ | ⛔ **no owner** — decompose gap #1, assigned to #10983 |
| **E2E-10 `BLOCKED(402)`** | ✅ | ⛔ **no owner** — same gap |

**8/10 chained; 2 gaps named and assigned.** Every Tier-E case has a designated CI contract twin *in the plan*; the
twin **builders** for 9 and 10 are the two gaps.

---

## Step 4 — Review-gate audit (the honest table)

| phase | gate | status | evidence |
|---|---|---|---|
| `epic-align` | strategy review | ✅ **9 cycles recorded**, 5 of them P-counted → `NO ISSUES FOUND` at cycle 9 | `00-align.md` §6 (7×P1 + 17×P2 fixed across cycles 1/2/5/6/7; cycle 8 cosmetic-only; cycle 3 never run and cycle 4 caught by the decompose gate — both rows record it) |
| `epic-research` | research brief review | ✅ **GATE B `NO ISSUES FOUND`**, GATE A cosmetic-only → fixed | **not in `01-research-brief.md`** — that file carries the artifact, not the gate record; the record is the **`#7949` pipeline comment of 2026-11-08T00:17:36Z**, row `01-research-brief.md \| epic-research \| ✅ GATE B \`NO ISSUES FOUND\`, GATE A cosmetic-only` |
| `epic-scope` | scope review | ✅ **3 cycles** → cosmetic-only, **the findings fixed in later stages** | **not in `02-scope.md`** (that file carries no scope-review section by design — its last line says the two reviews are recorded after confirmation). The record is the `#7949` pipeline comment of **2026-11-08T00:17:36Z**, whose row reads: **`epic-scope ×3 + duplication-architecture` — ✅ cosmetic-only; D&A `ISSUES — DEGRADED` (registry 401), verdicts recorded** — i.e. the comment records **cosmetic-only**, and does not itself say "all fixed" |
| `epic-scope` | **`duplication-architecture`** | ✅ ran, **`ISSUES — DEGRADED`** (registry 401); verdicts recorded, **not** fix-looped | `02-scope.md` §"verdicts recorded" |
| Test-Design Gate | issue filed with the full map | ✅ **#11454** | — |
| UX Design Gate | `UX_RATING` determination | ✅ **low**, no routing (below the ≥ medium threshold) | `02-scope.md` §3 |
| `epic-plan` §1–4 | design gate | ✅ → 8 findings fixed | **not a total in `04-plan.md`** — the record is the same pipeline comment (`2026-11-08T00:17:36Z`), row `04-plan.md \| 4 substep gates + D&A ×2 \| ✅ 40 findings fixed, incl. 3 P0`; the per-gate split (8 / 15 / 12 / 5 = 40) is this audit's accounting, not a per-gate record |
| `epic-plan` §5 | architecture gate | ✅ → **15** findings fixed | same pipeline-comment row; `04-plan.md` §5 carries the findings themselves, no per-gate total |
| `epic-plan` §6–7 | contracts + detailed E2E gate | ✅ → **12** findings fixed | same pipeline-comment row; `04-plan.md` §6/§7 carry the findings, no per-gate total |
| `epic-plan` §8 | coherence + risk gate | ✅ → **5** findings fixed | same pipeline-comment row; `04-plan.md` §8 carries the findings, no per-gate total |
| `epic-plan` §5 | **`duplication-architecture`** | ✅ ran, `ISSUES` — **2×P0**, verdicts folded | `04-plan.md` §8.5 |
| `epic-plan` §8 | **`duplication-architecture` re-check** | ✅ ran, `ISSUES` — **1×P0 + 3×P1**, folded | `04-plan.md` §5.3a |
| `epic-decompose` | per-issue gate | ✅ ran — **8 of 10 children fail it, 1 partly, 1 mostly**; every correction is named per issue | `#7949` Stage-5 comment (2026-11-08T00:01:58Z, "Gate A — per-issue verdicts (10 issues)") |
| `epic-decompose` | MECE gate | ✅ ran — **`ISSUES: 4`** (2 gaps, 1 overlap, 1 cycle) | same comment |
| Capstone Verification Gate | issue filed | ✅ **#11496** | — |

### ⚠️ Deviations, declared rather than buried

| # | deviation | why | what it costs |
|---|---|---|---|
| **D1** | **Scope and plan substeps were gate-grouped**, not one gate per substep (plan: 4 gates over 8 substeps; decompose: 1 gate over 10 issues) | each gate is a billed dispatch, and substeps 1–4 are thin here (no UI, no schema migration, no new journey) | a per-substep gate could catch drift *between* adjacent substeps that a grouped gate sees at once — **mitigated**: all four plan gates ran, and the decompose gate reviewed **every** issue in a table |
| **D2** | The **human approval gates** at scope were satisfied by this session's presentation + the owner's "proceed"/"ok"/"go", not by a `human_approval` step | enforcement was session-escaped earlier (recorded) and re-requesting approval for each re-entry would have stalled the lane | the approval is in the transcript, **not** in the enforcement audit — an auditor relying on `enforcement.jsonl` will not see it |
| **D3** | The **research stage's graph write-back** could not land | the hosted graph is at its **25,000-point cap** (`-32006`), and the CLI is out of sync with the tool schema (`#11076`) | the research findings are in the brief and on `#7949`, but **not** in the graph — so the next lane cannot find them by searching memory |
| **D4** | `00-plan.md` retained, not deleted | it is referenced by comments and by the children | the risk was that a reader took it as current — **mitigated at the document level**, not at its §5: `doc_status: superseded`, a supersession banner under its title, and its A4 passages rewritten as pointers to `02-scope.md` item 5 / `04-plan.md` §8.4 **AM7** |
| **D5** | **`file:line` citations were keyed to line numbers, and line numbers rot** | every forward merge into this branch moves them, and `tests/test_docs_citation_refs.py` cannot catch it — by its own docstring it checks line **presence**, not semantic correctness, so "a shift onto a DIFFERENT non-blank line stays green, and the pointer then silently names whatever row now sits there" | a citation that names the wrong line reads as verified. **The policy this bundle now follows, stated here so the next merger applies it:** the **symbol is authoritative and the line number is a convenience** (`00-plan.md:27-30`) — cite `quota_refusal_payload`, `retry_failed_capture`, `REGISTERED_NON_BAR_ROWS`, the `on_demand:` register heading — and where a number is given, it is **as of head `afc1889a3f52`** (the tree the numbers were read from; this branch's own last commits are docs-only and move no code line — but the branch was afterwards **rebased onto `a577d0e25c`**, and `#11642`'s vector move, `#11646`'s date resolver and `#11677` shift `sdk.py`, `projection/__init__.py`, `search_engine.py` and `config/ci-surfaces.yml` under the cites, by 1–184 lines: the eight cites a review found drifted were re-pointed at `790ef4d1b1`, and two more here — `sdk.py`'s consume pair for `subject_binding_available` and `search_engine.py`'s definition of it, which the list above ALREADY records as re-pointed at `afc1889a3f52` and which drifted AGAIN with `search_engine.py` (+199/−15) in the same rebase. A cite re-pointed once is not safe from a later merge. The rule is unchanged and is why the drift was survivable — the symbol is authoritative; the number is as-of its head, and this sentence now says which). The numbers a review found wrong were re-pointed at that head (`hosted_api.py:18205`/`:18216`, `:1245-1254` 429+409 `Retry-After`, `:18131`/`:18159`/`:18717`, `:17654` `POST /v1/sessions`; `sdk.py`'s `extraction_mode` pair, `subject_binding_available`'s pair, `bind_point_subjects`' call site; `record_projection.py`'s `RelationUpsert`; `capstone_run.py`'s `if not frozen.get("sha256")`; `capstone_verdict_check.py`'s V2 loop; `EXTRACTOR-V4-ARCHITECTURE.md:1400` D6 / `:1095` "D6 as ruled" / `:2085` §13.8 Q1; `config/ci-surfaces.yml`'s `# ── THE NON-GATING LANE (`on_demand:`) ──` block); **the sweep is still incomplete — every citation was not re-read in every file**, and that residual is named here rather than left for a reader to discover. Nine of the ten re-pointed cites were found by reading the sentence beside the number, not by any guard: only the bundle's own `sdk`/`edges`/`extractor_v2` cites have a sweeping test. |

---

## Step 5 — Final decision

```text
## Epic Verification Result

Epic:          #7949 — extractor v4 CAPSTONE
Status:        READY FOR DECOMPOSITION WORK — NOT ready to close

Artifacts:     8/8 present
Coherence:     12 findings = 3 classes (1 scope↔plan E2E count drift, 1 stale-blocker P0, 10 residual issue divergences)
E2E Alignment: 8/10 chained; 2 gaps (E2E-9, E2E-10) named and assigned to #10983
Review Gates:  15/15 ran (3 of them distinct duplication passes); 5 declared deviations (D1 gating granularity, D2 approval channel, D3 research write-back failure, D4 retained 00-plan.md, **D5 citations keyed to line numbers that rot on merge**)
```

**Decision: PROCEED** — into the children, **not** into closing the epic. The planning pipeline is complete and
gated; the epic's ten rows are unproven, which is the whole point of a capstone.

⛔ **Four things must be true before the epic can close**, and none is a plan gap:

0. **E6 is carried** — the sixth criterion registered by **`#11083`** (per-pack `declared → writable → written`
   census, before-arm **0 written on Object→Object**). It was **missing from this pipeline's artifacts** (built on
   E1–E5 + A1–A5) and is now recorded in `04-plan.md` §7a and as `02-scope.md` **in-scope item 10**. ⚠️ It is an
   in-scope item, **not** a criteria row: E6 has no row in D-S1's corpus matrix yet, unlike every E-row — recorded
   here rather than papered over. ⚠️ **E6 is `RED` by
   construction today**, and for the same reason as item 1 below — the wiring — **not** because of pack content.

1. The **wiring residual** (found at the decompose gate): `pack_hierarchy.py`'s applier ships and `extractor_v2.py`
   **never calls it** — no issue owns that call site. **This is the epic's real blocker**, not any closed issue.
2. The **quota** (`#10976`): the capture door returns 402 at the cap, so A1/A2/A3/A5 report `BLOCKED — capture
   refused 402` until it moves.
3. **A4 now has a target** — the D3 question it was blocked on is answered (owner, 2026-11-09: classification on
   by default; the declared kinds are the point). A4 asks whether **things and events carry the labels the
   ontology declares for them**, reported **per layer** (things / events / claims) — because the old framing
   counted labels on the claim layer, the one layer that is deliberately uniform, and so could never fail.

### What this pipeline bought, stated plainly

Not "the plan". The **findings** it forced — a false premise that rejected the obvious alternative; an unrecorded
reversal of a decided ruling; a **fixture G1 that refuses its own committed record**; an enum invariant that was
arithmetically false; a retry rule that would have **double-minted**; a schema shared with a **second bundle**; a
derivation that would have landed on a set the verdict does not read, and would have produced **live-only edges
dropped by every rebuild**; and a **three-day-stale blocker set** that re-measured into the epic's true blocker.
Sixty-plus findings, none of them findable by re-reading the draft.
