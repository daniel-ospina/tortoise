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
automatic `GITHUB_TOKEN` cannot create. Most of the reds are therefore not a failure of the tool: they
are the tool working, pointing at a queue item. The split is measured, because the gloss does not
cover all of them — of the 73 reds below, **68 were the tool's own exit 1** (the handoff) and **5 were
exit 2**, all five on `{"error": "could not run gh: [Errno 2] No such file or directory: 'gh'"}`: a
measurement failure, not a queue item. **The cause is the runner that picked the job up, not a missing
install step.** Read off `GET /actions/runs/<id>/jobs` for all 73 failures (2026-10-22): the five exit-2
runs ran on a **self-hosted** runner — `runner_name: pi-runner-vm-3` / `-vm-4` / `-vm-6`,
`runner_group_name: Default`, `labels: ["ubuntu-latest"]` — while the 68 exit-1 runs ran on the
GitHub-hosted image (`runner_name: "GitHub Actions …"`, `runner_group_name: "GitHub Actions"`). That
image ships `gh` in `/usr/bin` — measured, and recorded in `config/ci-surfaces.yml` on the
`tests/test_install_queue_reconcile_schedule.py` entry (its comment: `ships gh in /usr/bin`) — so
installing `gh` in the workflow would change nothing; the self-hosted runners advertise the
`ubuntu-latest` **label** without carrying `gh` on `PATH`, which is the same shortage #9018 (open)
records for `unzip` and `gh`. A mixed fleet means the exit-2 class recurs intermittently until that
is fixed. (The five runs:
`39321962358`, `39336862094`, `39364288888`, `39634379170`, `39642835446`.) Both classes are still
`ran-not-succeeded`, so both are still the coverage signal — which is the point of Fact 2.

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
| `repair` is not a branch-protection required context | `.mergify.yml` (`queue_conditions`/`merge_conditions`) and `.github/scripts/check-required-set.py`'s enumeration of the seven — **not** `.github/settings.yml`, whose own header says it is INERT and that `check-required-set.py` exists because of that; `.github/workflows/ci-check-repair.yml`'s job comment is the intent statement | the seven required contexts do not include `repair`; `grep -c repair .mergify.yml` → `0`. (`docs/ci/required-contexts.json` does mention `repair`, but as an *emitter* map entry, not as part of the required set — do not read it as one.) |
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
- **the same green is reachable without touching the exit code at all** — dropping `--apply` from
  the step (`python3 tools/ci_check_repair.py --json`) makes `report()` stop escalating the
  `needs_user_actor` case (`if apply: escalations += 1`), so the tool exits **0** for the very state
  this run exists to report, and the window reads `covered` again. A guard on the exit code alone
  does not see it;
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

Two tests, because the coupling has two ends and pinning one proves nothing about the other.

`tests/test_ci_check_repair.py::test_the_red_is_the_coverage_signal_so_it_cannot_be_greened_alone`
holds the **workflow** end, in two different ways:

- **structure** — neither `continue-on-error` **nor** `if:` may appear at the **job** level or on the
  repair step. Both conclude `success` without the repair succeeding: `continue-on-error` tolerates
  the failure, and a skipped job or step is not a failure at all. `if:` is the same green one line
  earlier, which is why it is banned alongside `continue-on-error` (the shape
  `tests/test_ai_review_gate_contract.py` already uses for the same reason);
- **behaviour** — the step's own shell is EXECUTED against a stubbed interpreter, and must hand back
  **every** non-zero class it is given (`1`, `2`, `3`) and `0` for a clean run — not just the two
  spellings a reviewer would probe by hand. This is the half a text pin cannot do:
  `declare -i rc=0` *after* the assignment, `[ "$rc" -ne 0 ] && rc=0` and `printf -v rc 0` all force
  the exit code while leaving `rc=${PIPESTATUS[0]}` and the literal `exit "$rc"` intact. So does a bare
  `rc=0` before the assignment — an assignment is itself a simple command and resets `PIPESTATUS`,
  which is exactly why that edit must be caught rather than reasoned about. Probing only `1` is what
  let `[ "$rc" = 2 ] && rc=0` — the natural way to green the "measurement failure" class under arm
  (B) — read **green**, so every non-zero class is probed;
- **the invocation** — the stub records its argv, and the step must still call
  `tools/ci_check_repair.py --apply`. Without `--apply` the tool exits `0` for the state this run
  exists to report, so an exit-code-only probe greens on a deletion;
- **the crash is not the signal** — stderr must be empty and the step summary must be written. The
  step re-arms `set -e` before its summary block and writes a fixed `/tmp` path, so a crashing block
  exits `1` on its own; without this arm the `must exit 1` assertion would pass without `rc` ever
  being propagated.

The step is found by CONTENT (`tools/ci_check_repair.py` in its `run:`), not by its display `name:`,
so renaming it for clarity is not a red — and the workflow is read as parsed YAML, so a job-level key
(the one place a substring window anchored on the step's `name:` is blind) is visible.

`…::test_the_launcher_acts_on_a_failed_window_and_stays_idle_on_a_green_one` holds the **launcher**
end by driving `tools/ci-check-repair-local.sh` itself, with `gh`/interpreter/`cmux` stubbed: a
`covered` window must leave the fallback idle, and a `ran-not-succeeded` window must run the tool.
This is not redundant with `workflow_state`'s own polarity test — `workflow_covered()` returning `0`
unconditionally makes the fallback permanently idle while every classifier assertion still passes.

Mutation-tested, each vector one at a time: job-level and step-level `continue-on-error`, job-level and
step-level `if:`, `rc=0` after the pipeline, `declare -i rc=0` placed **after** the assignment,
`[ "$rc" -ne 0 ] && rc=0`, `printf -v rc 0`, `[ "$rc" = 2 ] && rc=0` (the single-class green),
`--apply` dropped from the step, and `workflow_covered()` returning `0` — all red; a renamed step
stays green. Placement matters in both directions: `declare -i rc=0` **before** the pipeline is
harmless (the later `rc=${PIPESTATUS[0]}` overwrites it) and correctly stays green — only the
post-assignment form is a fail-open.

## What the owner still has to decide

Which surface carries the "needs a local fallback" verdict — (A) informational only on `main`,
(B) split the verdicts, or (C) leave it red. Under **(A)** and **(B)** the coverage signal must move
with it — that is not optional, and it is the cost this record exists to price. Under **(C)** nothing
moves: leaving the run red *is* the status quo, which is why (C) is not a change at all. Recording the
answer on #9365 is the relay.
