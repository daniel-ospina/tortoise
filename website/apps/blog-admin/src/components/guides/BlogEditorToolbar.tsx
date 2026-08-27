/**
 * BlogEditorToolbar — ported from ElDato's GuideEditorToolbar (issue #1798).
 *
 * Google-Docs-style formatting toolbar for the TipTap editor. Kept from the
 * original: ToolbarButton w/ tooltips, headings, bold/italic, lists, quote,
 * link, image insert, table grid picker, inline table-editing bar, undo/redo.
 * STRIPPED: YouTube, deal embed, deals carousel, columns (owner-approved port
 * list — plan §3). ADDED: code block button (StarterKit ships codeBlock;
 * the plan's markdown contract includes fenced code).
 *
 * Attribution: ported from
 * eldato/src/components/guides/GuideEditorToolbar.tsx (private repo, 2026-08).
 */

import { useState, useCallback } from 'react';
import { type Editor } from '@tiptap/react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import {
  Bold, Italic, Heading1, Heading2, Heading3, Pilcrow, List, ListOrdered,
  Quote, Code2, Undo2, Redo2, Link2, Image, Table, Trash2, Plus, Minus,
} from 'lucide-react';

interface ToolbarProps {
  editor: Editor | null;
  onInsertImage: () => void;
  inTable?: boolean;
}

function ToolbarButton({
  onClick,
  active,
  disabled,
  children,
  title,
}: {
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
  title: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant={active ? 'secondary' : 'ghost'}
          size="icon"
          className="h-8 w-8"
          onClick={onClick}
          disabled={disabled}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="text-xs">
        {title}
      </TooltipContent>
    </Tooltip>
  );
}

const MAX_ROWS = 8;
const MAX_COLS = 8;

function TableGridPicker({ onSelect }: { onSelect: (rows: number, cols: number) => void }) {
  const [hoverRow, setHoverRow] = useState(0);
  const [hoverCol, setHoverCol] = useState(0);

  const handleMouseEnter = useCallback((r: number, c: number) => {
    setHoverRow(r);
    setHoverCol(c);
  }, []);

  return (
    <div className="p-2">
      <div className="grid gap-0.5" style={{ gridTemplateColumns: `repeat(${MAX_COLS}, 1fr)` }}>
        {Array.from({ length: MAX_ROWS * MAX_COLS }, (_, i) => {
          const r = Math.floor(i / MAX_COLS) + 1;
          const c = (i % MAX_COLS) + 1;
          const highlighted = r <= hoverRow && c <= hoverCol;
          return (
            <button
              key={i}
              type="button"
              className={`w-5 h-5 border rounded-sm transition-colors ${
                highlighted
                  ? 'bg-primary/20 border-primary'
                  : 'bg-background border-border hover:border-muted-foreground/50'
              }`}
              onMouseEnter={() => handleMouseEnter(r, c)}
              onClick={() => onSelect(r, c)}
            />
          );
        })}
      </div>
      <p className="text-xs text-center text-muted-foreground mt-1.5">
        {hoverRow > 0 && hoverCol > 0 ? `${hoverCol} × ${hoverRow}` : 'Select size'}
      </p>
    </div>
  );
}

/** Inline table-editing bar — shown below the main toolbar when cursor is in a table */
function TableEditBar({ editor }: { editor: Editor }) {
  return (
    <div className="flex flex-wrap items-center gap-0.5 px-1.5 py-1 border-b bg-secondary/60 text-xs">
      <span className="text-muted-foreground text-xs mr-1 font-medium">Table:</span>
      <ToolbarButton onClick={() => editor.chain().focus().addRowAfter().run()} title="Add row after">
        <Plus className="w-3.5 h-3.5" />
      </ToolbarButton>
      <ToolbarButton onClick={() => editor.chain().focus().deleteRow().run()} title="Delete row">
        <Minus className="w-3.5 h-3.5" />
      </ToolbarButton>

      <div className="w-px h-5 bg-border mx-1" />

      <ToolbarButton onClick={() => editor.chain().focus().addColumnAfter().run()} title="Add column after">
        <Plus className="w-3.5 h-3.5 rotate-90" />
      </ToolbarButton>
      <ToolbarButton onClick={() => editor.chain().focus().deleteColumn().run()} title="Delete column">
        <Minus className="w-3.5 h-3.5 rotate-90" />
      </ToolbarButton>

      <div className="w-px h-5 bg-border mx-1" />

      <ToolbarButton onClick={() => editor.chain().focus().deleteTable().run()} title="Delete table">
        <Trash2 className="w-3.5 h-3.5 text-destructive" />
      </ToolbarButton>
    </div>
  );
}

export function BlogEditorToolbar({ editor, onInsertImage, inTable = false }: ToolbarProps) {
  const [tablePickerOpen, setTablePickerOpen] = useState(false);

  if (!editor) return null;

  function insertLink() {
    const url = window.prompt('URL:');
    if (url) {
      editor!.chain().focus().setLink({ href: url }).run();
    }
  }

  function handleTableSelect(rows: number, cols: number) {
    editor!.chain().focus().insertTable({ rows, cols, withHeaderRow: true }).run();
    setTablePickerOpen(false);
  }

  return (
    <div className="sticky top-0 z-10 bg-card rounded-t-xl">
      <div className="flex flex-wrap gap-0.5 p-1.5 border-b">
        {/* Text style */}
        <ToolbarButton onClick={() => editor.chain().focus().setParagraph().run()} active={editor.isActive('paragraph') && !editor.isActive('heading')} title="Normal text">
          <Pilcrow className="w-4 h-4" />
        </ToolbarButton>
        <ToolbarButton onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()} active={editor.isActive('heading', { level: 1 })} title="Heading 1">
          <Heading1 className="w-4 h-4" />
        </ToolbarButton>
        <ToolbarButton onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()} active={editor.isActive('heading', { level: 2 })} title="Heading 2">
          <Heading2 className="w-4 h-4" />
        </ToolbarButton>
        <ToolbarButton onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()} active={editor.isActive('heading', { level: 3 })} title="Heading 3">
          <Heading3 className="w-4 h-4" />
        </ToolbarButton>

        <div className="w-px bg-border mx-1" />

        {/* Formatting */}
        <ToolbarButton onClick={() => editor.chain().focus().toggleBold().run()} active={editor.isActive('bold')} title="Bold">
          <Bold className="w-4 h-4" />
        </ToolbarButton>
        <ToolbarButton onClick={() => editor.chain().focus().toggleItalic().run()} active={editor.isActive('italic')} title="Italic">
          <Italic className="w-4 h-4" />
        </ToolbarButton>

        <div className="w-px bg-border mx-1" />

        {/* Lists */}
        <ToolbarButton onClick={() => editor.chain().focus().toggleBulletList().run()} active={editor.isActive('bulletList')} title="Bullet list">
          <List className="w-4 h-4" />
        </ToolbarButton>
        <ToolbarButton onClick={() => editor.chain().focus().toggleOrderedList().run()} active={editor.isActive('orderedList')} title="Numbered list">
          <ListOrdered className="w-4 h-4" />
        </ToolbarButton>
        <ToolbarButton onClick={() => editor.chain().focus().toggleBlockquote().run()} active={editor.isActive('blockquote')} title="Quote">
          <Quote className="w-4 h-4" />
        </ToolbarButton>
        <ToolbarButton onClick={() => editor.chain().focus().toggleCodeBlock().run()} active={editor.isActive('codeBlock')} title="Code block">
          <Code2 className="w-4 h-4" />
        </ToolbarButton>

        <div className="w-px bg-border mx-1" />

        {/* Insert */}
        <ToolbarButton onClick={insertLink} title="Insert link">
          <Link2 className="w-4 h-4" />
        </ToolbarButton>
        <ToolbarButton onClick={onInsertImage} title="Insert image">
          <Image className="w-4 h-4" />
        </ToolbarButton>

        {/* Table grid picker */}
        <Popover open={tablePickerOpen} onOpenChange={setTablePickerOpen}>
          <Tooltip>
            <TooltipTrigger asChild>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant={inTable ? 'secondary' : 'ghost'}
                  size="icon"
                  className="h-8 w-8"
                >
                  <Table className="w-4 h-4" />
                </Button>
              </PopoverTrigger>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="text-xs">Insert table</TooltipContent>
          </Tooltip>
          <PopoverContent className="w-auto p-0" align="start">
            <TableGridPicker onSelect={handleTableSelect} />
          </PopoverContent>
        </Popover>

        <div className="w-px bg-border mx-1" />

        {/* Undo/Redo */}
        <ToolbarButton onClick={() => editor.chain().focus().undo().run()} disabled={!editor.can().undo()} title="Undo">
          <Undo2 className="w-4 h-4" />
        </ToolbarButton>
        <ToolbarButton onClick={() => editor.chain().focus().redo().run()} disabled={!editor.can().redo()} title="Redo">
          <Redo2 className="w-4 h-4" />
        </ToolbarButton>
      </div>

      {/* Inline table edit bar — visible when cursor is in a table */}
      {inTable && <TableEditBar editor={editor} />}
    </div>
  );
}
