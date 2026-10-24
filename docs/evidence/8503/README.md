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

# `#8503` — planted operator edges: 3/14 → 5/14, with every remaining miss attributed

**Measured at:** `a999be41e` (the lane's base) with the fix in the working tree, 2026-10-24.
E4's own gate (`tools/operator_receipt_check.py`) is the acceptance instrument; the
receipts this document cites are `tests/eval/write_path/receipts/w2b-operator-2026-10-24-8503-{a,b}.json`
and both exit **EVIDENCE VALID**.

> **Verdict: 5/14 in-14, up from 3/14 on the same instrument, and NOT the 14/14 the issue's
> `edge_correct` criterion asks for.** The part that is systematic is upstream of the edge:
> **endpoint recall rose 7/15 → 11/15 planted edges with both anchors present** across every
> run of the fixed tree, and **the extractor logged no operator drop in any run** — so no
> remaining `edge_missing` is an extractor-side drop. (That zero is NOT a clean bill for the
> write path: a commit-seam drop is not observable from a receipt — §1, blind spot.) The
> edge number itself sits inside this extractor's run-to-run variance (§5), which is why it
> is reported as a distribution and not as a point estimate.

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
| **operator drops** | `operator dropped:`, `operator with unknown op_type`, `endpoint NOT minted`, `MITIGATES target edge not emitted` | the operator pass refused an edge the model emitted |
| **supersede refusals** | `matches no S3 prior`, `is ambiguous`, `does not resolve to an S3 search result`, `is not expressible in the Layer-1 payload` | the replace channel dropped a CORRECTS/supersession record |

Measured on the fixed tree: **`operator_drops = 0` in every run.** Two defects in this
discriminator were found in review and fixed before merge:

1. the first cut merged the two marker sets and reported "5 operator drops" on a run where
the operator pass dropped nothing — five *entity-lifecycle* notices. Pinned by
`test_operator_drop_warnings_separates_the_replace_channel`.
2. the refusal set keyed on the `point supersedes=` **prefix**, which the recall leg's own
SUCCESS warning also carried — counting the one resolution that worked as a drop. The
markers are now refusal TAILS and the leg's success line is a distinct prefix, pinned by
`test_recall_leg_success_warning_is_not_a_refusal` (which runs the leg and classifies the
warning it actually emits, so a prose edit cannot satisfy it).

⛔ **A ZERO IS EVIDENCE ABOUT THE OPERATOR PASS AND NOTHING ELSE.** Two seams are outside
this instrument: the commit layer (`commit_ops.apply_payload_operators`) logs its skips
through `_logger` with no warnings sink on the capture, and a session whose snapshot failed
to read produces no warnings at all. The run note says so verbatim; the receipts committed
here predate that wording (their note still reads "not the write path dropping one") — the
counts are unchanged and the corrected note is what the shipped code emits.

**`supersede_refusals` is 5–7 per run** — the replace channel is systematically refusing
references. One of those refusals was the `SUPERSEDE` row's real mechanism (§3.2b).

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
| BEFORE (unmodified tree, 1 run) | **3** | 7 | 0 | — |
| BEFORE (the issue's own run, 2026-10-24T04:25Z) | **5** | — | not carried | — |
| AFTER run A | **5** | 11 | 0 | 1 |
| AFTER run B | **5** | 11 | 0 | 0 |

Two further diagnostic runs of the fixed tree (same instrument, prompt set slightly
narrower — before the supersede band landed) read **6** and **6** `edge_correct` with
`content_ok` 12/12, and two earlier harness runs of the intermediate prompt read 5 and 2.
**The honest range of the fixed tree is 2–6 edges, median 5.** `content_ok` never left
11–13 on any fixed-tree run, against **7** before — that is the systematic part.

## 3. The mechanisms and what was done about each

### 3.1 Endpoint recall — 5 of the 14 misses, 8 of 28 endpoints

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

With `operator_drops = 0` in every run, each `edge_missing` is a **selection** miss — the
model had both claims and wired no operator between them. That inference is bounded by the
commit-seam blind spot named in §1: the operator pass dropped nothing in these runs, but a
commit-layer drop is not observable from a receipt, so it is *not excluded*, only unobserved.
Three distinct sub-mechanisms were measured:

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
issue names as `#3679`'s supersedes/`S3 RESOLVE` path. The receipt now shows the real
mechanism: `point supersedes='We ship the lease fix all-at-once behind a single global kil'
matches no S3 prior — skipped (fail-open)`. The model **did** express the cross-session
reversal through the licensed channel; the ref differed from the wp06 prior by a lead-in
word ("We ship…" for "Ship…") and `_resolve_point_supersede` requires *exact* normalized
equality, so the CORRECTS edge — the only carrier of a cross-session decision reversal —
was dropped on a quoting slip.

**Fixed:** the resolver gains a recall leg under the **same never-guess discipline**. The
shape of that leg is the part review changed, and the first cut is worth recording: it
scored the ref's token COVERAGE inside the prior, dividing by the ref alone, so **one
differing content token was free** (6/7 = 0.857). Measured against that cut: "report the
latency regression to the payments team" folded onto "…to the search team", a 30-second
timeout onto a 60-second one, and a cause/effect pair onto its inversion — every one of them
a pair `distinguishing_difference` refuses to even FOLD. A false CORRECTS terminalizes a
live belief and there is no un-supersede API, so the leg is now **ordered containment**:

* the ref's content tokens must appear **in order** inside the prior's content tokens — a
  different number, entity, scope word, or a role inversion breaks containment, while an
  ADDITION in the prior (the measured drift) does not;
* the two must carry the **same negation markers** (set equality, not presence — every
  negator is a frame word, so containment alone would fold "the cache is shared" onto
  "the cache is not shared", and two claims can each carry a negator and mean opposites);
* a **unique** match is required (0 or 2+ candidates fail open with a named warning), and a
  ref under 5 content tokens is refused so a generic short ref cannot match half the graph.

The prompt also states that an **entity-level** supersede writes no reversal edge (the
Layer-1 payload has no such channel) — the model had been expressing the reversal that way.
`test_supersede_band_refuses_every_substitution` pins the safety direction with the exact
pairs the first cut folded.

⚠️ **This row still reads `edge_missing` in both after-runs**, because in those runs the
model used the *entity* channel only and emitted no point-level `supersedes` at all. The
fix is proven deterministically instead (§4, and the unit test), not by a lucky sample.

**(c) MITIGATES pair choice — a sibling point chosen as the dampener's src.**
`wp01_quarry_debug_op_02` and `wp04_aurora_perf_op_03` have both endpoints present and a
`MITIGATES` in the graph, wired to a *different* pair of the same argument: the mapper
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

**(d) `wp06_quarry_rollout_op_01` (SUPPORTS) and `wp03_ember_design_op_03`.** The last two
misses are endpoints whose emitted paraphrase sits below the anchor band: the lease claim is
present as "the duplicate-processing race cannot re-enter once the lease is held", the
maintenance-window claim as "ember auto-suppresses alerts during declared maintenance
windows". Both are *different claims* about the same subject, not distillations — the band is
right to refuse them, and no mechanism change would make them the planted claim.

## 4. The mutation arm — does the gate fail when a mechanism regresses?

`docs/evidence/8503/mutation_arm.py` reverts each mechanism **in memory** and then runs the
**real pin** from `tests/` under that mutation; a pin that raises is a detection (exit 0 =
every injected regression detected). The first cut of this script only re-read the mutated
constant — true by construction, and it would have passed with every pin deleted — so it was
rewritten in review:

```

1. operator-selection rules (the real pins run under mutation)
  [DETECTED] S2_TMPL: assistant-asserted claims are licensed
  [DETECTED] S2_TMPL: an asserting question carries its claim
  [DETECTED] S2_TMPL: an operational outcome is a durable claim
  [DETECTED] shared rule: a displaced theory is a NAND attacker (never IMPL)
  [DETECTED] shared rule: an entity supersede writes no reversal edge
  [DETECTED] shared rule: every warrant point is a src

2. SUPERSEDE recall leg (the drop the receipt measured)
  [DETECTED] leg ON: the drifted ref forms the CORRECTS
             supersessions=[{'superseded': 'pt_wp06_decision', 'supersedes_by': 'pt_99f3c02b3f229c942349180425af69e8910a95ee939fc668bbaba7172d4607', 'evidence': 'point-level supersede ref (claim/decision reversal; cross-session correction)'}]
  [DETECTED] leg OFF: the same ref is DROPPED again (the mutation is real)
             and the drop is named: point supersedes='We ship the lease fix all-at-once behind a single global kil' matches no S3 prior — skipped (fail-open)
  [DETECTED] a leg that folds EVERY prior is caught by the same pin

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
same pin); section 3 re-injects the two attribution defects this review found.

## 5. What did NOT move, and the honest limits of this measurement

* **`edge_correct` did not move beyond noise.** Before 3, after 5/5 (and 6/6 on two
  diagnostic runs, 2 on one harness run). The extractor is LLM-sampled
  (`deepseek/deepseek-v4-flash`, provider-served as `deepseek-flash`), and the README's own
  earlier note ("a corpus-level figure is stable enough at N=2") is **not** true of this
  state: the same code measured 2 and 6 on consecutive full-corpus runs. Any future claim on
  E4 needs ≥5 runs.
* **The acceptance number 14/14 is not reached, and the remaining bucket is model judgment**:
  3 MITIGATES rows (sibling-point choice), 1 SUPERSEDE row (channel not used by the model),
  2 endpoint rows (different claims about the same subject). No plant amendment is proposed:
  every planted pair is licensed by `ONTOLOGY.md` §3.1/§3.9.
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
* **Leakage did not regress**: `distractor_leakage_per_run` 1 and 0 after, against a design
  tolerance of 1 — the wider claim licence did not turn into routine-aside leakage.

### 5.1 What the review round changed (and what it caught)

Six findings were raised against the first cut and all six were fixed before merge. Two were
**data-integrity** defects in the new supersede leg, and they are the reason the leg is
ordered containment rather than a similarity score (§3.2b): the coverage ratio folded a
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
`tests/eval/write_path/test_write_path_grading.py` +
`test_write_path_operator_receipt.py`, the non-EP subset of
`test_write_path_runner.py`, and `docs/evidence/8503/mutation_arm.py` (exit 0). The full
`tests/eval/write_path/` directory cannot complete on this box — the EP/SVBP pass exceeds the
repo's 300 s per-test cap at this load — so CI is the authority for that leg.
