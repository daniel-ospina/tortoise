# M1 — Our read path, phase by phase: where the milliseconds actually go

**Date:** 2026-09-19 · **Job:** WAVE-R / M1 (`~/.pi/agent/state/b6-briefs/WAVE-R-M1-latency-profile.md`)
**Tree:** worktree based on **`origin/main` @ `c51bd8638`** · **Measurement-only: no product behaviour changed.**

The owner asked: *"is there something consuming a lot of the time budget that we could improve?"* This is
the first phase-by-phase measurement of our own read path. It answers that question with numbers, and it
surfaces one finding that outranks every ranking tweak: **the real capture path stores turn Points with no
embedding, so the dense leg has no material for the answer-bearing turns — while the query is still embedded
on every call** (filed as **#4194**).

---

## 0. Headline findings

1. **The query embedding is the single biggest retrieval-phase consumer: p50 38–73 ms (~47–60 % of the
   LLM-free retrieval budget).** It is a local CPU model call (bge-small), not network I/O. Isolated on this
   box: p50 24.5 ms / p95 49 ms; inside the lane, p50 38–73 ms under load.
2. **On the real capture-shaped corpus that embedding is spent for nothing.** Every episodic turn Point the
   capture path writes carries **no `embedding` property** (census in §6: 27/27 turn Points lack one), so the
   vector leg runs and returns **0 candidates** (`leg_trace: vector ran=True count=0`). The encode is paid in
   full, contributes nothing, and the ranking is **keyword-only**.
3. **The sequential post-retrieval graph chain is the second consumer: p50 12.5 ms (17.5 ms including the
   entity fetch) — ~22 % of the retrieval budget** — six independent round-trips (eight on a graph with
   operator edges). Its cost is dominated by *query execution*, not network RTT: on loopback the same six
   round-trips cost ~2 ms of RTT.
4. **The ANN/vector index itself is noise**: `leg_vector` p50 4.6 ms and returns **nothing** on this corpus.
   The ANN-tuning/quantisation literature is irrelevant at our size — confirmed.
5. **Top 3 = encode (~47–60 %), post-retrieval graph chain (~22 %), token estimation (~11 %)**; the parallel
   leg wave is ~7 %; fusion, dedup, boost, assembly and rendering together are **< 1 %**.
6. **The 5-question diagnostic: 3 ordering failures, 1 retrieval failure, 1 already inside the cut** (§5).

---

## 1. What was measured, and the caveat that governs every number

**Path instrumented (the real one):** `tortoise/ask_lane.py::run_ask_lane` → `tortoise/sdk.py::tortoise_fts_query`
→ `tortoise/search_engine.py` legs → `annotate_ask_hits` → `dedup_pool` → `apply_evidence_boost` →
`assemble_context` → `render_context` → reader call. Instrumentation is **runtime monkeypatching only**
(`_GuardedGraph.query`, the leg functions, the assembly functions, `_ask_reader_complete`) — no product file
was edited.

**Corpus:** the ask lane's real shape is a **capture-shaped turn store** (`{session}_t{i}` turn Points,
`is_episodic=true`, `(:Session)-[:CONTAINS]->(:Point)`, **no embedding**), seeded from the frozen fixture
`tests/fixtures/ask_spotcheck_composition.json` (sha256
`7f4062643323af4e5d0fec0b98bb3e3ca8499362c3f7e07a83c55f07633d15fa`). The latency run seeds one haystack
(533 Points) and runs 12 queries after a warm-up.

**Reader:** a deterministic stub for the phase table (so no paid call pollutes the retrieval numbers); the
real pinned reader (`TORTOISE_ASK_PROVIDER=deepseek-direct`, `deepseek/deepseek-v4-flash`) for **3** paid
calls, reported separately in §4.

**⛔ Environment caveat — this box is heavily loaded (load average ≈ 40 on 10 CPUs; the fleet *is* the load).**
Absolute milliseconds are pessimistic and vary run-to-run by 2–3×; the **shares** are the robust output.
The committed receipt is one 12-query run; a second (heavier-load) 12-query replicate of the same queries
measured `query_encode` p50 **72.6 ms**, ask total p50 **149.6 ms**, post-retrieval chain p50 **39.2 ms**,
`legs_wave` 9.2 ms, `token_estimate` 9.7 ms — the same ordering and comparable shares. The rank diagnostic
(§5) is deterministic and unaffected by load.

---

## 2. The phase table (ask lane, 12 queries, stub reader; ms)

Committed receipt: `evidence/2026-09-19-m1-read-path-latency.json`.

| phase | p50 | p95 | max | what it is |
|---|---:|---:|---:|---|
| **`query_encode`** | **37.8** | 51.3 | 51.3 | bge-small `encode([query])` — **a model call (CPU)** |
| `classify` | 0.001 | 0.003 | 0.003 | `classify_query` (deterministic) |
| `detect_qtype` | 0.026 | 0.053 | 0.053 | `detect_question_type` (deterministic) |
| `legs_wave` | 5.4 | 39.6 | 39.6 | the parallel leg wave (`ThreadPoolExecutor(max_workers=3)`) |
| └ `leg_fts` | 3.4 | 26.3 | 26.3 | lexical leg (overlaps the wave) |
| └ `leg_vector` | 4.6 | 31.7 | 31.7 | dense leg — runs, **returns 0** on this corpus |
| └ `leg_structural` | 0.011 | 0.018 | 0.018 | kind-scan (no kind on the ask lane) |
| `prf_expansion` | 0.7 | 2.0 | 2.0 | A4 `search_keys` second sparse pass |
| `fusion` (RRF) | 0.000 | 0.000 | 0.000 | rank fusion — free |
| `post_ep_annotate` | 2.7 | 4.4 | 4.4 | `annotate_ep_batch` — **1 round-trip** |
| `post_entity_fetch` | 2.3 | — | — | point content/session/`has_answer` fetch — **1 round-trip** |
| `post_relationships` | 3.1 | 10.5 | 10.5 | `get_relationships_bounded` — **2 round-trips** here (up to 5) |
| `post_epistemic_state` | 3.5 | 14.9 | 14.9 | `fetch_point_epistemic_state` — **1 round-trip** |
| `post_ask_annotate` | 2.6 | 9.0 | 9.0 | `annotate_ask_hits` (session date/speaker) — **1 round-trip** |
| `dedup` | 0.008 | 0.012 | 0.012 | `dedup_pool` |
| `evidence_boost` | 0.21 | 0.24 | 0.24 | A5 stored-mark boost (no marks → no-op) |
| `assemble` | 0.54 | 0.84 | 0.84 | `assemble_context` (pre-#4105 8k tok / 32 KiB caps) |
| `render` | 0.098 | 0.112 | 0.112 | `render_context` |
| `token_estimate` | 8.9 | 10.1 | 10.1 | `estimate_tokens_ask` (pure-Python word count, several calls) |
| `rerank` (A7, default OFF) | 0.007 | 0.014 | 0.014 | no-op |
| `reader` (stub) | 0.014 | 0.017 | 0.017 | stub — real reader in §4 |
| **ASK TOTAL (stub reader)** | **80.1** | 108.3 | 108.3 | |

*(n=12, so the tabulated p95 is the second-largest sample; treat it as a tail indicator, not a population
p95. `post_entity_fetch` is per-caller, not a phase-timer row — see §3.)*

### Top 3 consumers (share of the p50 retrieval budget)

| rank | consumer | p50 | share | bound by |
|---|---|---:|---:|---|
| **1** | **query encode** (bge-small, local CPU) | **37.8–72.6 ms** | **~47–60 %** | **model call (CPU)** |
| **2** | **post-retrieval graph chain** (5 round-trips + entity fetch) | **17.5 ms** | **~22 %** | store execution (+ RTT) |
| **3** | token estimation + leg wave | **~15 ms** | **~18 %** | pure CPU + store |
| — | fusion + dedup + boost + assemble + render | < 1 ms | **< 1 %** | pure CPU |

*(Phases nest — `legs_wave` contains the per-leg timings — so shares sum to slightly over 100 %; the ranking
is what matters. The post-retrieval chain's four named phase-timer rows sum to p50 **12.5 ms**; adding the
inline entity fetch (attributed to the `tortoise_fts_query` caller) gives **17.5 ms** — the receipt carries
both as `post_retrieval_chain_ms` and `post_retrieval_chain_incl_entity_fetch_ms`.)*

### Model-call-bound vs I/O vs CPU

- **Model-call-bound:** the query encode (38–73 ms p50) is the dominant *retrieval* term — a **local CPU
  embedder**, not a network call. The reader LLM (~1 s, §4) dominates the ask **end-to-end** but is a
  separate leg and must not be folded into retrieval.
- **I/O-bound:** the store path (legs + post-retrieval) ≈ **23 ms p50** (~29 %). Ten round-trips per query.
- **CPU-bound:** token estimation + assembly + fusion + rendering ≈ **10 ms p50** (~13 %); all
  sub-millisecond except `token_estimate`.

---

## 3. Store round-trips per query, and the "8 sequential round-trips" hypothesis

**Measured: 10 FalkorDB round-trips per ask query** (exactly 10 on every one of the 12 queries).

| group | caller | round-trips/q |
|---|---|---:|
| legs | `run_fts_query` | 1 |
| legs | `run_vector_query` | 2 |
| legs | `run_structural_query` | 0 (no kind) |
| PRF | `_search_keys_prf_expansion` | 1 |
| **post-retrieval** | `annotate_ep_batch` | 1 |
| **post-retrieval** | entity fetch (inside `tortoise_fts_query`) | 1 |
| **post-retrieval** | `get_relationships_bounded` | **2** (up to 5) |
| **post-retrieval** | `fetch_point_epistemic_state` | 1 |
| **post-retrieval** | `annotate_ask_hits` | 1 |
| | **total** | **10** |

**The hypothesis under test** — *"8 post-retrieval graph round-trips are sequential and independent, saving
~7×RTT if parallelised"* — is **structurally confirmed and quantitatively resized**:

- **Count:** the post-retrieval decoration is **6 sequential round-trips on this corpus**, not 8, because
  `get_relationships_bounded` issues Q1a+Q1b only when the result Points have **no operator edges**
  (turn-only corpus). On a real product graph with operators it issues up to **5** (Q1a, Q1b, Q2b, Q2-crit,
  Q2f) → **up to 9 post-retrieval round-trips** (matching the research's 8 + the ask annotation).
- **Independence:** confirmed — each call takes the same `result_ids` and returns an independent dict.
- **RTT (measured):**
  - **Docker FalkorDB, loopback** (`localhost:6379`, no TLS): `RETURN 1` **p50 0.319 ms** / p95 0.671 ms;
    Point-by-id p50 0.337 ms.
  - **Embedded (FalkorDBLite, unix socket)** on this loaded box: `RETURN 1` **p50 2.605 ms** / p95 58 ms.
  - ⚠️ **The RTT that decides this lever is the DEPLOYED one** — a networked/cloud FalkorDB at 5–15 ms RTT
    would make 5 saved round-trips worth 25–75 ms. That RTT is **not measurable from this box** and remains
    the open number (the sibling research reached the same conclusion).
- **The real cost is execution, not RTT.** The sequential post-retrieval chain measures **12.5–39 ms p50**
  across runs (17.5 ms including the entity fetch in the committed run), while 6× loopback RTT is **~2 ms**.
  So parallelising the chain overlaps *server execution* — the realistic saving is up to the **whole chain
  (~12–39 ms p50)**, of which pure RTT is a small slice on any local deployment. On a LAN/cloud store the
  saving grows with RTT.

---

## 4. The reader leg (reported separately — it is the LLM, not retrieval)

3 deliberate paid calls on the real pinned reader (`deepseek-direct` / `deepseek-v4-flash`);
receipt `evidence/2026-09-19-m1-reader-runs.json`:

| run | ask total | reader call | reader share | cost |
|---|---:|---:|---:|---:|
| 1 | 1120 ms | **985 ms** | 88 % | $0.00139 |
| 2 | 1702 ms | **1531 ms** | 90 % | $0.00139 |
| 3 | 1419 ms | **993 ms** | 70 % | $0.00117 |

**The reader is 70–90 % of ask end-to-end** (p50 ≈ 993 ms; the share varies with how contended the embedding
phase is). Retrieval tuning changes single-digit percent of the ask total; it matters for the **LLM-free
`tortoise_search` lane**, which is where these phase numbers bite.

---

## 5. The practitioner's diagnostic — the 5 window-miss questions

**Method.** For each of the 5 questions, seed that question's haystack from the frozen fixture (capture
shape), run the ask lane's retrieval at the **then-shipped defaults** (`limit=40`, `pool_size=None` → leg depth
`max(80,120)` — this profile predates #4105, which resolves `limit=200`, explicit `pool_size=200`), and locate the
**`has_answer`-marked** answer-bearing turn's rank in the fused ordering. The
gold turns were verified against the fixture's own `has_answer` flags (they match the turns named in
`docs/planning/2026-08-31-2070-scoping-package.md`).

**Classification rule (the brief's):** rank ≤ 40 → in the cut; 41–120 → in pool, below the cut (ordering);
> 120 → absent from the pool (retrieval).

| question | answer-bearing turn(s) | full fused rank | class |
|---|---|---:|---|
| `0a995998` (count = 3) | `answer_afa9873b_2_t10` | **21** | in cut |
| `0a995998` | `answer_afa9873b_3_t6` | **136** | **ABSENT (> 120)** |
| `0a995998` | `answer_afa9873b_1_t4` | **148** | **ABSENT (> 120)** |
| `1d4e3b97` | `answer_e6b6353d_t2` | **41** | below cut |
| `1d4e3b97` | `answer_e6b6353d_t4` | **75** | below cut |
| `1de5cff2` (Veja) | `answer_ultrachat_440262_t7` | **60** | below cut |
| `ceb54acb` | `answer_sharegpt_cGdjmYo_0_t3` | **74** | below cut |
| `e9327a54` (Sugar Factory) | `answer_ultrachat_480665_t7` | **30** | in cut |

**Per-question verdict (n of 5):**

- **ABSENT FROM THE POOL (retrieval failure — no reranker can save it): 1 of 5** — `0a995998`. It is a
  multi-session **count** question: only **1 of its 3 required turns** enters the 120-pool (rank 21); the
  other two (136, 148) never do. The reader sees 1 of 3 → it cannot count to 3 (the #2280 run recorded the
  reader answering "2 items" on exactly this question).
- **IN POOL, BELOW THE 40-CUT (ordering failure — a re-ranker/weighting can fix it): 3 of 5** —
  `1d4e3b97` (41/75), `1de5cff2` (60), `ceb54acb` (74).
- **ALREADY IN THE CUT: 1 of 5** — `e9327a54` (rank 30; its gold turn *is* in the assembled 40-item
  context on the measured tree).

*(The control's machine label in the diagnostic JSON labels `0a995998` `IN-CUT` because its rule is "any
gold turn in the cut"; the report's per-question verdict for it is a **retrieval failure**, because a count
question needs all three of its required turns. Same data, different question — stated so the two labels do
not look contradictory.)*

**Discrepancy with the previously recorded ranks (67 / 84 / 89 / 93 / 147) — reported honestly.** On the
measured tree under capture-shape seeding my ranks are **60 / 75(and 41) / 30 / 74 / 21·136·148**. Three are
within a few ranks of the recorded set; **`e9327a54` measures 30 (in cut), not 89**. I could not reproduce
89 on this tree from any configuration I tried (capture-shape seed, ask-lane defaults; `e9327a54`'s gold
turn is unambiguous: the dessert list naming "The Sugar Factory … at Icon Park"). Two live possibilities:
(a) the recorded 89 came from a different seeding/rank definition (e.g. eval-parity ingest with the dense leg
active, or a rank in the deduped pool), or (b) `e9327a54` has since moved into the cut. **I am reporting the
reproducible measurement, not the historical number**; this is flagged as the one place the two disagree.

**What this changes for the plan:** only **1 of the 5** is a true retrieval failure, and that one is a
count question whose other two sessions are also missing — the fix is recall (pool membership), not
re-ranking. The other 3 are genuine ordering failures within the pool, where a cheap reorder can act. One
(`e9327a54`) already reaches the reader.

---

## 6. ⛔ The headline: do real captured turns carry embeddings? **No.**

### Read-only census on a COPY of the real store (`~/.tortoise/tortoise.db` copied to `/tmp`; the original was never touched)

| quantity | measured |
|---|---:|
| `Point` total | 55 |
| `Point` with an `embedding` property | 18 |
| **episodic turn Points (`is_episodic=true`)** | **27** |
| **episodic turn Points WITH an embedding** | **0 / 27** |
| non-episodic Points | 28 |
| non-episodic Points WITH an embedding | 18 / 28 |
| embedded by kind | `statement` 16, `observation` 2 |
| `Session` / `Event` / `CONTAINS` edges | 1538 / 3732 / 27 |

This is not a seeding artifact. **The write path itself never embeds a turn:**

- `tortoise/sdk.py:3349-3360` (`capture_session`'s per-turn loop) writes each turn with a raw
  `MERGE (t:Point {id:$id}) SET t.content=…, t.pointKind=…, t.is_operator=false, t.speaker=…,
  t.is_episodic=true, t.status=…, t.createdAt=…, t.updatedAt=…, t.content_hash=…` — **no `embedding`**.
- `tortoise/hosted_api.py:8431-8440` (`_capture_session_impl`) is the same raw Cypher, same omission.
- By contrast `create_point` (`tortoise/sdk.py:2647-2651`) **does** compute and store
  `compute_embedding(content)` — extracted (non-episodic) Points are embedded; **turn Points are not.**

**Consequences, in order of importance:**

1. **The product's ranking over captured turns is keyword-only (FTS).** The dense leg has no turn material;
   `leg_trace` records `vector ran=True count=0` on every ask query against a capture-shaped store. The
   eval's `retrieval_degraded = 21/21` was **not** an artifact of the instrument — it reproduces the real
   write path.
2. **The query is still embedded on every call (~38–73 ms p50).** On a turn-only corpus that work is
   **100 % wasted** — paid, then discarded because there is nothing to compare against. This is the single
   biggest addressable millisecond consumer on the LLM-free read path.
3. The vector-arm benchmarks (2.42 ms p50 / 28.9 ms p95) were measured on **embedded, non-turn** points; the
   ANN literature is doubly irrelevant here — the arm is small *and* starved of material for the turns that
   answer questions.

**Filed as #4194; not fixed here** — this job changes no product behaviour.

---

## 7. Answering the owner's question directly

*"Is something consuming a lot of the time budget that we could improve?"* — **Yes, two things, and one of
them is pure waste:**

1. **The query encode (~47–60 % of the retrieval budget) produces nothing on turn corpora** because
   captured turns are not embedded. Either (a) embed turn Points at capture time so the dense leg has
   material (the leak is on the *write* path), or (b) skip the encode when the corpus carries no embeddings
   (bounded, cheap) — **and the fix that matters is (a): it is the difference between keyword-only and hybrid
   ranking for the product's answer-bearing evidence.**
2. **The post-retrieval graph chain (~22 %) is genuinely sequential** — 6 independent round-trips here, up
   to 9 on a real graph. Parallelising/batching it overlaps execution (save up to the whole chain, ~12–39 ms
   here) plus RTT (the size of that part depends on the *deployed* RTT, still unmeasured).
3. **Everything else the literature optimises is noise at our size**: the ANN index (p50 4.6 ms, 0 results),
   fusion (0.00 ms), assembly + rendering (0.6 ms), dedup + boost (0.2 ms).

**A cheaper encode (ONNX/OpenVINO for the same weights) attacks #1's cost but not its waste.** The ranking
win is the missing turn embedding; the latency win is not paying to embed a query whose leg can return
nothing.

---

## 8. Receipts, paid calls, and what could not be verified

- **Receipts:** `docs/research/evidence/2026-09-19-m1-read-path-latency.json` (phase table + per-query raw +
  round-trip census; produced by the committed tool), `…-m1-reader-runs.json` (the 3 paid reader calls),
  `…-m1-gold-rank-diagnostic.json` (per-turn ranks for the 5 questions).
- **Instrument:** `tools/profile_read_path.py` (runtime monkeypatches; no product-code change; it produces
  the latency receipt). Requires the `falkordblite` embedded extra to open an embedded graph.
- **Paid calls:** **3** real reader calls (`deepseek-direct` / `deepseek-v4-flash`), ≈ **$0.004** total. No
  other LLM calls were made; the embedding is a local model.
- **Read-only:** the real store `~/.tortoise/tortoise.db` was **copied**, never written; the census ran on
  the copy. Latency/rank runs used in-memory scratch graphs. No product behaviour was changed.

**Could NOT verify / open:**

1. **The deployed FalkorDB network RTT** (LAN/cloud) — the single number that sizes the round-trip lever.
   Measured only loopback (docker 0.32 ms) and embedded (2.6 ms); a cloud store at 5–15 ms would change the
   saving from ~2 ms to 25–75 ms.
2. **Why the historical `e9327a54` rank was 89** while the current measurement is 30 (in cut) — see §5.
3. **The rank of the gold turns when the dense leg is ACTIVE** (i.e. after turn embeddings exist) — by
   construction unmeasurable on a capture-shaped store; this is the A/B the write-path fix needs.
4. **Server-side execution vs RTT split per query** — `GRAPH.PROFILE` output was not collected; the §3 split
   is inferred from the RTT probes and the chain total.
