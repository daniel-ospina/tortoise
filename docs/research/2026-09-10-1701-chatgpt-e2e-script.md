---
title: "#1701 ChatGPT harness — human E2E script (recorded, indicator 2)"
type: engineering
subjects.team: epistemic-team
domain: platform
doc_status: live
created: 2026-09-10
---

# #1701: ChatGPT harness — human E2E script (recorded, indicator 2)

> Issue: #1701 · Branch: `feat/1701-oauth-routing` · Companion docs:
> `docs/research/2026-09-10-1701-state-audit.md` (indicator 2 was the single
> open server gap), `docs/scoping/2026-09-10-1701-remaining-slice-scoping.md`,
> `docs/plans/2026-09-10-1701-chatgpt-harness-remaining.md`.
> Status: **script for the R3 human run** — execute against the PR deploy and
> paste the filled evidence template into the PR body (non-blocking gate).

## Why this exists

The state audit proved the single-team ChatGPT-parallel flow works end-to-end
programmatically (discovery 200 → DCR 201 → PKCE consent/token 200 → `/mcp`
tools/list 200). What it could NOT prove — and what no unit test in this repo
can — is the ChatGPT **client-side** experience: OpenAI's Developer-mode app
surface, the in-webview OAuth consent page (CDN script, cookie jar, provider
redirect), plan-entitlement heuristics for write tools, and the multi-team
account chooser added by R1. This script closes that gap as a recorded human
run.

## Preconditions

- A ChatGPT account on a paid plan (Plus/Pro/Business/Enterprise/Education —
  **Developer mode** lives at Settings → Security and login).
- The account that signs in on the consent page belongs to the target Tortoise
  org. For the **multi-team chooser** leg, the account must hold ≥ 2 active
  teams; for the **single-team** leg it must hold exactly 1.
- The PR's dashboard deploy is reachable (`https://app.premiselabs.co`) and
  the hosted MCP endpoint answers at `https://api.premiselabs.co/mcp`
  (`GET /.well-known/oauth-protected-resource` → 200).

## Script

1. In the dashboard, open the onboarding wizard → **Connect your agent** →
   the **ChatGPT** tab (7th tab). Record what the tab shows: the
   Developer-mode steps, the org-naming line, and that **no API key** surface
   renders (no key-mint button, no paste box, no `tt_` text).
2. Copy the prompt (Copy prompt), then switch to chatgpt.com.
3. chatgpt.com → **Settings → Security and login → Developer mode** → enable.
4. chatgpt.com/plugins → **+** → create a **Developer-mode app**.
5. Enter the MCP server URL **exactly** as shown in the wizard tab
   (`https://api.premiselabs.co/mcp` — no trailing slash). Record the exact
   URL form used (the evidence template's connector-URL field).
6. Record the **auth choice** the connector UI offers (expected: OAuth / No
   Auth / Mixed — #1701's external re-verification says OAuth is listed and
   AS metadata discovery is automatic). Choose **OAuth**.
7. Click **Scan Tools**. ChatGPT opens the authorization flow (discovery +
   dynamic client registration + the hosted consent page in the webview).
8. On the consent page:
   - Record whether the session was **reused** (already signed in) or a fresh
     **sign-in** was required.
   - **Team selection must MATCH the org the wizard header names.** Single
     team: the consent page auto-binds that team and shows its name. Multi
     team: the team picker appears — record that it shows the team list
     **exactly once** (no duplicated options) and that **Authorize is
     disabled until a team is explicitly chosen**; pick the org named in the
     wizard's org-naming line. **If the chosen team ≠ the wizard org name,
     the E2E FAILS.**
   - Click Authorize. ChatGPT finishes the PKCE exchange (a redirect back to
     chatgpt.com is expected).
9. Record the **tool-scan result**: which `tortoise_*` tools appear in
   ChatGPT's tool list (Developer mode). Minimum: `tortoise_health` plus at
   least one read tool. Write tools appearing depends on the OpenAI plan tier
   (record it — external risk).
10. Paste the copied prompt into a **new ChatGPT chat** (the same chat the
    connector tools are attached to). Confirm ChatGPT acknowledges the
    workflows.
11. **Graph write round-trip:** ask a Tortoise question that must write
    (e.g. "file this finding as a point: <text>" — a tortoise-file-finding
    style instruction). ChatGPT's write action asks for confirmation in chat
    — confirm it. Then verify in the dashboard / SDK that the point landed in
    the **onboarding org's** graph (not a different team's).
12. Back in the dashboard ChatGPT tab: click **"I've connected it — Continue
    →"** — the harness-connected checkpoint saves and the done step renders.
13. Record any **redirects/errors** observed along the way and any **DCR
    errors under load** (shared-egress per-IP limiter — ops note).
14. **Refresh behaviour (~24 h later, optional leg):** with ChatGPT
    Developer-mode still attached, confirm a refresh-token rotation keeps the
    connection alive (the OAuth client refreshes silently) or records the
    re-consent prompt if OpenAI's client re-runs the flow.

## Evidence template (fill + paste into the PR body)

| # | Mandatory field | Value |
|---|---|---|
| 1 | ChatGPT plan tier + workspace type | |
| 2 | Connector URL form used (exact) | `https://api.premiselabs.co/mcp` |
| 3 | Auth choice shown in the connector UI | |
| 4 | Tool-scan result — which `tortoise_*` tools appeared | |
| 5 | Consent page: session reuse vs fresh sign-in | |
| 6 | Consent page: team selected in the picker vs org name shown in the wizard header — **mismatch fails the E2E** | |
| 7 | Consent picker showed the team list exactly once (no duplicate options) | |
| 8 | Graph write round-trip — prompt used + where the point landed (onboarding org?) | |
| 9 | Refresh behaviour after ~24 h | |
| 10 | Redirects / errors observed | |
| 11 | DCR errors under load (per-IP limiter) | |

## External risks (record, don't block on)

- **OpenAI plan entitlement** for write tools: the connector may surface only
  read tools on some plans (heuristic, not a server contract). The wizard's
  copy already frames confirmation-in-chat; a read-only scan result is
  recorded, not a failure — the server exposes the same tool set to any
  authenticated OAuth client.
- **Consent picker duplicates**: R1's JS rebuilds the `<select>` from scratch
  per preview run; a sequential re-run must never double-list teams (field 7
  pins this against the in-flight guard + clear-before-populate).
- **Consent-page JS beyond static strings**: disable-gate, retry, one-shot
  401 recovery, picker selection — the page's behaviors are pinned by pytest
  static-string assertions and Task 5's manual checklist; this run is the
  real-browser confirmation inside ChatGPT's webview.
