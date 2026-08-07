---
title: "Tortoise — Semantic + Epistemic + Episodic + Procedural Graph Engine"
type: readme
domain: epistemic
status: live
created: 2026-07-24
updated: 2026-08-06
---

# Tortoise

A graph engine for agent memory: claims are **Points**, relationships are **edges**, and belief scores are computed by propagating evidence through the graph (EP — Evidence Propagation).

A product of [Premise Labs](https://premiselabs.co).

## What's here

- `tortoise/` — the SDK, MCP server, projection, search engine, backup/restore
- `premise-labs/` — the hosted product's landing pages + dashboard (deploys to Cloudflare Pages)
- `docs/ONTOLOGY.md` — **canonical ontology v3.1** (co-located with the code it governs)
- `tests/` — test suite

## Canonical ontology

`docs/ONTOLOGY.md` is the single source of truth for the entity model (Point, Subject, Object, Event, Source), edge topology (IMPL/NAND/structural/about*), kind vocabularies, and EP semantics. It is **canonical** — product gaps are filed as issues, never added to the ontology as roadmap detail.

## Repo map & issue routing

File issues in the repo that owns the code:

| Repo | Owns | File issues for |
|---|---|---|
| **daniel-ospina/tortoise** (this repo) | Tortoise product: SDK, MCP, hosted API, graph engine, ontology | Tortoise product bugs, features, ontology gaps |
| **daniel-ospina/agent-infra** | Agent infrastructure: Pi extensions, skills, commit-workflow, CI gates, review-enforcer | Skill/pipeline/extension/CI work |
| **daniel-ospina/premise-labs** | Premise Labs internal ops: meetings recorder, CRM (Twenty), bridge scripts, health checks | Ops tooling, CRM, meeting pipeline |
| **daniel-ospina/eldato** | El Dato app (eldato.com.mx): scanner, webapp, deals/offers, notifications, ads, SEO | El Dato product work |

**Rule of thumb:** if the issue is about Tortoise code (this repo's `tortoise/` or `premise-labs/` dirs), file it here. If it's about agent tooling, file in agent-infra. If it's about Premise Labs ops (meetings/CRM), file in premise-labs.

## Quickstart

```bash
pip install -e .            # or: pip install 'tortoise[embeddings]' for vector search
# Hosted: sign up at tortoise.premiselabs.co, get an API key on the welcome page
# Self-hosted: see docs/infra-runbook.md
```

## License

Business Source License 1.1 — see [LICENSE](LICENSE)
