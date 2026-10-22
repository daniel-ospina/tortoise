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

### 5.2 Measured read paths

| Dimension | Measured value | Source |
|---|---|---|
| Ask-lane total, 533-Point capture-shaped corpus, 12 queries, stub reader (no LLM), **embedded lane** | **p50 80 ms / p95 108 ms**; the same 12 queries during heavier host load: **p50 ≈150 ms** | `docs/research/2026-09-19-m1-read-path-latency-profile.md` |
| End-to-end retrieval p95, E2E-8 gate, post-swap re-run (2026-08-21) — **contended benchmark laptop** under full-suite contention, not deployment-class; MiniLM control on the same box | **p95 345–460 ms across runs** (MiniLM control **418–464 ms** under identical conditions) | `docs/adr/ADR-009-embedder-selection.md` |

Read the caveats with the number, because they decide whether it applies to you:

- **It is the embedded lane.** Embedded FalkorDBLite has no FTS or HNSW index,
  so the FTS leg degrades and the dense leg runs brute-force. The benchmark
  README says explicitly that numbers taken on embedded **can reverse on
  Docker**; we are not going to publish that comparison as though it were ours.
- **It is one box under variable load.** The profile's own measurement records
  absolute milliseconds moving 2–3× between runs with host load, and states that
  the *shares* are the robust output.
- **The E2E-8 row is environment-bound, and it reads as a comparison.** Its
  **345–460 ms p95 across runs** was measured on the contended benchmark laptop
  under full-suite contention, beside a MiniLM control arm at **418–464 ms on the
  same box**. The ADR labels the 460 ms reading "inconclusive" *under full-suite
  contention* — the environment is what it declines to generalise from, not the
  comparison, which it reads in bge-small's favour.
  `docs/adr/ADR-009-embedder-selection.md` records the 2026-08-21 post-swap
  re-run and reads the split as the shipped bge-small model being 15–25% faster
  than the predecessor under identical conditions — that percentage is its
  345–358 ms bge reading against the 418–464 ms control, not the wide band above. The ≤ 300 ms band itself was
  never re-measured on a deployment-class box: #1656 reframed T15 from a latency
  re-validation into a throughput test (1,242 req/s at 15 concurrent, 0 failures)
  and recorded the launch latency band as superseded by an owner decision. §5.1's
  band is what a deployment-class box would be judged against, and §8 lists it as
  unmeasured.
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
| EP id-anchored query planning | the PLAN of every id-set statement the pass emits is asserted to be a `Node By Index Scan` on the frontier, not a label scan of the graph — the evidence pre-write's journal read included since #9304 (it was label-free and `All Node Scan`; it is now a per-label sweep, deduped on the node key so a multi-labelled claim is still journaled once). Where the shipped plan was a scan, a `WITH *` anchor barrier (`_EP_ANCHOR_BARRIER`) fixes it — or, where no barrier can apply, a label is named so a real index anchors it — the invariant is the plan, not a syntactic rule, because which sites need which fix was decided per site by measurement (#6211, below) | `tortoise/ep.py`, `tortoise/analyze.py` |
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
measurement, the traces, and the three distinct failure modes are in **#6211**.

**The hop's cost tracked the GRAPH, not the frontier (#6211, measured).** The
shape above looked frontier-proportional, but the frontier was never the anchor
of the queries that regressed: each id-set hop that ran over an expanding
traversal WITHOUT an anchor was **planned as a label scan of the traversal's
other endpoint**, with `n.id IN $ids` applied per record afterwards. (Three
`ep.py` id-set queries **that traverse** were already `Node By Index Scan`
without a barrier —
they are named below.) Measured with `GRAPH.PROFILE` on a 23,353-node /
160,000-edge fixture (falkordb-server 4.20.6), two-hop bridge at a 30-id
frontier:

| plan | records walked | wall |
|---|---|---|
| `Node By Label Scan (op:Point)` — shipped | 23,353 → 160,000 → **1,280,000** | 1.15 s |
| `Node By Index Scan (n:Point)` — `WITH *` barrier | 30 → **1,445** | 0.009 s |

That is why a **single-anchor** pass failed too: its floor is the whole graph's
edge set plus one traversal per edge (`frontier=1` still cost 0.78 s on the
fixture above), and it grows with every point the map gains. The barrier is now
at **eleven** sites (seven in `ep.py`, four in `analyze.py`), and **whether it
does the work differs per site** — that is why
the invariant is the plan assertion, not a syntactic rule. Barriered and
load-bearing (removing the barrier reddens the plan test): the seed
neighbourhood, the plain-seed direct-edge expansion, the bridge hop, Batch 1,
and all four of the operator selector's **barriered** legs in `analyze.py` —
the viability
query (`242.5 ms` -> `12.6 ms`) plus the incoming, backward and expansion legs
(the last three measured unanchored on the same fixture,
`Node By Label Scan (op:Point)` / `All Node Scan (a)` / `All Node Scan (p)`;
one `_bfs_select_operators` call took **1,507 ms** before them and **98 ms** now
— best of 3, same 168 operators returned). Barriered but NOT load-bearing,
kept as the engine-6.0.0 predicate-drop guard (#6976): the cache load's
back-message sweep, the BFS undirected direct-edge sweep and Batch 3, whose two
`MATCH` / undirected forms anchor on their own.

Three sites in `ep.py` traverse and were already `Node By Index Scan` without a
barrier on the pre-#9282 tree, so they carry none: `_load_cache`'s message sweep,
`seed_operators` and `_affected_factors` Batch 2. Those three plus the three
reads that do not traverse at all are the `ep.py` pass's **19 distinct
unbarriered id-set statements on a `:Point` seed set** (20 / 21 / 22 when the
seed lookup reaches `:Subject` / `:Object` / `:Event`) — the traversing sites at
8 + 1 + 1 (the message sweep is 2 relations × 4 epistemic labels) and the
single-node probes at 4 + 1 + 4: the cache's per-label node-params load (the
`ep_alpha` / `ep_beta` probe), the seed lookup and the draft filter. The seed
lookup is the one term that moves: it probes labels in `EPISTEMIC_LABELS` order
and breaks only when every seed has RESOLVED (`ep.py:1203-1219`), so it emits
one statement per label up to the last epistemic label a seed uses — all four
when a seed resolves under none.

A further **4 distinct statements** (#9304) are emitted on a run whose journal is
wired (`_emit`, i.e. an event log) and which carries evidence priors: the
evidence pre-write's journal read, one per epistemic label. That leg's fix is
NOT a `WITH *` barrier — a barrier helps only a traversal that has an anchored
operand, and the shipped form here was a LABEL-FREE disjunction
(`MATCH (n) WHERE (n:Point OR n:Subject OR n:Object OR n:Event) AND
n.id IN $ids`) with no label for any per-`(label, property)` index to anchor on:
measured `All Node Scan | (n)` on the 23k-node / 160k-edge fixture, i.e. the id
predicate applied per record over the whole node set, so its cost tracked the
GRAPH. #9304 replaced it with the same `UNWIND $ids AS cid` + label-and-id
pattern the pre-write immediately above it uses (measured
`Node By Index Scan | (n:{label})`) and moved #7853's "a `:Point:Object` node must
be journaled exactly ONCE" off the label-free form and onto a **node-keyed
dedupe** of the swept rows, which is what a per-label sweep needs to satisfy it.
So the census has no carve-out left: **every one of these statements is
index-anchored.**

Three of the operator selector's OWN id-set statements in `analyze.py` are NOT
counted here, because this census is `ep.py`'s: the operator selector's
operator→target leg (emitted by `_bfs_select_operators`, never by
`TortoiseEP.run()`), its per-label draft filter and its forward direct-edge
sweep. All three carry no barrier and all three are measured
`Node By Index Scan` — the operator→target leg on the operator's own label,
`analyze.py:666-671` — while the selector's four BARRIERED legs are the
viability, incoming, backward and expansion queries, counted above. The
traversal `sdk.py` mirrors that `compute_confidence` also runs
(`_window_closure`) are a separate, still-open leg of the same call (#9299) —
`sdk.py`'s, not this census's.

All eleven barriered sites, all six unbarriered ones (three traversing, three
single-node probes) and the evidence pre-write's 4-statement journal sweep are
in the pinned set: `tests/test_6211_ep_anchor.py`
captures the statements the pass emits and asserts the PLAN of each (`Node By
Index Scan`, and no `Node By Label Scan`; the selector test additionally rejects
`All Node Scan`), never their text; each is reached by the fixture
(`_load_cache`'s sweeps and Batch 2 directly, `seed_operators` through an
operator seed — the 300 operators hanging off the hub claim). The journal sweep
is reached by its own `run()` capture on a 5-node fixture (a wired `emit` stub
plus evidence priors, the baseline'd and double-labelled ids it has to get
right), because the pass's shared capture never calls `run()`. On this file's own
fixture (`_build`: ~8.6k nodes, ~8k edges — NOT the 23k/160k PROFILE fixture
above) that assertion covers the pass's **49 distinct** id-set
statements (95 emissions) and the operator selector's **12 distinct** (37
emissions across the two seeds the assertion uses — all seven of the selector's
id-set legs: four barriered, three not) — zero unanchored in either.
Against the pre-#9282 tree (`8c3823b92^`) the file's six behavioural tests run `4
failed, 2 passed` — the emitted-statement plan test, the hop's record bound, the
OR split's statement counts and the operator selector's four legs fail, while
the fixture guard and the OR-split row-semantics guard pass, because they pin
properties that tree already had. (The manifest-row test in that file — the
seventh then, the eighth since #9304 added the journal-read pin — pins this
manifest row, which #9282 itself added: on that tree there is no row to pin, so
it fails there for a different reason.) #9282 has since landed on `origin/main`,
so `origin/main` now IS the anchored tree and every baseline in this section names
the tree before it. The selector's expansion leg is individually discriminating:
removing its barrier alone reddens the selector test.

The two `OR`-shaped sweeps (`(a.id IN $ids OR b.id IN $ids)`, which can never be
an index anchor) are split into one anchored sweep per endpoint: the
target-anchored sweep takes every edge whose target is affected, the
source-anchored sweep takes the rest (`NOT b.id IN $ids`), so each edge is
admitted once. How each half keeps that "once" is site-specific, because the
two sweeps fill different containers. `_affected_factors` Batch 3 appends rows
to a list, so it de-duplicates on the EDGE's internal id (`id(r)`) — not on the
row, because a row-keyed dedupe would collapse two byte-identical parallel
direct edges into one factor, and not on a node id, because a multi-labelled
endpoint (`:Point:Object`) is matched by one label sweep per label it carries
and comes back once per label. `_load_cache`'s back-message sweep carries no
such key: its rows overwrite one `(src, tgt, rel)` entry of `_back_cache`, so a
multi-labelled endpoint re-reads the same edge once per label and two
byte-identical parallel edges (unsupported there — last writer wins) share one
slot. Those are the pre-existing cache semantics, unchanged by the split.
On the 23k-node / 160k-edge fixture above, engine at the issue's 1,000 ms query
bound:
`compute_confidence(anchors=[one claim], max_hops=2)` aborted at hop 1
(operator_bridge) over a **29-claim** frontier before the change and now
completes; the hop at a 1,000-id frontier went from **5.55 s** to **0.32 s**,
and `analyze`'s operator-viability query from **242.5 ms** (`All Node Scan`) to
**12.6 ms** (`Node By Index Scan`) — identical rows in every case. The affected
sets are identical before and after, and so are the extracted factor lists as
SETS (the OR split appends them in a different order — source-anchored rows
before target-anchored ones — so a list comparison differs in sequence while
the posteriors agree to ~1e-12, the summation order; `run` shuffles the factor
list per iteration anyway).

The split's own cost, measured rather than assumed, because it emits MORE
statements per pass — and it is a real cost, paid on the interactive path.
Instrumenting the graph handle and counting every query a `TortoiseEP.run()`
issues on the 40-claim interactive zone that
`tests/test_ep_local_395.py::test_ac5_interactive_latency_zone` builds, this
branch emits **11 more statements per pass** than the pre-#9282 tree
(`8c3823b92^`) (**+22** for the
two passes one `compute_confidence()` runs). Only the DELTA is quoted here: the
absolute totals move with how much of the call the recorder sees (a
`TortoiseEP.g`-level recorder and a graph-handle-level one disagree by the
sweeps that never reach the pass), and the delta is identical either way.
Nothing replaces anything — the two splits are the entire delta, and an added
`WITH *` barrier emits no statement of its own:

| site | pre-#9282 (`8c3823b92^`) | this branch |
|---|---|---|
| `_affected_factors` Batch 3 | **1** (one unlabelled `MATCH (a)-[r:IMPL\|NAND]->(b) ... (a.id IN $ids OR b.id IN $ids)`) | **8** (2 anchors x 4 epistemic labels) |
| `_load_cache` back-message sweep | **4** (target-labelled, `b.id IN $ids` in the same predicate) | **8** (2 anchors x 4 epistemic labels) |

The wall cost of those 11 statements is **not** inside the noise. Three
independent interleaved blocks of 45 paired runs each (order alternated both
ways, one child process per run) gave **1.024 s -> 1.108 s, +8 %** — per-block
medians 1.034/1.126, 1.011/1.108, 1.007/1.099 (+8.9 %, +9.6 %, +9.2 %) — with
the branch slower in **114 of 135** paired runs (37/45, 40/45, 37/45; sign test
p < 1e-4). A 6-run sequence had suggested the cost was inside the range; the
interleaved study is what settles it, and it settles it the other way. The AC5
guard itself is flaky on this host for BOTH trees — it trips its `< 1.2 s`
bound on the pre-#9282 tree too, and more often here (#9015 tracks that flake), so
per-run tallies from one host are not worth quoting as a characterisation.

**That cost is accepted, not denied.** The alternative on the other side is the
plan this change exists to remove: a label scan of the traversal's endpoint,
graph-proportional, which aborted the pass outright at a few thousand points.
Narrowing the fan-out back is not free either — sweeping only the labels
actually present (`live.epistemic_labels_of`) would also make the endpoint
duplication disappear, and with it the only observable failure that the
edge-keyed dedupe exists to catch (a multi-labelled `:Point:Object` endpoint
swept once per label is what produces the duplicate row).
Cutting the statement count while keeping that observability is a follow-up, not
a one-line change.

What remains open after it: the pass **completes slowly** — the same one-anchor
pass took **89 s** on the fixture, of which ~43 s is a per-message graph read
(`_read_node`, 45,087 `MATCH (n:Point {id:$id})` calls in one pass) rather than a
cache hit. That is not a hop-budget problem and is not fixed here. (The
`ep_dirty` clearing path, previously listed here as open, is now closed — see
below.)

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
  WIDEST statements a pass makes (20 load sweeps over the whole affected set,
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
- **The clearing path — closed for the targeted call.** The only code that
  retires a **converged pass's** recomputed roots is `_sweep_dirty_roots`, which
  was reachable only from the dreaming adapters (epoch-guarded,
  convergence-gated, and scoped to the pass's `affected` set), so the targeted
  fast path (`compute_confidence(factors=…)` / `compute_confidence(anchors=…)`)
  never cleared a writer's own flags: measured, a converged targeted pass over
  three freshly-written claims left all three `ep_dirty`, while a no-arg pass on
  the same graph cleared them. (The flag's two other writers are lifecycle
  events, not this rule: `_mark_dirty` clears it for terminal points as it
  re-marks them, and `_retry_cap_verdict` clears it when a root hits the retry
  cap — where a snapshot of **`0`** carries NO ordering claim, because `0` means
  "no `:EpMeta`" (a rebuilt graph, whose replayed stamps sit ahead of the reset
  counter — `#5166`) and `ep_dirty_at <= 0` matches no stamp at all: the cap
  **retires and logs** there rather than retaining silently, since bounding
  retries is the cap's whole job and an unbounded, silent one is inert (`#9362`
  review).) Both targeted branches now retire the flags they recomputed,
  through `_clear_targeted_pass_dirty`.
  The gates are NOT simply "the Dreamer's". **Two are shared** — `converged`,
  and the `#1163` stale-run guard — and **three are this path's own and
  stricter**: a pass that was **truncated** clears nothing (its `affected` set
  is incomplete by construction, which is why the reporting half above was this
  fix's prerequisite), a pass that **ran no factor** (`iterations == 0`, the
  `#3139` no-op) clears nothing (the Dreamer gets the same protection by
  *raising* from `_guard_dream_progress`; this path refuses the clear instead of
  failing the call), and a missing epoch snapshot refuses — the Dreamer cannot
  reach that state, because its snapshot is always an int (`0` when the epoch
  read failed, which matches no stamp and therefore clears nothing). The
  measurement is in `tests/test_ep_dirty_persist.py` (`#6211` section): the
  primary test is red before the change and green after, each refusing gate has
  its own fail-closed arm, and the request-scoped (empty mirror), best-effort
  failure and no-arg-untouched shapes are pinned too.
  The clear is **best-effort**: it runs after the posteriors, the
  `n.confidence` write-back and the `ConfidenceChanged` journal entries have
  landed, so a failed sweep keeps the flags set (retained for retry) and is
  logged rather than turning a committed pass into an error.
  **Known residual, not fixed here:** the no-arg branch still clears the dream's
  `affected` roots when that dream's operator selection was capped (measured: a
  260-reachable-operator graph returns `truncated: true` and the flags go
  clean), because the dream adapters gate on convergence, the flush guard and
  `_guard_dream_progress` only. That is a different call path with a different
  retry lifecycle, and it is tracked on its own:
  `daniel-ospina/tortoise#9363`. (The sweep's own mirror-vs-graph ordering is
  `daniel-ospina/tortoise#9362`: it was write-FIRST as of that change, so the
  best-effort handler above restores nothing on any reachable failure — it is
  kept as a defensive identity, not as a workaround.)

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
| Query throughput per tenant on a shared hosted instance | The one figure in the tree (1,242 req/s at 15 concurrent, 0 failures) comes from an in-process embedded-SDK load test over a 6-point corpus with no HTTP layer, so it is not a hosted-instance capacity number; per-tenant throughput on a shared instance is unmeasured | — |
| Write throughput above 5 concurrent writers | The verified coverage is 5; above it is untested | `tests/test_embedded_concurrency.py` |
| EP cost per operator, and belief divergence from exact at scale | Only the frontier guards and one failure threshold are measured | `#6211` |
| Memory footprint per deployment tier | Per-element sizes exist; a per-tier curve does not | — |
| Hosted multi-tenant capacity (graphs per instance, isolation cost) | Not measured | — |
| Graph-size ceiling on self-hosted and embedded | No cap is applied; the bound is host sizing | — |
| Exact HNSW-only RAM per embedding on the FalkorDB path (index-level attribution) | §6's ≈ 2.05 KB resident is a **derived** blend (payload + index share); the HNSW-only split is not measured | `docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md` |
| Deployment-class end-to-end p95 (the ≤ 300 ms band, E2E-8 on a benchmark box) | Only the contended-laptop run exists; `#1656` was reframed into a throughput test and closed without it, so no open issue tracks this | — |
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
