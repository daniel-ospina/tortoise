import { useState, useEffect, useRef, useMemo } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { forceCollide } from 'd3-force';
import { C } from '../../constants';

interface Edge {
  edgeId: string;
  type: 'IMPL' | 'NAND';
  target: {
    id: string;
    content: string;
    confidence: number | null;
    sourceKind: string | null;
  };
  mitigations: Array<{
    id: string;
    reason: string;
    strength: number | null;
  }>;
}

interface ArgumentsData {
  node: {
    id: string;
    name: string;
    confidence: number | null;
    lastCalibratedAt: string | null;
  };
  supports: Edge[];
  contradicts: Edge[];
  total_edges: number;
}

interface GraphNode {
  id: string;
  content: string;
  confidence: number | null;
  isCenter: boolean;
}

interface GraphLink {
  source: string;
  target: string;
  type: 'IMPL' | 'NAND';
}

interface Props {
  nodeId: string;
  nodeName: string;
  onBack: () => void;
}

function confidenceTier(conf: number | null): number {
  if (conf === null || conf === undefined) return 2;
  if (conf < 0.2) return 0;
  if (conf < 0.4) return 1;
  if (conf < 0.6) return 2;
  if (conf < 0.8) return 3;
  return 4;
}

export default function ArgumentView({ nodeId, nodeName, onBack }: Props) {
  const [data, setData] = useState<ArgumentsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fgRef = useRef<any>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch(`/api/object-arguments?id=${encodeURIComponent(nodeId)}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((d: ArgumentsData) => {
        if (!cancelled) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message || 'Failed to load arguments');
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [nodeId]);

  const graphData = useMemo(() => {
    if (!data) return { nodes: [] as GraphNode[], links: [] as GraphLink[] };

    const nodes: GraphNode[] = [];
    const nodeIds = new Set<string>();
    const links: GraphLink[] = [];

    // Center node
    nodes.push({
      id: nodeId,
      content: nodeName,
      confidence: data.node.confidence,
      isCenter: true,
    });
    nodeIds.add(nodeId);

    const addEdges = (edges: Edge[], type: 'IMPL' | 'NAND') => {
      for (const edge of edges) {
        const targetId = edge.target.id;
        // Link center → evidence node
        links.push({
          source: edge.target.id,
          target: nodeId,
          type,
        });

        if (!nodeIds.has(targetId)) {
          nodes.push({
            id: targetId,
            content: edge.target.content?.slice(0, 80) || targetId,
            confidence: edge.target.confidence,
            isCenter: false,
          });
          nodeIds.add(targetId);
        }
      }
    };

    addEdges(data.supports, 'IMPL');
    addEdges(data.contradicts, 'NAND');

    return { nodes, links };
  }, [data, nodeId, nodeName]);

  const paintNode = (node: any, ctx: CanvasRenderingContext2D, gs: number) => {
    const { content, confidence, isCenter } = node as GraphNode;
    const conf = confidence ?? 0.5;
    const tier = confidenceTier(conf);
    const fontSize = 10 + tier * 1.5;
    const radius = isCenter ? 16 : 6 + tier * 2;

    ctx.font = `${fontSize}px monospace`;

    // Draw circle
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
    if (isCenter) {
      ctx.fillStyle = '#7aa2f7';
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
    } else {
      const confColor = conf > 0.7 ? '#9ece6a' : conf > 0.4 ? '#e0af68' : '#f7768e';
      ctx.fillStyle = confColor;
      ctx.strokeStyle = `${confColor}88`;
      ctx.lineWidth = 1;
    }
    ctx.fill();
    ctx.stroke();

    // Label
    if (gs > 0.15) {
      const label = content?.slice(0, 25) || '';
      ctx.fillStyle = '#c0caf5';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, node.x, node.y + radius + fontSize + 2);
    }
  };

  const paintEdge = (link: any, ctx: CanvasRenderingContext2D) => {
    const { type } = link as GraphLink;
    const color = type === 'IMPL' ? C.impl : C.nand;
    const lw = type === 'IMPL' ? 1.5 : 2.5;

    // Guard: skip rendering until source/target are positioned objects
    if (typeof link.source !== 'object' || typeof link.target !== 'object') return;

    ctx.strokeStyle = color;
    ctx.lineWidth = lw;
    ctx.globalAlpha = type === 'IMPL' ? 0.6 : 0.8;

    const sx = typeof link.source === 'object' ? link.source.x : 0;
    const sy = typeof link.source === 'object' ? link.source.y : 0;
    const tx = typeof link.target === 'object' ? link.target.x : 0;
    const ty = typeof link.target === 'object' ? link.target.y : 0;

    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(tx, ty);

    // NAND: dashed. IMPL: solid.
    if (type === 'NAND') {
      ctx.setLineDash([4, 2]);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.globalAlpha = 1;
  };

  if (loading) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', flexDirection: 'column', gap: 12,
      }}>
        <div style={{ color: C.muted, fontSize: 14 }}>Loading arguments...</div>
        <button onClick={onBack} style={{
          background: 'transparent', border: `1px solid ${C.border}`,
          color: C.muted, borderRadius: 6, padding: '4px 14px',
          cursor: 'pointer', fontSize: 12,
        }}>
          ← Back to Tree
        </button>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', flexDirection: 'column', gap: 12,
      }}>
        <div style={{ color: C.nand, fontSize: 14 }}>Error: {error}</div>
        <button onClick={onBack} style={{
          background: 'transparent', border: `1px solid ${C.border}`,
          color: C.muted, borderRadius: 6, padding: '4px 14px',
          cursor: 'pointer', fontSize: 12,
        }}>
          ← Back to Tree
        </button>
      </div>
    );
  }

  if (!data || (data.supports.length === 0 && data.contradicts.length === 0)) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', flexDirection: 'column', gap: 12,
      }}>
        <div style={{ color: C.muted, fontSize: 14 }}>
          No arguments for "{nodeName}"
        </div>
        <button onClick={onBack} style={{
          background: 'transparent', border: `1px solid ${C.border}`,
          color: C.muted, borderRadius: 6, padding: '4px 14px',
          cursor: 'pointer', fontSize: 12,
        }}>
          ← Back to Tree
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Top bar */}
      <div style={{
        padding: '6px 14px', display: 'flex', gap: 10, alignItems: 'center',
        borderBottom: `1px solid ${C.border}`, background: C.panel,
        fontSize: 12, color: C.text,
      }}>
        <button onClick={onBack} style={{
          background: 'transparent', border: `1px solid ${C.border}`,
          color: C.accent, borderRadius: 6, padding: '3px 12px',
          cursor: 'pointer', fontSize: 12,
        }}>
          ← Back to Tree
        </button>
        <span style={{ fontWeight: 600 }}>Arguments for:</span>
        <span style={{ color: C.muted }}>{(nodeName || '').slice(0, 40)}</span>
        <div style={{ flex: 1 }} />
        <span style={{ color: C.impl, fontSize: 11 }}>+{data.supports.length} support</span>
        <span style={{ color: C.nand, fontSize: 11 }}>-{data.contradicts.length} contradict</span>
        <span style={{ color: C.muted, fontSize: 10 }}>
          {data.total_edges} total (cap: 50)
        </span>
      </div>

      {/* Graph */}
      <div style={{ flex: 1 }}>
        <ForceGraph2D
          ref={fgRef}
          graphData={graphData}
          nodeCanvasObject={paintNode}
          linkCanvasObject={paintEdge}
          backgroundColor={C.bg}
          cooldownTicks={100}
          d3AlphaDecay={0.01}
          d3VelocityDecay={0.2}
          linkDirectionalArrowLength={0}
          enableNodeDrag={false}
          d3Force={(engine: any) => {
            engine.force('charge').strength(-200);
            engine.force('link').distance(80);
            engine.force('center', null);
            engine.force('collision', forceCollide().radius(30).strength(2));
          }}
          onEngineStop={() => {
            fgRef.current?.zoomToFit?.(400, 50);
          }}
        />
      </div>

      {/* Legend */}
      <div style={{
        position: 'absolute', bottom: 40, right: 20, zIndex: 10,
        background: C.panel, border: `1px solid ${C.border}`,
        borderRadius: 6, padding: '6px 12px', fontSize: 10, color: C.text,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 14, height: 3, background: C.impl, borderRadius: 2 }} />
          IMPL (supports)
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 3 }}>
          <span style={{ width: 14, height: 3, background: C.nand, borderRadius: 2 }} />
          NAND (contradicts)
        </div>
      </div>
    </div>
  );
}
