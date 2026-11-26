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
import { existsSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync } from 'node:fs'
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
    // ⛔ A FILE THAT WAS COLLECTED BUT REPORTED ZERO ROWS. `collected.includes(file)`
    // is satisfied by a file whose worker died at IMPORT time: vitest still writes the
    // file into `testResults`, just with `assertionResults: []`, so the `pending` leg
    // has no rows to name and the partition is tautologically balanced (the reported
    // total never learned about the lost tests). MEASURED: `SIGKILL` after the import
    // block of `notificationPanel.dom.test.jsx` left this guard GREEN on
    // `{total 14, passed 14, success true}` with that file at 0 rows — a false PASS on
    // the leg that gates this PR. Names must be joined by ROWS, not assumed.
    //
    // ⛔ AND KEYED BY PATH, NOT BY BASENAME. `domTestFiles` is a FLAT readdir while
    // vitest's `include` is RECURSIVE, so a DOM test under `src/<dir>/` can share a
    // basename with a member of the population; keyed by name, last-wins would let the
    // crashed file inherit its twin's rows (false PASS, guard GREEN) or blame the
    // healthy twin for the crash (false RED). MEASURED: `src/zzz/notificationPanel.
    // dom.test.jsx` + the top-level file SIGKILLed at import left this guard GREEN on
    // `{total 15, passed 15}`. Resolved path cannot be confused for its twin.
    const rowsByPath = new Map()
    for (const r of report.testResults ?? []) {
      const resolved = realpathSync(r.name ?? '')
      assert.ok(!rowsByPath.has(resolved),
        `${basename(resolved)} appears TWICE in the DOM report (${resolved}) — `
        + 'rows cannot be attributed to a file')
      rowsByPath.set(resolved, (r.assertionResults ?? []).length)
    }
    for (const file of domTestFiles) {
      assert.ok((rowsByPath.get(realpathSync(join(here, file))) ?? 0) > 0,
        `${file} was COLLECTED but reported 0 test rows — its worker died at `
        + 'import/collection time, so every assertion in it was lost while the '
        + 'report still looked successful')
    }
    assert.deepEqual(failures, [], `DOM test failures:\n${failures.join('\n')}\n`
      + `--- stdout ---\n${childOutput(run.stdout)}`)
    assert.equal(report.numFailedTests ?? 0, 0,
      `vitest reported ${report.numFailedTests} failing DOM test(s)`)
    assert.equal(report.success, true,
      'vitest reported success:false — the DOM lane did not pass '
      + `(stdout tail: ${childOutput(run.stdout)})`)
    // ⛔ THE PARTITION MUST BE TOTAL. A vitest worker that OOMs mid-file still writes
    // a report — with `success: true`, the rows it managed to record, and NOTHING
    // saying the rest vanished. MEASURED: a 137 MB inspect string in one assertion
    // killed the worker over a REAL regression, and this guard passed on
    // `57 passed + 0 failed` against `62 collected`, so the CI leg stayed green while
    // five rows were never attributed. `numTotalTests` alone cannot catch that (it
    // still read 62); only the partition can.
    //
    // ⛔ …and the partition must count EVERY declared state, or it false-reds a
    // legitimate run. `numTotalTests` counts declared skips and todos;
    // `numPassedTests + numFailedTests` does NOT (measured: `test.skip` + `test.todo`
    // in one file → total 3, passed 1, pending 1, todo 1). Summing only pass+fail
    // therefore reds the gate on an all-green run with a parked `test.todo` and
    // blames a dead worker for it. Skips and todos are DECLARED, so they belong in
    // the identity.
    //
    // The `unruns` leg is what still catches a crash, and it does so PRECISELY: a
    // killed worker leaves rows at status `pending` (declared skips report `skipped`,
    // todos report `todo`), and MEASURED on the crash the top-level `numPendingTests`
    // read 0 while five rows sat at `pending` — so the row statuses, not the counters,
    // are what name them.
    const unruns = (report.testResults ?? [])
      .flatMap((r) => (r.assertionResults ?? []).filter((a) => a.status === 'pending')
        .map((a) => `${basename(r.name)} › ${a.fullName ?? a.title}`))
    assert.deepEqual(unruns, [],
      `DOM rows that never ran — a worker died mid-run and these were never reported:\n`
      + `${unruns.join('\n')}\n--- stdout tail ---\n${childOutput(run.stdout)}`)
    const accounted = (report.numPassedTests ?? 0) + (report.numFailedTests ?? 0)
      + (report.numPendingTests ?? 0) + (report.numTodoTests ?? 0)
    assert.equal(accounted, report.numTotalTests ?? 0,
      `the DOM lane collected ${report.numTotalTests} test(s) but accounted for only `
      + `${accounted} (pass+fail+pending+todo) — rows vanished from the report `
      + `(stdout tail: ${childOutput(run.stdout)})`)
    assert.ok((report.numTotalTests ?? 0) >= domTestFiles.length,
      `only ${report.numTotalTests} DOM test(s) ran for ${domTestFiles.length} file(s) — `
      + 'a file that collected nothing would otherwise pass this guard vacuously')
  } finally {
    rmSync(workDir, { recursive: true, force: true })
  }
})
