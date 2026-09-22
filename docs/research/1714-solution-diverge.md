---
title: "#1714 solution-diverge — six approaches"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-25
subjects.team: epistemic-team
aboutObjects: tortoise-memory-capture, tortoise-onboarding
---

# Solution-Diverge — Six approaches (2 agents × 3), three families

## Family 1 — Connector Resurrection (Agent 1 "Connector-Spine Resurrect" + Agent 2 "Connector Resurrection")

`tortoise/connectors/github.py` becomes the SINGLE GitHub producer. Add a REST fetch layer (extract the indexer's proven httpx fetch — rate-limit backoff/pagination — replacing gh-CLI subprocesses at connectors/github.py:129,150,175); add the net-new statement producer (`_issue_to_statements`: externalId-keyed statement Points, aboutObject→WorkItem, extractedFrom→Source); repoint hosted `_run_indexing` (hosted_api.py:8116-8155) at the connector via a new `TeamProjectionAdapter` (projection applies dicts via g.query — the biggest net-new unknown); retire the indexer as a producer (deletes the #1155-P2 two-producer eventKind divergence at the root). Quota preflight before first apply. Files: connectors/github.py, indexer/github_indexer.py (→ fetch helper, deleted as producer), hosted_api.py, projection/entities.py (net-new statement emission step), sdk.py:1973 (Session harness), __main__.py (tortoise sessions import), claude-hooks, AGENT_ONBOARDING.md, main.jsx + harnesses.js.
- Risks: connector production-readiness asserted not stress-tested; two write vocabularies coexist (projection dicts for entities, SDK for statements); gh-CLI removal breaks pipeline_cli users (keep as fallback).
- Best-fit: "reuse the connector/projection semantics wholesale" reading; want pipeline_cli/reconcile/event-log tooling to understand GitHub data free.
- Agent-2 variant adds: `_upsert_lifecycle` (closed/reopened Events + status projection), per-batch quota re-check, `updated_at` cursor poll-diff, docs walk via fetcher, capture-verification = server receipt primary (Session.harness + session_capture_receipt state key set only on hosted 2xx), ask = new dedicated wizard step "Memory capture".

## Family 2 — Unified Ingestion Module (Agent 1 "Unified Ingestion Module" + Agent 2 "Unified github_ingestion package")

New cohesive package owns ALL GitHub capture end-to-end: `tortoise/ingestion/` (github_sync.py, docs_sync.py, session_import/parsers, lifecycle) or `tortoise/github_ingestion/` (fetcher, diff, mapper, statements, quota, project, docs) — one pipeline contract `run(team, sources) → JobResult`. Both legacy paths retire to thin re-export stubs. Hosted jobs, self-hosted CLI, future connectors (Linear/Slack) all share the pipeline. MUST import projection/entities.py helpers (never reimplement #388 materialization). Diff state persisted (updated_at cursor). One vocabulary, one gating hook, one diff state — two-producer divergence structurally impossible.
- Files: NEW package; indexer + connector → stubs; hosted_api.py `_run_indexing` thin wrapper; sdk.py:1973; __main__.py (tortoise sessions import --harness X + tortoise index github-remote/docs); AGENT_ONBOARDING.md; main.jsx (Agent-1 variant: new dedicated wizard step; Agent-2 variant: expand step 1 in place into "Memory sources" three-toggle checklist).
- Capture verification: Agent-1 = hybrid (client install proof AND server receipt); Agent-2 = dual-ledger (local write-first log + server receipt; `tortoise sessions import` reconciles).
- Risks: LARGEST churn — retiring two pinned modules (test_github_connector.py eventIds, test_github_indexer.py FakeSDK); unified claim depends on project.py genuinely reusing semantics; recursive tree walk can burst rate limits (mitigation: backoff + incremental tree by sha).
- Best-fit: ingestion surface expected to grow (Linear/Slack parity); team can absorb a rewrite with ontology semantics locked down.

## Family 3 — Split-the-indexer (Agent 1 "Indexer Re-point + Prompt-First" + Agent 2 "Split-the-indexer with shared github_map")

Rework `github_indexer.py` IN PLACE into a two-phase pipeline (fetch+diff → project), and extract the ontology mapping into a NEW stateless shared module `tortoise/github_map.py` that BOTH the indexer and the orphaned connector import — single source of truth for issue_to_entities/issue_to_event/pr_to_event/issue_to_statements. Indexer stops writing kind="observation"; its output becomes event stream + statement specs applied through projection/entities.py (unchanged) + SDK (explicit-id create_point, aboutObject/extractedFrom; edits → bi-temporal supersede; true-while-open → invalidate_point). Two orchestration shells remain but share ONE mapper — divergence resolved at the mapping layer, no modules retired, all pinned tests survive.
- Files: NEW github_map.py; indexer (write-path rewrite + updated_at cursor + quota gate); connectors/github.py (mappers become thin wrappers over github_map); hosted_api.py (_run_indexing wires reworked indexer; connect-callback auto-index; Session harness :4062; new /v1/index/docs job); sdk.py:1973; __main__.py (tortoise sessions import --harness X + tortoise index docs); AGENT_ONBOARDING.md; main.jsx + harnesses.js.
- Agent-1 variant: ask is SELF-HOSTED-PROMPT-FIRST (wire AGENT_ONBOARDING.md Q3/Q5 to real mechanisms first; wizard syncs same jsonb state contract; minimal wizard changes: copy fix + auto-index + re-ask pane; T2 = `tortoise sessions watch` daemon/poll; capture-active = client-reported for T1/T2, honest disclosure for web).
- Agent-2 variant: ask = dashboard "Memory sources" panel POST-FIRST-VALUE (net-new dashboard surface, amend 15) as primary later-opt-in + ask home, linked from wizard; wizard changes minimal (copy fix + auto-index); capture verification = client-confirm primary for T1/T2 (write-first local receipt) + server-gated for T3 web only (disclosure until first hosted receipt); Session.harness for audit.
- Risks: two orchestration shells to keep in sync at orchestration level; indexer gains apply()-surface dependency on projection (adapter risk, narrow); #1155 divergence note must be retired deliberately (test_producers_share_event_id re-pinned to shared mapper).
- Best-fit: hosted control plane priority, smallest risk to pinned test surface, still get keyed/Events-as-truth/entity-linked/quota-fair — "rework the REST indexer in place" done as a real pipeline not a patch.
