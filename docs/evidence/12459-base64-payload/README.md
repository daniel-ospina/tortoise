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
| verbatim (`f64`) vector, JSON text → frame | 5,770 B | 4,112 B | 1.40× |

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
| truncated body, padding intact | refused |
| one element short, count unchanged | refused |
| unknown frame version | refused |
| non-base64 character in body | refused (`validate=True`) |

The **element count is load-bearing**, measured: `b64decode` silently drops an incomplete trailing
quantum, and at **−16 characters** the decoded length is still a multiple of the element size (381 whole
floats) — so a length-only check accepts a silently truncated vector. Only the declared count refuses
it.

## Reproduce

```bash
env -u TORTOISE_DB_URI uv run python \
    docs/evidence/12459-base64-payload/probes/p2_base64_probe.py
```

`measurement.txt` is one run's verbatim output. Exit 0 = measured; the probe asserts every equivalence
it prints and returns non-zero if one fails.

## What this evidence does NOT cover

External research was unavailable on the box this was measured on (four rungs, each a different missing
key/server — see the note's §0). The comparables survey (Zep/Graphiti episode payloads, Mem0, Letta,
pgvector's on-disk array format) was **not performed**, and the note makes no claim about them.
