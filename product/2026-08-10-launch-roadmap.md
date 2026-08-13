---
title: Tortoise Launch Roadmap — issue triage + prioritization
type: roadmap
domain: product
status: live
created: 2026-08-10
updated: 2026-08-10
---

# Tortoise Launch Roadmap

Result of a full audit of all ~145 open issues (10 parallel reviewers, each verified
against `origin/main` @ `23942d2`). Full per-issue evidence: `/tmp/tortoise-audit/cluster-{A..I2}.md`.

## MVP definition (owner, 2026-08-10)

1. **P1 — Get going:** a user's agent installs the **self-hosted** version, OR the user
   signs up for the **free hosted** version, OR the **paid hosted** version (Stripe works).
2. **P2 — Sessions saved & indexed:** agent chats/sessions are captured, saved, and
   indexed in the graph. **Deep extraction/mining from chats is explicitly NOT MVP.**

## Headline finding

**The tracker is badly stale.** Of ~145 open issues:
- **~50 are DONE** — shipped in main but never closed (incl. the entire ONTOLOGY v2.5
  epic #377 family, the entire feat(memory) #401–#415 family, the ontology-viz epic,
  Stripe billing #310, dashboard #300, backups #596, agent signup #663).
- **~12 are SUPERSEDED** (epic-7421 pi-plugin direction → replaced by service-over-MCP;
  v2.5 → v3.2 ontology drift; #328 lockfile → #492).
- **~6 are genuine launch blockers.** Everything else is post-MVP backlog or drop.

The **session save+index pipeline (P2) is essentially done on main** (#320 epic:
capture → auto-index → semantic search all shipped via #721/#722/#280/#511/#793/#796).
The **hosted signup→key→MCP path works** but has one production blocker (#801) and one
control-plane consistency gap (#765 subset).

---

## WAVE 0 — Tracker hygiene (do immediately, ~1h)

Close as **DONE** (shipped in main; cite PR in close comment):

| Issues | What shipped |
|---|---|
| #300, #306, #310, #311, #313, #314, #596, #663, #823 | Hosted platform: dashboard, infra, Stripe, onboarding+demo, GitHub connect, MCP mount, backups, agent signup, brute-force throttle |
| #378, #379, #380, #381, #382 (v3.2 drift note), #383, #384, #385, #386, #387, #389, #393 | ONTOLOGY v2.5 epic (~90% landed; spec now v3.2) |
| #401, #402, #403, #404, #406, #407, #408, #409, #411, #412, #413, #414, #415 | feat(memory) family — entire P0 read/navigation + extraction stack |
| #286, #509, #345, #321, #322 | Test deficit closed (2,550 tests), stale search tests fixed, FTS/RRF shipped |
| #817, #818, #820, #493 (core) | CLI/test/CI fixes already merged |
| #357, #358, #359, #360, #361, #369, #374, #535, #336, #340 | ontology-viz epic, SDK versioning, READMEs, onboarding capstone, readiness assessment, VSM split |

Close as **SUPERSEDED**: #377, #418 (v3.2 drift), #319 (BUSL answers it), #328 (→#492/#494),
#339, #350, #351, #352, #353, #354, #355 (pi-plugin direction abandoned for service-over-MCP),
#356 (backend endpoints gone — note: graph-viz Cypher still `%s`-formatted, dev-only tool),
#410 (replaced by list_pointkinds/list_sources).

Close as **DROP** (wrong product/repo or no code surface): #333, #346, #376 (DMeer),
#397 (internal data task), #417, #427 (internal skills), #489 (relocated to agent-infra#129).

**Owner actions (no open issue tracks them):** register GitHub OAuth App (#542 residual),
`supabase db push` + analytics secrets (#543 residual).

---

## WAVE 1 — Launch blockers (this week)

True blockers for P1 (get going) + P2 (saved & indexed):

| Pri | Issue | Why it blocks | Effort |
|---|---|---|---|
| 1 | **#801** Supabase email rate limit (429) kills free signups | Production signup funnel dies at volume. Supabase dashboard auth-email config fix + live E2E verify | micro |
| 2 | **#765 (urgent subset)** hosted writers still hit FalkorDB registry | Auth already flipped to Supabase (#851) — but register / agent_signup / `/v1/team/keys` mint+revoke+list still write the old registry → **freshly minted keys can 401**. Migrate these writers to `api_keys` w/ lookup_hash | standard |
| 3 | **#849** embedded_reaper kills the server it probes | Fail-open raw-RESP fallback closes the live embedded DB — data loss on the no-Docker self-host eval path | micro/standard |
| 4 | **#304** CLI: `tortoise init --api-key` + team commands | The hosted "agent connects" CLI surface; deps satisfied, ready to build | standard |
| 5 | **#337** pre-public secret audit | Repo already public; gitleaks full-history scan never run (~1h). Launch gate, low expected risk | micro |
| 6 | **#320 close-out** session-index epic | Batch-run the 4,190-file index + I1/I6 verification, then close epic (proves P2 end-to-end) | ops run |

Cheap wins to bundle into Wave 1:
- **#812** — 2 missing SDK methods (`events_poll`, `retract_point`) → events MCP tools stop 500ing; lets #432 close. ~2 methods.
- **#343** — 1-line crash fix (`_get_sdk()` catches ImportError only).

---

## WAVE 2 — Launch hardening (week 2)

| # | Issue | Why pre-launch |
|---|---|---|
| #494 | Dockerfile.hosted double-install + requirements.txt parity → deploy reproducibility (lockfile now available from #492) |
| #390 + #391 | One batched PR: ownedBy DAG-check bypass + about* edges in create_edge predicate set (worktrees exist, 0 commits) |
| #855 (+ land unmerged drift fix on `fix/844-ep-directional`) | EP NAND under-propagation — correctness of belief scores users will see |
| #331 | Triage P1 crash batch (e.g. `merge_points(None)` TypeError) |
| #308 | Signup CAPTCHA (Turnstile) — pairs with #801 fix |
| #302 remainder | Data-export + account-deletion endpoints (security baseline otherwise shipped) |
| #826 | welcome-e2e CI timeout — unblocks merge queue |
| #748 + #749 | Tests for revenue surfaces (session-key/invites HTTP layer; pricing mirror) — do together |
| #492 residual | AGENTS.md uv docs + CI pip→uv parity |

## WAVE 3 — Launch verification (week 3)

| # | Issue | Notes |
|---|---|---|
| #303 | Hosted E2E suite (12 cases) — partially covered already by welcome/billing e2e |
| #291 | Capstone full-platform E2E — depends on #303 |
| #323 | Hybrid-search capstone gates 1–4 (benchmarks/gate 5 deferred) |
| #529 | Harness-specific onboarding verification (Pi/Claude Code/Codex/Cursor paste-paths) |

**Launch = exit Wave 3.**

---

## POST-MVP backlog (sequenced by value)

1. **#265** client-side content encryption — #1 paid-tier question per #336 assessment
2. **#264 family** (#779–#787, #822, #416) — conversation mining Phases 2–4 + LLM-grade extraction (explicitly not MVP)
3. **#524** OAuth 2.1 remote MCP — additive over tt_ keys; when connector UX matters
4. **#557** sub-tenancy — after user↔team decoupling ships
5. **#669 remainder** (#763 invites, #764 onboarding/GH state, #766, #768, #771 registry retirement) — safe parallel/post-launch
6. **#316/#317** search benchmarks + reranking · **#395/#396** local EP · **#522** perf sweep · **#392/#394/#388** ontology backfill/provenance/Source nodes · **#405** domain constraints · **#526** SDK/client package split · **#691** fastmcp push · **#438** cross-domain discovery · **#307** invite/key-recovery emails · **#362** dynamic ontology kinds · **#753** two-layer extraction review · **#844** EP test threshold (symptom of #855) · **#819/#821/#824/#825/#332** hygiene
7. **#334/#347/#348** internal graph-data remediation (ops, not product)

---

## Critical path, one line

```
#801 signup fix → #765 key-mint writers → #304 CLI → #849 reaper  ──┐
#337 secret audit · #320 index run · #812/#343 (cheap)              ├→ Wave 2 hardening → Wave 3 E2E → LAUNCH
```

---

## Execution status (2026-08-10 evening)

**Wave 0:** DONE — 72 issues closed (52 done, 13 superseded, 7 drop) + #812/#320/#323-partial.
**MVP proof:** #320 epic CLOSED — 1,537 sessions batch-indexed, 0 errors, I1/I6 verified (P2 works end-to-end).

| Roadmap item | Status |
|---|---|
| #801 signup rate limit | PR #860 (server-side GoTrue bypass); #867 draft (client 429 UX, rebase after #860). Prod blocker was already fixed dashboard-side by #832 |
| #765 key-mint writers | **PR #874** — full writer migration, 164 tests green, NO bridge (owner: no legacy keys) |
| #849 reaper kill | PR #865 |
| #304 CLI init/team keys | **PR #875** — 4 commands, 41/41 tests |
| #337 secret audit | DONE — 0 live secrets (1 rotated FalkorDB pw in history; report on issue) |
| #390+#391 edge guards | PR #862 |
| #812 events SDK | CLOSED — already shipped on main |
| #343 client crash | PR #866 |
| #494 dep source | PR #859 |
| #826 CI timeout | PR #868 |
| #748+#749 revenue tests | PR #869 (69 tests) |
| #302 export/delete endpoints | PR #873 |
| CI flakes blocking ALL PRs | PR #872 (HF offline + crash-test determinism + lifecycle portability) |
| #855 EP NAND | **PR #871 READY** (prereq #852 merged; c1_drop ~0.001→~0.022 expected) |
| #323 capstone | G2/G3 PASS, G1 blocked on PR #803 (live-DB guards), G4 partial, G5 deferred (#316) |

**Suggested merge order:** #872 (un-green the queue) → #803 → #860 → #867 (rebased) → #859 → #862/#865/#866/#868/#869 → #871 → #873/#874/#875 → deploy flip #771 (migrations 0006-0010 in #874).

**Remaining Wave 2/3:** #308 signup CAPTCHA (after #860), #331 crash triage, #303 E2E suite, #291 capstone, #529 harness verification, #323 close after #803.
