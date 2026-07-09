---
title: "V2 Integration Map — Data Sources & Applications"
type: product
domain: product
status: seedling
tags: [v2, integration, data-sources, applications, connectors, roadmap]
summary: "Macro-view of all data sources and applications for the epistemic core V2. Maps ingestion strategies, connector priorities, and integrate/code/postpone decisions."
created: 2026-07-09
---

# V2 Integration Map — Data Sources & Applications

> **What this is:** A system-level map of every data source and application the epistemic core will eventually integrate with. Used to make integrate/code/postpone decisions — not a build plan per source, but the terrain.
> **Depends on:** `v1-strategy-2026-07-09.md` (V1 scope), `v1-model-architecture.md` (model routing), `source-operator-event.md` (entity classes), `embedding-retrieval.md` (retrieval model).
> **Note:** The DMer Reply Guy is being built on the DMer app for Instagram + Twitter. This doc maps the epistemic core side of that integration.

---

## 1. Application Layer — What Users Actually See

V1 ships no application. V1 is a library with Python interfaces. V2 is where applications consume the core.

| # | Application | User | Core capability used | V1 | V2 | Notes |
|---|------------|------|---------------------|-----|-----|-------|
| **A1** | **Strategy Assistant** | Coding/knowledge-work agents (Pi, Claude Code, Codex) | Point extraction from sessions, retrieval across past work, epistemic conflict detection, belief propagation queries | 🔧 M0-M2 library | ✅ Full integration | Primary V1→V2 bridge. V1 already targets coding agents. V2 adds the full epistemic reasoning layer. |
| **A2** | **Live Graphic Note Taker** | Meeting participant (Google Meet side panel) | Real-time point extraction, speaker attribution, event generation, decision/loose-thread summarization | ❌ | ✅ V2 | Requires streaming extraction (not batch), speaker diarization, live graph rendering. Hardest technical challenge. |
| **A3** | **DMer Reply Guy** | Social media operator (Instagram DM + Twitter) | Memory-augmented replies — what do we know about this person/topic/thread? | ❌ | ✅ V2 | Uses semantic retrieval over the graph. No real-time extraction needed — replies are pull-based. Easier than A2. Built on the DMer app. |

### Application Capability Matrix

| Capability | A1 (Strategy) | A2 (Live Notes) | A3 (Reply Guy) |
|-----------|:---:|:---:|:---:|
| Point extraction from transcripts | ✅ Batch | ✅ Streaming | ❌ |
| Semantic retrieval | ✅ | ✅ | ✅ |
| Epistemic conflict detection | ✅ | ✅ | ❌ |
| Belief propagation queries | ✅ | ❌ | ❌ |
| Speaker attribution | ❌ | ✅ | ❌ |
| Real-time graph rendering | ❌ | ✅ | ❌ |
| Decision summarization | ❌ | ✅ | ❌ |
| Social media API integration | ❌ | ❌ | ✅ (Instagram + Twitter) |

---

## 2. Data Sources — What Flows Into the Graph

Every source produces the same entity types (Source → Points → Operators → Events). The ingestion strategy differs, but the output is uniform.

### 2a. Source Ingestion Strategy Map

| # | Source | Format | Locator type | Extraction | V1 | V2 | Priority |
|---|--------|--------|-------------|------------|-----|-----|----------|
| **S1** | **Agent conversations** (Pi, Claude Code, Codex) | Session transcripts (JSONL) | `session:<agent>/<session_id>` | Tortoise extractor (LLM) | ✅ Core V1 | ✅ | **P0 — already building** |
| **S2** | **GitHub** (issues, PRs, READMEs, commits) | GitHub API | `github:<owner>/<repo>#<number>` | Zero-LLM (API fields → Source node). Body on-demand via locator. | ✅ Spec'd | ✅ | **P0 — spec exists, implement** |
| **S3** | **Internal docs** (`docs/teams/`) | YAML frontmatter markdown | `file:<path>` | Zero-LLM (frontmatter → Doc node). Content-hash delta. | ✅ Spec'd | ✅ | **P0 — spec exists, implement** |
| **S4** | **Google Docs** | Google Docs API | `gdoc:<document_id>` | Zero-LLM metadata (title, author, dates). Body via API on-demand. | ❌ | ✅ | **P1 — no LLM needed, API is stable** |
| **S5** | **Obsidian** | Markdown files in vault | `obsidian:<vault>/<path>` | Same as internal docs (frontmatter or first-line title). File watcher for delta. | ❌ | ✅ | **P1 — reuse doc ingestion, add file watcher** |
| **S6** | **Slack** | Slack API (channels, DMs, threads) | `slack:<workspace>/<channel>/<ts>` | Tortoise extractor (LLM) after pre-filter. Batch per channel. | ❌ | ✅ | **P1 — needs pre-filter, batch extraction** |
| **S7** | **Meeting recordings** (Zoom, Meet, Teams) | Transcript (provider API or local Whisper) | `meeting:<provider>/<meeting_id>` | Tortoise extractor (LLM) with speaker diarization. Streaming for live, batch for recorded. | ❌ | ✅ | **P2 — hardest technically (streaming + diarization)** |
| **S8** | **Email** (Gmail, Outlook) | Email API (Gmail API / Microsoft Graph) | `email:<provider>/<message_id>` | Pre-filter (newsletters → skip, 1:1 threads → extract). Tortoise extractor for threads. | ❌ | 🔮 | **P2 — high volume, needs triage** |
| **S9** | **Notion** | Notion API | `notion:<workspace>/<page_id>` | Zero-LLM metadata. Body blocks via API on-demand. | ❌ | 🔮 | **P3 — popular but low urgency for internal tool** |
| **S10** | **Linear / Jira** | Linear API / Jira API | `linear:<workspace>/<issue_id>` | Zero-LLM (API fields → Source node). Issue body on-demand. | ❌ | 🔮 | **P3 — task management, useful for project context** |
| **S11** | **Databases** (Postgres, Supabase) | SQL connection | `db:<host>/<database>/<table>` | Schema introspection → metadata. Row-level extraction TBD. | ❌ | 🔮 | **P3 — powerful but broad. Scope to schema + sample rows first.** |

### 2b. Ingestion Complexity by Source

```
Zero-LLM (structured API)          LLM extraction (unstructured text)
─────────────────────────          ─────────────────────────────────
S2  GitHub                         S1  Agent conversations  ◄── V1 core
S3  Internal docs                  S6  Slack
S4  Google Docs                    S7  Meeting transcripts
S5  Obsidian                       S8  Email threads
S9  Notion
S10 Linear / Jira
S11 Databases (metadata)

Easy — days per connector          Hard — streaming, diarization, pre-filtering
```

### 2c. Other Databases Worth Prioritizing

| Database | Why | Priority |
|----------|-----|----------|
| **Supabase** (already connected) | We already use it. Schema + row samples → context for agent decisions. "What's in the `business_audits` table?" becomes queryable. | **P2** — we have the connection, just need the connector |
| **Postgres** (generic) | Same pattern as Supabase. Most startups have one. | **P2** |
| **HubSpot CRM** (already connected via MCP) | Customer data. "What do we know about this prospect?" answers from CRM + graph. | **P2** — reuse MCP connection |
| **Google Search Console** (already connected via MCP) | SEO data. Queryable alongside content strategy claims. | **P3** — niche but already connected |
| **Stripe** | Revenue data. "Did the pricing change affect churn?" | **P3** — useful but narrow |

**Decision:** Database connectors follow a uniform pattern — schema introspection → metadata Source nodes → optional row sampling. Build one generic SQL connector; then Supabase/Postgres/HubSpot are config.

---

## 3. Integration Decision Matrix

### Integrate Now (V1, already building or spec'd)

| Source / App | Why now |
|-------------|---------|
| **S1 — Agent conversations** | Core V1. Tortoise M0-M2. |
| **S2 — GitHub** | Spec exists. Zero-LLM. High signal — issues/PRs contain decisions, rationale, evidence. |
| **S3 — Internal docs** | Spec exists. Zero-LLM. Already have 877 docs. |
| **A1 — Strategy Assistant** | V1 target. Coding agents are the primary user. |

### Code Next (V2, high priority)

| Source / App | Why next |
|-------------|----------|
| **S4 — Google Docs** | Zero-LLM. Simple API. Many strategic docs live in Google Docs before migrating to markdown. |
| **S5 — Obsidian** | Reuses doc ingestion. File watcher is the only new piece. Connor's vault is in Obsidian. |
| **S6 — Slack** | High signal — decisions happen in Slack. Needs pre-filter + batch extraction. |
| **A2 — Live Note Taker** | Hardest technical challenge (streaming + diarization + live rendering). Start prototyping early even if full ship is later. |
| **A3 — DMer Reply Guy** | Easiest application. Pull-based retrieval, no streaming, no LLM at response time (just retrieves from graph). Quick win to show value. Built on the DMer app (Instagram + Twitter). |

### Postpone (V2+, lower priority or high complexity)

| Source / App | Why postpone |
|-------------|-------------|
| **S7 — Meeting recordings** | Requires streaming extraction + speaker diarization + provider-specific APIs (Zoom, Meet, Teams). Each provider is its own integration. Start with recorded transcripts (simpler) before live. |
| **S8 — Email** | Extremely high volume. Needs aggressive pre-filtering. Gmail API + Microsoft Graph are two different integrations. Not worth it until other sources are stable. |
| **S9 — Notion** | Popular but our team doesn't use it. Low urgency for internal tool. |
| **S10 — Linear / Jira** | Useful for project context but GitHub already covers issue tracking for us. |
| **S11 — Databases** | Powerful but broad. Start with schema introspection only. Row-level extraction is a V3 concern. |

---

## 4. Connector Interface Contract

Every source connector implements the same interface so the core doesn't care where data comes from:

```python
class SourceConnector(Protocol):
    """Uniform interface for all data sources."""

    @property
    def source_type(self) -> str:
        """github, gdoc, slack, obsidian, meet, email, notion, linear, db, ..."""
        ...

    async def list_sources(self, since: datetime | None = None) -> list[SourceMetadata]:
        """Return all discoverable sources, optionally filtered by modification time.
        SourceMetadata = {locator, title, created_at, speaker, affiliation, tags}"""
        ...

    async def fetch_body(self, locator: str) -> str:
        """Retrieve full content for a given locator. Called on-demand, not during ingestion."""
        ...

    async def watch(self, callback: Callable[[SourceMetadata], Awaitable[None]]) -> None:
        """Optional. Subscribe to real-time changes. Only connectors that support
        webhooks/polling implement this. Batch connectors raise NotImplementedError."""
        ...
```

**Key design decisions:**
- **Metadata is eager** (extracted at ingestion), **body is lazy** (fetched on demand). The graph stores metadata + locator; the agent fetches the body when it needs to extract Points.
- **`watch()` is optional.** Batch connectors (GitHub, Google Docs) don't implement it. Streaming connectors (Slack, meetings) do.
- **One connector per source type.** GitHub connector handles issues, PRs, READMEs, and commits — different locator formats, same API.

---

## 5. Cross-Application Concerns

### 5a. Authentication

Every connector needs auth. V1 is local-first (no auth needed — filesystem + environment variables). V2 needs a credential store.

| Source | Auth method | Complexity |
|--------|-----------|------------|
| GitHub | Personal access token (env var) | Low |
| Google Docs | OAuth 2.0 (Google Cloud project) | Medium |
| Obsidian | None (local filesystem) | None |
| Slack | Bot token + Socket Mode | Medium |
| Zoom / Meet / Teams | OAuth 2.0 per provider | High (3 separate integrations) |
| Email | OAuth 2.0 (Gmail) + Microsoft Graph (Outlook) | High (2 separate integrations) |
| Notion | Internal integration token | Low |
| Linear / Jira | API key | Low |
| Databases | Connection string | Low |

**Decision:** Start with token-based auth (GitHub, Slack, Notion, Linear, databases). Defer OAuth providers (Google, Microsoft) until the connector is prioritized.

### 5b. Rate Limiting & Cost

| Source | Rate limit concern | Mitigation |
|--------|-------------------|------------|
| GitHub | 5,000 req/hr (token) | Content-hash delta. Only fetch changed items. |
| Slack | Tiered (varies by plan) | Batch per channel. Pre-filter to reduce extraction volume. |
| Google Docs | 100 req/100s per user | Metadata-only at ingestion. Body on-demand (rare). |
| Meeting transcripts | N/A (local Whisper or provider API) | Local Whisper = zero API cost. Provider API = per-minute cost. |
| Email | Gmail: 1B quota units/day | Aggressive pre-filter. Only extract 1:1 threads, skip newsletters. |

### 5c. Shared Graph, Multi-Source

The epistemic graph is shared across all sources. A Point extracted from a Slack message is adjacent to a Point extracted from a GitHub issue. The graph doesn't care about source boundaries — that's the whole point.

**Implication:** Deduplication across sources matters. A claim made in Slack and repeated in a GitHub issue should produce one Point with two provenance entries, not two Points. Tortoise's `content + context` merge key handles this — "same content, same context → one point, multiple sources."

---

## 6. Timeline Sketch

```
V1 (Jul–Aug 2026)
  S1  Agent conversations      ◄── Tortoise M0-M2
  S2  GitHub                   ◄── spec exists
  S3  Internal docs            ◄── spec exists
  A1  Strategy Assistant       ◄── library, no app shell

V2 (Sep–Oct 2026)
  S4  Google Docs              ◄── zero-LLM, quick
  S5  Obsidian                 ◄── reuse doc ingestion
  S6  Slack                    ◄── needs pre-filter + extraction
  A3  DMer Reply Guy           ◄── easiest app, quick win (Instagram + Twitter)
  A2  Live Note Taker          ◄── prototype streaming extraction

V2+ (Nov 2026+)
  S7  Meeting recordings       ◄── hard: streaming + diarization
  S8  Email                    ◄── hard: volume + 2 providers
  A2  Live Note Taker          ◄── full ship with diarization

V3 (2027)
  S9  Notion                   ◄── popular, not urgent for us
  S10 Linear / Jira            ◄── task context
  S11 Databases                ◄── schema introspection first
```

---

*See `v1-strategy-2026-07-09.md` for V1 product scope and competitive positioning.*
*See `v1-model-architecture.md` for model routing and write-cost decisions.*
*See `embedding-retrieval.md` for the 3-tier retrieval model underlying all applications.*
*See `source-operator-event.md` for the entity classes (Source #28, Operator #29, Event #30).*
