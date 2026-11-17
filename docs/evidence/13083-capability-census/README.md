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

**Measured:** 2026-11-17, on `8762578f8c` (`origin/main` at dispatch), private lane
`docker://:@127.0.0.1:16397/tortoise_test_matrix` (`tools/test_lane.py`). Instrument:
`probe_shape_bytes.py` in this directory. Raw output: `measurement-before.json`,
`measurement-after.json`.

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
  declaration says should be), then each value's byte length summed **in Python**. `size()` refuses a
  scalar and `toString()` refuses a list, so no single Cypher spelling covers every property type.

## ⚠️ Limits — quoted, not discovered later

1. **`sum(size(prop))` is a LOWER BOUND.** It excludes the per-property attribute-block entry. §5.1 of
   the audit measures that gap: ~2,520 B/point of `used_memory` against 1,500 B/point of payload, i.e.
   the block entry costs **~68 % more** than the payload it stores.
2. **`used_memory` includes block and index matrices**, so a small corpus over-states the marginal
   per-node cost (FalkorDB allocates in blocks). The corpus size is printed WITH the figure, so every
   number here is a per-shape marginal **at this corpus**, never a per-node constant.
3. **The arms are not a calibrated A/B.** The prose arm varies the projection's declaration, but the
   server's `used_memory` baseline differs between runs by ~95,296 B (5,235,632 vs 5,330,928). The
   `story_arc` payload figure (90,000 → 0) is exact and deterministic; the `used_memory` delta
   (151,216 B) is an order-of-magnitude witness, larger than the observed noise, not a measurement of it.
4. **Six shapes are declared UNEXERCISED rather than counted as zero**: `:Meta`, `:WhySnapshot`, `:Tag`,
   `:CommitRecord`, `:DeliveryReceipt`, and the `:Batch`/`:PipelineStage`/`:PackInstall`/`:PackSelection`
   group. The corpus does not create them, so this document has no measurement of them — which is a gap
   in the evidence, not a claim that they are free.

## The two arms

`--legacy-prose` seeds each turn's `:Point` with §9.1's narrative in its row's **`props` tail** — the
shape a pre-`#13083` record→cache projection produced — and the arm varies **only** whether the
projection's `node_from_row` keeps the key (removed in process for `before`, so both arms run the same
code and the same corpus).

| arm | `:Point` payload | `story_arc` bytes | `used_memory` delta | `:Point` nodes |
|---|---|---|---|---|
| `before` (prose kept) | 668,014 | **90,000** | 2,489,608 | 66 |
| `after` (prose dropped) | 578,584 | absent (= 0) | 2,338,392 | 66 |
| **Δ** | **−89,430** | **−90,000** | **−151,216** | 0 |

60 seeded legacy turn points ⇒ `story_arc` **1,500 B/turn** exactly, payload **1,490.5 B/turn** (the
570 B difference is `Point.content` moving between runs — reported rather than smoothed), and
`used_memory` **≈2,520 B/turn**.

**This arm measures the SIZE OF THE PRIZE, not a shipped change.** The move itself was withdrawn: the
repo's own drift pin (`tests/test_cache_projection.py::test_a_primary_property_round_trips_through_a_record_row`)
refuses a `CACHE_FORBIDDEN_COLUMNS` entry for a name `public.point` has no COLUMN for, because that dict is
keyed by record column names. See the audit's §5.1/§5.3.

## Per-shape census (single run, `before` arm)

| shape | payload bytes | note |
|---|---|---|
| `:Point` | 668,014 | `story_arc` 90,000 · `content` ~93,000 · `embedding` ~401,000 · the rest < 3,000 |
| `:GraphEvent` | 3,648 | 19 nodes × ~192 B payload; the §9.1 figure of 801 B/turn includes block overhead |
| `:Source` | 916 | |
| `:Event` | 680 | |
| `:Object` | 198 | |
| `:Session` | 177 | 5 sessions — the capture-telemetry family is ~20 B/session |
| `:Subject` | 139 | |

## Re-running it

```bash
uri="$(uv run python tools/test_lane.py uri)" || exit 1   # two steps on purpose
eval "$uri"
uv run python docs/evidence/13083-capability-census/probe_shape_bytes.py \
    --arm before --sessions 6 --turns 10 --legacy-prose > measurement-before.json
uv run python docs/evidence/13083-capability-census/probe_shape_bytes.py \
    --arm after  --sessions 6 --turns 10              > measurement-after.json
uv run python tools/test_lane.py down
```

⛔ Never the shared `localhost:6379` URI: nothing serves it on a cell, and a run that names it reports a
wall of connection refusals with no verdict (`#10399`).
