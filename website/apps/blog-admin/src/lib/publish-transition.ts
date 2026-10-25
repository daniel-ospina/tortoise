/**
 * publish-transition.ts — "does THIS write make the article's URL live?"
 *
 * ONE RULE, ONE HOME (#9680). The agent API already asks exactly this question
 * before it notifies IndexNow (`website/functions/blog/api/posts/[[path]].ts`,
 * `becameLive`), and its own comment states the rule this module restates:
 *
 *   "Everything else submits nothing: a draft write, an unpublish, **an edit
 *    that leaves a live row live**."
 *
 * The console writes status DIRECTLY to Supabase via supabase-js, so it never
 * passes through that predicate and has to apply it itself. Its editor save
 * fired on `mode === 'publish'` alone, and for an already-published post that
 * mode is the "Save & Republish" button — i.e. precisely an EDIT THAT LEAVES
 * THE ROW PUBLISHED, which the issue names twice and forbids:
 *
 *   • item 3: "Never submit on: … an edit that leaves status published …"
 *   • indicator (c): "an edit to an already-published post … submit nothing"
 *
 * ⚠️ THE 24H WINDOW DOES NOT MAKE THE OLD FORM SAFE, and that is the trap this
 * module closes. `_shared/indexnow.ts`'s window bounds REPEATS; it does not
 * classify transitions. An edit saved 25 hours after the publish passes the
 * window and submits a URL whose content did not change — invisible to any test
 * that asserts "exactly one submission" against a fresh row, and wrong in
 * production. The guard that decides *whether* must sit in front of the guard
 * that decides *how often*.
 *
 * "LIVE" IS TWO COLUMNS, mirroring the renderer rather than the row's status
 * alone: `_lib.ts` serves a post only when `status = 'published'` AND
 * `hold_for_review = false`, so a held row's URL answers 404 — submitting it
 * would hand six search engines a dead URL. Hence two arms, exactly the two the
 * agent API has:
 *   1. a `status` transition to published; and
 *   2. a hold CLEAR on a row that was already published — status unchanged, URL
 *      goes from 404 to 200 (#9830).
 *
 * NULL counts as HELD (`!== false`), matching `api/notify-publish.ts`, which
 * refuses anything that is not `=== false`. Reading NULL as clear would call a
 * row live that every public surface filters out.
 *
 * ⛔ ZERO IMPORTS, ON PURPOSE. This repo gates the console from Python, which
 * executes this file under `node --experimental-strip-types`
 * (`tests/test_blog_publish_notify_admin.py`) — the console has no gated TS
 * suite (`ci.yml`: "blog-admin has no gated suite"). A bare `import … from
 * '@/lib/…'` is unresolvable in that harness, which would push the rule back to
 * being pinned by a regex over source text instead of by running it.
 */

/** The two columns that decide whether an article's canonical URL is served. */
export interface VisibilityState {
  status: string;
  /** `null` counts as HELD — see the module header. */
  hold_for_review: boolean | null;
}

/**
 * Does the write `before → after` make the article's canonical URL live?
 *
 * PURE, so the rule is testable without a runtime and has exactly one home.
 * Called only for writes whose resulting status is `published`; a draft write
 * (including an unpublish) returns false through the first arm regardless.
 */
export function becomesLive(before: VisibilityState, after: VisibilityState): boolean {
  // Not published after the write: a draft, an unpublish, an archive. Nothing
  // to notify — and note an unpublish has no retraction to send either.
  if (after.status !== 'published') return false;
  // The publish transition itself: the row was not published before this write.
  if (before.status !== 'published') return true;
  // Already published and STILL published: the only remaining way this write
  // makes the URL live is clearing a hold that was hiding it.
  return before.hold_for_review !== false && after.hold_for_review === false;
}
