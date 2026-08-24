---
name: tortoise-file-finding
title: "tortoise-file-finding"
doc_status: live
subjects.team: epistemic-team
created: 2026-07-18
description: Ingest a research finding into the Tortoise graph. Creates a Point, checks for existing related claims, and surfaces connection candidates.
type: capability
domain: capability
status: live
allowed-tools: mcp__tortoise__tortoise_create_point, mcp__tortoise__tortoise_query
---

# tortoise:file-finding

Ingest a research finding into the epistemic graph.

## Steps

1. Accept finding content and source from the user or calling agent.
2. Call `tortoise_create_point(kind='statement', content=<finding>, extractedFrom=<source_url>)` to create the Point with provenance.
3. Call `tortoise_list_sources()` for discovery — identify what sources already exist in the graph.
4. Call `tortoise_query(kind='statement')` to find existing Points; post-filter by source to find Points from the same origin.
5. If existing Points found, surface them as potential connection candidates.
6. Offer to create IMPL/NAND connections or flag for review.

## Quality Gates

- **G1 (Static):** Content must be non-empty. Source must be a valid identifier (URL or name).
- **G2 (Semantic):** If the finding is near-identical to an existing Point, warn about potential duplicate.

## Error Handling

- If `tortoise_create_point` fails, report the error and do not proceed to query.
- If `tortoise_query` returns empty, report "no existing Points from this source" — this is a success state.
