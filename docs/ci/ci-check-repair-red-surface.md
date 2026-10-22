---
title: "ci-check-repair's red: which surface carries the verdict (#9365)"
type: engineering
domain: capability
doc_status: live
created: 2026-10-21
subjects.team: epistemic-team
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# `ci-check-repair`'s red — which surface carries the verdict (#9365)

> **Decision-support record for #9365, which is `needs-decision` and open.** It does not choose
> between the issue's arms (A), (B) and (C); it records the two facts that make the choice safe to
> implement, with the code anchors, so the next lane does not have to re-derive them — or worse,
> implement half of one of them and read green.
>
> Measured 2026-10-21 against `origin/main` @ `69390341f`. Run history read from
> `GET /repos/daniel-ospina/tortoise/actions/workflows/ci-check-repair.yml/runs`.

## The state being reported

`ci-check-repair` runs every 30 minutes and exits 1 when a PR has a required status context with **no
producing run at the head** — the `needs_user_actor` state, whose only remedy is a new PR event the
automatic `GITHUB_TOKEN` cannot create. A red run is therefore not a failure of the tool: it is the
tool working, pointing at a queue item.

**How often:** over the 233 scheduled runs of 2026-10-17 → 2026-10-21, **73 were red (31.3 %)**,
including

- **13 consecutive red windows** on 2026-10-19, 05:23 → 12:34, across **eleven distinct heads** (two
  heads repeat: `c495de1ab` at 09:34 and 09:53, `e9808af5f` at 10:23 and 10:45);
- **four consecutive red windows on one sha** (`d43a3b73a`, 2026-10-21, 06:36 → 07:50).

That is the alarm-fatigue premise of #9365, measured rather than asserted.

## Fact 1 — the red does NOT block a merge (option (C) is already the rail's behaviour)

The issue's first consequence reads "by the repo's own reading rule main is **RED** — and the merge
rail reads `main`'s runs, so this is not cosmetic." That is measurably false for the rail:

| Claim | Anchor | Measured |
|---|---|---|
| `repair` is not a branch-protection required context | `.github/workflows/ci-check-repair.yml` (job comment), `.github/settings.yml` | the seven required contexts do not include `repair` |
| the rail's red-relative clock skips non-code events | `tools/merge_throughput.py:101` `NON_CODE_EVENTS = frozenset({"schedule", "issues", "issue_comment"})`, used by `base_blocking_reds()` (`:3111-3112`) | a `schedule`-event red on `main` is **exempt** — it never enters the staleness clock |

So the residual is the **fleet's own "main is red" reading**, not the merge rail. The exemption is
decided by the *resolved* event and never granted by default: a red whose event cannot be resolved
still blocks (documented in `base_blocking_reds`).

## Fact 2 — arms (A) and (B) are NOT safe as written: the red is the coverage signal

`tools/ci-check-repair-local.sh` decides whether the local fallback runs **at all** from the newest
`ci-check-repair` run's **conclusion** (`gh run list --json conclusion` → `--workflow-state` →
`workflow_state()`, which returns `covered` only for `conclusion == "success"`). A failed window is,
by construction, the GitHub→local handoff.

Therefore:

- forcing `exit 0`, or adding `continue-on-error: true` to the tool step, makes the window read
  **`covered`**;
- the fallback then goes idle, and a PR in the `needs_user_actor` state has its required checks
  produced by **nobody**;
- that is the #8254 miss this workflow exists to prevent, restored silently — the coverage read fails
  **open**.

The invariant is already pinned in the suite:
`tests/test_ci_check_repair.py::test_exit_zero_never_hides_an_unhandled_repair` ("exit 0 must not
coexist with an unhandled repair"). Any "stop reddening" arm must therefore move the coverage signal
**off the run conclusion** (JSON artifact, step summary — the mechanism is open) **in the same
change**. That prerequisite is the real cost of (A) and (B), and the issue does not state it.

## What is pinned

`tests/test_ci_check_repair.py::test_the_red_is_the_coverage_signal_so_it_cannot_be_greened_alone`
fails if either half of the coupling is moved alone:

- `continue-on-error: true` at the **step** level — or at the **job** level, where it makes the whole
  run conclude `success` just as effectively;
- `exit 0` replacing `exit "$rc"`, or a forced `rc` (e.g. `rc=0` after `rc=${PIPESTATUS[0]}` — the
  literal `exit "$rc"` survives that edit, so it has to be pinned on the assignments);
- the coverage read ceasing to treat a failed window as the handoff.

It reads the workflow through parsed YAML (job keys, the step's keys, the step script's `rc=`
assignments and its last command), because a substring window anchored on the step's `name:` cannot
see a job-level key or a forced `rc` — the two most plausible ways to green this run. Mutation-tested
at all three seams: red with the naive change, green without it.

## What the owner still has to decide

Which surface carries the "needs a local fallback" verdict — (A) informational only on `main`,
(B) split the verdicts, or (C) leave it red. Whichever is chosen, the coverage signal moves with it;
that is not optional. Recording the answer on #9365 is the relay.
