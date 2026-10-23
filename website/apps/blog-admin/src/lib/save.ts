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
    // #9803: publish — including "publish again" on an already-live post. The
    // endpoint re-checks that the row is published and owns the 24h window, so
    // this is safe to fire on every publish save.
    if (mode === 'publish' && ctx.form.slug) {
      void notifyPublished(ctx.form.slug);
    }
    return ctx.id;
  };
}
