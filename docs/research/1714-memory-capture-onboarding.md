---
title: "Per-harness agent-session + GitHub memory capture — research brief (#1714)"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-25
subjects.team: epistemic-team
aboutObjects: tortoise-memory-capture, tortoise-onboarding
---

# Research Brief — Memory Capture in Onboarding (GitHub issues/docs + agent sessions, per harness)

> **Findings date:** 2026-08-25
> Issue: #1714 — feat(onboarding): memory-capture choice in wizard + self-hosted prompt
> Scope: per-harness agent-session capture mechanics, GitHub-issue lifecycle ingestion, GitHub-docs extraction, and where/how the wizard asks.

## 1. Context & Problem Reframing

Tortoise's onboarding promises memory capture on two surfaces, but neither delivers a working mechanism today:

- **Self-hosted prompt** (`tortoise/onboarding/AGENT_ONBOARDING.md`): Q3 "Record sessions?" **only flips an onboarding-state flag** (`tortoise_onboarding_session_recording` → `_update_onboarding_state`). No capture is wired — the plan's Step 3b contract ("file each conversation end via `POST /v1/sessions`") was never implemented. Q3 is a **live false promise**.
- **Hosted wizard** (app.premiselabs.co): step 1 connects GitHub OAuth but never triggers an index job and never asks about sessions or docs. Its copy says "issues come in as Events" while the indexer writes Points (`kind="observation"`).

The capture surface (`POST /v1/sessions`, LLM two-stage extraction → episodic Points/Session/Event; fail-closed 503 without an LLM provider key) and the GitHub indexer (`tortoise/indexer/github_indexer.py`, content-hash dedup, each issue state a distinct memory) both exist. The problem is **wiring the promise to a real per-harness mechanism** and **making ingestion lifecycle-aware + entity-linked**.

**Reframe:** this is not "build session capture from scratch" — the server surface exists and Pi has a **capture extension implemented** (local write leg verified live; hosted 2xx leg unproven on this machine). It is (a) confirming the per-harness capture mechanisms, (b) wiring the Q3 promise honestly, (c) making GitHub ingestion lifecycle-aware, (d) adding the wizard ask, (e) sequencing.

## 2. Findings

### 2.1 Per-harness capture matrix (the core deliverable)

| Harness | In-session | Post-hoc | Tier | Key path / format | Confidence |
|---|---|---|---|---|---|
| **Pi** | ✅ extension events: `session_shutdown` (reason `quit`), `agent_end`, `turn_end` | ✅ session JSONL always on disk | **T1 automatic** | `~/.pi/agent/sessions/--<cwd>--/<ts>_<uuid>.jsonl` (JSONL v3 tree; header `{"type":"session","version":3,...}`); extensions in `~/.pi/agent/extensions/*.ts` | High (locally verified + bundled docs) |
| **Claude Code** | ✅ hooks: `SessionEnd` (side-effect-only, 1.5s default budget), `Stop`/`SubagentStop` (with `last_assistant_message` + `transcript_path`), `PostToolUse`; **`type:"http"` hooks POST directly to an endpoint** | ✅ `~/.claude/projects/<encoded-path>/<uuid>.jsonl` | **T1 automatic** | hooks in `~/.claude/settings.json` / project `.claude/settings.json` | High (official hooks doc fetched; transcript path shown in official `transcript_path` examples) |
| **Claude Desktop** | ⚠️ chat GUI is MCP-only (no hooks); Claude Code-in-Desktop inherits hooks | ⚠️ 3P/Cowork: `local-agent-mode-sessions/<…>/local_<uuid>.json` + `audit.jsonl` (owner-only); signed-in consumer chat is cloud-synced | **T2 (post-hoc or MCP)** | macOS `~/Library/Application Support/Claude[-3p]/local-agent-mode-sessions/` (path discrepancy community vs official) | Medium |
| **Claude Web** | ⚠️ prompt-instructed only (no local FS, no hooks) | ❌ none | **T3 (instructed)** | n/a (cloud) | High |
| **Codex CLI** | ⚠️ no first-class session-end hook documented for capture | ✅ complete self-contained transcripts | **T2 (post-hoc; near-real-time poll)** | `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl` (`{timestamp,type,payload}` envelope; `state.sqlite` index; `CODEX_HOME` override) | High (official repo + multiple docs) |
| **Cursor** | ⚠️ MCP available; no documented hook surface | ⚠️ community: `state.vscdb` SQLite (workspaceStorage/globalStorage) AND/OR `~/.cursor/projects/<slug>/agent-transcripts/*/*.jsonl` | **T2 (post-hoc, needs spike)** | see left; **no official doc found** | Low–Medium (community only) |

**The design implication:** capture is a **three-tier system, not one mechanism**:
- **Tier 1 (automatic, zero model involvement):** Pi extensions (`session_shutdown`), Claude Code hooks (`SessionEnd`/`Stop` http hooks).
- **Tier 2 (post-hoc extraction):** Codex JSONL, Cursor JSONL/SQLite (spike needed), Claude Desktop local files.
- **Tier 3 (prompt-instructed):** Claude Web — the workflows prompt instructs the agent to file via MCP/`POST /v1/sessions` at conversation end. Works on every harness; the fallback and the "minimum honest promise".

### 2.2 Pi has a capture extension implemented (the "already built" piece) — with a caveat

Verified on this machine (`~/.pi/agent/extensions/`):
- **`reflect-hook.ts`** — fires on `session_shutdown` (quit only), appends the session to `~/.tortoise/session-events/<date>.jsonl` **synchronously before** the network call (quit-teardown can't lose data), then `POST {apiUrl}/v1/sessions` to hosted Tortoise when `TORTOISE_API_KEY` is set. Logs success **only** on 2xx (#94 honest-reporting mandate). Config: env vars or `~/.pi/agent/tortoise-config.json`.
- **`tortoise-capture/`** — `agent_end` → markdown to `~/.tortoise/docs/` + ingest (FalkorDB local or hosted POST); idempotency contract: turn points keyed `{session_id}_t{i}` with MERGE, claims dedup by content-hash.

**⚠️ Caveat (research review P1):** on this machine the **local JSONL leg is live** (`~/.tortoise/session-events/2026-08-25.jsonl` has 202 real entries) but the **hosted 2xx leg is UNPROVEN** — `TORTOISE_API_KEY` is unset and `~/.pi/agent/tortoise-config.json` does not exist, so per reflect-hook's own config contract the hosted POST cannot fire. The extension's wiring conventions (write-first, honest logging, idempotency keys) are the model to generalize, but end-to-end hosted success (key → POST → Points/Session visible) must be verified in scope before claiming the path is production-proven.

### 2.3 Anti-pollution patterns for GitHub-issue ingestion

(from Tortoise `docs/ONTOLOGY.md` §2–§6 + GitHub docs; full citations in Raw Notes)

1. **External-ID upsert with never-overwrite semantics** — key nodes by system-of-record ID (`externalId`: "Slack ts, GitHub issue #"); re-materialization does not bump version (#398 never-overwrite contract). The indexer currently keys by **content hash only** (`github_url` sits in props, unused for dedup) — an edit re-creates.
2. **Content change → supersede; state change → status transition** — new statement + `CORRECTS` edge to the old (marks `outdated: true`, edges transfer), or project closed/reopened onto the issue's status field. The LME v2 supersession machinery (E5 #1537 / E6 #1538 — `supersede()`, `tests/test_lme_ingest_v2_supersession.py`) is the existing model to apply.
3. **Events as truth, status as projection** — consume the GitHub `issues` webhook (actions `opened|edited|closed|reopened|transferred|deleted`) or diff `state`+`updated_at` on poll; append transition events, derive current state at query time.

**Current indexer gaps (verified):** content-hash dedup only; edited/closed/reopened issues mint NEW Points ("each state is a distinct memory" — the exact pollution the issue targets); no entity edges to repo/project/subject; `_MAX_ITEMS_PER_RUN = 500`; no webhook path (poll-only).

### 2.4 GitHub-docs extraction

No remote-docs path exists. `tortoise_ingest_corpus` is **local-directory, stdio-only** (hosted excludes directory access; prompt documents the honest skip). Options: (a) GitHub Contents API walk of `docs/` folders into the existing corpus pipeline, (b) local clone + existing corpus ingest, (c) webhook-triggered re-ingest on doc pushes. File indexer + `compute_file_hash` + `derive_session_id` already exist for dedup.

### 2.5 Hosted ↔ self-hosted parity (verified)

GitHub connect/index = hosted-only (stdio returns "No team context (HTTP mode required)"; prompt handles gracefully). Corpus ingest + transcript mining = stdio-only. Session capture surface = hosted HTTP (works from any harness with the key; local FalkorDB path exists for self-hosted). The wizard must present parity honestly per source.

## 3. Options

**A. Capture tiers as the product shape** (recommended): the wizard/prompt offers session capture once; the mechanism is chosen by harness tier (T1 automatic install step in the harness copy, T2 a one-command extractor, T3 prompt-instructed only). Honest per-harness note in the copy ("capture coverage depends on the harness" — plan Step 3b's original intent).

**B. In-session-only (prompt-instructed everywhere):** lowest effort, works for all harnesses via the workflows prompt, but no automatic coverage for Pi/Claude Code and no post-hoc backfill. Weakens the promise.

**C. In-session + post-hoc extractor CLI** (recommended scope for the issue): `tortoise sessions import --harness codex|cursor|claude-code|pi|desktop` mirroring the `tortoise mine-conversation` pattern (which already exists for transcripts, #1198), plus the T1 hooks/extensions install in the harness copy.

**D. Wizard UX for the ask:** step 1 ("Integrations") becomes "Memory capture" with three opt-in yes/no toggles — GitHub issues (existing connect + new index trigger + lifecycle), GitHub docs (new), Agent sessions (new; per-harness note). Answers persist to `/v1/onboarding/state` (existing pattern). Alternatively a single "capture everything" master toggle with per-source detail.

## 4. Risks & Pitfalls

- **False promise regression:** if the wizard asks about sessions before a real mechanism is installed per harness, Q3's false-promise failure repeats on the hosted surface. The ask must be gated on the tier actually delivered for the chosen harness.
- **Pi hosted capture leg unproven:** reflect-hook's local write is live, but no 2xx POST has been observed on this machine (no API key configured). Verify end-to-end (key → POST → Points) before generalizing the wiring to other harnesses.
- **Quit-teardown data loss:** Pi's reflect-hook solved this with a synchronous local JSONL write before the network call; any T1 hook must follow the same write-first contract (Claude Code `Stop` docs warn the transcript file lags — use `last_assistant_message` / fire on `SessionEnd`, not a transcript read).
- **LLM-provider gating:** `POST /v1/sessions` fails closed (503) without an LLM provider key — capture "enabled" must surface this honestly rather than silently dropping.
- **Cursor uncertainty:** no official storage doc; the parser needs an install-and-inspect spike before committing (scope as a research task inside the issue).
- **Pollution via webhooks:** webhook ingestion must be idempotent (delivery retries) and lifecycle-aware or it recreates the duplicate-issue problem with more velocity.
- **Parity overpromise:** docs extraction + transcript mining are stdio-only today; the hosted wizard must either enable a hosted path or state the honest difference (per the prompt's existing pattern).

## 5. Recommendations

1. **Tiered capture is the architecture:** T1 automatic (Pi extension, Claude Code hooks), T2 post-hoc extractor (Codex, Cursor-after-spike, Claude Desktop), T3 prompt-instructed (Claude Web). The wizard ask happens once; the harness copy installs the tier's mechanism.
2. **Wire the promise before asking:** ship the T1 install + T3 instructed tier first (covers every harness with an honest mechanism), then T2 post-hoc as an extractor CLI. Do NOT add the wizard question until the tier for the selected harness is real.
3. **GitHub ingestion goes lifecycle-aware:** external-id keying + `supersede()` on content change (reuse LME machinery) + status projection for closed/reopened + entity edges to repo/project/subject. Add idempotent webhook or diff-on-poll. Correct the wizard copy ("issues → Points with lifecycle", not "Events").
4. **GitHub docs:** Contents-API walk into the existing corpus pipeline (hosted-eligible) or local-clone + `tortoise_ingest_corpus` (self-hosted); webhook re-ingest deferred.
5. **Wizard UX:** expand step 1 to the three opt-ins, honest per-harness parity notes, answers to `/v1/onboarding/state`, later opt-in from the dashboard (post-launch).

## Raw Notes

- 2026-08-25 — Sub-agent fact sheet (web research + local inspection). Sources: pi bundled docs (`docs/session-format.md`, `docs/extensions.md`, `docs/sdk.md`, `docs/rpc.md` — local under `node_modules/@earendil-works/pi-coding-agent/docs/`); Claude Code hooks official: https://code.claude.com/docs/en/hooks (fetched) — events incl. SessionStart/SessionEnd/Stop/SubagentStop, handler types incl. `http`, Stop has `last_assistant_message` + `transcript_path`; Claude Desktop 3P data-storage official: https://claude.com/docs/third-party/claude-desktop/data-storage (fetched) — `local-agent-mode-sessions/` + `audit.jsonl` under `~/Library/Application Support/Claude-3p/` (community reports `Claude/` for consumer); Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` + `state.sqlite`, `CODEX_HOME` (codex.danielvaughan.com, verdent.ai, ccusage.com, developers.openai.com/codex/cli); Cursor: community-only — forum.cursor.com threads 7653/2825/77295, prismmd.app, zhiqiangzhang.cursor-chat-viewer (reads `~/.cursor/projects/*/agent-transcripts/*/*.jsonl`); `cass` cross-harness session indexer: github.com/Dicklesworthstone/coding_agent_session_search; GitHub webhook actions: https://docs.github.com/en/webhooks/webhook-events-and-payloads#issues.
- 2026-08-25 — Local verification: `~/.pi/agent/extensions/reflect-hook.ts` (session_shutdown → sync JSONL to `~/.tortoise/session-events/<date>.jsonl` then `POST /v1/sessions`, honest 2xx logging, `~/.pi/agent/tortoise-config.json` config) and `~/.pi/agent/extensions/tortoise-capture/` (agent_end → markdown + ingest, `{session_id}_t{i}` idempotency) exist and run; `~/.tortoise/session-events/2026-08-25.jsonl` present.
- 2026-08-25 — Codebase verification (tortoise): `AGENT_ONBOARDING.md` Q3 flag-only; `github_indexer.py` content-hash dedup + state-distinct-memory + no entity links + `_MAX_ITEMS_PER_RUN=500`; `POST /v1/sessions` LLM-gated; LME supersession tests `tests/test_lme_ingest_v2_supersession.py`; wizard step 1 connect-only (`/v1/onboarding/github/connect` + status), copy "issues → Events" vs Points (verified in built bundle `website/apps/dashboard/dist/assets/index-DKOFbvG4.js`: "issues come in as Events (optional, do it now or later)").
- 2026-08-25 — Behavioral/memory-source citations (problem-verify cycle-2 fix; name-level claims in 1714-problem-diverge.md/1714-problem-challenge.md now verifiable): Windows Recall privacy: https://www.bbc.com/news/articles/c62721z21e7v (and Microsoft Recall opt-in+Windows Hello coverage: computerworld.com, arstechnica.com); onboarding-friction: Chameleon (chameleon.io/blog/onboarding-best-practices), Orizon (orizon.co), Taqwah 44.7% form-abandonment study (taqwah.com); observability defaults: LangSmith traces-by-default (docs.smith.langchain.com), Langfuse sampling default (langfuse.com/docs); hook best-effort limits: https://code.claude.com/docs/en/hooks + community (dev.to boucle2026, HN 47895029); agent-memory noise: Kinney (kinney.dev), arXiv 2603.07670 / 2606.06448 / A-MEM arXiv 2502.12110; LLM extraction cost: morphllm 2026 session-cost analysis (morphllm.io/blog), premai/honeycomb guidance (premai.io); memory-as-product failures: TechCrunch 2026-06-10 (techcrunch.com), Lektik (lektik.com), DevRev (devrev.ai); privacy consent guidance: ReedSmith (reedsmith.com) + UC Davis privacy guidance (ucdavis.edu/privacy).
