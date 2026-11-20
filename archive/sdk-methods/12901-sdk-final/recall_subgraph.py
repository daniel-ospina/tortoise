# Archived sdk-method `recall_subgraph` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_recall_subgraph`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2).
# source:  tortoise/sdk.py @ 1231060fd2be9205b9b3bba1fd315e9b19b3602e
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'recall_subgraph'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def recall_subgraph(self, seed: str, *,
                        depth: int = 2,
                        completeness: str = "full",
                        max_nodes: int = 500) -> dict:
        """UC3 "subgraph" recall (epic #898 Wave B) — the COMPLETE connected
        subgraph for a seed/topic, completeness-optimized (high recall,
        precision secondary). Used before connecting a new document to the
        graph: deep understanding first.

        Args:
            seed: a node id (Point/Object/Subject/Event/Document id, Source
                url) OR a topic text (resolved via hybrid retrieval).
            depth: BFS expansion depth, 1-5 (default 2).
            completeness: "full" (default — every relationship type is an
                edge: about*, extractedFrom, hasPart, mitigated_by, ...) or
                "core" (epistemic core only: IMPL|NAND).
            max_nodes: node-count cap (10-5000, default 500) — bounded, not
                exhaustive-until-crash.

        Returns ``{nodes, edges, stats}``:
            nodes: [{id, type, content, kind, is_operator?, status?,
                confidence?}] — type is the lowercased graph label
                (point/object/subject/event/source). D10 (ONTOLOGY v3.15
                §4.4): a document is a `:Source`, so a document node's
                ``type`` is ``source``; its genre is the ``kind`` field
                (``documentKind``), not a separate label.
            edges: [{source, type, target}] — every edge with BOTH endpoints
                in the node set (the subgraph is closed over its edges).
            stats: {node_count, edge_count, depth, seed_count, truncated}.
        """
        from .ranking import SubgraphExpander

        # Validate bounds BEFORE seed resolution so an unresolvable seed
        # cannot silently skip validation (P2: depth=6 on a bogus seed must
        # error consistently, not depend on retrieval luck).
        if depth < 1 or depth > 5:
            raise ValueError(f"depth must be 1-5, got {depth}")
        if completeness not in ("core", "full"):
            raise ValueError(
                f"completeness must be 'core' or 'full', got {completeness!r}")
        if max_nodes < 10 or max_nodes > 5000:
            raise ValueError(f"max_nodes must be 10-5000, got {max_nodes}")

        seeds = self._resolve_subgraph_seed(seed)
        if not seeds:
            return {
                "nodes": [], "edges": [],
                "stats": {"node_count": 0, "edge_count": 0,
                           "depth": 0, "seed_count": 0,
                           "truncated": False},
            }
        expander = SubgraphExpander(self._get_proj())
        return expander.expand(seeds, depth=depth, completeness=completeness,
                               max_nodes=max_nodes)
