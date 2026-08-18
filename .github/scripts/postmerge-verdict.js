#!/usr/bin/env node
'use strict';

/**
 * Tri-state verdict for the post-merge-validation comment step (#1438).
 *
 * The comment step previously branched binary on the tests step's OUTCOME:
 * success -> "PASSED", anything else (including timeouts) -> "the merged
 * change broke the test suite" + linked-issue flag. A timeout is NOT evidence
 * the merged change broke anything — the suite simply didn't finish.
 *
 * This module computes the three-state verdict from the step's outcome AND
 * its real exit code:
 *
 *   success                      -> passed      (no issue flag)
 *   failure + rc 124/137/2       -> timed-out   (watchdog kill, no issue flag)
 *   cancelled                    -> timed-out   (runner/job-cap kill, no flag)
 *   failure + any other/missing  -> failed      (real breakage, issue flag)
 *
 * The rc 124/137/2 set mirrors python-ci.yml's watchdog contract (also #1430's
 * post-merge transplant): 124 = timeout SIGINT kill, 137 = -k 10 SIGKILL after
 * pytest ignored INT mid-test, 2 = pytest's own SIGINT summary.
 *
 * Exported as plain CJS (repo convention: scripts are CJS, no build step) so
 * the github-script step can `require()` it, and it carries a CLI dry-run mode
 * (`node postmerge-verdict.js <outcome> [exitCode]` -> JSON {verdict, body,
 * flagIssue}) exercised by tests/test_postmerge_verdict.py with mocked inputs.
 */

const VERDICTS = Object.freeze({
  PASSED: 'passed',
  TIMED_OUT: 'timed-out',
  FAILED: 'failed',
});

const TIMEOUT_EXIT_CODES = new Set(['124', '137', '2']);

/** Normalize an exit-code input (string step output) to a trimmed string or null. */
function normalizeExitCode(exitCode) {
  if (exitCode === undefined || exitCode === null) return null;
  const s = String(exitCode).trim();
  return s === '' ? null : s;
}

/**
 * @param {string|undefined} outcome  GitHub step outcome: success|failure|cancelled|skipped
 * @param {string|undefined} exitCode real tests-step exit code (step output), may be missing
 * @returns {'passed'|'timed-out'|'failed'}
 */
function computeVerdict(outcome, exitCode) {
  if (outcome === 'success') return VERDICTS.PASSED;
  // Runner-level kill (60m job cap, manual cancel) — the step never completed.
  // Not evidence of breakage, so it reads as a timeout, never "broke the suite".
  if (outcome === 'cancelled') return VERDICTS.TIMED_OUT;
  const code = normalizeExitCode(exitCode);
  if (outcome === 'failure' && code !== null && TIMEOUT_EXIT_CODES.has(code)) {
    return VERDICTS.TIMED_OUT;
  }
  // Conservative fallback: an unknown/missing nonzero status is a real failure.
  // A timeout is the only thing that downgrades the verdict.
  return VERDICTS.FAILED;
}

/** The linked issue is only flagged on a real failure — never on a timeout. */
function shouldFlagLinkedIssue(verdict) {
  return verdict === VERDICTS.FAILED;
}

/**
 * @param {'passed'|'timed-out'|'failed'} verdict
 * @param {{sha8: string, runUrl: string}} ctx
 * @returns {string} the comment body posted on the PR
 */
function buildCommentBody(verdict, ctx) {
  const { sha8, runUrl } = ctx;
  switch (verdict) {
    case VERDICTS.PASSED:
      return `✅ **Post-merge validation PASSED** — merge ${sha8} verified on main.`;
    case VERDICTS.TIMED_OUT:
      return `⚠️ **Post-merge validation did not complete** — the run was cut short before the suite finished (see run: ${runUrl}). A timeout is not evidence the merged change broke the suite; the WATCHDOG banner in the run log shows how far it got.`;
    default:
      return `❌ **Post-merge validation FAILED** — the merged change broke the test suite (see run: ${runUrl}). The linked issue must NOT be considered done until this is resolved.`;
  }
}

// CLI dry-run mode (test contract + manual preview):
//   node postmerge-verdict.js <outcome> [exitCode]
if (require.main === module) {
  const [, , outcome, exitCode] = process.argv;
  const verdict = computeVerdict(outcome, exitCode);
  const ctx = {
    sha8: 'deadbeef',
    runUrl: 'https://github.com/owner/repo/actions/runs/123',
  };
  process.stdout.write(`${JSON.stringify({
    verdict,
    body: buildCommentBody(verdict, ctx),
    flagIssue: shouldFlagLinkedIssue(verdict),
  }, null, 2)}\n`);
}

module.exports = {
  VERDICTS,
  TIMEOUT_EXIT_CODES,
  computeVerdict,
  shouldFlagLinkedIssue,
  buildCommentBody,
};
