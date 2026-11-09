---
title: "The vector move — measured on pgvector 0.8.7 / Postgres 16.15 (#11642)"
type: evidence
domain: platform
doc_status: live
created: 2026-11-09
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: search
aboutObjects: tortoise
---

# The vector move — what was MEASURED, and what was only QUOTED

`#11642` implements `#4051` **V1** (`docs/architecture/SEARCH-ARCHITECTURE.md`,
owner decision 2026-11-07): the vector index and its payload live in Postgres.
This file is the measurement record, kept separate from the decision so the two
can be corrected independently — and it names, for every figure, whether it was
**taken here** or **quoted from another issue**.

Everything under "taken here" ran against a real container:

- `pgvector/pgvector:pg16` → `PostgreSQL 16.15`, `vector` **0.8.7** — the same
  extension version `STORAGE-ARCHITECTURE.md` §12.1's first-party latency
  measurement used, so the figures are comparable.
- `falkordb/falkordb-server:v4.20.6` for the graph-side probes.
- The migration DDL was applied **verbatim** to stand-in tables carrying the
  real `embedding jsonb` column, and re-applied to prove idempotence.

---

## 1. The build — and its sizing against `maintenance_work_mem`

20,000 vectors × 384 dimensions = **30.7 MB** of raw vector bytes
(1,536 B each). Corpus: 50 centroids + gaussian noise, so it has real
neighbourhood structure rather than the degenerate near-ties a uniform random
corpus produces in 384 dimensions.

| setting | build time | outcome |
|---|---|---|
| `maintenance_work_mem` = **64 MB** (the Postgres DEFAULT), `max_parallel_maintenance_workers` = 2 | **4.0 – 4.2 s** (three consecutive rebuilds) | no spill notice. Index **39 MB**, heap 32 MB |
| `maintenance_work_mem` = **4 MB** | **37.4 s** | pgvector prints `hnsw graph no longer fits into maintenance_work_mem after 1866 tuples` … `HINT: Increase maintenance_work_mem to speed up builds` — **and the build COMPLETES** |

**⇒ The build degrades in TIME, never into an OOM.** That is the answer to the
lane's "a build that OOMs the container is a finding, not a retry": at 9× less
memory than it wants, pgvector spills and finishes rather than being killed. The
practical rule is `maintenance_work_mem` ≥ ~2× the raw vector bytes — ≈62 MB at
20k vectors, ≈619 MB at 200k — to stay on the fast path.

## 2. The backfill

`ALTER TABLE … ADD COLUMN embedding_vec vector(384) GENERATED ALWAYS AS (…) STORED`
over 20,000 existing rows: **6.9 s**, and it computed all 20,000 (verified:
`count(embedding_vec)` = 20,000 after the rewrite). A generated column needs no
separate backfill statement, which is why the migration has none.

## 3. The generated column's guards — the one place a cast would LOSE a write

`(embedding::text)::vector` — the obvious inline cast — **raises** on a JSON
array containing nulls:

```
ERROR:  invalid input syntax for type vector: "[null, null, … ]"
```

and the raise loses the WHOLE INSERT, not the row. So the converter
(`public.record_embedding_vector`) tests every element and returns NULL instead.
Verified over six shapes: 384 numeric → a vector; 768 numeric → NULL; `"string"`
→ NULL; JSON `null` → NULL; `[]` → NULL; 384 × `null` → **NULL, no exception**.

## 4. Ranking parity — the one thing the issue forbids changing silently

The graph index this replaces was created with `similarityFunction: 'cosine'`,
so the Postgres indexes are `vector_cosine_ops` and the leg returns the same
`1 - cosine` **distance** the Cypher YIELD produced. On the 20k clustered
corpus, HNSW top-10 vs an exact scan forced with `enable_indexscan = off`:

- **25 of 25 probes returned an IDENTICAL top-10 list**
- **250 of 250 members overlapped**

(For contrast, on the uniform-random corpus the same comparison is 0 of 25 —
which is why the corpus is stated: in 384 dimensions a uniform corpus has almost
no distance separation, so a top-10 there is near-arbitrary for any engine, and
a parity figure taken on it would measure the corpus rather than the index.)

## 5. The filtered-ANN shortfall — the measured hazard the leg has to defend against

pgvector's `hnsw.iterative_scan` decides what a FILTERED index scan does when the
filter is selective enough that the `ef_search` window holds no match. Measured,
20k × 384, `ef_search` = 40, filter matching **152** rows, `LIMIT 500`:

| `hnsw.iterative_scan` | rows returned |
|---|---|
| **`off`** (the DEFAULT) | **0** |
| `strict_order` | 150 |

**⇒ A filtered ANN under the default returns ZERO rows where 152 exist**, and
nothing distinguishes that from "the corpus is empty". `tortoise/record_vector.py`
therefore sets `strict_order` on every read *and* compares the row count against
the filter's own count on the short path, raising `VectorShortfall` rather than
passing a short answer off as a small corpus. `§6.1` of the search doc lists
this assertion as required before private-beta; this is it.

## 6. The cold restore — what is NEW here, and what is the #7846 baseline

| figure | source |
|---|---|
| `vectorset` index rebuild on restore, 20k nodes: **436.5 s** untuned, **191.8 s** tuned, and the rebuild **deadlocked** the instance | **QUOTED** — `#7846`, as recorded in `SEARCH-ARCHITECTURE.md` §3 and the issue body. **Not reproduced here**: the restores this lane could drive at 20k did not exercise that path, so this figure is the baseline the move removes, not a measurement of this change |
| cold **logical** `pg_dump` + `pg_restore` of the 20k vector corpus, HNSW build included | **TAKEN HERE — 10.9 s** |
| …its split: `--section=pre-data --section=data` **11.0 s**, then the HNSW build alone **4.3 s** | TAKEN HERE |
| `GRAPH.COPY` of 5,000 nodes + an HNSW index, on falkordb **4.20.6** | **TAKEN HERE — 0.66 s**, and the copy's catalogue AND kNN both intact (5 rows on the first query) |

⚠️ **What the `GRAPH.COPY` row means, and what the WIDER claim would be —
corrected by review round 2, P2.** `GRAPH.COPY` carries the index on this build,
and the repo's OWN measured evidence says the same about the graph-KEY path:
`tortoise/graph_key_restore.py` (quoted at its:163) records that the vector index
"travels INSIDE the graph key's serialized payload (measured —
`docs/evidence/7846-indexed-graph-restore/README.md` §4a)", which is why
`restore_graph_key_safe` drops the index before the `DUMP` and re-creates it from
the captured definition after the `RESTORE`. **So an earlier draft of this file
and of the migration header were wrong to say the index "is not carried in a
dump", and wrong again to conclude that "no restore path has anything to
rebuild".**

What survives, at the width the evidence supports:

* `#7846` §3's **436.5 s / 191.8 s / instance blocked** is the cost of restoring
  a payload that CARRIES the index — the price of the index existing anywhere in
  the restore path.
* After this change an index-free graph pays **none** of it: nothing to drop
  before the `DUMP`, nothing to re-create after the `RESTORE`, because
  `_ensure_vector_index_api` creates no in-graph index and drops one it finds
  (pinned by `tests/test_4997_vector_index_parity.py` and
  `tests/test_falkordb_compat.py`).
* ⛔ **And the scope that qualifies it: the saving is earned for every dump taken
  AFTER the retirement has swept that graph.** A dump taken BEFORE it still
  carries the index and still costs what §3 measured. `graph_key_restore` reads
  the definition from `CALL db.indexes()` rather than hardcoding it, so a retired
  store simply reports none and its re-create step becomes a no-op — that path
  needed no change here, and this change makes none.

The Postgres half, where the index now lives, rebuilds in **10.9 s** on a full
logical restore at 20k — and on a physical/base-backup restore it does not
rebuild at all.

## 7. The two defects this measurement found that reading did not

1. **`pg_restore` restores NOTHING if the converter's body is unqualified.** It
   empties `search_path` (`pg_dump` emits `set_config('search_path', '', false)`)
   and `CREATE TABLE` re-parses a generated column's expression through the
   function body, so an unqualified `vector(384)` there produces
   `ERROR: type "vector" does not exist` /
   `CONTEXT: SQL function "record_embedding_vector" during inlining` /
   `CREATE TABLE public.<t>` — and the table is never created, so every later
   statement in the restore fails behind it. The body and the opclass are
   schema-qualified, and the reason is on the function's own `COMMENT`.
2. **A generated column must be GATED, not dropped unconditionally.** A blind
   `DROP INDEX` on a store that has no vector index is a failing DDL round trip
   on every setup — and `_ensure_indexes` is reached from the hosted
   PER-REQUEST path, so that is a per-request cost. The retirement reads the
   catalogue first, making the steady state zero statements.

## Reproducing

```bash
docker run -d --name pgv -e POSTGRES_PASSWORD=pg -e POSTGRES_DB=record \
    -p 127.0.0.1:0:5432 pgvector/pgvector:pg16
# apply supabase/migrations/20261109000001_record_embedding_vector.sql, then:
#   ALTER TABLE … ADD COLUMN … GENERATED … STORED   → time the rewrite
#   CREATE INDEX … USING hnsw (embedding_vec public.vector_cosine_ops) → time it
#   pg_dump | pg_restore                            → time the cold restore
```

The erasure half is re-runnable as an operator verb:

```bash
uv run python tools/embedding_residual_scan.py --org <org> --graph <graph> --json
# 0 CLEAN · 3 RESIDUE (a finding) · 2 NO VERDICT (a store could not be read)
```
