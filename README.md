# Tortoise

Epistemic knowledge graph engine. FalkorDB-backed.

**Capabilities:**
- Point extraction from transcripts/documents
- Operator reasoning (NAND, IMPL) with BFS shock propagation
- Provenance chains (Point → Subject → Team)
- Entity extraction (Subjects, Objects, aboutEntities)
- Multi-tenant via graph_name (10K+ isolated graphs)
- Connector system (GitHub, Slack, Linear, custom)

**Architecture:**
```
Source → Extractor → Event Log → Projection → FalkorDB Graph
```

**License:** AGPLv3 + CLA (Apache 2.0 re-license available)

**Setup:**
```bash
pip install -r requirements.txt
python -m tortoise rebuild  # Rebuild graph from events
```
