---
title: "#11609 — the three structural blockers that hold twelve protected doors"
type: engineering
domain: platform
doc_status: live
created: 2026-11-18
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph, MCP tool surface
aboutObjects: "#11609, TOOL_REGISTRY, PROTECTED, NO_CONTAINER_YET, FastAPIRouterAdapter, tools/bridge_table.py, mcp_server.py"
---

# #11609 — the three structural blockers that hold twelve protected doors

**Status:** PLAN + PIN. Nothing in this change retires a door or alters a wire shape.
**Lane:** P4 (`feat/11609-p4-envelope-and-routes`).
**Authority for the population:** `tests/test_11609_door_retirement.py` (`PROTECTED`, `NO_CONTAINER_YET`).

The twelve doors this document is about are the ones `PROTECTED` holds for a **structural**
reason rather than a missing arm:

| # | reason class | doors |
|---|---|---|
| 1 | `-> list[dict]` wrap envelope (#8482) — **8 envelope-only**, plus `search` (row 2) = **9** | `search_sessions`, `suggest_entry_points`, `list_batches`, `list_namespaces`, `diary_read`, `expand_relationships`, `check_structure`, `calibrate_summary` |
| 2 | `rest_spec` — *recorded* as "the registry row IS the REST route" — **3**, of which 2 are non-envelope | `issue_insight`, `session_context`, `search` |
| 3 | the paged destination of two already-retired pointers — **1** | `query` |

**9 + 3 + 1 − 1 = 12** distinct names. `tortoise_search` is the `− 1`: it carries reasons 1
and 2 at once (it is `list[dict]` *and* it carries a `rest_spec`). Read the class sizes as
9 / 3 / 1, not 8 / 3 / 1 — with 8, `search` would have to hold only one reason, which §1.5
disproves. S1 unblocks the 9, and §4's `16 → 7` follows from that count.

⛔ **Reason 2 is FALSE on `main`** and that is this lane's headline finding: no REST route is
derived from a registry row (§1.5), so retiring those names deletes no route. `issue_insight`
and `session_context` are therefore **not blocked at all**, and `search` is held only by the
envelope. The names stay in `PROTECTED` — a stale reason is a reason to report and
re-decide, never to retire quietly.
The other 4 `PROTECTED` names are the "no arm on the container" class, and the 9
`NO_CONTAINER_YET` are a separate population; the 9 are measured in §1.4 because the brief
asks for them, and the 4 no-arm names are already measured by
`tests/test_11609_door_retirement.py::TestProtectedDoorsStayLive`
(`test_create_entity_still_has_no_arm_for_the_write`,
`test_the_recorded_container_still_has_no_arm_for_it`), which drives the container rather than
reading a comment — this document does not restate them.

---

## 1. What was measured (not argued)

### 1.1 The #8482 envelope — question (a)

The envelope is **not added by FastMCP for a tool whose ANNOTATION does not declare one**. It
derives the wrap from the return annotation (a container may also build the same `ToolResult`
by hand — see the note under the probe table, `_row_section_envelope`).
Two independent reads agree — the pinned FastMCP source and a live probe.

`fastmcp` is a **pinned dependency**, not vendored (`pyproject.toml:47`, `fastmcp==3.4.6`), so
this citation is version-scoped and will drift on a bump;
`.venv/lib/python3.12/site-packages/fastmcp/tools/base.py:357-369`:

```python
if self.output_schema is None:            # <- `-> Any` lands here
    if isinstance(structured, dict):
        return ToolResult(content=content, structured_content=structured)
    return ToolResult(content=content)    # <- a LIST returns NO structured_content
wrap_result = self.output_schema.get("x-fastmcp-wrap-result")
return ToolResult(
    content=content,
    structured_content={"result": structured} if wrap_result else structured,
    meta={"fastmcp": {"wrap_result": True}} if wrap_result else None,
)
```

Probe (`FastMCP("p").add_tool` + `call_tool`, a fake arm returning a list and a paginated dict):

| annotation | advertised `output_schema` | `structuredContent`, list arm | `structuredContent`, dict arm | `result.meta` (wire `_meta`) |
|---|---|---|---|---|
| `typing.Any` (**the containers today**) | `null` | **absent** | the dict itself | `null` |
| `object` | `{result:{}, required:["result"], x-fastmcp-wrap-result:true}` | `{"result":[…]}` | `{"result":{…}}` | `{"fastmcp":{"wrap_result":true}}` |
| `list[dict]` (**the doors**) | `{result:{type:array}, required:["result"], x-fastmcp-wrap-result:true}` | `{"result":[…]}` | `{"result":{…}}` | `{"fastmcp":{"wrap_result":true}}` |
| `list[dict] \| dict` (`tortoise_query`) | `{result:{anyOf:[array,object]}, required:["result"], x-fastmcp-wrap-result:true}` | `{"result":[…]}` | `{"result":{…}}` | `{"fastmcp":{"wrap_result":true}}` |
| `dict` | object schema, **no** wrap marker | **raises** `structured_content must be a dict or None` | the dict itself | `null` |

⛔ The `typing.Any` row is the FastMCP **CONTRACT**, measured on a synthetic tool with an
`Any` annotation — it is not a claim about each container's body. One of S1's six already
ships the envelope AND the marker by hand: `tortoise_graph_overview`'s row-section arms go
through `_row_section_envelope` (`mcp_server.py:7806`, marker at `:7842`, #10085) and
return `{"result": […]}` with `_meta={"fastmcp": {"wrap_result": true}}` (measured at
this head). So for that tool the marker is not a delta, and §1.2's "no envelope in the
advertised schema" is about the ADVERTISED schema, not an unwrapped body.

**Answer to (a).** The envelope adds exactly **four** wire facts: (i) `structuredContent`
becomes `{"result": <value>}`, (ii) `result` is in `required`, (iii) where a **wrapped**
annotation declares one, `result` also carries a `type` — `array` for `list[dict]`, and
NOTHING for the `object`-annotated form, whose `result` is `{}` (the `type: object` in that
schema — which the table's compressed rendering omits — is the schema's own top level, not
`result`'s), and (iv) a wrapped result stamps the `_meta` marker
`{"fastmcp":{"wrap_result":true}}`, where the arms S1 would change stamp **no `_meta`**
(`fastmcp/tools/base.py:368`; both arms probed, `test_a_wrapped_result_also_stamps_the_meta_marker`).
⛔ Not a universal "the containers stamp none": `tortoise_graph_overview`'s row-section
arms already stamp it by hand (`_row_section_envelope`, `mcp_server.py:7806`/`:7842`, see
issue 10085), so THIS fact's delta is its dict arms plus the other five containers — not
the marker itself. (§1.2 qualifies the same way: advertised schema only.)

A UNION declares `anyOf` and no `type` regardless of its
members (`list[dict] | dict` and `dict | bool` alike), and `object` declares `{}` and no
`type` — which is precisely why `_violates_wrap_envelope` is type-blind for both. A container
**can** return a pageable shape
while preserving the envelope — `list[dict] | dict` does it, which is the shape
`tortoise_query` has shipped since #11057 — **but no annotation can preserve the envelope
without re-wrapping the payload**, because the wrap is selected by the annotation and not by
the call. `-> dict` is not an option: it deletes the wrap marker and then *refuses* a list
return in FastMCP's result validation (measured).

Two consequences the decision turns on:

- `tortoise/mcp_server.py:2118` `_violates_wrap_envelope` treats a **missing `result`** as a
  violation for *every* wrap envelope, including `anyOf` — so a union keeps the wait-bound
  refusal repair.
- A dict `result` is a violation only under a **declared scalar/array `type`**: five of
  the six `_WRAP_ENVELOPE_TYPE_CHECKS` members (`array`, `string`, `boolean`, `number`,
  `integer`). The sixth, `object`, *expects* a dict, so a dict `result` is admissible there
  too — as it is under `anyOf` or an undeclared `type` (`:2144-2150`). So under a union, a
  `_safe` failure payload **stops** being repaired by #8482 and is left to **#11153**
  (`_payload_refusal_result`, defined `:2420`, applied `:3290`), which flips `isError` and
  keeps the payload byte-identical. That is the protocol-correct verdict channel
  (`isError` is the only failure signal uniform across clients), but it is *weaker* than the
  array envelope, which makes the failure unreadable as data at all.

### 1.2 The population, measured

Envelope doors — the 8 envelope-only ones, all confirmed `list[dict]` today (`search`, the
ninth, is in the `rest_spec` table below because it carries both reasons):

```text
tortoise_search_sessions        mcp_server.py:7085
tortoise_suggest_entry_points   mcp_server.py:5403
tortoise_list_batches           mcp_server.py:5274
tortoise_list_namespaces        mcp_server.py:5263
tortoise_diary_read             mcp_server.py:6267
tortoise_expand_relationships   mcp_server.py:5536
tortoise_check_structure        mcp_server.py:5215
tortoise_calibrate_summary      mcp_server.py:5744
```

`rest_spec` doors — the row and the route it **nominally** owns (it does not serve it; §1.5):

```text
tortoise_search          tool_registry.py:518   GET /v1/search         (also list[dict] -> both reasons)
tortoise_session_context tool_registry.py:994   GET /v1/context        (-> dict)
tortoise_issue_insight   tool_registry.py:1054  GET /v1/issue-insight  (-> dict)
```

Paged destination: `tortoise_query`, `mcp_server.py:4437`, `-> list[dict] | dict` — the only
container-shaped door on main that already carries a pageable union.

Containers, measured: the **advertised (registry)** tools whose `output_schema` is `None` —
`get_entity`, `graph_overview`, `search_knowledge`, `list_knowledge`, `check_confidence`,
`explore_connections`, `review_link_candidates` — i.e. **no envelope in the advertised
schema** (one of them, `graph_overview`, still ENVELOPES its row-section arms in the body —
see §1.1's table note). (`get_entity`
is a dict-only container and is **not** among S1's six: no envelope door lands on it. Two
retired aliases, `tortoise_overview` and `tortoise_get`, are still served through the
warning shim with `None` schemas and are excluded because they are not advertised tools.)

### 1.3 Why the containers are `Any` — it is not laziness

`TortoiseSDK.list_knowledge` (`sdk.py:30300`) returns **different shapes per `kind`**:

- `claim` → `paginated_query` → `{results, total, hasMore}` (already paged, AIP-158)
- `batch` (no `id`) → `list_batches` → **list**; `batch` with `id` → `list_batch` → dict
- `namespace` → `_list_namespaces()` → **list**
- `diary` → `_diary_read` → **list**
- `source_reliability`, `topic` → dicts

One annotation cannot honestly describe that. That heterogeneity is the real reason the
annotation is `Any`, and it is what makes the re-wrap cost asymmetric.

### 1.4 `NO_CONTAINER_YET` — the nine destinations, verified live-or-not

Checked against `TOOL_REGISTRY` **and** `TortoiseSDK` (the pointer must resolve to a live MCP
door, per `TestEveryPointerResolvesInOneHop`):

| door | destination | MCP door live? | SDK method exists? |
|---|---|---|---|
| `tortoise_delete`, `tortoise_delete_point`, `tortoise_delete_entity` | `delete_knowledge` | **no** | yes |
| `tortoise_create_edge`, `tortoise_create_operator` | `link_entities` | **no** | yes |
| `tortoise_events_poll` | `poll_events` | **no** | yes |
| `tortoise_ingest` | `sdk:write_knowledge_batch` | **no** | **no — does not exist** |
| `tortoise_org_create` | `tenancy:create_memory_graph` | **no** | **no** |
| `tortoise_list_graphs` | `tenancy:list_memory_graphs` | **no** | yes (`org_id`-scoped) |
| `tortoise_org_create` / `list_graphs` note | `tenancy:` = hosted API router, not an MCP door | — | — |

**Verdict: all nine reasons hold.** Not one destination is an MCP door today, so none is
retirable and none of the nine is unblocked by this lane. (`delete_knowledge`/`link_entities`/
`poll_events` existing as *SDK methods* does not help: a retirement pointer inside
`TOOL_REGISTRY` must name a live **tool**, and `tools/bridge_table.py`'s `DESTINATION` map is
what `_destination()` asserts against.)

### 1.5 The REST route — question (b)

`tool_registry.py:2391-2403`, `FastAPIRouterAdapter.register_all`:

```python
for entry in registry:
    if entry.rest_spec is None:
        continue                      # <- no row, no route
    handler = handlers.get(entry.name)
    if handler is None:
        continue                      # <- no handler, no route
    self._router.add_api_route(entry.rest_spec.path, handler, methods=[...])
```

**Answer to (b) — and it is NOT the answer the registry's prose gives.**

`register_all` *would* derive every route from a registry row. **But nothing calls it.**
Measured on `main`:

```text
$ rg -n 'FastAPIRouterAdapter' --glob '!.venv/*' .
./tortoise/tool_registry.py:2343:# 9 REST tool-ops classified for the FastAPIRouterAdapter:
./tortoise/tool_registry.py:2367:class FastAPIRouterAdapter:
./tests/test_tool_registry.py:809:class TestFastAPIRouterAdapter:
./tests/test_tool_registry.py:814:        from tortoise.tool_registry import TOOL_REGISTRY, FastAPIRouterAdapter  # noqa: I001
./tests/test_tool_registry.py:818:        adapter = FastAPIRouterAdapter(router)
```

(Excerpt. The print above is the product code plus the test that constructs the adapter. The
full run's remaining hits are 12 in other `docs/…` files, 10 in this document (one of them
this file's own frontmatter `aboutObjects:` line), and 16 in
`tests/test_11609_structural_blockers.py` — none of them product code.)

The only **instantiation** is in a test (`tests/test_tool_registry.py:818`); the product has
a comment and the class definition, and nothing else. And `tortoise/hosted_api.py` does not import
`TOOL_REGISTRY` for routes at all (its only registry import is `capability_catalog`, an
unrelated indexer table). The **served** routes are hand-written decorators that call the
**SDK** directly:

| route | where |
|---|---|
| `GET /v1/search` | `hosted_api.py:14927` (+ `selfhost_api.py:366` under the `/v1` prefix) |
| `GET /v1/context` | `hosted_api.py:37761` |
| `GET /v1/issue-insight` | `hosted_api.py:38059` → body calls `sdk.issue_insight(...)` at `:38076` |

⛔ **What the handler BODY does is MEASURED, not cited.** "No route is derived from a row" is
only half of S3's premise; the other half is that retiring the MCP door cannot break the
route, and nothing read the handler body until review round 3 — a handler that called the MCP
tool would have broken a live public route while every pin stayed green.
`tests/test_11609_structural_blockers.py` now holds both halves:
`test_the_serving_handler_reaches_the_sdk_not_the_mcp_door` (the door's MCP name and
`TOOL_REGISTRY` are absent from the serving handler's AST — docstring excluded — and the body
reaches `sdk.<method>`), and `test_the_sdk_edge_reading_is_not_vacuous` (its own falsifier: a
handler that DOES call the door is detected, and the real handler still yields the SDK edge).

So **retiring `tortoise_issue_insight` / `tortoise_session_context` / `tortoise_search`
deletes no REST route.** The coupling the registry records (*"retiring the registry entry
deletes its REST route with it"*) is **aspirational, not real** — the intended-but-unwired
design of #3875, which was **closed 2026-10-02** with the recommendation to *declare and
check* drift rather than derive routes, leaving `FastAPIRouterAdapter` dead code. The same
finding is already recorded at `docs/scoping/2026-09-17-3863-scope-output.md:309`: *"Wrong.
… `FastAPIRouterAdapter` is used only in a test."*

What `rest_spec` actually feeds today: the unwired adapter, `tools/dod_5090_probe.py`'s
reporting (`:8923` builds the `name → rest_spec` map; `:8939-8942` classifies a row without
one as "MCP only (no REST route)" — a fact about the surface, not a route), and comments in
`tools/surface_manifest.py:341` and `config/surface-manifest.yml:98`. **None of them deletes a
route when a row goes.**

⛔ This is a **correction to a recorded reason**, so it is reported and not acted on: the
three names stay in `PROTECTED` until the batch that changes the code moves them, with this
evidence in front of whoever owns that decision. It also means the `rest_spec` half of the
plan is **smaller than the brief assumed — it is not needed at all** (see §2.2).

---

## 2. The decision

### 2.1 Envelope doors — declare the list containers `list[dict] | dict`

One design, applied to every list container. It is not invented here: it is
`tortoise_query`'s shipped shape (`mcp_server.py:4437`). The property that makes it the
*smallest* change is precise, and it is narrower than "nothing changes": **for the list arms,
the migrated caller's `structuredContent` envelope is byte-identical** — a retiring door was
already `list[dict]`, so it already answered `{"result": [...]}`, and the container's list arm
under the union answers the same bytes. Three things still change for a migrating caller and
are not claimed away: the tool **name**, the argument **names** (the container's `mode=`/
`kind=` discriminator), and the advertised schema (`result` becomes `anyOf[array, object]`).
A fourth is visible only to a client that inspects `_meta`: a wrapped result stamps
`{"fastmcp":{"wrap_result":true}}` where the arms S1 would change stamp none (§1.1(iv)) —
except `tortoise_graph_overview`'s row-section arms, which stamp it by hand today.

Why the alternatives lose:

- **`-> object`** — same envelope, unconstrained `result`; weaker than the union (it does not
  name the array arm) and identical cost. Rejected as strictly less precise.
- **`-> dict`** — no wrap marker at all (so no `result` to declare), and FastMCP's own result
  validation then *refuses* the list arms (measured `ToolError: structured_content must be a
  dict or None`, raised in `ToolResult.__init__` at `fastmcp/tools/base.py:148`, reached from
  `convert_result`'s wrap selection at `:365-368`; it is FastMCP's refusal, not the Tortoise
  SDK's). Invalid.
- **Keep the doors live** — settles nothing; the status quo this lane exists to end.
- **A second tool per shape** — forbidden by the brief and by #4282's one-container direction.

**The cost, stated exactly.** The union re-wraps every *already-dict* arm of the six
containers: `structuredContent` goes from `<dict>` to `{"result": <dict>}`. The worst case is
`list_knowledge(kind="claim")` — the container's **default** arm — whose
`{results, total, hasMore}` a client may be reading with `sc["results"]`. That is a
customer-visible `structuredContent` change on six live tools, so it is a **surface change**
and needs Daniel's approval under *MCP/SDK Surface Approval*, not a mechanical lane's
judgement. (It needs no manifest re-cut: `config/surface-manifest.yml` records name /
`sdk_method` / `approval`, never an output schema.)

Failure coverage after the change: the `required: ["result"]` repair still covers the
wait-bound refusal on both arms; dict-arm failures are covered by **#11153**, not #8482. The
plan does **not** claim the array envelope's stronger "unreadable as data" property survives.

### 2.2 `rest_spec` doors — no mechanism is needed; the reason is stale

The smallest change here is **none**. Because no route is derived from the row (§1.5),
retiring `issue_insight` / `session_context` / `search` removes no route, so there is no route
to preserve and no `rest_spec` to re-home. The re-homing step the registry sketches is
unnecessary work built on an unwired adapter.

The consequence is that **two of the twelve doors are not blocked at all**:
`tortoise_issue_insight` and `tortoise_session_context` are held only by a reason that is
false on `main`. They are retirable in the batch that also updates `PROTECTED`; nothing
about them needs a design decision (unlike the envelope doors, which do). `tortoise_search`
remains blocked — by the **envelope**, which is real.

⛔ Not acted on here: `PROTECTED` is a recorded decision, and a stale reason is a reason to
**report and re-decide**, never to retire quietly. The value this lane delivers is that the
next batch no longer has to do work that the tree does not require.

### 2.3 `query` — a re-pointing decision, not this lane's

`query` is not blocked by a shape or a route; it is blocked by being the paged destination of
two **already-retired** pointers (`paginated_query`, `query_points_by_tag`). It retires only
in the batch that re-points those two, which is a migration decision (and it is one of the two
union envelopes the #8482 shrinkage floor measures). Left untouched.

---

## 3. What this change implements

**Nothing that alters a wire shape, a route, or a door.** The evidence does not support a
mechanical step:

- the envelope half needs a `structuredContent` change on six live tools ⇒ approval;
- the route half needs **no change at all** — the coupling it was written against does not
  exist (§1.5), which is a finding to report, not a change to make;
- all nine `NO_CONTAINER_YET` destinations are still not MCP doors ⇒ nothing to unblock.

What **is** implemented is the executable pin the plan rests on —
`tests/test_11609_structural_blockers.py` — so that none of the claims above can rot, and so
the next lane cannot repeat the one move that looks free and is not: annotating a container
and shipping a silent re-wrap.

### The external leg, and what it changed

Research rung 2 (`web_search` / `sonar`) was unavailable on this box (no `PERPLEXITY_API_KEY`;
the `brave-search` MCP closed the connection twice), so primary sources were fetched directly:

- **MCP pagination** (`modelcontextprotocol.io/specification/2025-06-18/server/utilities/pagination`)
  is **cursor-based**: the page of results stays a plain array and `nextCursor` is a **sibling**
  field — not a wrapper object — and the protocol applies it to `*/list` operations, **not** to
  `tools/call`. So there is no MCP-native "paged envelope" for a tool result: the `tools/call`
  result is *already* a wrapper (`content` / `structuredContent` / `_meta`), which is why this
tree's established out-of-band channel is `_meta` (`_SCHEMA_ERROR_META_KEY`, `mcp_server.py:2012`).
- **Gateway Routing** (`learn.microsoft.com/azure/architecture/patterns/gateway-routing`):
  *"If a service is consolidated, decomposed or replaced, the client doesn't necessarily
  require updating… only the routing changes."* — the canonical shape for keeping a REST route
  alive when the abstraction behind it moves.

The first citation is what rules out a *wrapper* as the answer and why the union, not a
paged object, is the shape that keeps the envelope. The second describes the mechanism the
registry **intended** to have — and measurement showed this tree never wired it (§1.5), which
is the finding that removed three steps from the plan.

---

## 4. The ordered steps that remain, and the doors each unblocks

| step | change | unblocks | gate |
|---|---|---|---|
| **S1** | `list[dict] \| dict` on `tortoise_list_knowledge`, `tortoise_search_knowledge`, `tortoise_graph_overview`, `tortoise_check_confidence`, `tortoise_explore_connections`, `tortoise_review_link_candidates` | `search_sessions`, `suggest_entry_points`, `list_batches`, `list_namespaces`, `diary_read`, `expand_relationships`, `check_structure`, `calibrate_summary`, **and `search`** (**9**) | **Daniel** — `structuredContent` re-wrap on 6 live tools |
| **S2** | retire those 9 in S1's batch; move them out of `PROTECTED` (16 → 7) in the same change | the 9 above | follows S1 |
| **S3** | retire `tortoise_issue_insight` and `tortoise_session_context` — **no route work**, because the `rest_spec` reason is stale (§1.5) | `issue_insight`, `session_context` (**2**) | 27/40 ruling batch; update `PROTECTED` with the evidence |
| **S4** | re-point `paginated_query` / `query_points_by_tag`, then retire `query` | `query` (**1**) | migration decision |
| **S5** | land the six missing destinations (`delete_knowledge`, `link_entities`, `poll_events`, `write_knowledge_batch`, `create_memory_graph`, `list_memory_graphs`) | the 9 `NO_CONTAINER_YET` | out of scope here |

Twelve structural doors: **9 in S1/S2** (8 envelope-only + `search`), **2 in S3** (stale
reason, no work), **1 (`query`) in S4**. Nine `NO_CONTAINER_YET`: all in S5. Nothing is retired
by this change, and no door is removed from `PROTECTED` — none of S1–S4 is implemented, so
none of the twelve reasons has been *acted on* yet (one has been shown false, and is reported).
