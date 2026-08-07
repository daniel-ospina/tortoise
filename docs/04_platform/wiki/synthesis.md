---
title: "Engineering Wiki — Synthesis"
type: synthesis
domain: platform
doc_status: draft
subjects.team: organisation-design-team
created: 2026-07-29
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Engineering Wiki — Synthesis

## Core Thesis

El Dato's engineering platform provides the infrastructure for the epistemic graph (Tortoise), Supabase backend, and agent/SDK operations. The platform must be correct, performant, and debuggable — inference accuracy bugs are P0.

## Key Entities

- **Tortoise EP (Expectation Propagation):** Beta-distributed belief propagation engine for the epistemic graph. Uses natural parameter space messages with Gauss-Jacobi quadrature moment projection.
- **SVBP (Stein Variational Belief Propagation):** Particle-based alternative to EP using SVGD. Handles multi-modal distributions but at 10-50× EP's computational cost.
- **Operator weights:** Graph-derived edge weights for EP factors — source tier, time decay, mitigation status, edge density, context tags.

## Key Concepts

- **Directed vs Bidirectional IMPL:** IMPL edges (A supports B) are semantically directed. Standard EP treats all edges as undirected factor graph edges, sending back-messages that cause false cascades. Making IMPL directional in TortoiseEP fixes the convergent argument bug.
- **Convergent argument bug:** Multiple T0 sources supporting a claim produce LOWER confidence than fewer sources. Root causes: bidirectional IMPL back-messages + edge density penalty using `min()` across all outputs.

## Active Debates

- Should directional IMPL be the default at the cost of losing backward information through IMPL chains? (Resolved: yes — the information loss is semantically correct.)
- Is SVBP a viable refinement for Beta beliefs, or should it be reserved for multi-modal NAND camp detection? (Deferred — revisit when needed.)

## Recent Research

- 2026-08-07: #338 service-model research — Tortoise already runs as a hosted service (Fly.io, MCP Streamable HTTP at /mcp, 58 tools, tenant tt_ keys); gap is positioning (README/docs library-first) + license consistency (README=BSL vs LICENSE=AGPLv3 vs pyproject=MIT; DEC-002 ranked AGPLv3-dual 0.906 > BSL 0.8875). See `docs/research/2026-08-07-338-service-model.md`.
- 2026-07-29: EP convergent argument fix evaluation (see log.md INGEST entry)
