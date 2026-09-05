---
title: "ADR-010: Auth Planes — Human Sessions and Machine Credentials Are Separate"
type: decisions
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-session, tortoise-api-keys, tortoise-dashboard
created: 2026-09-04
---

# ADR-010: Auth Planes — Human Sessions and Machine Credentials Are Separate

**Status:** Accepted (2026-09-04 — product decision; see #2246 and #1701)
**Date:** 2026-09-04
**Issue:** #2246
**Owner:** epistemic-team

## Context

The dashboard historically treated an API key as a session-adjacent credential:
the browser stored a durable `tt_` key in localStorage (`tortoise_api_key`),
probed it on mount (`GET /v1/team` with the stored key — the "rule-1 exemption"),
adopted it as working state, and the API Keys table marked that row "in use by
this dashboard" with delete/toggle suppressed and a rotate-only action.

That model produced the couplings this ADR retires:

1. **Security** — durable keys in JS-readable localStorage are an XSS-exfiltration
   liability. Sessions (HttpOnly, short-lived) are the only thing a browser should hold.
2. **UX** — the held-key guard created confusing, non-uniform key rows (rotate-only
   with suppressed delete/toggle) and an inaccurate label: after #1828/#2167 moved
   every read to the session JWT, *nothing* in the dashboard uses the key. The label
   was really "the key your agents use," guarded because revoking it would break
   agents — not because the dashboard depends on it.
3. **Complexity** — probe/adopt/drop classification, `classifyHeldKey` /
   `heldKeyClearState` / `probeClassifyStoredKey` / `nextRegenInstallState`
   machinery, and `isActiveKey` guard semantics all exist to manage a credential
   the browser no longer needs.

## Decision

Two planes, two credential families, never mixed:

| Plane | Who | Credential | Created how | Revoked how |
|---|---|---|---|---|
| **Human (control)** | You, in a browser | Session JWT (Supabase, cookie) | OAuth login (GitHub/Google) | Logout |
| **Machine (data)** | Agents (MCP), CLI, scripts, CI | Per-agent identity (OAuth) **or** durable API key | Connect-step authorize / API Keys tab | Dashboard revoke |

1. **The browser never holds an API key.** The dashboard is a session client; it
   *manages* keys (create/rotate/revoke/rename/toggle) but never authenticates with one.
2. **Key login is a bootstrap only.** API-key login exists solely for account-less
   (anon) teams as the path to connecting an account — already enforced: claimed
   teams reject key login ("requires GitHub/Google sign-in; API keys remain valid
   for graph operations"), and `dashboard_key_login` can be disabled per team.
3. **Agent connect = authorize, not copy** (Tier 1, #1701). Remote-MCP OAuth
   (RFC 9728 discovery + auth-code/PKCE, already live on `/mcp` per docs/oauth-mcp.md)
   gives each agent its own identity. The connect step becomes an authorize action
   for OAuth-capable harnesses (Claude Desktop / Cursor / Codex).
4. **API keys remain the Tier-2 machine credential** for CLI/scripts/CI and
   non-OAuth agents — durable, scoped, uniform table actions, no special rows.
5. **REST `/v1/*` stays key/JWT-only** — `oat_` tokens are MCP-only (D3 from
   docs/oauth-mcp.md); the auth-planes split does not change server auth surfaces,
   only what the browser holds and how the connect step presents credentials.

### What this changes (browser-side only)

- Session mode: remove the mount stored-key probe + adopt/drop; drop held-key
  state; keys table uniform (rotate + toggle + trash on every durable row).
- Connect step: source the presented key from the keys table (durable ∧ ¬revoked
  ∧ ¬disabled rows) or route to create-one — no localStorage-held credential.
- Anon/key-login mode keeps the existing key-in-browser bootstrap (Protect screen,
  claim) — decouple is session-mode-only.
- Server: **no changes** (list stays unfiltered for CLI/selfhost consumers; the
  session lane already resolves team + role).

## Consequences

- **Good:** uniform, honest keys table; no XSS-key surface in the browser; the
  connect step stays smooth via rows-source resolution (no prefill regression);
  progress toward the #1701 endgame where agents own their credentials.
- **Tradeoffs accepted:** "shown once" fragility is inherent to durable keys
  (server keeps hash only) and remains for Tier-2; the anon bootstrap still holds
  a key in the browser by definition (no session exists until an account connects).
- **Non-goals:** no `oat_` on REST; no server key-authentication removal;
  selfhost/registry lanes unchanged.

**Implementation:** #2246 · **Endgame:** #1701 · **Baseline work:** #2166, #2167, #2229, #2230
