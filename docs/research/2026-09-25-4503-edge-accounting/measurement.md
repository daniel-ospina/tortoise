---
title: "Measurement — the edge class is outside every accounting surface (#4503)"
type: research
domain: economics
doc_status: draft
created: 2026-09-25
updated: 2026-09-25
subjects.team: epistemic-team
issue: "#4503"
aboutSubjects: Tortoise relationship count, edge-resident EP message state, per-edge RAM
aboutObjects: tortoise/quota.py, tortoise/metering.py, product/pricing.json cost_basis, #4333 census, #5380
---

# The edge class is outside every accounting surface — measured

**Issue:** [#4503](https://github.com/danielospina/tortoise/issues/4503) · **Lane:** `obj7-4503-edges` · **Date:** 2026-09-25
**Base:** `origin/main` @ `a2a08beaa` — this branch is rebased onto it, so the branch tree contains it
Measured at: fix/4503-edge-accounting@26f6c5789d26bf169b1a1220242905f344579c41 on 2026-09-26
**Measurement history.** The figures in §2–§4 were taken on **2026-09-25** against this branch's tree, and
naming the branch here is not a convenience — it is the only tree that *can* have produced them:
`tools/edge_census.py` is **branch-only** and does not exist on `origin/main` at all
(`git cat-file -e origin/main:tools/edge_census.py` fails). An earlier revision of this line named
`origin/main` itself, which asserted a measurement tree that cannot contain the instrument and passed
the ancestry gate only vacuously (the named SHA *was* `origin/main`). Every locator in §1 was re-read
against `a2a08beaa` on **2026-09-26** and is unchanged. Three things make that sound, and none is an
assumption:
- The branch tree contains the declared base — `git merge-base --is-ancestor a2a08beaa HEAD` succeeds.
  That is pinned to the SHA, **not** to the moving `origin/main` ref: a sentence citing the live ref
  stops reproducing as soon as main advances, which it has (to `9c8f9c805`).
- Because the instrument is branch-only, comparing it across two MAIN trees is not a check anyone can
  run: an empty `git diff <a> <b> -- tools/edge_census.py` between two revisions that both lack the
  file would mean absent at both ends, not unchanged.
- The two commits between `99a98ddc5` and `a2a08beaa` change `tortoise/assembly.py`,
  `tortoise/commit_ops.py`, `tortoise/projection/entities.py` and tests. The only property they touch
  in the measured path is `supersedes_by` (a 200-char truncation fix), which `tools/edge_census.py`
  never sets — `grep supersede` in it returns nothing.

⚠️ **The staleness gate reads STALE, and that verdict cannot invalidate this finding.**
`tools/finding_provenance.py --validate` exits 1 as soon as `origin/main` advances past the named tree;
main has since moved to `9c8f9c805` (behind by 1), so it does. That check asks whether the tree a
finding was measured on contains today's main — a question about *product* findings, which can be
overtaken. This one cannot be: the instrument is branch-only, so no main tree could ever have produced
it, and the commits main gained touch nothing in the measured path — the counting, capping and pricing
surfaces. `git show --name-only 9c8f9c805` gives `tortoise/mcp_server.py`, `tortoise/sdk.py`, two tests,
`config/ci-surfaces.yml` and a generated doc: no `quota.py`, `metering.py`, `ep.py` or `pricing.json`.
(`tortoise/sdk.py` is imported by the instrument, but the change there is a private
`_assert_window_start_not_inverted` guard, not a counting path.) The line is kept rather than dropped because
the gate's own doctrine is that unknown provenance is not a pass, and a reader must be able to see
exactly which tree produced these numbers.
**Instrument:** `tools/edge_census.py` (shipped with this report) · **Status:** measurement only.

> **What the instrument touches.** Over a **`--uri`** connection a census reads through a raw
> `falkordb` client and issues no DDL — nothing on the target graph is created, altered or deleted;
> this is the path to use against a graph you do not own. Two paths DO open the SDK (which ensures
> indexes on that graph, idempotently, and on an embedded database may run a health recovery):
> `--embedded`, whose backend *is* the SDK, and `--uri` **combined with `--org`** — the cap count
> must come from the cap's own function rather than a reimplementation of its predicate, and the
> census is then taken from that one SDK handle so both halves describe the same graph. That DDL is
> not a side effect of the tool's handle choice — it happens inside the cap's own function, so it
> **cannot** be avoided by opening the graph another way — and the tool therefore refuses
> `--uri` + `--org` unless `--accept-schema-writes` says the write is wanted. Measured on a live
> graph: `--uri` + `--org` created **6 indexes** (including a 384-dim VECTOR index on `Point`),
> `--uri` alone created **0**. A read against a production graph you do not own should therefore
> use a **URI and no `--org`**.
> Relationship-type names read from the graph are never interpolated into a Cypher pattern: the
> census returns them as **values** (`RETURN type(r), count(r)`), so there is no interpreter for a
> crafted name to reach — stronger than binding it as `$rtype`, which still requires the name to be
> a first-class query parameter. A graph that does not exist is **refused**, never created by the
> act of measuring it (`--create-if-missing` is the deliberate opt-in).

> ⛔ **No price change, no cap change, no quota change, no engine change.**
> The findings that imply one are recorded as an **owner question** (issue #4503, protocol shape), not acted on.
> `product/pricing.json` values are untouched.

---

## 0. The finding in one paragraph

Everything Tortoise measures is measured in **nodes**. `count_org_usage(org, "points")` counts
non-episodic `Point` + `Object` + `Subject` **nodes** (`tortoise/quota.py:699-704`);
`:MeteringRecord` carries `write_ops` + `nodes_written` (`tortoise/metering.py:42-59`); and the declared cost basis is
`product/pricing.json` → `billing.cost_basis.bytes_per_node`, an object with **three keys and one
unit — bytes per node**. **Not one of the three has a relationship term.** Meanwhile EP persists four
belief properties **on the relationship** (`r.msg_alpha`, `r.msg_beta`, `r.back_msg_alpha`,
`r.back_msg_beta` — `tortoise/ep.py:241`, `:256`, `:333`, `:366`), so the state that grows with
relationship **density** rather than node **volume** is uncapped, unmetered and unpriced — and,
unlike the node-resident EP state that #2884 fixed, it is **never journaled** (#5380).

**⇔ Two independent axes of divergence, not one.** This lane read the node axis live on 2026-09-25 —
the cap counted **24,978** of **89,701** nodes, **3.59×** — and the denominator there is *every* node
(`MATCH (n)`), the same definition §3 uses below. #4333's own brief reads the same axis as
**24,965** of the **51,012 _labelled_** nodes, **2.04 : 1**
(`docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md:94-101`), because its
"resident" is the labelled set rather than all nodes. Both readings are real and they are not in
conflict — they count different sets — but the two figures must not be swapped for one another, and the
24,978/89,701 pair is **this lane's** reading, not #4333's. This report measures the **relationship**
axis, where the count is not wrong, it is **absent**.

---

## 1. The three surfaces, verified at `a2a08beaa`

| surface | what it counts | relationship term | evidence |
|---|---|---|---|
| **cap** | `count_org_usage(org, "points")` = non-episodic `Point` + `Object` + `Subject` **nodes** | ❌ none | `quota.py:699-704` — `MATCH (n) WHERE (n:Point AND …) OR n:Object OR n:Subject RETURN count(n)`. `_RESOURCE_LIMIT_KEYS` (`quota.py:174-183`) maps `points`/`api_keys`/`sessions`/`users`/`graphs` — **no resource names an edge**, and `documents` (`quota.py:651`) is a node count too. |
| **meter** | `write_ops` (API calls) + `nodes_written` (nodes) | ❌ none | `metering.py:42-59` — the `:MeteringRecord` shape. `nodes_written` is incremented from `plan.reconcile.net_new` (`hosted_api.py:11994`) — a **node** delta. |
| **cost basis** | `billing.cost_basis = {falkordb_per_gb, bytes_per_node, dedup}` | ❌ none | Three keys, one unit. `grep -rn "falkordb_per_gb"` → **exactly one non-test hit: the declaration itself** (`product/pricing.json:20`; the second is `tests/e2e/hosted/fixtures/pricing-e2e.json:19`). `bytes_per_node` has **zero** readers. **The cost basis is a declaration nothing computes or enforces.** |

A note on the grep, because the first revision of this measurement got it wrong: `cost_basis`
appears **31 times** under `battery/`, but every one is the homonymous **LLM-spend provenance**
(`provider_reported` / `estimated` / `mixed`) and is unrelated. The exact form of the finding is the
`falkordb_per_gb` grep above.

---

## 2. Measured — bytes per element shape

**Method** (identical to `#4333` §3.4, which is what makes the comparison valid): a **disposable
isolated** `falkordb/falkordb:latest` container, staged writes, `redis-cli INFO memory` →
`used_memory` read between stages. No other tenant shares the instance. Reproduce with:

```
python3 tools/edge_census.py probe --n 5000
```

**Raw stage readings, n=5,000 nodes / 4,999 edges** — the shipped tool's receipt of record, verbatim (`python3 tools/edge_census.py probe --n 5000 --json`):

```
baseline (fresh instance)                                         2682184  per_element=0
bare :Point node (label + id only)                                3550384  per_element=5000
+ keyword-only Point props                                        4451576  per_element=5000
bare :IMPL edge (no properties)                                   4876168  per_element=4999
+ real IMPL attrs (direction,confidence,weight,label,batch_id)    5499288  per_element=4999
+ the four EP message slots                                       5746088  per_element=4999
```

**Derived marginals** — every figure with the `n` it was divided by:

| element shape | marginal B | n | source |
|---|---|---|---|
| bare `:Point` node (label + `id`) | **173.6** | 5,000 | tool receipt above |
| bare `:Point` node (label + `id`) | **101.8** | 20,000 | hand-run probe, same method (see §2.3) |
| **dressed keyword-only Point** (`id`,`content`,`pointKind`,`status`,`confidence`,`createdAt`,`is_episodic`) | **353.9** | 5,000 | tool receipt above |
| bare `:IMPL` edge (no properties) | **84.9** | 4,999 | tool receipt above |
| bare `:IMPL` edge (no properties) | **89.8** | 19,999 | hand-run probe, same method |
| **dressed `IMPL` edge** (the real attr set — `direction`,`confidence`,`weight`,`label`,`batch_id`) | **209.5** | 4,999 | = 84.9 + 124.6 |
| **dressed EP-bearing edge** (dressed + the four message slots) | **259.0** | 4,999 | tool receipt above |
| — of which the four EP slots alone | **+49.4** | 4,999 | tool receipt above |
| — of which the edge attrs alone | **+124.6** | 4,999 | tool receipt above |

**Re-run stability.** A hand-run of the same stages at the same `n=5,000`, before the tool existed,
gave 258.0 / 354.8 / 124.6 / 49.4 — the shipped tool reproduced them at 259.0 / 353.9 / 124.6 / 49.4,
i.e. **within ≈1 B on every edge figure and ≈0.9 B on the node figure**. The two runs are the
re-run check, not two measurements of different things.

### 2.1 The cross-check that makes this trustworthy

`#4333`'s *independently run* props-only Point probe measured **355.6 B**. This probe, a different
container on a different day with its own property set, measured **353.9 B** — **0.5 % apart**. Two
independent instruments agreeing that closely is the evidence that the edge figures from the same
probe sit on the same scale as the node figures the cost basis is built on.

### 2.2 The comparison that is **not** made

An earlier revision of this measurement compared a **dressed** EP-bearing edge against a **bare**
node and concluded the edge was *more expensive* than the node we price. **That was withdrawn** — it
was a comparison of two different shapes. Like-for-like, at the same `n`:

> **a dressed EP-bearing edge costs ≈259 B — ≈0.73× a dressed keyword-only Point.**

**The edge is cheaper per element than the node we price.** The claim that survives is not magnitude
but **invisibility**:

> **An EP-bearing relationship costs ≈259 B of RAM, is ≈73 % of a keyword-only Point, and is counted
> by nothing, metered by nothing and priced by nothing.** It is also the only **belief-state** class
> that scales with relationship **density** rather than node **volume** — so a graph whose density
> rises grows RAM while every priced and capped surface stays flat, and the node cap
> (`max_graph_nodes`, the intended backstop on the stock) cannot see it.

⚠️ **Read the `n`.** The bare-node figure moved **101.8 B (n=20,000) → 173.6 B (n=5,000)** between
runs — fixed per-instance overhead dominates at small `n`. The **bare-edge** figure (84.9–89.8 B) and
the **property deltas** (+124.6 B attrs, +49.4 B four floats) were stable across
n=800 / 5,000 / 20,000. A per-element number without its `n` is not reproducible; the tool prints the
`n` and warns when it is below 5,000.

### 2.3 Why the n=20,000 rows have a hand-run source

The tool's `--n 20000` run exceeded this lane's command budget, so the two n=20,000 rows come from a
hand-run of the identical stages and the identical `INFO memory` read. Their arithmetic is printed in
the probe's own integer-division-free form here (`2,035,984 / 20,000 = 101.8`;
`1,795,896 / 19,999 = 89.8`) because the shell receipt rounded them to `101` and `89`. Recorded as a
source difference rather than blended into the tool receipt above.

---

## 3. Measured — the relationship axis is *absent*, not mis-counted

The distinction matters: `#4333`'s finding is that four node **sets** disagree. Edges are not a
fifth node set — they are a second **axis**.

| counting surface | predicate | counts |
|---|---|---|
| cap's node set | `(n:Point AND (n.is_episodic IS NULL OR n.is_episodic = false)) OR n:Object OR n:Subject` | nodes |
| displayed | `:Point` only | nodes |
| resident | `MATCH (n)` | nodes |
| **relationships** | **— none of the above reads `-[r]->` —** | **nothing** |

`tortoise/quota.py` is the *whole* cap implementation, and `grep -rn "count(r)" tortoise/quota.py
tortoise/metering.py` → **0 matches**. The census tool reports the two axes side by side precisely so
the absence is visible rather than inferred:

```
$ python3 tools/edge_census.py census --uri 'docker://:falkordb@localhost:6379' \
      --graph probe4503_a --org org_edge_accounting_4503 --accept-schema-writes
relationships: 2000
  by type  IMPL                     2000
  by slot  msg_alpha                0
  by slot  msg_beta                 0
  by slot  back_msg_alpha           0
  by slot  back_msg_beta            0
  EP-bearing (>=1 slot)          0
  all four slots                 0
nodes:
  resident         2003
  point_label      2000
  capped_points    2000
ratios:
  edges_over_resident      0.999
  resident_over_capped     1.002
```

The 2,000 relationships are `IMPL` edges carrying none of the four message slots, drawn between
2,000 `Point` nodes. The cap sees 2,000; the census sees 2,000 relationships beside them, and the
ratio of 1.002 is reported **because** the cap's own count was read from the same graph. That
same-graph read is load-bearing, not incidental: `--org` must be counted through the census's own
SDK handle, because `count_org_usage(org, "points")` with `sdk=None` builds a **second** SDK from the
environment (`TORTOISE_DB_PATH`, or the graph `org_<org>` in URI mode) and would therefore return a
number from a different database — a wrong-graph `0` being indistinguishable, to
`assert_subset_ratio_available`, from a legitimately empty org.

`capped_points` is `null` unless `--org` is passed, and the derived ratio is then **omitted** rather
than substituted: this tool will not invent a denominator, because inventing one is the defect it
exists to expose.

### 3.1 Gap: the live hosted graph is **not** re-measured here

`fly ssh console -a tortoise-y4mjjq` timed out from this lane, so the live relationship denominator is
a **dated reading**, not a fresh one, and the three figures come from two different artifacts:
`docs/architecture/STORAGE-ARCHITECTURE.md` §11.1 for ≈27,310 edges and its §11.3 for the 4,748
operator-joining edges, and #4333's brief for the 14,567 `extractedFrom` edges
(`docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md:115`). The **per-element**
figures above are unaffected (they are
isolated-container measurements), and `tools/edge_census.py census --uri <hosted uri>` makes the
re-measurement one command for the lane that holds the credential. **Stated as a gap, not papered
over.**

---

## 4. Measured — the edge state is not durable

This is the part that turned out to be sharper than the issue body states.

The issue body's coupling note asserts that `#2884` (OPEN) reports EP state is never journaled.
**`#2884` is CLOSED** — fix PR #4543, pinned by `tests/test_2884_ep_state_journaled.py`. So:

| state class | writer | journaled? | survives `rebuild_all`? |
|---|---|---|---|
| node: `posterior_alpha`/`posterior_beta`/`confidence`/`lastDreamedAt` | `ep.py:224-227` emits `ConfidenceChanged`; `dream.py` too | ✅ since #2884 | ✅ **verified below** |
| **edge: `msg_alpha`/`msg_beta`/`back_msg_alpha`/`back_msg_beta`** | `ep.py:241`, `:256` (batch), `:333`, `:366` (direct) — **no `_emit` anywhere** | ❌ **never** | ❌ **verified below** |

**`BELIEF_PROPS = ("confidence", "posterior_alpha", "posterior_beta", "lastDreamedAt")`**
(`tortoise/projection/entities.py:71`) is node-only, and `grep -rn "msg_alpha\|back_msg"
tortoise/projection/` → **0 matches** — so there is no replay branch that *could* restore an edge
message even from a hand-written log line. This is not "the log has it and the writer drops it"
(the `#2897` shape); it is **never emitted**.

### The measurement, end-to-end (a real `rebuild_all`)

From `tests/test_4503_edge_relationship_accounting.py::test_rebuild_all_loses_edge_messages_while_node_belief_survives`:

```
before rebuild : edges=4  msg_alpha=4  node posterior_alpha=3
journal types  : ['ConfidenceChanged', 'OperatorAdded', 'PointAdded']
any edge slot in journal: []
after  rebuild : edges=4  msg_alpha=0  node posterior_alpha=3
```

**Read it precisely:** the edges are **restored** (4 → 4, so this is not a missing-edge artifact),
the node belief **survives** (3 → 3 — the #2884 fix holds), and the edge message state is **gone**
(4 → 0). A rebuild therefore returns a graph that is *half* restored: the node beliefs are restored
to their post-EP values while the edge messages that produced them are not, so the next EP run
warm-starts from a different state than the pre-rebuild graph. **Rebuild is the documented
disaster-recovery path (#2313/#2083), so the recovery path degrades the belief state it is supposed
to preserve.**

⚠️ The test asserting this is a **CHARACTERISATION PIN** — it asserts current defective behaviour and
carries a pointer that **#5380 must invert it**. It is kept because the gap is otherwise invisible:
with the node half fixed, a rebuild *looks* restored.

**Filed as [#5380](https://github.com/danielospina/tortoise/issues/5380)**, with `#2897` receiving a
cross-reference comment only (same state class — edge-attached state outside the replay path — but a
**different mechanism**: `#2897`'s `tags` data *is* in the log).

---

## 5. Relationship to the #4333 census

`#4333`'s brief §7 records **"our own on-disk edge size ❌ estimated only"** — a row with no
instrument behind it. This report closes that row for the **FalkorDB RAM** unit (the
`STORAGE-ARCHITECTURE.md` §11.1 figure is Postgres on-disk at $0.125/GB/mo — a different store and a
different unit, and it must not be substituted into a RAM table). `#4333` has been sent the
`relations`-block measurement as a comment.

**Two rows requested from that census:** total relationship count split by type, and bytes per edge
by shape (bare / attrs-only / EP-bearing) — a `relations` block beside its `bytes_per_node` block.

---

## 6. What this measurement does **not** claim

- **It does not recommend a price, a cap, or a metered dimension.** Whether edges belong in the quota
  model is a **pricing-and-limits** question and the issue says so explicitly. Recorded as the owner
  question on #4503, protocol shape — **not** answered here.
- **It does not re-open D11** (hosted FalkorDB) or O2 (no volume target), and it does not propose
  shrinking the connection layer — the ruling itself is in `STORAGE-ARCHITECTURE.md` §11, and its
  literal `OVERRIDES:` marker is recorded on #4333
  (`docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md:54`).
- **It does not fix the journaling gap.** That is #5380 — a separate, owner-visible change with its
  own parity contract (the #330 pure-fold/graph-fold parity; the #6761 batched-flush boundary). This
  lane measures it; it does not quietly patch `ep.py`.
- **It is not a live hosted re-measurement** (§3.1).

## 7. Measured vs not measured

| question | status |
|---|---|
| does any accounting surface have a relationship term? | ✅ **measured** — none of the three does (§1) |
| marginal bytes per edge shape (bare / attrs / EP-bearing) | ✅ **measured** — isolated probe (§2) |
| dressed EP-bearing edge vs dressed keyword-only Point | ✅ **measured** — 0.73× (§2.2) |
| does the EP edge flush journal anything? | ✅ **measured** — nothing; no record carries a slot (§4) |
| does a rebuild lose the edge message state? | ✅ **measured** — 4 → 0, edges restored, node belief kept (§4) |
| the live hosted graph's CURRENT relationship count | ❌ **not measured here** — `fly ssh` unavailable from this lane (§3.1); tool ships for it |
| `GRAPH.MEMORY USAGE` as an independent memory attribution | ⚠️ **corroboration only** — MB-rounded, sampling estimate (`amortized_edge_attributes_by_type_sz_mb → IMPL = 1` at 19,999 edges, consistent with +49.4 B/edge and not refining it) |
| the embedded-Point comparator | ❌ **not measured in this probe** — `#4333` §3.4 owns it (4,701 B isolated-probe; 1.50 KB stored / 2.05 KB resident graph-wide). An earlier revision of this report asserted "≈half of an embedded Point" and it was **wrong by ~9×**; the clause is deleted, not softened. |

---

*Measurement only. No price, cap, quota or engine change is proposed. Every figure carries its `n`
and its raw reading, and every claim names the decision it does or does not contradict.*
