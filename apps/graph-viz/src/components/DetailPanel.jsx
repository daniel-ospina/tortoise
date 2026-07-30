import { useMemo } from 'react';
import { C } from '../constants';

const BS = {background:'transparent',border:'1px solid #1a2030',color:'#c0caf5',padding:'2px 8px',borderRadius:4,cursor:'pointer',fontSize:11};

export default function DetailPanel({ sel, graph, deg, onDelete, onDeleteEdge, onClose, onDismiss, showDet, onToggleShowDet }) {
  const ec = useMemo(() => {
    if (!sel) return { impl:0, nand:0 };
    const r = graph.edges.filter(e => e.source===sel.id||e.target===sel.id);
    return { impl:r.filter(e=>e.type==='IMPL').length, nand:r.filter(e=>e.type==='NAND').length };
  }, [sel, graph.edges]);

  if (!sel) return null;

  if (!showDet) {
    return (
      <div onClick={onToggleShowDet} style={{
        position:'absolute',bottom:20,left:20,zIndex:30,
        background:C.panel,border:`1px solid ${C.border}`,borderRadius:8,
        padding:'6px 12px',color:C.accent,fontSize:12,cursor:'pointer'
      }}>
        {(sel.content||'').slice(0,40)} · EP {(sel.confidence*100).toFixed(0)}% <span style={{color:C.muted}}>+</span>
      </div>
    );
  }

  return (
    <div style={{
      position:'absolute',bottom:20,left:20,zIndex:30,
      background:C.panel,border:`1px solid ${C.accent}`,borderRadius:12,
      padding:'12px 16px',maxWidth:440,color:C.text,fontSize:13,
      boxShadow:'0 4px 24px rgba(0,0,0,0.5)'
    }}>
      <div style={{display:'flex',justifyContent:'space-between'}}>
        <div style={{fontWeight:600,fontSize:14,lineHeight:1.3,flex:1}}>
          {(sel.content||'').slice(0,180)}
        </div>
        <button onClick={onClose} style={BS}>−</button>
      </div>
      <div style={{display:'flex',gap:10,marginTop:6,alignItems:'center',flexWrap:'wrap'}}>
        <span style={{color:C.muted,fontSize:11,maxWidth:180,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          {sel.context}
        </span>
        <span style={{color:C.accent,fontSize:11,fontWeight:600}}>
          {(sel.confidence*100).toFixed(0)}%
        </span>
        <div style={{width:60,height:6,background:C.surface,borderRadius:3,overflow:'hidden'}}>
          <div style={{width:`${sel.confidence*100}%`,height:'100%',background:C.accent,borderRadius:3}}/>
        </div>
      </div>
      <div style={{display:'flex',gap:12,marginTop:6}}>
        <span style={{color:C.impl,fontSize:11}}>+{ec.impl} supporting</span>
        <span style={{color:C.nand,fontSize:11}}>-{ec.nand} contradicting</span>
        <span style={{color:C.muted,fontSize:11}}>· {deg[sel.id]||0} edges</span>
      </div>
      <div style={{display:'flex',gap:8,marginTop:6}}>
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
