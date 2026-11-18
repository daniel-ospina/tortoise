# `#13436` P7 — the three write-path costs, measured (diagnosis only)

**Nothing here is optimised, and nothing here proposes an optimisation.** `#13436` names the failure
that would be: *"An optimisation off an unmeasured cost is the regression path."* Every one of these
three costs is bound by a recorded decision that forbids its obvious "fix", cited beside the number.

| | |
|---|---|
| **Artifacts** | [`write-path-costs.json`](write-path-costs.json) (run 1, load **7.3**) · [`write-path-costs-run2.json`](write-path-costs-run2.json) (run 2, load **7.3**) — a second run because one sample on a shared box is a number, not a measurement. Each carries its own box, load, and the graphs it measured. |
| **Instrument** | [`tools/measure_13436_write_costs.py`](../../../tools/measure_13436_write_costs.py) |
| **Box** | docker lane `falkordb/falkordb-server:v4.20.6`, `graph` ver **42006 (4.20.6)** |
| **Taken** | 2026-11-18, seven samples per measurement after warm-ups, medians reported |

Reproduce — **one process, because the lane's `export` lines do not survive into a second `uv run`**:

```bash
eval "$(uv run python tools/test_lane.py uri)"        # starts the lane; prints the four exports
uv run python tools/measure_13436_write_costs.py --reps 7 \
    --json docs/evidence/13436-write-path-costs/write-path-costs.json
```

(`tools/test_lane.py run -- …` is the *pytest* runner — `argv = [python, "-m", "pytest", *extra]`, so
`run -- true` is a collection error, not a no-op. The citation is the same one `#13436` uses for the
suite lane; here the tool is not a pytest target.)

The tool REFUSES rather than reports when an arm cannot be measured as posed: a seeded population that
did not land (read back, never assumed), a control that paid a gate read, a namespaced write that paid
none, a shape paying a varying number of reads, and a `_schema_is_current()` that returned anything but
`True` (which would make the sample the full sweep's cost, not the probe's).

⚠️ **Where the two runs differ, both numbers are given.** The box carried load 6–7.3 during both runs;
a single figure in this file is a median from one run, stated with the run it came from.

## 1 · `_nudge_candidates` — a 400-row `:Point` scan returning FULL `content`

`TortoiseSDK._nudge_candidates` (`tortoise/sdk.py`) runs
`MATCH (n:Point) WHERE NOT n.id IN $ex RETURN n.id, n.content, n.name, n.label, n.is_operator LIMIT 400`
and produces only the ADVISORY `nudges` field of the write response. Call sites: `create_entity`'s
return, `create_edge`'s return, and **once per Point** in `ingest`.

| Points | rows returned | `content` moved | nudge call (run 1 / run 2) | create WITHOUT / WITH nudge | **delta** |
|---|---|---|---|---|---|
| 1,000 | 400 (the cap) | 117.1 KiB | **12.77 / 12.68 ms** | 5.36 / 17.42 ⟶ **+12.06 ms** (run 1)<br>6.02 / 14.81 ⟶ **+8.79 ms** (run 2) | **+8.8 – +12.1 ms** |
| 5,000 | 400 (the cap) | 117.1 KiB | **12.28 / 12.85 ms** | 5.91 / 17.03 ⟶ **+11.12 ms** (run 1)<br>6.05 / 17.06 ⟶ **+11.01 ms** (run 2) | **+11.0 – +11.1 ms** |

The door delta is self-controlled **on ONE graph**: same seeds, same door, the only difference is an
instance-attribute patch (`sdk._nudge_candidates = lambda *a, **k: []`). ⇒ **an advisory field is
~55–70 % of the `create_entity` latency measured here** (the share moves with how fast the rest of the
door happens to run on a loaded box; the absolute delta, +8.8 to +12.1 ms, is the stable figure).

**The payload axis — and the first cut of this instrument got it wrong.** Content length against the
same 1,000-Point population, seeded from a real word list so bytes AND tokenisation scale together:

| content chars/row | `content` moved | nudge call run 1 | nudge call run 2 |
|---|---|---|---|
| 60 | 23.4 KiB | 9.10 ms | 9.37 ms |
| 300 | 117.1 KiB | 12.84 ms | 13.34 ms |
| 1,200 | 468.7 KiB | **23.35 ms** | **24.24 ms** |

⇒ **20× the content length (22× the bytes moved) takes the call from ~9 ms to ~24 ms — the content IS
a term, roughly linear above 300 chars.** ⛔ **A first cut seeded `"x" * n` and concluded the opposite
(~flat).** That seed varies BYTES while holding tokenisation constant (one repeated character is one
distinct token), and `_nudge_candidates` scores by TOKEN OVERLAP — so the shape was blind to the
scoring leg and its "flat within noise" reading was an artefact. Both the seed and the conclusion were
replaced; the retraction is recorded here because *"the payload does not matter"* is exactly the kind
of sentence that would have been quoted later.

**Internal consistency check:** the point-population arm at 1,000 Points uses the same 300-char seed, and
it reads 12.77 / 12.68 ms against the content axis's 12.84 / 13.34 ms at 300 chars — the two arms agree
within ~5 %, which is what makes the axis comparison meaningful.

**Who owns this cost — checked, because the first cut of this file said "nobody" and that was wrong.**
**`#11796` (OPEN)** owns the `ingest` half: *"the write-nudge block runs a placeholder keyed on the
literal word `related` — and scans 400 Points **per written point**"*. It does **not** cover the other
two doors, where one scan per write is paid by `create_entity` and `create_edge` on every call — **that
half is unowned**, and that gap is what P7 surfaces. ⚠️ Not measured here: the ingest door's N-scans
(`#11796` measured them; this arm calls `_nudge_candidates` directly and through `create_entity`), and
any cold-process effect (`#13229`'s cost, measured there).

## 2 · the `#5475` approved-kind gate — the whole-graph approval read, COUNTED

`pack_state.graph_installed_namespaces` is wrapped with a counter (the gate resolves it through an
in-function import, so the module attribute is the seam) and one request per shape is timed. **5,000
Points, approval set `{dev}`**; run 1 / run 2:

| shape | gate reads per request | request (median) | gate's share of the request |
|---|---|---|---|
| `create_point` `core:Project` (control) | **0** | 12.92 / 14.18 ms | 0 |
| `create_point` bare `statement` (control) | **0** | 10.94 / 16.76 ms | 0 |
| `create_point` `dev:epic` | **1** | 30.52 / 46.41 ms | 16.27 / 19.73 ms |
| `create_point(..., pointKind="dev:epic")` (props passthrough) | **1** | 34.65 / 36.25 ms | 16.27 / 19.73 ms |
| `create_entity(object, objectKind="dev:epic")` | **1** | 43.12 / 49.95 ms | 16.27 / 19.73 ms |
| ⛔ `create_point("dev:epic", …, pointKind="dev:epic")` — **SAME kind, TWO spellings** | **2** | 61.33 / 67.38 ms | 32.54 / 39.45 ms |
| ⛔ `create_entity(object, objectKind=…, object_kind=…)` — **SAME kind, TWO spellings** | **2** | 63.75 / 62.26 ms | 32.54 / 39.45 ms |

⇒ **A namespaced kind named ONCE pays exactly ONE whole-graph read** — the synthesis's *"paid twice per
request for a namespaced kind"* is **FALSIFIED for that shape** at this head, across three doors. A
single read costs **16.3–19.7 ms at 5,000 Points** against a **10.9–16.8 ms** bare/`core:` write, so
naming a pack roughly doubles a point write and is **~1.6×** an object write.

⇒ **The two-read request exists, and its trigger is the NUMBER OF KIND-VALUED PROPERTIES IN THE WRITE,
not the namespace.** `_refuse_unapproved_kinds_in_props` pays one read per kind-valued property it finds
(the `_KIND_PROP_KEYS` loop, the `_kind_spellings` alias loop, and **each `uses[]` item**), so it is paid
twice when the same kind arrives through two spellings (positional + props, camel + snake) **and equally**
when two *different* pack kinds arrive in one write (`create_event(eventKind="dev:a", uses=[{"kind":
"dev:b"}])`). Measured share: **≈53–59 % of the request**, or **≈65–78 % of the part above a bare write**;
the earlier "~85 %" in this file was a cross-row mix (doubled gate ÷ single-spelling request) and is
retracted.

⇒ **the hosted commit leg's own two reads are a DIFFERENT composition** (`hosted_commit_composition` in
each artifact, stated as arithmetic on measured parts and **not** an HTTP sample): one deliberate
pre-check read per request (`hosted_api.py:23946`, which does **not** rescan at `:23976`, by its own
comment) **plus one read per namespaced entity written** (`_create_entity` →
`_refuse_unapproved_kinds_in_props`) = **2 for a single-entity commit** — derived from the
single-spelling shape by name, after a first cut mistakenly derived it from the two-spelling row and
published 3.

⛔ **The owner of both shapes is `#8977`**, whose acceptance is *"a capture of M pack-kinded entities
performs O(1) approval-set reads"* and whose recorded warning is the one that matters here: **the fix may
not weaken the gate** (an unvalidated `ns:kind` made a whole pack writable).

## 3 · `projection._schema_is_current()` — the fail-closed pre-check

| Points | verdict | probe run 1 | probe run 2 | µs/point (run 1 / run 2) |
|---|---|---|---|---|
| 1,000 | `True` | 5.44 ms | 5.33 ms | 5.44 / 5.33 |
| 5,000 | `True` | 8.52 ms | 6.55 ms | 1.70 / 1.31 |
| 20,000 | `True` | 10.00 ms | 10.01 ms | 0.50 / 0.50 |

⇒ **marginal ≈0.24 µs/point in BOTH runs** (1k→20k slopes: 0.240 and 0.247 µs/pt) with a **≈5.2 ms fixed
floor on this box**. ⚠️ **The floor is the term that differs from the function's own docstring**
(1.25 ms @1k at 4.20.6, same engine version): the per-point slope reproduces — the docstring's
"≈0.2 µs/point" sits just under this box's 0.24 — so **this artifact does not correct that constant**;
an earlier draft did, from ONE sample, and the second run falsified it. The 5,000 reading is the noisy
one (8.52 vs 6.55); the 1,000 and 20,000 readings agree across runs to ~2 %.

**Who owns it — checked, and the answer is "nobody, and the source says otherwise".** `#5444` and
`#5482`, the two issues the function's docstring names for removing this cost, are **both CLOSED**
(`#5444` COMPLETED 2026-10-13 — it is what SHIPPED the expensive-but-correct precondition, not what
removes it). ⇒ **cost 3 is unowned**, and the docstring's attribution is stale (a dated note was added
at the function; the docstring's own numbers are left alone). ⛔ Dropping the `IS NOT NULL` conjuncts or
trusting the `point_fts_v2` marker remains refused by that docstring's measurements — a write-maintained
signal is the sanctioned route and it has no open issue.

## What these numbers do NOT decide

- no threshold, no budget, no "fast/slow" verdict;
- **no ownership invented**: cost 1's `ingest` half is `#11796`'s and its `create_entity`/`create_edge`
  half is nobody's; cost 2 is `#8977`'s; cost 3 is nobody's (`#5444`/`#5482` are closed). Naming an
  absence is the finding; converting it into a fix here is what `#13436` forbids.
