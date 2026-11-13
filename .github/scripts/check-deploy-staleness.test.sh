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
#   13b. the mirror that hides jq must not hide ANYTHING ELSE: a PATH entry holding a
#        DIRECTORY named like a tool, or a RELATIVE PATH entry, must not shadow a real
#        tool out of the mirror (both measured to, before the pins existed)
#   14. bound garbage / overflow              → normalized to the default
#   15. a valid explicit bound is honoured    → not force-defaulted
#   16. cannot resolve main (bad ref)         → exit 1 (cannot verify = never a pass)
#   17. a docs-ONLY lag, raw behind > bound   → exit 0 (deployable=0)   ← MUTATION (no crying wolf)
#   18. within COUNT bound but the oldest deployable commit is 48 h old
#                                             → exit 1, reason behind-too-long ← MUTATION (#10109 T-arm)
#   19. within COUNT bound, deployable commit is young → exit 0, age stated
#   20. the deploy trigger's `paths:` cannot be read → exit 1 (never a pass)
#   21. PARITY: the REAL `deploy-hosted.yml` filter is what the checker derives
#       (tortoise/ counts as deployable, docs/ does not)

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

# ── the synthetic repo: c0 → c1 → c2 → c3 on main, plus two side lines ───────
# Every main commit touches `tortoise/` (DEPLOYABLE under the fixture filter), so
# the count cases below exercise the deployable distance, not a docs-only lag.
# `docs-lag` adds three DOCS-ONLY commits (raw lag 3, deployable 0 — the
# false-alarm shape) and `old-deploy` adds ONE deployable commit dated 48 h ago
# (count 1, age over the bound — the "deploys stopped landing" shape).
REPO="$FIX/repo"
mkdir -p "$REPO/tortoise" "$REPO/docs"
commit_file() { # <path> <content> <message> [epoch]
  printf '%s\n' "$2" > "$REPO/$1"
  git -C "$REPO" add "$1"
  if [ -n "${4:-}" ]; then
    GIT_AUTHOR_DATE="@$4 +0000" GIT_COMMITTER_DATE="@$4 +0000" \
      git -C "$REPO" commit -q -m "$3"
  else
    git -C "$REPO" commit -q -m "$3"
  fi
  git -C "$REPO" rev-parse HEAD
}

git init -q -b main "$REPO"
git -C "$REPO" config user.email t@t
git -C "$REPO" config user.name t
C0="$(commit_file tortoise/app.py 0 c0)"
C1="$(commit_file tortoise/app.py 1 c1)"
C2="$(commit_file tortoise/app.py 2 c2)"
C3="$(commit_file tortoise/app.py 3 c3)"

# A side line, for the divergence case.
git -C "$REPO" checkout -q -b side "$C1"
SIDE="$(commit_file tortoise/side.py side side)"

# Three DOCS-ONLY commits on top of c3: raw lag 3, deployable lag 0.
git -C "$REPO" checkout -q -b docs-lag "$C3"
commit_file docs/n1.md 1 'docs 1' >/dev/null
commit_file docs/n2.md 2 'docs 2' >/dev/null
DLAG="$(commit_file docs/n3.md 3 'docs 3')"

# ONE deployable commit, committed 48 h ago: within the COUNT bound, past the
# AGE bound. 48 h chosen so the assertion does not sit near the 6 h default.
HOW_LONG_AGO=$(( $(date +%s) - 48 * 3600 ))
git -C "$REPO" checkout -q -b old-deploy "$C3"
OLD="$(commit_file tortoise/hotfix.py x 'hotfix' "$HOW_LONG_AGO")"

git -C "$REPO" checkout -q main
# main is 3 ahead of C1: `rev-list --count C1..main` == 2 (c2, c3).
BEHIND_C1="$(git -C "$REPO" rev-list --count "${C1}..main")"
BEHIND_DEPLOYABLE_C1="$(git -C "$REPO" rev-list --count "${C1}..main" -- 'tortoise/**')"
# docs-lag: raw 3, deployable 0.
BEHIND_CLAG="$(git -C "$REPO" rev-list --count "${C3}..docs-lag")"
BEHIND_DEPLOYABLE_CLAG="$(git -C "$REPO" rev-list --count "${C3}..docs-lag" -- 'tortoise/**')"

# ── the fixture deploy trigger the checker derives its paths from ───────────
# Deliberately carries the shapes the real file has: a trailing comment after
# an item, a multi-line comment INSIDE the block, and a quoted scalar.
DEPLOY_WF_FIXTURE="$FIX/deploy-hosted.fixture.yml"
cat > "$DEPLOY_WF_FIXTURE" <<'WF_EOF'
name: Fixture deploy
on:
  push:
    branches: [main]
    paths:
      - 'tortoise/**'
      - 'packs/**'          # a trailing comment must not become part of the glob
      # a comment line inside paths: must not terminate the block
      - 'Dockerfile.hosted'
  workflow_dispatch:
WF_EOF
# An unreadable-filter fixture: no `paths:` under on.push.
DEPLOY_WF_NOPATHS="$FIX/deploy-hosted.nopaths.yml"
cat > "$DEPLOY_WF_NOPATHS" <<'WF_EOF'
name: Fixture deploy
on:
  push:
    branches: [main]
  workflow_dispatch:
WF_EOF
# The REAL workflow, for the parity case (never a copy of its path list).
DEPLOY_WF_REAL="$SCRIPT_DIR/../workflows/deploy-hosted.yml"
# A PARTIALLY-parsed filter: the block is there, three entries parse, and then a
# folded scalar appears INSIDE `paths:`. Keeping the three collected entries
# would count a SMALLER set than the trigger and read GREEN while deployable
# commits wait, so the parser must exit non-zero and the call site must RED.
DEPLOY_WF_PARTIAL="$FIX/deploy-hosted.partial.yml"
cat > "$DEPLOY_WF_PARTIAL" <<'WF_EOF'
name: Fixture deploy
on:
  push:
    branches: [main]
    paths:
      - 'tortoise/**'
      - >-
          a folded scalar nobody's parser expected
      - 'packs/**'
      - 'fly.toml'
  workflow_dispatch:
WF_EOF
# YAML NODE DECORATION in the list: an ANCHOR defined above and an ALIAS used
# as an entry. Valid YAML — the real paths are ['tortoise/**', 'packs/**'] — but
# the alias strips to `*redeploy`, which matches no path. Emitting it makes the
# pathspec set SMALLER than the trigger's, so a deployable commit can go
# uncounted and the check reads GREEN while it waits: the fail-open this whole
# check exists to refuse, arriving silently. Deliberately whitespace-free, so
# ONLY the alias rule can refuse it and this case discriminates that rule.
DEPLOY_WF_ANCHOR="$FIX/deploy-hosted.anchor.yml"
cat > "$DEPLOY_WF_ANCHOR" <<'WF_EOF'
name: Fixture deploy
x-redeploy: &redeploy 'tortoise/**'
on:
  push:
    branches: [main]
    paths:
      - *redeploy
      - 'packs/**'
  workflow_dispatch:
WF_EOF

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
export DEPLOY_STALENESS_DEPLOY_WORKFLOW="$DEPLOY_WF_FIXTURE"
unset GITHUB_ACTIONS || true

reset_case() {
  : > "$STUB_TMP/calls.log"
  rm -f "$STUB_TMP/created.json" "$STUB_TMP/patched.log" "$STUB_TMP/comments.log"
  unset STUB_SEARCH_FAIL STUB_ALERT_SEARCH_JSON STUB_ALERT_CREATE_FAIL STUB_NEW_ALERT \
        STUB_COMMENT_FAIL STUB_ALERT_PATCH_FAIL STUB_CURL_FAIL STUB_VERSION_JSON \
        DEPLOY_STALENESS_COMMIT_SHA DEPLOY_STALENESS_MAX_BEHIND DEPLOY_STALENESS_MAX_AGE_HOURS \
        DEPLOY_STALENESS_DEPLOY_WORKFLOW 2>/dev/null || true
  export GH_TOKEN="test-token"
  export PATH="$BIN:$PATH"
  export DEPLOY_STALENESS_REPO_DIR="$REPO"
  export DEPLOY_STALENESS_REF="main"
  export DEPLOY_STALENESS_DEPLOY_WORKFLOW="$DEPLOY_WF_FIXTURE"
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

# The same, with the ref / deploy-trigger / age-bound seams overridable — the
# path-scoping and age-arm cases need a different tip, a different filter, and a
# moving age bound.
run_checker_with() { # <sha> [bound] [ref] [deploy-workflow] [max-age-hours]
  reset_case
  export DEPLOY_STALENESS_COMMIT_SHA="$1"
  [ -n "${2:-}" ] && export DEPLOY_STALENESS_MAX_BEHIND="$2"
  [ -n "${3:-}" ] && export DEPLOY_STALENESS_REF="$3"
  [ -n "${4:-}" ] && export DEPLOY_STALENESS_DEPLOY_WORKFLOW="$4"
  [ -n "${5:-}" ] && export DEPLOY_STALENESS_MAX_AGE_HOURS="$5"
  set +e
  "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"
  RC=$?
  OUT="$(cat "$STUB_TMP/stderr.log" 2>/dev/null || true)"
  set -e
}

# Same, but with a PATH that hides `jq` ALONE so the script's own `command -v jq`
# must refuse — while every other tool the checker may legitimately use stays
# resolvable.
#
# ⛔ #12077: this case used to run with `PATH="$BIN"`, which also removed
# `dirname` — so the checker's line 107
# (`SCRIPT_DIR="$(cd "$(dirname …)" && pwd)"`) collapses to `cd ""` BEFORE its
# `command -v jq` guard can speak, and the case then reports the MESSAGE as
# missing. Measured in CI and reproduced in `ubuntu:26.04`: `❌ case 13: names
# jq (missing 'jq is required' in: … line 107: dirname: command not found)`
# while `case 13: missing jq → exit 1` PASSED.
#
# ⚠️ The red needs BOTH halves, and the second one is why it is runner-only: the
# stripped PATH (no `dirname`) AND bash ≥ 5.3 (the runner image), where `cd ""`
# is an ERROR and `set -e` kills the checker. bash ≤ 5.2 tolerates `cd ""`
# (measured: 5.2.21 → `rc=0`, `pwd` = the cwd; 5.3.9 → `cd: null directory`,
# `rc=1`), so on such a box SCRIPT_DIR becomes the cwd, the jq guard IS reached
# and this case is GREEN — which is exactly why the defect was invisible off the
# runner. Do not read a green case 13 on an older box as "the CI red was wrong".
#
# Either way a PATH stripped to one stub makes the assertion depend on which
# helper the script happens to call first — an ordering artefact, not a property
# of the checker.
#
# So the no-jq PATH is a MIRROR of the real one (symlinks to every executable,
# jq excluded) rather than a whitelist: a whitelist rots the moment the checker
# gains a helper, and it rots into this same false RED. The pins at case 13 keep
# the mirror honest — it must hide jq AND keep dirname.
#
# ⚠️ The mirror must not INTRODUCE the same class (#12077 review round 1): it
# symlinks only REGULAR files (`-f`, not `-x` — a DIRECTORY is executable, and a
# PATH entry holding a directory named `dirname` then occupies that name in the
# mirror, shadowing the real tool out of reach of every later entry), and it
# resolves each PATH entry to an ABSOLUTE path first (`cd … && pwd -P`), because
# `ln -s` of a relative target against the mirror dir produces a DANGLING link
# that `-e` dedupe then makes permanent. Both were measured to hide a real
# `dirname` that the real PATH resolves — a false RED of exactly this class.
NO_JQ_PATH="$FIX/no-jq-path"
build_no_jq_path() { # [<dest>] — defaults to $NO_JQ_PATH
  local dest="${1:-$NO_JQ_PATH}"
  rm -rf "$dest"
  mkdir -p "$dest"
  local dir name
  local -a dirs
  IFS=: read -r -a dirs <<<"$PATH"
  # bash < 4.4 errors on `"${dirs[@]}"` for an EMPTY array under `set -u`, which would
  # kill the suite with no summary line — the failure the `|| true` on `ln` exists to
  # prevent. No caller supplies an empty PATH today; this keeps the next one safe.
  [ "${#dirs[@]}" -gt 0 ] || return 0
  for dir in "${dirs[@]}"; do
    [ -d "$dir" ] || continue
    dir="$(cd "$dir" && pwd -P)" || continue
    [ "$dir" = "$dest" ] && continue
    for name in "$dir"/*; do
      [ -f "$name" ] || continue # a DIRECTORY is `-x`; it must not occupy a tool's name
      [ "${name##*/}" = jq ] && continue
      if [ -x "$name" ] && [ ! -e "$dest/${name##*/}" ]; then
        # `|| true`: a single failed link must not abort the whole harness under the
        # `set -e` that `run_checker` leaves on (measured: a dangling-name collision
        # made `ln` fail and the suite exited with NO summary line at all). What the
        # mirror must guarantee is pinned immediately after this call, not here.
        ln -s "$name" "$dest/${name##*/}" 2>/dev/null || true
      fi
    done
  done
}

run_checker_no_jq() {
  reset_case
  export DEPLOY_STALENESS_COMMIT_SHA="$C3"
  build_no_jq_path
  set +e
  PATH="$NO_JQ_PATH" "$BASH_BIN" "$CHECKER" </dev/null 2>"$STUB_TMP/stderr.log"
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
# The refusal must be the script's OWN dependency check, not a casualty of the
# stripped PATH: an external tool needed before it (the old `dirname`-based
# SCRIPT_DIR) made the same case abort with a `dirname: command not found` and
# `cd: null directory` pair — pass locally (bash ≤5.2 treats `cd ""` as a no-op)
# and FAIL on the runner (bash 5.3 errors), with the operator never told about
# jq. This assertion catches that regression on EVERY bash version, because it
# forbids the symptom instead of relying on the shell's `cd ""` behaviour.
assert_not_contains "$OUT" "command not found" "case 13: needs NO external tool before its dependency checks (a dirname-less PATH must still reach the jq refusal)"
assert_not_contains "$OUT" "dirname" 'case 13: nothing in the refusal path depends on `dirname`'
# Non-vacuity, both directions: the case means "jq is hidden", not "PATH is
# empty". Without these pins the case passes on a PATH that hides every
# binary, which is the #12077 false RED — `dirname` gone, the checker dead at
# line 107, and its own message never reached.
# The AMBIENT jq must exist first, or "the mirror hides jq" proves nothing: on a
# host with no jq anywhere the mirror pin is green for the wrong reason.
if command -v jq >/dev/null 2>&1; then
  ok "case 13: jq exists on the ambient PATH, so the mirror pin means something"
else
  bad "case 13: no jq on the ambient PATH — the mirror pin cannot distinguish 'hidden' from 'absent'"
fi
if PATH="$NO_JQ_PATH" command -v jq >/dev/null 2>&1; then
  bad "case 13: the no-jq PATH still resolves jq (the case would test nothing)"
else
  ok "case 13: the no-jq PATH really does hide jq"
fi
if PATH="$NO_JQ_PATH" command -v dirname >/dev/null 2>&1; then
  ok "case 13: and it KEEPS dirname — a hidden coreutil, not a hidden jq, was the false RED"
else
  bad "case 13: the no-jq PATH also hides dirname, so the checker dies before its own guard"
fi

# ── 13b. the mirror must not HIDE a real tool (#12077 review round 1) ───────
# Two measured ways the FIRST cut of the mirror did, both re-creating the false
# RED this fix removes:
#   (a) a PATH entry holding a DIRECTORY named after a tool — `[ -x ]` is true for
#       a directory, and the `[ ! -e ]` dedupe then made it permanent, so the real
#       executable in a LATER entry could never be linked;
#   (b) a RELATIVE PATH entry — `ln -s` of a relative target resolves against the
#       MIRROR dir, producing a dangling link that `-e` cannot see, so the real
#       one is never linked either and `ln` also complains on the job log.
# Both cases assert the property (a tool the REAL PATH resolves still resolves
# through the mirror), not the example.
MIRROR_PROBE="$FIX/mirror-probe"
mkdir -p "$MIRROR_PROBE/early/dirname" "$MIRROR_PROBE/late" "$MIRROR_PROBE/reldir"
printf '#!/bin/sh\nexit 0\n' > "$MIRROR_PROBE/late/dirname"
printf '#!/bin/sh\nexit 0\n' > "$MIRROR_PROBE/reldir/dirname"
chmod +x "$MIRROR_PROBE/late/dirname" "$MIRROR_PROBE/reldir/dirname"
( PATH="$MIRROR_PROBE/early:$MIRROR_PROBE/late:/usr/bin:/bin"; build_no_jq_path "$MIRROR_PROBE/mirror-abs" )
if PATH="$MIRROR_PROBE/mirror-abs" command -v dirname >/dev/null 2>&1; then
  ok "case 13b: a DIRECTORY named like a tool does not shadow the real one out of the mirror"
else
  bad "case 13b: the mirror lost dirname behind a same-named DIRECTORY in an earlier PATH entry"
fi
( cd "$MIRROR_PROBE" && PATH="reldir:/usr/bin:/bin" build_no_jq_path "$MIRROR_PROBE/mirror-rel" )
if PATH="$MIRROR_PROBE/mirror-rel" command -v dirname >/dev/null 2>&1; then
  ok "case 13b: a RELATIVE PATH entry does not leave a dangling mirror link that hides the real tool"
else
  bad "case 13b: a relative PATH entry produced a dangling mirror link and hid dirname"
fi

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

# ── 17. docs-ONLY lag is NOT staleness (the false-alarm shape) ───────────────
# Raw distance 3 (> the bound of 2) with ZERO deployable commits. Before the
# deployability scope this filed "PRODUCTION STALE" and pointed the operator at
# `deploy-hosted` runs that do not exist for those commits — the muddied alarm
# that makes a detector inert. It MUST be green, and both numbers MUST be stated
# so the reader can see why.
run_checker_with "$C3" 2 "docs-lag" "" ""
assert_eq "$RC" 0 "case 17 (MUTATION): docs-only lag (raw ${BEHIND_CLAG}, deployable ${BEHIND_DEPLOYABLE_CLAG}) → exit 0"
assert_contains "$OUT" "behind=${BEHIND_CLAG}" "case 17: the RAW distance is still stated"
assert_contains "$OUT" "behind_deployable=${BEHIND_DEPLOYABLE_CLAG}" "case 17: the deployable distance is stated as 0"
[ ! -f "$STUB_TMP/created.json" ] && ok "case 17: no alert filed for a non-deployable lag" || bad "case 17: an alert was filed for a docs-only lag"

# ── 18. the AGE arm: within the COUNT bound, but deploys stopped landing ─────
# ONE deployable commit, dated 48 h ago. The count (1) is within the bound (2),
# so the count bound alone reports GREEN forever — the #10109 state. The age arm
# is what reds it.
run_checker_with "$C3" 2 "old-deploy" "" ""
assert_eq "$RC" 1 "case 18 (MUTATION): 1 deployable commit, 48 h old, bound 2 → exit 1"
assert_contains "$OUT" "behind-too-long" "case 18: reason names the age arm"
assert_contains "$OUT" "behind_deployable=1" "case 18: states that the COUNT is within bound"
assert_contains "$OUT" "max_age_hours=6" "case 18: names the age bound it crossed"
if [ -f "$STUB_TMP/created.json" ]; then
  ok "case 18: alert filed"
  assert_contains "$(cat "$STUB_TMP/created.json")" "behind_deployable=1" "case 18: body states the deployable distance"
  assert_contains "$(cat "$STUB_TMP/created.json")" "deployable_age_h=" "case 18: body states the deployable age"
  # The verdict is decided on SECONDS while `deployable_age_h` is floored, so the
  # seconds must be stated too — otherwise a 6 h 01 m alert prints
  # `deployable_age_h=6 max_age_hours=6` while RED and reads as an off-by-one.
  assert_contains "$(cat "$STUB_TMP/created.json")" "deployable_age_s=" "case 18: body states the age in SECONDS, so it can never appear to sit at the bound it crossed"
else
  bad "case 18: alert was NOT filed"
fi
# 18b DISCRIMINATION: the SAME tree and the SAME count, with the age bound
# raised past the pending commit's age → green. Proves the red in 18 came from
# the age, not from the count (a detector that reds on everything passes 18).
run_checker_with "$C3" 2 "old-deploy" "" "100"
assert_eq "$RC" 0 "case 18b: same commit, age bound 100 h → exit 0 (the age decided, not the count)"
[ ! -f "$STUB_TMP/created.json" ] && ok "case 18b: no alert filed" || bad "case 18b: unexpected alert"

# ── 19. within the count bound, deployable commit is YOUNG → green, age stated ─
run_checker_with "$C2" 2 "" "" ""
assert_eq "$RC" 0 "case 19: 1 young deployable commit ≤ bound 2 → exit 0"
assert_contains "$OUT" "behind_deployable=1" "case 19: deployable distance stated"
assert_contains "$OUT" "deployable_age_h=0" "case 19: the age of the pending commit is stated even when green"

# ── 20. the deploy trigger's paths cannot be read → RED, never a pass ─────────
# A checker that cannot tell which commits deploy cannot judge staleness. This
# is the fail-closed half of the path-scoping: a `deploy-hosted.yml` whose
# trigger shape changed must red, not silently fall back to a raw count (which
# would start crying wolf) or to "no paths" (which would count nothing and go
# green forever — the exact defect #10109 is about).
run_checker_with "$C1" 2 "" "$DEPLOY_WF_NOPATHS" ""
assert_eq "$RC" 1 "case 20: unreadable deploy paths → exit 1"
assert_contains "$OUT" "deploy-paths-unreadable" "case 20: reason names the unreadable filter"

# ── 20b. A PARTIAL parse is not a parse ───────────────────────────────────────
# The fixture keeps 3 usable entries after truncation. Emitting them would have
# counted a SMALLER deployable set than the trigger, i.e. under-reported the lag
# — the "silently never fires" shape. Must RED, and must not print a path.
run_checker_with "$C1" 2 "" "$DEPLOY_WF_PARTIAL" ""
assert_eq "$RC" 1 "case 20b: partially-parsed deploy paths → exit 1"
assert_contains "$OUT" "deploy-paths-unreadable" "case 20b: reason names the unreadable filter"
assert_not_contains "$OUT" "behind_deployable=0" "case 20b: a refused parse must never resolve to 0 deployable commits (the green fail-open)"

# ── 20c. YAML node decoration (anchor / alias) in `paths:` ────────────────────
# Valid YAML whose decoration strips to non-paths. If those were emitted the
# pathspec count would be 0 → GREEN while a deployable commit waits. The
# assertion on `behind_deployable=` (not on a literal emitted string) is what
# makes this discriminating: it fails on the OUTCOME the fail-open produces.
run_checker_with "$C1" 2 "" "$DEPLOY_WF_ANCHOR" ""
assert_eq "$RC" 1 "case 20c: anchor/alias in paths: → exit 1"
assert_contains "$OUT" "deploy-paths-unreadable" "case 20c: reason names the unreadable filter"
assert_not_contains "$OUT" "behind_deployable=0" "case 20c: never resolves a decorated entry to 0 deployable commits"

# ── 21. PARITY with the REAL deploy-hosted.yml, both directions ─────────────
# The checker derives the filter at run time instead of duplicating it, and the
# price of that is this pin: a commit touching `tortoise/**` is deployable, a
# commit touching `docs/` is not. If the real trigger's shape ever stops being
# parsed, case 20 reds; if the parse silently stops matching, these two red.
run_checker_with "$C1" "" "" "$DEPLOY_WF_REAL" ""
assert_eq "$RC" 0 "case 21a: real filter — tortoise/** commits are deployable (2 ≤ bound 2)"
assert_contains "$OUT" "behind_deployable=2" "case 21a: the real filter counts tortoise/** commits"
run_checker_with "$C3" "" "docs-lag" "$DEPLOY_WF_REAL" ""
assert_eq "$RC" 0 "case 21b: real filter — a docs-only lag on the real trigger → exit 0"
assert_contains "$OUT" "behind_deployable=0" "case 21b: the real filter does NOT count docs/ commits"

echo ""
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] && { echo "OK — all assertions passed"; exit 0; } || { echo "FAILED — $FAIL assertion(s) failed"; exit 1; }
