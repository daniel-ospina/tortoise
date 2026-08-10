<!-- research-path: docs/plans/2026-08-09-281-instantiates-aboutobject.md (no external research — zero third-party deps) -->

# #281 Re-scope: Event→Object connections — INSTANTIATES → aboutObject

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Bring session-indexing semantic connections in line with canonical ONTOLOGY v3.2 — replace the removed `INSTANTIATES` predicate with the canonical `aboutObject` edge, populate the missing Object properties, update ranking (signal key + all consumers), prune the security allowlist, and migrate existing edges.

**Team:** epistemic-team
**Role:** (unset)

**Architecture:** `_connect_issue_objects` (sdk.py) currently creates `(e:Event)-[:INSTANTIATES]->(o:Object)` via raw Cypher, bypassing the SDK's edge validation. ONTOLOGY.md §3.9 removed `instantiates` (#214, 2026-08-06 — Action dissolved in v3.0); §3.2 names `aboutObject` as the canonical Event→Object about-edge, and `create_about_edge()` (projection/edges.py:143) is the established creation path (used by `create_event` for aboutObject props). The re-scope routes through that path, fixes the Object node's missing `repo`/`issue_number`/`url` properties, updates ranking's graph_boost (both `_fetch_event_signals` AND its `graph_boost` consumer) to count `aboutObject`, removes `INSTANTIATES` from `KNOWN_REL_TYPES` (the drift test then guards the removal for all scanned modules), and migrates any live edges on every graph namespace.

### Pattern Research

Skipped — plan touches zero third-party deps (pure in-repo Python/Cypher change; no libraries, no SDKs, no external services).

### Integration Surface Map

| Surface | Change | Test layer | Bug pattern flags |
|---|---|---|---|
| `sdk.py:4157-4179` `_connect_issue_objects` | Raw `-[:INSTANTIATES]->` MERGE → `create_about_edge(event_id, oid, "aboutObject")` + Object props (`repo`, `issue_number`, `url`) | Unit (`tests/test_sdk_group3.py`) | Write-succeeds-read-fails (check `create_about_edge` return before counting), idempotency (MERGE dedup must survive re-run), defensive `issue_number` cast |
| `session_indexer.py:555` | Comment + call-site parity (call unchanged — lives in sdk) | Covered by sdk unit tests + Task 2 `rg` acceptance; session E2E is regression-only (does NOT touch this path — verified: test_session_capture_e2e.py exercises Document/Event capture, not session-indexing) | n/a |
| `ranking.py` `_fetch_event_signals` (238-262) **and `graph_boost` consumer (184-186)** + docstrings (12-23, 89, 172) | `-[:INSTANTIATES]->` → `-[:aboutObject]->`; signal key `instantiates` → `about_objects` **in both producer and consumer** | Unit (`tests/test_ranking.py`) | Silent-zero regression (renamed key must be consumed — boost-level assert), missing-signal degradation (OPTIONAL MATCH preserves) |
| `security.py:84` `KNOWN_REL_TYPES` | Remove `"INSTANTIATES"` | Drift test (`tests/test_security.py::test_known_rel_types_superset_of_inventory`) | NOTE: drift test scans `tortoise/**/*.py` raw text but EXCLUDES `security.py` and `sdk.py` — it guards ranking/session_indexer/other modules only; sdk.py is guarded by the explicit `rg` acceptance + Task 5 sweep |
| Live graph data | One-off migration: existing `-[:INSTANTIATES]->` edges → `aboutObject`, **on every graph namespace** (default, `*_tortoise`, `team_*`) | Manual dry-run + per-graph count verification (data migration, no automated test) | Partial failure (per-graph try/except), idempotency (re-run safe), missing old-graph edges |
| `docs/scoping-7769-graph-informed-ranking.md` | Doc text still describes INSTANTIATES boost | n/a (docs) | Stale literature — update in Task 5 |
| ONTOLOGY.md | No change needed — v3.2 already documents the removal (§3.9) and `aboutObject` (§3.2) | n/a | n/a |

**Test command baseline:** `python3 -m pytest tests/test_sdk_group3.py tests/test_ranking.py tests/test_security.py -v`

### Verification Plan

- **Unit:** `_connect_issue_objects` (edge type + props + idempotency + resolution-failure counting), ranking signal (about_objects count, degradation to 0.4·confidence on no-edge graphs), security drift (allowlist minus INSTANTIATES).
- **Integration:** session-indexing E2E (`tests/test_session_capture_e2e.py`) still green after predicate swap.
- **Data migration:** verified via per-graph dry-run + count queries before/after; re-run idempotent.
- **Skipped:** UX (no UI), config (no config change), research (no external deps).

---

## Task 0: Restore `shared_embedded_db` fixture in tests/conftest.py

**Intent:** `tests/test_ranking.py` (the file this plan modifies) cannot currently collect on origin/main — commit 33ce4db (#641) removed the session-scoped `shared_embedded_db` fixture from `tests/conftest.py` but left autouse `_use_shared_embedded_db` references in 5 modules (test_ranking, test_sdk_legacy_coverage, test_ep_selector, test_session_semantic_search, test_search_sessions_temporal). Every test in those modules errors with `fixture 'shared_embedded_db' not found`. This plan's own test steps cannot run until the fixture is restored. (Same root cause as open issue #647's ep_selector failure — restoring it here is the minimal prerequisite, not a duplicate fix.)

**Acceptance:** `python3 -m pytest tests/test_ranking.py -q` runs the 17 existing tests without `fixture 'shared_embedded_db' not found` errors (NOTE: `--collect-only` does NOT resolve fixtures — collection succeeds even with the fixture missing; the error only surfaces at test execution, so verify with an actual run).

**Files:**
- Modify: `tests/conftest.py` (restore fixture — the exact session-scoped body that existed pre-33ce4db)

**Step 1: Restore the fixture**

Re-add to `tests/conftest.py` (this exact body, from `git show 33ce4db^:tests/conftest.py`):

```python
@pytest.fixture(scope="session")
def shared_embedded_db():
    """One shared embedded FalkorDBLite DB for the whole session (#221 R5).

    R5 mitigation for the redislite process leak (#176): tests that need an
    embedded (redislite) DB create ONE server per session instead of one per
    test. Each test wipes the graph on its own (or the per-test graph name
    isolates it), so state never leaks across tests while the subprocess
    count stays at 1.

    # TODO(#176): stopgap — remove when the redislite root-cause fix lands.
    """
    import tempfile as _tf
    db_path = os.path.join(_tf.mkdtemp(prefix="tortoise_shared_embedded_"), "shared.db")
    yield db_path
```

**Step 2: Verify tests actually run**

Run: `python3 -m pytest tests/test_ranking.py -q`
Expected: 17+ tests RUN (failures beyond fixture-not-found are pre-existing #647 drift, out of scope) — critically, NO `fixture 'shared_embedded_db' not found` setup errors. (`--collect-only` is insufficient: pytest resolves fixtures at setup/execution, not collection.)

**Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: restore shared_embedded_db fixture removed in #641 (#281 prerequisite)"
```

## Task 1: Update ranking graph_boost to aboutObject (producer AND consumer)

**Intent:** ranking.py's session boost counts Objects a session references; it must read the canonical `aboutObject` edge so re-scoped sessions keep their boost — and the rename must be applied to both the signal producer and its consumer so the boost never silently zeroes.

**Acceptance:** `_fetch_event_signals` counts `aboutObject` edges (signal key `about_objects`); `graph_boost` reads `about_objects` (NOT `instantiates`); OPTIONAL MATCH degradation preserved (no aboutObject on old graphs → boost = 0.4·confidence, no error); docstrings at lines 12-23, 89, and 172 updated; `rg -n "INSTANTIATES" tortoise/ranking.py` → zero.

**Files:**
- Modify: `tortoise/ranking.py` (`_fetch_event_signals` 238-262, `graph_boost` 172-186, docstrings 12-23/89/172)
- Test: `tests/test_ranking.py`

**Step 1: Write the failing test** (use a local temp-db fixture mirroring test_sdk_group3.py's `sdk` fixture, incl. `sdk.close()` in teardown — do NOT leak redislite servers; `pytest` is already imported in test_ranking.py)

```python
@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    yield s
    s.close()

def test_event_signal_counts_about_objects(sdk):
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s2")
    oid = sdk.ulid()
    sdk._get_proj().g.query(
        "CREATE (o:Object {id:$oid, name:'obj', objectKind:'issue'})",
        params={"oid": oid})
    sdk._get_proj().create_about_edge(ev["eventId"], oid, "aboutObject")
    ranker = GraphRanker(projection=sdk._get_proj())
    sig = ranker._fetch_event_signals([ev["eventId"]])[ev["eventId"]]
    assert sig["about_objects"] == 1
    # boost-level: with about_objects=3 + confidence 0.5 → 0.6·(1-1/4) + 0.4·0.5
    # graph_boost(result, signals) — result is the entity dict (may be {}).
    assert ranker.graph_boost(
        {}, {"about_objects": 3, "is_event": True, "confidence": 0.5}) == round(0.6 * (1 - 1 / 4) + 0.4 * 0.5, 4)

def test_event_signal_degrades_without_about_objects(sdk):
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s3")
    ranker = GraphRanker(projection=sdk._get_proj())
    sig = ranker._fetch_event_signals([ev["eventId"]])[ev["eventId"]]
    assert sig["about_objects"] == 0
    # old-graph degradation: no aboutObject → boost is purely 0.4·confidence, no error
    assert ranker.graph_boost({}, {"about_objects": 0, "is_event": True, "confidence": 0.5}) == 0.2
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ranking.py::test_event_signal_counts_about_objects tests/test_ranking.py::test_event_signal_degrades_without_about_objects -v`
Expected: BOTH FAIL pre-change. `test_event_signal_counts_about_objects` fails because `_fetch_event_signals` returns `instantiates`, not `about_objects` (KeyError on `sig["about_objects"]`; the consumer-math assertion would then fail too). `test_event_signal_degrades_without_about_objects` fails the same way — `sig["about_objects"]` raises KeyError pre-change, so the degradation assertion never runs. Both pass post-change: counts test → `about_objects == 1` and boost `0.6·(1−1/4)+0.4·0.5 = 0.65`; degradation test → `about_objects == 0` and boost `0.4·0.5 = 0.2`.

**Step 3: Implement**

- In `_fetch_event_signals`: `OPTIONAL MATCH (e)-[:aboutObject]->(o:Object)` … `WITH e, count(o) AS about_objects` … return `about_objects` key; rename result dict key `instantiates` → `about_objects`.
- In `graph_boost` (lines 184-186): `about_objects = signals.get("about_objects", 0)`; `inst_norm = 1.0 - 1.0 / (1.0 + about_objects)`; update the comment "Events/Sessions: 0.6·aboutObject count (Objects referenced)".
- Update module docstring lines 12-23 AND the internal docstring at line 89 (`graph_instantiates` → `graph_about_objects`) AND line 172 comment: "graph_boost for Events/Sessions uses aboutObject edge count (Objects referenced = reusable knowledge)"; design note "aboutObject / PRODUCES edges may be absent on older graphs; the queries are OPTIONAL MATCH so the boost degrades to 0.4·confidence gracefully."

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ranking.py -v`
Expected: PASS (all ranking tests; any that referenced `instantiates` signal key updated accordingly).

**Step 5: Commit**

```bash
git add tortoise/ranking.py tests/test_ranking.py
git commit -m "fix(ranking): session boost reads aboutObject edges per ONTOLOGY v3.2 (#281)"
```

## Task 2: Swap predicate to aboutObject + populate Object props

**Intent:** Replace the ontology-removed `INSTANTIATES` edge with the canonical `aboutObject` edge and satisfy the issue's unmet item 5 (Object nodes get `repo`, `issue_number`, `url`), while preserving MERGE idempotency (same session indexed twice → one Object, one edge) and counting only successfully-resolved connections.

**Acceptance:** `_connect_issue_objects` creates `(e:Event)-[:aboutObject]->(o:Object)` (never `INSTANTIATES`); Object carries `name`, `objectKind`, and for dict items `repo`, `issue_number`, `url`; running the same session twice creates exactly one Object + one edge; `connected` counts only `create_about_edge` returns of True; `rg -n "INSTANTIATES" tortoise/sdk.py tortoise/session_indexer.py` → zero hits.

**Files:**
- Modify: `tortoise/sdk.py:4157-4179` (`_connect_issue_objects`)
- Modify: `tortoise/session_indexer.py:555` (comment parity)
- Test: `tests/test_sdk_group3.py`

**Step 1: Write the failing test** (use the existing `sdk` fixture — `test_sdk_group3.py` already defines it; do NOT invent an `embedded_graph` fixture)

```python
def test_connect_issue_objects_uses_about_object(sdk):
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s1")
    meta = {"issues": [{"id": "12", "title": "fix thing", "repo": "acme/app",
                        "number": 12, "url": "https://github.com/acme/app/issues/12"}]}
    n = sdk._connect_issue_objects(ev["eventId"], meta)
    assert n == 1
    rel = sdk._get_proj().g.query(
        "MATCH (e:Event {eventId:$eid})-[:aboutObject]->(o:Object) "
        "RETURN o.name, o.repo, o.issue_number, o.url",
        params={"eid": ev["eventId"]}).result_set
    # FalkorDB result_set rows are lists, not tuples
    assert rel == [["fix thing", "acme/app", 12, "https://github.com/acme/app/issues/12"]]
    # Parameterized rel-type: the edge TYPE is passed as a param, so no edge-syntax
    # literal appears in this file (Task 5 sweeps edge syntax only; raw-text words are fine)
    assert sdk._get_proj().g.query(
        "MATCH ()-[r]->() WHERE type(r) = $t RETURN count(*)",
        params={"t": "INSTANTIATES"}).result_set[0][0] == 0

def test_connect_issue_objects_idempotent(sdk):
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s1")
    meta = {"issues": [{"id": "12", "title": "fix thing", "repo": "acme/app",
                        "number": 12, "url": "https://github.com/acme/app/issues/12"}]}
    assert sdk._connect_issue_objects(ev["eventId"], meta) == 1
    assert sdk._connect_issue_objects(ev["eventId"], meta) == 1  # second run
    assert sdk._get_proj().g.query(
        "MATCH ()-[:aboutObject]->() RETURN count(*)").result_set[0][0] == 1
    assert sdk._get_proj().g.query(
        "MATCH (o:Object) RETURN count(*)").result_set[0][0] == 1

def test_connect_issue_objects_defensive_number(sdk):
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s1")
    meta = {"issues": [{"id": "13", "title": "odd", "repo": "acme/app",
                        "number": "N/A", "url": "https://github.com/acme/app/issues/13"}]}
    assert sdk._connect_issue_objects(ev["eventId"], meta) == 1  # no crash
    # oid = item.get("id") or item.get("number") → Object id is "13"
    row = sdk._get_proj().g.query(
        "MATCH (o:Object {id:'13'}) RETURN o.issue_number").result_set
    assert row[0][0] == "N/A"  # defensive cast stored the raw value

def test_connect_issue_objects_hash_fallback_and_bare_string(sdk):
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s1")
    import hashlib
    expected_id = f"issue_{hashlib.sha256('no ids here'.encode()).hexdigest()[:8]}"
    meta = {
        "issues": [{"title": "no ids here"}],        # neither id nor number → hash fallback
        "prs": ["just-a-string-pr"],                 # bare string item
    }
    n = sdk._connect_issue_objects(ev["eventId"], meta)
    assert n == 2
    # hash-fallback Object: issue_<sha256(title)[:8]>, objectKind issue, props default None
    row = sdk._get_proj().g.query(
        "MATCH (o:Object {id:$oid}) RETURN o.objectKind, o.repo, o.issue_number, o.url",
        params={"oid": expected_id}).result_set
    assert row == [["issue", None, None, None]]  # lists, not tuples
    # bare-string PR Object: objectKind pr, props default None, one edge
    pr = sdk._get_proj().g.query(
        "MATCH (e:Event {eventId:$eid})-[:aboutObject]->(o:Object {objectKind:'pr'}) "
        "RETURN o.name, o.repo, o.issue_number, o.url",
        params={"eid": ev["eventId"]}).result_set
    assert pr == [["just-a-string-pr", None, None, None]]  # lists, not tuples
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sdk_group3.py::test_connect_issue_objects_uses_about_object tests/test_sdk_group3.py::test_connect_issue_objects_idempotent tests/test_sdk_group3.py::test_connect_issue_objects_defensive_number tests/test_sdk_group3.py::test_connect_issue_objects_hash_fallback_and_bare_string -v`
Expected: **all four FAIL** pre-change. `test_connect_issue_objects_uses_about_object` and `test_connect_issue_objects_idempotent` fail (pre-change edge is `INSTANTIATES`, no aboutObject, no repo/issue_number/url props). `test_connect_issue_objects_defensive_number` fails — pre-change the Object MERGE never sets `issue_number`, so the row is `None`, not `"N/A"`. `test_connect_issue_objects_hash_fallback_and_bare_string` fails — the bare-string PR assertion queries `-[:aboutObject]->` which doesn't exist pre-change (only the hash-Object query and `n == 2` pass). All four pass after Step 3.

**Step 3: Implement**

In `_connect_issue_objects`:
- Build the Object MERGE to also set `o.repo=$repo, o.issue_number=$issue_number, o.url=$url` (defaults None for non-dict items).
- Defensive cast: `try: issue_number = int(item.get("number")) if item.get("number") is not None else None except (TypeError, ValueError): issue_number = item.get("number")`.
- Replace the raw `MERGE (e)-[:INSTANTIATES]->(o)` with `if self._get_proj().create_about_edge(event_id, oid, "aboutObject"): connected += 1` after the Object exists (create_about_edge resolves the Object by id and MERGEs the edge — idempotent; count only on True so resolution failure is not reported as success). When it returns False, log at debug level (`event_id` + `oid`) so a resolution failure is observable — the session_indexer.py:555 call site otherwise swallows it.
- Update the docstring: "Create aboutObject edges from an AgentSession Event to issue/PR Objects (ONTOLOGY §3.2)."

Update `session_indexer.py:555` comment to "Wire aboutObject edges to issue/PR Objects (parity with ingest_corpus)".

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sdk_group3.py::test_connect_issue_objects_uses_about_object tests/test_sdk_group3.py::test_connect_issue_objects_idempotent tests/test_sdk_group3.py::test_connect_issue_objects_defensive_number tests/test_sdk_group3.py::test_connect_issue_objects_hash_fallback_and_bare_string -v`
Expected: all PASS.

**Step 5: Regression — session E2E + suite slice**

Prerequisite: `tests/test_session_capture_e2e.py` requires a LIVE FalkorDB (`docker compose -f ../eldato/operations/memory/docker-compose.yml up -d` per AGENTS.md) — it has no skip guard and errors without it. The FalkorDBLite embedded baseline covers test_sdk_group3.py only.

Run (with live FalkorDB): `python3 -m pytest tests/test_sdk_group3.py tests/test_session_capture_e2e.py -v`
Run (embedded-only): `python3 -m pytest tests/test_sdk_group3.py -v`
Expected: all PASS in the chosen environment.

**Step 6: Commit**

```bash
git add tortoise/sdk.py tortoise/session_indexer.py tests/test_sdk_group3.py
git commit -m "fix(sdk): Event→Object connections use aboutObject per ONTOLOGY v3.2 (#281)"
```

## Task 3: Prune INSTANTIATES from security allowlist

**Intent:** `KNOWN_REL_TYPES` must not whitelist a predicate the ontology removed; the drift test (`test_known_rel_types_superset_of_inventory`) then proves no surviving `-[:INSTANTIATES]` literal in any scanned module (ranking.py, session_indexer.py, others — note sdk.py and security.py are excluded from its scan by design, so sdk.py is guarded by Task 2's `rg` acceptance + Task 5 sweep instead).

**Acceptance:** `INSTANTIATES` absent from `tortoise/security.py`; `tests/test_security.py::test_known_rel_types_superset_of_inventory` passes; `rg -n "\-\[:INSTANTIATES" tortoise/` → zero hits.

**Files:**
- Modify: `tortoise/security.py:84`
- Test: `tests/test_security.py`

**Step 1: Implement (no test-first — the drift test IS the test)**

Remove `"INSTANTIATES",` from the "Organisational / registry / session" line of `KNOWN_REL_TYPES` (keep BELONGS_TO, FOR_TEAM, CONTAINS, SUPPORTS, INFORMED_BY, PRODUCES).

**Step 2: Run the drift test**

Run: `python3 -m pytest tests/test_security.py -v`
Expected: PASS (Tasks 1-2 removed the code literals from ranking.py/session_indexer.py; sdk.py is excluded from the scan but its literal was removed in Task 2 anyway).

**Step 3: Commit**

```bash
git add tortoise/security.py
git commit -m "fix(security): drop INSTANTIATES from KNOWN_REL_TYPES — removed in ONTOLOGY v3.2 (#281)"
```

## Task 4: Migrate existing INSTANTIATES edges (one-off, idempotent, all namespaces)

**Intent:** graphs that already contain `-[:INSTANTIATES]->` edges (created between 2026-08-06 and this change) must be converted — on EVERY graph namespace (default graph, `*_tortoise` registry/selfhost graphs, and `team_*` hosted tenant graphs) — so ranking and ontology stay consistent everywhere.

**Acceptance:** script (a) enumerates target graphs (`db.list_graphs()` filtered to default, `*_tortoise`, `team_*`; or explicit `--graphs` list), (b) dry-run prints per-graph affected edge counts without writing, (c) live mode creates the matching `aboutObject` edge and detaches the old `INSTANTIATES` edge per pair, per graph, (d) per-graph try/except so one graph's failure leaves others untouched, (e) re-running reports zero remaining INSTANTIATES edges on every graph (idempotent).

**Files:**
- Create: `graph-scripts/migrate_instantiates_to_about.py`

**Step 1: Write the script (no automated test — one-off data migration, verified by dry-run + counts)**

```python
"""One-off migration (#281): convert Event-[:INSTANTIATES]->Object to
Event-[:aboutObject]->Object per ONTOLOGY v3.2 (predicate removed in #214).

Usage:
  python3 graph-scripts/migrate_instantiates_to_about.py --dry-run [--db URI] [--graphs a,b]
  python3 graph-scripts/migrate_instantiates_to_about.py [--db URI] [--graphs a,b]
"""
```

Logic per graph: `MATCH (e:Event)-[r:INSTANTIATES]->(o:Object) MERGE (e)-[:aboutObject]->(o) DETACH DELETE r` — enumerate graphs via `list_graphs()` filtered to `*_tortoise` + `team_*` (or explicit `--graphs`), PLUS the URI-default graph derived exactly as sdk.py does (`urlparse(uri).path.lstrip('/') or "tortoise"` — the `--db` URI path can be arbitrary, e.g. `redis://host/selfhost` → graph `selfhost`, so derive, don't assume "default"). Dry-run MUST print (a) the enumerated target list AND (b) any graphs present in `list_graphs()` that were EXCLUDED by the filter, so a missed namespace is visible, not silent. Count before/after per graph, try/except per graph, dry-run only selects and counts.

**Step 2: Dry-run (all namespaces)**

Run: `python3 graph-scripts/migrate_instantiates_to_about.py --dry-run`
Expected: prints per-graph edge counts; no writes.

**Step 3: Verify count parity per graph**

Run: for each graph in the dry-run output, query `MATCH ()-[:INSTANTIATES]->() RETURN count(*)` before vs after live run → after = 0 everywhere; `MATCH ()-[:aboutObject]->() RETURN count(*)` increased by exactly the before-count per graph.

**Step 4: Commit**

```bash
git add graph-scripts/migrate_instantiates_to_about.py
git commit -m "chore(graph-scripts): migrate INSTANTIATES→aboutObject edges (#281)"
```

## Task 5: Full verification + docs + close-out

**Intent:** prove the re-scope is regression-free end-to-end and stale docs don't survive.

**Acceptance:** full targeted suite green; edge-syntax sweep `rg -n "\-\[:INSTANTIATES" tortoise/ tests/` → **zero hits** (edge syntax only — raw-text `INSTANTIATES` is permitted in tests/ solely as the parameterized value `params={"t": "INSTANTIATES"}` in the Task 2 negative-assertion; the migration script's literal lives in `graph-scripts/`, outside both scopes); `docs/scoping-7769-graph-informed-ranking.md` updated (no INSTANTIATES references); ontology docs untouched (v3.2 already canonical).

**Files:**
- Modify: `docs/scoping-7769-graph-informed-ranking.md` (INSTANTIATES references → aboutObject)
- Test: `tests/test_sdk_group3.py tests/test_ranking.py tests/test_security.py tests/test_projection.py tests/test_session_capture_e2e.py`

**Step 1: Run the full targeted suite**

Run (with live FalkorDB for the E2E file — see Task 2 Step 5 prerequisite): `python3 -m pytest tests/test_sdk_group3.py tests/test_ranking.py tests/test_security.py tests/test_projection.py tests/test_session_capture_e2e.py -v`
Run (embedded-only): `python3 -m pytest tests/test_sdk_group3.py tests/test_ranking.py tests/test_security.py tests/test_projection.py -v`
Expected: all PASS. (Includes tests/test_projection.py — it holds `test_instantiates_rejected_by_create_edge` and `test_valid_predicates_no_longer_contains_instantiates`, the canonical assertions that the removed predicate stays rejected at the create_edge layer. Excludes test_search_sessions_temporal.py — pure temporal-filter tests, no changed surface.)

**Step 2: Literal sweep**

Run: `rg -n "\-\[:INSTANTIATES" tortoise/ tests/`
Expected: **zero hits** (edge-syntax only — a raw-text `rg INSTANTIATES` would also match the parameterized test value `params={"t": "INSTANTIATES"}` and comments in tests/, so sweep the edge syntax specifically; the migration script's literal lives in graph-scripts/, outside both scopes).

**Step 3: Update stale scoping doc**

Edit `docs/scoping-7769-graph-informed-ranking.md`: there are 11 INSTANTIATES mentions (verified: lines 7, 70-71, 86, 108, 120, 129, 134, 160, 165, 181) — sweep ALL of them deterministically:
- Replace INSTANTIATES-count description with aboutObject-count ("Objects referenced") + 0.6·aboutObject weighting + OPTIONAL MATCH degradation note.
- Line 165 (stale "GraphRanker should return 0.0 boost when no INSTANTIATES edges found") → correct to: degradation is 0.4·confidence on the is_event branch (Task 1), never 0.0.
- Line 134 ("INSTANTIATES edges must exist — dependency on #7740") → INVERT: post-change `aboutObject` edges exist from session indexing; remove the dependency framing.
- Lines 70-71 (session boosts framed as "future") → present tense.
- Line 86 (signal-table weight 0.4) → correct to the code's 0.6·inst_norm.
- Line 129 (names nonexistent `test_suggest_entry_points.py`) → drop or correct to the actual test file.
- All other occurrences → aboutObject terminology.
Run `rg -ni "instantiates" docs/scoping-7769-graph-informed-ranking.md` → zero.

**Step 4: Commit**

```bash
git add docs/scoping-7769-graph-informed-ranking.md
git commit -m "docs: ranking scoping doc reflects aboutObject boost (#281)"
```

**Step 5: Post close-out comment on #281** (via commit-workflow, after merge): summary of the 5 tasks, note that ONTOLOGY.md needed no change, and that `#283` E2E-6 (semantic connections test) is now unblocked to test `aboutObject`.

---

<!-- plan-review: cycles=8, status=clean, version=2.2.0 -->
