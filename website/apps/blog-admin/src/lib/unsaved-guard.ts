/**
 * unsaved-guard — module-level "editor is dirty" flag shared by the SPA shell
 * and the editor page.
 *
 * Single-editor-at-a-time SPA: PostEditor flips the flag on user edits and
 * clears it on successful save / unmount. App.tsx uses it to (a) warn via
 * beforeunload and (b) intercept hash-route navigations with a confirm dialog
 * (the route is reverted until the user confirms the discard).
 */

let dirty = false;

export function setDirty(value: boolean): void {
  dirty = value;
}

export function isDirty(): boolean {
  return dirty;
}

// Save-in-flight navigation guard: set when the user discards edits during a
// save — the editor's onSuccess must not navigate back into the editor.
let abandoned = false;
export function markAbandoned(): void { abandoned = true; }
export function wasAbandoned(): boolean { return abandoned; }
