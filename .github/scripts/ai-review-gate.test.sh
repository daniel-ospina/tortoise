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
#   plus: unsigned markers never pass, and the job must never gain an
#   `if:`/`needs:` (a SKIPPED required check reports Success).
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

# Structural tripwire: the required job must never become conditional.
if grep -qE '^    (if|needs):' "$WF"; then
    bad "gate job gained an if:/needs: — a SKIPPED required check reports Success"
else
    ok "gate job carries no if:/needs: (must always run)"
fi

# Structural tripwire (#2982): the step MUST declare GH_TOKEN. `gh` refuses to
# run in Actions without it, and the live diff hash depends on `gh api` — with
# no token, `live_diff_hash` is permanently empty and the entire diff-match
# path is dead code in production. The stubbed `gh` below bypasses gh's auth
# check, so NO runtime case in this harness can catch it; only this static
# assertion can. Not hypothetical: the first revision of #2982 shipped without
# the token and every runtime case still passed.
if awk '/^        env:/,/^        run: \|$/' "$WF" | grep -qE '^          GH_TOKEN:'; then
    ok "step env declares GH_TOKEN (gh api is usable in Actions)"
else
    bad "step env declares NO GH_TOKEN — gh api fails in Actions, so live_diff_hash is always empty and the diff-match path is dead code in production"
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

# Stubbed gh: the gate only ever asks for the PR body or the diff.
mkdir -p "$T/bin"
cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
if printf '%s' "$*" | grep -qF -- "application/vnd.github.v3.diff"; then
    [ "${STUB_DIFF_FAIL:-0}" = "1" ] && exit 1
    cat "${STUB_DIFF_FILE:?}"
    exit 0
fi
if printf '%s' "$*" | grep -qF -- "--jq .body"; then
    cat "${STUB_BODY_FILE:?}"
    exit 0
fi
exit 0
STUB
chmod +x "$T/bin/gh"

# Run the extracted gate with the body file as the PR body. The stub's API
# body fetch returns the same bytes, so both the env-var path and the REST
# path are exercised identically.
run_gate() { # <body-file>
    local bodyfile="$1" rcfile="$T/gate.rc" outfile="$T/gate.out"
    : > "$rcfile"; : > "$outfile"
    (
        export PATH="$T/bin:$PATH"
        export PR_BODY
        PR_BODY="$(cat "$bodyfile")"
        export HEAD_SHA="$HEAD" PR_NUMBER REPO_NAME GATE_SECRET="$KEY"
        export STUB_BODY_FILE="$bodyfile"
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

echo ""
echo "── Summary ───────────────────────────────────────────────────────"
echo "  PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || { echo "  ❌ FAILURES — fix and re-run"; exit 1; }
echo "  ✅ all checks passed"
exit 0
