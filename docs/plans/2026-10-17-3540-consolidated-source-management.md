# #3540 — consolidated source management (ruling B)

**Status:** implementing. **Owner ruling:** comment `2026-10-12T22:40:36Z` on #3540 (**B**).
**Surface change:** approved by that ruling, recorded in `docs/product/canonical-mcp-tools.md` as part of this change.

## What the ruling asks for

1. **One consolidated source-management tool** — lists, switches on, switches off.
2. **The two per-source tools folded into it** — the surface must end **one tool fewer**, not more.
3. **The web endpoints stay** underneath; the tool sits on top.
4. **Permission is the design:** the assistant may request but never self-grant; credentials never pass
   through the model; the change is attributable; deny-by-default.
5. The **tool name is proposed by this lane**, and the approval is recorded in the approved-tools doc.

## The authoritative map (from #7839's contract table: header at `tests/test_tool_registry.py:207`,
`_INTEGRATIONS` itself at `:233`)

| source | enable path today | state read today | agent-settable? |
|---|---|---|---|
| `github_issues` | `tortoise_onboarding_github_index` → `POST /v1/index/github` | `tortoise_onboarding_github_status` → `GET /v1/onboarding/github/status`, `tortoise_onboarding_state` → `GET /v1/onboarding/state` | yes |
| `agent_sessions` | `tortoise_onboarding_session_recording` → `POST /v1/onboarding/session-recording` | `tortoise_onboarding_state` | yes |
| `github_documents` | **none** — `POST /v1/index/docs`; status `GET /v1/index/docs/{job_id}` | none | **no — the tracked gap** |

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

`tortoise_manage_sources(actions)` — one tool, modelled on `tortoise_overview` (the existing
consolidation precedent: `sdk_method=""`, custom handler in `mcp_server.py` that dispatches).

- `action="list"` → one read of `GET /v1/onboarding/state`, projected to per-source state.
  "What sources exist and are they on" must be one call, or every caller re-derives it.
- `action="enable"`, `source=<one of the three>` → dispatch to that source's enable route.
- `action="disable"`, `source=<one of the three>` → dispatch to that source's off path.

**Arithmetic (the ruling's own constraint):** the two per-source enable tools retire into this one, and
`github_documents` gains its enable path **through the same tool** rather than by a new tool. So the
count moves 2 → 1 while the number of agent-settable sources moves 2 → 3. That is the only shape that
satisfies "one tool fewer" *and* closes the github-documents gap without a second addition.

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

### 2. The two GitHub sources share ONE connection

`POST /v1/onboarding/github/disconnect` disconnects GitHub, which stops **both** `github_issues` and
`github_documents`. A per-source `disable` that silently disconnects the other source would be a
destructive surprise.

**Resolution:** `disable` on either GitHub source is **connection-level** and says so in the tool
description and in the returned payload (`scope: "connection"`, and the other affected source named).
The alternative — inventing per-source disable semantics the backend does not have — would make the
tool lie about what it did.

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

## Definition of done

- [ ] `tortoise_manage_sources` in `TOOL_REGISTRY`, group `onboarding`, `http_policy=True`.
- [ ] `tortoise_onboarding_github_index` and `tortoise_onboarding_session_recording` retired.
- [ ] Net tool count **−1**; agent-settable sources **+1** (github_documents).
- [ ] #7839's table flipped: `github_documents` row live, its `xfail` removed — the two must move
      together, or the strict xfail reds.
- [ ] Tests: net −1 count, per-source enable/disable dispatch, refusal without permission, no
      credential in any argument or response, `list` covering all three, connection-level disable stated.
- [ ] Approval recorded in `docs/product/canonical-mcp-tools.md`; `config/surface-manifest.yml` re-cut;
      new route declared in `config/rest-surface-inventory.yml` (a live route that is not declared fails
      closed — learned the hard way on PR #8839).
