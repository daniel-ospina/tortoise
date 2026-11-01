---
title: "#10463 — RE-MEASUREMENT of the S3 prior-injection ablation, with the registration already on the base branch: ratio 0.988"
type: evidence
domain: platform
doc_status: live
created: 2026-11-01
updated: 2026-11-01
subjects.team: epistemic-team
aboutSubjects: "#10463, #5087"
aboutObjects: "tools/s3_prior_ablation.py, tests/eval/write_path, s3-ablation-a83c23a8.log, PRE-REGISTRATION.md"
---

# Re-measurement record — the S3 prior-injection ablation, run a second time on purpose

**Verdict: `HYPOTHESIS-FALSIFIED` at ratio `0.9881` — not met against the pre-registered `<= 0.75` bar.**
The recorded log said `0.870`. **The two disagree by 0.118**, and the disagreement matters: `0.870` reads as
*"priors cut creates by 13 %"*, while `0.988` reads as *"no measurable effect — the counts are 253 vs 250"*.
The **qualitative** conclusion is unchanged (the hypothesis is falsified either way); the **effect size is not**,
and the number that was being quoted overstated it. Both logs are in this bundle; this note is the provenance
record the ruling on `#10463` (`comment 6564634454`) asked for.

## 1. Why a second run exists at all

The recorded measurement (`s3-ablation-a1075dfc.log`, ratio 0.870) was ruled **PROVISIONAL**: its registration
and its log landed in ONE squash commit (`cfa3de857`), so both carry epoch `1793558646` and the ordering
property — *"the registration strictly precedes the log"* — could not be shown. The ruling kept the guard
unchanged and said the conclusion stands only **once re-measured with the registration already on the base
branch**. This run is that re-measurement. It is not a re-run to obtain a friendlier number: `0.988` is a
*less* favourable result for the hypothesis being tested than `0.870` was.

## 2. Ordering provenance (the property the recorded log lacked)

| fact | value |
|---|---|
| pre-registration add-commit **on `origin/main`** | `cfa3de857ef2c283929480e40579d6128c4c297d`, epoch `1793558646` |
| registration document sha256 at that commit | `70264efe738d5ee8d1721735f92077f0cae18e813e2b1102deecc4bd3c560156` |
| same document at the commit the bars were read from (`a1075dfc2`) | `70264efe…` — **byte-identical**, so the bars are the same document, not a lookalike |
| this log's add-commit | a commit made **after** the above (the PR that carries this file) |

So the registration was already published on the base branch, immutable by hash, before this log's add-commit —
the relation the guard exists to protect, established by base ancestry rather than by sibling commits inside one
contribution.

## 3. What was run, and where

| | |
|---|---|
| run head | `a83c23a851df7ff7aca45ae6898b17a6e4433a19` (the probe's own `head=` line) |
| `--prereg` | `a1075dfc2a964a6e9e1a49ec990ebadf8c9a180a` (bars parsed out of the COMMIT, never the worktree) |
| pinned source tree | `source_sha=718ee3b4…`; **0** production files and **0** metric-definition files differ between it and the run head |
| corpus | `tests/eval/write_path`, 7 sessions, `fixtures_hash sha256:dd7e8520…` (re-derived, matches the pin) |
| graph | a private throwaway FalkorDB lane (`tools/test_lane.py`), a fresh graph per arm, non-zero baseline refused |

**Why that head and not `main`.** The registration's §6 pin refuses a head where production code moved. Since
the registration was written, `tortoise/` has moved by 24 files — **including `extractor_v2.py`, which implements
the treatment** — and the two metric-definition files (`capstone_measure.py`, `capstone_run.py`) have moved too.
The pin therefore refuses `origin/main` at exit 3 (`35 production file(s) differ …`) and that refusal is correct:
measuring today's tree would measure a different system, not the registered experiment. `a83c23a85` is a
retained commit that carries the pinned production tree **and** a probe that can witness the arms, which is what
makes a re-measurement on the registered tree possible at all.

## 4. Result, beside the recorded one

| quantity | recorded (`a1075dfc2`) | this run (`a83c23a85`) |
|---|---|---|
| `created_new_nodes` OFF | 284 | **253** |
| `created_new_nodes` ON | 247 | **250** |
| **ratio (E1, bar `<= 0.75`)** | **0.870** | **0.9881** |
| verdict | `HYPOTHESIS-FALSIFIED` | `HYPOTHESIS-FALSIFIED` |
| arm integrity (E3, need 14/14) | 14/14 | **14/14** (OFF: 0 real calls; ON: 7 real calls) |
| real facts (E2a guard, `>= 1.00×`) | 47 → 40 (fell, guard violated) | **41 → 44** (rose, guard met) |
| operator edges (E4 guard, `>= 1.00×`) | 171 → 139 (fell) | **141 → 166** (rose) |
| sessions whose creates fell (E2b, `>= 4 of 7`) | 5 of 7 | **4 of 7** |
| model calls (E5, `<= 2.00×`) | — | 21 → 27 (`1.29×`) |
| concentration (largest session's share) | — | `0.4` (below `0.60`, no qualifier) |

Per-session, this run: `33→40`, `34→40`, `41→31`, `42→36`, `42→40`, `30→39`, `31→24`. **Three sessions
increased** (by 7, 6 and 9 nodes) and four fell; per-session ratios span `0.756 … 1.300`.

**How to read the two numbers together.** They are the same verdict with different magnitudes. Both falsify the
hypothesis, so nothing about the epic's plan changes because of the re-measurement. What changes is the
*sentence a reader would write*: `0.870` supports *"priors reduce creates by about a seventh, but not enough to
clear the bar"*; `0.988` supports *"priors do not measurably change creates at all"*. The second is the honest
one for this corpus: a 3-node difference on a 253-node base is inside the session-to-session spread this run
itself shows (a single session moved by 10 nodes in either direction).

## 5. Method differences, disclosed — and the one thing that did NOT move

No bar, floor, denominator or metric definition was changed to accommodate this run. The registration's pinned
block is unchanged, and `created_new_nodes`, `real_facts`, `operator_edges` and the verdict arithmetic are
byte-identical between the two runs' probe code; what differs is **instrumentation around them** (the plate that
holds the gauge, not the gauge):

1. **The recorded run's probe bytes were never committed.** Its log names `head=a1075dfc2`, but the probe at that
   commit reads a receipt field this lane does not populate and would have reported arm integrity `0/14`
   (`ARM-INVALID`), while the log prints `7/7 + 7/7`. The probe that produced `0.870` existed only as an
   uncommitted working copy; the README's own errata record that, and this run exists partly to give the number a
   probe that a reader can check out. **This run executed committed bytes** — `git show a83c23a85:tools/s3_prior_ablation.py`
   — and both logs' `head=` lines now name commits that actually contain the probe that ran.
2. **§6's admission rule is narrower than the letter of the frozen document.** §6 says the pin allows a head
   whose differing files are "inside `docs/evidence/` or `tools/`". The probe lineage this run used admits
   `docs/`, `tests/` and `tools/`, guarding the corpus and the metric artifacts separately and more strictly.
   The **property** §6 protects holds exactly — zero production files and zero metric-definition files moved —
   but the letter is wider than §6's wording, and that is stated here rather than left for a reader to notice.
3. **`--prereg a1075dfc2` is not an ancestor of `origin/main`** (the squash replaced it). The registration-on-base
   claim rests on the **byte-identical document** at `cfa3de857` (§2), not on ancestry of the `--prereg` commit.
   Said plainly because a reader checking ancestry alone would find the opposite.
4. **This is a second run of the pair.** §4 of the registration says *"one run of the pair is the measurement; a
   second run is a new pre-registration"*. This instance is ordered by the ruling on `#10463`, and it is
   recorded as a **separate instance with the same frozen bars** — not as a silent repeat under §4. The pins are
   unchanged; a reader who wants §4 applied at its word should read this log as a second registration instance
   whose only difference is the run head and the instrument version.
5. **Filename.** The probe wrote this log under a working filename; it is committed as
   `s3-ablation-a83c23a8.log` (named for the run head, as the bundle's reproduce command does). **The bytes were
   not touched** — sha256 `796c1a49c0d2ff22266377ca58713c0e3ff14febab2a9a62aca9a27689a06174`, identical before and
   after the rename.

## 6. Reproduce

```bash
# at a83c23a85 (pinned production tree intact, probe committed there):
git checkout a83c23a85
uv run python tools/s3_prior_ablation.py \
    --prereg a1075dfc2a964a6e9e1a49ec990ebadf8c9a180a \
    --out docs/evidence/5087-hypothesis/s3-ablation-a83c23a8.log
```

A repeat at a later head is refused by design at exit 3 (production code moved) — reproducing there needs its
own registration, which is the property that makes the bars untunable.

## 7. What this does not claim

- **Not** that the preregistered pipeline order changed: no production file was touched by this lane.
- **Not** that `0.870` was dishonest: its arithmetic was right for the run that produced it. What it lacked was a
  probe a reader could check out, and an ordering that could be shown. The re-measurement supplies both and gets
  `0.988`, which is the number the epic should now quote.
- **Not** a promotion of this bundle over the withdrawn log: that file is untouched, and the withdrawal ordered by
  the ruling is carried by its own lane (`#10544`).
