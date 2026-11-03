# Attestation census — open PRs

Repo `daniel-ospina/tortoise` · generated 2026-11-03T03:56:48Z by `tools/attest_census.py`.

> ⚠️ **SNAPSHOT, NOT A LIVE VERDICT.** This file is machine-generated from *one box's*
> registry at the timestamp above, and every push, review and gate re-run invalidates it.
> Re-run `uv run python tools/attest_census.py` before acting on any row — in particular,
> an `OK` or `NOTHING-TO-RESTORE` row from this snapshot is **not** evidence about the
> PR's current head, and `NOTHING-TO-RESTORE` never means "nobody reviewed this"
> fleet-wide: `~/.pi/agent/reviews/` is per-machine.

One row per open PR: its head, whether a review record exists on **this box**, whether that
record's head equals the PR's head, what the record claims, and what the `ai-review-gate`
check says. `RESTORE` is the only outcome that licenses re-posting a proof — and even then the
verdict is quoted from the record, never synthesised. A record at any other head is
`RE-REVIEW`: the code moved.

**RE-REVIEW=7 · NOTHING-TO-RESTORE=3 · IN-FLIGHT=9 · OK=17**

| PR | head | record | record head | match | mint | verdict | evidence | gate | outcome |
|---|---|---|---|---|---|---|---|---|---|
| #10694 | `538a63a3d3a6…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10693 | `4af1eaa5b82c…` | daniel-ospina-tortoise-10693.json | 4af1eaa5b82c… | yes | fresh | clean | comment:6594918768 | queued | **IN-FLIGHT** |
| #10692 | `9a92b7691f66…` | — | — | — | — | — | — | success | **OK** |
| #10691 | `9424a67afe9a…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10687 | `ad599da15b52…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10679 | `ad8bb281f5a8…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10678 | `c2a622ed120a…` | daniel-ospina-tortoise-10678.json | 3fa11b816462… | no | recovered-body | clean | null | queued | **RE-REVIEW** |
| #10677 | `f8842dc7e3f1…` | daniel-ospina-tortoise-10677.json | f8842dc7e3f1… | yes | fresh | clean | comment:6593568335 | success | **OK** |
| #10675 | `d6b3c700711e…` | — | — | — | — | — | — | success | **OK** |
| #10671 | `8ccb9fe8281a…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10668 | `1026a5db13e1…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10662 | `23a97d6bd29b…` | — | — | — | — | — | — | success | **OK** |
| #10648 | `6154f17b1d7f…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10641 | `5204e619a2d6…` | daniel-ospina-tortoise-10641.json | 9ce451ea8696… | no | fresh | clean | comment:6593270284 | queued | **RE-REVIEW** |
| #10640 | `48d36839537e…` | daniel-ospina-tortoise-10640.json | 85488a5d6758… | no | fresh | clean | comment:6593260793 | failure | **RE-REVIEW** |
| #10637 | `b47684855547…` | daniel-ospina-tortoise-10637.json | b47684855547… | yes | fresh | clean | comment:6593519692 | success | **OK** |
| #10636 | `efdffe13acef…` | daniel-ospina-tortoise-10636.json | 77cb30bab470… | no | fresh | clean | comment:6586658854 | success | **OK** |
| #10634 | `edfa92f17626…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10633 | `0f6f16ff619b…` | daniel-ospina-tortoise-10633.json | 0f6f16ff619b… | yes | fresh | clean | comment:6594267786 | success | **OK** |
| #10632 | `206f164c4f1b…` | daniel-ospina-tortoise-10632.json | 786110f38d96… | no | fresh | clean | comment:6587828183 | failure | **RE-REVIEW** |
| #10631 | `417930c57bd5…` | daniel-ospina-tortoise-10631.json | 417930c57bd5… | yes | fresh | clean | comment:6594326684 | success | **OK** |
| #10629 | `e6cdbf09c8f8…` | — | — | — | — | — | — | success | **OK** |
| #10627 | `9aa33df38636…` | daniel-ospina-tortoise-10627.json | 31d357be0338… | no | fresh | clean | comment:6594293382 | queued | **RE-REVIEW** |
| #10624 | `283f1cc58efa…` | daniel-ospina-tortoise-10624.json | f2603a2c3245… | no | recovered-body | clean | null | success | **OK** |
| #10622 | `cf918b59b344…` | — | — | — | — | — | — | success | **OK** |
| #10620 | `d272bc104710…` | daniel-ospina-tortoise-10620.json | 0b9033a2d1e1… | no | fresh | clean | comment:6594820640 | queued | **RE-REVIEW** |
| #10612 | `f4e856ca944b…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10610 | `8f80fb8224e7…` | daniel-ospina-tortoise-10610.json | 6c43ae62e2ab… | no | fresh | clean | comment:6593196579 | queued | **RE-REVIEW** |
| #10601 | `fdb6950eacfa…` | daniel-ospina-tortoise-10601.json | fdb6950eacfa… | yes | fresh | clean | comment:6593768046 | success | **OK** |
| #10575 | `cc7ed66ace9e…` | daniel-ospina-tortoise-10575.json | cc7ed66ace9e… | yes | fresh | clean | comment:6594336209 | success | **OK** |
| #10546 | `58dff75aca9b…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10543 | `c40edf0a7072…` | daniel-ospina-tortoise-10543.json | c40edf0a7072… | yes | fresh | clean | comment:6591960742 | success | **OK** |
| #10519 | `1ecdf368aa06…` | daniel-ospina-tortoise-10519.json | 1ecdf368aa06… | yes | fresh | clean | comment:6593589646 | success | **OK** |
| #10504 | `d57b07010ed8…` | daniel-ospina-tortoise-10504.json | d57b07010ed8… | yes | fresh | clean | comment:6595004413 | queued | **IN-FLIGHT** |
| #10452 | `ce432edbdeaf…` | daniel-ospina-tortoise-10452.json | ce432edbdeaf… | yes | fresh | clean | comment:6593607403 | success | **OK** |
| #10226 | `dde341d468dd…` | — | — | — | — | — | — | success | **OK** |

## What each flagged row needs

- **#10694** `fix/onboarding-credential-provenance` — IN-FLIGHT: gate has not concluded
- **#10693** `container-reaper-fix` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10691** `rail-durability` — NOTHING-TO-RESTORE: no record on this box at any head; the gate ran and found no head-bound published marker
- **#10687** `attest-repair` — IN-FLIGHT: gate has not concluded
- **#10679** `fix/7949-e4-receipt-corpus-pin` — IN-FLIGHT: gate has not concluded
- **#10678** `land/10137-census-pin` — RE-REVIEW: record binds 3fa11b816462…, PR head is c2a622ed120a… — the code moved
- **#10671** `fix-record-searchkeys` — IN-FLIGHT: gate has not concluded
- **#10668** `d11d12-gate-bytes` — IN-FLIGHT: gate has not concluded
- **#10648** `record-searchkeys` — IN-FLIGHT: gate has not concluded
- **#10641** `ci-zerojob` — RE-REVIEW: record binds 9ce451ea8696…, PR head is 5204e619a2d6… — the code moved
- **#10640** `quota-dims` — RE-REVIEW: record binds 85488a5d6758…, PR head is 48d36839537e… — the code moved
- **#10634** `issue-10597` — NOTHING-TO-RESTORE: no record on this box at any head; the gate ran and found no head-bound published marker
- **#10632** `issue-10540` — RE-REVIEW: record binds 786110f38d96…, PR head is 206f164c4f1b… — the code moved
- **#10627** `issue-10017` — RE-REVIEW: record binds 31d357be0338…, PR head is 9aa33df38636… — the code moved
- **#10620** `docs/8447-census-r2` — RE-REVIEW: record binds 0b9033a2d1e1…, PR head is d272bc104710… — the code moved
- **#10612** `fix/10602-read-lane` — NOTHING-TO-RESTORE: no record on this box at any head; the gate ran and found no head-bound published marker
- **#10610** `p0-measure` — RE-REVIEW: record binds 6c43ae62e2ab…, PR head is 8f80fb8224e7… — the code moved
- **#10546** `t-main-red-prereg` — IN-FLIGHT: gate has not concluded
- **#10504** `t-p0-10084` — IN-FLIGHT: record binds live head; gate has not concluded

## Reading rules

- A `RE-REVIEW` row is **not** restorable, however green the old verdict was: the reviewed artifact is gone.
- A `NOTHING-TO-RESTORE` row means exactly that. Never infer a review from a green test run.
- `mint:"carried"` is never evidence of a review of this head (#10177: a carry re-bound an older `clean`
  onto a head whose own review had found a P1).
- A record minted on **another box** is invisible here: `~/.pi/agent/reviews/` is per-machine. Rows are
  this box's view, and the census says so rather than implying a fleet-wide clean bill.
