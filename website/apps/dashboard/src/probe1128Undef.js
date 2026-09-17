// PROBE FILE — agent-infra#1128 acceptance artifact. NOT PRODUCT CODE.
//
// `__probeUndefinedFromIssue1128__` is never declared, so the dashboard's
// eslint.config.js (`no-undef` at `error`) reports it. The point of the probe
// is that `dashboard-js-tests` -> node-ci.yml's `lint` job must go RED on it.
// Before the #1128 fix the job ran at the repo root, found no config, printed
// "No ESLint config found — skipping lint" and certified nothing.
//
// Delete this file with this probe branch.
export const probe1128Undef = __probeUndefinedFromIssue1128__
