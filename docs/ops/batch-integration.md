# Batch integration — the gate is the batch, not the PR

**Status:** adopted 2026-10-31 by owner ruling — decision record: issue **#10399**.
**Supersedes:** the post-merge full suite as the *gate*. `python-ci.yml`'s `push: branches: [main]`
leg (and `post-merge-validation.yml`, which is warn-only by its own header) still runs, but it is now a
**detector**, not the gate: a red main is a process failure, not an expected state.

**OVERRIDES:** ci-main.yml's post-merge full suite — the suite now gates the batch BEFORE main, because
a post-merge suite cannot prevent a red main.

> The ruling names `ci-main.yml`. This repository's post-merge full suite is spelled
> `python-ci.yml` (`on: push: branches: [main]`) plus `post-merge-validation.yml`; there is no
> `ci-main.yml` file here. The override is against the **model** — "the full suite runs after the
> merge" — whichever workflow carries it.

## The invariant

> **`main` only ever receives a tree that has already passed the full suite.**

Everything below is in service of that one sentence. A tree that passed the suite is the only thing
that may be fast-forwarded/merged into `main`; a tree that has not run is not eligible, no matter how
green the constituent PRs looked individually.

Two consequences that are easy to get wrong:

1. **A PR's own CI is not the gate.** What makes a PR *batch-eligible* is a **clean review recorded at
   its live head** (`~/.pi/agent/reviews/<owner>-<repo>-<N>.json`, key `head_sha` == live
   `headRefOid`). Do not wait on a queued or red GitHub check — the batch supersedes it. This is the
   whole point: per-PR CI is what "takes forever" and what can still be green while the *combination*
   is red.
2. **The batch is the gate.** The full suite (not the evals) runs **once** on the batched tree. If it
   is red, the batch names the culprit and **drops** it. A red batch is never merged to be fixed later.

Why the combination can be red when every PR is green (this is not hypothetical — it is bors-ng's
opening example): PR #1 renames a function and every call site on main; PR #2 adds a new call to the
old name. Each is green against main. Merged together, main is red. Only a tree carrying both can
observe it.

## Phases

### 0. Baseline — run the suite on `origin/main` ALONE, first

Non-negotiable, and the cheapest step to skip wrongly. Without a baseline you cannot tell a failure a
PR *caused* from a failure that was **already there**, and you will drop an innocent PR (or "fix" a
pre-existing bug inside a batch, which is scope creep with no review).

```bash
git fetch origin main
git rev-parse origin/main                       # PIN THIS SHA — every later comparison is against it
git branch integration/base-check "$(git rev-parse origin/main)"   # no changes on this branch
uv sync --extra embeddings --extra parity       # see the trap below
uv run pytest tests/ -q                         # record: total/passed/failed/skipped + the failing ids
```

Record the baseline result **as data**: counts, the **exact list of failing test ids**, and the
duration. Those ids are the ledger that later failures are diffed against.

### 1. Assemble the batch

Eligibility: open, non-draft, clean review **at the live head**. Verify, never assume — a lane may have
pushed since its review:

```bash
gh pr view <N> --json headRefOid,mergeable     # headRefOid must equal the registry's head_sha
```

Build in **ascending PR number** so the composition is deterministic and reconstructible:

```bash
git checkout -b integration/batch-1 "$(git rev-parse origin/main)"
git fetch origin pull/<N>/head:refs/tmp/pr-<N>          # exactly the reviewed head
git merge --no-ff --no-edit -m "batch: merge PR #<N> (<sha>)" refs/tmp/pr-<N>
git log --oneline -1                                    # after EVERY merge
```

Each merge is a real `--no-ff` merge commit naming its PR, so `git log origin/main..integration/batch-1`
**is** the batch's manifest.

**Conflicts.** Resolve only when the resolution is **unambiguous** — both sides additive, or the same
fix applied two ways with identical effect. Write down which side you took and why. Otherwise **drop
that PR from the batch** and say so in the PR body. Never `-X ours`, never `-X theirs`, never `--force`:
a forced resolution hides a semantic conflict the suite may not catch, which is exactly the failure
mode this process exists to prevent.

### 2. Run the gate on the batch tree

Same lane, same command, same env as the baseline — a batch measured on a different lane is not
comparable to its baseline.

```bash
uv run pytest tests/ -q     # detached, polled; never one blocking call
```

### 3. Compare to the baseline and blame

- **A failure id that is in the baseline set is a `BASELINE(<ids>)` failure.** Not a PR's fault. Do not
  drop a PR for it, and do not fix it inside the batch. One line on whether the batch makes it worse.
- **A failure id that is NOT in the baseline set is NEW** and convicts a PR. Find which one:

```bash
git log --oneline origin/main..integration/batch-1        # candidate merges
git bisect start integration/batch-1 origin/main          # or, cheaper, walk the merges
#   at each step: run ONLY the failing files (not the whole suite)
#   git bisect good <merge sha> / git bisect bad <merge sha>
uv run pytest <the failing file(s)> -q
```

  `git bisect run` is the honest tool when the failing file set is stable:

```bash
git bisect run sh -c 'uv run pytest <failing files> -q >/dev/null 2>&1 || exit 1'
```

  Attribute to the **merge commit's PR** — the log line names it.
- **Drop the culprit**, do not fix it inside the batch:
  `git revert --no-edit <merge sha>` → re-run **only the failed files** → then **one more full suite
  run**. A batch that cannot be made green ships without the culprit, never with it.

### 4. Verdict — and it must be honest about what ran

| Verdict | Meaning |
|---|---|
| `BATCH-GREEN` | Full suite ran on the batched tree, no NEW failures vs baseline. |
| `BATCH-GREEN(BASELINE(<ids>))` | Green *relative to baseline*; the listed ids were already red on main. |
| `BATCH-RED(<ids>)` | New failures remain after dropping the culprit(s). Do not merge. |
| `UNPROVEN-FULL-SUITE(<lane>)` | **The full suite did not run.** Only the embedded carve-out (`TORTOISE_TEST_CARVE_OUT=1`, 17 embedded-only files) or a subset ran. This is never "green". |

⛔ **A batch is never green on a partial lane.** If docker/FalkorDB was unavailable and only the
embedded carve-out ran, the verdict is `UNPROVEN-FULL-SUITE(embedded-carve-out)`. Say that in those
words; a subset reported as "green" is how a post-merge red gets manufactured by a process that was
supposed to prevent it.

**Lane selection (pick the highest that actually works, and name it):**

1. `docker ps` works → docker FalkorDB lane, `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`.
2. Docker exists but the shared instance is contended or absent → `tools/test_lane.py uri` for a
   private container (export **all three** vars it prints).
3. No docker → `TORTOISE_TEST_CARVE_OUT=1` — and then the verdict is `UNPROVEN-FULL-SUITE`.

**The extras trap (#2985):** `uv sync --extra embeddings --extra parity`, always, in ONE command.
Plain `uv sync` silently drops the dense-retrieval extras; retrieval then degrades to keyword-only and
the run reports green for a product that no longer has embeddings. `uv sync --extra embeddings` alone
drops `parity` (pyarrow). The extras are part of the lane definition, not setup trivia.

## Aftermath

- The batch PR body is the **evidence**: constituent PRs, conflicts dropped and why, baseline vs batch
  matrix, per-PR attribution, verdict. Written with `--body-file`, never an inline body.
- The record of a *review* is not the record of a *batch*. Do not record a review of your own work
  (#825).
- Landing the batch is a separate, human-routed step. The batch lane never merges its own PR.

## Prior art this matches (and where it diverges)

Convergent practice, three independent implementations:

- **bors-ng** — merge queue, **staging branch**, merge the queued PRs into it, test once, and if it
  passes "the main branch contains the exact contents that were just tested, bit-for-bit". Batches by
  default and **bisects** on failure.
- **GitHub merge queue** — validates each PR "when applied to the latest version of the target branch
  and any pull requests already in the queue" (`merge_group` event), i.e. the same speculative-tree
  idea, per-PR rather than batched.
- **Zuul** — speculative execution / dependent pipelines: test the proposed *future state*, not the
  PR against today's main.

Divergence, deliberate: bors and GitHub's queue are **automated services** that own the merge; here the
batch is assembled by an agent and the **landing is human-routed**. The batching, the
test-what-you-ship invariant, and bisect-to-blame are the same. Manual `git log` + `git bisect run` is
this process's bisect.
