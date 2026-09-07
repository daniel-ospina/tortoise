"""Lane-matrix contract (#2291 I-1): one access mode per lane.

The battery real-run path must measure the PRODUCT, not raw-Cypher mimicry.
Every store-access lane below has EXACTLY ONE allowed access mode; the
audit test (tests/test_battery_lane_matrix.py) enforces the boundary
source-level (function-scoped), so a regression that reintroduces raw
Cypher on the real A4 path fails loudly.

| Lane | Access mode | Who | Enforced by |
|---|---|---|---|
| A4 real write+read (episode runtime) | PRODUCT SDK verbs only (TortoiseSDK over the per-scenario graph: create_point/create_operator/mitigate_operator/supersede_point; recall_state/tortoise_fts_query/recall_subgraph/compute_confidence) | `battery.arms.a4_tortoise.py` retrieve/record (and later the executor's product calls) | lane audit: zero `FalkorProjection(`/`.query(`/`MERGE`/`MATCH`/`UNWIND` tokens in retrieve/record bodies |
| Seed content (hermetic) | REFERENCE raw-MERGE ONLY via `batch_setup` (allowlisted fn) until Task 2 swaps the real channel to `sdk.ingest` | `battery.runner.setup.batch_setup` + `battery.testing.seeds.setup_seed_mode` | lane audit allowlist = exactly these two functions; setup_scenarios may call `batch_setup(` and nothing raw |
| Equivalence/probe tests | READ-ONLY consumption of the reference lane | tests (SeededStore.find_content, warm-guard, no-leak) | read-only by convention + the audit allowlist |
| Docker lane (optional parity) | Same code, store-mode delta only (URI transport); never a second access mode | CI optional | store-mode delta allowlist + lane-invariant assertion list |
| Product internals (tortoise/) | N/A — product's own seams (capture refresh, EP compute) | product | out of harness scope (ban binds the harness, not the product) |

Invariants: the real A4 runtime path NEVER constructs `FalkorProjection`
and never issues a raw graph query; every verb goes through the per-scenario
`TortoiseSDK` handle (one handle per scenario graph, `graph_name` bound at
construction); env stripped in fixtures (`TORTOISE_DB_URI`/`TORTOISE_DB_PATH`),
per-run embedded store, never `tortoise_test_matrix`; no `require_calibration=False`
anywhere in battery/.
"""
