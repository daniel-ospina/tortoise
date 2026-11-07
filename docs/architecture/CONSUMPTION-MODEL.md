---
title: "The consumption model — every door, two dimensions, no invented numbers (#4051)"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-02
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: CONSUMPTION-MODEL.md, product/pricing.json, tortoise/metering.py, tortoise/cost_dimensions.py
---

# The consumption model — every door, two dimensions, no invented numbers

**Status:** design. **This document sets the SHAPE of what a customer consumes. It sets no number, no price, no
cap and no code on any request path.** Every figure below is either a *measurement already landed* or
`TBD-BY-MEASUREMENT(...)`.

**Authority.** Owner ruling on `#4051`, 2026-11-02 (verbatim, quoted in full on the issue):

> *"the quota should be applied for anything a customer uses (and remember we're planning to move from nodes to
> FalkorDB RAM, postgres bytes, and extractor usage. I don't know exactly if we need all 3 or how that falls for
> customer tier caps and overage. But we wanted to get the new storage architecture deployed and the new extractor
> and MCP and then I would measure on myself and on private-beta users … Need to finish the P0/P1 before we can get
> private-beta customers invited)."*

That ruling settles **one** thing and refuses **two**: (a) *every* door a customer consumes through is metered
against the same allowance; (b) **which** dimensions, and their tier/overage mapping, is **not** settled by a lane;
(c) the numbers are the owner's, from measurement, after the storage + extractor + MCP deployments.

Read alongside `docs/ops/cost-dimensions.md` (the coverage matrix — *is dimension X measured?*) and
`docs/ops/extraction-cost-metrics.md` (the extraction metric set). This document answers the different question:
**which of the measured dimensions is billable, and what does the customer see at the edge of an allowance.**

---

## 1 · What already exists, by symbol

*By symbol, not by pinned `file:line` — deliberately.* A line number is a citation that rots the moment anything
above it is edited, and this table is meant to be read months from now; each symbol resolves in one
`rg -n '<symbol>' tortoise/`, and `python tools/cost_coverage_audit.py matrix` prints the live, checked list.

The premise that "nothing is metered" is false, and acting on it would build a second substrate beside one that
already exists. **Six** of the ten rows below write **per-org** into one durable ledger, in exactly ONE of two
substrates selected by `metering._supabase_mode()` — `:MeteringRecord` in the graph registry, or
`supabase.metering_records` post-`#669` (the flip DELETES the registry copy; neither is a mirror of the other),
keyed `(org_id, period_start)` — `tortoise/metering.py`;
**two** are per-org on **Prometheus** label sets rather than that ledger; and **two Postgres reads are SQL
functions with no per-org *byte* figure at all** (one is exact per-org *rows*). The registry of what each dimension
is, its unit, and its declared holes is `tortoise/cost_dimensions.py` (#5045), queryable with
`python tools/cost_coverage_audit.py matrix`.

| dimension | writer | reader | blocks a request? |
|---|---|---|---|
| `write_ops` (+ `nodes_written`) | `metering.record_write_ops` | `get_current_usage` / `get_write_op_yield` | **no** — alerts at 80 % / 100 % only |
| **capture extraction — tokens in/out + provider USD + calls** | `metering.record_capture_usage` | `metering.get_capture_usage` — **landed by this change**; before it, none | no |
| ask LLM — tokens in/out + USD | `metering.record_ask_usage` | `get_ask_usage` | no |
| embedding encode — texts / chars / wall-ms | `metering.record_embedding_usage` | `get_embedding_usage` | no |
| retrieval — queries / legs / wall-ms | `metering.record_retrieval_usage` | `get_retrieval_usage` | no |
| **graph storage — MB (gauge)** | `metering.record_graph_storage_reading` ← `graph_storage.measure_graph_storage` | `get_graph_storage_reading` | no |
| hosted compute — wall/CPU seconds, requests | `monitoring.record_compute` (Prometheus) | `compute_by_org` | no |
| egress — response bytes | `monitoring.record_egress` (Prometheus) | `egress_bytes_by_org` | no |
| Postgres **rows**, exact per-org | — | `analytics_events_rows_by_org()` | no |
| Postgres **bytes** | — | `postgres_storage_footprint()` — **database grain only** | no |

**What is genuinely missing is not the meter — it is the reader and the gate.**

1. **The extractor's token counters HAD no reader** (fixed by this change, §6 S1). `get_current_usage` reads
   `write_ops` alone, so *"tokens per team per month"* was written and unreadable from code — the residual
   `EXTRACTOR-V4-ARCHITECTURE.md` §0 records, and the one gap that made the owner's own measurement pass impossible
   to run from code rather than by hand-querying Cypher.
2. **There is no byte gate.** `max_storage_bytes` — the allowance key `STORAGE-ARCHITECTURE.md` §0 specifies —
   appears in **no** module on `main`; the meter is landed, the gate is not, and configuring an allowance before
   the meter is wired into the gate would refuse every points-gated write. Wiring is a prerequisite, not a defect.
3. **The storage meter is blind to the vector index.** `GRAPH.MEMORY USAGE` excludes the HNSW `vectorset` module
   (measured 8.5×–21.7× under-report on the index share, ≈2× on the total — `graph_storage.py`
   `VECTOR_INDEX_EXCLUDED`). It is a **sampling estimate**, it reports a range, and it is **not a cap input for a
   vector-bearing tenant** until that is closed.
4. **The MCP door is not attributable where compute is measured.** `hosted_compute` and `egress` read
   `scope['state']` at the ASGI layer; MCP resolves its org in a ContextVar that layer cannot see, so MCP traffic
   lands on the `""` child. A door that is not attributable is not metered, whatever the ledger says.

**What the docs already promise** (these are recorded decisions, and §10 tests this document against them): storage
is billed in **MB/GB**; extraction usage is billed in **tokens**; overage is **purchased with prepaid credits**;
tiers price **features**; prices are **calibrated after beta** from measured consumption.

---

## 2 · The dimension decision

The owner named three candidates and asked whether all three are needed. The evidence says **two billable
dimensions, served by three measurement substrates** — and the reason is not economy of effort, it is that one of
the three candidates is not measurable as a per-tenant figure at all.

| DIMENSION | cost-driver? | controllable? | measurable-today? | meter-as-cap | meter-as-overage | verdict |
|---|---|---|---|---|---|---|
| **FalkorDB RAM** | **yes** — resident memory on a shared instance we fund; `pricing.json` `cost_basis.falkordb_per_gb` = 73.0 and `docs/research/2026-08-08-event-log-subscriptions.md` records FalkorDB Cloud billed by instance memory | **yes, indirectly** — it moves with what the tenant keeps, not with how often they call | **partially** — `graph_storage_mb` is per-org and live, but a *sampling estimate* blind to the vector index, on a **shared** instance | **no** | **no, not as its own meter** | **KEEP as the substrate of the STORAGE dimension** — its MB figure *is* the storage unit, so it is not "not metered"; it is metered *under the storage name* |
| **Postgres bytes** | **yes** — durable storage that persists and grows (the record is the system of record, `STORAGE-ARCHITECTURE.md` §2.0) | **yes** — writes and retention | **no, per tenant** — `postgres_storage` is declared **database grain**; a per-org byte split would be an **allocation wearing a measurement's name** (`cost_dimensions.py`, `#5045`). Rows *are* exact per-org | **no** | **no** | **FOLD into the STORAGE dimension** — same unit, second substrate; the per-tenant split is `TBD-BY-MEASUREMENT` before it can carry a billable number |
| **Extractor usage** | **yes** — the only genuinely variable marginal cost: a provider bills us per token | **yes** — strongly: capture volume, batching, escalation, the salience gate all move it | **yes** — `capture_tokens_in/out` + `capture_calls` + `capture_cost_usd`, written per capture since `#5045`, plus per-stage detail in `extraction-cost-metrics.md` | **no** | **yes** | **KEEP as the second billable dimension** (tokens) |
| *write_ops* (today's billed unit) | **yes**, but it is a **proxy**: 1 API call, while a capture issues a measured **median ~441 FalkorDB operations** (`metering.py` `#3359`) | yes | yes | **no** | **yes** (today's `$5 / 10k`) | **KEEP during the transition**, and **RETIRE** as the *only* unit once §2.1's two are live — it cannot express LLM work at all |
| *embedding / retrieval / compute / egress* | **yes**, all four | **yes** | yes | no | no | **METER ONLY** — internal cost basis, never a customer-facing meter (they are the cost under `reads_free: true` and under our own serving) |

### 2.1 Why "all three" is the wrong question, and two is the smallest set that cannot be gamed

**FalkorDB RAM and Postgres bytes are not two dimensions — they are two substrates of one unit: bytes.** The
2026-09-26 ruling already fixed that unit (*"migrate away from counting nodes and start counting in mb/gb for
storage"*). Where those bytes physically sit is an **architecture** question that `#10000`-family owns and this
document does not touch; **the customer's storage line is one number, in bytes, over both substrates.** Two
storage meters would charge a tenant twice for the same durable fact and would make a migration
(graph → record, `#7879`) *change the bill* for a customer who changed nothing.

**RAM as its own meter is the one thing the comparables refuse to do.** Every product that meters memory —
Qdrant Cloud (vCPU / RAM / disk), Redis / FalkorDB Cloud (instance memory) — meters **provisioned capacity of a
dedicated instance**. On consumption-based serverless storage the field meters **bytes**: Pinecone GB-month,
Turbopuffer logical bytes stored, Weaviate stored vector dimensions, Supabase disk beyond included. We sell a
**shared multi-tenant instance** (`docs/data-safety.md`, `docs/infra-runbook.md`), so a tenant's "RAM" is a share of
an allocator, not an allocation anyone can read — the identical ground on which `#5045` refuses a per-org Postgres
byte split. ⇒ **RAM stays a cost-basis input that sets the storage allowance's shape; it is not a second meter.**

**Two meters is the smallest set that closes every door.** The field's design guidance converges on *"launch the
first plan with one primary billable metric"* and on units that are *measurable, predictable, value-aligned and hard
to game*; and it warns that counting *calls* under-prices calls of very different cost — which is exactly our
measured 440× spread (`#3359`). The two dimensions that pass all four tests are:

- **STORAGE (bytes)** — the *stock*. It is what a customer accumulates, it is the line they already understand
  (GB), and it is the one axis that cannot be gamed by making more small calls. Substrates: FalkorDB MB (live),
  Postgres bytes (`TBD-BY-MEASUREMENT`), journal bytes (`measured`, `#5612` — not yet a billable input).
- **EXTRACTION (tokens)** — the *flow*. It is the only dimension where our marginal cost is genuinely variable and
  genuinely the customer's doing, and it is the one the field prices per token universally.

Everything else stays **measured but unbilled**: it is how we check a price is sane, not how we collect one.

---

## 3 · The door inventory — every door a customer consumes through

The ruling's real content is *"a door that is not metered makes the number shown to a customer false"*. So each
door must move **at least one** of the two meters. Reading this table is how a future lane checks a new surface
without re-deriving the model.

| door | what it moves | metered today? |
|---|---|---|
| `POST /v1/sessions` (capture) | **extraction tokens**; graph bytes; record bytes | tokens ✓ · bytes ✓ (gauge) |
| `POST /v1/sessions/commit` | graph bytes; `write_ops`; record bytes | ✓ (byte charge, `#9798`/`#5637`) |
| `POST /v1/points`, `/v1/operators`, `/v1/sources`, `/v1/index/*` | graph bytes; `write_ops` | ✓ |
| **MCP tool calls** | same as the SDK door they wrap — including extraction via the capture tool | ⚠️ **write paths yes; compute/egress NO** — they land on the `""` org child (§1.4) |
| SDK (`TortoiseSDK`) | same as the REST door it wraps | ✓ |
| `ask` | extraction/LLM tokens | ✓ (tokens + USD) |
| retrieval / `search` | *nothing billable* — reads are free by decision; measured for cost only | ✓ (measured, unbilled) |
| embeddings | *nothing billable* — it is our compute under a read or a write already counted | ✓ (measured, unbilled) |
| **end-customer credentials (`tk_` children, `#8693`)** | the *parent* team's meters — the child is a cost of the builder's graph | ✓ (per-team ledger, `#8693` design) |

⇒ **One door is metered for extraction and storage but not for compute: MCP.** That is a `TBD` in §6, not a
decision — closing it needs org attribution at the MCP lane (`NEEDS-APPROVAL`-free; it changes no surface, but it is
`#4490`'s seam, not this document's).

---

## 4 · The tier × dimension matrix — numbers deliberately empty

Two axes only, plus the transition unit. Every numeric cell is the owner's, from measurement on himself and on
private-beta users, after the storage architecture + extractor + MCP deployments land.

| tier | STORAGE allowance (bytes) | STORAGE overage | EXTRACTION allowance (tokens / period) | EXTRACTION overage | `write_ops` (transition) |
|---|---|---|---|---|---|
| `anon` (unclaimed) | `TBD-BY-MEASUREMENT(bytes retained by a zero-email team in its first period — measured on the free-tier cohort)` | **none** — no purchase path at `anon`; the door is a claim, not a card, so the cap **refuses** (`#4815`) | `TBD-BY-MEASUREMENT(tokens per first-session capture — measured on new signups)` | **none** (same reason) | unchanged today (`1000`) |
| `free` | `TBD-BY-MEASUREMENT(p50 and p95 bytes retained after 30 days on the free cohort)` | **none** — `pricing.json` `overage: false`, so reaching the cap **refuses** (`#4815`) | `TBD-BY-MEASUREMENT(tokens/capture × captures per active free user per month)` | **none** — the same carve-out, not a soft edge | unchanged today (`10000`) |
| `solo` | `TBD-BY-MEASUREMENT(same population as free, one tier up)` | **prepaid credits** | `TBD-BY-MEASUREMENT(as free, on the paying cohort)` | **prepaid credits** | unchanged today (`10000`) |
| `pro` | `TBD-BY-MEASUREMENT(power-user cohort p50/p95)` | **prepaid credits** | `TBD-BY-MEASUREMENT(power-user capture rate × measured tokens/capture)` | **prepaid credits** | unchanged today (`50000`) |
| `team` | `TBD-BY-MEASUREMENT(team cohort p50/p95, multi-graph)` | **prepaid credits** | `TBD-BY-MEASUREMENT(team cohort)` | **prepaid credits** | unchanged today (`200000`) |
| above `team` | `TBD-BY-MEASUREMENT(whether a tier exists at all above the team allowance, or storage alone scales)` | **prepaid credits** | same | **prepaid credits** | `#4331` |

**The period is the subscription's own billing window**, not a calendar month — already the meter's definition
(`metering.py`, D10/D13). The matrix inherits it; it does not re-decide it.

**What must be measured before a cell can be filled — the list the owner's pass needs** (this is §5's contract):

1. **Retained bytes per tenant per period, p50 and p95, per tier** — from `get_graph_storage_reading` (MB gauge)
   *plus* the Postgres substrate once it is attributable, over one full billing window.
2. **Elasticity**: bytes per write op, and bytes per capture turn — so a storage allowance can be *reached* by the
   flow that was sold with it (the `#4495` stock/flow defect: a stock cap that the flow cannot reach is an
   allowance a customer pays for and cannot use).
3. **Tokens per capture, p50 and p95**, and captures per active user per month, per tier.
4. **The RAM↔bytes and Postgres↔bytes conversion**, so one storage number covers both substrates.
5. **Where the vector index sits in the storage figure** (§1.3) — without it, a vector-bearing tenant's storage
   number is ~2× under-measured and any allowance built on it is a promise we cannot keep.

---

## 5 · What happens at the boundary — and why nothing refuses on a consumption dimension

**For the two dimensions this document defines, the recorded decisions leave exactly one shape available, and it is
not a hard cap.** ⚠️ That is a statement about **these** dimensions only. The *node stock* is still a hard cap on
`main`, on **every** tier and on the **capture** door (`max_graph_nodes` → `max_points` → a `402` before the write),
which is the model defect `#4495` records and which §8 names as "still the enforced refusal on `main`". This
section describes the boundary a bytes/tokens allowance must have; it does not describe what `main` enforces today.

| recorded decision | what it forbids |
|---|---|
| owner, 2026-08-09 — *"NO capture caps. Tiers are feature baselines; usage is metered separately"* | refusing a capture on a consumption meter |
| owner, 2026-09-22 (`#4815`) — **paid** tiers price features, **never refusal** | a hard ceiling that refuses a **paying** customer's usage on a consumption dimension. `#4815` states its own scope verbatim: *"The free and anon tiers are **not** covered by this ruling and keep `overage: false` (refuse at the cap)"* |
| owner, 2026-09-26 (`#4495`) — *"Exceeding the storage allowance is **PURCHASED OVERAGE, not refusal**"*, via **prepaid credits** | postpaid auto-billing and a refusal at the allowance edge |
| `#4010` — sessions unlimited at every tier; `#3853` — no retention window | any bound that shrinks what a customer may keep, however it is denominated |

⇒ **On every tier with overage on, both billable dimensions are soft: crossing the allowance never refuses the
customer's data.** The only behaviours at the boundary are the three below, and each has a different owner.

| at the boundary | behaviour | who it protects | what the customer sees |
|---|---|---|---|
| allowance reached, credits available | **proceed and draw down credits**; the consumption is recorded against the overage line | the customer's work — never refused | the usage figure, the overage line, and a warning before the credits run out |
| allowance reached, **no** credits | **proceed**, and surface a *degraded-but-complete* state: the customer is over their allowance and is told so on the next read of their usage, with the purchase path | the customer's work (we do not stop them), and our revenue (they cannot silently grow) | a `402`-**class** signal only on the *non-consumption* doors they can already not use — never on capture or commit **for a consumption dimension**. The node stock refuses both today (§8); that is the constraint these two dimensions must *replace*, not a behaviour this table licenses |
| a **safety** bound, not a billing bound — a per-org rate or transient byte throttle | **429 + `Retry-After`**, self-healing in ≤ 60 s | the shared instance and every *other* tenant (noisy neighbour) | a `Retry-After` and a limit/scope/value body |

**The third row is the important distinction and the one lanes keep confusing.** A **throttle delays; it never
refuses the data.** The lane already runs two of them — `RateLimitMiddleware.PATH_LIMITS` (300 req/min/key on
commit) and the 10 MiB/min/org byte throttle (`#9798`, with its own `OVERRIDES:` line on `#4051`) — and both are
classified as *safety*, not *billing*, precisely so that adding one is never mistaken for reversing the
never-refusal ruling. **A per-tenant rate is not a consumption cap**, and this document does not raise or lower
either existing number.

**The one carve-out is `free` and `anon` in the PRICING table — and it is recorded, not assumed.** `#4815`'s ruling is
scoped to paid tiers, and it keeps the two unpaid tiers at `overage: false` — which, on `main` today, is a **refusal
at the cap** (`max_graph_nodes`, enforced at `tortoise/billing.py:1178`; §4's `STORAGE overage` / `EXTRACTION
overage` cells say `none` for exactly this reason). This document neither reverses that carve-out nor relies on it: the
three behaviours above describe a tier whose allowance can be **exceeded**; a `free`/`anon` tenant's allowance is
instead **reached and refused**, which is what `product/pricing.json` (`owner_confirmed 2026-08-07`) still says and
what `#4815` explicitly preserves. Whether the unpaid tiers keep a refusal once the two new dimensions are enforced
is the owner's call in the measurement pass (§4), and until he rules it stays as recorded.

**⚠️ And the carve-out is *wider* than `free`/`anon` in the code, which is the defect, not a second decision.**
`max_points` is set from `max_graph_nodes` for **every** tier (`apply_limits`, `tortoise/billing.py:702`), so the
node stock refuses a **paying** tenant too, on capture (`tortoise/hosted_api.py:16033-16076` returns `402` when
`count + est > max_points`, with no overage bypass) and on the points-gated write doors (`_check_org_limit(org,
"points")` — `:10639`, `:10701`, `:10740`, and every other points door, each via `enforce_org_limit`). `#4815`
itself says so: *"`max_graph_nodes` is a stock enforced as a write
refusal (402) … the node cap binds before the ops allowance, making ~93% of free's and **~83% of solo's** monthly
allowance unusable"* — solo is a paid tier. That is `#4495`'s **model defect** (a stock cap the flow cannot reach),
and it is precisely what the two dimensions in §2 replace once the owner measures. It is not a licence for this
document to add a refusal, and it is not evidence that never-refusal is scoped to `free`/`anon`.

⛔ **There is no row in this table that refuses a capture, a commit, a point, or a read because of a consumed
dimension on a tier with overage on.** The `free`/`anon` refusal-at-the-cap above is the single recorded exception,
and it comes from `#4815`, not from this table. If a future proposal needs a *new* refusal, it is a **reopen** of the
2026-09-22 and 2026-09-26 rulings, argued in front of the owner — never a lane's tuning decision.

---

## 6 · The instrument seams the measurement needs

The measurement pass can only produce the numbers in §4 if these are true. Status at this change:

| # | what the measurement needs | today | what this change does |
|---|---|---|---|
| S1 | **Per-org extractor tokens per period must be READABLE from code** | ✅ `metering.get_capture_usage(org_id)` (landed by `#4051`) | ✅ **readable AND consumed** — same reader, extended with the presence fact the gate needs (`measured` / `unmeasured_reason`), reading `capture_calls` / `capture_tokens_in` / `capture_tokens_out` / `capture_tokens_total` / `capture_cost_usd`, both substrates, zero-view degradation, never raises; `allowance_gate` is its ONE consumer (`#9567` D11/D12) |
| S2 | **Per-org graph MB must be readable, with its precision caveat** | ✅ `get_graph_storage_reading` (needs its caveat read first — `graph_storage.py`) | unchanged |
| S3 | **A per-tenant figure for the Postgres substrate** | ✗ database grain only; declared *not a measurement* (`#5045`) | **not here** — `TBD-BY-MEASUREMENT`; the shape must be *attributed tuple bytes + named exclusions*, and it must be labelled an **estimate**, never a measurement. Belongs with the `#4492`/`#7879` family |
| S4 | **The vector index must be inside the storage figure** | ✗ `GRAPH.MEMORY USAGE` is blind to it | **not here** — `#5331`'s remaining work; until then no storage allowance may be wired for a vector-bearing tenant |
| S5 | **MCP traffic must be attributable where compute and egress are measured** | ✗ lands on the `""` child | **not here** — `#4490`'s seam |
| S6 | **Elasticity: bytes per write op, bytes per capture turn** | ✓ `get_write_op_yield` (nodes per billed op) + `nodes_written`; the **byte** counterpart is open | **not here** — derived at measurement time from S1 + S2 |
| S7 | **No measurement may be presented as a cap** | ✓ the meters are declared measurement-only and fail-soft | §5's classification rule |

**Why S1 and not the others was landed first.** It is the one that is additive and a pure read of columns that
already exist: no new store, no new instrumentation, no route, no metered-response change, no MCP tool, no SDK
method. It turns the recorded residual (*"written but not readable from code"*) into a number the owner can pull per
tenant — which is the difference between a measurement pass that runs and one that does not.

**⛔ NOT in this change, deliberately:** no allowance **VALUE** — the three `ALLOWANCE_KEYS` fields exist and are
carried, but no resolver emits one, so nothing is armed — and no change to `/v1/team`'s metered response, no price,
no invoice. Those all still hold.

**⚠️ §1b's "no request path calls it" is SUPERSEDED — the reader is now an enforcement hook (`#9567` D11/D12).**
`#4051` §1b recorded that this reader was MEASUREMENT ONLY, with zero call sites, and
`tests/test_metering_capture_usage_reader.py` pinned that by scanning every module under `tortoise/`. The
`#9567` change arms the `capture_extraction` dimension the owner ruled (2026-09-26: extraction bills in tokens),
and an allowance cannot be enforced against a reader nothing is allowed to call — so `allowance_gate.py` is now its
**one** consumer, reached from `quota.enforce_org_limit` on both the REST and MCP capture doors, and the pin names
that consumer EXACTLY (`offenders == ["allowance_gate.py"]`) rather than relaxing to a blanket exemption.

Why the reader had to grow a presence fact at the same time: this document is where the "zero is a measurement"
rule lives (§4 — a capture that reported no provider usage records ZERO tokens), while the gate needs the opposite
half — an unreadable ledger is NOT a zero. A reader that degraded every failure to a zero view is the `#4493`
shape pointed the other way: a cap wired to it fails OPEN for exactly the tenant whose meter is broken. Hence
`measured` / `unmeasured_reason` on the same reader, with `absent` (a cumulative FLOW's real zero) decided by the
dimension and `read_error` / `period_error` refused by the gate.

**OVERRIDES:** `#4051` §1b's "the reader is MEASUREMENT ONLY — no request path calls it" — the ruled token dimension
has no other meter, so enforcement has to read this one; the measurement-only reading would leave the owner's
2026-09-26 token ruling unenforceable. See §5's classification rule and the reopen recorded on `#4051`.

**⚠️ "INERT" IS ABOUT WHAT THE GATE REFUSES, NOT ABOUT WHAT IT READS** — the correction that replaces the earlier
"so nothing is refused today" wording here. No resolver emits an allowance value, so no dimension can be over its
allowance and the gate refuses none on that ground. It is **not** free and it is **not** silent: `hosted_api._check_org_limit`
and `mcp_server._enforce_quota` fill the missing allowance keys from `quota.resolve_org_limits` on every **gated**
request (`points` / `documents` / `sessions`), so each such request performs one control-plane read — a PostgREST GET
in Supabase mode, an SDK open plus a `MATCH (t:Team {id:…})` in registry/self-host mode — and a **fault** in that read
is a `QuotaCheckError` → HTTP 500 **even with no allowance configured**. That is the read side of the `#3981` tension,
chosen deliberately (a config read we could not complete must never degrade to "unlimited"); the state that is NOT
refused is a resolver LOOKUP MISS — an org with no record keeps the caller's already-authoritative dict, because a cap
cannot be armed from a record that does not exist.

---

## 7 · What is deliberately not metered, and why

- **Nodes / `max_graph_nodes`.** Retired as the customer-facing unit by the 2026-09-26 ruling; superseded by
  storage bytes. It survives in `pricing.json` as an owner-held value and as the per-node *cost basis*
  (`bytes_per_node`), which is a different thing from a customer-facing meter.
- **Reads, retrieval, embeddings, egress, compute.** Measured, never billed — reads are free **by decision**
  (`billing.reads_free: true`). These are how we check that decision against cost, which is exactly what
  `cost_dimensions.py` exists for. Billing them would contradict the pricing surface.
- **RAM as its own line.** §2.1. It is a substrate of storage and it is not per-tenant measurable on a shared
  instance.
- **Per-seat.** `billing.per_seat: false` — a seat meter beside usage meters is the classic double charge.
- **A retention window as a meter.** Retention is a *policy*, and there is none by decision (`#3853`); metering it
  as a tier feature would smuggle the decision back in.
- **Anything billed per "API call".** The measured 440× spread between one call and the work behind it (`#3359`)
  is why `write_ops` cannot be the only unit and why the call count is not a candidate meter.
- **`mitigation_weight_delta`, the monitoring handler's own latency.** Declared `out_of_scope` with reasons in
  `cost_dimensions.py`; neither is a product cost.

---

## 8 · Recorded decisions this document obeys (and the contradiction test)

This document **implements** these; it reverses none of them. Run first, before anything in §2–§5 was adopted.

| recorded decision | where | this document |
|---|---|---|
| storage is billed in **MB/GB**, node unit retired | owner 2026-09-26, `STORAGE-ARCHITECTURE.md` §0 (carries `OVERRIDES:`) | **obeys** — §2 STORAGE dimension, bytes |
| extraction usage is billed in **tokens**; overage **purchased with prepaid credits** | owner 2026-09-26, `EXTRACTOR-V4-ARCHITECTURE.md` §0 (carries `OVERRIDES:`) | **obeys** — §2 EXTRACTION dimension; §5 behaviour |
| storage overage is **purchase, not refusal** | owner 2026-09-26, `STORAGE-ARCHITECTURE.md` §0 | **obeys** — §5 forbids a refusal at the allowance edge **where an overage path exists** (`solo`/`pro`/`team`) |
| tiers price **features**, never refusal | owner 2026-09-22, `#4815` (**paid** tiers; `free`/`anon` keep `overage: false`) | **obeys** — §5 carries the carve-out verbatim and adds no refusal of its own |
| the **node stock** stays a hard write refusal until the owner measures | `#4815` scope §1, `#4495`, `product/pricing.json` `max_graph_nodes` | **obeys** — §5 records it as the constraint the two dimensions replace, on every tier and on the capture door |
| **no capture caps**; usage is metered separately | owner 2026-08-09 | **obeys** — no consumption cap on either session write lane. The node stock's `402` on capture is the **code** defect §5 records (the constraint these dimensions replace), not a cap this document adopts |
| **no** session cap; **no** retention window of any length | `#4010`, `#3853` | **obeys** — §7 |
| prices are calibrated by the owner **after beta**, from measured consumption | owner 2026-09-26 | **obeys** — every number `TBD-BY-MEASUREMENT` |
| `write_ops` is the billed unit **today**, and redefining it is an owner decision | `metering.py` `#3359` | **obeys** — §2 keeps it as the transition unit; no redefinition here |
| a per-org Postgres **byte** figure would be an allocation, not a measurement | `cost_dimensions.py` `#5045` | **obeys** — §2 FOLD, §6 S3 marks it `TBD` |
| every lane must **not** invent caps before measurement | owner 2026-11-02 (`#4051`) | **obeys** — this is the document that states the shape and leaves the numbers empty |

**No `CONTRADICTS(...)` arises.** The pricing surface (`product/pricing.json`, `owner_confirmed 2026-08-07`) states
`overage_unit: write_ops` at `$5 / 10k`, per-tier `included_write_ops_per_month` and `max_graph_nodes`. **This
document does not edit, supersede or contradict any of them** — it defines the two dimensions that *replace*
`write_ops` and `max_graph_nodes` **when the owner measures**, and states that until then the published values stand
unchanged. **Two values in that file are already in tension with the newer rulings** and the tension is the owner's
to resolve, not this document's: `overage_unit: write_ops` (superseded in intent by the tokens/bytes rulings, not
yet in fact) and `max_graph_nodes` (superseded as the customer-facing unit by the 2026-09-26 ruling, still the
enforced refusal on `main`). Both are recorded, neither is edited here — editing either is the measurement pass's
output, not its input.

---

## 9 · Surface — what this needs approval for

**Nothing.** No MCP tool is added or removed, no public SDK method is added or removed, and no existing result
shape changes. `metering.get_capture_usage` is a module-level function in an internal module with **no** registry
entry, **no** MCP tool, **no** SDK method and **no** route; nothing in `tortoise/hosted_api.py` calls it. Per the
MCP/SDK surface rule, a *field on an existing result* would not need approval either — this change does not even do
that.

---

## 10 · What this document does not decide

1. **Any number** — allowance, overage rate, credit denomination, tier boundary. §4 is a shape with holes, and the
   holes are deliberate.
2. **Which substrate carries the storage bill, and when** — that is `#10000`-family architecture; this document
   only requires that the customer sees **one** storage number over both.
3. **Whether an extraction allowance is a pre-spend admission gate or a post-hoc meter** — the open question
   `EXTRACTOR-V4-ARCHITECTURE.md` §0 already records; it is a pricing/enforcement decision, post-calibration.
4. **The byte gate** (`max_storage_bytes`) — its build is a prerequisite recorded in §1.2; building it and
   configuring an allowance are two different acts and neither happens here.
5. **The `#4495` stock/flow question** (which of allowance and cap is primary) — §4 item 2 is the *measurement*
   that lets the owner answer it; the answer is theirs.
6. **Enforcement of any kind.** No caps wired into any request path by this document, by design and by scope.

---

**Related:** `#4051` (this ruling) · `#4495` (the stock/flow defect and the bytes ruling) · `#5045` (the cost
dimension registry) · `#5331` (the graph-storage meter and its index caveat) · `#3840`
(`docs/ops/extraction-cost-metrics.md`) · `#4492` (`docs/ops/postgres-storage-measurement.md`) · `#4493`
(`docs/ops/cost-allocation.md`) · `docs/architecture/STORAGE-ARCHITECTURE.md` ·
`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` · `docs/capacity.md` · `product/pricing.json`.
