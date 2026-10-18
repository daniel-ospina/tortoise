---
title: "Capstone evidence — #7950 S1–S6, the two-store record/cache split"
type: evidence
domain: platform
doc_status: final
created: 2026-10-14
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#7950, epic #7879, STORAGE-ARCHITECTURE.md §17, Postgres record, FalkorDB cache"
---

# Capstone evidence — `#7950` (S1–S6)

**Measured at:** `cap/7950-capstone-proof` merged with `origin/main@1f83eaab8` (merge commit
`98ab7ae7b`) on 2026-10-14 (worktree dirty: the `docs/evidence/7950-capstone/**` files this run
adds plus `tests/test_7950_capstone_probe.py`; the product source under `tortoise/` is unmodified
at that commit). The end-to-end log and the S2 census are measured at this head.

**Lane:** private docker FalkorDB `fdb-lane-deba02cbc2` on `127.0.0.1:16436`
(`uv run python tools/test_lane.py uri`). **Postgres:** a throwaway `postgres:16` container
`cap7950-pg` on `127.0.0.1:15499` carrying only the RECORD migrations.

**Decision being proved (not re-litigated):** `docs/architecture/STORAGE-ARCHITECTURE.md` **§2.0**
— owner, 2026-10-08 — *Postgres is the system of record; FalkorDB is a derived hot cache*. The
property that makes it safe is **`derived = replay(journal)`**. **§17** is the five gaps.

> **Every quantity measured in this lane carries its denominator and its sample.** Where a figure
> is **carried** from another lane's measurement it is labelled, with its date and source, and is
> not re-measured here. FalkorDB silently caps at **10,000 rows**; where a figure is taken from a
> live graph read, that cap applies and is stated.

---

## Verdict summary

| # | criterion | verdict | deciding artifact | homed |
|---|---|---|---|---|
| **S1** | every write goes to the truth store first; the cache is projected from it | ⛔ **NOT-GREEN** | `s1-bypass-probe.log`: an **armed** journal-backed projection refuses `apply` but lets `proj.g.query` write the cache directly (0 journal records) | **`#7951`** (OPEN) |
| **S2** | truth↔cache divergence is OBSERVED, not assumed | ⛔ **NOT-GREEN** | `s2-wiring-census.log`: `reconcile` / `record_projection.replay` / `read_record_view` / `rebuild_from_record` all have **0 production call sites**; and `e2e-chain-probe.log`: a clean record-sourced rebuild is `field_mismatches=1` (the `vecf32` narrowing — `#8615`) | **`#7951`** + **`#7970`** + **`#8615`** (OPEN) |
| **S3** | ⭐ the completeness marker works | ✅ **GREEN** | `s3-s4-named-tests.log` + live bounded rebuild in `e2e-chain-probe.log` (`Point:p1` → `not-hot`) | `#7952` (CLOSED) |
| **S4** | a residency miss is not an insufficiency | ✅ **GREEN** | `s3-s4-unit-tests.log`: 30 router tests; residency outranks sufficiency | `#7953` (CLOSED) |
| **S5** | derived/volatile fields are NOT in the system of record | ✅ **GREEN** | `s5-s6-pglite-validate.log` (`20261012000001_record_schema.sql PASSED`, incl. the `subject.props.status` refusal) | `#8263` (CLOSED) |
| **S6** | a canonical id exists | ✅ **GREEN** *(contract)* | `s5-s6-unit-tests.log`: 8 identity-contract tests | `#7955` (CLOSED) |

**One verdict changed from the previous capstone comment** (measured at `77a0c32bd`, before
`4c801fb18` landed): **S5 flipped NOT-GREEN → GREEN** (fixed by migration
`20261014000001_record_subject_props_no_derived.sql`, `#8263` CLOSED). **S1's verdict did not
change** — it stayed NOT-GREEN after `#8389` merged, because that commit *deliberately* leaves the
raw-handle lane `proj.g.query` open; only its **blocker attribution** was corrected (the previous
comment named `#8497`, which is now CLOSED `NOT_PLANNED`).

**The epic stays OPEN.** Its own closing rule: *"Close only when S1–S6 all have evidence attached."*
S1 and S2 have not passed.

---

## The end-to-end run (this is what the epic asked for)

The previous comment recorded that *"the capstone's premise — run the two-store system on a real
workload — has never been executed."* **It has now been executed, with the shipped modules**, on a
real Postgres + a real FalkorDB:

```console
$ TORTOISE_DB_URI=<private lane> uv run --with psycopg2-binary \
    python docs/evidence/7950-capstone/probes/e2e_chain_probe.py
journal records read: 39
record projection: replayed 39 journal records → 60 record mutations (0 declared no-op)
record row counts: {'journal_event': 39, 'source': 2, 'event': 1, 'session': 0, 'object': 1, 'subject': 1, 'point': 2, 'relation': 2, 'node_identity': 7}
read_record_view -> 7 nodes, 2 relations
record view by label: {'Point': 2, 'Event': 1, 'Subject': 1, 'Object': 1, 'Source': 2}
cache projection report: {'nodes_written': 7, 'relations_written': 2, 'statements': 9, 'record_nodes': 7, 'record_relations': 2, 'coverage': 'complete', 'declared_keys': 0, 'source': 'record'}
cache node counts (live read): [('Event', 1), ('Meta', 1), ('Object', 1), ('Point', 2), ('Source', 2), ('Subject', 1)]
hot read for Point 'p1': [['p1']]
bounded rebuild declaration: coverage=partial
unwritten record key 'Point:p1' covered-by-declaration=False → residency=not-hot
RECONCILE (after a record-sourced rebuild): record vs cache: 1 divergent of 7 checked (missing_in_cache=0, extra_in_cache=0, field_mismatches=1)
ARMED FOLD (proj.apply):          refused (DirectCacheWriteRefused)
ARMED RAW HANDLE (proj.g.query):  SUCCEEDED (direct cache write)
RECONCILE (after a DIRECT raw-handle cache write): record vs cache: 2 divergent of 8 checked (missing_in_cache=0, extra_in_cache=1, field_mismatches=1)
second-writer signature -> extra_in_cache=1
ARMED RAW HANDLE MUTATION:        Point:p1 gained an unauthorized property (Point.second_writer_marker)
RECONCILE (after a DIRECT raw-handle property WRITE): record vs cache: 2 divergent of 8 checked (missing_in_cache=0, extra_in_cache=1, field_mismatches=2)
field-mutation signature -> field_mismatches=2
CHAIN RESULT: journal -> record -> cache COMPLETED with the shipped modules
```

**Verbatim from `e2e-chain-probe.log`, except the first line only:** the shipped live engine prints a
`#4997` advisory — *"vector search on `test_cap7950_e2e` is unindexed for label(s) ['Event',
'Object', 'Source', 'Subject']"* — which is present verbatim in the log and omitted from the
headline block above.

**Raw output:** `e2e-chain-probe.log`; bootstrap: `probes/e2e_postgres_bootstrap.py`.

**The reconciliation readers return the real property maps, so the field half is a real count.** A
reader that returned `{}` for every key would make `field_mismatches` **structurally zero** —
`reconcile_views` compares two per-key mappings field by field, and two empty mappings always agree
— leaving the run proving key-set agreement only. With the actual maps the field half is measurable,
and it reports a real divergence (see S2).

**What this proves, and what it does not.**

- ✅ The two projections **work end to end**: journal (39 records) → Postgres record (60 mutations,
  7 `node_identity` rows) → `read_record_view` → `rebuild_from_record` → FalkorDB (node counts
  match the record exactly), and a bounded rebuild declares `partial` and marks an unwritten key
  `not-hot`.
- ✅ `reconciliation.reconcile` **detects a second writer on real data**, on both halves: a direct
  raw-handle node write reports `extra_in_cache=1` (the §17.1 key-set signature) and a direct
  raw-handle **property** write reports `field_mismatches=2` (the value signature).
- ⛔ A **clean** record-sourced rebuild is **not** zero-divergence: `field_mismatches=1` — the
  `vecf32` narrowing of `Point:p1.embedding` (record `[0.1, 0.2]` float64, cache
  `[0.10000000149011612, 0.20000000298023224]` float32). This is a **comparison-rule decision the
  module defers**, not a wiring gap; recorded on **`#8615`**. It is why S2 stays NOT-GREEN even
  after the wiring lands.
- ⛔ It is **driven by hand**. In the product, neither `record_projection.replay`, nor
  `read_record_view`, nor `rebuild_from_record`, nor `reconcile` has a single production caller
  (`s2-wiring-census.log`). The chain is **built**, not **wired**.

---

## S1 — every write to the truth first; cache projected from it — ⛔ NOT-GREEN

**Fails if:** a write reaches the cache directly. **It does.**

### The guard #8389 landed, and what it does not cover

`uv run pytest tests/test_7951_enforce_atomic_write.py tests/test_7951_write_doors.py tests/test_7951_delete_resurrection.py -q`
→ **51 passed** (`s1-7951-tests.log`). The guard is real and fail-closed for the two lanes it names.

But the raw handle is not one of them. `docs/evidence/7950-capstone/probes/s1_bypass_probe.py`
creates a **journal-backed** SDK (guard armed) and attempts three writes:

```text
journal_backed (guard armed): True
FOLD LANE (proj.apply):          refused (DirectCacheWriteRefused)
PUBLIC RAW LANE (proj.query):    refused (DirectCacheWriteRefused)
RAW HANDLE LANE (proj.g.query):   SUCCEEDED (write reached the cache)
cache rows for :S1Probe after all three attempts: ['raw-handle']
journal records after all three attempts: 0
S1 VERDICT: BYPASS CONFIRMED
```

**A write reached the cache with zero journal records.** This is the criterion's own failure mode:
*"a derived cache that is also written to directly is not derived."*

### The highest-traffic write path is apply-first

`uv run pytest -v "tests/test_7951_enforce_atomic_write.py::test_create_point_is_apply_first_a_recorded_deviation"`
→ **1 passed** (`s1-create-point-apply-first.log`). The shipped test's own docstring:
`create_point` writes the Point through the guarded handle and appends its `PointAdded` record only
afterwards — **cache ahead of truth**, §17.1's ordering contract exception 2.

### The surface is not small

Census (`probes/s1_census.py`, `s1-census.log`; population `tortoise/**/*.py`, method = test-design
§9: paren-balanced read of every `.query(` argument, first mutating Cypher verb):

```text
mutating `.query(` sites: 442  files: 38
first-verb tally: {'SET': 172, 'MERGE': 129, 'CREATE': 56, 'DETACH DELETE': 44, 'DELETE': 29, 'REMOVE': 12}
raw-handle sites (`.g.query` / `.g._g.query`): 288 of 442
raw-handle mutating sites (independent regex): 283 files: 23
```

⚠️ **The count has drifted every measurement**: `387` @2026-10-08 → `420` @2026-10-10 → `441` → **`442`
today. Each is a reading of a moving surface, not a contradiction.

**Missing:** enforcement on the raw-handle lane, and the atomic write. **Homed: `#7951`** — still
OPEN, and `#8389`'s own commit message says so: *"Deliberately NOT closed … a mutating statement on
the guarded handle `proj.g.query` … #7951 therefore stays OPEN."*

**Not a blocker (correcting the previous verdict):** `#8497` ("self-hosted is journal-less") is
**CLOSED `NOT_PLANNED`** (2026-10-13) — the self-host shape is decision-sanctioned (`THE PATH
DECISION`), not a defect. It must not be carried as an S1 blocker.

---

## S2 — divergence is OBSERVED, not assumed — ⛔ NOT-GREEN

**Fails if:** divergence is merely *recoverable*. The mechanism is *checkable*; it is **not
observed**, because nothing in the product calls it.

```console
$ uv run pytest tests/test_reconciliation.py -q
27 passed in 6.09s                                       (s2-reconciliation-tests.log)

$ uv run python docs/evidence/7950-capstone/probes/s2_wiring_census.py
production population: ['tortoise', 'tools', 'scripts']/**/*.py
== reconciliation.reconcile: 0 production call site(s) ==
== record_projection.replay: 0 production call site(s) ==
== cache_projection.read_record_view: 0 production call site(s) ==
== FalkorProjection.rebuild_from_record: 0 production call site(s) ==
                                                        (s2-wiring-census.log)
```

The mechanism itself is sound, and the end-to-end run proves it **on real data** — after a direct
raw-handle cache write it reports `extra_in_cache=1` (the second-writer signature, §17.2) and after
a direct raw-handle property write it reports `field_mismatches` (the value-divergence signature).
But a check that is never run observes nothing. `reconcile()`'s own docstring names the dependency:
*"until the record's own read path exists (#7970) and the cache is projected one way from it
(#7971)."* There is no scheduled/continuous runner.

**A second, independent reason S2 cannot go green — the field half.** With readers carrying the real
property maps, a **clean** record-sourced rebuild reports `field_mismatches=1`: `Point:p1.embedding`
is `[0.1, 0.2]` (float64) in the record and `[0.10000000149011612, 0.20000000298023224]` in the
cache, because `node_statement` writes a non-verbatim embedding as `vecf32` (`#5004`) while
`reconciliation._canonical` compares numbers **exactly, by design** (its docstring defers a tolerance
to "a separate decision with its own recorded bounds"). So `reconcile()` can never report a clean
cache for a node carrying a non-verbatim embedding, and a genuine vector-field divergence is
indistinguishable from that narrowing. **This is a decision the module defers, not a wiring gap**
— recorded and homed on **`#8615`**.

**Missing:** a running reconciliation — the record reader and its wiring. **Homed: `#7951`**
(the chokepoint the reconciliation is evidence for) and **`#7970`** (the record read path,
OPEN/deferred by §2.0). The comparison rule for vector-valued properties is homed on **`#8615`**.

---

## S3 — ⭐ the completeness marker works — ✅ GREEN

**Fails if:** an empty result is returned where `not-hot` is required.

```console
uv run pytest tests/test_residency_marker.py -q      → 58 passed
uv run pytest tests/test_cache_projection.py -q      → 30 passed
uv run pytest tests/test_cache_projection_live.py -q → 5 passed  (live FalkorDB)
```

Named load-bearing tests (`s3-s4-named-tests.log`, all PASSED):

- `TestReadPathEndToEnd::test_a_partial_store_marks_a_nonexistent_node_not_hot` — a read for a node
  outside the known-hot set returns **`not-hot`**, not an empty result.
- `TestReadPathEndToEnd::test_a_complete_store_marks_a_nonexistent_node_hot` — the polarity's other
  side.
- `TestApplyResidency::test_not_hot_never_stands_in_for_empty` — under a partial declaration,
  `hits == 0` with no truth fetch is **`degraded`**, never `empty`.
- `TestApplyResidency::test_a_consulted_record_makes_empty_a_real_finding` — `empty` requires the
  record to have been consulted.

The marker rides its **own** field on the **customer-callable** hosted `GET /v1/search`
(`TestHostedSearchRoute`, 6 passed — `s3-hosted-surface-tests.log`), and is **not** a fifth term of
`available | empty | degraded | unconfigured`.

**Independently reproduced in the end-to-end run** (`e2e-chain-probe.log`): a bounded rebuild
(`limit=1`) declares `coverage=partial`, and the unwritten record key `Point:p1` is `not-hot`.

**Recorded caveat (not hidden):** the marker is **OFF by default** (`TORTOISE_RESIDENCY`) and the
declaration **defaults to `complete`**, under which the marker **cannot fire** — §2.0's deliberate
deferral, not an absence. The per-key node-naming read path that would drive it in production
(`#7970`) is deferred.

---

## S4 — a residency miss is not an insufficiency — ✅ GREEN

```console
$ uv run pytest tests/test_residency_router.py -q
30 passed in 3.26s                                       (s3-s4-unit-tests.log)
```

`tortoise/residency_router.py` is a pure decision (`serve` / `fetch-record` / `escalate-tier`) with
**residency outranking sufficiency** (`test_residency_outranks_sufficiency`), a distinct
**`cache-unreachable`** branch that is a residency miss and never an absence
(`test_a_down_cache_is_a_residency_miss_not_an_absence`), and route names pinned **disjoint** from
the four status terms (`test_the_route_names_are_not_status_terms`). `#7953` CLOSED.

---

## S5 — derived/volatile fields are NOT in the system of record — ✅ GREEN

**This flipped from the previous verdict.** At `77a0c32bd`, `subject.props {"status":…}` was
**ACCEPTED** — the one cache name the carve-out missed. It is now **refused** by the forward
migration `20261014000001_record_subject_props_no_derived.sql` (`#8263`, CLOSED 2026-10-14).

Ship the shipped harness — it applies **every** migration in `supabase/migrations` in filename
order, then runs the SQL assertion suites with `ON_ERROR_STOP`:

```console
$ npm --prefix supabase/tests/pglite run validate
...
✅ 20261012000001_record_schema.sql PASSED (no exceptions)
✓ #7969: the record projection replays 39 journal records, is idempotent, total, and stores no EP scalar
✅ ALL MIGRATIONS + BOTH TEST SUITES + SPOT CHECKS PASSED
                                                          (s5-s6-pglite-validate.log, exit 0)
```

`20261012000001_record_schema.sql` §4a contains the exact probe the previous verdict ran by hand —
it inserts `subject.props {"status":"superseded"}` and requires a `check_violation`, with a
sentinel that fails the suite on the **specific** assertion if the CHECK does not fire (so a mere
`PASSED` means the refusal fired). The shipped Python reader agrees:

```console
$ uv run python -c "...record_projection.refused_props_names()..."
subject refused: ['embedding', 'status']                  (s5-placement-enumeration.log)
```

Every node table carries a `props` refusal CHECK, and `tests/test_record_schema_placement.py`
pins schema↔placement **both directions**:

```console
$ uv run pytest tests/test_record_schema_placement.py -q
26 passed in 2.87s                                        (s5-s6-unit-tests.log)
```

**Denominators:** the placement table enumerates **221 property names** across the seven labels
(192 PRIMARY / 29 DERIVED-VOLATILE / 0 UNRESOLVED) — `216` as measured at the `#7955` pass, plus
`#2782`'s five PRIMARY money rows annotated on top rather than folded into that pass's numbers;
the original live-graph sample was
**9 of 87 property names on a 10,000-`Point` sample**, itself truncated by FalkorDB's silent
10,000-row cap. The `#7969` clause asserts the record stores **no EP scalar** over the **39-record**
journal fixture.

---

## S6 — a canonical id exists — ✅ GREEN (contract); live formats NOT normalised

```console
$ uv run pytest tests/test_identity_contract.py -q
8 passed in 4.45s                                         (s5-s6-unit-tests.log)
$ npm --prefix supabase/tests/pglite run validate         → 20261012000001_record_schema.sql PASSED
```

The stable key is fixed **per label** — `:Point`→`id`, `:Event`→`eventId`,
`:Subject`/`:Object`→`name`, `:Source`→`url` — so a foreign key is **`(label, key)`**, never a bare
id; enforced in the schema by `public.node_identity.key_property` (generated) and composite FKs,
and pinned in Python by `stub_key` + `STRUCTURAL_REL_LABELS` (`aboutDocument` keys on `url`, never
the retired `coalesce(title, name)`). A new `:Point` id is minted by `tortoise/ids.py::ulid()`
(`^[0-9a-f]+-[0-9a-f]{12}$`). The criterion's fail condition — *"a shared canonical id does not
exist"* — is **not** met.

**Recorded caveat (not hidden).** The **two live id formats are not reconciled**: the ~`9,700`
legacy ids (`8,700 <hex>-<hex>` + `1,000 p-NNNNNNN` + `~30 EV-*`) remain, declared **opaque and
valid**, with normalisation deliberately deferred to **W1b** under the owner's 2026-10-10 scope
ruling. ⚠️ **Those three counts are carried from `#7955`'s live-graph measurement of 2026-10-08
(`STORAGE-ARCHITECTURE.md` §17.5) — they were NOT re-measured in this lane, which has no access to
the live graph**, so they are approximate-and-unattributed-here rather than a reading of this
tree; the `10,000`-row cap does **not** apply to them (they are whole-graph id counts, not the
truncated property-name sample the cap truncates). **W1b has no issue home** (searched 2026-10-14:
`#7955` states it is *"deliberately not a child"*; `#7879`'s body never names it). If S6 is read as
*"the live formats are reconciled"*, it is **NOT-GREEN and unowned**.

---

## The meta-question — is *"bytes never read again stop renting RAM"* measurable? — NO

- §17.4 landed the arithmetic (`#7954`) and names two empty inputs, `f` and `ρ` — **`#7983`** (OPEN).
- But there is **no read-frequency instrument at all**. `f` needs to know which bytes are read, and
  nothing measures that (`s2-read-ledger-census.log`; population `tortoise/**/*.py` and
  `supabase/migrations/*.sql`):

```console
grep -rniE "last_read|read_count|access_count|hot_fraction|read_ledger|query_telemetry" tortoise/  → 0 relevant
grep -rniE "query_log|query_telemetry|read_ledger|access_log|node_read" supabase/migrations/*.sql → 0
```

So `f` is a **policy** variable (what we choose to keep), not a **measured** one. The artifact that
would make the claim measurable is a **read ledger** — one persisted row per `(tenant, label, key)`
with `bytes`, `first_written_at`, `read_count`, `last_read_at` over a stated window. **Already
recorded on `#7983`** (same root; the record-side companion is `#4492`); this run re-measures at
HEAD and confirms it.

**And the only measurement that exists says the claim is currently false at today's policy.**
§17.4.3: at `f = 1` (*"keep the limit in falkor high"*, §2.0) the hybrid is a **strict net cost** —
`0.99×` at 1,000 tenants — because the saving `(1 − f) × cache` is zero while the record's lines are
strictly positive.

---

## ⚠️ KNOWN OPEN CONTRADICTION — reported, not resolved

The graph point `STORE-JUDGEMENT-postgres-becomes-the-off-box-copy-not-a-jsonl-projection`
(id `1a1224318c1-ea987165cdad`, status `live`) argues that **Postgres should be derived from the
store's own persistence (the AOF/RDB)**, not from the JSONL journal:

> *"IT IS: POSTGRES IS DERIVED FROM THE STORE'S OWN PERSISTENCE — the AOF/RDB — which #2881 already
> designates as the durability mechanism."*

This **contradicts the landed `#7969`** (the record projection is **journal → Postgres**), which the
end-to-end run above exercises. **This evidence does not pick a side and does not adopt one.**
Resolving it is a **reopen argued in the decision's own home** with the owner, per the fleet rule.
Recorded here because the proof touches the affected surface.

---

## What is still missing (exact blocking criteria)

| # | blocker |
|---|---|
| **S1** | enforcement on the raw-handle lane (`proj.g.query`) + the atomic write → **`#7951`** (OPEN) |
| **S2** | a running reconciliation: the record reader + the chokepoint wiring → **`#7951`** + **`#7970`** (OPEN); the comparison rule for vector-valued properties (a clean rebuild reports `field_mismatches=1`) → **`#8615`** (OPEN) |
| **S6 caveat** | live legacy-id normalisation (W1b) → **no issue home** |

Not blocking, recorded: the marker is off by default under a `complete` declaration (§2.0's
deliberate deferral); the record read path (`#7970`) is deferred; `#8497` is CLOSED `NOT_PLANNED`
(not a defect).

## Reproduction

```bash
# 0. private FalkorDB lane
uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
# 1. throwaway Postgres + the record migrations
docker run -d --name cap7950-pg -e POSTGRES_PASSWORD=cap -e POSTGRES_DB=capstone \
  -p 127.0.0.1:15499:5432 postgres:16
uv run --with psycopg2-binary python docs/evidence/7950-capstone/probes/e2e_postgres_bootstrap.py
# 2. the criteria
TORTOISE_DB_URI="$TORTOISE_DB_URI" uv run pytest \
  tests/test_7951_enforce_atomic_write.py tests/test_7951_write_doors.py \
  tests/test_7951_delete_resurrection.py tests/test_reconciliation.py \
  tests/test_residency_marker.py tests/test_residency_router.py \
  tests/test_record_schema_placement.py tests/test_identity_contract.py \
  tests/test_cache_projection.py tests/test_cache_projection_live.py -q
npm --prefix supabase/tests/pglite run validate
# 3. the probes
uv run python docs/evidence/7950-capstone/probes/s1_census.py
TORTOISE_DB_URI="$TORTOISE_DB_URI" uv run python docs/evidence/7950-capstone/probes/s1_bypass_probe.py
uv run python docs/evidence/7950-capstone/probes/s2_wiring_census.py
TORTOISE_DB_URI="$TORTOISE_DB_URI" uv run --with psycopg2-binary \
  python docs/evidence/7950-capstone/probes/e2e_chain_probe.py
# 4. teardown
docker rm -f cap7950-pg; uv run python tools/test_lane.py down
```
