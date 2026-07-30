import { useState, useCallback, useRef, useMemo, useEffect } from 'react';
import { TransformWrapper, TransformComponent } from 'react-zoom-pan-pinch';
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
  valueProposition: '#bb9af7',
  useCase: '#e0af68',
  feature: '#ff9e64',
  userJourney: '#7dcfff',
  workflow: '#c0caf5',
  requirement: '#f7768e',
};

const KIND_LABELS: Record<string, string> = {
  customerSegment: 'Segment',
  jobToBeDone: 'JTBD',
  valueProposition: 'Value Prop',
  useCase: 'Use Case',
  feature: 'Feature',
  userJourney: 'Journey',
  workflow: 'Workflow',
  requirement: 'Requirement',
};

const VALID_OBJECT_KINDS = [
  'customerSegment', 'jobToBeDone', 'feature',
  'userJourney', 'workflow', 'requirement',
];

const OBJECT_KIND_LABELS: Record<string, string> = {
  customerSegment: 'Customer Segment',
  jobToBeDone: 'Job to Be Done',
  feature: 'Feature',
  userJourney: 'User Journey',
  workflow: 'Workflow',
  requirement: 'Requirement',
};

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
      flattenTree(node.children, depth + 1, node.id, result);
    }
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

/** Inner content component */
function ChartContent({
  levels,
  totalHeight,
  containerWidth,
  selectedId,
  connection,
  hoveredNodeId,
  onSelect,
  onContextMenu,
  onStartConnection,
  onCanvasClickForConnection,
  onHoverNode,
  lines,
}: {
  levels: LayoutNode[][];
  totalHeight: number;
  containerWidth: number;
  selectedId: string | null;
  connection: ConnectionState | null;
  hoveredNodeId: string | null;
  onSelect: (node: TreeNode) => void;
  onContextMenu: (e: React.MouseEvent, node: TreeNode) => void;
  onStartConnection: (e: React.MouseEvent, nodeId: string, direction: 'top' | 'bottom') => void;
  onCanvasClickForConnection: (targetId: string) => void;
  onHoverNode: (id: string | null) => void;
  lines: { x1: number; y1: number; x2: number; y2: number; key: string }[];
}) {
  return (
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
      {levels.map((levelNodes) =>
        levelNodes.map((node) => {
          const isSelected = node.id === selectedId;
          const isHovered = node.id === hoveredNodeId;
          const kindColor = KIND_COLORS[node.objectKind] || C.muted;
          const confPct = node.confidence != null ? Math.round(node.confidence * 100) : null;
          const isDraft = node.status === 'draft';
          const isConnectionTarget = connection !== null && connection.sourceId !== node.id;

          return (
            <div
              key={node.id}
              onClick={(e) => {
                e.stopPropagation();
                if (connection && connection.sourceId !== node.id) {
                  onCanvasClickForConnection(node.id);
                } else {
                  onSelect(node);
                }
              }}
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
                border: `${isSelected ? 3 : 1.5}px solid ${
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
                cursor: connection
                  ? connection.sourceId === node.id
                    ? 'default'
                    : 'crosshair'
                  : 'pointer',
                zIndex: connection && connection.sourceId === node.id ? 3 : 1,
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
                transition: 'border 0.15s, background 0.15s',
                opacity: isDraft ? 0.6 : 1,
                boxShadow: connection && connection.sourceId === node.id
                  ? '0 0 16px rgba(224, 175, 104, 0.4)'
                  : undefined,
              }}
            >
              {/* + Connection buttons — top and bottom */}
              {isHovered && !connection && (
                <>
                  <div
                    onClick={(e) => onStartConnection(e, node.id, 'top')}
                    onMouseDown={(e) => e.stopPropagation()}
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
                      zIndex: 5,
                      lineHeight: 1,
                      boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
                    }}
                  >
                    +
                  </div>
                  <div
                    onClick={(e) => onStartConnection(e, node.id, 'bottom')}
                    onMouseDown={(e) => e.stopPropagation()}
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
                      zIndex: 5,
                      lineHeight: 1,
                      boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
                    }}
                  >
                    +
                  </div>
                </>
              )}

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
                  {KIND_LABELS[node.objectKind] || node.objectKind}
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

  // Context menu state
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; node: TreeNode } | null>(null);

  // Connection mode state (Miro-like)
  const [connection, setConnection] = useState<ConnectionState | null>(null);

  // "Add Object" submenu
  const [addSubmenu, setAddSubmenu] = useState(false);
  const [kindSearch, setKindSearch] = useState('');

  // Create modal
  const [createModal, setCreateModal] = useState<CreateModalState | null>(null);

  // Hover tracking for + buttons
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);

  // Transform state for coordinate conversion
  const [transformState, setTransformState] = useState<TransformState>({
    positionX: 0,
    positionY: 0,
    scale: 1,
  });

  // Track mouse position globally for connection line
  useEffect(() => {
    if (!connection) return;
    const handler = (e: MouseEvent) => {
      setConnection((prev) => {
        if (!prev) return null;
        return { ...prev, mouseX: e.clientX, mouseY: e.clientY };
      });
    };
    window.addEventListener('mousemove', handler);
    return () => window.removeEventListener('mousemove', handler);
  }, [connection]);

  const { levels, totalHeight, flat, maxDepth, byDepth, nodeMap } = useMemo(() => {
    const flat: LayoutNode[] = [];
    flattenTree(tree, 0, null, flat);

    const nodeMap = new Map<string, LayoutNode>();
    flat.forEach((n) => nodeMap.set(n.id, n));
    flat.forEach((n) => {
      if (n.parentId && nodeMap.has(n.parentId)) {
        nodeMap.get(n.parentId)!.children.push(n);
      }
    });

    const byDepth: Record<number, LayoutNode[]> = {};
    let maxDepth = 0;
    flat.forEach((n) => {
      if (!byDepth[n.depth]) byDepth[n.depth] = [];
      byDepth[n.depth].push(n);
      maxDepth = Math.max(maxDepth, n.depth);
    });

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

    const totalHeight = (maxDepth + 1) * (BOX_HEIGHT + LEVEL_GAP) - LEVEL_GAP + 120;
    return { levels, totalHeight, flat, maxDepth, byDepth, nodeMap };
  }, [tree, containerWidth]);

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

  // Connection handlers
  const startConnection = useCallback(
    (e: React.MouseEvent, nodeId: string, direction: 'top' | 'bottom') => {
      e.stopPropagation();
      e.preventDefault();
      const node = nodeMap.get(nodeId);
      if (!node) return;
      closeContextMenu();
      setConnection({
        sourceId: nodeId,
        sourceX: node.x + BOX_WIDTH / 2,
        sourceY: direction === 'top' ? node.y : node.y + BOX_HEIGHT,
        direction,
        mouseX: e.clientX,
        mouseY: e.clientY,
      });
    },
    [nodeMap, closeContextMenu],
  );

  const handleCanvasClickForConnection = useCallback(
    async (targetId: string) => {
      if (!connection) return;
      if (targetId === connection.sourceId) {
        setConnection(null);
        return;
      }

      // Create hasPart edge via API: create with parentId
      try {
        const targetNode = nodeMap.get(targetId);
        if (!targetNode) return;

        // The connection source is the parent, target is the child
        // We need to re-parent the target node: update it to have the source as parent
        // Create a new ontology-object with parentId = source
        // Actually, we need to create an edge from source to target
        // The API: POST /api/ontology-object with parentId creates a hasPart edge
        // But for re-parenting existing nodes, we should use the /api/edges endpoint
        const res = await fetch('/api/edges', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source: connection.sourceId,
            target: targetId,
            type: 'hasPart',
          }),
        });
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          console.error('Edge creation failed:', detail);
        }
        // Refresh handled by parent — we rely on tree re-fetch
      } catch (err) {
        console.error('Edge creation error:', err);
      }
      setConnection(null);
    },
    [connection, nodeMap],
  );

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

  // Filtered object kinds for submenu
  const filteredKinds = useMemo(() => {
    if (!kindSearch.trim()) return VALID_OBJECT_KINDS;
    const q = kindSearch.toLowerCase();
    return VALID_OBJECT_KINDS.filter(
      (k) =>
        k.toLowerCase().includes(q) ||
        (OBJECT_KIND_LABELS[k] || '').toLowerCase().includes(q),
    );
  }, [kindSearch]);

  const openCreateModal = (kind: string) => {
    setCreateModal({
      parentId: contextMenu?.node.id || null,
      parentName: contextMenu?.node.name || '(unknown)',
      kind,
    });
    closeContextMenu();
  };

  return (
    <div
      ref={containerRef}
      onClick={() => {
        closeContextMenu();
        // Cancel connection if clicking empty area
        if (connection) setConnection(null);
      }}
      style={{
        width: '100%',
        height: '100%',
        overflow: 'hidden',
        background: C.bg,
        position: 'relative',
        cursor: connection ? 'crosshair' : 'default',
      }}
    >
      <TransformWrapper
        initialScale={1}
        minScale={0.15}
        maxScale={3}
        centerOnInit
        wheel={{ step: 0.1 }}
        panning={{ velocityDisabled: true }}
      >
        <TransformComponent
          wrapperStyle={{ width: '100%', height: '100%' }}
          contentStyle={{ position: 'relative', width: containerWidth, height: totalHeight }}
          onTransformed={(ref) => {
            const state = ref.instance.transformState;
            setTransformState({
              positionX: state.positionX,
              positionY: state.positionY,
              scale: state.scale,
            });
          }}
          wrapperRef={(el) => { wrapperElRef.current = el; }}
        >
          <ChartContent
            levels={levels}
            totalHeight={totalHeight}
            containerWidth={containerWidth}
            selectedId={selectedId}
            connection={connection}
            hoveredNodeId={hoveredNodeId}
            onSelect={onSelect}
            onContextMenu={handleContextMenu}
            onStartConnection={startConnection}
            onCanvasClickForConnection={handleCanvasClickForConnection}
            onHoverNode={setHoveredNodeId}
            lines={lines}
          />
        </TransformComponent>
      </TransformWrapper>

      {/* Connection SVG overlay — viewport space, outside transform */}
      {connection && (
        <svg
          style={{
            position: 'absolute',
            inset: 0,
            pointerEvents: 'none',
            zIndex: 50,
          }}
        >
          {(() => {
            const wrapperRect = wrapperElRef.current?.getBoundingClientRect();
            if (!wrapperRect) return null;
            // Convert content-space source coords to viewport coords
            const sx = wrapperRect.left + connection.sourceX * transformState.scale + transformState.positionX;
            const sy = wrapperRect.top + connection.sourceY * transformState.scale + transformState.positionY;
            return (
              <>
                <line
                  x1={sx}
                  y1={sy}
                  x2={connection.mouseX}
                  y2={connection.mouseY}
                  stroke="#e0af68"
                  strokeWidth={2 / transformState.scale + 1}
                  strokeDasharray={`${6 / transformState.scale} ${4 / transformState.scale}`}
                  opacity={0.8}
                />
                <circle
                  cx={connection.mouseX}
                  cy={connection.mouseY}
                  r={6}
                  fill="#e0af68"
                  opacity={0.6}
                />
              </>
            );
          })()}
        </svg>
      )}

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
            padding: '4px 0', minWidth: 200, boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
          }}>
            {/* Add Object with hover submenu */}
            <div
              onMouseEnter={() => { setAddSubmenu(true); setKindSearch(''); }}
              onMouseLeave={() => setAddSubmenu(false)}
              style={{ position: 'relative' }}
            >
              <MenuItem
                label="Add Object →"
                onClick={() => {}}
                noHover
              />
              {addSubmenu && (
                <div
                  onMouseEnter={() => setAddSubmenu(true)}
                  style={{
                    position: 'absolute',
                    left: '100%',
                    top: 0,
                    background: C.panel,
                    border: `1px solid ${C.border}`,
                    borderRadius: 8,
                    padding: '6px 0',
                    minWidth: 220,
                    boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
                    zIndex: 102,
                  }}
                >
                  {/* Search bar */}
                  <div style={{ padding: '0 10px 6px' }}>
                    <input
                      autoFocus
                      value={kindSearch}
                      onChange={(e) => setKindSearch(e.target.value)}
                      placeholder="Search object types..."
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => e.stopPropagation()}
                      style={{
                        width: '100%',
                        boxSizing: 'border-box',
                        background: C.surface,
                        border: `1px solid ${C.border}`,
                        color: C.text,
                        borderRadius: 4,
                        padding: '5px 8px',
                        fontSize: 11,
                        outline: 'none',
                        fontFamily: 'inherit',
                      }}
                    />
                  </div>

                  {/* Object types list */}
                  {filteredKinds.length === 0 ? (
                    <div style={{ padding: '8px 14px', color: C.muted, fontSize: 11 }}>
                      No types match "{kindSearch}"
                    </div>
                  ) : (
                    filteredKinds.map((kind) => (
                      <div
                        key={kind}
                        onClick={(e) => {
                          e.stopPropagation();
                          openCreateModal(kind);
                        }}
                        style={{
                          padding: '6px 14px',
                          cursor: 'pointer',
                          fontSize: 12,
                          color: C.text,
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                        }}
                        onMouseEnter={(e) => {
                          (e.currentTarget as HTMLElement).style.background = C.surface;
                        }}
                        onMouseLeave={(e) => {
                          (e.currentTarget as HTMLElement).style.background = 'transparent';
                        }}
                      >
                        <span style={{
                          width: 8,
                          height: 8,
                          borderRadius: '50%',
                          background: KIND_COLORS[kind] || C.muted,
                          flexShrink: 0,
                        }} />
                        {OBJECT_KIND_LABELS[kind] || kind}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>

            <MenuItem label="View Arguments" onClick={() => { onViewArguments(contextMenu.node.id); closeContextMenu(); }} />
            {onNavigateToNode && (
              <MenuItem label="Show in Graph" onClick={() => { onNavigateToNode!(contextMenu.node.id); closeContextMenu(); }} />
            )}
            <MenuItem label={`Edit "${(contextMenu.node.name || '').slice(0, 30)}"`} onClick={() => { onEdit(contextMenu.node); closeContextMenu(); }} />
            <MenuItem label={`Delete "${(contextMenu.node.name || '').slice(0, 30)}"`} onClick={() => { onDelete(contextMenu.node); closeContextMenu(); }} danger />
          </div>
        </>
      )}

      {/* Create modal */}
      {createModal && (
        <CreateDialog
          parentId={createModal.parentId}
          parentName={createModal.parentName}
          defaultKind={createModal.kind}
          onClose={() => setCreateModal(null)}
        />
      )}
    </div>
  );
}

// ─────────────────── MenuItem ──────────────────

function MenuItem({
  label,
  onClick,
  danger,
  noHover,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
  noHover?: boolean;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        padding: '6px 14px',
        cursor: noHover ? 'default' : 'pointer',
        color: danger ? C.nand : C.text,
        fontSize: 12,
        background: hovered && !noHover ? C.surface : 'transparent',
      }}
    >
      {label}
    </div>
  );
}

// ─────────────────── CreateDialog ──────────────────

function CreateDialog({
  parentId,
  parentName,
  defaultKind,
  onClose,
}: {
  parentId: string | null;
  parentName: string;
  defaultKind: string;
  onClose: () => void;
}) {
  const [name, setName] = useState('');
  const [objectKind, setObjectKind] = useState(defaultKind);
  const [content, setContent] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (!name.trim()) {
      setError('Name is required');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body: Record<string, string> = {
        name: name.trim(),
        objectKind,
        context: 'product-strategy',
        content: content.trim() || name.trim(),
      };
      if (parentId) body.parentId = parentId;

      const res = await fetch('/api/ontology-object', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail?.error || detail?.error || `HTTP ${res.status}`);
      }
      onClose();
    } catch (err: any) {
      setError(err.message || 'Create failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 200,
        background: 'rgba(0,0,0,0.7)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: C.panel,
          border: `1px solid ${C.border}`,
          borderRadius: 12,
          padding: 24,
          width: 440,
          maxWidth: '90vw',
          boxShadow: '0 12px 48px rgba(0,0,0,0.7)',
        }}
      >
        <h3 style={{ color: C.text, margin: '0 0 6px', fontSize: 16, fontWeight: 600 }}>
          Add Object
        </h3>
        {parentId && (
          <div style={{
            color: C.muted,
            fontSize: 11,
            marginBottom: 14,
          }}>
            Parent: {parentName}
          </div>
        )}

        <Field label="Name *">
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Time-poor professionals"
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSubmit();
            }}
            style={inputStyle}
          />
        </Field>

        <Field label="Object Kind *">
          <select
            value={objectKind}
            onChange={(e) => setObjectKind(e.target.value)}
            style={inputStyle}
          >
            {VALID_OBJECT_KINDS.map((k) => (
              <option key={k} value={k}>
                {OBJECT_KIND_LABELS[k] || k}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Content">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="Description (defaults to name if empty)"
            rows={3}
            style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }}
          />
        </Field>

        {error && (
          <div style={{ color: C.nand, fontSize: 12, marginTop: 8 }}>{error}</div>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
          <button
            onClick={onClose}
            style={btnStyle}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            style={{ ...btnStyle, background: C.accent, color: '#fff', border: 'none' }}
            disabled={submitting}
          >
            {submitting ? 'Creating...' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────── Shared UI components ──────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ color: C.muted, fontSize: 11, marginBottom: 4, fontWeight: 500 }}>
        {label}
      </div>
      {children}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  boxSizing: 'border-box',
  background: C.surface,
  border: `1px solid ${C.border}`,
  color: C.text,
  borderRadius: 6,
  padding: '8px 10px',
  fontSize: 13,
  outline: 'none',
  fontFamily: 'inherit',
};

const btnStyle: React.CSSProperties = {
  background: 'transparent',
  border: `1px solid ${C.border}`,
  color: C.text,
  borderRadius: 6,
  padding: '6px 16px',
  cursor: 'pointer',
  fontSize: 13,
  fontWeight: 500,
};
