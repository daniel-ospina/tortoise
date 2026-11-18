# #11609 — the three structural blockers that hold twelve protected doors

**Status:** PLAN + PIN. Nothing in this change retires a door or alters a wire shape.
**Lane:** P4 (`feat/11609-p4-envelope-and-routes`).
**Authority for the population:** `tests/test_11609_door_retirement.py` (`PROTECTED`, `NO_CONTAINER_YET`).

The twelve doors this document is about are the ones `PROTECTED` holds for a **structural**
reason rather than a missing arm:

| # | reason class | doors |
|---|---|---|
| 1 | `-> list[dict]` wrap envelope (#8482) | `search_sessions`, `suggest_entry_points`, `list_batches`, `list_namespaces`, `diary_read`, `expand_relationships`, `check_structure`, `calibrate_summary` |
| 2 | `rest_spec` — the registry row IS the REST route | `issue_insight`, `session_context`, `search` |
| 3 | the paged destination of two already-retired pointers | `query` |

8 + 3 + 1 distinct names = **12** (`tortoise_search` carries reasons 1 and 2 at once).
The other 4 `PROTECTED` names are the "no arm on the container" class and the 9
`NO_CONTAINER_YET` are a separate population; both are measured below because the brief
asks for the 9, and because a stale reason in either is the same defect.

---

## 1. What was measured (not argued)

### 1.1 The #8482 envelope — question (a)

The envelope is **not added by the tool**. FastMCP derives it from the return annotation.
Two independent reads agree — the vendored source and a live probe.

`fastmcp/tools/base.py:355-369`:

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

| annotation | advertised `output_schema` | `structuredContent`, list arm | `structuredContent`, dict arm |
|---|---|---|---|
| `typing.Any` (**the containers today**) | `null` | **absent** | the dict itself |
| `object` | `{result:{}, required:["result"], x-fastmcp-wrap-result:true}` | `{"result":[…]}` | `{"result":{…}}` |
| `list[dict]` (**the doors**) | `{result:{type:array}, required:["result"], x-fastmcp-wrap-result:true}` | `{"result":[…]}` | `{"result":{…}}` |
| `list[dict] \| dict` (`tortoise_query`) | `{result:{anyOf:[array,object]}, required:["result"], x-fastmcp-wrap-result:true}` | `{"result":[…]}` | `{"result":{…}}` |
| `dict` | object schema, **no** wrap marker | **raises** `structured_content must be a dict or None` | the dict itself |

**Answer to (a).** The envelope adds exactly three wire facts: (i) `structuredContent`
becomes `{"result": <value>}`, (ii) `result` is in `required`, (iii) for a single-type
annotation `result` carries a declared `type`. A container **can** return a pageable shape
while preserving the envelope — `list[dict] | dict` does it, which is the shape
`tortoise_query` has shipped since #11057 — **but no annotation can preserve the envelope
without re-wrapping the payload**, because the wrap is selected by the annotation and not by
the call. `-> dict` is not an option: it deletes the wrap marker and then *refuses* a list
return at the SDK boundary (measured).

Two consequences the decision turns on:

- `tortoise/mcp_server.py:2118` `_violates_wrap_envelope` treats a **missing `result`** as a
  violation for *every* wrap envelope, including `anyOf` — so a union keeps the wait-bound
  refusal repair.
- A dict `result` is only a violation under a **declared `type: array`**; under `anyOf` it is
  admissible (`:2144-2150`). So under a union, a `_safe` failure payload **stops** being
  repaired by #8482 and is left to **#11153** (`_payload_refusal_result`, `:3287`), which flips
  `isError` and keeps the payload byte-identical. That is the protocol-correct verdict channel
  (`isError` is the only failure signal uniform across clients), but it is *weaker* than the
  array envelope, which makes the failure unreadable as data at all.

### 1.2 The population, measured

Envelope doors — all 8 confirmed `list[dict]` today:

```
tortoise_search_sessions        mcp_server.py:7085
tortoise_suggest_entry_points   mcp_server.py:5403
tortoise_list_batches           mcp_server.py:5274
tortoise_list_namespaces        mcp_server.py:5263
tortoise_diary_read             mcp_server.py:6267
tortoise_expand_relationships   mcp_server.py:5536
tortoise_check_structure        mcp_server.py:5215
tortoise_calibrate_summary      mcp_server.py:5744
```

`rest_spec` doors — the row and the route it owns:

```
tortoise_search          tool_registry.py:518   GET /v1/search         (also list[dict] -> both reasons)
tortoise_session_context tool_registry.py:994   GET /v1/context        (-> dict)
tortoise_issue_insight   tool_registry.py:1054  GET /v1/issue-insight  (-> dict)
```

Paged destination: `tortoise_query`, `mcp_server.py:4437`, `-> list[dict] | dict` — the only
container-shaped door on main that already carries a pageable union.

Containers, measured: `search_knowledge`, `list_knowledge`, `check_confidence`,
`graph_overview` (and `overview`, `explore_connections`, `review_link_candidates`) are all
`typing.Any` ⇒ **no `output_schema` ⇒ no envelope at all**.

### 1.3 Why the containers are `Any` — it is not laziness

`TortoiseSDK.list_knowledge` (`sdk.py:30295`) returns **different shapes per `kind`**:

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

`tool_registry.py:2384-2401`, `FastAPIRouterAdapter.register_all`:

```python
for entry in registry:
    if entry.rest_spec is None:
        continue                      # <- no row, no route
    handler = handlers.get(entry.name)
    if handler is None:
        continue                      # <- no handler, no route
    self._router.add_api_route(entry.rest_spec.path, handler, methods=[...])
```

**Answer to (b).** Every REST route is derived from a registry row that also has a handler.
Retiring `tortoise_issue_insight` / `tortoise_session_context` / `tortoise_search` therefore
**deletes `GET /v1/issue-insight` / `/v1/context` / `/v1/search`** — a public API removal, and
exactly what `PROTECTED` exists to prevent. The registry already records the intended fix in
prose: *"the member can stay live as a thin door and retire in the batch that re-homes its
REST twin."*

---

## 2. The decision

### 2.1 Envelope doors — declare the list containers `list[dict] | dict`

One design, applied to every list container. It is not invented here: it is
`tortoise_query`'s shipped shape (`mcp_server.py:4437`), and **migrating callers see no
change at all** — a door that retires was already `list[dict]`, so it already answered
`{"result": [...]}`; the container's list arm under the union answers `{"result": [...]}` —
byte-identical. That is the property that makes this the *smallest* change.

Why the alternatives lose:

- **`-> object`** — same envelope, unconstrained `result`; weaker than the union (it does not
  name the array arm) and identical cost. Rejected as strictly less precise.
- **`-> dict`** — deletes the wrap marker and then *refuses* the list arms at the SDK boundary
  (measured `ToolError: structured_content must be a dict or None`). Invalid.
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

### 2.2 `rest_spec` doors — re-home the route onto the container row

Smallest change, no new mechanism: move `rest_spec` from the leaf entry to the container
entry that absorbs it, **in the same change that retires the leaf** (two rows on one path
would double-register the route). The route keeps its path, method and handler contract —
only its owning row moves — so nothing customer-facing changes. This is the registry's own
recorded intent, and it is the **Gateway Routing / facade** pattern: the client-facing route
is a stable facade over a changing backend (`learn.microsoft.com/azure/architecture/patterns/gateway-routing`
— *"If a service is consolidated, decomposed or replaced, the client doesn't necessarily
require updating… only the routing changes"*).

`tortoise_search` needs **both** mechanisms in one batch: it is the only door carrying two
blockers, and landing either alone leaves the other holding it.

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
- the route half is one edit per door that cannot land without the door's retirement ⇒ the
  27/40 ruling;
- all nine `NO_CONTAINER_YET` destinations are still not MCP doors ⇒ nothing to unblock.

What **is** implemented is the executable pin the plan rests on —
`tests/test_11609_structural_blockers.py` — so that none of the three claims above can rot,
and so the next lane cannot repeat the one move that looks free and is not: annotating a
container and shipping a silent re-wrap.

---

## 4. The ordered steps that remain, and the doors each unblocks

| step | change | unblocks | gate |
|---|---|---|---|
| **S1** | `list[dict] \| dict` on `tortoise_list_knowledge`, `tortoise_search_knowledge`, `tortoise_graph_overview`, `tortoise_check_confidence`, `tortoise_explore_connections`, `tortoise_review_link_candidates` | `search_sessions`, `suggest_entry_points`, `list_batches`, `list_namespaces`, `diary_read`, `expand_relationships`, `check_structure`, `calibrate_summary` (**8**) | **Daniel** — `structuredContent` re-wrap on 6 live tools |
| **S2** | retire the 8 in S1's batch; update `PROTECTED` → 8 names move out (16 → 8) in the same change | the 8 above | follows S1 |
| **S3** | re-home `rest_spec` `GET /v1/issue-insight` → the `search_knowledge` row, retire `tortoise_issue_insight` | `issue_insight` (**1**) | 27/40 ruling batch |
| **S4** | re-home `GET /v1/context` → `check_confidence` row, retire `tortoise_session_context` | `session_context` (**1**) | 27/40 + D4 says the approved home is `recall_beliefs` (not live) — decide which |
| **S5** | S1 **and** S3/S4 together for the `search` row: union annotation + `GET /v1/search` re-homed | `search` (**1**) | S1 + S3 |
| **S6** | re-point `paginated_query` / `query_points_by_tag`, then retire `query` | `query` (**1**) | migration decision |
| **S7** | land the missing containers (`delete_knowledge`, `link_entities`, `poll_events`, `recall_beliefs`, `write_knowledge_batch`, tenancy pair) | the 9 `NO_CONTAINER_YET` | out of scope here |

Twelve structural doors: **8 in S1/S2**, **3 in S3–S5**, **1 (`query`) in S6**. Nine
`NO_CONTAINER_YET`: all in S7. Nothing is retired by this change, and no door is removed from
`PROTECTED` — S1 is not implemented, so none of the twelve reasons is false yet.
