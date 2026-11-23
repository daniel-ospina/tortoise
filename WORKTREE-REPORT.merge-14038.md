# WORKTREE-REPORT (merge lane, #14038) — deliberately a SIBLING file

⛔ **Why this is not `WORKTREE-REPORT.md`**: that file **already exists** (62,373 bytes, written
2026-11-23 18:27:31Z) and is owned by a **live** peer lane in this same worktree
(`prd-daniel-ospina-tortoise-14038` / `lane-bound.sh … for PR #14038`, log mtimes ≤ 90 s old throughout
this run). It carries a `report-digest:` footer over its whole body and `tools/worktree_report.py check`
parses it. Overwriting it would destroy a live lane's artifact; appending by hand would break its digest.
So this report is filed beside it, and nothing in the peer's file was touched.

## Worktree and branch

- worktree: `/home/cmux/tortoise/.worktrees/fix-14038`
- repo: `daniel-ospina/tortoise`
- branch: `feat/13998-threshold-emails`
- head at the time of these measurements: **`70a6b1d5b9d32bfe589f7d69a8be8a46244cacc8`**
- `origin/feat/13998-threshold-emails` == same sha (pushed by the peer lane at 18:32Z)
- working tree: **clean** (`git status --short` → 0 lines)

## The brief this lane was dispatched with is STALE — measured, not assumed

The brief said: *a `git merge --no-ff --no-commit origin/main` is IN PROGRESS with exactly 3 conflicted
files (15 hunks): `00-align.md` (1), `05-verify.md` (3), `tests/test_7949_capstone_measure.py` (11)*,
against `MERGE_HEAD` `a7baa98d053d` on head `cc327844ddd6`.

Measurement at start of this run:

```
$ git status --short | grep -E '^(UU|AA|DU|UD|AU|UA)'
(no output)
$ cat "$(git rev-parse --absolute-git-dir)/MERGE_HEAD"
(no file)
$ git rev-parse HEAD
023cd3eae45c9214e20291697f48ea6e3c214e5b      # brief said cc327844ddd6
```

The 15-hunk merge was **already resolved and committed** by a peer lane, and the branch has since moved
on. Reconstructed read-only with `git merge-tree d2cd8ab06e cc327844 a7baa98d` (base = the brief's
merge base), which reproduces **exactly** the 15 hunks the brief enumerated:

```
docs/epics/…/00-align.md            @@ -187,7 +187,13 @@          1 hunk
docs/epics/…/05-verify.md           @@ -99,19 +99,31 @@
                                    @@ -131,9 +143,15 @@
                                    @@ -142,7 +160,7 @@          3 hunks
tests/test_7949_capstone_measure.py (11 hunks)                     11 hunks
```

Landing path in history (all ancestors of the current head, verified with
`git merge-base --is-ancestor <c> HEAD` → rc 0 for each):

| commit | what it did |
|---|---|
| `225b31737021` / `6367d9cce43c` | `fold origin/main a7baa98d05 — three conflicts, each resolved by measurement` |
| `98d1d37477dc` | `restore the I7 row-derived guard the fold dropped, and re-measure the CI matrix figure` |
| `023cd3eae45c` | `fold origin/main f5fc7ca52c` (3 different conflicts: `ci-wall-clock.md` ×2, `ci_check_repair.py`, `test_ci_check_repair.py`) |
| `70a6b1d5b9d3` | `re-cut the comparability split's main-absent figure to 0` |

## Files changed by THIS lane

**None.** Nothing was committed, nothing was pushed, no file was edited:

- the merge the brief asked me to complete is already committed **and pushed**, so there was nothing to
  commit — an empty commit would be a fabricated artifact, not a completion;
- the tree went from dirty (peer's in-flight edits) to clean under me; committing during that window
  would have swept another lane's uncommitted work into my commit;
- pushing had nothing to push (`HEAD == origin/feat/13998-threshold-emails`).

## Suites run (verbatim command → measured result)

Plain `pytest` on this box fails on the DB gate, not on code:
`444 E  Failed: default pytest requires TORTOISE_DB_URI (epic #1647 P4); run the carve-out with
TORTOISE_TEST_CARVE_OUT=1`. The carve-out invocation is what was used:

| # | command (verbatim) | result |
|---|---|---|
| 1 | `TORTOISE_TEST_CARVE_OUT=1 timeout 900 .venv/bin/python -m pytest tests/test_7949_capstone_measure.py -q -p no:cacheprovider` | **444 passed**, 0 failed, 0 skipped (48.87 s) |
| 2 | `TORTOISE_TEST_CARVE_OUT=1 timeout 1200 .venv/bin/python -m pytest tests/test_12184_shifted_cites.py -q -p no:cacheprovider` | **43 passed**, 0 failed, 0 skipped (78.00 s) |
| 3 | `TORTOISE_TEST_CARVE_OUT=1 timeout 1500 .venv/bin/python -m pytest tests/test_docs_citation_refs.py tests/test_13998_capacity_notice.py -q -p no:cacheprovider` | **181 passed**, 0 failed, 0 skipped (45.90 s) |
| 4 | `.venv/bin/python -m ruff check tests/test_7949_capstone_measure.py` | `All checks passed!` |

The brief's "14 failed" baseline for `test_12184_shifted_cites.py` is gone: the fold carried main's
`#14326`, and it now reads **43 passed / 0 failed**.

## The 15 hunks — resolution and the measurement that makes it true

Every value below was re-derived **at the current resolved tree**, not copied from either side.

**`docs/epics/2026-11-04-7949-extractor-v4-capstone/00-align.md`**

1. `A-d` row (ours `sdk.py:19441-19442` + `hosted_api.py:19842`/`:22718`; theirs `sdk.py:19442-19443` +
   `hosted_api.py:19728`/`:22604`) → landed as the MEASURED HYBRID: `sdk.py:19442-19443`,
   `hosted_api.py:19842`, `hosted_api.py:22718`, `tortoise-capture.ts:1030` (+`:1022`), all four verified
   by `grep -n 'The v2 path reports' tortoise/sdk.py` → `19442`, `grep -n '"extraction_mode": "llm:<route>"'`
   → `19443`, `grep -n '@app.post("/v1/sessions")' tortoise/hosted_api.py` → `19842`,
   `grep -n '@app.post("/v1/sessions/install-probe")'` → `22718`,
   `grep -n 'export function postCapture\|postInstallProbe'` → `1030`/`1022`. Main's `19728`/`22604` are
   true of main's tree (this branch inserts 114 lines above them), so neither side was right wholesale.

**`docs/epics/2026-11-04-7949-extractor-v4-capstone/05-verify.md`**

2. hunk 1 — the `D5` row. Disputed: `sdk.py:29121` (ours) vs `:29150` (theirs); "the I7 range IS a
   re-point of THIS diff (+114)" (ours) vs "AND AT THIS HEAD `04-plan.md` IS NOT IN THIS DIFF … numstat
   is EMPTY" (theirs); receipt/filter cites `:22073`/`:28581` (ours) vs `:22074`/`:28582` (theirs).
   → landed: `:29150` (`grep -nF 'declared = declared_relation_predicates()' tortoise/sdk.py` → `29150`),
   `:22074` (`grep -n '"stats": _capture_stats_with_step_witness' tortoise/sdk.py` → `22074`) and
   `:28582` (`grep -n 'dedup_reviewed IS NULL' tortoise/sdk.py` → `28582`) — **theirs on all three** —
   plus a membership sentence bound to a NAMED head and its PAIR (ours' construction): the named head
   `cc327844ddd6…` does carry the target
   (`git show cc32784…:…/04-plan.md | grep -c 'hosted_api.py:15563-15640'` → `1`), this head's copy
   carries it (`grep -c` → `1`), main's copy does not (`git show origin/main:…/04-plan.md | grep -c` → `0`),
   and the live membership is `git diff --numstat origin/main...HEAD -- …/04-plan.md` → **`1 1`** (non-empty,
   so "IS in this diff" is the true spelling here). Both sides' *typed* numstat was dropped in favour of
   the command, which is the row's own rule.
3. hunk 2 — the `docs/00_index.md` paragraph. Disputed: ours' typed history values `19132`/`19140` and
   main's `#14290` "restored the pin numbering, so the two rows read `sdk.py:22074`/`sdk.py:28582` at BOTH
   heads" narrative. → landed: **no head-bound figure typed** —
   `grep -c '19132\|19140' …/05-verify.md` → `0`, and the membership is the command:
   `git diff --numstat origin/main...HEAD -- docs/00_index.md` → **`2 2`** (so no "not in the diff" claim
   could be typed either).
4. hunk 3 — the seven-document table's `docs/00_index.md` row. Disputed: the receipt-line cite's number.
   → landed: **no line number typed for that cite at all** ("NO LINE NUMBER IS TYPED FOR IT HERE"), which
   is measurement-compelled: `grep -c '_capture_stats_with_step_witness' docs/00_index.md` → `0`, so any
   typed `sdk.py:NNNNN` there would be unverifiable. The sweep `tests/test_docs_citation_refs.py` is green
   (181 passed with its sibling, row 3 above).

**`tests/test_7949_capstone_measure.py`**

hunks 1, 2, 3, 4, 6 and 11 (`278` vs `292`) — the `D5` line's `:N`-token count. Disputed `278`
   (ours) vs `292` (theirs) → landed **`295`**, MEASURED, neither side: the `D5` row is one markdown line
   of 66,421 chars and carries **295** cite-shaped tokens
   (`len(re.findall(r":\d{2,}", <D5 row>))` → `295`); the file's own census
   (`_D5_LINE_COUNT_PHRASES` → `_typed_comment_counts == {_measured_count}`) reds on any disagreement and
   reads 295 in all six spellings (`grep -o '[0-9]\+ cite-shaped|moving the line off `…`|line that carries … of'`)
   — so the figure is graded, not typed-and-trusted.
hunk 5 — main ADDS 34 lines: the `#14183` re-pointed-pair / arithmetic-identity arms. → main's side
   taken, which is **RESTORATION, not weakening** (a prior fold had dropped them): `_bad_arithmetic`,
   `_bad_diffs`, `_bad_subtractands` each present (3 occurrences apiece).
hunk 7 — main ADDS the `#14260` named-head entry list (4 needles). → main's side taken (restoration):
   all four needles present (`subject_binding_available`, `session entity-linking failed`,
   `whose payload points ALL fold as PARAPHRASE noops`, `if meta.get("route"):`).
hunk 8 — the `D5` provenance narrative (`:29121` vs `:29150` vs `:28935`). → landed on the MEASURED
   value `29150` with `→ 28935 →` kept as provenance of the base; both strings are asserted by the file and
   it is green.
hunk 9 — the provenance assertion pair (`sdk.py:29121` vs `:29150`) → landed `sdk.py:29150`, measured
   `29150` by the grep above; the refusal of `29121` is retained.
hunk 10 — the CHIMERA range `:15262-15339 → :15346-15423`. → landed as a REFUSAL arm (regex over both
   arrow spellings) plus a positive pin on the real pair. The refusal holds by measurement: the CHIMERA
   range occurs in **neither** copy — `grep -c '15346-15423' …/04-plan.md` → `0` at HEAD and → `0` at
   `origin/main` (`git show origin/main:… | grep -c`), while the real pair `15563-15640` is present at HEAD
   (`1`) and `15449-15526` is absent from current `origin/main` (`0`).

## What I could not do, and the residual I measured but did not fix

- **No commit, no push** — both were already done by the peer lane (head `70a6b1d5b9d3` is on `origin`).
  Creating a commit here would have been an empty ceremony; committing earlier in the run would have
  captured another live lane's uncommitted work.
- **`WORKTREE-REPORT.md` not rewritten** — a live peer lane owns it (see the header). This sibling file is
  the durable evidence instead.
- **MEASURED RESIDUAL, NOT FIXED (prose staleness, outside the merge):** `05-verify.md`'s `D5` paragraph
  states `main`'s copy carries the I7 range `hosted_api.py:15449-15526`. That was **true at the fold base**
  (`git show a7baa98d05:…/04-plan.md | grep -c '15449-15526'` → `1`; `point_count` at `:15449`) and at
  `f5fc7ca52c` (`1`, `:15449`), but `origin/main` is now `9aaaff10acd9`, where that row reads `0` and
  `point_count` sits at `:15826`. The test's arm is insensitive to this by design — it grades only that the
  base does **not** already carry the HEAD target (`_I7_TARGET`), which still holds — so the staleness is
  unred. It is prose about a historical base, not a false current claim, and it is a peer lane's file; I
  report it rather than edit under a live lane.
