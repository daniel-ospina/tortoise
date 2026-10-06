// eslint.config.js — dashboard static analysis for the use-before-define class (#3694).
//
// WHY THIS EXISTS: the dashboard had no JS lint config at all, and a
// use-before-define defect class has recurred three times, each caught late or
// not at all:
//
//   1. #2709 — an effect deps array referencing a later-declared `const`
//      (`harnessKey`, declared ~4,500 lines below the effect). A deps array is
//      evaluated eagerly during render, so this white-screened the dashboard for
//      every signed-in user. Covered only by a hand-rolled text tripwire.
//   2. A `const` declared inside a `try` and read from its `catch`
//      (refreshOnboarding), fixed only after a reviewer executed the function.
//   3. #3687 — `loadBranches` declares `const _teamAtCall` inside the `try` and
//      reads it from the `catch`, so every failed branch load throws
//      ReferenceError instead of taking the intended best-effort empty-list
//      path.
//
// A text scan reports on a spelling, never a behaviour — the brace-matching
// audit tool returned a FALSE POSITIVE while missing the instance already
// verified by hand. Static analysis is the correct instrument: it is
// scope-aware, so it catches all three shapes — and everything else in the same
// class — across the whole file at zero runtime cost. Verified against
// synthetic inputs: the #2709 deps-array shape and the try→catch shape are both
// reported, while a later-declared `const` read from inside a nested closure is
// reported too (see the KNOWN FALSE POSITIVES note below).
//
// ⛔ EXACTLY TWO RULES. Do NOT broaden this config: the wider lint surface is
// the separate #3216 / #3219 job, and a mass-red here would block the beta
// merge queue. No stylistic rules belong in this file.
//
// KNOWN FALSE POSITIVES — `no-use-before-define` is DEMOTED TO `warn` for this
// reason, and the reason is recorded here, not left implicit (a demoted rule
// with no note is indistinguishable from laziness and gets "fixed" back by the
// next reader). See the rule site below for the full note: 37 findings, 37
// false positives, and the one eager shape the rule still holds.
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'

export default [
  {
    // The app sources (including the .test.js suite that sits beside them).
    files: ['src/**/*.{js,jsx}'],
    // Registered, NOT enabled: main.jsx carries 17 pre-existing
    // `react-hooks/exhaustive-deps` disable directives (14 `disable-next-line`,
    // 3 `disable-line`), and ESLint reports a directive naming an unknown rule
    // as an error ("Definition for rule ... was not found") — 17 of them on
    // every run. Registering the plugin lets those directives resolve as
    // written. None of its rules are enabled here — enabling hooks linting is
    // part of #3216 / #3219.
    plugins: { 'react-hooks': reactHooks },
    linterOptions: {
      // ... and because that rule stays off, its 17 directives suppress nothing,
      // so ESLint would flag every one of them as unused on every run.
      reportUnusedDisableDirectives: 'off',
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      // JSX stays as written — Vite/esbuild does the transform, not ESLint.
      parserOptions: { ecmaFeatures: { jsx: true } },
      // Browser globals only; anything else undefined is a real finding.
      // Without this the 232 legitimate uses of document/window/setTimeout/...
      // drown the single real finding.
      globals: { ...globals.browser },
    },
    rules: {
      // ⚠️ DEMOTED TO `warn` — RECORDED REASON (do not flip back to `error`
      // without re-measuring): on this tree the rule reports 37 findings and
      // ALL 37 are false positives. The rule is purely textual/scope-based, so
      // it cannot tell an EAGER reference (a deps array, a render-body
      // expression) from a LAZY one (inside a nested closure that runs after
      // the declaration is initialised). Every one of the 37 is the lazy kind.
      //
      // WHAT THE RULE STILL HOLDS (this is why it is not deleted): the #2709
      // shape — an effect deps array, or any render-body expression, that
      // references a `const` declared later in the same component. A deps array
      // is evaluated eagerly during render, so that reference is a real TDZ
      // crash (it white-screened the dashboard for every signed-in user); it is
      // the one thing this rule catches that a closure body does not. So the
      // rule stays armed as a `warn` tripwire for exactly that shape across the
      // whole file.
      //
      // WHERE REAL COVERAGE LIVES (the warn is NOT the gate): the #2709 class is
      // gated today by the executed tripwire `src/tdzDepsTripwire.test.js`, which
      // fails the suite, not merely prints a warning.
      //
      // `variables: true` is the whole point: a `const`/`let` referenced before
      // its declaration within the SAME block scope is reported (the #2709
      // deps-array shape). The try→catch boundary is NOT one of those cases —
      // the catch clause is a separate scope, so the #3687 shape is
      // `no-undef`'s, below, not this rule's (verified: this rule is silent on
      // the exact #3687 shape).
      // `functions: false` keeps hoisted function declarations legal, so this
      // stays a bug detector rather than a hoisting style rule.
      //
      // REORDERING THE 37 IS DELIBERATELY NOT A WORK ITEM. No issue is filed
      // for it and none should be created; an honest consequence of never doing
      // it is nothing — they are false positives at runtime. Satisfying the rule
      // would mean moving ~15 hook and derived-const declarations across a
      // 9k-line component (three of them cannot move without dragging a whole
      // declaration chain), which is itself a white-screen risk. REVISIT when
      // the rule can actually GATE CI — i.e. once agent-infra#1128 (the reusable
      // node-ci lint job ignores `working-directory` and self-skips for nested
      // packages) is fixed so this config is enforced and a warn/error split is
      // visible — or when this component is decomposed into modules small enough
      // that the reorder is mechanical.
      'no-use-before-define': ['warn', { variables: true, functions: false }],
      // Typos, missing imports, and identifiers that leaked out of scope
      // (the #3687 try→catch shape lands here). Stays at `error`: unlike the
      // rule above, its findings are real, and it is the rule that caught
      // #3687 — a dead tool would be exactly the failure this config exists to
      // remove.
      'no-undef': 'error',
    },
  },
]
