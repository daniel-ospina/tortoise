---
title: "#11821 P0 — the journal's vector payload stored at float32 (evidence)"
type: evidence
domain: platform
doc_status: final
created: 2026-11-11
subjects.team: epistemic-team
aboutSubjects: Tortoise journal
aboutObjects: "#11821, docs/architecture/STORAGE-ARCHITECTURE.md §5.2, tortoise/embeddings.py stamp_journal_embedding, tortoise/log.py EventLog, tools/journal_growth.py"
---

# #11821 P0 — the journal's vector payload, before and after

**Measured at:** branch `perf/11821-journal-encoding` off `origin/main` `d1447b0fe3`,
2026-11-11, with `tools/journal_growth.py` — the same instrument `#5612` used, on the
same committed 29-type corpus (`supabase/tests/fixtures/record_projection_journal.jsonl`).

**What this proves.** The change and its size: a creating record's vector payload goes
**8,414 B → 4,689 B** (21.9 → 12.2 chars/float, **1.79×**), and the record itself
**9,176 B → 5,423 B** (**1.69×**), with the decoded values unchanged at float32 (and, in
fact, bit-identical in float64 — see *Why the VALUE is quantised*).

**What it does not prove, stated up front.** It is **not** "the record halved". A JSON
*number* encoding cannot reach 50 % of 8,414 B at float32 precision: the floor for this
corpus's magnitude distribution is ~4,689 B, because 9 significant digits with leading
zeros is ~11.2 chars and each element still costs its separator. The **4.1×** step in
`#11821` is **P2's base64**, which is a format migration on the rebuild path and is not
in this change.

## Run it

```console
$ env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
    uv run python docs/evidence/11821-journal-encoding/probes/journal_encoding_probe.py
```

The probe writes TWO journals from the corpus — the pre-change writer's form and the
writer as it stands — and runs `tools/journal_growth.py`'s own `measure()` over each, so
the delta is produced by the acceptance instrument rather than by arithmetic inside the
probe. `measurement.txt` beside this file is one run's output.

| directory | what it is |
|---|---|
| **before** | the pre-`#11821` writer, **reproduced**: each vector element is the embedder's float32 widened to a Python float (what `stamp_journal_embedding` did: `float(x)` of a numpy float32), appended with `json.dumps(..., ensure_ascii=False)`'s default separators. Byte-for-byte the old writer, which is why it reproduces the published 9,176 B. |
| **after** | the writer **as it is**: `stamp_journal_embedding` (float32 + `embedding_width`) and `EventLog.append` (compact separators). |

## The measurement

```console
== tools/journal_growth.py — the WHOLE 29-type corpus (39 records)
  before #11821 P0   39 records   51,346 B  mean  1316.6 B  p50    219 B  p95  8,877 B  max  9,176 B
  after  #11821 P0   39 records   32,130 B  mean   823.8 B  p50    201 B  p95  5,148 B  max  5,423 B
  embedding share: 42,070 B → 25,360 B (78.9% of the corpus)
```

Per class — one record per type, the **fullest** one (the shape production writes):

| record class | before | after | saved | ratio |
|---|---|---|---|---|
| `PointAdded` (create) | 9,176 B | **5,423 B** | 3,753 B | **1.69×** |
| — its **vector payload** | 8,414 B | **4,689 B** | 3,725 B | **1.79×** (21.9 → 12.2 chars/float) |
| `OperatorAdded` | 8,859 B | 5,124 B | 3,735 B | 1.73× |
| `PointPromoted` (re-emitted snapshot) | 8,657 B | 4,936 B | 3,721 B | 1.75× |
| `OperatorPromoted` | 8,647 B | 4,924 B | 3,723 B | 1.76× |
| `PointRevised` (delta, no vector) | 196 B | 181 B | 15 B | 1.08× |
| `EntityLinked` (delta, no vector) | 249 B | 228 B | 21 B | 1.09× |

**The two halves of the delta are separately attributable, which is why they are two
commits.** The vector rows above are dominated by the float32 change; the ~8 % on the
rows that carry **no vector at all** (`PointRevised`, `EntityLinked`, and every other
1.07–1.10× row in `measurement.txt`) is the **compact separators** alone — `", "` /
`": "` cost one byte per separator, measured at 435 B of a 9,176 B record and 383 B of
its 5,072 B vector.

## Why the VALUE is quantised, not just the text

`json.dumps` prints `float.__repr__` and there is **no hook for it** — the C and Python
encoders both call it directly, so a `float` subclass's `__repr__` is ignored (measured:
`json.dumps([F(0.1)])` → `[0.1]`, not the subclass's text). Formatting at the printer
would therefore need `json.encoder._make_iterencode` (private API) or a hand-written
serialiser on a durability path — and it would leave the payload holding 17-digit values
while the journal text holds 9, so a **live** write and a **replay** of the same record
would put different numbers in the record's `embedding` jsonb.

Quantising the value at the seam keeps the invariant **exact**: the stored float is the
float64 whose `repr` *is* the shortest float32 round trip, so
`json.loads(journal_text) == payload["embedding"]` bit for bit, and the float32 bits are
the embedder's own (`struct`-verified per element by
`tests/test_11821_journal_vector_width.py`).

## The promise this does NOT touch

A **caller-owned** (`embedding_verbatim`) or **preserved** (`embedding_preserved`) vector
is never narrowed — it is declared `f64` and keeps its raw form (`#5004` round-7:
*"a CALLER-owned vector must keep its RAW form. `vecf32()` is the ONE form it must not
take"*). The width marker rides the payload from this change — not from P2 — so that
population is **named** from the first PR rather than narrowed silently by a later one.
`tests/test_11821_journal_vector_width.py::test_a_verbatim_vector_is_never_narrowed` reds
if it is ever narrowed, and its fixture is deliberately off the float32 grid so the
assertion cannot pass vacuously.

A journal written **before** this change carries no marker and 17-digit values; it reads
and replays exactly as it always did (`test_a_journal_written_before_this_change_still_reads_and_replays`).

## Cost, measured

The quantiser is `1.08 ms` per 384-d vector (`float(str(np.float32(x)))` per element) —
measured on this corpus — against ~50 µs for the `float(x)` loop it replaces. It runs
once per journalled vector on the write path; the surrounding write (graph + record +
embed) is ms-scale. Stated rather than hidden: this is a write-path cost traded for a
44 % smaller vector payload on the one cost line with no revenue attached.
