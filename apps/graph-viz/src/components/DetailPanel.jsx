import { useMemo, useState, useEffect } from 'react';
import { C, API } from '../constants';
import { useOntologyTypes } from '../hooks/useOntologyTypes';

const BS = {background:'transparent',border:'1px solid #1a2030',color:'#c0caf5',padding:'2px 8px',borderRadius:4,cursor:'pointer',fontSize:11};

export default function DetailPanel({ sel, graph, deg, onDelete, onDeleteEdge, onClose, onDismiss, showDet, onToggleShowDet, onViewArguments }) {
  const { colors: kindColors } = useOntologyTypes();
  const ec = useMemo(() => {
    if (!sel) return { impl:0, nand:0 };
    const r = graph.edges.filter(e => e.source===sel.id||e.target===sel.id);
    return { impl:r.filter(e=>e.type==='IMPL').length, nand:r.filter(e=>e.type==='NAND').length };
  }, [sel, graph.edges]);

  const [children, setChildren] = useState([]);
  const [loadingRel, setLoadingRel] = useState(false);

  useEffect(() => {
    if (!sel || !showDet || !(sel.objectKind || sel.pointKind)) return;
    setLoadingRel(true);
    let cancelled = false;
    fetch(`${API}/api/ontology-object/${sel.id}/descendants`)
      .then(r => r.json())
      .then(d => {
        if (!cancelled) { setChildren(d.descendants || []); setLoadingRel(false); }
      })
      .catch(() => { if (!cancelled) setLoadingRel(false); });
    return () => { cancelled = true; };
  }, [sel, showDet]);

  if (!sel) return null;

  const isObject = sel.objectKind || sel.pointKind;
  const objectKind = sel.objectKind || sel.pointKind;
  const kindColor = kindColors[objectKind] || C.muted;

  if (!showDet) {
    return (
      <div onClick={onToggleShowDet} style={{
        position:'absolute',bottom:20,left:20,zIndex:30,
        background:C.panel,border:`1px solid ${C.border}`,borderRadius:8,
        padding:'6px 12px',color:C.accent,fontSize:12,cursor:'pointer'
      }}>
        {(sel.content||'').slice(0,40)} · EP {sel.confidence != null ? (sel.confidence*100).toFixed(0) : '—'}% <span style={{color:C.muted}}>+</span>
      </div>
    );
  }

  return (
    <div style={{
      position:'absolute',bottom:20,left:20,zIndex:30,
      background:C.panel,border:`1px solid ${C.accent}`,borderRadius:12,
      padding:'12px 16px',maxWidth:460,color:C.text,fontSize:13,
      boxShadow:'0 4px 24px rgba(0,0,0,0.5)'
    }}>
      <div style={{display:'flex',justifyContent:'space-between'}}>
        <div style={{fontWeight:600,fontSize:14,lineHeight:1.3,flex:1}}>
          {(sel.content||'').slice(0,180)}
        </div>
        <button onClick={onClose} style={BS}>−</button>
      </div>

      {/* Object kind badge */}
      {isObject && (
        <div style={{marginTop:6, display:'flex', gap:8, alignItems:'center', flexWrap:'wrap'}}>
          {sel.objectKind && (
            <span style={{fontSize:10,fontWeight:600,color:kindColor,background:`${kindColor}22`,padding:'2px 8px',borderRadius:4}}>
              {sel.objectKind}
            </span>
          )}
          {sel.pointKind && sel.pointKind !== sel.objectKind && (
            <span style={{fontSize:10,color:C.muted,background:C.surface,padding:'2px 8px',borderRadius:4}}>
              point: {sel.pointKind}
            </span>
          )}
          {sel.status && (
            <span style={{fontSize:10,color:C.muted,fontStyle:'italic'}}>
              {sel.status}
            </span>
          )}
        </div>
      )}

      <div style={{display:'flex',gap:10,marginTop:6,alignItems:'center',flexWrap:'wrap'}}>
        <span style={{color:C.muted,fontSize:11,maxWidth:180,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          {sel.context}
        </span>
        <span style={{color:C.accent,fontSize:11,fontWeight:600}}>
          {sel.confidence != null ? (sel.confidence*100).toFixed(0) : '—'}%
        </span>
        <div style={{width:60,height:6,background:C.surface,borderRadius:3,overflow:'hidden'}}>
          <div style={{width:`${(sel.confidence ?? 0) * 100}%`,height:'100%',background:C.accent,borderRadius:3}}/>
        </div>
      </div>

      {/* Calibration timestamp */}
      {sel.lastCalibratedAt && (
        <div style={{marginTop:4,fontSize:10,color:C.muted}}>
          Calibrated: {new Date(sel.lastCalibratedAt).toLocaleString()}
        </div>
      )}

      <div style={{display:'flex',gap:12,marginTop:6}}>
        <span style={{color:C.impl,fontSize:11}}>+{ec.impl} supporting</span>
        <span style={{color:C.nand,fontSize:11}}>-{ec.nand} contradicting</span>
        <span style={{color:C.muted,fontSize:11}}>· {deg[sel.id]||0} edges</span>
      </div>

      {/* Parent / Children */}
      {isObject && (loadingRel || children.length > 0) && (
        <div style={{marginTop:6,fontSize:11}}>
          {loadingRel && <span style={{color:C.muted}}>Loading relations...</span>}
          {children.length > 0 && (
            <div>
              <span style={{color:C.muted,fontWeight:600}}>{children.length} child{children.length!==1?'ren':''}:</span>
              {children.slice(0,5).map(c => (
                <span key={c.id} style={{
                  display:'inline-block',margin:'2px 4px 2px 0',padding:'1px 6px',
                  background:C.surface,borderRadius:3,color:C.text,fontSize:10,
                }}>
                  {(c.name||c.id?.slice(0,8))} {c.objectKind && <span style={{color:kindColors[c.objectKind]||C.muted}}>[{c.objectKind}]</span>}
                </span>
              ))}
              {children.length > 5 && <span style={{color:C.muted}}>+{children.length-5} more</span>}
            </div>
          )}
        </div>
      )}

      <div style={{display:'flex',gap:8,marginTop:6,flexWrap:'wrap'}}>
        {isObject && onViewArguments && (
          <button onClick={() => onViewArguments(sel.id)} style={{...BS,color:C.accent,borderColor:C.accent}}>
            View Arguments
          </button>
        )}
        <button onClick={onDelete} style={{...BS,color:C.nand,borderColor:C.nand}}>Delete</button>
        <button onClick={onDismiss} style={BS}>Dismiss</button>
      </div>
      <div style={{marginTop:8,maxHeight:150,overflow:'auto'}}>
        {graph.edges.filter(e=>e.source===sel.id||e.target===sel.id).slice(0,10).map(e=>{
          const o = graph.nodes.find(n => n.id===e.target&&n.id!==sel.id)
                 || graph.nodes.find(n => n.id===e.source&&n.id!==sel.id);
          const hm = e.mitigations?.length>0;
          return (
            <div key={e.id} style={{fontSize:11,color:C.muted,padding:'2px 0',display:'flex',alignItems:'center',gap:6}}>
              <span style={{color:e.type==='NAND'?C.nand:C.impl,fontWeight:700,minWidth:32}}>{e.type}</span>
              <span style={{flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                {(o?.content||'').slice(0,50)}
              </span>
              {hm && <span style={{color:C.mit,fontSize:10}} title={e.mitigations[0].content}>
                ⊘{(e.mitigations[0].strength*100).toFixed(0)}%
              </span>}
              {e.__user && <button onClick={()=>onDeleteEdge(e.id)} style={{...BS,color:C.nand,fontSize:10}}>✕</button>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
