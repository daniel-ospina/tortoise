#!/usr/bin/env bash
# billing-reconcile-cron.sh — the cadenced driver of the billing-mirror
# reconciler (#8878). Runs from .github/workflows/billing-reconcile-cron.yml.
#
# Why a script and not an inline `run:` block: the same reason #2796/#2982 gave
# for `registry-cron.sh` and `ai-review-gate.test.sh` — a driver that lives
# inline in YAML can only be exercised by the schedule, so a wrong judgement is
# discovered by paging a human. Extracted here, it is driven by
# `.github/scripts/billing-reconcile-cron.test.sh` with a stubbed `curl`.
#
# WHAT COUNTS AS A FAILURE. The endpoint
# (POST /v1/internal/billing/reconcile) answers:
#   * 200 + a pass summary      → judge the summary (below);
#   * 202 `already_running`     → BENIGN (the endpoint's own docstring: "the
#     next scheduled run retries; nothing queues"). Succeed with a notice.
#     `concurrency.cancel-in-progress: false` only stops a
#     scheduled↔scheduled overlap — it does NOT cover a `workflow_dispatch`
#     racing a manual run, nor the operator curl in `docs/infra-runbook.md`
#     §4.2 landing inside the daily pass, and both of those would otherwise
#     page a false billing incident. #596's registry driver treats the sweep's
#     `already_running` the same way (it branches on the BODY's `.status`).
#   * anything else             → the request failed (app down / key wrong).
#
# A 200 summary FAILS the run — i.e. it PAGES — when:
#   * `failed` is non-empty (a billed org could not be reconciled: a pass that
#     verified nothing must not read as a clean pass), or
#   * `unrepaired` is non-empty (Stripe resolved a tier the store did not end up
#     holding — a read-only reconciler is a BROKEN reconciler), or
#   * any `drift` entry has direction "up" (a paying customer WAS being
#     under-delivered; it is repaired now, and the reconciler also pages it
#     through the billing notify channel — this is the independent leg that
#     still fires if that channel is unconfigured).
#
# Drift DOWN (a portal downgrade/cancellation the mirror had not seen) is
# reported and is NOT a failure: Stripe truth arriving late, not a customer
# being short-changed.
#
# Env: INTERNAL_API_URL, FASTAPI_INTERNAL_KEY (both required).
# Exit: 0 healthy/already-running, 1 any failure above.
set -euo pipefail

API="${INTERNAL_API_URL:-}"
KEY="${FASTAPI_INTERNAL_KEY:-}"

if [ -z "$API" ] || [ -z "$KEY" ]; then
  echo "::error::INTERNAL_API_URL / FASTAPI_INTERNAL_KEY are unset — the billing reconciler cannot run."
  exit 1
fi

out="$(mktemp)"
trap 'rm -f "$out"' EXIT

# `-m 600` publishes THIS caller's patience. `/v1/internal/` is exempt from the
# 10 s transport wait bound and has no server-side ceiling (the #5028 class), so
# without it a hung pass would surface as a `timeout-minutes: 15` job kill
# instead of the legible `curl rc=28` the sibling driver reads. 600 s matches
# `registry-cron.sh`.
status="$(curl -sS -m 600 -o "$out" -w '%{http_code}' \
  -X POST "${API%/}/v1/internal/billing/reconcile" \
  -H "Authorization: Bearer ${KEY}")" || {
  rc=$?
  echo "::error::billing reconcile request failed (curl rc=${rc})"
  exit 1
}
echo "HTTP ${status}"
cat "$out"
echo

if [ "$status" = "202" ]; then
  # The documented benign overlap (#7743-adjacent class: delivered ≠ started,
  # here "already running" ≠ "broken").
  echo "::notice::a billing reconcile pass is already running — the next scheduled run retries (nothing queues)"
  exit 0
fi

if [ "$status" != "200" ]; then
  echo "::error::billing reconcile returned HTTP ${status}"
  exit 1
fi

# Judge the SUMMARY, not the status code. A 200 whose body is not a summary is
# a failure too — a proxy/landing page answering 200 would otherwise pass.
python3 - "$out" <<'PY'
import json
import sys

try:
    data = json.load(open(sys.argv[1]))
except (OSError, ValueError) as exc:
    print(f"::error::billing reconcile returned 200 but no parseable summary: {exc}")
    sys.exit(1)
if not isinstance(data, dict):
    print("::error::billing reconcile returned 200 but the body is not a summary object")
    sys.exit(1)

drift_up = [d for d in data.get("drift", []) if d.get("direction") == "up"]
failed = data.get("failed", [])
unrepaired = data.get("unrepaired", [])
print(
    f"checked={data.get('checked', 0)} repaired={data.get('repaired', 0)} "
    f"up={len(drift_up)} unchanged={data.get('unchanged', 0)} "
    f"skipped={data.get('skipped', 0)} failed={len(failed)} "
    f"unrepaired={len(unrepaired)}"
)
problems = []
if failed:
    problems.append(f"{len(failed)} org(s) FAILED to reconcile: {failed}")
if unrepaired:
    problems.append(
        f"{len(unrepaired)} org(s) were NOT repaired (Stripe truth "
        f"did not reach the store): {unrepaired}")
if drift_up:
    problems.append(
        f"{len(drift_up)} paying org(s) were being UNDER-DELIVERED "
        f"(repaired on this pass): {drift_up}")
if problems:
    for p in problems:
        print(f"::error::{p}")
    sys.exit(1)
PY
