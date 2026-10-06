---
title: "W7A — the D3 instrument can now SEE the dense leg: a switchable seeder, and the post-fix gold ranks"
type: runbook
domain: operations
doc_status: live
created: 2026-09-20
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# W7A — the D3 instrument can now SEE the dense leg

**⛔ FIRST LINE — the seeding mode of every receipt here.** `tools/ask_spotcheck.py`'s
seeder is now **SWITCHABLE**, and every number below names its arm:

* **`embed=True` (the DEFAULT, "embedded")** — turn Points carry the product's own vector,
  stored through **#4304's store seam** (`embeddings.encode_batch_for_store` with
  `proj.required_embedding_dim`). This is the post-#4194 product shape. **All primary gold
  ranks below are the `dense` arm.**
* **`embed=False` ("backlog")** — the pre-#4194 / no-embedder store; the state whose
  re-embedding is the user-facing choice owned by **#4197**. The `backlog` column below.

A receipt that does not name its seeding mode is not evidence; the D3 instrument's own
receipt now carries a `seeding_mode` block derived from the seeder's own default.

**Tree:** `fix/w7a-seeder-embeds-turns`, merge-base `f2b3e91b2` (contains #4304 `6355a5bc4`).
The gold-rank diagnostic measured the seeder at `fb0fe23c9`; the streamed instrument attempt
pinned `74c9b7997`. **Fixture sha256 unchanged:**
`7f4062643323af4e5d0fec0b98bb3e3ca8499362c3f7e07a83c55f07633d15fa` — asserted on every
`tools/ask_shape_rate.py` instrument run (its `FIXTURE_SHA256` pin); the two W7A
diagnostics above read the fixture by path and their receipts do not embed the hash, so here
the hash is verified at this tree, not by the gold-rank run.

---

## 1. The change (the seeder, not the ruler)

`tools/ask_spotcheck.py::_seed_memory` / `seed_capture_turn_store` gain
`embed: bool = SEED_TURNS_EMBEDDED_BY_DEFAULT` (**True**). Embedded mode:

* composes the stored `[role] <content>` text **once** (the string stored *is* the string
  encoded), exactly as `TortoiseSDK.capture_session` does;
* routes it through **the STORE seam** — `embeddings.encode_batch_for_store(texts,
  proj.required_embedding_dim)` — **never the raw encoder**. The width constraint belongs to
  the store's Point HNSW index (#4280): calling `compute_embeddings` directly would re-create
  the #4280 defect in a new place (dropping a usable self-consistent vector on the
  index-less brute-force lane, or handing `vecf32` a wrong-width vector on an indexed store);
* writes the product's **own three-way guard** (`WITH t, t.content_hash AS prior_ch` … new
  vector / preserve on unchanged content / clear on changed), so an embedder-less re-seed
  cannot keep a vector for text no longer on the node.

`embed=False` retains the un-embedded backlog shape — the coverage #4197 still needs.
`tools/gen_ask_transcripts.py::_seed` is deliberately left `embed=False`: the committed
transcript goldens (and the frozen instrument's known-GREEN control) were recorded on the
pre-#4194 store, and re-recording them is a new-instrument change.

**Nothing about the instrument's ruler changed:** legs, thresholds, reader pin, fixture, and
the pre-registered rule are untouched. The only instrument edits are additive/reporting:
`seeding_mode`, `assembly_budget` + per-question `context_tokens`, and a streamed
`deg=`/`tokens=` on the per-question line.

---

## 2. THE PRIMARY RESULT — per-question gold-turn rank in the ask pool

`docs/runbook/w7a-gold-rank-2026-09-20.json` — five window-miss questions, **zero paid
calls**. Rank = position of the gold turn in the ask-lane pool (`tortoise_fts_query`,
`limit=pool_size=120`); "in cut" = rank ≤ **40**, the reader-window cut the ask lane applies.
The ONLY difference between the arms is the seeder's `embed` switch — no manual vector
attachment.

| question | gold turns | backlog (`embed=False`) | **dense (`embed=True`, DEFAULT)** |
|---|---|---|---|
| `0a995998` | 3 | 20, ABSENT, ABSENT | **3, 38, 22** |
| `1d4e3b97` | 2 | 56, 84 | **8, 7** |
| `1de5cff2` | 1 | 67 | **67** (vector strategy timed out — see §4) |
| `ceb54acb` | 1 | 93 | **5** |
| `e9327a54` | 1 | 89 | **11** |
| **IN THE 40-ITEM CUT** | **8** | **1 / 8** | **7 / 8** |

Dense-arm seed census (`MATCH (p:Point) WHERE p.embedding IS NOT NULL`): **484, 474, 473,
493, 533** embedded Points per question; the backlog arm seeds **0**.

**Why this is the dense leg and not noise** — the same runs' vector-leg traces:

* `backlog` (4 of 5): `vector ran=true degraded=true reason=no_embeddings count=0`
  — the **exact 21/21 signature** the frozen instrument reported before this change.
  (`ceb54acb` is the exception: `reason=timeout count=0` — the same 500 ms
  multi-strategy collector deadline noted for the dense arm below, not a
  `no_embeddings` state.)
* `dense` (4 of 5): `vector ran=true degraded=false reason=ok count=120` — the dense leg
  returns a full 120-item leg.
* `dense` (`1de5cff2`): `vector ran=true degraded=true reason=timeout count=0` — the 500 ms
  multi-strategy collector deadline fired under load; its rank did not move. **This is a
  host-load artifact of the collector budget, not a missing vector** — the question's 473
  turn vectors are stored (embedded count above).

This reproduces the brief's expectation exactly: **7 of 8 gold turns inside the cut, up from
1 of 8.**

---

## 3. `retrieval_degraded` — before / after

| | keyword-only (recorded) | **after, dense leg alive** |
|---|---|---|
| `retrieval_degraded` | **21/21** (every run; vector leg `no_embeddings`) | **NOT 21/21** — vector leg `reason=ok` on 4/5 measured questions (0/5 `no_embeddings`); 1/5 `timeout` under load |
| per-question gold rank | 56/67/84/89/93 + 2 absent | **3, 38, 22, 8, 7, 67, 5, 11** |
| `shape_rate` | 0.238 (spread **0.238–0.333**) | **NOT OBTAINED — see §4** |
| L1 abstain / L2 provenance / L3 grounding | 10/21 · 21/21 · 9–10/21 | **NOT OBTAINED — see §4** |
| assembly budget filled (of ~8k tokens) | ~70 % median | **NOT OBTAINED — see §4** |

**The gate is answered:** the seeder fix took. The dense leg is no longer inert on captured
turns (`no_embeddings` → `ok`), and the primary retrieval consequence is 7/8 gold turns in
the cut.

---

## 4. What could NOT be verified — the full 21-question instrument pass

`tools/ask_shape_rate.py --mode live` was launched **six times** (pinned to the branch head;
launch headers in `w7a-instrument-attempt-2026-09-20.log`) and **no run completed a single
question** before being stopped. The blocker is the **host**, not the code:

* The instrument must **re-seed all 21 questions** through the **embedded FalkorDBLite**
  engine (it passes an explicit `db_path`, which by SDK precedence wins over
  `TORTOISE_DB_URI`), i.e. ~1,000 Cypher round-trips per question against a per-process
  `redis-server`.
* Measured **per-question seed wall time on the loaded host: 412 s** (question `0a995998`,
  484 turns, CPU-forced) — extrapolating to **~2.4 h per 21-question pass**, i.e. **~4.8 h
  for the mandated two runs**.
* Host load over the window averaged **100–362** on 10 CPUs (`load average: 362.33 261.58
  211.41` at the stop). The fleet's own `pi` sessions are the load.
* Both embedder devices were tried: **MPS** stalls in repeated **MPSGraph/MLIR graph
  compilation** (`at::native::mps::make_mps_graph`, `arange_mps_out`, variable-length
  batches) under load; **CPU** (forcing `torch.backends.mps.is_available = False`) is
  predictable but starved. `nice -n 10` was tried first and starves the embedded engine
  further; the last attempts ran at normal priority.
* A single paid **reader validation call** was made to prove the reader path is not the
  blocker: `deepseek-direct` returned in **1.0 s** (`route=deepseek-direct`). The reader is
  fast; the **seeding** is the cost.

**Consequence for the deliverable:** the *primary* result (gold ranks) and the *gate*
(`retrieval_degraded`) are established. The **post-fix `shape_rate` aggregate, the L1/L2/L3
per-leg counts, and the assembly budget could not be measured on this host** and MUST be
recorded on a quiet one — running the same pinned command:

```bash
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python tools/ask_shape_rate.py \
  --pin-sha "$(git rev-parse HEAD)" --mode live \
  --receipt docs/runbook/ask-shape-rate-w7a-<date>.json
```

The aggregate must be reported **with the byte-identical spread 0.238–0.333** beside it
(four questions — `2133c1b5`, `d6233ab6`, `eace081b`, `gpt4_d84a3211` — flip on the reader's
own output; a one-question delta is not evidence).

---

## 5. Consequence, per the three cases

* **Gold turns now inside the cut** (7 of 8; `0a995998` 20→3, `1d4e3b97` 56/84→8/7,
  `ceb54acb` 93→5, `e9327a54` 89→11): the pool **ordering** was the problem, and the **free
  fusion work (#4210)** is the win — no new retrieval machinery is needed to surface these.
* **Gold turn still below the cut** (`1de5cff2`, rank 67): its vector strategy hit the 500 ms
  **collector deadline** (`reason=timeout`), so the leg contributed nothing on that question.
  This is neither a retrieval-quality miss nor a missing vector — on a quiet host the
  strategy completes (the four comparable questions returned `count=120`). If it still fails
  on a quiet host, it becomes a **retrieval/graph** question and the candidate-introduction
  route matters.
* **`retrieval_degraded` still 21/21:** it is **not** — the fix took (§3). `no_embeddings` is
  gone.

---

## 6. Cost & artifacts

* **Paid reader calls made: 1** (the validation call only). The six instrument attempts
  reached no question, so they incurred **0** paid calls. The gold-rank and assembly
  diagnostics are **zero-paid** (local `BAAI/bge-small-en-v1.5`).
* Artifacts in this receipt's commit:
  * `docs/runbook/w7a-switchable-seeder-2026-09-20.md` (this file)
  * `docs/runbook/w7a-gold-rank-2026-09-20.json` (**the primary result**)
  * `docs/runbook/w7a_gold_rank_diagnostic.py` (zero-paid, two-arm, names its arms)
  * `docs/runbook/w7a_assembly_budget_diagnostic.py` (zero-paid; **not run to completion**
    here — retained for the quiet-host pass)
  * `docs/runbook/w7a-instrument-attempt-2026-09-20.log` (the launch headers of the six
    attempts, with the host load at each start)
* Seeder/tests: `tools/ask_spotcheck.py`, `tests/test_ask_seed_shape.py` (pins the embedded
  default, the `embed=False` backlog shape, and — by declaring a matching **and** a
  mismatching store width — that the seeder consults `required_embedding_dim`; a raw-encoder
  call fails the mismatch arm).

## 7. Not a rule change

The pre-registered rule (`shape_rate ≥ 0.80` ⇒ ADOPT) is untouched. No ADOPT/DO-NOT-CLAIM
verdict is claimed here, because the aggregate was not measured. The historical 0.90 is a
different reader AND instrument and remains non-comparable.
