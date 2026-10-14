import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist' },
  // #4915: the DOM test lane. The package's PRIMARY suite stays the zero-dep
  // `node --test src/*.test.js` command; this block adds a SECOND, render-level
  // runner for the deliverables a source pin cannot see — the app a user gets.
  // It reaches CI through that primary command rather than through a
  // `.github/workflows/` edit, which the PR credentials cannot push: the run is
  // executed by `src/domSuite.test.js` (a `node:test` file), which fails if any
  // `src/*.test.jsx` file is not collected here or does not pass. `npm run
  // test:dom` runs the lane directly.
  //
  // `include` is narrowed to `*.test.jsx` on purpose, and it is LOAD-BEARING:
  // vitest's default glob would also match the `node:test` files, which this
  // runner cannot execute (they import `node:test`, and the two runners report
  // differently) — the same narrowing blog-admin's config uses for its own
  // `*.test.ts` set. Widening it to `*.test.js` would also make
  // `domSuite.test.js` re-enter this runner (it spawns vitest) — a fork bomb.
  //
  // jsdom is the document, and index.css is loaded into it (`css: true`) so a
  // computed-style assertion can see a rule that HIDES a rendered fragment —
  // the evasion no source scan can observe.
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.jsx'],
    css: true,
  },
})
