import { useState, useEffect, useCallback, useMemo } from 'react';
import { C } from '../../constants';
import HierarchyChart from './HierarchyChart';
import ArgumentView from './ArgumentView';
import OntologyCrud, { type CrudAction } from './OntologyCrud';
import { useOntologyTypes } from '../../hooks/useOntologyTypes';

const CONTEXTS = ['product-strategy', 'licensing-decision', 'all'];

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

interface TreeResponse {
  tree: TreeNode[];
  total_nodes: number;
  context: string;
  filtered_from?: number;
  warning?: string;
}

export default function OntologyView({ onNavigateToNode, initialFocusId }: { onNavigateToNode?: (id: string) => void; initialFocusId?: string | null }) {
  const [context, setContext] = useState('product-strategy');
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [meta, setMeta] = useState<{ total_nodes: number; filtered_from?: number; warning?: string }>({ total_nodes: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<TreeNode | null>(null);
  const [view, setView] = useState<'tree' | 'arguments'>('tree');
  const [argumentsNodeId, setArgumentsNodeId] = useState<string | null>(initialFocusId || null);
  const [crud, setCrud] = useState<CrudAction | null>(null);
  const { colors: kindColors, labels: kindLabels } = useOntologyTypes();

  const fetchTree = useCallback(async (ctx: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/ontology-tree?context=${encodeURIComponent(ctx)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: TreeResponse = await res.json();
      setTree(data.tree);
      setMeta({
        total_nodes: data.total_nodes,
        filtered_from: data.filtered_from,
        warning: data.warning,
      });
    } catch (err: any) {
      setError(err.message || 'Failed to load ontology tree');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTree(context);
  }, [context, fetchTree]);

  // Auto-switch to argument view when initialFocusId is provided
  useEffect(() => {
    if (initialFocusId) {
      setArgumentsNodeId(initialFocusId);
      setView('arguments');
    }
  }, [initialFocusId]);

  const handleSelect = (node: TreeNode) => {
    setSelectedNode(node);
  };

  const handleViewArguments = (nodeId: string) => {
    setArgumentsNodeId(nodeId);
    setView('arguments');
  };

  const handleBackToTree = () => {
    setView('tree');
    setArgumentsNodeId(null);
  };

  const handleCreate = () => {
    setCrud({ type: 'create', node: null });
  };

  const handleEdit = (node: TreeNode) => {
    setCrud({ type: 'edit', node });
  };

  const handleDelete = (node: TreeNode) => {
    setCrud({ type: 'delete', node });
  };

  const handleCrudClose = (refresh?: boolean) => {
    setCrud(null);
    if (refresh) fetchTree(context);
  };

  const navigateToNode = (id: string) => {
    if (onNavigateToNode) {
      onNavigateToNode(id);
    }
  };

  // Find selected node's children from the tree
  const selectedNodeChildren = useMemo(() => {
    if (!selectedNode) return [];
    const findNode = (nodes: TreeNode[], targetId: string): TreeNode | null => {
      for (const n of nodes) {
        if (n.id === targetId) return n;
        if (n.children?.length) {
          const found = findNode(n.children, targetId);
          if (found) return found;
        }
      }
      return null;
    };
    const found = findNode(tree, selectedNode.id);
    return found?.children || [];
  }, [tree, selectedNode]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: C.bg }}>
      {/* Header bar */}
      <div style={{
        padding: '8px 14px', display: 'flex', gap: 8, alignItems: 'center',
        borderBottom: `1px solid ${C.border}`, background: C.panel, zIndex: 10,
      }}>
        <span style={{ color: C.text, fontWeight: 800, fontSize: 16 }}>Ontology</span>
        <span style={{ color: C.muted, fontSize: 11 }}>
          {meta.total_nodes} nodes
          {meta.filtered_from && meta.filtered_from !== meta.total_nodes
            ? ` / ${meta.filtered_from} total`
            : ''}
        </span>
        <div style={{ flex: 1 }} />
        <select
          value={context}
          onChange={e => setContext(e.target.value)}
          style={{
            background: C.surface, border: `1px solid ${C.border}`,
            color: C.text, borderRadius: 6, padding: '4px 10px', fontSize: 12,
            outline: 'none', cursor: 'pointer',
          }}
        >
          {CONTEXTS.map(c => (
            <option key={c} value={c}>{c === 'all' ? 'All Contexts' : c}</option>
          ))}
        </select>
        <button
          onClick={handleCreate}
          style={{
            background: C.accent, border: 'none', color: '#fff',
            borderRadius: 6, padding: '4px 14px', fontSize: 12,
            cursor: 'pointer', fontWeight: 600,
          }}
        >
          + New
        </button>
        <button
          onClick={() => fetchTree(context)}
          style={{
            background: 'transparent', border: `1px solid ${C.border}`,
            color: C.muted, borderRadius: 6, padding: '4px 10px', fontSize: 12,
            cursor: 'pointer',
          }}
        >
          ↺
        </button>
      </div>

      {/* Warning banner */}
      {meta.warning && (
        <div style={{
          padding: '6px 14px', background: '#2a2010', borderBottom: `1px solid ${C.mit}`,
          color: C.mit, fontSize: 11,
        }}>
          ⚠ {meta.warning}
        </div>
      )}

      {/* Main content */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {loading && (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            height: '100%', color: C.muted, fontSize: 14,
          }}>
            Loading ontology...
          </div>
        )}

        {error && (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center',
            justifyContent: 'center', height: '100%', gap: 12,
          }}>
            <div style={{ color: C.nand, fontSize: 14 }}>Error: {error}</div>
            <button
              onClick={() => fetchTree(context)}
              style={{
                background: C.surface, border: `1px solid ${C.border}`,
                color: C.text, borderRadius: 6, padding: '6px 16px',
                cursor: 'pointer', fontSize: 13,
              }}
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !error && tree.length === 0 && (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center',
            justifyContent: 'center', height: '100%', gap: 12,
          }}>
            <div style={{ color: C.muted, fontSize: 14 }}>
              No ontology objects in "{context}" context.
            </div>
            <button
              onClick={handleCreate}
              style={{
                background: C.accent, border: 'none', color: '#fff',
                borderRadius: 6, padding: '8px 20px', cursor: 'pointer',
                fontSize: 14, fontWeight: 600,
              }}
            >
              Create First Object
            </button>
          </div>
        )}

        {!loading && !error && tree.length > 0 && view === 'tree' && (
          <div style={{ display: 'flex', height: '100%' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <HierarchyChart
                tree={tree}
                selectedId={selectedNode?.id || null}
                onSelect={handleSelect}
                onViewArguments={handleViewArguments}
                onEdit={handleEdit}
                onDelete={handleDelete}
                onNavigateToNode={navigateToNode}
                context={context}
                onRefresh={() => fetchTree(context)}
              />
            </div>
            {selectedNode && (
              <div style={{
                width: 320, minWidth: 280, maxWidth: 380,
                borderLeft: `1px solid ${C.border}`,
                background: C.panel,
                overflow: 'auto',
                display: 'flex', flexDirection: 'column',
              }}>
                {(() => {
                  const sn = selectedNode;
                  const kColor = kindColors[sn.objectKind] || C.muted;
                  const confPct = sn.confidence != null ? Math.round(sn.confidence * 100) : null;
                  return (
                    <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
                      {/* Header */}
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{
                            fontSize: 15, fontWeight: 700, color: C.text,
                            lineHeight: 1.3, wordBreak: 'break-word', marginBottom: 6,
                          }}>
                            {sn.name || '(unnamed)'}
                          </div>
                          <span style={{
                            fontSize: 10, fontWeight: 600, color: kColor,
                            background: `${kColor}22`, borderRadius: 4,
                            padding: '2px 8px', textTransform: 'uppercase',
                            letterSpacing: 0.5,
                          }}>
                            {kindLabels[sn.objectKind] || sn.objectKind}
                          </span>
                        </div>
                        <button
                          onClick={() => setSelectedNode(null)}
                          style={{
                            background: 'transparent', border: `1px solid ${C.border}`,
                            color: C.muted, borderRadius: 4, padding: '2px 8px',
                            cursor: 'pointer', fontSize: 14, lineHeight: 1,
                          }}
                        >
                          ×
                        </button>
                      </div>

                      {/* Content */}
                      {sn.content && sn.content !== sn.name && (
                        <div style={{
                          fontSize: 12, color: C.muted, lineHeight: 1.5,
                          padding: '8px 10px', background: C.surface,
                          borderRadius: 6, wordBreak: 'break-word',
                        }}>
                          {sn.content}
                        </div>
                      )}

                      {/* Confidence */}
                      <div>
                        <div style={{ fontSize: 10, color: C.muted, marginBottom: 4, fontWeight: 500 }}>
                          Confidence
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <div style={{
                            flex: 1, height: 6, background: C.surface,
                            borderRadius: 3, overflow: 'hidden',
                          }}>
                            <div style={{
                              width: `${confPct || 0}%`, height: '100%',
                              background: confPct != null
                                ? confPct >= 70 ? C.impl : confPct >= 40 ? C.mit : C.nand
                                : 'transparent',
                              borderRadius: 3, transition: 'width 0.3s',
                            }} />
                          </div>
                          <span style={{ fontSize: 12, fontWeight: 600, color: C.text, minWidth: 36, textAlign: 'right' }}>
                            {confPct != null ? `${confPct}%` : '—'}
                          </span>
                        </div>
                      </div>

                      {/* Status */}
                      <div>
                        <div style={{ fontSize: 10, color: C.muted, marginBottom: 2, fontWeight: 500 }}>
                          Status
                        </div>
                        <span style={{
                          fontSize: 11, color: sn.status === 'draft' ? C.mit : C.impl,
                          fontStyle: sn.status === 'draft' ? 'italic' : 'normal',
                        }}>
                          {sn.status || 'active'}
                        </span>
                      </div>

                      {/* Calibrated */}
                      {sn.lastCalibratedAt && (
                        <div>
                          <div style={{ fontSize: 10, color: C.muted, marginBottom: 2, fontWeight: 500 }}>
                            Last Calibrated
                          </div>
                          <span style={{ fontSize: 11, color: C.text }}>
                            {new Date(sn.lastCalibratedAt).toLocaleString()}
                          </span>
                        </div>
                      )}

                      {/* ID */}
                      <div>
                        <div style={{ fontSize: 10, color: C.muted, marginBottom: 2, fontWeight: 500 }}>
                          ID
                        </div>
                        <code style={{
                          fontSize: 10, color: C.muted, background: C.surface,
                          padding: '2px 6px', borderRadius: 3, wordBreak: 'break-all',
                        }}>
                          {sn.id}
                        </code>
                      </div>

                      {/* Children */}
                      {selectedNodeChildren.length > 0 && (
                        <div>
                          <div style={{ fontSize: 10, color: C.muted, marginBottom: 4, fontWeight: 500 }}>
                            Children ({selectedNodeChildren.length})
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                            {selectedNodeChildren.map((child) => {
                              const childColor = kindColors[child.objectKind] || C.muted;
                              return (
                                <div
                                  key={child.id}
                                  onClick={() => setSelectedNode(child)}
                                  style={{
                                    display: 'flex', alignItems: 'center', gap: 6,
                                    padding: '4px 8px', background: C.surface,
                                    borderRadius: 4, cursor: 'pointer',
                                    fontSize: 11, color: C.text,
                                  }}
                                  title={child.content || child.name}
                                >
                                  <span style={{
                                    width: 6, height: 6, borderRadius: '50%',
                                    background: childColor, flexShrink: 0,
                                  }} />
                                  <span style={{
                                    flex: 1, overflow: 'hidden',
                                    textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                  }}>
                                    {child.name || '(unnamed)'}
                                  </span>
                                  <span style={{ fontSize: 9, color: childColor, fontWeight: 600 }}>
                                    {kindLabels[child.objectKind] || child.objectKind}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Action buttons */}
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 'auto', paddingTop: 8, borderTop: `1px solid ${C.border}` }}>
                        <button
                          onClick={() => handleViewArguments(sn.id)}
                          style={{
                            width: '100%', background: C.accent, border: 'none',
                            color: '#fff', borderRadius: 6, padding: '8px 12px',
                            cursor: 'pointer', fontSize: 12, fontWeight: 600,
                          }}
                        >
                          View Arguments
                        </button>
                        <button
                          onClick={() => handleEdit(sn)}
                          style={{
                            width: '100%', background: 'transparent',
                            border: `1px solid ${C.border}`, color: C.text,
                            borderRadius: 6, padding: '8px 12px',
                            cursor: 'pointer', fontSize: 12, fontWeight: 500,
                          }}
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => handleDelete(sn)}
                          style={{
                            width: '100%', background: 'transparent',
                            border: `1px solid ${C.nand}`, color: C.nand,
                            borderRadius: 6, padding: '8px 12px',
                            cursor: 'pointer', fontSize: 12, fontWeight: 500,
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        )}

        {!loading && !error && view === 'arguments' && argumentsNodeId && (
          <ArgumentView
            nodeId={argumentsNodeId}
            nodeName={selectedNode?.name || argumentsNodeId}
            onBack={handleBackToTree}
          />
        )}
      </div>

      {/* Footer */}
      <div style={{
        padding: '4px 14px', display: 'flex', gap: 16,
        borderTop: `1px solid ${C.border}`, background: C.panel,
        color: C.muted, fontSize: 10,
      }}>
        <span>Context: {context}</span>
        {selectedNode && (
          <span>Selected: {selectedNode.name?.slice(0, 50) || selectedNode.id}</span>
        )}
        <div style={{ flex: 1 }} />
        <span>Confidence scale: {['✦✦✦✦✦', '✦✦✦✦', '✦✦✦', '✦✦', '✦'].join(' > ')}</span>
      </div>

      {/* CRUD Dialogs */}
      {crud && (
        <OntologyCrud
          action={crud}
          context={context}
          onClose={handleCrudClose}
        />
      )}
    </div>
  );
}
