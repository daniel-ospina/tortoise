// ⚠️ ENFORCEMENT PROBE — agent-infra#1128 — MUST BE DELETED.
// This file is injected deliberately and only so that the required check
// `dashboard-js-tests / lint` reports a `no-undef` ERROR and goes RED.
// `eslint.config.js` sets `no-undef` to `error` over `src/**/*.{js,jsx}`,
// so a correctly-routed reusable node-ci lint job must report this line.
// If the leg stays green with this file present, the gate is still not linting.
export const probe1128Undefined = __probe1128UndefinedFromIssue1128__
