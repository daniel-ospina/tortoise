# AI Review Merge Gate

`main` branch protection requires the `ai-review-gate` status check on every
pull request. It replaces the human-approval requirement: merges proceed when
the code-review skill's AI review is recorded and all required checks are
green.

## How it works

- The `code-review` skill runs its review gate on the PR.
- When clean, the review is recorded and evidence is posted to the PR body by
  `record-review.sh`:

  ```
  review recorded: reviews/<PR>.json verdict=clean @ <full-sha>[ diff=<sha256-of-diff>] (<owner/repo>) sig=<hmac>
  ```

  The `diff=<sha256>` segment is optional (added producer-side by #2982) and is
  part of the SIGNED text. It records the sha256 of the PR's three-dot diff, so
  a review stays tied to the reviewed artifact rather than only the commit sha.

- The `ai-review-gate` workflow (`.github/workflows/ai-review-gate.yml`,
  `pull_request_target`) recomputes the HMAC signature with the
  `AI_REVIEW_GATE_KEY` repo secret (the agent machine holds the same key at
  `~/.pi/agent/.ai-review-gate-key`). The check passes only when the marker is
  fresh (recorded sha == head sha), signed, and bound to this PR and repo.

## Recording a review

```bash
record-review.sh <PR> <full-40-hex-head-sha> clean <owner/repo>
# verdict: clean | clean-micro
```

The marker is HMAC-signed, so a body edit cannot forge it. If the PR head
moves after a record (review-fix commits), re-run the code-review skill and
re-record at the new head.

## When the gate goes red

The gate distinguishes the failure causes in its `::error::` output, so the fix
is unambiguous:

| Message says | Cause | Fix |
|---|---|---|
| `No AI review evidence found` | no marker in the PR body | run `record-review.sh` |
| `is UNSIGNED` | marker has no ` sig=<hmac>` segment at all | re-record with a key configured |
| `was recorded for '<other>', not …` | marker is bound to a different repo | re-record for this repo |
| `HMAC mismatch` | key or signed text differs; prints `sha256` prefixes of the text it checked | compare the prefix with the recording machine, then re-record |
| `is stale` | marker is for another head sha | re-run the review, re-record at the new head |
| `carries no well-formed 40-hex recorded sha` | marker's `@` field is not a full sha | re-record with a full 40-char head sha |
| `malformed marker` | signed, but the line shape drifted from what this gate accepts | update the gate/producer together |
