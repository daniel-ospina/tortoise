---
title: "Capacity and limits — measured, per deployment mode"
type: reference
domain: platform
doc_status: live
created: 2026-10-13
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: tortoise capacity and scaling characteristics
aboutObjects: README.md, website/self-hosted.html, benchmarks/, tortoise/capacity limits
related:
  - issue: 3329
    note: "The public capacity page. No capacity or scaling characteristic was published before this page."
  - issue: 5063
    note: "The root this page belongs to: a written claim must stay bound to the system it describes. Every measured row below is pinned by tests/test_capacity_page.py."
  - issue: 6211
    note: "The measured belief-propagation failure threshold recorded in §7."
---

# Capacity and limits

**Two rules decide what appears on this page.**

1. A number appears only if it was **measured**, and the row names the committed
   artifact or the code symbol it comes from.
2. A dimension with no measurement is listed **by name** as unmeasured, with the
   issue that owns it. It is never estimated, extrapolated, or "typically".

We state this up front because the alternative is worse than silence: a scale
number an evaluator cannot check is a number they will discover to be wrong
during a proof of concept, and it takes the rest of the page with it. Where we
have nothing, the page says nothing but the word *unmeasured*.

This page follows the capacity questions procurement actually asks — write
concurrency, request rate limits, graph size, retrieval latency, storage
footprint, and belief-propagation cost — in that order.

## 1. Pick the deployment mode first

The three modes have materially different ceilings, and a single "Tortoise
scales to X" sentence would be false for at least two of them. Every limit below
is per mode.

| Mode | How it runs | Concurrent writers |
|---|---|---|
| **Embedded** (`TORTOISE_DB_PATH`) | One process, local FalkorDBLite file. **Evaluation only.** | None — single-writer by contract |
| **Self-hosted** (`TORTOISE_DB_URI`, docker compose) | Daemon plus a FalkorDB sidecar (AOF, named volume) | Verified (see §2) |
| **Hosted** (managed) | Shared service, one graph per team, per-plan caps | Per-org caps and per-key rate limits |

Embedded is not a deployment. It is what evals and parity runs execute on, and
its concurrency boundary is the reason. Source: `README.md` §Self-host
configuration; `website/self-hosted.html`.

## 2. Write concurrency

| Dimension | Measured value | Source |
|---|---|---|
| Embedded concurrent writers | **0** — concurrent writers on one embedded file lose data | `README.md`, `website/self-hosted.html`, `tests/test_embedded_concurrency.py` |
| Self-hosted (docker/`bolt://`) concurrent writers | **5 writer processes on one graph, no lost writes** | `tests/test_embedded_concurrency.py` |
| Hosted concurrent writers | Not published | Unmeasured — see §8 |

The self-hosted figure is the coverage of
`tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes`:
five concurrent subprocesses each write ten Points to one live graph, and the
test asserts all fifty land with no duplicates and no losses. It runs in CI's
`test-concurrency-falkor` job against a real FalkorDB sidecar, and skips
visibly (never green) where no live URI is configured.

Read that as a **verified floor, not a ceiling**. Five writers have been proven
not to lose writes; nothing above five has been tested, so the honest statement
is "integration-tested to 5 concurrent writers" and not a supported maximum.

For embedded, the boundary is the opposite one: the single-writer contract is
what routes concurrent writes to the Docker path, and the test file documents
the redislite limitation (five concurrent writers lose data) alongside the
single-writer-plus-many-readers pattern that is verified.

## 3. Request rate limits

| Surface | Limit | Source |
|---|---|---|
| Hosted REST and MCP, per API key | **100 req/min** | `tortoise/hosted_api.py` |
| Hosted `POST /v1/sessions/commit`, per key | **300 req/min**, on its own bucket | `tortoise/hosted_api.py` |
| Hosted, unauthenticated or malformed key | **100 req/min per client IP** | `tortoise/hosted_api.py` |
| Self-hosted daemon | **100 req/min** (`TORTOISE_RATE_LIMIT`) — per API key when one is presented, otherwise per client IP | `tortoise/selfhost.py`, `README.md` |
| Rate-limit bucket store (hosted) | **10,000 buckets** per process (`MAX_BUCKETS`) | `tortoise/hosted_api.py` |

The commit endpoint is deliberately exempt from the general key bucket and gets
its own 300/min bucket, so catch-up commits after offline capture neither consume
nor are consumed by the 100/min general budget. The limiter charges **once per
request** in `RateLimitMiddleware.dispatch`; the unit is called "points/minute"
in its own docstring and in the 429 body, and it is one charge per request.

Every per-IP bucket (the unauthenticated fallback above, plus register / signup /
recovery / claim / sensitive-op / session-login / invite buckets) identifies a
client by its **IPv6 /64 network** (default; knob
`TORTOISE_IP_BUCKET_IPV6_PREFIX`), never by its full 128-bit address — otherwise
one subscriber holding a delegated /64 would rotate addresses within it and mint
a fresh budget on every request (`#5490`). IPv4 and every IPv4-mapped IPv6
spelling key as the plain IPv4 address, so a dual-stack client cannot double its
budget; a malformed address maps to one shared `"anonymous"` bucket. Two clients
inside ONE /64 therefore share that bucket — the deliberate tradeoff of per-/64
identification (RIPE-690 / Better Auth default). Cross-/64 cardinality is
unchanged by this rule: it stays bounded by the 10,000-bucket store cap plus its
single shared overflow bucket, which fails closed.

**Convert requests to tool calls before you size anything.** One MCP tool call
can emit roughly 5–10 SSE requests, so the default 100 requests/min is closer to
**10–20 tool calls/min** per key in practice. That ratio is documented next to
the env var in `README.md` and `tortoise/selfhost.py`.

## 4. Graph size (hosted node cap)

| Plan | `max_graph_nodes` | Source |
|---|---|---|
| anon | **1,000** | `product/pricing.json` |
| free | **10,000** | `product/pricing.json` |
| solo | **25,000** | `product/pricing.json` |
| pro | **100,000** | `product/pricing.json` |
| team | **600,000** | `product/pricing.json` |

This is an enforced cap, not a declaration: the plan's `max_graph_nodes` is read
as the org's `max_points` and a write that would cross it is refused with a 402
(`tortoise/quota.py`, `tortoise/hosted_api.py`).

Two properties of the cap matter more than its value:

- **It counts graph nodes, not episodes.** The count covers Points without the
  `is_episodic` flag plus `Object`, `Subject` and `Event` nodes. Points that
  carry `is_episodic=true` are excluded from it, so captured conversation does
  not consume the plan's node budget — but the discriminator is the **flag**, and
  a missing flag counts as non-episodic, so a legacy or direct write with no flag
  IS charged (fail-closed).
- **It does not count relationships.** A graph whose *edge density* rises grows
  its resident memory while this cap and any other node-metered surface stay
  flat. The measured per-edge sizes are in §6.

Self-hosted and embedded deployments have **no node cap applied** — there the
bound is the host's own FalkorDB sizing.

## 5. Retrieval latency

### 5.1 Pre-registered targets

These are the benchmark harness's acceptance bands — what a run is judged
against — not achieved results. They are fixed by the #316 scoping revision and
locked by `tests/bench/test_bench_core.py`.

| Strategy | Target (isolation p95) | Source |
|---|---|---|
| FTS | **< 50 ms** | `benchmarks/bench_core.py`, `benchmarks/README.md` |
| Vector | **< 100 ms** | `benchmarks/bench_core.py`, `benchmarks/README.md` |
| Hybrid RRF | **< 200 ms** | `benchmarks/bench_core.py`, `benchmarks/README.md` |
| TF-IDF fallback | **< 500 ms** | `benchmarks/bench_core.py`, `benchmarks/README.md` |
| End-to-end (mix-weighted p95) | **≤ 300 ms** | `benchmarks/bench_core.py`, `benchmarks/README.md` |

The harness also runs its collective strategies under a 500 ms cap, with a
5,000 ms elevated column that measures true completion. A run above the 300 ms
end-to-end band is reported in the bands `cap-dominated` (>300 ms) or `tail`
(>500 ms) rather than as a pass.

### 5.2 One measured read path

| Dimension | Measured value | Source |
|---|---|---|
| Ask-lane total, 533-Point capture-shaped corpus, 12 queries, stub reader (no LLM), **embedded lane** | **p50 80 ms / p95 108 ms**; the same 12 queries during heavier host load: **p50 ≈150 ms** | `docs/research/2026-09-19-m1-read-path-latency-profile.md` |

Read the caveats with the number, because they decide whether it applies to you:

- **It is the embedded lane.** Embedded FalkorDBLite has no FTS or HNSW index,
  so the FTS leg degrades and the dense leg runs brute-force. The benchmark
  README says explicitly that numbers taken on embedded **can reverse on
  Docker**; we are not going to publish that comparison as though it were ours.
- **It is one box under variable load.** The profile's own measurement records
  absolute milliseconds moving 2–3× between runs with host load, and states that
  the *shares* are the robust output.
- It is the retrieval path with a deterministic stub reader, so no model call is
  inside it. The LLM leg is measured separately in the same profile.
- 533 Points is a small corpus. It is a shape, not a scale test.

**No corpus-scaling curve is published.** The harness carries scaling arms at
1k / 10k / 100k and the vectors-per-corpus arms, but no run against them is
committed, so quoting a curve here would be quoting something nobody ran.

## 6. Storage footprint per element

| Dimension | Measured value | Source |
|---|---|---|
| Dressed keyword-only Point (`id`, `content`, `pointKind`, `status`, `confidence`, `createdAt`, `is_episodic`) | **≈ 354 B** marginal | `docs/research/2026-09-25-4503-edge-accounting/measurement.md` |
| Bare `:Point` (label and `id` only) | **102–174 B**, and it moves with `n` — fixed per-instance overhead dominates at small graphs | `docs/research/2026-09-25-4503-edge-accounting/measurement.md` |
| Dressed `IMPL` edge (direction, confidence, weight, label, batch_id) | **≈ 210 B** marginal | `docs/research/2026-09-25-4503-edge-accounting/measurement.md` |
| EP-bearing edge (dressed plus the four message slots) | **≈ 259 B** — 0.73× a dressed Point | `docs/research/2026-09-25-4503-edge-accounting/measurement.md` |
| Per-embedding vector (`vecf32`, 384-dim) | **≈ 1.50 KB stored** (the fixed-width payload) + **≈ 0.55 KB HNSW share (derived)** = **≈ 2.05 KB resident** | `docs/architecture/STORAGE-ARCHITECTURE.md` |

The node figures are the output of `tools/edge_census.py`'s isolated per-element
probe at n=5,000, and an independently run probe measured a props-only Point at
**355.6 B** against this probe's 353.9 B — 0.5% apart, on a different container
on a different day. That agreement is why the edge figures from the same probe
are worth publishing at all.

**Memory footprint per deployment tier is not published.** We have per-element
sizes (§6) and a node cap (§4), but no measured total-footprint-per-tier curve,
so sizing a deployment from this page alone is not something we can support.

## 7. Belief propagation and dreaming

Belief propagation (EP) recomputes confidence over a bounded neighbourhood. Its
cost characteristics are bounded by explicit guards rather than by a measured
curve, and one of those guards has a **measured failure threshold**.

| Dimension | Value | Source |
|---|---|---|
| EP affected-claim frontier guard (new **claims** per hop) | **200** (`_EP_GUARD_FRONTIER_CAP`) | `tortoise/ep.py` |
| Operator-set cap on an explicit-k BFS (the ranking's operator input) | **200 operators**, truncated deterministically by id with a stderr warning — and reported in the caller's RESULT as `truncated` / `operators_seen` / `operators_used` since #6211 | `tortoise/analyze.py` |
| EP pass query that the engine aborts on its own query timeout | raised as **`EPSubgraphHopError`** (a `ResponseError` subclass) carrying the hop index (or `None` for a pass-level query), phase and frontier size (#6211) | `tortoise/ep.py` |
| EP degenerate-run guard | fires when the pass has collected **≥ 50%** of the graph's claims, on graphs of **≥ 500** claims | `tortoise/ep.py` |
| Dreaming per-pass window budget (stale-first) | **200** operators (`TORTOISE_DREAM_WINDOW_BUDGET`) | `tortoise/dream.py` |
| Dreaming full-mode operator cap (`dream_all`) | **200,000** (`max_total_operators`) | `tortoise/dream.py` |
| Dreaming window that raised a store timeout on a 5,001-claim synthetic graph | **5,000-claim window** — windows **≤ 2,000** completed | `tortoise/dream.py`, `graph-scripts/dream-scale-bench.py` |

### The measured failure threshold

On a **3,353-Point** graph with **2,073 dirty** claims, a two-hop EP
affected-claim BFS **times out after 13.4 s** once the operator frontier reaches
around 200, and a pass that reported `converged: true` **still left `ep_dirty`
set**. Twelve of fourteen per-anchor passes raised the timeout. That ~200 is the
hop query's own growth (the two-hop pattern traverses operators), and
`tortoise/analyze.py` separately caps the collected operator set at 200 — it is
**not** `_EP_GUARD_FRONTIER_CAP`, which bounds **new claims per hop**. The full
measurement, the traces, and the three distinct failure modes are in **#6211**,
which is open.

This is why the frontier guard exists, and why it is a *bound* rather than an
optimisation: the guard trades an exact closure for staying inside the store's
query budget. The graph instruction to "add your findings as you go" is what
surfaced it — the writes succeeded and the calibration could not run.

**What #6211 changed (the reporting half).** Two of the three failure modes are
now visible to a programmatic caller instead of to a human reading stderr:

- The 200-operator cap travels in `compute_confidence`'s **result**
  (`truncated`, `operators_seen`, `operators_used`). Before it, a capped pass
  and a full-graph pass both returned `{iterations, converged, confidences}` with
  `converged: true` — a published ranking with 0.0067 margins came from a capped
  pass and nothing in the result said so. The cap itself is deliberately NOT
  raised: the truncated topology is the honest problem, not the bound.
- A query of the pass that the engine aborts on its own query timeout raises
  `EPSubgraphHopError` (still a `redis.exceptions.ResponseError`, so existing
  handlers are unaffected), naming the hop index, the phase and the frontier
  size it was scoped to. Typed is the WHOLE pass, not only the two-hop
  expansion: the seed phase (`seed_lookup` / `seed_operators` /
  `seed_direct_edge` / `seed_neighbourhood`, hop 0), the loop's hops
  (`operator_bridge` / `direct_edge`), and the pass-level queries that walk the
  finished affected set (`factor_extraction`, `draft_filter`, `cache_load`,
  `cache_flush`, `evidence_prewrite`, `claim_count`, `ep_epoch`) — those report
  `hop: None`, because there is no hop to name (`draft_filter` is the one phase
  that appears in BOTH shapes: hop-scoped when the loop asks it, `hop: None` on
  the final fail-safe strip). The cache phases are the
  WIDEST statements a pass makes (16 load sweeps over the whole affected set,
  plus the write-back) — a different query from factor extraction's single
  largest one, and untyped it would keep the issue's symptom
  reachable on the largest query of the pass; a `cache_flush` timeout is
  reported as "this run's posteriors were NOT persisted", and `claim_count` /
  `ep_epoch` say plainly that narrowing seeds or `max_hops` cannot help,
  because those reads are not hop-scoped. An
  earlier revision typed only the expansions, which left the issue's own
  symptom reproducible on the largest query of the pass.
  Measured on a 3,353-claim / 160,000-edge graph with the engine's 1,000 ms
  timeout posture: the bridge query aborted after **1.00 s** over a
  **188–257-claim** frontier, and the pass failed after 1.1–2.9 s.
- **Still open — the clearing path.** `ep_dirty` is cleared only by the dreaming
  adapters' `_sweep_dirty_roots` (epoch-guarded, convergence-gated, and scoped to
  the pass's `affected` set). The targeted fast path
  (`compute_confidence(factors=…)` / `compute_confidence(anchors=…)`) never runs
  it: measured, a converged targeted pass over three freshly-written claims left
  all three `ep_dirty`, while a no-arg pass on the same graph cleared them. So a
  writer that follows the documented "compute confidence for my claims" call has
  **no** API that clears its own flags — #6211's second failure mode, still open.

### The full-mode cap is a refusal, not a silent truncation

`dream_all`'s 200,000-operator cap sits **below** the recorded 5k–500k operator
production range, so a default-budget full pass cannot complete at the top of
that range. Since #1338 the truncation is no longer silent: `converged_all`
returns `False`, the graph-wide trivial-stamp scan and the caller's dirty-root
sweep are both skipped, and a WARNING is emitted. The cap is deliberately **not**
raised — for a direct SDK or self-host call it is the only bound on a whole-graph
EP, and weakening it would trade a silent-truncation bug for an unbounded one.

`TORTOISE_DREAM_WINDOW_BUDGET` is the tunable knob for a window pass, and it is
read on every resolution, so a running deployment can be retuned without a
restart. Raising it is a per-pass reliability trade, not a free win — see the
measured window figures above.

## 8. Explicitly unmeasured

Every dimension procurement asks about that we cannot yet answer with a
measurement. Naming them is the point of the section: the honest answer is
"unmeasured", and the tracker is who closes it.

| Dimension | Why it is unmeasured | Tracker |
|---|---|---|
| Corpus-scaling latency curves (1k / 10k / 100k) | Harness and arms are committed; no run is | `benchmarks/run_report.py` |
| Query throughput (queries/sec) under concurrency | Latency was measured; throughput was not | — |
| Write throughput above 5 concurrent writers | The verified coverage is 5; above it is untested | `tests/test_embedded_concurrency.py` |
| EP cost per operator, and belief divergence from exact at scale | Only the frontier guards and one failure threshold are measured | `#6211` |
| Memory footprint per deployment tier | Per-element sizes exist; a per-tier curve does not | — |
| Hosted multi-tenant capacity (graphs per instance, isolation cost) | Not measured | — |
| Graph-size ceiling on self-hosted and embedded | No cap is applied; the bound is host sizing | — |
| Exact HNSW-only RAM per embedding on the FalkorDB path (index-level attribution) | §6's ≈ 2.05 KB resident is a **derived** blend (payload + index share); the HNSW-only split is not measured | `docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md` |
| Network latency between client and daemon | Every number above is measured at or near the store | — |

## 9. Why these numbers stay true

This page is the kind of claim that rots silently: nobody re-runs a benchmark
when a default changes, and a stale capacity number is discovered by a customer
rather than by us. So the page is not held up by discipline alone.

`tests/test_capacity_page.py` fails when:

- the page is no longer linked from `README.md` or `website/self-hosted.html`;
- a measured row names a source path that does not exist in the tree;
- a dimension this page publishes as measured disappears from it;
- the published write-concurrency figure stops matching the actual coverage of
  `tests/test_embedded_concurrency.py` — the check that caught this page's own
  predecessor claim, which said "2 concurrent writers" while the integration test
  had covered 5 for months.

**To add a number here, commit the measurement first.** The row's Source cell
must name a path that exists, so the artifact is the entry condition, not an
afterthought. For a live value (a default, a cap, a limit) prefer naming the
symbol — `tortoise/ep.py::_EP_GUARD_FRONTIER_CAP` rather than a line number,
which rots on the next edit above it.
