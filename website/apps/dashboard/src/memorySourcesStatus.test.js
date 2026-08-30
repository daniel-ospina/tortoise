// memorySourcesStatus.test.js — run with node --test (Node 20+, zero deps:
// the derivations are pure, no jsdom/React needed) (#1894).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  docsIndexedLabel,
  fmtElapsed,
  formatRelativeTime,
  jobElapsedSecs,
  jobStatusLine,
} from './memorySourcesStatus.js'

const NOW = Date.parse('2026-08-28T12:00:00Z')

// ── formatRelativeTime ───────────────────────────────────────────

test('fresh (<60s) renders "just now"', () => {
  assert.equal(formatRelativeTime('2026-08-28T11:59:30Z', NOW), 'just now')
})
test('minutes render "N min ago"', () => {
  assert.equal(formatRelativeTime('2026-08-28T11:58:00Z', NOW), '2 min ago')
})
test('hours render "N hr ago"', () => {
  assert.equal(formatRelativeTime('2026-08-28T09:00:00Z', NOW), '3 hr ago')
})
test('tier boundary at exactly 60s renders "1 min ago" (not "just now")', () => {
  assert.equal(formatRelativeTime('2026-08-28T11:59:00Z', NOW), '1 min ago')
  assert.equal(formatRelativeTime('2026-08-28T11:59:59Z', NOW), 'just now')
})
test('tier boundary at exactly 3600s renders "1 hr ago" (not "60 min ago")', () => {
  assert.equal(formatRelativeTime('2026-08-28T11:00:00Z', NOW), '1 hr ago')
  assert.equal(formatRelativeTime('2026-08-28T11:00:01Z', NOW), '59 min ago')
})
test('stale (>24h) renders a non-relative string (locale-independent)', () => {
  const out = formatRelativeTime('2026-08-20T09:00:00Z', NOW)
  assert.equal(typeof out, 'string')
  assert.ok(out.length > 0)
  // a broken <86400 branch would render "N min/hr ago" for an 8-day-old
  // stamp — the stale branch must NOT produce any relative-time string
  // (also excludes "N days ago" via the /ago/i probe).
  assert.ok(!/ago/i.test(out),
            `stale stamp must not render as relative time: ${out}`)
  assert.match(out, /\d{4}/)  // date-ish shape (toLocaleDateString has a year)
})
test('absent timestamp → null', () => {
  assert.equal(formatRelativeTime(null, NOW), null)
  assert.equal(formatRelativeTime(undefined, NOW), null)
  assert.equal(formatRelativeTime('', NOW), null)
})
test('invalid timestamp → null', () => {
  assert.equal(formatRelativeTime('not-a-date', NOW), null)
})
test('missing nowMs (ticker not yet hydrated) → null', () => {
  assert.equal(formatRelativeTime('2026-08-28T11:59:30Z', undefined), null)
})

// ── docsIndexedLabel ─────────────────────────────────────────────

test('indexed + timestamp → "Indexed · <rel time>"', () => {
  const label = docsIndexedLabel(
    { github_docs_indexed: true, github_docs_indexed_at: '2026-08-28T11:58:00Z' },
    NOW)
  assert.equal(label, 'Indexed · 2 min ago')
})
test('indexed, no timestamp → "Indexed" (legacy team — honest, no fabricated time)', () => {
  assert.equal(docsIndexedLabel({ github_docs_indexed: true }, NOW), 'Indexed')
})
test('not indexed → null', () => {
  assert.equal(docsIndexedLabel({ github_docs_indexed: false }, NOW), null)
  assert.equal(docsIndexedLabel({}, NOW), null)
  assert.equal(docsIndexedLabel(null, NOW), null)
})
test('disconnected-but-indexed still returns the label (no githubConnected dependency)', () => {
  const label = docsIndexedLabel(
    { github_docs_indexed: true, github_docs_indexed_at: '2026-08-28T11:58:00Z',
      github_connected: false },
    NOW)
  assert.equal(label, 'Indexed · 2 min ago')
})

// ── jobElapsedSecs / fmtElapsed ──────────────────────────────────

test('elapsed from epoch started_at', () => {
  assert.equal(jobElapsedSecs({ started_at: NOW / 1000 - 120 }, NOW), 120)
})
test('elapsed falls back to created_at when started_at missing', () => {
  assert.equal(jobElapsedSecs({ created_at: NOW / 1000 - 45 }, NOW), 45)
})
test('missing timestamps (client-minted {status:starting}) → null', () => {
  assert.equal(jobElapsedSecs({ status: 'starting' }, NOW), null)
  assert.equal(jobElapsedSecs(null, NOW), null)
})
test('future started_at (clock skew) clamps to 0s, never negative', () => {
  assert.equal(jobElapsedSecs({ started_at: NOW / 1000 + 60 }, NOW), 0)
})
test('fmtElapsed renders m s / s', () => {
  assert.equal(fmtElapsed(60), '1m 0s')
  assert.equal(fmtElapsed(7), '7s')
  assert.equal(fmtElapsed(null), null)
})
test('fmtElapsed 59s stays seconds, 61s flips to minutes', () => {
  assert.equal(fmtElapsed(59), '59s')
  assert.equal(fmtElapsed(61), '1m 1s')
})

// ── jobStatusLine ────────────────────────────────────────────────

test('progress 0 with NO repos fields → elapsed only, NO ETA (real backend shape pre-first-write)', () => {
  assert.equal(jobStatusLine({ status: 'started', progress: 0, started_at: NOW / 1000 - 30 }, NOW),
               '30s')
})
test('progress 0 WITH repos fields → elapsed + "0/N repos", still NO ETA', () => {
  assert.equal(jobStatusLine(
    { status: 'started', progress: 0, started_at: NOW / 1000 - 30,
      repos_processed: 0, repos_total: 2 }, NOW),
    '30s · 0/2 repos')
})
// #1894 review F1: the docs job is PRE-SEEDED with repos_processed=0 /
// repos_total=0, so the pre-first-write poll shape renders elapsed-ONLY —
// a fabricated-looking "0/0 repos" count would contradict the module's
// own "never fabricated" contract (github mints with no repos fields).
test('total 0 (docs pre-seeded 0/0 shape) → elapsed only, no count', () => {
  assert.equal(jobStatusLine(
    { status: 'started', progress: 0, started_at: NOW / 1000 - 30,
      repos_processed: 0, repos_total: 0 }, NOW),
    '30s')
})
test('progress 50 with 60s elapsed → elapsed + repos + ETA', () => {
  assert.equal(jobStatusLine(
    { status: 'started', progress: 50, started_at: NOW / 1000 - 60,
      repos_processed: 1, repos_total: 2 }, NOW),
    '1m 0s · 1/2 repos · ~1m 0s left')
})
test('progress 50 with 120s elapsed → multi-minute ETA formatting', () => {
  assert.equal(jobStatusLine(
    { status: 'started', progress: 50, started_at: NOW / 1000 - 120,
      repos_processed: 1, repos_total: 2 }, NOW),
    '2m 0s · 1/2 repos · ~2m 0s left')
})
test('fmtElapsed 120 → multi-minute form', () => {
  assert.equal(fmtElapsed(120), '2m 0s')
})
test('missing repos fields → elapsed only', () => {
  assert.equal(jobStatusLine({ status: 'started', progress: 50, started_at: NOW / 1000 - 60 }, NOW),
               '1m 0s')
})
test('missing timestamps → null (nothing to render)', () => {
  assert.equal(jobStatusLine({ status: 'starting' }, NOW), null)
  assert.equal(jobStatusLine(null, NOW), null)
})
test('progress >= 100 → NO ETA suffix (single-repo live write flash)', () => {
  assert.equal(jobStatusLine(
    { status: 'started', progress: 100, started_at: NOW / 1000 - 60,
      repos_processed: 1, repos_total: 1 }, NOW),
    '1m 0s · 1/1 repos')
})
test('elapsed < 5s suppresses the ETA (rate too noisy)', () => {
  assert.equal(jobStatusLine(
    { status: 'started', progress: 50, started_at: NOW / 1000 - 3,
      repos_processed: 1, repos_total: 2 }, NOW),
    '3s · 1/2 repos')
})
test('elapsed exactly 5s still suppresses the ETA (strict > 5 gate)', () => {
  assert.equal(jobStatusLine(
    { status: 'started', progress: 50, started_at: NOW / 1000 - 5,
      repos_processed: 1, repos_total: 2 }, NOW),
    '5s · 1/2 repos')
})
// #1894 review F2: ETA floor — a per-repo extrapolation with a tiny
// remainder (progress 99 + one slow tail repo) must NOT read "~0s left"
// while the walk runs on (fake precision). Any ETA < 5s is suppressed.
test('progress 99 with a slow tail repo → NO tiny-ETA countdown', () => {
  assert.equal(jobStatusLine(
    { status: 'started', progress: 99, started_at: NOW / 1000 - 300,
      repos_processed: 99, repos_total: 100 }, NOW),
    '5m 0s · 99/100 repos')
})
test('extrapolated ETA below the 5s floor → suppressed (4s case)', () => {
  assert.equal(jobStatusLine(
    { status: 'started', progress: 90, started_at: NOW / 1000 - 40,
      repos_processed: 9, repos_total: 10 }, NOW),
    '40s · 9/10 repos')
})
test('extrapolated ETA at exactly 5s still renders (>= 5 gate)', () => {
  assert.equal(jobStatusLine(
    { status: 'started', progress: 50, started_at: NOW / 1000 - 6,
      repos_processed: 1, repos_total: 2 }, NOW),
    '6s · 1/2 repos · ~6s left')
})

// ── CSS-rule assertion (#1894 render-layer gate — the rule lives in
// index.css; a regression to the dimmed disabled-switch would fail here) ──
test('index.css keeps the disabled-but-on switch full-opacity', () => {
  const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8')
  const ruleIdx = css.indexOf(".switch[disabled][data-on='true'][data-locked-on]")
  assert.ok(ruleIdx !== -1, '.switch[disabled][data-on=\'true\'][data-locked-on] rule must exist in index.css')
  // POSITIVE pin: the terminal docs switch must render at full opacity — any
  // dim value (0.4/0.5/0.6/shorthand .6) fails the test. A negative probe on
  // one literal (0.6) would let every other dim regression pass while the
  // visual bug (an on-state switch that looks off) persists.
  const blockEnd = css.indexOf('}', ruleIdx)
  const block = css.slice(ruleIdx, blockEnd)
  assert.match(block, /opacity:\s*1\b/,
               `locked-on rule must set full opacity, got: ${block}`)
})
