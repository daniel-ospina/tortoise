// harnessStatusCard.jsx — the per-harness capture-status rows of the Memory
// sources panel (Settings → Memory sources, and the wizard's Data-sources step).
//
// WHY THIS IS A MODULE, NOT INLINE JSX (#4915). Its deliverable is a RENDERED
// disclosure (#3700: "harness reported by your agent" beside the harness name),
// and the dashboard's `node --test` suite cannot observe a render: `main.jsx`
// is the whole application (browser globals, live session, CSS) and cannot be
// imported by a test. Before this file existed, the only guard that could see
// the disclosure was a static source pin (`harnessDisclosureTripwire.test.js`),
// which is a tripwire and not a proof — review of PR #4890 showed each
// hardening of it inviting the next evasion (a literal in the fragment's place,
// a prefix-matching binding, a shadowing declaration, a comment holding the
// same text, a mid-file swallow, CSS hiding the fragment).
//
// So the row is now an ordinary component with a NARROW prop surface (`state`,
// `sessionsOn`, `wizardHarness` — no effects, no fetches, no app state), which
// the DOM lane (`harnessDisclosureDom.test.jsx`, vitest + jsdom, `npm run
// test:dom`) renders and asserts: presence, absence, exact text, the
// alert/name-group linkage, and — because the config loads `index.css` into the
// document — that no rule HIDES it. The static tripwire stays as the cheap
// accretion guard it is, and no longer carries claims a source pattern cannot
// honour.
//
// `main.jsx` imports this module and renders it where the inline block used to
// be; the DOM output is unchanged. Copy still lives in `harnesses.js` /
// `captureStatus.js` — this module renders helpers' returns and authors none.
import {
  HARNESS_CAPTURE_INSTALL,
  HARNESS_CAPTURE_REASON,
  HARNESS_CAPTURE_SUPPORT,
  HARNESS_NAMES,
  HARNESS_ORDER,
} from './harnesses.js'
import {
  captureErrorForHarness,
  captureStatusForHarness,
  captureStatusLabelForHarness,
  harnessAttributionForHarness,
} from './captureStatus.js'

export function HarnessStatusCard({ state, sessionsOn, wizardHarness }) {
  const status = (h) => captureStatusForHarness(state, h)
  const lastError = (h) => captureErrorForHarness(state, h)
  // #3700: the per-row harness attribution — rendered beside the harness name.
  // Never inside the `role="alert"` failure sentence (see captureStatus.js).
  const harnessAttribution = (h) => harnessAttributionForHarness(state, h)

  return (
    <div className="harness-statuses">
      {HARNESS_ORDER.map((h) => {
        const st = status(h)
        const supported = !!HARNESS_CAPTURE_SUPPORT[h]
        const isCurrent = wizardHarness && h === wizardHarness
        // #2912 (review cycle 4 P2): a `wizardHarness === 'codexDesktop'`
        // alias used to live here, aliasing the Codex Desktop surface onto
        // the 'codex' row. It was unreachable, and it stays so:
        // HARNESS_ORDER has no codexDesktop row, so none of this
        // component's THREE call sites can hand it that value — Settings
        // passes null on purpose, the Data-sources wizard mount passes the
        // connect-step pick (`harnessPickEstablished ? wizardHarness :
        // null`), and the LEGACY_WIZARD_ARCHIVED rollback block is fed
        // solely by HARNESS_ORDER leaves. If a surface leaf ever reaches
        // this row, wire the alias through a unit-tested helper instead of
        // a source-grep tripwire.
        return (
          <div key={h} className={`harness-status status-${st}${isCurrent ? ' current' : ''}`}>
            {/* review P2-5: the polite live region is scoped to the HEAD
                — the harness name, its disclosure and the state word — so
                it still does not announce the whole multi-line snippet
                below. That is a deliberate OVERRIDE of P2-5 (PR #1830),
                which moved the region OFF the container and ONTO the
                state pill: its hazard was the multi-line snippet, and the
                #3700 disclosure must be announced with the state it
                qualifies. Moving the region to the head keeps the snippet
                out while covering the name and the disclosure.
                review P2-3: unsupported harnesses render the REASON,
                with no pill and no failure line (an unsupported harness
                has no install path, so a per-harness claim would
                contradict the reason). That guard is what
                `harnessAttributionForHarness` mirrors, so an unsupported
                row makes no per-harness claim and needs no disclosure.
                #3700: the row NAMES a harness whose value is a caller
                declaration, so the disclosure (`harnessAttribution`) sits
                in the same group as the name — one flex item, so
                `space-between` separates the name group from the pill —
                and the state word stays plain; the attribution is what
                keeps the row from reading as a server-observed harness. */}
            <div className="harness-status-head" aria-live="polite">
              <span className="harness-status-name" id={`harness-note-${h}`}>
                <strong>{HARNESS_NAMES[h]}</strong>
                {harnessAttribution(h) && (
                  <span className="dim small">· {harnessAttribution(h)}</span>
                )}
              </span>
              {supported && <span className="capture-state">{captureStatusLabelForHarness(state, h)}</span>}
            </div>
            {!supported && <p className="dim small">{HARNESS_CAPTURE_REASON[h]}</p>}
            {supported && st === 'install-pending' && sessionsOn && (
              <pre className="snippet">{HARNESS_CAPTURE_INSTALL[h]}</pre>
            )}
            {/* #3700: the alert's description is the row's name group
                (name + disclosure). */}
            {supported && lastError(h) && <p className="error small" role="alert" aria-describedby={`harness-note-${h}`}>{lastError(h)}</p>}
          </div>
        )
      })}
    </div>
  )
}
