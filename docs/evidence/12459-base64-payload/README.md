---
title: "#12459 P2 — base64-of-packed-float32 byte measurement (evidence)"
type: engineering
domain: platform
doc_status: live
created: 2026-11-13
subjects.team: epistemic-team
aboutSubjects: Tortoise journal
aboutObjects: "#12459, #11821, #12458, #5004, #11886, docs/plans/2026-11-13-issue-12459-journal-base64-payload.md, tortoise/log.py EventLog, tortoise/embeddings.py stamp_journal_embedding, tools/journal_growth.py"
---

# `#12459` — P2 byte measurement (base64 of packed float32)

Evidence for `docs/plans/2026-11-13-issue-12459-journal-base64-payload.md`. **This directory contains a
measurement probe, not storage code**: it changes no payload, writes no product journal and touches no
configured path. ⚠️ **It does run the writer** — it appends the corpus to a throwaway
`tempfile.mkdtemp()/events.jsonl` with the **committed** `EventLog.append`, then re-reads that file, because
the baseline has to be P0's *real on-disk bytes*, not a re-serialisation. That is also why the probe is
species-confused if described as "in-memory": the record set is in memory, the bytes it measures are not.

## What is measured, and against what

The baseline is the journal the **landed P0 writer** produces (`EventLog.append` +
`stamp_journal_embedding`) over the committed 29-type corpus
(`supabase/tests/fixtures/record_projection_journal.jsonl`), not the pre-P0 9,176 B figure. The probe
runs `tools/journal_growth.py` over that journal first, so the baseline figure is produced by the
committed instrument rather than by arithmetic inside the probe:

```text
39 records  32,130 B  mean 823.8 B  p95 5,148 B  max 5,423 B
```

That reproduces P0's published corpus total (`32,130 B`, `max 5,423 B`) exactly.

## Why `tools/journal_growth.py` cannot answer the vector questions

The tool attributes bytes by **re-serialising** each value with `json.dumps`' default (spaced)
separators (`_json_bytes`; its own docstring calls the output *attribution*). Since P0 also moved the
writer to compact separators, the tool's vector figure and the journal's on-disk bytes are on different
rulers — `5,072 B` spaced vs `4,689 B` compact for the same payload. That is #11886. Every P0 vector figure
here is the **on-disk compact span**, and it is taken from the bytes the probe's own `tempfile.mkdtemp()`
journal actually holds: the value is `raw_decode`d out of the written line, and the probe ASSERTS that the
compact re-serialisation reproduces that line **byte for byte** (that is the `⇒ the P0 figures above are the
WRITTEN bytes` line in `measurement.txt`). ⚠️ Not "before printing anything" — the corpus and
`journal_growth` lines print first, so the verdict is the exit code, not the ordering. The P2 columns are the framed *candidate* — nothing writes
them yet — re-serialised with the same compact separators. Each figure in the note names its ruler.

## The figures

| | P0 | P2 frame | ratio |
|---|---|---|---|
| `PointAdded` record | 5,423 B | 2,798 B | 1.94× |
| — its vector payload | 4,689 B (12.2 chars/float) | 2,064 B (5.4) | 2.27× |
| `OperatorAdded` record | 5,124 B | 2,499 B | 2.05× |
| `PointPromoted` record | 4,936 B | 2,311 B | 2.14× |
| `OperatorPromoted` record | 4,924 B | 2,299 B | 2.14× |
| whole corpus (39 records) | 32,130 B | 19,005 B | 1.69× |
| verbatim (`f64`) vector, JSON text → frame — **off-grid fixture** | 5,770 B | 4,112 B | 1.40× |
| …same, a **realistic** full-precision float64 vector | 7,552 B | 4,112 B | 1.84× |

Frame arithmetic for one 384-d vector: `384 × 4 = 1,536 B` raw → base64 `2,048 B` (+33.3 %) → `+16 B`
of framing (14 B prefix + 2 B of JSON quotes) = **2,064 B**.

⚠️ **The "~4.1×" in #11821 and #12459 is pre-P0 → P2** (`8,414 / 2,048`). P0 already took the first
1.79×, so the step still on the table is **2.27× on the vector, 1.94× on the record**.

## The two properties the format must survive

- **Decode equivalence at float32 bits** — **two** links computed against something **outside** the frame,
  both facing the producer: `decoded f32 == producer f32` and `P0 text→f32 == producer f32`. The third
  reported line, frame-invertibility (`decoded == the frame's own input`), is *implied* by those two by
  transitivity and carries no discriminating power of its own — it is printed for naming, not as an
  independent test. (An earlier version's third link was `pack_f32(narrow(x)) == pack_f32(x)` — an
  identity, true for random floats with no journal at all, so its `assert` could never fail.) A
  **float64-value** comparison is deliberately not the test, and it fails in OPPOSITE directions depending
  on the truth side: against the **producer** it passes 384 of 384 — an `f32` value is exactly a
  `float64`, so value equality carries no width and cannot discriminate; against P0's **un-narrowed
  decimal text** it rejects a correct encode, 0 of 384. Both arms are printed and asserted, beside a
  **1-ULP perturbation** negative control that proves the bits assertion can fail. ⚠️ The two
  comparisons need DIFFERENT truth handling, and conflating them is how this paragraph was wrong before:
  the f32-vs-P0-text comparison narrows the truth side to the **stored width** (`#8615` — the probe does it
  by packing both sides to float32 bits), while the `f64` fixture below is compared **bit-identically at its
  own width and is NOT narrowed by anything** — applying `stored_form.narrow_to_stored_width` /
  `consistency._as_f32` there would red the probe's own `assert` and would be the narrowing §4.4 calls a
  reopen.
- **The `f64` population is not narrowed** — a caller-owned / preserved vector round-trips to the
  **same float64 bits**, and the fixture is off the float32 grid so the check cannot pass vacuously
  (P0's R2 rule).

## The refusals (each raises; none decodes silently short)

| corruption | verdict |
|---|---|
| last quantum replaced + one dropped (a 1,536 B payload carries **no** `=` padding — 512 exact quanta) | refused (1,533 B decoded) |
| one element short, count unchanged | refused (1,532 B) |
| **packed `f64` under a declared `f32`** | refused (3,072 B decoded against a declared 1,536 B) |
| unknown frame version | refused |
| non-base64 character **replacing** alphabet chars | refused — by the decoder under `validate=True` (`binascii.Error`); the body's **length** is unchanged (2,048 chars, `%4==0`), so alignment is not what breaks — the **count** catches this one only under the *default* decoder, which discards the 8 `*` and leaves 1,530 B (`%4==2`) |
| non-base64 chars **inserted** (count-clean) | refused, by `validate=True` only — the default `False` discards them and decodes to the *correct* vector silently |

The **element count is load-bearing**, measured: `b64decode` *refuses* a cut that is not a multiple of 4
characters, so the hazard is the **quantum-aligned** cut — 4 base64 chars are 3 whole bytes, so a body
shortened by a multiple of 4 is still valid base64 and decodes silently to fewer bytes. With `m` = the
number of 4-character quanta removed, the decoded length is `4N − 3m` bytes (a quantum carries 3 bytes, not
4), so it stays a multiple of the 4-byte element only when `3m ≡ 0 (mod 4)`, i.e. `m ≡ 0 (mod 4)` — every
**16th** character. At **−16 characters** (`m = 4`, 1,524 B = **381 whole floats**) that is exactly the
case, so a length-only check accepts a silently truncated vector. Only the declared count refuses it.

## Reproduce

```bash
env -u TORTOISE_DB_URI uv run python \
    docs/evidence/12459-base64-payload/probes/p2_base64_probe.py
```

`measurement.txt` is one run's verbatim output. Exit 0 = measured; exit **1** = a claim was FALSIFIED (a
control or an assertion failed, and the guard prints which) — so a wrapper must not read a non-zero exit as
"the probe crashed"; exit 2 means the interpreter was older than 3.12 and the probe refused to run. The probe **asserts** the three decode links (two of them
producer-facing, plus the frame round-trip), the `f64` round-trip and its off-grid fixture, the six refusals
above, the sweep's cut set, the declared-count vacuity guard, and **all three** controls around the bits
assertion — the float64-VALUE comparison passing against the producer, the same comparison REJECTING P0's
un-narrowed text, and the 1-ULP perturbation flipping. ⚠️ Each is asserted, not printed: a printed control is
not a control — that is how a tautological link survived one round, and the first version of the float64 arm
was a widening identity with `decoded` absent from it, so its label named a comparison it never computed. The
remaining lines are printed evidence.

## External research

It was run — four `sonar` queries (~\$0.024) on: encodings for a float array in a text log, the 9-digit
float32 round-trip bound, per-record versioning on a replay path, and the comparables (Zep/Graphiti, Mem0,
Letta, pgvector). ⚠️ **§0 records the findings, not the sources** — the search answers were summarised into
§0(a)–(d) without transcribing their citation lists, so a reader cannot re-check them from this repository;
§1–§4 stand on the probe and on in-repo sources and do not depend on §0. Re-running the four queries is the
way to re-derive §0. The routing history is recorded because the first
draft got it wrong: rung 1's `seo-intelligence` MCP server is absent, and `PERPLEXITY_API_KEY` is **not
exported** in a fresh session's shell — but it **is** present in `$HOME/.env`, so `set -a; . ~/.env` makes
rung 2 work. Declaring the survey impossible was a false inference from a true observation.
