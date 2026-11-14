---
title: "#9025 — the journal's bound, priced from measured bytes (evidence)"
type: evidence
domain: platform
doc_status: final
created: 2026-11-13
subjects.team: epistemic-team
aboutSubjects: Tortoise journal
aboutObjects: "#9025, #5612, #11821, docs/architecture/STORAGE-ARCHITECTURE.md §5.2, tortoise/log.py EventLog, tortoise/journal_segments.py"
---

# #9025 — the bound, priced from measured bytes

**Measured at:** `feat/9025-journal-bound` off `origin/main` `c2e579e629`,
2026-11-13, with `tools/journal_growth.py` — the instrument `#5612` and `#11821`
both used — over a journal built by the **landed writer** (`EventLog.append` +
`stamp_journal_embedding`).

**What this adds.** `#5612` priced the journal per *record* (a creating record is
9,176 B pre-`#11821`) and per *create*. `#9025` needs a bound priced per **year**,
and a year is made of **captures** — where, on the record-armed lane, one capture
journals **ONE `TurnsRecorded` carrier** holding every turn's snapshot (`#10017`
R2). That record class had never been measured, so none of the three options
(keep / compact / archive) could be priced. The decision note is
[`#9025`'s comment `6867080064`](https://github.com/daniel-ospina/tortoise/issues/9025#issuecomment-6867080064);
this bundle is the measurement behind it.

## Run it

```console
$ env -u TORTOISE_DB_URI uv run python \
    docs/evidence/9025-journal-bound/probes/journal_bound_probe.py
```

`measurement.txt` beside this file is one run's output.

## The measurement (post-`#11821` P0 — the LANDED writer)

| what | bytes on disk |
|---|---|
| one turn — snapshot **plus its embedding** | **6,153 B** |
| — of which the vector alone | **4,689 B (76 %)** |
| — marginal cost of each extra turn | **6,097 B** |
| one extracted point (`PointAdded`, with vector) | **5,418 B** |
| the per-capture fixed tail (25 non-vector record classes) | **5,003 B** |
| **a small capture** (20 turns, 3 points) | **142,567 B** |
| **a typical capture** (98 turns, 8 points) | **642,952 B** |
| **a heavy capture** (98 turns, 25 points) | **734,269 B** |

**Three years**, at the repo's own write rate (`docs/drafts/2026-08-09-economics-guardrails.md`
§F3 records the power user at 390 captures/month; the light user's **~100/mo** is the
same document's §4 — the only light-user rate it states):

| who · capture size | per month | 1 year | 3 years | $/month at month 36 ($0.125/GB) |
|---|---|---|---|---|
| light · typical | 64.3 MB | 772 MB | 2.31 GB | $0.29 |
| power · typical | 250.8 MB | 3.01 GB | 9.03 GB | $1.13 |
| power · heavy | 286.4 MB | 3.44 GB | 10.31 GB | $1.29 |

A thousand power users: **9.03 TB, $1,128/month** at month 36 — 19.8 % of those
users' whole storage budget (`1,000 × 30 % × $19 = $5,700`). ⚠️ All figures are
**decimal GB (10⁹)**, the same unit as the byte columns above.

**The trigger.** Solo's entire storage budget is `30 % × $19 = $5.70/month`. The
journal line *alone* reaches it for one power user at **month 182** (15.2 years),
or **month 321** once `#11821` P2's base64 payload lands. ⚠️ **That 1.7–1.8× is per
capture size, not a single multiplier**: measured 1.73× (small), 1.76× (typical),
1.78× (heavy), and the 321 figure is the typical one. P2 itself is unlanded, so
this is arithmetic on two measured endpoints.

## The two limits, stated rather than footnoted

1. **Turn text is MODELLED at 1,000 characters.** At 4,500 — what the p50
   session's 111k tokens over ~98 turns implies — a turn is **9,636 B** instead of
   6,153 B, so the capture figures are **floor-to-mid**, not ceilings. The probe
   prints both, so the knob's weight is visible: the vector is 76 % of a turn either way.
2. **The P2 column is arithmetic, not a build.** It subtracts the measured
   per-vector delta (4,689 → 2,064 B, `#12469`'s measured base64 frame) from the
   measured post-P0 record. `#12469` is a design note; no P2 writer exists.

## What the probe does NOT do

It writes only into `tempfile.mkdtemp()`. It touches no configured journal, and it
rotates, compacts, archives and deletes nothing.

## Why the measurement is believable: it reproduces the landed pin

The probe re-stamps the corpus' richest `PointAdded` through
`stamp_journal_embedding`, appends it **through `EventLog.append`** (the same
`write_journal` path the rest of the probe uses, not a local copy of its
serialization), and **exits 2** unless it reproduces the published **5,423 B**
`#11821` P0 pin. This is not decoration: the first run of this probe skipped the
seam, wrote the vector at float64's 17-digit repr, and measured **8,525 B** — a
number no deployment stores, and one that would have re-baselined every
projection built on it. The pin check is what caught it (folded into `MEMORY.md`'s
`[pin-defensive-claim]` row, which is where pin-quality lessons live). The append
is what makes it catch the *other*
class as well: the pin used to be hand-serialized with `json.dumps(...)` +
`"\n"`, so a serialization change inside `tortoise/log.py` moved every figure
below it while the pin stayed green — the quiet re-baseline it exists to
prevent. Measured: switching `log.py`'s writer to default separators moves the
pin to **5,860 B** and the probe exits 2.

## The mechanism this bundle sits beside

`tortoise/journal_segments.py` — read-only discovery, a segment manifest with a
recorded boundary, a rotation **planner**, and the refusal that makes a
reconstruction claim honest (`assert_full_reconstruction` raises rather than
returning a falsy value). Its writers — `seal_active`, `record_disposition` and
the crash-repair verb `adopt_crashed_seal` — are behind
`TORTOISE_JOURNAL_SEGMENTS` (default OFF), rename rather than delete, and with
the flag off the on-disk layout and the default write path are byte-for-byte
unchanged. The falsifier is `tests/test_journal_segments_falsifier.py`, which
removes each guard from the module's own source and shows the lie reachable when
they are all gone.
