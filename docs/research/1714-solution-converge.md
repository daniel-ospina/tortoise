---
title: "#1714 solution-converge — FINAL plan"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-25
subjects.team: epistemic-team
aboutObjects: tortoise-memory-capture, tortoise-onboarding
---

# Solution-Converge — FINAL Plan (issue #1714)
> **Read in full before decomposition** — includes the 4 slices, the Phase 7 Review Incorporations section (which amends specific slices), and this header note. The consent-gate, fetch-order, revert, prop-contract, and webhook-eventId fixes ARE propagated into the slice bodies below; the Phase 7 section adds the remaining P2/P3 items.

## Chosen Approach

**Family 3 — Split-the-indexer with a shared stateless `tortoise/github_map.py`**, reworking `github_indexer.py` in place into a two-phase fetch+diff → project pipeline. All ontology mapping (entities/events/statements/lifecycle) lives in ONE shared module imported by BOTH the indexer (hosted) and the orphaned connector (self-hosted/pipeline_cli). Statement Points via explicit-id SDK writes (`create_point(kind="statement", id=…)`, `aboutObject`→WorkItem, `extractedFrom`→Source). Capture verification: server receipt primary for T1/T3, client write-first for T2, disclosure-only until receipt. 4 slices, each independently shippable; **T2 (import CLI + Codex/Desktop parsers) lands INSIDE Slice 2 after T1+T3** (per user staging — not deferred to a later slice; issue Target 4 requires Codex verified in-slice).

**Controller rationale (corrected):** Family 3 fits amendment 3 literally (indexer's proven httpx fetch stays the fetch layer; connector semantics become the shared mapper), retires nothing (pinned test surface survives — the FakeSDK mask is rewritten regardless), and resolves the #1155 eventKind divergence at the mapping layer (the deliberate normalization the #1155 note itself requested). It uses TWO write paths (`proj.apply` for entities/events, `sdk.create_point` for statements) — the genuine differentiators from Family 1 are module churn, fetch location, and test surface, NOT write-path count. `sdk._get_proj()` exists (sdk.py:1102, namespace-scoped, used by hosted_api at :409/:956) — the Family-1 TeamProjectionAdapter objection is narrowed to its real costs (relocating REST fetch into the orphaned connector + retiring the indexer module). Family 2's unified package is the better long-term shape ONLY if Linear/Slack parity becomes real — wrong investment under a moving #909 contract today.

**Rejected:** Family 1 (connector-resurrection: real costs are fetch relocation + module retirement + a write vocabulary the control plane doesn't use today). Family 2 (unified package: largest churn, retires two green pinned modules, unproven reuse, wrong under moving #909). Webhook ingestion (deleted/transferred documented as webhook-only gap). Supersede-on-everything (never — ontology amnesia). "Just dedup=True" (never — leaves removed kind + quota bomb).

---

## Slice 0 — GitHub ingestion baseline repair (root; everything gates on it)

**Files touched:**
- NEW `tortoise/github_map.py` — stateless pure mapper: `issue_to_object` (pm:issue WorkItem Object + routing props), `issue_to_event` (SINGLE vocabulary: eventKind `github.issue.{state-or-action}` (value set: open | closed | reopened), subject `issue:{repo}#{n}`, eventId `github-issue-{repo}-{n}-{event}` with event ∈ {created, closed, reopened} — creation KEEPS `-created` (byte-identical to today's pinned ids), state transitions mint distinct ids (`-closed`, `-reopened`); the #1155 normalization changes eventKind only, never the eventId scheme), `issue_to_subjects` (authors as Subject nodes + `aboutSubject` edges — absorbed from entity path's `github-user:*` event subjects), `pr_to_event` (`github_pr` sourceKind), `issue_to_statements` / `pr_to_statements` (amend 4: externalId `github:issue:{repo}#{n}`, deterministic id `pt_gh_{repo}_{n}_{sha256(content)[:12]}_{v}` with a per-issue MONOTONIC version suffix `v` (edit → v+1 mints a new id; **revert-to-prior-content increments v again — never reuses a terminal id**, so the edit→supersede→revert cycle stays current-truth correct and never collides with a superseded point; the current-statement lookup is `externalId` + `status != terminal`, NOT content-hash dedup); `aboutObject`→WorkItem, `extractedFrom`→Source), `diff_lifecycle(prev, cur)` (closed/reopened detection from `updated_at` cursor + state field). Migration note: existing `-created` Events in self-hosted connector graphs collide safely on MERGE (no dup) — falsification (a) holds on the first post-change run. Legacy no-suffix poll-path Events (`github-issue-{repo}-{n}`, test_github_connector.py:374-375) are left in place, never re-written. The :419 pin tuple becomes `("github-issue-test/repo-42-created", "github.issue.open")` (eventId unchanged, eventKind normalized).
- `tortoise/indexer/github_indexer.py` — reworked in place: Phase 1 = existing httpx fetch (`:23-66` rate-limit backoff/pagination) **pinned to `sort=updated&direction=desc`** (cursor-correct AND stoppable — created-desc would blind the diff beyond the window at org scale), parameterized per-run cap (cost control, not correctness), **"N issues beyond window" surfaced in job status** (honest truncation) + persisted per-repo `updated_at` diff cursor (cursor home = onboarding jsonb, no migration); Phase 2 = entity chain + lifecycle Event via `proj.apply()` (`proj = sdk._get_proj()`, sdk.py:1102 — NO TeamProjectionAdapter) + statements via `create_point(kind="statement", id=…, extractedFrom=…, externalId=…, dedup=True)` + `(p)-[:aboutObject]->(o:Object)` link. Writes `statement` ONLY (never `observation`). **Lifecycle decision table (THE rule):**
  - **Closed/reopened/state change ON TRANSITION → Event (`github.issue.closed`/`reopened`) + `Object.status` projection ONLY** (first-time ingestion of an already-closed issue mints ONLY `-created` with kind `github.issue.closed`, preserving the pinned assert at test_github_connector.py:30-31; the `-closed` backfill applies ONLY to pre-existing legacy self-hosted graphs, never new first-runs — no double-mint). Statement points are content/status-UNTOUCHED (metadata `updatedAt` bumps permitted per the two-phase write in P2-1); **props contract pinned: `{externalId, extractedFrom, source, github_repo, github_number, github_url}` ONLY — never `github_state` or any state-derived prop** (state lives exclusively on `Object.status`; `github_state IS NULL` asserted in the mapper test). `invalidate_point` is NEVER called in this pipeline** (it always writes CORRECTS, sdk.py:2682 — the amnesia defect). TDD asserts "no content/status mutation on close."
  - **Content edit → new statement + `supersede_point` (bi-temporal validFrom/validTo, edge transfer)** — legitimate per ONTOLOGY §3.1 (belief correction of the point-as-stated; old snapshot queryable within its validity window).
  - **Reopened-then-edited → supersede (same as edit).** True-while-open status changes never falsify content points.
  - Per-batch quota re-check (`enforce_team_limit("points")` resolved before first write, re-checked per batch).
- `tortoise/connectors/github.py` — mappers become thin wrappers over `github_map` (poll-path eventIds byte-identical; the #1155 divergence note at :231-242 deleted). **pm:card\* blast radius enumerated (named verification item):** grep `pm:cardCreated|pm:cardCompleted` across org repos (esp. operations/coordinator outside this repo); update `config/pipelines.yaml:17-18,47-48` + `graph-scripts/setup.py:932-933` kinds; external-consumer re-pin stated in the migration note. gh-CLI fetch stays for pipeline_cli (self-hosted stdio-only difference, documented). Event subject normalization (`github-user:*` → `aboutSubject` edges) greps for downstream consumers (operations/coordinator outside repo) and is called out in the migration note.
- `tortoise/hosted_api.py` — `_run_indexing` (:8116-8155) quota-preflighted + drives the reworked indexer; **first-run volume BOUNDED (pre-decided fallback, not implementer choice): default first-run scope = ONE repo regardless of org size** with an honest "index more" affordance + per-run points cap stopping at team `max_points` headroom with partial-completion status; the deliver-or-defer decision covers ONLY the reference-org's actual worst-case numbers (amend-7/16 pattern, named owner); connect callback (:8027-8088) fires auto-index-after-connect (amend 11, quota-gated; org from `_GITHUB_STATES`); re-poll endpoint (diff-on-poll re-run, amend 6).
- `graph-scripts/1714_dedup_observation.py` (amend 7/16 — named owner, deliver-or-defer: default **leave-as-is** for live graphs, opt-in best-effort merge script for teams that want dedup; the decision is RECORDED, not silent).

**TDD (real embedded SDK, FakeSDK deleted):**
1. `test_github_map.py` — pure mapper: single eventId/vocabulary, externalId, aboutObject target, extractedFrom, lifecycle diff, PR.
2. REWRITE `tests/test_github_indexer.py` (real SDK + `_wipe_or`): red = unkeyed `observation` duplicates on re-run; green = re-run ⇒ 0 new nodes; edit ⇒ supersede + CORRECTS; close ⇒ Event `github.issue.closed` + `Object.status=completed`, **NO point mutation** (explicit assertion); reopen ⇒ status back to `open`, no CORRECTS.
3. `test_github_index_lifecycle.py` — quota honest-fail at cap, auto-index-after-connect, re-poll.
4. EXTEND `test_github_connector.py::test_producers_share_event_id` — eventIds unchanged (creation stays `-created`); **deliberate eventKind re-pin at :419** (`("github-issue-test/repo-42-created", "pm:cardCreated")` → `("…-created", "github.issue.open")`, the #1155 normalization — named, reviewed, not hidden); `test_connector_sources.py:445` survives (verified — eventKind incidental there); ADD a mapper unit test fed **gh-CLI-shaped input** (field-name/casing adaptation lives in the thin wrappers and must be pinned, not silent).

**Acceptance:** falsification (a) re-run ⇒ 0 new nodes; (b) no `observation` writes; (c) lifecycle ⇒ Event + status, never CORRECTS on close; (d) quota preflight + per-batch; #1155 one vocabulary pinned; auto-index + re-poll live.

**Boundaries:** no webhook consumer (`deleted`/`transferred` documented as webhook-only gap in AGENT_ONBOARDING.md parity table); no metering changes beyond the gate; `supersede()` internals untouched; self-hosted CLI path untouched.

---

## Slice 1 — Remote GitHub-docs extraction

**Files touched:**
- NEW `tortoise/indexer/github_docs.py` — Contents-API walk reusing the indexer httpx pattern (recursion cap, incremental tree-by-sha, token scope `repo`). Fetch → **server-side staging under `TORTOISE_INGEST_BASE_DIR`** → internal `ingest_corpus` **function** (NOT the `tortoise_ingest_corpus` tool — stays `http_policy=False`).
- `tortoise/hosted_api.py` — `POST /v1/index/docs` + job mirroring `/v1/index/github` (team-scoped `_INDEX_JOBS` isolation copied); sets `github_docs_indexed`.
- `tortoise/quota.py` — extend `_count_resource` with a `documents` resource (`:Document` count) — the points gate is VACUOUS for docs (ingest_corpus creates Document/Event nodes, not Points). **Gate scope pinned (cycle-3 P2):** the `documents` gate fires on `/v1/index/docs` ONLY — session transcripts also MERGE `:Document` nodes (hosted_api.py:4456), so the count is endpoint-scoped (or transcript-counting is a deliberate documented decision), never an unpinned tenant-global surprise.
- Self-hosted: existing `tortoise index github <url>` clone path + `tortoise index directory`; honest stdio note in Q5 copy.

**TDD:**
1. `test_docs_fetcher.py` — walk via mock transport, staging under base, hash dedup (`compute_file_hash`); unchanged re-ingest ⇒ 0 new nodes (falsification (f)).
2. `test_index_docs_api.py` — job poll, **402 at Document cap (points gate would NOT fire)** — the gate is real, not vacuous; `github_docs_indexed` state key.
3. **Unset-base + escape-path fail-closed**: job fails honestly (no writes) when `TORTOISE_INGEST_BASE_DIR` is unset or the staged path escapes it (`ingest_dir_is_safe` accepts any absolute path when unset — security.py:211 — but `/v1/index/docs` IS tenant-reachable, so fail-closed is mandatory).

**Acceptance:** falsification (f); job poll; staging containment; #236 user-supplied-path exclusion intact for user paths.

**Boundaries:** webhook re-ingest deferred; `repo` scope only; no new metering beyond the documents gate.

---

## Slice 2 — Session capture wiring (T1+T3 first, T2 staged — per user)

**Files touched:**
- `tortoise/hosted_api.py` — `SessionRequest.harness` real field (not metadata); Session MERGE (:4062-4066) `SET s.harness`; **entity-linking pass after capture (amend 13, FIXED):** link Session node + extracted episodic Points to subject/project entities — `(s:Session)-[:aboutObject]->(o:Object)` and `aboutObject` on extracted points, resolved deterministically (uses the REGISTERED about-edge family: `aboutObject`/`aboutSubject` — `:ABOUT` is not a registered type and is not used; **ONTOLOGY.md edge table extended to add Session as an `aboutObject` source** (currently Point/Document/Event → Object; Session → Object is a one-line registration, in Slice 2); **resolution TRIGGER rule (pinned):** regex over conversation text for `github.com/{org}/{repo}/issues/{n}` and `{repo}#{n}` (bare `#n` only with a false-positive guard — first-match per extracted point, all-matches for the Session node; no-match ⇒ no link, honest); jsonb onboarding keys (no migration, registered in BOTH live defaults hosted_api.py:1850/:7552 + `_ALLOWED_STATE_KEYS` + PATCH model): **consent = the ENFORCED `session_recording` flag (one team-level boolean — the data plane reads it; `capture_opt_in` as a separate key is DROPPED)**, plus `capture_ask_shown`, `capture_revised` (exactly-once re-ask), and per-harness receipts `session_capture_receipt_{harness}` (**set only on hosted 2xx**). Legacy migration: existing `session_recording=True` teams are GRANDFATHERED as consented (re-ask still offers opt-out); Slice 2 ships the consent-set surface (the existing onboarding PATCH endpoint) so the 403 gate is not dead-on-arrival before Slice 3.
- `tortoise/sdk.py` — Session MERGE (:1973) `harness` in sync (contractual).
- `docs/ONTOLOGY.md` — edge table: register Session as an `aboutObject` source (Point/Document/Event → Object becomes Point/Document/Event/Session → Object).
- NEW MCP tool `tortoise_session_capture(conversation, harness)`: registered in `tool_registry.py` (explicit `http_policy`), wraps POST /v1/sessions with the same gates PLUS the **consent 403** (un-opted team → 403 — the enforced flag IS the consent; the MCP tool carries the identical check); `mcp_server.py` handler. stdio behavior pinned: honest "session capture requires hosted mode" error (no local fallback that bypasses the gates). This gives Claude Web (and every harness) an executable filing surface via the workflows prompt.
- `tortoise/__main__.py` — `session capture --harness <tier>`; NEW `tortoise sessions import --harness codex|claude-desktop|pi` (stages the parsed session artifact locally for data preservation, then POSTs; the RECEIPT marker is written ONLY on hosted 2xx — a 403/402/503 leaves no receipt and fails the import with an honest error); `tortoise/session_import/` parsers (codex.py high-confidence JSONL; claude_desktop.py; cursor.py gated on spike verdict — ships or honest `unsupported`).
- T1 assets wired into harness copy: Pi extension copy-install (outside repo — instructions in harnesses.js HARNESS_INSTALL); in-repo `tortoise/claude-hooks/session-end.sh` + `session-start.sh` (cp + `.claude/settings.json` SessionEnd/SessionStart entries, always exit 0, pass `harness` through `_cmd_session_capture` payload).
- T3 workflows prompt — **DRAFTED COPY** (P1-3): the claude-web prompt (harnesses.js:62) gains a 4th workflow paragraph: "4) Session filing — only if your team has enabled session capture: at the end of a conversation, call `tortoise_session_capture(conversation=<this conversation>, harness='claude-web')` to file it. Capture only runs when you call it; nothing is recorded otherwise. If the call fails (not enabled, quota, or provider limits), tell me it wasn't filed and don't retry." Disclosure-until-receipt: the wizard/dashboard shows capture as "active" only after `session_capture_receipt` is observed.
- **Claude-Web filing path = named spike/verify item with a MANDATORY executable fallback (P2-2 FIXED):** verify the claude.ai custom connector can invoke `tortoise_session_capture` (MCP) OR the workflows-prompt agent can POST `/v1/sessions` directly via the connector's native HTTP tool. **Disclosure-only is NOT an acceptable terminal state for the universal tier** — the ask only presents session capture for Claude Web once at least one executable filing path is confirmed; if neither path works, the session toggle is hidden for web with honest copy (not shown as available).

**TDD:**
1. EXTEND `test_capture_session.py` — harness persisted (real SDK); receipt set ONLY on 2xx; **entity-linking assertions**: Session `aboutObject` link + `aboutObject` on extracted episodic points (amend 13).
2. EXTEND `test_onboarding_endpoints.py` — jsonb keys round-trip (Supabase + registry modes).
3. `test_session_import_codex.py` + `test_session_import_desktop.py` — fixtures, idempotent re-import.
4. MCP tool: `tortoise_session_capture` registered + invokeable with `TORTOISE_SESSION_LLM_MOCK=1`, gates honored.
5. **Consent gate TDD:** un-opted team POST /v1/sessions → 403 AND MCP tool → 403; opted-in → 200; `tortoise sessions import` on 403 fails with no local receipt + honest error.
6. Cursor spike (research task inside slice; verdict recorded).
7. Pi E2E: hosted 2xx leg observed end-to-end (configure `TORTOISE_API_KEY` + `tortoise-config.json` — the brief §2.2 P1 caveat is closed IN this slice, not assumed).
8. **Claude Code hook smoke (P3-A FIXED):** session-end.sh exit-0 under failure + mocked POST producing a Session + `session_capture_receipt`.

**Acceptance:** Session.harness persisted; receipt 2xx-only; entity links present (amend 13); hooks exit-0; Codex/Desktop idempotent; spike verdict recorded; T3 has a working filing tool + drafted prompt.

**Boundaries:** #909 black box (wire-level contract handling only); T2 never bypasses the **403 consent gate** or the 402/503 gates; no new metering.

---

## Slice 3 — Honest ask (wizard + prompt + re-ask + later opt-in)

**Files touched:**
- `website/apps/dashboard/src/main.jsx` — step-1 → "Memory sources": three opt-in toggles (GitHub issues / GitHub docs / agent sessions) + auto-index surfacing; **misled-user re-ask pane** (gate: the enforced consent flag `session_recording=True` && !`capture_revised`, exactly once via `capture_ask_shown`; per-harness enablement derived from `HARNESS_CAPTURE_SUPPORT` + spike verdict + per-harness receipts — NOT a separate consent key); **later-opt-in dashboard "Memory sources" panel** (amend 15 — net-new surface, previously a dead end) with capture status per tier + **re-index affordance** (amend 6); DELETE false "issues come in as Events" copy (:2350,:2439) → "issues become work items with a lifecycle record, plus claims extracted from their content."
- `website/apps/dashboard/src/harnesses.js` — `HARNESS_INSTALL` per-harness capture step (T1 install: Pi extension / Claude Code hooks; T3: workflows prompt with capture); `HARNESS_STEPS['claude-web']` disclosure semantics; **web agent-sessions toggle gated on Slice 2's spike verdict** (hidden if neither MCP nor HTTP filing path works — cross-refs Slice 2's mandatory-executable-path rule; the toggle only appears when the spike confirms a working filing surface).
- `dist/` rebuilt + committed (it IS committed in this repo).
- `tortoise/onboarding/AGENT_ONBOARDING.md` — Q3 rewiring (:76-88): tier-aware, real mechanisms, transparency (what's recorded, where it goes), "enabled" gated on delivery; Q5 honest stdio note stays; tool dependency table updated (Q3 no longer marked "✅ Live (HTTP)" misleadingly); parity table names the webhook-only gap (amend 8) + self-hosted stdio differences.
- `tortoise/hosted_api.py` — `/v1/onboarding/session-recording` stays flag-only BUT the "active" claim is now truthful per tier (receipt-gated); delete the dead shadowed `OnboardingStatePatchRequest` at :1837 (extend :7648).

**TDD:**
1. EXTEND `test_onboarding_endpoints.py` — off-by-default defaults, PATCH flow, `capture_revised` flip, `capture_ask_shown` dedup.
2. EXTEND `test_onboarding_integration.py` — misled-user path (existing `session_recording=True` user sees re-ask once).
3. Dashboard panel e2e (app-test skill): toggles persist, re-index job completes.
4. AGENT_ONBOARDING.md false-promise grep clean (no "✅ enabled" without a mechanism).

**Acceptance:** off-by-default; transparent; gated on mechanism delivery; misled users re-asked exactly once; later opt-in from a working dashboard surface; copy honest; dist rebuilt.

**Boundaries:** no new API routes beyond `/v1/index/docs` (the MCP tool is a tool, not a route); no webhook/metering/#909 touch.

---

## Runtime Prerequisites

Docker FalkorDB test lane (`TORTOISE_DB_URI=docker://:falkordb@localhost:6379/tortoise_test_matrix`; embedded carve-out for the 17 files) · GITHUB_CLIENT_ID/SECRET + token with `repo` scope (covers Contents-API) · `TORTOISE_INGEST_BASE_DIR` sandbox SET for Slice 1 (fail-closed when unset) · LLM provider key for `/v1/sessions` (503 surfaced honestly; `TORTOISE_SESSION_LLM_MOCK=1` seam) · Pi hosted-capture leg verification IN Slice 2 (key + config; one real 2xx) · `dist/` rebuild+commit · **quota headroom RESOLVED in Slice 0 (P2-3 FIXED):** first-index volume is BOUNDED (per-run points cap stops the job at the team's `max_points` headroom with a honest partial-completion status + 'index more' affordance, and the default first-run scope is capped at a documented repo count); team-plan caps are verified against worst-case volume in-slice with a named owner (deliver-or-defer, the amend-7/16 pattern) — NOT a silent wiring-time TODO · Claude-Web-MCP-access spike.

## Verification Plan

Slice 0: falsification (a)(b)(c)(d) + #1155 equality, full docker lane. Slice 1: falsification (f) + Document-cap 402 + unset-base fail-closed. Slice 2: Session.harness + entity links (amend 13) + receipt 2xx-only + hooks smoke + Codex/Desktop idempotent + spike verdict + Pi 2xx leg. Slice 3: off-by-default, misled re-ask once, dashboard e2e, false-promise grep clean, dist rebuilt. Each child PR through code-review + commit-workflow gates.

## Acceptance Criteria (issue-level)

1. Ingestion keyed (externalId), Events-as-truth, entity-linked (aboutObject→WorkItem, extractedFrom→Source, Subject edges), quota-fair — re-run on unchanged issues/docs ⇒ 0 new nodes (a/f).
2. Indexer writes `statement` only.
3. Lifecycle: close/reopen ⇒ Event + status projection, statement points untouched (NEVER invalidate_point); edits ⇒ bi-temporal supersede; true-while-open never CORRECTS.
4. `session_recording=True` is a REAL per-harness mechanism: Session.harness + server-receipt verification + entity-linked capture (issue target: "Session/Event + episodic Points **linked to the subject/project entities**" — restored).
5. Ask off-by-default, transparent, gated on mechanism delivery; misled users re-asked exactly once; later opt-in from a working dashboard surface (amends 2/5/6/15).
6. Hosted remote-docs extraction works; stdio-only self-hosted difference honestly documented.
7. #909 untouched (black box); no webhook consumer; no metering changes beyond the index + documents gates.
8. Pinned surface contained: poll-path eventIds byte-identical (creation stays `-created`); **webhook transitions mint transition ids (`-closed`/`-reopened`)**; `test_github_connector.py:419` eventKind re-pin is a DELIBERATE named update; `test_github_indexer.py` rewritten in place to real SDK; everything else additive.
9. T3 has an executable filing surface (tortoise_session_capture + drafted prompt) — no inert promise.

## Boundaries (every slice)

#909 extraction pipeline = black box · no webhook consumer (deleted/transferred = documented gap) · no metering changes · jsonb no-migration · Session.harness graph-side · MCP tool = tool, not a route.

---

# Phase 7 Parallel Review — Controller Incorporations (part of the FINAL plan; fold into slices before decomposition)

> Read this section in full before decomposing — it amends the slices above. All P0/P1/P2 findings from the three Phase-7 reviewers (Codebase/Docs, UX, Devil's Advocate) are incorporated.

## P0/P1-1 — Server-enforced consent gate (the data-plane hole) [DA #1, UX #11, DA #11]
`session_recording`/`capture_opt_in` is today **copy-gated only** — `POST /v1/sessions` accepts any authenticated call (zero reads of the flag in the capture path). The new MCP tool + T3 prompt would be a consentless exfiltration surface (prompt-injection → whole conversation uploaded for a non-opted-in team). **Fix (Slice 2 + Slice 3):** `POST /v1/sessions` and `tortoise_session_capture` return **403 when the team has not opted in** — the flag IS the consent, enforced at the data plane. Collapse `capture_opt_in` into the enforced flag (one boolean: "this team consented to capture" — DA #11); keep `capture_revised`/`capture_ask_shown` for the exactly-once re-ask only. TDD: un-opted team POST → 403; opted-in → 200. The T3 workflows-prompt paragraph only instructs filing after opt-in is confirmed server-side.

## P1-2 — Edit-revert tombstones the current truth [DA #2]
The SDK's dedup query has NO terminal-status filter; `supersede_point` raises on already-terminal points. Edit v1→v2→revert-to-v1 regenerates v1's deterministic id → dedup-hit returns the SUPERSEDED v1 → silent stale truth or ValueError. **Fix (Slice 0):** scope the indexer's statement lookup to `externalId` + `status != terminal` (never rely on content-hash dedup for the current-statement resolution); TDD adds edit→supersede→revert→v3-current-no-error.

## P1-3 — Fetch order/cap blinds lifecycle at org scale [DA #3]
`/issues?state=all` uses GitHub's default created-desc and truncates at 500/repo — the tortoise org has 1,700+ issues, so old-but-active issues sit beyond the window and their lifecycle events/statement supersedes are silently never emitted; created-desc also makes the diff cursor unstoppable. **Fix (Slice 0):** pin `sort=updated&direction=desc` (cursor-correct AND stoppable), parameterize the cap (per-repo `updated_at` cursor makes it cost-control not correctness), and report "N issues beyond window" in job status (honest truncation).

## P2-1 — Re-run must not re-churn EP confidence [DA #4]
Dedup-hit WITH props → `update_point` → `updatedAt` bump → EP dirty-marking on every daily re-poll. **Fix (Slice 0):** two-phase write — dedup-probe WITHOUT props; write props only on genuine create. TDD: `updatedAt` byte-unchanged on re-run for unchanged issues (not just "0 new nodes").

## P2-2 — Entity-linking outcomes tracked, not silent [DA #5]
`create_about_edge` returns False silently on missing target; name-match links miss most real references. **Fix (Slice 2):** track `entity_links_attempted` / `entity_links_created` on the Session node (jsonb); warn-log misses; dashboard shows "linked N of M." "No-match ⇒ no link, honest" now covers match-that-fails-to-link too.

## P2-3 — Closed-event migration math [DA #6]
Second post-change run would mint `-closed` for already-closed issues (acceptance (1) violated); already-closed-at-first-ingest issues carry kind on `-created` (eventId asymmetry). **Fix (Slice 0):** one-time backfill on the first post-change run — detect existing `-created` events with closed-kind/`endedAt` and mint the `-closed` event in the same run; acceptance (1) then holds from run 1; qualify if backfill is rejected.

## P2-4 — Quota-headroom fallback pre-decided [DA #7]
**Fix (Slice 0):** pre-decided fallback = default first-run scope of ONE repo regardless of org size (honest "index more" affordance); only the reference-org's actual numbers are the deliver-or-defer item, not the fallback itself.

## P2-5 — pm:card* blast radius enumerated [DA #8]
**Fix (Slice 0):** named verification item — grep `pm:cardCreated|pm:cardCompleted` across org repos (esp. operations/coordinator outside this repo); update `config/pipelines.yaml:17-18,47-48` + `graph-scripts/setup.py:932-933` kinds; state external-consumer re-pin in the migration note.

## P2-6 — Statement prop contract pinned [Codebase #P2-1]
**Fix (Slice 0):** statement props = `{externalId, extractedFrom, source, github_repo, github_number, github_url}` ONLY — **never `github_state` or any state-derived prop** (state lives exclusively on `Object.status`); assert `github_state IS NULL` on statement points in the mapper test; "no point mutation" assertion scoped to content/status (metadata `updatedAt` bumps permitted).

## P2-7 — Webhook-path eventId decision [Codebase #P2-2]
**Fix (Slice 0):** pin mapper signature `issue_to_event(issue, previous_state=None)` — None ⇒ `-created`; webhook-closed → mint `-closed` (design-correct); restate acceptance #8 as "poll-path eventIds byte-identical; webhook transitions mint transition ids"; add webhook-closed eventId test.

## P2-8 — Deploy config for the docs sandbox [Codebase #P2-3]
**Fix (Slice 1):** add `TORTOISE_INGEST_BASE_DIR` to `entrypoint.sh`/`fly.toml` (server-owned sandbox dir) — without it `/v1/index/docs` fail-closes in production (dead-on-arrival); keep the endpoint check explicit (never fall through to unset-base acceptance on the tenant path).

## UX P1-a — Re-ask fires on BOTH surfaces [UX #1]
**Fix (Slice 3):** the misled-user re-ask gate (`session_recording=True && !capture_revised`, once via `capture_ask_shown`) renders on the wizard step-1 AND the dashboard "Memory sources" panel (persistent "needs your decision" until `capture_revised`), so non-wizard users with points are re-asked too.

## UX P1-b — One consent source across prompt + wizard [UX #2]
**Fix (Slice 3):** AGENT_ONBOARDING.md Q3's yes-branch writes the SAME keys as the wizard (the enforced consent flag + `capture_revised`), and skips its ask when `capture_revised` is set — no cross-surface double-ask, no divergence.

## UX P2s — [UX #3-#8]
- Session-toggle per-harness behavior (SUPERSEDES the earlier per-harness `capture_opt_in` shape — that key is DROPPED): the step-1 session toggle reflects ONE team-level consent (the enforced `session_recording` flag) and reads per-harness STATUS from `session_capture_receipt_{harness}` + `HARNESS_CAPTURE_SUPPORT`; the toggle re-renders on harness-tab switch (receipt-driven, not a per-harness consent key).
- Single source of truth for the web gate: `HARNESS_CAPTURE_SUPPORT` constant in harnesses.js consumed by BOTH the toggle render AND the conditional claude-web prompt paragraph, flipped in the same slice/commit as the dist rebuild.
- Toggle state machine: issues = off→on-but-not-connected (inline Connect) → connected+indexing; docs = disabled-with-reason until connected; sessions = per-harness with spike gate. Reuse `role="switch"`/`aria-checked`/`aria-label`; toggle failures render under the row with `role="alert"` (never the global banner — it appends an Upgrade CTA on any 402); optimistic-flip-with-revert + MERGE per `toggleDashboardKeyLogin`.
- Poll pattern: bounded by tries AND terminal-status short-circuit (stop on completed/failed), interval+timeout in refs, cleared on success + unmount, per-team staleness guard — do NOT copy the current github poll's dangling-timer anti-pattern.
- Capture status = 3-state display: off → "enabled — waiting for first capture" → "active" (receipt observed).
- Copy sweep includes `main.jsx:2437` ("issues → Events" connected-state line), not just :2350/:2439.

## P3/P4s absorbed (concise)
- Register every new jsonb key in BOTH live default-state dicts (`hosted_api.py:1850` is LIVE provisioning default + `:7552`) + `_ALLOWED_STATE_KEYS` + the PATCH model (unregistered keys are silently dropped); TDD round-trip covers a provisioned team.
- Q3 replacement copy DRAFTED (mechanism-gated "enabled" wording, what's recorded/where it goes, stdio variant); retire the `tortoise_diary_write` fallback (itself a false promise) with the honest self-hosted answer; broaden the false-promise grep to the actual current phrases ("will be saved as memory", "Session recording enabled"); update error-recovery + tool table.
- `tortoise_session_capture` stdio behavior pinned: honest "session capture requires hosted mode" error (matching the onboarding-tool precedent) — no local fallback that silently bypasses the 402/403 gates.
- Receipt per-harness (`session_capture_receipt_{harness}`) or graph-derived per-tier status (`MATCH (s:Session {harness:…})`); `SessionRequest.harness` validated against a `Literal` harness value set.
- Re-ask pane + later-opt-in panel = ONE panel with a "you previously enabled this" variant (DA #9 — not built twice).
- T2 import parsers = explicit "historical import (backfill)" deliverable with its own acceptance + size guard, NOT coupled to the wizard's capture acceptance (DA #10).
- Documents gate cap source pinned: reuse `max_points` with a documented conversion factor, or define the jsonb cap column explicitly (DA #12).
- Statement cardinality: 1:1 issue↔statement (single statement from title+body, externalId `github:issue:{repo}#{n}`) so supersede lookup is unambiguous; or per-statement unique externalIds.
- Close→reopen→close: pin the MERGE-conflation as accepted (documented) or add an occurrence discriminator; TDD covers the cycle.
- `_HARNESS_ANALYTICS_VALUES` gains claude-web/claude-desktop.
- StartedAt/endedAt passthrough pinned in the mapper spec; `_cmd_session_capture` preserves `source` when adding `harness`.
- Pi hosted-2xx E2E is an ops checklist item (live key + config), not a CI pytest (tracked as such).
- Dashboard panel home: Overview card row (next to stat cards) with empty/loading/error states; `aria-live` the re-ask pane.
- Delete only the dead `OnboardingStatePatchRequest` at :1837 — keep :1850 (live).
