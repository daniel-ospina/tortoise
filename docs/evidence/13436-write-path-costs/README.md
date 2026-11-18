# `#13436` P7 — the three write-path costs, measured (diagnosis only)

**Nothing here is optimised, and nothing here proposes an optimisation.** `#13436` names the failure
that would be: *"An optimisation off an unmeasured cost is the regression path."* Every one of these
three costs also has a recorded decision that forbids its obvious "fix", cited beside the number.

| | |
|---|---|
| **Artifact** | [`write-path-costs.json`](write-path-costs.json) — every arm, every sample statistic, the box |
| **Instrument** | [`tools/measure_13436_write_costs.py`](../../../tools/measure_13436_write_costs.py) |
| **Box** | docker lane `falkordb/falkordb-server:v4.20.6`, `graph` ver **42006 (4.20.6)**, load avg **5.9** at start / **5.9** at end |
| **Taken** | 2026-11-18, seven samples per measurement after warm-ups, medians reported |

Reproduce:

```bash
uv run python tools/test_lane.py run -- true          # a private lane; it prints the export lines
uv run python tools/measure_13436_write_costs.py --reps 7 --json /tmp/costs.json
```

The tool refuses rather than reports when an arm cannot be measured as posed: a seeded population
that did not land (read back, never assumed), a control that paid a gate read, a namespaced write
that paid none, and a `_schema_is_current()` that returned `False` (which would make the sample the
full sweep's cost, not the probe's).

⚠️ **TWO RUNS ARE RECORDED, AND THEY DO NOT AGREE TO THE DECIMAL.** The committed artifact is the
run at **load ≈5.9**; an earlier run at **load ≈3.5** is quoted beside it wherever the two differ,
because a single sample on a shared box would be a number, not a measurement. Where a figure moves
between them, the range is stated and no constant is asserted.

## 1 · `_nudge_candidates` — a 400-row `:Point` scan returning FULL `content`

`TortoiseSDK._nudge_candidates` (`tortoise/sdk.py`) runs
`MATCH (n:Point) WHERE NOT n.id IN $ex RETURN n.id, n.content, n.name, n.label, n.is_operator LIMIT 400`
and produces only the ADVISORY `nudges` field of the write response. It is called from
`create_entity`'s return, from `create_edge`'s return, and once per Point in `ingest`.

| Points | rows the scan returns | `content` moved | nudge call (median) | object create WITHOUT nudge | WITH nudge | **delta** |
|---|---|---|---|---|---|---|
| 1,000 | 400 (the cap) | 118.1 KiB | **12.19 ms** | 5.0–6.0 ms | 16.2–17.3 ms | **+11.22 ms** |
| 5,000 | 400 (the cap) | 118.1 KiB | **13.07 ms** | 5.2–5.7 ms | 15.6–16.8 ms | **+11.09 ms** |

The door delta is self-controlled **on one graph**: same seeds, same door, the only difference is an
instance-attribute patch (`sdk._nudge_candidates = lambda *a, **k: []`). ⇒ **an advisory field is
~65–70 % of an object create's latency at these sizes**, and the cost is FLAT in Point count because
the 400-row cap is.

The payload axis — same Point count, only the seeded content length differs (**this run**):

| content chars/row | `content` moved | nudge call (median) |
|---|---|---|
| 60 | 20.7 KiB | 9.43 ms |
| 300 | 114.5 KiB | 8.73 ms |
| 1,200 | 466.0 KiB | 9.61 ms |

⛔ **The payload axis is FLAT WITHIN NOISE — 22× the content, no movement beyond the spread.** The
earlier run at lower load showed 8.01 / 9.35 / 9.95 ms, i.e. at most ~1.9 ms across the same 22×,
which is the same conclusion with the noise the other way. ⇒ **what is paid is the label scan plus
row materialisation (the shape `#4997`'s unindexed-label class names), NOT the transferred bytes.**
This matters for the tempting "stop returning `content`" change: it would save at most ~1–2 ms of a
~9–12 ms call, so the honest entry point is the scan, not the column list — and ⛔ **that decision is
not taken here.**

⚠️ **Not measured:** the production graph (`#13232`'s "Unknown 2" — *is it ms or seconds there* — is
answered here only for 1k/5k nodes of OUR content shape), and any cold-start effect (`#13229`'s
cost, measured there).

## 2 · the `#5475` approved-kind gate — the whole-graph approval read, COUNTED

`pack_state.graph_installed_namespaces` is wrapped with a counter (the gate resolves it through an
in-function import, so the module attribute is the seam) and one request per shape is timed. **5,000
Points, approval set `{dev}`**:

| shape | gate reads per request | request (median) | gate's share |
|---|---|---|---|
| `create_point` `core:Project` (control) | **0** | 11.63 ms | 0 |
| `create_point` bare `statement` (control) | **0** | 11.07 ms | 0 |
| `create_point` `dev:epic` | **1** | 37.42 ms | 18.37 ms |
| `create_point(..., pointKind="dev:epic")` (props passthrough) | **1** | 37.17 ms | 18.37 ms |
| `create_entity(object, objectKind="dev:epic")` | **1** | 43.82 ms | 18.37 ms |
| ⛔ `create_point("dev:epic", …, pointKind="dev:epic")` — **SAME pack, TWO spellings** | **2** | 60.24 ms | 36.73 ms |
| ⛔ `create_entity(object, objectKind=…, object_kind=…)` — **SAME pack, TWO spellings** | **2** | 64.04 ms | 36.73 ms |

⇒ **The count is a property of the SPELLING, not of the namespace.** A namespaced kind named ONCE
pays **exactly one** whole-graph read — the synthesis's *"paid twice per request for a namespaced
kind"* is **FALSIFIED for that shape at this head**, across three doors. The two-read request exists,
but its trigger is a write that names the SAME pack kind through **two spellings** (the point door's
positional `kind` *plus* the props passthrough, or the camel `objectKind` *plus* the fold's snake
`object_kind` the gate also screens) — and there it is paid **per spelling**, 2 reads / 36.7 ms in one
request, i.e. **~85 % of the write** is the gate.

⇒ **the hosted commit leg's own two reads are a DIFFERENT composition** (`hosted_commit_composition`
in the artifact, stated as arithmetic on measured parts rather than a request-level sample): one
deliberate pre-check read per request (`hosted_api.py:23946`, which does **not** rescan at `:23976`,
by its own comment) **plus one read per namespaced entity written** (`_create_entity` →
`_refuse_unapproved_kinds_in_props`) = **2 for a single-entity commit**.

⛔ **The owner of both shapes is `#8977`**, whose acceptance is *"a capture of M pack-kinded entities
performs O(1) approval-set reads"* and whose recorded warning is the one that matters here: **the fix
may not weaken the gate** (an unvalidated `ns:kind` made a whole pack writable).

## 3 · `projection._schema_is_current()` — the fail-closed pre-check

| Points | verdict | probe (median) | µs/point |
|---|---|---|---|
| 1,000 | `True` | **5.17 ms** | 5.17 |
| 5,000 | `True` | **5.59 ms** | 1.12 |
| 20,000 | `True` | **9.69 ms** | 0.48 |

⚠️ **Do not read that table as a slope; fit it, and check the second run.** The marginal cost here is
**≈0.24–0.27 µs/point** (1k→20k: 0.238; 5k→20k: 0.273) with a **≈4.7 ms fixed floor**; the earlier
run at lower load read **≈0.43 µs/point** with a **≈4.5 ms floor**. ⇒ **the per-point term is
load-sensitive, and the docstring's own "≈0.2 µs/point" (4.20.4, one-disjunct shape — a table that
function already retracts as pre-`#11682`) sits at the fast end of a range this box reproduces on the
SAME engine version; what differs from the docstring's 4.20.6 sweep (1.25 / 2.74 / 7.91 ms at
1k/5k/20k) is the FIXED floor (~4.5–5.2 ms here), not the slope.** ⛔ **So this artifact does not
correct that constant** — an earlier draft of this README claimed a 2× discrepancy from one sample,
and the second run falsified the claim; both runs are recorded rather than the flattering one.

⛔ **Do not "optimise" this by dropping the `IS NOT NULL` conjuncts or by trusting the
`point_fts_v2` marker** — the docstring at the function measures both, and `#5444` is the sanctioned
route (a write-maintained signal). This artifact changes neither.

## What these numbers do NOT decide

- no threshold, no budget, no "fast/slow" verdict;
- no owner: `#8977` owns cost 2, `#5444` owns cost 3, and cost 1 has **no issue at all** — that
  absence is the finding `#13436` P7 was written to produce, and it is left as an absence rather than
  converted into a fix by this lane.
