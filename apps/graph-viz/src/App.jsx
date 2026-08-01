import { useState } from 'react';
import { C } from './constants';
import ViewSwitcher from './components/ViewSwitcher';
import ErrorBoundary from './components/ErrorBoundary';
import ForceGraphView from './components/ForceGraphView';

export default function App() {
  const [view, setView] = useState('force-graph');

  return (
    <div style={{
      width:'100vw',height:'100vh',background:C.bg,
      display:'flex',flexDirection:'column',
      fontFamily:'-apple-system,sans-serif'
    }}>
      <ViewSwitcher view={view} onViewChange={setView} />
      <ErrorBoundary name="Force Graph">
        {view === 'force-graph' && <ForceGraphView />}
      </ErrorBoundary>
      {view === 'ontology' && (
        <ErrorBoundary name="Ontology">
          <div style={{
            display:'flex',alignItems:'center',justifyContent:'center',
            height:'100%',color:C.muted,fontSize:16
          }}>
            Ontology View — Coming Soon
          </div>
        </ErrorBoundary>
      )}
    </div>
  );
}
