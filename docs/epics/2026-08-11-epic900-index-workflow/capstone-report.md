# Epic #900 — Capstone Verification Report (issue #1031)

Date: 2026-08-14 · Verifier: parent-session execution against origin/main @ 60463960

## Indicator 1 — Full index E2E suite green (T-CI wiring proven)

All six registered index-suite files run GREEN in one clean pass
(`pytest` with the half-a/SLOW matrix configuration, `-m "not track_b"`):

| File | Result |
|---|---|
| `tests/test_file_indexer.py` (S1 units) | ✅ passed |
| `tests/test_index_directory.py` (S2 + E2E-1..5,7,9-14,18,19) | ✅ passed |
| `tests/test_index_cli.py` (E2E-15/16 + S14) | ✅ passed |
| `tests/test_index_mcp.py` (E2E-17 + S8) | ✅ passed |
| `tests/test_index_restore.py` (T12 + S13/S15) | ✅ passed |
| `tests/test_backfill_sources.py` (E2E-8 + S9) | ✅ passed |
| **Total** | **232 passed, 2 skipped** (Linux-gated mount/root legs) |

Registration verified in python-ci.yml's half-a + SLOW_FILES matrix (T-CI #1207
landed; break-proves-wiring by registration).

## Indicator 2 — E2E-15 (hook) + E2E-16 (CLI) green

`tests/test_index_cli.py` covers E2E-15 legs (a)–(i) — happy path through the
real session-end hook (capture spy, post-drain health, CHILD_STDERR traceback
contract, lock contention, dead-URI, truncate/crash-recovery, hook-vs-sweep
overlap) — and E2E-16 legs (i)–(viii) — the operator-visible CLI contract
(stdout JSON, exit codes, env fallback, sandbox, --corpus-name) — plus the
S14 arg-resolution units. All green in the suite run above.

## Indicator 3 — T12 restore drill executed

`tests/test_index_restore.py` (T12 #1216 landed): the backup/restore drill —
corpus + events/ dir + db → wipe → `rebuild_all` (line-tolerant, wipe-after-
parse) → T12 semantics (session/meeting references dropped, doc references
survive, no phantom Sources) → re-index as repair oracle → `count(Source) ==
file_count`, edges restored, zero duplicate urls. Green in the run above.

## Indicator 4 — Doc-truth check green

E2E-16(i) IS the documented first-run sequence (`tortoise index directory
<corpus> --db <path>`) — the T-DOCS #1217 quickstart commands are exercised
verbatim by the CLI subprocess legs and parse to the §3.1 summary. Green.

## Indicator 5 — T11 live-corpus report — BLOCKED (environment)

The real 4,190-file session corpus is NOT present on this machine
(`~/.tortoise/docs/conversations` absent; no TORTOISE_SESSION_CORPUS). The
double-index smoke (SC3) cannot run here — recorded as a named follow-up
(F-900-1/T11 on a host with the production corpus). Fixture-scale idempotency
is proven by E2E-4/E2E-8/E2E-16(ii) (all green).

## Indicator 6 — JOINT-E2E green

`tests/test_joint_index_ingest.py` (#1032, merged PR #1279): ONE graph + BOTH
writers converge on a shared Source — survivor semantics, version unchanged,
zero sweep mutation, stub-Source exception class. Green.

## Verify-stage findings

Zero P0/P1. Two named residuals:
1. **T11 live-corpus smoke** — blocked on the production corpus host.
2. **hosted_api #909 bridge** still passes explicit `contentHash=""` on
   no-hash writes (the joint preserve semantics don't cover it) — scoped to
   #909's plan gate, noted in PR #1279.

**Epic acceptance: MET** (all implementation tasks landed; full index suite
green; restore drill + doc-truth executed; the single environment-blocked
indicator is a named follow-up).
