---
title: "W6C — the honest dense-leg baseline: rank movement of the five window-miss questions (#4194 / #4202)"
type: runbook
domain: operations
doc_status: live
created: 2026-09-19
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# W6C — the honest baseline: what the DENSE leg does to the five window misses

**⛔ First line — the branch and the encoder.** This is a **BRANCH measurement, not a
shipped-product measurement**: PR **#4202** (fix(capture): embed episodic turn Points on the turn
write path, `Closes #4194`) is **OPEN** (`mergedAt: null`), head
**`d51306c51de7cb387c05f66b9f67c96d1421615b`** — the sha the brief named. It is **not on `main`**.
The encoder was **REAL**: `BAAI/bge-small-en-v1.5` loaded through `sentence-transformers 5.7.0`
(HF cache `models--BAAI--bge-small-en-v1.5`; 199 weights loaded), **not a stand-in** — the
`assert_embedder()` pre-flight of the frozen instrument reports `present: true, class:
SentenceTransformer`, and the capture census below produced real 384-d vectors. Nothing here was
taken with a stub encoder.

**Tree measured:** the branch's product code at `d51306c51` **with `origin/main`'s #4139-repaired
instrument overlaid** (`tools/ask_shape_rate.py`, sha256
`d95c7f77644699127530c5ea6ed0f9c91d293fbab8f87af26e538a55a84256bf`, byte-identical to the
instrument the recorded 2026-09-19 receipt used). The branch predates #4139 and carries a stale
instrument that cannot drive the post-#3929 ask surface, so the overlay is required to run the
frozen instrument at all. Worktree `/tmp/4202-dense`; `--pin-sha` asserted `HEAD == d51306c51`.

---

## The headline

> **Superseded (W7A, 2026-09-20):** the ask seeder now embeds turn Points **by
> default**; the un-embedded store is the explicit `embed=False` variant (the
> #4197 backlog). The rest of this document records the pre-W7A baseline and is
> kept as history, not as current behaviour.

**The frozen instrument cannot see the dense leg — by construction — and that is the finding.**

`tools/ask_shape_rate.py` (and the whole ask-fixture family) seeds through
`tools/ask_spotcheck.py::seed_capture_turn_store`, and #4194 **deliberately** leaves that seeder
**without turn embeddings** — it must keep modelling the un-backfilled / no-embedder store the
shipping ask lane reads until the backlog decision (#4197). The divergence is **pinned by a test**.
So on the frozen instrument, **`retrieval_degraded` is 21/21 on `main`, on the branch, and on any
tree where the fix is not backfilled** — it measures the fixture's shape, not the product's write
path.

I therefore report both, and they must not be confused:

1. **The frozen instrument's result** (pre-registered rule, price-of-admission) — `retrieval_degraded`
   **21/21**, aggregate **not comparable to the dense leg**.
2. **A supplementary, read-only diagnostic** (new, not the frozen instrument) that seeds the same
   graph and then sets the turn Points' `embedding` with **the product's own
   `compute_embeddings` over the product's own stored-turn text** — i.e. exactly the vector #4202's
   write path now stores. The dense leg is the **only** difference between the two arms. This is
   the measurement the brief asked for, and it is the first in this product's history in which the
   dense leg actually contributes (`vector ran=true degraded=false reason=ok count=120`, vs
   `degraded=true reason=no_embeddings count=0`).

---

## STEP 0 — the precondition, established (not assumed)

### #4202 is NOT merged

| check | value |
|---|---|
| PR | #4202 `STATE=OPEN`, `mergedAt=null`, base `main` |
| head | `d51306c51de7cb387c05f66b9f67c96d1421615b` (matches the brief) |
| `origin/main` at measurement | `4a7b7202e` |

### Census — does a turn stored through the REAL capture path now carry an embedding?

Read-only, on a **copy** of the real store (`~/.tortoise/tortoise.db` → `/tmp/w6c/copy/tortoise.db`).
A capture was then run through
`TortoiseSDK.capture_session` with the branch's code (3 turns, extraction mocked locally —
`TORTOISE_SESSION_LLM_MOCK=1` — so **no LLM spend**).

| quantity | copy, before capture | after a real capture |
|---|---:|---:|
| `Point` total | 55 | 59 |
| `Point` with an `embedding` | 18 | 22 |
| **episodic turn Points** | **27** | **30** |
| **episodic turn Points WITH an embedding** | **0 / 27** | **3 / 30** |

The three captured turns (`w6c-census-capture_t0..t2`) each report `has_embedding: True`. This
reproduces the recorded 27/0 defect exactly before the capture and shows the branch's write path
**does** embed. The branch's own suite for this
(`tests/test_turn_embedding_write_path_4194.py`, including
`test_dense_leg_returns_the_captured_turn` and `test_hosted_capture_embeds_turn_points`) passes
**9/9** on this tree (322 s).

**So the leg is alive in the product path** — and inert in the instrument's fixture.

---

## The frozen instrument — two full runs (`docs/runbook/ask-shape-rate-2026-09-19-dense-leg-run{1,2}.json`)

Both runs: `--mode full`, fixture sha256
`7f4062643323af4e5d0fec0b98bb3e3ca8499362c3f7e07a83c55f07633d15fa` ✓, reader pinned
`deepseek-direct` / `deepseek/deepseek-v4-flash`, movement control **fired** (M1_l2_red, M2_l1_red,
M3_l3_red, M3_l1_red all true), known-green 4/4.

| what | keyword-only (recorded) | run 1 (branch) | run 2 (branch) |
|---|---|---|---|
| `shape_rate` | **0.238** (5/21) | **0.381** (8/21) | **0.381** (8/21) |
| L1 abstain | 10–12/21 | 12/21 | 12/21 |
| L2 provenance | 21/21 | 21/21 | 21/21 |
| L3 grounding | 9–10/21 | 11/21 | 11/21 |
| `ctx_recall` | 21/21 | 21/21 | 21/21 |
| **`retrieval_degraded`** | **21/21** | **21/21** | **21/21** |

**`retrieval_degraded` is STILL 21/21 — the leg is inert *in the instrument's store*, and this is
not evidence that #4202 failed.** See the headline: the fixture seeder is deliberately un-embedded
and the divergence is test-pinned. The real capture path embeds (census above). The frozen
instrument, as it stands, **cannot** produce a dense-leg measurement on captured turns; doing so
requires either the backlog/backfill decision (#4197) or an explicit change to the fixture seeder —
i.e. **a new instrument**, which is a decision, not a measurement.

### The aggregate delta is NOT the dense leg, and it is outside the recorded spread

The brief's recorded spread **on byte-identical code is 0.238 · 0.286 · 0.333**. My two runs are
**0.381 and 0.381** — identical per-question decisions (only 7 `answer_head` strings differ, i.e.
wording), so this pair contributes **no** spread. **0.381 is above the recorded top (0.333) by one
question.**

The delta **cannot be the dense leg**, because the candidate mix is unchanged
(`retrieval_degraded` 21/21 in all three runs; `handler_search.ids` **identical to the recorded
run on 21/21**). Three questions flipped to PASS versus the recorded run:

| question | recorded | run 1 | run 2 |
|---|---|---|---|
| `2133c1b5` | FAIL | PASS | PASS |
| `ba358f49_abs` | FAIL | PASS | PASS |
| `gpt4_7a0daae1` | FAIL | PASS | PASS |

That is reader-output variance plus a different tree (the recorded receipt's `tree_pin` was
`d26e1e7cd`, not `4a7b7202e`, and not this branch). **A three-question aggregate move with an
identical candidate mix is not evidence about ranking**; per the brief, per-question movement on a
named leg is the only usable signal.

---

## PRIMARY RESULT — per-question GOLD-TURN RANK of the five window misses

Supplementary diagnostic (`docs/runbook/w6c_gold_rank_diagnostic.py`,
receipt `docs/runbook/w6c-gold-rank-diagnostic-2026-09-19.json`). Pool depth 120, cut 40;
the gold turns are the fixture's own `has_answer` turns. **Zero paid calls** (deterministic
retrieval only).

**Validation of the "before" arm:** on the un-embedded (capture-shape) graph this diagnostic
reproduces the recorded keyword-only ranks **exactly** — 1de5cff2 = **67**, 1d4e3b97 = **84**,
e9327a54 = **89**, ceb54acb = **93** — and the two remaining `0a995998` gold turns fall beyond the
120-pool (consistent with the recorded **147**). The brief's `67 / 84 / 89 / 93 / 147` are thereby
pinned to named turns.

| question | gold turn | keyword-only (this diagnostic) | **dense leg alive** | in the 40-cut |
|---|---|---:|---:|---|
| `0a995998` (count = 3) | `answer_afa9873b_2_t10` | 20 | **3** | ✓ (was ✓) |
| `0a995998` | `answer_afa9873b_3_t6` | ABSENT (pool > 120; recorded 147) | **41** | ✗ — **one rank past the cut** |
| `0a995998` | `answer_afa9873b_1_t4` | ABSENT (pool > 120) | **23** | **✓ (was absent)** |
| `1d4e3b97` | `answer_e6b6353d_t2` | 56 | **8** | **✓** |
| `1d4e3b97` | `answer_e6b6353d_t4` | 84 | **7** | **✓** |
| `1de5cff2` | `answer_ultrachat_440262_t7` | 67 | **10** | **✓** |
| `ceb54acb` | `answer_sharegpt_cGdjmYo_0_t3` | 93 | **3** | **✓** |
| `e9327a54` | `answer_ultrachat_480665_t7` | 89 | **10** | **✓** |

Leg traces on the same queries, same graph, one variable:

* capture-shape — `{"leg":"vector","ran":true,"degraded":true,"reason":"no_embeddings","count":0}`
* dense-alive — `{"leg":"vector","ran":true,"degraded":false,"reason":"ok","count":120}`

**Movement: 1 of 8 gold turns was inside the cut before; 7 of 8 are inside it with the dense leg
alive, and the eighth moved from beyond the pool to rank 41 — one rank past the cut.**

---

## Assembly budget actually filled

Supplementary diagnostic (`w6c_assembly_budget_diagnostic.py`, receipt
`w6c-assembly-budget-2026-09-19.json`). The real ask-lane retrieval + dedup + boost + rerank +
assembly runs; only the **reader transport is a stub** (`context_tokens` is what is read), so
**zero paid calls**. All 21 questions, both arms:

| arm | tokens (median) | % of the 8000 cap (median / range) | evidence bytes > 32000 | `retrieval_degraded` |
|---|---:|---|---:|---:|
| capture-shape (keyword-only) | 5591 | **69.9 %** / 23.2–74.8 % | 10 / 21 | 21/21 |
| dense leg alive | 5665 | **70.8 %** / 28.1–75.4 % | 10 / 21 | **0/21** |

**The reader window is NOT ~97.4 % saturated on this fixture** — it fills ~70 % of the 8000-token
cap. With the dense leg contributing, the binding constraint moves to the **32 KiB byte cap**
(ten questions assemble to 32,000–32,764 bytes of the 32,768-byte cap). The 97.4 % figure belongs
to the M1 read-path profile's own corpus; it does not reproduce here. **Head-room of ~25–30 % of the
token budget exists, so better ranking can be delivered to the reader — the window is not what is
starving it.**

---

## The consequence (the brief's three cases)

1. **The dense leg closes the rank gap and moves the gold turns inside the 40-item cut** (7/8 in;
   the eighth at 41). **Ordering was the problem.** The free fusion work (#4210) is the win, and
   the keyword-only numbers that suggested a *retrieval* problem were an artifact of the missing
   turn embeddings.
2. **It is not a retrieval problem.** 7 of 8 gold turns are in the pool *and* in the cut; only one
   (`0a995998`'s third required turn, a multi-session **count** question) sits at 41, one rank
   outside. Graph-based candidate introduction (#2552/#3664) is *not* the lever for these five.
3. **`retrieval_degraded` is still 21/21 on the frozen instrument** — but the cause is the
   instrument's deliberately un-embedded fixture seeder, **not** a failure of #4202 (census: 3/3
   captured turns now carry a real vector; the branch's own 9/9 write-path suite passes). The
   claim "the first dense-leg ranking measurement" therefore holds **only** under the
   supplementary diagnostic; **the pre-registered `shape_rate ≥ 0.80 ⇒ ADOPT` rule is NOT applied
   to it and no ADOPT/DO-NOT-CLAIM verdict is claimed from it.**

**Actionable next step:** the frozen instrument cannot guard the fix it was built to measure. Until
either the fixture seeder embeds (a deliberate change, not a measurement) or the backlog is
backfilled (#4197), `retrieval_degraded` 21/21 will keep meaning "the fixture has no vectors" and
will be misread as "the leg is broken".

**DONE (W7A, 2026-09-20):** the fixture seeder now embeds by default (`embed=True`), so
`no_embeddings` is gone from the default arm (see `docs/runbook/w7a-switchable-seeder-2026-09-20.md`
§3); `embed=False` retains the #4197 backlog. The paragraph above is retained as the pre-W7A record.

---

## Rules compliance

* **No `--mock` anywhere.** `--pin-sha` asserted; reader real and pinned; not one `--mock`.
* **Paid reader calls made: 42** — run 1 = **21** (all attempt 1, no retries, no errors), run 2 =
  **21** (same). The two supplementary diagnostics made **0** paid calls (deterministic retrieval;
  the assembly run's reader is a stub and no quality claim is drawn from it). The census made **0**
  (extraction mocked locally).
* **No product behaviour changed.** Read-only on copies for the census; the instrument's legs,
  thresholds, fixture, reader pin and pre-registered rule were **not** touched.
* **Real store copied, never written.** Verified by CONTENT, not by mtime: the real store still
  reports **55 `Point` / 1538 `Session`** (identical to the recorded M1 census) and **0** W6C points,
  after the whole job. ⚠️ Its file **mtime does move** (opening the embedded store rewrites the file
  on open/close — reproduced with a pure read), so an mtime change is **not** evidence of a write;
  graph content is.

## What I could NOT verify

* **The hosted capture path was not exercised by a live capture** — only by its own test
  (`test_hosted_capture_embeds_turn_points`, passing). The live census went through
  `TortoiseSDK.capture_session` only.
* **Whether a fusion-weight change (#4210) brings `0a995998`'s third turn from 41 into the cut** —
  unmeasured; that is #4210's own A/B.
* **The recorded 0.238 / 0.286 aggregate values** — I could not reproduce them; both branch runs
  give 0.381 with identical per-question decisions. The delta is reader variance on a different
  tree, **not** the dense leg (identical candidate mix), so I report it and do not claim it as
  improvement.
* **The ~97.4 % window saturation** — does not reproduce on this fixture (measured ~70 %).
* **`e9327a54`'s historical rank of 89 vs the M1 profile's 30** — my un-embedded arm measures **89**,
  matching the recorded 89; the M1 profile's 30 remains unexplained by this job.
