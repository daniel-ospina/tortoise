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
// KNOWN FALSE POSITIVES (do not "fix" by reordering code): `no-use-before-define`
// is purely textual/scope-based — it cannot tell an eager reference (a deps
// array, a render-body expression) from a lazy one (inside a nested closure that
// runs after the declaration is initialised). On the current tree it reports 37
// hits, ALL of the lazy kind. They are quantified in #3694 rather than
// mass-edited, because reordering hook declarations in a 9k-line component is
// itself a white-screen risk.
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
      // `variables: true` is the whole point: a `const`/`let` referenced before
      // its declaration (same scope, including try→catch) is an error.
      // `functions: false` keeps hoisted function declarations legal, so this
      // stays a bug detector rather than a hoisting style rule.
      'no-use-before-define': ['error', { variables: true, functions: false }],
      // Typos, missing imports, and identifiers that leaked out of scope
      // (the #3687 try→catch shape lands here).
      'no-undef': 'error',
    },
  },
]
