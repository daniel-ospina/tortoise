---
title: "Orchestrator harness — autonomous lane dispatch and merge rail (`~/orchestrator`)"
type: operations
domain: platform
created: 2026-10-12
ownedBy: organisation-design-team
subjects.team: organisation-design-team
aboutSubjects: organisation-design-team
aboutObjects: daniel-ospina/tortoise, tortoise#8246
doc_status: draft
---

# Orchestrator harness

> **What this is.** A self-contained, single-host harness that turns one laptop into an
> autonomous delivery lane: it sweeps the open PR queue, dispatches GitHub issues into
> isolated worktrees as autonomous `pi` lanes, runs a fresh-context review → signed-record
> → merge chain on each result, and repairs the PRs whose reviewer found real problems.
>
> **Where it came from.** Built ad hoc in `~/orchestrator/` on one box during the
> 2026-10-10 → 2026-10-11 run that merged ~99 PRs (77 of them between
> `2026-10-10T23:45Z` and `2026-10-11T21:22Z`). It was never version-controlled. This
> directory is the versioned handover so it can be **evaluated** before being adopted.
>
> **Status: evaluation artifact.** Nothing here installs or runs on its own. `install.sh`
> renders a launchd plist and *prints* the load command; it does not execute it.
> Tracked by tortoise#8246.

---

## 1. Why it exists

The repo's normal path is one agent, one issue, one PR, human-paced. On a box that is left
running overnight that path is idle most of the time. This harness keeps the box saturated
without a human in the loop:

- **Fan-out:** N simultaneous implementation lanes, each in its own git worktree.
- **Fan-in:** a per-PR chain that reviews, records the review as a gate artifact, and merges.
- **Back-pressure:** every cycle is gated on load average and swap, so the box throttles
  itself instead of thrashing. Above `MERGE_PRIORITY_AT` open PRs (10) the balance shifts to
  **draining**: new lanes stop being dispatched and reviewer headroom rises, because a merge
  backlog means the scarce capacity belongs to review and merge, not to opening more PRs.
  The one exception is a `P0-blocker` at the **head of the backlog**, which the merge-priority
  gate does not hold back while `P0_BYPASS_MERGE_PRIORITY` is `1` (the shipped default): draining
  a backlog and starting the highest-severity work are not in conflict there. Only that gate is
  bypassed — load, lanes, swap and memory still bind, so no extra lane can open.
- **Self-healing:** a PR whose reviewer reports real findings is routed to a *fixer*, fixed,
  and re-reviewed — rather than being left red forever (this is the bug that stranded ~15
  PRs before it was fixed; see §7).

It deliberately does **not** use the `task` sub-agent tool. Workers are separate `pi -p`
processes in tmux sessions, so a killed worker cannot take the controller down with it.

---

## 2. Architecture

```text
                      launchd  com.eldato.orchestrator-loop  (RunAtLoad + KeepAlive)
                                        │
                                  orch-loop.sh        ← the controller, one process
                                        │
        ┌───────────────────────────────┼────────────────────────────────┐
        │                               │                                │
   (1) merge sweep              (2) capacity-gated dispatch      (3) backlog refresh
        │                               │                                │
        │  for every open non-draft PR  │  read candidates.txt            │  refresh-candidates.sh
        │  read the 7 required checks   │  skip if in dispatched.txt      │  pre-flight each issue
        │                               │  dispatch-merge.sh:             │  (collision_preflight.py)
        │   GREEN   → gh pr merge       │    git worktree add             │  append CLEAN ones
        │   PENDING → --auto            │    tmux new lane-<N>  ──┐       │
        │   FAILED  → see below         │    tmux new chain-<N> ──┤       │
        └───────────────────────────────┴─────────────────────────┼───────┘
                                                                  │
                        ┌─────────────────────────────────────────┘
                        ▼
        lane-<N>   → lane.sh   : pi -p in .worktrees/tm-<N>, implement → commit → push → open PR
                        │
        chain-<N>  → chain-merge.sh : poll for the PR to appear, then
                        ▼
        merge-<PR> → merge-one.sh :
                        1. fresh-context reviewer → posts its review as a PR comment (the audit artifact)
                        2. CLEAN   → record-review.sh (HMAC-signed marker into the PR body)
                                     → wait for ai-review-gate SUCCESS → gh pr merge --squash
                            ISSUES  → touch state/needsfix-<PR>-<head>   ← hands off to the fixer path
                            no line → NOTHING is written: the reviewer stalled/failed, so no fixer is
                                      authorised and no MAX_FIX_TRIES attempt is spent; the next
                                      cycle re-records a fresh review at the same head (#8602)
                        ▼
        fix-<PR>   → fix-one.sh  : materialises a worktree for the branch (including foreign
                                   branches), runs a fixer over the reviewer's findings, pushes a
                                   new commit, then re-runs merge-one.sh end-to-end
```

### Worker types

| Session | Script | Writes to | Purpose |
|---|---|---|---|
| `lane-<N>` | `lane.sh` | `.worktrees/tm-<N>` on `feat/<N>-tm` | implement issue #N → commit → push → open PR |
| `chain-<N>` | `chain-merge.sh` | — | wait for the lane's PR, then delegate to `merge-one.sh` |
| `merge-<PR>` | `merge-one.sh` | — (the reviewer gets its OWN disposable checkout, see §5.3) | fresh review → signed record → wait green → squash-merge |
| `fix-<PR>` | `fix-one.sh` | the PR's worktree | repair findings **or a failing required check**, push, re-run the merge chain |

### State files (`$O/`)

| File | Meaning |
|---|---|
| `heartbeat` | epoch seconds, rewritten every cycle — the liveness probe |
| `.orch.lock` | atomic single-instance lock (noclobber create); stale locks are reclaimed |
| `candidates.txt` | the dispatch backlog: issue numbers that passed collision pre-flight |
| `dispatched.txt` | issues already handed to a lane. **Append-only — never expires** (see §7.1) |
| `checked.txt` | issues whose pre-flight returned **CLEAN** — the one verdict that terminates the issue by **queueing** it. An **errored** pre-flight, a **COLLISION** and a **LANDED** verdict leave no entry (agent-infra#1716, #8560) |
| `landed.txt` | issues whose pre-flight returned **LANDED** (exit 4): the work already shipped, so they are kept out of the backlog and out of the LIMIT budget — but **not** recorded as checked-forever. The set is printed on every refresh pass and an entry is dropped once its issue is **CLOSED**; see §7.1b (#8560) |
| `state/mergeclaim-<PR>.lock` | single-owner claim: only one worker may review/record/merge a given PR |
| `state/needsfix-<PR>-<head>` | "a reviewer found real issues at this exact head" |
| `state/fixcount-<PR>` | fixer attempts for that PR, capped by `MAX_FIX_TRIES` |
| `state/parked-<PR>` | terminal state: the fixer cap was reached at the head recorded inside the file, so the sweep skips it until a new push re-arms it |
| `state/rerun-<PR>-<runid>` | CI re-run already requested for this run — prevents loops |
| `state/merged-<PR>.done` | merge confirmation |
| `state/last-open-n` | the last SUCCESSFUL open-PR count; reused only when positive if a later count fails — a failure never reads as 0 |
| `logs/` | one log per worker: `lane-*.log`, `chain-*.log`, `merge-*.log`, `fix-*.log`, `orch-loop.log` |
| `review/<PR>-<head>/` | the reviewer's disposable detached worktree at the head it is reviewing (#8484). Created outside the repo, removed by the EXIT trap; its parent directory is the only thing left behind |
| `wip/` | dirty-worktree captures taken before a shutdown (see §6) |

### Why the *reviewer* is a separate process from the *controller*

`merge-one.sh` spawns a fresh-context `pi -p` reviewer that has never seen the implementing
lane's reasoning. It reads the changed files at the PR head (`git show origin/<branch>:<path>`)
rather than only diff hunks, and posts its verdict as a PR comment. That comment is the
evidence the `record-review.sh` signature points at, so the merge gate is auditable after
the fact: the PR body names the comment it was signed against.

The reviewer runs in its **own** checkout, not the controller's: `merge-one.sh` provisions a
**detached** worktree at the head under review and cds the agent into it. Detached is the point —
a commit there moves no branch — and the tree is disposable, so a leaked mutation leaves with it
(§5.3, #8484). The controller's `refs/heads/<branch>` and `refs/remotes/origin/<branch>` are sampled
before and after the reviewer; a move the controller did not request is logged with its author and
refuses to record a verdict, because a review marker is head-bound.

---

## 3. Prerequisites

| Dependency | Why | Notes |
|---|---|---|
| `tmux` | every worker is a detached tmux session | `brew install tmux` |
| `gh` (authenticated) | all queue + PR operations | needs push access to the target repo |
| `pi` on `PATH` | the worker runtime (`pi -p --provider deepseek --model deepseek-v4-flash`) | provider/model are literals in `lane.sh`/`merge-one.sh`/`fix-one.sh` — change them there |
| `uv` | `lane.sh` runs `uv sync --extra embeddings --extra parity` in each worktree | tortoise-specific |
| `~/.pi/agent/scripts/record-review.sh` | writes the HMAC-signed review marker the `ai-review-gate` check verifies | shipped by `agent-infra` |
| **`AI_REVIEW_GATE_KEY`** | the merge authority — env var, or `~/.pi/agent/.ai-review-gate-key` | **this harness cannot merge without it.** Treat as a secret; never commit it |
| the target repo's `tools/collision_preflight.py` | `refresh-candidates.sh` refuses overlapping dispatches | tortoise-specific path |
| `.worktrees/` gitignored in the target repo | worktrees are created under it | `git check-ignore .worktrees` |

**Single-host assumption.** Everything is local: tmux sessions, worktrees, logs, the key.
There is no scheduler, no queue service, no shared database. Two boxes running this against
the same repo work fine *because* GitHub is the coordination medium — but they will duplicate
work unless they coordinate (see §5, "broad mode").

---

## 4. Install

```bash
# 1. Put the harness where $O will point (default ~/orchestrator)
mkdir -p ~/orchestrator && cp operations/orchestrator-harness/*.sh ~/orchestrator/
mkdir -p ~/orchestrator/logs ~/orchestrator/state ~/orchestrator/wip

# 2. Point it at your box (all three are overridable; defaults are the original box's values)
export OR=daniel-ospina/tortoise         # owner/repo
export REPO=$HOME/eldato/tortoise        # the local main checkout
export O=$HOME/orchestrator              # the harness state directory

# 3. Render + install the launchd agent (does NOT load it)
./install.sh

# 4. Start it when you mean to
launchctl load -w ~/Library/LaunchAgents/com.eldato.orchestrator-loop.plist

# liveness / stop
cat ~/orchestrator/heartbeat                 # epoch seconds, should be < ~5 min old
tmux ls                                      # lane-*, chain-*, merge-*, fix-*
launchctl unload -w ~/Library/LaunchAgents/com.eldato.orchestrator-loop.plist
```

`install.sh --uninstall` unloads the agent and removes the plist.

Run the offline self-test first — it needs no network, tmux server or git writes. **Every check
exercises the shipped script** (it extracts the real classifier, `sess()`, the claim block and the
reaper, or runs `install.sh` for real) rather than re-implementing the logic — a test that
re-implements the fix passes on the broken copy and proves nothing. That failure mode was found in
review of this very file. Every check is written to fail on the pre-fix tree it
pins; the live total is printed by `./selftest.sh` rather than quoted here.

```bash
./selftest.sh          # run it for the live list. Covers: syntax, exact session matching, the
                       # fail-closed gate classifier, claim atomicity and stale-claim recovery,
                       # the reaper's keep/reap decisions, rejection of a hostile branch name,
                       # install.sh rendering with a hostile path, limit-default consistency,
                       # placeholder parity, the daemon PATH (shim exclusion + execute-probe) and
                       # the failed-count fallback, README overclaim guards, credential scan
```

**The gate key only reaches the daemon if it is a FILE.** `install.sh` deliberately does not copy a
secret into a world-readable plist, and launchd inherits no shell environment — so an env-only
`AI_REVIEW_GATE_KEY` is reported as insufficient, not as "present". The box cannot merge without it.

### Tuning (10-core / 32 GB box — the values that were actually used)

| Var | Value | Why |
|---|---|---|
| `MAX_LOAD` | `cores*10` (100 on a 10-core box; sanity ceiling) | dispatch **and every agent spawn** stop above this 1-min load (resolved in `spawn-gate.sh`, rendered into the plist by `install.sh`, asserted equal by the self-test) |
| `MAX_LANES` | `4` | concurrent implementation lanes |
| `MAX_REVIEW` | `4` | concurrent `merge-<PR>` reviewers |
| `MERGE_PRIORITY_AT` | `10` | above this many open PRs, **merging takes priority**: dispatch of new lanes stops (unless none are running, or a `P0-blocker` is at the backlog head — see `P0_BYPASS_MERGE_PRIORITY`) and reviewer headroom rises to `MAX_REVIEW+2` |
| `P0_BYPASS_MERGE_PRIORITY` | `1` | when `1`, a `P0-blocker` at the **head of the backlog** is dispatched even above `MERGE_PRIORITY_AT` — only that gate is bypassed (load/lanes/swap/memory still bind); `0` restores the unconditional gate |
| `MAX_FIX` | `3` | concurrent `fix-<PR>` fixers |
| `MAX_FIX_TRIES` | `3` | per-PR fixer attempts before it is left alone |
| `MIN_SWAP_FREE` | `600` | MB; with `MAX_LOAD` raised this and `MIN_MEM_FREE_MB` are the binding gates in practice |
| `MIN_MEM_FREE_MB` | `512` | MB of *available* memory (`MemAvailable` on Linux, else `MemFree`+`Cached`; `free`+`inactive` on macOS). macOS shows pressure as compression with swap flat, so swap alone does not see it |
| `BATCH` | `2` | issues dispatched per cycle |
| `CYC` | `240` | seconds between cycles |
| `STALE_MIN` | `40` | a worker whose log has not grown in this many minutes is presumed hung and its session is killed (§6) |
| `LANE_TIMEOUT` | `2400` | wall-clock limit on a lane's `pi` run (s) |
| `REVIEW_TIMEOUT` | `1800` | wall-clock limit on a reviewer `pi` run (s) |
| `FIX_TIMEOUT` | `2400` | wall-clock limit on a fixer `pi` run (s) |

**Every agent spawn is load-gated, not just lane dispatch — and the gate is at the SPAWN, not at the
controller's decision.** A Mac reboot lands in an indexing storm (Spotlight, `PerfPowerServices`,
`duetexpertd`) that routinely pushes 1-min load past 200. There are two gates now, and the
second one is the invariant:

1. `orch-loop.sh` gates its **decisions to create a worker session** at four sites (conflict fixer,
   review-fix fixer, CI-red fixer, merge re-review), while the cheap actions (`gh pr merge`,
   `gh pr ready`, `gh run rerun`) stay ungated so merges keep flowing during a spike.
2. **The `pi` invocation itself is gated**, in the shared helper `spawn-gate.sh` — the only place in
   the harness that runs `pi` at all. `lane.sh`, `merge-one.sh` and `fix-one.sh` route through its
   `run_agent()`, which takes a **fresh** load reading and refuses above `MAX_LOAD`. A refusal costs
   nothing and consumes nothing: the worker exits `75`, records no verdict, and the PR is
   re-dispatched when load falls (a manual `chain-<N>` or `adopt-fix.sh` run names the refusal in
   its own log, so a box with no controller running does not fail silently).

   "Consumes nothing" needs two things the gate alone cannot provide, because the controller books a
   fixer attempt when it **creates the session** (`orch-loop.sh`) — *upstream* of the gate:
   - `fix-one.sh` **refunds** that attempt on a refusal. It is the only party that knows no agent
     ran, so it rewinds `fixcount-<PR>`; without the refund, three refusals reach `MAX_FIX_TRIES`
     and park a PR that never had a single fixer run.
   - the controller no longer deletes `needsfix-<PR>-<head>` at dispatch. `fix-one.sh` consumes it
     only once the agent has **actually started**, so a refusal leaves the reviewer's findings
     intact and the next cycle re-dispatches instead of spending a full fresh-context re-review on
     a head nobody fixed.

   Refusal is `AGENT_STARTED=0`, set by `run_agent()` and authoritative over the exit code: `pi`
   shares the 0–255 status namespace, so a `pi` that exited `75` for its own reason must not be read
   as a load refusal.

(2) is what makes the claim true rather than nearly true. Gating only the decision was
insufficient: `chain-<N>` — the primary first-review path for every own-lane PR, and the one path
the controller deliberately stands down for because the chain holds the claim — waits up to 90
minutes for its PR, so a lane dispatched on a quiet box had its reviewer launch onto whatever load
existed at that later moment (`fix-one.sh` and `adopt-fix.sh` reached the same ungated spawn).
`spawn-gate.sh` exists so there is **one** gate to keep true rather than one per launch path: a
fourth path added tomorrow is refused by `run_agent()` automatically, and `selftest.sh` asserts that
exactly one shipped file invokes `pi` and that the helper refuses above `MAX_LOAD`.

⚠️ **Both gates test load only.** The available-memory gate applies to *lane dispatch*, not to these
spawn sites — the two cover different populations. Raising `MAX_LOAD` therefore narrows the band in
which the spawn sites hold back (`16`–`99` is now permitted, where `16` used to hold). Closing that
needs the gate to consult memory as well, which changes the one-line shape its self-test extracts
from the shipped file; left as a named follow-up rather than half-done here.

⚠️ **The retired scripts in `retired/` are the named residual.** `refix.sh` and `watch-one.sh` still
call `pi` directly and are **not** load-gated. They are not installed, not invoked by
`orch-loop.sh`, and each carries a banner saying so — do not re-activate one without routing its
spawn through `spawn-gate.sh`. The self-test asserts the banner is present, so a silent
re-activation fails the suite.

**Swap, not lanes, is what actually throttles this box.** Observed at 15 concurrent workers:
`swap_free=588M` → dispatch correctly refused. Raising `MAX_LANES` alone buys nothing on 8 GB;
it just moves the pressure into the compressor. `swap_total=0` right after boot is treated as
*uninitialised*, not as pressure (that bug silently blocked all dispatch for the first minutes
after a restart until it was fixed).

**The capacity probes are portable: Linux cell + macOS host.** `#1702`. Every probe used to be a
macOS primitive, so on a Linux cell `load1()` returned empty (the `MAX_LOAD` gate could never bind),
swap read as `0` (the gate skipped by its own "uninitialised" rule), and the reaper's
`stat -f '%m'` became an arithmetic error that killed the controller one cycle after its first
dispatch — every lane/merge/fix writes the log the reaper stats. Each probe now has a Linux path
(`/proc/loadavg`, `/proc/meminfo`, `nproc`) and a macOS one (`sysctl`, `vm_stat`), and the reaper's
mtime probe is a GNU-then-BSD `stat` ladder with a numeric guard (GNU first on purpose: GNU
`stat -f` writes the filesystem listing to **stdout** before exiting 1, so a BSD-first `||` ladder is
the bug itself).

⚠️ **Available memory was the fifth macOS-only probe, and the only one that fails CLOSED.** The gate
is deliberately "UNKNOWN is not ample" (`mem_probe_failed` → do not add work), so with a `vm_stat`-only
probe every Linux cell set `MEM_UNKNOWN=1` on every cycle: load, lanes and swap could all be healthy
and the memory gate still bound — the controller completed cycles, logged `mem_probe_failed` and
dispatched nothing, which is a *different* false green from the one #1702 removes. `mem_avail_mb()`
therefore reads `/proc/meminfo` (`MemAvailable`, else `MemFree`+`Cached`) on a cell and keeps the
`vm_stat` `free`+`inactive` parse on macOS. The fail-closed ruling for a genuinely unreadable probe is
unchanged — only its Linux measurability is.

A load reading that is *unreadable* stays the documented **fail-open** (`can_spawn()` defaults it to
`0`): both supported platforms are measurable now, and failing closed on an unreadable probe would
turn "no load back-pressure" into a permanent controller outage. `install.sh` still refuses to run
off macOS (it renders a launchd plist) — the portability that matters is `orch-loop.sh`, the file
`cloud-cell-supervisor.sh` starts.

---

## 5. Merge policy and safety invariants

1. **The gate is never bypassed.** A merge requires `record-review.sh` to have written a
   signed marker for the *exact head SHA* into the PR body, and the `ai-review-gate` check to go
   **`SUCCESS`** — `SKIPPED`/`NEUTRAL`/absent do **not** count, because that means no signed review
   exists. `merge-one.sh` refuses to merge if the recorded head ≠ the current head, so
   a post-review push invalidates the evidence automatically.
1b. **One owner per PR, per box.** Every path into `merge-one.sh` (the lane's `chain-<N>`, a
   `fix-<PR>`, and the controller's sweep) takes the same atomic `state/mergeclaim-<PR>.lock` first.
   Without it a freshly opened PR gets two concurrent reviewers racing one head: duplicate evidence
   comments, duplicate `record-review` writes, and an `ISSUES`/`CLEAN` interleaving.
   **This is a local file, so it serialises one box only** — two boxes sharing a repo are not
   coordinated by it (§7.2b, tortoise#5052).
2. **The reviewer must state a plain outcome.** Its final line is exactly `CLEAN <comment-url>`
   or `ISSUES <n>`. A CLEAN comment may not contain "issues remain", "NOT CLEAN", or
   "requires human attention" — forbidden phrases, so an equivocal review cannot be laundered
   into a merge. A run that ends with **neither** line wrote no verdict at all (a stall at the
   wall limit, a crash): it authorises nothing — no `needsfix` marker, no fixer attempt, no
   `MAX_FIX_TRIES` spend — and the next cycle re-records a fresh review at the same head
   (#8602, agent-infra#1741). What the reviewer is asked to DO is also bounded: it runs in a
   disposable checkout with no interpreter environment of its own, and executing tests is the
   fixer's and CI's job, not the reviewer's.
3. **Scope adapts to the queue.** `open PRs > 10` → **broad** mode: the sweep covers *every*
   non-draft PR in the repo, not just this box's. `≤ 10` → own branches only
   (`*-tm`, `*-cap`, `fix/*`). Broad mode is how this box drains a peer's queue; it is also
   how two boxes accidentally duplicate work if they both run broad.
4. **Drafts are deliberately excluded** from the sweep — a peer's draft PR is not yours to
   merge. The cost is that our *own* lanes' drafts stall invisibly (see §7.2).
5. **Workers never touch the main checkout.** Every lane runs in `.worktrees/tm-<N>`; the
   controller only reads. `lane.sh` instructs its agent not to create or remove worktrees.
6. **No `task` sub-agents.** Extensions that would gate a nested agent (`review-enforcer`,
   `verification-gate`, `loop-enforcer`, `sequence-enforcer`) are explicitly disabled for
   workers, and the *harness* is the review gate instead.
7. **The key is the crown jewel.** The box that holds `AI_REVIEW_GATE_KEY` can authorise
   merges. It lives at `~/.pi/agent/.ai-review-gate-key`, is never written to logs, and is
   never copied into this bundle.
8. **One controller.** `.orch.lock` is created with `set -o noclobber`; a second start exits
   immediately, and a lock whose PID is dead is reclaimed.
9. **GitHub data never reaches a shell.** Inputs from the API are validated before use:
   `safe_ref` admits only `[A-Za-z0-9._/-]` **and** requires `git check-ref-format --branch`; PR ids
   must be all digits (`is_num`); worker arguments travel in a file and the daemon launches
   `merge-one.sh --argv <file>`, so no PR-derived value is embedded in the `sh -c` string that
   `tmux new-session` executes **on any live path**. (`retired/auto-loop.sh` still contains the old
   unsafe interpolation, annotated in place — it is unreachable from the live scripts and exists
   only as history; do not reuse it.) Branch names were the hole: git **permits** `'` `;` `$` `` ` `` `&` `|`
   `(` `)` `{` `}` `"` `<` `>` `#` in a ref (only spaces are forbidden, and `${IFS}` supplies
   those), so a fork PR on `fix/x';touch${IFS}/tmp/x;#` achieved arbitrary command execution on the
   host holding the merge key. Reproduced before the fix; the self-test now pins the validator.

### 5.3 Untrusted input and worker containment — read before running this unattended

This harness passes **attacker-controlled text** to language-model agents with shell access. Be
clear about what is and is not enforced:

- **The reviewer is contained; the fixer is deliberately not.** `merge-one.sh` runs its
  fresh-context reviewer in a **disposable, detached** git worktree of its own
  (`$O/review/<PR>-<head>`, created outside the repo), and **unsets** the main-edits hatch rather
  than merely not exporting it — `fix-one.sh` exports the hatch and runs `merge-one.sh` as a child,
  and an exported variable is inherited, so the unset is what makes the refusal hold on the
  re-review path that follows every fixer run.
  A commit there moves no branch, a mutation in it cannot reach the reviewed tree, and a reviewer
  that wanders into the shared checkout is **refused by `main-worktree-guard` rather than obeyed**.
  The tree is removed on exit, deregistering **only its own** worktree record by name — a repo-wide
  `git worktree prune` is banned (it deregisters every record whose directory is not stat-able at
  that instant, orphaning a sibling lane's checkout) — and a move of the **reviewed branch's** refs
  under the reviewer is logged with the ref and sha that actually moved, and refuses to record a
  verdict (#8484). The **fixer** is the opposite on purpose: `fix-one.sh` runs in the PR's own
  worktree with `AGENT_ALLOW_MAIN_EDITS=1`, because writing to that tree IS its job. Both still run
  with `AGENT_SKIP_REVIEW_GATE=1`, `ELDATO_SKIP_VGATE=1`, `SKILL_ENFORCER_DISABLED=1` and
  `LOOP_ENFORCER_DISABLED=1`, with `bash` and `gh` on `PATH`. **"Do NOT merge" remains a prompt
  instruction, not a control** — the harness's own gate (§5.1) is what stops an agent merging
  through this path.
- **Prompt injection is possible.** `fix-one.sh` reads the PR's comments into the fixer prompt as
  the authoritative review, and `merge-one.sh` feeds the reviewer the PR title, body and diff. A
  malicious PR body can therefore attempt to steer an agent that has shell access.
- **What actually constrains it.** The harness's own gate: only `orch-loop.sh` calls
  `gh pr merge`, and only after `record-review.sh` has written a signed marker for the exact head
  (§5.1) — so a rogue *agent* cannot merge through the harness path. It could still `gh pr merge`
  or `git push` directly if the token permits, which is a property of the token, not this code.
- **What is deliberately not done:** the gate key is never placed in a prompt, a plist, a log or a
  command line, and no script creates a secret-bearing file.
- **Open item (tortoise#8246):** contain the workers properly — a read-only key path, no
  merge-scoped token during review, and treating PR content as delimited data rather than
  instructions. Until then, run this on a box whose GitHub token you would accept being used by an
  agent that has read a hostile PR body.

---

## 6. Operating notes

### Deliberate stop (handing the laptop to a human)

The ritual, in order — the point is that **no uncommitted work is lost**:

1. `launchctl unload -w …` — so it does not restart on boot.
2. `pkill -f orch-loop.sh`, then `rm -f $O/.orch.lock`.
3. Freeze workers: `tmux kill-server` (or kill sessions individually) and check for strays.
4. Capture every dirty worktree: `git -C <wt> diff HEAD > $O/wip/<name>.patch`, plus a
   copy of untracked files and a `.status` file recording `git status --porcelain`.
5. Post a note on the coordination issue naming the open PRs, so a peer box keeps draining.

Durable state survives a reboot regardless: the signing key, review records, the worktrees'
`.git`, and every PR on GitHub.

### Restart recovery (what has to be re-derived by hand)

Workers do not survive a reboot and their tmux sessions are gone, so the controller has no
record that they existed. Today this is recovered by reading `git worktree list`, the
`$O/logs/*.log` tails, and the `wip/` patches. **This is the main operational weakness — see
§7.1.**

### Detecting a hung worker (do this first if the queue stops moving)

`pi -p` has no internal timeout, so a reviewer or fixer can hang indefinitely — and because the
concurrency caps count **tmux sessions**, one hang permanently occupies a slot. Observed
2026-10-12: three merge workers hung for 6–9 hours (one had spawned a `find /` that ran for 8
hours), so half the merge capacity was dead weight overnight while the queue looked busy.

```bash
# a worker whose log has not grown in 40+ minutes is hung
tmux ls -F '#{session_name}' | grep -E '^(merge|lane|fix)-' | while read s; do
  f=~/orchestrator/logs/$s.log
  printf '%s silent %ss\n' "$s" "$(( $(date +%s) - $(stat -f %m "$f") ))"
done | sort -k3 -n -r | head
```

`orch-loop.sh` now reaps these itself (`STALE_MIN`, default 40): the session is killed and the PR
is re-dispatched on the next sweep, with `claim_live()` freeing the dead owner's claim.

### Reading the logs

`$O/logs/orch-loop.log` is the controller's decision log; every line is prefixed by a tag:

```text
[scope] open=24 -> BROAD (help drain all)
[cap] load=7/100 lanes=4/4 mem_avail=11761M/512M swap_free=588M/600M   ← every gate names its ceiling
[dispatch] load=7 lanes=4 -> 4429 4489
[refresh] +#4508 CLEAN -> backlog
#8219 FAILED:ai-review-gate          ← per-PR verdict for the required checks
#8192 fixer attempt 1                ← routed to a fixer, not a re-record
...
[cap] load=104/100 lanes=4/4 mem_avail=11890M/512M swap_free=588M/600M
[dispatch] skipped (load=104>=max_load=100)   ← and WHY, naming the binding gate
```

---

## 7. Gaps: what is fixed, what is not

Details and evidence for the remaining items: tortoise#8246. Same failure class in the fleet rail:
agent-infra#1678, #1680, #1683.

### 7.1 Dispatch bookkeeping never expires — **FIXED in this PR**

`orch-loop.sh` prunes `candidates.txt` against `dispatched.txt`, so an issue number is recorded as
"handled" the first time it is handed to a lane, and nothing re-checked whether that dispatch
**delivered**. A lane that dies (reboot, OOM, crash) stranded its issue permanently — invisible to
both the loop and to issue-triage, because the issue stays open with no PR.

Observed: **#4114, #4095, #3554** were dispatched 2026-10-10, their lanes died in a shutdown, and
each sat with a dirty worktree, no PR, and no re-attempt for >24 h.

**Fix:** the background refresh now runs a reaper — for each `dispatched.txt` entry, if the issue is
still open **and** no PR branch matches the number **and** no `lane-<N>` session is live, the entry
is removed from `dispatched.txt` **and** `checked.txt` so the pre-flight re-queues it. Four of the
self-test's checks pin exactly this decision table (`4114` reaped, `8888` kept for its PR, `9999`
kept because it is closed, `7777` kept for its live lane).

⛔ **A failed listing is not an empty one.** The reaper refuses the whole pass unless both
`.open_iss` and `.pr_brs` are **present**; the capture deletes a list when `gh` fails, so "the read
failed" (absent) is distinguishable from "the answer is none" (present but empty). Before the
guard, a failed `gh pr list` left an empty `.pr_brs`, `has_pr` was false for every issue, and
`dispatched.txt` was cleared — so `orch-loop.sh` re-spawned a lane for an issue that already had a
PR (duplicate work against the shared queue, agent-infra#1705). The self-test's failure-path checks
pin both halves (absent → nothing reaped and `checked.txt` untouched; empty-but-readable → still
reaps), and the capture site is driven directly with a stub `gh`.

### 7.1b A `LANDED` verdict is suppressed, not blacklisted — **FIXED in this PR**

`collision_preflight.py` answers "is #N already done?" with exit **4 (`LANDED`)** (#8560). The
refresh loop runs that pre-flight over a pool built **only** from `gh issue list --state open`, so
the pre-flight's state-`CLOSED` arm is unreachable here: every `LANDED` this loop sees comes from
the **timeline** arm on an **open** issue, and may belong to a multi-PR issue that still needs work
(the gate's own #4886 caveat, whose remedy is *"verify it … if it still needs work, state the
remaining scope before dispatch"*).

Recording that verdict in `checked.txt` — as the first fix in this PR did — traded a visible
capacity leak for an invisible permanent one: `checked.txt` is pruned only by the reaper, which
walks `dispatched.txt`, and a landed issue is never dispatched, so the open work item left the
backlog permanently and nothing ever mentioned it again. That is the failure class §7.1 removed,
quoted two lines above the arm itself.

**Fix:** `LANDED` writes `landed.txt` instead:

- the pool loop filters it **before** the `LIMIT` counter, so a landed issue does not consume a
  pre-flight slot (the leak the first fix closed stays closed);
- the set is echoed on **every** pass (not only when a new verdict arrives), so an operator who asks
  "why is #N never dispatched again?" sees it — alongside the remedy — instead of it vanishing;
- nothing is queued: only `CLEAN` reaches `candidates.txt`;
- the reaper drops an entry once its issue is `CLOSED`, so the list drains as decisions are made.
  It does **not** infer CLOSED from absence in the capped `.open_iss` listing: that listing is
  `--limit 500` and this repo has >500 open issues, so it is permanently saturated and absence there
  proves nothing — the earlier `len(open_iss) < 500` guard tripped on every pass and the prune was a
  no-op. The prune asks the entry's own state directly (`gh issue view <N> --json state`, uncapped),
  and **keeps** an entry whose state cannot be read — a failed read never drops a suppression that is
  still owed. (Widening the shared listing is not the fix: `.pr_brs` is capped the same way, so a
  longer open list would expose the dispatcher reaper's `has_pr` to more false negatives — duplicate
  dispatch, worse than a stale entry.)

The self-test's `5f`/`5g` blocks pin all of it: the separate file, the absence of a `checked.txt`
entry, not queued, the set named on a later pass, the targeted per-entry prune dropping a closed
entry and keeping an open one — including against a **saturated** `.open_iss` that omits it — and
keeping (with a log line) an entry whose state could not be read.

### 7.2 Draft PRs are invisible to the sweep — **FIXED in this PR**

The selector was `select(.isDraft|not)` and nothing readied our own lanes' drafts, so a draft ran
its checks, failed `ai-review-gate`, and no worker ever touched it. Observed: **#8219**
(`feat/3524-tm`) and **#8085** (`feat/1037-tm`) sat with failed gates and an already-recorded
`ISSUES` verdict, stranded purely by the flag.

**Fix:** the sweep now includes drafts that match the harness's **own** branch patterns
(`*-tm`, `*-cap`, `fix/*`) and readies them once (`gh pr ready`) before the next cycle processes
them. Foreign drafts are excluded exactly as before — #4212 (`scope/w6b-graph-candidate`) is
another box's draft and is correctly left alone.

### 7.2c Conflicted PRs — **FIXED in this PR**

A CONFLICTING PR can never merge, but the sweep classified it GREEN/PENDING and called `gh pr merge`
on it every cycle, so the queue looked busy while nothing moved (observed: #8245, #8226, #8193, #8104). They are now routed to `fix-one.sh`, which integrates `origin/main`, resolves the conflict, and re-runs the review chain — capped by `MAX_FIX_TRIES` and parkable like any other fix loop. The
fixer prompt states that when a PR is CONFLICTING the conflict *is* the task.

### 7.2b Cross-box duplicate work — **OPEN**

The per-PR claim (§5.1b) is a **local file**, so it makes concurrency safe *within one box*. Two
boxes sweeping the same repo will still both review and attempt to merge the same PR, and both may
pick the same issue. This is inherent: the boxes share GitHub, not a coordinator. tortoise#5052
proposes "an authoritative, expiring claim registry" — the cross-box version of §5.1b.

### 7.3 The harness is unversioned (this directory is the remedy)

Until now it existed only as `~/orchestrator/*.sh` on one machine: no history, no review,
no way to hand it to another box, and no way to know which of the 14 scripts were live. §9
records which are live and which are retired.

### 7.4 Defects found in the first review of this artifact (all fixed here)

These are kept as a record because each one is a *failure mode of the design*, not a typo, and
because the self-test now drives every one of them:

| Defect | Impact | Fix |
|---|---|---|
| `MAX_LOAD` was rendered into the plist and documented as live back-pressure, but never compared to anything | the load ceiling did not exist; a lanes-bound stall logged itself as a load stall | the dispatch gate now tests load first and names the binding gate in `[dispatch] skipped (…)` |
| `SKIPPED`/`NEUTRAL` counted as satisfying **every** required check, including the merge authority | a skipped `ai-review-gate` read as `GREEN` and the PR merged **without any signed review** — a fail-open on the one gate that matters | the classifier requires explicit `SUCCESS` for `ai-review-gate` (absent counts as failed) |
| A lane's PR is driven by `chain-<N>`, whose session name the controller's in-flight guard could not see | two concurrent `merge-one.sh` runs per PR: duplicate evidence comments, duplicate `record-review` writes, `ISSUES`/`CLEAN` interleaving | atomic per-PR claim file, honoured by all three paths |
| `fixcount` was incremented and the findings marker deleted *before* the fixer spawn was attempted | a killed/failed spawn consumed one of `MAX_FIX_TRIES` and destroyed the findings, so no fixer ever ran | spawn first; bookkeep only on success, message the failure otherwise |
| Reaching `MAX_FIX_TRIES` fell through to the re-record branch | an unfixable PR burned a full fresh-context review **every cycle, forever** | terminal `state/parked-<PR>` state, keyed to the head so a new push re-arms it |
| `tmux has-session -t fix-82` prefix-matches `fix-820` | PRs whose number prefixes a running worker's were starved of attention for as long as it lived | exact session-name matching (`tmux ls -F … \| grep -qx`) |
| `refresh-candidates.sh` `cd`-ed into a scout worktree nothing ever created | on a fresh box the refresh exited 1 every cycle and the backlog silently never refilled | provision the worktree and its venv on first use, or fail loudly |
| `install.sh` reported an env-only gate key as present | the daemon silently started without it and the "merge role" install.sh just blessed did not work | only a key *file* is authoritative; env-only is reported as insufficient |
| Plist rendered with unescaped `sed` replacements | a `&` in a path expanded to the matched placeholder, producing a *valid* plist pointing at wrong paths (so `plutil -lint` cannot catch it) | escape `\`, `&` and `\|` in every substituted value |
| `retired/do-merge.sh` logged `rc=$?` after an unquoted `$(date)` | every merge failure logged as `rc=0` | capture the status first |
| `retired/watch-one.sh` built the fixer prompt from a log the reviewer had not written yet | the fixer always ran against an empty FINDINGS section | build the prompt after the reviewer run |
| the pre-fix sweep logged `#<PR> re-record` even when the concurrency cap suppressed the spawn (`{A\|\|B\|\|C;} \|\| D && echo` parses as `({A\|\|B\|\|C} \|\| D) && echo`) | a stalled drain looked busy | explicit `if/elif` branches |
| **this file's own self-test** re-implemented the fixed logic instead of reading the shipped scripts | it passed on the broken copy, so it pinned nothing — and the README claimed it did (found by the History reviewer, who proved 10/16 still passed on the pre-fix tree) | every check now extracts and drives the real classifier, `sess()`, claim block and reaper, or runs `install.sh`; §9/§7 claims corrected |
| `MAX_REVIEW` defaulted to 2 in `orch-loop.sh` but 4 in `install.sh`, the plist it renders, and §4 | running the loop directly (not via the plist) silently halved reviewer concurrency against the documented value; the `${MAX_REVIEW:-4}` fallback was dead code | default is 4 everywhere, and the self-test resolves all 10 limit defaults in both scripts and asserts they agree — including with the limits supplied via the environment, which is how the plist delivers them |
| the live sweep dropped the `CLOSED` guard that `retired/sweep.sh` had, keeping only `MERGED` | a PR listed open that closed in between fell through to `gh pr ready` + `gh pr merge` every cycle and logged `#<PR> merge fail` forever | `case "$PR_ST" in MERGED\|CLOSED) continue ;;` |
| `retired/auto-loop.sh` and `retired/sweep.sh` still carried the fail-open `ai-review-gate` classifier | shipping "the record" with the very bug being fixed invites reuse of it | both annotated in place as retired-with-known-fail-open |
| §3 and §8 admitted cross-box duplication while §5.1b said "one owner per PR" | the invariant was stated more strongly than the code delivers | §5.1b scoped to per-box; §7.2b states the open cross-box gap |
| **a PR branch name was interpolated into the `sh -c` string `tmux new-session` executes** | **arbitrary command execution on the unattended host that holds the merge key.** git permits `'` `;` `$` `` ` `` `&` `\|` `(` `)` `{` `}` `"` `<` `>` `#` in a ref name, so a fork PR on `fix/x';touch${IFS}/tmp/x;#` broke out of the quotes. Reproduced end-to-end before fixing | `safe_ref` allowlist + `check-ref-format`; numeric-only PR ids; and worker args now travel in a file (`--argv`) so no PR-derived value is interpolated at all |
| **the check classifier was a deny-list** (`bad = FAILURE\|ERROR\|CANCELLED\|TIMED_OUT\|ACTION_REQUIRED`) | a conclusion in **neither** list — one GitHub has not documented, or a **null** one — read `PENDING`, and `PENDING` arms the merge (`gh pr merge --auto`): the merge decision failed **open**. At the same time `CANCELLED` was named *red*, so a normally-superseded run (`cancel-in-progress: true`) was routed to `fix-one.sh` or to `gh run rerun` — failing **closed** on a green PR. `fix-one.sh`'s failing-check list named `CANCELLED` too, so such a run burned a capped fixer attempt (agent-infra#1714) | the classifier is an **allow-list with polarity**, the rule `admin-merge.sh` and `record-review.sh` already implement: non-red only for a NAMED conclusion (`success`/`neutral`/`skipped`/`cancelled`/`stale`), in-flight only for a NAMED status, and **everything else RED** — so an unrecognised or null conclusion blocks instead of arming; grouping is per `(app, name)` (newest attempt by `id`) so one app cannot mask another's; and an **empty** surface is `NOCHECKS`, never `GREEN`. `fix-one.sh` uses the same allow-list (agent-infra#1714) |
| the claim-file guard added in the first fix round introduced a permanent stall | the controller treated *the existence of* `mergeclaim-<PR>.lock` as "in flight", but only `merge-one.sh` removes it — a SIGKILLed worker or a reboot mid-merge left the PR skipped forever with no fixer and no re-record | `claim_live()` tests the recorded pid with `kill -0` and removes a dead owner's claim |
| 6 self-test checks passed vacuously because an empty extraction did not fail the check | the round-1 vacuity fix was incomplete: checks depending on `sess()`, the claim block or the reaper passed when the shipped script had no such code | `need()` turns a missing extraction into a FAIL; each check was then re-proved against the pre-fix tree it pins |
| the reviewer prompt told its agent to run `.venv/bin/python -m pytest` in a checkout that has no `.venv` (and, after #8484, to `uv sync` first) | reviewers hunted for an interpreter — `find $HOME -name pyvenv.cfg`, killed at >120s — and 35% of runs (7/7 in one window) ended at the 1800s cap with **no verdict**; 43/124 runs, 4 PRs false-parked | the reviewer is told tests are the fixer's and CI's job and not to run them or provision an environment here (non-vacuity argued by reading the pre-fix code); the prompt is a shipped `reviewer_prompt()` FUNCTION the self-test executes, so a reflow cannot silently restore the sentence |
| a verdict-less review (timeout/crash) and a real `ISSUES` verdict shared one `*)` arm in `merge-one.sh`, both writing `needsfix-<PR>-<head>` | a pure stall authorised a fixer for a review that never happened, spent one of `MAX_FIX_TRIES` on it, and three stalls PARKED the PR "needs a human" with the findings never addressed (7 PRs at `fixcount==3`; agent-infra#1741) | `verdict_kind()` classifies the final message (`clean`/`issues`/`none`) and `apply_verdict_marker()` writes the fixer authorisation for a real `ISSUES` verdict **only**; a no-verdict run is logged as a stall and writes no marker, so the next cycle re-records instead of inventing a fixer task (#8602) |
| **a hung worker held a concurrency slot for 9 hours** | `pi -p` has no timeout and the caps count **tmux sessions**, so a hung reviewer (or a runaway `find /` it spawned) permanently reduced merge throughput while the queue looked busy — three slots were dead weight overnight on 2026-10-12 | portable `run_agent()` wall-clock limit (bash `wait` + watchdog subshell, since macOS ships no `timeout`) plus a `reap_stale_workers()` sweep that kills any worker silent for `STALE_MIN` minutes |
| **only the controller's DECISION was load-gated; the `pi` spawns were not** (agent-infra#1720) | `chain-<N>` — the primary first-review path, and the one path the controller stands down for — waits up to 90 min for its PR, so its reviewer launched onto the load that existed *then*, not the quiet load the lane was dispatched under, and `fix-one.sh`/`adopt-fix.sh` reached the same ungated spawn while §4 claimed all of it was gated | the one `pi` invocation moved into `spawn-gate.sh::run_agent()`, which takes a **fresh** reading and refuses above `MAX_LOAD` — so the gate is at the spawn, and a fourth launch path is refused by construction. A refusal is `AGENT_LOAD_REFUSED` and is **not** a verdict: `merge-one.sh` exits without writing `needsfix-<PR>-<head>`, because recording one would authorise a fixer for a review that never happened and burn a `MAX_FIX_TRIES` attempt (the #1716 class). The self-test drives all three worker entry points under a stubbed high load and asserts no agent started, and asserts exactly one shipped file invokes `pi`. The ceiling, the core-count probe and `load1()` all live in the helper now, so the controller's decision and the worker's spawn cannot disagree |
| **a load-refused FIXER still consumed a `MAX_FIX_TRIES` attempt, and the controller had deleted the reviewer's findings marker before the gate ran** (#1720, review round) | the controller books the attempt on **session creation** (`orch-loop.sh`), which is upstream of the gate inside `fix-one.sh`; a refusal did no work yet left the counter incremented and `needsfix-<PR>-<head>` removed. Three refusals — the normal regime on a box running at load 95–175 against `MAX_LOAD=100` — reached the terminal branch and **parked a PR that never had a fixer run**, the #1716 class this change set out to close | `fix-one.sh` **refunds** `fixcount-<PR>` on a refusal (controller-dispatched runs only) and the controller no longer deletes the findings marker at dispatch: `fix-one.sh` consumes it itself, once the agent has started. Both halves are driven in §9's self-test with a pre-seeded counter and marker; the refund check fails on the pre-fix fixer |
| **`adopt-fix.sh` inverted its own exit status** (#1720, review round) | the new refusal-reporting line became the script's **last** command, so its status was the `[ … ] && echo` compound: `1` on a clean success (the path that used to exit `0`) and `0` on the load refusal it exists to report. Verified: `bash -c 'true; rc=$?; [ "$rc" = "75" ] && echo x'; echo $?` → `1` | the report is an `if … fi` and the script ends `exit "$rc"`; the self-test drives the shipped script with a stub fixer at rc 0 / 75 / 3 and asserts the status follows the fixer |
| **only `ai-review-gate` and `python-ci-gate` failures had a remediation path** | a PR red on any *other* required check (`docs`, `test-isolation`, `license-surface`, `legal-e2e`, `pricing-artifact`) was merely logged, so it sat red forever with **no worker at all**. Observed: #8278 had *unresolved conflict markers committed* (caught by the docs job's marker check) and #8193 had a new `MD018` docs-lint finding — both with zero workers for hours | those checks now route to `fix-one.sh` too, capped and parkable like every other fix loop; and `fix-one.sh` now fetches the failing check's **log** into the prompt, because it previously only ever read a reviewer comment — for a CI failure there may be no comment at all |

### 7.5 Still open, deliberately not fixed here

- **`state/` and `wip/` grow without bound.** Nothing prunes `merged-*.done`, `needsfix-*`,
  `fixcount-*`, `mergeclaim-*.lock`, `parked-*` or expired `rerun-*`. Needs a retention rule rather
  than an ad-hoc `find -mtime`.
- **`[ -z "$WT" ] && { … }` as a script's last statement** makes the exit status that of the
  test. The shipped `fix-one.sh` ends with a positive `echo` so it no longer leaks a non-zero
  status — but the pattern is easy to reintroduce, and `adopt-fix.sh` did exactly that (its new
  refusal report became the last command, inverting the status: `1` on success, `0` on the refusal
  it reports). `adopt-fix.sh` now ends `exit "$rc"` and the self-test drives that. The general fix
  is to end every script on an explicit status — `exit "$rc"` where a caller reads it (as here),
  `exit 0` where the last line is only reporting.
- **Worktree reaping.** Every dispatch and every foreign-branch fixer adds a worktree; the box
  carried ~150 by the end of the run. Nothing removes them after their PR merges. The reviewer's
  sandbox is the exception — `merge-one.sh`'s EXIT trap removes it on every exit, normal or
  trapped — but a worker that is SIGKILLed (or the box that dies) leaves one behind, like every
  other worktree. That trap deregisters **only its own** record (the sandbox's own `gitdir:` name);
  repo-wide pruning is the #1095 reaper's job, because a bare `git worktree prune` deregisters every
  record whose directory is not stat-able at that instant and can orphan a sibling lane's checkout.

---

## 8. Measured results

Branch-signature proxy: this box opens `*-tm` / `*-cap` / `*-night` / `*-evening` branches;
everything else in these counts is a peer box. Attribution by branch name is a proxy, not a
certificate — but the split is unambiguous.

| Window | This box | Peer | Notes |
|---|---|---|---|
| 2026-10-10 → 2026-10-12 (48 h) | **94 merged** | 41 merged | |
| 2026-10-11T04Z → 2026-10-12T05Z (24 h) | 51 merged | 27 merged | hour-by-hour below |
| Peak hour (2026-10-11T18Z) | **8 merged** | 0 | 8 PRs in 60 minutes |
| The 2026-10-10T23:45Z → 2026-10-11T21:22Z run | 77 → **99 merged** | — | this box's own handover record |

Hourly histogram (00:00Z–23:00Z on 2026-10-11, merged / this-box):

```text
04: 1/1   05: 4/3   06: 3/2   07: 5/5   09: 3/3   10: 3/2   11: 3/3
13: 2/1   14: 3/2   15: 4/2   16: 6/5   17: 7/5   18: 8/8   19: 3/3
20: 3/1   21: 3/1   22: 5/4   23: 2/1
```

Observed steady state at 8 GB: 15 concurrent workers (4 lanes + 4 chains + 3 fixers +
4 reviewers), load 7–18, swap 588 M–832 M free, dispatch throttled by the swap gate.

**Honest caveat on the comparison.** Two boxes running this harness against one repo
duplicate work unless they coordinate: both may pick the same issue, and both run the merge
sweep over the *same* PR queue. A large part of the throughput above is the harness keeping
a box saturated for hours with no human, plus the box holding the merge key — not an
algorithmic advantage. The claim worth evaluating is *utilisation*, not *efficiency per PR*.

---

## 9. What is shipped

**Live** (reachable from `orch-loop.sh`):

| Script | Role |
|---|---|
| `orch-loop.sh` | the controller: merge sweep → capacity-gated dispatch → backlog refresh |
| `spawn-gate.sh` | **the ONE load gate and the ONE `pi` invocation**; sourced by the controller and by every worker |
| `dispatch-merge.sh` | creates the worktree and starts the `lane-*` + `chain-*` pair for an issue |
| `lane.sh` | the implementation worker (prompt template included) |
| `chain-merge.sh` | waits for the lane's PR, then runs the merge chain |
| `merge-one.sh` | fresh review → signed record → wait green → merge; marks `needsfix-*` |
| `fix-one.sh` | fixes a reviewer's findings, pushes, re-runs the merge chain |
| `refresh-candidates.sh` | pre-flights issues (collision check) into the backlog |
| `merged-not-closed.sh` | reports open issues that already have a MERGED PR (VERIFY-then-close; never auto-closes) |
| `selftest.sh` | offline checks; each one drives the shipped code. Not part of the loop |

**Manual utility:** `adopt-fix.sh` — run `fix-one.sh` against a specific PR by hand.

**Retired** (`retired/`, kept so nothing is lost): `auto-loop.sh`, `dispatch.sh`,
`do-merge.sh`, `refix.sh`, `sweep.sh`, `watch-one.sh`. These are earlier iterations of the
same idea and are no longer invoked; they are included for the record, not for use.
`refix.sh` and `watch-one.sh` still call `pi` directly, so they are **not** load-gated — each
carries a banner saying so, and the self-test fails if a retired `pi`-invoking script loses it
(§4). Re-activating one means routing its spawn through `spawn-gate.sh` first.

**Portability edits applied to every shipped script.** Three per-box constants became
overridable with this box's values as defaults (the launchd `PATH` is no longer one of them —
`install.sh` renders the `__PATH__` placeholder from the directories `tmux`/`gh`/`uv`/`pi` actually
resolve to, refusing any `*/shims`, `*/shims/*` or `*/.pi/*` directory because macOS TCC makes such
a symlink EPERM under launchd, and then probes each tool by executing it):

```bash
OR="${OR:-daniel-ospina/tortoise}"
REPO="${REPO:-/Users/danielospina/Documents/GitHub/tortoise}"
O="${O:-$HOME/orchestrator}"
```

The shipped copies are **not** byte-identical to the ones running on the original box: they are
those copies **plus every §7.4 fix**. `install.sh` compares all 9 live scripts against the running
copies at `$O` and warns loudly on drift, because the daemon executes `$O/<script>` — a fix that
only lands in this directory is not in effect. `spawn-gate.sh` is on that list too: the workers
source it, so a stale copy at `$O` silently restores the ungated spawn. If you are adopting this
onto an existing box, sync them (`cp <harness>/*.sh $O/`) and restart the loop before trusting any
§5 invariant.

⛔ **Copying the scripts is not enough when a *limit* changed.** The launchd plist carries its own
environment block, rendered by `install.sh` from these defaults, and that environment **overrides the
script's own**. A job already loaded with `MAX_LOAD=16` keeps `16` no matter what `orch-loop.sh` now
defaults to. `install.sh` overwrites the plist *file* but does not reload an already-loaded job, and
`launchctl load -w` on a job that is already loaded does not reliably re-read it. So for a limits
change: re-run `./install.sh`, then `launchctl unload -w <plist> && launchctl load -w <plist>` — or
reboot, which is the only thing that reliably re-reads it — before trusting the new value.

Everything else is still tortoise-shaped and must be changed on another target: the branch
suffix (`tm`), `feat/<N>-tm` naming, `uv sync --extra embeddings --extra parity`, the
`TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest` invocation, the
`tools/collision_preflight.py` path, and the seven required check names
(`ai-review-gate python-ci-gate docs test-isolation license-surface legal-e2e pricing-artifact`).

---

## 10. Open questions for the evaluator

1. Does this belong in `agent-infra` (symlink-distributed via `manifest.json` + `sync.sh`,
   pull-only) rather than in a repo's `operations/`?
2. Should the two boxes coordinate through an explicit claim registry instead of branch-name
   heuristics? tortoise#5052 already proposes "an authoritative, expiring claim registry" —
   §7.1 is the same problem seen from the dispatcher's side.
3. Is a `~/`-rooted bash harness the right shape at all, or should this fold into the fleet
   rail's cmux workspaces (agent-infra#1683)?
