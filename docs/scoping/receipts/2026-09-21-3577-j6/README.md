# PR #3577 (fix/2513-retrieval-evidence) — J6 land-or-close evidence

Raw outputs persisted because a `/tmp` artifact does not survive and is not evidence
(`#3865`). All commands were run in the worktree `.worktrees/j6-3577-land`.

Merged head during this work: `9663258e6` — merge of `origin/main` `d9c62f1a9` into the
branch (the pre-merge PR head is `33059ae7a`, which CI measured, 278 commits
behind `origin/main` — `git rev-list --count 33059ae7a..origin/main`).

## Lane identification (test (b))

`test (b)` of the Python CI workflow for this PR ran **URI-less embedded** —
`TORTOISE_DB_URI` empty, `TORTOISE_TEST_CARVE_OUT=1` (verified in the job env dump of
run 35208465736, job 105190123256). A docker-lane comparison against it would be
shape-blind, so every "same lane" reproduction below uses the embedded lane unless the
file is itself docker-only.

## Red list at `33059ae7a` (before, from CI)

| Job | Result |
|---|---|
| `python-ci-gate` | fail |
| `test (b)` | fail — **1** failed, 4876 passed, 43 skipped: `tests/test_dr_endpoints.py::TestDrDrillScheduled::test_manual_drill_records_measured_time` |
| `agent-infra-ci / lint` | fail — ruff `I001` at `tools/longmem_eval/retrieve.py:1710` |
| `ai-review-gate` | see PR body / record-review marker |

## Files here

| File | What it is |
|---|---|
| `testb-dr-endpoints-merged-head-9663258e6.embedded.txt` | the `test (b)` red (`test_dr_endpoints.py::…test_manual_drill_records_measured_time`) re-run on the merged head in the **embedded** lane — **PASSED** |
| `test-session-reinjection-merged-head.docker.txt` | the PR's own E2E file `tests/test_session_reinjection.py` run in the **docker** lane on the merged head — **20 passed in 47.35s** (its skip lane; it is skipped in embedded) |
