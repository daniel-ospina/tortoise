/**
 * ImageNode — custom TipTap image extension with hover-to-delete overlay.
 * PORTED AS-IS from ElDato (issue #1798): extends the default Image
 * extension, adding a ReactNodeViewRenderer that shows an X button on hover
 * for easy deletion.
 *
 * Attribution: ported from
 * eldato/src/components/guides/ImageNode.tsx (private repo, 2026-08).
 */

import Image from '@tiptap/extension-image';
import { NodeViewWrapper, ReactNodeViewRenderer, type NodeViewProps } from '@tiptap/react';
import { X } from 'lucide-react';

function ImagePreview({ node, deleteNode, selected }: NodeViewProps) {
  const { src, alt, title } = node.attrs;

  return (
    <NodeViewWrapper className="image-node-wrapper my-4">
      <div className={`relative group inline-block max-w-full rounded-lg ${selected ? 'ring-2 ring-primary' : ''}`}>
        <img
          src={src}
          alt={alt || ''}
          title={title || undefined}
          className="max-w-full h-auto rounded-lg"
          draggable={false}
        />
        <button
          onClick={deleteNode}
          className="absolute top-2 right-2 bg-destructive text-white rounded-full p-1 opacity-0 group-hover:opacity-100 transition-opacity shadow-md"
          title="Remove image"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </NodeViewWrapper>
  );
}

export const ImageWithDelete = Image.extend({
  addNodeView() {
    return ReactNodeViewRenderer(ImagePreview);
  },
});
