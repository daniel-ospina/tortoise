---
title: "Epic #1891 — Decomposition: child issues + wiring"
type: decisions
domain: product
doc_status: draft
created: 2026-08-28
ownedBy: epistemic-team
---

# Epic #1891 — Decomposition (MECE-verified)

> Pipeline stage: Decompose · Skill: epic-decompose · MECE gate: clean after 1 fix loop (create_operator boundary contract, artifact-delivery ownership, soft edges)

## Child issues (dependency-ordered)

| # | Issue | Slice | Complexity | Depends on | Surfaces (#1898) | E2E |
|---|-------|-------|-----------|-----------|------------------|-----|
| 1929 | Pack catalog ships in wheel + both Docker images, CI smoke gate (bound derived from `len(DEFAULT_STARTER_PACKS)`), sample transcript package-data | 1 | standard | — | 1, 2, 4 | E2E-1 |
| 1930 | `TORTOISE_PACKS_DIR` env override + fail-safe resolution order | 1 | standard | #1929 | 3 | E2E-2 |
| 1931 | Authoring CLI (`pack new`/`pack validate`) + `_template` memory_granularity fix + install→mine→mint round-trip | 2 | standard | #1929 (+soft #1930) | 5, 6 | E2E-3 |
| 1932 | `docs/EXPANSION_PACKS.md` + ONTOLOGY §9 v3 refresh + quickstart refs + CI doc check + artifact-delivery assertion | 2 | standard | #1931 (+soft #1933) | 12 | E2E-3 (docs) |
| 1933 | Agent-ops rules-with-why starter pack + fixtures (active by default; starter set 4→5) | 1 | standard | #1929 | 11 | E2E-4 |
| 1934 | Enforcement seam (`resolve_enforcement`) → classifier retry + `create_operator` warn-not-block + violations event + battery baseline | 3 | complex | #1933 | 10 | E2E-5 |
| 1935 | Hosted per-tenant custom packs (`:PackManifest`, upload API, MCP tools, #1154, isolation) | 4 | complex | #1929 (merge contract w/ #1934 on create_operator boundary) | 5, 7, 8 | E2E-6 |
| 1936 | Export/import carries pack config (v1.1 additive, loud mismatch, reuses #1935 write path) | 4 | standard | #1935, #1930 | 8, 9 | E2E-7 |

## Dependency graph

```
1929 ──┬──► 1930 ──► 1936
       ├──► 1931 ──► 1932
       ├──► 1933 ──► 1934
       └──► 1935 ──► (merge contract: threads tenant view at #1934's seam boundary)
1936 ──► (reuses #1935's PackManifest write path)
```

Acyclic. 4-way parallel fan-out after #1929 (1930/1931/1933/1935), max depth 3.

## MECE gate findings (resolved)

| Type | Finding | Fix applied |
|------|---------|-------------|
| overlap | #1934/#1935 both touch `create_operator` (sdk.py) | Merge contract added to #1935: threads tenant view at #1934's seam call boundary; parallel-safe, no serialization |
| gap | E2E-3 docs-artifact-delivery assertion unowned | Owned by #1932 (coordinated with #1929 package-data) |
| soft edge | #1932↔#1933 worked example | #1932 body declares soft dependency (already stated) |
| soft edge | #1931→#1930 E2E-3 install leg | #1931 body notes `_PACKS_DIR` injection fallback until #1930 lands |
| overlap (low) | #1933/#1935 both edit pack_state.py | File-ownership note in #1935 (constant vs custom-source, additive) |
| info | #1936 must reuse #1935 write path | Reuse note added to #1936 |

## Capstone

- Capstone verification issue filed separately (epic-workflow Capstone Gate): clickthrough verification walking E2E-1…E2E-7 on the merged epic.
- Test-design issue: #1898 (integration-surface map — the canonical surface reference for every child issue).

## Wiring record

- Epic: #1891 (complex, epic, team:epistemic-team)
- Test-design: #1898 (standard)
- Children: #1929–#1936 (8 issues, complexity standard×6 + complex×2, team:epistemic-team)
- Related: #1154 (resolved by #1935), #557 (sub-tenancy — feeds), #318 (closed foundation), #1695 (completed classify-later — enforcement regression guard)
