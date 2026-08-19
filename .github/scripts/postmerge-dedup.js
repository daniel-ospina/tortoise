#!/usr/bin/env node
'use strict';

/**
 * Sound-dedup decision + attribution parsing for post-merge-validation (#1474).
 *
 * post-merge used to re-run the FULL suite (lean install '.[embeddings]') on
 * every merged tree — but python-ci.yml's push:main run already validates the
 * SAME tree with the full matrix. This module decides whether that run
 * concluded GREEN, so the workflow can skip the redundant pytest run and only
 * comment "covered" (aggregator reports success; the #1438 tri-state verdict +
 * #559 issue-flagging contract is preserved on the fall-open path).
 *
 * Dedup rule (the ONLY skip state): the python-ci push run for this exact
 * tree (event=push, head_sha = full pushed SHA) has status 'completed' AND
 * conclusion 'success'. Every other state — failure, cancelled, neutral,
 * skipped, queued, in_progress past the poll cap, run never appearing, API
 * error — falls open to the full run. Verified empirically 2026-08-19: the
 * runs endpoint's head_sha filter requires the FULL 40-char SHA (abbreviated
 * SHAs return 0 results).
 *
 * PR attribution (best-effort — affects comment/flag only, never the skip
 * decision): on push:main there is no pull_request payload, so the PR number
 * is parsed from the merge commit message ("Merge pull request #N from …")
 * and the linked issue from the PR body (same regex the #559 flag used).
 *
 * Exported as plain CJS (repo convention) with a CLI dry-run mode
 * (`node postmerge-dedup.js <mode> [args]` -> JSON) exercised by
 * tests/test_postmerge_dedup.py with mocked inputs.
 */

const MERGE_PR_RE = /^Merge pull request #(\d+)\b/;
const ISSUE_RE = /(?:closes|fixes|resolves)\s+#(\d+)/i;

/**
 * @param {string|null|undefined} message merge commit message
 * @returns {number|null} PR number parsed from "Merge pull request #N from …", or null
 */
function parsePrNumberFromMergeMessage(message) {
  if (!message) return null;
  const m = String(message).match(MERGE_PR_RE);
  return m ? parseInt(m[1], 10) : null;
}

/**
 * @param {string|null|undefined} body PR body
 * @returns {number|null} linked issue number ("Closes #N" / "Fixes #N" / "Resolves #N"), or null
 */
function parseIssueNumberFromBody(body) {
  if (!body) return null;
  const m = String(body).match(ISSUE_RE);
  return m ? parseInt(m[1], 10) : null;
}

/**
 * The ONLY skip state: completed + success. null/missing/unknown inputs
 * never skip (they fall open).
 *
 * @param {string|null|undefined} status      workflow-run status: queued|in_progress|completed|…
 * @param {string|null|undefined} conclusion  workflow-run conclusion: success|failure|cancelled|neutral|…
 * @returns {boolean} true only when the same-tree push run was green
 */
function dedupDecision(status, conclusion) {
  return status === 'completed' && conclusion === 'success';
}

/**
 * @param {{sha8: string, runUrl: string, pushRunId: string|number}} ctx
 * @returns {string} the comment posted on the PR when the run is skipped
 */
function buildSkipCommentBody(ctx) {
  const { sha8, runUrl, pushRunId } = ctx;
  return (
    `✅ **Post-merge validation SKIPPED — covered by python-ci push run #${pushRunId}** — ` +
    `tree ${sha8} was already validated green by the push-to-main full run (${runUrl}); ` +
    `the redundant post-merge run was elided (#1474).`
  );
}

// CLI dry-run mode (test contract + manual preview):
//   node postmerge-dedup.js parse-pr   "<merge commit message>"
//   node postmerge-dedup.js parse-issue "<pr body>"
//   node postmerge-dedup.js decide     [<status> [<conclusion>]]
//   node postmerge-dedup.js skip-body  <sha8> <runUrl> <pushRunId>
if (require.main === module) {
  const [, , mode, a, b, c] = process.argv;
  let out;
  switch (mode) {
    case 'parse-pr':
      out = { pr_number: parsePrNumberFromMergeMessage(a) };
      break;
    case 'parse-issue':
      out = { issue_number: parseIssueNumberFromBody(a) };
      break;
    case 'decide':
      out = { skip: dedupDecision(a, b) };
      break;
    case 'skip-body':
      out = { body: buildSkipCommentBody({ sha8: a, runUrl: b, pushRunId: c }) };
      break;
    default:
      throw new Error(`unknown mode: ${mode}`);
  }
  process.stdout.write(`${JSON.stringify(out, null, 2)}\n`);
}

module.exports = {
  parsePrNumberFromMergeMessage,
  parseIssueNumberFromBody,
  dedupDecision,
  buildSkipCommentBody,
};
