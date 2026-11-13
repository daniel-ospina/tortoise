<!-- research-path: issue #12378 comment carrying `<!-- research: v1 -->` (posted 2026-11-13) -->
<!-- plan-review: cycles=pending, status=pending, version=2.3.0 -->

# #12378 — read the Event layer the way the operator layer is now read

## Confirmed problem

`tools/curate_graph.py` performs the #11469 census over Points, Sources and (since PR
#12380) the operator layer. The **Event layer — 4,670 rows on `live-v4`, exactly
`counts.Event` — is never read**, and the harness records that omission in
`NOT_ENUMERABLE["event"]` (`curate_graph.py:640`) as *"READABLE but NOT read by this
pass"*. The layer is reachable in one advertised call:
`tortoise_get_entity(None, type="events", limit=N)` forwards to `tortoise_get_events`
(`mcp_server.py:6094`), which returns `MATCH (e:Event) … LIMIT $lim` as a bare list
(`sdk.py:49054`) — no total envelope, no offset walk.

Consequence, and it is the same class PR #12380 closed one layer over: the census
reports `by_class` with a single undifferentiated `event`-absent picture, so the
episodic tier's per-kind size (`AgentSession` vs `meeting` / `deployment` / …) is
unavailable, and Target 2 of #11469 — *"every dropped row is named in the report with
its reason"* — cannot be satisfied for 4,670 rows that were never enumerated.

**What this does NOT do:** it does not move a single row into `carry`.
`classify_event` (`curation.py:548`) answers `undecidable` on both its arms — an
agent-session container (raw tier, home is #10000's open D1 question) and a domain
Event (no `events` write section on the ingest bundle). Reading the layer buys
per-kind census resolution and per-row reasons, nothing more. The episodic tier's
destination remains #10000's decision, not this change's.

**Falsification check** (carried from the research block, one falsifier): if
`tortoise_get_entity(type="events", limit=N)` returned a `total` envelope or silently
clamped `limit`, reconciling against `counts.Event` would refuse correct reads.
Opened and read at `87c07462a8`: the return is a bare `list`, `limit` passes through to
`LIMIT $lim` unclamped. The falsifier does not fire.

Confidence: high — every load-bearing claim is a locator with a quoted line, and the
shape being mirrored already landed for operators in PR #12380.

## Alternatives considered

1. **Read the layer through `tortoise_query(kind="event")`.** Rejected on measurement:
   `tortoise_query` has no arm that reaches Events (the same finding as the operator
   layer: `kind="operator"` answers `total: 0`), and it takes no `limit` large enough
   to cover the layer. It is the escape hatch `NOT_ENUMERABLE["operator"]` used to
   name, and it was false there too.
2. **Keep counting from `counts.Event` and leave the read out.** This is the status
   quo and it is exactly the "silent cap" defect class: a number with no per-kind
   resolution and no per-row reason, in a report that presents itself as the census.
   Rejected — it is also the specific omission #12378 was filed for.
3. **Read via `tortoise_get_events` (the older name).** Rejected: retired at the
   server (`tool_registry.py`'s retirement map, #3883); the advertised path is
   `tortoise_get_entity(type="events")`.
4. **CHOSEN: mirror the operator read** — one routed call at a declared ceiling,
   reconciled against `counts.Event`, with the same three fail-closed arms and the
   same tri-state reporting, including on the disarmed arms. It is the pattern the
   codebase already carries, so it is reviewable against a landed precedent rather
   than against a new one.

## Complexity + domains

`complexity: standard`. Domains: `code` (reader + two verbs), `docs-product` (the
curation architecture doc states the omission). No MCP/SDK surface change: the tool
and its `graph` argument already exist, so the surface-approval rule is not engaged.

## Integration Surface Map

| Surface | Test layer |
|---|---|
| `MCPReader.read_events` (new) | unit, via the fake `_live_call_tool` surfaces |
| `cmd_inventory` payload | integration over `main([...])` with a fake surface |
| `cmd_plan` payload + disarmed arms | integration over `main([...])` |
| `_disarmed_artifact` field set | unit (both stop points) |
| `C.Row` → `classify_event` → `census.by_class` | integration (already covered upstream) |
| `NOT_ENUMERABLE` + `docs/architecture/CURATION-11469.md` | assertion + doc text |
| pre-existing fake surfaces in `tests/test_curation_11469.py` | regression (blast radius) |

## Research

Pointer: the `<!-- research: v1 -->` comment on #12378. Decisions it supports:

- reconciliation target is `counts.Event` (the tool returns a bare list, so there is
  no `total` key to prefer) — `mcp_server.py:5422`;
- the truncation arm is load-bearing because the read has `LIMIT` and no offset —
  `sdk.py:49054`, <https://docs.falkordb.com/cypher/return.html>;
- identity is `id`, with `eventId` as the fallback spelling — `entities.py:5769`;
- `content := name`, with `title` then `subject` as declared fallbacks —
  `entities.py:1958` (`name` is persisted *because* it is absent from `_EVENT_HANDLED`;
  meetings persist `title`);
- the read is routable — `tool_registry.py:2030`, and `tortoise_get_entity` is absent
  from `GRAPH_ARGUMENT_EXCLUDED` (`:1665`);
- no classifier or census change is needed — `curation.py:548`, `:618`.

## Wiring

| Call site / consumer | What it does there |
|---|---|
| `tools/curate_graph.py::MCPReader.read_events` | **new**: reconciles, refuses, maps rows to `C.Row(node_type="event")` |
| `tools/curate_graph.py::MCPReader._get_entities` | **new**: the routed call (`type="events"`, `limit=EVENT_READ_CEILING`, `graph`), with the graph-refusal fallback every other read has |
| `tools/curate_graph.py::cmd_inventory` | reads the layer, feeds `C.build_plan`, emits `events_read` / `events_total_reported` / `events_reconciled` |
| `tools/curate_graph.py::cmd_plan` | same, plus a **fresh** status read immediately before the Event read (narrows the count-vs-rows window to two requests) |
| `tools/curate_graph.py::_disarmed_artifact` | new `events` tri-state kwarg; `events_*` present on **every** disarmed arm (`None` = no completed read) |
| `tools/curate_graph.py::NOT_ENUMERABLE` | `"event"` entry replaced by a rationale comment: the layer is now read |
| `tortoise/curation.py::classify_event` | **unchanged** — receives rows it already classifies `undecidable` |
| `tortoise/curation.py::Plan.census` | **unchanged** — `by_class` derives `event:<kind>` from `node_type`+`kind` |
| `docs/architecture/CURATION-11469.md:49,75` | rows say "deliberately does not read it"; both updated or they become false |
| `tests/test_curation_11469.py::_with_operator_count` | extended to answer the Event read and `counts.Event` for every **pre-existing** fake (blast radius: ~40 fixtures) |

## Implementation steps

1. `EVENT_READ_CEILING` + `read_events` + `_get_entities`. **Test:**
   `test_the_event_layer_reaches_the_census` (`event:AgentSession` / `event:meeting`
   in `by_class`, `events_read == counts.Event`).
2. Content lane: `content := name`, fallbacks `title` then `subject`. **Test:**
   `test_an_events_text_is_its_name` (two Events sharing a `subject` stay distinct).
3. Identity: `id`, fallback `eventId`; a row with neither refuses. **Test:**
   `test_an_event_row_with_no_identity_is_refused`.
4. Guard arms, each with its own test: above ceiling; fills the limit with nothing
   vouching; zero rows with no count; count/rows disagreement; unreported count over a
   non-empty read is a `read_note`, not a refusal.
5. Routing: `graph` sent; a graph-BOUND credential falls back and discloses. **Test:**
   `test_the_event_read_is_routed_to_the_graph` + `…_falls_back_and_discloses`.
6. Wire both verbs; emit the three fields on both payloads **and** on every disarmed
   arm. **Test:** disarmed tri-state on both stop points.
7. Adapter: extend `_with_operator_count` so pre-existing fakes answer `events` +
   `counts.Event`. **Test:** the existing suite stays green (this is the blast radius).
8. `NOT_ENUMERABLE` + `docs/architecture/CURATION-11469.md`. **Test:**
   `test_the_event_layer_is_no_longer_claimed_unreadable` (asserts `"event"` absent and
   `object`/`subject` still named) + the doc diff.

## Rounds

**Rounds:** pending (filled at PLAN-VERIFY close)

### Review Scope Contract
**Class:** code

**Risk surface** — what this change can break, each with the check that would catch it:

- R1 **A truncated Event layer enters the census as complete.** → the fills-the-limit
  and above-ceiling arms + a mutation proof that deleting either arm reds its test.
- R2 **A correct read is refused** (false block: count/rows disagree legitimately). →
  the exact-equality arm is a *passing* test with the reported count equal to the rows,
  and the refusal text names its causes so a real refusal is attributable.
- R3 **The census key shape shifts** for consumers (`event:<kind>`). → a test asserting
  `by_class` keys and `total_rows` for a zero-Event graph are unchanged.
- R4 **Every pre-existing fake surface breaks** (new required read → rc 3). → the full
  suite run through `worktree_report.py run`; the adapter change is what keeps it green.
- R5 **An unrouted read mixes two graphs.** → routing test asserting the `graph`
  argument is sent, plus the bound-credential fallback disclosure test.
- R6 **Disarmed runs lose the new fields** (the round-2/3 defect class on the operator
  layer: the disarmed payload lacked the layer fields). → tri-state test on both
  disarmed stop points, `None` distinct from `(n, m)`.
- R7 **Doc drift**: `CURATION-11469.md` still says the pass deliberately does not read
  the layer. → updated in the same commit; the doc's own row cites #12378.

**Out of scope — must NOT be raised** (note, file, do not chase):

- Where the episodic tier belongs (#10000 / D1) — a decision, not a read bug.
- `Object` / `Subject` enumeration (~5,379 + 26 nodes, no listing surface) — unchanged
  and still named in `NOT_ENUMERABLE`.
- Any MCP/SDK surface change — none is proposed; `tortoise_get_entity` already exists.
- Style preference inside the reader, or the shape of pre-existing fixtures.

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the
declared surface.
