---
title: "Alignment Assessment — Parallel Epic #338"
type: engineering
domain: platform
doc_status: approved
subjects.team: organisation-design-team
created: 2026-08-07
---

<!-- epic: tortoise-user-journeys → issues #518 + #519 -->
<!-- date: 2026-08-07 -->
<!-- purpose: alignment check against parallel epic #338 (service-model migration) and its children -->

# Alignment Assessment — Parallel Epic #338 (Service-Model Migration)

**Checked:** #338 + PR #517 (research/scoping/plan docs) + children #523/#524/#525/#526 + #292 (hosted API regression) + #235 (onboarding).
**Method:** read the #338 implementation plan (`.worktrees/338-service-model/docs/plans/2026-08-07-338-service-model-plan.md`) and scoping doc in full; verified current code state for #292.

---

## Verdict: **ALIGNED with 2 coordination points + 1 dependency to honor**

No scope conflicts. Both epics are complementary halves of the same product direction: **#338 makes self-host a first-class service (daemon + license); this epic makes hosted a first-class journey (signup → dashboard → first memory) with the decoupled team/graph model**. They must land together — #338's README (T5.1) and our landing/pricing page tell the same "install → connect → query" story.

---

## 1. Direct dependencies (honor these)

| #338 item | Our epic's dependency | Coordination needed |
|---|---|---|
| **T5.1 README rewrite**: "hosted AND self-host are BOTH first-class quickstart paths… hosted is being built now (epic #235 + #518/#519/#292)" | Our epic IS the hosted quickstart that #338's README promises | Both must ship before launch; neither marks the other "coming soon". #338 already dropped the pre-merge coordination check (owner override) |
| **D3 license**: BSL 1.1 + $5M AUG (self-host) / hosted = commercial with free tier (outside grant) | Our pricing page self-hosted section must say **"free under BSL grant"** — NOT "free OSS" (BSL is source-available, not OSI open-source) | **FIXED in `product/pricing.md`** (self-hosted section now carries the BSL + $5M AUG + Apache-2.0-in-4-years license line) |
| **T5.4 D7 graph supersede** (file BSL decision, supersede DEC-002) | Our `product/pricing.md` supersedes the per-seat graph decision | Distinct decisions (license vs pricing) — no conflict; both reference the wiped-graph history |

## 2. Coordination points (no conflict, but sequence matters)

### CP-1: Shared auth stack — hosted_api.py (ours) vs mcp_server.py/mcp_auth.py (#338 T1.1)
- #338 T1.1 adds `auth_mode` param to `create_http_app()` (default "tenant" = byte-identical hosted); our epic adds tier-driven limits + session-held-key auth to `hosted_api.py`.
- **No file conflict** (different files), but both touch the hosted auth boundary. **Sequence:** #338's `auth_mode` is purely additive with a byte-identical default — it can land independently. Our decoupling work touches `get_current_team`/`team_create` (registry + limits) — also independent. **Risk is low; keep both PRs reviewable in isolation; if they touch the same test files, rebase order matters.**
- **Note:** #338's selfhost daemon uses `auth_mode="static"/"none"` — the SAME `TeamResolutionMiddleware` we extend for session-held keys. When session-held-key auth lands in our epic, the `"tenant"` mode carries it; selfhost modes are unaffected (they omit the middleware).

### CP-2: Landing/docs narrative — our pricing page + self-host section vs #338 T5.1/T5.2 (README, index.md)
- Our deliverable 13 (pricing page on tortoise.premiselabs.co with self-hosted section) and #338 T5.1 (README service-first rewrite: "Install → Connect → Query", hosted AND self-host both first-class) tell the same story.
- **Coordinate the copy:** landing CTA (hosted primary, "Connect your agent →") + "Self-hosting docs →" must match README's "hosted signup (free tier) OR `docker run`/`docker compose`" framing.
- #338 T5.1 references `claude mcp add tortoise https://api.premiselabs.co/mcp` and `codex mcp add` — our welcome page already presents the MCP config for Claude Code + Cursor; extend to Codex for consistency (small, in scope).

## 3. Children of #338 — status vs our epic

| Issue | Title | Status | Alignment with our epic |
|---|---|---|---|
| #523 | MCP tool-surface curation (58 tools past cliff) | OPEN | No direct overlap. Our dashboard/welcome present MCP config; tool COUNT is an onboarding-epic (#235) concern. Note as related |
| #524 | OAuth 2.1 remote MCP auth | OPEN (complex) | **Design criteria captured on #524** (token→team mapping under decoupling, per-team billing, tt_ fallback, build-after-decoupling). Deferred cleanly — no login-breaking impact when it lands |
| #525 | REST API completeness (from tool_registry RestSpec) | OPEN | No overlap. #525 extends REST for the service model; our dashboard uses existing `/v1/team`, `/v1/team/keys`, `/v1/sessions`, `/v1/points` — all live |
| #526 | SDK/client package split (deferred 2nd half) | OPEN | No overlap. Our self-host flow uses CLI (`tortoise init`/`onboard`) which stays importable; #526 is the eventual engine split |

## 4. #292 (hosted API 500 regression) — **already fixed**

- **Concern:** POST /v1/points, /v1/team/keys, /v1/sessions returned 500 (NameError `request` not defined — parallel sub-agent edits added `_async_audit(request,...)` without the param).
- **Verified fixed in current code:** `create_point(body, request, team)`, `create_api_key(request, response, team)`, `capture_session(body, request, team)` all have `request: Request` in signatures (hosted_api.py:609, 964, 1077).
- **Action for our epic:** E2E-1/E2E-3/E2E-6 (provision, key recovery, first point) exercise these exact endpoints — the live E2E walk will confirm the fix holds. No action needed now; issue stays OPEN until a walk verifies.

## 5. Owner override to respect (from #338 plan §6)

> "Hosted is being built now (epic #235 + #518/#519/#292), must not be disabled in docs; zero external users pre-launch, so no dead-end risk; both options ship fully and launch together. **Priority: SAP — full scope (both options working), then launch.**"

This **confirms our epic's framing** (hosted PRIMARY, self-hosted real second path) and removes any "hosted coming soon" hedging from the docs. Our landing/pricing copy must present both as live, with hosted as the primary CTA.

---

## Actions taken

1. **`product/pricing.md`** — self-hosted section now carries the BSL 1.1 + $5M AUG license line (per #338 D3), replacing the inaccurate "free OSS" phrasing.
2. **Scope doc (03-scope.md)** — will note the #338 coordination in the boundary rationale (CP-1/CP-2).
3. **#524 comment** (already posted) — design criteria align with #338's OAuth direction.

## Open items

- None blocking. When both epics near launch, run a **joint copy review** (landing + pricing + README + welcome) so the "install → connect → query" story is consistent across surfaces.
