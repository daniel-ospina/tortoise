---
title: "Event Subscriptions + Claim Lifecycle — Implementation Plan (#432)"
type: engineering
domain: engineering
doc_status: draft
created: 2026-08-08
subjects.team: epistemic-team
---

<!-- research-path: docs/research/2026-08-08-event-log-subscriptions.md -->

# Event Subscriptions + Claim Lifecycle — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Status:** Draft (pre plan-review — signature added by `plan-review` when clean)
**Complexity:** Standard (tier=Standard)
**Issue:** [#432 — subscriptions + claim lifecycle](https://github.com/daniel-ospina/tortoise/issues/432)
**Prerequisites:** Approved scoping (`docs/scoping-432-subscriptions-claim-lifecycle.md`, Approach A: durable event substrate + cursor-based poll; SDK-first state model; tombstone retraction). Research verified CLEAN after 2 review cycles (`docs/research/2026-08-08-event-log-subscriptions.md`) — **event store = FalkorDB `:GraphEvent` nodes in the team's graph namespace, NOT container JSONL.**
**Related issues (do NOT absorb):** #488 (EventLog `read_after` primitive — the poll cursor here reads FalkorDB event nodes, not the CLI EventLog), #689 (tombstone data-loss bug — partially fixed by the retraction-tombstone work in this plan), #690 (status vocabulary drift — this plan implements the corrected vocabulary).

**Written:** 2026-08-08

**Goal:** Make claim state transitions real and observable — a tenant can poll a durable, team-scoped event stream for graph-change and claim-lifecycle notifications (REST + MCP) without full-graph polling, and retraction becomes a tombstone instead of data loss.

**Team:** epistemic-team
**Role:** (not set)

**Architecture:** A single SDK emit hook (all surfaces — MCP, hosted REST, local — route through `TortoiseSDK`) writes `:GraphEvent` nodes `{seq, ts, type, payload, event_id}` — **no `team_id` property (plan-review P2)** — into the team's existing FalkorDB graph namespace (durability from FalkorDB Cloud AOF+backups; zero relationships so the nodes are invisible graph islands to domain queries, traversals, and EP; the per-team graph namespace IS the partition — isolation comes from `_make_sdk(namespace=team_id)`, never from a property). Claim state vocabulary becomes `draft → live → retracted → superseded` (plus `outdated`/`archived`; `challenged` stays edge-derived), retraction tombstones instead of hard-deleting in the projection, and a cursor-based poll surface (`GET /v1/events` + MCP `tortoise_events_poll`) reads the stream with at-least-once + event_id dedup, opaque cursors, 410 expiry, and 30-day retention.

---

## 1. Problem Statement

Hosted/SDK mutations emit **no events at all**: `TortoiseSDK.create_point/update_point/supersede_point/create_operator/annotate_operator` (sdk.py:382, :557, :695, :794, :857) write Cypher directly to the FalkorDB graph — no change record other than node `updatedAt`. The only durable graph event stream is the CLI/ingest path (`EventAPI._emit` → `EventLog`, api.py:42-52, log.py) that hosted tenants never touch. **A subscription mechanism with no event source is a notification endpoint with nothing to notify.**

Two compounding defects (verified in this worktree):

1. **The claim lifecycle is internally inconsistent.** Two divergent vocabularies coexist: EventAPI births points `status:'live'` (api.py:67) and `PointRetracted` **deletes** the point from the projection (`points.pop(ev["id"])`, projection/__init__.py:125-126); SDK births points `status:'draft'` (sdk.py:455), promotes to `'live'` on first operator edge (sdk.py:849), and validates against `POINT_STATUS_VALUES = {live, draft, outdated, archived}` (sdk.py:27) — **no `retracted`, no `superseded` as statuses, and the SDK has no `retract_point` method at all** (only EventAPI api.py:185).
2. **Retraction is data loss.** `_apply_one` hard-deletes retracted points on fold/replay; existing graphs that retracted content have lost it irrecoverably (#689).

The issue's indicator 3 ("integrate with shared_state event infra") is half-right: `EventCodec` (events.py:31/45) is real but serves only the PM/card domain — no graph/claim event flows through it.

---

## 2. Proposed Solution

**Approach A (converged): durable event substrate + cursor-based poll, SDK-first state model, tombstone retraction.** Build in this order — substrate before surface:

1. **Claim-state model** — extend `POINT_STATUS_VALUES` to `{draft, live, retracted, superseded, outdated, archived}` (**NO `challenged` — it is a DERIVED condition**, emerging from NAND-operator-edge presence on a live point, per user decision 2026-08-08); add `sdk.retract_point`; transition guards in `update_point`/`retract_point`/`supersede_point`; EventAPI parity (`_point` default live→draft).
2. **Retraction tombstone** — `_apply_one` keeps the point with `status:'retracted'` instead of `points.pop`; audit retraction-as-absence consumers (`split`, query filters, `list_points`).
3. **Durable event emission (the root-cause fix)** — a single SDK emit hook on `create_point`/`create_operator`/`retract_point`/`supersede_point`/`annotate_operator` writes **`:GraphEvent` nodes** `{seq, ts, type, payload, event_id}` — **no `team_id` property** (the per-team graph namespace IS the partition) — into the team's graph namespace (FalkorDB Cloud durability — AOF + backups), **zero relationships** (graph islands — invisible to label-scoped queries, traversals, EP), unique constraint on `event_id` (storage-layer dedup), plain index on `seq` (cursor reads), append-before-mutation, per-graph monotonic `seq` (atomic in-graph counter). EventAPI/EventLog JSONL path stays as-is.
4. **EventCodec registration** of claim event types (`PointAdded`, `OperatorAdded`, `PointRetracted`, `PointSuperseded`, `OperatorAnnotated`) — **registration now, codec encode/decode wiring deferred** to the task that ships the first real upcaster (Task 4). `ClaimStateChanged` is **DROPPED (plan-review P1)**: no code path emits it — every claim transition is observable via one of the five emit hooks.
5. **Subscription surface** — REST `GET /v1/events?after=<cursor>&types=<filter>` (team-scoped auth) returning `{events[], next_cursor}`; opaque cursor encoding `seq`; at-least-once + event_id dedup (unique constraint + `recovery.dedup_events`); expired cursor → **410 "replay from tail"**.
6. **MCP tool** `tortoise_events_poll` (readOnlyHint, registered in `tool_registry.py` with `http_policy=True` → auto-included in `HTTP_ALLOWED`).
7. **Retention** — 30-day Cypher DELETE (boot + config-driven interval) + size cap.

Out of scope (documented in scoping): MCP push notifications (FastMCP 3.4.6 gap), webhook delivery, SSE stream (additive, future), arbitrary Cypher-query filters, migration of historical retracted points.

### UX Design Decisions

UX gate **SKIPPED** (workflow/01.5): `UX_RATING = low` — no user-visible UI; two new developer/API surfaces (REST endpoint + MCP tool). All UX decisions were made during issue-scoping. No checklist items apply.

| # | Decision Type | User Choice | Rationale |
|---|---|---|---|
| — | UX gate | Skip | UX_RATING=low, zero UI files touched |

---

### Pattern Research

**Library docs (preflight)** — FalkorDB (in-repo via `redislite.falkordb_client` / FalkorDB Cloud; no pinned version in requirements.txt — connect via `TORTOISE_DB_URI`). context7 unavailable in this session → used Perplexity `web_search` (3 framings, distinct invocations) + fetch of the official `GRAPH.CONSTRAINT CREATE` doc page.

**Library version & API surface** — 3+ calls (official docs + FalkorDB blog)
- **Canonical:** Unique-constraint syntax is `GRAPH.CONSTRAINT CREATE <key> UNIQUE NODE <label> PROPERTIES <propCount> <prop...>` (e.g. `GRAPH.CONSTRAINT CREATE g UNIQUE NODE Person PROPERTIES 1 email`); `db.constraints` procedure lists created constraints. **A unique constraint REQUIRES an exact-match index to exist prior to its creation**; deleting the supporting index fails while the constraint exists. Source: docs.falkordb.com/commands/graph.constraint-create.html (fetched in full).
- **Competitor variance:** Composite indexes are supported: `CREATE INDEX FOR (n:User) ON (n.lastName, n.firstName)` (FalkorDB blog Cypher cheatsheet). The repo already creates indexes this way (sdk.py:336, wrapped in try/except).
- **Known pitfall:** A unique constraint is enforced **only when all constrained properties are non-null** — a node missing a constrained property does not violate uniqueness; array-valued properties are not covered (official docs). FalkorDB issue #664 reports crash/undefined behavior around unique constraints on string properties in some versions → create constraints defensively (try/except + `db.constraints` verification) and never rely on the constraint alone for dedup on read (keep `recovery.dedup_events`).

**Idiomatic usage patterns** — 3+ calls (FalkorDB docs + Neo4j/Cypher community)
- **Canonical:** Pagination = `ORDER BY` + `SKIP`/`LIMIT`; without `ORDER BY` result order is non-deterministic (docs.falkordb.com/cypher/skip.html). For change-feed reads, cursor-based range scans (`WHERE n.seq > $after ORDER BY n.seq LIMIT $limit`) are the recommended pattern for large offsets — deep `SKIP` is not optimized (Neo4j issue #12695, StackOverflow).
- **Competitor variance:** Cursor-based pagination with a unique sort key + filtering on already-retrieved values is the consensus best practice for large result sets (microservices/GraphQL cursor conventions).
- **Known pitfall:** `SKIP` is not efficient for large offsets — the engine still scans past skipped rows. Hence the plan's cursor = range scan on the plain `seq` index (per-graph = per-team), never offset pagination.

**Library/framework pitfalls** — 2 calls (FalkorDB known-limitations + issue #664)
- **Canonical:** FalkorDB `NULL` = missing/undefined value; constraints skip missing properties. Label-scoped `MATCH` scans only that label; isolated (zero-relationship) nodes do not affect pattern queries that require relationships, counts, or traversal — confirming the `:GraphEvent` graph-island guard.
- **Competitor variance:** N/A (graph-island behavior is inherent to Cypher pattern matching; corroborated by FalkorDB GraphRAG storage docs re: label hints).
- **Known pitfall:** unique-constraint crashes on string props reported (FalkorDB #664) — constraint creation must be non-fatal to boot, verified post-creation, and the read path must not assume storage-level dedup alone. `FalkorDBLite` (embedded) lacks `dropIndex` (projection/__init__.py:763 comment) — schema creation must be idempotent try/except (mirror sdk.py:336).

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | `POINT_STATUS_VALUES` + transition guards (`update_point`/`retract_point`/`supersede_point`, sdk.py) | State mutation | Internal | Unit | `{draft,live,retracted,superseded,outdated,archived}`; illegal transition → `ValueError`; retracted terminal | live→draft guard; unknown status; supersede keeps `outdated=true` back-compat |
| 2 | Projection `_apply_one` PointRetracted tombstone (`fold`/rebuild) | State mutation | Internal | Unit | retracted point kept with `status:'retracted'`; **re-folding a pre-change log under new code yields TOMBSTONES** (intended, tested behavior change — Task 2); `PointsMerged` + retract interaction pinned by test | old-log re-fold semantics; `PointsMerged` + retract interaction |
| 3 | Retraction-as-absence consumers (`split` projection/__init__.py:140, `query`/`paginated_query` sdk.py:933, `list_points` hosted_api.py:829) | State | Read | Unit + Integration | retracted excluded by default, queryable with filter (`include_retracted`/`status=` where feasible) | `split` returns retracted statements; `/v1/points` leaks retracted; `check_structure` orphaned_draft unaffected |
| 4 | SDK emit hook → `:GraphEvent` nodes | DB write (FalkorDB) | Out | Integration (FalkorDBLite) | node `{seq, ts, type, payload, event_id}` (**no `team_id` — graph namespace is the partition**); **zero relationships**; append-before-mutation; dedup=true create returns existing point **without** emitting; duplicate `event_id` append → caught+skipped (client-retry artifact) | append failure → missing event; seq race; phantom event on failed mutation; duplicate event on client retry |
| 5 | `:GraphEvent` schema (exact-match index + unique constraint on `event_id`; plain index on `seq`) | DB schema | — | Integration | idempotent creation; index-before-constraint ordering; `db.constraints` verification | constraint-requires-index error; FalkorDB #664 crash; FalkorDBLite gaps |
| 6 | `shared_state/events.py` registration (five claim event types — **no `ClaimStateChanged`**) + upcasters | Event infra | In/Out | Unit | registration + `EventCodec.encode/decode` round-trip at codec level; **encode/decode wiring deferred** to first-upcaster task | unregistered type raises; upcaster ordering |
| 7 | REST `GET /v1/events` (team-scoped auth, `get_current_team`) | API + Auth | In | Integration (hosted test client) | `{events[], next_cursor}`; 410 on expired cursor; **tenant isolation (team A can't read team B)** | cross-team leak; expired cursor; empty graph; types filter; limit bounds |
| 8 | MCP `tortoise_events_poll` (stdio + HTTP) + `tortoise_retract_point` (HTTP_ALLOWED, readOnly/destructive hints) | API + Auth | In | Integration (MCP client stdio + HTTP) | readOnlyHint on poll; `http_policy=True` in registry → auto `HTTP_ALLOWED`; stdio+HTTP parity; team scoping via `_get_team_sdk` | tool excluded from HTTP; hint missing; introspective quota test (test_mcp_http.py:606) drift |
| 9 | Retention purge (Cypher DELETE, boot + interval, config-driven) | DB write | Out | Integration | events older than `TORTOISE_EVENT_RETENTION_DAYS` deleted; size cap; idempotent | purge races poll; cap triggers cursor expiry mid-page |
| 10 | EventAPI parity (`_point` default live→draft, api.py:67) | State | Internal | Unit + regression | parity with SDK default; CLI/ingest blast radius checked | existing EventAPI tests/consumers break |

**Bug Pattern Flags**
- **Race conditions** (seq counter + append + concurrent polls): verify per-team `seq` monotonicity under concurrent SDK writes (atomic in-graph counter; test with concurrent appends).
- **Silent function skips** (emit hook): every mutation must produce a `:GraphEvent` node — test asserts graph contains the event after each mutation type; a skipped emission must fail the test.
- **Conditional guards** (transition guards): boundary tests on both sides of every guard branch.
- **DB business logic in Cypher** (constraint/index/retention/seq): graph-level integration tests against a REAL FalkorDBLite graph (the repo's pgTAP analog) — mock-free for the DB surfaces above.

**Checklist Notes**
- Empty vs null: `after=None` (tail) vs expired cursor vs empty graph must behave distinctly (410 vs empty batch).
- Atomicity: append-before-mutation + idempotent schema creation; document at-least-once (clients idempotent on replay) as the contract.
- Ordering: `seq` is the sole ordering key; `ts` is informational.

### Journey Test Map

**Journey 1: Tenant observes claim lifecycle changes**
1. SDK creates a point → **Acceptance:** `:GraphEvent` node exists with type `PointAdded`, zero relationships → **Test:** `tests/test_event_store.py::test_emit_on_create_point`
2. Tenant polls `GET /v1/events?after=<cursor>` → **Acceptance:** receives exactly the events appended after cursor, ordered by seq, dedup'd → **Test:** `tests/test_subscriptions.py::test_poll_after_cursor`
3. Point retracted → **Acceptance:** `PointRetracted` event emitted AND `get_point` returns `status:'retracted'` (not 404) → **Test:** `tests/test_claim_lifecycle.py::test_retract_tombstone`
4. Retracted point excluded from default queries → **Acceptance:** `query()` omits it; `include_retracted` surfaces it → **Test:** `tests/test_claim_lifecycle.py::test_retracted_excluded_from_queries`
5. Cursor expires (retention purge) → **Acceptance:** poll returns 410 with "replay from tail" hint; `after=None` resumes from tail → **Test:** `tests/test_subscriptions.py::test_expired_cursor_410`

**Journey 2: Cross-tenant isolation**
1. Team A and team B both mutate graphs → **Acceptance:** A's poll cursor never returns B's events → **Test:** `tests/test_subscriptions.py::test_tenant_isolation`

**Failure Modes**
- Duplicate event append (client retry) → **Expected:** unique constraint on `event_id` rejects; append catches + skips (no-op); read path dedups → **Test:** `tests/test_event_store.py::test_append_duplicate_event_id_rejected` + `test_read_dedups_duplicate_event_ids`
- Illegal status transition `live→draft` → **Expected:** `ValueError` → **Test:** `tests/test_claim_lifecycle.py::test_transition_guards`
- Mutated graph with no event (emit skipped) → **Expected:** test fails (silent-skip flag) → **Test:** per-mutation event-presence assertions

### Verification Plan

**Domain(s):** code (Python SDK + FalkorDB + REST/MCP surfaces). **Complexity:** Architecture=medium, Ontology=medium-high, UX=low, Accessibility=low.

| # | Skill/Layer | Depth | Reason |
|---|-------------|-------|--------|
| 1 | pytest unit | Full | Status model, transition guards, EventCodec round-trip, cursor encode/decode |
| 2 | pytest integration | Full | DB surfaces (event emission, schema, retention) on FalkorDBLite; REST poll via hosted test client; MCP stdio+HTTP |
| 3 | Regression | Full | `python -m pytest tests/ -v` green at each gate (existing suite: test_api, test_projection, test_mcp_*, test_hosted_api) |
| 4 | UX | Skip | No UI changes |
| 5 | Config | Skip | No config-file changes (new env vars documented, defaulted) |
| 6 | Research | Skip | FalkorDB facts verified in this plan's Pattern Research |

**Tech Stack:** Python 3.11+, pytest, FalkorDB (`redislite.falkordb_client` / FalkorDB Cloud), FastMCP 3.4.6 (pinned), FastAPI (hosted_api), `shared_state` (EventCodec, dedup_events).

---

## 3. Implementation Plan

> Commits are listed per task for TDD rhythm but the **commit-workflow skill gates every actual commit** (AGENTS.md hard rule). Executor: use `executing-plans`; each task's **Acceptance** is the Step 2.5 fidelity gate.

### Task 1: Claim-state model — vocabulary + transition guards + EventAPI parity

**Intent:** Make the claim lifecycle a real, enforced state machine. State vocabulary is `draft → live → retracted → superseded` (plus existing `outdated`, `archived`); **`challenged` is NOT a state** — it is a derived condition (NAND-operator edge present on a live point), documented in ontology §5. This is the substrate indicator 1 of #432.

**Acceptance:**
- `POINT_STATUS_VALUES` (sdk.py:27) = `frozenset({'draft', 'live', 'retracted', 'superseded', 'outdated', 'archived'})`; no `challenged` in the set.
- `sdk.retract_point(id)` exists; sets `status='retracted'` via a **single atomic guarded query** (plan-review P2); returns the updated point; raises `ValueError` if the point is missing, is an operator node, or is already terminal. **Terminal set = `retracted`/`superseded`/`archived`** — `deleted` is dropped (hard delete is `delete_point`, not a status); `archived` joins the set, aligned with the retract guard's `NOT IN` clause.
- `update_point` accepts **NO status changes except the draft→live promote (plan-review P1)**: `status='live'` is allowed only when the current status is `draft`/absent (matches the create_operator promote); any other `status` value → `ValueError` ("update_point only promotes draft→live — use retract_point()/supersede_point() for lifecycle transitions"). Non-status property/content edits remain unrestricted and emit nothing. `outdated` stays a settable boolean *flag* (not a status transition); `archived` remains in the vocabulary as a **reserved terminal state** (no v1 SDK write path — only direct graph writes), kept in the terminal set so retraction refuses it defensively.
- **Transition-observability note (plan-review P1):** `_ALLOWED_TRANSITIONS` still guards `retract_point`/`supersede_point`; `update_point` no longer accepts status changes (except draft→live promote) — so every claim transition is observable via one of the Task 3 emit hooks (`PointAdded`/`OperatorAdded`/`PointRetracted`/`PointSuperseded`/`OperatorAnnotated`), and no transition can slip through `update_point` unemitted.
- `supersede_point` sets old point `status='superseded'` **and retains** `outdated=true` (back-compat for consumers reading the flag).
- EventAPI `_point` (api.py:67) default `status:'live'` → `'draft'` (parity with SDK); all existing EventAPI tests updated to expect `draft`.
- `docs/ONTOLOGY.md` §4.1 status row updated to the new vocabulary; §5 documents `challenged` as NAND-edge-derived.

**Files:**
- Modify: `tortoise/sdk.py` (POINT_STATUS_VALUES :27, update_point :557-600, supersede_point :695, new retract_point near :646)
- Modify: `tortoise/api.py` (:_point default :67)
- Modify: `docs/ONTOLOGY.md` (§4.1 status row :201, §5 vocabulary)
- Modify: `tests/conftest.py` (new shared `sdk_factory` fixture — Tasks 1/2/3/5 use it instead of redefining `_sdk` three times; embeds the embedded-vs-docker concurrency note, plan-review P2)
- Test: `tests/test_claim_lifecycle.py` (new)

**Step 1: Write failing tests — `tests/test_claim_lifecycle.py`**

Uses the shared `sdk_factory` fixture from `tests/conftest.py` (created in this task — auto-discovered by pytest, no import needed):

```python
import pytest
from tortoise.sdk import TortoiseSDK, POINT_STATUS_VALUES


def test_status_vocabulary():
    assert POINT_STATUS_VALUES == frozenset(
        {"draft", "live", "retracted", "superseded", "outdated", "archived"}
    )
    assert "challenged" not in POINT_STATUS_VALUES


def test_transition_guards_live_to_draft(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "guarded")
    assert p["status"] == "draft"
    sdk.update_point(p["id"], status="live")  # draft→live promote still allowed
    with pytest.raises(ValueError, match="live"):
        sdk.update_point(p["id"], status="draft")  # any non-promote status rejected


def test_update_point_rejects_status_changes_except_promote(sdk_factory, tmp_path):
    # plan-review P1: update_point is non-status except draft→live promote
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "status-guard")
    for bad in ("retracted", "superseded", "outdated", "archived"):
        with pytest.raises(ValueError, match="retract_point|supersede_point"):
            sdk.update_point(p["id"], status=bad)


def test_retract_tombstone_status(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "tombstone-me")
    r = sdk.retract_point(p["id"])
    assert r["status"] == "retracted"


def test_retracted_is_terminal(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "terminal")
    sdk.retract_point(p["id"])
    with pytest.raises(ValueError):
        sdk.retract_point(p["id"])  # already retracted
    with pytest.raises(ValueError):
        sdk.update_point(p["id"], status="live")  # terminal → no promote


def test_retract_archived_is_terminal(sdk_factory, tmp_path):
    # plan-review P2 boundary: archived is terminal. v1 has no SDK path to
    # archived (reserved), so set it via the graph directly to exercise the guard.
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "archived")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='archived'", params={"id": p["id"]})
    with pytest.raises(ValueError, match="already"):
        sdk.retract_point(p["id"])


def test_retract_point_missing_and_operator_guards(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    with pytest.raises(ValueError, match="No point"):
        sdk.retract_point("does-not-exist")
    s = sdk.create_point("statement", "src")
    t = sdk.create_point("statement", "tgt")
    op = sdk.create_operator("IMPL", s["id"], [t["id"]])
    with pytest.raises(ValueError, match="operator"):
        sdk.retract_point(op["id"])  # operators are not retractable


def test_supersede_sets_status_and_keeps_flag(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    old = sdk.create_point("statement", "old")
    new = sdk.create_point("statement", "new")
    sdk.supersede_point(old["id"], new["id"])
    got = sdk.get_point(old["id"])
    assert got["status"] == "superseded"
    assert got.get("outdated") is True
```

**Step 2: Run — expect FAIL** (retract_point missing, vocabulary unchanged, supersede does not set status).

Run: `python -m pytest tests/test_claim_lifecycle.py -v` — Expected: FAIL (AttributeError / assertion errors).

**Step 3: Implement**

- sdk.py:27 → `POINT_STATUS_VALUES = frozenset({'draft', 'live', 'retracted', 'superseded', 'outdated', 'archived'})`.
- Add a module-level transition table in sdk.py near POINT_STATUS_VALUES — the **declarative spec for the retract/supersede guards + error messages** (plan-review P1: **not** consulted by `update_point` per-call):

```python
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"live"}),                        # promote via update_point/create_operator
    "live": frozenset({"retracted", "superseded"}),    # via retract_point / supersede_point
    "retracted": frozenset(),                            # terminal
    "superseded": frozenset(),                           # terminal
    "outdated": frozenset({"retracted"}),               # outdated stays a flag; retract allowed
    "archived": frozenset(),                             # terminal (reserved — no v1 write path)
}
```

- In `update_point`, keep the membership check against `POINT_STATUS_VALUES` (:576-579), then **restrict status writes to the draft→live promote** — the guard is folded INTO the update WHERE clause (plan-review P2: single round trip, no per-call transition-table fetch, no widened write window):

```python
# any status != 'live' → ValueError before the query:
#   "update_point only promotes draft→live — use retract_point()/supersede_point()"
now = datetime.now(timezone.utc).isoformat()
res = proj.g.query(
    "MATCH (n:Point {id:$id}) WHERE (n.status IS NULL OR n.status = 'draft') "
    "SET n.status = 'live', n.updatedAt = $now RETURN n",
    params={"id": id, "now": now})
if not res.result_set:
    exists = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)", params={"id": id}).result_set[0][0]
    if not exists:
        raise ValueError(f"No point {id!r}")  # match existing missing-point behavior
    raise ValueError(
        f"Illegal status transition — update_point only promotes draft→live; "
        f"use retract_point()/supersede_point() for lifecycle transitions")
```

The same WHERE guard applies to the :Object-labeled branch (:575-596) alongside its version bump. Non-status props bypass the guard entirely (plain SET, unchanged).
- New `retract_point` — **single atomic conditional query** (plan-review P2: one round trip on the happy path; trailing `get_point` dropped — the query RETURNs the updated node):

```python
def retract_point(self, id: str) -> dict:
    """Tombstone-retract a Point: status='retracted' (point stays in graph)."""
    from datetime import datetime, timezone
    proj = self._get_proj()
    r = proj.g.query(
        "MATCH (n:Point {id:$id}) "
        "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "AND (n.status IS NULL OR n.status NOT IN ['retracted', 'superseded', 'archived']) "
        "SET n.status = 'retracted', n.updatedAt = $now RETURN n",
        params={"id": id, "now": datetime.now(timezone.utc).isoformat()})
    if not r.result_set:
        # diagnostic read ONLY on the error path — happy path stays 1 round trip
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status",
            params={"id": id}).result_set
        if not row:
            raise ValueError(f"No point {id!r}")
        is_op, cur = row[0][0], row[0][1]
        if is_op:
            raise ValueError(f"Point {id!r} is an operator — retraction is for statement points")
        raise ValueError(f"Point {id!r} is already terminal ({cur!r}) — retraction is terminal")
    return r.result_set[0][0]  # updated node props (no trailing get_point round trip)
```

- `supersede_point`: add a transition guard (old point not an operator and not terminal per `_ALLOWED_TRANSITIONS` — `ValueError` otherwise, mirroring the retract guard; supersede is already multi-query, so the guard read is cheap) and `SET n.status='superseded'` alongside the existing `n.outdated=true` write (:720 area).
- api.py:67 `"status": "live"` → `"status": "draft"` in `_point`; update EventAPI-path tests that assert `live`.
- `docs/ONTOLOGY.md` §4.1 Point row: `Lifecycle: draft, live, retracted, superseded, outdated, archived. challenged is a derived condition (presence of a NAND operator edge on a live point), not a stored status (§5).` Add a §5 note documenting challenged-as-derived.

**Step 4: Run — expect PASS** for new tests; then run the full suite and fix EventAPI-parity fallout (`python -m pytest tests/ -v` — expect failures only in tests asserting `live` from EventAPI/_point paths; update those assertions to `draft`).

**Step 5: Commit** — `feat(432): claim state vocabulary + transition guards + EventAPI parity` (via commit-workflow skill).

---

### Task 2: Retraction tombstone in projection + retraction-as-absence consumer audit

**Intent:** Make retraction observable instead of a deletion — the fold/projection keeps the point with `status:'retracted'` (user decision #2). Partially fixes #689 (future retractions no longer destroy data). Consumers that relied on retraction-as-absence must be audited and, where the default-visible surface changes, excluded-by-default with an opt-in filter.

**Acceptance:**
- `_apply_one` (projection/__init__.py:125-126): `PointRetracted` sets `points[id]["status"] = "retracted"` instead of `points.pop(id)`; **no-op when the id is absent** (e.g., merged-away points — a retract-after-merge must neither KeyError nor resurrect a phantom). **Re-fold behavior change is INTENDED and TESTED (plan-review P2):** re-folding a pre-change log under the new code yields TOMBSTONES (`status:'retracted'` kept) instead of deletion — a deliberate change that recovers retracted points from retained logs. Existing materialized projections are NOT re-folded by this plan; NO migration reconstructs already-deleted historical retractions (#689 full remediation out of scope). **Not version-gated** (decision: tombstone-by-default for every `PointRetracted` event, regardless of `projection_version` — the alternative, gating on `projection_version >= 2`, was considered and rejected: it adds per-event age semantics with no consumer benefit).
- `split()` (projection/__init__.py:140) excludes `status=='retracted'` points from the statements list (default behavior preserved: retracted ≠ active statement).
- `sdk.query()` / `paginated_query()` / hosted `GET /v1/points` exclude `status='retracted'` by default (additive filter: `AND (n.status IS NULL OR n.status <> 'retracted')`), with an `include_retracted: bool = False` param on `query`/`paginated_query` for opt-in visibility.
- `get_point(id)` on a retracted point returns it with `status:'retracted'` (queryable with filter, per user decision).
- **`PointsMerged` + `PointRetracted` interaction pinned by test (plan-review P2):** fold `[PointAdded, PointsMerged, PointRetracted]` — the merged-away point stays gone (the tombstone skips absent ids, no phantom resurrection) and the fold completes without error (no KeyError).
- `check_structure` orphaned_draft (sdk.py:1135) unaffected (already `status:'draft'`-scoped).

**Files:**
- Modify: `tortoise/projection/__init__.py` (:_apply_one :125, :split :140)
- Modify: `tortoise/sdk.py` (query :933, paginated_query — add retracted filter + include_retracted)
- Modify: `tortoise/hosted_api.py` (list_points :829 — same filter; no include param on REST v1, keep surface minimal)
- Test: `tests/test_projection.py`, `tests/test_claim_lifecycle.py`

**Step 1: Write failing tests** — uses the shared `sdk_factory` fixture (tests/conftest.py, Task 1):

```python
def test_fold_retract_tombstones():
    from tortoise.projection import fold
    events = [
        {"type": "PointAdded", "id": "p1", "content": "x"},
        {"type": "PointRetracted", "id": "p1"},
    ]
    points = fold(events)
    assert "p1" in points
    assert points["p1"]["status"] == "retracted"


def test_fold_old_log_now_tombstones():
    # plan-review P2: re-folding a PRE-change log under new code yields a
    # tombstone (intended, tested behavior change) — not a pop, not version-gated.
    from tortoise.projection import fold
    points = fold([
        {"type": "PointAdded", "id": "p1", "content": "x"},
        {"type": "PointRetracted", "id": "p1", "projection_version": 1},
    ])
    assert points["p1"]["status"] == "retracted"


def test_points_merged_then_retracted_tombstones():
    # plan-review P2: PointsMerged pops merge_ids; a later PointRetracted for a
    # merged-away id is a NO-OP — no KeyError, no phantom resurrection.
    from tortoise.projection import fold
    points = fold([
        {"type": "PointAdded", "id": "p2", "content": "merged-away"},
        {"type": "PointsMerged", "id": "p1", "merge_ids": ["p2"]},
        {"type": "PointRetracted", "id": "p2"},
    ])
    assert "p2" not in points          # merged-away stays gone
    assert "p1" in points              # merge target present


def test_split_excludes_retracted():
    from tortoise.projection import split
    points = {"a": {"operator": None, "status": "live"},
              "b": {"operator": None, "status": "retracted"}}
    statements, operators = split(points)
    assert [p for p in statements] == [{"operator": None, "status": "live"}]


def test_query_excludes_retracted_by_default(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "gone")
    sdk.retract_point(p["id"])
    # query() returns a list of point dicts (paginated_query returns {results, ...})
    assert p["id"] not in [q["id"] for q in sdk.query()]
    assert p["id"] in [q["id"] for q in sdk.query(include_retracted=True)]
```

**Step 2: Run — expect FAIL** (pop still hard-deletes; no filters).

**Step 3: Implement** — replace `points.pop(ev["id"], None)` with a tombstone-set that **no-ops on absent ids** (`if ev["id"] in points: points[ev["id"]]["status"] = "retracted"` — merged-away points stay gone, no KeyError); add retracted filter to `query`/`paginated_query` (one shared WHERE-clause helper); update `split`; add the `include_retracted` param default False.

**Step 4: Run** — `python -m pytest tests/test_projection.py tests/test_claim_lifecycle.py -v` PASS; full suite PASS (existing tests asserting pop-delete behavior updated in this step — grep for `PointRetracted` in tests/).

**Step 5: Commit** — `feat(432): retraction tombstone in projection; exclude retracted from default query surfaces`.

---

### Task 3: Durable event emission — SDK emit hook writing `:GraphEvent` nodes

**Intent:** THE root-cause fix (scoping confirmed problem): every SDK claim/graph mutation emits a durable, team-scoped event. All three surfaces route through `TortoiseSDK` (mcp_server.py:14, hosted_api.py:45) — one hook covers MCP, REST, and local. Replaces the GAP-07 TODO (sdk.py:2168). EventAPI/EventLog JSONL path stays as-is.

**Acceptance:**
- New `tortoise/event_store.py`: `ensure_event_schema(proj)`, `next_seq(proj)`, `append_event(proj, seq, type, payload, event_id, ts=None)`, `read_after(proj, after_seq, types=None, limit=100)` — **no `team_id` parameter anywhere (plan-review P2)**; the SDK writes into its own graph namespace and the namespace IS the partition. `ts` is optional (defaults to now; tests backdate it for retention).
- **Duplicate `event_id` append (plan-review P1):** `append_event` catches the unique-constraint violation, logs a warning, and **skips** (no-op / returns the existing event) — `event_id` is a server-side ULID, so a collision is a client-retry artifact, never legitimate. The read path additionally dedups (defense in depth; see the direct-Cypher dedup test).
- Schema (idempotent, try/except — mirror sdk.py:336): exact-match index on `event_id` FIRST, then `GRAPH.CONSTRAINT CREATE <key> UNIQUE NODE GraphEvent PROPERTIES 1 event_id` (constraint requires the index first — Pattern Research); **plain** index `CREATE INDEX FOR (n:GraphEvent) ON (n.seq)` (per-graph = per-team; no `team_id` property to index — plan-review P2).
- `seq` is a per-graph (= per-team) monotonic integer from an atomic in-graph counter — one `GraphEventMeta` node per graph, **no `team_id` property**: `MERGE (m:GraphEventMeta) ON CREATE SET m.last_seq = 1 ON MATCH SET m.last_seq = m.last_seq + 1 RETURN m.last_seq` (single GRAPH.QUERY — atomic per graph; **verify atomicity against the deployed FalkorDB version** in Open Items).
- SDK `_emit_event(type_, payload)` calls `append_event` with **no team parameter** — events land in the SDK's own graph namespace (**server-derived — namespace is set by mcp_auth `_get_team_sdk`/hosted `_make_sdk`, never client-supplied** — mirrors the graph_name guard mcp_server.py:531). Isolation is the namespace, not a property (plan-review P2).
- Emit hooks fire on: `create_point` (only when a NEW point is created — dedup=True returning an existing point does NOT emit), `create_operator` (after the promote-to-live), `retract_point`, `supersede_point`, `annotate_operator`. Event types: `PointAdded`, `OperatorAdded`, `PointRetracted`, `PointSuperseded`, `OperatorAnnotated`. **Content edits (`update_point` non-status props) emit NOTHING** (plan-review P1) — the emit set is exactly these five hooks; every claim transition maps to exactly one.
- **Append-before-mutation** (EventAPI pattern api.py:48-51): event_id/seq/pid are computed first, event appended, then the graph mutation runs. Documented tradeoff: a failed mutation leaves a phantom event (at-least-once favors over-notification; consumers tolerate via `get_point` miss).
- **Zero-relationship guard:** event nodes (and the `GraphEventMeta` counter node) carry NO edges. Tests assert: `MATCH (e:GraphEvent)-[r]-() RETURN count(r)` == 0; `:GraphEvent` nodes invisible to `MATCH (n:Point ...)`, `sdk.get_point(<event_id>)` returns `{}` (the SDK's missing-point contract), `tortoise_fts_query` never returns an event node (search scans Points only), and a domain-label count `MATCH (n) WHERE NOT (n:GraphEvent) AND NOT (n:GraphEventMeta) RETURN count(n)` excludes the event nodes (plan-review P2).
- `event_id` = `self.ulid()`; `ts` = ISO8601 UTC (matches EventAPI `now_iso`); `payload` = JSON string of the **bare domain payload** — type/event_id/ts live as node properties (canonical), NOT re-embedded in the payload; codec encode/decode wiring is deferred (Task 4, plan-review P2).
- `read_after` returns events ordered by `seq ASC`, filtered by `types` if provided, honoring `limit` (default 100, max 1000), and **dedups duplicate `event_id`s** (defense in depth — see the direct-Cypher dedup test).

**Files:**
- Create: `tortoise/event_store.py`
- Modify: `tortoise/sdk.py` (emit hook + calls in create_point :382, create_operator :794, retract_point (Task 1), supersede_point :695, annotate_operator :857; GAP-07 TODO :2168)
- Test: `tests/test_event_store.py`

**Step 1: Write failing tests — `tests/test_event_store.py`** — uses the shared `sdk_factory` fixture (tests/conftest.py, Task 1; the embedded-vs-docker concurrency note lives on that fixture):

```python
import json


def _events(proj):
    # plan-review P2: no team_id property — the graph namespace IS the partition
    rows = proj.g.query(
        "MATCH (e:GraphEvent) RETURN properties(e) ORDER BY e.seq").result_set
    return [r[0] for r in rows]


def test_create_point_emits_graph_event(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "hello events")
    proj = sdk._get_proj()
    evs = _events(proj)
    assert len(evs) == 1
    assert evs[0]["type"] == "PointAdded"
    assert evs[0]["event_id"]
    assert json.loads(evs[0]["payload"])["id"] == p["id"]
    assert evs[0]["seq"] == 1


def test_dedup_create_does_not_emit(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "dedup me", dedup=True)
    sdk.create_point("statement", "dedup me", dedup=True)  # returns existing
    assert len(_events(sdk._get_proj())) == 1


def test_append_duplicate_event_id_rejected(sdk_factory, tmp_path):
    """plan-review P1: duplicate event_id append is rejected/dedup'd at the
    storage layer — append_event catches the constraint violation, logs, and
    skips (no-op). event_id is a server-side ULID; a collision is a retry
    artifact, never legitimate data."""
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    event_store.ensure_event_schema(proj)
    seq1 = event_store.next_seq(proj)
    event_store.append_event(proj, seq1, "PointAdded", {"id": "p1"}, "evt-dup")
    n1 = len(_events(proj))
    seq2 = event_store.next_seq(proj)  # counter still advances
    event_store.append_event(proj, seq2, "PointAdded", {"id": "p1"}, "evt-dup")
    assert len(_events(proj)) == n1     # second append skipped (no-op)
    assert seq2 == seq1 + 1


def test_read_dedups_duplicate_event_ids(sdk_factory, tmp_path):
    """plan-review P1: two :GraphEvent nodes with the SAME event_id written
    directly via Cypher (bypassing append_event) → read_after returns one.
    (final-verification P2) The unique constraint is deliberately NOT
    installed here — a schema'd graph rejects the second duplicate CREATE
    (that rejection path is covered by test_append_duplicate_event_id_rejected
    and ensure_event_schema). This test pins the READ-side dedup contract on a
    constraint-free graph: seed dupes first, then read_after dedups."""
    from tortoise import event_store
    sdk = sdk_factory(tmp_path, ensure_schema=False)
    proj = sdk._get_proj()
    # NOTE: no create_point call — constraint must not be installed in this test
    proj.g.query(
        "CREATE (e:GraphEvent {seq: 90, ts: $ts, type: 'PointAdded', "
        "payload: $pl, event_id: 'dup-1'})", params={"ts": "x", "pl": "{}"})
    proj.g.query(
        "CREATE (e:GraphEvent {seq: 91, ts: $ts, type: 'PointAdded', "
        "payload: $pl, event_id: 'dup-1'})", params={"ts": "x", "pl": "{}"})
    evs = event_store.read_after(proj, 0, limit=100)  # raw node properties
    by_id = [e for e in evs if e.get("event_id") == "dup-1"]
    assert len(by_id) == 1  # deduped at read


def test_all_mutations_emit(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    s = sdk.create_point("statement", "src")
    t = sdk.create_point("statement", "tgt")
    op = sdk.create_operator("IMPL", s["id"], [t["id"]])
    sdk.retract_point(t["id"])
    old = sdk.create_point("statement", "old")
    new = sdk.create_point("statement", "new")
    sdk.supersede_point(old["id"], new["id"])
    sdk.annotate_operator(op["id"], 0.5, 0.5, 0.5, 0.5)
    types = [e["type"] for e in _events(sdk._get_proj())]
    assert types.count("PointAdded") == 4  # src, tgt, old, new
    assert "OperatorAdded" in types and "PointRetracted" in types
    assert "PointSuperseded" in types and "OperatorAnnotated" in types


def test_content_edit_emits_nothing(sdk_factory, tmp_path):
    """plan-review P1: update_point non-status edits do NOT emit."""
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "before")
    sdk.update_point(p["id"], content="after")
    assert [e["type"] for e in _events(sdk._get_proj())] == ["PointAdded"]


def test_event_nodes_have_zero_relationships(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "isolated")
    proj = sdk._get_proj()
    assert proj.g.query("MATCH (e:GraphEvent)-[r]-() RETURN count(r)").result_set[0][0] == 0


def test_event_nodes_invisible_to_domain_queries(sdk_factory, tmp_path):
    """plan-review P2: get_point, search, and domain-label counts exclude events."""
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "visible")
    proj = sdk._get_proj()
    ev = _events(proj)[0]
    assert proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 1
    assert len(sdk.query()) == 1
    assert sdk.get_point(ev["event_id"]) == {}  # {} per get_point missing contract
    hits = sdk.tortoise_fts_query("visible", limit=50)  # search scans Points only
    hit_ids = {h["id"] for h in hits}
    assert p["id"] in hit_ids
    assert ev["event_id"] not in hit_ids
    domain = proj.g.query(
        "MATCH (n) WHERE NOT (n:GraphEvent) AND NOT (n:GraphEventMeta) RETURN count(n)"
    ).result_set[0][0]
    assert domain == 1  # only the Point; event + counter nodes excluded


def test_seq_is_monotonic_under_concurrency(sdk_factory, tmp_path):
    import threading
    # Embedded-vs-docker uncertainty is documented on the shared sdk_factory
    # fixture in tests/conftest.py (Task 1): the embedded redislite server is
    # shared per-path; if the embedded client is not multi-connection-safe,
    # this test runs against a live FalkorDB (docker) instead.
    errors = []
    def worker(i):
        try:
            s = sdk_factory(tmp_path)
            s.create_point("statement", f"c{i}")
        except Exception as e:  # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors
    proj = sdk_factory(tmp_path)._get_proj()
    seqs = [e["seq"] for e in _events(proj)]
    assert sorted(seqs) == seqs and len(set(seqs)) == len(seqs)
```

**Step 2: Run — expect FAIL** (`GraphEvent` label never created).

**Step 3: Implement** — `event_store.py` (schema, seq, append, read_after) + `_emit_event` on TortoiseSDK + hook calls. Emit placement:
- `create_point`: compute `pid`/dedup decision first; if new point → `_emit_event("PointAdded", {"id": pid, "kind": kind, ...})` before the CREATE query.
- `create_operator`: after pid computed, before edge creation → `_emit_event("OperatorAdded", {"id": pid, "op_type": op_type, "source_id": ..., "target_ids": ...})`.
- `retract_point`/`supersede_point`/`annotate_operator`: emit `PointRetracted`/`PointSuperseded`/`OperatorAnnotated` with the point/operator id + payload before the mutation query.
- `append_event`: payload serialized as the **bare domain dict** (JSON; no type/version/event_id/ts re-embedding — node props are canonical); the CREATE is wrapped in try/except — on a unique-constraint violation (duplicate `event_id`) log a warning and **skip** (never crash the mutation; plan-review P1).
- Schema creation runs lazily on first append (idempotent); `ensure_event_schema` also called from hosted boot (Task 7).

**Step 4: Run** — `python -m pytest tests/test_event_store.py -v` PASS; full suite PASS.

**Step 5: Commit** — `feat(432): SDK event emission -> :GraphEvent nodes (unique event_id, seq index, zero-relationship guard)`.

---

### Task 4: EventCodec registration of claim event types (registration only; wiring deferred)

**Intent:** Satisfy #432 indicator 3 — claim events integrate with the shared_state EventCodec (versioned, registered types, upcasters) instead of a parallel mechanism. **Scope is REGISTRATION ONLY in this task (plan-review P2):** the claim types become the catalog of record in `shared_state/events.py` with round-trip tests; encode/decode wiring into the emit hook / read path is DEFERRED to the task that ships the first real upcaster (the codec adds no value until events carry versions that need migration; node-level `type`/`event_id`/`ts` are canonical for v1). `ClaimStateChanged` is DROPPED (plan-review P1) — no code path emits it.

**Acceptance:**
- `tortoise/shared_state/events.py` registers exactly: `PointAdded`, `OperatorAdded`, `PointRetracted`, `PointSuperseded`, `OperatorAnnotated` — each with an (initially empty) upcaster chain via `register_event_type(name, upcasters=[])`. `ClaimStateChanged` is NOT registered.
- `EventCodec.encode(type_name, payload)` round-trips through `EventCodec.decode` for each of the five types (version 1) — codec-level unit tests only.
- **No emission/read wiring in this task:** `:GraphEvent` stores the bare domain payload (Task 3); `_emit_event`/`read_after` do NOT call the codec. A follow-up task ("ship the first real upcaster") wires `EventCodec.encode`/`decode` into the emit hook + read path, with a `try/except KeyError → raw fallback` for unregistered legacy types at that point.
- New test file `tortoise/shared_state/tests/test_events_claim.py` (or extend existing) with round-trip + upcaster-chain tests for the five types.

**Files:**
- Modify: `tortoise/shared_state/events.py` (register the five types — no `ClaimStateChanged`)
- Test: `tortoise/shared_state/tests/test_events_claim.py` (Create)
- (Deferred, NOT in this task: codec wiring in `tortoise/event_store.py` + `tortoise/sdk.py` `_emit_event` — lands with the first real upcaster)

**Step 1: Write failing tests** — round-trip for all five registered types; decode of a hypothetical v2 event with a stub upcaster produces v2 shape; `event_types()` reports the five claim types alongside the existing PM/card types, and **asserts `ClaimStateChanged` is absent** (plan-review P1).

**Step 2: Run — expect FAIL** (registration missing → `EventCodec.encode` raises KeyError for unregistered types).

**Step 3: Implement** — register the five types only; no changes to `_emit_event`/`read_after`.

**Step 4: Run** — shared_state tests PASS; full suite PASS.

**Step 5: Commit** — `feat(432): register claim event types in EventCodec (registration only; wiring deferred to first upcaster)`.

---

### Task 5: Subscription read surface — REST `GET /v1/events` + cursor + 410

**Intent:** Indicator 2 — a tenant polls graph/claim changes without full-graph polling. Built on the Task 3 stream, not the CLI EventLog (related: #488 tracks the EventLog primitive separately). Team scoping comes from auth (`get_current_team`) + SDK namespace — never client input.

**Acceptance:**
- `TortoiseSDK.events_poll(after: str | None = None, types: list[str] | None = None, limit: int = 100) -> dict` returns `{"events": [...], "next_cursor": str}` where events are **payload dicts (JSON-parsed bare domain payloads; codec decode deferred to the first-upcaster task — Task 4)** ordered by seq, dedup'd via `recovery.dedup_events`, and `next_cursor` is an **opaque** token encoding `{v:1, seq:last_seq}` (base64url JSON). `after=None` → tail (oldest retained). **The empty-graph cursor uses the SAME format** — `b64url({"v":1,"seq":0})` (plan-review P2; the plan's earlier `"v1:0"` string is unparseable by `_decode_cursor` and is removed).
- `GET /v1/events?after=<cursor>&types=a,b&limit=100` (hosted_api) — auth `Depends(get_current_team)`; team SDK `_make_sdk(namespace=team["team_id"])`; responds `{"events": [...], "next_cursor": "..."}`.
- **Expired cursor → HTTP 410** with body `{"detail": "cursor expired — replay from tail (after= omitted)"}`. **SDK-level (plan-review P2):** `events_poll` raises `ValueError("cursor expired — replay from tail")` when `after_seq < min(seq)` (query `MATCH (n:GraphEvent) RETURN min(n.seq)` — per-graph = per-team, no `team_id` filter); `_safe` (MCP) converts that to a structured error, and the REST route maps it to 410. **Empty graph:** `after` never expires; poll returns an empty batch with the same opaque cursor format encoding `{v:1, seq:0}`.
- **Cursor round-trip test (plan-review P2):** `_encode_cursor({v:1, seq:0})` (empty graph) decodes back to `{v:1, seq:0}` via `_decode_cursor` — one format for every cursor.
- Malformed cursor → 400 (`detail: "invalid cursor"`).
- `types` filter validates against registered event types; unknown type → 400.
- **Tenant isolation:** events live in per-team graph namespaces (`_make_sdk(namespace=team["team_id"])`); the REST test asserts team A's poll never contains team B's events (plan-review P2: isolation by namespace, not property).
- At-least-once contract documented in the endpoint docstring: clients must be idempotent on replay.

**Files:**
- Modify: `tortoise/sdk.py` (`events_poll`, `_encode_cursor`/`_decode_cursor` helpers, `events_since` internal)
- Modify: `tortoise/event_store.py` (`read_after` already; add min-seq + cursor helpers if SDK-side keeps them)
- Modify: `tortoise/hosted_api.py` (new `@app.get("/v1/events")` near :829)
- Test: `tests/test_subscriptions.py` (Create), `tests/test_hosted_api.py` (REST 410/400/isolation — **required, no literal stubs**, plan-review P2)

**Step 1: Write failing tests — `tests/test_subscriptions.py`** — uses the shared `sdk_factory` fixture (tests/conftest.py, Task 1):

```python
import base64, json, pytest
from tortoise.sdk import TortoiseSDK


def test_poll_after_cursor_roundtrip(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p1 = sdk.create_point("statement", "first")
    p2 = sdk.create_point("statement", "second")
    r1 = sdk.events_poll(after=None)
    assert [e["type"] for e in r1["events"]] == ["PointAdded", "PointAdded"]
    r2 = sdk.events_poll(after=r1["next_cursor"])
    assert r2["events"] == []  # nothing after tail
    sdk.retract_point(p2["id"])
    r3 = sdk.events_poll(after=r1["next_cursor"])
    assert [e["type"] for e in r3["events"]] == ["PointRetracted"]


def test_cursor_is_opaque_and_deterministic(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "x")
    r = sdk.events_poll()
    # opaque: base64url JSON {v:1, seq:N}
    decoded = json.loads(base64.urlsafe_b64decode(r["next_cursor"]))
    assert decoded["v"] == 1 and decoded["seq"] == 1
    # same tail cursor always yields the same batch (dedup + stable order)
    r2 = sdk.events_poll()
    assert r2["events"] == r["events"]


def test_empty_graph_cursor_roundtrip(sdk_factory, tmp_path):
    # plan-review P2: the empty-graph cursor uses the SAME opaque format —
    # b64url({"v":1,"seq":0}) — and round-trips through _decode_cursor.
    sdk = sdk_factory(tmp_path)
    r = sdk.events_poll()
    assert r["events"] == []
    decoded = json.loads(base64.urlsafe_b64decode(r["next_cursor"]))
    assert decoded == {"v": 1, "seq": 0}
    assert sdk.events_poll(after=r["next_cursor"])["events"] == []


def test_types_filter(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "a")
    sdk.retract_point(sdk.query()[0]["id"])
    r = sdk.events_poll(types=["PointRetracted"])
    assert [e["type"] for e in r["events"]] == ["PointRetracted"]
    with pytest.raises(ValueError, match="unknown event type"):
        sdk.events_poll(types=["Nope"])


def test_expired_cursor_raises_valueerror(sdk_factory, tmp_path):
    # plan-review P2: SDK raises a structured ValueError; _safe → MCP error,
    # REST maps to 410.
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "will-purge")
    old_cursor = sdk.events_poll()["next_cursor"]
    proj = sdk._get_proj()
    proj.g.query("MATCH (e:GraphEvent) SET e.ts = $old_ts",
                 params={"old_ts": "2020-01-01T00:00:00+00:00"})
    sdk.create_point("statement", "kept")  # newer event survives the purge
    # purge helper lands in Task 7 — trigger expiry here via direct Cypher DELETE
    # (plan-review final-verification P2: Task 5 must not depend on Task 7's helper)
    proj.g.query("MATCH (n:GraphEvent) WHERE n.seq < $min_seq DELETE n", params={"min_seq": 1})
    with pytest.raises(ValueError, match="cursor expired"):
        sdk.events_poll(after=old_cursor)
```

Plus **real REST tests** in `tests/test_hosted_api.py` (same file as the hosted fixtures; no literal stubs — plan-review P2):

```python
class TestEventsPoll:
    def test_events_poll_returns_events(self, client):
        r = client.post("/v1/points", json={"kind": "statement", "content": "e1"})
        assert r.status_code == 200
        r = client.get("/v1/events")
        assert r.status_code == 200
        body = r.json()
        assert [e["type"] for e in body["events"]] == ["PointAdded"]
        assert body["next_cursor"]

    def test_expired_cursor_410(self, client):
        client.post("/v1/points", json={"kind": "statement", "content": "old"})
        from tortoise import event_store
        from tortoise.hosted_api import _make_sdk
        # namespace = the authenticated team's graph (TEST_TEAM_ID)
        sdk = _make_sdk(namespace="test-team-001")
        proj = sdk._get_proj()
        stale = sdk.events_poll()["next_cursor"]
        # backdate ALL events past retention, add a fresh one, purge
        proj.g.query("MATCH (e:GraphEvent) SET e.ts = '2020-01-01T00:00:00+00:00'")
        client.post("/v1/points", json={"kind": "statement", "content": "fresh"})
        # purge helper lands in Task 7 — trigger expiry here via direct Cypher DELETE
    # (plan-review final-verification P2: Task 5 must not depend on Task 7's helper)
    proj.g.query("MATCH (n:GraphEvent) WHERE n.seq < $min_seq DELETE n", params={"min_seq": 1})
        r = client.get("/v1/events", params={"after": stale})
        assert r.status_code == 410
        assert "replay from tail" in r.json()["detail"]

    def test_malformed_cursor_400(self, client):
        r = client.get("/v1/events", params={"after": "not-a-cursor!!"})
        assert r.status_code == 400
        assert r.json()["detail"] == "invalid cursor"

    def test_tenant_isolation(self, client, internal_client):
        # Same pattern as TestCrossTenantIsolation.test_team_isolation
        # (provision_test_user in conftest is the fixture-based equivalent;
        # the hosted suite's established pattern is /internal/provision).
        from tortoise.hosted_api import _make_sdk, app, get_current_team
        from tests.test_hosted_api import TEST_TEAM
        for tid in ("iso-evt-a", "iso-evt-b"):
            r = internal_client.post("/internal/provision", json={
                "team_id": tid, "team_name": f"Team {tid}",
                "api_key_hash": f"hash-{tid}", "created_by": "tester"},
                headers={"Authorization": f"Bearer {_INTERNAL_KEY}"})
            assert r.status_code == 200, f"provision failed: {r.text}"
        _make_sdk(namespace="iso-evt-a").create_point(content="TEAM_A_EVT", kind="statement")
        _make_sdk(namespace="iso-evt-b").create_point(content="TEAM_B_EVT", kind="statement")
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM, team_id="iso-evt-a")
        try:
            r = client.get("/v1/events")
            bodies = [e["payload"].get("content", "") if isinstance(e["payload"], dict)
                      else e["payload"] for e in r.json()["events"]]
        finally:
            app.dependency_overrides.clear()
        assert "TEAM_A_EVT" in bodies
        assert "TEAM_B_EVT" not in bodies  # namespace isolation, no cross-team leak
```

**Step 2: Run — expect FAIL** (`events_poll` missing, route missing).

**Step 3: Implement** — SDK `events_poll` + cursor helpers (opaque base64url `{v:1, seq}` — one format for tail AND empty graph), `read_after` integration with `dedup_events`; `events_poll` raises `ValueError("cursor expired — replay from tail")` on `after_seq < min(seq)`; hosted route with `get_current_team` + 400 (malformed cursor / unknown type) and **410 mapping (catch the SDK `ValueError` → `HTTPException(status_code=410, detail="cursor expired — replay from tail (after= omitted)")`)**; MCP `_safe` surfaces the same ValueError as a structured error.

**Step 4: Run** — `python -m pytest tests/test_subscriptions.py tests/test_hosted_api.py -v` PASS.

**Step 5: Commit** — `feat(432): GET /v1/events cursor poll surface with 410 expiry + tenant isolation`.

---

### Task 6: MCP tools — `tortoise_events_poll` + `tortoise_retract_point` (registry, readOnly, HTTP_ALLOWED)

**Intent:** Expose the subscription surface to MCP/pi agents (stdio + Streamable HTTP) and make tombstone retraction tenant-reachable (the SDK method from Task 1 has no tenant surface otherwise — EventAPI retract is CLI-only). Tool registry is the single source of truth — `http_policy=True` auto-includes tools in `HTTP_ALLOWED` (mcp_auth.py:70 ← tool_registry).

**Acceptance:**
- `tortoise_events_poll(after: str | None = None, types: list[str] | None = None, limit: int = 100) -> dict` wraps `_safe(_get_team_sdk().events_poll, ...)`; registered in `tool_registry.py` with `annotations=_ro()` (readOnlyHint), `http_policy=True`, `sdk_method="events_poll"`. **readOnlyHint vs lazy-purge DELETE (plan-review P2):** the Task 7 lazy purge inside `events_poll` is **gated by `TORTOISE_EVENT_RETENTION_INTERVAL`** (purge at most once per interval per process), so polls are read-only in steady state; the rare maintenance DELETE is documented in the tool docstring (readOnlyHint covers user-visible state — the poll never mutates user content). The introspective quota test scans for CREATE/MERGE patterns only, so the DELETE does not trip `_QUOTA_GATED` enforcement.
- `tortoise_retract_point(id: str) -> dict` wraps `_safe(_quota_gated(_get_team_sdk().retract_point, "points"), id)`; registered with `annotations=_rw()` (destructiveHint), `http_policy=True`, `sdk_method="retract_point"`, and added to `_QUOTA_GATED` (mcp_server.py:145 set) — it is a status-mutating write like `tortoise_update_point`. **Add `.retract_point` to the `scan_patterns` tuple** in `TestIntrospectiveQuotaCompleteness.test_every_node_creating_tool_is_quota_gated` (tests/test_mcp_http.py:614) — it currently omits `retract_point`, so the test cannot enforce its gating (plan-review P2).
- HTTP-mode team scoping: `_get_team_sdk()` (mcp_auth.py:58) already returns `TortoiseSDK(namespace=team_id)` from the request-scoped ContextVar — poll/retract inherit isolation; stdio mode uses namespace `None` → `"default"` team.
- Stdio + HTTP parity tested; poll works when `_transport_mode` is stdio (dev mode) and http (auth'd).
- The introspective quota-completeness test (`tests/test_mcp_http.py:606`) still passes — poll body makes no node/edge-creating calls; retract is registered in `_QUOTA_GATED` and covered by `scan_patterns`.
- **Hosted tombstone contract (plan-review P2):** hosted-client test — create + retract a point via the team SDK, then assert `GET /v1/points` excludes it while `GET /v1/points/{point_id}` still returns it with `status:'retracted'` (tombstone contract — not a 404).

**Files:**
- Modify: `tortoise/mcp_server.py` (two tools near :552 update_point / :676 supersede)
- Modify: `tortoise/tool_registry.py` (two ToolDefinition entries; `_QUOTA_GATED` is in mcp_server.py)
- Test: `tests/test_mcp_server.py`, `tests/test_mcp_http.py` (HTTP_ALLOWED membership + quota-gate introspection — **add `.retract_point` to `scan_patterns`**), `tests/test_subscriptions.py` (stdio parity), `tests/test_hosted_api.py` (tombstone contract: `/v1/points` excludes, `/v1/points/{id}` returns retracted)

**Step 1: Write failing tests** — registry entries exist with correct annotations/policy; `tools/list` includes both tools over stdio; HTTP mode includes them in `HTTP_ALLOWED` and excludes nothing unexpected; poll over stdio returns the same shape as SDK `events_poll`.

**Step 2: Run — expect FAIL.**

**Step 3: Implement** — tool functions + registry entries + `_QUOTA_GATED` addition + `.retract_point` in the introspective test's `scan_patterns` (test_mcp_http.py:614); document the interval-gated maintenance purge in the poll tool docstring.

**Step 4: Run** — `python -m pytest tests/test_mcp_server.py tests/test_mcp_http.py tests/test_hosted_api.py -v` PASS.

**Step 5: Commit** — `feat(432): MCP tortoise_events_poll + tortoise_retract_point (readOnly/destructive, HTTP_ALLOWED)`.

---

### Task 7: Retention — 30-day purge + size cap (boot + interval, config-driven)

**Intent:** Bound `:GraphEvent` growth in the in-memory graph (FalkorDB Cloud bills by memory). Cursor expiry semantics already handle purged history (Task 5 410). Config-driven so ops can tune without code change.

**Acceptance:**
- `event_store.purge_expired(proj, retention_days)` deletes `MATCH (n:GraphEvent) WHERE n.ts < $cutoff DELETE n` (cutoff = ISO8601 UTC now − retention; per-graph = per-team — no `team_id` filter, plan-review P2).
- `event_store.purge_overflow(proj, max_events)` — if `count(n:GraphEvent) > max_events`, delete oldest by `ORDER BY n.seq ASC LIMIT <overflow>` (size cap; default from config).
- Config: `TORTOISE_EVENT_RETENTION_DAYS` (default `30`), `TORTOISE_EVENT_MAX_PER_TEAM` (default `500_000`), `TORTOISE_EVENT_RETENTION_INTERVAL` (default `3600`s) — read via `os.environ` with defaults (no config-file change; Verification Plan Config=skip).
- Purge runs: (a) at hosted boot (`_lifespan` / after schema ensure), (b) on an asyncio interval task in hosted_api lifespan (loop with `asyncio.sleep(interval)`), (c) lazily before `read_after` in embedded/stdio mode — **gated by `TORTOISE_EVENT_RETENTION_INTERVAL` (at most once per interval per process)** so polls stay read-only in steady state (resolves the MCP readOnlyHint tension, Task 6 — plan-review P2).
- Purge is idempotent and never blocks polls (best-effort; a poll during purge is safe — deletes only affect events older than the cutoff).
- Tests: events older than cutoff purged; `max_events` cap enforced (oldest dropped); purge + cursor expiry interaction (purged seq → 410).

**Files:**
- Modify: `tortoise/event_store.py` (purge helpers)
- Modify: `tortoise/hosted_api.py` (boot + interval task in lifespan)
- Modify: `tortoise/sdk.py` (lazy purge hook in `events_poll`)
- Test: `tests/test_event_store.py`, `tests/test_subscriptions.py` (expired-cursor-after-purge)

**Step 1: Write failing tests** — insert an event with a backdated `ts` (write via `event_store.append_event` directly with `ts = now - 31d` — the optional `ts` override added in Task 3), purge, assert gone; cap test with 3 events + max 2 → oldest dropped; purge→poll → 410 on the purged seq (SDK `ValueError`, Task 5).

**Step 2: Run — expect FAIL.**

**Step 3: Implement** — purge helpers + lifespan wiring (mirror existing lifespan task patterns in hosted_api.py — e.g. the dream enqueue loop at :85 area) + lazy purge in `events_poll`.

**Step 4: Run** — event_store + subscriptions tests PASS; full suite PASS.

**Step 5: Commit** — `feat(432): 30-day :GraphEvent retention + per-team size cap (boot + interval)`.

---

### Task 8: Docs — ontology §5 vocabulary, event catalog, index registration

**Intent:** Close the ontology loop: the state vocabulary + derived-`challenged` semantics + the `:GraphEvent` event catalog must be documented where consumers look (`docs/ONTOLOGY.md`, the engineering wiki index). AGENTS.md references `docs/00_index.md`, which does **not exist in this repo** — the wiki index `docs/04_platform/wiki/index.md` is the nearest index (verify at execution; if a `docs/00_index.md` appears on main by then, register there instead).

**Acceptance:**
- `docs/ONTOLOGY.md` §4.1 Point `status` row + §5 reflect: `{draft, live, retracted, superseded, outdated, archived}`; `challenged` derived from NAND-operator-edge presence on a live point; retraction = tombstone (status change, not deletion).
- New `docs/event-catalog.md`: event types table (name, version, emitted-by, payload fields, producer surface) for `PointAdded`, `OperatorAdded`, `PointRetracted`, `PointSuperseded`, `OperatorAnnotated` (**no `ClaimStateChanged` — dropped, plan-review P1**); delivery contract (at-least-once, event_id dedup, 30-day retention, opaque cursor, 410 expiry); `:GraphEvent` node schema `{seq, ts, type, payload, event_id}` (**no `team_id` — the graph namespace is the partition, plan-review P2**) + zero-relationship guard.
- Register `docs/event-catalog.md` in `docs/04_platform/wiki/index.md` (or `docs/00_index.md` if present).
- `docs/plans/` gets nothing else; scoping + research docs already filed.

**Files:**
- Modify: `docs/ONTOLOGY.md`
- Create: `docs/event-catalog.md`
- Modify: `docs/04_platform/wiki/index.md`
- Test: (none new — full suite regression in Step 4)

**Step 1:** Update `docs/ONTOLOGY.md` §4.1 status row + §5 (challenged-as-derived note, GraphEvent label reserved for the change log — distinct from the `:Event` ontology entity with `eventId`).

**Step 2:** Create `docs/event-catalog.md` per the catalog contract above; keep the table in sync with `shared_state/events.py` registrations (cross-reference by type name).

**Step 3:** Register the catalog in the wiki index.

**Step 4:** Full suite + doc consistency check — `python -m pytest tests/ -v` green; no test asserts the old vocabulary.

**Step 5: Commit** — `docs(432): claim state vocabulary + event catalog`.

---

## 4. Notes / Open Items

**Research Intake Summary (FalkorDB — cited)**
- Unique constraint: `GRAPH.CONSTRAINT CREATE <key> UNIQUE NODE <label> PROPERTIES <count> <props...>`; **requires a pre-existing exact-match index**; enforced only when constrained properties are non-null; array props excluded. Source: docs.falkordb.com/commands/graph.constraint-create.html (fetched 2026-08-08).
- Composite index: `CREATE INDEX FOR (n:Label) ON (n.p1, n.p2)` — FalkorDB blog Cypher cheatsheet; in-repo precedent sdk.py:336.
- Pagination: `ORDER BY` + `SKIP`/`LIMIT` is the documented pattern; cursor range-scans preferred over deep SKIP (Neo4j issue #12695). Plan uses `WHERE seq > $after ORDER BY seq LIMIT` on the plain `seq` index (per-graph = per-team).
- Pitfall: unique-constraint string-prop crashes reported (FalkorDB #664); FalkorDBLite lacks `dropIndex` (projection/__init__.py:763) — schema creation idempotent + non-fatal.
- Zero-relationship nodes are graph islands — invisible to label-scoped queries, traversals, EP (corroborated by FalkorDB MATCH/label-scan docs). Guard enforced by test.

**Open Items (verify/decide at execution; none block task sequencing)**
1. **`seq` atomicity:** confirm the `MERGE (m:GraphEventMeta ...) SET m.last_seq = m.last_seq + 1 RETURN m.last_seq` pattern is atomic per graph on the deployed FalkorDB version (Cloud). Fallback if not: ts-millis seq with +1 bump on collision (dupes tolerable — event_id remains unique; ordering ties only at same-ms). Test `test_seq_is_monotonic_under_concurrency` gates this.
2. **Append-before vs after mutation:** plan follows scoping (append-before-mutation, EventAPI parity). Consequence: failed mutations leave phantom events; poll consumers tolerate via `get_point` miss. Boot-reconcile (event watermark vs graph `updatedAt`) is documented as a follow-up, NOT in v1.
3. **Unique-constraint behavior on missing property:** constraint skips nodes missing `event_id` — the emit hook always sets `event_id` (ULID), so this is safe; do NOT rely on the constraint to reject null-event_id nodes (an Open-Item note for anyone touching the write path). Consider a MANDATORY constraint on `event_id`/`seq` as a hardening follow-up (safe on a fresh label).
4. **EventAPI parity blast radius:** changing `_point` default to `'draft'` flips CLI/ingest-created points (api.py:67). Existing tests asserting `live` from the EventAPI path must be updated (Task 1 Step 4). Confirm no production consumer depends on ingest points being immediately `live` (per scoping, parity was chosen — flag if that assumption is wrong).
5. **`outdated=true` vs `status='superseded'` redundancy:** supersede sets both (back-compat). A future consolidation belongs to #690 (vocabulary drift); not absorbed here.
6. **`include_retracted` surface:** only `query`/`paginated_query` gain the param; hosted `GET /v1/points` stays exclude-by-default with no param (keep REST surface minimal). If a tenant needs retracted visibility over REST, add the param in a follow-up.
7. **docs index:** `docs/00_index.md` does not exist in this repo (AGENTS.md references it — likely aspirational/agent-infra template). Register the event catalog in `docs/04_platform/wiki/index.md`; re-check at execution.
8. **SSE push / MCP notifications / webhooks:** out of scope (Approach B FastMCP gap, Approach C). The SSE endpoint remains a documented additive layer on top of this stream.
9. **Retention purge vs active cursor:** a client holding a cursor across a purge gets 410 on next poll (by design). Clients should treat 410 as "replay from tail, then dedup by event_id" (idempotent consumers per at-least-once).

**Related issues** — #488 (EventLog `read_after`: NOT absorbed; the poll reads FalkorDB event nodes), #689 (tombstone data-loss: partially fixed by Task 2 for future retractions; historical retracted points are unrecoverable — separate remediation), #690 (vocabulary drift: superseded-status work here informs it).

---

## 5. Execution Handoff

**Plan review:** The `plan-review` skill is invoked by the coordinator AFTER this doc is saved (per instructions, I did NOT run it). It will append its `<!-- plan-review: ... -->` signature; execution must not start until `status=clean`.

**Execution mode (pre-announcement):** 8 tasks — at/under the 8-task threshold → **subagent-driven development in this session** (`subagent-driven-development` skill), unless the coordinator prefers a parallel session.

**Verification contract:** every task ends with `python -m pytest tests/ -v` green at each gate; commits run through `commit-workflow` (AGENTS.md hard rule — no git ops performed while writing this plan). Graph-write discipline: implementers touching Tortoise graph writes must follow `skills/how-to-use-tortoise/SKILL.md`.

**Acceptance criteria mapping (from scoping):** AC1 ← Tasks 2+3; AC2 ← Task 5; AC3 ← Task 5 (isolation test); AC4 ← Task 6; AC5 ← Task 1; AC6 ← Tasks 3+5 (unique constraint + dedup_events).

---

## Review Cycle Log

- **plan-review cycle 1: 2 P1 + 12 P2 → fixed** (2026-08-08). Consolidated from three parallel reviewers (Structural, Integration, Efficiency); conflicts resolved in favor of the fixes.
  - **P1-1 (`ClaimStateChanged` zombie type):** dropped from Task 4 registration and Task 8 catalog; `update_point` restricted to non-status props (draft→live promote only) so every claim transition is observable via the five emit hooks; per-call transition-table fetch removed from `update_point`.
  - **P1-2 (duplicate-event tests):** `test_append_duplicate_event_id_rejected` + `test_read_dedups_duplicate_event_ids` added; `append_event` constraint-violation behavior stated (catch → log → skip).
  - **P2-3:** retract_point collapsed to one atomic conditional query (terminal set = retracted/superseded/archived; `deleted` dropped); boundary test for archived; update_point guard folded into the WHERE clause.
  - **P2-4:** Task 2 re-fold semantics — re-folding an old log yields TOMBSTONES (intended, tested; not version-gated; #689 remediation out of scope).
  - **P2-5:** `PointsMerged` + `PointRetracted` interaction test added (no-op for merged-away ids).
  - **P2-6:** `team_id` dropped from `:GraphEvent` schema and all event_store signatures (namespace = partition); plain `seq` index replaces `(team_id, seq)` composite.
  - **P2-7:** Task 4 scoped to registration-only; codec encode/decode wiring deferred to the first real upcaster; bare domain payload stored (node-level type/event_id/ts canonical).
  - **P2-8:** empty-graph cursor uses the same opaque `b64url({"v":1,"seq":0})` format (round-trip test); SDK `ValueError("cursor expired…")` → MCP structured error / REST 410; real REST tests (410/400/isolation) written, no stubs.
  - **P2-9:** `.retract_point` added to the introspective quota test's `scan_patterns`; readOnlyHint vs lazy-purge tension resolved by interval-gated purge + docstring note.
  - **P2-10:** hosted tombstone-contract test (`/v1/points` excludes retracted; `/v1/points/{id}` returns it).
  - **P2-11:** zero-relationship guard assertions extended (get_point → `{}`, search excludes events, domain-label count).
  - **P2-12:** shared `sdk_factory` fixture in tests/conftest.py (Tasks 1/2/3/5); embedded-vs-docker concurrency note folded into it.
  - **P2-13:** Task 8 Files block gained its `Test:` entry.

> Status remains `draft` until `plan-review` signs it clean.
<!-- plan-review: status=clean (cycle 2: 2 P1 + 12 P2 fixed, final verification CLEAN after 2 spec-precision edits) -->
