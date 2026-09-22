/**
 * Markdown roundtrip invariant — import(export(md)) ≡ md for the supported
 * subset (plan §6: "roundtrip integrity is a unit-tested invariant").
 *
 * Runs in jsdom (TipTap needs a DOM). Builds the SAME extension set the
 * editor uses (buildEditorExtensions) so the invariant is tested against
 * exactly what ships. E2E-5 asserts the DB body stays clean markdown after
 * an edit cycle; this test pins the underlying serializer behavior.
 */

import { describe, it, expect } from 'vitest';
import { Editor } from '@tiptap/react';
import { buildEditorExtensions, normalizeMarkdown, getMarkdownStorage } from './markdown';

function roundtrip(md: string): string {
  const editor = new Editor({
    extensions: buildEditorExtensions(),
    content: md,
  });
  try {
    return normalizeMarkdown(getMarkdownStorage(editor).getMarkdown());
  } finally {
    editor.destroy();
  }
}

const CORPUS: Array<[string, string]> = [
  ['paragraphs', 'A simple paragraph.\n\nSecond paragraph with **bold** and *italic*.'],
  ['headings', '# H1 title\n\n## H2 section\n\n### H3 subsection\n\n#### H4 detail'],
  ['lists', '- one\n- two\n- three\n\n1. first\n2. second'],
  ['nested lists', '- parent one\n  - child one\n  - child two\n- parent two'],
  ['blockquote', '> Quoted wisdom\n>\n> across two lines'],
  ['fenced code', '```ts\nconst x: number = 1;\nconsole.log(x);\n```'],
  ['link', 'Read the [docs](https://tortoise.premiselabs.co/docs).'],
  ['image', '![Tortoise](https://tortoise.premiselabs.co/blog/og-image.png)'],
  ['table', '| Name | Role |\n| --- | --- |\n| Tortoise | memory |\n| El Dato | scanner |'],
  ['mixed', '## Intro\n\nSome **bold** text with a [link](https://example.com).\n\n- item\n\n> a quote\n\n```sh\necho hi\n```'],
];

describe('markdown roundtrip invariant', () => {
  for (const [name, md] of CORPUS) {
    it(`preserves ${name}`, () => {
      expect(roundtrip(md)).toBe(normalizeMarkdown(md));
    });
  }

  it('is idempotent across repeated export', () => {
    const once = roundtrip('# T\n\ntext **bold**\n\n- a\n- b');
    const twice = roundtrip(once);
    expect(twice).toBe(once);
  });

  it('escapes raw HTML (body stays clean markdown, no passthrough)', () => {
    const out = roundtrip('hello <script>alert(1)</script> world');
    expect(out).not.toContain('<script>');
    expect(out).toContain('hello');
    expect(out).toContain('world');
  });
});
