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
awk '/^python3 - "\$O" <<.PY.$/{f=1;next} /^PY$/{f=0} f' "$REFRESH" > "$R/reap.py"
if need "$R/reap.py" "the reaper is extracted from the shipped refresh-candidates.sh"; then
# the extracted body shells out to tmux; export the fake so the child sees it
cat > "$R/tmux" <<'SH'
#!/bin/bash
printf 'lane-7777\nmerge-100\n'
SH
chmod +x "$R/tmux"
( PATH="$R:$PATH" python3 "$R/reap.py" "$R" ) > "$TMP/reap.out" 2>&1
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
fi
unset -f tmux

# ── 5b) the shipped refresh loop never blacklists a candidate it did not pre-flight ─
# `echo "$N" >> checked.txt` sat BEFORE `n=$((n+1)); [ "$n" -gt "$LIMIT" ] && break`, so the
# (LIMIT+1)-th candidate was recorded as checked and then the loop broke without ever running
# collision_preflight.py for it. checked.txt suppresses that issue forever and the §7.1 reaper
# only un-blacklists dispatched.txt entries — so one dispatchable issue was lost per refresh.
awk '/^n=0$/{f=1} f{print} /^done < /{f=0}' "$REFRESH" > "$TMP/refreshloop.sh"
if need "$TMP/refreshloop.sh" "the refresh loop body is extracted from the shipped refresh-candidates.sh"; then
  RL="$TMP/refresh"; mkdir -p "$RL/fakesc/.venv/bin"
  printf '10\n20\n30\n40\n50\n' > "$RL/.pool"
  : > "$RL/checked.txt"; : > "$RL/candidates.txt"; : > "$RL/dispatched.txt"
  cat > "$RL/fakesc/.venv/bin/python" <<'PY'
#!/bin/bash
# stand-in for the scout's venv python: every candidate pre-flights CLEAN
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
fi

# ── 6) install.sh renders correctly, including values sed would mangle ───────────
# Runs the real installer with a hostile $O containing '&' (in sed, & expands to the matched
# text, so `O=/tmp/a&b` used to render the path as /tmp/a__O__b — a VALID plist pointing at
# the wrong paths, which plutil -lint cannot catch).
SB="$TMP/sb"; mkdir -p "$SB/Library/LaunchAgents" "$SB/.config" "$SB/.pi/agent/scripts"
ln -s "$HOME/.config/gh" "$SB/.config/gh" 2>/dev/null || true
[ -f "$HOME/.pi/agent/scripts/record-review.sh" ] && ln -s "$HOME/.pi/agent/scripts/record-review.sh" "$SB/.pi/agent/scripts/record-review.sh"
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
ck "the repo-root path selftest.sh uses resolves to a git checkout" $rc

# ── 7) in-script defaults agree with install.sh's defaults ───────────────────────
# A limit documented in README §4 and rendered by install.sh must not be silently different in
# orch-loop.sh — MAX_REVIEW was 2 in the loop and 4 everywhere else.
rc=0
for v in MAX_LOAD MAX_LANES MAX_REVIEW MERGE_PRIORITY_AT MAX_FIX MAX_FIX_TRIES MIN_SWAP_FREE BATCH CYC; do
  a="$(grep -oE "^$v=\"\\\$\{$v:-[0-9]+\}\"" "$LOOP" | grep -oE '[0-9]+' | head -1)"
  b="$(grep -oE "^$v=\"\\\$\{$v:-[0-9]+\}\"" "$INST" | grep -oE '[0-9]+' | head -1)"
  [ -n "$a" ] && [ "$a" = "$b" ] || { echo "      $v: orch-loop=${a:-?} install=${b:-?}"; rc=1; }
done
ck "orch-loop.sh defaults match install.sh for all 9 limits" $rc

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
sed -n '/^mp_reason() {/,/^}/p;/^review_slots() {/,/^}/p' "$LOOP" > "$TMP/mp.sh"
if need "$TMP/mp.sh" "mp_reason()/review_slots() are extracted from the shipped loop"; then
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
sed -n '/^reap_stale_workers() {/,/^}/p' "$LOOP" > "$TMP/reapw.sh"
if need "$TMP/reapw.sh" "reap_stale_workers() is extracted from the shipped loop"; then
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
# the agent invocation is wall-clock bounded, portably (macOS has no timeout/gtimeout)
sed -n '/^run_agent() {/,/^}/p' "$MERGE" > "$TMP/runagent.sh"
if need "$TMP/runagent.sh" "run_agent() is extracted from the shipped merge-one.sh"; then
  # NOTE: the heredoc must EXPAND $TMP (unquoted) while leaving the runtime substitutions for the
  # script itself. A quoted heredoc left the source path literal, so run_agent never got defined and
  # the check passed on "command not found" — the exact vacuity this file exists to prevent.
  cat > "$TMP/ra.sh" <<SH
pi() { sleep 60; }
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

echo
echo "==> $pass passed, $fail failed"
[ "$fail" -eq 0 ]
