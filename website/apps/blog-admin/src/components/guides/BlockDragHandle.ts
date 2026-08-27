/**
 * BlockDragHandle — TipTap extension for Notion-style block drag-and-drop.
 * PORTED from ElDato (issue #1798) with column-creation logic STRIPPED
 * (the Columns extension is not part of the blog port list).
 *
 * Kept: always-visible drag-handle grip on every top-level block, native
 * ProseMirror reorder (drag above/below), click-handle context menu with
 * Delete.
 * Stripped: left/right drop zones → column creation, dealCarousel guard,
 * handleColumnDrop / handleAddToExistingColumns.
 *
 * Attribution: ported from
 * eldato/src/components/guides/BlockDragHandle.ts (private repo, 2026-08).
 */

import { Extension } from '@tiptap/core';
import { NodeSelection, Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet, EditorView } from '@tiptap/pm/view';
import { Node as PMNode } from '@tiptap/pm/model';

const DRAG_HANDLE_KEY = new PluginKey('blockDragHandle');

/** Walk up from event target to find the drag handle element. */
function findHandle(event: Event): Element | null {
  return (event.target as Element).closest?.('[data-drag-handle]') ?? null;
}

/** Create a zero-height wrapper containing the draggable grip icon. */
function createHandleWidget(): HTMLElement {
  const wrapper = document.createElement('div');
  wrapper.className = 'block-drag-handle-wrapper';

  const handle = document.createElement('span');
  handle.className = 'block-drag-handle';
  handle.contentEditable = 'false';
  handle.draggable = true;
  handle.setAttribute('data-drag-handle', '');
  handle.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
    <circle cx="5" cy="3" r="1.5"/><circle cx="11" cy="3" r="1.5"/>
    <circle cx="5" cy="8" r="1.5"/><circle cx="11" cy="8" r="1.5"/>
    <circle cx="5" cy="13" r="1.5"/><circle cx="11" cy="13" r="1.5"/>
  </svg>`;

  wrapper.appendChild(handle);
  return wrapper;
}

/* ── Block context menu (shown on handle click) ── */

let activeMenuCleanup: (() => void) | null = null;

function removeBlockMenu(): void {
  if (activeMenuCleanup) {
    activeMenuCleanup();
    activeMenuCleanup = null;
  }
}

function showBlockMenu(view: EditorView, blockPos: number): void {
  removeBlockMenu();

  const node = view.state.doc.nodeAt(blockPos);
  if (!node) return;

  const blockDOM = view.nodeDOM(blockPos);
  if (!(blockDOM instanceof HTMLElement)) return;

  const wrapper = view.dom.parentElement;
  if (!wrapper) return;

  const menu = document.createElement('div');
  menu.className = 'block-context-menu';

  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'block-context-menu-item block-context-menu-item-danger';
  deleteBtn.innerHTML =
    `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
    `<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>` +
    `<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>` +
    `<span>Delete</span>`;

  deleteBtn.addEventListener('mousedown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const currentNode = view.state.doc.nodeAt(blockPos);
    if (currentNode) {
      view.dispatch(view.state.tr.delete(blockPos, blockPos + currentNode.nodeSize));
    }
    removeBlockMenu();
    view.focus();
  });

  menu.appendChild(deleteBtn);

  // Position near the drag handle (left of block)
  const blockRect = blockDOM.getBoundingClientRect();
  const wrapperRect = wrapper.getBoundingClientRect();

  wrapper.style.position = 'relative';
  menu.style.top = `${blockRect.top - wrapperRect.top}px`;
  menu.style.left = `${Math.max(4, blockRect.left - wrapperRect.left - 28)}px`;

  wrapper.appendChild(menu);

  // Shift right if menu extends off-screen
  requestAnimationFrame(() => {
    const menuRect = menu.getBoundingClientRect();
    if (menuRect.left < 4) {
      menu.style.left = '4px';
    }
  });

  // Close on click outside or Escape
  const onDocMousedown = (e: MouseEvent) => {
    if (!menu.contains(e.target as Node)) removeBlockMenu();
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') { removeBlockMenu(); view.focus(); }
  };

  requestAnimationFrame(() => {
    document.addEventListener('mousedown', onDocMousedown, true);
    document.addEventListener('keydown', onKeyDown, true);
  });

  activeMenuCleanup = () => {
    menu.remove();
    document.removeEventListener('mousedown', onDocMousedown, true);
    document.removeEventListener('keydown', onKeyDown, true);
  };
}

/** Build widget decorations for drag handles on every top-level block. */
function buildHandleDecorations(doc: PMNode): Decoration[] {
  const decorations: Decoration[] = [];
  doc.forEach((_node, offset) => {
    decorations.push(
      Decoration.widget(offset, () => {
        const widget = createHandleWidget();
        // Store the block position directly in the DOM so mousedown can read it
        // without relying on posAtDOM (which is unreliable for widget decorations).
        const handle = widget.querySelector('[data-drag-handle]');
        if (handle) handle.setAttribute('data-block-pos', String(offset));
        return widget;
      }, {
        side: -1,
        key: `drag-handle-${offset}`,
      }),
    );
  });
  return decorations;
}

export const BlockDragHandle = Extension.create({
  name: 'blockDragHandle',

  addProseMirrorPlugins() {
    // Track which block position the drag originated from.
    // Set on mousedown (before dragstart) so the DOM rebuild from setting
    // NodeSelection is complete before the browser starts the drag.
    let dragFromPos: number | null = null;
    // Distinguishes click (show menu) from drag (reorder).
    let didDrag = false;

    return [
      new Plugin({
        key: DRAG_HANDLE_KEY,

        state: {
          init() {
            return null;
          },
          apply(tr) {
            if (tr.docChanged) {
              dragFromPos = null;
            }
            return null;
          },
        },

        props: {
          decorations(state) {
            return DecorationSet.create(state.doc, buildHandleDecorations(state.doc));
          },

          handleDOMEvents: {
            mousedown(view, event) {
              // User may click the SVG/circle inside the handle span,
              // so walk up with .closest() instead of checking event.target directly.
              const handle = findHandle(event);
              if (!handle) return false;

              // Read the block position from the data attribute (set by buildHandleDecorations).
              const blockPos = parseInt(handle.getAttribute('data-block-pos') ?? '', 10);
              if (isNaN(blockPos)) return false;

              const node = view.state.doc.nodeAt(blockPos);
              if (!node) return false;

              // Set NodeSelection NOW, before dragstart fires.
              dragFromPos = blockPos;
              didDrag = false;
              removeBlockMenu();
              view.dispatch(
                view.state.tr.setSelection(NodeSelection.create(view.state.doc, blockPos)),
              );

              // One-time document mouseup listener to detect click vs drag.
              // We can't use handleDOMEvents.mouseup because ProseMirror rebuilds
              // the handle DOM after the NodeSelection dispatch above.
              const onMouseUp = () => {
                document.removeEventListener('mouseup', onMouseUp);
                if (!didDrag && dragFromPos === blockPos) {
                  showBlockMenu(view, blockPos);
                }
                if (!didDrag) dragFromPos = null;
              };
              document.addEventListener('mouseup', onMouseUp);

              // Return true to prevent ProseMirror's mousedown handler from
              // creating a MouseDown instance that would override our selection
              // via delayedSelectionSync during mousemove.
              return true;
            },

            // No custom dragstart/dragover — ProseMirror's native handler sees
            // our NodeSelection and sets up view.dragging, dataTransfer, and
            // handles reorder drops for before/after zones.

            dragend(view) {
              dragFromPos = null;
              (view as unknown as { dragging: unknown }).dragging = null;
              return false;
            },
          },
        },
      }),
    ];
  },
});
