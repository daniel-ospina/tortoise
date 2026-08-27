/**
 * Markdown contract helpers (plan §6) — shared by PostEditor and the
 * roundtrip unit test.
 *
 * Canonical storage = markdown. The TipTap editor imports markdown on load
 * and exports markdown on save via the tiptap-markdown extension
 * (PINNED: "tiptap-markdown": "0.9.0" — peer @tiptap/core ^3.0.1).
 *
 * Roundtrip invariant: import(export(md)) ≡ md for the supported subset
 * (headings, bold/italic, lists, blockquote, fenced code, links, images,
 * tables). Enforced by src/lib/markdown.test.ts (vitest, jsdom env) and by
 * E2E-5 (DB body stays clean markdown after an edit cycle).
 */

import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import { Table } from '@tiptap/extension-table';
import { TableRow } from '@tiptap/extension-table-row';
import { TableCell } from '@tiptap/extension-table-cell';
import { TableHeader } from '@tiptap/extension-table-header';
import Placeholder from '@tiptap/extension-placeholder';
import { Markdown } from 'tiptap-markdown';
import type { Editor, Extensions } from '@tiptap/core';
import { ImageWithDelete } from '@/components/guides/ImageNode';
import { BlockDragHandle } from '@/components/guides/BlockDragHandle';

/**
 * The canonical editor extension set — used by PostEditor's useEditor AND by
 * the roundtrip test, so the invariant is tested against exactly what ships.
 */
export function buildEditorExtensions(): Extensions {
  return [
    StarterKit.configure({
      heading: { levels: [1, 2, 3, 4] },
      // codeBlock stays in StarterKit (toolbar "code block" button)
    }),
    Link.configure({ openOnClick: false }),
    ImageWithDelete,
    Table.configure({ resizable: false }),
    TableRow,
    TableCell,
    TableHeader,
    Placeholder.configure({
      placeholder: 'Start writing… paste from Google Docs or type markdown directly.',
    }),
    BlockDragHandle,
    // Markdown import/export — html:false keeps the exported body clean
    // markdown (raw HTML in the doc would be escaped, never passed through).
    Markdown.configure({
      html: false,
      tightLists: true,
      tightListClass: 'tight',
      bulletListMarker: '-',
      linkify: false,
      breaks: false,
    }),
  ];
}

/** Typed accessor for the tiptap-markdown storage (package types expose
 * MarkdownStorage but the editor Storage map is untyped). */
export function getMarkdownStorage(editor: Editor): { getMarkdown(): string } {
  return (editor.storage as unknown as { markdown: { getMarkdown(): string } }).markdown;
}

/** Export the editor doc as clean markdown (canonical body format). */
export function editorToMarkdown(editor: Editor | null): string {
  if (!editor) return '';
  const md = getMarkdownStorage(editor).getMarkdown();
  return md.replace(/\n+$/, ''); // drop trailing newline(s) — canonical body
}

/** Normalize markdown for equality comparisons (trailing newlines only). */
export function normalizeMarkdown(md: string): string {
  return md.replace(/\n+$/, '');
}
