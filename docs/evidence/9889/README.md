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

`#7949`'s capstone could only score E1 and E2 by parsing **one fixed artifact**
(`docs/evidence/7949-capstone/e1-e2.log`), so both rows reported `BLOCKED` /
`REFUSED` whatever a fresh run held — and `probes/capstone_measure.py` **forced
the measured arm ON**, so E1's *before* half (`after <= 0.50 x before`) could not
be produced by it at all. **That parse path is now gone:** the rows read
`e1-e2-scores.json` from this directory (see "The score record" below), so the
sentence above is the history this directory closes.

This directory holds the artifact of the three **additive** probes that close
both gaps, plus the numbers they measured. No `tortoise/` file changed.

| probe | the question it answers | falsifier |
|---|---|---|
| `probes/e1_paired_arm.py` | what is E1's **before** share on the pre-registered corpus, with the selection passes OFF? | refuses (measures nothing) if any pass switch is already set, if the backend cannot extract (no key → a measurement of a key's ABSENCE), or if the checkout's corpus is not the pre-registration's frozen one (`G1`); exit 2, writing no record, if the run's population is one E1's record refuses — a zero denominator, fewer `:Object` nodes than E1's frozen floor, or a before-share under the frozen `e1_min_before_share` |
| `probes/e1_gate_drops.py` | does the v4 value-gate predicate drop any candidate, over **every section the S2.2 pass judges** (`objects`/`events`/`points` — the Objects half is the `^the <X>` class)? (deterministic, zero model calls) | exit 2 if the positive controls stop dropping, if no selected section yields candidate text, if the graph yields no Object names, or if `--sections` names a section the instrument does not census — and a refusal writes **no** `--out` artifact, so a file left at that path after a refused run is the PREVIOUS run's |
| `probes/e2a_value_units.py` | how many of the frozen **14** value-bearing planted units STRICTLY survive? | refuses (exit 2, no record) when no searchable `TORTOISE_DB_URI` is set (the embedded fallback would read an empty graph as `0 of 14`) or when the graph holds no memory-level Points at all (an ABSENT population, not a losing pipeline); `--inject-anchor` refuses if injecting every lost unit's anchor moves the counter by nothing |

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

### Which probe revision produced each artifact

Two of the committed records are **older than the probe that ships beside them**,
and the README says so here rather than leaving a reader to diff the key sets:

* **`e1-paired-off-arm-1.json` / `-2.json`** were produced by the
  `e1_paired_arm.py` revision in **`7ea740082`** (the commit that added the probe
  and these records). The two review-fix commits after it — `76c2e1f09`
  (E1's Objects floor) and `fcc2157d9` (the corpus pin and
  `e1_min_before_share`) — taught the probe to pin four fields, so the shipped
  probe writes **13** keys and these two records carry **9**
  (`arm, graph, sessions, objects, bare_definite, share, object_names,
  label_counts, elapsed_s`). The four added fields — `head`, `fixtures_hash`,
  `object_floor`, `min_before_share` — **postdate them**.
* **`e2a-value-units-falsifier.json`** predates the `strict_all` label fix:
  its `with_anchor.per_session[*].strict_all` values are the **as-written**
  readings (the probe sourced that field from the pre-injection count on both
  passes), so the two blocks look identical in that one field. The shipped probe
  reports each block's own reading and keeps the pre-injection figure as
  `strict_all_as_written`.

They were **not re-recorded**, and the reason is this bundle's own finding: the
graphs lived in a disposable `tools/test_lane.py` container that has since been
removed (a graph is a key in the keyspace — gone with it), and a re-run is a
*new LLM realization*, not these numbers. So for these two records the head, the
corpus hash and the two floors are the **prose facts in the section above**
(`54922a800f96…`, `fixtures_hash=sha256:dd7e8520…`, `e1_objects` 30,
`e1_min_before_share` 0.20) rather than fields of the artifact. Because that is
the one operand E1's bar is a fraction of, the score record states it too: its
`before_arm_provenance` block names both artifacts, the four pin fields they
lack and the ones they carry, and why the after arm's corpus is re-verified
while theirs is not. No published
number moves: both OFF populations clear both floors (78 and 69 Objects;
0.3590 and 0.5652), and the falsifier's claim is `value_survived` 8/14 → 14/14
(recovered 6 of 6), which was the injected count in the record as committed.

A record that cannot name the revision that produced it is an artifact without
provenance — so a future record is expected to be written by the probe at the
head it claims (the shipped probe does this; these two were `git add`-ed from a
run taken before it did).

## Probe 1 — `e1_paired_arm.py`: E1's before arm

The same corpus, the same entry point (`capstone_measure.run_corpus`), the same
census (`capstone_measure.census`), with `TORTOISE_VET`,
`TORTOISE_CLASSIFY_LATER` and `TORTOISE_VALUE_GATE` OFF. The arm is **read back
from `os.environ`** and printed, never typed as a literal.

⚠️ **`TORTOISE_VALUE_GATE` OFF means `=0`, not "unset" — since `#10996`.** The
S2.2 mechanical half defaults **ON** (`tortoise/value_gate.py`), so an ABSENT
value gate is the *treatment* arm; a control produced by unsetting it would be
the treatment under the control's label. `e1_paired_arm.off_arm_readback()`
therefore PINS the value gate to `0` and reads it back (a switch the shell
EXPLICITLY armed is still refused), so the OFF arm is run with
`TORTOISE_VALUE_GATE=0`. That reproduces the **intent** of
`PRE-REGISTRATION.md`'s CONTROL arm ("the selection flags unset … their default
is OFF"); the frozen record's *wording* predates the flip and is left unedited —
amending a pre-registration is its owner's call, not a probe's.

**And the SCORER enforces the same rule on the records, not just the probes.**
`probes/e1_e2_scores.py` refuses a record that expresses this switch's OFF state
by **absence** — `null` in a before arm's `arm` block, `<unset>` on the ON log's
`arm:` line — on both sides of E1's pair, because `_is_truthy(None)` is False and
that is exactly what certifies a gate-armed run as a control. The one exception
is the records committed HERE: they predate the flip, so absence really was OFF
when they were taken, and they are admitted only under the pre-`#10996` campaign
head (`54922a800…`) — the licence is that head, so a record from any other head
with the switch absent is refused, and stripping the pin fields to look old is
not enough on its own. What the licence was used for travels with the numbers:
`e1-e2-scores.json` carries a `value_gate_pin` block naming every arm read as
`legacy-absent`. A re-take of E1's arms with `TORTOISE_VALUE_GATE=0` written and
read back is what closes the residual (`#7949`), and it cannot be done from the
repo: these graphs lived in a disposable container that no longer exists.

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
run could otherwise hand a reader.

**Both halves of E1's bar are applied, and the record says which population it
was taken on.** E1 is stored as a threshold AND the corpus it was argued over
(#9076), so before any model call the probe runs the shipped `G1`
(`capstone_measure.check_corpus`) against the pre-registration's
`corpus.fixtures_hash` — a checkout whose corpus is not the frozen one is
refused, never measured — and the record carries the `fixtures_hash` this run's
corpus actually had plus the `head` it was taken at. E1 has a SECOND
registered refusal, on the number itself rather than its denominator:
`denominator_floors.e1_min_before_share: 0.20` — the criterion says a control
arm under that share "cannot measure E1 (a share metric needs a non-trivial
base)" and is **REFUSED**. A before-share under 0.20 therefore exits **2** and
writes no record, citing the frozen floor. Both floors are read out of the
record (`e1_objects` through `parse_bars`'s `floors` map, `e1_min_before_share`
straight off `denominator_floors` — `parse_bars` exposes the share floor too, but
this probe is the PRODUCER of the number that floor is written over and refuses
with its own message, because an escaping `ValueError` from a helper is not a
refusal a run can be read from), never typed here, and
an absent or unreadable `e1_min_before_share` is refused rather than read as "no
floor" (#9513). Both arms below clear both floors (0.3590 and 0.5652 against
0.20; 78 and 69 Objects against 30), so their shares are measurements.

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
the S2.2 pass applies (`tortoise.value_gate.mechanical_verdict`) to the candidate
text the run wrote — zero model calls, so no LLM non-determinism.

```bash
uv run python docs/evidence/7949-capstone/probes/e1_gate_drops.py \
    --graph capstone_on_54922a800_1793378318 --out e1-gate-drops.json
```

⚠️ **That artifact covers `:Object` names only, and that is a known gap — not a
pass condition** (`#11201`). The pass judges three sections (`entities`,
`events`, `points`: `vet_gate.SECTIONS`), and `#10996`'s Step-4 condition is
written over **points+events**. The probe now takes `--sections`
(`objects,events,points` — all three sections the pass judges, and that is its
DEFAULT), records per-section
candidate counts and drops, and marks a section that yielded no candidates
`measured: false` / `coverage_complete: false` instead of printing a zero over
it:

```bash
uv run python docs/evidence/7949-capstone/probes/e1_gate_drops.py \
    --graph capstone_on_<head8>_<epoch> --sections objects,points,events \
    --out e1-gate-drops-sections.json
```

The ON-arm graph is ephemeral and its container is gone, so **the points+events
half of the pass condition is still UNMEASURED** — re-take it before treating
Step 4 as evaluated. ⛔ **Mind the arm:** `apply_vet` REMOVES what the predicate
discards before the payload is written, so a graph from a run with
`TORTOISE_VALUE_GATE` ON contains only SURVIVORS and any `0` read off it is
vacuous. The measurement needs a graph written by a run that pinned the
predicate **OFF** (`TORTOISE_VALUE_GATE=0` — since `#10996` "unset" is the ON
arm), which is what `capstone_measure.measured_arm()` does; the probe's record
carries that precondition as `arm_precondition`. Its `graph` names the run, and
the authoritative witness is that run's `# arm:` readback in its own log
(`capstone_measure.arm_header`), not the record — the probe cannot see the log.
Why the gap matters, measured with no
DB at all: an identifier-only *claim* is discarded and takes the entity only it
named with it (Rule 2 has no survivor left to reference), so one Discard removes
two nodes while an Objects-only census shows one
(`tests/test_9889_capstone_probes.py::
test_the_mechanical_predicate_drops_a_claim_and_its_entity_together`).

Measured on the ON arm's graph (matches that run's own census exactly — 61
Objects, 13 bare-definite, 0.2131) — ⛔ **and taken at head `54922a800`, which
PREDATES the `#10996` flip**: `on-arm.log:8` reads
`TORTOISE_VALUE_GATE=<unset>`, and before the flip absence really was OFF, so
that population is a gate-OFF population and the zero below is a measurement
rather than the vacuous kind:

```text
objects=61 bare_definite=13 share=0.21311475409836064
value_gate drops: 0 (among bare_definite: 0) controls alive=True
```

(That two-line block is the PRE-PR instrument's output, kept as the record of
what was measured. The shipped probe also prints the per-section line —
`objects=61(drops=0) events=… points=…`, with `UNMEASURED` for a section that
yielded no candidates — and a `STEP-4 (points+events) drops: …` line; it does not
re-print this artifact's numbers because the artifact predates those sections.)

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

**An absent population is refused, not scored — including by the falsifier.**
Two preconditions are checked before any number is emitted: with no
`TORTOISE_DB_URI` the SDK silently resolves the EMPTY embedded graph, and a
`--graph` name that was never written (a typo, a reaped graph) reads empty rather
than raising, so both produce the same confident `0 of 14` a real E2a regression
would. The probe therefore refuses (exit **2**, no record) when no searchable
backend is configured and when `sum(per_session[*].memory_points) == 0` — #9889
acceptance item 2's zero-denominator rule applied to E2a's population. Without
that check the falsifier CERTIFIES the failure: on an empty graph `before` is
`0/14`, the injection recovers all 14 synthetic anchors, and the probe prints
`recovered 14 of lost 14` green beside a number that measured nothing.

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
4. **The E1/E2 rows in the verdict table USED to parse `e1-e2.log`; no row the
   builder emits does.** This was written as an open limitation when the probes landed
   (#10342); the re-point followed and is landed
   (`docs/evidence/7949-capstone/probes/capstone_measure.py` cites
   `E1_E2_SCORES` for both rows, and `E1E2_LOG` no longer exists there). Kept as
   history because the reason the re-point was staged separately still holds: it
   is not additive, it changes the row contract, and a second scored log in the
   bundle would red every number the README quotes.
5. **The 2026-10-24 pass's numbers are not reproduced.** They were taken at
   `5f40d832c`; this head is ~28 commits later. That is the difference reported
   above, not a contradiction.

## Files

| file | what it is |
|---|---|
| `on-arm.log` | the measured-arm run's own log (`capstone_measure.py`, `# generated: 2026-10-30T16:48:36Z`, head `54922a800`). **Not** named `capstone-measured-*.log` on purpose: that glob is the bundle's "shipped log" and a second one reds its README pins. |
| `e1-paired-off-arm-1.json` | `e1_paired_arm.py` record, OFF run 1 — produced by the probe at `7ea740082`, so it predates the four pin fields the shipped probe writes (see "Which probe revision produced each artifact") |
| `e1-paired-off-arm-2.json` | `e1_paired_arm.py` record, OFF run 2 — same revision, same caveat |
| `e1-gate-drops.json` | `e1_gate_drops.py` record (every Object name, its verdict, the controls) |
| `e2a-value-units.json` | `e2a_value_units.py` record, as written (per-session ids, survived and lost) |
| `e2a-value-units-falsifier.json` | the same with `--inject-anchor` — both readings in one record. Predates the `strict_all` label fix: its `with_anchor.strict_all` carries the as-written reading |
| `e1-e2-scores.json` | **the score record** — E1 and E2 as the capstone's table rows read them, in one invocation of `probes/e1_e2_scores.py` (below) |

## The score record — how E1 and E2 become rows (`#9889` item 5)

Until this record existed, `probes/capstone_measure.py` could only score E1/E2 by
parsing `docs/evidence/7949-capstone/e1-e2.log` — a 25-Object, one-arm artifact
on a corpus that is not the pre-registration's. Both rows were therefore
`BLOCKED`/`REFUSED` whatever a fresh run held. This record is the piece that
makes the rows *these probes'*:

```bash
uv run python docs/evidence/7949-capstone/probes/e1_e2_scores.py \
    --out docs/evidence/9889/e1-e2-scores.json
```

```text
E1 REFUSED decidable=False (before spread 20.6 pp)
   before 0.3590 -> bound 0.1795 vs after 0.2131 = FAIL (ratio 0.5937)
   before 0.5652 -> bound 0.2826 vs after 0.2131 = PASS (ratio 0.3770)
E2 FAIL — E2a 8/14 (bar >= 13), E2b fused 1/114 = 0.008772
```

It measures nothing itself: every number is read out of a record above, and every
BAR out of the pre-registration — `capstone_measure.parse_bars`, the bundle's ONE
reader, so this record and the row that cites it cannot read two thresholds. It
**refuses** (exit 2, no record) when an input record is missing, when a threshold
cannot be parsed out of the frozen record, when the E2a record grades a
denominator other than the frozen **14** (a `13 of 14` bar printed over an N
nobody froze), when the E2a falsifier does not reach the frozen denominator (an
inert counter), when the ON arm and the E2a counts were taken on different
GRAPHS (the ON log's own `# graph:` line — its `# head:` is equal either way, so
the head cannot catch it), when the falsifier's SCORED `with_anchor` block is
not the run the E2a record was taken on (a graph is `capstone_on_<head>_<stamp>`,
so a SIBLING run of the same head is a different run, and naming the head is not
enough), when the TWO before artifacts carry the SAME `object_names` population
(two copies of one run are one realization, not two — the identity is the
population and not the `graph` name, so a fresh graph stamp over one population is
still one run and re-ordering the list does not make a second — and `decidable` is
the SET of pairings, so
two copies of one reading can only ever agree and would report a DECIDED E1 off
the replication the design requires and never got), when a before artifact
carries no graph at all (an absent run cannot be told apart from a duplicated
one), when the falsifier's SCORED `with_anchor` block does not DECLARE the
injection it is credited for (`injected_anchor is not True` — an un-injected
reading that reaches 14/14 is the inert counter under another name), when a
share is not the fraction its OWN `bare_definite` / `objects` counts give (on
either arm — the share is the one number E1's bar is scored on, and nothing may
edit it while the counts printed beside it stand), when a `with_anchor` block is present but EMPTY (a record that never
wrote its counter is not the flat shape that has no block), when its `as_written`
block carries no graph — an absent provenance is not an inherited one — sits on another run, or reads counts other than the E2a
record's (that block IS the E2a reading), when the value-gate positive controls
are dead, when a BEFORE artifact that names a pin names one other than the
frozen `fixtures_hash` / `e1_objects` / `e1_min_before_share` (E1's bar is a
fraction of that share, so its corpus and its floors are part of the bar —
`#9076`), or
when the ON run's corpus is not the pre-registration's frozen `fixtures_hash` —
so a row can never cite a corpus the record did not freeze, a denominator the
record did not freeze, or a population from another run.

**One provenance is disclosed rather than refused, and the record says so.**
The two before artifacts predate the four pin fields the shipped
`e1_paired_arm.py` writes (see "Which probe revision produced each artifact"),
so *their* corpus is a producer-time fact — G1 — that the pair cannot re-prove,
while the after arm's corpus is re-verified from `on-arm.log` on every run. A
`required` field would delete the only before arm this bundle has (the graphs
lived in a removed container, and a re-run is a new LLM realization, not these
numbers), so the driver instead **refuses a before artifact that names a
different corpus or floor** — the case that can lie — and carries the absence
into the record as `before_arm_provenance`: `status`, `pin_fields`, and per arm
`fixtures_hash` / `pin_fields_present` / `pin_fields_absent`. That is why the
asymmetry is visible to a reader of `e1-e2-scores.json` and not only to a reader
of this file. It does not move any number — E1 is `REFUSED` at n=1, so nothing
scores green off these arms — and it becomes material at the **n >= 2** below,
where the first DECIDED E1 will be computed from before shares whose corpus the
record cannot show; re-recording the pair at the shipped probe closes it.

**E1's refusal is a measurement, not a gap.** Two independent OFF realizations
(0.3590, 0.5652) and one ON realization (0.2131) can be paired two ways, and the
two pairings give different verdicts. At n=1 per arm the verdict would depend on
which before run is chosen, so the aggregate is `REFUSED` with both pairings
printed. The next requirement is **n >= 2 per arm** — and the two realizations
have to BE two runs: the driver refuses a before pair carrying the same
`object_names` population (the `graph` name alone is not the identity — a re-stamped
copy of one population is still one run), because two copies of one OFF record pair
identically, which makes the row
decidable off a single realization — a decided `FAIL` when the duplicated arm is
the 0.3590 one, and a decided `PASS` when it is the 0.5652 one. The `PASS` is the
direction that matters: it is the one this guard exists to stop, and the one
`e1_e2_scores.py`'s own reproduction measured.

### Replicating the AFTER arm — `n >= 2 per arm`, now expressible

Until `#4917` §5 item 5 was re-read against this driver, the requirement above
was **half expressible**: the before side held a list of realizations, and
`score()` read exactly one after arm from the module constant `ON_ARM_LOG`. No
number of ON runs could therefore satisfy `n >= 2 per arm` on the side the
effect is read off — the treatment was a single LLM realization **by
construction**, and the record stated only the control's spread.

A second (and later) ON realization is now a file pair beside the scored one:

```bash
# the SCORED run stays on-arm.log; extra realizations are on-arm-<n>.log,
# contiguous from 2, each beside its own census witness.
uv run python docs/evidence/7949-capstone/probes/capstone_measure.py \
    --graph capstone_on_<head8>_<epoch> --out on-arm-2.log
uv run python docs/evidence/7949-capstone/probes/e1_gate_drops.py \
    --graph capstone_on_<head8>_<epoch> --out e1-gate-drops-2.json
```

`e1_pairings` scores the **full (before x after) cross product**, and the row is
decided only when EVERY pairing agrees — a direction that can only make E1
harder to green, never easier. The record carries `after_arms` (each
realization's artifact, graph, share and census) and `after_spread_pp`, the half
of the confound nothing counted before.

Every cheap way to *appear* replicated is refused: an extra realization with no
`e1-gate-drops-<n>.json`; one under E1's frozen 30-`Object` floor; one measured
at another head (a bar is a threshold AND its corpus, `#9076`); a shared `graph`;
and one carrying another arm's `object_names` — the identity is the POPULATION,
not the graph name or its stamp. A gap in the numbering is refused too, because
the one thing a DELETED realization hides is the disagreement that keeps E1 from
being decided.

⚠️ **What this does NOT claim.** Replication makes the requirement expressible
and the treatment side's spread measurable; it does **not** decide this pair,
and the record says so in its own `why`. The disagreement here is on the BEFORE
side (20.6 pp), and no number of after realizations can resolve a disagreement it
is not a party to. What decides a mixed pair is a tighter control arm or the
controlled single-extraction design `#9076` built (`assert_one_gate_population`,
`probes/e1_e2_probe.py`) — the instrument this bundle's capstone README names as
the next step when `n >= 2` keeps disagreeing.

**The counts reach the rows.** The capstone's E2 row now carries E2a's count over
the FROZEN 14 (`8 of 14` today; the bar is `>= 13 of 14`), and E1's carries the
after arm's `Object` count in the PASS direction — the Objects that are NOT
bare-definite, over all of them (`48 of 61`; the measured bare-definite count the
criterion minimises is `13 of 61` and stays in the row's note, because a run whose
bare-definite count is 0 is the BEST outcome and a `0` numerator is refused as a
silent drop). They are
NOT a sibling pair any more: `tools/capstone_verdict_check.py`'s V8 compares two
copies of ONE population, and these two rows score two (all Objects vs the 14
value-bearing units) — naming them siblings is what forced both counts empty, and
an empty count on the row a `GREEN` would carry is refused by V5, so the first run
to reach 13/14 produced a table the gate exited 1 on.

⚠️ **Provenance of these records is stated once**, in §"Which probe revision
produced each artifact" above: the probe revision that produced the two OFF
records, the four pin fields that postdate them, the same caveat for the
falsifier record's `strict_all` label, why they were not re-recorded (the
disposable lane and its graphs are gone, and a re-run is a new realization — it
would need a fresh FalkorDB lane plus a model key, and it would not reproduce
these numbers), and why no published number moves.

The probes themselves live beside the instruments they reuse, in
`docs/evidence/7949-capstone/probes/`; their falsifiers are pinned in
`tests/test_9889_capstone_probes.py`.
