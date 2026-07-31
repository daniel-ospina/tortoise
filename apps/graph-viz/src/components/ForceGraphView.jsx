import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { forceCollide } from 'd3-force';
import removeOverlaps from 'remove-overlaps';
import { API, C, wrapLines } from '../constants';
import DetailPanel from './DetailPanel';

export default function ForceGraphView({ onViewArguments, initialFocusId }) {
  const [graph,setGraph]=useState({nodes:[],edges:[]});
  const [search,setSearch]=useState('');
  const [results,setResults]=useState([]);
  const [sel,setSel]=useState(null);
  const [rightClickPos,setRightClickPos]=useState(null);
  const [newPointText,setNewPointText]=useState('');
  const [mode,setMode]=useState(null);
  const [,setOverlayTick]=useState(0);
  const userNodes=useRef([]);
  const [edgeSrc,setEdgeSrc]=useState(null);
  const [content,setContent]=useState('');
  const [stats,setStats]=useState({});
  const [nandOnly,setNandOnly]=useState(false);
  const [showMit,setShowMit]=useState(true);
  const [showSrc,setShowSrc]=useState(false);
  const [showFilt,setShowFilt]=useState(false);
  const [showDet,setShowDet]=useState(true);
  const [showHelp,setShowHelp]=useState(false);
  const [showcase,setShowcase]=useState(false);
  const [sources,setSources]=useState([]);
  const [hiddenSrc,setHiddenSrc]=useState(new Set());
  const [srcActive,setSrcActive]=useState(false);
  const [cMin,setCMin]=useState(0);
  const [cMax,setCMax]=useState(100);
  const [eFilt,setEFilt]=useState('all');
  const [first,setFirst]=useState(true);
  const [nodeCount,setNodeCount]=useState(500);
  const [cooldown]=useState(300);
  const initialSettle=useRef(true);

  useEffect(()=>{
    if(!initialFocusId)return;
    fetch(`${API}/api/graph/neighborhood/${initialFocusId}?depth=1`).then(res=>res.json()).then(d=>{
      setGraph(d);
      const node=d.nodes.find(n=>n.id===initialFocusId);
      if(node)setSel(node);
    }).catch(()=>{});
  },[initialFocusId]);
  const [spacing,setSpacing]=useState(50);
  const fg=useRef();
  const spacingRef=useRef(50);

  const hover=useRef(null);
  const time=useRef(0);

  const deg=useMemo(()=>{const d={};for(const e of graph.edges){d[e.source]=(d[e.source]||0)+1;d[e.target]=(d[e.target]||0)+1;}return d;},[graph.edges]);

  useEffect(()=>{
    const ac = new AbortController();
    fetch(`${API}/api/graph?limit=${nodeCount}`,{signal:ac.signal}).then(r=>r.json()).then(d=>{
      setGraph(d);setStats({tn:d.total_nodes,te:d.total_edges,mit:d.total_mitigations});setFirst(false);
    }).catch(e=>{if(e.name!=='AbortError')console.error('graph fetch failed',e);});
    fetch(`${API}/api/sources`,{signal:ac.signal}).then(r=>r.json()).then(d=>setSources(d.sources||[])).catch(e=>{if(e.name!=='AbortError')console.error('sources fetch failed',e);});
    let id;const loop=(t)=>{time.current=t;id=requestAnimationFrame(loop);};id=requestAnimationFrame(loop);
    return ()=>{ac.abort();cancelAnimationFrame(id);};
  },[nodeCount]);

  const doSearch=useCallback(q=>{if(!q.trim()){setResults([]);return;}fetch(`${API}/api/search?q=${encodeURIComponent(q)}&limit=10`).then(r=>r.json()).then(d=>setResults(d.results)).catch(()=>{});},[]);

  const displayGraph=useMemo(()=>{
    let {nodes,edges}=graph;
    if(srcActive&&hiddenSrc.size){const ok=new Set();for(const n of nodes){let hide=false;for(const h of hiddenSrc){if((n.context||'').includes(h)){hide=true;break;}}if(!hide)ok.add(n.id);}nodes=nodes.filter(n=>ok.has(n.id));edges=edges.filter(e=>ok.has(e.source)&&ok.has(e.target));}
    const lo=cMin/100,hi=cMax/100;nodes=nodes.filter(n=>(n.confidence??.5)>=lo&&(n.confidence??.5)<=hi);
    const nids=new Set(nodes.map(n=>n.id));edges=edges.filter(e=>nids.has(e.source)&&nids.has(e.target));
    if(eFilt==='impl')edges=edges.filter(e=>e.type==='IMPL');
    if(eFilt==='nand')edges=edges.filter(e=>e.type==='NAND');
    if(nandOnly){edges=edges.filter(e=>e.type==='NAND');const conn=new Set();for(const e of edges){conn.add(e.source);conn.add(e.target);}nodes=nodes.filter(n=>conn.has(n.id));}
    return {nodes,links:edges};
  },[graph,srcActive,hiddenSrc,cMin,cMax,eFilt,nandOnly]);

  const pillDims=useMemo(()=>{
    const canvas=document.createElement('canvas');
    const ctx=canvas.getContext('2d');
    const sizes={};
    for(const n of displayGraph.nodes){
      const d=deg[n.id]||1;
      const conf=n.confidence??0.5;
      const importance=Math.log2(d+1)*(0.5+conf*0.5);
      const fs=8+importance*0.3;
      ctx.font=`${fs}px monospace`;
      const lines=wrapLines(ctx,n.content||'',50,15);
      const maxW=lines.length?Math.max(...lines.map(l=>ctx.measureText(l).width)):0;
      const pad=6;
      const pw=lines.length?maxW+pad*2:Math.max(10,4+importance*3);
      const ph=lines.length?lines.length*(fs+2)+pad*2:Math.max(10,4+importance*3);
      sizes[n.id]={pw,ph,fs};
    }
    return sizes;
  },[displayGraph.nodes,deg]);

  const communities=useMemo(()=>{
    const g=displayGraph;if(!g.nodes.length)return{};
    const adj={};for(const n of g.nodes)adj[n.id]=new Set();
    for(const e of g.links){if(adj[e.source])adj[e.source].add(e.target);if(adj[e.target])adj[e.target].add(e.source);}
    const comm={};let cid=0;for(const n of g.nodes)comm[n.id]=cid++;
    for(let iter=0;iter<10;iter++){for(const n of g.nodes){const counts={};for(const nb of adj[n.id]||[]){const nc=comm[nb];counts[nc]=(counts[nc]||0)+1;}let best=comm[n.id],bc=0;for(const[c,ct]of Object.entries(counts)){if(ct>bc){best=+c;bc=ct;}}comm[n.id]=best;}}
    const uniq=[...new Set(Object.values(comm))].sort();const map={};uniq.forEach((c,i)=>map[c]=i);
    const r={};for(const n of g.nodes)r[n.id]=map[comm[n.id]];return r;
  },[displayGraph]);
  const commCount=new Set(Object.values(communities)).size;

  const commCenters=useMemo(()=>{
    if(!commCount)return{};
    const centers={};
    const radius=Math.max(300,Math.sqrt(displayGraph.nodes.length)*20);
    const seen=new Set();
    for(const [,cid] of Object.entries(communities)){
      if(!seen.has(cid)){
        seen.add(cid);
        const angle=(cid/commCount)*2*Math.PI;
        centers[cid]={x:Math.cos(angle)*radius,y:Math.sin(angle)*radius};
      }
    }
    return centers;
  },[communities,commCount,displayGraph.nodes.length]);

  const commTerritory=useMemo(()=>{
    const t={};
    for(const [nid,cid] of Object.entries(communities)){
      if(!t[cid])t[cid]={count:0,maxPill:0};
      t[cid].count++;
      const dim=pillDims[nid];
      if(dim)t[cid].maxPill=Math.max(t[cid].maxPill,dim.pw,dim.ph);
    }
    for(const cid of Object.keys(t)){
      const cols=Math.ceil(Math.sqrt(t[cid].count));
      t[cid].radius=Math.max(150,cols*t[cid].maxPill/2+30);
    }
    return t;
  },[communities,pillDims]);

  const mergedGraph=useMemo(()=>displayGraph,[displayGraph]);

  useEffect(()=>{
    if(!displayGraph.nodes.length||!commCount)return;
    const byComm={};
    for(const n of displayGraph.nodes){
      const cid=communities[n.id]??0;
      if(!byComm[cid])byComm[cid]=[];
      byComm[cid].push(n);
    }
    const GOLDEN=2.39996;
    for(const [cid,nodes] of Object.entries(byComm)){
      const center=commCenters[cid];
      if(!center)continue;
      nodes.sort((a,b)=>((pillDims[b.id]?.pw||0)*(pillDims[b.id]?.ph||0))-((pillDims[a.id]?.pw||0)*(pillDims[a.id]?.ph||0)));
      const maxR=nodes.reduce((s,n)=>Math.max(s,Math.max(pillDims[n.id]?.pw||20,pillDims[n.id]?.ph||20)/2),0);
      const spacing=maxR*6+100;
      nodes.forEach((n,i)=>{
        if(i===0){n.x=center.x;n.y=center.y;}
        else{const r=spacing*Math.sqrt(i);const a=i*GOLDEN;n.x=center.x+r*Math.cos(a);n.y=center.y+r*Math.sin(a);}
        n.vx=0;n.vy=0;
      });
    }
  },[displayGraph.nodes,communities,commCenters,commCount,pillDims]);

  const scSet=useMemo(()=>{if(!showcase||!sel)return null;const s=new Set([sel.id]);for(const e of displayGraph.links){if(e.source===sel.id)s.add(e.target);if(e.target===sel.id)s.add(e.source);}return s;},[showcase,sel,displayGraph.links]);

  const paintNode=useCallback((node,ctx,gs)=>{
    const d=deg[node.id]||1;const conf=node.confidence??.5;
    const importance=Math.log2(d+1)*(0.5+conf*0.5);
    const isSel=node===sel;
    const isFaded=scSet&&!scSet.has(node.id);
    const alpha=isFaded?0.08:0.9;

    const fs=8+importance*0.3;ctx.font=`${fs}px monospace`;
    const lines=wrapLines(ctx,node.content||'',50,15);
    const showLabel=gs>0.12&&!isFaded&&lines.length>0;
    const maxW=showLabel?Math.max(...lines.map(l=>ctx.measureText(l).width)):0;
    const pad=6,pw=showLabel?maxW+pad*2:Math.max(10,4+importance*3),ph=showLabel?lines.length*(fs+2)+pad*2:Math.max(10,4+importance*3);

    const cid=communities[node.id]??0;const hue=(cid/Math.max(commCount,1))*360;
    const light=22+importance*5;
    const flash=node._epFlash||0;
    const fill=isFaded?'#1a1a2e':flash>0?`hsl(${hue},70%,${light+flash*30}%)`:`hsl(${hue},55%,${light}%)`;

    ctx.globalAlpha=alpha;
    const rx=node.x-pw/2,ry=node.y-ph/2,cr=5;
    ctx.beginPath();ctx.moveTo(rx+cr,ry);ctx.lineTo(rx+pw-cr,ry);ctx.arcTo(rx+pw,ry,rx+pw,ry+cr,cr);ctx.lineTo(rx+pw,ry+ph-cr);ctx.arcTo(rx+pw,ry+ph,rx+pw-cr,ry+ph,cr);ctx.lineTo(rx+cr,ry+ph);ctx.arcTo(rx,ry+ph,rx,ry+ph-cr,cr);ctx.lineTo(rx,ry+cr);ctx.arcTo(rx,ry,rx+cr,ry,cr);ctx.closePath();
    ctx.fillStyle=fill;ctx.fill();
    if(isSel){ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.stroke();}
    const confColor=conf>0.7?'#9ece6a':conf>0.4?'#e0af68':'#f7768e';
    ctx.strokeStyle=confColor;ctx.lineWidth=2;ctx.stroke();
    if(showLabel){
      ctx.fillStyle='#fff';ctx.textAlign='center';ctx.textBaseline='middle';const sy=node.y-(lines.length-1)*(fs+2)/2;lines.forEach((l,i)=>ctx.fillText(l,node.x,sy+i*(fs+2)));
      const pct=Math.round(conf*100);
      const badgeR=8;
      const bx=rx+pw-badgeR-2,by=ry+ph-badgeR-2;
      ctx.beginPath();ctx.arc(bx,by,badgeR,0,2*Math.PI);ctx.fillStyle=confColor;ctx.fill();
      ctx.fillStyle='#0a0e14';ctx.font=`bold ${badgeR-1}px monospace`;ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText(pct,bx,by);
    }
    ctx.globalAlpha=1;
  },[deg,sel,communities,commCount,scSet]);

  const paintEdge=useCallback((edge,ctx,gs)=>{
    const isNand=edge.type==='NAND';
    const edgeDeg=(deg[edge.source]||0)+(deg[edge.target]||0);
    const scale=gs||1;
    const lw=Math.max(0.5,(3+Math.min(edgeDeg/3,30))/scale);
    const hasMit=showMit&&edge.mitigations?.length>0;
    const mitS=hasMit?edge.mitigations[0].strength:1;
    const sx=edge.source.x,sy=edge.source.y,tx=edge.target.x,ty=edge.target.y;
    const dx=tx-sx,dy=ty-sy,len=Math.sqrt(dx*dx+dy*dy);if(!len)return;
    const ux=dx/len,uy=dy/len,mx=(sx+tx)/2,my=(sy+ty)/2;
    let alpha=isNand?0.7:0.5;
    if(isNand){const p=0.5+0.5*Math.sin(time.current*0.002+(edge.source.x||0)*0.01);alpha=0.5+p*0.3;}
    alpha*=mitS;
    const isFaded=scSet&&(!scSet.has(edge.source?.id||edge.source)||!scSet.has(edge.target?.id||edge.target));
    const ea=isFaded?0.02:alpha;
    ctx.strokeStyle=isNand?C.nand:C.impl;ctx.lineWidth=lw;ctx.globalAlpha=ea;
    if(hasMit){const g=10;ctx.beginPath();ctx.moveTo(sx,sy);ctx.lineTo(mx-ux*g,my-uy*g);ctx.stroke();ctx.setLineDash([4,3]);ctx.beginPath();ctx.moveTo(mx+ux*g,my+uy*g);ctx.lineTo(tx,ty);ctx.stroke();ctx.setLineDash([]);ctx.globalAlpha=0.8;ctx.fillStyle=C.mit;ctx.font='bold 13px monospace';ctx.textAlign='center';ctx.textBaseline='middle';ctx.save();ctx.translate(mx,my);ctx.rotate(Math.atan2(dy,dx));ctx.fillText('⊘',0,0);ctx.restore();}
    else{ctx.beginPath();ctx.moveTo(sx,sy);ctx.lineTo(tx,ty);ctx.stroke();}
    ctx.globalAlpha=1;
  },[deg,showMit,scSet]);

  useEffect(()=>{
    if(!displayGraph.nodes.length||!fg.current)return;
    const timers=[300,1500,4000,8000].map(ms=>setTimeout(()=>{
      if(initialSettle.current)fg.current?.zoomToFit?.(400,60);
    },ms));
    return ()=>timers.forEach(clearTimeout);
  },[displayGraph.nodes.length]);

  const fixOverlap=useCallback(()=>{
    if(!displayGraph.nodes.length)return;
    const rects=displayGraph.nodes.map(n=>{
      const rawLen=(n.content||'').length;
      const len=Math.min(rawLen,30);
      const lines=rawLen>22?2:1;
      return {x:n.x||0,y:n.y||0,width:Math.max(30,len*5+14),height:Math.max(15,lines*10+14),id:n.id};
    });
    removeOverlaps(rects,{method:'rectangle',maxIterations:20,maxMove:1.5});
    rects.forEach(r=>{const n=displayGraph.nodes.find(x=>x.id===r.id);if(n){n.x=r.x;n.y=r.y;n.fx=r.x;n.fy=r.y;}});
    fg.current?.zoomToFit?.(400,60);
  },[displayGraph.nodes]);

  const onNodeClick=useCallback(node=>{
    if(mode==='add-edge'){if(!edgeSrc)setEdgeSrc(node);else if(edgeSrc.id!==node.id){const t=confirm('IMPL (OK) or NAND (Cancel)?')?'IMPL':'NAND';fetch(`${API}/api/edges`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:edgeSrc.id,target:node.id,type:t})}).then(r=>r.json()).then(d=>setGraph(g=>({...g,edges:[...g.edges,{id:d.id,source:edgeSrc.id,target:node.id,type:t,__user:true}]}))).catch(()=>{});setMode(null);setEdgeSrc(null);}return;}
    setSel(node);setShowDet(true);
    fetch(`${API}/api/graph/neighborhood/${node.id}?depth=1`).then(res=>res.json()).then(d=>{setGraph(d);setSel(d.nodes.find(n=>n.id===node.id)||node);}).catch(()=>{});
  },[mode,edgeSrc]);

  const onNodeDragEnd=useCallback(node=>{
    node.fx=node.x; node.fy=node.y;
  },[]);

  const onBackgroundRightClick=useCallback((event,coords)=>{
    event.preventDefault();
    setRightClickPos({x:coords.x,y:coords.y,screenX:event.offsetX,screenY:event.offsetY});
    setNewPointText('');
  },[]);

  const doAddAtPosition=()=>{
    if(!newPointText.trim()||!rightClickPos)return;
    fetch(`${API}/api/points`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:newPointText})}).then(r=>r.json()).then(d=>{
      const newNode={id:d.id,content:newPointText,confidence:.3,x:rightClickPos.x,y:rightClickPos.y,fx:rightClickPos.x,fy:rightClickPos.y,__user:true,_screenX:rightClickPos.screenX,_screenY:rightClickPos.screenY};
      setGraph(g=>({...g,nodes:[...g.nodes,newNode]}));
      setNewPointText('');setRightClickPos(null);
    }).catch(()=>{});
  };

  const doAdd=()=>{if(!content.trim())return;fetch(`${API}/api/points`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content})}).then(r=>r.json()).then(d=>{setGraph(g=>({...g,nodes:[...g.nodes,{id:d.id,content,confidence:.3,__user:true}]}));setContent('');setMode(null);}).catch(()=>{});};
  const doDel=()=>{if(!sel)return;fetch(`${API}/api/points/${sel.id}`,{method:'DELETE'}).then(()=>{setGraph(g=>({...g,nodes:g.nodes.filter(n=>n.id!==sel.id),edges:g.edges.filter(e=>e.source!==sel.id&&e.target!==sel.id)}));setSel(null);}).catch(()=>{});};
  const doDelEdge=eid=>{fetch(`${API}/api/edges/${eid}`,{method:'DELETE'}).then(()=>setGraph(g=>({...g,edges:g.edges.filter(e=>e.id!==eid)})).catch(()=>{}));};
  const doReset=()=>{fetch(`${API}/api/graph?limit=${nodeCount}`).then(r=>r.json()).then(d=>{d.nodes.forEach(n=>{delete n.fx;delete n.fy;});setGraph(d);setSel(null);setHiddenSrc(new Set());setSrcActive(false);setCMin(0);setCMax(100);setEFilt('all');}).catch(()=>{});};
  const toggleSrc=name=>{setSrcActive(true);setHiddenSrc(p=>{const n=new Set(p);if(n.has(name))n.delete(name);else n.add(name);if(!n.size)setSrcActive(false);return n;});};

  const B={background:'#1a1f2e',border:'1px solid #1a2030',color:'#c0caf5',padding:'4px 12px',borderRadius:6,cursor:'pointer',fontSize:13,whiteSpace:'nowrap'};
  const BS={background:'transparent',border:'1px solid #1a2030',color:'#c0caf5',padding:'2px 8px',borderRadius:4,cursor:'pointer',fontSize:11};

  return (<>
    {/* Stats bar */}
    <div style={{padding:'8px 14px',display:'flex',gap:7,alignItems:'center',borderBottom:`1px solid ${C.border}`,background:C.panel,zIndex:20}}>
      <span style={{color:C.text,fontWeight:800,fontSize:18}}>◈ Tortoise</span>
      <span style={{color:C.muted,fontSize:11}}>{stats.tn?.toLocaleString()} claims · {stats.te?.toLocaleString()} relations{stats.mit>0?` · ${stats.mit} mitigated`:''} · {displayGraph.nodes.length} shown{commCount>1?` · ${commCount} communities`:''}</span>
      <div style={{flex:1}}/>
      <input placeholder="Search..." value={search} onChange={e=>{setSearch(e.target.value);doSearch(e.target.value);}} onKeyDown={e=>{if(e.key==='Escape'){setSearch('');setResults([]);}}} style={{padding:'5px 10px',background:C.surface,border:`1px solid ${C.border}`,borderRadius:6,color:C.text,width:180,fontSize:13,outline:'none'}}/>
      <select value={nodeCount} onChange={e=>setNodeCount(+e.target.value)} style={{...B,appearance:'none',padding:'4px 8px'}}>{[100,500,1000,2000,5000].map(n=><option key={n} value={n}>{n} nodes</option>)}</select>
      <button onClick={()=>{const nodes=displayGraph.nodes,links=displayGraph.links;const conf={};for(const n of nodes)conf[n.id]=n.confidence??0.5;const changed=new Set();for(let iter=0;iter<10;iter++){for(const e of links){const s=conf[e.source]??0.5,t2=conf[e.target]??0.5;let nc=t2;if(e.type==='IMPL')nc=t2+(s-t2)*0.5;else if(e.type==='NAND')nc=Math.max(0.05,t2-(s-0.5)*0.4);if(Math.abs(nc-t2)>0.01)changed.add(e.target);conf[e.target]=nc;}}const dur=2000,t0=performance.now();const anim=(now)=>{const t=Math.min((now-t0)/dur,1);for(const n of nodes){n.confidence=conf[n.id];if(changed.has(n.id))n._epFlash=Math.sin(t*Math.PI)*0.5;}if(t<1)requestAnimationFrame(anim);else{for(const n of nodes)delete n._epFlash;}};requestAnimationFrame(anim);}} style={{...B,background:'#1a3050'}} title="Run EP propagation">EP ▶</button>
      <span style={{color:C.muted,fontSize:11}}>Spacing</span>
      <input type="range" min="0" max="100" value={spacing} onChange={e=>{const v=+e.target.value;setSpacing(v);spacingRef.current=v;const cf=fg.current?.d3Force?.('charge');if(cf)cf.strength(-100-v*10);fg.current?.d3ReheatSimulation?.();}} style={{width:80,accentColor:C.accent}}/>
      <button onClick={()=>setShowFilt(!showFilt)} style={{...B,background:showFilt?'#1a3050':B.background}}>Filter</button>
      <button onClick={()=>setNandOnly(!nandOnly)} style={{...B,background:nandOnly?C.nand:B.background,borderColor:nandOnly?C.nand:B.border}} title="Contradictions">⚡</button>
      <button onClick={()=>setShowMit(!showMit)} style={{...B,background:showMit?C.mit:B.background,borderColor:showMit?C.mit:B.border}} title="Mitigations">⊘</button>
      <button onClick={()=>setShowcase(!showcase)} style={{...B,background:showcase?'#3a2060':B.background,borderColor:showcase?'#7c3aed':B.border}} title="Showcase">◎</button>
      <button onClick={()=>setShowSrc(!showSrc)} style={{...B,background:showSrc?'#1a3050':B.background}}>Sources</button>
      <button onClick={()=>setMode('add-point')} style={B} title="Add claim">+</button>
      <button onClick={()=>{setMode('add-edge');setEdgeSrc(null);}} style={B} title="Add relation">↗</button>
      {sel&&<button onClick={doDel} style={{...B,color:C.nand}}>✕</button>}
      <button onClick={fixOverlap} style={B} title="Remove overlaps (like Kumu)">⬡</button>
      <button onClick={doReset} style={B}>↺</button>
      <button onClick={()=>setShowHelp(!showHelp)} style={{...B,fontWeight:700}}>?</button>
    </div>

    {/* Filters */}
    {showFilt&&(<div style={{position:'absolute',top:48,right:16,zIndex:30,background:C.panel,border:`1px solid ${C.border}`,borderRadius:10,padding:'12px 16px',color:C.text,fontSize:12,boxShadow:'0 8px 32px rgba(0,0,0,0.6)',width:260}}>
      <div style={{display:'flex',justifyContent:'space-between',marginBottom:10}}><span style={{fontWeight:700}}>Filters</span><button onClick={()=>setShowFilt(false)} style={BS}>✕</button></div>
      <div style={{marginBottom:10}}><div style={{color:C.muted,fontSize:11,marginBottom:4}}>EP Confidence: {cMin}%–{cMax}%</div><input type="range" min="0" max="100" value={cMin} onChange={e=>setCMin(+e.target.value)} style={{width:'100%',accentColor:C.accent}}/><input type="range" min="0" max="100" value={cMax} onChange={e=>setCMax(+e.target.value)} style={{width:'100%',accentColor:C.accent}}/></div>
      <div style={{marginBottom:8}}><div style={{color:C.muted,fontSize:11,marginBottom:4}}>Edge type</div><div style={{display:'flex',gap:6}}>{['all','impl','nand'].map(t=><button key={t} onClick={()=>setEFilt(t)} style={{...BS,background:eFilt===t?C.surface:C.panel,borderColor:eFilt===t?C.accent:C.border,color:eFilt===t?C.text:C.muted}}>{t==='all'?'All':t.toUpperCase()}</button>)}</div></div>
      <div style={{color:C.muted,fontSize:10}}>{displayGraph.nodes.length} nodes · {displayGraph.links.length} edges</div>
    </div>)}

    {/* Help */}
    {showHelp&&(<div style={{position:'absolute',top:48,left:'50%',transform:'translateX(-50%)',zIndex:40,background:C.panel,border:`1px solid ${C.border}`,borderRadius:10,padding:'14px 20px',color:C.text,fontSize:12,boxShadow:'0 8px 32px rgba(0,0,0,0.6)',minWidth:300}}>
      <div style={{display:'flex',justifyContent:'space-between',marginBottom:8}}><span style={{fontWeight:700,fontSize:14}}>Navigation</span><button onClick={()=>setShowHelp(false)} style={BS}>✕</button></div>
      <div style={{display:'grid',gridTemplateColumns:'auto 1fr',gap:'4px 14px',lineHeight:1.8}}>
        <span style={{color:C.muted}}>Drag node</span><span>Pin position — connected nodes follow</span>
        <span style={{color:C.muted}}>Click node</span><span>Select + detail panel</span>
        <span style={{color:C.muted}}>Scroll</span><span>Zoom in/out</span>
        <span style={{color:C.muted}}>Shift+drag</span><span>Pan canvas</span>
        <span style={{color:C.muted}}>⬡</span><span>Remove overlaps (Kumu-style cleanup)</span>
        <span style={{color:C.muted}}>Esc</span><span>Clear search</span>
      </div>
    </div>)}

    {/* Search results */}
    {results.length>0&&(<div style={{position:'absolute',top:48,right:16,zIndex:30,background:C.panel,border:`1px solid ${C.border}`,borderRadius:8,maxHeight:340,overflow:'auto',width:380,boxShadow:'0 12px 40px rgba(0,0,0,0.6)'}}>{results.map(r=><div key={r.id} onClick={()=>{fetch(`${API}/api/graph/neighborhood/${r.id}?depth=1`).then(res=>res.json()).then(d=>{setGraph(d);setSel(d.nodes.find(n=>n.id===r.id)||null);}).catch(()=>{});setResults([]);setSearch('');}} style={{padding:'8px 12px',cursor:'pointer',color:C.text,fontSize:12,borderBottom:`1px solid ${C.border}`}} onMouseEnter={e=>e.target.style.background=C.surface} onMouseLeave={e=>e.target.style.background='transparent'}>{(r.content||'').slice(0,120)}</div>)}</div>)}

    {/* Edge mode */}
    {mode==='add-edge'&&<div style={{position:'absolute',top:48,left:'50%',transform:'translateX(-50%)',zIndex:30,background:C.panel,border:`1px solid ${C.accent}`,borderRadius:8,padding:'10px 18px',color:C.text,fontSize:13}}>{edgeSrc?`Source: "${(edgeSrc.content||'').slice(0,35)}" → click target`:'Click source node'}<button onClick={()=>{setMode(null);setEdgeSrc(null);}} style={{...B,marginLeft:12}}>Cancel</button></div>}

    {/* User-added nodes overlay */}
    {userNodes.current.length>0&&userNodes.current.filter(n=>!graph.nodes.some(gn=>gn.id===n.id)).map(n=>{
      let sx=n._screenX,sy=n._screenY;
      if((sx===undefined||sy===undefined)&&fg.current?.graph2ScreenCoords){
        try{
          const s=fg.current.graph2ScreenCoords(n.x,n.y);
          if(s&&isFinite(s.x)&&isFinite(s.y)){sx=s.x;sy=s.y;}
        }catch{/* graph2ScreenCoords may fail if graph not yet rendered */}
      }
      const conf=n.confidence??0.3;
      const confColor=conf>0.7?'#9ece6a':conf>0.4?'#e0af68':'#f7768e';
      return <div key={n.id} style={{position:'absolute',left:sx,top:sy,transform:'translate(-50%,-50%)',zIndex:25,pointerEvents:'auto',cursor:'pointer'}} onClick={()=>{
        if(mode==='add-edge'){
          if(!edgeSrc)setEdgeSrc(n);
          else if(edgeSrc.id!==n.id){
            const t=confirm('IMPL (OK) or NAND (Cancel)?')?'IMPL':'NAND';
            fetch(`${API}/api/edges`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:edgeSrc.id,target:n.id,type:t})}).then(r=>r.json()).then(d=>{
              setGraph(g=>({...g,edges:[...g.edges,{id:d.id,source:edgeSrc.id,target:n.id,type:t,__user:true}]}));
              setMode(null);setEdgeSrc(null);
            }).catch(()=>{});
          }
          return;
        }
        setSel(n);setShowDet(true);
      }}>
        <div style={{background:'#1a2e1a',border:`2px solid ${confColor}`,borderRadius:6,padding:'6px 10px',maxWidth:200,boxShadow:'0 4px 16px rgba(0,0,0,0.5)'}}>
          <div style={{color:'#fff',fontSize:12,fontFamily:'monospace',lineHeight:1.3}}>{(n.content||'').slice(0,80)}</div>
          <div style={{color:confColor,fontSize:10,fontWeight:700,marginTop:2}}>{Math.round(conf*100)}%</div>
        </div>
      </div>;
    })}

    {/* Right-click add point input */}
    {rightClickPos&&<div style={{position:'absolute',left:rightClickPos.screenX,top:rightClickPos.screenY,zIndex:50,background:C.panel,border:`1px solid ${C.accent}`,borderRadius:8,padding:'8px 12px',boxShadow:'0 8px 32px rgba(0,0,0,0.6)'}}>
      <input autoFocus placeholder="Type claim, Enter to add" value={newPointText} onChange={e=>setNewPointText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')doAddAtPosition();if(e.key==='Escape')setRightClickPos(null);}} style={{background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:4,padding:'4px 8px',fontSize:13,width:220,outline:'none'}}/>
    </div>}

    {/* Add modal */}
    {mode==='add-point'&&<div style={{position:'fixed',inset:0,zIndex:100,background:'rgba(0,0,0,0.7)',display:'flex',alignItems:'center',justifyContent:'center'}} onClick={()=>setMode(null)}><div style={{background:'#131821',border:'1px solid #1a2030',borderRadius:12,padding:24,width:420}} onClick={e=>e.stopPropagation()}><h3 style={{color:C.text,margin:'0 0 12px'}}>New Claim</h3><textarea value={content} onChange={e=>setContent(e.target.value)} placeholder="What's the claim?" autoFocus style={{width:'100%',minHeight:80,background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:8,padding:10,fontSize:14,resize:'vertical',outline:'none',fontFamily:'inherit'}}/><div style={{display:'flex',gap:8,marginTop:12,justifyContent:'flex-end'}}><button onClick={()=>setMode(null)} style={B}>Cancel</button><button onClick={doAdd} style={{...B,background:C.accent,color:'#fff'}}>Add</button></div></div></div>}

    {/* Detail panel */}
    <DetailPanel
      sel={sel}
      graph={graph}
      deg={deg}
      showDet={showDet}
      onClose={() => setShowDet(false)}
      onToggleShowDet={() => setShowDet(true)}
      onDelete={doDel}
      onDeleteEdge={doDelEdge}
      onDismiss={() => setSel(null)}
      onViewArguments={onViewArguments}
    />

    {/* Sources */}
    {showSrc&&<div style={{position:'absolute',top:48,left:20,zIndex:20,background:C.panel,border:`1px solid ${C.border}`,borderRadius:8,padding:'8px 0',maxHeight:360,overflow:'auto',width:200,fontSize:11,color:C.muted,boxShadow:'0 4px 16px rgba(0,0,0,0.4)'}}>
      <div style={{padding:'4px 12px',fontWeight:600,color:C.text,fontSize:12,display:'flex',justifyContent:'space-between'}}><span>Sources</span>{srcActive&&<button onClick={()=>{setHiddenSrc(new Set());setSrcActive(false);}} style={{...BS,fontSize:10}}>Clear</button>}</div>
      {sources.slice(0,20).map(s=><div key={s.name} onClick={()=>toggleSrc(s.name)} style={{padding:'3px 12px',cursor:'pointer',display:'flex',justifyContent:'space-between',alignItems:'center',opacity:hiddenSrc.has(s.name)?.35:1}}><span style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1}}>{s.name||'(none)'}</span><span style={{color:C.muted,marginLeft:6}}>{s.count}</span></div>)}</div>}

    {/* Legend */}
    <div style={{position:'absolute',bottom:20,right:20,zIndex:20,background:C.panel,border:`1px solid ${C.border}`,borderRadius:8,padding:'8px 14px',fontSize:11,color:C.text}}>
      <div style={{display:'flex',alignItems:'center',gap:6}}><span style={{width:18,height:3,background:C.impl,borderRadius:2,display:'inline-block'}}/> IMPL</div>
      <div style={{display:'flex',alignItems:'center',gap:6,marginTop:3}}><span style={{width:18,height:3,background:C.nand,borderRadius:2,display:'inline-block'}}/> NAND</div>
      {showMit&&<div style={{display:'flex',alignItems:'center',gap:6,marginTop:3}}><span style={{color:C.mit,fontWeight:700}}>⊘</span> Mitigation</div>}
    </div>

    {/* Welcome */}
    {first&&<div style={{position:'absolute',top:'50%',left:'50%',transform:'translate(-50%,-50%)',color:C.muted,textAlign:'center',zIndex:10,pointerEvents:'none'}}><div style={{fontSize:56,marginBottom:16,opacity:.6}}>◈</div><div style={{fontSize:18,color:C.text,marginBottom:8}}>Tortoise Knowledge Graph</div></div>}

    {/* Graph */}
    <div style={{flex:1,position:'relative'}}>
      <ForceGraph2D
        ref={fg}
        graphData={mergedGraph}
        nodeCanvasObject={paintNode}
        nodePointerAreaPaint={(node,color,ctx)=>{
          const s=pillDims[node.id];
          const pw=s?.pw||20,ph=s?.ph||20;
          ctx.fillStyle=color;
          ctx.fillRect(node.x-pw/2,node.y-ph/2,pw,ph);
        }}
        linkCanvasObject={paintEdge}
        onNodeClick={onNodeClick}
        onBackgroundRightClick={onBackgroundRightClick}
        onNodeDragEnd={onNodeDragEnd}
        onNodeHover={n=>{hover.current=n;}}
        backgroundColor={C.bg}
        linkDirectionalArrowLength={0}
        cooldownTicks={cooldown}
        d3AlphaDecay={0.005}
        d3VelocityDecay={0.3}
        minZoom={0.05}
        enableNodeDrag={true}
        d3Force={engine => {
          engine.force('charge').strength(-100 - spacingRef.current * 10);
          engine.force('link')
            .distance(150)
            .strength(e => {
              const sid = e.source?.id || e.source;
              const tid = e.target?.id || e.target;
              return communities[sid] === communities[tid] ? 0 : 0.06;
            });
          engine.force('center', null);
          engine.force('collision', forceCollide()
            .radius(d => {
              const s = pillDims[d.id];
              return s ? Math.max(s.pw, s.ph) / 2 : 40;
            })
            .strength(4.0).iterations(25)
          );
          engine.force('community-center', (() => {
            let nodes;
            const force = () => {
              for (const n of nodes) {
                const cid = communities[n.id];
                const center = commCenters[cid];
                if (!center) continue;
                const territory = commTerritory[cid]?.radius || 250;
                const dx = center.x - n.x, dy = center.y - n.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist > territory) {
                  const f = 0.005 * (dist - territory) / dist;
                  n.vx += dx * f;
                  n.vy += dy * f;
                }
              }
            };
            force.initialize = ns => { nodes = ns; };
            return force;
          })());
        }}
        onEngineStop={() => {
          if (!initialSettle.current) return;
          initialSettle.current = false;
          const nodes = displayGraph.nodes;
          if (!nodes.length) { fg.current?.zoomToFit?.(400, 60); return; }
          const rects = nodes.map(n => {
            const s = pillDims[n.id];
            return { x: n.x || 0, y: n.y || 0, width: s?.pw || 90, height: s?.ph || 40, id: n.id };
          });
          removeOverlaps(rects, { method: 'rectangle', maxIterations: 20, maxMove: 1.5 });
          const startPos = nodes.map(n => ({ id: n.id, x: n.x, y: n.y }));
          const startTime = performance.now();
          const animate = (now) => {
            const t = Math.min((now - startTime) / 500, 1);
            const ease = t * (2 - t);
            for (const r of rects) {
              const s = startPos.find(p => p.id === r.id);
              const n = nodes.find(x => x.id === r.id);
              if (s && n) { n.x = s.x + (r.x - s.x) * ease; n.y = s.y + (r.y - s.y) * ease; }
            }
            if (t < 1) requestAnimationFrame(animate);
            else fg.current?.zoomToFit?.(400, 60);
          };
          requestAnimationFrame(animate);
        }}
      />
    </div>
  </>);
}
