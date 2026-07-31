import { useState, useCallback } from 'react';
import { C } from './constants';
import ViewSwitcher from './components/ViewSwitcher';
import ErrorBoundary from './components/ErrorBoundary';
import ForceGraphView from './components/ForceGraphView';
import OntologyView from './components/ontology/OntologyView';

export default function App() {
  const [view, setView] = useState('force-graph');
  const [ontologyNodeId, setOntologyNodeId] = useState(null);

  const handleNavigateToOntologyNode = useCallback((id) => {
    setOntologyNodeId(id);
    setView('force-graph');
  }, []);

  const handleViewArguments = useCallback((id) => {
    setOntologyNodeId(id);
    setView('ontology');
  }, []);

  return (
    <div style={{
      width:'100vw',height:'100vh',background:C.bg,
      display:'flex',flexDirection:'column',
      fontFamily:'-apple-system,sans-serif'
    }}>
      <ViewSwitcher view={view} onViewChange={setView} />
      <ErrorBoundary name="Force Graph">
        {view === 'force-graph' && (
          <ForceGraphView onViewArguments={handleViewArguments} initialFocusId={ontologyNodeId} />
        )}
      </ErrorBoundary>
      {view === 'ontology' && (
        <ErrorBoundary name="Ontology">
          <OntologyView onNavigateToNode={handleNavigateToOntologyNode} initialFocusId={ontologyNodeId} />
        </ErrorBoundary>
      )}
    </div>
  );
}
