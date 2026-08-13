#!/usr/bin/env bash
# ============================================================================
# run_schema_tests.sh — SQL-level RLS + constraint tests for migrations
# 0006–0009 (issue #769): teams, api_keys, invitations, team_memberships
# extension, audit_events.actor_user_id → TEXT, column-level protection.
#
# What it does:
#   1. Ensures the local Supabase stack is running (requires Docker — the
#      Supabase CLI runs Postgres in containers).
#   2. `supabase db reset` — applies ALL migrations (0001–0009) on a fresh
#      database. ANY migration error aborts here with a non-zero exit
#      ("migrations apply cleanly" gate).
#   3. Executes supabase/tests/0006-0009_schema_rls_constraints.sql against
#      the fresh DB with ON_ERROR_STOP=1 — every assertion RAISEs on failure,
#      so any failure exits non-zero.
#
# Usage:  bash supabase/tests/run_schema_tests.sh
# Requires: supabase CLI + Docker (supabase start / db reset); psql or
#           docker CLI to run the assertion SQL.
# NOTE (#885): config.toml now enables [auth.captcha] (fail-closed). If
#           `supabase start` fails to boot the auth container, set
#           TURNSTILE_SECRET_KEY in .env (Turnstile TEST secret) or
#           temporarily flip [auth.captcha] enabled = false locally.
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_SQL="$REPO_ROOT/supabase/tests/0006-0009_schema_rls_constraints.sql"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 0. Dependencies ─────────────────────────────────────────────────────────
command -v supabase >/dev/null 2>&1 || die "supabase CLI not found — install: brew install supabase/tap/supabase"
if ! command -v docker >/dev/null 2>&1; then
  echo "⚠️  'docker' not found on PATH."
  command -v psql >/dev/null 2>&1 \
    && echo "    Will try the local psql fallback (postgresql://postgres:postgres@127.0.0.1:54322/postgres)." \
    || die "neither docker nor psql available — cannot run the assertion SQL. Install Docker Desktop (or psql)."
fi

# ── 1. Local stack running? ─────────────────────────────────────────────────
log "Checking local Supabase stack..."
if ! supabase status >/dev/null 2>&1; then
  log "Starting local Supabase stack (supabase start — needs Docker)..."
  supabase start || die "supabase start failed (is Docker running?)"
fi

# ── 2. Fresh DB — migrations must apply cleanly ─────────────────────────────
log "Resetting database and applying migrations 0001–0009 (supabase db reset)..."
supabase db reset --no-seed 2>/dev/null || supabase db reset

log "Migrations applied. Migration list:"
supabase migration list 2>&1 | tail -15

# ── 3. Run the assertion SQL ────────────────────────────────────────────────
log "Running SQL assertions (supabase/tests/0006-0009_schema_rls_constraints.sql)..."

RUN_SQL() { # $1 = command prefix that streams SQL on stdin
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    CONTAINER="$(docker ps --filter 'name=supabase_db_' --format '{{.Names}}' | head -1)"
    [ -n "$CONTAINER" ] || die "supabase db container not found — is the stack running?"
    # shellcheck disable=SC2086
    docker exec -i "$CONTAINER" psql -U postgres -d postgres -v ON_ERROR_STOP=1 < "$TEST_SQL"
  elif command -v psql >/dev/null 2>&1; then
    psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -v ON_ERROR_STOP=1 -f "$TEST_SQL"
  else
    die "no SQL runner available (docker or psql required)"
  fi
}

RUN_SQL || die "SQL assertions failed (see output above)"

printf '\n\033[1;32m✅ ALL ASSERTIONS PASSED — migrations 0006–0009 apply cleanly; RLS + constraints verified.\033[0m\n'
