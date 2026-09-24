#!/usr/bin/env bash
# ============================================================================
# availability-watchdog.sh — out-of-band availability probe + rate-limited
# self-healing + deduplicated alerting for the hosted API (#2850).
#
# WHY THIS EXISTS
# ---------------
# 2026-09-10, ~19:10–19:55 UTC: https://api.premiselabs.co (Fly app
# `tortoise-y4mjjq`) was fully unreachable for ~45 minutes and NOBODY WAS
# PAGED. It was found by a user reading a browser console error and ended by a
# human typing `flyctl machine restart`.
#
# Nothing in the stack could have caught it:
#   * Sentry runs INSIDE the process → structurally blind to a process that is
#     alive-but-not-serving. The failure class raises NO exception; the Fly
#     proxy simply stops routing ("no known healthy instances found for route
#     tcp/443") and every public request hangs until it times out.
#   * There is exactly ONE machine, so its de-registration IS a total outage.
#   * The Fly HTTP health check flaps and then goes quiet — its signal is the
#     same silence as "no traffic".
#
# So this probe deliberately runs OUTSIDE Fly (GitHub Actions, a different
# failure domain) and asserts the REAL USER PATH — an authenticated API route
# served by the app — not merely that a socket is open.
#
# WHAT IT DOES
#   1. PROBE   `GET <PROBE_URL>` (default GET /v1/organizations) with a generous
#              per-request timeout and N attempts before declaring failure, so
#              a transient blip cannot fire a false alarm.
#              Verdicts (see classify_code):
#                UP         — the app ANSWERED (2xx | 401 | 403 | 429)
#                DOWN       — no answer at all (000 timeout/conn) or 5xx
#                UNEXPECTED — it answered something else (3xx, 404, …)
#              A 401 is the EXPECTED unauthenticated answer for /v1/organizations
#              (source-verified: tortoise/session_auth.py verify_session_jwt
#              raises 401 "Missing session token" with zero network I/O when
#              the Authorization header is absent). 2xx and 401 both mean "the
#              app answered"; only 000/5xx mean it did not.
#              TWO PRODUCTION TARGETS (#3628). The Fly API probe is the default;
#              the workflow adds a SECOND step for the Cloudflare Pages auth
#              surface (`GET /auth/start`). During the #3616 sign-in outage only
#              /auth/start revealed it: /welcome answered 302 and /api/session
#              answered 401 the whole time, so a bare liveness probe of either
#              was GREEN while nobody could sign in. That step is a DEDICATED
#              step (not a target list) and uses three additive knobs:
#                * PROBE_EXPECT_STATUS=302 — an allow-list that replaces the
#                  hardcoded UP arms ONLY (000/5xx stay DOWN, other stays
#                  UNEXPECTED). Without it a healthy 302 is UNEXPECTED and the
#                  probe would page on a healthy site.
#                * PROBE_REQUIRE_HEADER=code_challenge_method=s256 — proof the
#                  PKCE flow row was actually written; a 302 without it is an
#                  ANSWERED-BUT-WRONG (UNEXPECTED) verdict, not an outage.
#                * its own PROBE_HOST_LABEL — the incident TITLE is the dedupe
#                  key, so two production targets with one label would fight
#                  over a single issue.
#              The auth target is in PROD_PROBE_URLS (so it files a PROD-titled
#              incident and pages) but NOT in RESTARTABLE_PROBE_URLS: it has no
#              Fly machine, and a 503 there means a missing binding, which a
#              restart cannot fix (`disarmed:no_machine`). `is_prod` is decided
#              by SET MEMBERSHIP (fail closed: an unrecognised URL stays a
#              DRILL), never by a boolean flag.
#              KNOWN BLIND SPOTS: each probe covers ONE route, and the API
#              probe only its UNAUTHENTICATED branch — an outage that leaves
#              /v1/organizations answering while other routes fail reads as UP,
#              and so does an auth-leg break that rejects every real token (the
#              probe sends none). It proves liveness + route presence, not
#              end-to-end authenticated traffic. See the runbook §6.8/§7.
#   2. ALERT   Files exactly ONE GitHub issue per incident, keyed by title, and
#              comments with an incremented count on later runs. On recovery it
#              confirms the recovery, comments and closes. This is the fix for
#              the #2706 failure mode: welcome-e2e-monitor.yml created a NEW
#              issue per failing run (its title embedded github.run_id) and
#              produced dozens of near-identical duplicates — all still open
#              (#2231 → present; 59 open at the 2026-09-11 19:41Z census, 61
#              the same evening — re-measure at cleanup time; #3014).
#   3. HEAL    If the service is confirmed DOWN for a SUSTAINED period, restart
#              the Fly machine — strictly rate limited so a database outage
#              cannot become an infinite restart loop. AWS Well-Architected
#              guidance for automated remediation: "only allow the watcher
#              service to terminate one instance every minute, and if it tries
#              to terminate more, then it sends an alert to get a human
#              involved". The analogue here (5-min cron intent, ONE machine):
#                * SUSTAINED_DOWN_MINUTES (10) of continuous failure AND
#                  SUSTAINED_MIN_RUNS (≥2) observed failing runs before the
#                  FIRST restart,
#                * RESTART_COOLDOWN_MINUTES (20) between attempts,
#                * MAX_RESTARTS_PER_HOUR (2) across a rolling hour, tracked
#                  INDEPENDENT of incident identity: a NEW incident inherits the
#                  still-in-window restart stamps left by the previous (now
#                  closed) incident, so a machine that flaps — down, restarted,
#                  recovers, incident closes, down again — cannot reset the
#                  budget at every incident boundary and restart forever;
#              when the cap is hit we do NOT restart and we comment (and page)
#              asking for a human — the "get a human involved" leg, re-notified
#              at most once per CAP_RENOTIFY_MINUTES (60).
#              A DNS-resolution or TLS/certificate failure is NOT restartable:
#              no machine restart repairs a name-resolution or certificate
#              problem, so the incident is filed and reported but the restart
#              leg is disarmed and the issue body says exactly why (the restart
#              budget is reserved for the transport-level failures a wedged
#              process actually produces).
#              An ATTEMPT is recorded to the incident BEFORE flyctl runs, so a
#              crash or a failed API call cannot erase the cooldown/cap record
#              (write-then-act: the durable record gates the side effect). A
#              failed attempt still counts against the cap — the failure mode
#              we are preventing is a restart attempt every 5 minutes.
#              MAX_RESTARTS_PER_HOUR=0 is honoured as an operator KILL SWITCH
#              (automated restarts off), not rounded up to the default.
#              Before any restart the RUNNER'S OWN EGRESS is controlled with a
#              probe of a known-good endpoint (CONTROL_URL): if the runner
#              cannot reach the internet the verdict is INCONCLUSIVE — the
#              incident is still recorded and alerted, but nothing is restarted
#              on the strength of a probe that may have failed locally.
#   4. PAGE    (optional) Telegram page on transitions only, reusing the
#              TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID secrets the DR driver
#              already uses. Absent secrets → skipped with a log line. A page
#              counts as delivered only when Telegram's own `ok` field is true
#              (the tortoise/telegram_push.py contract) — a 2xx with `ok:false`
#              is not delivery.
#   5. ESCALATE (#3887) a SUSTAINED incident reaches a person on a channel that
#              LEAVES GitHub. Transition pages (4) are the only pre-#3887 human
#              signal, and every post-run-1 page lived INSIDE the restart leg —
#              so a sustained answered-wrongly (`UNEXPECTED`) incident, which a
#              restart correctly declines, reached a human once and never
#              again. Evidence: 2026-09-16, GET /v1/organizations answered 404
#              for 11 h 19 m, one issue, no human acted, ended by an unrelated
#              deploy. The leg is keyed on the incident's SUSTAINED DURATION
#              (never on the transition, never on the restart outcome), so it
#              covers BOTH verdicts and every disarm path that reaches the
#              normal flow. Defaults: 3 x SUSTAINED_DOWN_MINUTES (30 min) AND
#              SUSTAINED_MIN_RUNS + 1 (3 runs), BOTH required — deliberately
#              stricter than the restart gate, because waking a human is the
#              costlier action, and a single failing tick satisfies neither leg.
#              Reminders at most once per CAP_RENOTIFY_MINUTES (which doubles as
#              the minimum gap between confirmed human pages of ANY kind, so the
#              cap escalation and this leg cannot double-page). Delivery is
#              FAIL-CLOSED: an undelivered page is never stamped as sent, is
#              recorded durably (`escalate_state=failed`), is retried on the
#              next run, and fails the run naming the channel — a broken pager
#              is never rendered as "all clear". A sustained DRILL escalates
#              too (that is how the leg is drilled) but still never restarts.
#              OVERRIDES: PagerDuty's "acknowledgment pauses further
#              notifications" — not adopted, because the incident body is on a
#              PUBLIC repo and this script's threat model treats it as
#              human-editable, so an ack field would be a fail-OPEN mute on the
#              pager. A bounded reminder interval is used instead. No
#              independent heartbeat either; the pager's own liveness is a
#              separate surface (tortoise #4573).
#
# STATE / DEDUPE KEY
#   The single open issue IS the incident state (no external store, no
#   variables API, no PAT). Its body carries a machine-readable one-line block
#   that this script rewrites on every run:
#     <!-- watchdog-state kind=… first_failure_ts=… down_runs=… \
#          last_down_ts=… last_comment_ts=… cap_notified_ts=… \
#          ledger_state=… ledger_src=… escalate_state=… escalate_ts=… \
#          page_ok_ts=… restarts=ts,ts -->
#   The field list is declared ONCE, in STATE_FIELDS, and a harness test asserts
#   it both ways plus the ORDER rule (`restarts` last: its parser captures the
#   remaining `[^>]*` tail). The two prose declarations here and in the runbook
#   had already drifted — both omitted `ledger_state=`/`ledger_src=` — which is
#   why the parity test exists rather than a third hand-kept copy.
#   `escalate_state`/`escalate_ts`/`page_ok_ts` are the escalation leg's memory:
#   `escalate_ts` is trusted for throttling ONLY while `escalate_state=sent` (an
#   attempt whose outcome was never recorded is retried, never trusted),
#   `page_ok_ts` records the last CONFIRMED-DELIVERED human page of any kind,
#   and both are future-clamped to 0 like every other throttle stamp because a
#   value that cannot be true must never mute the pager.
#   The title is the dedupe key — the script searches for an open issue whose
#   title contains the marker before filing, CONSTRAINED TO A MACHINE AUTHOR
#   (`author:app/github-actions` + the reserved `github-actions[bot]` login
#   re-check). Adoption additionally requires an EXACT title match and the
#   body-only `INCIDENT_STATE_MARKER` line, so an unrelated workflow's bot
#   issue whose title merely contains the loose terms is never adopted. On a
#   PUBLIC repo anyone can open an issue with our title and a forged state
#   block; adopting it would hand an attacker the sustained clock and the
#   restart ledger (a restart storm). A non-machine or non-ours match is NEVER
#   adopted, patched or closed — it is treated as "no incident" and a fresh
#   machine issue is filed.
#   The body is HUMAN-EDITABLE, so every parsed value is bounded/validated
#   (to_int), a stale clock (no failing run within STALE_RESET_MINUTES)
#   restarts the sustained window instead of trusting it, and the sustained
#   window is additionally CLAMPED to the issue's server-side `created_at`
#   (immutable, not body-editable) so no body edit can authorise a restart
#   before the incident has actually existed that long. The restart ledger is
#   FAIL-CLOSED: a `restarts=` value that is present but not fully parseable
#   refuses to act rather than silently dropping an entry (dropping could only
#   WEAKEN the cooldown/cap).
#   The ESCALATION leg's own wall-clock gate anchors on that same server-side
#   `created_at` (STATE_ESCALATE_ANCHOR_TS), NOT on the resettable
#   first_failure_ts: the stale-clock reset sets first_failure_ts to `now`,
#   which would make a >STALE_RESET_MINUTES-cadence incident's window
#   unsatisfiable forever (the exact #3887 failure), and a body-forged future
#   first_failure_ts would mute the pager. A future anchor is untrustworthy and
#   defers to the run leg. The restart leg's anchor is unchanged.
#
# SAFETY PROPERTIES
#   * No token is ever hard-coded; the Fly token comes from a repository
#     secret (FLY_API_TOKEN — the name already used by deploy-hosted.yml).
#     Absent → the restart leg is skipped with a clear log line + escalation,
#     and alerting still works.
#   * A non-member PROBE_URL (a drill) DISARMS the restart leg AND gets its
#     OWN incident identity (`[monitor] DRILL DOWN` + a `[DRILL]` suffix on the
#     host in the title): a
#     drill must never restart production, never mutate a production incident
#     (its body carries the cooldown/cap ledger), and never close one. Without
#     that separate identity a typo'd drill host files a MISLABELLED production
#     incident (and the next production run would then "recover" it).
#   * A PRODUCTION URL with no Fly machine behind it (the Pages auth surface) is
#     ALWAYS restart-disarmed (`disarmed:no_machine` on a DOWN verdict,
#     `disarmed:unexpected` on a DEGRADED one — the DEGRADED disarm is set
#     first): a 503 there means a missing binding, and restarting the API app
#     would restart an unrelated service. This is a property of the URL SET,
#     not of the failure class, so no per-run misconfiguration can arm it.
#   * A failed issue SEARCH refuses to file (never duplicate) and fails the
#     run; a non-numeric issue id is refused the same way. The monitor cannot
#     go silently deaf in the alerting direction.
#   * UNEXPECTED verdicts never restart: a restart cannot fix a route that
#     404s or a redirect, and restarting on it would be a restart loop.
#   * Nothing secret is ever published: redact_text() is applied AT THE
#     PUBLICATION BOUNDARY (create_issue / update_issue_body / comment_issue /
#     page) so it scrubs the probe URL (credentials + query string), the Fly
#     token and the Telegram token from every public body/comment/page/log
#     line — including text echoed back by flyctl. Captured command output goes
#     through scrub_output(), whose order is REFLOW → REDACT → TRUNCATE.
#     Reflowing the whitespace FIRST is what makes a WRAPPED credential
#     matchable: a real Fly token is `FlyV1 <macaroon>`, a SPACE-bearing value
#     that a captured log wraps at that space, so redacting first left both
#     fragments unmatched and the subsequent reflow re-joined them into a
#     complete, readable credential in the published body/comment/log.
#     Redacting before truncating is what keeps a long token whole:
#     truncating first splits it, after which the literal-value match
#     no longer matches the surviving prefix and a PARTIAL production token is
#     published. redact_text() also scrubs BY SHAPE (Fly `FlyV1 …` macaroons,
#     `id:secret` Telegram tokens) so a partial whose full value we do not hold
#     is still caught. stdout is DATA only (all logging is on stderr) so a log
#     line can never corrupt a captured helper return.
#   * Only a MACHINE-authored issue is ever adopted or mutated: the dedupe
#     search carries `author:app/github-actions` AND the returned item is
#     re-checked for the RESERVED `github-actions[bot]` login, an EXACT title
#     and the body-only `INCIDENT_STATE_MARKER` before its number is used, so
#     no public account (and no other workflow's bot issue) can seed or hijack
#     the incident state (see STATE above).
#   * Every notification is gated on a DURABLE record: the restart attempt and
#     the cap/INCONCLUSIVE escalation stamps are written BEFORE the side effect,
#     and a routine comment is published only after the body (which carries the
#     comment-throttle stamp) has been written. A failed state write therefore
#     means no restart, no page and no comment — never an unbounded
#     notification loop driven by state that could not be persisted.
#
# Deliberately NOT here: no paid third-party uptime service; no fly.toml or
# tortoise/ edits (owned elsewhere); no new GitHub labels (only a pre-existing
# one is used — the API silently DROPS unknown labels, which would leave the
# dedupe search blind).
#
# EXIT CODES
#   0  service UP (or recovered)
#   1  service DOWN / UNEXPECTED — the workflow FAILS so GitHub's own
#      notifications fire
#   1  the monitor itself is broken (missing GH_TOKEN/jq, failed search, failed
#      issue write/close, a drill that cannot record state) — fail loud, never
#      a deaf monitor
#
# Usage:  bash .github/scripts/availability-watchdog.sh
# Env:    see the defaults below (all overridable; the test harness drives them)
# Test:   bash .github/scripts/availability-watchdog.test.sh  (self-contained,
#         stubs curl/gh/flyctl; runs in CI job availability-watchdog)
# ============================================================================

set -euo pipefail

# ── configuration (all overridable — the test harness drives these) ─────────
# The Fly API surface. The workflow passes an EMPTY PROBE_URL on the scheduled
# path so this default applies (see availability-watchdog.yml).
DEFAULT_PROBE_URL="https://api.premiselabs.co/v1/organizations"
# The Pages auth surface (#3616 outage / #3628 gap): `GET /auth/start` is the
# ONE route that revealed the 35-minute sign-in outage — during it `/welcome`
# answered 302 and `/api/session` answered 401 the whole time, so a bare
# liveness probe of either was GREEN while nobody could sign in. The workflow
# drives this URL as a SECOND, DEDICATED STEP with its own expected status and
# required header; the probe is deliberately NOT generalised into a target
# list (that would rewrite safety-critical restart logic for no gain).
# The SESSION-BEARING origin is `app.premiselabs.co` (#4054 moved the BFF off
# the marketing project, which is the decided topology): `tortoise.*` answers
# 404 for /auth/start BY DESIGN, so probing it watched a route no user path
# touches — the same blindness #3628 was filed to end, one host to the left.
AUTH_PROBE_URL="https://app.premiselabs.co/auth/start"
# The SET of production probe URLs. `is_prod` is decided by SET MEMBERSHIP, not
# by a boolean flag: an unrecognised URL is ALWAYS a drill, so a typo'd or
# forgotten flag can never arm self-heal against an unexpected host (fail
# closed in the no-restart direction). A trailing `/` is normalised away.
# SINGLE-DASH default (not `:-`): an explicit EMPTY value must stay EMPTY so an
# operator who neutralises the set by setting it to "" gets the FAIL-CLOSED
# direction (everything is a drill) rather than `:-`'s opposite — the default
# set with self-heal ARMED. The default applies only when the variable is UNSET.
PROD_PROBE_URLS="${PROD_PROBE_URLS-$DEFAULT_PROBE_URL $AUTH_PROBE_URL}"
# Which production URLs have a Fly machine behind them and may therefore arm
# the restart leg. The auth surface is PRODUCTION for alerting and incident
# identity, but it is served by Cloudflare Pages: a 503 there means a missing
# binding, and `flyctl machine restart` on the API app cannot repair it — it
# would restart an unrelated service. Membership again (fail closed): a new
# production URL is non-restartable until explicitly added here — and an
# explicit EMPTY value stays empty (single-dash), so the kill-intent cannot
# fail OPEN into an armed restart (the `:-` form substituted the default).
RESTARTABLE_PROBE_URLS="${RESTARTABLE_PROBE_URLS-$DEFAULT_PROBE_URL}"
PROBE_URL="${PROBE_URL:-$DEFAULT_PROBE_URL}"
# Display name in titles/logs. Deliberately NOT derived from PROBE_URL: the
# title is the dedupe key and must not move when a drill overrides the URL.
# TWO production targets MUST pass DIFFERENT labels (the workflow's auth step
# sets its own) — a shared label shares ONE dedupe key, and the two surfaces
# would then fight over a single incident issue.
PROBE_HOST_LABEL="${PROBE_HOST_LABEL:-api.premiselabs.co}"
# OPTIONAL per-target expectation knobs (both empty = the built-in API
# contract below, so the existing target is byte-for-byte unchanged).
# PROBE_EXPECT_STATUS: a SPACE-SEPARATED allow-list of HTTP status codes that
#   mean UP. The 000/5xx=DOWN arm is checked FIRST and cannot be overridden —
#   listing a 5xx here does NOT make it healthy — so a target can declare "302
#   is healthy" on the Pages auth route without disarming the outage class.
#   Other answered codes stay UNEXPECTED. A malformed list fails closed.
# PROBE_REQUIRE_HEADER: a case-insensitive SUBSTRING that the RESPONSE HEADERS
#   must carry for an otherwise-UP answer to count as UP. The auth target asks
#   for `code_challenge_method=s256` — proof the PKCE flow row was actually
#   written. A healthy-looking status with the header missing is an
#   ANSWERED-BUT-WRONG verdict (not an outage). NEVER put a secret here: the
#   requirement is published in the incident body (the header DUMP is not).
PROBE_EXPECT_STATUS="${PROBE_EXPECT_STATUS:-}"
PROBE_REQUIRE_HEADER="${PROBE_REQUIRE_HEADER:-}"
PROBE_TIMEOUT_S="${PROBE_TIMEOUT_S:-25}"          # per-request --max-time
PROBE_CONNECT_TIMEOUT_S="${PROBE_CONNECT_TIMEOUT_S:-10}"
PROBE_ATTEMPTS="${PROBE_ATTEMPTS:-3}"             # retries before DOWN
PROBE_RETRY_SLEEP_S="${PROBE_RETRY_SLEEP_S:-10}"
# Recovery hysteresis: extra confirmation probes that must ALSO answer UP
# before the incident is closed (0 disables). Prevents a single flapping
# success from closing a live incident and resetting the sustained clock.
RECOVERY_CONFIRM_PROBES="${RECOVERY_CONFIRM_PROBES:-1}"
SUSTAINED_DOWN_MINUTES="${SUSTAINED_DOWN_MINUTES:-10}"
# Minimum number of OBSERVED failing runs before the first restart. Guards the
# case where the issue body carries an old/large first_failure_ts (a reopened
# or human-edited incident): the wall clock alone must not authorise a restart.
SUSTAINED_MIN_RUNS="${SUSTAINED_MIN_RUNS:-}"
RESTART_COOLDOWN_MINUTES="${RESTART_COOLDOWN_MINUTES:-20}"
MAX_RESTARTS_PER_HOUR="${MAX_RESTARTS_PER_HOUR:-2}"
COMMENT_THROTTLE_MINUTES="${COMMENT_THROTTLE_MINUTES:-15}"
CAP_RENOTIFY_MINUTES="${CAP_RENOTIFY_MINUTES:-60}"
# A failing run must have been seen within this window for the sustained clock
# to be trusted; otherwise the clock is reset to now (a stale incident — e.g.
# one a human reopened after recovery — must not restart production instantly).
# It must be well ABOVE the 5-min cadence: GitHub delays/drops scheduled runs
# under load, and a reset also re-arms the sustained window, so a value near the
# cadence would re-arm the window on every run (see the stale branch in main).
STALE_RESET_MINUTES="${STALE_RESET_MINUTES:-45}"
EVIDENCE_MAX_CHARS="${EVIDENCE_MAX_CHARS:-4000}"
FLY_APP="${FLY_APP:-tortoise-y4mjjq}"
FLY_API_TOKEN="${FLY_API_TOKEN:-}"
GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
REPO="${GITHUB_REPOSITORY:-daniel-ospina/tortoise}"
# Runner-side egress control: an endpoint wholly outside this app's failure
# domain. Only consulted when the app probe says DOWN and a restart is on the
# table, so UP runs cost nothing.
CONTROL_URL="${CONTROL_URL:-https://www.google.com/generate_204}"
CONTROL_TIMEOUT_S="${CONTROL_TIMEOUT_S:-10}"
CONTROL_ATTEMPTS="${CONTROL_ATTEMPTS:-2}"
# Pre-existing label only (see header). Applied when filing; NOT used to
# filter the dedupe search (a renamed label would empty the search and turn the
# monitor back into a duplicate-issue spammer).
ALERT_LABEL="${ALERT_LABEL:-auto-filed}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

# ── sustained-incident escalation (#3887) ───────────────────────────────────
# Every page() call site below is TRANSITION-based: one fires when an incident
# is filed, and the only post-run-1 pages live INSIDE the restart leg (cap /
# heal_failed / no_egress). So the classes with the least self-healing coverage
# — an answered-wrongly (`UNEXPECTED`) verdict, which correctly never restarts,
# and any DOWN on a restart-disarmed target — had NO escalation after run 1.
# Evidence: 2026-09-16, `GET /v1/organizations` answered 404 for 11 h 19 m; one
# incident issue, one transition page, no human acted; it ended only when an
# unrelated deploy landed (#3887).
#
# This leg is that escalation. It is keyed on the incident's SUSTAINED DURATION
# — NOT on the transition and NOT on the restart outcome — so it covers every
# verdict and every disarm path that reaches the normal flow. BOTH threshold
# legs are required (wall-clock AND observed failing runs) and both are
# deliberately STRICTER than the restart gate's: waking a human is the costlier
# action, so it gets the higher bar. A single failing tick satisfies NEITHER
# leg, so one bad probe can never page.
#
# REAL CADENCE, not nominal: the probe's cron intent is 5 min, but its MEASURED
# delivery is ~96 runs/day — one run per ~15 min (`availability-record.sh`
# header: 452 runs / 113.0 h = 33% of cron; 36% during the incident it
# measured, whose 48 failing runs over 11 h 19 m were 14.1 min apart). So the
# defaults are DERIVED from the SUSTAINED_* family — one declared relation
# instead of two literal pairs free to drift: 3 x SUSTAINED_DOWN_MINUTES =
# 30 min, and SUSTAINED_MIN_RUNS + 1 = 3 observed runs. `down_runs` reaches 1 on
# the run that sets the first-failure stamp, so the 3rd observed run is 2 probe
# intervals later — ~30 min at the measured ~15 min/run, binding together with
# the 30-minute floor. The range extends past that only when runs are spaced
# slower than ~15 min (up to ~45 min at one run per ~22 min); either way the page
# lands early in an 11-hour incident instead of never.
#
# OVERRIDES: PagerDuty's "acknowledgment pauses further notifications" — NOT
# adopted, because the incident body is on a PUBLIC repo and this script's own
# threat model treats it as human-editable, so an ack field would be a
# fail-OPEN mute on the pager (the worst possible direction). A bounded
# reminder interval is used instead.
#
# The recipient is CONFIGURABLE and defaults to the ops chat the transition
# pages already use, so sustained-incident escalation can be pointed at a
# different chat (e.g. an on-call group) without moving transition paging.
ESCALATION_CHAT_ID="${ESCALATION_CHAT_ID:-$TELEGRAM_CHAT_ID}"
# 0 is an operator KILL SWITCH (no sustained escalation; logged loudly, and no
# stamp is written, so re-enabling resumes on the next run).
ESCALATE_ENABLED="${ESCALATE_ENABLED:-1}"
# Empty => DERIVED in main() from the NORMALIZED sustained thresholds (see
# above), which is also what enforces the `escalate >= restart` invariant.
# Set explicitly to override; an explicit value below the restart threshold is
# clamped UP (fail closed toward the later page), with a warning.
ESCALATE_SUSTAINED_MINUTES="${ESCALATE_SUSTAINED_MINUTES:-}"
ESCALATE_MIN_RUNS="${ESCALATE_MIN_RUNS:-}"
# Test seam: pin "now" so cooldown/velocity arithmetic is deterministic.
WATCHDOG_NOW_EPOCH="${WATCHDOG_NOW_EPOCH:-}"

# ── the incident state block's field list, declared ONCE (round 5) ──────────
# `state_block()` renders these names in THIS order, and the harness asserts
# both directions (every declared name is rendered; every rendered `name=`
# token is declared) plus the ORDER rule: `restarts` MUST stay LAST, because
# its parser captures the remaining `[^>]*` tail — a field rendered after it
# would be swallowed, the strict ledger parser would then reject the value, and
# self-healing would fail closed forever. The list previously lived in five
# independent places (this writer, nine parse_state regexes, three regexes in
# `availability-record.sh`, the harness fixtures, and the runbook prose) with
# NO parity assertion, and it had already drifted: both prose declarations
# omitted `ledger_state=`/`ledger_src=` — so a parity test is the fix, not a
# fifth copy.
STATE_FIELDS="kind first_failure_ts down_runs last_down_ts last_comment_ts cap_notified_ts ledger_state ledger_src escalate_state escalate_ts page_ok_ts restarts"

# A body-only marker for machine incidents. `in:title "…"` is an
# order-insensitive AND of loose terms (NOT an exact phrase), and this PUBLIC
# repo carries hundreds of bot-authored monitor issues, so title text alone is
# not proof that an issue is OURS. This literal appears in every body we write
# and in no other producer's, so the dedupe requires it — plus an EXACT title
# match and the reserved `github-actions[bot]` login — before it will adopt,
# patch, or close a returned item (round 3, P2-2/P2-3).
INCIDENT_STATE_MARKER='<!-- availability-watchdog-state -->'

DOWN_MARKER="[monitor] PROD DOWN"
DOWN_TITLE="${DOWN_MARKER} — ${PROBE_HOST_LABEL} is not answering the availability probe"
DEGRADED_MARKER="[monitor] PROD DEGRADED"
DEGRADED_TITLE="${DEGRADED_MARKER} — ${PROBE_HOST_LABEL} answered the availability probe unexpectedly"

# The label published when the probe URL cannot be parsed safely (see
# probe_host_label). A constant, so an unparseable DRILL URL can never fall
# back to the production label and read as a production outage.
PROBE_HOST_REDACTED="<redacted-host>"

# Host label for the probe URL (a drill's issue must name the host it actually
# probed). Falls back to the REDACTED placeholder above when the URL cannot be
# parsed — never to `$PROBE_HOST_LABEL`, which in the drill branch is the
# PRODUCTION label and would mislabel a drill as a production outage.
# SECURITY: the host is taken WITHOUT the userinfo — `https://user:s3cr3t@h/`
# must not put credentials in a public issue TITLE (redact_url() strips them
# from the full URL, but this label is published on its own). The naive
# "cut the authority at the first `/`, then strip up to `@`" split leaks a
# PASSWORD when a hand-written userinfo contains a `/` (legal in practice, not
# per RFC 3986): `rediss://user:pa/ss@host:6379` made the authority `user:pa`
# — the password prefix — because the `@` fell outside the cut. So an `@` that
# is present in the string but ABSENT from the authority we cut is treated as a
# parse failure and the label is redacted outright.
probe_host_label() { # <url>
  local rest authority host
  rest="$1"
  # 1. drop the scheme (a scheme-less URL — a hand-written drill — still parses)
  case "$rest" in
    *://*) rest="${rest#*://}" ;;
  esac
  [ -n "$rest" ] || { printf '%s' "$PROBE_HOST_REDACTED"; return 0; }
  # 2. authority = everything before the first `/`, `?` or `#`
  authority="${rest%%[/?#]*}"
  # 3. an `@` in the remainder that the authority does not contain means the
  #    userinfo was NOT where the split assumed it was (see the header) — fail
  #    closed instead of publishing whatever we happened to cut.
  case "$rest" in
    *@*)
      case "$authority" in
        *@*) : ;;  # normal: the userinfo sits inside the authority
        *) printf '%s' "$PROBE_HOST_REDACTED"; return 0 ;;
      esac ;;
  esac
  # 4. strip the userinfo (everything up to the LAST `@`) and the `:port`
  host="${authority##*@}"
  host="$(printf '%s' "$host" | sed -E 's/:[0-9]+$//' || true)"
  if [ -n "$host" ]; then printf '%s' "$host"; else printf '%s' "$PROBE_HOST_REDACTED"; fi
}

# The incident IDENTITY (marker + title + label) is per-target. The title is the
# dedupe key, so a drill needs its own: reusing the production marker would make
# a drill adopt — and WRITE ITS STATE INTO — the live production incident, and a
# typo'd drill host would file a production-titled issue that the next
# production run would then "recover".
set_incident_identity() { # <is_prod>
  if [ "$1" = "1" ]; then
    # A per-target label is REQUIRED when more than one production target
    # exists: the title is the dedupe key. The workflow's auth step therefore
    # passes PROBE_HOST_LABEL=app.premiselabs.co explicitly — and the label MUST
    # be the host the step actually probes, because an incident is found by
    # EXACT title: a label that drifts from the URL retires the old incident
    # instead of resolving it (the harness pins the pair, derived).
    PROBE_HOST_LABEL="${PROBE_HOST_LABEL:-api.premiselabs.co}"
    DOWN_MARKER="[monitor] PROD DOWN"
    DEGRADED_MARKER="[monitor] PROD DEGRADED"
  else
    PROBE_HOST_LABEL="$(probe_host_label "$PROBE_URL") [DRILL]"
    DOWN_MARKER="[monitor] DRILL DOWN"
    DEGRADED_MARKER="[monitor] DRILL DEGRADED"
  fi
  DOWN_TITLE="${DOWN_MARKER} — ${PROBE_HOST_LABEL} is not answering the availability probe"
  DEGRADED_TITLE="${DEGRADED_MARKER} — ${PROBE_HOST_LABEL} answered the availability probe unexpectedly"
}

RUN_TMP="$(mktemp -d)"
trap 'rm -rf "$RUN_TMP"' EXIT

# ── logging ─────────────────────────────────────────────────────────────────
# ALL logging goes to STDERR. Several helpers (search_open_alert, do_restart)
# return their value on STDOUT and are called inside `$( )` — a log line on
# stdout would be captured into the return value and corrupt it (a __ERR__
# sentinel merged with a warning string was read as an issue number). stdout is
# DATA only; the harness asserts it stays empty on every case.
log()  { echo "$*" >&2; }
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  note() { echo "::notice::$*" >&2; }
  warn() { echo "::warning::$*" >&2; }
  fail() { echo "::error::$*" >&2; }
else
  note() { echo "NOTICE: $*" >&2; }
  warn() { echo "WARNING: $*" >&2; }
  fail() { echo "ERROR: $*" >&2; }
fi

now_epoch() {
  if [ -n "$WATCHDOG_NOW_EPOCH" ]; then
    printf '%s' "$WATCHDOG_NOW_EPOCH"
  else
    date -u +%s
  fi
}

# epoch -> ISO-8601 Z. GNU date first (CI runners), BSD date fallback (macOS
# local dev — the harness runs on both).
fmt_iso() {
  date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null && return 0
  date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null && return 0
  printf 'epoch:%s' "$1"
}

# ISO-8601 (GitHub's server-side timestamps) -> epoch, or "" when unparseable.
# The `epoch:<n>` form is accepted so this round-trips fmt_iso's own fallback
# (and works on a platform with neither GNU nor BSD date). A "" return is the
# fail-closed direction: the sustained-window anchor is then treated as
# unusable and the window is restarted, never trusted.
iso_to_epoch() { # <iso|epoch:n> -> epoch or ""
  local iso="$1" e
  [ -n "$iso" ] || { printf ''; return 0; }
  case "$iso" in
    epoch:*)
      # Only the numeric fallback form fmt_iso emits is accepted; anything else
      # is an unusable anchor (fail closed, not a trusted clock).
      e="${iso#epoch:}"
      case "$e" in
        ''|*[!0-9]*) printf ''; return 0 ;;
      esac
      printf '%s' "$e"; return 0 ;;
  esac
  e="$(date -u -d "$iso" +%s 2>/dev/null || true)"
  if [ -z "$e" ]; then
    # BSD date is strict about the literal 'Z'; try both spellings.
    e="$(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$iso" +%s 2>/dev/null || true)"
  fi
  if [ -z "$e" ]; then
    e="$(date -u -j -f "%Y-%m-%dT%H:%M:%S" "${iso%Z}" +%s 2>/dev/null || true)"
  fi
  case "$e" in
    ''|*[!0-9]*) printf ''; return 0 ;;
  esac
  printf '%s' "$e"
}

# bash arithmetic is DECIMAL-BY-SURPRISE: $((08)) is invalid octal and aborts
# the shell. The state block lives in a human-editable issue body, so every
# parsed integer is normalized here (leading zeros stripped, absurd lengths
# rejected) before it can reach $(( )).
to_int() { # <raw> <default>
  local raw digits
  raw="$(printf '%s' "${1:-}" | tr -dc '0-9')"
  [ -n "$raw" ] || { printf '%s' "$2"; return 0; }
  digits="${#raw}"
  [ "$digits" -le 12 ] || { printf '%s' "$2"; return 0; }
  raw="$(printf '%s' "$raw" | sed 's/^0*//')"
  [ -n "$raw" ] || raw="0"
  printf '%s' "$raw"
}

# int_or <env value> <default> <min> — tolerate a missing/garbage env override
# (e.g. PROBE_ATTEMPTS=0 would otherwise "confirm" an outage from zero probes).
int_or() {
  local v
  v="$(to_int "${1:-}" "$2")"
  [ "$v" -ge "$3" ] 2>/dev/null || v="$2"
  printf '%s' "$v"
}

# A probe URL may carry credentials (user:pass@) AND a query string that can
# carry a token. The issue body is PUBLIC — publish neither.
# The userinfo strip must not assume the authority cut comes first: a
# hand-written userinfo containing a `/` (`rediss://user:pa/ss@host:6379`) put
# the `@` OUTSIDE the naive `[^@/]*` window, so the sed-only version published
# the password prefix VERBATIM in the issue body. An `@` that is present in the
# remaining string but absent from the authority we cut therefore means the URL
# is unparseable, and the whole authority is replaced with `<redacted-url>`
# rather than guessed at.
redact_url() {
  local url="$1" scheme="" rest auth tail
  case "$url" in
    *://*) scheme="${url%%://*}://"; rest="${url#*://}" ;;
    *)     rest="$url" ;;
  esac
  # The authority ends at the first `/`, `?` or `#`.
  auth="${rest%%[/?#]*}"
  tail="${rest#"$auth"}"
  case "$rest" in
    *@*)
      case "$auth" in
        *@*) auth="<redacted>@${auth##*@}" ;;
        *)   printf '%s<redacted-url>' "$scheme"; return 0 ;;
      esac ;;
  esac
  printf '%s%s%s' "$scheme" "$auth" \
    "$(printf '%s' "$tail" | sed -E 's#\?.*$#?<redacted>#' || true)"
}

# Scrub anything secret out of text that ends up in a public issue body, a
# comment, or an Actions log: the raw probe URL, query-string credentials, the
# Fly token and the Telegram bot token — by VALUE and, belt-and-braces, by
# SHAPE. The shape pass is what catches a token we do not hold the value of (a
# token from another environment, or a PARTIAL already split by truncation) and
# it is why redaction must happen at the CAPTURE site, not only at the
# publication boundary.
redact_text() {
  local t="$1"
  if [ -n "$FLY_API_TOKEN" ]; then t="${t//$FLY_API_TOKEN/<redacted>}"; fi
  if [ -n "$TELEGRAM_BOT_TOKEN" ]; then t="${t//$TELEGRAM_BOT_TOKEN/<redacted>}"; fi
  if [ -n "$PROBE_URL" ]; then t="${t//$PROBE_URL/$(redact_url "$PROBE_URL")}"; fi
  printf '%s' "$t" | sed -E \
    -e 's#([?&](token|key|secret|sig|signature|api_key|apikey|access_token)=)[^&"[:space:]]*#\1<redacted>#g' \
    -e 's#FlyV1[[:space:]]+[A-Za-z0-9_=+/.,-]+#<redacted>#g' \
    -e 's#fm2_[A-Za-z0-9_=+/.,-]{20,}#<redacted>#g' \
    -e 's#[0-9]{6,12}:[A-Za-z0-9_-]{30,}#<redacted>#g'
}

# head -c closes the pipe early → SIGPIPE (exit 141) on the writer, which under
# `set -e` would abort an assignment. `|| true` keeps the truncation total.
first_chars() { # <text> <max>
  printf '%s' "$1" | head -c "$2" || true
}

# Capture-then-publish helper for COMMAND OUTPUT we are about to put in a
# public body/comment/log. The ORDER is the whole point:
#   1. REFLOW the whitespace to a single line, THEN
#   2. REDACT by value and by shape, THEN
#   3. TRUNCATE.
# (1) is load-bearing and was round 1's bug: a real Fly token is
# `FlyV1 <macaroon>` — a SPACE-bearing value — and a captured log wraps it at
# that space. Redacting FIRST left both fragments unmatched (the literal value
# contains a space, the log contained a newline) and the reflow that followed
# then RE-JOINED them into a complete, readable credential in the published
# body/comment/log. `tr -s '[:space:]' ' '` (not `tr '\n' ' '`) also folds the
# `\r` of a CRLF wrap, so `FlyV1\r\nfm2_…` reassembles to the exact token.
# (2) before (3) keeps a long token whole: truncating first splits it, after
# which the literal-value match no longer matches the surviving PREFIX and a
# partial production token would be published.
scrub_output() { # <text> <max>
  local t
  t="$(printf '%s' "$1" | tr -s '[:space:]' ' ' || true)"
  t="$(redact_text "$t")"
  first_chars "$t" "$2"
}

# ── verdict classification ──────────────────────────────────────────────────
# UP         — the app answered. 2xx is the obvious one; 401/403 mean "the
#              route exists and the app is enforcing auth" (the expected
#              unauthenticated answer for /v1/organizations); 429 means "the app
#              answered and is throttling us" (our probe being throttled is not
#              an outage).
# DOWN       — no answer (000: timeout / connection error) or a 5xx.
# UNEXPECTED — it answered something else: a 3xx (curl does not follow
#              redirects, so a moved route shows up) or a 4xx-other (404 = the
#              route is GONE — a deploy regression, not an outage).
classify_code() { # <status>
  local code="$1" want
  # EXPLICIT ALLOW-LIST (PROBE_EXPECT_STATUS set): only the listed codes are UP.
  # The 000/5xx DOWN arm is checked FIRST and is NOT overridable by the list — a
  # 5xx or 000 must never be listable as healthy, so a malformed list fails
  # CLOSED (a genuine outage still alerts) instead of silently disarming the
  # probe. The list can only widen which ANSWERED codes count as UP. Other
  # answered codes stay UNEXPECTED. This is the #3628 fix: the hardcoded `2??`
  # arm classified the Pages auth route's healthy 302 as UNEXPECTED, so a naive
  # probe of /auth/start would have paged on a perfectly healthy site.
  if [ -n "$PROBE_EXPECT_STATUS" ]; then
    case "$code" in
      000|5??)     printf 'DOWN'; return 0 ;;
    esac
    for want in $PROBE_EXPECT_STATUS; do
      if [ "$code" = "$want" ]; then printf 'UP'; return 0; fi
    done
    printf 'UNEXPECTED'
    return 0
  fi
  case "$code" in
    2??)         printf 'UP' ;;
    401|403|429) printf 'UP' ;;
    000|5??)     printf 'DOWN' ;;
    *)           printf 'UNEXPECTED' ;;
  esac
}

# curl exit code -> the LAYER that failed. A "no answer" (000) can come from
# very different layers, and only some of them are a restart's business:
# a WEDGED PROCESS shows up as a transport-level failure (the app refused the
# connection, timed out, or answered 5xx), which a restart can clear; a
# DNS-resolution or TLS/certificate failure is a name/service/certificate
# problem that no machine restart can repair — restarting on one only burns the
# automated-restart budget and adds noise to an incident a human must fix
# (review round 2, P2).
#   dns       — curl 6 (could not resolve host)
#   tls       — curl 35/51/58/59/60/66/77/80/82/83/90/91 (handshake/cert/SNI)
#   timeout   — curl 28
#   refused   — curl 7
#   transport — any other non-zero curl exit (56 recv failure, 52 empty reply…)
#   app       — curl itself succeeded; the HTTP status carries the story
#   none      — nothing recorded
classify_failure() { # <curl_exit_code> <stderr>
  local rc="$1" text="$2"
  case "$rc" in
    0)                                    printf 'app';     return 0 ;;
    6)                                    printf 'dns';     return 0 ;;
    28)                                   printf 'timeout'; return 0 ;;
    7)                                    printf 'refused'; return 0 ;;
    35|51|58|59|60|66|77|80|82|83|90|91)   printf 'tls';     return 0 ;;
    # 52 (empty reply), 55 (send error) and 56 (recv error) are the transport
    # signatures of a process that is HALF-DEAD mid-connection — a wedged
    # process, which a restart clears. `OpenSSL SSL_read … unexpected eof`
    # under 56 is exactly that signature, not a certificate problem, so these
    # codes NEVER take the message fallback below (round 3, P2-8).
    52|55|56)                              printf 'transport'; return 0 ;;
  esac
  # Some builds / a TLS-terminating proxy report a cert or name failure with a
  # generic code; the message is then the only signal. Match case-insensitively.
  # The tokens are deliberately NARROW: bare `ssl`/`tls` matched the half-dead
  # `SSL_read … eof` text and DISARMED restarting on it — a restart candidate
  # (round 3, P2-8). Only an explicit certificate/handshake phrase counts.
  case "$(printf '%s' "$text" | tr 'A-Z' 'a-z')" in
    *"certificate"*|*"self-signed"*|*"self signed"*|*"handshake failure"*) printf 'tls'; return 0 ;;
    *"resolve host"*|*"could not resolve"*|*"name or service not known"*|*"nodename nor servname"*) printf 'dns'; return 0 ;;
  esac
  printf 'transport'
}

# 0 = this failure class IS a restart's business; 1 = a restart cannot fix it.
restartable_failure() { # <class>
  case "$1" in
    dns|tls) return 1 ;;
    *)       return 0 ;;
  esac
}

# Case-insensitive SUBSTRING match over a captured response-header dump. The
# requirement is matched against the whole dump (status line + every header),
# so `code_challenge_method=s256` matches the `Location:` header of a 302
# whatever the header-name or value casing (S256). The dump itself is NEVER
# published: response headers carry Set-Cookie and other session material and
# the incident body is public. An empty needle never fails a healthy target —
# the caller checks for one first.
header_satisfied() { # <file> <needle>
  local needle
  needle="$(printf '%s' "$2" | tr 'A-Z' 'a-z')"
  [ -n "$needle" ] || return 0
  case "$(tr 'A-Z' 'a-z' < "$1" 2>/dev/null || true)" in
    *"$needle"*) return 0 ;;
    *)           return 1 ;;
  esac
}

# ── production-target classification ────────────────────────────────────────
# SET MEMBERSHIP, deliberately not a boolean flag (#3628). The workflow drives
# TWO production targets (the Fly API and the Pages auth surface); a
# forgotten/typo'd `IS_PROD=1` on a new target would arm self-heal against an
# unexpected host. With membership an unrecognised URL is ALWAYS a drill — the
# failure direction is "no restart, [DRILL]-titled incident", never "restart an
# unknown host". A trailing `/` is normalised away on both sides.
is_production_url() { # <url>
  local u p
  u="${1%/}"
  for p in $PROD_PROBE_URLS; do
    if [ "$u" = "${p%/}" ]; then return 0; fi
  done
  return 1
}

# Which production targets may arm the restart leg. A target that is production
# for alerting but has no Fly machine (the Pages auth surface) must NEVER
# restart; membership again keeps a new production URL non-restartable until it
# is explicitly listed here.
is_restartable_url() { # <url>
  local u p
  u="${1%/}"
  for p in $RESTARTABLE_PROBE_URLS; do
    if [ "$u" = "${p%/}" ]; then return 0; fi
  done
  return 1
}

# Human-readable form of a failure class, for the public issue body.
failure_label() { # <class>
  case "$1" in
    dns)       printf 'a DNS resolution failure' ;;
    tls)       printf 'a TLS/certificate failure' ;;
    timeout)   printf 'a connection timeout' ;;
    refused)   printf 'a connection refusal' ;;
    transport) printf 'a transport-level failure' ;;
    app)       printf 'an application error (the app itself answered 5xx)' ;;
    *)         printf 'a probe failure' ;;
  esac
}

# ── probe ───────────────────────────────────────────────────────────────────
# Which DEGRADED flavour the last probe observed (internal state, read by
# render_body() and the heal note so the incident body diagnoses the REAL
# failure mode):
#   status — the app answered with an unexpected HTTP status
#   header — the app answered with an ALLOWED status but a required response
#            header was missing (the route answered while the flow did not
#            initialise). Without this distinction the body's verdict row said
#            "an unexpected HTTP status" while the evidence two lines down
#            showed http_code=302, and the heal note pointed at the
#            route/deploy surface instead of the PKCE flow (review P2).
PROBE_DEGRADED_REASON="status"

# Sets: PROBE_VERDICT, PROBE_CODE, PROBE_FAILURE_CLASS, PROBE_EVIDENCE
# (multi-line). PROBE_FAILURE_CLASS is the curl-level failure layer from
# classify_failure() and is what decides whether a restart is even on the table.
# Retries DOWN verdicts (transient blips) — an UNEXPECTED verdict is a
# deterministic answer, so it stops immediately.
probe() {
  local attempt code timing body_file err_file hdr_file err_raw err_body v="" rc=0
  PROBE_EVIDENCE=""
  PROBE_CODE="000"
  PROBE_FAILURE_CLASS="none"
  PROBE_DEGRADED_REASON="status"

  attempt=1
  while [ "$attempt" -le "$PROBE_ATTEMPTS" ]; do
    body_file="$RUN_TMP/body.$attempt"
    err_file="$RUN_TMP/err.$attempt"
    hdr_file="$RUN_TMP/headers.$attempt"
    : > "$body_file"
    : > "$err_file"
    : > "$hdr_file"
    # Capture curl's EXIT CODE, not just its -w output: an HTTP 000 is emitted
    # for a DNS failure, a TLS failure, a refusal and a timeout alike, and the
    # exit code is the only thing that tells them apart (see classify_failure).
    # `-D` dumps the RESPONSE HEADERS to a file so a target can require one
    # (PROBE_REQUIRE_HEADER). curl still does NOT follow redirects (-L is
    # absent), so a 302's own headers — including `Location` — are what land
    # here. The dump is never published (Set-Cookie).
    rc=0
    timing="$(curl -sS -D "$hdr_file" -o "$body_file" -w '%{http_code} %{time_total}' \
      --connect-timeout "$PROBE_CONNECT_TIMEOUT_S" \
      --max-time "$PROBE_TIMEOUT_S" "$PROBE_URL" 2>"$err_file")" || rc=$?
    code="${timing%% *}"
    [ -n "$code" ] || code="000"
    timing="${timing#* }"
    [ "$timing" != "$code" ] || timing="?"
    PROBE_CODE="$code"
    err_raw="$(cat "$err_file" 2>/dev/null || true)"
    PROBE_FAILURE_CLASS="$(classify_failure "$rc" "$err_raw")"
    err_body="$(scrub_output "$err_raw" 300)"
    PROBE_EVIDENCE="${PROBE_EVIDENCE}attempt ${attempt}/${PROBE_ATTEMPTS}: http_code=${code} curl_exit=${rc} failure=${PROBE_FAILURE_CLASS} elapsed=${timing}s"
    [ -n "$err_body" ] && PROBE_EVIDENCE="${PROBE_EVIDENCE} curl=\"${err_body}\""
    PROBE_EVIDENCE="${PROBE_EVIDENCE}"$'\n'

    v="$(classify_code "$code")"
    # An EXPLICIT header requirement turns an otherwise-UP answer into an
    # ANSWERED-BUT-WRONG verdict: the status looked healthy but the flow did
    # not initialise. Deterministic (a missing header will not appear on a
    # retry), so it returns immediately like any other UNEXPECTED rather than
    # burning the retry budget.
    if [ "$v" = "UP" ] && [ -n "$PROBE_REQUIRE_HEADER" ]; then
      if ! header_satisfied "$hdr_file" "$PROBE_REQUIRE_HEADER"; then
        PROBE_DEGRADED_REASON="header"
        PROBE_EVIDENCE="${PROBE_EVIDENCE}required response header NOT found: \"$(scrub_output "$PROBE_REQUIRE_HEADER" 120)\" (HTTP ${code}) — the route answered, so this is not the outage class, but the flow did not initialise"$'\n'
        v="UNEXPECTED"
      fi
    fi
    if [ "$v" = "UP" ]; then
      PROBE_VERDICT="UP"
      return 0
    fi
    if [ "$v" = "UNEXPECTED" ]; then
      # The app answered — capture what it said (a public error body only; the
      # probe carries no credentials, so there is nothing secret to leak).
      if [ -s "$body_file" ]; then
        PROBE_EVIDENCE="${PROBE_EVIDENCE}response body (first 200 chars): $(scrub_output "$(cat "$body_file" 2>/dev/null || true)" 200)"$'\n'
      fi
      PROBE_VERDICT="UNEXPECTED"
      return 0
    fi
    if [ "$attempt" -lt "$PROBE_ATTEMPTS" ]; then
      log "attempt ${attempt}: HTTP ${code} — retrying in ${PROBE_RETRY_SLEEP_S}s"
      sleep "$PROBE_RETRY_SLEEP_S"
    fi
    attempt=$((attempt + 1))
  done

  PROBE_VERDICT="DOWN"
  return 0
}

# Runner-side egress control. 0 = this runner can reach the internet. 1 = it
# cannot, so a failing app probe is NOT evidence that the app is down: DNS, an
# egress proxy or a Cloudflare-side block on the runner's IP all look exactly
# like "no answer". Restarting production is the disruptive action in this
# script, so it is gated on a verdict we can trust.
control_ok() {
  local attempt code
  if [ -z "$CONTROL_URL" ]; then
    log "control probe disabled (CONTROL_URL is empty) — trusting the app probe for the restart decision"
    return 0
  fi
  attempt=1
  while [ "$attempt" -le "$CONTROL_ATTEMPTS" ]; do
    code="$(curl -sS -o /dev/null -w '%{http_code}' \
      --connect-timeout "$PROBE_CONNECT_TIMEOUT_S" \
      --max-time "$CONTROL_TIMEOUT_S" "$CONTROL_URL" 2>/dev/null || true)"
    case "$code" in
      2??|3??) return 0 ;;
    esac
    attempt=$((attempt + 1))
  done
  return 1
}

# ── GitHub issue helpers ────────────────────────────────────────────────────
urlencode() { printf '%s' "$1" | jq -sRr @uri; }

# Echoes a POSITIVE integer issue number, "" when none is open, or "__ERR__"
# when the search itself failed or answered something unparseable. ERR must
# NEVER be treated as "none" — that is the duplicate-spam direction. The caller
# refuses to file on anything non-numeric.
#
# SECURITY (PUBLIC repo): the repo is public, so "an open issue whose title
# matches" is NOT ours to adopt. Any account can open an issue with our title
# and a FORGED state block, and adopting it would hand them the sustained clock
# and the restart ledger — `down_runs=999` bypasses SUSTAINED_MIN_RUNS and a
# forged `restarts=` bypasses the cooldown AND the hourly cap (a restart storm
# that makes an outage worse). So the search carries `author:app/github-actions`
# AND each returned item is re-checked below against the RESERVED Actions login
# (`github-actions[bot]` — NOT the broader `type == "Bot"`, which also admits
# `renovate[bot]`/`dependabot[bot]`, round 3 P2-3), an EXACT title match and the
# body-only `INCIDENT_STATE_MARKER`. A non-machine or non-ours match is treated
# as "no incident" (a fresh machine issue is filed) and is NEVER adopted,
# patched, commented on or closed.
search_open_alert() { # <marker> <exact-title>
  local q enc out n total want_title="$2"
  # TITLE-ONLY dedupe key (no `label:` filter): a renamed/deleted label would
  # silently empty the search and turn the monitor back into a duplicate-issue
  # spammer (#2706 class). The label is applied when FILING, not when searching.
  # The author qualifier is the load-bearing security constraint (see above).
  q="repo:${REPO} is:issue is:open in:title author:app/github-actions \"$1\""
  enc="$(urlencode "$q")"
  # NB: the query MUST go in the URL path — `gh api -f q=…` switches the method
  # to POST and 404s on this endpoint (tenant-provision-monitor, #1133).
  # BOUNDED pagination (P3-12): `--paginate` walks every page and GitHub's own
  # 1000-result cap bounds it at ≤10 pages of 100, so the watchdog's own
  # duplicate accumulation (#2706) can never push the real incident off page 1
  # into a duplicate filing. `jq -s` below slurps the concatenated page docs.
  if ! out="$(gh api "search/issues?q=${enc}&per_page=100" --paginate 2>"$RUN_TMP/search.err")"; then
    warn "GitHub issue search failed: $(scrub_output "$(cat "$RUN_TMP/search.err" 2>/dev/null || true)" 200)"
    printf '__ERR__'
    return 0
  fi
  # "no open incident" (an empty item list) is NOT a failure — only an
  # unparseable answer is. Conflating the two makes the watchdog refuse to file
  # on every first outage (the search result for a fresh incident is empty).
  if ! n="$(printf '%s' "$out" | jq -rs --arg login "github-actions[bot]" --arg title "$want_title" --arg marker "$INCIDENT_STATE_MARKER" \
      '[.[].items[]? | select((.user.login // "") == $login) | select((.title // "") == $title) | select(((.body // "") | contains($marker)))][0].number // empty' 2>/dev/null)"; then
    warn "GitHub issue search returned an unparseable body"
    printf '__ERR__'
    return 0
  fi
  if [ -z "$n" ]; then
    # Distinguish "nothing matched" from "something matched but was NOT ours":
    # the latter is the hijack attempt this guard exists for, and it is worth a
    # loud line (we still file a fresh machine issue).
    total="$(printf '%s' "$out" | jq -rs '[.[].items[]?] | length' 2>/dev/null || true)"
    case "$total" in
      ''|*[!0-9]*) total=0 ;;
    esac
    if [ "$total" -gt 0 ]; then
      warn "issue search matched ${total} open issue(s) but NONE was authored by the GitHub Actions bot with our exact title and state marker — ignoring them (a forged look-alike is never adopted) and filing a fresh machine issue"
    fi
    printf ''
    return 0
  fi
  case "$n" in
    *[!0-9]*|0) printf '__ERR__'; return 0 ;;
  esac
  printf '%s' "$n"
}

get_issue_body() { # <n> -> the body, or __ERR__ when the read failed
  local out body
  # The body is the ONLY cooldown/cap memory. Swallowing a read failure would
  # look exactly like "no state" and the next write would ERASE the ledger
  # (down_runs reset to 1, restarts cleared) — the read-side twin of the
  # write-side bug that write-then-act fixes. Return a sentinel and let the
  # caller fail closed, like search_open_alert does.
  #
  # SEAM: the caller reads the issue's SERVER-SIDE created_at from
  # $RUN_TMP/issue.created_at (see the sustained-window clamp in main). It is
  # deliberately NOT returned on stdout: stdout carries the body / __ERR__ only,
  # and this helper is called inside `$( )` where a global assignment would not
  # survive anyway. The file is truncated FIRST so a failed re-read can never
  # leave a stale anchor behind.
  : > "$RUN_TMP/issue.created_at"
  if ! out="$(gh api "repos/${REPO}/issues/$1" 2>"$RUN_TMP/body.err")"; then
    warn "could not read the body of #$1: $(scrub_output "$(cat "$RUN_TMP/body.err" 2>/dev/null || true)" 200)"
    printf '__ERR__'
    return 0
  fi
  if ! body="$(printf '%s' "$out" | jq -r '.body // ""' 2>/dev/null)"; then
    warn "issue body for #$1 was not the expected JSON object"
    printf '__ERR__'
    return 0
  fi
  printf '%s' "$out" | jq -r '.created_at // ""' > "$RUN_TMP/issue.created_at" 2>/dev/null || : > "$RUN_TMP/issue.created_at"
  printf '%s' "$body"
}

create_issue() { # <title> <body> -> number ("" on failure)
  local payload out n
  # redact_text AT THE BOUNDARY: this is the last point before publication, so
  # no future caller can leak a probe URL / Fly token / Telegram token by
  # forgetting to scrub (idempotent — redacting twice is harmless).
  payload="$(jq -n --arg t "$(redact_text "$1")" --arg b "$(redact_text "$2")" --arg l "$ALERT_LABEL" \
    '{title:$t, body:$b, labels:[$l]}')"
  if ! out="$(printf '%s' "$payload" | gh api "repos/${REPO}/issues" --method POST --input - 2>"$RUN_TMP/create.err")"; then
    fail "issue create failed: $(scrub_output "$(cat "$RUN_TMP/create.err" 2>/dev/null || true)" 300)"
    printf ''
    return 0
  fi
  n="$(printf '%s' "$out" | jq -r '.number // empty' 2>/dev/null || true)"
  case "$n" in
    ''|*[!0-9]*) printf ''; return 0 ;;
  esac
  printf '%s' "$n"
}

# The issue body is the ONLY durable state store, so its write is
# AUTHORITATIVE: a failed update returns 1 and the caller decides (the restart
# path aborts rather than restarting without a durable cooldown/cap record).
# INVARIANT (security): the target number must have come from search_open_alert
# (machine-author verified) or create_issue (authored by our own token, i.e.
# the Actions app) — a PATCH here writes machine state into the issue, so it
# must NEVER be pointed at a human/attacker-authored look-alike.
update_issue_body() { # <n> <body> -> 0 ok / 1 failed
  local payload
  payload="$(jq -n --arg b "$(redact_text "$2")" '{body:$b}')"
  if ! printf '%s' "$payload" | gh api "repos/${REPO}/issues/$1" --method PATCH --input - >/dev/null 2>"$RUN_TMP/patch.err"; then
    warn "issue body update failed for #$1: $(scrub_output "$(cat "$RUN_TMP/patch.err" 2>/dev/null || true)" 200)"
    return 1
  fi
  return 0
}

comment_issue() { # <n> <body> -> 0 ok / 1 failed
  local payload
  payload="$(jq -n --arg b "$(redact_text "$2")" '{body:$b}')"
  if ! printf '%s' "$payload" | gh api "repos/${REPO}/issues/$1/comments" --method POST --input - >/dev/null 2>&1; then
    warn "comment failed on #$1"
    return 1
  fi
  return 0
}

close_issue() { # <n> -> 0 ok / 1 failed
  if ! printf '%s' '{"state":"closed"}' | gh api "repos/${REPO}/issues/$1" --method PATCH --input - >/dev/null 2>&1; then
    warn "close failed on #$1"
    return 1
  fi
  return 0
}

# ── optional Telegram page ──────────────────────────────────────────────────
# ONE sender, used by every human-page site, with ONE delivery contract:
# transport success AND the Telegram API's own `ok` field. That is the CONTRACT
# `tortoise/telegram_push.py::send_message` already enforces (`raise_for_status()`
# + `if not body.get("ok"): raise TelegramPushError`). The DRIVER split is
# deliberate and recorded (agent-infra/tortoise #4574): this workflow installs no
# Python toolchain and `tortoise/notify.py` imports httpx at module level, so
# importing the shared sender would add a dependency + supply-chain surface to
# the one job whose whole value is that it sits OUTSIDE the app's failure
# domain. Status-only was not enough: a misconfigured chat id answers
# `200 {"ok":false,"description":"chat not found"}`, which would have been
# recorded as a delivered page — the fail-open this contract closes.
# Returns 0 only when a human actually received the message.
telegram_send() { # <chat_id> <text>
  local chat="$1" text body_file err resp
  if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$chat" ]; then
    warn "telegram send skipped — TELEGRAM_BOT_TOKEN and the escalation chat are both required (a page must be addressed and signed)"
    return 1
  fi
  # Same publication boundary as the issue helpers: a page carries the probe
  # URL, flyctl's echoed output and (inside curl's URL) the bot token.
  text="$(redact_text "$2")"
  # Round 4, P3-7: `--fail-with-body` makes an HTTP 4xx a failure, but
  # `-o /dev/null` THREW AWAY Telegram's own error JSON — the actionable
  # `description` ("chat not found", "Unauthorized") never reached the log,
  # only curl's opaque `(22) ... error: 400`. Capture the body to a file and
  # surface it through scrub_output (which already covers the token) so a dead
  # paging channel is diagnosable from the public run log.
  body_file="$RUN_TMP/telegram-body.json"
  : > "$body_file"
  # The bot token is IN THE URL, and curl echoes the URL in its error text —
  # which would land in a PUBLIC Actions log. Capture stderr and redact the
  # token before logging.
  if ! err="$(curl -sS --fail-with-body --max-time 15 -o "$body_file" \
      "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${chat}" \
      --data-urlencode "text=$text" 2>&1)"; then
    resp="$(scrub_output "$(cat "$body_file" 2>/dev/null || true)" 200)"
    if [ -n "$resp" ]; then
      warn "telegram page failed: $(scrub_output "$err" 200) — api response: ${resp}"
    else
      warn "telegram page failed: $(scrub_output "$err" 200)"
    fi
    return 1
  fi
  # A 2xx is NOT delivery. Telegram answers `{"ok":false,"description":"..."}`
  # for a bad chat id / a bot removed from the chat, and `--fail-with-body`
  # passes that through with exit 0. The API's own verdict is the authority
  # (same rule as tortoise/telegram_push.py).
  if ! jq -e '.ok == true' "$body_file" >/dev/null 2>&1; then
    resp="$(scrub_output "$(cat "$body_file" 2>/dev/null || true)" 200)"
    warn "telegram page REJECTED by the API (HTTP 2xx but ok != true) — api response: ${resp:-<empty>}"
    return 1
  fi
  return 0
}

# Best-effort wrapper — the EXISTING contract of the 6 transition/restart page
# sites, deliberately unchanged: never fails the run, skips loudly when the
# channel is unconfigured (the incident issue is still the standing alert).
page() { # <text>
  if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    log "telegram page skipped (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set)"
    return 0
  fi
  telegram_send "$TELEGRAM_CHAT_ID" "$1" || true
  return 0
}

# A page that DEMANDS human action (the restart / failed-restart / velocity-cap
# / inconclusive-egress sites). Same best-effort contract as page() — it never
# fails the run and never changes a caller's control flow — but it records a
# CONFIRMED delivery in HUMAN_PAGED_THIS_RUN, so the same-run double-page guard
# and the persisted `page_ok_ts` mean what they say. A page that FAILED leaves
# the flag 0 and therefore suppresses nothing (#3887 review: an attempt stamp
# must never be read as "a human was paged").
page_human() { # <text>
  # Routes to TELEGRAM_CHAT_ID — the PRE-#3887 recipient. ESCALATION_CHAT_ID is
  # the SUSTAINED leg's recipient only (page_required below); using it here
  # would silently move the existing restart/cap/inconclusive pages off the ops
  # chat just because an operator configured an on-call override (#4591 review).
  if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    page "$1"
    return 0
  fi
  if telegram_send "$TELEGRAM_CHAT_ID" "$1"; then HUMAN_PAGED_THIS_RUN="1"; fi
  return 0
}

# The CONFIRMING variant (#3887). Returns 1 when the channel is unconfigured or
# delivery was not confirmed — so a caller that MUST reach a human can tell
# "paged" from "tried". The sustained-escalation leg is the only caller that
# treats a non-zero return as a failure; a failed send is never recorded as
# delivered.
page_required() { # <text>
  telegram_send "$ESCALATION_CHAT_ID" "$1"
}

# ── incident state (carried in the issue body) ──────────────────────────────
STATE_FIRST_FAILURE_TS=""
# The ESCALATION leg's wall-clock anchor, set by main() from the incident's
# SERVER-SIDE `created_at` (the same unforgeable timestamp the restart window is
# clamped to). It is deliberately SEPARATE from STATE_FIRST_FAILURE_TS: the two
# legs need different anchors. The RESTART leg must not act before
# SUSTAINED_DOWN_MINUTES of CONTINUOUS failure, so it uses the resettable
# first_failure_ts. The ESCALATION leg exists to remove #3887's incident that
# never reaches a human, so it must NOT be zeroed by the stale-clock reset and
# must NOT be muted by a forged future first_failure_ts — both of which
# first_failure_ts can be, and created_at cannot. Empty means "main() did not
# resolve one": decide_escalation then falls back to STATE_FIRST_FAILURE_TS, so
# the pure unit seam keeps working unchanged.
STATE_ESCALATE_ANCHOR_TS=""
STATE_DOWN_RUNS="0"
STATE_LAST_DOWN_TS="0"
STATE_LAST_COMMENT_TS="0"
STATE_CAP_NOTIFIED_TS="0"
# Sustained-incident escalation (#3887). `escalate_ts` is the epoch of the last
# escalation the workflow ATTEMPTED; it is TRUSTED for throttling ONLY while
# `escalate_state` says the outcome was persisted (`sent`). `escalate_state`
# records that outcome (`pending`/`sent`/`failed`). `page_ok_ts` is the epoch of
# the last CONFIRMED-DELIVERED human page of ANY kind — the delivery-confirmed
# stamp the cross-mechanism bound reads, so an UNDELIVERED page can never
# suppress a later one.
STATE_ESCALATE_TS="0"
STATE_ESCALATE_STATE=""
STATE_PAGE_OK_TS="0"
# Set at the human-page sites when the send was CONFIRMED delivered. Drives
# both the same-run double-page guard and the persisted `page_ok_ts`.
HUMAN_PAGED_THIS_RUN="0"
STATE_RESTARTS=""
# Set by parse_state when `restarts=` is PRESENT but not fully parseable. The
# restart gate refuses to act on it (fail closed) — dropping an entry could
# only WEAKEN the cooldown/cap.
STATE_RESTARTS_INVALID="0"
STATE_RESTARTS_RAW=""
# DURABLE FAIL-CLOSED LEDGER STATE (round 4, P2-7). A previous run may have
# failed closed because the restart ledger could not be TRUSTED — either the
# source ledger was present but corrupt (`invalid`) or the lookup could not be
# read at all (`unreadable`). That verdict used to live only in the run's log
# and heal note: the incident was left with `restarts=` EMPTY, so the NEXT run
# adopted the now-open incident, read the empty ledger as valid, and armed with
# an empty hourly budget — silently dropping the cap stamps the source carried
# (the restart-storm the cross-incident ledger exists to prevent). These two
# fields make the verdict DURABLE. `ledger_state` absent/unknown means clean;
# `ledger_src` names the issue to re-check (empty for the unreadable case,
# where the retry is a fresh lookup instead).
STATE_LEDGER_STATE=""
STATE_LEDGER_SRC=""
# The issue number the CURRENT ledger was read from (round 3, P2-4). On a NEW
# incident seeded from a previous one, this is the PREVIOUS issue — naming it in
# the corrupt-ledger failure is what sends the operator to the right place.
LEDGER_SOURCE_ISSUE=""

parse_state() { # <body>
  local body="$1"
  # Every field is passed through to_int: the body is human-editable and bash
  # arithmetic aborts the shell on values like `08` (invalid octal).
  STATE_FIRST_FAILURE_TS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*first_failure_ts=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "")"
  STATE_DOWN_RUNS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*down_runs=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "0")"
  STATE_LAST_DOWN_TS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*last_down_ts=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "0")"
  STATE_LAST_COMMENT_TS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*last_comment_ts=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "0")"
  STATE_CAP_NOTIFIED_TS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*cap_notified_ts=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "0")"
  # ── the escalation fields (#3887) ─────────────────────────────────────────
  STATE_ESCALATE_TS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*escalate_ts=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "0")"
  # Whitelist. An unrecognised value is treated as UNKNOWN (empty) — never as an
  # instruction. Note the field can only ever make the leg LOUDER: no value of
  # it suppresses a send (`escalate_ts` is the only throttle, and it is gated on
  # `sent` below).
  STATE_ESCALATE_STATE="$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*escalate_state=\([a-z]*\).*/\1/p' | head -1 || true)"
  case "$STATE_ESCALATE_STATE" in pending|sent|failed) : ;; *) STATE_ESCALATE_STATE="" ;; esac
  STATE_PAGE_OK_TS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*page_ok_ts=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "0")"
  STATE_RESTARTS=""
  STATE_RESTARTS_INVALID="0"
  STATE_RESTARTS_RAW=""
  STATE_LEDGER_STATE=""
  STATE_LEDGER_SRC=""
  # ── the durable fail-closed ledger sentinel (round 4, P2-7) ────────────────
  # Mirrors the state_block fields. Only the two values this script writes are
  # honoured; anything else (including an explicit `ok`) is treated as clean,
  # because the field is machine-written and an unrecognised value must not be
  # able to lock self-healing forever. That is NOT a new trust hole: whoever
  # can write this field can already clear `restarts=` — the same accepted
  # body-edit boundary the ledger integrity already rests on.
  STATE_LEDGER_STATE="$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*ledger_state=\([a-z]*\).*/\1/p' | head -1 || true)"
  case "$STATE_LEDGER_STATE" in invalid|unreadable) : ;; *) STATE_LEDGER_STATE="" ;; esac
  STATE_LEDGER_SRC="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*ledger_src=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "")"
  # ── the restart ledger: FAIL CLOSED ────────────────────────────────────────
  # Every other state read in this script fails closed (get_issue_body /
  # search_open_alert return __ERR__ and the caller exits 1). This one used to
  # do the OPPOSITE: a permissive capture plus a `continue` on any entry
  # `to_int` rejected meant an unparseable entry was silently DROPPED. Dropping
  # a cooldown stamp can only weaken the rate limit (the entry vanishes and
  # decide_restart may return `go` sooner) — that is fail-open, and this is the
  # field an attacker/human edit would target. So: parse the WHOLE value
  # strictly; anything not fully parseable sets STATE_RESTARTS_INVALID and the
  # restart gate refuses to act.
  STATE_RESTARTS_RAW="$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*restarts=\([^>]*\).*/\1/p' | head -1 || true)"
  STATE_RESTARTS_RAW="$(printf '%s' "$STATE_RESTARTS_RAW" \
    | tr ',' ' ' | tr -s '[:space:]' ' ' | sed -E 's/^[[:space:]-]+//; s/[[:space:]-]+$//')"
  # The ledger needs the SAME normalizer as the scalar fields, for the same
  # human-editable-body reasons: `restarts=…,0008` is bash OCTAL (the
  # substitution inside decide_restart aborts the shell, and with `set -e` the
  # run dies BEFORE any state write), a >12-digit entry makes `[ ts -gt last ]`
  # error so `last` stays 0 and the COOLDOWN IS SKIPPED, and a future
  # millisecond epoch yields a negative elapsed time → cooldown forever (an
  # inert self-heal with no escalation). Clamp a future stamp to now; REJECT
  # (do not drop) anything the normalizer refuses.
  local raw_ts clean_ts cnow ledger=""
  cnow="$(now_epoch)"
  if [ -n "$STATE_RESTARTS_RAW" ]; then
    case "$STATE_RESTARTS_RAW" in
      *[!0-9[:space:]]*) STATE_RESTARTS_INVALID="1" ;;
    esac
    if [ "$STATE_RESTARTS_INVALID" = "0" ]; then
      for raw_ts in $STATE_RESTARTS_RAW; do
        clean_ts="$(to_int "$raw_ts" "")"
        if [ -z "$clean_ts" ]; then
          # >12 digits (to_int's own bound): unparseable, not droppable.
          STATE_RESTARTS_INVALID="1"
          break
        fi
        if [ "$clean_ts" -gt "$cnow" ]; then clean_ts="$cnow"; fi
        ledger="${ledger:+$ledger }$clean_ts"
      done
      if [ "$STATE_RESTARTS_INVALID" = "0" ]; then STATE_RESTARTS="$ledger"; fi
    fi
  fi
  # Bound the ledger: keep only the last 24 attempts (a year of incidents at
  # the cap would otherwise grow the state block without limit).
  local kept="" ts n=0
  local rev=""
  for ts in $STATE_RESTARTS; do rev="$ts $rev"; done
  for ts in $rev; do
    # explicit `if` (not `[ ] && x`) — no reliance on the set -e AND-OR-list
    # exemption, which differs subtly across shell versions.
    if [ "$n" -lt 24 ]; then kept="$ts $kept"; fi
    n=$((n + 1))
  done
  STATE_RESTARTS="$(printf '%s' "$kept" | sed -E 's/ +$//')"
  # Absent first_failure_ts → this run is the FIRST failure (the conservative
  # direction: never let a missing clock authorise a restart).
  # NB: explicit `if` — a trailing `[ … ] && x` would make this function return
  # 1 whenever the state IS present, and `set -e` would kill the caller.
  if [ -z "$STATE_FIRST_FAILURE_TS" ]; then STATE_FIRST_FAILURE_TS="$cnow"; fi
  # Absent/0 last_down_ts is left at 0 — an UNKNOWN last failing run, NOT "just
  # now". Defaulting it to now made the stale-clock guard unreachable for a
  # body that simply omitted the field (an ancient first_failure_ts with no
  # last_down_ts then satisfied the sustained window on the FIRST observed
  # failing run). 0 trips the stale branch in main, which restarts the window —
  # the fail-closed direction. A machine body always carries a real stamp, so
  # this only fires on a hand-edited/legacy body.
  # A clock in the FUTURE (clock skew, or a hand-edited body) would print
  # negative durations AND lock the sustained gate forever (now - ff < 0), i.e.
  # an inert self-heal with no escalation — the worse failure mode. Clamp it.
  if [ "$STATE_FIRST_FAILURE_TS" -gt "$cnow" ]; then STATE_FIRST_FAILURE_TS="$cnow"; fi
  if [ "$STATE_LAST_DOWN_TS" -gt "$cnow" ]; then STATE_LAST_DOWN_TS="$cnow"; fi
  # The THROTTLE stamps need a clamp too, but the FAIL-LOUD direction: a future
  # `cap_notified_ts` would silence BOTH the cap escalation and the
  # INCONCLUSIVE page (they share the stamp), and a future `last_comment_ts`
  # would silence routine comments — clock skew or a hand-edited body could
  # mute the human channel indefinitely. A stamp that cannot be true is
  # therefore treated as "never notified" (0): at most one extra comment/page,
  # never a silent one.
  if [ "$STATE_LAST_COMMENT_TS" -gt "$cnow" ]; then STATE_LAST_COMMENT_TS="0"; fi
  if [ "$STATE_CAP_NOTIFIED_TS" -gt "$cnow" ]; then STATE_CAP_NOTIFIED_TS="0"; fi
  if [ "$STATE_PAGE_OK_TS" -gt "$cnow" ]; then STATE_PAGE_OK_TS="0"; fi
  # The escalation stamp is gated on its OUTCOME, which closes the window where
  # a run dies between writing the attempt marker and recording what happened.
  # `pending`/`failed` (or an absent state) means "we do not know whether a
  # human was reached" => treat the stamp as never-set and RETRY, which is the
  # fail-loud direction. A future stamp is clamped to 0 like every other
  # throttle stamp: an untrustworthy value must never mute the human channel.
  case "$STATE_ESCALATE_STATE" in
    sent) : ;;
    *) STATE_ESCALATE_TS="0" ;;
  esac
  if [ "$STATE_ESCALATE_TS" -gt "$cnow" ]; then STATE_ESCALATE_TS="0"; fi
  return 0
}

# CROSS-INCIDENT RESTART BUDGET (review round 2, P2).
# The restart ledger used to die with its incident. A machine that flaps — down
# 10 min, restart, recovers, the incident closes, down again — therefore began
# every NEW incident with an EMPTY ledger, so MAX_RESTARTS_PER_HOUR never
# tripped: one restart per ~10 min forever, which is precisely the unbounded
# self-inflicted restart loop the cap exists to bound.
# So a NEW incident seeds its ledger with the still-in-window restart stamps of
# the most recent MACHINE-authored incident for this marker — open OR closed,
# i.e. the ledger the previous incident left behind. `sort=created&order=desc`
# makes `.items[0]` the most recent. The SAME round-3 adoption guard as the
# dedupe search applies (exact title + body marker + reserved login), so an
# unrelated bot-authored issue whose title merely contains the loose terms is
# never read as this incident's ledger.
# Reads STATE_RESTARTS / STATE_RESTARTS_INVALID. Returns 1 when the previous
# ledger could not be READ, and the caller then fails closed (no restart
# without a provable hourly budget).
# The optional <exclude-issue> (round 4, P2-7) drops ONE issue from the result:
# the fail-closed RETRY path runs while this incident is already open, so
# without it the lookup would return the current (empty-ledger) incident as
# "the most recent" and never see the real previous one.
recent_restart_ledger() { # <marker> <now> <exact-title> [exclude-issue]
  local q enc out n body now="$2" want_title="$3" exclude="${4:-}" ledger ts
  q="repo:${REPO} is:issue author:app/github-actions in:title \"$1\""
  enc="$(urlencode "$q")"
  # Bounded pagination (P3-12), same bound as search_open_alert: `--paginate`
  # cannot page the real ledger off page 1 into a silent budget reset.
  if ! out="$(gh api "search/issues?q=${enc}&per_page=100&sort=created&order=desc" --paginate 2>"$RUN_TMP/ledger.err")"; then
    warn "restart-ledger search failed: $(scrub_output "$(cat "$RUN_TMP/ledger.err" 2>/dev/null || true)" 200)"
    return 1
  fi
  if ! n="$(printf '%s' "$out" | jq -rs --arg login "github-actions[bot]" --arg title "$want_title" --arg marker "$INCIDENT_STATE_MARKER" --arg exclude "$exclude" \
      '[.[].items[]? | select((.user.login // "") == $login) | select((.title // "") == $title) | select(((.body // "") | contains($marker))) | select($exclude == "" or ((.number // 0) | tostring) != $exclude)][0].number // empty' 2>/dev/null)"; then
    warn "restart-ledger search returned an unparseable body"
    return 1
  fi
  case "$n" in
    '') STATE_RESTARTS=""; STATE_RESTARTS_INVALID="0"; LEDGER_SOURCE_ISSUE=""; return 0 ;;
    *[!0-9]*|0)
      warn "restart-ledger search returned a non-numeric issue id ('${n}')"
      return 1 ;;
  esac
  # Record WHERE this ledger came from. On a NEW incident whose seeded ledger is
  # corrupt, main must name THIS issue (not the just-created one) in the
  # fail-closed message (round 3, P2-4).
  LEDGER_SOURCE_ISSUE="$n"
  body="$(get_issue_body "$n")"
  if [ "$body" = "__ERR__" ]; then return 1; fi
  # This helper's contract is to return a LEDGER, not to adopt the SOURCE
  # incident's clock/counters/sentinel. Snapshot every other state field around
  # the parse and restore it (round 4, P2-7): the round-4 retry runs while the
  # CURRENT incident's state is live, so clobbering it here would fabricate a
  # sustained window (and a sentinel) from the previous incident's body.
  local sff sdr sld slc scn sls slsrc ses sest spok
  sff="$STATE_FIRST_FAILURE_TS"; sdr="$STATE_DOWN_RUNS"; sld="$STATE_LAST_DOWN_TS"
  slc="$STATE_LAST_COMMENT_TS"; scn="$STATE_CAP_NOTIFIED_TS"
  sls="$STATE_LEDGER_STATE"; slsrc="$STATE_LEDGER_SRC"
  # #3887: the escalation triple is CURRENT-incident state too, and omitting it
  # here let a SOURCE incident's confirmed-page stamps bleed into the caller —
  # `decide_escalation` then returned `remind`/`wait_page_quiet` off the PREVIOUS
  # incident's page, silently muting the human page for a new incident that had
  # never paged (fail-OPEN, reproduced in review). Any new state field MUST be
  # added to BOTH snapshot lists below as well as to parse_state.
  ses="$STATE_ESCALATE_STATE"; sest="$STATE_ESCALATE_TS"; spok="$STATE_PAGE_OK_TS"
  parse_state "$body"
  STATE_FIRST_FAILURE_TS="$sff"; STATE_DOWN_RUNS="$sdr"; STATE_LAST_DOWN_TS="$sld"
  STATE_LAST_COMMENT_TS="$slc"; STATE_CAP_NOTIFIED_TS="$scn"
  STATE_LEDGER_STATE="$sls"; STATE_LEDGER_SRC="$slsrc"
  STATE_ESCALATE_STATE="$ses"; STATE_ESCALATE_TS="$sest"; STATE_PAGE_OK_TS="$spok"
  # Keep only the stamps still inside the rolling hour. decide_restart re-checks
  # the window; this just keeps the carried body small and the semantics plain.
  ledger=""
  for ts in $STATE_RESTARTS; do
    if [ $((now - ts)) -lt 3600 ]; then ledger="${ledger:+$ledger }$ts"; fi
  done
  STATE_RESTARTS="$ledger"
  return 0
}

# RE-VERIFY A DISARMED LEDGER (round 4, P2-7). Called on a repeat run whose
# durable sentinel says the previous run could not trust the ledger: re-read the
# named SOURCE issue and adopt its ledger only if it parses cleanly now.
# Returns 0 = repaired (STATE_RESTARTS carries the window-filtered ledger),
# 1 = still corrupt / unreadable (the caller must keep failing closed).
reseed_ledger_from_source() { # <source-issue>
  local src="$1" body now kept ts
  local sff sdr sld slc scn sls slsrc ses sest spok
  body="$(get_issue_body "$src")"
  if [ "$body" = "__ERR__" ]; then return 1; fi
  sff="$STATE_FIRST_FAILURE_TS"; sdr="$STATE_DOWN_RUNS"; sld="$STATE_LAST_DOWN_TS"
  slc="$STATE_LAST_COMMENT_TS"; scn="$STATE_CAP_NOTIFIED_TS"
  sls="$STATE_LEDGER_STATE"; slsrc="$STATE_LEDGER_SRC"
  # #3887: same snapshot rule as recent_restart_ledger — this runs mid-run on a
  # repeat DOWN run, so a bleed here would overwrite the CURRENT incident's
  # escalation state immediately before the final body write.
  ses="$STATE_ESCALATE_STATE"; sest="$STATE_ESCALATE_TS"; spok="$STATE_PAGE_OK_TS"
  parse_state "$body"
  STATE_FIRST_FAILURE_TS="$sff"; STATE_DOWN_RUNS="$sdr"; STATE_LAST_DOWN_TS="$sld"
  STATE_LAST_COMMENT_TS="$slc"; STATE_CAP_NOTIFIED_TS="$scn"
  STATE_LEDGER_STATE="$sls"; STATE_LEDGER_SRC="$slsrc"
  STATE_ESCALATE_STATE="$ses"; STATE_ESCALATE_TS="$sest"; STATE_PAGE_OK_TS="$spok"
  if [ "$STATE_RESTARTS_INVALID" = "1" ]; then
    # Never adopt HALF of a corrupt ledger.
    STATE_RESTARTS=""
    return 1
  fi
  now="$(now_epoch)"
  kept=""
  for ts in $STATE_RESTARTS; do
    if [ $((now - ts)) -lt 3600 ]; then kept="${kept:+$kept }$ts"; fi
  done
  STATE_RESTARTS="$kept"
  STATE_RESTARTS_INVALID="0"
  STATE_RESTARTS_RAW=""
  return 0
}

restart_history() { printf '%s' "$STATE_RESTARTS" | tr ' ' ','; }

state_block() { # <kind>
  # kind is lowercased in the machine-readable block (stable for parsers).
  # FIELD ORDER MATTERS: `restarts` is LAST because its parser captures the
  # remaining `[^>]*` tail, and `ledger_state`/`ledger_src` are parsed with a
  # whitespace-terminated capture so they MUST precede `restarts=`. The field
  # list itself is declared once in STATE_FIELDS (and parity-tested); see it for
  # the escalation fields (#3887): `escalate_state` precedes `escalate_ts` for
  # the same whitespace-terminated reason.
  printf '<!-- watchdog-state kind=%s first_failure_ts=%s down_runs=%s last_down_ts=%s last_comment_ts=%s cap_notified_ts=%s ledger_state=%s ledger_src=%s escalate_state=%s escalate_ts=%s page_ok_ts=%s restarts=%s -->' \
    "$(printf '%s' "$1" | tr 'A-Z' 'a-z')" "$STATE_FIRST_FAILURE_TS" "$STATE_DOWN_RUNS" \
    "$STATE_LAST_DOWN_TS" "$STATE_LAST_COMMENT_TS" "$STATE_CAP_NOTIFIED_TS" \
    "${STATE_LEDGER_STATE}" "${STATE_LEDGER_SRC}" \
    "${STATE_ESCALATE_STATE}" "${STATE_ESCALATE_TS}" "${STATE_PAGE_OK_TS}" \
    "$(restart_history)"
}

# What this target's UP contract IS, in words, for the incident body. Prose
# only — the logic is classify_code + header_satisfied.
probe_up_contract() {
  if [ -n "$PROBE_EXPECT_STATUS" ]; then
    printf '%s' "$PROBE_EXPECT_STATUS"
  else
    printf '2xx/401/403/429'
  fi
}

render_body() { # <kind> <kindlabel> <selfheal-note>
  local kind="$1" kindlabel="$2" heal="$3" summary what assertion
  if [ "$kind" = "DOWN" ]; then
    summary="🔴 **${PROBE_HOST_LABEL} is DOWN** — the probe got no answer from the app."
    what="no answer (timeout / connection error / 5xx) after ${PROBE_ATTEMPTS} attempts"
  else
    summary="🟠 **${PROBE_HOST_LABEL} answered unexpectedly** — the probe reached the app, but not with an expected response."
    # The verdict row is what an operator reads FIRST; it must name the ACTUAL
    # failure mode. A missing required header is not a status mismatch — the
    # status was the healthy one — and saying otherwise contradicts the raw
    # evidence two lines below the row.
    if [ "$PROBE_DEGRADED_REASON" = "header" ]; then
      what="the required response header was missing — the route ANSWERED (HTTP ${PROBE_CODE}) but the flow did not initialise"
    else
      what="an unexpected HTTP status (not $(probe_up_contract), not 5xx)"
    fi
  fi
  # The assertion paragraph is per-target: the API probe's contract and the
  # Pages auth probe's contract are different, and an incident body that
  # describes the WRONG contract sends the operator looking in the wrong place.
  # The "no Fly machine" clause is keyed on RESTARTABILITY (not on the presence
  # of PROBE_EXPECT_STATUS): setting an expectation knob on a restartable target
  # must not make the public body claim nothing will restart when it could.
  if [ -n "$PROBE_EXPECT_STATUS" ]; then
    assertion="The probe asserts a **specific contract**, not just that a socket is
open: \`GET $(redact_url "$PROBE_URL")\` must answer one of \`$(probe_up_contract)\`"
    if [ -n "$PROBE_REQUIRE_HEADER" ]; then
      assertion="${assertion} **and** its response headers must carry
\`$(scrub_output "$PROBE_REQUIRE_HEADER" 120)\`. A healthy-looking status with the required
header missing means the route answered while the flow did not actually
initialise."
    else
      assertion="${assertion}."
    fi
    if ! is_restartable_url "$PROBE_URL"; then
      assertion="${assertion} This surface has **no Fly machine** behind it (it is served by
Cloudflare Pages): an automated restart is not a possible remediation here and
is hard-disarmed. This is the #3616 class, where only \`/auth/start\` revealed
the outage and \`/welcome\` (302) and \`/api/session\` (401) stayed green
throughout."
    fi
  else
    assertion="The probe asserts the **real user path**, not just that a socket is open: an
authenticated API route served by the app. \`2xx\`/\`401\`/\`403\`/\`429\` all mean
\"the app answered\"; a timeout, a connection error or a 5xx mean it did not."
  fi
  cat <<EOF
$(state_block "$kind")
${INCIDENT_STATE_MARKER}

> 🤖 Machine-managed by \`.github/scripts/availability-watchdog.sh\` (#2850).
> The body is rewritten on every probe run — **add human notes as comments**.
> The **title is the dedupe key** (the watchdog searches for it before filing),
> so do not rename this issue.

## ${summary}

| | |
|---|---|
| **Verdict** | ${what} |
| **Kind** | ${kindlabel} |
| **Probe** | \`GET $(redact_url "$PROBE_URL")\` from GitHub Actions — OUTSIDE Fly (a different failure domain than the app) |
| **First observed** | $(fmt_iso "$STATE_FIRST_FAILURE_TS") |
| **Failing probe runs** | ${STATE_DOWN_RUNS} (cron intent 5 min; **measured** delivery ~15 min — see the escalation section below; last at $(fmt_iso "$STATE_LAST_DOWN_TS")) |
| **Restart attempts in the rolling hour (may include a prior incident)** | $(if [ -n "$(restart_history)" ]; then printf '%s' "$(restart_history)"; else printf 'none'; fi) |

${assertion}
Sentry cannot see this class of failure at all — it runs inside the process,
and a process that is alive-but-not-serving raises no exception.

### Latest probe evidence

\`\`\`
$(first_chars "$(redact_text "$PROBE_EVIDENCE")" "$EVIDENCE_MAX_CHARS")
\`\`\`

### Self-healing

$(redact_text "$heal")

### Escalation (#3887) — leaving GitHub

$(escalation_note)

**Operator runbook:** \`docs/infra-runbook.md\` → *Out-of-band availability
watchdog* — how to read a failure, how to restart manually, the cooldown, and
what to do when restarts do not help.
EOF
}

# ── restart decision + execution ────────────────────────────────────────────
# Echoes: go | wait_sustained | wait_runs | wait_cooldown | cap
# Reads STATE_FIRST_FAILURE_TS / STATE_DOWN_RUNS / STATE_RESTARTS; <now> via $1.
decide_restart() {
  local now="$1" ts last=0 n=0
  if [ $((now - STATE_FIRST_FAILURE_TS)) -lt $((SUSTAINED_DOWN_MINUTES * 60)) ]; then
    printf 'wait_sustained'; return 0
  fi
  # Wall-clock alone is not enough: at least SUSTAINED_MIN_RUNS failing runs
  # must have been OBSERVED (guards a stale/reopened incident whose stored
  # first_failure_ts is ancient).
  if [ "$STATE_DOWN_RUNS" -lt "$SUSTAINED_MIN_RUNS" ]; then
    printf 'wait_runs'; return 0
  fi
  for ts in $STATE_RESTARTS; do
    if [ "$ts" -gt "$last" ]; then last="$ts"; fi
  done
  if [ "$last" -gt 0 ] && [ $((now - last)) -lt $((RESTART_COOLDOWN_MINUTES * 60)) ]; then
    printf 'wait_cooldown'; return 0
  fi
  for ts in $STATE_RESTARTS; do
    if [ $((now - ts)) -lt 3600 ]; then n=$((n + 1)); fi
  done
  if [ "$n" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
    printf 'cap'; return 0
  fi
  printf 'go'
}

# ── sustained-incident escalation decision (#3887) ──────────────────────────
# Normalize the escalation knobs from the ALREADY-NORMALIZED sustained
# thresholds, so the `escalate >= restart` invariant holds by construction rather
# than by two literal pairs that happen to agree. Takes them as ARGUMENTS (not
# globals) so a unit call can drive any pair, and is called by main() AFTER its
# own normalization step.
normalize_escalation_knobs() { # <sustained_minutes> <sustained_runs>
  local s_min="$1" s_runs="$2" d_min d_runs raw_enabled
  # The value AS RECEIVED — no `to_int`, and no whitespace stripping — is the
  # ONLY kill-switch predicate. `to_int` strips non-digits, so a value like
  # `0abc`, `0x`, `0.0` or `00` would collapse to `0` and SILENTLY MUTE the
  # pager — a malformed operator value choosing the kill switch is exactly the
  # failure class this leg exists to remove, so it must not be reachable.
  # Likewise `false`/`off`/`no` strip to the empty string and would take the
  # default. And a whitespace normalize is the same trap one layer down: a YAML
  # block/folded scalar in a workflow `env:` (`|` or `>`) yields exactly
  # `0\n`, so `" 0"`/`"0\n"` would take the kill switch with NO warning — a
  # silent mute on a pager that must be fail-closed. Only a byte-exact `0`
  # disables escalation; an exact `1` (or unset/empty, the documented default)
  # enables it quietly; EVERYTHING else resolves to 1 and warns (fail CLOSED
  # toward paging).
  raw_enabled="${ESCALATE_ENABLED:-}"
  case "$raw_enabled" in
    0) ESCALATE_ENABLED=0 ;;
    1) ESCALATE_ENABLED=1 ;;
    "") ESCALATE_ENABLED=1 ;;  # unset/empty: the documented default (not a kill)
    *)
      ESCALATE_ENABLED=1
      warn "ESCALATE_ENABLED='[${raw_enabled}]' is not 0 or 1 — treating it as 1 (fail CLOSED toward paging; a kill switch must be the literal 0)"
      ;;
  esac
  d_min=$((s_min * 3))
  d_runs=$((s_runs + 1))
  ESCALATE_SUSTAINED_MINUTES="$(int_or "${ESCALATE_SUSTAINED_MINUTES:-}" "$d_min" 1)"
  ESCALATE_MIN_RUNS="$(int_or "${ESCALATE_MIN_RUNS:-}" "$d_runs" 1)"
  # Fail CLOSED toward the LATER page: a misconfiguration must never make the
  # human page fire EARLIER than the automated action it exists to escalate.
  if [ "$ESCALATE_SUSTAINED_MINUTES" -lt "$s_min" ]; then
    warn "ESCALATE_SUSTAINED_MINUTES=${ESCALATE_SUSTAINED_MINUTES} is below SUSTAINED_DOWN_MINUTES=${s_min} — clamping UP (a human page must not fire before the automated action)"
    ESCALATE_SUSTAINED_MINUTES="$s_min"
  fi
  if [ "$ESCALATE_MIN_RUNS" -lt "$s_runs" ]; then
    warn "ESCALATE_MIN_RUNS=${ESCALATE_MIN_RUNS} is below SUSTAINED_MIN_RUNS=${s_runs} — clamping UP"
    ESCALATE_MIN_RUNS="$s_runs"
  fi
}

# Echoes: off | wait_sustained | wait_runs | wait_page_quiet | wait_reminder | page | remind
# Pure: reads STATE_FIRST_FAILURE_TS / STATE_DOWN_RUNS / STATE_ESCALATE_TS /
# STATE_PAGE_OK_TS and the knobs; <now> via $1. No side effects, and unit-callable
# through the WATCHDOG_LIB_ONLY seam the pure parsers already use. The same-run
# guard (a human page CONFIRMED in this run) is applied by the CALLER, so this
# stays a pure function of persisted state.
decide_escalation() {
  local now="$1" w
  w=$((CAP_RENOTIFY_MINUTES * 60))
  if [ "$ESCALATE_ENABLED" != "1" ]; then printf 'off'; return 0; fi
  # BOTH legs, in the same order decide_restart uses: wall-clock first, then
  # observed failing runs. A single failing tick satisfies NEITHER, so one bad
  # probe can never page.
  #
  # ANCHOR (G2/G3): main() resolves the wall-clock start to the incident's
  # SERVER-SIDE `created_at` in STATE_ESCALATE_ANCHOR_TS, so the stale-clock
  # reset (which sets first_failure_ts=now) can no longer zero the pager's
  # window, and a body-forged future first_failure_ts can no longer mute it.
  # The fallback is STATE_FIRST_FAILURE_TS, which is what the unit seam (and any
  # caller that did not resolve an anchor) supplies.
  # A FUTURE anchor is UNTRUSTWORTHY (a clock that cannot be true is the
  # fail-OPEN mute this leg must never have), so it is treated as "the window
  # has already elapsed" — fail TOWARD paging, the same direction as the
  # missing-created_at fallback. It cannot cause an immediate page on its own:
  # the run leg BELOW still requires ESCALATE_MIN_RUNS OBSERVED failing runs,
  # which one forged stamp cannot supply. So a long-lived incident's first
  # observed run reaches the run leg, not the page.
  local anchor="${STATE_ESCALATE_ANCHOR_TS:-$STATE_FIRST_FAILURE_TS}"
  if [ "$anchor" -le "$now" ] && [ $((now - anchor)) -lt $((ESCALATE_SUSTAINED_MINUTES * 60)) ]; then
    printf 'wait_sustained'; return 0
  fi
  if [ "$STATE_DOWN_RUNS" -lt "$ESCALATE_MIN_RUNS" ]; then
    printf 'wait_runs'; return 0
  fi
  # The cross-mechanism bound. `page_ok_ts` records a CONFIRMED-DELIVERED human
  # page of ANY kind (written only after telegram_send returned 0), NEVER an
  # attempt — so an undelivered restart/cap/inconclusive page cannot silence
  # this leg for the window. This makes "last human page" enforced code rather
  # than a comment.
  if [ "$STATE_PAGE_OK_TS" -gt 0 ] && [ $((now - STATE_PAGE_OK_TS)) -lt "$w" ]; then
    printf 'wait_page_quiet'; return 0
  fi
  # The leg's own reminder window. `escalate_ts` is non-zero ONLY while
  # `escalate_state=sent` (parse_state nulls it for pending/failed), so an
  # attempt whose outcome was never recorded can never throttle the retry.
  if [ "$STATE_ESCALATE_TS" -gt 0 ] && [ $((now - STATE_ESCALATE_TS)) -lt "$w" ]; then
    printf 'wait_reminder'; return 0
  fi
  # A previous escalation was CONFIRMED delivered (escalate_ts is non-zero only
  # while escalate_state=sent) and its window has elapsed — this is a REMINDER.
  # Without this the caller would have to re-derive the distinction from the
  # state it already handed over, and the message would say "HUMAN NEEDED" on
  # every repeat instead of announcing itself as a reminder.
  if [ "$STATE_ESCALATE_TS" -gt 0 ]; then printf 'remind'; return 0; fi
  printf 'page'
}

# Prose for the incident body's ### Escalation section. NEVER names the chat id
# (`redact_text` scrubs the bot TOKEN, not the recipient) — an operator needs the
# CHANNEL KIND and whether a human was actually reached, not who.
escalation_note() {
  if [ "$ESCALATE_ENABLED" != "1" ]; then
    printf 'Sustained-incident escalation is **DISABLED** (`ESCALATE_ENABLED=0`, an operator kill switch) — no page will be sent for this incident. A deliberate operator kill, not an all-clear.'
  elif [ "$STATE_ESCALATE_STATE" = "failed" ]; then
    printf '⛔ **The last sustained-incident page was NOT DELIVERED** — the alert channel rejected it or could not be reached. **A human has NOT been reached for this incident, so this is not an all-clear.** The watchdog retries on every run and each of those runs fails, which makes it visible rather than silent; check `TELEGRAM_BOT_TOKEN` / `ESCALATION_CHAT_ID` and the Telegram API.'
  elif [ "$STATE_ESCALATE_STATE" = "pending" ]; then
    printf '⏳ A sustained-incident page is being attempted **right now** (the attempt is recorded before sending; a run that dies before recording the outcome is treated next run as unknown, so it retries).'
  elif [ "$STATE_ESCALATE_TS" -gt 0 ]; then
    printf '📟 **A human was paged for this sustained incident** — last CONFIRMED delivery at %s. Reminders at most once per %s min while it stays failing, and never within %s min of any other confirmed human page.' \
      "$(fmt_iso "$STATE_ESCALATE_TS")" "$CAP_RENOTIFY_MINUTES" "$CAP_RENOTIFY_MINUTES"
  else
    printf 'No sustained-incident page yet. After %s min of continuous failure AND %s observed failing runs (both required — one bad probe cannot page), a human is paged on the configured channel; then at most once per %s min.' \
      "$ESCALATE_SUSTAINED_MINUTES" "$ESCALATE_MIN_RUNS" "$CAP_RENOTIFY_MINUTES"
  fi
}

# 0 = restarted (machine ids echoed to stdout), 2 = could not (reason echoed)
do_restart() {
  local ids id out reason states
  if ! command -v flyctl >/dev/null 2>&1; then
    printf 'flyctl is not installed (FLY_API_TOKEN absent → the setup step was skipped)'
    return 2
  fi
  if ! out="$(flyctl machine list --app "$FLY_APP" --json 2>"$RUN_TMP/fly.err")"; then
    reason="$(scrub_output "$(cat "$RUN_TMP/fly.err" 2>/dev/null || true)" 200)"
    printf 'flyctl machine list failed: %s' "$reason"
    return 2
  fi
  ids="$(printf '%s' "$out" | jq -r '.[]? | select(.state == "started") | .id' 2>/dev/null || true)"
  if [ -z "$ids" ]; then
    # Report WHAT the fleet looks like: `starting`/`created` means a boot or a
    # deploy is in flight, in which case "restart FAILED" is the wrong story.
    states="$(printf '%s' "$out" | jq -r '[.[]? | .state] | join(",")' 2>/dev/null || true)"
    printf 'no STARTED machine for app %s (states: %s) — %s' "$FLY_APP" "${states:-unknown}" \
      "$(case "$states" in *starting*|*created*) printf 'a boot/deploy looks in flight — a restart would not help';; *) printf 'fleet state could not be confirmed';; esac)"
    return 2
  fi
  for id in $ids; do
    if ! flyctl machine restart "$id" --app "$FLY_APP" >/dev/null 2>>"$RUN_TMP/fly.err"; then
      reason="$(scrub_output "$(cat "$RUN_TMP/fly.err" 2>/dev/null || true)" 200)"
      printf 'flyctl machine restart %s failed: %s' "$id" "$reason"
      return 2
    fi
    printf '%s ' "$id"
  done
  return 0
}

# ── main ────────────────────────────────────────────────────────────────────
main() {
  local now kind kindlabel title marker issue decision heal_note comment_body
  local transition_kind restarted_ids rc n_loop kind_loop title_loop ledger_src stale_note="" is_prod=0 body_loop=""
  local esc_decision esc_pending esc_text esc_subject esc_why
  # Cross-incident restart budget (see recent_restart_ledger): the ledger
  # carried from the previous incident, whether it was readable, and whether the
  # carried ledger was itself corrupt (which must stay fail-closed).
  local carried_ledger="" carried_invalid="0" carried_source="" ledger_ok=1

  # Fail closed: a monitor that cannot file is a DEAF monitor (#2140 class).
  if [ -z "$GH_TOKEN" ]; then
    fail "GH_TOKEN is not set — refusing to run a monitor that cannot file or close issues (a deaf monitor is worse than no monitor)"
    exit 1
  fi
  if ! command -v jq >/dev/null 2>&1; then
    fail "jq is required"
    exit 1
  fi

  # Numeric env hardening: a misconfigured value must not turn into nonsense
  # (PROBE_ATTEMPTS=0 would "confirm" an outage from zero probes).
  PROBE_ATTEMPTS="$(int_or "$PROBE_ATTEMPTS" 3 1)"
  PROBE_CONNECT_TIMEOUT_S="$(int_or "$PROBE_CONNECT_TIMEOUT_S" 10 1)"
  PROBE_TIMEOUT_S="$(int_or "$PROBE_TIMEOUT_S" 25 1)"
  PROBE_RETRY_SLEEP_S="$(int_or "$PROBE_RETRY_SLEEP_S" 10 0)"
  RECOVERY_CONFIRM_PROBES="$(int_or "$RECOVERY_CONFIRM_PROBES" 1 0)"
  SUSTAINED_DOWN_MINUTES="$(int_or "$SUSTAINED_DOWN_MINUTES" 10 1)"
  RESTART_COOLDOWN_MINUTES="$(int_or "$RESTART_COOLDOWN_MINUTES" 20 1)"
  # 0 is a deliberate KILL SWITCH ("never auto-restart"), not a typo to round up
  # to the default — honour it, and let the cap branch report why.
  if [ "$(to_int "$MAX_RESTARTS_PER_HOUR" "")" = "0" ]; then
    MAX_RESTARTS_PER_HOUR=0
  else
    MAX_RESTARTS_PER_HOUR="$(int_or "$MAX_RESTARTS_PER_HOUR" 2 1)"
  fi
  COMMENT_THROTTLE_MINUTES="$(int_or "$COMMENT_THROTTLE_MINUTES" 15 0)"
  CAP_RENOTIFY_MINUTES="$(int_or "$CAP_RENOTIFY_MINUTES" 60 1)"
  STALE_RESET_MINUTES="$(int_or "$STALE_RESET_MINUTES" 45 1)"
  CONTROL_TIMEOUT_S="$(int_or "$CONTROL_TIMEOUT_S" 10 1)"
  CONTROL_ATTEMPTS="$(int_or "$CONTROL_ATTEMPTS" 2 1)"
  # ceil(SUSTAINED/5), floored at 2 observed failing runs.
  local default_min_runs=$(( (SUSTAINED_DOWN_MINUTES + 4) / 5 ))
  [ "$default_min_runs" -ge 2 ] || default_min_runs=2
  SUSTAINED_MIN_RUNS="$(int_or "${SUSTAINED_MIN_RUNS:-}" "$default_min_runs" 1)"
  # The escalation knobs derive from the values just normalized above.
  normalize_escalation_knobs "$SUSTAINED_DOWN_MINUTES" "$SUSTAINED_MIN_RUNS"

  # A URL that is not a member of PROD_PROBE_URLS is a DRILL: it must not
  # restart production AND must not resolve (close/comment) a production
  # incident. SET MEMBERSHIP, not a boolean flag — see is_production_url.
  if is_production_url "$PROBE_URL"; then is_prod=1; fi
  # …and it gets its OWN incident identity (marker/title/label). See
  # set_incident_identity — a shared marker would make a drill write its state
  # INTO the live production incident.
  set_incident_identity "$is_prod"

  now="$(now_epoch)"
  log "availability-watchdog (#2850) — probe $(redact_url "$PROBE_URL") → ${REPO}$([ "$is_prod" = 1 ] || printf ' [DRILL: self-heal + incident resolution DISARMED]')"
  log "limits: sustained=${SUSTAINED_DOWN_MINUTES}m/${SUSTAINED_MIN_RUNS} runs cooldown=${RESTART_COOLDOWN_MINUTES}m cap=${MAX_RESTARTS_PER_HOUR}/h comment-throttle=${COMMENT_THROTTLE_MINUTES}m cap-renotify=${CAP_RENOTIFY_MINUTES}m stale-reset=${STALE_RESET_MINUTES}m"
  # The escalation recipient is logged as a KIND, never the chat id (the run log
  # is PUBLIC-ish and redact_text scrubs the bot token, not the recipient).
  log "escalation: enabled=${ESCALATE_ENABLED} sustained=${ESCALATE_SUSTAINED_MINUTES}m/${ESCALATE_MIN_RUNS} runs remind=${CAP_RENOTIFY_MINUTES}m recipient=$(if [ -n "${ESCALATION_CHAT_ID}" ]; then if [ "${ESCALATION_CHAT_ID}" = "${TELEGRAM_CHAT_ID}" ]; then printf 'telegram-default'; else printf 'telegram-override'; fi; else printf 'UNCONFIGURED'; fi)"

  probe
  log "verdict: ${PROBE_VERDICT} (HTTP ${PROBE_CODE})"

  # ── UP ───────────────────────────────────────────────────────────────────
  if [ "$PROBE_VERDICT" = "UP" ]; then
    note "✅ ${PROBE_HOST_LABEL} is UP (HTTP ${PROBE_CODE})"
    if [ "$is_prod" != 1 ]; then
      # A drill resolves DRILL incidents only — its markers are DRILL-specific,
      # so the loop below can never touch a production incident. Resolving is
      # still needed: otherwise a typo'd drill host files an issue nothing can
      # ever close (a slow-motion #2706 accumulation).
      note "drill probe (PROBE_URL is not the production endpoint) — resolving only DRILL incidents; never a production one, never a restart"
    fi
    # Recovery hysteresis: a single flapping success must not close a live
    # incident (and reset the sustained clock). Require RECOVERY_CONFIRM_PROBES
    # additional UP answers.
    local i=1 confirmed=1
    while [ "$i" -le "$RECOVERY_CONFIRM_PROBES" ]; do
      local saved_verdict="$PROBE_VERDICT" saved_evidence="$PROBE_EVIDENCE"
      probe
      if [ "$PROBE_VERDICT" != "UP" ]; then
        # Deliberately do NOT restore the saved UP verdict/evidence: the
        # fall-through below needs the FAILING probe's result.
        confirmed=0
        warn "recovery NOT confirmed — confirmation probe ${i}/${RECOVERY_CONFIRM_PROBES} returned ${PROBE_VERDICT} (HTTP ${PROBE_CODE}); NOT closing any incident (flapping)"
        break
      fi
      PROBE_VERDICT="$saved_verdict"
      PROBE_EVIDENCE="$saved_evidence"
      i=$((i + 1))
    done

    if [ "$confirmed" = "1" ]; then
      for kind_loop in DOWN DEGRADED; do
        if [ "$kind_loop" = "DOWN" ]; then marker="$DOWN_MARKER"; title_loop="$DOWN_TITLE"; else marker="$DEGRADED_MARKER"; title_loop="$DEGRADED_TITLE"; fi
        n_loop="$(search_open_alert "$marker" "$title_loop")"
        case "$n_loop" in
          __ERR__*|'')
            if [ "$n_loop" = "__ERR__" ]; then
              # A monitor that cannot read its own incident state is broken; the
              # service being up does not make that acceptable (the next DOWN run
              # could act on a stale incident).
              fail "issue search failed on the recovery path — the monitor cannot confirm incident state; failing the run (service is UP)"
              exit 1
            fi
            continue ;;
          *[!0-9]*)
            fail "issue search returned a non-numeric issue id ('${n_loop}') — refusing to act (service is UP)"
            exit 1 ;;
        esac
        body_loop="$(get_issue_body "$n_loop")"
        if [ "$body_loop" = "__ERR__" ]; then
          fail "could not read the body of #${n_loop} — refusing to close an incident whose state cannot be read (the body carries the restart ledger)"
          exit 1
        fi
        parse_state "$body_loop"
        # CLOSE FIRST, comment second: a persistently failing close PATCH must
        # not re-post "Recovered" on every run (the UP path has no throttle
        # stamp — the closed incident itself is what ends the repetition).
        if ! close_issue "$n_loop"; then
          fail "could not close #${n_loop} — failing the run; a stale open incident can authorise a restart later"
          exit 1
        fi
        if ! comment_issue "$n_loop" "✅ **Recovered** — the availability probe at \`$(redact_url "$PROBE_URL")\` answered HTTP ${PROBE_CODE} at $(fmt_iso "$now").

The service failed ${STATE_DOWN_RUNS} probe run(s), starting $(fmt_iso "$STATE_FIRST_FAILURE_TS") (~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min). This incident is closed — the watchdog re-files automatically if it recurs."; then
          fail "#${n_loop} was CLOSED but the 'Recovered' comment failed — the state is correct, the record is missing; failing the run so it is not silent"
          exit 1
        fi
        note "closed ${kind_loop} incident #${n_loop} (recovered after ${STATE_DOWN_RUNS} failing run(s))"
        page "✅ RECOVERED — ${PROBE_HOST_LABEL} answers HTTP ${PROBE_CODE} again. Incident #${n_loop} closed after ${STATE_DOWN_RUNS} failing probe run(s) (~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min)."
      done
      exit 0
    fi

    # Unconfirmed recovery (the app answered once, then failed): an OPEN
    # incident is already the standing alert, so leave it open and stay green.
    # With NO incident open, reporting green after observing a full failure
    # would be the "green while down" failure mode — fall through to the
    # DOWN/UNEXPECTED path below with the failing confirmation probe's verdict.
    local any_open=0
    for kind_loop in DOWN DEGRADED; do
      if [ "$kind_loop" = "DOWN" ]; then marker="$DOWN_MARKER"; title_loop="$DOWN_TITLE"; else marker="$DEGRADED_MARKER"; title_loop="$DEGRADED_TITLE"; fi
      n_loop="$(search_open_alert "$marker" "$title_loop")"
      case "$n_loop" in
        __ERR__*) fail "issue search failed while checking for an open incident (flapping probe) — the monitor cannot confirm incident state; failing the run"; exit 1 ;;
        *[!0-9]*) fail "issue search returned a non-numeric issue id ('${n_loop}') — refusing to act"; exit 1 ;;
        '') : ;;
        *) any_open=1 ;;
      esac
    done
    if [ "$any_open" = "1" ]; then
      note "recovery not confirmed and an incident is already open — leaving it open (the standing alert)"
      # #3887: this early exit sits BEFORE the sustained-incident escalation leg,
      # so a flapping service can hold an open incident for hours while that leg
      # never runs — nothing is paged and the run is GREEN. Changing WHEN a flap
      # counts as still-failing is a behavioural change that needs its own
      # design, so this run NAMES the gap instead (mirroring the corrupt-ledger
      # path) rather than leaving "no page" to be read as "nothing to page
      # about". See runbook § *Known limits*.
      warn "recovery not confirmed (flapping) and an incident is already open — the sustained-incident escalation leg is NOT reached on this path, so NO escalation page is sent this run; the open incident is the standing alert (runbook § Known limits)"
      exit 0
    fi
    warn "recovery NOT confirmed (${PROBE_VERDICT}, HTTP ${PROBE_CODE}) and NO incident is open — filing an incident for the observed failure"
    # fall through: PROBE_VERDICT / PROBE_CODE / PROBE_EVIDENCE are the failing
    # confirmation probe's, which is exactly the state to report.
  fi

  # ── DOWN / UNEXPECTED ────────────────────────────────────────────────────
  if [ "$PROBE_VERDICT" = "DOWN" ]; then
    kind="DOWN"; kindlabel="DOWN (no answer)"; title="$DOWN_TITLE"; marker="$DOWN_MARKER"
  else
    kind="DEGRADED"; kindlabel="UNEXPECTED (answered wrongly)"; title="$DEGRADED_TITLE"; marker="$DEGRADED_MARKER"
  fi

  # UNEXPECTED never restarts (a restart cannot fix a 404 / a redirect), a
  # non-member URL is a drill, and a DNS/TLS failure is not a restart's
  # business (see restartable_failure).
  local restart_mode="$kind"
  if [ "$kind" = "DEGRADED" ]; then
    restart_mode="disarmed:unexpected"
  elif [ "$is_prod" != 1 ]; then
    restart_mode="disarmed:drill"
  elif ! is_restartable_url "$PROBE_URL"; then
    # #3628: a PRODUCTION surface with no Fly machine behind it (the Pages
    # auth route). A restart of the API app cannot repair a missing binding
    # and would restart an unrelated service — hard disarm, not a judgement
    # call. Unlike the failure-class guard below, this branch disarms whatever
    # the DOWN layer was. It is reachable ONLY for a DOWN verdict: a DEGRADED
    # verdict was already disarmed as `disarmed:unexpected` above, so the auth
    # target logs `no_machine` on a 503/timeout and `unexpected` on a
    # 302-with-missing-header (review P3 — the runbook used to claim this
    # target always logs `no_machine`).
    restart_mode="disarmed:no_machine"
    log "probe target is a production surface with no Fly machine — NOT restartable; the incident will be reported without a restart"
  elif ! restartable_failure "$PROBE_FAILURE_CLASS"; then
    # No machine restart repairs a name-resolution or certificate problem; it
    # would only spend the restart budget and add noise. The incident is still
    # filed and reported, and the body says why no restart ran.
    restart_mode="disarmed:unfixable"
    log "probe failed at the '${PROBE_FAILURE_CLASS}' layer — NOT restartable; the incident will be reported without a restart"
  fi

  issue="$(search_open_alert "$marker" "$title")"
  case "$issue" in
    __ERR__*)
      fail "GitHub issue search failed — refusing to file (never duplicate). This failing run IS the alert."
      exit 1 ;;
    *[!0-9]*)
      fail "GitHub issue search returned a non-numeric issue id ('${issue}') — refusing to act (never comment on or file a garbage id)"
      exit 1 ;;
  esac

  if [ -z "$issue" ]; then
    # CROSS-INCIDENT RESTART BUDGET (P2): a new incident inherits the
    # still-in-window restart stamps of the last machine incident, so closing
    # and reopening the incident (which is what flapping looks like) cannot
    # reset the hourly cap. Only the restart path needs it.
    if [ "$restart_mode" = "DOWN" ]; then
      if recent_restart_ledger "$marker" "$now" "$title"; then
        carried_ledger="$STATE_RESTARTS"
        carried_invalid="$STATE_RESTARTS_INVALID"
        carried_source="$LEDGER_SOURCE_ISSUE"
      else
        ledger_ok=0
      fi
    fi
    # New incident — the issue IS the dedupe key and the state store.
    STATE_FIRST_FAILURE_TS="$now"
    STATE_DOWN_RUNS="0"
    STATE_LAST_DOWN_TS="$now"
    STATE_LAST_COMMENT_TS="0"
    STATE_CAP_NOTIFIED_TS="0"
    # #3887: a NEW incident starts with NO escalation history. Leaving these at
    # the previous incident's values would stamp the new incident `sent` and
    # render a false "A human was paged for this sustained incident" claim.
    STATE_ESCALATE_STATE=""
    STATE_ESCALATE_TS="0"
    STATE_PAGE_OK_TS="0"
    # A new incident's escalation anchor is its creation — which is this run.
    STATE_ESCALATE_ANCHOR_TS="$now"
    HUMAN_PAGED_THIS_RUN="0"
    STATE_RESTARTS="$carried_ledger"
    STATE_RESTARTS_INVALID="$carried_invalid"
    # DURABLE FAIL-CLOSED LEDGER SENTINEL (round 4, P2-7): persist WHY the
    # seeded ledger is empty, so the next run cannot read that emptiness as a
    # valid, unlimited budget. See the globals and the re-verify block below.
    if [ "$ledger_ok" = "0" ]; then
      STATE_LEDGER_STATE="unreadable"
      STATE_LEDGER_SRC=""
    elif [ "$carried_invalid" = "1" ]; then
      STATE_LEDGER_STATE="invalid"
      STATE_LEDGER_SRC="$carried_source"
    fi
    progress="new"
  else
    body_loop="$(get_issue_body "$issue")"
    if [ "$body_loop" = "__ERR__" ]; then
      fail "could not read the body of incident #${issue} — the body is the cooldown/cap memory, so acting on an unreadable state could restart without a limit; refusing to act (never treat unreadable as 'no state')"
      exit 1
    fi
    parse_state "$body_loop"
    progress="repeat"
    # ── server-side anchor for the sustained window ─────────────────────────
    # SUSTAINED_DOWN_MINUTES used to gate solely on the body's
    # first_failure_ts, which the body itself carries. The issue's created_at
    # is set by GitHub and is NOT body-editable, so clamping the sustained
    # clock to it makes the window unforgeable from below: no body edit (and no
    # look-alike issue) can authorise a restart before the incident has
    # actually existed for SUSTAINED_DOWN_MINUTES. Belt-and-braces with the
    # machine-author search constraint.
    local created_ts
    created_ts="$(iso_to_epoch "$(cat "$RUN_TMP/issue.created_at" 2>/dev/null || true)")"
    if [ -z "$created_ts" ]; then
      # Production ALWAYS returns created_at; absent/unparseable means we have
      # no unforgeable anchor, so do not trust the body's clock — restart the
      # window (fail closed).
      log "incident #${issue} has no usable server-side created_at — restarting the sustained window (fail closed)"
      STATE_FIRST_FAILURE_TS="$now"
    else
      # A clock in the future (skew) would lock the sustained gate forever.
      if [ "$created_ts" -gt "$now" ]; then created_ts="$now"; fi
      if [ "$STATE_FIRST_FAILURE_TS" -lt "$created_ts" ]; then
        log "incident #${issue}: first_failure_ts precedes its created_at — clamping the sustained clock to the server-side created_at"
        STATE_FIRST_FAILURE_TS="$created_ts"
      fi
    fi
    # ── the ESCALATION leg's wall-clock anchor (G2/G3) ──────────────────────
    # The escalation leg is the one whose whole job is to reach a human for an
    # incident that nothing else closes, so its wall-clock start must NOT be a
    # value the stale-clock reset can zero (G2) and must NOT be one a body edit
    # can push into the future (G3). The incident's `created_at` is
    # GitHub-assigned and body-immutable, so it is the anchor whenever it is
    # usable. When it is NOT usable we have no unforgeable start, and the safe
    # direction for a PAGER is TOWARD paging: the wall-clock leg is deferred to
    # the run leg (`ESCALATE_MIN_RUNS`), which still requires that many OBSERVED
    # failing runs. `0` is the "already elapsed" sentinel — it can never mute.
    # This is SEPARATE from STATE_FIRST_FAILURE_TS so the restart leg keeps its
    # resettable continuous-failure clock, byte-identical.
    if [ -n "$created_ts" ]; then
      STATE_ESCALATE_ANCHOR_TS="$created_ts"
    else
      STATE_ESCALATE_ANCHOR_TS="0"
    fi
    # Stale-clock guard: if no failing run has been recorded recently, this
    # incident is NOT continuous (a human reopened it, a close failed after
    # recovery, or the issue sat open) — restart the sustained window instead
    # of trusting an ancient first_failure_ts.
    #
    # It resets ONLY the sustained clock. It must NOT touch the restart ledger
    # (restarts / cap_notified_ts): those are the rate-limit memory, and
    # clearing them here would disarm the cooldown and the hourly cap on any
    # run gap — the very moment (an incident) when GitHub delays runs. It must
    # not reset down_runs either: each run OBSERVES a failure, and pinning the
    # count at 0 would starve the SUSTAINED_MIN_RUNS gate forever (no restart,
    # ever) whenever the schedule slips.
    if [ $((now - STATE_LAST_DOWN_TS)) -gt $((STALE_RESET_MINUTES * 60)) ]; then
      stale_note="ℹ️ Stale incident clock reset: the previous failing run was $(fmt_iso "$STATE_LAST_DOWN_TS") (more than ${STALE_RESET_MINUTES} min ago), so the ${SUSTAINED_DOWN_MINUTES} min sustained window restarts now. Failing runs are counted continuously since $(fmt_iso "$now")."
      log "stale incident (last failing run $(fmt_iso "$STATE_LAST_DOWN_TS")) — sustained clock reset (the restart ledger is preserved)"
      STATE_FIRST_FAILURE_TS="$now"
    fi
  fi
  STATE_DOWN_RUNS=$((STATE_DOWN_RUNS + 1))
  STATE_LAST_DOWN_TS="$now"

  # Create the issue BEFORE acting, so every later write has a target.
  if [ "$progress" = "new" ]; then
    issue="$(create_issue "$title" "$(render_body "$kind" "$kindlabel" "⏳ Diagnosing — the self-healing decision is written at the end of this run.")")"
    if [ -z "$issue" ]; then
      fail "could not file the incident issue — this failing run IS the alert (runbook documents manual filing)"
      exit 1
    fi
    note "filed ${kind} incident #${issue} — ${title}"
    page "🔴 ${kind} — ${PROBE_HOST_LABEL} probe failing (HTTP ${PROBE_CODE} after ${PROBE_ATTEMPTS} attempts). Incident #${issue}: https://github.com/${REPO}/issues/${issue}"
  fi

  # ── self-heal decision ───────────────────────────────────────────────────
  # transition_kind drives the comment policy:
  #   major     → always comment (restart / restart-failed / velocity cap)
  #   otherwise → comment only if the throttle window has elapsed
  heal_note=""
  comment_body=""
  transition_kind="repeat"

  if [ "$restart_mode" = "DOWN" ]; then
    # ── DURABLE FAIL-CLOSED LEDGER SENTINEL, re-verify (round 4, P2-7) ────
    # An earlier run refused to trust the ledger and PERSISTED that verdict in
    # the state block. Re-derive it BEFORE the decision: without this, adopting
    # the now-open incident would read its empty `restarts=` as valid and arm
    # with an EMPTY hourly budget — silently losing the cap stamps the source
    # carried (the restart-storm the cross-incident ledger exists to prevent).
    # NOT on `progress=new`: this run's carry block ABOVE just read the source
    # (recent_restart_ledger did the authoritative lookup), so re-reading it here
    # would be a second, identical GET on the same body.
    if [ "$progress" != "new" ]; then
      case "$STATE_LEDGER_STATE" in
        invalid)
          if [ -n "$STATE_LEDGER_SRC" ] && reseed_ledger_from_source "$STATE_LEDGER_SRC"; then
            log "restart ledger in #${STATE_LEDGER_SRC} parses again — resuming with ledger [$(restart_history)]"
            STATE_LEDGER_STATE=""
            STATE_LEDGER_SRC=""
          fi ;;
        unreadable)
          # Retry the previous-incident lookup, EXCLUDING this incident — it is
          # open now and would otherwise be returned as "the most recent", so the
          # real previous ledger would never be seen. A successful lookup that
          # finds no OTHER incident means the budget really is empty.
          if recent_restart_ledger "$marker" "$now" "$title" "${issue:-}"; then
            if [ "$STATE_RESTARTS_INVALID" = "1" ]; then
              # A source WAS found this time, and it is corrupt.
              STATE_LEDGER_STATE="invalid"
              STATE_LEDGER_SRC="${LEDGER_SOURCE_ISSUE:-}"
            else
              log "previous-incident ledger lookup succeeded — resuming with ledger [$(restart_history)]"
              STATE_LEDGER_STATE=""
              STATE_LEDGER_SRC="${LEDGER_SOURCE_ISSUE:-}"
            fi
          fi ;;
      esac
    fi

    if [ "$ledger_ok" = "0" ] || [ "$STATE_LEDGER_STATE" = "unreadable" ]; then
      # Fail closed: without the previous incident's ledger we cannot prove
      # this restart is inside the hourly cap, and a restart storm is the worse
      # failure. The incident is still filed/escalated below, and the verdict
      # is PERSISTED so the next run keeps failing closed rather than arming
      # with an empty budget (round 4, P2-7).
      restart_mode="disarmed:no_ledger"
      STATE_LEDGER_STATE="unreadable"
      STATE_LEDGER_SRC=""
      warn "the previous incident's restart ledger could not be read — cannot prove the hourly budget; NOT restarting (fail closed; the next run retries the lookup)"
    elif [ "$STATE_RESTARTS_INVALID" = "1" ] || [ "$STATE_LEDGER_STATE" = "invalid" ]; then
      # FAIL CLOSED on an unparseable restart ledger BEFORE the egress control,
      # so a runner-side network failure cannot mask a corrupt ledger (and the
      # run never rewrites the body with the corrupt ledger silently
      # discarded). `restarts=` is the only cooldown/cap memory; if we cannot
      # read it whole we cannot prove a restart is allowed, and silently
      # treating it as "no restarts" is exactly the fail-OPEN direction that
      # turns into a restart storm. Refuse to act (like an unreadable issue
      # body) and fail the run loudly — the open incident remains the standing
      # alert.
      # NAME THE SOURCE (round 3, P2-4; extended round 4, P2-7): on a NEW
      # incident the corrupt value came from the PREVIOUS incident's body
      # (recent_restart_ledger read it), and on the durable-sentinel retry it
      # is named by `ledger_src=` — so taking $issue would send the operator to
      # the wrong (or to a freshly-recreated) issue.
      ledger_src="${LEDGER_SOURCE_ISSUE:-${STATE_LEDGER_SRC:-$issue}}"
      # Persist the verdict so the next run does not re-arm: on a repeat run
      # this is what keeps the incident fail-closed without relying on the
      # empty `restarts=` being non-empty (round 4, P2-7).
      STATE_LEDGER_STATE="invalid"
      STATE_LEDGER_SRC="$ledger_src"
      log "restart decision: disarmed:corrupt_ledger"
      fail "the restart ledger in incident #${ledger_src} is present but not fully parseable ('$(scrub_output "$STATE_RESTARTS_RAW" 120)') — refusing to restart: a dropped entry could only WEAKEN the cooldown/hourly cap. Fix the \`restarts=\` field in #${ledger_src}'s body (ts,ts or empty) and the next run resumes."
      # ⛔ NO ESCALATION PAGE IS SENT FROM THIS PATH, and saying nothing about it
      # would read as "already handled". The reason is structural: the incident
      # body is the escalation leg's idempotency store, and this branch refuses
      # to REWRITE that body at all (rewriting it would re-render `restarts=`
      # from the unparseable value it refused to trust, i.e. ERASE the ledger the
      # next run needs). Without a writable stamp a send would repeat on every
      # run — a page storm — so the leg cannot run here. The gap is named rather
      # than hidden, and it is the pager-liveness surface tracked in #4573.
      warn "no escalation page is sent for this incident while its state cannot be written (a send would repeat every run); the failing run IS the signal until the ledger is fixed — see tortoise #4573"
      # Do not leave a NEW incident promising "⏳ Diagnosing — the self-healing
      # decision is written at the end of this run" when this run ends here.
      # Record the disarm in the new incident — but NEVER PATCH the source: on a
      # repeat run $issue IS the corrupt issue and a write there would ERASE the
      # ledger we refused to trust (tests 44b/c/d assert zero PATCHes there).
      if [ "$progress" = "new" ]; then
        if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "⛔ **No restart attempted — the restart ledger in incident #${ledger_src} is corrupt.** The \`restarts=\` field there is present but not fully parseable ('$(scrub_output "$STATE_RESTARTS_RAW" 120)'), so the watchdog cannot prove another restart is inside the ${MAX_RESTARTS_PER_HOUR}/hour cap and fails closed rather than risk a restart storm. Fix the \`restarts=\` field in #${ledger_src}'s body (a comma-separated list of epoch stamps, or empty) and the next run resumes. Runbook § *Out-of-band availability watchdog*.")"; then
          warn "could not record the corrupt-ledger disarm in #${issue} (the run still fails closed)"
        fi
      fi
      exit 1
    fi
    # Runner-side egress control: a DOWN verdict from a runner that cannot
    # reach the internet says nothing about the app, and a restart is the
    # disruptive action here — do not take it on an untrustworthy verdict.
    if ! control_ok; then
      restart_mode="disarmed:no_egress"
      warn "control probe $(redact_url "$CONTROL_URL") also failed from this runner — cannot distinguish a runner network failure from an app outage; NOT restarting"
    fi
  fi

  # Round 3, P2-5: the runbook (§6.4) states every run records its verdict as
  # `disarmed:<reason>` or the armed path. `restart_mode` was only ever
  # COMPARED, never printed, so the doc was false. Emit it once the mode is
  # final (after the ledger/egress downgrades above).
  log "restart decision: ${restart_mode}"

  if [ "$restart_mode" = "DOWN" ]; then
    decision="$(decide_restart "$now")"
    # Round 4, P3-8: the mode line above is the ARM/DISARM verdict, but on the
    # armed path it prints only `DOWN` — the REASON a restart did not happen
    # yet (`wait_sustained` / `wait_runs` / `wait_cooldown` / `cap`) is decided
    # here and was logged NOWHERE, so the runbook's "why a restart did not
    # happen is in the run log" was false for a sustained-but-in-cooldown run.
    # Print the outcome too; the runbook now names both lines.
    log "restart outcome: ${decision}"
    case "$decision" in
      go)
        if [ -z "$FLY_API_TOKEN" ]; then
          heal_note="⚠️ **Restart NOT issued — the \`FLY_API_TOKEN\` repository secret is not set.** Self-healing is inert until an operator creates it (\`gh secret set FLY_API_TOKEN\`, a Fly token with machine-restart scope — \`fly tokens create deploy --app ${FLY_APP}\` is enough). Until then a human must restart manually: see the runbook § *Out-of-band availability watchdog*."
          warn "restart skipped — the FLY_API_TOKEN secret is absent"
          transition_kind="heal_disarmed"
        else
          # WRITE-THEN-ACT: record the attempt in the incident body BEFORE
          # calling flyctl. The body is the only cooldown/cap memory, so a
          # crash, a lost response, or a failed restart API call must not be
          # able to erase it (that would allow an unthrottled restart on every
          # run). If the record cannot be written, do NOT restart.
          STATE_RESTARTS="${STATE_RESTARTS:+$STATE_RESTARTS }$now"
          STATE_CAP_NOTIFIED_TS="0"
          if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "🚑 Restart attempt #$((STATE_DOWN_RUNS)) recorded at $(fmt_iso "$now") — issuing \`flyctl machine restart\` next; the next probe run is the recovery check.")"; then
            fail "could not record the restart attempt in #${issue} — NOT restarting (refusing to restart without a durable cooldown/cap record)"
            exit 1
          fi
          set +e
          restarted_ids="$(do_restart)"
          rc=$?
          set -e
          if [ "$rc" -eq 0 ]; then
            heal_note="🚑 **Restart issued** at $(fmt_iso "$now"): \`flyctl machine restart\` on \`${restarted_ids}\` (app \`${FLY_APP}\`). A restart takes ~60–90 s to boot, so the next probe run is the recovery check."
            transition_kind="restart"
            comment_body="🚑 **Self-heal — restarting Fly machine(s)** \`${restarted_ids}\` (app \`${FLY_APP}\`) at $(fmt_iso "$now").

Down for ~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min across ${STATE_DOWN_RUNS} failing run(s) (thresholds: ${SUSTAINED_DOWN_MINUTES} min and ≥${SUSTAINED_MIN_RUNS} runs; ${RESTART_COOLDOWN_MINUTES} min cooldown; ${MAX_RESTARTS_PER_HOUR}/hour cap).

A restart is a **symptom fix** — if this recurs, the root cause is still live (#2850: a FalkorDB socket timeout wedges the event loop; #2953: uvicorn binds the socket only after lifespan startup)."
            note "restart issued on: $(redact_text "${restarted_ids:-<unknown>}")"
            page_human "🚑 SELF-HEAL — ${PROBE_HOST_LABEL} down ~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min; restarting ${restarted_ids} (app ${FLY_APP}). Incident #${issue}."
          else
            heal_note="⛔ **Automatic restart FAILED** — ${restarted_ids}. **A human must intervene now**: \`flyctl machine restart <machine-id> -a ${FLY_APP}\` (\`<machine-id>\` from \`flyctl machine list -a ${FLY_APP}\`), or inspect \`flyctl logs -a ${FLY_APP}\`. This attempt is COUNTED against the ${MAX_RESTARTS_PER_HOUR}/hour cap (the watchdog will not retry it every 5 minutes). Runbook § *Out-of-band availability watchdog*."
            transition_kind="heal_failed"
            comment_body="$heal_note"
            fail "automatic restart failed: $(redact_text "${restarted_ids:-<unknown>}") (recorded as an attempt; counted against the hourly cap)"
            page_human "⛔ SELF-HEAL FAILED — ${PROBE_HOST_LABEL} still down; the automatic restart did not work (${restarted_ids}). A human is needed. Incident #${issue}."
          fi
        fi
        ;;
      wait_sustained)
        heal_note="⏳ No restart yet: down for $(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min. The watchdog waits ${SUSTAINED_DOWN_MINUTES} min of continuous failure before the first automated restart."
        ;;
      wait_runs)
        heal_note="⏳ No restart yet: ${STATE_DOWN_RUNS} failing run(s) observed; at least ${SUSTAINED_MIN_RUNS} are required (guard against a stale/reopened incident whose stored clock is old)."
        ;;
      wait_cooldown)
        heal_note="⏳ No restart yet: the ${RESTART_COOLDOWN_MINUTES} min cooldown between automated restarts has not elapsed."
        ;;
      cap)
        if [ "$MAX_RESTARTS_PER_HOUR" = "0" ]; then
          heal_note="⛔ **Automated restarts are DISABLED (\`MAX_RESTARTS_PER_HOUR=0\`, an operator kill switch) and the service is still down.** No restart will be attempted — a human must investigate: \`flyctl machine list -a ${FLY_APP}\`, \`flyctl logs -a ${FLY_APP}\`, FalkorDB reachability, the last deploy. Runbook § *Out-of-band availability watchdog*."
        else
          heal_note="⛔ **Restart velocity cap reached — a HUMAN must get involved.** ${MAX_RESTARTS_PER_HOUR} automated restart attempt(s) already ran within the last hour and the service is still down. A restart is not fixing it, so the watchdog deliberately stops: an unbounded restart loop on a database/platform outage makes the outage worse (this is the automated-remediation velocity cap). Investigate the root cause — \`flyctl logs -a ${FLY_APP}\`, FalkorDB reachability, the last deploy — and see the runbook § *Out-of-band availability watchdog*."
        fi
        comment_body="$heal_note"
        # The cap is a STATE, not an event: notify on entry, then re-notify at
        # most once per CAP_RENOTIFY_MINUTES (never every 5 minutes).
        if [ $((now - STATE_CAP_NOTIFIED_TS)) -ge $((CAP_RENOTIFY_MINUTES * 60)) ]; then
          transition_kind="cap"
          STATE_CAP_NOTIFIED_TS="$now"
          # WRITE-THEN-ACT again: if the notification stamp cannot be recorded,
          # a page now would repeat on EVERY run (the failure mode this
          # throttle exists to prevent). Publish only once it is durable.
          if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "$heal_note")"; then
            fail "could not record the cap notification in #${issue} — NOT escalating now (refusing to re-page every run because a write failed)"
            exit 1
          fi
          fail "restart velocity cap reached (${MAX_RESTARTS_PER_HOUR}/hour, ${STATE_DOWN_RUNS} failing runs) — escalating to a human"
          page_human "⛔ RESTART CAP REACHED — ${PROBE_HOST_LABEL} down ~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min and ${MAX_RESTARTS_PER_HOUR} restart attempt(s) in the last hour did not fix it. A HUMAN must intervene. Incident #${issue}."
        else
          transition_kind="cap_silent"
          warn "restart velocity cap still in force — already escalated $(( (now - STATE_CAP_NOTIFIED_TS) / 60 )) min ago (re-notify every ${CAP_RENOTIFY_MINUTES} min)"
        fi
        ;;
    esac
  else
    case "$restart_mode" in
      disarmed:drill)
        heal_note="🔒 Self-healing is **disarmed for this run** because \`PROBE_URL\` is not a production endpoint (a drill must never restart production). Recovery is NOT resolved from a drill either."
        transition_kind="disarmed"
        ;;
      disarmed:no_machine)
        heal_note="🔒 **No restart attempted — this is a production surface with NO Fly machine behind it.** \`$(redact_url "$PROBE_URL")\` is served by Cloudflare Pages; the automated restart leg targets the Fly API app (\`${FLY_APP}\`), so restarting it would restart an unrelated service and could not repair this failure. A \`503\` on the Pages auth surface usually means a missing/renamed binding (D1/KV) or a Pages routing change — check the Cloudflare Pages deployment, its bindings, and the last deploy (the deploy gate added in #3618 prevents deploying this class of fault; this probe catches the fault appearing AFTER a deploy). Runbook § *Out-of-band availability watchdog*."
        transition_kind="disarmed"
        ;;
      disarmed:unexpected)
        if [ "$PROBE_DEGRADED_REASON" = "header" ]; then
          heal_note="⛔ **No restart attempted** — the app ANSWERED, so a process restart is not the remediation. The failure is in the **PKCE flow**, not a wedged process: the redirect came back with an allowed status but without the required \`$(scrub_output "$PROBE_REQUIRE_HEADER" 120)\` header, which is the proof the flow row was written. Check the PKCE state written to D1/KV behind the auth route — the \`code_challenge\`/\`code_challenge_method\` pair, the D1 binding, and the last deploy of the auth function — NOT the route table or the deployed revision of the API app. Runbook § *Out-of-band availability watchdog*."
        elif is_restartable_url "$PROBE_URL"; then
          heal_note="⛔ **No restart attempted** — the app ANSWERED (an unexpected status, not silence), so a process restart is not the remediation. An unexpected \`404\`/\`3xx\` on an authenticated API route usually means a bad deploy or a moved route, not a wedged process: check the deployed revision and the route."
        else
          # Target-aware (review P3): this sibling branch used to call EVERY
          # surface "an authenticated API route", but the runbook tells
          # operators the incident body is the primary diagnostic — and the
          # auth target is a Cloudflare Pages route, not an API route. Keyed
          # on RESTARTABILITY (the same discriminator render_body uses), not on
          # the presence of an expectation knob. The status example is the
          # ACTUAL observed code, not a hardcoded class: `/auth/start` EXPECTS a
          # 302, so a `3xx` there is the NORMAL case and a `404` cannot occur —
          # the old `404`/`3xx` enumeration contradicted the evidence line above
          # it (review P3).
          heal_note="⛔ **No restart attempted** — the app ANSWERED (an unexpected status, not silence), so a process restart is not the remediation. An unexpected \`HTTP ${PROBE_CODE}\` on the probed production route \`$(redact_url "$PROBE_URL")\` — a Cloudflare Pages surface with no Fly machine behind it — usually means a bad deploy or a moved route, not a wedged process: check the Pages deployment and the route."
        fi
        transition_kind="disarmed"
        ;;
      disarmed:unfixable)
        # DNS and TLS/certificate failures (classify_failure) — the incident is
        # reported, the restart leg is disarmed, and the body says exactly why
        # so a human is not left wondering why self-healing did not fire.
        heal_note="⛔ **No restart attempted — the probe failed at a layer a machine restart cannot fix.** The last failing probe was $(failure_label "$PROBE_FAILURE_CLASS") against \`$(redact_url "$PROBE_URL")\`. Restarting the Fly machine would not repair name resolution or a TLS/certificate problem; it would only spend the automated-restart budget and add noise to an incident a human has to fix anyway. Check, in this order: the DNS record for the probe host, the certificate/SNI the app presents, then the platform proxy trace (\`flyctl logs -a ${FLY_APP}\`). Runbook § *Out-of-band availability watchdog*."
        transition_kind="disarmed"
        ;;
      disarmed:no_ledger)
        # The cross-incident restart budget is only as good as the previous
        # incident's ledger; without it we cannot prove this restart is inside
        # the hourly cap, so we fail closed (the incident is still reported).
        heal_note="⛔ **No restart attempted — the previous incident's restart ledger could not be read**, so the watchdog cannot prove another restart is inside the ${MAX_RESTARTS_PER_HOUR}/hour cap. It fails closed rather than risk a restart storm (an unbounded restart loop is what the cap exists to bound). Re-run once GitHub's search API responds, or inspect manually: \`flyctl machine list -a ${FLY_APP}\`, \`flyctl logs -a ${FLY_APP}\`. Runbook § *Out-of-band availability watchdog*."
        transition_kind="disarmed"
        ;;
      disarmed:no_egress)
        heal_note="⚠️ **No restart attempted — INCONCLUSIVE.** The app probe got no answer, but a control probe of a known-good endpoint (\`$(redact_url "$CONTROL_URL")\`) ALSO failed from this runner. A runner-side network/DNS/proxy failure looks exactly like an app outage, so the watchdog refuses to take the disruptive action (a restart) on an untrustworthy verdict. If the app is genuinely down, the alerting half of this monitor still fired and a human should check: \`flyctl machine list -a ${FLY_APP}\`. Runbook § *Out-of-band availability watchdog*."
        transition_kind="disarmed"
        # This is a HUMAN-ESCALATION page, so it is throttled like the cap: the
        # condition can persist for hours and paging every run would be the
        # notification-spam class this monitor exists to kill. cap_notified_ts
        # doubles as the "last human escalation" stamp (the two states are
        # mutually exclusive within a run) and is written BEFORE the page.
        if [ $((now - STATE_CAP_NOTIFIED_TS)) -ge $((CAP_RENOTIFY_MINUTES * 60)) ]; then
          STATE_CAP_NOTIFIED_TS="$now"
          if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "$heal_note")"; then
            fail "could not record the inconclusive notification in #${issue} — NOT paging now (refusing to re-page every run because a write failed)"
            exit 1
          fi
          page_human "⚠️ INCONCLUSIVE — ${PROBE_HOST_LABEL} probe failing AND the runner-side control probe failed; the watchdog cannot tell an app outage from its own network. NO restart. Incident #${issue}."
        else
          warn "inconclusive (runner egress) — already escalated $(( (now - STATE_CAP_NOTIFIED_TS) / 60 )) min ago (re-notify every ${CAP_RENOTIFY_MINUTES} min)"
        fi
        ;;
    esac
  fi

  [ -n "$stale_note" ] && heal_note="${stale_note}

${heal_note}"
  # ── comment policy ───────────────────────────────────────────────────────
  # Decide WHAT to publish, then WRITE THE STATE FIRST and publish after: the
  # throttle stamp lives in the body, so a comment posted before the stamp is
  # recorded would repeat on every later run — the same
  # notification-without-a-durable-record bug the restart path guards against.
  local do_comment=0
  if [ "$progress" = "new" ]; then
    : # the freshly created issue body is the first record; nothing to add
  elif [ "$transition_kind" = "restart" ] || [ "$transition_kind" = "heal_failed" ] || [ "$transition_kind" = "cap" ]; then
    do_comment=1
    STATE_LAST_COMMENT_TS="$now"
  elif [ "$STATE_LAST_COMMENT_TS" -gt 0 ] \
       && [ $((now - STATE_LAST_COMMENT_TS)) -lt $((COMMENT_THROTTLE_MINUTES * 60)) ]; then
    log "run #${STATE_DOWN_RUNS}: comment throttled (last comment $(( (now - STATE_LAST_COMMENT_TS) / 60 )) min ago; throttle ${COMMENT_THROTTLE_MINUTES} min)"
  else
    do_comment=1
    STATE_LAST_COMMENT_TS="$now"
    comment_body="🔁 **Still ${kind}** — probe run #${STATE_DOWN_RUNS} failed at $(fmt_iso "$now") (HTTP ${PROBE_CODE}); down since $(fmt_iso "$STATE_FIRST_FAILURE_TS") (~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min).

${heal_note}"
  fi

  # ── sustained-incident escalation (#3887) ────────────────────────────────
  # The decision is computed HERE, before the routine body write, so the attempt
  # marker rides the SAME PATCH (one extra PATCH only on the outcome) and the
  # body a reader sees already reflects this run's escalation state.
  esc_decision="$(decide_escalation "$now")"
  # The same-run guard. It reads a CONFIRMED delivery (page_human sets the flag
  # only after telegram_send returned 0), so a page that FAILED cannot suppress
  # the escalation. Without it a DOWN incident whose restart just paged would
  # page again seconds later for the same event.
  if [ "$HUMAN_PAGED_THIS_RUN" = "1" ]; then
    case "$esc_decision" in
      page|remind)
        log "escalation outcome: suppressed — a human page was already CONFIRMED delivered in this run"
        esc_decision="suppressed_same_run" ;;
    esac
  fi
  log "escalation outcome: ${esc_decision}"
  esc_pending=0
  case "$esc_decision" in
    page|remind)
      # WRITE-THEN-ACT: the attempt marker is durable BEFORE the send, so a
      # crash cannot leave an undelivered page unrecorded. `pending` is read as
      # "outcome unknown => retry", so the marker itself can never mute the leg.
      STATE_ESCALATE_TS="$now"
      STATE_ESCALATE_STATE="pending"
      # NOTE: `page_ok_ts` is DELIBERATELY NOT stamped here. It records a
      # CONFIRMED delivery, and this is only an ATTEMPT — stamping it optimistically
      # would let an undelivered page silence the retry for a whole
      # CAP_RENOTIFY_MINUTES window (fail-OPEN). It is stamped below, on the
      # confirmed-success branch and for a confirmed page_human delivery.
      esc_pending=1 ;;
  esac
  # A human page CONFIRMED delivered this run (a restart / failed restart / cap /
  # inconclusive page) is recorded HERE, so the cross-mechanism bound reads
  # delivery rather than an attempt.
  if [ "$HUMAN_PAGED_THIS_RUN" = "1" ]; then STATE_PAGE_OK_TS="$now"; fi

  if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "$heal_note")"; then
    fail "state write to #${issue} failed — NOT publishing a comment without a durable throttle record; the incident body is the cooldown/cap memory and is now STALE (this run's verdict is still ${PROBE_VERDICT})"
    exit 1
  fi
  if [ "$do_comment" = "1" ]; then
    comment_issue "$issue" "$comment_body" || warn "comment on #${issue} failed (non-fatal: the body already carries the state)"
  fi

  # The send happens AFTER the durable attempt marker. A failure does NOT stamp
  # a delivery, is recorded durably, is retried on the NEXT run (a failed page
  # delivers nothing, so retrying is not a page storm — and silence is the one
  # outcome requirement 5 forbids), and fails this run naming the channel.
  if [ "$esc_pending" = "1" ]; then
    # Report the SAME window the gate used (the escalation anchor), NOT the
    # restart clock: after a stale-clock reset first_failure_ts is `now`, so a
    # page for a 3-hour incident that said "for ~0 min" would contradict the very
    # fix that let it fire. An UNTRUSTWORTHY anchor carries NO age: the `0`
    # sentinel means "no unforgeable start" (created_at was unusable) and a
    # future stamp cannot be true — reporting either would publish a
    # 1970-derived "for ~28 million min" / "since 1970". Both are clamped to
    # `now`, the same treatment the gate gives the future stamp.
    esc_anchor="${STATE_ESCALATE_ANCHOR_TS:-$STATE_FIRST_FAILURE_TS}"
    if [ "$esc_anchor" -le 0 ] || [ "$esc_anchor" -gt "$now" ]; then esc_anchor="$now"; fi
    esc_age_min=$(( (now - esc_anchor) / 60 ))
    # The page must not assert a DIAGNOSIS the probe cannot support (#3887
    # review). On the INCONCLUSIVE (runner-egress) path the incident's own heal
    # note says the watchdog cannot tell an app outage from its own network, so
    # a page claiming "<host> has been DOWN" would state as fact what this run
    # has explicitly refused to decide. Branch the subject on the actual case.
    if [ "$restart_mode" = "disarmed:no_egress" ]; then
      esc_subject="${PROBE_HOST_LABEL} probe has been FAILING — INCONCLUSIVE (the runner-side control probe ALSO failed, so an app outage and a runner network failure look identical)"
      esc_why="A human must check: the watchdog cannot decide this from here, and nothing else will escalate it."
    else
      esc_subject="${PROBE_HOST_LABEL} has been ${kind}"
      esc_why="This class is one self-healing cannot close, so nothing else will escalate it."
    fi
    if [ "$esc_decision" = "remind" ]; then
      esc_text="🔁 STILL RUNNING for ~${esc_age_min} min (reminder) — ${esc_subject} since $(fmt_iso "$esc_anchor"); ${STATE_DOWN_RUNS} failing probe run(s), HTTP ${PROBE_CODE}. Nothing has closed it. Incident #${issue}: https://github.com/${REPO}/issues/${issue}"
    else
      esc_text="📟 HUMAN NEEDED — ${esc_subject} for ~${esc_age_min} min; ${STATE_DOWN_RUNS} failing probe run(s), HTTP ${PROBE_CODE}. ${esc_why} Incident #${issue}: https://github.com/${REPO}/issues/${issue}"
    fi
    if page_required "$esc_text"; then
      STATE_ESCALATE_STATE="sent"
      STATE_PAGE_OK_TS="$now"
      if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "$heal_note")"; then
        warn "the escalation WAS delivered but recording its outcome in #${issue} failed — the next run re-sends (at-least-once); the human HAS been reached"
      fi
      note "sustained-incident escalation delivered for #${issue} (${kind}, ${STATE_DOWN_RUNS} failing run(s), ~${esc_age_min} min)"
    else
      STATE_ESCALATE_STATE="failed"
      STATE_ESCALATE_TS="0"
      if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "$heal_note")"; then
        warn "the escalation send FAILED and recording that in #${issue} also failed — the run still fails and the next run retries the page"
      fi
      fail "⛔ sustained-incident escalation REQUIRED and NOT DELIVERED for #${issue} (${kind}, ${STATE_DOWN_RUNS} failing probe run(s)) — the alert channel is broken, so this is NOT an all-clear. Check that TELEGRAM_BOT_TOKEN is set and valid, that ESCALATION_CHAT_ID (default: the TELEGRAM_CHAT_ID ops chat) names a chat the bot can post to, and Telegram's status. Retried on the next run."
    fi
  fi

  fail "verdict ${PROBE_VERDICT} (HTTP ${PROBE_CODE}) — failing this run so GitHub's own notifications fire; incident: #${issue}"
  exit 1
}

if [ "${WATCHDOG_LIB_ONLY:-0}" != "1" ]; then
  main "$@"
fi
