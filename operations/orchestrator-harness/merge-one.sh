#!/usr/bin/env bash
# merge-one.sh <repo-path> <owner/repo> <pr> <branch>
# merge-one.sh --argv <file>            (4 lines: repo, owner/repo, pr, branch)
# Full chain for ONE PR: fresh-context review -> evidence comment -> record-review -> wait green -> merge.
# This box holds the AI_REVIEW_GATE_KEY, so it owns the merge authority for its own PRs.
set -u
# --argv exists because these values are attacker-influenced (a PR branch name may contain quotes,
# semicolons, $ and backticks) and the controller must launch us from a tmux `sh -c` string.
# Passing them through a file keeps every PR-derived value out of that string entirely.
if [ "${1:-}" = "--argv" ]; then
  ARGV="${2:-}"
  [ -f "$ARGV" ] || { echo "[merge] missing argv file: $ARGV" >&2; exit 1; }
  { read -r REPO; read -r OR; read -r PR; read -r BR; } < "$ARGV"
  rm -f "$ARGV"
else
  REPO="$1"; OR="$2"; PR="$3"; BR="$4"
fi
case "${PR:-}" in ''|*[!0-9]*) echo "[merge] refusing non-numeric PR: ${PR:-<empty>}" >&2; exit 1 ;; esac
O="${O:-$HOME/orchestrator}"; mkdir -p "$O/logs" "$O/state"
L="$O/logs/merge-$PR"
# The wall-clock limit AND the load gate both live in the shared helper, so this script has no
# `pi` invocation of its own to forget to gate (agent-infra#1720). NB: L above is a LOG PATH, not a
# load reading — the gate takes its reading as an argument for exactly that reason.
# Fail LOUD if the helper is missing: an unsourced helper leaves `run_agent` undefined, and that must
# never degrade into a verdict (see README §9 on syncing $O).
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SELF/spawn-gate.sh" || { echo "[merge $PR] FATAL: $SELF/spawn-gate.sh missing — refusing to run an ungated reviewer" >&2; exit 78; }
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
# ⛔ THE MAIN-EDITS HATCH IS DELIBERATELY *NOT* EXPORTED HERE (#8484). It used to be: this script
# exported the hatch AND skipped VGATE, and then ran the reviewer with cwd = the shared checkout
# — i.e. it switched OFF the one control that would have refused a write there, and left
# "do not touch the main checkout" as a prompt written to a model with shell access. The reviewer
# now gets its own checkout (see review_sandbox below), so the hatch buys nothing and its absence
# is the SECOND layer: a reviewer that wanders back into the shared checkout is REFUSED by
# main-worktree-guard instead of obeyed. fix-one.sh keeps the hatch — a fixer writing to the tree
# IS its job.
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1
# ⛔ NOT EXPORTING IS NOT ENOUGH — the hatch is UNSET here, not merely omitted. An exported variable
# is inherited across `bash <script>`, and fix-one.sh:30 exports the hatch and then runs THIS script
# as a child (fix-one.sh:137), so on the re-review that follows EVERY fixer run the reviewer started
# with the hatch OPEN and main-worktree-guard OBEYED it — the containment this script advertises was
# inert on its most common path (PR #8589 review). The property must belong to this script, not to a
# caller that happens to be clean.
unset AGENT_ALLOW_MAIN_EDITS ELDATO_ALLOW_MAIN_EDITS 2>/dev/null || true
export PI_MCP_SERVERS=none   # headless reviewer uses no MCP servers — saves ~25 processes/run
export PI_SKIP_VERSION_CHECK=1 SKILL_ENFORCER_DISABLED=1 LOOP_ENFORCER_DISABLED=1 SLACK_BRIDGE_DISABLE=1 VISION_INTERCEPTOR_DISABLED=1
cd "$REPO" || exit 1

git fetch origin "$BR" --quiet 2>/dev/null || true   # external (other-machine) branches may not be fetched locally

# ── review_helpers: the reviewer's OWN checkout (#8484) ───────────────────────
# #8484 measured what prompt-only containment costs: a fresh-context reviewer that mutated the tree
# it was reading, COMMITTED, merged origin/main and PUSHED — all unprompted — and absorbed the
# controller's unstaged edits on the way. Nothing in that flow asked for a ref move, and the only
# thing guarding it had been a sentence in the prompt.
#
# The reviewer now runs in a DETACHED worktree at the exact head under review. Detached is the point:
# a commit made there moves NO branch, so the worst a runaway reviewer can do to the reviewed ref is
# nothing. The tree is created OUTSIDE the repo so nothing it writes lands under the checkout as even
# an ignored path, and it is removed on exit so a leaked mutation is discarded WITH the tree instead
# of being swept into someone's commit. The containment is the mount, not the prompt.
# The ADMIN RECORD that git keeps for OUR sandbox — and never a scan of anyone else's. Two ways to
# find it: (a) the tree is still on disk and its `.git` file names the record outright; (b) the
# directory is gone but the record survived, and git names the record after the sandbox basename.
sandbox_record() { # sandbox_record <sandbox-dir> — print the admin dir, or nothing
  local d="${1:-}" g gcd
  [ -n "$d" ] || return 0
  g="$(sed -n 's/^gitdir: //p' "$d/.git" 2>/dev/null)"
  if [ -n "$g" ]; then printf '%s' "$g"; return 0; fi
  gcd="$(git -C "${REPO:-.}" rev-parse --git-common-dir 2>/dev/null)"
  case "$gcd" in ''|'.') gcd="${REPO:-.}/.git" ;; /*) ;; *) gcd="${REPO:-.}/$gcd" ;; esac
  [ -d "$gcd/worktrees/${d##*/}" ] && printf '%s' "$gcd/worktrees/${d##*/}"
  return 0
}

# Deregister exactly ONE record — OURS — and only after its own `gitdir` file names our sandbox.
# ⛔ A bare `git worktree prune` is BANNED here (agent-infra docs/ops/load-policy.md:491; repo-wide
# pruning is the #1095 reaper's job, and skills/code-review/SKILL.md forbids the shape for exactly
# this reason): it deregisters EVERY record whose directory is not stat-able at that instant
# (unmounted volume, permission blip, stale network mount), so a routine probe silently orphans an
# unrelated sibling lane's checkout while its files sit on disk. It is also unnecessary here — the
# record is ours by name, so it can be removed by name.
drop_sandbox_record() { # drop_sandbox_record <sandbox-dir>
  local d="${1:-}" rec g
  [ -n "$d" ] || return 0
  rec="$(sandbox_record "$d")"
  case "$rec" in */worktrees/*) ;; *) return 0 ;; esac
  # NB: the record's own `gitdir` file holds the path PLAIN (no `gitdir: ` prefix, unlike the
  # worktree's `.git` file); strip the prefix too in case a future git adds it.
  g="$(sed -n '1{s/^gitdir: //;p;}' "$rec/gitdir" 2>/dev/null)"
  [ -n "$g" ] || return 0
  case "$g" in "$d"/.git|*/"${d##*/}"/.git) ;; *) return 0 ;; esac
  rm -rf "$rec"
  return 0
}

review_sandbox() { # review_sandbox <ref|sha> — print the sandbox path, or nothing on failure
  [ -n "${1:-}" ] || return 1
  local base slug dir
  # Name the sandbox from digits plus a SANITIZED slug only: the path is `rm -rf`d below, so no
  # caller-supplied text may reach it as a separator, a `..` or a leading `-`.
  base="$(printf '%s' "$1" | cut -c1-40)"
  slug="$(printf '%s' "$base" | tr -c 'A-Za-z0-9._-' '_')"
  [ -n "$slug" ] || return 1
  dir="$O/review/$PR-$slug"
  case "$dir" in "$O"/review/*) ;; *) return 1 ;; esac
  mkdir -p "$O/review" 2>/dev/null || return 1
  # A sandbox at the SAME head is stale by construction (the head names it): a crashed run's leftover
  # is REMOVED, never reused — a reused tree could still carry the previous reviewer's mutant.
  if [ -e "$dir" ]; then
    git -C "$REPO" worktree remove --force "$dir" >/dev/null 2>&1 || rm -rf "$dir"
  fi
  # ⛔ A RECORD WITHOUT ITS TREE BRICKS THIS HEAD FOREVER. A registration keyed to this same sandbox
  # name survives a crashed run, and with the directory gone `worktree add` refuses ("missing but
  # already registered worktree") — measured with the shipped helper. review_sandbox then prints
  # nothing, merge-one.sh exits 6, and exit 6 writes NO needsfix marker, so the controller
  # re-dispatches a PR that can never be reviewed, every cycle, with no terminal state. Clear OUR OWN
  # record first — by name, never a repo-wide prune (see drop_sandbox_record).
  drop_sandbox_record "$dir"
  git -C "$REPO" worktree add --detach "$dir" "$base" >/dev/null 2>&1 || { rm -rf "$dir"; drop_sandbox_record "$dir"; return 1; }
  printf '%s' "$dir"
}

# The reviewer's checkout must never outlive this run. `worktree remove` drops git's admin entry AND
# the tree, but its exit status is NOT trusted and neither step is conditional on the other: a
# sandbox that survives the run is the leak this exists to prevent, whatever git reported. (A tree
# the reviewer deleted or damaged makes `remove` refuse — hence the rm + the TARGETED deregistration
# behind it.) It runs in the EXIT trap of a script that fires on every merge, on the shared checkout,
# so it may not touch any record that is not its own: a bare `git worktree prune` here was a P1
# finding and is BANNED (agent-infra docs/ops/load-policy.md:491, skills/code-review/SKILL.md).
review_cleanup() {
  [ -n "${SANDBOX:-}" ] || return 0
  git -C "${REPO:-.}" worktree remove --force "$SANDBOX" >/dev/null 2>&1
  [ -e "$SANDBOX" ] && rm -rf "$SANDBOX"
  # `remove` drops the admin entry when it succeeds. When it refused (a damaged tree) the record
  # survives the rm -rf above and would brick the next provisioning at this head — so drop OURS, by
  # name. Never a repo-wide prune: see drop_sandbox_record.
  drop_sandbox_record "$SANDBOX"
  return 0
}

# The reviewed branch's local + remote shas. Sampled BEFORE and AFTER the reviewer, so a ref move
# the controller did not request is LOUD instead of absorbed — and a review is never recorded
# against a head that is no longer the branch tip.
ref_shas() { # ref_shas <branch> — "<local> <remote>", "-" for an absent ref
  printf '%s %s' \
    "$(git -C "$REPO" rev-parse -q --verify "refs/heads/$1" 2>/dev/null || echo -)" \
    "$(git -C "$REPO" rev-parse -q --verify "refs/remotes/origin/$1" 2>/dev/null || echo -)"
}

# WHICH of the two sampled refs actually moved, and its sha. Taking the LOCAL field first (the
# pre-#8589 shape) mis-attributes the mover: a reviewer that pushes updates
# `refs/remotes/origin/<br>` but NOT `refs/heads/<br>`, so the local field is the UNCHANGED tip that
# was already under review — the log then named the wrong commit and the wrong actor. Diagnostic
# only (the refusal itself was right), but a mis-attribution is worse than none when the whole point
# is to name who moved the branch.
mover_of() { # mover_of <pre "<local> <remote>"> <post> <branch> — "<ref> <sha>"
  local pre_l pre_r post_l post_r
  pre_l="${1%% *}"; pre_r="${1##* }"
  post_l="${2%% *}"; post_r="${2##* }"
  if [ "$post_l" != "$pre_l" ]; then printf 'refs/heads/%s %s' "$3" "$post_l"; return 0; fi
  printf 'refs/remotes/origin/%s %s' "$3" "$post_r"
}

# ── the reviewer's verdict, and what it authorises ────────────────────────────
# Classify the reviewer's FINAL message. A function rather than an inline `case` so the self-test
# EXECUTES the shipped rule: a `grep` for its text stays green when an arm is deleted or reworded
# (the same lesson as orch-loop.sh's p0_merge_priority_override, #8469 review P2).
# `none` means there was NO verdict — the reviewer timed out, crashed, or was killed at
# $REVIEW_TIMEOUT. It is NOT "the reviewer found issues", and the two must never share a caller
# arm: that conflation is what turned a stall into a false park (#8602, agent-infra#1741).
verdict_kind() {
  case "${1:-}" in
    CLEAN*)  echo clean ;;
    ISSUES*) echo issues ;;
    *)       echo none ;;
  esac
  return 0
}

# Apply the reviewer's outcome marker. `needsfix-<PR>-<head>` is the ONLY thing that authorises
# orch-loop.sh to spawn a FIXER at this head, so it is written for a real `ISSUES` verdict and for
# nothing else. A verdict-less run writes NO marker: with one, a pure stall consumes one of
# MAX_FIX_TRIES, and three stalls park the PR "needs a human" with the reviewer's findings never
# addressed (measured: 7 PRs at fixcount==3, 4 false-parked — #8602 indicator (c)).
# Echoes the marker path when it wrote one.
apply_verdict_marker() {
  local kind="${1:-}" head="${2:-}" m
  [ "$kind" = "issues" ] || return 0
  [ -n "$head" ] || return 0
  m="$O/state/needsfix-$PR-$head"
  touch "$m" 2>/dev/null && printf '%s' "$m"
  return 0
}

# ── single-owner claim ────────────────────────────────────────────────────────
# Three paths reach this script: chain-<N> (waits for a lane's PR), fix-<PR>, and the
# controller's merge sweep. Only the last two are visible to the controller by session name,
# so without a shared claim a freshly opened PR gets TWO concurrent reviewers racing the same
# head: duplicate evidence comments, duplicate record-review writes, and an
# ISSUES/CLEAN interleaving where one worker's needsfix marker lands after the other merged.
# Atomic noclobber create is the lock; the controller also tests this file.
CLAIM="$O/state/mergeclaim-$PR.lock"
claim() { ( set -o noclobber; echo $$ > "$CLAIM" ) 2>/dev/null; }
if ! claim; then
  OPID="$(cat "$CLAIM" 2>/dev/null || echo 0)"
  if [ "${OPID:-0}" -gt 0 ] && kill -0 "$OPID" 2>/dev/null; then echo "[merge $PR] pid $OPID already owns this PR — exiting" >> "$L.log"; exit 0; fi
  rm -f "$CLAIM"
  claim || { echo "[merge $PR] claim race — exiting" >> "$L.log"; exit 0; }
fi
# ⛔ Same defect as orch-loop.sh, on the per-PR claim (§5.1b): a RETURNING INT/TERM handler
# resumes the interrupted command loop, so a TERM removed the claim while this worker kept
# reviewing and merging, and the controller could spawn a second worker for the same head. The
# handlers TERMINATE; the claim is released by the EXIT trap alone. Measured on /bin/bash 3.2.57:
# `exit 143` from a TERM handler still runs the EXIT trap (rc=143); an untrapped INT is ignored
# outright (still alive after 3s), so INT is trapped rather than dropped. (agent-infra#1715)
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'rm -f "$CLAIM"; review_cleanup' EXIT

# ── the load gate, consulted BEFORE any work (#8484) ──────────────────────────
# run_agent() applies this same predicate moments before it spawns — but it spawns AFTER the
# sandbox is provisioned, and a `git worktree add` is a full checkout of the repo. Doing that on a
# box the gate is about to refuse is real work (and a real `.git/worktrees` entry, and real disk)
# for a review that never happens, on the machine least able to afford it. Same shared predicate,
# same exit code, same log shape, so the operator cannot tell the two refusal sites apart; run_agent
# still re-reads load at the spawn, because that is the reading that governs.
_load="$(load1)"
if ! can_spawn "$_load"; then
  echo "[load-gate] REFUSED reviewer for PR #$PR: load=$_load >= max_load=$MAX_LOAD — no agent started, no review sandbox created, nothing was consumed" >> "$L.log"
  exit "$AGENT_LOAD_REFUSED"
fi

HEAD="$(gh pr view "$PR" --repo "$OR" --json headRefOid --jq .headRefOid 2>/dev/null)"
echo "[merge $PR] start $(date) head=$HEAD branch=$BR" > "$L.log"
# ⛔ THE HEAD OID MAY NOT EXIST IN THE LOCAL OBJECT STORE. A FORK PR's branch is not on `origin` at
# all, so the tolerant `git fetch origin "$BR"` above cannot find it and `worktree add <oid>` then
# fails hard (`fatal: invalid reference`). Not fetched = this PR can NEVER be reviewed: exit 6 writes
# no needsfix marker, so broad mode re-dispatches it every cycle, forever (PR #8589 review).
# `refs/pull/<N>/head` is where GitHub publishes a fork PR's head, and fetching it is the one call
# that makes the oid present.
[ -n "${HEAD:-}" ] && git -C "$REPO" fetch origin "refs/pull/$PR/head" --quiet 2>/dev/null || true

# The reviewer's cwd: an ISOLATED, detached checkout of the head being reviewed (#8484). A failure
# to provision is NOT fallen back to the shared checkout — that fallback would restore exactly the
# write surface this removes. No sandbox, no review.
SANDBOX="$(review_sandbox "${HEAD:-refs/heads/$BR}")"
if [ -z "$SANDBOX" ]; then
  echo "[merge $PR] ABORT: could not provision an isolated review checkout for ${HEAD:-refs/heads/$BR} — refusing to run the reviewer in the shared checkout (#8484)" >> "$L.log"
  # ⛔ AN UNREVIEWABLE HEAD MUST NOT BE AN UNBOUNDED PER-CYCLE RE-DISPATCH. A bare exit 6 writes no
  # marker, so the controller falls through to the re-record branch every cycle with no terminal
  # state and no escalation. When the head oid genuinely is not in the local store even after the
  # `refs/pull/<N>/head` fetch above, PARK it at this head: orch-loop.sh skips a PR whose
  # `parked-<PR>` equals its head, and a later push re-arms it (the marker is head-bound).
  if [ -n "${HEAD:-}" ] && ! git -C "$REPO" cat-file -e "${HEAD}^{commit}" 2>/dev/null; then
    echo "$HEAD" > "$O/state/parked-$PR"
    echo "[merge $PR] ⛔ PARKED at head=$HEAD: the head object is not in the local object store even after fetching refs/pull/$PR/head — a human must push or fetch it; no verdict was recorded" >> "$L.log"
  fi
  exit 6
fi
echo "[merge $PR] review sandbox=$SANDBOX (detached at ${HEAD:-refs/heads/$BR})" >> "$L.log"

# ── 1) fresh-context review, POSTED as a PR comment (the audit artifact) ──────
# The prompt is a FUNCTION so the self-test can EXECUTE it and assert on the text the reviewer
# actually receives (the #8602 check reads this function's output, not a grep of this file — a
# reflow of the heredoc would otherwise silently restore the deleted sentence).
reviewer_prompt() {
cat <<EOF
You are a FRESH-CONTEXT code reviewer for $OR. Review PR #$PR (branch $BR). Report only — do not fix code.
Your cwd is a DISPOSABLE checkout of this PR's head, created for you by the harness, on a DETACHED
HEAD: nothing you do in it can move a branch. Stay in it. Do NOT enter the shared checkout
($REPO) — that write is refused, not merely discouraged.
    git fetch origin main --quiet
    gh pr view $PR --repo $OR --json title,body,files
    gh pr diff $PR --repo $OR
Read the changed files IN FULL at the PR head (\`git show origin/$BR:<path>\`), not just the diff hunks. Read skills/code-review/SKILL.md for the discipline.
Report ONLY HIGH-confidence real issues: correctness bugs; security (injection/auth bypass/secrets); VACUOUS tests (would pass on the pre-fix code — prove it); scope/claim mismatch; regressions; fail-open paths; repo-rule violations — especially a NEW test file missing from config/ci-surfaces.yml (the manifest-integrity leg fails and the file is never selected).
DO NOT RUN THE TEST SUITE AND DO NOT PROVISION AN ENVIRONMENT HERE. This checkout is DISPOSABLE and has NO interpreter environment of its own; provisioning one, or searching the filesystem for an existing one, can burn the whole review budget in a single blocking call and the review then ends with no verdict at all (#8602: 35% of reviewer runs hit the 1800s cap, 7/7 in one window). Executing tests is the FIXER's job and CI's job, not yours. To argue a test is VACUOUS, read the test against the PRE-FIX code (\`git show origin/main:<path>\`) and show the assertion cannot tell the two apart. Bound every command; never walk \`\$HOME\` or \`/\`.

## REQUIRED: post your review as a PR comment (this is the merge-gate audit artifact)
Write your review to a file, then:
    gh pr comment $PR --repo $OR --body-file <your-review-file>
Your comment MUST state the outcome plainly and MUST NOT contain an unresolved assertion. Forbidden phrases in a CLEAN comment: "issues remain", "NOT CLEAN", "requires human attention", "auto-fix stalled/failed/reached cap".

## FINAL MESSAGE — exactly one line:
    CLEAN <the comment URL that gh pr comment printed>
  or, if you found real issues:
    ISSUES <n>
Do NOT modify code, commit, or merge.
EOF
}
reviewer_prompt > "$L-reviewer.txt"
# The reviewed refs, sampled immediately before the reviewer starts. `cd` into the sandbox FIRST so
# the agent's cwd is the sandbox — run_agent spawns `pi` without a cwd argument, so it inherits this
# shell's directory.
PRE_REFS="$(ref_shas "$BR")"
cd "$SANDBOX" || { echo "[merge $PR] ABORT: review sandbox $SANDBOX vanished before the reviewer started" >> "$L.log"; exit 6; }
run_agent "${REVIEW_TIMEOUT:-1800}" "$L.log" "$L-reviewer.txt" "reviewer for PR #$PR"
# ── did the reviewed branch move while the reviewer ran? (#8484) ──────────────
# Sampled around the agent. A review marker is HEAD-BOUND, so a CLEAN verdict recorded against a head
# that moved would authorise a merge on a diff nobody reviewed. This is the "make an unexpected ref
# move LOUD" half: it names the mover and refuses to record, instead of absorbing the move; the
# controller re-dispatches on the new head.
POST_REFS="$(ref_shas "$BR")"
if [ "$POST_REFS" != "$PRE_REFS" ]; then
  echo "[merge $PR] ⛔ UNREQUESTED REF MOVE while the reviewer ran: refs/heads/$BR + refs/remotes/origin/$BR were '$PRE_REFS' before it started, now '$POST_REFS'" >> "$L.log"
  # Name the field that ACTUALLY moved (mover_of): a push from the sandbox moves only the
  # remote-tracking ref, so a local-first pick logged the unchanged, already-reviewed head.
  mover_info="$(mover_of "$PRE_REFS" "$POST_REFS" "$BR")"
  moved="${mover_info%% *}"; mover="${mover_info##* }"
  echo "[merge $PR] ⛔ the moved ref is $moved" >> "$L.log"
  [ "$mover" != "-" ] && git -C "$REPO" log --no-walk --format="[merge $PR] mover: author=%an <%ae> committer=%cn <%ce> %h %s" "$mover" >> "$L.log" 2>&1
  echo "[merge $PR] ⛔ NOT recording a verdict — the reviewed head is gone; the controller re-dispatches on the new head" >> "$L.log"
  exit 5
fi
cd "$REPO" || exit 1
# A gate refusal (AGENT_LOAD_REFUSED) means NO agent ran. AGENT_STARTED is authoritative rather than
# the exit code, because `pi` shares the 0–255 status namespace: a pi that exited 75 itself must not
# be read as a refusal (that would skip recording a real verdict). (#1720 advisory)
if [ "$AGENT_STARTED" = "0" ]; then
  # A load refusal is NOT a verdict. Falling through would write `needsfix-<PR>-<head>`, which
  # authorises a fixer for a review that never happened, consumes a MAX_FIX_TRIES attempt and can
  # park the PR. Exit now; the claim is released by the EXIT trap and the controller's next cycle
  # re-dispatches this PR when load has fallen.
  echo "[merge $PR] reviewer NOT started (load gate) — no verdict recorded, none consumed" >> "$L.log"
  exit "$AGENT_LOAD_REFUSED"
fi
LAST="$(grep -aE '^(CLEAN|ISSUES) ' "$L.log" | tail -1 | tr -d '\r')"
echo "[merge $PR] verdict: ${LAST:-<none>}" >> "$L.log"

case "$(verdict_kind "$LAST")" in
  clean)
    URL="$(printf '%s' "$LAST" | grep -oE 'https?://[^ ]+' | head -1)"
    CID="$(printf '%s' "$URL" | grep -oE '[0-9]+$')"
    echo "[merge $PR] evidence comment id=${CID:-?} url=$URL" >> "$L.log"
    if [ -z "${CID:-}" ]; then echo "[merge $PR] ABORT: no comment id" >> "$L.log"; exit 2; fi
    # ── 2) record the review (writes the signed marker into the PR body) ──
    "$HOME/.pi/agent/scripts/record-review.sh" "$PR" "$HEAD" clean "$OR" --evidence "comment:$CID" >> "$L.log" 2>&1
    rc=$?
    echo "[merge $PR] record-review rc=$rc" >> "$L.log"
    [ $rc -ne 0 ] && { echo "[merge $PR] ABORT: record failed" >> "$L.log"; exit 3; }
    # ── 3) wait for the required ai-review-gate to go green (read the LATEST check row) ──
    gate=""
    for i in $(seq 1 30); do
      gate="$(gh pr checks "$PR" --repo "$OR" --json name,state --jq '[.[]|select(.name=="ai-review-gate")][0].state' 2>/dev/null)"
      echo "[merge $PR] gate=$gate (try $i)" >> "$L.log"
      [ "$gate" = "SUCCESS" ] && break
      sleep 30
    done
    if [ "$gate" != "SUCCESS" ]; then echo "[merge $PR] ABORT: gate=$gate (not green)" >> "$L.log"; exit 4; fi
    # ── 4) merge ──
    if gh pr merge "$PR" --repo "$OR" --squash >> "$L.log" 2>&1; then
      echo "[merge $PR] MERGED $(date)" >> "$L.log"
      touch "$O/state/merged-$PR.done"
    else
      echo "[merge $PR] merge command failed" >> "$L.log"
    fi
    ;;
  issues)
    # The reviewer found real issues at this exact head. Leave a marker so the loop knows to
    # spawn a FIXER (not another re-record) on its next cycle.
    echo "[merge $PR] ISSUES at head=$HEAD — needs a fixer" >> "$L.log"
    apply_verdict_marker issues "$HEAD" >/dev/null
    ;;
  *)
    # ⛔ A VERDICT-LESS RUN IS NOT A VERDICT (#8602, agent-infra#1741). An empty LAST means the
    # reviewer produced no `CLEAN`/`ISSUES` line — it timed out, crashed, or was killed at the
    # wall limit. Writing `needsfix` for it authorises a fixer for a review that never happened,
    # spends one of MAX_FIX_TRIES on it, and ends by parking the PR "needs a human" with the
    # reviewer's findings never addressed. Report the stall AS a stall (the `[timeout]` line
    # run_agent wrote above carries the wall limit and rc) and leave NO marker: the loop's next
    # cycle re-records — a fresh review at this head — instead of inventing a fixer task.
    echo "[merge $PR] NO VERDICT (no CLEAN/ISSUES line — reviewer stalled or failed) at head=$HEAD; NOT authorising a fixer" >> "$L.log"
    ;;
esac
echo "[merge $PR] end $(date)" >> "$L.log"
