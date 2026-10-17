#!/usr/bin/env bash
# billing-reconcile-cron.test.sh — self-check for
# `.github/scripts/billing-reconcile-cron.sh` (#8878 review).
#
# Run: bash .github/scripts/billing-reconcile-cron.test.sh
# Exits 0 when ALL cases pass, 1 on any failure. Self-contained: stubs `curl`
# on PATH (which also records its argv, so the request's own shape is pinned —
# `-m 600` included), no network, no real key.
#
# Coverage:
#   1. 200 + a clean summary                     → exit 0
#   2. 200 + `failed` non-empty                  → exit 1 + ::error::
#   3. 200 + `unrepaired` non-empty              → exit 1 + ::error::
#   4. 200 + a drift row with direction "up"     → exit 1 + ::error::
#   5. 200 + a drift row with direction "down"   → exit 0 (Stripe truth late,
#      not a customer being short-changed)
#   6. 202 `already_running`                     → exit 0 + ::notice::
#      ← the #8878 review P2: a concurrent pass is BENIGN and must not page
#   7. 500                                       → exit 1
#   8. 200 with an unparseable body              → exit 1 (a proxy answering 200
#      with a landing page must not read as a clean pass)
#   9. transport failure (curl non-zero)         → exit 1
#  10. missing INTERNAL_API_URL / key            → exit 1, no request made
#  11. the request itself: POST, bearer auth, and `-m 600` (the /v1/internal/
#      wait bound is the caller's to publish — #5028)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DRIVER="$ROOT/.github/scripts/billing-reconcile-cron.sh"
[ -x "$DRIVER" ] || chmod +x "$DRIVER" 2>/dev/null || true

FAILED=0
note() { printf '%s\n' "$*"; }
ok()   { note "  ok   — $*"; }
bad()  { note "  FAIL — $*"; FAILED=1; }

STUB_DIR="$(mktemp -d)"
trap 'rm -rf "$STUB_DIR"' EXIT
export STUB_ARGS_FILE="$STUB_DIR/curl-args"

# Stub curl: STUB_CODE → the `-w '%{http_code}'` value on stdout, STUB_BODY →
# the file written by `-o`. Degenerate to a transport failure when STUB_RC is
# set. Every invocation appends its argv so the request shape is assertable.
cat > "$STUB_DIR/curl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_ARGS_FILE"
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
[ -n "${STUB_RC:-}" ] && { echo "curl: (7) couldn't connect" >&2; exit "$STUB_RC"; }
[ -n "$out" ] && printf '%s' "$STUB_BODY" > "$out"
printf '%s' "$STUB_CODE"
exit 0
STUB
chmod +x "$STUB_DIR/curl"

# run_case <name> <expect_exit> <body> [env assignments…]
run_case() {
  local name="$1" expect="$2" body="$3"; shift 3
  : > "$STUB_ARGS_FILE"
  local out rc
  out="$(env PATH="$STUB_DIR:$PATH" STUB_ARGS_FILE="$STUB_ARGS_FILE" \
            INTERNAL_API_URL="https://api.example.test" \
            FASTAPI_INTERNAL_KEY="internal-test-key" \
            STUB_BODY="$body" "$@" \
            bash "$DRIVER" 2>&1)"
  rc=$?
  if [ "$rc" -eq "$expect" ]; then ok "$name (exit $rc)"
  else bad "$name — expected exit $expect, got $rc: ${out}"; fi
  LAST_OUT="$out"
  LAST_RC="$rc"
}

LAST_OUT=""
LAST_RC=0

CLEAN='{"checked":3,"repaired":0,"unchanged":3,"skipped":1,"drift":[],"unrepaired":[],"failed":[]}'

note "1. clean pass"
run_case "clean summary" 0 "$CLEAN" STUB_CODE=200
case "$LAST_OUT" in *"checked=3"*) ok "summary is echoed" ;; *) bad "summary not echoed: $LAST_OUT" ;; esac

note "2. failed orgs"
run_case "failed non-empty" 1 \
  '{"checked":2,"failed":[{"org_id":"org-a","error":"StripeAPIError: outage"}]}' STUB_CODE=200
case "$LAST_OUT" in *"::error::"*FAILED*) ok "pages a FAILED org" ;; *) bad "no FAILED error: $LAST_OUT" ;; esac

note "3. unrepaired orgs"
run_case "unrepaired non-empty" 1 \
  '{"checked":1,"unrepaired":[{"org_id":"org-a","expected_tier":"pro","stored_tier":"solo"}]}' \
  STUB_CODE=200
case "$LAST_OUT" in *"::error::"*NOT\ repaired*) ok "pages an UNREPAIRED org" ;; *) bad "no UNREPAIRED error: $LAST_OUT" ;; esac

note "4. drift up"
run_case "drift up" 1 \
  '{"checked":1,"repaired":1,"drift":[{"org_id":"org-a","direction":"up"}]}' STUB_CODE=200
case "$LAST_OUT" in *"UNDER-DELIVERED"*) ok "pages the under-delivered customer" ;; *) bad "no UNDER-DELIVERED error: $LAST_OUT" ;; esac

note "5. drift down only"
run_case "drift down is not a failure" 0 \
  '{"checked":1,"repaired":1,"drift":[{"org_id":"org-a","direction":"down"}]}' \
  STUB_CODE=200

note "6. 202 already_running is BENIGN"
run_case "202 already_running" 0 '{"status":"already_running"}' STUB_CODE=202
case "$LAST_OUT" in *"::notice::"*) ok "announces the benign overlap" ;; *) bad "no ::notice:: on 202: $LAST_OUT" ;; esac
case "$LAST_OUT" in *"::error::"*) bad "202 must NOT emit ::error:: (it pages)" ;; *) ok "no ::error:: on 202" ;; esac

note "7. HTTP 500"
run_case "500 fails the run" 1 '{"detail":"boom"}' STUB_CODE=500
case "$LAST_OUT" in *"returned HTTP 500"*) ok "names the status" ;; *) bad "no status in error: $LAST_OUT" ;; esac

note "8. 200 with a non-summary body"
run_case "unparseable 200 body" 1 '<html>welcome</html>' STUB_CODE=200

note "9. transport failure"
run_case "curl transport failure" 1 '' STUB_CODE=000 STUB_RC=7

note "10. missing configuration"
: > "$STUB_ARGS_FILE"
out="$(env -u INTERNAL_API_URL -u FASTAPI_INTERNAL_KEY PATH="$STUB_DIR:$PATH" \
          STUB_ARGS_FILE="$STUB_ARGS_FILE" bash "$DRIVER" 2>&1)"; rc=$?
if [ "$rc" -eq 1 ]; then ok "unset env fails closed (exit 1)"; else bad "unset env exited $rc"; fi
case "$out" in *"cannot run"*) ok "names the missing configuration" ;; *) bad "no config error: $out" ;; esac
if [ -s "$STUB_ARGS_FILE" ]; then bad "a request was attempted without credentials"; else ok "no request attempted"; fi

note "11. request shape"
run_case "clean pass (args record)" 0 "$CLEAN" STUB_CODE=200
args="$(cat "$STUB_ARGS_FILE" 2>/dev/null || true)"
case "$args" in *"-m 600"*) ok "publishes -m 600 (the /v1/internal/ wait bound)" ;; *) bad "no -m 600 in: $args" ;; esac
case "$args" in *"-X POST"*) ok "POST" ;; *) bad "not a POST: $args" ;; esac
case "$args" in *"/v1/internal/billing/reconcile"*) ok "hits the reconcile route" ;; *) bad "wrong route: $args" ;; esac
case "$args" in *"Authorization: Bearer internal-test-key"*) ok "sends the internal key" ;; *) bad "no bearer auth: $args" ;; esac
case "$args" in *"$'\n'"*"-X POST"*) bad "more than one request was made" ;; *) ok "exactly one request" ;; esac

if [ "$FAILED" -eq 0 ]; then
  note "billing-reconcile driver: ALL CASES PASS"
  exit 0
fi
note "billing-reconcile driver: FAILURES ABOVE"
exit 1
