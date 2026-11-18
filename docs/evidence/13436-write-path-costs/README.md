# `#13436` P7 — the three write-path costs, measured (diagnosis only)

**Nothing here is optimised, and nothing here proposes an optimisation.** `#13436` names the failure
that would be: *"An optimisation off an unmeasured cost is the regression path."* Every one of these
three costs also has a recorded decision that forbids its obvious "fix", cited beside the number.

| | |
|---|---|
| **Artifact** | [`write-path-costs.json`](write-path-costs.json) — every arm, every sample statistic, the box |
| **Instrument** | [`tools/measure_13436_write_costs.py`](../../../tools/measure_13436_write_costs.py) |
| **Box** | docker lane `falkordb/falkordb-server:v4.20.6`, `graph` ver **42006 (4.20.6)**, load avg **3.5** at start / **4.0** at end |
| **Taken** | 2026-11-18, seven samples per measurement after warm-ups, medians reported |

Reproduce:

```bash
uv run python tools/test_lane.py run -- true          # a private lane; it prints the export lines
uv run python tools/measure_13436_write_costs.py --reps 7 --json /tmp/costs.json
```

The tool refuses rather than reports when an arm cannot be measured as posed: a seeded population
that did not land (read back, never assumed), a `core:`/bare control that paid a gate read, an
unarmed gate that paid none, and a `_schema_is_current()` that returned `False` (which would make the
sample the full sweep's cost, not the probe's).

## 1 · `_nudge_candidates` — a 400-row `:Point` scan returning FULL `content`

`TortoiseSDK._nudge_candidates` (`tortoise/sdk.py`) runs
`MATCH (n:Point) WHERE NOT n.id IN $ex RETURN n.id, n.content, n.name, n.label, n.is_operator LIMIT 400`
and produces only the ADVISORY `nudges` field of the write response. It is called from
`create_entity`'s return, from `create_edge`'s return, and once per Point in `ingest`.

| Points | rows the scan returns | `content` moved | nudge call (median) | object create WITHOUT nudge | WITH nudge | **delta** |
|---|---|---|---|---|---|---|
| 1,000 | 400 (the cap) | 118.1 KiB | **12.07 ms** | 5.07 ms | 17.34 ms | **+9.63 ms** |
| 5,000 | 400 (the cap) | 118.1 KiB | **12.10 ms** | 5.23 ms | 15.62 ms | **+10.39 ms** |

The door delta is self-controlled **on one graph**: same seeds, same door, the only difference is an
instance-attribute patch (`sdk._nudge_candidates = lambda *a, **k: []`). ⇒ **an advisory field is
~60–70 % of an object create's latency at these sizes**, and the cost is FLAT in Point count because
the 400-row cap is.

The payload axis — same Point count, only the seeded content length differs:

| content chars/row | `content` moved | nudge call (median) |
|---|---|---|
| 60 | 20.7 KiB | 8.01 ms |
| 300 | 114.5 KiB | 9.35 ms |
| 1,200 | 466.0 KiB | 9.95 ms |

⇒ **the bytes are not the dominant term at these lengths**: 22× the payload buys ~1.9 ms. What is
paid is the label scan plus row materialisation — the shape `#4997`'s unindexed-label class names,
not a transfer bill. (This is the arm a future "just stop returning `content`" change would have to
beat: it would save ≤2 ms of a ~12 ms call at 1,200 chars and ~1 ms at 300.)

⚠️ **Not measured:** the production graph (`#13232`'s "Unknown 2" — *is it ms or seconds there* — is
answered here only for 1k/5k nodes of OUR content shape), and any cold-start effect (`#13229`'s
cost, measured there).

## 2 · the `#5475` approved-kind gate — the whole-graph approval read, COUNTED

`pack_state.graph_installed_namespaces` is wrapped with a counter (the gate resolves it through an
in-function import, so the module attribute is the seam) and one request per shape is timed. **5,000
Points, approval set `{dev}`**:

| shape | gate reads per request | request (median) | gate's share |
|---|---|---|---|
| `create_point` `core:Project` (control — names no pack) | **0** | 13.45 ms | 0 |
| `create_point` bare `statement` (control) | **0** | 12.45 ms | 0 |
| `create_point` `dev:epic` | **1** | 44.64 ms | 19.23 ms |
| `create_point(..., pointKind="dev:epic")` (props passthrough) | **1** | 46.09 ms | 19.23 ms |
| `create_entity(object, objectKind="dev:epic")` | **1** | 52.42 ms | 19.23 ms |

**One read, not two.** `#13232`'s ranked action says the scan is *"paid twice per request for a
namespaced kind"*; at this head, for all three SDK doors measured, it is paid **once**. A single gate
read costs **19.23 ms at 5,000 Points**, so a namespaced write is **~3.4–3.9×** a `core:`/bare write
that pays nothing — the same order `#8977` recorded (14.9 ms vs 4.8 ms at 5k), on a slower box.

⇒ **the two-read shape is the HOSTED commit leg, and it is arithmetic on measured parts, not a
request-level sample** (`hosted_commit_composition` in the artifact): one deliberate pre-check read
per request (`hosted_api.py:23946`, which does **not** rescan at `:23976`, by its own comment) **plus
one read per namespaced entity written** (`_create_entity` → `_refuse_unapproved_kinds_in_props`) =
**2 for a single-entity commit**, N+1 for N entities. ⛔ **The owner of that cost is `#8977`**, whose
acceptance is *"a capture of M pack-kinded entities performs O(1) approval-set reads"* and whose
recorded warning is the one that matters here: **the fix may not weaken the gate** (an unvalidated
`ns:kind` made a whole pack writable).

## 3 · `projection._schema_is_current()` — the fail-closed pre-check

| Points | verdict | probe (median) | µs/point |
|---|---|---|---|
| 1,000 | `True` | **4.97 ms** | 4.97 |
| 5,000 | `True` | **6.17 ms** | 1.23 |
| 20,000 | `True` | **13.08 ms** | 0.65 |

⇒ **marginal ≈ 0.43 µs/point** with a **≈4.5 ms fixed floor on this box** (1k→20k slope: 0.427 µs/pt;
5k→20k: 0.461 µs/pt). ⚠️ **The function's docstring carries a different per-point constant —
"≈0.2 µs/point", from a 4.20.4 reading, and it already retracts that table for the pre-`#11682`
one-disjunct shape.** The marginal cost measured here is **~2× that constant on the SAME engine
version**, so the constant is **host-specific** and must not be restated as a property of the code;
the docstring's own 4.20.6 three-way sweep (1.25 / 2.74 / 7.91 ms at 1k/5k/20k) differs from this run
by a fixed floor, not by a slope.

⛔ **Do not "optimise" this by dropping the `IS NOT NULL` conjuncts or by trusting the
`point_fts_v2` marker** — the docstring at the function measures both, and `#5444` is the sanctioned
route (a write-maintained signal). This artifact changes neither.

## What these numbers do NOT decide

- no threshold, no budget, no "fast/slow" verdict;
- no owner: `#8977` owns cost 2, `#5444` owns cost 3, and cost 1 has **no issue at all** — that
  absence is the finding `#13436` P7 was written to produce, and it is left as an absence rather than
  converted into a fix by this lane.
