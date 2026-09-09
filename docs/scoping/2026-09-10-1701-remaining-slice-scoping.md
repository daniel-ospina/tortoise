---
title: "#1701 Remaining-slice Scoping — ChatGPT harness (post-audit)"
type: engineering
subjects.team: epistemic-team
domain: platform
doc_status: live
created: 2026-09-10
---

<!-- research-path: docs/research/2026-09-10-1701-state-audit.md -->
<!-- issue-scoping: v5.1 double diamond (post-audit remaining slice) -->

# #1701 Remaining-slice Scoping — ChatGPT harness (post-audit)

**Issue:** #1701 (standalone; Level: project). Original complexity label: complex. This scoping covers the **remaining work after the state audit** (docs/research/2026-09-10-1701-state-audit.md) — the server OAuth 2.1 stack shipped under epic #524 (indicators 1/3/4 done). Remaining-slice rating: **standard** (below).

## Confirmed Problem

A ChatGPT user must be able to (a) complete the OAuth connector flow to the hosted MCP endpoint **regardless of how many teams their account belongs to**, and (b) find a ChatGPT option in the onboarding wizard's harness chooser with correct, current steps and skills-as-prompt copy. Today single-team users can already connect (server shipped), but multi-team users dead-end at consent (`400 invalid_resource` because ChatGPT cannot declare an RFC 8707 team resource and the consent page has no team choice), and the wizard has no ChatGPT entry at all.

## Why This Framing (evidence; rejected framings)

Rejected framings (from 2 adversarial problem-diverge agents + audit):
1. "Server complete; only the wizard tab remains" — **wrong**: live-verified `400 invalid_resource` for multi-team × resource-less on both `/oauth/consent/preview` and `/oauth/consent`. Indicator 4 ("token binds to the user's team") is unreachable for ChatGPT on multi-team accounts without a team choice.
2. "Defer everything; close on server completeness" — wrong: Indicator 5 (wizard ChatGPT tab) is entirely unshipped and is the user-visible acceptance criterion; ChatGPT remains the wizard's "dead end" the issue exists to remove.
3. "The ChatGPT tab is an epic-scale cross-surface vocabulary refactor" (devil's advocate) — **partially true and absorbed**: adding `chatgpt` to `HARNESS_ORDER` ripples to `harnesses.test.js` exactness arrays, the MemorySources capture panel (needs capture `false` + reason), and the archived A0 wizard path (needs total maps). The scope below treats this as a first-class constraint (vocabulary stays *total* everywhere), but the *server* session-capture/analytics vocabularies do **not** need widening — ChatGPT never files sessions and the wizard's harness name only lands in onboarding jsonb.
4. "Real-ChatGPT conformance risk makes the audit's server-complete claim untested" — absorbed as R3 (the human E2E is the primary falsification run) + two small de-risking server tolerances below (origin-root `resource` echo; exact connector URL form).

Unverifiable-external risks are documented, not silently ignored: OpenAI plan entitlement (Pro read/write variance), DCR shared-egress per-IP limiter (20/h/IP → all ChatGPT DCRs share OpenAI egress), ChatGPT's exact `resource` string behavior, consent-page rendering inside ChatGPT's OAuth webview. Each is addressed by (a) a server tolerance where cheap and safe, (b) R3's recorded human E2E which names what to observe, (c) an ops note in the PR body.

### Falsification
Evidence that would prove this framing wrong: if a multi-team account completed ChatGPT OAuth today (no picker, no resource) — falsified live on 2026-09-10 (both endpoints 400). If the wizard's 6 tabs already included ChatGPT — falsified by read (HARNESS_ORDER has 6 entries, no chatgpt anywhere in src).

**Confidence:** 88/100 (only the OpenAI-external behaviors remain uncertain, and the scope degrades gracefully for each).

## Scope (remaining slice)

### R1 — Consent page: team choice for resource-less OAuth clients (multi-team)
Server/UX additive; no schema change. Token stays (user, team)-bound; D4's client-declared resource path is unchanged for clients that declare one. **Branch key is "no team-scoped resource"** (covers both `resource` omitted AND an origin-root echo), never `resource is None`.
- `oauth.py`: `consent_preview` returns the user's **active** memberships (id + name + per-team scoped resource URL), **excluding suspended teams** (`teams.suspended_at` set — a suspended pick must fail cleanly at preview, not mid-authorize), with `team_id: null` instead of raising when the request resolves to **no team-scoped resource and >1 team**; zero-team still 403s; single-team shape unchanged.
- `oauth.py`: `parse_resource` tolerates the **origin root by exact equality** (`{base}` / `{base}/` after the existing `rstrip("/")`) as equivalent to the bare MCP resource — an OAuth client that echoes `resource={origin}` (OpenAI-docs pattern) maps to the default-team path instead of 400. NOT a prefix rule (`{base}/v1/keys` and any foreign origin stay 400). Tokens are only ever minted for a team the user belongs to.
- `hosted_api.py` `/oauth/consent/preview`: no logic change (returns the new shape).
- Consent page (`oauth.py _CONSENT_HTML` + renderer): when the preview lists memberships, render a team `<select>` with picker copy ("Choose the team this connection will use") replacing the "default (sole team)" line; the Authorize POST includes the chosen team's scoped `resource`. Single-team rendering byte-identical visually and behaviorally: the select is present-but-hidden (`display:none`) in the shared markup and only unhidden + populated by the `memberships.length > 1` branch — never actionable or visible on a single/auto-bound team page.
- **Consent-page JS hardening (verifier findings — ships with the picker):** (a) `onAuthStateChange` auto-advances to `showConsent()` so a session landing after the initial `null` does not park on sign-in; (b) `fetchPreview` 401 with a session present shows a "session rejected — sign in again" error + retry instead of silently re-showing sign-in (no infinite loop); (c) Authorize is disabled until the preview resolves (single team) or a team is selected (multi-team); preview failure renders a Retry action instead of leaving a stale actionable consent view that would hit the strict-400 with implementer-facing copy.
- `/oauth/consent` POST path unchanged (already validates membership for a team-scoped resource; strict no-resource multi-team 400 stays as defense-in-depth).
- Tests: update `test_multi_team_default_requires_declaration` to the new contract (preview 200 + memberships; consent POST with a member team-scoped resource binds that team; non-member team 403s; suspended team excluded from preview + its consent POST 403s cleanly; no-resource multi-team consent POST still 400). Add a static consent-page-JS test in the `node --test` pattern: server-render `consent_page_html`, assert the memberships branch emits the select + placeholder and the Authorize body uses the selected team's resource, and single/auto-bound team pages keep the select hidden (`display:none`) and non-actionable.

### R2 — Wizard ChatGPT harness tab (Indicator 5)
Additive entry to the 7-harness vocabulary + live connect-step branch. 6 existing harnesses byte-identical.
- `harnesses.js`: add `chatgpt` to `HARNESS_NAMES`, `HARNESS_ORDER` (last), `HARNESS_INTRO`, `HARNESS_STEPS` (Developer-mode steps w/ URL copy), `HARNESS_INSTALL` (skills-as-prompt payload — no local skills, Claude Web pattern), `HARNESS_SKILLLESS`, `HARNESS_COPY_LABEL` ('Copy prompt'), `HARNESS_CONTINUE_LABEL`, `HARNESS_CAPTURE_SUPPORT=false` + `HARNESS_CAPTURE_REASON`; `UNIVERSAL_COMMAND.chatgpt` (prompt payload; classed **teach-human** — user completes manual steps, then verifies in-chat). New no-key classifier `HARNESS_OAUTH = ['chatgpt']`; teach-human union becomes 3 (desktop/web/chatgpt). ChatGPT connector URL = a NEW `CHATGPT_MCP_URL = 'https://api.premiselabs.co/mcp'` constant **without** the trailing slash (OpenAI's connector validates an `/mcp` suffix; verified server-side that bare `POST /mcp` dispatches directly into the mounted MCP app — no 307).
- **Server vocab decision (P1 resolution):** `_SESSION_HARNESS_VALUES` / `_HARNESS_ANALYTICS_VALUES` (capture + copy-attribution Literals) are NOT widened — chatgpt never files sessions. The wizard's copy beacon PATCHes `{harness:'chatgpt', section:'config'}`; the server POPS harness/section before the state merge — it never lands in onboarding jsonb and, being outside the analytics enum, emits no `artifact_copied` event (inert, no error). Amend `tests/test_onboarding_endpoints.py::test_cross_surface_harness_vocab_contract` to subset semantics: server vocab stays the pinned 6 capture-capable values; `frontend ⊇ server`; `frontend - server == {'chatgpt'}` with a wizard-only comment. Do NOT "fix" the missing beacon by widening a Literal.
- `main.jsx` live connect step (self-fork, wizardStep 2): **invariant — the chatgpt connect branch renders for `wizardHarness === 'chatgpt'` UNCONDITIONALLY, above the `!harnessKey` gate**, regardless of `harnessKey`, `isOwnerAdmin`, or `wizardCopied` state. Members never see the owner/admin mint/paste gate; owners never mint a useless key; a key minted on an earlier claude-tab visit never leaks into chatgpt copy. Renders: steps list (`harness-steps` ol with per-step copy via the existing `wizardCopyStep`/`copiedStep`), intro, prompt snippet, Copy prompt (reuses `wizardCopy` → sticky label + copy beacon), Continue → `wizardHarnessContinue` (harness-connected checkpoint). Own Back/Skip chrome. The "shown once" key paragraph and the "Run the command… tell your agent" fragment (exclusion list) are unreachable for chatgpt by construction.
- MemorySources capture panel: chatgpt row renders the disabled-with-reason form (no crash; no undefined reason).
- Archived A0 legacy wizard maps stay total (HARNESS_INSTALL/HARNESS_STEPS/HARNESS_INTRO for chatgpt exist) so a rollback cannot crash on a chatgpt tab.
- `harnesses.test.js`: rewrite the exactness assertions for the 7-harness vocabulary (DE2E-5: self-install 4 / teach-human 3 incl. chatgpt; disjointness; universal command total). Teach-human verify phrasing becomes per-harness — chatgpt asserts its own tool-scan/in-chat verify sentence, NOT `tortoise_health` (its exact mechanics are an R3-unknown). New invariants: `HARNESS_OAUTH` disjoint from `HARNESS_SELF_INSTALL` and ⊆ `HARNESS_TEACH_HUMAN`; chatgpt ∉ any capture list. chatgpt copy assertions: contains Developer mode / plugins / `CHATGPT_MCP_URL` (exact no-slash) / OAuth / skills prompt; contains NO key, NO `tt_`, NO `.../mcp/` slash form. The pinned DE2E-5 teach-human `tortoise_health` regex applies to desktop/web only.
- Internal count comments updated (`harnesses.js` "six supported harnesses" skill-table comment, `main.jsx` "covers all 6 harnesses" comment) to the 7-tab reality.
- Rebuild `website/apps/dashboard/dist` (hashed bundle + index.html) in the same commit (repo convention).

### R3 — ChatGPT human E2E script + evidence template (Indicator 2)
OpenAI's connector creation is human-in-the-loop. Deliverable = a precise recorded script (docs/research/2026-09-10-1701-chatgpt-e2e-script.md) + evidence template; the merge is NOT blocked on the human click — the PR body marks it as the manual verification step with what to record (plan tier, tool scan result, a graph write round-trip, URL form used, any redirects, refresh behaviour after 24 h).

## Rejected Alternatives
| Alternative | Why rejected |
|---|---|
| GitHub-model: user-bound token, org as per-request tool param | Rejects #524's team-bound boundary; refactor across every MCP tool and the graph namespace — P0 scope. Account-chooser-at-consent (Cloudflare/Zapier pattern) reaches the same user outcome at ~5% of the cost. |
| No R1 now (document-only) | Multi-team is a live 400 dead-end for the exact client this issue enables; fix is small and additive. |
| Auto-pick first/sole team for resource-less multi-team | Ambiguous grant binding without user consent; account-chooser is the honest, standard pattern. |
| Mint an API key anyway and key-gate the chatgpt tab | ChatGPT has no header/key input — key would never be used and would burn the free cap; contradicts the issue's whole premise. |
| Change `MCP_URL` to drop the trailing slash for all harnesses | Violates the byte-identical-6 constraint; only ChatGPT's connector validates the suffix, so only chatgpt gets its own URL string. |
| Add chatgpt to server session-capture/analytics Literals | Semantically wrong (ChatGPT-cloud capture impossible) and unnecessary — the wizard never sends `harness=chatgpt` to capture endpoints. |
| DCR per-IP limiter raise/whitelist for OpenAI egress | Weakens an abuse control without evidence of pressure; flagged as an ops note + monitor point in R3, not a code change. |

## Verification Checklist (populated fractal fields)
| #1701 indicator | Surface | Test layer | Verification |
|---|---|---|---|
| 1/3/4 (already shipped) | discovery/PKCE/mcp | existing suite | regression: full oauth + hosted-auth + mcp-auth suites green |
| 4 (R1) | consent preview shape | unit/integration | multi-team preview 200 + memberships; consent binds picked team; non-member 403; strict no-resource POST preserved |
| 4 (R1) | origin-root resource echo | unit | `parse_resource(base, base)` → bare MCP resource (default team) |
| 5 (R2) | harness vocabulary | node --test | 7-harness exactness; chatgpt copy rules |
| 5 (R2) | live wizard render | ux (manual + build) | chatgpt tab key-less; steps + prompt; continue writes checkpoint |
| 2 (R3) | ChatGPT E2E | e2e (human, recorded) | recorded script + evidence; PR-body manual step |

## Wiring Check
| Touch point | Type | Covered by |
|---|---|---|
| `tortoise/oauth.py` (consent_preview memberships, parse_resource origin-root) | server | R1 |
| consent HTML/JS (`_CONSENT_HTML` picker + hardening) | server-rendered page | R1 |
| `tests/test_oauth_mcp.py` (contract + boundary matrix) | tests | R1 |
| consent page JS static assertions (node --test) | tests | R1 |
| `docs/oauth-mcp.md` L52 mapping-table row (preview contract + origin-root tolerance) | docs | R1 |
| `harnesses.js` vocabulary + comments | dashboard src | R2 |
| `main.jsx` live connect step (branch invariant) + MemorySources row | dashboard src | R2 |
| `harnesses.test.js` | node tests | R2 |
| `tests/test_onboarding_endpoints.py::test_cross_surface_harness_vocab_contract` | server pytest | R2 (subset semantics; server Literals unchanged) |
| `dist/` rebuild + index.html | deploy artifact | R2 |
| Server capture/analytics Literals (`_SESSION_HARNESS_VALUES`, `_HARNESS_ANALYTICS_VALUES`) | server | none — unchanged (chatgpt wizard-only) |
| `tortoise/onboarding/SKILL.md` + public/dist mirrors | served skill | none — chatgpt is wizard-only; the skill's "six" describes the skill-installing agent set (still correct) |
| E2E evidence doc + PR-body manual step | docs | R3 |

### Verification-gate resolutions (problem+solution verify, 2+2 verifier agents)
- P1 (wiring): server pytest `test_cross_surface_harness_vocab_contract` parses `HARNESS_ORDER` and asserts frontend==server==6 → amended to subset semantics (chatgpt wizard-only carve-out). Server Literals stay 6.
- P1 (wording fix): wizardCopy does NOT persist harness to onboarding jsonb and chatgpt emits no analytics beacon — copy beacon is inert by design; no Literal widening.
- P2 (consent JS): onAuthStateChange auto-advance, 401-with-session retry, Authorize disabled until team resolved, preview-failure Retry, static page-JS tests.
- P2 (suspended teams): excluded from memberships preview.
- P2/P3 (parse_resource): exact-equality only + full boundary test matrix (foreign origin 400, base+'/v1/keys' 400, base+'/mcp/' bare, origin-root × multi-team preview 200, origin-root × single-team binds sole team).
- P3 (A0 rollback): archived legacy wizard key-gate treats `HARNESS_OAUTH` members as key-satisfied (render copy path without a key) so a rollback cannot crash/mislead on the 7th tab.
- P3 (docs): `docs/oauth-mcp.md` mapping row updated; internal count comments updated; served onboarding skill untouched by decision (b).
- P4 (URL): no 307 for bare `/mcp` (verified empirically) — no follow-up needed; R3 evidence records the URL form.

## Complexity (remaining slice)
| Domain | Rating | Rationale |
|---|---|---|
| UX | standard | Existing claude-web tab pattern; key-less branch is new but bounded; copy follows proven structure |
| Architecture | standard | Small additive server change on a shipped, well-tested path |
| Security | standard | parse_resource leniency is narrow (origin root → team-bound default); server stays strict on mint |
| Ontology | low | No schema changes |

## Notes for the plan
- Open external risks to verify in the human E2E (not merge-blocking): OpenAI plan entitlement, DCR egress limiter pressure, consent page inside ChatGPT webview, exact URL/redirect behaviour.
- Ops note for PR body: DCR `TORTOISE_OAUTH_DCR_PER_HOUR` (20/h/IP) is shared across OpenAI egress — watch on launch.
- Adjacent product gap (NOT absorbed): org admins have no inventory/revocation surface for members' OAuth connections. Filed separately.
- chatgpt verify-in-chat sentence is R3-validated before it is treated as true; the plan pins the exact sentence and R3 confirms it.
