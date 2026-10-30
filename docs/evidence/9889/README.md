---
title: "#9889 — the capstone's three E1/E2 probes, and what they measured"
type: evidence
domain: platform
doc_status: live
created: 2026-10-30
subjects.team: epistemic-team
aboutSubjects: Tortoise extractor v4, E1 (volume), E2 (correctness)
aboutObjects: "#9889, #7949, #9080, #9116, EXTRACTOR-V4-ARCHITECTURE.md §S2.2, docs/evidence/7949-capstone"
---

# `#9889` — the capstone's three E1/E2 probes, and what they measured

`#7949`'s capstone can only score E1 and E2 by parsing **one fixed artifact**
(`docs/evidence/7949-capstone/e1-e2.log`), so both rows reported `BLOCKED` /
`REFUSED` whatever a fresh run held — and `probes/capstone_measure.py` **forces the
measured arm ON**, so E1's *before* half (`after <= 0.50 x before`) could not be
produced by it at all.

This directory holds the artifact of the three **additive** probes that close
both gaps, plus the numbers they measured. No `tortoise/` file changed.

| probe | the question it answers | falsifier |
|---|---|---|
| `probes/e1_paired_arm.py` | what is E1's **before** share on the pre-registered corpus, with the selection passes OFF? | refuses (measures nothing) if any pass switch is already set, if the backend cannot extract (no key → a measurement of a key's ABSENCE), or — exit 2, writing no record — if the run wrote fewer `:Object` nodes than E1's frozen floor |
| `probes/e1_gate_drops.py` | does the v4 value-gate predicate drop **any** `^the <X>` Object? (deterministic, zero model calls) | exit 2 if the positive controls stop dropping, or if the graph yields no Object names |
| `probes/e2a_value_units.py` | how many of the frozen **14** value-bearing planted units STRICTLY survive? | `--inject-anchor` refuses (exit 2) if injecting every lost unit's anchor moves the counter by nothing |

## The tree these numbers belong to

* head: **`54922a800f96858935b58ef250da569a4896df44`** — the branch base, which
  was also `origin/main` when measured (`git fetch && git rev-parse origin/main`
  returned the same sha).
* frozen corpus: `tests/eval/write_path` fixtures,
  `fixtures_hash=sha256:dd7e85200f4809f701b9e9c11816b541acea63afd0b2472998975e2ebcb035dc`
  (7 sessions) — `G1` compared it with the pre-registration's pin and it MATCHED.
* backend: a private `tools/test_lane.py` FalkorDB container (`fdb-lane-41e45f048a`)
  on `127.0.0.1:16402`. Its disposable graphs all minted by these runs.

⚠️ **These are NOT the numbers in the issue body.** They were re-measured, not
carried; the differences are stated in "Against the issue's table" below.

## Probe 1 — `e1_paired_arm.py`: E1's before arm

The same corpus, the same entry point (`capstone_measure.run_corpus`), the same
census (`capstone_measure.census`), with `TORTOISE_VET`,
`TORTOISE_CLASSIFY_LATER` and `TORTOISE_VALUE_GATE` OFF. The arm is **read back
from `os.environ`** and printed, never typed as a literal.

```bash
export TORTOISE_DB_URI="$(uv run python tools/test_lane.py uri | sed -n 's/^export TORTOISE_DB_URI=//p' | tr -d "'")"
uv run python docs/evidence/7949-capstone/probes/e1_paired_arm.py \
    --graph capstone_off_54922a800_$(date +%s) --out /tmp/off-arm.json
```

**The share is only emitted above E1's own denominator floor.** E1 scores a
share, and the frozen record floors that share's denominator at **30** `:Object`
nodes (`denominator_floors.e1_objects`; criterion E1: "all `:Object` nodes
written by the run, same arm, same commit"), which is why
`capstone_measure.e1_outcome` reports `below_floor=True` → `REFUSED` instead of a
verdict. This probe produces the number that bar is written over, so it reads
the same floor out of the same pre-registration and applies it: under 30 Objects
(and at zero, #9889 acceptance item 2) it exits **2** and writes **no record** —
a below-floor before-share is precisely the decidable-looking pair an undecidable
run could otherwise hand a reader. Both arms below cleared the floor (78 and 69),
so their shares are measurements.

Measured (two independent OFF runs, because a single realization cannot show
whether a difference is the arm or the model):

| run | `:Object` nodes | `^the <X>` | share | graph | sessions failed | elapsed |
|---|---|---|---|---|---|---|
| OFF 1 | 78 | 28 | **0.3590** | `capstone_off_54922a800_1793378986` | none | 247 s |
| OFF 2 | 69 | 39 | **0.5652** | `capstone_off_54922a800_1793379347` | none | 222 s |

The measured (ON) arm for the same head is the capstone's own run, quoted from
`on-arm.log` (this file, bottom): `e1-aux objects_in_graph=61 bare_definite=13
share=0.21311475409836064`.

**The paired E1 comparison at `54922a800` — and the finding is that it does not
decide:**

```text
before (OFF 1) = 0.3590   bound = 0.50 x 0.3590 = 0.1795   after 0.2131 > bound  -> MISS (ratio 0.5937)
before (OFF 2) = 0.5652   bound = 0.50 x 0.5652 = 0.2826   after 0.2131 < bound  -> PASS (ratio 0.3770)
```

E1's verdict at this head **flips with the OFF realization it is paired against**:
RED on the first (at most **10** of 61 Objects may be `^the <X>`; this run has
**13**), GREEN on the second (at most 17 may be). The two OFF runs of the SAME
arm differ from each other by **20.6 pp** — wider than the 14.6 pp the ON arm
moved away from OFF 1 — so at n=1 per arm the metric is **not decidable**, which
is exactly the confound `capstone_measure.py`'s one-arm note warns about, now
measured instead of asserted. `capstone_measure.py` could not say any of this;
that is the gap this probe closes, and the next requirement it exposes is
n >= 2 **per arm**, not just a second arm. E2a below carries the same caveat.

## Probe 2 — `e1_gate_drops.py`: E1's mechanism, deterministically

`capstone_measure.py` can report a *share*; a share cannot say whether the v4
selection path touched the class at all. This probe applies the exact predicate
the S2.2 pass applies (`tortoise.value_gate.mechanical_verdict`) to the Object
names the run wrote — zero model calls, so no LLM non-determinism.

```bash
uv run python docs/evidence/7949-capstone/probes/e1_gate_drops.py \
    --graph capstone_on_54922a800_1793378318 --out e1-gate-drops.json
```

Measured on the ON arm's graph (matches that run's own census exactly — 61
Objects, 13 bare-definite, 0.2131):

```text
objects=61 bare_definite=13 share=0.21311475409836064
value_gate drops: 0 (among bare_definite: 0) controls alive=True
```

**0 of 61 Objects** and **0 of 13 `^the <X>`** are dropped by the mechanical
predicate. The positive controls stayed alive: `#4118` and `a/b/c.py` DO drop,
and the owner's must-keep `the admin-merge rail` does NOT. A zero that cannot be
distinguished from a dead predicate is not a measurement, which is why both
refusals are tested (`tests/test_9889_capstone_probes.py`).

⚠️ Stated limit, and it is the shipped bundle's own (`E1-E2.md` limit 6 and
`e1_e2_probe.discriminating_control`): the mechanical predicate is
**identifier-only by design**, so "0 drops" says the predicate cannot fire on a
definite description — it does **not** say the S2.2 pass has no net effect, and
it does not hold for a population that references every Object.

## Probe 3 — `e2a_value_units.py`: the frozen 14-unit denominator

E2a's denominator is derived by rule in the pre-registration (§3.3): a planted
unit is value-bearing iff its `verbatim_anchor` contains a digit, minus two named
exclusions. No committed artifact carried it. The probe computes it from the gold
(`--check-denominator` prints 14, and fails if it is not 14) and grades survival
with the runner's own instruments (`grading.strict_survival_counts` over
`runner.snapshot_session`).

```bash
uv run python docs/evidence/7949-capstone/probes/e2a_value_units.py \
    --graph capstone_on_54922a800_1793378318 --out e2a-value-units.json
# the falsifier, in the same shape:
uv run python docs/evidence/7949-capstone/probes/e2a_value_units.py \
    --graph capstone_on_54922a800_1793378318 --out e2a-value-units-falsifier.json \
    --inject-anchor
```

```text
E2a: value units 8/14 (injected=False)
E2a falsifier: as_written 8/14 -> injected 14/14 (recovered 6 of lost 6)
```

**8 of 14** value-bearing units strictly survive — **RED** against the frozen
bar of `>= 13 of 14`. The falsifier recovers **6 of 6** lost units, so the 8/14
is a property of the pipeline and not of an inert counter. Losses are per
session, not uniform: `wp04_aurora_perf` lost 3 of 3, `wp05_retro_writeup` lost 2
of 6, `wp01_quarry_debug` lost 1 of 4.

## Against the issue's table (which was measured at `5f40d832c`)

| row | issue body, `5f40d832c` | measured here, `54922a800` | same verdict? |
|---|---|---|---|
| E1 before (`^the <X>` share) | 0.2698, 0.2923 (2 OFF runs) | **0.3590, 0.5652** (2 OFF runs) | wider population AND 20.6 pp of arm-internal spread |
| E1 after | 0.5397 | **0.2131** | **the direction reversed** — the share FELL here |
| E1 verdict the bar gives | REFUTED (share rose) | **UNDECIDED** — RED against OFF 1, GREEN against OFF 2 | neither run is REFUTED; the bar is narrower than the noise |
| E1 mechanism | 0 of 63 Objects, 0 of 34 `^the <X>` | **0 of 61 Objects, 0 of 13 `^the <X>`** | same |
| E2a strictly surviving | 2 of 14 | **8 of 14** | RED either way (`>= 13`) |

Two of the three rows moved and none of the moves is a fix: E1's after-share fell
(it rose at `5f40d832c`) but the pair cannot resolve whether it fell *enough*, and
E2a improved from 2/14 to 8/14 while staying far under 13/14. What changed is
that both are now *measured* from a reproducible artifact instead of refused —
and that the probe's second run turned a number into a statement about the
metric's own precision.

## What this does NOT settle

1. **No row goes GREEN.** `tools/capstone_verdict_check.py` requires all eight
   steps witnessed for a `GREEN` row, and this run witnessed **6 of 8** (S2.2 and
   S2.3 are unobservable through `capture_session`'s receipt — `#9116`).
2. **The ON/OFF difference is not attributable to the flags alone, and n=1 is not
   enough to pretend otherwise.** The arms are separate LLM realizations of the
   same corpus; the two OFF runs above differ from each other by **20.6 pp**,
   which is larger than the 14.6 pp the ON arm moved from OFF 1. The *mechanism*
   attribution is the deterministic drop count (probe 2), not the pair; the pair
   needs n >= 2 per arm before it can carry a verdict.
3. **The ON arm here is n=1.** Two OFF runs, one ON run. A second ON run was not
   taken inside this pass, so the ON arm's own spread is unmeasured — read the
   ON share as one realization, not as the arm's value.
4. **The E1/E2 rows in the verdict table still parse `e1-e2.log`.** These probes
   make the artifacts exist; re-pointing the row builder at them is a change to
   `capstone_measure.py`'s row contract plus the README pins it carries, and it
   is left out of this PR deliberately (it is not additive, and the issue itself
   measures that a second scored log in the bundle reds every number the README
   quotes).
5. **The 2026-10-24 pass's numbers are not reproduced.** They were taken at
   `5f40d832c`; this head is ~28 commits later. That is the difference reported
   above, not a contradiction.

## Files

| file | what it is |
|---|---|
| `on-arm.log` | the measured-arm run's own log (`capstone_measure.py`, `# generated: 2026-10-30T16:48:36Z`, head `54922a800`). **Not** named `capstone-measured-*.log` on purpose: that glob is the bundle's "shipped log" and a second one reds its README pins. |
| `e1-paired-off-arm-1.json` | `e1_paired_arm.py` record, OFF run 1 |
| `e1-paired-off-arm-2.json` | `e1_paired_arm.py` record, OFF run 2 |
| `e1-gate-drops.json` | `e1_gate_drops.py` record (every Object name, its verdict, the controls) |
| `e2a-value-units.json` | `e2a_value_units.py` record, as written (per-session ids, survived and lost) |
| `e2a-value-units-falsifier.json` | the same with `--inject-anchor` — both readings in one record |

The probes themselves live beside the instruments they reuse, in
`docs/evidence/7949-capstone/probes/`; their falsifiers are pinned in
`tests/test_9889_capstone_probes.py`.
