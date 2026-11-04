# Attestation census — open PRs

Repo `daniel-ospina/tortoise` · generated 2026-11-04T07:42:59Z by `tools/attest_census.py` · content sha256 `e2e763785f779b10d15b1e7c16d87fce942a263a783831ab0c2a66fe0d6620a1`.

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

**Totals:** NOTHING-TO-RESTORE=1

| PR | head | record | record head | match | mint | verdict | evidence | gate | outcome |
|---|---|---|---|---|---|---|---|---|---|
| #5 | `aaaaaaaaaaaa…` | — | — | — | — | — | — | not-read | **NOTHING-TO-RESTORE** |

## What each flagged row needs

- **#5** `b` — NOTHING-TO-RESTORE: no record on this box at any head; the published-marker surface was NOT read (--no-gate), so this is not a statement about GitHub

## Reading rules

- A `RE-REVIEW` row is **not** restorable, however green the old verdict was: the reviewed artifact is gone.
- A `NOTHING-TO-RESTORE` row means exactly that. Never infer a review from a green test run.
- `mint:"carried"` is never evidence of a review of this head (#10177: a carry re-bound an older `clean`
  onto a head whose own review had found a P1).
- A record minted on **another box** is invisible here: `~/.pi/agent/reviews/` is per-machine. Rows are
  this box's view, and the census says so rather than implying a fleet-wide clean bill.
