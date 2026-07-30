import { useState, useEffect } from 'react';
import { C } from '../../constants';
import { useOntologyTypes, type OntologyType } from '../../hooks/useOntologyTypes';

export interface CrudAction {
  type: 'create' | 'edit' | 'delete';
  node: any | null;
}

interface Props {
  action: CrudAction;
  context: string;
  onClose: (refresh?: boolean) => void;
}

// ─────────────────── CreateDialog ──────────────────

function CreateDialog({ context, onClose, ontologyTypes }: { context: string; onClose: (refresh?: boolean) => void; ontologyTypes: OntologyType[] }) {
  const [name, setName] = useState('');
  const [objectKind, setObjectKind] = useState(ontologyTypes[0]?.objectKind || '');
  const [content, setContent] = useState('');
  const [parentId, setParentId] = useState('');
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
      const body: any = {
        name: name.trim(),
        objectKind,
        context,
        content: content.trim() || name.trim(),
      };
      if (parentId.trim()) body.parentId = parentId.trim();

      const res = await fetch('/api/ontology-object', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail?.error || detail?.error || `HTTP ${res.status}`);
      }
      onClose(true);
    } catch (err: any) {
      setError(err.message || 'Create failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <DialogBase title="New Ontology Object" onClose={() => onClose()}>
      <Field label="Name *">
        <input
          autoFocus
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="e.g. Time-poor professionals"
          onKeyDown={e => { if (e.key === 'Enter') handleSubmit(); }}
          style={inputStyle}
        />
      </Field>
      <Field label="Object Kind *">
        <select
          value={objectKind}
          onChange={e => setObjectKind(e.target.value)}
          style={inputStyle}
        >
          {ontologyTypes.map(k => (
            <option key={k.objectKind} value={k.objectKind}>{k.label}</option>
          ))}
        </select>
      </Field>
      <Field label="Content">
        <textarea
          value={content}
          onChange={e => setContent(e.target.value)}
          placeholder="Description (defaults to name if empty)"
          rows={3}
          style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }}
        />
      </Field>
      <Field label="Parent ID (optional)">
        <input
          value={parentId}
          onChange={e => setParentId(e.target.value)}
          placeholder="UUID of parent node"
          style={inputStyle}
        />
      </Field>
      {error && <div style={{ color: C.nand, fontSize: 12, marginTop: 8 }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
        <button onClick={() => onClose()} style={btnStyle} disabled={submitting}>Cancel</button>
        <button onClick={handleSubmit} style={{ ...btnStyle, background: C.accent, color: '#fff', border: 'none' }} disabled={submitting}>
          {submitting ? 'Creating...' : 'Create'}
        </button>
      </div>
    </DialogBase>
  );
}

// ─────────────────── EditDialog ───────────────────

function EditDialog({ node, onClose }: { node: any; onClose: (refresh?: boolean) => void }) {
  const [name, setName] = useState(node.name || '');
  const [content, setContent] = useState(node.content || '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const version = node.version || 1;

  const handleSubmit = async () => {
    if (!name.trim()) {
      setError('Name is required');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body: any = {};
      if (name.trim() !== (node.name || '')) body.name = name.trim();
      if (content.trim() !== (node.content || '')) body.content = content.trim();

      if (!body.name && !body.content) {
        setError('No changes detected');
        setSubmitting(false);
        return;
      }

      const res = await fetch(`/api/ontology-object/${encodeURIComponent(node.id)}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'If-Match': String(version),
        },
        body: JSON.stringify(body),
      });

      if (res.status === 409) {
        const detail = await res.json().catch(() => ({}));
        const currentVersion = detail?.detail?.current_version || detail?.current_version || 'unknown';
        throw new Error(`Version conflict: object was modified by another agent (current v${currentVersion}, your v${version}). Please refresh and retry.`);
      }

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail?.error || detail?.error || `HTTP ${res.status}`);
      }
      onClose(true);
    } catch (err: any) {
      setError(err.message || 'Update failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <DialogBase title={`Edit "${(node.name || '').slice(0, 40)}"`} onClose={() => onClose()}>
      <div style={{ color: C.muted, fontSize: 11, marginBottom: 10 }}>
        Version: {version} · If-Match required
      </div>
      <Field label="Name *">
        <input
          autoFocus
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleSubmit(); }}
          style={inputStyle}
        />
      </Field>
      <Field label="Content">
        <textarea
          value={content}
          onChange={e => setContent(e.target.value)}
          rows={4}
          style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }}
        />
      </Field>
      {error && <div style={{ color: C.nand, fontSize: 12, marginTop: 8, whiteSpace: 'pre-wrap' }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
        <button onClick={() => onClose()} style={btnStyle} disabled={submitting}>Cancel</button>
        <button onClick={handleSubmit} style={{ ...btnStyle, background: C.accent, color: '#fff', border: 'none' }} disabled={submitting}>
          {submitting ? 'Saving...' : 'Save'}
        </button>
      </div>
    </DialogBase>
  );
}

// ─────────────────── DeleteDialog ─────────────────

interface Descendant {
  id: string;
  name: string;
  objectKind: string;
  depth: number;
}

function DeleteDialog({ node, onClose, kindColors, kindLabels }: { node: any; onClose: (refresh?: boolean) => void; kindColors: Record<string, string>; kindLabels: Record<string, string> }) {
  const [descendants, setDescendants] = useState<Descendant[] | null>(null);
  const [loadingDesc, setLoadingDesc] = useState(true);
  const [descError, setDescError] = useState<string | null>(null);
  const [force, setForce] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  const version = node.version || 1;

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/ontology-object/${encodeURIComponent(node.id)}/descendants`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((d: { descendants: Descendant[]; total_descendants: number }) => {
        if (!cancelled) {
          setDescendants(d.descendants);
          setLoadingDesc(false);
          if (d.total_descendants > 0) setForce(true);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setDescError(err.message);
          setLoadingDesc(false);
        }
      });
    return () => { cancelled = true; };
  }, [node.id]);

  const handleDelete = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (force && (descendants?.length || 0) > 0) params.set('force', 'true');

      const url = `/api/ontology-object/${encodeURIComponent(node.id)}${params.toString() ? '?' + params.toString() : ''}`;
      const res = await fetch(url, {
        method: 'DELETE',
        headers: { 'If-Match': String(version) },
      });

      if (res.status === 409) {
        const detail = await res.json().catch(() => ({}));
        const errMsg = detail?.detail?.error || detail?.error || '';
        if (errMsg.includes('children') || errMsg.includes('cascade')) {
          setForce(true);
          throw new Error(`Object has children. Set force=true to cascade delete ${descendants?.length || 0} descendants.`);
        }
        throw new Error(`Version conflict: ${errMsg || 'object was modified'}`);
      }

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail?.error || detail?.error || `HTTP ${res.status}`);
      }
      onClose(true);
    } catch (err: any) {
      setError(err.message || 'Delete failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <DialogBase title={`Delete "${(node.name || '').slice(0, 40)}"`} onClose={() => onClose()}>
      <div style={{ color: C.muted, fontSize: 11, marginBottom: 10 }}>
        Version: {version} · If-Match required
      </div>

      {/* Descendants preview */}
      <div style={{
        background: C.surface, borderRadius: 6, padding: 10,
        marginBottom: 12, maxHeight: 160, overflow: 'auto',
        fontSize: 12, color: C.text,
      }}>
        <div style={{ fontWeight: 600, marginBottom: 6, color: C.muted }}>
          Blast Radius
        </div>
        {loadingDesc && <div style={{ color: C.muted }}>Loading descendants...</div>}
        {descError && <div style={{ color: C.nand }}>Error: {descError}</div>}
        {descendants && descendants.length === 0 && (
          <div style={{ color: C.impl }}>No children — safe to delete</div>
        )}
        {descendants && descendants.length > 0 && (
          <>
            <div style={{ color: C.nand, marginBottom: 6 }}>
              ⚠ {descendants.length} descendant{descendants.length !== 1 ? 's' : ''} will be deleted:
            </div>
            {descendants.slice(0, 15).map(d => (
              <div key={d.id} style={{
                padding: '2px 0', fontSize: 11, color: C.muted,
                paddingLeft: d.depth * 12,
              }}>
                {d.depth > 0 && '↳ '}{d.name || d.id?.slice(0, 8)}
                <span style={{ color: kindColors[d.objectKind] || C.muted, marginLeft: 6, fontSize: 10 }}>
                  {kindLabels[d.objectKind] || d.objectKind}
                </span>
              </div>
            ))}
            {descendants.length > 15 && (
              <div style={{ color: C.muted, fontSize: 10, marginTop: 4 }}>
                ... and {descendants.length - 15} more
              </div>
            )}
          </>
        )}
      </div>

      {/* Force toggle */}
      {(descendants?.length || 0) > 0 && (
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: C.text, marginBottom: 12, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={force}
            onChange={e => setForce(e.target.checked)}
            style={{ accentColor: C.nand }}
          />
          <span style={{ color: C.nand }}>
            Force cascade delete ({descendants?.length || 0} descendants)
          </span>
        </label>
      )}

      {/* Confirmation */}
      {!confirmed ? (
        <div style={{ display: 'flex', gap: 8, marginTop: 8, justifyContent: 'flex-end' }}>
          <button onClick={() => onClose()} style={btnStyle}>Cancel</button>
          <button onClick={() => setConfirmed(true)} style={{ ...btnStyle, color: C.nand, borderColor: C.nand }}>
            I understand, proceed
          </button>
        </div>
      ) : (
        <>
          {error && <div style={{ color: C.nand, fontSize: 12, marginBottom: 8, whiteSpace: 'pre-wrap' }}>{error}</div>}
          <div style={{ color: C.nand, fontSize: 12, marginBottom: 10, fontWeight: 600 }}>
            Are you sure? This cannot be undone.
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button onClick={() => onClose()} style={btnStyle} disabled={submitting}>Cancel</button>
            <button onClick={handleDelete} style={{ ...btnStyle, background: C.nand, color: '#fff', border: 'none' }} disabled={submitting}>
              {submitting ? 'Deleting...' : `Delete${force ? ' All' : ''}`}
            </button>
          </div>
        </>
      )}
    </DialogBase>
  );
}

// ─────────────────── Shared Components ────────────

function DialogBase({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 200,
        background: 'rgba(0,0,0,0.7)', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: C.panel, border: `1px solid ${C.border}`,
          borderRadius: 12, padding: 24, width: 440, maxWidth: '90vw',
          boxShadow: '0 12px 48px rgba(0,0,0,0.7)',
        }}
      >
        <h3 style={{ color: C.text, margin: '0 0 14px', fontSize: 16, fontWeight: 600 }}>
          {title}
        </h3>
        {children}
      </div>
    </div>
  );
}

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

// ─────────────────── Export ───────────────────────

export default function OntologyCrud({ action, context, onClose }: Props) {
  const { types: ontologyTypes, labels: kindLabels, colors: kindColors, loading: typesLoading } = useOntologyTypes();

  if (action.type === 'create') {
    if (typesLoading) {
      return (
        <DialogBase title="New Ontology Object" onClose={() => onClose()}>
          <div style={{ color: C.muted, fontSize: 13, padding: '16px 0', textAlign: 'center' }}>
            Loading ontology types…
          </div>
        </DialogBase>
      );
    }
    return <CreateDialog context={context} onClose={onClose} ontologyTypes={ontologyTypes} />;
  }
  if (action.type === 'edit' && action.node) {
    return <EditDialog node={action.node} onClose={onClose} />;
  }
  if (action.type === 'delete' && action.node) {
    return <DeleteDialog node={action.node} onClose={onClose} kindColors={kindColors} kindLabels={kindLabels} />;
  }
  return null;
}
