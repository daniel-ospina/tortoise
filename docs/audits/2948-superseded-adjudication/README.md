---
title: "PR #2948 — superseded / refuted by 37d5ef00c (PR #2958)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-projection, tortoise-ci-review-gate
created: 2026-09-21
---

# PR #2948 — superseded / refuted by `37d5ef00c` (PR #2958)

**Verdict: CLOSE.** Both halves of PR #2948's change are already resolved on `main` — one
**landed identically** (superseded), one **landed as the deliberate opposite** (refuted).
The branch is 337 commits behind `origin/main` and **`mergeStateStatus: DIRTY` / `CONFLICTING`**
on both files it edits.

- PR #2948 head: `9642057418e446d5b985e99b716fd877853dc817` (base `main`)
- `origin/main` at adjudication: `d9c62f1a9580f8d832f5a8dc38e82925a7bd26ed`
  (the packet's `798d331c5` was **stale** — main moved on)
- Merge-base of the branch: `f976d941d9bfce3ec747888e54d16ab9a52c455c`
- Branch delta vs merge-base: 4 files, +345 — `tests/test_projection.py`,
  `tortoise/projection/__init__.py`, `tortoise/projection/entities.py`, `tortoise/sdk.py`

## The landing commit

```
37d5ef00c  fix(projection): open-set Point writer preserves unrecognised props + recomputes
           content_hash (#2795, #2894) (#2958)     [PR #2958, MERGED 2026-09-17]
```

`37d5ef00c` is an **ancestor of `origin/main`** (verified with `git merge-base --is-ancestor`).
It touched the **same two source files** as #2948 — `tortoise/projection/__init__.py`,
`tortoise/projection/entities.py` — plus `tests/test_projection.py`.

PR #2948's own source comment had flagged the unreconciled sibling: *"sibling PR #2958
(fix/2795-replay-open-set) defines `_POINT_LIST_PROPS = frozenset()` which refuses the raw
`tags` list … the two PRs must be reconciled at merge time."* The reconciliation happened —
on `main`, in favour of #2958's position. #2948 was never rebased onto it.

## Half 1 — #2942 `content_hash` on `PointRevised`: SUPERSEDED (landed identically)

`main` recomputes `content_hash` in `_revise_point`, precisely what #2948 does:

| #2948 (branch) | `origin/main` |
|---|---|
| imports `content_hash` from `tortoise.ids` | `__init__.py:1143` `from tortoise.ids import content_hash as _content_hash` |
| `set_clauses.append("n.content_hash = $ch")`, `params["ch"] = content_hash(new_content)`, guarded by `if new_content is not None:` | `__init__.py:5119` `set_clauses.append("n.content_hash = $content_hash")`, `params["content_hash"] = _content_hash(new_content)` |
| `try/except` → NULL on non-str | same, per `#2958 review` comment |

`main`'s version is a **superset**: it adds `skip_hash` (the `#4042` delete→recreate content
boundary) and threads the derived value through a conditional write. Nothing in #2948's half 1
remains unlanded.

Regression test already on `main`: `tests/test_projection.py:678`
`test_falkor_rebuild_recomputes_content_hash` — **PASSES on `origin/main` with zero branch code**
(`raw/main-baseline.txt`).

## Half 2 — #2897 `tags` / `TAGGED`: REFUTED (main made the deliberate opposite decision)

`main` **deliberately refuses** exactly what #2948 lands:

- `entities.py:248` — *"`tags` … is DENIED on replay (its raw-list half-restore is refused —
  #2897) and the drop is reported by the undeclared-list warning below, NOT suppressed here."*
- `entities.py:271` — `_POINT_LIST_PROPS: frozenset = frozenset()` — the live/rebuild boundary is
  named explicitly: `sdk.create_point` writes raw `n.tags` live, while **replay drops it, and the
  drop is REPORTED, not silent.** The `#2897` gap was converted from silent to reported, on
  purpose.

`#2948` does the reverse (writes `n.tags`, reconstructs `:Tag` + `TAGGED`). The two positions are
**mutually exclusive**, and `main` has a test enforcing its side:

`tests/test_projection.py:740` `test_falkor_rebuild_undeclared_list_prop_denied` asserts
`row[0][1] is None` for `n.tags` after `rebuild_all`, **and** that a warning naming `tags` is
emitted.

### 2×2 measurement (embedded lane, `TORTOISE_TEST_CARVE_OUT=1`)

Both revisions run in the **same lane and same collection shape**; the branch's own tags test
(extracted from `964205741:tests/test_projection.py`) was added alongside `main`'s two, so one
invocation per revision covers the whole matrix.

| test | `origin/main` `d9c62f1a9` | `main` + branch #2948's tags code |
|---|---|---|
| `test_falkor_rebuild_recomputes_content_hash` (main's, #2942) | **PASS** | **PASS** |
| `test_falkor_rebuild_undeclared_list_prop_denied` (main's, #2897 refusal) | **PASS** | **FAIL** |
| `test_J6_branch2948_preserves_tags_and_tagged_edges` (branch's, #2897 restore) | **FAIL** | **PASS** |

The branch's test is defined at `964205741:tests/test_projection.py:2291` as
`test_rebuild_all_preserves_tags_and_tagged_edges`; it was extracted into the scratch tree under the
`test_J6_branch2948_` name used above so its provenance is explicit in the run output.

- `raw/main-baseline.txt` — `1 failed, 2 passed` (the failure is the **branch's** test; main's two pass)
- `raw/main-plus-branch2948-tags.txt` — `1 failed, 2 passed` (the failure is **main's** test:
  `AssertionError: [[None, ['alpha']]]` / `assert ['alpha'] is None`)

The branch's tags code was applied **verbatim** (the `n.tags` write in `_upsert_point_props`, the
`_sync_tag_edges` method, and its call in `_upsert_point_edges`), each marked with a
`[J6-2948 EXPERIMENT]` comment in the scratch tree only. **No repository file was weakened,
skipped, `xfail`ed or env-gated.**

**Conclusion:** merging #2948 would break a landed `main` test that encodes a recorded decision.
Closing is required; adopting over the decision is not an available route.

## Why `test (b)` was red — not this PR's defect

`gh pr checks 2948` red list at adjudication (verbatim):
`python-ci-gate` FAIL, `test (b)` FAIL. (`ai-review-gate` **PASS** — the packet's claim that it
was red is **stale**; a clean review is recorded at the current head `964205741`.)

`test (b)`'s single failure, from the job log:

```
= 1 failed, 5035 passed, 48 skipped, 2 deselected, 28 warnings in 2210.68s (0:36:50) =
FAILED tests/test_oauth_token_fault.py::test_exactly_one_capture_per_conversion_path[refresh-teams] - assert 0 == 1
```

**Classification: PRE-EXISTING on the branch's stale base — now MOOT on `main`.**

- The PR's delta **does not touch** `tests/test_oauth_token_fault.py` nor any OAuth/tenancy code
  (4 files, all projection/sdk — verified with `git diff --name-only`).
- The failing parameter **no longer exists on `main`**: the branch's base had
  `@pytest.mark.parametrize("table", ["oauth_clients", "teams"])`; `main` has
  `["oauth_clients", "organizations"]`, renamed by
  `ec79951bd` *"refactor(tenancy): the tenant is called org, not team (#3543) (#3622)"*.
- Re-run on `origin/main` in the same lane: **`4 passed`** (`raw/oauth-red-on-main.txt`).

So the one red test on the branch is a base-drift artifact of a param `main` has since renamed —
not introduced by #2948, and not the reason to close. The reason to close is the conflict +
refutation above.

## Merge conflict

`git merge-tree --write-tree origin/main 964205741` → **CONFLICT (content)** in BOTH
`tortoise/projection/__init__.py` and `tortoise/projection/entities.py` (exit 1). This is the
`mergeStateStatus: DIRTY` / `mergeable: CONFLICTING` the PR reports.

## How to close

Close PR #2948 as **superseded** — naming `37d5ef00c` (PR #2958) for half 1 and the same commit's
deliberate #2897 refusal (`entities.py:248,271`) for half 2. Do **not** resurrect and re-land:
half 1 is a duplicate, half 2 is a reversal of a recorded decision. The residual #2948 itself
filed — `#2946`, tag edits on `PointRevised` — is a separate event path and stays open on its own
merits; it is not this PR.
