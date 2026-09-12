#!/usr/bin/env bash
# ai-review-gate.test.sh — shell harness for the `ai-review-gate` workflow's
# signed-evidence validation logic (#2982).
#
# The gate lives inline in .github/workflows/ai-review-gate.yml (it cannot
# reference a repo script: it runs under pull_request_target and never checks
# out PR code). To test its shell logic we EXTRACT the step's `run: |` block
# verbatim and execute it with a stubbed `gh` and a test HMAC key. Nothing is
# posted to GitHub and the real key is never read.
#
# Run: bash .github/scripts/ai-review-gate.test.sh
#
# Coverage (#2982):
#   (a) marker whose `@ sha` is STALE but whose signed `diff=` matches the
#       live PR diff                       → PASS (verdict carried forward)
#   (b) marker whose sha AND diff= both mismatch → FAIL
#   (c) legacy marker (no diff=) with sha == head → PASS
#   (d) `diff=` present but the live diff hash is UNAVAILABLE → FAIL CLOSED
#       (and the sha-match path still works in that state)
#   (e) tampered `diff=` breaks the signature → FAIL
#   (f) unsigned marker → FAIL
#   (g) PR-body source: the REST fetch wins over the env var, falls back on
#       empty / `null` / a failed fetch, and a failed fetch never clobbers the
#       env-var body with gh's error envelope
#   (h) an EMPTY live diff is not hashed into a usable `sha256("")` value
#   (i) verdict=clean-micro is accepted on both acceptance paths
#   (j) marker selection: a wrong-diff or bad-signature candidate never masks
#       a following good one
#   (k) evidence is bound to this PR and this repo (other PR / other repo)
#   (l) a whitespace-padded GATE_SECRET is normalised
#   plus the static invariants: the required job must never gain
#   `if:`/`needs:`/`continue-on-error:` (any indentation or quoting), the
#   trigger must stay `pull_request_target` with no `paths:` filter, the
#   permissions must still grant `pull-requests: read`, the step must declare
#   `GH_TOKEN`, and the extracted block must be the real gate step.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WF="$REPO_ROOT/.github/workflows/ai-review-gate.yml"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_rc() {
    if [ "$GATE_RC" = "$1" ]; then ok "$2"; else bad "$2 (got rc=$GATE_RC want $1; out: $GATE_OUT)"; fi
}
assert_contains() {
    if printf '%s' "$GATE_OUT" | grep -qF -- "$2"; then ok "$1"; else bad "$1 (missing: $2; out: $GATE_OUT)"; fi
}
assert_not_contains() {
    if printf '%s' "$GATE_OUT" | grep -qF -- "$2"; then bad "$1 (unexpected: $2; out: $GATE_OUT)"; else ok "$1"; fi
}

T="$(mktemp -d /tmp/ai-review-gate-test.XXXXXX)"
trap 'rm -rf "$T"' EXIT

# ── Extract the gate's run block verbatim ────────────────────────────────
# `run: |` is at 8 spaces; content at 10. Blank lines belong to the block;
# the block ends at the first non-blank line indented less than 10.
RUN_BLOCK="$T/run-block.sh"
awk '
  /^        run: \|$/ { f=1; next }
  f {
    if ($0 ~ /^[[:space:]]*$/) { print ""; next }
    if ($0 ~ /^          /) { sub(/^          /, ""); print; next }
    exit
  }
' "$WF" > "$RUN_BLOCK"

if [ ! -s "$RUN_BLOCK" ]; then
    echo "  ❌ could not extract the run block from $WF"
    exit 1
fi
if ! grep -q 'AI_REVIEW_GATE_KEY' "$RUN_BLOCK" || ! grep -q 'live_diff_hash' "$RUN_BLOCK"; then
    echo "  ❌ extracted run block does not look like the ai-review-gate step (extraction is broken — fix the harness)"
    exit 1
fi
if bash -n "$RUN_BLOCK"; then ok "extracted run block parses (bash -n)"; else bad "extracted run block has a bash syntax error"; fi

# ── Structural tripwires ─────────────────────────────────────────────────
# These invariants live in the YAML AROUND the extracted run block and so are
# unreachable by any runtime case below. Each is asserted statically, and each
# assertion is mutation-checked (see the comments). Isolate the blocks first.
awk '/^  ai-review-gate:$/ { f=1; next } f && /^  [^ ]/ { exit } f { print }' \
    "$WF" > "$T/job-block.yml"
awk '/^on:/ { f=1; next } f && /^[^ ]/ { exit } f { print }' \
    "$WF" > "$T/on-block.yml"
awk '/^permissions:/ { f=1; next } f && /^[^ ]/ { exit } f { print }' \
    "$WF" > "$T/perm-block.yml"

# (1) The required job must never become conditional or non-blocking. A
#     SKIPPED check reports Success; `continue-on-error` swallows a failure.
#     Either one silently passes the required check without evaluating any
#     evidence. The pattern covers quoted and space-padded key forms
#     (`    if :`, `    "if":`) as well as the plain ones.
if grep -qE '^[[:space:]]*("?)(if|needs|continue-on-error)(")?[[:space:]]*:' "$T/job-block.yml"; then
    bad "gate job gained if:/needs:/continue-on-error: — a skipped or swallowed required check reports Success"
else
    ok "gate job carries no if:/needs:/continue-on-error: (must always run)"
fi

# (2) The trigger must stay `pull_request_target`. Under `pull_request`, a
#     same-repo PR executes ITS OWN copy of this workflow (it can `exit 0` and
#     self-certify the required check), and fork PRs stop receiving
#     AI_REVIEW_GATE_KEY.
if grep -qE '^  pull_request_target:[[:space:]]*$' "$T/on-block.yml" \
   && ! grep -qE '^  pull_request:[[:space:]]*$' "$T/on-block.yml"; then
    ok "trigger is pull_request_target only (PR code can never run the gate)"
else
    bad "trigger is not exactly pull_request_target — a same-repo PR could run its own gate definition"
fi

# (3) No path filter may gate the workflow: a path-filtered required check
#     that does not run is reported as Success.
if grep -qE '^[[:space:]]*(paths|paths-ignore)[[:space:]]*:' "$T/on-block.yml"; then
    bad "trigger gained a paths:/paths-ignore: filter — a path-filtered required check reports Success"
else
    ok "trigger carries no paths:/paths-ignore: filter"
fi

# (4) Reading a PR diff needs `pull-requests: read` (+ `contents: read` for the
#     repo). Without them `gh api` fails, live_diff_hash is never computed, and
#     the diff-match path is dead in production — silently restoring the #2982
#     deadlock while the GH_TOKEN assertion below still passes.
if grep -qE '^  contents:[[:space:]]*read[[:space:]]*$' "$T/perm-block.yml" \
   && grep -qE '^  pull-requests:[[:space:]]*read[[:space:]]*$' "$T/perm-block.yml"; then
    ok "permissions grant contents: read + pull-requests: read (gh api can read the diff)"
else
    bad "permissions do not grant contents: read + pull-requests: read — gh api cannot read the diff, so the diff-match path is dead code"
fi

# (5) Structural tripwire (#2982): the STEP must declare GH_TOKEN. `gh`
#     refuses to run in Actions without it, and the live diff hash depends on
#     `gh api` — with no token, `live_diff_hash` is permanently empty and the
#     entire diff-match path is dead code in production. The stubbed `gh`
#     below bypasses gh's auth check, so NO runtime case in this harness can
#     catch it; only this static assertion can. Not hypothetical: the first
#     revision of #2982 shipped without the token and every runtime case still
#     passed. The range is anchored at THIS step's name line: an `env:` block
#     on an earlier step must not be able to satisfy it.
if awk '/^      - name: Validate signed AI review evidence in PR body$/,/^        run: \|$/' "$WF" \
     | grep -qE '^          GH_TOKEN:'; then
    ok "gate step env declares GH_TOKEN (gh api is usable in Actions)"
else
    bad "gate step env declares NO GH_TOKEN — gh api fails in Actions, so live_diff_hash is always empty and the diff-match path is dead code in production"
fi

# ── Fixtures ─────────────────────────────────────────────────────────────
KEY="test-gate-key-2982"
REPO_NAME="daniel-ospina/tortoise"
PR_NUMBER="2982"
HEAD="$(printf 'a%.0s' $(seq 1 40))"    # current PR head
STALE="$(printf 'b%.0s' $(seq 1 40))"   # head the review was recorded at

DIFF_FILE="$T/diff.txt"
printf 'diff --git a/x b/x\n+hello\n' > "$DIFF_FILE"
DH="$(openssl dgst -sha256 < "$DIFF_FILE" | awk '{print $NF}')"
OTHER_DIFF_FILE="$T/diff2.txt"
printf 'diff --git a/x b/x\n+other\n' > "$OTHER_DIFF_FILE"
DH2="$(openssl dgst -sha256 < "$OTHER_DIFF_FILE" | awk '{print $NF}')"

sign() { printf '%s' "$1" | openssl dgst -sha256 -hmac "$KEY" | awk '{print $NF}'; }
legacy_marker() { # <sha>
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean @ $1 (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
diff_marker() { # <sha> <diff-hash>
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean @ $1 diff=$2 (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}

# Stubbed gh: the gate only ever asks for the PR body or the diff. Every
# invocation is logged so that a production call-shape change (e.g.
# `--jq .body` → `--jq .title`) fails loudly instead of silently yielding
# empty output that the fallback then hides.
mkdir -p "$T/bin"
cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
[ -n "${STUB_LOG:-}" ] && printf '%s\n' "$*" >> "$STUB_LOG"
if printf '%s' "$*" | grep -qF -- "application/vnd.github.v3.diff"; then
    [ "${STUB_DIFF_FAIL:-0}" = "1" ] && exit 1
    cat "${STUB_DIFF_FILE:?}"
    exit 0
fi
if printf '%s' "$*" | grep -qF -- "--jq .body"; then
    if [ "${STUB_BODY_FAIL:-0}" = "1" ]; then
        # gh's real failure mode: the HTTP error envelope on STDOUT, rc != 0.
        printf '{"message":"Not Found","documentation_url":"https://docs.github.com/rest","status":404}\n'
        exit 1
    fi
    cat "${STUB_BODY_FILE:?}"
    exit 0
fi
echo "stub gh: unrecognised invocation: $*" >&2
exit 97
STUB
chmod +x "$T/bin/gh"

# Run the extracted gate. `<env-body-file>` seeds the event-payload PR_BODY;
# `<rest-body-file>` (default: the same file) is what the REST body fetch
# returns. Passing DISTINCT files is what proves which source the gate
# actually consumed — serving identical bytes to both (as this harness used to)
# makes the branch selection unobservable.
run_gate() { # <env-body-file> [<rest-body-file>]
    local envfile="$1" restfile="${2:-$1}" rcfile="$T/gate.rc" outfile="$T/gate.out"
    : > "$rcfile"; : > "$outfile"; : > "$T/gh.log"
    (
        export PATH="$T/bin:$PATH"
        export PR_BODY
        PR_BODY="$(cat "$envfile")"
        export HEAD_SHA="$HEAD" PR_NUMBER REPO_NAME
        export GATE_SECRET="${GATE_SECRET_OVERRIDE:-$KEY}"
        export STUB_BODY_FILE="$restfile" STUB_LOG="$T/gh.log"
        rc=0
        bash "$RUN_BLOCK" >"$outfile" 2>&1 || rc=$?
        printf '%s' "$rc" > "$rcfile"
    )
    GATE_RC="$(cat "$rcfile")"
    GATE_OUT="$(cat "$outfile")"
}

echo "── (a) stale sha, diff= matches live → carry forward ──────────"
diff_marker "$STALE" "$DH" > "$T/body-a"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-a"
assert_rc 0 "(a) gate passes"
assert_contains "(a) names the diff-match path" "passed via diff match"
# Call-shape drift: the gate must keep asking for the diff media type and the
# PR body in the exact shape the stub recognises. Without this, a production
# call-shape change yields empty output that the env-var fallback hides.
if grep -qF -- "application/vnd.github.v3.diff" "$T/gh.log"; then
    ok "(a) gate requested the diff media type"
else
    bad "(a) gate never requested the diff media type (stub log: $(cat "$T/gh.log"))"
fi
if grep -qF -- "--jq .body" "$T/gh.log"; then
    ok "(a) gate requested the PR body"
else
    bad "(a) gate never requested the PR body (stub log: $(cat "$T/gh.log"))"
fi
assert_not_contains "(a) no unrecognised gh invocation" "unrecognised invocation"

echo "── (b) sha AND diff= both mismatch → fail ─────────────────────"
diff_marker "$STALE" "$DH2" > "$T/body-b"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-b"
assert_rc 1 "(b) gate fails"
assert_contains "(b) reports the diff divergence" "does not match this PR's current diff hash"
# legacy stale marker (no diff field at all) also fails, with the stale message.
legacy_marker "$STALE" > "$T/body-b2"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-b2"
assert_rc 1 "(b2) legacy stale marker fails"
assert_contains "(b2) reports stale evidence" "AI review evidence is stale"

echo "── (c) legacy marker, sha == head → pass (backward compat) ────"
legacy_marker "$HEAD" > "$T/body-c"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-c"
assert_rc 0 "(c) gate passes"
assert_contains "(c) normal pass message" "AI review gate passed:"
if printf '%s' "$GATE_OUT" | grep -qF "passed via diff match"; then
    bad "(c) legacy pass must not claim the diff path"
else
    ok "(c) legacy pass does not claim the diff path"
fi

echo "── (d) diff= present but live hash unavailable → fail closed ──"
diff_marker "$STALE" "$DH" > "$T/body-d"
STUB_DIFF_FILE="$DIFF_FILE" STUB_DIFF_FAIL=1 run_gate "$T/body-d"
assert_rc 1 "(d) gate fails closed"
assert_contains "(d) says the live hash was unavailable" "live diff hash could not be computed"
# ...but the sha-match path must remain usable when the API is down.
legacy_marker "$HEAD" > "$T/body-d2"
STUB_DIFF_FILE="$DIFF_FILE" STUB_DIFF_FAIL=1 run_gate "$T/body-d2"
assert_rc 0 "(d2) sha-match still passes without a live diff hash"

echo "── (e) tampered diff= breaks the signature → fail ─────────────"
# Sign over diff=DH2, then swap in the live diff=DH without re-signing.
diff_marker "$STALE" "$DH2" | sed "s/diff=$DH2/diff=$DH/" > "$T/body-e"
if ! grep -qF "diff=$DH " "$T/body-e"; then
    bad "(e) fixture did not swap the diff= field"
else
    STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-e"
    assert_rc 1 "(e) gate fails"
    assert_contains "(e) reports a signature mismatch" "signature mismatch"
fi

echo "── (f) unsigned marker → never passes ─────────────────────────"
printf 'review recorded: reviews/%s.json verdict=clean @ %s (%s)\n' "$PR_NUMBER" "$HEAD" "$REPO_NAME" > "$T/body-f"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-f"
assert_rc 1 "(f) unsigned marker fails"
assert_contains "(f) explains it is not signed" "not signed"

echo "── (g) PR-body source: REST fetch vs env-var fallback ─────────"
# g1: only the REST body carries the marker → the gate consumed the REST fetch.
diff_marker "$HEAD" "$DH" > "$T/body-g-rest"
printf 'no marker here\n' > "$T/body-g-env"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-g-env" "$T/body-g-rest"
assert_rc 0 "(g1) gate uses the REST-fetched body"
# g2: REST returns an empty body → fall back to the env var.
printf '' > "$T/body-g-empty"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-g-rest" "$T/body-g-empty"
assert_rc 0 "(g2) falls back to the env-var body when the REST body is empty"
# g3: REST returns the literal `null` → fall back to the env var.
printf 'null\n' > "$T/body-g-null"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-g-rest" "$T/body-g-null"
assert_rc 0 "(g3) falls back to the env-var body on a literal null"
# g4: REST fetch FAILS with gh's real error envelope on stdout and rc != 0 →
#     the gate must fall back, NOT adopt the error JSON as the body.
STUB_DIFF_FILE="$DIFF_FILE" STUB_BODY_FAIL=1 run_gate "$T/body-g-rest" "$T/body-g-rest"
assert_rc 0 "(g4) a failed REST fetch does not clobber the env-var body"
assert_contains "(g4) warns about the failed body fetch" "PR body fetch via the REST API failed"
assert_not_contains "(g4) does not misreport a failed fetch as missing evidence" "No AI review evidence found"
# g5: the REST body is authoritative when both are present and differ.
printf 'no marker here either\n' > "$T/body-g-rest-empty"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-g-rest" "$T/body-g-rest-empty"
assert_rc 1 "(g5) the REST body wins over the env-var body"

echo "── (h) empty live diff → fail closed, never sha256(\"\") ────────"
: > "$T/empty-diff.txt"
diff_marker "$STALE" "$DH" > "$T/body-h"
STUB_DIFF_FILE="$T/empty-diff.txt" run_gate "$T/body-h"
assert_rc 1 "(h) an empty live diff yields no usable hash"
assert_contains "(h) says the live hash was unavailable" "live diff hash could not be computed"
EMPTY_H="$(printf '' | openssl dgst -sha256 | awk '{print $NF}')"
diff_marker "$STALE" "$EMPTY_H" > "$T/body-h2"
STUB_DIFF_FILE="$T/empty-diff.txt" run_gate "$T/body-h2"
assert_rc 1 "(h2) sha256 of the empty string is never accepted as a live diff hash"

echo "── (i) verdict=clean-micro on both acceptance paths ───────────"
micro_sha_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-micro @ ${HEAD} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
micro_diff_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-micro @ ${STALE} diff=${DH} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
micro_sha_marker > "$T/body-i1"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i1"
assert_rc 0 "(i1) clean-micro sha-match passes"
micro_diff_marker > "$T/body-i2"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i2"
assert_rc 0 "(i2) clean-micro diff-match passes"

echo "── (j) marker selection: a bad candidate never masks a good one ─"
# j1: a correctly-signed marker for the WRONG diff precedes the good one.
{ diff_marker "$STALE" "$DH2"; diff_marker "$STALE" "$DH"; } > "$T/body-j1"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-j1"
assert_rc 0 "(j1) skips a wrong-diff candidate and accepts the matching one"
assert_contains "(j1) accepted the matching marker" "diff=$DH ("
# j2: a bad-signature candidate precedes the good one.
{
    printf 'review recorded: reviews/%s.json verdict=clean @ %s diff=%s (%s) sig=%s\n' \
        "$PR_NUMBER" "$STALE" "$DH" "$REPO_NAME" "$(printf 'f%.0s' $(seq 1 64))"
    diff_marker "$STALE" "$DH"
} > "$T/body-j2"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-j2"
assert_rc 0 "(j2) skips a bad-signature candidate and accepts the next valid one"

echo "── (k) evidence is bound to this PR and this repo ─────────────"
# k1: a validly-signed marker for ANOTHER PR cannot satisfy this one.
other_pr_m="review recorded: reviews/9999.json verdict=clean @ ${HEAD} (${REPO_NAME})"
printf '%s sig=%s\n' "$other_pr_m" "$(sign "$other_pr_m")" > "$T/body-k1"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-k1"
assert_rc 1 "(k1) a marker recorded for another PR is rejected"
assert_contains "(k1) reports no evidence for this PR" "No AI review evidence found in PR #${PR_NUMBER}"
# k2: a validly-signed marker for ANOTHER REPO is rejected, and the diagnostic
#     names the repo instead of blaming the diff (two identical hashes).
other_repo_m="review recorded: reviews/${PR_NUMBER}.json verdict=clean @ ${STALE} diff=${DH} (other-org/other-repo)"
printf '%s sig=%s\n' "$other_repo_m" "$(sign "$other_repo_m")" > "$T/body-k2"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-k2"
assert_rc 1 "(k2) a marker recorded for another repo is rejected"
assert_contains "(k2) names the recorded repo" "was found for other-org/other-repo"

echo "── (l) a whitespace-padded GATE_SECRET is normalised ─────────"
# The deployed key file is 64 hex chars PLUS a trailing newline; the gate
# strips whitespace before HMACing. Lose that line and every signature breaks,
# while every whitespace-free fixture above still passes.
diff_marker "$HEAD" "$DH" > "$T/body-l"
GATE_SECRET_OVERRIDE=$'\n  '"$KEY"$'  \n'
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-l"
unset GATE_SECRET_OVERRIDE
assert_rc 0 "(l) a whitespace-padded key is normalised"

echo ""
echo "── Summary ───────────────────────────────────────────────────────"
echo "  PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || { echo "  ❌ FAILURES — fix and re-run"; exit 1; }
echo "  ✅ all checks passed"
exit 0
