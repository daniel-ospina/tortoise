---
title: "Plan — #1895 GitHub re-poll cursor advance: second-buffered ASC flush + truncated-clear on clean drain"
type: engineering
domain: capability
doc_status: draft
created: 2026-08-28
subjects.team: epistemic-team
ownedBy: epistemic-team
governingAgreement: "#1895 (standalone task; solution-converge validated against source + empirical repro)"
aboutObjects: github-index-cursor, tortoise-github-indexer
---

<!-- research-path: docs/plans/2026-08-28-1895-repoll-cursor-advance.md (this doc is the converge output; divergence = A1/A2/A3 analysis below, validated 2026-08-28 against tortoise/indexer/github_indexer.py, tests/_github_mock.py, tests/test_github_indexer.py, tortoise/hosted_api.py::_run_indexing). Diverge log: 2 problem-diverge agents (framings + devil's advocate — found the silent-loss variant + fresh-repo exposure), 2 problem-converge agents (converged on the prefix-completeness invariant), 1 solution-diverge agent (A1/A2/A3), 1 solution-converge agent (chose A1, empirically validated on the production shape). All findings recorded in the session; scope-verified clean after 2 cycles. Plan-verify (task-workflow-standard gate): 2 parallel verifiers, 2 cycles. Cycle 1 findings fixed: (1) fold-in of the naive monotonic skip into _inside_cursor REJECTED — empirically traced to skip the DRAIN backlog (#501..#600 never reached on the test_drain_mode fixture; test_drain_mode_drains_backlog_across_runs would fail) — the oscillation stays a MANDATORY fast-follow with its own design (§Follow-ups); (2) steady-state oscillation documented as a bounded 4-poll DIFF/DRAIN cycle (values corrected again in cycle 2 — from {S,500,t} a DRAIN processes exactly 500 more ⇒ {S,1000,t}, not 1040: verified cycle {S+1,1885}→{S,500,t}→{S,1000,t}→{S,1425}→{S+1,1885}); (3) truncated-clear elif hardened with .get() guards (hand-patched cursor KeyError); (4) Task 1 sort-site rebind made explicit (batch = r.json()); (5) Task 4 shape fixed (exact cap-multiple 600 @ S — the production shape needs FOUR runs — initial + 3 re-polls, three capped — to clear truncated); (6) quota-at-item-zero test labeled a GUARD test (green pre/post-fix); (7) fetch-cost caveat (cap cut on the walk's final second ⇒ full-stream fetch) added + follow-up; (8) explicit processed==500 asserts on runs 2-3. Cycle 2: found the {S,1040}→{S,1000} arithmetic error in 4 oscillation sites + acceptance-criterion wording; fixed. Cycle 3: found 2 doc-precision P2s (A3 figure 1925→1885/3.8×cap; Task 4 parenthetical run-count wording); fixed. Cycle 4: clean. -->

# Plan — #1895: GitHub re-poll diff cursor never advances (stuck truncated / silent loss)

## Context

- **Issue:** #1895 — `fix: GitHub re-poll diff cursor never advances — stuck truncated (perpetual DRAIN)`. Production observation: `daniel-ospina/tortoise`'s `github_index_cursor` frozen at `{"number": 1425, "truncated": true, "updated_at": "2026-08-18T02:49:35Z"}` while the graph contains objects/events past #1425 (up to #1885); re-polls keep running DRAIN (no `since`, cap-limited to `_MAX_ITEMS_PER_RUN = 500`) and the boundary never moves.
- **Confirmed problem (problem-verify, this session):** the composite `(updated_at, number)` cursor is exact-once **only when the processed set at its boundary second is a contiguous ASC prefix `[1..N]`**. `_fetch_items` re-sorts **each page** `(updated_at DESC, number ASC)`, but GitHub's `sort=updated&direction=desc` within-second tie order is unspecified, so a same-second block spanning pages is processed high-to-low **across** pages. A cap/quota cut mid-block therefore mints a cursor that cannot express the true (non-prefix) processed set. Two failure variants, both **empirically reproduced on this machine** (see §Current state):
  - **Freeze:** the next DRAIN run over-skips (`updated_at > cursor.updated_at` skipped + `== cursor.updated_at, number <= cursor.number` skipped) and processes **0 items**; `last is None` ⇒ the cursor is never rewritten ⇒ `truncated` persists forever ⇒ every re-poll re-walks the full stream (production: `{1425, truncated, 2026-08-18T02:49:35Z}` exactly reproduced by the current code on the production shape).
  - **Silent loss:** when the non-prefix hole is below the cursor number at the boundary second, the DRAIN skip permanently skips never-indexed low numbers (repro: #1..#1385 never materialize as Objects).
- **Closing criteria:** (1) the processed set at the boundary second of **every minted cursor** is a contiguous ASC prefix — a cursor is never minted from a partially-consumed second-block in the number-ASC sense; (2) both the freeze AND the silent-loss variant are **prevented for NEW state** (the fix is structural, so neither can recur after it lands); (3) existing github_indexer + hosted_api cursor tests stay green; (4) exact-once preserved (no duplicate events/statements across the boundary); (5) the production shape (`1425 items at one second + 460 newer across pages`) drains to completion across consecutive capped runs and `truncated` clears. **Scope-honesty:** any pre-existing production hole BELOW the frozen cursor's number (`#1..#1385` if already lost) is unrecoverable via any composite-cursor machinery — the legacy cursor `{1425, truncated, S}` encodes no processed-set ranges; recovery requires the gap-audit / re-index-from-scratch follow-up (§Follow-ups).
- **Complexity:** `complexity:standard` → Standard tier.
- **Dependencies:** none. Standalone task.
- **Scope guard:** the cursor-boundary mechanics in `tortoise/indexer/github_indexer.py` (`_fetch_items` buffer/flush, `index_repo` truncated-clear) + `tests/_github_mock.py` (deterministic within-second shuffle mode) + `tests/test_github_indexer.py` (+ one optional hosted_api-level test). **Out:** the pre-existing DIFF-mode probe-oscillation (documented §Follow-ups), the legacy non-prefix holes already lost in production (unrecoverable via any composite cursor — see A2 rejection), schema/cursor-shape changes (rejected A2), cap-contract changes (rejected A3), hosted_api schema changes (none needed — the cursor dict round-trips opaque).

## Current state (verified on HEAD 2026-08-28 + empirical repro)

| Surface | Today | Problem (#1895) |
|---|---|---|
| `github_indexer.py::_fetch_items` walk loop (two per-page `sorted(batch, (updated_at, -number), reverse=True)` sites) | per-page re-sort to `(updated DESC, number ASC)`; pages arrive in GitHub's unspecified within-second order | a same-second block spanning pages is processed high-to-low across pages ⇒ cap cut mid-block mints a non-prefix boundary (e.g. processed set `{1386..1425}` at second S with cursor `{S, 1425}`) |
| `github_indexer.py::_inside_cursor` DRAIN skip | skips `updated_at > cursor.updated_at` **and** `== cursor.updated_at, number <= cursor.number` | valid ONLY when the boundary processed set is a prefix; over-skips non-prefix holes (loss) or everything (freeze) |
| `github_indexer.py::index_repo` cursor mint | `last is None` ⇒ cursor untouched (stays truncated forever on a 0-processed drain) | a fully-drained backlog / exact-cap-multiple run leaves `truncated: true` permanently ⇒ perpetual DRAIN re-walks |
| **Empirical repro (this session, real embedded SDK, mock page_size=100):** production shape `1425 items at S (#1..#1425) + 460 at S+1 (#1426..#1885)` | RUN1 `cap=500` → processed 500, cursor **exactly `{S, 1425, truncated}`** (the production frozen value); RUN2 → processed **0**, cursor unchanged | RUN1's processed set at S = `{1386..1425}` (non-prefix); #1..#1385 never indexed (**loss**); RUN2 freezes (**stuck truncated**) |
| Same repro under the A1 buffer/flush (empirically re-validated this session) | RUN1→`{S, 40, truncated}`, RUN2→`{S, 540, truncated}`, RUN3→`{S, 1040, truncated}`, RUN4→385 processed, `{S, 1425}` **clean**, RUN5 (DIFF)→cursor `{S+1, 1885}`, 0 new nodes | boundary advances every run; `truncated` clears; ALL 1885 Objects present including #1/#40/#1385 |
| Legacy frozen cursor `{S, 1425, truncated}` fed to A1 (the production heal) | RUN1 (DRAIN)→0 processed → **clean `{S, 1425}`** (truncated-clear), RUN2 (DIFF)→460 processed (the newer block), `{S+1, 1885}` | the frozen cursor resumes advancing on the FIRST re-poll — no data loss, no re-walk backlog |
| Exact-cap-multiple repro (`500 items at S, cap 500`) under A1 + truncated-clear | RUN1→`{S, 500, truncated}`, RUN2→0 processed → **clean `{S, 500}`**, RUN3 (DIFF, `since` in query)→0 processed, all 500 Objects | truncated-clear fix verified |

## Pattern Research

> **Findings date:** 2026-08-28. **Gate skipped:** plan touches ZERO new third-party dependencies (stdlib + in-repo patterns only — `github_map._norm_issue`, the existing `_inside_cursor` skip rules, the existing `since`/DRAIN walk). Step A (prior research intake) ran: the #1895 issue body (O/I/T + context + research-needed), the P1-4 DRAIN design (PR #1792), T2-P4 composite-cursor design, and the three divergence approaches below, all re-verified against source this session.

**Codebase-verified mechanics:**
- GitHub stream order (`sort=updated&direction=desc`) delivers each exact `updated_at` second as a **contiguous run**; within-second tie order is unspecified. A per-second buffer can therefore always be completed by waiting for a different second (or walk end) — no extra ordering information is needed. **Assumption (documented, same contract the existing composite cursor already makes):** the stream is globally non-increasing in `updated_at`, so each second's items arrive contiguously across pages. GitHub guarantees `sort=updated` ordering; within-second ties are the only unspecified axis (neutralized by the buffer). Defensive hardening (merge same-second sub-blocks across a second-transition) is possible but not required — the current code fails identically under any non-contiguous stream, so A1 is never a regression on this axis.
- The composite cursor is exact-once **iff** every minted boundary second's processed set is an ASC prefix — the number tiebreak `<= cursor.number` then decides the boundary exactly. The fix must make this a **structural invariant** (never mint from a partial second-block), not a representational workaround.
- GitHub's `since` is inclusive and carried in Link next-URLs (DIFF window = `[cursor.updated_at − 1s, ∞)`; DRAIN strips `since` for the full walk) — unchanged by this plan; the DIFF window's `−1s` conservative over-fetch (the boundary second **and** the previous second re-probed idempotently) is pre-existing and preserved.
- `hosted_api._run_indexing` persists the cursor dict **opaquely** (`if result.get("cursor"): cursors[repo] = result["cursor"]`); no schema/validation on its contents; the truncated-clear flows through unchanged.

## Integration Surface Map (test-design — #1895-owned subset)

| Surface | Boundary | Bug pattern | Test layer |
|---|---|---|---|
| per-second flush ordering | `_fetch_items` buffer/flush → processed list | non-prefix boundary cut (loss/freeze) | unit + integration (multi-page same-second block) |
| DRAIN skip validity | `_inside_cursor` ← minted cursor | over-skip of never-indexed items | integration (production-shape drain) |
| truncated lifecycle | `index_repo` mint → persisted cursor → next run regime (DRAIN⇄DIFF) | `truncated` never clears ⇒ perpetual DRAIN | integration (0-processed drain, exact-cap-multiple, stuck-truncated) |
| within-second order robustness | mock pagination order ← real API tie order | fix passes on number-desc order but breaks on arbitrary order | unit (shuffle mode) |
| persistence round-trip | `hosted_api._run_indexing` finally | clean cursor lost / truncated stuck | existing lifecycle tests (green) + optional re-poll test |

## Approach selection — **A1: second-buffered ASC flush (+ `index_repo` truncated-clear)**

### Chosen: A1 — second-buffered ASC flush

**Mechanism (validated end-to-end on the production shape):** in `_fetch_items`, drop the two per-page re-sorts. Buffer each exact `updated_at` second as a contiguous run (the API stream is updated-DESC, so each second arrives as one contiguous block; the block is complete when a different second appears or the walk ends). Flush each **complete** block sorted `(updated_at, number)` ASC, feeding items through `_inside_cursor` and the cap. A cap/quota cut lands **mid-flush of a complete block** — the processed set at the cut second is by construction a contiguous ASC prefix, so the minted composite cursor always expresses the true processed set. The unprocessed buffered high numbers are dropped and re-fetched next run (DRAIN refetches from the top anyway). Plus the small `index_repo` fix: a **0-processed run that did not cap/quota-cut mints a CLEAN boundary cursor from the input cursor** (drops `truncated`), so a fully-drained backlog exits DRAIN.

**Rationale (outcome quality):**
- **Structural, not representational:** the prefix invariant is guaranteed by construction (a partial second-block is never flushed), so the loss variant is **impossible** — not just mitigated. The freeze variant is impossible twice over: the boundary always advances while backlog remains (each run drains ≥ 1 new ASC-prefix chunk ≤ cap), and the truncated-clear path exits DRAIN once drained.
- **Exact cap preserved:** processing stays `<= cap` exactly (hard ceiling, `_MAX_ITEMS_PER_RUN = 500` stays meaningful for cost control).
- **Zero schema churn:** cursor shape unchanged (`{updated_at, number[, truncated]}`); hosted_api models/validation/tests untouched; the legacy frozen cursor `{1425, truncated, S}` is *healed in place* by the same machinery (first DRAIN run either drains or 0-processed-clears).
- **Existing tests stay green** (traced + empirically re-validated this session): `test_drain_mode_drains_backlog_across_runs` (12 seconds × 50/sec, page_size 100 — each page holds 2 COMPLETE seconds, page boundaries fall between seconds, so no second spans a page; the A1 buffer also handles page-spanning seconds, so this test drains identically under both codes — sim-verified: run 1 `{T9,500,truncated}`, run 2 processed 100 → clean `{T11,600}`), `test_cursor_same_second_boundary` (cap=1 exact-once), `test_cursor_persists_and_stops_walk` (exact dict shape), `test_truncation_reports_issues_beyond_window`, `test_quota_break_stamps_truncated_cursor`, error-path tests, all lifecycle cursor/persistence tests.
- **Production cost is bounded and terminating (during the drain):** per-run fetch = the full DRAIN walk (~19-20 pages on the production repo — the boundary block S is the walk's FINAL second, so A1 must fetch through the stream end to complete it; same order as today's frozen runs, which already re-walk the full stream at 0 processed) but the state machine terminates: RUN1 500 → RUN2 500 → RUN3 500 → RUN4 385 → RUN5 (DIFF) clears the production freeze, vs today's infinite re-walk. Cap bounds PROCESSED/write volume (≤ 500 — the real cost control). **Fetch-cost caveat (plan-verify cycle 1):** when the cap cuts on the walk's FINAL second (production: the 1425-item S block is the oldest active second), A1 fetches the entire remaining stream before flushing — a FIRST run under today's code stops fetching at the cap (~page 5); A1 fetches to the stream end (~19 pages). On a repo whose oldest active second is a mass-update (tens of thousands of issues) every DRAIN/DIFF poll re-walks the full stream — tracked as a follow-up (fetch-budget guard, §Follow-ups); the drain path itself is unaffected (today's frozen runs already walk the full stream). **Steady-state caveat (post-drain — empirically verified, plan-verify cycle 1):** the DIFF window `[cursor.updated_at − 1s, ∞)` re-probes the pre-boundary second each poll (DIFF `_inside_cursor` skips only the cursor's own second); on a dense-boundary repo the probe consumes the whole cap and mints a cursor REGRESSED to the older second + re-stamps `truncated`. Verified 4-poll cycle on the production shape: `{S+1,1885} → {S,500,truncated} → {S,1000,truncated} → {S,1425} → {S+1,1885} → …` (poll 1 DIFF re-probes the S block and caps mid-block → `{S,500,t}`; poll 2 DRAIN processes exactly 500 more (#501..#1000) → `{S,1000,t}` — NOT 1040, which belongs to the initial drain from 40; poll 3 drains the remaining 425 → clean `{S,1425}`; poll 4 DIFF re-probes idempotently → `{S+1,1885}`. Truncated re-stamped on 2 of every 4 polls; every poll re-walks the full stream on this shape — the since-window covers the entire stream). Idempotent (0 mints, no loss), correct, but not "free" — the issue's headline symptom ("re-polls keep running DRAIN") recurs in milder form. Tracked by the MANDATORY fast-follow issue filed with this PR; the naive monotonic-skip fold-in is UNSAFE (skips the DRAIN backlog — §Follow-ups) and must NOT be added here.

**Edge cases (each traced + most empirically validated):**
- **Quota break mid-block:** `quota_check` fires per-item in `index_repo` against the ASC-flushed list ⇒ break leaves a prefix at the boundary ⇒ cursor `{S, N, truncated}` valid; next DRAIN resumes at the tail. (Existing `test_quota_break_stamps_truncated_cursor` covers the single-page case; `test_quota_break_at_item_zero_keeps_truncated` covers the break-before-first-item branch.)
- **`issues_beyond_window` on a 0-processed clear run:** `total_estimate` (rel="last" upper bound) still reports the full stream total, so the truncated-clear run can show "N issues beyond window" while actually draining nothing — a pre-existing reporting quirk (any full-walk DRAIN run reports the total); the number is an upper bound over the whole stream, not a backlog measure. Not changed by this fix.
- **Exact-cap-multiple end:** 500 new items, cap 500 ⇒ run mints `{S, 500, truncated}` honestly; next run 0-processed ⇒ truncated-clear mints clean `{S, 500}` ⇒ next run DIFF (validated).
- **Empty window / stuck-truncated:** 0-processed non-capped drain ⇒ clean cursor from input (validated — the legacy production frozen cursor `{S, 1425, truncated}` heals on its FIRST re-poll: DRAIN skips everything, truncated-clear mints `{S, 1425}`, the next poll is DIFF); DRAIN exits; DIFF re-polls are stable.
- **DIFF steady state:** cursor `{S, N}`, a few new items at S+1 ⇒ DIFF window `[S−1s, ∞)` ⇒ S+1 block processed, boundary block skipped `<= N`, cursor advances to `{S+1, max}` — the boundary moves to the newest second, not a page-arbitrary number. (The pre-boundary second is re-probed idempotently — the documented steady-state oscillation, §Follow-ups.)
- **End-of-walk partial block:** the final run flushes at walk end (complete by walk-end) — the partial block is a *complete* run.
- **Items with empty `updated_at`:** buffer as the `""` run, flush last (smallest string); `_inside_cursor` returns False (processed once); the cursor mint guard `if n["updated_at"]` never pins on them (preserved).
- **Mid-walk 401/429:** `GitHubFetchError` propagates from `_get` before any cursor is minted — `index_repo` never reaches the mint ⇒ `stats["cursor"]` stays the input ⇒ `_run_indexing` re-persists the input (honest fail, resume without gaps) — unchanged.
- **Memory:** the buffer holds one complete second-block (production 1425 × ~1 KB) — negligible.

### Rejected: A2 — representational cursor (`{"updated_at": U, "ranges": [[lo,hi],...]}`)

**When it WOULD have been better:** only if the legacy production cursor's true processed set were **knowable** (it is not — the frozen `{1425, S}` encodes no ranges) or if a future producer could mint non-prefix cursors (A1 makes that structurally impossible). A2's range lookup is ordering-robust by representation rather than by construction.
**Rejected because:** (1) schema churn — cursor shape, `hosted_api` model fields/validation/defaults, every exact-dict assertion (`test_cursor_persists_and_stops_walk`, `test_state_keys_survive_patch_roundtrip`, lifecycle persistence tests) — for zero gain over A1 on the actual failure modes; (2) the migration shim for the legacy cursor must conservatively reprocess the boundary block (idempotent probes) — i.e. **the same recovery A1 gets for free** via DRAIN refetch; (3) A2 does NOT fix the freeze — a 0-processed truncated run still needs the same truncated-clear; (4) a larger `_inside_cursor` correctness surface (range parsing/merging, boundary-second ranges vs newer-second coverage) with no compensating benefit.

### Rejected: A3 — second-aligned cap (block overshoot)

**When it WOULD have been better:** if drain latency were the dominant concern AND the cap contract were negotiable — production's 1885-item stream (1425 @ S + 460 @ S+1) would clear in ONE run (~3.8× cap).
**Rejected because:** (1) it changes `cap` from a hard ceiling to a soft budget — `_MAX_ITEMS_PER_RUN = 500` is the hosted job's cost control; an unbounded same-second block (a mass-update event can touch thousands of issues) means unbounded per-run cost and per-run points-adjacent write volume; (2) it REWRITES the existing green `test_cursor_same_second_boundary` (cap=1 must process exactly 1 today) — the acceptance says existing tests stay green; (3) it still needs the truncated-clear fix (a fully-drained-but-truncated cursor processes 0 and stays stuck), so A3 = A1's cost increase with A1's machinery minus the cap guarantee. Strictly dominated.

## Implementation Tasks

### Task 1: Rework `_fetch_items` — second-buffered ASC flush

**Files:**
- Modify: `tortoise/indexer/github_indexer.py` (`_fetch_items`, ~line 300-372)
- Test: `tests/test_github_indexer.py`

**Step 1: Write the failing regression test** (`tests/test_github_indexer.py`) — production shape; must FAIL on the current code (run 1 mints `{S, 1425, truncated}` non-prefix; low numbers lost). (All expected cursor values below re-verified empirically against the real mock + A1 algorithm this session.):

```python
def test_second_block_spanning_pages_drains_and_advances_boundary(sdk):
    """#1895: a same-second block spanning pages (production shape: 1425
    items at one second + 460 newer across pages) must drain ACROSS capped
    runs with the boundary advancing per run, and must never lose low
    numbers. Pre-fix: run 1 processed page 5's boundary items high-to-low
    (a non-prefix set {1386..1425}) and minted {S, 1425, truncated}; run 2
    (DRAIN) skipped #1..#1385 forever (loss) and froze at 0 processed."""
    S = "2026-08-18T02:49:35Z"
    S1 = "2026-08-18T02:49:36Z"
    issues = [gh_issue(n, updated_at=S1) for n in range(1426, 1886)]
    issues += [gh_issue(n, updated_at=S) for n in range(1, 1426)]
    t = MockGitHubTransport(issues=issues, page_size=100)  # 19 pages
    # run 1: 460 newer + 40 boundary-lowest → {S, 40, truncated}
    stats1 = _run(_indexer(t), sdk, cap=500)
    assert stats1["processed"] == 500
    assert stats1["cursor"] == {"updated_at": S, "number": 40, "truncated": True}
    assert stats1["issues_beyond_window"] > 0
    # runs 2-3: DRAIN drains 500 more each; boundary number strictly advances
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=500)
    assert stats2["processed"] == 500
    assert stats2["cursor"] == {"updated_at": S, "number": 540, "truncated": True}
    stats3 = _run(_indexer(t), sdk, cursor=stats2["cursor"], cap=500)
    assert stats3["processed"] == 500
    assert stats3["cursor"] == {"updated_at": S, "number": 1040, "truncated": True}
    # run 4: the boundary block tail (385) drains WITHOUT a cap cut → clean
    stats4 = _run(_indexer(t), sdk, cursor=stats3["cursor"], cap=500)
    assert stats4["processed"] == 385
    assert stats4["cursor"] == {"updated_at": S, "number": 1425}  # truncated gone
    # run 5 (DIFF): boundary-second items are probed, NOT re-minted — exact-once
    stats5 = _run(_indexer(t), sdk, cursor=stats4["cursor"], cap=500)
    assert stats5["events_minted"] == 0
    assert stats5["cursor"]["updated_at"] == S1  # boundary advanced past S
    # (steady-state probe: the DIFF window [S−1s,∞) re-probes the boundary
    # window — the 460 processed are the NEWER S1 block (idempotent probes,
    # 0 mints); the S block is entirely skipped (all numbers <= 1425);
    # documented §Follow-ups)
    # NO loss: every number, including the lows the pre-fix code skipped
    proj = sdk._get_proj()
    assert proj.g.query("MATCH (n:Object) RETURN count(n)").result_set[0][0] == 1885
    for n in (1, 40, 41, 1385, 1386, 1425, 1426, 1885):
        rows = proj.g.query(
            "MATCH (o:Object {id:$oid}) RETURN count(o)",
            params={"oid": f"github-issue-acme/repo1-{n}"}).result_set
        assert int(rows[0][0]) == 1, f"issue #{n} must be indexed (no loss)"
```

**Step 2:** Run it — verify it FAILS on the current code (run 1 cursor `{S, 1425, truncated}`; the `#1/#40/#41/#1385` assertions fail).

**Step 3: Implement** — replace the `_fetch_items` walk loop with the second-buffered flush. Exact change:
- Delete both per-page `batch = sorted(batch, key=..., reverse=True)` blocks (the re-sort becomes a per-**block** ASC sort inside the flush). ⚠️ The SECOND site (the post-fetch `batch = sorted(r.json(), ...)`) ALSO rebinds `batch` — when deleting the sort, keep the rebind as `batch = r.json()` followed by the unchanged `urls = self._link_header_urls(...)` / `next_url = urls.get("next")` / `total_estimate` refresh; losing the rebind re-iterates the stale batch forever (infinite loop against a non-empty page).
- Insert a `_flush(block)` closure + the `current_second`/`block` buffering in the walk loop (see pseudocode below).
- Update the docstring (new `#1895` paragraph).

```python
    # #1895: the GitHub stream (sort=updated&direction=desc) delivers each
    # exact updated_at SECOND as a contiguous run (within-second order
    # unspecified). Buffer each run and flush it COMPLETE, sorted number-ASC,
    # so a cap/quota cut mid-run always mints a cursor whose boundary-second
    # processed set is a contiguous ASC prefix — the composite cursor can
    # then express the true processed set exactly (the pre-fix per-page
    # re-sort processed a page-spanning same-second block high-to-low and
    # minted non-prefix cursors: DRAIN over-skipped → freeze/loss, #1895).
    def _flush(block: list[dict]) -> bool:
        """Process ONE complete second-run ASC. True ⇒ cap hit (walk stops)."""
        nonlocal items, cap_hit
        block.sort(key=lambda i: int(
            github_map._norm_issue(i)["number"] or 0))
        for item in block:
            if self._inside_cursor(item, cursor, drain=drain):
                continue
            items.append(item)
            if len(items) >= cap:
                cap_hit = True
                return True
        return False

    current_second: str | None = None
    block: list[dict] = []
    while True:
        for item in batch:
            second = github_map._norm_issue(item)["updated_at"]
            if current_second is None:
                current_second = second
            elif second != current_second:
                # previous second's run is COMPLETE — flush before the next
                if _flush(block):
                    return items, total_estimate, cap_hit
                current_second = second
                block = []
            block.append(item)
        if not next_url:
            _flush(block)  # final run — complete at walk end
            return items, total_estimate, cap_hit
        if since_cursor is None:
            ...  # unchanged: strip `since` from Link next-URL (DRAIN)
        r = await self._get(client, next_url)
        ...  # unchanged: next batch / next_url / total_estimate
```

**Step 4:** Run the new test — green. **Step 5:** Run the full file + lifecycle — all green.

### Task 2: `index_repo` — truncated-clear on clean 0-processed drain

**Files:**
- Modify: `tortoise/indexer/github_indexer.py` (`index_repo` cursor mint, ~line 720-735)
- Test: `tests/test_github_indexer.py`

**Step 1: Write the tests.** FALSIFIERS (both verified to FAIL on the current code — pre-fix a 0-processed run leaves `last is None` so the input truncated cursor is returned untouched): `test_truncated_clears_on_zero_processed_drain` and `test_stuck_truncated_cursor_clears_on_empty_drain`. GUARD test (green pre- AND post-fix; red only on an implementation that OMITS the `not quota_hit` guard): `test_quota_break_at_item_zero_keeps_truncated` — it protects the guard, it does not falsify the current code.

```python
def test_truncated_clears_on_zero_processed_drain(sdk):
    """#1895: an exact-cap-multiple run (500 new items, cap 500) stamps
    truncated; the NEXT run (DRAIN) processes 0 and must mint a CLEAN
    boundary cursor so the run after exits DRAIN. Pre-fix: the 0-processed
    run left `last is None` → cursor untouched → truncated forever."""
    S = "2026-08-18T02:49:35Z"
    t = MockGitHubTransport(
        issues=[gh_issue(n, updated_at=S) for n in range(1, 501)],
        page_size=100)
    stats1 = _run(_indexer(t), sdk, cap=500)
    assert stats1["cursor"] == {"updated_at": S, "number": 500, "truncated": True}
    stats2 = _run(_indexer(t), sdk, cursor=stats1["cursor"], cap=500)
    assert stats2["processed"] == 0
    assert stats2["cursor"] == {"updated_at": S, "number": 500}  # clean
    # run 3 is DIFF (its first issues request carries `since`), not DRAIN —
    # and stays exact-once
    n_before = len(t.issue_query_params())
    stats3 = _run(_indexer(t), sdk, cursor=stats2["cursor"], cap=500)
    assert stats3["processed"] == 0
    assert any("since" in p for p in t.issue_query_params()[n_before:]), \
        "a clean cursor must exit DRAIN (the next run uses since)"
    assert sdk._get_proj().g.query(
        "MATCH (n:Object) RETURN count(n)").result_set[0][0] == 500


def test_stuck_truncated_cursor_clears_on_empty_drain(sdk):
    """#1895: the exact production freeze shape — a truncated cursor whose
    backlog is FULLY indexed (the drain walk skips everything). The run
    must mint a clean cursor (not stay truncated forever) so re-polls exit
    DRAIN and stop re-walking the full stream."""
    S = "2026-08-18T02:49:35Z"
    t = MockGitHubTransport(issues=[gh_issue(1, updated_at=S),
                                    gh_issue(2, updated_at=S)])
    _run(_indexer(t), sdk)  # index both
    stuck = {"updated_at": S, "number": 2, "truncated": True}
    stats = _run(_indexer(t), sdk, cursor=stuck)
    assert stats["processed"] == 0
    assert stats["cursor"] == {"updated_at": S, "number": 2}  # truncated gone


def test_quota_break_at_item_zero_keeps_truncated(sdk):
    """#1895 (scope-verify): the truncated-clear guard `not quota_hit` is
    load-bearing. A quota break BEFORE the first item (processed=0,
    quota_hit=True, last=None) must NOT mint a clean cursor — the deferred
    backlog (items OLDER than the boundary second) would then be missed by
    the since-bounded DIFF walk → permanent silent loss. Pre-fix-adjacent:
    without the guard this test fails (cursor loses truncated)."""
    S = "2026-08-18T02:49:35Z"
    t = MockGitHubTransport(issues=[gh_issue(1, updated_at=S),
                                    gh_issue(2, updated_at=S)])
    from tortoise.quota import QuotaExceededError

    def _quota_check():
        raise QuotaExceededError("limit reached (test)")

    stuck = {"updated_at": S, "number": 1, "truncated": True}
    stats = _run(_indexer(t), sdk, cursor=stuck, quota_check=_quota_check)
    assert stats["processed"] == 0
    assert stats["quota_hit"] is True
    assert stats["cursor"]["truncated"] is True, \
        "a quota break is not a clean end — truncated must persist"
    # GUARD test (green pre- AND post-fix): passes on the current code too —
    # it only fails if an implementation writes the truncated-clear WITHOUT
    # the `not stats["quota_hit"]` guard (a quota break with 0 processed
    # must keep truncated: the deferred older backlog would be missed by a
    # since-bounded DIFF walk).
```

**Step 2:** (no separate step needed — the `since`-probe assertion above is final: capture the request count before run 3, then assert that at least one of the run's requests (its first page) carries `since`; the mock's pagination next-URLs carry no `since`, so only the first page of a DIFF walk has it).

**Step 3: Implement** — the exact code path (after the processing loop). The `elif` guards are `cursor.get(...)` (not bare indexing) so a hand-patched PATCH-state cursor (`{"truncated": true}` without `updated_at`) can never KeyError inside the clear path (plan-verify cycle 1):

```python
        if last is not None:
            new_cursor: dict[str, Any] = {
                "updated_at": last["updated_at"], "number": last["number"]}
            # Cap-truncated runs AND quota-interrupted runs stamp
            # `truncated` so the next run enters DRAIN mode and keeps
            # draining the deferred backlog (P2, PR #1792: a quota break
            # must not silently drop the unprocessed tail).
            if cap_hit or stats["quota_hit"]:
                new_cursor["truncated"] = True
            stats["cursor"] = new_cursor
        elif (cursor is not None and cursor.get("truncated")
                and cursor.get("updated_at")
                and not cap_hit and not stats["quota_hit"]):
            # #1895: a 0-processed run that did NOT cap/quota-cut means the
            # DRAIN walk skipped EVERY item — the deferred backlog is fully
            # drained (the previous truncated run landed on an exact cap
            # multiple, or the boundary block is exhausted). Mint a CLEAN
            # boundary cursor (drop `truncated`) so the next run exits
            # DRAIN; otherwise the cursor stays truncated forever and every
            # re-poll re-walks the full stream (production freeze, #1895).
            # (`last is None and cap_hit` is unreachable with non-empty
            # updated_at items — cap_hit requires >= cap processed items,
            # and an empty-updated_at item never pins `last` (pre-existing
            # degenerate edge, impossible on GitHub's schema); `quota_hit`
            # with 0 processed keeps truncated: a quota break is not a
            # clean end — the deferred older backlog would be missed by a
            # since-bounded DIFF walk. The `.get()` guards + falsy
            # updated_at check keep the clear path KeyError-proof against
            # hand-patched cursors — plan-verify cycle 1.)
            stats["cursor"] = {
                "updated_at": cursor["updated_at"],
                "number": int(cursor.get("number") or 0)}
```

**Step 4:** run the new tests — green. **Step 5:** full-file + lifecycle green.

### Task 3: Mock — deterministic within-second shuffle mode

**Files:**
- Modify: `tests/_github_mock.py` (`MockGitHubTransport.__init__` + issues handler)
- Test: `tests/test_github_indexer.py`

**Step 1 (test first):** `test_within_second_order_independent_advance` — 250 items at ONE second, `page_size=100`, `shuffle_within_second=True, seed=1895`; runs cap=100 → cursor `{S, 100, truncated}` → `{S, 200, truncated}` → clean `{S, 250}`; all 250 Objects present. (Fails pre-fix: run 1 mints `{S, 250, truncated}` — the 100 processed are the LOWEST per-page ASC slices… actually pre-fix the exact cut is order-dependent; the assertion that pins the **prefix** invariants is the loss-free Object census.)

**Step 2 (implement):** add `shuffle_within_second: bool = False` and `seed: int = 1895` kwargs; after the global `items.sort(...)` and before pagination, shuffle each equal-`updated_at` run with `random.Random(self.seed)` (stable, deterministic):

```python
        if self.shuffle_within_second:
            import random
            rng = random.Random(self.seed)
            i = 0
            while i < len(items):
                j = i + 1
                while j < len(items) and (items[j].get("updated_at")
                                          == items[i].get("updated_at")):
                    j += 1
                sub = items[i:j]       # NOTE: rng.shuffle(items[i:j]) would
                rng.shuffle(sub)       # shuffle a slice COPY (silent no-op) —
                items[i:j] = sub       # assign the shuffled slice back (#1895)
                i = j
```

**Step 3:** green. (This proves the ASC flush is independent of the within-second tie order — the mock's default number-DESC order is one realistic GitHub tie order; arbitrary order must behave identically. Consider asserting with a second fixed permutation, e.g. `seed=1`, to exercise more than one tie order. Post-drain, also assert `processed == 0` on the final run to pin the freeze-absence explicitly.)

### Task 4 (optional, stretch): hosted_api-level re-poll drain

**Files:**
- Test: `tests/test_github_index_lifecycle.py` (extend the existing `mock_github` pattern with a cap-limited transport)

`test_repoll_drains_and_clears_truncated_persisted`: seed a transport with an **exact cap-multiple shape — 600 items @ one second S, page_size 100** (the PRODUCTION shape would need FOUR runs — initial poll + 3 DRAIN re-polls, three of them cap-truncated — to clear truncated: 40→540→1040→1425, run 4 uncapped — so a single re-poll cannot demonstrate the clear; plan-verify cycle 1). Sequence: POST `/v1/index/github` → run 1 truncates at 500 → persisted cursor `{S, 500, truncated}`; POST `/v1/index/github/re-poll` (DRAIN) → drains 100 → persisted cursor clean `{S, 600}` (no `truncated` key); POST re-poll again → DIFF — assert `github_index_cursor["acme/repo1"] == {"updated_at": S, "number": 600}` AND that re-poll's first issues request carries `since` (via `transport.issue_query_params()`), 0 new nodes. Optional because the indexer-level tests (Tasks 1-2) cover the acceptance; this one proves the persistence round-trip end-to-end.

## Testing Strategy

**Existing tests that verify behavior (must stay green — all traced, docker lane verified on HEAD):**
- `tests/test_github_indexer.py`: `test_drain_mode_drains_backlog_across_runs` (multi-run DRAIN), `test_cursor_same_second_boundary` (cap=1 same-second exact-once), `test_cursor_persists_and_stops_walk` (exact cursor dict), `test_truncation_reports_issues_beyond_window`, `test_quota_break_stamps_truncated_cursor`, `test_quota_check_error_re_raised`, `test_mid_walk_401_honest_fail`, `test_fetch_error_raises_for_transport`, `test_rerun_zero_new_nodes`, lifecycle/object-only tests, PR tests.
- `tests/test_github_index_lifecycle.py`: `test_cursor_and_backfill_marker_persisted`, `test_issue_ingest_no_longer_consumes_points_quota` (clean cursor, no truncated), `test_resolve_repos_failure_preserves_persisted_cursors`, `test_second_run_full_org_with_cursors`, `test_state_keys_survive_patch_roundtrip`.
- No existing test asserts a truncated cursor STAYS truncated on a 0-processed run (grep-verified) — the truncated-clear breaks nothing.

**New tests (all in `tests/test_github_indexer.py` unless noted):**
1. `test_second_block_spanning_pages_drains_and_advances_boundary` — the issue-required integration test: ≥2 consecutive capped runs over a synthetic backlog advance the boundary and eventually clear `truncated`; exact production shape (1425 @ one second + 460 newer); asserts the no-loss Object census including the pre-fix-skipped low numbers.
2. `test_truncated_clears_on_zero_processed_drain` — exact-cap-multiple end: run 1 `{S, 500, truncated}` → run 2 0-processed → clean `{S, 500}` → run 3 DIFF (`since` present in the last issue query) → 0 new.
3. `test_stuck_truncated_cursor_clears_on_empty_drain` — the legacy frozen-cursor shape heals: 0-processed DRAIN mints a clean cursor.
4. `test_within_second_order_independent_advance` — mock shuffle mode proves order-independence of the ASC flush.
5. (optional) `tests/test_github_index_lifecycle.py::test_repoll_drains_and_clears_truncated_persisted` — hosted_api persistence round-trip.

## Verification Plan

```bash
# 1. Local quick check (URI-less carve-out lane — validated working on this machine):
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_github_indexer.py -q          # baseline 24 passed

# 2. Docker lane (default; FalkorDB container is up: `docker ps` → falkordb:6379):
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
  uv run pytest tests/test_github_indexer.py tests/test_github_index_lifecycle.py -v   # baseline 49 passed

# 3. New tests specifically:
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
  uv run pytest tests/test_github_indexer.py -k "second_block_spanning or zero_processed_drain or stuck_truncated or within_second_order" -v

# 4. Full-suite regression (docker lane):
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/ -q

# 5. Pre-commit gate (commit-workflow skill): uv run pytest tests/test_github_indexer.py tests/test_github_index_lifecycle.py
```

**Order of implementation gates:** Task 1 test fails on current code → Task 1 impl → green → Task 2 test fails → Task 2 impl → green → Task 3 → green → full-file + lifecycle + full-suite regression.

## Acceptance Criteria

1. `test_second_block_spanning_pages_drains_and_advances_boundary` green — the production shape (1425 @ one second + 460 newer) drains across capped runs with the boundary advancing per run (40→540→1040→1425) and `truncated` clearing; all 1885 issues indexed, no low-number loss.
2. `test_truncated_clears_on_zero_processed_drain` + `test_stuck_truncated_cursor_clears_on_empty_drain` green — a 0-processed, non-capped/quota DRAIN run mints a clean boundary cursor; the next run exits DRAIN (query carries `since`).
3. Both failure variants impossible: no minted cursor's boundary-second processed set is ever a non-prefix (structural: partial second-blocks are never flushed); the frozen production cursor `{1425, truncated, S}` resumes advancing on the first re-poll (1 DRAIN 0-processed clear + 1 DIFF — empirically verified).
4. Exact-once preserved — `events_minted == 0` on the post-drain DIFF run; `test_cursor_same_second_boundary` / `test_drain_mode_drains_backlog_across_runs` / `test_rerun_zero_new_nodes` green.
5. All existing github_indexer + hosted_api lifecycle tests green (both lanes).
6. Processing stays `<= cap` exactly (A1 preserves the hard ceiling; A3 rejected for relaxing it).
7. Post-drain steady state on dense-boundary repos is the DOCUMENTED bounded oscillation (`{S+1,1885} → {S,500,t} → {S,1000,t} → {S,1425} → {S+1,1885}` — lossless, idempotent, 0 mints): the boundary advances on polls 2-4 of each cycle and regresses once (S+1→S) when the DIFF probe caps mid-block — the FREEZE (boundary never advancing) is impossible. Tracked by the mandatory fast-follow issue filed with this PR (§Follow-ups). The loss (low numbers permanently skipped) is structurally impossible post-fix; the fast-follow is the only remaining churn.

## Runtime Prerequisites

- Python 3.12+ via `uv` (min uv 0.6.0; `uv sync` before running).
- Docker lane: FalkorDB container on `localhost:6379` (`docker compose up -d` — this repo's `docker-compose.yml`; AGENTS.md references the shared eldato compose for the CI matrix) + `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`.
- URI-less quick checks: `TORTOISE_TEST_CARVE_OUT=1` (test_github_indexer.py is NOT in the 17-file carve-out list — carve-out only bypasses the URI gate; embedded construction works locally).
- `TORTOISE_SECRET_PEPPER` — set by the test module (`test-static-pepper`), no operator action.
- No schema migration, no hosted_api code change, no new dependencies.

## Follow-ups (out of scope, documented for a future issue)

- **DIFF-mode probe oscillation (pre-existing, MANDATORY fast-follow — FILE THE ISSUE with this PR):** because `_inside_cursor` (DIFF) only skips the cursor's boundary second (not older seconds), a DIFF run whose `since − 1s` window includes the previous second re-probes it idempotently and can mint a cursor at that older second. **Post-fix production consequence (empirically verified on the production shape):** with a boundary second larger than cap (production: 1425 > 500), the probe consumes the whole cap, mints a cursor regressed to the older second, and re-stamps `truncated`. Verified 4-poll cycle: `{S+1,1885} → {S,500,truncated} → {S,1000,truncated} → {S,1425} → {S+1,1885} → …` (poll 1 DIFF probe caps mid-block → `{S,500,t}`; poll 2 DRAIN processes exactly 500 more → `{S,1000,t}`; poll 3 drains the remaining 425 → clean `{S,1425}`; poll 4 re-probes → `{S+1,1885}`) — the freeze converts into a bounded DIFF/DRAIN oscillation with `truncated` re-stamped on 2 of every 4 polls and a full-stream re-walk on every poll (the since-window covers the entire stream on this shape). Correct (0 new nodes, no loss) but not free — the issue's headline symptom ("re-polls keep running DRAIN") recurs in milder form until this lands. **Why the naive monotonic skip is NOT folded into #1895 (plan-verify cycle 1 — REJECTED with an empirical trace):** the rule `updated_at < U or (== U and number <= N)` applied to DRAIN mode SKIPS THE DEFERRED BACKLOG — verified on the `test_drain_mode` fixture (600 items, cap 500, cursor `{T9, 500, truncated}`): the monotonic rule re-processes the already-indexed newer 450 items (wasting the cap) and never reaches the deferred tail #501..#600 (skipped as `< T9`), permanently losing it — the exact loss #1895 fixes; `test_drain_mode_drains_backlog_across_runs` (run 2 must process the 100-item backlog) FAILS under it. A safe fold-in requires a boundary-invariant argument (older-than-boundary items provably indexed) that only a dedicated design can establish — hence a separate, mandatory fast-follow with its own review, landing immediately after #1895 in the same release.
- **Fetch-cost guard (new — plan-verify cycle 1):** A1 completes the cap-cut second's block before flushing, so when the cut lands on the walk's FINAL second the run fetches the entire remaining stream (production drain: ~19 pages/run — same order as today's frozen runs, which already re-walk the full stream; a FIRST run under today's code stops at the cap ~page 5). On a repo whose oldest active second is a mass-update (tens of thousands of issues), every DRAIN/DIFF poll re-walks the full stream — a bounded-cost follow-up (fetch-budget cap on the buffer) is warranted if such repos appear.
- **Legacy non-prefix holes already lost in production** (if any exist): unrecoverable via any composite cursor (the legacy cursor encodes no ranges — this is why A2's migration shim reduces to the same conservative reprocess A1 performs). A re-index-from-scratch (delete + full re-walk) is the only recovery path; not warranted unless a gap audit shows missing issues.
- **Mock `since` in Link next-URLs:** the mock does not carry `since` through pagination (real GitHub does); benign for all current and new tests (their since-windows cover the full fixture). Not changed.
