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
#   1. app reachable  — 5 quick probes; a dead app is a deploy failure, not a
#                       DB wait, so this fails FAST.
#   2. db.ok          — poll for up to 3 min (cold-start DB connect >60s per
#                       #338; DNS propagation after a restart ~2 min per
#                       #1381). IT DOES NOT DECIDE THE RUN (#4771): it is the
#                       WEAKER predicate (the FalkorDB data plane ALONE), so on
#                       exhaustion it reports a loud `::warning::` and the run
#                       proceeds to phase 3. The field is READ from parsed JSON,
#                       never text-matched: /health's additive `backup_watcher`
#                       block added a SECOND `"ok": true` to the body, and a
#                       `grep -q '"ok": *true'` matched the WATCHER's ok while
#                       db.ok was still false — silently neutralising the
#                       3-minute tolerance (#4470). A field's VALUE must be
#                       read, not its spelling.
#   3. /health/ready  — polled for up to 3 min, its own budget (#4545). It ANDs
#                       both planes, so it is the STRONGEST signal — and it is
#                       what DECIDES the run (#4771): exactly 200 → exit 0;
#                       never 200 → exit 1.
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

#: Data-plane (db.ok) poll — the 3-minute cold-start tolerance.
DB_TRIES="${DB_TRIES:-18}"
DB_SLEEP="${DB_SLEEP:-10}"

#: Readiness poll — its OWN budget, over the SAME predicate reported (#4545).
READY_TRIES="${READY_TRIES:-18}"
READY_SLEEP="${READY_SLEEP:-10}"

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

# ── 1. app reachable (fail fast) ────────────────────────────────────────────
app_ok=0
for _ in $(seq 1 "$APP_PROBES"); do
  if curl -fsS "$GATE_BASE/health" >/dev/null 2>&1; then
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
  if curl -fsS "$GATE_BASE/health" 2>/dev/null \
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
  echo "::warning::db.ok stayed false for $((DB_TRIES * DB_SLEEP))s — the FalkorDB data-plane probe (liveness-refresher budget) never reported ok; this is the WEAKER predicate, so the run proceeds to /health/ready, which ANDs both planes and decides"
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
  ready_code=$(curl -sS -o /dev/null -w '%{http_code}' "$GATE_BASE/health/ready" 2>/dev/null)
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
  [ "$db_ok" = "1" ] || db_clause="; db.ok was ALSO never true for $((DB_TRIES * DB_SLEEP))s — FalkorDB unreachable"
  # Distinguish a DEAD APP from an UNREADY one: 000 is curl's no-response
  # sentinel (connection refused/reset/DNS), while a real status means the app
  # answered and this is a genuine readiness failure. Reporting both as
  # "unreachable" would be the indistinguishable-failure class this gate exists
  # to avoid.
  echo "::error::/health/ready not 200 for $((READY_TRIES * READY_SLEEP))s — last status $ready_code (000 = no response from the app; other = answered but not ready)$db_clause"
  exit 1
fi
echo "health/ready 200 (observed HTTP 200)"
exit 0
