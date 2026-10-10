---
title: "Integration surface map — the Postgres record (epic #7879)"
type: test-design
domain: engineering
doc_status: draft
created: 2026-10-10
updated: 2026-10-10
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #7879, test-design #7967, integration surface map, Postgres record, two-store split"
---

# Integration surface map — epic #7879, the Postgres record

**Gate artifact:** `test-design` (Scope → Plan). Issue **#7967**. **Lane:** `c6-durability`.
**Epic:** **#7879** — *the Postgres record — the four ontology layers as an authoritative relational store.*
**Ruling:** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) — **Postgres is the system of record; FalkorDB is a derived hot cache.** §17's five gaps are its consequences, not new decisions.
**Scope ruling:** owner, 2026-10-10 — **there are no existing customers.** The migration is for **architectural correctness, not migration safety**; no dual-write/backfill/cutover surface appears below, and none should be added.
**Plan:** the interface map + dependency order + decomposition are posted on **#7879**.

> ⚠️ **This map is an intermediate artifact.** It feeds the epic's plan and every child issue's verification checklist. It is not a plan, and it approves no scope.

**Measured at:** `main@eed2be81472276b38d2b34e59298427be65a47cf` on 2026-10-10. Every quantity below carries its method and date — the write surface moved `387 → 409 → 420` in three days, and an unlabelled denominator is how the withdrawn `5.6×` happened (§1, §17.5). *(The first reading was taken at `1e5fc00ca5`; the branch was rebased onto `eed2be8147` and every figure re-measured there. The two trees differ only in `AGENTS.md`, and the readings are identical.)*

---

## 0. Read this first — there are TWO things called `S`

The map's surfaces and the capstone's acceptance criteria are **different namespaces that share the letter `S` and overlap in number**. Both are referenced verbatim across the child issues — **nine of them cite a surface** — so **neither namespace can be renumbered without breaking nine citations**. This crosswalk is the fix.

| capstone `#7950` criterion | `#7967` surface(s) | note |
|---|---|---|
| **S1** every write goes to the truth store first | **S2** | the chokepoint — capstone S1's *"fails if"* is surface S2's first failure mode |
| **S2** truth↔cache divergence is OBSERVED | **S3** + the reconciliation half of `#7952` | reconciliation is the **evidence** for capstone S2; the thing it observes is surface S3 |
| **S3** ⭐ the completeness marker works | **S4** | ⚠️ **surface S3 is NOT the marker** — the one collision that misfires most often |
| **S4** a residency miss is not an insufficiency | **S5** | ⚠️ **surface S4 is the marker, not the router** |
| **S5** derived/volatile fields are not in the record | **placement (W2, `#7955`) on S1 and S3** | the placement table is the criterion's evidence; it is **not a surface row of its own** |
| **S6** a canonical id exists | **S6** | same number, same subject — the only collision that is benign |

**Surface ids `S1`–`S9` and their scope are frozen by this document.** A child issue citing `S<n>` means a surface, never a capstone criterion.

---

## 1–2. The surfaces and their layers

The surfaces are **not the code components — they are the STORE boundaries.** That is the whole difference this epic introduces, and it is why the map is written as *truth → derived* transitions rather than as modules.

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---|---|---|---|---|---|
| **S1** | **journal → Postgres** (the record projection) | DB write / projection | In (journal → Postgres) | **Integration** (replay a real journal, assert equality) + **pgTAP** (the SQL mutations) | `derived = replay(journal)`; every event type has a defined mutation; **total** — an unhandled event is a failure, not a skip; **idempotent** — a second replay produces the same record; a field is PRIMARY **iff the journal carries it** | ⚠️ a field the journal carries is not projected ⇒ the record is **silently poorer than the history**; a non-idempotent projection ⇒ a second replay **double-applies**; an unknown event type **skipped** ⇒ totality lost in silence |
| **S2** | **the write chokepoint** (every door: SDK · EventAPI · extractor · indexer · hosted) | State (write path) + Concurrent | Both (door → truth → cache) | **Integration + e2e** (a write through EACH door lands journal→Postgres→cache **in that order**; **a direct cache write FAILS**) + **contract** on the entry API | one entry: append the journal event → apply the record projection → apply/enqueue the cache projection; retry semantics are `retryable_aborted_write` (retry **only** on a replaced/deleted handle, same handle reused, deterministic errors **never** retried, **original exception type preserved**); a bypass **fails closed** | ⚠️ a bypass ⇒ *"a derived cache that is also written to directly is not derived"*; a partial write ⇒ **cache ahead of truth**; an unjournaled write class ⇒ an **unrecoverable hole** in the record; a **delete-resurrection** class ⇒ replay restores deleted data; a **hidden door** (`proj.apply` called directly by `connectors/github.py` / `indexer/github_indexer.py`) ⇒ the chokepoint does not intercept |
| **S3** | **Postgres → FalkorDB** (the cache projection) | DB write / projection | **Out only** (record → cache, one way) | **Integration** (project a record, assert the cache) + **contract** (the cache is never authoritative) | the hot set is **DECLARED** (the placement table), not implicit; **one-way by construction** — nothing on this path may read the cache to decide what to project; eviction by **relocation, not flag** (§10.1); **no field exists in the cache without a PRIMARY claim in the record** | ⚠️ the cache drifts ahead of the record; a cache-only field is mistaken for record truth; **a second writer reappears** wherever the chokepoint (S2) is absent; a rebuild that can only read the **journal** rather than the **record** ⇒ the ruling's second half has no carrier |
| **S4** | **the completeness marker** | State / read-path contract | Out (a read's verdict) | **Unit + contract + e2e** | a read for a node **outside the known-hot set** returns an explicit **`not-hot`**, **never** an empty result; the marker is visible on a **customer-callable** surface, not only internally | ⚠️ **an empty result returned where `not-hot` is required — indistinguishable from "no such node"** (capstone **S3**; the owner hits this as the first customer); a marker implemented but never exposed on a callable surface ⇒ it cannot fire |
| **S5** | **the residency router** | State (routing decision) | In (a cache read) → Out (fetch-from-truth \| escalate) | **Unit + integration** | *"not in the cache"* ⇒ **fetch from the record**; *"the evidence does not answer this"* ⇒ **escalate a tier** (§12.4). **The two must not collapse.** | ⚠️ a cache miss treated as a **negative finding**; an unnecessary tier escalation; **a cache that is DOWN routed as "no evidence"** rather than as a residency miss; `not-hot` colliding with `[]` / `None` / `{}` at the seam |
| **S6** | **identity** (canonical id + stable-name replay keys) | State (identity) / DB schema | Both | **pgTAP** (PK/FK) + **unit** (the accessor) + **integration** (both stores resolve the same node) | one canonical id agreed by journal, record and cache; a rebuilt edge binds its sibling by **STABLE NAME** — `stub_key` + `STRUCTURAL_REL_LABELS` (`projection/edges.py`), whose keys are `url` / `name` / `coalesce(title, name)` — **never `target.id`** | ⚠️ two stores disagreeing on a node's identity; a rebuilt edge binding a sibling by a **graph-resident id** ⇒ it resolves to **NOTHING, in silence** |
| **S7** | **tenancy / RLS** | Auth boundary + DB | Guard (blocks access) | **pgTAP** (policies) + **integration** (a cross-tenant read returns nothing) | `team_id` on every row; RLS is the boundary; **one database, tenant-scoped rows** (§4) | ⚠️ a record read crossing a tenant boundary; **a new data-plane table shipped without RLS** — the control plane's own coverage is not complete today (measured below), and the data plane must not inherit the gap |
| **S8** | **the existing control plane** (`teams` · `api_keys` · `metering_*` · `oauth_*` · `audit_events` · `graphs` · `connectors` · `invitations`) | regression **only** | unchanged | **Integration** | **unchanged — no new surface here; do not extend it.** ⚠️ **one exception, and it is a seam rather than a surface:** `public.graphs` is the single control-plane table with a **data-plane role** — it is where the **known-hot set is declared** (§2.6 of the plan). It belongs to **S3/S7**, not to S8 | ⚠️ the data-plane work breaking a control-plane surface — the RLS helper, the metering path, or the `graphs` registry that S3's marker reads |
| **S9** | **the graph engine STOPPED** | e2e durability | — | **e2e** | with FalkorDB destroyed, the record answers, **and the cache is fully restorable from it** (`rebuild == live`, `#5285`, with the record as the source) | ⚠️ with FalkorDB destroyed the record cannot answer ⇒ **the epic's central claim is false**; the cache restorable only from the **journal** ⇒ `#7971`'s carrier is missing and the ruling's second half is unbuilt |

### Identity of each surface, measured rather than asserted

| surface | what was checked | method | result |
|---|---|---|---|
| **S2** | the write surface has no chokepoint | paren-balanced scan of every `.query(` argument under `tortoise/**/*.py`, first mutating Cypher verb in the argument | **420 sites · 34 files · 231 distinct enclosing (file, class, function) scopes** — `SET` 174 · `MERGE` 117 · `CREATE` 52 · `DETACH DELETE` 38 · plain `DELETE` 28 · `REMOVE` 11 · of which **2 callers** route through `_graph_write_with_retry` |
| **S3** | the projection machinery exists and is reachable | `projection/__init__.py` (`apply`, `rebuild_all`, `_ensure_indexes`) | present — the unit changes its **source** (journal → record), it does not build a fold from nothing |
| **S6** | the replay keys exist | `projection/edges.py` | `STRUCTURAL_REL_LABELS` at `:51`, `stub_key` at `:66` — the interface `#5086` calls *"one interface that must not be broken"* |
| **S7** | today's control-plane tenancy coverage | `CREATE TABLE` / `ENABLE ROW LEVEL SECURITY` / `CREATE POLICY` over `supabase/migrations/*.sql` | **30 `CREATE TABLE` statements · 28 distinct tables · 51 files**; **0 table names match the ontology nouns**; 23 of 28 tables have RLS enabled; 47 policies |
| **S8** | the control plane is control plane | noun scan of the 28 table names and their columns | **no table name** carries an ontology noun. One **column** is named `source`. ⚠️ A **raw text grep** over `supabase/migrations/*.sql` returns **80** hits — every one of them inside a control-plane identifier (`webhook_events`, `audit_events`, `analytics_events`, the column `source`). **The epic's claim "a scan for the ontology nouns returns zero matches" is true of identifiers, not of text**; quote it that way. **`graphs` holds no `Point`** |
| **S9** | the retry helper the chokepoint can reuse | `grep -n _graph_write_with_retry tortoise/` | `sdk.py:4598` (definition) with **exactly 2 callers** (`sdk.py:5768`, `sdk.py:14750`); `retry.py:14` marks it **PARTIALLY WIRED (#7405)** |

⚠️ **Not re-measured, and carried with their own method and date:** the **`1,074` Cypher / `57` files / `697` anchored + `377` whole-graph** read-surface split on `#7879` and `#7970` (**method unpublished** — a re-derivation attempt over string literals returns a different population, `1,525` literals across `89` files, so the two are **not the same unit** and must not be compared or summed), the **`9 of 87` property names** placement sample and the **`8,700` + `1,000` + ~`30`** id formats (`#7955`, measured 2026-10-08 against a **live** graph; no live graph is reachable from this lane). **Re-measure both before any unit is sized against them.**

### Provenance corrections found by re-measuring, 2026-10-10

The issue body's quantities are **not wrong — they are readings of a moving surface taken on earlier days**, and two of them are **lower bounds**. Recorded here so a unit is never sized against the stale reading.

| claim as filed | re-measured at `eed2be8147` | what changed |
|---|---|---|
| `387` mutating sites (2026-10-08, §17.1) | **420** | drift **plus** a method gap: neither earlier pass counted `REMOVE`, which is a mutating Cypher verb (`15` sites contain it; `11` have it first). The earlier figures are **lower bounds** |
| `409` mutating sites (2026-10-10, plan §1) | **420** | same method gap — the plan's own five-verb tally reproduces here exactly (`174+117+52+38+28 = 409`), so the only delta is `REMOVE` |
| `_graph_write_with_retry` at `sdk.py:4544` (`#7879` body) | **`sdk.py:4598`** | line drift; the plan already corrected this to `:4598`. **Second caller is `sdk.py:14750`, not `:14706`** — the symbol is authoritative, the number is a convenience (the architecture doc's own revision-pin rule) |
| "All **24** `CREATE TABLE`s across `supabase/migrations/`" (`#7879` body) | **30 statements · 28 distinct tables · 51 files** | the count was of a subset; the conclusion (**no ontology noun in any table name**) is unaffected and was re-verified |
| "A scan for the ontology nouns … returns **zero matches**" | **zero in table names; 80 in migration text** | ⚠️ **the claim is true of identifiers, not of text.** Quote it as *"no table or table name carries an ontology noun"* or the next reader's grep returns 80 and the finding looks false |
| the unit table's `W4` / `W5` / `W8` rows carry **no issue number**, and no row exists for the cache projection | **`#7968` / `#7969` / `#7970` / `#7971`** | the table predates the decomposition that created them — see §5 |

---

## 3. The integration checklist, applied

### Contract and data shape

- ☑ **Contract defined?** Yes, per surface — above. The two that are still prose and must become callable shapes before implementation are the **chokepoint entry** (S2) and the **`not-hot` verdict** (S4).
- ☑ **Boundary values?** The read path's are not numeric — they are the **presence set**: `not-hot`, `[]`, `None`, `{}`, and a real answer. All five must be distinguished at S5's seam.
- ☑ **Empty vs null?** ⚠️ **This is the epic's load-bearing case, not a checklist item.** `[]` is a *wrong* answer where `not-hot` is required (S4).

**Failure modes** — the store split's failure modes are *semantic*, not transport. The transport set still applies where the cache is reached over a socket (S5):

- ☑ **Timeout / service down (503) / network error on the cache** ⇒ the router must classify it as a **residency miss**, never as an insufficiency and never as an absence. **This is a distinct fourth branch and it is not in §17.3.**
- ☑ **Rate limit (429) / bad request (400)** — not applicable to the journal and the record (in-process / one database); applicable to the cache projection's retry budget only.
- ☑ **Malformed response** — the record projection's analogue is an **event type with no mutation**; S1 makes it a hard failure rather than a skip.

### Data integrity

- ☑ **Atomic writes?** ⚠️ **Two different answers, and collapsing them is a defect.** The **journal append + the record projection** are one transaction (§3: *"One transaction covers the journal and its projection — no dual write between them"*). The **cache projection** is a second surface (§17.2) and **cannot** be in that transaction — so it must be **idempotent and replayable** instead. **The chokepoint's contract must say which half is transactional and which is idempotent.**
- ☑ **Idempotent?** S1's twice-replay assertion; S3's `rebuild == live`.
- ☑ **Concurrent access safe?** ⚠️ **The chokepoint IS the concurrency control** — today two writers can reach the cache, and S2 is the lock. S3's projection racing a direct write is the same defect from the other end.
- ☑ **Ordering guaranteed?** The order `journal → record → cache` must be **asserted**, not assumed; S2's per-door test is where.

---

## 4. Bug-pattern flags

| pattern | detected? | required verification |
|---|---|---|
| **SQL business logic** | ⚠️ **Yes, by construction** — the record's mutations are all new Postgres SQL | **pgTAP is required for every mutating statement the record introduces** (S1, S6). TS/Python mocks cannot verify SQL logic; this is non-negotiable |
| **Silent function skips** | ⚠️ **Yes** — S1's *"an event with no handler is a failure, not a skip"*, and S4's *"empty is not `not-hot`"* are the same pattern in two layers | assert the **failure**, not just the happy path; assert the **marker**, not just a non-empty answer |
| **Race conditions** | ⚠️ **Yes** — two writers on the cache today; the chokepoint and the projection are concurrent by design | S2's per-door test; S3's assertion that nothing on the projection path reads the cache |
| **Conditional guards** | ⚠️ **Yes** — S5's two routing branches and S2's fail-closed guard | boundary tests on **both sides** of every guard; a guard that cannot fail is not a guard |
| **N+1 queries** | ⚠️ **Flagged as a RISK for `#7970`, not for the write path** — replacing one Cypher traversal with N row fetches is a plausible regression | the read-path tests must **count** queries, not only assert answers |
| **Stale closures** | ☐ Not applicable — no React or async-closure surface crosses these boundaries | — |

---

## 5. The units — every child, its surfaces, its interface

**Eleven issues are parented to `#7879`.** Every one of them cites this map. The mapping below is the corrected one: the issue body's table predates `#7968` / `#7969` / `#7970` / `#7971` and mis-attributes **S3**.

| unit | issue | surfaces | interface (plan §2) | depends on |
|---|---|---|---|---|
| **W1** identity — canonical id + stable-name replay keys | **`#7955`** | **S6** (S1, S3 as consumers) | E | — (**first**; blocks W4, W5, W10) |
| **W1b** legacy-id normalisation (`8,700` + `1,000` + ~`30`) | *deliberately not a child* | S6 | E | W1 — **P1**, per the owner's launch criterion (normalising an id after clients have written rows **is** a client migration) |
| **W2** field-level placement table | **`#7955`** (second unit, one issue) | **S1, S3** (the table *is* the S1/S3 field contract) | §2.0/§2.2 | — |
| **W3a** chokepoint **measurement** | **ran** — result recorded on `#7951` | S2 | C | — |
| **W3b** chokepoint **build** | **`#7951`** | **S2** (+ S9 for the resurrection class) | C | W3a |
| **W4** schema — four layers + the EP-scalar carve-out | **`#7968`** | **S1, S6, S7** | A | W1, W2 |
| **W5** record projection — journal → Postgres | **`#7969`** | **S1, S9** | A | W1, W4 |
| **W6a** reconciliation — divergence as a number | **`#7952`** | **S2 (the evidence), S3 (the cache it observes)** | B | W4 |
| **W6b** the completeness marker | **`#7952`** | **S4** | B · D | W3b, W4 — trigger: **partiality** |
| **W7** residency router | **`#7953`** | **S4, S5** | D | W6b |
| **W8** read path — the anchored surface | **`#7970`** | **S4, S5, S9** | D | W1, W4, W6b — **deferred by §2.0; P2** |
| **W10** cache projection — record → hot | **`#7971`** | **S3, S9** | B | W5, W2, W3b |
| **W9** two-store cost model | **`#7954`** | **none** (arithmetic — verified by the table reproducing, not by a layer) | G | — (parallel; blocks nothing) |
| — `aboutObject` stored-vs-derived | **`#7956`** | **none** (informative; **not parented to `#7879`**) | — | — |
| **capstone** — S1–S6, the launch claim's evidence | **`#7950`** | **all** (terminal gate) | all | every unit above |

### Two corrections to this map, and where they came from

1. **S3's carrier is `#7971`, not `#7952`.** The issue body maps *"W6 reconciliation + marker (`#7952`) → S3, S4."* **`#7952` builds neither half of S3** — it is continuous reconciliation plus the marker. The MECE gate caught this and filed **`#7971`**, in its own words: *"the surface that must assert it was attributed to an issue that does not build it."* Corrected above: **`#7952` owns S4 and supplies the reconciliation evidence for S2; `#7971` owns S3.**
2. **The unit table must name `#7968` / `#7969` / `#7970` / `#7971`.** The body's table lists `W4`, `W5` and `W8` with **no issue**, and has **no cache-projection row at all** — the ruling's second half. It predates the decomposition that created them. **The cache-projection unit is `W10` here because the plan's table has no row for it**; the number is new, the work is not.

**Unparented-vs-listed, recorded not resolved:** the decomposition table on `#7879` lists `#7956` as a child but `#7956` is **not** parented to `#7879`, and it does **not** cite this map. `#7971` exists and *is* parented but is absent from that table. Neither is a surface defect — both are recorded so the next lane does not re-derive them.

---

## 6. Hard edges (the dependency order this map implies)

Four edges bind the units, and two of them are outside this lane's control:

1. **W1 before W4/W5** — no schema without the key both stores agree on.
2. **W2 before W4** — no columns before "what is derived".
3. **W6b before W7** — §17.3 states it; the marker is how the router can tell the two events apart at all.
4. **W3b before W5/W6 are meaningful** — until one path writes the cache, the cache is written directly and the split is unproven regardless of the schema.
5. *(outside)* **`#5089` → `#7879`** — a projection replayed from an unfaithful journal is **faithfully wrong**.
6. *(outside)* **`config/ci-surfaces.yml` is a serialization point, not a file** (`#5086`) — every PR edits it, so those PRs cannot merge concurrently.

---

## 7. Explicitly NOT in this map

- **No migration-safety surface** — dual-write reconciliation · production backfill · blue/green cutover · rollback parity · customer comms. **Owner scope ruling, 2026-10-10:** the only customer is the dogfood instance. **A child issue claiming one of these as a verification surface is a defect in that issue.**
- **No performance/throughput surface at this stage.** Latency is a real concern (§12.1d is measured) but it is not a *correctness* surface of this epic; **`#7954` owns the arithmetic.** ⚠️ The one exception is **N+1 counting on `#7970`** (§4) — a *correctness-adjacent* regression count, not a latency budget.
- **No new control-plane surface** (S8) — regression only. `public.graphs` is the seam, not an extension point.
- **No new MCP tool and no new public SDK method.** ⛔ If `#7970`'s read path or S4's marker requires one, that needs **Daniel's approval FIRST** — the surface is a contract and `tools/surface-guard.py` is drift control, not consent.

---

## 8. Checklist notes

- **The chokepoint's transaction boundary must be stated in its contract** (§3): journal + record in **one** transaction, cache projection **idempotent** and outside it. A contract that claims one transaction across all three is wrong, and §17.2 is why.
- **The cache-unavailable branch belongs in S5 and is new here.** §17.3 names two events; a cache that is *down* is a third, and routing it as *"no evidence"* is the same failure as routing a residency miss that way.
- **`not-hot` must be distinguishable from `[]`, `None` and `{}`** at every seam the read path crosses — that is what the marker exists for, and an enums-vs-empty check is the cheapest way to assert it.
- **The placement table is the S1/S3 field contract**, so it must land before the schema — it is not documentation of the schema, it is the input to it.

---

## 9. How to re-verify this map

```bash
# S2 — the write surface. No committed tool does this census, so the scan is inline and
# exact: a paren-balanced read of every `.query(` argument under tortoise/**/*.py, taking
# the FIRST mutating Cypher verb in the argument. Journal: 387 @2026-10-08 (no plain
# DELETE in its verb set) · 409 @2026-10-10 (REMOVE omitted) · 420 today. REMOVE is a
# mutating verb and is the whole of the 409 -> 420 delta.
python3 - <<'PY'
import re, pathlib, collections
MUT = re.compile(r"\b(MERGE|CREATE|SET|DETACH\s+DELETE|DELETE|REMOVE)\b", re.I)
sites, files, first = 0, set(), collections.Counter()
for f in pathlib.Path("tortoise").rglob("*.py"):
    t = f.read_text(errors="replace")
    for m in re.finditer(r"\.query\(", t):
        i, d = m.end(), 1
        while d:
            d += (t[i] == "(") - (t[i] == ")")
            i += 1
        v = MUT.search(t[m.end():i])
        if v:
            sites += 1; files.add(str(f))
            first[re.sub(r"\s+", " ", v.group(1)).upper()] += 1
print("sites:", sites, "files:", len(files))
print("first-verb:", dict(first.most_common()))
PY
# -> sites: 420 files: 34
# -> first-verb: {'SET': 174, 'MERGE': 117, 'CREATE': 52, 'DETACH DELETE': 38, 'DELETE': 28, 'REMOVE': 11}

# S2 — the hidden door: writes that reach the cache through `proj.apply` with no journal call.
grep -rn "proj\.apply" tortoise/connectors/github.py tortoise/indexer/github_indexer.py

# S6 — the replay keys and the accessor.
grep -n "STRUCTURAL_REL_LABELS\|def stub_key" tortoise/projection/edges.py

# S7/S8 — the control plane's tables, its RLS coverage, and its ontology-noun count.
grep -rhoiE "create table (if not exists )?" supabase/migrations/*.sql | wc -l            # -> 30 statements
grep -rhoiE "create table (if not exists )?[a-z_\.\"]+" supabase/migrations/*.sql \
  | sed -E 's/.*create table (if not exists )?//I' | tr -d '"' | sort -u | wc -l         # -> 28 distinct tables
grep -rhoiE "enable row level security" supabase/migrations/*.sql | wc -l                # -> 23
grep -rhoiE "create policy" supabase/migrations/*.sql | wc -l                            # -> 47
# raw TEXT grep -> 80 hits, ALL inside control-plane identifiers. Scan the TABLE NAMES, not the text.
grep -rniE "\b(point|source|event|object|subject|operator|edge|episode|narrative)s?\b" \
  supabase/migrations/*.sql | wc -l                                                     # -> 80 (not 0)

# S9 — the retry helper the chokepoint reuses, and its wiring state.
grep -rn "_graph_write_with_retry" tortoise/ ; sed -n '14p' tortoise/retry.py
```

**The two carried quantities have no reproducible command here** (`#7955`'s `9 of 87` property sample and `#7879`'s `1,074 / 697 / 377` read-surface split, both 2026-10-08, both live-graph or unpublished-method) — **they are the two to re-measure first**, because they are the only figures in this document that no reader can check.

---

**Status of this artifact:** the map is **complete** as a gate artifact — all nine surfaces have an assigned layer, a contract and their failure modes, and all eleven children are mapped. **It approves no scope**; scope and plan carry their own human gates on `#7879`.
