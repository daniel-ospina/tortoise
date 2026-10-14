#!/usr/bin/env bash
# selftest.sh — offline checks for the shipped harness. No network, no tmux server, no git writes.
#
# IMPORTANT — how these checks are written.
# Several defects found in review were "the logic looks fixed but nothing calls it". So a check that
# RE-IMPLEMENTS the fixed logic proves nothing: it passes on the broken copy too. Every check below
# therefore EXTRACTS the real code from the shipped script (the classifier, sess(), esc(), the
# claim block, the reaper) or runs the real script, and asserts on its behaviour. Verified the hard
# way: dropping this file into the pre-fix tree fails these checks.
#
#   ./selftest.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pass=0; fail=0
ck() { # ck <name> <0|1>
  if [ "$2" -eq 0 ]; then printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass+1));
  else printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); fi
}
LOOP="$HERE/orch-loop.sh"; MERGE="$HERE/merge-one.sh"; INST="$HERE/install.sh"; REFRESH="$HERE/refresh-candidates.sh"
GATE="$HERE/spawn-gate.sh"   # the ONE load gate and the ONE `pi` invocation (agent-infra#1720)
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
# A missing extraction must FAIL, not warn: if the shipped script lacks the code under test the
# dependent checks would pass on an empty file — the exact vacuity this file was rewritten to fix.
need() { if [ -s "$1" ]; then return 0; else ck "$2" 1; return 1; fi; }
echo "==> offline self-test ($HERE)"

# ── 1) every script parses ───────────────────────────────────────────────────────
rc=0
for f in "$HERE"/*.sh "$HERE"/retired/*.sh; do
  [ "$(basename "$f")" = "selftest.sh" ] && continue
  bash -n "$f" || { echo "      syntax error: $f"; rc=1; }
done
ck "all shell scripts parse (bash -n)" $rc

# ── 2) the shipped sess() matches session names exactly ──────────────────────────
# Extracted from orch-loop.sh and driven against a fake `tmux`, so this pins the real function.
# `tmux has-session -t fix-82` prefix-matches `fix-820`, which starved PRs whose number prefixes
# a running worker's session name.
# sess() is a one-liner, so extract the single line (a range to /^}/ would swallow the whole loop).
grep -m1 '^sess() {' "$LOOP" > "$TMP/sess.sh"
if need "$TMP/sess.sh" "sess() is extracted from the shipped orch-loop.sh"; then
  tmux() { printf 'fix-820\nmerge-821\nlane-5\n'; }
  # shellcheck disable=SC1090
  . "$TMP/sess.sh"
  sess "fix-82"  && rc=1 || rc=0; ck "shipped sess(): fix-82 does NOT match fix-820" $rc
  sess "fix-820" && rc=0 || rc=1; ck "shipped sess(): fix-820 matches its own name" $rc
  sess "fix-8200" && rc=1 || rc=0; ck "shipped sess(): fix-8200 does NOT match fix-820" $rc
  unset -f tmux
fi

# ── 3) the shipped classifier is fail-closed on ai-review-gate ───────────────────
# Extracted from orch-loop.sh verbatim; only the bash-side $REQ is substituted.
REQ="ai-review-gate python-ci-gate docs test-isolation license-surface legal-e2e pricing-artifact"
awk '/^import sys,json$/,/^" 2>\/dev\/null\)\)$/' "$LOOP" | sed 's/^" 2>\/dev\/null))$//' \
  | sed "s/'\\\$REQ'/'$REQ'/" | sed -n '1,/^print(/p' > "$TMP/classify.py"
cl() { printf '%s' "$1" | python3 "$TMP/classify.py" 2>/dev/null; }
if need "$TMP/classify.py" "the classifier is extracted from the shipped orch-loop.sh"; then
got="$(cl '[{"name":"ai-review-gate","state":"SKIPPED"},{"name":"docs","state":"SKIPPED"}]')"
[ "$got" = "FAILED:ai-review-gate" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: ai-review-gate SKIPPED is not satisfied" $rc
got="$(cl '[{"name":"ai-review-gate","state":"NEUTRAL"}]')"
[ "$got" = "FAILED:ai-review-gate" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: ai-review-gate NEUTRAL is not satisfied" $rc
got="$(cl '[{"name":"docs","state":"SUCCESS"}]')"
[ "$got" = "FAILED:ai-review-gate" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: a missing ai-review-gate row is not satisfied" $rc
got="$(cl '[{"name":"ai-review-gate","state":"SUCCESS"},{"name":"python-ci-gate","state":"SUCCESS"},{"name":"docs","state":"SKIPPED"},{"name":"test-isolation","state":"SUCCESS"},{"name":"license-surface","state":"SUCCESS"},{"name":"legal-e2e","state":"NEUTRAL"},{"name":"pricing-artifact","state":"SUCCESS"}]')"
[ "$got" = "GREEN" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: gate SUCCESS + path-filtered legs skipped = GREEN" $rc
got="$(cl '[{"name":"ai-review-gate","state":"FAILURE"}]')"
[ "$got" = "FAILED:ai-review-gate" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: gate FAILURE is reported failed" $rc

# ── 3b) the classifier's POLARITY — an allow-list, not a deny-list ───────────────
# agent-infra#1714: the classifier named the red conclusions and filed EVERYTHING ELSE as PENDING,
# so an unrecognised or null conclusion reached `gh pr merge --auto` (fail OPEN), while CANCELLED —
# non-red, a superseded run is normal — was named red and routed to the fixer. The rule is the one
# admin-merge.sh and record-review.sh already implement, so these fixtures pin the shipped
# classifier TO THAT RULE rather than to a copy of it.
#
# What each check below is EVIDENCE FOR, stated rather than overclaimed: measured against the
# pre-fix tree (this file dropped in beside the deny-list scripts), the polarity fixtures fail
# there — EXCEPT those whose subject predates this change (red stays red, an empty surface is not
# green) and the §3d shell checks, which guard the backtick hazard this change's own comment text
# introduces (the deny-list base has no such comments, so they pass there by construction). Those
# are regression guards, not evidence for the polarity; the fixtures that DO discriminate assert an
# exact verdict, never "not GREEN" (an empty string from a crashed extractor satisfies any negative
# assertion).
full() { # full <required-name> <state|conclusion, or the literal null> -> a full required surface
  python3 - "$1" "$2" <<'PY'
import json,sys
n,s=sys.argv[1],sys.argv[2]
rows=[{"name":x,"state":"SUCCESS"} for x in ("ai-review-gate","python-ci-gate","docs",
      "test-isolation","license-surface","legal-e2e","pricing-artifact")]
for r in rows:
    if r["name"]==n: r["state"]=None if s=="null" else s
print(json.dumps(rows))
PY
}
allstates() { # allstates <state|conclusion> -> every required row carrying that same state
  python3 - "$1" <<'PY'
import json,sys
s=sys.argv[1]
print(json.dumps([{"name":x,"state":s} for x in ("ai-review-gate","python-ci-gate","docs",
      "test-isolation","license-surface","legal-e2e","pricing-artifact")]))
PY
}
# (a) THE FALSE-GREEN: a null conclusion is RED, never PENDING (PENDING arms auto-merge).
got="$(cl "$(full docs null)")"
[ "$got" = "FAILED:docs" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: a null conclusion is RED, not PENDING" $rc
# (b) a conclusion GitHub has not documented is RED too — the allow-list, not a red-token list.
got="$(cl "$(full docs MYSTERY_FAILURE)")"
[ "$got" = "FAILED:docs" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: an unrecognised conclusion is RED, not PENDING" $rc
# (c) an unrecognised STATUS is judged by its conclusion (the rail's #1353 shape): null -> RED.
got="$(cl '[{"name":"ai-review-gate","status":"completed","conclusion":"success"},{"name":"docs","status":"completely_finished","conclusion":null}]')"
[ "$got" = "FAILED:docs" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: an unrecognised status with a null conclusion is RED" $rc
# (d) CANCELLED/STALE are NON-RED: a superseded run must not block the merge.
got="$(cl "$(full python-ci-gate CANCELLED)")"
[ "$got" = "GREEN" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: CANCELLED does not block (non-red)" $rc
got="$(cl "$(full legal-e2e STALE)")"
[ "$got" = "GREEN" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: STALE does not block (non-red)" $rc
# (e) …but they are NOT green either: a surface of nothing but cancelled/stale proves nothing,
# so it must never read GREEN (a skipped/cancelled shard is not coverage — the rail's doctrine).
# The FULL required surface, every row cancelled/stale INCLUDING the gate — the gate rule is what
# refuses it, and asserting the EXACT verdict pins that. An inexact "!= GREEN" would pass on the
# deny-list too (it reads every CANCELLED row as red, i.e. FAILED), so this fixture would prove
# nothing about the polarity; the exact form discriminates.
got="$(cl "$(allstates CANCELLED)")"
[ "$got" = "FAILED:ai-review-gate" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: an all-cancelled surface cannot read GREEN (the gate refuses it)" $rc
# STALE across the WHOLE surface, with the gate a real success: non-red rows must not force
# PENDING. Exact verdict, so the deny-list (which names no STALE token at all and reads them as
# neither ok nor bad) gives PENDING and fails here.
got="$(cl "$(python3 - <<'PY'
import json
print(json.dumps([{"name":"ai-review-gate","state":"SUCCESS"}]+[{"name":x,"state":"STALE"} for x in
      ("python-ci-gate","docs","test-isolation","license-surface","legal-e2e","pricing-artifact")]))
PY
)")"
[ "$got" = "GREEN" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: STALE rows across the surface do not force PENDING (non-red)" $rc
# (f) an EMPTY surface is not green — a missing check is not a green check (absence != green).
got="$(cl '[]')"
[ "$got" = "NOCHECKS" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: an EMPTY check surface is NOCHECKS, not GREEN" $rc
got="$(cl '{}')"
[ "$got" = "NOCHECKS" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: an unreadable payload is NOCHECKS, not GREEN" $rc
# (g) RED beats PENDING and beats the greens: a completed red is final.
got="$(cl "$(full docs FAILURE)")"
[ "$got" = "FAILED:docs" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: a red among several green is RED" $rc
got="$(cl '[{"name":"ai-review-gate","state":"SUCCESS"},{"name":"docs","state":"FAILURE"},{"name":"python-ci-gate","state":"IN_PROGRESS"}]')"
[ "$got" = "FAILED:docs" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: a red is final — it is not masked by an in-flight check" $rc
# (h) grouping is per (app, name): one app's result must not mask the other's. The red row is
# placed FIRST and a non-red row LAST, so the pre-fix per-name map (last write wins) reads NEUTRAL
# and this fixture fails there — with the red last it would pass on the deny-list and prove nothing.
got="$(cl '[{"name":"ai-review-gate","state":"SUCCESS"},{"name":"docs","state":"FAILURE","workflow":"w2"},{"name":"docs","state":"NEUTRAL","workflow":"w1"}]')"
[ "$got" = "FAILED:docs" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: a same-named check under two apps cannot mask a red" $rc
# (i) the NEWEST attempt by id governs (the check-runs API shape). Assert the EXACT verdict, never
# a "not FAILED" match — an empty string from a crashed extractor satisfies that form (the pre-fix
# classifier raises TypeError on this payload and prints nothing).
got="$(cl '{"check_runs":[{"id":1,"name":"ai-review-gate","status":"completed","conclusion":"success","app":{"slug":"a"}},{"id":5,"name":"docs","status":"completed","conclusion":"failure","app":{"slug":"a"}},{"id":9,"name":"docs","status":"completed","conclusion":"success","app":{"slug":"a"}}]}')"
[ "$got" = "PENDING:python-ci-gate,test-isolation,license-surface,legal-e2e,pricing-artifact" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: a newer passing attempt supersedes an older red" $rc
# (i2) …and it is by ID, not by position: with the NEWEST attempt listed FIRST and the older red
# LAST, "last row wins" would read the red. Pinned in both directions, so a regression to
# last-wins cannot survive whichever order an API happens to return.
got="$(cl '{"check_runs":[{"id":1,"name":"ai-review-gate","status":"completed","conclusion":"success","app":{"slug":"a"}},{"id":9,"name":"docs","status":"completed","conclusion":"success","app":{"slug":"a"}},{"id":5,"name":"docs","status":"completed","conclusion":"failure","app":{"slug":"a"}}]}')"
[ "$got" = "PENDING:python-ci-gate,test-isolation,license-surface,legal-e2e,pricing-artifact" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: the newest attempt by id governs even when it is not the last row" $rc
got="$(cl '{"check_runs":[{"id":1,"name":"ai-review-gate","status":"completed","conclusion":"success","app":{"slug":"a"}},{"id":12,"name":"docs","status":"completed","conclusion":"failure","app":{"slug":"a"}},{"id":9,"name":"docs","status":"completed","conclusion":"success","app":{"slug":"a"}}]}')"
[ "$got" = "FAILED:docs" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: a newest-by-id red is not masked by a later-listed older green" $rc
got="$(cl '{"check_runs":[{"id":1,"name":"ai-review-gate","status":"completed","conclusion":"success","app":{"slug":"a"}},{"id":9,"name":"docs","status":"completed","conclusion":"success","app":{"slug":"a"}},{"id":12,"name":"docs","status":"completed","conclusion":"failure","app":{"slug":"a"}}]}')"
[ "$got" = "FAILED:docs" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped classifier: a newer failing attempt supersedes an older green" $rc
fi

# ── 3c) the fixer's failing-check list uses the SAME polarity (agent-infra#1714) ──
# The loop hands a red required check to fix-one.sh; its list named CANCELLED as failing, so a
# normally-superseded run burned a capped fixer attempt on a PR that was not red. Extracted from
# the shipped fix-one.sh, so this pins the real list rather than a re-implementation of it.
awk '/^FAILING_CHECKS="/,/^" 2>\/dev\/null\)"$/' "$HERE/fix-one.sh" \
  | sed -n '/^import sys,json$/,$p' | sed '/^" 2>\/dev\/null)"$/d' > "$TMP/failing_checks.py"
fc() { printf '%s' "$1" | python3 "$TMP/failing_checks.py" 2>/dev/null; }
if need "$TMP/failing_checks.py" "the fixer's failing-check list is extracted from fix-one.sh"; then
got="$(fc '[{"name":"docs","state":"FAILURE"},{"name":"legal-e2e","state":"CANCELLED"},{"name":"test-isolation","state":"STALE"},{"name":"license-surface","state":"NEUTRAL"}]')"
[ "$got" = "docs" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped fixer list: CANCELLED/STALE are not handed to the fixer" $rc
got="$(fc '[{"name":"docs","state":null},{"name":"legal-e2e","state":"MYSTERY_FAILURE"}]')"
[ "$got" = "docs legal-e2e" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped fixer list: a null/unrecognised state IS handed to the fixer (fail closed)" $rc
got="$(fc '[{"name":"docs","state":"SUCCESS"},{"name":"python-ci-gate","state":"IN_PROGRESS"}]')"
[ -z "$got" ] && rc=0 || { rc=1; echo "      got '$got'"; }
ck "shipped fixer list: a green/in-flight surface yields no failing check" $rc
fi

# ── 3d) the classifier travels through BASH: no backtick command-substitution ────
# The checks above extract the Python with awk/sed, so they never exercise the shell. The
# classifier is built as `V="$(… | python3 -c " <python> ")"`; an UNESCAPED backtick inside that
# double-quoted string is COMMAND-SUBSTITUTED by bash before Python ever sees it. A comment that
# spells `gh pr merge --auto` therefore RAN that command every cycle (the exact fail-open this
# PR exists to close), and a backticked `gh pr checks` spliced a multi-line table into the Python
# source (SyntaxError -> V="" -> no case arm matched). Extract the real assignment and drive it
# through a logged `gh` shim so the shell, not an awk extraction, is what runs.
awk '/^    V="\$\(gh pr checks/{f=1} f{print} /^" 2>\/dev\/null\)"$/{f=0}' "$LOOP" > "$TMP/classify_bash.sh"
if need "$TMP/classify_bash.sh" "the classifier's bash assignment is extracted from orch-loop.sh"; then
  mkdir -p "$TMP/bin"
  cat > "$TMP/bin/gh" <<'SH'
#!/usr/bin/env bash
printf 'GH_INVOKED: %s\n' "$*" >> "$GH_LOG"
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "checks" ]; then
  printf '[{"name":"ai-review-gate","state":"SUCCESS"},{"name":"python-ci-gate","state":"SUCCESS"},{"name":"docs","state":"SUCCESS"},{"name":"test-isolation","state":"SUCCESS"},{"name":"license-surface","state":"SUCCESS"},{"name":"legal-e2e","state":"SUCCESS"},{"name":"pricing-artifact","state":"SUCCESS"}]'
fi
exit 0
SH
  chmod +x "$TMP/bin/gh"
  # Assemble a driver: the real assignment verbatim, with the bash-side inputs it reads.
  {
    printf '%s\n' 'set -u'
    printf '%s\n' "OR='example/repo'"
    printf '%s\n' 'PR=8518'
    printf '%s\n' "REQ='ai-review-gate python-ci-gate docs test-isolation license-surface legal-e2e pricing-artifact'"
    cat "$TMP/classify_bash.sh"
    printf '%s\n' 'printf "V=%s\n" "$V"'
  } > "$TMP/drive.sh"
  # Only the pipeline's own `gh pr checks <PR> --json …` may run. A `pr merge` (or a bare
  # `pr checks`) in the log is a backtick leaking out of a comment as an executed command.
  : > "$TMP/gh.log"
  out="$(PATH="$TMP/bin:$PATH" GH_LOG="$TMP/gh.log" bash "$TMP/drive.sh" 2>"$TMP/drive.err")"
  n="$(wc -l < "$TMP/gh.log" | tr -d ' ')"
  [ "$n" = "1" ] && rc=0 || { rc=1; echo "      gh calls ($n): $(tr '\n' ';' < "$TMP/gh.log")"; }
  ck "shipped classifier: comments are not executed as commands (exactly one gh call)" $rc
  if grep -q 'pr merge' "$TMP/gh.log" 2>/dev/null; then rc=1; else rc=0; fi
  ck "shipped classifier: no backticked \`gh pr merge\` escapes from a comment" $rc
  if grep -q 'command not found' "$TMP/drive.err" 2>/dev/null; then rc=1; else rc=0; fi
  ck "shipped classifier: no backticked word is run as a command" $rc
  case "$out" in *V=GREEN*) rc=0;; *) rc=1; echo "      out: $out";; esac
  ck "shipped classifier: the bash substitution still yields GREEN" $rc
fi

# ── 4) the shipped claim block refuses a second owner ────────────────────────────
# Extracted from merge-one.sh and executed, so this pins the real lock, not a copy of the idiom.
sed -n '/^CLAIM="\$O\/state\/mergeclaim-\$PR.lock"/,/^trap .rm -f "\$CLAIM".*$/p' "$MERGE" > "$TMP/claim.sh"
mkdir -p "$TMP/O/state"
if need "$TMP/claim.sh" "the claim block is extracted from the shipped merge-one.sh"; then
# The claim block logs to "$L.log", so assert on the log, not on stdout.
# (a) a claim held by a LIVE pid must be refused
printf '%s' "$$" > "$TMP/O/state/mergeclaim-900.lock"
out="$( O="$TMP/O" PR=900 L="$TMP/claimtest" bash -c "set -u; . '$TMP/claim.sh'; echo ALIVE" 2>&1 )"
if grep -q 'already owns this PR' "$TMP/claimtest.log" 2>/dev/null; then rc=0; else rc=1; echo "      log: $(cat "$TMP/claimtest.log" 2>/dev/null)"; fi
ck "shipped claim block: a live owner is refused" $rc
case "$out" in *ALIVE*) rc=1;; *) rc=0;; esac
ck "shipped claim block: the refused worker does not proceed" $rc
# (b) a claim held by a DEAD pid must be reclaimed
printf '%s' "999999" > "$TMP/O/state/mergeclaim-901.lock"
out="$( O="$TMP/O" PR=901 L="$TMP/claimtest2" bash -c "set -u; . '$TMP/claim.sh'; echo ALIVE" 2>&1 )"
case "$out" in *ALIVE*) rc=0 ;; *) rc=1; echo "      got '$out'";; esac
ck "shipped claim block: a dead owner's claim is reclaimed" $rc
fi

# ── 5) the shipped reaper un-blacklists only undelivered dispatches ──────────────
# Extracted from refresh-candidates.sh and run against fixtures:
#   4114 = open, no PR, no lane        -> must be REAPED
#   8888 = open, PR exists             -> must be KEPT
#   9999 = not open                    -> must be KEPT
#   7777 = a live lane-7777 session    -> must be KEPT
R="$TMP/reap"; mkdir -p "$R"
printf '4114\n8888\n9999\n7777\n' > "$R/dispatched.txt"
printf '4114\n8888\n9999\n' > "$R/checked.txt"
printf '4114\n8888\n' > "$R/.open_iss"
printf 'feat/8888-tm\n' > "$R/.pr_brs"
tmux() { printf 'lane-7777\nmerge-100\n'; }
# extract the embedded reaper (the heredoc body only)
awk '/^python3 - "\$O" "\$OR" <<.PY.$/{f=1;next} /^PY$/{f=0} f' "$REFRESH" > "$R/reap.py"
if need "$R/reap.py" "the reaper is extracted from the shipped refresh-candidates.sh"; then
# the extracted body shells out to tmux; export the fake so the child sees it
cat > "$R/tmux" <<'SH'
#!/bin/bash
printf 'lane-7777\nmerge-100\n'
SH
chmod +x "$R/tmux"
# the landed prune shells out to `gh issue view <N> --repo <slug> --json state`
# (UNCAPPED, per-entry) — the fake answers from GH_STUB_OPEN (`<N> ...` = open,
# anything else = closed), and GH_STUB_FAIL=1 is the unreadable case.
cat > "$R/gh" <<'SH'
#!/bin/bash
if [ "${GH_STUB_FAIL:-0}" = "1" ]; then
  echo "gh: could not reach api.github.com" >&2; exit 1
fi
case " ${GH_STUB_OPEN:-} " in *" $3 "*) echo '{"state":"OPEN"}'; exit 0;; esac
echo '{"state":"CLOSED"}'
SH
chmod +x "$R/gh"
( PATH="$R:$PATH" python3 "$R/reap.py" "$R" "acme/example" ) > "$TMP/reap.out" 2>&1
kept="$(tr '\n' ' ' < "$R/dispatched.txt")"
case "$kept" in *4114*) rc=1;; *) rc=0;; esac
ck "shipped reaper: open+no-PR+no-lane dispatch is un-blacklisted" $rc
case "$kept" in *8888*) rc=0;; *) rc=1;; esac
ck "shipped reaper: an issue with a PR is kept" $rc
case "$kept" in *9999*) rc=0;; *) rc=1;; esac
ck "shipped reaper: a closed issue is kept" $rc
case "$kept" in *7777*) rc=0;; *) rc=1;; esac
ck "shipped reaper: an issue with a live lane is kept" $rc
case "$(tr '\n' ' ' < "$R/checked.txt")" in *4114*) rc=1;; *) rc=0;; esac
ck "shipped reaper: the reaped entry is removed from checked.txt" $rc

# ── 5a) a FAILED listing must not be read as "no PRs" ────────────────────────────────────────
# The capture (line above the heredoc) DELETES the list when `gh` fails, and the guard at the top of
# the extracted reaper refuses the pass when either list is ABSENT. These cases pin BOTH halves:
# an ABSENT list = the read FAILED = reap NOTHING; a PRESENT but EMPTY list = a real answer = reap.
# Pre-fix, an absent/empty `.pr_brs` made `has_pr` false for every issue and 8888 — which HAS a PR
# in the populated control above — was un-blacklisted and re-dispatched. (agent-infra#1705)
RF="$TMP/reap-failpr"; mkdir -p "$RF"
printf '4114\n8888\n' > "$RF/dispatched.txt"
printf '4114\n8888\n' > "$RF/checked.txt"
printf '4114\n8888\n' > "$RF/.open_iss"
# .pr_brs deliberately ABSENT: this is the shape the shipped capture leaves on a failed `gh pr list`
( PATH="$R:$PATH" python3 "$R/reap.py" "$RF" "acme/example" ) > "$TMP/reap-failpr.out" 2>&1
kept="$(tr '\n' ' ' < "$RF/dispatched.txt")"
case "$kept" in *4114*8888*) rc=0;; *) rc=1; echo "      dispatched.txt: '$kept'";; esac
ck "shipped reaper: an UNREADABLE PR listing reaps NOTHING (both entries kept)" $rc
case "$(tr '\n' ' ' < "$RF/checked.txt")" in *4114*8888*) rc=0;; *) rc=1;; esac
ck "shipped reaper: an UNREADABLE PR listing leaves checked.txt untouched" $rc
grep -q 'SKIPPED' "$TMP/reap-failpr.out" && rc=0 || rc=1
ck "shipped reaper: the skipped pass says so (an unreadable listing is not an empty one)" $rc
# the same guard for the neighbouring `.open_iss` probe, which had the safe polarity by accident
RO="$TMP/reap-failopen"; mkdir -p "$RO"
printf '4114\n8888\n' > "$RO/dispatched.txt"
printf '4114\n8888\n' > "$RO/checked.txt"
printf 'feat/8888-tm\n' > "$RO/.pr_brs"
( PATH="$R:$PATH" python3 "$R/reap.py" "$RO" "acme/example" ) > "$TMP/reap-failopen.out" 2>&1
case "$(tr '\n' ' ' < "$RO/dispatched.txt")" in *4114*) rc=0;; *) rc=1;; esac
ck "shipped reaper: an UNREADABLE open-issue listing reaps nothing either" $rc
# a PRESENT but EMPTY list is a REAL answer and must still reap
RE="$TMP/reap-emptypr"; mkdir -p "$RE"
printf '4114\n7777\n' > "$RE/dispatched.txt"
printf '4114\n7777\n' > "$RE/checked.txt"
printf '4114\n' > "$RE/.open_iss"   # 7777 is not open -> kept
: > "$RE/.pr_brs"                     # readable and empty: there are genuinely no PRs
( PATH="$R:$PATH" python3 "$R/reap.py" "$RE" "acme/example" ) > "$TMP/reap-emptypr.out" 2>&1
kept="$(tr '\n' ' ' < "$RE/dispatched.txt")"
case "$kept" in *4114*) rc=1;; *) rc=0;; esac
ck "shipped reaper: a genuinely EMPTY (readable) PR listing still reaps 4114" $rc
case "$kept" in *7777*) rc=0;; *) rc=1;; esac
ck "shipped reaper: the empty-listing pass still keeps a non-open issue (7777)" $rc
fi
unset -f tmux

# ── 5b) the shipped refresh loop never blacklists a candidate it did not pre-flight ─
# `echo "$N" >> checked.txt` sat BEFORE `n=$((n+1)); [ "$n" -gt "$LIMIT" ] && break`, so the
# (LIMIT+1)-th candidate was recorded as checked and then the loop broke without ever running
# collision_preflight.py for it. checked.txt suppresses that issue forever and the §7.1 reaper
# only un-blacklists dispatched.txt entries — so one dispatchable issue was lost per refresh.
awk '/^n=0$/{f=1} f{print} f&&/^echo "\[refresh\] checked \$n;/{exit}' "$REFRESH" > "$TMP/refreshloop.sh"
if need "$TMP/refreshloop.sh" "the refresh loop body is extracted from the shipped refresh-candidates.sh"; then
  RL="$TMP/refresh"; mkdir -p "$RL/fakesc/.venv/bin"
  # The pool the SHIPPED producer writes: bare numbers, one per line (severity lives in the
  # sort, not in the file). A tier prefix leaking back into `.pool` is a malformed argument
  # to the loop below, so the stub FAILS on a non-numeric issue number instead of matching
  # `*10*` inside `0 10` and letting a broken producer look green (#8469 review P2).
  printf '10\n20\n30\n40\n50\n' > "$RL/.pool"
  : > "$RL/checked.txt"; : > "$RL/candidates.txt"; : > "$RL/dispatched.txt"
  cat > "$RL/fakesc/.venv/bin/python" <<'PY'
#!/bin/bash
# stand-in for the scout's venv python: every candidate pre-flights CLEAN.
# Argv is `collision_preflight.py <N> --repo .`, so the issue number is $2.
case "$2" in *[!0-9]*|"") echo "MALFORMED_ARG $2" >&2; exit 3;; esac
echo "VERDICT: CLEAN"
PY
  chmod +x "$RL/fakesc/.venv/bin/python"
  O="$RL" LIMIT=3 SC="$RL/fakesc" bash "$TMP/refreshloop.sh" > "$TMP/refreshloop.out" 2>&1
  checked="$(tr '\n' ' ' < "$RL/checked.txt")"
  case "$checked" in *40*) rc=1;; *) rc=0;; esac
  ck "shipped refresh loop: the candidate past LIMIT is NOT marked checked" $rc
  case "$checked" in *10*20*30*) rc=0;; *) rc=1;; esac
  ck "shipped refresh loop: the first LIMIT candidates are marked checked" $rc
  cands="$(tr '\n' ' ' < "$RL/candidates.txt")"
  case "$cands" in *10*20*30*) rc=0;; *) rc=1;; esac
  ck "shipped refresh loop: the pre-flighted CLEAN candidates reach the backlog" $rc

  # ── the no-verdict arm must not starve the entries behind it, and must be bounded (#8469
  # review P2). Counting every entry against LIMIT let a few persistently non-answering entries
  # at the head re-spend the whole pass, so the CLEAN entries behind them were never reached.
  RL2="$TMP/refresh2"; mkdir -p "$RL2/fakesc/.venv/bin"
  printf '10\n20\n30\n40\n' > "$RL2/.pool"
  : > "$RL2/checked.txt"; : > "$RL2/candidates.txt"; : > "$RL2/dispatched.txt"
  cat > "$RL2/fakesc/.venv/bin/python" <<'PY'
#!/bin/bash
# 10 and 20 ERROR (no VERDICT line); 30 and 40 are CLEAN. Argv is
# `collision_preflight.py <N> --repo .`, so the issue number is $2.
case "$2" in
  10|20) echo 'boom: TypeError' >&2; exit 1;;
  *[!0-9]*|"") exit 3;;
esac
echo 'VERDICT: CLEAN'
PY
  chmod +x "$RL2/fakesc/.venv/bin/python"
  O="$RL2" LIMIT=2 SC="$RL2/fakesc" bash "$TMP/refreshloop.sh" > "$TMP/refresh2.out" 2>&1
  c2="$(tr '\n' ' ' < "$RL2/checked.txt")"
  case "$c2" in *30*40*) rc=0;; *) rc=1;; esac
  ck "shipped refresh loop: a non-verdict head does not starve the CLEAN entries behind it" $rc
  case "$c2" in *10*|*20*) rc=1;; *) rc=0;; esac
  ck "shipped refresh loop: a failed pre-flight is still not blacklisted on attempt 1" $rc

  # A permanently non-answering entry must eventually leave the candidate set: after RETRY_MAX
  # consecutive attempts it is marked checked, so the cursor advances past it.
  RL3="$TMP/refresh3"; mkdir -p "$RL3/fakesc/.venv/bin"
  printf '10\n' > "$RL3/.pool"
  : > "$RL3/checked.txt"; : > "$RL3/candidates.txt"; : > "$RL3/dispatched.txt"
  cat > "$RL3/fakesc/.venv/bin/python" <<'PY'
#!/bin/bash
case "$2" in *[!0-9]*|"") exit 3;; esac
echo 'boom: no verdict' >&2; exit 1
PY
  chmod +x "$RL3/fakesc/.venv/bin/python"
  for _pass in 1 2 3; do
    O="$RL3" LIMIT=2 RETRY_MAX=3 SC="$RL3/fakesc" bash "$TMP/refreshloop.sh" > "$TMP/refresh3.out" 2>&1
  done
  if grep -q 'marked checked after 3 attempts' "$TMP/refresh3.out" && [ "$(tr '\n' ' ' < "$RL3/checked.txt")" = "10 " ]; then rc=0; else rc=1; fi
  ck "shipped refresh loop: a persistently non-verdict entry is retried then bounded (RETRY_MAX)" $rc

  # ── 5c) a pre-flight that produced NO verdict must not blacklist the issue ───────────────────
  # Same loop, but the stub python now fails the way a broken scout venv / API failure does: it
  # exits non-zero and emits no `VERDICT` line. The pre-fix loop wrote checked.txt BEFORE reading
  # the verdict, so the issue was consumed permanently and could never be re-attempted — and the
  # §7.1 reaper walks dispatched.txt only, so nothing ever removed that entry. (agent-infra#1716)
  RV="$TMP/verdict"; mkdir -p "$RV/fakesc/.venv/bin"
  printf '10\n' > "$RV/.pool"
  : > "$RV/checked.txt"; : > "$RV/candidates.txt"; : > "$RV/dispatched.txt"
  cat > "$RV/fakesc/.venv/bin/python" <<'PY'
#!/bin/bash
# stand-in for a pre-flight that ERRORED: no VERDICT line, non-zero exit
echo "boom: could not reach the graph" >&2
exit 1
PY
  chmod +x "$RV/fakesc/.venv/bin/python"
  O="$RV" LIMIT=5 SC="$RV/fakesc" bash "$TMP/refreshloop.sh" > "$TMP/verdict.out" 2>&1
  case "$(tr '\n' ' ' < "$RV/checked.txt")" in *10*) rc=1;; *) rc=0;; esac
  ck "shipped refresh loop: an ERRORED pre-flight does NOT blacklist the issue" $rc
  case "$(tr '\n' ' ' < "$RV/candidates.txt")" in *10*) rc=1;; *) rc=0;; esac
  ck "shipped refresh loop: an ERRORED pre-flight queues nothing" $rc

  # ── 5d) a COLLISION is transient and must be re-attempted, not recorded forever ──────────────
  # The stub answers COLLISION on the first pass (a peer owns the issue) and CLEAN on the second
  # (the colliding work has landed). The pre-fix loop recorded checked.txt before the verdict, so
  # the issue was suppressed forever and the second pass never ran the pre-flight at all.
  RC="$TMP/collide"; mkdir -p "$RC/fakesc/.venv/bin"
  printf '10\n' > "$RC/.pool"
  : > "$RC/checked.txt"; : > "$RC/candidates.txt"; : > "$RC/dispatched.txt"
  cat > "$RC/fakesc/.venv/bin/python" <<'PY'
#!/bin/bash
# first pass: a peer's work is in flight; after it lands: CLEAN
S="$(dirname "$0")/.pass"
if [ -f "$S" ]; then echo "VERDICT: CLEAN"; else : > "$S"; echo "VERDICT: COLLISION"; fi
PY
  chmod +x "$RC/fakesc/.venv/bin/python"
  O="$RC" LIMIT=5 SC="$RC/fakesc" bash "$TMP/refreshloop.sh" > "$TMP/collide-1.out" 2>&1
  case "$(tr '\n' ' ' < "$RC/checked.txt")" in *10*) rc=1;; *) rc=0;; esac
  ck "shipped refresh loop: a COLLISION does not permanently blacklist the issue" $rc
  case "$(tr '\n' ' ' < "$RC/candidates.txt")" in *10*) rc=1;; *) rc=0;; esac
  ck "shipped refresh loop: a COLLISION is not queued" $rc
  O="$RC" LIMIT=5 SC="$RC/fakesc" bash "$TMP/refreshloop.sh" > "$TMP/collide-2.out" 2>&1
  case "$(tr '\n' ' ' < "$RC/candidates.txt")" in *10*) rc=0;; *) rc=1;; esac
  ck "shipped refresh loop: the COLLISION is re-attempted and queued once it clears" $rc

  # ── 5f) LANDED is suppressed, NOT blacklisted: landed.txt, reported on every pass ──────────
  # #8560 added a fourth verdict, LANDED (exit 4): the issue's work already shipped. Two properties
  # must hold, and the first fix in this PR delivered only the second one (#8560 review):
  #   (i)  nothing is queued for it, and it does not consume a LIMIT slot (a dispatch onto shipped
  #        code, plus a capacity leak);
  #   (ii) it is NOT recorded in checked.txt. This loop's pool is built from `--state open`, so the
  #        pre-flight's state-CLOSED arm is unreachable here: every LANDED it sees comes from the
  #        TIMELINE arm on an OPEN issue — possibly a multi-PR issue that still needs work (the
  #        gate's own #4886 caveat). checked.txt is only pruned for a reaped DISPATCH, and a landed
  #        issue is never dispatched, so writing it there removed an open work item from the backlog
  #        permanently and silently — the failure class the §7.1 reaper removed.
  # Pre-fix (this branch's previous head) `checked.txt` held 10 and no landed.txt existed, so
  # assertions (ii), the landed.txt write, and both "reported" checks below are RED.
  RL2="$TMP/landed"; mkdir -p "$RL2/fakesc/.venv/bin"
  printf '10\n' > "$RL2/.pool"
  : > "$RL2/checked.txt"; : > "$RL2/candidates.txt"; : > "$RL2/dispatched.txt"; : > "$RL2/landed.txt"
  cat > "$RL2/fakesc/.venv/bin/python" <<'PY'
#!/bin/bash
# stand-in for a landed pre-flight: a terminal verdict, non-zero exit in production. Each
# invocation is counted so pass 2 can prove the recorded issue is not re-pre-flighted.
echo x >> "$(dirname "$0")/.calls"
echo "VERDICT: LANDED (exit 4) — 1 authoritative terminal signal(s)"
PY
  chmod +x "$RL2/fakesc/.venv/bin/python"
  O="$RL2" LIMIT=5 SC="$RL2/fakesc" bash "$TMP/refreshloop.sh" > "$TMP/landed-1.out" 2>&1
  case "$(tr '\n' ' ' < "$RL2/landed.txt")" in *10*) rc=0;; *) rc=1;; esac
  ck "shipped refresh loop: a LANDED verdict is recorded in landed.txt (not checked.txt)" $rc
  case "$(tr '\n' ' ' < "$RL2/checked.txt")" in *10*) rc=1;; *) rc=0;; esac
  ck "shipped refresh loop: a LANDED OPEN issue is NOT blacklisted in checked.txt" $rc
  case "$(tr '\n' ' ' < "$RL2/candidates.txt")" in *10*) rc=1;; *) rc=0;; esac
  ck "shipped refresh loop: a LANDED verdict is NOT queued as a dispatch candidate" $rc
  case "$(cat "$TMP/landed-1.out")" in *"no verdict"*) rc=1;; *) rc=0;; esac
  ck "shipped refresh loop: a LANDED verdict is not logged as a missing verdict" $rc
  # the suppression is VISIBLE: the set and its remedy are named on the pass that records it
  case "$(cat "$TMP/landed-1.out")" in *"landed (awaiting verify/close"*"10"*) rc=0;; *) rc=1;; esac
  ck "shipped refresh loop: the landed set and its remedy are printed when it is recorded" $rc
  # ... and on a LATER pass, where no new verdict arrives at all. That is what makes the entry a
  # reported decision instead of an invisible permanent suppression (pre-fix: pass 2 prints nothing).
  O="$RL2" LIMIT=5 SC="$RL2/fakesc" bash "$TMP/refreshloop.sh" > "$TMP/landed-2.out" 2>&1
  case "$(cat "$TMP/landed-2.out")" in *"landed (awaiting verify/close"*"10"*) rc=0;; *) rc=1;; esac
  ck "shipped refresh loop: the landed set is STILL reported on a later pass" $rc
  [ "$(wc -l < "$RL2/fakesc/.venv/bin/.calls" | tr -d ' ')" = "1" ] && rc=0 || rc=1
  ck "shipped refresh loop: a recorded LANDED issue is not re-pre-flighted on the next pass" $rc
  # a landed entry is recorded but does not consume a slot on LATER passes: the pool is 10 (LANDED
  # once) then 20 (CLEAN), LIMIT=1. Pass 1 spends the single pre-flight slot on 10; pass 2 filters
  # the landed entry BEFORE the LIMIT counter, so 20 is pre-flighted and queued. Pre-fix (before
  # #8560's arm existed) the LANDED line fell through to `*)`, was never recorded, and so 10 was
  # re-pre-flighted on every pass — 20 could never be reached.
  RC2="$TMP/landed-cap"; mkdir -p "$RC2/fakesc/.venv/bin"
  printf '10\n20\n' > "$RC2/.pool"
  : > "$RC2/checked.txt"; : > "$RC2/candidates.txt"; : > "$RC2/dispatched.txt"; : > "$RC2/landed.txt"
  cat > "$RC2/fakesc/.venv/bin/python" <<'PY'
#!/bin/bash
case "$*" in *" 10 "*) echo "VERDICT: LANDED (exit 4)";; *) echo "VERDICT: CLEAN";; esac
PY
  chmod +x "$RC2/fakesc/.venv/bin/python"
  O="$RC2" LIMIT=1 SC="$RC2/fakesc" bash "$TMP/refreshloop.sh" > "$TMP/landed-cap-1.out" 2>&1
  case "$(tr '\n' ' ' < "$RC2/landed.txt")" in *10*) rc=0;; *) rc=1;; esac
  ck "shipped refresh loop: the LANDED entry is recorded while the slot is spent" $rc
  [ "$(tr '\n' ' ' < "$RC2/candidates.txt" | wc -w | tr -d ' ')" = "0" ] && rc=0 || rc=1
  ck "shipped refresh loop: the LIMIT-1 pass pre-flights one issue and queues none of them" $rc
  O="$RC2" LIMIT=1 SC="$RC2/fakesc" bash "$TMP/refreshloop.sh" > "$TMP/landed-cap-2.out" 2>&1
  case "$(tr '\n' ' ' < "$RC2/candidates.txt")" in *20*) rc=0;; *) rc=1;; esac
  ck "shipped refresh loop: a recorded LANDED entry frees the next pass's LIMIT slot (20 queued)" $rc

  # ── 5g) the reaper DROPS a landed entry once its issue is CLOSED ───────────────────────────
  # landed.txt must not become the same permanent list checked.txt was: an entry means "a decision
  # is owed", and once the issue is CLOSED the decision is made. Fail-closed still applies: an
  # entry whose state cannot be READ is kept, never assumed closed.
  #
  # ⛔ THE PRUNE READS `gh issue view <N> --json state` (UNCAPPED), NOT absence from `.open_iss`. The
  # pre-fix `len(open_iss) < 500` guard was a permanent no-op: `.open_iss` is `--limit 500` and this
  # repo has >500 open issues, so the guard tripped on EVERY pass and landed.txt could only be
  # cleared by hand. The saturated-listing check below is the non-vacuous one: `.open_iss` omits the
  # landed issues entirely, and the pre-fix code would keep BOTH (its cap guard fires).
  RP="$TMP/landed-prune"; mkdir -p "$RP"
  printf '10\n30\n' > "$RP/landed.txt"
  : > "$RP/.open_iss"
  : > "$RP/.pr_brs"
  : > "$RP/dispatched.txt"; : > "$RP/checked.txt"
  awk '/^python3 - "\$O" "\$OR" <<.PY.$/{f=1;next} /^PY$/{f=0} f' "$REFRESH" > "$RP/reap.py"
  if need "$RP/reap.py" "the reaper is extracted from the shipped refresh-candidates.sh"; then
    ( PATH="$R:$PATH" GH_STUB_OPEN="10" python3 "$RP/reap.py" "$RP" "acme/example" ) > "$TMP/landed-prune.out" 2>&1
    case "$(tr '\n' ' ' < "$RP/landed.txt")" in *10*) rc=0;; *) rc=1;; esac
    ck "shipped reaper: a landed entry whose issue is still OPEN is kept" $rc
    case "$(tr '\n' ' ' < "$RP/landed.txt")" in *30*) rc=1;; *) rc=0;; esac
    ck "shipped reaper: a landed entry whose issue is CLOSED is dropped" $rc
    case "$(cat "$TMP/landed-prune.out")" in *"dropped"*30*) rc=0;; *) rc=1;; esac
    ck "shipped reaper: dropping a settled landed entry is logged, not silent" $rc
    # fail-closed half: an UNREADABLE state prunes NOTHING (a failed read is not "closed")
    RPF="$TMP/landed-prune-fail"; mkdir -p "$RPF"
    printf '10\n30\n' > "$RPF/landed.txt"
    : > "$RPF/.open_iss"; : > "$RPF/.pr_brs"
    : > "$RPF/dispatched.txt"; : > "$RPF/checked.txt"
    ( PATH="$R:$PATH" GH_STUB_FAIL=1 python3 "$RP/reap.py" "$RPF" "acme/example" ) > "$TMP/landed-prune-fail.out" 2>&1
    case "$(tr '\n' ' ' < "$RPF/landed.txt")" in *10*30*) rc=0;; *) rc=1;; esac
    ck "shipped reaper: an UNREADABLE landed state prunes no entry (both kept)" $rc
    case "$(cat "$TMP/landed-prune-fail.out")" in *"could not be re-read"*) rc=0;; *) rc=1;; esac
    ck "shipped reaper: keeping an unreadable landed entry is logged, not silent" $rc
    # ⛔ NON-VACUOUS against the pre-fix cap guard: `.open_iss` is SATURATED at 500 entries and
    # omits 10, while gh reports 10 OPEN. Absence from a capped listing proves nothing, so 10 must
    # be KEPT; and 30 (gh: CLOSED) must still be dropped. Pre-fix, the `len(open_iss) < 500` guard
    # fired and neither entry was pruned.
    RPT="$TMP/landed-prune-saturated"; mkdir -p "$RPT"
    printf '10\n30\n' > "$RPT/landed.txt"
    seq 1 500 | grep -v '^10$' | grep -v '^30$' > "$RPT/.open_iss"   # 500 entries: the cap is hit
    : > "$RPT/.pr_brs"
    : > "$RPT/dispatched.txt"; : > "$RPT/checked.txt"
    ( PATH="$R:$PATH" GH_STUB_OPEN="10" python3 "$RP/reap.py" "$RPT" "acme/example" ) > "$TMP/landed-prune-sat.out" 2>&1
    case "$(tr '\n' ' ' < "$RPT/landed.txt")" in *10*) rc=0;; *) rc=1;; esac
    ck "shipped reaper: a saturated .open_iss does NOT make an OPEN landed entry look closed" $rc
    case "$(tr '\n' ' ' < "$RPT/landed.txt")" in *30*) rc=1;; *) rc=0;; esac
    ck "shipped reaper: the landed prune settles a CLOSED entry even when .open_iss is saturated" $rc
  fi
fi

# ── 5e) the capture site itself: a failed `gh` leaves ABSENCE, not an empty file ──────────────
# The reaper guard above is only sound if the capture never hands it an empty file for a FAILED
# read. Extract the shipped capture block and drive it with a stub `gh`: on failure NOTHING must be
# left behind (`rm -f`), and on success the (empty) file must be present — that difference is what
# lets the reaper tell "the read failed" from "the answer is none". The pre-fix `|| : > "$O/.pr_brs"`
# wrote an empty file in BOTH cases, which is the fail-open being fixed here. (agent-infra#1705)
awk '/^if ! gh issue list --repo "\$OR"/{f=1} f{print} f&&/^fi$/{c++; if(c==2) exit}' "$REFRESH" > "$TMP/acq.sh"
if need "$TMP/acq.sh" "the listing capture is extracted from the shipped refresh-candidates.sh"; then
  A="$TMP/acq"; mkdir -p "$A/fail" "$A/ok"
  cat > "$A/gh" <<'SH'
#!/bin/bash
case "${GH_MODE:-}" in
  fail) echo 'gh: could not reach api.github.com' >&2; exit 1 ;;
  ok)   exit 0 ;;
esac
SH
  chmod +x "$A/gh"
  ( PATH="$A:$PATH" GH_MODE=fail O="$A/fail" OR="acme/widget" bash "$TMP/acq.sh" ) > "$TMP/acq-fail.out" 2>&1
  [ ! -e "$A/fail/.pr_brs" ] && rc=0 || rc=1
  ck "shipped capture: a FAILED PR listing leaves NO .pr_brs (absence, not an empty answer)" $rc
  [ ! -e "$A/fail/.open_iss" ] && rc=0 || rc=1
  ck "shipped capture: a FAILED issue listing leaves NO .open_iss" $rc
  ( PATH="$A:$PATH" GH_MODE=ok O="$A/ok" OR="acme/widget" bash "$TMP/acq.sh" ) > "$TMP/acq-ok.out" 2>&1
  [ -e "$A/ok/.pr_brs" ] && [ ! -s "$A/ok/.pr_brs" ] && rc=0 || rc=1
  ck "shipped capture: a SUCCESSFUL empty PR listing leaves an EMPTY .pr_brs (a real answer)" $rc
fi

# ── 6) install.sh renders correctly, including values sed would mangle ───────────
# Runs the real installer with a hostile $O containing '&' (in sed, & expands to the matched
# text, so `O=/tmp/a&b` used to render the path as /tmp/a__O__b — a VALID plist pointing at
# the wrong paths, which plutil -lint cannot catch).
SB="$TMP/sb"; mkdir -p "$SB/Library/LaunchAgents" "$SB/.config" "$SB/.pi/agent/scripts"
ln -s "$HOME/.config/gh" "$SB/.config/gh" 2>/dev/null || true
[ -f "$HOME/.pi/agent/scripts/record-review.sh" ] && ln -s "$HOME/.pi/agent/scripts/record-review.sh" "$SB/.pi/agent/scripts/record-review.sh"
# install.sh prefers the version-independent pi-node symlink under $HOME, and the daemon probe now
# EXECUTES pi — so a sandbox HOME must expose a runnable pi, exactly as a real install's HOME does.
mkdir -p "$SB/.local/share/pi-node/current/bin"
printf '#!/bin/bash\nexit 0\n' > "$SB/.local/share/pi-node/current/bin/pi"; chmod +x "$SB/.local/share/pi-node/current/bin/pi"
HOSTILE="$TMP/x&y"
HOME="$SB" O="$HOSTILE" REPO="$HERE/../.." OR="acme/widget" bash "$INST" >"$TMP/inst.out" 2>&1
PL="$SB/Library/LaunchAgents/com.eldato.orchestrator-loop.plist"
[ -f "$PL" ] && rc=0 || { rc=1; echo "      install.sh did not render a plist: $(tail -3 "$TMP/inst.out")"; }
ck "install.sh renders a plist" $rc
if [ -f "$PL" ]; then
  grep -q '__[A-Z_]*__' "$PL" && rc=1 || rc=0
  ck "no unsubstituted placeholder survives rendering" $rc
  # The value must appear XML-escaped (a bare & is illegal XML); correctness is then proven by
  # the round-trip check below, which decodes it back and compares.
  grep -qF 'x&amp;y' "$PL" && rc=0 || { rc=1; echo "      expected XML-escaped '&' in the plist"; }
  ck "a '&' in a path is XML-escaped in the plist" $rc
  plutil -lint "$PL" >/dev/null 2>&1 && rc=0 || rc=1
  ck "the rendered plist is valid XML" $rc
  # Round-trip: XML-escaping must decode back to the original path, not merely be well-formed.
  decoded="$(plutil -extract EnvironmentVariables.O raw "$PL" 2>/dev/null || echo MISSING)"
  [ "$decoded" = "$HOSTILE" ] && rc=0 || { rc=1; echo "      decoded '$decoded' != '$HOSTILE'"; }
  ck "the hostile path round-trips through the plist unchanged" $rc
fi

# ── 6b) install.sh on a fresh checkout: dir-only gitignore, .worktrees not created yet ─
# install.sh is the FIRST step on a fresh box (README §4) and dispatch-merge.sh creates the
# worktree lazily — but `git check-ignore -q .worktrees` returns non-zero when the path does not
# exist and the repo ignores it with the directory-only pattern `.worktrees/`. The installer then
# died before rendering the plist. Reproduced in a throwaway git repo whose parent is not a repo.
FR="$TMP/freshrepo"; mkdir -p "$FR/repo"
git -C "$FR/repo" init -q >/dev/null 2>&1
printf '.worktrees/\n' > "$FR/repo/.gitignore"
# Non-vacuous: this exact repo really does defeat the pre-fix bare-path form.
( cd "$FR/repo" && git check-ignore -q .worktrees ) && rc=1 || rc=0
ck "non-vacuity: the pre-fix bare .worktrees guard fails on a fresh checkout" $rc
grep -q 'check-ignore -q .worktrees/' "$INST" && rc=0 || rc=1
ck "install.sh's gitignore guard uses the directory form .worktrees/" $rc
SB2="$TMP/sb2"; mkdir -p "$SB2/Library/LaunchAgents" "$SB2/.config" "$SB2/.pi/agent/scripts"
ln -s "$HOME/.config/gh" "$SB2/.config/gh" 2>/dev/null || true
mkdir -p "$SB2/.local/share/pi-node/current/bin"
printf '#!/bin/bash\nexit 0\n' > "$SB2/.local/share/pi-node/current/bin/pi"; chmod +x "$SB2/.local/share/pi-node/current/bin/pi"
HOME="$SB2" O="$TMP/fresh-o" REPO="$FR/repo" OR="acme/widget" bash "$INST" > "$TMP/inst-fresh.out" 2>&1
PL2="$SB2/Library/LaunchAgents/com.eldato.orchestrator-loop.plist"
[ -f "$PL2" ] && rc=0 || { rc=1; echo "      fresh checkout: $(tail -2 "$TMP/inst-fresh.out")"; }
ck "install.sh renders a plist on a fresh checkout (no .worktrees dir yet)" $rc

# ── 6c) the self-test resolves REPO to the checkout, not its parent ─────────────
# It passed `$HERE/../../..` (the PARENT of the repo root) to install.sh, which died
# "not a git checkout" on any fresh clone whose parent is not a git repo — the exact second box
# this harness exists to hand over. `$HERE/../..` is the root (check #13 already uses it).
grep -qE 'REPO="\$HERE/\.\./\.\./\.\."' "$HERE/selftest.sh" && rc=1 || rc=0
ck "selftest.sh does not point install.sh at the repo's parent" $rc
grep -qF 'REPO="$HERE/../.."' "$HERE/selftest.sh" && rc=0 || rc=1
ck "selftest.sh passes the repo root to install.sh" $rc
[ -e "$HERE/../../.git" ] && rc=0 || rc=1
ck "the repo-root path selftest.sh uses resolves to a git working tree" $rc

# ── 6d) the rendered plist PATH IS the resolved daemon PATH (the integration seam) ─────────────
# §17/§18 drive the extracted PATH-builder and probe, but nothing pinned that install.sh actually
# RENDERS $DAEMON_PATH into the plist. Review mutation (H) restored the pre-fix hardcoded base at the
# __PATH__ substitution and left the whole suite green — yet the rendered PATH is the fixed artifact.
# Render in a sandbox with a CONTROLLED PATH whose tools live in one fixture dir: the rendered PATH
# must contain it (so the check is red under the hardcoded base) and no shim/.pi segment. `uv` is
# deliberately absent, which also exercises the documented non-fatal-uv path end to end.
FB="$TMP/pathbin"; mkdir -p "$FB" "$TMP/sb3/Library/LaunchAgents" "$TMP/sb3/.config" "$TMP/sb3/.pi/agent/scripts"
for b in tmux gh pi; do printf '#!/bin/bash\nexit 0\n' > "$FB/$b"; chmod +x "$FB/$b"; done
ln -s "$HOME/.config/gh" "$TMP/sb3/.config/gh" 2>/dev/null || true
HOME="$TMP/sb3" O="$TMP/path-o" REPO="$HERE/../.." OR="acme/widget" PATH="$FB:/usr/bin:/bin" bash "$INST" > "$TMP/inst-path.out" 2>&1
PL3="$TMP/sb3/Library/LaunchAgents/com.eldato.orchestrator-loop.plist"
[ -f "$PL3" ] && rc=0 || { rc=1; echo "      controlled-PATH install did not render: $(tail -3 "$TMP/inst-path.out")"; }
ck "install.sh renders with a controlled PATH (uv absent stays non-fatal)" $rc
PLP="$(plutil -extract EnvironmentVariables.PATH raw "$PL3" 2>/dev/null || echo MISSING)"
case ":$PLP:" in *":$FB:"*) rc=0;; *) rc=1; echo "      rendered PATH=$PLP lacks the resolved tool dir $FB";; esac
ck "the rendered plist PATH is resolved, not the pre-fix hardcoded base" $rc
case ":$PLP:" in *"/shims"*|*"/.pi/"*) rc=1;; *) rc=0;; esac
ck "the rendered plist PATH contains no */shims or */.pi/* segment" $rc

# ── 7) in-script defaults agree with install.sh's defaults ───────────────────────
# A limit documented in README §4 and rendered by install.sh must not be silently different in
# orch-loop.sh — MAX_REVIEW was 2 in the loop and 4 everywhere else.
rc=0
# Assert the EFFECTIVE default, not the source text.
#
# Pattern-matching the source was a losing game: four review rounds each closed one spelling of the
# same hole and left another -- a literal, an arithmetic default, a duplicate definition, a duplicate
# with no trailing newline, then an indented/bare/conditional reassignment. Each fix taught the regex
# one more trick while bash kept resolving assignments by its own rules. Executing the assignments and
# comparing what bash RESOLVES tests the property we actually care about (that the two files agree)
# rather than a spelling of it.
#
# KNOWN LIMIT, stated so this is not oversold: the regex only matches an assignment that BEGINS a line
# (optionally after `export`). A `readonly VAR=`, an assignment on the same line as an `if`, or one
# inside a function body written on one line are therefore NOT evaluated, and such a form would evade
# this check. Those spellings do not occur in either file and neither is a natural way to write a
# limit, but a check cannot be stronger than what it evaluates, so the guarantee is "the assignments
# either file actually uses agree", not "any conceivable bash file agrees".
#
# Only lines that assign a KNOWN limit var (or _NCPU, below) are evaluated -- never the whole script.
_LIMITS="MAX_LOAD MAX_LANES MAX_REVIEW MERGE_PRIORITY_AT P0_BYPASS_MERGE_PRIORITY MAX_FIX MAX_FIX_TRIES MIN_SWAP_FREE MIN_MEM_FREE_MB BATCH CYC"
# _NCPU is evaluated but not printed: MAX_LOAD is derived from it, so pinning it here (as an earlier
# version did) would hide a divergence in the one input the derived ceiling depends on.
_EVAL_VARS="$_LIMITS _NCPU"
_LIMIT_RE="^[[:space:]]*(export[[:space:]]+)?($(printf '%s|' $_EVAL_VARS | sed 's/|$//'))="
eff_defaults() {
  ( if [ "$1" = env ]; then
      # Second pass: the limits are SET. This distinguishes `VAR="${VAR:-4}"` (honours the
      # override) from a bare `VAR=4` (ignores it) -- two forms that resolve identically when the
      # variable is unset, so a single unset pass cannot tell them apart. They diverge exactly where
      # it matters: the launchd plist supplies these as environment, and it OVERRIDES the script.
      for v in $_LIMITS; do export "$v=997"; done
    else
      for v in $_LIMITS; do unset "$v"; done
    fi
    unset _NCPU
    eval "$(grep -E "$_LIMIT_RE" "$2" 2>/dev/null)"
    for v in $_LIMITS; do printf '%s=%s\n' "$v" "$(eval "printf '%s' \"\${$v:-UNSET}\"")"; done )
}
# The loop's effective limits are the ones IT resolves — and since agent-infra#1720 that includes
# the limits declared in the helper it sources (MAX_LOAD lives there, because the workers read it
# too). Concatenate in SOURCING ORDER (helper first) so a later assignment in orch-loop.sh still
# wins, exactly as bash would resolve it at runtime. §7 therefore compares the gate's ceiling
# against the ceiling install.sh renders into the plist — the two must not diverge, or the daemon
# would advertise one MAX_LOAD while the gate applied another.
cat "$GATE" "$LOOP" > "$TMP/loop-limits.sh"
_da="$(eff_defaults unset "$TMP/loop-limits.sh"; eff_defaults env "$TMP/loop-limits.sh")"
_db="$(eff_defaults unset "$INST"; eff_defaults env "$INST")"
if [ -n "$_da" ] && [ "$_da" = "$_db" ]; then
  echo "      effective: $(printf '%s' "$_da" | tr '\n' ' ')"
else
  echo "      effective defaults DIFFER:"
  printf '%s\n' "$_da" > "$TMP/da.txt"; printf '%s\n' "$_db" > "$TMP/db.txt"
  diff "$TMP/da.txt" "$TMP/db.txt" 2>/dev/null | sed 's/^/        /'
  rc=1
fi
ck "orch-loop.sh (+ the helper it sources) and install.sh resolve the same effective defaults for all 11 limits" $rc

# ── 8) the dispatch gate consults load and names the binding gate ────────────────
# MAX_LOAD was rendered into the plist and documented, but never compared to anything.
grep -qE '\[ "\$L" -ge "\$MAX_LOAD" \]' "$LOOP" && rc=0 || rc=1
ck "dispatch gate compares load against MAX_LOAD" $rc
grep -q 'GATE_WHY=' "$LOOP" && rc=0 || rc=1
ck "dispatch skip message names the binding gate" $rc
grep -q 'merge-priority' "$LOOP" && rc=0 || rc=1
ck "the loop implements merge-priority above the backlog threshold" $rc
grep -q '= "CONFLICTING"' "$LOOP" && rc=0 || rc=1
ck "a CONFLICTING PR is routed to the fixer, not retried as a merge" $rc
grep -q 'CONFLICTING -> fixer attempt' "$LOOP" && rc=0 || rc=1
ck "the conflict fixer is logged and capped like other fix loops" $rc
grep -q 'resolving the conflict IS the task' "$HERE/fix-one.sh" && rc=0 || rc=1
ck "the fixer prompt treats a conflict as the task" $rc
# Drive the shipped rule rather than grepping for a comment: a regression that dropped the
# 'lanes >= 1' exception or the headroom arithmetic must fail here.
sed -n '/^mp_reason() {/,/^}/p;/^review_slots() {/,/^}/p;/^p0_merge_priority_override() {/,/^}/p' "$LOOP" > "$TMP/mp.sh"
if need "$TMP/mp.sh" "mp_reason()/review_slots()/p0_merge_priority_override() are extracted from the shipped loop"; then
  MERGE_PRIORITY_AT=10; MAX_REVIEW=4
  # shellcheck disable=SC1090
  . "$TMP/mp.sh"
  [ -n "$(mp_reason 11 1)" ] && rc=0 || rc=1
  ck "shipped mp_reason(): 11 open PRs with a lane running -> drain" $rc
  [ -z "$(mp_reason 11 0)" ] && rc=0 || rc=1
  ck "shipped mp_reason(): 11 open PRs with NO lane -> dispatch still allowed" $rc
  [ -z "$(mp_reason 10 3)" ] && rc=0 || rc=1
  ck "shipped mp_reason(): at the threshold, no drain override" $rc
  [ "$(review_slots 11)" = "6" ] && rc=0 || rc=1
  ck "shipped review_slots(): backlog raises reviewer headroom to MAX_REVIEW+2" $rc
  [ "$(review_slots 10)" = "4" ] && rc=0 || rc=1
  ck "shipped review_slots(): below the threshold, headroom is MAX_REVIEW" $rc
  # BP0 (#8468): DRIVE the shipped override, not a grep for its text. Deleting the block, or
  # changing its `-F'\t'` key, must fail here — a comment grep stayed green through both mutations.
  _po="$TMP/p0"; mkdir -p "$_po"
  O="$_po"; LOG="$TMP/p0.log"; : > "$LOG"
  _why="merge-priority (19 open PRs)"
  printf '7951\n' > "$_po/candidates.txt"; printf '7951\t0\n' > "$_po/.prio"
  [ -z "$(p0_merge_priority_override "$_why" 19)" ] && rc=0 || rc=1
  ck "shipped p0 override: a tier-0 backlog head clears the merge-priority gate" $rc
  grep -q 'OVERRIDDEN for a P0-blocker' "$LOG" && rc=0 || rc=1
  ck "shipped p0 override: the override is logged" $rc
  printf '8039\t1\n' > "$_po/.prio"
  [ "$(p0_merge_priority_override "$_why" 19)" = "$_why" ] && rc=0 || rc=1
  ck "shipped p0 override: a tier-1 head leaves the gate closed" $rc
  printf '7951\t0\n' > "$_po/.prio"
  [ "$(P0_BYPASS_MERGE_PRIORITY=0 p0_merge_priority_override "$_why" 19)" = "$_why" ] && rc=0 || rc=1
  ck "shipped p0 override: P0_BYPASS_MERGE_PRIORITY=0 restores the gate" $rc
  rm -f "$_po/.prio"
  [ "$(p0_merge_priority_override "$_why" 19)" = "$_why" ] && rc=0 || rc=1
  ck "shipped p0 override: a missing .prio suppresses the override (fail-safe)" $rc
fi
grep -qE 'case "\$\{PR_ST:-\}" in MERGED\|CLOSED\)' "$LOOP" && rc=0 || rc=1
ck "sweep skips CLOSED as well as MERGED" $rc

# ── 9) crash-safety bookkeeping ──────────────────────────────────────────────────
grep -q 'if tmux new-session -d -s "fix-\$PR"' "$LOOP" && rc=0 || rc=1
ck "fixer spawn is attempted before the attempt is recorded" $rc
grep -q 'PARKED after' "$LOOP" && rc=0 || rc=1
ck "reaching MAX_FIX_TRIES parks the PR instead of re-reviewing forever" $rc
grep -q 'claim_live "\$PR"' "$LOOP" && rc=0 || rc=1
ck "controller honours the claim written by the chain path" $rc

# ── 10) template / renderer placeholder parity ───────────────────────────────────
tpl="$(grep -oE '__[A-Z_]+__' "$HERE/launchd.plist.template" | sort -u)"
sedlist="$(sed -n '/^sed \\/,/launchd.plist.template/p' "$INST" | grep -oE '__[A-Z_]+__' | sed 's/^__//; s/__$//' | sed 's/^/__/; s/$/__/' | sort -u)"
[ -n "$tpl" ] && [ "$tpl" = "$sedlist" ] && rc=0 || rc=1
ck "every template placeholder is substituted by install.sh" $rc

# The placeholder parity above cannot see a renamed <key>: rename the env KEY, keep the placeholder,
# and both checks stay green while the loop reads a variable launchd never sets -- so the plist's
# environment override is silently ignored. That is the exact mechanism the `env` pass in §7 models,
# so the key names deserve their own assertion.
rck=0
for v in $_LIMITS; do
  grep -q "<key>$v</key>" "$HERE/launchd.plist.template" || { echo "      template has no <key>$v</key>"; rck=1; }
done
ck "every limit the loop reads is rendered as a plist env KEY" $rck

# ── 11) no credential material shipped ───────────────────────────────────────────
rc=0
if grep -rqE 'github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY' "$HERE" 2>/dev/null; then rc=1; fi
ck "no credential-shaped strings in the shipped tree" $rc

# ── 12) README does not claim more than the code does ────────────────────────────
# Guards the three overclaims found in review. Match the overclaim PHRASINGS, not the words —
# §9 legitimately needs to say "not byte-identical" and §7.4 legitimately quotes the old claim.
grep -q 'so behaviour is byte-identical' "$HERE/README.md" && rc=1 || rc=0
ck "README does not claim the shipped copy is byte-identical" $rc
grep -qE 'selftest.sh. now pins every|pins every fixed defect' "$HERE/README.md" && rc=1 || rc=0
ck "README does not claim the self-test pins every defect" $rc
grep -q 'One owner per PR, per box' "$HERE/README.md" && rc=0 || rc=1
ck "README scopes the claim invariant to one box" $rc
grep -q 'serialises one box only' "$HERE/README.md" && rc=0 || rc=1
ck "README states the cross-box limitation explicitly" $rc
# The README must not state a self-test check COUNT — it drifts the moment a check is added
# (it said 29 while the file had 42, then 46/46 while the file ran 57). The guard matches the
# PHRASINGS that actually drifted and is proved non-vacuous against a fixture of pre-fix text.
OVERC='[0-9]+ checks|[0-9]+ of its checks|measured [0-9]+/[0-9]+|[0-9]+/[0-9]+ shipped'
grep -qE "$OVERC" "$HERE/README.md" && rc=1 || rc=0
ck "README states no self-test check count that can drift" $rc
cat > "$TMP/overclaim.md" <<'MD'
review of this very file, and 19 of its checks fail when it is dropped into the
| measured 7/30 on the pre-fix tree vs 46/46 shipped |
MD
grep -qE "$OVERC" "$TMP/overclaim.md" && rc=0 || rc=1
ck "the drift guard matches the pre-fix phrasings (non-vacuous)" $rc
# The REQUIRED-check count is the page's own invariant, not a drifting number: it is fixed by the
# repo's protection. It must equal REQ and the README must never spell a different count beside
# the list it ships (it said "eight"/"8" against a live set of 7).
nreq="$(sed -n 's/^REQ="\(.*\)"$/\1/p' "$LOOP" | tr ' ' '\n' | grep -c .)"
case "$nreq" in 7) rword=seven;; 8) rword=eight;; *) rword="" ;; esac
[ -n "$rword" ] && grep -qE "($nreq|$rword) required check" "$HERE/README.md" && rc=0 || rc=1
ck "README states the required-check count REQ has ($nreq)" $rc
numerals='([0-9]+|one|two|three|four|five|six|seven|eight|nine|ten) required check'
bad=""
while read -r tok _; do
  [ -z "$tok" ] && continue
  case "$tok" in "$nreq"|"$rword") ;; *) bad="$tok";; esac
done < <(grep -oE "$numerals" "$HERE/README.md")
[ -z "$bad" ] && rc=0 || { rc=1; echo "      README claims '$bad required check'"; }
ck "README never disagrees with REQ on the required-check count" $rc
bad=""
while read -r tok _; do
  [ -z "$tok" ] && continue
  case "$tok" in "$nreq"|"$rword") ;; *) bad="$tok";; esac
done < <(printf 'read the 8 required checks\nand the eight required check names\n' | grep -oE "$numerals")
[ -n "$bad" ] && rc=0 || rc=1
ck "non-vacuity: the required-count guard flags the pre-fix '8'/'eight' (saw '$bad')" $rc
grep -q 'selftest.sh' "$HERE/README.md" && rc=0 || rc=1
ck "README points at the self-test" $rc

# ── 13) a hostile branch name cannot reach a shell ───────────────────────────────
# git PERMITS ' ; $ ` & | ( ) { } " < > # in a ref name — only spaces are forbidden, and ${IFS}
# supplies those. The controller embedded headRefName inside a tmux `sh -c` string, so a fork PR on
# branch `fix/x';touch${IFS}/tmp/VULN;#` got arbitrary command execution on the host that holds the
# merge key. This was reproduced end-to-end before the fix.
sed -n '/^safe_ref() {/,/^}/p' "$LOOP" > "$TMP/saferef.sh"
if need "$TMP/saferef.sh" "safe_ref() is extracted from the shipped orch-loop.sh"; then
  REPO="$HERE/../.."
  # shellcheck disable=SC1090
  . "$TMP/saferef.sh"
  HOSTILE="fix/x';touch\${IFS}/tmp/VULN;#"
  safe_ref "$HOSTILE" && rc=1 || rc=0
  ck "shipped safe_ref() rejects a hostile branch name" $rc
  safe_ref "fix/8246-orchestrator-harness-handover" && rc=0 || rc=1
  ck "shipped safe_ref() accepts a normal branch name" $rc
  safe_ref "feat/1234-tm" && rc=0 || rc=1
  ck "shipped safe_ref() accepts a generated branch name" $rc
fi
is_num() { case "${1:-}" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
grep -q '^is_num() {' "$LOOP" && rc=0 || rc=1
ck "the shipped loop defines is_num() for PR ids" $rc
grep -q -- "--argv" "$LOOP" && rc=0 || rc=1
ck "worker launches pass args via --argv, not string interpolation" $rc
grep -qE "'\\\$OR' \\\$PR '\\\$BR'" "$LOOP" && rc=1 || rc=0
ck "no \$OR/\$PR/\$BR interpolation remains in the spawn commands" $rc
# The retired tree ships next to the live scripts and is executable, so an unsafe interpolation
# there is a re-armable P0 for anyone who reuses it. It must be annotated as such.
grep -q 'RETIRED: unsafe interpolation' "$HERE/retired/auto-loop.sh" && rc=0 || rc=1
ck "the retired copy's unsafe interpolation is annotated in place" $rc
grep -q 'Prefer the live scripts' "$HERE/retired/auto-loop.sh" && rc=0 || rc=1
ck "the retired copy's banner warns against reuse" $rc

# ── 14) the controller does not treat a stale claim as in-flight ────────────────
sed -n '/^claim_live() {/,/^}/p' "$LOOP" > "$TMP/claimlive.sh"
if need "$TMP/claimlive.sh" "claim_live() is extracted from the shipped orch-loop.sh"; then
  mkdir -p "$TMP/CL/state"
  O="$TMP/CL"
  # shellcheck disable=SC1090
  . "$TMP/claimlive.sh"
  printf '%s' "$$" > "$O/state/mergeclaim-1.lock"
  claim_live 1 && rc=0 || rc=1
  ck "shipped claim_live(): a live owner counts as in flight" $rc
  printf '%s' "999999" > "$O/state/mergeclaim-2.lock"
  claim_live 2 && rc=1 || rc=0
  ck "shipped claim_live(): a dead owner is not in flight" $rc
  [ -f "$O/state/mergeclaim-2.lock" ] && rc=1 || rc=0
  ck "shipped claim_live(): the stale claim file is removed" $rc
fi

grep -qE "'docs\|test-isolation\|license-surface\|legal-e2e\|pricing-artifact'" "$LOOP" && rc=0 || rc=1
ck "a non-review required check failure is routed to the fixer" $rc
grep -q 'CI-red (.*fixer attempt' "$LOOP" && rc=0 || rc=1
ck "the CI-red fixer is logged and capped like other fix loops" $rc
grep -q 'FAILING_CHECKS=' "$HERE/fix-one.sh" && rc=0 || rc=1
ck "the fixer is given the failing check" $rc
grep -q 'failing.txt' "$HERE/fix-one.sh" && rc=0 || rc=1
ck "the fixer is given the failing CI log" $rc
# ── 15) a hung worker cannot hold a concurrency slot forever ────────────────────
# `pi -p` has no internal timeout. A hung reviewer occupied a merge slot for 9 hours on
# 2026-10-12 while the caps (which count SESSIONS) reported the slot as busy.
grep -q '^  reap_stale_workers$' "$LOOP" && rc=0 || rc=1
ck "the reaper runs every cycle" $rc
sed -n '/^reap_stale_workers() {/,/^}/p' "$LOOP" > "$TMP/reapw-body.sh"
# The reaper now delegates the mtime probe to the shipped portable helper (#1702), so its unit is
# the helper PLUS the reaper. Extracting the reaper alone leaves file_mtime_epoch undefined and the
# reaper silently skips every session (`command not found` -> the `|| continue` guard) — which is
# exactly what this check caught when the helper was introduced.
{ grep -m1 '^is_num() {' "$LOOP"; sed -n '/^file_mtime_epoch() {/,/^}/p' "$LOOP"; cat "$TMP/reapw-body.sh"; } > "$TMP/reapw.sh"
if need "$TMP/reapw.sh" "reap_stale_workers() and its file_mtime_epoch() helper are extracted from the shipped loop"; then
  mkdir -p "$TMP/RW/logs"
  : > "$TMP/RW/logs/merge-999.log"; touch -t 202601010000 "$TMP/RW/logs/merge-999.log"
  : > "$TMP/RW/fresh.log"
  KILLED=0
  tmux() { case "${1:-}" in ls) printf 'merge-999\n' ;; list-panes) echo 4242 ;; kill-session) KILLED=1 ;; esac; }
  kill() { :; }
  LOG="$TMP/RW/rw.log"; O="$TMP/RW"; STALE_MIN=1
  # shellcheck disable=SC1090
  . "$TMP/reapw.sh"
  reap_stale_workers
  [ "$KILLED" = "1" ] && rc=0 || rc=1
  ck "shipped reaper kills a session whose log has gone stale" $rc
  grep -q 'reap-worker' "$LOG" && rc=0 || rc=1
  ck "the reaper logs what it killed" $rc
  # Exact-match targets: a prefix match would let the reaper kill a DIFFERENT, healthy session
  # if the stale one exited between the snapshot and the kill.
  grep -q 'kill-session -t "=\$s"' "$LOOP" && rc=0 || rc=1
  ck "the reaper uses exact session targets (=name), not prefix matches" $rc
  grep -q 'list-panes -t "=\$s"' "$LOOP" && rc=0 || rc=1
  ck "the reaper resolves the pane pid with an exact target" $rc
  unset -f tmux kill
fi
# the agent invocation is wall-clock bounded, portably (macOS has no timeout/gtimeout). It lives in
# the shared helper since agent-infra#1720 (it is also the ONE gated spawn site — the gate itself is
# driven by the checks further down).
sed -n '/^run_agent() {/,/^}/p' "$GATE" > "$TMP/runagent.sh"
if need "$TMP/runagent.sh" "run_agent() is extracted from the shipped spawn-gate.sh"; then
  # NOTE: the heredoc must EXPAND $TMP (unquoted) while leaving the runtime substitutions for the
  # script itself. A quoted heredoc left the source path literal, so run_agent never got defined and
  # the check passed on "command not found" — the exact vacuity this file exists to prevent.
  # `load1` is held at 0 so a genuinely loaded box cannot make this check pass for the wrong reason:
  # it asserts the WATCHDOG, and a gate refusal would otherwise look like a kill. The real gate
  # (sourced from $GATE) still runs — only its reading is controlled.
  cat > "$TMP/ra.sh" <<SH
pi() { sleep 60; }
. "$GATE"
load1() { echo 0; }
. "$TMP/runagent.sh"
start=\$(date +%s); run_agent 2 "$TMP/ra.log" /dev/null; rc=\$?; end=\$(date +%s)
echo "\$((end-start)) \$rc"
SH
  ra="$(bash "$TMP/ra.sh" 2>/dev/null | tail -1)"
  set -- $ra
  # must have actually run (a real abort, non-zero rc), and been cut off well before the 60s sleep
  ran=0; grep -q 'wall_limit=' "$TMP/ra.log" 2>/dev/null && ran=1
  if [ "$ran" = "1" ] && [ "${1:-0}" -ge 1 ] && [ "${1:-99}" -lt 30 ] && [ "${2:-0}" -ne 0 ]; then rc=0; else rc=1; fi
  ck "shipped run_agent() kills a hung agent at the wall limit (elapsed=${1:-?}s rc=${2:-?} ran=$ran)" $rc
fi

# ── 16) a FAILED open-PR count is never read as zero ─────────────────────────────
# The pre-fix line was OPEN_N="$(gh pr list … 2>/dev/null)"; : "${OPEN_N:=0}" — rc and stderr
# were discarded and the empty result was coerced to 0, so one transient `TLS handshake timeout`
# turned a 25-open-PR queue into `open=0 -> OWN`, dropping the BROAD drain, the reviewer headroom
# and the merge-priority gate at once. Drive the SHIPPED function (extracted, not re-implemented)
# with a stubbed gh, and pin the failure path, the sticky fallback and the success write.
unset -f gh pi uv tmux 2>/dev/null || true   # an exported function would shadow the gh stub below
sed -n '/^open_pr_count() {/,/^}/p' "$LOOP" > "$TMP/opencount.sh"
if need "$TMP/opencount.sh" "open_pr_count() is extracted from the shipped orch-loop.sh"; then
  OC="$TMP/opencount"; mkdir -p "$OC/state"
  { echo 'set -u'; grep -m1 '^is_num() {' "$LOOP"; cat "$TMP/opencount.sh"; echo 'open_pr_count'; } > "$OC/drive.sh"
  # Driver must be non-vacuous: it parses and really defines the shipped function.
  bash -n "$OC/drive.sh" && rc=0 || rc=1
  ck "the open-PR-count driver parses" $rc
  # (a) gh FAILS: TLS handshake timeout on stderr, empty stdout, rc 1
  cat > "$OC/gh" <<'SH'
#!/bin/bash
echo 'Post "https://api.github.com/graphql": net/http: TLS handshake timeout' >&2
exit 1
SH
  chmod +x "$OC/gh"; rm -f "$OC/state/last-open-n"; LOG="$OC/log"; : > "$LOG"
  n="$( PATH="$OC:$PATH" O="$OC" OR="acme/widget" LOG="$LOG" bash "$OC/drive.sh" )"
  [ "$n" != "0" ] && [ -n "$n" ] && rc=0 || { rc=1; echo "      failed gh produced '$n'"; }
  ck "a failed open-PR count is NOT zero (got '$n')" $rc
  [ "$n" = "11" ] && rc=0 || { rc=1; echo "      got '$n'"; }
  ck "a first-ever failure assumes 11 (BROAD, resumes the drain)" $rc
  grep -q 'open=UNKNOWN rc=1' "$LOG" && rc=0 || { rc=1; echo "      log: $(cat "$LOG")"; }
  ck "a failed open-PR count logs an UNKNOWN diagnostic naming the rc" $rc
  grep -q 'TLS handshake timeout' "$LOG" && rc=0 || { rc=1; echo "      log: $(cat "$LOG")"; }
  ck "the diagnostic keeps gh's stderr instead of discarding it" $rc
  # (b) sticky: a later failure reuses the last good count, never resets to 0
  printf '25\n' > "$OC/state/last-open-n"; : > "$LOG"
  n="$( PATH="$OC:$PATH" O="$OC" OR="acme/widget" LOG="$LOG" bash "$OC/drive.sh" )"
  [ "$n" = "25" ] && rc=0 || { rc=1; echo "      sticky produced '$n'"; }
  ck "a later failure reuses the sticky last-known count (25)" $rc
  # (b2) a sticky 0 must NOT be reused: a DRAINED queue is the dangerous case (PRs arrive, then one
  # transient failure) and 0 drives OWN / no headroom / no merge-priority — the exact pre-fix bug.
  printf '0\n' > "$OC/state/last-open-n"; : > "$LOG"
  n="$( PATH="$OC:$PATH" O="$OC" OR="acme/widget" LOG="$LOG" bash "$OC/drive.sh" )"
  [ "$n" != "0" ] && [ -n "$n" ] && rc=0 || { rc=1; echo "      sticky-0 produced '$n'"; }
  ck "a sticky last-known of 0 is not reused (got '$n')" $rc
  [ "$n" = "11" ] && rc=0 || { rc=1; echo "      got '$n'"; }
  ck "a sticky 0 falls back to 11 (BROAD)" $rc
  # (b3) the fallback must clear the SCOPE threshold too, not just the merge-priority tunable:
  # max(MERGE_PRIORITY_AT, 10) + 1. Keying it to the tunable alone left a below-default tunable in
  # OWN — the unsafe direction — on a failed count.
  _mp="$( PATH="$OC:$PATH" O="$OC" OR="acme/widget" LOG="$LOG" MERGE_PRIORITY_AT=4 bash "$OC/drive.sh" )"
  [ "$_mp" = "11" ] && rc=0 || { rc=1; echo "      MERGE_PRIORITY_AT=4 produced '$_mp' (expected 11)"; }
  ck "a below-default MERGE_PRIORITY_AT (4) falls back to 11, above the scope threshold" $rc
  _mp="$( PATH="$OC:$PATH" O="$OC" OR="acme/widget" LOG="$LOG" MERGE_PRIORITY_AT=15 bash "$OC/drive.sh" )"
  [ "$_mp" = "16" ] && rc=0 || { rc=1; echo "      MERGE_PRIORITY_AT=15 produced '$_mp' (expected 16)"; }
  ck "an above-default MERGE_PRIORITY_AT (15) falls back to 16, above the tunable" $rc
  # (b4) a leading-zero tunable must be read as DECIMAL: `[ … -ge 10 ]` parses base 10 but `$(( … ))`
  # parses base 0, in which `010` is octal 8 — so the guard ratified "ten" and the arithmetic
  # returned 9, landing in OWN, the same unsafe direction the tunable-keyed fallback was fixed for.
  _mp="$( PATH="$OC:$PATH" O="$OC" OR="acme/widget" LOG="$LOG" MERGE_PRIORITY_AT=010 bash "$OC/drive.sh" )"
  [ "$_mp" = "11" ] && rc=0 || { rc=1; echo "      MERGE_PRIORITY_AT=010 produced '$_mp' (expected 11)"; }
  ck "a leading-zero MERGE_PRIORITY_AT (010) is decimal 10, falls back to 11" $rc
  _mp="$( PATH="$OC:$PATH" O="$OC" OR="acme/widget" LOG="$LOG" MERGE_PRIORITY_AT=notanum bash "$OC/drive.sh" )"
  [ "$_mp" = "11" ] && rc=0 || { rc=1; echo "      MERGE_PRIORITY_AT=notanum produced '$_mp' (expected 11)"; }
  ck "a non-numeric MERGE_PRIORITY_AT falls back to 11 without aborting" $rc
  # (c) SUCCESS: the count is returned and persisted for the next failure
  cat > "$OC/gh" <<'SH'
#!/bin/bash
printf '25\n'
SH
  chmod +x "$OC/gh"; rm -f "$OC/state/last-open-n"; : > "$LOG"
  n="$( PATH="$OC:$PATH" O="$OC" OR="acme/widget" LOG="$LOG" bash "$OC/drive.sh" )"
  [ "$n" = "25" ] && rc=0 || { rc=1; echo "      success produced '$n'"; }
  ck "a successful open-PR count returns the count" $rc
  [ "$(cat "$OC/state/last-open-n" 2>/dev/null)" = "25" ] && rc=0 || rc=1
  ck "a successful open-PR count is persisted to last-open-n" $rc
  # (c2) a GENUINE 0 (drained queue) must still be returned as 0 and persisted: the `-gt 0` guard
  # lives ONLY in the failure branch. Coercing a real 0 to 11 would make BROAD permanent and the
  # queue would never be treated as drained.
  cat > "$OC/gh" <<'SH'
#!/bin/bash
printf '0\n'
SH
  chmod +x "$OC/gh"; rm -f "$OC/state/last-open-n"; : > "$LOG"
  n="$( PATH="$OC:$PATH" O="$OC" OR="acme/widget" LOG="$LOG" bash "$OC/drive.sh" )"
  [ "$n" = "0" ] && rc=0 || { rc=1; echo "      success-0 produced '$n'"; }
  ck "a genuine 0 (drained queue) is still returned as 0" $rc
  [ "$(cat "$OC/state/last-open-n" 2>/dev/null)" = "0" ] && rc=0 || rc=1
  ck "a genuine 0 is persisted to last-open-n" $rc
fi
# The SEAM, not just the logic: a perfect open_pr_count() is dead code if the loop stops calling it.
# Review mutation (G) restored the pre-fix inline line at the call site — `OPEN_N="$(gh pr list …)";
# : "${OPEN_N:=0}"` — and left the extracted-function checks green because nothing pinned the wiring.
grep -qF 'OPEN_N="$(open_pr_count)"' "$LOOP" && rc=0 || { rc=1; echo "      the loop does not assign OPEN_N from open_pr_count()"; }
ck "the loop actually calls open_pr_count() (not the pre-fix inline gh count)" $rc
# The sweep listing must be captured and rc-checked too: piped straight into `while read` with
# 2>/dev/null, a failed listing is ZERO iterations and no output — a sweep that silently does
# nothing for the whole cycle. These pin the fix's shape; (a) above pins its behavioural sibling.
grep -q 'SWEEP_RC' "$LOOP" && rc=0 || rc=1
ck "the sweep listing captures its rc instead of discarding it" $rc
grep -q 'PR listing FAILED' "$LOOP" && rc=0 || rc=1
ck "a failed sweep listing is logged, not silently empty" $rc
# …and the sweep loop must be fed the CAPTURED listing: a shape that logs the failure but still
# pipes gh directly would sweep an empty list around the captured variable.
grep -qF '${SWEEP_LIST:-}" |' "$LOOP" && rc=0 || rc=1
ck "the sweep loop is fed the captured listing, not gh directly" $rc

# ── 17) the daemon PATH never contains a shim / ~/.pi directory ──────────────────
# macOS TCC blocks LAUNCHD from reading ~/Documents, so ~/.pi/agent/shims/gh (a symlink into
# ~/Documents) is EPERM under the daemon ("Operation not permitted") even though it runs fine in
# this interactive shell — which is why the install.sh probe-by-execution reports a FALSE GREEN.
# The guard is the exclusion in add_dir(), so DRIVE THE SHIPPED PATH CODE (extracted, not
# re-implemented): put a shim FIRST on the driving PATH, render DAEMON_PATH, and assert no shim/
# ~/.pi directory survives while the real binary's directory is kept. A pre-fix add_dir fixture
# proves the scenario is real and that dropping the exclusion would fail this check.
unset -f gh pi uv tmux 2>/dev/null || true   # an exported function would shadow the stubs below
awk '/^add_dir\(\) \{/{f=1} f{ if (/^ok "daemon PATH:/) exit; print }' "$INST" > "$TMP/daemonpath.sh"
if need "$TMP/daemonpath.sh" "the daemon-PATH block is extracted from the shipped install.sh"; then
  DP="$TMP/dp"; mkdir -p "$DP/home/.pi/agent/shims" "$DP/realbin"
  # the shim lives under a ~/.pi/.../shims path (exactly the shape TCC refuses); the REAL binary
  # lives in a plain directory that matches none of the excluded patterns.
  printf '#!/bin/bash\nprintf "gh 9.9.9 (shim)\\n"\n' > "$DP/home/.pi/agent/shims/gh"
  printf '#!/bin/bash\nprintf "gh 9.9.9 (real)\\n"\n' > "$DP/realbin/gh"
  chmod +x "$DP/home/.pi/agent/shims/gh" "$DP/realbin/gh"
  DRIVE_PATH="$DP/home/.pi/agent/shims:$DP/realbin:/usr/bin:/bin"
  pre="$(HOME="$DP/home" PATH="$DRIVE_PATH" command -v gh)"
  [ "$pre" = "$DP/home/.pi/agent/shims/gh" ] && rc=0 || { rc=1; echo "      'gh' resolved to '$pre'"; }
  ck "non-vacuity: 'gh' on the driving PATH resolves through the shim" $rc
  { echo 'set -u'; cat "$TMP/daemonpath.sh"; echo 'printf "%s\\n" "$DAEMON_PATH"'; } > "$DP/drive.sh"
  bash -n "$DP/drive.sh" && rc=0 || rc=1
  ck "the daemon-PATH driver parses" $rc
  out="$(HOME="$DP/home" PATH="$DRIVE_PATH" bash "$DP/drive.sh")"
  case "$out" in *"/shims"*|*"/.pi/"*) rc=1; echo "      daemon PATH: $out";; *) rc=0;; esac
  ck "the daemon PATH contains no */shims or */.pi/* directory" $rc
  case ":$out:" in *":$DP/realbin:"*) rc=0;; *) rc=1; echo "      daemon PATH: $out";; esac
  ck "the daemon PATH keeps the real binary's directory (the shim is dropped, not the tool)" $rc
  res="$(PATH="$out" command -v gh)"
  # The base DAEMON_PATH prefix is hardcoded and may hold a real gh on a brew box, so the invariant
  # is "resolves to a REAL executable, not the shim" — asserting equality with the fixture's realbin
  # over-specifies which of two real binaries wins and false-reds a `brew install gh` box.
  case "$res" in ""|"$DP/home/.pi/agent/shims/gh") rc=1;; *) [ -x "$res" ] && rc=0 || rc=1;; esac
  ck "under the daemon PATH, gh resolves to a real (non-shim) executable" $rc
  # Non-vacuity: the PRE-FIX add_dir (no exclusion) does bake the shim dir into the PATH.
  cat > "$DP/prefix-add_dir.sh" <<'SH'
add_dir() {
  case "$1" in /*) ;; *) return 0 ;; esac
  case ":$DAEMON_PATH:" in *":$1:"*) ;; *) DAEMON_PATH="$DAEMON_PATH:$1" ;; esac
}
DAEMON_PATH="/usr/bin:/bin"
p="$(command -v gh)"; add_dir "$(cd "$(dirname "$p")" && pwd)"
printf '%s\n' "$DAEMON_PATH"
SH
  old="$(HOME="$DP/home" PATH="$DRIVE_PATH" bash "$DP/prefix-add_dir.sh")"
  case "$old" in *"/shims"*) rc=0;; *) rc=1; echo "      pre-fix PATH: $old";; esac
  ck "non-vacuity: the pre-fix add_dir really admitted the shim dir" $rc
  # A PATH entry that is a SYMLINK to the shim directory must not slip past a name-only match,
  # and must not suppress the real binary (the resolve-past loop keys off the same test).
  ln -s "$DP/home/.pi/agent/shims" "$DP/shims-link"
  LINK_PATH="$DP/shims-link:$DP/realbin:/usr/bin:/bin"
  out2="$(HOME="$DP/home" PATH="$LINK_PATH" bash "$DP/drive.sh")"
  case "$out2" in *"/shims"*|*"/.pi/"*) rc=1; echo "      symlinked-shim daemon PATH: $out2";; *) rc=0;; esac
  ck "a symlink TO the shim dir cannot enter the daemon PATH" $rc
  case ":$out2:" in *":$DP/realbin:"*) rc=0;; *) rc=1; echo "      symlinked-shim daemon PATH: $out2";; esac
  ck "a symlinked shim dir does not suppress the real binary's directory" $rc
  # An EXPORTED `gh` function must not hide the real binary's directory: `type -P` ignores functions
  # (a function is not what launchd execs), so resolution still finds realbin.
  fnout="$( unset -f gh; gh() { exit 0; }; export -f gh; HOME="$DP/home" PATH="$DRIVE_PATH" bash "$DP/drive.sh" )"
  case ":$fnout:" in *":$DP/realbin:"*) rc=0;; *) rc=1; echo "      function-perturbed daemon PATH: $fnout";; esac
  ck "an exported 'gh' function does not hide the real binary's directory" $rc
fi

# ── 18) the install-time probe EXECUTES every tool, not just resolves it ────────
# Resolving a name is not running it: a shim/wrapper directory that does NOT match the exclusion
# patterns (cmux's `cmux-cli-shims` is a real one on this box) resolves the tool to a wrapper that
# exits 127, and a resolution-only probe reports install success while every lane spawn then fails.
# Drive the SHIPPED probe block with a fixture whose `pi` resolves but cannot run.
awk '/^for bin in tmux uv pi gh; do$/{f=1} f{print} f&&/^done$/{exit}' "$INST" > "$TMP/probe.sh"
if need "$TMP/probe.sh" "the install-time probe is extracted from the shipped install.sh"; then
  PB="$TMP/probe"; mkdir -p "$PB/bin"
  for b in tmux uv gh; do printf '#!/bin/bash\nexit 0\n' > "$PB/bin/$b"; chmod +x "$PB/bin/$b"; done
  # ⛔ The driver runs under install.sh's OWN shell options (`set -euo pipefail`, line 12), not a
  # weaker `set -u`: `_p="$(… || true)"` is reached only because install.sh runs under errexit, and
  # a `set -u`-only driver cannot see the unguarded-assignment regression it exists to pin.
  { echo 'set -euo pipefail'; echo 'die() { echo "DIED: $*" >&2; exit 9; }'; echo 'warn() { echo "WARN: $*" >&2; }'; cat "$TMP/probe.sh"; } > "$PB/drive.sh"
  bash -n "$PB/drive.sh" && rc=0 || rc=1
  ck "the probe driver parses" $rc
  printf '#!/bin/bash\nexit 127\n' > "$PB/bin/pi"; chmod +x "$PB/bin/pi"
  PATH="$PB/bin:/usr/bin:/bin" DAEMON_PATH="$PB/bin" bash "$PB/drive.sh" >/dev/null 2>&1; rc=$?
  [ "$rc" = "9" ] && rc=0 || { rc=1; echo "      probe rc=$rc (expected 9 — a resolved-but-non-running pi must fail install)"; }
  ck "the probe fails install when a resolved tool cannot RUN (exit 127)" $rc
  printf '#!/bin/bash\nexit 0\n' > "$PB/bin/pi"; chmod +x "$PB/bin/pi"
  PATH="$PB/bin:/usr/bin:/bin" DAEMON_PATH="$PB/bin" bash "$PB/drive.sh" >/dev/null 2>&1; rc=$?
  [ "$rc" = "0" ] && rc=0 || { rc=1; echo "      probe rc=$rc (expected 0 when every resolved tool runs)"; }
  ck "the probe passes when every resolved tool runs" $rc
  # An EXPORTED shell function named `gh` must NOT satisfy the probe: `command -v` reports it, but a
  # function is not what launchd execs and the daemon PATH has no gh binary at all.
  PB2="$PB/bin2"; mkdir -p "$PB2"
  for b in tmux uv pi; do printf '#!/bin/bash\nexit 0\n' > "$PB2/$b"; chmod +x "$PB2/$b"; done
  ( unset -f gh pi uv tmux; gh() { exit 0; }; export -f gh; \
    PATH="$PB2:/usr/bin:/bin" DAEMON_PATH="$PB2" bash "$PB/drive.sh" ) >/dev/null 2>&1; rc=$?
  [ "$rc" = "9" ] && rc=0 || { rc=1; echo "      probe rc=$rc (expected 9 — an exported gh FUNCTION is not a daemon binary)"; }
  ck "an exported shell function does not satisfy the probe" $rc
  # A wrapper that DELEGATES by name must not pass because the AMBIENT PATH can satisfy it: the
  # probe must execute under $DAEMON_PATH, where nothing can (the false green one layer in).
  PB3="$PB/w3"; mkdir -p "$PB3/shared" "$PB3/wrap" "$PB3/real" "$PB3/base"
  for b in tmux uv gh; do printf '#!/bin/bash\nexit 0\n' > "$PB3/shared/$b"; chmod +x "$PB3/shared/$b"; done
  printf '#!/bin/bash\nexit 0\n' > "$PB3/real/pi"; chmod +x "$PB3/real/pi"
  cat > "$PB3/wrap/pi" <<'SH'
#!/bin/bash
self="$(cd "${0%/*}" && pwd)"
found=""
IFS=:
for d in $PATH; do
  d="$(cd "$d" 2>/dev/null && pwd)" || continue
  [ "$d" = "$self" ] && continue
  [ -x "$d/pi" ] && { found="$d/pi"; break; }
done
[ -n "$found" ] && exec "$found" "$@"
exit 127
SH
  chmod +x "$PB3/wrap/pi"
  ( unset -f gh pi uv tmux; \
    PATH="$PB3/shared:$PB3/wrap:$PB3/real:/usr/bin:/bin" DAEMON_PATH="$PB3/base:$PB3/shared:/usr/bin:/bin:$PB3/wrap" bash "$PB/drive.sh" ) >/dev/null 2>&1; rc=$?
  [ "$rc" = "9" ] && rc=0 || { rc=1; echo "      probe rc=$rc (expected 9 — a name-delegating wrapper must fail under the daemon PATH)"; }
  ck "a name-delegating wrapper is probed under the daemon PATH, not the ambient one" $rc
fi

# ── load gate: an agent spawn must not be piled onto a post-boot indexing storm ──
# Driven, not grep'd: a text-presence check passes against a build with the gate neutered to
# `can_spawn() { true; }` — reproduced before this version replaced it.
# The predicate now lives in the shared helper (agent-infra#1720), which is the only file in the
# harness that invokes `pi`. Extraction is against THAT file, so this check tracks the real gate.
grep -m1 '^can_spawn() {' "$GATE" > "$TMP/canspawn.sh"
if need "$TMP/canspawn.sh" "can_spawn() is extracted from the shipped spawn-gate.sh"; then
  MAX_LOAD=16
  . "$TMP/canspawn.sh"
  can_spawn 0   && rc=0 || rc=1; ck "shipped can_spawn(): load 0 < 16 -> spawn allowed" $rc
  can_spawn 15  && rc=0 || rc=1; ck "shipped can_spawn(): load 15 < 16 -> spawn allowed" $rc
  can_spawn 16  && rc=1 || rc=0; ck "shipped can_spawn(): load 16 -> spawn held back" $rc
  can_spawn 221 && rc=1 || rc=0; ck "shipped can_spawn(): load 221 (post-boot storm) -> held back" $rc
  can_spawn ""  && rc=0 || rc=1; ck "shipped can_spawn(): an empty reading is the documented fail-open (a failed probe must not wedge merging)" $rc
fi
# Assert each of the four spawn SITES, not a count — a count is satisfied by decoy comments,
# which is exactly how an earlier version of this check passed against a stripped build. They pass
# the reading they cached for the cycle, so the [cap] log line and the gate cannot disagree.
fixer_sites=$(grep -F "grep -c '^fix-')" "$LOOP" | grep -cF 'can_spawn "$L"')
merge_site=$(grep -F "grep -c '^merge-')" "$LOOP" | grep -cF 'can_spawn "$L"')
review_site=$(grep -cF '&& can_spawn "$L" \' "$LOOP")
[ "$fixer_sites" = "2" ] && rc=0 || rc=1
ck "both fixer spawn sites (conflict + CI-red) carry the load gate" $rc
[ "$merge_site" = "1" ] && rc=0 || rc=1
ck "the merge re-review spawn carries the load gate" $rc
[ "$review_site" = "1" ] && rc=0 || rc=1
ck "the review-fix spawn carries the load gate" $rc
grep -q 'gh pr merge "\$PR" --repo "\$OR" --squash >>"\$LOG"' "$LOOP" && rc=0 || rc=1
ck "the cheap merge path stays ungated" $rc

# ── every WORKER launch path is gated at the spawn (agent-infra#1720) ──────────────
# The controller's four decision sites above were never the invariant: they gate the choice to
# create a session, not the `pi` process. chain-<N> carries the claim and waits up to 90 min for
# its PR, so it must re-read load when it actually spawns — which only happens if the SPAWN is
# gated. These checks pin (a) that there is exactly ONE spawn site and it is gated, and (b) that
# each worker ENTRY POINT really reaches it, by running the shipped script.
#
# (a) One spawn site. A fourth launch path added tomorrow fails HERE, which is the point: the
#     invariant is universal, not three fixes.
#     ⚠️ BOUNDARY, recorded rather than silently assumed (review note on #1720): the pattern matches
#     a COMMAND-position `pi` (`  pi …`, `  exec pi …`). A launch spelled `… "pi -p …"` inside a
#     tmux string, `command pi`, or an absolute `…/pi` would evade it. No such site exists today
#     (every `pi` occurrence in the shipped set was enumerated), and widening the pattern risks
#     matching the prose/heredocs that merely mention `pi` — so the claim this check supports is
#     "no file starts a line with `pi` except the helper", not "no conceivable launch can hide".
spawn_files="$(grep -lE '^[[:space:]]*(exec +)?pi( |$)' "$HERE"/*.sh 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')"
[ "$spawn_files" = "spawn-gate.sh " ] && rc=0 || { rc=1; echo "      pi is invoked by: ${spawn_files:-<none>}"; }
ck "exactly one shipped file invokes pi — the shared gated helper spawn-gate.sh" $rc
# Each worker must reach the gate and must not grow a private copy of the spawn (the copy-paste
# that produced this defect). Absence of a `pi` call in the worker is asserted by (a) already.
PATHCK=0
for w in lane.sh merge-one.sh fix-one.sh; do
  grep -q 'spawn-gate\.sh' "$HERE/$w" || { echo "      $w does not source spawn-gate.sh"; PATHCK=1; }
  grep -q 'run_agent "' "$HERE/$w" || { echo "      $w never calls run_agent"; PATHCK=1; }
  grep -qE '^[[:space:]]*run_agent\(\)' "$HERE/$w" && { echo "      $w defines its own private run_agent"; PATHCK=1; }
  grep -q 'AGENT_LOAD_REFUSED' "$HERE/$w" || { echo "      $w ignores a load refusal (rc 75)"; PATHCK=1; }
done
ck "lane.sh, merge-one.sh and fix-one.sh all start their agent through the shared gated run_agent()" $PATHCK
# A partial `cp` to $O (README §9) would leave a worker with no helper. It must die, not fall back
# to an ungated spawn — an undefined `run_agent` would otherwise read as "the agent ran" (rc 127).
FAILLOUD=0
for w in orch-loop.sh lane.sh merge-one.sh fix-one.sh; do
  grep -q 'spawn-gate\.sh missing' "$HERE/$w" || { echo "      $w would run WITHOUT the gate if the helper were missing"; FAILLOUD=1; }
done
ck "the controller and every worker refuse to run when the shared helper is missing (no ungated fallback)" $FAILLOUD
# (b) Drive the shipped helper with a stubbed sysctl and a stubbed pi: high load -> pi never runs.
#     Non-vacuous: deleting the gate, or neutering it to `can_spawn() { true; }`, starts pi at load
#     150 and fails this — which is exactly how the pre-fix tree is proved to fail it.
# This block is deliberately NOT guarded by `if [ -s "$GATE" ]`: a missing helper would then skip
# every driven check while still reporting a pass, which is the vacuity this file exists to
# prevent. On a tree without the gate these probes still RUN and fail — each worker starts its
# agent under a fake load of 150, which is exactly the defect, reproduced. (The block stays
# indented because the heredocs inside it must not be indented; bash does not care.)
  GB="$TMP/gatebin"; mkdir -p "$GB"; GMARK="$TMP/pi-ran"
  cat > "$GB/pi" <<'SH'
#!/bin/bash
: > "$PI_MARKER"
exit 0
SH
  # `sysctl -n hw.ncpu` is what the helper resolves MAX_LOAD from; `-n vm.loadavg` is the gate's
  # reading. Both are stubbed, so the test runs offline and at a load this box does not have.
  cat > "$GB/sysctl" <<'SH'
#!/bin/bash
if [ "${1:-}" = "-n" ] && [ "${2:-}" = "vm.loadavg" ]; then
  printf '{ %s.00 %s.00 %s.00 }\n' "$FAKE_LOAD" "$FAKE_LOAD" "$FAKE_LOAD"
else
  echo 10
fi
SH
  # `gh` returns a head sha (so the refusal path has a real head it COULD have recorded against),
  # `git` answers the worktree lookup — no repo, no network, no writes outside $TMP.
  printf '#!/bin/bash\nfor a in "$@"; do [ "$a" = ".headRefOid" ] && { echo deadbeefdeadbeef; exit 0; }; done\nexit 0\n' > "$GB/gh"
  printf '#!/bin/bash\ncase "${1:-}" in worktree) printf "worktree /tmp/w\\nbranch refs/heads/br\\n" ;; rev-parse) exit 0 ;; *) printf "\\n" ;; esac\nexit 0\n' > "$GB/git"
  chmod +x "$GB"/*
  printf 'prompt\n' > "$TMP/gate-prompt.txt"
  # the shipped helper, run under a fake load of 150 (the box's real ceiling is 100)
  ( export PATH="$GB:$PATH" FAKE_LOAD=150 PI_MARKER="$GMARK" MAX_LOAD=100
    rm -f "$GMARK"; . "$GATE"
    run_agent 1 "$TMP/gate-high.log" "$TMP/gate-prompt.txt" "probe agent" ) >/dev/null 2>&1
  GOT=$?
  [ "$GOT" = "75" ] && [ ! -e "$GMARK" ] && rc=0 || { rc=1; echo "      rc=$GOT marker=$([ -e "$GMARK" ] && echo present || echo absent)"; }
  ck "shipped run_agent(): load 150 >= MAX_LOAD=100 -> pi is NOT started, returns 75" $rc
  grep -q 'REFUSED' "$TMP/gate-high.log" && rc=0 || rc=1
  ck "shipped run_agent(): the refusal is logged where the operator will see it" $rc
  # …and a quiet box still starts it. The stub pi exits at once, so the 1 s watchdog is killed by
  # run_agent itself and nothing is left behind.
  ( export PATH="$GB:$PATH" FAKE_LOAD=3 PI_MARKER="$GMARK" MAX_LOAD=100
    rm -f "$GMARK"; . "$GATE"
    run_agent 1 "$TMP/gate-low.log" "$TMP/gate-prompt.txt" "probe agent" ) >/dev/null 2>&1
  [ -e "$GMARK" ] && rc=0 || rc=1
  ck "shipped run_agent(): load 3 < MAX_LOAD=100 -> pi IS started" $rc

  # (c) Drive the three WORKER ENTRY POINTS end to end. Each is the shipped script, run with the
  #     stubbed PATH above; the assertions are on what it DID (start an agent or not) and on the
  #     records a refusal must not leave behind.
  probe() { # probe <script> <load> <O> <args...>
    local script="$1" load="$2" o="$3"; shift 3
    mkdir -p "$o/logs" "$o/state"; rm -f "$GMARK"
    ( export PATH="$GB:$PATH" FAKE_LOAD="$load" PI_MARKER="$GMARK" MAX_LOAD=100 \
             O="$o" LANE_TIMEOUT=1 REVIEW_TIMEOUT=1 FIX_TIMEOUT=1
      bash "$HERE/$script" "$@" ) >/dev/null 2>&1
    PROBE_RC=$?
  }
  mkdir -p "$TMP/mrepo"
  # lane.sh <issue> <branch> <owner/repo>
  probe lane.sh 150 "$TMP/plO" 5 br owner/repo
  [ "$PROBE_RC" = "75" ] && [ ! -e "$GMARK" ] && grep -q REFUSED "$TMP/plO/logs/lane-5.log" && rc=0 || { rc=1; echo "      rc=$PROBE_RC"; }
  ck "lane.sh under load 150: REFUSES to spawn the lane agent, exits 75" $rc
  probe lane.sh 3 "$TMP/plO2" 5 br owner/repo
  [ -e "$GMARK" ] && rc=0 || rc=1
  ck "lane.sh under load 3: the agent IS spawned" $rc
  # merge-one.sh — the reviewer path behind chain-<N>, fix-<PR> and the controller sweep. Assert a
  # refusal leaves NO needsfix marker (recording one would authorise a fixer for a review that never
  # happened, burn a MAX_FIX_TRIES attempt and can park the PR) and releases the per-PR claim, so
  # the next cycle re-dispatches instead of skipping the PR as "in flight".
  probe merge-one.sh 150 "$TMP/pmO" "$TMP/mrepo" owner/repo 1 br
  [ "$PROBE_RC" = "75" ] && [ ! -e "$GMARK" ] && grep -q REFUSED "$TMP/pmO/logs/merge-1.log" && rc=0 || { rc=1; echo "      rc=$PROBE_RC"; }
  ck "merge-one.sh under load 150 (the chain-<N> path): reviewer REFUSES to spawn, exits 75" $rc
  [ -z "$(ls "$TMP/pmO/state" 2>/dev/null | grep '^needsfix-1-')" ] && [ ! -e "$TMP/pmO/state/mergeclaim-1.lock" ] && rc=0 || { rc=1; echo "      state: $(ls "$TMP/pmO/state" 2>/dev/null | tr '\n' ' ')"; }
  ck "a load refusal is NOT a verdict: no needsfix marker, and the PR claim is released" $rc
  probe merge-one.sh 3 "$TMP/pmO2" "$TMP/mrepo" owner/repo 1 br
  [ -e "$GMARK" ] && rc=0 || rc=1
  ck "merge-one.sh under load 3: the reviewer IS spawned" $rc
  # fix-one.sh — the post-fix re-review path, and what adopt-fix.sh runs. A refusal must also skip
  # re-running the merge chain: nothing changed, so a re-review is pure cost on a loaded box.
  probe fix-one.sh 150 "$TMP/pfO" "$TMP/mrepo" owner/repo 1 br
  [ "$PROBE_RC" = "75" ] && [ ! -e "$GMARK" ] && grep -q REFUSED "$TMP/pfO/logs/fix-1.log" && rc=0 || { rc=1; echo "      rc=$PROBE_RC"; }
  ck "fix-one.sh under load 150 (the adopt-fix path): fixer REFUSES to spawn, exits 75" $rc
  grep -q 'merge chain NOT re-run' "$TMP/pfO/logs/fix-1.log" && rc=0 || rc=1
  ck "a refused fixer does not re-run the merge chain on an unchanged head" $rc
  probe fix-one.sh 3 "$TMP/pfO2" "$TMP/mrepo" owner/repo 1 br
  [ -e "$GMARK" ] && rc=0 || rc=1
  ck "fix-one.sh under load 3: the fixer IS spawned" $rc

  # ⛔ ISSUE 1 (agent-infra#1720). The controller books a fixer attempt when it CREATES the session
  # (orch-loop.sh), which is UPSTREAM of the load gate inside fix-one.sh. A refusal therefore has to
  # give the attempt back: three refusals otherwise reach MAX_FIX_TRIES and PARK a PR that never had
  # a single fixer run. These drive the SHIPPED fixer through the controller's --argv contract with a
  # pre-seeded counter and findings marker.
  # Non-vacuity for the refund: the pre-fix fixer ignores the 5th argv line and never refunds, so
  # fixcount stays 1 and the check fails (verified by adding it, then removing the refund).
  AO="$TMP/pfA"; mkdir -p "$AO/logs" "$AO/state"
  printf '%s\n' "$TMP/mrepo" owner/repo 1 br deadbeefdeadbeef > "$AO/state/argv-1"
  echo 1 > "$AO/state/fixcount-1"
  : > "$AO/state/needsfix-1-deadbeefdeadbeef"
  rm -f "$GMARK"
  ( export PATH="$GB:$PATH" FAKE_LOAD=150 PI_MARKER="$GMARK" MAX_LOAD=100 O="$AO" FIX_TIMEOUT=1
    bash "$HERE/fix-one.sh" --argv "$AO/state/argv-1" ) >/dev/null 2>&1
  AREF=$?
  [ "$AREF" = "75" ] && [ ! -e "$GMARK" ] && [ "$(cat "$AO/state/fixcount-1" 2>/dev/null)" = "0" ] && [ -f "$AO/state/needsfix-1-deadbeefdeadbeef" ] && rc=0 || { rc=1; echo "      rc=$AREF fixcount=$(cat "$AO/state/fixcount-1" 2>/dev/null) marker=$([ -e "$AO/state/needsfix-1-deadbeefdeadbeef" ] && echo present || echo absent)"; }
  ck "a load-refused controller fixer refunds the MAX_FIX_TRIES attempt (a refusal is not an attempt)" $rc
  grep -q 'refunded' "$AO/logs/fix-1.log" && rc=0 || rc=1
  ck "the fixcount refund is logged in the fixer's own log" $rc
  # The other half: once the agent HAS started the attempt is spent, so the fixer consumes the
  # findings marker for ITS head. The controller no longer deletes it at dispatch, so a refused
  # fixer cannot leave the loop re-recording a fresh review of a head nobody fixed.
  # Non-vacuous: the pre-fix fixer never removes it (the check then sees the marker present).
  BO="$TMP/pfB"; mkdir -p "$BO/logs" "$BO/state"
  printf '%s\n' "$TMP/mrepo" owner/repo 1 br deadbeefdeadbeef > "$BO/state/argv-1"
  echo 1 > "$BO/state/fixcount-1"
  : > "$BO/state/needsfix-1-deadbeefdeadbeef"
  rm -f "$GMARK"
  ( export PATH="$GB:$PATH" FAKE_LOAD=3 PI_MARKER="$GMARK" MAX_LOAD=100 O="$BO" FIX_TIMEOUT=1
    bash "$HERE/fix-one.sh" --argv "$BO/state/argv-1" ) >/dev/null 2>&1
  [ -e "$GMARK" ] && [ ! -e "$BO/state/needsfix-1-deadbeefdeadbeef" ] && [ "$(cat "$BO/state/fixcount-1" 2>/dev/null)" = "1" ] && rc=0 || { rc=1; echo "      marker=$([ -e "$BO/state/needsfix-1-deadbeefdeadbeef" ] && echo present || echo absent) fixcount=$(cat "$BO/state/fixcount-1" 2>/dev/null)"; }
  ck "a STARTED fixer consumes the findings marker for its head and does NOT refund the attempt" $rc
  # And the controller must not delete the marker when it creates the session — that happens
  # upstream of the gate. Pin both halves structurally; the driven probes above pin the behaviour.
  grep -q 'rm -f "$O/state/needsfix' "$LOOP" && rc=1 || rc=0
  ck "the controller does not delete needsfix-<PR>-<head> at fixer dispatch" $rc
  grep -q 'rm -f "$O/state/needsfix-$PR-$HEAD"' "$HERE/fix-one.sh" && rc=0 || rc=1
  ck "fix-one.sh consumes the findings marker itself, only once the agent has started" $rc

  # ⛔ ISSUE 2 (agent-infra#1720). adopt-fix.sh's new reporting line became its LAST command, so the
  # script's status was the AND-list's: 1 on a clean success and 0 on the load refusal it exists to
  # report. Drive the shipped script with a stub fix-one.sh and assert the status follows the fixer.
  # `O` is where adopt-fix looks for fix-one.sh, so the stub goes THERE; `git` goes on PATH and the
  # stub dir precedes the real one (macOS ships no git in /opt/homebrew/bin, which adopt-fix
  # prepends, so the stub is the one that resolves).
  AX="$TMP/adopt"; mkdir -p "$AX/logs" "$AX/bin" "$AX/repo"
  printf '#!/bin/bash\nexit "${STUB_FIX_RC:-0}"\n' > "$AX/fix-one.sh"
  printf '#!/bin/bash\nexit 0\n' > "$AX/bin/git"
  chmod +x "$AX/fix-one.sh" "$AX/bin/git"
  af_rc() { ( export PATH="$AX/bin:$PATH" O="$AX" STUB_FIX_RC="$1"
              bash "$HERE/adopt-fix.sh" "$AX/repo" owner/repo 1 br ) >/dev/null 2>&1; echo $?; }
  A0="$(af_rc 0)"; [ "$A0" = "0" ] && rc=0 || { rc=1; echo "      rc=$A0"; }
  ck "adopt-fix.sh exits 0 when the fixer succeeded (status is not inverted by its reporting line)" $rc
  A75="$(af_rc 75)"; [ "$A75" = "75" ] && rc=0 || { rc=1; echo "      rc=$A75"; }
  ck "adopt-fix.sh exits 75 on a load refusal (the status it reports)" $rc
  A3="$(af_rc 3)"; [ "$A3" = "3" ] && rc=0 || { rc=1; echo "      rc=$A3"; }
  ck "adopt-fix.sh propagates a real fixer failure" $rc
  # The same probe on a copy with NO helper beside it: a partial sync must exit 78, not run ungated.
  # A quiet box is used deliberately — an ungated build WOULD spawn here, which is what this
  # discriminates.
  mkdir -p "$TMP/nogate"; cp "$HERE/lane.sh" "$TMP/nogate/"; rm -f "$GMARK"
  ( export PATH="$GB:$PATH" FAKE_LOAD=3 PI_MARKER="$GMARK" MAX_LOAD=100 O="$TMP/ngO"
    bash "$TMP/nogate/lane.sh" 5 br owner/repo ) >/dev/null 2>&1
  NG=$?
  [ "$NG" = "78" ] && [ ! -e "$GMARK" ] && rc=0 || { rc=1; echo "      rc=$NG marker=$([ -e "$GMARK" ] && echo present || echo absent)"; }
  ck "a worker with no spawn-gate.sh beside it exits 78 instead of starting an ungated agent" $rc
# The retired scripts are the named residual of §4's gating claim: they invoke pi and are NOT
# gated. A banner on each is what stops a silent re-activation from looking like any other file, so
# pin it — the claim is only honest while the residual is visible where reuse would happen.
RCK=0
for f in "$HERE"/retired/*.sh; do
  grep -qE '^[[:space:]]*(exec +)?pi( |$)' "$f" || continue
  { grep -q 'NOT load-gated' "$f" && grep -q 'spawn-gate\.sh' "$f"; } || { echo "      $(basename "$f") spawns pi with no 'not load-gated' banner"; RCK=1; }
done
ck "every retired script that invokes pi carries the 'not load-gated' banner" $RCK

# ── refresh-candidates.sh: the pool is ordered by SEVERITY, not by issue number ─────
# #8465: the pool was sorted `sort -n -u` over the NUMBERS ALONE — oldest first — so 238
# pool-eligible P2-moderate sat ahead of 20 P0-blocker and the P0s were unreachable. Extract the
# REAL pipeline from the shipped script and drive it with a fixture; re-implementing the sort here
# would pass on the broken copy too. Verified non-vacuous: the pre-fix extraction yields `sort -n -u`,
# whose output is `0 7951 1 8039 2 1120` and fails this assertion.
#
# #8469: the producer writes TWO files from one sort — `.prio` (`<number>TAB<tier>`, the map
# orch-loop.sh's P0 merge-priority override reads) and `.pool` (bare numbers, the contract the loop
# below reads; `cut -f1` derives it). The assertion pins BOTH: a tier leaking back into `.pool` is a
# malformed argument to the loop (`read -r N` takes the WHOLE line, so nothing is pre-flighted and
# the backlog never refills), and a missing `.prio` silently disables the override. Neither is
# visible from the sort alone.
POOL_SORT="$(grep -E '^\} \| sort ' "$REFRESH" | sed -e 's/^} | //' -e 's/ > "\$O\/\.prio"$//')"
if [ -n "$POOL_SORT" ] && grep -q '^cut -f1 "\$O/\.prio" > "\$O/\.pool"$' "$REFRESH"; then
  # The within-tier pairs are MIXED WIDTH on purpose. With every number four digits, `sort -n`
  # happens to agree with `sort -k1,1n -k2,2n` on this fixture, so a copy that dropped the key spec
  # stayed green (verified 2026-10-13). 900/1000 separate them — keyed puts 900 first, a bare
  # `sort -n` puts 1000 first — and tortoise is about to cross issue 10000.
  # Run the shipped pipeline ONCE and assert BOTH artefacts it feeds: `.prio` = number<TAB>tier,
  # and `.pool` = the `cut -f1` derivation this file's loop reads.
  pool_out="$(printf '2 1120\n1 8039\n0 7951\n2 3147\n1 7936\n0 7846\n2 3329\n1 8039\n0 3846\n1 900\n1 1000\n' \
              | sh -c "$POOL_SORT")"
  prio_got="$(printf '%s\n' "$pool_out" | tr '\t' ':' | tr '\n' ' ' | sed 's/ *$//')"
  pool_got="$(printf '%s\n' "$pool_out" | cut -f1 | tr '\n' ' ' | sed 's/ *$//')"
  { [ "$prio_got" = "3846:0 7846:0 7951:0 900:1 1000:1 7936:1 8039:1 1120:2 3147:2 3329:2" ] && \
    [ "$pool_got" = "3846 7846 7951 900 1000 7936 8039 1120 3147 3329" ]; } && rc=0 || rc=1
else rc=1; fi
ck "refresh-candidates.sh orders .prio severity-first (P0<P1<P2), age second, dedup by number, and cut-derives a bare-number .pool" $rc

# The pipeline above cannot see the TIER->RANK half. A copy that emitted the SAME rank for every
# tier still sorts "correctly" and still starves the P0s — #8465 itself. So assert the label->rank
# pairs the shipped script actually emits. Join backslash continuations first, or `--label` and the
# `|"N ` land on different lines and the extraction finds nothing (verified).
if [ -s "$REFRESH" ]; then
  RANKS="$(awk '{printf "%s", $0} /\\$/{next} {print ""}' "$REFRESH" \
    | awk '/--label/{lab=""; for(i=1;i<=NF;i++) if($i=="--label") lab=$(i+1);
	     if(match($0,/\|"[0-9] /)) print lab" -> "substr($0,RSTART+2,1)}' \
    | tr '\n' ' ' | sed 's/ *$//')"
  [ "$RANKS" = "P0-blocker -> 0 P1-severe -> 1 P2-moderate -> 2" ] && rc=0 || rc=1
else rc=1; fi
ck "refresh-candidates.sh ranks the tiers P0=0 < P1=1 < P2=2 (a constant rank would starve the P0s)" $rc

# The backlog is consumed top-down, so its ORDER IS the priority — a numeric re-sort put the P0s
# behind the P2s on every cycle (#8465). Assert BEHAVIOUR, not spelling: drive the shipped TAIL —
# the dedupe line to EOF, with the real $O redirected into $TMP — against a fixture, and assert the
# resulting file. Executing it is what makes the check spelling-independent. The textual forms both
# missed and over-matched: a grep for a command-position `sort` accepted 10 of 16 built variants that
# really did re-sort the backlog (`LC_ALL=C sort`, `command sort`, `env sort`, a subshell, an `if`, an
# absolute path, the path in a variable…), and it went RED on a COMMENT that merely quoted the pre-fix
# line — the natural thing to write here. A comment is inert when executed; it cannot trip this.
# Driving the dedupe line ALONE is also not enough: that cannot see an ADDED re-sort after it, so this
# drives to EOF rather than `-m1`.
# ⛔ ANCHOR BELOW THE POOL LOOP, NOT AT THE DEDUPE LINE. The pre-fix re-sort sat directly after
# `done < "$O/.pool"`, so anchoring on the dedupe left its own former position — the most likely
# place a partial revert restores it — outside the driven region: with the pre-fix line re-added
# there this check reported 130 passed, 0 failed (reproduced 2026-10-13 against the head). Starting
# after the loop's `done` covers every write the pool loop and the dedupe make to candidates.txt.
# RESIDUAL — a boundary, not a claim of completeness: the driven region starts after the pool loop,
# so code ABOVE it is not executed. The pool pipeline writes `$O/.pool`, never candidates.txt, and the
# reaping pass above that writes dispatched.txt/checked.txt — so nothing above the loop can re-sort the
# backlog today. A re-sort GATED on a value assigned above the loop would still be missed (verified).
# Widening further means stubbing `gh` and driving the pool build too; this is the boundary drawn.
if [ -s "$REFRESH" ]; then
  awk '/^done < /{f=1;next} f' "$REFRESH" > "$TMP/tail.sh"
  # Fail closed if the extraction found nothing, or lost the file it is supposed to exercise.
  if [ -s "$TMP/tail.sh" ] && grep -q 'candidates\.txt' "$TMP/tail.sh"; then
    # Redirect $O at the SOURCE rather than editing paths: `awk -v` passes the fixture dir without any
    # shell quoting of `$O` on the command line. Escape it first — in awk's gsub REPLACEMENT `&` means
    # the matched text and `\x` is an escape, so a `&` or `\\` in $TMP would corrupt the generated
    # script. macOS `mktemp -d` ignores TMPDIR so it cannot occur here, but the Linux CI lane honours
    # TMPDIR and this harness treats `&`-in-path as hostile everywhere else.
    esc_tmp="${TMP//\\/\\\\}"; esc_tmp="${esc_tmp//&/\\&}"
    awk -v tmp="$esc_tmp" '{gsub(/\$O/, tmp)} 1' "$TMP/tail.sh" > "$TMP/tail-drive.sh"
    # 5000 leads AND repeats, so one expectation separates all three outcomes: in-place dedupe keeps
    # the first occurrence (5000 100 9000); a re-sort of ANY spelling moves 100 to the front; a
    # deleted dedupe leaves both 5000s.
    printf '5000\n100\n9000\n5000\n' > "$TMP/candidates.txt"
    # ⛔ SANDBOX THE DRIVE. The substitution above only rewrites the literal `$O`; a path spelled any
    # other way escapes to the REAL location — and `$HOME/orchestrator/candidates.txt` is the shipped
    # script's own default O and is live on a running box. Binding O and HOME to $TMP closes it
    # (confirmed: without this a `$HOME/orchestrator/...` variant rewrote the live file).
    ( cd "$TMP" && O="$TMP" HOME="$TMP" bash tail-drive.sh ) >/dev/null 2>&1
    tail_got="$(tr '\n' ' ' < "$TMP/candidates.txt" 2>/dev/null | sed 's/ *$//')"
    [ "$tail_got" = "5000 100 9000" ] && rc=0 || rc=1
  else rc=1; fi
else rc=1; fi
ck "refresh-candidates.sh dedupes the backlog in place and nothing running it re-sorts it" $rc

# ── 19) the harness runs on Linux as well as macOS (#1702) ──────────────────────
# The shipped cell controller (cloud-cell-supervisor.sh) starts THIS loop on a Linux cell, and every
# capacity probe it used was macOS-only. Reproduced on GNU coreutils 9.7 (debian:stable-slim):
# `stat -f '%m' F` prints the FILESYSTEM LISTING on stdout and exits 1, so the `|| echo` fallback
# APPENDED, the arithmetic failed, `age` was never assigned, and set -u turned the following test
# into `age: unbound variable` — the controller died one cycle after its first dispatch, on every
# cell. The Linux branch of each replacement is driven below, with the GNU shapes stubbed.
# Verified the hard way: dropped into the PRE-FIX tree, these checks FAIL (reproduced against the
# pre-fix orch-loop.sh: file_mtime_epoch extraction empty and the GNU-shape reaper drive aborts).
# Called for by the issue's own verification table: a stub `stat` that reproduces the GNU shapes. It
# is created UNCONDITIONALLY so the reaper drive below really is driven by GNU semantics even when
# the helper is absent (otherwise the stub would be missing, the real BSD stat would answer, and the
# drive would pass on the broken copy — verified by running it against the pre-fix tree).
mkdir -p "$TMP/gnubin"
cat > "$TMP/gnubin/stat" <<'SH'
#!/bin/bash
# GNU coreutils shape, emulated on ANY host:
#   -c '%Y' <file>  -> succeeds, printing the epoch (real GNU behaviour)
#   -f '%m' <file>  -> prints the FILESYSTEM LISTING on stdout and exits 1 (real GNU behaviour)
# The -c branch MUST NOT delegate to the ambient /usr/bin/stat. Doing so emulates "GNU success" with
# a BSD binary on a mac and with a GNU one on Linux — either way the drive tests the HOST, not the
# GNU shape, and on Linux the branch returned GNU's listing/failure so the reaper skipped every
# session and the drive below reaped nothing (the four §19 checks false-redded the fixed tree).
# `date -r <file> +%s` is the portable way to ask any host for a file's epoch mtime.
if [ "${1:-}" = "-c" ]; then date -r "${3:-}" +%s; exit $?; fi
if [ "${1:-}" = "-f" ]; then echo "/dev/disk1 234881024 1234 5678 9% /"; exit 1; fi
exit 1
SH
chmod +x "$TMP/gnubin/stat"
sed -n '/^file_mtime_epoch() {/,/^}/p' "$LOOP" > "$TMP/mtime-body.sh"
# `need` on the helper ALONE: if it is absent (the pre-fix tree) the whole block is skipped, rather
# than every check inside passing vacuously off `command not found` (which really does return rc!=0).
if need "$TMP/mtime-body.sh" "file_mtime_epoch() is extracted from the shipped loop"; then
  { grep -m1 '^is_num() {' "$LOOP"; cat "$TMP/mtime-body.sh"; } > "$TMP/mtime.sh"
  : > "$TMP/mt.log"; touch -t 202601010000 "$TMP/mt.log"
  # The expected epoch is the FILE's, asked of the host portably. `/usr/bin/stat -f '%m'` is BSD-only:
  # on GNU coreutils it prints the filesystem listing to stdout and exits 1, so `want` became a
  # listing and the comparisons below failed against a CORRECT helper. `date -r <file> +%s` works on
  # both (GNU and BSD date accept a reference file), so this check no longer assumes the host's shape.
  want="$(date -r "$TMP/mt.log" +%s)"
  got="$(bash -c ". '$TMP/mtime.sh'; file_mtime_epoch '$TMP/mt.log'")"
  [ "$got" = "$want" ] && rc=0 || { rc=1; echo "      host got '$got' want '$want'"; }
  ck "file_mtime_epoch(): the host's real stat path returns the file's epoch mtime" $rc
  got="$(PATH="$TMP/gnubin:$PATH" bash -c ". '$TMP/mtime.sh'; file_mtime_epoch '$TMP/mt.log'")"
  [ "$got" = "$want" ] && rc=0 || { rc=1; echo "      gnu got '$(printf '%s' "$got" | tr '\n' '|')'"; }
  ck "file_mtime_epoch(): the GNU shape (stat -f prints a listing) still yields ONE numeric epoch" $rc
  # Both shapes fail -> non-zero rc with NO guessed age. The reaper then SKIPS the session; it must
  # not fall back to `date +%s` (age 0 is a guess that reads as a healthy worker either way).
  got="$(PATH="$TMP/gnubin:$PATH" bash -c ". '$TMP/mtime.sh'; file_mtime_epoch '$TMP/absent.log' && echo ZERO || echo NONZERO")"
  [ "$got" = "NONZERO" ] && rc=0 || { rc=1; echo "      got '$got'"; }
  ck "file_mtime_epoch(): an unreadable file fails to a non-zero rc instead of guessing an age" $rc
fi
# The reaper driven with the GNU stat shape: the pre-fix copy ABORTS here (`age: unbound variable`,
# rc 1) and kills nothing. Assert rc=0 AND the kill decision — stale reaped, fresh spared — so a
# "fix" that simply stopped reaping cannot pass this.
{ grep -m1 '^is_num() {' "$LOOP"; sed -n '/^file_mtime_epoch() {/,/^}/p' "$LOOP"; sed -n '/^reap_stale_workers() {/,/^}/p' "$LOOP"; } > "$TMP/reapw-gnu.sh"
if need "$TMP/reapw-gnu.sh" "the reaper and its stat helper are extracted for the GNU-shape drive"; then
  mkdir -p "$TMP/RG/logs"
  : > "$TMP/RG/logs/merge-999.log"; touch -t 202601010000 "$TMP/RG/logs/merge-999.log"
  : > "$TMP/RG/logs/merge-100.log"
  cat > "$TMP/rg.sh" <<SH
set -u
PATH="$TMP/gnubin:\$PATH"
LOG="$TMP/RG/rw.log"; O="$TMP/RG"; STALE_MIN=1; KILLED=""
tmux() { case "\${1:-}" in
  ls) printf 'merge-999\nmerge-100\n' ;;
  list-panes) echo 4242 ;;
  kill-session) KILLED="\$KILLED \$3" ;;
esac; }
kill() { :; }
. "$TMP/reapw-gnu.sh"
reap_stale_workers; rc=\$?
echo "rc=\$rc killed=\$KILLED"
SH
  out="$(bash "$TMP/rg.sh" 2>&1)"
  case "$out" in *"rc=0"*) rc=0;; *) rc=1; echo "      got: $out";; esac
  ck "shipped reaper, GNU stat shape: the cycle COMPLETES (rc 0) with no arithmetic abort" $rc
  case "$out" in *"merge-999"*) rc=0;; *) rc=1;; esac
  ck "shipped reaper, GNU stat shape: the stale session is reaped" $rc
  # ⛔ NOT a bare "does not contain merge-100": that is trivially true when the reaper ABORTS, so it
  # PASSED on the pre-fix code (verified against origin/main's orch-loop.sh). Assert the POSITIVE in
  # the same check — the run completed (rc 0) AND the killed set is EXACTLY the stale session — so a
  # crash and an over-reaching reaper both fail here. The stub records `$3` of `kill-session -t
  # "=$s"`, i.e. the exact-match token `=merge-999`; `grep -o` + stripping keeps the comparison exact
  # rather than a substring match that `=merge-999 =merge-100" would also satisfy.
  case "$out" in *"rc=0"*) rc=0;; *) rc=1; echo "      not rc=0: $out";; esac
  killed_list="$(printf '%s\n' "$out" | grep -o 'killed=.*' | sed 's/^killed= *//')"
  [ "$killed_list" = "=merge-999" ] || { rc=1; echo "      killed exactly: '$killed_list'"; }
  ck "shipped reaper, GNU stat shape: completes (rc 0) and reaps exactly the stale session" $rc
  grep -q 'reap-worker' "$TMP/RG/rw.log" 2>/dev/null && rc=0 || rc=1
  ck "shipped reaper, GNU stat shape: the kill is logged" $rc
fi
# `load1` moved into the shared helper (agent-infra#1720): the controller's decision sites and the
# workers' spawn gate MUST read the same probe, or the two gates disagree about what load is. The
# property is unchanged — extract from the file that now owns it.
sed -n '/^load1() {/,/^}/p' "$GATE" > "$TMP/load1.sh"
if need "$TMP/load1.sh" "load1() is extracted from the shipped helper"; then
  printf '7.42 1.10 0.95 2/345 999\n' > "$TMP/loadavg"
  got="$(bash -c ". '$TMP/load1.sh'; load1 '$TMP/loadavg'")"
  [ "$got" = "7" ] && rc=0 || { rc=1; echo "      got '$got'"; }
  ck "shipped load1(): the Linux (/proc/loadavg) path reads FIELD 1 as the 1-min load" $rc
  # macOS prints '{ 1.70 1.90 2.00 }', so there the 1-min load is field 2. Pin the FIELD, not only
  # the path: one awk shared by both platforms would pass a path-only check and still read 5-min.
  got="$(bash -c "sysctl() { echo '{ 3.30 4.40 5.50 }'; }; . '$TMP/load1.sh'; load1 '$TMP/absent-loadavg'")"
  [ "$got" = "3" ] && rc=0 || { rc=1; echo "      got '$got'"; }
  ck "shipped load1(): the macOS (sysctl vm.loadavg) path reads the 1-min field, not the 5-min" $rc
fi
sed -n '/^swap_usage_mb() {/,/^}/p' "$LOOP" > "$TMP/swap.sh"
if need "$TMP/swap.sh" "swap_usage_mb() is extracted from the shipped loop"; then
  printf 'MemTotal:       16000000 kB\nSwapTotal:       2048000 kB\nSwapFree:        1024000 kB\n' > "$TMP/meminfo"
  got="$(bash -c ". '$TMP/swap.sh'; swap_usage_mb '$TMP/meminfo'")"
  [ "$got" = "1000 2000" ] && rc=0 || { rc=1; echo "      got '$got'"; }
  ck "shipped swap_usage_mb(): the Linux (/proc/meminfo) path converts kB to MB as <free> <total>" $rc
  # 'we could not read it' must stay distinguishable from 'no swap configured' (total 0, the allow).
  # BOTH probes are stubbed to fail: with only the meminfo path unavailable the function correctly
  # falls back to sysctl, which is not the state under test here.
  got="$(bash -c "sysctl() { :; }; . '$TMP/swap.sh'; swap_usage_mb '$TMP/absent-meminfo' && echo OK || echo NONZERO")"
  [ "$got" = "NONZERO" ] && rc=0 || { rc=1; echo "      got '$got'"; }
  ck "shipped swap_usage_mb(): an unreadable probe returns non-zero, not a false '0 0'" $rc
  got="$(bash -c "sysctl() { echo 'total = 2048.00M  used = 1.00M  free = 2047.00M'; }; . '$TMP/swap.sh'; swap_usage_mb '$TMP/absent-meminfo'")"
  [ "$got" = "2047 2048" ] && rc=0 || { rc=1; echo "      got '$got'"; }
  ck "shipped swap_usage_mb(): the macOS (sysctl vm.swapusage) path still yields <free> <total> MB" $rc
fi
# Available memory is the FIFTH macOS-only probe and the only one that fails CLOSED: before #1702 a
# cell reported no vm_stat, so MEM_UNKNOWN was set every cycle and `mem_probe_failed` bound the gate
# FOREVER when load/lanes/swap were healthy — the controller ran, logged, and never dispatched a
# lane. That is the false green #1702 removes, so the Linux path is driven explicitly here.
sed -n '/^mem_avail_mb() {/,/^}/p' "$LOOP" > "$TMP/mem.sh"
if need "$TMP/mem.sh" "mem_avail_mb() is extracted from the shipped loop"; then
  { grep -m1 '^is_num() {' "$LOOP"; cat "$TMP/mem.sh"; } > "$TMP/memfull.sh"
  # MemAvailable 6144000 kB = 6000 MB; MemFree+Cached = 2048000+4096000 = 6144000 kB too, so one
  # expectation separates "read MemAvailable" from "read MemFree+Cached".
  printf 'MemTotal:       16000000 kB\nMemFree:         2048000 kB\nCached:          4096000 kB\nMemAvailable:    6144000 kB\n' > "$TMP/meminfo2"
  got="$(bash -c ". '$TMP/memfull.sh'; mem_avail_mb '$TMP/meminfo2'")"
  [ "$got" = "6000" ] && rc=0 || { rc=1; echo "      got '$got'"; }
  ck "shipped mem_avail_mb(): the Linux (/proc/meminfo MemAvailable) path yields available MB" $rc
  # Pre-3.14 kernels have no MemAvailable; MemFree+Cached is the documented fallback.
  printf 'MemTotal:       16000000 kB\nMemFree:         2048000 kB\nCached:          4096000 kB\n' > "$TMP/meminfo3"
  got="$(bash -c ". '$TMP/memfull.sh'; mem_avail_mb '$TMP/meminfo3'")"
  [ "$got" = "6000" ] && rc=0 || { rc=1; echo "      got '$got'"; }
  ck "shipped mem_avail_mb(): falls back to MemFree+Cached when MemAvailable is absent" $rc
  # macOS path kept: (1000 free + 2000 inactive) * 16384 B / 1 MiB = 46 (truncated).
  got="$(bash -c "vm_stat() { printf 'Mach Virtual Memory Statistics: (page size of 16384 bytes)\\nPages free: 1000.\\nPages inactive: 2000.\\n'; }; . '$TMP/memfull.sh'; mem_avail_mb '$TMP/absent-meminfo'")"
  [ "$got" = "46" ] && rc=0 || { rc=1; echo "      got '$got'"; }
  ck "shipped mem_avail_mb(): the macOS (vm_stat free+inactive) path still yields available MB" $rc
  # UNKNOWN must stay distinguishable from "0 MB available": the gate fails CLOSED on the former.
  got="$(bash -c "vm_stat() { :; }; . '$TMP/memfull.sh'; mem_avail_mb '$TMP/absent-meminfo' && echo OK || echo NONZERO")"
  [ "$got" = "NONZERO" ] && rc=0 || { rc=1; echo "      got '$got'"; }
  ck "shipped mem_avail_mb(): an unreadable probe returns non-zero (UNKNOWN), not a false 0" $rc
fi
# The regression guard that does not depend on any single spelling of the call site: a macOS-only
# kernel probe anywhere OUTSIDE the portable helpers is the bug, still shipped. `vm_stat` is included
# because it is the probe whose Linux absence was the LAST false green (the guard previously scoped
# only load/swap/mtime and so could not see it). `res_line` is exempt: it is a log-only measurement
# (`res_line` is called under "MEASURE every resource; decide nothing from it"), its memory field
# degrades to `?` off macOS, and it cannot gate dispatch — the gate's available-memory figure is
# mem_avail_mb(), whose call site is asserted separately below.
outside="$(awk '
  /^[[:space:]]*#/ {next}
  /^file_mtime_epoch\(\) \{|^load1\(\) \{|^swap_usage_mb\(\) \{|^mem_avail_mb\(\) \{|^res_line\(\) \{/ {h=1}
  h && /^\}/ {h=0; next}
  h {next}
  /sysctl -n (vm\.loadavg|vm\.swapusage)|stat -f .%m.|vm_stat/ {print FNR": "$0}
' "$LOOP")"
[ -z "$outside" ] && rc=0 || { rc=1; echo "      outside the helpers: $outside"; }
ck "no call site uses a macOS-only load/swap/mtime/memory probe outside the portable helpers" $rc
grep -q 'if _sw="$(swap_usage_mb)"; then SWFREE=' "$LOOP" && rc=0 || rc=1
ck "the dispatch gate reads swap through swap_usage_mb()" $rc
grep -q 'if MEMAVAIL_MB="$(mem_avail_mb)"; then' "$LOOP" && rc=0 || rc=1
ck "the dispatch gate reads available memory through mem_avail_mb() (not a bare vm_stat)" $rc
grep -q 'm="$(file_mtime_epoch "$f")" || continue' "$LOOP" && rc=0 || rc=1
ck "the reaper reads the log mtime through file_mtime_epoch() and skips on unknown" $rc
# install.sh renders MAX_LOAD into the plist from its OWN _NCPU and section 7 compares the two
# files' effective defaults — a divergence there would only surface ON a Linux host, so pin it here.
# ⛔ The ceiling now lives in the HELPER (agent-infra#1720: the workers read it too), so these
# properties are asserted against spawn-gate.sh. They are the same properties #1702 added to
# orch-loop.sh — a portable core count, and a coercion that PRECEDES the only read of the value it
# protects — relocated with the code, not dropped. install.sh keeps its own copy and is checked too.
grep -q 'nproc 2>/dev/null' "$INST" && rc=0 || rc=1
ck "install.sh derives _NCPU portably too (its MAX_LOAD must match the helper's on any host)" $rc
grep -q 'nproc 2>/dev/null' "$GATE" && rc=0 || rc=1
ck "spawn-gate.sh derives _NCPU portably (sysctl → nproc → getconf), not via a macOS-only probe" $rc
# ⛔ ORDER, not presence: a `_NCPU` coercion placed after `MAX_LOAD="${MAX_LOAD:-$(( _NCPU * 10 ))}"`
# — its only read — is a dead-guard. Pin that the coercion line PRECEDES the MAX_LOAD line.
_ncpu_guard_line="$(grep -n 'is_num "${_NCPU:-}"' "$GATE" | head -1 | cut -d: -f1)"
_maxload_line="$(grep -n '^MAX_LOAD="' "$GATE" | head -1 | cut -d: -f1)"
[ -n "$_ncpu_guard_line" ] && [ -n "$_maxload_line" ] && [ "$_ncpu_guard_line" -lt "$_maxload_line" ] && rc=0 || rc=1
ck "spawn-gate.sh coerces _NCPU BEFORE MAX_LOAD reads it (guard is not guard-after-use)" $rc
grep -q 'is_num "${MAX_LOAD:-}"' "$GATE" && rc=0 || rc=1
ck "spawn-gate.sh coerces a non-numeric MAX_LOAD instead of letting the load gate fail OPEN" $rc

# ── 20) a TERM'd controller EXITS; the lock/claim is not released while it is still running ──
# agent-infra#1715. The shipped trap was `trap 'rm -f "$LOCKF"' EXIT INT TERM`: for INT/TERM a
# handler that RETURNS resumes the interrupted command loop, so the documented stop path
# (`pkill -f orch-loop.sh`, README §6) deleted the lock while the controller kept sweeping,
# reviewing and merging — and the next start passed acq(), giving two autonomous merge controllers
# on one PR queue. Driven against the SHIPPED lock+trap block (extracted, not re-implemented) with a
# stub loop, so it fails on the pre-fix copy. The invariant asserted is "the lock and the process
# go away together": a released lock may only ever be observed as the process is leaving.
sed -n '/^# single-instance lock/,/^trap .rm -f "\$LOCKF".*$/p' "$LOOP" > "$TMP/lockblock.sh"
if need "$TMP/lockblock.sh" "the lock+trap block is extracted from the shipped orch-loop.sh"; then
  LK="$TMP/LK"; mkdir -p "$LK/state"
  { echo 'set -u'; echo "O='$LK'"; cat "$TMP/lockblock.sh"; echo 'while :; do sleep 0.2; done'; } > "$LK/drive.sh"
  bash -n "$LK/drive.sh" && rc=0 || rc=1
  ck "the lock-trap driver parses" $rc
  rm -f "$LK/.orch.lock"
  bash "$LK/drive.sh" & cpid=$!
  i=0; while [ ! -f "$LK/.orch.lock" ] && [ $i -lt 20 ]; do sleep 0.1; i=$((i+1)); done
  [ -f "$LK/.orch.lock" ] && rc=0 || { rc=1; echo "      the stub controller never took the lock"; }
  ck "the controller takes the single-instance lock" $rc
  [ "$(cat "$LK/.orch.lock" 2>/dev/null)" = "$cpid" ] && rc=0 || rc=1
  ck "the lock records the controller's pid" $rc

  kill -TERM "$cpid" 2>/dev/null
  released_while_alive=0; dead=0; i=0
  while [ $i -lt 100 ]; do
    if [ ! -f "$LK/.orch.lock" ]; then
      # The lock is gone: the controller must be on its way out, not resuming the loop. Poll rather
      # than sample once, so the few instructions between the EXIT trap's rm and the process dying
      # are not read as the defect.
      j=0; while [ $j -lt 10 ]; do kill -0 "$cpid" 2>/dev/null || { dead=1; break; }; sleep 0.1; j=$((j+1)); done
      [ "$dead" = "1" ] || released_while_alive=1
      break
    fi
    kill -0 "$cpid" 2>/dev/null || break
    sleep 0.05; i=$((i+1))
  done
  [ "$released_while_alive" = "0" ] && rc=0 || rc=1
  ck "a TERM releases the lock only as the controller exits, never while it keeps running" $rc
  [ "$dead" = "1" ] && rc=0 || { rc=1; kill -9 "$cpid" 2>/dev/null; }
  wait "$cpid" 2>/dev/null
  ck "a TERM'd controller actually exits" $rc
  [ -f "$LK/.orch.lock" ] && rc=1 || rc=0
  ck "the exited controller released its lock via the EXIT trap" $rc

  # The same defect on merge-one.sh's per-PR claim (§5.1b): a TERM must not free the claim while
  # the worker keeps reviewing/merging, which let a second worker spawn for the same head.
  sed -n '/^CLAIM="\$O\/state\/mergeclaim-\$PR.lock"/,/^trap .rm -f "\$CLAIM".*$/p' "$MERGE" > "$TMP/claimtrap.sh"
  if need "$TMP/claimtrap.sh" "the claim+trap block is extracted from the shipped merge-one.sh"; then
    { echo 'set -u'; echo "O='$LK'"; echo 'PR=9191'; echo "L='$LK/claim'"; cat "$TMP/claimtrap.sh"; echo 'while :; do sleep 0.2; done'; } > "$LK/mdrive.sh"
    bash -n "$LK/mdrive.sh" && rc=0 || rc=1
    ck "the claim-trap driver parses" $rc
    rm -f "$LK/state/mergeclaim-9191.lock"
    bash "$LK/mdrive.sh" & mpid=$!
    i=0; while [ ! -f "$LK/state/mergeclaim-9191.lock" ] && [ $i -lt 20 ]; do sleep 0.1; i=$((i+1)); done
    [ -f "$LK/state/mergeclaim-9191.lock" ] && rc=0 || { rc=1; echo "      merge-one.sh never took the claim"; }
    ck "the worker takes the per-PR claim" $rc
    kill -TERM "$mpid" 2>/dev/null
    m_rel=0; m_dead=0; i=0
    while [ $i -lt 100 ]; do
      if [ ! -f "$LK/state/mergeclaim-9191.lock" ]; then
        j=0; while [ $j -lt 10 ]; do kill -0 "$mpid" 2>/dev/null || { m_dead=1; break; }; sleep 0.1; j=$((j+1)); done
        [ "$m_dead" = "1" ] || m_rel=1
        break
      fi
      kill -0 "$mpid" 2>/dev/null || break
      sleep 0.05; i=$((i+1))
    done
    [ "$m_rel" = "0" ] && rc=0 || rc=1
    ck "a TERM frees the per-PR claim only as the worker exits" $rc
    [ "$m_dead" = "1" ] && rc=0 || { rc=1; kill -9 "$mpid" 2>/dev/null; }
    wait "$mpid" 2>/dev/null
    ck "a TERM'd merge-one.sh actually exits" $rc
  fi

  # The cycle sleep must be INTERRUPTIBLE: bash runs a trap only after the current foreground
  # command completes, so a bare `sleep "$CYC"` deferred the stop for up to the whole cycle
  # (measured 5.07s of a 6s sleep, vs 0.09s for `sleep … & wait $!`). Drive the SHIPPED line with
  # CYC=8 and require the stop inside 4s, so a revert to the bare sleep fails here.
  grep -m1 '^  sleep "\$CYC" & wait \$!$' "$LOOP" > "$TMP/cycsleep.sh"
  if need "$TMP/cycsleep.sh" "the interruptible cycle sleep is extracted from the shipped orch-loop.sh"; then
    { echo 'set -u'; echo "O='$LK'"; cat "$TMP/lockblock.sh"; echo 'CYC=8'; cat "$TMP/cycsleep.sh"; } > "$LK/cyc.sh"
    bash -n "$LK/cyc.sh" && rc=0 || rc=1
    ck "the cycle-sleep driver parses" $rc
    # Non-vacuity: the driver must OWN the lock (a leftover lock would make it refuse the lock and
    # exit immediately, so the "stopped inside 4s" check below would pass without ever sleeping).
    rm -f "$LK/.orch.lock"
    bash "$LK/cyc.sh" & ypid=$!
    i=0; while [ ! -f "$LK/.orch.lock" ] && [ $i -lt 20 ]; do sleep 0.1; i=$((i+1)); done
    [ "$(cat "$LK/.orch.lock" 2>/dev/null)" = "$ypid" ] && rc=0 || { rc=1; echo "      lock='$(cat "$LK/.orch.lock" 2>/dev/null)' != driver pid $ypid"; }
    ck "the cycle-sleep driver acquired the lock (it really is in the CYC sleep)" $rc
    sleep 0.5
    kill -0 "$ypid" 2>/dev/null && rc=0 || rc=1
    ck "the cycle-sleep driver is still sleeping when TERM arrives" $rc
    kill -TERM "$ypid" 2>/dev/null
    ydead=0; i=0
    while [ $i -lt 40 ]; do kill -0 "$ypid" 2>/dev/null || { ydead=1; break; }; sleep 0.1; i=$((i+1)); done
    [ "$ydead" = "1" ] && rc=0 || { rc=1; kill -9 "$ypid" 2>/dev/null; }
    wait "$ypid" 2>/dev/null
    ck "the shipped cycle sleep is interrupted by TERM inside CYC (stopped <4s, not 8s)" $rc
  fi

  # Reclamation must be REAL, not silently skipped: a lock whose PID is dead is reclaimed by the
  # next start (the SIGKILL/reboot path), while one held by a LIVE pid is refused. Without the
  # negative control, "reclaimed" and "silently overwritten" would be the same test. Deliberately
  # LAST in this section: the live-pid case intentionally leaves a lock in place.
  { echo 'set -u'; echo "O='$LK'"; cat "$TMP/lockblock.sh"; echo 'echo ACQUIRED'; } > "$LK/acquire.sh"
  bash -n "$LK/acquire.sh" && rc=0 || rc=1
  ck "the stale-lock driver parses" $rc
  printf '999999\n' > "$LK/.orch.lock"
  out="$(bash "$LK/acquire.sh" 2>&1)"
  case "$out" in *ACQUIRED*) rc=0;; *) rc=1; echo "      got '$out'";; esac
  ck "a stale lock (dead pid) is reclaimed by the next start" $rc
  printf '%s\n' "$$" > "$LK/.orch.lock"
  out="$(bash "$LK/acquire.sh" 2>&1)"
  case "$out" in *ACQUIRED*) rc=1; echo "      a live lock was stolen";; *) rc=0;; esac
  ck "a lock held by a LIVE pid is NOT stolen (reclamation is not a silent overwrite)" $rc
fi

echo
echo "==> $pass passed, $fail failed"
[ "$fail" -eq 0 ]
