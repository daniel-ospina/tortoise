---
title: "The record's residual — plan and decomposition (#7879, 2026-11-05)"
type: plan
domain: engineering
doc_status: draft
created: 2026-11-05
updated: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #7879, plan, residual decomposition, record write path, deploy drift gate"
---

# The record's residual — plan and decomposition

**Epic:** **#7879** — *the Postgres record — the four ontology layers as an authoritative relational store.*
**Ruling this serves:** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) — **Postgres is the
system of record for every layer; FalkorDB is a derived hot cache.** The one exception is the EP scalars
(`ep_alpha`, `ep_beta`, `confidence`, `posterior_*`, `baseline_*`), recomputed and never stored as authority.
**This document re-decides none of it.** A child that reverses §2.0 is a REOPEN, not a task.

**Measured at:** `origin/main@3ac6b1636` on 2026-11-05, after review cycle 1. This lane's first reading was at
`641e5f1b3`, twelve commits earlier, and **three of its load-bearing rows were stale within that window** —
issues #10185 and #10671 both closed, and the `search_keys` narrowing landed inside a batch fold. Two more were
**simply wrong** (#10620 is an unrelated docs PR; the detection machinery this plan first called "existing" does
not exist in code). §9's log, with §1.1 and §7.1–7.2, records every correction so the next lane does not
re-derive them.

---

## 0. What this document is, and what it is not

It is the **Plan** stage's artifact and the **Decompose** stage's specification for the epic's remaining work.
It approves no scope — **the owner asked this lane on 2026-11-05 to run the epic workflow and produce the
decomposition; the grouping below is the lane's proposal and approves no scope** (relayed to #7879 comment
`6663378009`). It re-decides no ruling. It is not an implementation plan for any single child.

The epic's implementation is **largely built and must not be re-litigated**: the four migrations and their
enforcing constraints, the three production write doors, the projection's totality/idempotency, the cache
rebuild from the record, the EP carve-out (7 `chk_*_props_no_derived` CHECKs), tenancy, the backup + restore
drill, and the R5 edge producers. **All 13 native sub-issues are closed.**

⚠️ **No census count is asserted here.** Two populations are in play and they do not agree: the native
sub-issue links (13, all closed, before this lane added 23 — 36 in total, **22 open / 14 closed** after #10683
was closed when its answer was recorded) and the issues whose *body* carries an
`Epic: #7879` field. The GitHub search API's tokenized `in:body` match returned **99 total / 26 open** by one
query and **38 / 11** by a stricter re-match — **so no count of THAT population is quoted anywhere in this
document; it is not reproducible.** ⚠️ **Two different populations must not be confused:** the 1691 figure in
§1.2 is the **whole repository's** open-issue count (`issues(states:OPEN){totalCount}`), which *is* reproducible
and is quoted only to bound the duplicate hunt. What is reproducible about *this* population: the epic's native
sub-issue list contained **only closed issues** before this lane, so the parent read as done while the store was
inert.

---

## 1. The measured state — and the corrections this lane had to make

| fact | state at `3ac6b1636` | how it is known |
|---|---|---|
| `public.point.search_keys` declared type vs what producers emit | declared `jsonb`; every producer emits the graph's flat space-joined **string** | `20261012000003_record_epistemic.sql:80`; `20261102000002_record_point_search_keys_text.sql` header; `projection/entities.py::flatten_search_keys_prop` is the only flattener |
| **who narrowed it** | **`a6e20f05b`** — *"fold: 27 mergeable PRs into one reviewed batch"* (#10717) | `git log --oneline origin/main -- supabase/migrations/20261102000002_…sql` |
| #10648 | **MERGED** — it carried the *attribution* (`RecordStatementError`) and the window-based `/health` verdict, **not** the migration (its 29 changed files contain nothing under `supabase/migrations/`) | `gh pr view 10648 --json files` |
| #10671 (keep `jsonb`, wrap the string) | **CLOSED, never merged** — `mergedAt: null` | `gh pr view 10671` |
| the alarm (#10185) | **CLOSED, landed on main** — `/health` carries a degraded record verdict | `tortoise/hosted_api.py` (`record_refusing`, the `_record_health` contract) |
| the two migrations | present in the repo, **unapplied in production** | `20261102000001_graphs_promote_default.sql`, `20261102000002_record_point_search_keys_text.sql` |
| the deploy | **refused on every run, 0 successes.** #10896's own tally at filing: `[{"":2},{"cancelled":5},{"failure":13}]` = 20 runs, last good deploy 2026-11-03T02:53Z; **re-measured today: `[{"cancelled":3},{"failure":17}]`** = 20 runs, still 0 successes | #10896; `gh run list --workflow deploy-hosted.yml` |
| consequence in production | every point record refused → the row dropped → `public.relation` starved with it. ⚠️ **Date every reading:** `record_write_failures` is **per process**. It read `write_failed: 302` at ~19:17Z on 2026-11-02 (#10593, #10896); it read `{}` on the 2026-11-05T08:39Z boot, and because #9994 records that **there is no positive write counter**, an empty counter cannot distinguish "no refusals" from "no writes". `/health` reads `ok: true` because the degraded verdict is **inside the blocked deploy** | #10593; #10896; live `/health` read 2026-11-05T16:28Z |
| de-facto store | **FalkorDB**, 68,526 nodes / 195 MB resident, zero evictions | #10976 |

**⇒ The one-sentence verdict.** The record's correctness work is done and merged; **the epic's entire first mile
is one blocked deploy**, and until that deploy lands the record cannot be evidenced, priced, or used, and nothing
may be safely evicted from the cache. ⛔ **Do not read `/health`'s `migrations_head` as evidence either way:**
**#10896**'s own verification comment establishes it is *"the highest migration-shaped file THE IMAGE CARRIES"*
(`migrations_source: checkout`) — **it answers which migration the image ships, never which one has been
APPLIED.** The unapplied state is evidenced by the drift gate's refusal itself, which reads prod's
`supabase_migrations.schema_migrations` and names the two migrations as `BLOCKING repo-ahead`.

**⇒ The ordering consequence, stated plainly.** The lane brief said *"#10593 is the first child, gated."* It is
the right name for the **symptom** and the epic's first deliverable — but its **prerequisite is #10896**, which
the brief did not name. Decomposing around #10896 would parallelise across the one gate that makes every other
slice unprovable, which is what the brief forbids.

### 1.1 ⚠️ Correction: `#10620` is not this epic's incident

This lane first cited **#10620** as the `search_keys` defect throughout. **It is an unrelated docs PR**
(`docs(8447): the census names the bound the surface actually has, and censuses tortoise_query`, CLOSED,
`mergedAt: null`). #10648's own review had already falsified the attribution — its body, review finding 4: *"the
change was traced to #10620, which is an unrelated docs PR."* The likely origin of the confusion is that the pin
file is named `tests/test_record_searchkeys_10620.py`.
**The correct references are #10593 (the open symptom) and #10648 / `a6e20f05b` (the fix).** Corrected in this
document and in `01-test-design.md` §10.

⚠️ **The misattribution is not fully removable, and saying so is the point.** Migrations are append-only, so
`20261102000002_record_point_search_keys_text.sql`'s own header still reads *"(#10620, epic #7879 · R4's remaining
half)"* — the primary artifact a reader of this fix opens. The sites named so far — `tools/ci_selection.py`,
`docs/infra-runbook.md`, and — **13 occurrences on 11 lines**, the two pin-filename mentions aside — this
epic's own `08-hosted-prerequisites.md`, including the line the **302** figure in §1 is quoted from — are
**examples, not a census**: `git grep -l 10620` matched **23 files**, and the two that matter most were not on
that list. `tortoise/record_projection.py` carries it on **18 lines** (`:265` reads *"#10620 — R4's remaining
half: `public.point.search_keys` NARROWED to …"*), and
`tests/test_9078_record_written_by_production_path.py` carries it inside the **assertion message** at `:2367`
(*"… #10620 closed `search_keys`"*) — the text a future failing pin prints, and the very file this document
quotes at §7.2. `tortoise/hosted_api.py` (4 lines) and `config/ci-surfaces.yml` (`:606`, `:3627`) assert it too.
So a lane that "fixes the residual" from this paragraph alone leaves a **test assertion message** wrong.
**The
correction travels in #10683's recording comment (where a reader of the decision looks) and belongs with
the sweep in #10864; it cannot travel in the migration.** ⚠️ **`08-hosted-prerequisites.md`
is a DECLARED residual, not a covered one:** #10864 is scoped to three other post-#10648 contradictions and does
not reach this one, so no carrier takes it today — a lane that reads that file meets the misnumbering unopposed.

### 1.2 Existing-work census — how duplication was ruled out (and one measurement that was not a census)

**The population is 1691 open issues** (`repository{issues(states:OPEN){totalCount}}` at `3ac6b1636`). ⚠️ **A
`gh search issues --limit 300` sweep returns exactly 300 and is NOT the population** — this lane's first pass used
one, and it was truncated; recorded here so the next lane does not repeat it.

| method | result |
|---|---|
| body-search for the **failure class** (`declared type`, `schema disagreement`, `22P02`, `InvalidTextRepresentation`, `DatatypeMismatch`, `type contract`) | candidates **#10984**, **#11001**, **#11081**, **#10695**, **#8929** — each read: all unrelated to a declared-type guard except **#11081** (the beta board, below) and **#10695** (the #10671/#10675 P2 lineage, which is a *diagnostic* fix, not a contract pin) |
| **open-PR** search for anyone already building a declared-type guard | **none** |
| `uv run python tools/collision_preflight.py 7879 --repo .` | **CLEAN** (exit 0, 7/8 surfaces; 1 advisory partial) — and it named the in-flight PRs this plan now adopts: **#11064**, **#11004**, **#10852** |
| the two **boards** that already track this territory | **#10977** (*"making the hosted graph usable"* — the spine, per-area state and in-flight PRs) and **#11081** (*"the beta path"* — 17 `P0-blocker` / 17 `P1-severe` open at 2026-11-05T16:10Z, carrying its own instruments and a *"do not re-derive"* instruction). **Both are cited, neither is re-derived here.** |
| does anyone already own **G5's byte measurements**? | body-searches for `postgres_storage_footprint`, `bytes per day` and *"the overlap"* return only #7879's own comment and #10000 — **the gap in §5.1 is real** |
| does this **compete with the epic's own earlier decomposition**? | **no — it updates it.** The 2026-10-31 pointer on #7879 recorded *"Gap = 0. No child filed, deliberately"*; five days later the epic's first blocker had no parent. This document is the delta from that state, not a second plan |

**⇒ #11075 is not a peer.** No open or closed issue proposes a declared-type contract, and the only prior artifact
of this class was the `_SCHEMA_DISAGREEMENTS` vocabulary **deleted with #9264** (§7.2).

---

## 2. Scope — five groups, and what is not in it

The groups are **mutually exclusive by deliverable** and **collectively exhaustive over the epic's four
acceptance criteria**, with one criterion honestly marked *waived* rather than satisfied:

| group | the deliverable | acceptance criterion |
|---|---|---|
| **G1 — the record accepts every production write** | `write_failed`/min = 0 sustained for 30 min on the deployed app, proved by a **positive** write counter | 1 (the "exist in Postgres" half) |
| **G2 — the record is proven end to end** | the capstone returns PASS by execution against a **battery of falsifiers**, not inference | 1 (the "rebuilt without data loss" half) |
| **G3 — the placement residuals are closed** | the record can serve what a cache purge would delete | 1 (no silent loss) |
| **G4 — the record is operable in the shipped image** | the record's own verbs run in the deployed image; reconcile runs on a clock | 1 (operable, not merely written) |
| **G5 — cost per byte is measured** | the byte census the owner prices the cache share from — **five** measurements, see §5.1 | cost basis for §0's ruling |

| criterion | verdict this plan carries |
|---|---|
| 2 — EP scalars recomputed, and the schema says so | **MET** (7 `chk_*_props_no_derived` CHECKs, measured live). Needs only a regression guard, not a group. |
| 3 — a read path answers from Postgres with the engine stopped | ⛔ **WAIVED BY THE RULING, NOT MET.** #7970 is closed and its own body says the read-path shift *"is DEFERRED by the owner's own ruling … it must not hold the critical path."* Closing-as-completed is not satisfaction; §2.0's §"keep the limit in falkor high so we don't need to rush" is the deferral. **This plan must not be read as claiming criterion 3.** Amending the acceptance table is the owner's act. |
| 4 — one artifact for the schema | **MET** (one `RECORD_MIGRATIONS` registry, no glob, pinned and shipped in the wheel). |

### 2.1 Out of scope — declared, not omitted

- **#10000** (raw turns + narrative still written into the graph) and **#10017** (purge the historic turn
  nodes). #10017 is **destructive and gated on G3**: its own rule is *"if the census is anything but exact,
  stop — the answer is a backfill from the graph into the record, not a delete."*
  ⚠️ **But #10000 is not fully excludable, and the review found it:** the capstone's one open PARTIAL leg is
  the `:Source.summary` prose route, whose **writer is the capture path** (`sdk._session_source_metadata`) —
  i.e. #10000's producer. **G2 therefore cannot PASS on that route without a #10000-family change.** It is
  carried as an explicit **G2** carrier gap in **§5.2** — not as silent exclusion, and not billed to G3, because
  G2's PASS is the thing it blocks.
- **#10976** (nothing evicts the cache) — the egress half, explicitly gated on this epic.
- **#5090** (the vector layer).
- **Every pricing number** — the owner's, from measurement (`CONSUMPTION-MODEL.md` §4).
- **`#9548`'s arming decision is SETTLED, and arming is AUTHORISED.** ⚠️ **This plan's first draft carried the
  superseded ruling and was wrong.** The owner posted on #9548 at 2026-11-03T19:06Z: *"**arming the record is
  authorised** (`TORTOISE_RECORD_DSN`, R1)"*, with the marker *"**OVERRIDES:** the standing \"hosted postponed\"
  ruling — superseded for this issue by the owner's explicit clarification that no customer is on the service and
  that the beta is blocked on P0/P1 closure."* The record **is** armed (`/health` → `"armed": true`, `dsn:
  adb0c38b`). #9548 stays OPEN because **Target (a)** — one captured session fully projected — is unmet, which is
  downstream of the deploy, **not an owner gate**. **PR #11064** (open, `BLOCKED`) is the doc sweep that purges
  the superseded instruction; this plan must not re-seed it — and the first draft did.

---

## 3. Dependency order

```text
#11075  declared-type guard
   ↑ NO GATE: DB-free, needs no deploy — start in parallel today. Its only link to #10896 is that the field it
guards is only *live* once the deploy lands.

#10896  (the deploy — 2 migrations unapplied)              ← the first gate
   │
   ├── #10593  write_failed → 0 in production
   │      └── #9994  positive write counter                  (rows landed, not just "no longer failing")
   │
   ├── #10663  the record's verbs run in the deployed image
   │      └── #9276  reconcile on a schedule
   │
   └── #9567  CAPSTONE (G2)
          ├── #9548  ← NOT an owner gate: arming AUTHORISED 2026-11-03; OPEN on Target (a), i.e. downstream of the deploy
          └── #9559  CONDITIONAL — gates D11/D12 only (#9567: "must not absorb", `FOLLOW`)

G3 (parallel once writes land): #10394 · #10429 · #10369 · #10193 · #9881 · #9833 · #10864 · #9129 · #9997
   └── the `:Source.summary` capture-path route (deps: a #10000-family change — see §2.1/§5)
G5 (needs the deploy, not G2): the five byte measurements in §5.1, not the three an earlier draft carried
G4 remainder: #9558 (pooling) · #10600 (docs census) — no gate of their own
```

**#8607 is NOT a prerequisite of the capstone.** Its own body records the owner's 2026-10-10 ruling putting it
at *"low concern … so it is **P2, not critical**"*, with *"**Not in scope:** … making it a launch blocker."*
Escalating it to a `#9567` precondition would reverse a recorded scope ruling from a decomposition lane, so it
stays a **non-blocking G3 row**.

**⇒ There is ONE serialisation point, not two.** #10896 gates everything, and **#9548 is a DEPENDENT, not a gate**:
arming is authorised (2026-11-03, `OVERRIDES:` the earlier postponement) and the record is live-armed, so #9548's
remaining Target (a) — one captured session fully projected — is exactly what G1 proves. **The epic is therefore
not owner-blocked anywhere.** What it *is* blocked on is one deploy, and what remains unresolved for G2 is the
`:Source.summary` route in §5.2, which no deploy fixes.

---

## 4. Test-design gate — the map is current, and it is missing exactly one failure mode

The map is `01-test-design.md` (#7967, CLOSED, measured `main@eed2be8147` on 2026-10-10). Every group maps onto
an existing surface, so **it is not stale and is not re-filed**: G1 → **S1**+**S2**; G2 → **S9**+**S1**/**S3**;
G3 → **S1**+**S4**/**S5**; G4 → **S2**. G5 is an instrument, not a surface.

**The gap, and why a new child is earned:** S1's failure modes are *totality*, *idempotency* and *a carried
field not projected*. None of them fires when **the column's declared type refuses the value the journal
carries** — totality holds, the projection is idempotent, placement is right, and the row is dropped anyway.
That class has now cost rows twice. The delta is appended to `01-test-design.md` §10; its pin is **#11075**.

---

## 5. The decomposition

**24 existing issues carry this work; exactly one is new** (25 rows: **21 open, 3 closed records
— #10185, #10683, #10671 — and 1 new child, #11075**). Filing a peer for a known root is the defect this tracker
has already paid for once, so the decomposition's product is **navigation and order**.

| # | what | group | state at `3ac6b1636` | action |
|---|---|---|---|---|
| 1 | **#10896** deploy: 2 unapplied migrations | G1 | OPEN | **linked + labelled** — the gate |
| 2 | **#10593** every hosted record write refused | G1 | OPEN | **linked**; the root is recorded in comment `6663361035` — ⚠️ **the body still asks for that investigation**, so the comment, not the body, is the answer |
| 3 | **#11075** the declared-type guard | G1 | NEW | **filed** |
| 4 | **#9994** positive write counter | G1 | OPEN | linked |
| 5 | **#10185** the silent-arming alarm | G1 | **CLOSED** | recorded: landed on main, **undelivered by #10896** |
| 6 | **#10683** the `search_keys` decision record | G1 | **CLOSED** (this lane) | answer recorded in comment `6663370867` — shape A + the `OVERRIDES:` marker + three factual corrections (§6) |
| 7 | **#10671** the losing shape | G1 | **CLOSED, unmerged** | none |
| 8 | **#9567** capstone | G2 | OPEN, `owner-decision` | linked; **blocked by #9548** |
| 9 | **#9548** arm the record (R1) | G2 | OPEN — **arming AUTHORISED 2026-11-03**; Target (a) unmet | linked; **do not re-post the superseded "postponed" ruling** — the record is already armed, and **PR #11064** is the open sweep that purges it |
| 10 | **#9559** the byte gate | G2 | OPEN (`FOLLOW`) | linked; **conditional — D11/D12 only** |
| 11 | **#8607** id normalisation | G3 | OPEN, owner-deferred P2 | linked; **not a capstone precondition** |
| 12 | **#10394** the restore re-materialises the narrative | G3 | OPEN | linked |
| 13 | **#10429** the `:Point` narrative replay route | G3 | OPEN | linked |
| 14 | **#10369** the procedural views read carriers with `IS NOT NULL` | G3 | OPEN | linked |
| 15 | **#10193** the app role has no `SELECT` on the procedural views | G3 | OPEN | linked |
| 16 | **#9881** the record refuses `EntityMutated op=rename` | G3 | OPEN | linked |
| 17 | **#9833** `backfill_about_entities` mints unrestorable edges | G3 | OPEN | linked |
| 18 | **#10864** three texts disagree with the code | G3 | OPEN | linked |
| 19 | **#9129** journal the onboarding FLOW state | G3 | OPEN | **linked** — ⚠️ **added by review cycle 1**: it is criterion 1's "rebuilt without data loss" and was absent from the first draft |
| 20 | **#9997** the DSN fingerprint has four definitions | G3 | OPEN | **linked** — ⚠️ **added by review cycle 1** |
| 21 | **#10663** the record's verbs cannot run in the deployed image | G4 | OPEN | linked |
| 22 | **#9276** reconcile on a schedule | G4 | OPEN | linked |
| 23 | **#9558** pooling + column-map cache | G4 | OPEN (`FOLLOW`) | linked |
| 24 | **#10600** the census docs still read "not armed yet" | G4 | OPEN, docs-only | linked |
| 25 | **#10977** hosted-graph cost/structure | G5 | OPEN, cross-epic **tracking** | linked — **but it is not G5's deliverable** (below) |

### 5.1 G5 has no closable carrier yet — and the ruling names FIVE measurements, not three

G5's deliverable is the owner's 2026-11-01 byte-unit correction (#7879 comment `2026-11-01T05:59:48Z`), which
enumerates **five** — and the count is load-bearing, because it ends *"⛔ Until (1)–(5) exist, 'the graph is full'
is a claim about a proxy":

1. **FalkorDB RAM bytes per graph**, and the turn/narrative share — measured per **synthetic session** only.
2. **Postgres bytes per table** — `postgres_storage_footprint()` exists, **never run against the deployment**.
3. **The overlap** (bytes held in both stores) — **not measured at all**.
4. **Bytes/day growth of each store** — **not measured**, and without it "how long until full" is unanswerable.
5. **RAM bytes per node, measured** — the conversion from a node count to a budget. A figure exists
   (`1,672 B` resident / `6,003 B` capped, `STORAGE-ARCHITECTURE.md` §0) and it is **owner-gated** (#4333);
   re-deriving or revising it is a reopen, not this lane's act.

**#10977 is a cross-epic tracking issue and can never be closed as "G5 done"; items 2, 3 and 4 have no carrier
today, and item 5's carrier is owner-held.** The decomposition therefore records G5 as **partially carried**.
Escalating another child for it now would be a third ticket for one root. **Owner-gated, declared.**

**One PR touched this ground and is adopted, not duplicated:** **PR #11004** (*"storage(10976): an egress
census for the FalkorDB cache"*, **MERGED** `d1806ab5d` at 2026-11-05T17:55Z — after this plan's measurement
point; the census landed, it has not been RUN) is the egress half — it belongs to #10976, which is gated on this
epic, so G5 must not absorb it; and #10977, the tracking issue above, remains the board for all of it.

### 5.2 G2's carrier gap — the `:Source.summary` route (named, not absorbed)

| what | why it blocks G2 | carrier |
|---|---|---|
| **the `:Source.summary` prose route** | the committed capstone run (`docs/verify/2026-10-31-record-capstone.md`, #10456) returns `PARTIAL`, and this is one of its open routes: the capture path (`sdk._session_source_metadata`) writes a transcript slice into `:Source.summary` | ⚠️ **IN FLIGHT, not uncarried:** **PR #10852** (OPEN) — *"the session :Source stops carrying turn prose — the summary is a topic lexicon"* — is that change. It belongs to #10000's family, which §2.1 excludes from this epic, so **G2 cannot PASS on this route from inside this epic's scope** unless the owner (a) accepts it as NOT MEASURED in writing on #9567, or (b) admits #10852 here |

⚠️ **This plan's first draft overstated this as "no path removes it".** An open PR does. The gap is real but it is a
**dependency on in-flight work**, not an orphan — and a decomposition that called it an orphan would have sent a
lane to re-implement #10852.

**This is a G2 row, not a G3 row** — G2's PASS is what it blocks. It was billed to G3 in this plan's first draft,
which is exactly the kind of mis-grouping that makes a "blocked" item look parallel-safe.

**Declared, not absorbed:** `#4333` (the true per-node cost — `tortoise/quota.py` carries an `OVERRIDES:` line;
owner territory), `#10000`, `#10017`, `#10976`, `#5090`.

---

## 6. The recording obligation on #10683 (a decision already taken, unrelayed)

**#10648 merged shape A** — the column narrows to `text`, because the producer's form is authoritative and
nothing reads the record's copy as JSON. #10683 still poses the A/B/C question, and its own body states that
**whoever records the answer owes an `OVERRIDES:` line**, because #9264's scope note characterised this field as
*"not a type narrowing"*.

**⇒ Correct the record and close it, in ONE comment.** Three corrections travel with the marker, because the
issue body currently misdirects a reader:

| #10683 says | on `3ac6b1636` |
|---|---|
| "`20261102000001_record_point_search_keys_text.sql` does `ALTER TABLE … TYPE text`" | **`20261102000001` is `graphs_promote_default.sql`.** The narrowing is `20261102000002_record_point_search_keys_text.sql` |
| "#10648 (…18 files) removes the jsonb forward migration" | #10648 carried attribution + the `/health` verdict; **the migration landed in `a6e20f05b` (#10717)** |
| "#10671 … stays OPEN until this is answered" | **#10671 is CLOSED, unmerged** |

The marker, which must sit where the next lane reads it:

> **OVERRIDES:** #9264's scope note characterising `point.search_keys` as *"not a type narrowing"* — the field IS
> a type narrowing, because the producer emits the graph's flat space-joined string and nothing reads the
> record's copy as a JSON structure; leaving the note uncorrected invites a revert of
> `20261102000002_record_point_search_keys_text.sql` as legacy.

This is **recording, not deciding**: the merge is the decision, and the contradiction test found no ruling
reversed. #9264's own *guidance* — *"Prefer the schema side"* — supports shape A rather than being overridden by
it.

---

## 7. The one new child (#11075) — and the failure it names

**`test(record): pin the write path's declared-type contract`** — a test that fails when a producer emits a
value the column's **declared** type cannot hold.

- **The failure it prevents, with its incidents:** a **column's declared type refusing the value its producer
  emits** — observed as `InvalidTextRepresentation` (`22P02`) **and** `DatatypeMismatch` (`42804`). Twice: #9264
  (`session.capture_redactions`, `jsonb` ← integer) and #10593 (`point.search_keys`, `jsonb` ← string). The
  second cost **~3 days and counting** (opened 2026-11-02T12:31Z, still open).
  ⚠️ **Name the class by its property, not by one SQLSTATE.** #9264's measured error was
  `psycopg2.errors.DatatypeMismatch` (*"column \"capture_redactions\" is of type jsonb but expression is of type
  integer"*), not `22P02`; a pin keyed on `22P02` alone would miss the `42804` shape and would make #11075's
  "second arm replays the #9264 instance" assert that two different errors are one.
- **Reversibility:** delete it and **the product** is worse — rows drop silently again and only a live counter
  nobody reads says so. Not process-only machinery.
- **Ratio:** one test plus a declared-type reader; no fixtures re-encoding a schema.

### 7.1 ⛔ Correction: the mechanism this plan called "existing" does not exist

Review cycle 1 measured it: **`grep -rn jsonb_columns` returns zero code hits.** #10671 proposed that reader and
was **closed unmerged**, so nothing landed. What *does* exist points the other way:

- `tortoise/record_projection.py` states that a **forward migration narrowing a column's TYPE** is a form *"the
  readers do not parse this form, and deliberately"*. ⚠️ **Precisely:** `_COLUMN_RE` **matches** the declared type
  in a **non-capturing** group, and `columns = {m.group(1) …}` keeps only the **name** — so there is nothing
  being discarded, and the type must be **lifted into a capture group**. So *type extraction is nearly there*
  and must be captured, not built; **the genuinely new work is the `ALTER COLUMN … TYPE` form, the widened type
  vocabulary, and the value→declared-type predicate.** And `_COLUMN_RE`'s type vocabulary is a **hardcoded seven-term allow-list**
  (`text|jsonb|integer|boolean|bigint|double precision|timestamptz`), so a derivation built on it **silently
  skips any column of another type** — which is why #11075's closure target ("a column added to a migration with
  no expectation reds") holds **only for types that vocabulary admits**, and the issue says so.

**Consequence for #11075, and it is the sharp one:** an implementer who follows "reuse `jsonb_columns()`" reads
`search_keys jsonb` from the *original* migration, and the pin **reds on the current, correct tree** — whose
obvious repair is to exempt the column or revert the narrowing, i.e. **re-create the outage the pin exists to
prevent.**

**What is actually new work, stated once so the two documents cannot disagree:** the declared type must be
**captured from the existing match** (`_COLUMN_RE` already matches it — above), the **`ALTER COLUMN … TYPE`
form must be built**, the **type vocabulary must be widened** past its hardcoded seven terms, and the
**value→declared-type predicate is new**. That mix of "capture one, build three" is why #11075's `db` domain is
**complex** rather than standard.

### 7.2 ⛔ Correction: this class-detector was deliberately deleted once

`tests/test_9078_record_written_by_production_path.py` records that the `_SCHEMA_DISAGREEMENTS` vocabulary was
made **empty and then "gone rather than parked"** when #9264 closed — the project's "a pin that outlives its
defect" rule. #11075 must say so, and say why a **migration-derived** check differs from the deleted
**hand-maintained vocabulary**: a derived check cannot go empty (it is closed over the migration set), so it
cannot outlive its defect in that way. Without that paragraph the next lane reads #11075 as re-litigating a
recorded removal.

---

## 8. Verification of this decomposition

- [ ] **every open issue carrying an `Epic: #7879` field OR a sub-issue link is in §5 or in §2.1** — stated this
      way round, because "every issue in §5 has a link" cannot catch an omission (review cycle 1 found two)
- [ ] `gh api repos/daniel-ospina/tortoise/issues/7879/sub_issues --paginate` contains the live carriers
      (#10896, #10593, #11075, #9567, #9548, #10977, …), not only closed ones — **verified after this pass: 36
      linked, 22 open / 14 closed**
- [ ] **every P0 named in this plan actually carries the `P0-blocker` label** — #10896 arrived with **zero**
      labels; it and #10593/#10977 now carry `P0-blocker` + `team:epistemic-team`
- [ ] the decomposition comment on #7879 carries a provenance line and names #10896 as the first gate
- [ ] #10683 carries the `OVERRIDES:` line **and** the three factual corrections from §6
- [ ] #11075's body passes the issue-creation body lint (Objective present; ≥2 observable Indicators; ≥1 Target
      each) and carries a verification checklist derived from S1/S2
- [ ] #11075's body names the missing reader and the deliberately-deleted vocabulary (§7.1, §7.2)
- [ ] the capstone (#9567) is named as the Verify-stage gate; **no second capstone is filed**; its #9548
      dependency and #9559's `D11/D12`-only conditionality are both drawn
- [ ] criterion 3 is recorded as **WAIVED by §2.0**, never as met
- [ ] a fresh-context reviewer returns `NO ISSUES FOUND` on (a) this plan's coherence and (b) §5's MECE property

## 9. Review log

| cycle | scope | verdict |
|---|---|---|
| 1 | plan coherence + MECE + the new issue (fresh context) | **15 issues: 10 × P1, 5 × P2.** All 15 verified against the tree and fixed: (1) `jsonb_columns()` does not exist — §7.1; (2) `#10620` misattributed — §1.1; (3) narrowing credited to the wrong PR — §1; (4) MECE gap #9129/#9997 — §5 rows 19–20; (5) criterion 3 wrongly "met" — §2; (6) missing `#9567 → #9548` edge — §3; (7) #8607 wrongly escalated — §3; (8) G2 blocked by an out-of-scope #10000 route — §2.1; (9) G5 has no closable carrier — §5.1; (10) #9559 conditional — §3; (11) #11075 wrongly serialised — §3; (12) two counts not reproducible — §0/§1; (13) #10683's wrong migration filename — §6; (14) deleted-vocabulary disclosure — §7.2; (15) carve-out config rating — #11075 body |
| 2 | MECE re-verification + fix verification (fresh context) | **12 issues (4 × P1, 8 × P2).** All verified and fixed: (1) the `:Source.summary` carrier §2.1 promised was absent from §5 and mis-grouped — new **§5.2**; (2) #10593's body was never corrected — comment `6663361035` posted, row re-worded; (3) the class was named by `22P02` while #9264 measured `DatatypeMismatch` — §7 re-stated by property; (4) G5's ruling names **five** measurements, not three — §5.1; (5) the §5 preamble count did not reproduce — 24 existing + 1 new; (6) #9129/#9997 were claimed linked but were not — **now linked**; (7) #10896 carried **zero** labels — now labelled; (8) #11075 was still drawn inside the deploy subtree — **hoisted**; (9) present-tense production state — §1 now dates every reading and notes `{}` is not green without #9994; (10) `db: complex` rested on a claim the code contradicts — §7.1 corrected (type is captured, the predicate is new) and the closure claim bounded by the 7-term vocabulary; (11) "the owner approved the shape" had no artifact — replaced with the relay that exists, citing the #7879 comment; (12) "the misnumbering stops here" was false — the migration header still carries `#10620` and is append-only, so §1.1 now says where the correction does and does not travel |
| 2b | `[VGATE]` content verification of the two committed artifacts (fresh context, before commit) | **5 issues, all in this document's own text and four of them introduced BY the cycle-2 fixes:** (i) §5 row 6 still read `#10683 OPEN` while this lane had already closed it; (ii) §0's "added 21" was the pre-cycle-2 figure — measured 23 added, 36 total, 22 open; (iii) `01-test-design.md` §10 and §7.1 **contradicted each other** on whether type extraction is built or captured — reconciled to "capture one, build three", stated once in §7.1 and cross-cited; (iv) §7.1 carried a truncated sentence (`the reader must be.`); (v) `#9264`'s duration cell said "closed same day" while its timestamps give **2.9 days** — corrected, and `#10593`'s cell now reads "~3 days and counting". This is the class a commit-gate exists for: every one was a self-inflicted staleness produced by fixing the previous round |
| 2c | `[VGATE]` round 3 **+ the existing-work census the owner asked for** | **One BLOCKING finding: F1 — this plan carried #9548's SUPERSEDED "hosted postponed" ruling as live.** The owner authorised arming on 2026-11-03T19:06Z with its own `OVERRIDES:` marker, the record is live-armed (`"armed": true`), and **PR #11064** (open, `BLOCKED`) exists precisely to purge that superseded instruction from docs — which this plan would have re-seeded. Fixed in §2.1, §3 (the "TWO serialisation points" claim was **wrong**: there is ONE, and #9548 is a dependent) and §5 row 9. **F2**: a quote dropped `this form` mid-phrase against `record_projection.py:220`. **Census (§1.2)**: open-issue population measured at **1691**, not the 300 a capped sweep returns; no issue or PR proposes a declared-type guard; `collision_preflight 7879` → **CLEAN**; and it surfaced **#10852** (the `:Source.summary` carrier was called an orphan and is in fact an open PR) and **#11004** (the egress census, which must not be folded into G5). |
| 3 | `docs` check repair + external verification of this document's load-bearing claims (fresh context, at `a5e1d721f`) | **4 issues (1 × P1, 3 × P2), all verified against the ledger and the tree.** (1) **P1** — §2's G5 row and §3's dispatch line still said *three* measurements while §5.1's ruling enumerates **five**: the cycle-2 fix edited §5.1 only, so a lane following §3 would commission 3 of 5 and could report G5 done short of (3)–(5) — exactly the self-inflicted staleness row 2b describes. Fixed at both sites, both now pointing at §5.1. (2) **P2** — §0 cited a "§10" this document does not have (it ends at §9); now names §9's log with §1.1/§7.1–7.2. (3) **P2** — §5.1 said "two in-flight PRs" over one PR plus one tracking issue, and ranked **#11004 OPEN** 33 minutes after it merged (`d1806ab5d`, 2026-11-05T17:55Z); re-counted and re-ranked, with the census recorded as landed-but-not-run. (4) **P2** — §1.1's list of where `#10620` still appears omitted this epic's own `08-hosted-prerequisites.md` (13 occurrences on 11 lines, one of them the line §1's **302** figure is quoted from), and **#10864 does not cover it**; now listed and declared a residual with no carrier. The `docs` failure itself was six markdownlint findings (4 × MD018, 1 × MD040, 1 × MD055), reproduced with the CI's own pinned linter and cleared. |
| 4 | re-review of the cycle-3 fixes (fresh context, at `ec1660408`) | **One P2, and it was introduced by cycle 3's own fix.** §1.1 said the `#10620` misnumbering appears in `08-hosted-prerequisites.md` "three times"; the measured count is **13 occurrences on 11 lines** (15 including the two legitimate pin-filename mentions) — an error that understated the residual ~4×, in the sentence a lane defers the work on, and it also reached the cycle-3 commit message. Corrected at §1.1; cycles 1 and 2's findings were re-verified as genuinely fixed, and the other three cycle-3 fixes held. |
