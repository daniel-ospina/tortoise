---
title: "#13436 P7 — the three write-path costs, measured (diagnosis only)"
type: evidence
domain: platform
doc_status: final
created: 2026-11-17
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#13436, #13445, tools/measure_13436_write_costs.py"
---

# `#13436` P7 — the three write-path costs, measured (diagnosis only)

**Nothing here is optimised, and nothing here proposes an optimisation.** `#13436` names the failure
that would be: *"An optimisation off an unmeasured cost is the regression path."* Every one of these
three costs is bound by a recorded decision that forbids its obvious "fix", cited beside the number.

| | |
|---|---|
| **Artifacts** | [`write-path-costs.json`](write-path-costs.json) — **run 1**, 2026-11-18T02:14:49Z, load **6.1–7.6** (written before the `box.git_*` provenance fields existed) · [`write-path-costs-run2.json`](write-path-costs-run2.json) — **run 2**, 2026-11-18T03:01:16Z, load **9.4–11.3** (it rose during the run), whose `box.git_head` names the tree it measured and whose `git_dirty` is EMPTY. Two runs because one sample on a shared box is a number, not a measurement; and the second records **which tree** it measured, so the provenance claim is checkable from the artifact instead of from this sentence. |
| **Instrument** | [`tools/measure_13436_write_costs.py`](../../../tools/measure_13436_write_costs.py) |
| **Box** | docker lane `falkordb/falkordb-server:v4.20.6`, `graph` ver **42006 (4.20.6)**, Python 3.12.3 |

Reproduce — **in one shell**, because `uri` *prints* `export` lines and nothing sets them for you:

```bash
eval "$(uv run python tools/test_lane.py uri)"        # starts the lane, prints the four exports
uv run python tools/measure_13436_write_costs.py --reps 7 \
    --json docs/evidence/13436-write-path-costs/write-path-costs.json
```

(`tools/test_lane.py run -- …` is the *pytest* runner — `argv = [python, "-m", "pytest", *extra]`, so
`run -- true` is a collection error, not a no-op. The lane is the same one `#13436` cites for the
suite; here the tool is not a pytest target.)

The tool REFUSES rather than reports when an arm cannot be measured as posed: a seeded population that
did not land (read back, never assumed), a control that paid a gate read, a namespaced write that paid
none, a shape paying a varying number of reads, and a `_schema_is_current()` that returned anything but
`True` (which would make the sample the full sweep's cost, not the probe's).

⚠️ **Both runs are reported for every figure, and where they disagree both numbers stand.** The box
carried load **6.1–7.6** in run 1 and **9.4–11.3** in run 2 — two different machines-under-load, one
of them heavily oversubscribed — which is why the ABSOLUTE cost is the figure this file leads with and
the derived SHARES are given as ranges. ⚠️ **Run 2 drifted materially DURING the measurement** (1-min
average 9.59 → 11.27, +17.5 %), and the §1 block-pair deltas are the figures most exposed to that: the
delta is a before/after pair of sequential blocks, so a load rise between them lands in it. That is
stated rather than smoothed — it is also why the §1 numbers are quoted as a RANGE across the two runs
and why no single figure is presented as *the* cost.

## 1 · `_nudge_candidates` — a 400-row `:Point` scan returning FULL `content`

`TortoiseSDK._nudge_candidates` (`tortoise/sdk.py`) runs
`MATCH (n:Point) WHERE NOT n.id IN $ex RETURN n.id, n.content, n.name, n.label, n.is_operator LIMIT 400`
and produces only the ADVISORY `nudges` field of the write response. Call sites: `create_entity`'s
return, `create_edge`'s return, and **once per Point** in `ingest`.

| Points | rows returned | `content` moved | nudge call (run 1 / run 2) | create WITHOUT → WITH nudge | **delta** |
|---|---|---|---|---|---|
| 1,000 | 400 (the cap) | 117.1–117.2 KiB | **12.77 / 13.24 ms** | 6.35 → 18.41 (run 1) · 5.69 → 17.97 (run 2) | **+12.06 / +12.28 ms** |
| 5,000 | 400 (the cap) | 117.1–117.2 KiB | **12.28 / 13.33 ms** | 5.70 → 16.81 (run 1) · 5.98 → 18.81 (run 2) | **+11.12 / +12.84 ms** |

The point COUNT does not move it: the same 400 rows come back at 1,000 and at 5,000 Points, and the
call reads **12.3–13.3 ms** in the four cells.

The door delta is self-controlled **on ONE graph**: same seeds, same door, the only difference is an
instance-attribute patch (`sdk._nudge_candidates = lambda *a, **k: []`). ⚠️ **The two blocks are
SEQUENTIAL, not interleaved** (with-nudge first, then patched), so a load drift between them lands
entirely in the delta — the artifact publishes load before AND after so a reader can see it.
⚠️ In run 2 that check FAILS: the 1-min average rose 9.59 → 11.27, so its block-pair deltas are
the figures most exposed to drift and are quoted as part of a cross-run range, never on their own.

⇒ **an advisory field is ~65–68 % of the `create_entity` latency measured here** (12.06/18.41 = 65.5 %,
11.12/16.81 = 66.1 %, 12.28/17.97 = 68.3 %, 12.84/18.81 = 68.2 %)
— **+11.1 to +12.8 ms of a 16.8–18.8 ms write.**

**The payload axis — and the first cut of this instrument got it wrong.** Content length against the
same 1,000-Point population, seeded from a real word list so bytes AND tokenisation scale together:

| content chars/row (declared → measured) | `content` moved | nudge call run 1 | nudge call run 2 |
|---|---|---|---|
| 60 → **59.84** (run 1) / **60.00** (run 2) | 23.4 KiB | 9.10 ms | 9.98 ms |
| 300 → **299.81** / **300.00** | 117.1–117.2 KiB | 12.84 ms | 14.56 ms |
| 1,200 → **1,199.98** / **1,200.00** | 468.7–468.8 KiB | **23.35 ms** | **23.42 ms** |

⇒ **20× the content length (20× the bytes moved) takes the call from 9.1 ms to 23.35 / 23.42 ms in the
two runs — 2.57× / 2.35× — so the content IS a term, and the shape is close to linear above 300 chars.**

⚠️ **«Declared → measured» is not decoration** (`#13436` review). The word-list seeder aimed at the
declared length and could land **up to one character short** — `" ".join(out)[:chars]` returns the WHOLE
join when that join is a character short — so run 1 averages **59.84 / 299.81 / 1,199.98**
chars/row, while **run 2 measures exactly 60 / 300 / 1,200**. Both artifacts carry `content_chars_requested` beside the MEASURED `content_chars_per_row` /
`content_bytes_per_row`, re-derived from their own `content_bytes_returned / rows_returned` (the seed is
an ASCII word bank, so bytes = characters for these runs). The instrument now **pads to exactly the
declared length and raises otherwise** — which is why run 2's axis is 60 / 300 / 1,200 to the character. ⚠️ The
multiplier is quoted from the DECLARED axis (**20×**) because that is what the axis is chosen on; at the
measured lengths it is 1,199.975 / 59.84 = **20.05×** in run 1 and exactly **20.0×** in run 2, the same number to the precision this box holds.
(The first cut was 7 characters short — 53 / 293 / 1,193 published as 60 / 300 / 1,200 — which is where
the irreconcilable "22×" came from, and why this is now a pinned invariant rather than a comment.)
⛔ **A first cut seeded `"x" * n` and concluded the opposite (~flat).** That seed varies BYTES while
holding tokenisation constant (one repeated character is one distinct token), and `_nudge_candidates`
scores by TOKEN OVERLAP — so the shape was blind to the scoring leg and its "flat within noise" reading
was an artefact. Both the seed and the conclusion were replaced; the retraction is recorded here because
*"the payload does not matter"* is exactly the kind of sentence that would have been quoted later.

⚠️ **The two arms are separate graph populations, and they agree less at higher load:** the
1,000-Point population arm seeds the same 300 chars and read 12.77 vs the content arm's 12.84 in run 1
(0.5 %), but 13.24 vs 14.56 in run 2 (9.9 %). That is why the axis is reported per run rather than
pooled, and why the ratio is what carries the conclusion rather than the middle point's absolute: the
1,200-char endpoint reproduces across both runs to **0.3 %** (23.35 vs 23.42), while the 60-char cell
moves **9.6 %** between them (9.10 vs 9.98) — the same magnitude as the disagreement above.

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

| shape | gate reads per request | request run 1 → run 2 | one gate read (single / double) |
|---|---|---|---|
| `create_point` `core:Project` (control) | **0** | 12.92 → 11.95 ms | — |
| `create_point` bare `statement` (control) | **0** | 10.94 → 13.97 ms | — |
| `create_point` `dev:epic` | **1** | 30.52 → 31.89 ms | 16.27 / 16.89 ms |
| `create_point(..., pointKind="dev:epic")` (props passthrough) | **1** | 34.65 → 33.52 ms | 16.27 / 16.89 ms |
| `create_entity(object, objectKind="dev:epic")` | **1** | 43.12 → 38.73 ms | 16.27 / 16.89 ms |
| ⛔ `create_point("dev:epic", …, pointKind="dev:epic")` — **SAME kind, TWO spellings** | **2** | 61.33 → 51.53 ms | 32.54 / 33.78 ms |
| ⛔ `create_entity(object, objectKind=…, object_kind=…)` — **SAME kind, TWO spellings** | **2** | 63.75 → 61.70 ms | 32.54 / 33.78 ms |

⇒ **A namespaced kind named ONCE pays exactly ONE whole-graph read** — the synthesis's *"paid twice per
request for a namespaced kind"* is **FALSIFIED for that shape** at this head, across three doors. A
single read costs **16.3–16.9 ms at 5,000 Points** against a **10.9–14.0 ms** bare/`core:` write in the
same run, so naming a pack costs **2.3–2.8× the same run's control** (run-matched: 30.52/10.94 = 2.79×
and 31.89/13.97 = 2.28× against bare; 30.52/12.92 = 2.36× and 31.89/11.95 = 2.67× against `core:`),
and an object write pays the same one read inside a 38.7–43.1 ms request.

⇒ **The two-read request exists, and its trigger is the NUMBER OF KIND-VALUED PROPERTIES IN THE WRITE,
not the namespace.** `_refuse_unapproved_kinds_in_props` pays one read per kind-valued property it finds
(the `_KIND_PROP_KEYS` loop, the `_kind_spellings` alias loop, and **each `uses[]` item**), so it is paid
twice when the same kind arrives through two spellings (positional + props, camel + snake) **and equally**
when two *different* pack kinds arrive in one write (`create_event(eventKind="dev:a", uses=[{"kind":
"dev:b"}])` — mechanism, not measured here).

Measured share of the request: **37.7–53.3 %** for a single-spelling namespaced write (run 1: 53.3 % on
`point_ns`, 37.7 % on `object_ns`; run 2: 53.0 % / 50.4 % / 43.6 %) and **51.0–65.6 %** for a two-spelling
one (run 1: 53.1 % / 51.0 %; run 2: 65.6 % / 54.7 %). ⚠️ **The share is the unstable figure** — it moves with
how fast the rest of the door happens to run on a loaded box, and one draft's "share of the part above a
bare write" exceeded 100 % on a later run, because the control itself got slower. **The stable figure is
the absolute cost of a read: 16.3–16.9 ms at 5,000 Points.** Two earlier percentages in this file are
retracted rather than reworded: *"~85 % of the write"* (a cross-row mix — doubled gate ÷ single-spelling
request) and *"65–78 % of the part above a bare write"* (an unstable denominator).

⇒ **the hosted commit leg's own two reads are a DIFFERENT composition** (`hosted_commit_composition` in
each artifact, stated as arithmetic on measured parts and **not** an HTTP sample): one deliberate
pre-check read per request (`hosted_api.py:23946`, which does **not** rescan at `:23976`, by its own
comment) **plus one read per namespaced entity written** (`_create_entity` →
`_refuse_unapproved_kinds_in_props`) = **2 for a single-entity commit** — derived from the
single-spelling shape **by name**, after a first cut mistakenly derived it from the two-spelling row and
published 3.

⛔ **The owner of both shapes is `#8977`**, whose acceptance is *"a capture of M pack-kinded entities
performs O(1) approval-set reads"* and whose recorded warning is the one that matters here: **the fix may
not weaken the gate** (an unvalidated `ns:kind` made a whole pack writable).

## 3 · `projection._schema_is_current()` — the fail-closed pre-check

| Points | verdict | probe run 1 → run 2 | µs/point (run 1 / run 2) |
|---|---|---|---|
| 1,000 | `True` | 5.44 → 5.30 ms | 5.44 / 5.30 |
| 5,000 | `True` | 8.52 → 6.83 ms | 1.70 / 1.37 |
| 20,000 | `True` | 10.00 → 8.76 ms | 0.50 / 0.44 |

⇒ **marginal 0.18–0.24 µs/point across the two runs** (1k→20k slopes: 0.240 and 0.182 µs/pt) with a
**5.3–5.4 ms fixed floor on this box**. ⚠️ **The floor is the term that differs from the function's own
docstring** (1.25 ms @1k at 4.20.6, same engine version) and the slope **brackets** its "≈0.2 µs/point"
(0.18 below it, 0.24 above it, and an earlier run read 0.26) — so **this artifact corrects that constant in
neither direction**: the slope reproduces within host variation, the FLOOR is what this box adds. An
earlier draft DID claim a 2× correction from one sample; the later runs falsified it, which is why the
claim is gone rather than reworded. The 5,000 reading is the noisy one in both runs (8.52 vs 6.83); the
1,000 and 20,000 readings each agree across runs to within ~13 %.

**Who owns it — checked, and the answer is "nobody, and the source says otherwise".** `#5444` and
`#5482`, the two issues the function's docstring names for removing this cost, are **both CLOSED**
(`#5444` COMPLETED 2026-10-13 — it is what SHIPPED the expensive-but-correct precondition, not what
removes it; `#5482` COMPLETED too). ⇒ **cost 3 is unowned**, and the docstring's attribution is stale (a
dated note was added at the function; the docstring's own numbers are left alone). ⛔ Dropping the
`IS NOT NULL` conjuncts or trusting the `point_fts_v2` marker remains refused by that docstring's
measurements — a write-maintained signal is the sanctioned route and it has no open issue.

## What these numbers do NOT decide

- no threshold, no budget, no "fast/slow" verdict;
- **no ownership invented**: cost 1's `ingest` half is `#11796`'s and its `create_entity`/`create_edge`
  half is nobody's; cost 2 is `#8977`'s; cost 3 is nobody's (`#5444`/`#5482` are closed). Naming an
  absence is the finding; converting it into a fix here is what `#13436` forbids.
