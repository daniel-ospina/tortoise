# Attestation census — open PRs

Repo `daniel-ospina/tortoise` · generated 2026-11-03T03:00:31Z by `tools/attest_census.py`.

One row per open PR: its head, whether a review record exists on **this box**, whether that
record's head equals the PR's head, what the record claims, and what the `ai-review-gate`
check says. `RESTORE` is the only outcome that licenses re-posting a proof — and even then the
verdict is quoted from the record, never synthesised. A record at any other head is
`RE-REVIEW`: the code moved.

**RE-REVIEW=4 · NOTHING-TO-RESTORE=4 · IN-FLIGHT=9 · OK=16**

| PR | head | record | record head | match | mint | verdict | evidence | gate | outcome |
|---|---|---|---|---|---|---|---|---|---|
| #10687 | `eb9059b1ee9a…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10679 | `68c350b21084…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10678 | `167f76c18ca9…` | daniel-ospina-tortoise-10678.json | 3fa11b816462… | no | recovered-body | clean | null | queued | **RE-REVIEW** |
| #10677 | `f8842dc7e3f1…` | daniel-ospina-tortoise-10677.json | f8842dc7e3f1… | yes | fresh | clean | comment:6593568335 | success | **OK** |
| #10676 | `56b98805a7de…` | — | — | — | — | — | — | success | **OK** |
| #10675 | `d6b3c700711e…` | — | — | — | — | — | — | success | **OK** |
| #10671 | `3c1186317a5d…` | — | — | — | — | — | — | success | **OK** |
| #10668 | `7515dd36dd5f…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10662 | `23a97d6bd29b…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10648 | `b35124174f69…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10641 | `9ce451ea8696…` | daniel-ospina-tortoise-10641.json | 9ce451ea8696… | yes | fresh | clean | comment:6593270284 | success | **OK** |
| #10640 | `85488a5d6758…` | daniel-ospina-tortoise-10640.json | 85488a5d6758… | yes | fresh | clean | comment:6593260793 | success | **OK** |
| #10637 | `b47684855547…` | daniel-ospina-tortoise-10637.json | b47684855547… | yes | fresh | clean | comment:6593519692 | success | **OK** |
| #10636 | `efdffe13acef…` | daniel-ospina-tortoise-10636.json | 77cb30bab470… | no | fresh | clean | comment:6586658854 | success | **OK** |
| #10634 | `edfa92f17626…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10633 | `0f6f16ff619b…` | daniel-ospina-tortoise-10633.json | 0f6f16ff619b… | yes | fresh | clean | comment:6594267786 | queued | **IN-FLIGHT** |
| #10632 | `337827318ed4…` | daniel-ospina-tortoise-10632.json | 786110f38d96… | no | fresh | clean | comment:6587828183 | failure | **RE-REVIEW** |
| #10631 | `417930c57bd5…` | daniel-ospina-tortoise-10631.json | 8dd177f02e2b… | no | fresh | clean | comment:6587223062 | failure | **RE-REVIEW** |
| #10629 | `e6cdbf09c8f8…` | — | — | — | — | — | — | success | **OK** |
| #10627 | `31d357be0338…` | daniel-ospina-tortoise-10627.json | 31d357be0338… | yes | fresh | clean | comment:6594293382 | queued | **IN-FLIGHT** |
| #10624 | `283f1cc58efa…` | daniel-ospina-tortoise-10624.json | f2603a2c3245… | no | recovered-body | clean | null | success | **OK** |
| #10622 | `cf918b59b344…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10620 | `0b9033a2d1e1…` | daniel-ospina-tortoise-10620.json | 6c345d3b9ac6… | no | fresh | clean | comment:6589270622 | success | **OK** |
| #10612 | `f4e856ca944b…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10610 | `6c43ae62e2ab…` | daniel-ospina-tortoise-10610.json | 6c43ae62e2ab… | yes | fresh | clean | comment:6593196579 | success | **OK** |
| #10601 | `fdb6950eacfa…` | daniel-ospina-tortoise-10601.json | fdb6950eacfa… | yes | fresh | clean | comment:6593768046 | success | **OK** |
| #10575 | `cc7ed66ace9e…` | daniel-ospina-tortoise-10575.json | 4ceb2d3147c4… | no | fresh | clean | comment:6590423686 | failure | **RE-REVIEW** |
| #10546 | `ffcb672a6985…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10543 | `c40edf0a7072…` | daniel-ospina-tortoise-10543.json | c40edf0a7072… | yes | fresh | clean | comment:6591960742 | queued | **IN-FLIGHT** |
| #10519 | `1ecdf368aa06…` | daniel-ospina-tortoise-10519.json | 1ecdf368aa06… | yes | fresh | clean | comment:6593589646 | success | **OK** |
| #10504 | `780aff0e0a14…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10452 | `ce432edbdeaf…` | daniel-ospina-tortoise-10452.json | ce432edbdeaf… | yes | fresh | clean | comment:6593607403 | success | **OK** |
| #10226 | `dde341d468dd…` | — | — | — | — | — | — | success | **OK** |

## What each flagged row needs

- **#10687** `attest-repair` — IN-FLIGHT: gate has not concluded
- **#10679** `fix/7949-e4-receipt-corpus-pin` — IN-FLIGHT: gate has not concluded
- **#10678** `land/10137-census-pin` — RE-REVIEW: record binds 3fa11b816462…, PR head is 167f76c18ca9… — the code moved
- **#10668** `d11d12-gate-bytes` — NOTHING-TO-RESTORE: no record on this box at any head, and no head-bound published marker
- **#10662** `p0-9664` — IN-FLIGHT: gate has not concluded
- **#10648** `record-searchkeys` — NOTHING-TO-RESTORE: no record on this box at any head, and no head-bound published marker
- **#10634** `issue-10597` — NOTHING-TO-RESTORE: no record on this box at any head, and no head-bound published marker
- **#10633** `issue-5095` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10632** `issue-10540` — RE-REVIEW: record binds 786110f38d96…, PR head is 337827318ed4… — the code moved
- **#10631** `p1-8488a` — RE-REVIEW: record binds 8dd177f02e2b…, PR head is 417930c57bd5… — the code moved
- **#10627** `issue-10017` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10622** `fix/8447-c2-boundary-tests` — IN-FLIGHT: gate has not concluded
- **#10612** `fix/10602-read-lane` — IN-FLIGHT: gate has not concluded
- **#10575** `chore/ci-timing-refresh-6b8793351a` — RE-REVIEW: record binds 4ceb2d3147c4…, PR head is cc7ed66ace9e… — the code moved
- **#10546** `t-main-red-prereg` — IN-FLIGHT: gate has not concluded
- **#10543** `t-p1-10515` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10504** `t-p0-10084` — NOTHING-TO-RESTORE: no record on this box at any head, and no head-bound published marker

## Reading rules

- A `RE-REVIEW` row is **not** restorable, however green the old verdict was: the reviewed artifact is gone.
- A `NOTHING-TO-RESTORE` row means exactly that. Never infer a review from a green test run.
- `mint:"carried"` is never evidence of a review of this head (#10177: a carry re-bound an older `clean`
  onto a head whose own review had found a P1).
- A record minted on **another box** is invisible here: `~/.pi/agent/reviews/` is per-machine. Rows are
  this box's view, and the census says so rather than implying a fleet-wide clean bill.
