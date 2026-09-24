#!/usr/bin/env bash
# auto-file-issue.test.sh — self-check for
# .github/scripts/auto-file-issue.sh (#3907).
#
# Run: bash .github/scripts/auto-file-issue.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs `gh`
# on PATH. No network, no GitHub.
#
# Coverage (#3907 acceptance in brackets):
#   1. no open issue → exactly ONE issue filed, titled with the STABLE key, no
#      run id in the title [a first-time fault still files]
#   2. a machine-authored open issue → comments "Recurrence #1", files NOTHING
#      [two firings ⇒ one issue with two recorded occurrences]
#   3. a second recurrence (one occurrence comment already on the issue) →
#      comments "Recurrence #2" [the counter is visible, not hidden]
#   4. the dedupe search carries author:app/github-actions + is:open
#   5. the occurrence count PAGES past the first 100 comments (the counting walk
#      is live, not a single-page stub that always reads 0)
#   6. a FORGED-TITLE human-authored issue is never commented on and a fresh
#      machine issue is filed instead (the #3064 class, on the shared path)
#   7. another App's bot (`renovate[bot]`) → NOT adopted (`type == "Bot"` is not
#      a boundary)
#   8. a machine-authored WORD-MATCH look-alike (title contains the key but is
#      not exactly it) → NOT adopted; a fresh issue is filed
#   9. a failed search → exit 1, files NOTHING, comments on NOTHING [never a
#      blind duplicate]
#  10. a failed comment → exit 1 (the #2140 deaf-monitor class)
#  11. a failed create → exit 1
#  12. a non-numeric id → refuse loudly, touch nothing
#  13. no token → fail closed before contacting GitHub
#  14. the filed body is the body FILE's content (not the title)
#  15. AUTO_FILE_LIB_ONLY=1 sources the lib without executing main
#
# Case 2 is the #3907 acceptance pin: it FAILS on the old behaviour (a duplicate
# issue per run) and on the silent-drop behaviour (no comment at all).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/auto-file-issue.sh"

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
assert_match() { # <haystack> <regex> <label>
  if printf '%s' "$1" | grep -qE -- "$2"; then ok "$3"; else bad "$3 (no match: $2)"; fi
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
# Handles exactly the shapes the helper uses:
#   gh api "search/issues?q=…&per_page=20"                 (GET search)
#   gh api "repos/O/R/issues/N/comments?per_page=100&page=P" (GET comments)
#   gh api "repos/O/R/issues/N/comments" --method POST -f body=…  (comment)
#   gh api "repos/O/R/issues" --method POST -f …           (create)
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
# NB: the default lives in a variable — a literal JSON object inside
# ${VAR:-{...}} is mis-parsed (the first '}' closes the expansion).
DEFAULT_ITEMS_JSON='{"items":[]}'
echo "GH ${method} ${path}" >> "$STUB_TMP/calls.log"
case "$path" in
  search/issues*)
    echo "GH-Q ${path}" >> "$STUB_TMP/calls.log"
    [ "${STUB_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: search failed" >&2; exit 1; }
    printf '%s' "${STUB_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}" ;;
  */comments\?*)
    # Paged occurrence counting: page 2 (if requested) is a distinct fixture.
    page="1"
    case "$path" in *page=2*) page="2" ;; *page=3*) page="3" ;; esac
    echo "GH-C page=${page}" >> "$STUB_TMP/calls.log"
    [ "${STUB_COMMENTS_FAIL:-0}" = "1" ] && { echo "gh: comments failed" >&2; exit 1; }
    case "$page" in
      2) printf '%s' "${STUB_COMMENTS_JSON_P2:-[]}" ;;
      3) printf '%s' "${STUB_COMMENTS_JSON_P3:-[]}" ;;
      *) printf '%s' "${STUB_COMMENTS_JSON:-[]}" ;;
    esac ;;
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

export GITHUB_REPOSITORY="daniel-ospina/tortoise"
export GITHUB_SERVER_URL="https://github.com"
export GITHUB_RUN_ID="123456"
export GH_TOKEN="test-token"

BODY_FILE="$FIX/body.md"
printf 'Weekly check found orphaned prod rows. Counts: teams=3.\n' > "$BODY_FILE"
STABLE_TITLE="e2e-live reconcile: orphaned prod rows detected"

reset_case() {
  : > "$STUB_TMP/calls.log"
  rm -f "$STUB_TMP/created.txt" "$STUB_TMP/comment.txt"
  unset STUB_SEARCH_JSON STUB_SEARCH_FAIL STUB_CREATE_FAIL STUB_COMMENT_FAIL \
        STUB_COMMENTS_JSON STUB_COMMENTS_JSON_P2 STUB_COMMENTS_JSON_P3 \
        STUB_COMMENTS_FAIL || true
  export GH_TOKEN="test-token"
}

run_helper() { # -> RC, OUT
  local so
  so="$("$HELPER" file --title "$STABLE_TITLE" --body-file "$BODY_FILE" 2>&1)"
  RC=$?
  OUT="$so"
}

count_calls() { grep -c -- "$1" "$STUB_TMP/calls.log" 2>/dev/null || true; }
created()   { [ -f "$STUB_TMP/created.txt" ] && cat "$STUB_TMP/created.txt" || echo ''; }
commented() { [ -f "$STUB_TMP/comment.txt" ] && cat "$STUB_TMP/comment.txt" || echo ''; }

# A search result item as production returns it: authored by the GitHub Actions
# bot. Every fixture that expects adoption MUST carry this author and the EXACT
# title; use a different author/title deliberately elsewhere.
search_json() { # <number> [title] [login] [type]
  local n="${1:-42}" t="${2:-$STABLE_TITLE}" l="${3:-github-actions[bot]}" ty="${4:-Bot}"
  printf '{"items":[{"number":%s,"title":"%s","user":{"login":"%s","type":"%s"}}]}' \
    "$n" "$t" "$l" "$ty"
}
# A page of `n` comments, `with_marker` of which carry the occurrence marker.
comments_page() { # <n> <with_marker>
  local total="$1" marked="$2" i out="["
  for ((i = 0; i < total; i++)); do
    if [ "$i" -lt "$marked" ]; then b="🔁 Recurrence #$((i + 1)) <!-- auto-file-occurrence -->"; else b="a human comment"; fi
    [ "$i" -gt 0 ] && out+=","
    out+="{\"body\":\"${b}\"}"
  done
  printf '%s]' "$out"
}

echo "auto-file-issue.test.sh — #3907 taxonomy"
echo

# ── 1: no open issue → exactly one issue, stable title, bug label ───────────
reset_case
run_helper
assert_eq "$RC" "0" "1. no open issue → exit 0"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "1" "1. no open issue → exactly ONE issue filed"
assert_eq "$(count_calls 'GH POST repos/.*/comments')" "0" "1. a first-time fault comments on nothing"
assert_contains "$(created)" "$STABLE_TITLE" "1. the filed title is the STABLE dedupe key (no run id)"
assert_not_contains "$(created)" "123456" "1. the stable key carries NO run id (the #2706 defect)"
assert_contains "$(created)" "labels[]=bug" "1. the filed issue keeps its label"
assert_contains "$(created)" "teams=3" "1. the body is the body FILE's content"

# ── 2: a machine-authored open issue → recur, do not duplicate ──────────────
reset_case
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_COMMENTS_JSON='[]'
run_helper
assert_eq "$RC" "0" "2. machine-authored open issue → exit 0"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "0" "2. NO duplicate issue is filed (#3907 acceptance)"
assert_eq "$(count_calls 'GH POST repos/.*/issues/42/comments$')" "1" "2. the existing issue #42 is commented on"
assert_contains "$(commented)" "Recurrence #1" "2. the first recurrence is numbered and VISIBLE (not silently dropped)"
assert_contains "$(commented)" "auto-file-occurrence" "2. the comment carries the counting marker"

# ── 3: a second recurrence → #2 (the counter comes from the issue) ──────────
reset_case
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_COMMENTS_JSON="$(comments_page 1 1)"
run_helper
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "0" "3. a second recurrence files no duplicate"
assert_contains "$(commented)" "Recurrence #2" "3. the recurrence counter advances to #2"
assert_contains "$(commented)" "actions/runs/123456" "3. the recurrence records WHICH run observed it"

# ── 4: the search carries the machine-author constraint ─────────────────────
reset_case
run_helper
assert_eq "$(count_calls 'GH-Q.*author%3Aapp%2Fgithub-actions')" "1" "4. the dedupe search carries author:app/github-actions"
assert_eq "$(count_calls 'GH-Q.*is%3Aopen')" "1" "4. the dedupe search only considers OPEN issues"

# ── 5: the occurrence count PAGES past the first 100 comments ──────────────
# A stub that only ever answers page 1 makes the counting walk look correct
# while it silently stops at 100. Page 1 is FULL (100 items, 100 markers), so a
# live walk must fetch page 2 and take its 3 markers → next is #104.
reset_case
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_COMMENTS_JSON="$(comments_page 100 100)"
export STUB_COMMENTS_JSON_P2="$(comments_page 3 3)"
run_helper
assert_eq "$(count_calls 'GH-C page=2')" "1" "5. a FULL first page triggers the second page fetch"
assert_contains "$(commented)" "Recurrence #104" "5. the count spans BOTH pages (100 + 3 + 1)"

# ── 6: a FORGED-TITLE human-authored issue is never adopted ────────────────
# PUBLIC repo: any account can open an issue with our title. Adopting it silences
# the monitor's own tracker AND hands an outsider the comment thread.
reset_case
export STUB_SEARCH_JSON="$(search_json 666 "$STABLE_TITLE" 'attacker' 'User')"
run_helper
assert_eq "$RC" "0" "6. forged title (human) → exit 0"
assert_eq "$(count_calls 'GH POST repos/.*/issues/666/comments$')" "0" "6. forged title → NEVER commented on"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "1" "6. forged title → a FRESH machine issue is filed instead"
assert_contains "$OUT" "NONE was an EXACT machine-authored" "6. forged title → logged loudly"

# ── 7: another App's bot is NOT our bot ─────────────────────────────────────
# `renovate[bot]`/`dependabot[bot]` are user.type == "Bot"; the reserved LOGIN is
# the boundary, and exact-title + login are BOTH required.
reset_case
export STUB_SEARCH_JSON="$(search_json 666 "$STABLE_TITLE" 'renovate[bot]' 'Bot')"
run_helper
assert_eq "$(count_calls 'GH POST repos/.*/issues/666/comments$')" "0" "7. renovate[bot] look-alike → NEVER commented on"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "1" "7. renovate[bot] look-alike → a fresh machine issue is filed"

# ── 8: a WORD-MATCH look-alike is not the key ──────────────────────────────
# `in:title "…"` is a word match, so a machine issue whose title CONTAINS the
# key but is not equal to it must not be adopted (it is a different finding).
reset_case
export STUB_SEARCH_JSON="$(search_json 77 "$STABLE_TITLE (superseded)")"
run_helper
assert_eq "$(count_calls 'GH POST repos/.*/issues/77/comments$')" "0" "8. a word-match look-alike → NOT commented on"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "1" "8. a word-match look-alike → a fresh exact-key issue is filed"

# ── 9: a failed search refuses to file (never duplicate) ───────────────────
reset_case
export STUB_SEARCH_FAIL=1
run_helper
assert_eq "$RC" "1" "9. search failure → exit 1 (a red run, never a blind duplicate)"
assert_eq "$(count_calls 'GH POST repos/.*/issues$')" "0" "9. search failure → files NOTHING"
assert_eq "$(count_calls 'GH POST repos/.*/comments')" "0" "9. search failure → comments on NOTHING"
assert_contains "$OUT" "refusing to file a possible duplicate" "9. search failure → says exactly why"

# ── 10: a failed comment fails the step loudly ─────────────────────────────
reset_case
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_COMMENT_FAIL=1
run_helper
assert_eq "$RC" "1" "10. a failed comment → exit 1 (the #2140 deaf-monitor class)"
assert_not_contains "$OUT" "recorded recurrence" "10. a failed comment prints no success notice"

# ── 11: a failed create fails the step loudly ──────────────────────────────
reset_case
export STUB_CREATE_FAIL=1
run_helper
assert_eq "$RC" "1" "11. a failed create → exit 1 (never a silently unfiled finding)"

# ── 12: a non-numeric id is refused ────────────────────────────────────────
reset_case
export STUB_SEARCH_JSON='{"items":[{"number":"not-a-number","title":"'"$STABLE_TITLE"'","user":{"login":"github-actions[bot]","type":"Bot"}}]}'
run_helper
assert_eq "$RC" "1" "12. a non-numeric id → exit 1"
assert_eq "$(count_calls 'GH POST')" "0" "12. a non-numeric id → nothing is filed or commented"

# ── 13: no token → fail closed before contacting GitHub ────────────────────
reset_case
unset GH_TOKEN
run_helper
assert_eq "$RC" "1" "13. missing token → exit 1 (fail closed)"
assert_contains "$OUT" "deaf monitor" "13. missing token → names the deaf-monitor risk"
assert_eq "$(count_calls 'GH ')" "0" "13. missing token → GitHub is never contacted"

# ── 14: the body file must exist ───────────────────────────────────────────
reset_case
so="$("$HELPER" file --title "$STABLE_TITLE" --body-file "$FIX/nope.md" 2>&1)"; rc=$?
assert_eq "$rc" "2" "14. a missing body file → usage error exit 2"
assert_eq "$(count_calls 'GH POST')" "0" "14. a missing body file → nothing is posted"

# ── 15: lib-only sourcing does not execute main ────────────────────────────
reset_case
so="$(AUTO_FILE_LIB_ONLY=1 bash -c 'set -uo pipefail; . "$1"; printf "lib-ok"' _ "$HELPER" 2>&1)"; rc=$?
assert_eq "$rc" "0" "15. AUTO_FILE_LIB_ONLY=1 sources cleanly"
assert_contains "$so" "lib-ok" "15. sourcing under LIB_ONLY does not run main"
assert_eq "$(count_calls 'GH ')" "0" "15. sourcing under LIB_ONLY contacts nothing"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "auto-file-issue.test.sh: $PASS passed, 0 failed ✅"
  exit 0
fi
echo "auto-file-issue.test.sh: $PASS passed, $FAIL FAILED ❌"
exit 1
