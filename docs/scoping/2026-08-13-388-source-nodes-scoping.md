---
title: "<!-- issue-scoping: v5.1 double diamond + verify -->"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

<!-- issue-scoping: v5.1 double diamond + verify -->
# Scoping — #388 feat(connectors): emit proper Source nodes with sourceKind and references

**Issue:** daniel-ospina/tortoise#388 · **Tier:** standard · **Epic:** #7227 · **Depends on:** #7240
**Date:** 2026-08-13 · **Mode:** streamlined (retry — prior run stalled mid-research; PRIOR_RESEARCH: `docs/research/2026-08-13-388-source-nodes-research.md`)

---

## Confirmed Problem

Connector events (GitHub/Linear/Slack) carry source provenance metadata — `source` (repo/team/channel-scope string) and `sourceKind` (`github_issue`, `linear_card`, `slack_message`) on the poll/webhook `EventRecorded` dicts (github.py:369/395, linear.py:171/197, slack.py:196) — but that metadata is **not modeled at the graph-materialization boundary**: the projection's `_upsert_event` handler (the single choke point through which 100% of connector events flow via `proj.apply`) never materializes a Source node or a `references` edge — the fields survive only as inert Event-node extra props via `_persist_extra_props` (#228, entities.py:496-498; `source`/`sourceKind` are in neither `_META_KEYS` nor `_EVENT_HANDLED`), and `source` is a non-dereferenceable scope string (`github:{repo}`) anyway. *(Precision: the GitHub entity-path event, `_issue_to_entities` github.py:198-212, carries neither — only its Object carries `url`; that path additionally needs metadata enrichment, covered in Plan Step 2.)* As a result, the `(Point)-[:extractedFrom]->(Source)-[:references]->(Entity)` provenance chain (ONTOLOGY §3.4, P4 consumer `get_provenance_chain`, sdk.py:7210) returns empty for every GitHub/Linear/Slack entity.

**Root cause (not symptom):** the graph-shape projection layer ignores the provenance metadata producers already emit. The issue's literal framing ("connectors create Source nodes") would patch the symptom per-connector; the root-cause fix is central materialization in the projection choke point, which all three connectors (and any future connector) already pass through — validated by the event-sourcing precedent (Azure event-sourcing, Protean projection-granularity, TypeGraph idempotent projectors — PRIOR_RESEARCH architecture axis) and by the in-repo precedent `_upsert_document` → `(Source {url:doc_id})-[:references]->(Document {id:doc_id})` (#205, ONTOLOGY §3.4) which already does exactly this pattern for documents.

### Why This Framing
- **Evidence:** (a) `_upsert_document` (entities.py:265) is the in-repo precedent — documents already get Source→references wiring; ONTOLOGY §3.4 §163 explicitly states "entity-reference detection in connectors remains a follow-up"; (b) all three connectors emit `source`+`sourceKind` on every event record — the metadata exists, only the projection ignores it; (c) all connector ingestion converges on `proj.apply()` → `_upsert_event` (github.ingest:260, linear.ingest, slack.ingest — verified in code); (d) per-connector emission would need ~8+ call sites across github's poll/webhook/entity paths alone, triplicated across 3 connectors — drift risk with no test covering the interaction (metronix-memory precedent, PRIOR_RESEARCH).
- **Rejected alternatives:** see Problem Diamond below (Framing A per-connector, Framing C SDK-centralized — both rejected on evidence).

### Falsification Check
Framing B fails if: (a) connector events flow through a path other than `proj.apply`/`_upsert_event` — **disconfirmed** (all three connectors call `proj.apply`); (b) materializing Source nodes changes EP weights — **disconfirmed** (SOURCE_KIND_DEFAULTS registers `github_issue`/`slack_message`/`linear_card` as explicit `None` = neutral, source_credibility.py:64-70; no inheritance change); (c) the provenance chain consumer can't traverse Event nodes — **disconfirmed** (`get_provenance_chain` matches `(entity)` label-agnostically, returns `labels(entity)`); (d) **a `source`-presence gate is safe for ALL choke-point producers — disconfirmed, FIXED**: `mining.py` `_make_event` (mining.py:417-440) emits EventRecorded with `source` but NO `sourceKind`/`sourceUrl`, applied via `api.projection.apply(record)` (mining.py:414) → the same `_upsert_event` choke point. The materialization gate must therefore fire only on a registered connector `sourceKind` or an explicit `sourceUrl` — never on bare `source` — or mined conversations would materialize spurious non-URL Source nodes.

### Confidence: 85/100

---

## Problem Diamond (inline, streamlined — no sub-agent dispatch per conductor)

### Alternative Problem Framings
- **Framing A (original):** "Connectors must emit Source nodes" — per-connector code creates SourceCreated events + references edges. *Strength:* literal reading of the issue; *weakness:* triplicated logic, drift risk (metronix precedent), webhook/entity paths easily missed, treats a projection responsibility as a producer responsibility.
- **Framing B (root cause — CHOSEN):** "Connector events carry source metadata that the projection's graph-materialization boundary drops" — the shared `_upsert_event` choke point must materialize Source + references from metadata already present. *Strength:* single fix covers all 3 connectors + webhook + entity paths + future connectors; rebuild-safe-by-construction (derived from EventRecorded, idempotent MERGE); matches event-sourcing canonical pattern and in-repo document precedent. *Weakness:* requires a precise gate in `_upsert_event` (fire only on registered connector sourceKind or explicit sourceUrl — NOT bare `source`, which would catch mining.py's source-only events) so non-connector producers are untouched.
- **Framing C (SDK-centralized):** "The SDK write path should create Source nodes centrally from sourceKind on any write" (extend the create_document extractedFrom wiring, #394). *Strength:* SDK facade is a single surface; *weakness:* connectors apply events directly via `proj.apply` — they do NOT route through SDK facades, so this would rewire the connector→projection boundary for zero benefit over B.

### Adversarial / Disconfirmation Queries (run inline)
1. **"Should the SDK create Source nodes centrally from sourceKind on any write?"** → No: connectors bypass SDK facades (`proj.apply(ev)` directly, github.ingest:260); SDK-central would require rewiring all connector paths. Framing C rejected on codebase evidence.
2. **"What breaks for existing consumers if Source nodes appear now?"** → `list_sources` (sdk.py:1971) / MCP `tortoise_list_sources` will show new 0-point Sources (expected — provenance browsing gains connector sources); EP inheritance: all connector kinds neutral → **zero EP weight change** (verified SOURCE_KIND_DEFAULTS); `get_provenance_chain` starts returning rows (intended — this IS the P4 unblock); rebuild replay: connector events aren't in the JSONL log (connectors apply graph-only), so derived Sources behave exactly like existing connector-derived Events — no rebuild regression. Backward-compat research: adding node/edge types is additive-safe (TypeGraph schema-evolution, PRIOR_RESEARCH).
3. **"Is repo-level Source granularity (`github:{repo}`) enough?"** → No: MITRE provenance guide + Springer KG-provenance survey: coarse-grained provenance has dead ends, "does not have the granularity to track how each entry was transformed"; the P4 consumer needs the specific issue/PR/message. Per-entity Source URLs are feasible: GitHub `url` field available via `gh` CLI JSON + already on webhook/entity paths; Linear GraphQL query **already selects `url`** (linear.py:87) — one line to pass through; Slack permalink via `chat_getPermalink` (WebClient already in use) or constructed `https://{workspace}.slack.com/archives/{channel}/p{ts}` (Slack docs canonical). Quality-over-convenience: per-entity wins.
4. **"Does eager per-event Source creation explode the graph / churn?"** → One Source per entity (MERGE-on-url, idempotent), not per event occurrence. Version-churn on re-poll is the real risk (`_upsert_source` bumps `version` on MATCH) → plan MUST coalesce unchanged re-materializations (TypeGraph pitfall, PRIOR_RESEARCH). Mitigation: choke-point MERGE sets sourceKind/title/ingestedAt with `coalesce` and does NOT bump version on unchanged re-apply.
6. **"Could the P4 consumer be served WITHOUT Source nodes — by extending `get_provenance_chain` to read `source`/`sourceKind` off Event-node properties (zero graph change)?"** → No: `extractedFrom` (ONTOLOGY §3.3) only links Point → Source **nodes**; without Source nodes the chain is structurally empty regardless of consumer logic. (Honesty note: `source`/`sourceKind` DO persist as inert Event-node props via #228, so a consumer-side fallback is *partially* feasible future work — but the (Point)-[:extractedFrom]->(Source) shape is mandated by the issue O/I/T and ONTOLOGY §3.4; and `source` is a non-dereferenceable scope string, so even direct reads would not yield usable URLs. The per-entity URL materialization adds real value.)
5. **"Should Sources be created lazily (at Point extraction via `_link_source`) instead of eagerly at event time?"** → No: lazy creation leaves connector entities without Sources until a Point exists — contradicts the issue objective (connectors create Source nodes) and leaves the P4 references edge empty for connector entities with no extracted Points. Eager-at-event-time chosen; `_link_source` remains the Point-side fallback (both coexist — `extractedFrom` and `references` are distinct edges, ONTOLOGY §3.3/§3.4).

### Boundary & Stakeholders
- **In scope:** projection `_upsert_event` materialization; connector event metadata enrichment (per-entity `sourceUrl`, sourceKind fixes); sourceKind vocabulary registration; ONTOLOGY §3.4/§5 doc update; connector + projection + idempotency + list_sources regression tests.
- **Out of scope:** P4 consumer changes (`get_provenance_chain` works as-is); EP inheritance changes (kinds stay neutral); Slack/Linear webhook support (Linear has none); connector event-log persistence (graph-only, existing behavior); `complete_source`/content hashing for connector sources; backfill of pre-existing connector entities (no connector events are persisted in JSONL, so there is nothing to backfill — graph-only materialization applies to future events only).
- **Affected but unmentioned:** MCP `tortoise_list_sources` consumers (cosmetic new entries); any dashboards reading `list_sources`; the two-producer Object/Event id collision (`github-issue-{repo}-{number}` poll vs `{entity_id}-created` entity path) — adjacent issue, filed separately.

---

### Axis Research

> **PRIOR_RESEARCH dedup:** the existing brief `docs/research/2026-08-13-388-source-nodes-research.md` (2026-08-13, Fast intent, 6 post-dedup queries, Exa — Perplexity 429) covers the Ontology axis (PROV-O canonical; Springer granularity pitfall), the Architecture axis (Azure event-sourcing; metronix-memory + curiosity-ai + ucp-gen competitor precedents; Protean/TypeGraph projection pitfalls), and graph upsert idempotency (Neo4j MERGE semantics; Curiosity idempotency rules; investigraph ID spaces; backward-compat). Deduplicated against it — no re-queries on those axes.

**New findings this run (3 queries, Exa — Perplexity 429; within the ≤4 post-dedup cap):**

- **Per-entity URL schemes (canonical, gaps in the brief):**
  - **Slack:** canonical permalink = `https://{workspace}.slack.com/archives/{channel_id}/p{message_ts_without_dots}` (e.g., `https://ghostbusters.slack.com/archives/C1H9RESGA/p135854651500008`); official method `chat.getPermalink(channel, message_ts)` returns this URL; threaded replies add `?thread_ts=...&cid=...`. Pitfall: the `p{ts}` path segment is officially returned by getPermalink (recommended over hand-construction — StackOverflow warns against relying on unofficial internal syntax). source: https://docs.slack.dev/reference/methods/chat.getpermalink ; https://stackoverflow.com/questions/43837626
  - **Linear:** canonical per-issue URL = `https://linear.app/{workspace}/issue/{identifier}` (e.g., `https://linear.app/storytell/issue/OPS-79`); the GraphQL API returns a `url` field directly on Issue objects. Our Linear connector's GraphQL query **already selects `url`** (tortoise/connectors/linear.py:87) — pass-through is a one-line change; cycles have no web URL (fall back to team-level source). sources: https://linear.app/developers/graphql ; https://apis.io/schemas/linear/linear-issue/
  - **GitHub:** canonical issue URL `https://github.com/{repo}/issues/{n}`, PR `https://github.com/{repo}/pull/{n}`; `gh` CLI exposes `url` in `--json` output (already used in poll_raw_issues / webhook / entity path) — add to poll `--json` field list. (General knowledge — no query needed.)
- **Multi-target references / granularity (pitfalls):**
  - One Source node referencing many entities is standard practice, not an anti-pattern: ArcGIS KG provenance — "one source can provide provenance for many properties of different entities and relationships"; entity-level provenance records. source: https://doc.esri.com/en/arcgis-pro/latest/help/data/knowledge/add-provenance-to-a-knowledge-graph.html
  - Granularity tradeoff confirmed (MITRE provenance guide): fine-grained per-entity provenance is more informative but costs storage/complexity; "when in doubt, prefer capturing too much and later summarizing, rather than capturing too little and finding dead ends." Repo-level Source = dead end for per-issue provenance. sources: https://www.mitre.org/sites/default/files/publications/practical-provenance-guide-MP100128.pdf ; Springer Datenbank-Spektrum 2024 (already in PRIOR_RESEARCH)

**Trigger assessment:** Ontology axis (medium) and Architecture axis (medium) both fired; Library-deps axis NOT triggered (no new third-party deps — all plumbing in-repo; Slack SDK already used). Deduped the ontology/architecture axes against PRIOR_RESEARCH; the 3 new queries covered only demonstrated gaps (URL schemes + multi-target precedent).

### Integration Docs
- **No new third-party dependencies.** All required plumbing is in-repo and battle-tested:
  - `TortoiseSDK.create_source(url, sourceKind, ...)` (sdk.py:6695) — MERGE-on-url, dual-write sourceKind↔credibilityTier, invalidates inheritance gate + reliability cache. *(Not used by the connector choke point — reference only.)*
  - `FalkorProjection.link_source_to_entity(source_url, entity_id, entity_label, source_kind)` (projection/edges.py:212) — auto-creates Source on MERGE; validates label ∈ {Document, Event, Object}. Use for the github entity-path Object reference.
  - `_upsert_source` (entities.py:501) — SourceCreated handler (**negative dependency**: ON MATCH bumps version — do NOT reuse for connector re-materialization; write-plans handoff constraint).
  - `_upsert_document` (entities.py:265) — the #205 in-repo precedent for Source→Entity references wiring.
  - `SOURCE_KIND_DEFAULTS` (source_credibility.py:64) — extensible kind→tier registry; `register_source_kind_default` for new kinds (all connector kinds neutral).
  - `chat_getPermalink` via existing Slack `WebClient` (slack.py:56) — one SDK call per message for per-message permalinks.

---

## Plan (draft — implementation design will be refined in writing-plans)

### Proposed Solution (Approach 1 — chosen)
**Central projection materialization + minimal connector metadata enrichment.** The projection's `_upsert_event` (single choke point for all connector events) materializes a Source node from the event's `source`/`sourceKind`/`sourceUrl` metadata when present, and wires `(Source)-[:references]->(Event {eventId})` (+ `(Source)-[:references]->(Object {id})` when the connector's entity path provides the object id). Connectors are enriched to carry per-entity `sourceUrl` and correct `sourceKind` — the producer's only job. Graph shape stays the projection's responsibility (event-sourcing principle; mirrors `_upsert_document` #205 precedent).

### Implementation Steps (task-level)
1. **Projection — Source materialization in `_upsert_event` (tortoise/projection/entities.py:343):**
   - **Gate (verifier P1 fix):** materialize a Source ONLY when the event carries a registered connector `sourceKind` OR an explicit `sourceUrl` field. Pin the gate predicate as an explicit frozenset `_CONNECTOR_SOURCE_KINDS = {"github_issue", "github_pr", "linear_card", "slack_message"}` (coherence P2 fix — NOT `SOURCE_KIND_DEFAULTS` registry membership, which also contains `document` and T0-T4 tier forms; the explicit set keeps the mining.py exclusion precise). Bare `source` alone → no Source node. This excludes the second choke-point producer `mining.py` (events with `source` but no `sourceKind`, mining.py:417-440) and any other non-connector emitter.
   - On gate fire: MERGE `(s:Source {url: $sourceUrl or $source})` with **kind set only on CREATE** — `ON CREATE SET s.sourceKind=$sk, s.title=$sourceUrl, s.contentHash='', s.ingestedAt=$now`; **ON MATCH, leave `sourceKind` untouched** (`coalesce(s.sourceKind, $sk)` — coherence P1 fix: an EXISTING Source's kind is authoritative per #398 "never overwritten" contract, sdk.py:6720-6722; silent re-kind would be the metronix source_role-demotion shape). **No version bump on re-materialization** (TypeGraph churn pitfall). Note: a pre-existing `_link_source` stub (kind `'document'`) therefore keeps `document` — documented in Step 6.
   - Wire `MATCH (s:Source {url}), (e:Event {eventId}) MERGE (s)-[:references]->(e)` — always, when eventId present.
   - Wire `(s)-[:references]->(Object {id})` ONLY when the event carries an explicit `sourceObjectId`-style field set exclusively by the github entity path (`entity_id` = `github-issue-{repo}-{number}`). **`event.object` must NEVER be used as an Object key** — on poll/webhook paths it is the entity TITLE string (github.py:373, linear.py:175, slack.py:190), and `_upsert_event`'s produces edge already stubs `Object {name: title}` with a random ulid; using it would no-op or wire references to the wrong stub. (Verifier P3 fix — removes the previous "decision at writing-plans" ambiguity.)
   - Note: `_persist_extra_props` (#228) continues to persist `source`/`sourceKind`/`sourceUrl` as Event-node props alongside the new Source materialization — unchanged, additive, and relevant to the AC2 idempotency test (both mechanisms coexist in `_upsert_event`).
   - Gate: no `source` field → no Source node (non-connector events untouched — zero regression surface).
2. **Connector: github.py** — fix the duplicate-key literal (`"sourceKind": "github_issue", "sourceKind": "github_issue"` at :369 and :395 — second key silently wins today); **pass the already-fetched `url` field through `_issue_to_event`/`_pr_to_event` into a `sourceUrl` event field** (the `url` field is already in all `gh --json` lists — poll_raw_issues:105, _poll_issues:120, _poll_prs:137 — and in webhook payloads via `html_url`; the mappers currently drop it); emit `sourceKind` = `github_pr` for PR events (currently mislabeled `github_issue`); apply to poll + webhook + `_issue_to_entities` paths; entity path (`ingest()`, proj.apply at github.py:238/254/273/277) adds one `proj.link_source_to_entity(source_url, obj_id, "Object", source_kind)` call next to the existing routing/aboutSubject raw queries.
3. **Connector: linear.py** — pass through `url` from GraphQL (already queried at :91) into event `sourceUrl`; cycle events keep team-level `source` fallback (no web URL); **cycle events emit `sourceKind: "linear_cycle"`** (coherence P3 fix — cycles are not cards; registered neutral in Step 5, replacing the semantic mislabel `linear_card` on cycle events).
4. **Connector: slack.py** — per-message `sourceUrl` via `chat_getPermalink(channel, message_ts)` (WebClient already available); on failure/absence, fall back to channel-level `source` (graceful — permalink is an enhancement, not a hard dep). **Keep enrichment ingestion-scoped or lazy** (coherence P4 fix): `poll()` is a public API used standalone for JSONL/preview — per-message permalink calls inside the pure mapper would cost ~2×limit API calls per poll with no projection attached.
5. **Vocabulary: source_credibility.py** — register `github_pr` AND `linear_cycle` in `SOURCE_KIND_DEFAULTS` as `None` (neutral) so the kinds are resolvable; existing kinds stay neutral (no EP change).
6. **Docs: docs/ONTOLOGY.md §3.4 + §5** — document connector Source materialization (choke-point projection), `github_pr` + `linear_cycle` sourceKind values, the kind-is-authoritative-on-MATCH rule (#398), and **fallback keying: `Source.url` may be a container-scope string (`slack:{channel}`, `linear:{team_key}`) when no per-entity URL exists** (coherence P4 fix — ONTOLOGY §3.4's `url (permalink)` column implies URL-ness; the fallback must be stated). Keep §3.4 wording scoped to the code-validated `references` target set (Document|Event|Object); the #909 §4.3 #8 Source-target extension is documented in the ontology but not yet enforced in `link_source_to_entity` — do not cement that drift either way.
7. **Tests** (see Testing Strategy below).

### Testing Strategy
- **Connector mapping tests** (test_github_connector.py, test_linear_connector.py, test_slack_connector.py): **add** `sourceUrl` (per-entity) + `sourceKind` assertions (PR → `github_pr`, cycle → `linear_cycle`) on emitted events; **update fixtures to carry `url`** (the poll fixtures currently drop it; webhook fixtures already pass `html_url`). Note: no sourceKind assertions exist in these test files today — these are new assertions (coherence P4 fix).
- **Projection integration test** (test_projection.py or new test_connector_sources.py): applying a GitHub/Linear/Slack-shaped EventRecorded creates exactly one Source node (per-entity url) with correct sourceKind + `references` edge(s); non-connector events (no `source`) create no Source nodes.
- **Idempotency test:** apply the same event twice → Source/Event/Object node counts unchanged, version unchanged (coalesced upsert). **Plus: re-apply a connector event over a PRE-EXISTING Source** — both `_link_source` stub (kind `document`) and operator-tiered variants — and assert `sourceKind` + EP unchanged (coherence P1 fix — guards the #398 kind-is-authoritative contract).
- **list_sources regression:** connector sources appear with correct sourceKind and 0 points; document-source listing unchanged.
- **Provenance chain test:** create a Point with `extractedFrom` = connector sourceUrl → `get_provenance_chain` returns the Source + referenced entity (P4 unblock proof). Note: the pre-existing two-Events-per-issue shape (poll eventId `github-issue-{repo}-{number}` at github.py:371 vs entity-path eventId `{entity_id}-created` at :229) means the entity-path test must assert BOTH Event nodes exist with their two `references` edges — do not enshrine a wrong one-Event invariant (coherence P3 fix).
- **EP no-change test:** connecting Source nodes with neutral connector kinds does not alter existing Point weights (guards the EP-neutral claim).

### Verification Plan
- Full suite: `python -m pytest tests/ -v` (FalkorDBLite embedded). Live-FalkorDB run for connector integration tests per repo README.
- Manual spot check: run github connector poll twice against a fixture repo; assert `MATCH (s:Source) RETURN count(s)` stable.

### Runtime Prerequisites
- No new deps, no new services, no migrations. Slack permalink path uses the existing `SLACK_BOT_TOKEN` client. **Rate-limit note:** `chat_getPermalink` is called per message INCLUDING thread replies (poll fetches up to `limit` + `limit` replies, slack.py:60-99) — a single poll can add ~2×`limit` API calls, not one. Acceptable within Slack's tier limits; batched alternative (construct permalink when `workspace_domain` is configured) documented as a follow-up if it becomes a bottleneck.
- **Task-ordering note:** if the `_upsert_event` gate is implemented as `SOURCE_KIND_DEFAULTS` registry membership, land Step 5 (`github_pr` registration) before or with Step 1 — otherwise PR events rely solely on the sourceUrl leg of the gate (safe in practice: PR events carry per-entity URLs and `resolve_tier` returns None for unregistered kinds — EP-neutral).

### Acceptance Criteria
1. Applying a GitHub/Linear/Slack EventRecorded creates exactly one Source node keyed on the per-entity URL (with explicit container-level fallback: linear cycles → `linear:{team_key}`, slack permalink failure → `slack:{channel}` — deliberate non-URL fallback keying, documented in Rejected Alternatives), with correct `sourceKind` and `references` edge(s) to the Event (and Object on the github entity path).
2. Re-applying the same events (re-poll) leaves node counts and versions unchanged (idempotent, no churn).
3. Events without `source` metadata create no Source nodes; **mining-shaped events (`source` present but no sourceKind/sourceUrl, e.g. mining.py:417-440) also create no Source nodes** (regression-free for all non-connector choke-point producers).
4. `list_sources` / MCP `tortoise_list_sources` show connector Sources with correct sourceKind; existing document/point Sources unchanged.
5. `get_provenance_chain` returns connector provenance for a Point with `extractedFrom` = connector source (P4 unblocked).
6. EP inheritance weights unchanged for existing Points (connector kinds neutral).
7. GitHub PR events carry `sourceKind: github_pr` (not `github_issue`); duplicate-key literals removed.
8. ONTOLOGY §3.4/§5 updated; all existing tests pass.

---

## Rejected Alternatives

- **Approach 2 — Per-connector emission (the issue's literal framing):** each connector emits SourceCreated events + calls link_source_to_entity per entity. *Why rejected:* triplicates the logic across 3 connectors × (poll + webhook + entity) paths (~8+ call sites in github alone); drift risk with no test coverage of the interaction (metronix-memory precedent: silent behavior change, "no migration, no test covering the interaction"); future connectors would need to re-implement; the projection already owns graph shape for every other entity type. *When it WOULD have been better:* if only one connector existed and the projection were not a shared choke point — neither holds.
- **Approach 3 — SDK-centralized write path:** route connector events through a new SDK facade that auto-creates Sources on any write (extension of create_document's extractedFrom wiring, #394). *Why rejected:* connectors bypass SDK facades entirely (`proj.apply(ev)` directly) — this approach would rewire the connector→projection boundary with no benefit over Approach 1, and would duplicate the choke point (SDK facade + projection both materializing). *When it WOULD have been better:* if connectors were already SDK-facade consumers (they are not).
- **Upsert strategy — create-always:** emit a fresh Source per event occurrence. *Why rejected:* duplicate Source nodes per entity on re-poll; breaks node-count idempotency. Chosen: upsert-by-sourceUrl (MERGE).
- **References representation — embedded (JSON array property on Source):** *Why rejected:* `get_provenance_chain` traverses `[:references]` edges; embedded refs would break the P4 consumer and violate ONTOLOGY §3.4. Chosen: edge type (existing `references` edge, already validated for Document|Event|Object targets).
- **Granularity — repo/team/channel-level Source:** *Why rejected:* dead-end provenance (MITRE/Springer granularity pitfall); every issue would collapse to one Source; P4 consumer cannot distinguish entities. Chosen: per-entity Source with container-level fallback only when the per-entity URL is unavailable (slack permalink failure, linear cycles).

---

## Wiring Check

| Touch Point | Type | Covered By | Status |
|-------------|------|------------|--------|
| `_upsert_event` (projection/entities.py:343) — Source materialization + references | Core code | Plan Step 1 | ✅ |
| Connector events — `sourceUrl` + sourceKind (github.py:369/395, linear.py:171/197, slack.py:196) | Core code | Plan Steps 2-4 | ✅ |
| GitHub entity path Object reference (ingest(), ~:285) | Core code | Plan Step 2 | ✅ |
| `SOURCE_KIND_DEFAULTS` / `register_source_kind_default` (source_credibility.py:64) | Vocabulary | Plan Step 5 | ✅ |
| ONTOLOGY.md §3.4 / §5 (references edge, sourceKind vocab) | Docs | Plan Step 6 | ✅ |
| MCP `tortoise_list_sources` (mcp_server.py:684) | Consumer | Regression test (list_sources) | ✅ |
| `get_provenance_chain` (sdk.py:7210) — P4 consumer | Consumer | Provenance-chain test | ✅ |
| EP inheritance (source_credibility.resolve_tier / ep.py) | Cross-cutting | EP no-change test | ✅ |
| Event-log rebuild (projection/rebuild_all) | Cross-cutting | No change for connector events (graph-only today; derived Sources follow). **Note (coherence P3 fix):** a rebuilt graph materializes connector-URL Sources only via `_link_source` (kind `document`) — a known #330 parity-limitation class; #330 parity tests must exempt connector Source kinds; Step 6 documents that kind is re-derived on connector re-poll | ✅ |
| Second choke-point producer `mining.py` (`source` without sourceKind, mining.py:414) | Non-connector producer | Gate scoped to connector kinds / explicit sourceUrl → mining events excluded; regression test | ✅ |
| Tests: connector mapping, projection, idempotency, provenance, EP | Tests | Plan Step 7 | ✅ |
| GitHub duplicate-key literal + PR kind mislabel | Adjacent bug (in touched lines) | Absorbed — Plan Step 2 (in-scope: the lines are the issue's own cited lines) | ✅ |
| Two-producer Event/Object id collision | Adjacent bug (colliding eventId literal lives in a touched function `_issue_to_event`, but the fix has event-log blast radius) | Filed as extra issue (#—, see Extra Issues) — deferred, not absorbed | ✅ |
| `parallel_work_check` C1/C2 checkpoint | Infra | **Skipped — tool not installed** (missing infra tooling; noted per streamlined mode) | ⚠️ |

**<HARD-GATE>** All wiring gaps resolved — no uncovered touch points block completion.

---

## Verification Gates

### problem-verify
- 2 cycles. Cycle 1: 1×P1 (mining.py gate — fixed) + precision fixes. Cycle 2: both verifiers NO ISSUES FOUND. Clean.

### solution-verify
- 2 cycles. Cycle 1: 1×P1 (see log — fixed). Cycle 2: both verifiers NO ISSUES FOUND. Clean.

### Qwen coherence (Phase 5.6)
- **`[QWEN-GATE] substitute reviewer used`** — qwen3.8-max provider blocked (HTTP 401). Fresh-context substitute dispatched per conductor instruction. Result: see Review Cycle Log.

---

## Clarifications
None — no questions qualified for the human gate. Confidence 85/100 (problem) and approach evidence is codebase-grounded; taxonomy matches (ontology vocabulary extension `github_pr`, new graph nodes) are additive-safe and researched (TypeGraph additive schema evolution; connector kinds neutral → no EP/cost impact; no destructive ops; no new third-party deps). One optional human decision deferred to writing-plans/execution: whether Slack per-message permalinks (1 extra API call/message) are worth it vs channel-level Sources for the initial slice — the plan defaults to permalink-with-fallback, which is safe either way.

---

## Review Cycle Log

### problem-verify — Cycle 1
- Verifier A: P0=0, P1=1, P2=0, P3=2, P4=1
- Verifier B: P0=0, P1=0, P2=1, P3=2, P4=1
- Controller action: **Fixed P1 (Verifier A — mining.py second producer)**: gate tightened to fire only on registered connector sourceKind or explicit sourceUrl (verified mining.py:414/417-440 emits `source`-only EventRecorded through the same `_upsert_event` choke point); added mining-shaped regression test to AC3 + wiring row. Also fixed: problem-statement overclaim (entity-path event github.py:198-212 lacks source/sourceKind — noted as precision fix), Plan Step 2 mechanism (url already in gh --json lists; pass-through into sourceUrl), line citations (sdk.py:7210, linear.py:91, github ingest apply sites), and added explicit disconfirmation 6 (consumer-side alternative ruled out — extractedFrom links only to Source nodes). Ignored nothing.
- Re-dispatching both verifiers → Cycle 2.
- Cycle 2 (final): both verifiers returned NO ISSUES FOUND.

### solution-verify — Cycle 1
- Verifier A: P0=0, P1=0, P2=0, P3=2, P4=2
- Verifier B: P0=0, P1=0, P2=0, P3=2, P4=0
- Controller action: No P0/P1 → gate passes without re-dispatch. Incorporated P3×2 (both verifiers): (1) Confirmed Problem wording corrected — `source`/`sourceKind` persist as inert Event-node extra props via `_persist_extra_props` (#228), not "dropped entirely"; disconfirmation 6 honesty note added; (2) Plan Step 1 Object-wiring pinned to an explicit `sourceObjectId` field with `event.object` (title on poll paths) explicitly ruled out. Incorporated P4×2: extra-props coexistence note in Step 1; Slack rate-limit magnitude (per-message incl. thread replies ≈ 2×limit calls/poll) + Step 5-before-Step-1 ordering note if gate uses registry membership.
- No re-dispatch needed (P2+ only → incorporate and pass).

### [QWEN-GATE] coherence — substitute reviewer
- Substitute: P0=0, P1=1, P2=1, P3=3, P4=4. Controller: **Fixed P1** (Plan Step 1 `ON MATCH` sourceKind overwrite contradicted the #398 never-overwrite contract — changed to kind-set-on-CREATE-only with `coalesce(s.sourceKind, $sk)` on MATCH + pre-existing-Source idempotency test). **Fixed P2** (gate predicate pinned as explicit `_CONNECTOR_SOURCE_KINDS` frozenset, not registry membership). Incorporated P3×3 (id-collision rationale corrected + two-Events test expectation; rebuild parity note; `linear_cycle` kind) and P4×4 (ONTOLOGY §3.4 drift scoping; test wording; `_upsert_source` negative-dep note; slack lazy enrichment + fallback keying). Fix applied deterministically (code-verified) — no re-run per conductor (max 1 cycle). Coherence verdict: COHERENT — no problem↔solution drift, no dropped dimensions, no research contradictions.

*(Detailed P1 descriptions appended by the controller in the finalize step — see gate notes below.)*

---

## Complexity

| Domain | Rating | Basis |
|--------|--------|-------|
| TIER | standard | Issue-declared; multi-file (projection + 3 connectors + vocab + docs + tests) but well-scoped, all primitives exist, no new architecture |
| UX_RATING | low | No UI surface; MCP/CLI list_sources cosmetic additions only |
| ONTOLOGY_RATING | medium | sourceKind vocabulary extension (`github_pr`); new Source nodes + references edges for connector entities (additive, already documented in §3.4); needs §5 + §3.4 doc update |
| ARCH_RATING | medium | Projection choke-point change (`_upsert_event`); connector event schema gains `sourceUrl`; idempotency/version-churn handling |
| DATA_RATING | low | No migrations, no schema change; graph nodes additive |
| TEST_RATING | medium | New integration + idempotency + regression coverage across connectors |
| DEP_RATING | low | No new third-party deps (Slack SDK already used; zero new services) |

---

## Extra Issues Filed During Scoping
- **#1155** (filed 2026-08-13): two-producer Event id collision — poll path `eventId` = `github-issue-{repo}-{number}` collides with entity-path `eventId` = `{entity_id}-created` and the Object id, producing two Event nodes per issue; two-producer identity ambiguity (PRIOR_RESEARCH investigraph ID-space note). Adjacent bug — not absorbed; noted in Wiring Check.
