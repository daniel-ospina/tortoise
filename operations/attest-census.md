# Attestation census — open PRs

Repo `daniel-ospina/tortoise` · generated 2026-11-04T01:10:03Z by `tools/attest_census.py` · content sha256 `0ea61c34830b73d967278919edbc7674ef4341ed650134399d8efc2f51e323ed`.

> ⚠️ **SNAPSHOT, NOT A LIVE VERDICT.** This file is machine-generated from *one box's*
> registry at the timestamp above, and every push, review and gate re-run invalidates it.
> Re-run `uv run python tools/attest_census.py` before acting on any row — in particular,
> an `OK` or `NOTHING-TO-RESTORE` row from this snapshot is **not** evidence about the
> PR's current head, and `NOTHING-TO-RESTORE` never means "nobody reviewed this"
> fleet-wide: `~/.pi/agent/reviews/` is per-machine.
>
> The header's `content sha256` is a digest of THIS FILE'S OWN BODY, so an edit that does not
> also recompute it cannot pass — the ACCIDENTAL edit (the hand reflow that produced this
> check, a sentence left stale by a later change) is caught offline, without `gh`. ⛔ It is
> **not a provenance proof**: the digest is unkeyed and its algorithm is in this repo, so an
> editor who recomputes it after editing produces a file that verifies. Read a matching digest
> as "nobody edited this by accident", never as "the tool emitted these rows". The row for
> the PR that regenerated this file names the head that PR had AT generation time. That is NOT
> necessarily the parent of the commit adding the file (measured #10687 cycle 18: a worktree one
> merge behind the remote tip records the REMOTE head, so the row named an ancestor's sibling).
> What holds by construction is narrower and is what matters: a document cannot contain the sha
> of the commit that contains it.

One row per open PR: its head, whether a review record exists on **this box**, whether that
record's head equals the PR's head, what the record claims, and what the `ai-review-gate`
check says. `RESTORE` is the only outcome that licenses re-posting a proof — and even then the
verdict is quoted from the record, never synthesised. A record at any other head is not
evidence of the live code: it is `RE-REVIEW` when the gate has no green verdict, and `OK`
only because the gate itself already accepted the proof (so no re-post is owed).

**Totals:** RE-REVIEW=3 · NOTHING-TO-RESTORE=12 · IN-FLIGHT=16 · OK=17

| PR | head | record | record head | match | mint | verdict | evidence | gate | outcome |
|---|---|---|---|---|---|---|---|---|---|
| #10829 | `b7e76de9bada…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10823 | `182ced166430…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10813 | `9794d0d4f6dc…` | — | — | — | — | — | — | success | **OK** |
| #10811 | `8dfdbd63eea5…` | — | — | — | — | — | — | success | **OK** |
| #10807 | `9c56f14b5899…` | — | — | — | — | — | — | success | **OK** |
| #10804 | `e85f1ad44153…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10802 | `dd08c9174d98…` | — | — | — | — | — | — | success | **OK** |
| #10801 | `c0b52778f71b…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10800 | `0230c05c3f59…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10797 | `ee2a1589195d…` | — | — | — | — | — | — | success | **OK** |
| #10792 | `1c24f1e052f5…` | — | — | — | — | — | — | success | **OK** |
| #10791 | `0563a2bf3a23…` | — | — | — | — | — | — | success | **OK** |
| #10790 | `b9a69939d026…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10788 | `9c3e95a3acc4…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10783 | `171adf8129ca…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10781 | `1a9445e3682b…` | — | — | — | — | — | — | success | **OK** |
| #10777 | `c470f4be0902…` | — | — | — | — | — | — | success | **OK** |
| #10771 | `0eaa4eb27069…` | — | — | — | — | — | — | success | **OK** |
| #10762 | `ab8e4cccb2f4…` | — | — | — | — | — | — | success | **OK** |
| #10757 | `76bc46b1a541…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10737 | `b5bb01db2f45…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10727 | `0f96c1e4b730…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10726 | `69f4c802de4c…` | — | — | — | — | — | — | success | **OK** |
| #10724 | `39412ce14ebc…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10723 | `9fe385d09c69…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10714 | `5f588df249b9…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10712 | `3f68e35d5674…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10704 | `63402d2eb033…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10703 | `7ac24e47ac36…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10702 | `d9ae2df3261b…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10694 | `c2a8027cde73…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10693 | `548a5f727430…` | daniel-ospina-tortoise-10693.json | 548a5f727430… | yes | fresh | clean | comment:6616736554 | success | **OK** |
| #10691 | `e4806ecd4641…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10687 | `aa057ff56593…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10679 | `2b38365c9fec…` | — | — | — | — | — | — | success | **OK** |
| #10675 | `2dd306dcf9a5…` | — | — | — | — | — | — | success | **OK** |
| #10668 | `9640104b2fbe…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10648 | `011ba78b19fd…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10641 | `70e9a84ae046…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10634 | `08cba56a61d1…` | daniel-ospina-tortoise-10634.json | 08cba56a61d1… | yes | fresh | clean | comment:6617103700 | success | **OK** |
| #10632 | `db2da9ee5458…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10622 | `d4017bc88e4d…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10620 | `180d72da3eeb…` | daniel-ospina-tortoise-10620.json | a5b75cafa163… | no | fresh | clean | comment:6616810994 | queued | **RE-REVIEW** |
| #10610 | `85a2cae89220…` | — | — | — | — | — | — | queued | **IN-FLIGHT** |
| #10601 | `daece75b1768…` | daniel-ospina-tortoise-10601.json | a9fcc6b52d21… | no | fresh | clean | comment:6585488852 | queued | **RE-REVIEW** |
| #10575 | `9965d6911da3…` | daniel-ospina-tortoise-10575.json | 7c8c827ea974… | no | fresh | clean | comment:6607557535 | queued | **RE-REVIEW** |
| #10543 | `d8b03a969d17…` | — | — | — | — | — | — | failure | **NOTHING-TO-RESTORE** |
| #10226 | `dde341d468dd…` | — | — | — | — | — | — | success | **OK** |

## What each flagged row needs

- **#10829** `batch-9` — IN-FLIGHT: gate has not concluded
- **#10823** `p0-10137-belief` — IN-FLIGHT: gate has not concluded
- **#10804** `p1-10360d` — IN-FLIGHT: gate has not concluded
- **#10801** `p1-10360` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10800** `p1-10538` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10790** `batch-2026-11-03` — IN-FLIGHT: gate has not concluded
- **#10788** `p1-10368` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10783** `p1-9736` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10757** `fix/10487-consent-cold-start` — IN-FLIGHT: gate has not concluded
- **#10737** `p1-10626-r4` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10727** `p1-10605` — IN-FLIGHT: gate has not concluded
- **#10724** `fix/8906-points-gate-counts-write-graph` — IN-FLIGHT: gate has not concluded
- **#10723** `p1-10046` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10714** `fix/10602-graph-wait-budget` — IN-FLIGHT: gate has not concluded
- **#10712** `container-reaper2` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10704** `batch/t2` — IN-FLIGHT: gate has not concluded
- **#10703** `batch-t3` — IN-FLIGHT: gate has not concluded
- **#10702** `batch/t1` — IN-FLIGHT: gate has not concluded
- **#10694** `fix/onboarding-credential-provenance` — IN-FLIGHT: gate has not concluded
- **#10691** `rail-durability` — IN-FLIGHT: gate has not concluded
- **#10687** `attest-repair` — IN-FLIGHT: gate has not concluded
- **#10668** `d11d12-gate-bytes` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10648** `record-searchkeys` — IN-FLIGHT: gate has not concluded
- **#10641** `ci-zerojob` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10632** `issue-10540` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10622** `fix/8447-c2-boundary-tests` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census
- **#10620** `docs/8447-census-r2` — RE-REVIEW: record binds a5b75cafa163…, PR head is 180d72da3eeb… — the code moved
- **#10610** `p0-measure` — IN-FLIGHT: gate has not concluded
- **#10601** `p0-10537b` — RE-REVIEW: record binds a9fcc6b52d21…, PR head is daece75b1768… — the code moved
- **#10575** `chore/ci-timing-refresh-6b8793351a` — RE-REVIEW: record binds 7c8c827ea974…, PR head is 9965d6911da3… — the code moved
- **#10543** `t-p1-10515` — NOTHING-TO-RESTORE: no record on this box at any head; the gate concluded failure — whether a published marker exists is answered by the gate's own annotation, not by this census

## Reading rules

- A `RE-REVIEW` row is **not** restorable, however green the old verdict was: the reviewed artifact is gone.
- A `NOTHING-TO-RESTORE` row means exactly that. Never infer a review from a green test run.
- `mint:"carried"` is never evidence of a review of this head (#10177: a carry re-bound an older `clean`
  onto a head whose own review had found a P1).
- A record minted on **another box** is invisible here: `~/.pi/agent/reviews/` is per-machine. Rows are
  this box's view, and the census says so rather than implying a fleet-wide clean bill.
