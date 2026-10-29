---
title: "Run only what your diff affects — tools/run_affected.sh (#10130)"
type: engineering
domain: operations
doc_status: live
created: 2026-10-27
updated: 2026-10-27
subjects.team: epistemic-team
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Run only what your diff affects

`tools/run_affected.sh` answers **"what does my diff actually need to run?"** locally, in one
command, and runs it. It is a thin, opinionated wrapper over the affected-subset selector
(`tools/ci_selection.py`) — the same selector `python-ci.yml` uses to decide which files CI runs.

## Why this exists (measured)

The selector already worked; only CI internals used it, and reaching it required knowing two things
that are not guessable. So lanes either ran the whole selected CI set locally or — worse — pushed a
commit to get an answer from CI.

Measured on #10118 (2026-10-27): CI selected **863 files** for that diff while the suites it actually
needed were **257 tests**. Every fix round was pushed to CI instead of reasoned about locally.

## One command

```bash
tools/run_affected.sh                 # print the selected test files (default range)
tools/run_affected.sh --print         # same, explicit
tools/run_affected.sh --summary       # machine-readable counts only
tools/run_affected.sh --run           # uv run pytest <the selected files>
tools/run_affected.sh --run -- -x -q  # extra pytest args go after `--`
tools/run_affected.sh v1.2.0          # a bare revision means `<rev>...HEAD`
tools/run_affected.sh --range origin/main~10...HEAD
tools/run_affected.sh --files-from /tmp/changed.txt   # I already have the list
tools/run_affected.sh --allow-full    # accept a `full: true` selection
```

The default range is `origin/main...HEAD` — three dots, i.e. `git merge-base`, so commits that landed
on `main` since your branch forked are not counted as *your* changed files. The name list is computed
with `git diff --no-renames`, exactly as `python-ci.yml` computes it: with rename detection on, a moved
`tortoise/sdk.py` is reported only under its new name, so a shared module's blast radius would silently
narrow to the `core` surface. `$RANGE` given twice is a usage error rather than "last one wins".

`--print` writes the selected files to **stdout** (one `tests/…py` path per line, ready to pipe) and a
**one-line summary to stderr**: how many files, which surfaces produced them, and what the slow /
carve-out legs would have run. A lane that only looks at stdout gets a clean list; a lane that reads
the terminal gets the number *with its explanation*. (With `--allow-full` the single stdout line is
`tests` — the directory — not a `.py` path.)

**The range is a COMMIT range, so uncommitted work is not in the selection.** When the worktree has
uncommitted or untracked paths the tool says so on stderr by name and count, because the failure mode
it must not have is a silent *under*-selection: a lane edits a shared module, gets the last commit's
answer, and never computes the edit's blast radius. Commit, or pass a list that includes the dirty
paths with `--files-from` (`--files-from` and a range are mutually exclusive — one source of truth).

## Trap 1 — `--changed-files` takes the LIST on stdin, not a path to it

`ci_selection.py --changed-files <path>` makes the *path* one changed file. Nothing in
`SOURCE_PATTERNS` knows it, so the selection takes its deliberate fail-closed FULL-matrix branch and
answers:

```json
{"full": true, "test_files": "ALL", "surfaces": [every surface]}
```

That answer is indistinguishable from a legitimate full selection (a push, a schedule, a shared
module) — and it has already been quoted in a live PR as "this docs-only diff selects everything"
(#9109). **This tool therefore never passes a path**: it pipes the list in on stdin
(`--changed-files -`), which is pinned by `tests/test_run_affected.py`.

Two consequences you will notice:

- Passing a **path** as the range operand is refused as a usage error (exit 2) with an explanation —
  that is the trap, not a range. Use `--files-from <file>`.
- A `full: true` selection is **refused** (exit 3) unless you pass `--allow-full`. The refusal names
  the selector's own reason (a `SHARED_MODULES` path such as `tortoise/sdk.py`, a linter-policy file,
  an unknown path) and lists the surfaces. Running everything is then a deliberate choice, never a
  silent one.

If you must call the selector yourself, the form is:

```bash
git diff --name-only origin/main...HEAD | uv run python tools/ci_selection.py --changed-files -
```

## Trap 2 — a big number is the selector working

The selection is **surface**-based, not import-graph based. `core` alone is **743 test files** out of
**1,224** in the tree (re-measured 2026-10-27 at `77dc2bbba` + #10194's own test file; it was
737 / 1,218 when this doc was written), and a docs-prose diff still pulls its surface's smoke set.
Measured at this head:

| Changed path | Selection | Files |
|---|---|---|
| `docs/blog-authoring.md` (prose) | surfaces `onboarding`, not full | 137 |
| `tools/run_affected.sh` (non-python tool) | tier-1 smoke, surfaces `[]` | 64 |
| `tortoise/sdk.py` (SHARED_MODULE) | `full: true` — blast radius | all |
| `config/ci-surfaces.yml` (falls to `core`) | surfaces `core`, not full | 687 |
| `HEAD...HEAD` (nothing) | nothing, and it says so | 0 |

So a few hundred files for one small diff is expected — a `config/` edit pulls the whole `core`
surface (that 687 is this lane's own diff: it moved from 688 when #10194 took the two
`tests/eval/` files a `config/` diff used to select out of the gate, and its own test file is the
+1 back). If a number really looks wrong, the summary line names the
surfaces responsible — that is the diagnostic, not a smaller number.

## What it does NOT run

The `test-slow` and `test-carve-out` legs are **not** run locally: they need the docker FalkorDB lane
and the embedded carve-out targeting (see `AGENTS.md` → Testing). The summary names how many slow
files the matched surfaces own and whether the carve-out leg would trigger, so what you skip is
visible rather than assumed.

A **third** leg is subtracted as well: `uri_requiring` (12 files at this head — `test_capabilities_endpoint.py`,
`test_onboarding_state_cas.py`, the `test_onboarding_state_split_*` family, …). Those cannot run in a
URI-empty leg at all, so `select()` removes them from every tier-2 answer; the summary does not carry
a count for them (the selection JSON has no key for it), and this tool does not run them. If your diff
touches the hosted/API state surface those files guard, run the docker lane explicitly:

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_onboarding_state.py -q
```

A **fourth** set is subtracted, and it is the one that surprises people: the **non-gating lane**
(`config/ci-surfaces.yml: on_demand` — **44 files** at this head). These are tests that exist and run
but are deliberately not a merge gate:

| lane entry | files | its runner |
|---|---|---|
| `eval/**` + `longmem_eval/**` (the subtree form, expanded against disk — the count GROWS with the subtree and is never a literal) | 43 | `.github/workflows/eval-suite-manual.yml` — `workflow_dispatch` only (#10194) |
| `eval/retrieval/test_integration.py` (already inside `eval/**`, so it runs in BOTH shapes — and it is the most expensive of the subtree's files: 1 523.4 s of their 2 604.3 s, measured over the 42 they numbered at #10194) | 0 extra | `.github/workflows/evals-on-demand.yml` — its own literal lane entry, embedded shape (#4711) · and `.github/workflows/eval-suite-manual.yml`, docker shape, as one of the subtree's files |
| `test_perf_lane_volunteer_context.py` | 1 | `.github/workflows/perf-lane.yml` (#3992) |

So a diff under `tests/eval/` or `tests/longmem_eval/`, or one that selects the `eval` surface (an
`eval`-registered test file, `tools/longmem_eval/…`, `tortoise/embeddings.py`), will select **none of
those files** — a green PR there says nothing about them. That is the owner's decision (#10194, evals
paused), not a tool defect: run the manual workflow, or `--run` with the paths yourself.

`--run` otherwise runs the fast set only — the same files the PR's shard legs run. Without that
export, a default checkout **refuses** the run outright (epic #1647 P4): the URI gate reds every test
in the selection at fixture setup, which reads as "my diff broke things" when it is the environment.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | selection printed / ran; also "the range has no changed files" (said explicitly) |
| `2` | usage error — unknown option, two modes at once, two ranges, `--files-from` together with a range, bad range, missing `--files-from`, or a **path** passed where a range belongs (trap 1) |
| `3` | **refused**: the selector answered `full: true` and `--allow-full` was not given |
| `4` | the selector itself failed (non-zero, or unparsable JSON) |
| other | with `--run`: pytest's own exit code, unchanged |

## How the selection works, and what this tool cannot fix

`select()` maps each changed path to a surface via `SOURCE_PATTERNS` / `SHARED_MODULES` in
`tools/ci_selection.py`, unions those surfaces' test files, then subtracts the slow, carve-out,
URI-requiring and non-gating-lane sets. Unknown paths and shared modules fail closed to FULL, on
purpose.

What that means for the number you see:

- **It over-selects by design and under-selects nowhere.** A surface includes every test file that
  guards anything in it, so a diff inside one surface pulls that surface's whole set. This is the same
  direction every comparable system takes (below).
- **Membership is not this tool's to choose.** Moving a test file between surfaces, or between the
  fast pool and `slow_files` / `on_demand`, is a curation decision in `config/ci-surfaces.yml` and
  needs its own evidence (#10127). An `on_demand` entry takes two forms — one file, or a `dir/**`
  subtree expanded against disk — and a subtree is the right spelling when the UNIT of the decision is
  a directory (#10194's `eval/**`, `longmem_eval/**`): a new file under it is off the gate with no
  config edit, where a literal list would put it back into a shard and tell nobody. This tool only
  *reads* that manifest, and its own test file is the only row it registers.

## What comparable selective runners do (and what we took)

Read for this doc, 2026-10-27:

- **pytest-testmon** selects by *execution*: it records which code blocks each test executed (via
  `coverage.py`) and re-runs a test whose executed blocks changed. Its stated reliability limits are
  `coverage.py`'s limits, and it deliberately re-runs dependents on the smallest change — i.e. it
  trades over-selection for safety. *Taken:* over-select rather than risk a false negative; a subset
  that misses a test is worse than a subset that runs one unnecessarily.
- **Pants / Bazel (target graphs)** derive the affected set from *declared* dependencies
  (`--changed-since` plus `--changed-dependents=direct|transitive`; `rdeps` queries). Their documented
  false-negative risk is exactly the undeclared edge — Pants says outright that it does not understand
  *transitive third-party* dependencies, and recommends diffing from the merge base
  (`git merge-base HEAD origin/main`). *Taken:* the merge-base default range (`A...HEAD`), and the
  lesson that an undeclared edge under-selects — which is why our unknown paths fail closed to FULL.
- **Nx / Turborepo `affected`** use a git diff plus a project graph. Both document the same two
  failure modes we hit: Nx warns that a task which does not declare its inputs is **not** selected even
  though the project imports the changed file, and that a change to a widely-used project "selects most
  tasks"; Turborepo's `--affected` is `--filter=...[main...HEAD]`, operates at package granularity by
  default (task-level inputs are opt-in), and a **shallow checkout makes all packages "changed"**.
  *Taken:* both make the fail-closed answer possible and *loud* rather than silent (Nx ships
  `--explain` for exactly this; our selector prints the cause on stderr), and both treat a large
  selection as expected rather than as a defect.

The convergence: every comparable system over-selects on shared inputs, and the failure worth fixing
is not the size — it is an answer whose *reason* is invisible. That is the one thing this tool changes.

## Verification

```bash
uv run python tools/ci_selection.py --integrity      # the manifest gate this tool reads
# the wrapper's pins; DB-free, but pytest's default lane refuses a URI-less run (#1647 P4):
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_run_affected.py -q
tools/run_affected.sh --print                        # this branch's own diff
```

The pins (`tests/test_run_affected.py`) cover: `--print` emits pytest-pathable `tests/…` entries that
exist on disk; the printed set **is** the selector's own answer for the same list (no second
implementation); a `full: true` selection is refused with exit 3 and the reason named; `--allow-full`
accepts it; a path operand — absolute **or** relative to the caller — is refused as the #9109 trap;
`--files-from` plus a range is a usage error; an unparsable selector payload exits 4 with a named
message rather than a traceback; `--run` forwards `--`-args to pytest and returns pytest's own exit
code; the changed list comes from `git diff --no-renames` (a renamed shared module must not narrow the
selection); a dirty worktree is WARNED about by name without touching the repo; `--range` twice and
`--files-from` plus a range are usage errors; a caller-relative `--files-from` path resolves; the
script's only selector invocation is the stdin form; an empty range says so.
