# Solution Approaches — Issue #1196: graph insight on issue creation (surface b)

> Solution-diverge input for issue #1196. Independent framing: each approach answers the
> three forks differently (where insight logic lives / input contract / standalone-vs-mode).
> Companion to `2026-08-14-1196-issue-insight.md` (problem + surface feasibility + decisions).
> Findings date: 2026-08-14. No winner selected.

## Shared constraints (all approaches)

- **Graph-only, live queries only.** Per scoping decision 3, GH API state is OUT of path —
  every approach reads the graph and nothing else. "Never duplicate" is trivially satisfied:
  all approaches are read-only (no writes → no emission side effects; same input → same output).
- **Fail-closed envelope.** Empty graph → explicit `no_prior_knowledge`; graph-service failure →
  `_safe()` error-dict contract (never raise); stale index → honest "not indexed" state, never a
  fabricated insight. Precedent: `session_context` → `no_prior_sessions`.
- **2 data points + pointer.** Every approach emits a compact dict: ≥1 live-derived data point
  (a graph row, count, or EP-annotated hit — never hardcoded copy), plus a pointer whose search
  topic is live-derived from the top hit (never fixed copy).
- **Invocation is a cross-repo deliverable.** All three require a step in
  `skills/issue-creation/SKILL.md` (symlink → agent-infra; committed there via skill-sync).
  The tortoise PR carries tool + tests + docs only.
- **E2E pattern.** All three reuse the E2E-17 convention (`tests/test_index_mcp.py`): fresh
  embedded DB (`TortoiseSDK(tmpdir/t.db, namespace=...)`), seed graph via `create_point`
  with github-indexer-shaped props (`kind="observation", source="github", github_repo,
  github_number, github_state`), invoke the **module-level mcp_server handler directly** (the
  exact function body FastMCPAdapter wraps), transport-mode ContextVar fixture, assert via raw
  Cypher on `sdk._get_proj().g`. "Issue-created → insight" is simulated: seed prior knowledge as
  the indexer would have, then call the insight tool with the would-be issue's title.

---

## Approach A — Dedicated composite tool + SDK method: `tortoise_issue_insight` (free-text input)

The refined version of the obvious shape — logic in the SDK (testable, REST-derivable), new
read-only MCP tool, agent passes what it is about to file.

**Name:** `tortoise_issue_insight(title, body=None, repo=None, limit=2)`

**Description:** New `ToolDefinition` (readOnlyHint, `http_policy=True`, `group="memory"`) +
new `TortoiseSDK.issue_insight()` method + thin `_safe()`-gated handler. Returns
`{has_prior, no_prior_knowledge, data_points: [...], insight: "...", more_in_graph: "<topic>"}`.

**Architecture — where logic lives:** SDK method, ~40 lines, placed beside `session_context`
(new "Issue Insight (#1196)" section):
1. Semantic stage: `self.tortoise_fts_query(title + body, entity_type="point", limit=2)` —
   cross-session decisions, EP-tagged claims, "we already decided this" (the research-designated
   non-GitHub-natively-covered space; deliberately NOT re-running textual similar-issue dedup).
2. Repo stage (only when `repo=` given): structural `self.query(kind="observation",
   filters={"source":"github","github_repo":repo})` → prior-issue count/state mix for that repo.
3. Shaping: keep top 2 hits (EP `confidence_mean` annotated), compose the pointer from the top
   hit's content-derived topic. Empty graph → `no_prior_knowledge: true` (same arithmetic as
   `no_prior_sessions`). `repo=` given + graph non-empty + zero observation points for repo →
   `repo_not_indexed: true` + actionable text (run `tortoise_onboarding_github_index`).

Handler is the standard `return _safe(_get_team_sdk().issue_insight, title, body=..., ...)`
wrapper (mirrors `tortoise_session_context`). Registry entry carries
`rest_spec=RestSpec(GET, "/v1/issue-insight")` → `FastAPIRouterAdapter` derives the hosted
endpoint for free (mirrors `/v1/context`); `hosted_api.py` route wrapper only resolves the team.

**Files touched:**
- `tortoise/sdk.py` — `issue_insight()` method (semantic + repo-stage + shaping + fail-closed branches)
- `tortoise/mcp_server.py` — `tortoise_issue_insight()` handler (thin `_safe` wrapper)
- `tortoise/tool_registry.py` — ToolDefinition; **tool count 85 → 86**
- `tortoise/hosted_api.py` — `/v1/issue-insight` route (auto-derived, team-resolution wrapper)
- `tests/test_issue_insight.py` — E2E legs + unit tests (empty graph, `_safe` failure, repo_not_indexed)
- `tests/test_tool_registry.py` — count 85 → 86
- agent-infra `skills/issue-creation/SKILL.md` — invocation step (cross-repo commit)

**E2E:** Seed observation points (2 repos, incl. one matching the title's topic) + one
high-EP decision point. Call `tortoise_issue_insight(title="...", repo="owner/a")` through the
handler → assert `len(data_points) >= 1` and each point's content matches a seeded row (live,
not copy). Empty-DB leg → `no_prior_knowledge`. Monkeypatched `_get_team_sdk` raising →
error dict, no crash. Repo-with-zero-points leg → `repo_not_indexed`.

**Risks:**
- Tool bloat: 85 → 86 (research explicitly flagged tool count as a real cost; registry count
  test must move).
- Free-text input is semantically fuzzy — garbage title → weak/decoy matches
  (`suggest_entry_points` had the decoy problem for garbage queries; needs a threshold on
  `rrf`/`confidence_mean` before emitting a "match").
- Staleness signal only fires when the skill passes `repo=` — if invocation omits it, only
  empty-graph fail-closed is reachable (stale-index leg untested in practice).
- E2E "≥1 data point" can be flaky if seed confidence is low — seed must include a
  high-EP point so the semantic match is deterministic.

**Tradeoffs:** Cleanest contract and strongest "standalone surface (b)" story; REST mirror is
derived, not duplicated. Pays the tool-count tax and needs threshold tuning for fuzzy input.

**Best-fit-if:** The team accepts +1 tool for a clean, named, documented surface, and wants the
hosted REST path as a free byproduct for a later human-UI follow-on.

---

## Approach B — Insight mode on an existing tool: `tortoise_recall(mode="issue")` — zero new tools

Directly challenges the "new read-only MCP tool" shape: no registry growth, no tool-count test
churn. The insight rides the preset+override mode pattern recall already has.

**Name:** `tortoise_recall(mode="issue", query="<title>", repo=None)` — new mode, existing tool.

**Description:** Extend `_RECALL_MODES = ("state", "gaps", "subgraph", "custom")` with `"issue"`;
add a dispatch branch routing to a new SDK `recall_issue(query, *, repo=None, limit=2)` method.
Returns a mode-specific shape (recall already varies shape per mode: `{mode, results}` vs
`{mode, nodes, edges, stats}`) — issue mode returns
`{mode: "issue", no_prior_knowledge, data_points, insight, more_in_graph}`.

**Architecture — where logic lives:** SDK mode-branch (new `recall_issue` method; the
`tortoise_recall` handler already forwards `mode` + `query` and already has the unknown-mode
fail-closed guard — adding to `_RECALL_MODES` is the only handler change, plus a docstring
tweak on the tool description). Internally identical query composition to Approach A
(semantic fts stage + optional repo-scoped structural stage), but reachable only through
recall's dispatch.

**Files touched:**
- `tortoise/sdk.py` — `recall_issue()` method (mode branch; same internals as A's `issue_insight`)
- `tortoise/mcp_server.py` — `_RECALL_MODES` tuple + one dispatch branch + description tweak
- `tortoise/tool_registry.py` — **description-only** tweak on `tortoise_recall` (count stays 85)
- `tests/test_recall_issue.py` (or extend `test_mcp_server.py` recall tests) — mode dispatch,
  empty-graph, failure, unknown-mode guard still intact
- agent-infra `skills/issue-creation/SKILL.md` — invocation step (cross-repo commit)

**E2E:** Same embedded-DB seeding as A; call `tortoise_recall(mode="issue", query="<title>")`
through the handler → assert `mode == "issue"` and `len(data_points) >= 1`. Empty DB →
`no_prior_knowledge`. Unrecognized-mode leg still returns the existing structured error.

**Risks:**
- **Invocation gap persists** — the confirmed problem is *invocation*; a mode buried inside an
  existing 15-param tool is harder for the creating agent to discover and reach for than a named
  tool. The skill step must spell out the exact call shape.
- Contract muddying: recall's documented return contract says `{mode, results}` /
  `{mode, nodes, edges, stats}`; a third shape weakens the doc contract, and issue-mode ignores
  most of recall's 15 params (confusing when mode-specific params are set).
- Mode-guard tests and description-parity checks touch recall — small but real regression surface.

**Tradeoffs:** Zero tool-bloat cost (the research's stated preference: "parameterize existing
query tools"); recall is epistemically the right home ("what does the graph already believe
about this?"); but the weakest invocation story of the three, and the shaping lives under a
generic tool name rather than the surface the issue names.

**Best-fit-if:** Tool-count cost is the dominant concern and the fleet already reaches for
`tortoise_recall` regularly — the skill step can be a one-line call into a familiar tool.

---

## Approach C — Repo-identity pull, composition at the MCP layer, staleness as first-class state

Challenges the input contract: instead of free-text, the agent passes the repo it is filing
into (a scalar it already holds) and the insight is *keyed to repo identity*. Insight logic
composes existing SDK primitives at the MCP layer — no new SDK method.

**Name:** `tortoise_issue_insight(repo, title=None, body=None, limit=2)`

**Description:** New read-only tool whose primary input is `repo` (`owner/name`), with
`title`/`body` optional enrichment. Registry entry uses `handler_override` (no `sdk_method` —
precedent: raw-Cypher REST ops) + optional `rest_spec`.

**Architecture — where logic lives:** mcp_server-layer composition over existing primitives,
in the handler itself (optionally factored into a `tortoise/insight.py` helper so hosted_api can
mirror):
1. Repo stage (primary): structural `sdk.query(kind="observation",
   filters={"source":"github","github_repo":repo})` → prior-issue count, open/closed mix,
   newest few. This is where **staleness is defined**: repo given + zero observation points for
   it + non-empty graph → `repo_not_indexed: true` + actionable text — the common onboarding
   case per research (empty/stale graphs are the norm), now a first-class, testable state.
2. Semantic stage (when `title`/`body` given): `tortoise_fts_query(title+body)` for
   cross-repo decisions — deliberately the *secondary* hook so the tool does not re-run
   GitHub-native similar-issue dedup (research: surface what GitHub does not).
3. Shape 2 data points: repo stats (structural, deterministic) + top semantic hit (EP-annotated).

**Files touched:**
- `tortoise/mcp_server.py` — `tortoise_issue_insight(repo, title=None, body=None, limit=2)`
  handler composing `sdk.query` + `tortoise_fts_query`; fail-closed branches in-handler
- `tortoise/tool_registry.py` — ToolDefinition with `handler_override` (+ optional
  `rest_spec=RestSpec(GET, "/v1/issue-insight")`); **tool count 85 → 86**
- (optional) `tortoise/insight.py` — shared composition helper for REST mirroring
- `tests/test_issue_insight.py` — repo-scoped E2E + staleness legs + unit tests
- `tests/test_tool_registry.py` — count 85 → 86
- agent-infra `skills/issue-creation/SKILL.md` — invocation step (cross-repo commit)

**E2E:** Seed observation points for `owner/a` and `owner/b`; call handler with
`repo="owner/a"` → assert repo-scoped count ≥1 data point and repo-specific content (no bleed
from `owner/b`). Leg: `repo="owner/c"` (zero points, non-empty graph) → `repo_not_indexed`
honest fail-closed. Empty DB → `no_prior_knowledge`. Failure path → error dict.

**Risks:**
- Repo-required input: if the skill omits `repo`, the tool degrades to `no_prior_knowledge`
  (safe but useless) — invocation must guarantee the scalar.
- Repo-keyed structural path is issue-dedup-adjacent (GitHub-native territory) — mitigated by
  making the semantic cross-repo stage the *primary hook for the "aha"* and repo stats secondary
  framing, but the risk is real if the shaping drifts.
- `handler_override` without `sdk_method` means no SDK-level unit test surface — logic is only
  testable through the handler (embedded DB through the handler, or a `_get_team_sdk` stub);
  REST mirror needs the optional helper module or it duplicates composition.
- `repo_not_indexed` inference can false-positive while an in-flight `_INDEX_JOBS` job (1h TTL)
  is still populating — graph-inferred staleness is inherently a snapshot; acceptable for
  fail-closed (the false positive is "honest unknown", never a fabricated insight).

**Tradeoffs:** Deterministic, identity-keyed input (lowest invocation friction — the agent
always knows the repo); the only approach where "stale index" is a first-class, testable state
with an actionable pointer; misses cross-repo "we already decided this" unless title/body are
passed, and the structural stage risks duplicating GitHub-native dedup unless the shaping keeps
semantic stage primary.

**Best-fit-if:** Onboarding-time empty/stale graphs are the dominant real-world state (per
research) and the fleet reliably passes repo identity — the honest `repo_not_indexed` pointer is
the beta "aha" that converts an empty graph into a call to action.

---

## Cross-approach comparison

| Fork | A (SDK method + new tool) | B (recall mode) | C (repo-identity pull) |
|---|---|---|---|
| Where logic lives | SDK method `issue_insight` | SDK mode-branch `recall_issue` | MCP-layer composition over primitives |
| Input contract | title/body (+optional repo) | recall `query`=title (+optional repo) | repo (required) + optional title/body |
| Standalone vs mode | New tool (85→86) | Mode on existing tool (85) | New tool (85→86) |
| Stale-index fail-closed | Only when `repo=` passed | Only when `repo=` passed | First-class (`repo_not_indexed`) |
| REST mirror | Derived via rest_spec | Not applicable (tool already has no REST) | Optional via handler_override + rest_spec |
| Invocation friction | Named tool, low | Buried mode, highest | Named tool + single scalar, lowest |
| E2E shape | Seed graph → call handler with title | Seed graph → recall(mode="issue") | Seed 2 repos → call handler with repo |
