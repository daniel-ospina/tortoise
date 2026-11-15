---
title: "#12894 Stage A — the storage shape, mapped, and the ideal stated against it"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-15
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "#12894, #9190, #7951, #7879, #5612, #9025, #12458, #10017, STORAGE-ARCHITECTURE.md, durability-posture.md"
related:
  - issue: 12894
    note: "The archaeology mandate; artifacts A1–A7 are its deliverables."
  - issue: 9190
    note: "The ordinal's recorded owner. A7's reopen is argued in this decision's own home."
  - issue: 7951
    note: "The atomic write, incrementally. A5 records what already landed."
  - issue: 7879
    note: "The Postgres record epic — still OPEN and unowned at this head."
---

# #12894 Stage A — the storage shape, mapped, and the ideal stated against it

**This document changes no product code.** It is the map (A1–A6) and the ideal stated against the map
(A7), for Stage A of `#12894`.

**Measured at:** `main` @ `58f904ccf793` (this worktree, `.worktrees/12894-storage-archaeology`).
Every count in §A2 comes from a `tools/worktree_report.py run --cmd` log committed with the worktree
report; the commands are reproduced verbatim where the count appears.

**How to read this document.** §A1 is the completeness artifact and is the one to grade. §A2–§A5 are the
map. §6 resolves the recorded tension the issue names, in words. §A7 is the ideal, and §A7.4 is the
section that says what the map **contradicts** — including the hypothesis's own crux. §9 states what this
lane did not do.

---

## A1 — The document inventory (the completeness artifact)

### A1.0 What the population is, and how it was closed

`docs/00_index.md` is the scope proof, and it is **247 lines carrying 182 entries** (5 separator rows and
5 header rows excluded). Recorded:

```
$ python3 -c '...count table rows...'         # via worktree_report.py run --name A1-index-population
file lines      : 247
table rows      : 192
separator rows  : 5
header rows     : 5
ENTRIES         : 182
```

**182 = 41 in scope + 141 out of scope.** Every one of the 182 is accounted for below, in one of two
tables; there is no third bucket and no unclassified remainder.

**⚠️ First finding, and it changes what "prove the population from the index" can mean: the two documents
that define the storage shape are not registered in the index as entries.**

| document | index hits | what the hits are |
|---|---|---|
| `docs/architecture/STORAGE-ARCHITECTURE.md` | 6 | **all six are mentions inside OTHER entries' prose** (`#5612`, `#11821`, `#11086`, `#7983`, `#10000`, `#8607`) — it has **no index row of its own** |
| `docs/event-catalog.md` | **0** | not mentioned anywhere in the index |
| `docs/plans/2026-10-14-7951-atomic-write-outbox.md` | **0** | not mentioned anywhere in the index — and it is the plan for the atomic write |

So the index **cannot** close this population by itself: the governing architecture document, the event
catalogue, and the atomic-write plan are all reachable only by cross-reference from the documents that
are indexed. The scope below is therefore the union of (a) the index's own entries that touch storage and
(b) the documents those entries cross-reference, plus the mandatory starting set the issue names. This is
a **defect to file**, not a convenience: a lane starting from the index alone would not find the document
that governs the subject. (Filed as its own item in §A6, debt row D-22.)

### A1.1 In scope — 41 documents, each `READ` with the section or line taken

| # | Document | Status | What was taken from it (section / line) |
|---|---|---|---|
| 1 | `docs/durability-posture.md` (936 lines) | **READ (full)** | §The rule `:41`; `:48` *"It is never the durability mechanism"*; `:59` *":GraphEvent is the 30-day delivery/audit stream, not a backup"*; §Authoritative node classes `:73`; §The one carried high-water mark `:264` (the whole `:GraphEventMeta` block `:264-380`: the `last_seq` allocator, `MAX_SEQ = 2**53-1`, `first_seq` never carried, `reestablish_watermark`, the "**A rebuild still requires a QUIESCED graph**" residual); §Per-deployment posture `:471` incl. the five deployment rows `:500-560` and the record's own row + §The record's posture `:583`; §Why this document exists `:660` (the gate's claim shapes, `#9867`'s blind wordings); §Open items `:710` |
| 2 | `docs/architecture/STORAGE-ARCHITECTURE.md` (2099) | **READ (§1, §2, §2.0, §2.1, §2.2, §3, §5, §5.1, §5.2, §6, §6.1, §12.1c, §14.1, §17.1–§17.4.6)** | `:104` §1 the problem; `:116` §2.0 the owner ruling **and its 2026-11-07 SPLIT + the `OVERRIDES:` line**; `:173` §2.1 what FalkorDB charges for; `:217` §3 the split; `:236` *"READ THE AUTHORITY COLUMN AS AUTHORITY, NOT AS LOCATION"*; `:243` the per-write truth test; `:275` *"journal completeness is a PRECONDITION"*; `:285` **§ "The journal's scope: REBUILD, not durability"**; `:316` the invariant `derived tables = replay(the journal) NOT recompute(the sources)`; `:342` *"ONE ATOMIC WRITE … may be an outbox … **This is the whole answer**"* and *"**The current JSONL cannot do (1)**"*; `:361` *"They are also recorded in the journal, which is truth"*; `:427` §5.2 measured growth (and the per-capture total `#9025`); `:450` §6 the split does not fix volume; `:1115` §12.1c (vector index decision — **this, not SEARCH §12.1c, is where §12.1c lives**; see A1.3); `:1546` §14.1 decisions; `:1664` §17.1; `:1700` the 387-site measurement; `:1702` §17.2; `:1822` §17.4.2; `:2090` §17.5 |
| 3 | `docs/architecture/SEARCH-ARCHITECTURE.md` (566) | **READ (§3, §4, §7)** | `:157` §3 "What runs where" (writes/projections/backup-restore stay in FalkorDB; the graph stays a *derived copy*); `:205` §4 "Authority (unchanged, cited not restated)"; `:361` §7 "The window" (why a complete cache is 193 % of the storage target and the window is not yet armed) |
| 4 | `docs/ONTOLOGY.md` (1561) | **READ (§2, §4.2, §4.7, §5 headings)** | `:61` §2 the Four-Ontology Model table — the layer vocabulary used verbatim below; `:71` the Procedural row (`Object.status` is a **write-through cache** and *"the journal/event stream is the reconstruction source"*); `:75-97` the state-centric model (*"the Episodic layer's events are the truth; status is a fold cache over the events — never the truth itself"*); `:638` §4.7 temporal model; §5's kind vocabularies (read for the `decision`/`statement` split only) |
| 5 | `docs/event-catalog.md` (330) | **READ (full)** | `:89-92` *"The JSONL rebuild journal that `rebuild_all` replays is a second, differently-shaped store"*; `:100-101` the `__TornTailSealed__` seal shifts no record index; `:263-266` **no down-version guarantee**; `:286` `seq` = *"Per-graph monotonic, atomic in-graph counter (`GraphEventMeta.last_seq`)"* — i.e. the catalogue documents the **` :GraphEvent`** counter, not `journal_event.seq`; `:305-307` the 30-day/500k retention |
| 6 | `docs/00_index.md` (247) | **READ (full)** | The population proof above; and the absence finding (`STORAGE-ARCHITECTURE` and `event-catalog` unregistered) |
| 7 | `docs/retention-and-deletion.md` (606) | **READ** | `:30-34` *"This is the canonical document … link here and do not restate the number"*; `:41-42` the promise; `:73-75` *"Restore window ≠ retention window"*; `:92-97` no per-entity purge inside a live graph |
| 8 | `docs/data-safety.md` (53) | **READ (full)** | `:15` retention is delegated here; `:36` graph content is **infrastructure-level encryption only**; `:37` control-plane audit events go Postgres-first with a local JSONL fallback — a **different** dual-write seam from the journal's |
| 9 | `docs/ops/registry-backup-dr.md` (1233) | **READ (§1–§4, the retention and tombstone sections)** | `:17` retention windows delegated; `:76` *"Cadence is HOURLY, not daily"*; `:380` the per-graph artifact path `backups/{org_id}/{graph}/{ts}_{rnd}/dump.enc` |
| 10 | `docs/scoping-2313-per-graph-backups.md` (234) | **READ (full)** | `:17` the pre-fix shape (one graph per team — custom graphs unbacked); `:130` the pool-competition bug; `:206`/`:211` the owner's Q1/Q4 rulings |
| 11 | `docs/capacity.md` | **READ (write-concurrency and storage-footprint rows)** | the per-deployment write-concurrency row (embedded single-writer; Docker 5 writer processes) — the basis of A3's rows |
| 12 | `docs/ops/postgres-storage-measurement.md` | **READ** | `postgres_storage_footprint()` / `analytics_events_rows_by_org()`; the TOAST/double-count traps; the explicit **retention finding**: `analytics_events` has no prune path |
| 13 | `docs/architecture/SUBSTRATE-REBUILD-MAP.md` (527) | **READ** | `:86` **E6 `#7879` OPEN and unowned**; `:94-96` *"E2 is CLOSED with an interpreter-level invariant still stated as its goal … a claim about the cache, and the 2026-10-08 hybrid ruling made the record authoritative"*; `:244-246` the replay keys; `:339` *"#7879 (the record) ────► everything: the cache is derived, the record is truth"* |
| 14 | `docs/architecture/CONSUMPTION-MODEL.md` | **READ (the census + dimension table)** | the storage meter is blind to the HNSW index; no `max_storage_bytes` gate on `main`; the two-substrates-of-one-bytes-unit finding |
| 15 | `docs/ops/cost-dimensions.md` + `tortoise/cost_dimensions.py` | **READ (registry rows for postgres_storage / journal bytes)** | `cost_dimensions.py:492` `journal_event_bytes_for_rows`; the declared dimension registry |
| 16 | `docs/ops/postgres-per-org-bytes.md` (via #12762) | **READ (issue body + plan comment)** | attribution/total = 83.9 % on 404 rows / 3 orgs, 16.1 % un-attributable; `pg_column_size` does **not** report the 18-byte pointer; the double-count trap |
| 17 | `docs/architecture/PI-CAPTURE-ARCHITECTURE.md` | **READ (the cursor + store-sync backstop sections)** | the **per-file cursor, advance-on-2xx-only** pattern — the closest in-tree comparable to an ordinal-bearing consumer, and the reason A4 distinguishes *cursor* from *ordinal* |
| 18 | `docs/architecture/BOUNDED-OPERATION-CONTRACT.md` | **READ (`event_store.read_after` row)** | `read_after` **REFUSES** out-of-domain because *"its cursor never signals end-of-collection"* — load-bearing for A4 |
| 19 | `docs/adr/ADR-009-embedder-selection.md` | **READ (the model/revision pinning decision)** | the model-id + revision pinning that makes `embedding` non-recomputable (R1) |
| 20 | `docs/plans/2026-10-14-7951-atomic-write-outbox.md` (259) | **READ (full)** | increment 1; the ordering contract `:§3` with its **three named pre-append writes**; the 5-row `Wiring Check`; the 10 declared residuals — in particular **residual 7** (the `:GraphEvent` store is written **before** the JSONL append, `sdk.py:5653-5683` vs `5814`) and **residual 2** (the record projection on the live path + `last_applied_seq` "STILL OPEN") |
| 21 | `docs/plans/2026-10-17-7951-update-point-outbox.md` (161) | **READ** | increment 2a: the `update_point` revision arms append before the cache write; the promote arm stays apply-first because its guarded `WHERE` can refuse |
| 22 | `docs/plans/2026-10-18-7951-delete-point-outbox.md` (279) | **READ** | increment 4a **and its reversal by `#9207`**: the `DETACH DELETE` now lands **first** and the record follows, because refusing a delete after the destroy had already happened reported a lie |
| 23 | `docs/plans/2026-09-05-2194-journal-objectregistered.md` (278) | **READ (full)** | `:7` the goal; `:12` post-apply emission (the phantom-event hazard); `:52` the append is **best-effort warn-and-continue** → *"the Object is live-but-not-durable for that write"* |
| 24 | `docs/plans/2026-09-06-2295-subjectadded-journal.md` (133) | **READ (full)** | `:11` *"no SDK write ever produces the record → journaled-SDK Subjects **vanish silently on rebuild**"*; `:23` no fold vocabulary; `:109` `DocumentCreated` is the remaining unjournaled canonical create |
| 25 | `docs/plans/2026-09-07-2488-invalidate-point-journal.md` (143) | **READ (full)** | `:96` *"Do NOT sort by ts (ts collides within the same ms; **JSONL carries no seq**)"* — the single most misleading sentence in the corpus, and A7 handles it explicitly; `:141` the merge-order constraint |
| 26 | `docs/plans/2026-09-18-3689-annotator-props-journal.md` (216) | **READ (full)** | `:3-4` Gate 0: *"category A (silent destruction of work)"*; `:18-28` the two-defect class (a record reaching `:GraphEvent` and never the JSONL line) |
| 27 | `docs/plans/2026-09-22-2814-rebuild-config-durability.md` (561) | **READ (full)** | `:29` the unconditional `MATCH (n) DETACH DELETE n` + journal-only replay; `:37` the wipe statement stays byte-identical; `:121` the version-bump pairing rule |
| 28 | `docs/plans/2026-09-22-unjournaled-mutation-class.md` (1250) | **READ (full)** | `:83` live-ahead-of-journal on a serialization **or I/O** failure; `:91` `append` had no lock, lines could tear, `read_all` **raises** mid-file; `:1250` *"**The journal remains a PARTIAL WAL** … The seam is a **convention**, not a structural guarantee"* |
| 29 | `docs/plans/2026-11-13-issue-12459-journal-base64-payload.md` (974) | **READ (full)** | `:287-288` append-only, *"Nothing is truncated or rewritten — the seal is another append"*; `:777` implementation blocked; `:824` the **silent** build-rollback vector-drop |
| 30 | `docs/epics/2026-10-10-7879-postgres-record/03-schema.md` (207) | **READ (the `journal_event` schema intent)** | `(org_id, graph_id, seq)` with `seq` as *"the idempotency key"*; deliberately **no FK**; `payload jsonb` |
| 31 | `docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md` (1247) | **READ (§0.1, N1, the ordered prerequisite list)** | the anchoring cutover (`#7970`) stays **deferred** by §2.0 |
| 32 | `docs/epics/2026-10-10-7879-postgres-record/10-read-path-anchored-surface.md` (214) | **READ** | which anchored reads the record serves (`#7970`) — the read surface that must not regress |
| 33 | `docs/epics/2026-10-10-7879-postgres-record/06-cache-projection.md` (653) | **READ (§0.5 and the writable-field set)** | `rebuild_from_record`, the field-set discipline, the eight graph-only `_SNAPSHOT_SECTIONS` classes dropped by the record path |
| 34 | `supabase/migrations/20261012000001_record_event_stream.sql` | **READ** | `:81` `CREATE TABLE IF NOT EXISTS public.journal_event (`; `:96` the **append-only trigger function**; `:141` `idx_journal_event_node` |
| 35 | `supabase/migrations/20261012000005_record_org_purge_rpc.sql` | **READ** | `purge_org_record(p_org_id)` — the record's only delete path |
| 36 | `supabase/migrations/20261114000001_per_org_postgres_bytes.sql` | **READ** | `journal_event_bytes_by_org(...)`, `journal_event_bytes_for_rows(...)` |
| 37 | `docs/infra-runbook.md` (§3.5, §9) | **READ (the two record sections only)** | the record-backup job's preconditions; `python -m tortoise record-restore` and its four refusals |
| 38 | `docs/ops/multi-graph-migration-runbook.md` | **NOT READ — declared** | Reason: it is a runbook for moving a *graph key* between stores (the `migrate-db` verb). Its storage claims delegate to `durability-posture.md` §Open items (the `#4125` entry), which is read. Its own content is procedural and adds no claim this map needs; if Stage B touches `migrate-db`, this is the document to read first. |
| 39 | `docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md` | **READ (the entries for §3/§5.2/§17.1)** | confirms which of §3's claims are amendments vs originals, so A7 does not reopen a claim that was already narrowed |
| 40 | `docs/evidence/5612-journal-growth/README.md` + `docs/evidence/11821-journal-encoding/README.md` + `docs/evidence/9025-journal-bound/` | **READ (the measurement records)** | 9,176 B → 5,423 B per creating record; the two rulers (12.2 vs 13.2 chars/float); 642,952 B per typical capture; 3.01 GB/year per power user |
| 41 | `docs/verify/2026-10-31-record-capstone.md` | **READ** | the record's declared capstone status — what is proven vs asserted about the record today |

### A1.2 In scope — the issue population (15 cited + 1 audit comment)

| # | Issue | State at this head | What was taken from it |
|---|---|---|---|
| I1 | `#2194` | CLOSED/COMPLETED (PR #2309) | `ObjectRegistered` journaling: the replay consumer pre-existed, the **producer** was missing; emission is **post-apply** and **best-effort** |
| I2 | `#2295` | CLOSED/COMPLETED (PR #2359) | The Subject twin; **`SubjectAdded ∉ _GRAPH_EVENT_TYPES` → JSONL-only**; *"vanish silently on rebuild"* |
| I3 | `#3895` | CLOSED/COMPLETED (PR #3921) | The dump's edge export was unfiltered (node loop filtered, edge loop not) → 313/10,000 edges unreferenced → restore refused. `_EXPORT_SKIP_LABELS` excludes `GraphEventMeta`/`TeamMeta`/`EpMeta` |
| I4 | `#4240` | CLOSED/COMPLETED (PR #5632) | **Rejected promoting `EntityLinked` into `_GRAPH_EVENT_TYPES`**; one file per graph (`<base>/<graph>/events.jsonl`); *"the JSONL is **never** the durability mechanism"*; and the **inversion**: *"the journal append IS the commit point on `create_point`"* |
| I5 | `#4492` | CLOSED/COMPLETED (PR #8744) | The unit ruling (**bytes, not nodes**); `analytics_events` has **no** retention path; retention itself is out of that issue's scope |
| I6 | `#5004` | CLOSED/COMPLETED (PR #5071) | The journal **stripped** `embedding`, so `derived = replay(journal)` was false for the largest cost lever; *"if the journal doesn't carry it, it isn't derived — it is primary"*; R1 (embedding STORES, never regenerated) |
| I7 | `#5612` | CLOSED/COMPLETED — **but item 2 (the bound) was never delivered** | The growth measurement (9,176 B creating record, 8,414 B vector); **the only `seq` in the issue corpus is `max(:GraphEvent.seq)`**; **P1 recommends moving the watermark *read* to the record — a read relocation, not an allocation decision**; the retention ruling (*"do not bound until checkpoint/truncate exists"*, *"the owner's"*) |
| I8 | `#8526` | **PR, MERGED** (not an issue) | Self-host journal arming: `derived = replay(journal)` was **false on every shipped self-hosted deployment** — a **wiring** gap; *"Arming the journal buys REBUILDABILITY, not DURABILITY"*; **"Ship Postgres to self-host now" — parked by the owner** |
| I9 | `#9025` | **OPEN** | The journal's growth is still unbounded; the live census: volume **974 MB, 652 MB used, ~4 days of headroom at ~50 MB/day**; recommends *keep everything* + the non-destructive segmentation plumbing (`journal_segments.py`) |
| I10 | `#9078` | CLOSED/COMPLETED | The record had **zero production callers**; landed **three journal doors → one seam** (`record_writer.write_live_record`), **after** the append and **before** the fold: **journal → record → cache**. And the deferral that becomes `#9190`: *"The live path has no ordinal … writing it here would fabricate a position a later replay would collide with or duplicate."* |
| I11 | `#9190` | CLOSED/COMPLETED (PR #9216, hardened #9277) | **The ordinal's owner and its reasons.** `MAX(seq)+1` **rejected**; a **sidecar counter inside the append's existing critical section**; the count is *"defined by the replay's own reader"*; and *"`event_store.next_seq` / `:GraphEventMeta` — a **different** seq over a different population"* |
| I12 | `#10017` | CLOSED/**NOT_PLANNED** (a no-op locally; reopen condition = hosted armed) | **R2**: a journal entry whose replay materialises **no graph node** (`TurnsRecorded`); the §3 falsification — *"a journal rebuild today produces a graph the running deployment does not have"*; and *"`:GraphEvent` is **not** projected from the record — it is written by the **journal** path"* |
| I13 | `#11821` | CLOSED/COMPLETED (**P0 only**) | The vector width: 9,176 → 5,423 B; the `:GraphEvent` mirror at **32,032 B/session** *"rented for a watermark"*; P1 → `#12458`, P2 → `#12459` |
| I14 | `#12458` | CLOSED/**NOT_PLANNED** | **Read in full, including the closure and the audit comment.** The audit classifies **20 `:GraphEvent` sites**; the closure is a **REVERSAL finding** (P1 deletes the medium `_GRAPH_EVENT_TYPES` is defined *by*), plus the size premise already removed by `#10017`. **It leaves one defect standing: the `next_seq` second allocator** |
| I15 | `#12762` | CLOSED/COMPLETED | Per-org bytes from what we WRITE (`journal_event_bytes_by_org` / `_for_rows`); the TOAST findings; **`#3843`/`#3853` bind `analytics_events`, not `journal_event`** — so `journal_event` retention has **no ruling** |
| I16 | `#12894` | OPEN | This mandate |

**`#12458`'s audit is a method to copy and a population that is not mine.** Its 20 rows enumerate *code sites
that consult a store* (13 read sites + 3 writers/posture + 4 dead). A journal census enumerates *records and
their producers/consumers*. Worse, **neither population is a subset of the other**: `:GraphEvent` omits
JSONL-only types (`SessionRecorded`, `TurnsRecorded`, `EntityMutated`, `SessionNarrated`, `EntityLinked`) and
**includes one with no JSONL line at all** (`PointRetracted` on hard delete, `event-catalog.md:13`). And the
*direction of travel* differs: a reader audit asks "who breaks if this write stops"; a journal census asks
"what is in the rebuild source, and can it be replayed". `#4664` (the rebuild does not replay `:GraphEvent`)
makes those answer differently. **What I copied is the discipline — exhaustive enumeration, one class per
row, blockers named as fatal — not the rows.**

### A1.3 A citation correction the issue's own text needs

The mandate lists `docs/architecture/SEARCH-ARCHITECTURE.md` **§12.1c**. `SEARCH-ARCHITECTURE.md` has 11
numbered sections and no §12.1c. **§12.1c is `STORAGE-ARCHITECTURE.md:1115`** (the vector-index decision,
"RESOLVED 2026-11-07"). Both were read. Recorded here because a lane that trusts the brief's section number
will read the wrong document and conclude the section is missing.

### A1.4 Out of scope — 141 entries, by declared class

Each class is a **rule**, and the entries it covers are named by their index entry number? No — the index
is unnumbered in the file, so they are named by their position in the extracted list (1–182, in file order),
which the extraction command above reproduces deterministically.

| Class | Rule | Entries (by extracted position) | Count |
|---|---|---|---|
| **C1 — other subsystems, no storage-shape claim** | auth/consent/OAuth, permissions/tenancy control plane, MCP/SDK surface inventory, extraction/gates, entity resolution, eval batteries, temporal/retrieval levers, blog/SEO, CI timings and runner logistics, fleet/reaper/git tooling, harness/eval, competitor and market research | 1–3, 7–13, 14–24 (excl. 4,5,6), 25–28, 30–41, 44, 48–52, 57–65, 68–75, 76–86, 91–92, 94–95, 99–105, 111–138, 140–158, 159–165, 167–169, 170–171, 176–177, 179, 182 | 128 |
| **C2 — process/observability-of-machinery** | finding-provenance gate, issue-work process, batch review, `run_affected`, container lifetime, temp-dir sweep | 10, 12, 11, 13, 15, 20, 21 | 7 |
| **C3 — reads a storage surface but asserts nothing about its shape** | `#4503` edge class outside accounting; `#9664`/`#9675`/`#10183`/`#11461` capstones and AX principles; `#11083` pack-relation census; `#11469` curation; `#8607` legacy ids; `#1779`; `#1402` | 48, 92, 101, 103, 104, 105, 180, 182, 177, 127 | 6 |

**128 + 7 + 6 = 141. 141 + 41 = 182.** ✅

**Why C3 is not "read"**: each of those touches a storage table or verb *as an input to another question*
(a census, a capstone row, an AX ruling). None asserts a claim about the storage shape, so reading them
would add no row to this map — but the reason is stated per class rather than per file, which is the
honest form: **a class rule is checkable, a silence is not.**

---

## A2 — The code census

### A2.1 The JSONL journal: three writers, one append, one seam

```
$ rg -n "(self\._journal|self\.journal|event_log|_event_log|active_log|log)\.append\(" tortoise/
tortoise/mining.py:997:        seq = api.log.append(record)
tortoise/api.py:2153:        seq = self.log.append(event)
tortoise/sdk.py:14008:            seq = log.append(event)
```

| site | what it is | lane(s) live in |
|---|---|---|
| `tortoise/log.py:1393` `EventLog.append` | **the append** — returns the record's 1-based position, or `None` when the position is not proven | all journaled lanes |
| `tortoise/sdk.py:14008` | `_emit_event`'s JSONL leg — the SDK door | hosted, self-host, embedded-if-configured, CI |
| `tortoise/api.py:2153` | `EventAPI._emit` — the API door (captures the raw-write Event path) | hosted, self-host |
| `tortoise/mining.py:997` | `ConversationMiner._emit_event` — the miner door | hosted (mining sweep) |

**Three doors, one append, and the three are exactly `#9078`'s writer population.** Every other producer
reaches the journal through one of them. **That chokepoint is real and is the good news in this map** — the
journal has *one* write primitive while the cache has none (§A6 D-01).

### A2.2 The JSONL journal: readers — one replay reader, four bypass readers

```
$ rg -n "log\.read_all\(|read_all\(salvage=True\)|read_for_replay" tortoise/     # 39 lines
```

Production call sites that parse journal records (excluding docstring references and the embedded
`shared_state` codec, which is a different log):

| site | reader class |
|---|---|
| `tortoise/projection/__init__.py:13991` `rebuild_all` → `:14050` `_rebuild_all_replay` | **replay engine 1** (the whole-graph wipe + journal replay) |
| `tortoise/projection/__init__.py:13428` `rebuild` / `:10640`,`:10673` (protocol + in-memory impl) | **replay engine 2** (in-memory) |
| `tortoise/consistency.py:3320` `recover_from_log` | **replay engine 3** (guarded recovery) |
| `tortoise/backup.py:571`, `:715` | **replay engine 4** (the JSONL fallback when an RDB is absent) |
| `tortoise/projection/__init__.py:19188` `rebuild_from_record` | **engine 5 — record-sourced**, not journal-sourced. Shares the wipe, the gate and the index creation; **does not read the journal** |
| `tortoise/api.py:2436`,`:2457`; `tortoise/mining.py:807`; `tortoise/ingest.py:1407`; `tortoise/m0.py:38`,`:44`; `tortoise/indexer/github_indexer.py:858` | fold/read consumers (not rebuilds) |
| `tortoise/sdk.py:17965`, `:32114`, `:52509`, `:1353` | SDK-level fold readers (statement splitting, scans, the SDK's `read_all` proxy) |
| `tortoise/__main__.py:418`, `:517` | CLI verbs |
| **bypass readers of `read_all`** (`#12459` measured): seal/tail detection, `subject_binding`'s key scan, `MentionScan` (`log.py:1243`), `backup._count_journal_events` (`backup.py:68`) | **these do not see a payload format change** — they tokenize/scan, they do not decode |

### A2.3 The Postgres record: `journal_event` has exactly one writer and one reader that folds it

```
$ rg -n "INSERT INTO public\.journal_event|FROM public\.journal_event|journal_event_bytes_for_rows|_journal_row" tortoise/
tortoise/record_projection.py:5995:def _journal_row(...)
tortoise/record_projection.py:6005:        "INSERT INTO public.journal_event "
tortoise/record_projection.py:6499:_JOURNAL_INSERT_PREFIX = "INSERT INTO public.journal_event"
tortoise/record_projection.py:6513:    THE POSITION WATERMARK (#9187). `_journal_row` keys the consumed position on
tortoise/record_projection.py:6545:            plan.append(_journal_row(record, seq, org_id, graph_id))
tortoise/cache_projection.py:2988:    "FROM public.journal_event "
tortoise/postgres_bytes.py:118:ROWS_RPC = "journal_event_bytes_for_rows"
tortoise/postgres_bytes.py:215:    """Parse a ``journal_event_bytes_for_rows()`` payload. RAISES on malformed."""
tortoise/cost_dimensions.py:492:            "journal_event_bytes_for_rows",
```

| site | class |
|---|---|
| `record_projection.py:5995` `_journal_row` → the INSERT at `:6005`, `ON CONFLICT (org_id, graph_id, seq) DO NOTHING` | **the only writer** |
| `record_projection.py:6499` `_JOURNAL_INSERT_PREFIX` | the same statement recognised **by SQL string prefix** — see §A6 D-09 |
| `record_projection.py:6545` `replay_plan` | the replay driver: `enumerate(records, start=start_seq)` → `_journal_row` |
| `record_writer.write_live_record` (`record_writer.py:1541`, `position=` flag) | the **live** writer of the same row, from the append's returned position |
| `cache_projection.py:2952` `_LIFECYCLE_EVENTS_SQL` → `:2988` | the **one reader that folds** it. Its own header says *"`seq` orders it"* (`:2952`) and the ordering is applied by the consumer: the statement as written carries **no `ORDER BY` and no cursor**, and it selects only the lifecycle/state-op/creation classes by predicate. So it is **gap-tolerant** — which matters for A4 — and it does not read the table as a feed |
| `postgres_bytes.py:118` / `cost_dimensions.py:492` / migration `20261114000001` | the **byte meter**, positioned by `p_seqs` (`#12762`) |
| `record_read.py:34` | **explicitly does NOT read it** — *"It never reads `public.journal_event`, and it never reads the graph."* |
| `purge_org_record(p_org_id)` (`20261012000005`) | the only delete path (org cascade) |
| append-only **trigger** (`20261012000001:96`) | an UPDATE is refused; a consumed position is not revisable |

### A2.4 `:GraphEvent` and `:GraphEventMeta` — the second store and its own allocator

```
$ rg -n "^def (next_seq|capture_watermark|reestablish_watermark|append_event|read_after|purge_expired|purge_overflow|ensure_event_schema|degraded|degraded_graphs|clear_degraded|record_degraded|_refresh_first_seq)" tortoise/event_store.py
222 record_degraded · 270 degraded · 284 degraded_graphs · 318 clear_degraded
388 ensure_event_schema · 427 next_seq · 524 capture_watermark · 571 reestablish_watermark
777 append_event · 922 read_after · 1014 purge_expired · 1038 purge_overflow · 1062 _refresh_first_seq
```

```
$ rg -n "next_seq\(|append_event\(|read_after\(|capture_watermark\(|reestablish_watermark\(" tortoise/*.py tortoise/projection/*.py | grep -v "^tortoise/event_store.py"     # 15 lines, call sites only
tortoise/sdk.py:13832:                seq = next_seq(proj)
tortoise/sdk.py:13833:                append_event(proj, seq, type_, graph_payload, self.ulid())
tortoise/sdk.py:13587:        evs = read_after(proj, after_seq, types=types, limit=limit)      # poll_events
tortoise/webhook_delivery.py:1300:    events = read_after(proj, int(sub.get("cursor") or 0), ...)      # the retry sweep
tortoise/why_snapshot.py:526:        events = read_after(proj, int(min_cursor), limit=...)         # why-block precompute
tortoise/projection/__init__.py:13495 / :13695    capture → reestablish (engine 2)
tortoise/projection/__init__.py:2133              capture_watermark (the pre-wipe capture)
tortoise/projection/__init__.py:18902            reestablish_watermark (engine 5, record-sourced)
```

`_GRAPH_EVENT_TYPES` has **11 members** (`sdk.py:4671`), and the set is **defined by the store boundary**, not
by a list (`sdk.py:4658-4660`, `#12458` gate 1's verified quote). `:GraphEventMeta` appears at **44** sites in
`tortoise/` (recorded run `A4-GraphEventMeta-sites`), of which the allocator is `next_seq`'s `MERGE` at
`event_store.py:491` and the carries are `capture_watermark:557` / `reestablish_watermark:657`,`:738` /
`hosted_backup.py:842`,`:914` / `webhook_delivery.py:1052` / `sdk.py:13582` (`first_seq` for the 410).

### A2.5 The reconciliation

| population | count | source |
|---|---|---|
| journal append call sites | **3** | recorded run `A2-journal-append-sites` |
| journal replay engines | **5** (4 journal + 1 record) | §A2.2 table |
| journal readers, production parse sites | **11** + 4 bypass | §A2.2 table |
| `journal_event` writers | **1** | §A2.3 |
| `journal_event` folding readers | **1** | §A2.3 |
| `journal_event` byte-meter readers | **2** (py) + 1 RPC + 1 migration fn | §A2.3 |
| `_GRAPH_EVENT_TYPES` members | **11** | `sdk.py:4671` |
| `event_store` allocator call sites | **1** (`sdk.py:13832`) | recorded run `A2-event-store-callsites` |
| `event_store` feed read sites | **3** (`sdk.py:13587`, `webhook_delivery.py:1300`, `why_snapshot.py:526`) | same run |
| `:GraphEventMeta` mentions in `tortoise/` | **44** | recorded run `A4-GraphEventMeta-sites` |

**The number that reconciles the shape: the journal has 1 write primitive and 5 replay engines; the record has
1 write path and 1 folding reader; `:GraphEvent` has 2 stores' worth of bookkeeping and 1 allocator that is the
wrong one.** The asymmetry is the finding: **the rebuild source has the most readers and the fewest writers;
the delivery feed has the most machinery and no replay.**

---

## A3 — The lane matrix

| | **hosted** | **embedded (redisLite)** | **self-host (Docker compose)** | **CI** |
|---|---|---|---|---|
| **Journal file?** | **Yes, if `TORTOISE_EVENT_LOG_BASE_DIR` is set** (`config.py:84`, `fly.toml`, `entrypoint.sh`) — the hosted write path journals **nothing** when it is unset (`_resolve_event_log_path` → `None`, `durability-posture.md:49-52`) | **No** (a `path=` DB, no base dir unless exported) | **Yes since `#8526`** — armed by a **default** `<data_root>/events` **only when the data root is a persistent MOUNT** (`config.ensure_selfhost_event_log`, `os.path.ismount` + `/proc/self/mountinfo`, *not* `isdir`) | **No** for the carve-out (`TORTOISE_TEST_CARVE_OUT=1`, 17 embedded-only files); **yes** for docker-lane tests that set the base dir |
| **Record DSN?** | **Yes** with `TORTOISE_RECORD_DSN` (default **OFF**, presence-based: `record_writer.py:266`) | **No** | **No, and this is an owner decision** — *"Ship Postgres to self-host now — Product scope — **parked by the owner**"* (`#8526`) | **Yes** in the pglite/pgvector tests; **no** in the ordinary suite |
| **Which A2 sites are live** | all 3 journal doors; `write_live_record`; `cache_projection`'s `journal_event` fold; all 5 replay engines; `next_seq`/`append_event`/`read_after`; the 30-day + 500k purge (`hosted_api.py:2317-2345`); the byte meter | **none of the journal ones.** The graph is written directly; `_emit_event`'s JSONL leg no-ops | the 3 journal doors; **no record** → `write_live_record` returns `NOT_ARMED`; the 5 engines minus the record's; `next_seq`/`append_event`| the journal doors where a base dir is exported; the record only under the pglite harness |
| **Authority for the derived layer** | journal (rebuild) **and** `public.point`/etc. (the record is the system of record, §2.0) — **two answers to two questions**; see §6 | the graph is the **only** copy | **the journal is the only truth store** (`#8526`: *"REBUILDABILITY, not DURABILITY"*) | the test fixture |
| **What "just delete the file" breaks** | the record's **arming** (a record with no journal has no ordinal to copy), the 30-day feed's writer, and every rebuild (the derived layer becomes the only copy — §3's invariant) | **nothing to delete** | **everything**: no record, so the journal is the only rebuild source | the carve-out's embedded lanes are unaffected; the docker lanes lose rebuild coverage |
| **Durability mechanism (§durability-posture)** | the store's backup: graph archives ≤1 h, **record dump — unbounded today, no cron** | AOF off by default | AOF + **declared** off-box target (`#8543`/`#8544`), **not drilled** | n/a |

**The matrix's load-bearing row is the second: the record is armed only where the journal already exists.** A
Deployment that has no journal has no ordinal to give the record, and a deployment that has an ordinal
allocator with no journal is, today, unbuildable. That is the dependency the issue names — and A7.5 shows it
is a **wiring** dependency, not a logical one.

---

## A4 — The ordinal

### A4.1 Who allocates today — and it is decided, not accidental

**The journal file allocates.** `tortoise/log.py:828-845`:

> `public.journal_event` keys the position a record consumed on `(org_id, graph_id, seq)`, and the schema
> calls `seq` *"the idempotency key"* … **THE SOURCE IS THE JOURNAL ITSELF. A record's position is its 1-based
> index among the records a REPLAY would read — `read_all(salvage=True)`, the ONE reader the replay engines
> use (`read_for_replay`)** — so a live write and a full replay number the same record the same way, **by
> construction rather than by agreement**.

The mechanism: `EventLog.append` (`log.py:1393`) returns `int | None`; a **sidecar counter** is maintained
**inside the append's existing critical section** — `_APPEND_LOCKS` (one `threading.Lock` per journal,
`log.py:620`) plus `flock` plus one `write(2)` (`log.py:594-620`) — and validated
against `(size, mtime_ns, st_dev, st_ino)` so a stale cache costs a **recount**, never a wrong answer
(`log.py:1894` `position()`, `log.py:1207` the recount, `log.py:1211` `read_for_replay`). `#9190` chose this
over three alternatives, and **`MAX(seq)+1` was rejected by name** (`log.py:836-839`):

> a number a later replay would either collide with (`ON CONFLICT DO NOTHING` silently absorbs the replay's
> position row) or duplicate (one event recorded under two seqs). Both are silent.

### A4.2 What `journal_event.seq` *means*

- **Not** an identity. The row's identity is `(org_id, graph_id, seq)`; the *record's* content identity is its
  `event_id` / `ts` / `type`.
- **An assertion about what the record consumed**: "this record was the N-th record the replay reader would
  see". The schema calls it *"the idempotency key: applying a position twice is a no-op"* (`03-schema.md:86`,
  `record_projection.py:5999-6003`).
- **Per-graph**, and valid as such **only because one graph = one journal file** (`record_writer.py:2409`
  states the coupling: *"construction holds per JOURNAL FILE, while `journal_event`'s key is per"*).
- **Irreversible**: append-only by **trigger** (`20261012000001:96`), inserted `ON CONFLICT DO NOTHING`.

### A4.3 What `:GraphEventMeta` is for — and why it is *not* the same ordinal

`:GraphEventMeta{last_seq, first_seq}` is the allocator for **`:GraphEvent`**, the graph-resident
30-day delivery/audit stream. `next_seq` (`event_store.py:427`, `MERGE` at `:491`) mints it; `MAX_SEQ =
2**53 - 1` (`:381`) because FalkorDB compares numeric ranges in double precision and `read_after`'s
`WHERE e.seq > $after` **silently drops** rows above `2**53` (`durability-posture.md:264-380`).

`#9190` refused it as the source, verbatim:

> `event_store.next_seq` / `:GraphEventMeta` — a **different** seq over a different population; using it
> would make the live ordinal and the replay ordinal diverge again.

**So there are three monotonic counters in the system**, and only one is `journal_event.seq`:

| counter | store | allocated by | population | journalled? |
|---|---|---|---|---|
| journal position → `journal_event.seq` | the JSONL file (+ sidecar) | `EventLog.append` | journal records | it **is** the journal |
| `:GraphEventMeta.last_seq` | FalkorDB | `event_store.next_seq` | `:GraphEvent` rows (11 types + `PointRevised` under the change-feed switch) | **no record describes it** — declared at `event_store.py:52-57` |
| `:EpMeta.ep_version` | FalkorDB | `SDK._advance_ep_version` | EP ordering epochs | **no** — declared `graph-metadata:ep-epoch` (`#9123`), carried by nothing, reset by a rebuild (`#5166`) |

**That third counter is a finding, not a footnote.** `durability-posture.md` documents two monotonic
counters' fate across a wipe (`:GraphEventMeta` carried, `:EpMeta` explicitly **not** preserved because
preserving it *"would pin a stale epoch"*). So the system's two counters have **opposite** rebuild
semantics by design, and the code already knows it.

### A4.4 What an issued cursor means

Two different things are called a cursor, and conflating them is how "move the ordinal" becomes a silent
break:

| cursor | coordinate | who holds it | what a change does |
|---|---|---|---|
| **feed cursor** | **`:GraphEvent.seq`** | every `next_cursor` ever issued, every stored `webhook_subscription.cursor`, every parked `poll_events` caller | **`#12458`'s BK3 (fatal):** every issued cursor *is a `:GraphEvent.seq`*, so serving journal positions *"silently re-points live cursors at a different coordinate system"* |
| **journal tail cursor** | an opaque token (`log.read_after`, `log.py:2555`) | **no production caller** (`#12458` row 18) | re-reads and slices the whole log per poll; a *capability*, not a substitute |
| **diff boundary** | a second-resolution instant (`#1990`) | the diff verb | items older than the cursor's second are provisional |

Plus the **expired-cursor** arm: `poll_events` answers **410** using `:GraphEventMeta.first_seq`
(`sdk.py:13582`, `event-catalog.md`, `durability-posture.md:283-300`). `first_seq` is deliberately **not**
carried across a rebuild — the truthful floor is `last_seq + 1`, so no cursor survives.

### A4.5 What breaks if the record allocates

This is the question the hypothesis assumes, and the answer is **not** "nothing" — it is four specific
things, one of which `#12458` already measured as fatal:

1. **The `enumerate(records, start=1)` replay numbering** (`record_projection.py:6545`) would become a
   *second* allocation, and `ON CONFLICT DO NOTHING` would silently absorb the difference. `#9187`'s
   `start_seq` seam exists precisely to stop a tail replay renumbering to `1..` — it would have to become
   *"read the carried seq"* instead of *"continue the enumeration"*.
2. **The live path's only ordinal source disappears** unless something replaces it. Today `append` returns
   it; `write_live_record(position=True)` writes it. With no journal, `position=False` is the shipped shape —
   the row is simply omitted (`record_projection.py:6403`).
3. **The feed's coordinate system is untouched but its *substitute* is not**: `journal_event.seq` is not
   `:GraphEvent.seq`, and `#12458` BK3 says re-pointing cursors is fatal. So a record-allocated ordinal
   **cannot** become the feed cursor without a migration of every stored cursor — a separate decision.
4. **The gap semantics change.** `journal_event` is read by `cache_projection` with **no ORDER BY and no
   cursor** (§A2.3), so gaps are tolerable *there*; but the journal's own consumers assume contiguity (a
   torn tail is sealed and shifts no index). A record allocator that permits gaps makes "index N" and
   "position N" different claims, which is exactly what `#9190` avoided.

---

## A5 — What already exists (so nothing is proposed that is already built)

| Capability | Exists? | Where |
|---|---|---|
| Journal append returning the record's **position** | **YES** | `log.py:1393`, sidecar at `:1894`, `#9190` |
| `journal_event` table, `(org_id, graph_id, seq)` PK, `payload jsonb` | **YES** | `20261012000001_record_event_stream.sql:81` |
| `journal_event` **append-only by trigger** | **YES** | same file `:96` |
| `journal_event` **node index** | **YES** | same file `:141` (`org_id, graph_id, node_label, node_key`) |
| `journal_event` **byte meter per org** | **YES** | `#12762`, `20261114000001`, `postgres_bytes.py`, `cost_dimensions.py:492` |
| Record **org purge** RPC | **YES** | `20261012000005_record_org_purge_rpc.sql:22` |
| Record **tenancy role + GUC** | **YES** | `20261025000001_record_tenancy_role_and_guc.sql` |
| Live record writer fed by the append's ordinal | **YES** | `record_writer.write_live_record` (`:1541`), 3 doors (`#9078`) |
| Record **replay** with a `start_seq` watermark seam | **YES** | `record_projection.replay_plan` (`:6505-6545`), `#9187` |
| **Record → cache** projection (a 5th rebuild engine) | **YES** | `cache_projection.py`, `FalkorProjection.rebuild_from_record` (`projection/__init__.py:19188`) |
| Record **reconcile** with a production trigger | **YES** | `record_reconcile.py` (`python -m tortoise record-reconcile`), `#9189` |
| Record **backup / restore** verbs + a drill | **YES (drill not against hosted)** | `record_backup.py`, `record-restore` (`durability-posture.md:500-560`) |
| Journal **segments** (manifest + boundary + a raising reconstruction claim) | **YES, writers flag-gated OFF** | `journal_segments.py`, `TORTOISE_JOURNAL_SEGMENTS` |
| Atomic-write **ordering** on 4 doors | **PARTIALLY** | `create_point` (`#7951` inc 1 — **the record IS the commit point**), `update_point` revision arms (inc 2a), `delete_point` (**order reversed by `#9207`**), onboarding (declared, not journalled) |
| **`last_applied_seq` on the record** (per-projection watermark) | **NO** — and this is the gap | the only `last_applied_seq` is `consistency.py:1341-1396`, a **file-based** journal↔graph watermark, not a record-side one. `#7951` inc 2's second half is *"STILL OPEN"* |
| **A transactional outbox table** | **NO** (and none is needed for `journal_event` — the table *is* the outbox, because it is written in the same transaction as the layer rows) |
| **A record-side ordinal allocator** | **NO** |

**⇒ The plan proposes only the last row.** Everything else in the ideal shape has a shipped implementation;
what is missing is (a) the last row, (b) `last_applied_seq`, and (c) the *routing* that makes them the only
path (§A6 D-01).

---

## A6 — The debt map

Every row is classified **B = blocks the ideal shape** · **O = orthogonal** · **R = must be retired**. Rows
marked ⚑ are named by **no document** and were found by reading code in this lane.

| # | Debt | Where | Class | Why |
|---|---|---|---|---|
| D-01 ⚑ | **The cache has no chokepoint.** Two measurements disagree — `STORAGE-ARCHITECTURE.md:1700` says *"387 mutating `.query(` sites across 211 functions in 32 files"* (2026-10-08); `2026-10-14-7951-atomic-write-outbox.md:36` says *"442 mutating `.query(` sites in 38 files, **288 on the raw handle**"*. Both are labelled measurements of the same surface. | `tortoise/**` | **B** | "One atomic write" is unreachable while 288+ sites can write the cache directly. **The two counts must be reconciled before Stage C sizes the work** — a plan built on the wrong denominator is the `#3895` class. |
| D-02 ⚑ | **`_journal_row`'s statement is recognised downstream by SQL string prefix** (`_JOURNAL_INSERT_PREFIX = "INSERT INTO public.journal_event"`) | `record_projection.py:6499` | **R** | A reformat of the INSERT (whitespace, column order, a `RETURNING`) silently breaks whatever matches on the prefix. Replace with the `Statement` object's identity. |
| D-03 | **The record has no `last_applied_seq`** — you cannot ask "is the record caught up?" | absent; `#7951` inc 2 | **B** | Without it, a record that lags its journal is indistinguishable from one that has caught up. `#9078`'s failure posture is *"counted … and a replay heals them"* — but nothing says how far behind. |
| D-04 | **`_emit_event` writes `:GraphEvent` BEFORE the JSONL append** | `sdk.py:13832-13833` then `:14008`; declared as `#7951` residual 7 → `#8696` | **B** | The append-failure arm leaves an **orphan `:GraphEvent`**: a feed row for a mutation that never committed. `#9208` made the loss *visible*; it did not remove the orphan. Any "one atomic write" must state where this store sits. |
| D-05 | **The replay fold is not parity-equal to the live write**: `_POINT_DENY` drops `ep_alpha`/`ep_beta`/`baseline_set`/`baseline_source` on replay | `projection/entities.py:2472`, `#8699` | **B** | This is *why* `#7951` rejected replacing the raw `CREATE` with the fold (`2026-10-14-…:§8.2`). Increment 3 (*"the cache driven from the record"*) cannot land before it. |
| D-06 | **`rebuild_all` does not replay `:GraphEvent` rows at all** | `#4664`, consolidated into `#5048`, unfixed | **B** | So `:GraphEvent` is neither a cache nor a durable log today: it survives no rebuild, and its counter has to be **carried** by hand (`#4653`). |
| D-07 | **`:GraphEventMeta` is a *second* allocator over a different population** | `event_store.py:427`, `#12458` left standing | **B** | `#12458`'s own closure says it is *"a real defect independent of the mirror's fate, and it is the part worth a ticket"*. No ticket exists. |
| D-08 ⚑ | **`:EpMeta.ep_version` is a third monotonic counter, unjournalled, reset by a rebuild, with a documented late-sweep consequence** | `projection/__init__.py`, `#9123` lane, `#5166` | **O** | It is an EP ordering *bound*, not a storage ordinal. Declared, pinned, and its consequence (`_sweep_dirty_roots` cannot clear flags until the epoch re-climbs) is recorded. Not in this shape's path. |
| D-09 | **The journal has no bound of any kind** — no rotation, compaction, retention. The live volume is 974 MB at 78 %, ~4 days of headroom at ~50 MB/day | `#9025` **OPEN**; `#5612` item 2 never delivered | **B** | Not because a bound is needed for atomicity, but because the journal is the very thing the ideal shape keeps for the no-record lanes. A capacity failure converts a rebuild source into an outage. **Owner decision; this lane does not choose one.** |
| D-10 | **The vector payload was widened to float64 decimal text** (P0 landed) and the **base64 step is blocked** | `#11821` P0 landed; `#12459` **blocked** — its `P1` gate points at `#12458`, which closed `NOT_PLANNED` | **O** | It changes bytes, not shape. But it is **blocked on a released dependency**, which is a process debt: `#11821`'s rule ("P1 merged and measured") can never be met, and `#12459` says so itself (`:777`). |
| D-11 | **A build rollback over a frame-bearing journal drops vectors silently and reports success** | `2026-11-13-12459:824` | **O** | A migration hazard for a format change, not a shape defect. Its remedy (readers ship before writers) is the same discipline A7.6 asks for. |
| D-12 | **Three doors gate on a shared `_journal_write_failures` counter delta**, which a concurrent writer can move | `#7951` residual 10; `#5612` | **B** | `create_point` was moved to the per-call outcome (`True`/`False`/`None`); the siblings were not. A commit point decided by a shared counter is a racy commit point. |
| D-13 | **`create_point` keeps three named pre-append writes** (`:EpMeta`, `:GraphEvent`, the `:Source` adopt-on-touch) | `2026-10-14-7951:§3` | **B** | They are the reason "the append is the commit point" is only *nearly* true. Each is disclosed; none is closed. |
| D-14 | **`apply-first` is a declared class of eight unjournalled writer lanes** | `STORAGE-ARCHITECTURE.md:1672-1698` | **O** | Declared, pinned per lane (`tests/test_9206_apply_first_lane.py` + the census). It is a *declaration of where writes live*, and the doc says so: *"a declaration names where the write lives; it does not make the write replayable."* |
| D-15 | **The JSONL is a PARTIAL WAL** — a delta cannot reproduce an entity whose creation was never journaled; a raw-Cypher or no-base-dir write journals nothing | `2026-09-22-unjournaled-mutation-class:1250` | **B** | §3 says *"journal completeness is a PRECONDITION"*. This is the precondition being false in named places. It is why "delete the file" is unsafe **today** and why A7.5 sequences the change. |
| D-16 | **Torn-tail tolerance is an allow-list of additive types** | `log.py` seal + `read_all(salvage=True)`; `#5917`; `#9254` | **O** | Correctly scoped: a `EntityMutated` op=delete dropped by a replay is **resurrection, not an exception** (`STORAGE-ARCHITECTURE.md:361`). Its existence is a *constraint on any new ordinal* — a torn tail must not shift a position. |
| D-17 | **`_NO_PROJECTION_FOLD`** declares two types whose replay materialises no node | `projection/__init__.py:8029`; `SessionNarrated` `#9555`, `TurnsRecorded` `#10017` R2 | **O** | A decision the dispatcher *states* (`STORAGE-ARCHITECTURE.md:361` calls it "a declared exception"). But it is the mechanism by which `derived = replay(journal)` became **not** exact, which `#10017` used to falsify §3's promise. |
| D-18 | **`#10017` remains a no-op on a one-sided measurement** — closed as no-op locally, reopen condition = hosted armed, and the hosted arm has since reported `record.armed=true` | `#10017` closure + the later comment | **B** | The purge question is live again on a deployment nobody has measured. This lane does not reopen it (#9025/#10017 are explicitly out of scope) but the map must say the "no-op" reading is local-only. |
| D-19 | **The `:GraphEvent` mirror rents 32,032 B/session for a watermark** — and the premise is now stale | `#11821` body; removed by `#10017`'s carrier on the armed lane | **R** (the *premise*), **B** (the allocator) | Retire the **premise**, keep the store (owner-closed `NOT_PLANNED`), fix the **allocator** (D-07). Three distinct fates for one paragraph. |
| D-20 ⚑ | **`record_read.py` never reads `journal_event`** — the transcript is served from `public.session_narrative`/turn rows, so the record's *read* path does not depend on the journal table at all | `record_read.py:34` | **O** | A positive finding: it means the record's read surface is already journal-independent, which is a precondition A7 does not have to build. |
| D-21 ⚑ | **`journal_event` retention has no ruling anywhere.** `#3843`/`#3853` bind `analytics_events`; `#12762` says so explicitly | `#12762` body | **B** | The record table gets the journal's growth **in addition to** the journal, forever, with no decision on pruning it. `#9025`'s bound decision, whenever it lands, will be about the file only. |
| D-22 ⚑ | **The governing documents are not registered in `docs/00_index.md`**: `STORAGE-ARCHITECTURE.md` has no index row; `event-catalog.md` has **0** mentions; the `#7951` atomic-write plan has **0** | §A1.0 | **B** | A lane proving completeness from the index — as this mandate instructs — **cannot find the document that governs the subject**. This is the same class as the `#10114` lesson: a green check that answers a different question. |
| D-23 ⚑ | **`docs/00_index.md`'s `#5612` cell still presents the pre-`#11821` vector figures as the current cost** in its first clause, then corrects them 40 lines later in the same cell | `docs/00_index.md:153` | **O** | Deferred to the doc's own errata discipline; noted so Stage D does not re-derive it as a new finding. |
| D-24 | **`record_writer.py:2409` states the construction coupling in a comment only**: the ordinal's validity holds *"per JOURNAL FILE, while `journal_event`'s key is per"* | `record_writer.py:2409` | **B** | It is the single sentence that makes the whole shape hang together, and nothing tests it: a deployment with two journal files for one graph would issue colliding positions and `ON CONFLICT DO NOTHING` would hide it. **No such pin exists** — check `tests/` for one in Stage B. |

**24 rows: 13 block, 8 orthogonal, 3 must be retired.** The three must-retire rows are each a *premise* or a
*string coupling*, not a code path — which is itself the finding: **almost nothing in this shape has to be
deleted. What must change is who owns one number, and how many doors there are.**

---

## A7 — The ideal shape, stated against the map

### A7.1 The shape, in one paragraph

**One write, one ordinal owner, one authority per question.** A mutation commits **once**, in Postgres, in a
single transaction that inserts the layer rows **and** the `journal_event` position row; the ordinal is
allocated **inside that transaction** by a per-graph counter row, so allocation order *is* commit order; the
cache is materialised from the record (never from the file); the JSONL becomes an **optional export** that a
lane without a record writes and no lane reads on the write path; `:GraphEvent` keeps its 30-day delivery
role but stops minting its own positions.

### A7.2 What the map contradicts in the hypothesis

The issue's hypothesis has five clauses. The map **supports three, narrows one, and contradicts one**. The
contradicted one is the crux.

| hypothesis clause | verdict | the map says |
|---|---|---|
| "One atomic write — the record insert and the projection land in the same transaction (or an idempotent outbox)" | **SUPPORTED, and mostly already built** | The record **is** an outbox in the only sense that matters here: `journal_event` is written in the same transaction as the layer rows (`record_projection.replay_plan` emits both from one plan; the live writer does the same). What is missing is *routing* (D-01), not a mechanism. §3:342 already names the outbox form as the answer. |
| "The graph stays derived — unchanged" | **SUPPORTED** | §2.0/§3/SEARCH §3, all read. Nothing in this lane touches it. |
| "The JSONL becomes an OPTIONAL export … on hosted it stops being on the write path" | **SUPPORTED, with a condition the hypothesis does not state** | On self-host there is **no record** (`#8526` parked it). So the JSONL cannot become "optional" *everywhere*; it becomes **the lane-specific primary** on self-host and embedded, and an export on hosted. The hypothesis's phrasing implies one shape; the matrix (A3) says two. |
| "Fly disk leaves the critical path" | **SUPPORTED** | Follows from the above once the record is armed on every hosted graph — including the case §A3's first row names where **`TORTOISE_EVENT_LOG_BASE_DIR` is unset and the hosted path journals nothing at all**. |
| **"The record owns the ordinal. `next_seq`-style allocation moves to the record so a position exists without a file."** | ⛔ **CONTRADICTED — by a recorded decision, and the reason is mechanical, not stylistic** | **`#9190` decided the opposite and rejected `MAX(seq)+1` by name** (`log.py:836-839`). The record does not allocate today; it *copies*. And the crux sentence contains a second claim that is **false as written**: a `next_seq`-style allocation does **not** give you *"'a position' without a file"* — it gives you a position **without a commit order**. PostgreSQL sequences are deliberately nontransactional: `nextval()` advances immediately, is not rolled back, and concurrent callers do not wait for earlier transactions to commit, so "a consumer that sees 11 may wait forever for 10, or incorrectly conclude that the log is complete" (`docs/…` — external, §9.2, verified in this repo only as *"there is no sequence anywhere in the record schema"*: `rg 'nextval|CREATE SEQUENCE' supabase/migrations/` returns nothing). A bare sequence is the **worse** choice, not the better one: it would replace a number that is correct-by-construction with a number that is correct-by-luck. |
| *(implied)* "the ordinal is the crux" | **NARROWED** | The crux is not the ordinal; it is **the arming dependency** — the record is only written where a journal record already exists to hand it (§A2.1, §A3 row 2). Fix that and the ordinal follows; fix the ordinal and the dependency remains. |

### A7.3 The two other recorded positions, and whether this recommendation touches them

| recorded position | touched? |
|---|---|
| `durability-posture.md:48` — the JSONL *"is never the durability mechanism"*; authority lives in a store-managed artifact | **Not reversed. Unchanged.** A7.1 keeps durability = the store's backup. The record-allocated ordinal does not make the file durable and does not make the record one — it makes the *record* the ordering authority, which is a fourth question (§6). |
| `STORAGE-ARCHITECTURE.md:316` — `derived = replay(journal) NOT recompute(sources)` | **Not reversed. Its subject changes, and that is the reopen A7 argues.** If the ordinal is carried in the record, the invariant becomes `derived = replay(record)` on the hosted lane — **the same invariant with a different source of the same records**. On the no-record lanes it is unchanged. §3:285 already scopes the journal to *rebuild*, not durability; A7 keeps it scoped to rebuild and changes only *which physical copy rebuild reads from*. |
| The graph-is-a-derived-copy decision (`SEARCH-ARCHITECTURE.md:157`, `STORAGE-ARCHITECTURE.md:116`) | **Untouched.** |

### A7.4 The "three stores" framing is an undercount, and that changes the target

The issue says "no app-written file as a **third** store". The live `create_point` path touches **five**
independent durable surfaces per logical write, in this order (`2026-10-14-7951:§3` + `sdk.py`):

1. `:EpMeta.ep_version` — a graph counter (MERGE, unjournalled, declared `#9123`)
2. `:Source.canonicalUrl` / `.urlAliases` — an adopt-on-touch write
3. `:GraphEvent` row **+** `:GraphEventMeta.last_seq` — the delivery store **and its own counter**
4. the JSONL append — **the commit point** (`#4240`, `#7951`)
5. `public.journal_event` + the layer rows
6. the FalkorDB cache `CREATE`/`MERGE`

**So "one atomic write" is really "collapse six statements across four stores and three counters into one
transaction, and declare the two that must stay outside it."** The two that must stay outside are named by
`#7951` residual 5 and are not optional: `:EpMeta` and the `:Source` adopt-on-touch both write values **into
the payload**, so they must precede it. **Any plan that promises a single transaction without naming those
two is promising something the code cannot do.**

### A7.5 The mechanism by which the record *can* allocate the ordinal — and the reopen it requires

⛔ **First, the honest framing: this is a REOPEN of `#9190`, not an adoption.** The route is to argue it on
`#9190` (and on §3's invariant), with the evidence; it is **not** to implement it. This lane proposes the
argument; the owner rules.

**The reopen's argument.** `#9190`'s reasoning has two steps: (i) replay numbers records by read order; (ii)
therefore only the file can produce the number replay will agree with, and a guessed number collides or
duplicates. **Step (ii) is only true of a *derived* number.** If the record allocates the ordinal **and the
journal record carries it**, then replay *reads* the number instead of computing it, and (i) stops being a
constraint. The disagreement `#9190` feared came from a *fabricated* number, not from a *stored* one. So the
reopen is one sentence: **replace "replay derives the ordinal" with "replay reads the ordinal"** — after
which any transactional, commit-ordered allocator is correct.

**The mechanism, concretely.** Not a sequence. A **per-graph counter row** updated in the same transaction as
the insert:

```sql
-- record_cursor(org_id, graph_id, seq) ; one row per graph
UPDATE public.record_cursor
   SET seq = seq + 1, updated_at = now()
 WHERE org_id = %s AND graph_id = %s
RETURNING seq;                       -- then INSERT INTO journal_event (…, seq, …)
```

**Why this and not `nextval()`.** The increment is *transactional*: a rollback restores the counter, so a
position is never burned and never reused-after-being-seen. And the row lock **serialises writers per graph**,
which is what makes **allocation order equal commit order** — the property a bare sequence cannot give. The
cost is one hot row per graph. **This repo already pays that cost in the same shape**: the journal append is
serialised per graph by `_APPEND_LOCKS` + `flock` + one `write(2)` (`log.py:594-620`), so the record
allocator is a *lateral* move in concurrency, not a new bottleneck.

⚠️ **And the record schema already carries an IDENTITY sequence — which I had recorded as absent and was
wrong about.** `public.relation.id` is `bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY`
(`20261012000003_record_epistemic.sql:167`), and its separate sequence ACL is a live operational fact
(`20261025000001_record_tenancy_role_and_guc.sql:205-212` — without the grant, *"EVERY app-role INSERT into
`relation` raises `permission denied for sequence relation_id_seq`"* and *"the whole edge-bearing record is
lost, not just the edge"*). **That is the strongest local evidence for the proposal and also its sharpest
warning:** the mechanism is not novel here, and the failure mode of getting its grant wrong is a lost
record, not an error message. It also draws the line precisely — `relation.id` may take gaps because it is a
**surrogate**; a `journal_event.seq` may not, because it is *"the idempotency key"*. So the allocator must be
the transactional counter row, **not** a sequence: same table, opposite requirement.

*(External basis for the sequence/rollback/commit-order claims: §9.2. What I verified **locally**:
`journal_event` has **no** id/sequence column — its key is the journal's position, `03-schema.md:86` — while
`relation.id` is the identity-sequence precedent above; and `journal_event` is append-only by trigger
(`20261012000001:96`), so an UPDATE-based counter must live in its **own** table, not on `journal_event`.)*

**What happens to an in-flight write across that change.** Four cases, each answered:

| window | today | after |
|---|---|---|
| ordinal allocated, transaction rolls back | n/a (the file's index cannot be taken back, but a torn tail is sealed and shifts no index) | **the counter rolls back too** — no gap, no burned position. This is the case a sequence cannot do. |
| record commits, journal export fails | n/a | **the record is authoritative**; the export is repaired by re-rendering from the record. The failure is now a *delivery* failure, not a *truth* failure. |
| record commits, cache write fails | replay heals (`#7951` T5) | unchanged — replay heals |
| journal export commits, record fails | **today's live ordering cannot produce this** (the record is written *after* the append, `#9078`) | **cannot happen**: the record is the commit point |

⛔ **And the ordering must flip, which is the real work.** Today: journal → record → cache. After: record
(+ordinal) → cache, with the journal **exported** from the record. That flip is a *reversal of the ordering
`#9078` recorded* and of `#4240`'s *"the journal append IS the commit point"*. It is therefore a **second
reopen**, and it must be argued where those decisions live — not slipped in under the ordinal change.

**The embedded / self-host lanes, which have no record.** The allocator becomes a **declared mode**, not a
replacement:

| lane | allocator | what carries the number |
|---|---|---|
| hosted, record armed | the `record_cursor` row (per graph, in-transaction) | `journal_event.seq` **and** the journal export line (`seq` field, new) |
| self-host (no record) | `EventLog.append` position, **as today** | the journal's own read order — `enumerate` is correct here |
| embedded | no journal, no ordinal | nothing needs one |
| mixed / migrating | the journal carries `seq` **only when the record allocated it**; absent ⇒ enumerate | per-record dispatch, exactly the shape `#12459` already plans for a format change (append-only ⇒ old lines are never rewritten ⇒ both shapes readable forever) |

**What that means for `#2488`'s "JSONL carries no seq".** It stays **true of today's file** and becomes
false of a *new* file: the sentence is scoped to the current format. Anyone quoting it to argue the ordinal
cannot be journalled is quoting a description, not a decision — and this is exactly why the sentence needs an
addendum rather than a re-derivation.

### A7.6 The falsifiers — what goes red if the shape is built wrong

Stage C needs these, so A7 states them rather than leaving Stage B to invent them:

1. **Two journal files for one graph** must be **refused** before any position is issued (`record_writer.py:2409`
   says the coupling; nothing tests it — D-24). *Red today: no such guard exists.*
2. **A rolled-back record transaction must not burn a position**: allocate in a rolled-back transaction, then
   assert the next committed record's `seq` is the successor. *A `nextval()` implementation goes red on this
   one* — which is why it is the falsifier that distinguishes the two allocator designs.
3. **Two concurrent writers on one graph must produce commit-ordered positions**: interleave two transactions
   so the one that allocates first commits last, and assert the reader sees them in `seq` order with no
   missing row. *A bare-sequence implementation goes red on this one.*
4. **Replay must reproduce `journal_event.seq` exactly**, including after a tail-segment replay with
   `start_seq` (`#9187`) — i.e. the carried number wins over the enumeration.
5. **The no-record lanes must not require a `seq`**: self-host round-trip (`append` → `rebuild_from_log`)
   with no DSN, asserting no `journal_event` is attempted.
6. **A journal without `seq` in its records must still replay** (the migration arm) — the mixed-version
   falsifier `#12459` names for its own format change.

### A7.7 What Stage C must do first, in order

Derived from the debt map, smallest safe unit first — **not** from the ideal shape backwards:

1. **Reconcile D-01's two counts** (387/211/32 vs 442/38/288) before anything is sized. One command, and it
   is the denominator for everything else.
2. **D-03** `last_applied_seq` — additive, no ordering change, and it is the instrument you need to verify
   every later step.
3. **D-24** the two-journal-files guard — a one-test change that makes the current ordinal's premise explicit.
4. **D-07** retire the `:GraphEventMeta` allocator in favour of the record's position, on the record-armed
   lanes only. This is `#12458`'s own "part worth a ticket" and needs **no** cursor migration, because the
   `:GraphEvent.seq` values themselves do not change — only who mints them.
5. **D-05** (`#8699`) the fold's parity, which unblocks *"the cache driven from the record"*.
6. **Only then** the allocator move (A7.5) — the largest and most reversible-cost change, argued as two
   reopens first.
7. **D-09** the bound — owner decision, and it should follow the shape change, not precede it: bounding the
   file while the file is still the commit point converts a cost problem into a data-loss problem
   (`STORAGE-ARCHITECTURE.md:427` says exactly this).

---

## 6 — The tension between `durability-posture.md` and `STORAGE-ARCHITECTURE.md` §3, resolved in words

**Deliverable of this lane, and the reason it belongs in the plan rather than in either document.** The
tension is real and it is not resolvable by preference, because **the two documents are answering two
different questions and each is using the word "authority" for its own.**

**The three questions, stated so they cannot be confused again:**

| # | Question | Answer | Authority, and where it is recorded |
|---|---|---|---|
| **Q1 — durability** | *What survives the loss of the machine, the volume, or the app-written state?* | **The store's own persistence plus an off-box copy of it. Nothing else.** | `durability-posture.md:41-60`. The journal **is not** it (`:48`), because it is written outside the store's transaction, is incomplete in three named ways, and would be a dual-write hazard. |
| **Q2 — reconstruction** | *What makes the derived layer regenerable?* | **The journal** — it reconstructs a projection under changed fold logic, migrates engines, and audits beyond `:GraphEvent`'s 30-day window (`durability-posture.md:44-47`). | `STORAGE-ARCHITECTURE.md:316` (`derived = replay(journal)`), scoped at `:285`. |
| **Q3 — commit ordering** | *What does a live write commit to first?* | **The journal append.** `#4240`: *"the journal append IS the commit point on `create_point`"*; §17.1's `apply-first` lanes are the declared exceptions. | `STORAGE-ARCHITECTURE.md` §17.1 (`:1664-1698`), `2026-10-14-7951:§3`. |

**These are three different predicates over the same object, and every recorded position is true within its
own:**

- The journal is **not durable** (Q1) *and* is **the reconstruction source** (Q2) *and* is **committed to
  first** (Q3). There is no contradiction in that sentence; there is a contradiction only if "authority" is
  read as one thing.
- **§3 already says so** — `:285` is headed *"The journal's scope: REBUILD, not durability"* and states
  *"DURABILITY = the store's backup (#2881); REBUILDABILITY = the journal."* So the resolution is **in the
  document**, not invented here.

**Then why did the shape drift, if the resolution is written?** Three reasons, and each is a fixable defect
rather than an ambiguity:

1. **The qualifier is in a subsection while the unqualified claim is in a table.** `:316`'s `derived = replay
   (journal)` is the section's headline invariant; `:285`'s scope note is a `####` heading 31 lines earlier.
   A reader who lands on the invariant does not necessarily read the qualifier — and the invariant is quoted
   elsewhere without it (including in `#12894`'s own body, which is why the issue believes the two documents
   disagree).
2. **`STORAGE-ARCHITECTURE.md:361` still says the journal *"is truth"*** — *"They are also recorded in the
   journal, which is truth."* That sentence is defensible under Q2 and indefensible under Q1, and it is the
   one that `durability-posture.md:660-708` is built to delete. It survives because it does not match the
   durability gate's claim *shapes* (`:660-708`, `#9867` lists the blind wordings and says the list is not
   complete). **So the two canonical documents are consistent in their reasoning and inconsistent in their
   unqualified sentences, and the gate cannot see the second one.**
3. **Q3 has no home.** Neither document is the recorded authority for *commit ordering*. `durability-posture`
   owns Q1 by declaration. §3 owns Q2 by its invariant. Q3 lives in **`docs/plans/2026-10-14-7951-…` §3 and
   in `#4240`'s comment thread** — a plan document and an issue, for a rule that governs every write in the
   product. **That is the actual defect:** the axis with the most operational consequence has the weakest
   recorded home, so it is the one that gets re-decided per lane.

**The resolution, in one sentence, to be carried into whichever document claims the vocabulary next:**

> The journal is **authoritative as a total order over decisions** and **never authoritative as durable
> storage**. Durability is the store's backup (Q1); reconstruction is the journal (Q2); the live path commits
> to the journal append first (Q3). A statement that says "the journal is truth" without naming which of the
> three it means is the ambiguity, not the fact that all three are true.

**What this does *not* do.** It does not re-word either document (that is a doc change with its own review,
and `#9867` owns the wording sweep). It does not rescue `:361` — that sentence should be qualified, and
filing it is the right next step rather than editing it inside an archaeology PR. It does not claim Q3 is
right: A7.5 proposes *reversing* Q3 on the hosted lane, which is why Q3 needs a recorded home **before** it
is changed, or the change will be invisible to the next lane.

---

## 9 — Research provenance, and what was verified locally

### 9.1 Rung taken

**Rung 1 (`mcp__seo-intelligence__perplexity_search`) was UNAVAILABLE** — the server is not in this
session's MCP catalog (`mcp_catalog` lists exa, brave-search, playwright-browser, gemini, tortoise,
search-console; no `seo-intelligence`). **Error class: server absent, not an auth failure.**
**Rung 2 taken**: direct Perplexity API, model `sonar` only (approximately $0.02 total for 3 queries; no
`sonar-deep-research` / `sonar-reasoning-pro` call was made). `PERPLEXITY_API_KEY` is not exported in a fresh
shell but **is** in `$HOME/.env` — the same routing repair `#12459` recorded, which confirms its note rather
than re-deriving it.

### 9.2 What each source gave, and whether it was verified locally

| Claim taken | Source | Verified locally? |
|---|---|---|
| The transactional outbox exists to prevent the dual-write failure (commit-then-publish-fails, or publish-then-rollback), and delivery is normally **at least once** so consumers need durable event IDs and idempotent handlers | Debezium's outbox documentation (`debezium.io/documentation/reference/stable/integrations/outbox.html`) | **YES, in this repo's own vocabulary**: `journal_event` keys `(org_id, graph_id, seq)` as *"the idempotency key: applying a position twice is a no-op"* (`record_projection.py:5999`), and `ON CONFLICT DO NOTHING` is the idempotent handler. The record **is** an outbox under this definition. |
| Same-transaction projection is the right choice **only** when the projection is local, small and operationally essential — otherwise it couples every schema change to the write path and multiplies lock contention | same | **PARTIALLY**: this repo already pays that cost knowingly — the record's plan emits layer rows *and* the position row from **one** plan (`replay_plan`). The map records the cost as `ρ` (§17.4), measured. No new claim is made from this. |
| PostgreSQL sequences are **nontransactional**: `nextval()` advances immediately, is not rolled back, and concurrent callers do not wait — so a sequence cannot provide a gapless or **commit-ordered** ordinal, and a consumer seeing 11 may wait forever for 10 | `postgresql.org/docs/current/sql-createsequence.html`, `…/functions-sequence.html` | **PARTIALLY, and my first pass got this wrong** (corrected in place rather than restated): I claimed the record schema had *no sequence at all*; it **has one** — `public.relation.id` is `bigint GENERATED ALWAYS AS IDENTITY` (`20261012000003:167`), whose ACL split is documented at `20261025000001:205-212`. What is true is narrower and is the load-bearing half: **`journal_event` has no sequence and no id column** — its key is the journal's position — and the one sequence the record does carry is a **surrogate**, where gaps are harmless, which is exactly the property `seq` cannot have. The claim that a sequence is nontransactional is PostgreSQL's own documented behaviour and is **not** independently re-measured here — stated as external. |
| A **transactional counter row** (`UPDATE … SET c = c + 1 RETURNING`) gives gapless committed numbering by serialising on one row, at a throughput cost | same | **YES for the cost analogue**: the journal already serialises per graph on one file handle (`thread lock + flock + one write(2)`, `log.py:820-826`), so the per-graph serialisation is a lateral move. The row-lock semantics themselves are not measured here (no Postgres bench was run — a bench is a real cost and A7.5 is a proposal, not a landing). |
| Migrating an app-written WAL into the store: deploy the **new reader first**, backfill, dual-write, reconcile by event identity, cut readers, then writers, then retire; and state whether the order is **allocation**, **commit** or **publication** order because they are not interchangeable | OpenSearch migration-assistant architecture; Kafka topic-migration practice (`acemq.com/guides/kafka-migration-guide`); `architecture-weekly.com/p/the-write-ahead-log-a-foundation` | **YES, and it maps onto choices this repo already made**: `#12459` plans exactly this shape for a format change (readers before writers, per-record dispatch, append-only ⇒ both shapes readable forever, `:824`'s silent-drop warning is the falsifier); `#8526` is the reader-first arm for the self-host wiring gap. The paper trail is in-tree, not imported. |
| Stored vectors: pgvector and every comparable delegate the vector to a store rather than a versioned on-disk format | (carried from `#12459`'s own survey, cited not re-run) | **YES**: `20261109000001_record_embedding_vector.sql` + `#11642` — the `embedding_vec vector(384)` GENERATED column is exactly that delegation. |

**Tagged as NOT verified locally, and therefore not relied on for any decision in this document:** the
PostgreSQL row-lock throughput numbers (no bench run); the "at least once" delivery rate of any specific
relay (no relay is proposed); the OpenSearch/Kafka migration timings (they are pattern evidence, not
measurements of this system).

### 9.3 What I did with a sub-agent's finding I could not confirm at first

A cited line (`docs/plans/2026-09-22-unjournaled-mutation-class.md:1250`) appeared to say something
different from the quote supplied to me — until I read the **whole line** rather than its first 200
characters: the phrase *"The journal remains a PARTIAL WAL"* sits past the truncation. **The citation was
correct and my instrument was wrong.** Recorded because the inverse error — applying a false correction to a
correct artifact — is the one that propagates, and because the near-miss is the same class as the
"measurement artifact is not a state" rule: a truncated read is a measurement defect, not a state.

---

## 10 — What this lane did not do

- **No product code.** No migration, no allocator, no test was written or edited. The six falsifiers in
  §A7.6 are specified, not implemented.
- **No reopen filed.** A7.5's argument is stated; it is **not** posted as a reopen on `#9190` or `#9078`,
  because a reopen is the owner's to open and Stage B is where it is argued to convergence.
- **No bound chosen for the journal** (`#9025`) and **no absorption of `#9025`/`#12762`** — both were out of
  scope by the brief, and the map records their state without deciding them.
- **No measurement run.** Every number quoted is from a committed artifact or a recorded command; nothing was
  re-measured (§9.2 says so per row).
- **`docs/ops/multi-graph-migration-runbook.md` not read**, with the reason stated in A1.1 row 38.
- **The 141 out-of-scope index entries are classified by rule, not read individually.** That is the honest
  form and the reason is stated in A1.4 — but a reviewer who believes one of them *does* assert a
  storage-shape claim has a checkable list to point at, which is what the artifact is for.

## 11 — Documents read *after* I thought I was done (the honest completeness measure)

The brief asks this explicitly, so it is recorded rather than left to inference:
`docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md`, `docs/epics/2026-10-10-7879-postgres-record/06-cache-
projection.md` §0.5, `docs/architecture/BOUNDED-OPERATION-CONTRACT.md` (which is where `read_after`'s
refusal is argued — and which changed A4.4's cursor table), `docs/verify/2026-10-31-record-capstone.md`,
`docs/evidence/9025-journal-bound/`, and `docs/plans/2026-10-18-7951-delete-point-outbox.md` (whose `#9207`
**reversal** of the delete ordering contradicted the ordering A7.5 initially assumed — the single most
consequential late read in this lane).

**What I could still be wrong about, named rather than implied:** whether a guard against two journal files
per graph exists in tests (D-24 is stated as *"check in Stage B"* because I did not finish that search — my
`rg` for one returned nothing, which is not the same as proving its absence); and the exact current site
count for D-01 (two published numbers disagree and I did not re-measure). One item from the first draft is
now **closed rather than carried**: I had flagged that I might have missed an `ORDER BY` in
`cache_projection`'s `journal_event` select. I read the builder end to end — the statement carries no
`ORDER BY` and no cursor, and its header says *"`seq` orders it"* at `:2952`, so the ordering is the
consumer's. That changes A4.5's reasoning in one place and it is corrected there rather than here.
