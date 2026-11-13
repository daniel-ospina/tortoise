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
measurement probe, not storage code**: nothing here encodes a journal, touches a writer, or changes a
payload. It computes lengths and bit-equality over an in-memory record set built by the **committed
writer**.

## What is measured, and against what

The baseline is the journal the **landed P0 writer** produces (`EventLog.append` +
`stamp_journal_embedding`) over the committed 29-type corpus
(`supabase/tests/fixtures/record_projection_journal.jsonl`), not the pre-P0 9,176 B figure. The probe
runs `tools/journal_growth.py` over that journal first, so the baseline figure is produced by the
committed instrument rather than by arithmetic inside the probe:

```
39 records  32,130 B  mean 823.8 B  p95 5,148 B  max 5,423 B
```

That reproduces P0's published corpus total (`32,130 B`, `max 5,423 B`) exactly.

## Why `tools/journal_growth.py` cannot answer the vector questions

The tool attributes bytes by **re-serialising** each value with `json.dumps`' default (spaced)
separators (`_json_bytes`; its own docstring calls the output *attribution*). Since P0 also moved the
writer to compact separators, the tool's vector figure and the journal's on-disk bytes are on different
rulers — `5,072 B` spaced vs `4,689 B` compact for the same payload. That is #11886. Every vector figure
here is the **on-disk compact span**, measured by walking the journal the probe actually wrote, and each
figure in the note names its ruler.

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

- **Decode equivalence at float32 bits** — `base64 → float32 bits == P0 text → float32 bits == the
  producer's float32 bits`, on every vector-bearing record. It is **not** float64-value equality with
  P0's text: P0's stored float64 is deliberately off the float32 grid (`0.0012301534`, not
  `0.001230153371579945`), which is why `stored_form.narrow_to_stored_width` / `consistency._as_f32`
  narrow the truth side (#8615).
- **The `f64` population is not narrowed** — a caller-owned / preserved vector round-trips to the
  **same float64 bits**, and the fixture is off the float32 grid so the check cannot pass vacuously
  (P0's R2 rule).

## The refusals (each raises; none decodes silently short)

| corruption | verdict |
|---|---|
| truncated body, padding intact | refused (1,533 B decoded) |
| one element short, count unchanged | refused (1,532 B) |
| **packed `f64` under a declared `f32`** | refused (3,072 B decoded against a declared 1,536 B) |
| unknown frame version | refused |
| non-base64 character in body | refused (`validate=True`) |

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

`measurement.txt` is one run's verbatim output. Exit 0 = measured; the probe asserts every equivalence
it prints and returns non-zero if one fails.

## External research

It was run — four `sonar` queries (~$0.024) on: encodings for a float array in a text log, the 9-digit
float32 round-trip bound, per-record versioning on a replay path, and the comparables (Zep/Graphiti, Mem0,
Letta, pgvector). ⚠️ **§0 records the findings, not the sources** — the search answers were summarised into
§0(a)–(d) without transcribing their citation lists, so a reader cannot re-check them from this repository;
§1–§4 stand on the probe and on in-repo sources and do not depend on §0. Re-running the four queries is the
way to re-derive §0. The routing history is recorded because the first
draft got it wrong: rung 1's `seo-intelligence` MCP server is absent, and `PERPLEXITY_API_KEY` is **not
exported** in a fresh session's shell — but it **is** present in `$HOME/.env`, so `set -a; . ~/.env` makes
rung 2 work. Declaring the survey impossible was a false inference from a true observation.
