# Attestation census — open PRs

Repo `daniel-ospina/tortoise` · generated 2026-11-03T03:11:42Z by `tools/attest_census.py`.

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

**RE-REVIEW=3 · NOTHING-TO-RESTORE=5 · IN-FLIGHT=12 · OK=13**

| PR | head | record | record head | match | mint | verdict | evidence | gate | outcome |
|---|---|---|---|---|---|---|---|---|---|
| #10691 | `18196ec44c7b…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10687 | `cef17511de41…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10679 | `68c350b21084…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10678 | `167f76c18ca9…` | daniel-ospina-tortoise-10678.json | 3fa11b816462… | no | recovered-body | clean | null | queued | **RE-REVIEW** |
| #10677 | `f8842dc7e3f1…` | daniel-ospina-tortoise-10677.json | f8842dc7e3f1… | yes | fresh | clean | comment:6593568335 | success | **OK** |
| #10675 | `d6b3c700711e…` | — | — | — | — | — | — | success | **OK** |
| #10671 | `9230ad5e45df…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
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
| #10631 | `417930c57bd5…` | daniel-ospina-tortoise-10631.json | 417930c57bd5… | yes | fresh | clean | comment:6594326684 | queued | **IN-FLIGHT** |
| #10629 | `e6cdbf09c8f8…` | — | — | — | — | — | — | success | **OK** |
| #10627 | `31d357be0338…` | daniel-ospina-tortoise-10627.json | 31d357be0338… | yes | fresh | clean | comment:6594293382 | queued | **IN-FLIGHT** |
| #10624 | `283f1cc58efa…` | daniel-ospina-tortoise-10624.json | f2603a2c3245… | no | recovered-body | clean | null | success | **OK** |
| #10622 | `cf918b59b344…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10620 | `0b9033a2d1e1…` | daniel-ospina-tortoise-10620.json | 6c345d3b9ac6… | no | fresh | clean | comment:6589270622 | success | **OK** |
| #10612 | `f4e856ca944b…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10610 | `8f80fb8224e7…` | daniel-ospina-tortoise-10610.json | 6c43ae62e2ab… | no | fresh | clean | comment:6593196579 | queued | **RE-REVIEW** |
| #10601 | `fdb6950eacfa…` | daniel-ospina-tortoise-10601.json | fdb6950eacfa… | yes | fresh | clean | comment:6593768046 | success | **OK** |
| #10575 | `cc7ed66ace9e…` | daniel-ospina-tortoise-10575.json | cc7ed66ace9e… | yes | fresh | clean | comment:6594336209 | queued | **IN-FLIGHT** |
| #10546 | `ffcb672a6985…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10543 | `c40edf0a7072…` | daniel-ospina-tortoise-10543.json | c40edf0a7072… | yes | fresh | clean | comment:6591960742 | queued | **IN-FLIGHT** |
| #10519 | `1ecdf368aa06…` | daniel-ospina-tortoise-10519.json | 1ecdf368aa06… | yes | fresh | clean | comment:6593589646 | success | **OK** |
| #10504 | `ea43be91b955…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10452 | `ce432edbdeaf…` | daniel-ospina-tortoise-10452.json | ce432edbdeaf… | yes | fresh | clean | comment:6593607403 | success | **OK** |
| #10226 | `dde341d468dd…` | — | — | — | — | — | — | success | **OK** |

## What each flagged row needs

- **#10691** `rail-durability` — IN-FLIGHT: gate has not concluded
- **#10687** `attest-repair` — IN-FLIGHT: gate has not concluded
- **#10679** `fix/7949-e4-receipt-corpus-pin` — NOTHING-TO-RESTORE: no record on this box at any head; the gate ran and found no head-bound published marker
- **#10678** `land/10137-census-pin` — RE-REVIEW: record binds 3fa11b816462…, PR head is 167f76c18ca9… — the code moved
- **#10671** `fix-record-searchkeys` — IN-FLIGHT: gate has not concluded
- **#10668** `d11d12-gate-bytes` — NOTHING-TO-RESTORE: no record on this box at any head; the gate ran and found no head-bound published marker
- **#10662** `p0-9664` — IN-FLIGHT: gate has not concluded
- **#10648** `record-searchkeys` — NOTHING-TO-RESTORE: no record on this box at any head; the gate ran and found no head-bound published marker
- **#10634** `issue-10597` — NOTHING-TO-RESTORE: no record on this box at any head; the gate ran and found no head-bound published marker
- **#10633** `issue-5095` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10632** `issue-10540` — RE-REVIEW: record binds 786110f38d96…, PR head is 337827318ed4… — the code moved
- **#10631** `p1-8488a` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10627** `issue-10017` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10622** `fix/8447-c2-boundary-tests` — IN-FLIGHT: gate has not concluded
- **#10612** `fix/10602-read-lane` — IN-FLIGHT: gate has not concluded
- **#10610** `p0-measure` — RE-REVIEW: record binds 6c43ae62e2ab…, PR head is 8f80fb8224e7… — the code moved
- **#10575** `chore/ci-timing-refresh-6b8793351a` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10546** `t-main-red-prereg` — NOTHING-TO-RESTORE: no record on this box at any head; the gate ran and found no head-bound published marker
- **#10543** `t-p1-10515` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10504** `t-p0-10084` — IN-FLIGHT: gate has not concluded

## Reading rules

- A `RE-REVIEW` row is **not** restorable, however green the old verdict was: the reviewed artifact is gone.
- A `NOTHING-TO-RESTORE` row means exactly that. Never infer a review from a green test run.
- `mint:"carried"` is never evidence of a review of this head (#10177: a carry re-bound an older `clean`
  onto a head whose own review had found a P1).
- A record minted on **another box** is invisible here: `~/.pi/agent/reviews/` is per-machine. Rows are
  this box's view, and the census says so rather than implying a fleet-wide clean bill.
