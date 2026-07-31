import { useState, useCallback, useRef, useMemo, useEffect } from 'react';
import { TransformWrapper, TransformComponent } from 'react-zoom-pan-pinch';
import type { ReactZoomPanPinchRef } from 'react-zoom-pan-pinch';
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
      flattenTree(node.children, depth + 1, node.id, result);
    }
  });
}

function reorderChildrenInTree(
  nodes: TreeNode[],
  movedNodeId: string,
  toIndex: number,
): TreeNode[] {
  const rootIdx = nodes.findIndex((n) => n.id === movedNodeId);
  if (rootIdx !== -1) {
    const reordered = [...nodes];
    const [moved] = reordered.splice(rootIdx, 1);
    reordered.splice(toIndex, 0, moved);
    return reordered;
  }
  return nodes.map((node) => {
    const childIdx = node.children.findIndex((c) => c.id === movedNodeId);
    if (childIdx !== -1) {
      const children = [...node.children];
      const [moved] = children.splice(childIdx, 1);
      children.splice(toIndex, 0, moved);
      return { ...node, children };
    }
    if (node.children.length > 0) {
      return { ...node, children: reorderChildrenInTree(node.children, movedNodeId, toIndex) };
    }
    return node;
  });
}

const BOX_WIDTH = 200;
const BOX_HEIGHT = 80;
const LEVEL_GAP = 100;
const NODE_GAP = 24;
const PLUS_SIZE = 20;

interface ConnectionState {
  sourceId: string;
  sourceX: number;
  sourceY: number;
  direction: 'top' | 'bottom';
  mouseX: number;
  mouseY: number;
}

interface CreateModalState {
  parentId: string | null;
  parentName: string;
  kind: string;
}

interface TransformState {
  positionX: number;
  positionY: number;
  scale: number;
}

interface ReorderState {
  nodeId: string;
  levelIndex: number;
  fromIndex: number;
  offsetX: number;
}

const DRAG_THRESHOLD = 5;

function ChartContent({
  levels,
  totalHeight,
  containerWidth,
  selectedId,
  connection,
  hoveredNodeId,
  kindColors,
  kindLabels,
  reorder,
  onSelect,
  onContextMenu,
  onStartConnection,
  onCanvasClickForConnection,
  onHoverNode,
  onBoxMouseDown,
  lines,
}: {
  levels: LayoutNode[][];
  totalHeight: number;
  containerWidth: number;
  selectedId: string | null;
  connection: ConnectionState | null;
  hoveredNodeId: string | null;
  kindColors: Record<string, string>;
  kindLabels: Record<string, string>;
  reorder: ReorderState | null;
  onSelect: (node: TreeNode) => void;
  onContextMenu: (e: React.MouseEvent, node: TreeNode) => void;
  onStartConnection: (e: React.MouseEvent, nodeId: string, direction: 'top' | 'bottom') => void;
  onCanvasClickForConnection: (targetId: string) => void;
  onHoverNode: (id: string | null) => void;
  onBoxMouseDown: (e: React.MouseEvent, node: LayoutNode, levelIdx: number, nodeIdx: number) => void;
  lines: { x1: number; y1: number; x2: number; y2: number; key: string }[];
}) {
  return (
    <div style={{ width: containerWidth, height: totalHeight, position: 'relative', minWidth: '100%' }}>
      {levels.map((levelNodes, levelIdx) =>
        levelNodes.map((node, nodeIdx) => {
          const isSelected = node.id === selectedId;
          const isHovered = node.id === hoveredNodeId;
          const isDragging = reorder?.nodeId === node.id;
          const kindColor = kindColors[node.objectKind] || C.muted;
          const confPct = node.confidence != null ? Math.round(node.confidence * 100) : null;
          const isDraft = node.status === 'draft';
          const isConnectionTarget = connection !== null && connection.sourceId !== node.id;

          return (
            <div
              key={node.id}
              onClick={(e) => {
                if (connection && connection.sourceId !== node.id) {
                  onCanvasClickForConnection(node.id);
                } else if (!reorder) {
                  onSelect(node);
                }
              }}
              onMouseDown={(e) => onBoxMouseDown(e, node, levelIdx, nodeIdx)}
              onContextMenu={(e) => onContextMenu(e, node)}
              onMouseEnter={() => onHoverNode(node.id)}
              onMouseLeave={() => onHoverNode(null)}
              style={{
                position: 'absolute',
                left: node.x,
                top: node.y,
                width: BOX_WIDTH,
                height: BOX_HEIGHT,
                background: isSelected ? C.surface : C.panel,
                border: `${isSelected ? 2.5 : 1.5}px solid ${
                  connection && connection.sourceId === node.id
                    ? '#e0af68'
                    : isSelected
                      ? kindColor
                      : isConnectionTarget && connection
                        ? `${kindColor}aa`
                        : C.border
                }`,
                borderRadius: 10,
                padding: '8px 12px',
                cursor: isDragging
                  ? 'grabbing'
                  : connection
                    ? connection.sourceId === node.id
                      ? 'default'
                      : 'crosshair'
                    : 'grab',
                zIndex: isDragging ? 5 : isHovered ? 2 : 1,
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
                transition: isDragging
                  ? 'none'
                  : 'border 0.15s, background 0.15s, transform 0.15s, box-shadow 0.15s',
                opacity: isDraft ? 0.6 : 1,
                transform: isHovered && !isDragging && !connection ? 'scale(1.03)' : 'scale(1)',
                boxShadow: isSelected
                  ? `0 0 20px ${kindColor}44, 0 0 8px ${kindColor}22`
                  : isDragging
                    ? '0 8px 32px rgba(0,0,0,0.5), 0 0 16px rgba(224,175,104,0.3)'
                    : connection && connection.sourceId === node.id
                      ? '0 0 16px rgba(224, 175, 104, 0.4)'
                      : undefined,
                userSelect: 'none',
              }}
            >
              {isHovered && !connection && !isDragging && (
                <>
                  <div
                    onClick={(e) => {
                      e.stopPropagation();
                      onStartConnection(e, node.id, 'top');
                    }}
                    style={{
                      position: 'absolute',
                      left: '50%',
                      top: -PLUS_SIZE / 2,
                      transform: 'translateX(-50%)',
                      width: PLUS_SIZE,
                      height: PLUS_SIZE,
                      borderRadius: '50%',
                      background: C.accent,
                      border: `2px solid ${C.bg}`,
                      color: '#fff',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 14,
                      fontWeight: 700,
                      cursor: 'pointer',
                      zIndex: 6,
                      lineHeight: 1,
                      boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
                    }}
                  >
                    +
                  </div>
                  <div
                    onClick={(e) => {
                      e.stopPropagation();
                      onStartConnection(e, node.id, 'bottom');
                    }}
                    style={{
                      position: 'absolute',
                      left: '50%',
                      bottom: -PLUS_SIZE / 2,
                      transform: 'translateX(-50%)',
                      width: PLUS_SIZE,
                      height: PLUS_SIZE,
                      borderRadius: '50%',
                      background: C.accent,
                      border: `2px solid ${C.bg}`,
                      color: '#fff',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 14,
                      fontWeight: 700,
                      cursor: 'pointer',
                      zIndex: 6,
                      lineHeight: 1,
                      boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
                    }}
                  >
                    +
                  </div>
                </>
              )}

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
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  maxWidth: 180,
                }}>
                  {kindLabels[node.objectKind] || node.objectKind}
                </span>
              </div>

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
                  {confPct != null ? `${confPct}%` : '\u2014'}
                </span>
              </div>
            </div>
          );
        }),
      )}

      <svg
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          pointerEvents: 'none',
          zIndex: 3,
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
    </div>
  );
}

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
  const wrapperElRef = useRef<HTMLDivElement | null>(null);
  const transformRef = useRef<ReactZoomPanPinchRef>(null);
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
  const { labels: kindLabels, colors: kindColors, types, loading: typesLoading } = useOntologyTypes();
  const [connection, setConnection] = useState<ConnectionState | null>(null);
  const [addSubmenu, setAddSubmenu] = useState(false);
  const [kindSearch, setKindSearch] = useState('');
  const [createModal, setCreateModal] = useState<CreateModalState | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [transformState, setTransformState] = useState<TransformState>({ positionX: 0, positionY: 0, scale: 1 });
  const [localTree, setLocalTree] = useState<TreeNode[]>(tree);
  useEffect(() => { setLocalTree(tree); }, [tree]);
  const [reorder, setReorder] = useState<ReorderState | null>(null);

  const dragStartRef = useRef<{
    nodeId: string;
    levelIdx: number;
    nodeIdx: number;
    startX: number;
    startY: number;
    transformAtStart: { positionX: number; positionY: number; scale: number };
  } | null>(null);
  const byDepthRef = useRef<Record<number, LayoutNode[]>>({});

  useEffect(() => {
    if (!connection) return;
    const handler = (e: MouseEvent) => {
      setConnection((prev) => prev ? { ...prev, mouseX: e.clientX, mouseY: e.clientY } : null);
    };
    window.addEventListener('mousemove', handler);
    return () => window.removeEventListener('mousemove', handler);
  }, [connection]);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!dragStartRef.current) return;
      const dx = e.clientX - dragStartRef.current.startX;
      const dy = e.clientY - dragStartRef.current.startY;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist > DRAG_THRESHOLD && Math.abs(dx) > Math.abs(dy) * 1.2) {
        if (!reorder) {
          transformRef.current?.resetTransform();
          setReorder({ nodeId: dragStartRef.current.nodeId, levelIndex: dragStartRef.current.levelIdx, fromIndex: dragStartRef.current.nodeIdx, offsetX: dx });
        } else {
          setReorder((prev) => (prev ? { ...prev, offsetX: dx } : null));
        }
      }
    };
    const handleMouseUp = (e: MouseEvent) => {
      const drag = dragStartRef.current;
      if (!drag) return;
      if (reorder) {
        const finalDx = e.clientX - drag.startX;
        const levelNodes = byDepthRef.current[drag.levelIdx] || [];
        if (levelNodes.length > 1) {
          const slotWidth = BOX_WIDTH + NODE_GAP;
          const delta = Math.round(finalDx / slotWidth);
          const newIdx = Math.max(0, Math.min(drag.nodeIdx + delta, levelNodes.length - 1));
          if (newIdx !== drag.nodeIdx) {
            setLocalTree((prev) => reorderChildrenInTree(prev, drag.nodeId, newIdx));
          }
        }
        setReorder(null);
      }
      dragStartRef.current = null;
    };
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      dragStartRef.current = null;
    };
  }, [reorder]);

  const handleBoxMouseDown = useCallback(
    (e: React.MouseEvent, node: LayoutNode, levelIdx: number, nodeIdx: number) => {
      if (e.button !== 0) return;
      if (connection) return;
      dragStartRef.current = { nodeId: node.id, levelIdx, nodeIdx, startX: e.clientX, startY: e.clientY, transformAtStart: { ...transformState } };
    },
    [connection, transformState],
  );

  const { levels, totalHeight, flat, maxDepth, byDepth, nodeMap } = useMemo(() => {
    const flat: LayoutNode[] = [];
    flattenTree(localTree, 0, null, flat);
    const nodeMap = new Map<string, LayoutNode>();
    flat.forEach((n) => nodeMap.set(n.id, n));
    flat.forEach((n) => { if (n.parentId && nodeMap.has(n.parentId)) nodeMap.get(n.parentId)!.children.push(n); });
    const byDepth: Record<number, LayoutNode[]> = {};
    let maxDepth = 0;
    flat.forEach((n) => { if (!byDepth[n.depth]) byDepth[n.depth] = []; byDepth[n.depth].push(n); maxDepth = Math.max(maxDepth, n.depth); });
    const levels: LayoutNode[][] = [];
    for (let d = 0; d <= maxDepth; d++) {
      const nodes = byDepth[d] || [];
      const isReorderLevel = reorder && d === reorder.levelIndex;
      const totalWidth = nodes.length * BOX_WIDTH + (nodes.length - 1) * NODE_GAP;
      const startX = Math.max(0, (containerWidth - totalWidth) / 2);
      nodes.forEach((n, i) => {
        let baseX = startX + i * (BOX_WIDTH + NODE_GAP);
        if (isReorderLevel && reorder) {
          const slotWidth = BOX_WIDTH + NODE_GAP;
          const targetDelta = Math.round(reorder.offsetX / slotWidth);
          const targetIdx = Math.max(0, Math.min(reorder.fromIndex + targetDelta, nodes.length - 1));
          if (i === reorder.fromIndex) { baseX += reorder.offsetX; }
          else if (targetIdx > reorder.fromIndex && i > reorder.fromIndex && i <= targetIdx) { baseX -= BOX_WIDTH + NODE_GAP; }
          else if (targetIdx < reorder.fromIndex && i >= targetIdx && i < reorder.fromIndex) { baseX += BOX_WIDTH + NODE_GAP; }
        }
        n.x = baseX;
        n.y = d * (BOX_HEIGHT + LEVEL_GAP);
        n.width = BOX_WIDTH;
        n.height = BOX_HEIGHT;
      });
      levels.push(nodes);
    }
    const totalHeight = (maxDepth + 1) * (BOX_HEIGHT + LEVEL_GAP) - LEVEL_GAP + 120;
    return { levels, totalHeight, flat, maxDepth, byDepth, nodeMap };
  }, [localTree, containerWidth, reorder]);

  useEffect(() => { byDepthRef.current = byDepth; }, [byDepth]);

  const handleContextMenu = useCallback((e: React.MouseEvent, node: TreeNode) => {
    e.preventDefault();
    e.stopPropagation();
    setAddSubmenu(false);
    setKindSearch('');
    setContextMenu({ x: e.clientX, y: e.clientY, node });
  }, []);

  const closeContextMenu = useCallback(() => {
    setContextMenu(null);
    setAddSubmenu(false);
    setKindSearch('');
  }, []);

  const startConnection = useCallback(
    (e: React.MouseEvent, nodeId: string, direction: 'top' | 'bottom') => {
      const node = nodeMap.get(nodeId);
      if (!node) return;
      closeContextMenu();
      setConnection({ sourceId: nodeId, sourceX: node.x + BOX_WIDTH / 2, sourceY: direction === 'top' ? node.y : node.y + BOX_HEIGHT, direction, mouseX: e.clientX, mouseY: e.clientY });
    },
    [nodeMap, closeContextMenu],
  );

  const handleCanvasClickForConnection = useCallback(
    async (targetId: string) => {
      if (!connection) return;
      if (targetId === connection.sourceId) { setConnection(null); return; }
      try {
        const res = await fetch('/api/edges', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source: connection.sourceId, target: targetId, type: 'hasPart' }) });
        if (!res.ok) { const detail = await res.json().catch(() => ({})); console.error('Edge creation failed:', detail); }
      } catch (err: unknown) { console.error('Edge creation error:', err); }
      setConnection(null);
    },
    [connection],
  );

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
          if (parent.children.length > 1) {
            const firstChild = parent.children[0];
            const lastChild = parent.children[parent.children.length - 1];
            const midY = cy + (cy2 - cy) / 2;
            const leftX = firstChild.x + BOX_WIDTH / 2;
            const rightX = lastChild.x + BOX_WIDTH / 2;
            if (leftX !== rightX) { svgLines.push({ x1: leftX, y1: midY, x2: rightX, y2: midY, key: `${parent.id}-hconn` }); }
          }
        });
      });
    });
    return svgLines;
  }, [levels]);

  const filteredKinds = useMemo(() => {
    if (!kindSearch.trim()) return types.map((t) => t.objectKind);
    const q = kindSearch.toLowerCase();
    return types.filter((t) => t.objectKind.toLowerCase().includes(q) || t.label.toLowerCase().includes(q)).map((t) => t.objectKind);
  }, [kindSearch, types]);

  const openCreateModal = (kind: string) => {
    setCreateModal({ parentId: contextMenu?.node.id || null, parentName: contextMenu?.node.name || '(unknown)', kind });
    closeContextMenu();
  };

  if (typesLoading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', background: C.bg }}>
        <div style={{ color: C.muted, fontSize: 14, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 24, height: 24, border: `2px solid ${C.border}`, borderTopColor: C.accent, borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
          <span>Loading ontology types...</span>
        </div>
        <style>{'@keyframes spin { to { transform: rotate(360deg); } }'}</style>
      </div>
    );
  }

  if (tree.length === 0) {
    return (
      <div ref={containerRef} style={{ width: '100%', height: '100%', background: C.bg, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
        <div style={{ color: C.muted, fontSize: 14 }}>No objects to display. Right-click on the canvas to create one.</div>
        <div style={{ color: C.muted, fontSize: 12, opacity: 0.6 }}>Use the context menu or the <strong style={{ color: C.accent }}>+ New</strong> button in the header.</div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onClick={() => { closeContextMenu(); if (connection) setConnection(null); }}
      style={{ width: '100%', height: '100%', overflow: 'hidden', background: C.bg, position: 'relative', cursor: connection ? 'crosshair' : 'default' }}
    >
      <TransformWrapper
        ref={transformRef}
        initialScale={1}
        minScale={0.15}
        maxScale={3}
        centerOnInit
        wheel={{ step: 0.1 }}
        panning={{ velocityDisabled: true }}
        onTransform={(_ref, state) => { setTransformState({ positionX: state.positionX, positionY: state.positionY, scale: state.scale }); }}
      >
        <TransformComponent
          wrapperStyle={{ width: '100%', height: '100%' }}
          contentStyle={{ position: 'relative', width: containerWidth, height: totalHeight }}
          wrapperProps={{ ref: (el: HTMLDivElement | null) => { wrapperElRef.current = el; } }}
        >
          <ChartContent
            levels={levels}
            totalHeight={totalHeight}
            containerWidth={containerWidth}
            selectedId={selectedId}
            connection={connection}
            hoveredNodeId={hoveredNodeId}
            kindColors={kindColors}
            kindLabels={kindLabels}
            reorder={reorder}
            onSelect={onSelect}
            onContextMenu={handleContextMenu}
            onStartConnection={startConnection}
            onCanvasClickForConnection={handleCanvasClickForConnection}
            onHoverNode={setHoveredNodeId}
            onBoxMouseDown={handleBoxMouseDown}
            lines={lines}
          />
        </TransformComponent>
      </TransformWrapper>

      {connection && (() => {
        const wrapperRect = wrapperElRef.current?.getBoundingClientRect();
        const containerRect = containerRef.current?.getBoundingClientRect();
        if (!wrapperRect || !containerRect) return null;
        const offsetX = wrapperRect.left - containerRect.left;
        const offsetY = wrapperRect.top - containerRect.top;
        const sx = offsetX + connection.sourceX * transformState.scale + transformState.positionX;
        const sy = offsetY + connection.sourceY * transformState.scale + transformState.positionY;
        const mx = connection.mouseX - containerRect.left;
        const my = connection.mouseY - containerRect.top;
        return (
          <svg style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 50 }}>
            <line x1={sx} y1={sy} x2={mx} y2={my} stroke="#e0af68" strokeWidth={2 / transformState.scale + 1} strokeDasharray={`${6 / transformState.scale} ${4 / transformState.scale}`} opacity={0.8} />
            <circle cx={mx} cy={my} r={6} fill="#e0af68" opacity={0.6} />
          </svg>
        );
      })()}

      <div style={{ position: 'absolute', left: 0, bottom: 0, width: '100%', padding: '8px 16px', background: `${C.panel}dd`, backdropFilter: 'blur(6px)', borderTop: `1px solid ${C.border}`, fontSize: 11, fontFamily: 'monospace', color: C.muted, display: 'flex', gap: 16, zIndex: 10 }}>
        {Array.from({ length: maxDepth + 1 }, (_, d) => (<span key={d}>Level {d}: {(byDepth[d] || []).length} nodes</span>))}
        <span style={{ marginLeft: 'auto' }}>Total: {flat.length} nodes</span>
      </div>

      {contextMenu && (
        <>
          <div onClick={closeContextMenu} style={{ position: 'fixed', inset: 0, zIndex: 100 }} />
          <div style={{ position: 'fixed', left: contextMenu.x, top: contextMenu.y, zIndex: 101, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: '4px 0', minWidth: 200, boxShadow: '0 8px 32px rgba(0,0,0,0.6)' }}>
            <div onMouseEnter={() => { setAddSubmenu(true); setKindSearch(''); }} onMouseLeave={() => setAddSubmenu(false)} style={{ position: 'relative' }}>
              <MenuItem label="Add Object →" onClick={() => {}} noHover />
              {addSubmenu && (
                <div onMouseEnter={() => setAddSubmenu(true)} style={{ position: 'absolute', left: '100%', top: 0, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: '6px 0', minWidth: 220, boxShadow: '0 8px 32px rgba(0,0,0,0.6)', zIndex: 102 }}>
                  <div style={{ padding: '0 10px 6px' }}>
                    <input autoFocus value={kindSearch} onChange={(e) => setKindSearch(e.target.value)} placeholder="Search object types..." onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()} style={{ width: '100%', boxSizing: 'border-box', background: C.surface, border: `1px solid ${C.border}`, color: C.text, borderRadius: 4, padding: '5px 8px', fontSize: 11, outline: 'none', fontFamily: 'inherit' }} />
                  </div>
                  {filteredKinds.length === 0 ? (
                    <div style={{ padding: '8px 14px', color: C.muted, fontSize: 11 }}>No types match &quot;{kindSearch}&quot;</div>
                  ) : (
                    filteredKinds.map((kind) => (
                      <div key={kind} onClick={(e) => { e.stopPropagation(); openCreateModal(kind); }} style={{ padding: '6px 14px', cursor: 'pointer', fontSize: 12, color: C.text, display: 'flex', alignItems: 'center', gap: 8 }} onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = C.surface; }} onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}>
                        <span style={{ width: 8, height: 8, borderRadius: '50%', background: kindColors[kind] || C.muted, flexShrink: 0 }} />
                        {kindLabels[kind] || kind}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
            <MenuItem label="View Arguments" onClick={() => { onViewArguments(contextMenu.node.id); closeContextMenu(); }} />
            {onNavigateToNode && <MenuItem label="Show in Graph" onClick={() => { onNavigateToNode!(contextMenu.node.id); closeContextMenu(); }} />}
            <MenuItem label={`Edit "${(contextMenu.node.name || '').slice(0, 30)}"`} onClick={() => { onEdit(contextMenu.node); closeContextMenu(); }} />
            <MenuItem label={`Delete "${(contextMenu.node.name || '').slice(0, 30)}"`} onClick={() => { onDelete(contextMenu.node); closeContextMenu(); }} danger />
          </div>
        </>
      )}

      {createModal && (
        <CreateDialog parentId={createModal.parentId} parentName={createModal.parentName} defaultKind={createModal.kind} kindLabels={kindLabels} types={types} onClose={() => setCreateModal(null)} />
      )}
    </div>
  );
}

function MenuItem({ label, onClick, danger, noHover }: { label: string; onClick: () => void; danger?: boolean; noHover?: boolean }) {
  const [hovered, setHovered] = useState(false);
  return (
    <div onClick={onClick} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} style={{ padding: '6px 14px', cursor: noHover ? 'default' : 'pointer', color: danger ? C.nand : C.text, fontSize: 12, background: hovered && !noHover ? C.surface : 'transparent' }}>
      {label}
    </div>
  );
}

function CreateDialog({ parentId, parentName, defaultKind, kindLabels, types, onClose }: { parentId: string | null; parentName: string; defaultKind: string; kindLabels: Record<string, string>; types: { objectKind: string; label: string }[]; onClose: () => void }) {
  const [name, setName] = useState('');
  const [objectKind, setObjectKind] = useState(defaultKind);
  const [content, setContent] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (!name.trim()) { setError('Name is required'); return; }
    setSubmitting(true);
    setError(null);
    try {
      const body: Record<string, string> = { name: name.trim(), objectKind, context: 'product-strategy', content: content.trim() || name.trim() };
      if (parentId) body.parentId = parentId;
      const res = await fetch('/api/ontology-object', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!res.ok) { const detail = await res.json().catch(() => ({})); throw new Error(detail?.detail?.error || detail?.error || `HTTP ${res.status}`); }
      onClose();
    } catch (err: unknown) { setError(err instanceof Error ? err.message : 'Create failed'); } finally { setSubmitting(false); }
  };

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, padding: 24, width: 440, maxWidth: '90vw', boxShadow: '0 12px 48px rgba(0,0,0,0.7)' }}>
        <h3 style={{ color: C.text, margin: '0 0 6px', fontSize: 16, fontWeight: 600 }}>Add Object</h3>
        {parentId && <div style={{ color: C.muted, fontSize: 11, marginBottom: 14 }}>Parent: {parentName}</div>}
        <Field label="Name *">
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Time-poor professionals" onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }} style={inputStyle} />
        </Field>
        <Field label="Object Kind *">
          <select value={objectKind} onChange={(e) => setObjectKind(e.target.value)} style={inputStyle}>
            {types.map((t) => (<option key={t.objectKind} value={t.objectKind}>{kindLabels[t.objectKind] || t.objectKind}</option>))}
          </select>
        </Field>
        <Field label="Content">
          <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="Description (defaults to name if empty)" rows={3} style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }} />
        </Field>
        {error && <div style={{ color: C.nand, fontSize: 12, marginTop: 8 }}>{error}</div>}
        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={btnStyle} disabled={submitting}>Cancel</button>
          <button onClick={handleSubmit} style={{ ...btnStyle, background: C.accent, color: '#fff', border: 'none' }} disabled={submitting}>{submitting ? 'Creating...' : 'Create'}</button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ color: C.muted, fontSize: 11, marginBottom: 4, fontWeight: 500 }}>{label}</div>
      {children}
    </div>
  );
}

const inputStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box', background: C.surface, border: `1px solid ${C.border}`, color: C.text, borderRadius: 6, padding: '8px 10px', fontSize: 13, outline: 'none', fontFamily: 'inherit' };
const btnStyle: React.CSSProperties = { background: 'transparent', border: `1px solid ${C.border}`, color: C.text, borderRadius: 6, padding: '6px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 500 };
