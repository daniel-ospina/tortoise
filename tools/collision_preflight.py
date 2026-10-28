#!/usr/bin/env python3
"""collision_preflight — fail-loud, all-surface in-flight-work pre-flight (#3061).

Why this exists
---------------
Before dispatching a workstream for an issue, agents are supposed to check
whether another worktree / branch / PR already covers it. The check that was
actually run looked only at REMOTE surfaces (open PRs, remote branches,
assignees, claim comments) and piped the ONE local check
(``git worktree list | grep <N> | tail``) through a truncating window — so on a
297-worktree hub a live collision read as "no collision". Two dispatches
(#2985, #2952) duplicated in-flight work that already had open PRs (#3005,
#3018).

Design contract
---------------
0. THE TARGET IS ESTABLISHED, NOT ASSUMED. The tool resolves an explicit
   ``owner/name`` (``--repo owner/name``, or derived from ``--repo PATH``/
   cwd) and sends it on every REPOSITORY-SCOPED ``gh`` call, and it PRINTS the
   resolved ``owner/name`` and the issue's FULL TITLE in the verdict. Two
   calls are deliberately NOT repository-scoped because they cannot be: ``gh
   repo view`` (a fallback that DISCOVERS the slug — there is nothing to send
   yet) and ``gh api user`` (the GitHub login that identifies this lane's
   account, not a repository). A verdict that
   does not name what it measured cannot be trusted: on 2026-09-18 the tool
   resolved TORTOISE #1178 from a tortoise worktree while the target was
   AGENT-INFRA #1178, printed no repository and no title, and returned
   ``CLEAN`` for work it never looked at (#4027). If the issue is ABSENT from
   the target repo the run is INCOMPLETE (exit 2) — "not found here" is not
   "no in-flight work". If ``--repo`` is omitted and the number resolves in
   more than one candidate repo, the tool REFUSES rather than guess.
1. EVERY surface is always evaluated. There is no ``--only`` flag, no partial
   mode, no early exit. If a BLOCKING surface cannot be queried the run is
   INCOMPLETE, never CLEAN. Surfaces declared ADVISORY (see the surface table)
   are still evaluated and still reported, but they cannot block a dispatch.
   An advisory row's own status never enters the blocking/incomplete set, so it
   can never make an otherwise-CLEAN run non-clean. Where a read's failure DOES
   make the run INCOMPLETE (a failed timeline, say) the BLOCKING surface that
   owns the read is what sets it — the advisory row is merely marked unqueried
   beside it, so the report never prints an unread advisory row as a measured
   zero. A surface that could never prevent a duplicate carries no information
   in its failure, and halting a dispatch for it was the defect #5251 removes.
   Every blocking surface keeps the fail-closed posture.
2. Worktree enumeration is UNTRUNCATED, and the OPEN-PR surface is enumerated
   to COMPLETENESS. This module never pipes, heads or tails git output — the
   reported count is the full count — and the open PR list is fetched with
   ``--limit N+1`` so a list longer than its cap is detectable and is reported
   TRUNCATED → INCOMPLETE, never silently partial. A capped list that
   quietly queried a subset of PRs is the same fail-open class as the bug this
   tool exists to fix.
   The closed-PR surface is a deliberate, LABELLED exception: it is ADVISORY,
   so it is fetched as a single bounded sample (one request, ``per_page`` ≤ 100)
   whose completeness is still OBSERVED — the response's own ``Link`` header
   yields the total, and a sample smaller than that total is reported as
   ``⚠ PARTIAL`` and never as a complete list. It is the one place a partial
   list is not INCOMPLETE, and it is the one place it cannot be: this surface
   can never block, so its partiality cannot authorize a dispatch that a full
   enumeration would have refused.
3. A hit on a BLOCKING surface exits non-zero and names the surface. An
   unqueryable BLOCKING surface exits non-zero as INCOMPLETE. "No collision"
   (0) and "could not check" (2) are different outcomes by construction. An
   ADVISORY surface obeys neither: it is reported, but it can force neither
   outcome (see point 1).
4. Matching is boundary-exact for the issue number — the regex
   ``(?<![0-9])N(?![0-9])`` means ``3061`` never matches ``30610`` — so the
   tool cannot manufacture a collision out of an unrelated number.

5. THE LANDED QUESTION IS ANSWERED AUTHORITATIVELY, AND IT IS NOT A SAMPLE
   (#8560). This gate answers TWO questions, not one: "who is on #N?" (the
   surfaces below) and "is #N already DONE?". The second is not derivable from
   the first, and the tool used to have no answer for it: a CLOSED issue was
   printed as `closed issues are warn-only, not a hit`, and the only terminal-PR
   surface was a bounded ADVISORY sample — so an issue whose fix had already
   merged could read CLEAN. That happened twice in one session (#7174, whose
   merged PR #7403 fell outside the 100-PR sample, and #5035) and each time a
   lane was dispatched to re-implement shipped code. The landed answer is
   therefore its OWN surface, BLOCKING (an unqueryable one is INCOMPLETE, never
   CLEAN), fed by ONE authoritative source: the issue's own state — a CLOSED
   issue is decisive. It exits 4 (LANDED), a NEW code: every existing consumer
   stops on ANY non-zero exit, and a caller that reads the code alone can still
   tell a landing from a collision or an unqueryable surface.

   ⚠️ #8625: the PAGINATED timeline query is STILL run for an OPEN issue (an
   unreadable timeline is INCOMPLETE, never CLEAN), but a MERGED PR whose TITLE
   names the issue is now EVIDENCE, NOT A VERDICT. GitHub closes an issue only
   on a closing keyword — "a plain issue reference does not automatically close
   the issue" — so a reference is not a closure, and reading one as terminal
   re-created #4886's "a MERGED follow-up PR naming the issue blocks that issue
   forever" defect on a new surface (measured: three merged increments — #8127,
   #8315, #8389 — refused dispatch on #7951, none of them closing it). Those
   hits are reported LOUDLY on the ADVISORY surface `merged PRs naming an OPEN
   issue`; they can never decide a verdict.

6. THE NUMBER IS RESOLVED TO THE WORK ITEM, NOT REFUSED (#9957). A pull request
   number is also an issue number on GitHub, so a lane BRIEFED by PR number is a
   normal input — and the gate used to answer it with a refusal that NAMED the
   linked issue and then asked the dispatcher to re-read it. Measured on #9941:
   it served #7970, three lanes held #7970, and a fourth dispatch keyed on the
   PR number was one keystroke from being allowed. The tool held the mapping and
   did not use it.

   So an OPEN PR number is resolved through the three mechanical signals it
   carries, in SOURCE-FIRST order: GitHub's computed `closingIssuesReferences`
   (unioned with the body's closing keyword), then the `(#N)` title convention,
   then the `<type>/<n>-<slug>` branch convention. The verdict is then computed
   for the resolved work item(s) — the SAME pre-flight, the same surfaces, and
   the SAME exit codes (0/1/2/4). A PR whose work item cannot be resolved is a
   REFUSAL (exit 2, cause (b)) that names what it checked and could not resolve:
   "could not resolve" is never CLEAN. ⛔ Nothing here is table-driven and
   nothing is special-cased by number — a mapping table would answer for the
   numbers someone happened to record and would silently stop answering for the
   next one.

   ⛔ RESOLUTION CAN ONLY TIGHTEN, AND THAT REQUIRES EVERY ARM TO SPEAK. The
   three signals are UNIONED, never read as a precedence chain: a chain STOPS at
   the first arm that speaks, so the arms after it are not gated at all, and a
   work item nobody looks at is a dispatch nothing refused. (Measured shape: a
   body saying `Closes #1000` with `(#7970)` in the title and `feat/7970-…` in
   the branch resolved to `[1000]` alone — #7970, the work item three lanes held
   in this issue's own case, was invisible.) Every resolved number is matched
   against the PR that produced it on the re-run (its title, branch or closing
   reference is where the number came from), and several work items aggregate
   WORST-FIRST (`_EXIT_SEVERITY`). So a resolution can add a refusal and can
   never authorise a dispatch that the direct run would have refused.
   SOURCE-first order survives as the LABEL only — which arm the header names —
   and never as which numbers are gated.

   ⛔ AND A RESOLVED RUN PRINTS EXACTLY ONE `^VERDICT:` LINE — THE AGGREGATE.
   The per-work-item lines are re-prefixed (`WORK ITEM #N VERDICT: …`) because
   the fleet's only text-keyed consumer reads the FIRST anchored one
   (`agent-infra/scripts/orchestrator-harness/refresh-candidates.sh`:
   `grep -E '^VERDICT' | head -1`) and never compares it to the exit code. With
   one anchored line per work item, a mixed resolution handed that consumer the
   most PERMISSIVE verdict out of a report whose exit code refused the dispatch.

   The residual is stated rather than hidden: a MERGED or CLOSED PR number is
   not resolved at all (the refusal this relies on is scoped to OPEN PRs,
   #7009), so such a number keeps its old, documented behaviour — reports
   immutable history and can read CLEAN. A lane that wants the landed answer for
   a terminal PR must name the issue; resolving a terminal PR would need a
   targeted `gh pr view` on EVERY run, which is a fleet-shared GraphQL cost this
   change does not add.

Surfaces (8 rows, ALL hit-capable)
----------------------------------------------------------------------------
  issue landed              issue state (gh issue view);     (CLOSED issue —
                            /issues/N/timeline when OPEN     the ONLY source
                                                             of a landing)
  merged PRs naming an      no query of its own — it         (MERGED PR whose
  OPEN issue                reuses the LANDED row's          TITLE names an
  · ADVISORY ·              /issues/N/timeline read; a       OPEN issue; a
                            failure marks BOTH rows          reference, not a
                            INCOMPLETE (the LANDED row       closure — #8625)
                            is BLOCKING, so the RUN
                            is INCOMPLETE too)
  open PRs                  gh pr list --state open   (title / headRef;
                                                         body closing
                                                         reference;
                                                         GitHub's
                                                         closingIssuesReferences)
  recently-closed PRs       gh api REST, ONE request   (title / headRef;
  · ADVISORY ·              /repos/<owner>/<repo>/pulls   body closing
                                                         reference)
                                                         (#5251)
  local branches            git for-each-ref refs/heads   (a 0-ahead branch
                                                           with no live owner,
                                                           no uncommitted work and
                                                           no live claim ref is
                                                           ADVISORY — EMPTY CLAIM,
                                                           #6108; an unreadable
                                                           reading BLOCKS)
  remote branches           git for-each-ref refs/remotes   (all remotes)
  local worktrees           git worktree list --porcelain   (UNTRUNCATED; the
                                                           same EMPTY-CLAIM leg
                                                           applies to a record
                                                           whose branch is 0
                                                           ahead, #6108)
  issue assignee/holder     gh issue view N (assignee) +  (a lane MECHANICALLY
                            the FLEET BOARD's lane->issue  attributed to #N whose
                            attribution + holder liveness  holder has NOT stopped;
                                                           comment prose is never
                                                           read — not even fetched,
                                                           #8175)

The `issue keywords` row was removed with the lexical arm (#3504): a row that
can only report a guess does not become honest by being listed. The eighth row
today is a DIFFERENT one — `merged PRs naming an OPEN issue` (#8625), the
advisory home of the title-reference arm that used to be an authoritative source
of `LANDED` — so do not read "8 rows" as the deleted keyword row returning, and
do not read this ADVISORY row as a landing.

NOT PRESENT: a fleet-session surface. #1233 asked for one wired to
`map-sessions.py`, and it was built and measured for this change — then left
out, because the prescribed discipline (issue number + >= 2 distinctive title
keywords) does not identify a session that HOLDS an issue; it identifies every
session that has READ it. On #3827 the surface attributed 13 sessions, 11 of
them sessions holding a pasted copy of the fleet board (a mailer-daemon
session, a marketing session, ...). Every one of them is fail-closed noise, and
blocking dispatch on 13 phantom holders would reproduce the exact
"a gate that always fires is a gate that gets worked around" corrosion this
change fixes for claim comments. The mechanism that CAN tell a holder from a
reader — the board's lane->issue attribution — now exists and is exactly what the
claim gate reads (below). The standalone `fleet-session surface` ROW is still
absent: keyword attribution is not a substitute for it, and the board's
mechanical attribution is what replaced the prose read.

Claim gate — the HOLDER, never prose (#8175)
--------------------------------------------
⛔ COMMENT PROSE IS NOT READ. It is not classified, not pattern-matched, and not
even FETCHED: `gh issue view` no longer asks for `comments`. Repeated attempts to
read ownership out of a comment's TEXT are the record of why — and their count is
deliberately not given here: the issue's own record disagrees ("six review
rounds" vs "seven attempts"), which is what a number with no single artifact
behind it does. Each attempt found a fresh string that read as ownership where
there was none, and every miss went the unsafe way (a live lane reported free).
Our own attribution code states the root cause in one line: every lane
authenticates as the SAME GitHub account, so a claim-shaped comment cannot
distinguish owners.

What the gate asks instead: *is a lane still working this issue?* The answer
comes from the fleet board (`tools/fleet_state.py`), which attributes a lane to
an issue from MECHANICAL ownership signals only — the lane's branch slug, its
worktree path, and the lane's OWN first user message. Nothing is inferred from
the name of anything a comment happens to mention.

The decision rule, in full:

    a lane is attributed to the issue, and its OWN liveness record shows a LIVE
      pi process                                     -> COLLISION (exit 1)
    a lane is attributed, `pid_alive` is False, and the board MEASURED its
      liveness WITH pid evidence (`liveness_measured` and a recorded `pi_pid`)
                                                     -> reported, NOT blocking
    a lane is attributed, and the board CANNOT say — no `pid_alive` reading, a
      `pid_alive: false` that is only the unmeasured DEFAULT or that has no pid
      evidence behind it (INCLUDING a lane whose workspace is terminal: the
      workspace's absence is not evidence about the process), the live-session
      store could not be read, or the attribution is ambiguous
      (`resolve_sessions` refuses to guess)          -> INCOMPLETE (exit 2)
    no lane is attributed                              -> not blocking

⚠️ A TERMINAL WORKSPACE APPEARS IN NO ROW ABOVE AS EVIDENCE. It is written into
the board and read here only for the REASON's detail (`_stopped_reason`) —
`fleet_state.orphan_report` is explicit that a gone cmux workspace "says nothing
about whether a pi process outlived it", so a terminal verdict can never decide
this gate's question ("has the writer stopped RUNNING?"). A terminal lane reads
LIVE when a pid says so, STOPPED when a MEASURED pid says so, and UNMEASURED —
hence INCOMPLETE, blocking — otherwise.

⛔ WHY THE LANE'S OWN `pid_alive`, NOT `lane_live_map`, AND NOT
`is_genuinely_free`. Both of those answer OTHER questions.

`is_genuinely_free` asks "can this lane take MORE work?", and its first reason is
`open_pr_count or held_issue_count`. A lane attributed to issue *N* holds *N* by
construction, so it is NEVER "genuinely free", and composing the rule on that
predicate collapses it to "any attributed lane blocks", which would keep a DEAD
lane's stale branch blocking forever. Even the count-stripped form is False for
a terminal lane (no measurable pane) — so that predicate never frees the case
this change exists for.

`lane_live_map` asks "is this lane still a live fleet member?", and it
deliberately marks a TERMINAL lane `False` EVEN WHEN A PROCESS OUTLIVED ITS
WORKSPACE (`#7738`: "a terminal lane is DEAD even when its liveness is
unmeasured"). `fleet_state.orphan_report` names the hazard in the same module:
*"a terminal lane's cmux workspace is GONE, which says nothing about whether a pi
process outlived it — a terminal lane CAN still carry `pid_alive: true`."* The
gate's question is "has the writer stopped RUNNING?", so a live pid must win over
a gone workspace. The shape that would fool a rule keyed on the terminal verdict
is a terminal workspace whose process is STILL RUNNING — and on 2026-10-13 the
live board held exactly that: `π - land-lane-1`, the holder of #6151 (the case
this change exists to unfreeze), was terminal `RECOVERABLE` with `pid_alive:
True` (pi_pid 7054). That lane has since exited and the board now reports it
STOPPED; the *rule* is pinned by the synthetic test
`test_a_terminal_holder_whose_process_is_alive_still_blocks`, not by that
observation, which is evidence of the shape rather than a current reading.

Fail-closed, and NO dismissal switch. A board that cannot be read, rebuilt, or
attributed marks this surface INCOMPLETE — never CLEAN, and never a downgrade of
a blocking holder. Six unmeasured cases in particular: the board's live-session
store could not be read (its `liveness-unmeasured` orphan); a lane carries no
`pid_alive` reading at all; a lane carries `pid_alive: false` **as a default**,
because the board keeps no session record for it (`liveness_measured` is False —
`lane_live_map` reads that same shape as `None`, and `build_state` appends "no
session record for this lane — liveness UNMEASURED, not dead" when it reports
that lane's free reasons); a lane is measured but carries NO `pi_pid`
(`lane_live_map` reads that shape as `None` too — no live pid can be found or
ruled out when there was no pid to observe); the board's OPEN-ISSUE read hit its
cap, so an issue outside that window cannot be attributed and its absence from
the index is NOT a measured "nobody" (the board stamps
`reconciliation.open_issues_truncated`, and an unstamped board is rebuilt — the
writer this replaces capped that read at 800 against 989 open issues today); or
the board
could not be obtained. ⚠️ A TERMINAL workspace is NOT a way to demote: its cmux
workspace is GONE, which says nothing about whether a pi process outlived it
(`fleet_state.orphan_report`), so the terminal verdict can only ever supply the
REASON for a demotion that the lane's own MEASURED `pi_pid` already earned. A
terminal lane whose process is still running BLOCKS (a live pid wins), and a
terminal lane whose liveness is unmeasured joins the cases above and BLOCKS too.
A stale or absent
cache is REBUILT before answering (a reader that served an aged cache could
report "nobody is working this" about work that began minutes ago — the unsafe
direction); a rebuild that cannot answer is the unmeasured case. There is no
flag of this gate's own, no environment variable it reads and no human input that
clears a blocking holder: the reading is
mechanical, on every run.

⚠️ THIS DELIBERATELY WIDENS THE FAIL-CLOSED SURFACE. The issue surface now rests
on the FLEET BOARD, so an unreadable board makes this surface INCOMPLETE for
EVERY target — where before it edited only on a prose match. That is the direct
consequence of the ruling (unmeasured => block), and it is stated here so it is
not later reported as a bug.

⛔ THE BOARD IS NOT REDIRECTABLE BY THIS GATE. `fleet_state.DEFAULT_OUT` is
`$HOME`-derived, but the gate adds no override of its own — no flag, no variable,
no human input clears a blocking holder — and it re-reads the resolved constant
rather than deriving a path at read time.

⚠️ MEASUREMENT BOUNDARY — the open-issue window is BOUNDED AND CHECKED. The
board attributes a lane to an issue only if the issue is in its open-issue read,
and that read is capped (`fleet_state.OPEN_ISSUES_LIMIT`). The board therefore
records `reconciliation.open_issues_truncated`; when it is true, an issue with no
attribution entry is INCOMPLETE rather than a measured "nobody", because the
window cannot prove the negative. A board that does not stamp the flag AT ALL is
refused and REBUILT rather than read as complete — the writer this replaces
capped the same read at 800 while this repo has 989 open issues, so a missing
stamp means coverage is UNKNOWN, not complete. While the read IS wide enough
(989 open issues against a limit of 2000 on 2026-10-14 — the normal case),
absence in `index.issues` IS measured: the issue was in the window and no lane's
branch slug, worktree path or session text named it. The branch and worktree
surfaces block independently on the issue number either way.

⚠️ THE BOARD IS SINGLE-REPO, and a cached board for ANOTHER repo is not served:
`index.issues` is keyed by a bare issue number and attributed against that
board's own open-issue set, so serving it here could both fabricate a holder and
hide one. A `--repo owner/name` pre-flight for a repo the cache is not stamped
with therefore REBUILDS — the fail-closed direction.

⛔ **THE GATE NEVER WRITES THE SHARED BOARD.** It reads it, and rebuilds it in
memory when the cache is stale, absent, or stamped for another repo — but it
never writes `fleet_state.DEFAULT_OUT`. Its own rebuild is a PARTIAL board (per-PR
CI verdicts are not read, and the review binding cannot reach its `diff-match`
refinement; the board's writer passes callables that shell out per PR), so a
write-back would silently degrade a shared artifact for its other consumers
(`fleet_state lane` prints `ci=`/`review=`). Paying a rebuild per pre-flight while
the cache is stale is the honest price of not shipping a partial board as if it
were the real one — the board's own writer repairs it.

Number matching is applied to full refs/paths/PR text — and it is the ONLY
matching there is. There is no lexical arm (#3504): shared domain vocabulary is
never consulted, so no stoplist, no distinctiveness tier, and no
`--min-keywords` dial exist or need to. What survives is one precision guard,
learned from real runs:

  * PR-BODY PROSE IS NOT A COLLISION. A closed PR cannot be in-flight, and a
body that merely cross-references an issue ("restored in #2745", "triaged and
filed as #2751") is not work for it. On the body of a PR only a CLOSING
REFERENCE is a strong hit; a bare number mention is recorded as a WEAK,
non-blocking signal — it is printed for transparency but can never by itself
produce a "do NOT dispatch" verdict. This is a live-bug fix: those two exact
bodies produced a false COLLISION for #2745 and #2751 because every `#N` in
prose was treated as work.

  * REMOTE branches are number-matched and nothing else. That is now true of
every surface, but it is worth keeping the reason: this repo's remote-tracking
namespace holds hundreds of stale, abandoned refs, and scanning it lexically
produced 878 false hits for one issue. The convention is
`<type>/<issue#>-<slug>`, which is what the check actually needs.
  * A TERMINAL PR IS NOT IN-FLIGHT WORK. The argument above ("a closed PR
cannot be in-flight") applies to the WHOLE PR, not only to its body prose: on
the `recently-closed PRs` surface every match — a number in the title, a number
in the head ref, a closing reference — is reported
as a non-blocking WEAK signal naming the state that decided it (`merged` /
`closed`). Immutable history is context, not work (#4886, #5112, #4533), and the
LIVE surfaces — open PRs, local and remote branches, worktrees, the issue
assignee/holder surface —
keep their strength unchanged, so a PR closed minutes ago whose branch is still
live is still caught by the branch surface. A TERMINAL BRANCH is excluded on the
same principle and by two exact predicates (#5186): its tip SHA equals a MERGED
PR's head SHA (a squash merge loses the commits, so ancestry CANNOT see it —
GitHub's own docs say the original SHAs are gone), or its tip is an ancestor of
`origin/main`. `patch-id` is deliberately NOT a third predicate: both of its
whitespace modes ignore whitespace, so it can call a live branch terminal — a
false ACCEPT, which is this test's dangerous direction. Before this, having
shipped part of an issue was what blocked shipping the rest: a merged follow-up
PR's title
necessarily names its issue, so the number-in-title match blocked that issue
forever with no dismissal path.
  * A DIGIT RUN INSIDE A HEX DIGEST IS NOT A REFERENCE. See `number_present`:
the containing run's shape decides it, so a SHA-256 in a review attestation
cannot fabricate a hit (#4935, #3611).

Keywords are GONE, by design (#3504). There is no lexical arm at all: see the
note where the stoplists used to live. A blocking hit is a match on the ISSUE
NUMBER (a reference to this issue) or a computed GitHub field; shared domain
vocabulary is never consulted.

Self-identity is an INPUT, not an inference (#3504 classes 1/3/4). The caller
declares its own branch(es) and worktree(s) with ``--self-branch`` /
``--self-worktree`` (and the current branch is detected automatically), so a hit
on the caller's OWN artifacts is reported as yours and cannot block. Inferring
"is this mine?" from text is what manufactured those false COLLISIONs.

Usage
-----
    uv run python tools/collision_preflight.py <issue-or-PR-number>
        [--repo OWNER/NAME | --repo PATH]
        [--self-branch REF] [--self-worktree PATH]
        [--gh PATH] [--git PATH]
        [--timeout SECS] [--pr-limit N] [--closed-pr-limit N]
        [--closed-pr-timeout SECS]

An OPEN PULL REQUEST number is accepted and RESOLVED to the work item(s) it
serves (#9957) — see design-contract point 6. The number to give it is still
the ISSUE's, and a PR number is only a convenience: resolving one costs extra
surfaces, while the issue number is the target every brief should name.

``--repo`` accepts EITHER ``owner/name`` (the GitHub target; a local clone is
located for the git surfaces) OR a path to a worktree of the target repo (the
existing behaviour; ``owner/name`` is then derived from its remote). Omitted,
the current directory is used and the number is checked for ambiguity across
sibling repos before any verdict is issued.

Exit codes
----------
    0  CLEAN        every BLOCKING surface queried and no STRONG hit. Non-blocking
                    hits are still LISTED (the caller's own branch/worktree, a
                    terminal branch or PR, the shared fleet account as assignee, a
                    prose cross-reference, and every hit on an advisory surface)
    1  COLLISION    >= 1 STRONG hit on a >= 1 BLOCKING surface (do NOT dispatch).
                    A weak hit never decides this, and a hit on an ADVISORY
                    surface never decides it, however it is shaped
    2  INCOMPLETE   NOT clean, and NOT a collision. MULTIPLE CAUSES, and a
                    caller must distinguish them because the remedies differ:
                    (a) >= 1 BLOCKING surface could not be queried (remedy: fix
                        the surface — the report names the CAUSE, and only says
                        `fix gh auth/network` when that is what failed; a budget
                        exhaustion says so and says re-authenticating cannot
                        help, `_incomplete_remedy`), or was TRUNCATED at its
                        completeness cap (remedy: raise `--pr-limit`, EXCEPT on
                        the REST rescue, which reads one page at GitHub's
                        maximum and has no setting to widen — the report says
                        which kind it measured), or the
                        FLEET BOARD could not answer the claim gate — its LANE
                        ROSTER is ABSENT (remedy: PROVISION it with one row per
                        live lane, a CELL step; `fleet_state.py build` builds
                        the board FROM that file, so a rebuild fails on the SAME
                        read), or the roster is PRESENT but unreadable (remedy:
                        FIX THE FILE — a rebuild cannot help, it fails on the
                        same read), or its read recorded NO CAUSE (remedy: start
                        from the FILE itself; `fleet_state.py build` cannot
                        create or repair it), or the board is unreadable / the
                        attribution is ambiguous (remedy: rebuild the board,
                        `uv run python tools/fleet_state.py build`). A surface
                        that can never
                        produce a blocking hit is EXEMPT and its failure is only
                        REPORTED: it has no ability to prevent a duplicate, so its
                        failure cannot conceal one (#5251). Rendered as a report
                        ending in `VERDICT: INCOMPLETE`.
                    (b) the queried NUMBER is not a work item: it is an OPEN PULL
                        REQUEST (#7009) whose work item could not be RESOLVED
                        (#9957). Raised by `NotAWorkItem` BEFORE any verdict is
                        computed, so this cause prints NO report and NO
                        `VERDICT` line — only the refusal, on stderr, in TWO
                        sentences: why a PR number cannot be answered, then what
                        the resolver checked and could not resolve. The remedy is
                        NOT "fix gh auth/network"; it is "re-run with the issue
                        number". ⛔ A PR number whose work item IS resolvable never
                        reaches this cause — it is gated as that work item. A
                        caller keying its remedy on
                        `VERDICT: INCOMPLETE` will MISS this one; keying on the
                        exit code alone cannot tell (a) from (b).
                    (c) the TARGET is ambiguous: `--repo` was omitted and the number
                        resolves in more than one sibling repo (`_ambiguity_refusal`).
                        Verdict-less in exactly the same way as (b) — stderr only,
                        no report — so a caller inferring "no VERDICT line means it
                        is the open-PR refusal" MISROUTES this one. Remedy: pass
                        `--repo owner/name`.
    3  usage / internal error
    4  LANDED       the issue's own state is CLOSED (worded from its closure
                    reason) — a TERMINAL fact, so this is NOT free work (do
                    NOT dispatch). A landed issue is never reachable as CLEAN,
                    and this code takes precedence over 1 and 2 (a terminal
                    signal is decisive). It is DISTINCT from 1 because the
                    remedy differs — verify and close the issue, not
                    coordinate with a lane — and from 2 because there is
                    nothing to re-run. ⚠️ #8625: a MERGED PR that merely NAMES
                    this issue is NOT authoritative and can NEVER produce this
                    code; it is reported on the ADVISORY surface.

Env seams (tests point these at stubs; production defaults are the real tools)
    COLLISION_PREFLIGHT_GH                gh binary        (default: gh)
    COLLISION_PREFLIGHT_GIT               git binary       (default: git)
    COLLISION_PREFLIGHT_TIMEOUT           per-command secs (default: 60)
    COLLISION_PREFLIGHT_PR_LIMIT          open-PR cap     (default: 1000)
    COLLISION_PREFLIGHT_CLOSED_PR_LIMIT   closed-PR SAMPLE size — per_page of
                                          the single advisory request
                                          (default: 100, clamped to 100)
    COLLISION_PREFLIGHT_CLOSED_PR_TIMEOUT closed-PR request (default: 60)
    COLLISION_PREFLIGHT_REPO_ROOTS        ':'-separated roots scanned for
                                          sibling repos (default: parent of the
                                          current repo's main worktree)
    COLLISION_PREFLIGHT_BOARD_CACHE       "1" enables the gate's PRIVATE board
                                          cache (default: unset => inert,
                                          behaviour byte-identical). It selects
                                          no path: see the note below
    COLLISION_PREFLIGHT_BOARD_CACHE_TTL   seconds a cached board stays servable
                                          (default: 300, clamped to
                                          fleet_state.STATE_FRESH_S; <= 0
                                          disables the cache)
The claim gate reads the fleet board at ``fleet_state.DEFAULT_OUT``, the user's
state dir (``$HOME/.pi/agent/state/fleet-state.json``). The gate adds NO override
of its own: no flag selects the board, no environment variable names it, and it
is never written from here. ``$HOME`` is simply where the OS keeps that
directory — and how the tests place a board of their own — not a switch this
gate offers; the read is pinned to the resolved constant and never re-derived
(``test_the_board_is_read_from_default_out_not_from_a_redirected_home``).
The gate does keep a PRIVATE copy of the PARTIAL board it rebuilds, for the
duration of one refresh pass (``COLLISION_PREFLIGHT_BOARD_CACHE``, sources
``rebuilt-cache``) — and that copy cannot be aimed either: the environment may
only ENABLE it, while the directory is derived from ``DEFAULT_OUT``'s own parent,
because a caller-selectable directory is a caller-selectable BOARD (a planted,
answerable, holder-free one clears a live holder — the very thing the pin above
forbids). Only a successful, answerable, correctly-stamped rebuild is ever
stored, and it is never written back over the shared board.

    OVERRIDES: the usual "the caller supplies the cache directory" convention —
    the gate's private board-cache directory is DERIVED from
    ``fleet_state.DEFAULT_OUT`` and is deliberately not settable; a
    caller-named cache directory is a caller-named fleet board, which can clear
    a live holder (#8948).
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # #4053: this is an ENVIRONMENT/USAGE failure, not a collision. `SystemExit(<str>)`
    # exits 1, which is EXIT_COLLISION — so a check that never ran read as "another lane
    # is on it", with no `VERDICT:` line to contradict it and nothing saying no surface
    # was queried. That is the same defect class as
    # `COLLISION_PREFLIGHT_TIMEOUT=abc` (#3619), which is now EXIT_USAGE. Emit the
    # message on stderr and exit EXIT_USAGE. The constant is defined below; this guard
    # necessarily runs before it (and before the imports, #5128), so the value is
    # spelled out with the name in this comment rather than referenced.
    print(
        f"collision-preflight: requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/collision_preflight.py`",
        file=sys.stderr,
    )
    raise SystemExit(3)  # EXIT_USAGE

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, urlencode

# The fleet board is the ONLY source for the claim gate's holder read (#8175).
# Lane attribution (branch slug / worktree path / the lane's OWN session text)
# and liveness are both already built there, and this module must re-derive
# NEITHER — a second implementation is how the two answers drift apart. Imported
# by path because this tool is also loaded directly from its file (see
# `tests/test_collision_preflight.py`), so `from tools import fleet_state` would
# depend on a `sys.path` the caller may not have set.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_state

EXIT_CLEAN = 0
EXIT_COLLISION = 1
EXIT_INCOMPLETE = 2
EXIT_USAGE = 3
# #8560: the terminal answer to "is #N already landed?". It is a REFUSAL and
# MUST NOT be CLEAN; distinct from EXIT_COLLISION because the remedy differs
# (verify/close, not coordinate) and distinct from EXIT_INCOMPLETE because
# nothing needs re-running. Adding a code is fail-safe for a consumer that reads
# the EXIT CODE (any non-zero stops it); the one in-repo consumer that parses the
# VERDICT TEXT instead — agent-infra/scripts/orchestrator-harness/refresh-candidates.sh
# (retired from this repo 2026-10-19; the live controller reads the canonical copy) —
# gets an explicit LANDED arm in this same change, so the new verdict is not
# silently defeated there (it used to fall through to "no verdict").
EXIT_LANDED = 4

#: #9957: the WORST verdict wins when one PR number resolves to SEVERAL work
#: items. Ordered by how decisive the refusal is, and not by numeric value: 4 is
#: a terminal fact (the docstring says it takes precedence over 1 and 2), 1 is a
#: live claim, 2 is "the world could not be read", 0 authorises a dispatch.
#: Aggregating this way can only ever RAISE the code, never lower it — so
#: resolving to more work items can add a refusal and can never authorise one.
#: ⛔ AND THE MAP IS ORDER, NOT ARITHMETIC: a multi-item run prints the winner of
#: `max(…, key=_EXIT_SEVERITY[…])` as its ONE anchored `VERDICT:` line, so
#: `max(numeric code)` (which puts INCOMPLETE above COLLISION and changes the
#: remedy the caller is told to apply) is NOT a valid substitute.
#: `test_the_severity_order_ranks_decisiveness_not_numeric_value` pins the order.
_EXIT_SEVERITY = {
    EXIT_CLEAN: 0,
    EXIT_INCOMPLETE: 1,
    EXIT_COLLISION: 2,
    EXIT_LANDED: 3,
}
_EXIT_LABEL = {
    EXIT_CLEAN: "CLEAN",
    EXIT_COLLISION: "COLLISION",
    EXIT_INCOMPLETE: "INCOMPLETE",
    EXIT_LANDED: "LANDED",
}

DEFAULT_TIMEOUT = 60.0
# PR caps are completeness bounds, not sampling windows: OPEN PRs are fetched
# with `--limit cap+1` and the cap is applied to the full result, so a list
# longer than its cap is reported TRUNCATED.
PR_LIMIT = 1000

# The closed-PR surface is a BOUNDED SAMPLE fetched in exactly ONE request
# (#5251), and it is the tool's only ADVISORY surface.
#
# WHY ONE REQUEST. Since #5129 every match on a TERMINAL PR is `weak`, and
# `format_report` decides on `strong` -> `incomplete` — so
# this surface's hits cannot block a dispatch, and its failure cannot conceal a
# collision. Enumerating it to exhaustion (~19 requests at per_page=100 here;
# measured `Link: rel="last"` = page 19 of 1,848) paid a multi-request cost for
# a verdict-inert signal, and that cost lands on the SECONDARY rate limiter,
# which GitHub documents as unobservable ("There is not a way to check the
# status of your secondary rate limit"). ~93 calls at per_page=20 per lane, run
# by many lanes at once, blow it COLLECTIVELY — which is how one lane's run
# failed and halted its dispatch.
#
# COMPLETENESS IS STILL OBSERVABLE. The response's own `Link: rel="last"`
# header gives the total on the SAME call, so the surface reports
# "100 of 1,848" and marks itself a partial sample. It never presents 100 as
# everything — the exact failure mode this tool exists to prevent.
#
# SORT. The default (created, desc) is used deliberately: `sort=updated` reads
# the events log and drifts from the returned `updated_at` (#5251), while
# `created` is a field of the payload itself.
#
# `gh pr list --search` / `search/issues` are NEVER used: the search API caps
# at 1000 results, and `search/issues` returns ISSUE-shaped results carrying no
# `head.ref` — silently disabling this surface's branch-name leg. Both verified
# against the live API (#5251). `per_page=100` also no longer resets on this
# host (3/3 runs returned 100 items), so one page can be a full one.
CLOSED_PR_LIMIT = 100
#: Page size for the TARGETED head-ref lookup (#5485). Bounded on purpose: the
#: lookup runs only for local refs that match the issue number AND survive every
#: predicate, and it is ONE list request plus one `compare` per MERGED PR that
#: request returns — so normally 0-2 calls, and never a call per branch. A FULL
#: page is treated as a possible truncation and refused rather than read as "no
#: merged PR".
_TARGETED_PR_PAGE = 100

# One request's wall-clock budget. The multi-request enumeration this replaced
# needed 600 s; a single call must finish well inside the default.
CLOSED_PR_TIMEOUT = 60.0

# ── surface authority (#5251) ────────────────────────────────────────────────
# A BLOCKING surface can prevent a duplicate, so its failure carries
# information and sets INCOMPLETE (fail closed). An ADVISORY surface cannot:
# its hits never reach the verdict and its failure conceals nothing, so making
# it INCOMPLETE would only halt a dispatch for no reason — the defect #5251
# removes. Authority is a PROPERTY OF THE SURFACE, not of its data: deriving
# the demotion from a payload field (e.g. dropping `state`) silently promotes
# an advisory hit back to `strong`, which is #5129's data-dependent shape.
AUTHORITY_BLOCKING = "blocking"
AUTHORITY_ADVISORY = "advisory"

MAX_HITS_SHOWN = 20

SURFACE_OPEN_PRS = "open PRs"
SURFACE_CLOSED_PRS = "recently-closed PRs"
SURFACE_LOCAL_BRANCHES = "local branches"
SURFACE_REMOTE_BRANCHES = "remote branches"
SURFACE_WORKTREES = "local worktrees"
# `issue assignee/holder` — renamed from `issue assignee/comments` in #8175,
# when the surface stopped reading comments. The name is what a reader uses to
# decide whether a row covered what they care about, so a row that no longer
# reads comments must not be NAMED as though it did.
SURFACE_ISSUE = "issue assignee/holder"
# #8560: the AUTHORITATIVE terminal answer. Its hits decide EXIT_LANDED, never
# EXIT_COLLISION — the surface is separated out in `format_report` — and it is
# BLOCKING, so a failure to read it is INCOMPLETE rather than a silent CLEAN.
SURFACE_LANDED = "issue landed"

# #8625: a MERGED PR whose TITLE names an OPEN issue is NOT a landing.
# GitHub's own semantics make the issue's STATE authoritative — "a plain issue
# reference does not automatically close the issue; automatic closure requires a
# supported closing keyword" — so a reference is not a closure. Reading one as
# terminal re-creates #4886's defect ("a MERGED follow-up PR naming the issue
# blocks that issue forever … no dismissal path") on a new surface; #4886 is
# CLOSED, and #8560's own rationale says its answer "preserves #4886's rule that
# a terminal PR must not block forever". The title-arm therefore moves here:
# reported loudly, never blocking.
SURFACE_UNCLOSED_REF = "merged PRs naming an OPEN issue"

# The complete, ordered surface list. `_assert_all_surfaces` proves the run
# evaluated every row — a partial run is an internal error (exit 3), never a
# silently-narrower CLEAN.
#
# There were SEVEN rows until #3504: a `keywords` row carried the lexical arm's
# derived vocabulary and its BLIND/INCOMPLETE annotation. With the lexical arm
# deleted the row had nothing left to report — nothing ever added a hit to it;
# it existed only to describe a dimension that no longer matches anything — so
# it is gone rather than kept as a decorative CLEAN line. Surface counts read
# 6/6 after that deletion. `issue landed` (#8560) is a DIFFERENT seventh row: it
# is not the deleted keyword row returning, it answers a question the tool never
# had an authoritative source for. Counts read 8/8 (#8625 added `SURFACE_UNCLOSED_REF`).
ALL_SURFACES = (
    SURFACE_LANDED,
    SURFACE_UNCLOSED_REF,
    SURFACE_OPEN_PRS,
    SURFACE_CLOSED_PRS,
    SURFACE_LOCAL_BRANCHES,
    SURFACE_REMOTE_BRANCHES,
    SURFACE_WORKTREES,
    SURFACE_ISSUE,
)

# Surfaces whose hits and failures CANNOT affect the verdict (#5251). They are
# still queried and still reported; they just cannot block a dispatch or force
# INCOMPLETE. Membership here is the ONE place the demotion is expressed.
ADVISORY_SURFACES = frozenset({SURFACE_CLOSED_PRS, SURFACE_UNCLOSED_REF})

# ── target-repo resolution (#4027) ───────────────────────────────────────────
# `--repo` accepts `owner/name` OR a directory path. The slug is what every
# `gh` call is sent; the path is where the git surfaces run. When a slug is
# given without a path (or vice versa) the other half is derived, and a half
# that cannot be derived leaves its surfaces INCOMPLETE — never silently
# reading a different repository.
REPO_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
REPO_ROOTS_ENV = "COLLISION_PREFLIGHT_REPO_ROOTS"
# Sibling repos probed for the omitted-`--repo` ambiguity refusal. Exceeding
# the cap is INCOMPLETE (fail closed), never a silent partial scan.
CANDIDATE_REPO_CAP = 40
AMBIGUITY_TIMEOUT = 30.0

STATUS_CLEAN = "CLEAN"
STATUS_HIT = "HIT"
STATUS_INCOMPLETE = "INCOMPLETE"

# ── there is NO keyword stoplist here, by design (#3504) ─────────────────────
# This file used to derive "distinctive" keywords from the issue title and match
# them against branch names, worktree paths and PR head refs. That whole arm is
# GONE, and the reason matters more than the code did.
#
# A stoplist can never enumerate a repo's vocabulary: every word added to it is a
# word some other title needs. `capture`, `verify`, `retrieval`, `temporal`,
# `collision` and `preflight` all passed the ">=5 chars after plural folding and
# not in the stoplist" test, so ALL of them were labelled DISTINCTIVE and a
# branch named after any one of them blocked an unrelated issue. Measured on the
# live beta queue: #3516 (the queue's #3 BLOCKER) was blocked by
# `feat/3809-capture-verify` on the words `capture`/`verify`; #2520 by
# `fix/2976-temporal-retrieval`; and #3504 itself — the issue ABOUT this tool —
# was un-dispatchable on `collision`/`preflight`, because its own title is made
# of them.
#
# The defect was not "the regex was imprecise". It was that a LEXICAL test was
# producing a BLOCKING verdict on surfaces it cannot reason about — the caller's
# own artifacts, ordinary engineering vocabulary, and immutable history. Deleting
# it IS the fix. The surviving blocking signal is a match on the ISSUE NUMBER,
# which is a REFERENCE to this issue rather than a guess about meaning. Shared
# domain vocabulary is not evidence of shared work and is no longer consulted at
# all (#3504 classes 2 and 5; decision D3).
#
# ⛔ Do not reintroduce a stoplist, an IDF threshold, or a similarity score here.
# The computed surfaces that would replace it are a claim registry (#5052 —
# OPEN, proposed, NOT yet built) and file overlap (agent-infra #1241) — state,
# not text over immutable history. ⛔ Recorded as PROPOSED because that is what
# it is: citing a ticket as an existing mechanism is how an ownerless promise
# ends up in a comment (#7051).

# ── the claim gate's HOLDER read (#8175) ─────────────────────────────────────
#
# There is NO comment-prose arm. `_CLAIM_RE`, the JEV ownership classifier, its
# prompt/version pin, its body cache and its context builder were DELETED here —
# not reworded — because repeated attempts at reading ownership out of a comment's
# TEXT each found a fresh string that read as ownership where there was none, and
# every miss went the unsafe way. The gate reads the fleet board instead; the
# module docstring's "Claim gate — the HOLDER, never prose" section carries the
# decision rule and the fail-closed matrix.
#
# ⛔ DO NOT REINTRODUCE a regex, a stoplist, a model call, or any other reader of
# comment TEXT here. "Is this comment a lane taking ownership?" is unanswerable
# on a fleet where every lane shares one GitHub account; the question the gate
# actually needs — "is a lane still working this issue?" — is already measured
# mechanically by `tools/fleet_state.py`.
#
# The freshness window is fleet_state's OWN (`state_freshness`), reused rather
# than duplicated: a SECOND window would be a second definition of "current",
# and the two would drift.


class NotAWorkItem(Exception):
    """The queried number is a PULL REQUEST, not a work item (#7009).

    Deliberately NOT a `SurfaceError`. A `SurfaceError` raised while a surface
    is scanned is caught by that surface's own handler and turned into a
    PER-SURFACE advisory note — right for "this one surface could not be
    measured", wrong for "you asked about the wrong object". At THIS call site
    the handler would additionally MISLABEL the failure: it records the surface
    as incomplete under a closing-reference-source reason and prescribes "fix gh
    auth/network", a remedy unrelated to the actual problem and one that no
    amount of retrying resolves.

    This class therefore carries a DISTINCT identity through to `main`, which
    turns it into exit 2 rather than into a mislabelled advisory note.

    `EXIT_INCOMPLETE` is the protocol's existing "do not start on a guess"
    verdict, so this needs no new exit code, flag or gate.

    ⛔ IT NOW CARRIES THE PR RECORD, AND `main()` RESOLVES IT (#9957). Before
    this, the refusal was where a PR-numbered dispatch ENDED: the gate said
    "re-run with the issue number" and left the caller to obey prose. Measured
    on #9941, three lanes held the issue and a fourth dispatch keyed on the PR
    number was about to be allowed, because the number one away from the work —
    and the tool already had it in hand, in the message it printed. So the
    refusal is now the RESOLUTION TRIGGER: `pr` is the PR entry the scan
    refused on (its title, head ref, body and computed closing references), and
    `resolve_pull_request_work_items` turns it into the work item(s) the run
    should have been asked about. A refusal remains the outcome only when that
    resolution FAILS (see the reason `main()` prints).
    """

    #: The PR entry the refusal was raised for (title / headRefName / body /
    #: closingIssuesReferences). Empty when the raiser had none read — the
    #: resolution then has nothing to resolve and the refusal stands.
    pr: dict

    def __init__(self, message: str = "", pr: dict | None = None) -> None:
        super().__init__(message)
        self.pr = pr or {}


class BoardRefusal(str):
    """The board's refusal SENTENCE, carrying the RECORDED CAUSE as data (#9392
    review P2).

    ⛔ A REFUSAL THAT NAMES A CAUSE MUST CARRY IT, NOT SPELL IT. `_board_can_answer`
    composes a sentence that embeds `reconciliation.registry_error` and ends with
    the sentence `fleet_state.registry_remedy(raw)` derived FROM it; the exit-2
    remedy then handed that whole sentence BACK to `registry_remedy`, so the note
    (never empty) always looked like a cause: the helper's "no cause recorded" arm
    was unreachable and the line asserted "the roster EXISTS but could not be read"
    for a board that recorded nothing — the "states more than the read
    established" defect this issue files, one cause over.

    A `str` subclass, so every existing consumer (f-strings, comparisons,
    `assertIn`, `who_is_on.py`) keeps working unchanged and the sentence stays the
    one wording; only a reader that wants the machine value (`read_holders` →
    `HolderRead.registry_error`) reaches for `.cause`. The pattern the
    `truncation_widenable` / `budget_exhausted` comments on `Surface` record, with
    the payload riding on the message rather than a new return shape — because
    `_read_board`'s `None`-when-unanswerable contract is pinned by tests and must
    NOT be loosened just to carry a cause.
    """

    #: The board's RECORDED cause, or ``None`` when it recorded none. Never prose.
    cause: str | None

    def __new__(cls, message: str = "", cause: str | None = None) -> BoardRefusal:
        obj = super().__new__(cls, message)
        obj.cause = cause
        return obj


class SurfaceError(Exception):
    """A surface could not be queried (INCOMPLETE — never CLEAN).

    ⛔ `detail` CARRIES THE UNTRUNCATED TEXT; THE MESSAGE DOES NOT (#9402
    review, P1). `_gh_json` one-lines the failing `gh` STDERR and cuts it at 200
    characters, so whatever the cut removes the classifier can never see — and
    `gh` prints transport chatter before the GraphQL error on a verbose or
    retrying run. Past 200 characters of it, the canonical `GraphQL: API rate
    limit already exceeded for user ID …` is gone from every string the gate
    reads: the rescue then never fired and the report prescribed
    `gh auth/network` for a spent budget, which is the exact defect #9402 files,
    on the exact surface it exists to rescue.

    ⛔ WHAT IS *NOT* THE MECHANISM, measured on this head so the next reader does
    not repeat the arithmetic: the cut applies to the STDERR only, never to the
    assembled message — the (long) command line is prefixed AFTER it. So a
    `--repo` slug of 36+ characters does NOT hide the signature on its own
    (`test_a_long_slug_alone_does_not_hide_the_exhaustion` pins that
    non-boundary). The hazard is the cut on the string it really applies to, and
    the raw text is attached HERE, where it is still whole, so
    `_is_budget_exhaustion` can classify it. A raiser that does not attach one
    keeps the old behaviour (classify on the message), so nothing else moves.
    """

    def __init__(self, message: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


@dataclass
class Hit:
    surface: str
    ref: str
    detail: str
    strength: str  # "strong" (issue number / closing reference) | "weak"
    # What KIND of fact produced a hit (#8560 review, #8625). The `issue landed`
    # surface sets every kind but `unclosed`; the ADVISORY `merged PRs naming an
    # OPEN issue` surface sets that one; the PR surface (`scan_pr_surface`, on its
    # OPEN and CLOSED samples alike) sets `prose-body` for a body that merely
    # MENTIONS the issue; every other surface leaves the default.
    # `format_report` and `who_is_on.py` word their answers from it, so a
    # `CLOSED` issue parked as NOT_PLANNED is never described as "already
    # shipped" — the refusal direction is identical (do NOT dispatch), but the
    # terminal fact is not a merge.
    #   "completed"    — the issue was closed as COMPLETED (a shipment)
    #   "not-planned"  — closed as NOT_PLANNED (declined/superseded)
    #   "closed"       — closed, reason unavailable/unrecognised
    #   "unclosed"     — a MERGED PR names an OPEN issue (#8625): evidence of a
    #                    REFERENCE, never of a closure; advisory-only
    #   "prose-body"   — a PR BODY that merely cross-references the issue (open or
    #                    closed): a reference, never in-flight work (weak,
    #                    non-blocking)
    # ⛔ There is no "shipped" kind: #8625 removed the timeline title arm's
    # authority, so the branch that read it became unreachable — and the file's
    # own standard at the hit-tag site ("a tag entry for it would be dead code
    # that reads as a live tier") refuses to keep one.
    kind: str = "collision"


def _is_default_branch(name: str, defaults: set[str]) -> bool:
    """Is `name` one of the repo's default-branch names?

    Case-INSENSITIVE on purpose. The `main`/`master` fallback seeds are
    exact-case, and when `origin/HEAD` cannot be resolved (a bare clone, a
    fetched-only mirror, a single-ref CI checkout) those seeds are ALL the tool
    has. A default branch spelled `Main` would then equal no seed, be
    auto-declared as a self-branch, and demote every PR whose head it is — the
    P0 re-entered through a spelling. Git branch names ARE case-sensitive, so
    this is a deliberate WIDENING of the refusal set; erring toward refusing
    leaves the hit BLOCKING, which is the fail-closed direction.

    The residual gap is stated, not hidden: a default branch outside the seed
    set entirely (say `trunk` or `develop`) is still auto-declared when
    `origin/HEAD` is unresolvable. Resolving the symref is the real fix and it
    covers every normal clone; the seeds are a fallback for the clones that have
    no symref. This is asserted by
    `test_case_variant_default_branch_is_refused`, which pins BOTH the widening
    and the gap so neither can drift unnoticed.
    """
    return name.lower() in {n.lower() for n in defaults}


def _standing_in(path: str | None) -> bool:
    """True when `path` IS the directory this process is running from.

    The ONLY condition under which self-identity is auto-detected, and
    deliberately narrower than "the caller named this path". Naming a path says
    where to LOOK, not whose work lives there: `--repo /other/lane` points at
    another lane's checkout, and auto-declaring ITS branch and worktree as the
    caller's own reported CLEAN on that lane's live work. Standing in a
    directory is the one thing that does make its checkout yours.

    `realpath` on both sides: `/var` and `/private/var` are the same place on
    macOS and the two producers disagree on the spelling (the same trap
    `Identity.owns_worktree` documents).
    """
    if not path:
        return False
    try:
        return os.path.realpath(path) == os.path.realpath(os.getcwd())
    except OSError:  # pragma: no cover - defensive
        return False


def _default_branch_names(git_bin: str, cwd: str, timeout: float) -> set[str]:
    """Names of the repo's DEFAULT branch, so auto-declaration can refuse them.

    ⛔ A default branch is not a lane's private branch. It is the shared landing
    target — and on a FORK pull request it is also the ordinary HEAD REF NAME,
    because a fork pushes from its own `main`. Declaring the default as "mine"
    therefore demotes a genuine in-flight PR to `weak` and reports CLEAN on real
    work. Measured: the fleet's hub checkout sits on `main`, so the *default*
    way to run this tool (`--repo .` from the hub) reproduced it, not an exotic
    path. Guarding only the SEARCHED-clone case was not enough.

    The resolved `origin/HEAD` is consulted first, with `main`/`master` as a
    fallback for a clone that has no remote HEAD symref. Erring toward a LARGER
    set is deliberate: refusing to auto-declare leaves the hit blocking, which
    is the fail-closed direction.
    """
    names = {"main", "master"}
    rc, out, _err, _to = _run(
        # NO `--short`: the short form is `origin/<branch>`, which
        # `_short_branch` deliberately refuses to rewrite (a bare `origin/x` is
        # indistinguishable from a local branch literally named `origin/x`). Ask
        # for the full ref so the tested helper — which drops exactly ONE
        # component, and only the remote one — does the parsing.
        [git_bin, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        cwd, timeout,
    )
    if rc == 0 and out.strip():
        # ⛔ `rsplit("/", 1)[-1]` was WRONG here and re-opened the very hole this
        # function exists to close. It kept only the LAST component, so a default
        # branch named `release/2024` was registered as `2024`: the real name was
        # missing from the refusal set, `release/2024` was auto-declared as a
        # self-branch, and the self arm (which runs FIRST for every PR, branch and
        # worktree) demoted genuine in-flight work on the default branch — CLEAN
        # on real work. The P0 re-entered through a parse.
        names.add(_short_branch(out.strip()))
    return names


def _short_branch(ref: str) -> str:
    """The bare branch name behind any ref spelling the tool EMITS.

    `refs/heads/x/y` and `refs/remotes/origin/x/y` both collapse to `x/y`.
    Exactly one component is dropped, and only from the REMOTE prefix: the
    remote namespace is the one with an extra `<remote>` component to remove.

    ⛔ Dropping a component from `refs/heads/` was a real bug here — a branch
    name may itself contain slashes, so `refs/heads/fix/3061-mine` became
    `3061-mine` and stopped matching the declaration, leaving the caller's OWN
    branch blocking. Caught by the test for exactly that case.

    A bare `origin/x` is deliberately NOT rewritten: it is indistinguishable
    from a local branch literally named `origin/x`, and guessing would suppress
    a real hit. Declare the short name or a full ref.
    """
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    for prefix in ("refs/remotes/", "remotes/"):
        if ref.startswith(prefix):
            rest = ref[len(prefix):]
            _head, sep, tail = rest.partition("/")
            return tail if sep else rest
    return ref


@dataclass
class Identity:
    """Who is running this pre-flight.

    `login` is the GitHub account, and it is the ONLY identity the ASSIGNEE
    surface consumes: it is what the assignee rule compares against. It does NOT
    attribute work to a specific LANE — every lane shares this account, so a
    login cannot distinguish lanes. That is why the claim gate reads the fleet
    board instead of a comment's author (#8175).

    `self_branches` / `self_worktrees` are the caller's OWN artifacts, and they
    exist because inferring ownership from TEXT is what manufactured the false
    COLLISIONs this issue is about (#3504 classes 1 and 4). The caller knows its
    own branch and worktree at call time, so they are declared here and a hit on
    one of them is reported as the caller's own work rather than a competing
    claim. This is an INPUT: nothing about a branch name is inspected to decide
    "is this mine?".

    ⛔ THE FLAGS AND THE AUTO-DETECTION PULL IN OPPOSITE DIRECTIONS, and the
    earlier wording of this docstring ran the two together:

      * OMITTING `--self-branch`/`--self-worktree` is fail-CLOSED. Adding a ref to
        this set REMOVES hits, so a missing declaration can only leave MORE refs
        blocking (`test_own_branch_declared_is_not_a_claim_but_undeclared_still_
        blocks` pins exactly that: undeclared => COLLISION).
      * The AUTO-DETECTION in `main()` is the FAIL-OPEN half: it declares the
        target checkout's own branch and root without being asked, so a WRONG
        auto-declaration is what suppresses a real finding.

    That asymmetry is why the repository DEFAULT branch is excluded from
    auto-declaration, and why an explicit `--self-branch` is never
    second-guessed. The flags are the authoritative input; the auto-detection is
    a convenience."""

    login: str | None = None
    self_branches: frozenset[str] = frozenset()
    self_worktrees: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Canonicalise the declared worktrees ONCE, at construction.

        ⛔ A raw string comparison is WRONG here, and it fails in the blocking
        direction. The same directory has two names on macOS — `/var` and
        `/private/var` are the same place — and the two producers do not agree:
        `tempfile` hands out `/var/...`, while `git worktree list --porcelain`
        prints the resolved `/private/var/...`. Comparing them as strings
        silently declines to recognise the caller's OWN worktree, which is
        #3504 class 4 reappearing inside the fix for #3504 class 4. Found by a
        mutation test: the worktree test only passed because the BRANCH check
        masked the path check.

        Both sides are normalised (the declaration here, the probe in
        `owns_worktree`), so the comparison is between canonical paths.
        """
        self.self_worktrees = frozenset(
            os.path.realpath(p) for p in self.self_worktrees if p
        )

    def owns_branch(self, ref: str | None) -> bool:
        """True when `ref` names one of the caller's own branches.

        ⛔ BOTH sides are normalised, and the REMOTE namespace is normalised too.
        A lane that pushes its branch has its own work appear as
        `refs/remotes/origin/<branch>`, which is a *different string* from the
        `refs/heads/<branch>` it declared. Stripping only `refs/heads/` left the
        remote copy comparing unequal and therefore BLOCKING, so the lane's own
        pushed branch refused its own dispatch — #3504 class 4 surviving in the
        one namespace every lane actually populates.

        Normalising both sides also removes the one-directional asymmetry the
        old docstring papered over: it claimed "a caller may pass either", which
        held for a full candidate against a short declaration but NOT for a
        short candidate against a full declaration.
        """
        if not ref:
            return False
        if not self.self_branches:
            return False
        return _short_branch(ref) in {_short_branch(b) for b in self.self_branches}

    def owns_worktree(self, path: str | None) -> bool:
        """True when `path` names one of the caller's own worktrees. Both sides
        are `realpath`-canonicalised (see `__post_init__`) — a raw comparison
        does not recognise the caller's own worktree under macOS's `/var` =
        `/private/var` aliasing, and unrecognised means BLOCKING."""
        if not path:
            return False
        return os.path.realpath(path) in self.self_worktrees


@dataclass
class RepoTarget:
    """The repo this verdict is ABOUT. `slug` (owner/name) is sent on every
    `gh` call; `path` is where the git surfaces run. Either may be None when it
    could not be derived — its surfaces then report INCOMPLETE, never a scan of
    some other repository."""

    slug: str | None = None
    path: str | None = None
    source: str = "unresolved"
    requested: bool = False
    # NO `named_by_caller` / `caller_checkout` field. An earlier revision carried
    # one to mean "the caller pointed at this checkout rather than a clone found
    # by search", and used it to decide which refs were the caller's own — so
    # `--repo <another lane's worktree>` declared THAT lane's branch and worktree
    # as the caller's and reported CLEAN on its live work. The distinction is
    # NOT an ownership gate (a path is where to LOOK, not whose work lives
    # there), and once `_standing_in` superseded it as the gate the field had no
    # reader left. Deleting it is deliberate: a write-only field whose docstring
    # explains a use that no longer exists is an invitation for the next reader
    # to "restore" the gate. The reasoning lives on `_standing_in`.


@dataclass
class Surface:
    name: str
    status: str = STATUS_CLEAN
    hits: list[Hit] = field(default_factory=list)
    note: str = ""
    truncated: bool = False
    truncation_note: str = ""
    # The LANE-ROSTER read's recorded cause, as DATA beside the note (#9392
    # review P2) — `None` means the board recorded none. `_incomplete_remedy`
    # used to re-derive this by handing the composed note back to
    # `registry_remedy`, which made the helper's "no cause recorded" arm
    # unreachable and asserted "the roster EXISTS" from a board that had recorded
    # nothing. Same lesson as `truncation_widenable` / `budget_exhausted` below.
    registry_error: str | None = None
    # Whether this cap is a SETTING the operator can raise (`--pr-limit`), or a
    # ceiling no setting can lift (#9423 review, P3). The REST rescue reads ONE
    # page at GitHub's per_page maximum, so on that path `--pr-limit` cannot
    # widen anything — and `_incomplete_remedy` must not prescribe it. Tracked
    # as DATA beside the note (not parsed back out of the note's prose), so the
    # note and the remedy cannot disagree about whether a cure exists.
    truncation_widenable: bool = True
    # Whether this surface failed on a BUDGET EXHAUSTION the tool MEASURED, as
    # DATA beside the note (#9423 review round 10, P3). `_incomplete_remedy`
    # used to re-parse the note's prose for the signature — and the note embeds
    # the gh error line only after `_one_line(..., limit=600)`, whose truncation
    # keeps the FIRST 600 chars: a verbose stderr with the signature past that
    # point lost it, and the report then prescribed "fix gh auth/network" for a
    # cause the same run had already established. A surface that WAS rescued from
    # an exhaustion knows the cause without reading a note, so it says so here.
    budget_exhausted: bool = False
    # Whether this surface's hits and failures can affect the VERDICT.
    # `advisory` surfaces are still queried, still reported, and still HIT —
    # they simply cannot block a dispatch and cannot force INCOMPLETE (#5251).
    authority: str = AUTHORITY_BLOCKING

    def add(self, ref: str, detail: str, strength: str,
            kind: str = "collision") -> None:
        self.hits.append(Hit(self.name, ref, detail, strength, kind))
        self.status = STATUS_HIT

    def incomplete(self, note: str) -> None:
        self.status = STATUS_INCOMPLETE
        self.note = note

    def mark_truncated(self, note: str, *, widen: bool = True) -> None:
        """The surface was queried but the list hit its cap, so it is PARTIAL.
        Tracked separately from `status` so a hit found in the partial list is
        still reported while the truncation keeps the run out of CLEAN.

        `widen=False` records a cap that NO setting can lift (the REST rescue's
        single page at GitHub's per_page maximum), so `_incomplete_remedy`
        states the cap without offering a `--pr-limit` that cannot help."""
        self.truncated = True
        self.truncation_note = note
        self.truncation_widenable = widen


# ── process helpers ──────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: str, timeout: float, env: dict | None = None):
    """Run a command and capture output. Never raises for non-zero exit;
    returns (rc, stdout, stderr, timed_out)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
        )
        return proc.returncode, proc.stdout, proc.stderr, False
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: command not found", False
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout:g}s", True
    except OSError as exc:  # pragma: no cover - defensive
        return 126, "", f"{cmd[0]}: {exc}", False


# C0/C1 control characters (ESC/CSI/OSC/BEL/DEL included) are stripped from
# every UNTRUSTED field before it reaches the report. The report IS the
# artifact a human reads to decide "do NOT dispatch", and GitHub-sourced text
# (issue title, PR titles) plus a git-remote-derived slug is
# attacker-controlled: an ESC sequence can blank or overwrite the VERDICT line
# on the reading terminal, and OSC 52 can rewrite the clipboard — a fail-open
# class. (Comment bodies were once listed here; #8175 stopped fetching them, so
# they are no longer a field this protects.) \t\n\r are kept here and collapsed to spaces by `_one_line`.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _sanitize(text: str | None) -> str:
    """Strip terminal control characters from an untrusted string."""
    return _CONTROL_RE.sub("", text or "")


# A whole ANSI escape SEQUENCE (its payload included), not just the ESC byte:
# an OSC 52 clipboard overwrite is `ESC ] 52 ; c ; <payload> BEL`, and
# stripping only the ESC/BEL leaves `<payload>` behind. It is applied to every
# GitHub-sourced DISPLAY field and to the issue's own `state` before that value
# decides a terminal reading (`scan_landed_surface` and `run_preflight` compare
# the de-sequenced state to `OPEN`), so a state carrying a sequence can neither
# blank the VERDICT line nor make this row and the landed row disagree about the
# same value. NOTE: comment bodies are no longer read at all (#8175), so this is
# no longer part of a claim decision.
_ESCAPE_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"   # OSC … BEL | ST
    r"|\x1b\[[0-?]*[ -/]*[@-~]"            # CSI …
    r"|\x1b[@-Z\\-_]"                      # Fe escape
    # NO fallback for any other ESC + one char. That alternative consumed the
    # character AFTER a bare ESC, so an ESC sitting just before a claim hid it
    # ("\x1bon it now" -> "n it now" — no match) — a fail-open divergence
    # from `main`, which matches that body. A bare ESC is removed by
    # `_CONTROL_RE` below instead, which drops the byte and KEEPS the
    # following character; whole escape SEQUENCES are still stripped above.
)


def _strip_control_sequences(text: str | None) -> str:
    """Remove whole ANSI escape sequences (payload included) plus any
    remaining control character, for MATCHING. Display uses `_sanitize`."""
    return _CONTROL_RE.sub("", _ESCAPE_RE.sub("", text or ""))


def _is_within(path: str, root: str) -> bool:
    """Is `path` the same as `root`, or inside it? (both already realpath'd.)"""
    if not path or not root:
        return False
    root = root.rstrip(os.sep)
    return path == root or path.startswith(root + os.sep)


def _one_line(text: str, limit: int = 200) -> str:
    flat = " ".join(_sanitize(text).split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


# ── matching ─────────────────────────────────────────────────────────────────

def number_present(text: str, issue: int) -> bool:
    """Boundary-exact issue-number match: 3061 matches '#3061', 'w3061',
    'fix/3061-x' but NEVER '30610' — and NOT a digit run inside a hex digest
    (#4935, #3611).

    The containing run decides it: a review-signature value (`sig=…1a4889d6a…`)
    is 64 hex characters, so every 4-digit substring occurs inside it by
    chance. Checking the SHAPE of the run rather than excluding vocabulary is
    what keeps this deterministic."""
    for match in re.finditer(rf"(?<![0-9]){issue}(?![0-9])", text or ""):
        if _inside_hex_digest(text, match.start(), match.end()):
            continue
        return True
    return False


# A run of this many `[0-9a-fA-F]` characters containing at least one LETTER is
# a DIGEST, not a set of issue references (#4935, #3611).
_HEX_RUN_MIN = 8
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _inside_hex_digest(text: str, start: int, end: int) -> bool:
    """True when ``text[start:end]`` sits inside a hex DIGEST.

    The run is expanded over `[0-9a-fA-F]` in both directions. It is a digest
    only when all three hold: the run is at least `_HEX_RUN_MIN` long, it holds
    at least one letter, and the matched number does NOT lead it.

    The SYMMETRIC test is what keeps this fail-CLOSED, and two review cycles are
    why it is symmetric. A fragment has hex on BOTH sides of the number:
    `d6233ab6`, `a4356bcd`, and a 64-hex review signature are strictly interior
    matches. A number glued to a word on either edge is a reference — `3061cafe`
    (`cafe` is a word; cycle 1 read this LIVE branch CLEAN) and `beef3061` /
    `facade3061` (there the word is the hex-looking part and the number is the
    reference; cycle 2 read these CLEAN). Requiring both edges to be interior
    makes every discarded case the blocking one, so this is strictly more
    fail-closed than either one-sided version it replaces."""
    lo = start
    while lo > 0 and text[lo - 1] in _HEX_DIGITS:
        lo -= 1
    hi = end
    while hi < len(text) and text[hi] in _HEX_DIGITS:
        hi += 1
    run = text[lo:hi]
    if len(run) < _HEX_RUN_MIN or not any(c.isalpha() for c in run):
        return False
    return lo < start and hi > end


#: Branch namespaces that are GENERATED by tooling, never chosen by a lane.
#: A `mergify/merge-queue/<hash>` branch is created and deleted by the merge
#: queue for each queued PR, so its name is a hash and carries no intent.
#:
#: ⛔ THE WIDTH OF THIS TUPLE IS A FAIL-CLOSED DECISION, NOT A CONVENIENCE. A
#: lane can create `refs/heads/mergify/3061-lane` locally, and a namespace listed
#: more broadly than what was measured would render that branch invisible on a
#: BLOCKING surface: a false CLEAN, which for this tool is strictly worse than a
#: false COLLISION. Name the sub-namespace that was actually measured, and widen
#: it only with a decision and a test that pins the widening.
#:
#: Every entry must be narrow enough that no lane would choose it. Each one below
#: is a bot-minted namespace whose refs were verified against their committer
#: identity in this repo (`mergify/merge-queue/`, `mq/merge-queue/`).
_GENERATED_BRANCH_PREFIXES = (
    "mergify/merge-queue/",
    "mq/merge-queue/",
)

#: The CI bot's refresh branch sits under a LANE-OWNED namespace (`chore/`), so
#: its prefix alone would be too wide: `chore/ci-timing-refresh-3061-manual` is a
#: name a lane could reasonably write, and excluding it would hide a claim on a
#: BLOCKING surface — again the direction that is strictly worse than a false
#: COLLISION. The generator mints `<prefix><short SHA>`, so the tail is anchored
#: to what it actually produces. Anything else stays a claim.
_CI_TIMING_PREFIX = "chore/ci-timing-refresh-"
_CI_TIMING_TAIL_MIN = 7
#: ⚠️ NOT `_HEX_DIGITS`: that set deliberately includes `A-F` because digest
#: matching needs it, but `git rev-parse --short` emits LOWERCASE only. An
#: uppercase tail is therefore a name only a lane could write, and filtering it
#: would be a false CLEAN on a blocking surface.
_CI_TIMING_TAIL_DIGITS = frozenset("0123456789abcdef")


def _is_generated_branch(name: str) -> bool:
    """True when ``name`` is a tool-generated branch, not a lane's claim.

    WHY THIS EXISTS ALONGSIDE THE HEX-DIGEST RULE (#3611). The digest rule
    (`_inside_hex_digest`) excludes a number INTERIOR to a hex run, and it is
    deliberately asymmetric: a number that LEADS a hex-looking run stays a
    reference, because `3061cafe` is a real branch name a lane wrote. That
    asymmetry is correct, so `mergify/merge-queue/6160bad001` — where `6160`
    leads the hash `6160bad001` — is genuinely indistinguishable from
    `3061cafe` by SHAPE alone. The two differ by ORIGIN, not by spelling, so
    origin is what this checks.

    Excluding a generated namespace is not fail-open AS LONG AS the namespace
    holds only tool-minted names: a lane cannot claim one, so a hit there was
    never in-flight work. It also cannot hide a real claim — a lane's branch
    (`fix/3611-…`) is untouched by the filter. Without it the surface reports a
    PHANTOM OWNER, which is worse than a miss: the caller is told `exit 1 =
    someone holds it` for an issue nobody holds, and the item is dropped from
    the queue.

    ⚠️ Both halves of that argument are load-bearing, and the first is the one
    that fails silently. Excluding a namespace WIDER than the measured one
    converts a false COLLISION into a false CLEAN, and this is a blocking
    surface — see `_GENERATED_BRANCH_PREFIXES`.

    The name is tested AFTER stripping the ref prefix the surfaces actually
    carry: `git for-each-ref` hands over `refs/remotes/origin/mergify/…` and
    `refs/heads/fix/…`, so a test on the bare name never fires on the real
    input."""
    stripped = name
    for prefix in ("refs/remotes/", "refs/heads/"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    # `refs/remotes/<remote>/<branch>`, so drop the remote segment too.
    if name.startswith("refs/remotes/"):
        _, _, rest = stripped.partition("/")
        stripped = rest or stripped
    if stripped.startswith(_CI_TIMING_PREFIX):
        tail = stripped[len(_CI_TIMING_PREFIX):]
        return (len(tail) >= _CI_TIMING_TAIL_MIN
                and all(c in _CI_TIMING_TAIL_DIGITS for c in tail))
    return any(stripped.startswith(p) for p in _GENERATED_BRANCH_PREFIXES)


#: The branch namespace whose `<n>` is an ISSUE reference: `fix/<n>-<slug>`.
#:
#: ⛔ ONE PREFIX, AND THE WIDTH IS A FAIL-CLOSED DECISION MADE THREE TIMES.
#: Cycle 1 listed `perf|ci|build|chore|docs|refactor|test|…`, and review produced
#: `perf/1200-rps-cache`, `ci/1800-timeout`, `chore/5000-graph-cleanup`; cycle 4
#: produced `feat/2048-batch` — in every one of those namespaces the number can be
#: a METRIC, a SIZE or a DATE, so cancelling a real PATH hit on one would be a
#: false CLEAN. `fix/` is the one namespace measured where `<n>-<slug>` is an
#: issue claim (828 remote refs, and the #9540 evidence itself, `fix/9397-…`).
#: The cost of the narrow set is a residual OVER-block for a `feat/<n>-…` or
#: `chore/<n>-…` worktree whose path is stale — the safe direction. Widening
#: needs a measured argument that the number CANNOT be a size (or a lookup that
#: the issue exists), plus a test, because every entry is a licence to demote.
_LANE_BRANCH_CLAIM = re.compile(r"fix/#?(\d{3,7})(?![0-9])(?:-|$)")


def _branch_names_other_issue(branch: str, issue: int) -> int | None:
    """The OTHER issue this branch's own name claims, or None.

    #9540: a worktree was matched to issue 3718 by its PATH alone — `/tmp/wt-3718`
    — while its branch was `fix/9397-about-edge-properties`. The directory name
    was a leftover; the lane's claim was #9397. A path is a SPELLING, and a
    substring of it is not evidence, so the stronger field (the branch a lane
    deliberately named) must be able to contradict it — otherwise the top of the
    product queue is frozen by a directory name and the gate has no dismissal
    switch by design (#8175).

    ⛔ NARROW ON PURPOSE, because this DEMOTES a blocking hit and a false CLEAN
    is the dangerous direction. It answers only one question — "does this branch
    claim a DIFFERENT issue, in the claim shape a lane actually writes?" — and
    returns None for everything else, so the caller keeps blocking:

      * a generated branch (`mergify/merge-queue/…`) claims nothing. The caller
        never asks this question about one (`branch_matchable` gates the
        demotion as well as the hit test), so the PATH still blocks —
        `test_worktree_on_a_generated_branch_is_still_found_by_its_path` and
        `test_generated_branch_with_a_claim_shaped_tail_still_blocks`;
      * a name with no claim shape (`chore/bump-2024-10`, `release-1.2.3`), or
        a number in a namespace that is not an issue reference
        (`perf/1200-rps-cache`, `ci/1800-timeout`, `feat/2048-batch`), returns
        None: a version, a date, a metric and a size are not claims, and
        cancelling a real PATH hit on one would be a false CLEAN;
      * a branch that names THIS issue returns None (the branch is the claim);
      * a detached worktree (`(detached)`) returns None — with no branch name
        there is no contradiction, so the path keeps blocking.

    ⛔ THE RESIDUAL IS STRUCTURAL AND IS STATED RATHER THAN HIDDEN: nothing in
    the listing distinguishes "(stale PATH, live branch)" from "(live PATH,
    branch recycled from another issue)". This rule chooses the BRANCH in both
    cases, because the branch is the field the lane deliberately named and it is
    the same attribution the fleet board makes (a lane is identified by its
    branch slug) — so the two surfaces agree instead of the path silently
    out-voting the branch. The alternative leaves the original defect in place
    (a leftover directory name freezes an issue forever, with no dismissal
    switch by design, #8175), which is the failure this rule exists to end.

    A branch whose only mention of this issue is INTERIOR TO A HEX DIGEST
    (`fix/9397-abc3718def`) is not read as naming it — that is #3611/#4935's
    existing rule, not an exception made here — so such a branch demotes the
    PATH hit exactly like any other foreign claim.
    """
    claimed_other: int | None = None
    for match in _LANE_BRANCH_CLAIM.finditer(branch or ""):
        if _inside_hex_digest(branch, match.start(1), match.end(1)):
            continue
        claimed = int(match.group(1))
        # ⛔ EVERY MATCH IS READ BEFORE ANY ANSWER, because the ORDER of the two
        # findings decides the verdict and returning on the first one is
        # fail-OPEN: a name carrying BOTH (`feat/9397-x/fix/3061-y` — a
        # dependency in the slug) claims THIS issue as well, so it must keep
        # blocking no matter which segment comes first.
        if claimed == issue:
            return None
        if claimed_other is None:
            claimed_other = claimed
    return claimed_other


def _pr_terminal_state(pr: dict) -> str | None:
    """The terminal state of a PR, or None while it can still be in flight.

    BOTH fields are read on purpose. GitHub's REST `/pulls` reports
    `state: "closed"` for merged and unmerged PRs alike, so `state` alone
    cannot name which happened; `mergedAt` is the field that names a merge. A
    rule keyed on `state == "merged"` would be dead code — REST never returns
    it — and the guard would silently never fire (#5052, the F10 trap)."""
    state = (pr.get("state") or "").strip().lower()
    # Liveness is decided by `state` alone where it speaks: an OPEN PR is never
    # terminal, so a stray `mergedAt` on an open payload cannot downrank a live
    # hit. (Hardening from review cycle 1; no real transport was observed to do
    # this — every open PR genuinely carries `mergedAt: null`.)
    if state in ("open", "opened"):
        return None
    if pr.get("mergedAt") or pr.get("merged_at"):
        return "merged"
    if state == "closed":
        return "closed"
    return None


def _is_issue_number(value: object) -> bool:
    """True only for a genuine issue number.

    ⛔ NOT `isinstance(value, int)`: `bool` subclasses `int`, so `True` passes —
    and `issue in [True]` is true for issue #1. One predicate, used by both the
    mapping and the bare-value arm, so they cannot drift apart.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _closing_ref_numbers(pr: dict) -> list[int]:
    """The issue numbers GITHUB ITSELF computed as closed by this PR.

    `closingIssuesReferences` is the SOURCE, not a re-derivation: it is the
    field the web UI uses to close issues on merge, so it is exact by
    construction. This tool used to re-implement it with a
    `close[sd]?|fix(?:es|ed)?|resolve[sd]?` regex — a second, drifting copy of a
    grammar GitHub already owns (#3504: "prefer the source over the regex").

    The key is REQUIRED on the blocking surfaces. A missing field is NOT "closes
    nothing": reading absence as empty would DROP a blocking signal, and
    dropping a blocking signal is the fail-OPEN direction — it is how a
    duplicate dispatch gets through. So absence raises, and the surface becomes
    INCOMPLETE.
    """
    refs = pr.get("closingIssuesReferences")
    if refs is None:
        raise SurfaceError(
            "closingIssuesReferences is ABSENT from the PR payload — refusing to "
            "read a missing field as 'closes nothing', which would silently DROP "
            "a blocking signal. Request the field explicitly from gh."
        )
    if not isinstance(refs, list):
        raise SurfaceError(
            "closingIssuesReferences is present but not a list "
            f"({type(refs).__name__}) — refusing"
        )
    numbers: list[int] = []
    for item in refs:
        # ⛔ `isinstance(..., int)` is TRUE FOR `bool` (`bool` subclasses `int`),
        # so `[{"number": true}]` appended `True` — and `issue in [True]` is true
        # for issue #1. GitHub does not emit it and the effect would only ADD a
        # hit (fail-closed), but a type check that silently accepts the wrong
        # type is not a type check. Both arms exclude it, so the element falls
        # through to the refusal below rather than being read as a number.
        if isinstance(item, dict) and _is_issue_number(item.get("number")):
            numbers.append(item["number"])
        elif _is_issue_number(item):
            numbers.append(item)
        else:
            # ⛔ The contract above is applied PER ELEMENT, not just to the
            # container. `["3504"]`, `[{"number": "3504"}]` and
            # `[{"number": None}]` all used to fall through and be appended
            # NOWHERE — so a field that was present and claimed to close N could
            # vanish with no INCOMPLETE, which is the same fail-open drop as
            # reading an absent field as empty. The body-regex union hides it
            # for a body reference, but GitHub also derives closing references
            # from the title and commit messages, where the body need not carry
            # a closing keyword at all. Refuse rather than drop.
            raise SurfaceError(
                "closingIssuesReferences contains an element this tool cannot "
                f"read as an issue number ({item!r}) — refusing to drop a "
                "closing reference silently"
            )
    return numbers


def closing_reference(text: str, issue: int) -> bool:
    """REGEX FALLBACK — the re-derivation, used ONLY where the field cannot be had.

    `_closing_ref_numbers` reads GitHub's own computed `closingIssuesReferences`
    and is the SOURCE every BLOCKING surface prefers (#3504: do not re-derive
    what GitHub already computed). This regex is the fallback for the two call
    sites where that field is unavailable — and they are NOT the same posture:

    * the ADVISORY closed-PR surface, whose payload comes from the REST `/pulls`
      endpoint and therefore carries no `closingIssuesReferences`. Every hit
      there is already `weak`, so an imprecise regex cannot block anything;
    * the BLOCKING open-PR surface when its list was rescued over REST (#9402):
      REST has no such field either, so on that call this regex is the ONLY
      closing test, and a non-terminal hit from it is `strong` and DECIDES the
      verdict. That is why the surface says the computed test is UNMEASURED
      (never "closes nothing"), and why the hit's detail text keys on the
      surface's AUTHORITY rather than on `use_closing_field`.

    So the accurate rule for an editor is: a blocking surface uses
    `_closing_ref_numbers` as the SOURCE, and this regex only as an
    ADDING-ONLY union (when the field was requested) or as the documented
    REST-rescue fallback, whose hits stay `strong`.

    ⛔ THE GRAMMAR LIVES IN ONE PLACE (#9957). This used to spell the pattern
    inline, and `_closing_numbers_in_text` needs the SAME grammar to answer the
    opposite question ("which issues does this text close?" instead of "does it
    close #N?"). Two copies of a grammar drifting apart is the precise defect
    #3504 deleted, so the pattern is defined once and this predicate is a
    membership test over it.
    """
    return issue in _closing_numbers_in_text(text)


#: The closing-keyword grammar: the ONE definition, used by
#: `closing_reference` (does this text close #N?) and by
#: `_closing_numbers_in_text` (which issues does this text close?).
_CLOSING_KEYWORD_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s*:?\s*#(\d+)(?![0-9])"
)


def _arm_numbers(text: str | None, pattern: re.Pattern) -> list[int]:
    r"""Numbers a `pattern` states in `text`, in order, deduped.

    One reader for every `(#N)`-shaped arm, so a match is parsed in exactly one
    place instead of a grammar being re-spelled per arm.

    ⛔ THE WIDTH IS UNBOUNDED ON PURPOSE (`\d+`, not `\d{3,7}`), AND THAT IS A
    FAIL-OPEN FIX. `closing_reference` delegates here, and its pre-#9957 pattern
    was `#{issue}(?![0-9])` with NO width bound — so narrowing the shared grammar
    would have stopped `Closes #42` matching issue 42. On the REST-rescued
    open-PR surface the body keyword is the ONLY closing signal and a
    non-terminal hit there is `strong`, so that narrowing silently DROPS a
    blocking hit and a false CLEAN can authorise a dispatch. Measured by review
    (#9957 cycle 1): `#7`, `#42`, `#99` went True -> False under the narrow form.
    `test_closing_reference_still_sees_short_issue_numbers` pins it. A width
    bound belongs in `_is_plausible_issue_number`, which is the RESOLVER's own
    policy — the resolver over-blocks by construction, while this predicate is
    one a verdict depends on.

    ⛔ AND NO HEX-DIGEST GUARD HERE, DELIBERATELY. `number_present` needs one
    because it searches ARBITRARY text for a number (#4935, #3611). Every
    pattern passed here anchors its digits to a non-hex delimiter — a `#`, then
    end-of-run or `)` / `-` — and `_inside_hex_digest` requires hex on BOTH
    sides, so it can never fire on these arms: the left side is always the
    pattern's own literal. A guard that cannot fire reads as protection and is
    not, which is the standard this file applies to every other dead face.
    ⛔ If a future arm does NOT anchor its digits, it must add the guard back.
    """
    body = text or ""
    numbers: list[int] = []
    for match in pattern.finditer(body):
        value = int(match.group(1))
        if value not in numbers:
            numbers.append(value)
    return numbers



def _closing_numbers_in_text(text: str | None) -> list[int]:
    """Every issue number this text CLOSES by keyword, in order, deduped.

    The inverse of `closing_reference`, over the same grammar — and over the same
    `_arm_numbers` body, so there is exactly one place a match is read.
    """
    return _arm_numbers(text, _CLOSING_KEYWORD_RE)


# ── #9957: a PULL REQUEST number is a dispatch input too ────────────────────
# A pull request number IS an issue number on GitHub, so a lane briefed by PR
# number is a NORMAL input — and the gate used to answer it with a refusal that
# named the linked issue and then asked the dispatcher to re-read. Measured on
# #9941: it closed #7970, three lanes held #7970, and a fourth dispatch keyed on
# `#9941` was one keystroke from being allowed. The gate held the mapping (it
# printed "It closes #7970") and did not use it — "a tool that cannot answer its
# own question".
#
# So the number is RESOLVED, never special-cased and never table-driven: the
# three mechanical signals a PR carries are read in the order that prefers the
# SOURCE over a re-derivation (#3504).
#
# ⛔ WHY THE UNION OF ALL THREE ARMS, AND NOT A PRECEDENCE CHAIN. A precedence
# chain STOPS at the first arm that speaks, so every arm after it is not gated at
# all — and a number nobody gates is a dispatch nothing refused, which is the
# one direction this tool must never move. The measured shape: a body saying
# `Closes #1000` with `(#7970)` in the title and `feat/7970-…` in the branch
# resolved to `[1000]` alone, so #7970 — the work item three lanes held in this
# issue's own case — was invisible; where that mis-resolution read CLEAN the run
# exited 0 while `collision_preflight 7970` exited 1.
#
# The cost of the union is NOISE: `(#2048)` in a title or `perf/1200-rps-cache`
# in a branch can be a SIZE or a METRIC, and resolving one drags an unrelated
# issue into the run (where an issue-absent surface makes the whole dispatch
# INCOMPLETE). For COLLISION and INCOMPLETE that noise IS the fail-CLOSED
# direction — the next pass retries an over-blocked candidate, while
# under-blocking duplicates a lane's work.
#
# ⛔ ONE DIRECTION IS NOT FAIL-CLOSED, AND IT IS THE ONE THAT STOPS RETRYING.
# If the metric-shaped number happens to name a real CLOSED issue, the run
# reports `VERDICT: LANDED (exit 4)` — and LANDED is the answer the caller
# treats as authoritative and therefore does NOT re-ask
# (`agent-infra/scripts/orchestrator-harness/refresh-candidates.sh`, whose LANDED
# arm is the one that ends the retry). So a noise arm can STARVE real work, not
# merely delay it. That is traded deliberately for RECALL — the opposite error is
# the duplicate dispatch this whole section exists to prevent — and it is stated
# here rather than left for a reader to infer from "noise is harmless". The
# remedy lives with the caller: a candidate that stops on LANDED must be
# checkable by its ISSUE number, which is where the real answer lives.
# `test_a_METRIC_shaped_branch_resolving_to_a_CLOSED_issue_is_LANDED` pins this
# direction, so narrowing an arm forces this note to be revisited with it.
#
# SOURCE-first order survives as the LABEL — `arm` names the highest-precedence
# contributing arm and the header names EVERY contributing arm — and never as a
# choice of which numbers are gated.
#
# ⛔ WHY THE BRANCH ARM IS WIDER THAN `_LANE_BRANCH_CLAIM`. That regex is also
# used to DEMOTE a blocking path hit, where widening is a false CLEAN (#9540) —
# so it stays `fix/`-only. Here a wider match can only resolve the run onto a
# number the PR itself will then be matched against, i.e. over-block at worst:
# #9941's branch is `feat/7970-record-serves-anchored-reads`, and a `fix/`-only
# resolver would have failed on exactly the case that filed this issue.
_TITLE_ISSUE_RE = re.compile(r"\(#(\d+)\)")
_RESOLVE_BRANCH_ISSUE = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9._-]*)/#?(\d+)(?![0-9])(?:-|$)"
)

#: The widest number the resolver will treat as an ISSUE. Deliberately generous
#: (this repo is at ~10k), and a policy of the RESOLVER only — it is why
#: `_arm_numbers` keeps its grammar unbounded: a bound here can only cost the
#: resolver a resolution (a refusal, the fail-closed direction), while a bound in
#: the shared grammar would narrow `closing_reference`, a predicate a verdict
#: depends on.
_ISSUE_NUMBER_MAX = 9_999_999


def _is_plausible_issue_number(value: int) -> bool:
    """Is `value` a number that could NAME an issue, rather than an id or a size?

    A wider run (`(#1234567890123)`) is a snowflake, a timestamp or a metric;
    resolving one would only spend a `gh issue view` on a number that cannot
    exist. The bound is the resolver's own — see `_ISSUE_NUMBER_MAX`.
    """
    return 0 < value <= _ISSUE_NUMBER_MAX

#: How each arm is named in the resolution line and in the refusal message.
_ARM_CLOSING = "closing reference"
_ARM_TITLE = "title `(#N)`"
_ARM_BRANCH = "branch `<type>/<n>-<slug>`"


@dataclass(frozen=True)
class WorkItemResolution:
    """What a PULL REQUEST number resolves to (#9957).

    `issues` is the UNION of every arm's numbers, so it is empty exactly when NO
    arm produced a work item; `checked` then carries one factual line per arm,
    which is the refusal's evidence. It is built by the resolver, never
    re-derived by the caller, so the message cannot claim a check that did not
    run.

    `arm` is the LABEL (the highest-precedence arm that contributed a number) and
    `arms` every contributing arm in precedence order — labelling only. Neither
    decides which numbers are gated: `issues` already holds them all, because a
    precedence CHAIN over the gating set drops the losing arms' work items and a
    work item nobody gates is a dispatch nothing refused.

    `closing_field_unreadable` records that GitHub's computed
    `closingIssuesReferences` could not be read (#9402). The closing arm then
    rests on the body's closing keyword ALONE, which is a PARTIAL read — the
    success header must say so, or an authoritative-sounding "Resolved via
    closing reference" is printed over evidence the tool never had.
    """

    number: int
    issues: tuple[int, ...]
    arm: str | None
    checked: tuple[str, ...]
    dropped_self: bool
    arms: tuple[str, ...] = ()
    closing_field_unreadable: bool = False
    @property
    def resolved(self) -> bool:
        return bool(self.issues)

    @property
    def contributing_arms(self) -> tuple[str, ...]:
        """Every arm that contributed a gated number, precedence order.

        Falls back to `arm` so a caller never has to know both fields exist.
        """
        if self.arms:
            return self.arms
        return (self.arm,) if self.arm else ()


def resolve_pull_request_work_items(
    pr: dict, number: int,
) -> WorkItemResolution:
    """The work item(s) a PULL REQUEST serves — the number a dispatch meant.

    Three mechanical signals, ALL of them read (see the section note above), in
    SOURCE-FIRST order only for labelling: GitHub's own computed
    `closingIssuesReferences` (unioned with the body's closing keyword, the same
    deliberate ADDING-ONLY union `scan_pr_surface` documents), the repo's own
    `(#N)` title convention, and the `<type>/<n>-<slug>` branch convention. The
    returned `issues` is the UNION of the three — never the first arm that
    speaks — because a dropped arm is a work item the caller will not gate.

    ⛔ THE PR'S OWN NUMBER IS NEVER A WORK ITEM. A PR that "closes" itself is
    the same object, so `number` is dropped from every arm's result — without
    that, resolution would hand the caller back the number it refused, which
    either loops or re-refuses with a misleading reason. `dropped_self` records
    that it happened, so the refusal can say so instead of claiming nothing was
    found.

    ⛔ AN UNREADABLE CLOSING FIELD IS NOT AN EMPTY ONE. `_closing_ref_numbers`
    raises on an absent/malformed field (the REST-rescued list genuinely has
    none, #9402), and reading that as "closes nothing" is the fail-OPEN
    direction. The arm records that it could not be read — as
    `closing_field_unreadable`, which the SUCCESS header renders too — and it
    never asserts a fact the read did not establish. Its numbers on that path
    are the body's closing keyword alone, so the header names that provenance
    instead of claiming the computed field spoke.

    ⛔ COST: EVERY resolved work item runs the FULL pre-flight. A PR closing N
    issues costs N pre-flights — N surface enumerations on the fleet-shared
    GraphQL budget (#100-121's first-class concern) — and the union above is
    precisely what makes N larger than one. Deliberate: the cheap alternative is
    gating fewer items, which is the dropped-arm fail-OPEN this resolver exists
    to remove. There is no cap, so the cost is bounded only by the number of
    issues a PR names; a batch PR dispatched by number pays it in full.

    No I/O, no verdict: this answers "which work items?" and nothing else, so
    it is directly testable and cannot itself decide a dispatch.
    """
    checked: list[str] = []
    unreadable = False
    try:
        closing = list(_closing_ref_numbers(pr))
    except SurfaceError:
        unreadable = True
        closing = []
    body_numbers = _closing_numbers_in_text(pr.get("body"))
    closing += [n for n in body_numbers if n not in closing]

    title_numbers = _arm_numbers(pr.get("title"), _TITLE_ISSUE_RE)
    head = pr.get("headRefName") or ""
    if _is_generated_branch(head):
        # A tool-minted branch (`mergify/merge-queue/6160bad001`) claims
        # nothing — the same reason it is not read as an issue claim anywhere
        # else in this module.
        branch_numbers: list[int] = []
        branch_note = f"branch {head!r} is tool-generated and claims nothing"
    else:
        branch_numbers = _arm_numbers(head, _RESOLVE_BRANCH_ISSUE)
        branch_note = (
            f"branch {head!r} names no issue as `<type>/<n>-<slug>`"
            if not branch_numbers else
            "branch " + repr(head) + " names "
            + ", ".join(f"#{n}" for n in branch_numbers)
        )

    if unreadable:
        checked.append(
            "closing references: the computed field could not be read and the "
            "body carries no closing keyword"
            if not body_numbers else
            "closing references: the computed field could not be read; the body's "
            "closing keyword names " + ", ".join(f"#{n}" for n in body_numbers)
        )
    elif closing:
        checked.append(
            "closing references name " + ", ".join(f"#{n}" for n in closing)
        )
    else:
        checked.append(
            "closing references name nothing (the computed field is empty and the "
            "body has no closing keyword)"
        )
    checked.append(
        f"title {pr.get('title')!r} names no issue as `(#N)`"
        if not title_numbers else
        "title " + repr(pr.get("title")) + " names "
        + ", ".join(f"#{n}" for n in title_numbers)
    )
    checked.append(branch_note)

    dropped: set[int] = set()
    #: The UNION of every arm's kept numbers, in the order the arms contributed
    #: them; `arms` records which arm contributed each — both in SOURCE-first
    #: order (closing, title, branch), which is the order the LABEL uses.
    gated: list[int] = []
    arms: list[str] = []

    def _keep(values: list[int]) -> list[int]:
        kept: list[int] = []
        for value in values:
            if value == number:
                dropped.add(value)
                continue
            if _is_plausible_issue_number(value) and value not in kept:
                kept.append(value)
        return kept

    for arm, values in (
        (_ARM_CLOSING, closing),
        (_ARM_TITLE, title_numbers),
        (_ARM_BRANCH, branch_numbers),
    ):
        # ⛔ EVERY ARM IS READ, AND THE UNION IS WHAT GETS GATED. Stopping at the
        # first arm that speaks drops the other arms' numbers, and a work item
        # nobody gates is one a dispatch is never refused for. An extra arm can
        # only ADD a refusal — `_EXIT_SEVERITY` aggregates worst-first — so the
        # union is safe and a chain is not. `arms` keeps the SOURCE-first order
        # for the LABEL.
        kept = _keep(values)
        fresh = [value for value in kept if value not in gated]
        if fresh:
            arms.append(arm)
            gated.extend(fresh)

    if not gated:
        return WorkItemResolution(
            number=number, issues=(), arm=None, checked=tuple(checked),
            dropped_self=bool(dropped), arms=(),
            closing_field_unreadable=unreadable,
        )
    return WorkItemResolution(
        number=number, issues=tuple(sorted(gated)), arm=arms[0],
        checked=tuple(checked), dropped_self=bool(dropped),
        arms=tuple(arms), closing_field_unreadable=unreadable,
    )


def _resolution_route(resolution: WorkItemResolution) -> str:
    """How the resolution was REACHED, named honestly (#9957 review).

    The success header is the one place a caller reads *why* a number resolved,
    so it may never sound more authoritative than the read behind it. Two things
    it must disclose:

    * EVERY contributing arm — the union means a header naming one arm would
      hide the others' work items from the reader;
    * a PARTIAL closing read. On the REST-rescued open-PR surface (#9402) the
      computed `closingIssuesReferences` is absent for EVERY entry, and the
      closing arm then rests on the body's closing keyword alone. Printed as a
      bare "Resolved via closing reference", that partial read is
      indistinguishable from the authoritative one — and the numbers of the
      arms that lost are then never shown anywhere.
    """
    arms = list(resolution.contributing_arms)
    if not resolution.closing_field_unreadable:
        return ", ".join(arms)
    partial = (
        "the body's closing keyword ONLY — GitHub's computed "
        "`closingIssuesReferences` field could not be read"
    )
    if _ARM_CLOSING in arms:
        arms[arms.index(_ARM_CLOSING)] = f"{_ARM_CLOSING} ({partial})"
    else:
        arms.append(
            "the closing arm rested on " + partial + " and named nothing"
        )
    return ", ".join(arms)


def _resolution_failure(resolution: WorkItemResolution) -> str:
    """The refusal that NAMES what could not be resolved (#9957).

    Fail-closed by construction: this string is only ever reached on the path
    that returns `EXIT_INCOMPLETE`, and it never claims a check that did not
    run — the evidence lines are the resolver's own, not a re-telling.
    """
    number = resolution.number
    if resolution.dropped_self and not resolution.issues:
        return (
            f"could not resolve #{number} to a work item: every work-item "
            f"reference it carries is #{number} — the PR's OWN number, which is "
            "the PR and not the work it serves. Nothing was checked about any "
            "issue, and that is NEVER clean. Re-run with the issue number."
        )
    return (
        f"could not resolve #{number} to a work item, so no verdict about the "
        f"issue it serves could be computed. Checked: {'; '.join(resolution.checked)}. "
        "Nothing was checked about any issue, and that is NEVER clean. Re-run "
        "with the issue number."
    )


# ── git surfaces ─────────────────────────────────────────────────────────────

# ── #6622: a RECORD of work is not a WORKER ─────────────────────────────────
# Both gates below exist because a blocking surface was counting a *record* as a
# holder, and exit 1 is a hard "do not dispatch" in every enforcement site
# (epic-executor, issue-workflow, executing-plans, subagent-driven-development).
# So a record of work that no longer exists blocked the item INDEFINITELY: the
# lane is told somebody holds it, takes the next item, and nobody ever picks
# this one up. The measured instances (2026-09-29) were two worktree
# directories that did not exist and a `.worktrees/` entry with no index.
#
# ⛔ THE SURFACES ARE MADE EVIDENCE-AWARE, NOT DELETED. `[local worktrees]`
# prevents REAL duplicate work (#2985 vs #3005), so removing it would be the
# over-correction the issue warns against. The defect is narrower: the surface
# could not tell a record from a worker.
#
# ⚠️ The asymmetry is deliberate, and matches this tool's stated posture — a
# missed duplicate is worse than a false alarm. Only conditions that make work
# IMPOSSIBLE downgrade a hit (`exists=no`, `index=no`, a remote that is not a
# configured remote). Recency is REPORTED (`age=`) but never applied: an agent
# editing files makes no git call, so a live worktree's index mtime goes stale
# while it still holds the work, and that false-negative direction is the
# dangerous one.


def _refspec_namespace_prefix(dest: str) -> str | None:
    """The `refs/remotes/…/` PREFIX a fetch-refspec DESTINATION populates, else None.

    ⛔ NON-GLOB DESTINATIONS AND MID-PATH `*` ARE THE TWO TRAPS, AND
    NEITHER IS EXOTIC.

    (i) NON-GLOB. `git remote set-branches origin main` writes
    `+refs/heads/main:refs/remotes/origin/main` — no `*` at all. Skipping
    the non-glob form (the first cut of this fix did exactly that) yields
    an EMPTY namespace set, and an empty set demotes EVERY `refs/remotes/…`
    ref — so a configured remote's genuine remote-tracking branch became a
    false CLEAN. Taking the PARENT errs WIDE (it also covers siblings that
    refspec does not fetch), and on this surface wide is SAFE.

    (ii) A `*` IS NOT ALWAYS THE LAST SEGMENT. Git accepts
    `+refs/heads/*:refs/remotes/x/*/y` and creates real tracking refs like
    `refs/remotes/x/<branch>/y`. A prefix taken as the PARENT of that
    destination keeps the literal `*` (`refs/remotes/x/*/`), which prefixes
    NO real ref, so the branch was demoted — a fail-open, and the reason the
    rule is not simply "parent of the destination".

    The rule that covers both: stop at the FIRST `*` when there is one,
    else take the parent of the exact target. The two agree on the common
    forms (`refs/remotes/origin/*` and `refs/remotes/origin/main` both yield
    `refs/remotes/origin/`) and both err WIDE.

    ⛔ ONE RULE, TWO CALLERS, AND THAT IS THE POINT. `_remote_tracking_namespaces`
    needs the SET of prefixes; `_remote_tracking_namespace_remotes` needs each
    prefix's OWNING REMOTE. A second hand-rolled parse would be free to drift
    from this one, and a drift here is a demotion decision — so the parse lives
    once. (#7693 review round 2.)
    """
    dest = dest.strip().lstrip("+")
    if not dest.startswith("refs/remotes/"):
        return None
    star = dest.find("*")
    prefix = dest[:star] if star != -1 else dest.rsplit("/", 1)[0] + "/"
    return prefix if prefix.startswith("refs/remotes/") else None


def _remote_tracking_namespaces(
    git_bin: str, repo: str, timeout: float,
) -> set[str] | None:
    """The `refs/remotes/…` PREFIXES the configured remotes actually fetch into.

    ⛔ THE REFSPEC IS THE DISCRIMINATOR, NOT THE REMOTE'S NAME (#6622). A
    `refs/remotes/<x>/…` ref is a remote-tracking BRANCH only when some
    configured remote's FETCH REFSPEC targets that namespace. Any other
    namespace is a one-shot fetch TARGET: `git fetch <url>
    refs/pull/N/head:refs/remotes/pr/N` writes a ref that names the CACHE KEY.

    READING THE NAME INSTEAD GOT THIS WRONG IN THREE WAYS, and all three are in
    the FAIL-OPEN direction on a blocking surface:
      * a remote whose name contains a slash (`git remote add foo/bar <url>`)
        fetches into `refs/remotes/foo/bar/*`, so comparing the FIRST path
        segment against the full name set never matched;
      * a non-standard refspec (`remote.origin.fetch
        +refs/heads/*:refs/remotes/upstream/*`) declares a namespace that is not
        the remote's name at all;
      * a SINGLE-SEGMENT cache key (`refs/remotes/pr5343`) has no third segment,
        so a "namespace + tail" split missed it and the false COLLISION this
        issue is about survived for it;
      * a NON-GLOB destination is not exotic — `git remote set-branches origin
        main` writes `+refs/heads/main:refs/remotes/origin/main` with no `*` at
        all. Skipping the non-glob form leaves the set EMPTY, and an empty set
        demoted every `refs/remotes/…` ref. The PARENT of each destination is
        taken instead, which covers both forms (`refs/remotes/origin/*` and
        `refs/remotes/origin/main` both yield `refs/remotes/origin/`) and errs
        WIDE — the safe direction on a blocking surface.

    ⛔ AND THE CONFIGURED REMOTES' NAMES ARE ADDED, NOT SUBSTITUTED. A remote can
    be configured with NO fetch refspec at all, in which case the refspec arm
    contributes nothing for it; `refs/remotes/<name>/` can only make MORE refs
    block, so this arm cannot reopen the hole the refspec arm closes. The two are
    combined rather than chosen between.

    Measured on this repo (2026-10-02): the only refspec is
    `+refs/heads/*:refs/remotes/origin/*`, so the only live namespace is
    `refs/remotes/origin/`, while `refs/remotes/pr/*` alone holds 2,810 refs
    named `pr/1 … pr/2810` and `refs/remotes/pr5343` … `refs/remotes/pr2996`
    hold ten more, every one a PR number.

    None is fail-CLOSED: an unreadable refspec list leaves every ref blocking,
    exactly as before this change.
    """
    try:
        rc, out, _err, timed_out = _run(
            [git_bin, "config", "--get-regexp", r"^remote\..*\.fetch$"],
            repo, timeout,
        )
    except Exception:  # pragma: no cover - _run raises only on programmer error
        return None
    # ⛔ `git config --get-regexp` exits 1 when there is NO MATCH, which is a
    # SUCCESSFUL read of an empty configuration — not a read failure. Collapsing
    # the two would leave every ref blocking forever in a repo with no configured
    # remote, while a real error (128, 127) must still answer None. Both
    # directions are pinned by tests.
    if timed_out or rc not in (0, 1):
        return None
    namespaces: set[str] = set()
    for line in out.splitlines():
        _key, _sep, value = line.partition(" ")
        _src, colon, dest = value.strip().partition(":")
        if not colon:
            continue
        prefix = _refspec_namespace_prefix(dest)
        if prefix is not None:
            namespaces.add(prefix)
    # ⛔ THE REMOTE NAMES ARE A FAIL-CLOSED ADDITION, NOT A REPLACEMENT. A remote
    # can be configured with NO fetch refspec at all, and its namespace would
    # then never appear above. Adding `refs/remotes/<name>/` can only make MORE
    # refs block, so this arm can never open the fail-open hole the refspec arm
    # exists to close — which is why the two are combined rather than chosen
    # between. (It is also what a slash-named remote needs: `git remote add
    # foo/bar <url>` fetches into `refs/remotes/foo/bar/*`, covered by both.)
    try:
        rc_remotes, out_remotes, _err2, timed_out2 = _run(
            [git_bin, "remote"], repo, timeout,
        )
    except Exception:  # pragma: no cover - _run raises only on programmer error
        return None
    if rc_remotes != 0 or timed_out2:
        return None
    for name in out_remotes.splitlines():
        name = name.strip()
        if name:
            namespaces.add(f"refs/remotes/{name}/")
    return namespaces


def _fetch_cache_remote(
    refname: str, remote_namespaces: set[str] | None,
) -> str | None:
    """The namespace segment of a fetch-cache ref, else None (the BLOCKING answer).

    Returns a segment only when the ref is under `refs/remotes/` and NO
    configured remote's refspec targets it. None — the blocking answer — is
    returned for a local branch, for a ref inside a live namespace, and whenever
    the namespace set is UNREADABLE ***or EMPTY***, so every uncertain case stays
    exactly as strict as it was.

    ⛔ AN EMPTY SET IS NOT "EVERYTHING IS A CACHE" — it is an INABILITY TO TELL,
    AND IT FAILS CLOSED. The set is empty when the repo configures no remote (and
    no fetch refspec) at all. A `refs/remotes/origin/…` ref can then be a
    leftover of an earlier configuration or a deliberately created one, and the
    tool can affirm neither. Reading an empty set as a cache demoted EVERY remote
    ref in such a repo — the fail-open direction on a blocking surface, and the
    shape this suite's own fixture (which configures no remote) would have hit.
    """
    if not remote_namespaces:
        return None
    if not refname.startswith("refs/remotes/"):
        return None
    if any(refname.startswith(ns) for ns in remote_namespaces):
        return None
    rest = refname[len("refs/remotes/"):]
    return rest.split("/", 1)[0] or None


def _worktree_liveness(
    path: str, locked: str | None = None, prunable: str | None = None,
) -> tuple[bool, str]:
    """(can this registered record still hold work?, the basis, rendered).

    ⛔ PATH-ABSENCE ALONE IS NOT EVIDENCE OF DEATH — it is evidence of a
    REGISTRATION git no longer matches, and those come apart in the direction
    that matters. `mv <worktree> <elsewhere>` leaves a fully working worktree:
    a linked worktree's `.git` is a pointer holding an ABSOLUTE `gitdir:`, so
    git keeps working from the new location — while `git worktree list` goes on
    reporting the OLD path and tags the record `prunable gitdir file points to
    non-existent location`. Demoting on `exists=no` alone therefore reported a
    LIVE lane as `non-blocking` and let this gate exit 0, which is the exact
    false-CLEAN the pre-dispatch check exists to prevent (the duplicate-ownership
    defect #3061 was filed for).

    The discriminator is git's ABORTED-ADD marker, `initializing`, which is the
    only value that means "cannot hold work": it is the shape MEASURED for both
    dead records in this repo. A LOCK IS PRUNE-PROTECTION, NOT LIVENESS —
    `git worktree lock` is documented for a worktree "on a portable device or
    network share which is not always mounted", i.e. one whose path is EXPECTED
    to be absent — and `--porcelain` emits `locked` INSTEAD OF `prunable`, so a
    moved worktree its owner also locked reports only `locked portable`. Every
    other lock, a lock-free `prunable`, and a bare absence FAIL CLOSED.

    `exists=no` and `gitdir=no` remain EVIDENCE and are always reported, so a
    false hold is visible at a glance instead of having to be re-derived by hand.
    The age is reported for the same reason.

    ⛔ UNREADABLE IS NOT ABSENT. `os.path.isdir` answers False for EACCES and
    ESTALE as well as for a missing directory, so demoting on it would turn a
    live-but-unreadable worktree (a stale mount, a directory owned by another
    user) into a false CLEAN on a BLOCKING surface — the dangerous direction.
    The probes therefore separate the two and FAIL CLOSED on everything except
    FileNotFoundError, the same polarity `age` already uses.

    ⛔ `gitdir=` IS DELIBERATELY NOT AN INDEX TEST, and the label says what is
    MEASURED rather than what was hoped for. For a LINKED worktree `<path>/.git`
    is a one-time gitdir POINTER; the index lives in the main repo's
    `.git/worktrees/<name>/index` — and a worktree created with `--no-checkout`
    has no index BY DESIGN while still being a worktree a lane holds, which is
    exactly the shape this repo's own test fixtures create. An `index=` label
    there asserted `index=yes` about a tree with no index: the opposite of the
    evidence this fix exists to surface.
    """
    def _shape(basis: str) -> tuple[bool, str]:
        """Demote ONLY on git's ABORTED-ADD marker; otherwise FAIL CLOSED.

        ⛔ A LOCK IS PRUNE-PROTECTION, NOT A LIVENESS SIGNAL. `git worktree
        lock` exists for a worktree "on a portable device or network share which
        is not always mounted" — i.e. precisely a worktree whose PATH is EMPTY
        EXPECTED TO BE ABSENT. Reading any lock as "cannot hold work" therefore
        demotes a live lane, and `--porcelain` emits `locked` INSTEAD OF
        `prunable` (git's own if/else-if), so the prunable guard below never sees
        it: a MOVED worktree that its owner also LOCKED reports only
        `locked portable` and was read as non-blocking — a false CLEAN on this
        gate, the duplicate-ownership direction (#3061).

        The one value that does mean "cannot hold work" is `initializing`, the
        marker an ABORTED `git worktree add` leaves behind — the shape MEASURED
        for both dead records in this repo. Every other lock, and a lock-free
        `prunable`, and a bare absence, keeps blocking.
        """
        lock = f"locked={locked}" if locked else "locked=no"
        if locked == "initializing":
            return False, (
                f"{basis} {lock} — aborted `git worktree add`; cannot hold work"
            )
        if locked:
            return True, (
                f"{basis} {lock} — a lock is prune-protection, NOT a liveness "
                "signal — NOT demoted (fail-closed)"
            )
        if prunable:
            return True, (
                f"{basis} {lock} prunable={prunable} — a moved worktree and a "
                "deleted one are indistinguishable here, and a moved one is "
                "LIVE (its .git holds an absolute gitdir) — NOT demoted "
                "(fail-closed; run `git worktree prune` if genuinely dead)"
            )
        return True, f"{basis} {lock} — no lock, NOT demoted (fail-closed)"

    if not path:
        return _shape("exists=no (record carries no path)")
    try:
        os.stat(path)
    except FileNotFoundError:
        return _shape("exists=no (registered directory is not on disk)")
    except OSError as exc:
        return True, f"exists=unreadable ({exc.__class__.__name__}) — not demoted"
    if not os.path.isdir(path):
        return _shape("exists=no (registered path is not a directory)")
    marker = os.path.join(path, ".git")
    try:
        os.stat(marker)
    except FileNotFoundError:
        return _shape("gitdir=no (no .git marker: not a usable worktree)")
    except OSError as exc:
        return True, f"exists=yes gitdir=unreadable ({exc.__class__.__name__})"
    try:
        age_hours = max(0.0, (time.time() - os.path.getmtime(marker)) / 3600.0)
    except OSError:
        return True, "exists=yes gitdir=yes age=unreadable"
    return True, f"exists=yes gitdir=yes age={age_hours:.1f}h"


# ── THE GATE'S PRIVATE BOARD CACHE (agent-infra #1985 follow-up) ────────────────────────────────
# WHY. A refresh pass runs this tool once PER CANDIDATE (6 in a pass), and every run rebuilt the fleet
# board: measured 15.9 s of a 19 s run (`gh_json` -> `open_issues` alone 11.2 s, plus ~3 s of
# backoff). That is N x the same whole-repo read; it slows every pass into contention with the
# controller's own sweep, and a CONTENDED GitHub read comes back as an UNQUERYABLE SURFACE — which is
# not a verdict, so the backlog never filled and create activity stopped with no error anywhere.
#
# WHAT. OPT-IN, and ONE value only: the PARTIAL board this gate rebuilds (`build_state` with
# `ci_verdict=None, live_diff=None`), stored for the duration of a pass and served to the NEXT
# candidate in it. Only SUCCESSFUL, answerable, correctly-stamped rebuilds are stored — an unreadable
# surface must never be cached as "empty", which would turn a probe failure into a clean verdict.
# ⛔ The git enumerations (`for-each-ref refs/heads`, `refs/remotes`, `rev-list --first-parent`) are
# deliberately NOT cached: they cost ~65 ms combined (~0.4% of the rebuild), and the branch set is
# exactly the signal that a lane claimed an issue seconds ago — a snapshot that outlives a branch
# creation hides the one blocker the branch surfaces supply (#4256). Zero staleness is affordable
# there; it is not affordable here (the rebuild is the 84%).
#
# ⛔ THE CACHE DIRECTORY IS NOT SETTABLE, AND THAT IS THE POINT (review P1). A caller-selectable
# directory is a caller-selectable BOARD: `_board_can_answer` / `_board_is_for_repo` / `state_freshness`
# are all fields read OUT of that same file, so a crafted board — fresh, answerable, correctly stamped,
# listing no holder — clears a live holder and sends a second lane into one checkout (#3061 / #4256).
# So the module contract holds unchanged: no environment variable NAMES the board. The seam below only
# ENABLES the gate's own copy, and the directory is DERIVED from `fleet_state.DEFAULT_OUT` — the same
# directory the board itself is read from, i.e. exactly the trust boundary this tool has always
# accepted (a same-user process can forge the shared board too; the gate never claimed to defend that,
# and a signature would need a key it does not have). The KEY carries the slug AND repo_root, so two
# repos — or two clones of one repo — can never answer each other's pre-flight.
#
# Env seams (both are rows in the module docstring's table):
#   COLLISION_PREFLIGHT_BOARD_CACHE      "1" enables the cache (default: unset => inert, behaviour
#                                        byte-identical; any other value => off)
#   COLLISION_PREFLIGHT_BOARD_CACHE_TTL  seconds a cached board stays servable (default 300), clamped
#                                        to `fleet_state.STATE_FRESH_S`; <= 0 disables the cache
BOARD_CACHE_ENV = "COLLISION_PREFLIGHT_BOARD_CACHE"
BOARD_CACHE_TTL_ENV = "COLLISION_PREFLIGHT_BOARD_CACHE_TTL"
BOARD_CACHE_DIRNAME = ".preflight-board-cache"
BOARD_CACHE_TTL_DEFAULT = 300.0
_BOARD_CACHE_ON = frozenset({"1", "true", "yes", "on"})


def _cache_dir() -> str | None:
    """The gate's own cache directory — DERIVED, never named by the caller (see above)."""
    if (os.environ.get(BOARD_CACHE_ENV) or "").strip().lower() not in _BOARD_CACHE_ON:
        return None
    return os.path.join(
        os.path.dirname(os.fspath(fleet_state.DEFAULT_OUT)), BOARD_CACHE_DIRNAME,
    )


def _cache_path(key: str) -> str | None:
    d = _cache_dir()
    if not d:
        return None
    digest = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(d, digest + ".json")


def _cache_ttl() -> float:
    """Seconds a cached board stays servable — bounded by the BOARD'S OWN window.

    `nan` / `inf` are the trap values for a numeric env seam: `inf <= 0` and `x > nan` are both
    False, so a single mistyped export would otherwise make an entry immortal — the same defect the
    numeric seams' `nan`/`inf` tests exist to prevent. And a RAISED TTL must not outlive the window
    `fleet_state` itself enforces, or the cache would serve a board the fleet already calls STALE;
    the ceiling is therefore `STATE_FRESH_S`, not the caller's wish. A non-numeric value falls back
    to the default (the tightest of the accepted bounds) rather than crashing a cache that is, at
    worst, an optimisation.
    """
    try:
        ttl = float(os.environ.get(BOARD_CACHE_TTL_ENV) or BOARD_CACHE_TTL_DEFAULT)
    except ValueError:
        ttl = BOARD_CACHE_TTL_DEFAULT
    if not math.isfinite(ttl):
        ttl = BOARD_CACHE_TTL_DEFAULT
    if ttl <= 0:
        return 0.0
    return min(ttl, float(fleet_state.STATE_FRESH_S))


def _cache_get(key: str):
    path = _cache_path(key)
    if not path:
        return None
    ttl = _cache_ttl()
    if ttl <= 0:
        return None
    try:
        if time.time() - os.path.getmtime(path) > ttl:
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _cache_put(key: str, value) -> None:
    path = _cache_path(key)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(value, fh)
        os.replace(tmp, path)
    except Exception as exc:
        # ⛔ A FAILED PUT IS NOT SILENT. Dropping the cache is the safe direction; doing it invisibly is
        # not — a non-serialisable board would quietly restore one rebuild per candidate, i.e. the
        # pre-fix cost, with nothing anywhere saying so (review P2). One line on stderr, no crash: the
        # rebuild already happened, so this run's verdict is unaffected.
        print(
            f"collision-preflight: board cache write failed ({type(exc).__name__}: {exc}) — "
            "rebuilding the board on every run of this pass",
            file=sys.stderr,
        )
        with contextlib.suppress(OSError):
            os.unlink(f"{path}.{os.getpid()}.tmp")
        return


def _git_refs(
    git_bin: str, repo: str, namespace: str, timeout: float,
) -> list[tuple[str, str]]:
    """(refname, tip-sha) for every ref in `namespace`, in ONE git call.

    The tip SHA rides along in the SAME `for-each-ref` because the terminal test
    needs it per ref: asking per-ref (`git rev-parse`) would be one subprocess
    per branch, and this repo carries ~900 local and ~1,300 remote refs.
    """
    rc, out, err, timed_out = _run(
        [git_bin, "for-each-ref", "--format=%(refname)%09%(objectname)", namespace],
        repo, timeout,
    )
    if rc != 0:
        why = "timeout" if timed_out else f"exit {rc}"
        raise SurfaceError(f"git for-each-ref {namespace} failed ({why}): {_one_line(err)}")
    refs: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, _sep, sha = line.partition("\t")
        name = name.strip()
        # Remote HEAD symrefs are not work.
        if not name or name.endswith("/HEAD"):
            continue
        refs.append((name, sha.strip()))
    return refs


def _first_parent_shas(
    git_bin: str, repo: str, timeout: float,
) -> set[str] | None:
    """Every commit on `origin/main`'s FIRST-PARENT chain, or None if unreadable.

    ⛔ THIS IS THE DISCRIMINATOR THAT MAKES PREDICATE 2 SOUND, and without it the
    ancestor arm was only ONE COMMIT DEEP. `sha != main_tip` refuses to call a
    branch terminal when its tip is exactly main's tip — but the moment main
    advances a single commit, a branch with NO COMMITS OF ITS OWN is a STRICT
    ancestor of main and predicate 2 demoted it. That is not an exotic state, it
    is the ordinary fleet flow: `git worktree add -b fix/N-slug` (branch at main's
    tip, nothing committed yet), some other PR merges, and any lane running the
    pre-flight for N before that first commit gets CLEAN — on a lane that has
    already claimed the issue by creating the branch. The remote surface cannot
    mask it, because the branch has not been pushed.

    Membership of main's OWN linear history separates the two states that
    ancestry alone cannot:

      * "just created" — the tip IS one of main's first-parent commits, because
        that is exactly what a fresh branch points at. REFUSE to demote.
      * "absorbed branch head" — the tip entered main as a SECOND parent through
        a merge, so it is NOT on the first-parent chain. Demote.

    A fast-forward landing puts the branch's own commits on the first-parent
    chain, so it is NOT demoted — the same fail-closed direction as before, and
    this repo squash-merges anyway.

    One local call, no API cost (D4 constrains REST calls, not local git). A
    failure returns None, and the caller treats that as "do not apply predicate
    2" rather than as "nothing is merged" — an unreadable witness must never
    become a downgrade.
    """
    rc, out, _err, _to = _run(
        [git_bin, "rev-list", "--first-parent", "origin/main"], repo, timeout,
    )
    if rc != 0:
        return None
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def _ancestor_merged_refs(
    git_bin: str, repo: str, namespace: str, timeout: float,
) -> set[str] | None:
    """Refs whose tip is an ANCESTOR of origin/main — i.e. already merged.

    ONE call: `--merged` performs the reachability walk once for the whole
    namespace, where `git merge-base --is-ancestor` per ref would be one
    subprocess per branch.

    Returns None (not an empty set) when the walk cannot be performed, so the
    caller can SAY so. This is deliberately NOT fatal: the test only ever
    DOWNGRADES a hit to `weak`, so failing to compute it can only leave a hit
    BLOCKING — which is the fail-closed direction. A silent empty set would
    misreport "nothing is merged" as a measured fact.
    """
    rc, out, _err, _timed_out = _run(
        [git_bin, "for-each-ref", "--format=%(refname)",
         "--merged=origin/main", namespace],
        repo, timeout,
    )
    if rc != 0:
        return None
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def _ahead_behind(
    git_bin: str, repo: str, ref: str, timeout: float,
) -> tuple[int, int] | None:
    """(ahead, behind) of `ref` relative to `origin/main`, or None if unreadable.

    #6108: the ONE datum that lets an operator tell "a lane has committed work
    here" from "a branch with this issue's number in its name exists" was
    measured NOWHERE in the output. The verdict was therefore identical for a
    branch with 7 commits of WIP and for an empty branch created three days ago
    and abandoned, and the difference was discoverable only by running
    `git rev-list --count origin/main..<branch>` by hand afterwards — measured
    on four P0 issues, two of which were empty.

    `--left-right` on `origin/main...<ref>` prints `<left>\t<right>`, where the
    LEFT count is the commits reachable from the first operand alone (main, i.e.
    BEHIND) and the RIGHT count is those reachable from the ref alone (AHEAD).
    The order is the reverse of the tuple, so it is unpacked explicitly rather
    than positionally.

    ONE local git call, and it runs ONLY for refs that already matched the issue
    number — so it is normally 0-5 calls per run. It is deliberately NOT
    `for-each-ref`-batched: batching means walking every one of this repo's ~1686
    local refs to answer a question about three of them.

    ANY failure returns None, and a caller must treat None as "not measured"
    rather than as "zero ahead": a branch that could not be counted keeps
    BLOCKING. A missing measurement must never become a downgrade — the same
    fail-closed rule the terminal predicates above rest on.
    """
    rc, out, _err, _timed_out = _run(
        [git_bin, "rev-list", "--count", "--left-right",
         f"origin/main...{ref}"],
        repo, timeout,
    )
    if rc != 0:
        return None
    parts = out.split()
    if len(parts) != 2:
        return None
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if ahead < 0 or behind < 0:  # pragma: no cover - defensive
        return None
    return ahead, behind


class TerminalState(NamedTuple):
    """Why a ref CANNOT be in flight — and WHICH KIND of leftover it is.

    `tip_is_a_merged_head` is the distinction #10174 needs, and it is the same
    distinction the two predicates already make internally:

      * predicate 1 says the ref's OWN TIP is a MERGED PR's head — a **MERGED
        LEFTOVER**, which is exactly the squash-merge shape (the PR's commits never
        entered main, so no ancestry test can see the landing);
      * predicate 2 says the tip is a commit main ABSORBED as a merge parent, which
        is a landing of a different shape and says nothing about `origin`.

    Only the first carries the #10174 evidence: a merged leftover's REMOTE branch is
    normally deleted at merge, and that pair — a merged PR naming this tip AND no
    remote branch at all — is the strongest available evidence that no lane is live.
    Carrying the flag rather than re-deriving it at the call site is deliberate: a
    caller that re-tested `sha in merged_heads` would be a SECOND copy of predicate
    1's guards, and the guards are what keep a freshly-created branch (sitting on a
    merged PR's head) blocking.
    """

    reason: str
    tip_is_a_merged_head: bool = False


def _merged_pr_label(number: int | None) -> str:
    """`#4242`, or an explicit gap — never a silent `#None`.

    The number is carried by the sampled payload (#10174), but a demotion must never
    DEPEND on it: the load-bearing datum is the head SHA, and a payload missing the
    number still proves the ref is a merged leftover.
    """
    if isinstance(number, int):
        return f"#{number}"
    return "(number not on the sampled payload)"


def _branch_terminal_state(
    ref: str, sha: str | None, merged_heads: Mapping[str, int | None],
    ancestor_merged: set[str] | None, main_tip: str | None,
    first_parent: set[str] | None,
) -> TerminalState | None:
    """Why this branch ref CANNOT be in flight, or None when it might be.

    #5186: a SQUASH-merged branch is the ordinary case in this repo, and its
    commits are not ancestors of main — so `git branch -d` refuses, and the
    ancestor test is blind to it BY CONSTRUCTION. The branch then blocks the
    issue it closed, permanently, with no dismissal path. #5129 fixed exactly
    this shape for the PR surface and did not cover local branches.

    Two independent predicates:

      1. TIP SHA == a MERGED PR's head SHA. Squash-merging discards the commits
         but the PR record keeps the original head SHA, so this is the source.
         It costs ZERO extra API calls: the closed-PR sample is already fetched
         and carries the head sha and `mergedAt`.
         ⚠ Coverage is the SAMPLED window, not all of merge history: that sample
         is bounded to `CLOSED_PR_LIMIT` (100) most recent closed PRs. A
         squash-merge older than the window is NOT detected HERE, and the branch
         keeps blocking (fail-closed — the safe direction, but the doc must not
         imply full coverage). ⛔ THE GAP IS CLOSED OUTSIDE THIS FUNCTION, for
         exactly the refs where it bites: `_branch_terminal_state_from_prs` runs
         a TARGETED head-ref lookup on the refs that would otherwise block, so a
         squash-merge older than the sample no longer blocks forever (#5485,
         measured on #6151). This function stays a pure predicate over data the
         caller already holds.
      2. The tip is an ancestor of origin/main AND is NOT one of main's own
         first-parent commits — i.e. it entered main as a MERGE parent, which is
         the shape of an absorbed branch head. One `for-each-ref --merged` walk
         per namespace, plus one `rev-list --first-parent` for the whole run.

         ⛔ PLAIN ANCESTRY IS NOT ENOUGH, and the failure is fail-open in BOTH
         directions from the exact tip. A branch created at main's tip with NO
         COMMITS OF ITS OWN YET is trivially "an ancestor of main" — the ordinary
         state of a lane between `git worktree add` and its first commit, which is
         PRECISELY when another lane may dispatch the same issue. An earlier
         revision excluded only the tip EQUAL to main's tip, which closed the
         window only until main advanced ONE commit; from then on the fresh branch
         was strictly behind and demoted. The first-parent exclusion closes the
         whole window, because a fresh branch's tip IS a main-line commit at any
         distance. The cost is that a FAST-FORWARD landing is not detected and
         keeps blocking — the fail-closed direction, and this repo squash-merges
         anyway.

         ⛔ THE SAME EXCLUSION IS ON PREDICATE 1, and it has to be. Predicate 1
         matches the tip against the head SHAs of merged PRs, and a fresh branch
         sitting at main's tip has main's SHA — so if main's tip is itself a
         merged PR's head (a fast-forward or rebase landing, an empty-diff PR),
         predicate 1 matched a branch with NO COMMITS OF ITS OWN and demoted it.
         Restricting the exclusion to the ancestor arm left this primary arm
         fail-open, which is the same defect the arm was added to close. Any
         predicate that DOWNGRADES refuses the moment the tip is
         INDISTINGUISHABLE FROM "JUST CREATED" — i.e. when it equals main's tip.

         ⛔ The two predicates treat an UNRESOLVED tip DIFFERENTLY, and that
         difference is deliberate rather than an oversight:

           * Predicate 1 (tip-SHA == a merged PR's head) is the EXACT one —
             decision D4 rests on it precisely because a merged PR record names
             the branch head as GitHub computed it (correction #2: "the exact
             tip-SHA test is the predicate"). An unresolvable tip does not make
             that record any less exact, so it still demotes.
           * Predicate 2 (tip is an ancestor of main) is the APPROXIMATE one and
             is fed by a `--merged` walk over the same ref, so an unresolvable
             tip means the walk failed too and it does not demote at all.

         The residual that this leaves, stated rather than hidden: in a repo
         whose `origin/main` cannot be resolved, a branch that is FRESH at
         exactly a merged PR's head is read as landed. It needs the local
         `origin/main` to be absent or unreadable AND the branch to be sitting
         on a merged head. NO MECHANISM OWNS THIS RESIDUAL TODAY — the registry
         this comment used to defer it to does not exist — so this function
         DECLINES it rather than guessing at it (#7051).

    ⛔ `main_tip` HAS NO DEFAULT, AND `None` MEANS "DO NOT APPLY PREDICATE 2".
    (`main_tip` and `first_parent` are resolved by separate git calls, so
    `first_parent` can be readable while `main_tip` is not — a stub that fails
    only `rev-parse` reproduces exactly that — and this guard is what keeps the
    arm off in that case.)
    Both halves are load-bearing:

      * No default, because a defaulted `main_tip=None` made strictness OPT-IN:
        any caller that omitted it silently got plain (non-strict) ancestry, so
        the fail-open this arm exists to close was one forgotten argument away.
      * `None` disables the arm rather than loosening it, because this predicate
        can only ever DOWNGRADE a hit. An unknown tip makes "just created" and
        "landed behind main" indistinguishable, and the safe answer to "I cannot
        tell" is to leave the ref BLOCKING. A tip that cannot be resolved (no
        `origin/main`, no local checkout) therefore costs a false COLLISION at
        worst, never a false CLEAN.

    Predicate 1 is the EXACT one (a merged PR record names the branch's head);
    predicate 2 is the secondary, approximate one. Its remaining imprecision is
    stated rather than hidden: a branch whose tip entered main as a merge parent
    while its own work did NOT land is still read as terminal, and a fast-forward
    landing and rebase residue are both missed. No mechanism owns that residue
    today — the registry these comments used to defer it to does not exist — so
    this function declines it rather than guessing (#7051).

    BOTH apply to LOCAL branches only. A remote-tracking ref is a local CACHE of
    the last fetch, not the remote's state: a branch that was squash-merged and
    then REUSED for new work still reads as its old, merged sha until someone
    fetches. Judging that terminal would be a false ACCEPT — a live branch read
    as free — so remote-tracking refs are never demoted by these predicates.

    ⛔ `patch-id` is deliberately NOT a third predicate (D1; agent-infra #1362
    ledger 09-23). `--stable` and the default `--unstable` both IGNORE
    WHITESPACE, so a patch-id match can call a branch merged when its content
    differs — a FALSE ACCEPT, which is this test's dangerous direction, because
    a live branch read as terminal is a live lane read as free.
    """
    if not ref.startswith("refs/heads/"):
        # ⛔ REMOTE-TRACKING REFS ARE A CACHE, so a terminal verdict on one can be
        # a false ACCEPT: `refs/remotes/origin/<branch>` keeps pointing at a
        # merged PR's head sha after the branch has been REUSED for new work,
        # because nothing in this tool fetches. The local `refs/heads/` value is
        # what the lane actually has and is authoritative for "is there unlanded
        # work here". Refusing to demote a remote ref leaves the hit BLOCKING,
        # which is the fail-closed direction — and the local branch, if it still
        # exists, is judged on its own merits by this same call.
        return None
    if (
        sha
        and sha in merged_heads
        # ⛔ `sha != main_tip` IS NOT "the only guard left" when `first_parent`
        # is unavailable — an earlier version of this comment said so, and the
        # `--merged` fallback added below guards that same state. In every state a
        # test can construct the fallback SUPERSEDES this comparison (a mutation
        # deleting this line breaks no test).
        #
        # IT IS KEPT ANYWAY, and the reason is specific rather than caution in
        # general: `main_tip` and `ancestor_merged` come from SEPARATE git calls
        # (`rev-parse origin/main`, then `for-each-ref --merged=origin/main`). If
        # they disagree — a force-push landing between them — a ref can point at
        # `main_tip` and still be absent from the `--merged` set, and in exactly
        # that state this comparison is the only thing refusing the demotion.
        # Narrow, unreachable by any stub, and fail-CLOSED, which is why it stays
        # rather than being deleted on the strength of a green suite.
        and sha != main_tip
        # ⛔ THE EXCLUSION BELONGS ON *BOTH* PREDICATES, AND `sha != main_tip`
        # ALONE IS NOT ENOUGH ON EITHER — it is ONE COMMIT DEEP. A branch created
        # at `origin/main`'s tip with no commits of its own has exactly main's
        # SHA, and whenever that SHA is also a merged PR's `head.sha` (a
        # fast-forward / rebase landing, or an empty-diff PR) this predicate
        # returned "squash-merged" and demoted a lane that had merely CLAIMED the
        # issue by creating the branch. Comparing against the CURRENT tip only
        # closed that while main had not moved; as soon as main advanced, the
        # fresh branch was strictly behind the tip, `sha != main_tip` was
        # satisfied, and the same fail-open returned through the PRIMARY arm —
        # which is the defect cycle 5 closed on predicate 2 and left here. The
        # discriminator is the same one: a tip on main's own first-parent chain is
        # a commit main already contains (exactly what a fresh branch points at),
        # NOT an absorbed branch head, which entered main as a MERGE parent. When
        # `first_parent` is unreadable the exact-tip comparison still applies, and
        # the merged-PR record is treated as the exact evidence D4 says it is.
        and not (first_parent is not None and sha in first_parent)
        # ⛔ THE FIRST-PARENT WITNESS CAN GO MISSING WHILE MAIN'S TIP IS KNOWN,
        # and then `sha != main_tip` is the ONLY guard left — which is one commit
        # deep, i.e. exactly the window cycle 6 closed. A branch created at
        # main's tip and left behind by ONE commit is "indistinguishable from
        # just created" in the same sense, and it was demoted to CLEAN whenever
        # `rev-list --first-parent` failed while `rev-parse` and the `--merged`
        # walk both succeeded.
        #
        # THE FALLBACK DISCRIMINATOR IS THE `--merged` WALK, which answers the
        # same question from a different git command: a ref that IS merged into
        # main has a tip main already CONTAINS — which is what a fresh branch
        # points at — so it must not be called landed; a squash-merge residue is
        # NOT merged, because squash discards the commits, so it still demotes.
        # With neither witness available the downgrade is refused, and with main's
        # tip unresolved this clause is inert (`test_squash_merged_branch_is_
        # terminal_and_cannot_block` runs in a repo with no `origin/main`).
        and not (
            main_tip is not None
            and first_parent is None
            and (ancestor_merged is None or ref in ancestor_merged)
        )
    ):
        # ⛔ THE NOTE NAMES THE PULL REQUEST (#10174). "its tip SHA is a merged PR's
        # head" is enough to DEMOTE and not enough to ACT: a reader of the gate's
        # output needs the number to verify the landing and to clean the ref up, and
        # the harvest already carries it (`number` is on the same sampled payload as
        # the `headSha`). The vocabulary is deliberate too — "MERGED LEFTOVER" says
        # what the ref IS, where the old wording only said what it is not.
        return TerminalState(
            f"squash-merged — its tip {sha[:9]} is the head of the MERGED PR "
            f"{_merged_pr_label(merged_heads.get(sha))}, so its content already "
            "landed even though its commits are not ancestors of main — a MERGED "
            "LEFTOVER, not a live holder",
            tip_is_a_merged_head=True,
        )
    if (
        # Every condition guards a DOWNGRADE, so an unreadable witness leaves the
        # ref BLOCKING.
        #
        # `main_tip is not None` is an INDEPENDENT WITNESS, not a leftover of the
        # exact-tip comparison it used to guard: `main_tip` and `first_parent`
        # come from separate git calls, so `first_parent` can be readable while
        # `main_tip` is not (a stub failing only `rev-parse` reproduces exactly
        # that), and requiring BOTH is the fail-closed posture for this
        # approximate arm.
        main_tip is not None
        and first_parent is not None
        and ancestor_merged is not None
        and ref in ancestor_merged
        # ⛔ `sha not in first_parent` SUPERSEDES the `sha != main_tip` guard that
        # used to sit here, and that guard was DELETED rather than kept alongside
        # it: `rev-list --first-parent origin/main` always contains main's own
        # tip, so this condition already implies it (a mutation test proved it —
        # deleting `sha != main_tip` changed no behaviour and broke no test). Two
        # conditions that LOOK like independent protection but are not is worse
        # than one, because the next reader cannot tell which is load-bearing.
        # It is NOT redundant over in predicate 1, which must also work when
        # `first_parent` is unreadable.
        and sha not in first_parent
    ):
        return TerminalState(
            "merged into origin/main (its tip is a branch head main absorbed)"
        )
    return None


def _branch_terminal_state_from_prs(
    gh_bin: str, slug: str | None, cwd: str, timeout: float,
    branch: str, sha: str | None, main_tip: str | None,
    first_parent: set[str] | None = None,
    ancestor_merged: set[str] | None = None,
) -> str | None:
    """The TARGETED terminal test for ONE local branch — the sampled window's fix.

    #5485: every terminal test in this tool reads data ALREADY IN HAND, so a
    squash-merge older than `CLOSED_PR_LIMIT` (100) is invisible and the branch
    then blocks the issue it closed — permanently, with no dismissal path.
    Measured 2026-10-06 on #6151: `refs/heads/fix/6134-6146-6151-graph-correctness`
    (tip adcfedc42, 1 commit ahead of main, 0 worktrees, no open PR) blocked the
    one item the priority stack labels UNHELD, because its PR #6156 merged on
    2026-09-28 — far outside the sample.

    This asks GitHub about THIS ref's PRs, over REST (`gh api`), for the same
    reason the closed-PR surface is REST: the `gh pr list` GraphQL path resets on
    this host (#3587). It demotes on EITHER of TWO arms — the exact one that
    decision D4 rests on (a MERGED PR whose `head.sha` EQUALS this branch's tip),
    and CONTAINMENT (the tip is an ANCESTOR of a MERGED PR's head, which is what
    closes #5485's tip-divergence half):

      * a branch name REUSED after its merge has moved past that sha and keeps
        blocking (the merge is not evidence about the NEW commits),
      * a CLOSED-unmerged PR is not a landing and keeps blocking,
      * ALL THREE of predicate 1's exclusions refuse: a tip equal to main's tip,
        a tip on main's FIRST-PARENT chain, and — when that witness is
        unreadable — a tip in the `--merged` walk's set. `sha == main_tip` alone
        is ONE COMMIT DEEP: a lane that has just created a worktree sits ON
        main's tip, and if that tip is itself a merged PR's head (a fast-forward
        or empty-diff landing) an unguarded test would call a branch with NO
        COMMITS OF ITS OWN terminal — a live lane read as free, the dangerous
        direction,
      * a FULL page (>= `_TARGETED_PR_PAGE` PRs) refuses, and that refusal is
        deliberately CONSERVATIVE: with `sort=created desc` the OLDEST PR is the
        one that falls off, the merges this function exists to find are the old
        ones, so a full page cannot be read as "no merged PR" — even though the
        answer may in fact be on the page.

    Returns a human reason when the ref is terminal. For a ref that KEEPS
    BLOCKING there are TWO outcomes, and telling them apart is load-bearing:
      * `None` — the lookup RAN and found no merged PR for this ref, or one of
        the guards above excluded it, or there is no slug/tip to ask about. The
        caller's default is BLOCKING, so this is never a demotion by itself.
      * `SurfaceError` — the lookup COULD NOT RUN (gh error, non-list payload, a
        full page). It PROPAGATES so the caller can COUNT it and name the
        inability in the surface note; the ref blocks either way, so "I could not
        ask" is never presented as "there is no such PR". ⛔ An earlier cut of
        this PR swallowed these into `None`, which made the caller's counting
        DEAD CODE and hid an unanswerable lookup behind a confident-looking block.
    """
    if not slug or not sha:
        return None
    # ⛔ ONE REFNAME, BOTH USES. The production caller passes the FULL refname
    # (`refs/heads/...`) because `ancestor_merged` holds `%(refname)` values — but
    # the parameter is named `branch` and the REST `head=` filter takes the SHORT
    # name. Normalising HERE rather than at the call site is what keeps those two
    # from drifting: a caller (or a later refactor) passing the natural short name
    # would otherwise compare a short name against a set of refnames, never match,
    # and silently re-open the `--merged` fail-open this function exists to close —
    # with every test still green. Both spellings resolve in the API (verified
    # live 2026-10-06: `owner:refs/heads/x` and `owner:x` returned the same PR).
    ref = branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"
    branch = ref.removeprefix("refs/heads/")
    # ⛔ THE SAME THREE GUARDS PREDICATE 1 USES, AND THEY ARE REQUIRED HERE FOR A
    # SHARPER REASON: this function is called PRECISELY when
    # `_branch_terminal_state` has ALREADY refused, so a guard omitted here is a
    # guard BYPASSED BY CONSTRUCTION. `sha != main_tip` alone is ONE COMMIT DEEP
    # (predicate 1's own words): a lane that has merely CLAIMED the issue by
    # creating `fix/<N>-…` sits on a main-line commit, and that commit can be a
    # merged PR's head (fast-forward, rebase or empty-diff landing) — so once main
    # advances by one, the weak test passes and a branch with NO COMMITS OF ITS
    # OWN is read terminal: a live lane read as free, the dangerous direction.
    # Predicate 1 closes that with the first-parent witness and the `--merged`
    # fallback; mirrored here clause for clause (#5595 cycles 5 and 6).
    if sha == main_tip:
        return None
    if first_parent is not None and sha in first_parent:
        return None
    if (
        main_tip is not None
        and first_parent is None
        and (ancestor_merged is None or ref in ancestor_merged)
    ):
        return None
    owner = slug.split("/")[0]
    # ⛔ THE REF IS PERCENT-ENCODED INTO THE QUERY. `git check-ref-format` permits
    # `&`, `#`, `+` and `%` in a ref, and a ref name in a shared clone is
    # writer-influenced: interpolated raw, a branch named `fix/N&head=owner:other`
    # adds a SECOND `head=` parameter, the API honours the LAST one, and the
    # lookup is then answered by a DIFFERENT branch's PRs — which breaks the
    # scoping this arm's whole soundness argument rests on. `/` and `:` are left
    # intact (the form verified to resolve against the API).
    query = urlencode(
        {
            "state": "all",
            "head": f"{owner}:{branch}",
            "per_page": _TARGETED_PR_PAGE,
            "sort": "created",
            "direction": "desc",
        },
        quote_via=quote,
        safe="/:",
    )
    args = [
        "api",
        f"repos/{slug}/pulls?{query}",
        "--jq",
        'map({number, state, headSha: (.head.sha // ""), '
        'mergedAt: (.merged_at // "")})',
    ]
    # ⛔ NO INNER SWALLOW, so the CALLER can count the inability and report it in
    # the surface note. This used to `return None` on failure, which made the
    # caller's `except SurfaceError` unreachable and the promised "⚠ … could not
    # run" note DEAD CODE — leaving an unanswerable lookup indistinguishable from
    # "no merged PR exists". The ref still keeps blocking either way.
    prs = _gh_json(gh_bin, args, cwd, timeout)
    if not isinstance(prs, list):
        raise SurfaceError(
            f"head-ref lookup for {branch} returned {type(prs).__name__}, not a list"
        )
    # ⛔ A FULL PAGE IS REFUSED, NOT READ AS "NOT FOUND". `sort=created desc`
    # puts the OLDEST PR last, so the merges this function exists to find are the
    # ones that fall off a full page — a silent "not found" would keep the issue
    # blocked forever (#5485) while looking like a completed lookup. The refusal
    # is deliberately CONSERVATIVE: it fires on any full page, so it can refuse an
    # answer that was in fact on the page — the safe direction, never the other.
    if len(prs) >= _TARGETED_PR_PAGE:
        raise SurfaceError(
            f"head-ref lookup for {branch} hit its {_TARGETED_PR_PAGE}-PR page cap; "
            "an older merged PR may be off-page"
        )
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        # MERGED only: `_pr_terminal_state` also names a closed-unmerged PR
        # "closed", and an abandoned PR is not a landing.
        if _pr_terminal_state(pr) != "merged":
            continue
        head_sha = str(pr.get("headSha") or "")
        if head_sha == sha:
            return (
                f"its tip {sha[:9]} is the head of the MERGED PR #{pr.get('number')} "
                f"(targeted head-ref lookup, outside the {CLOSED_PR_LIMIT}-PR sample) "
                "— a MERGED LEFTOVER, not a live holder"
            )
        # ⛔ THE TIP-DIVERGENCE ARM — #5485's unsolved half, and the reason the
        # exact test above does NOT close this issue on its own. A MERGED PR's
        # head is frequently NOT the branch tip: it picks up a merge of main or
        # an amend before landing. Measured on #6156: its head is a merge commit
        # (a749c662d) and the LOCAL tip (adcfedc42) is its ANCESTOR — so equality
        # refuses a branch whose work demonstrably landed, which is exactly the
        # "blocks forever" half.
        #
        # The sound closure is CONTAINMENT, not equality: if the branch's tip is
        # an ancestor of the merged PR's head, every commit the branch owns is
        # inside the PR that landed. ⛔ NOT a patch-id (D1 forbids it for exactly
        # this test: `--stable` and `--unstable` both ignore whitespace, so a
        # match can call a branch merged when its content differs — a
        # FALSE-ACCEPT). A branch REUSED after its merge has moved PAST that
        # head, so the tip is no longer an ancestor of it — `compare` reports
        # **`behind`**, the opposite of the `ahead` this arm accepts — and it
        # keeps blocking: the safe direction is preserved by the predicate's own
        # shape.
        if not head_sha:
            continue
        if not _tip_is_ancestor_of(gh_bin, slug, cwd, timeout, sha, head_sha):
            continue
        return (
            f"its tip {sha[:9]} is contained in the MERGED PR #{pr.get('number')} "
            f"(head {head_sha[:9]}; targeted lookup + compare, outside the "
            f"{CLOSED_PR_LIMIT}-PR sample) — a MERGED LEFTOVER, not a live holder"
        )
    return None


class RemoteBranch(NamedTuple):
    """What the branch API said about a demoted ref's REMOTE counterpart (#10174).

    `sha` is the remote tip the same response carried, or "" when it carried none. It
    exists so the note can compare it with the demoted LOCAL tip: `present` at the same
    tip is the leftover and can be deleted; `present` at another tip means the NAME was
    reused and a lane may be live under it — advice to delete there would destroy the
    only holder of unlanded work (review finding on #10205).
    """

    kind: str
    why: str
    sha: str = ""


def _remote_branch_state(gh_bin: str, slug: str | None, cwd: str, timeout: float,
                        ref: str) -> RemoteBranch:
    """("gone" | "present" | "unreadable", a one-line reason) for a LOCAL branch.

    #10174: the question is *does the remote still hold this branch?* — and only
    GitHub can answer it authoritatively. The local `refs/remotes/<remote>/<branch>`
    would be free (the run already enumerates that namespace) and is a FETCH CACHE:
    its absence means "this checkout has not fetched it", not "it is deleted", and
    its presence may be a stale duplicate of a ref deleted weeks ago. The note this
    feeds exists to say whether the strongest evidence is available, so it may not
    be built from the weakest one.

    ⛔ IT IS A NOTE-ONLY READ, AND THE POLARITY IS DIFFERENT FROM EVERY OTHER READ
    IN THIS MODULE. Failure does NOT keep the ref blocking, because the demotion does
    not rest on this datum: the load-bearing fact is that the ref's TIP IS A MERGED
    PR'S HEAD, which is GitHub's own record of the landing. So the three outcomes
    are all safe — a failed read can neither demote nor un-demote anything, and it
    must never be narrated as `gone` ("the branch is deleted" and "we could not look"
    are different sentences with different remedies, the module's oldest defect).

    Bounded like every other optional surface: ONE request, paid only for a ref that
    the merge-history test has ALREADY demoted (zero-or-one per DEMOTED REF — several
    demoted refs in one run cost several reads; never one per local branch). The branch
    name is percent-encoded into the path for the same reason
    `_branch_terminal_state_from_prs` encodes it into the query — a ref name is
    writer-influenced and `/` is the only character left intact.

    ⛔ IT ALSO RETURNS THE REMOTE TIP, and that is NOT a convenience (review finding on
    #10205). A branch NAME is reused: a lane can take a name whose old incarnation was
    squash-merged and push NEW work to it from another machine or a cell, and this clone
    never fetches, so `origin/<branch>` can be AHEAD of the demoted local ref holding
    commits that landed nowhere. `name` alone cannot tell that apart from the leftover,
    and the first cut of this clause used it to advise "delete the remote one too" —
    advice that would have deleted the only holder of a live lane's unlanded work. The
    tip is on the SAME response, so the comparison is free and the advice can be made
    SAFE BY CONSTRUCTION instead of by the reader's caution. `sha` is "" when the payload
    carried no tip; a caller must then treat the comparison as UNVERIFIED, never as a
    match.
    """
    if not slug:
        return RemoteBranch("unreadable", "no owner/name for the target repo")
    branch = _short_branch(ref) if ref and ref != "(detached)" else ""
    if not branch:
        return RemoteBranch("unreadable", f"{ref!r} names no branch")
    path = f"repos/{slug}/branches/{quote(branch, safe='/')}"
    try:
        payload = _gh_json(
            gh_bin,
            ["api", path, "--jq", "{name: .name, sha: (.commit.sha // \"\")}"],
            cwd, timeout,
        )
    except SurfaceError as exc:
        blob = str(exc).lower()
        # `gh` renders a non-2xx as `HTTP <code>: <status> (<url>)`, so the 404 is
        # matched on the CODE as well as on the prose — a bare "not found" test
        # would miss the documented form on a response with no body prose.
        if "http 404" in blob or "not found" in blob:
            return RemoteBranch("gone", f"the branch API returned 404 for `{branch}`")
        return RemoteBranch("unreadable", _one_line(str(exc)))
    if not isinstance(payload, dict):
        return RemoteBranch("unreadable", (
            f"the branch API answered {type(payload).__name__}, not an object"
        ))
    name = payload.get("name")
    if not (isinstance(name, str) and name):
        return RemoteBranch("unreadable", (
            f"the branch API answered without a name ({_one_line(repr(payload))})"
        ))
    remote_sha = payload.get("sha")
    return RemoteBranch(
        "present",
        f"the branch API still returns `{name}`",
        remote_sha if isinstance(remote_sha, str) else "",
    )


def _remote_counterpart_clause(state: RemoteBranch | None, ref: str,
                              tip: str | None) -> str:
    """The note fragment for `_remote_branch_state`'s answer, or "" when unasked.

    ⛔ EVERY ANSWER SAYS A DIFFERENT THING, and the differences are the point:

      * `gone` — corroboration a lane is dead: nothing on the remote can still be held
        under a deleted name, so the local ref is safe to delete;
      * `present` at the MERGED tip — the remote still holds exactly the artifact this
        demotion proved landed, so it is a second cleanup target (a local delete alone
        leaves `origin` holding it);
      * `present` at ANOTHER tip — the name was REUSED and the remote holds work this
        local ref never had. Then a lane may be live under that name: say so, and NEVER
        advise a delete (review finding on #10205 — see `_remote_branch_state`);
      * `present` with no comparable tip, or `unreadable` — UNKNOWN, stated as such, and
        stated not to be load-bearing, so neither can be mistaken for the other.
    """
    if state is None:
        return ""
    kind, why = state.kind, state.why
    branch = _short_branch(ref)
    if kind == "gone":
        return (
            f"; its remote counterpart `origin/{branch}` is GONE ({why}) — a merged "
            "leftover with no remote ref is the strongest evidence no lane is live: "
            "nothing on the remote can still be held under a deleted name"
        )
    if kind == "present":
        if state.sha and tip and state.sha == tip:
            return (
                f"; its remote counterpart `origin/{branch}` STILL EXISTS {why} at the "
                f"SAME tip {tip[:9]} this demotion proves landed — that remote ref is "
                "the leftover too, so delete both (a local delete alone re-dresses the "
                "name on the next fetch)"
            )
        if state.sha and tip:
            return (
                f"; its remote counterpart `origin/{branch}` STILL EXISTS {why} but at "
                f"ANOTHER tip ({state.sha[:9]}, not this ref's {tip[:9]}) — the NAME WAS "
                "REUSED and the remote holds work this local ref never had, so a lane "
                "may be LIVE under it: do NOT delete the remote ref, and do not read "
                "this row as free. Only the local leftover is proven"
            )
        return (
            f"; its remote counterpart `origin/{branch}` STILL EXISTS {why}, but its "
            "tip could not be compared with this ref — treat it as UNVERIFIED: a "
            "reused name can hold live work, so do NOT delete the remote ref"
        )
    return (
        f"; whether the remote still holds `origin/{branch}` could not be read ({why}) "
        "— UNKNOWN, and the demotion does not rest on it: tip equality is the "
        "load-bearing test"
    )


def _ask_remote_state(remote_state: object, ref: str) -> RemoteBranch | None:
    """Call the injected remote-state reader, folding a failed READ into `unreadable`.

    The reader is injected (like `counts` / `tip_age` / `stale`) so the gh binary,
    slug and timeout stay in `run_preflight`, and so the cost is paid ONLY for a ref
    that reached a MERGED-LEFTOVER demotion.

    ⛔ A RAISED `SurfaceError` MUST NOT ESCAPE THIS CALL. `scan_branch_surface` is
    called inside the branch surface's `try`, whose handler marks the whole surface
    INCOMPLETE (`git-unavailable`) — so an unhandled note-read failure would turn a
    CLEAN run into exit 2 over a datum that cannot change the verdict.
    """
    if remote_state is None:
        return None
    try:
        return remote_state(ref)  # type: ignore[operator]
    except SurfaceError as exc:
        return RemoteBranch("unreadable", _one_line(str(exc)))
    except Exception as exc:  # a NOTE read can never break the scan
        return RemoteBranch("unreadable", _one_line(f"{type(exc).__name__}: {exc}"))


def _tip_is_ancestor_of(
    gh_bin: str, slug: str, cwd: str, timeout: float, tip: str, head_sha: str,
) -> bool:
    """True when `tip` is an ancestor of `head_sha`, per GitHub's compare API.

    ⛔ THE STATUS POLARITY IS THE TRAP, and it was read backwards on the first
    write-up: for `compare/{base}...{head}`, `status` describes the HEAD relative
    to the BASE, so when the base (`tip`) is an ancestor of the head (`head_sha`)
    GitHub returns **`ahead`** (head is ahead of base), NOT `behind`. Verified on
    the #6156 case: `compare/adcfedc42...a749c662d` -> `{ahead: 4, behind: 0,
    status: "ahead"}`. Reading it the other way made every containment fail and
    the arm silently inert — a demotion predicate that never fires is the same
    class of defect as one that fires wrongly, and it looks like a pass.

    Asked of GITHUB rather than computed locally because a merged PR's head
    object is usually absent from the local clone (after a squash merge it is
    not an ancestor of main), and fetching it would be a second transport for a
    fact one REST call answers exactly. ⛔ A FAILURE PROPAGATES (`SurfaceError`)
    rather than returning False: the caller counts it and names the inability in
    the surface note, because "I could not ask" must not be presented as "there
    is no such PR". The ref keeps blocking either way.
    """
    args = [
        "api",
        f"repos/{slug}/compare/{tip}...{head_sha}",
        "--jq",
        "{status: .status}",
    ]
    payload = _gh_json(gh_bin, args, cwd, timeout)
    if not isinstance(payload, dict):
        raise SurfaceError(
            f"compare/{tip[:9]}...{head_sha[:9]} returned "
            f"{type(payload).__name__}, not an object"
        )
    return str(payload.get("status") or "").strip().lower() in ("ahead", "identical")


# ── STALE HOLDERS: a branch that was never asked whether it is still held ────
#
# THE MEASURED DEFECT (tortoise#9806, measured 2026-10-24). On the owner's Mac, with a
# reloaded controller running `SCOPE=auto`, TWENTY-FOUR candidates in a row answered
# `collision — a peer owns it, retried next pass` and the backlog stayed at 0. The holders
# `who_is_on` named were not claims at all: they were BRANCHES whose names carry the issue
# number, one commit ahead of a main that had moved on by 3732, whose last commit was 54.8
# days old, with no live process, no uncommitted work and no claim ref behind them. A
# branch whose number matches was read as a live claim the moment it was created, and
# NOTHING ever asked again — the gate revalidated a past state forever.
#
# WHAT THIS IS NOT. It is NOT the demotion #6108 proposed and refused. #6108 wanted
# `ahead == 0` to stop blocking; that was refused because a fresh branch between
# `git worktree add` and its first commit is EXACTLY 0 ahead. This rule never looks at
# `ahead`: it asks whether the HOLDER EVIDENCE ITSELF has gone stale, in four independent
# dimensions at once. A brand-new empty branch fails the `behind` test by construction (it
# sits at main's tip), so the window #6108 protects is untouched by THIS rule.
#
# ⚠️ AND #6108 HAS SINCE BEEN REOPENED AND GRANTED, NARROWLY (2026-10-24). `judge_empty`
# below demotes a LOCAL branch that is 0 commits ahead **and** has no live worktree owner,
# no uncommitted work and no live claim ref — the measured block was a P0 (#9567) and an
# open P1 (#9097) frozen forever by an empty ref. The `worktree add` window the refusal
# was about stays BLOCKING under that leg **while the lane's process is alive**: a live
# `pi` standing in the checkout IS an owner. ⚠️ And for PREPARE-THEN-DISPATCH — a
# controller that creates the checkout before the lane's process starts — the protection
# is the CLAIM REF (`refs/claims/issue-<N>`), never the checkout's existence: a clean,
# registered, unclaimed checkout with no process in it is indistinguishable from an
# abandoned one by every signal this gate can read, and the `OVERRIDES:` decision below
# demotes it. `judge_empty`'s STATED RESIDUALS say so; read them before relying on a
# worktree to hold an issue.
#
#     OVERRIDES: #6108's recorded trade-off — "the count is REPORTED, not acted on …
#     every 0-ahead branch must keep blocking" — a 0-ahead branch that is BOTH far behind
#     main AND silent for a week AND unowned AND unclaimed is now ADVISORY. #6108's
#     reasoning ("the over-block is the accepted price of never reading a lane that has
#     CLAIMED an issue as free") is preserved in the dimension it is actually about: the
#     CLAIM. A lane that has claimed the work still blocks, through all four tests.
#
# THE FOUR TESTS, all required (any one failing leaves the ref BLOCKING — fail-closed):
#
#   1. DIVERGENCE — the tip is at least `STALE_BEHIND_MIN` commits behind
#      `origin/main`. MEASURED, not guessed (this repo, 2026-10-24: 1767 local+remote
#      refs, 1507 of them naming a number): behind p10 = 141, p25 = 386, median = 2113.
#      The four blocked issues sit at 457, 777, 777, 783, 3732.
#   2. SILENCE — no commit on the tip newer than `STALE_TIP_DAYS` days. MEASURED against
#      the population that is demonstrably LIVE: every branch checked out in a worktree on
#      this box (94 worktrees) has a tip at most 5.7 days old and is at most 356 behind.
#      The four blocked issues: 8.6, 12.2, 12.4 and 54.8 days. `STALE_TIP_DAYS = 7.0` is
#      the only integer day that sits above the live population's maximum and below the
#      smallest blocked case; it is deliberately NOT higher, because #2446's tip is 8.6
#      days old and a threshold of 10 would leave it blocking.
#   3. NO LIVE WORKTREE OWNER — no live `pi` process whose cwd is inside a worktree
#      checked out on this ref, AND no uncommitted work in any such worktree.
#      ⛔ THE PROCESS-CWD CHECK IS THE DISCRIMINATOR, NOT AGE: a worktree is not stale
#      because it is old (a long test run is a live owner and its `pi` parent holds the
#      cwd). Age enters NEITHER this test nor the worktree surface — it appears only in
#      test 2, on the BRANCH, which is what the abandoned branch actually leaves behind.
#   4. NO LIVE CLAIM REF — `refs/claims/issue-<N>` on the target's origin: absent, or
#      present with an EXPIRED lease. This is the one test that costs network I/O, so it
#      runs LAST and only for a ref that already passed 1-3. Its polarity is
#      `claims.sh:_lease_is_live`'s, copied deliberately: an absent / unparseable lease
#      is LIVE, an unreadable registry is LIVE, and an ORPHANED claim is LIVE here (the
#      reaper owns orphan release; treating it as free would be a second, unreviewed
#      copy of that policy). Every one of those choices can only keep a ref BLOCKING.
#
# A ref that passes all four is ADVISORY — reported in full, with the basis, at `weak`
# strength, which cannot contribute to the verdict. It is NEVER silently dropped: a
# reader must be able to see the stale holder, because the cleanup (deleting the branch)
# is a separate, evidence-based action and this gate does not perform it.
#
# ⛔ NOT `tools/branch_reaper.py`'S QUESTION. That tool asks "may this branch be DELETED?"
# from GitHub PR state and answers with a delete; this asks "is this ref still EVIDENCE of
# in-flight work?" from owner evidence and answers with an advisory row. Merging the two
# would let a dispatch gate delete a branch — never.
STALE_BEHIND_MIN = 300
STALE_TIP_DAYS = 7.0
# The claim namespace `claims.sh` writes issue claims into (`claim_issue_ref`). Mirrored,
# not imported: agent-infra is a separate checkout this tool may not have.
CLAIM_NS = "refs/claims"
CLAIM_TMP_NS = "refs/claims-tmp"


def _ref_tip_age_days(git_bin: str, repo: str, ref: str, timeout: float,
                      now: float | None = None) -> float | None:
    """Age in days of `ref`'s tip COMMIT, or None when it cannot be read.

    The COMMITTER date, from the commit object itself — never a file mtime and never the
    ref's own reflog: an mtime is when this checkout last touched the ref, which on a
    fetch cache is "just now" for a commit that is a month old.

    None means NOT MEASURED, and a caller must keep the ref BLOCKING on it. `--format=%ct`
    always prints, so an empty or unparseable answer is a read failure, not an age of 0.
    """
    rc, out, _err, _timed_out = _run(
        [git_bin, "log", "-1", "--format=%ct", ref], repo, timeout,
    )
    if rc != 0:
        return None
    try:
        committed = int(out.strip())
    except ValueError:
        return None
    age = ((time.time() if now is None else now) - committed) / 86400.0
    return max(0.0, age)


def _local_worktrees_for(ref: str, sha: str | None,
                        blocks: list[dict] | None) -> list[dict]:
    """The worktree records a `ref` could be held through.

    TWO keys, because a worktree records one and the branch may be spelled another:

      * `branch` — the same bare name (`refs/heads/x` for a local ref, and the LOCAL twin
        `refs/heads/x` for `refs/remotes/<remote>/x`). A lane checks out the local branch;
        `who_is_on` reports whichever ref carries the number, so matching on the bare name
        is what keeps one abandoned claim from being judged twice with two answers;
      * `head` — the tip SHA. A DETACHED worktree records `(detached)` and no branch, and
        a lane testing a pushed head is exactly that shape.

    ⛔ `(detached)` IS NEVER MATCHED BY NAME. Treating it as a name would make every
    detached worktree "the same holder" as every ref under judgement, so uncommitted work
    in one unrelated checkout would keep an abandoned branch blocking forever — the
    over-block this rule exists to remove, reintroduced through the back door.

    `None` for `blocks` means the listing could not be read, and the caller must treat that
    as "cannot prove", never as "no worktrees".
    """
    if blocks is None:
        return []
    bare = _short_branch(ref) if ref and ref != "(detached)" else ""
    out = []
    for block in blocks:
        branch = block.get("branch") or ""
        if bare and branch and branch != "(detached)" and _short_branch(branch) == bare:
            out.append(block)
            continue
        if sha and block.get("head") == sha:
            out.append(block)
    return out


class StaleHolder:
    """Is a number-named ref still EVIDENCE of in-flight work? (see the block above)

    One instance per pre-flight run: the process census, the worktree-dirt reads and the
    claim read are all cached here, so the four tests are paid at most once each no matter
    how many refs name the issue.

    ⛔ EVERY FAILURE TO MEASURE LEAVES THE REF BLOCKING. There is no arm of `judge` that
    turns "I could not read it" into an advisory row: the unmeasured cases are the ones
    that used to be false CLEANs in this module's history, and this gate's whole polarity
    is that an unread surface is not a free one.
    """

    def __init__(self, git_bin: str, repo: str, issue: int, timeout: float,
                 worktrees: list[dict] | None, now: float | None = None) -> None:
        self.git_bin = git_bin
        self.repo = repo
        self.issue = issue
        self.timeout = timeout
        self.worktrees = worktrees
        self.now = time.time() if now is None else now
        self._census: tuple[list[str], str] | None = None
        #: Live `pi` pids whose cwd the census could NOT resolve. `fleet_state.process_cwds`
        #: deliberately SKIPS a pid whose cwd is unreadable (hidepid, a removed directory, an
        #: `lsof` placeholder) and reports no error, because an exited pid is not a gap. For
        #: the STALE leg that is harmless (it asks "is a lane standing HERE" and a per-pid
        #: judgement follows); for the EMPTY leg it is the fail-open direction, so a LIVE
        #: unresolved pid makes the empty test UNREADABLE instead of "nobody there".
        self._census_gaps: list[int] = []
        self._claim: tuple[bool, str, bool] | None = None
        self.demoted = 0
        self.judged = 0
        #: The EMPTY-claim leg's own counters (`judge_empty`), kept SEPARATE from the
        #: stale leg's so a surface note can report which rule did the work.
        self.empty_demoted = 0
        self.empty_judged = 0
        self.empty_kept: list[str] = []
        self.empty_unmeasured: list[str] = []
        #: Why a CANDIDATE (a ref that already passed the divergence and silence tests)
        #: was still kept blocking. Those are the interesting refusals — an owner or a
        #: claim — and the surface note prints them, so a reader who expected a demotion
        #: can see which of the two remaining tests caught it (#6108's auditability).
        self.kept: list[str] = []
        #: Why a candidate could not be MEASURED at all (`counts`/tip date unreadable).
        #: Separated from `kept` because the two say different things: one is the rule
        #: holding, the other is the gate refusing to decide — and a reader must be able to
        #: tell "a live owner is there" from "we could not look".
        self.unmeasured: list[str] = []
        #: (live, basis, UNREADABLE). ⛔ THE THIRD ELEMENT IS NOT DERIVED FROM THE BASIS
        #: TEXT: a refusal that says "I could not look" must read differently from one
        #: that says "I looked and a lane is there", and matching on prose to tell them
        #: apart is how that distinction silently stops working (the owner reader below
        #: PREFIXES the basis with the checkout's path, so an equality test on the bare
        #: text never matched — measured in review, 2026-10-24). It is returned by the
        #: read that knows.
        self._owners: dict[str, tuple[bool, str, bool]] = {}

    # ── test 3a: a live process standing in a worktree ───────────────────────

    def _process_cwds(self) -> tuple[list[str], str]:
        """(every cwd this user's live `pi` processes stand in, error).

        `pi` is the fleet's sanctioned lane process (`nohup pi …`, a cmux pane's
        `exec pi`, a `task` child), and its cwd is what attributes it to a worktree — so
        this is the same instrument `fleet_state.live_sessions` uses for the claim gate,
        reused rather than re-derived. Both readers are `fleet_state`'s: `/proc` where the
        kernel exposes it (the cells) and one batched `lsof` on macOS, which is the box
        this rule exists for.

        ⛔ A CENSUS THAT CANNOT ANSWER IS AN ERROR, NOT AN EMPTY LIST. `ps` printing
        nothing, or a store that answered while a cwd could not be resolved, makes the
        worktree UNMEASURED — and an unmeasured worktree is never demoted.

        STATED RESIDUAL: a lane's process is a `pi`; a NON-`pi` process standing in a
        worktree with no `pi` parent (an orphaned test run whose lane has exited) is not
        seen here. Its work is still protected by test 3b (an uncommitted tree is not
        demoted), and the demoted row prints its basis so a reader can see what was
        measured.
        """
        if self._census is not None:
            return self._census
        procs, perr = fleet_state.pi_processes()
        if perr:
            self._census = ([], perr)
            return self._census
        pids = [int(p["pid"]) for p in procs if p.get("pid")]
        cwds, cerr = fleet_state.process_cwds(pids)
        if cerr:
            self._census = ([], cerr)
            return self._census
        # ⛔ AN UNRESOLVED CWD IS NOT "NO LANE THERE". `process_cwds` skips a pid whose cwd
        # cannot be resolved WITHOUT reporting an error (its docstring: "an unread
        # /proc/<pid>/cwd is skipped"; the per-pid judgement is `live_sessions`' job).
        # Taken as a complete census that reads "no live pi process" about a live lane the
        # reader simply could not see — verified in review with `/proc/<pid>/cwd` at
        # PermissionError. So the gap is named here, and `_census_complete` refuses to
        # answer from it. Only LIVE gaps count: a pid that exited between the two reads is
        # not a lane (the same distinction `_usable_cwd` documents).
        self._census_gaps = [
            pid for pid in pids if pid not in cwds and fleet_state.pid_alive(pid)
        ]
        self._census = (sorted(set(cwds.values())), "")
        return self._census

    def _census_complete(self) -> str:
        """`""` when the census can answer "is a lane standing in X" for EVERY live pi.

        A gap is a live process whose cwd this user cannot read: its lane cannot be
        attributed to a checkout, so "nobody holds this" is not a measurement it can
        support. Returned as a reason rather than a bool so the refusal prints WHAT was
        unreadable (#1185's rule: an unreadable verdict must never read as free).
        """
        _cwds, err = self._process_cwds()
        if err:
            return f"the process census could not be read ({err})"
        if self._census_gaps:
            return (f"the process census could not resolve the cwd of {len(self._census_gaps)} "
                    f"live `pi` process(es) (pid {', '.join(str(p) for p in self._census_gaps[:4])})"
                    + (" …" if len(self._census_gaps) > 4 else ""))
        return ""

    def _owner(self, path: str) -> tuple[bool, str, bool]:
        """(does a live owner hold this worktree?, basis, was the read UNREADABLE?)."""
        if path in self._owners:
            return self._owners[path]
        cwds, err = self._process_cwds()
        if err:
            basis = (f"the process census could not be read ({err}) — a live owner "
                     "cannot be ruled out (NOT demoted)")
            self._owners[path] = (True, basis, True)
            return self._owners[path]
        real = os.path.realpath(path)
        prefix = real.rstrip(os.sep) + os.sep
        for cwd in cwds:
            if cwd == real or cwd.startswith(prefix):
                self._owners[path] = (True, f"live pi process standing in it ({cwd})", False)
                return self._owners[path]
        # ⛔ A DIRECTORY THAT IS NOT THERE CANNOT BE `git status`-ED, and `_run` reports
        # that spawn failure as "<git>: command not found" — a reason that blames the
        # wrong thing. `os.path.isdir` is False for EACCES/ESTALE as well as for a missing
        # path, and every one of those FAILS CLOSED here: an unreadable tree cannot be
        # read as clean, and a MOVED worktree is LIVE (its `.git` holds an absolute
        # `gitdir:`), so absence is never evidence of death. Stated, not assumed.
        if not path or not os.path.isdir(path):
            basis = ("its registered worktree directory is not on disk or not readable, so "
                     "uncommitted work cannot be ruled out — NOT demoted (a MOVED worktree "
                     "keeps working from its new path)")
            self._owners[path] = (True, basis, True)
            return self._owners[path]
        rc, out, errout, timed_out = _run(
            [self.git_bin, "status", "--porcelain"], path, self.timeout,
        )
        if rc != 0:
            why = "timeout" if timed_out else f"exit {rc}"
            basis = (f"`git status` in the worktree failed ({why}: "
                     f"{_one_line(errout)}) — uncommitted work cannot be ruled out "
                     "(NOT demoted)")
            self._owners[path] = (True, basis, True)
            return self._owners[path]
        if out.strip():
            self._owners[path] = (True, "the worktree has uncommitted work", False)
            return self._owners[path]
        self._owners[path] = (False, "no live pi process in the worktree and no "
                                     "uncommitted work in it", False)
        return self._owners[path]

    def _live_worktree_owner(
        self, ref: str, sha: str | None,
    ) -> tuple[bool, str, bool]:
        """(held by a live worktree?, basis, UNREADABLE?) — ONE answer for all its records."""
        if self.worktrees is None:
            basis = ("the worktree list could not be read, so a live owner cannot "
                     "be ruled out (NOT demoted)")
            return True, basis, True
        records = _local_worktrees_for(ref, sha, self.worktrees)
        for block in records:
            live, basis, unreadable = self._owner(block.get("path") or "")
            if live:
                return True, f"{block.get('path')}: {basis}", unreadable
        if not records:
            return False, "no worktree is checked out on it", False
        return False, "; ".join(
            f"{block.get('path')}: no live pi process and no uncommitted work"
            for block in records
        ), False

    def _name_matched_owner(self, ref: str) -> tuple[bool, str, bool]:
        """(is `ref` held by a worktree CHECKED OUT ON IT?, basis) — NAME MATCH ONLY.

        ⛔ DELIBERATELY NOT `_live_worktree_owner`, and the difference is PRECISION, not
        style. That reader also matches a record by its HEAD SHA, because a lane testing a
        pushed head is a detached checkout of the same commit. For a 0-ahead branch the tip
        SHA is a commit main already has, so the SHA key would hand this judgement to
        whatever OTHER checkout happens to sit on that commit — on a hub checkout, `main`'s
        own worktree. Uncommitted work there is not work on this branch, and blocking an
        empty claim on it would be the same "irrelevant dirt holds an arbitrary issue
        forever" defect `_local_worktrees_for` refuses for `(detached)`.
        """
        if self.worktrees is None:
            basis = ("the worktree list could not be read, so a live owner cannot "
                     "be ruled out (NOT demoted)")
            return True, basis, True
        bare = _short_branch(ref)
        records = [
            block for block in self.worktrees
            if (block.get("branch") or "")
            and block["branch"] != "(detached)"
            and bare
            and _short_branch(block["branch"]) == bare
        ]
        for block in records:
            live, basis, unreadable = self._owner(block.get("path") or "")
            if live:
                return True, f"{block.get('path')}: {basis}", unreadable
        if not records:
            return False, "no worktree is checked out on it", False
        return False, "; ".join(
            f"{block.get('path')}: no live pi process and no uncommitted work"
            for block in records
        ), False

    def _unregistered_checkout_owner(
        self, ref: str, base: str,
    ) -> tuple[bool, str, bool]:
        """(does a live `pi` stand in a checkout of `ref` git does NOT register?, basis, UNREADABLE?).

        ⛔ THE HOLE THIS CLOSES (reproduced in review, 2026-10-24). `_name_matched_owner`
        asks the worktree LIST, and the list is not the world: a lane whose checkout was
        MOVED and whose stale registration was then PRUNED (`git worktree prune`, which is
        this module's own advice for a dead record) has NO record at all. The list then
        answers "no worktree is checked out on it" while a live `pi` stands in the moved
        tree writing this very branch — and the ref is demoted. Measured: `mv <wt>
        moved-away` + prune + a live `pi` in the moved tree -> `VERDICT: CLEAN (exit 0)`.

        So the census is asked the COMPLEMENT question: for every live `pi` cwd that is
        inside NO registered checkout, does that cwd BELONG TO THIS REPOSITORY?

          * its `--git-common-dir` IS ours -> an unregistered checkout of ours (a worktree
            whose registration was removed). Ask git which branch it holds: `ref`
            (a claim — BLOCKS), another branch (not this ref), or a HEAD it cannot name
            (BLOCKS — it cannot be ruled out);
          * the common dir cannot be read AND its `.git` pointer names a path under OUR git
            dir -> OURS and BROKEN, which is exactly what `worktree prune` leaves behind
            when the tree was moved: BLOCKS, reported `UNREADABLE`;
          * anything else (a `pi` run from `$HOME`, another repository's checkout, a clone)
            -> not a claim on this repository's refs at all. ⛔ A SEPARATE CLONE HAS ITS OWN
            BRANCHES: treating a foreign checkout on a same-named branch as a holder would
            block every issue in every repo on a box where some other repo happens to carry
            the same branch name — the over-block that gets a gate switched off.

        `base` is `_name_matched_owner`'s answer, carried into the basis so the printed row
        still says what the list said.
        """
        why = self._census_complete()
        if why:
            basis = (f"{base}; and {why}, so a live owner of an UNREGISTERED checkout "
                     "cannot be ruled out — NOT demoted")
            return True, basis, True
        cwds, _err = self._process_cwds()
        registered = [
            os.path.realpath(block["path"]) for block in (self.worktrees or [])
            if block.get("path")
        ]
        ours = self._git_common_dir(self.repo)
        if ours is None:
            basis = (f"{base}; and this checkout's own git directory could not be "
                     "identified, so an UNREGISTERED checkout cannot be ruled out "
                     "— NOT demoted")
            return True, basis, True
        bare = _short_branch(ref)
        for cwd in cwds:
            real = os.path.realpath(cwd)
            if any(
                real == root or real.startswith(root.rstrip(os.sep) + os.sep)
                for root in registered
            ):
                continue
            theirs = self._git_common_dir(cwd)
            if theirs is None:
                # ⛔ NOT AN ERROR BY ITSELF: the cwd may simply not be a checkout this
                # process can attribute. Only a `.git` POINTER INTO OUR OWN GIT DIR makes
                # it ours — that is the fingerprint of a linked worktree whose admin dir
                # was pruned after the tree moved.
                pointer = self._worktree_gitdir_of(cwd)
                if pointer and _is_within(pointer, ours):
                    basis = (f"{base}; and a live pi process stands in {cwd}, whose `.git` "
                             f"names {pointer} — an UNREGISTERED checkout of THIS repository "
                             "(moved, or pruned) whose HEAD cannot be named — NOT demoted")
                    return True, basis, True
                continue
            if os.path.realpath(theirs) != os.path.realpath(ours):
                # Another repository. Its branches are not this repository's refs.
                continue
            rc2, name, _err2, _to2 = _run(
                [self.git_bin, "-C", cwd, "symbolic-ref", "--quiet", "--short", "HEAD"],
                self.repo, self.timeout,
            )
            if rc2 != 0:
                basis = (f"{base}; and a live pi process stands in the unregistered checkout "
                         f"{cwd} of this repository, whose HEAD cannot be named (detached or "
                         "unreadable) — NOT demoted")
                return True, basis, True
            if _short_branch(name.strip()) == bare:
                return True, (
                    f"a live pi process stands in the UNREGISTERED checkout {cwd}, "
                    f"which is on {ref} (its worktree record is gone — moved, or pruned)"
                ), False
        return False, (
            f"{base}; no live pi process stands in an unregistered checkout of it"
        ), False

    def _git_common_dir(self, where: str) -> str | None:
        """The ABSOLUTE git-common-dir of the checkout at `where`, or None.

        `rev-parse --git-common-dir` prints a path RELATIVE to the cwd for a main checkout
        (".git") and an absolute one for a linked worktree, so it is resolved here rather
        than trusted. `None` means "could not be read" — the caller decides what that
        means, because for the EMPTY leg it is evidence only when the `.git` pointer names
        our own git dir (`_unregistered_checkout_owner`).
        """
        rc, out, _err, _to = _run(
            [self.git_bin, "-C", where, "rev-parse", "--git-common-dir"],
            self.repo, self.timeout,
        )
        if rc != 0 or not out.strip():
            return None
        got = out.strip()
        if not os.path.isabs(got):
            got = os.path.join(where, got)
        return os.path.realpath(got)

    @staticmethod
    def _worktree_gitdir_of(cwd: str) -> str:
        """What `<cwd>/.git` POINTS AT when it is a linked worktree's pointer file, else `""`.

        A linked worktree's `.git` is a FILE holding `gitdir: <path>`; a main checkout's is
        a DIRECTORY. Only the pointer is read, and only to answer "is this checkout ours?".
        """
        marker = os.path.join(cwd, ".git")
        try:
            if not os.path.isfile(marker):
                return ""
            with open(marker, encoding="utf-8", errors="replace") as handle:
                first = handle.readline().strip()
        except OSError:
            return ""
        if not first.startswith("gitdir:"):
            return ""
        target = first.split(":", 1)[1].strip()
        if not target:
            return ""
        if not os.path.isabs(target):
            target = os.path.join(cwd, target)
        return os.path.realpath(target)

    # ── test 4: the issue claim ──────────────────────────────────────────────

    def _claim_live(self) -> tuple[bool, str, bool]:
        """(is a LIVE `refs/claims/issue-<N>` held?, basis).

        The read is `claims.sh:claim_issue_preflight`'s, in the same order, for the same
        reason: `git ls-remote <ref>` exits 0 with EMPTY output when the ref does not
        exist and NON-ZERO only when the remote could not be consulted, so collapsing the
        two would read an outage as "nobody holds it". The lease is DATA (the claim
        commit's own message) and a claim is `--force-with-lease`-fenced on the ref's
        SHA, so a temp ref is fetched to read the message and deleted immediately.

        ⛔ THE POLARITY IS COPIED FROM THE ONE FUNCTION THAT OWNS IT (`_lease_is_live`,
        claims.sh): absent, `0`, non-numeric and oversized leases are all LIVE. An
        unreadable registry, an unreadable fetch and an unreadable local clock are LIVE
        too. Every one of those only ever REFUSES a demotion.

        ⛔ ONLY `issue-<N>` IS AN ISSUE CLAIM. `refs/claims/<PR>-<sha12>` and
        `refs/claims/pr-<N>` are the PR keys and the issue/PR number spaces are disjoint;
        reading one as an issue claim would block issue N on a review lane for PR N.
        """
        if self._claim is not None:
            return self._claim
        ref = f"{CLAIM_NS}/issue-{self.issue}"
        rc, out, err, timed_out = _run(
            [self.git_bin, "ls-remote", "origin", ref], self.repo, self.timeout,
        )
        if rc != 0:
            why = "timeout" if timed_out else f"exit {rc}"
            basis = (f"the claim registry could not be read ({ref}: "
                     f"`git ls-remote` {why}: {_one_line(err)}) — NOT demoted")
            self._claim = (True, basis, True)
            return self._claim
        sha = out.split()[0] if out.split() else ""
        if not sha:
            self._claim = (False, f"no `{ref}` on origin", False)
            return self._claim
        tmp = f"{CLAIM_TMP_NS}/preflight-{os.getpid()}-{int(self.now)}"
        try:
            frc, _fout, ferr, _fto = _run(
                [self.git_bin, "fetch", "-q", "origin", f"+{ref}:{tmp}"],
                self.repo, self.timeout,
            )
            if frc != 0:
                basis = (f"`{ref}` exists but its lease could not be read "
                         f"({_one_line(ferr)}) — treated as LIVE, NOT demoted")
                self._claim = (True, basis, True)
                return self._claim
            return self._lease_from_ref(ref, tmp, sha)
        finally:
            _run([self.git_bin, "update-ref", "-d", tmp], self.repo, self.timeout)

    def _lease_from_ref(self, ref: str, tmp: str, sha: str) -> tuple[bool, str, bool]:
        rc, out, _err, _to = _run(
            [self.git_bin, "cat-file", "commit", tmp], self.repo, self.timeout,
        )
        if rc != 0:
            self._claim = (True, (f"`{ref}` exists but its commit could not be read — "
                                 "treated as LIVE, NOT demoted"), True)
            return self._claim
        lease = ""
        for line in out.splitlines():
            for token in line.split():
                if token.startswith("lease_until="):
                    lease = token.split("=", 1)[1]
        # claims.sh:_lease_unparseable — an absent value, the `0` sentinel, any
        # non-digit (a negative collapses to `0`) and a 19+-digit value (the `[`
        # 18-digit bound exists so a full-width lease cannot wrap) are all "a claim
        # EXISTS whose lease this reader cannot parse" — i.e. LIVE, never free.
        if not lease or not lease.isdigit() or len(lease) > 18 or lease == "0":
            self._claim = (True, (f"`{ref}` is held with a lease this reader cannot "
                                 f"parse (lease_until={lease or 'absent'}) — treated as "
                                 "LIVE, NOT demoted"), True)
            return self._claim
        until = int(lease)
        if until > int(self.now):
            self._claim = (True, (f"`{ref}` is a LIVE claim (lease {until - int(self.now)}s "
                                 "left) — NOT demoted"), False)
            return self._claim
        self._claim = (False, (f"the only `{ref}` (sha {sha[:9]}) EXPIRED at {until}, "
                              f"{(int(self.now) - until) // 3600}h ago"), False)
        return self._claim

    # ── the four tests ───────────────────────────────────────────────────────

    def note_since(self, judged0: int, demoted0: int, kept0: int,
                   unmeasured0: int = 0) -> str:
        """The surface note for ONE surface's share of the judgement.

        Scoped by the caller's counters so a row reports only what IT measured: the
        worktree row must not claim the branch row's refusals (a note that attributes one
        surface's measurement to another is the defect `truncation_widenable` and
        `registry_error` exist to prevent on their own surfaces).
        """
        judged = self.judged - judged0
        if not judged:
            return ""
        note = (
            f"; {judged} stale-holder candidate(s) judged, "
            f"{self.demoted - demoted0} demoted to ADVISORY (behind >= "
            f"{STALE_BEHIND_MIN}, tip silent >= {STALE_TIP_DAYS:g}d, no live worktree "
            "owner, no live claim ref — reported, non-blocking)"
        )
        unmeasured = self.unmeasured[unmeasured0:]
        if unmeasured:
            note += ("; NOT demoted on an unreadable measurement: "
                     + " | ".join(dict.fromkeys(unmeasured)))
        kept = self.kept[kept0:]
        if kept:
            # ⛔ A REFUSAL IS NAMED, NOT COUNTED. The four tests ARE the rule, so a reader
            # who expected a demotion must be able to see WHICH one held — the same
            # argument #6108 makes for printing the ahead-count.
            note += ("; kept blocking by a live owner or claim: "
                     + " | ".join(dict.fromkeys(kept)))
        return note

    def judge(self, ref: str, sha: str | None,
              counts: tuple[int, int] | None,
              tip_age_days: float | None) -> tuple[bool, str]:
        """(provably abandoned?, basis naming what was measured).

        A "no" carries the FIRST failing dimension, so the report can say why a ref the
        operator expected to be stale is still blocking — the auditable half of #6108.
        """
        self.judged += 1
        if counts is None:
            basis = "its distance from origin/main could not be measured"
            self.unmeasured.append(basis)
            return False, basis
        ahead, behind = counts
        if behind < STALE_BEHIND_MIN:
            return False, (f"only {behind} behind origin/main (< {STALE_BEHIND_MIN}) "
                           "— a recent base is a live claim")
        if tip_age_days is None:
            basis = "its tip commit's date could not be read"
            self.unmeasured.append(basis)
            return False, basis
        if tip_age_days < STALE_TIP_DAYS:
            return False, (f"its tip is {tip_age_days:.1f} days old (< {STALE_TIP_DAYS:g}) "
                           "— a recent commit is a live claim")
        live, owner_basis, _owner_unreadable = self._live_worktree_owner(ref, sha)
        if live:
            self.kept.append(owner_basis)
            return False, owner_basis
        claimed, claim_basis, _claim_unreadable = self._claim_live()
        if claimed:
            self.kept.append(claim_basis)
            return False, claim_basis
        self.demoted += 1
        return True, (
            f"no commit in {tip_age_days:.1f} days, {behind} behind origin/main, "
            f"{ahead} ahead; {owner_basis}; {claim_basis}"
        )

    # ── the EMPTY leg: nothing in the ref that main does not already have ───

    def judge_empty(self, ref: str,
                    counts: tuple[int, int] | None) -> tuple[bool, str, bool]:
        """(provably an EMPTY claim?, basis, the basis is an UNREADABLE reading).

        THE MEASURED DEFECT (measured 2026-10-24T03:20Z, `collision_preflight.py 9567`):
        `refs/heads/feat/9567-tm` was **0 commits ahead / 121 behind origin/main** — a
        branch a lane created and then never used — and it refused dispatch on #9567 (a
        P0-blocker) permanently, for every agent, with no way to clear it short of
        deleting the branch. #9097 was the second instance in the same window. The
        StaleHolder rule above cannot reach this shape: that rule needs the ref to be
        far behind (>= 300), and this one is only 121 behind — so the divergence arm is
        NOT satisfied, and a same-day tip fails SILENCE too. The stale rule is built to
        infer abandonment from age; an empty branch needs no inference at all — main
        already contains every commit the ref has, so there is nothing to collide with.

        ⛔ THIS IS THE DEMOTION #6108 ASKED FOR AND `.judge` DELIBERATELY REFUSED, and
        the refusal was a RECORDED DECISION, so it is reopened rather than quietly
        reversed. The owner reopened it on 2026-10-24 with the measured cost above. The
        old objection was real and is answered by the CONDITIONS, not by argument: a
        branch between `git worktree add` and its first commit is ALSO 0 ahead, and
        demoting it would let two lanes write to one branch (#4256) — so a 0-ahead ref
        is demoted ONLY when every mechanical piece of holder evidence is absent.

            OVERRIDES: #6108's recorded trade-off — "THE COUNT IS REPORTED, NOT ACTED
            ON … every 0-ahead branch must keep blocking" — a local branch that is 0
            commits ahead of origin/main AND has NO live worktree owner (no live `pi`
            process standing in a checkout of it, no uncommitted work in any such
            checkout) AND NO live `refs/claims/issue-<N>` is now ADVISORY. The claim
            #6108's trade-off protects is still protected: a lane that has CLAIMED the
            work still blocks, through the worktree-owner and claim-ref tests, which is
            where "has a lane taken this" is answered mechanically since #8175 (the
            branch's NAME is not the claim; the holder is).

        ALL of the following are required (any failure leaves the ref BLOCKING):

          1. it is a LOCAL branch (`refs/heads/`). ⛔ NOT a remote-tracking ref. The
             STALE leg above does demote `origin/...` refs (a 300-behind, silent, unowned
             one), but for THIS leg a remote-tracking ref is a FETCH CACHE: its sha is
             whatever the last fetch wrote, so "0 ahead of origin/main" would describe a
             stale cache, not the branch that exists on the remote — the class #7693 is
             about. It is refused by name, and a 0-ahead `refs/remotes/origin/...` keeps
             blocking (#7693, #6108).
          2. `ahead == 0`, measured AT THE GATE (one `rev-list --count --left-right`,
             never a cached count). An unreadable count is NOT zero — it is
             `UNREADABLE`, and the ref keeps blocking.
          3. no live worktree OWNER (`_name_matched_owner`, NAME match only): no live `pi`
             process whose cwd is inside a worktree CHECKED OUT ON IT, and no uncommitted
             work in one. A failed `git status` / an unreadable process census is
             UNREADABLE, and an unreadable census can never be `no owner`.
          4. no LIVE claim ref (`_claim_live`, the same polarity `claims.sh` owns).

        STATED RESIDUALS. The first two cannot produce a false CLEAN; the third is a
        real, DECIDED window — stated here rather than argued away, because a reader who
        relies on a worktree to hold an issue needs to know what actually holds it:

          * a worktree record whose DIRECTORY is gone (a `mv`, or a registration not yet
            pruned) keeps blocking: `_worktree_liveness` documents why absence is not
            evidence of death, and `_owner` then cannot run `git status` in it — so the
            ref is UNREADABLE, not empty. A human clears it with `git worktree prune` or
            by deleting the branch; this gate REPORTS, it never reaps;
          * a lane whose work is entirely uncommitted in a checkout the process census
            cannot associate with the ref (a non-`pi` process) is covered by condition 3's
            dirt test only — the same residual `.judge` states. A MOVED checkout is NOT
            in that class: while its registration survives, `_owner` reads it through the
            path it has (a missing directory is UNREADABLE, not clean); once the
            registration is PRUNED, `_unregistered_checkout_owner` finds the lane by
            asking git what branch each unregistered `pi` cwd holds;
          * ⛔ PREPARE-THEN-DISPATCH. A clean, registered checkout of the ref with NO live
            `pi` process in it and no claim ref is demoted: every mechanical signal this
            gate can read says the same thing about it as about an abandoned checkout, and
            the decision above distinguishes by DIRT (a dirty checkout blocks, a clean one
            does not). A flow that creates the checkout BEFORE the lane's process starts —
            a controller running `git worktree add`, writing a brief, then dispatching — is
            therefore protected by `refs/claims/issue-<N>` (`claims.sh`), which BLOCKS, and
            NOT by the checkout's existence. Take the claim first, or expect a peer's
            pre-flight to answer CLEAN for the issue in that window.
        """
        if not ref.startswith("refs/heads/"):
            # NOT counted: this is not a candidate, it is out of scope by construction.
            return False, (
                "not a local branch — the EMPTY-CLAIM test is scoped to refs/heads/ "
                "(a remote-tracking ref is a fetch cache, #7693)"
            ), False
        if counts is None:
            # COUNTED: it IS a candidate — it named the issue, it is local, and only the
            # measurement failed. Leaving the counter to the `ahead == 0` arm dropped
            # exactly the case this leg exists for out of the surface note (measured in
            # review: the note printed nothing at all for an unreadable distance).
            self.empty_judged += 1
            basis = "its distance from origin/main could not be measured"
            self.empty_unmeasured.append(basis)
            return False, basis, True
        ahead, behind = counts
        if ahead != 0:
            # NOT counted: a ref with its own commits is not an empty claim, and the
            # stale leg is what judges those.
            return False, f"{ahead} commit(s) ahead of origin/main — it carries work", False
        self.empty_judged += 1
        live, owner_basis, owner_unreadable = self._name_matched_owner(ref)
        if not live:
            # ⛔ THE LIST IS NOT THE WORLD — see `_unregistered_checkout_owner`, which is
            # where a MOVED+PRUNED checkout's live lane used to disappear into a CLEAN.
            live, owner_basis, owner_unreadable = self._unregistered_checkout_owner(
                ref, owner_basis,
            )
        if live:
            # ⛔ "I COULD NOT LOOK" IS NOT "A LANE IS THERE". Both block, but filing an
            # unreadable reading under the owner/claim heading told the reader the opposite
            # of what happened (measured in review), so the two are separated here exactly
            # as the stale leg separates `kept` from `unmeasured`.
            (self.empty_unmeasured if owner_unreadable else self.empty_kept).append(owner_basis)
            return False, owner_basis, owner_unreadable
        claimed, claim_basis, claim_unreadable = self._claim_live()
        if claimed:
            (self.empty_unmeasured if claim_unreadable else self.empty_kept).append(claim_basis)
            return False, claim_basis, claim_unreadable
        self.empty_demoted += 1
        return True, (
            f"EMPTY-CLAIM — 0 commits ahead of origin/main ({behind} behind), so the "
            f"ref holds nothing main does not already have; {owner_basis}; {claim_basis}"
        ), False

    def empty_note_since(self, judged0: int, demoted0: int, kept0: int,
                         unmeasured0: int = 0) -> str:
        """The surface note for ONE surface's share of the EMPTY-leg judgement."""
        judged = self.empty_judged - judged0
        if not judged:
            return ""
        note = (
            f"; {judged} 0-ahead local branch(es) judged as EMPTY CLAIMS, "
            f"{self.empty_demoted - demoted0} demoted to ADVISORY (0 commits ahead of "
            "origin/main, no live worktree owner, no uncommitted work, no live claim "
            "ref — reported, non-blocking)"
        )
        unmeasured = self.empty_unmeasured[unmeasured0:]
        if unmeasured:
            note += ("; NOT demoted on an unreadable measurement: "
                     + " | ".join(dict.fromkeys(unmeasured)))
        kept = self.empty_kept[kept0:]
        if kept:
            note += ("; kept blocking by a live owner or claim: "
                     + " | ".join(dict.fromkeys(kept)))
        return note


def scan_branch_surface(
    surface: Surface, refs: list[tuple[str, str]], issue: int,
    identity: Identity, merged_heads: Mapping[str, int | None],
    ancestor_merged: set[str] | None, main_tip: str | None,
    first_parent: set[str] | None,
    remote_namespaces: set[str] | None = None,
    targeted_terminal: dict[str, str] | None = None,
    counts: object = None,
    stale: StaleHolder | None = None,
    tip_age: object = None,
    remote_state: object = None,
) -> None:
    """NUMBER matching only — the lexical arm is gone (#3504).

    A ref is examined only when it names this issue; when it does, the decision
    is taken in this order, so the caller's own work is never read as a
    competing claim:

      1. **self** — the ref is the caller's OWN branch (#3504 class 4)
      2. **terminal** — its content already landed (#5186)
      2b. **terminal, targeted** — a MERGED PR whose head is this ref's tip, or
          whose head CONTAINS this ref's tip, found outside the closed-PR
          sample (#5485); supplied by the caller, which has the gh binary and
          the resolved slug. Demoted on EITHER an exact tip match OR
          containment — see `_branch_terminal_state_from_prs`
      3. **stale, unowned, unclaimed** — the ref's own holder evidence is past its
         expiry, measured in FOUR dimensions at once (divergence, silence, no live
         worktree owner, no live claim ref — `StaleHolder`): reported at `weak`
         strength as ADVISORY and never dropped (#9806). `ahead` plays no part
      3b. **EMPTY CLAIM** — a LOCAL branch 0 commits ahead of `origin/main` with no
         live worktree owner, no uncommitted work and no live claim ref
         (`StaleHolder.judge_empty`): ADVISORY, reported and never dropped (#6108,
         reopened 2026-10-24). An UNREADABLE measurement is `UNREADABLE` and BLOCKS
      4. otherwise **blocking** — a live lane that names this issue

    A self, terminal, stale or empty ref is reported (never silent) at `weak` strength,
    which cannot contribute to the verdict. Refs that do not name the issue are not
    reported at all: with the lexical arm gone there is nothing to say about
    them, and emitting ~170 "already merged" rows would bury the real signal.
    """
    for ref, sha in refs:
        # A tool-generated branch cannot be a lane's claim (#3611) — checked
        # BEFORE the number test, since the number test is what false-matches.
        if _is_generated_branch(ref):
            continue
        if not number_present(ref, issue):
            continue
        if identity.owns_branch(ref):
            surface.add(
                ref,
                "your own branch (declared with --self-branch, or the current "
                "branch of this checkout) — not a competing claim",
                "weak",
            )
            continue
        terminal = _branch_terminal_state(
            ref, sha, merged_heads, ancestor_merged, main_tip, first_parent,
        )
        if terminal is not None:
            # #10174: a MERGED LEFTOVER says WHICH PR landed it, and whether the
            # remote still holds the branch. The remote read is asked ONLY here —
            # for a ref the merge history has already demoted — so the cost is one
            # bounded request for zero-or-one ref per run, never per branch.
            extra = ""
            if terminal.tip_is_a_merged_head:
                extra = _remote_counterpart_clause(
                    _ask_remote_state(remote_state, ref), ref, sha)
            surface.add(
                ref,
                f"branch is {terminal.reason}{extra} — immutable history, not "
                "in-flight work (non-blocking), and NOT a licence to reuse the "
                "name",
                "weak",
            )
            continue
        # #5485: the tests above cannot see a squash-merge older than the closed-PR
        # sample. The caller looked THAT up for exactly the refs reaching here, and a
        # reason exists only for an EXACT tip match on a merged PR, or when
        # the tip is CONTAINED in one (ancestry) — both supplied by the caller. The
        # fail-open guards live in `_branch_terminal_state_from_prs` (NOT here):
        # `run_preflight` passes THAT function the witnesses, and this site only
        # consumes the demotion it returned.
        targeted = (targeted_terminal or {}).get(ref)
        if targeted is not None:
            # Every value in `targeted_terminal` is a MERGED PR naming this tip
            # (exactly or by containment), i.e. the same MERGED-LEFTOVER class as
            # predicate 1 — so the same corroboration applies, and it is asked the
            # same bounded way (this ref was about to BLOCK).
            extra = _remote_counterpart_clause(
                _ask_remote_state(remote_state, ref), ref, sha)
            surface.add(
                ref,
                f"branch is terminal — {targeted}{extra}; immutable history, not "
                "in-flight work (non-blocking), and NOT a licence to reuse the "
                "name",
                "weak",
            )
            continue
        # #6622: a fetch-cache ref names a CACHE KEY, not a holder. A
        # `refs/remotes/…` ref that NO configured remote's refspec targets was
        # written by a one-shot `git fetch <url> <spec>:refs/remotes/<x>/N`. This
        # is the tool's OWN stated model of that surface — its note says a
        # remote-tracking ref "is a local fetch cache", and
        # `_branch_terminal_state` already refuses to judge one — so counting it
        # as a strong holder contradicted the model the surface documents.
        cache_remote = _fetch_cache_remote(ref, remote_namespaces)
        if cache_remote is not None:
            surface.add(
                ref,
                f"fetch-cache ref ('{cache_remote}' under refs/remotes/, which no "
                "configured remote's fetch refspec targets) — a cache KEY written "
                "by a one-shot git fetch, not a branch a lane holds (non-blocking)",
                "weak",
            )
            continue
        # #6108: the ahead-count, reported on every BLOCKING branch hit. A gate
        # whose two cases — "a lane has committed work here" and "an empty branch
        # whose name contains this number exists" — print identically cannot be
        # audited by its reader, and an un-auditable gate gets overridden until
        # it stops being a gate (#6108's own argument).
        #
        # Note the scope: this is the path that DECIDES the verdict. A ref that
        # already `continue`d above (the caller's own branch, a terminal one, a
        # fetch-cache key) is non-blocking by construction, so its distance from
        # main carries none of the ambiguity this datum exists to resolve — and
        # skipping the call there means no git subprocess is paid for a hit whose
        # strength is already settled.
        #
        # ⛔ THE COUNT IS REPORTED **AND** (for a provably empty claim) DECIDED. Both
        # halves are deliberate, and the history matters because the second half was a
        # recorded REFUSAL until 2026-10-24.
        #
        # REPORTED: a gate whose two cases — "a lane has committed work here" and "an
        # empty branch whose name contains this number exists" — print identically cannot
        # be audited by its reader, and an un-auditable gate gets overridden until it
        # stops being a gate (#6108's own argument).
        #
        # DECIDED: #6108 asked for `ahead == 0` to stop blocking, and the first attempt
        # was refused because a lane between `git worktree add` and its first commit is
        # EXACTLY 0 ahead — demoting that would let two lanes write to one branch
        # (#4256, which must never reopen). The measured cost of the refusal then landed
        # (2026-10-24T03:20Z): `refs/heads/feat/9567-tm` at 0 ahead / 121 behind froze a
        # P0-blocker and `fix/9097-protocol-precedence` (0/75) an open P1, permanently,
        # for every agent — the block's cause was an empty ref nobody could delete from
        # inside a gate that never reaps. The owner reopened #6108 for the NARROW form:
        # `StaleHolder.judge_empty` demotes a 0-ahead LOCAL branch only when every
        # mechanical piece of holder evidence is also absent (no live worktree owner, no
        # uncommitted work, no live claim ref). The `worktree add` window stays BLOCKING
        # while the lane's process is alive (a live `pi` in the checkout is an owner); the
        # PREPARE-THEN-DISPATCH shape — checkout created before the process starts — is
        # held by the CLAIM REF instead, which is a stated residual in `judge_empty`.
        #
        # ⛔ SO `ahead` IS STILL NOT DECISIVE BY ITSELF, and nothing here reads it as
        # "free": an UNREADABLE count is `UNREADABLE` (blocking), a ref with ≥1 commit
        # ahead blocks exactly as before, and the stale leg (`judge`) is unchanged and
        # still runs after this one. See `judge_empty`'s `OVERRIDES:` line and its
        # STATED RESIDUALS — a worktree record whose directory is gone keeps blocking,
        # because absence is not evidence of death and the gate reports, never reaps.
        ahead = behind = None
        if counts is not None:
            measured = counts(ref)  # type: ignore[operator]
            if measured is not None:
                ahead, behind = measured

        detail = f"matched issue-number ({issue})"
        if ahead is not None:
            detail += f" — {ahead} commit(s) ahead / {behind} behind origin/main"
        if stale is not None:
            measured = (ahead, behind) if ahead is not None else None
            empty, empty_basis, unreadable = stale.judge_empty(ref, measured)
            if empty:
                surface.add(
                    ref,
                    f"{detail} — ADVISORY, not in-flight work: {empty_basis} — reported "
                    "so it can be cleaned up, NEVER dropped",
                    "weak",
                )
                continue
            if unreadable:
                # The ref stays BLOCKING, and the reason is named as a FAILED READ so a
                # reader can tell "the gate declined to decide" from "a lane is there".
                detail += (f" — UNREADABLE {empty_basis} — NOT treated as an empty "
                           "claim (fail-closed)")
            age = tip_age(ref) if tip_age is not None else None  # type: ignore[operator]
            abandoned, basis = stale.judge(ref, sha, measured, age)
            if abandoned:
                surface.add(
                    ref,
                    f"{detail} — ADVISORY, not in-flight work: {basis} — reported "
                    "so it can be cleaned up, NEVER dropped",
                    "weak",
                )
                continue
        surface.add(ref, detail, "strong")


def _worktree_blocks(porcelain: str) -> list[dict]:
    """Parse `git worktree list --porcelain`, REFUSING anything it cannot read.

    ⛔ A PATH CONTAINING A NEWLINE SPLITS A RECORD, AND THE OLD PARSER SILENTLY
    TRUNCATED IT. `--porcelain` does not C-quote a newline; it emits the raw
    byte, so a worktree whose path is `<tmp>/wt/fix\n3061-mine` arrives as
    `worktree <tmp>/wt/fix` followed by a bare `3061-mine`. The second line
    matched no prefix and was DROPPED, the path was truncated at the newline, and
    the issue number — which sat after it — stopped matching, so the live
    worktree vanished from the surface with no error at all. Fail-OPEN, on the
    surface #3061 exists to make untruncated.

    The call-site guard could not catch it: it compared `len(blocks)` against the
    number of `"worktree "` lines, and this function starts exactly one block per
    such line, so the two were equal by construction and the guard was
    unreachable. Validate the SHAPE instead — an unreadable line is a parse loss,
    and a parse loss is a REFUSAL, never a silently smaller answer.
    """
    blocks: list[dict] = []
    cur: dict | None = None
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        if line.startswith("worktree "):
            if cur is not None:
                blocks.append(cur)
            cur = {"path": line[len("worktree "):].strip()}
        elif cur is None:
            raise SurfaceError(
                f"worktree porcelain: content before any 'worktree ' record "
                f"({line[:80]!r}) — refusing a partial scan"
            )
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):].strip()
        elif line == "detached":
            cur["branch"] = "(detached)"
        elif line == "bare":
            cur["bare"] = True
        elif line.startswith("locked") or line.startswith("prunable"):
            # #6622: CAPTURE git's OWN signal that a registration cannot be used.
            # Dropping these (a bare `pass`) left the liveness decision with
            # path-absence ALONE — which is a FALSE CLEAN for a worktree that was
            # MOVED: `mv` leaves a fully working worktree behind (its `.git`
            # holds an absolute `gitdir:`), while `git worktree list` keeps
            # reporting the OLD path and marks it `prunable`. A lane can be live
            # in the moved directory and the gate would exit 0. See
            # `_worktree_liveness`.
            #
            # The VALUE is kept, not a bool: `locked initializing` names the
            # aborted `git worktree add` that produced it and `prunable gitdir
            # file points to non-existent location` names the moved record, and
            # both ride into the reported basis so a reader can tell the two
            # apart without re-deriving them by hand.
            if line.startswith("locked"):
                cur["locked"] = line[len("locked"):].strip() or "yes"
            else:
                cur["prunable"] = line[len("prunable"):].strip() or "yes"
        elif line.startswith("HEAD "):
            # The worktree's checked-out commit, carried free by --porcelain.
            #
            # ⛔ IT REACHES NEITHER TERMINAL PREDICATE when the worktree is
            # DETACHED. `_branch_terminal_state` refuses every ref that does not
            # start with `refs/heads/`, and a detached worktree's branch is the
            # literal `"(detached)"` — so the tip-SHA predicate is refused BEFORE
            # it is ever compared, and the ancestor predicate tests
            # `ref in ancestor_merged`, a set of REF NAMES, which `"(detached)"`
            # cannot be in. A detached worktree is therefore reported as a hit
            # even at a squash-merged head: fail-closed, and deliberate — with no
            # branch name there is no ref to name a landing. An earlier version of
            # this comment claimed the HEAD reached the tip-SHA predicate, which
            # is false for exactly the detached case it was describing.
            cur["head"] = line[len("HEAD "):].strip()
        else:
            # Reachable exactly when a record did not parse as the format
            # documents. A truncated (not-porcelain) read and a newline inside a
            # path both land here as an unrecognised continuation line.
            raise SurfaceError(
                f"worktree porcelain: unrecognised line {line[:80]!r} — the "
                "parse LOST part of a record (a path containing a newline "
                "splits one), so the answer would be silently smaller than the "
                "truth; refusing (NOT clean)"
            )
    if cur is not None:
        blocks.append(cur)
    if porcelain.strip() and not blocks:
        raise SurfaceError(
            "worktree porcelain: non-empty output parsed to zero records — "
            "refusing a partial scan"
        )
    for record in blocks:
        # `--porcelain` emits HEAD for every record except a `bare` one, so a
        # record without either is one the parse could not actually see. This is
        # checked HERE, where the record is built, because the call site can no
        # longer tell a missing field from a worktree that genuinely has none.
        if not record.get("path"):
            raise SurfaceError(
                "worktree porcelain: a record has no path — refusing a partial "
                "scan"
            )
        if not record.get("head") and not record.get("bare"):
            raise SurfaceError(
                "worktree porcelain: a record has neither HEAD nor 'bare' — "
                "refusing a partial scan"
            )
        # ⛔ `branch` IS REQUIRED TOO, for every NON-BARE record, because it is
        # one of the two fields the hit test reads (`number_present(path, issue)
        # or number_present(branch, issue)`) and a worktree whose only
        # issue-naming field is its branch is reported as NO HIT when the field
        # is absent — read as the empty string and silently dropped. Requiring
        # `path` and HEAD while leaving `branch` optional applied the "a parse
        # loss is a REFUSAL" rule to two of the three fields it covers.
        # `--porcelain` emits `branch <ref>` OR the literal `detached` for every
        # non-bare record, so absence here is a parse loss, not a shape.
        if not record.get("bare") and not record.get("branch"):
            raise SurfaceError(
                "worktree porcelain: a non-bare record has neither a 'branch' "
                "line nor 'detached' — the parse lost the field the worktree hit "
                "test reads; refusing a partial scan"
            )
    return blocks


def scan_worktree_surface(
    surface: Surface, blocks: list[dict], issue: int, identity: Identity,
    merged_heads: Mapping[str, int | None], ancestor_merged: set[str] | None,
    main_tip: str | None, first_parent: set[str] | None,
    counts: object = None,
    stale: StaleHolder | None = None,
    tip_age: object = None,
) -> None:
    """Untruncated worktree scan — NUMBER match on the full path and branch.

    Same decision as the branch surface (self, then terminal, then EMPTY CLAIM, then
    STALE, then blocking), because a worktree hit is the same claim seen from a
    different angle: #3504 class 4's reproduction had the caller's OWN worktree among its
    blocking hits.

    ⛔ THE SAME EMPTY-CLAIM TEST APPLIES HERE, AND IT MUST (#6108, reopened 2026-10-24).
    The measured block named BOTH rows for the same claim — `refs/heads/feat/9567-tm`
    (0 commits ahead) AND its worktree record — so demoting the branch alone would have
    left #9567 un-dispatchable through this one. `judge_empty` is scoped to
    `refs/heads/`, so a DETACHED record is not demoted by it: no branch means no claim to
    prove empty. The path is matched in full — a worktree is named after its
    issue by convention, and the full path is the honest field to read.

    ⛔ THE SAME STALE-HOLDER TEST APPLIES HERE, AND IT MUST (#9806). A record and a ref
    naming the issue are two ways of seeing ONE claim, and the measured incident
    (`.worktrees/tm-2446`, 8.7 days old, on `feat/2446-tm`) names the WORKTREE — demoting
    the branch alone would have left the issue un-dispatchable through this row. Its
    condition 3 is narrower here: the record IS the worktree whose owner and dirt are
    checked, so "no live owner" is a statement about this record, not about the branch.
    ⛔ AGE IS NOT THE DISCRIMINATOR THERE: a long test run is a live owner, and it is the
    process-cwd read (plus uncommitted work) that decides, never how old the checkout is.

    ⛔ THE PATH IS THE WEAKER OF THE TWO FIELDS, AND #9540 IS WHY. A path is also
    a leftover: `/tmp/wt-3718` held a lane whose branch claimed #9397, and the
    substring alone was enough to block #3718 permanently. The path still blocks
    whenever nothing contradicts it (a generated or detached branch included) —
    see `_branch_names_other_issue` for the one contradiction that demotes it.
    """
    for block in blocks:
        path = block.get("path", "")
        branch = block.get("branch", "")
        # The BRANCH half is skipped for a generated name (a queue checkout
        # says nothing about ownership); the PATH is still matched in full, so
        # a real worktree cannot be hidden by this (#3611).
        branch_matchable = not _is_generated_branch(branch)
        path_hit = number_present(path, issue)
        branch_hit = branch_matchable and number_present(branch, issue)
        if not (path_hit or branch_hit):
            continue
        label = f"{path} [{branch or 'detached'}]"
        if identity.owns_worktree(path) or identity.owns_branch(branch):
            surface.add(
                label,
                "your own worktree (declared with --self-worktree, or this "
                "checkout) — not a competing claim",
                "weak",
            )
            continue
        # #9540: a PATH SUBSTRING IS A SPELLING, NOT A CLAIM. `/tmp/wt-3718`
        # matched #3718 while its branch (`fix/9397-about-edge-properties`)
        # claimed #9397 — a leftover directory name froze a product defect that
        # every other surface reported free. The branch is the field a lane
        # deliberately named, so when the two disagree the branch wins and the
        # path hit is reported (never silent) at a strength that cannot block.
        # Scoped to the case where the branch is the ONLY contradiction: a
        # branch that names THIS issue blocks, and a detached/generated branch
        # leaves nothing to contradict the path, so both keep blocking.
        if path_hit and not branch_hit and branch_matchable:
            # ⛔ `branch_matchable` IS REQUIRED HERE, AND ITS ABSENCE WAS A
            # DOCUMENTED-BUT-BYPASSED GUARD (review cycle 4). Without it a
            # GENERATED branch whose name contains a claim-shaped tail
            # (`mergify/merge-queue/fix/9397-x`) was demoted: `branch_hit` is
            # False for it BY CONSTRUCTION, and the helper then read the raw name
            # and answered 9397. A bot-minted name is not evidence about a lane,
            # so it must not be evidence about the PATH either — the worktree
            # record still blocks, which is what this module's docstring and
            # `_is_generated_branch` already promised.
            other = _branch_names_other_issue(branch, issue)
            if other is not None:
                surface.add(
                    label,
                    f"its PATH contains {issue} but its BRANCH claims #{other} "
                    "— a path substring is a spelling, not a claim; the branch "
                    "is the claim (non-blocking)",
                    "weak",
                )
                continue
        terminal = _branch_terminal_state(
            branch, block.get("head"), merged_heads, ancestor_merged,
            main_tip, first_parent,
        )
        if terminal is not None:
            # ⛔ NO REMOTE COUNTERPART HERE, deliberately: this record is a WORKTREE
            # on disk, and whether `origin` still has a branch of that name cannot
            # make a checkout less dead (or more live). The branch surface asks that
            # question about the BRANCH REF, which is the thing the remote can hold.
            surface.add(
                label,
                f"worktree is on a {terminal.reason} — immutable history, not "
                "in-flight work (non-blocking)",
                "weak",
            )
            continue
        # #6622: a RECORD is not a WORKER — but a missing DIRECTORY is not proof
        # that the record is dead, because `mv` moves a worktree that keeps
        # working (its `.git` holds an absolute `gitdir:`) while this listing goes
        # on reporting the old path. Only git's own `locked` signal demotes; a
        # lock-free `prunable` and a bare absence keep blocking. The branch was
        # checked first (above), so a live BRANCH that names this issue still
        # blocks even when the worktree record is dead (#3611).
        live, basis = _worktree_liveness(
            path, block.get("locked"), block.get("prunable"),
        )
        if not live:
            surface.add(
                label,
                f"matched issue-number ({issue}) but {basis} — a record that "
                "cannot be live work (non-blocking)",
                "weak",
            )
            continue
        if stale is not None:
            # The ref this record is held through: the branch when it has a usable name,
            # else the HEAD sha (a detached checkout — `_local_worktrees_for` matches it
            # by sha, and `rev-list origin/main...<sha>` is well defined).
            rev = branch if (branch and branch != "(detached)") else (block.get("head") or "")
            measured = counts(rev) if (counts is not None and rev) else None  # type: ignore[operator]
            # #6108 (reopened 2026-10-24): the SAME EMPTY-CLAIM leg as the branch surface,
            # because a worktree record and a ref naming the issue are ONE claim seen from
            # two angles — demoting the branch alone would leave the issue undispatchable
            # through this row. `judge_empty` is scoped to `refs/heads/`, so a DETACHED
            # record (its `rev` is a sha) is never demoted here: no branch, no claim to
            # prove empty.
            #
            # ⛔ AND IT RUNS ONLY ON A BRANCH THAT NAMES THIS ISSUE (`branch_hit`), which is
            # what keeps #3611 intact. A record reached by its PATH alone — a generated
            # `mergify/…`/CI branch, or a branch that names something else — is not a claim
            # this rule may judge: the BRANCH surface never sees a generated ref at all
            # (`_is_generated_branch` skips it before matching, and the module promises a
            # bot-minted name is not evidence about the PATH either), so demoting one here
            # would make the two surfaces disagree about the same ref in the fail-OPEN
            # direction. A path-only hit keeps BLOCKING exactly as it did before.
            unreadable = False
            if branch_hit:
                empty, empty_basis, unreadable = stale.judge_empty(rev or label, measured)
                if empty:
                    surface.add(
                        label,
                        f"matched issue-number ({issue}) ({basis}) — ADVISORY, not "
                        f"in-flight work: {empty_basis} — reported so it can be cleaned "
                        "up, NEVER dropped",
                        "weak",
                    )
                    continue
                if unreadable:
                    surface.add(
                        label,
                        f"matched issue-number ({issue}) ({basis}) — UNREADABLE "
                        f"{empty_basis} — NOT treated as an empty claim (fail-closed)",
                        "strong",
                    )
                    continue
            age = tip_age(rev) if (tip_age is not None and rev) else None  # type: ignore[operator]
            abandoned, why = stale.judge(rev or label, block.get("head"), measured, age)
            if abandoned:
                surface.add(
                    label,
                    f"matched issue-number ({issue}) ({basis}) — ADVISORY, not "
                    f"in-flight work: {why} — reported so it can be cleaned up, "
                    "NEVER dropped",
                    "weak",
                )
                continue
        surface.add(label, f"matched issue-number ({issue}) ({basis})", "strong")


# ── GitHub surfaces ──────────────────────────────────────────────────────────

def _require_issue_payload(data: dict, where: str) -> None:
    """The issue payload's own contract: `assignees` must be present, must be a
    list, and must hold OBJECTS.

    ⛔ THE VALIDATED SET IS THE READER SET (#8175). `gh issue view` is asked for
    `number,title,state,stateReason,assignees,url` and NOTHING ELSE: the claim
    gate no longer reads comment bodies, so `comments` is neither fetched nor
    validated. A field no reader touches needs no contract — and a contract over
    a field that is never read is dead weight that reads as a live check.

    ⛔ TWO FAILURE MODES IN OPPOSITE DIRECTIONS, from the same omission.
    `gh issue view --json …,assignees` ALWAYS returns the key (as an array,
    often empty), so:

      * a MISSING key is a malformed payload, and reading it as "no assignees"
        DROPS a different-account assignee — the fail-OPEN direction, because
        absence looks exactly like emptiness. Same reasoning that already makes
        an absent `closingIssuesReferences` INCOMPLETE rather than "closes
        nothing".
      * a non-object ELEMENT (`[null]`) reached `.get` and raised
        `AttributeError`, which is neither `SurfaceError` nor `RuntimeError` — so
        it escaped as a TRACEBACK with no `VERDICT` line and exit 1, the code
        this module documents as COLLISION. `_require_pr_dicts` closed that for
        the PR lists; this closes it for the issue surface, and additionally
        rejects a non-STRING `login`, which crashed inside
        `assignee_attribution`'s `.strip()`.

    Raising `SurfaceError` routes every case through the existing handler, which
    NAMES the reason and marks this surface INCOMPLETE.
    """
    key = "assignees"
    if key not in data:
        raise SurfaceError(
            f"{where} has no {key!r} key — `gh issue view --json` always "
            "returns it, so absence means a malformed payload, not an empty "
            "list; reading it as empty would DROP an assignee (NOT clean)"
        )
    value = data[key]
    if not isinstance(value, list):
        raise SurfaceError(
            f"{where} has a non-list {key!r} ({type(value).__name__}) — "
            "refusing (NOT clean)"
        )
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise SurfaceError(
                f"{where} has a non-object element in {key!r} at index "
                f"{index} ({type(item).__name__}) — refusing to read a "
                "malformed issue payload as if it were empty (NOT clean)"
            )
    for index, assignee in enumerate(data["assignees"]):
        login = assignee.get("login")
        if login is not None and not isinstance(login, str):
            raise SurfaceError(
                f"{where} has a non-string assignee login at index {index} "
                f"({type(login).__name__}) — refusing (NOT clean)"
            )
    # Top-level scalars. `gh` types `title`/`state`/`url` as STRING and `number`
    # as an integer, so anything else is a malformed payload. They are checked
    # because the contract describes the PAYLOAD: a payload that does not match
    # its documented shape is not one whose remaining fields can be trusted. It
    # is deliberately NOT justified by which of them some function reads — that
    # is a claim about the whole call graph, and it goes stale on the next edit.
    for key in ("title", "state", "url"):
        if key not in data:
            raise SurfaceError(
                f"{where} has no {key!r} key — `gh issue view --json` always "
                "returns it, so absence means a malformed payload (NOT clean)"
            )
        value = data[key]
        if not isinstance(value, str):
            raise SurfaceError(
                f"{where} has a non-string {key!r} ({type(value).__name__}) — "
                "refusing (NOT clean)"
            )
    # `number` is an INTEGER on the wire, not a string, so it is checked with the
    # shared predicate (which also rejects `bool`) rather than against `str`.
    if "number" not in data:
        raise SurfaceError(
            f"{where} has no 'number' key — `gh issue view --json` always "
            "returns it, so absence means a malformed payload (NOT clean)"
        )
    if not _is_issue_number(data["number"]):
        raise SurfaceError(
            f"{where} has a non-integer 'number' "
            f"({type(data['number']).__name__}) — refusing (NOT clean)"
        )


def _require_pr_dicts(prs: list, where: str, required: tuple = ()) -> None:
    """Every element of a PR list must be a JSON object.

    ⛔ THE CONTAINER WAS TYPE-CHECKED AND ITS ELEMENTS WERE NOT. A list holding a
    non-object (`[null]` — a proxy or stub, a truncated write, a future API
    shape) reached `pr.get("title")` and raised `AttributeError`. That is neither
    `SurfaceError` nor `RuntimeError`, so it escaped as a TRACEBACK with NO
    `VERDICT` line and exit 1 — the code this module documents as COLLISION. The
    direction is fail-closed, but exit 1 is read by callers as "another lane is
    on it", so the run would be mistaken for contention and the lane waited for
    would never appear. Raise `SurfaceError` instead: the surrounding handler
    turns it into a named INCOMPLETE on the surface that failed.
    """
    for index, pr in enumerate(prs):
        if not isinstance(pr, dict):
            raise SurfaceError(
                f"{where} returned a non-object element at index {index} "
                f"({type(pr).__name__}) — refusing to read a malformed PR list "
                "as if it were empty (NOT clean)"
            )
        # Checking only that the element was an object left the string fields it
        # carries to raise inside whatever read them — a traceback with no
        # VERDICT line and exit 1, which callers read as COLLISION.
        #
        # WHAT IS REQUIRED IS DECIDED BY ABSENCE LOSING INFORMATION, and the
        # decisive case is not obvious: a field that reads as the empty string
        # turns into "no problem here" on a surface that can BLOCK. A required
        # field whose absence only removes an ADVISORY demotion is not the same
        # thing, and requiring it would trade a real refusal for a cosmetic one.
        #
        # Two classes sit behind that rule:
        #   * the fields every payload of this shape carries, checked here;
        #   * the fields only ONE transport projects, which the caller names in
        #     `required` — a field the other transport never sends cannot be
        #     required of it.
        for key in ("title", "headRefName", "body", "mergedAt"):
            if key not in pr:
                raise SurfaceError(
                    f"{where} returned an element with no {key!r} at index "
                    f"{index} — absence reads as empty, and a reader consumes it "
                    "on a BLOCKING surface, so a missing key would silently drop "
                    "that signal (NOT clean)"
                )
        # ⛔ `number` is the field the hit test compares against the issue. It is
        # an INTEGER on the wire, and `_is_issue_number` also rejects `bool`
        # (`isinstance(True, int)` is True).
        if "number" not in pr:
            raise SurfaceError(
                f"{where} returned an element with no 'number' at index {index} "
                "— refusing a malformed PR list (NOT clean)"
            )
        if not _is_issue_number(pr["number"]):
            raise SurfaceError(
                f"{where} returned a non-integer 'number' at index {index} "
                f"({type(pr['number']).__name__}) — refusing (NOT clean)"
            )
        # ⛔ `state` is REQUIRED, STRING, AND NON-BLANK. The blank case is the
        # fail-OPEN one: `_pr_terminal_state` strips it, `""` is not in
        # ("open", "opened"), and a truthy `mergedAt` then marks a LIVE PR
        # terminal — every match on the BLOCKING open-PR surface goes out as
        # `weak` and the run reports CLEAN. A blank `state` is not a value either
        # transport produces, so it is a malformed payload, not a state.
        if "state" not in pr:
            raise SurfaceError(
                f"{where} returned an element with no 'state' at index {index} — "
                "refusing a malformed PR list (NOT clean)"
            )
        if not isinstance(pr["state"], str) or not pr["state"].strip():
            raise SurfaceError(
                f"{where} returned a blank or non-string 'state' at index "
                f"{index} ({pr['state']!r}) — a blank state is not a state: it "
                "drops the liveness short-circuit and demotes a live PR to "
                "immutable history (NOT clean)"
            )
        for key in ("merged_at", *required):
            if key not in pr and key in required:
                raise SurfaceError(
                    f"{where} returned an element with no {key!r} at index "
                    f"{index} — this transport projects it, so its absence means "
                    "the payload is malformed, not that the value is empty "
                    "(NOT clean)"
                )
        # The TYPE half. `title`/`headRefName` are in it as well as the presence
        # loop above: `isinstance(5, str)` is False and the value would reach
        # `_one_line(...)`/`re.search`, so presence alone is not the check.
        for key in ("title", "headRefName", "body", "mergedAt", "merged_at",
                    *required):
            if key in pr and pr[key] is not None and not isinstance(pr[key], str):
                raise SurfaceError(
                    f"{where} returned a non-string {key!r} at index {index} "
                    f"({type(pr[key]).__name__}) — refusing (NOT clean)"
                )


#: How much of a failing `gh` stderr a NOTE carries. Wider than the 200-char
#: DISPLAY cut in `_gh_json` because a note is the text the REMEDY is derived
#: from (`_incomplete_remedy` classifies `Surface.note` with
#: `_is_budget_exhaustion`), and a classifier that cannot see the signature
#: answers "not an exhaustion" about an exhaustion — which is how a long stderr
#: turned a spent budget into the `gh auth/network` remedy (#9402 review, P1).
#: A note is prose, not a terminal row, so it can be wider.
_REFUSED_READ_NOTE_LIMIT = 600


def _gh_json(gh_bin: str, args: list[str], repo: str, timeout: float):
    rc, out, err, timed_out = _run([gh_bin, *args], repo, timeout)
    if rc != 0:
        why = "timeout" if timed_out else f"exit {rc}"
        # ⛔ THE STDERR IS CUT FOR DISPLAY, `detail` IS NOT (#9402 review, P1).
        # Past 200 characters of `gh` chatter the failure signature is gone from
        # the message, and `_is_budget_exhaustion` used to read only the message
        # — so a verbose/retrying run looked like a broken auth instead of a
        # spent budget. Carrying the raw text keeps ONE predicate that can
        # actually see what it is testing for, at any stderr length.
        raise SurfaceError(
            f"gh {' '.join(args)} failed ({why}): {_one_line(err or out)}",
            detail=f"{why}: {err or out}",
        )
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise SurfaceError(
            f"gh {' '.join(args)} returned non-JSON: {exc}: {_one_line(out)}",
            detail=f"non-JSON: {exc}: {out}",
        ) from exc


def _issue_view_rest(gh_bin: str, slug: str, issue: int, cwd: str,
                     timeout: float) -> tuple[dict | None, str | None]:
    """Read issue metadata over REST when the GraphQL budget is exhausted.

    `gh issue view` is GraphQL-ONLY. When that budget runs out — which happens
    routinely on this fleet, and is what made this tool report three unqueryable
    surfaces for #3435 while `gh api` answered the identical question in the very
    next call — EVERY pre-flight reports INCOMPLETE. That is the instrument
    holding the datum and refusing to read it: it fails closed on a condition it
    could have measured, rather than on one it could not.

    REST has a SEPARATE budget, so one fallback turns that false INCOMPLETE back
    into a real answer.

    Returns ``(payload, None)`` on success and ``(None, reason)`` when REST could
    not answer or answered with a body that fails the issue-payload contract, so
    the caller's fail-closed classification is unchanged: this can only ever
    convert a spurious failure into a reading, never a failure into a pass.

    ⛔ THE CONTRACT IS ENFORCED HERE, NOT BY THE CALLER (#3435 review, P1). This
    runs INSIDE the caller's `except SurfaceError` handler, so a raise from it is
    caught by the handler it lives in — it escaped as a TRACEBACK with no
    `VERDICT` line and exited 1, the code this module documents as COLLISION. A
    malformed body must be a NAMED INCOMPLETE, and the only way to guarantee that
    for every future caller is to return the reason rather than raise it.

    The shape is normalised to what `gh issue view --json` returns, because the
    downstream code reads exactly those keys and compares `state`
    case-insensitively (REST says 'open', GraphQL says 'OPEN'). Every key is
    carried through RAW — including a `null`/absent `assignees` — so the contract
    check sees the body's real shape instead of a pre-digested copy.

    ⛔ IT MUST ANSWER ABOUT THE ISSUE IT WAS ASKED ABOUT (#3435 review, P2).
    `GET /repos/{slug}/issues/{N}` is the ISSUE-OR-PULL-REQUEST endpoint: for a
    PR number it returns a PULL REQUEST object, which carries every key this
    normaliser reads and therefore passes the payload contract — a PR read as an
    issue, with the transport deciding the answer (`gh issue view` refuses that
    input). A misrouted or differently-numbered body would likewise be read as
    this issue's state and assignees, and an `OPEN` with no assignees reads FREE:
    the fail-open direction. Both are refused here, for the same reason the
    contract is: the reader must see the datum the question asked for.
    """
    where = f"gh api repos/{slug}/issues/{issue}"
    try:
        raw = _gh_json(gh_bin, ["api", f"repos/{slug}/issues/{issue}"], cwd, timeout)
    except SurfaceError as exc:
        return None, str(exc)
    except ValueError as exc:
        # ⛔ `_run` decodes with `text=True`, i.e. STRICT: one non-UTF-8 byte in
        # the body raises `UnicodeDecodeError`, which is a ValueError — NOT a
        # SurfaceError — so it passed this handler, the caller's `except
        # SurfaceError`, and `main()`'s NotAWorkItem/RuntimeError pair, and
        # escaped as a traceback with no VERDICT line and exit 1 (the code this
        # module documents as COLLISION). Refusing rather than repairing is
        # deliberate: replacement characters would compose a slug/payload that
        # no endpoint sent. (The same hole exists on the pre-existing primary
        # read; closing it in `_run` changes git-output handling globally and is
        # tracked separately — tortoise#8881.)
        return None, (
            f"{where} returned output that is not decodable as text ({exc}) — "
            "refusing (NOT clean)"
        )
    if not isinstance(raw, dict):
        return None, f"{where} returned non-object JSON ({type(raw).__name__})"
    if "pull_request" in raw:
        return None, (
            f"{where} is a PULL REQUEST, not an issue — refusing to read it as a "
            "work item (NOT clean)"
        )
    if raw.get("number") != issue:
        return None, (
            f"{where} answered for #{raw.get('number')!r}, not #{issue} — refusing "
            "a body that is not about the issue that was asked (NOT clean)"
        )
    payload = {
        "number": raw.get("number"),
        "title": raw.get("title"),
        "state": raw.get("state"),
        "stateReason": raw.get("state_reason"),
        "assignees": raw.get("assignees"),
        "url": raw.get("html_url"),
    }
    try:
        _require_issue_payload(payload, where)
    except SurfaceError as bad:
        return None, str(bad)
    return payload, None


def _is_budget_exhaustion(failure: str | BaseException) -> bool:
    """Is this gh failure a BUDGET EXHAUSTION (which REST may be able to answer)?

    The ONE test every rescue path and the report's remedy share, because the two
    must agree: a rescue gated on a signature the remedy does not recognise
    prints a remedy for a cause it already knew was not the cause — the exact
    defect #9402 files ("the printed remedy is neither cause").

    ONLY a budget exhaustion is eligible for a REST rescue. Any other failure is
    a genuine "could not read", and rescuing it from a second endpoint would
    turn a real refusal into a reading — the fail-open direction. Match the
    PHENOMENON, not the exception type.

    gh renders a non-2xx as ``HTTP <code>: <status> (<url>)``, so a 429 arrives
    as ``HTTP 429: Too Many Requests`` with NO prose at all — a bare
    "rate limit" substring test silently under-triggers on the canonical exhaust
    form. The GraphQL form (``GraphQL: API rate limit already exceeded for user
    ID …``) is what `gh pr list` / `gh issue view` actually emit once the
    fleet-shared user-level budget is gone (#9402), and note that the PRIMARY
    ``rate_limit.resources.graphql`` counter still reads thousands remaining when
    it fires — so the counter can never be the test.

    ⛔ IT TESTS THE **UNTRUNCATED** TEXT WHEN THERE IS ONE (#9402 review, P1).
    Accepting a `SurfaceError` (or its message) is not enough on its own: the
    message embeds a 200-character one-lining of `gh`'s stderr, so a long
    stderr can push the signature past the cut — and the predicate then answered
    "not an exhaustion" about an exhaustion. `SurfaceError.detail` carries the
    raw text for exactly this read; a plain string is classified as given.
    """
    raw = failure if isinstance(failure, str) else (
        getattr(failure, "detail", "") or str(failure)
    )
    lowered = str(raw).lower()
    return (
        "rate limit" in lowered
        or "too many requests" in lowered
        or "http 429" in lowered
    )


def _rest_refusal_note(rest_why: str, graphql: BaseException) -> str:
    """Compose ``<REST reason> (GraphQL read: …)`` from the UNTRUNCATED cause.

    ⛔ THIS STRING BECOMES THE SURFACE NOTE, AND THE NOTE IS WHAT THE REMEDY IS
    DERIVED FROM (#9423 review, P3). A failed rescue is reported by the
    per-surface handler as ``gh-unavailable: {exc}``, and `_incomplete_remedy`
    classifies that note with `_is_budget_exhaustion`. Composing it from
    `str(graphql)` — the 200-character DISPLAY message `_gh_json` builds —
    reproduced #9402 on the ONE branch that did not carry the cause forward: an
    exhaustion whose `gh` stderr chattered past 200 characters PLUS a REST
    request that refused for its own reason produced a note with no signature,
    the surface was therefore not classified as exhausted, and the run ended
    with `fix gh auth/network (or rebuild the fleet board) and re-run` for a
    budget the rescue had just matched on.

    So the raw text comes from `SurfaceError.detail` (see its docstring) at the
    SAME wider cut the successful-rescue note uses — a note is prose the remedy
    is derived from, not a terminal row (see `_REFUSED_READ_NOTE_LIMIT`).
    """
    raw = getattr(graphql, "detail", "") or str(graphql)
    return _one_line(
        f"{rest_why} (GraphQL read: {raw})", limit=_REFUSED_READ_NOTE_LIMIT
    )


#: GitHub's REST maximum page size, and therefore the widest page a single
#: `/pulls` request can return (see `_open_prs_rest`).
_REST_PULLS_PAGE = 100


def _rest_pulls_page_size(limit: int) -> int:
    """The `per_page` the REST open-PR rescue actually requests (#9402 review).

    ⛔ ONE FORMULA, TWO READERS — and that is the point. The request sends
    `min(_REST_PULLS_PAGE, max(1, limit))`, while the truncation note has to tell
    a human WHICH page size came back full. When the note recomputed its own
    copy of the expression (or, worse, hardcoded GitHub's 100) the two could
    disagree, and the note then prints a false statement about the very request
    it is describing — a refusal whose stated reason is not what caused it. So
    the size is computed once, here, and the caller interpolates it.

    `--pr-limit` is accepted at any value `>= 1` (`main()` refuses `< 1`, so
    the `max(1, …)` here is belt-and-braces rather than reachable), and values
    BELOW `_REST_PULLS_PAGE` are legal and documented — so the requested page is
    genuinely smaller than GitHub's maximum at a normal setting, and the caller
    must say so rather than call every request "the maximum".
    """
    return min(_REST_PULLS_PAGE, max(1, int(limit)))


def _rest_pr_wire_reason(element: object, index: int, where: str) -> str | None:
    """Why one REST `/pulls` ELEMENT cannot be read — or ``None`` when it can.

    ⛔ THE WIRE ELEMENT IS VALIDATED BEFORE ANYTHING READS IT, AND NOTHING IS
    SYNTHESISED FROM ABSENCE. `_open_prs_rest` builds the `gh pr list --json`
    projection itself and then runs `_require_pr_dicts` on the records it just
    built — so that contract never saw the WIRE. Two defects followed, and both
    are the class this file already closed elsewhere:

    * a non-object element (`[null]`, `[1]`, `["x"]`) reached
      `element.get("head")` and raised `AttributeError`. That is neither
      `SurfaceError` nor `RuntimeError`, and the rescue is called from INSIDE an
      `except SurfaceError` body, so it escaped every handler as a TRACEBACK
      with no `VERDICT` line and exit 1 — the code this module documents as
      COLLISION, which callers read as "another lane is on it, do not
      dispatch". Exactly the shape `_require_pr_dicts` and `_issue_view_rest`
      were written for;
    * every key was FABRICATED (`element.get("title") or ""`, `head.get("ref")
      or ""`), so an ABSENT key became EMPTINESS and the presence checks in
      `_require_pr_dicts` could never fire for this transport. A `/pulls` body
      whose element lacks `title` dropped the title test — on the BLOCKING
      surface — without saying so. Absence looks exactly like emptiness, which
      is the fail-OPEN direction. `body` is on the same list and is the sharpest
      case: on this transport the body regex is the ONLY closing-reference test
      left, so an absent `body` dropped the one test that can still refuse.
      `"body": null` (a PR with no description) is a real VALUE and stays
      accepted — only the missing KEY is refused.

    Returning the REASON (rather than raising) keeps the refusal a NAMED
    INCOMPLETE: the caller turns a `None` answer into a `SurfaceError` that names
    both this cause and the GraphQL read that sent us here.
    """
    if not isinstance(element, dict):
        return (
            f"{where} returned a non-object element at index {index} "
            f"({type(element).__name__}) — refusing to read a malformed PR list "
            "as if it were empty (NOT clean)"
        )
    # The scalar keys every `/pulls` element carries and a blocking reader
    # consumes. ⛔ `body` IS ON THIS LIST (#9423 review), and the reason is the
    # one the closed-PR branch states: REST sends `"body": null` for an empty
    # description, so NULL is a real wire VALUE — but a MISSING KEY is a
    # malformed payload, and it is not interchangeable with null. On the
    # REST-rescued BLOCKING surface the body regex is the ONLY closing-reference
    # test there is, so reading an absent `body` as "" would silently drop the
    # one test that can still refuse. (Null stays accepted: emptiness is what
    # GitHub says about a PR with no description.)
    for key in ("number", "state", "title", "body"):
        if key not in element:
            return (
                f"{where} returned an element with no {key!r} at index "
                f"{index} — REST `/pulls` always sends number, state, title, "
                "body and head.ref, so this is a MALFORMED payload, not an "
                "empty value; filling it in would silently drop that test on a "
                "BLOCKING surface (NOT clean)"
            )
    # ⛔ `state` IS TYPED HERE, AND IT HAS TO BE (#9423 review, P3). The
    # projection coerces it (`str(element.get("state") or "").upper()`) so the
    # dedicated readers get `gh pr list --json`'s GraphQL casing — and that
    # coercion made `_require_pr_dicts`' `isinstance(pr["state"], str)` refusal
    # UNREACHABLE on this transport: a truthy non-string wire `state` (`123`,
    # `["open"]`) became the string `"[OPEN]"`, so a malformed element was READ
    # as a live, non-terminal PR instead of being refused, and the documented
    # "blank or non-string state is a REFUSAL" contract held on the GraphQL path
    # only. The direction here is the safe one (non-terminal ⇒ strong hits ⇒
    # COLLISION, never CLEAN), which is why it is a P3 and not a fail-open — but a
    # contract that silently does not apply to one of two transports is how the
    # two reads start disagreeing.
    if not isinstance(element.get("state"), str):
        return (
            f"{where} returned an element with a non-string 'state' at index "
            f"{index} ({type(element.get('state')).__name__}) — a coerced state "
            "would be read as a live PR instead of being refused, and `state` "
            "is what decides whether a hit is terminal (NOT clean)"
        )
    # ⛔ `title` / `body` ARE TYPED HERE TOO, AND FOR THE SAME REASON AS `state`
    # (#9423 review round 6, P3). The projection writes
    # `element.get("title") or ""` / `element.get("body") or ""`, so a FALSY
    # non-string wire value (`0`, `false`, `[]`, `{}`, and `title: null`) was
    # laundered into `""` BEFORE `_require_pr_dicts`' `isinstance(..., str)` loop
    # could see it — the "non-string title/body is a REFUSAL" contract therefore
    # held for TRUTHY non-strings only, i.e. on one of the two transports. Not a
    # verdict-level fail-open (title/body only ever ADD hits, and the rescued
    # surface is forced INCOMPLETE regardless), but it is the identical
    # laundering that `state` was just fixed for, and a contract that applies to
    # half of one transport's values is how the reads start disagreeing.
    #
    # `body` is the ONE key where `null` is a real wire VALUE (a PR with no
    # description), so it is accepted and only a non-string, non-null body is
    # refused. A `null` title is refused: GitHub never sends one.
    if not isinstance(element.get("title"), str):
        return (
            f"{where} returned an element with a non-string 'title' at index "
            f"{index} ({type(element.get('title')).__name__}) — coercing it to "
            "'' would silently drop the title test on a BLOCKING surface "
            "(NOT clean)"
        )
    if element.get("body") is not None and not isinstance(element.get("body"), str):
        return (
            f"{where} returned an element with a non-string 'body' at index "
            f"{index} ({type(element.get('body')).__name__}) — coercing it to "
            "'' would silently drop the body's closing-reference test on a "
            "BLOCKING surface (NOT clean)"
        )
    head = element.get("head")
    if not isinstance(head, dict):
        return (
            f"{where} returned an element with a non-object 'head' at index "
            f"{index} ({type(head).__name__}) — `head.ref` is the ONLY input to "
            "the head-ref test, so refusing rather than reading it as empty "
            "(NOT clean)"
        )
    if not isinstance(head.get("ref"), str):
        return (
            f"{where} returned an element with no string 'head.ref' at index "
            f"{index} ({type(head.get('ref')).__name__}) — the head-ref test "
            "would silently be skipped on a BLOCKING surface (NOT clean)"
        )
    return None


def _open_prs_rest(gh_bin: str, slug: str, cwd: str, timeout: float,
                   limit: int) -> tuple[list[dict] | None, bool, str | None]:
    """Enumerate OPEN pull requests over REST when GraphQL refuses (#9402).

    Returns ``(prs, possibly_truncated, reason)``: ``(prs, …)`` on success and
    ``(None, False, reason)`` when REST could not answer, so the caller's
    fail-closed classification is unchanged — this can only ever convert a
    spurious failure into a READING, never a failure into a pass (the same
    contract `_issue_view_rest` carries for issue metadata, #3435).

    WHY THIS EXISTS. `gh pr list --json closingIssuesReferences` is GraphQL-only,
    and the GraphQL budget is ONE bucket shared by the whole fleet; when it is
    spent, EVERY candidate reads exit 2 and no lane can pass the mandated
    dispatch gate. REST has a SEPARATE, far larger budget, so the enumeration
    itself — plus every test that reads only fields REST carries (title, head
    ref, number, state) — can still be answered.

    ⛔ IT CANNOT ANSWER THE CLOSING-REFERENCE TEST, AND DOES NOT PRETEND TO.
    `closingIssuesReferences` is a GraphQL-computed field (#3504: "the SOURCE,
    not a re-derivation") with NO REST equivalent — the REST `/pulls` payload
    simply has no such key. So the records returned here DELIBERATELY omit it,
    and the caller marks the surface INCOMPLETE with a named reason rather than
    letting the scan read absence as "closes nothing". Rescuing the enumeration
    must never be mistaken for having proved the negative.

    `possibly_truncated` is decided by EVIDENCE: the request asks for the page
    size in `_rest_pulls_page_size(limit)` — `limit` capped at GitHub's maximum
    of `_REST_PULLS_PAGE`, so it is the MAXIMUM only when `--pr-limit` is at
    least that — and a full page cannot prove the list is complete. It is the
    REST counterpart of the GraphQL path's `limit + 1` fetch, and it keeps a
    full page out of "complete" exactly as that does. The caller's truncation
    note interpolates the SAME helper, so the note can never describe a page
    size the request did not ask for.
    """
    where = f"gh api repos/{slug}/pulls?state=open"
    per_page = _rest_pulls_page_size(limit)
    try:
        raw = _gh_json(
            gh_bin,
            ["api", f"repos/{slug}/pulls?state=open&per_page={per_page}"],
            cwd, timeout,
        )
    except SurfaceError as exc:
        return None, False, str(exc)
    except ValueError as exc:
        # `_run` decodes STRICTLY, so one non-UTF-8 byte raises
        # `UnicodeDecodeError` — a ValueError, not a SurfaceError — which would
        # escape this handler and the caller's, and surface as a traceback with
        # no VERDICT line and exit 1 (the code this module calls COLLISION).
        # Refusing rather than repairing is deliberate: replacement characters
        # would compose a payload no endpoint sent.
        return None, False, (
            f"{where} returned output that is not decodable as text ({exc}) — "
            "refusing (NOT clean)"
        )
    if not isinstance(raw, list):
        return None, False, (
            f"{where} returned non-list JSON ({type(raw).__name__}) — refusing "
            "to read a malformed PR list as if it were empty (NOT clean)"
        )
    prs: list[dict] = []
    for index, element in enumerate(raw):
        # ⛔ THE WIRE ELEMENT IS CHECKED BEFORE THE NORMALISATION BELOW READS IT.
        # See `_rest_pr_wire_reason`: without this the element-type contract runs
        # on records this loop built, so `[null]` is an `AttributeError` (a
        # traceback with no VERDICT line, exit 1 = COLLISION) and an absent
        # `title`/`head.ref` is silently fabricated into "" — a dropped test on a
        # BLOCKING surface.
        reason = _rest_pr_wire_reason(element, index, where)
        if reason is not None:
            return None, False, reason
        head = element["head"]
        # REST spells three of these differently from the `gh pr list --json`
        # projection (`head.ref`, `html_url`, `merged_at`), and `state` in
        # lowercase. Normalising HERE keeps every downstream reader —
        # `_pr_terminal_state`, `scan_pr_surface`, the merged-head harvest — on
        # ONE shape, so a rescue cannot be read differently from a primary read.
        #
        # ⛔ TWO CHECKS, TWO DIFFERENT CONTRACTS — NEITHER ONE IS THE OTHER.
        # `_rest_pr_wire_reason` (above) enforces what the WIRE must send, so no
        # element is dereferenced before it is typed and no absent key is filled
        # in. `_require_pr_dicts` (below) then enforces the `gh pr list --json`
        # PROJECTION's contract, which is what every downstream reader consumes.
        # Running the projection's check on the RAW elements instead was the
        # first bug here (found by
        # `test_open_pr_graphql_exhaustion_is_enumerated_over_rest`, whose first
        # run refused every REST element for a missing `headRefName`): REST
        # deliberately does not carry the projection's spellings, so that turns
        # every rescue into a refusal — fail-closed, but a gate that can never
        # answer.
        prs.append({
            "number": element.get("number"),
            "title": element["title"] or "",
            "body": element.get("body") or "",
            "headRefName": head["ref"] or "",
            "state": str(element.get("state") or "").upper(),
            "url": element.get("html_url") or element.get("url") or "",
            "mergedAt": element.get("merged_at"),
            # ⛔ NOT SET — see the docstring. Absence here is MEASURED absence of
            # the source, and the caller says so; it is never "closes nothing".
        })
    try:
        _require_pr_dicts(prs, f"the REST open-PR enumeration for {slug}")
    except SurfaceError as exc:
        # A contract violation in a record this loop BUILT (a non-integer
        # `number`, a blank `state`) is the same class as the wire malformations
        # above: name it and return, instead of letting it escape through the
        # caller's `except SurfaceError` body as a traceback with no VERDICT line.
        return None, False, str(exc)
    return prs, len(raw) >= per_page, None


# An RFC-8288 link relation may be QUOTED or UNQUOTED (`rel="last"` / `rel=last`),
# and a response may carry MORE THAN ONE `Link:` header line. Both forms are
# matched here, and the caller joins every header line before parsing, because a
# header this pattern cannot read must never be mistaken for "no header" — that
# is the sample-presented-as-everything failure this file exists to prevent.
#
# The trailing guard is `(?![\w-])`, NOT `\b`: after a QUOTED relation the next
# character is a quote or a space, both non-word, so `\b` can never match there
# and the pattern would silently fail on exactly the form GitHub sends.
_LINK_LAST_RE = re.compile(r'[?&]page=(\d+)>;\s*rel=(?:"last"|last)(?![\w-])')
_LINK_NEXT_RE = re.compile(r'rel=(?:"next"|next)(?![\w-])')


def _closed_pr_sample(gh_bin: str, slug: str | None, cwd: str,
                      timeout: float,
                      sample: int) -> tuple[list[dict], int | None, bool]:
    """Fetch the closed-PR surface in ONE bounded request (#5251).

    Returns ``(prs, approx_total, partial)``. ``approx_total`` comes from the
    response's OWN ``Link: rel="last"`` header, so completeness is observable on
    the same call that fetches the sample: the caller reports "100 of ~1,848"
    instead of silently presenting 100 as everything.

    ``partial`` is decided by EVIDENCE, never by whether a parse succeeded: it is
    True when more pages provably exist (a ``rel="next"``, or a ``rel="last"``
    total larger than the page). It is False when no ``Link`` header is present
    at all, OR when the advertised last page contains the whole list — so a
    ``Link`` header CAN be present on a ``partial=False`` response, and "no Link
    header" is not the only way to reach completeness. Any ``Link`` header the
    parser cannot read is treated as partial with a floor total rather than as
    completeness.

    ``--paginate`` is deliberately absent. Following the ``rel="next"`` chain
    to exhaustion cost ~19 requests on this repo (measured ``Link: rel="last"``
    = page 19 of 1,848 at ``per_page=100``; ~93 at ``per_page=20``) to compute a
    signal that, since #5129, cannot reach the verdict. That cost lands on the
    SECONDARY rate limiter, which GitHub documents as unobservable and which
    many lanes therefore blow COLLECTIVELY — how one lane's run failed and
    halted its dispatch. One page plus its header's total is strictly more
    information per request than N pages were.

    The path is built from the RESOLVED ``owner/name`` literally. It does NOT
    use gh's ``{owner}/{repo}`` placeholders: those resolve from the CURRENT
    DIRECTORY, which is exactly how the cross-repo false CLEAN of #4027
    happened (``gh api`` has no ``--repo`` flag).
    """
    if not slug:
        raise SurfaceError(
            "target-repo-unresolved: no owner/name for the target repo — "
            "refusing to query the closed-PR surface against an unknown repo"
        )
    # GitHub caps `per_page` at 100 and SILENTLY returns 100 for a larger value
    # while the `Link` header echoes the value that was REQUESTED. `per_page=5000`
    # therefore yields 100 rows and a `rel="last"` of page 19, so `19 * 5000`
    # would report ~95,000 closed PRs on a repo with 1,854. Clamp to the real
    # maximum so the estimate can never be fabricated out of an unhonoured
    # parameter. (This also keeps `--closed-pr-limit`, whose documented default
    # was once 5000, from silently meaning something other than it says.)
    page_size = max(1, min(int(sample), 100))
    args = [
        "api", "-i",
        f"repos/{slug}/pulls?state=closed&per_page={page_size}",
        "--jq",
        "map({number, title, body, state, url, "
        "headRefName: .head.ref, headSha: (.head.sha // \"\"), "
        "mergedAt: .merged_at})",
    ]
    rc, out, err, timed_out = _run([gh_bin, *args], cwd, timeout)
    if rc != 0:
        why = "timeout" if timed_out else f"exit {rc}"
        raise SurfaceError(f"gh {' '.join(args)} failed ({why}): {_one_line(err or out)}")
    # `-i` prints the status line and headers, then a blank line, then the body.
    # The wire separator is CRLF; LF is accepted too because a proxy or a future
    # gh may normalise it. If neither is found the header/body split is
    # ambiguous, so the surface fails loudly rather than parsing a guess.
    head, sep, body = out.partition("\r\n\r\n")
    if not sep:
        head, sep, body = out.partition("\n\n")
    if not sep:
        raise SurfaceError(
            "gh api -i produced no header/body separator — cannot derive the "
            "closed-PR total, so the sample's completeness is unknowable; "
            "refusing to present a partial list as complete"
        )
    try:
        prs = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SurfaceError(
            f"gh {' '.join(args)} returned a non-JSON body: {exc}: {_one_line(body)}"
        ) from exc
    if not isinstance(prs, list):
        raise SurfaceError(
            f"gh {' '.join(args)} returned a non-list body: {_one_line(body)}"
        )
    # The total is derived from the LAST PAGE NUMBER the response advertises —
    # NOT from `total_count`, which belongs to the search API and is unavailable
    # here (search cannot return `head.ref`, so using it would silently disable
    # this surface's branch-name leg). `last_page * page_size` is an UPPER bound
    # on the true count, so it is reported with a `~`: the surface never claims a
    # precise total it did not measure.
    #
    # `partial` is decided by EVIDENCE, not by whether the parse succeeded. A
    # `Link` carrying `rel="next"` PROVES more pages exist, so it must never be
    # reported as "this page is the complete list" — even when the `rel="last"`
    # entry is absent or unparseable. Treating a failed parse as "no header
    # present" would re-introduce exactly the failure this tool exists to
    # prevent: presenting a sample as everything.
    #
    # EVERY `Link` header line is joined before parsing (a response may send more
    # than one), and both the quoted and unquoted relation forms are accepted —
    # the tests' own stub emits two lines, so a parser that read only the first
    # would disagree with the transport it is meant to model (review cycle 2).
    link_values = " ".join(
        m.group(1).strip()
        for m in re.finditer(r'(?im)^link:\s*(.*)$', head)
    )
    last_match = _LINK_LAST_RE.search(link_values)
    has_next = _LINK_NEXT_RE.search(link_values) is not None
    if last_match:
        approx_total = int(last_match.group(1)) * page_size
        # ⛔ `has_next` IS DECISIVE, exactly as the invariant above says. Reading
        # only `last` here meant that when the two relations disagreed — `last`
        # parseable, `next` present — the page was reported as "the complete
        # list" while the response itself proved a further page existed, i.e. the
        # sample-as-everything failure this tool exists to prevent. The advisory
        # surface cannot change an exit code, so no test caught it; the human
        # reading the report is the one who was misled.
        partial = has_next or approx_total > len(prs)
    elif has_next or link_values:
        # More pages provably exist, or a Link header exists that this parser
        # could not interpret. Report partial-with-a-floor rather than claiming
        # completeness, and use a floor (never a fabricated number) so the
        # wording cannot overstate.
        approx_total = len(prs) + 1
        partial = True
    else:
        # No Link header at all: this page IS the whole list.
        approx_total = None
        partial = False
    return prs, approx_total, partial


def _pr_ref(pr: dict) -> str:
    # Both sides of the #3219 merge rewrote this from the same `%`-format
    # original into an f-string; the rendered output is byte-identical, so the
    # origin/main form is kept (fewer intermediates, matches the surrounding
    # post-merge code).
    return (
        f"PR #{pr.get('number', '?')} "
        f"{_one_line(pr.get('title', ''), 90)} "
        f"[{pr.get('headRefName', '')}]"
    )


def scan_pr_surface(
    surface: Surface, prs: list[dict], issue: int, identity: Identity,
    use_closing_field: bool,
) -> None:
    """PR surface matching.

    ⛔ ORDER IS LOAD-BEARING, but the number==issue case is NO LONGER a
    first-decided *weak hit*: an OPEN PR whose number is the issue RAISES
    `NotAWorkItem` (#7009) — ON A NON-ADVISORY SURFACE. The refusal is scoped by
    `surface.authority`, so an ADVISORY entry whose `state` is non-terminal is
    REPORTED weak and never refused: an advisory surface cannot force an exit.
    That check runs at block 0, BEFORE the ownership
    check — it is an INPUT-VALIDITY test, not a match test, so `main()`
    auto-declaring the caller's own branch (the documented `--repo .` form)
    cannot suppress it. The caller's OWN PR is still decided before any match
    test; a TERMINAL PR whose number is the issue still takes the weak arm and
    leaves the verdict CLEAN.

    RAISES `NotAWorkItem` for an open PR number on a non-advisory surface, in
    ADDITION to `SurfaceError`.
    On the CLI path ONLY `NotAWorkItem` reaches `main()`; a `SurfaceError` is
    absorbed by the per-surface handler and never becomes this refusal. A
    PROGRAMMATIC caller of this function or of `run_preflight` must therefore
    catch `NotAWorkItem` itself.

    #4567 found this order
    inverted: the keyword test ran first and `continue`d unconditionally, so the
    self-PR suppression below it was **dead code** and `exit 0` was unreachable
    for any issue whose number is also a PR. Deleting the keyword arm removes
    that particular trap, but the ordering rule stays explicit because "test
    something before self" is the defect, not the keyword arm itself.

    STRONG hits are name-like or contractual fields: the title (this repo's
    convention is `type(scope): #N ...`), the head ref (`<type>/<N>-<slug>`), or
    a COMPUTED closing reference. The body is otherwise PROSE: a bare `#N`
    mention there is a WEAK, non-blocking signal — cross-reference prose is not
    work (live bug: "restored in #2745" on closed PR #2926, "filed as #2751" on
    PR #2754).

    A TERMINAL PR (merged or closed) cannot be in flight, so every match on one
    is immutable history: REPORTED, at `weak` strength, which `format_report`
    never counts toward the verdict (#4886, #5112, #4533).

    `use_closing_field` selects the closing-reference test. It is True on the
    BLOCKING open-PR surface, which asks gh for `closingIssuesReferences` —
    GitHub's own field, exact by construction. It is False in TWO places, and
    they are NOT the same posture:

    * the ADVISORY closed-PR surface, whose REST payload has no such field, so
      that one falls back to the regex — and every hit it can produce there is
      already `weak` (an advisory surface cannot force an exit);
    * the BLOCKING open-PR surface when its enumeration came from the REST
      rescue (#9402) — REST `/pulls` carries no `closingIssuesReferences`
      either, so the same regex fallback runs, but a NON-TERMINAL hit there is
      `strong` and decides the verdict. Every hit on this call site is therefore
      NOT already `weak`, which is why the fallback's detail text keys on
      `surface.authority` and not on this flag (a hit that refuses must not
      describe itself as advisory — #9423 review).
    """
    for pr in prs:
        title = pr.get("title") or ""
        body = pr.get("body") or ""
        head = pr.get("headRefName") or ""
        terminal = _pr_terminal_state(pr)
        suffix = "" if terminal is None else (
            f" — PR is {terminal}: immutable history, not in-flight work "
            "(non-blocking)"
        )
        # 0. INPUT VALIDITY, BEFORE THE SELF/OWNERSHIP CHECKS (#7009).
        #    The PR *is* the issue: the caller passed a PULL REQUEST number. A pull request number IS an issue number on GitHub, so this
        #    is not an exotic input error — and the verdict it produces describes
        #    the PULL REQUEST, never the work item.
        #
        #    Measured 2026-10-06: `collision_preflight.py 7477` returned CLEAN
        #    (exit 0) while #7455 — the issue that PR closes — was held on FOUR
        #    surfaces at once (open PR, local branch, remote branch, live
        #    worktree). Exit 0 is this protocol's instruction to DISPATCH, so that
        #    was a FAIL-OPEN on a blocking check: a lane is told to start work
        #    that already has a lane, a branch and a worktree. The original #7009
        #    symptom was the same input read as COLLISION (fail-CLOSED — lost
        #    throughput, nothing duplicated); once the self-match was downgraded
        #    to `weak` the polarity flipped and the surviving form duplicates
        #    work, which is the failure this whole pre-flight exists to prevent.
        #
        #    So a self-match must REFUSE, not be filed as a `weak` hit and
        #    forgotten. The refusal needs no new exit code, no new flag and no
        #    new gate: `EXIT_INCOMPLETE` is the protocol's existing "do not start
        #    on a guess" verdict, and `NotAWorkItem` reaches it without being
        #    mislabelled as a broken surface.
        #
        #    ORDERING IS LOAD-BEARING, which is why this sits at 0 and not at 2:
        #    it is an INPUT-VALIDITY test, not a match test, so the "self before
        #    match" ordering (#4567) does not apply to it. Placed AFTER the
        #    self-check it is UNREACHABLE for the one PR that matters — `main()`
        #    auto-declares the current branch for the documented `--repo .`
        #    invocation, so when the open PR belongs to the lane running the gate
        #    (exactly the #7477 shape) the self-arm matches first, files a `weak`
        #    hit and continues, and the run returns `VERDICT: CLEAN`, exit 0.
        #    Measured both ways: `--self-branch <PR head>` and auto-detect each
        #    gave RC=0 / CLEAN / refusal absent. A refusal the caller's own
        #    branch can suppress is not a refusal.
        #    The guard is scoped to the BLOCKING surface through `surface.authority`,
        #    which IS the invariant rather than a proxy for it. Without that scope the
        #    refusal also fires from the ADVISORY closed-PR sample — whose elements can
        #    carry a non-terminal `state` — and an advisory surface could then force
        #    exit 2, the one thing such a surface must never do (the authority split
        #    `test_advisory_closed_pr_strong_shape_cannot_block_but_is_reported` pins).
        #    It would also print "is an OPEN PULL REQUEST" about a PR drawn from the
        #    CLOSED sample.
        #
        #    `use_closing_field` (True only on the blocking CALL SITE) correlates with
        #    the authority today, but it is a CALLER-REQUEST flag: were the blocking
        #    path ever to stop asking gh for `closingIssuesReferences`, keying on it
        #    would silently DELETE this fail-closed refusal. `surface.authority` cannot
        #    drift that way — it is assigned per surface at construction from
        #    ADVISORY_SURFACES.
        #
        #    Coverage note, stated accurately: an advisory number==issue test already
        #    existed (`test_closed_pr_own_number_is_weak_not_blocking`), but its fixture
        #    omits `state`, which `gh_fixtures` normalizes to "closed" — TERMINAL — so
        #    the `terminal is None` clause was never exercised for the number==issue arm
        #    on that surface. The uncovered shape is a NON-TERMINAL state, which
        #    `test_advisory_surface_cannot_refuse_a_number_that_is_the_issue` pins.
        if (
            surface.authority != AUTHORITY_ADVISORY
            and terminal is None
            and str(pr.get("number")) == str(issue)
        ):
            linked = []
            unreadable = False
            # The message decoration must NOT be able to pre-empt the refusal.
            # `_closing_ref_numbers` raises `SurfaceError` on an absent/malformed
            # `closingIssuesReferences`, and this runs BEFORE the raise below — so
            # an unguarded call would let the open-PR handler absorb it and report
            # a mislabelled broken surface ("closing-reference-source-unavailable
            # … fix gh auth/network") instead of the refusal. Degrade to an empty
            # list: the refusal carries on and merely says less.
            try:
                if use_closing_field:
                    linked = sorted(_closing_ref_numbers(pr))
                else:
                    # A BLOCKING surface whose caller did not request the field:
                    # its CONTENTS are unknown, not empty, so the message below
                    # must not answer "it names no closing issue". The guard keys
                    # on `surface.authority` (may this surface refuse?) while this
                    # read keys on `use_closing_field` (did the CALLER request the
                    # field? — it is a request flag, not a property of the
                    # payload, which may still lack it: that is the SurfaceError
                    # arm below) — different questions, so they may legitimately
                    # diverge. `unreadable` is what keeps the divergence from
                    # becoming a false claim. ⛔ THE DIVERGENCE IS LIVE, NOT
                    # HYPOTHETICAL (#9402): the BLOCKING open-PR call site passes
                    # `use_closing_field=not via_rest`, so when the enumeration
                    # came from the REST rescue its list has no
                    # `closingIssuesReferences` and a pre-flight on an OPEN PR's
                    # own number reaches EXACTLY this arm — it refuses with "its
                    # closing-reference field could not be read", which is the
                    # right wording for a field the caller never asked for.
                    # `test_blocking_surface_without_the_closing_field_reports_it_unread`
                    # pins the wording and
                    # `test_open_pr_number_refusal_survives_a_rest_rescued_list`
                    # pins the live route end to end.
                    unreadable = True
            except SurfaceError:
                # `unreadable` is carried so the message below does NOT assert a
                # fact the tool cannot know. THIS branch is reached when the field
                # is ABSENT or malformed — its contents are unknown — so "it names
                # no closing issue" would be a claim about contents the tool never
                # read. That wording belongs to the present-and-empty field, which
                # is a KNOWN "closes nothing". A wrong reason for a right decision
                # is the defect class this very change is about.
                linked = []
                unreadable = True
            # The refusal is UNCONDITIONAL within the open case. It does not
            # depend on the PR naming a linked issue, because the defect is
            # not "we could not name the work item" — it is "the number you
            # passed is not a work item". Gating on a closing reference left
            # the fail-open intact for exactly the PR that exposed it: #7477
            # references (#7455) in its TITLE (this repo's convention) and
            # carries NO closing keyword, so its closing field is empty and a
            # `linked`-gated refusal fell straight through (measured).
            where = (
                " It closes " + ", ".join(f"#{n}" for n in linked) + "."
                if linked else
                " Its closing-reference field could not be read, so resolve the"
                " work item by hand (this repo's convention puts it in the title"
                " as `(#N)`)."
                if unreadable else
                " It names no closing issue, so resolve the work item by hand"
                " (this repo's convention puts it in the title as `(#N)`)."
            )
            raise NotAWorkItem(
                f"#{issue} is an OPEN PULL REQUEST, not a work item. A pull "
                f"request number is also an issue number, so any verdict "
                f"computed here describes the PR and NOT the issue it belongs "
                f"to — and an exit 0 would authorise a dispatch on work this "
                f"PR belongs to.{where} Re-run with the issue number this PR "
                f"belongs to.",
                # #9957: the record travels WITH the refusal, so `main()` can
                # resolve the number to the work item instead of handing the
                # caller's prose-obedience problem back to it.
                pr=pr,
            )
        # 1. SELF (#3504 class 4; #4567's ordering root cause).
        if identity.owns_branch(head):
            surface.add(_pr_ref(pr),
                        "your own PR (its head branch is one of --self-branch) — "
                        "not a competing claim", "weak")
            continue
        # 2. A TERMINAL PR whose number == the issue keeps the old (weak)
        #    behaviour, and that is load-bearing:
        #    `test_closed_pr_own_number_is_weak_not_blocking` pins it — a number
        #    whose object is a CLOSED/MERGED PR is immutable history, not
        #    in-flight work, so CLEAN is the CORRECT answer for it. Refusing
        #    there would turn a right answer into a refusal. The hazard is
        #    entirely the NON-TERMINAL case, which block 0 refuses on a
        #    NON-ADVISORY surface; on the ADVISORY one block 0 cannot fire, so
        #    that same entry reaches here and is reported weak — which is the
        #    behaviour this block exists to preserve.
        if str(pr.get("number")) == str(issue):
            surface.add(_pr_ref(pr),
                        f"PR number == issue ({issue}): this PR *is* the issue, "
                        "not separate in-flight work (non-blocking)", "weak")
            continue
        # 3. Real match tests.
        if number_present(title, issue):
            surface.add(_pr_ref(pr),
                        f"matched issue-number ({issue}) in title{suffix}",
                        "weak" if terminal else "strong")
            continue
        if number_present(head, issue):
            surface.add(_pr_ref(pr),
                        f"matched issue-number ({issue}) in branch{suffix}",
                        "weak" if terminal else "strong")
            continue
        if use_closing_field:
            # Raises on a missing/malformed field; the caller turns that into
            # INCOMPLETE rather than silently losing a blocking signal.
            by_field = issue in _closing_ref_numbers(pr)
            # DELIBERATE UNION, not redundancy. The computed field is the
            # SOURCE and is authoritative where it speaks; the regex is kept as
            # a second, ADDING-ONLY test because a field-only implementation
            # would DROP a hit the moment GitHub's field misses something a
            # body states plainly (`Closes #N` in a cross-repo reference, say),
            # and dropping a blocking signal is the fail-OPEN direction — the
            # one a gate must never take. The union can only add hits, so
            # "prefer the source" is honoured without betting the gate on the
            # source being complete for every edge case. The field's ABSENCE is
            # still loud: it raises above and becomes INCOMPLETE.
            by_body = closing_reference(body, issue)
            if by_field or by_body:
                how = (
                    "GitHub's closingIssuesReferences includes" if by_field
                    else "closing keyword in the body for"
                )
                surface.add(_pr_ref(pr), f"{how} #{issue}{suffix}",
                            "weak" if terminal else "strong")
                continue
        elif closing_reference(body, issue):
            # ⛔ THE WORDING FOLLOWS THE AUTHORITY, NOT `use_closing_field`. Until
            # #9402, `use_closing_field=False` meant ONLY the advisory closed-PR
            # sample, so this detail could assert "this surface is advisory".
            # The blocking open-PR surface now also passes False when its list
            # came from the REST rescue (REST has no `closingIssuesReferences`),
            # and there a non-terminal hit is `strong` — blocking. A hardcoded
            # advisory claim would then print, under `VERDICT: COLLISION`, two
            # false statements in the one line a human reads at the point of
            # refusal: that the surface is advisory, and that it did not produce
            # the refusal. `surface.authority` is the property that decides
            # whether this hit can refuse, so it is the property the sentence
            # states; the blocking form names the REAL cause (the field was not
            # requested on this call) instead.
            fallback_why = (
                "this surface is advisory"
                if surface.authority == AUTHORITY_ADVISORY
                else "closingIssuesReferences was not requested on this call"
            )
            surface.add(_pr_ref(pr),
                        f"closing reference to #{issue} in body (regex fallback — "
                        f"{fallback_why}){suffix}",
                        "weak" if terminal else "strong")
            continue
        if number_present(body, issue):
            surface.add(_pr_ref(pr),
                        f"prose mention of #{issue} in body — not a closing "
                        "reference (non-blocking)", "weak",
                        # ⛔ A STRUCTURED KIND, not a substring of `detail`. The two
                        # reports used to split the weak set with `"prose" in
                        # h.detail`, which is a test over free text that EMBEDS a
                        # lane label (`lane '12 Prose naturalisation' ...`): a
                        # stopped holder whose label contains "prose" was then
                        # named a "prose-only cross-reference" while the non-prose
                        # kinds went unmentioned (#8175 review).
                        kind="prose-body")


def assignee_attribution(login: str | None, identity: Identity) -> str:
    """Attribute an issue assignee: ``other`` | ``shared`` | ``unknown``.

    GitHub's assignee is a LOGIN only — unlike a comment it carries no lane or
    session marker. The rule is now keyed on the only question that matters:
    **can this value distinguish one lane from another?**

    * ``other`` — a DIFFERENT login. Affirmatively another party, so it blocks.
    * ``shared`` — OUR OWN login. On this fleet every lane authenticates as one
      account, so this value cannot distinguish lanes: it is equally consistent
      with a human triaging, with this lane, and with any other lane. A signal
      that is the same in every world carries no information, and the tool has
      no business blocking a dispatch on it. It is ADVISORY.
    * ``unknown`` — no login, a placeholder, or we could not resolve our own
      login. We cannot tell, so it blocks: that is the fail-closed direction.

    This REPLACES the previous rule, which kept the shared-account case as a
    blocking hit on the argument that suppressing it "would blind the surface
    to every other lane". That argument is exactly backwards for a gate: a
    value that is identical for every lane cannot reveal any lane, so treating
    it as a hit does not preserve sensitivity — it manufactures a permanent
    false COLLISION. The repo owner is the assignee on 86 of 622 open issues,
    which is triage, not contention (#3504 class 3).
    """
    if not login or login.strip().lower() in ("", "unknown", "ghost", "none"):
        return "unknown"
    if not identity.login:
        return "unknown"
    if login.strip().lower() != identity.login.strip().lower():
        return "other"
    return "shared"


# ── the claim gate's holder read (#8175) ─────────────────────────────────────
#
# The gate asks the FLEET BOARD one question: is a lane still working this issue?
# Attribution and liveness are both the board's own answers (see the module
# docstring), so this section does only four things — read the board (fresh, or
# REBUILT when stale/absent), check that it could answer at all, project the
# lanes the board attributes to this issue, and classify each holder.
#
# ⛔ NO COMMENT PROSE IS READ HERE. There is no regex, no model call and no path
# that takes a comment body — `gh issue view` no longer even FETCHES `comments`.
# The constant block above states why that is permanent.

# Holder classifications. The order below is the gate's preference, and it is
# fail-closed: an UNMEASURED liveness is never downgraded to "stopped".
HOLDER_LIVE = "live"              # a live pi process            -> blocks
HOLDER_STOPPED = "stopped"        # measured, no live pi process   -> reported only
HOLDER_UNMEASURED = "unmeasured"  # no pid reading at all          -> fail closed
HOLDER_AMBIGUOUS = "ambiguous"    # `resolve_sessions` refused     -> fail closed

#: The ONLY non-empty states `fleet_state.workspace_terminal` emits. A terminal
#: workspace is NOT evidence about the PROCESS (its cmux workspace is gone — that
#: "says nothing about whether a pi process outlived it"), so it can never decide
#: the verdict; these names gate the STOPPED reason's detail only, and an
#: unrecognised name must not be narrated as a dead workspace.
TERMINAL_STATES = ("RECOVERABLE", "RETIRED")

#: The WEAK (non-blocking) hit kinds, named ONCE. Two reports print this set —
#: `format_report` here and `who_is_on`'s WEAK SIGNALS heading — and they were
#: kept in sync by prose until they drifted (#8175); a shared constant is the
#: only form that cannot. `WEAK_KIND_PROSE_ONLY` is split out because the two
#: reports count prose-only hits separately from the rest, and it is
#: interpolated by BOTH of them (not just one) for that same reason.
WEAK_HIT_KINDS = (
    "a HOLDER the board reports as stopped (the writer has stopped), your own "
    "work, a terminal branch/PR, the shared fleet account, a fetch-cache ref "
    "under a namespace no configured remote fetches into, a worktree RECORD "
    "that cannot be live work (#6622), an EMPTY CLAIM — a local branch 0 commits "
    "ahead of origin/main with no live owner, no uncommitted work and no live "
    "claim ref (#6108)"
)
WEAK_KIND_PROSE_ONLY = "a prose-only cross-reference"

#: The holder verdicts that make the surface INCOMPLETE rather than a collision:
#: in each case the board could not ANSWER, which is not the same fact as "a lane
#: is on it" (the module's design contract keeps exit 1 and exit 2 distinct).
HOLDER_UNANSWERED = (HOLDER_UNMEASURED, HOLDER_AMBIGUOUS)


@dataclass(frozen=True)
class Holder:
    """One lane the board attributes to this issue, and what the board says about
    it. `verdict` is one of the `HOLDER_*` constants above."""

    lane: str
    evidence: str
    verdict: str
    reason: str


@dataclass(frozen=True)
class HolderRead:
    """The claim gate's whole answer for ONE issue.

    `measured` is False when the BOARD could not answer — unreadable,
    unrebuildable, or carrying an unmeasured provenance — and the issue surface
    is then INCOMPLETE, never CLEAN. `holders` is EMPTY when the board answered
    and no lane is attributed: that measured "nobody" is the only state in which
    this arm does not block.
    """

    measured: bool
    source: str
    unanswerable: str = ""
    holders: tuple[Holder, ...] = ()
    # The roster's SIZE, from the board's own counts (#9392). `None` means the
    # board did not publish it (an older writer) — NOT zero: an unread roster
    # must never be reported as an empty one.
    roster_rows: int | None = None
    workspaces_count: int | None = None
    # Whether the LIVE WORKSPACE read actually happened (#9392 review P1). The
    # count beside it is only a measurement when this is True: an unreachable
    # cmux (`workspaces_measured: false`, the documented fail-open of #7738)
    # publishes NO workspaces, so a bare `0` there is an artefact of the failed
    # read and printing it asserts an empty live fleet.
    workspaces_measured: bool = False
    # THE ROSTER READ'S RECORDED CAUSE, as DATA beside the note (#9392 review
    # P2). `_incomplete_remedy` used to hand the whole composed NOTE back to
    # `fleet_state.registry_remedy`, so the note — which always ends with a
    # remedy sentence — always read as a non-empty "cause" and the helper's
    # "no cause recorded" arm was unreachable there: the exit-2 line asserted
    # "the roster EXISTS but could not be read" for a board that had recorded no
    # cause at all. `None` therefore means NOT RECORDED (distinct from "" only
    # in provenance; both take that arm), exactly like `budget_exhausted` /
    # `truncation_widenable` on `Surface`, which exist for this same reason.
    registry_error: str | None = None


def _board_can_answer(state: dict) -> str:
    """`''` when the board can answer the attribution question, else the reason.

    The board carries its own provenance, and BOTH reads are load-bearing for
    this question: an unread lane REGISTRY enumerates zero lanes (so "nobody
    holds it" would come from a read that never happened), and an unread GITHUB
    side yields no open-issue set, which is what attribution filters against.
    Either failure is UNMEASURED — never "no holder".

    ⛔ THE SHAPE OF THE ANSWER IS CHECKED TOO, not only the provenance. The
    answer is read OUT of `index.issues`, so a board that is "measured" but
    carries no usable index would make `_holders_from_board` return `()` — and
    "absence read as emptiness" is exactly the fail-open this function exists to
    stop, one key over from the reads its provenance describes.
    """
    rec = state.get("reconciliation")
    if not isinstance(rec, dict):
        return (
            "the board carries no `reconciliation` provenance, so whether its "
            "lane-registry and GitHub reads succeeded is unknown"
        )
    if rec.get("registry_measured") is not True:
        # ⛔ THE REMEDY IS DERIVED FROM THIS CAUSE (#9392), not left to the
        # verdict line's generic sentence. An absent roster is not a read that
        # went wrong on a bad day: it was never provisioned, `fleet_state.py
        # build` cannot create it (the board is built FROM it), and the two
        # other readers of this note (`who_is_on.py`, and the exit-2 remedy)
        # must give a lane the SAME answer. `registry_remedy` is the one place
        # that sentence lives, so the three cannot disagree.
        # ⛔ ONE BINDING, THREE USES (#9392 review P2). The prose, the remedy and
        # `cause` are all derived from this ONE validated value: composing the
        # prose from the raw field while publishing `cause` from the validator
        # let them disagree — a truthy non-string cause printed "the roster
        # EXISTS but could not be read" in the note while the exit-2 line said
        # "the cause … was not recorded".
        cause = _board_registry_error(rec)
        return BoardRefusal(
            "the board's lane registry could not be read "
            f"({cause or 'cause not recorded'}) — the lane "
            "roster is UNKNOWN, not empty — "
            f"{fleet_state.registry_remedy(cause or '')}",
            # …and the MACHINE value beside the sentence, so a downstream reader
            # classifies the cause it was given instead of re-parsing this prose
            # back out of it.
            cause=cause,
        )
    if rec.get("github_measured") is not True:
        return (
            "the board's GitHub read failed "
            f"({rec.get('github_error') or 'cause not recorded'}) — the "
            "open-issue set that attribution filters against is unknown"
        )
    # ⛔ AND WAS THAT OPEN-ISSUE READ WIDE ENOUGH TO PROVE A NEGATIVE? The flag
    # must be PRESENT and a BOOL, because it is the one field whose absence is
    # NOT the safe reading (#8175): the writer this change replaces capped the
    # same read at 800 against 989 open issues today, so an unstamped board's
    # coverage is UNKNOWN, not complete — such a board is rebuilt, never served.
    if not isinstance(rec.get("open_issues_truncated"), bool):
        return (
            "the board does not record whether its OPEN-ISSUE read was truncated "
            "(the field is absent or not a boolean), so whether it could see "
            "this issue at all is unknown — an absent attribution would then "
            "come from a read that may never have covered the issue"
        )
    index = state.get("index")
    if not isinstance(index, dict) or not isinstance(index.get("issues"), dict):
        return (
            "the board carries no usable `index.issues`, so no attribution can "
            "be read from it — an empty answer would come from a shape that was "
            "never there, not from a measured 'nobody'"
        )
    if not isinstance(state.get("lanes"), (list, tuple)):
        return (
            "the board carries no usable `lanes` list, so the liveness of any "
            "attributed lane cannot be read"
        )
    # ⛔ THE BOARD'S OWN LIVENESS PROVENANCE, AND IT IS REQUIRED, NOT OPTIONAL.
    # `build_state` publishes a `liveness-unmeasured` orphan when the live-session
    # store could not be read, and in that state every lane's `pid_alive` is False
    # BY DEFAULT — so without this check the holder read would call every
    # attributed lane "stopped" from a read that never happened. The dataclass
    # states the rule this enforces: "an unmeasured `pid_alive` is UNMEASURED, not
    # False, and must not be reported as a dead owner".
    #
    # ⛔ AND AN ABSENT ORPHAN REPORT IS NOT "NO ORPHAN". The check used to be
    # `isinstance(orphans, (list, tuple)) and any(...)`, which a board that simply
    # OMITS the key skipped — leaving the gate to read per-lane `pid_alive` values
    # from a board whose session store may never have been read at all: a wrong
    # CLEAN. The key is therefore REQUIRED (the writer always writes it), the same
    # polarity `open_issues_truncated` gets one field up.
    #
    # This check comes BEFORE any per-lane reading because the BOARD's provenance
    # is authoritative here: in this state `build_state` writes every lane's
    # `pid_alive` as False by DEFAULT, so a board that cannot show its live-session
    # store was read must not be trusted even where a per-lane field claims a
    # measurement.
    orphans = state.get("orphans")
    if not isinstance(orphans, (list, tuple)):
        return (
            "the board carries no usable orphan report, so whether its "
            "live-session store was read — and therefore whether an attributed "
            "lane's `pid_alive: false` is a measurement or a default — is "
            "unknown"
        )
    if any(
        isinstance(o, dict) and o.get("kind") == "liveness-unmeasured"
        for o in orphans
    ):
        # #8999: the board now names WHICH store failed and why (both cmux grammars
        # and the process table are tried), so the refusal carries the cause instead
        # of only the fact. An older board has no such field and gets the bare
        # sentence.
        cause = rec.get("liveness_error")
        return (
            "the board's live-session store could not be read, so NO lane's "
            "liveness is measurable — an unmeasured pid is not a dead owner"
            + (f" (tried: {cause})" if isinstance(cause, str) and cause else "")
        )
    return ""


def _board_is_for_repo(state: dict, slug: str) -> bool:
    """Is this board the one built for `slug`?

    ⛔ A CACHED BOARD IS SINGLE-REPO EVIDENCE. `build_state` stamps `repo`, and
    `attribute_claims` gates every attribution on THAT repo's open-issue set
    while `index.issues` is keyed by a BARE issue number (`"6151"`, not
    `"owner/name#6151"`). Serving a fresh board built for another repo would
    therefore both FABRICATE a holder (the other repo's #N) and HIDE one (this
    repo's #N, whose only signal is a lane's own session text) — in the repo
    where `--repo owner/name` cross-repo pre-flights are routine. A board with no
    `repo` stamp is treated the same way as a mismatched one: rebuild, which is
    the fail-closed direction and repairs the stamp.

    The comparison folds case: GitHub slugs are case-insensitive, and the tool
    deliberately preserves the selector's case in its report, so a
    case-different selector is the SAME repo and must not force a rebuild.
    """
    repo = state.get("repo")
    return isinstance(repo, str) and repo.strip().lower() == slug.strip().lower()


def _read_board(slug: str | None, repo_root: str) -> tuple[dict | None, str, str]:
    """`(board, source, why)` — the fleet board, REBUILT when stale or absent.

    Serves the cached board only while it is FRESH (the board's own
    `fleet_state.state_freshness` window) and REBUILDS otherwise. Serving an aged
    cache is the unsafe direction this change exists to stop: a lane that began
    work after the last build is invisible in a stale board, which would then
    report "nobody is working this" about work that started minutes ago.

    `why` is non-empty exactly when no board could be produced.

    `source` names WHICH of the four reads answered, and it is rendered into the
    gate's own note, so a reader can see which one it was:

    * ``cache``         — the shared board at `fleet_state.DEFAULT_OUT`, fresh and
                          answerable;
    * ``rebuilt-cache`` — the gate's PRIVATE copy of the partial board it rebuilt
                          earlier in this pass (`COLLISION_PREFLIGHT_BOARD_CACHE`),
                          served only while the BOARD'S OWN clock says it is fresh;
    * ``rebuilt``       — a rebuild performed on this run. It is served from memory
                          and written to that private cache, never back over the
                          shared board — see the module docstring's "The gate never
                          writes the shared board";
    * ``unmeasured``    — no board could be produced (`why` says why, and it blocks).

    The rebuild uses the board's OWN tools (bare gh / cmux from PATH), not this
    tool's `--gh` / `--git`. That is safe in the fail-closed direction: a rebuild
    whose gh is unavailable reads UNMEASURED and blocks.
    """
    if slug is None:
        return None, "unmeasured", (
            "the target repo could not be resolved, so no board can be built for it"
        )
    path = Path(fleet_state.DEFAULT_OUT)
    cached: object = None
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except (OSError, ValueError):
            cached = None
    if isinstance(cached, dict) and not fleet_state.state_freshness(cached)["stale"]:
        why = _board_can_answer(cached)
        if not why and not _board_is_for_repo(cached, slug):
            why = (
                f"the cached board was built for {cached.get('repo')!r}, not "
                f"{slug!r} — `index.issues` is keyed by a BARE issue number and "
                "attributed against that board's own open-issue set, so serving "
                "it here could both fabricate and hide a holder"
            )
        if not why:
            return cached, "cache", ""
        # A FRESH board that cannot answer is REBUILT rather than believed: the
        # rebuild is a real measurement, and only if IT cannot answer is the
        # question unmeasured. A stale, other-repo, or unstamped board falls
        # through to the same rebuild, which is served from memory.
    # ⛔ THE GATE'S OWN CACHE — NOT the shared board. `build_state` here is a PARTIAL board (no per-PR
    # CI, no review `diff-match`), so writing it to `fleet_state.DEFAULT_OUT` would degrade it for every
    # other consumer (see the note above `return rebuilt, "rebuilt", ""`). Caching it in the gate's
    # PRIVATE cache degrades nothing: the only reader is this gate, and the value is the same partial
    # board it would have computed anyway.
    # WHY IT MATTERS: a refresh pass runs this tool once PER CANDIDATE, and the rebuild measured 15.9 s
    # of a 19 s run (`fleet_state.open_issues` alone 11.2 s + 3 s of backoff). Six candidates paid it six
    # times, which pushed a pass into contention with the controller's own sweep; a contended surface
    # reads as UNQUERYABLE, which is not a verdict, so the backlog never filled and create activity
    # stopped. Paying it once per pass is honest; paying it once per candidate is waste.
    # ⛔ THE KEY CARRIES repo_root, not just the slug: two CLONES of one repo sharing a cache dir must
    # not serve each other's board, whose holder attribution is computed against that clone's own
    # local branches and worktrees (#4256 hazard).
    board_key = f"partial_board|{slug}|{repo_root}"
    cached_board = _cache_get(board_key)
    if isinstance(cached_board, dict) and not _board_can_answer(cached_board) \
            and _board_is_for_repo(cached_board, slug) \
            and not fleet_state.state_freshness(cached_board)["stale"]:
        # ⛔ AND THE BOARD'S OWN CLOCK BOUNDS THE HIT (review P2). A raised TTL would otherwise honour
        # an aged board — a lane that began work after the cached build is invisible in it, which reads
        # as "nobody is working this" about work that started minutes ago. `_cache_ttl` clamps the TTL
        # to `STATE_FRESH_S`, so this is belt and braces; it also cancels a skew-dated `generated_at`
        # that a clamped TTL alone would let through.
        return cached_board, "rebuilt-cache", ""
    try:
        rebuilt = fleet_state.build_state(
            repo=slug,
            repo_root=repo_root,
            orch_ws=os.environ.get("ORCH_WS", ""),
            sample_s=fleet_state.DEFAULT_SAMPLE_S,
            do_pane=True,
            ci_verdict=None,
            live_diff=None,
        )
    except Exception as exc:
        return None, "unmeasured", (
            f"the fleet board could not be rebuilt ({type(exc).__name__}: {exc})"
        )
    if not isinstance(rebuilt, dict):
        return None, "unmeasured", "the fleet board rebuild returned no state object"
    why = _board_can_answer(rebuilt)
    if not why and not _board_is_for_repo(rebuilt, slug):
        why = (
            f"the rebuilt board is stamped {rebuilt.get('repo')!r} rather than "
            f"{slug!r}, so it does not describe the repo that was asked about"
        )
    if why:
        return None, "unmeasured", why
    # ⛔ THE GATE NEVER WRITES THE SHARED BOARD. The write-back was tried and
    # removed: `build_state` here is called with `ci_verdict=None,
    # live_diff=None` (the board's own writer passes `make_ci_verdict()` and
    # `make_live_diff()`, which shell out per PR), so the board rebuilt here is a
    # PARTIAL one — `prs[].ci` is unset, and the review binding cannot reach its
    # `diff-match` refinement. Writing that to `fleet_state.DEFAULT_OUT` would
    # silently degrade a shared artifact for every other consumer (`fleet_state
    # lane` prints `ci=`/`review=`) for up to a freshness window, i.e. ship a
    # fallback as if it were the board. A read-only gate must not do that. Paying
    # the rebuild on every pre-flight while the cache is stale is the honest
    # price; the board's own writer repairs it.
    _cache_put(board_key, rebuilt)
    return rebuilt, "rebuilt", ""


def _holder_verdict(lane: dict, label: str, evidence: str,
                    live: bool | None) -> Holder:
    """Classify ONE attributed lane from the board's own records.

    Order matters and is fail-closed: an AMBIGUOUS attribution is decided before
    liveness (the board refuses to guess whose session a lane is, so no reading
    of that lane's liveness can be trusted), and an UNMEASURED liveness is never
    reported as stopped.
    """
    session = lane.get("session")
    session = session if isinstance(session, dict) else {}
    liveness = lane.get("liveness")
    liveness = liveness if isinstance(liveness, dict) else {}
    terminal = lane.get("terminal")
    terminal = terminal if isinstance(terminal, dict) else {}
    # ⛔ TWO CONDITIONS, AND BOTH ARE LOAD-BEARING — this mirrors
    # `fleet_state.lane_live_map`, which refuses to call a lane dead without
    # pid evidence (`liveness_measured` AND a recorded `pi_pid`).
    #
    # `liveness_measured` is a THREE-valued fact on the board: True measured,
    # absent/malformed UNMEASURED. Only the literal `True` counts, and
    # `_board_can_answer` already refused the board-level
    # `liveness-unmeasured` orphan that would make every lane's default
    # `pid_alive: False` meaningless.
    #
    # `pid_evidence` is the second half: `build_state` derives `lane_measured`
    # from the RECORDS, but `pid_alive` from the set of pids that are ALIVE — so
    # a lane whose records carry no pid at all gets `liveness_measured: True`
    # with `pid_alive: False` and `pi_pid: None`, and "no live pid" there means
    # "there was no pid to be alive", not "the process stopped". `lane_live_map`
    # reads exactly that shape as `None`. A recorded (even dead) pid — which is
    # what a stopped writer leaves, e.g. `π - land-lane-1` carries `pi_pid: 7054`
    # with `pid_alive: False` — is what makes "the board measured it and found
    # no live process" a true statement.
    measured = (liveness.get("liveness_measured") is True
                and bool(session.get("pi_pid")))
    # ⛔ ONLY AN *UNRESOLVED* LANE IS AMBIGUOUS. `fleet_state.resolve_sessions`
    # records `guess_refused` whenever a pane binding was DANGLING (`claim()`
    # copies `dangling[label]` onto the resolution) — including a lane that a
    # LATER rule then RESOLVED (registry override / unique disk match), which is
    # `bound=True` with a measurable liveness. Reading the note alone would
    # mislabel that resolved lane AMBIGUOUS, turn a definite holder into exit 2,
    # and print a reason ("refused to guess") that is false. `shared_with` is a
    # genuine partition violation and is ambiguous unconditionally.
    #
    # `bound` is a real boolean on every board `build_state` writes (it is a
    # computed property), so only the literal `True` counts as RESOLVED: a
    # malformed value must not read as resolved (which would skip the ambiguity
    # check) any more than it may read as a stop.
    refused = session.get("guess_refused")
    resolved = session.get("bound") is True
    if session.get("shared_with") or (refused and not resolved):
        return Holder(
            label, evidence, HOLDER_AMBIGUOUS,
            "attribution is ambiguous — the board could not resolve whose "
            f"session this lane is ({session.get('guess_refused') or 'the session is shared with another lane'})",
        )
    # ⛔ THE LANE'S OWN LIVENESS RECORD DECIDES, and a LIVE PROCESS WINS OVER A
    # TERMINAL WORKSPACE. `lane_live_map` (and the terminal verdict it folds in)
    # answers a DIFFERENT question — "is this lane still a live fleet member?" —
    # and deliberately marks a terminal lane dead *even when a process outlived
    # the workspace* (#7738). `fleet_state.orphan_report` names the danger:
    # "a terminal lane's cmux workspace is GONE, which says nothing about whether
    # a pi process outlived it — a terminal lane CAN still carry `pid_alive:
    # true`." The claim gate asks "has the writer stopped RUNNING?", so the pid
    # reading is the evidence and the workspace's absence is not.
    #
    # The shape that would fool a rule keyed on the terminal verdict is a
    # terminal workspace whose process is STILL RUNNING; on 2026-10-13 the live
    # board held exactly that (`π - land-lane-1`, holder of #6151: terminal
    # RECOVERABLE with `pid_alive: True`). The rule is pinned by the synthetic
    # test `test_a_terminal_holder_whose_process_is_alive_still_blocks`, not by
    # that observation — a live lane must never read free because its workspace
    # is gone.
    if live is True:
        return Holder(
            label, evidence, HOLDER_LIVE,
            "the board reports a LIVE pi process for it, so its holder has not "
            "stopped RUNNING",
        )
    if live is False:
        # ⛔ `pid_alive: False` IS NOT ITSELF A MEASUREMENT. `build_state` writes
        # `session.pid_alive = bool(alive_pids)`, so a lane with NO session
        # record gets `False` BY DEFAULT while its `liveness.liveness_measured`
        # stays False — and `lane_live_map` reads exactly that shape as `None`
        # ("a lane with no pid evidence is UNMEASURED here, not dead: there is
        # nothing to call dead"). Demoting on the default would report a lane
        # whose writer was never observed as a stopped writer — a false CLEAN,
        # the one direction this gate must never take. `build_state` appends
        # "no session record for this lane — liveness UNMEASURED, not dead" when
        # it reports that lane's free reasons.
        #
        # ⛔ AND A TERMINAL WORKSPACE IS NOT ITS OWN EVIDENCE. A terminal verdict
        # says the cmux workspace is GONE — and `fleet_state.orphan_report` warns
        # in as many words that this "says nothing about whether a pi process
        # outlived it". Demoting on it would read an UNMEASURED pid (the
        # `pid_alive: False` DEFAULT of a lane the board keeps no session record
        # for) as a stopped writer: a wrong CLEAN, and the very shape #7738
        # exists to describe. So the terminal arm is NOT a second way to demote —
        # `measured` is the only one, exactly as it is for a lane whose workspace
        # still exists. A terminal lane whose process is ALIVE returns LIVE above
        # (#6151's `π - land-lane-1` on 2026-10-13); a terminal lane the board
        # MEASURED — a recorded `pi_pid` no longer alive, which is that same lane
        # on 2026-10-14 — returns STOPPED below; a terminal lane whose liveness
        # is UNMEASURED stays UNMEASURED and blocks. Measured 2026-10-14 against
        # the live board: 27 lanes, 9 attributed issues, and ZERO verdicts change
        # under this rule — the change is fail-closed by construction and was a
        # no-op on the real board.
        if measured:
            # The workspace verdict is only ever the REASON here, never the
            # decision — and only when it is a name the board emits (an
            # unrecognised string must not be narrated as a dead workspace).
            terminal_state = terminal.get("state")
            return Holder(label, evidence, HOLDER_STOPPED, _stopped_reason(
                terminal if terminal_state in TERMINAL_STATES else None))
        if liveness.get("liveness_measured") is True:
            # Measured, but with NO pid to measure: `pi_pid` is None, so there is
            # no process whose liveness was observed. `lane_live_map` reads this
            # shape as `None` too.
            return Holder(
                label, evidence, HOLDER_UNMEASURED,
                "the board read this lane's records but keeps NO pid for it "
                "(`pi_pid` is None), so no live process can be found or ruled "
                "out — liveness is UNMEASURED, not dead",
            )
        return Holder(
            label, evidence, HOLDER_UNMEASURED,
            "the board records no session for this lane (`liveness_measured` is "
            "False), so its `pid_alive: false` is a DEFAULT rather than a "
            "measurement — liveness is UNMEASURED, not dead",
        )
    # No pid reading at all. `pid_alive` is a real boolean whenever the board's
    # session store was read, so a missing value is a shape the gate cannot read
    # — never a dead owner.
    return Holder(
        label, evidence, HOLDER_UNMEASURED,
        "the board carries no `pid_alive` reading for this lane, so its liveness "
        "is UNMEASURED (not dead)",
    )


def _stopped_reason(terminal: dict | None) -> str:
    """Why a lane reads STOPPED — ALWAYS from a measured liveness.

    `measured` is the only path here, so this never narrates a verdict it did not
    take: a terminal workspace is the *reason's* detail, not the evidence.
    """
    if terminal:
        state = terminal.get("state")
        detail = terminal.get("reason") or "terminal"
        return (
            "the board MEASURED this lane's liveness and found NO live pi "
            f"process (its workspace is {state}: {detail})"
        )
    return (
        "the board MEASURED this lane's liveness and found no live pi process"
    )


def _holders_from_board(state: dict, issue: int) -> tuple[Holder, ...]:
    """Every lane the board attributes to `issue`, classified.

    Attribution is the board's own `index.issues` projection, built by
    `fleet_state.attribute_claims` from the lane's branch slug, its worktree path
    and its OWN first user message — never from comment prose. An issue absent
    from that index has NO attributed lane.
    """
    index = state.get("index")
    index = index if isinstance(index, dict) else {}
    issues = index.get("issues")
    issues = issues if isinstance(issues, dict) else {}
    entry = issues.get(str(issue))
    if not isinstance(entry, dict):
        return ()
    labels = entry.get("lanes")
    if not isinstance(labels, (list, tuple)) or not labels:
        return ()
    evidence_by_lane = entry.get("evidence")
    if not isinstance(evidence_by_lane, dict):
        evidence_by_lane = {}
    lanes_by_label: dict[str, dict] = {}
    duplicated: set[str] = set()
    board_lanes = state.get("lanes")
    for lane in board_lanes if isinstance(board_lanes, (list, tuple)) else ():
        if not isinstance(lane, dict):
            continue
        identity = lane.get("identity")
        if isinstance(identity, dict) and isinstance(identity.get("lane"), str):
            label = identity["lane"]
            if label in lanes_by_label:
                duplicated.add(label)
            lanes_by_label[label] = lane
    live_map = {label: _lane_pid_alive(lane) for label, lane in lanes_by_label.items()}
    holders: list[Holder] = []
    for label in labels:
        if not isinstance(label, str):
            continue
        evidence = str(evidence_by_lane.get(label) or "attributed by the fleet board")
        if label in duplicated:
            # ⛔ TWO RECORDS, ONE LABEL. `lanes_by_label` is last-wins, so reading
            # the surviving record would let a LIVE record followed by a STOPPED
            # one answer "stopped" — a false CLEAN from an anomaly the label
            # cannot resolve. Fail closed instead.
            holders.append(Holder(
                label, evidence, HOLDER_UNMEASURED,
                "the board enumerates MORE THAN ONE lane record with this label, "
                "so which one holds the issue cannot be read",
            ))
            continue
        lane = lanes_by_label.get(label)
        if lane is None:
            # The board attributes this issue to a lane it does not enumerate.
            # A missing lane record cannot be read as "stopped" — fail closed.
            holders.append(Holder(
                label, evidence, HOLDER_UNMEASURED,
                "the board attributes this issue to a lane it does not enumerate, "
                "so that lane's liveness cannot be read",
            ))
            continue
        holders.append(_holder_verdict(lane, label, evidence, live_map.get(label)))
    return tuple(holders)


def _lane_pid_alive(lane: dict) -> bool | None:
    """The lane's OWN pid reading: `True` / `False` / `None` (not measured).

    Deliberately NOT `fleet_state.lane_live_map`, which folds a TERMINAL verdict
    into `False` and therefore reports a terminal lane whose process outlived its
    workspace as dead. That is the right answer to "is this lane still a live
    fleet member?" and the wrong answer to "has the writer stopped RUNNING?" —
    see `_holder_verdict`, and `fleet_state.orphan_report`'s warning that a
    terminal lane CAN still carry `pid_alive: true`.

    A value that is not a JSON boolean is UNMEASURED rather than False: the
    whole point of the `None` arms is that an unread pid must never present as a
    dead owner.

    ⛔ `False` IS ONLY MEANINGFUL WITH `liveness.liveness_measured`. `build_state`
    writes `session.pid_alive = bool(alive_pids)`, so a lane whose session record
    is absent carries `False` BY DEFAULT — `_holder_verdict` reads
    `liveness_measured` alongside this and refuses to demote on the default alone.
    """
    session = lane.get("session")
    if not isinstance(session, dict):
        return None
    alive = session.get("pid_alive")
    return alive if isinstance(alive, bool) else None


def _issue_entry_problem(state: dict, issue: int) -> str:
    """`''` when the board's `index.issues` ENTRY for `issue` is readable.

    ⛔ ABSENCE IS AN ANSWER ONLY IF THE READ COVERED THE ISSUE. No entry means
    `attribute_claims` attributed no lane — a legitimate "nobody" ONLY when the
    board's OPEN-ISSUE read was wide enough to have seen this issue (it gates
    every attribution on membership in that read, so an issue outside it can
    never be attributed). A board that does not SAY its read was complete leaves
    coverage unknown, which is not an answer.

    ⛔ AND A MALFORMED ENTRY IS NEVER AN ANSWER. `_holders_from_board` collapses
    every unreadable entry to `()`, which is indistinguishable from that measured
    "nobody", so a non-object entry, a non-list `lanes`, or a lane label that is
    not a name would all read as CLEAN — the same absence-read-as-emptiness
    fail-open `_board_can_answer` closes one key over, for the outer shape only.
    """
    index = state.get("index")
    index = index if isinstance(index, dict) else {}
    issues = index.get("issues")
    issues = issues if isinstance(issues, dict) else {}
    key = str(issue)
    if key not in issues:
        # ⛔ ABSENCE IS A MEASURED ANSWER ONLY IF THE READ COVERED THIS ISSUE.
        # `index.issues` is populated from `attribute_claims`, which gates every
        # attribution on membership in the board's OPEN-ISSUE read — so if that
        # read hit its cap (#8175: `fleet_state.OPEN_ISSUES_LIMIT`), the issues it
        # did not see cannot be attributed, and an absent entry would read as a
        # measured "nobody" for an issue nobody ever looked at. That is the same
        # absence-read-as-emptiness fail-open one level up, and the only safe
        # reading of an UNRECOGNISED value for it is "coverage unknown" — the
        # polarity `_board_can_answer` also enforces, so this board would already
        # have been rejected and rebuilt (``test_a_truncated_open_issue_read_...``).
        rec = state.get("reconciliation")
        trunc = rec.get("open_issues_truncated") if isinstance(rec, dict) else None
        if trunc is True:
            read = rec.get("open_issues_read")
            limit = rec.get("open_issues_limit")
            return (
                "the board's OPEN-ISSUE read hit its cap "
                f"({read} issue(s) read, limit {limit}), so an issue outside that "
                "window cannot be attributed — its absence here is not a "
                "measured 'nobody'"
            )
        if trunc is not False:
            return (
                "the board does not record whether its OPEN-ISSUE read was "
                f"truncated ({'absent' if trunc is None else type(trunc).__name__}), "
                "so its coverage of this issue cannot be read"
            )
        return ""
    entry = issues[key]
    if not isinstance(entry, dict):
        return (
            f"the board's attribution entry for this issue is a "
            f"{type(entry).__name__}, not an object — its lanes cannot be read"
        )
    lanes = entry.get("lanes")
    # An EMPTY list is refused too: the writer creates the entry and appends its
    # first lane in the SAME iteration (`attribute_claims`), so a persisted entry
    # with no lanes is another shape the writer never emits — and
    # `_holders_from_board` would read it as a measured "nobody".
    if not isinstance(lanes, (list, tuple)) or not lanes:
        return (
            "the board's attribution entry for this issue carries no usable "
            "`lanes` list"
        )
    if not all(isinstance(lane, str) for lane in lanes):
        return (
            "the board's attribution entry for this issue lists a lane that is "
            "not a name"
        )
    return ""


def _board_count(rec: dict, key: str) -> int | None:
    """One of the board's published COUNTS, or ``None`` when it did not publish
    a usable one (#9392).

    ⛔ ABSENT IS NOT ZERO, the same rule the provenance fields obey: a board from
    an older writer carries no `registry_rows`, and reporting `0` would say "the
    roster is empty" about a row count nobody measured. A bool is rejected too —
    `isinstance(True, int)` is True, and a `True` here would print "1 row".
    """
    value = rec.get(key) if isinstance(rec, dict) else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _board_registry_error(state: object) -> str | None:
    """The LANE-ROSTER cause the board RECORDED, or ``None`` when it recorded
    none (#9392 review P2).

    ⛔ THE CAUSE IS READ FROM THE BOARD, NEVER FROM A NOTE. `_board_can_answer`
    composes a sentence that embeds this value AND appends `registry_remedy(...)`;
    handing that sentence back to `registry_remedy` made its "no cause recorded"
    arm unreachable and asserted "the roster EXISTS" for a board that recorded
    nothing. The field is the one place the cause exists, so it is what travels.

    Accepts either the whole state or its `reconciliation` mapping, so a caller
    holding `rec` does not have to re-wrap it just to ask this question.
    """
    rec = state.get("reconciliation") if isinstance(state, dict) else None
    if rec is None and isinstance(state, dict):
        rec = state          # already the `reconciliation` mapping
    if not isinstance(rec, dict):
        return None
    error = rec.get("registry_error")
    if not isinstance(error, str) or not error.strip():
        # A BLANK CAUSE IS NOT A CAUSE. The board writes `""` when the read
        # recorded none (`load_registry` always writes a cause on its only failure
        # path), so collapsing blank and absent here keeps `None` meaning exactly
        # "no cause recorded" at every reader.
        return None
    return error


def read_holders(issue: int, *, slug: str | None, repo_root: str) -> HolderRead:
    """The claim gate's answer for `issue`: who holds it, are they still there?

    The single entry point `run_preflight` calls. Never raises: an unobtainable
    board is a `measured=False` read, which the issue surface reports as
    INCOMPLETE (fail-closed), never as "nobody".
    """
    state, source, why = _read_board(slug, repo_root)
    if state is None:
        # ⛔ THE CAUSE TRAVELS INSIDE THE REFUSAL, NOT BESIDE IT (#9392 review
        # P2). `why` is a `BoardRefusal`, so the roster cause the board RECORDED
        # reaches the exit-2 remedy as data. `_read_board`'s contract is
        # deliberately unchanged: an unanswerable board is still returned as
        # `None`, so it can never be read as an answer (the two tests that pin
        # that stay green — the cause rides on the sentence, not on the state).
        return HolderRead(measured=False, source=source, unanswerable=why,
                          registry_error=getattr(why, "cause", None))
    entry_problem = _issue_entry_problem(state, issue)
    if entry_problem:
        return HolderRead(
            measured=False, source=source, unanswerable=entry_problem,
        )
    return HolderRead(
        measured=True, source=source, holders=_holders_from_board(state, issue),
        roster_rows=_board_count(
            state.get("reconciliation") or {}, "registry_rows"),
        workspaces_count=_board_count(
            state.get("reconciliation") or {}, "workspaces_count"),
        # ⛔ THE COUNT'S PROVENANCE, read with the same strict-true polarity the
        # board-level checks use: only a literal `True` says the workspace read
        # happened, and an OLDER board that carries the count but not the flag is
        # treated as unmeasured (fail-closed — it prints one fewer number, never
        # a fact it cannot support).
        workspaces_measured=(
            (state.get("reconciliation") or {}).get("workspaces_measured")
            is True),
    )


def _holder_gate_note(read: HolderRead) -> str:
    """One line stating what the gate read, so the row never claims a reader it
    does not have — the row is named `issue assignee/holder` for that reason.

    ⛔ THE MEASURED ARMS PUBLISH THE ROSTER'S SIZE BESIDE ITS SOURCE (#9392).
    `board source` is the READ's provenance, not the roster's coverage: a
    hand-written ONE-ROW roster (the reader's own lane) prints `board source:
    rebuilt` exactly like a full one, and the measured incident is that this
    turned 11 live lanes invisible and flipped an INCOMPLETE into a CLEAN with no
    code change. Printing the row count (and the live workspace count beside it)
    makes a roster of one VISIBLY a roster of one. It changes no verdict — the
    gate still keys on `registry_measured` — so it is disclosure, not a
    dismissal switch.
    """
    counts = ""
    if read.roster_rows is not None:
        counts = f"; lane roster: {read.roster_rows} row(s)"
        # ⛔ THE LIVE COUNT ONLY WHEN IT WAS MEASURED (#9392 review P1). Every
        # failure path of `cmux_workspaces` returns an EMPTY map, so an
        # unmeasured read would print "vs 0 live workspace(s)" — telling a
        # reader that every roster row is a ghost lane, from a read that never
        # happened. `registered: N` alone is a smaller answer than a false one.
        if read.workspaces_count is not None and read.workspaces_measured:
            # ⛔ TWO COUNTS, NOT A COMPARISON (#9392 review P2). Roster rows
            # legitimately OUTLIVE the workspaces they name (the board retires
            # such rows to `RECOVERABLE`/`RETIRED`), and a cell's ORCHESTRATOR
            # workspace has no roster row at all, so `N vs M` printed a
            # discrepancy in the HEALTHY case — the opposite of the intent.
            counts += f"; live cmux workspaces: {read.workspaces_count}"
    if not read.measured:
        return (
            "claim gate: the FLEET BOARD could not answer — "
            f"{read.unanswerable} (comment prose is not read, #8175)"
        )
    if not read.holders:
        return (
            "claim gate: holder read from the FLEET BOARD "
            f"(board source: {read.source}{counts}) — NO lane is mechanically "
            "attributed to this issue; comment prose is not read (#8175)"
        )
    counts_by_verdict = {verdict: 0 for verdict in
              (HOLDER_LIVE, HOLDER_STOPPED, HOLDER_UNMEASURED, HOLDER_AMBIGUOUS)}
    for holder in read.holders:
        counts_by_verdict[holder.verdict] = (
            counts_by_verdict.get(holder.verdict, 0) + 1
        )
    return (
        "claim gate: holder read from the FLEET BOARD (board source: "
        f"{read.source}{counts}) — {len(read.holders)} lane(s) attributed: "
        f"{counts_by_verdict[HOLDER_LIVE]} still holding, "
        f"{counts_by_verdict[HOLDER_STOPPED]} stopped, "
        f"{counts_by_verdict[HOLDER_UNMEASURED]} liveness unmeasured, "
        f"{counts_by_verdict[HOLDER_AMBIGUOUS]} ambiguous (comment prose is not "
        "read, #8175)"
    )


def scan_issue_surface(
    surface: Surface, issue_data: dict, identity: Identity,
    holders: HolderRead,
) -> None:
    """Assignee + HOLDER (#8175).

    TWO readers, and NEITHER of them is prose:

      * the ASSIGNEE login. It carries no lane or session marker, so the rule is
        "can this value distinguish one lane from another?": a DIFFERENT login
        is another party's and blocks; OUR SHARED login is identical for every
        lane and is therefore advisory (see `assignee_attribution`); anything
        unresolvable blocks. The previous rule blocked the shared-account case
        too, which made the repo owner's own triage assignments (86 of 622 open
        issues) permanent false COLLISIONs (#3504 class 3).
      * the HOLDER read (`holders`, produced by `read_holders` from the FLEET
        BOARD — branch slug / worktree path / the lane's own session text).
        `HOLDER_LIVE` BLOCKS; `HOLDER_STOPPED` is reported and does NOT block
        (the board MEASURED liveness and found no live pi process); an UNMEASURED
        liveness (no `pid_alive` reading, or the board's live-session store could
        not be read), an AMBIGUOUS attribution, or a board that could not answer
        at all make this surface INCOMPLETE — never CLEAN.

    ⛔ COMMENT BODIES ARE NOT READ. Not fetched, not matched, not classified —
    `gh issue view` no longer asks for `comments` at all. See the module
    docstring's "Claim gate — the HOLDER, never prose", which also records that
    every attempt at a prose rule there failed in the unsafe direction.
    """
    for assignee in issue_data.get("assignees") or []:
        login = assignee.get("login") if isinstance(assignee, dict) else str(assignee)
        who = assignee_attribution(login, identity)
        if who == "other":
            surface.add(
                f"assignee:{login}",
                "issue is assigned to a DIFFERENT account — affirmatively another "
                "party's claim",
                "strong",
            )
        elif who == "shared":
            surface.add(
                f"assignee:{login}",
                "issue is assigned to the SHARED fleet account — that value is "
                "identical for every lane, so it cannot distinguish one lane from "
                "another; triage, not contention (non-blocking)",
                "weak",
            )
        else:
            surface.add(
                f"assignee:{login}",
                "assignee could not be attributed (no login, a placeholder, or "
                "this tool could not resolve its own login) — counts as a hit "
                "(fail closed)",
                "strong",
            )

    # ── the holder arm ──────────────────────────────────────────────────────
    holder_note = _holder_gate_note(holders)
    # The roster cause travels as DATA beside the note so the exit-2 remedy
    # classifies the board's RECORDED cause instead of re-parsing this prose
    # (#9392 review P2). Set unconditionally: `None` is itself the answer
    # "the board recorded no cause", which is the arm that was unreachable.
    surface.registry_error = holders.registry_error
    for holder in holders.holders:
        if holder.verdict == HOLDER_LIVE:
            surface.add(
                f"holder:{holder.lane}",
                f"lane {holder.lane!r} is attributed to this issue "
                f"({holder.evidence}), and {holder.reason} — a live holder blocks "
                "the dispatch",
                "strong",
            )
    for holder in holders.holders:
        if holder.verdict == HOLDER_STOPPED:
            # REPORTED, never blocking: the note says WHY the board read it as
            # stopped, so a demotion is auditable rather than silent.
            surface.add(
                f"holder:{holder.lane}",
                f"lane {holder.lane!r} is attributed to this issue "
                f"({holder.evidence}) but has STOPPED — {holder.reason} "
                "(reported, non-blocking)",
                "weak",
            )
    unanswered = [
        holder for holder in holders.holders
        if holder.verdict in HOLDER_UNANSWERED
    ]
    problem = ""
    if not holders.measured:
        problem = holder_note
    elif unanswered:
        detail = "; ".join(
            f"lane {holder.lane!r}: {holder.reason}" for holder in unanswered
        )
        problem = f"{holder_note}; {detail}"
    if problem:
        # ⛔ NEVER CLOBBER AN EXISTING REASON. `incomplete()` OVERWRITES `note`,
        # so if a caller passes a surface that is ALREADY INCOMPLETE (today's
        # call site cannot — the two other `incomplete()` sites both leave
        # `issue_data` None, so this function is not called on them — but the
        # invariant is what keeps a future caller from losing the first reason),
        # appending preserves the reason the run is incomplete instead of
        # letting the claim gate's own story make the report misdescribe what it
        # measured.
        if surface.status == STATUS_INCOMPLETE:
            surface.note = f"{surface.note}; ALSO: {problem}"
        else:
            surface.incomplete(problem)
    else:
        surface.note = (surface.note + "; " + holder_note) if surface.note else holder_note

    if not surface.hits:
        state = issue_data.get("state")
        # De-sequenced, like the `issue landed` row: a state carrying an escape
        # sequence must not make THIS row assert a terminal reading about the
        # same value the landed row — which this note points to — measured as
        # OPEN (#8560 review).
        state_norm = (
            _strip_control_sequences(state).strip().upper()
            if isinstance(state, str) else ""
        )
        if state_norm and state_norm != "OPEN":
            # #8560: the terminal FACT itself is now carried by the `issue
            # landed` surface, which decides EXIT_LANDED. This note remains only
            # as a visibility aid on the assignee/holder row; it must not say
            # the closed state is decision-free, because it no longer is.
            note = (
                f"issue state={_sanitize(state)} (the terminal answer for this "
                "state is carried by the `issue landed` surface)"
            )
            surface.note = (surface.note + "; " + note) if surface.note else note


# ── the LANDED answer (#8560) ─────────────────────────────────────────────
# The gate answered only "who is on #N?", never "is #N already done?" — so an
# issue whose fix had ALREADY MERGED could read CLEAN and a lane was dispatched
# to re-implement shipped code (#7174: merged PR #7403 sat outside the bounded
# ADVISORY closed-PR sample; #5035). This section is the authoritative answer.
#
# ⛔ IT IS NOT A SECOND BOUNDED SAMPLE. The closed-PR surface's bounded sample
# cannot prove the ABSENCE of a landing PR, and "absence of evidence" read as
# "free work" is exactly the defect. The timeline is PAGINATED to exhaustion,
# so a merge older than any one page is still reachable.
TIMELINE_PAGE_SIZE = 100


def _timeline_events(gh_bin: str, slug: str | None, cwd: str, timeout: float,
                     issue: int) -> list[dict]:
    """The issue's OWN timeline, PAGINATED to exhaustion (#8560).

    `--slurp` wraps the pages into one JSON array-of-arrays and `--paginate`
    follows every `rel="next"`, so the result is the WHOLE timeline rather than
    a sample. A non-zero exit (a mid-pagination transport failure) RAISES: the
    partial output a failed `--paginate` leaves behind is never salvaged into
    "no landing PR", because that is the fail-OPEN direction this whole section
    exists to close — a landing PR that was not seen would read as free work.

    The path is built from the RESOLVED `owner/name` literally, never from gh's
    `{owner}/{repo}` placeholders (those resolve from the cwd — the #4027
    cross-repo false CLEAN).
    """
    if not slug:
        raise SurfaceError(
            "target-repo-unresolved: no owner/name for the target repo — "
            "refusing to read a landing timeline against an unknown repository"
        )
    args = [
        "api", "--paginate", "--slurp",
        f"repos/{slug}/issues/{issue}/timeline?per_page={TIMELINE_PAGE_SIZE}",
    ]
    rc, out, err, timed_out = _run([gh_bin, *args], cwd, timeout)
    if rc != 0:
        why = "timeout" if timed_out else f"exit {rc}"
        raise SurfaceError(f"gh {' '.join(args)} failed ({why}): {_one_line(err or out)}")
    try:
        pages = json.loads(out)
    except json.JSONDecodeError as exc:
        raise SurfaceError(
            f"gh {' '.join(args)} returned non-JSON: {exc}: {_one_line(out)}"
        ) from exc
    if not isinstance(pages, list):
        raise SurfaceError(
            f"gh {' '.join(args)} returned a non-list body: {_one_line(out)}"
        )
    if not pages:
        # A bare `[]` is never a valid `--paginate --slurp` body: an empty
        # result is `[[]]` (ONE empty page), measured on gh 2.97.0. Read as
        # zero pages it becomes a measured, EMPTY timeline, and the surface
        # then reports a COMPLETE measurement ("0 timeline event(s)") on an
        # input it never read — the fail-OPEN direction this section exists to
        # close. Mirrors `branch_reaper.fetch_prs`, which raises on the same
        # shape for the same reason.
        raise SurfaceError(
            f"gh {' '.join(args)} returned an empty page list — a valid "
            "slurp payload always has at least one page"
        )
    events: list[dict] = []
    for page_index, page in enumerate(pages):
        if not isinstance(page, list):
            raise SurfaceError(
                f"gh {' '.join(args)} page {page_index} is not a list "
                f"({type(page).__name__}) — refusing to read a malformed "
                "timeline as empty (NOT clean)"
            )
        for item in page:
            if not isinstance(item, dict):
                raise SurfaceError(
                    f"gh {' '.join(args)} page {page_index} carries a non-object "
                    f"event ({type(item).__name__}) — refusing to inflate "
                    "`no landing PR` from a shape it cannot read"
                )
            events.append(item)
    return events


def _merged_reference(event: dict) -> tuple[int, str] | None:
    """`(pr_number, title)` for a MERGED pull request this event cross-references.

    Only `cross-referenced` events carry a `source.issue`, and only one whose
    `pull_request.merged_at` is set has actually LANDED. An OPEN cross-reference
    is in-flight work — the open-PR / branch / closed-PR surfaces already judge
    those — so it is deliberately NOT read here as a landing.

    Every field is validated: a malformed event yields `None` (ignored) rather
    than raising, because this predicate runs over the whole timeline and one
    unreadable entry must not make the answer unknowable. The CALLER's contract
    check on the page shapes has already refused a body that is not a list of
    objects.
    """
    if event.get("event") != "cross-referenced":
        return None
    source = event.get("source")
    if not isinstance(source, dict):
        return None
    src_issue = source.get("issue")
    if not isinstance(src_issue, dict):
        return None
    pull = src_issue.get("pull_request")
    if not isinstance(pull, dict) or not pull.get("merged_at"):
        return None
    number = src_issue.get("number")
    title = src_issue.get("title")
    if not _is_issue_number(number) or not isinstance(title, str):
        return None
    return number, title


def _landed_wording(kinds: set[str]) -> str:
    """Classify landed hit kinds into the ONE claim both verbs may make.

    The single source of truth for the terminal wording (#8560 review): the
    gate's verdict AND `tools/who_is_on.py`'s answer derive from this, so the
    two verbs cannot describe the same input differently.

    ⛔ Only `SURFACE_LANDED` hits reach this function, and after #8625 that
    surface is fed by the issue's own state ALONE — so the reachable kinds are
    the closure reasons below. A merged timeline PR is NOT one of them: it is an
    ADVISORY (`merged PRs naming an OPEN issue`) and never gets here. The former
    ``shipped`` kind was removed with that arm; classifying it here again would
    be dead code that reads as a live tier.

      * ``shipment``   — a `COMPLETED` closure WAS observed; the only class that
                         may say the work shipped.
      * ``not-planned``— the only observed fact is a `NOT_PLANNED` closure
                         (declined or superseded); the work did not ship.
      * ``terminal``   — a CLOSED issue whose ``stateReason`` is unavailable or
                         unrecognised. The refusal (do NOT dispatch) holds, but
                         no shipment may be claimed: the tool did not observe
                         one.
    """
    if kinds <= {"not-planned"}:
        return "not-planned"
    if "completed" in kinds:
        return "shipment"
    return "terminal"


def _landed_conclusion(landed_hits: list[Hit]) -> str:
    """The closing clause of the LANDED verdict, worded from the TERMINAL FACT.

    ⛔ A closed issue is not a merged PR (#8560 review). The refusal is the same
    in every case — do NOT dispatch — but the verdict must not state a cause it
    did not observe. `NOT_PLANNED` means the issue was declined or superseded;
    the work did not ship, and "appears ALREADY SHIPPED" is a false diagnosis
    that sends a human looking for a merge that does not exist. A `COMPLETED`
    closure IS a shipment. A merged timeline PR is not a candidate at all since
    #8625: it is an ADVISORY (a reference, not a closure), so it cannot reach
    this function and cannot be named as the cause of a LANDED verdict.
    """
    wording = _landed_wording({hit.kind for hit in landed_hits})
    if wording == "not-planned":
        return (
            "the issue was CLOSED AS NOT PLANNED (superseded or declined) — "
            "the work did not ship, but it is TERMINAL and never free work; "
            "do NOT dispatch"
        )
    if wording == "shipment":
        return "the work appears ALREADY SHIPPED — do NOT dispatch"
    return "the work item is TERMINAL (closed) — do NOT dispatch"


def scan_landed_surface(surface: Surface, unclosed: Surface, issue: int,
                        issue_data: dict,
                        events: list[dict] | None) -> None:
    """The authoritative "is #N already landed?" answer (#8560).

    TWO sources, both aimed at THIS issue:

      1. the ISSUE'S OWN STATE — `gh issue view` already returns it, so it costs
         nothing. `CLOSED` is decisive and terminal: a closed issue is not free
         work, and the tool used to print it as warn-only and return CLEAN.
      2. a MERGED PR, cross-referenced by this issue, whose TITLE names the
         issue number (boundary-exact, `number_present`). `#7174`'s PR #7403
         is exactly this shape. The timeline is paginated, so an older merge is
         reachable where the closed-PR sample is not.

    ⚠️ #8625: source (2) is EVIDENCE, NOT A VERDICT. When the issue is OPEN, a
    merged title-naming PR is reported on `SURFACE_UNCLOSED_REF` (advisory) and
    never decides `LANDED` — a reference is not a closure, and #4886 already
    records that a merged follow-up PR must not block an issue forever.

    `events is None` means the timeline was NOT fetched (state was terminal, so
    it was unnecessary, or the query failed and the caller already marked this
    surface). It is NEVER read as "no landing PR": an OPEN issue with events
    `None` is INCOMPLETE, because the single fact that would have answered the
    question was not measured.

    A state that is neither OPEN nor CLOSED is INCOMPLETE too: the tool cannot
    tell whether the work item is still live, and reading an uninterpretable
    state as free work is the fail-OPEN direction.
    """
    raw_state = issue_data.get("state")
    # Interpret the DE-SEQUENCED state, not merely the byte-sanitised one.
    # `_sanitize` is the DISPLAY helper (and the module's stated contract): it
    # removes the control BYTES only, so an OSC 52 payload survives it as
    # literal text — `CLOSED\x1b[2K\x1b]52;c;AAAA\x07` becomes
    # `CLOSED[2K]52;c;AAAA` — and a real CLOSED would be read as
    # uninterpretable. That is the fail-CLOSED direction but the WRONG verdict:
    # the terminal fact is present and must decide. `_strip_control_sequences`
    # removes the WHOLE sequence (payload included) for MATCHING, so the same
    # input still reads as CLOSED. See
    # `test_control_sequences_in_issue_state_are_stripped`.
    normalised = (
        _strip_control_sequences(raw_state).strip().upper()
        if isinstance(raw_state, str) else ""
    )
    if normalised == "CLOSED":
        # ⛔ THE REASON DECIDES THE WORDING, never the direction (#8560 review).
        # `CLOSED` is decisive either way — a closed issue is never free work —
        # but an issue parked as `NOT_PLANNED` was DECLINED or SUPERSEDED; its
        # work did not ship. Reporting it as "the work appears ALREADY SHIPPED"
        # invents a merge that never happened (measured live: #8497,
        # `state_reason=not_planned`, read as ALREADY SHIPPED). The module holds
        # its refusals to the reason that actually caused them (#2573).
        reason_raw = issue_data.get("stateReason")
        reason = (
            _strip_control_sequences(reason_raw).strip().upper().replace("-", "_")
            if isinstance(reason_raw, str) else ""
        )
        if reason == "NOT_PLANNED":
            detail = (
                f"issue state={_sanitize(raw_state)} (closed as NOT PLANNED) — "
                "the work item is TERMINAL: the issue was declined or superseded, "
                "NOT shipped; it is never free work"
            )
            kind = "not-planned"
        elif reason == "COMPLETED":
            detail = (
                f"issue state={_sanitize(raw_state)} (closed as COMPLETED) — the "
                "work item is TERMINAL; a closed issue is never free work"
            )
            kind = "completed"
        else:
            # An unavailable/unrecognised reason is NOT read as "shipped": the
            # refusal (do not dispatch) is identical and never depends on it.
            detail = (
                f"issue state={_sanitize(raw_state)} — the work item is TERMINAL; "
                "a closed issue is never free work"
            )
            kind = "closed"
        surface.add(f"issue #{issue}", detail, "strong", kind)
        return
    if normalised != "OPEN":
        surface.incomplete(
            f"issue-state-uninterpretable: state={_sanitize(raw_state)!r} is "
            "neither OPEN nor CLOSED, so whether this work item is still live "
            "cannot be established — refusing to read it as free (NOT clean)"
        )
        return
    if events is None:
        surface.incomplete(
            "issue-timeline-unmeasured: the targeted landing query did not run "
            "for this OPEN issue, so a merge older than the closed-PR sample "
            "cannot be ruled out (NOT clean)"
        )
        return
    merged_refs = 0
    naming: list[tuple[int, str]] = []
    for event in events:
        found = _merged_reference(event)
        if found is None:
            continue
        merged_refs += 1
        number, title = found
        if number == issue:
            # The PR IS the issue number: that is the #7009 wrong-object input,
            # refused elsewhere; it is not evidence of a landing.
            continue
        if number_present(title, issue):
            naming.append((number, title))
    for number, title in naming:
        # #8625: NOT a landing. The issue is OPEN and a reference is not a
        # closure — GitHub closes an issue only on a closing keyword.
        unclosed.add(
            f"PR #{number}",
            "MERGED PR names this issue in its TITLE while the issue is still "
            "OPEN — a reference is not a closure, so this is NOT a landing: "
            "verify against the artifact, then either close the issue or state "
            f"its remaining scope: {_one_line(title, 90)}",
            "strong",
            "unclosed",
        )
    if naming:
        surface.note = (
            f"issue state=OPEN; {len(naming)} merged PR(s) name this issue in "
            "their title — a reference is not a closure; reported as an advisory "
            f"on `{SURFACE_UNCLOSED_REF}`, NOT as a landing"
        )
    elif not surface.hits:
        surface.note = (
            f"issue state=OPEN; {len(events)} timeline event(s), "
            f"{merged_refs} merged cross-reference(s), none naming #{issue} "
            "in its title"
        )


# ── target-repo resolution (#4027) ──────────────────────────────────────────

def _valid_slug(slug: str | None) -> str | None:
    """`owner/name` or None. Remote-derived slugs are UNTRUSTED: a git remote
    URL (or gh output) can carry terminal control sequences or path junk, and
    the slug is printed in the report. A slug that is not EXACTLY the GitHub
    slug shape is rejected, leaving its surfaces INCOMPLETE — never printed and
    never used to reach gh."""
    if slug and REPO_SLUG_RE.fullmatch(slug):
        return slug
    return None


def _remote_slug(git_bin: str, path: str, timeout: float) -> str | None:
    """`owner/name` from the repo's git remote, OFFLINE.

    Preferred over `gh repo view` because it is deterministic, needs no network,
    and works identically in a linked worktree. Only gitlab/github-style
    `host:owner/name` and `host/owner/name` URLs are understood; anything else
    falls through to the gh fallback. The result is validated with
    `REPO_SLUG_RE` (`_valid_slug`) because it is printed and sent to gh.
    """
    rc, out, _err, _to = _run([git_bin, "remote", "-v"], path, timeout)
    if rc != 0:
        return None
    urls: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            urls.append((parts[0], parts[1]))
    for name, url in urls:  # prefer `origin`
        if name != "origin":
            continue
        m = re.search(r"[/:]((?:[^/]+))/([^/\s]+?)(?:\.git)?$", url)
        if m and m.group(2):
            return _valid_slug(f"{m.group(1)}/{m.group(2)}")
    for _name, url in urls:
        m = re.search(r"[/:]((?:[^/]+))/([^/\s]+?)(?:\.git)?$", url)
        if m and m.group(2):
            return _valid_slug(f"{m.group(1)}/{m.group(2)}")
    return None


def _gh_slug(gh_bin: str, path: str, timeout: float) -> str | None:
    """`owner/name` from gh itself (fallback when there is no usable remote).
    Validated like every other remote-derived slug before it is printed."""
    rc, out, _err, _to = _run(
        [gh_bin, "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        path, timeout,
    )
    if rc == 0 and out.strip():
        return _valid_slug(out.strip())
    return None


def resolve_slug(gh_bin: str, git_bin: str, path: str, timeout: float,
                 explicit: str | None = None) -> tuple[str | None, str]:
    """Resolve the GitHub `owner/name` for a local checkout."""
    if explicit:
        return explicit, "--repo"
    slug = _remote_slug(git_bin, path, timeout)
    if slug:
        return slug, "git remote"
    slug = _gh_slug(gh_bin, path, timeout)
    if slug:
        return slug, "gh repo view"
    return None, "unresolved"


def _main_worktree_root(git_bin: str, path: str, timeout: float) -> str | None:
    """The MAIN worktree's root for a (possibly linked) worktree.

    `--git-common-dir` points at the shared `.git`, so its parent is the main
    checkout even when `path` is a linked worktree under `.worktrees/`. This is
    what makes sibling-repo discovery work from a worktree, where the mere
    parent directory would list the repo's OWN worktrees.
    """
    rc, out, _err, _to = _run(
        [git_bin, "rev-parse", "--path-format=absolute", "--git-common-dir"],
        path, timeout,
    )
    if rc != 0 or not out.strip():
        return None
    common = Path(out.strip())
    return str(common.parent) if common.name == ".git" else str(common)


def repo_roots(git_bin: str, path: str, timeout: float) -> list[str]:
    """Directories scanned for sibling repos (for clone lookup + ambiguity)."""
    override = os.environ.get(REPO_ROOTS_ENV, "").strip()
    if override:
        return [p for p in override.split(os.pathsep) if p]
    root = _main_worktree_root(git_bin, path, timeout) or path
    parent = Path(root).parent
    return [str(parent)] if parent.is_dir() else []


def _git_repo_dirs(roots: list[str]) -> list[str]:
    """Directories under `roots` that look like local clones.

    BOTH levels are guarded: an unreadable ROOT is skipped, and so is an
    unreadable ENTRY. `Path.is_dir()`/`Path.exists()` swallow
    ENOENT/ENOTDIR/EBADF/ELOOP but NOT `EACCES`, so a sibling that is stat-able
    but not traversable — a root-owned `drwx------` dir under a shared `/tmp`,
    which exists on a cmux cloud cell (`/tmp/cmux-blesh-runtime-0`) and not on a
    dev box — raised straight out of the `.git` probe and aborted the whole
    scan (#8650). On the `--repo owner/name` path that killed
    `find_local_clone` and exited 1, the COLLISION code, with EMPTY stdout and
    no `VERDICT:` line — so a collision was reported with no surface named,
    where the designed outcome is a verdict. The same walk backs
    `who_is_on --repo <slug>`.

    Scope note: the controller's `refresh-candidates.sh` invokes the pre-flight
    as `--repo .`, which is the PATH branch of `_resolve_target`; it never
    reaches `find_local_clone`, so it was NOT exposed to this crash (measured —
    the pre-fix tool exits 0 with a `VERDICT:` on that form).

    Skipping is lossless for clone DISCOVERY in the `EACCES` case, which is the
    one observed on the cell. `EACCES` here means the process lacks SEARCH
    permission on `entry`, and every consumer of a returned path then runs
    `git -C <entry>` (`_remote_slug`), which needs the same search permission to
    `chdir`. An entry this probe cannot read therefore cannot yield a slug,
    cannot appear in `candidate_slugs`, and cannot be returned by
    `find_local_clone` — it is never a clone "the surface would have found".

    A non-`EACCES` `OSError` (a transient EIO/ENOMEM on a network mount, say) is
    DELIBERATELY treated the same way, by the recorded decision on #8650 that
    only `OSError` is swallowed; for those the probe may fail where a later
    `git -C <entry>` would have succeeded. Treating them as "not a clone" is
    fail-closed downstream (`find_local_clone` returns None, which drives the
    `no-local-clone` INCOMPLETE reason, exit 2), never a silent CLEAN.

    Only `OSError` is swallowed; a non-OSError failure still propagates.
    """
    dirs: list[str] = []
    for root in roots:
        try:
            entries = sorted(Path(root).iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir() and (entry / ".git").exists():
                    dirs.append(str(entry))
            except OSError:
                continue
    return dirs


def candidate_slugs(git_bin: str, timeout: float, roots: list[str]) -> list[str]:
    """Distinct `owner/name` slugs of the local sibling repos, in stable order."""
    slugs: list[str] = []
    for directory in _git_repo_dirs(roots):
        slug = _remote_slug(git_bin, directory, timeout)
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def find_local_clone(selector: str, git_bin: str, timeout: float,
                     roots: list[str]) -> str | None:
    """A local checkout of `selector`, so the git surfaces describe the TARGET
    repo rather than whatever directory happens to be the cwd. The slug
    comparison is CASE-INSENSITIVE: GitHub and `gh --repo` accept
    `Owner/Name`, so a case-different selector must find the clone rather than
    leaving every git surface INCOMPLETE (a false exit 2)."""
    want = selector.strip().lower()
    for directory in _git_repo_dirs(roots):
        slug = _remote_slug(git_bin, directory, timeout)
        if slug and slug.lower() == want:
            return directory
    return None


def probe_issue_in_repo(gh_bin: str, slug: str, issue: int, cwd: str,
                        timeout: float) -> bool | None:
    """True if #N resolves in `slug`; False if definitively absent; None if the
    repo could not be queried at all (transport/auth) — never treated as
    absent, because an unqueryable repo may be exactly where the issue lives."""
    rc, out, err, _to = _run(
        [gh_bin, "issue", "view", str(issue), "--repo", slug, "--json", "number"],
        cwd, timeout,
    )
    if rc == 0:
        return True
    blob = f"{err}\n{out}".lower()
    if "could not resolve to an issue" in blob or "could not resolve to a pull request" in blob:
        return False
    if "could not resolve to a repository" in blob:
        # A local remote pointing at a repo GitHub does not have (deleted,
        # renamed, private-to-someone-else): not a candidate at all.
        return False
    return None


# ── orchestration ────────────────────────────────────────────────────────────

def _classify_issue_error(exc: SurfaceError) -> str:
    blob = str(exc).lower()
    if "could not resolve to an issue" in blob or "could not resolve to a pull request" in blob:
        return "issue-absent"
    if "could not resolve to a repository" in blob:
        return "repo-unresolved"
    if "not found" in blob:
        return "issue-absent"
    return "transport"


def run_preflight(
    issue: int,
    target: RepoTarget,
    gh_bin: str,
    git_bin: str,
    timeout: float,
    open_pr_limit: int = PR_LIMIT,
    closed_pr_limit: int = CLOSED_PR_LIMIT,
    closed_pr_timeout: float = CLOSED_PR_TIMEOUT,
    identity: Identity | None = None,
) -> tuple[list[Surface], str | None, int]:
    identity = identity or Identity()
    surfaces: dict[str, Surface] = {
        name: Surface(
            name,
            authority=(
                AUTHORITY_ADVISORY if name in ADVISORY_SURFACES
                else AUTHORITY_BLOCKING
            ),
        )
        for name in ALL_SURFACES
    }
    cwd = target.path or os.getcwd()
    slug = target.slug

    # 1. Issue metadata FIRST — it is a surface in its own right (assignee +
    #    holder), and its title is what the report prints in full so a
    #    wrong-target read is visible at the point of use (#4027). A failure here
    #    leaves THAT surface INCOMPLETE and does not silently degrade anything
    #    else: the title used to feed the keyword arm, and that arm is gone.
    #
    #    The target repo (`slug`) is sent EXPLICITLY on every gh call. If it
    #    cannot be resolved the run is INCOMPLETE rather than inferred from the
    #    cwd: querying "whatever repo this directory happens to be" is exactly
    #    the cross-repo false CLEAN of #4027.
    issue_data: dict | None = None
    title: str | None = None
    if slug is None:
        surfaces[SURFACE_ISSUE].incomplete(
            "target-repo-unresolved: no owner/name could be derived for the "
            f"target repo (source={target.source}) — refusing to query gh against "
            "an unknown repository (NOT clean)"
        )
    else:
        try:
            data = _gh_json(
                gh_bin,
                ["issue", "view", str(issue), "--repo", slug, "--json",
                 "number,title,state,stateReason,assignees,url"],
                cwd, timeout,
            )
            if isinstance(data, dict):
                # Before anything reads it: a malformed payload must become a
                # NAMED INCOMPLETE, never a traceback (exit 1 == COLLISION) and
                # never a silent empty read (fail-open).
                _require_issue_payload(data, f"gh issue view #{issue} in {slug}")
                issue_data = data
                title = data.get("title") or None
            else:
                raise SurfaceError("gh issue view returned non-object JSON")
        except SurfaceError as exc:
            # ONLY a budget exhaustion is eligible for the REST fallback (see
            # `_is_budget_exhaustion`). Any other failure is a genuine "could not
            # read", and rescuing it from a second endpoint would turn a real
            # refusal into a reading — the fail-open direction. ⛔ The EXCEPTION
            # is passed, not `str(exc)`: the message embeds a display-cut
            # stderr, and the predicate must classify the WHOLE of it
            # (#9402 review, P1).
            _exhausted = _is_budget_exhaustion(exc)
            # ⛔ THE THIRD OF THE THREE RESCUE SITES (#9423 review round 12, P3).
            # The flag is set where the exhaustion is the cause OF THE INCOMPLETE —
            # i.e. on the FAILED-rescue branch below — and NOT here (#9423 review
            # round 13, P3). Set before the attempt, it was also True on the
            # SUCCESSFUL-rescue path, where the surface is left CLEAN here and can
            # later go INCOMPLETE for an unrelated reason (the holder gate's "the
            # fleet board could not answer"). `_incomplete_remedy` classifies on the
            # flag, so the run then printed "the cause is a GRAPHQL BUDGET
            # EXHAUSTION … re-run when it resets" while the row above named the
            # board — a remedy naming a cause the same report contradicts, which is
            # #9402's own defect in a narrower corner.
            rescued: dict | None = None
            rest_why: str | None = None
            if _exhausted:
                rescued, rest_why = _issue_view_rest(
                    gh_bin, slug, issue, cwd, timeout
                )
            if rescued is not None:
                issue_data = rescued
                title = rescued.get("title") or None
            else:
                # The REST attempt ALREADY refused for its own reason (a
                # transport failure, a 404, a non-object body, or a payload that
                # breaks the issue contract — all returned as `rest_why`, never
                # raised: a raise here would escape this handler as a traceback
                # and exit 1, which this tool defines as "another lane is on it").
                # Classify THAT, so an issue REST reports absent is named
                # `issue-absent` rather than being blamed on the GraphQL budget,
                # while the budget exhaustion stays visible in the reason.
                # ⛔ BUILT FROM THE UNTRUNCATED CAUSE (#9423 review, P3): this
                # string becomes the surface NOTE, and `_incomplete_remedy`
                # classifies the note — so the 200-char display message meant a
                # long stderr could hide the signature here too and the remedy
                # then named `gh auth/network` for a spent budget.
                detail = exc if rest_why is None else SurfaceError(
                    _rest_refusal_note(rest_why, exc)
                )
                kind = _classify_issue_error(detail)
                # ⛔ THIS BRANCH IS NOT EXHAUSTION-ONLY (#9423 review round 14,
                # P2). `if _exhausted:` above guards only the REST ATTEMPT; this
                # `else` is also reached when `_exhausted` is False and no rescue
                # was tried at all — a plain transport/auth failure (`connection
                # reset`, `HTTP 401`, a missing fixture), whose `kind` is
                # "transport". Flagging THAT as a budget exhaustion made a broken
                # credential print "gh auth is FINE (re-authenticating cannot
                # help) … re-run when the budget resets", steering the operator
                # away from the real cause — #9402's own defect, reintroduced in a
                # corner. So the predicate that led here is the one that gates the
                # flag.
                if _exhausted and kind not in ("issue-absent", "repo-unresolved"):
                    surfaces[SURFACE_ISSUE].budget_exhausted = True
                if kind == "issue-absent":
                    msg = (
                        f"issue-absent: #{issue} does not exist in {slug} — "
                        "\"not found here\" is NOT \"no in-flight work\" (fail closed)"
                    )
                elif kind == "repo-unresolved":
                    msg = f"repo-unresolved: {slug} could not be resolved"
                else:
                    msg = f"gh-unavailable: {detail}"
                surfaces[SURFACE_ISSUE].incomplete(msg)
    # 1.5 THE LANDED ANSWER (#8560) — the authoritative "is #N already done?".
    #     Scanned HERE, right after the issue metadata (whose `state` it reads)
    #     and BEFORE the in-flight surfaces, so its verdict is decided even if
    #     every other surface later fails. It is a BLOCKING surface: an
    #     unqueryable timeline is INCOMPLETE, never CLEAN. The paginated timeline
    #     query is SKIPPED only when the issue's own state already answers the
    #     question — a CLOSED issue is terminal — so a landed-via-state run costs
    #     no extra API call, while an OPEN issue always pays the one targeted,
    #     paginated query that reaches a merge older than the closed-PR sample.
    landed_surface = surfaces[SURFACE_LANDED]
    if slug is None:
        landed_surface.incomplete(
            "target-repo-unresolved: no owner/name for the target repo — "
            "refusing to read a landing timeline against an unknown repository "
            "(NOT clean)"
        )
    elif issue_data is None:
        landed_surface.incomplete(
            "issue-metadata-unavailable: the issue's own state could not be "
            "read, so neither it nor its timeline could be consulted (NOT clean)"
        )
    else:
        _state = (issue_data.get("state") or "")
        # De-sequenced, not byte-sanitised (see `scan_landed_surface`): this
        # same value decides BOTH whether the timeline is fetched here and the
        # scan's verdict, so the two must not disagree about "OPEN".
        _state_norm = (
            _strip_control_sequences(_state).strip().upper()
            if isinstance(_state, str) else ""
        )
        timeline: list[dict] | None = None
        if _state_norm == "OPEN":
            try:
                timeline = _timeline_events(gh_bin, slug, cwd, timeout, issue)
            except SurfaceError as exc:
                landed_surface.incomplete(f"gh-unavailable: {exc}")
        # Do NOT re-scan a surface that already failed: the scan would overwrite
        # the gh-unavailable reason with a generic "timeline unmeasured" note,
        # and the report would blame the wrong thing.
        if landed_surface.status != STATUS_INCOMPLETE:
            scan_landed_surface(landed_surface, surfaces[SURFACE_UNCLOSED_REF],
                                issue, issue_data, timeline)

    # ⛔ #8629 review (F2). The ADVISORY `merged PRs naming an OPEN issue` surface
    # is fed by the SAME timeline read the BLOCKING landed surface performs (and,
    # for an uninterpretable issue state, by the same state read). So every path
    # that leaves the landing question unmeasured — an unresolved repo, unreadable
    # issue metadata, a failed timeline query, or an issue state that is neither
    # OPEN nor CLOSED — also leaves the advisory surface UNMEASURED. Reporting it
    # as a clean `0` was a printed ABSENCE for a read that never happened, the same
    # defect class the report site already guards for `recently-closed PRs`. It is
    # ADVISORY, so this can never force INCOMPLETE nor change the exit code: it
    # only makes the row render "could not be queried" instead of a false `0`.
    #
    # ⛔ The CLOSED arm is deliberately NOT this case. A CLOSED issue has no
    # unclosed reference BY DEFINITION, so that path IS measured (its advisory
    # count is a true zero) and must stay clean — marking it incomplete here would
    # manufacture exactly the false "could not be queried" this guard removes.
    if (landed_surface.status == STATUS_INCOMPLETE
            and surfaces[SURFACE_UNCLOSED_REF].status != STATUS_INCOMPLETE):
        surfaces[SURFACE_UNCLOSED_REF].incomplete(
            landed_surface.note
            or "the landing query did not complete, so the timeline this surface "
               "shares with it was never read"
        )

    # NOTE: the issue surface (assignee + holder) is scanned LATER, after the
    # git surfaces — see the #8175 note at its call site. ⛔ The ordering is
    # HISTORICAL and carries NO dependency: the holder read comes from the FLEET
    # BOARD, not from any local git surface, and the former reason here ("so the
    # claim gate can re-verify the artifacts a claim names") describes a
    # re-verification that #8175 DELETED. Do not preserve the order for it.

    # The keyword dimension is GONE (#3504) — see the note where the stoplists
    # used to live. Nothing is derived from the title any more; `title` is kept
    # because the report prints it in full, which is what makes a wrong-target
    # read visible at the point of use (#4027).

    # 2. PR surfaces. Open PRs are enumerated to COMPLETENESS over
    #    `gh pr list` (one GraphQL request, fetched as `--limit cap+1`), where
    #    hitting the cap marks the surface TRUNCATED — which keeps the run out
    #    of CLEAN. That is the BLOCKING path and its posture is unchanged.
    #    ⛔ "OVER `gh pr list`" IS NOT THE WHOLE STORY (#9402): that read is
    #    GraphQL, the GraphQL budget is fleet-shared, and when it is EXHAUSTED
    #    the enumeration is RESCUED over REST (`gh api repos/<slug>/pulls`, see
    #    the `via_rest` block below). The rescue does not loosen the posture —
    #    the surface still ends INCOMPLETE (the computed closing-reference test
    #    is GraphQL-only and stays unmeasured), so CLEAN is unreachable either
    #    way — but it does mean the open-PR list below can arrive from REST.
    #    Closed PRs are the ADVISORY surface: ONE bounded request (`api -i`, no
    #    `--paginate`) whose own `Link` header supplies the total, so a partial
    #    sample stays visible without an unbounded, rate-limit-blowing
    #    enumeration (#5251). The `--search` filter is deliberately NOT used
    #    (the search API silently caps at 1000 results — a partial query wearing
    #    a complete face — and `search/issues` cannot return `head.ref`).
    # Head SHAs of MERGED PRs → the PR NUMBER that landed them, harvested from the
    # closed-PR sample this run already fetches (`number`, `head.sha` and
    # `merged_at` are all on the REST payload).
    #
    # ⛔ IT IS A `sha -> number` MAP, NOT A SET (#10174), because the demotion has to
    # NAME the pull request it rests on. A set can say "some merged PR's head is this
    # tip"; only the number lets the reader verify the landing and clean the ref up,
    # and the number is on the SAME payload the set was built from — so naming it
    # costs nothing. The read is unchanged in every other respect: an empty or failed
    # sample still leaves the map empty and every branch BLOCKING.
    #
    # The branch surface uses this for the squash-merge test (#5186) at ZERO added
    # API cost — decision D4, which is why that test reuses this map instead of
    # issuing a `gh pr list --head <ref>` per branch. ⛔ D4's ZERO-COST CONSTRAINT IS
    # AMENDED TWICE, and this comment is the place it would otherwise be lost:
    #   * #5485 — the TARGETED lookup below DOES issue REST calls for local refs that
    #     match the issue number AND survive every predicate: ONE bounded list
    #     request, plus one `compare` per MERGED PR it returns (up to the page cap),
    #     so normally 0-2 and never a call per branch. A failure aborts that ref's
    #     whole lookup and is COUNTED, so the ref keeps blocking (fail-closed).
    #   * #10174 — ONE `repos/<slug>/branches/<name>` request, paid only for a ref the
    #     merge history has ALREADY demoted, to say whether the remote counterpart is
    #     gone, still there, and AT WHICH TIP. One per DEMOTED REF (several demoted
    #     refs cost several reads; never one per local branch), and note-only: a
    #     failure neither demotes nor un-demotes (see `_remote_branch_state`).
    merged_heads: dict[str, int | None] = {}
    #: Did the harvest actually RUN? `false` means the closed-PR sample could not be
    #: read, so predicate 1 had no witness at all — which the branch surface's note
    #: must SAY, because "no merged PR matches this tip" and "the merged-PR record
    #: could not be read" are different facts about a ref that keeps blocking.
    merged_heads_readable = False
    for surface_name, state, limit in (
        (SURFACE_OPEN_PRS, "open", open_pr_limit),
        (SURFACE_CLOSED_PRS, "closed", closed_pr_limit),
    ):
        surface = surfaces[surface_name]
        if slug is None:
            surface.incomplete(
                "target-repo-unresolved: no owner/name for the target repo — "
                "refusing to enumerate PRs against an unknown repository "
                "(NOT clean)"
            )
            continue
        try:
            if state == "closed":
                # ONE request, and the response's own Link header supplies the
                # total — so a partial sample stays OBSERVABLE, never silent
                # (#5251). This surface is ADVISORY: it cannot block, and its
                # partiality must not set INCOMPLETE (see ADVISORY_SURFACES and
                # the advisory-aware accounting in `format_report`).
                prs, approx_total, partial = _closed_pr_sample(
                    gh_bin, slug, cwd, closed_pr_timeout, limit,
                )
                # Before the harvest AND the scan, so a malformed element cannot
                # reach `.get()` on either path.
                _require_pr_dicts(prs, f"the closed-PR sample for {slug}",
                                  required=("headSha",))
                for _pr in prs:
                    if _pr.get("merged_at") or _pr.get("mergedAt"):
                        # `headSha` is the PROJECTED key this sample actually
                        # carries (see `_closed_pr_sample`'s jq filter). Reading
                        # a nested `head.sha` here was a silent no-op: the
                        # projection never emits it, so the map stayed empty and
                        # the squash-merge test never ran in production.
                        _sha = _pr.get("headSha")
                        if _sha:
                            # `number` rides along in the SAME projection and is
                            # what makes the demotion NAME its PR (#10174). A
                            # payload whose number is absent still demotes — the
                            # head SHA is the load-bearing datum, not the label.
                            _number = _pr.get("number")
                            merged_heads[str(_sha)] = (
                                _number if isinstance(_number, int) else None
                            )
                merged_heads_readable = True
                # ⛔ THIS ADVISORY SURFACE'S DATA FEEDS A BLOCKING ONE, and the
                # authority split does not forbid it — the split is about which
                # surface can BLOCK, not about where evidence comes from. The
                # `merged_heads` harvested just above (from the closed-PR
                # sample, authority ADVISORY) are passed to the BLOCKING branch
                # and worktree scans, where predicate 1 uses them to DEMOTE a hit
                # to `weak` — so advisory data can turn COLLISION into CLEAN.
                #
                # That is deliberate (D4: the sample is already fetched, so the
                # squash-merge test costs zero API calls) and it is safe in
                # exactly one direction:
                #
                #   * it can only ever REMOVE a hit, never add one — every entry
                #     is an exact head SHA from a MERGED PR record, and a failed
                #     or empty sample leaves the branches BLOCKING;
                #   * so a failed closed-PR sample is fail-CLOSED, which is
                #     asserted by
                #     `test_branch_still_blocks_when_the_closed_pr_sample_fails`.
                #
                # Keep it that way: this coupling is the one place where the
                # advisory/blocking split is not structural, and it is the only
                # reason the report's "ONLY a BLOCKING surface can decide the
                # verdict" is a statement about BLOCKING, not about evidence.
                #
                # `use_closing_field=False`: this payload is REST `/pulls`, which
                # has no `closingIssuesReferences`. Every hit here is advisory.
                scan_pr_surface(surface, prs, issue, identity,
                                use_closing_field=False)
                if partial:
                    shown = (
                        f"~{approx_total}" if approx_total is not None
                        else "an unreadable total"
                    )
                    surface.mark_truncated(
                        f"sampled the most recent {len(prs)} of {shown} "
                        "closed PR(s) in ONE request; the remainder were NOT "
                        "scanned. This surface is ADVISORY — the sample cannot "
                        "block a dispatch, and its partiality does NOT make the "
                        "run INCOMPLETE. The sample size is bounded by GitHub's "
                        "per_page maximum (100), so a repo with more closed PRs "
                        "than that can never show this surface complete — that "
                        "is the point of the bound, not a setting to widen."
                    )
                else:
                    surface.note = (
                        f"{len(prs)} closed PR(s) in one request "
                        "(this page is the complete list)"
                    )
                continue
            # `closingIssuesReferences` is requested explicitly: it is GitHub's
            # own computed field (#3504), and `scan_pr_surface` REQUIRES it on
            # this blocking surface — a missing key is INCOMPLETE, never
            # "closes nothing".
            args = ["pr", "list", "--state", state, "--repo", slug,
                    "--limit", str(limit + 1),
                    "--json",
                    "number,title,body,headRefName,state,url,mergedAt,"
                    "closingIssuesReferences"]
            # #9402: the OPEN-PR read is GraphQL-only and the GraphQL budget is
            # shared by the whole fleet. When it is spent, this surface — the one
            # that BLOCKS a duplicate dispatch — could not be queried at all, so
            # every candidate read exit 2 and no lane could pass the mandated
            # gate. A budget exhaustion is rescued over REST exactly as issue
            # metadata already is (#3435), which recovers every test that reads a
            # field REST carries. What it CANNOT recover is the computed closing
            # reference (`closingIssuesReferences` has no REST equivalent), so the
            # surface is still INCOMPLETE — but with a reason that names the
            # unmeasured test instead of a blanket "gh unavailable", and a REAL
            # collision (title / head ref / number) now decides exit 1 instead of
            # being swallowed by it.
            via_rest = False
            rest_truncated = False
            rest_cause = ""
            try:
                prs = _gh_json(gh_bin, args, cwd, timeout)
            except SurfaceError as exc:
                if state != "open" or not _is_budget_exhaustion(exc):
                    raise
                prs, rest_truncated, rest_why = _open_prs_rest(
                    gh_bin, slug, cwd, timeout, limit
                )
                if prs is None:
                    # The REST attempt refused for ITS OWN reason — carry both, so
                    # the budget exhaustion stays visible without being blamed for
                    # a transport failure it did not cause.
                    # ⛔ AND CARRY IT UNTRUNCATED (#9423 review, P3). This raise
                    # is turned into the surface note by the per-surface handler
                    # (`gh-unavailable: {exc}`), and `_incomplete_remedy`
                    # classifies THAT note: composing it from `str(exc)` — the
                    # 200-char display message — dropped the budget signature
                    # whenever `gh` chattered past the cut, so the run printed
                    # `fix gh auth/network` for a spent budget on the one branch
                    # that did not carry the cause forward.
                    # ⛔ AND THE CAUSE IS RECORDED AS DATA HERE TOO (#9423 review
                    # round 11, P3). The successful-rescue branch sets
                    # `surface.budget_exhausted`, but this FAILED-rescue branch —
                    # the other one that knows the cause, since it is inside the
                    # same `_is_budget_exhaustion` gate — still left the remedy to
                    # re-parse the note, and the note embeds the gh error after
                    # `_one_line(..., limit=_REFUSED_READ_NOTE_LIMIT)`'s 600-char
                    # cut. Both branches that KNOW must say so the same way, or the
                    # asymmetry just moves the hazard to the rarer path.
                    surface.budget_exhausted = True
                    refusal = _rest_refusal_note(rest_why, exc)
                    raise SurfaceError(refusal, detail=refusal) from exc
                via_rest = True
                # ⛔ THE NOTE CARRIES THE UNTRUNCATED CAUSE (#9402 review, P1).
                # `_incomplete_remedy` classifies `s.note` with the SAME
                # predicate, so embedding the 200-char DISPLAY message here
                # meant a long stderr could hide the signature from the remedy
                # too — and the run then printed `gh auth/network` for a spent
                # budget on the very surface the rescue had just read. A note
                # is prose, not a terminal row, so it gets the wider cut.
                rest_cause = _one_line(
                    exc.detail or str(exc), limit=_REFUSED_READ_NOTE_LIMIT
                )
            if not isinstance(prs, list):
                raise SurfaceError(f"gh {state}-PR enumeration returned non-list JSON")
            _require_pr_dicts(prs, f"the {state}-PR enumeration for {slug}")
            if via_rest and rest_truncated:
                # ⛔ THE PAGE SIZE IN THE NOTE IS THE ONE THE REQUEST ASKED FOR.
                # This note is the ONLY truncation note the REST path can emit
                # (`len(prs) > limit` below is unreachable there by construction:
                # `len(prs) <= per_page <= limit`), so a hardcoded "GitHub's
                # maximum of 100" would be the sole thing a human reads — and it
                # is FALSE whenever `--pr-limit` is under 100, where the actual
                # page was smaller and the remedy is the very `--pr-limit` flag
                # the sibling note names. The size comes from the same helper the
                # request used, so the two cannot drift.
                page = _rest_pulls_page_size(limit)
                # ⛔ THE PAGE SIZE IS ITS OWN CLAUSE, AND ITS ASIDE STAYS INSIDE
                # IT (#9423 review, P3). It used to be spliced straight into
                # "… and returned a FULL page", so the aside — "no larger page
                # can be requested" — swallowed the sentence's verb and the note
                # read as though a LARGER page had been "requested and returned":
                # the opposite of what it measures. The page clause now closes
                # on itself (`;`) and the measurement is stated once, after it.
                if page < _REST_PULLS_PAGE:
                    pagesize = (
                        f"per_page of {page} — less than GitHub's maximum "
                        f"{_REST_PULLS_PAGE}, because --pr-limit asked for {page}"
                    )
                    remedy = (
                        " Widen with --pr-limit (or "
                        "COLLISION_PREFLIGHT_PR_LIMIT)."
                    )
                    widen = True
                else:
                    pagesize = (
                        f"per_page of {_REST_PULLS_PAGE}, GitHub's maximum, so no "
                        "larger page could be requested"
                    )
                    remedy = ""
                    # ⛔ NO WIDENING EXISTS HERE, SO THE REMEDY MUST NOT OFFER ONE
                    # (#9423 review, P3). `--pr-limit` and `PR_LIMIT` do not
                    # bound this request — `_rest_pulls_page_size` caps it at
                    # GitHub's maximum — so this flag is what tells
                    # `_incomplete_remedy` to state the cap instead of
                    # prescribing a flag that cannot change the answer.
                    widen = False
                surface.mark_truncated(
                    "the REST fallback answered ONE page at the requested "
                    # ⛔ THE ASIDE IS CLOSED BEFORE THE MAIN VERB RESUMES. The
                    # tail used to be `and returned a FULL page`, so the
                    # parenthetical swallowed it: the report said a LARGER page
                    # "can be requested and returned", i.e. the opposite of what
                    # happened, in the one line a human reads at the point of
                    # refusal (#9423 review, P3).
                    f"{pagesize} — and returned a FULL page, so "
                    "more open PR(s) may exist and were NOT scanned — this "
                    "surface is INCOMPLETE (never CLEAN)." + remedy,
                    widen=widen,
                )
            if len(prs) > limit:
                surface.mark_truncated(
                    f"truncated at the {limit} cap — more than {limit} {state} "
                    f"PR(s) exist and were NOT scanned; this surface is "
                    "INCOMPLETE (never CLEAN). Widen with --pr-limit "
                    "(or COLLISION_PREFLIGHT_PR_LIMIT)."
                )
                prs = prs[:limit]
            try:
                scan_pr_surface(surface, prs, issue, identity,
                                use_closing_field=not via_rest)
            except SurfaceError as exc:
                # A missing/malformed closingIssuesReferences would DROP a
                # blocking signal, so it is INCOMPLETE rather than a silent
                # pass. Raised from inside the scan, caught here so the other
                # surfaces still report.
                surface.incomplete(
                    f"closing-reference-source-unavailable: {exc}"
                )
            if via_rest and surface.status != STATUS_INCOMPLETE:
                # The rescue READ the list, so the title / head-ref / number tests
                # all ran — but the computed closing-reference test did not, and
                # absence of the key is UNMEASURED, not "closes nothing". This is
                # the honest half of #9402: recovering the enumeration must not
                # be mistaken for having proved the negative.
                # ⛔ THE CAUSE IS RECORDED AS DATA, NOT LEFT TO PROSE PARSING
                # (#9423 review round 10, P3). This branch runs ONLY because
                # `_is_budget_exhaustion` matched the GraphQL failure, so the
                # surface's cause is KNOWN here. `_incomplete_remedy` re-reading
                # the note was the fragile half: the note embeds the gh error via
                # `_one_line(..., limit=_REFUSED_READ_NOTE_LIMIT)`, which keeps
                # the FIRST 600 chars, so a verbose stderr could push the
                # signature out of the note entirely and the report would again
                # prescribe "fix gh auth/network" for a spent budget. (An earlier
                # round moved the cause to the front of the NOTE and claimed that
                # fixed it — the review correctly showed it did not, because the
                # cut is applied to `rest_cause`, not to the note.)
                surface.budget_exhausted = True
                surface.incomplete(
                    f"closing-references-unmeasured: refused read — {rest_cause} "
                    "— the open-PR list was then answered over REST; "
                    "`closingIssuesReferences` is GraphQL-only and has NO REST "
                    "equivalent, so the computed closing-reference test could not "
                    f"run ({len(prs)} PR(s) WERE read — the title, head-ref and "
                    "number tests all ran, and any hit from them is reported "
                    "above) — treat the un-read test as UNKNOWN, not as "
                    "'closes nothing'"
                )
            # ⛔ `incomplete()` writes into `note`, so an unguarded assignment
            # here CLOBBERS the reason the surface is INCOMPLETE — and replaces
            # it with the word "complete", on a surface that just said it could
            # not be read. The status stayed correct (exit 2), so only the
            # REPORT lied, which is the failure mode this tool exists to catch.
            # A test asserts the reason string, which is how this was found.
            if not surface.truncated and surface.status != STATUS_INCOMPLETE:
                surface.note = f"{len(prs)} PR(s) enumerated (complete, cap {limit})"
        except SurfaceError as exc:
            surface.incomplete(f"gh-unavailable: {exc}")

    # 3. Branch surfaces. The TERMINAL predicates are not applied to the
    #    remote namespace (see `_branch_terminal_state`): a remote-tracking ref
    #    is a local cache, and demoting on it could call a reused live branch
    #    merged. Remote refs are number-matched ONLY: the
    #    remote-tracking namespace carries hundreds of stale branches and
    #    keyword-scanning it produced 878 false hits on a real run.
    # The local-branch walk, kept so the worktree surface can REUSE it rather
    # than re-issuing the identical query (and so its unavailability is
    # reported once, consistently, on every surface that depends on it).
    ancestor_merged_local: set[str] | None = None
    # `origin/main`'s tip, resolved ONCE: the ancestor arm must be STRICT (see
    # `_branch_terminal_state`), and that needs the tip to compare against. If it
    # cannot be resolved the arm is NOT applied and already-merged refs keep
    # blocking — the fail-closed direction, never a silent loosening.
    main_tip: str | None = None
    if target.path is not None:
        _rc, _out, _err, _to = _run(
            [git_bin, "rev-parse", "--verify", "--quiet", "origin/main"],
            cwd, timeout,
        )
        if _rc == 0 and _out.strip():
            main_tip = _out.strip()
    # ⛔ The witness predicate 2 actually needs (see `_first_parent_shas`).
    # Without it, `sha != main_tip` protects a fresh branch only until main
    # advances one commit, after which plain ancestry reads it as landed.
    first_parent: set[str] | None = None
    if target.path is not None:
        first_parent = _first_parent_shas(git_bin, cwd, timeout)
    # #6622: read the remote-tracking NAMESPACES once (ONE `git config` call).
    # ONLY a namespace some configured remote's fetch refspec actually populates
    # can hold a remote-tracking BRANCH; every other `refs/remotes/…` ref is a
    # one-shot fetch TARGET, so its name is a cache key rather than a holder.
    # None (unreadable) leaves all refs blocking, which is the pre-existing
    # behaviour.
    remote_namespaces = _remote_tracking_namespaces(git_bin, cwd, timeout)
    # ── #9806: the STALE-HOLDER test's two input callables, defined once ─────
    # Both pay one git call per REF THAT MATCHES the issue number, so a normal run pays
    # nothing extra: the branch arm below and the worktree arm in step 4 share them.
    def _branch_counts(_ref: str) -> tuple[int, int] | None:
        return _ahead_behind(git_bin, cwd, _ref, timeout)

    def _ref_tip_age(_ref: str) -> float | None:
        return _ref_tip_age_days(git_bin, cwd, _ref, timeout)

    # #10174: does the REMOTE still hold this branch, and at which tip? Paid only for
    # a ref the merge history already demoted — one read per DEMOTED REF, never one per
    # local branch (see `_remote_branch_state`) — and note-only: a failure may not
    # demote, un-demote, or turn the surface INCOMPLETE.
    def _remote_state(_ref: str) -> RemoteBranch:
        return _remote_branch_state(gh_bin, slug, cwd, timeout, _ref)

    # ⛔ THE WORKTREE LISTING IS READ HERE, BEFORE THE BRANCH SURFACES, because the
    # stale-holder test asks a question about the WORKTREES (`is a live process standing
    # in one of them?`) while scanning a BRANCH. It is the same ONE `git worktree list
    # --porcelain`, read once and handed to both steps; step 4 consumes this result rather
    # than re-issuing it, so the surface still reports ONE failure for ONE read. A read
    # that fails leaves `worktree_blocks` None, which the test treats as "cannot prove"
    # (no demotion) and step 4 reports as INCOMPLETE exactly as before.
    worktree_blocks: list[dict] | None = None
    worktree_error: SurfaceError | None = None
    if target.path is not None:
        try:
            rc, out, err, timed_out = _run(
                [git_bin, "worktree", "list", "--porcelain"], cwd, timeout
            )
            if rc != 0:
                why = "timeout" if timed_out else f"exit {rc}"
                raise SurfaceError(f"git worktree list failed ({why}): {_one_line(err)}")
            worktree_blocks = _worktree_blocks(out)
        except SurfaceError as exc:
            worktree_error = exc
    # The one object both surfaces ask. Constructed even when the listing failed — its
    # own `worktrees is None` arm refuses every demotion in that case.
    stale = StaleHolder(
        git_bin, cwd, issue, timeout, worktree_blocks,
    ) if target.path is not None else None
    # The LOCAL pass used to record `sha -> (ref, why)` here so the REMOTE pass could
    # demote a twin caching the same tip. That demotion is gone and the map with it —
    # see the removal block below, on the `else`-less namespace loop. Dead state and
    # stale prose both invite re-adding the demotion they described.
    for surface_name, namespace in (
        (SURFACE_LOCAL_BRANCHES, "refs/heads"),
        (SURFACE_REMOTE_BRANCHES, "refs/remotes"),
    ):
        surface = surfaces[surface_name]
        if target.path is None:
            surface.incomplete(
                f"no-local-clone: no local checkout of {slug or '(unknown repo)'} "
                "was found — this surface cannot describe the TARGET repo and is "
                "left INCOMPLETE rather than scanning a different one (NOT clean)"
            )
            continue
        try:
            refs = _git_refs(git_bin, cwd, namespace, timeout)
            # ONE `--merged` walk per namespace (#5186). `None` means the walk
            # could not run: the test only ever DOWNGRADES a hit, so the run
            # stays fail-closed, but the note says so rather than reporting an
            # inability as "nothing is merged".
            # ⛔ The `--merged` walk is ONLY meaningful for `refs/heads`.
            # `_branch_terminal_state` refuses to judge a remote-tracking ref (it
            # is a fetch cache), so running the walk on `refs/remotes` and
            # reporting its count told the reader that N remote refs had been
            # found merged and downgraded — when the surface had refused to
            # judge a single one. A walk FAILURE there printed an alarming
            # warning about a test that cannot affect that row. Report what is
            # measured.
            if namespace == "refs/heads":
                ancestor_merged = _ancestor_merged_refs(
                    git_bin, cwd, namespace, timeout
                )
                ancestor_merged_local = ancestor_merged
            else:
                ancestor_merged = None
            # #5485: the terminal tests above all read data already in hand, so a
            # squash-merge older than the sample is invisible and its branch blocks
            # forever. Ask GitHub about the LOCAL refs that will actually block —
            # bounded by the issue-number match, so this is normally 0-2 calls. Any
            # failure leaves the ref BLOCKING (never a demotion) and is counted so
            # the note can report the inability rather than pass strictness off as
            # a measurement.
            targeted_terminal: dict[str, str] = {}
            targeted_failures = 0
            if namespace == "refs/heads":
                for _ref, _sha in refs:
                    if _is_generated_branch(_ref) or not number_present(_ref, issue):
                        continue
                    if identity.owns_branch(_ref):
                        continue
                    _state = _branch_terminal_state(
                        _ref, _sha, merged_heads, ancestor_merged, main_tip,
                        first_parent,
                    )
                    if _state is not None:
                        continue
                    try:
                        _reason = _branch_terminal_state_from_prs(
                            gh_bin, slug, cwd, timeout, _ref, _sha, main_tip,
                            first_parent=first_parent,
                            ancestor_merged=ancestor_merged,
                        )
                    except SurfaceError:
                        targeted_failures += 1
                        continue
                    if _reason is not None:
                        targeted_terminal[_ref] = _reason
            # ⛔ THE #7693 REMOTE-REF DEMOTION WAS HERE, AND IT WAS REMOVED AFTER EIGHT REVIEW
            # ROUNDS. DO NOT RE-ADD IT WITHOUT READING THIS.
            #
            # The idea was simple: when this run proves a tip terminal on the LOCAL
            # surface, a `refs/remotes/<r>/<b>` ref caching that same tip is the same
            # immutable history and should not block forever. Seven rounds of fresh
            # review found SEVEN live false-CLEANs against it — `VERDICT: CLEAN (exit
            # 0)` while a lane was actively working the branch — and six of those
            # seven were found INSIDE THE PREVIOUS ROUND'S FIX. In order:
            #   1. keying on the remote-tracking ref's CACHED sha (a fetch cache this
            #      tool never refreshes; a merged-then-REUSED branch keeps the stale sha);
            #   2. deriving the REMOTE from the ref's first path segment (wrong for a
            #      non-standard refspec and for a slash-named remote);
            #   3. deriving the BRANCH from the prefix remainder (`set-branches origin
            #      'fix/*'` makes the remainder `X` while the branch is `fix/X`);
            #   4. a prefilter that demoted a remote it never READ, plus `ls-remote
            #      --heads` being blind to a `refs/pull/*` SOURCE;
            #   5. a SOURCE-narrowed spec passing the prefilter, being read, and still
            #      demoting;
            #   6. a PRIOR refspec — a ref cached by a former non-identity spec is mapped
            #      by NOTHING after the config is corrected, so "no head maps onto this
            #      ref" read as ABSENCE;
            #   7. a same-sha DECOY — the positive-same-sha rule proves SOME branch the
            #      current spec maps onto the ref sits at the cached sha, not that it is
            #      what the ref caches, so a prior-spec leftover is re-explained by a
            #      decoy and the true holder stays invisible.
            #
            # THE REASON ROUNDS 6-7 COULD NOT BE FIXED is the finding that matters:
            # the question the demotion asks — "is there a live branch this ref might
            # represent?" — is about HISTORY the configuration no longer contains.
            # "No advertised head maps onto this ref" cannot distinguish a branch
            # DELETED on the remote from a LEFTOVER cached by a prior, different
            # refspec; they are the same observation. No amount of care recovers
            # information that is not there.
            #
            # SO THE ANSWER IS THE ONE THAT NEEDS NO PROOF: the remote ref BLOCKS, as it
            # did before #7694. That costs an OVER-BLOCK — a merged branch's remote twin
            # keeps its issue un-dispatchable — which is an annoyance. A false CLEAN is
            # not: it sends two lanes into one shared checkout, which is precisely what
            # this tool exists to prevent. The preflight's other surfaces (local
            # branches, worktrees, claims, the issue itself) still find real in-flight
            # work.
            #
            # #7693 stays OPEN for the over-block. The evidence lives there and in
            # PR #7718; a doc line would not have stopped the collision.
            # #6108: the ahead-count reaches the surface through a callable so
            # the scan decides WHEN to pay for it — only for refs that already
            # matched the issue number. See the note in `scan_branch_surface`:
            # the count is REPORTED on every hit, and since #6108 was reopened it
            # also DECIDES one narrow case (`judge_empty` — 0 ahead with no owner,
            # no uncommitted work and no live claim), never by itself.
            # #9806: `tip_age` and `stale` ride along for the SAME reason — one git
            # call and one claim read, only for a ref that matched AND is far enough
            # behind AND silent long enough to be a stale-holder candidate.
            # #10174: `remote_state` is injected for the SAME cost discipline —
            # `scan_branch_surface` asks it ONLY for a ref the merge history has
            # already demoted, i.e. normally zero or one request per run, never one
            # per ref. It is a NOTE read: it can neither demote nor keep a ref
            # blocking, so its failure is folded into `unreadable` inside the scan
            # (`_ask_remote_state`) rather than declared as an INCOMPLETE surface.
            judged0, demoted0, kept0, unmeasured0 = (
                (stale.judged, stale.demoted, len(stale.kept), len(stale.unmeasured))
                if stale else (0, 0, 0, 0)
            )
            # The EMPTY-CLAIM leg's own counters, snapshotted beside the stale leg's so
            # each surface reports only its own measurements (#6108).
            empty_judged0 = stale.empty_judged if stale else 0
            empty_demoted0 = stale.empty_demoted if stale else 0
            empty_kept0 = len(stale.empty_kept) if stale else 0
            empty_unmeasured0 = len(stale.empty_unmeasured) if stale else 0
            scan_branch_surface(
                surface, refs, issue, identity, merged_heads,
                ancestor_merged, main_tip, first_parent,
                remote_namespaces=remote_namespaces,
                targeted_terminal=targeted_terminal,
                counts=_branch_counts,
                stale=stale,
                tip_age=_ref_tip_age,
                remote_state=_remote_state,
            )
            surface.note = f"{len(refs)} ref(s) enumerated"
            if namespace != "refs/heads":
                surface.note += (
                    "; terminal tests are not applied to these refs by name (a "
                    "remote-tracking ref is a local fetch cache, so judging it "
                    "terminal on its NAME could call a reused live branch merged), "
                    "and no remote ref is cleared as the terminal TWIN of a local "
                    "branch — a local terminal twin does NOT clear its remote "
                    "counterpart, which blocks by design (#7693, that demotion was "
                    "removed after eight review rounds found seven live "
                    "false-CLEANs in it)"
                )
                if remote_namespaces is None:
                    # #6622: report the INABILITY rather than presenting a strict
                    # answer as a measured one. Nothing was demoted, so this row
                    # is exactly as strict as it was before the change — but a
                    # reader cannot tell that from the rows alone.
                    surface.note += (
                        "; ⚠ the configured remotes' fetch refspecs could not be "
                        "read, so NO fetch-cache ref was demoted — every ref here "
                        "blocks (fail-closed)"
                    )
            elif ancestor_merged is None:
                surface.note += (
                    "; ⚠ 'merged into main' detection UNAVAILABLE (for-each-ref "
                    "--merged failed) — already-merged refs are NOT downgraded by "
                    "that test (the squash-merge test still applies)"
                )
            else:
                surface.note += (
                    f"; {len(ancestor_merged)} already merged into main"
                )
            # #10174: predicate 1's witness (the merged-PR head map) is fed by the
            # ADVISORY closed-PR sample, and when that sample cannot be read the map
            # is EMPTY — so a ref that KEEPS BLOCKING may be blocking only because
            # nobody could ask. Saying so is the difference between "a live holder is
            # there" and "the merged-PR record could not be read", which have
            # different remedies; the ref stays BLOCKING either way (fail-closed).
            if namespace == "refs/heads" and not merged_heads_readable:
                surface.note += (
                    "; ⚠ the merged-PR head witness could not be read (the closed-PR "
                    "sample failed), so the squash-merge test could not run — every "
                    "ref here that the targeted lookup did not clear keeps BLOCKING "
                    "(fail-closed)"
                )
            if namespace == "refs/heads" and targeted_failures:
                surface.note += (
                    "; ⚠ the targeted head-ref lookup could not run for "
                    f"{targeted_failures} ref(s) — those refs keep blocking "
                    "(fail-closed)"
                )
            # #9806: the stale-holder test is reported as a MEASUREMENT (how many refs
            # it judged and how many it demoted), never as a silent narrowing. A reader
            # must be able to see that a hit became advisory and why.
            if stale is not None:
                surface.note += stale.note_since(
                    judged0, demoted0, kept0, unmeasured0)
                surface.note += stale.empty_note_since(
                    empty_judged0, empty_demoted0, empty_kept0, empty_unmeasured0)
        except SurfaceError as exc:
            surface.incomplete(f"git-unavailable: {exc}")

    # 4. Worktree surface — UNTRUNCATED by construction; the count is proof.
    #    `worktree_blocks` was READ IN STEP 3 above (the stale-holder test needs the
    #    worktree side of a branch hit), so this step CONSUMES that one read rather than
    #    re-issuing it: one `git worktree list --porcelain` per run, one failure reported.
    #    #8175 deleted the claim-artifact verifier that once read it.
    blocks: list[dict] | None = worktree_blocks
    surface = surfaces[SURFACE_WORKTREES]
    if target.path is None:
        surface.incomplete(
            f"no-local-clone: no local checkout of {slug or '(unknown repo)'} was "
            "found — the worktree surface cannot describe the TARGET repo and is "
            "left INCOMPLETE rather than scanning a different one (NOT clean)"
        )
    elif worktree_error is not None:
        surface.incomplete(f"git-unavailable: {worktree_error}")
    else:
        try:
            # `_worktree_blocks` REFUSES an unreadable record, an unrecognised
            # continuation line, and a record missing its path or HEAD, so a
            # parse loss is a NAMED INCOMPLETE rather than a silently smaller
            # answer. The count guard that used to sit here could never fire: it
            # compared `len(blocks)` with the number of `worktree ` lines, and
            # the parser built one block per such line.
            # Reuse the LOCAL-BRANCH walk instead of repeating the identical
            # `for-each-ref --merged=origin/main refs/heads` query, and carry
            # its availability through: when the walk could not run, the
            # branch surfaces say so, and this surface claiming a plain
            # "untruncated" enumeration implied the terminal test had run.
            _wt_ancestor = ancestor_merged_local
            judged0, demoted0, kept0, unmeasured0 = (
                (stale.judged, stale.demoted, len(stale.kept), len(stale.unmeasured))
                if stale else (0, 0, 0, 0)
            )
            empty_judged0 = stale.empty_judged if stale else 0
            empty_demoted0 = stale.empty_demoted if stale else 0
            empty_kept0 = len(stale.empty_kept) if stale else 0
            empty_unmeasured0 = len(stale.empty_unmeasured) if stale else 0
            scan_worktree_surface(
                surface, blocks or [], issue, identity, merged_heads,
                _wt_ancestor, main_tip, first_parent,
                counts=_branch_counts, stale=stale, tip_age=_ref_tip_age,
            )
            surface.note = f"{len(blocks or [])} worktree(s) enumerated (untruncated)"
            if stale is not None:
                surface.note += stale.note_since(
                    judged0, demoted0, kept0, unmeasured0)
                surface.note += stale.empty_note_since(
                    empty_judged0, empty_demoted0, empty_kept0, empty_unmeasured0)
            if _wt_ancestor is None:
                surface.note += (
                    "; ⚠ 'merged into main' detection UNAVAILABLE (for-each-ref "
                    "--merged failed) — already-merged refs are NOT downgraded by "
                    "that test (the squash-merge test still applies)"
                )
        except SurfaceError as exc:
            surface.incomplete(f"git-unavailable: {exc}")

    # 5. Issue surface (assignee + HOLDER). It is scanned LAST only because that
    #    is where it has always been; it no longer depends on any local git
    #    surface — the claim gate reads the FLEET BOARD, not the worktree
    #    listing (#8175).
    if isinstance(issue_data, dict):
        # The holder read is SKIPPED when the issue payload itself is unreadable:
        # the surface is already INCOMPLETE (so the run can never be CLEAN), and
        # a rebuild of the board is a real sample window plus its own gh/cmux
        # reads. Skipping it cannot manufacture a CLEAN — it can only save the
        # cost of a question whose answer cannot change the verdict.
        holders = read_holders(issue, slug=slug, repo_root=target.path or cwd)
        scan_issue_surface(surfaces[SURFACE_ISSUE], issue_data, identity, holders)

    ordered = [surfaces[name] for name in ALL_SURFACES]
    return ordered, title, issue


def _assert_all_surfaces(ordered: list[Surface]) -> None:
    names = [s.name for s in ordered]
    if names != list(ALL_SURFACES):
        raise RuntimeError(
            f"internal error: surface set is incomplete/partial: {names!r} != {list(ALL_SURFACES)!r}"
        )


#: The claim gate's OWN note text for an unreadable lane roster
#: (`_board_can_answer`). `_incomplete_remedy` partitions on THIS, so the roster's
#: remedy is selected from the cause the same run measured, rather than taking
#: the generic arm — which prescribes a rebuild that fails on the same read
#: (#9392). It is this module's own marker, never a foreign string: the two sites
#: are asserted to agree by `test_the_roster_marker_is_the_note_the_gate_emits`.
_LANE_ROSTER_NOTE = "the lane roster is UNKNOWN, not empty"


def _incomplete_remedy(incomplete: list[Surface]) -> str:
    """The remedy for an INCOMPLETE run, keyed on its CAUSE (#9402).

    The report used to end every INCOMPLETE run with one sentence — "fix gh
    auth/network (or rebuild the fleet board) and re-run" — and for the most
    common live cause BOTH halves are wrong: the fleet-shared user-level GraphQL
    budget is spent, `gh auth` answered fine in the same minute, and the board's
    own GitHub read is a victim of the same exhaustion rather than its cure. A
    caller following that remedy spends a cycle re-authenticating and rebuilding
    a board that could not have helped.

    So the remedy is chosen from the surface notes with the SAME predicate the
    REST rescues use (`_is_budget_exhaustion`) — the rescue and the remedy must
    agree, or the report prescribes a cure for a cause it has already recognised.

    ⛔ TRUNCATION IS A CAUSE OF ITS OWN, AND IT IS READ FROM ITS OWN FIELD.
    `Surface.mark_truncated` writes `truncation_note` and NEVER `note`, so a
    remedy that inspects only `s.note` cannot see a truncated surface at all:
    the pure-GraphQL truncation case (a list longer than `--pr-limit`,
    `note == ""`) fell to the generic arm and told the reader to re-authenticate
    and rebuild the fleet board, for a cause the report had just MEASURED to be
    a completeness cap — while that surface's own truncation note says to widen
    `--pr-limit`. A remedy that names a cause the report has disproved is the
    same defect class as a wrong verdict reason.

    ⛔ ONE SURFACE CAN CARRY BOTH CAUSES, so the partition is THREE-WAY and
    `both` is decided FIRST. The REST rescue (#9402) makes that the COMMON case
    rather than a corner: an exhausted GraphQL read rescued over REST whose page
    comes back FULL leaves the open-PR surface `incomplete()` (the
    closing-reference test is unmeasured) AND `truncated` (the page was capped)
    in the SAME run. An arm that tested "every incomplete surface is exhausted"
    before looking at truncation then returned the budget cause alone and
    DROPPED the cap — a remedy omitting a cause this same report MEASURES, and
    the dropped one is the cause whose cure is a SETTING the operator controls:
    re-running after the budget resets at `--pr-limit 20` truncates again. The
    same partition is what keeps a both-flagged surface from being named TWICE
    in the mixed arm (once as exhausted, once as truncated).

    The multi-cause arms are not decoration: when only SOME surfaces are
    exhausted or truncated, claiming the generic cause in general would be wrong
    for the others, so each recognised cause is named as a CONTRIBUTOR and the
    generic remedy is kept for the remainder.

    ⛔ AND THE TRUNCATION REMEDY IS PAGE-SIZE AWARE, because not every cap has a
    cure. The REST rescue reads exactly ONE page at GitHub's `per_page` maximum,
    so `--pr-limit` (default 1000) cannot lift it — while the PRIMARY path's cap
    (`len(prs) > limit`) is precisely what `--pr-limit` widens. Prescribing the
    widening for the capped case put the verdict line in contradiction with the
    surface note twenty lines above it in the SAME report (#9423 review, P3).
    `Surface.truncation_widenable` carries which cap this is, as DATA, so the
    note and the remedy cannot disagree.
    """
    both = [
        s for s in incomplete
        if s.truncated
        and (s.budget_exhausted or _is_budget_exhaustion(s.note or ""))
    ]
    both_ids = {id(s) for s in both}
    exhausted = [
        s for s in incomplete
        if id(s) not in both_ids
        and (s.budget_exhausted or _is_budget_exhaustion(s.note or ""))
    ]
    truncated = [
        s for s in incomplete if id(s) not in both_ids and s.truncated
    ]
    generic = "fix gh auth/network (or rebuild the fleet board) and re-run"
    # ⛔ TWO CAUSES THE GENERIC SENTENCE MISPRISES, AND THEY ARE ANSWERED FROM
    # THEIR OWN NOTES (#9423 review, P3). An `issue-absent` or
    # `repo-unresolved` surface says so in its own note — "#{issue} does not
    # exist in {slug}" / "`{slug}` could not be resolved" — and then the VERDICT
    # line forty lines below told the reader to re-authenticate and rebuild a
    # board, which could not change either fact. Pre-existing wording (the old
    # report said it for every cause), but this function is now the ONE place
    # that chooses a remedy per cause, so leaving the misnomer here is choosing
    # it. Neither cause is exhaustion or truncation, so they are partitioned
    # FIRST and cannot double-count against those arms.
    absent = [
        s for s in incomplete
        if "issue-absent:" in (s.note or "")
        or "repo-unresolved:" in (s.note or "")
        or "target-repo-unresolved:" in (s.note or "")
    ]
    absent_ids = {id(s) for s in absent}
    if absent and len(absent) == len(incomplete):
        return (
            "a TARGET the tool cannot resolve — "
            f"{', '.join(s.name for s in absent)} could not be addressed at all. "
            "Check the number and the repository: this is NOT a read failure, so "
            "re-authenticating and rebuilding the fleet board cannot change it, "
            "and \"not found here\" is not \"no in-flight work\" (the run "
            "correctly refuses to report CLEAN for a target it could not read)"
        )
    if absent:
        named = ", ".join(s.name for s in absent)
        tail_cause = _incomplete_remedy(
            [s for s in incomplete if id(s) not in absent_ids]
        )
        return (
            f"PART of this is a TARGET the tool cannot resolve ({named} — check "
            f"the number and repository; re-authenticating cannot change it), "
            f"and PART is: {tail_cause}"
        )
    # ⛔ THE LANE ROSTER IS ITS OWN CAUSE, AND ITS OWN REMEDY (#9392).
    # Every arm below answers a cause a RE-READ can fix: a budget that resets, a
    # cap a setting widens, a network that comes back. An unreadable roster is
    # none of those — `fleet_state.py build` builds the board FROM that file, so
    # "rebuild the fleet board" is the dead end this issue files, and `gh auth`
    # is fine in the same run (every other surface answered). The marker is this
    # module's OWN note text (`_board_can_answer`), not a foreign signature, and
    # the cause is carried on the surface (`Surface.registry_error`) and classified
    # by `fleet_state.registry_remedy` — the same helper `fleet_state.py build` and the surface
    # note take their sentence from, so a lane gets ONE answer from all three.
    roster = [s for s in incomplete if _LANE_ROSTER_NOTE in (s.note or "")]
    if roster:
        roster_ids = {id(s) for s in roster}
        # ⛔ THE CAUSE IS CARRIED, NOT RE-PARSED (#9392 review P2). Passing the
        # composed note back to `registry_remedy` made the note itself look like
        # the cause: it is never empty, so the helper's "no cause recorded" arm
        # could not be reached and the exit-2 line asserted the roster EXISTS for
        # a board that recorded nothing. The recorded cause is the only input.
        recorded = [s.registry_error for s in roster
                    if s.registry_error is not None]
        roster_cause = fleet_state.registry_remedy(
            recorded[0] if recorded else "")
        text = (
            "the LANE ROSTER is UNKNOWN (not empty) — no lane can be attributed "
            "to this issue until it is readable, and this is NOT a read that "
            f"retrying or rebuilding can fix: {roster_cause}"
        )
        if len(roster) == len(incomplete):
            return text
        tail_cause = _incomplete_remedy(
            [s for s in incomplete if id(s) not in roster_ids]
        )
        return f"PART of this is {text}; PART is: {tail_cause}"
    if not both and not exhausted and not truncated:
        return generic
    budget = (
        "a GRAPHQL BUDGET EXHAUSTION shared by the whole fleet — `gh auth` is "
        "FINE (re-authenticating cannot help), the primary "
        "`rate_limit.resources.graphql` counter can still read thousands "
        "remaining when the user-level bucket refuses, and the fleet board's own "
        "GitHub read fails for this same cause (so rebuilding it cannot help "
        "either). Re-run when the budget resets — NO readable counter shows "
        "this bucket, so probe the read that failed instead "
        "(`gh pr list --state open --limit 1 --json number`): an answer means "
        "the bucket is back. Any surface above that "
        "answered over REST is already valid evidence"
    )
    widen_remedy = (
        "a TRUNCATED list — the surface hit its completeness cap and the "
        "remainder was NOT scanned. That is a SETTING, not a failed read: widen "
        "with --pr-limit (or COLLISION_PREFLIGHT_PR_LIMIT) and re-run; `gh "
        "auth` is fine and the fleet board does not need rebuilding"
    )
    # ⛔ THE TRUNCATION REMEDY IS PAGE-SIZE AWARE, EXACTLY AS THE SURFACE NOTE IS
    # (#9423 review, P3). The REST rescue reads ONE page at GitHub's per_page
    # MAXIMUM, so on that path `--pr-limit` (default 1000) cannot widen anything:
    # prescribing it would put the verdict line in contradiction with the
    # surface's own note twenty lines above, and burn a cycle. The widen case is
    # still real for the PRIMARY path (`len(prs) > limit`) and for a REST page
    # below the maximum, which is why the two arms are kept distinct instead of
    # dropping the advice.
    capped_remedy = (
        "a TRUNCATED list — the surface hit its completeness cap and the "
        "remainder was NOT scanned. There is NO SETTING to widen here: the REST "
        f"rescue reads ONE page at GitHub's maximum per_page of {_REST_PULLS_PAGE}, "
        "so --pr-limit cannot lift it. Re-run once the budget resets, or accept "
        "a partial enumeration that is already INCOMPLETE; `gh auth` is fine and "
        "the fleet board does not need rebuilding"
    )

    def _truncation_text(surfaces: list[Surface]) -> str:
        """The truncation cause for `surfaces`, naming both kinds when both are
        present: a widening that EXISTS and a cap that has none."""
        widen = [s for s in surfaces if s.truncation_widenable]
        capped = [s for s in surfaces if not s.truncation_widenable]
        if widen and capped:
            return (
                "TRUNCATED list(s) — the surface(s) hit their completeness cap "
                "and the remainder was NOT scanned. Part of that is a SETTING "
                f"(widen --pr-limit for {', '.join(s.name for s in widen)}); "
                "part has NO setting at all "
                f"({', '.join(s.name for s in capped)}: the REST rescue reads "
                f"ONE page at GitHub's maximum per_page of {_REST_PULLS_PAGE}, "
                "so --pr-limit cannot lift it). Re-run once the budget resets; "
                "`gh auth` is fine and the fleet board does not need rebuilding"
            )
        if capped:
            return capped_remedy
        return widen_remedy

    if len(both) == len(incomplete):
        # Every incomplete surface carries BOTH measured causes: name BOTH, and
        # name the surface once. Gated on the FULL set (not on "no exclusive
        # contributor") so a mixed run still falls through to the contributor
        # arm below and keeps the generic remedy for the remainder.
        return (
            f"the causes are BOTH, both measured on "
            f"{', '.join(s.name for s in both)}: {budget} — AND, independently, "
            f"{_truncation_text(both)}"
        )
    if len(exhausted) == len(incomplete):
        return f"the cause is {budget}"
    if len(truncated) == len(incomplete):
        return f"the cause is {_truncation_text(truncated)}"
    remaining = [
        s for s in incomplete
        if id(s) not in both_ids
        and id(s) not in {id(x) for x in exhausted}
        and id(s) not in {id(x) for x in truncated}
    ]
    parts: list[str] = []
    if exhausted:
        parts.append(f"{', '.join(s.name for s in exhausted)}: {budget}")
    if truncated:
        parts.append(
            f"{', '.join(s.name for s in truncated)}: "
            f"{_truncation_text(truncated)}"
        )
    if both:
        parts.append(
            f"{', '.join(s.name for s in both)}: BOTH {budget} — AND, "
            f"independently, {_truncation_text(both)}"
        )
    tail = ""
    if remaining:
        tail = (
            f" — while the remaining surface(s) "
            f"({', '.join(s.name for s in remaining)}) need {generic}"
        )
    return "PART of this is " + "; PART is ".join(parts) + tail


def format_report(
    ordered: list[Surface],
    issue: int,
    target: RepoTarget,
    title: str | None,
) -> tuple[str, int]:
    _assert_all_surfaces(ordered)

    hits = [h for s in ordered for h in s.hits]
    # ── authority (#5251) ───────────────────────────────────────────────────
    # ONLY a BLOCKING surface can decide the verdict — which is a statement about
    # which surface may BLOCK, NOT about where its evidence comes from: the
    # advisory closed-PR sample's head SHAs are deliberately consumed by the
    # blocking branch scans to demote squash-merge residue (D4). That coupling
    # can only ever REMOVE a hit, and it is stated where the harvest happens.
    #
    # An advisory surface is
    # still queried, still hit, and still reported — it simply cannot block a
    # dispatch, and its failure cannot conceal a collision, so it must not set
    # INCOMPLETE either. Filtering by SURFACE (not by hit strength) is what
    # makes the demotion STRUCTURAL: were an advisory surface to emit a `strong`
    # hit, it still could not block — the guarantee #5129's data-dependent shape
    # could not give, where dropping a payload field promoted a hit back to
    # `strong`.
    advisory = [s for s in ordered if s.authority == AUTHORITY_ADVISORY]
    advisory_names = {s.name for s in advisory}
    # A surface that was never actually queried must not be counted as queried.
    # Before #5251 the CLEAN line was reachable only when `incomplete == []`, so
    # "7/7 surfaces queried" was literally true; now exit 0 can mean the advisory
    # surface was never read, and claiming 7/7 would be a NEW false completeness
    # statement in the very artifact a human reads to authorize a dispatch
    # (review cycle 1). So the count is of surfaces ACTUALLY queried, and any
    # advisory shortfall is named beside it.
    queried = [
        s for s in ordered
        if s.status != STATUS_INCOMPLETE and not s.truncated
    ]
    advisory_partial_or_failed = [
        s for s in advisory if s.status == STATUS_INCOMPLETE or s.truncated
    ]
    advisory_note = ""
    if advisory_partial_or_failed:
        advisory_note = (
            f" ({len(advisory_partial_or_failed)} advisory surface(s) partial or "
            "unqueried — cannot block; see the ADVISORY section)"
        )
    blocking = [s for s in ordered if s.authority != AUTHORITY_ADVISORY]
    incomplete = [
        s for s in blocking if s.status == STATUS_INCOMPLETE or s.truncated
    ]
    strong = [
        h for h in hits
        if h.surface not in advisory_names
        and h.surface != SURFACE_LANDED
        and h.strength == "strong"
    ]
    weak = [
        h for h in hits
        if h.surface != SURFACE_LANDED and h.strength == "weak"
    ]
    # #8560: the landed surface is BLOCKING, but its hits decide EXIT_LANDED —
    # NOT EXIT_COLLISION — so they are separated out before the strong/weak
    # tiers, exactly as advisory hits are. A landed hit is never counted as a
    # collision: the two refusals name different remedies, and conflating them
    # would make the refusal's stated reason not what caused it.
    landed_hits = [h for h in hits if h.surface == SURFACE_LANDED]
    # ── the verdict counts the hits that DECIDED it (#3504 class 2) ──────────
    # `strong` is now the ONLY tier that can block, so it is the only tier the
    # refusal may count. The verdict line used to print `len(hits)` — EVERY hit,
    # weak prose and advisory rows included — which is how #2573 read as
    # "8 hit(s)" and a COLLISION while its actual blocking content was smaller:
    # the labels said non-blocking and the verdict contradicted them. A refusal
    # whose stated reason is not what caused it is not verifiable.
    deciding = strong

    lines: list[str] = []
    slug = _sanitize(target.slug) or "(unresolved)"
    lines.append(f"collision-preflight: issue #{issue}")
    lines.append(f"repo: {slug}   [resolved from: {target.source}]")
    lines.append(
        f"local checkout: {target.path or '(none — git surfaces INCOMPLETE)'}"
    )
    # The FULL title, never truncated: this line is what makes a wrong-target
    # read visible at the point of use (#4027). When it is unavailable the
    # report says so. That is a VISIBILITY aid, not a fail-closed gate: the
    # title no longer feeds any decision (the lexical arm that consumed it is
    # deleted, #3504), so a missing title cannot make a surface unreadable.
    # The fail-closed signals are the issue surface's own status and every
    # other surface's; none of them depends on the title.
    lines.append(
        f"title: {' '.join(_sanitize(title).split()) if title else '(unavailable — target not established)'}"
    )
    lines.append("keyword gate: none — the lexical arm is deleted (#3504). A blocking "
                 "hit is a match on the ISSUE NUMBER or a computed GitHub field; shared "
                 "domain vocabulary is never consulted.")
    lines.append(
        "claim gate: HOLDER liveness from the FLEET BOARD — the lane's own "
        "`session.pid_alive` (branch slug / worktree / the lane's own session text); "
        "comment prose is NOT read. A lane attributed to the issue and still "
        "holding it BLOCKS; a stopped holder is reported only; an unmeasured "
        "liveness, an ambiguous attribution, or an unreadable board is INCOMPLETE "
        "(fail-closed). The gate offers NO dismissal switch (#8175)."
    )
    lines.append("")
    surface_width = max(len("SURFACE"), *(len(s) for s in ALL_SURFACES))
    lines.append(f"{'SURFACE':<{surface_width}} {'STATUS':<11} {'HITS':<5} NOTE")
    for surface in ordered:
        note = surface.note or ""
        if surface.truncated:
            note = (note + " " if note else "") + (
                "⚠ PARTIAL — advisory sample, cannot block"
                if surface.authority == AUTHORITY_ADVISORY
                else "⚠ TRUNCATED — list is partial"
            )
        status = "ADVISORY" if surface.authority == AUTHORITY_ADVISORY else surface.status
        lines.append(f"{surface.name:<{surface_width}} "
                     f"{status:<11} {len(surface.hits):<5} {note}")
    if hits:
        lines.append("")
        lines.append("HITS")
        by_surface: dict[str, list[Hit]] = {}
        for hit in hits:
            by_surface.setdefault(hit.surface, []).append(hit)
        for surface_name, surface_hits in by_surface.items():
            for hit in surface_hits[:MAX_HITS_SHOWN]:
                # Two tiers only, now that the lexical arm is deleted (#3504):
                # a `keyword` strength no longer exists, so a tag entry for it
                # would be dead code that reads as a live tier.
                # #8560: a landed hit is tagged distinctly. It is the one hit
                # on a BLOCKING surface that neither authorizes nor refuses like
                # a collision, and the bare `(number)` tag is what a collision
                # prints — so an unlabelled landed hit would be indistinguishable
                # from the one that caused a COLLISION.
                if hit.surface == SURFACE_LANDED:
                    tag = "landed"
                else:
                    tag = {"strong": "number", "weak": "weak"}.get(
                        hit.strength, hit.strength)
                # An ADVISORY surface's hits are tagged distinctly. A CLEAN
                # report can now legitimately DISPLAY a hit that would have
                # blocked on a blocking surface, and the bare `(number)` tag is
                # exactly what a blocking number hit prints — so an untagged
                # advisory hit would be indistinguishable from the one that
                # caused a refusal (review cycle 1).
                #
                # ⛔ APPLIED TO WEAK HITS TOO. The marker used to be gated on
                # `hit.strength != "weak"`, which excluded the COMMON case on
                # this surface: a match on a closed PR is weak whenever that PR
                # is terminal (merged), i.e. almost always. So the advisory row
                # printed the bare `(weak)` tag — which is equally what a weak
                # hit on a BLOCKING surface prints — and the stated rationale
                # ("distinguishable from a blocking surface's hit") was false for
                # exactly the hits the surface usually produces. The exemption
                # protected against nothing; drop it.
                #
                # Emitted as its OWN parenthesised group rather than appended to
                # `tag`: appending produced `(number (advisory — cannot block))`,
                # and nesting the marker inside the strength tag made a report
                # line that is read by an agent harder to parse than the two
                # facts deserve to be.
                # NOT named `advisory`: that name already holds the LIST of
                # advisory Surface objects in this function (used below for the
                # advisory sections), and a str binding here shadowed it —
                # `AttributeError: 'str' object has no attribute 'truncated'`.
                # Caught by the existing strong-advisory test.
                advisory_tag = (
                    " (advisory — cannot block)"
                    if surface_name in advisory_names else ""
                )
                lines.append(
                    f"  [{hit.surface}] {_sanitize(hit.ref)} — "
                    f"{_sanitize(hit.detail)} ({tag}){advisory_tag}"
                )
            if len(surface_hits) > MAX_HITS_SHOWN:
                # The COUNT is complete and visible; only the detail sample is
                # capped (a completeness check must never hide a count).
                lines.append(
                    f"  [{surface_name}] … +{len(surface_hits) - MAX_HITS_SHOWN} "
                    f"more hit(s) on this surface (total {len(surface_hits)})"
                )
    if weak and not strong:
        # ⛔ The heading must not describe only the PROSE kind. Since identity
        # and terminal detection landed, `weak` also carries hits that plainly
        # ARE work but cannot block: the caller's own branch/worktree, a
        # terminal branch, and an assignee equal to the shared fleet account.
        # Calling all of them "prose-only cross-references" made the report
        # contradict its own contents — the "unverifiable verdict" class this
        # change exists to remove (#3504 class 2).
        lines.append("")
        lines.append("WEAK SIGNALS (reported, non-blocking — these do NOT block)")
        prose_only = [h for h in weak if h.kind == "prose-body"]
        if prose_only:
            lines.append(
                f"  {len(prose_only)} {WEAK_KIND_PROSE_ONLY}(s) of #{issue}; "
                "no title / branch / worktree / closing-reference match."
            )
        rest = len(weak) - len(prose_only)
        if rest:
            lines.append(
                f"  {rest} further non-blocking hit(s) that are NOT prose — "
                f"{WEAK_HIT_KINDS}."
            )
        lines.append("  These do NOT block a dispatch.")
    if incomplete:
        lines.append("")
        lines.append("INCOMPLETE SURFACES")
        for surface in incomplete:
            # BOTH reasons are printed, never `truncation_note or note`: a
            # surface can be INCOMPLETE *and* truncated, and the `or` form
            # silently dropped the incompleteness reason — the same
            # "the report misdescribes what it measured" defect already fixed
            # once in this change (where an unguarded note assignment clobbered
            # `incomplete()`'s message).
            parts = [p for p in (surface.note, surface.truncation_note) if p]
            lines.append(
                f"  [{surface.name}] " + (" — ".join(parts) if parts else "unreadable")
            )
    if advisory:
        lines.append("")
        lines.append(
            "ADVISORY SURFACES — reported only; these cannot block a dispatch"
        )
        for surface in advisory:
            detail = ""
            if surface.truncated:
                detail = " — " + (surface.truncation_note or "partial sample")
            elif surface.status == STATUS_INCOMPLETE:
                detail = " — " + (surface.note or "could not be queried")
            lines.append(f"  [{surface.name}] {len(surface.hits)} hit(s){detail}")
        if any(s.hits for s in advisory):
            lines.append(
                f"  NOTE: a match here is on a TERMINAL PR — immutable history, "
                f"not in-flight work — so it does NOT block #{issue}. Verify it "
                "by hand before treating the issue as already done."
            )
        if any(s.status == STATUS_INCOMPLETE or s.truncated for s in advisory):
            lines.append(
                "  NOTE: an advisory surface being partial or unqueryable is NOT "
                "an INCOMPLETE run — it cannot conceal a collision, so it cannot "
                "force exit 2 (the fail-closed posture is kept for every surface "
                "whose failure COULD)."
            )
    lines.append("")
    if landed_hits:
        lines.append(
            f"VERDICT: LANDED (exit {EXIT_LANDED}) — {len(landed_hits)} "
            f"authoritative terminal signal(s) for #{issue} in {slug}: "
            + _landed_conclusion(landed_hits)
        )
        # The remedy is the point of a DISTINCT verdict: a landed issue needs
        # verification and closure, not coordination (COLLISION) and no re-run
        # (INCOMPLETE). ⚠️ #8625: LANDED is now produced by the issue's own
        # CLOSED state ALONE — a merged PR that merely names the issue is an
        # ADVISORY (a reference, not a closure) and cannot reach this branch, so
        # the preamble below names the state as the sole authority.
        lines.append(
            "  The authority here is the issue's OWN STATE, and nothing else. A "
            "MERGED PR that merely NAMES an issue is a reference, not a closure "
            "(#8625), so it is reported on the ADVISORY `merged PRs naming an "
            "OPEN issue` surface and can never produce this verdict. There is no "
            "dismissal switch — verify the closure against the issue body. If it "
            "still needs work, state its REMAINING scope and re-open the issue "
            "before dispatch, so the next lane does not re-implement shipped "
            "code."
        )
        if incomplete:
            lines.append(
                # "FULLY" for the same reason as the other two summary sites
                # (#9423 review round 9, P3): a REST-rescued surface WAS read,
                # only not completely — and this line sits directly beneath a row
                # that says "N PR(s) WERE read", so the old absolute contradicted
                # its own row.
                f"  ALSO INCOMPLETE: {len(incomplete)} surface(s) could not be "
                f"FULLY queried ({', '.join(s.name for s in incomplete)}) — but a "
                "terminal signal above is decisive, so the verdict is LANDED, "
                "not INCOMPLETE."
            )
        if strong:
            lines.append(
                f"  ({len(strong)} BLOCKING collision hit(s) are ALSO present — "
                "the work item is TERMINAL AND another lane appears to be on it.)"
            )
        return "\n".join(lines) + "\n", EXIT_LANDED
    if strong:
        lines.append(
            f"VERDICT: COLLISION (exit {EXIT_COLLISION}) — {len(deciding)} blocking "
            f"hit(s) across {len({h.surface for h in deciding})} surface(s) for "
            f"#{issue} in {slug}; do NOT dispatch"
        )
        # Say the non-deciding hits exist, so a refusal never reads as if the
        # report contained nothing else — but never let them inflate the count
        # that states WHY it refused (#3504 class 2).
        if len(hits) != len(deciding):
            lines.append(
                f"  ({len(hits) - len(deciding)} further hit(s) were reported but are "
                "non-blocking — weak/advisory — and did NOT cause this refusal)"
            )
        # The remedy belongs at the POINT OF REFUSAL, and it names only the
        # HOLDER hits: a refusal can also be forced by a non-holder hit (an open
        # PR, a branch), so it must not read as "clearing this holder clears the
        # refusal". There is NO dismissal switch (no `--ignore`, no env var, no
        # human input) — the holder read is mechanical, so the honest remedy is
        # to name the lane and say it must be coordinated with (or verified as
        # stopped) before dispatching. Worth stating because the OLD remedy named
        # a comment a human could hand-edit to clear the block; that escape is
        # deliberately gone.
        holder_hits = [
            h for h in strong if h.ref.startswith("holder:")
        ]
        if holder_hits:
            named = "; ".join(
                _sanitize(h.ref) for h in holder_hits[:MAX_HITS_SHOWN]
            )
            if len(holder_hits) > MAX_HITS_SHOWN:
                named += f"; … +{len(holder_hits) - MAX_HITS_SHOWN} more"
            lines.append(
                "  REMEDY: live HOLDER(s) on the fleet board: "
                f"{named}. There is NO dismissal switch — a lane the board "
                "attributes to this issue and reports as still holding it blocks "
                "by design (a missed duplicate is worse than a false alarm), and "
                "nothing here can be hand-edited to clear it. Coordinate on the "
                "issue with each named lane, or confirm the board's reading "
                "(`uv run python tools/fleet_state.py who "
                f"{issue}`). This line names only the holder hits; the refusal "
                "may also be forced by a non-holder hit above."
            )
        if incomplete:
            # ⛔ "COULD NOT BE QUERIED" IS FALSE FOR A REST-RESCUED SURFACE
            # (#9423 review round 8, P3). The rescue (#9402) ENUMERATES the open-PR
            # list and runs the title/head-ref/number tests over it; only the
            # GraphQL-computed closing-reference test is unmeasured. The summary
            # line said "open PRs could not be queried" directly beneath a
            # blocking hit that `open PRs` HAD produced, while the surface's own
            # note said "N PR(s) WERE read". A summary that contradicts the row
            # above it is the "report misdescribes what it measured" class this
            # PR exists to close, so the verb is now precise: PARTIAL reads are
            # "could not be FULLY queried", which is true for all three causes
            # (unmeasured test, truncated cap, unread surface).
            lines.append(
                f"  ALSO INCOMPLETE: {len(incomplete)} surface(s) could not be FULLY "
                f"queried "
                f"({', '.join(s.name for s in incomplete)}) — "
                f"{_incomplete_remedy(incomplete)}"
            )
        return "\n".join(lines) + "\n", EXIT_COLLISION
    if incomplete:
        lines.append(
            f"VERDICT: INCOMPLETE (exit {EXIT_INCOMPLETE}) — {len(incomplete)} surface(s) "
            f"could not be FULLY queried ({', '.join(s.name for s in incomplete)}); "
            f"this is NOT clean for #{issue} in {slug} — "
            f"{_incomplete_remedy(incomplete)}"
        )
        return "\n".join(lines) + "\n", EXIT_INCOMPLETE
    if weak:
        # NOT "prose signals": since identity and terminal detection landed,
        # `weak` also carries the caller's own branch/worktree, a terminal
        # branch, and an assignee equal to the shared fleet account. The WEAK
        # SIGNALS block above was already corrected to say so; leaving this line
        # calling them all "prose" made the report contradict its own contents
        # eight lines earlier — the same unverifiable-verdict class this change
        # removes (#3504 class 2).
        lines.append(
            f"NOTE: {len(weak)} weak, non-blocking signal(s) ignored (see WEAK "
            "SIGNALS above); none can cause a refusal"
        )
    lines.append(
        f"VERDICT: CLEAN (exit {EXIT_CLEAN}) — {len(queried)}/{len(ALL_SURFACES)} "
        f"surfaces queried{advisory_note}, no in-flight work found for #{issue} in {slug}"
    )
    return "\n".join(lines) + "\n", EXIT_CLEAN


def _resolve_target(args, timeout: float) -> tuple[RepoTarget | None, int]:
    """Resolve `--repo` (owner/name OR path OR omitted) into a RepoTarget.

    Returns (target, exit_code); exit_code != 0 means the CLI must abort.
    """
    repo_arg = args.repo
    requested = repo_arg is not None
    slug_explicit: str | None = None
    # An EMPTY/whitespace `--repo` is a usage error, never the cwd: `Path("")`
    # is a directory, so it used to take the PATH branch with path="" and run
    # the git surfaces against os.getcwd() while the report claimed "local
    # checkout: (none — git surfaces INCOMPLETE)". That is exactly the
    # unset-shell-variable accident this rejects up front.
    if repo_arg is not None and not repo_arg.strip():
        print(
            "collision-preflight: --repo must be `owner/name` or an existing "
            "directory; got an empty/whitespace value (an unset shell variable "
            "must not be reinterpreted as the cwd)",
            file=sys.stderr,
        )
        return None, EXIT_USAGE
    if repo_arg is None:
        path = os.getcwd()
        source = "cwd"
    elif Path(repo_arg).is_dir():
        path = repo_arg
        source = "--repo PATH"
    elif REPO_SLUG_RE.fullmatch((repo_arg or "").strip()):
        path = os.getcwd()
        slug_explicit = repo_arg.strip()
        source = "--repo owner/name"
    else:
        print(
            "collision-preflight: --repo must be `owner/name` or an existing "
            f"directory, got: {repo_arg!r}",
            file=sys.stderr,
        )
        return None, EXIT_USAGE

    target = RepoTarget(requested=requested)
    roots = repo_roots(args.git, path, timeout)
    if slug_explicit:
        current, _how = resolve_slug(args.gh, args.git, path, timeout)
        target.slug = slug_explicit
        target.source = source
        # GitHub and `gh --repo` are case-insensitive; compare that way so a
        # case-different selector still finds the local clone (and the canonical
        # resolved slug is what is sent to gh).
        if current and current.lower() == slug_explicit.lower():
            target.path = path
        else:
            # A clone FOUND by searching, which is very often the ordinary
            # checkout the caller is sitting in but is NOT evidence that it is
            # theirs — hence `_standing_in`, not this branch, decides identity.
            target.path = find_local_clone(slug_explicit, args.git, timeout, roots)
    else:
        slug, how = resolve_slug(args.gh, args.git, path, timeout)
        target.slug = slug
        target.path = path
        target.source = f"{source} ({how})"
    return target, 0


def _ambiguity_refusal(args, target: RepoTarget, timeout: float) -> int | None:
    """When `--repo` was omitted, refuse rather than guess which repo #N is in.

    Returns an exit code to abort with, or None to proceed. A number that
    resolves in more than one candidate repo (or that resolves elsewhere but
    not here) must NEVER be silently resolved to whichever repo the cwd happens
    to be — that is the cross-repo false CLEAN of #4027.
    """
    if target.requested or not target.slug:
        return None
    cwd = target.path or os.getcwd()
    probe_timeout = min(timeout, AMBIGUITY_TIMEOUT)
    roots = repo_roots(args.git, cwd, timeout)
    try:
        slugs = candidate_slugs(args.git, timeout, roots)
    except Exception:  # pragma: no cover - defensive
        slugs = []
    if len(slugs) > CANDIDATE_REPO_CAP:
        print(
            f"collision-preflight: {len(slugs)} candidate repos exceed the "
            f"{CANDIDATE_REPO_CAP} cap — refusing to guess which one #{args.issue} "
            "belongs to; pass --repo owner/name",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    others = [s for s in slugs if s != target.slug]
    holds_others: list[str] = []
    unqueried: list[str] = []
    for other in others:
        verdict = probe_issue_in_repo(args.gh, other, args.issue, cwd, probe_timeout)
        if verdict is True:
            holds_others.append(other)
        elif verdict is None:
            unqueried.append(other)
    current_holds = probe_issue_in_repo(args.gh, target.slug, args.issue, cwd, probe_timeout)
    if current_holds is None:
        unqueried.insert(0, target.slug)
    holders = ([target.slug] if current_holds else []) + holds_others
    if len(holders) > 1:
        print(
            f"collision-preflight: AMBIGUOUS target — #{args.issue} resolves in "
            f"{', '.join(holders)}; refusing to guess. Re-run with --repo owner/name.",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    if current_holds is False and len(holds_others) == 1:
        print(
            f"collision-preflight: #{args.issue} does NOT exist in {target.slug} "
            f"(the current repo) but resolves in {holds_others[0]} — "
            f"re-run with --repo {holds_others[0]}",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    if unqueried:
        print(
            f"collision-preflight: cannot rule out a second repo for #{args.issue} "
            f"— these candidates could not be probed: {', '.join(unqueried)}. "
            "A partial check is never CLEAN; pass --repo owner/name to disambiguate.",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    return None


class _UsageParser(argparse.ArgumentParser):
    """An `ArgumentParser` whose usage errors exit EXIT_USAGE, not argparse's 2.

    argparse's default is `sys.exit(2)`, which COLLIDES with this tool's
    EXIT_INCOMPLETE. That is not cosmetic. #3504 deleted the lexical arm
    (`--keywords` / `--min-keywords`), so the most likely way to invoke this tool
    wrongly is now a stale caller still passing one of those flags — and a
    caller handed exit 2 learns "a surface could not be read", which is a
    transient-looking condition it may sensibly RETRY. Retrying a flag that can
    never be accepted is a loop with no exit. "You called it wrong" and "the
    world could not be read" are different facts and get different codes.

    Both directions are fail-closed (neither permits a dispatch), so this is a
    clarity fix, not a safety one — but the whole point of a pre-flight gate is
    that its answer is legible."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _verdict_for_pull_request(
    exc: NotAWorkItem,
    number: int,
    run_one,
) -> tuple[str | None, int]:
    """Resolve a PR number to the work item(s) it serves, and gate THOSE (#9957).

    Called only when the pre-flight refused the number as an open pull request.
    The refusal is the TRIGGER, not the answer: the gate already holds the
    mapping (it printed "It closes #N"), so it resolves instead of handing the
    caller a prose instruction. This is the whole of the fix — the same
    pre-flight, the same surfaces, the same exit codes, run against the number
    the dispatch MEANT.

    `run_one` is `main`'s own single-number pre-flight, passed in rather than
    re-implemented so a resolved run cannot drift from a direct one.

    Fail-closed in every degenerate shape:

      * no work item resolvable  -> the two-part refusal names WHY (the
        resolver's own evidence lines), and the code is `EXIT_INCOMPLETE` —
        never `EXIT_CLEAN`. This is #9957's own rule, and the same polarity the
        main-health rule uses: a missing check is not a green check;
      * a resolved number is ITSELF an open PR -> refused, not re-resolved.
        Recurse and a PR whose only reference is a peer PR loops; refuse and
        the caller gets the same "re-run with the issue number" remedy it
        would have got before, which is the safe end of that shape. ⛔ That
        refusal is ONE ITEM's verdict (`EXIT_INCOMPLETE`), never the whole
        run's: the other items have already run, and the aggregate below must
        still decide over them — a LANDED item outranks it (#9970 review);
      * several work items -> EVERY one is gated and the WORST verdict decides
        (`_EXIT_SEVERITY`), so a resolution can add a refusal and can never
        authorise a dispatch one of its work items would have refused. The run
        prints ONE anchored `VERDICT:` line — that aggregate — and the per-item
        lines are DEMOTED to `WORK ITEM #N VERDICT: …`, because the fleet's only
        text-keyed consumer reads the first `^VERDICT` line and does not compare
        it with the exit code (`_demote_verdict_lines`).
    """
    resolution = resolve_pull_request_work_items(exc.pr or {}, number)
    if not resolution.resolved:
        # BOTH halves are printed: the first is the sentence the caller already
        # knows (and several tests pin its wording), the second is the NEW fact
        # — what was checked and what could not be resolved. A refusal that
        # does not say what it looked at is a refusal the caller cannot repair.
        print(f"collision-preflight: {exc}", file=sys.stderr)
        print(
            "collision-preflight: " + _resolution_failure(resolution),
            file=sys.stderr,
        )
        return None, EXIT_INCOMPLETE

    results: list[tuple[int, int, str]] = []
    for issue in resolution.issues:
        try:
            text, code = run_one(issue)
        except NotAWorkItem as inner:
            # A resolved number that is ITSELF an open PR is never RE-resolved
            # (a PR whose only reference is a peer PR would recurse), so there
            # is no verdict for THIS item — but that is one item's problem, and
            # it must not discard the items that DID run. #9970 review measured
            # the shipped shape returning from inside this loop, BEFORE the
            # aggregate: over `[#100 LANDED, #8000 <an open PR>]` it reported
            # "nothing was checked" (exit 2) while `#100` had been checked and
            # its terminal LANDED was already computed — losing the precedence
            # the exit-code docstring gives 4 over 2 AND claiming a check that
            # ran never happened. So the refusal becomes THIS item's report, an
            # item-level `EXIT_INCOMPLETE`, and `_EXIT_SEVERITY` decides as it
            # does for every other item. The line is ANCHORED so the one-item
            # shape also leaves a text-keyed reader a verdict instead of an
            # empty value.
            text = (
                f"{_VERDICT_LINE_PREFIX}{_EXIT_LABEL[EXIT_INCOMPLETE]} "
                f"(exit {EXIT_INCOMPLETE}) — the work item #{issue}, which "
                f"#{number} resolves to, is ITSELF an open pull request — "
                f"{inner} It is not re-resolved (that can recurse), so THIS "
                "work item could not be gated; ask again with the issue it "
                "serves. Do NOT dispatch.\n"
            )
            code = EXIT_INCOMPLETE
        results.append((code, issue, text))

    worst_code = max(results, key=lambda row: _EXIT_SEVERITY[row[0]])[0]
    # Worst FIRST — the DECIDING work item is the one a reader needs.
    #
    # ⛔ WHAT PROTECTS A TEXT-KEYED CALLER IS NOT THIS ORDER. Sorted worst-LAST
    # (the natural reading of "the last line decides"), a resolution over
    # `[#100 CLEAN, #200 COLLISION]` printed CLEAN first — and the fleet's only
    # consumer reads the FIRST anchored line
    # (`agent-infra/scripts/orchestrator-harness/refresh-candidates.sh`:
    # `grep -E '^VERDICT' | head -1`, never compared against the exit code), so
    # it took CLEAN — "dispatch" — out of a report whose exit code refuses the
    # dispatch. Reversing the order alone would leave the same bug for a tail
    # reader.
    #
    # ⛔ SO A MULTI-ITEM RUN PRINTS EXACTLY ONE anchored `VERDICT:` line — the
    # aggregate, below — and the per-item lines are re-prefixed by
    # `_demote_verdict_lines`. An anchored `^VERDICT` read can then only ever see
    # the decision, whichever end it reads from: the consumer is repaired without
    # touching it (it is another repo's harness), and no caller has to know the
    # run was a resolution.
    results.sort(key=lambda row: _EXIT_SEVERITY[row[0]], reverse=True)
    map_text = ", ".join(f"#{issue}" for issue in resolution.issues)
    header = (
        f"collision-preflight: a PULL REQUEST number was given — #{number}. A "
        "pull request number is ALSO an issue number, so a verdict computed for "
        f"#{number} would describe the PR and never the work it serves. Resolved "
        f"via {_resolution_route(resolution)}: #{number} → {map_text}. The "
        "report(s) below are computed for the WORK ITEM(S); exit 0 still means "
        "dispatch.\n"
    )
    if len(results) == 1:
        # One work item: its own `VERDICT:` line IS the aggregate — same label,
        # same code — so it is left ANCHORED and nothing is added. Demoting it
        # would leave a single-item resolved run with NO anchored verdict at all:
        # the text-keyed consumer would read an empty value and queue nothing,
        # the same class of silent defeat as the bug this change removes.
        return header + results[0][2], worst_code
    per_item = ", ".join(
        f"#{issue} {_EXIT_LABEL[code]}" for code, issue, _text in results
    )
    body = "".join(
        _demote_verdict_lines(text, issue) for _code, issue, text in results
    )
    aggregate = (
        f"VERDICT: {_EXIT_LABEL[worst_code]} (exit {worst_code}) — PR #{number} "
        f"resolves to {len(results)} work item(s); the WORST decides"
        + ("; do NOT dispatch" if worst_code != EXIT_CLEAN else "")
        + ".\n"
    )
    tail = (
        f"RESOLUTION: PR #{number} → {len(results)} work item(s) "
        f"({per_item}); the WORST decides "
        f"{_EXIT_LABEL[worst_code]} (exit {worst_code})"
        + ("; do NOT dispatch" if worst_code != EXIT_CLEAN else "")
        + ".\n"
    )
    return header + aggregate + body + tail, worst_code


#: The anchored verdict prefix every report line uses, and the ONE prefix a
#: text-keyed caller greps. Only the aggregate may carry it in a multi-item run.
_VERDICT_LINE_PREFIX = "VERDICT: "


def _demote_verdict_lines(text: str, issue: int) -> str:
    """Re-prefix a per-work-item report's `VERDICT:` line so `^VERDICT` misses it.

    A run over SEVERAL resolved work items must leave exactly ONE anchored
    `VERDICT:` line — the aggregate — because the fleet's only text-keyed
    consumer reads the FIRST anchored one and does not compare it against the
    exit code (see `_verdict_for_pull_request`). Re-prefixing rather than
    reordering is what makes that true for a head reader AND a tail reader, and
    it keeps every per-item verdict visible and attributable (`WORK ITEM #2712
    VERDICT: COLLISION …`) instead of hiding the losing ones.

    Only the anchored `VERDICT: ` prefix is touched, so the `ALSO INCOMPLETE:`
    marker and every advisory row keep the exact shape their own consumers
    parse.
    """
    if not text:
        return text
    return "\n".join(
        f"WORK ITEM #{issue} {line}" if line.startswith(_VERDICT_LINE_PREFIX)
        else line
        for line in text.split("\n")
    )


def main(argv: list[str] | None = None) -> int:
    parser = _UsageParser(
        prog="collision_preflight",
        description="Fail-loud, all-surface in-flight-work pre-flight for a GitHub issue (#3061).",
    )
    parser.add_argument(
        "issue", type=int,
        help="issue number to check, e.g. 3061 — or an OPEN pull request number, "
             "which is RESOLVED to the work item(s) it serves (#9957)",
    )
    parser.add_argument(
        "--repo", default=None,
        help="target repo: `owner/name` (a local clone is located for the git "
             "surfaces) OR a path to a worktree of the target repo. Omitted: the "
             "current directory, with a cross-repo ambiguity refusal rather than a "
             "guess (#4027)",
    )
    # ── self-identity: DECLARED, never inferred (#3504 classes 1/4) ─────────
    # The caller knows its own branch and worktree at call time. Passing them is
    # how a hit on the caller's OWN artifacts stops being read as a competing
    # claim — the fix that a text heuristic could not make, because "is this
    # mine?" is not a property of the string. Repeatable: a lane may own several
    # refs. Omitting these flags is fail-CLOSED — adding a ref to the self set
    # REMOVES hits, so a missing declaration can only leave more refs blocking.
    # The AUTO-DETECTION below is the opposite: it is fail-OPEN, because it
    # declares the target checkout's own branch and root unbidden. The flags are
    # the authoritative input; the auto-detection is the convenience.
    parser.add_argument(
        "--self-branch", action="append", default=[], metavar="REF",
        help="a branch this session OWNS (repeatable). A hit on it is reported "
             "as your own work and cannot block. The target checkout's current "
             "branch is added automatically UNLESS it is recognised as the "
             "repository's default branch. (Recognition seeds main/master and "
             "resolves origin/HEAD; with origin/HEAD unresolvable, a default "
             "branch outside the seed set — trunk, develop — is NOT recognised "
             "and IS auto-declared. A known gap, pinned by a test rather than "
             "hidden.)",
    )
    parser.add_argument(
        "--self-worktree", action="append", default=[], metavar="PATH",
        help="a worktree this session OWNS (repeatable). A hit on it is reported "
             "as your own work and cannot block. The target checkout's own root "
             "is added automatically.",
    )
    parser.add_argument("--gh", default=os.environ.get("COLLISION_PREFLIGHT_GH", "gh"),
                        help="gh binary (env COLLISION_PREFLIGHT_GH)")
    parser.add_argument("--git", default=os.environ.get("COLLISION_PREFLIGHT_GIT", "git"),
                        help="git binary (env COLLISION_PREFLIGHT_GIT)")
    # Deliberately NO `type=` on --timeout / --pr-limit / --closed-pr-limit, and
    # each env default is passed through RAW. A bad value must be EXIT_USAGE, and
    # neither argparse's own error path (exits 2 == EXIT_INCOMPLETE) nor an
    # eagerly-converted default (an uncaught ValueError out of `add_argument` ->
    # traceback + exit 1 == EXIT_COLLISION) reports a misconfiguration as itself:
    # `COLLISION_PREFLIGHT_TIMEOUT=abc` used to read as "another lane is on it"
    # (#3619, #4053). Coerced and validated below.
    parser.add_argument("--timeout",
                        default=os.environ.get("COLLISION_PREFLIGHT_TIMEOUT", DEFAULT_TIMEOUT),
                        metavar="SECS",
                        help="per-command timeout in seconds (env COLLISION_PREFLIGHT_TIMEOUT)")
    parser.add_argument("--pr-limit",
                        default=os.environ.get("COLLISION_PREFLIGHT_PR_LIMIT", PR_LIMIT),
                        metavar="N",
                        help="open-PR completeness cap; a longer list is TRUNCATED/INCOMPLETE "
                             f"(default {PR_LIMIT}; env COLLISION_PREFLIGHT_PR_LIMIT)")
    parser.add_argument("--closed-pr-limit",
                        default=os.environ.get("COLLISION_PREFLIGHT_CLOSED_PR_LIMIT", CLOSED_PR_LIMIT),
                        metavar="N",
                        help="closed-PR SAMPLE size (per_page of the single "
                             "request). The response's own Link header gives the "
                             "total, so a sample smaller than the total is "
                             "reported as ~N but is NOT INCOMPLETE — this surface "
                             "is ADVISORY and cannot block a dispatch "
                             f"(default {CLOSED_PR_LIMIT}; env COLLISION_PREFLIGHT_CLOSED_PR_LIMIT)")
    # Deliberately NO ``type=float`` here. A bad value must be EXIT_USAGE, and
    # neither argparse's own error path (exits 2 == EXIT_INCOMPLETE) nor an
    # eagerly-converted env default (uncaught ValueError -> exit 1 ==
    # EXIT_COLLISION) reports a misconfiguration as itself. Validated below.
    parser.add_argument("--closed-pr-timeout",
        default=os.environ.get("COLLISION_PREFLIGHT_CLOSED_PR_TIMEOUT", CLOSED_PR_TIMEOUT),
        metavar="SECS",
        help="wall-clock budget (secs) for the single closed-PR request"
             f" (default {CLOSED_PR_TIMEOUT:g}; env "
             "COLLISION_PREFLIGHT_CLOSED_PR_TIMEOUT)")
    args = parser.parse_args(argv)

    if args.issue <= 0:
        print("collision-preflight: issue number must be positive", file=sys.stderr)
        return EXIT_USAGE
    # Coerce the raw seam strings above and report a bad one as EXIT_USAGE — the
    # same treatment --closed-pr-timeout gets further down.
    try:
        args.timeout = float(args.timeout)
    except (TypeError, ValueError):
        print("collision-preflight: --timeout must be a number > 0", file=sys.stderr)
        return EXIT_USAGE
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print("collision-preflight: --timeout must be finite and > 0", file=sys.stderr)
        return EXIT_USAGE
    for flag, attr in (("--pr-limit", "pr_limit"),
                       ("--closed-pr-limit", "closed_pr_limit")):
        try:
            value = int(getattr(args, attr))
        except (TypeError, ValueError):
            print(f"collision-preflight: {flag} must be an integer >= 1",
                  file=sys.stderr)
            return EXIT_USAGE
        if value < 1:
            print(f"collision-preflight: {flag} must be >= 1", file=sys.stderr)
            return EXIT_USAGE
        setattr(args, attr, value)

    # `nan`/`inf` are the trap: `nan <= 0` and `inf <= 0` are both False, so a
    # bare positivity check passes them to subprocess.run(timeout=…), where they
    # raise ValueError/OverflowError out of `_run` — no VERDICT line, exit 1 (the
    # COLLISION code). Reject non-numeric and non-finite explicitly.
    try:
        closed_pr_timeout = float(args.closed_pr_timeout)
    except (TypeError, ValueError):
        print("collision-preflight: --closed-pr-timeout must be a number > 0",
              file=sys.stderr)
        return EXIT_USAGE
    if not math.isfinite(closed_pr_timeout) or closed_pr_timeout <= 0:
        print("collision-preflight: --closed-pr-timeout must be > 0", file=sys.stderr)
        return EXIT_USAGE

    target, code = _resolve_target(args, args.timeout)
    if target is None:
        return code

    # Ambiguity refusal only when `--repo` was omitted: an explicit `--repo`
    # (either form) is the operator's disambiguation and must not be second-guessed.
    refusal = _ambiguity_refusal(args, target, args.timeout)
    if refusal is not None:
        return refusal

    # Identity has TWO halves and they answer different questions.
    #
    # `login` is the GitHub account. The ASSIGNEE surface compares it. It does
    # NOT attribute work to a specific lane — every lane shares this account,
    # which is why the claim gate reads the fleet board (#8175).
    #
    # `self_branches` / `self_worktrees` are the caller's OWN refs, and they are
    # the half that fixes #3504: the caller's own artifacts are excluded by
    # IDENTITY rather than by inspecting a string. `--self-branch` /
    # `--self-worktree` are ALWAYS authoritative and are never second-guessed.
    #
    # The best-effort auto-detection below is a different matter, and it is
    # gated on `_standing_in` deliberately. Adding a ref to the self set
    # REMOVES hits, so "it can only leave more hits" — the claim this block used
    # to make — is false: it is the FAIL-OPEN direction. And the checkout is not
    # always the caller's. With `--repo owner/name`, `_resolve_target` searches
    # for a clone; when the cwd is a different repo it finds the canonical hub
    # checkout, typically sitting on `main`. Declaring `main` as a self-branch
    # then suppresses a PR whose head branch is `main` — the ordinary shape for
    # a fork PR — and reports CLEAN on real in-flight work.
    #
    # So: auto-declare only for a checkout the CALLER chose. Omitting
    # `--self-branch` remains fail-closed, and a caller whose checkout was
    # searched for can always pass it explicitly.
    cwd_for_identity = target.path or os.getcwd()
    self_branches = {b.strip() for b in args.self_branch if b and b.strip()}
    self_worktrees = {
        w.strip().rstrip("/") for w in args.self_worktree if w and w.strip()
    }
    if target.path and _standing_in(target.path):
        self_worktrees.add(target.path.rstrip("/"))
        rc, out, _err, _to = _run(
            [args.git, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd_for_identity, args.timeout,
        )
        if rc == 0 and out.strip() and out.strip() != "HEAD":
            # `HEAD` means detached (no branch) — nothing to declare. A failure
            # here is not reported: it can only leave hits blocking.
            #
            # ⛔ AND NEVER THE DEFAULT BRANCH. This is not a refinement, it is the
            # difference between the tool working and the tool being inert: the
            # hub checkout sits on `main`, so `--repo .` — the documented,
            # default invocation — would otherwise declare `main` as a
            # self-branch, and every open PR whose head is `main` (the ordinary
            # fork-PR shape) is demoted to weak before the closing-reference test
            # is reached. An explicit `--self-branch main` is still honoured:
            # that is the caller ASSERTING it, and the assertion is theirs to
            # make. This only refuses to make it for them.
            _branch = out.strip()
            _defaults = _default_branch_names(
                args.git, cwd_for_identity, args.timeout
            )
            if not _is_default_branch(_branch, _defaults):
                self_branches.add(_branch)

    identity = Identity(
        login=None,
        self_branches=frozenset(self_branches),
        self_worktrees=frozenset(self_worktrees),
    )
    rc, out, _err, _to = _run(
        [args.gh, "api", "user", "-q", ".login"],
        cwd_for_identity, args.timeout,
    )
    if rc == 0 and out.strip():
        identity.login = out.strip()

    def _run_one(number: int) -> tuple[str, int]:
        """The pre-flight + report for ONE number.

        Factored out (#9957) so a RESUMED run — one that resolved a PR number to
        its work item — goes through the identical path. A second entry point
        would be a second set of surfaces to keep in step.
        """
        ordered, title, issue = run_preflight(
            number, target, args.gh, args.git, args.timeout,
            args.pr_limit, args.closed_pr_limit,
            closed_pr_timeout, identity,
        )
        return format_report(ordered, issue, target, title)

    try:
        try:
            report, code = _run_one(args.issue)
        except NotAWorkItem as exc:
            # The refusal is about the QUESTION, not about a surface, so it must
            # not be absorbed into a per-surface note and must never become a
            # verdict.
            #
            # ⛔ #9957: THE REFUSAL IS NOW THE RESOLUTION TRIGGER. A PR number is
            # a normal dispatch input, and the gate already holds the work item's
            # number — so it answers about the WORK rather than asking the caller
            # to obey prose. Only a resolution that FAILS still ends here, and it
            # then names what it could not resolve (exit 2, the protocol's "do
            # not start on a guess" channel, #7009).
            report, code = _verdict_for_pull_request(exc, args.issue, _run_one)
            if report is None:
                return code
    except RuntimeError as exc:  # partial-run guard — BOTH paths
        print(f"collision-preflight: {exc}", file=sys.stderr)
        return EXIT_USAGE
    sys.stdout.write(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
