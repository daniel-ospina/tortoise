import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Tortoise blog admin SPA.
//
// base '/admin/' — ABSOLUTE asset paths, because the public base path IS known:
// the deploy-dashboard job stages the built app into
// website/apps/dashboard/dist/admin/ (deploy-pages.yml), so the bundle always
// lives at /admin/assets/… and is served on the app origin by the admin gate
// Function (website/apps/dashboard/functions/admin/[[path]].ts) — same-origin
// with the `__Host-session` cookie (#4171).
//
// #3952: this was `'./'`. A relative base resolves against the DOCUMENT URL
// (RFC 3986 §5.2.3 "Merge Paths"), so whether it worked depended on the
// document's segment depth: at `/admin/` the base prefix is `/admin/` and
// `./assets/…` resolved to the real bundle, but at the extensionless `/admin`
// — the canonical console URL, and the exact form the gate's own `next=` emits
// — the prefix is `/`, so the shell requested `/assets/index-…js`, which is
// never deployed → 404 → blank page. Odd-depth shell routes failed the same way
// (`/admin/blog/edit` resolved to `/admin/blog/assets/…`). An absolute base makes
// the bundle resolve from EVERY entry path the gate serves the shell for.
//
// Vite documents the relative form (`'./'` or `''`) as the fallback "if you don't
// know the base path in advance" (vite.dev/guide/build.html → "Relative base");
// here it is fixed and known, so the absolute form is the documented treatment.
export default defineConfig({
  plugins: [react()],
  base: '/admin/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
});
