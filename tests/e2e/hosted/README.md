# Hosted Platform E2E Suite (#303 — 12 detailed test cases)

Automated end-to-end tests for the Tortoise Hosted Platform, driving the
**real deployment artifact** — `uvicorn tortoise.hosted_api:app` booted as a
subprocess on embedded FalkorDBLite — over real HTTP with pytest-playwright
`APIRequestContext`. CI job: `hosted-e2e` in `.github/workflows/ci.yml`.

## Reconstruction note

The epic's design docs (`docs/epics/2026-08-03-tortoise-hosted-platform/04-plan.md`
and `05-test-design.md`) were **lost in the eldato→tortoise migration**. The
12-case design here is reconstructed from the #303 issue body, capstone #291's
journey, the surviving `-D` markers in code (E2E-3-D billing, E2E-6-D
export/delete, E2E-7-D security, E2E-4-D/8-D data-model plan), and the
surviving user-journeys E2E designs. Full reconstruction record:
`docs/plans/2026-08-12-303-hosted-e2e-suite.md`.

## Run

```bash
# Local hermetic mode (CI default): boots the server, zero secrets, ~1-3 min
RUN_HOSTED_E2E=1 python -m pytest tests/e2e/hosted/ -q -rs

# Remote mode against a staging deployment (no server boot):
E2E_BASE_URL=https://staging.example.co RUN_HOSTED_E2E=1 ALLOW_PROD=1 \
  python -m pytest tests/e2e/hosted/ -q -rs
# ALLOW_PROD=1 is required for ANY non-loopback target, regardless of scheme
# (signup-safety precedent — an http:// tunnel fronting prod is still prod).
```

Without `RUN_HOSTED_E2E` / `E2E_BASE_URL` the suite skips gracefully with a
clear message (RUN_LEGAL_E2E pattern).

### Remote mode is a subset

Remote targets run real rate limiting and do not share the local seams, so
the suite skips the cases that cannot pass against an arbitrary deployment:

- **E2E-1-D direct register tests** — the server-side register limiter
  counts every `/v1/register` POST (3/hr/IP, consumed before validation);
  the shared tenant pool needs the whole budget.
- **E2E-2-D / E2E-3-D** — webhook legs are signed with the fixture
  `STRIPE_WEBHOOK_SECRET` and resolve prices via the fixture
  `STRIPE_PRICE_IDS` catalog; caps are asserted against the fixture pricing
  file.
- **E2E-5-D / E2E-12-D / bare-server legs** — local-only seams.

Remaining request volume is modest, but the target's global request limiter
(100/min by default) still applies — point remote mode at a deployment with
headroom (or `RATE_LIMIT_DISABLED=1`).

## The 12 cases

| File | Case | Journey stage |
|---|---|---|
| test_01_signup_provision.py | E2E-1-D | signup → provisioning → API key → first Point |
| test_02_free_tier_limits.py | E2E-2-D | free tier limits (fail-closed 402) |
| test_03_billing_upgrade.py | E2E-3-D | Pro upgrade/billing (hermetic signed webhooks) |
| test_04_tenant_isolation.py | E2E-4-D | tenant isolation + revocation |
| test_05_backup_restore.py | E2E-5-D | backup → restore round-trip |
| test_06_export_delete.py | E2E-6-D | owner-only export + team deletion |
| test_07_security_baseline.py | E2E-7-D | security baseline (auth matrix, HSTS, posture) |
| test_08_multi_team.py | E2E-8-D | multi-team membership, invites, RBAC |
| test_09_github_integration.py | E2E-9-D | GitHub connect/status/callback/index |
| test_10_session_capture.py | E2E-10-D | agent session capture (regex extraction) |
| test_11_mcp_connect.py | E2E-11-D | MCP initialize → tools/list → tools/call |
| test_12_selfhost_migration.py | E2E-12-D | self-hoster migration (parity journey) |

Every case carries ≥2 negative tests.

## Hermetic seams (local mode only)

- `TORTOISE_BACKUP_STORAGE=memory` (#303 seam in `hosted_api._backup_storage`)
  + `fixtures/pricing-e2e.json` via `TORTOISE_PRICING_PATH` (pro/team
  `daily_backups: true`, `e2e_small` cap tier) → E2E-5-D.
- Self-signed Stripe webhooks against a local `STRIPE_WEBHOOK_SECRET` with a
  local `STRIPE_PRICE_IDS` catalog → hermetic tier bump, zero Stripe network
  (E2E-3-D; the real-checkout live leg stays with the precedent suite
  `tests/e2e/test_billing_upgrade.py`, gated on `STRIPE_TEST_*`).
- Local JWKS mock + minted RS256 JWTs for the session-auth plane
  (E2E-6-D/8-D).
- Second subprocesses: a minimal-env "bare" server (unconfigured negatives)
  and the selfhost daemon (E2E-12-D).

Remote mode skips the cases whose seams are local-only (E2E-5-D, E2E-12-D,
bare-server legs) and shares ≤3 tenants (server-side register limit 3/hr/IP).

## Embedded daemon hygiene (shared dev machines)

Hermetic runs boot embedded FalkorDBLite/redislite daemons under `~/.tortoise`.
On shared dev boxes these can accumulate between sessions — the #176/#1003§4
leak class (hundreds of orphaned `redis-server` daemons pinning temp sockets,
~10 GB RSS, plus stale `~/.tortoise/index-*.pid` files).

- **Boot-timeout flakes are environmental.** The 60s boot budget (30s request
timeouts mid-suite) fails under CPU contention from orphaned daemons. Before
blaming the suite, check `pgrep -fc 'redislite/bin/redis-server'` — a clean
machine should show 0–2 daemons, not dozens.
- **Ops sweep** (after confirming no live sessions are running):

  ```bash
  pkill -f 'redislite/bin/redis-server'   # orphaned embedded daemons
  rm -f ~/.tortoise/index-*.pid           # stale pid files (reaper clears
                                          # sockets, not pid files)
  ```

- **Fixture-scoping/teardown:** session-shared embedded DB audit is #1012
(closed via PR #1108, orphans ~46 → 1); the per-session atexit/lifecycle bound
for test-spawned daemons is tracked as a follow-up (see #1003 status).
