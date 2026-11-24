---
title: "Source spool proof — byte-identical before and after the drain rehearsal (#14232)"
type: evidence
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: capture-spool, session-drain
aboutObjects: tortoise, "#14232", "#14261", tools/spool_drain_rehearsal.py
created: 2026-11-23
---

# Proof: the source spool was never written to

The whole operation rests on one recorded decision — **the spool is the only copy,
so a transcript is never deleted, moved, or consumed by a rehearsal**. This is the
proof for this lane, taken outside the harness so it covers the entire session
(including the manual smoke tests), not only the four harness runs.

## Method

```bash
SPOOL="${TORTOISE_CAPTURE_SPOOL_DIR:-$HOME/.tortoise/capture-spool}"

# BEFORE — taken before the first harness run (20:19 UTC)
find "$SPOOL/entries" -type f | sort | xargs sha256sum > /tmp/spool-baseline-sha.txt
find "$SPOOL/entries" -type f -print0 | sort -z | xargs -0 cat | sha256sum

# AFTER — taken after all four modes had run (21:2x UTC)
find "$SPOOL/entries" -type f | sort | xargs sha256sum > /tmp/spool-after-lane-sha.txt
find "$SPOOL/entries" -type f -print0 | sort -z | xargs -0 cat | sha256sum
diff /tmp/spool-baseline-sha.txt /tmp/spool-after-lane-sha.txt
```

## Result

| | before | after |
|---|---|---|
| files (`meta + turns`) | 530 (265 + 265) | 530 (265 + 265) |
| per-file sha256 list | — | **identical** (`diff` empty) |
| aggregate sha256 over every file, sorted | `38d03983b0c33d84d899e61dfa16da6bfb8e204d5d1332b8d0233d531766f0fe` | `38d03983b0c33d84d899e61dfa16da6bfb8e204d5d1332b8d0233d531766f0fe` |
| transcript-only digest (`*.turns.jsonl`) | — | `3e67bfeffcf07cd398fce93237df26f121c2107801a22819b4f4a765d786446f` |

**The source spool is byte-identical**, per file and in aggregate. The harness
independently reports `invariant_source_untouched: true` in all four mode JSONs
(it re-fingerprints before and after each mode and exits non-zero on a mismatch).

## Why the aggregate CAN stay identical here, and why that is not the general case

In this lane the ORIGINAL was never drained — only copies were. On a **real**
drain the per-entry `meta.json` is rewritten (`filed_key`, `attempts`,
`next_attempt_at_ms`), so the whole-tree aggregate WILL change while every
`*.turns.jsonl` stays identical. That is the distinction the runbook's Stage 4
draws: compare **transcript digests**, not the tree, when proving a real drain
lost nothing.

## The counts this corpus had (and what they mean for a drain)

- 265 entries = **264 unfiled + 1 already `filed_key`**
- 0 `terminal_failure`
- `attempts` up to **11** — carried from the production 402/429 storm, which is
  what makes a single new refusal land at the 6 h backoff cap (see the runbook's
  Stage 3b).
