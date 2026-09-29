---
title: "Plan — #5050: the durations map gets a measured writer and a validator on top"
type: engineering
domain: capability
doc_status: draft
created: 2026-09-28
subjects.team: organisation-design-team
aboutSubjects: ci durations map
aboutObjects: ci-surfaces.yml, ci_manifest.py, ci_timing.py, ci_selection.py
---

# Plan — #5050: the durations map gets a measured writer and a validator on top

<!--
Research path: none — the ruling is recorded on #5050 and the failure is a
measurement, not a question.
Scoping source: #5050 (owner ruling) + the landed bridge #6003 (`8b745cd62`) +
this PR's predecessor #5719 (branch `fix/5050-selection-manifest` @ `501af4b07`).
Status: this PR. The ruled direction changed the increment's shape:
#5719 built a second WRITER (`config/ci-durations-source.json` + a `sweep`
generator). The ruling makes `tools/ci_timing.py --refresh-durations` the sole
writer, so the generator is DELETED and only its validation survives.
-->

**Issue:** #5050 · **Epic:** #5215 (the durations bridge) · **Repo:** `daniel-ospina/tortoise`
**Branch:** `fix/5050-validator-on-measured` · **Supersedes:** PR #5719's `fix/5050-selection-manifest`
**Landed dependency:** #6003 (`8b745cd62`) — the bridge, already on `main`

---

## 1. The failure, measured

`config/ci-surfaces.yml:durations` is the weight vector `split_fast_gate` packs the
push halves by. The committed map said the two halves were balanced at **1.000x**.
The legs measured **1.60x** — 41.08 vs 25.65 minutes, **15.43 min of cycle time
wasted per run** — because the map's numbers were a one-off 2026-09-22 sweep that
nothing ever refreshed. Source: run `36361388386` (`push` to `main`, resolved
`origin/main` `f520a657`) as recorded in `docs/ci/merge-throughput-measurements.md`;
the imbalance is tracked by `#5050` (this parent) and `#6135`.

The map's own provenance confirmed it: `config/ci-surfaces.yml` carried **no
`durations_captured_at`** at all (verified on `origin/main @ eb2912b54`, `grep -c`
→ `0`). A map with no capture date cannot be told from a fresh one, which is
exactly why nobody noticed.

**The owner's ruling is the spec.** A measured number cannot be invented; an
invented number is indistinguishable from a correct one. Staleness is
self-revealing (a capture date); invention is not. So the measurements are the
source of truth, `--refresh-durations` is the sole writer, and a validator sits on
top: **it checks the measured map, it does not own it.**

## 2. What that demotes

| #5719 artefact | disposition | why |
|---|---|---|
| `config/ci-durations-source.json` (+7425 lines) | **DELETED** | a committed copy of what the collector now feeds directly — a second writer, and a second thing to go stale |
| `tools/ci_manifest.py::sweep` | **DELETED** | re-derives what `--refresh-durations` already derives |
| `render_rows` / `rewrite_manifest` / `write_record` / `seed_from_manifest` / `RETAINED` / `PINS` / `PROVISIONAL_WEIGHT` | **DELETED** | the projection machinery, now owned by the bridge |
| `register_provisional` + `ci_selection._register_provisional` | **DELETED** | it WROTE the record and rewrote the manifest — the ruled-out behaviour, wired into `--register` |
| `guard_reachability_issues` + `guard_inputs` + `guard-audit` + `ci_selection._tool_guard_surface` | **DELETED** | selection-manifest behaviour, not the measured map; a selector change, a new declared manifest block and a `git ls-files` subprocess inside `--integrity`, none of it about durations. The issues it named (#3362/#4115/#4186/#4658) stay open and are their own work |
| `SOURCE_PATTERNS` doc entries for the two architecture docs | **DELETED** | same: a selection behaviour change (#4658), not a validator |
| `ci_selection.fast_files_absent_from_halves` `ENV_BROKEN_FILES` subtraction | **DELETED** | a real 1-line bug fix (#4835), but it is `ci_selection`'s warning, not this map's validity |
| `.github/workflows/python-ci.yml` slow-artifact rename | **DELETED** | it existed so the `sweep` could download one slow artifact deterministically, and that `sweep` is gone. The DOWNLOAD half is genuinely collision-free — `ci-timing.yml` fetches `pytest-log-*` by **pattern** — but the UPLOAD is not: `python-ci.yml`'s `test-slow` is a `half: [a, b]` matrix and BOTH legs upload the same artifact name `pytest-log-${{ github.job }}` (= `pytest-log-test-slow`), so under `upload-artifact@v4` only one leg's log survives. The honest bound: the writer's per-file max is PARTIAL whenever a `test-slow` leg's log is dropped, which is reachable and therefore invisible to this validator. Renaming/parameterising that upload name is out of this PR's scope and tracked by issue #6263 |
| the 559-line test file | **REPLACED** | rewritten around the verdict's boundaries; the deleted surface's tests went with it |

**The test footprint, stated exactly: three test files are touched, and none for
the row-count pin.** One EXISTING file is modified — `tests/test_ci_timing.py`,
for the dated-stamp fix below. `tests/test_ci_manifest.py` is NEW (the
validator's own verdict-boundary suite), and `tests/test_ci_selection.py` gains
the `tools/ci_timing.py` carve-out regression test
(`test_ci_timing_tool_change_fails_closed_to_full`).
`tests/test_ci_timing.py` changes for one reason: `integrity_problems` now
composes the validator's freshness check, and that test rendered the refreshed
manifest with a hard-coded `2026-09-28T00:00:00Z` stamp while asserting the gate
stayed green. Against a real clock that assertion becomes a dated red 21 days
later, on a fixed future date, with no code change — the same stale-able-literal
class as the row-count pin. The test now stamps with the clock the production
caller (`--refresh-durations`) actually uses.

The row-count pin itself is **not** carried here. It was red on `main` before
**#6155** (`552e845ec` — the commit this branch is now rebased onto), and `main`
fixed it in that same commit with a key-set-identity assertion plus
a mutation proof — better than the first draft of this branch's version. This
branch takes `main`'s, and #6174 was closed as superseded.

## 3. What is here

`tools/ci_manifest.py` — the validator. **It writes nothing.** Each of the four
checks the ruling names is implemented at exactly one level, and the ones that
already exist are **composed, not re-implemented**:

| check | owner |
|---|---|
| coverage | `ci_selection.duration_coverage_issues` (the 0.90 floor) |
| disjoint keys | `ci_selection.leg_coverage_issues` — every classified file in exactly one leg. The leg arithmetic belongs to the selector that decides the legs; a validator-local copy would be a second gate free to disagree with it |
| value plausibility | **new here** — a weight the writer cannot produce |
| staleness / age | **new here** — `durations_captured_at` vs `MAX_AGE_DAYS` |

The plausibility rule is only decidable *because* the writer has a floor and a
fixed render: the bridge rewrites every **sampled** weight as
`max(measured, 0.1)` **at one decimal place**, and never emits `0.0` — `0.0` is
the declared unmeasured / minimum-weight-pin sentinel it leaves in place. So the
values that occur are exactly `0.0` ∪ one-decimal values `>= 0.1`. Anything else
— the sub-floor window `(0, 0.1)`, or a finer fraction such as a hand-typed
`0.15` — is unreachable by construction, and that is what makes it decidable
from the value alone. The precision is pinned to the writer's own output
(`test_the_writers_precision_is_the_one_this_check_assumes`), not to a constant
this module hopes stays true. An INDIVIDUAL `0.0` is allowed deliberately:
whether a given zero is an honest carry-forward or a lazy stand-in cannot be
decided from the value, which is the whole reason the map needed a writer-side
capture date. The map-LEVEL exception is that a non-empty map in which EVERY
weight is `0.0` is RED — the writer refuses a zero measured key and floors every
resolved key, so it cannot produce one.

**The honest limit of this increment, measured.** The refresh **merges**:
`render_refreshed_manifest` rewrites only the keys the collector sampled and
carries every other row forward byte-identically (`tools/ci_timing.py` — "merge,
not replace"; the `carried_forward` stat). So a hand-typed weight that IS reachable
for that writer — at or above `0.1` with at most one decimal place — is **not
corrected by a refresh** unless the sampled run happened to exercise that exact
file. The validator catches a map that was never measured, and a weight the
writer cannot render; it does not catch a plausible number typed yesterday, and
the writer preserves one. This is the bound of "measurements
are the source of truth", recorded as evidence on #4783 (`durations map: no guard
catches a wrong VALUE` — closed `NOT_PLANNED`, and the closure assumed the
measured writer removed the class; on un-sampled keys it does not).

`tools/ci_selection.py --integrity` and `tools/ci_timing.py`'s refresh gate now
both compose `ci_manifest.check`. Before, the refresh's gate **duplicated** the
same three `ci_selection` calls in a second place, held in step by a docstring;
now the invariant is structural.

## 4. Exit-code semantics (`tools/ci_manifest.py`)

| code | meaning | conditions |
|---|---|---|
| **0** | observed, plausible, fresh, complete, disjoint | a parseable `durations_captured_at` that is no more than `FUTURE_TOLERANCE` (1 day) ahead of now and no older than `MAX_AGE_DAYS` (21 — three missed weekly refreshes); every weight `0.0` (the sentinel) or a one-decimal value `>= 0.1`, with **at least one** such `>= 0.1` value (an all-`0.0` map is RED); and no `ci_selection` map/leg defect |
| **1** | an **OBSERVED** defect | a weight in `(0, 0.1)`, a negative weight, a weight carrying finer precision than the writer renders (one decimal place), a map whose every value is the `0.0` sentinel (no measurement at all), a capture date in the future (beyond 1 day of clock skew), a capture date older than `MAX_AGE_DAYS`, or any `ci_selection` map/leg defect (a non-numeric or non-finite weight, a `durations` key that is not a mapping, coverage / dead-key / partition defects) |
| **2** | **UNKNOWN** | `durations_captured_at` absent or unparseable; an empty/absent `durations` map while the manifest classifies fast-pool files; a manifest that cannot be read or parsed |

**Exit 2 is never 0 and red outranks unknown.** The state in which a stale or
invented weight is indistinguishable from a measured one is exactly the state in
which the map must not be reported as valid, and "we could not look" is not "it
is fine".

**In the merge gate, UNKNOWN is a NOTICE only for genuine ABSENCE — a present
but malformed value is RED.** An absent capture date is currently the real state
of `main`, so gating on it would red `manifest-integrity` repo-wide until the
weekly refresh landed (#6091 blocks that refresh from opening its PR at all — see
below): refusing honest merges for a state no lane owns, on the same day the
ruling warns that gates which refuse honest merges are a cost. The gate keeps its
documented polarity (an absent map is "this repo has not adopted durations" =
PASS, pinned by
`test_null_or_non_mapping_durations_reports_instead_of_tracebacking`), and
prints the reason as a NOTICE. The ENFORCING entry point `ci_selection.py
--integrity` — the required `manifest-integrity` job — therefore exits 0 on an
ABSENT stamp, and that is the ONLY stamp state it softens: presence is the KEY'S,
so a `durations_captured_at` that is PRESENT but unparseable is a MALFORMED
manifest value, an OBSERVED defect, and is RED (exit 1). Softening it too would
invert fail-closed, because degrading a stale-but-parseable stamp (RED) to
`'not-a-date'` would turn exit 1 into exit 0. The strict exit-2 verdict lives in
`tools/ci_manifest.py`, which no workflow invokes yet; wiring it as a required
job is not this PR's scope.

Everything the gate *can* observe is already fail-closed: the moment a capture
date exists, a stale one, a future one and an unreachable weight are all RED and
the gate exits non-zero. Only the never-refreshed state is softened.

**How that state is meant to end, and the live blocker on it.** The writer
(`--refresh-durations`, weekly `ci-timing.yml`) is what supplies the stamp. It
currently **cannot open its refresh PR at all**: `#6091` — the repo setting
*Actions → Workflow permissions → "Allow GitHub Actions to create and approve
pull requests"* is disabled, so `gh pr create` with the automatic `GITHUB_TOKEN`
is refused however the job's permissions are scoped. That is a repository setting
only the owner can change; no code change reaches it. So this PR does **not**
claim the map will become observable on its own, and does not present the notice
as a fix for the staleness. It records the dependency, and the honest verdict on
`main` today is UNKNOWN (exit 2) until `#6091` is resolved and a refresh lands.
The alternative — gating on UNKNOWN — would red `manifest-integrity` repo-wide
from this PR's first hour and keep it red until an owner-only setting is changed,
which trades an unobserved map for a frozen queue.

## 5. Verification

- `tests/test_ci_manifest.py` — each test pinning one semantic boundary: the
  three exit codes, the writer's own `Z`-suffixed stamp format, absence vs
  unparseable vs stale vs future, the `(0, 0.1)` window, the `0.0` sentinel
  staying legal, a malformed map RED where an absent one is UNKNOWN, red
  outranking unknown, an unreadable manifest, and the gate's narrowed polarity
  (a GENUINELY ABSENT stamp is a notice; a present-but-unparseable one is RED).
  (No count is written here on purpose: a
  literal test count in prose is the same stale-able pin the 688 assertion was.)
- `test_the_committed_map_carries_no_red_defect` — **the behaviour-neutrality
  pin**: the validator is not red on the tree it lands on, and the declared
  `0.0` rows are a documented state rather than a defect. This is also what keeps
  the weekly refresh able to write: it *carries forward* those rows, so a check
  that reddened them would have made every refresh refuse.
- `test_the_floor_is_the_writers_own` — pins `VALUE_FLOOR ==
  ci_timing.DURATIONS_VALUE_FLOOR_S`, so the validator and the writer cannot drift.
- `tests/test_ci_timing.py` + `tests/test_ci_selection.py` — the two suites whose
  composition changed.

## 6. Follow-ups this does NOT do

- **The map is still un-refreshed.** `main` carries no `durations_captured_at`, so
  the honest verdict today is UNKNOWN (exit 2). Supplying it needs `#6091`
  resolved (an owner-only repo setting — see §4) and then a refresh run; this PR
  does not run it, and does not touch the 1.60x imbalance.
- **`main`'s row-count pin** (`#6143`) is **not** carried here — `#6155`
  (`552e845ec`) landed that fix, and this branch takes it. The `test_ci_timing.py`
  change in this diff is the dated-stamp fix described in §2, which this change
  makes necessary.
- `#4835`, `#4658`, `#3362`, `#4115`, `#4186`, and #5719's bootstrap-trap work
  are **not** fixed here and are unchanged on `main`.

## 7. Learnings

- **A parallel writer is cheaper to delete than to reconcile.** #5719's 8388
  added lines were mostly a second copy of a pipeline the collector had just been
  given; the surviving validator is ~250. The ratio is the tell.
- **The floor is what makes an invented weight detectable at all.** Without a
  writer-side floor there is no arithmetic fingerprint to check, and the only
  available guard is provenance — which is why `durations_captured_at` is
  load-bearing and its ABSENCE has to be a distinct verdict rather than a
  default.
- **A value of `0.0` in the committed map is a declaration, not a defect.** It is
  the unmeasured / minimum-weight pin sentinel, and a plausibility check that
  reddened it would have failed on the repo's own documented state *and* blocked
  the refresh that carries it forward. The check had to be defined against the
  writer's reachable set, not against "small numbers are suspicious".
- **A refresh gate that mirrors another gate by convention will drift.** #6003's
  `integrity_problems` re-implemented `--integrity`'s duration calls and held
  them in step with a docstring. Composing one `check` makes it structural for
  four lines.
