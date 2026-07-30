import { useState, useCallback, useRef, useMemo, useEffect } from 'react';
import { C } from '../../constants';
import { useOntologyTypes } from '../../hooks/useOntologyTypes';

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

type LayoutNode = TreeNode & {
  depth: number;
  x: number;
  y: number;
  width: number;
  height: number;
  parentId: string | null;
  childIndex: number;
  totalSiblings: number;
  children: LayoutNode[];
};

/** Flatten tree into a single array — ALL nodes at ALL depths. */
function flattenTree(
  nodes: TreeNode[],
  depth: number,
  parentId: string | null,
  result: LayoutNode[],
): void {
  nodes.forEach((node, i) => {
    const layoutNode: LayoutNode = {
      ...node,
      depth,
      x: 0,
      y: 0,
      width: 0,
      height: 0,
      parentId,
      childIndex: i,
      totalSiblings: nodes.length,
      children: [],
    };
    result.push(layoutNode);
    if (node.children?.length) {
      // Push ALL descendants into result — children relationship rebuilt post-flatten
      flattenTree(node.children, depth + 1, node.id, result);
    }
  });
}

const BOX_WIDTH = 200;
const BOX_HEIGHT = 80;
const LEVEL_GAP = 100;
const NODE_GAP = 24;

export default function HierarchyChart({
  tree,
  selectedId,
  onSelect,
  onViewArguments,
  onEdit,
  onDelete,
  onNavigateToNode,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(1200);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      setContainerWidth(entries[0].contentRect.width);
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; node: TreeNode } | null>(null);

  // Fetch dynamic ontology types from backend (labels + colors)
  const { labels: kindLabels, colors: kindColors } = useOntologyTypes();

  const { levels, totalHeight, flat, maxDepth, byDepth } = useMemo(() => {
    const flat: LayoutNode[] = [];
    flattenTree(tree, 0, null, flat);

    // Rebuild parent→children relationships (flattenTree pushes all nodes into flat,
    // so children arrays are empty — rebuild via parentId lookup)
    const nodeMap = new Map<string, LayoutNode>();
    flat.forEach((n) => nodeMap.set(n.id, n));
    flat.forEach((n) => {
      if (n.parentId && nodeMap.has(n.parentId)) {
        nodeMap.get(n.parentId)!.children.push(n);
      }
    });

    // Group by depth
    const byDepth: Record<number, LayoutNode[]> = {};
    let maxDepth = 0;
    flat.forEach((n) => {
      if (!byDepth[n.depth]) byDepth[n.depth] = [];
      byDepth[n.depth].push(n);
      maxDepth = Math.max(maxDepth, n.depth);
    });

    // Compute positions
    const levels: LayoutNode[][] = [];
    for (let d = 0; d <= maxDepth; d++) {
      const nodes = byDepth[d] || [];
      const totalWidth = nodes.length * BOX_WIDTH + (nodes.length - 1) * NODE_GAP;
      const startX = Math.max(0, (containerWidth - totalWidth) / 2);

      nodes.forEach((n, i) => {
        n.x = startX + i * (BOX_WIDTH + NODE_GAP);
        n.y = d * (BOX_HEIGHT + LEVEL_GAP);
        n.width = BOX_WIDTH;
        n.height = BOX_HEIGHT;
      });

      levels.push(nodes);
    }

    const totalHeight = (maxDepth + 1) * (BOX_HEIGHT + LEVEL_GAP) - LEVEL_GAP + 40;
    return { levels, totalHeight, flat, maxDepth, byDepth };
  }, [tree, containerWidth]);

  const handleContextMenu = useCallback((e: React.MouseEvent, node: TreeNode) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, node });
  }, []);

  const closeContextMenu = useCallback(() => setContextMenu(null), []);

  // Lines connecting parents to children
  const lines = useMemo(() => {
    const svgLines: { x1: number; y1: number; x2: number; y2: number; key: string }[] = [];
    levels.forEach((levelNodes) => {
      levelNodes.forEach((parent) => {
        if (!parent.children?.length) return;
        parent.children.forEach((child) => {
          const cx = parent.x + BOX_WIDTH / 2;
          const cy = parent.y + BOX_HEIGHT;
          const cx2 = child.x + BOX_WIDTH / 2;
          const cy2 = child.y;
          svgLines.push({ x1: cx, y1: cy, x2: cx2, y2: cy2, key: `${parent.id}-${child.id}` });

          // If multiple children, draw a horizontal connector at sibling level
          if (parent.children.length > 1) {
            const firstChild = parent.children[0];
            const lastChild = parent.children[parent.children.length - 1];
            const midY = cy + (cy2 - cy) / 2;
            const leftX = firstChild.x + BOX_WIDTH / 2;
            const rightX = lastChild.x + BOX_WIDTH / 2;
            if (leftX !== rightX) {
              svgLines.push({ x1: leftX, y1: midY, x2: rightX, y2: midY, key: `${parent.id}-hconn` });
            }
          }
        });
      });
    });
    return svgLines;
  }, [levels]);

  return (
    <div
      ref={containerRef}
      onClick={closeContextMenu}
      style={{
        width: '100%',
        height: '100%',
        overflow: 'auto',
        background: C.bg,
        position: 'relative',
      }}
    >
      <div style={{ width: containerWidth, height: totalHeight, position: 'relative', minWidth: '100%' }}>
        {/* SVG lines layer */}
        <svg
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            pointerEvents: 'none',
            zIndex: 0,
          }}
        >
          {lines.map((l) => (
            <line
              key={l.key}
              x1={l.x1}
              y1={l.y1}
              x2={l.x2}
              y2={l.y2}
              stroke={C.border}
              strokeWidth={1.5}
            />
          ))}
        </svg>

        {/* Boxes layer */}
        {levels.map((levelNodes, depth) =>
          levelNodes.map((node) => {
            const isSelected = node.id === selectedId;
            const kindColor = kindColors[node.objectKind] || C.muted;
            const confPct = node.confidence != null ? Math.round(node.confidence * 100) : null;
            const isDraft = node.status === 'draft';

            return (
              <div
                key={node.id}
                onClick={(e) => { e.stopPropagation(); onSelect(node); }}
                onContextMenu={(e) => handleContextMenu(e, node)}
                style={{
                  position: 'absolute',
                  left: node.x,
                  top: node.y,
                  width: BOX_WIDTH,
                  height: BOX_HEIGHT,
                  background: isSelected ? C.surface : C.panel,
                  border: `${isSelected ? 3 : 1.5}px solid ${isSelected ? kindColor : C.border}`,
                  borderRadius: 10,
                  padding: '8px 12px',
                  cursor: 'pointer',
                  zIndex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                  transition: 'border 0.15s, background 0.15s',
                  opacity: isDraft ? 0.6 : 1,
                }}
              >
                {/* Kind badge */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{
                    fontSize: 9,
                    fontWeight: 700,
                    color: kindColor,
                    background: `${kindColor}22`,
                    borderRadius: 4,
                    padding: '1px 6px',
                    textTransform: 'uppercase',
                    letterSpacing: 0.5,
                  }}>
                    {kindLabels[node.objectKind] || node.objectKind}
                  </span>
                </div>

                {/* Name */}
                <div style={{
                  fontSize: 12,
                  fontWeight: 600,
                  color: C.text,
                  lineHeight: 1.3,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                }}>
                  {node.name || '(unnamed)'}
                </div>

                {/* Confidence bar */}
                <div style={{ marginTop: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
                  <div style={{
                    flex: 1,
                    height: 4,
                    background: C.border,
                    borderRadius: 2,
                    overflow: 'hidden',
                  }}>
                    <div style={{
                      width: `${confPct || 0}%`,
                      height: '100%',
                      background: confPct != null
                        ? confPct >= 70 ? '#9ece6a' : confPct >= 40 ? '#e0af68' : '#f7768e'
                        : 'transparent',
                      borderRadius: 2,
                      transition: 'width 0.3s',
                    }} />
                  </div>
                  <span style={{
                    fontSize: 10,
                    color: confPct != null ? C.text : C.muted,
                    fontWeight: 600,
                    minWidth: 32,
                    textAlign: 'right',
                  }}>
                    {confPct != null ? `${confPct}%` : '—'}
                  </span>
                </div>
              </div>
            );
          }),
        )}
      </div>

      {/* Debug footer — level counts */}
      <div style={{
        position: 'absolute',
        left: 0,
        bottom: 0,
        width: '100%',
        padding: '8px 16px',
        background: `${C.panel}dd`,
        backdropFilter: 'blur(6px)',
        borderTop: `1px solid ${C.border}`,
        fontSize: 11,
        fontFamily: 'monospace',
        color: C.muted,
        display: 'flex',
        gap: 16,
        zIndex: 10,
      }}>
        {Array.from({ length: maxDepth + 1 }, (_, d) => (
          <span key={d}>
            Level {d}: {(byDepth[d] || []).length} nodes
          </span>
        ))}
        <span style={{ marginLeft: 'auto' }}>Total: {flat.length} nodes</span>
      </div>

      {/* Context menu */}
      {contextMenu && (
        <>
          <div onClick={closeContextMenu} style={{ position: 'fixed', inset: 0, zIndex: 100 }} />
          <div style={{
            position: 'fixed', left: contextMenu.x, top: contextMenu.y, zIndex: 101,
            background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8,
            padding: '4px 0', minWidth: 180, boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
          }}>
            <MenuItem label="View Arguments" onClick={() => { onViewArguments(contextMenu.node.id); closeContextMenu(); }} />
            {onNavigateToNode && (
              <MenuItem label="Show in Graph" onClick={() => { onNavigateToNode!(contextMenu.node.id); closeContextMenu(); }} />
            )}
            <MenuItem label={`Edit "${(contextMenu.node.name || '').slice(0, 30)}"`} onClick={() => { onEdit(contextMenu.node); closeContextMenu(); }} />
            <MenuItem label={`Delete "${(contextMenu.node.name || '').slice(0, 30)}"`} onClick={() => { onDelete(contextMenu.node); closeContextMenu(); }} danger />
          </div>
        </>
      )}
    </div>
  );
}

function MenuItem({ label, onClick, danger }: { label: string; onClick: () => void; danger?: boolean }) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        padding: '6px 14px', cursor: 'pointer',
        color: danger ? C.nand : C.text, fontSize: 12,
        background: hovered ? C.surface : 'transparent',
      }}
    >
      {label}
    </div>
  );
}
