<!-- research-path: docs/plans/2026-08-05-125-metadata-capture.md -->

# Metadata-Only Session Capture Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make session capture metadata-only — a Document node with topics/summary/sessionId/eventId (searchable via FTS), mechanism provenance via uses→Skill + event-log snapshot, preserving on-demand full extraction.

**Team:** epistemic-team
**Role:** (omitted)

**Architecture:** tortoise-capture writes markdown (topics/summary in frontmatter via TS heuristics) → `ingest.py --capture-metadata` → `api.add_document(topics, summary, session_id, event_id, about_entities)` then `api.add_event()` (sessionCaptured, uses→Skill, produces→Document) → Document + Event + edges. Document search via `entity_type=document` (FTS on scalar `_searchText`). Full extraction = `tortoise ingest` without flag (unchanged).

**Baseline:** `origin/main` at `52e11d6` (includes #122 edges + #49 Phase 1 context stop-writes). Plan worktree: `plan/125-metadata-capture`.

---

### Pattern Research

**Library docs (preflight)** — FalkorDB FTS (only third-party surface). context7 unavailable in session; Perplexity used instead.

**Library version & API surface** — 3 calls, confirmed:
- Canonical: `CALL db.idx.fulltext.createNodeIndex('Document', '_searchText')` + `CALL db.idx.fulltext.queryNodes('Document', $q) YIELD node, score` (docs.falkordb.com/cypher/indexing/fulltext-index.html)
- Competitor variance: GraphRAG SDK uses same `createNodeIndex` pattern for `__Entity__` label
- Known pitfall: FTS indexes **scalar** properties; list/array FTS is NOT the documented pattern — validates the plan's scalar `_searchText` design

**Idiomatic usage patterns** — 2 calls:
- Canonical: array membership filter = `WHERE topic IN d.topics` or `any(topic IN d.topics WHERE ...)` (docs.falkordb.com/cypher/functions.html)
- Known pitfall: array fields support native indexing via `IN`; filter predicates should move to indexed properties

**Library/framework pitfalls** — 1 call:
- Known pitfall: FTS index creation is **idempotent** — "already exists"/"already indexed" errors are silently caught (GraphRAG SDK pattern). Matches `_ensure_indexes`' existing try/except convention.

**#49 Phase 1 coordination** — `context` field is being removed (stop-writes landed). The plan's 22K migration uses `pointKind`/property markers, NOT `context`. `query_suggestions.py` is kind-based (orthogonal to Document search). `references` edge is Source→Entity (≠ aboutSubject Subject↔Entity).

---

### Integration Surface Map

| Surface Type | Specific Surface | Data Flow | Contract | Test Layer |
|---|---|---|---|---|
| DB — Document node | topics/summary/sessionId/eventId/_searchText/doc_status | Write | List[str]/str/str/str/str/str; _searchText = join(title,summary,topics) | Integration (test_projection) |
| DB — Document FTS index | `Document._searchText` | Write | `createNodeIndex('Document','_searchText')` idempotent | Integration (test_search_engine) |
| DB — Event node | sessionCaptured, uses, produces, performs, aboutSubject | Write | EventRecorded payload → _upsert_event edges | Integration (test_projection) |
| API — add_document | topics/summary/session_id/event_id params | In | DocumentCreated event payload | Unit (test_api) |
| API — add_event | new public method (id=eid) | In | EventRecorded event payload | Unit (test_api) |
| API — search read path | entity_type=document | Out | FTS + structural + degradation chain | Integration (test_search_engine) |
| Registry | sessionCaptured in CANONICAL_EVENT_KINDS | Both | Valid pack kind | Unit (test_pack_registry) |
| Extension (TS) | deriveTopics/deriveSummary, frontmatter, --capture-metadata | In/Out | TS heuristic → markdown frontmatter → ingest parse | Unit (index.test.ts) |
| Idempotency | MERGE(ulid doc_id) + begin_ingest content-hash | Both | No dup Documents/Events | Integration |
| Migration | 22K legacy Points FTS floor | Write | backfill_document_search_text() idempotent | Integration |

**Bug Pattern Flags:**
- `_upsert_event` uses-handler: str→'other', dict→kind, bare-dict normalize (else corrupt Object names)
- `produces→Document` vs `produces→Object`: objectType hint required (else Object clone, broken provenance)
- FTS index missing → `run_fts_query` returns [] silently (index-not-found caught) — backfill + index both required
- `_searchText` must SET on EVERY MERGE (ON CREATE + ON MATCH) — stale FTS on re-ingest otherwise

**Checklist Notes:** ALL integration tests use `tortoise_test_*` graph names. `test_guard()` (#99) blocks production names (`tortoise`, `tortoise_restored_*`); the `tortoise_test_*` prefix is safe (guard allows non-blocked names, and convention keeps tests isolated). FalkorDB 4.x+ required (FTS). Baseline = origin/main (has #122 edges + #49 Phase 1).

---

### Tech Stack
- Python 3.11+, FalkorDB 4.x+ (FTS), TypeScript (capture extension), ULID (ids.py)

---

## Task 1: Extend `_upsert_document` — topics/summary/sessionId/eventId/_searchText/doc_status + aboutSubject

**Intent:** The Document node must carry the capture metadata and a searchable `_searchText` field, plus aboutSubject edges for the agent link. This is the foundation — nothing else works without the fields persisting.

**Acceptance:** `_upsert_document` SETs topics (list), summary (str), sessionId (str), eventId (str), doc_status (str), and `_searchText` (computed) on EVERY write (ON CREATE + ON MATCH). aboutSubject edges created when about_entities present.

**Files:**
- Modify: `tortoise/projection/entities.py` (_upsert_document, ~147-171)
- Test: `tests/test_projection.py`

**Step 1: Write the failing test**

```python
def test_upsert_document_capture_fields():
    # in tests/test_projection.py, using test-prefixed graph
    sdk.test_guard()  # #99 guard
    proj = sdk._get_proj()
    # Pre-create the Subject so the about-edge cascade succeeds at the
    # label-agnostic _try_about_edge branch (Subject pre-exists in real
    # E2E flow via _upsert_event's performs edge).
    proj.apply({"type": "SubjectAdded", "id": "agent-pi", "name": "agent-pi",
                "subject_kind": "other"})
    proj.apply({"type": "DocumentCreated", "id": "test-doc-1",
                "title": "Conv", "topics": ["licensing", "AGPL"],
                "summary": "Compared licenses", "session_id": "sess-1",
                "event_id": "evt-1", "doc_status": "captured",
                "about_entities": ["agent-pi"]})
    rows = proj.g.query("MATCH (d:Document {id:'test-doc-1'}) RETURN d.topics, d.summary, d.sessionId, d.eventId, d.doc_status, d._searchText").result_set
    assert rows[0][0] == ["licensing", "AGPL"]
    assert "AGPL" in rows[0][5]  # _searchText includes topics
    assert "Compared" in rows[0][5]  # includes summary
    # aboutSubject edge
    rows2 = proj.g.query("MATCH (d:Document {id:'test-doc-1'})-[:aboutSubject]->(s) RETURN s.id").result_set
    assert len(rows2) == 1
```

**Step 2: Run test to verify it fails**

Run: `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_p1 python3 -m pytest tests/test_projection.py::test_upsert_document_capture_fields -v`
Expected: FAIL — `_searchText`/`topics` not set (fields absent).

**Step 3: Implement `_build_search_text` + extend `_upsert_document`**

```python
def _build_search_text(title, summary, topics):
    parts = [title, summary] + list(topics or [])
    return " ".join(filter(None, parts))
```

In `_upsert_document`, add to SET clauses:
```python
"d.topics=coalesce($topics, d.topics, [])",
"d.summary=coalesce($summary, d.summary, '')",
"d.sessionId=coalesce($sid, d.sessionId, '')",
"d.eventId=coalesce($eid, d.eventId, '')",
"d.doc_status=coalesce($ds, d.doc_status, 'draft')",
"d._searchText=coalesce($st, d._searchText, d.title)",
```
params add: `"topics": ev.get("topics"), "summary": ev.get("summary"), "sid": ev.get("session_id"), "eid": ev.get("event_id"), "ds": ev.get("doc_status")` — use `ev.get(field)` with NO default so `None` → Cypher `null`, letting `coalesce` fall through to the existing value on partial updates. (CRITICAL: `""` is non-null in Cypher — `coalesce("", d.field, ...)` returns `""` and WIPES existing. Never use `""` defaults in these SET clauses.)
Compute `_searchText` CONDITIONALLY:
```python
has_text = bool(ev.get("title") or ev.get("summary") or ev.get("topics"))
st = _build_search_text(ev.get("title", ""), ev.get("summary", ""), ev.get("topics") or []) if has_text else None
```
`None` (Cypher null) → `coalesce($st, d._searchText, d.title)` preserves existing on partial updates; non-null updates when the event carries text.

After the MERGE, if `ev.get("about_entities")`: call generalized `_create_about_edges(did, ev["about_entities"])`. **Do the label-agnostic generalization (source MATCH `(n:Point {id:$pid})` → `(n {id:$pid})`) HERE in Task 1** — Task 1's test needs it (the about-edge test creates a Document source). Task 2 then handles the rename `point_id`→`source_id` + docstring + `_upsert_event` changes. (This resolves the ordering ambiguity: Task 1 self-contained.)

**Step 4: Run test to verify it passes**

Run: `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_p1 python3 -m pytest tests/test_projection.py::test_upsert_document_capture_fields -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tortoise/projection/entities.py tests/test_projection.py
git commit -m "feat: Document capture fields — topics/summary/sessionId/eventId/_searchText/doc_status + aboutSubject"
```

---

## Task 2: Generalize `_create_about_edges` + extend `_upsert_event` (uses-dict bridge, produces→Document)

**Intent:** (a) about edges must work for Document sources (currently Point-hardcoded); (b) `_upsert_event` must handle structured uses dicts (`{name, kind}`) and route produces→Document vs Object via objectType. This is the provenance correctness core.

**Acceptance:** `_create_about_edges` source MATCH is label-agnostic. `_upsert_event` handles uses as str/list[str]/list[dict] (dict→name+kind), creates `uses→Object {objectKind:kind}`, and routes `produces→Document` when objectType="document" (else Object).

**Files:**
- Modify: `tortoise/projection/edges.py` (_create_about_edges, ~55-87), `tortoise/projection/entities.py` (_upsert_event, ~172-211)
- Test: `tests/test_projection.py`

**Step 1: Write failing tests**

```python
def test_upsert_event_uses_dict_kind():
    # uses=[{name:'tortoise-capture', kind:'skill'}] → Object objectKind='skill'
    proj.apply({"type": "EventRecorded", "id": "evt-1", "eventKind": "sessionCaptured",
                "subject": "agent-pi", "object": "doc-1", "objectType": "Document",
                "uses": [{"name": "tortoise-capture", "kind": "skill"}]})
    rows = proj.g.query("MATCH (e:Event {eventId:'evt-1'})-[:uses]->(o:Object) RETURN o.name, o.objectKind").result_set
    assert rows[0] == ["tortoise-capture", "skill"]

def test_upsert_event_produces_document():
    # objectType='Document' → produces→Document (real node), not Object clone
    rows = proj.g.query("MATCH (e:Event {eventId:'evt-1'})-[:produces]->(d:Document) RETURN d.id").result_set
    assert rows[0][0] == "doc-1"
    rows2 = proj.g.query("MATCH (e:Event {eventId:'evt-1'})-[:produces]->(o:Object) RETURN count(o)").result_set
    assert rows2[0][0] == 0  # no Object clone
```

**Step 2: Run to verify fail**

Run: `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_p2 python3 -m pytest tests/test_projection.py::test_upsert_event_uses_dict_kind tests/test_projection.py::test_upsert_event_produces_document -v`
Expected: FAIL — uses dict → str() corruption; produces→Object always.

**Step 3: Implement**

`edges.py _create_about_edges`: change source MATCH `(n:Point {id:$pid})` → `(n {id:$pid})` (label-agnostic; verify `_try_about_edge` cascade already label-agnostic). Also rename `point_id` → `source_id` and update docstring to "Link entity (Point, Document, or Event) to named entity" — the method now serves Document/Event sources too.

`entities.py _upsert_event`: 
- uses loop: `if isinstance(uses, str): uses=[uses]; elif isinstance(uses, dict): uses=[uses]`; per item: `if isinstance(item, dict): name=item.get("name",""); kind=item.get("kind","other") else: name=str(item); kind="other"`; MERGE Object with `objectKind=$kind`.
- produces: `object_type = inner.get("objectType", "")`; if `object_type == "Document"`: `MERGE (d:Document {id:$obj})` + `MERGE (e)-[:produces]->(d)`; else existing `MERGE (o:Object {name:$obj})` path.

**Step 4: Run to verify pass**

Run: (same command as Step 2)
Expected: PASS.

**Step 5: Commit**

```bash
git add tortoise/projection/edges.py tortoise/projection/entities.py tests/test_projection.py
git commit -m "feat: uses-dict bridge (kind→objectKind) + produces→Document objectType routing + label-agnostic about edges"
```

---

## Task 3: `add_document` extended params + `add_event` public method (api.py)

**Intent:** Expose the capture fields on add_document and a public add_event so ingest.py can emit sessionCaptured with the full provenance payload.

**Acceptance:** `add_document()` accepts topics/summary/session_id/event_id/about_entities and passes to DocumentCreated event. `add_event()` exists, emits EventRecorded with id=eid, subject, eventKind, object, objectType, uses, about_entities, startedAt, endedAt.

**Files:**
- Modify: `tortoise/api.py` (add_document ~205-239, add add_event after add_document)
- Test: `tests/test_api.py`

**Step 1: Write failing test**

```python
def test_add_document_capture_fields_and_add_event():
    api = EventAPI(...)  # in-memory log
    did = api.add_document("doc-1", "Conv", topics=["licensing"], summary="Compared",
                           session_id="s1", event_id="evt-1", about_entities=["agent-pi"])
    eid = api.add_event("evt-1", "sessionCaptured", subject="agent-pi",
                        object_name="doc-1", object_type="Document",
                        uses=[{"name": "tortoise-capture", "kind": "skill"}])
    assert did == "doc-1" and eid == "evt-1"
    events = api._log.read_all()
    assert any(e["type"] == "DocumentCreated" and e.get("topics") == ["licensing"] for e in events)
    assert any(e["type"] == "EventRecorded" and e["eventKind"] == "sessionCaptured" for e in events)
```

**Step 2: Run to verify fail**

Run: `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_p3 python3 -m pytest tests/test_api.py::test_add_document_capture_fields_and_add_event -v`
Expected: FAIL — TypeError (unexpected kwargs).

**Step 3: Implement**

`add_document`: add params `topics=None, summary="", session_id="", event_id="", about_entities=None`; pass through to `_emit("DocumentCreated", ..., topics=topics or [], summary=summary, session_id=session_id, event_id=event_id, about_entities=about_entities or [])`.

`add_event`: new method:
```python
def add_event(self, event_id, event_kind, *, subject="", object_name="", object_type="",
              uses=None, about_entities=None, participants=None, started_at="",
              ended_at="", **extra) -> str:
    self._emit("EventRecorded", id=event_id, eventKind=event_kind, subject=subject,
               object=object_name, objectType=object_type, uses=uses or [],
               about_entities=about_entities or [], participants=participants or [],
               startedAt=started_at, endedAt=ended_at, **extra)
    return event_id
```

**Step 4: Run to verify pass**

Run: (same as Step 2)
Expected: PASS.

**Step 5: Commit**

```bash
git add tortoise/api.py tests/test_api.py
git commit -m "feat: add_document capture fields + add_event public method"
```

---

## Task 4: `sessionCaptured` in CANONICAL_EVENT_KINDS (pack_registry.py)

**Intent:** Register the new event kind so validation passes.

**Acceptance:** `sessionCaptured` in `CANONICAL_EVENT_KINDS`; pack registry validation accepts it.

**Files:**
- Modify: `tortoise/pack_registry.py` (~33-36)
- Test: `tests/test_pack_registry.py`

**Step 1: Write failing test**

```python
def test_session_captured_canonical():
    from tortoise.pack_registry import CANONICAL_EVENT_KINDS
    assert "sessionCaptured" in CANONICAL_EVENT_KINDS
```

**Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_pack_registry.py::test_session_captured_canonical -v`
Expected: FAIL (AssertionError).

**Step 3: Implement**

Add `"sessionCaptured"` to `CANONICAL_EVENT_KINDS` frozenset.

**Step 4: Run to verify pass**

Run: (same as Step 2) — Expected: PASS.

**Step 5: Commit**

```bash
git add tortoise/pack_registry.py tests/test_pack_registry.py
git commit -m "feat: register sessionCaptured event kind"
```

---

## Task 5: Document FTS index + `_searchText` backfill (projection/__init__.py)

**Intent:** Make `_searchText` queryable (FTS index) and cover pre-existing Documents (backfill) — the read-path foundation.

**Acceptance:** `_ensure_indexes` creates FTS `("Document","_searchText")` idempotently. `backfill_document_search_text()` sets `_searchText=title` where NULL.

**Files:**
- Modify: `tortoise/projection/__init__.py` (_ensure_indexes ~355-385)
- Test: `tests/test_search_engine_gaps.py`

**Step 1: Write failing tests**

```python
def test_document_fts_index_exists():
    # after init, CALL db.indexes() includes Document/_searchText
    proj = sdk._get_proj()
    rows = proj.g.query("CALL db.indexes() YIELD type, label, properties").result_set
    assert any(r[1] == "Document" and "_searchText" in r[2] for r in rows)

def test_backfill_document_search_text():
    # create a Document without _searchText, run backfill, verify set
    proj.apply({"type": "DocumentCreated", "id": "old-1", "title": "Old"})
    backfill_document_search_text(proj)  # or via sdk
    rows = proj.g.query("MATCH (d:Document {id:'old-1'}) RETURN d._searchText").result_set
    assert rows[0][0] == "Old"
```

**Step 2: Run to verify fail**

Run: `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_p5 python3 -m pytest tests/test_search_engine_gaps.py::test_document_fts_index_exists tests/test_search_engine_gaps.py::test_backfill_document_search_text -v`
Expected: FAIL — no index, backfill absent.

**Step 3: Implement**

`_ensure_indexes`: add to FTS creation list (guarded by FalkorDB 4.x check + try/except):
```python
try:
    self.g.query("CALL db.idx.fulltext.createNodeIndex('Document', '_searchText')")
except Exception:
    pass  # already exists (idempotent per FalkorDB docs)
```

Also add a `documentKind` range index (structural queries filter by it; prevents full label scan at scale):
```python
try:
    self.g.query("CREATE INDEX FOR (n:Document) ON (n.documentKind)")
except Exception:
    pass  # already exists
```

`backfill_document_search_text(proj=None)`:
```python
def backfill_document_search_text(proj):
    proj.g.query("MATCH (d:Document) WHERE d._searchText IS NULL SET d._searchText = coalesce(d.title, '')")
```

**Step 4: Run to verify pass**

Run: (same as Step 2) — Expected: PASS.

**Step 5: Commit**

```bash
git add tortoise/projection/__init__.py tests/test_search_engine_gaps.py
git commit -m "feat: Document FTS index on _searchText + idempotent backfill"
```

---

## Task 6: Document search read path — verify existing + add ANY(topic) filter (search_engine.py)

**Intent:** Ensure `entity_type=document` works end-to-end (FTS + structural + degradation). NOTE: scoping review verified `run_structural_query` already handles document (kind_field=documentKind) and `run_fts_query` label.capitalize() → Document — this task VERIFIES and adds the topic-list filter.

**Acceptance:** `search_engine` returns Document results via FTS (on _searchText) and structural (documentKind); topic-list filter works via `any()`/`IN`.

**Files:**
- Modify: `tortoise/search_engine.py` (verify ~100-131, ~256-263; add ANY topic filter)
- Test: `tests/test_search_engine_gaps.py`

**Step 1: Write failing test**

```python
def test_document_search_returns_sessions():
    # create Document with topics, run search(entity_type="document", query="licensing")
    sdk.create_document("doc-1", "Conv", documentKind="transcript",
                        props={"topics": ["licensing"], "summary": "Compared",
                               "_searchText": "Conv Compared licensing"})
    results = search_engine.search("licensing", entity_type="document", ...)
    assert any(r["id"] == "doc-1" for r in results)

def test_document_structural_topic_filter():
    # filter by topic in list via any()
    rows = proj.g.query("MATCH (d:Document) WHERE any(t IN d.topics WHERE t = 'licensing') RETURN d.id").result_set
    assert any(r[0] == "doc-1" for r in rows)
```

**Step 2: Run to verify fail**

Run: `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_p6 python3 -m pytest tests/test_search_engine_gaps.py::test_document_search_returns_sessions tests/test_search_engine_gaps.py::test_document_structural_topic_filter -v`
Expected: FAIL — FTS index may not exist yet (task order) or search path gaps.

**Step 3: Implement**

- Verify `run_fts_query` handles entity_type=document (label.capitalize() → Document; needs FTS index from Task 5 — task order ensures it). Update the docstring entity_type list to include `'document'`, `'object'` (currently lists only point/event/subject/operator — misleading).
- Verify `run_structural_query` document branch exists (kind_field=documentKind — confirmed in review).
- Add `any(t IN n.topics WHERE t = $val)` filter support for topic-based structural queries (if the query interface passes a topics filter).
- Confirm degradation_chain passes entity_type through (already entity-agnostic).

**Step 4: Run to verify pass**

Run: (same as Step 2) — Expected: PASS.

**Step 5: Commit**

```bash
git add tortoise/search_engine.py tests/test_search_engine_gaps.py
git commit -m "feat: verify document search read path + topic-list structural filter"
```

---

## Task 7: SDK Document batch fetch + search integration (sdk.py)

**Intent:** Ensure `tortoise_search(entity_type="document")` returns topics/summary/sessionId/eventId so agents get full metadata.

**Acceptance:** SDK document batch fetch returns the new fields; search pipeline returns Documents with correct result shape.

**Files:**
- Modify: `tortoise/sdk.py` (~1818-1830 batch fetch, verify ~1627-1648 search)
- Test: `tests/test_search_engine.py`

**Step 1: Write failing test**

```python
def test_sdk_document_search_returns_metadata():
    # Setup: create a transcript doc with capture metadata (non-vacuous)
    sdk.create_document("test-sdk-doc", "Conv", documentKind="transcript",
                        props={"topics": ["licensing"], "summary": "Test",
                               "sessionId": "s1", "eventId": "e1"})
    results = sdk.tortoise_search("licensing", entity_type="document")
    doc = next(r for r in results if r.get("id") == "test-sdk-doc")
    assert doc["topics"] == ["licensing"]
    assert doc["summary"] == "Test"
    assert doc["sessionId"] == "s1"
    assert doc["eventId"] == "e1"
```

**Step 2: Run to verify fail**

Run: `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_p7 python3 -m pytest tests/test_search_engine.py::test_sdk_document_search_returns_metadata -v`
Expected: FAIL — batch fetch returns only title/documentKind.

**Step 3: Implement**

Extend the document batch fetch query to `RETURN n.id, n.title, n.documentKind, n.topics, n.summary, n.sessionId, n.eventId` and map into results.

**Step 4: Run to verify pass**

Run: (same as Step 2) — Expected: PASS.

**Step 5: Commit**

```bash
git add tortoise/sdk.py tests/test_search_engine.py
git commit -m "feat: SDK document search returns capture metadata"
```

---

## Task 8: Capture extension — TS heuristics + frontmatter + --capture-metadata (tortoise-capture/index.ts)

**Intent:** Stop full-transcript extraction; generate topics/summary in TS; write to frontmatter; pass `--capture-metadata`; use ULID doc_id; remove broken runClassify.

**Acceptance:** Extension writes topics/summary to frontmatter, doc_id = ulid, spawns `ingest --capture-metadata`, no runClassify call. TS heuristics produce non-empty topics for a real conversation.

**Files:**
- Modify: `operations/pi-config/extensions/tortoise-capture/index.ts` (buildMarkdown ~94-123, runIngest ~218, agent_end ~210)
- Test: `operations/pi-config/extensions/tortoise-capture/index.test.ts`

**Step 1: Write failing test**

```typescript
test("deriveTopics extracts topics from conversation", () => {
  const topics = deriveTopics([{role: "user", content: "We discussed licensing and AGPL and AGPL again"}]);
  expect(topics).toContain("AGPL");
});
test("buildMarkdown includes topics and summary in frontmatter", () => {
  const md = buildMarkdown(conversation, meta);
  expect(md).toMatch(/topics:/);
  expect(md).toMatch(/summary:/);
});
```

**Step 2: Run to verify fail**

Run: `cd operations/pi-config/extensions/tortoise-capture && npx jest index.test.ts`
Expected: FAIL — deriveTopics/buildMarkdown undefined.

**Step 3: Implement**

- `deriveTopics(conversation)`: regex `[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*` on user messages, stoplist-filter, ≥3 chars, top 10. NOTE (known limitation): Title Case words only — lowercase terms (api, cli, oauth2) and multi-word phrases (pull request) are missed; a future TF-IDF or LLM pass (tracked in #133) covers these.
- `deriveSummary(conversation)`: first user message truncated to 200 chars.
- `buildMarkdown`: add `topics: [a, b]` (comma-joined) + `summary: "..."` to frontmatter.
- agent_end: doc_id = ulid(); replace `runIngest(filePath, config)` with `runIngest(filePath, config, "--capture-metadata")`; REMOVE `runClassify(filePath, config)`.
- Add `runIngest(..., extraArg)` to append the flag.

**Step 4: Run to verify pass**

Run: (same as Step 2) — Expected: PASS.

**Step 5: Commit**

```bash
git add operations/pi-config/extensions/tortoise-capture/index.ts operations/pi-config/extensions/tortoise-capture/index.test.ts
git commit -m "feat: capture extension — TS topics/summary heuristics, ulid doc_id, --capture-metadata, remove runClassify"
```

---

## Task 9: Ingest `--capture-metadata` flag — wire add_document + add_event, skip extraction (ingest.py)

**Intent:** The capture path: parse topics/summary/sessionId from frontmatter, emit Document + sessionCaptured Event, SKIP LLM extraction. Full extraction (no flag) unchanged.

**Acceptance:** `ingest.py --capture-metadata <file>` creates Document + sessionCaptured Event + uses→Skill, ZERO Points. `ingest.py <file>` (no flag) still fully extracts. doc_id = ULID filename.

**Files:**
- Modify: `tortoise/ingest.py` (argparse ~87-110, main ~115-200)
- Test: `tests/test_ingest.py`

**Step 1: Write failing tests**

```python
def test_capture_metadata_creates_document_no_points():
    # Create temp .md with topics/summary frontmatter
    import tempfile, pathlib
    md = pathlib.Path(tempfile.mkdtemp()) / "sess.md"
    md.write_text("---\ntitle: Test\ntopics: licensing, AGPL\nsummary: Compared\nsessionId: s1\n---\n\n## User\nDiscuss licensing\n")
    # Run ingest --capture-metadata against test DB
    run_main([str(md), "--db", test_db, "--capture-metadata"])  # or subprocess
    # Assert: Document exists with fields (doc_id is a ULID from the extension — discover by property, not hardcoded id)
    rows = proj.g.query("MATCH (d:Document) WHERE d.sessionId = 's1' RETURN d.topics, d.summary, d.eventId").result_set
    assert rows and rows[0][0] == ["licensing", "AGPL"]
    # Assert: ZERO Points extracted
    pts = proj.g.query("MATCH (p:Point) RETURN count(p)").result_set[0][0]
    assert pts == 0
    # Assert: sessionCaptured Event + produces→Document + uses→Skill (match via sessionId)
    ev = proj.g.query("MATCH (e:Event {eventKind:'sessionCaptured'})-[:produces]->(d:Document) WHERE d.sessionId = 's1' RETURN count(e)").result_set[0][0]
    assert ev >= 1
    uses = proj.g.query("MATCH (e:Event {eventKind:'sessionCaptured'})-[:uses]->(o:Object {objectKind:'skill'}) RETURN count(o)").result_set[0][0]
    assert uses >= 1

def test_full_ingest_unaffected():
    # Run ingest WITHOUT --capture-metadata on same file → Points created
    run_main([str(md), "--db", test_db])
    pts = proj.g.query("MATCH (p:Point) RETURN count(p)").result_set[0][0]
    assert pts > 0
```

**Step 2: Run to verify fail**

Run: `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_p9 python3 -m pytest tests/test_ingest.py -v`
Expected: FAIL — no --capture-metadata flag.

**Step 3: Implement**

- argparse: `ap.add_argument("--capture-metadata", action="store_true")`.
- In main, after frontmatter parse: `topics_raw = fm.get("topics", ""); topics = [t.strip() for t in topics_raw.split(",") if t.strip()] if topics_raw else []; summary = fm.get("summary", ""); session_id = fm.get("sessionId", "")`.
- Pass to `add_document(..., topics=topics, summary=summary, session_id=session_id, event_id=<new ulid>, about_entities=[agent_subject])`.
- If `args.capture_metadata`: call `api.add_event(<eid>, "sessionCaptured", subject=<agent>, object_name=source_id, object_type="Document", uses=[{"name":"tortoise-capture","kind":"skill"}])`, then **SKIP** the `if is_doc: extract_from_document(...)` + EP propagation blocks.
- doc_id/source_id = the ULID filename (from extension).

**Step 4: Run to verify pass**

Run: (same as Step 2) — Expected: PASS.

**Step 5: Commit**

```bash
git add tortoise/ingest.py tests/test_ingest.py
git commit -m "feat: ingest --capture-metadata — Document+Event, skip extraction; full path unchanged"
```

---

## Task 10: Integration verification — end-to-end capture smoke

**Intent:** Prove the whole path works: capture → Document+Event+edges, searchable, idempotent, on-demand preserved.

**Acceptance:** All integration checks pass (below). Tests use test-prefixed graphs + test_guard.

**Files:**
- Test: `tests/test_session_capture_e2e.py` (NEW)

**Step 1: Write the E2E test**

```python
def test_capture_e2e():
    sdk.test_guard()
    # 1. capture: Document with fields, ZERO Points
    # 2. sessionCaptured Event + produces→Document + uses→Skill + performs→Subject + aboutSubject
    # 3. search(entity_type="document", query="topic") returns the session
    # 4. double-capture = no-op (MERGE)
    # 5. full ingest (no flag) still extracts Points
```

**Step 2: Run to verify fail**

Run: `TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_e2e python3 -m pytest tests/test_session_capture_e2e.py -v`
Expected: FAIL initially (not all wired yet).

**Step 3-4: Implement wiring gaps until pass**

Iterate: fix any integration gap the test surfaces (field plumb, edge, search). Run until PASS.

**Step 5: Commit**

```bash
git add tests/test_session_capture_e2e.py
git commit -m "test: end-to-end session capture — Document+Event+edges, search, idempotency, on-demand preserved"
```

---

### Verification Plan

| Layer | Runs | Covers |
|---|---|---|
| Unit | pytest (api, pack_registry, ingest helpers) | add_document fields, add_event, sessionCaptured kind, frontmatter parse |
| Integration | pytest (projection, search_engine, ingest) | Document fields persist, FTS index + backfill, uses-dict bridge, produces→Document, search returns sessions |
| E2E | pytest (session_capture_e2e) | Full capture→Document→search→idempotency→on-demand |
| TS | jest (index.test.ts) | deriveTopics/deriveSummary, frontmatter, ulid |
| Migration | pytest (backfill test) | Existing Documents get _searchText=title |

**Domain routing:** architecture=complex → integration-heavy; ontology=complex → verify pack kinds; config=standard → unit+integration; content=low → TS heuristic unit tests.

**Deferred (non-code):** 22K legacy content migration beyond FTS backfill → #49 (context removal) follow-up; retention UI → hosted.

---

### Execution Mode

**≤ 8 tasks → subagent-driven (this session).** This plan has 10 tasks (2 are verification-heavy). Recommend parallel session if context pressure; subagent-driven acceptable with fresh-subagent-per-task + code review.
