<!-- research-path: issue #12378 comment carrying `<!-- research: v1 -->` (posted 2026-11-13) -->
<!-- plan-review: cycles=3, status=capped, version=2.3.0 -->

# #12378 — read the Event layer the way the operator layer is now read

## Confirmed problem

`tools/curate_graph.py` performs the #11469 census over Points, Sources and (since
PR #12380) the operator layer. The **Event layer — 4,670 rows on `live-v4`, exactly
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
| `_disarmed_artifact` field set | unit (**three** stop points, incl. the ledger arm) |
| `C.Row` → `classify_event` → `census.by_class` | integration (already covered upstream) |
| `NOT_ENUMERABLE` + `docs/architecture/CURATION-11469.md` | assertion + doc text |
| pre-existing fake surfaces in `tests/test_curation_11469.py` | regression (blast radius) |

## Research

Pointer: the `<!-- research: v1 -->` comment on #12378. Decisions it supports:

- reconciliation target is `counts.Event` (the tool returns a bare list, so there is
  no `total` key to prefer) — `mcp_server.py:5422`; the forwarding hop is
  `tortoise_get_entity` → `tortoise_get` (`:5574`) → the `t == "events"` arm (`:6094`)
  → `:6096` calls `tortoise_get_events`;
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
| `tools/curate_graph.py::MCPReader._get_entities` | **new**: the routed call (`type="events"`, `limit` = `EVENT_READ_CEILING + 1` — one request, **never sized to the count** (see step 1); `graph`), with the graph-refusal fallback every other read has |
| `tools/curate_graph.py::cmd_inventory` | reads the layer, feeds `C.build_plan`, emits `events_read` / `events_total_reported` / `events_reconciled` |
| `tools/curate_graph.py::cmd_plan` | same, plus a **fresh** status read immediately before the Event read (narrows the count-vs-rows window to two requests) |
| `tools/curate_graph.py::_disarmed_artifact` | new `events` tri-state kwarg; `events_*` present on **every** disarmed arm (`None` = no completed read) |
| `tools/curate_graph.py::NOT_ENUMERABLE` | `"event"` entry replaced by a rationale comment: the layer is now read |
| `tortoise/curation.py::classify_event` | **CHANGED** (round-1 correction): the session-kind test is case-insensitive — the write path stores `AgentSession` while the arm tested lower-case `agentSession`, so making the layer reachable gave all 4,670 session rows a false reason. The census key keeps the original capitalisation |
| `tortoise/curation.py::Plan.census` | **unchanged** — `by_class` derives `event:<kind>` from `node_type`+`kind` |
| `docs/architecture/CURATION-11469.md:49,75` | rows say "deliberately does not read it"; both updated or they become false |
| `tests/test_curation_11469.py::_with_operator_count` **and** `::_operator_surface` | **both** extended to answer the Event read and `counts.Event` for every **pre-existing** fake (blast radius: the file's 35 inline fakes and 48 `main([...])` call sites; `_operator_surface` cannot be wrapped, so it answers the read itself) |

## Implementation steps

1. `EVENT_READ_CEILING` + `read_events` + `_get_entities`. **Test:**
   `test_the_event_layer_reaches_the_census` (`event:AgentSession` / `event:meeting`
   in `by_class`, `events_read == counts.Event`).

   ⛔ The bound is **OURS, not the surface's**: `get_events` validates no upper `limit`
   (it goes straight into the Cypher `LIMIT`), so declaring it as "the surface's maximum"
   would be false — and the refusal's stated cause would not bind. The read is therefore
   **sized to `EVENT_READ_CEILING + 1`**, and the bound is only consumed by a layer that
   genuinely has no whole read. The refusal says exactly that: a size bound on ONE
   response, raise it deliberately if the layer legitimately outgrew it.

   ⛔ The request size is what makes an accept SOUND, and it took three revisions to get
   right. `limit` goes straight into the Cypher `LIMIT`, so a response **shorter** than
   the request IS the exact layer, and a response that **fills** the request is "at least
   this many" and indistinguishable from a truncation.
   - `min(reported + 1, EVENT_READ_CEILING)` (original) ate the headroom exactly AT the
     bound: a layer reporting 10,000 that had grown to 10,050 returned 10,000 rows == the
     count and was filed `events_reconciled: True` with 50 rows unread.
   - `reported + 1` (PR-review cycle 1) left ONE row of headroom, so a layer that grew by
     a single Event FILLED the request — every growth became truncation-shaped, and a
     grow-then-shrink-back race was accepted as whole over unread rows (cycle 4,
     reproduced: 4 live rows unread, 4 deleted rows reported).
   - `EVENT_READ_CEILING + 1` pays the cost ONCE, at the bound: every response below the
     bound is provably whole at ANY size, and a response that reaches the bound is above
     what one response may accept and refuses.
   ⛔ A disagreement in **EITHER direction is re-counted once** before refusing
   (finding 6, made symmetric in PR-review cycle 2): `counts.Event` is fed by
   `index_sessions` (growth) and `:Event` is deleted by the hosted entity-delete path
   (shrink), so a layer that changed between the two requests is a CORRECT read and
   refusing it is a false block. A fresh count equal to the rows accepts in either
   direction (and says so on `read_note`); anything else still refuses — and a filled
   request refuses even when the fresh count agrees, because equality cannot prove
   wholeness there.
2. Content lane: `content := name`, fallbacks `title` then `subject`; rows carrying
   none are COUNTED on `read_note`. ⚠️ Round-2 correction: the note says only what is
   TRUE — those rows are keyed `node_type:kind:sha`, so **same-kind** ones share a
   `stable_key` in `--rows`, and **no duplicate claim is made for them** (`classify_event`
   takes no `duplicate_of`/`keeper_id`, every Event arm is `undecidable`, `Decision` has
   no duplicate field). A first draft claimed the plan's duplicate fold reported them;
   measured, it never does. **Test:**
   `test_an_events_text_is_its_name_with_title_and_subject_fallbacks` +
   `test_an_event_with_no_text_is_counted_on_the_report` (which reds if the false claim
   returns).

   ⚠️ **`classify_event` is NOT unchanged** (round-1 correction): it tested the session
   kinds case-SENSITIVELY against lower-case `agentSession`, while the write path stores
   `AgentSession`. Unreachable while nothing read the layer; reachable the moment it is
   read, and then all 4,670 agent-session rows get a false per-row reason — which is the
   one thing reading the layer buys. **Test:**
   `test_an_agent_session_event_gets_the_session_reason_not_the_domain_one`.
3. Identity: `id`, fallback `eventId`; a row with neither refuses. **Test:**
   `test_an_event_row_with_no_identity_is_refused`.
4. Guard arms, each with its own test: above ceiling; fills the limit with nothing
   vouching; zero rows with no count; count/rows disagreement; unreported count over a
   non-empty read is a `read_note`, not a refusal.
5. Routing: `graph` sent; a graph-BOUND credential falls back and discloses. **Test:**
   `test_the_event_read_is_routed_to_the_graph` + `…_falls_back_and_discloses`.
6. Wire both verbs; emit the three fields on both payloads **and** on all THREE disarmed
   call sites. **Test:** disarmed tri-state on the failed-read arm AND the ledger arm.
7. Adapter: extend **BOTH** `_with_operator_count` and `_operator_surface` so pre-existing
   fakes answer `events` + `counts.Event`. **Test:** the existing suite stays green (this
   is the blast radius).
8. `NOT_ENUMERABLE` + `docs/architecture/CURATION-11469.md`. **Test:**
   `test_the_event_layer_is_no_longer_claimed_unreadable` (asserts `"event"` absent and
   `object`/`subject` still named) + `test_the_architecture_doc_no_longer_claims_the_event_layer_is_unread`
   (asserts the doc's two old sentences are gone and the row cites #12378) — so the doc
   drift is PINNED rather than remembered.

## Rounds

**Rounds:** 3 (verifiers: 1 per round, class: code) — round 1 returned 5 issues, round 2
returned 6 (two of them round-1 fixes that had only half-landed), and round 3 returned 4,
all documentation/arithmetic. Every finding was verified against source before being
applied. ⚠️ **Disclosure: the tier's cycle cap for `standard` is 3, so the round-3 fixes
have NO fourth confirmation round at this gate** — the fixes are pinned by tests that
red when reverted, and the change goes to the code-review gate (a separate, fresh
budget) at the final head. This gate exits CAPPED, not clean.

### Review Scope Contract

**Class:** code

**Risk surface** — what this change can break, each with the check that would catch it:

- R1 **A truncated Event layer enters the census as complete.** → the fills-the-limit
  and above-ceiling arms + a mutation proof that deleting either arm reds its test.
- R2 **A correct read is refused** (false block: count/rows disagree legitimately). →
  the exact-equality arm is a *passing* test with the reported count equal to the rows,
  and the refusal text names its causes so a real refusal is attributable.
- R3 **The census key shape shifts** for consumers (`event:<kind>`). → step 1's assertion
  on the census keys (`event:AgentSession` / `event:meeting` / `event:deployment`), which
  is the check that actually discriminates. ⚠️ The zero-Event-graph assertion alone is
  INERT for this risk — with no event rows a broken key leaves it green — so it is carried
  as the no-regression half only.
- R4 **Every pre-existing fake surface breaks** (new required read → rc 3). → the full
  suite run through `worktree_report.py run`. ⚠️ **BOTH** fixture builders must be
  extended, not one: `_with_operator_count` (the wrapper most pre-existing fakes go
  through) **and** `_operator_surface`, whose own docstring says operator-read fixtures
  must not be wrapped — so it has to answer the Event read itself. The file carries 35
  inline `def call(...)` fakes and 48 `main([...])` call sites; those that reach the
  census armed are the blast radius.
- R5 **An unrouted read mixes two graphs.** → routing test asserting the `graph`
  argument is sent, plus the bound-credential fallback disclosure test.
- R6 **Disarmed runs lose the new fields** (the round-2/3 defect class on the operator
  layer: the disarmed payload lacked the layer fields). → tri-state tests on the disarmed
  stop points, `None` distinct from `(n, m)`. **Three** call sites in `cmd_plan`, not two:
  a refused census read, an unexpected exception, and a refused/damaged LEDGER — a
  two-arm test stays green with the ledger arm silently dropping `events_*`.
- R7 **Doc drift**: `CURATION-11469.md` still says the pass deliberately does not read
  the layer. → updated in the same commit, **and pinned**: a test asserts neither
  "this pass does NOT read it" nor "counted here, NOT enumerated" survives in the doc,
  so a future revert of the row reds rather than drifting.

**Out of scope — must NOT be raised** (note, file, do not chase):

- Where the episodic tier belongs (#10000 / D1) — a decision, not a read bug.
- `Object` / `Subject` enumeration (~5,379 + 26 nodes, no listing surface) — unchanged
  and still named in `NOT_ENUMERABLE`.
- Any MCP/SDK surface change — none is proposed; `tortoise_get_entity` already exists.
- Style preference inside the reader, or the shape of pre-existing fixtures.

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the
declared surface.
