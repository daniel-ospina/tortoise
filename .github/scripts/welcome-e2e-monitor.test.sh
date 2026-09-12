#!/usr/bin/env bash
# welcome-e2e-monitor.test.sh — self-check for
# .github/scripts/welcome-e2e-monitor.sh (#801 / #2706 / #2850).
#
# Run: bash .github/scripts/welcome-e2e-monitor.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs
# `gh` on PATH. No network, no GitHub.
#
# Coverage:
#   1. no open issue            → files exactly ONE issue with the stable title
#   2. a machine-authored open issue → comments, files NOTHING (dedupe)
#   3. the dedupe search carries author:app/github-actions
#   4. a FORGED-TITLE human-authored issue → never commented on, a fresh
#      machine issue is filed instead (the #3064 round-2 P1)
#   5. a forged item alongside a real machine item → adopts the machine one
#   6. a non-Bot `user.type` but the bot login → still adopted (belt-and-braces)
#   6b/6c. ANOTHER App's bot (`renovate[bot]`, `dependabot[bot]`) → NOT adopted
#      (round 4, P3-9: `user.type == "Bot"` is not a security boundary)
#   7. a failed search → exit 1, files NOTHING, comments on NOTHING
#   8. a non-numeric id → refuse loudly
#   9. no GH_TOKEN → fail closed before contacting GitHub
#  10. the created issue carries the bug label and the run link
#
# The forged-title case is the regression: it FAILS on the round-1 inline
# title-only dedupe and passes only with the author constraint.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONITOR="$SCRIPT_DIR/welcome-e2e-monitor.sh"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_eq() { # <actual> <expected> <label>
  if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (got '$1', want '$2')"; fi
}
assert_contains() { # <haystack> <needle> <label>
  case "$1" in
    *"$2"*) ok "$3" ;;
    *) bad "$3 (missing '$2')" ;;
  esac
}
assert_not_contains() { # <haystack> <needle> <label>
  case "$1" in
    *"$2"*) bad "$3 (unexpectedly found '$2')" ;;
    *) ok "$3" ;;
  esac
}

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
BIN="$FIX/bin"
mkdir -p "$BIN"
export STUB_TMP="$FIX/stub"
mkdir -p "$STUB_TMP"

# ── stub: gh ────────────────────────────────────────────────────────────────
cat > "$BIN/gh" <<'GH_EOF'
#!/usr/bin/env bash
# Handles exactly the shapes the monitor uses:
#   gh api "search/issues?q=…&per_page=5"            (GET)
#   gh api "repos/O/R/issues" --method POST -f …     (create)
#   gh api "repos/O/R/issues/N/comments" --method POST -f body=… (comment)
DEFAULT_ITEMS_JSON='{"items":[]}'
[ "${1:-}" = "api" ] || { echo "GH unexpected: $*" >&2; exit 1; }
path="${2:-}"; method="GET"
shift 2 || true
fields=()
while [ $# -gt 0 ]; do
  case "$1" in
    --method) method="$2"; shift 2 ;;
    --input)  shift ;;
    --jq)     shift 2 ;;
    -f)       fields+=("$2"); shift 2 ;;
    *)        shift ;;
  esac
done
echo "GH $method ${path%%\?*}" >> "$STUB_TMP/calls.log"
case "$path" in
  search/issues*)
    echo "GH-Q $path" >> "$STUB_TMP/calls.log"
    [ "${STUB_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: search failed" >&2; exit 1; }
    printf '%s' "${STUB_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}" ;;
  */comments)
    [ "${STUB_COMMENT_FAIL:-0}" = "1" ] && { echo "gh: comment failed" >&2; exit 1; }
    printf '%s\n' "${fields[@]}" > "$STUB_TMP/comment.txt"
    printf '{}' ;;
  */issues)
    [ "${STUB_CREATE_FAIL:-0}" = "1" ] && { echo "gh: create failed" >&2; exit 1; }
    printf '%s\n' "${fields[@]}" > "$STUB_TMP/created.txt"
    printf '{"number":900}' ;;
  *) printf '{}' ;;
esac
exit 0
GH_EOF
chmod +x "$BIN/gh"
export PATH="$BIN:$PATH"

export GH_TOKEN="test-token"
export GITHUB_REPOSITORY="daniel-ospina/tortoise"
export GITHUB_SERVER_URL="https://github.com"
export GITHUB_RUN_ID="123456"

reset_case() {
  : > "$STUB_TMP/calls.log"
  rm -f "$STUB_TMP/created.txt" "$STUB_TMP/comment.txt"
  unset STUB_SEARCH_JSON STUB_SEARCH_FAIL STUB_CREATE_FAIL STUB_COMMENT_FAIL || true
  export GH_TOKEN="test-token"
}

run_monitor() { # -> RC, OUT (stderr+stdout)
  local so
  so="$("$MONITOR" 2>&1)"
  RC=$?
  OUT="$so"
}

count_calls() { grep -c -- "$1" "$STUB_TMP/calls.log" 2>/dev/null || true; }
created()  { [ -f "$STUB_TMP/created.txt" ] && cat "$STUB_TMP/created.txt" || echo ''; }
commented(){ [ -f "$STUB_TMP/comment.txt" ] && cat "$STUB_TMP/comment.txt" || echo ''; }

# A search result item as PRODUCTION returns it: authored by the GitHub Actions
# bot. Every fixture that expects adoption MUST carry this author; use a
# different author deliberately for the hijack test.
search_json() { # <number> [title] [login] [type]
  local n="${1:-42}" t="${2:-monitor}" l="${3:-github-actions[bot]}" ty="${4:-Bot}"
  printf '{"items":[{"number":%s,"title":"%s","user":{"login":"%s","type":"%s"}}]}' \
    "$n" "$t" "$l" "$ty"
}

echo "welcome-e2e-monitor.test.sh — #801/#2706/#2850 taxonomy"
echo

# ── 1: no open issue → exactly one issue, stable title, bug label ───────────
reset_case
run_monitor
assert_eq "$RC" "0" "no open issue → exit 0"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "1" "no open issue → exactly ONE issue filed"
assert_contains "$(created)" "live-signup-monitor: welcome-e2e failing" "the filed title is the STABLE dedupe key (no run id)"
assert_contains "$(created)" "labels[]=bug" "the filed issue keeps its bug label"
assert_contains "$(created)" "actions/runs/123456" "the filed body carries the failing run link (#2706)"

# ── 2: a machine-authored open issue → comment, never duplicate ─────────────
reset_case
export STUB_SEARCH_JSON="$(search_json 42)"
run_monitor
assert_eq "$RC" "0" "machine-authored open issue → exit 0"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "0" "machine-authored open issue → NO duplicate filed"
assert_eq "$(count_calls 'GH POST repos/.*/issues/42/comments$')" "1" "machine-authored open issue → commented on #42"
assert_contains "$(commented)" "Still failing" "the comment names the state"
assert_contains "$(commented)" "actions/runs/123456" "the comment carries the failing run link"

# ── 3: the dedupe search carries the machine-author constraint ──────────────
reset_case
run_monitor
assert_eq "$(count_calls 'GH-Q.*author%3Aapp%2Fgithub-actions')" "1" "the dedupe search carries the author:app/github-actions constraint"
assert_eq "$(count_calls 'GH-Q.*is%3Aopen')" "1" "the dedupe search only considers OPEN issues"

# ── 4: a FORGED-TITLE human-authored issue is never adopted (#3064 P1) ──────
# PUBLIC repo: any account can open an issue with our title. Treating it as our
# state carrier both silences the monitor's own tracker AND hands an outsider a
# comment thread it can drive. It must be ignored and a FRESH machine issue
# filed. This case FAILS on the round-1 title-only dedupe.
reset_case
export STUB_SEARCH_JSON="$(search_json 666 'live-signup-monitor: welcome-e2e failing' 'attacker' 'User')"
run_monitor
assert_eq "$RC" "0" "forged title (human) → exit 0"
assert_eq "$(count_calls 'GH POST repos/.*/issues/666/comments$')" "0" "forged title → NEVER commented on (no stolen thread)"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "1" "forged title → a FRESH machine issue is filed instead"
assert_contains "$(created)" "live-signup-monitor: welcome-e2e failing" "forged title → the fresh issue carries the real title"
assert_contains "$OUT" "NONE was authored by" "forged title → logged loudly"

# ── 5: a forged look-alike beside a REAL machine issue → adopt the real one ─
# The filter must not simply bail on any non-machine match; it must skip it and
# still find ours (otherwise a squatter could permanently blind the dedupe).
reset_case
export STUB_SEARCH_JSON='{"items":[{"number":666,"title":"forged","user":{"login":"attacker","type":"User"}},{"number":42,"title":"real","user":{"login":"github-actions[bot]","type":"Bot"}}]}'
run_monitor
assert_eq "$(count_calls 'GH POST repos/.*/issues/42/comments$')" "1" "a forged look-alike beside a real one → the REAL #42 is adopted"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "0" "a forged look-alike beside a real one → no duplicate filed"
assert_eq "$(count_calls 'GH POST repos/.*/issues/666/comments$')" "0" "a forged look-alike → never commented on"

# ── 6: belt-and-braces — the bot LOGIN alone is enough ──────────────────────
reset_case
export STUB_SEARCH_JSON="$(search_json 43 'monitor' 'github-actions[bot]' 'User')"
run_monitor
assert_eq "$(count_calls 'GH POST repos/.*/issues/43/comments$')" "1" "user.type User but login github-actions[bot] → still adopted (the login is reserved)"

# ── 6b: another App's bot is NOT our bot (round 4, P3-9) ────────────────────
# `renovate[bot]`/`dependabot[bot]` have user.type == "Bot", so the old
# OR-guard adopted them. The reserved LOGIN is the boundary. This case FAILS on
# the round-3 code (it commented on #666).
reset_case
export STUB_SEARCH_JSON="$(search_json 666 'live-signup-monitor: welcome-e2e failing' 'renovate[bot]' 'Bot')"
run_monitor
assert_eq "$RC" "0" "renovate[bot] look-alike → exit 0"
assert_eq "$(count_calls 'GH POST repos/.*/issues/666/comments$')" "0" "renovate[bot] look-alike → NEVER commented on (type=Bot is not the boundary)"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "1" "renovate[bot] look-alike → a FRESH machine issue is filed instead"
assert_contains "$OUT" "NONE was authored by" "renovate[bot] look-alike → logged loudly"

# ── 6c: dependabot[bot] is likewise not ours ────────────────────────────────
reset_case
export STUB_SEARCH_JSON="$(search_json 667 'live-signup-monitor: welcome-e2e failing' 'dependabot[bot]' 'Bot')"
run_monitor
assert_eq "$(count_calls 'GH POST repos/.*/issues/667/comments$')" "0" "dependabot[bot] look-alike → NEVER commented on"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "1" "dependabot[bot] look-alike → a fresh machine issue is filed"

# ── 7: a failed search refuses to file (never duplicate) ────────────────────
reset_case
export STUB_SEARCH_FAIL=1
run_monitor
assert_eq "$RC" "1" "search failure → exit 1 (fail loud, never a silent deaf monitor)"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "0" "search failure → files NOTHING (never a blind duplicate)"
assert_eq "$(count_calls 'GH POST repos/.*/comments$')" "0" "search failure → comments on NOTHING"
assert_contains "$OUT" "refusing to file a possible duplicate" "search failure → says exactly why"

# ── 8: a non-numeric id is refused ──────────────────────────────────────────
reset_case
export STUB_SEARCH_JSON='{"items":[{"number":"not-a-number","title":"x","user":{"login":"github-actions[bot]","type":"Bot"}}]}'
run_monitor
assert_eq "$RC" "1" "a non-numeric id → exit 1"
assert_eq "$(count_calls 'GH POST')" "0" "a non-numeric id → nothing is filed or commented"

# ── 9: no GH_TOKEN → fail closed before contacting GitHub ──────────────────
reset_case
unset GH_TOKEN
run_monitor
assert_eq "$RC" "1" "missing GH_TOKEN → exit 1 (fail closed)"
assert_contains "$OUT" "deaf monitor" "missing GH_TOKEN → names the deaf-monitor risk"
assert_eq "$(count_calls 'GH ')" "0" "missing GH_TOKEN → GitHub is never contacted"

# ── 10: a failed comment fails the step loudly (never silent) ───────────────
reset_case
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_COMMENT_FAIL=1
run_monitor
assert_eq "$RC" "1" "a failed comment → exit 1 (the #2140 deaf-monitor class)"
assert_not_contains "$OUT" "commented instead of filing" "a failed comment → no success notice is printed"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "welcome-e2e-monitor.test.sh: $PASS passed, 0 failed ✅"
  exit 0
fi
echo "welcome-e2e-monitor.test.sh: $PASS passed, $FAIL FAILED ❌"
exit 1
