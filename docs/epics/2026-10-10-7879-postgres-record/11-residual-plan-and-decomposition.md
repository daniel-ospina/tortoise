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
`641e5f1b3`, twelve commits earlier, and **three of its load-bearing rows turned out stale** — #10185 closed
inside that window (2026-11-05T04:54Z), while **#10671 was already closed and the `search_keys` narrowing had
already landed in the batch fold `a6e20f05b` when the first reading was taken** (both 2026-11-03), so two were
stale before the window opened, not during it. Two more were **simply wrong** (#10620 is an unrelated docs PR;
the detection machinery this plan first called "existing" **did not exist where it was measured** — it landed
later, by #10726, and §7.1 states which half of that correction still holds). §9's log records the review
rounds that corrected a **claim in this plan**, with the commit that landed each — **from row 6 onward; the
bound is deliberately unstated, since a range written "6–N" had to be re-edited every time a row landed** —
while §1.1 / §7.1–7.2 carry the corrections themselves, so the next lane does not re-derive them. **A round
whose only effect is on this log's own wording, a tally, or a line wrap is recorded in its commit rather than as
a row, and a fix that a later commit sharpens is recorded in that commit's message** — so the log does not grow
one row per row, and no enumeration in this paragraph is closed.

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
sub-issue links (13, all closed, before this lane added 23 — 36 in total, **21 open / 15 closed**: #10683 closed
when its answer was recorded, and **#9548 closed 2026-11-06T01:57Z** by `#11109`'s merge — landed on `main`
and, since the 2026-11-06T04:24:26Z deploy (`ba87797c`), **SERVED** (`record_writes_applied` is published on
`/health`); re-measured 2026-11-06T05:14Z with `gh api …/issues/7879/sub_issues --paginate
| jq -r '.[].state' | sort | uniq -c`, and the pair moves as issues close, so read it as a dated census) and the issues
whose *body* carries an
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
| the two migrations | present in the repo, **"unapplied in production" as of the 2026-11-05 readings below — RE-MEASURE before relying on it.** The image serving at the **2026-11-06T02:27Z** `/health` read (`c3af2d018`, run `42135045025`) ran the drift gate at 2026-11-06T01:02:43Z and it answered `OK (no blocking drift)` with **no** `BLOCKING repo-ahead` and **no** `warn-only repo-ahead` line — and the script prints both whenever the sets are non-empty, so `REPO_AHEAD` was empty then. ⚠️ What that shows is that *the gate no longer evidences an unapplied state*; it does **not** show the DDL physically ran (that needs its own read of `supabase_migrations.schema_migrations` plus the objects). The reconciliation of this row, the *deploy* row's tally and the "one blocked deploy" verdict is filed as **#11129** | `20261102000001_graphs_promote_default.sql`, `20261102000002_record_point_search_keys_text.sql`; `gh run view 42135045025 --log`; live `/health` 2026-11-06T02:27Z |
| the deploy | **this row is DATED and its headline is no longer current.** #10896's own tally at filing: `[{"":2},{"cancelled":5},{"failure":13}]` = 20 runs, last good deploy 2026-11-03T02:53Z; **re-measured 2026-11-05: `[{"cancelled":3},{"failure":17}]`** = 20 runs, 0 successes **as of that reading**. ⚠️ **A deploy HAS landed since:** the live `/health` reports `build.commit_sha=c3af2d0188b80c3ef2772e9c2baef44b588fee42`, `started_at=2026-11-06T01:24:49Z` (read 2026-11-06T02:27Z), i.e. after both tallies — so "refused on every run" is false as a statement about today and stays true only as a statement about the window it was measured over | #10896; `gh run list --workflow deploy-hosted.yml`; live `/health` 2026-11-06T02:27Z |
| consequence in production | every point record refused → the row dropped → `public.relation` starved with it. ⚠️ **Date every reading:** `record_write_failures` is **per process**. It read `write_failed: 302` at ~19:17Z on 2026-11-02 (#10593, #10896); it read `{}` on the 2026-11-05T08:39Z boot, and because #9994 records that **there is no positive write counter**, an empty counter cannot distinguish "no refusals" from "no writes". ⚠️ **The channel now EXISTS on main — and, as of the 2026-11-06T04:24:26Z deploy, it is SERVED on production.** #9548's positive pair (`record_writes_applied` / `record_last_write_at` on `/health`) landed on **main** in PR #11109 (`74fdd9ca0`, merged 2026-11-06T01:57Z). Two dated reads bracket the delivery: at the 2026-11-06T02:27Z read the deployed image was `c3af2d018` (**started 2026-11-06T01:24:49Z — BEFORE that merge**), and its `record` block carried `record_write_failures: {"write_failed": 8}`, `degraded: false` and **no** `record_writes_applied` key; at the **2026-11-06T05:26:10Z** read the image was **`ba87797c`, started 2026-11-06T04:24:26Z** — a successor of `74fdd9ca0` — and the block now serves **`record_writes_applied: 0`, `record_last_write_at: null`** with `armed: true`, `degraded: false`, `status: ok`. ✅ **That is the "armed and never wrote" reading #9994 exists to make possible, now answered BY PRODUCTION**: an empty *failure* object alone never said it, and this pair does. ⚠️ The pre-delivery reading above (`c3af2d018`/`e74361912` serving **no** `record_writes_applied` key) stands on its own dates; what it does **not** support — and what an earlier revision of this cell concluded from it — is that the pair is **not served**. That conclusion is retired by the 2026-11-06T05:26:10Z read (#11128 review P2, measured 2026-11-06T05:22Z and re-measured 05:26:10Z). ⚠️ The delivery rode the first successful deploy of a commit **at or after** `74fdd9ca0` that was not superseded before it started, which is NOT necessarily the next one: `deploy-hosted.yml` sets `concurrency … cancel-in-progress: false`, and that flag protects the **running** run only — GitHub cancels a **pending** run by default when a newer run queues in the same group, which is the measured sequence (the merge's own deploy `42147195536` @ `74fdd9ca0`, created 2026-11-06T01:57:26Z, ended **cancelled**; `42151443480` @ `ba87797c`, created 2026-11-06T02:36:55Z, **succeeded** and shipped the pair). `/health` reads `ok: true` because nothing armed is refusing right now (`degraded: false`), not because the degraded verdict is undelivered — that verdict IS served on this image | #10593; #10896; live `/health` reads 2026-11-06T02:27Z **and 05:26:10Z**; `gh run view 42147195536 42151443480`; #9548 / PR #11109; #11129 |
| de-facto store | **FalkorDB**, 68,526 nodes / 195 MB resident, zero evictions | #10976 |

**⇒ The one-sentence verdict.** The record's correctness work is done and merged; **the epic's entire first mile
is one blocked deploy**, and until that deploy lands the record cannot be evidenced, priced, or used, and nothing
may be safely evicted from the cache. ⛔ **Do not read `/health`'s `migrations_head` as evidence either way:**
**#10896**'s own verification comment establishes it is *"the highest migration-shaped file THE IMAGE CARRIES"*
(`migrations_source: checkout`) — **it answers which migration the image ships, never which one has been
APPLIED.** The unapplied state is evidenced by the drift gate's refusal itself, which reads prod's
`supabase_migrations.schema_migrations` and names the two migrations as `BLOCKING repo-ahead`.

> ⛔ **DATED — NO LONGER CURRENT (this paragraph is the historical verdict, kept as written).** Both halves were
> re-measured on 2026-11-06 and neither holds today: the drift gate ran at **2026-11-06T01:02:43Z** inside run
> `42135045025` and answered **`OK (no blocking drift)`** with **no** `BLOCKING repo-ahead` line, and a deploy
> **has landed** since the tallies (`c3af2d018`, started 2026-11-06T01:24:49Z). So *"the drift gate's refusal
> itself"* is not available as evidence any more — read **§1's *the two migrations* and *the deploy* rows**, not
> this paragraph, for the current state (and note that neither shows the DDL physically ran). The reconciliation
> of this verdict with those rows is **#11129**, which is OPEN: until it lands, treat *"one blocked deploy"* as a
> dated summary of the window it was written in, never as a description of today.

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
**examples, not a census**: `git grep -l 10620` matches **26 files at this head** — **21 at the
measurement commit** — the difference being these two documents, which match because they discuss the number,
and **three files landed after the measurement**: `tests/test_supabase_cli_pin_10593.py` (#11100),
`tests/test_9548_record_search_keys_shape.py` (#10726's fold batch) and `tortoise/monitoring.py` (#11054, one
comment at `:803`). The first two were **missing from this paragraph's first version** and are named here now;
the third landed later still, which is why 25 — this sentence's earlier figure — no longer reproduces.
`tortoise/record_projection.py` carries it on
**23 lines** (`:267` reads *"#10620 — R4's remaining half: `public.point.search_keys` NARROWED to …"*), and
`tests/test_9078_record_written_by_production_path.py` carries it inside the **assertion message** at `:2367`
(*"… #10620 closed `search_keys`"*) — the text a future failing pin prints, and the very file this document
quotes at §7.2. `tortoise/hosted_api.py` (4 lines) and `config/ci-surfaces.yml` (the *"#10620: the record stopped dropping every point that carried
search keys"* comment — `:625` at this head, `:615` at `origin/main`; the number is the handle that moves,
which is why the quoted comment is the durable one — plus the *"#10620: same dual
registration as the guards above"* comment — `:3627` at the measurement commit; the number is a convenience every
rebase moves, so the quoted comment is the handle) assert it too.
So **even the sites named here are not the set**, and ⚠️ **none of the sites named here is carried today**:
`#10864` — named as the candidate home by this paragraph's first version, since removed — is scoped to three
OTHER post-#10648 contradictions, proposes no correction to the `#10620` **attribution**, and no open issue or
PR sweeps the number. It is **not** a home for this correction, and nothing else in this plan names one.
⚠️ **"No correction to the attribution" is not "no correction to a `#10620` occurrence", and the difference
lands on two of the sites this paragraph names.** `#10864`'s §2 rewrites `docs/infra-runbook.md:3517` — that
file's **only** occurrence, the `degraded` / `degraded_reasons` / `refusing` row — and the comment near
`tortoise/hosted_api.py:6503`, *"⛔ WHETHER THE RECORD IS LOSING ROWS RIGHT NOW
(#10185/#10620)"*, one of that file's **4** occurrences; the numbers #10864 quotes for it are its own head's
(`:6400`, and `:6435` at `443498e66` — both shifted by main, `:6435` → `:6503` by the time the sentence itself
landed in `5dc22c19a`, which is why it was stale at the head it shipped on), so the durable handle is
the symbol, not the number; its fix reads *"add the second clause (and the same
one-clause note at `hosted_api.py:6400`)"*. Both edits add the missing `refusing`-absence clause and neither
touches the number, so the residual is still uncarried — but a lane that reads a blanket *"proposes no
correction"* can leave the two lines most likely to be rewritten under `#10864` unflagged. Its other **two** texts
are not occurrence lines — `:1335` is a docstring (`tests/test_record_searchkeys_10620.py`; that file's 9
occurrences are `1/22/64/427/705/901/1004/1126/1160`) and §3 is the merged PR body — so the count is **one
of three texts**, and that one targets **two occurrence lines**, not none.
The sites this paragraph names are **eleven**, not five — the five code files the fix itself must edit
(`tools/ci_selection.py`, `tortoise/record_projection.py`, `config/ci-surfaces.yml`,
`tests/test_9078_record_written_by_production_path.py` and `tortoise/hosted_api.py`) — **plus two documents**:
`docs/infra-runbook.md`, whose single occurrence sits **on the very row `#10864` rewrites** — the `degraded` /
`degraded_reasons` / `refusing` field row it cites by line — so the one lane already holding that file is exactly
the lane that must be told these occurrences are in scope, **and the same holds for `tortoise/hosted_api.py`,
whose `:6503` comment `#10864` rewords too** — the holding lane is one lane, and before this round only the
runbook was named to it; and `08-hosted-prerequisites.md`, which is not "the
one" uncovered site but simply the one this epic owns — **plus two more the paragraph names and an earlier
version of this sentence dropped**: `tests/test_record_searchkeys_10620.py`, the pin whose **name** is where the
number spread from, carrying it on **9 lines** and contradicting itself about it — `:1` still opens *"#10620 — the
record dropped every point carrying `search_keys`, silently"* while `:22` corrects it in the same file *("#10620,
which is a docs PR, not this defect")*; and `tests/test_supabase_cli_pin_10593.py` (`:5` — *"the `point.search_keys` fix
of #10620/#10593"* — which **carries the attribution too**, so it is a correction target, not a passing
reference).
The **tenth** is `tests/test_9548_record_search_keys_shape.py` — **18 occurrences, and a correction target, not a
census entry**: its own lines repeat the falsified attribution (`:134` *"#10620 fixed it on the COLUMN side"*;
`:150` and `:199` *"`text` since #10620"*; also `:29-31`, `:408`, `:488`, `:550`, `:702`, `:814`) — landed by the #10726
fold batch after this census was measured. It is named because an enumeration that omits the newest
carrier is precisely the omission row 16(2) graded, and **because this paragraph's first version called it "a
census entry, not a correction target", which the round at `1bbadce90` falsified**: `#10620` is a CLOSED,
never-merged docs PR (`mergedAt: null`), so *"#10620 fixed it"* is the same misnumbering as `test_9078…:2367`,
not a neutral reference to it.
The **eleventh** is the append-only `supabase/migrations/20261102000002_record_point_search_keys_text.sql` —
named above as the **primary artifact a reader of this fix opens**, with **6 occurrences**, and the one carrier
the correction provably **cannot** travel in. It is counted because rows 15(2) and 16(2) both graded P2 on
"the census included it, the paragraph named it, and the enumeration dropped it", and this enumeration is of
*carriers named here*, and of the eleven **only the append-only migration cannot be corrected at all** — every
other one, all three test files included, carries the attribution and must be swept.
**The correction travels in #10683's recording comment** (where a reader of the decision looks), **cannot travel
in the migration**, and **has no sweep issue** — filing one, or adding this scope to `#10864`, is the residual
carrier's decision, and this document asserting either would be the false pointer this paragraph just fixed.
⚠️ **And that recording comment is itself a second live carrier of the falsified mechanism claim:** comment
`6663370867` line 45 still reads *"`record_projection._COLUMN_RE` already captures the type name"* — the wording
§7.1 falsifies. So the document that says *"the correction travels in #10683's recording comment"* points a
reader at a comment that repeats the error; §7.1 is authoritative and the comment needs the same one-line
correction `#11075`'s body does. ⚠️ **`08-hosted-prerequisites.md`
is a DECLARED residual, not a covered one:** #10864 is scoped to three other post-#10648 contradictions and does
not reach this one, so no carrier takes the **correction** — not the five code sites, not the runbook or the three
test files above, and not `#10864` itself, which rewords two of those lines without touching the number —
today, and a lane that reads that file meets the misnumbering unopposed.

### 1.2 Existing-work census — how duplication was ruled out (and one measurement that was not a census)

**The population is 1691 open issues** (`repository{issues(states:OPEN){totalCount}}` at `3ac6b1636`). ⚠️ **A
`gh search issues --limit 300` sweep returns exactly 300 and is NOT the population** — this lane's first pass used
one, and it was truncated; recorded here so the next lane does not repeat it.

| method | result |
|---|---|
| body-search for the **failure class** (`declared type`, `schema disagreement`, `22P02`, `InvalidTextRepresentation`, `DatatypeMismatch`, `type contract`) | candidates **#10984**, **#11001**, **#11081**, **#10695**, **#8929** — each read: all unrelated to a declared-type guard except **#11081** (the beta board, below) and **#10695** (the #10671/#10675 P2 lineage, which is a *diagnostic* fix, not a contract pin) |
| **open-PR** search for anyone already building a declared-type guard | **none** |
| `uv run python tools/collision_preflight.py 7879 --repo .` | **CLEAN** (exit 0, 7/8 surfaces; 1 advisory partial) — and it named the in-flight PRs this plan now adopts: **#11064** (still open) and two that have since **merged** — **#11004** (`d1806ab5d`) and **#10852** (`ba87797c4`) |
| the two **boards** that already track this territory | **#10977** (*"making the hosted graph usable"* — the spine, per-area state and in-flight PRs) and **#11081** (*"the beta path"* — 17 `P0-blocker` / 17 `P1-severe` open at 2026-11-05T16:10Z, carrying its own instruments and a *"do not re-derive"* instruction). **Both are cited, neither is re-derived here.** |
| does anyone already own **G5's byte measurements**? | body-searches for `postgres_storage_footprint`, `bytes per day` and *"the overlap"* return only #7879's own comment and #10000 — **the gap in §5.1 is real** |
| does this **compete with the epic's own earlier decomposition**? | **no — it updates it.** The 2026-10-31 pointer on #7879 recorded *"Gap = 0. No child filed, deliberately"*; five days later the epic's first blocker had no parent. This document is the delta from that state, not a second plan |

**⇒ #11075 is not a peer.** No open or closed issue proposes a declared-type contract, and the only prior artifact
of this class was the `_SCHEMA_DISAGREEMENTS` vocabulary — #9264 cleared only its `capture_redactions` entry;
the vocabulary went **empty** when its last entry (`search_keys`) closed in `a6e20f05b` / #10717 (§7.2).

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
  i.e. #10000's producer. **G2 therefore cannot PASS on that route without a #10000-family change** — that
  change is **#10852**, landed on `main` 2026-11-06, **carried in this branch and in the serving build**
  (§5.2). It is
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
  adb0c38b`). #9548 is **CLOSED** as of 2026-11-06T01:57Z — `#11109`'s merge (`74fdd9ca0`) landed the record's
  POSITIVE channel, which is Target (a)'s evidence — and it was never an owner gate. ⚠️ **The closure is
  landed-on-main and the channel is SERVED** — `/health` publishes `record_writes_applied` from build `ba87797c`
  (2026-11-06T04:24:26Z) onward, read on `443498e66` at 2026-11-06T06:57Z; §5 rows 4, 5 and 9 carry the same
  statement. ⚠️ **A served channel is still not evidence the hosted app writes today:** the counter reads `0`, so
  *"closed"* remains a statement about the code, not about rows landing. **PR #11064** (open, `BLOCKED`) is the doc sweep that purges
  the superseded instruction; this plan must not re-seed it — and the first draft did.

---

## 3. Dependency order

```text
#11075  declared-type guard
   ↑ NO GATE: DB-free, needs no deploy — start in parallel today. Its only link to #10896 is that the field it
guards is only *live* once the deploy lands.

#10896  (the deploy — "2 unapplied migrations", its title)   ← the first gate
   │
   ├── #10593  write_failed → 0 in production
   │      └── #9994  positive write counter                  (rows landed, not just "no longer failing")
   │
   ├── #10663  the record's verbs run in the deployed image
   │      └── #9276  reconcile on a schedule
   │
   └── #9567  CAPSTONE (G2)
          ├── #9548  ← NOT an owner gate: arming AUTHORISED 2026-11-03; **CLOSED 2026-11-06** (#11109 = Target (a)'s channel), **SERVED since the 2026-11-06T04:24:26Z deploy** (`ba87797c`)
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
arming is authorised (2026-11-03, `OVERRIDES:` the earlier postponement), the record is live-armed, and the issue
is **now CLOSED** (2026-11-06, `#11109`'s positive channel = Target (a)). What G1 still proves is that the closed
target is **observable in the hosted app**, which the closure on its own does not show. **The epic is therefore
not owner-blocked anywhere.** What it *is* blocked on is one deploy, and what remains unresolved for G2 is the
`:Source.summary` route in §5.2, which no deploy fixes.

---

## 4. Test-design gate — the map is current, and it is missing exactly one failure mode

The map is `01-test-design.md` (#7967, CLOSED, measured `main@eed2be8147` on 2026-10-10). Every group maps onto
an existing surface, so **it is not stale and is not re-filed**: G1 → **S1**+**S2**; G2 → **S9**+**S1**/**S3**;
G3 → **S1**+**S4**/**S5** — **and not exhaustively**: G3's row 11 (`#8607`) *is* the map's **W1b**, which
`01-test-design.md` assigns to **S6**, so a verification scope read off this line alone would drop it. Read each
child's own surface citation; this is a **lower bound**, not a scope. G4 → **S2**. G5 is an instrument, not a surface.

**The gap, and why a new child is earned:** S1's failure modes are *totality*, *idempotency* and *a carried
field not projected*. None of them fires when **the column's declared type refuses the value the journal
carries** — totality holds, the projection is idempotent, placement is right, and the row is dropped anyway.
That class has now cost rows twice. The delta is appended to `01-test-design.md` §10; its pin is **#11075**.

---

## 5. The decomposition

**24 existing issues carry this work; two are new** (26 rows: at the `3ac6b1636` snapshot **22 open / 2
closed** — #10185, #10671 — with **#10683 closed later that same day** (2026-11-05T16:31Z, by this lane) and
**#9548 closed since** (2026-11-06), so the live split is **20 open / 4 closed**; plus 2 new children, #11075 and #11129). Filing a peer for a known root is the defect this tracker
has already paid for once, so the decomposition's product is **navigation and order**.

| # | what | group | state at `3ac6b1636` | action |
|---|---|---|---|---|
| 1 | **#10896** deploy: 2 unapplied migrations *(its title — whether the premise still holds is #11129's, §1)* | G1 | OPEN | **linked + labelled** — the gate |
| 2 | **#10593** every hosted record write refused | G1 | OPEN | **linked**; the root is recorded in comment `6663361035` — ⚠️ **the body still asks for that investigation**, so the comment, not the body, is the answer |
| 3 | **#11075** the declared-type guard | G1 | NEW | **filed** |
| 4 | **#9994** positive write counter | G1 | OPEN | the channel **landed on main** (PR #11109, merged 2026-11-06T01:57Z) **and is now SERVED**: the deployed `/health` publishes `record_writes_applied: 0` / `record_last_write_at: null` — first on `ba87797c` (started 2026-11-06T04:24:26Z, read at 05:26:10Z) and, at the 2026-11-06T06:57Z read, on `443498e66` (started 05:57:25Z). ⚠️ The two earlier reads (`c3af2d018` @ 02:27Z, `e74361912` @ 02:55Z) were **both pre-merge** and served no `record_writes_applied` key — that dated observation stands; the absence claim it supported does not. The issue is still OPEN; closing it is the issue's own decision, not this row's |
| 5 | **#10185** the silent-arming alarm | G1 | **CLOSED** | recorded: landed on main, **and DELIVERED** — the deployed `/health` serves `degraded` / `degraded_reasons` / `refusing`, all clear on build `443498e66` (`degraded: false`, `degraded_reasons: {}`, `refusing: null`, read 2026-11-06T06:57Z). ⚠️ The earlier "undelivered by #10896" note is superseded by that measurement |
| 6 | **#10683** the `search_keys` decision record | G1 | OPEN at `3ac6b1636` — **CLOSED 2026-11-05T16:31Z (this lane)** | answer recorded in comment `6663370867` — shape A + the `OVERRIDES:` marker + three factual corrections (§6) |
| 7 | **#10671** the losing shape | G1 | **CLOSED, unmerged** | none |
| 8 | **#9567** capstone | G2 | OPEN, `owner-decision` | linked; **#9567's own body lists `#9548` in `Blocked by`** — that edge is now **satisfied** (`#9548` CLOSED 2026-11-06, `#11109`), leaving the deploy (`#10896`) as the only live one: `#9548` is a DEPENDENT, not a gate (see §3) |
| 9 | **#9548** arm the record (R1) | G2 | OPEN at `3ac6b1636` — **now CLOSED 2026-11-06 (#11109)** | linked; **do not re-post the superseded "postponed" ruling** — the record is already armed, and **PR #11064** is the open sweep that purges it. ⚠️ **The closure is landed-on-main and the channel is SERVED** — `record_writes_applied` is published on `/health` from build `ba87797c` (2026-11-06T04:24:26Z) onward, read on `443498e66` at 2026-11-06T06:57Z. A served channel is still **not** an observation that the hosted app writes: it reads `0` |
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
| 26 | **#11129** the "blocked deploy / 2 migrations unapplied" premise vs. the deploy that shipped `c3af2d018` | G1 | **NEW** | **filed** by PR #11128 — §1's *the two migrations* and *the deploy* rows defer to it, and the one-sentence verdict above (§1) now carries a dated pointer to it |

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
| **the `:Source.summary` prose route** | the committed capstone run (`docs/verify/2026-10-31-record-capstone.md`, #10456) returns `PARTIAL`, and this is one of its open routes: the capture path (`sdk._session_source_metadata`) writes a transcript slice into `:Source.summary` | ⚠️ **LANDED AND NOW CARRIED HERE:** **PR #10852** — *"the session :Source stops carrying turn prose — the summary is a topic lexicon"* — is that change, **MERGED 2026-11-06T02:36:52Z (`ba87797c4`)**. `ba87797c4` **is an ancestor of this head** as of the rebase onto `origin/main` `5dc22c19a`, and it is in the **serving build** (`443498e66`, started 2026-11-06T05:57:25Z) — so the route is no longer uncarried, and the earlier reading (*"not in this branch: the last `origin/main` merge here is `f1aa7362b`"*) is retired by the rebase, which leaves no merge commit to point at. **What that changes for G2:** the committed capstone run predates the change, so its `PARTIAL` on this route is a **stale measurement**, and G2 needs the run REPEATED on a build carrying `ba87797c4` — not an owner admission for a change the branch already has. Whether the change is *in scope* is a separate question from whether it is *carried*: it belongs to #10000's family, which §2.1 excludes from this epic, so if the re-run still returns `PARTIAL` the owner must either accept it as NOT MEASURED in writing on #9567 or admit #10852 here |

⚠️ **This plan's first draft overstated this as "no path removes it".** A merged PR does. The gap is real but it is a
**dependency on work that has now landed and is carried here** (until the rebase it was landed-but-uncarried), not an
orphan — and a decomposition that called it an orphan would have sent a
lane to re-implement #10852.

**This is a G2 row, not a G3 row** — G2's PASS is what it blocks. It was billed to G3 in this plan's first draft,
which is exactly the kind of mis-grouping that makes a "blocked" item look parallel-safe.

**Declared, not absorbed:** `#4333` (the true per-node cost — `tortoise/quota.py` carries an `OVERRIDES:` line;
owner territory), `#10000`, `#10017`, `#10976`, `#5090`.

---

## 6. The recording obligation on #10683 (a decision already taken, **and relayed**)

**#10648 merged shape A** — the column narrows to `text`, because the producer's form is authoritative and
nothing reads the record's copy as JSON. **#10683's *body* still poses** the A/B/C question — the issue itself
is CLOSED and the answer is comment `6663370867` (§5 row 6) — and that body states that
**whoever records the answer owes an `OVERRIDES:` line**, because #9264's scope note characterised this field as
*"not a type narrowing"*.

**⇒ Recorded and the issue closed, in ONE comment** — `#10683` is **CLOSED** (`2026-11-05T16:31Z`) on comment
`6663370867`, which carries shape A, the `OVERRIDES:` marker and the three corrections below. ⛔ **Do not re-post
this**: a lane that re-runs the recording obligation acts on a closed issue. The three corrections, recorded there
because the issue body still misdirects a reader:

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

## 7. The new children — #11075 (specified below) and #11129 (reconciliation carrier)

**#11129 is the second new child** (§5 row 26). It carries the reconciliation of §1's dated rows and the
one-sentence verdict above; it names no code change, so this section does not specify it further. Everything
below specifies **#11075**.

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

### 7.1 ⛔ Correction: the mechanism this plan called "existing" did not exist where it was measured

⚠️ **Review cycle 1 measured `grep -rn jsonb_columns` → zero code hits, and that was TRUE at the commit it
measured (`3ac6b1636`) — it is FALSE at this head.** `git grep -c jsonb_columns -- '*.py'` → **29 hits across 2
files**; `jsonb_columns()` is defined at `tortoise/record_projection.py:1607`, landed by `e74361912` (#10726's fold
batch), which is in this branch through the rebase onto `origin/main` `5dc22c19a` (`f1aa7362b` named the merge
that used to carry it — a rebase leaves no such commit). So #10671's reader **did** land, by another route, together
with the parsers this section said had to be built. **What survives — and is the reason this section still
exists — is the narrower claim about `_COLUMN_RE` itself, below.**

What *does* remain true of `_COLUMN_RE`:

- ⚠️ **A comment in `tortoise/record_projection.py` (`:222-226`) still says a **forward migration narrowing a
  column's TYPE** is a form *"the readers do not parse this form, and deliberately"* — and **that comment is
  stale at this head**, because `_ALTER_COLUMN_TYPE_RE` (`:1529`) parses exactly that form. It is quoted here
  because it is the sentence this plan's cycle-1 correction was built on. §7.1's own subject is narrower:
  `_COLUMN_RE` **matches** the declared type
  in a **non-capturing** group, and `columns = {m.group(1) …}` keeps only the **name** — so there is nothing
  being discarded, and the type is **not** captured *by `_COLUMN_RE`*. At this head the capture exists in a
  **separate** pattern (`_COLUMN_TYPE_RE`, `:1502`), not in `_COLUMN_RE`, so anything reusing `_COLUMN_RE` for
  the type still has to lift it. And `_COLUMN_RE`'s type vocabulary is a **hardcoded
  seven-term allow-list** (`text|jsonb|integer|boolean|bigint|double precision|timestamptz`), so a
  derivation built on it **silently skips any column of another type** — which is why #11075's closure target
  ("a column added to a migration with no expectation reds") holds **only for types that vocabulary
  admits**, and the issue says so.

**Consequence for #11075, and it is now the OPPOSITE of what this section first drew:** it said an implementer who
follows "reuse `jsonb_columns()`" would read `search_keys jsonb` from the *original* migration and **red a
correct tree**. Measured at this head, **`jsonb_columns()` returns 11 names and `search_keys` is not one of them** —
because the reader applies `_ALTER_COLUMN_TYPE_RE` over the base `CREATE TABLE`. Reusing it is therefore
**correct and cheap**, and the warned outage **cannot occur**; the earlier warning was true of a tree in which the
ALTER form did not exist.

**What is actually new work, restated for this head:** the declared-type **capture and the `ALTER COLUMN … TYPE`
form have landed** (`_COLUMN_TYPE_RE` `:1502`, `_ADDED_COLUMN_TYPED_RE` `:1513`, `_ALTER_COLUMN_TYPE_RE` `:1529`),
so the earlier "capture one, build three" is **satisfied for the jsonb question**. What remains is the **type
vocabulary** — still a hardcoded seven-term allow-list — and the **value→declared-type predicate** for types
outside it. #11075's `db` domain stays **complex** on that narrower remainder, not on the four items this section
first enumerated.

### 7.2 ⛔ Correction: this class-detector was deliberately deleted once

`tests/test_9078_record_written_by_production_path.py` records a **two-step** removal: #9264 closed having cleared
only `capture_redactions`, the vocabulary still held `search_keys` at the commit before its deletion, and it
became **empty and then "gone rather than parked"** only when that last entry closed — `a6e20f05b` (#10717),
recorded in history under the misnumbered `#10620` — the project's "a pin that outlives its defect" rule, applied
at the LAST entry. #11075 must say so, and say why a **migration-derived** check differs from the deleted
**hand-maintained vocabulary**: a derived check cannot go empty (it is closed over the migration set), so it
cannot outlive its defect in that way. Without that paragraph the next lane reads #11075 as re-litigating a
recorded removal. ⚠️ **And #11075's body carries the ONE-STEP version of that provenance today** — *"records that
`_SCHEMA_DISAGREEMENTS` was emptied and then made 'gone rather than parked' **when #9264 closed**"* — so that
body's contradicted statements now number **four**: this one, its `carve_out:` registration order, its
`_COLUMN_RE` "**does** capture" claim and its `grep -rn jsonb_columns` "**zero code hits**" claim. **All four are
named here and in `01-test-design.md` §10**, which gives the same total; the same one-line correction is owed to
it, and §7.1's and this section's text are authoritative until it lands.

---

## 8. Verification of this decomposition

- [ ] **every open issue carrying an `Epic: #7879` field OR a sub-issue link is in §5 or in §2.1** — stated this
      way round, because "every issue in §5 has a link" cannot catch an omission (review cycle 1 found two)
- [ ] `gh api repos/daniel-ospina/tortoise/issues/7879/sub_issues --paginate` contains the live carriers
      (#10896, #10593, #11075, #9567, #9548, #10977, …), not only closed ones — **measured at that pass: 36
      linked, 22 open / 14 closed**; ⚠️ **live at this head: 21 open / 15 closed**, #9548 having closed
      2026-11-06 by `#11109` — **verified 2026-11-06T05:14Z**
- [ ] **every P0 named in this plan actually carries the `P0-blocker` label** — #10896 arrived with **zero**
      labels; it and #10593/#10977 now carry `P0-blocker` + `team:epistemic-team`
- [ ] the decomposition comment on #7879 carries a provenance line and names #10896 as the first gate
- [ ] #10683 carries the `OVERRIDES:` line **and** the three factual corrections from §6
- [ ] #11075's body passes the issue-creation body lint (Objective present; ≥2 observable Indicators; ≥1 Target
      each) and carries a verification checklist derived from S1/S2
- [ ] #11075's body names the **reader that was missing when it was filed** — it exists at this head — and the deliberately-deleted vocabulary (§7.1, §7.2)
- [ ] the capstone (#9567) is named as the Verify-stage gate; **no second capstone is filed**; its #9548
      dependency and #9559's `D11/D12`-only conditionality are both drawn
- [ ] criterion 3 is recorded as **WAIVED by §2.0**, never as met
- [ ] a fresh-context reviewer returns `NO ISSUES FOUND` on (a) this plan's coherence and (b) §5's MECE property

## 9. Review log

| cycle | scope | verdict |
|---|---|---|
| 1 | plan coherence + MECE + the new issue (fresh context — #11075, the only new child at that round; #11129 was filed later, by PR #11128) | **15 issues: 10 × P1, 5 × P2.** All 15 verified against the tree and fixed: (1) `jsonb_columns()` does not exist — §7.1; (2) `#10620` misattributed — §1.1; (3) narrowing credited to the wrong PR — §1; (4) MECE gap #9129/#9997 — §5 rows 19–20; (5) criterion 3 wrongly "met" — §2; (6) missing `#9567 → #9548` edge — §3; (7) #8607 wrongly escalated — §3; (8) G2 blocked by an out-of-scope #10000 route — §2.1; (9) G5 has no closable carrier — §5.1; (10) #9559 conditional — §3; (11) #11075 wrongly serialised — §3; (12) two counts not reproducible — §0/§1; (13) #10683's wrong migration filename — §6; (14) deleted-vocabulary disclosure — §7.2; (15) carve-out config rating — #11075 body |
| 2 | MECE re-verification + fix verification (fresh context) | **12 issues (4 × P1, 8 × P2).** All verified and fixed: (1) the `:Source.summary` carrier §2.1 promised was absent from §5 and mis-grouped — new **§5.2**; (2) #10593's body was never corrected — comment `6663361035` posted, row re-worded; (3) the class was named by `22P02` while #9264 measured `DatatypeMismatch` — §7 re-stated by property; (4) G5's ruling names **five** measurements, not three — §5.1; (5) the §5 preamble count did not reproduce — 24 existing + 1 new; (6) #9129/#9997 were claimed linked but were not — **now linked**; (7) #10896 carried **zero** labels — now labelled; (8) #11075 was still drawn inside the deploy subtree — **hoisted**; (9) present-tense production state — §1 now dates every reading and notes `{}` is not green without #9994; (10) `db: complex` rested on a claim the code contradicts — §7.1 corrected and the closure claim bounded by the 7-term vocabulary. ⚠️ **The round logged as row 6 below** (branch commit titled "review cycle 4", `e16697a92`) **then falsified the wording that correction introduced** (`_COLUMN_RE` does *not* capture the type — see §7.1), and **#11075's body still carries that falsified wording**: it reads *"its `_COLUMN_RE` **does** capture the declared type and then keeps only the column **name** — so capturing the type is a small change"*. §7.1 is authoritative; a lane must not act on the issue text alone, and the correction was posted on #11075 (comment `6668307013`) — the body still carries the text; (11) "the owner approved the shape" had no artifact — replaced with the relay that exists, citing the #7879 comment; (12) "the misnumbering stops here" was false — the migration header still carries `#10620` and is append-only, so §1.1 now says where the correction does and does not travel |
| 2b | `[VGATE]` content verification of the two committed artifacts (fresh context, before commit) | **5 issues, all in this document's own text and four of them introduced BY the cycle-2 fixes:** (i) §5 row 6 still read `#10683 OPEN` while this lane had already closed it; (ii) §0's "added 21" was the pre-cycle-2 figure — measured 23 added, 36 total, 22 open; (iii) `01-test-design.md` §10 and §7.1 **contradicted each other** on whether type extraction is built or captured — reconciled to "capture one, build three", stated once in §7.1 and cross-cited; (iv) §7.1 carried a truncated sentence (`the reader must be.`); (v) `#9264`'s duration cell said "closed same day" while its timestamps give **2.9 days** — corrected, and `#10593`'s cell now reads "~3 days and counting". This is the class a commit-gate exists for: every one was a self-inflicted staleness produced by fixing the previous round |
| 2c | `[VGATE]` round 3 **+ the existing-work census the owner asked for** | **One BLOCKING finding: F1 — this plan carried #9548's SUPERSEDED "hosted postponed" ruling as live.** The owner authorised arming on 2026-11-03T19:06Z with its own `OVERRIDES:` marker, the record is live-armed (`"armed": true`), and **PR #11064** (open, `BLOCKED`) exists precisely to purge that superseded instruction from docs — which this plan would have re-seeded. Fixed in §2.1, §3 (the "TWO serialisation points" claim was **wrong**: there is ONE, and #9548 is a dependent) and §5 row 9. **F2**: a quote dropped `this form` mid-phrase against `record_projection.py:220`. **Census (§1.2)**: open-issue population measured at **1691**, not the 300 a capped sweep returns; no issue or PR proposes a declared-type guard; `collision_preflight 7879` → **CLEAN**; and it surfaced **#10852** (the `:Source.summary` carrier was called an orphan and is in fact an open PR) and **#11004** (the egress census, which must not be folded into G5). |
| 3 | `docs` check repair + external verification of this document's load-bearing claims (fresh context, at `a5e1d721f`) | **4 issues (1 × P1, 3 × P2), all verified against the ledger and the tree.** (1) **P1** — §2's G5 row and §3's dispatch line still said *three* measurements while §5.1's ruling enumerates **five**: the cycle-2 fix edited §5.1 only, so a lane following §3 would commission 3 of 5 and could report G5 done short of (3)–(5) — exactly the self-inflicted staleness row 2b describes. Fixed at both sites, both now pointing at §5.1. (2) **P2** — §"Measured at" cited a "§10" this document does not have (it ends at §9); it now names §9's log with §1.1/§7.1–7.2. (3) **P2** — §5.1 said "two in-flight PRs" over one PR plus one tracking issue, and ranked **#11004 OPEN** 33 minutes after it merged (`d1806ab5d`, 2026-11-05T17:55Z); re-counted and re-ranked, with the census recorded as landed-but-not-run. (4) **P2** — §1.1's list of where `#10620` still appears omitted this epic's own `08-hosted-prerequisites.md` (13 occurrences on 11 lines, one of them the line §1's **302** figure is quoted from), and **#10864 does not cover it**; now listed and declared a residual with no carrier. The `docs` failure itself was six markdownlint findings (4 × MD018, 1 × MD040, 1 × MD055), reproduced with the CI's own pinned linter and cleared. |
| 4 | re-review of the cycle-3 fixes (fresh context, at `ec1660408`) | **One P2, and it was introduced by cycle 3's own fix.** §1.1 said the `#10620` misnumbering appears in `08-hosted-prerequisites.md` "three times"; the measured count is **13 occurrences on 11 lines** (15 including the two legitimate pin-filename mentions) — an error that understated the residual ~4×, in the sentence a lane defers the work on, and it also reached the cycle-3 commit message. Corrected at §1.1; cycles 1 and 2's findings were re-verified as genuinely fixed, and the other three cycle-3 fixes held. |
| 5 | `[VGATE]` pre-merge primary-source verification of §1.1's census (this lane, at the post-rebase head). ⚠️ **Appended before the peer lane added rows 6–7 for cycles that landed EARLIER, so these ordinals follow review numbering, not commit order** | **One P2.** §1.1 cited `config/ci-surfaces.yml` at `:606`, `:3627`; `:606` is right, but `:3627` carries no `#10620` **at the head this round measured (`e16697a92`)** — it is the **#9685 installer-test** comment, four lines under the `test_runner_autoscale.py` entry at `:3623` — and at the head this PR now ships that number has drifted a third time (`:5516`'s comment), with the `#10620` one sitting at `:3731`. **The citation was exact when the round logged as row 6 below wrote it** — the pre-rebase commit `502c0810a`, written against `3ac6b1636`, where `:3627` *was* the `#10620` comment; rebasing the branch onto `af36e9240` shifted that comment to `:3664` (*"#10620: same dual registration as the guards above"*), 37 lines down — so this is the drift class row 8 names, not a number that round got wrong. The paragraph's other measurements all reproduce exactly: `git grep -l 10620` → **23 files**; `tortoise/record_projection.py` → **18 lines**, `:265` verbatim; `tests/test_9078_record_written_by_production_path.py:2367` → the assertion message; `tortoise/hosted_api.py` → **4 lines**; `08-hosted-prerequisites.md` → **13 occurrences on 11 lines** once the two pin-filename mentions are set aside (15 occurrences on 13 lines raw). Corrected at §1.1, and the wrapped bold span the citation sat in was rejoined. |
| 6 | fresh-context review at the pre-rebase head `43aad420d` — P1, census and §7.1 landed as `e16697a92` after the rebase; the §10 `#11075` residue and comment `6668307013` landed as `fddb8d4be` | **3 issues (1 × P1, 2 × P2), all verified before applying.** (1) **P1** §10 ordered registration in `carve_out:`, which is neither a classification (`classify_test_file()` reads only `manifest["surfaces"]`, so `integrity()` reds `manifest-integrity`) nor additive (`files -= carve` removes the file from every docker leg); measured 57 carve-out entries, all in a surface (**0** carve-out-only files), and the cited precedent is `api`+`core`, not a carve-out — §10 now names `test_record_schema_placement.py` (`core`). (2) the `#10620` census named three sites as the set — `git grep -l 10620` matches **23 files**, missing `record_projection.py` (18 lines) and the **assertion message** at `test_9078…:2367`; now examples, not a census. (3) `_COLUMN_RE` was said to capture the type "and discard it" — it has **one** capture group and the type is **non-capturing**, so `m.group(2)` does not exist; also two-item "new work" → three, and a circular `see §7.1`. ⚠️ **RESIDUE: `#11075`'s body still ORDERS the wrong registration** — its config row reads *"must be registered in `config/ci-surfaces.yml`'s `carve_out:` list or it never rides the URI-unset leg (#10648 needed the same registration)"*, on a precedent this document measures as false. `01-test-design.md` §10 is authoritative; the correction was posted on `#11075` (comment `6668307013`); **the line numbers cited there were pre-rebase** — the durable citation is the SYMBOL in `tools/ci_selection.py` (`classify_test_file()`, `integrity()`, `files -= carve`), because the numbers move with every merge: `:3237` / `:3630` / `:3578` at the review head `04d45b195`, `:3323` / `:3716` / `:3664` after merging `origin/main` for this PR. A follow-up comment records them |
| 7 | fresh-context review at `502c0810a`, landed as `e490b86e6` | **3 issues (all P2), all verified before applying.** (1) §2 row 8 said "#9567 … blocked by #9548" while §3 says in bold **"#9548 is a DEPENDENT, not a gate"** and draws it downstream of the deploy (§2.1 states the same fact as Target (a) being "downstream of the deploy, **not an owner gate**") — the F1 class, surviving in the row a dispatcher reads when ordering G2; row 8 now states which edge it is. (2) the cycle-2 log row still asserted the falsified "(type is captured …)" claim, and the correction left **#11075's body** carrying it verbatim — both now recorded as residues. (3) the G3→surface map was under-scoped in both documents: G3 row 11 (`#8607`) is the map's **W1b** = **S6** (`01-test-design.md:131`), dropped by "G3 → S1+S4/S5"; both now name S6, and **this plan** called the list a superset indicator — ⚠️ **that label was itself wrong and row 8 corrects it: the list OMITS a required surface, so it is a lower bound, not a superset.** `01-test-design.md` never carried the label; it only gained “plus **S6**” |
| 8 | fresh-context review at `27724034c` (post-rebase), landed as `72f567ad0` | **3 issues (all P2), each verified against the primary source before applying.** (1) §4 said the G3 surface mapping was "not exhaustively … a superset indicator, not a scope" while establishing that it OMITS S6 — a list that drops a required element is a **lower bound**, so the word said the opposite of the clause beside it; now "**lower bound**, not a scope". (2) the row this lane had appended as `5 [VGATE]` broke the table's own convention (rows `2b`/`2c` carry the marker in the SCOPE cell, not the ordinal), so it read as a cycle named "5 [VGATE]"; moved. (3) ⚠️ **four line numbers in `e16697a92`'s own commit message drifted with the rebase** — `tools/ci_selection.py` `:3201`, `:3592`, `:3541`, `:4354` are `3237` (`classify_test_file`), `3630` (`integrity`), `3578` (`files -= carve`) and the `push_legs` carve-out docstring, now `:4390` (`:4354` on this head is the `#6145` ratio comment). At `3ac6b1636` exactly two of the four were exact (`:3201` and `:4354`); `:3541` was off by one (the subtraction is `:3542`) and `:3592` was a blank line (`def integrity` is `:3594`). Accurate only in part, and stale the moment the branch moved — the same class this PR removes from the documents. A pushed message is not rewritable without a force-push, so it is recorded here; **the durable fix is to cite symbols, not line numbers**, which is what #11075's residue (row 6) needs too when it is carried. |
| 9 | the rounds that corrected this log's own audit trail — landed as `fddb8d4be`, `a81872a4f`, `6e770c57f`, `1b890f7b4` — **plus `f45dbac11`**, which corrected §7.2's provenance narrative rather than this table | **Five P2 and two content fixes — (b)–(f) in this table's own attributions, (a) in `01-test-design.md` §10 and this table's row 2, (g) in this plan's §7.2.** (a) **Content:** `01-test-design.md` §10 and row 2 said the `#11075` correction "belongs posted" in the present tense while it *had* been posted — both now say posted, with comment `6668307013`, and the body still carries the text so it stays a live trap (`a81872a4f`). (b) Row 2 attributed the `_COLUMN_RE` falsification to "cycle 4" **by the table's own numbering**, where row 4 is the round that found the `08-hosted-prerequisites.md` undercount — disambiguated to the branch commit title (`a81872a4f`). (c) Row 8 named `e16697a92`'s successor, which is row 7's commit `e490b86e6`, not row 8's `72f567ad0` (`6e770c57f`). (d) Row 8 said "five line numbers" where `e16697a92` cites **four** `tools/ci_selection.py` numbers, and claimed all were accurate at `3ac6b1636` where `:3541`/`:3592` were already off by one and two (`6e770c57f`). (e) Row 5 named `:3627` a `test_runner_autoscale` comment — it is the **#9685 installer-test** comment, and row 5's ordinal is a review number whose landing is a *descendant* of rows 6–7's (`1b890f7b4`). (f) Row 6 credited `e16697a92` with a fix that landed in `fddb8d4be`, which no row named (`1b890f7b4`). (g) ⚠️ **Not this table but this plan's §7.2:** both documents dated the `_SCHEMA_DISAGREEMENTS` deletion to `#9264`'s closure (2026-10-22), but `a6e20f05b^` still held one entry — the vocabulary went empty and *"gone rather than parked"* only when its **last** entry closed, in `a6e20f05b` (#10717), which history records under the misnumbered `#10620` (`f45dbac11`); all sites now give the two-step sequence. ⚠️ Rounds that correct **only this table** are recorded in their own commits rather than as further rows, so the log does not grow by one row per row. |
| 10 | the rounds that corrected §"Measured at", §1.1's citations and this table's own dates — landed as `8314fb188`, `e03e9de6a`, `9f0a61061`, **and this row's own commit `d1e2e9ff1`, which carries (f) and the final form of (c)** | **Five P2 and one P1, none of them a measurement — all in this document's own framing of its measurements; the numbers themselves reached their final form two rows earlier.** (a) §1.1's `config/ci-surfaces.yml` citation gave `:3664` under a `Measured at: 3ac6b1636` header, where that line is an unrelated comment — the `#10620` site was `:3627` there; now cites the anchor text plus both numbers. (b) The completeness claim at §"Measured at" was short by the content-changing round `f45dbac11` (§7.2's `_SCHEMA_DISAGREEMENTS` two-step) — row 9 now names it. (c) Row 5's "cycle 4" was the *branch commit's* label, not table row 4, so `502c0810a` now stands beside it. (d) **P1 — "stale within that window" was true for one of three rows:** `#10185` closed 2026-11-05T04:54Z inside the twelve-commit window, but `#10671` (2026-11-03T08:36Z) and the `a6e20f05b` narrowing (2026-11-03T18:32Z) both predate it, so two were already stale at the first reading; the header now says so. (e) `git grep -l 10620` is **23 files on this head and 21 at the measurement commit**, the delta being these two documents — given as 23 flat under a `3ac6b1636` header, the same defect (a) fixes. (f) Row 5's own "was exact" clause named `e16697a92`, the **post-rebase** twin whose tree already has the comment moved; it now names `502c0810a`, the pre-rebase commit the text was actually written against. |
| 11 | the round that corrected a closed issue's instruction and a self-referential conclusion — landed as `ca0672249` (the §6 retitle + past-tense imperative, and the `6663370867` live-carrier note) and this row's own commit `7b51482e8` ((1)'s body-scoping, (3) and (4)) | **Three P2 and one P1, all in this plan's own instructions rather than in any measurement.** ⚠️ **P1 — §6's heading said #10683 was "unrelayed" and its imperative told a lane to *record and close* it.** `gh issue view 10683` → **CLOSED** `2026-11-05T16:31Z`, on comment `6663370867` (`2026-11-05T16:30:58Z`), which carries shape A, the `OVERRIDES:` marker and §6's own three-row correction table; §5 row 6 had said so all along. Heading retitled "**and relayed**", the imperative is past tense with an explicit ⛔ "Do not re-post this", and the body line that still read "**#10683 still poses** the A/B/C question" is now scoped to the body — the state claim, not just the verb, was the trap. This is the class row 2b recorded catching for `#10683`'s *state* in §5; it survived one section down, in the section about #10683. (2) §1.1 says the `#10620` correction "travels in #10683's recording comment" — and `6663370867` line 45 itself reads *"`record_projection._COLUMN_RE` already captures the type name"*, the wording §7.1 falsifies. Registered there as a second declared live carrier beside `#11075`'s body, with the comment id, because a pointer to the error is worse than no pointer. (3) §1.1's closing sentence said a lane fixing the residual "from this paragraph alone" leaves the assertion message wrong — but the paragraph had just been given that site, so the conclusion contradicted its own content; now states the real residue (the named sites are not the set). (4) Row 7 said **both** documents "call the list a superset indicator"; `01-test-design.md` never carried the word at any commit (`git log --all -S'superset'` on it is empty) — only this plan did, and row 7 now says which. |
| 12 | the fresh-context reviews at `5cb525775` and at the head `04d45b195` — this row's own commit | **Three P2, each verified against the primary source before applying, plus one reviewer note falsified and one pre-existing residue filed.** (1) **§1.1 named the one uncovered site and the sweep that covers it — and was wrong on both.** It read *"of the sites named, the one **no carrier takes** is this epic's own `08-hosted-prerequisites.md`"* while telling a reader the correction *"belongs with the sweep in #10864"*. Measured: `#10864`'s body proposes three texts, **two of whose fix targets ARE `#10620` occurrence lines** (`docs/infra-runbook.md:3414`, `hosted_api.py:6404`) though neither correction touches the attribution — ⚠️ **this cell's original "none a `#10620` occurrence" was false as a measurement; corrected by row 17** — and **none** of the named sites is carried — so a lane could read "the one" and leave the four code carriers named in the same paragraph (`tools/ci_selection.py`, `tortoise/record_projection.py`, `config/ci-surfaces.yml`, `tests/test_9078…:2367`). §1.1 now says no carrier takes any of them, that `08-hosted-prerequisites.md` is simply the one this epic owns, and that **no sweep issue exists** — filing one, or adding the scope to `#10864`, is the residual carrier's decision. (2) **The citations this PR itself added were stale in the tree it merges into.** §1.1's *"`:3664` on this head"* and row 6's *"at HEAD `classify_test_file()` is `:3237`, `integrity()` is `:3630`, `files -= carve` is `:3578`"* were true of the branch tip and false at the merge: after merging `origin/main` (#11100) the same lines are `:3678` and `:3323`/`:3716`/`:3664`, and `git grep -l 10620` went **23 → 24** (`tests/test_supabase_cli_pin_10593.py`, landed by #11100 after the measurement). Row 6 now cites the **symbols** and both pairs of numbers as pinned observations; §1.1 keeps only the ref-pinned `:3627` and says the quoted comment text is the handle. (3) **The PR body's three `tools/ci_selection.py` citations** (`:3211`, `:3615`, `:3541`) resolved to unrelated code in **every** tree measured — the symbols it already named are the citation now, and the body points at this log for the enumeration. ⚠️ **A reviewer note falsified, recorded so it is not re-applied:** the claim that the pre-rebase heads (`a5e1d721f`, `ec1660408`, `502c0810a`, `43aad420d`, `2552192e6`) cannot be resolved by any reader is wrong — `gh api repos/daniel-ospina/tortoise/commits/<sha>` returns a full sha for each — so no "pre-rebase heads are unresolvable" header note was added; rows 6 and 8 already name the landing commits. ⚠️ **A pre-existing residue, filed rather than fixed here:** `01-test-design.md` §S9's `sdk.py:4598` (definition) + *"exactly 2 callers"* is false at every ref — the only definition is `sdk.py:10818`, the call-site count is **36**, and it was already `:4649`/`:5822`/`:14968` when #7992 wrote it — **#11118**. |
| 13 | the fresh-context review at `53b9c3afd` — the round that caught two claims stated in the present tense after the sentence carrying them had been corrected | **Two P2 and one residue clarified — both are the class this log exists for: a correction landing in one place while the wording it falsified survives in another, one clause away.** (1) **§1.1's appositive pointed at its own deletion.** It read *"`#10864` — named elsewhere in this plan as a candidate home —"*, but the "elsewhere" was the very sentence row 12 removed: `grep -n 10864` on this plan returns the appositive itself, the *"adding this scope to `#10864`"* sentence, the declared-residual sentence, §3's G3 parallel list, §5 row 18 (**`#10864` three texts disagree with the code**), and the §9 rows that quote the removed text — and **none** names a candidate home. ⚠️ **The tally itself is ref-bound and is given in row 16, not here:** every §9 row that names the number raises it, which is the same law row 15 states for the other token. ⚠️ **Cited by anchor, not ordinal:** the ordinals this row first carried were the *review* head's, and two of them landed on the wrong target one commit later — one read `:273` for §5 row 18, whose neighbour at that line is §5 row **16** (`#9881`), in the very sentence whose point is that §5 row 18 is what a reader finds. The nearest live thing a reader finds is §5 row 18, an OPEN G3 row titled *"three texts disagree with the code"*, which is what a `#10620` residual also looks like — so the appositive re-manufactured the false pointer row 12(1) had just removed. It is now past tense and self-locating (*"named as the candidate home by this paragraph's first version, since removed"*), with an explicit "nothing else in this plan names one". (2) **The PR body's item 6 said "Both now name S6 and call the list a superset indicator" — false in the present tense, twice over.** `grep -c superset` is **0** in `01-test-design.md` (it never carried the phrase at any commit — row 11(4) measured that), and in this plan every occurrence sits inside a §9 row: **three at `53b9c3afd`, four at `246099892`** because this row is the fourth. ⚠️ **The figure is given per ref and never as "at the head", because a row that quotes the count raises it** — see row 14, where quoting it here made the next head read five; the live sentence — §4's *"a **lower bound**, not a scope"* — carries neither the phrase nor a claim to it, and row 8(1) records the superset label as **wrong** for the stated reason. The body now says the list is a lower bound and notes the earlier label was corrected. ⚠️ **Residue clarified, not filed:** open fold PR **#10226** incidentally deletes six `#10620` lines from `test_9078…` while reverting the vocabulary to a live dict — it does not correct the misnumbering, so *"no open issue or PR sweeps the number"* stands, but the number is not safe from incidental edits. |
| 14 | the fresh-context review at `246099892` — the round a row cannot audit for itself | **Four P2, and the first two are the log auditing its own newest row, which is why they count.** (1) ⚠️ **Row 13's citations were head-relative ordinals, and two of them landed on the wrong target one commit later** — the class rows 8(3), 10(a) and 12(2) each already graded P2, in the row that graded it. Measured at the head that row ships at: `grep -n 10864` returned the sites row 13 now names by anchor — **eight** there, `7` at the head it reviewed (row 16 gives the whole series) — and the ordinals it named were `53b9c3afd`'s; `81a82f56b` and `246099892` each inserted a line inside §1.1, so the cited `:273` became §5 row **16** (`#9881`) rather than row 18, and `:214` became the deploy tree rather than §3's G3 parallel list. `grep -c superset` was likewise the review head's **3**, not the **4** row 13 asserted — the fourth occurrence being **row 13 itself**, and the head that carries row 14 holds **five**. The figure is therefore given per ref in both rows, never as "at the head": a row that quotes the count raises it. Row 13 now cites **anchors and row ids**, not ordinals, and gives the count for the ref it measured instead of a head claim. (2) **The PR body's completeness claim covered *"rows 5–12"* while the document this same commit edited had just moved to *"rows 6–13"** — one artefact over, the identical "fixing one site left the wording standing in another" failure that the round before it was about; the body now names the same range as the document. ⚠️ **Neither artifact keeps a bound now:** a "6–N" range has been re-edited on every row this log grew, so both the body and this document's header name the log rather than a span that must be hand-synced — the same reasoning that removed the ordinals in (1). (3) ⚠️ **`#11075`'s body carries a THIRD contradicted statement that neither document registered:** it says `_SCHEMA_DISAGREEMENTS` was emptied and made *"gone rather than parked"* **when #9264 closed** (body line 44) — the **one-step** attribution §7.2's own two-step provenance corrects. Section 7.2 of this plan and of `01-test-design.md` now register it beside that body's `carve_out:` order and its `_COLUMN_RE` "**does** capture" claim, making three live carriers in one body. ⚠️ **A fourth, `zero code hits`, was found by row 26; both sections now enumerate all four** — so this cell's "three" is the round's partial registration, not the body's total. (4) **The PR body cited `tortoise/record_projection.py:1344-1346` bare**, unpinned to any ref, in the very section whose item 1 declares the symbol-citation policy — the number is accurate at the head but it is the one most likely to rot (it is the citation that stops an implementer writing `m.group(2)` and getting `IndexError`) and `_COLUMN_RE` is already named in the same sentence; the body now cites the symbol, as it does for the map's W1b row and the `test_9078…` assertion message. ⚠️ **Not a finding, checked:** §1.1's *"nothing else in this plan names one"* sits nine lines from *"adding this scope to `#10864` … is the residual carrier's decision"* — current state vs a future decision, not a contradiction. |
| 15 | the fresh-context review at `6703e7355` — the round where the log's own tally bit itself, and §1.1's site count proved short of its own list | **Two P2, each a smaller instance of the class the row before it graded — which is the honest shape of a converging loop, and also its warning.** (1) ⚠️ **The count of the word row 8(1) corrects was off by one at the head that carried it, and its pronoun named the wrong row.** Rows 13 and 14 quote the `grep -c` figure, and *quoting it raises it*: measured **3** at `53b9c3afd`, **4** at `246099892`, **5** at `6703e7355` — so row 13's "at the head is 4" and row 14's "this row is the fourth" were both false the moment each landed, and the pair `3 → 4` row 14 attributed to itself is row 13's. ⚠️ **This row deliberately does not restate the count** — that is the finding, not an omission — and the figure is given per ref (`3` / `4` / `5`) in both rows now, never as "at the head". The general form: **a tally that a new row increments cannot be stated as a current-state claim in that row.** (2) ⚠️ **§1.1 enumerated "five code files" where its own paragraph names seven sites** — `docs/infra-runbook.md` (`:101`) survived neither clause: it is not a code file and it was not the declared residual, so the census included it, the paragraph named it, and the enumeration dropped it. It is not hypothetical: that file's single occurrence sits **on the very row `#10864` rewrites** (the `degraded` / `degraded_reasons` / `refusing` field row), so the one lane already holding the file is exactly the lane never told the occurrences there are in scope. Now: **seven** — five code files plus the runbook and `08-hosted-prerequisites.md` — with the runbook's collision stated. |
| 16 | the fresh-context review at `ebf4ac11d` — the round that found the previous round had applied its own law to one of two adjacent tallies | **Two P2, and both are the *same* two defects one notch smaller — the loop is flat, not shrinking, and this row says so rather than pretending otherwise.** (1) ⚠️ **`grep -c 10864` was still stated as a head claim, and does not reproduce at the head that carries it.** Rows 13 and 14 said "eight", which is true only of `246099892`: measured **7** at `53b9c3afd`, **8** at `246099892`, **9** at `6703e7355` (row 14's own mention is the ninth), **11** at `ebf4ac11d`. ⚠️ **Row 13, not row 14, also said "three `§9` rows"** — row 14 never mentioned `§9` at all, and "three" was right at `246099892`, the head row 13 shipped at, growing with every later row. ⚠️ **This row's first version mis-attributed that to both rows; the correction is recorded here rather than silently rewritten, which is this row's own subject.** ⚠️ **Both series live here and nowhere else**, because every §9 row that names the number adds one, so **no row may state a count at the head that carries it, this row included** — `grep -c 10864` on the whole plan: **`7` / `8` / `9` / `11`** at `53b9c3afd` / `246099892` / `6703e7355` / `ebf4ac11d`; and restricted to §9's rows: **`2` / `3` / `4` / `5` / `6`** at `53b9c3afd` / `246099892` / `6703e7355` / `ebf4ac11d` / the commit carrying this row. Rows 13/14 now cite anchors only and point here. That is row 15's law applied to the second of the two adjacent tallies, which is what row 15 failed to do. (2) ⚠️ **§1.1's own enumeration was still short of its own paragraph, for the second round running.** It said "seven"; the paragraph names **nine**, and the two dropped are the ones that matter most — `tests/test_record_searchkeys_10620.py`, the pin whose **name** spread the number and which carries it on **9 lines** while **contradicting itself**: its `:1` still opens *"#10620 — the record dropped every point carrying `search_keys`, silently"* and its `:22` corrects it in the same file; and `tests/test_supabase_cli_pin_10593.py` (`:5`, a passing reference, part of the 24-vs-21 delta). A lane taking "seven" would leave the first line of the very file whose name caused the confusion asserting the falsified attribution. ⚠️ **Recorded, not filed, and deliberately:** the pin file's `:1`/`:22` contradiction is documentation drift in a test docstring — category B by the admission rule, cost is a lane's time rather than work or data — so it is measured here and left to the sweep the residual carrier decides on, rather than filed as a ninth peer of a root this section already names. |
| 17 | the fresh-context review at `94fddf578` — the round where the paragraph that declares the residual was wrong about its only neighbour | **One P2, verified against the live issue body and the tree before applying — and it is the same false-pointer class, in the same paragraph, for the third round running.** ⚠️ **§1.1 read *"`#10864` … proposes no correction to a `#10620` occurrence"*, four lines above its own statement that the runbook's single occurrence *"sits on the very row `#10864` rewrites"*.** Both cannot hold, and the first is false as a measurement: `#10864` §2's fix reads *"add the second clause (and the same one-clause note at `hosted_api.py:6400`)"*, and both targets ARE `#10620` occurrence lines at the merged head — `docs/infra-runbook.md:3415` (`(#10185, #10620)`, that file's **only** occurrence) and `tortoise/hosted_api.py:6435` (`(#10185/#10620)`, one of that file's **4**) — so **one of its three texts** (§2) proposes corrections to occurrence lines, and it targets **two** of them. Neither correction touches the attribution, so the residual is still uncarried and *"no carrier takes it"* survives; what did not survive was the blanket wording, which a lane can read as *"`#10864` is irrelevant to the number"* and then leave the two lines most likely to be rewritten under it unflagged. §1.1 now says "no correction to the **attribution**", states the one-of-three count, and warns about `tortoise/hosted_api.py` as well as the runbook — **the previous round warned only about the runbook, and the lane holding it holds `hosted_api.py` too.** Row 12's cell carried the false measurement as an established finding; ⚠️ **it is corrected in place there, not only here.** ⚠️ **This row deliberately states no `grep -c 10864` value** — row 16's law — and adds one occurrence to that series merely by existing. ⚠️ **`origin/main` then advanced 5 commits (#11109, #10726, #11091, #11024, #11097) and four of the nine sites moved under this document** — so every pinned number was re-measured on the MERGED tree before this row landed, not carried from the review head: `git grep -l 10620` went **24 → 25** files (the new carrier is `tests/test_9548_record_search_keys_shape.py`, **18 lines, all correct references** — a census entry, not a correction target, so the enumeration went **nine → ten** — ⚠️ **this cell's "all correct references … not a correction target" was FALSIFIED by row 22: the file carries the attribution on 18 lines and is a correction target; §1.1 is authoritative**), `record_projection.py` **18 → 23** lines with its header now at `:267`, the runbook row `:3414 → :3415`, the `hosted_api.py` comment `:6404 → :6435`, `ci-surfaces.yml` `:606 → :615`, and the delta against the measurement commit is now **25-vs-21**. `tests/test_9078…:2367`, the pin file's 9 lines and `08-hosted-prerequisites.md`'s 13-on-11 did **not** move. That re-pin is row 12(2)'s finding applied to a base that moved again, and it is why the merged head — not the review head — is what these numbers describe. ⚠️ **Not a finding, checked:** `tests/test_record_searchkeys_10620.py:1335` (that issue's §1 text) is *not* an occurrence line — the file's 9 are `1/22/64/427/705/901/1004/1126/1160` — and its §3 text is the merged PR body, so the count is **one** of three texts, that one targeting two occurrence lines rather than three, and gate item **(3)** of `#10864`'s Indicators (its Indicators field has three, numbered (1)–(3)) is a pin-filename reference, not an occurrence edit. |
| 18 | the fresh-context review at `35f1934ae` — the round where the base moved under the document and the merge itself created a stale issue-state claim | **One P1 and two P2, each verified against the primary source before applying.** (1) ⚠️ **P1 — `#9548` is CLOSED, and this plan asserted it OPEN / "Target (a) unmet" in four places plus the three population counts it feeds.** `gh issue view 9548` → `CLOSED 2026-11-06T01:57:25Z`, closed by `#11109`'s merge (`74fdd9ca0`) — which is an **ancestor of this head**, i.e. the closure arrived *with the `origin/main` merge this round performed*, 38 minutes before the head commit. Fixed at §2.1, §3's graph and serialisation sentence, §5 row 9 and the §5 preamble, and both count sites (live: **36 linked, 21 open / 15 closed**, not 22/14). ⚠️ **The closure is landed-on-main, NOT delivered:** `#10896` still carries 2 unapplied migrations — the distinction §5 row 5 already draws for `#10185` — so "closed" must not be read as "the hosted app writes today". This is the class row 11 graded P1 ("the state claim, not just the verb, was the trap"), and the §5 table's `3ac6b1636` header does not protect the undated prose that repeated it. (2) ⚠️ **P2 — §1.1's own new count was a non sequitur against the clause it was drawn from:** it said the other *two* of `#10864`'s texts are not occurrence lines and then concluded "two of three texts", when the correct figure is **one** text (§2) targeting **two** occurrence lines — which §9 row 12's cell already said correctly. Corrected in §1.1 and row 17. (3) ⚠️ **P2 — row 5's `:3627` clause was ref-unpinned ("on the head this PR ships") and had drifted a third time:** at this head `:3627` is the `#5516` comment and the `#10620` one sits at `:3731`. The clause as written was accurate at the **pre-rebase** `502c0810a`, written against `3ac6b1636` (row 5 already says so) and was false at the head it named — the rebase onto `af36e9240` had already moved the `#10620` comment to `:3664`, which `git show e16697a92:config/ci-surfaces.yml` confirms. ⚠️ **This item's first version said the clause "was accurate at `e16697a92`"; that was wrong and row 19 records the correction** — the fix pins each number to a ref where it IS true: row 5 now cites `:3627` as the `#9685` installer comment at `e16697a92`, and §1.1's `:3627` stays pinned to *"the measurement commit"* (`3ac6b1636`), where it really is the `#10620` comment. ⚠️ **Recorded, not adopted:** the reviewer's external rung failed (`web_search`, `sonar`, `PERPLEXITY_API_KEY not set`) and it fell back to `web_fetch` on the rustdoc book — a config gap in the reviewer's environment, not a finding about this document. |
| 19 | the fresh-context review at `414e774ac` — the round that caught this lane's own fix landing half-applied, and a row that contradicted the row above it | **Four P2, all verified against the primary source, and the first is the sharpest kind: a fix CLAIMED but not made.** (1) ⚠️ **§1.1 still read "two of three texts" with its own preceding clause saying the other two are not occurrence lines** — the previous round's edit had replaced `targeting` with `that one targeting` and left the count alone, so the sentence got *more* self-contradictory while row 18(2) recorded it as fixed. Corrected to **one** text targeting **two** occurrence lines, and row 17's "states the two-of-three count" corrected with it. This is why a fix is not done until the artifact is re-read: the claim of a fix is not the fix. (2) ⚠️ **§5's preamble conflated the live split with the `3ac6b1636` snapshot** — it listed `#9548` among the snapshot's *closed* records one clause before saying `#9548` closed *after* the snapshot, i.e. both halves could not hold. It now gives each figure its own ref: **21 open / 3 closed at `3ac6b1636`** (#10185, #10683, #10671), **#9548 closed since**, live **20 open / 4 closed**. ⚠️ **This cell's snapshot figure is itself wrong and is corrected by row 23(2): `#10683` closed 3h34m AFTER `3ac6b1636`, so the snapshot is 22 open / 2 closed.** Then (3) ⚠️ **Row 18(3) asserted the `:3627` clause "was accurate at `e16697a92`", which row 5 itself falsifies** — at `e16697a92` `:3627` is the `#9685` installer comment and the `#10620` one had already moved to `:3664`; the clause was accurate at the **pre-rebase** `502c0810a` against `3ac6b1636`. Row 18(3) is corrected in place and says so. (4) ⚠️ **§1.1's enumeration of **ten** was short by one of the paragraph's own carriers: the append-only migration.** The paragraph names `supabase/migrations/20261102000002_record_point_search_keys_text.sql` at `:98-101` as *"the primary artifact a reader of this fix opens"*, and it carries **6** occurrences — but the enumeration dropped it, which is exactly rows 15(2) and 16(2)'s finding (*"the census included it, the paragraph named it, and the enumeration dropped it"*) for the third time. It is now the **eleventh**, named as the carrier the correction provably cannot travel in. ⚠️ **Everything else reproduces at this head** — the 25/21 census and its 4-file delta, `record_projection` 23/`:267`, the runbook's single `:3415`, `hosted_api` 4/`:6435`, `ci-surfaces` `:615`, `test_9078…:2367`, the pin file's 9 lines, `08-hosted`'s 13-on-11, `test_9548…`'s 18, `_COLUMN_RE`'s one capture group, and the 57/0 carve-out manifest — so this round's defects are all in the *prose that describes* the measurements, never in the measurements. |
| 20 | the fresh-context review at `1c0eaa3c7` — the round that found round 19's own fix half-applied, in the same cell, one clause later | **One P2, and it is the fourth recurrence of the same count, which is the finding as much as the defect is.** ⚠️ **Row 17 carried the fixed count mid-cell and the falsified one in its last sentence** — *"so **one of its three texts** (§2) proposes corrections to occurrence lines"* … then *"so the count is two of three texts targeting two occurrence lines rather than three"*. Both cannot hold; given the cell's own premises (§1 is a docstring, §3 is the merged PR body) exactly **one** text targets occurrence lines, and it targets two. Row 19(1) recorded row 17 as corrected, so the log said "fixed" while the wording survived eleven lines later — the same "the claim of a fix is not the fix" failure row 19 was opened for, one round deeper. ⚠️ **Why it kept recurring: the count lived at **four statements over three sites, and it took rounds 19 and 20 together to close three of them** — not one per round.** Row 12's cell had it and **was already right** (§2's *targets*), §1.1 had it (round 19), and row 17 had it **twice** (round 19 the first, round 20 the second). What finally closed it was grepping the *number form* across the whole document rather than re-reading the paragraph I had just edited — the paragraph-level re-read is exactly the instrument that missed it three times. ⚠️ **A note on the value at row 12:** its parenthetical `docs/infra-runbook.md:3414` / `hosted_api.py:6404` are that round's head-bound values, not live ones — `:3415` / `:6435` at this head — and they are left as written because a dated measurement is a record, not a claim about today; row 17 carries the live pair. ⚠️ **Everything else reproduces at this head** — the eleven-site enumeration (5 code files + 2 documents + 3 test files + the append-only migration) — **except the §5 preamble's snapshot figure**, which read `21 open / 3 closed` in this cell and is **22 open / 2 closed** (row 23(2)); live is `20 open / 4 closed`. |
| 21 | the fresh-context review at `9c203f5b7` — the round that found a merged PR described as open, and a row falsified by its own next clause | **One P1 and one P2, both verified against the primary source.** (1) ⚠️ **P1 — §5.2 described `#10852` as "(OPEN)" and `:351` repeated it ("An open PR does"), 20 minutes after it MERGED.** `gh pr view 10852` → `MERGED 2026-11-06T02:36:52Z` (`ba87797c4`), and that merge is **not an ancestor of this head** — the last `origin/main` merge here is `f1aa7362b` — so the change is landed on `main`, absent from this tree, and still **undelivered by `#10896`**. That is the distinction this plan now draws for `#9548`, applied to a PR rather than an issue; a lane reading "OPEN" would wait for a merge that already happened or seek an owner admission it may not need. §2.1's requirement sentence now names `#10852` as the change it says is needed, so the two sites agree. ⚠️ **The branch was deliberately NOT re-merged with `origin/main`:** `#10852` touches `config/ci-surfaces.yml` and `08-hosted-prerequisites.md`, so a second merge would re-invalidate every pinned number in §1.1 for the third time in this PR's life, and the census that matters (`git grep -l 10620` = 25) is unchanged on `origin/main@bc5a26436`. The state is stated with its refs instead. (2) ⚠️ **P2 — row 20's "each round fixed one" was falsified by its own parenthesis**, which credits round 19 with two of the four statements and says row 12's site was already right; it now says four statements over three sites, closed three of them between rounds 19 and 20. ⚠️ **A note for the next lane, because this log is the thing under review:** every round of this loop has found the defect in the PROSE describing a measurement, never in a measurement — so read the sentences that *restate* a claim, not only the sentence that carries it, and prefer grepping the claim's form to re-reading the paragraph just edited. |
| 22 | the fresh-context review at `1bbadce90` — the round where "not a correction target" turned out to be the misattribution itself | **Two P2, both verified against the primary source.** (1) ⚠️ **§1.1 called `tests/test_9548_record_search_keys_shape.py` "a census entry and not a correction target", and its own lines carry the falsified attribution** — `:134` *"#10620 fixed it on the COLUMN side"*, `:150`/`:199` *"`text` since #10620"*, plus `:29-31`, `:408`, `:488`, `:550`, `:702`, `:814`. `#10620` is a **CLOSED, never-merged docs PR** (`mergedAt: null`), so *"#10620 fixed it"* is the same misnumbering the paragraph exists to correct, not a neutral reference to it — and `tests/test_supabase_cli_pin_10593.py:5` (*"the `point.search_keys` fix of #10620/#10593"*) carries it too. Both are now correction targets, and the enumeration's closing claim (*"three are already explicitly not correction targets"*) is corrected: of the eleven, **only the append-only migration cannot be corrected at all**. ⚠️ Leaving them classified as neutral would have sent the residual sweep past ~13 lines of a live test asserting that a closed, never-merged docs PR made the schema narrowing — the exact failure rows 12(1) and 17 graded. (2) ⚠️ **§5.2 still called the carrier "a dependency on in-flight work" one line after the P1 fix made it "a merged PR does"** — the document reserves "in-flight" for OPEN PRs (`§1.2`'s table called `#11064`, `#11004` and `#10852` "in-flight", two of which have since merged), so the synonym survived the fix in the next clause. It now reads **landed-but-undelivered**, and the §1.2 row states which of the three are still open. **The recurring shape, one level up:** a fix that replaces a word can leave its synonym standing, and a classification that reads "this one is only a reference" is a claim like any other — both were settled by reading the cited lines rather than the sentence about them. |
| 23 | the fresh-context review at `602f5fa75` — the round that caught a word boundary, a snapshot three hours too late, and a marker the table's own convention requires | **Three P2, all mechanical, and the first and third were produced by the round before.** (1) ⚠️ **A missing space made the enumeration's closing claim render as one word.** `:158` read *"of the eleven\*\*only the append-only migration…"*, so the CommonMark delimiter run opened bold against the letter `n` and the rendered text read **"elevenonly"** — the fix that sentence exists to make, jamming the two halves together. `markdownlint-cli2` reports **0 issues** on it, so no CI leg would have caught it; a CommonMark render did, and the document now carries no `[word]<strong>` or `[word]<em>` boundary anywhere. (2) ⚠️ **The §5 preamble counted `#10683` as closed *at* `3ac6b1636`, and it closed 3h34m AFTER that commit.** `git show -s --format=%cI 3ac6b1636` → `2026-11-05T12:56:58Z`; `#10683` → `closed_at 2026-11-05T16:31:00Z`. The snapshot is therefore **22 open / 2 closed** (#10185 `04:54:01Z`, #10671 `11-03`), with `#10683` closed later that day and `#9548` since, giving the live **20 open / 4 closed** — and §5 row 6 now says *"OPEN at `3ac6b1636` — CLOSED 2026-11-05T16:31Z"* so the table agrees with its own header. (3) ⚠️ **Row 17 stated the classification row 22 falsified in the present tense and carried no in-place marker**, though the same cell demands one (*"it is corrected in place there, not only here"*) and row 12 does carry one. It is marked now. **The lesson at this point in the loop:** a fix is not complete until the ARTIFACT is re-read *and re-rendered* — the last two rounds' defects were both introduced by the previous round's fix, in the clause that fix was about. |
| 24 | the fresh-context review at `d3fca77af` — the round where the count was right in the paragraph and short in the sentence that closes it | **Three P2, all citation/count defects, none in a measurement.** (1) ⚠️ **The "nothing carries it" clause still said "the two test files above"** (`:169`) while the paragraph names **three** — `test_record_searchkeys_10620.py`, `test_supabase_cli_pin_10593.py`, and `test_9548…`, the last of which round 22 had just promoted to a correction target. The clause was written when the enumeration was nine, and the round that made it eleven edited the closing claim above it but not this one; `:159`'s *"both test files included"* carried the same under-count. Both now say three. (2) ⚠️ **Rows 19(2) and 20 still asserted the §5 snapshot as `21 open / 3 closed`**, which row 23(2) had already falsified (`#10683` closed 3h34m after `3ac6b1636` → **22 open / 2 closed**), and neither carried the in-place marker the table's convention requires. Both are marked now. ⚠️ **The pattern in the log itself: a fix to the paragraph does not reach the sentence that CLOSES the paragraph, and a fix to a live section does not reach the log rows that recorded the earlier version of it.** Every round of this loop has now found its defect either one clause after the fix or one table row below it — the sweep has to be document-wide, over both the live prose and the log, and it is the only thing that has actually converged. (3) ⚠️ **`gate item (17)` of `#10864`'s Indicators was a dangling citation** — that field has three items, `(1)`–`(3)`, and the one the sentence describes is **`(3)`**. A false pointer in the row whose subject is false pointers. |
| 25 | the fresh-context review at `1da4cd150` — the round whose findings were both in the row the round before had just written | **Two P2, both introduced by row 24's own edit, and both fixed.** (1) ⚠️ **Row 20 listed the §5 snapshot figure under "everything else reproduces at this head" and then marked it wrong in the same sentence** — a figure cannot both reproduce and be false. It is now outside the "reproduces" list, which the sentence names as the one exception. (2) ⚠️ **Row 19's inserted marker left no sentence terminator**, so the render read `…22 open / 2 closed (3) ⚠️ Row 18(3)…` and absorbed the next finding's enumerator into the snapshot clause; a full stop separates them now. ⛔ **Mitigation adopted rather than another round of the same fix:** a new log row may state what was FOUND and what was CHANGED, but it does not restate counts, figures or enumerations that live in the body — every restatement is a second site that has to stay in sync, and most of this log's recent defects were exactly that. The body is authoritative; rows 1–24's restatements are kept only because each was verified when written, and the ones later falsified carry in-place markers. |
| 26 | the fresh-context review at `fcbf1d739` — the round that found the epic's load-bearing mechanism section describing a tree three commits older than the one it ships into | **One P1, and it is the merge-induced class this log has graded four times, now in §7.1.** ⚠️ **§7.1 opened with `grep -rn jsonb_columns` returning *zero code hits* and concluded that the reader and the `ALTER COLUMN … TYPE` form had to be built — but #10726's fold batch, which this branch merged in `f1aa7362b`, landed exactly that reader.** `jsonb_columns()` is defined at `tortoise/record_projection.py:1607`, with `_COLUMN_TYPE_RE`, `_ADDED_COLUMN_TYPED_RE` and `_ALTER_COLUMN_TYPE_RE` beside it; measured at this head it returns 11 names and **`search_keys` is not one of them**, so the section's *sharpest* warning — that reusing the reader would read the superseded `jsonb` and red a correct tree — was the opposite of the truth. ⚠️ **What survives, and is now the whole of §7.1:** `_COLUMN_RE` itself still matches the declared type in a **non-capturing** group, so a consumer of *that* pattern must lift it; and the type vocabulary is still a hardcoded seven-term allow-list. The rest — capture, the ALTER form, the reader — has landed, so *"capture one, build three"* is satisfied and `#11075`'s remainder is the vocabulary plus the value→declared-type predicate for types outside it. Fixed in §7.1, in §0's one-sentence verdict, in §8's checklist item, in §7.2's carrier count for `#11075`'s body (**four** in total there, with *"zero code hits"* among them), and in `01-test-design.md` §10's twin paragraph, which now gives the same four. ⚠️ **The general lesson, and it is the same one the base commit keeps making:** a merge that brings the branch *forward* brings it forward in the FILE SYSTEM too — every section that says "X does not exist" must be re-measured after a base merge, not only the sections that cite line numbers. |
