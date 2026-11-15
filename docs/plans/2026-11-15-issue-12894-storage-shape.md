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
Every count in §A2 is **re-derivable from the command printed beside it** — the census blocks reproduce the
command verbatim and, where the output is long, its contiguous head. ⛔ **`WORKTREE-REPORT.md` is NOT
committed** (`.gitignore:158-159`), so no stored log can be retrieved and none is cited as if it could be:
the commands in this document are the artifact, and a reader re-runs them. Where a count below has no
command beside it, that is a defect in this document, not a pointer to a log.

**How to read this document.** §A1 is the completeness artifact and is the one to grade. §A2–§A5 are the
map. §6 resolves the recorded tension the issue names, in words. §A7 is the ideal, and §A7.4 is the
section that says what the map **contradicts** — including the hypothesis's own crux. §9 states what this
lane did not do.

---

## A1 — The document inventory (the completeness artifact)

### A1.0 What the population is, and how it was closed

**The population, and the rule that defines it — stated first, because every number below depends on it.**
Every row of `docs/00_index.md` contributes **one member**: the first backticked path in that row's path
column that names a **`.md` file which exists in the tree**. A row whose path column names no such file
contributes no member, and exactly **two** rows are like that: `docs/00_index.md:84` names a generated file that
is not in the tree (`docs/product/sdk-rename-table.md`), and `:167` names only a directory. **182 − 2 = 180.**
A row whose cell *starts* with a non-markdown artifact still contributes — its later `.md` token is the member
(`:59` → `CONTRIBUTING.md`, `:85` → `docs/product/sdk-surface-declaration.md`, and so on), which is why `:85`
and `:87` are the duplicate pair below. Stated that way the population is a function of two files and is
reproducible without trusting this document's arithmetic; **stated without the `.md` restriction it reproduces
179 / 154 / 102 / 50 instead, which is how an earlier revision of this table went wrong.**

| | count |
|---|---|
| index rows | **182** |
| …that name an existing file (one member each) | **180** |
| …of those, **distinct** documents (two are listed twice) | **178** |
| documents **read** for this map | **44** |
| …of those, with an index row of their own | **25** |
| …of those, **without one** (reachable only by cross-reference) | **19** |
| index documents **not read** | **153** |

The two documents listed twice are `docs/registry-graph-schema.md` (`:73`, `:74`) and
`docs/product/sdk-surface-declaration.md` (`:85`, `:87`). The read set is **44**: the **41** that occupy
`§A1.1`'s **40 `READ` rows** (41 numbered rows, one of them declared **NOT READ**; row 40 bundles three
evidence records; row 16 records an issue body and its plan comment, not a document — the path an earlier
revision printed under it does not exist) plus the **3** read after the first draft (`§12`). **25 + 19 = 44**
and **25 + 153 = 178**, so both partitions close.

⛔ **Two earlier revisions of this table were arithmetically broken, and how they broke is the point.** The
first said 41 / 22 / 19 / 159: it counted *rows* as documents (so the three-record row counted once, hiding
2 documents inside it), it counted the row declared **NOT READ** as read, and it left the three late reads
inside the "not read" population while naming them in `§A1.4`. The second said 180 / 155 and was reproducible
only under a *different* membership test (any backticked token anywhere in the cell) than the one it counted
rows with — two conventions in one table. **A population whose rule is unstated is not a population**, which
is why the rule now comes before the numbers.

⛔ **And that is the finding, stated as arithmetic rather than as an impression: 19 of the 44 documents
this map needed — 43 % of the read set — have no index row of their own.** Named exactly, they are: the three
record migrations (`20261012000001`, `20261012000005`, `20261114000001`); the two governing documents
(`STORAGE-ARCHITECTURE.md`, `event-catalog.md`); all five journal-mutation plans
(`#2194`/`#2295`/`#2488`/`#3689`/`#2814`); the unjournalled-mutation class plan; the `#7951` atomic-write plan;
`03-schema.md` and `06-cache-projection.md` from the record epic; `docs/scoping-2313-per-graph-backups.md`;
`STORAGE-ARCHITECTURE-CHANGELOG.md`; `docs/00_index.md` **itself**; the `9025-journal-bound` evidence record;
and `docs/verify/2026-10-31-record-capstone.md`. (An earlier revision named `per-org-bytes` here — there is no
such document; the per-org-bytes work is a migration and a module, and `§A1.1` row 16 records its issue body.) **Reading was driven by cross-reference and by what the issue named, not by the
register** — which is exactly what makes D-22 a defect rather than a preference.

**So the scope proof cannot be "the index, closed".** Two claims are made instead, and each is checkable:

1. **What was read is listed, per document, with the section or line taken** — `§A1.1`, 41 rows. Any reader
   can take a row and go to the line.
2. **What was *not* read is listed, per document, with the reason it was not needed** — `§A1.4`, 48 documents for
   the in-scope-and-unread set, plus 105 by exclusion rule. **Nothing is unclassified and nothing is
   unlisted.** A reason can be argued with; a silence cannot.

**⚠️ A correction to this document's own first draft, kept rather than quietly replaced:** it claimed
*"182 = 41 in scope + 141 out of scope"* with three class lists that **did not add up** — the printed ranges
totalled 145 not 128, `C3`'s list held 10 not 6, the union was 146 not 141, **36 entries fell in no class**,
`C2` sat entirely inside `C1` so the two could not be summed, and the "41" were a different population that
included documents with no index row. **The partition was restated until it was true**, and the restatement is
below: the key is now the **path**, never a position number in a table (position numbers drift the moment a
row is inserted — which is what had happened).

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

### A1.1 In scope — 41 numbered rows: 40 `READ`, 1 declared `NOT READ`

| # | Document | Status | What was taken from it (section / line) |
|---|---|---|---|
| 1 | `docs/durability-posture.md` (936 lines) | **READ (full)** | §The rule `:41`; `:48` *"It is never the durability mechanism"*; `:59` *":GraphEvent is the 30-day delivery/audit stream, not a backup"*; §Authoritative node classes `:73`; §The one carried high-water mark `:264` (the whole `:GraphEventMeta` block `:264-470`: the `last_seq` allocator, `MAX_SEQ = 2**53-1`, `first_seq` never carried, `reestablish_watermark`, the "**A rebuild still requires a QUIESCED graph**" residual); §Per-deployment posture `:471` incl. the five deployment rows `:500-560` and the record's own row + §The record's posture `:583`; §Why this document exists `:660` (the gate's claim shapes, `#9867`'s blind wordings); §Open items `:710` |
| 2 | `docs/architecture/STORAGE-ARCHITECTURE.md` (2099) | **READ (§1, §2, §2.0, §2.1, §2.2, §3, §5, §5.1, §5.2, §6, §6.1, §12.1c, §14.1, §17.1–§17.4.6)** | `:104` §1 the problem; `:116` §2.0 the owner ruling **and its 2026-11-07 SPLIT + the `OVERRIDES:` line**; `:173` §2.1 what FalkorDB charges for; `:217` §3 the split; `:236` *"READ THE AUTHORITY COLUMN AS AUTHORITY, NOT AS LOCATION"*; `:243` the per-write truth test; `:275` *"journal completeness is a PRECONDITION"*; `:285` **§ "The journal's scope: REBUILD, not durability"**; `:316` the invariant `derived tables = replay(the journal) NOT recompute(the sources)`; `:342` *"ONE ATOMIC WRITE … may be an outbox … **This is the whole answer**"* and *"**The current JSONL cannot do (1)**"*; `:361` *"They are also recorded in the journal, which is truth"*; `:427` §5.2 measured growth (and the per-capture total `#9025`); `:450` §6 the split does not fix volume; `:1115` §12.1c (vector index decision — **this, not SEARCH §12.1c, is where §12.1c lives**; see A1.3); `:1546` §14.1 decisions; `:1664` §17.1; `:1700` the 387-site measurement; `:1702` §17.2; `:1822` §17.4.2; `:2090` §17.5 |
| 3 | `docs/architecture/SEARCH-ARCHITECTURE.md` (566) | **READ (§3, §4, §7)** | `:157` §3 "What runs where" (writes/projections/backup-restore stay in FalkorDB; the graph stays a *derived copy*); `:205` §4 "Authority (unchanged, cited not restated)"; `:361` §7 "The window" (why a complete cache is 193 % of the storage target and the window is not yet armed) |
| 4 | `docs/ONTOLOGY.md` (1561) | **READ (§2, §4.2, §4.7, §5 headings)** | `:61` §2 the Four-Ontology Model table — the layer vocabulary used verbatim below; `:70` the Procedural row (`Object.status` is a **write-through cache** and *"the journal/event stream is the reconstruction source"*); `:75-97` the state-centric model (*"the Episodic layer's events are the truth; status is a fold cache over the events — never the truth itself"*); `:638` §4.7 temporal model; §5's kind vocabularies (read for the `decision`/`statement` split only) |
| 5 | `docs/event-catalog.md` (330) | **READ (full)** | `:89-92` *"The JSONL rebuild journal that `rebuild_all` replays is a second, differently-shaped store"*; `:100-101` the `__TornTailSealed__` seal shifts no record index; `:263-266` **no down-version guarantee**; `:286` `seq` = *"Per-graph monotonic, atomic in-graph counter (`GraphEventMeta.last_seq`)"* — i.e. the catalogue documents the **`:GraphEvent`** counter, not `journal_event.seq`; `:305-307` the 30-day/500k retention |
| 6 | `docs/00_index.md` (247) | **READ (full)** | The population proof above; and the absence finding (`STORAGE-ARCHITECTURE` and `event-catalog` unregistered) |
| 7 | `docs/retention-and-deletion.md` (606) | **READ** | `:30-34` *"This is the canonical document … link here and do not restate the number"*; `:41-42` the promise; `:73-75` *"Restore window ≠ retention window"*; `:92-97` no per-entity purge inside a live graph |
| 8 | `docs/data-safety.md` (53) | **READ (full)** | `:15` retention is delegated here; `:36` graph content is **infrastructure-level encryption only**; `:37` control-plane audit events go Postgres-first with a local JSONL fallback — a **different** dual-write seam from the journal's |
| 9 | `docs/ops/registry-backup-dr.md` (1233) | **READ (§1–§4, the retention and tombstone sections)** | `:17` retention windows delegated; `:76` *"Cadence is HOURLY, not daily"*; `:380` the per-graph artifact path `backups/{org_id}/{graph}/{ts}_{rnd}/dump.enc` |
| 10 | `docs/scoping-2313-per-graph-backups.md` (234) | **READ (full)** | `:17` the pre-fix shape (one graph per team — custom graphs unbacked); `:65` the pool-competition bug (*"Pool-level retention competition is latent today"*); `:226`/`:229` the owner's Q1/Q4 rulings |
| 11 | `docs/capacity.md` | **READ (write-concurrency and storage-footprint rows)** | the per-deployment write-concurrency row (embedded single-writer; Docker 5 writer processes) — the basis of A3's rows |
| 12 | `docs/ops/postgres-storage-measurement.md` | **READ** | `postgres_storage_footprint()` / `analytics_events_rows_by_org()`; the TOAST/double-count traps; the explicit **retention finding**: `analytics_events` has no prune path |
| 13 | `docs/architecture/SUBSTRATE-REBUILD-MAP.md` (527) | **READ** | `:86` **E6 `#7879` OPEN and unowned**; `:94-96` *"E2 is CLOSED with an interpreter-level invariant still stated as its goal … a claim about the cache, and the 2026-10-08 hybrid ruling made the record authoritative"*; `:244-246` the replay keys; `:339` *"#7879 (the record) ────► everything: the cache is derived, the record is truth"* |
| 14 | `docs/architecture/CONSUMPTION-MODEL.md` | **READ (the census + dimension table)** | the storage meter is blind to the HNSW index; no `max_storage_bytes` gate on `main`; the two-substrates-of-one-bytes-unit finding |
| 15 | `docs/ops/cost-dimensions.md` + `tortoise/cost_dimensions.py` | **READ (registry rows for postgres_storage / journal bytes)** | `cost_dimensions.py:492` `journal_event_bytes_for_rows`; the declared dimension registry |
| 16 | `#12762`'s **issue body + plan comment** (no document: `docs/ops/postgres-per-org-bytes.md` was printed here by an earlier revision and **does not exist**) | **READ (issue body + plan comment)** | attribution/total = 83.9 % on 404 rows / 3 orgs, 16.1 % un-attributable; `pg_column_size` does **not** report the 18-byte pointer; the double-count trap |
| 17 | `docs/architecture/PI-CAPTURE-ARCHITECTURE.md` | **READ (the cursor + store-sync backstop sections)** | the **per-file cursor, advance-on-2xx-only** pattern — the closest in-tree comparable to an ordinal-bearing consumer, and the reason A4 distinguishes *cursor* from *ordinal* |
| 18 | `docs/architecture/BOUNDED-OPERATION-CONTRACT.md` | **READ (`event_store.read_after` row)** | `read_after` **REFUSES** out-of-domain because *"its cursor never signals end-of-collection"* — load-bearing for A4 |
| 19 | `docs/adr/ADR-009-embedder-selection.md` | **READ (the model/revision pinning decision)** | the model-id + revision pinning that makes `embedding` non-recomputable (R1) |
| 20 | `docs/plans/2026-10-14-7951-atomic-write-outbox.md` (259) | **READ (full)** | increment 1; the ordering contract `:§3` with its **three named pre-append writes**; the 5-row `Wiring Check`; the 10 declared residuals — in particular **residual 7** (the `:GraphEvent` store is written **before** the JSONL append) and **residual 2** (the record projection on the live path + `last_applied_seq` "STILL OPEN"). ⚠️ **The line range this plan gives for residual 7 — `sdk.py:5653-5683` vs `:5814` — has drifted and no longer names those sites**; at this head `:5653-5683` is the `status`-key-rendering refusal and `:5814` is a `# ── #9964` comment. **The current lines are `sdk.py:13832-13833` (`next_seq`/`append_event`) then `:14008` (`log.append`)** — which is what D-04 cites. Publishing a document's own stale range without re-resolving it is the exact failure this map's provenance claim (§9) exists to prevent |
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
| I4 | `#4240` | CLOSED/COMPLETED (PR #5632) | **Rejected promoting `EntityLinked` into `_GRAPH_EVENT_TYPES`**; one file per graph (`<base>/<graph>/events.jsonl`); *"the JSONL is **never** the durability mechanism"*; and **the ordering supersession** — a 2026-10-15 comment (daniel-ospina, `IC_kwDOToroq88AAAABcKuJBw`) records that *"the journal append IS the commit point on `create_point`: a failed append means the `CREATE` is skipped, so nothing persists"*, that this **supersedes F1's fail-soft outcome** (*"The premise the outcome half rested on is gone"*), and that departing from it *"requires an exception in §17.1 … the `OVERRIDES:` route — not a test flip"*. **This comment, not any document, is where Q3 lives** (§6) |
| I5 | `#4492` | CLOSED/COMPLETED (PR #8744) | The unit ruling (**bytes, not nodes**); `analytics_events` has **no** retention path; retention itself is out of that issue's scope |
| I6 | `#5004` | CLOSED/COMPLETED (PR #5071) | The journal **stripped** `embedding`, so `derived = replay(journal)` was false for the largest cost lever; *"if the journal doesn't carry it, it isn't derived — it is primary"*; R1 (embedding STORES, never regenerated) |
| I7 | `#5612` | CLOSED/COMPLETED — **but item 2 (the bound) was never delivered** | The growth measurement (9,176 B creating record, 8,414 B vector); **the only `seq` in the issue corpus is `max(:GraphEvent.seq)`**; **P1 recommends moving the watermark *read* to the record — a read relocation, not an allocation decision**; the retention ruling (*"do not bound until checkpoint/truncate exists"*, *"the owner's"*) |
| I8 | `#8526` | **PR, MERGED** (not an issue) | Self-host journal arming: `derived = replay(journal)` was **false on every shipped self-hosted deployment** — a **wiring** gap; *"Arming the journal buys REBUILDABILITY, not DURABILITY"*; **"Ship Postgres to self-host now" — parked by the owner** |
| I9 | `#9025` | **OPEN** | The journal's growth is still unbounded; the live census: volume **974 MB, 700 MB used (78 %)**, of which the events directory is **652 MB (93 % of it)**, ~4 days of headroom at ~50 MB/day; recommends *keep everything* + the non-destructive segmentation plumbing (`journal_segments.py`) |
| I10 | `#9078` | CLOSED/COMPLETED | The record had **zero production callers**; landed **three journal doors → one seam** (`record_writer.write_live_record`), **after** the append and **before** the fold: **journal → record → cache**. And the deferral that becomes `#9190`: *"The live path has no ordinal … writing it here would fabricate a position a later replay would collide with or duplicate."* |
| I11 | `#9190` | CLOSED/COMPLETED (PR #9216, hardened #9277) | **The ordinal's owner and its reasons.** `MAX(seq)+1` **rejected**; a **sidecar counter inside the append's existing critical section**; the count is *"defined by the replay's own reader"*; and *"`event_store.next_seq` / `:GraphEventMeta` — a **different** seq over a different population"* |
| I12 | `#10017` | CLOSED/**NOT_PLANNED** (a no-op locally; reopen condition = hosted armed) | **R2**: a journal entry whose replay materialises **no graph node** (`TurnsRecorded`); the §3 falsification — *"a journal rebuild today produces a graph the running deployment does not have"*; and *"`:GraphEvent` is **not** projected from the record — it is written by the **journal** path"* |
| I13 | `#11821` | CLOSED/COMPLETED (**P0 only**) | The vector width: 9,176 → 5,423 B; the `:GraphEvent` mirror at **32,032 B/session** — its own words: *"ours is read for `max(:GraphEvent.seq)` — a **watermark**, not provenance — so it rents RAM for a counter"*; P1 → `#12458`, P2 → `#12459` |
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

### A1.4 Not read — 153 index documents, by declared class, and 48 named individually

`docs/00_index.md` is **247 lines carrying 182 rows**, of which **180 name an existing file** and **178 name
something distinct** (`§A1.0` states the membership rule). Of those 178, **25 were read** and **153 were
not**. The 153 are accounted for as **105 by an exclusion rule** and **48 named individually with a reason**
— so there is no unclassified remainder, and the two claims are separate: a rule is checkable in bulk, and a
named reason is checkable one at a time.

**By rule (105).** Each class is a **path-prefix rule**, evaluated over the 153 unread documents, so any reader
can reproduce it without trusting a count:

| Class | Rule (path prefixes) | Count |
|---|---|---|
| **C1 — another subsystem, asserts nothing about the storage shape** | `docs/quickstart-*`, `docs/beta-feedback`, `docs/infra-runbook`, `docs/ops/ci-*`, `docs/ops/batch-*`, `docs/ops/container-lifetime`, `docs/ops/BATCH-REVIEW`, `docs/ops/abuse-flag`, `docs/ops/ISSUE-WORKING`, `docs/blog-authoring`, `docs/auth-architecture`, `docs/authorization-architecture`, `docs/engineering/auth/`, `docs/oauth-mcp`, `docs/adr/ADR-010`, `docs/adr/ADR-012`, `product/competition`, `docs/product/`, `docs/ax/`, `docs/scoping/`, `docs/research/2026-08*`, `docs/research/2026-09-0*`, `docs/research/2026-09-1*`, `docs/research/2026-09-25-4503`, `docs/research/2026-10-15-2427`, `docs/research/memory-attribution`, `docs/research/2026-11-08-1779`, `docs/ci/`, `docs/ci-timing`, `tools/ci_check_repair`, `docs/plans/2026-09-10-2789`, `docs/plans/2026-09-22-3525`, `docs/plans/2026-10-11-2081`, `docs/plans/2026-10-18-8098`, `docs/plans/2026-10-16-8051`, `docs/plans/2026-10-22-1402`, `docs/epics/2026-11-07-2281`, `docs/runbook/4220`, `docs/runbook/3514`, `docs/runbook/b7-*`, `docs/runbook/3806`, `docs/runbook/1987`, `benchmarks/`, `docs/benchmarks/comparison*`, `docs/benchmarks/1144*`, `docs/adr/ADR-009`, `docs/adr/ADR-011`, `docs/experiments/`, `docs/scoping-2600`, `docs/evidence/3589`, `docs/evidence/1789`, `docs/evidence/9664`, `docs/evidence/3770`, `docs/evidence/10137` | **103** |
| **C2 — process, or observability of the machinery** | `docs/ops/batch-integration`, `tools/run_affected`, `tools/batch_review`, `docs/infra/tmpdir-sweep`, `docs/scoping/2026-09-18-4098`, `docs/scoping/2026-09-20-4408`, `docs/runbook/4408`, `docs/scoping/2026-09-01-2090`, `docs/scoping/2026-09-02-2127` — **7 of the 9 prefixes match an entry, and 5 of those 7 already carry a C1 prefix** (`docs/ops/batch-integration` and the four `docs/scoping/…`), while `tools/run_affected` and `tools/batch_review` match no entry at all; the class is therefore **2**: `docs/infra/tmpdir-sweep.md`, `docs/runbook/4408-branch-reaper.md`) | **2** |
| **C3 — reads a storage surface, asserts nothing about its shape** | none unread: every such entry was **read** instead (see `§A1.1` rows for `#4503`, `#11083`, `#11469`, `#8607`, `#1779`, `#1402`). **The class is empty by construction here, and saying so is the point** — this was the class the first draft used to *exclude* documents it had not opened. | **0** |

**103 + 2 + 0 = 105. 105 + 48 = 153.** ✅

**Named individually (48) — in-scope by path, not read, with the reason.** (Two `#7951` increment plans —
`2026-10-17-7951-update-point-outbox.md` and `2026-10-18-7951-delete-point-outbox.md` — appeared in this table
in an earlier revision and are **read**: they are `§A1.1` rows 21 and 22. Nothing in the 48 is read.) These are the entries whose
prefix does not match an exclusion rule, so each needs its own stated reason. (Three further documents were
in this list in an earlier revision and are **read** now — `docs/evidence/10000-turns-bytes/README.md`,
`docs/evidence/9558-record-write-cost/README.md`, `docs/evidence/7950-capstone/README.md`; they are counted
in `§A1.0`'s 44 and the reason each was read late is in `§12`.)

| Not read | Why not |
|---|---|
| `docs/architecture/MEMORY-SUBSTRATE-PROBLEM-MAP.md`, `MEMORY-SUBSTRATE-FINDINGS-LEDGER.md` | Declared status/durability halves of `#2820`/`#2852`; their durability claims delegate to `durability-posture.md`, which was read. **The findings ledger is the one I would read next** — it is the corrections half and may hold an item this map repeats. |
| `docs/epics/2026-10-10-7879-postgres-record/03-schema.md`, `06-cache-projection.md` | **Read** (they are in `§A1.1`; listed here only because they are absent from the index). |
| `docs/evidence/7949-capstone/*` (6 files), `docs/epics/2026-11-04-7949-extractor-v4-capstone/` | The extractor-v4 capstone. Storage appears only as a cost basis; the storage conclusions live in `§17` of the architecture doc, which was read. |
| `docs/evidence/10976-licence`, `7846-indexed-graph-restore`, `7983-cost-magnitudes`, `8503`, `8607-w1b`, `11083`, `11086`, `11739-*`, `4623-*`, `2026-10-21-shared-coordination-memory` | **Partially read via `§A1.2`'s issue census** and each is a *measurement of a different question* (eviction licence, RESTORE wedge, cost magnitudes, operator edges, legacy ids, pack census, write-rate gates, quote population, cohort cap, fleet coordination). `11821-journal-encoding` is **not** in this list: it is one of the three evidence records **read** in `§A1.1` row 40, which is where its 9,176 → 5,423 B figures come from — an earlier revision of this row wrongly said it was not opened, and wrongly attributed it to D-09 (D-09 is the missing journal bound; it names `#9025` and `#5612`). |
| `docs/ops/multi-graph-migration-runbook.md`, `CONTRIBUTING.md` | Neither asserts anything about the journal/record shape. The runbook moves a **graph key** between stores and its storage claims delegate to `durability-posture.md` (the full reason is `§A1.1` row 38); `CONTRIBUTING.md` carries the finding-provenance gate, which is process. |
| `docs/plans/2026-10-09-5362-retroactive-correction`, `2026-10-10-3644-source-version-window`, `2026-10-11-1990-diff-boundary-invariant`, `2026-10-16-8056-class-plural-enumeration`, `2026-10-18-8882-phase0-1-census`, `2026-11-04-5087-epic-decomposition`, `2026-11-09-11656-endpoint-kind-binding` | Each is a **per-door or per-mechanism** plan: back-dating, `:Source` version windows, the DIFF cursor boundary, temporal enumeration, graph census, the extractor epic, endpoint-kind binding. They change *what* is written, not the storage shape that carries it. `3644` and `1990` are the two I would read next, because `:Source` versioning and the cursor boundary are the two payload-level invariants `§A7` touches. |
| `docs/architecture/ENTITY-VERDICT-BANDS`, `S2-DECISION-SEAM`, `CURATION-11469`, `INGEST_CONTRACT`, `client-server-split`, `docs/epics/2026-11-04-5090-vector-layer/TEST-DESIGN-surface-map`, `docs/registry-graph-schema` | Decision/contract surfaces above the storage layer. `ENTITY-VERDICT-BANDS` is the one that matters most to `§A6` (it is the policy `_POINT_DENY` implements, D-17) and **I did not open it**; that is a stated gap, not a satisfied one. |
| `docs/runbook/2578-*`, `2655-*`, `2684-*`, `2886-*`, `2976-*`, `4107-*`, `4210-*` | Temporal/retrieval/preference measurement runbooks. Reading surfaces only. |
| `docs/ops/669-post-flip-verification`, `cost-allocation`, `extraction-cost-metrics`, `docs/infra/pytest-timeout-guard`, `tools/finding_provenance.py`, `tortoise/__all__.py`, `docs/research/2026-10-19-8709-member-own-key-surface`, `docs/research/2026-11-09-11656-kindpair-enforcement` | Verification procedure, cost *policy*, test-infrastructure, and two non-storage research docs. |

⛔ **The honest summary of this section:** 25 of 178 index documents were read (14 %), and **the reason that
is not a scandal is only that the index does not contain the governing documents** — 19 of the 44 documents
that were read are invisible to it. Both halves of that sentence are defects and neither is a defence of the
other.

---

## A2 — The code census

### A2.1 The JSONL journal: three writers, one append, one seam

```console
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

```console
$ rg -n "log\.read_all\(|read_all\(salvage=True\)|read_for_replay" tortoise/     # 39 lines
tortoise/m0.py:38:    points = fold(log.read_all())
tortoise/m0.py:44:    print(f"events     : {len(log.read_all())}  → {args.log}")
tortoise/log.py:841:# among the records a REPLAY would read — `read_all(salvage=True)`, the ONE
… (36 more; `rg` walks the tree in parallel, so the FILE order differs between runs — the COUNT does not)
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

```console
$ rg -n "INSERT INTO public\.journal_event|FROM public\.journal_event|journal_event_bytes_for_rows|_journal_row" tortoise/     # 11 lines
tortoise/cost_dimensions.py:492:            "journal_event_bytes_for_rows",
tortoise/cache_projection.py:2909:#:   the record (`mining.py`, #5475). `_journal_row` strips the envelope keys
tortoise/cache_projection.py:2988:    "FROM public.journal_event "
tortoise/postgres_bytes.py:118:ROWS_RPC = "journal_event_bytes_for_rows"
tortoise/postgres_bytes.py:215:    """Parse a ``journal_event_bytes_for_rows()`` payload. RAISES on malformed."""
tortoise/record_projection.py:496:    # as a NODE prop: this set is not only a props set — `_journal_row` filters
tortoise/record_projection.py:5995:def _journal_row(record: Mapping[str, Any], seq: int, org_id: str,
tortoise/record_projection.py:6005:        "INSERT INTO public.journal_event "
tortoise/record_projection.py:6499:_JOURNAL_INSERT_PREFIX = "INSERT INTO public.journal_event"
tortoise/record_projection.py:6513:    THE POSITION WATERMARK (#9187). `_journal_row` keys the consumed position on
tortoise/record_projection.py:6545:            plan.append(_journal_row(record, seq, org_id, graph_id))
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

```console
$ rg -n "^def (next_seq|capture_watermark|reestablish_watermark|append_event|read_after|purge_expired|purge_overflow|ensure_event_schema|degraded|degraded_graphs|clear_degraded|record_degraded|_refresh_first_seq)" tortoise/event_store.py     # 13 lines
222:def record_degraded(proj, stage: str, event_type: str, event_id: str | None,
270:def degraded(proj) -> dict[str, Any] | None:
284:def degraded_graphs() -> list[dict[str, Any]]:
318:def clear_degraded(graph_name: str | None = None) -> None:
388:def ensure_event_schema(proj) -> None:
427:def next_seq(proj) -> int:
524:def capture_watermark(proj) -> dict | None:
571:def reestablish_watermark(proj, carried_last_seq: int | None, *,
777:def append_event(proj, seq: int, type_: str, payload: dict, event_id: str,
922:def read_after(proj, after_seq: int, types: list[str] | None = None,
1014:def purge_expired(proj, retention_days: int = 30) -> int:
1038:def purge_overflow(proj, max_events: int) -> int:
1062:def _refresh_first_seq(proj) -> None:
```

```console
$ rg -n "next_seq\(|append_event\(|read_after\(|capture_watermark\(|reestablish_watermark\(" tortoise/*.py tortoise/projection/*.py | grep -v "^tortoise/event_store.py"     # 15 lines
tortoise/projection/__init__.py:2133:    entry = capture_watermark(proj)
tortoise/projection/__init__.py:4978:    lane is instead handed to `reestablish_watermark(..., restore_reason=...)`,
tortoise/projection/__init__.py:13495:        carried_watermark = capture_watermark(self)
tortoise/projection/__init__.py:13695:            restored = reestablish_watermark(self, carried_last_seq)
tortoise/projection/__init__.py:18902:            restored = reestablish_watermark(
tortoise/log.py:24:    events = log.read_after()          # all events so far
tortoise/log.py:28:    new_events = log.read_after(cursor)
tortoise/log.py:2555:    def read_after(self, cursor: str | None = None) -> list[dict]:
tortoise/log.py:2579:        ``read_after(token)`` will return all future events).
tortoise/change_feed.py:73:The fold sees ONE page — the rows ``read_after(..., limit=limit)`` returned —
tortoise/webhook_delivery.py:1300:            events = read_after(proj, int(sub.get("cursor") or 0), types=types,
tortoise/why_snapshot.py:526:        events = read_after(proj, int(min_cursor), limit=max(1, int(limit)))
tortoise/sdk.py:13587:        evs = read_after(proj, after_seq, types=types, limit=limit)
tortoise/sdk.py:13832:                seq = next_seq(proj)
tortoise/sdk.py:13833:                append_event(proj, seq, type_, graph_payload, self.ulid())
```

`_GRAPH_EVENT_TYPES` has **11 members** (`sdk.py:4671`), and the set is **defined by the store boundary**, not
by a list (`sdk.py:4658-4660`, `#12458` gate 1's verified quote). `GraphEventMeta` appears at **61** sites in
`tortoise/` (`rg -n 'GraphEventMeta' tortoise/ | wc -l`; **50** of them spell it `` `:GraphEventMeta` ``), of
which the allocator is `next_seq`'s `MERGE` at `event_store.py:491` and the carries are
`capture_watermark:557` / `reestablish_watermark:657`,`:738` / `hosted_backup.py:842`,`:914` /
`webhook_delivery.py:1052` / `sdk.py:13582` (`first_seq` for the 410).

### A2.5 The reconciliation

| population | count | source |
|---|---|---|
| journal append call sites | **3** | §A2.1 (the command is reproduced there) |
| journal replay engines | **5** (4 journal + 1 record) | §A2.2 table |
| journal readers, production parse sites | **12** + 4 bypass | §A2.2 table, §A2.6 |
| `journal_event` writers | **1** | §A2.3 |
| `journal_event` folding readers | **1** | §A2.3 |
| `journal_event` byte-meter readers | **2** (py) + 1 RPC + 1 migration fn | §A2.3 |
| `_GRAPH_EVENT_TYPES` members | **11** | `sdk.py:4671` |
| `event_store` allocator call sites | **1** (`sdk.py:13832`) | §A2.4 (command reproduced there) |
| `event_store` feed read sites | **3** (`sdk.py:13587`, `webhook_delivery.py:1300`, `why_snapshot.py:526`) | same block |
| `GraphEventMeta` mentions in `tortoise/` | **61** (50 spell `` `:GraphEventMeta` ``) | `rg -n 'GraphEventMeta' tortoise/ \| wc -l`, §A2.4 |

**The number that reconciles the shape: the journal has 1 write primitive and 5 replay engines; the record has
1 write path and 1 folding reader; `:GraphEvent` has 2 stores' worth of bookkeeping and 1 allocator that is the
wrong one.** The asymmetry is the finding: **the rebuild source has the most readers and the fewest writers;
the delivery feed has the most machinery and no replay.**

⛔ **Two corrections to this census, from review, both of which make the population larger:**

- **A reader was missing.** `tortoise/journal_segments.py:914` `read_all_segments` (the `def`; `:913` is blank) → `:938`
  `records.extend(read_for_replay(log))` is a **replay-order reader that spans segments**, with its own
  salvage contract (*"⛔ **STRICT BY DEFAULT.** `read_for_replay` salvages … a skipped line is a REFUSAL
  here"*, `:920-928`). It was inside the 39-line `rg` that was run. **It is not cosmetic:** it is the one
  shipped reader whose numbering is **not** a single file's 1-based index — which is exactly the shape D-24
  and A7.6's first falsifier assume cannot exist. So "one graph = one file = one index" is already not
  literally true in the codebase.
- **The table did not add to its own total.** The consumer rows sum to **13**, and one of them —
  `sdk.py:1353` — is `InMemoryLog.read_all` (`return list(self._events)`), i.e. the *"different log"* this
  section's own preamble excludes. Corrected: **12**, not 11.

### A2.6 What an *armed* capture actually leaves in the graph — measured, and not what §A7.1 assumes

`docs/evidence/10000-turns-bytes/README.md` (read late, because review found it unclassified) measures a
record-armed capture and reports something the first draft of this document did not know:

| state | bytes / session | graph contents |
|---|---|---|
| before the turns moved | **322,063** | 1,000 `:Point`, 1,000 `CONTAINS` |
| after, **record NOT armed** | **321,175** | unchanged — the *capability fallback*, within 0.3 % |
| after, **record armed** | **32,032** | **`:GraphEvent` 1,008, `:Session` 26, `:Source` 1, `:Point` 1** — and `sdk._capture_turn_ids` returns `[]` |

⛔ **So on the armed lane the graph is *not* a point cache at all: it holds one `:GraphEvent` carrier per turn
whose payload is `{id, kind, content_hash}` — *"the text is not in it"*** — and the transcript lives in
`public.point` (1,007 rows, 1,518,584 bytes of `content`, read back by `record_read.session_text`). Two
consequences this map must carry:

1. **A7's "the graph is a derived cache of the record" is, on the armed lane, "the graph is a per-turn
   `:GraphEvent` carrier that duplicates a `journal_event` row"** — a third copy of the same decision, not a
   cache of it. That is the store D-19 retires the *premise* for, and it is why D-19's 32,032 B/session figure
   is the *surviving* residue rather than the graph's cost (§A2.6 already says the figure is the mirror's).
2. **The document states its own limit, which must be quoted with the number:** the committed figure's
text-scan covers three properties (`n.c`, `n.content`, `n.text`); the `payload`/`props` half is pinned
separately by `tests/test_10000_turns_record_only.py` on a **2-turn** capture, not on this corpus — and
whether those carriers should leave the graph is explicitly *"a placement decision of their own"* belonging
to the record→cache seam (`#10070`/`#10017`), *"not claimed here"* (`:91-101`).

⚠️ **Instrument limit, quoted rather than assumed away:** `GRAPH.MEMORY USAGE` reads whole MB, reads **0** for
the armed corpus, and is **blind to the HNSW `vectorset` module** (`#5331`) — `used_memory` is the instrument,
and the figures are a per-session marginal at 25 sessions × 40 turns × 1,500 chars, not a per-node constant.

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
  see". The schema calls it *"`seq` is the idempotency key: applying a position twice is a no-op"* (`03-schema.md:86`,
  `record_projection.py:5999-6003`).
- **Per-graph**, and valid as such **only because one graph = one journal file** (`record_writer.py:2409`
  states the coupling: *"construction holds per JOURNAL FILE, while `journal_event`'s key is per"*).
- **Irreversible**: append-only by **trigger** (`20261012000001:96`), inserted `ON CONFLICT DO NOTHING`.

### A4.3 What `:GraphEventMeta` is for — and why it is *not* the same ordinal

`:GraphEventMeta{last_seq, first_seq}` is the allocator for **`:GraphEvent`**, the graph-resident
30-day delivery/audit stream. `next_seq` (`event_store.py:427`, `MERGE` at `:491`) mints it; `MAX_SEQ =
2**53 - 1` (`:381`) because FalkorDB compares numeric ranges in double precision and `read_after`'s
`WHERE e.seq > $after` **silently drops** rows above `2**53` (`durability-posture.md:264-470`; the QUIESCED precondition is at `:403`).

`#9190` refused it as the source, verbatim:

> `event_store.next_seq` / `:GraphEventMeta` — a **different** seq over a different population; using it
> would make the live ordinal and the replay ordinal diverge again.

**So there are four monotonic counters in the system**, and only one of them is `journal_event.seq`:

| counter | store | allocated by | population | journalled? |
|---|---|---|---|---|
| journal position → `journal_event.seq` | the JSONL file (+ sidecar) | `EventLog.append` | journal records | it **is** the journal |
| `:GraphEventMeta.last_seq` | FalkorDB | `event_store.next_seq` | `:GraphEvent` rows (11 types + `PointRevised` under the change-feed switch) | **no record describes it** — declared at `event_store.py:52-57` |
| `:EpMeta.ep_version` | FalkorDB | `SDK._advance_ep_version` | EP ordering epochs | **no** — declared `graph-metadata:ep-epoch` (`#9123`), carried by nothing, reset by a rebuild (`#5166`) |
| **`relation.id`** | **Postgres** | **`GENERATED ALWAYS AS IDENTITY`** (`20261012000003:167`) | every edge row in the record | **yes, by the row itself** |

The fourth is a **surrogate**: gaps are harmless because nothing orders by it and nothing replays it, which is
exactly why it is *not* a model for the ordinal — `journal_event.seq` is an **idempotency key** (A4.2), and
gaps in an idempotency key are gaps in what a replay can see. **The first draft said "three, and only one is
`journal_event.seq`" and omitted this one; a reader planning A7.5's step 4 from that table would not have
known the record already mints numbers.**

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
| **A transactional outbox table** | **NO** (and none is needed for `journal_event` — the table *is* the outbox, because it is written in the same transaction as the layer rows) | — |
| **A record-side ordinal allocator** | **NO** | — (proposed by §A7) |

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
| D-09 | **The journal has no bound of any kind** — no rotation, compaction, retention. The live volume is 974 MB with **700 MB used (78 %)** — the events directory alone is 652 MB — ~4 days of headroom at ~50 MB/day | `#9025` **OPEN**; `#5612` item 2 never delivered | **B** | Not because a bound is needed for atomicity, but because the journal is the very thing the ideal shape keeps for the no-record lanes. A capacity failure converts a rebuild source into an outage. **Owner decision; this lane does not choose one.** |
| D-10 | **The vector payload was widened to float64 decimal text** (P0 landed) and the **base64 step is blocked** | `#11821` P0 landed; `#12459` **blocked** — its `P1` gate points at `#12458`, which closed `NOT_PLANNED` | **O** | It changes bytes, not shape. But it is **blocked on a released dependency**, which is a process debt: `#11821`'s rule ("P1 merged and measured") can never be met, and `#12459` says so itself (`:777`). |
| D-11 | **A build rollback over a frame-bearing journal drops vectors silently and reports success** | `2026-11-13-12459:824` | **O** | A migration hazard for a format change, not a shape defect. Its remedy (readers ship before writers) is the same discipline A7.6 asks for. |
| D-12 | **Three doors gate on a shared `_journal_write_failures` counter delta**, which a concurrent writer can move | `#7951` residual 10; `#5612` | **B** | `create_point` was moved to the per-call outcome (`True`/`False`/`None`); the siblings were not. A commit point decided by a shared counter is a racy commit point. |
| D-13 | **`create_point` keeps three named pre-append writes** (`:EpMeta`, `:GraphEvent`, the `:Source` adopt-on-touch) | `2026-10-14-7951:§3` | **B** | They are the reason "the append is the commit point" is only *nearly* true. Each is disclosed; none is closed. |
| D-14 | **`apply-first` is a declared class of eight unjournalled writer lanes** | `STORAGE-ARCHITECTURE.md:1672-1698` | **O** | Declared, pinned per lane (`tests/test_9206_apply_first_lane.py` + the census). It is a *declaration of where writes live*, and the doc says so: *"a declaration names where the write lives; it does not make the write replayable."* |
| D-15 | **The JSONL is a PARTIAL WAL** — a delta cannot reproduce an entity whose creation was never journaled; a raw-Cypher or no-base-dir write journals nothing | `2026-09-22-unjournaled-mutation-class:1250` | **B** | §3 says *"journal completeness is a PRECONDITION"*. This is the precondition being false in named places. It is why "delete the file" is unsafe **today** and why A7.5 sequences the change. |
| D-16 | **Torn-tail tolerance is an allow-list of additive types** | `log.py` seal + `read_all(salvage=True)`; `#5917`; `#9254` | **O** | Correctly scoped: a `EntityMutated` op=delete dropped by a replay is **resurrection, not an exception** (`STORAGE-ARCHITECTURE.md:361`). Its existence is a *constraint on any new ordinal* — a torn tail must not shift a position. |
| D-17 | **`_NO_PROJECTION_FOLD`** declares **eight** types whose replay materialises no node | `projection/__init__.py:8029-8080`; `SessionNarrated` `#9555`, `TurnsRecorded` `#10017` R2 | **O** | A decision the dispatcher *states* (`STORAGE-ARCHITECTURE.md:361` calls it "a declared exception"). The undercount mattered to the argument: the first draft said "two", which framed it as a narrow special case, when in fact **six of the eight long predate the two it names** — `IngestStarted`, `BatchIdStamped`, `CalibrationRecorded`, `DedupeRecorded`, `DedupeRejected`, `EntityBindingRefused`, `SessionNarrated`, `TurnsRecorded`. So "declared no-op" is an **established pattern**, and *that* is what made `derived = replay(journal)` approximate long before `#10017` used it to falsify §3's promise. |
| D-18 | **`#10017` remains a no-op on a one-sided measurement** — closed as no-op locally, reopen condition = hosted armed, and the hosted arm has since reported `record.armed=true` | `#10017` closure + the later comment | **B** | The purge question is live again on a deployment nobody has measured. This lane does not reopen it (#9025/#10017 are explicitly out of scope) but the map must say the "no-op" reading is local-only. |
| D-19 | **The `:GraphEvent` mirror rents 32,032 B/session for a watermark** — and the premise is now stale | `#11821` body; removed by `#10017`'s carrier on the armed lane | **R** (the *premise*), **B** (the allocator) | Retire the **premise**, keep the store (owner-closed `NOT_PLANNED`), fix the **allocator** (D-07). Three distinct fates for one paragraph. |
| D-20 ⚑ | **`record_read.py` never reads `journal_event`** — the transcript is served from `public.session_narrative`/turn rows, so the record's *read* path does not depend on the journal table at all | `record_read.py:34` | **O** | A positive finding: it means the record's read surface is already journal-independent, which is a precondition A7 does not have to build. |
| D-21 ⚑ | **`journal_event` retention has no ruling anywhere.** `#3843`/`#3853` bind `analytics_events`; `#12762` says so explicitly | `#12762` body | **B** | The record table gets the journal's growth **in addition to** the journal, forever, with no decision on pruning it. `#9025`'s bound decision, whenever it lands, will be about the file only. |
| D-22 ⚑ | **The governing documents are not registered in `docs/00_index.md`**: `STORAGE-ARCHITECTURE.md` has no index row; `event-catalog.md` has **0** mentions; the `#7951` atomic-write plan has **0** | §A1.0 | **B** | A lane proving completeness from the index — as this mandate instructs — **cannot find the document that governs the subject**. This is the same class as the `#10114` lesson: a green check that answers a different question. |
| D-23 ⚑ | **`docs/00_index.md`'s `#5612` cell still presents the pre-`#11821` vector figures as the current cost** in its first clause, then corrects them ~300 characters later in the same cell (the cell is **one** 1,931-character markdown line, so a "lines later" distance was never measurable) | `docs/00_index.md:153` | **O** | Deferred to the doc's own errata discipline; noted so Stage D does not re-derive it as a new finding. |
| D-24 ⚑ | **The ordinal's validity rests on one invariant the code states in a comment rather than enforces: ONE journal file per graph, forever.** `record_writer.py:2408-2410`: *"\"One record one number by construction\" holds per JOURNAL FILE, while `journal_event`'s key is per `(org_id, graph_id, seq)`"* | `record_writer.py:2412`, `config.py:92` | **B** | ⛔ **This is NOT a new finding and the first draft was wrong to present it as one.** The paragraph continues (`:2412-2425`) to state the residual in full — *"a tenant with **TWO durable journals** … the second journal's rows are absorbed by `ON CONFLICT … DO NOTHING`"* — **and tracks it on `#9318` (OPEN)**, whose own text proposes journal identity in the key **"or a per-tenant ordinal allocator"**. So the fix A7.5 argues for is the second of the two fixes already named in the source. **What this map adds is only the reachable trigger**, because the issue is framed abstractly: the concrete way to get two journals is a **`TORTOISE_EVENT_LOG_BASE_DIR` change on a live deployment**, after which the same graph numbers from 1 while the record holds the old rows — silent, count-free, no degraded state. Nothing refuses it. |
| **D-25** | ⛔ **The ordinal is not merely unowned — on a live-size journal it is intermittently UNPROVABLE, and this is the single most important thing the first draft missed.** `EventLog.append` returns **`None`** rather than a position in two documented, **repeatable** windows: (a) a recount that outlasts a peer's 5 s `_FLOCK_TIMEOUT_S`, which stamps `_FLOCK_UNHEALTHY` for 30 s during which *"every append in that window returns `None`"* — *"the journal past roughly **50 MiB** can outlast a PEER's deadline … a **repeatable recount → degrade cycle**"* (`log.py:1503-1520`); and (b) a sidecar that cannot be written, where `_POSITION_CACHE_DEAD` means *"an append does NOT recount — it reports `None`"* (`log.py:1523-1530`) | `log.py:1503-1530`; `record_writer.py:2396-2407`; `record_projection.py:6402-6407`; `monitoring.py:1459-1466` | **B** | **A `None` position writes NO `journal_event` row** — `write_live_record(position=False)`, *"Such a record writes no position row … the row arrives on a later replay"* — so the record's ordinal is not just unowned, it is **missing for a population of live writes**, and D-03 is right that nothing (`last_applied_seq`) can detect the gap meanwhile. It is **counted, not silent** (`tortoise_record_position_unproven_total`), which is what keeps it out of P0 severity for the product — but the lane's own D-09 quotes the live journal at **974 MB, ~19× the 50 MiB threshold the code names**, on a box where a 20 MiB recount measures ~1.95 s. **Tracker: `#9252` (OPEN)** — *"a sidecar that cannot be written makes every append recount the whole journal under the lock"*, a `#9190` follow-up whose stated fix is *"a checkpointed counter with a tail reader"*. ⛔ **This is also the strongest available evidence FOR A7.5**, and the first draft never argues it: the code's own remedy for the file's position problem is a counter that does not recount. **Falsifier:** a peer append during a recount on a large journal must still yield a `journal_event.seq`. |
| **D-26** | **A journal record can exist for a write that never landed — the exact inverse of D-25, and it falsifies A7.5's "journal export commits, record fails — cannot happen".** *"a MERGE that aborts after its bounded retries leaves an orphan record … a journal-only rebuild then restores the attribution of the attempt that never wrote a row"* | `STORAGE-ARCHITECTURE.md:1672-1698` | **B** | **`#9390` (OPEN)** — *"replay attribution skew: an orphan `SessionRecorded` from an aborted `:Session` MERGE wins the coalesce fold"*. So the journal is **not** a strict prefix of the record's committed set in either direction: D-25 is a record write with no journal position, D-26 is a journal record with no record row. **Any design that makes one store the total order must say which of the two it resolves**, and A7.5's window table assumed away the second. |

**26 rows: 16 block, 8 orthogonal, 1 must be retired, 1 carrying two fates.** The tally is counted from the
class column and **the first draft's `24 / 13 / 8 / 3` did not match its own table** (the true count was 14
block and 2 retire then, and the follow-on sentence named three must-retire rows when only two existed —
`D-02`'s string coupling and `D-19`'s premise; `D-19` counts **once** as `R` here even though its allocator
half is also `B`, which is stated in its own row). **Two rows were added after review** (`D-25`, `D-26`), each
of which is the *inverse* of a case A7.5 assumed away, so the shape's difficulty went **up** on re-reading,
not down. The must-retire rows are each a *premise* or a *string coupling*, not a code path — which is itself
the finding: **almost nothing in this shape has to be deleted. What must change is who owns one number, and
how many doors there are.**

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
| **"The record owns the ordinal. `next_seq`-style allocation moves to the record so a position exists without a file."** | ⛔ **CONTRADICTED — by a recorded decision, and the reason is mechanical, not stylistic** | **`#9190` decided the opposite and rejected `MAX(seq)+1` by name** (`log.py:835-838`, which reads *"writer could only guess `MAX(seq)+1` — a number a later replay would either"*). The record does not allocate today; it *copies*. And the crux sentence contains a second claim that is **false as written**: a `next_seq`-style allocation does **not** give you *"a position without a file"* — it gives you a position **without a commit order**. PostgreSQL sequences are deliberately nontransactional: `nextval()` advances immediately, is not rolled back, and concurrent callers do not wait for earlier transactions to commit, so "a consumer that sees 11 may wait forever for 10, or incorrectly conclude that the log is complete" (`docs/…` — external, §9.2). ⚠️ **And the record schema does already carry a sequence** — the first draft recorded the opposite and was wrong: `public.relation.id` is *"bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY"* (`20261012000003_record_epistemic.sql:167`), whose separate sequence ACL is a documented hazard of its own (`20261025000001:205-212`: without the grant every `relation` INSERT raises `42501` and *"the whole edge-bearing record is lost"*). A bare sequence is the **worse** choice, not the better one — it would replace a number that is correct-by-construction with a number that is correct-by-luck, in a schema that already demonstrates the upkeep a sequence costs. |
| *(implied)* "the ordinal is the crux" | **NARROWED** | The crux is not the ordinal; it is **the arming dependency** — the record is only written where a journal record already exists to hand it (§A2.1, §A3 row 2). Fix that and the ordinal follows; fix the ordinal and the dependency remains. |

### A7.3 The two other recorded positions, and whether this recommendation touches them

| recorded position | touched? |
|---|---|
| `durability-posture.md:48` — the JSONL *"is never the durability mechanism"*; authority lives in a store-managed artifact | **Not reversed. Unchanged.** A7.1 keeps durability = the store's backup. The record-allocated ordinal does not make the file durable and does not make the record one — it makes the *record* the ordering authority, which is a fourth question (§6). |
| `STORAGE-ARCHITECTURE.md:316` — `derived = replay(journal) NOT recompute(sources)` | **Not reversed. Its subject changes, and that is the reopen A7 argues.** If the ordinal is carried in the record, the invariant becomes `derived = replay(record)` on the hosted lane — **the same invariant with a different source of the same records**. On the no-record lanes it is unchanged. §3:285 already scopes the journal to *rebuild*, not durability; A7 keeps it scoped to rebuild and changes only *which physical copy rebuild reads from*. |
| The graph-is-a-derived-copy decision (`SEARCH-ARCHITECTURE.md:157`, `STORAGE-ARCHITECTURE.md:116`) | **Untouched.** |

### A7.4 The "three stores" framing is an undercount, and that changes the target

The issue says "no app-written file as a **third** store". The live `create_point` path touches **six**
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
which is what makes **allocation order equal commit order** — the property a bare sequence cannot give.
⛔ **But the first draft's cost argument was a false equivalence, and it is the kind of false comfort that
sinks a design review.** It said the record allocator is *"a lateral move in concurrency, not a new
bottleneck"* because *"the journal append is already serialised per graph"* (`log.py:594-620`). Three things
are wrong with that: (a) **scope** — `flock` covers the seal read-modify-write plus **one `write(2)`** and is
released before the API, record and cache legs; the row lock is held until **COMMIT**, across the six
statements and four stores A7.4 enumerates. (b) **it is not even a guarantee today** — `_FLOCK_TIMEOUT_S`
is **5 s** and on timeout *"the append PROCEEDS on the single-write `O_APPEND` guarantee"* (`log.py:625-633`),
so the journal deliberately **abandons** per-graph serialisation under a wedged peer. (c) the honest
statement is therefore the opposite of reassuring: **the row lock buys the guarantee precisely because it
holds longer than the thing being compared to it.** One hot row per graph, held across a Postgres
transaction, is a real new bottleneck and must be quoted as one.

⛔ **And the seed is unspecified, which is the gap a reviewer will find first.** `record_cursor.seq` must
be initialised on an existing deployment holding 974 MB of journal, and the only available seed is
`MAX(journal_event.seq)` — **the operation `log.py:835-838` rejects by name**, and which is outright wrong
under D-24's two-journal case. A7.5 argues that replay must *read* the number rather than derive it and then
seeds the allocator by deriving it. **The rule must be stated:** a one-time locked
`SELECT max(seq) … FOR UPDATE` at cutover, **refused** if a second journal is detected (D-24 / `#9318`),
run only on a quiesced graph (the same precondition `durability-posture.md:403` already imposes on a
rebuild).

⚠️ **This is not a novel proposal, and presenting it as one would be the lane's worst failure.** `#7950`'s
capstone measured exactly this premise as **S1 — *"every write goes to the truth store first; the cache is
projected from it"* — and verdict `⛔ NOT-GREEN`**, with *"an **armed** journal-backed projection refuses
`apply` but lets `proj.g.query` write the cache directly (0 journal records)"*, **open on `#7951`**
(`docs/evidence/7950-capstone/README.md:38`). So A7's "ideal" is **the un-landed S1 of an existing epic**, and
what this map adds is the ordinal mechanism, not the goal. ⚠️ The same capstone's **S2 is also `NOT-GREEN`**
and for a reason that *is* this document's crux: `read_record_view` has **2** production call sites, the
reconcile verb is the only **0**, and *"**no schedule drives either verb yet**, so divergence is observed
only when a human runs the sweep"* (`:39`). **Divergence observation is unarmed — the same arming-dependency
class A7.2 names, measured rather than argued.**

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
| journal export commits, record fails | **today's live ordering cannot produce this** (the record is written *after* the append, `#9078`) | **cannot happen** by construction |
| ⛔ **the reverse: a journal record for a write that never landed** | **it DOES happen today** — an aborted `:Session` MERGE leaves an orphan `SessionRecorded` that wins the coalesce fold (`#9390`, OPEN; `STORAGE-ARCHITECTURE.md:1672-1698`) | **unchanged by this design** — the flip does not address it, and A7.5 must not claim the journal is a strict prefix |
| ⛔ **a live write whose position is UNPROVABLE** (`EventLog.append` → `None`) | **it DOES happen today** — a recount past a peer's 5 s deadline, or a sidecar that cannot be written (D-25, `#9252`, OPEN) | **fixed**: the counter is the position, so no recount is on the path |
| ⛔ **pre-transaction `:EpMeta` / `:Source` adopt-on-touch land, then the record rolls back** | **it happens today** — both are written *before* the append, in the graph (A7.4) | **still happens**: an advanced EP epoch and an adopted `canonicalUrl` for a mutation that never committed. Nothing reconciles it; `#5166` and `source_identity --backfill` are the only repairs. **This is the last paragraph of A7.4 and the first draft's table had no row for it.** |

⛔ **And the ordering must flip, which is the real work.** Today: journal → record → cache. After: record
(+ordinal) → cache, with the journal **exported** from the record. That flip is a *reversal of the ordering
`#9078` recorded* and of `#4240`'s *"the journal append IS the commit point"*. It is therefore a **second
reopen**, and it must be argued where those decisions live — the `#4240` comment already names the route
verbatim: *"an exception in §17.1 (a direct cache write on an uncommitted record) — the `OVERRIDES:` route —
not a test flip"*. ⚠️ Note what that sentence reveals: the route it names is for going **backwards** (letting
the cache lead). A7.5 needs the opposite — the **record** leading — which is not an `OVERRIDES:` of §17.1 at
all but a **rewrite of Q3's unrecorded rule**. That is why §6 says Q3 must have a recorded home first.

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

1. **Two journal files for one graph** must be **refused**, or the position must carry the journal's identity. The
   reachable trigger is not exotic — **changing `TORTOISE_EVENT_LOG_BASE_DIR` on a live deployment** gives the
   graph a fresh journal that numbers from 1 while the record keeps the old rows, and
   `ON CONFLICT DO NOTHING` swallows every new position silently (D-24). *Red today: no such guard exists and no
   test covers it.*
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
| **Q3 — commit ordering** | *What does a live write commit to first?* | **The journal append.** `#9078`: *"The journal append is the commit point (§17.1) and it is a file append"*; `#4240`'s 2026-10-15 supersession comment: *"the journal append IS the commit point on `create_point`: a failed append means the `CREATE` is skipped, so nothing persists"*; §17.1's `apply-first` lanes are the declared exceptions. | **Nowhere durable.** §17.1 (`:1664-1698`) states the *rule* (`every write goes to the truth store first`), and its eight `apply-first` rows are the declared *departures* — but the sentence that fixes the order of the **journal append vs the record write** exists only as a supersession comment on an issue about a different defect. |

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
   ⚠️ **And the comment itself says so**, which is why this is a finding rather than a complaint: it records
   that it had to *supersede another issue's test assertion* to make the ordering true, and that any
   departure *"requires an exception in §17.1 … the `OVERRIDES:` route — not a test flip"*. **That is the
   route A7.5's reopening must take** — and it is the reason Q3 needs a recorded home *before* the ordering
   changes, not after.

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
| A **transactional counter row** (`UPDATE … SET c = c + 1 RETURNING`) gives gapless committed numbering by serialising on one row, at a throughput cost | same | **PARTIALLY — and the cost half is NOT a lateral move** (corrected in place, `§A7.5`): the journal does serialise per graph on one file handle (`thread lock + flock + one write(2)`, `log.py:594-620`), but `flock` covers only the seal read-modify-write plus one `write(2)` and is released before the API, record and cache legs, and it is **abandoned** after a 5 s timeout (`_FLOCK_TIMEOUT_S`, `log.py:625-633`). A row lock held until `COMMIT` across six statements is therefore a **new** bottleneck, not an equivalent one — quoting this row as reassurance re-asserts the equivalence `§A7.5` removes. The row-lock **semantics** remain unmeasured here (no Postgres bench was run — a bench is a real cost and A7.5 is a proposal, not a landing). |
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
  re-measured (§9.2 says so per row) — **with one exception, added after review**: D-01's denominator. A
  single regex scan for mutating Cypher verbs over `tortoise/**/*.py` at this head returns **458 mutating
  `.query(` sites across 50 files**, which matches **neither** published figure (387/211/32 and 442/38/288).
  Method differs from theirs, so this is not a correction of either number — it is proof that **both are
  stale**, and therefore that D-01's "reconcile two numbers" framing was too generous: the task is
  *re-measure*, not *choose*. ⚠️ To be explicit about the limit: **my scan is not theirs** (it counts a
  regex over source, they count resolved call sites), so the honest output is "the denominator is unknown
  and one command shows it", not "the answer is 458".
- **`docs/ops/multi-graph-migration-runbook.md` not read**, with the reason stated in A1.1 row 38.
- **153 index documents not read**: 105 by a stated exclusion rule, **48 named individually with a reason** in
  A1.4. The first draft claimed a 41/141 partition that did not survive its own lists (36 entries in no
  class); that is corrected rather than restated, and A1.0 records what the wrong version said.

## 11 — Documents read *after* I thought I was done (the honest completeness measure)

The brief asks this explicitly, so it is recorded rather than left to inference:
`docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md`, `docs/epics/2026-10-10-7879-postgres-record/06-cache-
projection.md` §0.5, `docs/architecture/BOUNDED-OPERATION-CONTRACT.md` (which is where `read_after`'s
refusal is argued — and which changed A4.4's cursor table), `docs/verify/2026-10-31-record-capstone.md`,
`docs/evidence/9025-journal-bound/`, and `docs/plans/2026-10-18-7951-delete-point-outbox.md` (whose `#9207`
**reversal** of the delete ordering contradicted the ordering A7.5 initially assumed — the single most
consequential late read in this lane).

**What I could still be wrong about, named rather than implied:** the exact current site count for D-01 (three
numbers now disagree and I re-measured only to prove staleness, not to establish a denominator); and whether a
*record-side* guard exists on a path other than the one I searched (*"I found no guard"* is still an absence of
evidence — but D-24 no longer depends on that search, because **`#9318` already owns the defect**, which is
stronger than any claim I could make from a missing test).

---

## 12 — The review cycles, and what they changed (the honest version)

**Cycle 1.** A fresh-context reviewer was dispatched against this document **after** its first committed form, with the
explicit instruction to hunt for false citations, unsupported counts, and unexamined surface. **It found 16
issues (1 P0, 8 P1, 7 P2) and every finding below was re-verified against the source before being applied;
two were partly false and are recorded as such.**

**Cycle 2**, also fresh-context, ran against the commit that cleared the required `docs` check. It found 7
(3 P1, 4 P2). Every one was re-measured before being applied, and **one was partly false**: the claim that
`§A1.4`'s C2 class is 4 rather than 2 does not survive the population — 7 of C2's 9 prefixes match an index
entry and 5 of those already carry a C1 prefix, so C2 is **2** and only the *sentence* beside it was broken.

**Cycle 3**, fresh-context again, ran against cycle 2's fixes and found 4 more (1 P1, 3 P2) — including that
cycle 2's own replacement table was reproducible only under a membership rule it had not stated.

**Cycle 4**, fresh-context, ran against cycle 3's fixes and found 5 (2 P1, 3 P2): the stated rule omitted the
`.md` restriction so it reproduced different numbers than the table, two documents were listed as unread that
`§A1.1` records as read, and three rows of cycle 3's own audit were wrong (a mis-attributed fix, a stale
matcher figure, and a duplicated named row).

**Cycle 5**, fresh-context, ran against cycle 4's fixes and found 3 (1 P1, 2 P2): cycle 4's own exclusion
sentence named the wrong four rows, `§A1.1` row 16 cited a path that does not exist, and a row/document unit
had drifted. That is the honest shape of this section: **the arithmetic in `§A1` took five cycles to become
reproducible, and each cycle's failure is a different failure** — first a row/path conflation, then two conventions in one
table, then a partition whose rules were printed but whose population was not, then a rule that did not say
which artifacts it counted, then an explanation of that rule that cited the wrong examples.

**Cycle 6**, fresh-context, ran against cycle 5's fixes and found 3 more, all P2 and all **pointers**: the
enumeration of the 19 no-row documents still named the phantom `per-org-bytes` and omitted four real ones, two
§12 cells cited this document's own line numbers (which drift on every edit), and D-24 quoted
`record_writer.py:2412-2413` for a sentence at `2408-2410`. No substantive claim changed. **The lesson the
sixth cycle teaches is the one worth keeping: cite sections and row titles, never your own line numbers.**

**Cycle 7**, fresh-context, ran against cycle 6's fixes and found 2 more, both P2 and both pointers again: the
§12 cell that recorded "cite sections, not line numbers" was **itself** carrying four line numbers of this
document (already stale at the head that wrote them), and D-23's "corrects them 40 lines later in the same cell"
described a distance that cannot exist — that index cell is a single markdown line, and the correction is ~300
characters further on. Both fixed by removing the number. **A section that audits its own accuracy has to obey
the rule it is auditing**, which is the third time in this table that a correction paragraph was the defect.

**Cycle 8**, fresh-context, ran against cycle 7's fixes and found 4, all P2 and all provenance: three rows of this
table still carried the audit's own drift — a citation range that stopped mid-set, a rewritten quote whose nouns
had swapped, a pointer at a blank line — and a fourth recorded that cycle 7's own fix had no row. The corrections are in the table
below, which now holds a row for every cycle and every fix it claims.

**Cycle 9**, fresh-context, asked to open every quotation this time, found 6 more, all P2 — and five were
**citation defects in §A1, not in this audit table**: a pointer at a blank line (`ONTOLOGY.md` row 4), a
decision-bearing pointer aimed at a ratings table instead of the owner's rulings (`scoping-2313` row 10), a
mid-set range that excludes the very precondition A7.5 depends on (`durability-posture.md`), a quote whose
wording exists in no cited source (`#11821`), and a census that labelled the events directory as the volume's
"used" (974 MB volume / 700 MB used / 652 MB events — `#9025`). The sixth was this table's own count of its
cycle-8 rows.

**The cumulative lesson is worth stating plainly, because it is the artifact's own thesis:** an archaeology
document is only as good as its pointers, and pointers decay the moment you edit the document around them.
Nine cycles found no change to the *shape* the map describes; they found that roughly one in ten of the
`file:line` citations was off — a blank line, a mid-set range, a quote with a swapped noun. That is a fair
estimate of the defect rate to expect in any un-reviewed citation layer. Rows from every cycle are in the table below, in the order they were found.

| what the reviewer found | verified? | what changed |
|---|---|---|
| **P0 — the unproven-position class (`#9252`) is entirely absent**, and the document quotes the 974 MB journal that makes it bite | ✅ **verbatim** (`log.py:1503-1530`, `monitoring.py:1459-1466`) | **new debt row D-25**, plus the strongest evidence *for* A7.5; D-09 and A7.6 now connect to it |
| A1's partition is arithmetically false — 36 entries in no class, `C2 ⊂ C1` | ✅ **re-derived** (C1 = 145 not 128, union 146 not 141, 36 uncovered) | **A1.0/A1.4 rewritten**: the key is now the **path**, never a position number; 105 by rule + named with a reason (the count was 54 here; it is **48** after cycle 2, which also fixed the *membership rule*, so the denominators moved 159 → 153 for that reason and not only because three late reads left the population) |
| D-24 is a duplicate of open **`#9318`**, stated 4 lines below the quote that names it | ✅ **verbatim** (`record_writer.py:2412-2425`) | ⚑ removed from D-24, the tracker cited, and the argument demoted to "the reachable trigger" — which is all it ever added |
| A7.2 kept the "no sequence in the record schema" claim that A7.5 corrects | ✅ (the `rg` does return a `nextval` comment line) | parenthetical replaced with the narrow true statement |
| A7.5's "lateral move in concurrency" + unspecified seed | ✅ (`log.py:625-633`; `MAX(seq)+1` rejected at `:835-838`) | false equivalence **removed and stated as a real new bottleneck**; the seeding rule written |
| "three monotonic counters" vs the document's own fourth | ✅ (`20261012000003:167`) | **A4.3 is now four rows**, and says why the fourth is a surrogate |
| the reader census omits `journal_segments.read_all_segments`; the table sums to 13 | ✅ (`journal_segments.py:914`) | reader added; total corrected to **12**; the consequence for D-24 stated |
| `#9390` (OPEN) falsifies A7.5's "cannot happen" row | ✅ (`#9390` OPEN) | **new debt row D-26** + two rows added to A7.5's window table |
| §17.1 names "four further OPEN blockers" | ❌ **partly false** — `#9252` and `#9390` are OPEN, but **`#9350` is CLOSED, `#9213` MERGED, `#9391` CLOSED** | only the two genuinely open ones were added; **the other three were not**, and this is recorded rather than quietly dropped |
| `_NO_PROJECTION_FOLD` declares 8 types, not 2 | ✅ (`projection/__init__.py:8029-8080`) | D-17 corrected, and the undercount is now part of the argument |
| block quote alters "a seq" → "a position" | ✅ (`log.py:832-833`) | restored |
| `sdk.py:5653-5683` is a stale range lifted from the plan | ✅ (it is now the `status`-key refusal) | re-resolved to `sdk.py:13832-13833`/`:14008` |
| A6's tally (13/8/3) did not match its own table | ✅ (it was 14/8/2) | recount published, and **26 rows now** after D-25/D-26 |
| **§A1.0's coverage arithmetic did not close** — 22 indexed + 19 absent = 41 read, but one of the 41 rows is declared NOT READ, row 40 bundles three documents, and the three late reads were counted as unread while §A1.4 named them | ✅ **re-measured twice**: 182 rows → **180** name an existing file → **178 distinct** (two listed twice); read **44** = **25** with an own row + **19** without; unread **153**. `§A1.4`'s **2** for C2 **survives** — 7 of its 9 prefixes match an entry and 5 of those already carry a C1 prefix | A1.0 now states the **membership rule before the numbers** (the cycle-1 attempt was reproducible only under a different rule than it counted rows with, so it was replaced, not patched) |
| **§9.2 re-asserted the cost equivalence `§A7.5` had just removed** ("the per-graph serialisation is a lateral move"), and cited `log.py:820-826`, which is `_read_exactly` (def at `:806`), not the lock seam | ✅ (`log.py:594-620` is the seam; `:625-633` is its 5 s abandonment; `:820-826` is the tail read) | §9.2's row now says the opposite, cites the seam, and names the row as a place that would re-assert the removed claim |
| **`§A2` claimed a stored-log provenance that cannot be retrieved** — "every count comes from a `worktree_report.py run` log committed with the worktree report", while `.gitignore:158-159` ignores both the report and its logs | ✅ (`.gitignore:158-159`, `git check-ignore -v`) | provenance restated: the **command** beside each count is the artifact, and the `:GraphEventMeta` count has one now |
| `:GraphEventMeta` "appears at **44** sites" | ❌ **wrong by 17 sites** — reported 44, measured **61** (`rg -n 'GraphEventMeta' tortoise/ \| wc -l`; 61 at the measured base too, by `git grep -c`), of which 50 spell `` `:GraphEventMeta` `` | count corrected in §A2.4 and §A2.5, with the command printed beside it |
| three cross-references pointed at sections that do not say it: `§A7.0` (no such section), `§A4` for the 32,032 figure (it is `§A2.6`), and "**five** durable surfaces" above a six-item list the next sentence calls "six statements" | ✅ (`grep '^#\\+ '` has A7.1–A7.7 only; `32,032` occurs only in `§A1.2`, `§A2.6` and `§A6`'s D-19 row — never in `§A4`) | `§A7.0` → `§A7.5`; `§A4` → `§A2.6`; `five` → `six` |
| the `read_all` census excerpt pasted three output lines as if they were the command's contiguous head, when `rg`'s parallel walk makes the file order unstable between runs | ✅ (re-running the command returns a different file order; the **39** total is stable) | the block now pastes the real contiguous head and **says** the order is not stable, instead of implying an excerpt that reproduces |
| **the partition was still not reproducible**: the C1 prefix list yields 103 or 104 depending on the membership rule, and `docs/ops/multi-graph-migration-runbook.md` was in the unread set with its reason living in `§A1.1` row 38 | ✅ **re-derived**: with the rule stated in `§A1.0`, C1 = **103**, C2 = **2**, C3 = 0, named = **48**, and 103 + 2 + 48 = 153 ✓. Two documents had no named row at all — the runbook and `CONTRIBUTING.md` — and both now do | named table extended; the rule, not just the totals, is what changed |
| **three §A2 census blocks presented curated regroupings as raw `rg` output** (one pasted `:13495 / :13695 capture → reestablish` — a line `rg` cannot print; one claimed 15 lines and pasted 8; one claimed 11 and pasted 9) | ✅ (re-ran all three: **11**, **13** and **15** lines respectively) | all three now paste the **real, complete** output with its line count, so a reader diffing their own run against the block sees the same thing |
| `§A1.4` said `11821-journal-encoding` was not opened and was "named in D-09", while `§A1.1` row 40 records it as **READ** and D-09 names `#9025`/`#5612` | ✅ (`§A1.1` row 40; D-09 is the `§A6` row titled "The journal has no bound of any kind") | the row now records it as read and the D-09 attribution is withdrawn |
| `§A1.1`'s heading claimed "41 documents, each `READ`" over a table containing one `NOT READ` row (**fixed in cycle 2's commit, recorded here for completeness** — it was not a cycle-3 finding) | ✅ (row 38) | heading now says 41 numbered rows: 40 `READ`, 1 declared `NOT READ` |
| **`§A1.0`'s rule omitted the `.md` restriction**, so as printed it reproducibly gave 179 / 154 / 102 / 50 while the table said 178 / 153 / 103 / 48; the four rogues are the rows whose first existing path is `tools/finding_provenance.py`, `tortoise/__all__.py`, a JSONL and a script | ✅ (re-derived both variants: with `.md` → 180 / 178 / 153 / C1 103 / 2 / 48; without → 180 / 179 / 154 / 102 / 50) | the rule now names `.md`, lists the four excluded rows, and states the wrong variant's numbers so the next reader can tell which rule they reproduced |
| **two `#7951` increment plans were listed as "not read"** in `§A1.4` while `§A1.1` rows 21 and 22 record both as READ with section-level detail (and `§11` names one as a late read) | ✅ (`§A1.1` rows 21/22; `2026-10-18-7951-delete-point-outbox.md:14-17` carries the `#9207` ordering reversal the row cites) | the row is gone; a note under the heading records that both are read and that nothing in the 48 is |
| cycle 3's row for the `§A1.1` heading described a **cycle-2** change, and its "named only 40 of the 48" was a bad matcher's artefact (the measured gap was **two**) | ✅ (`git diff ff98ab6bad..7f735b3130` contains no heading change; a fresh coverage pass finds 0 unnamed) | the heading row is labelled as cycle 2's, and the 40/48 figure is removed |
| cycle 3 **added a duplicate** `docs/evidence/7949-capstone/*` row to the named table, which already had one for the same six files | ✅ (two identical rows in `7f735b3130`) | the duplicate is removed |
| cycle 4's exclusion sentence named **four rows as contributing no member**, and all four do (their later `.md` token is the member); the two rows that actually contribute none are `docs/00_index.md:84` and `:167`. Applying the sentence as written gave 176 / 175 / 150 | ✅ (re-derived: the four cited rows resolve to `CONTRIBUTING.md`, `sdk-surface-declaration.md` and two `docs/…` members; `:84` names a generated file not in the tree and `:167` names only a directory) | the paragraph now names the two real exclusions, says `182 − 2 = 180`, and explains why a cell that opens with a `.py` still contributes |
| `§A1.1` row 16 presented `docs/ops/postgres-per-org-bytes.md` as a read document, and that path **does not exist** | ✅ (`ls` → not found; `git ls-files` → empty) | the row now records what was actually read (`#12762`'s issue body + plan comment) and says the path does not exist; the read set is **44**, not 45 |
| `§A1.4`'s row count was stated as "48 rows" when the section has 9 body rows covering 48 documents | ✅ (9 data rows, per-row coverage 2+6+11+2+7+7+7+6 = 48) | the unit is now "documents", matching the per-document coverage it claims |
| the enumeration of the **19** read documents with no index row still named `per-org-bytes` (a document that does not exist) and omitted four that do — the same defect cycle 5 fixed in `§A1.1` row 16, left standing six lines above it | ✅ (re-derived from the tree: 3 migrations, 2 governing docs, 5 journal plans, the unjournalled-mutation plan, the `#7951` plan, `03-schema.md`, `06-cache-projection.md`, the `#2313` scoping, the changelog, `docs/00_index.md` itself, `9025-journal-bound`, `record-capstone.md` = 19) | the list now names all 19 and records that the phantom was removed |
| two §12 cells cited the document's **own** line numbers for the `32,032` figure and for D-09, none of which resolve after later edits | ✅ (`32,032` occurs only in `§A1.2`, `§A2.6` and `§A6`'s D-19 row; D-09 is the `§A6` row "The journal has no bound of any kind") | both cells now cite *sections and row titles*, which is the only form that survives an edit — **including this cell, which a later cycle caught still carrying four line numbers of its own** |
| D-24 quoted `record_writer.py:2412-2413` for a sentence that is at **2408-2410** (`§A4.2` already cited it correctly as `:2409`) | ✅ (`awk 'NR>=2406 && NR<=2414'`) | the citation is corrected to `2408-2410` |
| cycle 6's own evidence cell cited four of this document's line numbers for `32,032`, already stale at the head that wrote them | ✅ (`32,032` occurs only in `§A1.2`, `§A2.6` and `§A6`'s D-19 row; D-09 is the `§A6` row "The journal has no bound of any kind") | both cells cite sections and row titles; §12 re-audited so no `:NNN` in it points at this document |
| D-23 said the index cell "corrects them 40 lines later"; that cell is one markdown line | ✅ (`docs/00_index.md:153` is a single 1,931-character line; the correction begins ~300 characters after the stale figure) | stated in characters, with the one-line fact named |
| D-17's "eight types" cited `projection/__init__.py:8029-8052`, a range that stops before `SessionNarrated` and `TurnsRecorded` — both of which the row names as members | ✅ (the set closes at `:8080`; the cited 23-line range shows 6) | range widened to `:8029-8080` |
| the schema quote had its nouns swapped — the schema says *"`seq` is the idempotency key: applying a position twice is a no-op"* | ✅ (`03-schema.md:86`) | quoted verbatim; the paraphrase that had travelled through `log.py:832` is no longer presented as the schema's words |
| the `#9390`/reader row cited `journal_segments.py:913`, a blank line; `read_all_segments` is at `:914` | ✅ (`sed -n '911,916p'`) | corrected in both places it appears |
| §12's preamble printed cycle 7 before cycle 6, and the claim "rows from every cycle" was false for cycle 7 | ✅ (heading order `grep -n '^\*\*Cycle'`) | cycles are in order and every cycle, including this one, has a row |
| `§A1.1` row 4 pointed at `docs/ONTOLOGY.md:71` for the Procedural row; `:71` is blank, the row is `:70` | ✅ (`awk 'NR==70'` / `NR==71`) | corrected — a blank-line pointer, the same class cycle 8 fixed in `journal_segments.py` |
| `§A1.1` row 10 pointed at `:130` for the pool-competition bug and `:206`/`:211` for the owner's Q1/Q4 rulings; `:130` and `:206` are blank and `:207-211` is a ratings table | ✅ (the finding is `:65`; Q1 is `:226`, Q4 `:229`) | re-pointed at the three lines that carry them |
| `durability-posture.md:264-380` was cited twice, and A7.5 depends on the QUIESCED precondition inside it; the `:GraphEventMeta` block runs to `:470` and the precondition is at `:403` | ✅ (`sed -n '264p;403p;470,471p'`) | range corrected to `:264-470`, and the cutover row now cites `:403` directly |
| `#11821` was quoted as *"rented for a watermark"*; its body says *"so it rents RAM for a counter"* | ✅ (`gh issue view 11821`, body) | quoted verbatim, including its "watermark, not provenance" clause |
| the `#9025` census was printed as "974 MB, 652 MB used"; 652 MB is the EVENTS DIRECTORY, the volume's used space is 700 MB (78 %) | ✅ (`gh issue view 9025`, comment 6867080064: `/dev/vdc 974M 700M used … 78%`, `/data/events 652M`) | corrected in §A1.2 and in D-09, which now agree |
| §12's cycle-8 sentence said "two rows" and then listed three | ✅ (the table carries three drift rows plus the cycle-7 row) | corrected to three plus one |

⛔ **The most important thing this cycle did was make the shape harder, not easier.** Two findings (D-25,
D-26) are cases A7.5 had **assumed away in both directions** — a record write with no journal position, and a
journal record with no record row — and one (§A2.6, `#10000`) removed an assumption in A7.1: **on the armed
lane the graph holds one `:GraphEvent` carrier per turn and essentially no `:Point` nodes at all**, so "the
graph is a derived cache" is too charitable a description of what is actually there.

**Three documents were read *because* the review found them unclassified or mis-summarised** —
`docs/evidence/10000-turns-bytes/README.md`, `docs/evidence/9558-record-write-cost/README.md`,
`docs/evidence/7950-capstone/README.md` — and all three changed a conclusion (A2.6, D-01, A7.5). **That is the
measure this lane should be judged on:** not whether the first draft was right, but whether the artifact that
ships says what the sources say.
