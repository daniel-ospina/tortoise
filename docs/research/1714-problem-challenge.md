---
title: "#1714 devil's-advocate challenge report"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-25
subjects.team: epistemic-team
aboutObjects: tortoise-memory-capture, tortoise-onboarding
---

# Problem-Diverge — Agent B Challenge Report (issue #1714)

**Source:** worktree `feat/1714-memory-capture`. External research: LangSmith/Langfuse docs, Claude Code hooks docs + community, Windows Recall coverage, onboarding-friction studies (Chameleon, Orizon, Taqwah), agent-memory surveys (Kinney, arXiv 2603.07670 / 2606.06448 / A-MEM 2502.12110).

## 1. REVERSE THE PROBLEM

### 1a. What if asking in the wizard at all is wrong?
- **The ask is friction with zero activation value.** Self-hosted prompt is already 6 questions + Q5b. External data: wizards >5 steps lose >50% completion (Chameleon); engagement drops at 7 items, abandonment at 12 (Orizon); cutting a signup form 7→3 fields cut abandonment 44.7% (Taqwah). "Do you want session recording?" at minute one is **consent theater** — the user can't evaluate value, cost, or trust surface.
- **The ask is the thing that already failed.** Q3's false promise shows the ask creates commitments the product can't honor. And a hosted wizard CANNOT install a Pi extension or Claude Code hook into the user's local environment (no FS access from the hosted surface) — so even with a real mechanism existing, the wizard "yes" flips a flag and the DELIVERY requires the user to separately edit `~/.claude/settings.json` or install an extension. The false promise survives the gate.
- **Industry does the opposite.** LangSmith logs all traces by default; Langfuse default sampling = 1 (100%); sampling is a back-end policy, never a per-source consent dialog. The observability answer: "all of them; filter at query time."
- **Privacy says opt-in, but not via a wizard toggle.** Windows Recall shipped default-on → "privacy nightmare" (BBC) → forced to opt-in + Windows Hello + just-in-time decryption. Durable pattern: **off by default, visible recording indicator when on, one-click off, local-first storage, enable moment after first value** — not a consent checkbox at onboarding.
- **What the reversal surfaces:** the wizard should ask *nothing* about sessions; it should (a) auto-index GitHub post-connect (decided), (b) show a "Memory sources" surface *after* first value where sessions/docs can be enabled with transparency, (c) have the SELF-HOSTED prompt — which runs in the user's environment and CAN write hook configs — be the mechanism-installer. Gate on mechanism *delivery*, not just existence.

### 1b. What if per-harness is the wrong axis?
- **The graph can't see the harness.** The `Session` node carries `id, created_at, turn_count, is_episodic` — **no harness/agent field**. Capture is namespaced per team. The issue's verification ("per-harness capture verified") could pass while the promise is unobservable in the data.
- **Per-harness is an infinite regress.** New harnesses appear continuously (Gemini CLI, Warp, Aider, Cline). The brief's matrix shows the shape: T1 (Pi, Claude hooks), T2 (Codex, Cursor-spike, Desktop), **T3 (prompt-instructed — works on every harness)**. **T3 is the promise; T1/T2 are acceleration.** The issue inverts this by making per-harness the headline.
- **Alternative axes:** per-session opt-in (the T3 workflow-prompt pattern — user decides at conversation end with full context) or per-team (data lands in a team graph; recording is team policy). The wizard-level blanket yes is the weakest information position.
- **Recommendation:** promise the universal tier (T3), deliver per-harness mechanics as progressive enhancement, add a **harness field on Session** so the promise is auditable.

### 1c. What if GitHub ingestion should NOT be lifecycle-superseded (as specified)?
- **Ontology §2:** "Status is derived, not stored… events are the truth, status is a read-only projection." A closed/reopened issue is a **state change → Event** (`connectors/github.py` already emits `eventKind: github.issue.<state>`) + projected status on the WorkItem Object. It must **never** supersede the content point: the claim "issue #42 proposes X" was true while open; closing it does not falsify the claim.
- **Ontology §3.1 defines CORRECTS narrowly:** "New point corrects/replaces an outdated point… marks target outdated:true" — the *belief-correction* primitive, right for LME v2 (fact changed), NOT for a world-state change (issue closed). Edits → bi-temporal validity window (§4.1 validFrom/validTo; E6 stamps validTo on supersede — "old fact stops being true exactly when new fact became true"). Terminal statuses excluded from default query surfaces (§5 tombstone contract) — naive supersede = **amnesia**, not memory.
- **"Each state is a distinct memory" is not pollution per the ontology.** The graph is state-centric memory (§2). The actual pollution: (a) *unkeyed* duplicates (externalId upsert never used; github_url in props), (b) *unlinked* islands (no entity edges). The issue targets the wrong disease.
- **Wizard-copy target is a half-truth in the other direction:** correcting "Events" → "Points with lifecycle" lands on the other wrong horn. The connector model: issues are **WorkItem Objects + lifecycle Events + Sources**, with statement Points *about* the issue content. Copy should say "issues become work items with a lifecycle record, plus claims extracted from their content."
- **Orphaned-connector finding (strongest internal discovery):** `tortoise/connectors/github.py` ALREADY implements the ontology-correct path — poll + webhook (`start_webhook`), `_issue_to_entities` (sourceObjectId), `_issue_to_event` (`eventKind: github.issue.<state>`), `ingest(proj)` through `_upsert_event` (Source→references→Event + Source→references→Object, `projection/entities.py`) — with tests (`tests/test_github_connector.py`). Reachable ONLY via `pipeline_cli.py`; `/v1/index/github` uses the crude content-hash indexer writing `kind="observation"` — a **REMOVED legacy kind** (ontology §5: "observation is removed"). Scope must answer: **why extend the indexer instead of wiring/repairing the connector path that already does events + entities + sources?**

## 2. PRE-MORTEM — "We shipped #1714 and it failed. Why?"

### Scenario A (Technical): The quota bomb + unprovable capture leg
GitHub index path has **no quota gate** (`_run_indexing` → `index_issues` → `create_point`; zero `count_team_usage` calls, unlike /v1/sessions). Default max_points=1000; `_MAX_ITEMS_PER_RUN=500` is **per repo** — a 20-repo org writes up to 10,000 Points on onboarding day → default cap blown by auto-index-after-connect → every subsequent /v1/sessions capture fails 402 — the feature the wizard just promised is dead on arrival. Separately: hosted capture leg unproven (P1); extraction 503s without provider key; wizard can't detect either condition.

### Scenario B (Product): The graph becomes noise, not memory
All three sources dump into one team graph with no importance ranking: every closed issue, every "ok" turn, every doc. #329 flood-gate history is the in-repo cautionary tale; v2 extractor still estimates 3× points per turn. External: raw accumulation without memory management produces noisy retrieval (Kinney; arXiv 2606.06448; 2603.07670). The "non-polluting" indicator measures duplicates; the real failure is **relevance**. Churn: connect → index → say yes → see noise → disable → churn.

### Scenario C (Operational): Lifecycle-awareness dead without a re-poll trigger
Hosted path is poll-only; `start_webhook` unwired; `_INDEX_JOBS` in-process, evicted after 1h, no scheduler. "Re-index after an edit" requires someone to re-run the job; nothing does. E2E target passes while production goes stale. Plus: org-wide initial index can burn GitHub 5000/hr rate limit during onboarding; per-harness promise unverifiable (no harness field); multi-team users can't choose which graph receives a session.

## 3. HIDDEN DEPENDENCIES

| # | Dependency | Status | Why it matters |
|---|---|---|---|
| 1 | Epic #909 extraction pipeline | OPEN | /v1/sessions LLM two-stage extraction, commit endpoint are #909 slices; #1714 wires an in-flight epic's contract |
| 2 | Server + client LLM keys | Unverifiable by wizard | Extraction 503s without server key; reflect-hook won't POST without user TORTOISE_API_KEY |
| 3 | Hosted capture leg proven | **Unproven** (P1) | T1 automatic story generalizes from a reflect-hook whose hosted POST never observed 2xx |
| 4 | LME supersession machinery | Exists, statement-scoped | Reuse requires handling observation-kind points + externalId keying the indexer lacks |
| 5 | #388 connector projection path | **Exists, orphaned** | connectors/github.py + projection/entities.py already do events/entities/sources — never named in the issue |
| 6 | Onboarding state schema | Limited fields | No github_docs, no per-harness fields — new asks need schema additions |
| 7 | Session data model | No harness/agent field | "Per-harness capture verified" unobservable in the data |
| 8 | GitHub docs extraction | Net-new | corpus ingest stdio-only; hosted remote-docs path doesn't exist |
| 9 | Quota asymmetry | Index ungated · Sessions 402-gated | Latent production bug (Scenario A) |
| 10 | Dashboard source | Wizard copy in src/main.jsx (monolith) + rebuilt dist | Editing wizard = edit src + rebuild dist |

## 4. COUNTER-EVIDENCE — memory capture tried differently

1. **Ask-at-onboarding vs capture-all-at-infra.** LangSmith logs all traces by default; Langfuse sample_rate=1; sampling is back-end policy, never a per-source setup dialog. Wizard per-source opt-in toggles are the opposite design and land in the onboarding drop-off band.
2. **Always-on capture privacy — Windows Recall.** Default-on → privacy nightmare → forced opt-in + Windows Hello + just-in-time decryption. Durable resolution: harness/OS-level opt-in with visible indicator + local control, not a setup-dialog consent toggle without transparency.
3. **Agent-memory failure modes.** Raw transcript accumulation without memory management (consolidation, forgetting, retrieval-time filtering) produces noise and contradictions; "noisy stored memory produces noisy retrieval" (Kinney; arXiv 2603.07670; 2606.06448; A-MEM 2502.12110). Tortoise's #329 flood gate is the in-repo instance. Success metric (points created, 0 duplicates) measures the wrong thing.
4. **Hook-based capture is best-effort, not a guarantee.** Claude Code: Stop hooks don't fire on user interrupts or in VSCode; can be overridden after 8 blocks; SessionEnd is side-effect-only with 1.5s default budget; hooks silently break on settings changes/auto-update. Sessions ending via interrupt/IDE-close are exactly when memory matters most. Write-first contract mitigates but doesn't eliminate systematic under-capture.
5. **In-repo counter-example: LME supersession is bi-temporal, not destructive.** test_lme_ingest_v2_supersession.py models supersede for changed *facts*; E6 stamps validTo; the past stays queryable. Applying CORRECTS to a *closed* issue marks content "outdated" that was never wrong — violating §2/§3.1.

## Bottom line
The issue's problem statement is **correct about the symptoms** (false promises, unkeyed duplicates, no entity links) but **wrong about three remedies**: (1) the wizard ask should not be a yes/no toggle at onboarding — capture should be off-by-default, visible, locally controlled, enabled after first value, with the self-hosted prompt as mechanism-installer; (2) "per-harness" is a mechanism taxonomy leaking into the product promise — promise the universal T3 tier, deliver T1/T2 as progressive enhancement, add a harness field to Session; (3) lifecycle ingestion must be **Events-as-truth + bi-temporal statements + WorkItem Objects** (the orphaned connectors/github.py path already does most of this) — not supersede-on-everything. The highest-risk unverified assumption is the **quota asymmetry** between the ungated GitHub index and the gated session capture — that alone can kill the feature on day one.
