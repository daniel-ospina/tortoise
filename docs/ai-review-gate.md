---
title: "AI Review Merge Gate"
type: engineering
domain: capability
doc_status: live
created: 2026-08-30
subjects.team: epistemic-team
ownedBy: epistemic-team
---

# AI Review Merge Gate

`main` branch protection requires the `ai-review-gate` status check on every
pull request. It replaces the human-approval requirement: merges proceed when
the code-review skill's AI review is recorded and all required checks are
green.

## How it works

- The `code-review` skill runs its review gate on the PR.
- When clean, the review is recorded and evidence is posted to the PR body by
  `record-review.sh`:

  ```text
  review recorded: reviews/<PR>.json verdict=clean @ <full-sha> (<owner/repo>) sig=<hmac>
  review recorded: reviews/<PR>.json verdict=clean @ <full-sha> diff=<sha256> (<owner/repo>) sig=<hmac>
  ```

  The second form is emitted when the PR diff can be fetched from the REST
  API. Its `diff=` field is inside the signed text, so the HMAC covers it.

  > **Producer dependency.** The `diff=` form ships in the producer half of
  > #2982 (`record-review.sh` in agent-infra, PR #767). Until that producer is
  > deployed, `record-review.sh` emits the legacy sha-only form and only the
  > sha-match path is exercised in production — the diff-match path is
  > covered by the gate harness below.

- The `ai-review-gate` workflow (`.github/workflows/ai-review-gate.yml`,
  `pull_request_target`) recomputes the HMAC signature with the
  `AI_REVIEW_GATE_KEY` repo secret (the agent machine holds the same key at
  `~/.pi/agent/.ai-review-gate-key`). A marker is accepted when EITHER its
  signed `@ <sha>` is the PR's current head, OR its signed `diff=<sha256>`
  equals the diff hash the gate computes live from the GitHub REST API for
  this PR. The check fails closed: if the live diff hash cannot be computed
  (API blip, missing `gh`/`openssl`), a `diff=` marker is not accepted.

## Why the diff, not just the head sha (#2982)

`strict: true` branch protection requires a PR branch to be up to date with
`main`. Updating it (`gh pr update-branch`) inserts a merge commit and moves
the head — but the PR's three-dot diff is byte-identical. When evidence was
keyed only to the head sha, every update invalidated a still-correct verdict,
so a green PR could never reach a terminal mergeable state. Keying evidence to
the diff lets the verdict carry forward across a merge-only update. A change
that actually changes the reviewed diff still invalidates it.

`record-review.sh` also carries a verdict forward itself: when asked to record
a now-stale sha whose diff is unchanged from what was recorded, it re-records
against the current head. A stale sha whose diff changed is still refused
(exit 3).

Both the producer and the gate hash the bytes returned by the REST API
(`Accept: application/vnd.github.v3.diff`) — never a local `git diff`, whose
output would not byte-match the API's.

### What the diff-match path does and does not verify

The gate cannot tell **which revision** the reviewer saw. It verifies only that
the marker is signed and that the signed `diff=` equals this PR's diff at check
time. The binding between a recorded sha and the diff actually reviewed is
enforced by `record-review.sh`'s stale-sha guard, not by the gate.

That matters for the producer's two escape hatches. `--force-stale` (and the
head-fetch fail-open arm) compute `diff=` from the PR's **current** diff while
keeping the caller-supplied stale sha, so a marker minted that way is accepted
here even though the reviewed artifact cannot be shown unchanged. Closing that
trust gap is producer-side work (agent-infra#784).

Do **not** "fix" it here by requiring the recorded sha to be an ancestor of the
current head: this repo's documented refresh path is `git rebase origin/main` +
`git push --force-with-lease`, which rewrites the sha while keeping the
three-dot diff byte-identical. An ancestor bound would reject exactly the
carry-forward case #2982 exists for and re-create the deadlock.

### Tests

The gate's shell logic runs inline in the workflow (it cannot reference a repo
script — `pull_request_target` never checks out PR code), so
`.github/scripts/ai-review-gate.test.sh` **extracts the `run:` block verbatim**
and drives it with a stubbed `gh` and a fabricated HMAC key. It also asserts the
non-runtime invariants — the required job carries no
`if:`/`needs:`/`continue-on-error:`, the trigger stays `pull_request_target`
with no `paths:` filter, the permissions still grant `pull-requests: read`, and
the step declares `GH_TOKEN`.

The harness is wired into `ci.yml` as `ai-review-gate-tests`. That job is **not
(yet) a required check**, so a red harness does not block a merge — see #3091 to
add it to branch protection.

### `GH_TOKEN` is required in the workflow (#2982)

The gate's `gh api` calls only work if the step declares `GH_TOKEN`. `gh`
**refuses to run inside GitHub Actions** without it:

```text
gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN environment variable.
```

The step sets `GH_TOKEN: ${{ github.token }}` (least privilege — it honours the
workflow's `permissions:` block, `contents: read` + `pull-requests: read`, which
is what reading a PR diff needs). If that line is ever removed, `live_diff_hash`
becomes permanently empty and the diff-match path silently degrades to dead
code: a legitimately carried-forward verdict is then rejected as stale, and the
error message misreports a static misconfiguration as a transient API blip.
Because the test harness stubs `gh`, no runtime test can catch this — the
harness asserts the `GH_TOKEN` declaration statically for exactly that reason.

## Recording a review

```bash
record-review.sh <PR> <full-40-hex-head-sha> clean <owner/repo>
# verdict: clean | clean-micro
```

The marker is HMAC-signed, so a body edit cannot forge it. If the PR head
moves after a record because of new review-fix commits, re-run the code-review
skill and re-record at the new head. If it moves only because the branch was
updated against `main` — whether by a merge commit or by a rebase plus
`--force-with-lease` — the three-dot diff is unchanged and the recorded
evidence remains valid.
