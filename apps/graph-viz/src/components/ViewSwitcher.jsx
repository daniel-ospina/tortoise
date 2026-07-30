import { useEffect, useState } from 'react';
import { API } from '../constants';

export default function ViewSwitcher({ view, onViewChange }) {
  const [ontologyReady, setOntologyReady] = useState(false);

  // Feature flag: enable ontology tab when backend signals ready
  useEffect(() => {
    // Manual override via ?view=ontology query param
    if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('view')) {
      setOntologyReady(true);
      return;
    }
    // Poll /api/health for ontology_status
    let cancelled = false;
    const check = () => {
      fetch(`${API}/api/health`)
        .then(r => r.json())
        .then(d => {
          if (!cancelled && d?.ontology_status === 'ok') {
            setOntologyReady(true);
          }
        })
        .catch(() => {}); // Backend not ready yet — keep polling
    };
    check(); // Immediate check
    const interval = setInterval(check, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const tabStyle = (active) => ({
    padding:'8px 20px',
    background: active ? '#1a3050' : 'transparent',
    border:'none',
    borderBottom: active ? '2px solid #7aa2f7' : '2px solid transparent',
    color: active ? '#c0caf5' : '#565f89',
    cursor:'pointer', fontSize:14,
    fontWeight: active ? 600 : 400,
    fontFamily:'-apple-system,sans-serif',
  });

  return (
    <div style={{
      display:'flex',gap:2,padding:'4px 14px',
      background:'#131821',borderBottom:'1px solid #1a2030'
    }}>
      <button onClick={() => onViewChange('force-graph')} style={tabStyle(view==='force-graph')}>
        Force Graph
      </button>
      {ontologyReady && (
        <button onClick={() => onViewChange('ontology')} style={tabStyle(view==='ontology')}>
          Ontology
        </button>
      )}
    </div>
  );
}
