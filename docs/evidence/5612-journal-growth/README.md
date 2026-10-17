---
title: "#5612 — the hosted journal's growth, measured (evidence)"
type: evidence
domain: platform
doc_status: final
created: 2026-10-17
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#5612, STORAGE-ARCHITECTURE.md §5.1/§5.2/§15, tortoise/log.py EventLog, tortoise/embeddings.py stamp_journal_embedding"
---

# #5612 — the hosted journal's growth, measured

**Measured at:** branch `fix/5612-journal-bounds` off `origin/main`, 2026-10-17,
`tools/journal_growth.py` + the probe below.

**What this proves, and what it does not.** It produces the number
`STORAGE-ARCHITECTURE.md` §5.1 has recorded as missing since 2026-09-23 — *"No
measurement of the journal's growth exists yet"* — per record class and per
operation, on the real record shapes with the real pinned embedder's vector
width. It does **not** bound, rotate, compact or expire anything, and it does
**not** choose a retention policy: the journal is the rebuild source
(`derived = replay(journal)`), so any prefix a bound discards must first be
absorbed by something else. That is a durability-posture decision for the owner
(§ *Why the bound is not chosen here* below).

## Run it

```console
$ uv run python tools/journal_growth.py supabase/tests/fixtures/record_projection_journal.jsonl
$ env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
    uv run python docs/evidence/5612-journal-growth/probes/corpus_growth_probe.py
```

`tools/journal_growth.py` also measures a **live** journal directory
(`TORTOISE_EVENT_LOG_BASE_DIR`, one `*.jsonl` per graph, enumerated the way
`rebuild_all` enumerates it) and emits `--json` for a dashboard. On a real
hosted journal it reports the embedding share directly, record by record.

## The measurement

```console
$ env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
    uv run python docs/evidence/5612-journal-growth/probes/corpus_growth_probe.py
corpus: record_projection_journal.jsonl — 39 records, 29 event types
vector: 384-d, BAAI/bge-small-en-v1.5, stamped through stamp_journal_embedding (the writer's own seam)

== per-operation cost (bytes on disk, JSON line + terminator)
  create_point / capture turn              PointAdded           8,877 B  (no vector: 246 B)
  add_operator                             OperatorAdded        8,859 B  (no vector: 319 B)
  promote (re-emitted snapshot)            PointPromoted        8,657 B  (no vector: 228 B)
  content/metadata revision (delta)        PointRevised           196 B
  of which the vector's bytes:                                 8,414 B (21.9 chars/float)

== the 29-type corpus, measured WHOLE (one record of each type — a record-type mix, NOT an operation mix)
  without vectors     39 records     8,711 B  mean   223.4 B  p95    336 B  max    570 B
  with vectors        39 records    51,346 B  mean  1316.6 B  p95  8,877 B  max  9,176 B
  embedding share of the vector-bearing corpus: 42,070 / 51,346 B (81.9%)

== growth per 1,000 operations
  1,000 point creates (vector)         8,877,000 B  (   8.5 MB)
  1,000 content/metadata revisions       196,000 B  (   0.2 MB)
  1,000 entity links                     242,000 B  (   0.2 MB)
  1,000,000 point creates           8,877,000,000 B  (   8.3 GB)

VERDICT: a measurement, not a bound — the retention/rotation/checkpoint shape is an owner (durability-posture) decision (#5612)
```

### Method

| input | what is real about it |
|---|---|
| record shapes | the committed corpus `supabase/tests/fixtures/record_projection_journal.jsonl` — one record of each of the 29 emitted event types |
| the vector | width = the pinned embedder's (`tortoise.embeddings.EMBEDDING_MODEL`, BAAI/bge-small-en-v1.5, **384**); encoded by `tortoise.embeddings.stamp_journal_embedding` — the SAME seam `TortoiseSDK._emit_event` and `EventAPI._point` call — so the float reprs and the `embedding_model` / `embedding_revision` / `embedding_text_hash` identity keys are production code, not a probe re-implementation |
| the vector's values | a seeded unit vector. Bytes do not depend on which text was encoded: same width, same range, same repr length |
| the byte figure | the record's JSON line **plus its terminator**, i.e. the bytes the volume stores. `tools/journal_growth.py` asserts `bytes + other_bytes == file size` exactly (`tests/test_journal_growth.py`) |

Only the record types a writer actually stamps a vector onto are stamped
(`PointAdded`/`OperatorAdded` — creating — and `PointPromoted`/`OperatorPromoted`
— re-emitted snapshots). `PointRevised` is left as committed because
`update_point` **strips** the vector it derived from `new_content` (#4208, the
replay re-encodes it); only a CALLER-supplied vector rides that record.

## What the numbers say

1. **The journal is vector-dominated, not record-dominated.** A `PointAdded`
   without a vector is 246 B; with one it is 8,877 B, and **8,414 B of that
   (95%) is the vector's text** — the *decimal expansion* of 384 floats
   (**21.9 characters per float**, where the graph stores 4 bytes per float as
   `vecf32`). The vector is 97% of the record's growth over the vectorless form
   (8,631 B). The identity keys #5004 added are the remaining ~217 B: the
   `stamp_journal_embedding` seam is not what costs memory, the encoding is.
2. **Creates dominate; revisions are cheap.** A content or metadata revision is
   a 196 B delta. Growth tracks the number of **creates** (points, operators,
   capture turns) — i.e. it tracks the number of *snapshots*, and a snapshot is
   the only record that carries a point's full state.
3. **The cost is on the order of 8.5 MB per 1,000 creates** at the pinned
   embedder: 10,000 creates ≈ 83 MB, 1,000,000 ≈ 8.3 GB. That is the figure
   §5's cost table and §6's volume problem have been missing.
4. **The `p95` of a journal is a vector record, always.** On the vector-bearing
   corpus, p50 is 229 B and p95 is 8,877 B — a 39× gap. Any bound argued from a
   mean is arguing from a number no single record resembles. This is also why
   the whole-journal read's cost matters: it is proportional to the sum of
   these, and `restore()` no longer parses the journal to report its event
   count (measured on #5612 at 20.4 s → 0.07 s on a 300,000-record journal,
   fixed in #7417).

## Why the bound is not chosen here

Item 1 of #5612 (concurrent appends) landed as #7417/#5917 (a sealed torn tail +
byte-safe per-line decode); its premise — that a mid-file interleave needs a
lock — was **measured and not reproducible** on #5612 (2026-10-06: 12 threads ×
40 records × 20 KB, 8 fork processes, 12 threads × 2 MB records; zero
interleaves), because `open(path, "a")` is `O_APPEND` and CPython issues ONE
`write(2)` per record. Item 2 — unbounded growth — is the remaining half, and the
shape of the bound is **not a lane's call**:

- **The journal is the rebuild source.** `rebuild_all` is an unconditional wipe +
  journal-only replay (`STORAGE-ARCHITECTURE.md` §3), so a retention window whose
  discarded prefix no snapshot has absorbed converts a *cost* problem into
  *silent state loss* — the graph a restore produces would be missing the
  points whose creating records were the discarded ones. The measurement above
  sharpens this: those records are exactly the ones carrying the state.
- **`docs/durability-posture.md` is the recorded authority** on what the journal
  promises. A retention policy is a statement about that promise, so it belongs
  there, with the owner — not in a measurement PR.
- The safe shapes and their prerequisites are recorded on #5612
  (`#issuecomment-6008256611`): checkpoint-then-truncate requires
  checkpoint/truncate semantics to exist first; the measurement was the
  prerequisite for choosing a size.

**One consequence of this measurement is worth putting in front of the owner
before any retention window is picked:** narrowing the vector encoding
(base64 of the float32 the graph itself stores, instead of a JSON list of
float64 decimal reprs) is a **~4× reduction on the vector-dominated record**
with no state discarded and no change to what is journaled — a change to the
*journal format*, not to the journal's *retention*. It is deliberately NOT
implemented here: it is a rebuild-contract change (replay must decode it), so it
needs its own decision and its own tests.

## Reproduce the accounting

```console
$ TORTOISE_DB_URI='docker://:@127.0.0.1:16393/tortoise_test_matrix' \
    uv run pytest tests/test_journal_growth.py -q
14 passed
```
