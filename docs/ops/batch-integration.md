---
title: "Batch integration — the gate is the batch, not the PR"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: batch-integration, ci-wall-clock, ci-headroom-guards
aboutObjects: tortoise, "#10399", .mergify.yml, .github/workflows/python-ci.yml, .github/workflows/post-merge-validation.yml, config/ci-surfaces.yml
created: 2026-10-31
updated: 2026-10-31
---

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
that may be landed on `main`; a tree that has not run is not eligible, no matter how green the
constituent PRs looked individually.

Three consequences that are easy to get wrong:

1. **A PR's own CI is not what decides eligibility.** What makes a PR *batch-eligible* is a **clean
   review recorded at its live head** (`~/.pi/agent/reviews/<owner>-<repo>-<N>.json`, key `head_sha`
   == live `headRefOid`). Do not wait on a queued or red GitHub check *before batching* — the batch
   supersedes that check as evidence. This is the whole point: per-PR CI is what "takes forever" and
   what can still be green while the *combination* is red. (It does not waive the landing rail — see
   [Landing](#landing--main-must-still-be-the-tree-you-tested).)
2. **The batch is the gate.** The full suite (not the evals) runs **once** on the batched tree. If it
   is red, the batch names the culprit and **drops** it. A red batch is never merged to be fixed later.
3. **The tested tree must be the landed tree.** A suite run certifies one tree, and only one. If `main`
   moved while the batch ran, the tree that lands is a composition nobody tested — see
   [Landing](#landing--main-must-still-be-the-tree-you-tested), which is not optional.

Why the combination can be red when every PR is green: PR #1 renames a function and every call site on
main; PR #2 adds a new call to the old name. Each is green against main. Merged together, main is red.
Only a tree carrying both can observe it. This is bors-ng's opening example, and it is also **this
repository's own incident record** — `.mergify.yml` carries two recorded composition failures
(#5293, #5335), each a batch of one whose failure its base did not explain.

### This repository already has a merge queue — what it does, and what the batch adds

`.mergify.yml` is a **live** merge queue for `main`, not a plan. Today it merges a PR onto the current
base inside a `mergify/merge-queue/main` branch and runs CI there, so a merge is validated against the
tree that will actually exist; `merge_conditions: check-success=python-ci-gate` is evaluated on that
queue branch, `batch_size: 4`, `merge_method: squash`. The queue is the reason a PR does not have to be
manually updated before merging, and it already catches composition failures.

That overlap is real and must not be papered over — but it is not the same gate:

| | `.mergify.yml` queue | this batch |
|---|---|---|
| what runs | the **tiered selection** for the diff (`config/ci-surfaces.yml`) | the **full** deterministic suite, once |
| over what | `main ∪ the queued PRs`, batch of up to 4 | an arbitrary set, assembled in ascending PR order |
| baseline | none — a green run is absolute | a **Phase 1 baseline on `origin/main`**, so pre-existing red is separated from caused red |
| on failure | re-tested, bisected | attributed to a merge commit, and the culprit **dropped** |
| base drift | the queue **is** the writer, so its base cannot drift under it | the batch does not own `main`, so the base **can** drift — see Landing |

So: the queue owns the merge and validates a composition; the batch owns the *evidence* and validates a
composition the queue cannot (larger than 4, chosen deliberately, measured against a baseline). Neither
substitutes for the other. The ruling above moves the **full suite** to the batch; it does not remove
the queue, and nothing here asks for it to be removed.

## Phases

### 0. Select the lane — first, before anything runs

The suite is **URI-required**. `tests/conftest.py`'s session fixture fails the whole run closed when
`TORTOISE_DB_URI` is unset and `TORTOISE_TEST_CARVE_OUT` is not opted in
(`tests/_embedded.py::_assert_p4_uri_required`). A suite command with no lane does not produce a
baseline; it produces a session-start refusal. So the lane is picked and **exported once**, in the shell
that runs every later phase.

Pick the highest lane that actually works, and name it in the record:

1. `docker ps` works → the docker FalkorDB lane:
   `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`.
2. Docker exists but the shared instance is contended or absent → `tools/test_lane.py uri` for a
   private container (export **all three** vars it prints).
3. No docker → `TORTOISE_TEST_CARVE_OUT=1`. Then the verdict is `UNPROVEN-FULL-SUITE` and **no phase
   below can change that**.

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'   # or the test_lane.py vars
uv sync --extra embeddings --extra parity    # ONE command — see the extras trap below
```

The **same lane** must serve Phase 1 and Phase 3. A batch measured on a different lane from its
baseline is not comparable to it.

### 1. Baseline — run the suite on `origin/main` ALONE, first

Non-negotiable, and the cheapest step to skip wrongly. Without a baseline you cannot tell a failure a
PR *caused* from a failure that was **already there**, and you will drop an innocent PR (or "fix" a
pre-existing bug inside a batch, which is scope creep with no review).

```bash
git fetch origin main
PINNED="$(git rev-parse origin/main)"           # PIN THIS SHA — every later comparison is against it
git branch integration/base-check "$PINNED"     # no changes on this branch
uv run pytest tests/ -q -m 'not track_b and not live and not integration'
```

Record the baseline result **as data**: counts, the **exact list of failing test ids**, and the
duration. Those ids are the ledger that later failures are diffed against.

**The marker filter is not decoration.** Every CI lane in this repo runs the deterministic suite with
`-m 'not track_b and not live and not integration'` (`.github/workflows/python-ci.yml`). `live` marks
live-API probes and `integration` marks opt-in tests needing live credentials or production access
(`pyproject.toml`). A bare `pytest tests/` therefore runs tests that cannot pass on a lane without
those credentials — and by the Baseline rule every such failure becomes a `BASELINE(<ids>)` id, or
convicts an innocent PR. The filter is what makes the run comparable to CI at all.

### 2. Assemble the batch

Eligibility: open, non-draft, clean review **at the live head**. Verify, never assume — a lane may have
pushed since its review:

```bash
gh pr view <N> --json headRefOid,mergeable     # headRefOid must equal the registry's head_sha
```

Build in **ascending PR number** so the composition is deterministic and reconstructible:

```bash
git checkout -b integration/batch-1 "$PINNED"
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

### 3. Run the gate on the batch tree

Same lane, same command, same env as the baseline:

```bash
uv run pytest tests/ -q -m 'not track_b and not live and not integration'   # detached, polled; never one blocking call
```

### 4. Compare to the baseline and blame

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

- **Drop the culprit**, do not fix it inside the batch. Every merge in the batch is a merge commit **by
  construction**, and Git refuses to revert a merge without a mainline: a bare `git revert <sha>` exits
  128 (`is a merge but no -m option was given`) and drops nothing. The batch branch is mainline `1` —
  the first parent is the batch's own history, the second is the PR — so:

  ```bash
  git revert --no-edit -m 1 <merge sha>
  ```

  Then re-run **only the failed files**, then **one more full suite run**. A batch that cannot be made
  green ships without the culprit, never with it.

### 5. Verdict — and it must be honest about what ran

| Verdict | Meaning |
|---|---|
| `BATCH-GREEN` | Full suite ran on the batched tree, no NEW failures vs baseline. |
| `BATCH-GREEN(BASELINE(<ids>))` | Green *relative to baseline*; the listed ids were already red on main. |
| `BATCH-RED(<ids>)` | New failures remain after dropping the culprit(s). Do not merge. |
| `UNPROVEN-FULL-SUITE(<lane>)` | **The full suite did not run.** Only the embedded carve-out or a subset ran. This is never "green". |

⛔ **A batch is never green on a partial lane.** If docker/FalkorDB was unavailable and only the
embedded carve-out ran, the verdict is `UNPROVEN-FULL-SUITE(embedded-carve-out)`. Say that in those
words; a subset reported as "green" is how a post-merge red gets manufactured by a process that was
supposed to prevent it.

**Tell a full run from a carve-out run by counting, not by remembering.** The carve-out set is
`config/ci-surfaces.yml` → `carve_out:` (mirrored by `tests/_embedded.py` → `TEST_NO_REDIRECT_STEMS`;
`tests/test_ci_selection.py` fails if the two disagree). Read the list — do not quote a remembered
count of it, because the set grows and any number written here is stale by the next reconciliation.

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

### Landing — `main` must still be the tree you tested

This is the one property the prior art gets for free and a hand-assembled batch does not: bors
fast-forwards `main` to the staging branch it just tested ("bit-for-bit"), and GitHub's queue validates
against *"the latest version of the target branch and any pull requests already in the queue"* — in
both, **the service owns the merge**, so the validated tree is the landed tree by construction. Here
nothing owns it. `.mergify.yml` keeps merging to `main` continuously (that is its job), so a batch run
can outlive its own base.

Nothing else in this document is worth anything if the base moved, so check it at landing time, not at
assembly time:

```bash
git fetch origin main
[ "$(git rev-parse origin/main)" = "$PINNED" ] || {
  echo "main moved: $PINNED -> $(git rev-parse origin/main) — re-baseline and re-run phases 1-3"; exit 1
}
```

If that assertion fails, there are exactly two honest routes and no third:

1. **Re-baseline and re-run** — `PINNED` becomes the new main, phases 1 and 3 run again on a batch
   rebuilt from it. Expensive, always correct.
2. **Freeze merges to `main` for the batch's duration** — declare it in the batch PR and hold the
   queue, so the base cannot move. Cheap, and only honest if the hold is real.

What is **not** a route: merging the batch PR anyway. A squash-merge onto the pinned base reproduces
the tested tree exactly (the batch's merge base *is* `PINNED`); a squash-merge onto a `main` that has
moved applies the batch's diff to a different tree, and the result is a composition no run has ever
seen — the same class of failure this doctrine exists to remove, arriving through the door marked
"landing".

**The landing rail still applies.** The ruling moves *what makes a constituent PR eligible*, never the
protections on the branch the batch lands on. `main` keeps the seven required contexts declared in
`.github/settings.yml` (`pricing-artifact`, `docs`, `test-isolation`, `license-surface`, `legal-e2e`,
`python-ci-gate`, `ai-review-gate`; `.github/scripts/check-required-set.py --live` is the assertion that
the file and the live protection agree), and `.mergify.yml` re-checks `python-ci-gate` on the queue
branch. The batch PR therefore still owes those contexts — the batch run is the *evidence* that they
will pass, not a substitute for them, and this doctrine does not ask for an exemption. What it removes
is waiting on each constituent PR's own CI before batching; what it adds is a full-suite run that the
rail's tiered selection does not perform.

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

Divergence, deliberate: all three are **automated services that own the merge**, and this repo already
runs one of them (`.mergify.yml`). Here the *batch* is assembled by an agent, the *landing* is
human-routed, and nothing owns `main` on the batch's behalf — which is exactly why the landing
assertion above has to be written down instead of inherited. The batching, the test-what-you-ship
invariant, and bisect-to-blame are the same. Manual `git log` + `git bisect run` is this process's
bisect.
