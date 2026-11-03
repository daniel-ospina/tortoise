# Attestation census — open PRs

Repo `daniel-ospina/tortoise` · generated 2026-11-03T04:53:03Z by `tools/attest_census.py`.

> ⚠️ **SNAPSHOT, NOT A LIVE VERDICT.** This file is machine-generated from *one box's*
> registry at the timestamp above, and every push, review and gate re-run invalidates it.
> Re-run `uv run python tools/attest_census.py` before acting on any row — in particular,
> an `OK` or `NOTHING-TO-RESTORE` row from this snapshot is **not** evidence about the
> PR's current head, and `NOTHING-TO-RESTORE` never means "nobody reviewed this"
> fleet-wide: `~/.pi/agent/reviews/` is per-machine.

One row per open PR: its head, whether a review record exists on **this box**, whether that
record's head equals the PR's head, what the record claims, and what the `ai-review-gate`
check says. `RESTORE` is the only outcome that licenses re-posting a proof — and even then the
verdict is quoted from the record, never synthesised. A record at any other head is not evidence of the live code: it is `RE-REVIEW` when the gate has no green verdict, and `OK`
 only because the gate itself already accepted the proof (so no re-post is owed).

**RE-REVIEW=4 · NOTHING-TO-RESTORE=4 · IN-FLIGHT=7 · OK=24**

| PR | head | record | record head | match | mint | verdict | evidence | gate | outcome |
|---|---|---|---|---|---|---|---|---|---|
| #10704 | `73255f170316…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10703 | `7214d25e013a…` | daniel-ospina-tortoise-10703.json | 7214d25e013a… | yes | fresh | clean | comment:6595267425 | success | **OK** |
| #10702 | `dbdc1a460490…` | daniel-ospina-tortoise-10702.json | dbdc1a460490… | yes | fresh | clean | comment:6595185528 | success | **OK** |
| #10694 | `e8407a2c68af…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10693 | `4af1eaa5b82c…` | daniel-ospina-tortoise-10693.json | 4af1eaa5b82c… | yes | fresh | clean | comment:6594918768 | success | **OK** |
| #10692 | `9a92b7691f66…` | — | — | — | — | — | — | success | **OK** |
| #10691 | `9424a67afe9a…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10687 | `27731921e407…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10679 | `ad8bb281f5a8…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10678 | `c1285d5639f9…` | daniel-ospina-tortoise-10678.json | 3fa11b816462… | no | recovered-body | clean | null | queued | **RE-REVIEW** |
| #10677 | `f8842dc7e3f1…` | daniel-ospina-tortoise-10677.json | f8842dc7e3f1… | yes | fresh | clean | comment:6593568335 | success | **OK** |
| #10675 | `c355f3698fae…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10671 | `8ccb9fe8281a…` | — | — | — | — | — | — | success | **OK** |
| #10668 | `16d5a7e2cf6c…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10662 | `23a97d6bd29b…` | — | — | — | — | — | — | success | **OK** |
| #10648 | `6154f17b1d7f…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10641 | `5204e619a2d6…` | daniel-ospina-tortoise-10641.json | 9ce451ea8696… | no | fresh | clean | comment:6593270284 | success | **OK** |
| #10640 | `48d36839537e…` | daniel-ospina-tortoise-10640.json | 85488a5d6758… | no | fresh | clean | comment:6593260793 | success | **OK** |
| #10637 | `b47684855547…` | daniel-ospina-tortoise-10637.json | b47684855547… | yes | fresh | clean | comment:6593519692 | success | **OK** |
| #10636 | `efdffe13acef…` | daniel-ospina-tortoise-10636.json | 77cb30bab470… | no | fresh | clean | comment:6586658854 | success | **OK** |
| #10634 | `8e1b492612b1…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10633 | `76f0237161e3…` | daniel-ospina-tortoise-10633.json | 76f0237161e3… | yes | fresh | clean | comment:6595312462 | success | **OK** |
| #10632 | `206f164c4f1b…` | daniel-ospina-tortoise-10632.json | 786110f38d96… | no | fresh | clean | comment:6587828183 | success | **OK** |
| #10631 | `417930c57bd5…` | daniel-ospina-tortoise-10631.json | 417930c57bd5… | yes | fresh | clean | comment:6594326684 | success | **OK** |
| #10629 | `e6cdbf09c8f8…` | — | — | — | — | — | — | success | **OK** |
| #10627 | `4155fb4bbc2b…` | daniel-ospina-tortoise-10627.json | 31d357be0338… | no | fresh | clean | comment:6594293382 | failure | **RE-REVIEW** |
| #10624 | `283f1cc58efa…` | daniel-ospina-tortoise-10624.json | f2603a2c3245… | no | recovered-body | clean | null | success | **OK** |
| #10622 | `cf918b59b344…` | — | — | — | — | — | — | success | **OK** |
| #10620 | `d272bc104710…` | daniel-ospina-tortoise-10620.json | d272bc104710… | yes | fresh | clean | comment:6595207708 | success | **OK** |
| #10612 | `f28f307a66d7…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10610 | `8f80fb8224e7…` | daniel-ospina-tortoise-10610.json | 6c43ae62e2ab… | no | fresh | clean | comment:6593196579 | success | **OK** |
| #10601 | `c49e069f2198…` | daniel-ospina-tortoise-10601.json | c49e069f2198… | yes | fresh | clean | comment:6595724200 | queued | **IN-FLIGHT** |
| #10575 | `990ad9ab62ff…` | daniel-ospina-tortoise-10575.json | cc7ed66ace9e… | no | fresh | clean | comment:6594336209 | queued | **RE-REVIEW** |
| #10546 | `58dff75aca9b…` | — | — | — | — | — | — | success | **OK** |
| #10543 | `c40edf0a7072…` | daniel-ospina-tortoise-10543.json | c40edf0a7072… | yes | fresh | clean | comment:6591960742 | success | **OK** |
| #10519 | `1ecdf368aa06…` | daniel-ospina-tortoise-10519.json | 1ecdf368aa06… | yes | fresh | clean | comment:6593589646 | success | **OK** |
| #10504 | `7e34e071ea49…` | daniel-ospina-tortoise-10504.json | d57b07010ed8… | no | fresh | clean | comment:6595004413 | queued | **RE-REVIEW** |
| #10452 | `ce432edbdeaf…` | daniel-ospina-tortoise-10452.json | ce432edbdeaf… | yes | fresh | clean | comment:6593607403 | success | **OK** |
| #10226 | `dde341d468dd…` | — | — | — | — | — | — | success | **OK** |

## What each flagged row needs

- **#10704** `batch/t2` — IN-FLIGHT: gate has not concluded
- **#10694** `fix/onboarding-credential-provenance` — IN-FLIGHT: gate has not concluded
- **#10691** `rail-durability` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10687** `attest-repair` — IN-FLIGHT: gate has not concluded
- **#10679** `fix/7949-e4-receipt-corpus-pin` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10678** `land/10137-census-pin` — RE-REVIEW: record binds 3fa11b816462…, PR head is c1285d5639f9… — the code moved
- **#10675** `p0-10000b` — IN-FLIGHT: gate has not concluded
- **#10668** `d11d12-gate-bytes` — IN-FLIGHT: gate has not concluded
- **#10648** `record-searchkeys` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10634** `issue-10597` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10627** `issue-10017` — RE-REVIEW: record binds 31d357be0338…, PR head is 4155fb4bbc2b… — the code moved
- **#10612** `fix/10602-read-lane` — IN-FLIGHT: gate has not concluded
- **#10601** `p0-10537b` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10575** `chore/ci-timing-refresh-6b8793351a` — RE-REVIEW: record binds cc7ed66ace9e…, PR head is 990ad9ab62ff… — the code moved
- **#10504** `t-p0-10084` — RE-REVIEW: record binds d57b07010ed8…, PR head is 7e34e071ea49… — the code moved

## Reading rules

- A `RE-REVIEW` row is **not** restorable, however green the old verdict was: the reviewed artifact is gone.
- A `NOTHING-TO-RESTORE` row means exactly that. Never infer a review from a green test run.
- `mint:"carried"` is never evidence of a review of this head (#10177: a carry re-bound an older `clean`
  onto a head whose own review had found a P1).
- A record minted on **another box** is invisible here: `~/.pi/agent/reviews/` is per-machine. Rows are
  this box's view, and the census says so rather than implying a fleet-wide clean bill.
