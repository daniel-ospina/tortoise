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
**9,176 B → 5,423 B** (**1.69×**), with the values' **float32 bits unchanged**
(`test_every_element_keeps_its_float32_bits`) and the journal line decoding **bit-identically
to the payload the writer held** (`json.loads(line)["embedding"] == payload["embedding"]`) — see
*Why the VALUE is quantised*.

⚠️ **It is NOT “the same float64 numbers”.** The old writer wrote the float32 WIDENED to
float64 (`float(np.float32(0.1))` → `0.10000000149011612`); the writer now emits the
shortest decimal of the same float32 (`0.1`). Both cast to the same float32 bits — the bits
are what is preserved, and the float64 inflation is exactly what this change stops. The
bit-identity this bundle can claim is between the journal TEXT and the payload, never
between the new and the old float64 values.

⚠️ **Two figures for the vector, and each names its instrument.** `4,689 B` (and its
`12.2 chars/float`) is the **on-disk span** of the `embedding` value in the compact
journal — arithmetic from this probe (*Two rulers, below*). `tools/journal_growth.py`
reports **5,072 B (1.66×, 13.2 chars/float)** for the same vector, because it attributes
bytes by **re-serialising** each value with `json.dumps`' default (spaced) separators
(`tools/journal_growth.py:_json_bytes`, and its own docstring calls the output
*attribution*, not accounting). A reader reproducing the vector figure with the named
tool gets the tool's number, not the probe's — so neither number is quoted as the other's.

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
  embedding share, numerator and denominator on ONE ruler each:
    before  42,070 B of 51,346 B = 81.9% (both spaced)
    after   23,445 B of 32,130 B = 73.0% (both compact)
    ⚠️ the tool's own attribution for the AFTER journal is 25,360 B — a SPACED re-serialisation, so it is NOT the numerator for the compact 32,130 B (that mix reads 78.9%).
```

**Two rulers, and the share is taken on one of them.** `tools/journal_growth.py`'s
`embedding_bytes` is a **spaced** figure whatever the journal on disk looks like, because
it re-serialises each value with `json.dumps`' default separators; `bytes` is the
journal's own on-disk total. Before this change both were spaced, so the quotient was one
ruler. The compact writer puts them on different rulers, and their quotient (25,360 /
32,130 = **78.9 %**) reads part of the spacing change as if it were vector text — which
is why this probe measures the on-disk span itself (each journal, its own separators) and
prints the pair above instead. The honest statement is that the vector's share of the
journal went **81.9 % → 73.0 %** on a fixed ruler.

Per class — one record per type, the **fullest** one (the shape production writes):

| record class | before | after | saved | ratio |
|---|---|---|---|---|
| `PointAdded` (create) | 9,176 B | **5,423 B** | 3,753 B | **1.69×** |
| — its **vector payload**<sup>†</sup> | 8,414 B | **4,689 B** | 3,725 B | **1.79×** (21.9 → 12.2 chars/float) |
| `OperatorAdded` | 8,859 B | 5,124 B | 3,735 B | 1.73× |
| `PointPromoted` (re-emitted snapshot) | 8,657 B | 4,936 B | 3,721 B | 1.75× |
| `OperatorPromoted` | 8,647 B | 4,924 B | 3,723 B | 1.76× |
| `PointRevised` (delta, no vector) | 196 B | 181 B | 15 B | 1.08× |
| `EntityLinked` (delta, no vector) | 249 B | 228 B | 21 B | 1.09× |

<sup>†</sup> The vector row is the **on-disk span** of the `embedding` value (this probe's
arithmetic, each journal with its own separators). The tool's spaced re-serialisation of
the *same* vector is **5,072 B** after (13.2 chars/float); `4,689 B` cannot be produced by
`tools/journal_growth.py`, whose `_json_bytes` always uses default separators.

**The two halves of the delta are separately attributable, and the attribution is
carried by the MEASUREMENT — not by the history: both changes landed in ONE commit.**
The probe separates them by construction. The vector rows above are dominated by the
float32 change; the ~8 % on the rows that carry **no vector at all** (`PointRevised`,
`EntityLinked`, and every other
1.07–1.10× row in `measurement.txt`) is the **compact separators** alone — `", "` /
`": "` cost one byte per separator — **435 B on the pre-change record** (9,176 B spaced,
8,741 B written compact) and **437 B on the post-change one** (5,860 B spaced, 5,423 B on
disk) — and the saving on the vector alone is **383 B under either ruler** (8,414 → 8,031
spaced, 5,072 → 4,689 compact), which is why the two figures agree here and diverge nowhere
else. ⚠️ Both records are named because the possessive is where this bundle has been wrong
before: the 9,176 B record's own vector is **8,414 B**, and **5,072 B is the POST-change
vector** (spaced; 4,689 B compact) — not a size the 9,176 B record ever had.

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
`json.loads(journal_text)["embedding"] == payload["embedding"]` bit for bit, and the float32
bits are the embedder's own (`struct`-verified per element by
`tests/test_11821_journal_vector_width.py`).

## The promise this does NOT touch

A **caller-owned** (`embedding_verbatim`) or **preserved** (`embedding_preserved`) vector
is never narrowed — it is declared `f64` and keeps its raw form (`#5004` round-7: the
graph-only id's only restorer is `projection.FalkorProjection._rebuild_all_replay`'s
`embedding_verbatim` guard — *"a CALLER-owned vector must keep its RAW form. `vecf32()` is the ONE form it
must not take"* — and the flag picks the storage form in
`projection/entities.py::_upsert_point_props`'s
`CASE WHEN $embedding_verbatim THEN $embedding ELSE vecf32($embedding)`, set by
`TortoiseSDK.create_point`'s `_create_map["embedding_verbatim"]`). The width marker rides the payload from this change — not from P2 — so that
population is **named** from the first PR rather than narrowed silently by a later one.
`tests/test_11821_journal_vector_width.py::test_a_verbatim_vector_is_never_narrowed` reds
if it is ever narrowed, and its **grid-exact** fixture is what keeps the assertion from
passing vacuously: that vector IS on the grid, so the grid rule does not refuse it and only
the `is_verbatim` arm stands between it and a narrowed rewrite — measured, the row REDS
with that arm removed (and was GREEN, i.e. a pin that could not fail, before the
grid-exact fixture was added). The off-grid sibling beside it is the *value-level* half,
which the grid rule already refuses on its own; it is not the guard this sentence is
about.

**And the marker is not the only test, on purpose.** A vector is narrowed only when every
element is **exactly what a float32 widens to** (`float(np.float32(v)) == v`, checked
vectorised). Nothing the embedder emits can be off that grid, so an off-grid element is
somebody else's value — and at least one path hands one over with **no marker at all**
(`EventAPI.add_point(fields=…)` refuses `embedding_verbatim` as a forged key, and never
mints it — the only marker that door mints is the WIDTH). The grid check makes that
population `f64`/untouched by construction, and it
is what catches in-range-but-off-grid values (`16777217.0`, `1e-50`) that a
narrow-both-sides-and-compare guard would silently rewrite.

A journal written **before** this change carries no marker and 17-digit values; it reads
and replays exactly as it always did (`test_a_journal_written_before_this_change_still_reads_and_replays`).

## Cost — an author-side spot check, NOT a bundle artifact

⛔ **These two numbers are machine-dependent and are deliberately NOT in `measurement.txt`**:
the probe is deterministic by design (every other figure in this bundle reproduces
byte-for-byte), so a wall-clock reading inside it would make the file un-diffable. Stated
with its ruler instead, and reproducible by this command on any box:

```console
$ env -u TORTOISE_DB_URI uv run python - <<'EOF'
import timeit, numpy as np
from tortoise.embeddings import shortest_float32_vector
vec = [float(x) for x in np.random.default_rng(5612).standard_normal(384).astype(np.float32)]
for label, fn in (("before float(np.float32(x)) loop", lambda: [float(np.float32(x)) for x in vec]),
                  ("after  shortest_float32_vector ", lambda: shortest_float32_vector(vec))):
    print(f"{label}: {timeit.timeit(fn, number=200) / 200 * 1e6:.1f} us per 384-d vector")
EOF
before float(np.float32(x)) loop: 129.0 us per 384-d vector
after  shortest_float32_vector : 482.4 us per 384-d vector
```

The **ratio** (≈3.7× here) is the transferable part; the absolute numbers are this box's
(8-core Linux cell, `n=200`). It runs once per journalled vector on the write path, and the
surrounding write (graph + record + embed) is ms-scale. Stated rather than hidden: a
write-path cost traded for a 44 % smaller vector payload on the one cost line with no
revenue attached. ⚠️ This replaces an earlier `1.68 ms` / `14 µs` pair that carried no
command and does not reproduce — a 384-element cast loop does not run in 14 µs.
