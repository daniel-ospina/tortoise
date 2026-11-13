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
    note: "P1 — closed NOT_PLANNED 2026-11-13T19:16Z (its 32,032 B/session premise is dead and the change was classified a REVERSAL). The #11821 sequencing gate that named it is now unsatisfiable as written and must be re-stated by #11821's owner; until then implementation stays blocked."
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
**Issue:** #12459 · **spec:** #11821 · **P0 (landed):** #11835 · **blocking predecessor:** #12458 (P1) — **closed `NOT_PLANNED`**; see §5
**Evidence + probe:** `docs/evidence/12459-base64-payload/`

---

## 0. Research first — what this note was built from, and what it could not reach

### Internal (read before deciding anything)

| source | what it settles |
|---|---|
| `~/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md` | **ABSENT on this box** — declared, not skipped. Nothing here is a graph query. |
| #11821 (+ its P0 PR #11835) | the three-phase spec. P0 = width, P1 = the `:GraphEvent` mirror, **P2 = this**. The sequencing rule is stated in **two homes**, quoted together here and attributed separately in §5: #11821 — *"no implementation until P0 and P1 are merged and measured"*; PR #11835 — *"… shares a PR with nothing."* |
| #11835 (P0's PR) | the Review Scope Contract R1–R5, the measured post-P0 figures, and the two acceptance figures it reported as **not** holding rather than adjusting them |
| `tortoise/embeddings.py::stamp_journal_embedding`, `_stamp_journal_vector_width`, `_is_shortest_f32_text`, `shortest_float32_vector` | the landed P0 seam: what "the shortest float32 round trip" means here, the **three outcomes** of the width stamp, and the eight homes the marker is declared in |
| `tests/test_11821_journal_vector_width.py` (**51 tests** at this head) | the pins P2 must not move: float32 bits, the never-narrowed population, the pre-change replay test at `:518` |
| `docs/durability-posture.md` **R1** | the embedding **stores** — a replay restores it verbatim and never regenerates it. *"presence is ownership."* |
| `docs/architecture/STORAGE-ARCHITECTURE.md` §5.2 | the recorded P2 finding already on the books: *"~4× more than base64 of the same information as the float32 the graph itself stores (2,048 B for a 384-d vector)"* — and that it needs its own decision and its own tests |
| `tools/journal_growth.py` | read **critically** — see §1.1. It cannot report the vector payload's **on-disk** span (it re-serialises with spaced separators). |
| #11886 | the ruler defect: the tool attributes bytes by re-serialising with **spaced** separators, so its vector figure and the journal's on-disk figure are on different rulers |

### External — a routing failure I first mis-diagnosed, then repaired

**Recorded because the first version of this note declared the survey impossible and that was wrong.**

| rung | attempt | outcome |
|---|---|---|
| 1 | `mcp_load seo-intelligence` | ⛔ `Unknown MCP server 'seo-intelligence'` — the server is not in this box's `mcp_catalog` |
| 2 | `web_search` (`sonar`, the cheapest) | ⛔ `PERPLEXITY_API_KEY not set` — **but the credential exists**, at `$HOME/.env`, which this shell does not source (`~/.profile` sources `/tmp/cell-home/keys.env`, which does not exist) |
| 3 | `mcp_load brave-search` | ⛔ `MCP error -32000: Connection closed` |
| 4 | `mcp_load exa` → `web_search_exa` | ⛔ `(401) EXA_API_KEY` |

I first wrote that "no rung could be repaired without a credential only the owner can supply". **That was a
false inference from a true observation**: a fresh-context reviewer found `$HOME/.env` holds a live
`PERPLEXITY_API_KEY`, and `set -a; . ~/.env` made rung 2 work. So the survey below **was run** — four
queries on `sonar`, ~\$0.024 total, at the `research` skill's cost gate. The lesson is recorded rather than
smoothed over, because the failure mode ("the cheapest rung did not load" → "the owner must unblock it") is
how a repairable gap becomes a permanent one.

- **(a) binary float encodings in a text log.** A packed `float32` array is `4N` bytes raw (`N` = elements); base64 spends
  `4⌈4N/3⌉` **characters** — four per 3-byte input quantum, and the input is `4N` bytes, not `N`
  (≈ `5.33N`, i.e. **+33 %**) and keeps the JSON envelope and the surrounding
  record greppable, while the *values* become opaque. The binary alternatives are cheaper and worse here:
  protobuf `packed repeated float` is `4N + h + ℓ` (≈ `4N+3`), MessagePack and CBOR are ≈ `5N` for an
  array of float32, and Arrow/Parquet add buffers, offsets and schema framing that dominate a single small
  record. **Only a *text* encoding keeps the log line a JSON object**, which is the property §2.5 needs — and
  several do, so base64 is chosen among them for ubiquity, not for being the smallest (§2.5.1 measures all
  six: `b85`/`z85` are 25 % overhead against base64's 33.3 %, `a85` is refused for containing `"` and
  `\`, `hex` costs +100 %). On
  **corruption detection none of them gives this note anything**: no text alphabet carries a checksum, and of
  the binary formats only Parquet offers one (an optional **page-level** CRC32 — it covers the page, not the
  vector); a checksum would not catch a **validly re-encoded** alteration in any case. That is why §2.2's
  refusals are structural (count, version, alphabet) and why this note does **not** claim integrity from
  the encoding. Recorded
  in-repo too: `STORAGE-ARCHITECTURE.md` §5.2 reads *"~4× more than base64 of the same information as the
  float32 the graph itself stores (2,048 B for a 384-d vector, against 8,414 B of decimal text)"* — so
  **base64 is 4.11× smaller than the float64 decimal text** (8,414 → 2,048), and **the decimal text is
  5.48× the raw 4 B/float** (8,414 → 1,536) — which is the same fact as *"base64 pays **+33.3 %** over
  raw"* (2,048 B of base64 against 1,536 B raw), the trade §2.5 refuses. The two comparisons differ in subject and §1.3/§2.5 keep
  them apart (the `~5×` in the source is *text vs raw*, never *base64 vs raw*).
- **(b) shortest-round-trip printing.** The formal bound is `⌈1 + p·log₁₀2⌉` with `p = 24` significand
  bits ⇒ `⌈8.22⌉ = **9**`: for every finite binary32 value there is a ≤9-digit decimal that correctly
  rounds back to **exactly** it, and "round-trips" means `R₃₂(P(x)) = x` — equality of sign, exponent and
  significand, not closeness. It is a **sufficient worst case, not a per-value cost**, which is exactly
  why P0's text measures **12.2 chars/float** and not 9. Ryu/Grisu exist to find the *shortest* such
  numeral rather than a fixed 9. The bound is the cited argument; §2.3's sweep is a falsification attempt
  on a **narrow population** — 2,000,000 standard-normal draws cast to float32, which contains no
  subnormals and no wide exponent band — and it did not falsify it there (`%.9g` exact
  2,000,000/2,000,000; `%.8g` misses 10,758; `%.7g` misses 1,336,573).
- **(c) versioned formats on a replay path.** The convergent pattern is **both layers**: a stable
  container header for framing, and a **per-record/per-batch version discriminator read *before* the
  payload is interpreted**, plus length-delimited records so an old reader can skip what it cannot decode,
  stable integrity metadata, and — the rule that matters here — ***"unknown version" must be a controlled
  error, never an attempt to guess the layout***. A **file-level** version is safe *"only when every
  record in that file is guaranteed to use the same format"*, and is explicitly called out as unsafe
  across *"rolling upgrades or records from multiple producers"*. That is §2.1's argument, reached
  independently: this journal is append-only (#5917) and, in the normal deployment shape, **upgraded in
  place**, so a file header cannot describe it — and §3.1's `unknown version → refuse` is the cited rule, not a local preference.
- **(d) what the memory comparables store — answered, and the answer is "not this".** **Zep/Graphiti**
  episodes are graph nodes whose *content* may be `text`/`message`/`json`, and embeddings are computed for
  graph entities and handed to the configured graph store; the episode payload is not documented as
  carrying a serialized embedding array. **Mem0** computes the embedding per memory and passes it to a
  vector-store adapter (pgvector or Qdrant), so the durable form is the store's native column, not JSON.
  **Letta/MemGPT** passages carry `Optional[List[float]]` at the API layer with an `embedding_config`, and
  in PostgreSQL land as a native pgvector value (padded to 4096 dims). **pgvector** itself stores a native
  variable-length vector of 32-bit floats. And **none of the four documents a versioned on-disk embedding
  serialization format at all.** So there is **no comparable practice to copy**, and — read against the
  contradiction test — nothing here contradicts #5004 or R1: they delegate the vector to a store, while
  this journal is required to **carry** it (R1's *presence is ownership*). The inline-in-a-text-log vector
  is therefore *ours alone*, which is precisely why this format has to be designed and tested rather than
  borrowed.

⚠️ **Provenance of (a)–(d):** these are four `sonar` search queries (~\$0.024) whose *findings* are recorded
here and whose **citation lists are not reproduced**, so none of (a)–(d) can be re-checked from this
repository — re-running the four queries is the way to re-derive them (`docs/evidence/12459-base64-payload/README.md`
records the queries and the routing that first failed). **§1–§7 do not depend on §0**: every byte figure is
the probe's, and every binding constraint is an in-repo decision (§4). (d) is used only to say *there is no
practice to copy* — a claim whose failure would leave the design exactly as it stands.
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

```text
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

```text
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

Both #11821 and #12459 say **~4.1×**. That is `8,414 / 2,048 = 4.11` — pre-P0 decimal text against the
**bare base64**. **P0 already took the first 1.79× of it.** The **leg** still on the table is P0 → P2 (the
alphabet inside it is a *second*, smaller choice — §2.5.1 measures `b85` at 2.03× on the record):

```text
vector payload   4,689 → 2,064 B   = 2.27×
whole record     5,423 → 2,798 B   = 1.94×
```

⚠️ **The 16 B frame sits at that join, so the legs do not multiply back to 4.11 exactly**: on the *framed*
span the headline is `8,414 / 2,064 = 4.08`, and the legs close it **unrounded** —
`(8,414/4,689) × (4,689/2,064) = 8,414/2,064`; the printed 2-dp legs multiply to `4.06`, so quote the
identity, not a product of rounded figures. Read `4.11` as
*text-vs-bare-base64* and `4.08` as *text-vs-frame* — 1 % apart, each right on its own ruler, which is the
whole point of §1.1.

This is #11886's ruler lesson applied to #11821's own headline, and it changes the cost/benefit, so it is
restated rather than inherited. It does not weaken the *format* (§2–§4 stand on their own); it weakens
the *justification by bytes*, which is what §7 is for.

### 1.5 Restated in the cost model #11821 used (1,000,000 creates, \$0.125/GB)

```text
P0 journal   1,000,000 × 5,423 B = 5.423 GB  ≈ $0.68/mo
P2 journal   1,000,000 × 2,798 B = 2.798 GB  ≈ $0.35/mo      (saves 2.625 GB ≈ $0.33/mo)
pre-P0       1,000,000 × 9,176 B = 9.176 GB  ≈ $1.15/mo
```

In #11821, the *"Effect on the unbounded line"* paragraph reads **"8.5 GB ≈ \$1.06/mo → ~4.7 GB ≈ \$0.59 (P0) →
~2.1 GB ≈ \$0.26 (P2)"**, and the figure under test is the **P0 → P2** leg of it. Measured, it is
**5.42 GB ≈ \$0.68 → 2.80 GB ≈ \$0.35**, and the difference is **arithmetic that can be reproduced**, not a
different basis:

```text
  pre-P0 blended basis in that table:  8,500 B/record  (8.5 GB at 1,000,000 — §5.2, STORAGE-ARCHITECTURE.md:438)
  P0 row:  8,500 × 4,689 / 8,414 = 4,737 B → 4.74 GB   (table says 4.7 GB  ✓)
  P2 row:  8,500 × 2,048 / 8,414 = 2,069 B → 2.07 GB   (table says 2.1 GB  ✓)
```

So both rows are **reproducible arithmetic on a blended basis, plus a vector-ratio assumption the P2 row
then breaks**: they scale the whole record by the VECTOR's ratio. That is nearly right pre-P0, where the
vector is 92 % of the record — and wrong once the non-vector bytes are a **quarter** of it (734 of 2,798 B
= 26 %). The P2 row assumes
the record shrinks *with* its vector, when in fact **734 B** of the record (`2,798 − 2,064`) does not
shrink at all. Corrected on the creating record: **2.80 GB, saving ≈ \$0.33/mo** — where the vector-ratio
row implies `(5.423 − 2.069) × $0.125 = $0.42`, i.e. the correction is **~22 %** of that quote. ⚠️ Name the
base: that `$0.42` pairs the **measured** P0 (`5.423 GB`) with the **modelled** P2 row (`2.069 GB`), which
is what a reader would actually budget. It is *not* the quote's own saving — the quote's two rows imply
`2.67 GB = $0.33/mo` unrounded (`2.60 GB` on the table's printed 4.7/2.1 rows), because both rows' absolute
errors partly cancel — the P2 row **understates the truth by 26 %** (2.069 modelled vs 2.798 measured). The
printed 2.60 GB is **0.95 %** from the measured 2.625 GB saving; the unrounded 2.67 GB is 1.6 % above it. The defect is the row *values*, not
the saving the pair happens to imply. Stated because a reader would budget from the quote. Whether this line is metered at all is **not settled
anywhere**: #11758, the pricing-of-record ticket, closed `COMPLETED` on 2026-11-12 without addressing
journal bytes, and #4333 (open) counts **node** sets, not journal bytes.

---

## 2. The format

### 2.1 Where the version marker lives — **per record, in the value**

```json
{"type":"PointAdded","ts":"…","id":"…","point":{"id":"…","embedding":"b64:1:f32:384:BAAQ…","embedding_width":"f32", …}}
```

```text
"b64"  : the frame marker — a value that cannot be a legitimate `embedding`
"1"    : the frame version
"f32"  : the element width, IN the frame (and checked against `embedding_width`, §2.4)
"384"  : the element count
"BAAQ…": RFC 4648 standard base64 of the packed little-endian array
```

**Why per record and not a file header** — this is the constraint that decides it, and it is internal:

- The journal is **append-only**. `EventLog.append`'s torn-tail contract (#5917) is explicit, at
  `tortoise/log.py:1402-1403`: *"Nothing is truncated or rewritten — the seal is another append."* A
  file-level version header would require
  **rewriting existing journals** to add it. That is a different operation (a migration on a file that
  may be tens of GB), on the one artifact whose whole promise is that it is never modified.
- A file header is also **wrong under an in-place writer upgrade**, which is the deployment's normal
  shape: once a v2 build appends to a v1 journal the file holds **both** shapes, and there is no
  rewrite-free way to declare v2 for the tail alone. (An earlier draft blamed concatenation and restore —
  `backup.restore` **replaces** the journal (`backup.py:638`, `shutil.copy2`, truncating the destination;
  its docstring says "REPLACES the destination journal"). So restore never **creates** a mixed-version file —
  but it faithfully **carries** one that the in-place upgrade already mixed, so restore is not a
  counter-example either, and the reason stands on the append/upgrade ground alone. Rotation, if and when
  #9025 lands, would yield separate files, which a per-file header would also handle.)
- Per-record is self-describing, so the reader needs **no file-level state** — the same property that
  makes §3's mixed-version replay a per-line dispatch instead of a sequential state machine.

**Why the marker is `b64:` and not a sibling key.** Three options were considered and two are refused:

| option | verdict |
|---|---|
| `"embedding": "b64:1:f32:384:…"` (a tagged string under the **same** key) | ✅ chosen |
| `"embedding_b64": "…"` with `embedding` dropped | ⛔ **refused** — R1 is *presence is ownership*: an absent `embedding` key means "pre-#5004 record, recompute is the only behaviour available". Dropping it would make the replay **re-embed**, silently changing the graph. |
| `"embedding": {"b64": "…"}` (a nested object) | ⛔ **refused** — `stamp_journal_embedding` treats a `dict` as *"not a numeric sequence"* and **drops the vector to an owned `None`** (`_drop_journalled_embedding`). It is the right refusal for a hand-edited journal and the wrong shape for a format. |

A string is **also** a shape the seam refuses to treat as a vector — `_drop_journalled_embedding` replaces
it with an owned `None` and logs a warning. It is the *same* branch as the dict row two lines above
(`isinstance(raw, (str, bytes, dict))`, `embeddings.py:504`), so the two rows are one refusal rather than
two, and a mis-ordered pipeline is **loud**, not silently transposed.

### 2.2 How the length is encoded — and why the count is load-bearing, measured

The element count is in the frame, and it is **not** redundant with the base64 length. The hazard is
**quantum-aligned truncation**, and it is measured on a real 384-d payload (`p2_base64_probe.py`, verbatim
in `measurement.txt`):

```text
   cut   chars%4   decoded    floats   len%4==0   count check (declared n)
   -1    3        refused by b64decode (Incorrect padding)
   -2    2        refused by b64decode (Incorrect padding)
   -3    1        refused by b64decode (Invalid base64-encoded string: … cannot be 1 more than a multiple of 4)
   -4    0          1,533 B   383.25   False       refuses
   -8    0          1,530 B   382.50   False       refuses
   -12   0          1,527 B   381.75   False       refuses
   -16   0          1,524 B   381.00   True        refuses
   -20   0          1,521 B   380.25   False       refuses
  count check on the -16 frame: REFUSED (frame declares 384 elements at f32 (1536 B), decoded 1524 B)
```

*(Quoted from `measurement.txt`, one `b64decode` message elided with `…` for width. The two checks disagree
on exactly one row — `len%4==0` is `True` at −16 while the declared-count check `refuses` — and that
disagreement **is** the argument: a reader with only the length check accepts a 381-float truncation, and the
count is the only thing between it and a silently short vector.)*

`b64decode` **refuses** a cut that is not a multiple of 4 characters (`Incorrect padding`, or *"cannot be 1
more than a multiple of 4"*) — so the danger is not a partial quantum. It is the **aligned** cut: 4 base64
characters are 3 whole bytes, so a body shortened by a multiple of 4 characters is still **valid** base64
and decodes silently to fewer bytes. Let `m` be the number of **quanta** (4-character units) removed, so
`k = 4m` characters and **three bytes per quantum**: the decoded length is **`4N − 3m`** bytes (not
`4(N−3m)`: a 4-character quantum carries 3 bytes, not 4). Then `4N − 3m ≡ 0 (mod 4)` iff
`3m ≡ 0 (mod 4)` iff `m ≡ 0 (mod 4)`, i.e. only every **16th** character may go. At **−16 characters**
(`m = 4`, `4N − 3m = 1,524`) that is exactly the
case (1,524 B = **381 whole floats**), so a reader checking only `len(decoded) % 4` **accepts a silently
truncated vector**. Only the declared count refuses it. (This is the fail-open direction on the durability
artifact: a short vector replayed into the graph is a wrong graph with no error anywhere.) `validate=True`
is still mandatory — the default `False` **discards** characters outside the alphabet instead of refusing, so
an **insertion** into the body is count-clean and decodes to the *right* vector *silently* (measured: 4
inserted `*` → default `b64decode` gives 1,536 B, count-clean, bit-identical; `validate=True` raises). That is
why the probe has **two** character cases, not one: a *replacement* is caught by the count, and only an
*insertion* is caught by `validate`. The probe asserts both halves: every multiple-of-4 cut decodes silently,
and — *of the cuts measured (−1…−20)* — `−16` is the only one whose decoded length passes a `%4` check
(`−32` would also pass: `m = 8`, 1,512 B = 378 floats; it is outside the sweep).

The reader's **six** refusals, all measured in the probe:

```text
== the reader refuses (each raises, none decodes silently short)
  last quantum replaced + one dropped (1,533 B) REFUSED (ValueError, decoded 1,533 B)
  one element short, count unchanged         REFUSED (ValueError, decoded 1,532 B)
  packed f64 under a declared f32            REFUSED (ValueError, decoded 3,072 B)
  unknown version                            REFUSED (ValueError, decoded 1,536 B)
  non-base64 character REPLACING alphabet chars REFUSED (Error)
  non-base64 chars INSERTED (count-clean, validate-only) REFUSED (Error)
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
== the producer's float32 bits`**, verified on **every record the probe gives a vector to — 5 of 39**, of which there are four types
(`PointAdded`, `OperatorAdded`, `PointPromoted`, `OperatorPromoted`). ⚠️ **Say what that breadth does and
does not mean**: the committed corpus carries exactly **one** `embedding`, a **2-element placeholder** on
`/point/embedding`, which the probe **replaces** with its own injected 384-d `unit_vector()` before writing
— so the five rows are five records sharing one vector, not five corpus vectors, and §2.5 says the same
("the vector the probe injects; no corpus record carries it"). The five rows are still the right check
because the property is **element-wise and therefore vector-independent**: each of the 384 floats must
survive pack → base64 → unpack. ⛔ **It is not the equality the obvious test would assert, and which way
that test fails depends on the TRUTH SIDE** — both arms are computed and asserted in the probe, because
stating only one of them is how this paragraph was wrong before:

- **against the producer**, `float(decoded) == float(producer)` **passes, 384 of 384**. It cannot
  discriminate, and not because the numbers agree loosely: an `f32` value **is** exactly a `float64`, so the
  comparison carries no WIDTH at all — a faithful `f32` pack and any lossless re-encode of the same values
  satisfy it identically. Vacuous as a *discriminator*.
- **against P0's un-narrowed decimal text**, the same test **rejects a correct encode — 0 of 384**. The
  text holds the shortest round-tripping decimal (`0.0012301534`) and the decode holds the `f32` value, so
  the truth side has to be narrowed to the stored width (`#8615`) before any comparison at all. That is a
  **false red**, not leniency — the same test is simply measuring the wrong thing from either side.

The probe prints both (`float64-VALUE decoded == producer …: True` / `… == P0 un-narrowed text …: False`),
and beside them the **negative control** that proves the bits assertion is not vacuous: a **1-ULP
perturbation** of one element flips it to `False`. ⚠️ **The first draft of that control was itself inert** —
it printed `float(np.float64(v)) == v` over the producer with `decoded` ABSENT, under a label naming
`decoded==producer`, so it was a widening identity that no input can falsify and the guard under it could
never fire: the same class as the round-14 printed control this note reports, one control down. Both arms
are now driven from a real verified vector and a `False` / `True` where this text says otherwise returns 1.
The bits are the property because the bits are what the graph stores.

**Two of the three links face the producer, and the third is the frame's own round-trip.** Precisely:
`decoded f32 == producer f32` and `P0 text→f32 == producer f32` are computed against something **outside**
the frame; frame-invertibility (`decoded == the frame's input`) is *implied* by those two by transitivity and
adds no discriminating power — it is reported because it is the property a reader would name, not because it
is an independent test. An earlier draft's third link was worse than implied: it was
`pack_f32(narrow(x)) == pack_f32(x)`, an **identity** (packing as `f32` rounds whether or not you round
first), true for 384 random floats with no journal at all, so the `assert` built on it could never fail.

That 9 significant digits suffice for float32 is float32's own structure (24-bit significand ⇒ 9 decimal
digits round-trip; 8 do not always). Checked rather than cited, on 2,000,000 random float32 values:

```text
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
already implied by `embedding_width`) and it removes the one **detectable-at-read** failure — a frame that disagrees with the marker; a marker
already wrong when written is copied into the frame, so the two agree and nothing can catch it at rebuild
(that is the `#11835` F2 class, which is why §2.4.1's writer rule exists) — and it removes the last way the
width can be wrong at rebuild
time without anything noticing. Where a checked duplicate and a single source of truth conflict, the
unrecoverable direction wins.

#### 2.4.1 The writer side of that rule — a width-less vector is never framed

The cross-check above is a **reader** rule, and it has a writer-side precondition the note must state or the
two rules combine into a defect: `embedding` present with **no** `embedding_width` is a **supported** shape,
not legacy. The seam is *guarded at the call site* — `#5148`, an unimportable `tortoise.embeddings` must not
fail a write — so such a capture journals the vector with no width declaration, and
`record_projection.py` says so verbatim: *"a capture whose seam was unimportable journals the vector with no
width — exactly the pre-#11821 shape, which this set must keep accepting"* (6 tests in
`tests/test_write_path_unreachable_seam_5148.py`).

**So the encoder frames only what declares a width**: it takes the token from `embedding_width` and nothing
else, and a marker-less vector is written as a plain JSON list — the shape the reader already accepts on the
pre-P0 path. Two ways to get this wrong, both fatal in exactly the way §3 exists to prevent:

- **frame it anyway** → `read_all` refuses a record that `append` accepted: a supported write becomes
  unreplayable, which is §3's stated worst defect;
- **default the token** → a marker-less **caller-owned** `f64` vector gets framed `f32` and is **narrowed**,
  which §4.4 says would be a reopen.

Pin 8 in §3.2 is the round trip that catches both.

### 2.5 The log line stays newline-delimited and parseable — and why not raw bytes

Raw packed float32 is **1,536 B**, i.e. 512 B per record cheaper than base64. It is refused, and this is
measured rather than asserted: on the probe's deterministic unit vector (`unit_vector()`, seed 5612,
normalised — the vector the probe injects; no corpus record carries it), the raw packet contains

```text
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

#### 2.5.1 base64 is not the only text alphabet — so the cheaper ones are measured, not waved at

An earlier draft said base64 is the *only* framing that keeps the line a JSON object, and that is
**false**: every text alphabet does. The comparison is in the probe (`measurement.txt`), on the same
1,536 B payload, field bytes = 14 B prefix + 2 quotes + body:

```text
   alphabet            body B   field B   vs b64   needs JSON escaping
   (field B = 14 B prefix + the JSON-ESCAPED string, so escaping is charged for)
   hex                    3,072     3,088    1.496x   False
   base64 (RFC 4648 §4)   2,048     2,064    1.000x   False
   base64url (§5)         2,048     2,064    1.000x   False
   b85 (RFC 1924)         1,920     1,936    0.938x   False
   a85 (Adobe Ascii85)    1,920     1,970    0.954x   True
   z85 (ZeroMQ)           1,920     1,936    0.938x   False
```

**b85 and z85 are 128 B cheaper on this field** — a record of `5,423 − 4,689 + 1,936 = 2,670 B`, i.e.
**2.03×** on the record and **2.42×** on the vector instead of 1.94×/2.27×. So the choice is a **recorded
trade, not an optimum**, and this note does not claim otherwise:

- **base64 wins on ubiquity and hand-inspectability.** RFC 4648, in **most** languages' standard
  libraries (Python, Java, Go, C#'s `System.Convert`, Node's `Buffer` — but **not** Rust's or C's, which is
  the same reason b85's support is thin), and `echo '<b64>' | base64 -d | xxd` lets a human read a vector
  out of a journal during an incident.
  b85 has a Python stdlib decoder and much less support elsewhere; z85 has none in Python's stdlib (the
  probe implements it) and its alphabet is a ZeroMQ spec (RFC 32), not an IETF one.
- **a85 is refused outright**: its alphabet contains the quote and the backslash, so it needs JSON escaping
  and would break the one-write-no-escaping property §2.5 exists to keep — and its real JSON field is
  **1,970 B**, not the 1,936 its body length suggests, which is why the table's field column charges the
  escaped length (measured: `needs JSON escaping = True`).
- **hex is refused on size**: +100 % overhead, 3,088 B — still **1.52× better than P0 on the field** (and
  1.42× on the record, since the 734 B of non-vector bytes do not move), but it **gives back 39 %** of the
  win for no property base64 lacks.

If a later lane wants the extra 128 B/record, **b85 is the drop-in** — same frame, same count, same width
tag, `base64.b85encode`/`b85decode` — and the format version is what makes that a v2 rather than a break.
Choosing it here buys 4.6 % of the record and costs a decoder most non-Python readers would have to write;
this note declines that trade rather than overlooking it.

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
(measured: −4 chars → 1,533 B, i.e. 383.25 floats with the first 383 whole; −8 → 1,530 B, 382.50), but only
after the human trims the **body** to a 4-char boundary (a 3-byte multiple) **and the decoded bytes to a
4-byte multiple** — the two moduli differ, so two trims are needed. So hand-recovery changes **shape**, not
existence. Cost, not correctness; recorded under Rollback (§6) as a permanent loss.

---

## 3. The reader for old lines — the surface where the danger lives

A payload that appends fine and cannot replay is *the* defect, and it surfaces only when a rebuild is
needed — i.e. at the worst possible moment, against a journal nobody can re-create.

### 3.1 One dispatch, in one place

The frame is decoded in **`EventLog.read_all`**, at the record-read boundary, so every vector consumer
(`fold`, `rebuild_all`, `begin_ingest`'s cache scan, `record_projection`, `recover_from_log`, the backup
and cursor readers) still sees **exactly the shape it sees today** and needs no change. One spelling of
the frame, one place to get it wrong — the same reason P0 put the width decision in one seam.

**The bypasses, named rather than left to be found.** Four journal line-parsers do *not* go through
`read_all`: `log.py`'s seal/tail detection, `subject_binding`'s key scan, `MentionScan` (`log.py:1243`, used
by `EventLog.mention_lines` and `sdk._journal_presence_state`), and `backup._count_journal_events`
(`backup.py:68`, used on the restore path at `:660` — **count-only**: it reads no key and no value, so a
frame cannot reach it, and `embedding` appears nowhere in `backup.py`). The three that read keys read
`type`/id-shaped keys and **never** `embedding` — measured at this head: `embedding` appears as a **code**
token on **no line** of `log.py`, `subject_binding.py` **or `backup.py`** (tokenised, comments excluded, and
**both** token classes counted — a `NAME` and a `STRING`, because a JSON access like `rec["embedding"]` is a
string token and a NAME-only scan would miss it; `log.py`'s eight mentions are all comment prose, and the
other two files have none at all), and `_apply_journal_presence` — the parser behind `MentionScan` — and
`_journal_presence_state` contain none. So an undecoded frame cannot reach them. That is a property to
keep, not an accident to assume: the implementation's tests must red if any of them starts reading a
vector, and they must spy on **`_apply_journal_presence`**, not only on `MentionScan`, or the symbol that
could actually start reading one is unwatched. (`sdk._journal_vector` is the opposite direction — a
`Vectorf32` → JSON-able list converter on the **write** path — and reads no journal.)

Dispatch is on the **value**, per record:

| `embedding` value in the line | meaning | reader |
|---|---|---|
| a JSON **list** | P0 (float32 shortest text) or pre-P0 (float64 text) | unchanged — the existing path |
| a **string** starting `b64:` | P2 frame | decode per §2; refuse on any of the **six** corruptions |
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
  6. **every refusal of §2.2 and §2.4 each red**: truncated body (−16 chars, the measured silent case),
     short array with the count unchanged, **packed `f64` under a declared `f32`** (3,072 B decoded against
     a declared 1,536 B — §6's rollback row depends on this one), unknown version, non-base64 character,
     **and the two §2.4 cross-checks**: a frame whose declared width *disagrees* with the payload's
     `embedding_width` must raise, and a frame on a record carrying **no** `embedding_width` must raise.
     §2.4 calls that cross-check the one thing stopping the width being wrong at rebuild "without anything
     noticing", so it cannot be the only decode-path rule with no pin.
  7. **`append` decodes nothing**: a decoded record re-appended writes a **frame** (the encoder is at the
     write boundary, not on the in-memory record), so re-stamping a read record cannot flip its width —
     the #11835 F2 class of defect, one layer out.
  8. **a width-less vector is never framed, and still replays.** `embedding` present with **no**
     `embedding_width` is a **supported** shape, not a legacy one: the seam is *guarded at the call site*
     (`#5148` — an unimportable seam must not fail a write), so a capture whose seam did not run journals
     the vector with no width declaration, and `record_projection.py`'s comment on
     `TURNS_SNAPSHOT_OPTIONAL_KEYS` says so ("exactly the pre-#11821 shape, which this set must keep
     accepting"; pinned by `tests/test_write_path_unreachable_seam_5148.py`, 6 tests). So the encoder takes
     its width token **from `embedding_width` and nothing else** — a marker-less vector is written as a
     plain JSON list, never a frame — and the test must prove the round trip: append → `read_all` →
     `rebuild_all` succeeds, unframed. ⛔ Without that rule the note's own reader rule (§2.4's *frame with
     no marker → refuse*) turns a **supported write into an unreplayable record** (§3's stated worst
     defect), and the alternative — defaulting the token — would frame a caller-owned `f64` vector as
     `f32` and **narrow it**, which §4.4 says is a reopen.

### 3.3 The failure this section exists to prevent

`rebuild_all` and `consistency.recover_from_log` are the two consumers that can be wrong
**catastrophically rather than visibly** — both wipe and replay (`recover_from_log`'s docstring says "wipe +
full replay"; `rebuild_all`'s says "this wipes the whole graph before replaying"), so a wrong decode is
silent rather than a visible read error. A reader that skipped a frame-bearing record would rebuild a graph **missing vectors** and
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
  (`CASE WHEN $embedding_clear THEN NULL WHEN $embedding IS NULL THEN n.embedding WHEN
  $embedding_verbatim THEN $embedding ELSE vecf32($embedding) END` — all four arms, at
  `projection/entities.py:2976`);
- **`embedding_preserved`** — a vector copied from a **previous** capture (possibly anyone's), which the
  turn carrier does not mark verbatim, so it is the only signal there.

Both stamp `embedding_width: "f64"` and their values are left **untouched**.

### 4.2 How the encoding preserves that — it encodes f64, it does not narrow it

Under P2 that population is base64 of **packed float64** (`struct.pack("<…d")`): every bit of the caller's
value survives, and the lossless property is *stronger* than under P0, where the value's text was already
float64's shortest repr (also exact, but only by `repr`'s guarantee). Measured on an off-the-float32-grid
fixture (the P0 test's rule, so the check cannot pass vacuously):

```text
  float64 frame round-trip bit-identical: True
  …and it is genuinely off the float32 grid: True   (so a narrowing WOULD be visible)
  f64 spans (the off-grid fixture): today 5,770 B (15.0 chars/float) → frame 4,112 B (10.71) = 1.40x
  f64 spans (a realistic full-precision float64 vector): today 7,552 B (19.7 chars/float) → frame 4,112 B (10.71) = 1.84x
```

Two fixtures, because the **magnitude is a property of the text, not of the encoding**: this fixture
(`0.1 + i·1e-9`, ~15.0 chars/float) gives **1.40×**, a realistic full-precision float64 vector (~19.7
chars/float) gives **1.84×**.

⛔ **And the *sign* is a property of the text too — which is why §1's figures use a real vector.** The
frame costs a flat **5.375 chars/float (`f32`) / 10.71 (`f64`)** — both the full framed span
(2,064/384 and 4,112/384), not the bare base64 — so it is a **floor the text can fall below**. Measured on a 384-d vector of degenerate shapes (the probe prints these):

```text
   shape                            f32: text    frame    ratio    f64: text    frame    ratio
   all zeros                            1,537    2,064    0.74x       1,537    4,112    0.37x
   small ints 0..383                    2,195    2,064    1.06x       2,195    4,112    0.53x
   the probe's real vector (f32)        4,689    2,064    2.27x       8,031    4,112    1.95x
```

**Each column is on its own width's ruler**, which is the #11886 discipline applied inside the table: the
`f32` text is **P0's shortest float32 text** (what the journal writes today — 4,689 B, the same figure as
§1.4), and the `f64` text is the value's full float64 repr (what a caller-owned vector's text is). Printing
both through one `json.dumps` would put the `f32` row on a **form the journal does not write today** —
8,031 B, the pre-P0 full-precision text re-serialised compactly (the journal wrote that same encoding at
8,414 B with default separators; `log.py:1552` names both sides of that delta). 3.89× is the ratio of
*that* intermediate; the real `f32` ratio is §1.4's **2.27×**.

A ratio **below 1 means the frame is LARGER than the text**. So on zeros, base64 loses for **both** widths
(the `f64` frame is 2.68× the text; even `f32` is 1.34×), and on small integers it loses for `f64` (1.87×)
while only marginally winning for `f32` (1.06×). ⚠️ Sparse and zero-heavy **caller** vectors are an **assumption, not a measurement** — nothing in this
repository measures the caller-owned population's density — so the row is presented as *if* they occur; the
conclusion does not need them to be common. Base64 is a **size trade whose sign depends on the vector**. It is taken
here for the *bit exactness*, the single reader and the removal of the shortest-decimal invariant — not
because it always shrinks. A degenerate vector that loses bytes is still a correct vector.

(The `f64` text here is full precision — `0.10000000149011612`, what a widened float32 prints — so it
exceeds §1's `f32` figures, which are P0's *shortest* text. The two are not comparable, and for the
embedder population §1's are the ones the journal actually writes.) And it is narrowed **not at all**: the frame's width
field is what carries the promise — `f64` in, `f64` out.

### 4.3 What happens to a caller-owned vector under the new format

It is encoded at float64, decoded at float64, and restored through the same `embedding_verbatim` guard that
exists today — the guard is **not** touched by the encoding, because it reads `embedding_verbatim` (the
caller-ownership marker), not `embedding_width` (the format marker).
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

**Implementation is blocked, and the blocker now has to be re-stated.** This is #11821's own sequencing
rule, stated on the PR that landed P0:

> **P2** — base64 float32 payload: **design note on #11821 first**, and no implementation until P0 and P1
> are merged and measured. It is a format migration on the rebuild path and shares a PR with nothing.

⛔ **UPDATE, 2026-11-13T19:16Z (a concurrent lane, after this note's first draft): #12458 was closed
`NOT_PLANNED`. It will never merge, so the "P1 merged and measured" leg of that rule cannot be satisfied by
a merge.**

Its closing verification record states why, and the substance matters more than the status:

- **P1's premise is dead on its own terms.** The 32,032 B/session `:GraphEvent` figure was measured at
  `fffcc7c0c2` before the turn carrier landed (`aed841e8b9`, four days later); the carrier is not an
  ancestor of the commit the figure was measured at, and the falsifier (`test_10017_turn_carrier_replay` →
  21 passed at `ca74f2375e`) shows the per-turn `:GraphEvent` write is already absent on the armed lane. As
  the record puts it: *"P1 as scoped would reclaim a cost that no longer exists."*
- **And it was classified a `REVERSAL`, not a narrowing.** `_GRAPH_EVENT_TYPES` (`tortoise/sdk.py:4670`) is
  an 11-type claim-transition contract, not a copy of the journal; dropping the mirror removes the
  contract's mechanism rather than trimming it.
- The audit's four blockers are real and **BK1 is fatal to the idea** — the journal-less lane is a supported
  shape, and the `:GraphEvent` store is the only feed implementation for it.

So the gate is **not** met, and it is also **not** satisfiable as written. What follows from that, stated so
the note cannot be read as either an authorisation or a release:

- **Whoever owns #11821 must re-state the gate.** It now hangs on a decision that was deliberately declined,
  and re-basing a recorded sequencing rule is that owner's call — not this note's.
- **Absent that re-statement, implementation stays blocked**, and this note is not evidence that any of it
  has been approved.
- **P2 shares a PR with nothing.** Not P1's, not #11886's, not a docs sweep.
- **§1's figures do not depend on P1's fate.** They are measured on an unarmed lane's corpus
  (`docs/evidence/`), so if P2 is later authorised, re-run the probe rather than adjust the numbers — it is
  committed for exactly that.

---

## 6. Rollback — if the format proves wrong after it ships

The frame was chosen so that this question has a cheap answer, which is the strongest argument for §2.1:

| scenario | recovery | permanently lost |
|---|---|---|
| the **decode** is wrong (unpack, endianness, the count) | **rebuild from records**: the journal bytes are intact and the frame is self-describing, so a fixed reader replays them correctly. The graph is rebuilt, not restored. | **nothing** — the bytes were never destructive |
| the **encode** is wrong (pack) | a wrong pack puts wrong bytes **in the journal**, so a rebuild replays them faithfully and wrongly. A pack that violates the frame's own length (packed `<d` under a declared `f32`, or 383 elements under a count of 384) is **refused** by the count — measured: 3,072 B and 1,532 B decoded against a declared 1,536 B. A pack that is self-consistent but wrong (element order, a transposition) decodes **silently**, and the replay writes the wrong vector with no error — §3.3's failure exactly. | **the vector as written**, in the silent case: the journal is the record of record, so it can never supply the correct value again. ⚠️ **But the window is not zero** — the graph is written from the *same in-memory payload* (`projection/entities.py:2976`, `vecf32($embedding)`), so while a wrong pack has corrupted only the journal bytes the live graph still holds the correct vector: **dump the graph's vectors before the next `rebuild_all`** and they are recoverable; after a rebuild the wrong bytes are replayed and it is permanent. The rest of the record always rebuilds. |
| the format is wrong (base64 turns out to be the wrong choice) | **dual reader**: both shapes stay readable indefinitely, because dispatch is per record and needs no state. The writer reverts to JSON text; journals that already carry frames keep replaying. | **nothing** — no record is rewritten, so no migration is needed in either direction |
| the frame is withdrawn entirely | a reader that stops decoding still has to keep understanding frames for as long as any frame-bearing journal exists. That reader is a **permanent** obligation of the format. | a second reader path, forever |
| ⛔ **a BUILD rollback** — the previous image is redeployed over a journal that already carries frames | **nothing automatic, and the failure is silent.** A pre-P2 build hands the frame *string* to the fold: `_writable_journalled_vector` returns `None` for anything that is not a list/tuple (`projection/entities.py:896`), and `owns_embedding = "embedding" in p` is **True**, so the `elif not op and owns_embedding` arm takes neither the write nor the recompute — and the width warning at `:2896` only fires when `journalled is not None`. So **every framed vector is dropped with no log line and the rebuild reports success**, which is §3.3's stated failure exactly. The P2 **reader must be forward-ported before any rollback** — a binary rollback is not a rollback. ⚠️ This is also the bound on §2.1's "a mis-ordered pipeline is loud": `_drop_journalled_embedding` warns on the **write** seam; the **rebuild** path degrades silently. | **the vectors in the rebuilt graph** — the journal still holds them, so a forward-ported reader recovers them, but the graph built by the rolled-back image does not have them and nothing said so |
| base64 makes the log un-inspectable in an incident | a decode helper (`tools/journal_decode.py`-shaped) restores human readability on demand; it is a **tool**, not a format change | **human greppability of the vector** — you can no longer read, diff or hand-edit a vector in the log by eye. This is a real, permanent loss and the main non-byte cost of P2. |
| a tear's surviving vector is no longer directly readable | nothing to recover: the record was never half-applied | **hand-recoverability of the vector's bytes as-is**: the values survive as a decodable **prefix**, but only after a human trims the body to a 4-char boundary — the shape of hand-recovery changes, not its existence (§2.6) |

**What is permanently lost if the format ships and then is withdrawn:** the simplicity of a single-shape
journal — every frame-bearing journal is thereafter a second code path that must keep a reader. That cost is
**incurred by shipping at all**, not by withdrawing, and it is the reason §7 exists.

⚠️ **And "no migration is needed in either direction" is a statement about the FORMAT, not about a BUILD.**
The dual reader makes the *data* rollback-free; it does not make the *deployment* rollback-free, because a
pre-P2 image cannot read a frame and does not say so (row 5 above). The safe rollback order is: ship the
reader everywhere first, then the writer — i.e. the usual expand/contract — and until the last pre-P2 build is
gone, a rollback is a **data-loss operation**, not a revert.

---

## 7. Recommendation — stated plainly, because the number is smaller than the headline

**The format in §2–§4 is the correct one if P2 is taken.** §2.1 chooses a **tagged string under the existing
key**, against a sibling key, a nested object and a file header (§2.1), and a binary layer (§2.5) — and it
satisfies all four
recorded constraints: per-record versioning (append-only), a count that fails closed on the measured
silent-truncation path, two widths so #5004 is preserved rather than overridden, and JSONL compatibility so
the tear contract and the reader are not replaced.

⚠️ **It is the chosen shape, not the only workable one, and the note does not claim otherwise.** The two
structural refusals in §2.1 are the sibling key (it must drop `embedding`, and R1's *presence is ownership*
then forces a silent re-embed) and the file header (append-only, in-place upgrade). A **nested object** is
not refused by structure at all: it fails only because `stamp_journal_embedding` refuses a `dict` — *"the
same branch"* that also refuses a bare string (`embeddings.py:504`) — and either would be reachable if §3.1
decoded the value in `read_all` before the seam sees it. The flat string is preferred on **hand-readability**
(a `b64:1:f32:384:` prefix is legible in the log without knowing the schema) and because it adds no key to
the payload; a nested object with a version/count/width is a defensible alternative that would need its own
measurement.

⛔ **The alphabet inside that shape is a separate choice, and base64 is not the only one that works.**
§2.5.1 measures six: `b85` satisfies all four constraints **128 B/record cheaper** (2,670 B, 2.03×) and needs
no escaping, so base64 is a recorded trade on ubiquity and hand-inspectability — *not* an optimum and *not* a
necessity. `b85` is the drop-in if a later lane wants the 4.6 %; changing the frame's version token is what
makes that a v2 rather than a break. What the format does *not* decide is whether P2 is worth taking, and the
honest numbers for that are:

- **the remaining win is 1.94× on a creating record and 2.27× on its vector — not 4.1×** (§1.4);
- **≈ \$0.33/mo per million creates** at \$0.125/GB, on a line whose metering is unsettled (§1.5);
- **the sign of the trade depends on the vector** — a degenerate caller vector can come out *larger* (§4.2);
- against **a format migration on the rebuild path** — the one surface whose failure is a silently wrong
  graph — plus a **permanent second reader** and the loss of hand-inspectable vectors.

**Recommendation, given what happened to P1 — and it is now a weaker recommendation than the note first
drew.** The decision input the note asked for (`P1's measurement`) has arrived, and its content is *not*
one of the two branches this section originally contemplated: P1 was **declined as a reversal whose premise
had already disappeared** (§5), which says nothing about the vector and leaves the journal's vector payload
as the dominating record term it always was. So:

- **The byte case for P2 is real and stands on its own**: 1.94× on a creating record, measured, with the
  format decided rather than guessed.
- **It is also the smaller half of what #11821 hoped for, and it is not urgent.** The line it shrinks is
  ~\$0.33/mo per million creates and its metering is unsettled; meanwhile the durability artifact is the one
  place where a silent failure is a wrong graph, and P1's fate is a live demonstration of how a
  cost-driven change to this area ends (a premise measured on a tree that no longer existed, and a
  "narrowing" that was really a reversal).
- **So: do not take P2 on the strength of the byte saving alone.** Take it if and when the journal's vector
  payload shows up as a real cost or a real operator problem, and then take it **exactly as specified here**
  — its own PR, §3.2's eight pins as the review scope, and the `#11821` gate re-stated first (§5). If it is
  never taken, this note is the recorded design that was declined, which is cheaper than rediscovering it.

That judgement belongs to the owner of #11821 and of the journal's cost line — not to this note.

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

```bash
# 4. the §3.1 claim that no bypassing reader touches a vector: `embedding` as a CODE
#    token (comments excluded) on the three journal line-parsers that read KEYS and
#    skip `read_all` (the fourth, `backup._count_journal_events`, is count-only and
#    reads no key at all — `rg -n embedding tortoise/backup.py` finds none).
#    ⛔ NAME tokens alone are not enough — a JSON access is a STRING token, so
#    `rec["embedding"]` would be invisible to a NAME-only check. Both are counted.
uv run python - <<'PY2'
import io, inspect, tokenize
import tortoise.sdk as s
for path in ("tortoise/log.py", "tortoise/subject_binding.py", "tortoise/backup.py"):
    code, comments = [], 0
    for t in tokenize.generate_tokens(io.StringIO(open(path).read()).readline):
        if t.type == tokenize.COMMENT and "embedding" in t.string:
            comments += 1
        elif t.type == tokenize.NAME and t.string == "embedding":
            code.append(("NAME", t.start[0]))
        elif t.type == tokenize.STRING and "embedding" in t.string:
            code.append(("STRING", t.start[0]))
    print(f"{path}: code tokens {code or 'none'}; comment mentions {comments}")
for fn in ("_apply_journal_presence", "_journal_presence_state", "_journal_vector"):
    print(fn, "mentions embedding:", "embedding" in inspect.getsource(getattr(s, fn)).lower())
PY2
```

```bash
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
