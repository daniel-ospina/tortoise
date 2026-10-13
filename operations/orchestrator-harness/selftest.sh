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
_LIMITS="MAX_LOAD MAX_LANES MAX_REVIEW MERGE_PRIORITY_AT MAX_FIX MAX_FIX_TRIES MIN_SWAP_FREE MIN_MEM_FREE_MB BATCH CYC"
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
_da="$(eff_defaults unset "$LOOP"; eff_defaults env "$LOOP")"
_db="$(eff_defaults unset "$INST"; eff_defaults env "$INST")"
if [ -n "$_da" ] && [ "$_da" = "$_db" ]; then
  echo "      effective: $(printf '%s' "$_da" | tr '\n' ' ')"
else
  echo "      effective defaults DIFFER:"
  printf '%s\n' "$_da" > "$TMP/da.txt"; printf '%s\n' "$_db" > "$TMP/db.txt"
  diff "$TMP/da.txt" "$TMP/db.txt" 2>/dev/null | sed 's/^/        /'
  rc=1
fi
ck "orch-loop.sh and install.sh resolve the same effective defaults for all 10 limits" $rc

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
grep -m1 '^can_spawn() {' "$LOOP" > "$TMP/canspawn.sh"
if need "$TMP/canspawn.sh" "can_spawn() is extracted from the shipped loop"; then
  MAX_LOAD=16
  . "$TMP/canspawn.sh"
  L=0;   can_spawn && rc=0 || rc=1; ck "shipped can_spawn(): load 0 < 16 -> spawn allowed" $rc
  L=15;  can_spawn && rc=0 || rc=1; ck "shipped can_spawn(): load 15 < 16 -> spawn allowed" $rc
  L=16;  can_spawn && rc=1 || rc=0; ck "shipped can_spawn(): load 16 -> spawn held back" $rc
  L=221; can_spawn && rc=1 || rc=0; ck "shipped can_spawn(): load 221 (post-boot storm) -> held back" $rc
  L="";  can_spawn && rc=0 || rc=1; ck "shipped can_spawn(): unset load defaults to 0 (documented fail-open)" $rc
fi
# Assert each of the four spawn SITES, not a count — a count is satisfied by decoy comments,
# which is exactly how an earlier version of this check passed against a stripped build.
fixer_sites=$(grep -F "grep -c '^fix-')" "$LOOP" | grep -cF can_spawn)
merge_site=$(grep -F "grep -c '^merge-')" "$LOOP" | grep -cF can_spawn)
review_site=$(grep -cF '&& can_spawn \' "$LOOP")
[ "$fixer_sites" = "2" ] && rc=0 || rc=1
ck "both fixer spawn sites (conflict + CI-red) carry the load gate" $rc
[ "$merge_site" = "1" ] && rc=0 || rc=1
ck "the merge re-review spawn carries the load gate" $rc
[ "$review_site" = "1" ] && rc=0 || rc=1
ck "the review-fix spawn carries the load gate" $rc
grep -q 'gh pr merge "\$PR" --repo "\$OR" --squash >>"\$LOG"' "$LOOP" && rc=0 || rc=1
ck "the cheap merge path stays ungated" $rc

# ── refresh-candidates.sh: the pool is ordered by SEVERITY, not by issue number ─────
# #8465: the pool was sorted `sort -n -u` over the NUMBERS ALONE — oldest first — so 238
# pool-eligible P2-moderate sat ahead of 20 P0-blocker and the P0s were unreachable. Extract the
# REAL pipeline from the shipped script and drive it with a fixture; re-implementing the sort here
# would pass on the broken copy too. Verified non-vacuous: the pre-fix extraction yields `sort -n -u`,
# whose output is `0 7951 1 8039 2 1120` and fails this assertion.
POOL_SORT="$(grep -E '^\} \| sort ' "$REFRESH" | sed -e 's/^} | //' -e 's/ > "\$O\/\.pool"$//')"
if [ -n "$POOL_SORT" ]; then
  pool_got="$(printf '2 1120\n1 8039\n0 7951\n2 3147\n1 7936\n0 7846\n2 3329\n1 8039\n0 3846\n' \
              | sh -c "$POOL_SORT" | tr '\n' ' ' | sed 's/ *$//')"
  [ "$pool_got" = "3846 7846 7951 7936 8039 1120 3147 3329" ] && rc=0 || rc=1
else rc=1; fi
ck "refresh-candidates.sh orders the pool severity-first (P0<P1<P2), age second, dedup by number" $rc

# The backlog file is consumed top-down, so its ORDER IS the priority — a numeric re-sort put any
# appended P0 at the BACK (#8465). The shipped script must dedupe WITHOUT sorting.
if [ -s "$REFRESH" ] && ! grep -qE '^sort -n -u -o "\$O/candidates\.txt"' "$REFRESH"; then rc=0; else rc=1; fi
ck "refresh-candidates.sh dedupes the backlog without re-sorting it by issue number" $rc

echo
echo "==> $pass passed, $fail failed"
[ "$fail" -eq 0 ]
