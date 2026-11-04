fix(9749): the connection attributes follow the effective route — refused where they cannot be stored, written where they can

Closes #9749

## The defect, re-measured on this tree (both halves)

The issue body names the promoted route. Measuring it also exposed the other
half of the same accept-and-drop class:

| bundle | `_validate_bundle` | written edge |
|---|---|---|
| `points` p1→p2 plain `IMPL` + `confidence: 0.5, weight: 0.25` | `[]` (clean) | operator created; **every** IMPL edge `confidence=NULL, weight=NULL` |
| `entities` Object↔Object plain `IMPL` + `confidence: 0.6, weight: 0.3` | `[]` (clean) | direct edge written with `confidence=NULL, weight=NULL` |

The second one matters because `docs/INGEST_CONTRACT.md` §13 row 4 names that
edge as the attributes' **home** ("confidence/weight belong on the plain
direct-edge path only"). Validation was the only place the value ever existed:
`ingest` never forwarded `confidence`/`weight` into `create_direct_edge`, which
has always accepted and SET them.

## What changed

- **`tortoise/sdk.py::_check_connection`** — the attribute arms are now judged
  by the **EFFECTIVE** route (`write_route`, the value the multi-item arm
  already used), so a promoted Point-bearing connection is refused instead of
  being validated as a direct edge and written by a route that cannot store it.
  This is the #9173 review's own recorded instruction *("when it is fixed this
  arm must move WITH the multi-item arm above")*, and it implements the answer
  §13 row 4 already claimed. `_reject_operator_route_attributes` gives the two
  spellings their own sentence:
  - explicit `reify:true`/`mitigation` → drop the key (it belongs on the direct
    edge);
  - a plain spelling **promoted** by the endpoint-kind gate (#8499) → names the
    promotion and points at `sdk.create_direct_edge(...)`, a remedy the
    promotion does not override.
- **the write loop** (same file) forwards `confidence`/`weight` into
  `create_direct_edge`, so the route §13 row 4 names actually stores them — and
  replays them, because the `DirectEdgeCreated` journal descriptor already
  carries both attributes (pinned post-`rebuild_all`).
- **`docs/INGEST_CONTRACT.md`** — §13 row 4 (effective-route scoping + the
  Point↔Point migration line), §7 row 5 (both messages shipped, not "planned"),
  §2 (the flagship bundle example no longer puts `confidence` on a Point↔Point
  connection, because that is now a Phase-1 violation; a rule bullet names the
  direct-edge home), §11 ("edge-carried" defined).
- **tests** — new `tests/test_9749_connection_attribute_route.py` (registered in
  `config/ci-surfaces.yml`): both promoted spellings refused in Phase 1 **and**
  Phase 2 with zero mutation, the plain promoted spelling still ingesting, the
  direct route storing + range-checking the value for `Object↔Object` and
  `Subject↔Subject`, and the value surviving `rebuild_all`. The E2E-5 canonical
  fixture moves its float `confidence` onto a `:Subject`↔`:Subject` direct edge
  and keeps a Point↔Point plain IMPL, so the isomorphism legs still exercise the
  promoted operator route.

Not touched: the MCP tool registry and the public `TortoiseSDK` surface.
`create_operator` is **not** given a `confidence`/`weight` parameter — that
would be an SDK surface change needing owner approval, and carrying the value
onto its input edges would be a live-only write (no journal descriptor, so
`rebuild_all` would drop it: a new silent drop one layer down).

## Test evidence (this tree, after the final edit)

Docker lane (`tools/test_lane.py`, private instance on `127.0.0.1:16404`):

```
.venv/bin/python -m pytest tests/test_9749_connection_attribute_route.py \
    tests/test_ingest_validation.py tests/test_8499_point_mediated_routing.py -q
66 passed in 295.56s

.venv/bin/python -m pytest tests/test_ingest_bundle.py tests/test_ingest_conformance.py \
    tests/test_7925_route_parity.py tests/test_ingest_event_endpoint.py \
    tests/test_ingest_rebuild_durability.py tests/test_ingest_safety.py \
    tests/test_canonical.py tests/test_ingest_mode.py -q
266 passed in 1244.21s
```

Embedded carve-out lane (`TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest`):

```
tests/test_ingest_mode.py -q                 19 passed in 184.64s
tests/test_9749_connection_attribute_route.py -q   9 passed in 174.71s
```

Lint: `ruff check tortoise/sdk.py tests/test_9749_connection_attribute_route.py tests/test_ingest_mode.py` → **All checks passed**.
CI surface: `python tools/ci_selection.py --integrity` → `✅ integrity: all test files classified`.
