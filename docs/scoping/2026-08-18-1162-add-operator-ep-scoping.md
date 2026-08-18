# #1162 — EventAPI.add_operator runs global extract_svbp_factors (env-gated)

## Problem

`EventAPI.add_operator` (tortoise/api.py, Gate 4 block) calls the GLOBAL
`projection.extract_svbp_factors()` on **every** operator write when the
deprecated SVBP path is live (jax installed via the `quadrature` extra —
pyproject.toml:36-38). The scan is O(graph): 2 graph-wide queries (#400 batch
pattern) + a 5-iteration SVBP warm-start run over all factors, alongside the
shipping EP path. Only manifests with the extra installed, but the dev env has
jax 0.11.0, so every EventAPI-driven ingest triggers it.

## Scope (chosen)

The #395 scoped extractor `extract_factors_for_operators` was **removed** as
dead code (PR #1273 note, projection/__init__.py:1571-1586) — its parity tests
pinned it against `extract_svbp_factors`, not the local path's reference. It
does not exist to route through.

Chosen fix: **scope the SVBP warm-start factor set to the new operator**
instead of the global scan. The new operator's factor is fully known in
`add_operator`'s scope — `(op_id, op_type, [inputs], weight)` with the same
weight rule as `extract_svbp_factors` (3.0 NAND / 1.0 IMPL) and the same
≥2-inputs degenerate exclusion. Per-write cost drops O(graph) → O(1), no graph
queries. This preserves the deprecated-but-live incremental warm-start
semantics (Bug 4 max_iter restore / Bug 5 lazy-init) rather than dropping it.

Alternative rejected: skip SVBP warm-start entirely (issue's second option).
Keeps EventAPI.get_confidence (SVBP) stale after operator adds when jax is
installed — a real behavior change on a live surface for no cost win (the
local warm-start is O(1)).

Out of scope: the one-time `get_svbp()` init scan (lazy-init, deprecated SVBP
initialization — not the per-write hot path), the EP local machinery
(TortoiseEP._affected_factors, #395 — unchanged), SDK.create_operator (does
not route through EventAPI.add_operator).

## Acceptance

- [x] add_operator no longer calls the global extract_svbp_factors per write
- [x] jax-absent degrade-to-None path unchanged (get_svbp → None → no scan)
- [x] EP parity (local EP vs full-graph within tolerance) — the #395 AC4
      contract, re-run green (test_ep_local_395.py)

## Implementation (done 2026-08-18)

1. **tortoise/api.py** — Gate 4 warm-start factor set is now built locally
   from the new operator's own `(op_id, op_type, [inputs], weight)` (3.0 NAND
   / 1.0 IMPL, ≥2-input degenerate exclusion — same rules as
   `extract_svbp_factors` / `_affected_factors`). Zero graph queries per
   write. Lazy-init (Bug 5) and max_iter restore (Bug 4) preserved.
2. **tortoise/sdk.py (regression fix found during validation)** — the
   epic-903 refactor (7ad23e40, #1240) regressed `compute_confidence`'s
   no-arg path to a whole-graph `extract_svbp_factors` extract + full run,
   making the #395 delta-C local branch dead code. `test_ac2_noarg_runs_local_ep_no_global_extract`
   (AC2) has been red on main since. Restored the #395 local contract: dirty
   roots → from-scratch local dream (warm_start=False, stamp_dreamed_at=False,
   calibration propagated) → `ep.run(roots, max_hops=None)`. AC2 green again;
   anchors branch keeps its no_factors guard + direct run.
3. **tests/test_1162_add_operator_local_svbp.py** (new, api surface,
   registered in config/ci-surfaces.yml) — 4 tests: no-global-scan with SVBP
   live, NAND weight 3.0, degenerate <2-input skip, jax-absent degrade.

## Pre-existing failures observed (not caused by this change)

- `test_calibration.py::test_require_calibration_default` — fails when
  `tests/test_decide.py` is imported in the same single-process session
  (module-level `os.environ.setdefault("TORTOISE_EP_REQUIRE_CALIBRATION", "0")`
  leaks into the session; xdist per-file isolation hides it in CI).
- `test_epic903_freshness.py::TestLastDreamedAtIndex::test_composite_index_created_non_embedded`
  — live-Docker index assertion fails on this machine (environment).
  Both verified red on HEAD.

## Complexity

micro/standard — single-block change in api.py + one new test file
(api surface) + ci-surfaces.yml registration.

## Test plan

1. `test_add_operator_does_not_scan_whole_graph_when_svbp_present` — pre-set
   `api._svbp` (recording fake), monkeypatch `extract_svbp_factors` on the
   projection → assert zero global-extract calls; the SVBP run receives
   exactly the new operator's local factor; max_iter restored (Bug 4).
2. Degenerate <2-input operator → no warm-start run (matches the global
   extractor's exclusion rule), still no scan.
3. get_svbp → None (jax absent) → add_operator works, no scan
   (degrade-to-None preserved).
