# Attestation census — open PRs

Repo `daniel-ospina/tortoise` · generated 2026-11-03T16:04:01Z by `tools/attest_census.py` · content sha256 `1caa827f5ee44d6e3c3c6ee05e239fc6fa42417818bb2e26876c796462b329db`.

> ⚠️ **SNAPSHOT, NOT A LIVE VERDICT.** This file is machine-generated from *one box's*
> registry at the timestamp above, and every push, review and gate re-run invalidates it.
> Re-run `uv run python tools/attest_census.py` before acting on any row — in particular,
> an `OK` or `NOTHING-TO-RESTORE` row from this snapshot is **not** evidence about the
> PR's current head, and `NOTHING-TO-RESTORE` never means "nobody reviewed this"
> fleet-wide: `~/.pi/agent/reviews/` is per-machine.
>
> The header's `content sha256` is a digest of THIS FILE'S OWN BODY, so it proves the file
> came out of the renderer unedited (a hand edit — even prose — cannot verify). The row for
> the PR that regenerated this file names the head that PR had AT generation time, which is
> necessarily the parent of the commit adding the file: a document cannot contain the sha
> of the commit that contains it.

One row per open PR: its head, whether a review record exists on **this box**, whether that
record's head equals the PR's head, what the record claims, and what the `ai-review-gate`
check says. `RESTORE` is the only outcome that licenses re-posting a proof — and even then the
verdict is quoted from the record, never synthesised. A record at any other head is not
evidence of the live code: it is `RE-REVIEW` when the gate has no green verdict, and `OK`
only because the gate itself already accepted the proof (so no re-post is owed).

**Totals:** RESTORE=3 · RE-REVIEW=14 · NOTHING-TO-RESTORE=3 · IN-FLIGHT=2 · OK=22

| PR | head | record | record head | match | mint | verdict | evidence | gate | outcome |
|---|---|---|---|---|---|---|---|---|---|
| #10757 | `3ba42b83aac6…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10752 | `f06f0d68598b…` | daniel-ospina-tortoise-10752.json | f06f0d68598b… | yes | fresh | clean | comment:6607029272 | success | **OK** |
| #10744 | `5412c8f3afb3…` | — | — | — | — | — | — | success | **OK** |
| #10738 | `59380dc432f8…` | — | — | — | — | — | — | success | **OK** |
| #10737 | `e4ecba6146dc…` | — | — | — | — | — | — | success | **OK** |
| #10727 | `d9975de4f7a2…` | — | — | — | — | — | — | success | **OK** |
| #10726 | `42e97c7e5c99…` | — | — | — | — | — | — | success | **OK** |
| #10724 | `848e4525cf1c…` | daniel-ospina-tortoise-10724.json | ec4f93daa2d6… | no | carried | clean | null | success | **OK** |
| #10723 | `e7b7407097fd…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10717 | `baf951165881…` | daniel-ospina-tortoise-10717.json | 651d19f31526… | no | fresh | clean | comment:6605297517 | queued | **RE-REVIEW** |
| #10715 | `662e2c8f72ab…` | daniel-ospina-tortoise-10715.json | 662e2c8f72ab… | yes | fresh | clean | comment:6602266568 | failure | **RESTORE** |
| #10714 | `a2eba15dcdde…` | daniel-ospina-tortoise-10714.json | 9fde0b786ddc… | no | recovered-body | clean | null | success | **OK** |
| #10712 | `4fdc230a244b…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10704 | `2345056b5352…` | daniel-ospina-tortoise-10704.json | 2345056b5352… | yes | fresh | clean | comment:6608543660 | queued | **IN-FLIGHT** |
| #10703 | `f527315cc29f…` | daniel-ospina-tortoise-10703.json | 7214d25e013a… | no | recovered-body | clean | null | failure | **RE-REVIEW** |
| #10702 | `a3bbe78bca86…` | daniel-ospina-tortoise-10702.json | a3bbe78bca86… | yes | fresh | clean | comment:6606925238 | success | **OK** |
| #10694 | `e93a73cb5620…` | daniel-ospina-tortoise-10694.json | e93a73cb5620… | yes | fresh | clean | comment:6607921386 | failure | **RESTORE** |
| #10693 | `c4f05bd926cc…` | daniel-ospina-tortoise-10693.json | 4af1eaa5b82c… | no | recovered-body | clean | null | failure | **RE-REVIEW** |
| #10691 | `5bb455120bd4…` | daniel-ospina-tortoise-10691.json | 5bb455120bd4… | yes | fresh | clean | comment:6606561022 | failure | **RESTORE** |
| #10687 | `60913dff1ca7…` | daniel-ospina-tortoise-10687.json | 4ac0ee5e78eb… | no | recovered-body | clean | null | failure | **RE-REVIEW** |
| #10679 | `fad62ac45d4f…` | daniel-ospina-tortoise-10679.json | ba8c10302b96… | no | fresh | clean | comment:6598059106 | failure | **RE-REVIEW** |
| #10678 | `3e9eb43233cc…` | daniel-ospina-tortoise-10678.json | c1285d5639f9… | no | fresh | clean | comment:6595824573 | success | **OK** |
| #10677 | `8adea46c97ca…` | daniel-ospina-tortoise-10677.json | f8842dc7e3f1… | no | carried | clean | null | success | **OK** |
| #10675 | `d4e345795682…` | daniel-ospina-tortoise-10675.json | d6b3c700711e… | no | fresh | clean | comment:6593659187 | success | **OK** |
| #10668 | `3854a244f9b0…` | daniel-ospina-tortoise-10668.json | 3854a244f9b0… | yes | fresh | clean | comment:6605086848 | success | **OK** |
| #10648 | `9e9c3b6f4c54…` | daniel-ospina-tortoise-10648.json | b50fede18530… | no | fresh | clean | comment:6588866386 | failure | **RE-REVIEW** |
| #10641 | `0f281042e342…` | daniel-ospina-tortoise-10641.json | 5204e619a2d6… | no | fresh | clean | comment:6594889033 | failure | **RE-REVIEW** |
| #10640 | `71876bb06238…` | daniel-ospina-tortoise-10640.json | 48d36839537e… | no | fresh | clean | comment:6595090971 | success | **OK** |
| #10637 | `f6fba57a9b91…` | daniel-ospina-tortoise-10637.json | f6fba57a9b91… | yes | carried | clean | null | success | **OK** |
| #10634 | `4f4b116ffb51…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10633 | `61067feec1d3…` | daniel-ospina-tortoise-10633.json | 0f6f16ff619b… | no | fresh | clean | comment:6594343670 | queued | **RE-REVIEW** |
| #10632 | `6723e2be8409…` | daniel-ospina-tortoise-10632.json | 5009a3d6899a… | no | fresh | clean | comment:6604417193 | failure | **RE-REVIEW** |
| #10631 | `9c4225fabda6…` | daniel-ospina-tortoise-10631.json | b0af7efefc7e… | no | fresh | clean | comment:6589841326 | success | **OK** |
| #10629 | `53e7b6d362e2…` | daniel-ospina-tortoise-10629.json | 059f460bee99… | no | recovered-body | clean | null | success | **OK** |
| #10627 | `4a402318a2b0…` | daniel-ospina-tortoise-10627.json | 005504325cac… | no | fresh | clean | comment:6589941198 | failure | **RE-REVIEW** |
| #10622 | `399bc38c0375…` | daniel-ospina-tortoise-10622.json | 399bc38c0375… | yes | fresh | clean | comment:6597949761 | success | **OK** |
| #10620 | `8b7e2c25cd92…` | daniel-ospina-tortoise-10620.json | 0b9033a2d1e1… | no | recovered-body | clean | null | failure | **RE-REVIEW** |
| #10612 | `655659781a85…` | daniel-ospina-tortoise-10612.json | d3223a692675… | no | carried | clean | null | failure | **RE-REVIEW** |
| #10610 | `9aee307c823a…` | daniel-ospina-tortoise-10610.json | 8f80fb8224e7… | no | fresh | clean | comment:6594643840 | failure | **RE-REVIEW** |
| #10601 | `1d5f196ed017…` | daniel-ospina-tortoise-10601.json | fdb6950eacfa… | no | recovered-body | clean | null | failure | **RE-REVIEW** |
| #10575 | `7c8c827ea974…` | daniel-ospina-tortoise-10575.json | ad0a09e401aa… | no | fresh | clean | comment:6597361345 | success | **OK** |
| #10543 | `f0388cc8fb73…` | daniel-ospina-tortoise-10543.json | f16ca1cf6f0b… | no | fresh | clean | comment:6585478346 | success | **OK** |
| #10504 | `77dfa36bcc7a…` | daniel-ospina-tortoise-10504.json | 77dfa36bcc7a… | yes | carried | clean | null | success | **OK** |
| #10226 | `dde341d468dd…` | — | — | — | — | — | — | success | **OK** |

## What each flagged row needs

- **#10757** `fix/10487-consent-cold-start` — IN-FLIGHT: gate has not concluded
- **#10723** `p1-10046` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10717** `fold-2026-11-03` — RE-REVIEW: record binds 651d19f31526…, PR head is baf951165881… — the code moved
- **#10715** `suite-completes` — RESTORE: record binds the live head with clean + evidence comment:6602266568 — the artifact exists; re-post its own verdict verbatim
- **#10712** `container-reaper2` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10704** `batch/t2` — IN-FLIGHT: record binds live head; gate has not concluded
- **#10703** `batch-t3` — RE-REVIEW: record binds 7214d25e013a…, PR head is f527315cc29f… — the code moved
- **#10694** `fix/onboarding-credential-provenance` — RESTORE: record binds the live head with clean + evidence comment:6607921386 — the artifact exists; re-post its own verdict verbatim
- **#10693** `container-reaper-fix` — RE-REVIEW: record binds 4af1eaa5b82c…, PR head is c4f05bd926cc… — the code moved
- **#10691** `rail-durability` — RESTORE: record binds the live head with clean + evidence comment:6606561022 — the artifact exists; re-post its own verdict verbatim
- **#10687** `attest-repair` — RE-REVIEW: record binds 4ac0ee5e78eb…, PR head is 60913dff1ca7… — the code moved
- **#10679** `fix/7949-e4-receipt-corpus-pin` — RE-REVIEW: record binds ba8c10302b96…, PR head is fad62ac45d4f… — the code moved
- **#10648** `record-searchkeys` — RE-REVIEW: record binds b50fede18530…, PR head is 9e9c3b6f4c54… — the code moved
- **#10641** `ci-zerojob` — RE-REVIEW: record binds 5204e619a2d6…, PR head is 0f281042e342… — the code moved
- **#10634** `issue-10597` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10633** `issue-5095` — RE-REVIEW: record binds 0f6f16ff619b…, PR head is 61067feec1d3… — the code moved
- **#10632** `issue-10540` — RE-REVIEW: record binds 5009a3d6899a…, PR head is 6723e2be8409… — the code moved
- **#10627** `issue-10017` — RE-REVIEW: record binds 005504325cac…, PR head is 4a402318a2b0… — the code moved
- **#10620** `docs/8447-census-r2` — RE-REVIEW: record binds 0b9033a2d1e1…, PR head is 8b7e2c25cd92… — the code moved
- **#10612** `fix/10602-read-lane` — RE-REVIEW: record binds d3223a692675…, PR head is 655659781a85… — the code moved
- **#10610** `p0-measure` — RE-REVIEW: record binds 8f80fb8224e7…, PR head is 9aee307c823a… — the code moved
- **#10601** `p0-10537b` — RE-REVIEW: record binds fdb6950eacfa…, PR head is 1d5f196ed017… — the code moved

## Reading rules

- A `RE-REVIEW` row is **not** restorable, however green the old verdict was: the reviewed artifact is gone.
- A `NOTHING-TO-RESTORE` row means exactly that. Never infer a review from a green test run.
- `mint:"carried"` is never evidence of a review of this head (#10177: a carry re-bound an older `clean`
  onto a head whose own review had found a P1).
- A record minted on **another box** is invisible here: `~/.pi/agent/reviews/` is per-machine. Rows are
  this box's view, and the census says so rather than implying a fleet-wide clean bill.
