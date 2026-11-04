<!-- issue-scoping: v5.1 double diamond + verify (#9749) -->

## Scope — #9749

### Problem, re-measured on this tree (not taken from the body)

`fix/9749-promoted-conn-weight` (rebased onto `origin/main` @ `f8970b536`), embedded SDK:

| bundle | `_validate_bundle` | written edge |
|---|---|---|
| `points` p1→p2 plain `IMPL`, `confidence: 0.5, weight: 0.25` | `[]` — clean | operator created; **every** `(op)-[:IMPL]->(point)` edge reads `confidence=NULL, weight=NULL` |
| `entities` Object↔Object plain `IMPL`, `confidence: 0.6, weight: 0.3` | `[]` — clean | direct edge written with `confidence=NULL, weight=NULL` |

The second row is **not** in the issue body and is the same defect class: `tortoise/sdk.py`'s
direct branch called `create_direct_edge(op_type, src, dsts[0], label=…, direction=…, batch_id=…,
promote_source=…)` and never passed `confidence`/`weight` — the one primitive that *does* accept,
SET and journal both. So "validated as direct-edge attributes" was true of the **validator** only.

### Decision (no new one needed — §13 row 4 already records it)

`docs/INGEST_CONTRACT.md` §13 row 4 is the recorded decision: `confidence`/`weight` on an
operator-routed connection is a **Phase-1 violation**, and the operator-less direct edge is where
those attributes live. The code implemented neither half. The issue's own `OVERRIDES:` line says the
same. So:

- **operator route ⇒ refuse** (judged by the EFFECTIVE route, so the promoted Point-bearing spelling
  is covered). An operator has no edge attribute to carry the value; refusing is what §13 row 4
  claims, and it is what the #9173 review's comment instructed ("when it is fixed this arm must move
  WITH the multi-item arm above").
- **direct route ⇒ store** them (forward into `create_direct_edge`), which makes §13 row 4's migration
  advice ("they belong on the plain direct-edge path only") actually executable.

### Boundaries

**In scope:** `tortoise/sdk.py::_check_connection` attribute arms + the ingest write loop's direct
branch; `docs/INGEST_CONTRACT.md` §2 / §7 row 5 / §11 / §13 row 4; the E2E-5 canonical fixture; one
new test file (+ `config/ci-surfaces.yml` registration).

**Out of scope, deliberately:**

1. **No `TortoiseSDK` surface change.** `create_operator(op_type, source_id, target_ids, label,
   direction, promote_source, semantics)` gains **no** `confidence`/`weight`: that is the public SDK
   surface, which needs owner approval (AGENTS.md → MCP/SDK Surface Approval). No MCP tool changed.
2. **No carrying onto the operator's input edges.** It needs the same surface change *and* a journal
   descriptor for those attributes — the `OperatorAdded` payload carries `operator: {op_type,
   inputs}` and the fold re-creates the input edges with `{idx}` only, so a post-write `SET` would be
   a **live-only** value that `rebuild_all` silently drops (a new instance of the very class this
   issue closes, one layer down).
3. **No EP semantic change.** Per §11 the values stay recorded-for-audit; EP still computes belief
   from factor structure and never seeds messages from author-stated confidence.
4. **No route change.** The endpoint-kind gate (#8499) and the direct/operator split are untouched.

### Alternatives considered and rejected

| alternative | why not |
|---|---|
| Forward the values onto the operator's input edges (carry instead of refuse) | Needs the owner-gated SDK surface (`create_operator`) **and** a journal change for rebuild parity; without both it converts a live-time drop into a rebuild-time drop. That is a separate decision, not this fix. |
| Warn instead of refuse (write + drop + `warnings[]`) | Still writes a graph the caller's bundle did not describe, and the objective says a caller must never be told "clean" for a discarded attribute. |
| Leave the arm on the REQUEST route (keep the silent drop on the promoted spelling) | This is the bug. |
| Refuse `confidence`/`weight` on the direct route too (symmetry) | Would delete the only home §13 row 4 gives them, and would invalidate the contract's own §2 example shape. |

## Wiring

| surface | file:line (this branch) | change | pinned by |
|---|---|---|---|
| Phase-1/Phase-2 connection validation | `tortoise/sdk.py:19974` (`if write_route == "operator":`) | attribute arms judged by the effective route | `test_promoted_point_pair_refuses_the_attribute`, `test_explicit_operator_spelling_refuses_the_attribute`, `test_mitigation_route_refuses_the_attribute_too` |
| Refusal messages (two spellings) | `tortoise/sdk.py:20073` (`_reject_operator_route_attributes`) | promoted-by-the-gate caller is told why and pointed at `sdk.create_direct_edge(...)`; explicit-route caller is told to drop the key | message assertions above |
| Write loop, direct branch | `tortoise/sdk.py:22443` (`confidence=conn.get("confidence")`) | forwards both attributes into `create_direct_edge` | `test_direct_route_writes_confidence_and_weight`, `test_direct_route_with_subject_pair_also_stores_the_value` |
| Rebuild durability | `DirectEdgeCreated` fold (`projection/__init__.py` attr SET list) | unchanged — it already replays `direction/confidence/weight/label/batch_id`; now reachable from ingest | `test_direct_route_attributes_survive_rebuild` |
| Contract docs | `docs/INGEST_CONTRACT.md` §2 bullet + example, §7 row 5, §11, §13 row 4 | effective-route scoping, both messages shipped, migration line for Point-bearing pairs | review |
| E2E-5 canonical fixture | `tests/test_ingest_mode.py::_canonical_bundle` | float `confidence` moved onto a `:Subject`↔`:Subject` direct edge; a Point↔Point plain IMPL kept for the promoted route | `TestModeIsomorphism`, `TestBatchIdCanonicalization` (19 passed) |
| CI selection | `config/ci-surfaces.yml` | new test file registered in the ingest family | `python tools/ci_selection.py --integrity` |

### Verification

Docker lane (`tools/test_lane.py` private instance): `66 passed` (new file + validation + #8499
routing) and `266 passed` (ingest family: bundle, conformance, 7925 parity, event endpoint, rebuild
durability, safety, canonical, ingest_mode). Embedded carve-out lane: `19 passed` (ingest_mode) and
`9 passed` (new file). `ruff check` clean. Full detail in PR #10201.
