---
title: "Fleet coordination state — the inventory, the measured volume, and the costed hosted Tortoise option"
type: evidence
domain: platform
doc_status: final
created: 2026-10-21
subjects.team: epistemic-team
aboutSubjects: fleet coordination state, claims, dispatch ledger, capacity map
aboutObjects: "tortoise, agent-infra#1709, tortoise#5052, tortoise#7871, product/pricing.json, STORAGE-ARCHITECTURE.md §17"
---

# Fleet coordination state — where it lives, what it costs to host, and the one slice moved into the graph

**Measured on:** `2026-10-21`, on branch `feat/graph-work-claims` (worktree `.worktrees/graph-claims`),
against the committed fleet-map census of `2026-10-17` and a private lane graph for the per-claim probe.
**Owner ruling (relayed, not re-litigated):** the wait for Tortoise's Postgres migration is over — the
hosted cost is to be explored NOW and the hosted version used rather than blocking on the migration.
Recorded on `daniel-ospina/tortoise#7871` (the graph issue that maps this) and
`daniel-ospina/agent-infra#1709` (the claim-work issue).

## 0. What the inventory says, in one sentence

**The fleet has exactly one store two boxes can both read — `refs/claims/*` on a git origin — and it
deletes its own history on release, while every store that keeps history lives on one box's `/tmp`.**

That is not a metaphor. It is the measured reason two boxes were handed the same fix and produced
`#2247` and `#2250`, 31 minutes apart, and nothing noticed. Neither PR has a claim ref: the registry
only stops a duplicate for work someone remembered to claim, and dispatch does not require one.

## 1. Inventory — every piece of coordination state, where it physically lives

Read on `2026-10-21` on cell-3 (`HOME=/tmp/cell-home`); counts are as-of that read, not estimates.

| State | Where it physically lives | Which machine can read it | Durable across a reboot? |
|---|---|---|---|
| **Claim on a unit of work** (issue / PR head / landing) | `refs/claims/*` on the **target repo's git origin**; owner + `lease_until` in the claim commit message; **deleted** (`--force-with-lease`) on release | **Any box with read access to origin** — the only genuinely cross-box store. `git ls-remote 'refs/claims/*'` = **7 refs** in `tortoise`, **7** in `agent-infra` | ✅ the ref is on GitHub · ❌ **the history is not** — a released claim is deleted, so "who held #5052 yesterday" is unanswerable |
| Claim snapshot cache | `$HOME/orchestrator/state/claims-snapshot` (**7 lines**) | that one box only | ❌ `$HOME` is `/tmp/cell-home` — **wiped on reboot** |
| Capacity / lane state | `$HOME/.pi/agent/state/fleet-state.json` (**19,952 B**, `generated_at 2026-10-21T03:20`) + `lane-registry.tsv` (**82 B**) | that one box only; the file is a **12-hour-old snapshot** when read | ❌ under `/tmp`; regenerated, never inherited |
| Dispatch ledger / worklist | `$HOME/orchestrator/state/` — **187 files, 380 KB** (`candidates.txt` 13 lines, `checked.txt`, `fixcount-*`, `collided`, `claim-hosts`, `disk.tsv`) | that one box only | ❌ under `/tmp` |
| WIP ownership (which lane is on which branch/worktree) | `.worktrees/<lane>` on each box + local branches | that box; `tools/who_is_on.py` enumerates **remote refs only**, so a never-pushed branch or a worktree's uncommitted work is invisible (#4256) | ❌ worktree is local disk |
| PR ownership | GitHub assignee/review state **plus** `refs/claims/pr-<N>` (**2 refs** today) | every box (GitHub) | ✅ GitHub · ❌ the claim half is deleted on release |
| Fleet capacity map (in flight) | `refs/boxes/<host>/<record>` — `capacity.sh` in `agent-infra` (`feat/capacity-autonomy`), published on the same origin | every box **once a box publishes**; `git ls-remote 'refs/boxes/*'` = **0 refs today** | ✅ by design · ❌ **nothing has been published yet**, so no box can read a peer's capacity right now |
| The owner's context | a human, one session | the human | ❌ |
| Prose on GitHub | issue bodies, PR bodies, comments | every box | ✅ — but **not machine-readable**: no CAS, no expiry, and a gate that trusted a comment blocked a P1 forever (#8175) |

### 1.1 Which of them are already broken across boxes — stated plainly

- **The claim registry is correct and unread at the point that failed.** The measured duplicate
  (`#2247` / `#2250`) happened on work dispatched **without** a claim. A registry that only stops
  duplicates for claimed work does not stop the unclaimed dispatch that produced the duplicate.
- **The claim store's own history is gone by design.** 7/7 refs in each repo are stale (#2246: "7 refs
  / 25 h / 0 lanes"), and every released claim was deleted — so the store cannot answer the one
  question an inventory asks: *who owned this, and when did they stop?*
- **Every history-keeping store is single-box, and on a cell it is on `/tmp`.** `fleet-state.json` is a
  snapshot of the box that wrote it; `who_is_on.py` sees only what a remote ref happens to name.
- **The capacity map, the one in-flight fix for exactly this, has published nothing yet** (`refs/boxes/*`
  = 0 refs), so today's placement decisions are still local — which is how cell-1 sat at load 29.8/8 cpu
  while cell-4 sat at 0.56/8.

## 2. The costed hosted option — measured volume, then price

### 2.1 The volume, measured (not estimated)

| Quantity | Measured value | Source |
|---|---|---|
| Fleet map graph, as a hosted tenant would hold it | **21,065 nodes** — 10,374 `:Point`, **10,583 `:GraphEvent`**, 60 `:Source`, 1 `:Event` | `docs/evidence/7983-cost-magnitudes/graph-content-census.json` (2026-10-17, live self-hosted map at `:16379`) |
| Resident size of that map | **35 MB graph memory** = **0.0352 GB** (the census's own `total_graph_sz_mb`, i.e. 21,065 nodes × the measured **1,672 B/resident node**) | `docs/evidence/7983-cost-magnitudes/graph-content-census.json` (`graph_memory_usage.total_graph_sz_mb: 35`); `STORAGE-ARCHITECTURE.md` §17.4.3 for the per-node rate. ⚠️ `0.1499 GB` (§17.4.6) is **not** this map: it is the cache's 143 MiB over the **89,701**-node stock §17.4.3 derives 1,672 B from, so quoting it here overstated this map 4× |
| **Charged** nodes of that map | **≈10,435** (Points + Object/Subject/Event; the `GraphEvent` journal is **not** charged) | `tortoise/quota.py` (the `points` counter IS the pricing `max_graph_nodes` cap) |
| **One claim**, measured on a private lane graph today (`docker://:@127.0.0.1:16393`) | **+2 nodes per claim on the `create_point` path** (1 `:Point` + 1 `:GraphEvent` journal entry); **+5 MB for 3,000 claims** (incl. 2 MB of index) ⇒ **≈1,748 B resident/claim**; **10.7 ms per create** (3,000 creates in 32.0 s) | this lane's probe, `GRAPH.MEMORY USAGE` + `MATCH (n) RETURN count(n)` |
| **One claim on the SHIPPED write path** (re-measured in review, since the tool no longer uses `create_point` — it needs an atomic `MERGE`, see §3) | **+1 node** (`:Point`) per claim; `create_point` on the same graph adds **≈+2 per point** (`:Point` + `:GraphEvent`) plus two ONE-OFF graph-level meta nodes (`EpMeta`, `GraphEventMeta`) on a graph's first point | 30 `create_point` calls on the review graph: total +62 nodes; `MATCH (n) RETURN count(n)` and label deltas before/after `SDKClaimStore.create` vs `sdk.create_point` |
| Chargeable nodes per claim | **1** (the journal entry is not counted; the Point is) | `tortoise/quota.py` |
| **Writes per day** — lane dispatches ≈ PR creation | **63–118/day** across the two repos: `tortoise` 39/81/52/28 PRs on 10-18…10-21, `agent-infra` 27/37/27/11/18 | `gh pr list --state all` on each repo, run today |
| **Claims per month** | **≈3,000** (100/day × 30) ⇒ **3,000 chargeable nodes**, **3,000 resident nodes** (the shipped MERGE path), **≈3 MB**, **≈6,000 write ops** (create + release) | derived from the two rows above |
| **Reads per dispatch pass** | **13** candidates (`candidates.txt`), i.e. ~13 `status` reads per pass; 360 passes/day/box ⇒ **≈4,680 reads/day/box** | `$HOME/orchestrator/candidates.txt`, the 240 s cycle |

### 2.2 The price (every number sourced)

| Line | Rate | Source |
|---|---|---|
| Plan tiers | anon 1k · **free 10k nodes / 10k writes, $0** · **solo 25k / 10k, $9** · **pro 100k / 50k, $25** · **team 600k / 200k, $149** | `product/pricing.json` (`status: current`, `owner_confirmed: 2026-08-07`) |
| Writes beyond the included volume | **$5 per 10k write ops**; **reads are free** | same file (`billing.overage_price_per_10k`, `reads_free: true`) |
| FalkorDB Cloud RAM | **$73 / 1 GB / month** (STARTUP) · **$350 / 8 GB / month** = $43.75/GB (PRO) | `docs/architecture/STORAGE-ARCHITECTURE.md` §17 (the two-store price table) |
| Disk / egress / IOPS | $0.125/GB · $0.09/GB (250 GB incl.) · $0.024/IOPS (3,000 incl.) | same table |
| Compute we already run | Fly app `tortoise-y4mjjq`, `shared` cpu, **2 cpus / 4,096 MB**, `min_machines_running = 1` | `fly.toml` |

### 2.3 The answer

- **Coordination state alone** (claims only, retaining history — the point of moving it): 3,000
  chargeable nodes/month at 100 claims/day. Free's 10,000 nodes holds **~3 months**; **solo ($9/mo,
  25k)** holds **~8 months**; **pro ($25/mo, 100k)** holds **~33 months**. Writes stay inside every
  tier's included volume (**6,000/month** against free's 10,000). Reads are free, and the documented
  per-key ceiling — **100 req/min ≈ 10–20 tool calls/min** — is four orders of magnitude above the
  measured **~0.25 reads/s** this workload needs.
- **Coordination state + the existing fleet map in one graph** (the useful configuration — the map is
  what a lane steers by): **10,435 chargeable nodes today**, which **already exceeds free's 10,000**, so
  the floor is **solo at $9/month**; the combined count crosses solo's 25,000 in **~5 months** at the
  measured claim rate, at which point it is **pro at $25/month**. Per box (3 boxes): **$3–8.33/box/month**.
- **Marginal infrastructure on our own hosted deployment: $0.** The whole state is **0.035 GB** (35 MB,
  §2.1 — *not* the 0.1499 GB §17.4.6 measures over a different, 89,701-node stock) against
  an **8 GB PRO FalkorDB Cloud** instance already on the bill at $350/month (~0.44% of that line), and the
  Fly app it fronts is already running for the product. Upgrading a *plan* is a product decision about
  our own internal tenant; it is not new infrastructure.
- **What is missing to use hosted today** (measured, not assumed): **a credential AND a hosted write
  path for this slice.** The credential half is real: `.mcp.json`'s `tortoise` entry declares
  `https://api.premiselabs.co/mcp/` with **no `Authorization` header**, and neither `TORTOISE_API_KEY`
  nor `TORTOISE_API_URL` is set in a cell environment — so every cell writes to its own box-local graph
  (`:16379`, `docker://`) or to nothing.
- **The claim tool is NOT a drop-in on hosted, and this README said it was.** `SDKClaimStore` writes
  through `sdk._get_proj().g.query(<raw Cypher>)` — the bolt/docker projection handle — because the CAS
  and the fence are Cypher statements (§3). The hosted surface is MCP/HTTP, where a graph handle does
  not exist, so the `create_point`/`get_point`/`update_point`/`query` SDK calls this paragraph used to
  cite are **not** what the pilot calls, and swapping the endpoint would not move it. The tool says so
  itself: `TORTOISE_API_URL` is explicitly REFUSED as proof of a store because it "has **no hosted
  client**" (module STORE section, and `_require_store_configured`), and a dispatch against a target it
  cannot write must refuse rather than land box-locally. So the honest answer to "what is missing" is
  **a credential, a plan ≥ solo, and a hosted store/transport for the claim slice** — the last one is
  work (a client + a write path with an atomic equivalent of `MERGE`-on-id), not a config flag.
- **So the money is $9–25/month and it does not pass through Postgres.** The claim slice needs Point
  create/read/update only; the relational record store (`#7879`) is not on this path. That is the
  measured answer to "are we waiting on the migration": **no** — but the hosted migration of THIS slice
  is a small, separate build (above), not a key swap, and the pilot's comparable economics hold whether
  the graph is self-hosted `bolt://` (already running) or hosted.

## 3. The pilot — one slice, the claim, in the graph

`tools/work_claim.py` (this branch), `tests/test_work_claim.py` (77 fake-store tests + 3 live tests, 80
collected — re-derived with `--collect-only`, and pinned against this line by the suite itself), contract
`rc 0/1/2/3` identical to `claims.sh` for `acquire`/`release` so a caller needs no
new branch (`rc 3` is a malformed invocation; `status`'s rc reports that it ANSWERED — see §3.1).

**What it stores.** One `:Point` per held unit, id = `sha256(repo, key)` rendered as the SDK's ULID shape —
**deterministic, and never containing a box** (`claims.sh`'s own recorded lesson: *"a KEY CONTAINING THE
BOX ID IS NOT A CAS"*). Content = one canonical line (`repo`, `key`, `owner`, `session`, `branch`, `head`,
`lease_until`, `epoch`, `purpose`, `claimed_at`, plus the capacity fields); props are the same fields
namespaced (`claim_owner`, `claim_epoch` — the SDK **silently ignores** a client-supplied `owner`, measured
on a live write).

⛔ **`create_point` is NOT the CAS — the first version of this pilot said it was, and measurement
falsified it.** Two `create_point(id='probe-dup-1')` calls on a live FalkorDB left **two `:Point` nodes
with the same id** (contents `A` and `B`), and `get_point` returned A: the loser's node exists and is
invisible to every read by id, so a property query reports one unit of work **twice, with two owners**.
Every write is therefore ONE atomic statement —

```cypher
MERGE (n:Point {id:$id}) ON CREATE SET n += $props, n.content=$content, n.__cas=$token
RETURN count(n) AS c, collect(n.content) AS contents, collect(n.__cas = $token) AS wins
```

with a token unique to the call: `MERGE` applies its `ON CREATE` exactly once, so only one
contender can create the id; the loser's statement matches the existing node and returns the
winner's content. **Every returned value is an aggregate on purpose**: a bare `n.content`
alongside `count(n)` GROUPS BY that column, so a legacy graph holding two nodes for one id
returns one row per duplicate with `c = 1` — the duplicate guard never fires, which is how an
earlier form of this statement (quoted here until review of #9487) silently disabled itself
(measured). The aggregates make `count(n)` report the real node count and give `contents`/`wins`
one row regardless of duplicates. `wins` reports whose create ran, which the store uses as a
consistency check — the verdict is the content comparison, because a contender writing a
byte-identical record is the same identity, not a second holder.
State changes are `MATCH … WHERE n.claim_epoch = $expected SET …`, which touches 0 rows when a peer moved
the epoch — that is the fence, in the store, not in the caller.

- **create = atomic MERGE + token**, keyed `issue-<N>` / `pr-<N>` — disjoint prefixes, like `claims.sh`.
- **read-back is the authority**, never the write's exit code.
- **lease is data, not liveness** (`#1709` rec 3) — a remote pid cannot be probed.
- **fencing by epoch** (`#1709` rec 6) — a reclaim bumps it, the write is conditional on the epoch the
  caller read, and a lost reclaim names the winner. The hand-off closes: acquire → release → acquire is
  covered by the live test (epoch 1 → 2); a released record is reclaimable because there is no holder
  left to race, and a **live** holder is never displaced. A store that cannot express the precondition
  (a blind `update_point`) makes `acquire` refuse (rc 2) rather than double-grant, and the operator
  route is `release --force --reason`, which records the reason and the releasing identity.
- **release expires in place instead of deleting**, which is the one behaviour deliberately different from
  `refs/claims/*`: the ledger keeps its history, so "who owned this, and when did they stop" is answerable.
- **the capacity fields are stored, never measured** — `capacity.sh` measures (`host`, `ncpu`, `lpc`,
  `lanes`); this tool persists them **with the claim**, so the fleet's claim map and capacity map are one
  record. It is not a second probe, and it does not touch `capacity.sh`. A field that was **not**
  supplied is stored as the explicit marker `absent`, never as `0` — `0` is a measurement of an idle box,
  and a claim taken without a measurement must not assert one. `status --repo --json` carries all four
  through, so the shipped reader answers "who holds this, and on how loaded a box".

### 3.1 The failure path — it REFUSES, and says why

`rc 2` is a **refusal**, and the controller must not dispatch:

```text
$ env -u TORTOISE_DB_URI uv run python tools/work_claim.py acquire --repo … --issue 5052 --session cell-t1
REFUSED: no claim store configured: set TORTOISE_DB_URI (the shared graph). An unset store is
refused rather than answered from a default target — an empty read from the wrong graph looks
exactly like 'nobody holds this' (#7871).
rc=2
```

(Order matters: `acquire` checks its OWN identity first, because a claim whose owner no later
process can reproduce could never be released. Omit `--session` and you get rc 3 with
`usage: acquire needs --session …`, not this refusal.)

⛔ **`TORTOISE_API_URL` is NOT proof of a store** (review of this branch, measured). Accepting it
armed the guard and then let `TortoiseSDK()` ignore it — the SDK reads only `TORTOISE_DB_URI` and
falls back to the box's EMBEDDED file. `check` printed `ok: claim store reachable` and exited 0
while the claim landed locally: acquired here, free on every peer, which is the duplicate-dispatch
failure reported as a success. The guard now names the API URL explicitly and refuses it, and
`check` prints the resolved target (`ok: claim store reachable (docker://…)`).

⛔ **`check` returns its siblings' verdict**: a box-local target is **rc 2 with the refusal**
(the same answer `acquire`/`status`/`release` give) unless `--allow-box-local` acknowledges a
single-box trial — before that it printed `ok: …` on stdout and exited 0 with the
disqualification only as a stderr `WARNING`, which is a dispatch preflight saying "go" for the
exact store shape the tool refuses (review of #9487 P2). `check --json` emits the same verdict as
one object.

`status` ANSWERS, so its exit code is the answer's deliverability, not the claim's liveness —
`free`, `expired` and `held` are all rc 0 and the JSON `status` field says which. `claims.sh`'s own
status verb is the opposite polarity (rc 1 when UNHELD); the branchable "a live holder exists" code
is `acquire`'s/`release`'s rc 1. The difference is stated in the module's CALLER CONTRACT rather
than left for a caller to discover.

Every shape of "I cannot answer" is rc 2, and each one names its cause: an **unset** store (refused
*before* any query — the SDK would otherwise answer from a default target, which is #7871's measured
read defect, "an unset URI returning 0 rows silently"), a **store that resolved to the box-local
embedded file**, an **unreachable** store, a **write whose read-back fails**, a **reclaim the store
cannot fence**, and a store that **neither creates nor returns** a claim. A reachable store with no
claim is a different, real answer (`status: free`, rc 0) and the two never collapse. A **malformed
invocation** is rc 3, not rc 2: a controller retries rc 2, and retrying a bad command cannot help.

⛔ **This is a deliberate departure from a recorded decision, and it is marked as one:**

> **OVERRIDES:** the fleet claim registry is fail-OPEN when its store is unreachable
> (agent-infra#1709, 2026-10-14 — chosen because review/merge duplicates are idempotent per head) —
> this dispatch-time claim is fail-CLOSED instead, because the measured cost of proceeding blind at
> dispatch is a duplicated lane and PR (#2247/#2250), not a repeated idempotent side effect.

The recorded decision is not overturned here. `claims.sh`'s merge/review path keeps its fail-open
behaviour untouched (this branch does not edit it), and the departure is **scoped to dispatch**, with
the evidence for reopening the broader decision posted on `agent-infra#1709`.

## 4. Contradiction test — the recorded decisions this work had to pass

Two statements were found that could have owned this question, and both are addressed rather than
overridden silently:

1. **`agent-infra#1709` (2026-10-14, scoping comment):** *"a coordination primitive must **fail open**
   when its store is unreachable rather than stall the fleet"* — recorded for the **review/merge** path.
   Addressed by scoping the fail-closed behaviour to **dispatch** and adding the `OVERRIDES:` line above.
2. **`agent-infra#1709` (research, rec 2):** *"Implement the cross-box claim as a **git ref**, with no
   new infrastructure."* Addressed by **not replacing it**: `refs/claims/*` keeps working and stays the
   merge-path authority (its "acquire-time CAS is the authority" design is copied, not contested). What
   the measurement adds is that the ref store **cannot keep the history** this inventory requires
   (released claims are deleted) and that "no new infrastructure" is now a **$9–25/month** line rather
   than an unknown — which is exactly the cost the owner ruling asked to be explored rather than deferred.

No recorded decision was found assigning coordination state to any other store, or restricting the graph
to product memory only. `tortoise#7871` (the graph as the orchestrator's map) and `tortoise#5052` (an
authoritative, expiring claim registry) both point at the same gap this pilot fills for one slice; `#5052`
specifies the record's fields (`repo, issue, session, branch, head-sha, ttl, purpose`) and this pilot
stores all of them.

## 5. What was deliberately NOT done

- **Did not touch `claims.sh`, `capacity.sh`, `orch-loop.sh`, or any controller** — those are live lanes
  (`feat/capacity-autonomy` / PRs `#2247`, `#2250`). This branch ships the store and its contract; wiring
  dispatch to call it is a separate, reviewable change in `agent-infra`.
- **Did not migrate any other slice** — no worklist, no worktree registry, no PR publish pipeline. The
  brief was one slice, and a ledger that grows by one authority at a time is the point.
- **Did not declare the kind in a pack.** `work-claim` is registered in-process by the tool's own
  idempotent `register_claim_kind()` (`domain_loader.register_kind`), which `SDKClaimStore.__init__`
  calls — deliberately not as an import-time side effect, because loading the domain loader prints the
  pack/ontology boundary warnings and an import hook would put them on `--json`'s stdout. Promoting it
  into `packs/agent-ops/manifest.yaml` is the follow-up once a second consumer exists.
- **Did not re-litigate the owner ruling**, and did not treat `#7871`'s status as a blocker: its gap 0
  ("the live graph refuses every write") was measured against the **box-local** graph at `:16379`; every
  write in this pilot was verified against a live graph, and the hosted tenant is a different endpoint.

## 6. Reproducing

```bash
# 1. The HERMETIC contract — no store at all, so the three live legs skip VISIBLY.
#    `TORTOISE_TEST_CARVE_OUT=1` is the embedded-only lane; without it a URI-less run of a
#    Docker-lane test file errors rather than skips (measured on this branch: the 3 live legs
#    SKIP under the carve-out flag and ERROR without it).
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_work_claim.py -q   # 77 passed at this head, 3 skipped

# 2. The FULL file — the per-claim volume probe on a private lane graph (so no peer's target
#    is touched), which is also what runs the three live legs.
uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
uv run pytest tests/test_work_claim.py -q            # 80 passed (all three live legs run)

# `--allow-box-local`: this lane's container is loopback BY DESIGN. EVERY verb that reads or
# writes the store (`acquire`, `status`, `release`) refuses a loopback target otherwise,
# because a claim there is invisible to every peer (see §3). `check` refuses it too (rc 2).
uv run python tools/work_claim.py acquire --repo daniel-ospina/tortoise --issue 5052 --allow-box-local \
    --session cell-t1 --branch feat/x --purpose "pilot" --host cell-t1 --ncpu 8 --lpc 372 --lanes 3
uv run python tools/work_claim.py status --repo daniel-ospina/tortoise --issue 5052 --json --allow-box-local
uv run python tools/work_claim.py status --repo daniel-ospina/tortoise --json --allow-box-local   # repo-wide
uv run python tools/work_claim.py release --repo daniel-ospina/tortoise --issue 5052 --session cell-t1 --allow-box-local
```
