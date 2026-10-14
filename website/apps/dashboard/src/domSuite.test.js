// domSuite.test.js — #4915: the DOM lane is wired INTO the primary suite.
//
// WHY A TEST, AND NOT A LINE OF CI YAML. The dashboard's CI leg runs ONE command
// — `npm ci && npm run build && node --test src/*.test.js` — so a second runner
// needs either that command changed (a `.github/workflows/` edit) or a way for
// the primary command to reach it. The first route is not available to the lanes
// that open these PRs: the session credential has no `workflow` scope and the
// push of a workflow change is REJECTED (the same constraint `tools/
// skip-guard.py` records for its own lane signal — see the `TORTOISE_TEST_
// EXPECT_URI` alias). The second route is also the one that CANNOT SILENTLY
// UN-WIRE: a YAML `test-command` only says what CI runs, while this test makes an
// unrun DOM lane RED the primary command — on a renamed file, a narrowed
// `include`, a missing jsdom/vitest install, or a runner that collects nothing.
//
// WHAT IT ASSERTS. `vitest run` (the DOM lane, `npm run test:dom`) is executed as
// a child process with the package as its cwd, and the JSON report it writes is
// read: every `src/*.test.jsx` file on disk must appear in the collected set (a
// file the config stopped selecting fails here rather than passing vacuously),
// zero tests may fail, and the run must have collected at least one test per
// file. The child's own output is quoted in the failure message, so a red here is
// diagnosable without re-running anything.
//
// NOT RECURSIVE. Vitest's `include` is `src/**/*.test.jsx` (vite.config.js) and
// this file is `.js`, so the child run cannot reach this file. Widen that glob to
// `*.test.js` and this test will fork-bomb itself — the comment in vite.config.js
// says the same thing from the other side.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { basename, dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const packageDir = join(here, '..')
const vitestBin = join(packageDir, 'node_modules', '.bin', 'vitest')

// The lane's population, read from disk rather than from the runner — that is
// what makes a file the config dropped observable.
const domTestFiles = readdirSync(here).filter((f) => f.endsWith('.test.jsx')).sort()

test('#4915: the DOM lane runs with the primary suite, and collects every DOM file', () => {
  assert.ok(domTestFiles.length > 0,
    'no `src/*.test.jsx` files exist — the DOM lane has no tests, so this guard would pass vacuously')
  assert.ok(existsSync(vitestBin),
    `the DOM runner is not installed (${vitestBin}) — the lane is dead. Install with \`npm ci\` in website/apps/dashboard`)

  const workDir = mkdtempSync(join(tmpdir(), 'dashboard-dom-lane-'))
  const reportPath = join(workDir, 'vitest-report.json')
  let run
  try {
    run = spawnSync(vitestBin, ['run', '--reporter=json', `--outputFile=${reportPath}`], {
      cwd: packageDir,
      encoding: 'utf8',
      timeout: 300_000,
    })
    const childOutput = (stream) => (stream || '').trim().split('\n').slice(-25).join('\n')
    if (!existsSync(reportPath)) {
      assert.fail('the DOM lane wrote no report — vitest did not run to completion.\n'
        + `status: ${run.status} signal: ${run.signal}\n`
        + `--- stdout ---\n${childOutput(run.stdout)}\n--- stderr ---\n${childOutput(run.stderr)}`)
    }
    const report = JSON.parse(readFileSync(reportPath, 'utf8'))
    const collected = (report.testResults ?? []).map((r) => basename(r.name ?? ''))
    const failures = (report.testResults ?? [])
      .flatMap((r) => (r.assertionResults ?? []).filter((a) => a.status === 'failed')
        .map((a) => `${basename(r.name)} › ${a.fullName ?? a.title}`))

    for (const file of domTestFiles) {
      assert.ok(collected.includes(file),
        `${file} was NOT collected by the DOM runner — vitest's \`include\` (vite.config.js) no longer `
        + `selects it, so its assertions are not running. Collected: ${collected.join(', ') || '(none)'}`)
    }
    assert.deepEqual(failures, [], `DOM test failures:\n${failures.join('\n')}\n`
      + `--- stdout ---\n${childOutput(run.stdout)}`)
    assert.equal(report.numFailedTests ?? 0, 0,
      `vitest reported ${report.numFailedTests} failing DOM test(s)`)
    assert.equal(report.success, true,
      'vitest reported success:false — the DOM lane did not pass '
      + `(stdout tail: ${childOutput(run.stdout)})`)
    assert.ok((report.numTotalTests ?? 0) >= domTestFiles.length,
      `only ${report.numTotalTests} DOM test(s) ran for ${domTestFiles.length} file(s) — `
      + 'a file that collected nothing would otherwise pass this guard vacuously')
  } finally {
    rmSync(workDir, { recursive: true, force: true })
  }
})
