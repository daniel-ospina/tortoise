---
title: "#8503 — the planted operator edges: measured before/after, the mechanisms, and the mutation arm"
type: log
domain: platform
doc_status: live
created: 2026-10-24
subjects.team: epistemic-team
aboutSubjects: Tortoise extractor v4
aboutObjects: "#8503, #7949, #2552, #3679, #2514, #9077, EXTRACTOR-V4-ARCHITECTURE.md"
---

# `#8503` — planted operator edges: 5/14, every remaining miss attributed, and the mechanisms behind them

**Measured at:** `a999be41e` (the lane's base) with the fix in the working tree, 2026-10-24.
E4's own gate (`tools/operator_receipt_check.py`) is the acceptance instrument; the
receipts this document cites are `tests/eval/write_path/receipts/w2b-operator-2026-10-24-8503-{a,b,preleg}.json`
and all three exit **EVIDENCE VALID**.

> **Verdict: 5/14 in-14 — the SAME number the issue's own pre-fix run measured, and NOT the
> 14/14 the criterion asks for. This PR does not claim a gain on the criterion number.**
> What it does establish, on committed artifacts: (a) **endpoint recall rose 7/15 → 11/15**
> planted edges with both anchors present (11–13 on the fixed-tree receipts, 7 on the pre-fix
> probe) — the systematic part, upstream of the edge; (b) **the extractor logged no OPERATOR-PASS drop in any run**
> — and that is the whole of the zero: it is NOT a clean bill for the write path (a commit-seam or a
> failed snapshot is invisible to it, §1) and it does NOT make every `edge_missing` a selection miss,
> because the SUPERSEDE row's is a refusal in the replace channel (§3.2b, §3.2e); (c) row-level churn is
> two-way and inside sampling noise — **2** rows flipped red→green in BOTH fixed-tree runs
> (`wp04_aurora_perf_op_02`, `wp05_retro_writeup_op_01`), **1** flipped green→red in both
> (`wp07_bluepeak_followup_op_03`), and counting a flip in *at least one* run gives 5 up and 2
> down (§2, §5). The deliverable is the ATTRIBUTION, not a moved needle.

---

## 1. The instrument, and why the missing warnings were the actual blocker

`#8503` could not attribute its own misses: an `edge_missing` verdict (both endpoints
present, no qualifying edge) has **two** mechanisms — the model never *formed* the edge
(selection) or the seam *dropped* one it emitted (a write-path bug) — and the receipts
carried no extraction warnings at all. That ambiguity is what the issue was filed on.

`runner` now records the extractor's own warnings per session (`extraction_warnings`) and
derives a corpus-level `operator_audit.drop_warnings` that separates the two classes:

| class | markers (refusal TAILS, never a shared prefix) | what a count means |
|---|---|---|
| **operator drops** | `operator dropped:`, `operator with unknown op_type`, `non-dict operator entry`, `endpoint NOT minted`, `MITIGATES target edge not emitted` | the operator pass refused an edge the model emitted |
| **supersede refusals** | `matches no S3 prior`, `is the point itself`, `supersedes itself`, `is ambiguous (`, `matches no S3 prior or same-session point`, `non-dict retraction ref`, `empty retraction ref`, `does not resolve to an S3 search result`, `lifecycle=superseded with no supersedes ref`, `with no surviving point id` | the replace channel dropped a CORRECTS/supersession record |

Measured on the fixed tree: **`operator_drops = 0` in every run that carries the field** (the `preleg` receipt predates
it and records only the pre-narrowing refusal class). ⚠️ The column counts
**warning LINES, not operators**: one operator whose `src` names an emitted entity logs both
`endpoint NOT minted` (the mint refusal) and `operator dropped: src/dst did not resolve` (the
pass consuming it), so a non-zero count over-reports — only the `== 0` test is sound. Five
defects in this discriminator were found in review and fixed before merge (the last two are the
Round 6/7 paragraph in §5.1 — they are defects of this same marker set):

1. the first cut merged the two marker sets and reported "5 operator drops" on a run where
the operator pass dropped nothing — five *entity-lifecycle* notices. Pinned by
`test_operator_drop_warnings_separates_the_replace_channel`.
2. the refusal set keyed on the `point supersedes=` **prefix**, which the recall leg's own
SUCCESS warning also carried — counting the one resolution that worked as a drop. The
markers are now refusal TAILS and the leg's success line is a distinct prefix, pinned by
`test_recall_leg_success_warning_is_not_a_refusal` (which runs the leg and classifies the
warning it actually emits, so a prose edit cannot satisfy it).
3. `non-dict operator entry … skipped` had NO marker, so a malformed operator — a drop —
left `operator_drops` at 0 while the run note said nothing had been dropped (round 3). It is
a marker now, and the mutation arm pins the class.

⛔ **A ZERO IS EVIDENCE ABOUT THE OPERATOR PASS AND NOTHING ELSE.** Two seams are outside
this instrument: the commit layer (`commit_ops.apply_payload_operators`) logs its skips
through `_logger` with no warnings sink on the capture, and a session whose snapshot failed
to read produces no warnings at all. (A third seam this note used to carry — a point-level
`supersedes` ref on a point that FOLDS — is **closed** by #9906: the ref is carried onto the
surviving point and reaches the resolver, so a refusal there is counted like any other, and a
carried ref whose fold resolved no graph id is NAMED (`with no surviving point id`) and
counted too. Only those two remain invisible here.) The run note says so verbatim; the receipts committed
here predate that wording (their note still reads "not the write path dropping one") — the
counts are unchanged and the corrected note is what the shipped code emits.

**`supersede_refusals`: 2 (receipt A) and 4 (receipt B)** — and the way to read that number
is a lesson in itself. The receipts RECORD 6 and 7, because they were produced before review
round 2 narrowed two markers: `is not expressible in the Layer-1 payload` also matches the
`lifecycle='changed'` notice, which drops no `supersedes` ref at all (a changed entity's
record is still appended), and the self-supersede refusal (`is the point itself`) matched no
marker at all after round 1 removed the prefix form. Re-classifying the receipts' stored
`extraction_warnings` with the shipped markers gives **2 / 4**, of which one (run A) and two (run B) are
entity refs that did not resolve (the wp07 `all-at-once rollout` case — one of the
`SUPERSEDE` row's two mechanisms, §3.2b) and the rest are superseded-with-no-ref notices. The replace channel
refuses a *little*, not systematically; the earlier "5–7 per run" reading was the classifier
over-counting by ~3×.

## 2. The measurement — 15 planted edges, 14 in the criterion

`wp02_lumen_refactor_op_01` is the pre-registered exclusion (a capture failure in the
original corpus run), so the criterion denominator is **14**.

| # | planted edge | kind | endpoints `from`/`to` | BEFORE (a999be41) | AFTER run A | AFTER run B |
|---|---|---|---|---|---|---|
| 1 | `wp01_quarry_debug_op_01` | SUPPORTS | from:user / to:assistant | ❌ to_content_missing | ❌ edge_missing | ✅ edge_correct |
| 2 | `wp01_quarry_debug_op_02` | MITIGATES | from:assistant / to:assistant | ❌ edge_missing | ❌ to_content_missing | ❌ to_content_missing |
| 3 | `wp02_lumen_refactor_op_01` | MITIGATES | from:user / to:assistant | ❌ from_content_missing | ❌ from_content_missing | ❌ from_content_missing |
| 4 | `wp03_ember_design_op_01` | MITIGATES | from:user / to:user | ✅ edge_correct | ❌ edge_missing | ✅ edge_correct |
| 5 | `wp03_ember_design_op_02` | MITIGATES | from:user / to:assistant | ❌ to_content_missing | ✅ edge_correct | ❌ to_content_missing |
| 6 | `wp03_ember_design_op_03` | MITIGATES | from:user / to:assistant | ❌ to_content_missing | ❌ to_content_missing | ❌ to_content_missing |
| 7 | `wp04_aurora_perf_op_01` | SUPPORTS | from:user / to:assistant | ❌ to_content_missing | ❌ edge_missing | ❌ edge_missing |
| 8 | `wp04_aurora_perf_op_02` | MITIGATES | from:assistant / to:user | ❌ from_content_missing | ✅ edge_correct | ✅ edge_correct |
| 9 | `wp04_aurora_perf_op_03` | MITIGATES | from:user / to:assistant | ❌ edge_missing | ❌ edge_missing | ❌ edge_missing |
| 10 | `wp05_retro_writeup_op_01` | NEGATE | from:user / to:user | ❌ edge_missing | ✅ edge_correct | ✅ edge_correct |
| 11 | `wp05_retro_writeup_op_02` | SUPPORTS | from:user / to:user | ❌ from_content_missing | ✅ edge_correct | ❌ edge_missing |
| 12 | `wp06_quarry_rollout_op_01` | SUPPORTS | from:user / to:user | ❌ from_content_missing | ❌ to_content_missing | ❌ edge_missing |
| 13 | `wp07_bluepeak_followup_op_02` | NEGATE | from:user / to:user | ✅ edge_correct | ✅ edge_correct | ✅ edge_correct |
| 14 | `wp07_bluepeak_followup_op_03` | MITIGATES | from:user / to:user | ✅ edge_correct | ❌ edge_missing | ❌ edge_missing |
| 15 | `wp07_bluepeak_followup_op_04` | SUPERSEDE | from:user / to:user | ❌ edge_missing | ❌ edge_missing | ❌ edge_missing |

| arm | `edge_correct` (in-14) | `content_ok` /15 | `operator_drops` | `distractor_leakage` |
|---|---|---|---|---|
| BEFORE (unmodified tree, 1 diagnostic run — `before-run.json`) | **3** | 7 | — | — |
| BEFORE (2026-10-19 product-lane receipt) | 2 | 8 | — | 0 |
| BEFORE (the issue's own run, 2026-10-24T04:25Z) | **5** | — | not carried | — |
| AFTER run A | **5** | 11 | 0 | 1 |
| AFTER run B | **5** | 11 | 0 | 0 |
| AFTER `preleg` (before the recall leg) | **5** | 13 | — (field absent; NOT 0) | **2 — above the gold tolerance of 1** |

**Provenance of the BEFORE column, stated rather than implied.** `before-run.json` (committed
beside this file) is a DIAGNOSTIC PROBE artifact, not a runner receipt: it drives the same
seven sessions through the same `TortoiseSDK.capture_session` at the base commit and replays
`grading.grade_planted_operators`, but it carries no receipt schema and no `verdict`. It
grades all **15** edges (the AFTER receipts grade 15 and the criterion drops wp02, so 3/15
and 3/14 are the same measurement). The issue's own pre-fix run is a third artifact, a real
receipt, and reads **5/14** — which is why this document does not claim a gain at the
criterion number. The probe exists because no pre-fix receipt at the base commit does.

⚠️ **All three committed fixed-tree receipts carry `verdict: regression` /
`failure_origin: gate_regression`**
(salient survival 0.511 / 0.500 / 0.489 against the blessed 0.544, all below it). "EVIDENCE VALID" means the
receipt is a valid E4 artifact, NOT that the benchmark passed — the gate holds the two apart
on purpose. The pre-fix operator receipt (`...-2026-10-19.json`) also reads `regression`, so
this is a stale family-wide baseline rather than a regression this PR caused, and it is not
this issue's to re-bless.

**What the committed artifacts support, and nothing more.** `before-run.json` = 3 (content_ok
7); `...-8503-a.json` and `...-8503-b.json` = 5 and 5 (content_ok 11 and 11);
`...-8503-preleg.json` = 5 (content_ok 13); and the issue body itself reports 5 for its own
pre-fix run (that receipt is cited by the issue but is NOT in the tree — `results/` holds only
`7947-*`, so it is quoted, not verified). So the
committed range on the fixed tree is **5–5 at the criterion number**, and `content_ok` 11–13
against 7 before is the only movement the artifacts establish. Two further probe runs of this
lane (not committed — the instrument is `probe`, not the runner) read 6 and 6, and two
intermediate-run receipts read 5 and 2; they are named here so the reader can see why the
document says the metric is noisy, and they are deliberately NOT used to claim a bigger
range or a better median.

## 3. The mechanisms and what was done about each

### 3.1 Endpoint recall — the upstream half, and 7 of 28 endpoints

The issue scopes endpoint recall out ("the other 5 failures are NOT this issue"). They were
measured anyway, because an edge cannot form without both endpoints, and the measurement
found a **mis-scoped rule**, not a generic capability gap:

| turn role of the planted endpoint | endpoints | missing BEFORE |
|---|---|---|
| user | 20 | **2** (10%) |
| assistant | 8 | **5** (62.5%) |

The S2/S4 prompts carried: *"USER VS ASSISTANT (E3): an assistant suggestion/proposal is NOT
a user fact — do not emit it as a statement point unless the user confirmed it."* That rule
was written (#1535) for **user-personal-state** facts and read by the mapper as a blanket
ban on assistant turns. It contradicts the model it feeds:

* a Point carries **no speaker** at all — the prompt says so three lines above ("speaker is
  derived at read time from the source turn"), so the rule deletes content the graph could
  not attribute even if it wanted to;
* `EXTRACTOR-V4-ARCHITECTURE.md` §"Operators connect only epistemic targets" and
  `ONTOLOGY.md` §3.1 make a **hypothesis** exactly the kind of claim the belief layer exists
  to hold: it is a Point with a confidence that a later NAND can attack. Dropping "the
  sidebar feature is the prime suspect" deletes a belief, not chatter.

**Fixed (mis-scoped, not too strict):** the exclusion is narrowed to what asserts nothing —
process chatter and bare offers — and assistant-*asserted* claims (diagnosis, hypothesis,
derived conclusion, estimate, proposed direction) are licensed as Points. Two sibling
omissions the same measurement exposed are now named explicitly: **a question that asserts a
claim carries that claim** (`"what about services with no owner row, they will fall through
any ownership-based router?"`), and **an operational outcome that evidences a claim is a
durable claim** (the rollback that normalized traffic, the chaos run that showed no
duplicates) — the value filter's "strip, don't drop" list already covered a decision that
carries an issue id but not an outcome that carries a command and a clock time.

**Measured effect:** endpoints present 7/15 → 11/15 planted edges; assistant-turn endpoints
3/8 → 4–6/8 (varies by run).

### 3.2 Operator selection — the `edge_missing` half

`operator_drops = 0` in every run **that carries the field**. That is a statement about the
OPERATOR PASS and nothing wider: in the rows §3.2e classes as selection misses the model had both
claims and wired no operator between them. Two bounds apply. The commit-seam blind spot named in
§1 (a commit-layer drop is not observable from a receipt, so it is *not excluded*, only
unobserved). And the replace channel, which the SUPERSEDE row's `edge_missing` carries — an
extractor-side refusal this section does not own (§3.2b). §3.2e's row-by-row accounting is the
only summary; the sub-sections below are the mechanisms behind it — (a) is the one this PR FIXED
and so is not in the residual, (b)–(d) are the residual classes §3.2e counts.

**(a) kind selection — a displaced theory wired as IMPL, not NAND.** `wp05_retro_writeup_op_01`
(`NEGATE`) was `edge_missing` because the mapper emitted `IMPL` from the root-cause claim to
the theory it refuted ("the initial investigation blamed the search index"). The NAND rule
only named the *later-claim-contradicts-earlier-claim* shape and an explicit negation; a
retrospective's finding *displaces* a theory without negating its words. **Fixed** in the
shared `OPERATOR_SEMANTICS_RULE` (reaches S2 and S4 from one source): a theory the
conversation displaces is the claim **under attack** (dst), the finding is src, and "never
wire IMPL between a root cause and the theory it refuted". This row flipped to
`edge_correct` in **every** run of the fixed tree.

**(b) the replace channel — a drifted copy of the prior.** The `SUPERSEDE` row is the one the
issue names as `#3679`'s supersedes/`S3 RESOLVE` path. **`w2b-operator-2026-10-24-8503-preleg.json`
(committed, EVIDENCE VALID) shows the mechanism** — a run of this lane before any recall leg:
`point supersedes='We ship the lease fix all-at-once behind a single global kil'
matches no S3 prior — skipped (fail-open)`. The model **did** express the cross-session
reversal through the licensed channel; the ref differed from the wp06 prior by a leading word
AND a dropped tail ("…rather than a week of per-shard canary"), and
`_resolve_point_supersede` required exact normalized equality, so the CORRECTS edge — the only
carrier of a cross-session decision reversal — was dropped. (The two later receipts do NOT
carry that string: in those runs the model used the entity channel instead.)

**What was tried, and what ships — the honest version.** Four review rounds each found a hole
in a *tolerant* matcher, and the fourth settled the design:

| attempt | drift it tolerated | what it folded |
|---|---|---|
| exact equality (pre-fix) | nothing | the measured ref — the row's whole loss |
| token-COVERAGE ratio | one differing content token | a substituted entity, a changed number, a flipped role order, a changed scope word (R1) |
| ordered / contiguous span over frame-STRIPPED tokens | any content-token addition | an exchanged connective, a substituted subject, a deleted qualifier (R2); a substituted preposition once frame words were kept in the span (R3) |
| span over FULL tokens, additive tail allowed | an added **tail** | any trailing QUALIFIER the ref dropped — "…kill flag" over "…kill flag after the staging canary passes" (R4) |
| **shipped: frame-EDGE-trimmed token equality** | **frame words at the two ends only** | nothing reachable that changes what the claim asserts |

The reason the last one ships is not exhaustion, it is the failure mode: a false CORRECTS marks
a live belief `outdated` (`commit_ops.apply_supersessions` → `supersede()`), and there is no
un-supersede API. A tail-tolerant matcher cannot be made safe with a closed-class vocabulary,
because **trailing truncation is exactly what this model does** — the measured ref is one — so
every qualifier spelling it has not been taught is a silent corruption. The leg therefore
tolerates only an edge made of NON-load-bearing frame words (articles, pronouns, prepositions at
the very ends), and it refuses everything else:

* core-token **EQUALITY**, after trimming the NON-load-bearing frame words at each end of both
  sequences (`_EDGE_TRIMMABLE` = frame words MINUS negators, conditions, connectives, dates,
  clock units and temporal-scope adverbs). Everything interior is compared token-for-token, so a different number, entity,
  scope word, preposition, connective or role order refuses — and so does a negated or gated
  version of the same claim, because those words are never trimmed and therefore sit inside the
  compared core. Negation and condition need no separate gate; that is stated in the code
  rather than left as a reader's inference;
* no **substituted subject**: pronouns ARE trimmable at an edge, so a pronoun ref must agree
  with any pronoun the prior names (a one-sided pronoun is the measured drift's shape and is
  allowed);
* a **unique** match (0 or 2+ candidates fail open with a named warning) and a **5-content-token
  floor**.

⛔ **CONSEQUENCE, stated rather than buried: this does NOT repair the `SUPERSEDE` row.** The
measured ref is a content-tail truncation, so the leg refuses it — and it keeps refusing it by
test, not by luck (`test_supersede_leg_tolerates_frame_edges_only`, and the mutation arm's
"the MEASURED content-tail truncation does NOT resolve"). The row stays `edge_missing` in §2.
Repairing it needs a **semantic** resolver — the Jev-shaped seam
`EXTRACTOR-V4-ARCHITECTURE.md` names as unbuilt ("it is work, not a config flag") — or a prompt
that gets the ref copied exactly. It is not something a string predicate should be trusted
with, which is the conclusion four review rounds paid for.

**(c) MITIGATES pair choice — a sibling point chosen as the dampener's src.** In the rows this
names below, both endpoints are present and a `MITIGATES` is in the graph, wired to a *different*
pair of the same argument: the mapper
attaches the dampener to the **remedy decision** ("Cache the embedding results in memory
with a ten minute TTL") rather than to the **forecast** ("a ten-minute TTL cache will absorb
nearly all of the repeat traffic") or the **safeguard** ("invalidate the cache entry on a
write"). All three are separate Points, so all three are legitimate `MITIGATES` srcs — the
ontology licenses each pair; the plant asserts one. **Not a plant defect** (no amendment
proposed: the asserted pair is licensed by §3.9/§3.1 relevance-on-the-operator). The rule
now says every warrant point gets its own dampener; `wp04_aurora_perf_op_02` flipped to
`edge_correct` in every run of the fixed tree, `wp04_aurora_perf_op_03` did not — **this is
the single largest remaining bucket (3 rows) and it is a model-judgment limitation, not a
rule gap.**

**(d) the endpoint rows whose emitted paraphrase is a DIFFERENT claim.** `wp03_ember_design_op_03`
targets "Maintenance windows are the other classic noise source"; the extractor emitted "Ember
auto-suppresses alerts during declared maintenance windows", which states the remedy, not the
noise source. `wp06_quarry_rollout_op_01` targets "the lease makes that impossible no matter how
we roll out"; the extractor emitted "The duplicate-processing race cannot re-enter once the
lease is held" (a close paraphrase the anchor band still refuses) and "The lease guarantee is
scope-independent". The band is right to refuse these — they are different claims about the same
subject, and no mechanism change would make them the planted one.

**(e) THE RESIDUAL, ROW BY ROW — all nine misses of each run, not a sample.** The earlier
summary counted 6 of 9 and omitted `SUPPORTS` entirely, which is the kind the issue's own
closing bullet names; this is the complete accounting, recomputed from the committed receipts:

| verdict class | run A (9 misses) | run B (9 misses) |
|---|---|---|
| `to_content_missing` | `wp01_quarry_debug_op_02`, `wp03_ember_design_op_03`, `wp06_quarry_rollout_op_01` | `wp01_quarry_debug_op_02`, `wp03_ember_design_op_02`, `wp03_ember_design_op_03` |
| `edge_missing` — MITIGATES pair choice (c) | `wp03_ember_design_op_01`, `wp04_aurora_perf_op_03`, `wp07_bluepeak_followup_op_03` | `wp04_aurora_perf_op_03`, `wp07_bluepeak_followup_op_03` |
| `edge_missing` — SUPPORTS (the mapper formed no IMPL between two present claims) | `wp01_quarry_debug_op_01`, `wp04_aurora_perf_op_01` | `wp04_aurora_perf_op_01`, `wp05_retro_writeup_op_02`, `wp06_quarry_rollout_op_01` |
| `edge_missing` — SUPERSEDE (b) | `wp07_bluepeak_followup_op_04` | `wp07_bluepeak_followup_op_04` |

So the residual is, per run: **MITIGATES 3 (A) / 2 (B) + SUPPORTS 2 (A) / 3 (B) + SUPERSEDE 1 + endpoint rows 3**,
and the honest class statement is: *no `edge_missing` here is an operator-pass drop; the MITIGATES
and SUPPORTS rows are the mapper choosing a different (or no) pair, the SUPERSEDE row is the
resolver — an **entity-channel** refusal in the two committed after-receipts, a **drifted-copy**
refusal in the `preleg` run, and the two are not merged here — and the `to_content_missing` rows
are endpoint recall.* The
`SUPPORTS` class is the least understood of the four: the mapper emits `IMPL` edges in every one
of these sessions, just not between the planted pair, and nothing in this PR fixes that — the
prompt already asks for it.

**Note on the row-level attributions above.** A row's verdict can differ between runs (this
extractor is sampled), so a row named under one class in run A may sit under another in run B —
`wp03_ember_design_op_02` is `to_content_missing` in B and correct in A, and
`wp06_quarry_rollout_op_01` is `to_content_missing` in A and `edge_missing` in B. The table
reports the verdict each receipt actually recorded; it does not harmonise them.

## 4. The mutation arm — does the gate fail when a mechanism regresses?

`docs/evidence/8503/mutation_arm.py` reverts each mechanism **in memory** and then runs the
**real pin** from `tests/` under that mutation; a pin that raises is a detection (exit 0 =
every injected regression detected). The first cut of this script only re-read the mutated
constant — true by construction, and it would have passed with every pin deleted — so it was
rewritten in review:

```text
1. operator-selection rules (the real pins run under mutation)
  [DETECTED] S2_TMPL: assistant-asserted claims are licensed
  [DETECTED] S2_TMPL: an asserting question carries its claim
  [DETECTED] S2_TMPL: an operational outcome is a durable claim
  [DETECTED] shared rule: a displaced theory is a NAND attacker (never IMPL)
  [DETECTED] shared rule: an entity supersede writes no reversal edge
  [DETECTED] shared rule: every warrant point is a src

2. SUPERSEDE recall leg (the drop the receipt measured)
  [DETECTED] leg ON: the frame-edge ref forms the CORRECTS
             supersessions=[{'superseded': 'pt_wp06_decision', 'supersedes_by': 'pt_99f3c02b3f229c942349180425af69e8910a95ee939fc668bbaba7172d4607', 'evidence': 'point-level supersede ref (claim/decision reversal; cross-session correction)'}]
  [DETECTED] the MEASURED content-tail truncation does NOT resolve (the residual is pinned, not left open)
  [DETECTED] leg OFF: the same ref is DROPPED again (the mutation is real)
             and the drop is named: point supersedes='lease fix ships behind the canary flag at' matches no S3 prior — skipped (fail-open)
  [DETECTED] a leg that folds EVERY prior is caught by the same pin
  [DETECTED] restoring the COVERAGE RATIO is caught by the negative pin (the predicate is pinned, not just described)
  [DETECTED] reverting to the ORDERED-SUBSEQUENCE predicate is caught by the negative pin (the span tightening is pinned, not just described)
  [DETECTED] removing the TOKEN FLOOR is DETECTED by its pin
  [DETECTED] removing the PRONOUN predicate is DETECTED by its pin
  [DETECTED] trimming LOAD-BEARING edge words is DETECTED by its pin
  [DETECTED] letting a TEMPORAL ADVERB back into the trim is DETECTED by its pin

3. edge_missing attribution (operator drop vs supersede refusal)
  [DETECTED] shipped classifier separates the classes AND ignores the recall leg's success line
             operator_drops=1 supersede_refusals=2
  [DETECTED] a marker that swallows the recall leg's SUCCESS line is DETECTED by its pin
  [DETECTED] the merged-marker defect (entity notices counted as operator drops) is DETECTED by its pin

MUTATION ARM: every injected regression was detected by its pin.
```

Section 1 now calls `tests/test_extractor_v2.py::TestOperatorSelectionRules` itself; section 2
replays the **actual strings the after-run receipt recorded**, so the drift resolves with no
LLM in the loop, and adds the SAFETY direction (a leg that folds every prior must red the
same pin); section 3 re-injects the attribution defects both review rounds found, and the
coverage-ratio shape is re-injected as a fourth arm in section 2.

## 5. What did NOT move, and the honest limits of this measurement

* **`edge_correct` did not move beyond noise.** The committed artifacts read 3 before
  (probe) and 5/5 after, against the issue's own pre-fix 5 — the number the criterion
  measures is the same. The extractor is LLM-sampled
  (`deepseek/deepseek-v4-flash`, provider-served as `deepseek-flash`), and the README's own
  earlier note ("a corpus-level figure is stable enough at N=2") is **not** true of this
  state: the same code measured 2 and 6 on consecutive full-corpus runs. Any future claim on
  E4 needs ≥5 runs.
* **The acceptance number 14/14 is not reached; the residual is NINE rows per run, in four
  classes** (§3.2e lists every one): MITIGATES pair choice 3 (A) / 2 (B) · SUPPORTS with no IMPL
  formed between two present claims 2 (A) / 3 (B) · SUPERSEDE 1 · `to_content_missing` 3 in each.
  No plant amendment is proposed: every planted pair is licensed by `ONTOLOGY.md` §3.1/§3.9.
* **Environment disclosure.** The cell's root filesystem was at 100% (315 MB free) and
  `.venv` could not be synced (`uv sync --extra embeddings` failed with ENOSPC on
  `triton/_C/libtriton.so`). The measurements ran on a peer lane's venv read-only
  (`/tmp/cell-wt-fin9812/.venv/bin/python`, same `uv.lock` family) with `PYTHONPATH` pointed
  at this worktree, against a private FalkorDB lane container
  (`fdb-lane-759b821a79`, `127.0.0.1:16403`). No product code was changed to accommodate it.
  The full `tests/eval/write_path/` suite could not be run to completion here: the EP/SVBP
  pass (`tortoise/svbp.py`, jax) exceeds the repo's 300 s per-test cap at this box's load
  (average 8–11 on 8 CPUs with ~60 live worktrees). The suites that do not enter that path
  are green (§6).
* **Leakage: 1 and 0 in the two after-receipts, but 2 in `preleg`** — and 2 is ABOVE the golds'
  `distractor_leakage_tolerance` of 1, which `schema.py` records as a regression in `compare_run`.
  So the honest statement is NOT "the wider claim licence did not leak": it is that of the three
  committed fixed-tree runs, TWO measured within tolerance (1 and 0) and one did not (2), on a
  corpus whose leakage tolerance is 1. That is a risk this PR carries rather than a non-regression, and it is
  stated here because the alternative — quoting only the two runs that read 1 and 0 — is the
  selective reporting this document exists to avoid.

### 5.1 What the review rounds changed (and what they caught)

Six findings were raised against the first cut and all six were fixed before merge. Two were
**data-integrity** defects in the new supersede leg, and they are the reason the leg is
frame-EDGE-trimmed equality rather than a similarity score (§3.2b): the coverage ratio folded a
substituted entity, a changed number, a flipped role order and a changed scope word, and the
polarity test was presence-of-any-negator rather than set equality. Two more were defects in
this PR's own instrumentation: the refusal markers keyed on a prefix the recall leg's success
warning shared (so a resolution counted as a drop), and the run note claimed more than its
instrument can see (the commit seam is not observed — §1). The last two were a tautological
mutation-arm section and a missing negative test; both are now real (§4, and
`test_supersede_band_refuses_every_substitution`).

The lesson worth keeping: a recall leg on a belief-corrupting write must be a **predicate**,
not a threshold. "Is this ref a copy of this prior, or not" has a shape; "is it 0.85 similar"
does not, and the difference is a live belief terminalized with no un-supersede API.

**Round 2** found the predicate was still blind on three axes, because `_FRAME_STOPWORDS`
holds the connectives AND the pronouns: an exchanged connective ("tuesday **and** wednesday"
vs "tuesday **or** wednesday"), a substituted subject ("**we** ship" vs "**they** ship"), and
a DELETED gating qualifier (a ref truncated before "once the canary passes in staging") all
contained in order. All three are now closed with predicates the module already had —
`_connective_swap`, a pronoun-set check that refuses a substitution but not an addition (the
measured ref prefixes "We" to a prior with no subject), and condition-marker equality —
and `test_supersede_band_refuses_every_substitution` pins each one. Round 2 also caught two
honesty defects in this document and one in the classifier: the refusal count over-read by
~3× (§1), the headline claimed a gain its own table does not support (§ above), and the
receipts' `verdict` (`regression` — against a baseline that is stale fleet-wide, the pre-fix
receipt reads the same) was never stated. The mutation arm now also re-injects the
coverage-ratio shape, so the predicate is pinned rather than merely described.

**Round 3** found five more, in two passes. First the condition predicate itself was
spoofable: `_condition_markers` reads whole
tokens AND their word-parts (deliberately, for the fold path), and the sub-token split of
"all-at-once" yields the marker `once` — so when both sides spelled that compound, a ref that
had DROPPED a genuine "once the canary passes in staging" clause compared equal and resolved.
The leg now uses `_supersede_condition_markers`, which matches whole tokens only (a marker
cannot be minted from inside a compound; every refusal this loses is still a refusal), and the
compound case is pinned alongside the plain one. The containment test itself was tightened
from an ordered subsequence to a **contiguous span** at the same time: an ordered match can be
recombined across two clauses of one prior, which is not a copy of anything.

The same round then found four more, and they are the reason the leg moved from a span over
frame-stripped tokens to a span over the full token sequence:

* a substituted **PREPOSITION** crossed every gate — "raise the timeout **by** 30 seconds"
  superseded "raise the timeout **to** 30 seconds". Prepositions are frame words and are in no
  connective slot, so nothing read them; a frame word inside the span is compared now;
* the **pronoun** gate tokenised on a raw split, so `"they" ship …` (one quote mark) restored
  the subject substitution — it now tokenises with the same punctuation strip as everything
  else;
* the **negation** predicate and the **token floor** had no discriminating pin: the pin's only
  polarity case was refused by the floor instead, and setting the floor to 1 left the suite
  green. Both now have an isolating case AND their own mutation arm;
* `non-dict operator entry … skipped` was an unclassified real drop (§1).

The pattern across the three rounds is worth stating: every hole was a **frame word** the
predicate could not see, and each fix moved the leg closer to "the prior must literally
contain this text". That is what a false CORRECTS costs.

**Round 4** finished the argument and narrowed the leg to what a string predicate can be
trusted with. Two reachable corruptions remained in the span design: a pronoun ref against a
prior that names its subject with a NOUN ("They can raise the timeout…" superseding "The
payments team can raise the timeout…"), and — the systemic one — any TRAILING qualifier the
ref dropped, because the span allowed an additive tail by construction and the condition
vocabulary cannot enumerate qualifier spellings ("…kill flag after the staging canary
passes", "…to 30 seconds for the payments queue"). Tail truncation is precisely the drift this
model produces, so the tail tolerance had to go. The leg is now frame-EDGE-trimmed token
EQUALITY: the only drift it accepts is an edge made of frame words, and the measured wp07
truncation is refused BY TEST (§3.2b, and the mutation arm pins the refusal). Round 4 also
found that `_connective_swap` had stopped being load-bearing (the span subsumed it, so it was
removed from the leg), that the `non-dict operator entry` marker was not pinned (it is now),
and that `build_receipt` defaulted a missing `drop_warnings` to zeros — a receipt asserting
"0 drops" for a run that measured none (it now omits the block).

**Round 5** finished the simplification the narrowing implied. A leading `If` is BOTH a frame
word and a condition marker, so the edge trim was eating it — an ungated claim superseded a
gated one — and a trailing `today`/`now` was trimmed the same way. The trim is now restricted to
**non-load-bearing** frame words (`_EDGE_TRIMMABLE` = frame words minus negators, conditions,
connectives, dates, clock units and temporal-scope adverbs), which put negation and conditions
INSIDE the compared core and made their separate gates dead code — those gates and
`_supersede_condition_markers` are gone, and the code says so rather than leaving a reader to
infer it. Round 5 also caught that the run note's "logged no operator drop" wording was
reachable with `operator_drops > 0` (a false statement in a shipped receipt: the operator
sentence is now built from the count, with all four branches pinned), and that the churn
sentence overstated the row flips (2 red→green and 1 green→red in both runs; 5/2 counting a
flip in at least one).

**Rounds 6 and 7** were the classifier's turn. Round 6 found that `is ambiguous` in the refusal
markers also matched `entity '<x>' kind '<y>' is ambiguous across namespaces … created with the
explicit kind` — a notice that refuses nothing, because the entity IS created — so a receipt
claimed a replace-channel drop that never happened; the markers now key on the resolver's own
parenthesised forms (`is ambiguous (`), and the pin enumerates every resolver ambiguity branch.
Round 6 also caught `operator_drop_notes` returning NO note for the drops-only case (the one case
the note exists for) behind a vacuous `not in` assertion, and a residual accounting that covered 6
of 9 misses per run while omitting `SUPPORTS`. Round 7 found the last two unclassified refusal
sites (`non-dict retraction ref`, `empty retraction ref`), which let a run whose retractions were
all refused ship the "no supersede refusal" note.

**Round 8** found no defect in the executable change and one in the INSTRUMENT'S REACH: the two
pins that bind THIS document (the `docs/00_index.md` row and the verdict banner below) lived in
`tests/test_extractor_v2.py`, which is registered `core` **and is a `slow_files` member** — and
`tier1` has slow files subtracted from it, while `docs/` is in `NON_PYTHON_PREFIXES` and
`docs/evidence/8503/` matches no `CORE_ALSO` entry. A diff touching only this README therefore
took the selector's docs-only early return (`surfaces: []`, `slow_run: false`, 63 test files) and
the pins ran in **no leg at all**: deleting the index row, or restoring the
pre-fix banner sentence that read every `edge_missing` as the model's miss (the wording the pin
below forbids, so it is named and not quoted here), merged green and only reddened `main`
post-merge. Strictly the failure #8503's own attribution work exists to prevent — a green check
over a claim nothing measured. The two file-scan pins now live in
`tests/test_evidence_8503_claims.py`, registered in **both** `core` and `tier1` (the lane a
docs-only diff actually runs), and the selector itself is pinned by
`tests/test_ci_selection.py::test_the_8503_docs_pins_run_on_a_docs_only_diff`, which also proves
the `tier1` entry is what closes the hole. The third pin stays in `test_extractor_v2.py` because
it reads `tortoise/extractor_v2.py`'s source — correctly bound to a CODE diff.

## 6. Reproduction

```bash
export TORTOISE_DB_URI="$(tools/test_lane.py uri | sed -n "s/^export TORTOISE_DB_URI=//p" | tr -d "'")"
SESSIONS="--session wp01_quarry_debug --session wp02_lumen_refactor --session wp03_ember_design \
          --session wp04_aurora_perf --session wp05_retro_writeup --session wp06_quarry_rollout \
          --session wp07_bluepeak_followup"

PYTHONPATH=$PWD .venv/bin/python -m tests.eval.write_path.runner run $SESSIONS --no-ep-pass \
    --out tests/eval/write_path/receipts/w2b-operator-$(date +%F)-8503-a.json
PYTHONPATH=$PWD .venv/bin/python tools/operator_receipt_check.py \
    tests/eval/write_path/receipts/w2b-operator-2026-10-24-8503-a.json   # EVIDENCE VALID

PYTHONPATH=$PWD .venv/bin/python docs/evidence/8503/mutation_arm.py     # exit 0
```

Suites run green on this tree (review-round head): `tests/test_extractor_v2.py`,
`tests/test_evidence_8503_claims.py`, `tests/eval/write_path/test_write_path_grading.py` +
`test_write_path_operator_receipt.py`, the non-EP subset of
`test_write_path_runner.py`, and `docs/evidence/8503/mutation_arm.py` (exit 0). The full
`tests/eval/write_path/` directory cannot complete on this box — the EP/SVBP pass exceeds the
repo's 300 s per-test cap at this load — so CI is the authority for that leg.
