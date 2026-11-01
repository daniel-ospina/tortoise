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

**Verdict: `HYPOTHESIS-FALSIFIED` at ratio `0.9881` — E1 not met against the pre-registered `<= 0.75` bar.**
The recorded log said `0.870`. **The two disagree by 0.118**, and the disagreement matters: `0.870` reads as
*"priors cut creates by about a seventh"*, while `0.988` reads as *"no measurable effect — 253 vs 250 nodes"*.
The **qualitative** conclusion is unchanged (falsified either way). The **magnitude** is not, and `0.870`
overstated it.

⚠️ **Read this before quoting `0.988`.** The same pinned tree, corpus and 7 sessions produced an **untreated**
baseline of `284` in the recorded run and `253` here — a **31-node (10.9 %) drift in the arm that received no
treatment**, about **ten times** the 3-node ON−OFF difference (`31` vs `3` nodes; `10.9 %` of the recorded run's
untreated arm and `1.2 %` of this run's, each against its own untreated-arm denominator). This instrument cannot resolve an effect below
about 11 % between runs, so the defensible sentence is *"no measurable effect at ~0.99, well inside the ~11 %
between-run drift this instrument shows on its untreated arm"* — **not** *"priors cut creates by 1 %"*. If you
need one number, quote the pair (`253 / 250`) beside the drift, or quote `0.870` and `0.988` together as the
range this corpus supports. The four-decimal ratio `0.9881` is an arithmetic artefact of a 3-node difference
(`250 / 253`), not a measurement that fine.

## 1. Why a second run exists at all

The recorded measurement (`s3-ablation-a1075dfc.log`, ratio 0.870) was ruled **PROVISIONAL** — the ruling is the
`gh issue view 10463 --comments` record whose id is `comment 6564634454`: its registration
and its log landed in ONE squash commit (`cfa3de857`), so both carry epoch `1793558646` and the ordering
property — *"the registration strictly precedes the log"* — could not be shown. The ruling kept the guard
unchanged and said the conclusion stands only **once re-measured with the registration already on the base
branch**. This run is that re-measurement. It is not a re-run to obtain a friendlier number: `0.988` is a
*less* favourable result for the hypothesis than `0.870` was.

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
| corpus | `tests/eval/write_path`, 7 sessions, `fixtures_hash sha256:dd7e8520…` (re-derived by the probe and matched to the pin) |
| graph | a private throwaway FalkorDB lane (`tools/test_lane.py down` after the run), one graph per arm, named `s3ablate_a83c23a8_<epoch>_off` / `_on` — distinct from every other run's graphs. ⚠️ **The probe at this head has no baseline assertion** (`assert_fresh_graph` landed at `2e1d5d730`, **after** this run head — `git rev-list --count a83c23a85..2e1d5d730` = 32 commits, of which 9 touch the probe — and `git show a83c23a85:tools/s3_prior_ablation.py | grep -c assert_fresh_graph` = 0), so freshness here is *by construction of the graph name and the lane*, and the log carries no baseline line and could not. The bundle's `README.md` item 11 records the same limitation for this instrument generation. |
| witness | the arms' S3 state is asserted from the value `search_graph` **actually returned** per session, not from the run receipt — see §5 item 3 |
| baseline | none could be asserted at this head (see the graph row); the log prints no baseline line |

**Why that head and not `main`.** The registration's §6 pin refuses a head where production code moved. Since
the **`--prereg` commit `a1075dfc2` / the pinned `source_sha` `718ee3b4`**, `tortoise/` has moved by 24 files —
**including `extractor_v2.py`, which implements the treatment** — and both metric-definition files
(`capstone_measure.py`, `capstone_run.py`) have moved too. (Measured against `cfa3de857` instead, the count is 4
files and `extractor_v2.py` is *not* among them — the 24-file figure is against the pinned tree, which is the
baseline the pin actually uses.) The pin therefore refuses `origin/main` at exit 3 (`35 production file(s)
differ …`), and that refusal is correct: measuring today's tree measures a different system, not the registered
experiment. `a83c23a85` is a retained commit that carries the pinned production tree **and** a probe that can
witness the arms, which is what makes a re-measurement on the registered tree possible at all.

## 4. Result, beside the recorded one

| quantity | recorded (`a1075dfc2`) | this run (`a83c23a85`) |
|---|---|---|
| `created_new_nodes` OFF | 284 | **253** |
| `created_new_nodes` ON | 247 | **250** |
| **ratio (E1, bar `<= 0.75`)** | **0.870** | **0.9881** |
| verdict | `HYPOTHESIS-FALSIFIED` | `HYPOTHESIS-FALSIFIED` |
| arm integrity (E3, need 14/14) | 14/14 | **14/14** (OFF: 0 real calls; ON: 7 real calls) |
| real facts (E2a guard) | 47 → 40 (fell, guard violated) | **41 → 44** (rose, guard met) |
| operator edges (E4 guard) | 171 → 139 (fell) | **141 → 166** (rose) |
| sessions whose creates fell (E2b) | 5 of 7 | **4 of 7** |
| model calls (E5, `<= 2.00×`) | 21 → 27 (`1.29×`) | 21 → 27 (`1.29×`) |
| concentration (largest session's share) | `0.395` | `0.4` (both below `0.60`, no qualifier) |

Per-session, this run: `33→40`, `34→40`, `41→31`, `42→36`, `42→40`, `30→39`, `31→24`. Per-session ratios span
`0.756 … 1.300`.

**Session 1 is blind in both arms** — the pre-registration says so in §0 and §4 (*"Session 1 is blind in BOTH
arms (fresh graph, nothing exists); that is stated here so its near-zero delta is not read as a treatment effect
after the fact"*). `wp01_quarry_debug` is session 1, and it moved `33 → 40` (**+7**) with no prior content
available to either arm. **That +7 is noise by construction**, and it is the clearest single datum on this run's
noise floor: the treatment provably could not act, yet the counts moved by 7. So the honest count of sessions
that increased is **two** (`wp02` +6, `wp06` +9), not three.

**The noise floor is bigger than the effect, measured two ways.** (i) *Between runs, untreated arm, same
session:* `wp02` created `51` in the recorded run and `34` here — **17 nodes apart** — with `wp01` and `wp06`
12 nodes apart (`45→33`, `42→30`). (ii) *Within a run, no treatment possible:* session 1 moved `+7`. Against
that, the largest within-run ON−OFF delta in either run is `15` nodes (`wp02`, recorded run), and the whole-run
ON−OFF difference here is `3`. **The re-measurement's 3-node difference is smaller than the drift on the arm
that received nothing** — which is why the drift, not the ratio, is the headline.

**How to read the two numbers together.** Same verdict, different magnitudes. Both falsify the hypothesis, so
nothing in the epic's plan changes because of the re-measurement. What changes is the sentence a reader would
write: `0.870` supports *"priors reduce creates by about a seventh, but not enough to clear the bar"*; `0.988`
supports *"priors do not measurably change creates at all"*. The second is the one this corpus supports — but
**only at the resolution in the warning at the top of this file**: the 31-node between-run drift on the untreated
arm is larger than every per-session change **in nodes** in both runs, and about ten times the 3-node difference
between the two arms here. (Per-session changes measured in *percent* are larger than 11 % — `wp06` `+30.0 %`,
`wp03` `−24.4 %` — which is the same point from the other side: percentages are noisy at these counts.) The
drift bounds the **between-run** comparison of two totals; it does not bound a single session's own swing.

## 5. Method differences, disclosed — and the one thing that did NOT move

No bar, floor, denominator or metric definition was changed to accommodate this run. The registration's pinned
block is unchanged. Between the probe revision at `a1075dfc2` and the one at `a83c23a85`, the **metric functions
are byte-identical** — `created_counts`, `real_facts`, `operator_edges`, `concentration` — and so is every number
they produce; the only files this run's head differs by from the pin are evidence/docs/tests/tooling.

`decide()` — the function that turns those numbers into a ratio and a verdict — is **not** byte-identical,
and saying so matters in the one paragraph whose job is to show nothing was tuned. Three hunks differ inside it,
and a fourth refusal sits beside it:

- a **session-count refusal inside `decide()`** (*"the arms replay N/M sessions; the pre-registration freezes 7"*).
  Both runs replay 7/7, so it never fires. A second, module-level `paired_sessions` helper called from `main()`
  refuses the same condition while building the per-session table;
- the **guards reformulated** from `on >= off` to `on >= guard_ratio_min * off` — algebraically identical at
  `guard_ratio_min = 1.00`, and the same outcome both ways: recorded run `40 >= 47` → False, this run
  `44 >= 41` → True — carrying the E2b `not-majority-of-sessions` qualifier in the same hunk, which neither run
  triggers (5 of 7 and 4 of 7, bar 4);
- **two payload keys added** (`corroboration_met`, `cost_met`), which add the `bar`/`met=` annotation to the printed
  `COST:` line — this log prints
  `COST: model calls OFF 21 ON 27 (ratio 1.2857142857142858; bar <= 2.0; met=True)` where the recorded log prints
  the ratio alone — and feed a `SESSIONS WHOSE CREATES FELL:` line that the recorded probe did not print at all.

**No measured quantity differs between the two runs' code**: the counts, ratio, real facts, operator edges, cost and
concentration are produced by byte-identical functions, and the two logs' numbers are compared line by line in §4.
What differs is refusals that never fire, and reporting that annotates data both logs already carry.

What differs beyond that is **instrumentation around those functions**, plus seven deviations stated here rather
than left for a reader to find:

1. **The recorded run's probe bytes were never committed, and this run's probe cannot prove it was the bytes
   that ran.** The recorded log names `head=a1075dfc2`, but the probe at that commit reads a receipt field this
   lane never populates and would have reported arm integrity `0/14` (`ARM-INVALID`), while the log prints
   `7/7 + 7/7`. So the probe that produced `0.870` existed only as an uncommitted working copy — the bundle's
   `README.md` item 7 records exactly that. For **this** run the situation is better but not proved: the
   committed log's line set matches `git show a83c23a85:tools/s3_prior_ablation.py` in every respect (every
   format string present, and no `tree=` / `probe_sha256=` / baseline line, which that revision cannot print),
   but **that revision predates `probe_sha256=` and `assert_clean_tree`**, so it cannot *certify* the bytes it
   executed. Claim made here: *the log is consistent with the committed probe at this head* — not *the log
   proves it*. The first instrument generation that can certify its own bytes is later than both runs.
2. **§6's admission rule is narrower than the letter of the frozen document.** §6 says the pin allows a head
   whose differing files are "inside `docs/evidence/` or `tools/`". The probe at this run head admits files by
   prefix — `docs/`, `tools/`, `tests/` — and re-derives the corpus's `fixtures_hash`; it does **not** carry the
   later `METRIC_ARTIFACT_PREFIXES` guard (`grep -c METRIC_ARTIFACT_PREFIXES` = 0 at `a83c23a85`; that pin was
   added by `7aaee0875`, which is not an ancestor of this head). The **property** §6 protects still holds for
   this run, but it holds **by measurement, not by guard**: the seven files the log lists as differing between
   `source_sha` and the run head contain **no** metric artifact, and `capstone_measure.py` /
   `capstone_run.py` / `tests/eval/write_path` are byte-identical to the pinned tree. Stated this way because the
   stronger claim — that this head *enforced* a metric-definition pin — would be false.
3. **The E3 witness is the S3 function's return value, not the run receipt.** The log says this in its own
   `S3 WITNESS:` line; it is a disclosed deviation from the pre-registration's wording *"asserted from the run
   receipt"*, because the `capture_session` lane surfaces no `search` block at all. Repeated here because this
   note is the document the ruling asked for.
4. **§3 rule 5's verdict text is not printed in full.** The registration fixes the form
   `HYPOTHESIS-FALSIFIED (ratio X)`; the log prints `RATIO: 0.9881` and `VERDICT: HYPOTHESIS-FALSIFIED` on
   separate lines rather than folding the ratio into the verdict token. The failure branch that sets the frozen
   text is guard-violated only at this head (the bundle's `README.md` item 12 records the same deviation), so
   the ratio and the verdict agree but are not in one string.
5. **`--prereg a1075dfc2` is not an ancestor of `origin/main`** (the squash replaced it). The registration-on-base
   claim rests on the **byte-identical document** at `cfa3de857` (§2), not on ancestry of the `--prereg` commit.
   Said plainly because a reader checking ancestry alone would find the opposite.
6. **This is a second run of the pair.** §4 of the registration says *"one run of the pair is the measurement; a
   second run is a new pre-registration"*. This instance is ordered by the ruling on `#10463`, and it is
   recorded as a **separate instance with the same frozen bars** — not as a silent repeat under §4. The pins are
   unchanged; a reader who wants §4 applied at its word should read this log as a second registration instance
   whose only differences are the run head and the instrument version.
7. **Filename.** The probe wrote this log to the path given to `--out`, which was a working filename
   (`s3-ablation-88b4e19d9.log` — a run-time token, not a commit: it resolves to no object here); it is committed as
   `s3-ablation-a83c23a8.log`, named for the run head as the bundle's reproduce command names it. **No filename for
   this log appears inside it** — the only paths it names are the seven in its `source_sha..head changed files` line
   (line 5), none of which is this log — so nothing in it is inconsistent with the name it is committed under.
   **But the pre-rename bytes were not recorded, so the rename is disclosed, not verifiable.** The committed blob's
   sha256 is `796c1a49c0d2ff22266377ca58713c0e3ff14febab2a9a62aca9a27689a06174`.

## 6. Reproduce

```bash
# at a83c23a85 (pinned production tree intact, witness-capable probe committed there):
git checkout a83c23a85
uv run python tools/s3_prior_ablation.py \
    --prereg a1075dfc2a964a6e9e1a49ec990ebadf8c9a180a \
    --out docs/evidence/5087-hypothesis/s3-ablation-a83c23a8.log
```

A repeat at a later head is refused by design at exit 3 (production code moved) — reproducing there needs its
own registration, which is the property that makes the bars untunable. A repeat at the **same** head is a new
draw, and the two runs already in this bundle differ by 10.9 % on the untreated arm: treat any single draw as
one sample, not as the effect size.

## 7. What this does not claim

- **Not** that the preregistered pipeline order changed: no production file was touched by this lane.
- **Not** that `0.870` was dishonest: its arithmetic was right for the run that produced it. What it lacked was a
  probe a reader could check out, and an ordering that could be shown. The re-measurement supplies the ordering,
  comes within reach of the probe question, and gets `0.988` — which is the number to quote **with the drift**,
  not instead of it.
- **Not** that this run supersedes the recorded one as *the* measurement. Two draws on one corpus, 10.9 % apart
  on the untreated arm, agree on the verdict and disagree on the magnitude. That disagreement is the finding.
- **Not** a promotion of this bundle over the withdrawn log: that file is untouched, and the withdrawal ordered by
  the ruling is carried by its own lane (`#10544`).
