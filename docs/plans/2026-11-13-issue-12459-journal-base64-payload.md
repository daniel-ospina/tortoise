---
title: "P2 — the journal's vector payload as base64 of packed float32 (design note, no implementation)"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-13
updated: 2026-11-13
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "issue #12459, issue #11821, issue #12458, issue #11835, issue #5004, issue #11886, issue #11758, journal format, rebuild path, embedding_verbatim, base64 float32"
related:
  - issue: 12459
    note: "The design-note deliverable for P2 (this document)."
  - issue: 11821
    note: "The three-phase spec this note takes P2 from; P0 landed in #11835."
  - issue: 12458
    note: "P1 — the blocking predecessor. Implementation is blocked on it landing and being measured."
  - issue: 5004
    note: "Round-7: a caller-owned vector keeps its RAW form. The constraint that stops a single uniform encoding."
  - issue: 11886
    note: "The ruler defect (spaced vs compact attribution) that every byte figure in this note names its instrument for."
---

# P2 — the journal's vector payload as base64 of packed float32

**Status:** proposed — **design note only. No storage code, no payload change, no migration is proposed as
landed by this document.**
**Complexity:** Standard (a format change on the durability artifact; the risk is on the rebuild path, not
the append path)
**Issue:** #12459 · **spec:** #11821 · **P0 (landed):** #11835 · **blocking predecessor:** #12458 (P1)
**Evidence + probe:** `docs/evidence/12459-base64-payload/`

---

## 0. Research first — what this note was built from, and what it could not reach

### Internal (read before deciding anything)

| source | what it settles |
|---|---|
| `~/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md` | **ABSENT on this box** — declared, not skipped. Nothing here is a graph query. |
| #11821 | the three-phase spec. P0 = width, P1 = the `:GraphEvent` mirror, **P2 = this**. Its own sequencing rule: *"no implementation until P0 and P1 are merged and measured… shares a PR with nothing."* |
| #11835 (P0's PR) | the Review Scope Contract R1–R5, the measured post-P0 figures, and the two acceptance figures it reported as **not** holding rather than adjusting them |
| `tortoise/embeddings.py::stamp_journal_embedding`, `_stamp_journal_vector_width`, `_is_shortest_f32_text`, `shortest_float32_vector` | the landed P0 seam: what "the shortest float32 round trip" means here, the **three outcomes** of the width stamp, and the eight homes the marker is declared in |
| `tests/test_11821_journal_vector_width.py` (**51 tests** at this head) | the pins P2 must not move: float32 bits, the never-narrowed population, the pre-change replay test at `:518` |
| `docs/durability-posture.md` **R1** | the embedding **stores** — a replay restores it verbatim and never regenerates it. *"presence is ownership."* |
| `docs/architecture/STORAGE-ARCHITECTURE.md` §5.2 | the recorded P2 finding already on the books: *"~4× more than base64 of the same information as the float32 the graph itself stores (2,048 B for a 384-d vector)"* — and that it needs its own decision and its own tests |
| `tools/journal_growth.py` | read **critically** — see §1.1. It cannot report the vector payload's **on-disk** span (it re-serialises with spaced separators). |
| #11886 | the ruler defect: the tool attributes bytes by re-serialising with **spaced** separators, so its vector figure and the journal's on-disk figure are on different rulers |

### External — ⛔ **UNAVAILABLE on this box. Declared, with the errors.**

The brief's rung 1 is `mcp__seo-intelligence__perplexity_search`. Four rungs were attempted; each failed on
a different missing piece:

| rung | attempt | error |
|---|---|---|
| 1 | `mcp_load seo-intelligence` | `Unknown MCP server 'seo-intelligence'` |
| 2 | `web_search` (`sonar`, the cheapest model) | `PERPLEXITY_API_KEY not set` |
| 3 | `mcp_load brave-search` | `MCP error -32000: Connection closed` |
| 4 | `mcp_load exa` → `web_search_exa` | loads, then `(401) API key must be provided … EXA_API_KEY` |

There is no `.env` on this box (`AGENT_INFRA_PATH` is unset too), so no rung could be repaired without a
credential only the owner can supply. **That is a gap in this note, not a silent substitution.** What it
means in practice: the external questions are answered from (a) sources **already recorded in this repo**
and (b) **measurements run here**, which is a stronger footing for the numeric claims and a weaker one for
the comparables survey. Concretely:

- **(a) binary float encodings in a text log.** The trade-off is already recorded in-repo
  (`STORAGE-ARCHITECTURE.md` §5.2, #5612's citations): base64 of packed float32 is **+33 % over raw** and
  ~5× smaller than float64 decimal text. Measured here, on the record that matters (§1).
- **(b) shortest-round-trip printing.** Answered by **measurement instead of citation** — §2.3. The claim
  "9 significant digits is exactly float32's precision" is checkable, so it was checked: on 2,000,000
  random float32 values, `%.9g` round-trips **2,000,000/2,000,000**; `%.8g` misses **10,758**; `%.7g`
  misses **1,336,573**.
- **(c) versioned formats on a replay path.** Answered from this repo's own contracts — the append-only
  rule (#5917: *"Nothing is truncated or rewritten — the seal is another append"*) and R1's
  presence-is-ownership. These **forbid** the obvious external pattern (a file-level header), which is a
  constraint no comparable's practice can override; see §2.1.
- **(d) what the memory comparables store.** ⛔ **NOT ANSWERED.** Zep/Graphiti episode payloads, Mem0,
  Letta and pgvector's on-disk array format were not surveyed, and no claim about them is made in this
  note. This is the one question the note leaves open, and it does not block the decision: the binding
  constraints here are #5004 and R1, both internal, and a comparable's practice could not reverse either.

**CONTRADICTION TEST (asked first, before cost, quality or fit).** Three recorded positions bind this
design, and none may be reversed by adoption:

1. **#5004 round-7** — *"a CALLER-owned vector must keep its RAW form. `vecf32()` is the ONE form it must
   not take."* A single uniform float32 encoding for the whole payload **contradicts** it. So the format
   does not have one encoding: it has a **width-tagged** one (§4).
2. **P0's width marker** — declared from the first PR *precisely so a later format change cannot narrow
   the `embedding_verbatim` population silently*. An encoding that narrowed it would be a **reopen, not an
   adoption**. The encoding below narrows nothing (§4).
3. **`docs/durability-posture.md` R1** — derived properties are **STORED, not recomputed**. The encoding
   touches R1's *carrier*, never its rule: the vector keeps being stored and restored, it is only the
   *spelling* of the stored value that changes, and the replay still never re-embeds. §4.3 states the one
   place this must be proven rather than asserted.

---

## 1. Byte delta, measured

### 1.1 The instrument question first — because #11821's headline is on two rulers

`tools/journal_growth.py` **cannot report the vector payload's on-disk span.** It attributes bytes by
re-serialising each value with `json.dumps`' **default (spaced)** separators
(`tools/journal_growth.py::_json_bytes`), and its own docstring calls the output *attribution*, not
accounting. Since P0 also moved the writer to **compact** separators, its vector figure and the journal's
actual byte count are on different rulers. That is #11886, and it is why every vector figure below names
its ruler:

| figure | ruler | instrument |
|---|---|---|
| **4,689 B** (P0 vector, 12.2 chars/float) | on-disk compact span | `docs/evidence/11821-journal-encoding/probes/journal_encoding_probe.py` |
| 5,072 B (13.2 chars/float) | spaced re-serialisation | `tools/journal_growth.py` |
| **5,423 B** (P0 `PointAdded` record) | on-disk, whole line + terminator | **both** (they agree on whole records) |

The ~383 B between 4,689 and 5,072 is the separator change, not a second encoding.

### 1.2 The baseline, re-measured post-merge (not quoted from pre-P0)

```bash
# Runs the committed writer over the committed 29-type corpus, then the committed instrument over the result.
env -u TORTOISE_DB_URI uv run python \
    docs/evidence/12459-base64-payload/probes/p2_base64_probe.py
```

Output (verbatim, `.worktree-report/logs/03-…log`):

```
== tools/journal_growth.py on the P0 journal (the committed instrument)
  39 records  32,130 B  mean 823.8 B  p95 5,148 B  max 5,423 B
```

That reproduces P0's published corpus figure (`39 records  32,130 B  max 5,423 B`) exactly, so the
baseline below is P0's **post-merge** size — **not** the pre-P0 9,176 B, which P0 already moved.

| record class (one per type, the fullest) | P0 record | P0 vector span | P2 vector span | P2 record | record ratio |
|---|---|---|---|---|---|
| **`PointAdded`** (creating) | **5,423 B** | **4,689 B** | **2,064 B** | **2,798 B** | **1.94×** |
| `OperatorAdded` | 5,124 B | 4,689 B | 2,064 B | 2,499 B | 2.05× |
| `PointPromoted` | 4,936 B | 4,689 B | 2,064 B | 2,311 B | 2.14× |
| `OperatorPromoted` | 4,924 B | 4,689 B | 2,064 B | 2,299 B | 2.14× |
| whole corpus (39 records) | 32,130 B | — | — | 19,005 B | 1.69× |

### 1.3 The arithmetic, term by term

For one 384-d vector, at the frame in §2:

```
raw float32 payload      384 elements × 4 B/elem            = 1,536 B
base64 (RFC 4648 standard alphabet)  ⌈1536/3⌉ × 4            = 2,048 B   (+33.3 % — the encoding tax)
frame prefix  "b64:1:f32:384:"  = 4 marker + 2 version
                                  + 4 width + 4 count         =   14 B
plus 2 B of JSON string quotes                                =    2 B
                                                              ---------
vector span under P2                                        = 2,064 B
                                                            
record:  5,423 − 4,689 + 2,064                               = 2,798 B
ratio    5,423 / 2,798 = 1.94×   (vector alone: 4,689/2,064 = 2.27×)
```

Cross-check on an independent path: 384 floats × **5.375** chars/float = 2,064 B. P0 was 12.2 chars/float,
pre-P0 21.9 — so the per-element cost is now **5.4 characters, of which 5.33 is the base64 of 4 bytes**.
The framing is 16 B on a 2,064 B field: **0.8 %**. That is the whole overhead of the frame.

### 1.4 ⛔ The headline "~4.1×" is a **pre-P0 → P2** figure, not the step this note decides

#11821 and #12459 both say **~4.1×**. That is `8,414 / 2,048 = 4.11` — pre-P0 decimal text against the P2
frame. **P0 already took the first 1.79× of it.** The step that is actually still on the table, and the
only one a decision here can spend, is:

```
vector payload   4,689 → 2,064 B   = 2.27×
whole record     5,423 → 2,798 B   = 1.94×
```

This is #11886's ruler lesson applied to #11821's own headline, and it changes the cost/benefit, so it is
restated rather than inherited. It does not weaken the *format* (§2–§4 stand on their own); it weakens
the *justification by bytes*, which is what §7 is for.

### 1.5 Restated in the cost model #11821 used (1,000,000 creates, $0.125/GB)

```
P0 journal   1,000,000 × 5,423 B = 5.423 GB  ≈ $0.68/mo
P2 journal   1,000,000 × 2,798 B = 2.798 GB  ≈ $0.35/mo      (saves 2.625 GB ≈ $0.33/mo)
pre-P0       1,000,000 × 9,176 B = 9.176 GB  ≈ $1.15/mo
```

#11821's closing line said **"~4.7 GB ≈ $0.59 (P0) → ~2.1 GB ≈ $0.26 (P2)"**. Measured, it is
**5.42 GB ≈ $0.68 → 2.80 GB ≈ $0.35**, and the difference is **arithmetic that can be reproduced**, not a
different basis:

```
  pre-P0 blended basis in that table:  8,500 B/record  (8.5 GB at 1,000,000 — §5.2, STORAGE-ARCHITECTURE.md:438)
  P0 row:  8,500 × 4,689 / 8,414 = 4,737 B → 4.74 GB   (table says 4.7 GB  ✓)
  P2 row:  8,500 × 2,048 / 8,414 = 2,069 B → 2.07 GB   (table says 2.1 GB  ✓)
```

So both rows are **reproducible arithmetic on a blended basis, plus a vector-ratio assumption the P2 row
then breaks**: they scale the whole record by the VECTOR's ratio. That is nearly right pre-P0, where the
vector is 92 % of the record — and wrong once the non-vector bytes are a third of it. The P2 row assumes
the record shrinks *with* its vector, when in fact **734 B** of the record (`2,798 − 2,064`) does not
shrink at all. Corrected on the creating record: **2.80 GB, saving ≈ $0.33/mo** — where the vector-ratio
row implies `(5.423 − 2.069) × $0.125 = $0.42`, i.e. the correction is **~22 %** of that quote. Stated
because a reader would budget from the quote. Whether this line is metered at all is **not settled
anywhere**: #11758, the pricing-of-record ticket, closed `COMPLETED` on 2026-11-12 without addressing
journal bytes, and #4333 (open) counts **node** sets, not journal bytes.

---

## 2. The format

### 2.1 Where the version marker lives — **per record, in the value**

```json
{"type":"PointAdded","ts":"…","id":"…","point":{"id":"…","embedding":"b64:1:f32:384:BAAQ…","embedding_width":"f32", …}}
```

```
"b64"  : the frame marker — a value that cannot be a legitimate `embedding`
"1"    : the frame version
"f32"  : the element width, IN the frame (and checked against `embedding_width`, §2.4)
"384"  : the element count
"BAAQ…": RFC 4648 standard base64 of the packed little-endian array
```

**Why per record and not a file header** — this is the constraint that decides it, and it is internal:

- The journal is **append-only**. `EventLog.append`'s torn-tail contract (#5917) is explicit: *"Nothing is
  truncated or rewritten — the seal is another append."* A file-level version header would require
  **rewriting existing journals** to add it. That is a different operation (a migration on a file that
  may be tens of GB), on the one artifact whose whole promise is that it is never modified.
- A file header is also **wrong under concatenation and restore**, both of which the deployment does:
  a header says "every line below is v2", which is false for a journal that was restored over a v1 tail
  or had a segment concatenated.
- Per-record is self-describing, so the reader needs **no file-level state** — the same property that
  makes §3's mixed-version replay a per-line dispatch instead of a sequential state machine.

**Why the marker is `b64:` and not a sibling key.** Three options were considered and two are refused:

| option | verdict |
|---|---|
| `"embedding": "b64:1:f32:384:…"` (a tagged string under the **same** key) | ✅ chosen |
| `"embedding_b64": "…"` with `embedding` dropped | ⛔ **refused** — R1 is *presence is ownership*: an absent `embedding` key means "pre-#5004 record, recompute is the only behaviour available". Dropping it would make the replay **re-embed**, silently changing the graph. |
| `"embedding": {"b64": "…"}` (a nested object) | ⛔ **refused** — `stamp_journal_embedding` treats a `dict` as *"not a numeric sequence"* and **drops the vector to an owned `None`** (`_drop_journalled_embedding`). It is the right refusal for a hand-edited journal and the wrong shape for a format. |

A string is the one shape the seam already refuses to treat as a vector — `_drop_journalled_embedding`
replaces it with an owned `None` and logs a warning (the row two lines above), so a mis-ordered pipeline
is **loud**, not silently transposed.

### 2.2 How the length is encoded — and why the count is load-bearing, measured

The element count is in the frame, and it is **not** redundant with the base64 length. The hazard is
**quantum-aligned truncation**, and it is measured on a real 384-d payload (`p2_base64_probe.py`, verbatim
in `measurement.txt`):

```
   cut   chars%4   decoded    floats   len%4==0   count check (declared n)
   -1    3        refused by b64decode (Incorrect padding)
   -2    2        refused by b64decode (Incorrect padding)
   -3    1        refused by b64decode (Invalid base64-encoded string: … cannot be 1 more than a multiple of 4)
   -4    0          1,533 B   383.25   False       refuses
   -8    0          1,530 B   382.50   False       refuses
   -12   0          1,527 B   381.75   False       refuses
   -16   0          1,524 B   381.00   True        ACCEPTS (381 floats)
   -20   0          1,521 B   380.25   False       refuses
  count check on the -16 frame: REFUSED (frame declares 384 elements at f32 (1536 B), decoded 1524 B)
```

`b64decode` **refuses** a cut that is not a multiple of 4 characters (`Incorrect padding`, or *"cannot be 1
more than a multiple of 4"*) — so the danger is not a partial quantum. It is the **aligned** cut: 4 base64
characters are 3 whole bytes, so a body shortened by a multiple of 4 characters is still **valid** base64
and decodes silently to fewer bytes. Since `3k ≡ 0 (mod 4)` iff `k ≡ 0 (mod 4)`, only every fourth such cut
leaves a length that is a multiple of the 4-byte element — and at **−16 characters** that is exactly the
case (1,524 B = **381 whole floats**), so a reader checking only `len(decoded) % 4` **accepts a silently
truncated vector**. Only the declared count refuses it. (This is the fail-open direction on the durability
artifact: a short vector replayed into the graph is a wrong graph with no error anywhere.) `validate=True`
is still mandatory — the default `False` discards characters outside the alphabet instead of refusing. The
probe asserts both halves: every multiple-of-4 cut decodes silently, and `−16` is the only one of them whose
decoded length passes a `%4` check.

The reader's four refusals, all measured in the probe:

```
== the reader refuses (each raises, none decodes silently short)
  truncated body, padding intact             REFUSED (ValueError, decoded 1,533 B)
  one element short, count unchanged         REFUSED (ValueError, decoded 1,532 B)
  packed f64 under a declared f32            REFUSED (ValueError, decoded 3,072 B)
  unknown version                            REFUSED (ValueError, decoded 1,536 B)
  non-base64 character in body               REFUSED (Error)
```

### 2.3 What "lossless for the float32 the model emitted" means, precisely — measured

Two different claims get conflated under "lossless", and P2 changes which one holds:

- **P0 (today):** the journal holds `float64` values whose *text* is the shortest decimal that rounds back
  to the same float32 (`shortest_float32_vector`). The stored float64 is therefore deliberately **off the
  float32 grid** — `0.0012301534`, not `0.001230153371579945` — and equality is defined on **float32
  bits**, which is why `consistency._as_f32` / `stored_form.narrow_to_stored_width` narrow the truth side
  (#8615).
- **P2:** the journal holds **the float32 bits themselves**. There is no rounding to reason about: decode
  gives back exactly the 4 bytes that were packed.

That acceptance property is stated at the right width: **`base64 → float32 bits == P0 text → float32 bits
== the producer's float32 bits`**, verified on **all five** vector-bearing records in the corpus (5 of 39;
the probe loops every record, not one per type — `p2_base64_probe.py`, "decode equivalence"). It is **not**
float64-value equality with P0's text, and a test that asserted that would fail on every element by design.

That 9 significant digits suffice for float32 is float32's own structure (24-bit significand ⇒ 9 decimal
digits round-trip; 8 do not always). Checked rather than cited, on 2,000,000 random float32 values:

```
  %.7g -> float32:   663,427/2,000,000 exact, 1,336,573 MISMATCH
  %.8g -> float32: 1,989,242/2,000,000 exact,    10,758 MISMATCH
  %.9g -> float32: 2,000,000/2,000,000 exact,         0 MISMATCH
```

This is why P0's text costs ~12 characters (≈9 digits, sign, point, exponent) and why the 5.33 chars/float
of base64 is below the *information-theoretic* floor of any decimal encoding: 4 bytes carry
`log10(2^32) ≈ 9.6` decimal digits, and base64 spends 5.33 characters on them.

### 2.4 Two widths, one frame — the `#5004` half cannot be uniform

`f32` → base64 of `struct.pack("<…f", …)`; `f64` → base64 of `struct.pack("<…d", …)`. Same frame, different
element size, declared in the frame **and** cross-checked against P0's `embedding_width`:

- both present and **disagreeing** → refuse the record (a marker that does not describe its own payload is
  the one thing P0's marker exists to prevent — the same failure `_is_shortest_f32_text` was added to
  close, #11835 P0/F2);
- frame present, `embedding_width` **absent** → refuse (no pre-P0 record may carry a frame);
- `embedding_width` present, value is a plain JSON list → P0 shape, read as today (the mixed-log path).

The duplication is deliberate: it costs **4 B of the 16 B frame** (the `f32:` width token, which is
already implied by `embedding_width`) and it removes the only way the width can be wrong at rebuild
time without anything noticing. Where a checked duplicate and a single source of truth conflict, the
unrecoverable direction wins.

### 2.5 The log line stays newline-delimited and parseable — and why not raw bytes

Raw packed float32 is **1,536 B**, i.e. 512 B per record cheaper than base64. It is refused, and this is
measured rather than asserted: on the probe's deterministic unit vector (`unit_vector()`, seed 5612,
normalised — the vector the probe injects; no corpus record carries it), the raw packet contains

```
  contains 0x0A (newline): 4    contains 0x22 ('"'): 4    contains 0x00: 7
```

Raw float32 bytes include `0x0A` and `0x22`, so they cannot live in a JSON string unescaped and cannot be
a JSONL line. Carrying them would need a **binary framing layer** — length-prefixed, non-newline-delimited —
which is a *different log*, and it would retire the whole existing torn-tail classifier (§2.6) in exchange
for 512 B/record. Base64's alphabet excludes both bytes (RFC 4648's 64 symbols contain neither; measured
in the appendix: `0x0A=0`, `0x22=0`), so:

- the line stays one JSON object, one `\n` terminator, written by **one `write(2)`** on an `O_APPEND` fd —
  the property that makes a record un-interleavable (`_write_record`);
- `read_all`'s existing split-on-`b"\n"` is untouched, including its deliberate refusal to use
  `str.splitlines()` (which would split a raw U+2028 inside a content string);
- the +33.3 % is the price of **not** replacing the reader, the writer and the tear contract at once.

### 2.6 What a torn tail looks like under the new frame — unchanged, and why

The journal's existing machinery, which this format does **not** touch:

1. `append` appends; a writer killed mid-`write(2)` leaves an unterminated fragment.
2. The **next** `append` *seals* it — terminates the fragment and appends `__TornTailSealed__` (a bare,
   deliberately non-JSON line) — instead of writing onto it (#5917: appending onto a fragment merged two
   records and **lost the second**, permanently).
3. `read_all` splits on `\n`, tolerates a malformed **trailing** line, counts it, retains its raw text
   (`torn_trailing_raw`), and `#3316`'s classifier decides from the fragment's prefix whether dropping it
   could **revive removed state** — refusing the replay if so.

Under the new frame every one of those steps is identical, because the frame is **inside a JSON string**:

- a tear anywhere in the record — before `"embedding"`, inside the prefix, inside the base64 body — leaves
  the JSON **unterminated** (no closing quote/bracket), so the line is malformed under exactly today's
  rule. There is no truncation of a base64 body that is *also* valid JSON, which is the new shape that
  could have hidden from the classifier.
- `record_types_from_partial`/`_torn_type_key_at_end_of_line` read the fragment's **prefix** for a `"type"`
  key. The frame changes only the `embedding` **value**, which sits deep in the payload — after `type` in
  every shape (measured: `type` is the first key in **39/39** of the committed corpus's records — the
  fixture's own order, which `EventLog.append` preserves; the **SDK** writer builds its envelope
  `event_id, ts, type` (`sdk._emit_event`), so under the writer's compact separators the `"type"` key
  opens at byte **79** and is third). So a tear late enough to keep the type keeps it exactly as today, and
  a tear before it is already #3316's ambiguous case, which **refuses** rather than guesses. The frame
  moves neither boundary.
- seals and torn markers are **line-level** artifacts, not payload-level, so nothing about them moves.

**The one new interaction, stated:** a torn record now loses the same number of *replayed* records (it was
always dropped, never half-applied), but a human salvaging the fragment can no longer *read* the surviving
tail — a base64 body truncated on a 4-character boundary still decodes to a **prefix** of the vector
(measured: −4 chars → 383 floats, −8 → 382, …), but only after the human trims it to that boundary and
decodes it. So hand-recovery changes **shape**, not existence. Cost, not correctness; recorded under
Rollback (§6) as a permanent loss.

---

## 3. The reader for old lines — the surface where the danger lives

A payload that appends fine and cannot replay is *the* defect, and it surfaces only when a rebuild is
needed — i.e. at the worst possible moment, against a journal nobody can re-create.

### 3.1 One dispatch, in one place

The frame is decoded in **`EventLog.read_all`**, at the record-read boundary, so every vector consumer
(`fold`, `rebuild_all`, `begin_ingest`'s cache scan, `record_projection`, `recover_from_log`, the backup
and cursor readers) still sees **exactly the shape it sees today** and needs no change. One spelling of
the frame, one place to get it wrong — the same reason P0 put the width decision in one seam.

**The bypasses, named rather than left to be found.** Three journal line-parsers do *not* go through
`read_all`: `log.py`'s seal/tail detection, `subject_binding`'s key scan, and `MentionScan`
(`log.py:1243`, used by `EventLog.mention_lines` and `sdk._journal_presence_state`). All three read
`type`/id-shaped keys and **never** `embedding` — measured at this head: `embedding` appears as a **code**
token on **no line** of `log.py` or `subject_binding.py` (tokenised, comments excluded; `log.py`'s eight
mentions are all comment prose), and `_apply_journal_presence` — the parser behind `MentionScan` — and
`_journal_presence_state` contain none. So an undecoded frame cannot reach them. That is a property to
keep, not an accident to assume: the implementation's tests must red if any of them starts reading a
vector, and they must spy on **`_apply_journal_presence`**, not only on `MentionScan`, or the symbol that
could actually start reading one is unwatched. (`sdk._journal_vector` is the opposite direction — a
`Vectorf32` → JSON-able list converter on the **write** path — and reads no journal.)

Dispatch is on the **value**, per record:

| `embedding` value in the line | meaning | reader |
|---|---|---|
| a JSON **list** | P0 (float32 shortest text) or pre-P0 (float64 text) | unchanged — the existing path |
| a **string** starting `b64:` | P2 frame | decode per §2; refuse on any of the four corruptions |
| **absent** | pre-#5004 / foreign record | unchanged — recompute is the only behaviour available (R1) |
| `null` | owned "no vector" | unchanged — the replay must **not** invent one (R1) |
| a string **not** starting `b64:` | impossible; the seam drops every string | refuse with a message naming the value |

Because the dispatch is per record, a mixed-version journal needs **no state**: an old line, then a new
line, then an old line replays correctly in one pass. That ordering is not hypothetical — it is what a
rollback (§6), a restored backup, or two hosts on different builds produce.

### 3.2 The test that proves a mixed-version log replays end to end

Named here so the implementation cannot choose a weaker one:

- `tests/test_12459_mixed_version_replay.py` (§3.2 pins), each pinning one failure it prevents:
  1. **old → new → old, one journal, replayed**: write a pre-P0 line, append a P2-frame line, append
     another old-shape line, then `rebuild_all` and compare the resulting graph to the graph built from the
     same records written entirely in the old shape. *Prevents:* a reader that assumes one format for the
     whole file.
  2. **the vector survives the rebuild bit-identically**: after replay, each Point's `vecf32`-widened
     vector equals the float32 the producer emitted — narrowed through `stored_form`, never compared as
     float64 (the #8615 rule, unchanged).
  3. **a verbatim vector is restored un-narrowed** and is off the float32 grid on purpose, so the test
     cannot pass vacuously (P0's fixture rule).
  4. **a torn tail under the new frame still replays**: append a P2-frame record, truncate the file
     mid-body (no terminator), `read_all` → the record is dropped, counted in `torn_trailing_count`, kept in
     `torn_trailing_raw`, and the replay **succeeds** for a harmless type / **refuses** for a reviving one —
     i.e. #3316's behaviour, unchanged, with the frame in the record.
  5. **a sealed fragment before a P2 record still costs nothing**: the #5917 shape (torn fragment, then the
     next append) with P2-framed records — the record after the seal is present, once.
  6. **the four refusals of §2.2 each red**: truncated body (−16 chars, the measured silent case), short
     array with the count unchanged, unknown version, non-base64 character — each must raise, not
     silently-short.
  7. **`append` decodes nothing**: a decoded record re-appended writes a **frame** (the encoder is at the
     write boundary, not on the in-memory record), so re-stamping a read record cannot flip its width —
     the #11835 F2 class of defect, one layer out.

### 3.3 The failure this section exists to prevent

`rebuild_all` is the only consumer that can be wrong **catastrophically rather than visibly**: it wipes and
reconstructs. A reader that skipped a frame-bearing record would rebuild a graph **missing vectors** and
report success; a reader that decoded wrongly would rebuild **different vectors** and report success. Both
are silent. That is why the review of this note and of the implementation must be on the **rebuild** path
and not the append path — the issue says so, and it is right.

---

## 4. `#5004`'s interaction, explicitly

### 4.1 Which population keeps RAW form

`#5004` round-7, verbatim from `projection/__init__.py::_rebuild_all_replay`: *"a CALLER-owned vector must
keep its RAW form. `vecf32()` is the ONE form it must not take."* P0 named that population in the payload:

- **`embedding_verbatim`** — a vector the **caller** supplied (set by `TortoiseSDK.create_point`'s
  `_create_map`), which the store writes un-narrowed
  (`CASE WHEN $embedding_verbatim THEN $embedding ELSE vecf32($embedding)`);
- **`embedding_preserved`** — a vector copied from a **previous** capture (possibly anyone's), which the
  turn carrier does not mark verbatim, so it is the only signal there.

Both stamp `embedding_width: "f64"` and their values are left **untouched**.

### 4.2 How the encoding preserves that — it encodes f64, it does not narrow it

Under P2 that population is base64 of **packed float64** (`struct.pack("<…d")`): every bit of the caller's
value survives, and the lossless property is *stronger* than under P0, where the value's text was already
float64's shortest repr (also exact, but only by `repr`'s guarantee). Measured on an off-the-float32-grid
fixture (the P0 test's rule, so the check cannot pass vacuously):

```
  float64 frame round-trip bit-identical: True
  …and it is genuinely off the float32 grid: True   (so a narrowing WOULD be visible)
  f64 spans: today (JSON text) 5,770 B → frame 4,112 B  (1.40×)
```

So the verbatim population **shrinks too** (1.40×, since 15.0 chars/float of float64 text → 10.7 base64
chars/float) while being narrowed **not at all**. The frame's width field is what carries the promise:
`f64` in, `f64` out.

### 4.3 What happens to a caller-owned vector under the new format

It is encoded at float64, decoded at float64, and restored through the same `embedding_verbatim` guard that
exists today — the guard is **not** touched by the encoding, because it reads the marker, not the width.
R1 is likewise untouched in substance: presence is ownership still (the key is present in every case), the
replay still restores rather than recomputes, and `embedding_model` / `embedding_revision` /
`embedding_text_hash` still ride the creating record. **The one thing the implementation must prove** is
that the decoded `f64` values reach the verbatim guard as `float64` (not pre-narrowed by the decoder) —
pinned by §3.2 pin 3.

### 4.4 Would this encoding narrow it?

**No.** Which means this is **not** a reopen. Had the design chosen one uniform float32 encoding, it
**would** narrow the verbatim population and it **would** be a reopen — reopening #5004's round-7 ruling in
its own home, with the evidence, rather than adopting over it. It does not, so no reopen is opened and the
ruling is unchanged: the frame carries two widths precisely so it does not have to override it.

---

## 5. Blocked-on

**Implementation is blocked on P1 (#12458) merged and measured.** This is #11821's own sequencing rule,
stated on the PR that landed P0:

> **P2** — base64 float32 payload: **design note on #11821 first**, and no implementation until P0 and P1
> are merged and measured. It is a format migration on the rebuild path and shares a PR with nothing.

Consequences, stated so the note cannot be read as an authorisation:

- **P1 is a separate lane and is not this lane's to wait on.** Its measurement is an **input to §7's
  recommendation**, not a precondition for the note to exist. If P1 lands and moves the corpus figures, §1's
  arithmetic must be **re-measured**, not adjusted — the probe is committed for exactly that.
- **P2 shares a PR with nothing.** Not P1's, not #11886's, not a docs sweep.
- **Nothing in this note may be implemented before P1 is measured**, and the note is not evidence that any
  of it has been approved for implementation.

---

## 6. Rollback — if the format proves wrong after it ships

The frame was chosen so that this question has a cheap answer, which is the strongest argument for §2.1:

| scenario | recovery | permanently lost |
|---|---|---|
| the **decode** is wrong (unpack, endianness, the count) | **rebuild from records**: the journal bytes are intact and the frame is self-describing, so a fixed reader replays them correctly. The graph is rebuilt, not restored. | **nothing** — the bytes were never destructive |
| the **encode** is wrong (pack) | a wrong pack puts wrong bytes **in the journal**, so a rebuild replays them faithfully and wrongly. A pack that violates the frame's own length (packed `<d` under a declared `f32`, or 383 elements under a count of 384) is **refused** by the count — measured: 3,072 B and 1,532 B decoded against a declared 1,536 B. A pack that is self-consistent but wrong (element order, a transposition) decodes **silently**, and the replay writes the wrong vector with no error — §3.3's failure exactly. | **the vector as written**, in the silent case: the journal is the record of record, so the correct value cannot be recovered from it. The rest of the record still rebuilds. |
| the format is wrong (base64 turns out to be the wrong choice) | **dual reader**: both shapes stay readable indefinitely, because dispatch is per record and needs no state. The writer reverts to JSON text; journals that already carry frames keep replaying. | **nothing** — no record is rewritten, so no migration is needed in either direction |
| the frame is withdrawn entirely | a reader that stops decoding still has to keep understanding frames for as long as any frame-bearing journal exists. That reader is a **permanent** obligation of the format. | a second reader path, forever |
| base64 makes the log un-inspectable in an incident | a decode helper (`tools/journal_decode.py`-shaped) restores human readability on demand; it is a **tool**, not a format change | **human greppability of the vector** — you can no longer read, diff or hand-edit a vector in the log by eye. This is a real, permanent loss and the main non-byte cost of P2. |
| a tear loses more of a record than before | nothing to recover: the record was never half-applied | **hand-recoverability of a torn vector**: a base64 body truncated on a 4-char boundary still decodes to a **prefix** of the vector, but only after a human trims it to that boundary and decodes — so hand-recovery changes **shape**, not existence (§2.6) |

**What is permanently lost if the format ships and then is withdrawn:** the simplicity of a single-shape
journal — every frame-bearing journal is thereafter a second code path that must keep a reader. That cost is
**incurred by shipping at all**, not by withdrawing, and it is the reason §7 exists.

---

## 7. Recommendation — stated plainly, because the number is smaller than the headline

**The format in §2–§4 is the correct one if P2 is taken.** It is the only framing that satisfies all four
recorded constraints at once: per-record versioning (append-only), a count that fails closed on the
measured silent-truncation path, two widths so #5004 is preserved rather than overridden, and JSONL
compatibility so the tear contract and the reader are not replaced. The decision it *does not* make is
whether P2 is worth taking now, and the honest numbers for that are:

- **the remaining win is 1.94× on a creating record and 2.27× on its vector — not 4.1×** (§1.4);
- **≈ $0.33/mo per million creates** at $0.125/GB, on a line #11758 has not established is metered at all;
- against **a format migration on the rebuild path** — the one surface whose failure is a silently wrong
  graph — plus a **permanent second reader** and the loss of hand-inspectable vectors.

**Recommendation:** if P1's measurement shows the vector still dominating the journal, take P2 **with this
format**, and sequence it as its own PR with §3.2's seven pins as the review scope. If P1 shows the record
line is no longer vector-dominated in practice, the 1.94× is being bought against the wrong risk, and the
correct answer is to **not take P2** — and this note then stands as the recorded design that was declined,
which is cheaper than rediscovering it. That judgement belongs to whoever holds P1's measurement, not to
this note.

---

## Appendix — reproducing every figure

```bash
# 1. everything in §1, §2.2 and §4, the silent-truncation sweep, and the decode-equivalence proof
env -u TORTOISE_DB_URI uv run python \
    docs/evidence/12459-base64-payload/probes/p2_base64_probe.py

# 2. the P0 baseline the note starts from (reproduces 5,423 B / 4,689 B)
env -u TORTOISE_DB_URI uv run python \
    docs/evidence/11821-journal-encoding/probes/journal_encoding_probe.py

# 3. the 9-digit and raw-byte measurements quoted in §2.3 / §2.5
uv run python - <<'PY'
import base64, struct
import numpy as np
rng = np.random.default_rng(12459)
v = rng.standard_normal(2_000_000).astype(np.float32)
for d in (7, 8, 9):
    back = np.array([float("%.*g" % (d, float(x))) for x in v], dtype=np.float32)
    print(d, int((back != v).sum()))
# the SAME vector the probe uses (§2.5's counts), then the appendix's own
for seed, skip in ((5612, 0), (12459, 2_000_000)):
    r = np.random.default_rng(seed)
    if skip:
        r.standard_normal(skip)
    # NORMALISED, exactly as the probe's unit_vector() does — the raw-byte
    # counts below differ on the un-normalised draw.
    v = r.standard_normal(384).astype(np.float32)
    v = (v / np.linalg.norm(v)).astype(np.float32)
    raw = struct.pack("<384f", *[float(x) for x in v])
    body = base64.b64encode(raw)
    print(seed, len(raw), raw.count(b"\n"), raw.count(b'"'), raw.count(b"\x00"), len(body),
          "b64 0x0A=", body.count(b"\n"), "0x22=", body.count(b'"'))
PY
```

# 4. the §3.1 claim that no bypassing reader touches a vector: `embedding` as a CODE
#    token (comments excluded) on every journal line-parser that skips `read_all`
uv run python - <<'PY2'
import io, inspect, tokenize
import tortoise.sdk as s
for path in ("tortoise/log.py", "tortoise/subject_binding.py"):
    code, comments = [], 0
    for t in tokenize.generate_tokens(io.StringIO(open(path).read()).readline):
        if t.type == tokenize.COMMENT and "embedding" in t.string:
            comments += 1
        elif t.type == tokenize.NAME and t.string == "embedding":
            code.append(t.start[0])
    print(f"{path}: code lines {code or 'none'}; comment mentions {comments}")
for fn in ("_apply_journal_presence", "_journal_presence_state", "_journal_vector"):
    print(fn, "mentions embedding:", "embedding" in inspect.getsource(getattr(s, fn)).lower())
PY2
```

# 5. the §2.6 key-order claim: `type` first in all 39 corpus records; the SDK
#    envelope puts it third
uv run python - <<'PY3'
import json
from tortoise.ids import ulid, now_iso
recs = [json.loads(l) for l in open("supabase/tests/fixtures/record_projection_journal.jsonl") if l.strip()]
firsts = [list(r)[0] for r in recs]
print(f"records {len(recs)}; type is FIRST key in {firsts.count('type')} of {len(recs)}")
# the SDK writer's envelope order (_emit_event): event_id, ts, type, …
# COMPACT separators, which is what EventLog.append writes (log.py:1563).
env = {"event_id": ulid(), "ts": now_iso(), "type": "PointAdded"}
j = json.dumps(env, separators=(",", ":"))
print("type key opens at byte", j.index('"type"'), "| key position", list(env).index("type"))
PY3
```

Suites touched by the *design* (none by this document — it changes no code): `test_11821_journal_vector_width`,
`test_journal_growth`, `test_log`, `test_5917_torn_tail_seal`, `test_5612_journal_append_serialization`,
`test_10017_record_carrier`, `test_10017_turn_carrier_replay`. The implementation's own suites are §3.2's.
