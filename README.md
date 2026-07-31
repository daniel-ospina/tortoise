---
title: "Premise Labs — AI Lab"
type: readme
domain:
status: seedling
tags: []
summary: ""
created: 2026-07-24
updated: 2026-07-30
---

# Premise Labs

An AI lab building the premises intelligence stands on.

**What's here:**
- Strategy docs, internal processes, research, and financial models
- The [premise-labs landing page](premise-labs/index.html) (deployed at [premiselabs.co](https://premiselabs.co))
- Tortoise knowledge graph engine lives in the [tortoise](https://github.com/daniel-ospina/tortoise) repo (public, BSL 1.1)

**What lives elsewhere:**
- **Tortoise SDK & MCP server** → [tortoise](https://github.com/daniel-ospina/tortoise) repo (Business Source License 1.1)
- **Canonical ontology** (`ONTOLOGY_v2.5.md`) → [eldato/docs/teams/](https://github.com/daniel-ospina/eldato/blob/main/docs/teams/organisation-design-team/domains%20(S1)/data/ONTOLOGY_v2.5.md)
- **Coordination infrastructure** → `eldato/operations/coordination/` (Organisation Design Team)
- **Agent infrastructure** → [agent-infra](https://github.com/daniel-ospina/agent-infra) — Pi extensions, skills, scripts

## Structure

```
premise-labs/
├── premise-labs/     → Landing page (single-scroll HTML, deploys to Cloudflare Pages)
├── docs/             → Internal strategy, research, legal docs
├── data/             → Data index and entity catalog
├── LICENSE           → Business Source License 1.1
├── AGENTS.md         → Agent instructions for this repo
└── CLAUDE.md         → Claude Code project instructions
```

## Quick Links

- [premiselabs.co](https://premiselabs.co) — Public landing page
- [Tortoise](https://github.com/daniel-ospina/tortoise) — Knowledge graph engine (public repo)
- [Agent Infrastructure](https://github.com/daniel-ospina/agent-infra) — Shared agent tooling
- [ONTOLOGY v2.5](https://github.com/daniel-ospina/eldato/blob/main/docs/teams/organisation-design-team/domains%20(S1)/data/ONTOLOGY_v2.5.md) — Canonical entity model

## License

Business Source License 1.1 — see [LICENSE](LICENSE)

## Related Repositories
- [tortoise](https://github.com/daniel-ospina/tortoise) — Knowledge graph engine (public, BSL 1.1)
- [agent-infra](https://github.com/daniel-ospina/agent-infra) — Shared agent infrastructure
- [eldato](https://github.com/daniel-ospina/eldato) — El Dato main app + canonical ontology
- [eldato-outreach](https://github.com/daniel-ospina/eldato-outreach) — B2B WhatsApp outreach
- [org-data](https://github.com/daniel-ospina/org-data) — Org data (Supabase → Tortoise)
