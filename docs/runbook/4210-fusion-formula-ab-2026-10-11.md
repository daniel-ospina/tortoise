---
title: "#4210 — the fusion-formula A/B: weighted RRF vs a score-normalised convex combination, and the weight that explains most of the gap"
type: runbook
domain: operations
doc_status: live
created: 2026-10-11
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# #4210 — the fusion-formula A/B

**Status:** the A/B **instrument** and a **directional, zero-paid measurement** land here.
**No product behaviour changed.** The formula **ruling is the owner's** (#4210: *"Rule on the
formula: keep weighted RRF, switch to convex, or hybridise. **Owner decision.**"*) and this
document reaches no ruling — it supplies the numbers the ruling needs.

**Receipt:** `docs/runbook/4210-fusion-formula-ab-2026-10-11-embedded.json`
**Instrument:** `tools/fusion_formula_ab.py` (its own guard: `tests/test_fusion_formula_ab.py`).

---

## 0. First line — what this measurement IS, and what it is NOT

| | |
|---|---|
| **Lane** | **embedded** (in-process). The vector leg is a brute-force scan (no HNSW) and the sparse leg is the FTS/TF-IDF fallback. **Directional, NOT decision-grade.** The recorded instrument's lane is docker. |
| **Population** | the ask fixture (`tests/fixtures/ask_spotcheck_composition.json`), seeded through the **same** seeder the frozen instrument uses — `tools.ask_spotcheck._seed_memory(embed=True)`, the post-#4194/#4304 default — **18 of 21 questions** (every question carrying `has_answer` turns). |
| **Paid calls** | **0** (local `BAAI/bge-small-en-v1.5`; no reader). |
| **`shape_rate`** | **NOT measured.** It needs the frozen instrument's paid reader pass, whose aggregate carries a measured 0.238–0.333 spread on byte-identical code. A quiet-host instrument run is the follow-up (§5). |
| **Leg set per question** | `('fts','vector')` on **18/18** — the arms were comparable on every question (`fusion_arms_comparable_on_every_question: true`). |

**Why a directional run is the honest delivery here.** The docker lane could **not** be
measured on this host: at load average 26–41 the projection's index DDL exceeds its 60 s
socket ceiling and the open path then reports the graph unhealthy —

```text
RuntimeError: DB health check failed on open (server/production mode)
  ← Failed to create index on n.id: Timeout reading from socket
```

— and the embedder's shipped 90 s load bound (`EmbeddingModel._LOAD_TIMEOUT_S`) is likewise
exceeded, which would have made the dense leg **inert** and the whole comparison meaningless.
This is the same constraint the W6C/W7A receipts recorded (*"MUST be recorded on a quiet one"*).
The instrument therefore pre-warms the embedder with a bounded, recorded timeout and refuses to
pretend: the receipt carries `embedder_ready: true` (or the run cannot answer the question at
all — the receipt says so).

---

## 1. STEP 0 — the precondition, RE-MEASURED (the body is provenance-stale)

The sequencing argument in issue **#4210** rests on three states of the world. **Two of the three are
stale.**

| #4210 body says | Measured 2026-10-11 | Evidence |
|---|---|---|
| *"Fix the write path so captured turns carry their vector (**PR #4202**)"* — open, unmerged | **#4202 is `CLOSED` (never merged). The fix landed as PR #4304, `MERGED` 2026-09-20**, which is the *same* change (`fix(capture): embed episodic turn Points on the turn write path (#4194)`) and carries `tests/test_turn_embedding_write_path_4194.py`. #4194 is closed. | `gh pr view 4202/4304`; `tortoise/sdk.py::_capture_turn_embeddings` + the `CASE … vecf32($emb)` turn write |
| *"Then **re-measure** the per-question gold ranks with the dense leg alive — the honest baseline, never yet taken"* | **Taken.** W6C (PR #4217, merged) recorded the branch baseline; **W7A (PR #4395, merged 2026-09-20)** re-measured on the repaired seeder where turn embedding is the **default** — *"the ask seeder now embeds turn Points by default; the un-embedded store is the explicit `embed=False` variant"*. | `docs/runbook/w7a-switchable-seeder-2026-09-20.md`; `docs/runbook/w7a-gold-rank-2026-09-21.json` |
| *"the `search_keys_prf` … is **default ON on the ask lane** … That is a live default whose evidence is mixed"* | **Stale — the correction has already landed.** The ask lane's default is now **OFF**: `ask_env_bool("TORTOISE_ASK_SEARCH_KEYS_PRF", False)  # A4, default OFF until measured (#4593)`. | `tortoise/ask_lane.py:520`; #4593 closed 2026-10-03 |
| the decision is recorded at `tortoise/sdk.py` **~:12996** | **:18688** (the file grew) | `tortoise/sdk.py:18688` |
| the decision's `OVERRIDES:` marker | **NOT applied.** #4210 itself reserves it: *"applying it is the owner's call"*. The wording is reproduced in §6 for one-paste application. | #4210 body |

**Consequence:** the issue's precondition for the A/B (**step 2**, the dense-leg-alive baseline)
**has landed**, so the A/B itself is the only remaining non-owner work — which is this document.
The provenance corrections are relayed to the issue.

---

## 2. The A/B — what was compared, and the one-variable guarantee

Two arms, and a **control**:

| arm | arithmetic | where it lives |
|---|---|---|
| `rrf_default` | the **shipped** weighted RRF, weights resolved by the SDK: `{"vector": 1.5}` | `tortoise/search_engine.py::rrf_fusion` — **unchanged** |
| `convex` | `s(d) = α·p_norm(d) + (1−α)·mean(normᵢ(d) for i≠primary)`, per-leg **min-max** score normalisation, `α=0.7`, primary `vector` | **`tools/fusion_formula_ab.py`** — measurement tooling only |
| `rrf_3 / rrf_5 / rrf_10` | **the same shipped RRF**, same captured legs, **only the vector weight moved** | control |

**The convex arm is deliberately NOT a product capability.** The shipped default is a recorded
owner decision (2026-08-25) and the formula ruling is the owner's; shipping the convex formula
would be adopting a finding over a decision — exactly what #4210 must not do. The implementation
therefore stays in the tool, and `test_convex_is_not_a_product_capability` fails if it is ever
promoted to `tortoise.search_engine`.

**One-variable by construction.** The convex arm fuses the RRF arm's **own captured leg lists**
(the tool swaps `rrf_fusion` for one retrieval call and injects the convex order), so a
second-pass leg timeout cannot change which candidates were fused. That guarantee was added
because the first build of the instrument **did** exhibit the confound and reported it
(`same_legs: false` on `0a995998`, 2026-10-10) — the flag is what caught it.

---

## 3. The result

**Mean nDCG@10 over 18 questions** (binary gains over `has_answer` turns; pool depth 120, reader
cut 40):

| arm | mean nDCG@10 | Δ vs shipped default (90% paired bootstrap CI, points) |
|---|---:|---|
| **`rrf_default`** (vector **1.5×** — what ships) | **0.4946** | — |
| `rrf_3` (vector 3×, same formula) | 0.6380 | **+14.34** [+9.03, +19.98] |
| `rrf_5` (vector 5×, same formula) | 0.6335 | — |
| `rrf_10` (vector 10×, same formula) | 0.6743 | **+17.96** [+8.18, +26.33] |
| **`convex` α=0.7** | **0.6940** | **+19.94** [+11.88, +28.01] |
| `convex` α=0.5 | 0.5707 | — |
| `convex` α=0.9 | 0.6958 | — |
| **`convex` − `rrf_10`** | — | **+1.97** [**−4.78**, +9.65] |

Per-question: convex is **better on 13** questions, **worse on 3**, **tied on 2**; gold turns
**19 improved / 10 worsened / 5 unchanged**.

| qid | gold | rrf 1.5× | convex 0.7 | rrf 3× | rrf 10× |
|---|---:|---:|---:|---:|---:|
| `0100672e` | 2 | 0.177 | **0.605** | 0.396 | 0.544 |
| `0a995998` | 3 | 0.235 | **0.605** | 0.349 | 0.376 |
| `1d4e3b97` | 2 | 0.398 | 0.571 | 0.442 | **0.807** |
| `1de5cff2` | 1 | 0.000 | 0.356 | 0.315 | **0.500** |
| `2133c1b5` | 2 | 0.807 | **1.000** | 0.877 | **1.000** |
| `26bdc477` | 2 | 0.818 | **1.000** | 0.850 | **1.000** |
| `830ce83f` | 3 | 0.907 | 0.913 | 0.913 | 0.913 |
| `b0479f84` | 1 | 0.500 | **1.000** | **1.000** | **1.000** |
| `ceb54acb` | 1 | 0.387 | **1.000** | 0.500 | 0.631 |
| `d6233ab6` | 2 | **0.307** | 0.218 | 0.264 | 0.193 |
| `e4e14d04` | 2 | 0.591 | **0.832** | **0.832** | **0.832** |
| `e831120c` | 2 | **0.613** | 0.571 | 0.818 | 0.850 |
| `e9327a54` | 1 | 0.000 | 0.356 | 0.289 | 0.431 |
| `eace081b` | 1 | **1.000** | **1.000** | **1.000** | 0.500 |
| `gpt4_6ed717ea` | 2 | 0.544 | 0.807 | **0.850** | 0.807 |
| `gpt4_7a0daae1` | 2 | 0.832 | 0.832 | **0.850** | 0.832 |
| `gpt4_8279ba02` | 1 | 0.431 | 0.631 | 0.631 | 0.631 |
| `gpt4_d84a3211` | 4 | **0.359** | 0.195 | 0.308 | 0.291 |

**Cross-check against the recorded baseline.** On the five W6C/W7A window-miss questions the
shipped-default arm reproduces the recorded **docker-lane** dense-leg ranks almost exactly —
`0a995998` **3 / 38 / 22** here vs the recorded **3 / 41 / 23**; `1d4e3b97` **8 / 7** vs **8 / 7**;
`ceb54acb` **5** vs **5**; `e9327a54` **11** vs **11**. (`1de5cff2` differs: the recorded
docker run hit the 500 ms collector deadline and returned rank 67; this embedded run did not
time out and returned 11.) The populations are therefore comparable even though the lanes differ.

---

## 4. The reading — most of the gap is the **weight**, not the formula

**This is the finding the ruling needs, and it is not the one the issue's framing anticipates.**

1. **The convex arm's advantage over the shipped default is large and significant**:
   **+19.94 points**, CI [+11.88, +28.01]. So the shipped **1.5× is badly under-weighted**.
2. **But ~72–90 % of that gain is reachable by the SAME formula**: `rrf_3` alone buys
   **+14.34 points** [+9.03, +19.98] and `rrf_10` buys **+17.96** [+8.18, +26.33]. Raising the
   exported `TORTOISE_FUSION_WEIGHTS` knob is a **weight** refinement — and the 2026-08-25
   decision **explicitly pre-authorises** exactly that (*"the LongMemEval-S re-burn later will
   refine this"*), with **no reopening required**.
3. **The formula's own increment over a heavily-weighted RRF is not distinguishable from zero on
   this population**: `convex` − `rrf_10` = **+1.97 points, CI [−4.78, +9.65]** — the interval
   **includes 0** at n=18.
4. **The formula is not uniformly better**: it makes **3 questions worse** (`d6233ab6`
   0.307→0.218, `e831120c` 0.613→0.571, `gpt4_d84a3211` 0.359→0.195) and drives the α curve in
   **both** directions (`d6233ab6` falls to 0.000 at α=0.9). Reporting the aggregate alone would
   have hidden this — which is precisely why #4210 mandates per-question reporting.

**What this does NOT license.** It does not say "keep RRF" (that is the owner's ruling). It does
not say the convex gain is spurious. It says: **on this population, the two effects are
separable, and the cheap, pre-authorised one accounts for most of the movement.** A ruling on
the formula that ignores the weight axis would attribute to arithmetic what belongs to a
weight, and the decision would then be re-litigated the moment someone re-tunes the weight.

**The two knobs are not on the same scale.** The convex arm's α and RRF's weight are different
parameterisations (a min-max normalised score blend vs a reciprocal-rank multiplier), so the
control curve is an *empirical* bracket, not an exact matched-weight comparison. The honest
statement is the bracket: RRF's own weight recovers most — but not all — of the gap.

---

## 5. The follow-up the decision still needs (quiet host, docker lane)

The directional result above must be **re-taken on the recorded instrument's lane before the
ruling**, and the `shape_rate` leg of #4210's metric list cannot be produced here at all
(paid reader; host-sensitive aggregate). On a quiet host:

```bash
TORTOISE_DB_URI='docker://:falkordb@<host>:6379/<ns>' \
  .venv/bin/python tools/fusion_formula_ab.py --lane docker \
    --alphas 0.5,0.7,0.9 --rrf-weights 1.5,3,5,10 \
    --out docs/runbook/4210-fusion-formula-ab-<date>-docker.json
```

The tool writes the receipt **after every question** (a contended host must not lose the rows it
already measured), re-seeds a question into a fresh graph on a transport failure, and records
`embedder_ready` — a run that loses the dense leg cannot masquerade as a result.

---

## 6. The `OVERRIDES:` wording — supplied, NOT applied

Issue **#4210** supplies this wording and reserves its application to the owner (*"Not applied
unilaterally — the marker belongs on the decision's own artifact, and the decision is the
owner's. This issue supplies the wording; applying it is the owner's call."*). It was **not**
applied by this work. For one-paste application onto the decision's own artifact
(`tortoise/sdk.py`, the `#1657 weighted RRF` comment — the artifact a lane actually reads):

> **OVERRIDES:** the hybrid-retrieval SOTA formula (normalised-score convex combination, Bruch
> TOIS 2023) — kept **weighted rank-fusion (RRF, vector 1.5×)** on the 2026-08-25 owner ruling,
> because the in-house burn measured equal-weight RRF costing ~2 nDCG and the stronger leg was
> known-good; a later re-burn was pre-authorised to refine it.

---

## 7. Artifacts

* `tools/fusion_formula_ab.py` — the A/B instrument (measurement only; the convex arm lives here).
* `tests/test_fusion_formula_ab.py` — 23 hermetic tests: the convex arithmetic by hand-computed
  values, its drop-in parity with `rrf_fusion`, the scoped arm swap, the one-variable capture,
  the RRF control, and the **measurement-only guard**. Registered under `core`.
* `docs/runbook/4210-fusion-formula-ab-2026-10-11-embedded.json` — the receipt (per-question
  rows, both arms, the α sweep, the RRF weight control, and the paired bootstrap CIs).
