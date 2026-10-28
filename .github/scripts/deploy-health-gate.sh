#!/usr/bin/env bash
# ============================================================================
# deploy-health-gate.sh — the post-deploy health gate for `deploy-hosted.yml`
# (#1719 Task 7; extracted from inline workflow shell for #4545).
#
# WHY THIS IS A SCRIPT AND NOT INLINE WORKFLOW SHELL
#   The gate's whole job is to decide whether a deploy SUCCEEDED, and it is the
#   only thing standing between a healthy deploy and a red `deploy-hosted.yml`
#   on `main` — which is what froze production at v619. Inline, it could not be
#   tested, and it shipped the defect #4545 reports:
#
#     THE GATE POLLED A WEAKER SIGNAL THAN THE ONE IT ASSERTED.
#
#   It polled `db.ok` — the FalkorDB DATA plane only — for up to 3 minutes, and
#   then asserted `/health/ready` ONCE. But `/health/ready` is
#   `AND(Supabase control plane, FalkorDB data plane)` (see
#   `tortoise/hosted_api.py::health_ready`), so it is a STRICTLY STRONGER
#   predicate than `db.ok`. `db.ok == true` therefore licensed an assertion
#   about a predicate that had never been polled — and on a cold start the
#   control plane is routinely not connected yet. Observed twice in one hour:
#
#       db.ok true → (2-3 s later) /health/ready: 503 → deploy marked FAILED
#
#   In the second instance `flyctl deploy` (step 12) had ALREADY SUCCEEDED and
#   released v666, and the app was serving 200 on both endpoints minutes later.
#   The workflow reddened a deploy that had already gone out.
#
#   The invariant this file exists to keep:
#       NEVER ASSERT A PREDICATE YOU HAVE NOT YOURSELF OBSERVED TO BE TRUE,
#       ON THE STRENGTH OF A WEAKER ONE.
#   So readiness is polled with its OWN budget, over the SAME predicate that is
#   then reported. That does NOT weaken the gate: it is still fail-closed (a
#   readiness signal that never arrives within the budget fails the deploy), it
#   just no longer fails a deploy that was merely EARLY. #4771 then applied the
#   same invariant one level up — to the DECISION (see the CONTRACT note below).
#
# BEHAVIOUR
#   ⛔ Every window stated below is `tries × (sleep + the phase's per-REQUEST
#   bound)`, DERIVED in the "per-REQUEST bounds" block further down. That block
#   is the ONE place the arithmetic lives; the numbers are restated here only so
#   this header is readable, and
#   `tests/test_deploy_workflow.py::test_the_health_gate_header_states_the_budgets_its_knobs_derive`
#   pins all three lines to the shipped knobs, so a knob change reds CI instead
#   of leaving this header describing a window the script does not have.
#   1. app reachable  — up to 75 s (1.2 min) of quick probes; a dead app is a
#                       deploy failure, not a DB wait, so this fails FAST.
#   2. db.ok          — polled for up to 270 s (4.5 min) (cold-start DB connect
#                       >60s per #338; DNS propagation after a restart ~2 min
#                       per #1381). IT DOES NOT DECIDE THE RUN (#4771): it is
#                       the WEAKER predicate (the FalkorDB data plane ALONE), so
#                       on exhaustion it reports a loud `::warning::` and the
#                       run proceeds to phase 3. The field is READ from parsed
#                       JSON, never text-matched: /health's additive
#                       `backup_watcher` block added a SECOND `"ok": true` to
#                       the body, and a `grep -q '"ok": *true'` matched the
#                       WATCHER's ok while db.ok was still false — silently
#                       neutralising the phase-2 tolerance (#4470). A field's
#                       VALUE must be read, not its spelling.
#   3. /health/ready  — polled for up to 414 s (6.9 min), its own budget
#                       (#4545). It ANDs both planes, so it is the STRONGEST
#                       signal — and it is what DECIDES the run (#4771):
#                       exactly 200 → exit 0; never 200 → exit 1.
#
# CONTRACT (#4771) — THE WEAKER PREDICATE INFORMS, THE STRONGEST DECIDES.
#   Phase 2's `db.ok` and phase 3's `/health/ready` are two INDEPENDENT probes
#   with DIFFERENT budgets: `db.ok` reads the background liveness refresher
#   (`_HEALTH_PROBE`), while readiness runs its OWN coordinator `_READY_PROBE`
#   with its own budget at request time (`tortoise/hosted_api.py::health_ready`;
#   `_READY_PROBE` is defined there as `HealthProbe(lambda: _probe_db(),
#   timeout=DB_PROBE_HARD_TIMEOUT, fresh_only=True)`). So they can legitimately
#   disagree. Observed live in production on 2026-09-22 (issue #4771):
#
#       /health        → db.ok=false, error "probe timeout after 1.5s"
#       /health/ready  → 200 {"db":"connected","control_plane":"connected"}
#
#   Pre-#4771 the gate exited 1 on phase 2, so the WEAKER observation decided
#   the run and the stronger, more complete one was never consulted — the
#   #4545 invariant violated at the decision level rather than the assertion
#   level. Now the weaker one only informs.
#
#   THIS IS NOT A WEAKENING. `/health/ready` ANDs the SAME FalkorDB data plane
#   (`_READY_PROBE` calls the same `_probe_db()`) with the SAME control plane,
#   on its own probe and its own budget — so a GENUINELY unreachable FalkorDB
#   fails readiness too and the run still exits 1 (pinned by the harness: db.ok
#   never true + readiness never 200 → exit 1). What changed is only that the
#   weaker observation no longer gets to decide on its own.
#
# All three budgets are env-overridable so the harness
# (deploy-health-gate.test.sh) can drive every phase in milliseconds.
# ============================================================================

set -uo pipefail

#: The service under test. Named GATE_BASE, not BASE, so that a future job- or
#: workflow-level `BASE` (a generic name) cannot silently retarget the deploy
#: gate at a non-production host while it still reports "deployed".
GATE_BASE="${GATE_BASE:-https://api.premiselabs.co}"

#: App-reachability probes — fail fast, no DB wait.
APP_PROBES="${APP_PROBES:-5}"
APP_PROBE_SLEEP="${APP_PROBE_SLEEP:-5}"

#: Data-plane (db.ok) poll — the cold-start tolerance. Its window is
#: `DB_TRIES × (CURL_MAX_TIME_HEALTH + DB_SLEEP)` = 270 s, i.e. the sleep alone
#: is NOT the window; the BEHAVIOUR header above restates it and a test pins it.
DB_TRIES="${DB_TRIES:-18}"
DB_SLEEP="${DB_SLEEP:-10}"

#: Readiness poll — its OWN budget, over the SAME predicate reported (#4545).
READY_TRIES="${READY_TRIES:-18}"
READY_SLEEP="${READY_SLEEP:-10}"

# ── per-REQUEST bounds (the phase budgets above are not bounds on their own) ─
# A phase budget of `N tries × S sleep` is only a bound if each REQUEST is
# bounded, and curl has no total-transfer timeout by default (its connect
# timeout defaults to 300 s and a slow-but-progressing response is unbounded).
# Without these, a SLOW (not dead) endpoint makes the phase budget a lie: the
# caller's own `timeout-minutes` then becomes the thing that kills the job, and
# a `timeout-minutes` kill ends the job BEFORE `if: failure()` is evaluated —
# so the #2240 out-of-band alert never runs and a LIVE, unhealthy release is
# reported only as a red run with a truncated log. Measured: the caller's cap
# (deploy-hosted.yml `post-deploy-verify`) was justified as 2× a “≤ 6.4 min”
# worst case that assumed every curl returned INSTANTLY.
# ⛔ ONE KNOB PER REQUEST, never one knob for every request. The unit is the
# REQUEST, not the endpoint: two requests to the SAME path can cost the caller
# different things, and a client bound below the server's own bound reds a
# HEALTHY but slow release (the 200 arrives after curl gave up, curl prints
# 000, and phase 3 reports it as "no response from the app", the
# indistinguishable-failure class this file exists to avoid).
#   • phase 1 hits `/health` — an in-memory snapshot, no I/O — as the FIRST
#     request of the run, against a container that has only just started. The
#     cold-start cost of that first request (DNS/TLS, runtime warm-up, first
#     touch of the code path) is NOT the endpoint's own I/O, and a hiccup here
#     ends the gate BEFORE the DB wait and the readiness poll run at all — so it
#     throws away the two budgets that would have tolerated it. It therefore gets
#     the LOOSER bound: `CURL_MAX_TIME_APP` = 10 s. (Not "the only request whose
#     failure fails the gate": phase 3 fails it too, by design — this one fails
#     it EARLY.)
#   • phase 2 hits the SAME `/health` with a TIGHTER bound
#     (`CURL_MAX_TIME_HEALTH` = 5 s), because it runs only after phase 1 has
#     already proven reachability and it is a bounded POLL: 18 × (5 + 10) = 270 s
#     is the budget that has to hold, and a slack per-request bound is what
#     would make it not hold.
#   • `/health/ready` runs BOTH plane coordinators SEQUENTIALLY in Supabase mode
#     (`hosted_api.health_ready`: `_READY_PROBE` then `_CONTROL_PLANE_PROBE`),
#     and each enforces its OWN hard bound:
#         DB_PROBE_HARD_TIMEOUT      = `monitoring.PROBE_HARD_TIMEOUT`
#                                      = 3.1 + 2.0 + 0.5 = 5.6 s
#           (it ALIASES the coordinator bound; the 1.5 s `PROBE_TIMEOUT` is the
#            inner QUERY leg only, and quoting that as the total is the trap
#            #2988 was fought over)
#         CONTROL_PLANE_HARD_TIMEOUT = 5.0 phases + 1.0 margin = 6.0 s
#     → the server's declared worst case is 11.6 s, so the client bound is
#     derived ABOVE it (11.6 → 13, ~1.12×): a bound must bind the REQUEST, and
#     the request is the whole two-plane call. Re-derive it if either constant
#     moves. `_READY_PROBE` is `fresh_only=True`, so a completed result is
#     served only while it is younger than its OWN read budget
#     (`monitoring.HealthProbe._view_locked` → `max_age = self._timeout` =
#     5.6 s); the polls are `READY_SLEEP` = 10 s apart, i.e. older than that
#     budget, so EVERY poll re-probes. A sustained slow data plane must not be
#     read as a dead one, which is exactly what a 5 s bound did.
# With these bounds the worst case IS a number:
#   phase 1  5 × (10 + 5)  = 75 s
#   phase 2 18 × ( 5 + 10) = 270 s
#   phase 3 18 × (13 + 10) = 414 s
#                     total = 759 s = 12.65 min  (vs the caller's 25 min → 1.98×)
CURL_CONNECT_TIMEOUT="${CURL_CONNECT_TIMEOUT:-3}"
CURL_MAX_TIME_APP="${CURL_MAX_TIME_APP:-10}"
CURL_MAX_TIME_HEALTH="${CURL_MAX_TIME_HEALTH:-5}"
CURL_MAX_TIME_READY="${CURL_MAX_TIME_READY:-13}"

# ── guard the knobs ─────────────────────────────────────────────────────────
# A non-integer override would make `seq` produce nothing and then blow up the
# `$((...))` inside the failure message — and under `set -u` that aborts the
# shell AT the echo, so `exit 1` never runs. bash 3.2 then exits with the LAST
# command's status, i.e. GREEN. Fail loudly on a bad knob rather than failing
# OPEN on a dead database. (Introduced with the env-overridable knobs; main
# used a literal `3 min` here, which could not be mis-set.)
for _knob in APP_PROBES APP_PROBE_SLEEP DB_TRIES DB_SLEEP READY_TRIES READY_SLEEP; do
  case "${!_knob}" in
    '' | *[!0-9]*)
      echo "::error::$_knob must be a non-negative integer (got '${!_knob}')"
      exit 2
      ;;
  esac
done

# The per-REQUEST bounds must be POSITIVE, not merely integral: curl reads
# `--max-time 0` as "no limit", so allowing 0 here would silently restore the
# unbounded request this change exists to remove — a guard that permits the
# hole is worse than none, because the file then claims a bound it does not have.
# ⛔ `0` is not the only SPELLING of zero: `00` and `000` are integral, so they
# pass the pattern test, and curl reads `--max-time 00` as "no limit" exactly
# like `--max-time 0` (measured). The first guard is therefore on the SHAPE and
# the second is on the VALUE — a guard that pattern-matched `0` alone would
# report a bound the file does not have on any zero-padded override.
for _knob in CURL_CONNECT_TIMEOUT CURL_MAX_TIME_APP CURL_MAX_TIME_HEALTH CURL_MAX_TIME_READY; do
  case "${!_knob}" in
    '' | *[!0-9]*)
      echo "::error::$_knob must be a POSITIVE integer — curl reads 0 as 'no limit', which is the unbounded request this bound exists to remove (got '${!_knob}')"
      exit 2
      ;;
  esac
  # `10#` forces base 10, so `08` is eight rather than an invalid octal literal.
  if [ "$((10#${!_knob}))" -le 0 ]; then
    echo "::error::$_knob must be a POSITIVE integer — got '${!_knob}', whose VALUE is 0, and curl reads a zero --max-time (0, 00, 000) as 'no limit', which is the unbounded request this bound exists to remove"
    exit 2
  fi
done

# ── 1. app reachable (fail fast) ────────────────────────────────────────────
app_ok=0
for _ in $(seq 1 "$APP_PROBES"); do
  if curl -fsS --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME_APP" "$GATE_BASE/health" >/dev/null 2>&1; then
    app_ok=1
    break
  fi
  sleep "$APP_PROBE_SLEEP"
done
if [ "$app_ok" != "1" ]; then
  echo "::error::app unreachable after deploy ($GATE_BASE/health)"
  exit 1
fi
echo "app reachable — polling DB data plane"

# ── 2. data plane: read the db.ok FIELD, never text-match the body ──────────
db_ok=0
for _ in $(seq 1 "$DB_TRIES"); do
  if curl -fsS --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME_HEALTH" "$GATE_BASE/health" 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if (d.get("db") or {}).get("ok") else 1)' 2>/dev/null; then
    db_ok=1
    break
  fi
  sleep "$DB_SLEEP"
done
# #4771: the weaker predicate INFORMS, it does not DECIDE. A db.ok that never
# becomes true is recorded loudly and the run PROCEEDS to phase 3 — readiness
# ANDs the same data plane on its own probe/budget, so it is just as
# fail-closed as this poll and strictly more complete. Exiting here is what let
# the weaker observation decide a healthy release (see the header CONTRACT).
if [ "$db_ok" != "1" ]; then
  echo "::warning::db.ok stayed false for $((DB_TRIES * (DB_SLEEP + CURL_MAX_TIME_HEALTH)))s — the FalkorDB data-plane probe (liveness-refresher budget) never reported ok; this is the WEAKER predicate, so the run proceeds to /health/ready, which ANDs both planes and decides"
else
  echo "db.ok true"
fi

# ── 3. readiness: the STRONGEST signal, polled over its OWN predicate ───────
# /health/ready ANDs the Supabase control plane + the FalkorDB data plane.
# db.ok==true does NOT imply ready (#4545), so this gets its own tolerance
# rather than being asserted once on the strength of the data plane.
#
# The HTTP STATUS is READ, not inferred from curl's exit code. `curl -f`
# succeeds on ANY status < 400, so a redirecting readiness endpoint (a
# misconfigured route, a CDN rule) PASSED this gate — main logged the true
# status (`-w "health/ready: %{http_code}"`) but never failed on it, so a 302
# sailed through as a success. Exactly 200 is what the gate claims to have
# observed, so exactly 200 is what it now requires.
ready_ok=0
ready_code=""
for _ in $(seq 1 "$READY_TRIES"); do
  # Only substitute the sentinel when NOTHING was captured: curl can print a
  # real code and STILL exit non-zero (a transfer failure after the status
  # line), and overwriting an observed 200 with 000 would report "no response"
  # for an app that answered — the indistinguishable-failure class this file's
  # comment warns about.
  ready_code=$(curl -sS --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME_READY" -o /dev/null -w '%{http_code}' "$GATE_BASE/health/ready" 2>/dev/null)
  [ -n "$ready_code" ] || ready_code="000"
  if [ "$ready_code" = "200" ]; then
    ready_ok=1
    break
  fi
  sleep "$READY_SLEEP"
done
if [ "$ready_ok" != "1" ]; then
  # #4771: readiness is the predicate that DECIDES the run. When phase 2 ALSO
  # never observed db.ok, say so in the failure — readiness failing is the vote
  # that fails the deploy, and naming the data plane explicitly keeps the
  # diagnosis as specific as phase 2's own error used to be.
  db_clause=""
  [ "$db_ok" = "1" ] || db_clause="; db.ok was ALSO never true for $((DB_TRIES * (DB_SLEEP + CURL_MAX_TIME_HEALTH)))s — FalkorDB unreachable"
  # Distinguish a DEAD APP from an UNREADY one: 000 is curl's no-response
  # sentinel (connection refused/reset/DNS), while a real status means the app
  # answered and this is a genuine readiness failure. Reporting both as
  # "unreachable" would be the indistinguishable-failure class this gate exists
  # to avoid.
  echo "::error::/health/ready not 200 for $((READY_TRIES * (READY_SLEEP + CURL_MAX_TIME_READY)))s — last status $ready_code (000 = no response from the app; other = answered but not ready)$db_clause"
  exit 1
fi
echo "health/ready 200 (observed HTTP 200)"
exit 0
