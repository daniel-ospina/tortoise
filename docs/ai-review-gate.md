---
title: "AI Review Merge Gate"
type: engineering
domain: capability
doc_status: live
created: 2026-08-30
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: ai-review-gate
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
  API. The `diff=<sha256>` segment is optional (added producer-side by #2982)
  and is part of the SIGNED text — it records the sha256 of the PR's three-dot
  diff **after normalization** (#1362), so a review stays tied to the reviewed
  artifact rather than only the commit sha. The gate must accept BOTH shapes: a
  regex that omits the optional segment rejects every correctly-signed
  post-#2982 marker before the HMAC check is ever reached (#3076).

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
  this PR. The diff-match arm fails closed: if the live diff hash cannot be
  computed (API blip, missing `gh`/`openssl`/`python3`), a *stale-sha* `diff=` marker is
  not accepted. The gate computes TWO digests from the same fetched bytes and
  accepts a marker whose signed `diff=` equals EITHER — the normalized digest
  (#1362) or the legacy raw digest, so existing markers keep working. A marker
  whose `@ <sha>` matches the current head still passes on the sha-match path
  regardless — that binding is the stronger, pre-#2982 claim, so its `diff=`
  field (if present) is never matched against the live diff.

## Why the diff, not just the head sha (#2982)

`strict: true` branch protection requires a PR branch to be up to date with
`main`. Updating it (`gh pr update-branch`) inserts a merge commit and moves
the head — but the PR's three-dot diff is byte-identical **whenever `main`'s
advancement did not touch a file the PR also changes** (if it did, the hunk
context/blob ids change and the evidence is genuinely stale, so a re-record is
correct). When evidence was keyed only to the head sha, even an untouched
diff invalidated a still-correct verdict,
so a green PR could never reach a terminal mergeable state. Keying evidence to
the diff lets the verdict carry forward across a merge-only update. A change
that actually changes the reviewed diff still invalidates it.

The producer half (agent-infra PR #767, **not yet deployed**) also carries a
verdict forward itself: asked to record a now-stale sha whose diff is unchanged
from what was recorded, it re-records against the current head, and it refuses
a stale sha whose diff changed (exit 3). Until it ships, the deployed
`record-review.sh` still refuses *any* stale sha and emits no `diff=` — see the
producer-dependency note above.

Both the producer and the gate hash the bytes returned by the REST API
(`Accept: application/vnd.github.v3.diff`) — never a local `git diff`, whose
output would not byte-match the API's. Those bytes are normalized before
hashing (#1362, below).

### Diff normalization (#1362)

A base move (`git rebase`, `gh pr update-branch`) rewrites the `index
<old>..<new>` lines and the `@@ -a,b +c,d @@` hunk headers **even when every
changed line is byte-identical**, so a digest over the raw bytes stops matching
a still-correct verdict and forces a fresh review. Measured on a real BEHIND PR
(#4841): the two revisions were the same 137,767 bytes with exactly six lines
different — four hunk headers and two `index` lines. `git patch-id --stable`
matched; the shipped raw digest did not, and the reviewed PR was refused. The
owner ruling (2026-09-23) is to compute the digest over a **normalized** diff.

The normalization is a signed cross-repo contract with
`record-review.sh` / its `scripts/lib/diff-normalize.py` (agent-infra#1362);
both sides MUST implement exactly the same predicate below (the producer half
is agent-infra#1431 — if the two halves diverge, every freshly-signed marker
stops matching fleet-wide, the #3076 shape). Process the diff **entry-scoped**
(entry boundaries are `^diff --git ` lines), as lines (split on `\n`, preserve
the final line's trailing-newline state):

1. **Drop** an `^index [0-9a-f]+\.\.[0-9a-f]+( [0-7]{6})?$` line **only when its
   entry contains at least one hunk line** (`^@@ `).
2. **Keep** the `index` line **verbatim** when the entry contains **no** hunk
   line — a binary entry (`^Binary files .* differ$` / `^GIT binary patch$`) or
   a hunk-less empty-file add/delete.
3. **Rewrite** every
   `^@@ -([0-9]+)(,([0-9]+))? \+([0-9]+)(,([0-9]+))? @@(.*)$` to
   `@@ -0,<old-count> +0,<new-count> @@<heading>`, each count defaulting to `1`
   when its group is absent (`@@ -5 +5 @@` means one line each) — this applies
   to every entry.
4. Every other line passes through unchanged.

The contract sentence: **drop the `index` line exactly when the hunk content
already carries the change.**

> **Binary carve-out (amendment to the 2026-09-23 ruling).** A binary entry has
> no hunks and `Binary files … differ` carries no content, so its `index` line
> is the **only** content-bearing field. Dropping it made two *distinct* binary
> revisions normalize identically: review binary v1, sign the marker, swap in
> v2, and the required gate **accepted** an unreviewed binary — a fail-open.
> The amendment is required by the ruling's own rationale ("the `index` line is
> redundant with hunk content" — false precisely when there is no hunk content)
> and is recorded on agent-infra#1362, comment 5806797023. The producer must
> mirror it with the **same hunk-presence predicate** (agent-infra#1431) — a
> binary-marker-presence predicate would diverge on a hunk-less *non-binary*
> entry (the empty-file add/delete in step 2).

Hunk **content**, hunk **counts**, and the `diff --git` / `---` / `+++` / mode /
rename / binary lines and section heading are deliberately **not** normalized —
counts derive from content, so they move only when content moves. The
transformation needs has-this-entry-seen-a-hunk state, so it is a line filter
**with state** (the consumer uses an inline `python3` block mirroring the
producer's `split("\n")` / `"\n".join(…)`; a stateless `sed` one-liner cannot
express it). Never use `git patch-id`: `--stable` and the default both
**ignore whitespace**, so a whitespace-only change would carry a stale verdict
forward (a false accept). sha256 over normalized bytes does not.

The gate computes **both** digests from the same fetched bytes and accepts a
marker whose signed `diff=` equals either:

- `live_diff_hash_norm` — the normalized digest, for markers a post-#1362
  producer emits; and
- `live_diff_hash_raw` — the legacy raw digest, for **every** marker already
  recorded.

The legacy arm is mandatory: without it every existing marker breaks and the
required check reddens fleet-wide. This is a **consumer-first land order** —
the gate is safe to land before or after the producer, and changes nothing
until the producer starts emitting the normalized digest.

### What the diff-match path does and does not verify

The gate cannot tell **which revision** the reviewer saw. It verifies only that
the marker is signed and that the signed `diff=` equals this PR's diff at check
time. The binding between a recorded sha and the diff actually reviewed is
enforced by `record-review.sh`'s stale-sha guard, not by the gate.

That matters once the producer half lands (agent-infra PR #767). Its
`--force-stale` (and its head-fetch fail-open arm) will compute `diff=` from the
PR's **current** diff while keeping the caller-supplied stale sha, so a marker
minted that way IS accepted here even though the reviewed artifact cannot be
shown unchanged. Closing that trust gap is producer-side work
(agent-infra#784).

Do **not** "fix" it here by requiring the recorded sha to be an ancestor of the
current head: this repo's documented refresh path is `git rebase origin/main` +
`git push --force-with-lease`, which rewrites the sha while keeping the
three-dot diff byte-identical whenever the rebase does not have to reconcile
`main`'s changes into the PR's files. An ancestor bound would reject exactly
the carry-forward case #2982 exists for and re-create the deadlock.

### Tests

The gate's shell logic runs inline in the workflow (this workflow has no
checkout step, so it cannot reference a repo script), so
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
is what reading a PR diff needs). If that line is ever removed, the live diff
hashes (`live_diff_hash_norm`/`live_diff_hash_raw`)
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
evidence remains valid **for this required check**.

> The local `review-enforcer` extension keeps its own, head-bound merge gate, so
a plain local merge is still blocked after a merge-only update. That is tracked
as agent-infra#1351, not fixed here.

## When the gate goes red

The gate distinguishes the failure causes in its `::error::` output, so the fix
is unambiguous:

| Message says | Cause | Fix |
|---|---|---|
| `No AI review evidence found` | no marker in the PR body | run `record-review.sh` |
| `is UNSIGNED` | marker has no ` sig=<hmac>` segment at all | re-record with a key configured |
| `was recorded for '<other>', not …` | marker is bound to a different repo | re-record for this repo |
| `was found for <other>, not for <repo>` | marker is STALE **and** bound to a different repo | re-record for this repo |
| `normalises to an empty value` | the configured secret is whitespace-only, so the HMAC key would be the empty (public) string | set a real `AI_REVIEW_GATE_KEY` |
| `HMAC mismatch` | key or signed text differs; prints `sha256` prefixes of the text it checked | compare the prefix with the recording machine, then re-record |
| `is stale` | marker is for another head sha, and its `diff=` is absent, could not be hashed live, or matches neither the normalized nor the raw digest | re-run the review, re-record at the new head |
| `live diff hash could not be computed` | the REST diff fetch failed; a `diff=` marker fails closed rather than carrying forward | re-run the job once the API is reachable — the evidence may still be valid |
| `carries no well-formed 40-hex recorded sha` | marker's `@` field is not a full sha | re-record with a full 40-char head sha |
| `malformed marker` | signed, but the line shape drifted from what this gate accepts | update the gate/producer together |
