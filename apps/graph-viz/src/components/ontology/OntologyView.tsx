import { useState, useEffect, useCallback } from 'react';
import { C } from '../../constants';
import TreeView from './TreeView';
import ArgumentView from './ArgumentView';
import OntologyCrud, { type CrudAction } from './OntologyCrud';

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
          <TreeView
            tree={tree}
            selectedId={selectedNode?.id || null}
            onSelect={handleSelect}
            onViewArguments={handleViewArguments}
            onEdit={handleEdit}
            onDelete={handleDelete}
            onNavigateToNode={navigateToNode}
          />
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
