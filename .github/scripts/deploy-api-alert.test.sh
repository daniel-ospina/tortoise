#!/usr/bin/env bash
# deploy-api-alert.test.sh — self-check for .github/scripts/deploy-api-alert.sh
# (#2240 — the out-of-band alert for a failed hosted-deploy job).
#
# Run: bash .github/scripts/deploy-api-alert.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs
# `gh`/`curl` on PATH. No network, no GitHub.
#
# Coverage (#2240 acceptance in brackets):
#   1. a drift-gate failure files ONE issue whose title is the STABLE per-step
#      key — no run id, no timestamp, no count [alert on the first failing run]
#   2. that body carries the gate's OWN report (the blocking versions + the
#      ordered `gh workflow run supabase-deploy.yml --ref main` remediation),
#      so the alert cannot disagree with the run it describes
#   3. the FIRST failing step is the one attributed — a later failure in the
#      same payload does not steal the key [same step ⇒ same key]
#   4. a NON-drift failure gets its own key and NO drift section [per-step key]
#   5. a failure with NO step reported falls back to the job-level key (never a
#      crash, never a silent no-op)
#   6. an existing machine-authored issue with the exact key is COMMENTED on
#      (`Recurrence #1`), never duplicated [one issue per failing streak]
#   7. a failed dedupe search → exit 1 and NOTHING filed [never a blind dup]
#   8. no token → exit 1 BEFORE contacting GitHub [deaf-alert guard]
#   9. the drift case with an UNREADABLE report still files, and says so
#  10. the label is passed through to the create call
#  11. a Telegram delivery failure does NOT fail the step (the ledger is the
#      alert; the page is the nudge) but IS logged
#  12. Telegram is NOT called when the ledger leg failed (order: record, then
#      nudge — a paging outage must never cost the record)
#  13. the ledger leg is what fails the step when it cannot file
#  14. `--job` is required and an unknown argument is a usage error (exit 2)
#  15. the body file is written under RUNNER_TEMP and is the issue's body
#  16. a NON-Actions author is refused by the shared substrate on this path too
#      (the PAT trap: it would file a duplicate on every failing run)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/deploy-api-alert.sh"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_eq() { if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (got '$1', want '$2')"; fi; }
assert_contains() {
  case "$1" in *"$2"*) ok "$3" ;; *) bad "$3 (missing '$2')" ;; esac
}
assert_not_contains() {
  case "$1" in *"$2"*) bad "$3 (unexpectedly found '$2')" ;; *) ok "$3" ;; esac
}

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
BIN="$FIX/bin"
mkdir -p "$BIN"
export STUB_TMP="$FIX/stub"
mkdir -p "$STUB_TMP"

# ── stub: gh (exactly the shapes the substrate uses) ───────────────────────
cat > "$BIN/gh" <<'GH_EOF'
#!/usr/bin/env bash
[ "${1:-}" = "api" ] || { echo "GH unexpected: $*" >&2; exit 1; }
path="${2:-}"; method="GET"; paginate=0
shift 2 || true
fields=()
while [ $# -gt 0 ]; do
  case "$1" in
    --method) method="$2"; shift 2 ;;
    --jq) shift 2 ;;
    --paginate) paginate=1; shift ;;
    -f) fields+=("$2"); shift 2 ;;
    *) shift ;;
  esac
done
echo "GH ${method} ${path}" >> "$STUB_TMP/calls.log"
# NB: the default lives in a variable — a literal JSON object inside
# `${VAR:-{...}}` is mis-parsed (the first '}' closes the expansion), which is
# how this stub first shipped and made every create case look like a refusal.
DEFAULT_ITEMS_JSON='{"items":[]}'
case "$path" in
  user)
    # The real installation-token 403 (actions/runner#3289). Nothing may need
    # this call — the substrate asserts the author from the WRITE's response.
    echo 'gh: Resource not accessible by integration (HTTP 403)' >&2
    exit 1 ;;
  search/issues*)
    [ "${STUB_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: search failed" >&2; exit 1; }
    printf '%s\n' "${STUB_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}"
    if [ "$paginate" = "1" ] && [ -n "${STUB_SEARCH_JSON_P2:-}" ]; then printf '%s\n' "$STUB_SEARCH_JSON_P2"; fi
    exit 0 ;;
  */comments\?*)
    [ "${STUB_COMMENTS_FAIL:-0}" = "1" ] && { echo "gh: comments failed" >&2; exit 1; }
    printf '%s' "${STUB_COMMENTS_JSON:-[]}" ;;
  */comments)
    [ "${STUB_COMMENT_FAIL:-0}" = "1" ] && { echo "gh: comment failed" >&2; exit 1; }
    printf '%s\n' "${fields[@]}" > "$STUB_TMP/comment.txt"
    printf '{"user":{"login":"%s"}}' "${STUB_COMMENT_LOGIN:-github-actions[bot]}" ;;
  */issues)
    [ "${STUB_CREATE_FAIL:-0}" = "1" ] && { echo "gh: create failed" >&2; exit 1; }
    printf '%s\n' "${fields[@]}" > "$STUB_TMP/created.txt"
    printf '{"number":900,"user":{"login":"%s"}}' "${STUB_CREATE_LOGIN:-github-actions[bot]}" ;;
  *) printf '{}' ;;
esac
GH_EOF
chmod +x "$BIN/gh"

# ── stub: curl (Telegram leg) ───────────────────────────────────────────────
cat > "$BIN/curl" <<'CURL_EOF'
#!/usr/bin/env bash
echo "CURL $*" >> "$STUB_TMP/calls.log"
out=""
args=("$@")
for i in "${!args[@]}"; do
  case "${args[$i]}" in -o) out="${args[$((i + 1))]}" ;; esac
done
if [ "${STUB_TG_FAIL:-0}" = "1" ]; then
  echo "curl: (7) Failed to connect" >&2
  exit 7
fi
[ -n "$out" ] && printf '%s' '{"ok":true}' > "$out"
exit 0
CURL_EOF
chmod +x "$BIN/curl"
PATH="$BIN:$PATH"
export PATH

# ── ambient: exactly what Actions provides, plus what the job must bind ────
export GITHUB_REPOSITORY="daniel-ospina/tortoise"
export GITHUB_SERVER_URL="https://github.com"
export GITHUB_RUN_ID="987654321"
export GITHUB_SHA="deadbeefcafe"
export GITHUB_WORKFLOW_REF="daniel-ospina/tortoise/.github/workflows/deploy-hosted.yml@refs/heads/main"
export RUNNER_TEMP="$FIX/runner-temp"
mkdir -p "$RUNNER_TEMP"
DRIFT_REPORT="$FIX/drift-report.txt"
cat > "$DRIFT_REPORT" <<'REPORT_EOF'
check-migration-drift: repo migrations vs prod (project ybetwichurajbfswfeqa)
  BLOCKING repo-ahead migrations (pending in repo, NOT applied to prod):
    - 20260926000002
    - 20260930000001
  note: BLOCKING list above is the COMPLETE repo-ahead set — 2 of 2 repo-ahead pending
  OUT OF ORDER (sort before prod's newest applied version 20261001000001) — do NOT apply these as-is:
    - 20260926000002
  Remediation (in this order):
    1. resolve each OUT OF ORDER version above (a or b);
    2. then dispatch the deploy for the pending set above —
       gh workflow run supabase-deploy.yml --ref main
REPORT_EOF

reset_case() {
  : > "$STUB_TMP/calls.log"
  rm -f "$STUB_TMP/created.txt" "$STUB_TMP/comment.txt"
  unset STUB_SEARCH_JSON STUB_SEARCH_JSON_P2 STUB_SEARCH_FAIL STUB_CREATE_FAIL \
        STUB_COMMENT_FAIL STUB_COMMENTS_FAIL STUB_COMMENTS_JSON \
        STUB_CREATE_LOGIN STUB_COMMENT_LOGIN STUB_TG_FAIL || true
  export GH_TOKEN="test-token"
  export TELEGRAM_BOT_TOKEN="tg-secret"
  export TELEGRAM_CHAT_ID="12345"
}

run_alert() { # <args…> -> RC, OUT
  local so
  so="$("$HELPER" "$@" 2>&1)"
  RC=$?
  OUT="$so"
}
count_calls()  { grep -c -- "$1" "$STUB_TMP/calls.log" 2>/dev/null || true; }
# Bound to the END of the path: a bare `…/issues` substring also matches
# `…/issues/42/comments`, so a naive count read a recurrence comment as a filed
# duplicate (and would have hidden a real duplicate behind the same collapse).
count_creates()  { grep -cE -- 'GH POST .*/issues$' "$STUB_TMP/calls.log" 2>/dev/null || true; }
count_comments() { grep -cE -- 'GH POST .*/issues/[0-9]+/comments$' "$STUB_TMP/calls.log" 2>/dev/null || true; }
title_line()   { grep -m1 '^title=' "$STUB_TMP/created.txt" 2>/dev/null || echo ''; }
created()      { [ -f "$STUB_TMP/created.txt" ] && cat "$STUB_TMP/created.txt" || echo ''; }
commented()    { [ -f "$STUB_TMP/comment.txt" ] && cat "$STUB_TMP/comment.txt" || echo ''; }
search_json() { # <number> [title] [login]
  printf '{"items":[{"number":%s,"title":"%s","user":{"login":"%s","type":"Bot"}}]}' \
    "${1:-42}" "${2:-}" "${3:-github-actions[bot]}"
}

DRIFT_STEPS="parity=success verify-secrets=success provenance=success drift=failure fly-machines=skipped set-secrets=skipped deploy=skipped"

echo "── case 1/2/10/15: drift gate fails → ONE issue with the gate's report ─"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "the alert leg succeeded"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues')" "1" "exactly one issue filed"
CREATED="$(created)"
assert_contains "$(title_line)" "title=deploy-hosted: deploy-api failed at 'drift'" "the title is the stable per-step key (step id 'drift')"
assert_not_contains "$(title_line)" "987654321" "the TITLE carries NO run id (that is the #2706 duplicate-spam defect; the id belongs in the body)"
assert_contains "$CREATED" "labels[]=bug" "the label is passed through"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "BLOCKING repo-ahead migrations" "the body carries the gate's own report"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "gh workflow run supabase-deploy.yml --ref main" "the body carries the exact remediation"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "Check migration drift (fail-closed)" "the body names the failed step"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "987654321" "the body links the run (the detail lives there)"

echo "── case 1b: a >6000-byte report keeps its ACTIONABLE TAIL ─────────────"
# The gate prints its context (remote-ahead, non-conforming files, the
# warn-class preamble) BEFORE the actionable block and ENDS with the BLOCKING
# list and the ordered remediation. So the excerpt must be the TAIL of the
# report: `head -c 6000` keeps the noise and drops the one thing the alert
# exists to carry. This fixture is larger than the cap with the parts on
# opposite sides of it, so the direction is pinned and not merely asserted.
BIG_REPORT="$FIX/drift-report-big.txt"
{
  echo "check-migration-drift: repo migrations vs prod (project ybetwichurajbfswfeqa)"
  echo "  NOISE-BEFORE-THE-CUT-MARKER"
  i=0
  while [ "$i" -lt 400 ]; do
    echo "  warn-class line $i — index-only drift, non-blocking, listed for context only"
    i=$((i + 1))
  done
  echo "  BLOCKING repo-ahead migrations (pending in repo, NOT applied to prod):"
  echo "    - 20260926000002"
  echo "  Remediation: apply migrations first —"
  echo "    gh workflow run supabase-deploy.yml --ref main"
} > "$BIG_REPORT"
if [ "$(wc -c < "$BIG_REPORT")" -gt 6000 ]; then
  ok "the fixture exceeds the 6000-byte cap ($(wc -c < "$BIG_REPORT") bytes) — otherwise this case is vacuous"
else
  bad "the fixture must exceed the cap, or head and tail are indistinguishable"
fi
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$BIG_REPORT"
assert_eq "$RC" "0" "the alert still succeeds on a large report"
BIG_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")"
assert_contains "$BIG_BODY" "BLOCKING repo-ahead migrations" "the ACTIONABLE block survives the cap (tail, not head)"
assert_contains "$BIG_BODY" "gh workflow run supabase-deploy.yml --ref main" "and the ordered remediation survives — head -c drops both"
assert_not_contains "$BIG_BODY" "NOISE-BEFORE-THE-CUT-MARKER" "the preamble is what gets truncated away"
assert_contains "$BIG_BODY" "LAST 6000 bytes" "and the truncation is stated, so a reader knows why the head is missing"

echo "── case 3: the FIRST failing step is attributed ─────────────────────"
reset_case
run_alert --job deploy-api \
  --steps "parity=success verify-secrets=failure drift=skipped deploy=failure" \
  --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "the alert leg succeeded"
assert_contains "$(title_line)" "title=deploy-hosted: deploy-api failed at 'verify-secrets'" "the FIRST failing step owns the key"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "Verify secrets exist" "the body names that step"
assert_not_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "BLOCKING repo-ahead migrations" "no drift report on a non-drift failure"

echo "── case 4: a non-drift step gets its OWN key ───────────────────────"
reset_case
run_alert --job deploy-api --steps "parity=success deploy=failure" --drift-report "$DRIFT_REPORT"
assert_contains "$(title_line)" "title=deploy-hosted: deploy-api failed at 'deploy'" "a different step ⇒ a different key (a different finding)"
assert_not_contains "$(title_line)" "drift" "the drift key is not reused"

echo "── case 5: no step reported → job-level key, never a crash ─────────"
reset_case
run_alert --job deploy-api --steps "" --drift-report ""
assert_eq "$RC" "0" "an unattributed failure still alerts"
assert_contains "$(title_line)" "title=deploy-hosted: deploy-api failed" "the job-level fallback key is used"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "not reported" "the body says the step was not reported"

echo "── case 6: an existing machine-authored issue → Recurrence, no dup ──"
reset_case
export STUB_SEARCH_JSON="$(search_json 42 "deploy-hosted: deploy-api failed at 'drift'")"
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "the recurrence leg succeeded"
assert_eq "$(count_creates)" "0" "no duplicate issue was filed"
assert_eq "$(count_comments)" "1" "the occurrence was recorded on the existing issue"
assert_contains "$(commented)" "Recurrence #1" "the occurrence count is visible (#3907)"

echo "── case 16: a NON-Actions author is refused (the PAT trap) ─────────"
reset_case
export STUB_CREATE_LOGIN="some-human"
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "1" "a non-Actions author fails the step loudly"
assert_contains "$OUT" "REQUIRES the GitHub Actions token" "the misuse is named (it would duplicate every run)"

echo "── case 7: a failed dedupe search → refuse, file nothing ───────────"
reset_case
export STUB_SEARCH_FAIL=1
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "1" "a failed search fails the step"
assert_eq "$(count_creates)" "0" "nothing was filed (never a blind duplicate)"
assert_eq "$(count_calls 'CURL')" "0" "and no page was sent (the record comes first)"

echo "── case 8: no token → refuse BEFORE contacting GitHub ──────────────"
reset_case
unset GH_TOKEN GITHUB_TOKEN || true
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "1" "no token fails the step"
assert_eq "$(count_calls 'GH')" "0" "no GitHub call at all"
assert_contains "$OUT" "GH_TOKEN is not set" "the refusal is loud"
export GH_TOKEN="test-token"

echo "── case 9: an unreadable drift report still alerts, and says so ────"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/does-not-exist.txt"
assert_eq "$RC" "0" "the alert still files"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "report file was not readable" "the degradation is named in the body"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "does NOT re-run the gate" "and it explains why it does not re-read prod"

echo "── case 11/12/13: the two legs, and their order ────────────────────"
reset_case
export STUB_TG_FAIL=1
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "a Telegram failure does NOT fail the step (the ledger is the alert)"
assert_contains "$OUT" "telegram page NOT delivered" "the paging failure is still surfaced"
assert_eq "$(count_calls 'CURL')" "1" "the page was attempted"
reset_case
export STUB_CREATE_FAIL=1
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "1" "a ledger failure fails the step (a finding must never look filed)"
assert_eq "$(count_calls 'CURL')" "0" "and the nudge is skipped — the record comes first"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "the happy path succeeds"
assert_eq "$(count_calls 'CURL')" "1" "the page is sent when the ledger recorded the finding"

echo "── case 17: the other two sites get their OWN key and their own meaning ──"
# `packaging-smoke` and `post-deploy-verify` can each fail while `deploy-api` is
# SKIPPED, so a notifier only inside `deploy-api` would leave that silent. They
# are not interchangeable with it either: a red in one means the deploy was
# SKIPPED, in the other that a bad release is already live.
reset_case
run_alert --job packaging-smoke --steps "buildx=success build-image=failure pack-assert=skipped"
assert_eq "$RC" "0" "a failed pack smoke alerts"
assert_contains "$(title_line)" "title=deploy-hosted: packaging-smoke failed at 'build-image'" "the pack-smoke key names ITS job and step"
assert_not_contains "$(title_line)" "deploy-api" "and cannot be mistaken for a deploy-api failure"
PACK_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")"
assert_contains "$PACK_BODY" "Build hosted image" "the body names the failed step"
assert_contains "$PACK_BODY" "SKIPPED" "the body says the deploy was skipped (what a red HERE means)"
assert_not_contains "$PACK_BODY" "LIVE and unhealthy" "and not another job's meaning"
reset_case
run_alert --job post-deploy-verify --steps "health-gate=failure bypass-report=skipped machine-env=skipped"
assert_eq "$RC" "0" "a failed post-deploy verification alerts"
assert_contains "$(title_line)" "title=deploy-hosted: post-deploy-verify failed at 'health-gate'" "its own key"
POST_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")"
assert_contains "$POST_BODY" "LIVE and unhealthy" "the body says a bad release is live, NOT that the deploy failed"
assert_not_contains "$POST_BODY" "did **NOT** flip" "and not the deploy-api meaning (the deploy did succeed here)"
# The opener used to hard-code "the hosted deploy pipeline is blocked" for all
# three jobs — FALSE here, where the deploy already succeeded and the release is
# live. The job's own meaning must be the only claim in the body. (Round-4
# review, P2-1: a body that misleads the on-call reader is the defect this whole
# change exists to remove.)
assert_not_contains "$POST_BODY" "blocked" "the body makes no 'pipeline is blocked' claim for a job that does not block it"
assert_contains "$PACK_BODY" "SKIPPED" "(control) the pack-smoke body DOES state the consequence that is true there"

# ── case 17: the pasted report cannot break the body it is pasted into ───────
# The report is pasted into a fenced block. Two ways it could damage the issue:
# a fence run inside the excerpt closing the block early (the rest then renders as
# live markdown), and `tail -c` landing inside a multibyte character (invalid
# UTF-8 in a body GitHub then mangles). Both are pinned here. (Round-5, P3-2.)
FENCE_REPORT="$FIX/drift-report-fence.txt"
{
  echo "check-migration-drift: repo migrations vs prod"
  echo '  BLOCKING repo-ahead migrations (pending in repo, NOT applied to prod):'
  echo "    - 20260926000002   # see ###\`\`\` the runbook"
  echo '  ```'
  echo '  Remediation: apply migrations first —'
  echo '    gh workflow run supabase-deploy.yml --ref main'
} > "$FENCE_REPORT"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FENCE_REPORT"
assert_eq "$RC" "0" "the alert succeeds on a report containing a fence"
FENCE_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")"
assert_contains "$FENCE_BODY" "gh workflow run supabase-deploy.yml --ref main" "the remediation is still carried"
assert_contains "$FENCE_BODY" '````' "the block is fenced with MORE backticks than the excerpt contains, so it cannot close early"
# Byte-boundary: cut the report mid-character and require the body to stay valid
# UTF-8 (`iconv -c` drops the split sequence). The fixture is built so the 6000-
# byte tail cut lands EXACTLY inside a two-byte `é` — and that property is itself
# asserted, so this case cannot silently become vacuous.
python3 - "$FIX/drift-report-utf8.txt" <<'PY'
import sys, pathlib
head = b"check-migration-drift\n" + b"x" * 5900
payload = b"\n  BLOCKING repo-ahead migrations\n    - 20260926000002\n    gh workflow run supabase-deploy.yml --ref main\n"
# Derive the padding so the cut (`total - 6000`) lands on the SECOND byte of the
# 2-byte `é`: total = len(head) + 2 + len(payload) + pad + 1  and
# total - 6000 == len(head) + 1.
pad = (len(head) + 1 + 6000) - (len(head) + 2 + len(payload) + 1)
assert pad > 0, pad
pathlib.Path(sys.argv[1]).write_bytes(head + "é".encode() + payload + b"#" * pad + b"\n")
PY
if tail -c 6000 "$FIX/drift-report-utf8.txt" | python3 -c "import sys; sys.stdin.buffer.read().decode('utf-8')" 2>/dev/null; then
  bad "the utf8 fixture must be cut MID-CHARACTER, or this case is vacuous"
else
  ok "the fixture really is cut inside a multibyte character (the case is not vacuous)"
fi
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-utf8.txt"
assert_eq "$RC" "0" "the alert succeeds on a report cut mid-character"
if python3 -c "import sys,pathlib; pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')" "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null; then
  ok "the body is valid UTF-8 even though the cut landed inside a multibyte character"
else
  bad "the body is NOT valid UTF-8 — a split multibyte character reached the issue"
fi

# A job with no declared note still alerts, with an honest generic sentence
# rather than a wrong one (the workflow's site set is pinned by pytest).
reset_case
run_alert --job some-other-job --steps "whatever=failure"
assert_eq "$RC" "0" "an undeclared job still alerts"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "read the run" "and gets the generic sentence, not a wrong one"

echo "── case 14: usage errors ───────────────────────────────────────────"
reset_case
run_alert --steps "$DRIFT_STEPS"
assert_eq "$RC" "2" "--job is required"
run_alert --job deploy-api --bogus x
assert_eq "$RC" "2" "an unknown argument is a usage error"
assert_eq "$(count_calls 'GH')" "0" "a usage error contacts nothing"
# A value-taking flag with no value must be a usage error WITH a message, not a
# bare `shift 2` failure under `set -e` (exit 1, no output). (Round-5, P3-1.)
for flag in --job --steps --drift-report; do
  reset_case
  run_alert "$flag"
  assert_eq "$RC" "2" "$flag with no value is a usage error"
  assert_contains "$OUT" "needs a value" "and says so, instead of exiting 1 silently"
done
# `--help` documents itself and succeeds. This used to exit 2: `usage` returns 2
# and `set -e` aborted before the `return 0` beside it could run. (Round-4
# review, P3.)
reset_case
run_alert --help
assert_eq "$RC" "0" "--help exits 0, as it claims"
assert_eq "$(count_calls 'GH')" "0" "--help contacts nothing"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "✅ deploy-api-alert.test.sh — $PASS assertions passed"
  exit 0
fi
echo "❌ deploy-api-alert.test.sh — $FAIL of $((PASS + FAIL)) assertions failed"
exit 1
