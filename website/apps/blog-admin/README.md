# Tortoise Blog Admin (`website/apps/blog-admin`)

React + Vite + TypeScript + Tailwind + shadcn-style UI + TipTap admin SPA for the
Tortoise blog CMS (issue #1798, epic `docs/epics/2026-08-27-tortoise-blog-cms/03-plan.md`).
Served by the admin gate Function (`website/functions/admin/[[path]].ts`) at `/admin/*`.

## Dev

```bash
cp .env.example .env   # VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY
npm install
npm run dev            # vite dev server (localhost:5173)
npm test               # vitest — markdown roundtrip invariant
npm run typecheck      # tsc --noEmit
npm run build          # tsc --noEmit && vite build → dist/ (base './', relative assets)
```

## Auth model

- The SPA reads the PKCE session with the **user's own token** via a storage
  adapter keyed on `sb-tortoise-auth-token` (localStorage + the site's
  parent-domain cookie — `src/lib/supabase.ts`). No service-role keys client-side.
- Data authorization is Supabase RLS: `blog_posts` admin_all policy gates on
  `is_admin()` membership (migration `20260827000001`, issue #1793).
- `useAuth` additionally calls the `is_admin()` RPC; missing session / non-admin
  → redirect to `https://tortoise.premiselabs.co/auth`.

## Views (hash routes)

| Route | View |
|---|---|
| `#/` | Posts list — tabs: Review Queue / All / Draft / Published / Archived |
| `#/new` | New post editor |
| `#/edit/:id` | Post editor (TipTap) |
| `#/audit` | Audit table (published_by/reviewed_by + timestamps) |

Review queue (plan W2) surfaces three kinds: **agent drafts** (`status=draft`),
**unreviewed direct-published** (`published` AND `reviewed_at IS NULL`), and
**held** (`hold_for_review=true`). Actions per state: publish, mark reviewed,
edit, request changes (with note), unpublish, archive, clear hold. Unpublish /
archive / request-changes are human-gated (confirm dialogs).

## Markdown contract

Canonical `blog_posts.body` = markdown. The editor imports markdown on load and
exports markdown on save via **`tiptap-markdown` pinned to `0.9.0`** (exact —
no caret; peer `@tiptap/core ^3.0.1`, installed with TipTap 3.19/3.20).

Roundtrip invariant `import(export(md)) ≡ md` for the supported subset
(headings, bold/italic, lists, blockquote, fenced code, links, images, tables)
is enforced by `src/lib/markdown.test.ts` (vitest + jsdom), which builds the
SAME extension set as the editor (`buildEditorExtensions`). `html: false` keeps
raw HTML out of the body (E2E-5 asserts DB body stays clean markdown).

## ElDato port

Editor UX ported from the ElDato repo (private checkout), attribution comments
in each file: `PostEditor` ← `src/pages/admin/GuideEditor.tsx`,
`PostsList` ← `GuidesList.tsx`, `BlogEditorToolbar` ← `GuideEditorToolbar.tsx`,
`ImageNode` ← `ImageNode.tsx`, `BlockDragHandle` ← `BlockDragHandle.ts`.
Stripped: deal embeds/carousels/columns/FAQ/related-deal filters. Theme is the
tortoise slate/cyan dark theme (NOT ElDato purple): bg `#060b14`, text `#cbd5e1`,
accent `#06b6d4`, border `#1e293b`, Georgia serif headings.

## Notes / deviations

- `sanitizeObjectKey()` is implemented locally in `src/lib/blog-api.ts`
  (basename-only, strip `..` `/` backslash + control chars) — the #1793 helper
  it was supposed to consume is not in this worktree branch yet; the local
  implementation mirrors the plan §4 contract so it can be swapped later.
- Bundle is ~1.3 MB minified (TipTap + React + supabase-js) — fine for an
  auth-gated admin surface; chunk-splitting can be added if it ever matters.
