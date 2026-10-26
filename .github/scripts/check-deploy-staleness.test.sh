#!/usr/bin/env bash
# check-deploy-staleness.test.sh — self-check for
# .github/scripts/check-deploy-staleness.sh (#10109).
#
# Run: bash .github/scripts/check-deploy-staleness.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: builds a
# synthetic git repo (real commits, so `merge-base --is-ancestor` / `rev-list
# --count` are exercised for real), stubs `gh` on PATH (no network, no GitHub),
# and pins the deployed revision through the script's own seams.
#
# ⛔ NON-VACUITY IS THE POINT. A staleness check that cannot fail is the defect
# (#10109) restated, so the load-bearing cases are the ones where the checker
# MUST go red: the deployed commit behind the bound (THE MUTATION — the
# 2026-10-26 shape, 7 behind), a divergent commit, a sha that is not an object,
# and an unreadable revision. Each asserts BOTH the non-zero exit AND the
# filed/updated alert carrying `deployed=`/`main=`/`behind=`. The failed-search
# and missing-token cases assert the mirror-image side effect — exit 1 with NO
# alert filed, never a false page.
#
# Coverage:
#   1.  deployed == main                     → exit 0, NO alert, behind=0 stated
#   2.  behind ≤ bound (2 behind, bound 2)   → exit 0, NO alert, behind=2 stated
#   3.  behind > bound (2 behind, bound 1)   → exit 1, alert filed, behind=2  ← MUTATION (stale)
#   4.  existing open alert                  → PATCHed, NOT duplicated
#   5.  a DIVERGED commit (side branch)      → exit 1, reason names not-ancestor
#   6.  a sha that is NOT an object          → exit 1, reason names not-an-object
#   7.  an UNREADABLE revision (empty seam)  → exit 1, never a pass (fail closed)
#   8.  the HTTP read path: curl 404s        → exit 1 (unreadable), NOT a pass
#   9.  the HTTP read path: curl returns a valid payload → compared like a seam
#   10. recovery: current + open alert        → close + Recovered comment, exit 0
#   11. alert search fails                    → exit 1, NO create (not a false page)
#   12. missing GH_TOKEN                      → exit 1 before any gh call
#   13. missing jq                            → exit 1
#   14. bound garbage / overflow              → normalized to the default
#   15. a valid explicit bound is honoured    → not force-defaulted
#   16. cannot resolve main (bad ref)         → exit 1 (cannot verify = never a pass)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASH_BIN="$(command -v bash)"
CHECKER="$SCRIPT_DIR/check-deploy-staleness.sh"

ALERT_TITLE_FIXTURE='[OPS] production deployment is stale (behind origin/main)'
ALERT_MARKER_FIXTURE='<!-- deploy-staleness-alert -->'

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_eq() { if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (got '$1', want '$2')"; fi; }
assert_contains() { case "$1" in *"$2"*) ok "$3" ;; *) bad "$3 (missing '$2' in: $(printf '%s' "$1" | head -c 300))" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) bad "$3 (unexpectedly found '$2')" ;; *) ok "$3" ;; esac; }

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT

# ── the synthetic repo: c0 → c1 → c2 → c3 on main, plus a side branch ────────
REPO="$FIX/repo"
mkdir -p "$REPO"
git init -q -b main "$REPO"
git -C "$REPO" config user.email t@t
git -C "$REPO" config user.name t
: > "$REPO/f"
git -C "$REPO" add f
git -C "$REPO" commit -q -m c0
C0="$(git -C "$REPO" rev-parse HEAD)"
echo 1 > "$REPO/f"; git -C "$REPO" commit -qam c1; C1="$(git -C "$REPO" rev-parse HEAD)"
echo 2 > "$REPO/f"; git -C "$REPO" commit -qam c2; C2="$(git -C "$REPO" rev-parse HEAD)"
echo 3 > "$REPO/f"; git -C "$REPO" commit -qam c3; C3="$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" checkout -q -b side "$C1"
echo side > "$REPO/g"; git -C "$REPO" add g; git -C "$REPO" commit -qam side
SIDE="$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" checkout -q main
# main is 3 ahead of C1: `rev-list --count C1..main` == 2 (c2, c3).
BEHIND_C1="$(git -C "$REPO" rev-list --count "${C1}..main")"

BIN="$FIX/bin"
mkdir -p "$BIN"
STUB_TMP="$FIX/stub"
mkdir -p "$STUB_TMP"
export STUB_TMP

# ── stub: gh (search/create/patch/comment only — no heartbeat, no workflow) ──
cat > "$BIN/gh" <<'GH_EOF'
#!/usr/bin/env bash
[ "${1:-}" = "api" ] || { echo "GH unexpected: $*" >&2; exit 1; }
DEFAULT_ITEMS_JSON='{"items":[]}'
path="${2:-}"; method="GET"; input=0; paginate=0
shift 2 || true
while [ $# -gt 0 ]; do
  case "$1" in
    --method) method="$2"; shift 2 2>/dev/null || shift ;;
    --input) input=1; shift ;;
    --paginate) paginate=1; shift ;;
    *) shift ;;
  esac
done
payload=""
if [ "$input" = "1" ]; then payload="$(cat)"; fi
echo "GH $method $path" >> "$STUB_TMP/calls.log"

case "$path" in
  search/issues*)
    [ "${STUB_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: search failed" >&2; exit 1; }
    printf '%s' "${STUB_ALERT_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}" ;;
  */issues/*/comments)
    printf '%s' "$payload" | jq -c . >> "$STUB_TMP/comments.log"
    [ "${STUB_COMMENT_FAIL:-0}" = "1" ] && { echo "gh: comment failed" >&2; exit 1; }
    printf '{}' ;;
  */issues)
    printf '%s' "$payload" | jq -c . > "$STUB_TMP/created.json"
    [ "${STUB_ALERT_CREATE_FAIL:-0}" = "1" ] && { echo "gh: create failed" >&2; exit 1; }
    printf '{"number":%s}' "${STUB_NEW_ALERT:-500}" ;;
  */issues/*)
    printf '%s' "$payload" | jq -c . >> "$STUB_TMP/patched.log"
    case "$payload" in *'"state":"closed"'*) echo "CLOSE $path" >> "$STUB_TMP/patched.log" ;; esac
    [ "${STUB_ALERT_PATCH_FAIL:-0}" = "1" ] && { echo "gh: patch failed" >&2; exit 1; }
    printf '{}' ;;
  *) printf '{}' ;;
esac
exit 0
GH_EOF
chmod +x "$BIN/gh"

# ── stub: curl (the HTTP read path; the seam path never reaches it) ──────────
cat > "$BIN/curl" <<'CURL_EOF'
#!/usr/bin/env bash
echo "CURL $*" >> "$STUB_TMP/calls.log"
[ "${STUB_CURL_FAIL:-0}" = "1" ] && { echo "curl: connection refused" >&2; exit 7; }
printf '%s' "${STUB_VERSION_JSON:-{\"version\":\"0\",\"commit_sha\":null}}"
exit 0
CURL_EOF
chmod +x "$BIN/curl"

# ── base env ────────────────────────────────────────────────────────────────
export PATH="$BIN:$PATH"
export GITHUB_REPOSITORY="daniel-ospina/tortoise"
export DEPLOY_STALENESS_REPO_DIR="$REPO"
export DEPLOY_STALENESS_REF="main"
unset GITHUB_ACTIONS || true

reset_case() {
  : > "$STUB_TMP/calls.log"
  rm -f "$STUB_TMP/created.json" "$STUB_TMP/patched.log" "$STUB_TMP/comments.log"
  unset STUB_SEARCH_FAIL STUB_ALERT_SEARCH_JSON STUB_ALERT_CREATE_FAIL STUB_NEW_ALERT \
        STUB_COMMENT_FAIL STUB_ALERT_PATCH_FAIL STUB_CURL_FAIL STUB_VERSION_JSON \
        DEPLOY_STALENESS_COMMIT_SHA DEPLOY_STALENESS_MAX_BEHIND 2>/dev/null || true
  export GH_TOKEN="test-token"
  export PATH="$BIN:$PATH"
  export DEPLOY_STALENESS_REPO_DIR="$REPO"
  export DEPLOY_STALENESS_REF="main"
}

run_checker() { # <commit-sha-or-UNSET> [bound]  → sets RC, OUT (stderr)
  reset_case
  if [ "${1:-__unset__}" = "__unset__" ]; then
    unset DEPLOY_STALENESS_COMMIT_SHA
  else
    export DEPLOY_STALENESS_COMMIT_SHA="$1"
  fi
  [ -n "${2:-}" ] && export DEPLOY_STALENESS_MAX_BEHIND="$2"
  set +e
  "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"
  RC=$?
  OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
  set -e
}

# Same, but PATH stripped so jq is provably absent (the script's own `command -v
# jq` must refuse, not find a system jq). bash is resolved here first.
run_checker_no_jq() {
  reset_case
  export DEPLOY_STALENESS_COMMIT_SHA="$C3"
  set +e
  PATH="$BIN" "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"
  RC=$?
  OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
  set -e
}

seed_open_alert() { # [number]
  local n="${1:-500}"
  STUB_ALERT_SEARCH_JSON="$(printf '{"items":[{"number":%s,"title":"%s","body":"%s","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$n" "$ALERT_TITLE_FIXTURE" "$ALERT_MARKER_FIXTURE")"
  export STUB_ALERT_SEARCH_JSON
}

echo "== check-deploy-staleness.test.sh (#10109) =="

# ── 1. current: deployed == main ─────────────────────────────────────────────
run_checker "$C3"
assert_eq "$RC" 0 "case 1: current → exit 0"
assert_contains "$OUT" "behind=0" "case 1: distance stated (behind=0)"
[ ! -f "$STUB_TMP/created.json" ] && ok "case 1: no alert filed" || bad "case 1: unexpected alert filed"

# ── 2. within bound: 2 behind, bound 2 → still green ─────────────────────────
run_checker "$C1" 2
assert_eq "$RC" 0 "case 2: behind=$BEHIND_C1 ≤ bound 2 → exit 0"
assert_contains "$OUT" "behind=${BEHIND_C1}" "case 2: distance stated (behind=$BEHIND_C1)"
[ ! -f "$STUB_TMP/created.json" ] && ok "case 2: no alert filed" || bad "case 2: unexpected alert filed"

# ── 3. THE MUTATION: 2 behind, bound 1 → stale → RED + alert ─────────────────
run_checker "$C1" 1
assert_eq "$RC" 1 "case 3 (MUTATION): behind=$BEHIND_C1 > bound 1 → exit 1"
assert_contains "$OUT" "behind=${BEHIND_C1}" "case 3: distance stated in the run"
if [ -f "$STUB_TMP/created.json" ]; then
  ok "case 3: alert filed"
  assert_contains "$(cat "$STUB_TMP/created.json")" "$ALERT_TITLE_FIXTURE" "case 3: alert title is the stable key"
  assert_contains "$(cat "$STUB_TMP/created.json")" "deployed=$C1" "case 3: body names the stale deployed sha"
  assert_contains "$(cat "$STUB_TMP/created.json")" "main=$C3" "case 3: body names main"
  assert_contains "$(cat "$STUB_TMP/created.json")" "behind=${BEHIND_C1}" "case 3: body states the distance"
else
  bad "case 3: alert was NOT filed"
fi

# ── 4. dedupe: an open alert is PATCHed, never duplicated ────────────────────
reset_case
seed_open_alert 500
export DEPLOY_STALENESS_COMMIT_SHA="$C1"
export DEPLOY_STALENESS_MAX_BEHIND=1
set +e; "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"; RC=$?; set -e
OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
assert_eq "$RC" 1 "case 4: stale with an open alert → still exit 1"
[ ! -f "$STUB_TMP/created.json" ] && ok "case 4: NOT duplicated (no create)" || bad "case 4: a duplicate issue was created"
if [ -f "$STUB_TMP/patched.log" ] && grep -q 'deploy_staleness=stale' "$STUB_TMP/patched.log"; then
  ok "case 4: the open alert body was PATCHed"
else
  bad "case 4: the open alert body was NOT updated"
fi

# ── 5. diverged: a side-branch commit is NOT an ancestor of main ─────────────
run_checker "$SIDE"
assert_eq "$RC" 1 "case 5: a divergent deployed commit → exit 1"
assert_contains "$OUT" "not-ancestor" "case 5: reason names the divergence"

# ── 6. a sha that is not an object in this repo ──────────────────────────────
run_checker "1111111111111111111111111111111111111111"
assert_eq "$RC" 1 "case 6: a sha not in the repo → exit 1"
assert_contains "$OUT" "not-an-object" "case 6: reason names not-an-object"

# ── 7. unreadable revision (empty seam): fail closed, never a pass ───────────
reset_case
export DEPLOY_STALENESS_COMMIT_SHA=""
set +e; "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"; RC=$?; set -e
OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
assert_eq "$RC" 1 "case 7: an unreadable revision → exit 1 (never a pass)"
assert_contains "$OUT" "unreadable" "case 7: reason names unreadable"

# ── 8. HTTP path: curl fails → unreadable, RED ───────────────────────────────
reset_case
unset DEPLOY_STALENESS_COMMIT_SHA
export STUB_CURL_FAIL=1
set +e; "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"; RC=$?; set -e
OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
assert_eq "$RC" 1 "case 8: the version read failed → exit 1 (never a pass)"
assert_contains "$OUT" "unreadable" "case 8: reason names unreadable"

# ── 9. HTTP path: a valid payload is compared like a seam ────────────────────
reset_case
unset DEPLOY_STALENESS_COMMIT_SHA
export STUB_VERSION_JSON="{\"version\":\"0.0.0\",\"commit_sha\":\"$C1\"}"
export DEPLOY_STALENESS_MAX_BEHIND=1
set +e; "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"; RC=$?; set -e
OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
assert_eq "$RC" 1 "case 9: version JSON commit_sha read and found behind → exit 1"
assert_contains "$OUT" "behind=${BEHIND_C1}" "case 9: the API-read sha produced the distance"

# ── 10. recovery: current + an open alert → close + Recovered comment ────────
reset_case
seed_open_alert 500
export DEPLOY_STALENESS_COMMIT_SHA="$C3"
set +e; "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"; RC=$?; set -e
OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
assert_eq "$RC" 0 "case 10: recovered (current) → exit 0"
grep -q 'CLOSE' "$STUB_TMP/patched.log" && ok "case 10: the open alert was closed" || bad "case 10: the open alert was NOT closed"
grep -q 'Recovered' "$STUB_TMP/comments.log" && ok "case 10: a Recovered comment was posted" || bad "case 10: no Recovered comment"

# ── 11. alert search fails → exit 1, NO create (never a false page) ──────────
reset_case
export DEPLOY_STALENESS_COMMIT_SHA="$C1"
export DEPLOY_STALENESS_MAX_BEHIND=1
export STUB_SEARCH_FAIL=1
set +e; "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"; RC=$?; set -e
OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
assert_eq "$RC" 1 "case 11: search failed → exit 1"
[ ! -f "$STUB_TMP/created.json" ] && ok "case 11: NO alert filed on a failed search" || bad "case 11: a false page was filed on a failed search"

# ── 12. missing GH_TOKEN → exit 1 before any gh call ─────────────────────────
reset_case
unset GH_TOKEN
export DEPLOY_STALENESS_COMMIT_SHA="$C3"
set +e; "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"; RC=$?; set -e
OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
assert_eq "$RC" 1 "case 12: missing GH_TOKEN → exit 1"
assert_contains "$OUT" "GH_TOKEN is not set" "case 12: names the missing token"
grep -q 'GH ' "$STUB_TMP/calls.log" 2>/dev/null && bad "case 12: gh was called without a token" || ok "case 12: no gh call made"

# ── 13. missing jq → exit 1 ──────────────────────────────────────────────────
run_checker_no_jq
assert_eq "$RC" 1 "case 13: missing jq → exit 1"
assert_contains "$OUT" "jq is required" "case 13: names jq"

# ── 14. bound garbage / overflow → default ───────────────────────────────────
run_checker "$C1" "2h"
assert_eq "$RC" 0 "case 14a: bound '2h' → default 2 → 2-behind is green"
assert_contains "$OUT" "not a plain integer" "case 14a: warns about the mangled bound"
run_checker "$C1" "99999999999999999999"
assert_eq "$RC" 0 "case 14b: over-magnitude bound → default 2 → green"
run_checker "$C1" "100000"
assert_eq "$RC" 0 "case 14c: bound 100000 REJECTED to default (2), so 2-behind is green"
assert_contains "$OUT" "above 10000" "case 14c: warns the bound is above the range"

# ── 15. a valid explicit bound is honoured ───────────────────────────────────
run_checker "$C1" 3
assert_eq "$RC" 0 "case 15: bound 3 → 2-behind is green (honoured, not forced to default)"

# ── 16. cannot resolve main (a ref that does not exist) ──────────────────────
reset_case
export DEPLOY_STALENESS_COMMIT_SHA="$C3"
export DEPLOY_STALENESS_REF="no/such/ref"
set +e; "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"; RC=$?; set -e
OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
assert_eq "$RC" 1 "case 16: cannot resolve main → exit 1 (cannot verify = never a pass)"
assert_contains "$OUT" "cannot resolve" "case 16: names the unresolvable ref"

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] && { echo "OK — all assertions passed"; exit 0; } || { echo "FAILED — $FAIL assertion(s) failed"; exit 1; }
