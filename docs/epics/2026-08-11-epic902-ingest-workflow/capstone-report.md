# Epic #902 — Capstone Verification Report (issue #1060)

Date: 2026-08-14 · Verifier: parent-session execution against origin/main @ 60463960

## Indicator — Full Track A E2E suite green in default CI

All registered ingest-suite files run GREEN in one clean pass
(`-m "not track_b"` — the Track A surface):

| File | Coverage | Result |
|---|---|---|
| `tests/test_ingest_bundle.py` | E2E-1..13 core (A0-A3, §8 routing) | ✅ passed |
| `tests/test_ingest_mode.py` | E2E-5 (A6 — mode isomorphism) | ✅ passed |
| `tests/test_ingest_validation.py` | A1 Phase-1 check helpers | ✅ passed |
| `tests/test_ingest_rebuild_durability.py` | A10 rebuild legs | ✅ passed |
| `tests/test_ingest_safety.py` | A8 Track A (E2E-1,6,8,13,17) + Track B | ✅ passed |
| `tests/test_ingest_conformance.py` | A12 doc/behavior conformance | ✅ passed |
| `tests/test_ingest_audit.py` | A13 list_batch audit E2E | ✅ passed |
| `tests/test_ingest_promotion.py` | A11 interim promote route | ✅ passed |
| `tests/test_a9_direct_edge_traversal.py` | A9 selector + E2E-14 | ✅ passed |
| `tests/test_direct_edge.py` | §8 direct-edge writer | ✅ passed |
| **Total** | | **174 passed, 1 skipped** (J8 skill markers — pending #1057) |

## Track B red-first observable

`tests/test_ingest_safety.py` Track B tests (E2E-3 zombie resolution, E2E-7.1
draft-invisibility proof obligation, E2E-7.2 direct-edge convergence control,
promote_point contract) carry `@pytest.mark.track_b` + skipifs and run in the
dedicated `test-track-b` job (must-pass — both sentinels shipped). `-m track_b`
→ 4 passed.

## GATE-2 decisions verified in-suite

- Q3 derived-liveness: E2E-13 (gated+operator commits, EP-inert <2 live,
  1-live/1-draft boundary) — `test_ingest_safety.py`.
- Q6 direct-edge traversal: E2E-14 (IMPL 3-cycle + NAND-inclusive numeric
  termination) — `test_a9_direct_edge_traversal.py`.
- A0 Q2-lock row 9: gated status:'live' rejection — `test_ingest_safety.py`.
- Q5 warnings contract: A12 closed-set enforcement (11 keys) — `test_ingest_conformance.py`.

## Residuals

1. **J8 skill section** (#1057, agent-infra) — the how-to-use-tortoise ingest
   markers (incl. the ELEVEN-key warnings table) are asserted by the A12
   conformance test with a skip-until-landed predicate; the suite is green
   with the skip, and the marker-grep enforces the moment #1057 lands.
2. **hosted_api #909 bridge** contentHash passthrough — scoped to #909.

**Epic acceptance: MET** (all Track A E2Es green in default CI; Track B
red-first observable; GATE-2 decisions in-suite).
