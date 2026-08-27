/**
 * save.ts — the PostEditor save mutation as a testable unit.
 *
 * The mutation reads ALL save inputs through getState() AT EXECUTION TIME,
 * never from closure values captured when the mutation object was created.
 * PostEditor wires getState to refs (formRef / editorRef) so a save invoked
 * from any render's mutation options always uses the current editor body and
 * form state — this is the stale-closure guard (code-review P2, PR #1818).
 */

import { buildSaveRecord, createPost, updatePost, type SaveFormFields } from '@/lib/blog-api';

export type SaveMode = 'draft' | 'publish';

export interface SaveContext {
  form: SaveFormFields;
  /** The editor body markdown at the moment the save RUNS (not when queued). */
  editorBody: string;
  userId: string;
  isNew: boolean;
  id?: string;
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
      return created.id;
    }
    if (!ctx.id) throw new Error('Missing post id');
    await updatePost(ctx.id, record);
    return ctx.id;
  };
}
