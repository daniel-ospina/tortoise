/**
 * save.ts — the PostEditor save mutation as a testable unit.
 *
 * The mutation reads ALL save inputs through getState() AT EXECUTION TIME,
 * never from closure values captured when the mutation object was created.
 * PostEditor wires getState to refs (formRef / editorRef) so a save invoked
 * from any render's mutation options always uses the current editor body and
 * form state — this is the stale-closure guard (code-review P2, PR #1818).
 */

import { buildSaveRecord, createPost, updatePost, purgePostCache, notifyPublished, type SaveFormFields } from '@/lib/blog-api';
import { becomesLive } from '@/lib/publish-transition';

export type SaveMode = 'draft' | 'publish';

export interface SaveContext {
  form: SaveFormFields;
  /** The editor body markdown at the moment the save RUNS (not when queued). */
  editorBody: string;
  userId: string;
  isNew: boolean;
  id?: string;
  /** True when the post is currently published (draft-save = unpublish → purge). */
  wasPublished?: boolean;
  /**
   * True when the STORED row's `hold_for_review` is anything but `false` — so a
   * NULL counts as held, exactly as `api/notify-publish.ts` reads it. Only the
   * publish path consults it: clearing that hold is the one way a save on an
   * already-published post still makes its URL live (#9830), and treating it as
   * "not held" would silently drop that notification.
   *
   * Defaults to `false`, i.e. "not held", which makes a save that forgot to
   * pass it skip the notification rather than submit on an ordinary edit — the
   * safe direction: a skipped notification is recoverable, a flood is not.
   */
  wasHeld?: boolean;
}

export type GetSaveContext = () => SaveContext;

/** Create the mutation body for a save. Reads state via getState() when invoked. */
export function createSaveHandler(getState: GetSaveContext) {
  return async (mode: SaveMode): Promise<string> => {
    const ctx = getState();
    const record = buildSaveRecord(ctx.form, ctx.editorBody, mode, ctx.userId);
    if (ctx.isNew) {
      record.created_by = ctx.userId;
      const created = await createPost(record);
      // #9803: the editor writes status directly to Supabase, so the agent
      // API's publish transition never sees this — the editor tells the
      // side-effect endpoint instead. Publish only; a draft create submits
      // nothing.
      if (mode === 'publish' && created.slug) void notifyPublished(created.slug);
      return created.id;
    }
    if (!ctx.id) throw new Error('Missing post id');
    await updatePost(ctx.id, record);
    // #1865: any save that moves a published post to a NON-public visibility
    // must purge the edge cache (best-effort, fail-open) or a stale 200
    // survives the TTL. Covers: draft-save (= unpublish) AND a published post
    // saved with hold_for_review=true (the public fetch filters
    // hold_for_review=eq.false — a third visibility axis that bypasses the
    // status-based purge).
    const holdHidesPublished = ctx.form.hold_for_review && ctx.wasPublished;
    if ((mode === 'draft' && ctx.wasPublished || holdHidesPublished) && ctx.form.slug) {
      void purgePostCache(ctx.form.slug);
    }
    // #9680 item 3 / indicator (c): notify only when THIS save makes the URL
    // live — the same predicate the agent API applies in its PATCH handler
    // (`becameLive`, `functions/blog/api/posts/[[path]].ts`). One rule, one home
    // (`lib/publish-transition.ts`).
    //
    // The form this replaces fired on `mode === 'publish'` alone, and on an
    // already-published post that mode is the "Save & Republish" button: an
    // EDIT THAT LEAVES THE ROW PUBLISHED, which the issue forbids. The 24h
    // window could not cover for it — the window bounds repeats, so an edit
    // saved a day after the publish passed it and submitted a URL that had not
    // changed.
    //
    // The create path above needs none of this: a row that did not exist before
    // cannot have been live, so a create always transitions. Do not "unify" the
    // two into an unconditional call for symmetry.
    const publishedAfter = record.status ?? 'draft';
    const makesUrlLive = becomesLive(
      { status: ctx.wasPublished ? 'published' : 'draft', hold_for_review: ctx.wasHeld === true },
      { status: publishedAfter, hold_for_review: ctx.form.hold_for_review === true },
    );
    if (makesUrlLive && ctx.form.slug) {
      void notifyPublished(ctx.form.slug);
    }
    return ctx.id;
  };
}
