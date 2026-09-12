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

## Running a real leg — install + retrieval preflight (#2985)

Canonical dev env, ONE command with every extra the real lanes need:

    uv sync --extra embeddings --extra parity

⚠️ `uv sync` with an explicit `--extra` is EXACT: it SILENTLY REMOVES
every extra you do not name (`uv sync --extra embeddings` drops
`parity`/pyarrow, which is how a measurement run broke mid-investigation).
Name every extra in ONE command, or use `--all-extras`. A plain `uv sync`
yields a **KEYWORD-ONLY product** (no `sentence-transformers` →
`EmbeddingModel.get()` returns None → the dense retrieval leg is never
submitted → retrieval silently degrades to FTS-only).

Real lanes now fail closed on that degraded environment, from two angles:

| Real path | Gate on a degraded env |
|---|---|
| MABench Tortoise parity lane (`memoryagentbench_tortoise_executor`) | `TortoiseCrMemory.retrieval_legs()` probes `recall_state` with a `leg_trace`; when the VECTOR leg did not run the lane REFUSES (`ExecutorUnavailable` carrying a machine-readable `capability_gate`) → explicit not-measured cell, never an FTS-only number wearing `real_tortoise` |
| Battery real arm run (`battery/runner/run.py`; arm `a4` declares `requires_hybrid_retrieval`) | `require_hybrid_retrieval()` runs at arm-init BEFORE setup/ingest; the arm is skipped, `summary.run.retrieval_degraded=true`, and the `init_failure` reason names the preflight |

The shared fail-closed primitive is
`battery/runner/retrieval_preflight.py::require_hybrid_retrieval()` (used at
arm-init, where no trace exists yet); the parity lane prefers the product's
OBSERVED `leg_trace` (`retrieval_capability_gate`) because availability must
never be used as a guess when an observed trace exists. Mock/hermetic lanes
are exempt by construction (their fake memory has no retrieval engine).

Persisted provenance: the parity record carries the `capability_gate`
(`vector_leg` / `reason` / `legs_seen` / raw `leg_trace`) AND the observed
`retrieval_legs` / `retrieval_degraded`
(`battery/cli.py` → `parity_record.json`); `summary.json` carries the
run-level union of legs the retrieval-reading arms observed. A number
persisted without the retrieval conditions behind it is not a product
measurement.

Example legs (real reader/judge — needs `OPENROUTER_API_KEY`):

    battery run --executor real --arms a0,a4 --tier 1
    battery parity --execute --mock     # hermetic parity smoke (no keys/spend)
"""
