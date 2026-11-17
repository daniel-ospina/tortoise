---
title: "#13083 — bytes per stored SHAPE on a fixed corpus, and what the §9.1 prose would save"
type: evidence
domain: platform
doc_status: draft
created: 2026-11-17
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#13083, #13082, #10017, #10070, #10429, STORAGE-ARCHITECTURE.md §9.1"
ownedBy: epistemic-team
---

# `#13083` — what each stored shape costs the graph

**Measured:** 2026-11-17, at head `6850b74c14` (the head both JSONs record as `tree.git_head`; the audit's base is
`8762578f8c` = `origin/main` at dispatch, where the probe does not yet exist — the JSON's `tree.git_head` is
the authority for what ran, and it names this branch), private lane
`docker://:@127.0.0.1:16397/tortoise_test_matrix` (`tools/test_lane.py`). Instrument:
`probe_shape_bytes.py` in this directory. Raw output: `measurement-before.json`,
`measurement-after.json`.

**Provenance, stated precisely.** Each JSON records `tree.git_head` = the branch head **at run time**. ⚠️ That
is **not** `8762578f8c` (`origin/main` at dispatch — the probe does not exist there), and it is not the final tip
either: **both arms record `6850b74c14`**, because the instrument was corrected under review and both were
re-run at that head.
**The tree to reproduce these figures from is the commit that contains the probe AND both JSONs** (they land
together), not the sha inside the JSON. The audit's base is `8762578f8c`.

**Why this file exists.** The capability audit
(`docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md`) states which READERS touch each shape. Its byte
columns come from here, and this is the home for the method, the arms and — most importantly — the
**limits of the instrument**, so a reader cannot take a payload figure for a residency figure.

## The corpus

Fixed and synthetic: 6 sessions × 10 turns × 1,500 characters with a deterministic 384-dim `float32`
vector per turn (the production embedding width), **plus** one public capture through
`sdk._capture_session` (mock extractor, hermetic) and one of each of `Source`/`Event`/`Object`/`Subject`
created through their SDK doors. Same ids, same texts, same vectors in both arms — reproducible on any
machine, and never a snapshot of tenant data.

Record **unarmed** in both arms, so a capture's turns land as NODES. That is deliberate: the fallback
lane is the population a `record_only` move would remove from.

## Instrument

- **Residency**: the server's own `INFO memory → used_memory`, taken immediately before and after the
  corpus — the same instrument §9.1's own measurement uses (`docs/evidence/10000-turns-bytes/README.md`).
  `GRAPH.MEMORY USAGE` is not used as the primary: it reports whole MB and is blind to the HNSW
  `vectorset` module (`#5331`).
- **Per-shape payload**: for each label, `keys(n)` from the graph (what a WRITER put there, not what a
  declaration says should be), then each value's **rendered** length summed in Python (`repr_bytes_by_label`
  in the JSON names it honestly). `size()` cannot substitute — it refuses a scalar and returns an element
  COUNT for a list. ⚠️ **For a numeric ARRAY this over-reports by ~4.7×**: a 384-dim float vector renders as
  ~7,187 characters of digits and commas, while its stored `float32` payload is `384 × 4 = 1,536` bytes.
  String properties are exact; array properties' stored footprint must be **derived from the width**, never
  read off this column. A review caught the first cut publishing the rendered figure *as* bytes.

## ⚠️ Limits — quoted, not discovered later

1. **`repr_bytes_by_label` is a RENDERED size, not a stored one.** For a string that is exact; for a numeric
   array it **over-reports** (~4.7x for a 384-dim float vector: ~7,187 rendered characters vs a 1,536 B
   stored `float32` payload), so an array's real footprint must be derived from its width. It is also not a
   *residency* figure: the per-property attribute-block entry is excluded, and this instrument does not
   quantify that gap. An earlier draft did — it quoted ~2,520 B/point from a `used_memory` difference that
   later proved to be baseline noise (limit 3). The claim was withdrawn, not repaired.
2. **`used_memory` includes block and index matrices**, so a small corpus over-states the marginal
   per-node cost (FalkorDB allocates in blocks). The corpus size is printed WITH the figure, so every
   number here is a per-shape marginal **at this corpus**, never a per-node constant.
3. **`used_memory` is not a calibrated A/B on this box.** Across the committed pair the baseline differs
   by **847,064 B** in the committed pair (4,166,640 vs 5,013,704) and by **49,504 B** in the pair before it
   — same code, same corpus. The drift is run-dependent and has been both larger and far smaller than the
   90,000 B payload delta, so `used_memory` is **not used for any figure** in this record or the audit. Only
   the deterministic rendered-size numbers are quoted.
4. **Seven shape-groups are declared UNEXERCISED rather than counted as zero**: `:Meta`, `:WhySnapshot`,
   `:Tag`, `:ContextSubscription`, `:CommitRecord`, `:DeliveryReceipt`, and the
   `:Batch`/`:PipelineStage`/`:PackInstall`/`:PackSelection` group. The corpus does not create them, so this
   document has no measurement of them — which is a gap in the evidence, not a claim that they are free.
   (`:Tag` is `graph`-verdicted in the audit; it is unexercised **here**, which is a coverage gap, not a
   `record_only` verdict.)

## The two arms

`--legacy-prose` seeds each turn's `:Point` with §9.1's narrative in its row's **`props` tail** — the
shape a pre-move record→cache projection produced. **Both arms run it**, and the arms vary **only** the
declaration: `before` pops `CACHE_FORBIDDEN_COLUMNS["point"]`, `after` sets it. (⚠️ The first cut of this
instrument ran the `after` arm without `--legacy-prose`, so the arms differed by whether the prose was
SEEDED rather than by the declaration — a review caught it and the arm was rebuilt. The asserted
invariant at the top of the arm is what stops that regressing.)

| arm | `:Point` payload | `story_arc` bytes | `content` | `embedding` | `:Point` nodes |
|---|---|---|---|---|---|
| `before` (entry absent) | 668,014 | **90,000** | 93,287 | 474,348 | 66 |
| `after` (entry present) | 578,014 | **0** | 93,287 | 474,348 | 66 |
| **Δ** | **−90,000** | **−90,000** | 0 | 0 | 0 |

60 seeded legacy turn points ⇒ **exactly 1,500 B/turn**. `content` and `embedding` are byte-identical
between the arms, which is the check that this is one variable and not a corpus difference.

⚠️ **`used_memory` is NOT quoted for this pair.** The two runs' baselines differ by **847,064 B** — larger than
the payload delta — and the previous pair's differed by **49,504 B**. The drift is run-dependent, so any
residency marginal here would be the instrument's noise dressed as a measurement. The payload figure is exact
and deterministic; the residency figure is **unmeasured**.

**This arm measures the SIZE OF THE PRIZE, not a shipped change.** The move was withdrawn: two policy pins
in `tests/test_cache_projection.py` (`:185-190`, `:243-246`) refuse the declaration until it is widened
with a tail-only category — a decision belonging to `#10429`, which owns this route. The MECHANISM does
honour the entry (`_props_from_row`'s `tail_denied` is the plain forbidden set plus its spellings,
`cache_projection.py:2710`). See the audit's §5.1.

## Per-shape census (single run, `before` arm)

| shape | payload bytes | note |
|---|---|---|
| `:Point` | 668,014 | 66 nodes · `content` **93,287 (exact)** · `embedding` 474,348 **rendered** = **101,376 stored** (66 × 384 × 4 B) · `story_arc` 90,000 (this arm) · the rest < 11,000 |
| `:GraphEvent` | 12,768 | 67 nodes × ~190 B rendered; the §9.1 figure of 801 B/turn includes block overhead |
| `:Source` | 916 | 2 nodes |
| `:Event` | 680 | 3 nodes |
| `:Session` | 303 | 7 nodes; the **exercised** capture telemetry is **13 B over 7 sessions** (`capture_redactions` 7 + `capture_ok` 4 + `capture_extractor` 2) ≈ **2 B/session**, not the ~20 an earlier draft claimed |
| `:Object` | 198 | 2 nodes |
| `:Subject` | 139 | 2 nodes |
| `:GraphEventMeta` / `:EpMeta` | 4 | 2 nodes |
| **total (rendered)** | **683,022** | `:Point` is 97.8 % of rendered payload |

## Re-running it

```bash
uri="$(uv run python tools/test_lane.py uri)" || exit 1   # two steps on purpose
eval "$uri"
uv run python docs/evidence/13083-capability-census/probe_shape_bytes.py \
    --arm before --sessions 6 --turns 10 --legacy-prose > measurement-before.json
uv run python docs/evidence/13083-capability-census/probe_shape_bytes.py \
    --arm after  --sessions 6 --turns 10 --legacy-prose > measurement-after.json
# ⛔ BOTH arms carry --legacy-prose. `--arm after` WITHOUT it exits 2 by design:
# it would seed no prose at all, differing from `before` by the SEEDING rather
# than by the declaration — the confound a review caught in the first cut.
uv run python tools/test_lane.py down
```

⛔ Never the shared `localhost:6379` URI: nothing serves it on a cell, and a run that names it reports a
wall of connection refusals with no verdict (`#10399`).
