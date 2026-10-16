---
title: "Explanation — Understanding-Oriented Documentation"
type: index
domain: operations
doc_status: live
created: 2026-10-16
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Explanation (Understanding-Oriented)

> **Diátaxis Quadrant:** Explanation documentation clarifies architecture, design trade-offs, security models, durability guarantees, and the "why" behind system decisions.

New architectural explanations and design rationale documents should be added to `docs/explanation/`.

## Architecture, Security & Durability

| Document | Path | Summary |
|---|---|---|
| **Vision — MCP & SDK Surface** | [`docs/product/vision-mcp-sdk-surface.md`](../product/vision-mcp-sdk-surface.md) | Target surface philosophy, governing artifacts, and settled design tensions. |
| **Authentication Architecture** | [`docs/auth-architecture.md`](../auth-architecture.md) | Server-side BFF + HttpOnly `__Host-session` on `app.premiselabs.co` and key-permission boundaries. |
| **Authorization Architecture** | [`docs/authorization-architecture.md`](../authorization-architecture.md) | Principal / Scope / Capability model, provenance-derived scoping, and `authorize(principal, action, resource)`. |
| **D1 Session Store Lifecycle** | [`docs/engineering/auth/d1-session-store.md`](../engineering/auth/d1-session-store.md) | Session/flow schema, CAS predicates, 503-vs-401 failure semantics, and retention sweeps. |
| **Consent Identity Bridge** | [`docs/engineering/auth/consent-identity-bridge.md`](../engineering/auth/consent-identity-bridge.md) | Signed consent token contract (`bind` set, single-use `jti`, TTL) and threat mitigation. |
| **OAuth 2.1 for Remote MCP** | [`docs/oauth-mcp.md`](../oauth-mcp.md) | Discovery, PKCE flow, DCR capacity policy, and trusted-CIDR exemptions. |
| **Data Safety** | [`docs/data-safety.md`](../data-safety.md) | Encryption in transit and at rest across hosted and self-hosted deployments. |
| **Retention & Deletion Promise** | [`docs/retention-and-deletion.md`](../retention-and-deletion.md) | Canonical data retention windows, grace periods, and deletion guarantees. |
| **Durability Posture** | [`docs/durability-posture.md`](../durability-posture.md) | Durability mechanisms, loss windows, and verification evidence per deployment mode. |
| **Client / Server Package Split** | [`docs/client-server-split.md`](../client-server-split.md) | Rationale and boundary between the full engine and the thin `tortoise-client` driver. |
| **Pytest-Timeout Guard Integrity** | [`docs/infra/pytest-timeout-guard.md`](../infra/pytest-timeout-guard.md) | Why in-process `ITIMER_REAL` / `SIGALRM` calls disarm `pytest-timeout` and how the suite-wide guard prevents it. |

## Architecture Decision Records (ADRs) & Landscape Analysis

| Record | Path | Summary |
|---|---|---|
| **ADR-009: Embedder Selection** | [`docs/adr/ADR-009-embedder-selection.md`](../adr/ADR-009-embedder-selection.md) | Decision record for embedding model selection and hybrid retrieval. |
| **ADR-010: Auth Planes (Session vs. Agent Key)** | [`docs/adr/ADR-010-auth-planes-session-agent-key.md`](../adr/ADR-010-auth-planes-session-agent-key.md) | Separation between interactive browser sessions and machine API credentials. |
| **ADR-011: DR Alert Resolution Authority** | [`docs/adr/ADR-011-resolution-authority-for-dr-alerts.md`](../adr/ADR-011-resolution-authority-for-dr-alerts.md) | Evidence-gated resolution authority for disaster recovery alerts. |
| **ADR-012: Browser OAuth Grant (Implicit to PKCE)** | [`docs/adr/ADR-012-browser-oauth-grant-implicit-to-pkce.md`](../adr/ADR-012-browser-oauth-grant-implicit-to-pkce.md) | Retirement of the implicit grant in favor of Authorization Code + PKCE. |
| **Agent-Harness Landscape** | [`docs/research/2026-09-01-gbrain-learnings/platform-landscape.md`](../research/2026-09-01-gbrain-learnings/platform-landscape.md) | Volunteering-memory end-state distribution targets across coding agent harnesses. |
| **Competitor Analysis** | [`product/competition/_analysis.md`](../../product/competition/_analysis.md) | Deep-dive profiles and four-epistemic-primitives matrix across 14 competing systems. |
