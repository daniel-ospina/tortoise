# #3540 — consolidated source management (ruling B)

**Status:** implemented on this branch (PR #8863). **Owner ruling:** comment `2026-10-12T22:40:36Z` on #3540 (**B**).
**Surface change:** approved by that ruling; the approval record lands in
`docs/product/canonical-mcp-tools.md` **with the code in this same change** (the relay record is part of the
DoD below, not a separate step — an approval that never reaches the artifact the next lane reads is the
failure the relay rule exists to prevent).

## What the ruling asks for

1. **One consolidated source-management tool** — lists, switches on, switches off.
2. **The two per-source tools folded into it** — the surface must end **one tool fewer**, not more.
3. **The web endpoints stay** underneath; the tool sits on top.
4. **Permission is the design:** the assistant may request but never self-grant; credentials never pass
   through the model; the change is attributable; deny-by-default.
5. The **tool name is proposed by this lane**, and the approval is recorded in the approved-tools doc.

## The authoritative map (from #7839's contract table — header at `tests/test_tool_registry.py:207`, `_INTEGRATIONS` at `:233`)

| source | enable path today | state read today | agent-settable? |
|---|---|---|---|
| `github_issues` | `tortoise_onboarding_github_index` → `POST /v1/index/github` | `tortoise_onboarding_github_status` → `GET /v1/onboarding/github/status`, `tortoise_onboarding_state` → `GET /v1/onboarding/state` | yes |
| `agent_sessions` | `tortoise_onboarding_session_recording` → `POST /v1/onboarding/session-recording` | `tortoise_onboarding_state` | yes |
| `github_documents` | **none** — `POST /v1/index/docs`; status `GET /v1/index/docs/{job_id}` | `tortoise_onboarding_state` → `GET /v1/onboarding/state` already carries `docs_enabled`, `github_docs_indexed`, `github_docs_indexed_at` | **no — the tracked gap is ENABLE, not state** |

### Which two, exactly — `github_connect` is deliberately NOT one of them

`github-issues` has TWO enable-path tools in that table: `tortoise_onboarding_github_connect` (OAuth:
returns an authorize URL + CSRF state) and `tortoise_onboarding_github_index` (starts the index job).
So "the two per-source tools" needs naming, not assuming:

| tool | what it does | in or out |
|---|---|---|
| `tortoise_onboarding_github_index` | switches the source on (starts indexing) | **retired into the new tool** |
| `tortoise_onboarding_session_recording` | switches the source on/off | **retired into the new tool** |
| `tortoise_onboarding_github_connect` | authorisation step (URL + CSRF state) | **stays** |

`github_connect` stays because it is not a source switch — it is the authorisation handshake, and the
ruling's own condition says *"Credentials never pass through the model. Authorisation to an outside
service stays a host/user-side step."* Folding an OAuth initiation into an agent-callable
list/enable/disable operation would push exactly that handshake toward the model, which is the thing
the condition forbids. That reading also matches the arithmetic: the two that retire are the two
switches, giving **2 → 1**, "one tool fewer".

Correction worth keeping: `tortoise_list_sources` / `tortoise_create_source` are **not** these. They are
graph *provenance* Sources (content origin, `tier`, `sourceDate`). Customer integrations are the
`onboarding_*` family. A scoping pass that stopped at the word "source" would retire the wrong tools.

## The design

`tortoise_manage_sources(action, source)` — one tool, modelled on `tortoise_overview` (the existing
consolidation precedent: `sdk_method=""`, custom handler in `mcp_server.py` that dispatches).

- `action="list"` → one read of `GET /v1/onboarding/state`, projected to per-source state.
  "What sources exist and are they on" must be one call, or every caller re-derives it.
- `action="enable"`, `source=<one of the three>` → dispatch to that source's enable route.
- `action="disable"`, `source=<one of the three>` → dispatch to that source's off path.

**Arithmetic (the ruling's own constraint):** the two per-source enable tools retire into this one, and
`github_documents` gains its enable path **through the same tool** rather than by a new tool. So the
count moves 2 → 1 while the number of agent-settable sources moves 2 → 3. That is the only shape that
satisfies "one tool fewer" *and* closes the github-documents gap without a second addition.

> **OVERRIDES:** the "reads and writes never share a tool" principle
> (`docs/product/canonical-mcp-tools.md`) **and** `docs/product/beta-sdk-surface.md`'s "account
> tenancy is not on the MCP" — the owner ruled for **one** consolidated operation that lists *and*
> switches sources, over per-source tools (2026-10-12, on #3540). It is safe rather than split because
> the entry is `writes=True`: a read-only key is refused even for `action="list"`, so the read inside
> a write-gated tool is unreachable by a read-scoped caller. **Do not "fix" this by splitting `list`
> into its own read tool** — that restores the principle and undoes the ruling's single-tool
> arithmetic; reopen #3540 instead.

## Two collisions, resolved explicitly rather than silently

### 1. `agent_sessions` is default ON — ruling B's "deny-by-default" would reverse #1927

`POST /v1/onboarding/session-recording` documents the existing decision: session recording is the
**optional OFF-switch, default ON, ToS-covered** (`#1927`), not a consent gate.

Ruling B says *"Deny-by-default. A source is off unless it has been switched on."* Applied literally to
`agent_sessions`, that flips a recorded, deliberate default and silently changes what the product
captures for every existing team.

**Resolution:** the permission rule governs *what the assistant may do*, not *what the product already
does*. `disable` works and is honoured; `enable` on `agent_sessions` is a no-op that reports already-on
rather than an error, because the source is on by default. The tool **reports** this (`default: "on"` per
source in `list`) instead of hiding it. Reversing #1927 is a separate owner decision and is not taken
here — this paragraph is the record that it was considered and deliberately not reversed.

### 2. The two GitHub sources share ONE connection — but the OFF is per source

`POST /v1/onboarding/github/disconnect` disconnects GitHub, which stops **both** `github_issues` and
`github_documents`. The first reading of this collision was that a per-source `disable` had no
mechanism behind it and must therefore be connection-level. **That reading was wrong**, and it would
have reversed a recorded decision:

- `issues_enabled` / `docs_enabled` are live, client-patchable **per-source enable-intent** keys
  (`_ONBOARDING_DEFAULT_STATE`, `DEFAULT_ONBOARDING_STATE`, and the `PATCH /v1/onboarding/state`
  model).
- The dashboard's source toggles write **only** those keys and never `github_connected` — that is the
  fix #1924 shipped ("the off-toggle writes the per-source ENABLE intent ... NEVER
  `github_connected`"), and `tests/test_onboarding_endpoints.py::test_issues_off_does_not_disconnect_github`
  pins it: switching one source off leaves the connection and the sibling source intact.
- A full disconnect to hide one source also forces a fresh OAuth round-trip to bring it back. That is
  the behaviour #1924 removed, and re-introducing it through a tool would be a **reversal of a
  recorded decision**, not an implementation choice.

**Resolution:** `disable` on either GitHub source records that source's own enable intent
(`issues_enabled` / `docs_enabled` → `false`) and leaves the connection and the sibling source
untouched; `enable` restores the same flag and starts that source's index job. The connection stays a
separate, explicitly named fact in `list` (`connection_connected`), so "the source is off" and "GitHub
is connected" can never be conflated in the payload.

**The gap this exposes, named as scope rather than hidden behind a destructive default:** those two
flags are **intent-only** today — `rg -n 'issues_enabled|docs_enabled'` finds no reader anywhere in
`tortoise/` outside the two defaults dicts and the PATCH model, so nothing server-side honours a
source-off, and an in-flight index job is not cancelled. The tool therefore reports
`enforcement: "intent-only"` in the payload and says the same in its entry description: it records
the team's intent (exactly what the dashboard toggle does today) and does **not** claim custody of a
source it cannot stop. Making the flag enforced — the indexer and the capture path reading it — is the
remaining work item, and it is tracked on #3540 rather than being papered over with a disconnect.

## Permission model (ruling B's attached condition)

- **Request, never self-grant.** The handler requires the caller's own permission for the org *and*
  that source; refusal is a refusal, not a silent no-op.
- **Credentials never pass through the model.** No action takes, returns, or echoes a token. GitHub
  authorization stays a host/user-side step; `enable` on a GitHub source refuses with the connect
  instruction rather than soliciting credentials.
- **Attributable.** Every enable/disable logs subject, org, source, action, and outcome.
- **Deny-by-default** — for the purpose above, with the `#1927` exception stated in the payload.

## Implementation mechanics (found by reading, not assumed)

Four facts that decide how the change must be written — each would have produced a wrong or broken
implementation if guessed:

1. **Retirement goes through `RETIRED_USE_INSTEAD`, not deletion.** The repo's rule (#3883, stated in
   `mcp_server.py:6020`): *"when a name is retired, a caller still gets an answer and is TOLD the name is
   retired, naming the replacement"*, and `#3883` is documented as a hard prerequisite for executing
   removals. `RETIRED_TOOL_REGISTRY` is derived — `[t for t in _ENTRY_DECLARATIONS if t.name in
   RETIRED_USE_INSTEAD]` (`tool_registry.py:1590-1591`) — so adding the two names to that map removes them
   from the *advertised* surface (the count really does shrink) while a caller still resolves them and
   is pointed at `tortoise_manage_sources`. **Deleting the entries instead would answer "tool not
   found"**, which is the behaviour #3883 exists to prevent.
2. **Dispatch is `globals()[t.name]`** (`mcp_server.py:6013`), so the new tool needs a module-level
   `def tortoise_manage_sources`; the two existing handlers stay in place because the retired shims
   reuse them.
3. **`disable` on a GitHub source needs an `await`** — `github_disconnect` awaits
   `_revoke_github_token(token)`, and **every** MCP tool handler in `mcp_server.py` is synchronous
   (measured: 0 `async def tortoise_*`). So either the handler schedules the disconnect on the running
   loop and reports it as started (the pattern the indexing tools already use), or it becomes the first
   async tool — an unverified change to the dispatch path, and not assumed here.
4. **Permission is already enforced at the tool boundary.** `_enforce_mcp_tool_scope`
   (`mcp_server.py:713`) reads the registry's `writes=True` and requires `graphs:write`, and an
   unresolvable name is DENIED. So "the caller's own permission" needs no new check — it needs the new
   entry to declare `writes=True` (it does) and the handler to call
   `_reject_graph_bound_mcp_org_surface`, like every other org-level writer.

## The #7839 contract table: how the strict xfail resolves

The rows move together — the `github-documents` row becomes live **and** the `strict` xfail on
`test_docs_index_tool_registered` goes away — but not by keeping that test as written. A consolidated
`tortoise_manage_sources` declares **no** `rest_spec` (it sits on top of the routes and adds none), so
it can never satisfy an assertion that a registered tool wraps exactly
`("POST", "/v1/index/docs")`, and it can carry at most one `rest_spec` anyway.

**The choice, stated:** the route-wrapping test is **replaced**, not relaxed, by
`tests/test_tool_registry.py::TestAgentAddressableIntegrations::test_the_consolidated_tool_carries_the_docs_source`,
which asserts the consolidated entry is registered and group-reachable **and** that no per-source
docs-route tool appeared (an addition there would undo the count reduction the same ruling requires).
The strict xfail is deleted with the test it guarded; `test_the_two_per_source_switches_are_retired_not_deleted`
and `test_the_consolidated_tool_replaces_both_switches_one_for_one` carry the #3883 retirement half.
Settling command after implementation:
`uv run pytest tests/test_tool_registry.py -k "consolidated or retired_not_deleted" -q`
(expected: all pass, with no `xfail` marker left on the docs row).

## Definition of done

- [x] `tortoise_manage_sources` in `TOOL_REGISTRY`, group `onboarding`, `http_policy=True`.
- [x] `tortoise_onboarding_github_index` and `tortoise_onboarding_session_recording` retired.
- [x] Net tool count **−1**; agent-settable sources **+1** (github_documents).
- [x] #7839's table flipped: `github_documents` row live, its `xfail` removed — the two must move
      together, or the strict xfail reds.
- [x] Tests: net −1 count, per-source enable/disable dispatch, refusal without permission, no
      credential in any argument or response, `list` covering all three, and the per-source disable
      proven to leave `github_connected` and the sibling source intact (#1924), with the intent-only
      enforcement gap named in the payload.
- [x] Approval recorded in `docs/product/canonical-mcp-tools.md`; `config/surface-manifest.yml` re-cut.
      The rest-surface item does **not** apply as written: the consolidated tool declares **no**
      `rest_spec` — it sits on top of the five existing routes and adds none — so
      `config/rest-surface-inventory.yml` has nothing new to declare. (A live route that is not
      declared fails closed — learned the hard way on PR #8839 — which is why this is stated rather
      than silently skipped.)
