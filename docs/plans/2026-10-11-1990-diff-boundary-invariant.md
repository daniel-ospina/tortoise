---
title: "Plan — #1990 the DIFF boundary invariant: items older than the cursor's second are provably indexed, so skip them and stop the walk there"
type: engineering
domain: capability
doc_status: draft
created: 2026-10-11
subjects.team: epistemic-team
aboutSubjects: epistemic-team
ownedBy: epistemic-team
governingAgreement: "#1990 (fast-follow of #1895; solution-converge validated against source + an empirical cursor harness)"
aboutObjects: github-index-cursor, tortoise-github-indexer
---

# Plan — #1990: DIFF-mode probe oscillation on dense-boundary repos

## Context

- **Issue:** #1990 — the mandatory fast-follow PR #1989 was required to file for #1895. Post-#1895 the
  production *freeze* (a boundary that never advances) became a bounded, lossless DIFF/DRAIN
  oscillation: `truncated` re-stamped on 2 of every 4 polls, and every poll re-walking the full stream
  on a repo whose boundary second holds more items than `_MAX_ITEMS_PER_RUN = 500`.
- **Verified pre-fix cycle (issue body, production shape 1425 @ `S` + 460 @ `S+1`):**
  `{S+1,1885} → {S,500,t} → {S,1000,t} → {S,1425} → {S+1,1885} → …`
- **Scope guard:** `tortoise/indexer/github_indexer.py` (`_inside_cursor`, `_fetch_items`,
  `index_repo`'s mint) + `tests/test_github_indexer.py`. **Out:** the cursor's *representation*
  (A2 range cursor, rejected in #1895), `hosted_api`, the cap contract, schema/migration work.
- **Dependency:** #1895 / PR #1989 must be merged (it is: `4bd8b3371`, `01d70e829`, `5ec446258`,
  `b2a38db3d`). #1990 is standalone otherwise.

## Confirmed problem (measured on `origin/main`, not inferred)

A projection-stubbed cursor harness (`GitHubIndexer.index_repo` with `_project_issue`/`_project_pr`
replaced by no-ops — the fetch/flush/mint logic only) reproduces both churn mechanisms at full speed.
Pre-fix rows (page_size 10, cap 40, `S` = 100 items, `S1` = 30 newer items — the production shape
scaled down; the property that matters is *pre-boundary second > cap*):

| run | processed | minted cursor | pages | note |
|---|---|---|---|---|
| drain (4 runs) | 40/40/40/10 | `{S,10,t}` → `{S,50,t}` → `{S,90,t}` → `{S,100}` | 13 | pre-fix drain is correct |
| DIFF run | 30 | `{S1,130}` | 13 | advanced to the newest second |
| **steady-state poll** | **40** | **`{S,40,truncated}`** | **13** | boundary REGRESSED to `S`, `truncated` re-stamped, full re-walk — while the graph already held all 130 objects |

The same harness on the canonical P1-4 drain fixture (600 items over 12 seconds, cap 500) shows the
pre-fix code **ping-ponging forever**: `{T10,550}` → `{T9,500,t}` → `{T10,550}` → `{T9,500,t}` → …,
each poll re-walking the full stream and spending 500 cap units on already-indexed items. The
existing `test_drain_mode_drains_backlog_across_runs` passes through this state without asserting it
(its run 3 checks only `points_created == 0` and the object census).

**Root cause.** `_inside_cursor(drain=False)` skips only the cursor's OWN second
(`== U and number <= N`). The DIFF window is `[U−1s, ∞)`, so the pre-boundary second is re-probed
every poll; when that block exceeds the cap the probe consumes the whole cap and mints a cursor
REGRESSED to the older second. Two further defects ride on the same representational weakness:

1. **Truncation was decided per-block, not per-readable-region.** A cap that lands exactly on a
   block's last processable item was a "truncation" even when nothing readable followed it
   (the same defect #1989 review round 2 special-cased at a natural walk end), which is what turns
   the conservative DRAIN-minted cursor into the permanent ping-pong above.
2. **The walk had no lower bound.** Nothing stopped it from walking to the stream end even though
   nothing below the boundary second is readable.

## The DIFF boundary invariant (I)

> **I(cursor):** for every cursor this indexer can mint **without** `truncated`, every stream item with
> `updated_at < cursor.updated_at` is already indexed.

**Proof** (induction over the mint paths; "processed" = projected *or* skipped as already-indexed):

- **B (fresh run).** A run with no cursor fetches with no `since` (full walk). A clean mint means the
  walk was uncapped and reached the stream end, so every fetched item was processed; items older than
  the minted (newest-processed) second were therefore all processed. I holds.
- **S1 (clean DIFF mint from a cursor C, I(C)).** The window `[U₀ − 1s, ∞)` covers every item with
  `updated_at >= U₀ − 1s`; a clean completion processed or skipped each of them. Items **below**
  `U₀ − 1s` were not fetched — but they are `< U₀`, so I(C) already indexed them. An item's
  `updated_at` only ever moves *forward*, so "old now" implies "old (or gone) then". I holds for
  `U₁ >= U₀`.
- **S2 (clean mint from a DRAIN run, any input).** DRAIN strips `since` (full walk), so a clean
  completion processed or skipped **every** item in the stream — including every item below the new
  boundary. I holds regardless of the input cursor's claim: a DRAIN completion re-establishes the
  claim globally.
- **S3 (0-processed heal, `01d70e829`).** The heal requires `total_fetched == 0`, i.e. the full walk
  skipped every fetched item, which is only possible if no item below the boundary second exists —
  items below the boundary are never skipped in DRAIN mode, so one would have been fetched. I holds
  (there is nothing older than `U` to index).
- **Legacy cursors.** Cursors minted before #1895 are minted at `last` (the *oldest* processed item),
  so nothing is older than their boundary; the pre-#1989 ones are covered by the same argument. The
  `gap_audit_required` heal is covered by S3.

### Corollary (used twice)

1. Any item a DIFF walk fetches with `updated_at < U` is provably indexed → skipping it is safe, and
   idempotent re-projection would only burn cap and regress the cursor.
2. The stream is globally non-increasing in `updated_at` (the assumption the #1895 per-second buffer
   already relies on), so once a DIFF walk sees a second strictly below `U`, **nothing readable
   remains on any later page** → the walk may terminate there. A mis-ordered stream can only *delay*
   newer items (the mint never advances past an unprocessed item), never lose them.

## Approach selection

### Chosen — B1: invariant-aware DIFF boundary + readable-region truncation

1. `_inside_cursor`: in DIFF mode, skip items strictly older than the boundary second (corollary 1).
2. `_fetch_items`: stop the DIFF walk as soon as the stream descends below the boundary second
   (corollary 2), reporting `boundary_stop` so the mint can tell the operator the truth about
   "N issues beyond window".
3. `_fetch_items`: make truncation a property of the **readable region** — after the cap is full the
   walk keeps *scanning* (skipping provably-indexed items, appending nothing) until it either finds a
   processable item (`cap_hit` → truncated) or the readable region ends (boundary stop / natural walk
   end → clean). A premature walk end (rel="last" present, no rel="next") still stamps `truncated`
   via `walk_complete` (#1989 review round 3), preserving the round-3 silent-loss guard.

### Rejected — the naive monotonic fold-in into DRAIN

Empirically rejected in #1895's plan-verify
(§Follow-ups): on the `test_drain_mode` fixture the rule skips the deferred backlog (#501..#600 never
reached) — permanent silent loss. It stays rejected here; the residual that still needs it in a
*claim-gated* form is #8090.

### Rejected — shrink the DIFF window from `U − 1s` to `U`

The `−1s` is the correctness margin
against GitHub `since` exclusivity/coarseness (`_fetch_page`'s own note): with `since=U` an item
updated at exactly `U` whose number is above the cursor's could be dropped, and nothing would ever
re-fetch it. The invariant removes the *cost* of the margin (the pre-boundary second is now skipped
before it can consume the cap) without removing the margin itself.

### Rejected — fetch budget only (issue direction 2)

A page/byte cap bounds the fetch cost but leaves
the cap consumption, the cursor regression and the `truncated` re-stamp untouched. B1 makes the walk
*shorter* (bounded by the boundary block) rather than merely cheaper to be wrong.

### Rejected — A2 range/representational cursor

Already rejected in #1895 (schema churn across
`hosted_api` and every exact-dict assertion) for zero gain over the invariant.

## Implementation

`tortoise/indexer/github_indexer.py` only:

| site | change |
|---|---|
| `_inside_cursor` | added the `not drain and updated_at < cur_updated` skip, with the invariant argument in the docstring |
| `_fetch_items` | `_flush` no longer stops at `len(items) == cap`; it sets `cap_hit` only when a **processable** item is found past a full cap, so the caller keeps scanning the readable region |
| `_fetch_items` | `_at_boundary(second)` (DIFF only) terminates the walk at the boundary descent; returns a 5-tuple with `boundary_stop` |
| `_fetch_items` | the `final` / `allow_exempt` block-exemption machinery is **removed** — the readable-region rule subsumes it (net simplification) |
| `index_repo` | unpacks `boundary_stop`; `issues_beyond_window` stays 0 on a boundary-stopped walk (nothing is deferred, so the stream-wide rel="last" total is not a "remaining" measure) |

No `hosted_api`, MCP-registry or SDK-surface change; the cursor dict shape is untouched (no new field),
so every existing exact-dict assertion keeps its meaning.

## Tests

Four new tests in `tests/test_github_indexer.py` (no new file → no `config/ci-surfaces.yml` change):

| test | falsifies (pre-fix observation) |
|---|---|
| `test_diff_probe_on_dense_boundary_repo_does_not_restamp_truncated` | steady-state poll: pre-fix 40 processed, `{S,40,truncated}`, 13 pages → post-fix 0 processed, `{S1,130}`, 4 pages, `issues_beyond_window == 0` |
| `test_diff_after_drain_cursor_does_not_pingpong_into_drain` | pre-fix `{T10,550}` → `{T9,500,t}` → `{T10,550}` → … forever; post-fix the probe ends clean at `{T0,50}` and the next two polls are no-ops |
| `test_diff_probe_does_not_steal_cap_from_new_items` | pre-fix the probe spent 7 of 10 cap units on the pre-boundary second and minted `{S−1s,206,truncated}`; post-fix 3 processed (only the new items), `{S1,503}` |
| `test_diff_exact_cap_at_boundary_block_mints_clean` | pre-fix minted a false `{S,600,truncated}` (wasting a DRAIN re-walk plus a false `gap_audit_required`); post-fix clean `{S,600}` |

Existing tests are **not modified**: the new behavior is a strict extension (skip more in DIFF mode,
stop earlier, decide truncation on the readable region), and the #1895/#1989 assertions — including
`test_drain_mode_drains_backlog_across_runs` (the DRAIN backlog must still be reached),
`test_cursor_same_second_boundary`, `test_premature_walk_end_*`,
`test_exact_cap_multiple_mints_clean_and_next_run_is_diff`, `test_within_second_order_independent_advance`
— all stay green (42 passed in the file, see Evidence).

## Runtime prerequisites

Same as #1895: Python 3.12 via `uv`, and either the docker lane
(`TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`) or the URI-less carve-out
(`TORTOISE_TEST_CARVE_OUT=1`). The private lane instance (`tools/test_lane.py`) was used for this
work. No migration, no new dependency.

## Verification (run for this plan)

```bash
export TORTOISE_DB_URI='docker://:@127.0.0.1:16391/tortoise_test_matrix'   # tools/test_lane.py uri
.venv/bin/python -m pytest tests/test_github_indexer.py -q                 # 42 passed
.venv/bin/python -m pytest tests/test_github_index_lifecycle.py -q         # 29 passed, 1 pre-existing failure (#4709)
```

The projection-stubbed harness (fetch/flush/mint only) was used to fix the expected cursor sequences
*before* the slow lane runs, and to falsify each test against `origin/main`.

## Residuals (out of scope, filed)

- **#8090** — the DIFF↔DRAIN ping-pong survives when the **newest** second's block exceeds the cap
  (verified cycle `{S,2025} → {S1,500,t} → {S,1000,t} → {S,1500,t} → {S,2000,t} → {S,2025} → …`).
  Breaking it needs the older-than-boundary claim *carried on the cursor* (a marker, with fail-closed
  clearing) so DRAIN may skip provably-indexed older items, plus preserving the input boundary on
  clean mints. Its failure mode is silent loss, so it needs its own design + review.
- **#4709** — `test_repoll_drains_and_clears_truncated_persisted` fails on `main` too (verified by
  stash): the hosted_api POST refuses after the 10s transport wait bound because `index_repo`'s
  projection walk still runs on the event loop. That is why this plan's end-to-end evidence is
  indexer-level plus the stubbed harness.

## Acceptance mapping (#1990)

| criterion | how it is met |
|---|---|
| dense-boundary repo polls without re-stamping `truncated` on the post-drain steady state | `test_diff_probe_on_dense_boundary_repo_does_not_restamp_truncated` (0 processed, clean cursor, 0 mints) |
| no re-walk of the full stream on every poll | the walk terminates at the boundary descent: 4 pages instead of 13 on the pinned shape; the non-dense drain transition stabilises after one poll instead of ping-ponging |
| `test_drain_mode_drains_backlog_across_runs` stays green | unchanged test, green (run 2 still processes the 100-item deferred backlog) |
| exact-once preserved (0 mints, no loss) | every new test asserts `events_minted == 0` on the probe and the full object census; the prefix invariant (#1895) is untouched |
