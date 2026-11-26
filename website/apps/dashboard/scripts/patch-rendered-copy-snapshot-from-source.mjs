// Patch wizardRenderedCopy.snapshot.json textually from the SOURCE constant.
//
// The generator (scripts/gen-wizard-rendered-copy-snapshot.mjs) renders main.jsx
// through jsxSourceProbe, which needs `esbuild`; this worktree has no
// node_modules. The only field that moved is the Pi capture-install snippet,
// which main.jsx interpolates from PI_CAPTURE_INSTALL — so the snapshot's
// rendered text can be re-derived from the SAME source string the probe would
// have rendered, without a build. Every occurrence is replaced (the pi prompt
// appears in both key modes), and the file's formatting is preserved because
// the splice is inside one JSON string value.
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { PI_CAPTURE_INSTALL } from '../src/harnesses.js'

const here = dirname(fileURLToPath(import.meta.url))
const snapPath = join(here, '..', 'src', 'wizardRenderedCopy.snapshot.json')
const START = 'mkdir -p ~/.pi/agent/extensions'
const END = '# Backfill past Pi sessions'

const snippet = PI_CAPTURE_INSTALL
const s = snippet.indexOf(START)
const e = snippet.indexOf(END)
if (s < 0 || e < 0 || e < s) {
  throw new Error('the PI_CAPTURE_INSTALL snippet no longer carries the anchors — refusing to write')
}
const newRegion = snippet.slice(s, e)
const escapedNew = JSON.stringify(newRegion).slice(1, -1)

let content = readFileSync(snapPath, 'utf8')
let replaced = 0
let cursor = 0
for (;;) {
  const from = content.indexOf(START, cursor)
  if (from < 0) break
  const to = content.indexOf(END, from)
  if (to < 0) throw new Error('anchor mismatch inside the snapshot — refusing to write')
  content = content.slice(0, from) + escapedNew + content.slice(to)
  cursor = from + escapedNew.length // the replacement starts with START: never re-scan it
  replaced += 1
}
if (replaced === 0) throw new Error('no occurrence of the snippet found — refusing to write')
JSON.parse(content) // fail loudly if the splice broke the JSON
writeFileSync(snapPath, content)
console.log(`patched ${replaced} occurrence(s)`)
