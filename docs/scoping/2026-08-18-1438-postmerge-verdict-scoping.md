---
title: "#1438 post-merge comment tri-state verdict — Scope"
type: decisions
issue: "#1438"
date: 2026-08-18
status: scoping-phase5
revision: 1 — initial
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-18
---

# Scoping — #1438: post-merge comment step needs a tri-state verdict

> issue-scoping v5.1, lightweight time-boxed pass (complexity: standard). Filed 2026-08-18.
> Baseline: today's comment step branches binary on `steps.tests.outcome` — every
> timeout-kill (job cap → `cancelled`, or #1430 watchdog kill → rc 124/137/2)
> posts "❌ the merged change broke the test suite" and flags the linked issue.

## Confirmed Problem

`post-merge-validation.yml`'s "Comment result on PR" step is a binary pass/fail:
`TESTS_OUTCOME === 'success'` → PASSED comment; anything else → FAILED "broke the
suite" comment + linked-issue flag. A timeout is **not** evidence the merged change
broke anything — the suite simply didn't finish. Verified today: run 32104818556 on
PR #1379 posted the FAILED comment with `TESTS_OUTCOME=cancelled` (60m job cap).

## Key discovery (problem-diverge)

The distinguishing signal already exists — the watchdog exit codes from python-ci's
proven pattern (and #1430's transplant): **124** (SIGINT kill), **137** (SIGKILL after
INT ignored), **2** (pytest's own interrupt summary). A real suite failure is rc **1**
(or any other nonzero). Today `cancelled` is the observable for the job-cap kill.
So the verdict function is: `outcome × exit-code → passed | timed-out | failed`.

## Product decision (the "Research Needed" from the issue)

- **Timeout-kill / cancelled** → ⚠️ "validation did not complete — see run" (names the
  timeout, points at the WATCHDOG banner in the run log), **no** linked-issue flag.
  A timeout is not evidence of breakage; flagging the issue would be a false accusation.
- **Real failure (rc 1 +)** → ❌ keeps today's "broke the suite" body **and** the
  linked-issue flag. Unchanged behavior (issue indicator b).
- Warn-only workflow → no gate impact either way.

## Implementation shape

1. Extract the verdict logic into a testable plain-CJS module
   `.github/scripts/postmerge-verdict.js` (`computeVerdict(outcome, exitCode)`,
   `buildCommentBody(verdict, {sha8, runUrl})`, `shouldFlagLinkedIssue(verdict)`)
   + a `node <script> <outcome> [exitCode]` CLI dry-run mode that emits JSON
   `{verdict, body, flagIssue}`.
2. The `tests` step writes its real exit code as a step output
   (`echo "exit_code=$rc" >> "$GITHUB_OUTPUT"` under `set +e`, then `exit $rc`) —
   this is the piece #1430's watchdog must keep writing (noted in a comment).
3. The github-script comment step `require()`s the module and branches in three
   states: success → passed; cancelled → timed-out; failure + 124/137/2 → timed-out;
   anything else → failed.
4. Contract tests `tests/test_postmerge_verdict.py` shell out to the CLI with mocked
   TESTS_OUTCOME/exit-code inputs and assert verdict + emitted comment body + the
   flag-issue decision (issue's Verification checklist: dry-run against mocked inputs).

## Verdict matrix (single source of truth)

| outcome  | exit_code     | verdict    | flag issue |
|----------|---------------|------------|------------|
| success  | any/0         | passed     | no         |
| failure  | 124/137/2     | timed-out  | no         |
| cancelled| (step killed) | timed-out  | no         |
| failure  | 1/other/missing | failed   | yes        |
| skipped  | –             | failed     | yes (conservative) |

## Out of scope

- #1430's in-step watchdog (separate issue) — this PR only adds the `exit_code`
  output contract the watchdog must preserve + the comment-side tri-state.
- Any gate behavior (warn-only workflow).
