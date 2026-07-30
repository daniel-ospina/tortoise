import { useState, useCallback } from 'react';
import { Tree } from 'react-arborist';
import { C } from '../../constants';

interface TreeNode {
  id: string;
  name: string;
  content: string;
  objectKind: string;
  pointKind: string;
  confidence: number | null;
  lastCalibratedAt: string | null;
  status: string;
  version: number;
  children: TreeNode[];
}

interface Props {
  tree: TreeNode[];
  selectedId: string | null;
  onSelect: (node: TreeNode) => void;
  onViewArguments: (nodeId: string) => void;
  onEdit: (node: TreeNode) => void;
  onDelete: (node: TreeNode) => void;
  onNavigateToNode?: (id: string) => void;
}

const KIND_COLORS: Record<string, string> = {
  customerSegment: '#7aa2f7',
  jobToBeDone: '#9ece6a',
  feature: '#bb9af7',
  userJourney: '#e0af68',
  workflow: '#7dcfff',
  requirement: '#f7768e',
};

const KIND_LABELS: Record<string, string> = {
  customerSegment: 'Segment',
  jobToBeDone: 'JTBD',
  feature: 'Feature',
  userJourney: 'Journey',
  workflow: 'Workflow',
  requirement: 'Req',
};

function confidenceTier(conf: number | null): number {
  if (conf === null || conf === undefined) return 2;
  if (conf < 0.2) return 0;
  if (conf < 0.4) return 1;
  if (conf < 0.6) return 2;
  if (conf < 0.8) return 3;
  return 4;
}

const FONT_SIZES = [10, 11, 12, 13, 14];
const CONFIDENCE_LABELS = ['✦', '✦✦', '✦✦✦', '✦✦✦✦', '✦✦✦✦✦'];

export default function TreeView({
  tree,
  selectedId,
  onSelect,
  onViewArguments,
  onEdit,
  onDelete,
  onNavigateToNode,
}: Props) {
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    node: TreeNode;
  } | null>(null);

  const handleActivate = useCallback((node: any) => {
    onSelect(node.data);
  }, [onSelect]);

  const closeContextMenu = useCallback(() => {
    setContextMenu(null);
  }, []);

  const handleContainerClick = useCallback(() => {
    if (contextMenu) closeContextMenu();
  }, [contextMenu, closeContextMenu]);

  // Inline NodeRenderer so context menu handler is captured via closure
  const NodeRenderer = useCallback(({ node, style, dragHandle }: any) => {
    const data = node.data as TreeNode;
    const tier = confidenceTier(data.confidence);
    const kindColor = KIND_COLORS[data.objectKind] || '#565f89';
    const fontSize = FONT_SIZES[tier];
    const isDraft = data.status === 'draft';

    return (
      <div
        style={{
          ...style,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '2px 8px',
          cursor: 'pointer',
          fontSize,
          color: isDraft ? C.muted : C.text,
          fontStyle: isDraft ? 'italic' : 'normal',
          borderLeft: `3px solid ${kindColor}`,
        }}
        ref={dragHandle}
        onContextMenu={(e) => {
          e.preventDefault();
          setContextMenu({ x: e.clientX, y: e.clientY, node: data });
        }}
      >
        <span style={{
          display: 'inline-block',
          fontSize: 9,
          fontWeight: 600,
          color: kindColor,
          background: `${kindColor}22`,
          borderRadius: 3,
          padding: '1px 5px',
          whiteSpace: 'nowrap',
        }}>
          {KIND_LABELS[data.objectKind] || data.objectKind || '?'}
        </span>
        <span style={{
          flex: 1,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>
          {data.name || data.content?.slice(0, 60) || '(unnamed)'}
        </span>
        <span style={{
          fontSize: 9,
          color: kindColor,
          opacity: 0.7,
          minWidth: 40,
          textAlign: 'right',
        }}>
          {data.confidence !== null && data.confidence !== undefined
            ? `${Math.round(data.confidence * 100)}% ${CONFIDENCE_LABELS[tier]}`
            : '—'}
        </span>
      </div>
    );
  }, []);

  return (
    <div
      onClick={handleContainerClick}
      style={{
        height: '100%',
        background: C.bg,
        overflow: 'auto',
        fontSize: 12,
      }}
    >
      <Tree
        data={tree}
        height={typeof window !== 'undefined' ? window.innerHeight - 100 : 600}
        width="100%"
        indent={20}
        rowHeight={28}
        openByDefault={true}
        selection={selectedId || undefined}
        onActivate={handleActivate}
      >
        {NodeRenderer}
      </Tree>

      {/* Context Menu */}
      {contextMenu && (
        <>
          <div
            onClick={closeContextMenu}
            style={{
              position: 'fixed', inset: 0, zIndex: 100,
            }}
          />
          <div style={{
            position: 'fixed',
            left: contextMenu.x,
            top: contextMenu.y,
            zIndex: 101,
            background: C.panel,
            border: `1px solid ${C.border}`,
            borderRadius: 8,
            padding: '4px 0',
            minWidth: 180,
            boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
          }}>
            <ContextMenuItem
              label="View Arguments"
              onClick={() => {
                onViewArguments(contextMenu.node.id);
                closeContextMenu();
              }}
            />
            {onNavigateToNode && (
              <ContextMenuItem
                label="Show in Graph"
                onClick={() => {
                  onNavigateToNode(contextMenu.node.id);
                  closeContextMenu();
                }}
              />
            )}
            <ContextMenuItem
              label={`Edit "${(contextMenu.node.name || '').slice(0, 30)}"`}
              onClick={() => {
                onEdit(contextMenu.node);
                closeContextMenu();
              }}
            />
            <ContextMenuItem
              label={`Delete "${(contextMenu.node.name || '').slice(0, 30)}"`}
              onClick={() => {
                onDelete(contextMenu.node);
                closeContextMenu();
              }}
              danger
            />
          </div>
        </>
      )}
    </div>
  );
}

function ContextMenuItem({
  label,
  onClick,
  danger,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        padding: '6px 14px',
        cursor: 'pointer',
        color: danger ? C.nand : C.text,
        fontSize: 12,
        background: hovered ? C.surface : 'transparent',
      }}
    >
      {label}
    </div>
  );
}
