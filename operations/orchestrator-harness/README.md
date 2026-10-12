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
| `merge-<PR>` | `merge-one.sh` | — | fresh review → signed record → wait green → squash-merge |
| `fix-<PR>` | `fix-one.sh` | the PR's worktree | repair findings **or a failing required check**, push, re-run the merge chain |

### State files (`$O/`)

| File | Meaning |
|---|---|
| `heartbeat` | epoch seconds, rewritten every cycle — the liveness probe |
| `.orch.lock` | atomic single-instance lock (noclobber create); stale locks are reclaimed |
| `candidates.txt` | the dispatch backlog: issue numbers that passed collision pre-flight |
| `dispatched.txt` | issues already handed to a lane. **Append-only — never expires** (see §7.1) |
| `checked.txt` | issues already pre-flighted; prevents re-running the collision check |
| `state/mergeclaim-<PR>.lock` | single-owner claim: only one worker may review/record/merge a given PR |
| `state/needsfix-<PR>-<head>` | "a reviewer found real issues at this exact head" |
| `state/fixcount-<PR>` | fixer attempts for that PR, capped by `MAX_FIX_TRIES` |
| `state/parked-<PR>` | terminal state: the fixer cap was reached at the head recorded inside the file, so the sweep skips it until a new push re-arms it |
| `state/rerun-<PR>-<runid>` | CI re-run already requested for this run — prevents loops |
| `state/merged-<PR>.done` | merge confirmation |
| `state/last-open-n` | the last SUCCESSFUL open-PR count; reused only when positive if a later count fails — a failure never reads as 0 |
| `logs/` | one log per worker: `lane-*.log`, `chain-*.log`, `merge-*.log`, `fix-*.log`, `orch-loop.log` |
| `wip/` | dirty-worktree captures taken before a shutdown (see §6) |

### Why the *reviewer* is a separate process from the *controller*

`merge-one.sh` spawns a fresh-context `pi -p` reviewer that has never seen the implementing
lane's reasoning. It reads the changed files at the PR head (`git show origin/<branch>:<path>`)
rather than only diff hunks, and posts its verdict as a PR comment. That comment is the
evidence the `record-review.sh` signature points at, so the merge gate is auditable after
the fact: the PR body names the comment it was signed against.

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

### Tuning (8 GB / 8-core box — the values that were actually used)

| Var | Value | Why |
|---|---|---|
| `MAX_LOAD` | `16` | dispatch stops above this 1-min load |
| `MAX_LANES` | `4` | concurrent implementation lanes |
| `MAX_REVIEW` | `4` | concurrent `merge-<PR>` reviewers |
| `MERGE_PRIORITY_AT` | `10` | above this many open PRs, **merging takes priority**: dispatch of new lanes stops (unless none are running) and reviewer headroom rises to `MAX_REVIEW+2` |
| `MAX_FIX` | `3` | concurrent `fix-<PR>` fixers |
| `MAX_FIX_TRIES` | `3` | per-PR fixer attempts before it is left alone |
| `MIN_SWAP_FREE` | `600` | MB; **the binding gate in practice** |
| `BATCH` | `2` | issues dispatched per cycle |
| `CYC` | `240` | seconds between cycles |
| `STALE_MIN` | `40` | a worker whose log has not grown in this many minutes is presumed hung and its session is killed (§6) |
| `LANE_TIMEOUT` | `2400` | wall-clock limit on a lane's `pi` run (s) |
| `REVIEW_TIMEOUT` | `1800` | wall-clock limit on a reviewer `pi` run (s) |
| `FIX_TIMEOUT` | `2400` | wall-clock limit on a fixer `pi` run (s) |

**Swap, not lanes, is what actually throttles this box.** Observed at 15 concurrent workers:
`swap_free=588M` → dispatch correctly refused. Raising `MAX_LANES` alone buys nothing on 8 GB;
it just moves the pressure into the compressor. `swap_total=0` right after boot is treated as
*uninitialised*, not as pressure (that bug silently blocked all dispatch for the first minutes
after a restart until it was fixed).

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
   into a merge.
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

- **Not sandboxed.** The spawned reviewer and fixer run with `AGENT_SKIP_REVIEW_GATE=1`,
  `ELDATO_SKIP_VGATE=1`, `AGENT_ALLOW_MAIN_EDITS=1`, `SKILL_ENFORCER_DISABLED=1` and
  `LOOP_ENFORCER_DISABLED=1`, with `bash` and `gh` on `PATH`. "Do NOT merge" and "do not touch the
  main checkout" are **prompt instructions, not controls**.
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
[cap]   lanes=4 swap_free=588M swap_total=4096M load=7
[dispatch] load=7 lanes=4 -> 4429 4489
[refresh] +#4508 CLEAN -> backlog
#8219 FAILED:ai-review-gate          ← per-PR verdict for the required checks
#8192 fixer attempt 1                ← routed to a fixer, not a re-record
[dispatch] skipped (load=7 lanes=4)  ← and why
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
| `MAX_REVIEW` defaulted to 2 in `orch-loop.sh` but 4 in `install.sh`, the plist it renders, and §4 | running the loop directly (not via the plist) silently halved reviewer concurrency against the documented value; the `${MAX_REVIEW:-4}` fallback was dead code | default is 4 everywhere, and the self-test asserts all 8 limit defaults agree between the two scripts |
| the live sweep dropped the `CLOSED` guard that `retired/sweep.sh` had, keeping only `MERGED` | a PR listed open that closed in between fell through to `gh pr ready` + `gh pr merge` every cycle and logged `#<PR> merge fail` forever | `case "$PR_ST" in MERGED\|CLOSED) continue ;;` |
| `retired/auto-loop.sh` and `retired/sweep.sh` still carried the fail-open `ai-review-gate` classifier | shipping "the record" with the very bug being fixed invites reuse of it | both annotated in place as retired-with-known-fail-open |
| §3 and §8 admitted cross-box duplication while §5.1b said "one owner per PR" | the invariant was stated more strongly than the code delivers | §5.1b scoped to per-box; §7.2b states the open cross-box gap |
| **a PR branch name was interpolated into the `sh -c` string `tmux new-session` executes** | **arbitrary command execution on the unattended host that holds the merge key.** git permits `'` `;` `$` `` ` `` `&` `\|` `(` `)` `{` `}` `"` `<` `>` `#` in a ref name, so a fork PR on `fix/x';touch${IFS}/tmp/x;#` broke out of the quotes. Reproduced end-to-end before fixing | `safe_ref` allowlist + `check-ref-format`; numeric-only PR ids; and worker args now travel in a file (`--argv`) so no PR-derived value is interpolated at all |
| the claim-file guard added in the first fix round introduced a permanent stall | the controller treated *the existence of* `mergeclaim-<PR>.lock` as "in flight", but only `merge-one.sh` removes it — a SIGKILLed worker or a reboot mid-merge left the PR skipped forever with no fixer and no re-record | `claim_live()` tests the recorded pid with `kill -0` and removes a dead owner's claim |
| 6 self-test checks passed vacuously because an empty extraction did not fail the check | the round-1 vacuity fix was incomplete: checks depending on `sess()`, the claim block or the reaper passed when the shipped script had no such code | `need()` turns a missing extraction into a FAIL; each check was then re-proved against the pre-fix tree it pins |
| **a hung worker held a concurrency slot for 9 hours** | `pi -p` has no timeout and the caps count **tmux sessions**, so a hung reviewer (or a runaway `find /` it spawned) permanently reduced merge throughput while the queue looked busy — three slots were dead weight overnight on 2026-10-12 | portable `run_agent()` wall-clock limit (bash `wait` + watchdog subshell, since macOS ships no `timeout`) plus a `reap_stale_workers()` sweep that kills any worker silent for `STALE_MIN` minutes |
| **only `ai-review-gate` and `python-ci-gate` failures had a remediation path** | a PR red on any *other* required check (`docs`, `test-isolation`, `license-surface`, `legal-e2e`, `pricing-artifact`) was merely logged, so it sat red forever with **no worker at all**. Observed: #8278 had *unresolved conflict markers committed* (caught by the docs job's marker check) and #8193 had a new `MD018` docs-lint finding — both with zero workers for hours | those checks now route to `fix-one.sh` too, capped and parkable like every other fix loop; and `fix-one.sh` now fetches the failing check's **log** into the prompt, because it previously only ever read a reviewer comment — for a CI failure there may be no comment at all |

### 7.5 Still open, deliberately not fixed here

- **`state/` and `wip/` grow without bound.** Nothing prunes `merged-*.done`, `needsfix-*`,
  `fixcount-*`, `mergeclaim-*.lock`, `parked-*` or expired `rerun-*`. Needs a retention rule rather
  than an ad-hoc `find -mtime`.
- **`[ -z "$WT" ] && { … }` as a script's last statement** makes the exit status that of the
  test. The shipped `fix-one.sh` ends with a positive `echo` so it no longer leaks a non-zero
  status, but the pattern is easy to reintroduce — a `exit 0` at the end of each script would pin it.
- **Worktree reaping.** Every dispatch and every foreign-branch fixer adds a worktree; the box
  carried ~150 by the end of the run. Nothing removes them after their PR merges.

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
| `dispatch-merge.sh` | creates the worktree and starts the `lane-*` + `chain-*` pair for an issue |
| `lane.sh` | the implementation worker (prompt template included) |
| `chain-merge.sh` | waits for the lane's PR, then runs the merge chain |
| `merge-one.sh` | fresh review → signed record → wait green → merge; marks `needsfix-*` |
| `fix-one.sh` | fixes a reviewer's findings, pushes, re-runs the merge chain |
| `refresh-candidates.sh` | pre-flights issues (collision check) into the backlog |
| `selftest.sh` | offline checks; each one drives the shipped code. Not part of the loop |

**Manual utility:** `adopt-fix.sh` — run `fix-one.sh` against a specific PR by hand.

**Retired** (`retired/`, kept so nothing is lost): `auto-loop.sh`, `dispatch.sh`,
`do-merge.sh`, `refix.sh`, `sweep.sh`, `watch-one.sh`. These are earlier iterations of the
same idea and are no longer invoked; they are included for the record, not for use.

**Portability edits applied to every shipped script.** Three per-box constants became
overridable with this box's values as defaults (the launchd `PATH` is no longer one of them —
`install.sh` renders the `__PATH__` placeholder from the directories `tmux`/`gh`/`uv`/`pi` actually
resolve to, refusing any `*/shims`, `*/shims/*` or `*/.pi/*` directory because macOS TCC makes such
a symlink EPERM under launchd, and then probes each tool by executing it):

```bash
OR="${OR:-daniel-ospina/tortoise}"
REPO="${REPO:-/Users/home/eldato/tortoise}"
O="${O:-$HOME/orchestrator}"
```

The shipped copies are **not** byte-identical to the ones running on the original box: they are
those copies **plus every §7.4 fix**. `install.sh` compares all 8 live scripts against the running
copies at `$O` and warns loudly on drift, because the daemon executes `$O/<script>` — a fix that
only lands in this directory is not in effect. If you are adopting this onto an existing box, sync
them (`cp <harness>/*.sh $O/`) and restart the loop before trusting any §5 invariant.

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
