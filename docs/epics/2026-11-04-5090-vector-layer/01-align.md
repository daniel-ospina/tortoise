---
title: "Strategy alignment — epic #5090, the vector layer"
type: decision
domain: planning
doc_status: draft
created: 2026-11-04
updated: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #5090, the vector layer, decision V1, embed gate, STORAGE-ARCHITECTURE.md §12"
---

# Strategy alignment — epic #5090

> **Citation convention:** a pointer to a **code** location names the **symbol or the literal**, and does
> not carry a `path:line`. Four line-number citations in this file went stale by 30–70 lines within two
> days of being written (`#11261`), because ordinary edits above them move the target. Grep the named
> symbol; where a bare literal is the anchor, grep the literal. Document citations (`docs/…:line`) are
> out of this convention's scope.

**Stage:** 1 of 6 (`epic-workflow` → `epic-align`). **Epic:** #5090 — *the vector layer — what earns an
embedding, and where it lives.* **Measured at head `641e5f1b37`.** **Review gate:** verifier (fresh
context) — **two cycles**, and **cycle 2 changed the decision from PROCEED to REPOINT**. The verdict log,
which supersedes this line, is **§6**.

> ⛔ **READ §8 FIRST.** It records the decisions the repo and the graph had **already settled** before this
> lane derived anything, and the **six** things this lane proposed and then withdrew — several already
> refuted, one on a do-not-re-invent list (all six enumerated in §8.2). §1–§7 are kept as the reasoning
> trail; where they conflict with §8, **§8 wins**.

---

## 0. Three premises the lane brief asserts that do not reproduce

Recorded first, because the scope is wrong if they are taken on trust. Each was checked with a command.

| lane brief says | measured at this head |
|---|---|
| *"the runtime warns on every search"* | **The warning is SETUP-time, not per-search.** `_record_vector_index_inventory` (`projection/__init__.py`, reached from `_ensure_indexes` via `_ensure_indexes_declared`) emits `log.warning` **once per (endpoint, graph) per process**, guarded by `_VECTOR_INDEX_GAP_WARNED`. The **query-time** fallback logs at **info/debug** (source comment cites #1359 for *"not warning"*). It *looks* per-search on any short-lived CLI process (each `goal-state.py --line` invocation is a fresh process); it is not per query. **The full-scan defect is real and is paid on every query — the warning framing is what is wrong.** |
| *"$73/GB"* as a per-byte cost of the *whole* graph | Correct as the recorded basis (`billing.cost_basis.falkordb_per_gb = 73.0`), but it must be applied to the **unindexed vector payload**, not to 195 MB — ⚠️ and *"removable"* is not a reading the 2026-11-10 ruling leaves open for `:Object`/`:Subject` (see §7.1 item 3). See §3. |
| the epic is *"3 of 4 DoD bullets met"* | **2 of 4 are MET at this head** — `uv run python tools/dod_5090_probe.py` (2026-11-08) returns **`MET / PARTIAL / MET / PARTIAL`**. The command is the provenance, not a recorded sha: the probe stamps the head it runs at, and a sha written into prose is a fact relative to a history a rebase rewrites — so a later reader re-runs it rather than trusting one. Row 2 is PARTIAL; row 4 is PARTIAL rather than `NOT MET` because V1 is **DECIDED (2026-11-07)** — `STORAGE-ARCHITECTURE.md` §12.1c's verdict line records it (*"DECIDED 2026-11-07 — the vector index does NOT live in FalkorDB"*, with `V1 decided` stated on that same line), and the probe reads `V1 decided` as a decided marker. ⚠️ Row 4 can never reach `MET`: its `implemented` leg is `UNMEASURED` by construction, because a decision record proves a decision, not an implementation. |

---

## 1. Adversarial strategy test

### 1.1 What the epic claims, against what is there

| epic body says | at `641e5f1b37` | how verified |
|---|---|---|
| 141 MB graph, 45 MB indices, 25k nodes | **195 MB, 93 MB indices (48 %), 68,526 nodes** | `#10976` body, measured 2026-11-04 on graph `live-v4`; same figures as the lane brief |
| ≈50 MB of stored vector properties | **19.4 MB** — the pre-ruling *"removable"* figure (12,657 × 1,536 B = 19,441,152 B); the ≈50 MB is the whole vector class, including the `Point` leg that *is* indexed; the unindexed `Object`+`Event` subset is the payload a removal would have targeted — ⚠️ **REFUSED for `:Object`/`:Subject` on 2026-11-10 (`#11395` comment `6781921075`), so nothing here is reclaimable, and for `:Event` the 2026-09-24 direction stands** | the epic's own 2026-10-08 comment (a scope correction over the 2026-09-24 measurement, not a new reading) |
| "no rule says what earns an embedding" | **the rule exists**; it is off by default | `embed_gate.RULE_SET is value_gate.RULES` → `True`; `embed_gate_enabled()` reads `TORTOISE_EMBED_GATE`, unset in the **4** shipped config surfaces that exist (the instrument walks 6; `docker-compose.yaml` and `Dockerfile` are *absent* from this tree) |
| "12,657 embeddings stored and unindexed" | **still true in substance** — index creation covers `Point` only | `_ensure_vector_index_api` in `projection/__init__.py` — the `CALL db.idx.vector.createNodeIndex('Point', 'embedding', …)` procedure literal and its Cypher fallback `CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding)`; no other label's vector DDL in `tortoise/`, `graph-scripts/`, `tools/`. (the `create_cypher()` renderer in `graph_key_restore.py` **replays** a dumped catalogue; it never decides.) |
| a fallback query reports the fallback | **MET** | `#4999` / PR #5159 added `mechanism: index \| scan \| scan_fallback` |

**The labels, precisely.** The served/written set is `{Event, Object, Point, Source, Subject}`
(`ENTITY_TYPE_LABELS`). `Point` is indexed ⇒ **four** labels are unindexed ⇒ *"index them in the graph"*
means **four further resident HNSW structures**, not three. ⚠️ `STORAGE-ARCHITECTURE.md` §12.1c **said**
*"a SECOND … and a THIRD"*; that count was wrong and **this PR corrects it — see §8.3**.

### 1.2 Alternatives considered

| # | alternative | verdict |
|---|---|---|
| **A1** | **Discharge the epic as complete.** | **Rejected — on one reason, and it is the only one that survives checking.** Not *"closing would orphan `#4333`"* (that issue is independent; closing a parent removes a link, not the issue). Not *"it abandons row 4"* (V1 lives in `STORAGE-ARCHITECTURE.md` §12.1c/§14.3, not in the epic — and it was **answered 2026-11-07** in favour of moving the index to Postgres). Not *"no capstone exists"* (a capstone is a Stage-6 artifact and cannot exist for an epic still in Stage 1 — its absence proves nothing). **The reason is `§14.3 V3`, whose row now reads *"Unblocked by V1 — ✅ the DECISION is answered, ⚠️ the WORK remains (2026-11-07)"*: the epic is the only ACTIONABLE record of that remaining work.** ⚠️ **Cycle-2 withdrawal: the earlier wording claimed the epic was the *only* artifact binding `#4997` to V1. That is false** — `STORAGE-ARCHITECTURE.md` §14.3 **V3**'s row states it, and so does the runtime warning text in `_record_vector_index_inventory` (`projection/__init__.py`) (*"Whether those labels get an index is §14.3 V3 of STORAGE-ARCHITECTURE.md; V1 settled only WHERE an index lives"*). The epic is not the only *record*; it is the only one that can be **closed, reassigned or repointed** — a doc row and a log string cannot. Discharge it and the unindexed-label defect keeps its prose and loses its work item, which is how a deferred defect becomes an accepted one. **What follows is REPOINT (§7), not PROCEED and not discharge.** |
| **A2** | **Index the four unindexed labels in FalkorDB.** | **Rejected, and the brief forbids it without a stated non-reversal.** It is §14.3 **V3**'s content (V1 settled only WHERE the index lives), and it commits **four** further permanently-resident HNSW structures on a store billed at provisioned RAM with no eviction; and it deepens the thing the epic exists to undo. Measured benefit at today's volume: **~6 ms** (`2.74 ms` indexed vs `9.26 ms` full scan of 7,859 `:Object` vectors). |
| **A3** | **Answer V1 = move the vectors to Postgres now.** | **Rejected as this epic's action, and as premature on 2026-11-04 — ⚠️ the owner has since ANSWERED V1 in this direction (2026-11-07: the index moves to Postgres), so what is rejected here is the EPIC taking the move, not the location; the implementation is `#4051`'s. The facts below are worse for the move than the epic assumed.** Verified: **no `pgvector` anywhere** (`grep -E 'pgvector\|vector(\|hnsw\|create extension' supabase/migrations/ tortoise/record_*.py` → zero); embeddings live in `embedding jsonb` on `point`/`source`/`event`; **`public.object`/`public.subject` have no embedding column and a CHECK forbidding one.** So branch 2 is build-from-nothing at the schema layer. ⚠️ **And the owner's earlier postponement of the hosted setup was SUPERSEDED, not ignored**: arming `TORTOISE_RECORD_DSN` was authorised on 2026-11-03T19:06Z, and `#9548` is **CLOSED COMPLETED (2026-11-06T01:57:25Z)** on its **write path** — the DSN **is armed** (`record.armed: true` in the `/health` payload, `fetched_at` 2026-11-05T15:07Z) but hosted writes were rejected `22P02` (`#10593`, **CLOSED COMPLETED 2026-11-06T19:51:42Z**) and no hosted session is evidenced as projected. (`BLOCKED-CRED` in that lane is the *backup* credential gating a MIGRATION, not the arming.) That is a **write-path/evidence wall**, not a scheduling signal — which makes it a *transient* blocker, and makes the sequencing argument (record before vectors) sharper, not softer. |
| **A4** | **Fold #5090 into `#10976` / `#7879`.** | **Rejected.** Three different claims: the cache's *egress*, the record's *authority*, the vector layer's *shape*. `#10976` names `#5090` as related, not parent; neither epic's acceptance covers *"what earns an embedding / where the index lives"*. |
| **A5** | **Reduce the epic to evidence + decision carriers, and let the neighbours dissolve the cost.** | **ADOPTED as the shape of the remainder.** If `#10000` removes raw turns from the graph and `#10976` bounds residency, a material share of the vector class leaves with them — so the epic's own job is the *shape* question, not the *volume* question. |

### 1.3 Anti-post-rationalisation — the strongest case NOT to proceed

- **The epic may be dissolved rather than solved.** Turns were ~80 % of a measured graph (`#10000`) and
  dominated the vector class. Ingress plus egress work could shrink the bill without the epic deciding
  anything.
- **The measured case for indexing is weak today.** ~6 ms, against four further resident HNSW
  structures on a store that charges provisioned RAM with no eviction. §12.1c already concludes that is
  not worth it.
- **Most of what is left is an owner decision or a document.** Filing issues where the work is already
  owned is motion, not progress.

**The counter-case, and why it wins:** two of the four DoD rows are PARTIAL on *the same population*
(the embedding writers with no recognised producer call — **15 when this was written** (2026-11-04);
**19 measured 2026-11-07**, of a 20-writer outside-gate population), `#10541` — the issue that was to resolve
them — closed **COMPLETED** on 2026-11-04T00:49Z and **both rows are still PARTIAL at this head**. That is
a genuine, currently-unfiled gap: an issue closed as complete whose own remainder outlived it. Proceeding
is justified **only** to the extent the work either (a) makes V1 answerable, or (b) closes a genuinely
unfiled gap. Both are in scope; nothing else is.

⚠️ **Corrected 2026-11-07 (`#11444`, measured): the "same population" premise no longer holds for row 1.**
Row 1's scorer reads `tortoise/embed_writer_register.py`, so all 20 writers discovery finds outside the gate
are **credited** (13 `exempt`, 7 `recognised_producer`, owner issues cited per entry) and row 1 is `PARTIAL`
on the shipped gate default alone. Row 2 is still `PARTIAL` on its own unresolved-label population. The
argument above stands on row 2's half; it must not be quoted as "both rows hold on the writer population".

---

## 2. Eisenhower matrix

|  | Urgent | Not Urgent |
|---|---|---|
| **Important** | `#10976` (P0 egress), `#10000` (P0 ingress) — both `P0-blocker`, both launch-gating | **← #5090 sits here: SCHEDULE** |
| **Not Important** | — | — |

**Placement: Important / Not Urgent → Schedule.**

⚠️ **`#10976` and `#10000` sit in Important/Urgent, not here:** both are `P0-blocker` and launch-gating, and importance is read off launch impact, not off ownership (*"other owners"*) — ownership is not one of the two axes. A `P0-blocker` that
gates launch is **Important** by definition. They are also *more* urgent than this epic, which is part of
why this epic does not hold the critical path.

- **Important.** 48 % of the graph is index; the vector class is the dominant line on a provisioned-RAM
  bill at $73/GiB-month with no eviction; and the decision gets structurally more expensive once a
  customer's data has landed (the migration window closes permanently).
- **Not urgent — on one leg, not two.** Nothing gates launch on it (§2's own grid: the two P0s do, and
  they are elsewhere). The earlier *second* leg — *"the owner postponed arming, a deliberate scheduling
  signal"* — is **withdrawn**: the owner authorised arming on 2026-11-03 and the block is a
  write-path/evidence wall (`22P02`, `#10593`; no hosted projection evidenced) at that reading — `#10593` closed COMPLETED 2026-11-06 and the hosted projection is still unevidenced. That weakens "Not Urgent" and is stated rather than quietly dropped.
- **The one genuinely urgent item is cheap and is not the epic's build:** V1's evidence, which is **`#4051`'s now, not parked** (`03-scope.md` §6 row 2) — the location is decided (2026-11-07) and `SEARCH-ARCHITECTURE.md` §9 **M1** consumes the measurement, so what unblocks is the *implementation*, not the decision. That work touches no production store.

---

## 3. Profit-growth alignment

**Causal chain — recorded so it can be REFUTED, which it is.** The chain the epic assumed runs: vector
bytes sit in RAM-priced, no-eviction storage at `billing.cost_basis.falkordb_per_gb = 73.0`
(`product/pricing.json`) → every stored vector is **permanent** rent → the per-tenant storage allowance is
consumed by data that is either identifier-only (no retrieval value) or index overhead (48 % of the graph)
→ the free/pro working set is artificially small → a customer reaches the allowance before the product has
earned the price.

**⛔ Every arrow in that chain is broken on our stack, and §16.1 says so:** the bill is **provisioned** RAM,
so removing a byte changes it by **$0.00**; and under the 2026-10-08 billing ruling retained bytes are
**REVENUE**, so "store less" is not a cost strategy at all. The table below is kept **because the refutation
is the finding**, not because the chain is the justification.

**Order of magnitude — computed from the REMOVABLE figure, per tenant, not from the whole graph.**

| quantity | value | basis |
|---|---|---|
| removable vector payload today — ⚠️ **REFUSED for `:Object`/`:Subject` (2026-11-10); this is the pre-ruling figure, not a licence** | **19.4 MB** | 12,657 × 1,536 B (the epic's own 2026-10-08 correction) |
| ⇒ rent avoided | **$0.00** | the bill is **provisioned** RAM, so neither the payload nor the index changes it (§16.1, HIGH) |
| at 10 tenants of this size | **still $0.00** | the tier is a machine size, not a data size |
| to reach **$1,000/month** | **≈ 14 GB** of unindexed payload (the pre-ruling *"removable"* framing; ⚠️ REFUSED for `:Object`/`:Subject`, 2026-11-10) | ~730× today's payload |

⚠️ **This corrects two inflated figures in an earlier draft, and the correction matters.** That draft said
*"tens of dollars per month today"* — which is the whole 195 MB graph at $73/GiB (**$13.90**), including
the 93 MB of indices this epic explicitly defers to V1. And it said *"$100s–$1000s/month at ten or more
tenants"* — which does not reach $1000s: ten tenants is ~$140, and the storage doc's month-6 projection
(300–500k vectors × 2.05 KB = 0.6–1.0 GB) is **$45–75/month**, not thousands.

**⇒ The honest statement: this epic, as scoped, is worth about a dollar a month per tenant today, and
low tens of dollars a month at the sizes the storage doc projects for month six.** Its value is
**structural, not immediate**: it decides where a class that is 48 % of the graph lives, before customers
make that decision irreversible. It must **not** be sold as the volume fix — that is `#10000` and
`#10976`, and they are both larger.

**Faster/better path to the same profit outcome?** For the *bill*, yes: `#10000` and `#10976` remove or
bound bytes without deciding anything, and cost less than a store migration. For the *decision*, no —
there is no cheaper way to stop paying rent on four unindexed labels than to decide where they live.

---

## 4. Key assumptions

| # | assumption | confidence | what falsifies it |
|---|---|---|---|
| K1 | Vectors are stored in the graph and unindexed for four labels at HEAD | **high** — `ENTITY_TYPE_LABELS` = 5 served labels, `Point` indexed, `Point`-only DDL | a vector index appears for a non-`Point` label |
| K2 | The rule exists and is off by default | **high** — object identity, and a 4-surface config walk that finds it unset | `TORTOISE_EMBED_GATE` set in a shipped surface, or `embed_gate_enabled()` true without it |
| K3 | A cross-store vector move preserves recall on **our** query mix | **low — unmeasured, and it is the decision's hinge.** A comparable graph+`pgvector` shape measured recall@10 **0.63–0.72** after the same move; `pgvector`'s filtered ANN (`hnsw.iterative_scan`) is a capability we do not have at all today | **V1's evidence — now `#4051`'s** (`SEARCH-ARCHITECTURE.md` §9 **M1**; `03-scope.md` §6 row 2; `02-research.md` carries the numbers). V1 is answered (2026-11-07), so this is the implementation's measurement, not this epic's work |
| K4 | The record can serve reads without the graph, so a vector move is safe | **medium.** 4 of 4 layers exist in schema and `record_*` modules exist; the record's DSN **is armed** (`record.armed: true` at 2026-11-05T15:07Z) and hosted writes were being rejected `22P02` at that reading (`#10593` — **CLOSED COMPLETED 2026-11-06T19:51:42Z**, by #10620 narrowing `point.search_keys` to `text`), so no hosted session is evidenced as projected | the record serving a query with the graph stopped |
| K5 | Arming the EMBED gate moves the write rate materially | **medium — unmeasured.** `#5008` ordered measurement first, deliberately | the rate measurement in scope item 1 |
| K6 | Object/Subject embeddings absent from the record are a *re-embed cost*, not data loss | **high** — an embedding is derivable from its text | a stored vector whose source text is itself gone |
| K7 | The two `PARTIAL` DoD rows can be closed without a new mechanism | **medium** — `#10541` closed COMPLETED with both rows still PARTIAL, so the remainder is a *reading* decision plus arming, not new code | the arming measurement showing the rate unchanged |

---

## 5. Decision

**Feature:** epic #5090 — the vector layer (what earns an embedding, and where it lives)

**Decision: REPOINT — the epic stays open with its acceptance reduced to the remainder in §7. It is not discharged, and it is not re-scoped upward.**

**Alternatives considered:** discharge as complete (A1 — rejected on `§14.3 V3 "Unblocked by V1 — ✅ the DECISION is answered, ⚠️ the WORK remains (2026-11-07)"` alone: the
epic is the only *actionable* record of the deferral — the doc row and the runtime warning state it but cannot be closed or reassigned — so discharging it converts a *deferred* defect
into an *accepted* one); index the four labels in the graph (A2 — rejected: §14.3 **V3**'s content, four further
resident HNSW structures, ~6 ms); move vectors to Postgres now (A3 — rejected for THIS epic: the location is answered (2026-11-07), and the move was infeasible when this row was written — no `pgvector` existed, the record
forbids `object`/`subject` embeddings, and the record was armed but its write path was rejecting `22P02` (`#10593`, since CLOSED COMPLETED 2026-11-06); the implementation is `#4051`'s); fold into a neighbour
(A4 — rejected: three different claims); let the neighbours dissolve the cost (A5 — adopted as the shape
of the remainder).

**Profit impact:** **none directly, and that is the finding.** The bill is on provisioned RAM (§16.1,
HIGH) and the 2026-10-08 billing ruling makes **retained bytes revenue** — so no byte-removal on this epic's
surface moves the bill or the price. What this epic is worth is **structural**: it makes the embedding rule
operative, makes the writer population's exemptions a property of the product, makes the store state its own
vector contract, and removes an inconsistency between three artifacts about a decision still open. §3's table
above is the arithmetic that **refutes** the cost story, kept because the refutation is the finding.
**Not the volume fix** (`#10000`/`#10976` are, and they are larger).

**Eisenhower placement:** Important / Not Urgent → **Schedule**. Nothing gates launch on it; the decision
gets more expensive once customer data lands; the only cheap-and-urgent item is the measurement. Its
"not urgent" leg rests on launch-gating, **not** on the owner postponing anything — that was wrong and is
withdrawn.

**Key assumptions:** K1 high · K2 high · **K3 low — the hinge** · K4 medium · K5 medium · K6 high · K7
medium.

**Recommendation:** proceed, but only to work that is either **genuinely unfiled** (the recall
measurement; the two mandated workflow artifacts; the writer-population remainder that outlived
`#10541`; the enforcement default) or **the owner's decision carrier** (V1). File nothing that duplicates
`#5008`, `#4997`, `#4333`, `#10465`, `#10541`, `#8419`, `#10976`, `#10000`, `#7879`.

**Routing:** PROCEED → `epic-research` (stage 2), then `epic-scope` (stage 3).

---

## 6. Review gate

**Cycle 1** — fresh-context reviewer, `epic-align` prompt + 8 independent fact-checks with commands.
Result: **2 P0, 4 P1, 3 P2, 1 P3**. All incorporated:

| sev | finding | disposition |
|---|---|---|
| **P0** | `#9548` was described as *"postponed by the owner"*; the owner **authorised** arming 2026-11-03T19:06Z, the DSN **is armed** (`record.armed: true`, 2026-11-05T15:07Z), and it was OPEN on the **write path** (`22P02`, `#10593` — **CLOSED COMPLETED 2026-11-06T19:51:42Z**) plus the unevidenced hosted projection — not on a credential wall | corrected in §1.2 A3, §2, §5, K4 |
| **P0** | *"the warning fires on every search"* is false — setup-time, once per (process, graph); query fallback logs at info/debug | §0 row 1; K1 restated |
| P1 | *"3 of 4 DoD bullets met"* is false; the probe returned **1 MET of 4** at the time of that review (**2 of 4 at this head**) | §0 row 3, §1.1, §5 |
| P1 | DoD **row 2's** remainder omitted; `#10541` closed COMPLETED with both rows still PARTIAL | §1.3, K7, §5 routing |
| P1 | `#10976`/`#10000` (both `P0-blocker`) placed in *Not Important* | §2 grid + explicit correction |
| P1 | A1 rejected on three reasons that do not survive checking | §1.2 A1 — replaced with the `§14.3 V3` reason |
| P2 | profit inflated in the doc's own units ($13.90 from the whole graph; "$1000s" unreachable) | §3 recomputed from the removable figure per tenant |
| P2 | *"three further HNSW structures"* vs *"four unindexed labels"* | §1.1 — **four**; §12.1c's own "second…third" flagged |
| P2 | `§14.3`'s V1 row still carries the retracted ≈50 MB / 141 MB / 45 MB pair | **delivered in this lane — see §7**, together with §12.1c's *"SECOND … THIRD"* label count (a register correction, not a decision change) |
| P3 | *"6 shipped config surfaces"* — two are absent from the tree | §1.1 — *4 that exist, 6 walked* |

**Cycle 2** — fresh-context reviewer, corrections-only pass. Result: **2 P1 + 1 P3.** Incorporated:

| sev | finding | disposition |
|---|---|---|
| **P1** | **A1's sole surviving reason is falsified** — *"the epic is the only artifact binding `#4997` to V1"*; the doc row at `STORAGE-ARCHITECTURE.md` §14.3 **V3** and the runtime warning in `_record_vector_index_inventory` (`projection/__init__.py`) both state it | A1 re-argued on *actionability*, and **the decision changed from PROCEED to REPOINT** (§5, §7) |
| **P1** | the `§14.3` register correction was *recorded as incorporated* while no artifact carried it — the named target, scope §3.2, does not exist | **delivered for real in §7**, and both files corrected |
| P3 | §6 cited *"§0-preamble"*, where `#9548` appears nowhere | the claim lives in §1.2 A3 and §2; the pointer now names them, and the §0 attribution is gone |

**Verified clean by the cycle-2 reviewer:** the `#9548` authorisation wording; the setup-time warning
characterisation; `1 of 4` MET (**2 of 4 at this head**); `#10541` CLOSED/COMPLETED with both rows still PARTIAL; both `P0-blocker`s
placed Important; `§14.3 V3` read *"Rides on V1"* (as of cycle 2; it now reads *"Unblocked by V1 — ✅ the DECISION is answered, ⚠️ the WORK remains (2026-11-07)"*); the recomputed profit arithmetic; **four** unindexed
labels; *4 surfaces exist / 6 walked*.

---

## 7. REPOINT — what this epic's acceptance becomes, and the register correction

### 7.1 Why REPOINT rather than PROCEED

PROCEED would have implied the epic's existing acceptance still describes its work. It does not: one of
four DoD rows is MET, one is an owner decision, and **both PARTIAL rows hold on the embedding writers
with no recognised producer call** (§1.3 — 15 when this was written, 19 measured 2026-11-07) — row 1 additionally on the gate being `set ON in: none` on the
shipped default and one write path embedding outside it. ⚠️ **Corrected 2026-11-07 (`#11444`, measured):
the row-1 half of that sentence is superseded.** Row 1's scorer reads the product register, so the writer
population and the one ungated path are **credited** (#5008) and row 1 is `PARTIAL` on the shipped gate
default alone; row 2 is unchanged. The conclusion below is unaffected, and the decision it records is
what later made row 1's scoring legible — but do not quote the superseded clause as current.
**The instrument says re-classing that population
moves neither verdict** (the `#10541` census comment in `tools/dod_5090_probe.py`: *"the census CLASSIFIES the unresolved set;
it does not license moving this verdict … flipping this is a decision, not a cleanup"*), so rows 1–2 are
closed by the **re-score decision** S2 carries (`03-scope.md:64`; `#11085`) — not by arming the gate and
not by the register. ⚠️ **Corrected 2026-11-07 (`#11444`, measured):** that sentence is now true of row 2
ONLY. Row 1's scorer reads the product register, so **row 1 does move on a re-classing** — its hold is
`undeclared` (a writer the gate never sees and the product does not name), and row 1 reaches `MET` once
every outside-gate writer is gated or named while the shipped gate default is ON. Row 2 is unchanged: the
`#10541` census still classifies without licensing a verdict move there, because its question is which
LABELS are query-servable, not who wrote the vector. **REPOINT** is option **C** from `#7879`'s own
re-measurement comments — retire completed scope from the acceptance, keep the epic open on exactly what
remains, and let the owner close it when the decision lands. It is neither discharge (§1.2 A1) nor
re-scoping upward (inventing children the neighbours already own).

**Acceptance after repointing — the epic is done when, and only when:**

1. The embedding rule is operative on the shipped default, with the measured rate recorded (scope S1).
2. The exemption register has a production home, and DoD rows 1–2 are re-scored as a recorded decision (S2).
3. **The unretrievable vectors are censused, and the disposition of the two labels the ruling names is settled — `:Object`/`:Subject` removal is REFUSED** (owner, 2026-11-10, `#11395` comment `6781921075`), so **no surface-approval request follows for them**; ⚠️ **the refusals do NOT extend to `:Event`/`:Source`/`:Document` (3 of the 5 `entity_type` values in S3's population): the ruling names two labels and no others, and for the rest no recorded decision authorises a removal either, so S3's `MET` rests on the explicit per-label coverage, never on an inference that one ruling reaches five values**; and `#10976`/`#7879` are **reconciled** to the ruled wording at `S3-VECTOR-CENSUS.md` §6 — **that reconciliation is DONE, not owed** (both comments re-pointed 2026-11-10: `6779662659`, `6779663836`) — because **V1 was answered
2026-11-07** (the index moves to Postgres), so the target is the wording S3 §6 already carries — location decided, the work is `#4051`'s — not
"pending V1" (S3). **V1 itself is §14.3's and is NOT this epic's acceptance.**
4. The store reports its vector contract positively, not only when deficient (S4).
5. The test-design map and the capstone exist and pass (S5).

## 8. The prior record — what was already settled, and what this lane withdraws

**Added after the owner challenged the research depth. It was a fair challenge: this lane re-derived
things the record already held, and proposed six things it then withdrew — several of which the record had
already refused (§8.2).** Recorded in full
so the next lane does not repeat it.

### 8.1 Already settled — do not re-open

| ruling | date | home | what it settles |
|---|---|---|---|
| **D11 — stay on hosted FalkorDB; the optimisation is DEFERRED** | 2026-09-24 | **#4333** decision record | No engine migration. Self-hosting, if ever, is **priced first** — same engine, SSPLv1, no rewrite. ⚠️ **Owner, 2026-11-05: *"we're not self-hosting flakorDB [sic] now. we have a lot of work that needs doing first"*** (ruling relayed at [#5090 comment 6669523691](https://github.com/daniel-ospina/tortoise/issues/5090#issuecomment-6669523691), per the DECISION RELAY rule) ⇒ **self-hosting is OUT of the option set, not deferred-and-available.** |
| **The billing model** | 2026-10-08 | graph `1a11c902dac` | Tiers are **capped, priced per feature**, not sized by volume; over cap = **overage**, not upgrade. Two metered units: **storage per byte**, **compute per extraction**. ⇒ *"fix the billing unit"* is no longer a candidate. **Corollary: because we charge per stored byte, retained bytes are REVENUE — so "store less" is NOT a strategy.** |
| **The unit** | 2026-10-25 | **#4333** | Cost is reported in exactly three things, all bytes/usage: **graph RAM bytes** (rows + indices + **the vector index**), **Postgres bytes**, **extraction usage**. Owner, verbatim: *"i alreayd said no cost in nodes (we kepe the old design untilw e can replace it ... we're moving away form nodes instead of using the tokens to actually implement the design I already requested)"* — `alreayd`, `kepe`, `untilw` and `form` are the source's own spellings and the `...` is the source's own elision; all are kept and **named here**, outside the quotation, so the quoted bytes stay identical to the source — whereas a silently repaired typo inside quotation marks would not be. Where this epic instead marks a typo with an **inline** `[sic]`, that is the standard editorial marker and **not** a defect: this row forgoes it only because the ruling is restored in full, so every misspelling can be enumerated rather than annotated. |
| **The engine's ROLE** | 2026-10-08 | §14.2 | FalkorDB **stays and is DEMOTED** to a derived hot cache. **"Shall we migrate to Postgres?" is not an open question and a lane must not re-open it.** |
| **V1's own framing** | amended 2026-10-08 | §14.3 | ⚠️ **"The hybrid makes V1 more urgent, not less"** — as of 2026-10-08; §14.3 now carries it in the past tense (*"made V1 urgent"*) after the 2026-11-07 resolution — under the hybrid every cache byte is rent on something Postgres already stores. **V1 is a CACHE-TIER question, not a data-cost question.** |
| **V1 has a named measurement** | — | §15 **M5** | *"Where should the vector index live? — our query latency with the index resident, against a real `GRAPH.MEMORY USAGE` read"*. The latency half was measured 2026-09-24 (§12.1d). |
| **The two-store cost model exists** | — | **§17.4** | Closed form: `multiplier = 1/(f + (1+ρ)/778.7)` → **0.99× at `f`=1.00, 4.83× at `f`=0.20**. §17.4.5: the split is **`f`-conditional** and **net-negative today**, because §2.0's *"keep the limit in falkor high"* **is `f = 1`**. |
| **`f` cannot be measured today** | 2026-10-17 | §17.4.6 (M8) | It needs a window over a tenant's real query mix; **hosted capture is off by owner ruling 2026-10-14** and no self-hosted tenant serves a workload. The instrument exists (`tortoise/read_ledger.py`); the reading does not. ⚠️ **It is not an input to the 2026-11-07 V1 ruling** (`SEARCH-ARCHITECTURE.md` §1), so it does not reopen V1. |
| **Per-graph offload is Enterprise-only** | 2026-09-24 + confirmed 2026-11-05 | #4333 (D11 record) | `falkordbe.idle-threshold-ms` (**default `0` = disabled**), `evict-interval-ms`, `/data/offload`, `GRAPH.LOAD` are documented **only** under Enterprise. ⚠️ **New in this lane:** Enterprise is offered as **BYOA inside our own AWS account**, so §16.1's *"(self-managed)"* is narrower than the vendor's offering — but **#4333 owns pricing it**, and it is **not this epic's**. |
| **Eviction cannot be a server policy** | 2026-11 | **#10976** (OPEN — `#11004` is its merged census PR) | Measured: `maxmemory-policy allkeys-lru` at a ceiling below resident size evicted **a whole graph — 20,000 → 0, silently**; `GRAPH.LIST` still named it. Every key had `ttl=-1`. That is why `noeviction` is pinned and why the sweep **refuses** until the record can serve. |
| **Who owns the storage architecture** | 2026-09-23 (re-scope) | **#4333** | Re-scoped by the owner from *investigate* to **own the storage architecture**. The changelog records as a **correction of a wrong owner**: *"`#5090` is a **consumer** of the vector layer, not the enumeration"* — §17.5's placement enumeration is **#7955**'s, §17.1's routing half is **#7951**'s. |

### 8.2 What this lane withdraws

| withdrawn | why | the record that refutes it |
|---|---|---|
| **"Move the vectors to Postgres to save money"** | The bill is on **provisioned RAM**, and retained bytes are **revenue**. Removing bytes changes the bill by **$0.00**. | §16.1 (HIGH: *"cost is a function of the machine, not the data"*) · graph `1a118eb5b0a` · the 2026-10-08 billing ruling |
| **"Compress the embeddings, 8–32× smaller"** | **Already refused.** Quantization is explicitly listed as **not worth measuring — *"no engine to apply it to"***. FalkorDB's stored form is `vecf32`; there is no quantization surface. | **§15**, *"Not worth measuring (recorded so they are not re-invented)"* |
| **"Delete the unindexed vectors and re-measure the tier" — presented as new** | It is the **recorded** next move, with its own falsifier, and it was already an option on the cost decision. Presenting it as a finding was re-derivation. | graph option `1a11923050e` · the FIFTH OPTION point `1a11917857c` · the PRESCRIBED FIRST MOVE `1a118eba7b0` |
| **"There is no two-store cost model"** (implied) | §17.4 **is** that model, with measured inputs and a closed form — and it gives a sharper reason not to move than anything this lane offered. | **§17.4–§17.4.6** |
| **"Self-hosting is a live cost lever"** | **Owner, 2026-11-05: not now.** It stays recorded as D11's future option; it is **not** in this epic's option set. | D11 + owner ruling 2026-11-05 |
| **The `$1.32/month removable-payload` profit chain** | **The wrong kind of number twice over**: retained bytes are revenue (billing ruling), and the bill is provisioned RAM (V1 cost refutation). The arithmetic was corrected and the *model* was still wrong. | the 2026-10-08 billing ruling · §16.1 |

### 8.3 What survives, and it is why the decision stands

The epic's remainder is **not** a storage decision — that is `#4333`'s, and V1's home is **§14.3**. What
survives is what nobody else owns:

1. **The embedding rule is written and switched off** — a real defect, in nobody else's scope.
2. **The exemption authority lives only in a read-only measurement script** — three write paths have no
declared exemption in the product.
3. **Four of five served labels have no vector index and no efficient query path** — the 2026-09-24 reading counted the 12,657 stored vectors on `Object` (7,859) and `Event` (4,798), which a full scan serves today, and none on `Source`/`Subject` — though BOTH of those labels' writers do store `embedding` (`_upsert_document` → `:Source`, D10; `_upsert_subject`, `#7845`), so the live population is what `--census` measures, never what the index inventory implies (§12.1b) — and the *removal* of the
   unindexed ones is **not authorised by any recorded decision**: the 2026-09-24 direction points the other
   way ("keep the vectors, make them searchable"), and the 2026-10-08 billing ruling removes the cost
   argument rather than granting one (retained bytes are revenue; removing bytes moves the bill by $0.00).
   ⚠️ This is a **contradiction-test result**, not an assumption — see scope §1: the census is filed, the
   decision **was** the owner's and has been given for the two labels it names (**REFUSED**, 2026-11-10, `#11395` comment `6781921075`), so those are not removed and the surface rule gates nothing for them; for `:Event`/`:Source`/`:Document` no recorded decision authorises a removal either, and the 2026-09-24 direction stands.
4. **The epic has never had its two mandated workflow artifacts.**

**Decision unchanged: REPOINT.** The repointed acceptance is in §7.1, with S3 re-scoped per the above.

Two recorded numbers had gone stale in the decision register, and a stale register is read as an
instruction rather than as history — a failure class this repo has already been burned by. Neither
correction changes a decision; **V1 has since been answered (2026-11-07): the vector index moves to Postgres — see §7.1 item 3.** Delivered in this lane:

- `docs/architecture/STORAGE-ARCHITECTURE.md` **§12.1c** — *"a SECOND … and a THIRD"* → **four**, because
the served set is `{Event, Object, Point, Source, Subject}` and only `Point` is indexed.
- `docs/architecture/STORAGE-ARCHITECTURE.md` **§14.3 V1 row** — *"≈50 MB of stored vectors + a 45 MB
  index total, on 141 MB"* → **a 93 MB index total on a 195 MB graph** (measured 2026-11-04, graph
  `live-v4`, `#10976`) **+ the payload axis as the 2026-09-24 measurement it belongs to: 33,580
  embeddings ≈ 51.6 MB, of which the unindexed `Object`+`Event` 12,657 ≈ 19.4 MB was the removable
  subset.** The ≈50 MB was carried as a current figure long after it was dated, and this epic's own
  2026-10-08 comment had already narrowed what removal could claim to the 19.4 MB — ⚠️ **and the 2026-11-10 ruling then refused removal for `:Object`/`:Subject` entirely, so the figure is history, not a licence.**
