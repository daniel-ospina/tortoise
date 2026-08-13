---
title: "Strategy Alignment Decision — Epic #529: Harness-specific onboarding variants (Pi / Claude Code / Codex / Cursor)"
type: decisions
domain: strategy
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-11
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Strategy Alignment Decision — Epic #529: Harness-specific onboarding variants

**Feature:** Ship harness-specific onboarding variants on top of the generalized one-artifact onboarding shipped in epic #235, so each agent harness (Pi, Claude Code, Codex, Cursor) gets its optimal setup surface — config + prompt — reaching first memory in ≤2 copy-paste actions (structural for file-based variants, behavioral for the chat-paste leg — see AL-7b).

**Decision: PROCEED.**

## Review-gate fixes applied (fresh-context reviewer, 3 cycles)

1. **P1 — Profit instrument (cycle 1):** added Measurement subsection — per-harness `artifact_copied` attribution via repaired welcome-page beacon (auth + body) and `harness`/`section` fields on the existing PATCH model.
2. **P1 — Inherited #235 A1 (cycle 1):** re-surfaced as AL-7b with per-trigger-model confidence split and chat-paste fallback line.
3. **P1 — Instrument non-functional as specified (cycle 2):** code-verified the existing beacon is dead (no body, no auth → 401); scope corrected to "micro-build task: beacon repair + model extension + event emit; no new endpoints".
4. **P2 — File-vs-chat asymmetry (cycle 2):** corrected — all four harnesses have file-based instruction surfaces (CLAUDE.md/AGENTS.md/.cursor/rules); the split is a scope choice following the epic body's prescribed variant shapes; CLAUDE.md/AGENTS.md documented as persistent alternatives.
5. **P2 — Bundle justification / per-harness priority (cycle 1):** Cursor/Pi = build budget, Claude/Codex = verification-first budget.
6. **P2 — Cursor literal-key posture (cycle 1):** AL-3 conditional on `${ENV}` expansion support in `.cursor/mcp.json`.
7. **P2 — Manufactured urgency (cycle 1):** urgency declared self-imposed (knowledge freshness), no external clock.
8. **P2 — Harness enum conformance (cycle 3):** beacon emits raw harness names per #235's `artifact_copied` enum (no `-v1` suffix).
9. **P2 — PATCH handler impl note (cycle 3):** `harness`/`section` must be popped from the state merge before `_update_onboarding_state` (same pattern as `email`); the event emit reads raw body fields.

**Gate result:** ISSUES fixed through cycle 3; cycle 3 returned one P2 (fixed above) + non-blocking minor notes. Align gate CLEARED.

**Context:** Epic #235 (CLOSED, shipped) delivered the generalized flow: welcome page with 4 harness tabs (MCP config Block A per harness + shared onboarding prompt Block B), canonical prompt at `tortoise/onboarding/AGENT_ONBOARDING.md` deployed to `https://premiselabs.co/onboarding-prompt.md` via `deploy-pages.yml`, hosted onboarding MCP tools + `/v1/onboarding/*` REST endpoints, and the CLI printing the prompt URL after `tortoise onboard`. #235's scope doc explicitly deferred "Harness-specific setup instructions (Pi/Claude/Codex/Cursor)" to "Future epic (harness-specific optimizations)" — this epic is that future epic. Its research brief flagged assumption A10 (LOW confidence): "One combined artifact works across Pi, Claude Code, Codex, and Cursor … Different paste mechanisms may require different artifacts." #529 resolves A10 by shipping per-harness variants that share the generalized core.

## Step 1 — Adversarial Strategy Test

**Alternatives considered:**

1. **Keep the single generalized artifact (status quo post-#235).** The welcome page already renders a config tab per harness plus one shared prompt. Zero build cost.
   *Why rejected:* the current Block A configs are harness-adjacent but not harness-optimal: Claude Code is shown a raw `mcpServers` JSON blob instead of the canonical `claude mcp add` CLI one-liner (Claude Code users must hand-merge JSON into `.mcp.json`); Codex is shown a shell export + CLI pair (close, but untested as a paste path); Cursor is shown only the `mcpServers` JSON — the `.cursor/rules/` instruction surface is unused, so the Cursor agent is never *told* to run onboarding; Pi is shown JSON with no instruction surface at all. The generalized artifact optimizes for the intersection of harnesses, which is the worst surface for each individual harness. This is exactly the failure A10 predicted.
2. **Auto-detect on connect (MCP on-connect hook that triggers onboarding).** Best UX if feasible.
   *Why rejected:* #235 research confirmed no standard MCP "on-connect" hook exists; harnesses don't notify agents of new servers in a reliable way. Not buildable in this epic's envelope. Kept as a north-star note, not scope.
3. **White-glove-only onboarding (human sets up each user).** No build cost, highest success rate per user.
   *Why rejected:* does not scale and produces no durable, testable paste path — the epic's indicators (documented + tested paste path per harness) exist precisely so onboarding survives without white-gloving. Retained as fallback for edge cases, not as the product surface.
4. **Single per-harness bootstrap script (`curl | bash` installer).** One command does everything (writes config + instructions). Closest to true one-artifact for all harnesses.
   *Why partially adopted/rejected:* executing arbitrary install scripts is a trust barrier for the security-conscious MCP audience, and each harness stores config differently (Claude user-scope JSON vs Codex TOML/env vs Cursor `.cursor/mcp.json` vs Pi `.mcp.json`), so one installer must embed four writers. For Pi — where the harness natively resolves `.mcp.json` with env-var expansion and reads instruction files (AGENTS.md/skills) — a *file pair* achieves the same outcome without script execution. Script installer deferred; file-pair variants ship now.

**Anti-post-rationalization — strongest reasons NOT to build this:**

- **Volume:** hosted platform has single-digit users (#235 A3). Four variant surfaces is 4× maintenance for ~0 current traffic. *Counter:* the surface already exists and ships to every signup; the cost of it being wrong is paid per-user regardless of volume, and fixing it now (while the flow is fresh and small) is cheaper than re-learning it during an acquisition push.
- **Polish, not unlock:** the generalized flow technically works on all four harnesses. *Counter:* "works" ≠ "reaches first memory in ≤2 paste actions without confusion." Cursor today has no instruction surface at all (prompt must be pasted into chat manually and the user must know that) — that is a real gap, not polish.
- **Drift risk:** four variant prompts can diverge from the canonical `AGENT_ONBOARDING.md` (the #496 single-source-of-truth rule). *Counter:* accepted as the primary design constraint — variants are thin harness wrappers (config + delivery instructions) around the SAME core prompt; the flow questions live in exactly one place and variants either embed-by-reference (URL fetch) or are generated/validated against the core in CI. See Plan.
- **Harness churn:** `claude mcp add` / `codex mcp add` syntax evolves; pasted commands rot silently. *Counter:* accepted risk; mitigated by keeping the canonical command strings in one module/file and covering them with ref tests (pattern already exists: `tests/test_onboard_prompt_ref.py`).

**Opportunity cost:** the same effort could go to dashboard polish or GitHub-indexing reliability. Those improve post-setup value; this epic protects the only conversion path into the product. With the hosted platform live and #235's surface shipped, completing the setup surface is the highest-leverage cheap work available.

## Step 2 — Eisenhower Matrix

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | **← #529 lands here** | Dashboard polish, indexing reliability |
| **Not Important** | — | Installer-script variant, auto-detect hook |

Justification: the onboarding surface is live and is the first thing every new user touches — the gap between "generic artifact" and "harness-optimal artifact" is paid by every signup from now until fixed (important), and the fix is cheap while #235's context is still fresh. **Urgency honesty (reviewer P2-3, accepted):** the urgency is self-imposed (knowledge freshness + cheap-now), not event-driven — no external clock exists at single-digit user volume. Placement stays Do-now on that explicit basis, not on a manufactured deadline.

## Step 3 — Profit Growth Alignment

Causal chain: harness-optimal paste path → fewer setup failures and less confusion per harness → higher landing→first-memory completion rate → more users experience "my agent remembers" in session 1 (the churn determinant per #235 research) → higher retention → higher conversion when paid tiers ship.

Magnitude: **$0/month directly at current traffic** (no paid tier exists). This is funnel-protection, not revenue-generation: it protects 100% of future acquisition spend from leaking at setup. Rough model: if harness-specific surfaces lift landing→first-memory completion by even 10–20 percentage points on the two file-pair variants (Cursor, Pi — today weakest: no instruction surface at all), every future cohort compounds on it. Cost envelope: content + one welcome-page section + deploy wiring + tests + the beacon repair and model extension described under Measurement — no new backend endpoints required (all `/v1/onboarding/*` infra ships in #235).

**Measurement (reviewer P1-1, accepted; corrected per cycle-2 review):** the profit chain's testable middle link is landing→first-memory completion; nothing instruments it today (#235 shipped funnel analytics as design-only). Code truth (verified this session): `welcome.html`'s copy beacon fires `fetch(…/v1/onboarding/state, {method:"PATCH"})` with **no body and no Authorization header** — the PATCH endpoint requires Bearer auth (`Depends(get_current_team)`), so the beacon 401s today and has never recorded anything. The instrument is therefore a micro-build task in this epic, not free reuse: (a) welcome page sends the beacon WITH the key it already displays (`Authorization: Bearer <key>`) and a body `{harness: "claude"|"codex"|"cursor"|"pi", section: "config"|"prompt"}` — raw harness names, matching #235's `artifact_copied` enum exactly (cycle-3 fix: no `-v1` suffix; variant version is implied by event timing, keeping funnel queries compatible with #235's schema); (b) backend adds `harness`/`section` optional fields to `OnboardingStatePatchRequest` and emits `_track_analytics_event(team_id, "artifact_copied", {harness, section})` from the PATCH handler — both prop names already exist in `_ALLOWED_ANALYTICS_PROPS` and `artifact_copied {harness, section}` is exactly the event #235's analytics schema designed. Scope honesty: **no new endpoints, one model extension + one event emit + beacon repair**; the beacon fires on config copy (prompt copy fires `section: "prompt"`), keeping event meaning unambiguous. (c) The E2E verification doc records machine-verified legs (config validity, URL reachability, first-memory write) per variant as the baseline. The profit claim beyond this baseline stays untestable until volume exists.

## Step 4 — Key Assumptions

| # | Assumption | Confidence | Validation |
|---|-----------|-----------|-----------|
| AL-1 | `claude mcp add --transport http` with an Authorization header connects to the hosted Streamable-HTTP server | MEDIUM | E2E: run the exact command against api.premiselabs.co (or local hosted test server) in Claude Code; human-in-harness for the chat leg |
| AL-2 | `codex mcp add tortoise --url … --bearer-token-env-var TORTOISE_API_KEY` is current Codex CLI syntax and connects | MEDIUM | Verify against current Codex CLI docs + E2E attempt from this machine |
| AL-3 | Cursor reads project `.cursor/mcp.json` (`mcpServers` with `url` + `headers`) and `.cursor/rules/*.mdc` rule files; the rule can instruct the agent to run onboarding. Includes whether `.cursor/mcp.json` header values support `${ENV}` expansion — if yes, ship env-var indirection (no literal `tt_` key on disk, matching the Pi variant's security posture); if no, document the literal-key trade-off explicitly | MEDIUM | Verify current Cursor docs format (mdc frontmatter) + env-expansion support |
| AL-4 | Pi's mcp-client resolves `.mcp.json` (cwd → git-toplevel → `~/.pi/agent/.mcp.json`) and expands `${VAR}` in header values, so no literal key is needed in the file | HIGH | Verified in mcp-client extension source (`resolveMcpJsonPath`, env expansion incl. `${VAR:-default}`) this session |
| AL-5 | Variants can share the generalized core without feature divergence (wrapper-not-fork design) | HIGH | Design constraint enforced in Plan + coherence review; CI drift check |
| AL-6 | The chat-paste leg of each variant needs human-in-harness verification; automation can cover config validity, URL reachability, and prompt-content integrity — including a per-harness paste-trigger test (does the flow actually start after the paste?) | HIGH | Test plan splits machine-verifiable vs human-verified legs |
| AL-7b | **Inherited from #235 A1 (LOW, never validated):** users must paste config AND prompt; pasting only the config never starts the flow. **Cycle-2 correction:** the file-vs-chat split is a scope choice, not a harness property — Claude Code auto-loads `CLAUDE.md` and Codex CLI reads `AGENTS.md`, so all four harnesses have a file-based instruction surface and "≤2 pastes" COULD be structural everywhere. The epic body prescribes the variant shapes (Claude/Codex = CLI + chat prompt; Cursor/Pi = file pairs); the Plan implements the prescribed shapes and documents `CLAUDE.md`/`AGENTS.md` blocks as the persistent alternative for Claude/Codex (eliminating repeat-paste per session). "≤2 paste actions" is structural for file-based variants and behavioral for the chat-paste leg | MEDIUM (file) / LOW (chat) | Research stage verifies CLAUDE.md/AGENTS.md auto-load behavior; chat-paste variants ship a fallback line ("If the agent doesn't start the flow, paste: 'Start Tortoise onboarding'"); human-in-harness test covers the trigger |
| AL-8 | No new backend endpoints are needed — #235's `/v1/onboarding/*` + MCP tools cover all four variants (the measurement instrument above extends one existing model; it adds no endpoint) | HIGH | Surface inventory in Research stage |

## Per-harness priority (reviewer P2-1, accepted)

The unlock case is proven for **Cursor and Pi** (no instruction surface exists today — the agent is never told to run onboarding). **Claude Code and Codex** are polish-level (JSON→CLI ergonomics swap; untested-but-plausible existing pair). Effort allocation in the Plan reflects this: Cursor/Pi = build budget (new file-pair surfaces), Claude/Codex = verification-first budget (confirm the paste path E2E, upgrade the surface where it is free — e.g. the `claude mcp add` one-liner).

## Step 5 — Routing

PROCEED → hand off to epic-research (light: reuse #235's research brief; the harness matrix exists; focus on verifying current harness CLI/file syntax and the exact shipped surface inventory).

## Human-gate note

Per dispatch directive (epic filed and execution authorized; no human approval pauses), the epic-workflow human gates after Scope and Plan are satisfied by documented self-review + fresh-context reviewer cycles; decisions that would have paused for a human are recorded inline in the stage docs.
