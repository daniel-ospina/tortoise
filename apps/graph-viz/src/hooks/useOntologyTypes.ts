import { useState, useEffect, useRef } from 'react';

export interface OntologyType {
  objectKind: string;
  label: string;
  color: string;
  description: string;
  icon: string | null;
  context: string;
}

interface OntologyTypesResponse {
  context: string;
  contexts: string[];
  types: OntologyType[];
  total: number;
}

interface UseOntologyTypesReturn {
  /** Full list of ontology types for the selected context */
  types: OntologyType[];
  /** Map of objectKind → label for quick lookups */
  labels: Record<string, string>;
  /** Map of objectKind → color for quick lookups */
  colors: Record<string, string>;
  /** Available contexts (expansion packs) */
  contexts: string[];
  /** Loading state — true during initial fetch */
  loading: boolean;
  /** Error message if fetch failed */
  error: string | null;
  /** Whether the data came from hardcoded fallback */
  isFallback: boolean;
}

/**
 * Hardcoded fallback — used when the API is unreachable.
 * Mirrors _CANONICAL_KINDS in server/main.py for graceful degradation.
 * NOTE: keep in sync with the server's core + product-strategy entries
 * (colors/labels/contexts) so fallback and live data look identical.
 */
const FALLBACK_TYPES: OntologyType[] = [
  { objectKind: 'customerSegment', label: 'Customer Segment', color: '#7aa2f7', description: '', icon: 'users', context: 'product-strategy' },
  { objectKind: 'jobToBeDone', label: 'Job to Be Done', color: '#9ece6a', description: '', icon: 'briefcase', context: 'product-strategy' },
  { objectKind: 'valueProposition', label: 'Value Proposition', color: '#bb9af7', description: '', icon: 'gem', context: 'product-strategy' },
  { objectKind: 'useCase', label: 'Use Case', color: '#e0af68', description: '', icon: 'play-circle', context: 'product-strategy' },
  { objectKind: 'feature', label: 'Feature', color: '#ff9e64', description: '', icon: 'puzzle-piece', context: 'product-strategy' },
  { objectKind: 'userJourney', label: 'User Journey', color: '#7dcfff', description: '', icon: 'map', context: 'product-strategy' },
  { objectKind: 'requirement', label: 'Requirement', color: '#f7768e', description: '', icon: 'clipboard-check', context: 'product-strategy' },
  { objectKind: 'statement', label: 'Statement', color: '#c0caf5', description: '', icon: 'file-text', context: 'core' },
  { objectKind: 'decision', label: 'Decision', color: '#7aa2f7', description: '', icon: 'check-square', context: 'core' },
];

function buildMaps(types: OntologyType[]): Pick<UseOntologyTypesReturn, 'labels' | 'colors'> {
  const labels: Record<string, string> = {};
  const colors: Record<string, string> = {};
  for (const t of types) {
    labels[t.objectKind] = t.label;
    colors[t.objectKind] = t.color;
  }
  return { labels, colors };
}

/**
 * Fetch ontology type definitions from the backend.
 *
 * @param context - Context/expansion pack slug (default: "all")
 * @returns Object with types array and derived labels/colors maps
 */
export function useOntologyTypes(context: string = 'all'): UseOntologyTypesReturn {
  const [types, setTypes] = useState<OntologyType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isFallback, setIsFallback] = useState(false);
  const [contexts, setContexts] = useState<string[]>([]);
  const lastFetchedContext = useRef<string | null>(null);

  useEffect(() => {
    if (lastFetchedContext.current === context) return;
    lastFetchedContext.current = context;

    let cancelled = false;

    const params = new URLSearchParams({ context });
    fetch(`/api/ontology-types?${params}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: OntologyTypesResponse) => {
        if (!cancelled) {
          setTypes(data.types);
          setContexts(data.contexts);
          setLoading(false);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          console.warn('[useOntologyTypes] API unavailable, using fallback:', err.message);
          setError(err.message);
          setTypes(FALLBACK_TYPES);
          setIsFallback(true);
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [context]);

  const { labels, colors } = buildMaps(types);

  return {
    types,
    labels,
    colors,
    contexts,
    loading,
    error,
    isFallback,
  };
}
