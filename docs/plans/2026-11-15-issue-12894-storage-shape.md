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

> **This is v2 (Stage B round 2).** v1 answered the archaeology mandate and passed its own review cycle; the
> Stage-B review then found the **mechanism** unbuildable — 7 blocking defects, four of them silent — and this
> revision answers each one with a mechanism plus a falsifier. **`§A8` is the deliverable that proves none was
> dropped** (`finding → section`), and **`§A8.2` is what this revision could not resolve** — read that first if
> you are reviewing. The map (A1–A6) is revised only where the review found it contradictory; the shape (A7) is
> substantially rewritten.

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
| documents **read** for this map | **45** |
| …of those, with an index row of their own | **26** |
| …of those, **without one** (reachable only by cross-reference) | **19** |
| index documents **not read** | **152** |

The two documents listed twice are `docs/registry-graph-schema.md` (`:73`, `:74`) and
`docs/product/sdk-surface-declaration.md` (`:85`, `:87`). The read set is **45**: the **42** that occupy
`§A1.1`'s **41 `READ` rows** (42 numbered rows, one of them declared **NOT READ**; row 42 is
`docs/INGEST_CONTRACT.md`, which Stage B read and quotes at `§A7.5`; row 40 bundles three evidence records;
row 16 records an issue body and its plan comment rather than a document — the path an earlier revision
printed under it does not exist) plus the **3** read after the first draft (`§12`). **26 + 19 = 45** and
**26 + 152 = 178**, so both partitions close.

⛔ **Two earlier revisions of this table were arithmetically broken, and how they broke is the point.** The
first said 41 / 22 / 19 / 159: it counted *rows* as documents (so the three-record row counted once, hiding
2 documents inside it), it counted the row declared **NOT READ** as read, and it left the three late reads
inside the "not read" population while naming them in `§A1.4`. The second said 180 / 155 and was reproducible
only under a *different* membership test (any backticked token anywhere in the cell) than the one it counted
rows with — two conventions in one table. **A population whose rule is unstated is not a population**, which
is why the rule now comes before the numbers.

⛔ **And that is the finding, stated as arithmetic rather than as an impression: 19 of the 45 documents
this map needed — 42 % of the read set — have no index row of their own.** Named exactly, they are: the three
record migrations (`20261012000001`, `20261012000005`, `20261114000001`); the two governing documents
(`STORAGE-ARCHITECTURE.md`, `event-catalog.md`); all five journal-mutation plans
(`#2194`/`#2295`/`#2488`/`#3689`/`#2814`); the unjournalled-mutation class plan; the `#7951` atomic-write plan;
`03-schema.md` and `06-cache-projection.md` from the record epic; `docs/scoping-2313-per-graph-backups.md`;
`STORAGE-ARCHITECTURE-CHANGELOG.md`; `docs/00_index.md` **itself**; the `9025-journal-bound` evidence record;
and `docs/verify/2026-10-31-record-capstone.md`. (An earlier revision named `per-org-bytes` here — there is no
such document; the per-org-bytes work is a migration and a module, and `§A1.1` row 16 records its issue body.) **Reading was driven by cross-reference and by what the issue named, not by the
register** — which is exactly what makes D-22 a defect rather than a preference.

**So the scope proof cannot be "the index, closed".** Two claims are made instead, and each is checkable:

1. **What was read is listed, per document, with the section or line taken** — `§A1.1`, 42 rows. Any reader
   can take a row and go to the line.
2. **What was *not* read is listed, per document, with the reason it was not needed** — `§A1.4`, 47 documents for
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

### A1.1 In scope — 42 numbered rows: 41 `READ`, 1 declared `NOT READ`

| # | Document | Status | What was taken from it (section / line) |
|---|---|---|---|
| 1 | `docs/durability-posture.md` (936 lines) | **READ (full)** | §The rule `:41`; `:48` *"It is never the durability mechanism"*; `:59` *":GraphEvent is the 30-day delivery/audit stream, not a backup"*; §Authoritative node classes `:73`; §The one carried high-water mark `:264` (the whole `:GraphEventMeta` block `:264-470`: the `last_seq` allocator, `MAX_SEQ = 2**53-1`, `first_seq` never carried, `reestablish_watermark`, the "**A rebuild still requires a QUIESCED graph**" residual); §Per-deployment posture `:471` incl. the five-row deployment table `:575-581` (the record's own row is `:577`) and §The record's posture `:583`; §Why this document exists `:660` (the gate's claim shapes, `#9867`'s blind wordings); §Open items `:710` |
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
| 42 | `docs/INGEST_CONTRACT.md` (the durability sections) | **READ (the durability clause)** | `:463-467` *"the default embedded mode has no AOF window, so the JSONL journal is the durability contract"* — the third unqualified statement of the split, and the one A7 must not contradict |

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

### A1.4 Not read — 152 index documents, by declared class, and 47 named individually

`docs/00_index.md` is **247 lines carrying 182 rows**, of which **180 name an existing file** and **178 name
something distinct** (`§A1.0` states the membership rule). Of those 178, **26 were read** and **152 were
not**. The 152 are accounted for as **105 by an exclusion rule** and **47 named individually with a reason**
— so there is no unclassified remainder, and the two claims are separate: a rule is checkable in bulk, and a
named reason is checkable one at a time.

**By rule (105).** Each class is a **path-prefix rule**, evaluated over the 152 unread documents, so any reader
can reproduce it without trusting a count:

| Class | Rule (path prefixes) | Count |
|---|---|---|
| **C1 — another subsystem, asserts nothing about the storage shape** | `docs/quickstart-*`, `docs/beta-feedback`, `docs/infra-runbook`, `docs/ops/ci-*`, `docs/ops/batch-*`, `docs/ops/container-lifetime`, `docs/ops/BATCH-REVIEW`, `docs/ops/abuse-flag`, `docs/ops/ISSUE-WORKING`, `docs/blog-authoring`, `docs/auth-architecture`, `docs/authorization-architecture`, `docs/engineering/auth/`, `docs/oauth-mcp`, `docs/adr/ADR-010`, `docs/adr/ADR-012`, `product/competition`, `docs/product/`, `docs/ax/`, `docs/scoping/`, `docs/research/2026-08*`, `docs/research/2026-09-0*`, `docs/research/2026-09-1*`, `docs/research/2026-09-25-4503`, `docs/research/2026-10-15-2427`, `docs/research/memory-attribution`, `docs/research/2026-11-08-1779`, `docs/ci/`, `docs/ci-timing`, `tools/ci_check_repair`, `docs/plans/2026-09-10-2789`, `docs/plans/2026-09-22-3525`, `docs/plans/2026-10-11-2081`, `docs/plans/2026-10-18-8098`, `docs/plans/2026-10-16-8051`, `docs/plans/2026-10-22-1402`, `docs/epics/2026-11-07-2281`, `docs/runbook/4220`, `docs/runbook/3514`, `docs/runbook/b7-*`, `docs/runbook/3806`, `docs/runbook/1987`, `benchmarks/`, `docs/benchmarks/comparison*`, `docs/benchmarks/1144*`, `docs/adr/ADR-009`, `docs/adr/ADR-011`, `docs/experiments/`, `docs/scoping-2600`, `docs/evidence/3589`, `docs/evidence/1789`, `docs/evidence/9664`, `docs/evidence/3770`, `docs/evidence/10137` | **103** |
| **C2 — process, or observability of the machinery** | `docs/ops/batch-integration`, `tools/run_affected`, `tools/batch_review`, `docs/infra/tmpdir-sweep`, `docs/scoping/2026-09-18-4098`, `docs/scoping/2026-09-20-4408`, `docs/runbook/4408`, `docs/scoping/2026-09-01-2090`, `docs/scoping/2026-09-02-2127` — **7 of the 9 prefixes match an entry, and 5 of those 7 already carry a C1 prefix** (`docs/ops/batch-integration` and the four `docs/scoping/…`), while `tools/run_affected` and `tools/batch_review` match no entry at all; the class is therefore **2**: `docs/infra/tmpdir-sweep.md`, `docs/runbook/4408-branch-reaper.md`) | **2** |
| **C3 — reads a storage surface, asserts nothing about its shape** | none unread: every such entry was **read** instead (see `§A1.1` rows for `#4503`, `#11083`, `#11469`, `#8607`, `#1779`, `#1402`). **The class is empty by construction here, and saying so is the point** — this was the class the first draft used to *exclude* documents it had not opened. | **0** |

**103 + 2 + 0 = 105. 105 + 47 = 152.** ✅

**Named individually (47) — in-scope by path, not read, with the reason.** (Two `#7951` increment plans —
`2026-10-17-7951-update-point-outbox.md` and `2026-10-18-7951-delete-point-outbox.md` — appeared in this table
in an earlier revision and are **read**: they are `§A1.1` rows 21 and 22. Nothing in the 48 is read.) These are the entries whose
prefix does not match an exclusion rule, so each needs its own stated reason. (Three further documents were
in this list in an earlier revision and are **read** now — `docs/evidence/10000-turns-bytes/README.md`,
`docs/evidence/9558-record-write-cost/README.md`, `docs/evidence/7950-capstone/README.md`; they are counted
in `§A1.0`'s 45 and the reason each was read late is in `§12`.)

**Two further contradictions the Stage-B review (M13) named, checked at head rather than assumed, with the
result recorded either way:**

| M13 claim | verdict at this head | evidence |
|---|---|---|
| three documents are listed READ in `§A1.1` and NOT READ in `§A1.4` | **already fixed** before Stage B round 1 — the two `#7951` increment plans and `docs/evidence/11821-journal-encoding/README.md` were reclassified by cycles 2 and 5, and the rows below say so | this table's preamble and the `10976…` row |
| one row cites a document that does not exist (`docs/ops/postgres-per-org-bytes.md`) | **already fixed** — `§A1.1` row 16 now records what was actually read (`#12762`'s issue body + plan comment) and states the path does not exist | `§A1.1` row 16 |
| two wrong path prefixes (`docs/INGEST_CONTRACT.md`, `docs/client-server-split.md`) | **TRUE at head, now fixed** — they were printed as bare basenames inside a `docs/architecture/…` row | the row above; both live at `docs/` root (`git ls-files`) |
| four line citations point at blank lines or the wrong row | **cannot be reproduced at head** — an exhaustive re-audit of **every** `path:line` citation in this document (160 resolvable, basenames resolved through `git ls-files`) returns **2** flags, and **both are inside `§12`'s audit table quoting the pointer it corrected** (`journal_segments.py:913`, `ONTOLOGY.md:71`); no live citation points at a blank line | `python3` audit script, run under `tools/worktree_report.py run`; log kept with the worktree report. ⚠️ This is a **pointer** check, not a content check: it proves no citation lands on a blank line or past EOF, and cannot prove a citation lands on the *right row* |

| Not read | Why not |
|---|---|
| `docs/architecture/MEMORY-SUBSTRATE-PROBLEM-MAP.md`, `MEMORY-SUBSTRATE-FINDINGS-LEDGER.md` | Declared status/durability halves of `#2820`/`#2852`; their durability claims delegate to `durability-posture.md`, which was read. **The findings ledger is the one I would read next** — it is the corrections half and may hold an item this map repeats. |
| `docs/evidence/7949-capstone/*` (6 files), `docs/epics/2026-11-04-7949-extractor-v4-capstone/` | The extractor-v4 capstone. Storage appears only as a cost basis; the storage conclusions live in `§17` of the architecture doc, which was read. |
| `docs/evidence/10976-licence`, `7846-indexed-graph-restore`, `7983-cost-magnitudes`, `8503`, `8607-w1b`, `11083`, `11086`, `11739-*`, `4623-*`, `2026-10-21-shared-coordination-memory` | **Partially read via `§A1.2`'s issue census** and each is a *measurement of a different question* (eviction licence, RESTORE wedge, cost magnitudes, operator edges, legacy ids, pack census, write-rate gates, quote population, cohort cap, fleet coordination). `11821-journal-encoding` is **not** in this list: it is one of the three evidence records **read** in `§A1.1` row 40, which is where its 9,176 → 5,423 B figures come from — an earlier revision of this row wrongly said it was not opened, and wrongly attributed it to D-09 (D-09 is the missing journal bound; it names `#9025` and `#5612`). |
| `docs/ops/multi-graph-migration-runbook.md`, `CONTRIBUTING.md` | Neither asserts anything about the journal/record shape. The runbook moves a **graph key** between stores and its storage claims delegate to `durability-posture.md` (the full reason is `§A1.1` row 38); `CONTRIBUTING.md` carries the finding-provenance gate, which is process. |
| `docs/plans/2026-10-09-5362-retroactive-correction`, `2026-10-10-3644-source-version-window`, `2026-10-11-1990-diff-boundary-invariant`, `2026-10-16-8056-class-plural-enumeration`, `2026-10-18-8882-phase0-1-census`, `2026-11-04-5087-epic-decomposition`, `2026-11-09-11656-endpoint-kind-binding` | Each is a **per-door or per-mechanism** plan: back-dating, `:Source` version windows, the DIFF cursor boundary, temporal enumeration, graph census, the extractor epic, endpoint-kind binding. They change *what* is written, not the storage shape that carries it. `3644` and `1990` are the two I would read next, because `:Source` versioning and the cursor boundary are the two payload-level invariants `§A7` touches. |
| `docs/architecture/ENTITY-VERDICT-BANDS`, `docs/architecture/S2-DECISION-SEAM`, `docs/architecture/CURATION-11469`, `docs/client-server-split.md`, `docs/epics/2026-11-04-5090-vector-layer/TEST-DESIGN-surface-map`, `docs/registry-graph-schema` | Decision/contract surfaces above the storage layer. ⛔ **Two corrections to this row, both from the Stage-B review (M13):** (a) the paths above are now spelled in full — an earlier revision printed these as bare basenames inside a `docs/architecture/…` list, which read as though `INGEST_CONTRACT.md` and `client-server-split.md` lived under `docs/architecture/`; they live at **`docs/` root**; (b) **`docs/INGEST_CONTRACT.md` is no longer "not read"** — the review was right that `:463-467` carries a **durability claim for the embedded lane** (*"the default embedded mode has no AOF window, so the JSONL journal is the durability contract"*), which is a storage-shape assertion at the contract level, not above it. Reading it changes no A-row, but it does mean the map can no longer say the contract surface asserts nothing about durability: **it asserts the opposite of `durability-posture.md:48` for one lane**, and `§6` now records that as a third unqualified sentence alongside `STORAGE-ARCHITECTURE.md:361`. `ENTITY-VERDICT-BANDS` is still unopened and remains a stated gap. |
| `docs/runbook/2578-*`, `2655-*`, `2684-*`, `2886-*`, `2976-*`, `4107-*`, `4210-*` | Temporal/retrieval/preference measurement runbooks. Reading surfaces only. |
| `docs/ops/669-post-flip-verification`, `cost-allocation`, `extraction-cost-metrics`, `docs/infra/pytest-timeout-guard`, `tools/finding_provenance.py`, `tortoise/__all__.py`, `docs/research/2026-10-19-8709-member-own-key-surface`, `docs/research/2026-11-09-11656-kindpair-enforcement` | Verification procedure, cost *policy*, test-infrastructure, and two non-storage research docs. |

⛔ **The honest summary of this section:** 26 of 178 index documents were read (15 %), and **the reason that
is not a scandal is only that the index does not contain the governing documents** — 19 of the 45 documents
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
| `record_writer.write_live_record` (`:2270`) | the **live** writer of the same row, from the append's returned position. ⛔ It takes **no `position=` kwarg** — `write_live_record(record, *, durable, seq, tenant)`; `position=` belongs to `RecordWriter.write` (`:1519`, its `:param` at `:1541`), and `write_live_record` passes `position=seq is not None` (`:2479`) |
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
to the record→cache seam (`#10070`/`#10017`), *"not claimed here"* (`:85-87`; `:91` opens the instrument's limits section, which is the next bullet's subject).

⚠️ **Instrument limit, quoted rather than assumed away:** `GRAPH.MEMORY USAGE` reads whole MB, reads **0** for
the armed corpus, and is **blind to the HNSW `vectorset` module** (`#5331`) — `used_memory` is the instrument,
and the figures are a per-session marginal at 25 sessions × 40 turns × 1,500 chars, not a per-node constant.

### A2.7 The journal's MUTATORS that are not appends — the census §A2.1 does not cover (M1)

§A2.1 counts **writers of records**. It does not count **writers of the FILE**, and the Stage-B review is right
that the omission matters, because three paths mutate the file in ways no append path can see — and each one
is a **D-28 trigger** (the ordinal's seed) and a **D-24 trigger** (the two-journal absorption):

| mutator | what it does to the file | why it is not an append |
|---|---|---|
| `backup.py:638` `shutil.copy2(events_file, events_path)` | **replaces** the journal wholesale on a restore | `#7767`'s own comment: *"everything below REPLACES the destination — the journal always"*. After it, the file's identity and length are whatever the backup held. |
| `journal_segments.py:1132` `seal_active` | **renames the active segment aside** and creates a fresh empty `events.jsonl` (`:1141`) | the file-local 1-based index restarts at 1 while the *segment-spanning* reader (`read_all_segments`, §A2.1) keeps counting. `TORTOISE_JOURNAL_SEGMENTS=1` is the switch; the writers are flag-gated **OFF**, so this is one flip from live. |
| `log.py:2215-2224` `tail_bytes` (the guard) | documents that an **in-place truncate followed by a regrow** past the old watermark *"leaves the same inode at a larger size with different bytes below the seam"* — and that `st_dev`/`st_ino` and size cannot verify it, **nor can `mtime_ns`** | there is no code path that *does* this; what is missing is any check that *detects* it. The pair (mutator absent, detector absent) is the hazard. |

**Why this belongs in the map and not in a footnote.** The ordinal's seed is a **recount**, so a mutator that
changes the recount changes the seed. `A4.1` says the sidecar validation's failure mode is *"a recount, never
a wrong answer"* — true of the **position** (a recount recovers it) and **false of the seed** (a recount *is* the
answer, so a short journal silently seeds low). D-28 carries that row, and §A7.6's falsifiers 7–9 are its
executable form.

### A2.8 `capture_spool` — the in-tree comparable that was missing (M4)

See D-30 for the row and the ruling text. The two facts that make it a **comparable** rather than a curiosity:
it appends **outside any transaction** with `O_APPEND` + `fsync` (`:2207-2213`), and it is idempotent by a
**content-addressed key** (`sha256(session_id \0 sha256(turns))`, `:614-623`) rather than by a caller-held
cursor. The two facts that make it a **constraint on this design**: its bound is an **admission** bound that
**refuses** rather than reaps (`:24-33`, owner ruling `#3870`), and its oldest undelivered entry is *never*
reclaimed to make room. **So this repo already contains a durable app-written store whose answer to "what do
we do at the ceiling" is "refuse, count it, keep the backlog" — and whose answer to "bound by time" is an
owner `NO`.** Any bound proposed for `journal_event` (D-21/B7) is now measured against that precedent, not
against a blank page.

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

### A3.1 The two lanes the matrix was missing (M11)

Both were absent from the original matrix, and both matter because **the record allocator's arming rule is
exactly what they either do not have or cannot use**:

| lane | why it is absent from the matrix above | what the flip does to it |
|---|---|---|
| **MCP BASE / stdio** | it is **journal-less by construction** — `mcp_auth.py:193-201` resolves a stdio transport with no HTTP door, so no `EventLog` is on the path at all. It is not "a deployment without a journal"; it is a **transport** that never had one. | **nothing, and that is the point:** it has no journal, so it has no position to carry and no export to repair. Under A7.5 it is the cleanest no-record lane — it must remain **provably** outside the ordinal, which is what A7.6's falsifier 5 asserts. |
| **M2 session-capture** | it is a **record door whose write is declined on purpose** — `write_live_record(… durable=False)` (`record_writer.py:2270-2274`, decline semantics at `:2287-2290`, guard at `:2106-2107`) | ⛔ **The flip removes the reason it declined.** `durable=False` exists because the capture lane has *no journal position to hand the record* — which is precisely the coupling A7.5's flip removes (record allocates, journal carries). So this lane **gains** an ordinal and stops being a `NOT_DURABLE_JOURNAL_REFUSAL`; A7.7 step 6 must convert it deliberately, and §A7.8's test column names the file that pins the decline. |

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
(`log.py:1894` `position()`, `log.py:1207` the recount, `log.py:1211` `read_for_replay`). The citations are
corrected here to the anchors that carry them: the opaque **tail token** is `cursor_at_end()` (`log.py:2573`),
not `read_after` (`:2555`, which *consumes* a token and returns events).

⚠️ **"Never a wrong answer" is scoped to the POSITION and does not extend to the SEED (`M2`, D-28).** A
recount recovers a stale *position*, so the sentence is true where it was written. But §A7.5 seeds a counter
from a recount, and for the seed the recount **is** the answer — so a journal that shrank (a restore replaced
it, a segment was sealed aside, an in-place truncate+regrow defeated `st_dev`/`st_ino`/size/`mtime_ns`) seeds
the allocator **low** and the error is silent and cumulative. `#9190` chose this
over three alternatives, and **`MAX(seq)+1` was rejected by name** (`log.py:835-838`):

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
   it; `RecordWriter.write(..., position=True)` writes it, and `write_live_record` passes `position=seq is not None` (`:2479`). With no journal, `seq=None` — hence `position=False` — is the shipped shape —
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
| Live record writer fed by the append's ordinal | **YES** | `record_writer.write_live_record` (`:2270`, **not** `:1541` — that is a `:param` line in `RecordWriter.write`), 3 doors (`#9078`) |
| **A wiring check that asserts the live door writes the journal's position** | **YES, and it must be re-derived** | `tools/record_wiring_check.py:372` `live_door_writes_the_journals_position` and `:547` `live_call_sites_pass_the_ordinal` (M12) |
| Record **replay** with a `start_seq` watermark seam | **YES** | `record_projection.replay_plan` (`:6505-6545`), `#9187` |
| **Record → cache** projection (a 5th rebuild engine) | **YES** | `cache_projection.py`, `FalkorProjection.rebuild_from_record` (`projection/__init__.py:19188`) |
| Record **reconcile** with a production trigger | **YES** | `record_reconcile.py` (`python -m tortoise record-reconcile`), `#9189` |
| Record **backup / restore** verbs + a drill | **YES (drill not against hosted)** | `record_backup.py`, `record-restore` (`durability-posture.md:577`, the hosted-record row of the deployment table; §The record's posture `:583`) |
| Journal **segments** (manifest + boundary + a raising reconstruction claim) | **YES, writers flag-gated OFF** | `journal_segments.py`, `TORTOISE_JOURNAL_SEGMENTS` |
| Atomic-write **ordering** on 4 doors | **PARTIALLY** | `create_point` (`#7951` inc 1 — **the record IS the commit point**), `update_point` revision arms (inc 2a), `delete_point` (**order reversed by `#9207`**), onboarding (declared, not journalled) |
| **`last_applied_seq` on the record** (per-projection watermark) | **NO** — and this is the gap | the only `last_applied_seq` is `consistency.py:1341-1396`, a **file-based** journal↔graph watermark, not a record-side one. `#7951` inc 2's second half is *"STILL OPEN"* |
| **A transactional outbox table** | **NO** (and none is needed for `journal_event` — the table *is* the outbox, because it is written in the same transaction as the layer rows) | — |
| **A record-side ordinal allocator** | **NO** | — (proposed by §A7; **authorised by the `#9190` reopen ruling**, comment `6902871297`) |
| **A `record_cursor` table** (the allocator's own row) | **NO** | — (§A7.8 lists it as a deliverable, not a proposal: the ruling names the mechanism) |

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
| **D-30** ⚑ | **`capture_spool.py` (3,581 lines) + `capture_backlog.py` (649) are the closest in-tree comparable to this design and were absent from the map entirely** — a durable, app-written WAL of user content, appended outside any transaction (`O_APPEND` + `fsync`, `:2207-2213`), with **an owner ruling** (`#3870`: type-2 user data kept *until the user deletes it*; a TTL *"CONTRADICTS that ruling"*; count and bytes are an **ADMISSION** bound, not an eviction trigger; at a ceiling with nothing reclaimable the spool is `SATURATED` and **REFUSES the write**; *"the oldest undelivered entry is never reaped to make room"*) and a **content-addressed capture key** `sha256(session_id \0 sha256(turns))` that makes a replayed spool file **one** session | `capture_spool.py:24-41`, `:614-623`, `:804-806`, `:2207-2213` | **O** for the shape, **B** for the count | ⛔ M4, and a `grep spool` over this plan returned 0 hits before this row existed. It is `O` for the *shape* (a client-side spool, not a second server-side truth store) and **`B` for the surface count**, because it is a durable app-written surface of user content this design must rule in or rule out — and it is the **second** such surface carrying an explicit owner ruling that says *do not bound by time* (the journal is the first, per D-21/D-27). Two things transfer directly: the **admission-bound / refuse-never-reap** posture (the only bound shape this repo has an owner ruling for), and the **content-addressed idempotency key** (the journal's `seq` is a *positional* idempotency key, so re-running must be safe by the same construction, not by the caller's memory). §A7.4 counts it. |
| D-19 | **The `:GraphEvent` mirror rents 32,032 B/session for a watermark** — and the premise is now stale | `#11821` body; removed by `#10017`'s carrier on the armed lane | **R** (the *premise*), **B** (the allocator) | Retire the **premise**, keep the store (owner-closed `NOT_PLANNED`), fix the **allocator** (D-07). Three distinct fates for one paragraph. |
| D-20 ⚑ | **`record_read.py` never reads `journal_event`** — the transcript is served from `public.session_narrative`/turn rows | `record_read.py:34` (*"It never reads `public.journal_event`, and it never reads the graph"*) | **O** | ⛔ **Narrowed by review (M14), and the widening it used to carry is withdrawn.** As first written this row generalised to "the record's read path does not depend on the journal table at all" and drew an inference ("a precondition A7 does not have to build"). **Both are false and both were checked at head:** `record_serve.read_node` folds the journal on a production read path (`record_serve.py:827-830` → `apply_event_stream_folds` over `read_lifecycle_events`, serving `GET /v1/points/{id}`), and so does `rebuild_from_record` (`projection/__init__.py:19300-19301`). Only the **transcript** read is journal-independent. So the true statement is narrower and the inference is deleted: **A7 *does* have to keep `journal_event` readable on the point-read and rebuild paths, and that is precisely why B7's row matters.** |
| **D-27** ⚑ | **`journal_event` is the ONLY carrier of `Object.status`/`Subject.status` on two write paths** — the `EntityMutated op=restatus` value *"reaches the record ONLY here"*, and a creation record's birth `status` is *"its only carrier"* (`_split_properties` refuses the name in `props`) — while `apply_event_stream_folds` **skips every later fold once a node is terminal** | `cache_projection.py:2961-2973`; `:3364-3366` | **B** | ⛔ **This is the row that makes any retention bound on `journal_event` dangerous, and it is the answer to B7.** Because a terminal fold is sticky, dropping an *earlier* terminal row lets a *later* fold apply — so an archived `Object` is answered **LIVE** after a rebuild or an anchored read. That is not a stale read; it is **resurrection**, the failure class `STORAGE-ARCHITECTURE.md:361` already says a replay must never commit. **Consequence for D-21:** `journal_event` is a **PRIMARY** store for those two properties, so a bound on it can only be **refused or explicitly ruled** — never derived from `#9025`'s file-only decision, which is about a *redundant* copy. |
| **D-28** ⚑ | **The ordinal requires that the journal never SHRINK** — and three paths shrink it today: a restore **replaces** it (`backup.py:625-638`, `shutil.copy2`), `seal_active` renames it aside and starts a fresh empty one (`journal_segments.py:1132-1141`), and an in-place truncate + regrow defeats every cheap identity check (`log.py:2221-2224`: `st_dev`/`st_ino`, size *and* `mtime_ns` cannot verify it) | `log.py:1503-1530`, `record_writer.py:2407-2416`, `backup.py:638`, `journal_segments.py:1132` | **B** | ⛔ **A4.1's claim that the sidecar validation costs "a recount, never a wrong answer" is FALSE for the ordinal** (M2): for a *position* a recount is recovery, but for the *seed* the recount **is** the answer, so a shortened journal silently re-seeds low. **Three live triggers, not one** (M1): `TORTOISE_JOURNAL_SEGMENTS=1` (writers flag-gated **OFF**, so it is one switch-flip away) makes the file-local index return `1` again after a seal while `journal_event` holds `1..N` — every new row then absorbed — and `#9025`'s own recommendation *is* the non-destructive segmentation plumbing, so the trigger is on the roadmap rather than merely reachable. **Under the record allocator this class narrows but does not vanish:** the *allocator* survives shrinkage (it never recounts), while the *seed* and the *replay floor* do not. |
| **D-29** ⚑ | **The record's own restore is a SECOND writer of `journal_event`** — `record-restore` bulk-loads position rows with `pg_restore --data-only` (`record_backup.py:1723-1733`), bypassing `_journal_row` entirely, which is why `_journal_row` is referenced **nowhere** in `record_backup.py` | `record_backup.py:1658`, `:1723-1733`; dump list at `:1167` | **B** | M3. A restore can therefore install position rows with no counter to match them — which is why B3 puts `record_cursor` in `THE_RECORD_TABLES` (the dump list **is** `tuple(THE_RECORD_TABLES)`), and why the restore must **refuse** when `record_cursor.seq < COALESCE(max(journal_event.seq), 0)` afterwards. A restore that forgets the counter returns a populated `journal_event` with the allocator at 1: every subsequent position collides and — with today's `ON CONFLICT DO NOTHING` — is swallowed. |
| D-21 ⚑ | **`journal_event` retention has no ruling anywhere.** `#3843`/`#3853` bind `analytics_events`; `#12762` says so explicitly | `#12762` body | **B** | The record table gets the journal's growth **in addition to** the journal, forever, with no decision on pruning it. `#9025`'s bound decision, whenever it lands, will be about the file only. |
| D-22 ⚑ | **The governing documents are not registered in `docs/00_index.md`**: `STORAGE-ARCHITECTURE.md` has no index row; `event-catalog.md` has **0** mentions; the `#7951` atomic-write plan has **0** | §A1.0 | **B** | A lane proving completeness from the index — as this mandate instructs — **cannot find the document that governs the subject**. This is the same class as the `#10114` lesson: a green check that answers a different question. |
| D-23 ⚑ | **`docs/00_index.md`'s `#5612` cell still presents the pre-`#11821` vector figures as the current cost** in its first clause, then corrects them ~300 characters later in the same cell (the cell is **one** 1,931-character markdown line, so a "lines later" distance was never measurable) | `docs/00_index.md:153` | **O** | Deferred to the doc's own errata discipline; noted so Stage D does not re-derive it as a new finding. |
| D-24 ⚑ | **The ordinal's validity rests on one invariant the code states in a comment rather than enforces: ONE journal file per graph, forever.** `record_writer.py:2408-2410`: *"\"One record one number by construction\" holds per JOURNAL FILE, while `journal_event`'s key is per `(org_id, graph_id, seq)`"* | `record_writer.py:2412`, `config.py:84` | **B** | ⛔ **This is NOT a new finding and the first draft was wrong to present it as one.** The paragraph continues (`:2412-2425`) to state the residual in full — *"a tenant with **TWO durable journals** … the second journal's rows are absorbed by `ON CONFLICT … DO NOTHING`"* — **and tracks it on `#9318` (OPEN)**, whose own text proposes journal identity in the key **"or a per-tenant ordinal allocator"**. So the fix A7.5 argues for is the second of the two fixes already named in the source. **What this map adds is only the reachable trigger**, because the issue is framed abstractly: the concrete way to get two journals is a **`TORTOISE_EVENT_LOG_BASE_DIR` change on a live deployment**, after which the same graph numbers from 1 while the record holds the old rows — silent, count-free, no degraded state. Nothing refuses it. |
| **D-25** | ⛔ **The ordinal is not merely unowned — on a live-size journal it is intermittently UNPROVABLE, and this is the single most important thing the first draft missed.** `EventLog.append` returns **`None`** rather than a position in two documented, **repeatable** windows: (a) a recount that outlasts a peer's 5 s `_FLOCK_TIMEOUT_S`, which stamps `_FLOCK_UNHEALTHY` for 30 s during which *"every append in that window returns `None`"* — *"the journal past roughly **50 MiB** can outlast a PEER's deadline … a **repeatable recount → degrade cycle**"* (`log.py:1503-1520`); and (b) a sidecar that cannot be written, where `_POSITION_CACHE_DEAD` means *"an append does NOT recount — it reports `None`"* (`log.py:1523-1530`) | `log.py:1503-1530`; `record_writer.py:2396-2407`; `record_projection.py:6402-6407`; `monitoring.py:1459-1466` | **B** | **A `None` position writes NO `journal_event` row** — `write_live_record(seq=None)` → `position=False` (`:2479`), *"Such a record writes no position row … the row arrives on a later replay"* — so the record's ordinal is not just unowned, it is **missing for a population of live writes**, and D-03 is right that nothing (`last_applied_seq`) can detect the gap meanwhile. It is **counted, not silent** (`tortoise_record_position_unproven_total`), which is what keeps it out of P0 severity for the product — but the lane's own D-09 quotes the live journal at **974 MB, ~19× the 50 MiB threshold the code names**, on a box where a 20 MiB recount measures ~1.95 s. **Tracker: `#9252` (OPEN)** — *"a sidecar that cannot be written makes every append recount the whole journal under the lock"*, a `#9190` follow-up whose stated fix is *"a checkpointed counter with a tail reader"*. ⛔ **This is also the strongest available evidence FOR A7.5**, and the first draft never argues it: the code's own remedy for the file's position problem is a counter that does not recount. **Falsifier:** a peer append during a recount on a large journal must still yield a `journal_event.seq`. |
| **D-26** | **A record can exist for a write whose graph node never landed — and ⚠️ NOT, as an earlier revision of this row said, "a journal record with no record row".** `sdk._emit_event("SessionRecorded", …)` runs **before** the `:Session` MERGE (`sdk.py:4085` vs `:4154`) and `_emit_event` writes the journal row **and** the record row from the returned position, so `#9390`'s orphan carries both: what is missing is the **graph node**. It is therefore the A7.5.10 row *below* it, not the "journal without record" one, and **the flip does not close it** — the remedy is the fold change `sdk.py:4078-4082` names (tell a LANDED record from an orphan, or make the two one commit point). *"a MERGE that aborts after its bounded retries leaves an orphan record … a journal-only rebuild then restores the attribution of the attempt that never wrote a row"* | `STORAGE-ARCHITECTURE.md:1672-1698` | **B** | **`#9390` (OPEN)** — *"replay attribution skew: an orphan `SessionRecorded` from an aborted `:Session` MERGE wins the coalesce fold"*. So the journal is **not** a strict prefix of the record's committed set in either direction: D-25 is a record write with **no journal position**; D-26 is a **record whose graph node never landed** (the reverse of what an earlier revision of this row said — see the definition cell). **Any design that makes one store the total order must say which of the two it resolves**, and A7.5's window table assumed away the second. |

**30 rows: 19 block, 9 orthogonal, 1 must be retired, 1 carrying two fates.** The tally is counted from the
class column and **the first draft's `24 / 13 / 8 / 3` did not match its own table** (the true count was 14
block and 2 retire then, and the follow-on sentence named three must-retire rows when only two existed —
`D-02`'s string coupling and `D-19`'s premise; `D-19` counts **once** as `R` here even though its allocator
half is also `B`, which is stated in its own row). **Two rows were added after the Stage-A review** (`D-25`,
`D-26`), each of which is the *inverse* of a case A7.5 assumed away (D-25: no position; D-26: no graph node), so the shape's difficulty went **up** on
re-reading, not down. **Four more were added by the Stage-B review** — `D-27` (B7: the status carrier),
`D-28` (M1/M2: the journal must never shrink), `D-29` (M3: the restore is a second writer) and `D-30` (M4:
`capture_spool`) — and the same thing happened again: `D-27` converts a *retention* question into a
*correctness* one, and `D-30` adds a durable surface nobody had counted. The must-retire rows are each a
*premise* or a *string coupling*, not a code path — which is itself the finding: **almost nothing in this
shape has to be deleted. What must change is who owns one number, how many doors there are, and which
surfaces are allowed to answer a read.**

---

## A7 — The ideal shape, stated against the map

### A7.1 The shape, in one paragraph

**One write, one ordinal owner, one authority per question.** A mutation commits **once**, in Postgres, in a
single transaction that inserts the layer rows **and** the `journal_event` position row; the ordinal is
allocated **inside that transaction** by a per-graph counter row, so allocation order *is* commit order; the
cache is materialised from the record (never from the file); the JSONL becomes an **optional export** that a
lane without a record writes and no lane reads on the write path; **`:GraphEvent` keeps its own positions.**

⛔ **That last clause is a correction, and it is B6.** An earlier revision said `:GraphEvent` *"stops minting
its own positions"*. It cannot, and the two statements cannot both be built, because **`journal_event.seq` and
`:GraphEvent.seq` are not two names for one coordinate — they are two coordinates**, and the feed cursor every
client holds is the second one:

| the coordinate | minted by | what holds it | can A7 re-point it? |
|---|---|---|---|
| **`:GraphEvent.seq`** (the delivery/audit stream's position) | `event_store.next_seq` (`:427`, `MERGE` at `:491`) over `:GraphEventMeta.last_seq` | **every issued feed cursor** — `_encode_cursor` is literally `{"v": 1, "seq": N}` (`sdk.py:13436-13443`), `next_cursor` is the last event's seq (`:13589`), every stored `webhook_subscription.cursor`, every parked `poll_events` caller | **NO.** `#12458`'s BK3: re-pointing them *"silently re-points live cursors at a different coordinate system"* — a migration of every stored cursor is a **separate decision** this lane does not make and the `#9190` ruling does not authorise |
| **`journal_event.seq`** (the record's position, the idempotency key) | `EventLog.append` today; **the `record_cursor` row** under A7.5 | nothing outside the record and the export line | **that is the whole of A7.5** — and it changes no issued cursor, because nothing was ever issued from it |

**Two facts carry the correction, and both are structural rather than stylistic:**

1. **`:GraphEvent`'s domain is bounded by an allocator that refuses, and journal positions are not subject to
   that bound.** `MAX_SEQ = 2**53 - 1` (`event_store.py:381`) exists because FalkorDB compares numeric ranges
   in double precision, so `read_after`'s `WHERE e.seq > $after` **silently drops** rows above `2**53`
   (`durability-posture.md:264-470`); `next_seq` refuses past it (`:471-500`, the `ValueError` at `:509-520`).
   A journal position has no such ceiling and no such refusal — so making the two coordinates one would make
   the *feed* inherit a bound it did not have, or the *journal* inherit a refusal it does not need.
2. **`first_seq` is maintained BY PURGES**, and that is what makes a cursor's expiry legible.
   `:GraphEventMeta.first_seq` is the truthful floor: `poll_events` answers 410 below it (`sdk.py:13582`), and
   `first_seq` is deliberately **not carried across a rebuild** so no cursor survives one. If journal
   positions were served through that field, a **purge gap** and a **lost position** would be the same
   observation — and `journal_event` has no purge of its own to advance the field correctly anyway.

**So `:GraphEvent` keeps keeping its positions.** A7 changes *who owns the record's ordinal*, and §A7.5 is the
only place the coordinate changes. Anything else — D-07's second allocator, the `:GraphEvent` mirror, the
cursor encoding — is **out of this design**, and §A7.7 therefore **removes D-07 from the sequence entirely**
(`next_seq` stays, every issued cursor stays valid; D-07 waits for its own reopen). ⛔ **An earlier draft of
this paragraph said the opposite — that step 4 *keeps* D-07 "with no cursor migration (the values do not change,
only who mints them)" — and the round-2 review of the revision caught the two sentences standing side by side.**
That phrasing is the condemned instruction: "only who mints them" is adoption, and adoption breaks every stored
cursor. The sentence is recorded rather than deleted, because **this is the second time in this document that
the adoption argument has been written down and had to be withdrawn** — a reader who finds it anywhere else in
the plan should treat it as wrong.

### A7.2 What the map contradicts in the hypothesis

The issue's hypothesis has five clauses. The map **supports three, narrows one, and contradicts one**. The
contradicted one is the crux.

| hypothesis clause | verdict | the map says |
|---|---|---|
| "One atomic write — the record insert and the projection land in the same transaction (or an idempotent outbox)" | **SUPPORTED, and mostly already built** | The record **is** an outbox in the only sense that matters here: `journal_event` is written in the same transaction as the layer rows (`record_projection.replay_plan` emits both from one plan; the live writer does the same). What is missing is *routing* (D-01), not a mechanism. §3:342 already names the outbox form as the answer. |
| "The graph stays derived — unchanged" | **SUPPORTED** | §2.0/§3/SEARCH §3, all read. Nothing in this lane touches it. |
| "The JSONL becomes an OPTIONAL export … on hosted it stops being on the write path" | **SUPPORTED, with a condition the hypothesis does not state** | On self-host there is **no record** (`#8526` parked it). So the JSONL cannot become "optional" *everywhere*; it becomes **the lane-specific primary** on self-host and embedded, and an export on hosted. The hypothesis's phrasing implies one shape; the matrix (A3) says two. |
| "Fly disk leaves the critical path" | **SUPPORTED** | Follows from the above once the record is armed on every hosted graph — including the case §A3's first row names where **`TORTOISE_EVENT_LOG_BASE_DIR` is unset and the hosted path journals nothing at all**. |
| **"The record owns the ordinal. `next_seq`-style allocation moves to the record so a position exists without a file."** | ⛔ **REOPENED AND REVERSED BY THE OWNER (`#9190` comment `6902871297`) — the record allocates, the journal carries, replay reads.** The `nextval()` half of the clause is still rejected by name, and for a reason the reopen keeps | `#9190` decided the opposite and rejected `MAX(seq)+1` by name (`log.py:835-838`); the **reopen ruling now authorises the counter row** on the argument that the collision it feared came from a *fabricated* number, not a *stored* one. The second claim in the clause is **false as written**: a `next_seq`-style allocation does **not** give you *"a position without a file"* — it gives you a position **without a commit order**. PostgreSQL sequences are deliberately nontransactional: `nextval()` advances immediately, is not rolled back, and concurrent callers do not wait for earlier transactions to commit, so "a consumer that sees 11 may wait forever for 10". ⚠️ **And the record schema does already carry a sequence** — `public.relation.id` is *"bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY"* (`20261012000003:167`), whose separate sequence ACL is a documented hazard of its own (`20261025000001:205-212`: without the grant every `relation` INSERT raises `42501` and *"the whole edge-bearing record is lost"*). A bare sequence is the **worse** choice, not the better one — it would replace a number that is correct-by-construction with a number that is correct-by-luck. **This is why the ruling's mechanism is a counter row and not a sequence, and the distinction survives the reversal.** |
| *(implied)* "the ordinal is the crux" | **NARROWED** | The crux is not the ordinal; it is **the arming dependency** — the record is only written where a journal record already exists to hand it (§A2.1, §A3 row 2). Fix that and the ordinal follows; fix the ordinal and the dependency remains. |

### A7.3 The two other recorded positions, and whether this recommendation touches them

| recorded position | touched? |
|---|---|
| `durability-posture.md:48` — the JSONL *"is never the durability mechanism"*; authority lives in a store-managed artifact | **Not reversed. Unchanged.** A7.1 keeps durability = the store's backup. The record-allocated ordinal does not make the file durable and does not make the record one — it makes the *record* the ordering authority, which is a fourth question (§6). |
| `STORAGE-ARCHITECTURE.md:316` — `derived = replay(journal) NOT recompute(sources)` | **Not reversed. Its subject changes, and that is the reopen A7 argues.** If the ordinal is carried in the record, the invariant becomes `derived = replay(record)` on the hosted lane — **the same invariant with a different source of the same records**. On the no-record lanes it is unchanged. §3:285 already scopes the journal to *rebuild*, not durability; A7 keeps it scoped to rebuild and changes only *which physical copy rebuild reads from*. |
| The graph-is-a-derived-copy decision (`SEARCH-ARCHITECTURE.md:157`, `STORAGE-ARCHITECTURE.md:116`) | **Untouched.** |
| ⚠️ `docs/INGEST_CONTRACT.md:463-467` — *"the default embedded mode has no AOF window, so the JSONL journal is the durability contract"* | **Untouched by A7, and it is a third unqualified sentence of the same class as `STORAGE-ARCHITECTURE.md:361` (§6).** It is a **contract-level durability claim for the embedded lane** and it says the journal *is* durability where `durability-posture.md:48` says it never is. Recorded here rather than edited: it is a doc change with its own review, and the embedded lane is the one lane A7 does **not** touch. |

### A7.4 The "three stores" framing is an undercount, and that changes the target

The issue says "no app-written file as a **third** store". The live `create_point` path touches **six**
statements across **four stores and three counters**, in this order (`2026-10-14-7951:§3` + `sdk.py`):

1. `:EpMeta.ep_version` — a graph counter (MERGE, unjournalled, declared `#9123`)
2. `:Source.canonicalUrl` / `.urlAliases` — an adopt-on-touch write
3. `:GraphEvent` row **+** `:GraphEventMeta.last_seq` — the delivery store **and its own counter**
4. the JSONL append — **the commit point** (`#4240`, `#7951`)
5. `public.journal_event` + the layer rows
6. the FalkorDB cache `CREATE`/`MERGE`

⛔ **The count that matters is not six statements — it is how many of them are TRUTH-BEARING durable
surfaces, and that number is FIVE today and SIX once `capture_spool` is included (M4).** The two counts are
reconciled by one rule, stated because otherwise "five" and "six" are just two opinions:

> **Counted surface** = a durable store that the application writes **outside the record transaction** and
> that **cannot be rebuilt from the record**. The cache is *not* counted: it is the derived copy, rebuilt by
> replay, and `SEARCH-ARCHITECTURE.md:157` says so. Statements 1–5 are counted; statement 6 is not.

| # | truth-bearing surface | written by | rebuildable from the record? |
|---|---|---|---|
| 1 | `:EpMeta.ep_version` | `SDK._advance_ep_version` | **NO** — `#5166`: reset by a rebuild, and preserving it *"would pin a stale epoch"* (`#9123`) |
| 2 | `:Source.canonicalUrl` / `.urlAliases` | the adopt-on-touch write | partially — `source_identity --backfill` is the only repair |
| 3 | `:GraphEvent` + `:GraphEventMeta` | `_emit_event` **before** the append (D-04) | **NO** — `#4664`: the rebuild does not replay `:GraphEvent` |
| 4 | the JSONL journal | `EventLog.append` | **it is the rebuild source on the no-record lanes** |
| 5 | `public.journal_event` + the layer rows | the record write | it *is* the record |
| **6** | **`capture_spool` (`+ capture_backlog`)** | the capture client, `O_APPEND` + `fsync`, **outside any transaction** (`capture_spool.py:2207-2213`) | **NO** — a client-side WAL of user content with an owner ruling that it is kept until the user deletes it (`#3870`) |

**So "one atomic write" is really "collapse six statements across four stores and three counters into one
transaction, and declare the two that must stay outside it" — plus a sixth truth-bearing surface that this
transaction never touches at all.** The two that must stay outside are named by `#7951` residual 5 and are not
optional: `:EpMeta` and the `:Source` adopt-on-touch both write values **into the payload**, so they must
precede it. **Any plan that promises a single transaction without naming those two is promising something the
code cannot do.** And `capture_spool` is the surface that proves the counting rule earns its keep: it is
app-written, it is durable, it is user content, and **nothing in this design can rebuild it** — so it must be
either ruled in (A7.5's arming table) or explicitly ruled out, and an earlier revision did neither (it did not
mention the file at all).

### A7.5 The mechanism — the reopen is RULED, so this section states the mechanism, not the argument

⛔ **Ground: the owner has ruled** (`#9190` comment `6902871297`, 2026-11-15): *"replay READS the ordinal"*, the
record allocates, the journal record carries it. **This section therefore implements a ruling on paper.** What
the ruling authorises is **exactly one** thing — *the record allocates the record's ordinal and the journal
record carries it* — and §A7.8/§A7.6 are written to stay inside that boundary.

⛔ **THE RULING NAMES THREE THINGS IT DOES NOT DECIDE, and this boundary listed two of them under the word "Two"
for two rounds** (found by the review of this revision; the ruling's own words are in comment `6902871297`). All
three are listed here, each mapped to what in this plan touches it — that mapping is the point, because a boundary
list that is *nearly* complete is what lets a plan act on the missing item without noticing:

1. ⛔ **Whether journal history is BOUNDED** (the ruling: *"Whether journal history is bounded (`#9025`, open)"*).
   **This plan touches it via B7** — and does not act on it: §A7.5.12 and §A7.7 step 7 bound the **file** only, and
   D-27 shows a *table* bound is a **resurrection** bug, so it is refused here rather than decided. A table bound
   needs its own issue.
2. ⛔ **WHAT A `journal_event` ROW CONTAINS beyond the ordinal** (the ruling: *"what a journal record *contains*
   beyond the ordinal"*). **This plan DOES act on it** — §A7.8 item 6 (M15) widens the row with `event_id`,
   `initiated_by`, `projection_version` and requires `node_label`/`node_key` to be populated. **So that widening is
   a separate decision and must be taken on its own issue before §A7.7 step 6.1**; it is **not** authorised by this
   ruling, and this plan must not ship it on the strength of the ordinal ruling alone.
3. ⛔ **The graph's role** (the ruling: *"the graph's role (it stays a derived structural copy — unchanged)"*).
   **This plan does not reach it** — nothing in §A7 changes what a graph *is* — and it says so here rather than
   leaving it unmentioned, because an unmentioned exclusion is indistinguishable from one the plan has decided.

⛔ **And one fence that is this plan's own, not the ruling's:** the ruling authorises the **ordinal** to move. It
does **not** authorise `:GraphEvent` to change coordinate (B6) — §A7.1 keeps `:GraphEvent`'s **own** positions, the
record ordinal is a **second, independent** coordinate, and no issued cursor moves. Stated separately because it is
where a reader would most plausibly infer more than was ruled.
**If a revision step ever needs any of the three, that is a NEW REOPEN** — argued on its own issue, not taken here.

#### A7.5.1 Why a counter row and not a sequence — and what the row lock actually buys

```sql
-- public.record_cursor(org_id, graph_id, seq, mode, updated_at) ; one row per graph
-- ⛔ ILLUSTRATIVE OF THE INCREMENT'S TRANSACTIONALITY ONLY — §A7.5.2 is the allocator. Copied verbatim without
--    `mode = 'allocating'`, this statement allocates on a `halted` AND on a `seeded` graph, which would undo
--    rung 0 (§A7.5.11), §A7.5.12's mode table and falsifier 17(b); copied without the divergence guard it silently
--    repairs the `cursor < max` that §A7.5.2 raises on (B5).
UPDATE public.record_cursor SET seq = seq + 1, updated_at = now()
 WHERE org_id = %s AND graph_id = %s
   AND mode = 'allocating'                                   -- a halted/seeded graph must match no row
   AND seq >= COALESCE((SELECT max(seq) FROM public.journal_event
                         WHERE org_id = %s AND graph_id = %s), 0)   -- SILENT on divergence: matches no
                         --   row and raises nothing; §A7.5.2's rowcount
                         --   assertion is what makes it loud
RETURNING seq;
```

The increment is **transactional**: a rollback restores the counter, so a position is never burned and never
reused-after-being-seen — the case a `nextval()` cannot do (a sequence is not rolled back, and PostgreSQL
documents that it cannot provide gapless numbering at all; §9.2 cites the page). ⛔ **But the sharper fact —
and it is what B4 turns on — is that the row lock is held until COMMIT, so the guarantee is stronger than
"allocation order":**

> **A second transaction cannot allocate *any* position on that graph until the first has committed or rolled
> back.** Its `UPDATE` blocks on the row. So *allocation order = commit order* holds **by lock**, not by
> convention — and a `seq` sequence therefore has **no holes from rollbacks**, only from purges.

⛔ **And the first draft's cost argument was a false equivalence, which the review removed and the ruling
records.** It said the record allocator is *"a lateral move in concurrency, not a new bottleneck"* because
*"the journal append is already serialised per graph"* (`log.py:594-620`). Three things are wrong with that:
(a) **scope** — `flock` covers the seal read-modify-write plus **one `write(2)`** and is released before the
API, record and cache legs; the row lock is held until **COMMIT**, across the six statements and four stores
A7.4 enumerates. (b) **it is not even a guarantee today** — `_FLOCK_TIMEOUT_S` is **5 s** and on timeout *"the
append PROCEEDS on the single-write `O_APPEND` guarantee"* (`log.py:625-633`), so the journal deliberately
**abandons** per-graph serialisation under a wedged peer. (c) the honest statement is therefore the opposite of
reassuring: **the row lock buys the guarantee precisely because it holds longer than the thing being compared
to it.** One hot row per graph, held across a Postgres transaction, is a real new bottleneck and is quoted as
one.

⚠️ **Not a novel proposal, and presenting it as one would be the lane's worst failure.** `#7950`'s capstone
measured exactly this premise as **S1 — *"every write goes to the truth store first; the cache is projected
from it"* — verdict `⛔ NOT-GREEN`**, with *"an **armed** journal-backed projection refuses `apply` but lets
`proj.g.query` write the cache directly (0 journal records)"*, open on `#7951`
(`docs/evidence/7950-capstone/README.md:38`). A7's "ideal" is **the un-landed S1 of an existing epic**; what
this map adds is the ordinal mechanism. The same capstone's **S2 is also `NOT-GREEN`** and for a reason that
*is* this document's crux: *"**no schedule drives either verb yet**, so divergence is observed only when a
human runs the sweep"* — **divergence observation is unarmed**, the same arming-dependency class A7.2 names,
measured rather than argued.

#### A7.5.2 The allocation and the insert are ONE statement (M9), and a batch takes a BLOCK (M8)

`replay_plan` is a **pure statement list** built before execution, and `RecordWrite.first_seq/last_seq` plus the
byte meter all key off it — so an ordinal allocated *before* the plan is built cannot be known. The mechanism
is a **data-modifying CTE**, which needs no plan-build-time value: the ordinal is computed *by the statement*
and handed to the INSERT inside it.

```sql
-- one statement: allocate, then insert the position row with the allocated number
-- ⚠️ PSEUDOCODE for the payload columns (`…` is the record's own field set); the CTE shape is the mechanism.
WITH floor AS (
    SELECT COALESCE(max(seq), 0) AS m
      FROM public.journal_event
     WHERE org_id = $1 AND graph_id = $2
), alloc AS (
    UPDATE public.record_cursor c
       SET seq = c.seq + $3,                                   -- $3 = N (the block size)
           updated_at = now()
     WHERE c.org_id = $1 AND c.graph_id = $2
       -- ⛔ MODE IS PART OF THE PREDICATE, not a note about it. A `seeded` or `halted` graph must
       --    match NO ROW and therefore raise. `halted` is the rollback state §A7.5.11 promises, and it
       --    is one of the three states §A7.7 6.5 distinguishes **by mode** — the fallback removal there
       --    is keyed on `allocating` ONLY, so a `halted` graph keeps its fallback and loses only its
       --    allocator. "Stop allocating" therefore has to be a property of THIS statement. Without
       --    this term the `mode` column is inert AND the `elif mode != 'allocating'` branch of the
       --    assertion below is unreachable: the UPDATE already matched, so `rows_written == N` and the
       --    `!= N` guard never opens.
       AND c.mode = 'allocating'
       -- ⛔ DIVERGENCE IS LOUD, NOT REPAIRED HERE. If the counter is BEHIND the table, this
       --    statement must fail — not silently lift itself. `GREATEST` lives in the SEED only.
       AND c.seq >= (SELECT m FROM floor)
    RETURNING c.seq - $3 + 1 AS first_seq                    -- block = [first_seq, first_seq + N - 1]
                                                             -- ⛔ `+ 1` IS LOAD-BEARING. `RETURNING` evaluates
                                                             --   against the NEW row, so `c.seq` here is
                                                             --   `old + $3`; without the `+ 1` the first
                                                             --   position re-issues `old` — the number the
                                                             --   counter ALREADY handed out — and the first
                                                             --   live write after a healthy seed collides on
                                                             --   the `journal_event` PK. This is §A7.5.1's
                                                             --   `seq + 1 … RETURNING seq` in block form; the
                                                             --   two sections must agree on the arithmetic.
)
INSERT INTO public.journal_event (org_id, graph_id, seq, event_type, payload, recorded_at, node_label, node_key)
SELECT $1, $2, a.first_seq + i.ord - 1, … FROM alloc a, generate_series(1, $3) AS i(ord)
RETURNING seq;                                                -- block written; min/max taken by the caller
                                                             -- (an aggregate is NOT legal in INSERT … RETURNING —
                                                             --  wrap it: WITH ins AS (INSERT … RETURNING seq)
                                                             --  SELECT min(seq), max(seq) FROM ins)
```

⚠️ **The `RETURNING` line above is deliberately the plain column form**: a first draft of this section wrote
`RETURNING min(seq), max(seq)` on the INSERT itself, which PostgreSQL rejects (`aggregate functions are not
allowed in RETURNING`). The block's bounds come from `alloc.first_seq` — which the statement already knows — or
from a wrapping CTE, and the point is only that **they are returned, not predicted** (M9).

⛔ **The allocation must FAIL CLOSED — i.e. it must RAISE when it cannot allocate — and the first version of this
CTE failed OPEN, which is the worst shape in the design (found by the Stage-B round-2 review of this revision).**
If the `UPDATE` matches **no row**, `INSERT … SELECT … FROM alloc` inserts **zero rows and raises nothing**: the
write commits with no position row and no ordinal, in a design whose entire premise is that a position always
exists. Three ways to match no row, and two of them are ordinary: the graph was never seeded (or is `seeded`,
not yet `allocating`); a mistyped key; and — the one that will actually happen — **RLS with
`app.current_org_id` unset** (`ENABLE ROW LEVEL SECURITY` is already on the record tables,
`20261012000001:128`, and the app role is `NOBYPASSRLS`, `20261025000001:114`; §A7.8 item 2 adds `FORCE` for
this new table), where `current_setting('app.current_org_id', true)` returns NULL, the policy
matches nothing, and the UPDATE silently touches zero rows (§A7.8 item 2's policy is exactly that predicate).
**So the statement is followed by an assertion, and it is part of the mechanism, not of the test — and the
assertion must NAME which of the three it hit**, because "refused" and "diverged" and "not armed" send the
operator to three different places (§A7.5.5, §A7.5.11, and nothing at all):

```text
rows_written = <the INSERT's rowcount>
if rows_written != N:
    -- ⛔ THE DIAGNOSTIC RUNS WITH app.current_org_id SET — there is no owner-role escape: `FORCE`
    --    subjects the owner to the policy and `record_projection.py:6085-6093` refuses the record-table
    --    owner outright. Without the GUC the policy that made the write fail also filters the probe,
    --    and arm 1 lies.
    if no record_cursor row for (org, graph):        RAISE  "not cut over — no counter row"
    elif mode != 'allocating':                       RAISE  "mode=<mode> — not the allocator"
    elif seq < COALESCE(max(journal_event.seq), 0):  RAISE  "DIVERGED — counter behind the table; see A7.5.11"
    else:                                            RAISE  "allocation matched no row (RLS GUC unset?)"
```

⛔ **And the diagnostic queries above must NOT run under the same unset GUC the write failed under — this is
what makes the fourth arm reachable at all** (found by the review of this revision). §A7.8 item 2's policy is
`USING (org_id = current_setting('app.current_org_id', true))`, so with the GUC unset the probe `SELECT` is
filtered by **that same predicate**: the row exists but is invisible, the probe reads "absent", and **arm 1
fires** — telling the operator *"not cut over — no counter row"* in the one failure this paragraph calls *"the
one that will actually happen"*, when the counter row is right there and the real fault is the GUC. The `else`
arm and falsifier 17(c) would then describe a message no run can produce. So the diagnostic is specified as
running **with `app.current_org_id` set for the check itself** — *not* "as the owner role", which `FORCE`
specifically denies (§A8.3.4) — and falsifier 17(c) asserts the **message**, not merely that something raised.

⛔ **And the `UPDATE` carries a `mode = 'allocating'` predicate, which the first version omitted — that omission
made the `mode` column inert and made falsifier 17(b) red against this very SQL** (round-2 review of the
revision). A `seeded` or `halted` graph must **match no row and therefore raise**, not silently allocate: that
is what makes "halted" a state a rollback can actually enter (§A7.5.11) and what makes 6.3's transition
meaningful (§A7.7). **It is also what makes the second arm above reachable**, which it was not without it.

§A7.6 falsifier 17 is its executable form, and the RLS-GUC-unset shape is one of its three cases.

- **The allocation is `cursor + N`, and divergence with the table is a REFUSAL** (this is the correction that
  makes B5's P0 arm able to fire at all). An earlier draft wrote `GREATEST(cursor, max(seq)) + N` **here**, which
  is self-defeating: a graph whose counter is behind the table would have been **silently repaired by its next
  write**, so the reconciliation and its refusal arm could never observe the condition they exist for — and B2's
  recommended repair rung would have been the default path rather than a repair. **`GREATEST` therefore lives
  ONLY in the seed (§A7.5.5)**, where the whole point is to establish a floor; on the hot path, `cursor <
  max(seq)` raises, and the operator decides (B2).
- **Block allocation** (M8) is `+ $3` with `RETURNING seq - $3 + 1`: a batch of N records takes the **contiguous**
  range `[first_seq, first_seq+N-1]` in **one** statement, which is what `replay_plan`'s one-contiguous-segment
  requirement and `tools/backfill_copy_turns.py:969-977`'s non-contiguity refusal both demand. **The
  idempotency-key consequence is the one that must be stated**, because it is where block allocation usually
  goes wrong: the block is only ever **half-used** if the transaction dies before using all N — and a
  *transactional* counter **rolls back**, so no block is burned. If a future variant ever allocates a block in
  one transaction and *uses* it in another (a hi-lo cache), the unused tail becomes **holes in an idempotency
  key** — which this design refuses by construction, and §A7.6 falsifier 10 asserts.
- **`RETURNING` threaded back** is what replaces plan-build-time knowledge: `RecordWrite` carries
  `first_seq`/`last_seq` from the executed statement, so the byte meter and the export both read the number the
  database chose.

#### A7.5.3 The four preconditions — stated, not assumed (M5)

The guarantee above is conditional, and every condition is checkable at arming time:

| precondition | today | what the design does |
|---|---|---|
| **Not autocommit** | ✅ true — `record_writer.py:987-996`: *"`autocommit` is deliberately NOT touched: the transaction boundary belongs to the caller"* | assert it; an autocommit connection makes each statement its own transaction and the lock meaningless |
| **READ COMMITTED** | not checked anywhere | **REFUSE to arm** when `current_setting('transaction_isolation')` is not `read committed`. Under RR/SERIALIZABLE a blocked counter update raises **`40001`**, and **no retry exists anywhere on this path** — neither `record_writer.py` nor `record_projection.py` handles `40001`/`40P01`/`57014`, and nothing refuses a DSN carrying `-c default_transaction_isolation=serializable`. A guard is cheaper than a retry loop and fails loudly; a retry loop would be a second thing to get right. |
| **The counter is the LAST lock taken** | n/a | the order is: layer-row locks → **counter row** → commit. Taking the counter first would hold it across every layer write and lengthen the serialised window; taking it last makes the window one statement. |
| **Sole allocator in its coordinate space** | ❌ false today — see M6 below | the replay path must take the **same** row lock before inserting position rows, so live and replay serialise on one row rather than on two different things. |

#### A7.5.4 The second allocator, and the refusal that replaces absorption (M6 + B5)

`replay_plan(position=True)` still inserts `(org_id, graph_id, seq)` from `enumerate(records, start=start_seq)`
(`record_projection.py:6541-6545`) — a **second allocator writing the same coordinate**, and `#9187`'s
`start_seq` seam exists only to let a tail replay continue the enumeration. Under the ruling, the rule becomes:

> **Replay prefers the CARRIED number and REFUSES on `carried != start_seq + i`.** `absent ⇒ enumerate` is
> permitted **only** for a journal with no counter (a not-cut-over graph), which is the same absence-means-
> today's-behaviour rule the live path uses.

And **absorption stops being a policy**:

- **live path:** the `INSERT` **drops `ON CONFLICT (org_id, graph_id, seq) DO NOTHING`**. A duplicate position
  becomes a **`23505` that fails the transaction** — loud, attributed, counted. The conflict clause has been
  the mechanism by which a stale counter was *silently* converted into data loss (§A7.5.5); removing it is the
  fix, and its cost is stated plainly: **a genuine duplicate write now errors instead of being swallowed**,
  which is the whole point.
- **replay path:** keeps its conflict guard — ⛔ **but a numeric comparison is NOT a guard, and the first version
  of this bullet was wrong (found by the round-2 review of this revision).** `carried != start_seq + i` compares
  **numbers only**, so "same position, different record" is invisible to it — and it is **unobservable in the
  table**, because `journal_event` has no `event_id` (`ENVELOPE_KEYS` drops it, §A7.5.8). A shorter journal that
  re-enumerates from 1 onto occupied numbers (D-28) therefore passes the numeric check and its retained
  `ON CONFLICT DO NOTHING` **absorbs a different record**. So the replay's conflict becomes a **refusal unless
  the existing row's `event_id` matches**, which makes **§A7.8 item 6 (the envelope widening) a PRECONDITION of
  this rule, not a separate nicety of §A7.5.8** — a dependency the first version left unstated.

  ⛔ **And there is a NULL rule, which the first version of this bullet did not have and which would have made
  the guard refuse all of history (round-2 review of this revision):** `_journal_row` never inserted `event_id`,
  so **every row written before item 6 has `event_id IS NULL`.** "Refuses unless it matches" would therefore
  refuse every historical row and break both the `#9187` tail replay and §A7.6 falsifier 4. The rule is:

  | the existing row's `event_id` | what the guard does |
  |---|---|
  | **non-NULL and equal** | proceed — same record at the same position |
  | **non-NULL and different** | **REFUSE** — same position, different record. This is the D-28 case |
  | **NULL** (pre-widening) | **proceed, and COUNT it** (`tortoise_record_position_unidentified_total`). The comparison is not available, and "unknown" must not read as "different" — a guard that fails closed over history is a guard that cannot be rolled out. The backfill is §A7.8 item 6's second half: join the journal line at that `seq` and populate the column, after which the counter must be **zero** for the graph to claim the strict guard. |

- Replay also takes `SELECT … FOR UPDATE` on the `record_cursor` row, so the two paths cannot allocate
  concurrently (§A7.5.3's fourth precondition).
- **`_JOURNAL_INSERT_PREFIX` (D-02)** must stop recognising the statement by SQL string prefix in the same
  change — a `RETURNING` addition alters the prefix, which is exactly the silent breakage D-02 predicts.

#### A7.5.5 The seed — the rule, as executable SQL (B1)

⛔ **This is the defect that would have shipped.** Seeding from `MAX(journal_event.seq)` alone is **silently
wrong for a population the map proves non-empty**: the record DSN is presence-based and OFF by default
(`record_writer.py:263-266`), and a `None` position writes **no row** (`record_projection.py:6402-6407`, D-25).
So a graph with a large journal and **zero** `journal_event` rows seeds `NULL → 0`; its next write takes `1`
while that line's replay index is `N+1`, and `ON CONFLICT DO NOTHING` absorbs the loser **permanently**,
because the table is append-only by trigger. **Enumeration is rejected as a SOURCE; it is required as a
FLOOR.**

```sql
-- THE SEED. $3 = the enumerated floor: the number of records a REPLAY of this graph's journal would read,
--             computed by the replay's OWN reader (read_all_segments -> read_for_replay), so it is
--             SEGMENT-AWARE and cannot disagree with replay about what the journal contains.
BEGIN;
  SET LOCAL statement_timeout = '30s';
  SELECT set_config('app.current_org_id', $1, true);   -- ⛔ FIRST READ-BEARING STATEMENT (the `SET LOCAL` above
                                                      --    touches no table): transaction-local, exactly as the
                                                      --    record's own doors install it. Both reads below are
                                                      --    RLS-protected, and there is NO owner-role escape
                                                      --    (§A7.8 item 2's FORCE + `record_projection.py:6085-6093`)

  -- (0) refuse if this graph is already cut over — and note the exact predicate: a row in mode
  --     'allocating' or 'halted' is cut over; a row in 'seeded' is NOT, and MUST be re-seedable, because
  --     §A7.7 6.3's remedy for a graph that resumed writing before the cutover is "re-run 6.2".
  SELECT mode FROM public.record_cursor WHERE org_id = $1 AND graph_id = $2;
  --     mode IN ('allocating','halted') → ROLLBACK + REFUSE (never re-seed a live/halted counter)
  --     mode = 'seeded'                → permitted: recompute both floors and re-seed below
  --     no row                          → the normal first-cutover case

  -- (1) the record's floor
  SELECT COALESCE(max(seq), 0) FROM public.journal_event WHERE org_id = $1 AND graph_id = $2;  -- $record_floor

  -- (1b) NAME THE REFUSAL. Five cases, and the refusal must say which one — same rule as §A7.5.2's
  --      rowcount assertion.
  --      ⛔ THE SEED ADMITS THREE CASES AND REFUSES TWO — and the split INSIDE `record_floor < $3` is
  --      not arbitrary. It is whether any carried number exists:
  --
  --        $record_floor >  $3     CASE 1   the record holds positions this journal cannot reproduce
  --                                        (replaced / truncated / re-segmented; D-24/D-28).
  --                                        → REFUSE.
  --        $3 = 0 AND floor = 0   CASE 0    **an EMPTY graph — created, never written.** There is no
  --                                        floor to establish: seed `seq = 0` ("nothing allocated"),
  --                                        which is why §A7.8 item 1's CHECK is `seq >= 0` and not
  --                                        `> 0`, and the first write then takes `1`. Not a lift, not
  --                                        a refusal — the only honest value is zero.
  --        $record_floor =  0     CASE 2a  **NOTHING IS CARRIED.** The record has no position row for
  --          (and $3 > 0)                   this graph, so there is no carried number for a different
  --                                        journal to collide with — the "mix two journals into one
  --                                        coordinate space" hazard needs carried numbers to mix.
  --                                        → **LIFT to $3.** ⛔ This is B1's MOTIVATING POPULATION
  --                                        (the large journal with ZERO `journal_event` rows, D-25)
  --                                        and it is what falsifier 7 asserts. Refusing it would
  --                                        make the seed unable to do the one thing B1 claims.
  --        0 < floor <  $3        CASE 2b  **NUMBERS ARE CARRIED AND THE TWO READINGS ARE
  --                                        INDISTINGUISHABLE** — a D-25 tail, or a journal replaced by a
  --                                        LONGER one. The only evidence separating them is condition
  --                                        (iii) “the same journal identity”, which has NO per-row data
  --                                        source until `#9318` (D-24 is a guard; D-28 shows `stat`
  --                                        cannot supply it). **DEFERRED ⟹ REFUSE.**
  --        $record_floor =  $3     HEALTHY → proceed to (2).
  --
  --      ⛔ This predicate has been wrong twice, in opposite directions, and both were the SAME defect —
  --      a rule stated once and then applied to a case it was not about:
  --        v1 printed `WHERE $record_floor <= $3` — it LIFTED 2b on two of three conditions and committed,
  --           while five passages deferred it. Prose the artifact did not obey (§A8.3.1's class).
  --        v2 printed `WHERE $record_floor = $3` — it obeyed the deferral **and refused 2a as well**, which
  --           is the population B1 exists for, making falsifier 7's green path unreachable against this
  --           very SQL. Caught by the next review.
  --      v3's `WHERE $record_floor = 0 OR $record_floor = $3` admitted **case 0** (empty graph) into the
  --      lift branch, whose value is 0 — which a `CHECK (seq > 0)` rejected. ⛔ **The fix for that was NOT the
  --      `$3 > 0` qualifier below — a review caught this comment crediting it with a fix it does not perform:**
  --      `$record_floor = $3` already admits `(0,0)`, and the qualification accepts exactly the same population
  --      as v3. **The fix is §A7.8 item 1's `CHECK (seq >= 0)` plus case 0 having its own named outcome** — the
  --      qualifier is kept only so the SQL reads the same case list as the table, and it changes nothing.
  --      Stating a mechanism that does nothing when it runs is this document's own designated defect class, so
  --      it is named rather than left to look load-bearing.
  --      ⛔ AND THIS WHOLE TRANSACTION INSTALLS THE TENANT GUC AS ITS FIRST STATEMENT: the prologue is
  --      `SELECT set_config('app.current_org_id', $1, true);` — transaction-local, exactly as the record's own
  --      doors install it (`record_projection.replay()` and `cache_projection.read_record_view()`, per
  --      `20261025000001:41-46`). `record_cursor`'s read below is RLS-protected, so without the GUC it returns
  --      NOTHING and **`$record_floor` reads 0** — which misclassifies a 2b graph as 2a and would **lift** it.
  --      (⛔ `$3` is NOT affected: it comes from the replay's own JSONL reader, `read_all_segments ->
  --      read_for_replay`, not from SQL. An earlier form of this note claimed `$3` also read 0, which was false.)
  --      **What stops that being a silent loss is the policy's `WITH CHECK` half** (§A7.8 item 2): the unscoped
  --      INSERT raises `42501` instead of committing a lifted floor. The requirement is still the GUC — but the
  --      hazard it removes is a misclassification-to-lift, not a zero seed. ⛔ **There is no
  --      owner-role escape** — an earlier form of this note offered one, and it is denied twice over: `FORCE
  --      ROW LEVEL SECURITY` on `record_cursor` (§A7.8 item 2) subjects the owner to the policy, and the
  --      record's own connection guard refuses *"superuser, BYPASSRLS, a member of such a role, or the owner
  --      of a record table"* (`record_projection.py:6085-6093`). The GUC is the context; there is no
  --      substitute for it.

  -- (2) seed on the three admitted cases. The `WHERE` IS the decision: it is what refuses 2b, so deleting it
  --     seeds a longer replacement as if it were a legal tail (B1's silent-loss class). `GREATEST` remains
  --     the fail-safe below it.
  --     ⛔ The conflict arm is a scoped UPDATE, not `DO NOTHING`: step (0) promises a `seeded` row is
  --     re-seedable (6.3's remedy for a graph that resumed writing), and `DO NOTHING` writes nothing — so
  --     step (3) would read the STALE seq back and refuse, turning the documented remedy into a refusal.
  --     The scope is what makes it safe: `allocating` and `halted` can never be touched by a seed.
  INSERT INTO public.record_cursor (org_id, graph_id, seq, updated_at)
  SELECT $1, $2, GREATEST($record_floor, $3), now()
   WHERE $record_floor = $3 OR ($record_floor = 0 AND $3 > 0)
  ON CONFLICT (org_id, graph_id) DO UPDATE
     SET seq = EXCLUDED.seq, updated_at = now()
   WHERE record_cursor.mode = 'seeded'
  RETURNING seq;

  -- (3) READ BACK: the empty-RETURNING result is NOT self-describing (a concurrent/previous seed and a
  --     refused WHERE both return zero rows). The two causes must be told apart, or a silent no-op reads as a refusal.
  SELECT seq FROM public.record_cursor WHERE org_id = $1 AND graph_id = $2;   -- must equal $3; else ROLLBACK + REFUSE
COMMIT;
```

⚠️ **Why `GREATEST` is still written even though the `WHERE` makes it evaluate to `$3` in every admitted case** (both admitted forms — equality, and `0 < $3`)
(raised by the round-2 review of this revision, which called it dead code — it is not, quite): under the refusal
rule the expression can only return the journal floor, **so it is a fail-safe, not the decision.** Its job is
the case where a later editor keeps the expression and *drops the `WHERE`* — with `GREATEST` present that
mutation still cannot seed **below** the record's own floor, which is the one direction that would silently
re-issue live numbers. **The refusal, not the expression, is what makes the rule correct** — and §A7.6
falsifier 7 asserts the refusal arm specifically, so deleting the `WHERE` breaks a test rather than a graph.

⛔ **And the `record_floor < floor` branch was WRONG in the first version, which claimed a *replaced* journal
is always caught by case 1 (P1, round-2 review).** It is caught only when the replacement is **shorter**. A
journal that was **replaced by a longer one** — a restore of a newer backup, a mount that swapped roots, a
`TORTOISE_EVENT_LOG_BASE_DIR` change onto a journal with more records — produces `record_floor < floor`, which
is **the exact same signature as a legal D-25 tail**. The seed has no evidence that separates them, and the
first version's “heal the tail” would then mix two journals' records into one coordinate space. **Therefore the
D-25 heal is CONDITIONAL, and the condition is evidence the replacement cannot produce:**

| the tail must be | because |
|---|---|
| a **contiguous run at the END** of the journal — every missing position `record_floor+1 … floor` has a record, and every position at or below `record_floor` has a row | a replacement displaces the **whole** numbering, so its gaps are not a suffix |
| **positionless in the record**, i.e. the missing rows are genuinely absent, not occupied by other records | otherwise the tail is not missing, it is *different* |
| **under the same journal identity** the record's rows were written against (D-24 / `#9318`) | the identity check is the only thing that distinguishes “the tail grew” from “this is another journal” |

**If any of the three fails, treat it as case 1 and REFUSE.** §A7.6 falsifier 18 asserts the replacement case
specifically — the review is right that a seed which cannot tell a healed tail from a swapped journal is the
same silent-loss class as the bug it fixes.

⛔ **But condition (iii) has no data source today, and the round-2 review of this revision is right about that —
so case 2b REFUSES BY DEFAULT.** (i) and (ii) both hold for a **longer replacement**: its tail positions all have
records, and the missing rows really are absent. Everything therefore rests on (iii) *"the same journal
identity"* — and the record carries **no journal identity**: D-24 is a *guard* (a one-test change), not a per-row
identity, `#9318`'s identity-in-the-key is **unbuilt**, and §A6's own D-28 records that `st_dev`/`st_ino`/size/
`mtime_ns` **cannot** verify it. **An agreed-on condition that cannot be evaluated is not a condition, it is a
form of words** — so the honest rule is:

> **Case 2b (`0 < record_floor < enumerated_floor`) REFUSES unless an operator supplies the journal identity
> out of band — and until `#9318` lands, it always refuses.** The D-25 *tail* heal is therefore **deferred**,
> not implemented: those graphs stay uncut (and keep working on today's allocator), which is a **cost**, stated
> plainly, and much cheaper than mixing two journals into one coordinate space with no error anywhere.
> ⛔ **What is deferred is the TAIL heal — not the zero-row graph.** Case 2a (`record_floor = 0`) carries no
> numbers, so there is nothing for a different journal to collide with and the seed lifts it (this section's
> opening, falsifier 7). A review of this revision found a first form of the fix had scoped the deferral to
> *all* of `record_floor < enumerated_floor`, which **refused B1's own motivating population and made
> falsifier 7 unreachable against this very SQL** — the same defect as the one it fixed, one level out: a rule
> applied to a case it was not about.
> ⛔ **This is a reduction in what B1 claims, and it is the correct reduction:** the seed's job is to be right,
> and a lift it cannot justify is a lift it must not take.

**Five outcomes, and the operator action for each:**

| case | meaning | action |
|---|---|---|
| `record_floor = 0` and `enumerated_floor = 0` (**case 0**) | an **empty graph** — created, never written. There is no floor to establish. | ✅ **SEED `0`** — "nothing allocated" — and the first write takes `1`. This is the case that makes §A7.8 item 1's CHECK `seq >= 0` rather than `> 0`; with `> 0` the seed of a brand-new graph died on a raw constraint violation instead of one of its named outcomes (found by the review of this revision). |
| `record_floor > enumerated_floor` | the record holds positions the **current journal cannot reproduce** — a replaced, truncated or re-segmented journal (D-24/D-28). **REFUSE the cutover**; write no counter row. | **Do nothing else.** Absence of a `record_cursor` row *is* "not cut over", so the graph keeps working on today's allocator (the journal's own position), and the record keeps declining to carry a number. Then: restore the journal from a backup, or accept the record as authoritative via B2's repair ladder — **which is a decision, not a default.** |
| `record_floor = 0` (with `enumerated_floor > 0`) | **nothing is carried** — the record has no position row for this graph at all (D-25: `position=None` writes no row, `record_projection.py:6402-6407`). There is no carried number for a different journal to collide with, so the hazard the deferral exists for **cannot occur in this case**. | ✅ **LIFT — proceed.** This is B1's motivating population and falsifier 7's green path: the counter seeds to the journal floor, so **the next write takes `floor + 1`** — which holds only because §A7.5.2's block form returns `seq - $3 + 1` (see the `+ 1` note there; without it the first live write re-issues `floor` and collides). **Count it** (`tortoise_record_seed_lifted_total`) — a lift is a repaired D-25 window, not a routine event. |
| `0 < record_floor < enumerated_floor` | **numbers ARE carried and the two readings are indistinguishable** — a D-25 tail *and* a journal **replaced by a longer one** (see the three conditions above), with **no evidence in the record today that separates them**. | ⛔ **REFUSE — case 2b is DEFERRED, not implemented.** Condition (iii) ("the same journal identity") is the only thing that distinguishes the two readings and it **has no data source** until `#9318`, so all three conditions cannot be evaluated and the tail must not be lifted on two of three. **Do nothing else:** the graph stays uncut and keeps working on today's allocator, which is a **cost**, stated plainly, and cheaper than mixing two journals into one coordinate space with no error anywhere (§A8.2 item 9). ⛔ **This row said "LEGAL — proceed" in an earlier form while five other passages said "deferred"; the artifact followed the row and lifted. The row was the defect, and it is corrected here rather than only in the prose.** When `#9318` lands the lift returns with an evaluable (iii): heal the tail with a `start_seq`-bounded replay (`replay_plan(start_seq = record_floor + 1)`) and **count it** — a lift is a repaired D-25 window, never a routine event. |
| `record_floor == enumerated_floor` | the healthy case | proceed |

**Preconditions on the cutover, both already recorded elsewhere:** the graph is **QUIESCED**
(`durability-posture.md:403` imposes exactly this on a rebuild) — and ⛔ **for a reason, not as ritual: a
QUIESCED graph is what makes the two floors comparable at all**, because a live writer moves the journal
between the two reads. **A non-quiesced cutover is the sharpest silent failure in this section** — and ⛔ **it is
NOT falsified, because nothing can detect it: §A7.6 #19 was withdrawn precisely here** (the seed takes no lock a
journal writer contends), so quiescence is an **operator-asserted precondition** and §A8.2 item 7 is where that
gap is recorded. And **no second journal exists** for the graph (D-24 / `#9318`; the enumerated floor
comes from the segment-spanning reader, so a *sealed* journal is handled — and a *replaced* one is caught by
case 1, **or by nothing at all**: case 2b's three tail conditions cannot evaluate condition (iii) today, so it
**refuses by default** and this precondition is an assertion, not a check (§A7.5.5, §A8.2 item 9). *(Case 2a —
`record_floor = 0` — is lifted and needs no such precondition: nothing is carried, so nothing can be mixed.)*

#### A7.5.6 The arm: AFTER COMMIT for `allocating`, BEFORE COMMIT for `halted` — and the export re-serialises into seq order (B4)

⚠️ **And the drainer needs one rule it did not have:** for a `halted` graph it **drains the backlog as at the
halt — to the FROZEN `record_cursor.seq`, NOT to `max(journal_event.seq)` — and then stops.** The counter is frozen
by design, so `record_cursor.seq > last_exported` (the burned rule) is never true again, and a hole left by a halt
is neither "in flight" nor "burned" — §A7.5.6's hole table carries its own row for that shape.
⛔ **The target is the frozen counter and not the table's max, and the distinction is the whole rule:** on a halted
graph the **file leads** (PRE-COMMIT arm), so `max(journal_event.seq)` keeps **rising** as post-halt writes commit
rows — draining to it would append a second line for positions the file already carries, giving two records at one
`seq` and shifting the enumeration that is the halted graph's own position source (§A7.5.4 would then refuse on
`carried != start_seq + i`). **And after the halt the WRITER is the exporter**: on a halted graph
`EventLog.append` appends the line and advances `last_exported` with it; the drainer never appends again until a
re-cut.

⛔ **The export-lag AGE alarm is ARMED ONLY IN `mode = 'allocating'`** — not "mode-aware", which would leave the
scope to the implementer. On a `halted` graph, and on a `seeded` graph (which is every graph created after
cutover until §A7.7 6.6 ships, §A7.8 item 8), "no progress" is the **expected steady state**, so an alarm armed
there fires forever on a healthy system — the failure a mode check written as "suppress in `halted`" relocates
rather than removes. Falsifier 15(c) asserts both directions.

**There is no third arm, and the argument is short.** The journal line must (a) carry a number that only exists
once allocated, and (b) be visible to replay **only if the transaction committed**:

> ⛔ **THE ARM IS PER MODE, AND THIS SECTION STATES IT PER MODE (added because an earlier form stated it
> universally and three other sections then contradicted the rollback).** A graph in **`allocating`** writes the
> file **AFTER COMMIT** — the argument below. A graph in **`halted`** writes it **BEFORE COMMIT** (the file leads;
> `EventLog.append` writes the line and the drainer idles for it), because a halted graph's position is the file's
> own line count and the post-commit arm's lag would make the second write reuse the first's number. A graph in
> neither mode (`seeded`, or no row) is not allocating and has no arm of this section to satisfy. **One arm per
> graph, never neither and never both** — and the mode is the selector, exactly as it is for §A7.7 6.5's fallback
> removal and §A7.6 #12's refusal.

- **Before COMMIT, on an `allocating` graph:** a rollback leaves `seq = N` in a file that cannot roll back, and the
  next allocation takes `N` again — **two lines carrying the same number**, verbatim the `#9190` defect
  (`log.py:835-838`). ⛔ **`halted` is EXEMPT, and not by tolerance:** there the position is the file's own line
  count, which the written line **has already advanced** — so the next write takes `N+1`, not `N`, and the
  `#9190` defect this bullet describes cannot occur. The box above states the per-mode rule; this bullet is its
  argument for the `allocating` half only.
- **After COMMIT:** the file gains the line only when the row exists, so a rolled-back allocation leaves no
  trace. **Cost: file order need not equal seq order** — and that is the residual this section must answer.
- **A staging file** (write to `events.jsonl.tmp`, rename after commit) is **not a third arm**: replay reads the
  committed file, so the line becomes visible only after COMMIT. It is an implementation detail of arm 2.

**How the export re-serialises into seq order — and what it costs.** The exporter appends only the **contiguous
prefix**: it holds `last_exported` per graph and appends `last_exported+1` onward, reading rows from
`journal_event` (whose **presence is the commit marker** — the row is inserted in the same transaction as the
allocation). A committer that got `6` while `5` is still in flight does **not** append; it leaves its line to the
drainer, which appends it once `5` lands. Because the row lock already forces allocation order = commit order
(§A7.5.1), a hole in `journal_event` can only mean *in flight now* or *burned by a purge* — never "committed out
of order" — so the drainer's wait is bounded by one transaction, not by a timeout guess.

⛔ **State the price, because it is the price the review asked to see: the per-graph lock RETURNS on the export
path.** The write path gains one serialised row-lock window per graph; the **export** path gains a second
per-graph serialisation, weaker than the transaction's — a wedged exporter delays **export**, never a commit,
and the export is not the truth store. That asymmetry is the whole reason the arm is post-commit: **the thing
that must be ordered is the transaction; the thing that must be ordered second is the file, and only because
replay reads it.**

⛔ **And the drainer has a liveness hole that must be closed by instrumentation, not by hope — this is the
revision's own new silent-failure candidate, found by applying the reviewer's question to itself.** A hole in
`journal_event` fills when the transaction that owns it commits **or rolls back**; but a transaction that
neither commits nor rolls back (a hung writer, an abandoned connection holding the row lock) leaves the hole
open **forever**, and every later position for that graph then waits behind it. Nothing in the write path goes
red — the *commits* keep succeeding; only the **export** stalls, and an export that is behind is
indistinguishable from one that is idle. **Two guards are therefore part of the deliverable, not of the
runbook:** (a) the connection must carry `statement_timeout` **and** `idle_in_transaction_session_timeout`, so
the hole's owner is killed and the transaction rolls back (restoring the counter — no position burned); (b) the
export lag must be **armed** — a gauge `tortoise_journal_export_lag_seq{graph}` and an alarm on **its age**, not
its value, because the failure mode is
"no progress", which no threshold on the value detects. §A7.6 falsifier 15 asserts the alarm fires; an unarmed
gauge here would be the same class of defect as the capstone's S2 ("no schedule drives either verb yet").

⛔ **And a hole that is BURNED — not in flight — must be distinguishable, or the drainer blocks on it forever
(P1, round-2 review).** "A hole can only mean in flight or burned by a purge" was the first version's claim, and
it left out **the burns this design itself creates**: B2 rung 1 (a counter bumped past the table) and rung 2 (a
row deleted by an operator) both leave a number with no row and **no transaction that will ever fill it**. The
drainer must therefore have a rule for a provably-burned number, and it is a **statement about the record, not a
timeout**:

| the hole at `last_exported + 1` | what the drainer does |
|---|---|
| **a row exists** | append it, advance, continue |
| **no row, and a live transaction holds the counter row** | wait (bounded by the transaction, and by (a) above) |
| **no row, no holder, and `record_cursor.seq > last_exported` (the counter has moved past it)** | **BURNED** → advance `last_exported` past it, **count it** (`tortoise_journal_export_burned_total`), and continue. This is the same fact the reconciliation uses, so the two cannot disagree. |
| **no row, no holder, and `record_cursor.seq == last_exported`** | **NOT a drainer case — this row exists to say so.** ⛔ An earlier form of this table claimed a fourth row for it and that row was **unreachable**, because on a halted graph the WRITER appends the line **and advances `last_exported` with it** (above): after a successful export the file's last line sits **at** `last_exported`, and when the record leg rolls back what is missing is a **`journal_event` row**, not a file line — so the drainer never looks at this position and cannot stall on it. **A halted rolled-back write is a hole in the RECORD, healed by a later replay's enumeration (§A7.5.10 row 4), not a hole in the export.** Row 3 covers the export hole; row 2 covers the in-flight one; nothing else exists |

⚠️ **What the drained file looks like with a burned number: the export is not contiguous, and `#2488`'s
enumeration assumption no longer describes it.** That is the honest consequence of a repair rung, and it is why
rung 1 is *labelled* as a divergence (§A7.5.11) rather than as a clean fix.

#### A7.5.7 Fail-hard on the ordinal path, and the breaker becomes fail-fast (M10)

Today `write_live_record` **must not raise** (`record_writer.py:2304-2309`) and a failed connect silences the
door for **60 s** (`LIVE_WRITE_BREAKER_SECONDS`, `:353`) — so a write in that window has **no ordinal
anywhere**: the journal export would have nothing to carry, which under this design is a *lost position*, not a
degraded record. **The posture therefore flips, and it is a direct consequence of the ruling rather than a
choice available inside it:**

> **On the ordinal path, a record failure FAILS THE WRITE.** The breaker no longer silences the door; it turns
> every write in its window into a **fast failure**. A record outage becomes a **write outage** — which is what
> "the record is the commit point" means, and it is the honest reading of `durability-posture.md`'s own Q1
> answer: if the truth store is unreachable you cannot accept a write and be truthful about it.

⚠️ **Priced, not hidden:** this reverses `record_writer.py:78-101`'s **decision 3** and the live door's
`OVERRIDES` (whose precondition is *"it runs after the caller's journal append has already committed"* — a
precondition that **evaporates** when the record becomes the commit point: a record failure would now fail a
write whose other legs landed, which is exactly what the `OVERRIDES` existed to avoid). That is **M7's first
reversed decision**, and it needs its own recorded ruling on `record_writer.py`'s decision list — see §A7.5.9.
The alternative arm — declare the 60 s breaker window a **named bounded degraded state** in which writes are
accepted without an ordinal — is stated and **rejected**, because a position that exists for most writes and not
others is precisely the D-25 class this design exists to end.

#### A7.5.8 The export must be REPRODUCIBLE from the record, and today it is not (M15)

⛔ **"The export is repaired by re-rendering from the record" is not achievable at this head**, and the review is
right to stop it here. `_journal_row` inserts only `(org_id, graph_id, seq, event_type, payload, recorded_at)`
and filters `payload` through `ENVELOPE_KEYS` (`record_projection.py:493-505`), so:

| what a JSONL line needs | is it in `journal_event`? |
|---|---|
| `type` | ✅ as the `event_type` **column** |
| `ts` | ✅ as `recorded_at` |
| `seq` | ✅ as the key |
| **`event_id`** | ❌ **dropped by the envelope filter** |
| **`initiated_by`** | ❌ dropped |
| **`projection_version`** | ❌ dropped — and a JSONL line needs it |
| **`node_label` / `node_key`** | ❌ **columns exist (`20261012000001:86-87`), are never inserted**, so `idx_journal_event_node` is **empty** |

⚠️ **A correction to the finding's own wording, checked at head:** it lists `type` and `ts` among the dropped
keys. They are **not dropped** — they are *moved into columns*. The finding's **conclusion is unaffected and in
fact sharper**: the three genuinely dropped keys are exactly the three a replay needs, plus two empty columns.

**Two arms, and this revision takes the first:**

1. **Widen in this change** (chosen): add the three envelope keys as columns, or stop filtering them out of
   `payload`, **and populate `node_label`/`node_key`** so the node index is not empty. Consequences that must be
   declared, not discovered: the row's bytes grow (the byte meter `#12762`'s `journal_event_bytes_for_rows` will
   move — a **cost** line for the owner, not a rounding error), and the index population changes for the first
   time. §A7.8 lists all four as migration/writer deliverables and §A7.6 falsifier 11 asserts the round trip.
2. **Declare only the layer rows reproducible** (rejected): it prices the loss as *"the export cannot be
   re-rendered, so a lost JSONL is a permanently lost export on the record-armed lane"* — which contradicts
   A7.1's whole claim that the file is a **derivable export**. Rejected for that reason, and the rejection is
   recorded so a later lane does not rediscover it as a saving.

#### A7.5.9 The two recorded decisions this reverses (M7) — both are REOPENS, named

| # | the decision, and where it lives | why the flip reverses it | the route |
|---|---|---|---|
| **M7-a** | `record_writer.py:78-101` — **decision 3** (*"Armed but unreachable ⇒ the write FAILS"*) **plus** the LIVE door's `OVERRIDES` line, whose stated precondition is that it *"runs after the caller's journal append has already committed"* | the flip makes the **record** the commit point, so that precondition no longer holds and the `OVERRIDES` has nothing to override. The decision's *spirit* (a record failure must be loud) **survives**; its *scope* (which legs may have landed first) inverts. | a decision-list amendment on `record_writer.py`'s own header, authored as a **reopen** with this evidence. ⛔ **Not** taken by a comment in this plan, and **not** implied by the `#9190` ruling, which is about the ordinal. |
| **M7-b** | `record_writer.py:116-123` — **decision 5** (*"The position watermark ⇒ the journal's own offset, owned by the CALLER"*, with the rationale *"The seam does not mint or cache a counter … A re-run after a crash re-derives the same offset, so nothing renumbers"*) | `record_cursor` **is** a counter that the seam now mints. The sentence is not merely stale — its **central claim** (*"nothing renumbers"*) is the property being replaced, and the ruling replaces it deliberately: nothing renumbers because **the number is stored**, not because it is re-derived. | the same reopen, second clause. §A4's `#2477` note ("what this lane adds is not the number, it is the *owner*") is the same edit from the other side. |

⚠️ **And these are not the only two sentences that become false — they are the two that are DECISIONS.** Five
descriptive statements also become false of a new file and must be qualified rather than reversed (§A7.5.12):
`#2488`'s *"the JSONL carries no seq"*, `log.py:828-845`'s *"THE SOURCE IS THE JOURNAL ITSELF"*,
`event-catalog.md`'s `seq` row, **`durability-posture.md:405`'s *"because the journal carries no `seq` its
number is lost too"***, **and the shipped schema's own comment** — `COMMENT ON TABLE public.journal_event`
(`20261012000001:94`) says *"applying a seq twice is a **no-op**"*, which §A7.5.4's live path **falsifies** by
dropping `ON CONFLICT DO NOTHING` so a duplicate position becomes a `23505` (B5) — which sits in the very paragraph this plan cites for the QUIESCED precondition, so that
requirement now survives for this plan's own reason (a live writer moves the journal between the two floor
reads) rather than the seq-less-file reason stated there. The distinction matters: **a decision needs a reopen; a description needs an
addendum.** Conflating them is how a description gets treated as a ruling (or a ruling gets quietly edited as if
it were a description).

#### A7.5.10 What happens to an in-flight write across that change

| window | today | after |
|---|---|---|
| ordinal allocated, transaction rolls back (**`allocating`**) | n/a (the file's index cannot be taken back, but a torn tail is sealed and shifts no index) | **the counter rolls back too** — no gap, no burned position, and the *next* allocation takes the same number. ⛔ **Scoped to `allocating`, because on a `halted` graph the statement is false and the next row's scoping forces the admission:** there is no counter allocation on the write path — the position is the file's line count (§A7.5.6's PRE-COMMIT arm) — so a rolled-back write leaves a line at `N+1` with **no** row, the next write appends `N+2`, and the number is **not** reused. That is a real gap, and it is healed by a later replay's enumeration (§A7.5.10 row 4), not by this invariant. §A7.5.6's hole table gained the matching fourth row for it |
| record commits, journal export fails | n/a | **the record is authoritative**; the export is re-rendered from the record **once M15's widening lands** (§A7.5.8) — before it, this row is a fiction and is labelled as one |
| record commits, cache write fails | replay heals (`#7951` T5) | unchanged — replay heals |
| journal export commits, record fails | ⛔ **it DOES happen today, and trivially: the record leg is a second, fallible step after the append.** `_emit_event` does `log.append(event)` and only then `_write_record_projection(event, seq=seq)` (`sdk.py:14062`), counting failures in `record_write_failures` and healing them on a later replay — so an append whose projection fails leaves a journal record with no row. ⚠️ It is **not** `#9390`, whose orphan carries *both* rows (the append runs before the `:Session` MERGE, and the record row is written from the returned position) — see the next row | **cannot happen by construction ON AN `allocating` GRAPH** — the file gains a line only after COMMIT, so a journal record with no record row has nothing to be a copy of. ⛔ **Scoped to `allocating`, because a `halted` graph uses the pre-commit arm (§A7.5.6): there the append precedes the record leg exactly as it does today, so this row's shape IS reachable on a halted graph and is healed by a later replay's enumeration, not by this invariant.** |
| ⛔ **the reverse: a journal record for a write that never landed** | **it DOES happen today** — an aborted `:Session` MERGE leaves an orphan `SessionRecorded` that wins the coalesce fold (`#9390`, OPEN; `STORAGE-ARCHITECTURE.md:1672-1698`) | **unchanged by this design** — the flip does not address it, and this table must not claim the journal is a strict prefix |
| ⛔ **a live write whose position is UNPROVABLE** (`EventLog.append` → `None`) | **it DOES happen today** — a recount past a peer's 5 s deadline, or a sidecar that cannot be written (D-25, `#9252`, OPEN) | **fixed on the record-armed lane**: the counter is the position, so no recount is on the path |
| ⛔ **pre-transaction `:EpMeta` / `:Source` adopt-on-touch land, then the record rolls back** | **it happens today** — both are written *before* the append (A7.4) | **still happens**: an advanced EP epoch and an adopted `canonicalUrl` for a mutation that never committed. Nothing reconciles it; `#5166` and `source_identity --backfill` are the only repairs. This is the last paragraph of A7.4 stated as a row. |
| ⛔ **the record is unreachable for 60 s and the breaker silences the door** | the write lands with **no ordinal anywhere** (D-25's twin) | **the write FAILS** (§A7.5.7) — there is no window in which a write commits without a position |

#### A7.5.11 The rollback: stop allocating, keep the carried numbers, never return a graph to enumeration (B2)

⛔ **The rule, unconditional.** A revert of the *write* path (stop allocating) is safe. A revert of the *read*
path (replay stops reading the carried number and enumerates again) is **not**, and it cannot be repaired after
the fact: `journal_event_is_append_only` refuses UPDATE **always**, and DELETE only when
`current_setting('app.record_journal_purge', true) = 'on'` (`20261012000001:96-113`), whose one production path
sets it and then deletes the whole **org** (`20261012000005:22-35`).

| the shape of a rollback | safe? |
|---|---|
| **stop allocating** (new graphs seed from the journal's own position; per-graph counters left in place, unused) | **YES** — this is the intended rollback, and it costs nothing on a graph whose enumeration and counter never diverged |
| **return a graph to enumeration** (replay stops reading `seq`) | **NO, ever** — for every line written in the new mode the enumeration floor and the carried `seq` disagree the moment the graph ever had a hole; replay would renumber committed rows |
| **per-graph exemption** (this graph may return to enumeration) | **only with proof**: every line's carried `seq` equals its 1-based position in the replay's own enumeration, checked over the **whole** journal, one query. **Absent that proof, carried-forever** — the exemption is not a judgement call. ⛔ **And the proof has a VALIDITY WINDOW, which the first version did not state (P1, round-2 review):** it is a statement about **one quiesced instant**, not a property the graph keeps. So the proof is taken **with the graph quiesced, at the moment of the revert**, and it is **refused while any allocator is live on that graph** (`SELECT … FOR UPDATE NOWAIT` on the counter row is the check — a held lock means someone is allocating). A proof taken an hour earlier is a **measurement artifact, not a state** — the same rule that stopped a healthy graph being rebuilt from a compaction summary, applied here to a rollback |

**The exact repair, as a ladder — each rung with what it costs:**

0. **Stop allocating for THIS graph — the `halted` write, and the ONLY writer of it.** This rung exists because
   §A7.5.12's transitions table claimed `allocating` → `halted` had a writer ("rung 2's bump") and **it did not**:
   rung 1 is a decision not to act, rung 2 never touches `mode`, and rung 3 installs the *dump's* mode. So the
   rollback state was entered by nothing, and under §A7.5.2's `AND c.mode = 'allocating'` predicate the allocator
   would have kept allocating through a rollback (found by the review of this revision):

   ```sql
   UPDATE public.record_cursor SET mode = 'halted', updated_at = now()
    WHERE org_id = $1 AND graph_id = $2 AND mode = 'allocating';
   ```

   **Guarded by the same quiescence assertion as the exemption proof** (§A8.2 item 7 — operator-asserted, not
   enforced), because halting a graph whose allocator is mid-transaction leaves the writer holding a counter
   nobody advances. **What it costs:** nothing on a graph whose enumeration and counter never diverged — the
   carried numbers stay readable (§A7.5.12) and the fallback stays installed (§A7.7 6.5 removes it for
   `allocating` only). ⛔ **This is the per-graph meaning of "stop allocating".** A code revert — the read path
   going back to enumeration for every graph — is a **different** action with a different risk, and §A7.5.12's
   "never return a graph to enumeration" line is about that one.

   ⛔ **AND A HALTED GRAPH'S APPEND ARM IS THE PRE-COMMIT ONE — stated here because leaving it implicit made the
   rollback unable to take two writes** (found by the review of this revision). The enumeration position is the
   file's line count (`log.py:840-845`: *"a record's position is its 1-based index among the records a REPLAY would
   read"*). Under §A7.5.6's AFTER-COMMIT export the file trails the table by the drainer's latency, so on a halted
   graph write #1 would take `N+1`, and write #2 would still read a file at `N`, compute `N+1` again, and raise
   `23505` on `journal_event`'s PK (§A7.5.4 drops `ON CONFLICT DO NOTHING` on the live path) — for a graph this rung
   promises keeps working, and **permanently** if the drainer is wedged, which §A7.5.6 itself contemplates. **So the
   halt restores the PRE-COMMIT arm for that graph: the file leads, `EventLog.append` writes the line, and the
   drainer idles for it.** §A7.5.6's AFTER-COMMIT arm is a property of the **`allocating`** mode, exactly as §A7.7
   6.5's fallback removal is — a graph in either mode gets exactly one arm, never neither and never both.
1. **Leave the bad row where it is, and keep the counter above it.** ⛔ **Restated by the round-2 review, which
   was right that the first version named neither the mechanism nor the cost it claimed.** With `cursor + N` as
   the allocator (§A7.5.2), *leaving* the counter above the table is what happens **by itself** — so this rung is
   a **decision not to act**, not an `UPDATE`: the bad row stays, positions stay contiguous, the counter keeps
   climbing, and the export does **not** stall. **What is lost:** that position permanently names the wrong
   record, so `derived = replay(record)` is approximate for the graph, and **the reconciliation reports it for as
   long as the row exists** — an *armed* divergence rather than a silent one. ⚠️ **Executed instead as a manual
   `SET seq = max(seq) + 1` it is a DIFFERENT and worse rung**: it burns `max+1`, leaving a hole the drainer must
   recognise as burned (§A7.5.6's third row) or it blocks there forever. **The first version wrote the manual
   bump and called its cost `cursor > max` forever — which does not follow, because the next write makes them
   equal. That claim is withdrawn.** This rung is **still recommended**, on the strength of the
   decision-not-to-act reading — ⛔ **and its applicability is narrower than "a bad row exists" (round-2
   review of this revision, which is right):** it is the rung for a **wrongly-NAMED row with a healthy
   counter**. For the other state — `cursor < max(seq)`, which the allocator now **refuses on** (§A7.5.2) —
   *"no action"* would mean **every write raises forever**, and the only action that clears it is a bump, i.e.
   the variant this section calls worse. **So rung 1 and the refusal arm must not be read together:** the
   refusal arm's remedy is rung 2 or 3 (or restoring the counter from the table, which is what rung 3's dump
   does), and §A8.2 records that no clean rung exists for it — that gap is the finding, not an omission.
2. **A documented operator DELETE** — `BEGIN; SET LOCAL app.record_journal_purge = 'on'; DELETE FROM
   public.journal_event WHERE org_id = $1 AND graph_id = $2 AND seq = $3; COMMIT;`
   ⛔ **Blast radius, stated exactly, because this is the GUC path and it is worse than it looks:**
   - the trigger's DELETE arm has **no WHERE guard** — `IF TG_OP = 'DELETE' AND
     current_setting('app.record_journal_purge', true) = 'on' THEN RETURN OLD` — so the escape is not scoped to
     an org, a graph or a row. **Any** position row in **any** org is deletable by any session that sets the GUC.
   - the **app role cannot do it**: `tortoise_record_app` holds `SELECT, INSERT` on `journal_event`
     (`20261025000001:190`). A DELETE needs the **owner/superuser** role — i.e. the repair needs a *stronger*
     credential than the application path, run by hand, with no audit row.
   - **the only backstop is a record dump, and no schedule takes one** (§A3's own durability row: *"record dump
     — unbounded today, no cron"*). So this rung's price is **the entire record since the last manual dump**,
     and a position row is the idempotency key for layer rows that are *not* deleted — deleting it does not
     delete the layer rows, it **re-arms** them for a future replay that would then apply them twice.
   **⇒ This rung is documented because B2 requires the exact repair to be named, and it is NOT recommended.**
3. **Restore the record from a dump to before the bad row.** Price: everything written since the dump; and it
   re-introduces D-29 (the restore installs rows *and* must install the counter, or refuse).

**Recreating the graph is not on this ladder, and saying why is part of the answer.** A rebuild replays
`journal_event`; if the positions are wrong, the rebuilt cache is derived from the wrong positions — a
recreation launder the error through a rebuild and reports success. (`rebuild_from_record` folds the journal, so
the mis-numbered row is still the input.)

⚠️ **What must be said about the file: the export is NOT repairable by rollback either.** A rolled-back mode
leaves lines in a file that carry `seq` values nothing reads; they are harmless (the enumeration ignores a
`seq` it does not use) — but only if the reader tolerates the *presence* of a field it does not consume, which
§A7.6 falsifier 6 asserts rather than assumes.

#### A7.5.12 The embedded / self-host lanes, which have no record

The allocator becomes a **declared mode**, not a replacement. ⛔ **The mode is the `record_cursor.mode` COLUMN
(`seeded` / `allocating` / `halted`), and row-absence means only "this graph was never cut over" — the first
version made row-presence the whole marker and that is the defect the round-2 review found (P1): "stop
allocating" keeps the row, so row-presence would have read a *halted* graph as an *allocating* one, and §A7.7
step 6.5 would then have removed its enumeration fallback, leaving it with neither.** The three states, and
what each means:

| `mode` | who allocates | the enumeration fallback |
|---|---|---|
| *(no row)* | `EventLog.append`, exactly as today | **present** — this is every no-record lane and every uncut graph |
| `seeded` | **nobody yet** — the 6.2→6.3 window; the graph must be quiesced, and §A7.7 6.3 is what changes this row | present |
| `allocating` | the `record_cursor` row | **removed only here** (§A7.7 6.5) |
| `halted` | **nobody** — the rollback state (§A7.5.11's "stop allocating") | present (a halted graph is exactly the graph whose carried numbers must keep being readable) |

⛔ **The transitions are the mechanism, and leaving them implicit was the round-2 review's P1.** A mode column
whose writers are unspecified is a column that drifts: `seeded` and `halted` both mean "nobody allocates", so a
writer that sets the wrong one silently moves a graph between two states with opposite remedies. **Six
transitions exist, each with exactly one writer — plus one WRITER THAT IS NOT A TRANSITION and sets `mode`
arbitrarily**, which is why it gets its own row below rather than being left out of the table:

| from → to | who writes it | when |
|---|---|---|
| *(no row)* → `allocating` | **the graph-creation hook** (§A7.8 item 8) — `INSERT … (seq = 0, mode = 'allocating')` in the same transaction that creates the `:Graph` | every graph created **after** the cutover. ⛔ **It has its own row rather than folding into the one below because it does not pass through `seeded`:** a brand-new graph has an empty journal, so there is no backfill to wait for and no quiescence to assert — going to `allocating` directly is the whole point, and routing it through `seeded` would leave it permanently non-allocating, because `seeded → allocating` has exactly one writer and that writer is the per-cutover step 6.3 |
| *(no row)* → `seeded` | **the seed SQL** (§A7.5.5), in the same transaction as the `INSERT … SELECT` — run by **one** caller: the one-off cutover | the cutover step 6.2. ⛔ **The second "when" was missing and a review found the consequence:** 6.2 is a per-graph backfill performed once, so without the creation-time caller **every graph created after cutover day never gets a counter row** and keeps running today's allocator — the D-25 `position=None` window, the 60 s fail-soft breaker and the two-journal-files hazard — forever, while §A7.5.7's fail-hard posture is scoped to "the ordinal path" and therefore never applies to it. **A cutover is a snapshot; it is not a policy.** |
| `seeded` → `allocating` | **the 6.3 arming step** — a single `UPDATE … SET mode='allocating' WHERE mode='seeded'`, guarded by the quiescence assertion | §A7.7 step 6.3, *after* the backfill, never before |
| `allocating` → `halted` | **rung 0** (§A7.5.11) — the `UPDATE … SET mode='halted' … AND mode='allocating'` printed there, guarded by the quiescence assertion | an operator-initiated rollback; **`halted` is never entered automatically**. ⛔ **This row said "rung 2's bump and rung 3's restore" and neither writes `mode`** — rung 1 is a decision not to act, rung 2 is a journal DELETE, rung 3 installs the dump's mode — so the state had no writer until rung 0 was added (found by the review of this revision). The "one writer" claim is now true per row — but the table was still **incomplete** until the restore row above was added (found by the next review) |
| **`* → *` (any mode, by restore)** | **`record-restore` / rung 3** — `pg_restore --data-only` writes the **dump's** `mode` for every restored graph | ⛔ **A restore into an EMPTY target only, and that is the whole of the reachable shape** (found by the review of this revision). The fifth writer is real — §A7.8 item 4 puts `record_cursor` in `THE_RECORD_TABLES`, that tuple **is** the dump's `--table` list (`record_backup.py:1167` → `record_tables()`), and `pg_restore --data-only` bulk-loads every column — **but the verb refuses a populated target outright** (`restore_record_backup` raises `RecordRestoreRefused` on any non-empty target, `record_backup.py:1698-1709`). So "a dump taken while a graph was `allocating`, restored onto a graph since halted" **cannot happen**, and the guard an earlier form of this row proposed ("compare the restored `allocating` against the live `halted`") had **no live `mode` to compare against**. In the shape the verb permits, the restored `mode` is consistent with the restored counter by construction and needs no guard. **The residual is a FUTURE one, stated so it is not re-derived: if a later change ever lifts the populated-target refusal, that change must carry the guard** — which is this item's own standard ("an unexamined purge is how a deliberate choice becomes an accident") applied to restore |
| `halted` → `allocating` (**the re-cut, completing**) | **the operator, explicitly, in ONE statement that both recomputes the floor and arms** — `UPDATE public.record_cursor SET mode='allocating', seq = $enumerated_floor, updated_at=now() WHERE org_id=$1 AND graph_id=$2 AND mode='halted' AND $record_floor = $enumerated_floor`, guarded by the quiescence assertion | ⛔ **This row replaces a routing that could not complete** (found by the review of this revision): the earlier form went `halted → seeded` and relied on `seeded → allocating` to finish the job — but `seeded` means *nobody allocates*, the only `seeded → allocating` writer is the **per-cutover** step 6.3, and this table's own text calls arming a graph whose numbers were consumed *"re-arming … how the same ordinal gets issued twice"*. A re-cut that stopped at `seeded` left the graph permanently on today's allocator; one that went through 6.3 armed an **unverified** counter, because 6.3 carries no floor check. **The re-cut is therefore ONE transition with ONE writer that recomputes and arms together, and it refuses unless the floors are equal** — the same healthy-case rule as §A7.5.5. If the floors differ the graph **stays `halted`** (no partial move, no `seeded` limbo) and the case-2b deferral applies. |
| `halted` → `seeded` (**abandoning the counter**) | **the operator, explicitly** — `UPDATE public.record_cursor SET mode='seeded' WHERE org_id=$1 AND graph_id=$2 AND mode='halted'`, guarded by the same quiescence assertion | ⛔ **NOT the re-cut** — this is the state for a graph whose counter is to be *discarded* and rebuilt from the journal, and it is only reachable when the operator intends to **re-run the whole cutover for that graph** (6.2 + 6.3). Reaching it without 6.3 leaves the graph in the pre-arm window, which is why the row above exists and why this one is not the re-cut's route. a halted graph is re-cut by a **fresh seed**, never re-armed: re-arming a graph whose carried numbers were already consumed is how the same ordinal gets issued twice. ⛔ **The transition that makes that possible must be named, because §A7.5.5 step (0) REFUSES `mode IN ('allocating','halted')`** and round 3's conflict arm is scoped `WHERE mode = 'seeded'` — so without this writer "re-cut by a fresh seed" named a call that refused, and `halted` was a **one-way door** in the section whose subject is a *reversible* rollback (found by the review of this revision). The quiescence guard is what keeps it a decision rather than a default: re-cutting a graph whose allocator is live is the thing §A7.5.12's "never re-armed" sentence is for.|

**`mode` is machine-readable on purpose** — §A7.7 step 6.5 gates the removal of the enumeration fallback on
`mode = 'allocating'`, so the classification is a column read, not a log-and-hope.

| lane | allocator | what carries the number | mode |
|---|---|---|---|
| hosted, record armed | the `record_cursor` row (per graph, in-transaction, held to COMMIT) | `journal_event.seq` **and** the journal export line (`seq` field, new) | `allocating` |
| self-host (no record) | `EventLog.append` position, **as today** | the journal's own read order — `enumerate` is correct here | **no row ⇒ not cut over** |
| embedded | no journal, no ordinal | nothing needs one | no row |
| **MCP BASE / stdio** | none — **journal-less by construction** (`mcp_auth.py:193-201`) | nothing | **provably never** (§A7.6 falsifier 5) |
| **M2 session-capture** | the `record_cursor` row once converted | the export line | `allocating`, at §A7.7 step 6.4 — its `durable=False` decline was *because* it had no position to hand over, which is the coupling the flip removes |
| mixed / migrating (**the window §A7.7 inserts**) | the journal carries `seq` **only when the record allocated it**; absent ⇒ enumerate | per-record dispatch | `seeded` → `allocating` |

**What that means for `#2488`'s "JSONL carries no seq".** It stays **true of today's file** and becomes false of
a *new* file: the sentence is scoped to the current format. Anyone quoting it to argue the ordinal cannot be
journalled is quoting a description, not a decision — which is why it needs an **addendum**, not a reopen
(§A7.5.9).

**And the bound (`#9025`) stays OUT.** Nothing in this design bounds the journal, and D-27 now shows why a bound
on `journal_event` is a resurrection bug rather than a cost decision. `#9025`'s file-only decision cannot be
extended to the table, and this section does not extend it.

### A7.6 The falsifiers — one per blocking answer, and the reviewer's question applied

Stage C needs these, so A7 states them rather than leaving Stage B to invent them. ⛔ **Every blocking answer
above had the reviewer's question applied to it — *"if this step is done wrong, does anything go red?"* — and a
blocking answer with no falsifier was treated as not answered.** The table's last column is what does NOT go red
before the falsifier exists, i.e. why the row is here rather than in a test file.

⛔ **There are nineteen LIVE falsifiers — and #19 is a hole, not a typo.** #19 ("the cutover must be refused on a
non-quiesced graph") was withdrawn by this revision's own review because the seed has no mechanism to detect
quiescence and no lock to take, so the test could only pass vacuously or fail against its own spec. **The number
is deliberately not reused:** a renumbered list would hide the withdrawal, and the withdrawn promise is now
§A8.2 item 7. Everything else below is live.

1. **Two journal files for one graph** must be **refused**, or the position must carry the journal's identity
   (D-24 / `#9318`). The reachable trigger is not exotic — **changing `TORTOISE_EVENT_LOG_BASE_DIR` on a live
   deployment** gives the graph a fresh journal that numbers from 1 while the record keeps the old rows, and
   `ON CONFLICT DO NOTHING` swallows every new position silently. **Now three triggers, not one** (M1): a
   `backup.py:638` restore replaces the file, and `TORTOISE_JOURNAL_SEGMENTS=1` + `seal_active` restarts the
   file-local index at 1. *Red today: no guard exists and no test covers it. Nothing goes red — that is the
   defect.*
2. **A rolled-back record transaction must not burn a position**: allocate in a rolled-back transaction, then
   assert the next committed record's `seq` is the successor. *A `nextval()` implementation goes red* — which is
   why this is the falsifier that distinguishes the two allocator designs.
3. **Two concurrent writers on one graph must produce commit-ordered positions**: interleave two transactions
   so the one that allocates first commits last, and assert the reader sees them in `seq` order with no missing
   row. *A bare-sequence implementation goes red.* ⚠️ **The test must actually block**: because the counter row
   lock is held to COMMIT, the second `UPDATE` blocks — a test that runs the two transactions sequentially
   passes against a **broken** implementation and is therefore not this falsifier.
4. **Replay must reproduce `journal_event.seq` exactly**, including after a tail-segment replay with `start_seq`
   (`#9187`) — the carried number wins over the enumeration, and a **mismatch must REFUSE** (`carried !=
   start_seq + i`), not absorb (M6).
5. **The no-record lanes must not require a `seq`**: self-host round-trip (`append` → `rebuild_from_log`) with
   no DSN, asserting no `journal_event` is attempted — **and the MCP BASE/stdio lane must be provably outside
   the ordinal** (§A3.1), since it has no journal to have a position in.
6. **A journal without `seq` in its records must still replay** (the migration arm) — the mixed-version
   falsifier `#12459` names for its own format change. **This is the falsifier for the window §A7.7 inserts.**
7. **B1 — the seed, on the population that breaks it.** A graph with a journal of `N` records and **zero**
   `journal_event` rows: seed it, write once, and assert the new row's `seq` is `N+1` **and** that a replay of
   the file reproduces `N+1` for that line. *Today: `seq = 1`, absorbed, silently.* **And the refusal arm:** seed
   a graph whose `max(journal_event.seq) > enumerated_floor` and assert the cutover **refuses and writes no
   counter row** — a seed that proceeds in that case is the same silent-loss bug wearing the fix's clothes.
8. **B2 — the rollback leaves the carried numbers readable.** Revert to enumeration for a graph whose lines all
   carry `seq` equal to their position, and assert replay still produces the same graph (the *exemption* path);
   then do it for a graph with a hole, and assert the rollback is **refused** rather than silently renumbering.
   ⚠️ **Nothing in the codebase distinguishes these two graphs today**, which is why the predicate is part of
   the deliverable and not of the test.
9. **B4 — the export is in seq order, not commit-arrival order.** Commit `5` and `6` such that `6`'s committer
   reaches the exporter first, then drain, and assert the file's order is `…, 5, 6`. *A "each writer appends
   its own line after COMMIT" implementation goes red on exactly this, and nothing else does.*
10. **M8 — a batch takes ONE contiguous block.** Write a batch of `N` records and assert `last_seq − first_seq +
    1 == N` and that `tools/backfill_copy_turns.py` accepts the resulting journal (it refuses non-contiguity at
    `:969-977`). **And a hi-lo variant must go red**: an implementation that allocates a block in one
    transaction and uses part of it in another leaves **holes in an idempotency key**, which this test asserts
    cannot happen.
11. **M15 — the export is reproducible.** Delete the JSONL file, re-render it from `journal_event`, and assert
    the replayed graph is **identical** — including `event_id`, `initiated_by`, `projection_version`, and a
    **non-empty `idx_journal_event_node`**. *Red today: three of those keys are filtered out by
    `ENVELOPE_KEYS` and the two node columns are never inserted, so the re-render produces a journal that
    cannot be replayed to the same graph — and it fails **quietly**, as a replay that yields less.*
12. **B5/B6 — reconciliation is an ARMED check, not a query somebody remembers to run.** Post-replay, assert
    `record_cursor.seq >= COALESCE(max(journal_event.seq), 0)`, with **`cursor < max` as the P0 arm that REFUSES the next
    write** (not merely logs). *A reconciliation that exists only as a verb is the S2 defect the capstone
    measured: "no schedule drives either verb yet".* ⛔ **AND THE REFUSAL IS SCOPED TO `mode = 'allocating'`** — the
    same scope §A7.7 6.5 uses for the fallback removal, and for the same reason. On a `halted` graph the counter is
    **frozen** by design while the fallback keeps writing (`write_live_record` passes `position=seq is not None`,
    `record_projection.py:6402-6409`, so the live path *does* insert `journal_event` rows), so after the **first**
    fallback write `cursor < max` — and an unscoped refusal would stop the very graph §A7.5.11's rollback promises
    to leave working. A `halted` graph is expected to diverge; an **`allocating`** graph diverging is the bug.
13. **M10 — the fail-soft path must not produce a positionless write.** Kill the record mid-write and assert the
    write **fails loudly**, with no journal line and no cache write — and specifically that the 60 s breaker
    window does not accept a write and skip the ordinal.
14. **M5 — a serializable DSN must be REFUSED at arming**, with the refusal naming the setting. *Red today:
    nothing reads `transaction_isolation`, and a DSN carrying `-c default_transaction_isolation=serializable`
    arms happily and then raises `40001` on the first contended allocation, where no retry exists.*
15. **A stalled export must be VISIBLE** (§A7.5.6). Hold a position's transaction open past every timeout and
    assert (a) it is killed by `idle_in_transaction_session_timeout` and its counter rolls back, and (b) the
    export-lag **age** alarm fires while later commits keep succeeding. *Red today: nothing measures export
    lag at all, and "stalled" is indistinguishable from "idle" — the S2 shape the capstone measured.*
    ⛔ **(c) THE ALARM IS ARMED ONLY IN `allocating`, ASSERTED IN BOTH DIRECTIONS IN THE SAME RUN.** On a `halted`
    graph and on a `seeded` graph the age alarm must **not** fire; on an `allocating` graph with an
    equivalent induced stall it must go **red**. *The paired leg is the point:* a one-sided test passes an
    implementation that suppresses the alarm everywhere, which is the failure the mode check exists to prevent.
    Without (c) the mode rule is unverifiable, and this section's own standard is that "a blocking answer with no
    falsifier was treated as not answered".*
16. **B6's own falsifier — because the round-1 one could not detect B6 (P1, round-2 review).** Capture a feed
    cursor (`poll_events` → `next_cursor`) **before** the change, replay the feed at the new head, and assert the
    captured cursor **still lands on the same `:GraphEvent` rows**. Falsifier 4 asserts that *replay reproduces
    `journal_event.seq`* — which is a statement about the **record's** coordinate and says nothing about the
    feed's. **A build that does the wrong reading (deleting `next_seq`, writing the journal's number into
    `:GraphEvent.seq`) ships with falsifier 4 green and every stored cursor dead.** This is §A8.1's B6 row's
    real falsifier, and its addition is what makes that row honest.
17. **The allocation must not fail open (P1, round-2 review).** Three cases, all asserting a **raised error and
    no commit**: (a) no `record_cursor` row; (b) `record_cursor.mode = 'halted'`; (c) **`app.current_org_id`
    unset under RLS** (`ENABLE` at `20261012000001:128` + the `NOBYPASSRLS` app role at `20261025000001:114`),
    where the UPDATE matches zero rows and — without §A7.5.2's
    rowcount assertion — `INSERT … SELECT … FROM alloc` writes **nothing and raises nothing**. ⛔ **And (c)
    asserts the MESSAGE, not merely the raise** (found by the review of this revision): with the GUC unset the
    probe `SELECT` on `record_cursor` is filtered by the same policy, so a probe run under that GUC reports
    the row as absent and yields arm 1's *"no counter row"* — the wrong diagnosis for the very case this
    paragraph calls the one that will actually happen. (c) therefore asserts the **GUC/RLS arm's** text, and
    it goes red against any implementation whose diagnostic runs under the failed context.
18. **A journal REPLACED BY A LONGER ONE must not be seeded as a D-25 tail (P1, round-2 review).** Two cases,
    because the first version tested only the vacuous one. **(a)** A graph whose counter row is in `seeded` mode
    (the only state in which the seed may re-run, §A7.5.5 step 0) and whose journal was replaced by a longer
    one: the re-seed must **REFUSE**, and refuse with the *tail-evidence* error, not with "already seeded".
    **(b)** A graph with **no** counter row (a first cutover — the only time the seed normally runs) whose
    journal is a longer replacement: **`0 < record_floor < enumerated_floor` ⇒ case 2b ⇒ REFUSES with the
    *deferred* reason**, no counter row written. ⛔ *This case asserted `record_floor > enumerated_floor` ⇒
    case 1, which is the **inverted** predicate: a replacement with MORE records gives a LARGER
    `enumerated_floor`, so (b) as written tested the case a shorter journal produces, not a longer one — and
    it would have gone green against a seed that lifts. A falsifier aimed at the wrong side of an inequality
    is the same defect as one attached to a finding it cannot detect.* *A falsifier that only exercises (b)
    proves nothing about (i)–(iii), since step 0 refuses before they are evaluated — which is exactly the
    defect the round-2 review found in the earlier wording.* **And (a) must carry numbers to be 2b at all:**
    a `seeded` graph with `record_floor = 0` is case 2a and is **lifted**, so (a) names a row that already
    holds positions — otherwise it asserts a refusal of the case the seed is supposed to heal.
19. **⛔ WITHDRAWN — "the cutover must be refused on a non-quiesced graph" was not implementable (round-2
    review, and the withdrawal is the honest outcome).** The seed takes no lock a journal writer contends
    (there is no per-graph record row before one is created, and the journal is a file), so "refuses" had no
    detection path and "takes a lock that blocks the writer" named a mechanism that exists nowhere in
    §A7.5.5. **A falsifier that must either go red against the spec or always pass is not a falsifier**, so it
    is removed rather than relaxed, and the precondition it was standing in for is promoted where it belongs:
    **QUIESCENCE IS AN OPERATOR-ASSERTED PRECONDITION, not an enforced one**, recorded as unresolved in §A8.2.
    The number is left as a hole so the next reader sees the withdrawal rather than a silent renumbering.
20. **B7 — a retention bound on `journal_event` must not resurrect an archived node (added because B7's
    assigned falsifier could not detect it).** Take a node whose terminal state was carried by an
    `EntityMutated op=restatus` row (or a creation record's birth `status`) — the two properties D-27 shows
    are **only** carried by `journal_event` — then delete a **PREFIX** of that graph's `journal_event` rows while
    the node's later rows remain, and rebuild from the record / serve `GET /v1/points/{id}`. Assert the node
    still reads **terminal**. ⛔ **Red TODAY, not "green by absence of the defect":** this procedure performs
    the deletion itself, and that deletion is executable now — §A7.5.11 rung 2 names the GUC path
    (`BEGIN; SET LOCAL app.record_journal_purge = 'on'; …`) and the append-only trigger's DELETE arm carries
    **no row guard** (`IF TG_OP = 'DELETE' AND current_setting('app.record_journal_purge', true) = 'on' THEN
    RETURN OLD`, `20261012000001:104-106`), so the **prefix** form `… AND seq <= $3` executes on the current
    schema. *(Rung 2 itself prints the single-row form `… AND seq = $3`; this falsifier needs the prefix form,
    so it derives the statement rather than quoting rung 2 verbatim — and it runs **as the owner role**, since
    the app role holds only `SELECT, INSERT` on `journal_event`, `20261025000001:190`.)* Once the
    terminal row is gone the fold loses it (`_LIFECYCLE_EVENTS_SQL` reads the terminal value only from
    `journal_event`, `cache_projection.py:2961-2973`, and the point read folds that stream,
    `record_serve.py:826-829`), so the node rebuilds with no status — which the code's own comment
    (`cache_projection.py:2947-2949`) says is read as **LIVE**. **So the defect is demonstrable before any bound
    exists, and the test must go red again, unchanged, against the bound's first implementation.** *(An earlier
    form of this row said the opposite — "green by absence of the defect" — and a false polarity claim is the
    one thing this section says is worse to get wrong than having no falsifier at all.)* And it must be red
    against the bound's first implementation, because `apply_event_stream_folds` skips every later fold once a
    node is terminal (`cache_projection.py:3364-3366`): drop the earlier terminal row and a LATER fold applies,
    so an archived object is answered **LIVE** — resurrection, not staleness. ⛔ **B7 previously cited #12 as its falsifier
    and that citation was wrong:** #12's predicate is `record_cursor.seq >= COALESCE(max(journal_event.seq), 0)`, and a
    **prefix deletion leaves `max(seq)` unchanged**, so the reconciliation stays green while the resurrection
    happens — a falsifier attached to a finding it cannot detect, the exact defect this section says is worse
    than none. §A7.8 item 7 does not help either: it rules on the **org purge**, and it rules **not extended**,
    so there is nothing there for a bound to exercise.

**Why this list is longer than the last one, and why that is the correct direction.** The round-1 list was six
falsifiers for a design with seven blocking defects — the count was the symptom. Every blocking answer now
carries a falsifier, and the rows added for `M1`/`M8`/`M15`/`M5`/export-lag and for the round-2 self-review's own ten
P0/P1 findings (§A8.3's table — the population, not a separate count) are there because those are **mechanism** findings whose failure mode is **silence**, not an
exception. ⛔ **Falsifier 16 exists because the round-2 review of this revision caught the revision using a
falsifier that could not detect the finding it was attached to — which is the same defect class the revision
exists to remove, one level up.** ⛔ **Falsifier 20 exists because a later review caught the SAME defect again,
one level further out: B7 cited #12 as its falsifier, and #12's predicate (`cursor >= max`) cannot see a PREFIX
deletion — so the finding this document rates as making `journal_event` retention a correctness question had,
for one round, a green test that could not go red.** Two rounds, two instances of one class: the class is not
rare, and it is invisible unless someone asks *"what does this assert go red on?"* of every row.

### A7.7 What Stage C must do first, in order — including the step that does not exist today

Derived from the debt map, smallest safe unit first — **not** from the ideal shape backwards. **Step 6 is new
and it is the sequencing change this revision owes (M6/M11): a reader-first cutover with a backfill and a
mixed-version window. Its coordinate is stated at the end of this section.**

1. **Reconcile D-01's two counts** (387/211/32 vs 442/38/288 vs this lane's 458/50) before anything is sized.
   One command, and it is the denominator for everything else.
2. **D-03** `last_applied_seq` — additive, no ordering change, and it is the instrument you need to verify
   every later step.
3. **D-24** the two-journal-files guard — a one-test change that makes the current ordinal's premise explicit.
   **(M1) Extend it to the two other shrink mutators** (`backup.py:638`, `seal_active`), or the guard protects
   one of three doors.
4. **⛔ D-07 is OUT of this design — and the round-1 sequence was wrong to include it (P0, round-2 review).**
   The earlier instruction read *"retire the `:GraphEventMeta` allocator in favour of the record's position … the
   record mints the value that goes into `:GraphEvent`"*, and **that cannot be built**. Either the record mints
   `:GraphEvent.seq` — which **is** adoption, i.e. B6 — or `next_seq` stays and D-07 is unfixed. "The values do
   not change, only who mints them" would require the new minter to reproduce `GraphEventMeta.last_seq`, which
   lives in **FalkorDB** and cannot be read inside the Postgres transaction that allocates. **So the step is
   deleted rather than reworded:** `next_seq` **stays**, every issued cursor stays valid, and D-07 remains its
   own ticket (`#12458`'s "part worth a ticket") awaiting **its own reopen** — one that would have to migrate
   every stored cursor (`webhook_subscription.cursor`, every parked `poll_events` caller) and would therefore be
   a *bigger* change than the one this plan proposes. ⛔ **§A8.2 item 5 was the honest signal: a step the plan
   could not state a mechanism for should not have been in the sequence at all.**
5. **D-05** (`#8699`) the fold's parity, which unblocks *"the cache driven from the record"*.
6. **⛔ NEW — the allocation cutover, reader-first, in six sub-steps.** This is the step that does not exist in
   the round-1 sequence, and it is what makes the allocator move a *migration* rather than a *flip*:
   1. **Readers ship first.** `replay_plan` learns to **prefer the carried `seq`** and to **refuse** on a
      mismatch (§A7.5.4); the journal reader learns to tolerate a line with a `seq` it does not need. With no
      writer emitting `seq` yet, this is a **no-op on data** and changes no number. ⚠️ **And §A7.8 item 6 (the
      envelope/node-column widening) is a PRECONDITION of this sub-step, not of 6.3** — the refusal cannot
      detect "same position, different record" until `event_id` is in the table (§A7.5.4). ⛔ **AND IT REQUIRES
      ITS OWN DECISION ON ITS OWN ISSUE FIRST (§A7.5, exclusion 2): the `#9190` ruling does not authorise changing
      what a `journal_event` row contains.** A gate that lives only in §A7.5 is a gate a lane reading THIS sequence
      will not see, and the thing it gates is a widening the ruling does not cover.
   2. **Backfill the floor.** Seed `record_cursor` per graph with §A7.5.5's statement, **on quiesced graphs
      only**, with the tail evidence check; **gate it on step 3's shrink guard** (D-24/D-28), because a shrink
      *inside this window* is exactly what the seed cannot see. Graphs that **refuse** are listed and left
      uncut — that list is the output of this sub-step, not an error.
   3. **The mixed-version window opens.** Writers begin emitting the `seq` field on the export line **and**
      continue writing the position row, **and the live writer starts allocating from `record_cursor`**. ⛔ **The
      round-1 wording of this sub-step was false and is corrected (P1, round-2 review):** between 6.2 and 6.3 the
      counter **exists** while the writer is still `EventLog.append` — **two writers over one coordinate**, with
      nothing comparing them, and the old absorbing conflict clause still in force. **So the exact statement is:**
      *from 6.2 until 6.3 the graph has a seeded counter and NO allocator using it* (a quiesced graph, which is
      the precondition for seeding at all — a graph that resumes writing before 6.3 must **re-run 6.2**), and
      **from 6.3 onward there is exactly one allocator per graph** (the row lock, §A7.5.1). "No graph is ever in a
      state where two writers disagree" holds **only in the second interval**; the first interval's safety rests
      on quiescence, which is a state that must be *asserted*, not assumed.
   4. **Cut the cache write over** behind the record (the S1 flip `#7951` already owns), and convert the M2
      session-capture lane off `durable=False` (M11) — its decline existed only because it had no position.
   5. **Close the window:** delete the enumeration fallback **for graphs in the `allocating` mode**, leaving it
      for `halted`, `seeded` and no-row graphs. ⛔ **Keyed on the MODE COLUMN, not on row existence (P1,
      round-2 review — this was the sharpest defect in the sequence).** With row-presence as the marker, 6.5
      would have **destroyed the rollback §A7.5.11 promises**: "stop allocating" leaves the row in place, so a
      rolled-back graph would read as *allocating-but-not-allocating* and have **no fallback and no allocator**.
      `record_cursor.mode` (§A7.8 item 1) therefore makes `allocating` / `halted` / `seeded` three distinguishable
      states, and 6.5 removes the fallback only for `allocating`.
   ⛔ **The coordinate this window reconciles on is `journal_event.seq`, and only that.** Not `:GraphEvent.seq`
   (B6 — a different coordinate, untouched), not a new field, not file order. One coordinate, two readers: the
   carried number wins where present, enumeration only where absent. **That is what makes the window safe to
   be long** — both readers agree about the *same* number for the *same* line.
   6. **Ship the creation hook (§A7.8 item 8) in the SAME change as 6.3 — not before, not after.** Every
      `:Graph` created from that moment gets `record_cursor(seq = 0, mode = 'allocating')` **through §A7.8 item 8's
      `provision_graph_record` RPC**, at both creation chokepoints. ⛔ **This step was missing and it is the one that decides whether the cutover is a project or a
      policy:** 6.1–6.5 migrate the graphs that exist on cutover day, and without 6.6 every graph created
      afterwards is born uncut, keeps today's allocator, and does so **forever**. It must land **with** 6.3
      because 6.3 is what makes the live writer allocate from `record_cursor` — a graph created in the window
      between them would allocate from a counter that does not exist. **Rollback: this is the LAST thing that may
      be reverted** — unshipping it silently returns every new graph to enumeration, which no alert covers.
   **Rollback of each sub-step:** 6.1 and 6.2 are reversible with no data change; 6.3 is reversible by *"stop
   allocating"* (§A7.5.11); **6.6 is shipped WITH 6.3 and is the LAST thing that may be reverted** — unshipping it
   silently returns every new graph to enumeration, which no alert covers; 6.4 is where the reopens (M7) must
   already be recorded; 6.5 is **not** reversible
   without proof per graph, and is therefore the last thing done.
7. **D-09** the bound — owner decision, and it should follow the shape change, not precede it: bounding the
   file while the file is still the commit point converts a cost problem into a data-loss problem
   (`STORAGE-ARCHITECTURE.md:442` says exactly this — the sentence is at `:442`, not at §5.2's heading `:427`; a
   later review found the pointer landing on the heading). ⛔ **And per B7 this step is the FILE only.** A bound on
   `journal_event` is a **different decision** (D-27: it is a primary status store), it is not covered by
   `#9025`, and it must be refused or ruled — never derived.

**Two steps the round-1 sequence was missing for reasons other than the window**, both from the review:

- **M12 — `tools/record_wiring_check.py` must be re-derived, not deleted, and it belongs at step 6.1**:
  `live_door_writes_the_journals_position` (`:372`) and `live_call_sites_pass_the_ordinal` (`:547`) assert
  **exactly the rule the change inverts**. A check deleted "because it now fails" removes the only instrument
  that can see a door which stopped writing its position. Its replacement asserts the *new* rule (the door hands
  the record a number the **record** minted) at the **same** strictness.
- **M11 — the six test suites Stage C must check** that pin the behaviour being replaced (four of which must move) are named in §A7.8's "suites that must
  move" table, because a plan that names two test files for a change of this size has not looked at what pins
  it.

### A7.8 The migration, as a deliverable list (B3) — and the "first updatable record table" rationale

⛔ **A restore silently restarts the allocator at zero unless every item in this list lands.** `THE_RECORD_TABLES`
is a hand-maintained tuple (`record_projection.py:404-419`) that drives **the dump** (`record_backup.py:1167`,
`tuple(THE_RECORD_TABLES)`), **the manifest column pin** (`:230-240`, parsed from the migrations), **the
pre-backup counts** (`:1038-1065`) and **the RLS posture check** (`:512`, `ROLE_POSTURE_SQL` over
`record_tables()`). A restore then returns a populated `journal_event` with the counter at 1 → every position
collides and (with today's clause) is swallowed.

| # | deliverable | why, and what breaks without it |
|---|---|---|
| 1 | **`public.record_cursor(org_id text, graph_id text, seq bigint NOT NULL, mode text NOT NULL DEFAULT 'seeded', updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (org_id, graph_id))`** with **`CHECK (seq >= 0)`** and **`CHECK (mode IN ('seeded','allocating','halted'))`** — ⛔ **and NO `REFERENCES public.graphs … ON DELETE CASCADE`, deliberately, unlike every sibling record table** (`journal_event` at `20261012000001:91`, `node_identity` at `:70`): `purge_org_record` deletes `public.organizations` and relies on that cascade (`20261012000005:22-35`), so a cascade FK here would **delete the counter with the purge** and let a re-armed graph restart at 1 — the exact outcome item 7 rules against. An earlier form of this item simply omitted the FK, which left item 7's decision resting on an unstated constraint that an implementer would have added by pattern with **`CHECK (seq >= 0)`** and **`CHECK (mode IN ('seeded','allocating','halted'))`** | the allocator's row. ⛔ **`seq = 0` must be legal and `> 0` must not be asserted: a fresh graph has allocated nothing, and `0` is how the allocator's `old + N` arithmetic expresses that** (the first write then takes `1`). A `CHECK (seq > 0)` makes the seed of an empty graph die on a raw constraint violation instead of one of its named outcomes (found by the review of this revision). ⛔ **The `mode` column is a deliverable, not a convenience** — §A7.7 step 6.5 and §A7.5.11's rollback both key on it, and **row-presence alone cannot distinguish "allocating" from "halted"**, which is how a rollback would leave a graph with neither an allocator nor a fallback (P1, round-2 review). `seeded` exists so the 6.2→6.3 window is a **named state** rather than an implied one. |
| 2 | **RLS: `ENABLE` + `FORCE ROW LEVEL SECURITY`, policy with `USING` AND `WITH CHECK`** on `current_setting('app.current_org_id', true)` | ⛔ **The posture it must match is stated accurately here, because an earlier form of this row claimed something the schema does not have (found by the review of this revision).** What exists today is **`ENABLE ROW LEVEL SECURITY`** on each record table — `journal_event` at `20261012000001:128`, `node_identity` at `:115`, `source` at `:202` — plus a **`NOBYPASSRLS`, `NOSUPERUSER`** application role (`20261025000001:114`, `:143`). What exists **nowhere** is `FORCE ROW LEVEL SECURITY`: `git grep 'FORCE ROW LEVEL' -- '*.sql'` returns **no matches**. `20261025000001:239-272` carries the `USING` **and** `WITH CHECK` clause **pair** — which is what that sentence is about — but it carries **no** `ENABLE` and **no** `FORCE`. So: `ENABLE` + the `NOBYPASSRLS` role is the **existing** shape, and **`FORCE` is a NEW requirement this table adds** (its own migration header must say so) — do not justify it as precedent. **A policy with `USING` alone lets a tenant INSERT another tenant's cursor row; a policy with `WITH CHECK` alone lets it read one.** Neither is optional. ⛔ **And the predicate fails CLOSED in the worst way: an unset GUC makes it NULL, matches no row, and the allocation UPDATE touches nothing** — which is §A7.5.2's rowcount assertion's third case and §A7.6 falsifier 17's. |
| 3 | **`GRANT SELECT, INSERT, UPDATE ON public.record_cursor TO tortoise_record_app`** — and **no DELETE** | `journal_event` is `SELECT, INSERT` only (`20261025000001:190`). **This is the first record table the app role may UPDATE**, and it needs its own rationale in the migration header: the row carries **no content**, its UPDATE **is** the allocation, and DELETE is withheld so a purge must be an explicit RPC (the org purge deletes the whole org, `20261012000005:35`). |
| 4 | **`"record_cursor"` added to `THE_RECORD_TABLES`** | dump, manifest pin, pre-backup counts, RLS posture — all four read that tuple. **Item 1 alone is not enough** and this is the item the review found missing. |
| 5 | **the migration named in `RECORD_MIGRATIONS`** (`record_projection.py:193`) | the registry **is** the tuple; a file not in it is read by nobody. `tests/test_9231_record_migrations_trigger.py` pins **both directions** — tuple→disk (`:229-230`: every name exists) and disk→tuple (`:263-268`: every shipped file is named). A migration that ships without the tuple entry is read by nobody **and** the test that would say so does not exist for it. |
| 6 | **`journal_event` widened for a reproducible export (M15)** — `event_id`, `initiated_by`, `projection_version` carried (as columns or unfiltered in `payload`), and `node_label`/`node_key` **populated** by `_journal_row` | without it §A7.5.8's "re-render from the record" is a fiction. Cost, declared: row bytes grow, so `journal_event_bytes_for_rows` (`#12762`) moves — **a price the owner should see before it is paid**, not a rounding error. ⛔ **This item is also a PRECONDITION of §A7.5.4's refusal guard and of §A7.7 step 6.1**, not merely of §A7.5.8: until `event_id` is in the table, a replay can only compare *numbers*, so "same position, different record" is unobservable and its conflict is still absorbable. |
| 7 | **the org purge RPC: extend, or explicitly not** | if `purge_org_record` deletes `journal_event` rows but leaves `record_cursor`, the counter continues above the purge — which is **correct** (a position must never be reused within the record) and makes the reconciliation's healthy predicate `cursor >= COALESCE(max(journal_event.seq), 0)`, with `cursor > COALESCE(max(...), 0)` meaning *burned by a purge*. ⛔ **The `COALESCE` is load-bearing HERE more than anywhere else:** the purge is precisely the operation that leaves a counter above a graph with **zero** `journal_event` rows, so a bare `max()` is NULL over exactly the population this item describes — and both of its arms (healthy and burned) would then be neither true nor false. Extending the purge to delete the cursor row would instead let a re-armed graph restart at 1 and collide. **So: NOT extended, and the reason is written into the RPC's comment** — and it is **item 1's missing cascade FK that makes that ruling hold**: the purge reaches these tables through `organizations → graphs → <table>`, so a `record_cursor` carrying the siblings' `ON DELETE CASCADE` would be deleted by a purge this RPC never touches — an unexamined purge is how a deliberate choice becomes an accident. |
| 8 | **the graph-creation hook — it ARMS, not only seeds, and its CARRIER is a new RPC.** ⛔ **"In the creating transaction" is not buildable as first written** (found by the review of this revision) and the plan does not get to leave the carrier to an implementer: the `:Graph` is a **FalkorDB** node (`sdk.py` `reg.query("CREATE (g:Graph …)")`) and on the record-armed lane graph creation is a **PostgREST insert with a compensating delete and no open transaction** (`hosted_api.py` `insert_graph` → `supabase_control.py` `cp.query("graphs", method="POST")` → `_rollback_graph` on failure), plus a **second**, best-effort creation site for the default graph (`_mint_default_graph_row_best_effort`, *"never fails the caller"*). So the deliverable is **one Postgres RPC — `provision_graph_record(org_id, graph_id, name, kind, namespace, created_at)` (the `public.graphs` row's own columns; `org_id` and `graph_id` are the key) — that inserts the `public.graphs` row AND `record_cursor(org_id, graph_id, seq = 0, mode = 'allocating')` in ONE transaction**, called at both chokepoints. | ⛔ **Without it the cutover is a SNAPSHOT POPULATION, not a migration** — a graph created afterwards has no counter row, is "uncut" by §A7.5.12's definition, and keeps the enumerating allocator with the D-25 `position=None` window, the 60 s fail-soft breaker and the two-journal-files hazard, **forever**. Fail-hard applies to **custom-graph creation only**; the default graph's row is obtained by a **reconciliation sweep** — see the site map below, because the earlier form of this row said "the two provisioning sites" and those are exactly the ones that must not fail (`#10025` F1). |

**⛔ Deliverable 8's site map — the two graph-creation sites, and which one may fail hard.**

An earlier form of this row said *"the two provisioning sites"*, and that was wrong in a way that would have cost accounts (found by the review of this revision): **`provision_org` and `provision_org_with_token` ARE two of the sites, and they are exactly the ones that must not fail.**

| site | path | may it fail hard? |
|---|---|---|
| **custom graph** | `hosted_api.py:27179` `insert_graph(cp, …)` — its caller compensates with `_rollback_graph` (`:27357-27375`) | **YES** — a raise rolls the graph back, which is what a failed creation should do |
| **default graph row** | `supabase_control.py:4304` `insert_graph(…)` inside `ensure_default_graph_row`, whose **only** caller is `_mint_default_graph_row_best_effort` — and that mint is called by `provision_org` (`:3239`), `provision_org_with_token` (`:3308`) and the credential resolver (`:1340`) | ⛔ **NO — never.** `#10025` F1 is explicit (`:3219-3235`): *"`/v1/register` deletes the data-plane graph it just created, agent signup drops the ONLY copy of the plaintext key, and both then refuse a retry … **A transient error on the `graphs` read here would therefore cost an account**"* |

**So `provision_org` and `provision_org_with_token` ARE two of the sites, and they are exactly the ones that must not fail.** Fail-hard applies to **custom-graph creation only.** The default graph's counter row is therefore obtained by a **RECONCILIATION, not by failing creation**: a sweep (at provisioning completion, at login, or on a schedule) that seeds any graph whose `record_cursor` row is missing — `INSERT … (seq = 0, mode = 'allocating')` resolves to **case 0** and is idempotent, so a sweep is safe to run anywhere and cheap. **Until that sweep exists, an org's default graph can be born uncut** and keep today's allocator — the residual in §A8.2 item 11, widened to say so., or the graph silently keeps today's allocator forever  (it resolves to **case 0**, `seq = 0`, so it is cheap and cannot race a writer that does not exist yet). ⛔ **`mode = 'allocating'` is load-bearing, not a default:** a hook that ran §A7.5.5's seed verbatim would take item 1's `DEFAULT 'seeded'`, and `seeded` means **nobody allocates** (§A7.5.12's mode table) — so the graph would fall back to the enumeration and keep today's allocator, i.e. the exact harm this item was added to remove. There is **no 6.2→6.3 window for a brand-new graph** (its journal is empty, so there is nothing to backfill and nothing to quiesce), which is why the hook goes straight to `allocating` rather than through the two-step the cutover needs | ⛔ **Without it the cutover is a SNAPSHOT POPULATION, not a migration.** §A7.7 step 6.2 seeds the graphs that exist on cutover day, once; a graph created afterwards has no counter row, is "uncut" by §A7.5.12's own definition, and keeps the enumerating allocator — with the D-25 `position=None` window, the 60 s fail-soft breaker and the two-journal-files hazard that this plan exists to remove. §A7.5.7's fail-hard posture does not cover it (it is scoped to "the ordinal path"), and §A8.2 item 10 records the consequence for any window where the hook is not yet shipped. **Deliverable, not a nice-to-have: without it the plan fixes the past and not the future.** |

**Falsifier (B3, and it is the whole point): restore a dump into a fresh database, then assert the next write is
`max(seq)+1`.** Two sub-assertions, because there are two ways to get it wrong: (a) the dump **contains**
`record_cursor` (item 4 present), and (b) the restored counter is **≥** `COALESCE(max(journal_event.seq), 0)` — a restore
that carries the table but not the rows, or the rows but not the table, fails one of the two. ⚠️ And **a third**,
from D-29: `record-restore` bulk-loads via `pg_restore --data-only` (`record_backup.py:1723-1733`), bypassing
`_journal_row` — so the restore verb must **refuse** when the post-restore predicate
`cursor >= COALESCE(max(journal_event.seq), 0)` is false rather than proceeding and letting the first write
collide.

### A7.8.1 The suites that must move, and the tests that pin it (M11)

The round-1 plan named **two** test files for a change that inverts the behaviour of four of these six suites (the fifth, `tests/test_9206_apply_first_lane.py`, is listed so Stage C does not assume it is unchanged). Named here so
Stage C starts from the real population:

| suite | lines | what it pins | what the change does to it |
|---|---|---|---|
| `tests/test_9190_journal_position.py` | 1,736 | the position's definition, the sidecar, the recount, the degrade windows (~40 tests) | **the source of the number moves.** The file's subject survives; its **allocator identity** does not. This is the largest single suite to re-derive. |
| `tests/test_9078_record_written_by_production_path.py` | 5,021 | the three live doors → one seam, and **the position the door hands over** | the door now hands over a number the **record** minted — the seam's contract changes direction (it becomes a *return* value, not a *parameter*). |
| `tests/test_9231_record_migrations_trigger.py` | 356 | the registry both directions, the shipped-file↔tuple pin | §A7.8 items 1–5 are pinned here; the new migration must satisfy both directions. |
| `tests/test_10017_backfill_copy.py` | 1,986 | the copy-turn backfill and its contiguity refusal | M8's block allocation must keep it green — and it is the executable form of falsifier 10. |
| `tests/test_9206_apply_first_lane.py` | 640 | the eight declared `apply-first` lanes (D-14) | unchanged by the allocator, **listed so Stage C does not assume it is** — it is the pin for "a declaration names where the write lives". |
| **new — `tests/test_XXXX_graph_creation_seed.py`** | — | that a graph created **after** cutover has a `record_cursor` row **in `mode = 'allocating'`**, and that its first write takes `1`. ⛔ **The `mode` assertion is the point:** a hook that only seeds leaves `DEFAULT 'seeded'` — a row that allocates nothing — and the graph is *still* uncut, so the harm relocates from "no row" to "a row with no allocator" (§A8.2 item 10) | **new**: this is the suite §A7.7 step 6.6 and §A7.8 item 8 require, and nothing today pins it |

⚠️ **M2 session-capture's decline is pinned at `record_writer.py:2287-2290`** and its conversion (§A7.7 step 6.4)
is the step that changes it — not a drive-by.

---

## A8 — The revision's own audit: `finding → section`, and what this revision could NOT resolve

### A8.1 The deliverable table — every finding, and where it landed

**Blocking (B1–B7).** Each has a **mechanism** and a **falsifier**; the column that matters is the third.

| # | finding | where it landed | its falsifier |
|---|---|---|---|
| **B1** | the seed is silently wrong for a journal with no position rows | **§A7.5.5** — the seed as executable SQL, the enumerated floor taken from the replay's **own** reader, `GREATEST(record_floor, journal_floor)`, and the **refusal** when the record floor is above the journal floor; **five outcomes** tabulated (case 0 empty graph → seed `0`; case 2a nothing carried → **lift**; case 2b → refuse; case 1 → refuse; healthy → proceed) with the operator action for each; §A6 **D-28** carries the shrink trigger; §A4.1's "recount never a wrong answer" is scoped | **§A7.6 #7** — a journal of N with zero rows must next write `N+1`; and the refusal arm must write **no** counter row. ⛔ **Round 3 narrows B1's claim and says so: case 2b (the D-25 *tail* heal) is DEFERRED**, because its condition (iii) — the same journal identity — has no data source until `#9318`, so the seed refuses **2b** by default and those graphs stay uncut (§A8.2 item 9). **Case 2a (`record_floor = 0`) is not deferred** — it carries no numbers, so falsifier 7 asserts it. ⛔ **Corrected in the fifth pass: that path was NOT live even so**, because §A7.5.2's block form returned `seq - $3` (the counter's *current* value) as `first_seq`, so the first write after any healthy seed collided on the `journal_event` PK. The `+ 1` is §A8.3.3's P0; the seed's admission and the allocator's output had to be right **together** for this claim to be true at all. **B1 is answered for the case that can be decided and explicitly not for the case that cannot** — the alternative was a lift on two of three conditions |
| **B2** | no rollback path; append-only trigger makes a mis-numbered row unrepairable | **§A7.5.11** — *stop allocating, keep the carried numbers, never return a graph to enumeration*, with a per-graph exemption requiring a **proof predicate**; the repair ladder priced rung by rung; the GUC path and its blast radius named; recreate-the-graph excluded with the reason | **§A7.6 #8** — the rollback must be refused for a graph with a hole, not silently renumber |
| **B3** | `record_cursor` absent from the record's table registry → restore restarts the allocator at zero | **§A7.8** — the full deliverable list (table, RLS `USING` **and** `WITH CHECK`, `GRANT SELECT, INSERT, UPDATE` and **no DELETE**, `THE_RECORD_TABLES`, `RECORD_MIGRATIONS` both directions) + the **first-updatable-record-table** rationale + the purge decision; **§A7.8** falsifier: restore a dump, assert the next write is `max(seq)+1` | **§A7.8** (three sub-assertions: table present, counter ≥ max, restore **refuses** when it is not) |
| **B4** | the arm is never stated; both arms break a guarantee | **§A7.5.6** — **AFTER COMMIT for `allocating`** (and the PRE-COMMIT arm for `halted`, stated there), with the three-way argument (before / after / staging-is-not-a-third-arm), the contiguous-prefix export that re-serialises into seq order, and the admitted price: **the per-graph lock returns on the export path**; §A7.5.1 supplies the lock fact that makes it safe | **§A7.6 #9** — commit 5 and 6 out of arrival order; the file must still read 5,6 |
| **B5** | a stale counter becomes permanent silent loss | **§A7.5.2** (the allocation is `cursor + N`; **`cursor < max(seq)` RAISES** — `GREATEST` lives only in the seed, because on the hot path it silently repaired the very divergence the refusal arm exists to surface) · **§A7.5.4** (the live path **drops** `ON CONFLICT DO NOTHING`; the replay path refuses on `event_id` **when it is non-NULL, counts when NULL pre-widening, and refuses only on a non-NULL mismatch** — the NULL rule is round 2's fix, since the first version would have refused all of history) · **§A7.5.2**'s **rowcount assertion**, without which the CTE fails open | **§A7.6 #12** (reconciliation **armed**) and **#17** (the three no-row cases, including the RLS-GUC-unset shape) |
| **B6** | cursors ARE `:GraphEvent.seq`, so §A7.1 and §A4.5 cannot both be built | **§A7.1** — corrected to *"`:GraphEvent` keeps its own positions"*, with the two-coordinate table, the bounded `MAX_SEQ` domain and `first_seq`-purge-maintained facts; **§A7.7** now **removes D-07 from the sequence entirely** (the round-1 step 4 instruction — *"the record mints the value that goes into `:GraphEvent`"* — was adoption by another name and could not be built; `next_seq` stays, D-07 waits for its own reopen) | **§A7.6 #16** — a cursor captured **before** the change must still land on the same `:GraphEvent` rows after it. **Falsifier #4 does NOT cover this** and the round-2 review was right that the earlier row over-claimed |
| **B7** | `journal_event` is the only carrier of `Object.status`/`Subject.status` | **§A6 D-27** (new row: the terminal-fold stickiness makes a bound a **resurrection** bug) + **§A7.5.12** (the bound stays out) + **§A7.7 step 7** (the bound is the FILE only; a table bound is a different decision); §A2.8/D-30 give the in-tree precedent (`#3870`) | **§A7.6 #20** (prefix deletion + rebuild must still read terminal) + §A7.8 item 7 (the purge is extended only if a ruling says so). ⛔ **This cell cited #12 and was wrong — #12 is a *number* reconciliation, and a prefix deletion leaves `max(seq)` unchanged, so it stays green through the resurrection B7 describes.** |

**Material (M1–M16).** Each is folded into the section it belongs to — and the table is the proof none was dropped. ⛔ **M16's row must live in THIS table, not in the blocking table above it:** an earlier form appended it to the *blocking* table (whose preamble says B1–B7) while retitling this one to M1–M16, so both headings misdescribed their tables — in the section whose stated job is *"the table is the proof none was dropped"*.

| # | finding | landed in |
|---|---|---|
| **M1** | the census omits journal MUTATORS that are not appends | **§A2.7** (new: the three mutators, tabulated, each with the property it breaks) + **§A6 D-28** + **§A7.7 step 3** (the D-24 guard must cover all three doors) + **§A7.6 #1** |
| **M2** | "the ordinal requires the journal never shrinks" as a debt row | **§A6 D-28** + **§A4.1** (the "recount, never a wrong answer" sentence scoped to the position) |
| **M3** | the record's own restore is a second writer of `journal_event` | **§A6 D-29** + **§A7.8** (the restore must refuse when `cursor < max`) + the third falsifier sub-assertion |
| **M4** | five surfaces is an undercount: `capture_spool` is absent from the plan entirely | **§A6 D-30** (the row and the ruling text) + **§A2.8** (the comparable, and the two facts that make it one) + **§A7.4** (the counting rule and the **sixth** surface) — ⛔ **the earlier row also cited §A7.5.11/§A7.5.12, and `spool` appears in neither**; the round-2 review was right, and the anchor is withdrawn rather than left standing — **count-falsifier:** §A6 D-30's own count-falsifier: `grep spool` over the plan must not return 0 |
| **M5** | "allocation order = commit order" preconditions must be stated | **§A7.5.3** (the four-precondition table, with the **refuse-to-arm** on a non-READ-COMMITTED DSN) + **§A7.6 #14** |
| **M6** | a second allocator writes the same space (`replay_plan`) | **§A7.5.4** (carried wins; refuse on divergence; replay takes the **same** row lock) + **§A7.7 step 6.1** (readers ship first) + **§A7.6 #6** |
| **M7** | two recorded decisions are reversed and not named among the reopens | **§A7.5.9** (`record_writer.py:78-101` decision 3 + the live door's `OVERRIDES`; and `:116-123` decision 5's *"nothing renumbers"*) — both named as **reopens with their route**, plus the reopen-vs-addendum distinction for the four descriptive statements |
| **M8** | batches: contiguity is required and block allocation is unstated | **§A7.5.2** (block allocation `+ N` / `RETURNING seq − N`, with the idempotency-key consequence stated) + **§A7.6 #10** (a hi-lo variant must go red) |
| **M9** | the ordinal cannot be known at plan-build time | **§A7.5.2** (the data-modifying CTE: one statement, so the number is chosen *by* the statement; `RecordWrite.first_seq/last_seq` threaded back from `RETURNING`) |
| **M10** | fail-soft contradicts "no burned position" | **§A7.5.7** (posture flips: fail-**hard** on the ordinal path; the breaker becomes fail-fast, and the alternative — a named bounded degraded state — is stated and rejected) + **§A7.6 #13** |
| **M11** | tests and lanes are missing | **§A3.1** (the two missing lanes: MCP BASE/stdio and M2 session-capture) + **§A7.8.1** (the six suites Stage C must check, four of which must move, with line counts and what each pins) + **§A7.7 step 6.4** |
| **M12** | `tools/record_wiring_check.py` is absent yet asserts exactly the inverted rule | **§A5** (added to "what already exists", with both check names) + **§A7.7** (re-derived at step 6.1, **not** deleted, with the replacement held to the same strictness) |
| **M13** | the completeness artifact contradicts itself | **§A1.4** (a four-row table answering each sub-claim at this head: two already fixed, one still true and now fixed, one not reproducible — with the 160-citation audit as the evidence) + the `INGEST_CONTRACT` row + **§A7.3** (the embedded-lane durability claim recorded as a third unqualified sentence) |
| **M14** | D-20 is over-general | **§A6 D-20** (narrowed to `record_read.py`; the two counter-examples named; **the inference withdrawn**) |
| **M15** | "the export is repaired by re-rendering from the record" is not achievable | **§A7.5.8** (the column-by-column table, a **correction to the finding's wording**, and the two arms with the widening chosen and its cost declared) + **§A7.6 #11** + **§A7.8 item 6** |
| **M16** | nothing seeded a graph created **after** the cutover, so the fix was a snapshot of the past | **§A7.8 item 8** (a `provision_graph_record` RPC that writes the `public.graphs` row and `record_cursor(seq = 0, mode = 'allocating')` in ONE transaction) · **§A7.7 step 6.6** (shipped with 6.3, and the last thing that may be reverted) · **§A7.8.1** (the suite that pins it) · **§A8.2 item 11** (the residual for the best-effort default-graph mint, which stays best-effort under `#10025` F1 and can therefore be born uncut until a reconciliation sweep exists). ⛔ **The `mode` half is the falsifier's point:** a hook that only seeds writes `DEFAULT 'seeded'`, which allocates nothing, and the test must go red on it |

**Two things the brief required kept, and where they are:** §A7.4's honesty is **§A7.4** itself — now with an
explicit counting rule and **six** truth-bearing surfaces (the fifth being the file, the sixth `capture_spool`),
plus the cache explicitly *not* counted and why. The missing sequencing step is **§A7.7 step 6**, six sub-steps,
with **the coordinate it reconciles on** stated in bold: `journal_event.seq`, and only that.

### A8.2 What this revision could NOT resolve — read this first, next round

1. **Whether the GUC DELETE (B2 rung 2) should exist at all.** I can state its blast radius exactly — the
   trigger's DELETE arm has **no WHERE guard**, it needs an owner role, there is no audit, and the only backstop
   is a record dump no schedule takes — but I cannot make it *safe* without changing the trigger, and changing the
   trigger to accept a scoped predicate is a **product change** this document is not authorised to specify. **The
   honest unresolved question: is a scoped operator repair a legitimate operational tool, or should the answer be
   "restore from a dump, and build the dump schedule first"?** I lean to the second and have not ruled it.
2. **Whether fail-hard (M10) survives the durability posture's availability claims.** The flip follows from "the
   record is the commit point", and `durability-posture.md` does not say what a record outage should do to the
   write path — it says what durability *is*, not what availability may be traded. **A record outage becoming a
   write outage is a product-level consequence that needs an owner ruling**, and this revision states it rather
   than deciding it.
3. **The export path's real cost.** §A7.5.6 says the per-graph lock returns on the export path and that a wedged
   exporter delays export, not commit. **I have measured neither the added latency nor the contention**, and I
   will not estimate them: the row-lock and exporter costs are stated as *unmeasured* here and in §9.2, because a
   number invented in a plan becomes a number quoted in a review.
4. **Whether six is the right final count of truth-bearing surfaces.** The counting rule is stated (§A7.4) so the
   number is now derivable rather than asserted — but the rule itself is mine, and a reader who counts
   "statements" gets ten while a reader who counts "stores" gets five. **If the rule is wrong, the number is
   wrong, and the rule is easier to argue with than the number** — which is why the rule is printed.
5. **~~Whether `:GraphEvent`'s allocator (D-07) can actually be retired~~ — CLOSED by the round-2 self-review,
   and the *question* was the finding.** The first version listed this as unresolved while §A7.7 step 4 stated
   it as a build instruction — a self-contradiction. **It cannot be retired inside this design** (a record-minted
   value cannot reproduce `GraphEventMeta.last_seq`, which lives in FalkorDB, without being the record's ordinal
   — B6), **so D-07 is out of the sequence** and the falsifier that would catch a wrong build is §A7.6 #16.
   ⛔ **Recorded rather than deleted: an unresolved item that also appears as an instruction is how a reviewer
   concludes the plan is fine. Removing the instruction was the resolution; the item stays so the next round can
   see what it was.**
6. **The `#9025` bound and D-27 interact, and I have only stated the interaction.** If a bound on `journal_event`
   is ever ruled in, the terminal-fold stickiness means the bound must be **per-node-state-aware** (you cannot
   drop an `op=restatus` row for a node whose later rows remain) — which may make the bound *unimplementable*
   rather than merely dangerous. That is a finding for whoever rules on `#9025`, and I have not proven either
   way.
7. **⛔ Quiescence at cutover is OPERATOR-ASSERTED, not enforced, and that is the largest thing this revision
   stopped pretending to have solved.** The first version promised the seed would *refuse on a non-quiesced
   graph* and a falsifier (#19) asserted it. It cannot: the seed takes no lock a journal writer contends, the
   journal is a **file**, and before the cutover there is no per-graph record row to lock. **So the falsifier
   was withdrawn (§A7.6 #19, number left as a hole) rather than relaxed, and quiescence is a precondition the
   runbook must state and an operator must satisfy.** The residual risk is exactly the one the withdrawal
   admits: two floors read either side of a live write. **The only mechanical mitigation this design offers is
   the post-seed assertion (§A7.5.5 step **(3)**), which turns a wrong floor into a REFUSAL — after the fact.** A
   genuine fix needs a write-path coordination point this design does not have, and naming that gap is more
   useful than a promise the code cannot keep.
8. **⛔ A `cursor < max(seq)` state has NO clean repair rung (round-2 review of this revision — the finding is
   the gap, not an omission).** The allocator now **refuses** on it (§A7.5.2), which is correct but leaves the
   graph unable to allocate; rung 1's *"no action"* is a decision not to act and does **not** clear a refusal;
   rung 0 halts THE graph but repairs no counter (that is its point, not a defect); no rung carries a bump
   §A7.5.11 does not call **worse** (a manual `SET seq = max(seq) + 1` re-issues
   burned numbers or skips them); rung 2 is a journal DELETE, not a counter repair; rung 3's restore needs a
   dump **no schedule takes**. **So the state is detected, refused and
   unrepairable by the plan's own tools** — an operator's only real routes are a manual counter correction
   against `journal_event`, or restoring the whole graph. ⛔ **This is a consequence of choosing refusal over
   silent reuse, and refusing is still right** — but a design that refuses must say what the road back is, and
   today the road back is manual. Whoever implements §A7.5.11 should expect to build rung 4.
9. **⛔ Case 2b of the seed (the D-25 *tail* heal) is DEFERRED, not implemented.** §A7.5.5's case 2b needs a journal
   identity the record does not carry (D-24 is a guard, `#9318` is unbuilt, D-28 shows `stat` fields cannot
   supply it), so **the seed refuses case 2b by default and those graphs stay uncut** — a graph that carries
   numbers whose two possible readings cannot be told apart. ⛔ **This item is scoped to 2b and no wider:**
   case 2a (`record_floor = 0`, D-25's `None`-position graphs) carries **no** numbers, is therefore not
   ambiguous, and **is lifted** (falsifier 7). Reading this item as "D-25's `None`-position graphs stay uncut"
   is what made a first fix refuse B1's motivating population, so the distinction is printed here rather than
   left to the reader. Stated as deferred rather than solved because the alternative — lifting on two of three
   conditions — is the same silent-loss class as the bug being fixed. **The unblocking dependency is
   `#9318`**, and until it lands, B1's claim is narrower than round 1's text made it.

10. **⛔ The cutover is a SNAPSHOT POPULATION until §A7.8 item 8 ships.** Step 6.2 seeds the graphs that exist on
    cutover day; the creation-time hook is a deliverable (§A7.8 item 8) and, until it lands, **every graph created
    after cutover stays uncut** — **no counter row at all**, today's enumerating allocator, the D-25 `position=None`
    window and the 60 s fail-soft breaker. ⛔ **And the hook must ARM, not merely seed:** a hook running §A7.5.5's
    seed verbatim takes `DEFAULT 'seeded'`, i.e. a row that allocates nothing, and the graph is *still* uncut —
    the harm relocates from "no row" to "a row with no allocator". So the pair is *a counter row in `allocating`*
    (§A7.8 item 8), and anything less does not close this residual. That is not a footnote: on a lane that creates graphs it is the **steady state
    within days**, and it means the design's central claim (a position always exists) holds only for the
    population that existed on cutover day. **The two are shipped as one change or the plan's own premise is
    false a week after it lands.** Named here rather than in item 8 so a reader of the residuals cannot miss it.

11. **⛔ An org's DEFAULT graph can be born uncut, and this plan does not fix it.** `_mint_default_graph_row_best_effort`
    (`supabase_control.py:4391-4400`) is a **recorded decision** — *"never failing the caller (#10025 F1)"*, *"auth must
    not fail because a control-plane write did"* — reached from the **auth** path. The creation hook (§A7.8 item 8) is
    therefore scoped to **custom-graph creation only** — ⛔ **and the residual is WIDER than "minted during login":
    the mint is called by `provision_org` (`:3239`) and `provision_org_with_token` (`:3308`) as well as the credential
    resolver (`:1340`), so an org whose default graph is minted at PROVISIONING or at login can both end up without a
    counter row** and keep today's allocator. Reversing `#10025` F1 is worse than a login failure: the code records
    that a raise there *"would cost an account"* — `/v1/register` deletes the graph it just created, agent signup drops
    the ONLY copy of the plaintext key, and both then refuse a retry. **So this plan will not fail it unilaterally**
    and the remedy is a decision: a **reconciliation sweep** (at provisioning completion, at login, or on a schedule —
    `INSERT … (seq = 0, mode = 'allocating')` is case 0 and idempotent, so a sweep is safe and cheap), or a reopen of
    `#10025` F1 with its account-loss cost priced.

### A8.3 The revision reviewed itself, and the review found ten — recorded because the finding class is the point

The round-2 lens was applied to the revision by a **fresh-context reviewer asked one question** the earlier
rounds never asked of *this* document: *does the revision introduce a new silent failure?* It found **three P0 and
seven P1**, all of which are now fixed and each of which is recorded here rather than quietly removed:

⛔ **These counts were wrong in the revision itself, and a later review caught them: the heading said "five", the
body said "three P0 and eight P1" (= eleven), and this table carries ten rows.** *"Each of which is recorded here"*
is a claim about a population, so a count that does not match the table is not a typo — it is the one thing this
section exists to disprove. **The table is the population and the counts now follow it (three + seven = ten).**
The eleventh finding could not be recovered: if the review did return eleven, one row was lost between the review
and the table, and it is recorded as **unrecoverable rather than invented** — a fabricated row would have made the
heading true and the section false.

| # | what the revision got wrong | the fix |
|---|---|---|
| **P0** | §A7.7 step 4 instructed Stage C to *retire the `:GraphEventMeta` allocator in favour of the record's position* — **adoption by another name**, i.e. exactly B6 | D-07 removed from the sequence; `next_seq` stays; §A7.6 #16 gives B6 a falsifier that can actually detect it |
| **P0** | the allocator was `GREATEST(cursor, max(seq)) + N`, which **silently repaired** a `cursor < max` divergence at the next write — so B5's refusal arm could never fire, and B2's "repair" rung was the default path | hot path is `cursor + N` and **raises** on divergence; `GREATEST` lives only in the seed, where a floor is the point |
| **P0** | the replay guard compared **numbers only** — "same position, different record" was unobservable because `journal_event` has no `event_id`, so a shorter journal re-enumerating onto occupied numbers was **absorbed** | the replay conflict refuses unless `event_id` matches; **§A7.8 item 6 becomes a precondition of the guard**, not a nicety of the export |
| **P1** | the allocation CTE **failed open**: `INSERT … SELECT … FROM alloc` writes **zero rows and raises nothing** when the UPDATE matches none (no cursor row, a mistyped key, or **an unset `app.current_org_id` under `FORCE RLS`**) | rowcount assertion as part of the mechanism; falsifier 17 names all three |
| **P1** | the seed's case 2 treated `record_floor < floor` as *only* a D-25 tail — but a journal **replaced by a longer one** has the identical signature, and the "heal" would mix two journals into one coordinate space | three tail-evidence conditions, and any failure ⇒ REFUSE; falsifier 18 |
| **P1** | B2 rung 1 (the *recommended* one) named the wrong mechanism and claimed a cost (`cursor > max` forever) that does not follow from its own arithmetic; executed as a manual bump it leaves a hole the drainer blocks on forever | rung restated as *decision not to act*; the manual-bump variant named as a **worse** rung; the drainer gained a **burned-number rule** (§A7.5.6) |
| **P1** | the per-graph exemption's proof had **no validity window** — a statement about an instant, taken once, with no quiesce requirement | proof is about a **quiesced graph at the moment of the revert**, refused while any allocator is live |
| **P1** | §A7.7 6.2→6.3 claimed "no graph is ever in a state where two writers disagree" — false **inside that very window** (the counter exists while the writer is still the file) | the claim is scoped to the interval it holds in; a graph that resumes writing before 6.3 must re-run 6.2 |
| **P1** | §A7.7 6.5 keyed the fallback removal on **row existence**, which would have **destroyed the rollback §A7.5.11 promises** (a halted graph keeps its row) | `record_cursor.mode` (`seeded`/`allocating`/`halted`) is a deliverable; 6.5 keys on `allocating` only |
| **P1** | two §A8.1 cells cited anchors that did not contain the answer (M4 → §A7.5.11/12, where `spool` appears nowhere; B5 → §A7.5.1, which has no refusal posture) | both re-pointed; the wrong anchors are **named as withdrawn** in the cells rather than replaced silently |

⛔ **The lesson is the same one the whole document argues, one level up: a falsifier attached to a finding it
cannot detect is worse than no falsifier,** because it converts "unverified" into "verified" without changing
anything. Falsifier 16 exists because the revision made exactly that mistake about B6, and §A8.1's B5 row changed
because the revision had attached B5's most important half to a section that does not contain it.

**What the reader should conclude from §A8.3 existing at all:** the revision is not claimed clean. It is claimed
*reviewed by an instrument that could fail it* — and the next round's most useful target is the part of this
document that no falsifier reaches: the **operator-facing** decisions in §A8.2.

#### A8.3.1 The third pass: the fixes were checked, and three of them were not fixes

The second review fixed ten findings. The third pass asked only *"is each fix actually a fix?"* — and found
that **three had been applied as words rather than as mechanism**, which is the failure mode this document keeps
naming one level down:

| found | why the "fix" did not fix it | what it is now |
|---|---|---|
| **P0** | §A7.1 had been corrected to remove D-07 from §A7.7, but the same paragraph still said §A7.7 *"keeps D-07 at step 4"* — the withdrawn sentence was left standing beside its own correction | corrected in place, and **the withdrawn phrasing is quoted as withdrawn** rather than deleted, so a reader comparing rounds sees the contradiction resolved rather than erased |
| **P0** | the replay guard's *"refuses unless `event_id` matches"* would have **refused every row written before the envelope widening** (`event_id IS NULL`) — breaking the `#9187` tail replay and falsifier 4, i.e. the fix introduced exactly the outage class it was meant to prevent | an explicit **NULL rule**: non-NULL-equal proceeds, non-NULL-different refuses, NULL **proceeds and counts** a named metric; the backfill is §A7.8 item 6's second half and the counter must reach zero before the strict guard is claimed |
| **P0** | the seed's three tail-evidence conditions included *"the same journal identity"* — and **no per-row journal identity exists** (D-24 is a guard, `#9318` is unbuilt, D-28 shows `stat` cannot supply it). Condition (iii) was **not evaluable**, so case 2 would have lifted on two of three conditions | **case 2 REFUSES by default**; the D-25 tail heal is **DEFERRED, not implemented**, and §A8.2 item 9 says so. A narrower claim that is true beats a broader one that needs a condition nothing can check |

**And one falsifier did not survive the same question.** #19 asserted the seed refuses on a non-quiesced graph —but
the seed takes no lock a writer contends and has no detection path, so the test could only pass vacuously or fail
against the spec. **It was withdrawn and its number left as a hole** (§A7.6 intro), and the promise became §A8.2
item 7 (*quiescence is operator-asserted*). The withdrawal is the outcome, not a gap in it: **a falsifier that
cannot fire is worse than no falsifier**, because it converts unverified into verified.

⛔ **"Two" was wrong and is corrected to "one": the paragraph named one withdrawal (#19) and no second one exists.**
The only other candidate is round-1's B6 falsifier, which §A7.6 #16 says was **replaced, not withdrawn** — replaced
is a different outcome and it has a live number, so folding it in here would have made #16's replacement read as a
gap. **A count with no population behind it is the same defect as a falsifier that cannot fire**, and this paragraph
is in the section that exists to make withdrawals auditable.

⛔ **What the third pass proves about the process: a review that only asks "was the finding addressed?" cannot
distinguish a mechanism from a sentence.** All three P0s above had a fix that *read* as responsive and changed
nothing mechanically. The question that found them is one line — *"what does this do when it runs?"* — and it
belongs in the next round's brief rather than being rediscovered each time.

#### A8.3.2 The fourth pass: the fix was scoped to the wrong population, and that is the same defect again

The third pass found that the seed's `mode`-predicate fix had been applied as **prose while the SQL kept the old
behaviour**, and that the seed's case-2 deferral had been applied as **prose while the SQL kept lifting**. Both
were corrected. The fourth pass asked the same one question of the corrections — *what does this do when it
runs?* — and found that **the correction to the seed had over-corrected**, plus five smaller defects:

| found | why the "fix" was not a fix | what it is now |
|---|---|---|
| **P0** | the seed was narrowed to `WHERE $record_floor = $3` (**equality only**), which obeys the 2b deferral **and refuses case 2a as well** — `record_floor = 0` is B1's **motivating population** (the large journal with zero `journal_event` rows, D-25) and the one **falsifier 7** asserts must next write `N+1`. So the seed could no longer do the single thing B1 claims for it, and falsifier 7's green path was unreachable **against the document's own SQL** | the deferral is **scoped to case 2b** (`0 < record_floor < enumerated_floor` — numbers *are* carried, readings indistinguishable); **case 2a lifts**, because with nothing carried there is no carried number for a different journal to collide with — the hazard the deferral exists for cannot occur. Admission is `$record_floor = 0 OR $record_floor = $3`. ⛔ **The lesson is one level out from the previous three: it is not enough that a rule be *mechanism* rather than prose — it must also be scoped to the population it is about.** v1 applied "lift" to everything, v2 applied "refuse" to everything; the rule was about the middle case both times |
| **P1** | the rowcount assertion's **fourth arm was unreachable**: the diagnostic probe `SELECT`s `record_cursor` under the *same* RLS policy that made the write fail, so with `app.current_org_id` unset the probe reports "row absent" and **arm 1 fires** — "not cut over — no counter row" — for the one failure §A7.5.2 calls "the one that will actually happen", when the row exists and the fault is the GUC | the diagnostic is specified as running **with the GUC set for the check**, and **falsifier 17(c) asserts the message**, not merely that something raised. ⛔ **This cell said "(or as the owner role)" and that escape is gone — `FORCE` denies it and so does `record_projection.py:6085-6093`; the sixth pass found the offer still standing and removed it (§A8.3.4)** |
| **P1** | a `seeded` counter row **could never be re-seeded**, contradicting step (0)'s own promise and 6.3's remedy: `ON CONFLICT DO NOTHING` writes nothing, so step (3) reads the **stale** `seq` back and refuses — turning the documented recovery into a refusal | the conflict arm is a **scoped UPDATE** — `DO UPDATE SET seq = EXCLUDED.seq … WHERE record_cursor.mode = 'seeded'` — so re-seed corrects, and `allocating`/`halted` can never be touched by a seed |
| **P1** | falsifier 20's polarity claim was **false**: it said the test was "green by absence of the defect", but the test performs the deletion itself and rung 2 prints that `DELETE` — so it is **red today** | polarity corrected: the defect is demonstrable **now**, and the test must go red again against the bound's first implementation. A false claim about what goes red is precisely what this section says is worse than no falsifier |
| **P2** | §A7.7 step 7 cited `STORAGE-ARCHITECTURE.md:427` for a sentence that is at `:442`; `:427` is §5.2's **heading** | `:442`, with the correction named rather than swapped silently — a pointer landing on a heading is the class §12 says took nine cycles to clean |
| **P2** | the freshly added `mode`-predicate comment said `halted` is *"the state §A7.7 6.5 gates the fallback removal on"* — **inverted**: 6.5 removes the fallback for `allocating` only and **keeps it** for `halted` | restated: 6.5 keys on `allocating`, so a `halted` graph keeps its fallback and loses only its allocator. The inversion the round-2 review found was re-introduced by the comment that fixed it |

⛔ **What the fourth pass proves: a fix can be a mechanism and still be wrong, if it is scoped to the wrong
population.** Three rounds in a row the same class appeared — prose-for-mechanism, then mechanism-for-the-wrong-case
— and each round it presented as *responsive*. The check that catches all of it is two questions, not one:
*"what does this do when it runs?"* **and** *"which population is this rule about, and is the artifact I just
changed that population?"*

#### A8.3.3 The fifth pass: THREE SECTIONS HAD NEVER BEEN CHECKED AGAINST EACH OTHER

The fourth pass's fix (scoping the deferral to case 2b) was verified correct, as were all five of its other
fixes. But the fifth pass asked the question one level out — *do the sections that describe the same mechanism
agree with each other?* — and found the **first P0 in this document that no previous round had looked at**,
sitting in the section immediately **upstream** of everything the last four rounds had been fixing:

| found | what was wrong | what it is now |
|---|---|---|
| **P0** | **the block allocator re-issued the counter's CURRENT value.** `UPDATE … SET seq = c.seq + $3 … RETURNING c.seq - $3 AS first_seq` evaluates `RETURNING` against the **new** row, so `first_seq = (old + $3) - $3 = old` — the number the counter had **already** handed out. Every cut-over graph's first live write would then collide on the `journal_event` PK `(org_id, graph_id, seq)`, and §A7.5.4 **drops** `ON CONFLICT DO NOTHING` on the live path, so it **raises**. §A7.5.1's single-record form is `seq + 1 … RETURNING seq` ⇒ `old + 1`; for `N = 1` the two sections allocated **different numbers from the same counter** | `RETURNING c.seq - $3 + 1 AS first_seq` — block `[old+1, old+N]`, matching §A7.5.1, and `AND c.seq >= max(seq)` becomes the correct healthy predicate. The `RETURNING seq - $3` restatement in §A7.5.2's bullet list is corrected with it. ⛔ **This is the farthest-reaching defect found so far, and it had nothing to do with any of the 13 findings before it.** Four rounds fixed the seed's *admission*; nobody had checked the seed's **output** against the allocator that consumes it |
| **P1** | the four-case seed rule was **not exhaustive**: an empty graph (`record_floor = 0`, `enumerated_floor = 0`) was admitted into the lift branch and wrote `seq = 0`, which §A7.8 item 1's own `CHECK (seq > 0)` rejects — so seeding a brand-new graph died on a raw constraint violation instead of one of its named outcomes | **case 0** added (empty graph → seed `0`, first write takes `1`), and the CHECK is now **`seq >= 0`** — `0` is how the allocator's `old + N` arithmetic says "nothing allocated". Admission gained the `$3 > 0` qualifier the outcome table had always carried while the SQL did not |
| **P1** | **`FORCE ROW LEVEL SECURITY` was asserted three times as an existing schema property and exists nowhere** — `git grep 'FORCE ROW LEVEL' -- '*.sql'` returns no matches, and `20261025000001` carries **no** `ENABLE` either (the `ENABLE`s are in the per-table creation migrations, e.g. `20261012000001:128`) | the real posture is stated: `ENABLE` + a `NOBYPASSRLS`/`NOSUPERUSER` app role (`20261025000001:114`, `:143`), and **`FORCE` is named as a NEW requirement this table adds**, not as precedent. The justification sentence about `20261025000001:239-272` is scoped to the `USING`/`WITH CHECK` **pair** it actually carries. ⛔ **This row claimed it had also corrected the "or as the owner role" alternative, and it had not** — the sentence survived untouched in both places that specify the diagnostic (§A7.5.2 and falsifier 17(c)); the sixth pass found it and removed it (§A8.3.4) |
| **P1** | **`halted` was a one-way door.** §A7.5.12's transitions table named "re-cut by a **fresh seed**" as the only exit, and §A7.5.5 step (0) **refuses** `mode IN ('allocating','halted')` while the conflict arm is scoped `WHERE mode = 'seeded'` — so the exit named a call that refused. The table's "exactly one writer" claim was false for the row it added | the **`halted` → `seeded`** transition is named with its writer (an explicit operator `UPDATE … WHERE mode = 'halted'`, guarded by the quiescence assertion), so §A7.5.11's rollback is reversible, which is the only reason that section exists |
| **P1** | §A8.1's B1 cell still said **"three outcomes"** against §A7.5.5's four (and now five) | corrected, with the `record_floor = 0` lift added to the cell's one-line description |
| **P2** | the `GREATEST` justification's parenthetical still said "(equality admission, above)", a predicate round 4 removed | corrected |
| **P2** | falsifier 20 cited §A7.5.11 rung 2 as printing "the exact statement", but rung 2 prints the **single-row** `… AND seq = $3` while #20 needs the **prefix** `… AND seq <= $3` | #20 now cites the **mechanism** (the trigger's DELETE arm has no row guard, `20261012000001:104-106`) and names the **owner role** its fixture needs, since the app role holds only `SELECT, INSERT` (`20261025000001:190`) |
| **P2** | the **seed** never stated the execution context its own reads require — the exact trap round 4 had just fixed for §A7.5.2's diagnostic | `SELECT set_config('app.current_org_id', $1, true);` in the prologue (§A7.5.5). ⛔ **This cell said "(or as the table owner)" and also that "both floors read 0", making case 0/2a "seed 0". Both are wrong and were left standing for two rounds while this section claimed them corrected** — the owner is denied by `FORCE` and by `record_projection.py:6085-6093`, `$3` comes from the JSONL reader so no GUC reaches it, and the unset-GUC hazard is a **misclassification-to-lift** that `WITH CHECK` catches as `42501`, not a zero seed |

#### A8.3.4 The sixth pass: the rollback state had no one to enter it, and a predicate that can never be true

The fifth pass found **no P0** — the first round that did not — and verified the allocator arithmetic by hand
(`N=1,old=0 → 1`; `N=3,old=7 → 8,9,10`; last always `old+N`), which is the fix that mattered most. It then found
**4 P1 / 3 P2**, and three of them are the same shape as every round before: **a correction applied in one place
and left standing in another.**

| found | what was wrong | what it is now |
|---|---|---|
| **P1** | **`allocating` → `halted` had NO writer.** §A7.5.12's "exactly one writer" table named "rung 2's bump and rung 3's restore" — rung 1 is a *decision not to act*, rung 2 never touches `mode`, rung 3 installs the **dump's** mode. So the rollback state was entered by nothing, and under §A7.5.2's `AND c.mode = 'allocating'` predicate **the allocator kept allocating through a rollback**. §A8.2 item 8 repeated the non-existent "rung 2's bump" | **rung 0** added to §A7.5.11 — the printed `UPDATE … SET mode='halted' … AND mode='allocating'`, guarded by the quiescence assertion — and the transitions row and §A8.2 item 8 re-pointed at it. §A7.5.11's two meanings of "stop allocating" (per-graph halt vs a read-path code revert) are now stated as the two different actions they are. ⛔ **The fifth pass fixed the *exit* from `halted`; this fixes the *entry*. A state with a door out and no door in is not reversible** |
| **P1** | **the reconciliation predicate can never be true on the population the fifth pass created.** `record_cursor.seq >= max(journal_event.seq)` is spelled without `COALESCE` in five places, while the allocator's own floor is `COALESCE(max(seq), 0)`. For a case-0 graph (`seq = 0`, zero rows) `max()` over no rows is **NULL**, so `0 >= NULL` is NULL — the armed check reports a **correctly seeded fresh graph as divergent**, and the refusal arm never fires either | `COALESCE(max(seq), 0)` in **six** (the §A7.5.2 diagnostic arm, §A7.6 #12, §A7.8 item 7, §A7.8's B3 restore assertion, D-29, and #20's explanation of the #12 mis-citation). ⛔ **The sweep was reported as complete and was not** — §A7.8 item 7 and B3 still spelled a bare `max` until the seventh pass, and item 7 is the worst place to miss it: a purge leaves a counter above a graph with zero rows by design, so both of item 7's arms were neither true nor false over exactly its own population (§A8.3.5). **A round that legitimises a new state must re-check every predicate that quantifies over it** — case 0 was created by the fifth pass and this check was never re-read |
| **P1** | **the seed's execution-context requirement was prose, not SQL** — and its "or as the owner role" escape is denied twice over: `FORCE ROW LEVEL SECURITY` on `record_cursor` subjects the owner to the policy, and the record's own guard refuses *"superuser, BYPASSRLS, a member of such a role, **or the owner of a record table**"* (`record_projection.py:6085-6093`) | `SELECT set_config('app.current_org_id', $1, true);` is now the seed's **first statement**, matching the record's own doors (`20261025000001:41-46`), and **every** "or as the owner role" is gone. ⛔ The fifth pass's own §A8.3.3 row had claimed this fix; it was never applied |
| **P1** | the fifth pass's `$3 > 0` qualifier was credited with removing the constraint violation — and **it does nothing**: `$record_floor = $3` already admits `(0,0)`, so the two predicates accept exactly the same population. The actual fix was `CHECK (seq >= 0)` plus case 0 having a name | the comment now says so. The qualifier is kept only so the SQL reads the same case list as the table — **a mechanism credited with a fix it does not perform is this document's own designated defect class**, so it is labelled rather than left looking load-bearing |
| **P2** | the "Five outcomes" table had a **second header row** between case 0 and the other four — two tables, one of them one row long | duplicate header deleted |
| **P2** | §A8.2 item 7 cited `§A7.5.5 step 4`, which does not exist (the steps are `(0)`, `(1)`, `(1b)`, `(2)`, `(3)`) | `step (3)` |
| **P2** | the new context note cited `20261012000001:128` for **both** reads, but that line is `journal_event`'s `ENABLE`; `record_cursor` does not exist in that migration at all | split: `journal_event` → `20261012000001:128`, `record_cursor` → §A7.8 item 2. And `$3` is not read by this transaction, so "both read 0" is corrected |

#### A8.3.5 The seventh pass: the sweep was reported complete and had missed the predicate it mattered most for

The sixth pass found **no P0** for the second round running, and verified five of its seven findings outright.
It found the other two **claimed but not applied** — the document's own designated defect class, now asserted as
done in the very section that exists to make unapplied fixes visible. Plus two mechanism findings that the
cross-section trace surfaced.

| found | what was wrong | what it is now |
|---|---|---|
| **P1** | **the `COALESCE` sweep missed two places and reported "all five".** §A7.8 item 7 still read `cursor >= max` / `cursor > max` and B3's third sub-assertion still read `cursor >= max(seq)`. **Item 7 is the worst place to miss it** — the org purge leaves a counter above a graph with **zero** `journal_event` rows *by design*, so both of item 7's arms (healthy, and "burned by a purge") were neither true nor false over exactly the population item 7 describes | `COALESCE` in both, the count corrected to **six**, and item 7 now says why the `COALESCE` is load-bearing *there* more than anywhere else |
| **P1** | **the seed's context note argued a failure mode its own design makes impossible.** It claimed `$3` also reads 0 under an unset GUC — but `$3` comes from the replay's **JSONL reader** (`read_all_segments -> read_for_replay`), not from SQL, so no GUC can affect it. And it claimed the outcome would be a silent `seq = 0` seed, when the policy's `WITH CHECK` half makes the unscoped INSERT raise `42501` | restated to the true hazard: an unset GUC makes **`$record_floor` read 0**, which **misclassifies a 2b graph as 2a and would lift it** — and what stops that being a silent loss is the `WITH CHECK` half, not the read. The requirement (install the GUC) is unchanged; the argument for it is now true |
| **P1** | **`record_cursor.mode` has a FIFTH writer the transitions table denied.** The table said "only these four transitions exist, each with exactly one writer" — but `pg_restore --data-only` writes the **dump's** `mode` for every restored graph (§A7.8 item 4 puts `record_cursor` in `THE_RECORD_TABLES`, that tuple **is** the dump's `--table` list, `record_backup.py:1167` → `record_tables()`). A dump taken while a graph was `allocating`, restored onto a graph since halted, **silently re-arms the allocator with no operator decision** — and §A7.8's restore refusal checks only the counter | the restore gets its own row (`* → *`), naming the hazard and requiring either a `mode` guard on restore or an explicitly accepted, operator-stated consequence. **An unstated writer is how a deliberate choice becomes an accident** — which is this same item's standard for the purge |
| **P1** | **rung 0's "the fallback stays installed" could not both hold with #12's unscoped refusal.** `write_live_record` passes `position=seq is not None` (`record_projection.py:6402-6409`), so the live path **does** insert `journal_event` rows: a halted graph's counter is frozen by design while its fallback advances the table, so after the **first** fallback write `cursor < max` — and an unscoped #12 refuses the next write, stopping the very graph the rollback promises to leave working | #12's refusal is **scoped to `mode = 'allocating'`**, exactly as §A7.7 6.5 scopes the fallback removal. **A `halted` graph is expected to diverge; an `allocating` graph diverging is the bug** |
| **P2** | `set_config` was called the seed's **"FIRST STATEMENT"** when `SET LOCAL statement_timeout` precedes it (harmless, but it is a false claim in the line carrying the fix) | "first read-bearing statement", with the distinction stated |
| **P2** | §A8.3.3's seed-context row still advertised *"or as the table owner"* while its sibling row was marked superseded — so a reader of that section saw the stale cell as current | corrected, with the same marker its sibling carries |
| **P2** | §A8.3.4 said §A8.2 item 8 was "re-pointed at rung 0"; item 8's ladder enumeration never mentioned rung 0 | rung 0 added to item 8's enumeration ("rung 0 halts THE graph but repairs no counter — that is its point, not a defect") |

#### A8.3.6 The eighth pass: the audit table was wrong about ITSELF, for the third round running

The seventh pass found **no P0** for the third round running, verified four of R5's seven findings against the
artifact, and caught the rest — including the one that matters most about *this section*: **the row claiming a
cell was corrected, in the section whose job is to make unapplied fixes visible, and the cell had not been
touched.** Third round running. It also ran the fourth cross-section trace (a restore) and found that a fix this
same section had just recorded described a state the code makes impossible.

| found | what was wrong | what it is now |
|---|---|---|
| **P1** | **§A8.3.3's seed-context cell was claimed corrected and was byte-identical to the previous head** — it still offered *"or as the table owner"* **and** still asserted `"both floors read 0"` ⇒ *"case 0/2a seeds 0"*: the two exact claims §A7.5.5's SQL now denies. The previous round had edited the **sibling** cell and recorded the fix against this one | corrected, with the marker, and the row now says it was left standing for two rounds while this section claimed it fixed. ⛔ **A reader of §A8.3.3 held a picture of the seed that the seed's own SQL contradicted — and "seeds 0" is the shape that invites the over-correction R2 produced** |
| **P1** | **the `halted` state had no append arm, and under §A7.5.6's only arm the SECOND write after a halt collides.** The enumeration position is the file's line count; the AFTER-COMMIT export leaves the file trailing the table, so write #1 takes `N+1` and write #2 recomputes `N+1` from a stale file and raises `23505` on `journal_event`'s PK (§A7.5.4 dropped the conflict absorber). Rung 0's "**costs nothing**" and #12's justification both rested on a mechanism that could not take two writes — permanently if the drainer is wedged, which §A7.5.6 itself contemplates | **a halted graph's append arm is the PRE-COMMIT arm — and the arm is now scoped PER MODE in §A7.5.6 itself**, not only in rung 0, with §A7.5.10 and §A8.1's B4 cell de-universalised with it. What it says: the file leads, `EventLog.append` writes the line, the drainer idles. §A7.5.6's AFTER-COMMIT arm is a property of the **`allocating`** mode, exactly as §A7.7 6.5's fallback removal is — one arm per graph, never neither and never both. ⛔ **The row as first written claimed §A7.5.6 already scoped it; §A7.5.6 mentioned no mode at all until the eighth pass put the scope there, and §A7.5.10 and §A8.1's B4 cell asserted the universal arm** — corrected in all three (§A8.3.7) |
| **P1** | **the restore row's hazard was unreachable through the shipped verb, and its proposed guard had nothing to compare against.** `restore_record_backup` raises `RecordRestoreRefused` on any non-empty target (`record_backup.py:1698-1709`), so "a dump restored onto a graph since halted" cannot happen — and in the only shape the verb permits (an empty target) there is **no live `mode`** for "compare restored `allocating` against live `halted`" to compare against | restated to the reachable shape: the fifth writer is real, the verb's populated-target refusal is what makes the hazard impossible, and the guard is recorded as a **future** obligation on whoever lifts that refusal |
| **P2** | **"four test suites" vs five rows vs "the five suites that must move"** — §A7.7 and §A7.8.1 said four, §A8.1's M11 cell said five must move, and the table has five rows of which the fifth is explicitly *"unchanged … listed so Stage C does not assume it is"* | "the five suites Stage C must check, four of which must move", with the fifth named and its status stated |
| **P2** | **"Three descriptive statements also become false" — there is a fourth**, and it sits in the very paragraph this plan cites for the QUIESCED precondition: `durability-posture.md:405`, *"because the journal carries no `seq` its number is lost too"* | added as the fourth, **with the consequence stated**: the QUIESCED requirement survives for this plan's own reason (a live writer moves the journal between the two floor reads), not for the seq-less-file reason that document gives |
| **P2** | **`record_cursor`'s DDL omitted the `ON DELETE CASCADE` FK every sibling record table carries — and item 7's whole purge ruling silently depended on that omission.** `journal_event` and `node_identity` both declare `FOREIGN KEY (org_id, graph_id) REFERENCES public.graphs … ON DELETE CASCADE` (`20261012000001:91`, `:70`), and `purge_org_record` deletes `public.organizations` relying on that chain (`20261012000005:22-35`). An implementer following the schema's own pattern would add the cascade and **item 7's ruling would be reversed with nothing in the artifact to notice** | item 1 now states **`no` FK, deliberately, with the reason**, and item 7 points at that clause as what makes its ruling hold. ⛔ **This is item 7's own standard — "an unexamined purge is how a deliberate choice becomes an accident" — applied to itself** |

#### A8.3.7 The ninth pass: the arm was fixed in one section and asserted universally in three others

The eighth pass found **no P0** for the fourth round running, verified five of R6's six findings against the
artifact, and found that the sixth — the halted append arm — was **fixed in rung 0 and contradicted everywhere
else**, plus one genuine **gap in the plan** that no earlier round had looked for.

| found | what was wrong | what it is now |
|---|---|---|
| **P1** | **the halted arm was stated in rung 0 only, while §A7.5.6 asserted the AFTER-COMMIT arm universally** (it mentioned no mode at all), §A7.5.10 still said *"cannot happen by construction — the file gains a line only after COMMIT"*, and §A8.1's B4 cell still said *"§A7.5.6 — **AFTER COMMIT**"* with no scope. The eighth pass's own §A8.3.6 row claimed §A7.5.6 scoped it; it did not | **the arm is scoped PER MODE inside §A7.5.6 itself** (arm 2 for `allocating`, arm 1 for `halted`, no arm for a graph that is not allocating), §A7.5.10's row and §A8.1's B4 cell are de-universalised, and the **drainer** gained the rule it was missing: for a `halted` graph it drains the pre-halt backlog to `max(journal_event.seq)` and stops, and **falsifier 15's age-based lag alarm becomes mode-aware** — on a halted graph "no progress" is the expected steady state, not the failure that alarm exists to catch. ⛔ **A collision is gone, but the alarm would have fired forever on a legitimately halted graph: fixing the write exposed the monitor** |
| **P1** | **nothing seeds a graph created AFTER step 6.2.** `(no row) → seeded` had exactly one writer and one *when* — *"the cutover step 6.2"* — and 6.2 is a per-graph backfill performed **once**. So every graph created after cutover day never gets a counter row, stays "uncut" by §A7.5.12's own definition, and keeps today's allocator — the D-25 `position=None` window, the 60 s fail-soft breaker, the two-journal-files hazard — **forever**, while §A7.5.7's fail-hard posture is scoped to "the ordinal path" and never applies to it | **a graph-creation seed hook is a deliverable** (§A7.8 item 8 — it resolves to case 0, `seq = 0`, so it is cheap and cannot race a writer that does not exist yet), the transition row gained its **second** *when*, and **§A8.2 item 10** records the consequence for any window where the hook has not shipped. ⛔ **This is the first finding in nine rounds that was not a false claim but a MISSING REQUIREMENT — and it is the one that decides whether the plan fixes the past or the future: a cutover is a snapshot, not a policy** |

P2s, all sweep-misses of a fix applied one line away from where the count or pointer lives: **§A8.1's M7 cell
still said "three" descriptive sentences** against §A7.5.9's four; **§A7.5.9 pointed at §A7.5.10** for
qualifications that live in §A7.5.12; **§A7.8 item 1's FK insertion swept the two `CHECK` constraints out of the
DDL** and into a sentence that then credited them with an unrelated job; and **§A7.5.2's fail-open paragraph
repeated its own clause**, leaving an emphasis span open across the inserted parenthetical.

#### A8.3.8 The tenth pass: the fix for the missing requirement did not do the requirement

The ninth pass found **no P0** for the fourth round running and verified four of R7's six findings outright. Then
it found that the **one fix this document had called "the one that will actually matter in production" was itself
incomplete**, plus three sections still asserting the universal arm the round before had scoped.

| found | what was wrong | what it is now |
|---|---|---|
| **P0** | **the new graph-creation hook seeded and did not ARM.** §A7.8 item 8 said *"run §A7.5.5's seed for that graph"* — and that INSERT omits `mode`, so it takes `DEFAULT 'seeded'`. `seeded` means **nobody allocates** (§A7.5.12), and `seeded → allocating` has exactly **one** writer: the per-cutover step 6.3. So a post-cutover graph would get a row that allocates nothing, fall back to enumeration, and keep today's allocator — **the exact harm the hook was added to remove, relocated from "no row" to "a row with no allocator"**. §A8.2 item 10's mechanism (*"no counter row"*) was therefore also wrong | item 8 now writes `seq = 0, **mode = 'allocating'**` in the creating transaction, with the reason: a brand-new graph has an **empty journal**, so there is no backfill to wait for and no quiescence to assert — it goes straight to `allocating` rather than through the two-step the cutover needs. The transitions table gained a **`(no row) → allocating`** row of its own (one writer: the hook), and §A8.2 item 10's mechanism is corrected. ⛔ **A fix that adds the call and omits the state change is the CRUD-DEFAULT form of "a mechanism that does nothing when it runs"** |
| **P1** | **the hook appeared in no step of the sequence Stage C is told to follow** — §A7.7 step 6's sub-steps were 6.1–6.5, so a lane following it in order would ship the cutover, declare it done, and have built no creation-time caller: the exact state §A8.2 item 10 says makes *"the plan's own premise false a week after it lands"* | **§A7.7 step 6.6** added — shipped **with 6.3** (the step that makes the live writer allocate from `record_cursor`; a graph created between them would allocate from a counter that does not exist), with its rollback stated as *the last thing that may be reverted*. A **§A8.1 M16 row** and a **§A7.8.1 suite** were added with it, because a deliverable absent from the sequence, the finding index and the suite map is the same defect it was added to close, one level up |
| **P1** | **§A7.5.10's first row was still universal and is false for `halted`** — it said a rolled-back ordinal leaves *"no gap, no burned position, and the next allocation takes the same number"*, while the row immediately below it had just been scoped to `allocating`. On a `halted` graph the file already carries `N+1`, so a rolled-back write leaves a line with **no** row, the next write appends `N+2`, and the number is **not** reused — a real gap that matched **no** row of §A7.5.6's hole table | row 1 scoped to `allocating` with the `halted` behaviour stated, and **a fourth row added to §A7.5.6's hole table** for the shape it produces (`no row, no holder, counter == last_exported`) — which is the case the third row structurally cannot cover, because a halted graph's counter is frozen so `cursor > last_exported` is never true again |
| **P1** | **the mode-aware alarm had no falsifier, and it was scoped to the wrong set.** Falsifier 15 was **not edited at all** — so an implementation ignoring `mode` passes it unchanged — and the rule said "suppressed in `halted`", while under item 8 a post-cutover graph sits in **`seeded`** permanently: the alarm would fire forever on every future graph, i.e. the failure the fix was written to prevent, relocated | the rule is now *"**armed only in `allocating`**"*, and falsifier 15 gained **(c) a PAIRED assertion** — does **not** fire on `halted`, does **not** fire on `seeded`, with an equivalent induced stall on an `allocating` graph going **red in the same run**. A one-sided test passes an implementation that suppresses the alarm everywhere |
| **P2** | §A7.5.6's **heading** and its **"Before COMMIT"** bullet still stated the universal arm — and the bullet was the argument a rollback reader would use to conclude that rung 0 reintroduces `#9190` | heading retitled per mode; the bullet scoped to `allocating` with `halted`'s exemption argued (*the position is the file's own count, which the written line already advanced, so the next write takes `N+1`, not `N`*) |
| **P2** | the halted drainer rule named `max(journal_event.seq)` as its target — **a quantity that keeps rising** on exactly the graph the rule is for, since the file leads and post-halt writes keep committing rows. Read live, it appends a second line for positions the file already carries | the target is the **frozen `record_cursor.seq`**, with the consequence stated (two records at one `seq` would shift the enumeration that is the halted graph's own position source), plus the bookkeeping that was missing: **on a halted graph the writer is the exporter** |
| **P2** | §A8.3.6's halted-arm cell carried **a literally duplicated clause**, in the audit table this round is about | deduplicated and the emphasis span closed |
| **P2** | **§A7.5.1's allocator snippet — the first SQL a reader meets — lacked `mode = 'allocating'`.** Copied verbatim it allocates on a halted graph, undoing rung 0, §A7.5.12's table and falsifier 17(b) | the snippet now carries the predicate **and** the divergence guard, and is labelled *illustrative of the transactionality only — §A7.5.2 is the allocator*. ⛔ **The mirror of this document's designated defect — a printed statement that does something it must not — is worse than one that does nothing** |

#### A8.3.9 The eleventh pass: four of the previous round's fixes were never written, and the audit said they were

The tenth pass asked whether the fixes *did what they were written for*, and found the hook's `mode`. The eleventh
pass asked the next question — *is the fix in the file?* — and found that **four of the tenth round's fixes were
never written at all**, while §A8.3.8 recorded all four as applied: the hole table's fourth row, the drainer's
frozen-counter target, falsifier 15(c), and §A7.5.1's snippet.

**The cause is mechanical, and it is worth naming because it will happen again: the edits were applied by one batch
script whose fourth replacement did not match, so the script aborted before writing — and its output said nothing
that distinguished "aborted" from "applied".** The author read the *intent*, not the file. Every later check
consisted of reading the audit row, which was written from the same intent. **A batch tool that fails atomically
looks exactly like one that succeeded, to a reader who does not re-open the artifact.**

| found | what was wrong | what it is now |
|---|---|---|
| **P0** | **the hook's carrier does not exist.** *"In the same transaction that creates the `:Graph`"* has no referent: the `:Graph` is a **FalkorDB** node, and on the record-armed lane graph creation is a **PostgREST insert with a compensating delete and no open transaction** (`hosted_api.py:27179` → `supabase_control.py:4184` → `_rollback_graph`), plus a **second** best-effort site for the default graph (`_mint_default_graph_row_best_effort`, *"never fails the caller"*). An implementer would have had to invent the carrier, and at the best-effort site it fails **silently** — the graph is born uncut forever | §A7.8 item 8 now names the carrier: **one Postgres RPC that inserts the `public.graphs` row AND `record_cursor(seq = 0, mode = 'allocating')` in ONE transaction**. ⛔ **This cell printed the pre-`691c65aed0` form** — signature `provision_graph_record(org_id, graph_id)`, and an un-scoped fail-hard that *"the best-effort mint must not swallow it"* — and both were defects: the signature could not carry the row's columns, and the un-scoped rule reversed `#10025` F1 at `provision_org`/`provision_org_with_token`. **The CURRENT rule is at `:1826`:** fail-hard for custom-graph creation only, and a reconciliation sweep for the default graph. Annotated rather than rewritten, because the record of what was wrong is the point (§A8.3.11). ⛔ **This is the same unbuildable cross-store claim for which §A7.7 step 4 deleted D-07**, one deliverable later |
| **P1** | **the `halted → seeded` re-cut could never complete.** It routed through `seeded` and relied on `seeded → allocating` to finish — but `seeded` means *nobody allocates*, the only `seeded → allocating` writer is the **per-cutover** step 6.3, and this table's own text calls arming a consumed counter *"how the same ordinal gets issued twice"*. One route parked the graph permanently on today's allocator; the other armed an **unverified** counter, because 6.3 carries no floor check | **`halted → allocating` is now ONE transition with ONE writer** that recomputes the floor and arms together, refusing unless the floors are equal (the §A7.5.5 healthy-case rule). Differing floors leave the graph **`halted`** — no partial move, no `seeded` limbo. `halted → seeded` is retained but explicitly **not** the re-cut: it is for a graph whose counter is discarded and whose whole cutover is re-run |
| **P1** | **§A8.1's preambles misdescribed their own tables** — *"Material (M1–M16)"* over M1–M15, and *"Blocking (B1–B7)"* over eight rows, because M16 had been appended to the **blocking** table | M16's home is stated as the Material table, and the defect is named in place — a count over a population, in the section whose job is *"the table is the proof none was dropped"* |
| **P1** | **the counts over step 6 and §A7.8.1 were not swept** when 6.6 and the sixth suite were added: step 6 was still *"in five sub-steps"* in two places, M11 and §A7.8.1 still said five suites, and **§A7.7's rollback map still enumerated 6.1–6.5**, omitting the one sub-step whose own text says it is the last thing that may be reverted | six sub-steps, six suites, and 6.6 added to the rollback map. ⛔ **A lane trusting "the five sub-steps" would land 6.1–6.5 and no creation-time caller — re-creating the exact harm 6.6 exists to remove** |
| **P2** | §A8.3.6's cell had an orphan `**` where the deleted duplicate used to be (13 markers, odd) | closed |
| **P2** | §A7.5.12 said *"Four transitions exist"* over a table of five | five |

#### A8.3.10 The twelfth pass: TWO REAL DEFECTS AND NO P0 — and one of them was a recorded decision

The twelfth pass found **no P0**, and verified **nine of R9's ten** findings present in the artifact — the four
that had been lost were the ones this round re-applied and checked by `grep`. It then found **5 P1 / 3 P2**, of
which two are the most consequential of any round in this section, because both are **recorded decisions**.

| found | what was wrong | what it is now |
|---|---|---|
| **P1** | **§A8.1's preambles were still wrong, for the third round** — M16's row was in the **Blocking** table (preamble: B1–B7) while the Material preamble claimed M1–M16, so **both headings misdescribed their tables**. The previous round had added a *note* saying M16 must move rather than moving it | M16 moved into the Material table (16 rows), the Blocking table back to 7, and both preambles now describe the populations they head. ⛔ **A note that a row must move is not the move** |
| **P1** | **the fail-hard rule silently reversed `#10025` F1.** §A7.8 item 8 said graph creation *"must fail"* and *"the best-effort mint must not swallow it"* — but `_mint_default_graph_row_best_effort` (`supabase_control.py:4391-4400`) is a **recorded decision**: *"never failing the caller (#10025 F1)"*, *"auth must not fail because a control-plane write did"*, reached from the **auth** path. Reversing it turns a record-DSN failure into a **login** failure — and §A7.5.9 says the two reopens it names are the complete set | **scoped to the two provisioning sites**, where creation already fails the caller; the auth mint **stays best-effort**, and the consequence is stated rather than hidden: **an org's default graph may be born uncut**. Named as **§A8.2 item 11** with both remedy routes (a post-login reconciliation, or a reopen of F1 with its auth-outage cost priced). ⛔ **A plan may not absorb a recorded decision by writing a stronger adjective** |
| **P1** | **§A7.5's "two things it does not authorise" omitted the ruling's THIRD exclusion.** The owner comment lists three (*bounded history*, ***what a journal record contains* beyond the ordinal**, the graph's role); the plan named two — while §A7.8 item 6 (**M15**) widened `journal_event` with `event_id`, `initiated_by`, `projection_version` and required `node_label`/`node_key`, on the critical path before step 6.1 | the third exclusion is stated, and item 6 is named as **a separate decision that must be taken on its own issue before 6.1**, not read out of the §A7.5 ruling |
| **P1** | **the new fourth hole-table row was unreachable.** It said a halted rolled-back write leaves the file at `last_exported + 1` — contradicting the writer rule three lines above, which has the halted writer **append the line and advance `last_exported` with it**. After a successful export the file's last line sits **at** `last_exported`; what a rolled-back record leg leaves missing is a **`journal_event` row**, not a file line | the row is kept and **restated to say it is NOT a drainer case** — the hole a halted rollback leaves is in the **RECORD**, healed by a later replay's enumeration, and the drainer cannot stall on it. ⛔ **The previous round added that row to prevent a stall that does not exist; deleting the false row and naming where the hole actually lives was the fix** |
| **P1** | §A7.5.12's transition count was off by one against its own table — "five transitions" over six transitions plus a non-transition, because **the count moved from four to five in the same round the population moved from five to six** | "six transitions exist … plus one writer that is not a transition" |
| **P2** | §A7.5.1's new guard was labelled `-- loud, not repaired here`, but a `WHERE` that matches no row **raises nothing** — the loudness lives in §A7.5.2's rowcount assertion | relabelled: *silent on divergence; §A7.5.2's assertion is what makes it loud* |
| **P2** | `provision_graph_record(org_id, graph_id)` cannot insert the `public.graphs` row the same sentence says it inserts — that insert needs eight columns | the signature carries them: `(org_id, graph_id, name, kind, namespace, created_at)` |
| **P2** | §A7.7 **step 6.6** still said *"in its creating transaction"* — the phrase R9's P0 found unbuildable — although item 8 now names the RPC | the step names the RPC and both chokepoints. **The step is where the work is scheduled, so leaving the old phrasing there re-opens the gap for a reader who starts at §A7.7** |

#### A8.3.11 The thirteenth pass: the same reversal, one site over — and a false clause that hid it

The twelfth pass found **no P0** and verified nine of R9's ten findings. It then found **one P0 and two P1**, and
the P0 is the sharpest single finding in this section — because **it was a fix for the previous reversal, and it
carried the same reversal one site over.**

| found | what was wrong | what it is now |
|---|---|---|
| **P0** | **the fail-hard scoping still reversed `#10025` F1.** The previous round scoped fail-hard to *"the two provisioning sites"* and left *"the third site"* (the auth mint) best-effort. **But the mint is not a third site — it is the CARRIER of one of the two:** `provision_org` (`:3239`) and `provision_org_with_token` (`:3308`) both call `_mint_default_graph_row_best_effort`, and the default graph row is minted *only* through it (`ensure_default_graph_row` → `insert_graph` at `:4304`, whose only caller is the mint). So *"fail-hard at the two provisioning sites"* **made the mint fail-hard** — the exact `#10025` F1 harm, and worse than the login failure the previous round priced: the code records that a raise there *"would cost an account"* | **the real site map is printed**: custom-graph creation (`hosted_api.py:27179`, compensated by `_rollback_graph`) **may** fail hard; the default graph row is reachable **only** through the mint and must stay best-effort at **all three** callers. The default graph's counter row is therefore obtained by a **reconciliation sweep**, not by failing creation. §A8.2 item 11's residual is **widened** from "minted during login" to "minted at **provisioning or** login". ⛔ **The false clause — "where creation already fails the caller" — is deleted, because one of the two sites is the one that must not** |
| **P1** | **§A8.3.9's P0 row printed the reversed rule as current** — the pre-`691c65aed0` signature *and* the un-scoped fail-hard (*"the best-effort mint must not swallow it"*) — with no superseded marker, in the section that annotates superseded cells elsewhere | annotated in place, pointing at what the current rule is and saying the record of what was wrong is the point |
| **P1** | **the boundary's completeness record was wrong three ways at once:** it said *"Two things"* while listing three, it labelled the record-contents exclusion *"the ruling's own third"* (it is the **second**), and **the ruling's actual third — the graph's role — was stated nowhere** while §A8.3.10 reported the set as closed | all **three** exclusions are now stated, each mapped to what the plan touches (B7 refuses a table bound; M15 **acts** on record contents and so needs its own decision before 6.1; the graph's role is untouched and said so), the labels are corrected, and the `:GraphEvent` fence (B6) is separated out as **this plan's own** rather than the ruling's |

P2s, all sweep-misses of a fix applied one line from its consumer: M16's cell cited **§A8.2 item 10** for the
default-graph residual (it is item 11); the restated hole-table row and §A7.5.10 row 1 cited **row 2** of §A7.5.10
for the heal-by-enumeration when the phrase is in **row 4**; *"Four descriptive statements"* was still four when the
**shipped schema's own `COMMENT`** (`20261012000001:94`, *"applying a seq twice is a **no-op**"*) is a fifth that
§A7.5.4's live path falsifies; §A8.3.10 attributed its own verification to *"the eleventh pass"*, which its
predecessor records as having found the P0; and **§A7.7 step 6.1** scheduled the item-6 widening as a precondition
without the decision gate §A7.5 requires before it.

⛔ **What the thirteenth pass proves, and it is the round that justifies this whole section: a fix can carry the
very defect it was written to remove, one site over, and the record of the fix is what hides it.** The previous
round removed a reversal of `#10025` F1 by scoping it — and the scope named a population that *contained* the site
it meant to exclude. **A scope is a claim about a population**, and this is the fifth time in thirteen rounds that a
claim about a population was the defect.

---

⛔ **What the twelfth pass proves, and it is the first round whose findings were about DECISIONS rather than
mechanics: a plan can reverse a recorded decision without ever using the word "reopen", by writing a rule that is
simply stronger than the one on record.** Two of this round's findings are exactly that — a fail-hard clause that
quietly overrode `#10025` F1, and a boundary claim that was short by the one exclusion the plan then acted on. The
contradiction test in the standing rules catches them only if the boundary list is **complete**, which is why the
third bullet is now printed rather than implied.

---

⛔ **What the eleventh pass proves, and it is about the tooling rather than the document: a fix record written from
the same intent as the fix cannot detect a fix that was never made.** Four rounds have now found an audit row
asserting an unapplied fix, and this is the first one where the mechanism is visible — a batch edit aborted
atomically and reported success for the whole batch. The rule that follows is not "write better records": it is
**re-open the artifact after every write, and treat a batch's exit status as no evidence about its individual
members.**

---

⛔ **What the tenth pass proves: the tenth round is where a fix's own premise gets tested, and it failed.** For four
rounds the document was fixed by adding mechanisms; this round's P0 was that the new mechanism's *state* was
wrong — a `DEFAULT` in a DDL, three lines from the sentence that argued the row would allocate. **Every previous
round asked "is this fix applied?"; this one asked "does the fix, as applied, do the thing it was written for?"**
That is the question a `DEFAULT` answers badly, because nothing in the diff has to change for the answer to be
"no".

---

⛔ **What the ninth pass proves: a fix stated in the section under review is not a fix of the SYSTEM, and the
review record is not a substitute for the artifact — for the fourth round running.** Five of six findings were
either an assertion left standing three sections away, a count not swept with its own edit, or an audit row
claiming a scope that had not been written. The one that will actually matter in production, though, is the
**missing requirement**: no round before this one asked *"what about the graphs that do not exist yet?"*, and the
answer was that the plan silently excluded them.

---

⛔ **What the eighth pass proves, and it is worse than the seventh's lesson: this section is now the least
trustworthy part of the document.** Three rounds running, at least one row asserted a fix that had not been
applied — and the seventh pass's row about §A8.3.3 was itself about the previous round's identical failure. A
reader should treat every "what it is now" cell here as **a claim with a path:line**, not a conclusion: open the
line. That is why each row above names one. **An audit table is not evidence. It is an index to evidence.**

---

⛔ **What the seventh pass proves, and it is the least comfortable lesson here: two rounds in a row the largest
defect was not in the design but in the REVIEW RECORD.** The document's audit tables are what makes unapplied
fixes visible — and the last two rounds each contained a row asserting a fix that had not been made. That is the
marker problem one level up: **a record that says a fix was applied is not evidence that it was**, and the only
check that works is re-opening the artifact. Every round in this section is now written so the next reader can
re-open it rather than trust it.

---

⛔ **What the sixth pass proves: a fix round must re-read the predicates that quantify over the state the last
fix created.** The fifth pass made `seq = 0` with zero journal rows a legitimate, designed state — and left five
reconciliation predicates that are NULL over it, one of them the armed check B5/B6 is built on. That is the same
lesson as the fifth pass, one level up: **the consumer of a changed invariant is not always in the same section,
and it is not always a function — sometimes it is a WHERE clause.**

---

⛔ **What the fifth pass proves, and it is the sharpest lesson in this section: fixing the section you are
pointed at is not the same as checking the system.** Four consecutive rounds reviewed §A7.5.5's *admission*
rule — its predicate, its population, its scope — and found a real defect each time. **None of them read the
`RETURNING` clause eight lines above it**, which made every one of those fixes moot: a perfectly-admitted seed
still hands its first position to a collision. The question that would have caught it is not "is this fix
right?" but *"what consumes this value, and does that consumer agree with the producer?"*

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

⛔ **A fourth unqualified sentence exists, found by the Stage-B review (M13), and it is the only one that
contradicts Q1 at the CONTRACT level rather than the architecture level:** `docs/INGEST_CONTRACT.md:463-467` —
*"the default embedded mode has no AOF window, so the JSONL journal is the durability contract"*. Read plainly,
that says the journal **is** durability, for the embedded lane, in the document customers' integrations are
written against. It is not obviously wrong — on default embedded there is no AOF window, so the journal is what
survives — but it is the same sentence class as `STORAGE-ARCHITECTURE.md:361`, and it is **worse placed**,
because a customer reading a contract will read it as a promise. Recorded here rather than edited: it is a doc
change with its own review (and it may need a *qualification*, not a deletion, since the embedded claim is
operationally true). §A7.3 lists it as untouched by A7.

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

**Stage B round 2 re-attempted rung 1 and got the same answer, for the same reason.** `mcp_catalog` again lists
exa, brave-search, playwright-browser, gemini, tortoise and search-console — **no `seo-intelligence`** — so
`mcp__seo-intelligence__perplexity_search` could not be called. **RUNG TAKEN: 2** (direct Perplexity API,
`sonar`, 2 queries, ~$0.01; no `sonar-deep-research` and no `sonar-reasoning-pro`). ⛔ **The error is recorded
as a class, not a stack trace: the server is ABSENT from the catalog** — the rung-1 route is not merely
failing this session, it is not installed for this box, and the next lane hits the same wall unless it is
fixed in the MCP configuration rather than at the query.

### 9.2 What each source gave, and whether it was verified locally

| Claim taken | Source | Verified locally? |
|---|---|---|
| The transactional outbox exists to prevent the dual-write failure (commit-then-publish-fails, or publish-then-rollback), and delivery is normally **at least once** so consumers need durable event IDs and idempotent handlers | Debezium's outbox documentation (`debezium.io/documentation/reference/stable/integrations/outbox.html`) | **YES, in this repo's own vocabulary**: `journal_event` keys `(org_id, graph_id, seq)` as *"the idempotency key: applying a position twice is a no-op"* (`record_projection.py:5999`), and `ON CONFLICT DO NOTHING` is the idempotent handler. The record **is** an outbox under this definition. |
| Same-transaction projection is the right choice **only** when the projection is local, small and operationally essential — otherwise it couples every schema change to the write path and multiplies lock contention | same | **PARTIALLY**: this repo already pays that cost knowingly — the record's plan emits layer rows *and* the position row from **one** plan (`replay_plan`). The map records the cost as `ρ` (§17.4), measured. No new claim is made from this. |
| PostgreSQL sequences are **nontransactional**: `nextval()` advances immediately, is not rolled back, and concurrent callers do not wait — so a sequence cannot provide a gapless or **commit-ordered** ordinal, and a consumer seeing 11 may wait forever for 10 | `postgresql.org/docs/current/sql-createsequence.html`, `…/functions-sequence.html` | **PARTIALLY, and my first pass got this wrong** (corrected in place rather than restated): I claimed the record schema had *no sequence at all*; it **has one** — `public.relation.id` is `bigint GENERATED ALWAYS AS IDENTITY` (`20261012000003:167`), whose ACL split is documented at `20261025000001:205-212`. What is true is narrower and is the load-bearing half: **`journal_event` has no sequence and no id column** — its key is the journal's position — and the one sequence the record does carry is a **surrogate**, where gaps are harmless, which is exactly the property `seq` cannot have. The claim that a sequence is nontransactional is PostgreSQL's own documented behaviour and is **not** independently re-measured here — stated as external. |
| A **transactional counter row** (`UPDATE … SET c = c + 1 RETURNING`) gives gapless committed numbering by serialising on one row, at a throughput cost | same | **PARTIALLY — and the cost half is NOT a lateral move** (corrected in place, `§A7.5`): the journal does serialise per graph on one file handle (`thread lock + flock + one write(2)`, `log.py:594-620`), but `flock` covers only the seal read-modify-write plus one `write(2)` and is released before the API, record and cache legs, and it is **abandoned** after a 5 s timeout (`_FLOCK_TIMEOUT_S`, `log.py:625-633`). A row lock held until `COMMIT` across six statements is therefore a **new** bottleneck, not an equivalent one — quoting this row as reassurance re-asserts the equivalence `§A7.5` removes. The row-lock **semantics** remain unmeasured here (no Postgres bench was run — a bench is a real cost and A7.5 is a proposal, not a landing). |
| Migrating an app-written WAL into the store: deploy the **new reader first**, backfill, dual-write, reconcile by event identity, cut readers, then writers, then retire; and state whether the order is **allocation**, **commit** or **publication** order because they are not interchangeable | OpenSearch migration-assistant architecture; Kafka topic-migration practice (`acemq.com/guides/kafka-migration-guide`); `architecture-weekly.com/p/the-write-ahead-log-a-foundation` | **YES, and it maps onto choices this repo already made**: `#12459` plans exactly this shape for a format change (readers before writers, per-record dispatch, append-only ⇒ both shapes readable forever, `:824`'s silent-drop warning is the falsifier); `#8526` is the reader-first arm for the self-host wiring gap. The paper trail is in-tree, not imported. |
| Stored vectors: pgvector and every comparable delegate the vector to a store rather than a versioned on-disk format | (carried from `#12459`'s own survey, cited not re-run) | **YES**: `20261109000001_record_embedding_vector.sql` + `#11642` — the `embedding_vec vector(384)` GENERATED column is exactly that delegation. |
| **Block allocation of a contiguous ordinal range in ONE statement** (`UPDATE counter SET next = next + N RETURNING next − N`): the block is reserved by one `UPDATE`, the row lock is held to transaction end, a *transactional* counter rolls the increment back (so no block is burned), and **allocating a block in one transaction and using it in another turns the unused tail into holes in an idempotency key** — which is why a hi-lo cache must not be used for a key consumers deduplicate on | PostgreSQL manual — `UPDATE … RETURNING`, explicit locking, transaction isolation, `CREATE SEQUENCE`, and the sequence-functions page (which states a sequence **cannot** provide gapless numbering and that `nextval()` is not rolled back) — **taken for Stage B round 2, M8/B5** | **YES, in this repo's own terms**: `journal_event`'s key is the position and the schema calls it *"the idempotency key: applying a position twice is a no-op"*, so a hole is a **visible loss**, not slack — which is why the design takes a **transactional** counter and refuses a hi-lo cache (§A7.5.2). `relation.id` is the counter-example that proves the distinction: a surrogate may take gaps (`20261012000003:167`). |
| **Reader-first migration of a coordinate**: expand the readers, backfill, run a **mixed-version window** in which both encodings are accepted, cut the writers over, then contract; and **never run two independent allocators for one ordering domain** — the correctness boundary is *allocator ownership*, not the sequence's atomicity | Martin Fowler, `ParallelChange` (expand/contract); PostgreSQL `CREATE SEQUENCE` + transaction-isolation pages; Kafka's design notes on partition ordering vs. producer idempotence — **taken for Stage B round 2, M6/M11/§A7.7 step 6** | **PARTIALLY** — the pattern is in-tree as well (`#12459`'s readers-before-writers and per-record dispatch, `:824`'s silent-drop warning as its falsifier), and the *ownership* half is enforced mechanically here rather than by convention: the `record_cursor` **row lock** makes one allocator per graph a lock-ordered fact (§A7.5.1), and the absent-row rule makes "this graph is not cut over" a **state**, not a flag (§A7.5.12). **What is NOT verified locally:** that a long mixed-version window is safe for *this* system's readers — that is §A7.6 #6's falsifier, not a measurement. |

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

- **No product code.** No migration, no allocator, no test was written or edited. The **nineteen live**
  falsifiers in §A7.6 are specified, not implemented — **#19 is withdrawn and its number left as a hole**, which
  is itself part of what this lane did: a falsifier that could only pass or go red against its own spec was
  removed rather than relaxed (§A8.2 item 7).
- **No reopen filed, and the distinction now matters (Stage B round 2).** The **allocator** reopen is **already
  RULED by the owner** (`#9190` comment `6902871297`), so A7.5 implements it on paper. The **two reopens this
  revision newly identifies** — `record_writer.py:78-101` decision 3 + the live door's `OVERRIDES` (M7-a) and
  `:116-123` decision 5's *"nothing renumbers"* (M7-b) — are **named with their route and NOT posted**, because
  the route is a decision-list amendment on `record_writer.py`'s own header, authored as a reopen with this
  evidence. ⛔ **Two things the ruling does NOT authorise, and this revision did not take:** a `:GraphEvent`
  coordinate change (B6 — §A7.1 now says it keeps its own positions) and a retention bound on `journal_event`
  (B7 — D-27 shows it is a correctness question, and §A7.5.12 refuses it). Either would be a **new reopen**.
- **No bound chosen for the journal** (`#9025`) and **no absorption of `#9025`/`#12762`** — both were out of
  scope by the brief, and the map records their state without deciding them. **B7 strengthens the refusal:**
  a bound on `journal_event` is not `#9025`'s question at all, and §A7.7 step 7 says so explicitly.
- **No product-level ruling taken on fail-hard (M10) or on the GUC repair (B2).** Both are stated with their
  prices and both are listed in §A8.2 as unresolved, because each is an availability/operability decision that
  belongs to the owner, not to an archaeology lane.
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
- **152 index documents not read**: 105 by a stated exclusion rule, **47 named individually with a reason** in
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

**Stage B round 1 — a different instrument, and it found a different class.** Nine fresh-context cycles above
were asked *"is this document accurate?"* and they answered it: the map is accurate, the arithmetic
reproduces, the citations are (now) correct. **Three further fresh-context reviewers were then asked a harder
question — *"can this be BUILT?"* — and the answer was no.** Seven blocking defects, four of them **silent**,
and fifteen material ones. ⛔ **That is the most important lesson this document contains, and it is not about
citations: a document can pass nine accuracy cycles and still describe a mechanism that cannot be built,**
because accuracy review checks statements against sources and mechanism review checks statements against
each other. Every one of the seven was a **contradiction between two true sentences** in the same plan — a seed
rule that derives the number the plan forbids deriving; a rollback that does not exist beside an append-only
trigger; a sentence (:A7.1) that its own A4.5 refutes; a surface count that omits a store.**No amount of
re-reading a true sentence finds that.**

**Stage B round 2 (this revision)** answers each of the seven with a mechanism and a falsifier, and this
section is the honest record of what the *revision itself* got wrong and had to correct:

| what the round-2 revision found while answering | where it was wrong | what changed |
|---|---|---|
| the findings' own citation `record_projection.py:6005` for `ON CONFLICT DO NOTHING` | **drifted** — it is `:6008` | corrected in §A7.5.4; the finding's conclusion was unaffected |
| the findings' M15 list of dropped keys (`type`, `ts`, …) | **imprecise** — `type` and `ts` **survive as the `event_type` and `recorded_at` columns**; the truly dropped keys are `event_id`, `initiated_by`, `projection_version`, plus two never-inserted node columns | §A7.5.8 records the correction and **keeps — sharpens — the finding's conclusion** |
| the plan's own A5 citation `write_live_record` at `:1541` | **wrong** — that is a `:param` line; the `def` is at `:2270` | §A5 corrected |
| A4.1's *"a recount, never a wrong answer"* | **true of the position, false of the seed** | scoped in §A4.1 and §A2.7, carried as D-28 |
| D-20's generalisation that *"the record's read path does not depend on the journal"* | **false** — `record_serve.read_node` and `rebuild_from_record` both fold it | narrowed to the transcript read, and **the inference ("a precondition A7 does not have to build") withdrawn** |
| `log.py`'s opaque tail cursor cited as `read_after` | **drifted** — `read_after` *consumes* a token; `cursor_at_end()` returns one | cited correctly in §A4.1 |
| `config.py:92` as the base-dir config (D-24) | **drifted** — the env constant is at `:84`; `:92` is the resolver | **corrected in D-24's own row** (an earlier draft of this table said "not yet corrected"; that was true when written and is not true of the committed head — the row now cites `:84`) |
| M13's “four citations point at blank lines” | **cannot be reproduced at this head** — an exhaustive re-audit of **160** resolvable `path:line` citations returns 2 flags, both inside this audit table quoting the pointer it corrected | §A1.4's four-row table records the verdict per sub-claim rather than accepting or dismissing the finding wholesale |

⛔ **The last row is the one to keep.** A review finding is a **claim, not a fact** (and this document's own
cycle history is the evidence): two of M13's four sub-claims were already fixed before round 1, one was still
true, and one could not be reproduced at all. **Applying all four as written would have re-edited three
correct passages** — which is how a false correction propagates, and why every row above was re-verified
against the source before it was applied.

**The cumulative lesson is worth stating plainly, because it is the artifact's own thesis:** an archaeology
document is only as good as its pointers, and pointers decay the moment you edit the document around them.
Ten cycles found exactly one error in the *shape* the map describes (the row above, contradicted by the map's
own D-26); everything else was citations — a blank line, a mid-set range, a quote with a swapped noun. That
is a fair estimate of the defect rate to expect in any un-reviewed citation layer, and it is why the map's
claims are worth more than its pointers. Rows from every cycle are in the table below, in the order they were found.

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
| ⛔ **A7.5's window table said "journal export commits, record fails" cannot happen today, while D-26 — this document's own row — exists because `#9390` shows it does** | ✅ (`STORAGE-ARCHITECTURE.md:1672-1698`; `#9390` OPEN; the append precedes the state write) | the row now says it happens today and that the **flip** is what removes the class, which is a stronger argument for A7.5 than the row carried before |
| `durability-posture.md:500-560` was cited twice for material at `:575-581` (the five-row deployment table; the record's own row is `:577`), and `:500-560` is the embedding rule and the `:Source` field-split table | ✅ (`sed -n '573,584p'`) | both pointers re-pointed at the deployment table and the record's posture section |
| two quotes in `§A2.6` were placed at `:91-101`; they are at `:85-87`, and `:91` opens the next bullet's subject | ✅ (`sed -n '85,87p'`) | corrected |
| cycle 10 attributed the "journal without record" class to `#9390`; that orphan carries **both** rows (the append runs before the `:Session` MERGE) and lacks only the **graph node** | ✅ (`sdk.py:4085` vs `:4154`; `_emit_event` → `_write_record_projection(event, seq=seq)`) | the class is stated for its ordinary cause (a failed projection, counted in `record_write_failures`); `#9390` moves to the row it belongs to and D-26 is corrected with it |
| `write_live_record` was cited at `record_writer.py:1541` and called with `position=`, which is not its kwarg | ✅ (`write_live_record(record, *, durable, seq, tenant)` at `:2270`; `position=` belongs to `RecordWriter.write` at `:1519`, `:param` at `:1541`) | three call sites and one table row corrected to the real API shape |
| Stage B's edits left `03-schema.md` / `06-cache-projection.md` as body rows of a table headed "not read" (both are READ) and described `docs/INGEST_CONTRACT.md` as "now read" without counting it | ✅ (both are `§A1.1` rows 30/33; INGEST now has `§A1.1` row 42 and an index row of its own) | accounting re-derived: read **45 = 26 + 19**, unread **152 = 103 + 2 + 0 + 47** |
| §12's "cumulative lesson" paragraph appeared twice after an earlier insertion | ✅ (two copies, differing only in "Nine/Ten cycles") | the stale copy is removed |

⛔ **The most important thing this cycle did was make the shape harder, not easier.** Two findings (D-25,
D-26) are cases A7.5 had **assumed away in both directions** — a record write with no journal position, and a
record whose graph node never landed — and one (§A2.6, `#10000`) removed an assumption in A7.1: **on the armed
lane the graph holds one `:GraphEvent` carrier per turn and essentially no `:Point` nodes at all**, so "the
graph is a derived cache" is too charitable a description of what is actually there.

**Three documents were read *because* the review found them unclassified or mis-summarised** —
`docs/evidence/10000-turns-bytes/README.md`, `docs/evidence/9558-record-write-cost/README.md`,
`docs/evidence/7950-capstone/README.md` — and all three changed a conclusion (A2.6, D-01, A7.5). **That is the
measure this lane should be judged on:** not whether the first draft was right, but whether the artifact that
ships says what the sources say.
