#!/usr/bin/env bash
# ai-review-gate.test.sh — shell harness for the `ai-review-gate` workflow's
# signed-evidence validation logic (#2982).
#
# The gate lives inline in .github/workflows/ai-review-gate.yml (this workflow
# has no checkout step, so it cannot reference a repo script). To test its
# shell logic we EXTRACT the step's `run: |` block
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
#       for a STALE sha (d), while the sha-match path still works in that state
#       for a legacy marker (d2) AND for a HEAD-BOUND `diff=` marker (d3) —
#       whose head binding does not consult the live diff at all
#   (e) tampered `diff=` breaks the signature → FAIL
#   (f) unsigned marker → FAIL
#   (g) PR-body source: the REST fetch wins over the env var, falls back on
#       empty / `null` / a failed fetch, and a failed fetch never clobbers the
#       env-var body with gh's error envelope
#   (h) an EMPTY live diff is not hashed into a usable `sha256("")` value
#   (i) verdict=clean-micro is accepted on both acceptance paths
#   (j) marker selection: a wrong-diff or bad-signature candidate never masks
#       a following good one
#   (k) evidence is bound to this PR and this repo (other PR / other repo),
#       including a diff-bearing marker replayed from another PR with an
#       IDENTICAL diff= (the diff-match arm matches on the hash alone, so the
#       PR anchor is the only thing preventing the replay)
#   (l) a whitespace-padded GATE_SECRET is normalised
#   (m) a whitespace-ONLY GATE_SECRET fails closed (it normalizes to the EMPTY
#       public key — never a pass)
#   (n) a malformed trailing line cannot hijack the wrong-repo diagnostic and
#       suppress the accurate stale/diff explanation
#   (o) normalized-digest acceptance (#1362): a marker carrying the NORMALIZED
#       digest of a base-moved diff carries forward (the D1 win); the legacy
#       RAW digest is STILL accepted for the same diff (backward compat); a
#       marker matching neither is rejected; a normalized diff= still fails
#       closed when the fetch fails; and the head-bound path (rule (a)) is
#       unchanged
#   (p) a whitespace-only change changes the normalized digest (anti-
#       `patch-id` pin — `git patch-id --stable` would MATCH and falsely
#       carry a stale verdict forward)
#   (q) the #1362 binary carve-out (amendment to the 2026-09-23 ruling, per
#       the ruling's own rationale — agent-infra#1362 comment 5806797023):
#       two DIFFERENT binaries at the same path normalize to DIFFERENT
#       digests (the fail-open is closed, end to end at the gate), a text
#       base-move keeps the SAME normalized digest (no #4969 regression), a
#       binary base-move keeps it too, binary add/delete/mode-change entries
#       keep the `index` line, a hunk-less NON-binary entry keeps it as well
#       (the predicate is HUNK PRESENCE, not binary-marker presence), the
#       legacy raw-hash arm still accepts, and everything still fails closed
#       when the diff cannot be fetched
#   (q2) multi-entry ENTRY-SCOPING: two distinct binaries sharing a diff with a
#       hunk-bearing entry keep DISTINCT digests (binary before AND after the
#       hunk), and the multi-entry normalized bytes are pinned — a normalizer
#       that shares hunk state across entry boundaries, or drops an entry on
#       flush, fails HERE (every `index KEPT` vector above is single-entry)
#   plus direct vector tests of the extracted `normalize_review_diff`
#       function against the #1362 spec (ENTRY-SCOPED index-line drop —
#       dropped only when the entry carries a hunk, kept verbatim otherwise —
#       hunk-header rewrite with the absent-count default of 1, mode-width
#       boundary, and final-newline preservation)
#   plus the static invariants: the required job must never gain
#   `if:`/`needs:`/`continue-on-error:` (any indentation or quoting), the
#   trigger must be EXACTLY `pull_request_target` (asserted over `pull_request*`
#   TOKENS, so `pull_request: {}`, a space before the colon, a quoted key, a
#   flow mapping or an inline `on: pull_request` cannot evade), the job must
#   carry no job-level `permissions:` override, the workflow must
#   grant `contents: read` + `pull-requests: read` and no `paths:` filter, the
#   step must declare `GH_TOKEN`, no unexpected `gh` call shape may occur, and
#   the extracted block must be the real gate step.
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

# ── normalize_review_diff: direct vector tests (#1362) ────────────────────
# The gate normalizes the fetched diff before hashing it. Extract the helper
# from the SAME run block and drive the #1362 spec vectors through it directly,
# so a subtly wrong sed expression fails HERE with a named vector instead of
# only as an opaque digest mismatch in the end-to-end cases below.
NORM_FN="$T/normalize-fn.sh"
awk '/^normalize_review_diff\(\) \{/ { f=1 } f { print } f && /^\}$/ { exit }' \
    "$RUN_BLOCK" > "$NORM_FN"
if [ -s "$NORM_FN" ] && grep -q 'index \[0-9a-f\]' "$NORM_FN"; then
    ok "extracted normalize_review_diff from the run block"
    # shellcheck disable=SC1090
    source "$NORM_FN"
else
    bad "could not extract normalize_review_diff from the run block (the vector tests would pass vacuously)"
fi

check_norm() { # <input> <expected-output>
    local got
    got="$(printf '%s' "$1" | normalize_review_diff)"
    if [ "$got" = "$2" ]; then
        ok "normalize [$1] -> [$2]"
    else
        bad "normalize [$1] -> [$got] (want [$2])"
    fi
}
# Required #1362 vectors.
check_norm '@@ -1,5 +1,7 @@' '@@ -0,5 +0,7 @@'
check_norm '@@ -12 +12 @@' '@@ -0,1 +0,1 @@'
check_norm '@@ -12,0 +13,4 @@ def f():' '@@ -0,0 +0,4 @@ def f():'
# The index line is dropped ONLY inside an entry that carries a hunk (#1362
# amendment): the hunk content already carries the change.
check_norm $'diff --git a/x b/x\nindex 1a2b3c4..5d6e7f8 100644\n@@ -1,2 +1,3 @@\n ctx\n+added' \
           $'diff --git a/x b/x\n@@ -0,2 +0,3 @@\n ctx\n+added'
check_norm $'diff --git a/x b/x\nindex 1a2b3c4..5d6e7f8\n@@ -1 +1 @@\n-a\n+b' \
           $'diff --git a/x b/x\n@@ -0,1 +0,1 @@\n-a\n+b'
# ...and KEPT in a hunk-less entry, where no hunk content carries it. The
# predicate is HUNK PRESENCE, not the presence of a binary marker: a binary
# entry and a hunk-less empty-file add/delete BOTH keep the line.
check_norm 'index 1a2b3c4..5d6e7f8 100644' 'index 1a2b3c4..5d6e7f8 100644'
check_norm 'index 1a2b3c4..5d6e7f8' 'index 1a2b3c4..5d6e7f8'
check_norm $'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ' \
           $'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ'
check_norm $'diff --git a/empty b/empty\nnew file mode 100644\nindex 0000000..e69de29' \
           $'diff --git a/empty b/empty\nnew file mode 100644\nindex 0000000..e69de29'
# One side missing its count defaults that side to 1; the other count is kept.
check_norm '@@ -5,3 +9 @@ rest' '@@ -0,3 +0,1 @@ rest'
check_norm '@@ -5 +9,3 @@ rest' '@@ -0,1 +0,3 @@ rest'
# Mode width is EXACTLY six octal digits: seven digits is not git's index line.
check_norm 'index 1a2b3c4..5d6e7f8 1006440' 'index 1a2b3c4..5d6e7f8 1006440'
check_norm 'index 1a2b3c4..5d6e7f8 10064' 'index 1a2b3c4..5d6e7f8 10064'
# ...and the mode-width boundary holds INSIDE a hunk entry too: a 7-digit mode
# is not git's index line, so it is passed through even when a hunk is present.
check_norm $'diff --git a/x b/x\nindex 1a2b3c4..5d6e7f8 1006440\n@@ -1 +1 @@\n-a\n+b' \
           $'diff --git a/x b/x\nindex 1a2b3c4..5d6e7f8 1006440\n@@ -0,1 +0,1 @@\n-a\n+b'
# Non-hex and non-index lines pass through unchanged.
check_norm 'diff --git a/x b/x' 'diff --git a/x b/x'
# Final-newline state is preserved (sed); awk would append one and shift the
# digest, so this pins the choice of tool as much as the rewrite itself.
nl_missing="$(printf 'x' | normalize_review_diff | wc -c | tr -d ' ')"
nl_present="$(printf 'x\n' | normalize_review_diff | wc -c | tr -d ' ')"
if [ "$nl_missing" = "1" ] && [ "$nl_present" = "2" ]; then
    ok "normalization preserves the final-newline state (missing stays missing)"
else
    bad "normalization changed the final-newline state (missing -> '$nl_missing' bytes, present -> '$nl_present' bytes)"
fi

# ── (q2) multi-entry entry-scoping (the #1362 amendment, pinned) ──────────
# Every `index KEPT` vector above is a SINGLE-entry fixture, so a normalizer
# whose hunk state leaks ACROSS entry boundaries — a whole-diff `has_hunk`, or
# an entry reset that drops the preceding entry — satisfies them all. With a
# hunk-bearing entry sharing the diff, such a normalizer makes two DISTINCT
# binaries normalize IDENTICALLY: exactly the unreviewed-binary fail-open this
# amendment closes. These pairs differ ONLY in a binary entry's `index` line,
# in BOTH orders, and the multi-entry normalized BYTES are pinned verbatim.
HUNK_ENTRY=$'diff --git a/t.txt b/t.txt\nindex aaaaaaa..bbbbbbb 100644\n--- a/t.txt\n+++ b/t.txt\n@@ -1,3 +1,4 @@\n ctx\n+added\n ctx2'
BIN_V1_ENTRY=$'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ'
BIN_V2_ENTRY=$'diff --git a/f.bin b/f.bin\nindex 3333333..4444444 100644\nBinary files a/f.bin and b/f.bin differ'
# binary BEFORE hunk
printf '%s\n%s\n' "$BIN_V1_ENTRY" "$HUNK_ENTRY" > "$T/multi-bh-v1.diff"
printf '%s\n%s\n' "$BIN_V2_ENTRY" "$HUNK_ENTRY" > "$T/multi-bh-v2.diff"
# hunk BEFORE binary
printf '%s\n%s\n' "$HUNK_ENTRY" "$BIN_V1_ENTRY" > "$T/multi-hb-v1.diff"
printf '%s\n%s\n' "$HUNK_ENTRY" "$BIN_V2_ENTRY" > "$T/multi-hb-v2.diff"
for _order in bh hb; do
    _m1="$(normalize_review_diff < "$T/multi-${_order}-v1.diff" | openssl dgst -sha256 | awk '{print $NF}')"
    _m2="$(normalize_review_diff < "$T/multi-${_order}-v2.diff" | openssl dgst -sha256 | awk '{print $NF}')"
    if [ -n "$_m1" ] && [ "$_m1" != "$_m2" ]; then
        ok "(q2) distinct binaries keep DISTINCT digests in a multi-entry diff ($_order)"
    else
        bad "(q2) entry-scoping lost: distinct binaries normalized IDENTICALLY in a multi-entry diff ($_order) — $_m1"
    fi
done
# Pin the multi-entry bytes. Resetting the entry buffer BEFORE `flush` drops the
# binary ENTRY entirely; a whole-diff (precomputed) `has_hunk` drops the binary
# entry's kept `index` line. A STREAMING leak (initialise `has_hunk` once, never
# reset) keeps this `bh` vector green and is caught by the `hb` digest case
# above — which is why BOTH orders are asserted. This vector is what catches the
# entry-drop and precomputed variants, which would pass every single-entry
# assertion above.
check_norm "$(printf '%s\n%s\n' "$BIN_V1_ENTRY" "$HUNK_ENTRY")" \
           $'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ\ndiff --git a/t.txt b/t.txt\n--- a/t.txt\n+++ b/t.txt\n@@ -0,3 +0,4 @@\n ctx\n+added\n ctx2'

# ── Structural tripwires ─────────────────────────────────────────────────
# These invariants live in the YAML AROUND the extracted run block and so are
# unreachable by any runtime case below. Each is asserted statically, and each
# assertion is mutation-checked (see the comments). Isolate the blocks first.
# The anchors allow quoted keys and whitespace before the colon, because a
# quoted-but-identical key (`  "ai-review-gate":`) is the same job. An anchor
# that only matched the unquoted spelling extracted an EMPTY block for the
# others, and an empty block makes the tripwires below pass VACUOUSLY (they
# grep a 0-byte file). Fail closed on an empty extraction, like RUN_BLOCK.
awk '/^  ["'\'']?ai-review-gate["'\'']?[[:space:]]*:/ { f=1; next } f && /^  [^ ]/ { exit } f { print }' \
    "$WF" > "$T/job-block.yml"
# Print the `on:` line itself too: an inline `on: pull_request` is a valid
# spelling and its trigger token lives on that line.
awk '/^["'\'']?on["'\'']?[[:space:]]*:/ { f=1; print; next } f && /^[^ ]/ { exit } f { print }' \
    "$WF" > "$T/on-block.yml"
awk '/^["'\'']?permissions["'\'']?[[:space:]]*:/ { f=1; next } f && /^[^ ]/ { exit } f { print }' \
    "$WF" > "$T/perm-block.yml"
for _blk in job on perm; do
    if [ ! -s "$T/${_blk}-block.yml" ]; then
        echo "  ❌ could not extract the ${_blk} block from $WF — the structural tripwires would pass vacuously"
        exit 1
    fi
done

# (1) The required job must never become conditional or non-blocking. A
#     SKIPPED check reports Success; `continue-on-error` swallows a failure.
#     Either one silently passes the required check without evaluating any
#     evidence. The pattern matches the key as a TOKEN after an optional `?`
#     (YAML explicit key: `? if` / `: false` — valid Actions YAML that GitHub's
#     own parser accepts) or quote, and accepts a line that ENDS at the key,
#     because an explicit key puts its `:` on the NEXT line (cycle-3 review).
#     Text matching still cannot enumerate every spelling: the parsed-document
#     assertions in tests/test_ai_review_gate_contract.py are spelling-proof.
#     Plain, quoted (`    "if":`), space-padded (`    if :`) and explicit-key
#     (`    ? if`) forms are all matched.
if grep -qE '(^|[[:space:]{,?])["'\'']?(if|needs|continue-on-error)["'\'']?[[:space:]]*(:|$)' "$T/job-block.yml"; then
    bad "gate job gained if:/needs:/continue-on-error: — a skipped or swallowed required check reports Success"
else
    ok "gate job carries no if:/needs:/continue-on-error: (must always run)"
fi

# (2) The trigger must be EXACTLY `pull_request_target`. Under `pull_request`, a
#     same-repo PR executes ITS OWN copy of this workflow (it can `exit 0` and
#     self-certify the required check), and fork PRs stop receiving
#     AI_REVIEW_GATE_KEY. Assert the SET of `pull_request*` TOKENS in the `on:`
#     block, not a spelled-out key: a key-spelling matcher misses `pull_request
#     : {}` (space before colon), `{pull_request: {...}}` (flow mapping), a
#     quoted key, and an inline `on: pull_request` — each of which is the
#     banned trigger under a different, VALID spelling (review finding, cycle
#     2). Token-matching is spelling-agnostic; comments are stripped first so
#     prose cannot confuse it.
on_tokens="$(sed 's/#.*//' "$T/on-block.yml" | grep -oE 'pull_request[A-Za-z_-]*' | sort -u | sed '/^$/d')"
if [ "$on_tokens" = "pull_request_target" ]; then
    ok "trigger is pull_request_target only (PR code can never run the gate)"
else
    bad "trigger is not exactly pull_request_target (pull_request* tokens found: $(printf '%s' "$on_tokens" | tr '\n' ',') ) — a same-repo PR could run its own gate definition"
fi

# (3) No path filter may gate the workflow. A required check whose workflow is
#     skipped by path filtering stays PENDING forever, so the PR can never
#     merge (a self-inflicted deadlock rather than a silent bypass — an earlier
#     version of this message claimed it reports Success; corrected in the
#     cycle-3 review). Matched as a token, so a flow mapping on the trigger
#     line and an explicit key (`? paths`) are both caught.
if grep -qE '(^|[[:space:]{,?])["'\'']?(paths|paths-ignore)["'\'']?[[:space:]]*(:|$)' "$T/on-block.yml"; then
    bad "trigger gained a paths:/paths-ignore: filter — a path-filtered required check stays Pending, so the PR can never merge"
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
# A JOB-level `permissions:` is valid YAML and OVERRIDES the workflow block for
# that job, so a job-level grant that drops `pull-requests: read` leaves the
# assertion above green while making the diff-match path dead in production.
if grep -qE '(^|[[:space:]{,?])["'\'']?permissions["'\'']?[[:space:]]*(:|$)' "$T/job-block.yml"; then
    bad "gate job carries a job-level permissions: override — it can drop pull-requests: read while the workflow-level grant still looks fine"
else
    ok "gate job carries no job-level permissions: override (workflow grant is effective)"
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

# ── #1362 normalized-digest fixtures ──────────────────────────────────────
# A realistic diff whose `index` line and hunk headers move on a base update
# while every changed line stays byte-identical. The raw and normalized
# digests differ, so a marker keyed to one is refused by an unnormalized gate
# and accepted by this one. Both digests are pinned as LITERALS, independently
# computed, so a broken normalizer cannot pass by agreeing with itself.
DIFF_NORM_FILE="$T/diff-norm.txt"
cat > "$DIFF_NORM_FILE" <<'DIFFEOF'
diff --git a/x.py b/x.py
index 1111111..2222222 100644
--- a/x.py
+++ b/x.py
@@ -1,4 +1,6 @@
 ctx1
-old
+new
+added
 ctx2
@@ -20,3 +22,2 @@ def f():
 a
-b
+c
DIFFEOF
DH_RAW="a8d3ddc90a83487e02fe2d9af96b348dc113cd7b5dc80babcaf5d4bd2f2b213d"
DH_NORM_1362="efba06e08d0a4c1832f5ec0afdd8a142ce4f7b4b56777f1810f1657ecc22439e"
# Fixtures for the anti-`patch-id` pin: identical except for the whitespace in
# one changed line (`git patch-id --stable` ignores whitespace and MATCHES).
WS_A_FILE="$T/ws-a.txt"
WS_B_FILE="$T/ws-b.txt"
printf 'diff --git a/y b/y\n--- a/y\n+++ b/y\n@@ -1,1 +1,1 @@\n-hello\n+hello world\n' > "$WS_A_FILE"
printf 'diff --git a/y b/y\n--- a/y\n+++ b/y\n@@ -1,1 +1,1 @@\n-hello\n+hello  world\n' > "$WS_B_FILE"

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
[ -n "${STUB_UNRECOGNISED:-}" ] && printf '%s\n' "$*" >> "$STUB_UNRECOGNISED"
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
    : > "$rcfile"; : > "$outfile"; : > "$T/gh.log"; : > "$T/gh-unrecognised"
    (
        export PATH="$T/bin:$PATH"
        export PR_BODY
        PR_BODY="$(cat "$envfile")"
        export HEAD_SHA="$HEAD" PR_NUMBER REPO_NAME
        export GATE_SECRET="${GATE_SECRET_OVERRIDE:-$KEY}"
        export STUB_BODY_FILE="$restfile" STUB_LOG="$T/gh.log"
        export STUB_UNRECOGNISED="$T/gh-unrecognised"
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
# Both the diff fetch and the body fetch must be scoped to THIS repo and THIS
# PR. If either ever drifts to a different PR, the diff-match arm would
# validate a marker against another PR's diff — the whole binding is void. The
# stub serves whatever it is given, so only the CALL SHAPE can catch this.
if [ "$(grep -cE -- "repos/${REPO_NAME}/pulls/${PR_NUMBER}([^0-9]|$)" "$T/gh.log")" -ge 2 ]; then
    ok "(a) both gh calls target repos/${REPO_NAME}/pulls/${PR_NUMBER} (number-boundary exact)"
else
    bad "(a) a gh call is not scoped to repos/${REPO_NAME}/pulls/${PR_NUMBER} (stub log: $(cat "$T/gh.log"))"
fi
# The stub's diagnostic goes to STDERR, which the gate discards (`2>/dev/null`)
# on both calls — so grepping GATE_OUT for it can never fail. Assert against the
# stub's own side-channel file, so an UNEXPECTED gh call shape is detected
# instead of silently yielding empty output the env-var fallback then hides.
if [ ! -s "$T/gh-unrecognised" ]; then
    ok "(a) no unrecognised gh invocation"
else
    bad "(a) unrecognised gh invocation(s): $(cat "$T/gh-unrecognised")"
fi

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
# d3: the same holds for a HEAD-BOUND `diff=` marker. Rule (a) matches on the
#     sha ALONE — the live diff hash is never consulted — so a diff= segment
#     cannot make rule (a) fail closed. The marker here carries DH2, a hash
#     that does NOT match the live diff: the pass can only come from the sha
#     binding, which is the intentional pre-#2982 behaviour. Pinned
#     deliberately: a "restore fail-closed" edit that gates rule (a) on
#     `[ -n "$live_diff_hash" ]` would break the normal path in production,
#     and must fail HERE instead.
diff_marker "$HEAD" "$DH2" > "$T/body-d3"
STUB_DIFF_FILE="$DIFF_FILE" STUB_DIFF_FAIL=1 run_gate "$T/body-d3"
assert_rc 0 "(d3) a head-bound diff= marker passes when the live diff fetch fails"
if printf '%s' "$GATE_OUT" | grep -qF "passed via diff match"; then
    bad "(d3) head-bound pass must not claim the diff path"
else
    ok "(d3) head-bound pass does not claim the diff path"
fi

echo "── (e) tampered diff= breaks the signature → fail ─────────────"
# Sign over diff=DH2, then swap in the live diff=DH without re-signing.
diff_marker "$STALE" "$DH2" | sed "s/diff=$DH2/diff=$DH/" > "$T/body-e"
if ! grep -qF "diff=$DH " "$T/body-e"; then
    bad "(e) fixture did not swap the diff= field"
else
    STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-e"
    assert_rc 1 "(e) gate fails"
    assert_contains "(e) reports an HMAC mismatch (#3076 wording)" "HMAC mismatch"
fi

echo "── (f) unsigned marker → never passes ─────────────────────────"
printf 'review recorded: reviews/%s.json verdict=clean @ %s (%s)\n' "$PR_NUMBER" "$HEAD" "$REPO_NAME" > "$T/body-f"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-f"
assert_rc 1 "(f) unsigned marker fails"
assert_contains "(f) explains it is not signed (#3076 wording)" "is UNSIGNED"

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

echo "── (i3) verdict=clean-low on both acceptance paths (#4755) ────"
clean_low_sha_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-low @ ${HEAD} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
clean_low_diff_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-low @ ${STALE} diff=${DH} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
clean_low_sha_marker > "$T/body-i3a"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i3a"
assert_rc 0 "(i3a) clean-low sha-match passes"
clean_low_diff_marker > "$T/body-i3b"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i3b"
assert_rc 0 "(i3b) clean-low diff-match passes"

# The vocabulary must stay CLOSED (#4755). A verdict OUTSIDE it is refused even
# with a valid HMAC over the marker text — the signature covers the verdict
# string, so a relabel verifies only if the recorder signed that relabel, and
# the regex must not admit it in the first place.
oov_sha_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-anything @ ${HEAD} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
oov_diff_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-anything @ ${STALE} diff=${DH} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
oov_sha_marker > "$T/body-i3c"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i3c"
assert_rc 1 "(i3c) an out-of-vocabulary verdict is refused at the head"
oov_diff_marker > "$T/body-i3d"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i3d"
assert_rc 1 "(i3d) an out-of-vocabulary verdict is refused on the diff-match path"

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
# k3: PR-anchoring of the diff-match arm. A validly-signed marker for ANOTHER PR
#     whose signed diff= EQUALS this PR's live diff must not be replayable here.
#     The diff-match arm matches on the hash alone, so without the
#     `reviews/<PR_NUMBER>.json` anchor an identical diff on an unrelated PR
#     (e.g. a reverted/cherry-picked change) would cross-satisfy this one.
other_pr_diff_m="review recorded: reviews/9999.json verdict=clean @ ${STALE} diff=${DH} (${REPO_NAME})"
printf '%s sig=%s\n' "$other_pr_diff_m" "$(sign "$other_pr_diff_m")" > "$T/body-k3"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-k3"
assert_rc 1 "(k3) a diff-bearing marker recorded for another PR is rejected"
assert_contains "(k3) reports no evidence for this PR" "No AI review evidence found in PR #${PR_NUMBER}"

echo "── (l) a whitespace-padded GATE_SECRET is normalised ─────────"
# The deployed key file is 64 hex chars PLUS a trailing newline; the gate
# strips whitespace before HMACing. Lose that line and every signature breaks,
# while every whitespace-free fixture above still passes.
diff_marker "$HEAD" "$DH" > "$T/body-l"
GATE_SECRET_OVERRIDE=$'\n  '"$KEY"$'  \n'
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-l"
unset GATE_SECRET_OVERRIDE
assert_rc 0 "(l) a whitespace-padded key is normalised"

echo "── (m) a whitespace-ONLY key fails closed ─────────────────────"
# A whitespace-only secret passes the raw `-z` guard and then normalizes to the
# EMPTY string, which is a PUBLIC HMAC key: any PR author could mint a
# stale-sha marker carrying the live `diff=` and turn the required check green
# on an unreviewed diff. The gate must re-validate AFTER normalization.
# Sign the marker with the EMPTY key — the key every attacker knows once the
# secret normalizes to "". If the gate skips validation of the NORMALIZED key,
# this marker verifies and the required check passes on an unreviewed diff.
empty_key_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean @ ${HEAD} diff=${DH} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(printf '%s' "$m" | openssl dgst -sha256 -hmac "" | awk '{print $NF}')"
}
empty_key_marker > "$T/body-m"
GATE_SECRET_OVERRIDE=$'\n \t \n'
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-m"
unset GATE_SECRET_OVERRIDE
assert_rc 1 "(m) a whitespace-only key never becomes the empty HMAC key"
assert_contains "(m) names the normalization failure" "normalises to an empty value"

echo "── (n) a malformed trailing line cannot hijack the repo diagnostic ─"
# A well-formed but STALE own-repo marker followed by a line that carries a
# trailing ` (other/repo) sig=<hex>` but no well-formed 40-hex sha must still
# report the real cause (stale). Naming `other/repo` suppresses the accurate
# stale/diff diagnostic on a REQUIRED check.
{
    diff_marker "$STALE" "$DH2"
    printf 'review recorded: reviews/%s.json verdict=clean @ not-a-sha (some-other/place) sig=%s\n' \
        "$PR_NUMBER" "$(printf 'f%.0s' $(seq 1 64))"
} > "$T/body-n"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-n"
assert_rc 1 "(n) gate fails"
assert_contains "(n) reports the real cause" "latest recorded ${STALE} — expected ${HEAD}"
assert_not_contains "(n) does not misattribute the repo" "was found for some-other/place"

echo "── (o) normalized diff digest (#1362) ─────────────────────────"
# o1 — the D1 win. Post-#1362 the producer signs the NORMALIZED digest, so a
# base move that rewrote only index/hunk headers still carries the verdict
# forward. The marker's sha is stale, so acceptance can ONLY come from (b).
diff_marker "$STALE" "$DH_NORM_1362" > "$T/body-o1"
STUB_DIFF_FILE="$DIFF_NORM_FILE" run_gate "$T/body-o1"
assert_rc 0 "(o1) a normalized-hash marker carries forward"
assert_contains "(o1) names the diff-match path" "passed via diff match"
assert_contains "(o1) reports the normalized digest matched" "matched the normalized digest"
# o2 — backward compatibility. The legacy RAW digest is STILL accepted for the
# same diff. Without this arm every marker already recorded breaks and the
# required check reddens fleet-wide. This is the case that pins the consumer-
# first land order.
diff_marker "$STALE" "$DH_RAW" > "$T/body-o2"
STUB_DIFF_FILE="$DIFF_NORM_FILE" run_gate "$T/body-o2"
assert_rc 0 "(o2) a legacy raw-hash marker is still accepted (backward compat)"
assert_contains "(o2) reports the raw digest matched" "matched the raw digest"
# o3 — a marker matching NEITHER digest is rejected, and the message names
# BOTH live digests (normalized + raw) so the operator is not left guessing.
DH_NEITHER="$(printf 'deadbeef%.0s' $(seq 1 8))"   # exactly 64 hex chars
diff_marker "$STALE" "$DH_NEITHER" > "$T/body-o3"
STUB_DIFF_FILE="$DIFF_NORM_FILE" run_gate "$T/body-o3"
assert_rc 1 "(o3) a marker matching neither digest is rejected"
assert_contains "(o3) reports the diff divergence" "does not match this PR's current diff hash"
assert_contains "(o3) names the normalized digest" "normalized="
assert_contains "(o3) names the raw digest" "raw="
# o4 — fail closed. When the diff cannot be fetched BOTH digests are empty and
# a diff= marker is not accepted via (b). A transient API failure is not a
# bypass, exactly as before normalization.
diff_marker "$STALE" "$DH_NORM_1362" > "$T/body-o4"
STUB_DIFF_FILE="$DIFF_NORM_FILE" STUB_DIFF_FAIL=1 run_gate "$T/body-o4"
assert_rc 1 "(o4) normalized diff= fails closed when the fetch fails"
assert_contains "(o4) says the live hash was unavailable" "live diff hash could not be computed"
# o5 — rule (a) is unchanged. A diff= marker at the CURRENT head passes on the
# sha binding alone even when the fetch fails and its diff= matches neither
# digest. Normalization must never gate the head-bound path.
diff_marker "$HEAD" "$DH_NEITHER" > "$T/body-o5"
STUB_DIFF_FILE="$DIFF_NORM_FILE" STUB_DIFF_FAIL=1 run_gate "$T/body-o5"
assert_rc 0 "(o5) the head-bound path is unchanged by normalization"
if printf '%s' "$GATE_OUT" | grep -qF "passed via diff match"; then
    bad "(o5) head-bound pass must not claim the diff path"
else
    ok "(o5) head-bound pass does not claim the diff path"
fi

echo "── (p) whitespace-only change changes the digest (anti-patch-id) ─"
# `git patch-id --stable` and its default IGNORE whitespace, so it would MATCH
# these two diffs and carry a stale verdict across a real whitespace-only
# change — a false accept. sha256 over normalized bytes must NOT. This is the
# reason the gate does not use patch-id, pinned as an executable property.
DH_WS_A="$(normalize_review_diff < "$WS_A_FILE" | openssl dgst -sha256 | awk '{print $NF}')"
DH_WS_B="$(normalize_review_diff < "$WS_B_FILE" | openssl dgst -sha256 | awk '{print $NF}')"
if [ -n "$DH_WS_A" ] && [ "$DH_WS_A" != "$DH_WS_B" ]; then
    ok "(p) a whitespace-only change yields a different normalized digest"
else
    bad "(p) a whitespace-only change did NOT change the digest — a patch-id-style normalizer would carry a stale verdict forward (got '$DH_WS_A' vs '$DH_WS_B')"
fi
# ...and end to end: a marker for the pre-whitespace diff is refused against the
# post-whitespace live diff.
diff_marker "$STALE" "$DH_WS_A" > "$T/body-p"
STUB_DIFF_FILE="$WS_B_FILE" run_gate "$T/body-p"
assert_rc 1 "(p) a marker for the pre-whitespace diff is rejected"

echo "── (q) #1362 binary carve-out: entry-scoped index retention ────"
# The amendment to the 2026-09-23 ruling (recorded on agent-infra#1362, comment
# 5806797023, per the ruling's OWN rationale): the `index` line is dropped
# exactly when the hunk content already carries the change. A binary entry has
# no hunks and `Binary files … differ` carries no content, so an unconditional
# drop made two DISTINCT binary revisions normalize identically — review v1,
# sign, swap in v2, and the required gate ACCEPTED the unreviewed binary.

# (a) The fail-open is CLOSED. Two different binaries at the same path must
#     produce DIFFERENT normalized digests. This is the mutation-pinned case:
#     restore the unconditional index drop and BOTH entries normalize to the
#     same bytes, so this assertion goes red (verified by mutating the
#     normalizer to always drop — see the PR body).
BIN1="$T/bin1.diff"; BIN2="$T/bin2.diff"
printf 'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ\n' > "$BIN1"
printf 'diff --git a/f.bin b/f.bin\nindex 3333333..4444444 100644\nBinary files a/f.bin and b/f.bin differ\n' > "$BIN2"
DH_BIN1_NORM="$(normalize_review_diff < "$BIN1" | openssl dgst -sha256 | awk '{print $NF}')"
DH_BIN2_NORM="$(normalize_review_diff < "$BIN2" | openssl dgst -sha256 | awk '{print $NF}')"
if [ -n "$DH_BIN1_NORM" ] && [ "$DH_BIN1_NORM" != "$DH_BIN2_NORM" ]; then
    ok "(a) two different binaries normalize to DIFFERENT digests"
else
    bad "(a) two different binaries normalized IDENTICALLY ($DH_BIN1_NORM) — the index-line drop fail-open is back"
fi
# ...and end to end at the GATE, which is where the fail-open was exploitable:
# a marker signed for the reviewed v1 is accepted against v1 and REJECTED
# against the unreviewed v2.
diff_marker "$STALE" "$DH_BIN1_NORM" > "$T/body-bin1"
STUB_DIFF_FILE="$BIN1" run_gate "$T/body-bin1"
assert_rc 0 "(a) a marker for the reviewed binary v1 is accepted against v1"
assert_contains "(a) names the diff-match path" "passed via diff match"
STUB_DIFF_FILE="$BIN2" run_gate "$T/body-bin1"
assert_rc 1 "(a) the SAME marker is REJECTED against the unreviewed binary v2 (fail-open closed)"
assert_contains "(a) reports the diff divergence for v2" "does not match this PR's current diff hash"

# (b) Text base-move keeps the SAME normalized digest — #4969's win is not
#     regressed. The two renderings differ only in the `index` line and the
#     hunk start lines; every content line is identical.
TEXT_B1="$T/text-b1.diff"; TEXT_B2="$T/text-b2.diff"
printf 'diff --git a/f b/f\nindex 1111111..2222222 100644\n--- a/f\n+++ b/f\n@@ -1,3 +1,4 @@\n ctx\n+added\n ctx2\n' > "$TEXT_B1"
printf 'diff --git a/f b/f\nindex aaaaaaa..bbbbbbb 100644\n--- a/f\n+++ b/f\n@@ -10,3 +11,4 @@\n ctx\n+added\n ctx2\n' > "$TEXT_B2"
DH_TB1="$(normalize_review_diff < "$TEXT_B1" | openssl dgst -sha256 | awk '{print $NF}')"
DH_TB2="$(normalize_review_diff < "$TEXT_B2" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$DH_TB1" = "$DH_TB2" ]; then
    ok "(b) a text base-move leaves the normalized digest unchanged"
else
    bad "(b) a text base-move changed the normalized digest — #4969's win regressed ($DH_TB1 vs $DH_TB2)"
fi
RAW_TB1="$(openssl dgst -sha256 < "$TEXT_B1" | awk '{print $NF}')"
RAW_TB2="$(openssl dgst -sha256 < "$TEXT_B2" | awk '{print $NF}')"
if [ "$RAW_TB1" != "$RAW_TB2" ]; then
    ok "(b) control: the RAW digest did move (so this is the normalization, not the fixture)"
else
    bad "(b) control: the raw digest did NOT move — the fixture is not a base-move shape"
fi

# (c) Binary base-move keeps the SAME normalized digest. A no-regression
#     guard (not a mutation pin — (a)/(d)/(e) carry the pins): the base move
#     rewrites the sibling TEXT entry's index/header; the binary entry is
#     byte-identical (a base move does not touch the binary's content, so its
#     blob-OID pair does not move), so the KEPT index line cannot make the
#     digest move either.
BIN_B1="$T/binb1.diff"; BIN_B2="$T/binb2.diff"
printf 'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ\ndiff --git a/t.txt b/t.txt\nindex 1111111..2222222 100644\n--- a/t.txt\n+++ b/t.txt\n@@ -1,3 +1,4 @@\n ctx\n+added\n ctx2\n' > "$BIN_B1"
printf 'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ\ndiff --git a/t.txt b/t.txt\nindex aaaaaaa..bbbbbbb 100644\n--- a/t.txt\n+++ b/t.txt\n@@ -10,3 +11,4 @@\n ctx\n+added\n ctx2\n' > "$BIN_B2"
DH_BB1="$(normalize_review_diff < "$BIN_B1" | openssl dgst -sha256 | awk '{print $NF}')"
DH_BB2="$(normalize_review_diff < "$BIN_B2" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$DH_BB1" = "$DH_BB2" ]; then
    ok "(c) a binary base-move leaves the normalized digest unchanged"
else
    bad "(c) a binary base-move changed the normalized digest ($DH_BB1 vs $DH_BB2)"
fi

# (d) Binary add / delete / binary+mode-change entries keep the `index` line.
BIN_ADD="$T/bin-add.diff"; BIN_DEL="$T/bin-del.diff"; BIN_MODE="$T/bin-mode.diff"
printf 'diff --git a/f.bin b/f.bin\nnew file mode 100644\nindex 0000000..2222222\nBinary files /dev/null and b/f.bin differ\n' > "$BIN_ADD"
printf 'diff --git a/f.bin b/f.bin\ndeleted file mode 100644\nindex 1111111..0000000\nBinary files a/f.bin and /dev/null differ\n' > "$BIN_DEL"
printf 'diff --git a/f.bin b/f.bin\nold mode 100644\nnew mode 100755\nindex 1111111..2222222\nBinary files a/f.bin and b/f.bin differ\n' > "$BIN_MODE"
for _spec in "add:$BIN_ADD:index 0000000\.\.2222222" "delete:$BIN_DEL:index 1111111\.\.0000000" "mode:$BIN_MODE:index 1111111\.\.2222222"; do
    _name="${_spec%%:*}"; _rest="${_spec#*:}"; _file="${_rest%%:*}"; _re="${_rest#*:}"
    if normalize_review_diff < "$_file" | grep -qE "^${_re}$"; then
        ok "(d) binary ${_name} entry keeps its index line"
    else
        bad "(d) binary ${_name} entry LOST its index line"
    fi
done

# (e) A hunk-less NON-binary entry keeps the `index` line. The predicate is
#     HUNK PRESENCE, not the presence of a binary marker: an empty-file add
#     (`index 0000000..e69de29`) has no hunk and no `Binary files` line, and
#     must still keep the line.
printf 'diff --git a/empty b/empty\nnew file mode 100644\nindex 0000000..e69de29\n' > "$T/empty-add.diff"
if normalize_review_diff < "$T/empty-add.diff" | grep -qE '^index 0000000\.\.e69de29$'; then
    ok "(e) a hunk-less non-binary (empty-file add) entry keeps its index line"
else
    bad "(e) a hunk-less non-binary entry LOST its index line — the predicate is binary-marker presence, not hunk presence"
fi
# ...and the flip side, so (e) cannot pass vacuously: the SAME index line IS
# dropped once the entry carries a hunk.
printf 'diff --git a/empty b/empty\nindex 0000000..e69de29\n@@ -0,0 +1 @@\n+x\n' > "$T/empty-hunk.diff"
if normalize_review_diff < "$T/empty-hunk.diff" | grep -qE '^index '; then
    bad "(e) the index line survived a HUNK-bearing entry — the drop is not entry-scoped"
else
    ok "(e) the same index line IS dropped when the entry carries a hunk"
fi

# (f) The legacy RAW-hash arm still accepts (unchanged behaviour). A marker
#     signed with the raw digest of a hunk-bearing diff is accepted, so every
#     marker already recorded stays valid (the consumer-first land order).
#     A hunk-bearing fixture is required: for a binary-only entry the carve-out
#     keeps the index line, so the normalized bytes EQUAL the raw bytes and the
#     raw arm is not separately observable.
DH_TB1_RAW="$(openssl dgst -sha256 < "$TEXT_B1" | awk '{print $NF}')"
DH_TB1_NORM="$(normalize_review_diff < "$TEXT_B1" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$DH_TB1_RAW" != "$DH_TB1_NORM" ]; then
    ok "(f) fixture: the raw and normalized digests differ (so the arm is observable)"
else
    bad "(f) fixture: raw == normalized, so the legacy arm is not exercised"
fi
diff_marker "$STALE" "$DH_TB1_RAW" > "$T/body-raw-1362"
STUB_DIFF_FILE="$TEXT_B1" run_gate "$T/body-raw-1362"
assert_rc 0 "(f) a legacy raw-hash marker is still accepted (backward compat)"
assert_contains "(f) reports the raw digest matched" "matched the raw digest"

# (g) Everything still fails closed when the diff cannot be fetched: BOTH
#     digests are empty, so a stale-sha binary `diff=` marker is not accepted.
diff_marker "$STALE" "$DH_BIN1_NORM" > "$T/body-bin-fc"
STUB_DIFF_FILE="$BIN1" STUB_DIFF_FAIL=1 run_gate "$T/body-bin-fc"
assert_rc 1 "(g) a binary normalized diff= fails closed when the diff fetch fails"
assert_contains "(g) says the live hash was unavailable" "live diff hash could not be computed"

# (g2) A FAILING normalizer clears BOTH digests, not just the normalized one.
#      The guard at the fetch site only covers a MISSING python3; this exercises
#      the else-branch that a present-but-broken normalizer takes. Without it, a
#      broken normalizer would silently fall back to the raw arm and a stale
#      raw `diff=` marker would still be accepted.
cat > "$T/bin/python3" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
chmod +x "$T/bin/python3"
diff_marker "$STALE" "$DH_BIN1_NORM" > "$T/body-normfail"
STUB_DIFF_FILE="$BIN1" run_gate "$T/body-normfail"
assert_rc 1 "(g2) a failing normalizer rejects a normalized diff= marker (fail closed)"
assert_contains "(g2) says the live hash was unavailable" "live diff hash could not be computed"
# ...and the legacy RAW arm is cleared too (no silent fallback to the weaker arm).
diff_marker "$STALE" "$DH_TB1_RAW" > "$T/body-raw-normfail"
STUB_DIFF_FILE="$TEXT_B1" run_gate "$T/body-raw-normfail"
assert_rc 1 "(g2) a failing normalizer clears the RAW arm too (no silent fallback)"
rm -f "$T/bin/python3"

echo ""
echo "── Summary ───────────────────────────────────────────────────────"
echo "  PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || { echo "  ❌ FAILURES — fix and re-run"; exit 1; }
echo "  ✅ all checks passed"
exit 0
