> ## ⚠️ AMENDED — `rename` WAS WITHDRAWN FROM THIS PLAN (read before implementing)
>
> **This plan's rename rows are SUPERSEDED.** Any step below that journals a
> `name`-bearing write (a `rename` record), or that asserts a rename round-trips,
> describes behaviour **this lane deliberately does NOT ship**. Implementing Task 1
> as originally written **reintroduces a verified regression** — see #4769.
>
> **Why.** A journaled `rename` folds inline in pass 1b, but
> `_fold_object_superseded` falls back to matching **by NAME** for legacy id-less
> records (#2164 ISSUE-B) and that fold is **DEFERRED** to a sweep that runs
> *after* pass 1b. The rename therefore moves the node's name before the sweep
> matches on it, and the supersede is **dropped**: `rebuild_all` returns `live`
> while live/`apply()` keep `superseded`. Pre-rename-journalling the match held,
> so this was a regression the lane introduced — caught by review round 3.
>
> **What actually ships:** `restatus` / `revise` (**#3312**) and `delete`
> (**#3300**). The op set partitions cleanly because `classify_entity_mutation_op`
> returns `rename` iff `name in props`, and no other op touches `name`.
>
> **Where rename went:** **#4769**, which lands rename journalling *together with*
> the structural sweep-ordering fix — three review rounds each found a different
> defect at that same boundary, because `rebuild_all` does not model journal order
> **across its deferred sweeps**.
>
> **Interim behaviour:** a `name`-bearing write applies live and journals **every
> other key**, withholding only `name`, and **warns on every occurrence** naming
> #4769. The `rename` FOLD arm is retained, and it applies `state` — so a
> raw/legacy record folds only if it carries the new name as `state["name"]`.
>
> ---

<!-- research-path: docs/plans/2026-09-22-unjournaled-mutation-class.md (scope: https://github.com/daniel-ospina/tortoise/issues/3312#issuecomment-5779625594) -->

# Unjournaled durable mutation — `EntityMutated` op extension Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make a durable property mutation on a canonical entity (and a `delete_point` hard delete) reproduce on replay, by extending #3299's `EntityMutated` op set — so `rebuild_all` / `apply` / `recover_from_log` / `restore` materialise the state the mutation intended.

**Team:** organisation-design-team
**Role:** product-implementer
**Rev:** v3 — v1 reviewed by 5 reviewers, v2 by 3 (2 P0 + 5 P1 + P2s), v3 by 1. Every finding was accepted and folded in; see `## Review log` for the per-finding resolutions.

**Architecture:** #3299 already declares the carrier — `EntityMutated`, a JSONL-only record with an `op` discriminator, folded by `_fold_entity_mutation`. This plan implements the missing producers for the two confirmed mechanisms. **(C1)** `_update_entity`'s non-Point branches write `SET n += props` and emit nothing, so replay reverts the entity to its creation snapshot; the fix journals the **post-write state of the mutated properties** — the write's own keys carrying the graph's **stored** values — under the recorded `state` key, and folds it with the recorded blind idempotent `SET n += state`, keyed on the identity table. **(C2)** `delete_point` hard-deletes live but emits `PointRetracted`, whose fold tombstones; the fix splits the two stores — a `:GraphEvent`-only `PointRetracted` (subscriber surface unchanged) plus a JSONL-only `EntityMutated op="delete"` (replay hard-deletes). One record type, one meaning.

---

## Read before writing code

1. **`tests/test_entity_delete_rebuild.py`** — the established harness for this exact record type. Its `env` fixture is the shape every test here must use; v1 of this plan invented an SDK API that does not exist and its red step was therefore vacuous. **Verified surfaces** (do not re-invent):
   - fixture: `TortoiseSDK(str(tmp_path/"x.db"), event_log_path=str(events/"events.jsonl"))` → yield `(sdk, events)`, `sdk.close()`
   - replay: `sdk._get_proj().rebuild_all(str(events))` — **`TortoiseSDK` has no `rebuild_all`**
   - journal read: `EventLog(str(events/"events.jsonl")).read_all()`
   - graph read: `sdk._get_proj().g.query(cypher, params=…).result_set`
   - event store: `sdk.events_poll(after=None)` → `{"events": [...], "next_cursor": …}`; rows carry a parsed `payload` dict. **There is no `tortoise_events_poll` SDK method** (that is the MCP tool name) and **no `since_seq` parameter anywhere in the repo**.
   - vector leg (needs the `embeddings` extra — see Task 5): `tortoise.search_engine.run_vector_query(graph, query_vec, …, entity_type="document")` — signature is `(graph, query_vec, limit, …)`, **not** `(text, entity_type=)`.
   - in-memory projection: `InMemoryProjection().points` (a dict) — **no `list_points()`**.
2. **`tortoise/projection/entities.py:365-374`** — *"None values are excluded — Cypher null semantics in SET maps are **unreliable**."* The repo deliberately avoids `SET n += {k: null}`. **C1's removal fidelity depends on exactly that construct.** Task 6 Step 2 makes this an explicit gate with a designed fallback; do not assume it from the docker lane alone.
4. **`sdk.create_entity(type, name, …)` returns `{"node": {...}, "nudges": [...]}` — there is NO top-level `id`.** `obj["id"]` raises `KeyError`. Use `sdk.create_entity(...)["node"]["id"]`, or a thin alias that returns the node. `create_point` does return the node at top level — only `create_entity` wraps.
5. **A missing property on an EXISTING node returns `[[None]]`, not `[]`.** `_rows(... RETURN o.note) == []` is true only when the *node* is absent. Assert absence with `RETURN o.note IS NULL AS m` → `[[True]]`, or `RETURN count(o.note)` → `[[0]]`.
6. **The embedded engine is unreachable from `env` while `TORTOISE_DB_URI` is exported** — the #1647 redirect flips an explicit-path `TortoiseSDK` to docker. Run the embedded leg under the URI-less carve-out (`TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_unjournaled_mutation_class.py`), where `env` is already embedded.
7. **Test helpers (`_journal`, `_rows`, `_deletes`, `_REC`) are per-file in this repo** (`tests/test_entity_delete_rebuild.py`, `tests/test_operator_annotator_rebuild.py`, `tests/test_rebuild_recreate_content_parity.py` each carry their own). This plan **follows that convention rather than extracting a shared module** — recorded `keep separate`: extraction would put three unrelated test files in this diff for no behavioural gain.
8. **`tortoise/projection/__init__.py:1376-1380`** — the "ONLY source of the label→id-property mapping" claim. It is **false as stated**: `_RESOLVE_BRANCHES` (`:4818-4829`) is a deliberate superset adding `("Source","url")`, and `navigation._ROOT_BRANCHES` (`navigation.py:20`) is a third copy. Do not repeat the "cannot drift" claim in this diff.

### Pattern Research

> **Gate skipped** — plan touches **zero third-party dependencies**. In-repo Python only; no new or changed library API, no package added, no external service introduced. The only external system is the pre-existing FalkorDB driver. (`writing-plans` skip rule: "Step B of the research intake gate … when the plan touches zero third-party dependencies".)

**Step A (prior-research intake) — from the scope's §6, still governing:**

- **A · vocabulary granularity (recorded-decision conflict).** Canonical event-naming guidance favours intent-revealing types and names `DataModified`/`CustomerUpdated` as anti-patterns (eventsourcing.dev; EventSourcingDB "Naming Events Beyond CRUD"); the dissenting source sanctions intent-in-property (berthon.dev, 2024-12-18), with the "event explosion" cost named. **This contradicts #3299's recorded op-set decision → a hard stop against silent adoption; the route is a reopen, which we do not take.** The record has exactly one consumer (the replay fold), so cross-consumer legibility conventions are weak here.
- **B · `replay == live` (R9).** History-based given/when/then + a rebuild verification against the real DB. The **vacuity trap** ("two equally incomplete projections compare equal") is answered by a consumed-vs-appended watermark (Dudycz's `revision`; Debezium read offset) — filed as **#4630** — and operationally by this plan's **non-folded-set assertion** (Tasks 1/6).
- **C · partial-write ordering (WAL).** Bedrock: *"write log records before corresponding data"* (PostgreSQL WAL; *Database Internals*: "First, log it. Then, do it."). The repo's **post-apply emit is the forbidden ordering**. **Not adopted in this lane** (class-wide, ~20 writers) — filed as **#4631**, which now also records the **interleaving** variant (see the Concurrency row).
- **D · tombstone vs hard delete.** "One event type must not mean two things"; canonical event sourcing has **no DELETE at all**, so the repo's hard delete is a **deliberate departure** — an **`OVERRIDES:` line is posted on #2296**.

### Integration Surface Map

| Surface Type | Specific Surface | Data Flow | Contract | Test layer |
|---|---|---|---|---|
| **DB (graph) — node props** | `MATCH (n:<label> {<idProp>:$id}) SET n += $props RETURN [k IN $keys \| properties(n)[k]] AS vals` on the five non-Point canonical labels | Write + read-back | Returns the applied keys' **stored** values in `keys` order; **`[]` on a no-match** (`count(n)` beside `properties(n)` becomes a grouping key — do NOT add it) | **Integration** |
| **DB (graph) — replay fold** | `MATCH (n:<label> {<idProp>:$id}) SET n += $s RETURN count(n)` | Write | Idempotent; `[[0]]` on a miss | **Integration** |
| **DB (graph) — hard delete** | `delete_point` `MATCH (n:Point {id:$id}) DETACH DELETE n` | Write | Unchanged live behaviour | **Integration** |
| **Event/journal — JSONL** | `EventLog.append` (`log.py:41-45`) | Out | `json.dumps` with **no `default=`**; `_emit_event` catches and logs at WARNING and returns normally (`sdk.py:2637-2642`) — so a serialization **or I/O** failure leaves the live write durable and the journal short (**live-ahead-of-journal**). Reason `state` carries the graph's *stored* value (kills the `TypeError` half) — the I/O half is Task 6's test, not a claim | **Integration** |
| **Event store — `:GraphEvent`** | `_emit_event` writes it **iff `type_ ∈ _GRAPH_EVENT_TYPES`** — a missing `payload` is synthesized (`{"id": …}`/`{}`), **not** a gate (`sdk.py:2574-2598`) | Out | `tortoise_events_poll` sees no change from C2 | **Integration** |
| **Replay engines** | `rebuild_all` (inline pass-1b), `FalkorProjection.apply` (⇒ `rebuild(log)`, `recover_from_log`, `restore`'s JSONL fallback) | Read (replay) | Journal order is the correctness contract | **Integration** |
| **⚠️ Hard-delete seq readers** | `journal_hard_delete_seqs` (`projection/__init__.py:1690`) — consumed by `rebuild_all`'s deferred `EntityLinked` sweep (`:4311`), `consistency.recover_from_log:223`, `backup.restore:167`. Deliberately **excluded** `PointRetracted` ("retraction is deliberately NOT included", `:1705`) | Read | **C2 changes this record's classification from "retraction, excluded" to "hard delete"** → a `delete_point`ed Point's seq now enters the set, suppressing `EntityLinked` replays touching it. Intended (live `DETACH DELETE` also destroys the edges) — **assert it** | **Integration** |
| **⚠️ Fail-closed guard input** | `FalkorProjection._journal_hard_deleted_ids` (`:2622`) → the **#3947 pre-wipe `_assert_episodic_points_recreatable` guard** (`:2671/2718`) | Read | **C2 also flips this**: an episodic Point deleted via `delete_point` goes from *not exempt* (the guard would refuse the rebuild) to *exempt* (the rebuild proceeds). Intended (an explicit delete is an explicit intent) — **assert it, and assert a merely retracted Point is still NOT exempt** | **Integration** |
| **In-memory fold** | `InMemoryProjection._apply_one` (`projection/__init__.py:1494`) | Internal | Point-only `{id: point}` dict; the five labels are out of its model | **Unit** |
| **Public doors** | `sdk.update_entity`, `sdk.delete`, `sdk.delete_point`, `sdk.delete_entity`, MCP `tortoise_update_entity` / `tortoise_delete_point` / `tortoise_delete_entity` | In | Must all reach the journaled path | **Integration** (MCP is a delegate — assert delegation + the write, not a separate behaviour) |
| **Backup/restore** | `backup.restore(into_falkor=True)` | Read (replay) | RDB short-circuit at `backup.py:143-150` bypasses folds; the JSONL fallback (`:169-173`, `proj.apply` per record) does not | **Integration** (see Task 6's decision) |
| **Concurrency** | Two concurrent `EventLog.append` calls | Contested | **NOT "none".** `append` is `open(path,"a")` + `write(json+"\n")` with **no lock**; a record exceeding the buffer is multiple `write()` syscalls, so lines can tear, and `read_all` **raises** on a malformed mid-file line (it only skips a malformed trailing one). Independently, append order ≠ `SET` order, so two concurrent mutations on one entity can replay in reverse. C1 makes journal **order** load-bearing for property mutations for the first time | **Disclosed + referred to #4631** (comment posted); **no flaky test is written** — see Task 7 |

**Bug Pattern Flags**

- **FAIL-OPEN (silent-failure class):** `_emit_event`'s JSONL append is best-effort — a failure is caught, logged at WARNING, and swallowed. A malformed payload *or* an `OSError` therefore **loses durability silently** while `update_entity` reports success. → Task 6 tests both halves.
- **Two-writer-one-event (semantics):** `PointRetracted` has two producers with different live end-states. → Task 5.
- **Two readers change classification:** the hard-delete seq set and the #3947 guard both key on the record's class. → Task 5's named assertions.
- **Ordering-not-enforced:** cross-file journal ordering (#21, declared, row 11) **and** concurrent interleaving (#4631, disclosed).
- **False invariant:** `_CANONICAL_ENTITY_ID_PROPS` is not the only label→key table (`_RESOLVE_BRANCHES`, `_ROOT_BRANCHES`) — and `update_entity(<source url>)` silently writes nothing (**filed #4649**). Do not re-assert singleness.

### Verification Plan

| Layer | Depth | What it proves |
|---|---|---|
| **Unit** | yes | `_apply_one` no-ops the state ops and warns on an unknown op; the op-vocabulary derivation holds; the absent-node guard never emits |
| **Integration (real FalkorDB)** | the bulk | Every acceptance row of the scope, on the public SDK + the MCP door; all four replay engines; the non-folded set empty; the two seq-reader behaviour changes; idempotency; append failure |
| **E2E (Playwright)** | **n/a** | No UI surface |
| **UX / accessibility** | **n/a** | UX=low, no UI change |
| **Content / config / research** | **n/a** | Not in this change |
| **Embedded lane** | **required for the removal row** | `entities.py:370` warns null-in-`SET`-map is unreliable; the removal row must pass on the embedded engine too or the fold must change (Task 5) |

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_unjournaled_mutation_class.py -v
uv run pytest tests/ -q                 # regression: no new failures/warnings
uv run ruff check tortoise/ tests/      # F841/F401 — see the lint notes in Tasks 2/4
```

### Journey Test Map

```markdown
### Journey: An operator archives an Object, then the graph is rebuilt
1. `update_entity(obj, status="archived")` → live status is `archived` → `test_restatus_round_trips`
2. `rebuild_all()` → replayed status is `archived`, not the creation snapshot → `test_restatus_round_trips`
3. the same through the MCP door → identical outcome → `test_door_journals_and_replays[mcp_update_entity]`

### Journey: An operator renames an Object, then the graph is rebuilt
1. `update_entity(obj, name="B")` → live name is `B` → `test_rename_round_trips`
2. `rebuild_all()` → replayed name is `B`; no node carries the old name → `test_rename_round_trips`

### Journey: An operator hard-deletes a Point, then the graph is rebuilt
1. `delete_point(pid)` → node absent live; `events_poll` still shows a `PointRetracted` row → `test_event_store_row_preserved`
2. `rebuild_all()` → node absent (no tombstone) → `test_delete_point_round_trips`

### Failure Modes
- Journal append fails (serialization or OSError) → **Expected:** the live write stands, a WARNING names the lost record, and the divergence is *visible* to a later non-folded check → `test_append_failure_is_loud_and_observable`
- Update targets an absent node → **Expected:** no live write, no phantom record, no crash → `test_absent_node_no_phantom_record`
- A prop value the graph rejects (nested dict) → **Expected:** raises, node unchanged, **no record** → `test_graph_rejected_value_writes_neither_node_nor_record`
- Rename record arrives before its creation → **Expected:** declared divergence + fold-miss warning → `test_cross_file_rename_is_declared_divergence`
- A pre-fix journal's `delete_point` hard delete → **Expected:** declared divergence (tombstone), **exempt** from live==replay → `test_prefix_journal_is_a_declared_divergence`
- An old binary replays a new op → **Expected:** silent no-op, re-converging on a new-binary rebuild → `test_downgrade_old_fold_drops_new_op`
- A record replayed twice → **Expected:** identical state, no warning for the mutation ops → `test_replay_is_idempotent`
```

**Tech Stack:** Python 3.12, pytest, FalkorDB (docker; plus the embedded lane for the removal row), no new dependencies. The vector test additionally needs `uv sync --extra embeddings --extra parity` and `battery.runner.retrieval_preflight.require_hybrid_retrieval` to pass — a keyword-only install silently disables the dense leg (`AGENTS.md`).

**Adversarial Threat Surface:** (not adversarial) — no gate, path, credential, or enforcement code. The one attacker-adjacent surface is **label interpolation into Cypher**, which stays closed by `_CANONICAL_ENTITY_LABELS` / `_ENTITY_ID_PROP` and is asserted in Task 4.

---

## Task dependency map

```
T1 (red set) ──▶ T2 (vocabulary + helper) ──▶ T3 (producer) ──▶ T4 (fold arms) ──▶ T5 (C2)
                                                                                    │
                                              T6 (engines + residuals + failure modes) ◀┘
                                                                                    │
                                              T7 (coverage + embedded lane) ◀────────┘
                                                                                    │
                                              T8 (docs + verification) ◀────────────┘
```

T2–T5 all edit `tortoise/sdk.py` and/or `tortoise/projection/__init__.py`; **do not parallelise them** across worktrees. T5 (C2) touches a disjoint function from T2–T4's C1 edits and *can* be developed concurrently by a second agent on a non-overlapping region, but the two must merge before T6.

---

### Task 1: Red — the acceptance set

**Intent:** Pin the defect (and the payload rule) before touching it.
**Acceptance:** every **behavioural** test FAILS on the current tree for the **right reason** — the mutation tests on the *replayed* value, the record-shape tests on `len(recs) == 0`, the removal row because nothing is journaled. A `KeyError`/`AttributeError` failure is a **broken test**, not a red one. **Three Task-1 tests are green-on-arrival derivation guards, not reds** — `test_exactly_one_entity_mutated_builder` (1 site today), `test_state_ops_are_explicit_noops` (today's `_apply_one` already ignores the three ops), and `test_graph_rejected_value_writes_neither_node_nor_record` (the pre-fix path also raises `… primitive types` and emits nothing). Do not burn time trying to redden an invariant that must stay green.

**Files:** Create/modify `tests/test_unjournaled_mutation_class.py`

**Step 1: the harness.** Copy `tests/test_entity_delete_rebuild.py`'s helper shape (`env`, `_journal`, `_rows`), then add:

```python
_REC = "EntityMutated"
_STATE_OPS = ("rename", "restatus", "revise")


def _fold_warnings(caplog) -> list[str]:
    """The NON-FOLDED SET for C1 — state-op fold misses + any unknown/pending op.

    Deliberately op-aware. A bare substring match on "fold matched no entity"
    would ALSO collect the pre-existing pass-1b DELETE warning (its message is
    literally "rebuild: EntityMutated delete fold matched no entity …"), which
    the policy excludes as legitimately idempotent — so the lane's headline
    evidence would go red on a *valid* journal (a retried delete; restore's
    JSONL fallback onto a non-empty graph).
    """
    out = []
    for r in caplog.records:
        if r.levelno < logging.WARNING:
            continue
        m = r.message
        if "unknown EntityMutated op" in m or "has no fold arm" in m:
            out.append(m)
        elif "fold matched no entity" in m and not m.startswith(
                "rebuild: EntityMutated delete fold"):
            out.append(m)
    return out


def _recs(events, oid) -> list[dict]:
    return [e for e in _journal(events)
            if e.get("type") == _REC and e.get("id") == oid]
```

**Step 2: the failing tests.** Note `["node"]["id"]` and the `IS NULL` predicate (see `## Read before writing code`).

```python
class TestUpdateEntityDurability:
    def test_restatus_round_trips(self, env, caplog):
        sdk, events = env
        oid = sdk.create_entity("object", name="O1", objectKind="k")["node"]["id"]
        sdk.update_entity(oid, status="archived")
        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))
        assert _rows(sdk._get_proj(),
                     "MATCH (o:Object {id:$i}) RETURN o.status", i=oid) == [["archived"]]
        assert _fold_warnings(caplog) == []

    def test_rename_round_trips(self, env, caplog):
        sdk, events = env
        oid = sdk.create_entity("object", name="A", objectKind="k")["node"]["id"]
        sdk.update_entity(oid, name="B")
        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))
        proj = sdk._get_proj()
        assert _rows(proj, "MATCH (o:Object {id:$i}) RETURN o.name", i=oid) == [["B"]]
        assert _rows(proj, "MATCH (o:Object {name:'A'}) RETURN o.id") == []  # node absence
        assert _fold_warnings(caplog) == []

    def test_mixed_write_records_once(self, env):
        """name+status in ONE write: one record, op=rename, `state` carries BOTH."""
        sdk, events = env
        oid = sdk.create_entity("object", name="M1", objectKind="k")["node"]["id"]
        sdk.update_entity(oid, name="M2", status="archived")
        recs = _recs(events, oid)
        assert [r["op"] for r in recs] == ["rename"]
        assert recs[0]["state"] == {"name": "M2", "status": "archived"}

    def test_reserved_name_props_do_not_corrupt_the_record(self, env):
        """`type`/`point`/`state`/`old`/`new` as TENANT props, incl. the SCALAR
        `props=` form (`_coerce_props` FLATTENS a dict-valued `props=`, so only
        the scalar survives as a literal prop).

        `type`/`point`/`old`/`new` ARE written live (that is the point of the
        test) — so the node-absence loop covers only keys the caller did NOT
        write. The record's own envelope is checked on the RECORD."""
        sdk, events = env
        oid = sdk.create_entity("object", name="R1", objectKind="k")["node"]["id"]
        sdk.update_entity(oid, type="X", point="P", old="o", new="n")
        sdk.update_entity(oid, props="literal")
        recs = _recs(events, oid)
        assert len(recs) == 2
        assert all(r["type"] == _REC for r in recs)      # envelope type intact
        assert recs[0]["state"]["type"] == "X"           # nested, NOT spread
        assert "old" not in recs[0] and "new" not in recs[0]   # the RECORD has neither
        proj = sdk._get_proj()
        for leaked in ("state", "event_id", "projection_version"):
            assert _rows(proj, f"MATCH (o:Object {{id:$i}}) RETURN o.{leaked} IS NULL AS m",
                         i=oid) == [[True]]              # no phantom node prop

    def test_absent_node_no_phantom_record(self, env):
        sdk, events = env
        before = len([e for e in _journal(events) if e.get("type") == _REC])
        sdk.update_entity("obj_does_not_exist", status="archived")
        assert len([e for e in _journal(events) if e.get("type") == _REC]) == before

    def test_property_removal_round_trips(self, env, caplog):
        """A full `properties(n)` SNAPSHOT cannot replay this — the removed key
        is ABSENT from the snapshot, so `SET n += snapshot` resurrects it from
        the creation record. Row 4d."""
        sdk, events = env
        oid = sdk.create_entity("object", name="D1", objectKind="k", note="T")["node"]["id"]
        sdk.update_entity(oid, note=None)
        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))
        assert _rows(sdk._get_proj(),
                     "MATCH (o:Object {id:$i}) RETURN o.note IS NULL AS m",
                     i=oid) == [[True]]                  # NOT `== []` — the node exists
        assert _fold_warnings(caplog) == []

    def test_cleared_identity_prop_does_not_resurrect(self, env, caplog):
        """`{"name": None, "status": "s"}` classifies as `rename` with a null
        `name`; `name` is Object/Subject's MERGE key, so a cleared name plus a
        later re-registration is its own path — the `note` row does not cover it."""
        sdk, events = env
        oid = sdk.create_entity("object", name="CL1", objectKind="k")["node"]["id"]
        sdk.update_entity(oid, name=None, status="s")
        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))
        assert _rows(sdk._get_proj(), "MATCH (o:Object {id:$i}) RETURN o.name IS NULL AS m",
                     i=oid) == [[True]]
        assert _fold_warnings(caplog) == []

    def test_record_state_keys_match_props(self, env):
        sdk, events = env
        oid = sdk.create_entity("object", name="K1", objectKind="k")["node"]["id"]
        sdk.update_entity(oid, status="s2", objectKind="k2")
        rec = _recs(events, oid)[-1]
        assert set(rec["state"]) == {"status", "objectKind"}
        assert rec["state"]["status"] == "s2"            # the GRAPH'S stored value

    def test_unchanged_value_still_journals_and_replays(self, env):
        """`status=<the value it already has>`: keys non-empty + a match, so a
        record lands. Pins that the gate is not "journal only on change"."""
        sdk, events = env
        oid = sdk.create_entity("object", name="N1", objectKind="k", status="live")["node"]["id"]
        sdk.update_entity(oid, status="live")
        assert len(_recs(events, oid)) == 1
        assert _recs(events, oid)[0]["state"] == {"status": "live"}

    def test_repeated_noop_write_record_count(self, env):
        """Resource-exhaustion pin (the one family with no other coverage): the
        emit gate is "keys non-empty AND matched", NOT "changed" — so N identical
        writes append N records, replayed by every rebuild. Linear growth is the
        accepted cost of the always-journal gate; this row makes a later
        "journal only on change" optimisation RED instead of silently changing
        the durability contract."""
        sdk, events = env
        oid = sdk.create_entity("object", name="NO1", objectKind="k", status="live")["node"]["id"]
        for _ in range(3):
            sdk.update_entity(oid, status="live")
        assert len(_recs(events, oid)) == 3

    def test_empty_props_emits_no_record(self, env):
        sdk, events = env
        oid = sdk.create_entity("object", name="E1", objectKind="k")["node"]["id"]
        before = len([e for e in _journal(events) if e.get("type") == _REC])
        sdk.update_entity(oid)
        assert len([e for e in _journal(events) if e.get("type") == _REC]) == before

    def test_graph_rejected_value_writes_neither_node_nor_record(self, env):
        """A nested-dict prop value reaches the graph and is rejected BEFORE any
        append — the write-vs-journal ordering the whole lane depends on. The
        match= is what keeps this from passing on an unrelated crash."""
        import redis.exceptions
        sdk, events = env
        oid = sdk.create_entity("object", name="G1", objectKind="k")["node"]["id"]
        before = len([e for e in _journal(events) if e.get("type") == _REC])
        with pytest.raises(redis.exceptions.ResponseError, match="primitive types"):
            sdk.update_entity(oid, meta={"a": 1})
        assert _rows(sdk._get_proj(),
                     "MATCH (o:Object {id:$i}) RETURN o.meta IS NULL AS m",
                     i=oid) == [[True]]
        assert len([e for e in _journal(events) if e.get("type") == _REC]) == before


class TestReviseAndIdentity:
    def test_revise_generic_prop_round_trips(self, env, caplog):      # row 3
        sdk, events = env
        oid = sdk.create_entity("object", name="O3", objectKind="k")["node"]["id"]
        sdk.update_entity(oid, objectKind="k2")
        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))
        assert _rows(sdk._get_proj(), "MATCH (o:Object {id:$i}) RETURN o.objectKind",
                     i=oid) == [["k2"]]
        assert _fold_warnings(caplog) == []

    def test_event_prop_update_round_trips(self, env, caplog):        # row 4
        """The ONLY case exercising `_ENTITY_ID_PROP["Event"] == "eventId"`
        through the new fold."""
        sdk, events = env
        ev = sdk.create_entity("event", name="Ev1", eventKind="k")
        eid = ev["node"]["id"]
        sdk.update_entity(eid, eventStatus="done")
        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))
        assert _rows(sdk._get_proj(),
                     "MATCH (e:Event {eventId:$i}) RETURN e.eventStatus", i=eid) == [["done"]]
        assert _fold_warnings(caplog) == []

    def test_sibling_survives_the_rebuild(self, env):                 # row 6
        """The fidelity check: the fix must not 'cure' divergence by dropping
        everything."""
        sdk, events = env
        keep = sdk.create_entity("object", name="Keep1", objectKind="k")["node"]["id"]
        gone = sdk.create_entity("object", name="Gone1", objectKind="k")["node"]["id"]
        sdk.delete_entity(gone)
        sdk._get_proj().rebuild_all(str(events))
        assert _rows(sdk._get_proj(), "MATCH (o:Object {id:$i}) RETURN o.id", i=keep) == [[keep]]

    def test_re_mention_after_revise(self, env, caplog):              # row 9
        """create(A) → restatus → register A again: the re-registration path vs
        the `op=restatus` state must not clobber asymmetrically.

        NOTE: the "ObjectRegistered re-fire" this row originally reasoned about
        does not reach the journal — the second `create_entity` is probe-gated to
        the FIRST canonical registration (`sdk.py:17162-17166`), so only one
        `ObjectRegistered` line exists. The row asserts the OBSERVABLE property
        instead: live == replay after the re-registration.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="RM1", objectKind="k")["node"]["id"]
        sdk.update_entity(oid, status="archived")
        sdk.create_entity("object", name="RM1", objectKind="k")      # re-register
        live = _rows(sdk._get_proj(),
                     "MATCH (o:Object {id:$i}) RETURN o.status, o.name", i=oid)
        assert live == [["archived", "RM1"]]                         # precondition
        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))
        replay = _rows(sdk._get_proj(),
                       "MATCH (o:Object {id:$i}) RETURN o.status, o.name", i=oid)
        assert replay == live                                        # live == replay
        assert _fold_warnings(caplog) == []

    def test_two_non_point_labels_same_id(self, env, caplog):         # row 10
        """One id on TWO non-point labels — the shape that exercises
        `_CANONICAL_ENTITY_ID_PROPS` kind-scoping. NOT Point+Object (C1 emits
        nothing for `:Point`, #4094).

        THE ID MUST ACTUALLY BE SHARED. `create_entity("object", …)` mints
        `obj-<sha26>`, so pairing it with a literal would NOT share an id and the
        assertion would pass merely because the filter sees one label — the
        docstring/body-disagree defect cycle 4 caught. Build the Document with
        the OBJECT's id as its `doc_id`:

            from tortoise.api import EventAPI
            from tortoise.log import EventLog
            api = EventAPI(EventLog(str(events / "events.jsonl")),
                           initiated_by="extractor", projection=sdk._get_proj())
            api.add_document(doc_id=oid, title="D2", document_kind="k")   # emits DocumentCreated

        (`add_document(doc_id, title, *, document_kind=…)` — `projection` is an
        `EventAPI.__init__` kwarg, NOT a method kwarg.)

        WHAT IS ASSERTED: `_update_entity` is id-wide and applies the same
        `SET n += $props` to EVERY label matching the id, so both labels receive
        the SAME written props — each folded against its OWN
        `_ENTITY_ID_PROP[label]`. The row therefore asserts per-label records and
        per-label folded state; it does NOT assert "each node keeps its own prop"
        (the code cannot produce that).
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="R10", objectKind="k")["node"]["id"]
        from tortoise.api import EventAPI
        from tortoise.log import EventLog
        api = EventAPI(EventLog(str(events / "events.jsonl")),
                       initiated_by="extractor", projection=sdk._get_proj())
        api.add_document(doc_id=oid, title="D2", document_kind="k")

        sdk.update_entity(oid, status="shared_status")
        labels = sorted(r["label"] for r in _recs(events, oid))
        assert labels == ["Document", "Object"]          # BOTH labels journaled
        assert all(r["state"] == {"status": "shared_status"} for r in _recs(events, oid))

        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))
        proj = sdk._get_proj()
        assert _rows(proj, "MATCH (o:Object {id:$i}) RETURN o.status", i=oid) == [["shared_status"]]
        assert _rows(proj, "MATCH (d:Document {id:$i}) RETURN d.status", i=oid) == [["shared_status"]]
        assert _fold_warnings(caplog) == []


class TestInMemoryFold:
    def test_state_ops_are_explicit_noops(self):
        p = InMemoryProjection()
        for op in _STATE_OPS:
            p.apply({"type": _REC, "op": op, "id": "x", "label": "Object",
                     "state": {"status": "archived"}})
        assert p.points == {}

    def test_unknown_op_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            InMemoryProjection().apply({"type": _REC, "op": "wat", "id": "x",
                                        "label": "Object"})
        assert any("unknown EntityMutated op" in r.message for r in caplog.records)

    def test_pending_op_is_named_not_unknown(self, caplog):
        """`retract`/`supersede` are recorded (#3299) with no arm yet — the
        in-memory fold must say so, not call them unknown (parity with
        `_fold_entity_mutation`)."""
        for op in ("retract", "supersede"):
            with caplog.at_level(logging.WARNING):
                InMemoryProjection().apply({"type": _REC, "op": op, "id": "x",
                                            "label": "Object"})
            assert any("has no fold arm" in r.message for r in caplog.records)
            assert not any("unknown EntityMutated op" in r.message for r in caplog.records)


class TestOpVocabulary:
    def test_vocabulary_partition_is_total_and_hand_maintained(self):
        """The #2901 shape: BOTH halves hand-written, asserted equal to the
        vocabulary — so an op added to `_ENTITY_MUTATION_OPS` with no
        classification REDs here instead of being auto-absorbed as "pending"."""
        from tortoise.projection import (
            _ENTITY_MUTATION_IMPLEMENTED_OPS, _ENTITY_MUTATION_OPS,
            _ENTITY_MUTATION_PENDING_OPS, _ENTITY_MUTATION_STATE_OPS)
        assert set(_ENTITY_MUTATION_OPS) == (
            _ENTITY_MUTATION_IMPLEMENTED_OPS | _ENTITY_MUTATION_PENDING_OPS)
        assert _ENTITY_MUTATION_PENDING_OPS == frozenset({"retract", "supersede"})
        assert _ENTITY_MUTATION_IMPLEMENTED_OPS == frozenset(
            {"delete", "rename", "restatus", "revise"})
        assert _ENTITY_MUTATION_STATE_OPS == frozenset(
            {"rename", "restatus", "revise"})
        assert _ENTITY_MUTATION_STATE_OPS | {"delete"} == _ENTITY_MUTATION_IMPLEMENTED_OPS

    def test_classifier_returns_only_declared_ops(self):
        """AST scan (the precedent is tests/test_terminal_status_vocabulary.py):
        every string literal `classify_entity_mutation_op` can return must be in
        the state-op set — an ADDED branch cannot slip through."""
        import ast
        from pathlib import Path
        _ROOT = Path(__file__).resolve().parent.parent   # the precedent's anchor
        src = (_ROOT / "tortoise/projection/__init__.py").read_text()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "classify_entity_mutation_op")
        returned = {n.value.value for n in ast.walk(fn)
                    if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)}
        from tortoise.projection import _ENTITY_MUTATION_STATE_OPS
        assert returned == set(_ENTITY_MUTATION_STATE_OPS), (
            f"classifier returns {returned} but the declared state ops are "
            f"{set(_ENTITY_MUTATION_STATE_OPS)}")

    def test_exactly_one_entity_mutated_builder(self):
        """F2: the 'one builder' claim must be auditable, not prose. Exactly one
        construction expression for the record type may exist."""
        from pathlib import Path
        _ROOT = Path(__file__).resolve().parent.parent
        hits = []
        for f in (_ROOT / "tortoise").rglob("*.py"):
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if "_ENTITY_MUTATION_RECORD_TYPE" in line and "_emit_event" in line:
                    hits.append(f"{f}:{i}")
        assert len(hits) == 1, f"expected 1 builder, found {hits}"
```

**Step 3: Run** → `uv run pytest tests/test_unjournaled_mutation_class.py -v`. Expect **value/record** failures — **not** `KeyError`/`AttributeError`/`TypeError`. Any of those three means a surface or accessor is still invented; re-read `## Read before writing code`.

**Step 4: Commit** with the mandated `-F` form.

---

### Task 2: Green — one op vocabulary, one record builder

**Intent:** Fix v1's duplication: the vocabulary was declared for 3 of 4 values, the producer re-declared the names as literals, and the record builder was bypassed by 2 of its 3 producers.
**Acceptance:** one declaration; the producer cannot emit an undeclared op; all three producers route through one builder; a derivation test fails if a new op is added without a fold arm.

**Files:** Modify `tortoise/projection/__init__.py` (beside `_CANONICAL_ENTITY_ID_PROPS`); `tortoise/sdk.py`

**Step 1: The single declaration** (in `projection`, where the folds and `_CANONICAL_ENTITY_ID_PROPS` already live — `sdk.py:36-38` already imports from it at module scope, so there is no cycle)

```python
# #3299: THE EntityMutated op vocabulary — the ONE declaration. #2901's lesson
# (tortoise/live.py) applies verbatim: a hand-written subset at a call site is
# how a value gets silently omitted from a reader filter. The producer imports
# this; both folds classify against it; tests/test_unjournaled_mutation_class.py
# derives the relationship and REDs when an op is added without a fold arm.
_ENTITY_MUTATION_OPS: tuple[str, ...] = (
    "delete", "rename", "restatus", "revise",       # implemented here
    "retract", "supersede",                          # recorded on #3299 — no arm yet
)
_ENTITY_MUTATION_IMPLEMENTED_OPS: frozenset[str] = frozenset(
    {"delete", "rename", "restatus", "revise"})
# The ops that carry a `state` payload (everything implemented except delete).
_ENTITY_MUTATION_STATE_OPS: frozenset[str] = frozenset(
    {"rename", "restatus", "revise"})
# HAND-MAINTAINED, not derived from the difference: a derived set would
# auto-absorb an op added to _ENTITY_MUTATION_OPS with no classification,
# leaving the derivation test green on exactly the addition it exists to catch.
_ENTITY_MUTATION_PENDING_OPS: frozenset[str] = frozenset({"retract", "supersede"})


def classify_entity_mutation_op(props: dict) -> str:
    """The write's PRIMARY INTENT → its `op` value (C1).

    Precedence name > status > other. `state` always carries the write's full
    applied map, so a mixed write loses nothing by the labelling; a consumer
    wanting EVERY status transition reads `state["status"]` and must not filter
    on `op`. Lives here, not in sdk, so the producer cannot invent a name.
    """
    if "name" in props:
        return "rename"
    if "status" in props:
        return "restatus"
    return "revise"
```

**Step 2: The one record builder** (`sdk.py`, above `_update_entity`). Widened to the delete shape so **all three producers** can use it:

```python
    def _journal_entity_mutation(self, label: str, id_val: str, op: str, *,
                                 state: dict | None = None,
                                 name: str | None = None) -> None:
        """Build and emit ONE ``EntityMutated`` record — the only place one is built.

        ``state``: the mutation's own keys holding the values the GRAPH stored
        (never the caller's raw values — a coerced ``Decimal``/``numpy`` value
        would make ``EventLog.append``'s ``json.dumps`` raise, and ``_emit_event``
        swallows that to a warning, silently losing the mutation). ``None`` for a
        delete — the recorded shape carries *"nothing for a delete"* (#3299).

        The payload is NESTED because ``_emit_event`` reserves ``point``/
        ``payload``/``id`` and its envelope carries ``type``/``event_id``/``ts``/
        ``initiated_by``/``projection_version``: a flat spread would let a tenant
        prop named ``type`` corrupt the record.

        Identity is recorded AS WRITTEN (label + matched key + name) and never
        re-derived from the live node at replay time.
        """
        from tortoise.projection import _ENTITY_MUTATION_IMPLEMENTED_OPS
        if op not in _ENTITY_MUTATION_IMPLEMENTED_OPS:
            raise ValueError(f"{op!r} is not an implemented EntityMutated op")
        record: dict = {"id": id_val, "op": op, "label": label}
        if state is not None:
            record["state"] = state
        if name is not None:
            record["name"] = name
        self._emit_event(_ENTITY_MUTATION_RECORD_TYPE, **record)
```

**Step 3: convert the THIRD producer — `_delete_entity`** (`sdk.py:17451`). v1 of this plan left it emitting directly, so the builder's "only place one is built" claim would have been false at the moment it landed:

```python
                # (inside the per-label matched branch)
                self._journal_entity_mutation(label, id_val, "delete")
```

replacing `self._emit_event(_ENTITY_MUTATION_RECORD_TYPE, id=id_val, op="delete", label=label)`.

**Step 4: the derivation tests** (in the new test file) — see Task 1's `TestOpVocabulary`. Three assertions carry the unification:
- the partition is **total and hand-maintained** (so an unclassified addition REDs);
- the classifier's return set is **AST-scanned** against `_ENTITY_MUTATION_STATE_OPS` (so an *added* branch REDs, not just a replaced fallthrough);
- `_ENTITY_MUTATION_RECORD_TYPE` appears in **exactly one** `_emit_event` call across `tortoise/` (so a future second builder REDs).

**Step 5: Lint gate.** `uv run ruff check tortoise/ tests/` must stay clean — in particular do **not** import a name you do not use (`F401`) and do **not** leave an assigned-but-unused local (`F841`).

**Step 6: Commit** with the mandated `-F` form.

---

### Task 3: Green — the `_update_entity` producer (C1)

**Intent:** Journal what the write wrote, at the write surface, from the graph's own stored values.
**Acceptance:** Task 1's mutation tests pass; the record's `state` contains **exactly** the applied keys.

**Files:** Modify `tortoise/sdk.py` — `_update_entity`

**Step 1: Replace the hardcoded label tuple and the non-Point branch** (the Point branch is **unchanged** — #4094 shipped it):

```python
        from tortoise.projection import (
            _CANONICAL_ENTITY_ID_PROPS, classify_entity_mutation_op)

        for label, prop in _CANONICAL_ENTITY_ID_PROPS:
            if label == "Point":
                ...  # UNCHANGED #4094 PointRevised annotator-dims branch
            else:
                # C1 (#3312/#3377): this branch used to apply caller props with
                # a live `SET n += $p` and emit NOTHING — so rebuild_all replayed
                # the entity from its creation snapshot (status/name/objectKind
                # reverted).
                #
                # `state` = the WRITE'S OWN KEYS carrying the GRAPH'S STORED
                # values:
                #   * a key set to None is REMOVED live, so `properties(n)`
                #     omits it -> `state[k] is None` -> the fold removes it too
                #     (a full `properties(n)` snapshot would omit the key and
                #     RESURRECT it from the creation record);
                #   * a coerced value (Decimal/numpy -> native) is journalled as
                #     the stored primitive, so `log.py`'s default-less
                #     `json.dumps` cannot silently drop the record;
                #   * keys the write did NOT apply are absent, so replay can never
                #     overwrite the typed VectorF32 the upsert created
                #     (`properties(n)` returns a vector as a `list`; re-applying
                #     it demotes it and breaks the vector leg).
                keys = list(props)
                res = proj.g.query(
                    f"MATCH (n:{label} {{{prop}:$id}}) SET n += $props "
                    "RETURN [k IN $keys | properties(n)[k]] AS vals",
                    params={"id": id_val, "props": props, "keys": keys},
                )
                # No match => [] for THIS form. Do NOT add `count(n)`: beside
                # `properties(n)` it becomes a grouping key, so a miss yields no
                # row at all and a duplicate-id match yields one row PER GROUP.
                # "no live write" and "present under a different identity key"
                # (the #4649 Source/url case) collapse here — both correctly
                # produce no record, but only the former is asserted.
                if not keys or not res.result_set:
                    continue
                self._journal_entity_mutation(
                    label, id_val,
                    classify_entity_mutation_op(props),
                    state=dict(zip(keys, res.result_set[0][0])),
                    name=props.get("name"),
                )
```

**Step 2: Run** → Task 1's mutation tests pass; the replayed-value assertions now pass too (Task 4 is still needed for the fold — if Task 4 is not yet done, expect the *replay* half to still fail; that is the correct red/green boundary).

**Step 3: Commit.**

---

### Task 4: Green — the fold arms (all engines) + the warning policy

**Intent:** Teach `_fold_entity_mutation` the state ops; make a state-op fold-miss and an unknown op loud **inside the fold** so every engine records them; **do not** change the existing delete-miss policy.
**Acceptance:** all Task 1 tests pass; every engine sees a state-op fold-miss; the delete warning behaviour is byte-identical to today.

**⚠️ Policy (a scope-recorded decision — do not reverse it as v1 did).** The scope's §2 rule: the fold-miss warning fires **only for the three state ops**. `op="delete"` matching 0 rows is **legitimately idempotent** (a retried `delete_point`; `restore`'s JSONL fallback replaying onto a non-empty graph) and **must not warn** — otherwise the lane's headline evidence (`_fold_warnings == []`) becomes a false positive on legitimate replays. Consequences:
- the delete warning **stays where it is** (pass-1b's call site) and is **not** moved or duplicated;
- the state-op warning goes inside `_fold_entity_mutation` (because `apply()` discards the returned count, so a call-site-only warning would be vacuous on `rebuild(log)` / `recover_from_log` / `restore`);
- the unknown-op warning goes inside `_fold_entity_mutation` (and in `_apply_one`), because an unknown op is a *loss*, not an idempotent no-op;
- the **`event_id` field is preserved** in every message — it is the only handle that locates the diverging journal line, and the lane's whole evidence model rests on this warning stream.

**Step 1: Add the arm.**

```python
        op = ev.get("op")
        rid = ev.get("id")
        label = ev.get("label")

        if op in _ENTITY_MUTATION_STATE_OPS:
            # #3312/#3377: replay the mutation from its applied property map.
            if not isinstance(rid, str):
                return 0                                   # #331 parity
            if not (isinstance(label, str) and label in _CANONICAL_ENTITY_LABELS):
                # The label is NEVER interpolated from the journal — only an
                # allowlisted member reaches the Cypher label position.
                _warn_entity_mutation_fold_miss(op, rid, label, ev.get("event_id"))
                return 0
            state = ev.get("state")
            if not isinstance(state, dict):
                _warn_entity_mutation_fold_miss(op, rid, label, ev.get("event_id"))
                return 0
            prop = _ENTITY_ID_PROP[label]
            r = self.g.query(
                f"MATCH (n:{label} {{{prop}:$id}}) SET n += $s RETURN count(n)",
                params={"id": rid, "s": state},
            )
            matched = r.result_set[0][0] if r.result_set else 0
            if not matched:
                _warn_entity_mutation_fold_miss(op, rid, label, ev.get("event_id"))
            return matched or 0

        if op != "delete":
            # Outside the recorded vocabulary, or recorded-but-unimplemented
            # (`retract`/`supersede`). LOUD: a silently dropped mutation is
            # exactly this class's defect. The message distinguishes the two so
            # the next extender is not told its sanctioned op is "unknown".
            if op in _ENTITY_MUTATION_PENDING_OPS:
                logger.warning(
                    "rebuild: EntityMutated op %r is recorded (#3299) but has no "
                    "fold arm yet (event_id=%s id=%r label=%r) — its mutation is "
                    "NOT replayed", op, ev.get("event_id"), rid, label)
            else:
                logger.warning(
                    "rebuild: unknown EntityMutated op %r (event_id=%s id=%r "
                    "label=%r) — no fold applied; the record's mutation is LOST "
                    "on replay", op, ev.get("event_id"), rid, label)
            return 0
        # ... existing delete arm, unchanged ...
```

**Step 2: the helper** (module level, beside the vocabulary declaration):

```python
def _warn_entity_mutation_fold_miss(op: str, rid, label, event_id) -> None:
    """The state-op fold-miss signal — the non-folded set for C1.

    Emitted INSIDE the fold, not at a call site: ``apply()`` discards the
    returned count, and ``rebuild(log)`` / ``recover_from_log`` / ``restore``'s
    JSONL fallback all fold through it, so a call-site-only warning would leave
    the non-folded-set assertion vacuous on three of the four replay engines.

    Deliberately NOT used for ``op="delete"``: a delete matching 0 rows is
    legitimately idempotent (a retried delete; restore's fallback replaying onto
    a non-empty graph) and the existing pass-1b warning already covers the
    rebuild_all case. Widening it would turn the lane's own evidence into a
    false positive on valid journals.

    ``event_id`` is carried: it is the only handle that locates the diverging
    journal line.
    """
    logger.warning(
        "rebuild: EntityMutated %s fold matched no entity (event_id=%s id=%r "
        "label=%r) — the journal claims a mutation whose entity never "
        "re-existed on this replay (post-wipe divergence or out-of-order "
        "journal)", op, event_id, rid, label)
```

**Step 3: `_apply_one`** — an explicit branch (it is a second, separate implementation; it must not silently disagree on loudness):

```python
        elif t == "EntityMutated":
            op = ev.get("op")
            if op == "delete":
                rid = ev.get("id")
                if isinstance(rid, str) and _owns_point(ev.get("label")):
                    points.pop(rid, None)
            elif op in _ENTITY_MUTATION_STATE_OPS:
                # Explicit no-op: this projection indexes POINTS only, so the
                # five canonical labels the op mutates are outside its model.
                # Named here (not a silent fall-through) so the absence is a
                # decision a reader can see, matching the graph fold's contract.
                pass
            elif op in _ENTITY_MUTATION_PENDING_OPS:
                logger.warning(
                    "in-memory fold: EntityMutated op %r is recorded (#3299) but "
                    "has no fold arm yet — no fold applied", op)
            else:
                logger.warning(
                    "in-memory fold: unknown EntityMutated op %r — no fold applied",
                    op)
```

**Step 4: Lint.** Deleting nothing here, but if you also touch the pass-1b block, keep `matched = self._fold_entity_mutation(ev)` **used** (it feeds the existing delete warning) or make it a bare call — do not leave `F841`.

**Step 5: Run** → all Task 1 tests pass; `uv run pytest tests/ -q` shows **no new failures** and **no new delete-miss warnings** (if it does, the policy above was reversed).

**Step 6: Commit.**

---

### Task 5: Green — C2, the `delete_point` two-store split (and its two downstream readers)

**Intent:** Make a hard delete replay as a hard delete, without removing the subscriber signal — and account for the two readers whose classification of the record changes.
**Acceptance:** live and replay both show the Point **absent**; the `:GraphEvent` row survives; `retract_point` still tombstones; the two readers' behaviour changes in the intended direction and are pinned.

**Files:** Modify `tortoise/sdk.py` — `delete_point`; tests in the new file

**Step 1: the two-store split.**

```python
        proj.g.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": id})
        # #3300 residual: ONE event type must not mean two end-states.
        # `delete_point` hard-deletes; `retract_point` tombstones. The single
        # `PointRetracted` (id=) call emitted BOTH stores, and its fold has
        # exactly one meaning — tombstone — so a hard-deleted Point came back on
        # rebuild as `status='retracted'`.
        #
        # Two stores, one meaning each:
        #   * :GraphEvent ONLY (payload style, `id=` omitted) — the subscriber
        #     surface is UNCHANGED; `tortoise_events_poll` still sees the
        #     point-delete lifecycle row.
        #   * JSONL ONLY (`EntityMutated` is not in _GRAPH_EVENT_TYPES) — the
        #     replay journal carries the honest hard-delete record, whose fold is
        #     `_delete_entity_by_id`. No `state`: the recorded shape carries
        #     "nothing for a delete".
        self._emit_event("PointRetracted", {"id": id})
        self._journal_entity_mutation("Point", id, "delete")
```

**Step 2: tests, including the two downstream readers.**

```python
class TestDeletePointDurability:
    def test_delete_point_round_trips(self, env, caplog):
        sdk, events = env
        pt = sdk.create_point("fact", "delete me")
        sdk.delete_point(pt["id"])
        assert _rows(sdk._get_proj(), "MATCH (n:Point {id:$i}) RETURN n", i=pt["id"]) == []
        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))
        assert _rows(sdk._get_proj(), "MATCH (n:Point {id:$i}) RETURN n", i=pt["id"]) == []
        assert _fold_warnings(caplog) == []
        j = _journal(events)
        assert len([e for e in j if e.get("type") == _REC and e.get("op") == "delete"]) == 1
        assert not [e for e in j if e.get("type") == "PointRetracted"]   # JSONL: none

    def test_event_store_row_preserved(self, env):
        """The subscriber surface loses nothing (#432)."""
        sdk, events = env
        pt = sdk.create_point("fact", "signal me")
        sdk.delete_point(pt["id"])
        rows = sdk.events_poll(after=None)["events"]
        assert any(r["type"] == "PointRetracted"
                   and r.get("payload", {}).get("id") == pt["id"] for r in rows)

    def test_retract_point_still_tombstones(self, env, caplog):
        sdk, events = env
        pt = sdk.create_point("fact", "retract me")
        sdk.retract_point(pt["id"])
        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))
        assert _rows(sdk._get_proj(),
                     "MATCH (n:Point {id:$i}) RETURN n.status", i=pt["id"]) == [["retracted"]]
        assert _fold_warnings(caplog) == []

    def test_delete_point_suppresses_about_edges_on_replay(self, env):
        """Reader 1: `journal_hard_delete_seqs` deliberately EXCLUDED
        PointRetracted; C2 classifies the record as a hard delete, so a
        deleted Point's seq now enters the set and the deferred EntityLinked
        sweep (`projection/__init__.py:4311`) suppresses links touching it —
        matching the live DETACH DELETE, which destroys those edges."""
        # The link MUST be journaled for the suppression assertion to be
        # non-vacuous. `create_entity(aboutObject=…)` is a TRAP: it wires
        # through `FalkorProjection.create_about_edge`, a bare
        # `MERGE (s)-[:rel]->(t)` that emits NO `EntityLinked` record (scope
        # mechanism 3a) — and it sources from the Event it is creating, so it
        # cannot produce a Point-sourced edge at all. Use the journaled helper:
        #   from tortoise.session_link import link_entity
        #   assert link_entity(proj, "Point", pid, oid, sdk=sdk) == 1
        # (the path tests/test_capture_entity_attachment_3664.py already uses).
        ...

    def test_journal_hard_deleted_ids_classifies_the_new_record(self, env):
        """Reader 2: `_journal_hard_deleted_ids` feeds the #3947 pre-wipe
        `_assert_episodic_points_recreatable` guard. **Assert the READER, not a
        guard refusal** — v2 of this plan asserted the wrong direction three
        ways: (i) `_episodic_point_ids()` reads the LIVE graph and `rebuild_all`
        captures it BEFORE the pre-wipe snapshot, so a journaled episodic Point
        + `retract_point` does NOT raise; (ii) a `delete_point`ed Point is
        *absent from the live roster*, so the guard has nothing to refuse —
        "the guard would refuse the rebuild before C2" is false; (iii) the C2
        effect is only observable when a Point is **live** and the journal
        hard-deletes it.

        So: build a raw (unjournaled) live episodic `:Point`, hand-append (a) a
        raw `EntityMutated op=delete label="Point"` and (b) a raw
        `PointRetracted`, and assert `_journal_hard_deleted_ids` CONTAINS the id
        for (a) and OMITS it for (b).

        The guard's own arithmetic is `missing = before - covered -
        _journal_hard_deleted_ids(events)` (`projection/__init__.py:2718`), so a
        LIVE Point whose journal carries (a) is **EXEMPT** and `proj.rebuild(log)`
        **proceeds** — assert that (do NOT assert a raise: the clause would be
        self-contradictory and would push the implementer toward weakening a
        fail-closed #3947 guard). Then assert the mirror case: a live Point whose
        only journal record is a raw `PointRetracted` is NOT exempt (retraction
        is not a hard delete) and IS refused with `RebuildDroppedEpisodicPoints`
        (`projection/__init__.py:1643`).
        Reword the surface-map row's stated direction to "the id becomes exempt
        in `_journal_hard_deleted_ids`", not "the guard would refuse the rebuild".
        """
        ...
```

**Step 3: Run, then commit.**

---

### Task 6: Green — engine parity, the payload-fidelity gate, and the failure modes

**Intent:** Prove the fix on every replay engine; make the removal row hold on the engine the repo itself calls unreliable; and test the silent-failure class both halves.
**Acceptance:** the state ops round-trip on all engines; the removal row passes on **docker and embedded**, or the fold is changed; the append-failure and idempotency rows pass.

**Files:** the new test file

**Step 1: engine parity.** For `{status, name, objectKind}`, assert live == replay **and** `_fold_warnings == []` on:
- `rebuild_all` (inline pass-1b),
- `rebuild(log)` / `FalkorProjection.apply(ev)` per record,
- `recover_from_log(...)`.
- **`restore`: declared to be covered by `apply()`'s leg.** `backup.restore`'s JSONL fallback (`backup.py:169-173`) calls `proj.apply` per record — the *same* fold — so a third fixture would re-test one call site; the RDB short-circuit at `:143-150` never replays. State this in the test's docstring rather than leaving an unresolved either/or.

**Step 2: the payload-fidelity gate** (the two rows that falsify a snapshot payload):

```python
    def test_vector_leg_survives_a_mutation(self, env):
        """A `properties(n)` snapshot demotes VectorF32 -> List and the query
        returns [] with `query_failed` — a silent retrieval break after
        recovery. Requires the embeddings extra; a keyword-only install makes
        this fail closed via retrieval_preflight, not silently pass.

        NOTE: create the Document through a JOURNALED door (`EventAPI
        .add_document`, which emits `DocumentCreated`) — `create_entity
        (type='document')` emits none, so rebuild_all would drop the Document
        and the assertion would fail for row 4f's reason, not for the vector's.
        """
        ...
```

```python
    def test_property_removal_round_trips_on_both_engines(self, env):
        """`entities.py:370` records that Cypher null semantics in SET maps are
        UNRELIABLE — and C1's removal fidelity depends on exactly
        `SET n += {k: null}`. This row must hold on the embedded engine too.

        IF IT DOES NOT: do not weaken the test. Change the fold to remove
        explicitly — iterate the `None`-valued keys and issue
        `REMOVE n.<key>` — validating each key against a strict property-name
        pattern first (a caller-supplied key is NEVER interpolated unvalidated;
        that is the same rule the label already follows). Record the engine
        behaviour and the chosen path in the plan/tests.

        ENGINE: run this leg under the URI-less carve-out —
          TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_unjournaled_mutation_class.py -k removal
        While `TORTOISE_DB_URI` is exported the #1647 redirect flips `env`'s
        explicit-path TortoiseSDK to docker, so a "both engines" claim from one
        process is impossible; the two lanes are two invocations.
        """
        ...
```

**Step 3: the failure-mode rows.**

```python
    def test_replay_is_idempotent(self, env):
        """Applying each record twice must be a no-op and silent for the state
        ops. (The delete case is exercised separately and deliberately does NOT
        feature in the non-folded set — a second delete is idempotent.)"""
        ...

    def test_record_lands_in_jsonl(self, env):
        """The happy path the lane's durability rests on: exactly one record
        with the expected `state`, assertable BY CONTENT."""
        ...

    def test_append_failure_is_loud_and_observable(self, env, monkeypatch, caplog):
        """v1 claimed non-serializable payloads were 'not possible' — that covers
        only the TypeError half. An OSError (disk full, EACCES, read-only mount)
        leaves a live SET with no record and `update_entity` reporting success:
        live-ahead-of-journal, the exact divergence C1 exists to remove.
        Assert: the live node IS mutated, the WARNING names the lost record, and
        a later rebuild shows the divergence."""
        ...

    def test_delete_point_append_failure_resurrects_the_point(self, env, monkeypatch, caplog):
        """The C2 flavor of the partial-failure family. v2 pinned only C1's:
        there, a failed append leaves a live node with a STALE PROP. On C2's
        path a `DETACH DELETE` whose `EntityMutated op="delete"` append fails
        means replay resurrects the Point from its `PointAdded` line — a whole
        node, not a prop. The non-folded set is EMPTY on both flavors (a missing
        record produces no fold-miss by construction), so only the VALUE
        assertion can see it. Monkeypatch `EventLog.append` to raise OSError
        ONLY for the EntityMutated append in `delete_point`; assert the Point is
        absent live, a WARNING names the lost record, `_fold_warnings == []`,
        and that with the patch undone a rebuild resurrects it."""
        ...

    def test_cross_file_rename_is_declared_divergence(self, env, caplog):
        """#21, shared by every apply()-based engine: build the two-file journal
        explicitly (the rename in an EARLIER-sorted .jsonl than the creation),
        assert the replayed name is the OLD one AND that a fold-miss warning
        fired — a declared divergence, never a silent pass."""
        ...

    def test_prefix_journal_is_a_declared_divergence(self, env, caplog):
        """Row 12 — back-compat. Hand-append a bare JSONL `PointRetracted` (the
        pre-fix `delete_point` shape, the `_raw_append` helper in
        tests/test_2884_ep_state_journaled.py) over a hard-deleted Point:
        replay leaves a tombstone where live has no node. EXEMPT from
        live==replay — assert the divergence, and that it is INVISIBLE to
        _fold_warnings (the record does fold; it folds to the wrong end-state)."""
        ...

    def test_downgrade_old_fold_drops_new_op(self, env, caplog):
        """Row 13. A PRE-FIX fold is `if ev.get("op") != "delete": return 0`
        (`projection/__init__.py:5633`) — reproduce it locally over the same
        events and assert the silent drop + the replay divergence, then assert a
        new-binary rebuild re-converges. This is the observable meaning of the
        downgrade tolerance the lane relies on."""
        ...

    def test_unknown_op_warns_on_both_folds(self, env, caplog): ...
    def test_pending_op_is_named_not_unknown(self, env, caplog):
        """`retract`/`supersede` are recorded (#3299) with no arm yet — the
        warning must say so, not call them unknown."""
        ...
```

**Step 4: Run everything** → `uv run pytest tests/test_unjournaled_mutation_class.py -v`, then `uv run pytest tests/ -q`, then the embedded lane for the removal row. **Step 5: Commit.**

---

### Task 7: Green — turn the convention into a signal, and close the concurrency claim

**Intent:** The seam is a **convention** — an unwrapped future writer can still forget it (Reviewer #5's D7). Add the cheapest mechanism that makes the *next* omission red, and stop asserting a concurrency posture nobody checked.
**Acceptance:** the coverage test reds if a public write door stops journaling; the surface map no longer claims "concurrency: none".

**Files:** the new test file; (no production change expected — this is the detection half)

**Step 1: the coverage test.**

```python
class TestWriteDoorCoverage:
    """The lane's seam is a CONVENTION (see the bounded claim). This test is what
    makes the next omission visible: every public door that mutates a canonical
    entity must leave a journal record and a wipe+replay must reproduce live.
    A missing record is NOT covered by #3585 — that covers a NON-FOLDED record,
    not an absent one."""

    # The door set is the surface map's "Public doors" row, IN FULL — a
    # hand-maintained subset is the D6 shape this lane exists to break.
    # `sdk.delete` is a delegator (sdk.py:5007-5022 → delete_point/
    # delete_entity), so it is covered transitively and asserted as such rather
    # than omitted silently; the two MCP delete doors are included.
    @pytest.mark.parametrize("door", [
        "update_entity", "delete_entity", "delete_point", "delete",
        "mcp_update_entity", "mcp_delete_point", "mcp_delete_entity",
    ])
    def test_door_journals_and_replays(self, env, door):
        ...
```

**Step 2: cross-ref, don't test, the concurrency case.** The interleaving/reordering failure is disclosed in the surface map and referred to **#4631** (comment posted). Do **not** write a timing-dependent test — it would be flaky and would not prove the property. **Do not** substitute a "`append` makes one `write()` call" shape guard either: a record exceeding the buffer is *multiple* underlying writes, so that assertion greens exactly the records that can tear (manufactured confidence in the failure mode it claims to guard). State the residual plainly and stop.

**Step 3: Commit.**

---

### Task 8: Green — the documents C2/C1 make stale, and the verification pass

**Intent:** C1 changes the journal's record vocabulary and C2 changes what `delete_point` means; the repo's own catalog and three in-code comments still assert the old meaning.
**Acceptance:** `docs/event-catalog.md` documents `EntityMutated`; no in-tree claim survives that `delete_point`'s event provides rebuild parity.

**Files:** Modify `tortoise/sdk.py` (`delete()` docstring `~5011`; the `#548` comment `~5184-5187`), `tests/test_pointinvalidated_rebuild.py` (`~364`), **`docs/event-catalog.md`**

**Step 1: the three comments + two FALSE INVARIANT sites.** The two sites below are in files this diff already modifies, and they sit directly above the table the new code now relies on — leaving a known-false invariant there is the A6 failure the review flagged:
- `tortoise/projection/__init__.py:1376-1380` — *"This table is the ONLY source of the label→id-property mapping … Mirrored by the live writer `sdk._delete_entity`."* Falsified by `_RESOLVE_BRANCHES` (`:4818-4829`, a deliberate superset adding `("Source","url")`) and `navigation._ROOT_BRANCHES` (`navigation.py:20`). Reword to: *"the canonical MUTATION/DELETE map; the resolve paths declare their own OR-sets at `_RESOLVE_BRANCHES`."*
- `tortoise/sdk.py:17434-17436` — *"the ONE label→id-property table … so the producer and the fold cannot drift."* Same fix: the table is shared for the **delete/mutation** path; resolution uses its own tables. (The `Source`/url write no-op itself is **#4649** — the comment, not the bug, is fixed here.)

**Step 2: the three C2 comments.** Replace the old reading with the two-store split (the `PointRetracted` row is the `:GraphEvent` subscriber signal; parity now comes from `EntityMutated op="delete"`).

**Step 3: the test parenthetical + the conversion test.** `tests/test_pointinvalidated_rebuild.py:364`'s reason (*"NOT SDK delete_point — that emits PointRetracted, which tombstones the fresh incarnation pre-sweep and breaks the id-reuse premise"*) is now false. Do **not** merely re-word it to assert an untested claim: **add the conversion test** — a variant of that test that uses `sdk.delete_point` + a raw re-create and passes — then word the comment to the demonstrated fact. (Note the interaction Reviewer #1 flagged: pass-1b's `journal_deleted.add(rid)` runs *before* the `last_recreate_seq` anchor check, #4305 — the conversion test is what proves the anchor still suppresses the new delete record correctly.)

**Step 4: `docs/event-catalog.md`.** It is the declared home for folded record shapes and currently names `EntityMutated` (`op` vocabulary, `state` reader rule) nowhere, while confirming `PointRetracted`'s producer. Add: the record shape (identity keys; the `op` vocabulary; `state` = the applied keys carrying the graph's **stored** values, and **no `state` on `delete`**); the reader rule (*inspect `state` keys; a mixed write labels one primary `op`*); and reconcile the `PointRetracted` producer row for C2's split.

**Step 5: dispose of the two stale plan docs.** `docs/plans/2026-09-07-2488-invalidate-point-journal.md:87,105` and `docs/plans/2026-09-11-2977-object-retraction.md:59` still state the old `delete_point` reading. Add a one-line superseded pointer in each (do not rewrite history in a plan doc).

**Step 6: the acceptance sweep — widened.** v1's grep was scoped to `tortoise/ tests/`:

```bash
grep -rn "PointRetracted\|EntityMutated" docs/ tortoise/ tests/ tools/ skills/ \
  | grep -v "retract_point"
```

**Step 7: verification before completion.**

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_unjournaled_mutation_class.py -v
uv run pytest tests/ -q
uv run ruff check tortoise/ tests/
```

Then `verification-before-completion`, then `commit-workflow` (which owns the review gate, PR body, and merge). **This is a Complex-tier plan with 8 tasks → the skill's handoff rule selects the in-session subagent-driven mode (≤ 8 tasks).**

---

## Review log

### plan-review — cycle 1 (5 reviewers: #1 Structural, #2 Integration, #3 UX, #4 Failure Mode, #5 Duplication & Architecture)

**All P0/P1/P2 accepted.** The five reviewers returned 1 P0, 7 P1, ~14 P2.

| # | Finding (severity) | Resolution |
|---|---|---|
| 1 | **P0 — the tests target an API that does not exist** (`tortoise` fixture, `TortoiseSDK.rebuild_all`/`event_log()`/`list_entities`/`run_vector_query`/`tortoise_events_poll(since_seq=)`, `InMemoryProjection.list_points()`) → the red step proved nothing | Added `## Read before writing code` with every real surface (from the repo's own `tests/test_entity_delete_rebuild.py`), and rewrote all test code against it |
| 2 | **P1 — the fold-miss policy was reversed** (v1 made delete-misses warn; the scope says they must not) + the message dropped `event_id` | Task 4 now states the policy explicitly: warn inside the fold **only** for state ops + unknown/pending ops; the delete warning stays at pass-1b unchanged; `event_id` preserved in every message |
| 3 | **P1 — two unnamed downstream readers of the changed record** (`journal_hard_delete_seqs`; `_journal_hard_deleted_ids` → the #3947 fail-closed guard) | Both added as surface-map rows with the **intended direction** stated, plus two named assertions in Task 5 |
| 4 | **P1 — acceptance rows with no test** (rows 4, 6, 9, 10, 12) and row 12 mis-stated as `live == replay` | Rows 4/6/9/10 added to Task 1; row 12 carved out as a **declared divergence** with its own test (`test_prefix_journal_is_a_declared_divergence`) |
| 5 | **P1 — Task 5's vector test used the non-journaled Document door** (row 4f's known failure) | Task 6's vector test now creates via `EventAPI.add_document`, with the reason in the docstring |
| 6 | **P1 — `test_record_lands_in_jsonl` never defined; the append-failure class over-claimed** | The test is written out, plus `test_append_failure_is_loud_and_observable` (OSError) and corrected Failure Modes wording |
| 7 | **P1 — the op vocabulary was declared for 3 of 4 values; the producer re-declared literals** | Task 2: one `_ENTITY_MUTATION_OPS` declaration (incl. `delete`, plus the recorded-but-pending `retract`/`supersede`), `classify_entity_mutation_op()` in `projection`, and a **derivation test** |
| 8 | **P1 — the record builder was bypassed by 2 of its 3 producers** | Widened to `state: dict \| None = None`; all three producers route through it |
| 9 | **P1 — false invariant claims** (`_CANONICAL_ENTITY_ID_PROPS` "cannot drift") + a third copy (`_ROOT_BRANCHES`) | Removed the claim from the plan; the underlying `Source`/url silent no-op write **filed as #4649** |
| 10 | **P1 — no detection for the next unjournaled writer** | Task 7's `TestWriteDoorCoverage` |
| 11 | **P2 — the retrofit audit is unowned** | **Filed as #4650** |
| 12 | **P2 — lint gates (F841/F401), missing dependency map, Task 5's TDD ordering** | Lint notes in Tasks 2/4; `## Task dependency map`; row **4d** moved into Task 1's red set, row **4e** deliberately left in Task 6 (the vector test needs the embeddings extra and the journaled Document door) |
| 13 | **P2 — the `restore` acceptance was an unresolved either/or** | Decided: `restore`'s coverage is declared to be `apply()`'s (same call site, `backup.py:169-173`) |
| 14 | **P2 — the surface map's `:GraphEvent` gate and "silently dropped" were both wrong** | Corrected (the store is written by *type registration*; the append **raises, is caught, and is logged**) |
| 15 | **P2 — `docs/event-catalog.md` missing; the acceptance grep too narrow** | Task 8 now edits the catalog, disposes of two stale plan docs, and widens the sweep to the whole tree |
| 16 | **P2 — the concurrency claim ("none") was unexamined** | Corrected to the real posture (torn lines + append order ≠ `SET` order) and referred to **#4631** (comment posted); no flaky test written |
| 17 | **P2 — the no-op write / journal growth / input edges untested** | Added `test_unchanged_value_still_journals_and_replays`, `test_empty_props_emits_no_record`, `test_graph_rejected_value_writes_neither_node_nor_record`, `test_record_state_keys_match_props` |
| 18 | **P2 (Reviewer #3) — the reader rule and the new vocabulary were discoverable only outside the tree** | Task 8 Step 3 (the catalog entry carries the reader rule) |
| 19 | **P2 (Reviewer #5 D8, suppressed) — the removal row's `SET n += {k: null}` vs `entities.py:370`'s "unreliable" warning, docker-only** | Task 6 Step 2 makes it an explicit **gate** with the embedded lane required and a designed fallback (validated explicit `REMOVE`) |
| 20 | **P2 — Task 8's test-rationale rewrite asserted an untested claim** | Now: **add the conversion test**, then word the comment to the demonstrated fact |

### plan-review — cycle 2 (3 reviewers: #1+#2 Structural/Integration, #4 Failure Mode, #5 Duplication & Architecture)

**Proportionality note:** cycle 2 consolidated #1 and #2 into one dispatch — in cycle 1 they returned 4 of 5 findings in common — and did not re-dispatch #3 (UX), whose cycle-1 return was two informational P2s, both already folded in. #4 and #5 were re-dispatched unchanged. All 2 P0 + 5 P1 + accepted P2s from cycle 2 are folded into v3.

| # | Finding (severity) | Resolution |
|---|---|---|
| 1 | **P0 — `create_entity` has no top-level `id`** (`{"node":…, "nudges":…}`), so 9 Task-1 tests died on `KeyError` before asserting — the same defect class cycle-1's P0 was written to fix | `## Read before writing code` item 4 + every test body uses `["node"]["id"]` |
| 2 | **P0 — a missing property returns `[[None]]`, not `[]`**, so the removal row, the graph-rejected pin and the reserved-name loop could never pass | `## Read before writing code` item 5 + `RETURN o.<k> IS NULL AS m` assertions |
| 3 | **P0/P1 — the #3947 guard direction was wrong three ways** (`_episodic_point_ids` reads the LIVE graph; a `delete_point`ed Point is absent from the roster, so the guard never refuses) | Task 5's test now asserts the **reader** (`_journal_hard_deleted_ids`) plus the live-but-journal-deleted case; the surface-map row's direction reworded |
| 4 | **P1 — the reserved-name loop asserted `old`/`new` absent while the test itself writes them live** (a v1 artifact: v1's *record* carried them) | Loop reduced to keys the caller did not write; the record-shape claim moves onto `recs[0]` |
| 5 | **P1 — `_fold_warnings`'s substring matcher also collects the existing DELETE warning**, contradicting Task 4's policy | `_fold_warnings` is now explicitly op-aware |
| 6 | **P1 — the derivation test was tautological** (`_PENDING` derived from the difference it asserts; no arm inspection) | `_ENTITY_MUTATION_PENDING_OPS` hand-maintained + equality asserted; **AST scan** of the classifier's return set; a third assertion that the builder appears in exactly one `_emit_event` call |
| 7 | **P1 — `_delete_entity` was never converted to the builder**, so "the only place one is built" would be false on landing | Task 2 Step 3 converts it; the AST/grep guard pins it |
| 8 | **P1 — two known-false invariant comments survive in the diff's own files** (`projection:1376-1380`, `sdk:17434-17436`) | Task 8 Step 1 fixes both comments (the `Source`/url *bug* stays filed as #4649) |
| 9 | **P1 — Task 7's door list under-covered the surface map's own row** and its docstring overclaimed | The parametrize set is the full door row (both MCP delete doors added), `sdk.delete` covered transitively and asserted |
| 10 | **P2 — `test_repeated_noop_write_record_count` was dropped** in the v2 rewrite (the one resource-exhaustion row) | Restored; the bounded claim now names unbounded no-op journal growth as the accepted cost |
| 11 | **P2 — Task 7's optional "single `write()` call" shape guard was bogus** (a record over the buffer is multiple writes — it would green the tearing records) | Dropped; the residual is stated plainly and referred to #4631 |
| 12 | **P2 — the C2 append-failure flavor was untested** (there: a stale prop; here: a whole node resurrects) | `test_delete_point_append_failure_resurrects_the_point` added |
| 13 | **P2 — `_apply_one` did not distinguish pending ops** (its own fold would call a recorded op "unknown") | Pending branch mirrored; `test_pending_op_is_named_not_unknown` added |
| 14 | **P2 — `pytest.raises(Exception)` too broad** | Narrowed to `redis.exceptions.ResponseError, match="primitive types"` |
| 15 | **P2 — `embedded_env` was undefined and unreachable under an exported URI** | The route is now the URI-less carve-out; stated in `## Read before writing code` and Task 6 |
| 16 | **P2 — Task 5's about-edge setup used the NON-journaled path** (`create_about_edge` emits nothing) → the suppression assertion would pass vacuously | Explicit `session_link.link_entity(...)` |
| 17 | **P2 — row 10 was not constructible through public doors** | The construction is named (`EventAPI.add_document(doc_id=…)`), with instructions to declare a raw-append fallback rather than let the row pass vacuously |
| 18 | **P2 — the new file would be the 5th copy of `_journal`/`_rows`** | Recorded `keep separate` — the repo's per-file convention, justified rather than silently followed (extraction would put three unrelated test files in this diff) |
| 19 | **P2 — `{"name": None, "status": "s"}` classifies as `rename` with a null name**, and `name` is Object/Subject's MERGE key — untested | `test_cleared_identity_prop_does_not_resurrect` added |
| 20 | **P2 — the review log overstated row 12** (row 4e is in Task 6, not Task 1) | Log corrected |

**Verified sound by cycle 2 (recorded, not suppressed):** the `[k IN $keys \| properties(n)[k]]` return shape and its `[]`-on-a-miss vs the `[[0]]` count form; `SET n += {k: null}` removal fidelity on **both** engines; `dict(zip(keys, vals))` cannot truncate; the nested payload leaving the envelope intact; no import cycle (`sdk→projection`, never the reverse); `classify_entity_mutation_op`'s home; C2's two-store split reproducing exactly with `events_poll` unchanged; reader 1's direction and writability; the `restore` = `apply()` decision; `docs/event-catalog.md` as the right home and both stale plan docs at the cited lines; the `REMOVE n.<key>` fallback being key-validated (no injection); and that `_journal_entity_mutation`'s `ValueError` is **not** reachable after a graph commit (the classifier's codomain is the implemented set).

### plan-review — cycle 3 (1 verifier, scoped to the v2→v3 delta)

**Proportionality note:** cycle 2's findings were localized test-code precision and claim-scoping, so cycle 3 was a single scoped verifier rather than a fresh full set. All 3 P1 + 5 P2 accepted and folded in.

| # | Finding (severity) | Resolution |
|---|---|---|
| 1 | **P1 — the #3947 assertion's excess clause was self-contradictory** (`missing = before - covered - _journal_hard_deleted_ids(events)`, so a live Point with a hard-delete record is EXEMPT and `rebuild(log)` *proceeds* — the clause asked for a raise, and "fixing" it would have meant weakening a fail-closed guard) | Replaced: assert the **proceed** direction for the hard-delete record, and the **refusal** for the mirror case (a live Point whose only record is a raw `PointRetracted`); the real exception name `RebuildDroppedEpisodicPoints` (`:1643`) is now cited |
| 2 | **P1 — the row-10 construction named an invented signature** (`EventAPI.add_document(..., projection=…)` — `add_document` has no `projection` kwarg and no `**kwargs`; it is an `EventAPI.__init__` kwarg) | The construction is now given in full (`EventAPI(EventLog(...), initiated_by=…, projection=sdk._get_proj()).add_document(doc_id=…, …)`) with a live-write assertion, and Task 6's vector note points at the same door |
| 3 | **P1 — `test_re_mention_after_revise` asserted nothing** (`assert live is not None` on a `result_set` list is a tautology; the test passed on the defective tree) plus a false premise (the second `create_entity` is probe-gated, so there is no `ObjectRegistered` re-fire) | Rewritten concretely: precondition assert on live, `rebuild_all`, assert `replay == live`, `_fold_warnings == []`; the false premise is corrected in the docstring |
| 4 | **P2 — Task 2 carried a superseded Step-5 block defining a second `class TestOpVocabulary`** that would have shadowed (deleted) Task 1's derivation tests | Block removed; Task 2 is Steps 1–6 with no duplicate |
| 5 | **P2 — the two new AST/grep scans imported unused names (`inspect`, `re`) and used cwd-relative paths** (F401 would fail the plan's own lint step; the cited precedent anchors absolutely) | Imports dropped; both scans anchor on `Path(__file__).resolve().parent.parent` |
| 6 | **P2 — cross-reference + numbering defects in `## Read before writing code`** (item 2 pointed at Task 5 instead of Task 6 Step 2; the list ran 1,2,4,…) | Task ref corrected. The list is still 1,2,4,…,8 — cosmetic, and markdown renders it 1–7; the cycle-3 log's "contiguous" claim was inaccurate and is corrected here |
| 7 | **P2 — the `**Rev:**` header still said v2 while the body carried the cycle-2 log** | Header updated to v3 with the per-cycle provenance |
| 8 | **P2 — row 10 was still a `...` body inside the RED set** (so it passed by doing nothing) | Written concretely, with an explicit `xfail`-with-reason instruction as the only alternative to a real body |

**Confirmed resolved by cycle 3 (recorded):** cycle-2 P0 #1 (every `create_entity` result reads `["node"]["id"]`; `create_entity` is the only wrapping creator), P0 #2 (property absence uses `IS NULL`; the only `== []` left is genuine node absence), P1 #4 (`old`/`new` off the node loop, on the record), P1 #5 (`_fold_warnings` is op-aware and correct against all four real message shapes — and the six other pre-existing `fold matched no …` warnings say `Point`/`Object`, never `entity`, so none is mis-collected), P1 #6 (the AST scan finds a module-level `FunctionDef` with string `Constant` returns; the builder-site count is 1 today and 1 after the plan's edits; the hand-maintained pending set is a real trap), P1 #7, P1 #9, and the reader half of P0 #3.

### plan-review — cycle 4 (1 verifier, final) — **1 P1 + 2 P2, all folded in**

| # | Finding (severity) | Resolution |
|---|---|---|
| 1 | **P1 — `test_two_non_point_labels_same_id`'s docstring and body disagreed**: `create_entity("object", …)` mints `obj-<sha26>`, so no id was shared; the assertion `sorted(labels) == ["Document"]` was true *only because* the ids differed, and it therefore never exercised `_CANONICAL_ENTITY_ID_PROPS` kind-scoping (while still being a legitimate red, so not vacuous). Also asserted "each node keeps its own prop", which `_update_entity` cannot produce (it is id-wide: the same `SET n += $props` hits every label matching the id) | Rebuilt with option (b): the Document is created with the **Object's** id (`api.add_document(doc_id=oid, …)`), and the row now asserts `["Document", "Object"]` per-label records, the shared `state`, and per-label folded values — with the "own prop" claim replaced by what the code actually does |
| 2 | **P2 — Task 1's acceptance over-claimed** ("every test FAILS" when three are green-on-arrival guards) | Acceptance now separates the behavioural reds from the three derivation/guard rows, with a note not to try to redden them |
| 3 | **P2 — the cycle-3 log claimed the `## Read before writing code` list was contiguous** when it runs 1,2,4,… | Claim corrected in place (cosmetic; markdown renders it 1–7) |

**Confirmed by cycle 4 (all quote-verified against the tree):** the `#3947` rewrite matches `missing = sorted(before - covered - _journal_hard_deleted_ids(events))` (`:2717-2718`) and the real `RebuildDroppedEpisodicPoints` (`:1643`); the row-10 signatures (`EventAPI.__init__(log, *, initiated_by, agent_id=None, projection=None)`, `add_document(doc_id, title, *, document_kind="")` — and it does emit `DocumentCreated`); `test_re_mention_after_revise` REDs pre-fix for the right reason (the second `create_entity` is probe-gated → no second `ObjectRegistered`; `_upsert_object`'s ON CREATE/MATCH split keeps live `archived` while pre-fix replay yields `live`); `TestOpVocabulary` appears exactly once; Task 2 is Steps 1–6; the scans carry no unused imports and anchor absolutely, finding 1 builder site today and 1 after the edits; and **zero `...` bodies remain in Task 1's red set** (the 13 residual `...` blocks are all green-phase spec-blocks in Tasks 5–7).

**Exit:** the single P1 is fixed and its fix is a body that now tests what it claims. No issue remains that would cause wasted implementation work.

**Not fixed, deliberately (recording it rather than silently passing):**

- **No detection mechanism for a *missing* record beyond Task 7's coverage test.** A write that emits no record produces no signal by construction — that is what "the seam is a convention" means. Task 7 narrows the window to the doors that exist today; a structural guarantee is a different (larger) design, and the write-primitive chokepoint was rejected in the scope's §3b on grounds Reviewer #5 independently endorsed.

## Out of scope (referenced / filed — not absorbed)

| Item | Home |
|---|---|
| `:Point` general prop mutation hole | **#4094** (annotator-dims half already shipped) |
| Edge/tag writes with no carrier | **#2296** / **#2897** |
| Journal-before-apply ordering **and** interleaved appends | **#4631** (comment posted) |
| R9 live↔journal watermark | **#4630** |
| Document creation via the generic door | **#2296** (disclosed; asserted in Task 1/6) |
| Record-shape payload (delta vs snapshot) | **#3299** — `OVERRIDES:` posted; **#4642** superseded |
| R8 fail-closed half | **#3585** |
| Identity (minted ids, `NameAlreadyHeld`, `supersededBy`) | **#3590 / PR #3596** |
| FalkorDB silent int clamp | **#4647** |
| Collision pre-flight false negative | **#4635** |
| `update_entity` on a url-keyed Source is a silent no-op | **#4649** |
| Retrofit audit of already-divergent journals | **#4650** |

## The bounded claim — must appear in the PR body, in substance

This lane closes **two of four** mechanisms in the class: property mutations with no carrier, and one event type with two live end-states. It does **not** close the class — edges/tags still have no carrier (#2296/#2897) and several carriers are still unfolded (#1048). **The journal remains a PARTIAL WAL**: the payload is a delta, so it cannot reproduce an entity whose creation was never journaled (row 4f), nor any prop written by a raw-Cypher or no-`event_log_path` path (`docs/durability-posture.md:46-50`) — the live graph stays authoritative for everything unjournaled, and "replay == live" must not be read as a doctrine restored. The seam is a **convention**, not a structural guarantee (Task 7's coverage test narrows the window; it does not close it). And the lane **keeps** the post-apply ordering, so a crash between the `SET` and the append is unrecoverable, and concurrent appends can reorder or tear (#4631). The emit gate is *"keys non-empty AND matched"*, not *"changed"* — so repeated no-op writes append one record each, and journal growth per entity is linear in write CALLS, not in state changes (accepted, pinned by `test_repeated_noop_write_record_count`).
