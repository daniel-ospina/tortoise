---
title: "The dense leg under load — mechanism, latency, and what removes it (#13108)"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-17
updated: 2026-11-17
subjects.team: epistemic-team
issue: "#13108"
aboutSubjects: dense (vector) retrieval leg, hybrid degradation, capture-time link-before-create prior, FalkorDB vector index
aboutObjects: STORAGE-ARCHITECTURE.md §14.3 V3, §12.1a size-earned rule, SEARCH-ARCHITECTURE.md §6.2, #11901, #2952, #4999, #6851
---

# The dense leg under load — mechanism, latency, and what removes it

**Issue:** [#13108](https://github.com/daniel-ospina/tortoise/issues/13108) · **Instrument:**
`tools/dense_leg_probe_13108.py` · **Artifacts, this directory:** `probe-*.json` (dense-leg arms +
the eval's own windows), `eval-*.json` (the `#11901` harness, `window_modes` split).
**Decision this feeds:** `STORAGE-ARCHITECTURE.md` §14.3 **V3** — the *index* question, which
`#11901` closed as *"not earned"* on a 390 s sweep. **No index is created here**; §6 is the
evidence a reopen would need.

---

## 0. Preflight — what could and could not be read

**Rung 1 (`mcp__seo-intelligence__perplexity_search`) is not available on this box.** This
harness exposes no MCP tool surface at all, and the `#11901` brief records the same server
failing to spawn (`tsx ENOENT` — it needs a per-checkout `npm install`). **Recorded as
unavailable, not as skipped.** The external pass was made against the Perplexity API directly
with `"model": "sonar"` named on every request — the research skill's cheap rung, no
`sonar-deep-research`, no `sonar-reasoning-pro` — with `PERPLEXITY_API_KEY` from
`/home/cmux/keys.env` (mode 0600, the file the research skill names as its home). **2 queries
attempted / 2 returned**, no 429, total billed **$0.01242**.

**The graph could not be read.** `[mandate-preflight]` reports
`TORTOISE-GRAPH-BRIEF.md` **missing** on this cell (`/tmp/cell-home/.pi/agent/state/`), and there
is no provisioning path for it (#1193 owns the content, #3377 the report). So the internal pass
rests on the docs, and the graph is recorded as **not readable here** — never as "empty".

**The claim gate could not be completed** either: `collision_preflight.py 13108` returns
`INCOMPLETE (exit 2)` because `lane-registry.tsv` was never provisioned in this cell. No lane is
attributable to the issue; the only HITs were this lane's own branch and worktree.

**The box:** 16 CPUs, 31 GB. "At rest" below means load ≈ 3–8; "under load" means the RECORDED
range **load 114–142** (the highest is `windows-8000-under-load-timeout.json` →
`box.loadavg` = `[140.03, 142.31, 139.81]`; `#13121` round 8 F5 widened the band to what the
artifacts hold), produced by 22 real embedder processes (`TORTOISE_EMBED_THREADS=8`) — the capture
write path's own work, not a burn loop. ⛔ **The `eval-*.json` artifacts record NO load field**
(`grep -c load eval-*.json` → 0 0 0), so any load figure attached to an eval row is the operator's
reading of the box at the time, not a measurement; the probe's own `windows`/`arms` rows DO write
`load.loadavg`.

---

## 1. Pass 1 — the internal canon, in my own words

**What the dense leg is.** `run_vector_query` (`search_engine.py:3525`) is the semantic half of a
fused read. It first tries the engine's ANN (`CALL db.idx.vector.queryNodes`, two argument-order
signatures, `#1359`); if that is unavailable or fails it falls through to an exact brute-force
scan (`vec.euclideanDistance` over the label, `WHERE n.embedding IS NOT NULL`); both paths hand
their rows to ONE ordering pass (distance ASC, then the shared recency/id tie, `#10121`) and
convert distance → similarity. Its cost is therefore *a function of which branch it took*, and
`#4999` added the `mechanism` key — `index` | `scan` | `scan_fallback` — so a caller can tell
without reading the source.

**When it is dropped.** Two different events, and the difference matters:

1. **It is never submitted.** `query_vec` is `None` — no embedder, or the encoder raised — so
   `strategies["vector"]` is false and the leg does not run. The trace says `no_query_vector`
   (`ran=False`, `degraded=False`, `#5412`) or the declaration derives `leg_absent`.
   ⛔ **This is not a latency failure at all.** `sdk.tortoise_fts_query` encodes the query on the
   caller's thread **before** `degradation_chain` is called, so the encoder is outside every
   budget below and cannot be what trips it. ⛔ **No encode figure is quoted here**
   (`#13121` review round 5, F5): the probe deliberately excludes the encoder
   (`tools/dense_leg_probe_13108.py:33-40`) and `grep -c encode` over all ten committed
   artifacts returns 0, so a millisecond number would be a claim with no artifact behind it.
   The claim that matters is the CODE one, and it is verifiable at `sdk.py`: the encode call
   precedes the chain, so its cost is charged to the caller's thread, not to the leg's budget.
2. **It is submitted and runs out of budget.** Two budgets, both 500 ms by default: the
   per-leg driver timeout (`timeout_ms: int = 500` on the VECTOR leg, `search_engine.py:3526`;
   `:2832` is `run_fts_query`'s — the LEXICAL leg's — and `runner_timeout` in `degradation_chain`
   is defined at `:4839`, not `:4673`, which is a comment line about `#4210`/`#3019`;
   `#13121` round 8 F4) and the collector's
   `as_completed(timeout=0.5)`. A leg **still not done at the post-deadline rescan** is
   recorded `reason="timeout", ran=True, degraded=True` (`#8450`, `#6851`).
   ⛔ **The cap bounds the WAIT, never the COLLECTION** (`#6851`): `as_completed` can overshoot
   (measured medians of ~100 ms late, worst ~270 ms, i.e. 0.501–0.772 s), and the rescan
   collects every future that is `done()` by then. A leg that finished at 600 ms is
   **collected, not degraded** — which is exactly why an over-budget `run_vector_query` sample
   and a `keyword_only` window are not the same quantity, and why §3 measures both.

**What the system tells the caller.** Labelling exists and is good: `#2952`'s
`declared_degraded_read(leg_trace)` returns
`{degraded_read: True, hybrid: False, vector_leg_unavailable, missing_legs: ["vector"], reason}`
and `require_hybrid_read` raises `HybridReadUnavailableError` rather than let a single-leg read
be called hybrid; `#4999`'s `mechanism` names the path; `#4997` emits one line per graph naming
the unindexed labels. **But all of it is opt-in**: a default product caller passes
`leg_trace=None` and sees none of it. So *prevention* is what is missing — not labelling — which
is why DoD row 3 is `MET` and the issue's complaint is the 45 %.

**The recorded decisions this touches.** `§12.1a`'s **size-earned rule**: an index is earned by
volume, byte-shaped, not by tenant count. `§12.1c`: HNSW is per `(label, property)`, so
"index the objects" means a further resident index for **every** served but unindexed label —
four more today (`Event`, `Object`, `Source`, `Subject`), five in total. `§14.3 V3`: the
**removal** half is DECIDED KEEP (owner, 2026-11-10); the surviving question is **whether those
labels are indexed**, ordered *resolution step first, index second* — and `#11901` closed that
ordering's index half as **"not earned"** on a 390 s sweep. `§15 M1` measured 2.74 ms indexed vs
**9.26 ms** for a full scan of 7,859 `:Object` vectors, at rest, on our own box (2026-09-24).

⛔ **So the contradiction test runs before anything else in §2.** An index is a recorded
decision's premise, not a free improvement: **nothing below creates one.**

---

## 2. Pass 2 — best practice, and the contradiction test applied to it

### 2.1 How a hybrid system should degrade when one leg exceeds its budget

The convergent answer, across implementations:

| system | what a timed-out leg does |
|---|---|
| **Elasticsearch** | per-shard timeout; timed-out shards are cancelled and the response can carry `"timed_out": true` with partial results (`allow_partial_search_results` defaults `true`; `false` turns it into an error) |
| **OpenSearch** | same shape: `search.default_search_timeout` + `search.cancel_after_time_interval`, `search.default_allow_partial_results` defaults `true` |
| **Vespa** | the timeout is a **deadline propagated** through execution; incomplete results are reported via coverage/degradation metadata |
| **Weaviate / Qdrant / Lucene** | cancel or raise (`ExcessiveTimeElapseException`); the "vector leg timed out, keep lexical, mark degraded" policy is the **caller's** job |

⇒ **Surface the degradation; do not retry in-request.** The recommended shape is: start both legs
under one request deadline, give the ANN leg an *adaptive* budget from the remaining time, cancel
it on expiry, return the lexical leg, and **label the result** (`vector: "timed_out"`,
`degraded: true`). Blind retries "consume the remaining deadline and usually worsen tail
latency"; circuit breaking belongs at the service level, not per query.

**Contradiction test.** This *supports* what we already do (`#2952` declares; `#6851` documents
that the cap is a wait bound) and it **contradicts one of the issue's own candidate fixes**: a
bounded in-request retry is a measured anti-pattern. It is therefore **not implemented here** —
and it is not a recorded decision either, so nothing was overridden; it was simply the wrong
option, and saying so is the finding. The "adaptive budget" option is not adopted for the same
family of reasons: the collector's deadline is *shared* by three legs, so raising the vector
leg's share is a change to the read's whole latency contract, and it would not make a scan
cheaper — it would only move the threshold the queueing crosses.

### 2.2 What a full scan costs versus an ANN index

384-dim `all-MiniLM-L6-v2`, cosine, k=10, 100k vectors, 500 queries.

**PROVENANCE (`#13121` rounds 8–9):** this table is excerpted from ONE source — the mnemolith
project's README, published at <https://pypi.org/pypi/mnemolith/0.3.3/json> and, before the rename,
at <https://pypi.org/pypi/aether-cache/0.1.8/json> (one source under two names; `aether-cache` now
serves a "renamed to mnemolith" notice). It is excerpted to **4 of the README's 6 rows**
(`ef=20` and `ef=200` are dropped), so "verbatim" would be wrong. ⛔ **It is a library README
benchmark, not a peer-reviewed study** — the ratios derived from it inherit that weight. ⚠️ The
`/project/…` HTML page is script-challenged for a non-browser client; the `/pypi/<name>/<version>/json`
endpoint is not, and that is what a reader should fetch to check these figures.
**An independently verifiable cross-check disagrees by ~20× and does not weaken the conclusion:**
<https://zayunsna.github.io/tips/2026-10-19-vector_search_hnsw/> (fetched 2026-11-17; scripts and raw
output published) measures an exact NumPy scan at **0.805 ms per query on 133,957** real embeddings
and HNSW at 0.167 ms, with its own stated caveat that NumPy batches 500 queries across cores while
the HNSW queries ran single-threaded. Whichever table a reader prefers, the tuned exact scan of a
100k class corpus is **sub-millisecond**, and ours is **13.26 ms at 1,000** documents (§3.4) — the
gap argument below does not depend on which of the two is used.

| method | recall@10 | p50 | p99 |
|---|---|---|---|
| exact brute force | 100.0 % | **18.58 ms** | 20.52 ms |
| HNSW `ef_search=10` | 90.5 % | 0.49 ms | 1.38 ms |
| HNSW `ef_search=50` | 98.5 % | 0.66 ms | 0.89 ms |
| HNSW `ef_search=100` | 99.4 % | 0.84 ms | 1.41 ms |

⇒ at 100k an index is ~22× faster at 99.4 % recall; **below ~10k an exact scan is normally the
right answer** (**0.186 ms at 1k**, ~1.86 ms at 10k — the linear extrapolation of the measured
18.58 ms at 100k; earlier revisions of this paragraph rounded it to "~0.2 ms", which is what made
the ratios below self-inconsistent — `#13121` round 7 F2).

**This is where the external pass becomes evidence rather than background.** Our own
measurement (§3.4) is **≈13.26 ms to scan 1,000 `:Object` vectors** — **≈71×** the literature's
0.186 ms at the same size (**≈180×** once the two refused round trips are added back,
13.26 + 20.17 = 33.43 ms, which is what the pre-fix read paid). So the cost we are paying is
**not the algorithm's**: it is
FalkorDB evaluating `vec.euclideanDistance` as a Cypher function per row and sorting the label. That reframes V3's option set — "get an index" and "make the scan not be a
per-row interpreter call" both address this, and the second one does not touch the recorded
decision at all.

---

## 3. The measurements

All numbers from `tools/dense_leg_probe_13108.py` against a real FalkorDB (`test_lane.py`
container), the **shipped** `run_vector_query` with `leg_trace` on, `is_embedded=False`, the
capture prior's own `limit=3`. `:Object` is unindexed; `:Point` is the **indexed control at the
same volume with the same names and the same vectors** (the `:Point` mirror is written from the
object's own stored embedding), so the two arms differ in exactly one thing.

### 3.1 The mechanism — measured for two labels, INFERRED for three

| label | samples | `mechanism` |
|---|---|---|
| `:Object` | **100 at rest / 60 under load** (`probe-1000-at-rest-and-concurrent.json`, `probe-1000-under-load.json` → `arms["object@c1"]`) | **`scan_fallback`** on every one |
| `:Point` (indexed control, same names + vectors) | **100 at rest / 60 under load** (`arms["point@c1"]`) | **`index`** on every one |
| `:Event`, `:Source`, `:Subject` | ⛔ **NONE — not sampled here** | **`scan_fallback`, INFERRED, not measured** |

⛔ **The third row is an inference and says so** (`#13121` round 10). `--labels` defaults to
`object,point` (`tools/dense_leg_probe_13108.py`) and `grep -c -E 'Event|Subject|Source'` over all
ten artifacts returns 0, so those three labels were never measured. What IS measured about them is
the census: `census.vector_indexes` lists **`Point` as the only VECTOR index**, so a read on any
other served label cannot take the `index` branch — the same branch, reached by the same probe
result. An earlier revision of this table listed all four unindexed labels together in the
"measured" row, which is the claim class this document exists to refuse.

A bare index probe explains the `scan_fallback`: on this engine **BOTH** argument-order signatures
are refused for `:Object` while the same call answers on `:Point` (`signature B`), and —

> ⛔ the engine answers `Invalid arguments for procedure 'db.idx.vector.queryNodes'` for a
> **missing index** and for a **wrong argument order** alike. The message does not distinguish
> them; the catalogue (`CALL db.indexes()`: `Point` carries `embedding: ['VECTOR']`, the other
> four carry none) is what does.

Each refused round trip costs **≈10 ms** on this box, so an unindexed dense read pays **≈20 ms
before the scan starts** — 58 % of a 34.45 ms read at 1,000 nodes (§5).

### 3.2 Latency, at rest and under load (1,000-node label)

| arm | at rest (load 5.4) | 16 concurrent readers, idle box | under load 114–128 |
|---|---|---|---|
| `:Object` c=1 | p50 **34.45** / p99 45.22 ms · 0/100 over | — | p50 **133.25** / p99 **634.50 ms** · **7/60 over budget** |
| `:Object` c=16 | p50 **723.07** / p99 1092.53 ms · **87/100 over** | — | p50 **551.78** / p99 860.21 · **43/60 over** |
| `:Point` c=1 | p50 **21.04** / p99 28.76 ms · 0/100 | — | p50 **62.15** / p99 370.33 · 0/60 |
| `:Point` c=16 | p50 **450.95** / p99 924.31 · **41/100 over** | — | p50 **461.79** / p99 681.90 · **24/60 over** |

Two things this settles and one it corrects:

* The **scan is 1.6× the indexed read** at rest and the gap widens under load (133 vs 62 ms p50).
* **Contention alone is enough to blow the budget on EITHER label** — the indexed control loses
  41/100 at c=16 on an *idle* box. **So an index is not sufficient on its own**: the store
  serialises work on the graph, and this read's budget is shared by three legs.
* ⚠️ **Correction to the issue's premise:** the encoder is *not* part of this budget (it runs
  before the chain), and `keyword_only` is not simply "the store was slow". §3.3 shows the
  collector's salvage (`#6851`) absorbing a large share of over-budget legs.

### 3.3 The eval's own windows — and why the eval under-reproduces this

One "window" here is `tools/candidate_recall_11901.py`'s own closure: the shipped read at
`limit=3` plus `#2952`'s **strict** predicate (`require_hybrid_read`). The eval runs those
sequentially; a capture fleet does not.

| population | box | windows | `window_modes` | `keyword_only` share |
|---|---|---|---|---|
| **1,000 names**, eval end-to-end, **before** the fix | rest (operator's reading, ~3) | 4,396 | `{hybrid: 4396, keyword_only: 0}` | **0 %** |
| **1,000 names**, eval end-to-end, **after** the fix | rest (operator's reading, decaying from 54) | 4,396 | `{hybrid: 4396, keyword_only: 0}` | **0 %** |
| **1,000 names**, eval end-to-end | **load 109–131** (operator's reading; only `131` is in the artifact's own filename) | 4,396 | `{hybrid: 4396, keyword_only: 0}` | **0 %** |
| **8,000 names**, probe windows c=1 | rest (load 7.7) | 60 | `{hybrid: 60}` | 0 % |
| **8,000 names**, probe windows c=1 | load 114–134 | 120 | `{hybrid: 120}` | 0 % |
| **8,000 names**, probe windows c=1 | **load 122–132** | 60 | `{hybrid: 58, keyword_only: 2}` | **3.3 %** — reason **`timeout`** |

⚠️ **The four committed 8,000-row artifacts predate the `population_arg`/`population_measured`
fix (`#13121` rounds 4–9) and still carry `config.population: 1000` with `population_measured:
null`, beside their own `census.labels.Object.stored: 8000`.** That field is the `--seed`
ARGUMENT on a `--no-seed` run, not the population read; **`census.labels.*.stored` is the reading
to quote**, and it is the field these rows rest on. (The tool now writes both halves; the
artifacts were not regenerated — the same disclosure §5 makes for the empty-arm zeros.)

⛔ **A fourth 8,000/1,000-window row is WITHDRAWN, not carried** (`#13121` round 6, F5): an earlier
revision listed `1,000 names, probe windows c=8/16/32` with `{hybrid: 96}` and window p50s
638/1249/2607 ms. No committed artifact backs it — no 1,000-name artifact has a populated `windows`
key, no artifact records concurrency 8/16/32 windows, and `grep -c '1249'` / `'2607'` over the ten
artifacts returns 0 — so the row is removed rather than left as a number without a source.

⛔ **The eval artifacts carry NO load/box field** (`grep -c load eval-*.json` → 0 0 0), so the "box"
column above is the OPERATOR'S READING of the machine at the time, not a figure the artifact
records — and the one load value that is machine-recorded anywhere is the `131` in
`eval-1000-load131-before.json`'s filename (`#13121` round 7 F4). The window arms below are the
opposite: their `load.loadavg` is written by the probe.

**The two eval runs are BETWEEN-RUN, not paired, and that is declared rather than glossed.**
Their population digests differ — `0defe6b3adae26ee` vs `343c1c49f4568d8d` — because the capture
directory is live (`#11901` recorded the same directory returning ~11 % fewer definite
descriptions two hours apart). That is why the two runs' recall and pooled-candidate figures move
by ±1 (`rank_sum` 343 → 342, `pooled.set_sum` 869 → 870): **the difference is not attributable to
the fix**, and no claim below rests on it. The paired before/after is §5's, taken in one process
on one population under one load.

**The reproduction is real but weaker than the issue's 45 %**, and both facts matter:

* It fires **only at the production-ish label size**: 8,000 names + load ~125 loses the leg with
  `reason="timeout"`; the same load at 1,000 names loses nothing. Since the eval's own population
  is 1,000, **the eval cannot see this bug at its own scale** — a recall number from it is not
  evidence about the loaded production store, which is the point the issue makes in the opposite
  direction.
* ⚠️ **The 45 % was measured on the owner's box and I could not reproduce its magnitude here.**
  The variables I can name that would raise it: a larger label (the production census is a
  `--census` question, not re-quotable here — `#4997`'s figure is 7,859 *objects*), a store
  shared with other lanes' traffic (this lane's container is private), and a slower disk/CPU
  share. What I *can* say is measured: the cost is a monotone function of label size and of
  concurrent readers, and it crosses 500 ms in both.

### 3.4 Where the time actually goes (1,000 nodes, c=1, at rest)

| component | measured |
|---|---|
| two refused `queryNodes` round trips | **20.17–20.28 ms** (`index_attempt[0].elapsed_ms`: 10.18 + 9.97 in `fix-1000-paired-at-rest.json`; 10.19 + 10.06 in `probe-1000-at-rest-and-concurrent.json`) |
| the scan itself (`vec.euclideanDistance` over 1,000 rows + sort) | **13.26 ms** — the `hint` arm of `fix-1000-paired-at-rest.json` (`object[hint]@c1` p50), which is the same read with ONLY those two attempts removed |
| the indexed control, same corpus, `nohint` (inventory forced to None = pre-#13108) | **20.88 ms** |
| the indexed control, same corpus, `hint` (inventory supplied — the shipped path) | **21.00 ms** — a 0.12 ms difference, i.e. the hint is a no-op for an indexed label, as it must be |

⛔ **An earlier version of this table printed `the scan itself ≈23.45 ms` and a `10.11 + 10.05`
split. Neither is in any committed artifact and the pair is arithmetically impossible beside a
34.45 ms total (20 + 23.45 = 43.45).** `#13121` review round 5 (F3) caught it; the figures above
are the artifact-backed decomposition, and the ratio derived from them elsewhere in this brief
was restated with them.

⇒ on an unindexed label, **most of the read is producing a known error**. That is the defect §5
removes, and it is independent of whether an index is ever created.

---

## 4. The cause, stated

1. **The mechanism is the unindexed scan** — `scan_fallback` on 100 % of `:Object` reads,
   `index` on 100 % of the `:Point` control, on the same corpus (§3.1). This is §14.3 V3's
   unindexed-embeddings defect, measured at read time rather than at census time.
2. **Its cost is a function of label size** — 34.45 ms at 1,000 rows, 56.88 ms at 8,000, versus
   21.04 → 21.48 ms for the indexed control, which is **flat in N** (§3.2, §6).
3. **The 500 ms budget is crossed by contention, and it is crossed first and harder by the
   scan** — 43/60 vs 24/60 over budget at 16 concurrent readers; 7/60 vs 0/60 at c=1 under load
   (§3.2). The collector's post-deadline rescan (`#6851`) salvages a share of those, which is why
   an over-budget leg is not yet a keyword-only window (§3.3).
4. **A large share of the scan's cost is not the scan** — two refused index attempts, ~58 % of a
   1,000-node read (§3.4). Removing them removes work from the same queue the timeout is
   measuring.

⇒ **It is BOTH, and the issue's binary is the wrong frame.** The index question (V3) is real and
unresolved; the wasted-round-trips defect is separate, does not touch V3, and is what §5 fixes.

---

## 5. The smallest honest fix, and what it is not

**Chosen: stop paying for an index the store has already measured absent.** The projection
already measures it, and exposes the measurement in TWO halves —
`FalkorProjection._vector_indexed_labels` (the REPORTED pair, which `#11087` pins to read
"measured and empty" when the catalogue answered but nothing in it parsed) and
`FalkorProjection._vector_indexed_labels_for_reads` (the BEHAVIOURAL half, `None` in exactly
that lane). ⛔ **What is forwarded is the behavioural half**, at all three `sdk.py` call sites;
`degradation_chain`/`run_vector_query` take it as the optional `vector_indexed_labels`, and a
label absent from it skips the doomed `queryNodes` attempt and goes straight to the scan. Wiring
the reported pair here would turn an engine catalogue reshape into a full scan of every dense
read, `:Point` included — which is why the two halves exist.

| | before (`None` = not measured) | after (inventory supplied) | change |
|---|---|---|---|
| `:Object` c=1, **1,000** nodes (100 reads each, at rest, paired) | p50 **34.78 ms** | p50 **13.26 ms** | **−62 %** |
| `:Object` c=1, **8,000** nodes (60 reads each, paired) ⚠️ `population` field is pre-fix — see §3.3 | p50 **65.73 ms** | p50 **47.47 ms** | **−28 %** |
| eval windows under load ~130, 8,000 nodes | p50 **15.41 s** | ⚠️ **not measured** | ⚠️ **withdrawn** — see the note below |
| the `#11901` eval, 1,000 names, end-to-end | 442.4 s, `{hybrid: 4396, keyword_only: 0}` | **304.5 s**, `{hybrid: 4396, keyword_only: 0}` | **−31 %** wall clock, **same mode split**, recall@3 300/300 both sides (⚠️ between-run — see §3.3) |

⚠️ **The end-to-end window row is WITHDRAWN, not carried (`#13121` review round 4, F2).**
An earlier version of this table read `p50 17.31 s → 14.89 s (−14 % end-to-end)`. No committed
artifact contains those figures (`grep -o '17\.31\|14\.89' *.json` → nothing), and no PAIRED
8,000-node window run exists: both paired artifacts (`fix-1000-paired-at-rest.json`,
`fix-8000-paired.json`) carry `"windows": {}`, and the one 8,000-under-load window artifact
(`windows-8000-under-load-timeout.json`, single arm, `reads: 0`) reports p50 **15,412.62 ms**.
The single-arm reading is what the row now carries; the paired **:Object** rows stand on their
own artifacts. ⛔ The rule this correction applies is §3.3's own: a paired claim must come from
ONE process on ONE population under ONE load, and where that did not happen the artifact says so
rather than the table rounding it into a delta.

⚠️ **`fix-8000-paired.json` predates the fix commit, and says so now only in this note (`#13121`
review round 4, F3).** Its `code_head` is `d93c4c99e970`, which does not contain
`vector_indexed_labels` (`git show d93c4c99e9:tortoise/search_engine.py | grep -c
vector_indexed_labels` → 0); the fix landed at `a2fe16beb8`. So that pair was measured against an
UNCOMMITTED draft of the fix, the tool recorded no working-tree state, and "was the 8,000 pair
measured on the fix?" was unanswerable from the artifact. The tool now records `worktree.dirty`
+ a diff digest beside `code_head` so the next run cannot be ambiguous; the figure is otherwise
sound (both arms `scan_fallback`, `hint` 47.47 ms vs `nohint` 65.73 ms p50), and re-running the
pair at the merged head is the way to remove the qualification entirely.

Why this one and not the alternatives:

* **Not a bounded retry** — §2.1: it is the documented anti-pattern, and it spends the deadline
  the other legs need.
* **Not an adaptive budget** — it does not make anything cheaper; it moves the threshold the
  queueing crosses, and it changes the read's whole latency contract.
* **Not an index** — that is V3's, and this lane does not take it.
* **Not "hide the fallback"** — the mechanism is still `scan_fallback`, `degraded` is still
  `False` (a scan returns semantic rows), `declared_degraded_read` is untouched, and DoD row 3
  still reads **MET** (§7). The fix makes the read *faster*, never *quieter*.

⚠️ **What it does NOT do:** it does not make the scan cheaper, does not reduce the label's
coupling to N, and does not eliminate timeouts under sustained contention. At 8,000 nodes and
load ~130 the dense leg still timed out (§3.3).

---

## 6. What an index would be for, and what it would cost — the V3 evidence

**No index was created — on the labels the decision is about.** ⛔ Stated exactly (`#13121` round
11 F1): this lane creates **no** index on `:Object`, `:Subject`, `:Event` or `:Source` — not by a
flag, not by an argument. What a **fresh probe graph** does get is the `:Point` VECTOR index, from
the projection's own `_ensure_indexes()` on construction (`tortoise/projection/__init__.py`), which
is the index the product already ships (`#4997`) and the reason the control arm can be a control;
`tools/dense_leg_probe_13108.py`'s docstring names it as the one thing the tool creates that it does
not own, and `census.vector_indexes` records it on the probe's own graphs. An unqualified "no index
was created" would be false, and this section says so before making the argument that the decision
needs.

* **What it would be for:** removing the size dependence. The indexed control's p50 is
  **21.04 ms at 1,000 rows and 21.48 ms at 8,000** — flat; the scan's is **34.45 → 56.88 ms** and
  rising. Under load 114–128 at c=1, the indexed control stays **0/60 over budget** while the
  scan is **7/60 over**.
* **What it would NOT fix:** contention. At 16 concurrent readers the *indexed* leg is 41/100
  (idle) and 24/60 (loaded) over budget. An index raises the ceiling; it does not remove the
  queue.
* **What it costs:** per §12.1c, HNSW is per `(label, property)`, so indexing the served-but-
  unindexed labels is **four further resident index structures** in FalkorDB (five in total) on
  the resource that dominates the bill — or **one filtered index** in Postgres, which is where
  V1 already decided the vectors go. The `:Point` control in this measurement *is* the
  "one more resident index" cost, empirically: it is the same corpus, indexed.
* **What V3 asked for and this lane does not answer:** the per-label **census**
  (`dod_5090_probe.py --census` on a named graph). Every figure above is a *read-time* latency on
  a seeded population of 1,000 / 8,000 — sizes chosen to bracket the census-era range, not the
  live label.

⚠️ **And a third option this measurement opens, which is neither index nor budget:** the scan is
**≈71×** a tuned exact scan of the same size (13.26 ms vs 0.186 ms, the §2.2 linear
extrapolation of the measured 18.58 ms at 100k — **per 1,000 rows**, not per 10k). ⛔ The pre-fix
read paid **≈180×** once the two refused round trips are added back (13.26 + 20.17 = 33.43 ms). If
the requirement is "the dense leg fits in 500 ms at 8,000 rows", a scan that costs 0.2–2 ms instead
of 13 ms achieves it without touching V3 at all. That is a bigger change than this lane should make, and it is filed
as such rather than assumed.

---

## 7. Falsifiers, each with its output

| falsifier | status |
|---|---|
| the mechanism and its latency are **measured, not inferred** | ✅ §3.1 (`scan_fallback` 100 % vs `index` 100 %), §3.2 (latency tables, load printed beside every arm), §3.4 (component decomposition) |
| the keyword-only share is measured **before and after on a load-matched box** | ✅ §3.3 (1,000 names, eval end-to-end: 0 % at rest **and** 0 % at load 131 — recorded as a *negative* result and explained), §5 (paired before/after in one process under one load) |
| if no index is proposed, the issue says **what it would be for and what it would cost** | ✅ §6 — flat-in-N latency for the indexed control, the 4-vs-1 index cost from §12.1c, and the census this lane does not substitute for |
| **DoD row 3 still passes** — a genuine fallback is still reported | ✅ `dod_5090_probe.py`: **3 MET / 1 PARTIAL**, row 3 `MET` (`index`/`scan`/`scan_fallback` all still report, `#2952` untouched on 3/3 mechanisms) |
| `uv run python tools/dod_5090_probe.py` still reports 3 MET / 1 PARTIAL | ✅ same run — row 4 is the `PARTIAL` |

## 8. What is still open

1. **V3's index question — for the owner.** §6 is the evidence; this lane does not create one.
2. **The 45 % magnitude is not reproduced here** (§3.3). Reproducing it needs a census-sized
   label and a store shared with fleet traffic; both are named, neither is guessed at.
3. **The scan's ≈71× gap to a tuned exact scan** (§2.2, §6) — an alternative remedy with no
   decision attached, unmeasured beyond the comparison above.
4. **The default product caller still sees no degradation** — `leg_trace=None` everywhere
   (`#2952` is opt-in). Surfacing `degraded_read` on the product's own read is unaffected by this
   lane and remains the issue's third candidate fix.
