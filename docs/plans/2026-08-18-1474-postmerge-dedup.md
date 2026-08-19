---
title: "Post-Merge Dedup vs Push-to-Main Full Run — Implementation Plan"
type: engineering
domain: capability
doc_status: draft
created: 2026-08-18
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: github-actions-workflow, ci, post-merge-validation, python-ci
---

<!-- research-path: in-repo precedent (postmerge-verdict.js #1438, python-ci.yml concurrency/changes jobs) + live API verification 2026-08-19 — zero new third-party deps -->

# Post-Merge Dedup vs Push-to-Main Full Run — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal (#1474):** eliminate post-merge's redundant full-suite run on trees the python-ci `push:main` full run already validated green — while preserving the #1438 tri-state verdict + #559 issue-flagging contract and the branch-protection check semantics (aggregator reports success on skip).

**Team:** epistemic-team
**Role:** (none set)

**Key empirical facts (verified 2026-08-19, live API calls):**
- The `head_sha` filter on `GET actions/workflows/{file}/runs` returns 0 results for an **abbreviated** SHA — the poll must pass the full 40-char `context.sha`.
- Push runs serialize on `python-ci-${{ github.event_name }}-${{ github.ref }}` with `cancel-in-progress: false` — a run for our SHA can be queued behind a previous merge's run (queue + ~45-55m suite ⇒ poll cap 110m).
- 2 of the last 3 push runs were `conclusion=cancelled` (2026-08-19, runs 1989/1990) — dedup will often fall open on this repo's back-to-back-merge traffic; fall-open is correct by design.
- Merge commits follow `Merge pull request #N from …` (verified in git history) — parseable for PR attribution; squash-merge/direct-push commits → no attribution → fall-open + comment no-op.

### Pattern Research

> **Findings date:** 2026-08-19
> **Gate skipped** — zero third-party deps. In-repo precedent is complete: the #1438 verdict module pattern (`.github/scripts/postmerge-verdict.js` + `tests/test_postmerge_verdict.py`), the concurrency-queue semantics of python-ci's push group, and `actions/github-script@v7` require-from-workspace already proven in the existing comment step.

## Design

### Trigger / structure change (`post-merge-validation.yml`)

- `on:` `pull_request: [closed]` → **`push: branches: [main]`**.
- `permissions:` add **`actions: read`** (list python-ci workflow runs) — kept alongside existing `contents: read`, `issues: write`, `pull-requests: write`.
- New **`dedup-check` job** (always succeeds — every exception ⇒ `skip=false`, fall-open):
  - checkout (`ref: ${{ github.sha }}` for determinism)
  - `github-script` step:
    1. `parsePrNumberFromMergeMessage(context.payload.head_commit.message)`; if a PR number: `pulls.get` and verify `pr.merge_commit_sha === context.sha` (tree-attribution guard); parse `issue_number` from the PR body with the #559 regex (same one the flag step used on `pr.body`).
    2. Poll `actions.listWorkflowRuns({ workflow_id: 'python-ci.yml', event: 'push', head_sha: context.sha, per_page: 10 })` — take the run with the **max id** (re-runs supersede), break when `status === 'completed'`; sleep 30s between polls; **110-min cap**. **EMPTY-PAGE GRACE (verifier P2):** if no run appears for the first 5 polls (2.5 min — covers the run-creation race), fall open immediately — a never-appearing run (broken/disabled python-ci.yml) must not burn 110 min of billed idle runner time.
    3. `skip = dedupDecision(status, conclusion)` = `status === 'completed' && conclusion === 'success'`.
    4. Outputs: `skip`, `pr_number`, `issue_number`, `push_run_id`. All errors caught → `core.warning` + defaults (skip=false).
- **`validate` job** (`needs: dedup-check`, `if: always()` — the check that branch protection tracks):
  - **GATE POLARITY (verifier P1-1):** every suite step + the verdict-comment step gates on `if: needs.dedup-check.outputs.skip != 'true'` — NOT `== 'false'`. Rationale: if `dedup-check` fails outside its try/catch (checkout flake, action-download failure, runner loss), its outputs are **empty strings**; `'' != 'true'` → true → the full run still executes (fall-open). The `== 'false'` polarity would make `'' == 'false'` → false → suite silently skipped → **false-green check with zero tests** — the exact failure mode this design must exclude. The skip-comment step alone gates on `== 'true'`.
  - Checkout `ref:` `${{ github.event.pull_request.merge_commit_sha }}` → **`${{ github.sha }}`** (no pull_request payload on push; explicit ref also prevents a mid-run main advance from checking out a newer tree).
  - "Comment result on PR" step: gated `always() && skip != 'true'`; PR number / issue number from job outputs (env) instead of `context.payload.pull_request`; `sha8` from `context.sha`. `postmerge-verdict.js` imports **unchanged** — the #1438 contract is preserved verbatim. **EMPTY-PR GUARD (verifier P1-2):** when `pr_number` is empty (direct push to main — no "Merge pull request #N" attribution), the comment and issue-flag are NO-OPS (`core.info` + skip the API call) — otherwise `createComment({issue_number: ''})` would 4xx and spuriously red the check. Same guard applies to the skip-path step.
  - NEW skip-path step (runs when `skip == 'true'`): posts `buildSkipCommentBody({sha8, runUrl, pushRunId})` to the PR (no-op when `pr_number` empty); job succeeds ⇒ aggregator reports success (OpenTau pattern).
  - "Fail the check" step unchanged — fires only on `steps.tests.outcome == 'failure'`, impossible on the skip path (tests skipped, not failed).

### New module `.github/scripts/postmerge-dedup.js` (plain CJS, CLI dry-run — mirrors postmerge-verdict.js)

| Export | Purpose |
|---|---|
| `parsePrNumberFromMergeMessage(msg)` | `/Merge pull request #(\d+)/` → number \| null |
| `parseIssueNumberFromBody(body)` | `/(?:closes\|fixes\|resolves)\s+#(\d+)/i` (identical to the #559 flag regex) → number \| null |
| `dedupDecision(status, conclusion)` | `status === 'completed' && conclusion === 'success'` — the ONLY skip state; null/missing/unknown inputs → false |
| `buildSkipCommentBody({sha8, runUrl, pushRunId})` | "✅ covered by python-ci push run #N on the same tree — post-merge elided the redundant run (#1474)" |

CLI modes for the contract tests: `parse-pr "<msg>"`, `parse-issue "<body>"`, `decide "<status>" "<conclusion>"`, `skip-body "<sha8>" "<runUrl>" "<pushRunId>"` → JSON.

### Tests `tests/test_postmerge_dedup.py`

Subprocess CLI contract tests (same shape as `test_postmerge_verdict.py` — pure, no network/DB):
- parse-pr: merge commit → 1467; `Merge branch 'main'` → null; squash-format → null.
- parse-issue: Closes/Fixes/Resolves + case-insensitive → number; none → null.
- decide: (completed, success) → true; every other (failure/cancelled/neutral/skipped/in_progress/queued) → false; **null/missing/empty status & conclusion → false (verifier P2)**.
- skip-body: contains the run reference + "covered" wording.

### Files changed

| File | Change |
|---|---|
| `.github/workflows/post-merge-validation.yml` | trigger, permissions, dedup-check job, validate gating + ref + comment input refactor + skip step |
| `.github/scripts/postmerge-dedup.js` | NEW |
| `tests/test_postmerge_dedup.py` | NEW |
| `docs/plans/2026-08-18-1474-postmerge-dedup.md` | this plan |

### Verification checklist

1. YAML parses (`python3 -c "import yaml,sys; yaml.safe_load(open(...))"`); every new `if:` expression is well-formed GitHub expression syntax.
2. `bash -n` on the unchanged bash steps (install/reaper/tests) still passes.
3. Fall-open truth table exercised via the module CLI (`decide` cases) — skip ONLY on completed+success.
4. **Gate-polarity check (verifier P1-1):** every suite/verdict-comment gate in the YAML uses `!= 'true'` (empty output → fall-open); ONLY the skip-comment uses `== 'true'`. Grep-verified.
5. `uv run pytest tests/test_postmerge_verdict.py tests/test_postmerge_dedup.py -q` (no FalkorDB needed — pure subprocess).
6. No third-party deps added; no `postmerge-verdict.js` changes (contract frozen).

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| Poll burns idle runner minutes on fall-open | Directed mechanism (issue); cap 110m bounds it; C2 documented in scoping |
| Push run cancelled mid-poll | conclusion=cancelled ⇒ fall-open (truth table) |
| Re-run of a push run for the same SHA | max-id wins; its conclusion is authoritative |
| PR body unparsable / direct push | fall-open; comment/flag no-op; tests still run |
| `head_sha` filter quirk (abbreviated SHA) | always pass full `context.sha` (verified) |
