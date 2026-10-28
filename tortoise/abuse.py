"""Abuse detection + enforcement for the hosted platform (#308).

Durable substrate (migration 0015): ``abuse_events`` rows + ``teams.
suspended_at``/``flagged_at`` + ``api_keys`` INSERT trigger (the only seam
that sees BOTH dashboard mints and the signup ``provision_org`` RPC).

Rules (env-overridable thresholds):
- R1  point_create: SUM(weight) > 500 / 1h   -> stage-1 flag, stage-2 REVIEW ALERT
- R2  key_create:   count    > 10  / 24h     -> stage-1 flag, stage-2 REVIEW ALERT
                     (stage 2 asks a human to decide; it does NOT suspend —
                      #5425 owner ruling: rate-limits, then talk to them)
- R3  reads:        > 100 / 5min per-key OR per-org -> ops alert (Telegram-only, #3639)
- R4  geo:          first unseen CF-IPCountry per org -> ops alert (Telegram-only)
- R8 signup_velocity: N anon signups/IP/window (breach >= threshold) ->
                     notify ops only (Telegram; never suspends)

Two-stage staging with EPISODE semantics (scoping delta 13 + code-review
fixes): flags are PER-RULE (flag event rows carry the rule). Stage 2 raises
the operator review alert only when (a) the rule's flag is a full window old,
AND (b) the rule has at least one event between the flag and the current
window's start — evidence the breach actually persisted across the boundary.
Episodes END on a clean evaluation (window back under threshold → flag_clear
event) or on un-suspend (the RPC clears all episodes) — so a burst after a
quiet period or after recovery is a NEW episode: it re-flags and can never
escalate on its first evaluation. Rules are independent: an R1 flag never
escalates a first R2 breach.

Suspension signal set (scoping delta 14): the process-wide set is a
CACHE-INVALIDATION SIGNAL, never a rejection authority — membership forces a
fresh resolution; the durable ``teams.suspended_at`` is the sole ground for
403/-32006; entries clear when a fresh resolution returns NULL (un-suspend
self-heals on the next request). ⚠️ **#5425: this mechanism is currently
INERT in production** — the automatic suspension that used to populate it is
deleted, so nothing calls ``mark_suspended`` and membership is never true.
What enforces a suspension is the durable read alone. See the delta-14 note
above ``_SUSPENDED_SIGNAL`` for the full statement.

Everything here is best-effort on the request path: recording/evaluation
failures are logged and swallowed — abuse telemetry must never break the
write path. Kill-switch: ``TORTOISE_ABUSE_DISABLED=1``.

The failure handling is NOT uniform (#4872): the five swallow sites on the
enforcement DECISION/ACTION path (``window_sum``, ``clean_window_episode_end``,
``latest_flag_at``, ``rule_event_between``, ``flag_org``) report
to the OPERATOR — an ERROR record plus a platform-scoped operator incident,
because a failed evaluation is otherwise indistinguishable from "the engine
decided not to enforce". The remaining
telemetry / notification / durability swallows stay debug-only;
``DECISION_FAULT_LANES`` declares the boundary and why. (#5425 deleted the
``suspend_org`` lane with the suspension it guarded: the map has FIVE keys, and
the only remaining enforcement ACTION lane is ``flag_org``.)

Notification volume (#3631): an R1/R2 flag or breach alert is emitted ONLY
after the corresponding store write persists — a store read/write failure
REDUCES notification volume, never increases it (the 2026-09-14 storm was a
swallowed ``flag_org`` write failure that notified on every evaluation, 401
alerts in 3h). The ALERT is also bounded to once per ``(org, rule)`` per
STAGE per staging window in-process, so neither a read that raises nor one that
returns a stale ``None`` (replica lag) can re-notify per evaluation (the stage
distinction is what lets the stage-2 breach alert still escalate a stage-1
flag alert). The claim is released when the engine observes the episode end (a
clean window), so a genuinely NEW episode alerts again. The durable store
remains authoritative for staging; the in-process map bounds ONE process, so
the honest ceiling across replicas is ``N replicas × 1`` per window — a global
cap needs shared state (Redis/DB) and is deliberately out of scope here.

The persist-before-notify rule does NOT apply to the stage-2 breach alert the
way it did to the flag alert: #5425 deleted the suspend WRITE that used to gate
it, so what bounds the breach alert is ``_claim_notify`` alone (once per
``(org, rule, stage)`` per window, in-process). Its durable trace — the
``review_needed`` event row — is written best-effort INSIDE ``_notify``, after
the decision, and a failure there is debug-only. Stated because the old
sentence claimed a guarantee its artifact no longer provides.
(The notify-only rules R3/R4/R8 have no flag to persist.)
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from tortoise.incident_kinds import (
    KIND_ABUSE_DECISION_FAULT,
    KIND_ABUSE_ENFORCEMENT_FAULT,
    KIND_ABUSE_REVIEW_NEEDED,
)

logger = logging.getLogger(__name__)

EVENT_POINT_CREATE = "point_create"
EVENT_KEY_CREATE = "key_create"
EVENT_AUTH_IP = "auth_ip"
EVENT_FLAG = "flag"
EVENT_FLAG_CLEAR = "flag_clear"  # episode end (clean eval or un-suspend)
EVENT_SUSPEND = "suspend"
EVENT_UNSUSPEND = "unsuspend"
#: #5425 owner ruling (2026-10-08): a PERSISTENT breach raises a human-review
#: alert. It is deliberately NOT ``EVENT_SUSPEND`` — the engine no longer
#: suspends, so recording a suspend event (or alerting "auto-suspended") would
#: record something that did not happen, which is the exact class this ruling
#: deleted (#4872).
EVENT_REVIEW = "review_needed"
EVENT_READ_VELOCITY = "read_velocity"
EVENT_SIGNUP_VELOCITY = "signup_velocity"
EVENT_RECOVERY_VELOCITY = "recovery_velocity"

ALERT_TYPES = (EVENT_FLAG, EVENT_REVIEW, EVENT_SUSPEND, EVENT_AUTH_IP,
               EVENT_READ_VELOCITY, EVENT_SIGNUP_VELOCITY,
               EVENT_RECOVERY_VELOCITY)

# ── #10156: two REGISTERS, not one list ────────────────────────────────────
#
# The dashboard rendered every entry in the list above under one heading —
# "Security alerts" / "Suspicious activity detected on this Organization.
# Revoke any key you don't recognize." — including ``read_velocity``, which is
# notify-only by design (R3 never suspends: nothing is disabled, throttled or
# locked). A month-old metric blip was therefore presented as an incident, with
# a destructive remedy for an event the system never acted on.
#
# The register is decided by THE FACT, never by how alarming the type sounds:
#
#   * ``REGISTER_ALERT``    — something HAPPENED: the account's state changed,
#                            a human acted, or an authenticated credential
#                            event (a sign-in from a new country) occurred.
#   * ``REGISTER_ACTIVITY`` — a detector OBSERVED a rate. It measured; it
#                            changed nothing. Informational, muted, no verb.
#
# A type missing from this map defaults to ACTIVITY (see ``_alert_dict``): an
# unknown signal must never be presented as an incident, because incident
# framing is the claim that something happened.
REGISTER_ALERT = "alert"
REGISTER_ACTIVITY = "activity"
ALERT_REGISTER_OF = {
    EVENT_SUSPEND: REGISTER_ALERT,        # the org's state changed
    EVENT_FLAG: REGISTER_ALERT,           # the org was marked (flagged_at)
    # NOT "a human is reviewing it" (an earlier comment said so, and it was
    # wrong): the ENGINE writes this when a rule wants an operator to look, and
    # no human has acted yet. It is still an incident rather than an observation
    # — it is the stage-2 escalation of a flag (a recorded state change and a
    # request for a human), not a rate we merely measured.
    EVENT_REVIEW: REGISTER_ALERT,
    EVENT_AUTH_IP: REGISTER_ALERT,        # a credential event: new-country auth
    EVENT_READ_VELOCITY: REGISTER_ACTIVITY,
    EVENT_SIGNUP_VELOCITY: REGISTER_ACTIVITY,
    EVENT_RECOVERY_VELOCITY: REGISTER_ACTIVITY,
}

#: How long a detection BOUT may stay silent before the next row starts a new
#: episode (#10156). The row writer fires at most once per detector window
#: (``ReadVelocityTracker``: 5 min), so without a gap the dashboard renders one
#: row per window: the live dataset holds 13 ``read_velocity`` rows for ONE
#: continuous bout, and the customer read them as 13 separate alerts.
#:
#: 3h is measured against that dataset, not picked: the 2026-09-25/26 bout's
#: LONGEST internal silence is 2h09m (21:44:04Z → 23:53:55Z), so any threshold
#: at or below that splits the very bout this issue is about, and 3h is the
#: smallest round value above it. Read the other way: the bout stays open until
#: the key has been quiet for 36 consecutive windows — longer than any plausible
#: pause inside one agent session — so re-opening after it is a genuinely new
#: bout, not a window boundary.
EPISODE_GAP_S = 3 * 3600

#: Rows scanned (newest first) to build the episode feed. 500 is the number the
#: #10156 dataset needs by a wide margin (the busiest org holds 4 428 ``flag``
#: rows in total, spread over ten days of BOUTS) and it stays well inside
#: PostgREST's default page. The bound is honest rather than invisible: when the
#: scan is saturated the OLDEST episode is marked ``truncated`` and the feed
#: renders its count as ``×N+``, because a count from a cut window is a floor,
#: not a count.
_ALERT_SCAN = 500

#: How many ACKNOWLEDGEMENT rows are read per org (#10156). Deliberately larger
#: than a feed page and small enough to be a single bounded read: an ack that
#: falls outside this window reappears as a CURRENT row, so the bound must sit
#: far above any plausible number of dismissals (one per episode, not per row).
_ACK_SCAN = 2000

#: The event type an ACKNOWLEDGEMENT is persisted as (#10156). See
#: ``record_alert_ack`` on both stores for why an ``abuse_events`` row rather
#: than a new table: the alert history already lives there, ``abuse_cleanup``
#: already applies retention to it, and no rule reads it (every rule counts a
#: narrow ``window_sum`` over ONE event_type), so an ack can neither inflate a
#: rule nor render as an alert (``ALERT_TYPES`` does not contain it).
EVENT_ALERT_ACK = "alert_ack"

#: The rules that can produce a stage-1 FLAG episode (#308 R1/R2). Declared
#: once because it is read by the code that ENDS an episode (``flag_clear`` /
#: ``unsuspend_org`` walk it) and by the code that REPORTS one (#4874's
#: ``active_flag_episodes``): a rule added to ``_evaluate`` without being added
#: here would flag the org while nothing ever ended its episode, leaving the
#: org-level chip outliving its reason.
#:
#: ⚠️ There is a SECOND copy of this set that Python cannot reach: the durable
#: un-suspend is the plpgsql function ``abuse_unsuspend``, whose body writes the
#: ``flag_clear`` rows itself. So a rule added HERE must also be added THERE, or
#: ``SupabaseAbuseStore.unsuspend_org`` leaves that rule's episode open forever
#: while the memory store ends it — the exact divergence this tuple exists to
#: prevent.
#:
#: THERE is the NEWEST migration defining ``abuse_unsuspend``, not one named
#: file: the function is already re-established once (``0015_abuse_events.sql``
#: for teams → ``20260915000001_tenancy_team_to_org.sql`` for orgs) and supabase
#: applies migrations in filename order, so a later ``CREATE OR REPLACE``
#: replaces an earlier one. Add a FORWARD migration that redefines it — never
#: edit a migration already on ``main`` (the append-only gate rejects that, and
#: a guard pinned to a shipped file would then red while pointing at an
#: un-editable artifact). The coupling is guarded by
#: ``tests/test_abuse.py::test_durable_unsuspend_clears_every_flagging_rule``,
#: which resolves the newest definition and fails when the two lists drift in
#: either direction (a rule added to this tuple, or one dropped by a forward
#: migration).
FLAGGING_RULES = (EVENT_POINT_CREATE, EVENT_KEY_CREATE)

#: How many rows the durable store reads PER flagging rule when folding the
#: active episodes (#4874). Only flag / flag_clear rows carry a ``rule`` — the
#: ``point_create`` breach rows written by ``record_point_create`` and the
#: ``api_keys`` trigger's ``key_create`` rows leave it NULL — so a rule-scoped
#: read returns episodes, not traffic: 50 rows is up to 25 episodes of that
#: rule. Bounded on purpose (an operator diagnostic must not become an
#: unbounded scan), and truncation is safe here because the read is ordered
#: newest-first and the fold only ever needs the NEWEST flag/clear per rule.
_EPISODE_READ_LIMIT = 50


def _int_env(name: str, default: int) -> int:
    try:
        v = os.environ.get(name)
        return int(v) if v and v.strip().isdigit() else default
    except (TypeError, ValueError):
        return default


#: #5493 review P2-3: window misconfigurations already warned about, keyed
#: ``(name, raw_env_value)``. These readers sit on request hot paths — the
#: unauthenticated signup limiter re-reads its window 2-3x per request — so an
#: unlatched warning lets a misconfig be driven to arbitrary log volume by
#: traffic. The operator needs the FIRST occurrence of each distinct
#: misconfiguration, not one line per read (mirrors ``_WIDTH_MISMATCH_WARNED``
#: in ``embeddings.py`` and ``_DEV_PEPPER_WARNED`` in ``auth.py``).
_WINDOW_WARNED: set[tuple[str, str | None]] = set()
_WINDOW_WARN_LOCK = threading.Lock()


def _negative_env(raw: str | None) -> bool:
    """True when ``raw`` parses as a negative int.

    ``_int_env`` gates on ``isdigit()``, which rejects ``"-1"`` — it therefore
    returns the knob's DEFAULT, and before #5493's review it did so SILENTLY,
    leaving the operator-facing promise (a non-positive window is reported)
    false for the negative half. This is the test that makes it true. A value
    that does not parse at all is not negative and keeps its existing
    (unreported) default.
    """
    if raw is None:
        return False
    try:
        return int(raw) < 0
    except (TypeError, ValueError):
        return False


def _window_env(name: str, default: int) -> int:
    """Env-tunable rate-limit WINDOW, floored so a non-positive value can
    never fail OPEN (#5493).

    A window is the one limiter knob whose non-positive value *disables*
    protection instead of tightening it — and it does so in one of two
    shapes, depending on where the window is applied:

    * **Hosted bucket limiter** (``_check_ip_bucket_rate_limit``): the bucket
      is pruned (``now - t < window_s``) and THEN compared to the limit. A
      non-positive window empties it on every request, ``len(bucket) >=
      limit`` is never reached, and every request is allowed, FOREVER — the
      fail-open this floor exists to remove.
    * **In-process velocity trackers** (``ReadVelocityTracker``,
      ``SignupVelocityTracker``, ``RecoveryVelocityTracker``): the prune runs
      BEFORE the append and the comparison runs AFTER it, so a non-positive
      window never prunes and the bucket collapses to this request's sample.
      ``len(bucket)`` is then ``1``, which clears neither
      ``ReadVelocityTracker``'s strict ``> threshold`` nor the signup/recovery
      ``>= threshold`` against the configured thresholds (>1): the breach
      signal NEVER FIRES — silently disabled — and the notify-dedup test
      ``now - last < window_s`` is never true, so the once-per-window dedup is
      defeated too. (Not an allow-everything fail-open here; a lose-the-signal
      one.)

    Either way ``0`` is a second, undocumented off-switch — the intended one
    is ``RATE_LIMIT_DISABLED=1`` — so a non-positive window falls back to the
    knob's DEFAULT (always at least one second). The precedent for an
    out-of-range env value degrading to its default is the ``max(1,
    _int_env(...))`` guard on the capture/dream/scorecard executors
    (``hosted_api.py:210``, and the same pattern at 223/1589/1599). A warning
    names the variable, the raw value, and the value actually used, and is
    emitted ONCE per distinct misconfiguration (see ``_WINDOW_WARNED``) —
    never once per read.

    Do NOT "simplify" this back to ``_int_env``. ``_int_env`` is shared with
    the THRESHOLD/limit knobs, where a non-positive value is a legitimate
    fail-CLOSED deny-all (``len(bucket) >= 0`` is always true) — the floor
    belongs to the window knobs alone, so it lives here and not in
    ``_int_env``.

    ``_int_env`` treats a NEGATIVE value as unset (its ``isdigit()`` gate) and
    yields ``default`` — so a negative never arrives as a negative. The RAW
    value is therefore inspected too: ``"0"`` (and ``"00"``) reaches the
    warning path as a non-positive ``value``, while a negative is detected by
    ``_negative_env``. BOTH warn through the same latch, and neither changes
    the value returned — a non-positive window always yields the knob's
    default.
    """
    raw = os.environ.get(name)
    value = _int_env(name, default)
    if value > 0 and not _negative_env(raw):
        return value
    fallback = default if default > 0 else 1
    with _WINDOW_WARN_LOCK:
        first = (name, raw) not in _WINDOW_WARNED
        _WINDOW_WARNED.add((name, raw))
    if first:
        logger.warning(
            "%s=%r is not a valid rate-limit window (must be a positive number "
            "of seconds); using %d so the limiter cannot fail open (#5493)",
            name, raw, fallback)
    return fallback


def _id_set_env(name: str) -> set[str]:
    """A comma-separated id allowlist from the environment (#10156).

    Used for the first-party/test exemptions on the read-velocity tracker.
    Whitespace is stripped and empty entries are dropped, so a trailing comma
    or a multi-line value cannot silently exempt the empty key id (which would
    exempt every org-scope read through the per-key scope's ``if key_id`` arm).
    Read once, at tracker construction, like every other abuse knob — an ops
    change needs the same restart a threshold change does.
    """
    raw = os.environ.get(name) or ""
    return {part.strip() for part in raw.split(",") if part.strip()}


def abuse_disabled() -> bool:
    return os.environ.get("TORTOISE_ABUSE_DISABLED") == "1"


def appeal_url() -> str:
    return os.environ.get(
        "TORTOISE_ABUSE_APPEAL_URL",
        "https://tortoise.premiselabs.co/docs.html#appeal",
    )


def suspended_message() -> str:
    return (
        "This organization has been suspended due to unusual activity. "
        f"Appeal: {appeal_url()}"
    )


# ── #4872: the enforcement decision path's failure lanes ────────────────────
#
#: The DECLARED inventory of failure lanes on the abuse enforcement decision
#: path (#4872): swallow site -> ``(incident kind, fallback)``. ``fallback`` is
#: what the handler DID when the store call failed, i.e. the enforcement
#: CONSEQUENCE — and it is NOT recoverable from the lane name, which is why the
#: incident carries it. After #5425 no lane suspends: the terminal action is an
#: operator REVIEW ALERT, so "toward/away from suspension" is read as "toward/
#: away from the escalation". TWO lanes fail TOWARD it — ``rule_event_between``
#: (``continuity_true``) and ``clean_window_episode_end`` — the latter because a
#: failed guard read OR clear write leaves the flag episode ARMED, so the stale
#: anchor survives and a later over-threshold evaluation still escalates to the
#: review alert. The away-from-it lanes are ``window_sum``, ``latest_flag_at``
#: (``reflag``) and the one enforcement ACTION lane, ``flag_org``. This map is
#: the single source of truth
#: for both values, so the token and the code cannot drift as a pair, and it is
#: pinned against the source by ``tests/test_abuse.py``: every ``except`` handler
#: inside ``_evaluate``/``_flag`` must call ``report_abuse_decision_fault`` (an
#: ADDED uninstrumented swallow is RED), and the emitted tokens must equal this
#: map's keys exactly once each (a renamed token is RED).
#:
#: ``clean_window_episode_end`` names the clean-window episode-END LEG (its
#: guard read OR its clear write), NOT the ``latest_flag_at`` method: H2 and H3
#: both involve ``latest_flag_at`` and take opposite enforcement directions, so
#: the lane is the leg.
#:
#: DELIBERATELY EXCLUDED (declared so the boundary is not re-discovered next
#: incident): ``record_event`` in :meth:`AbuseEngine.record_point_create` (a
#: recording-leg drop — folding it into ``UNMETERED_INCREMENT`` would break
#: ``tests/test_metering_window_admission.py::test_mcp_abuse_failure_is_not_
#: reported_as_a_dropped_increment``), ``_notify`` (the notification leg — after
#: #4778 it reaches the un-gated ``_incident_alert_store`` seam and is NOT
#: sweep-gated; the residual is only "no ``DR_ISSUES_PAT``, or an unusable
#: object store"), ``MemoryAbuseStore._durable``, and the
#: R3/R8/geo trackers — all different defect classes from "the enforcement
#: decision could not complete".
DECISION_FAULT_LANES: dict[str, tuple[str, str]] = {
    "window_sum": (KIND_ABUSE_DECISION_FAULT, "return_none"),
    "clean_window_episode_end": (KIND_ABUSE_DECISION_FAULT, "return_none"),
    "latest_flag_at": (KIND_ABUSE_DECISION_FAULT, "reflag"),
    "rule_event_between": (KIND_ABUSE_DECISION_FAULT, "continuity_true"),
    "flag_org": (KIND_ABUSE_ENFORCEMENT_FAULT, "return_flag"),
}

#: Defensive default only: every lane is declared above, so this is unreachable
#: in practice and the source-scan fence REDs if an undeclared lane is emitted.
#: It is NOT a third real kind — an undeclared lane must not silently disappear,
#: so it reports as the decision kind with ``fallback="unknown"``.
_UNKNOWN_LANE_KIND = KIND_ABUSE_DECISION_FAULT

#: Traceback-suppression window (seconds) for the fault ERROR record. The
#: COMPACT record still fires on EVERY occurrence — it is the residual for a
#: deployment with no alert channel — but the ``exc_info`` stack is emitted at
#: most once per lane per window. The fault cause is shared substrate, so a
#: substrate outage makes every decision-path store call raise, and
#: ``record_point_create`` evaluates TWICE per write (R1 + R2): without this the
#: debug-to-ERROR elevation would emit two multi-KB stacks per write request for
#: the whole outage, i.e. a log-amplification path on the request path.
_FAULT_TRACEBACK_WINDOW_S = 60.0
_fault_traceback_at: dict[str, float] = {}
_fault_traceback_lock = threading.Lock()


def _fault_traceback_due(lane: str, now: float | None = None) -> bool:
    """True at most once per ``lane`` per ``_FAULT_TRACEBACK_WINDOW_S``."""
    now = time.monotonic() if now is None else now
    with _fault_traceback_lock:
        last = _fault_traceback_at.get(lane)
        if last is not None and now - last < _FAULT_TRACEBACK_WINDOW_S:
            return False
        _fault_traceback_at[lane] = now
        return True


def report_abuse_decision_fault(lane: str, org_id: str,
                                rule: str | None = None,
                                error: BaseException | None = None) -> None:
    """Operator alert: a store call on the abuse DECISION path FAILED (#4872).

    Mirrors ``metering.report_unmetered_increment``: the failure is absorbed by
    us (the request is served and the decision path keeps its documented
    fallback) and announced to the OPERATOR — never silently, and never to the
    user. Without this, a broken enforcement path is indistinguishable from
    "the engine decided not to enforce": ``_evaluate`` returns ``"breach"``
    when a store call fails, exactly as it does while still inside the staging
    window — the same value for "could not evaluate" and "evaluated, still
    staging" — and the exception that would name the cause (``supabase_control.rpc``
    carries the PostgREST ``message``) is what the old ``logger.debug`` threw
    away. (#5425 removed the ``suspend_org`` lane and with it the sharpest
    example — a failed suspend that silently skipped the enforcement — but the
    ambiguity is a property of the return vocabulary, not of that one lane.)

    The alert is PLATFORM-SCOPED (one shared substrate cause, one incident) and
    the local ERROR record is the durable diagnosis, carrying the resolved
    ``kind`` and the PostgREST message. For a deployment with no alert channel
    configured the ERROR record is the residual (the ``UNMETERED_INCREMENT``
    contract), and it fires on EVERY occurrence; only the ``exc_info`` stack is
    bounded to one per lane per ``_FAULT_TRACEBACK_WINDOW_S`` (see
    ``_fault_traceback_due``).

    Never raises: the alert must not become a new failure path on an enforcement
    path (a signal that can raise is a bypass by another name). The retained
    state is NOT repaired by this call — this makes the fault VISIBLE; arming
    or repairing the suspend is a separate, owner-held decision.
    """
    with contextlib.suppress(Exception):  # the alert must never raise
        kind, fallback = DECISION_FAULT_LANES.get(lane, (_UNKNOWN_LANE_KIND, "unknown"))
        logger.error(
            "ABUSE FAULT (#4872): kind=%s lane=%s org=%s rule=%s "
            "error=%s: %s — the abuse enforcement decision could not complete; "
            "the retained state is the handler's fallback (%s), which may point "
            "TOWARD or AWAY from a suspension",
            kind, lane, org_id or "<none>", rule or "<none>",
            type(error).__name__, error, fallback,
            exc_info=error if _fault_traceback_due(lane) else None,
        )
        from tortoise.operator_alert import alert_abuse_fault

        alert_abuse_fault(kind, lane, org_id, error, rule=rule, fallback=fallback)


# ── Suspended signal set (delta 14) ─────────────────────────────────────────
#
# ⚠️ #5425 (2026-10-08): the ENGINE no longer has a caller for
# `mark_suspended`. It used to be marked as a side effect of the automatic
# stage-2 suspension, which the owner ruling DELETED — so in production this
# set is now populated by nothing, `is_suspended_signal()` is always False,
# and the consumers that used membership as a cache-invalidation fast path
# (`hosted_api` org deletion, `mcp_auth` membership + self-heal) are inert.
#
# The contract they back — "membership forces a FRESH resolution" — is
# therefore VACUOUS as written. That is stated here rather than deleted,
# because retiring a security-adjacent fast path is a SEPARATE decision from
# retiring the automatic suspension; do not read the delta-14 line above as
# describing a live mechanism.
#
# What actually enforces a suspension now is the DURABLE
# `organizations.suspended_at` read (the 403) — unaffected by #5425: an
# out-of-band `abuse_suspend` RPC still takes effect, at the cost of up to the
# MCP membership-cache TTL (≤ 60 s) of staleness in THIS process.
# `mark_suspended` is retained as the seam an operator-initiated suspension
# would use to signal this process; nothing in-repo calls it, and it cannot
# signal a foreign process.
_SUSPENDED_SIGNAL: set[str] = set()
_SIGNAL_LOCK = threading.Lock()


def mark_suspended(org_id: str) -> None:
    if org_id:
        with _SIGNAL_LOCK:
            _SUSPENDED_SIGNAL.add(org_id)


def clear_suspended(org_id: str) -> None:
    with _SIGNAL_LOCK:
        _SUSPENDED_SIGNAL.discard(org_id)


def is_suspended_signal(org_id: str) -> bool:
    with _SIGNAL_LOCK:
        return org_id in _SUSPENDED_SIGNAL


def _utcnow(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)  # noqa: UP017


def _ensure_aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)  # noqa: UP017


def _parse_ts(value) -> datetime | None:
    """Parse an ISO timestamp (PostgREST 'Z' or offset form) → aware dt."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _ensure_aware(parsed)


def _org_email(store, org_id: str) -> str | None:
    """Best-effort owner email. Retained for the caller shape only — abuse
    alerts are Telegram-only since #3639, so ``notify_abuse`` no longer reads
    the ``email`` key (removing the now-dead call sites is a #3646 follow-up).
    """
    try:
        return store.org_email(org_id) if org_id else None
    except Exception:
        return None


# ── Active-flag read (#4874) ───────────────────────────────────────────────

def fold_flag_episodes(rows) -> list[dict]:
    """Fold ``abuse_events`` rows into the ACTIVE per-rule flag episodes.

    This is the READ-side definition of "an episode is active", and it is
    deliberately the same rule ``latest_flag_at`` applies on the enforcement
    path: the newest ``flag`` row for a rule, unless a ``flag_clear`` for that
    rule lands at or after it (``>=`` — a clear written in the same instant
    ends the episode). The two implementations are kept separate on purpose
    (``latest_flag_at`` is a DECISION lane and rewriting it for a diagnostic is
    not worth the risk), so the agreement is PINNED BY TEST instead —
    ``tests/test_abuse.py::TestActiveFlagEpisodes::test_fold_agrees_with_latest_flag_at``.

    Only facts RECORDED at flag time are returned: the rule, the count that
    breached, the threshold it breached, the staging window, and when the
    episode started. Nothing is re-derived — a flag row predating the
    ``details`` payload reports ``None`` for count/threshold/window_s rather
    than a recomputed guess (the window it originally breached is not
    recoverable later, and a fabricated number here would be worse than none).

    ``rows`` may come from the in-memory store (``created_at`` is a datetime)
    or PostgREST (an ISO string); both normalise through ``_parse_ts``. Rows
    without a ``rule`` (breach traffic, geo, velocity — and the ``api_keys``
    trigger's ``key_create`` rows) are ignored: only flag/flag_clear rows
    carry one.
    """
    newest_flag: dict[str, tuple[datetime, dict]] = {}
    newest_clear: dict[str, datetime] = {}
    for row in rows or ():
        rule = row.get("rule")
        ts = _parse_ts(row.get("created_at"))
        if not rule or ts is None:
            continue
        event_type = row.get("event_type")
        if event_type == EVENT_FLAG:
            prev = newest_flag.get(rule)
            if prev is None or ts >= prev[0]:
                newest_flag[rule] = (ts, row)
        elif event_type == EVENT_FLAG_CLEAR:
            prev_clear = newest_clear.get(rule)
            if prev_clear is None or ts >= prev_clear:
                newest_clear[rule] = ts
    episodes: list[dict] = []
    for rule, (flagged_at, row) in newest_flag.items():
        cleared = newest_clear.get(rule)
        if cleared is not None and cleared >= flagged_at:
            continue  # episode ended by a clean window or an un-suspend
        details = row.get("details")
        if not isinstance(details, dict):
            details = {}
        episodes.append({
            "rule": rule,
            "since": flagged_at.isoformat(),
            "count": details.get("count"),
            "threshold": details.get("threshold"),
            "window_s": details.get("window_s"),
        })
    episodes.sort(key=lambda e: e["since"], reverse=True)
    return episodes


# ── Stores ──────────────────────────────────────────────────────────────────

class MemoryAbuseStore:
    """In-memory store: registry/selfhost mode + tests.

    Optional ``registry_write`` callback makes enforcement durable in
    selfhost: ``registry_write(org_id, suspended_at, flagged_at)`` writes
    the Org node props (scoping delta 4). Deploy-reset semantics apply to
    the in-memory rows themselves (documented degradation).

    Optional ``registry_read`` callback is its READ half (#4874 review P2):
    ``registry_read(org_id) -> {"found", "flagged_at", "suspended_at"}``
    returns the SAME registry props ``GET /v1/team`` renders, so
    ``org_state`` / ``org_flagged_at`` / ``org_suspended`` answer with the
    same values the customer chip does even after a restart. Without it (a
    bare in-process store) those reads fall back to the in-process dicts and
    ``org_state`` reports ``found: None`` — it genuinely cannot tell whether
    an org exists.
    """

    def __init__(self, registry_write=None, registry_read=None):
        self.rows: list[dict] = []
        self.flags: dict[str, datetime] = {}
        self.suspended: dict[str, datetime] = {}
        self.org_emails: dict[str, str | None] = {}
        self._registry_write = registry_write
        self._registry_read = registry_read
        self._lock = threading.Lock()

    def _append(self, org_id: str, event_type: str, *, weight: int = 1,
                key_id: str | None = None, country: str | None = None,
                rule: str | None = None, details: dict | None = None,
                created_at: datetime | None = None) -> None:
        with self._lock:
            self.rows.append({
                "org_id": org_id, "event_type": event_type,
                "weight": int(weight), "key_id": key_id, "country": country,
                "rule": rule, "details": details or {},
                "created_at": _ensure_aware(_utcnow(created_at)),
            })

    def record_event(self, org_id: str, event_type: str, *, weight: int = 1,
                     key_id: str | None = None, country: str | None = None,
                     rule: str | None = None, details: dict | None = None,
                     created_at: datetime | None = None) -> None:
        self._append(org_id, event_type, weight=weight, key_id=key_id,
                     country=country, rule=rule, details=details,
                     created_at=created_at)

    def window_sum(self, org_id: str, event_type: str, window_s: int,
                   now: datetime | None = None) -> int:
        now = _ensure_aware(_utcnow(now))
        cutoff = now - timedelta(seconds=window_s)
        with self._lock:
            return sum(
                int(r.get("weight") or 1) for r in self.rows
                if r["org_id"] == org_id and r["event_type"] == event_type
                and r["created_at"] > cutoff
            )

    def latest_flag_at(self, org_id: str, rule: str) -> datetime | None:
        """ACTIVE per-rule flag-episode anchor: the newest flag row, or None
        when a flag_clear (clean evaluation or un-suspend) ended the
        episode after it."""
        with self._lock:
            flags = [r["created_at"] for r in self.rows
                     if r["org_id"] == org_id
                     and r["event_type"] == EVENT_FLAG
                     and r.get("rule") == rule]
            clears = [r["created_at"] for r in self.rows
                      if r["org_id"] == org_id
                      and r["event_type"] == EVENT_FLAG_CLEAR
                      and r.get("rule") == rule]
        if not flags:
            return None
        newest_flag = max(flags)
        if clears and max(clears) >= newest_flag:
            return None  # episode ended
        return newest_flag

    def flag_clear(self, org_id: str, rule: str,
                   now: datetime | None = None) -> None:
        """End a flag episode (clean evaluation or recovery)."""
        now = _ensure_aware(_utcnow(now))
        self._append(org_id, EVENT_FLAG_CLEAR, rule=rule, created_at=now)
        # teams.flagged_at is the org-level chip: clear only when NO rule
        # still has an active episode.
        other_active = any(
            self.latest_flag_at(org_id, r) is not None
            for r in FLAGGING_RULES if r != rule)
        if not other_active:
            self.flags.pop(org_id, None)
            self._durable(org_id, "flagged_at", None)

    def rule_event_between(self, org_id: str, rule: str, after: datetime,
                          before: datetime) -> bool:
        """Continuity evidence: any rule event in (after, before]."""
        after, before = _ensure_aware(after), _ensure_aware(before)
        with self._lock:
            return any(r["org_id"] == org_id and r["event_type"] == rule
                       and after < r["created_at"] <= before
                       for r in self.rows)

    def org_state(self, org_id: str) -> dict:
        """The org's enforcement state from the DURABLE source (#4874).

        Returns ``{"found": bool | None, "flagged_at": datetime | None,
        "suspended": bool}``. With a ``registry_read`` wired this is the
        registry Team node's ``flagged_at`` / ``suspended_at`` — byte for byte
        the props ``GET /v1/team`` renders (and the 403 suspension refusal
        keys on), so the operator answer cannot contradict the customer chip
        even after a restart. Without one, the in-process dicts are the only
        thing this store has, and ``found`` is ``None``: it cannot say whether
        the org exists.
        """
        if self._registry_read is not None:
            state = self._registry_read(org_id) or {}
            return {
                "found": state.get("found"),
                "flagged_at": _parse_ts(state.get("flagged_at")),
                "suspended": _parse_ts(state.get("suspended_at")) is not None,
            }
        return {"found": None,
                "flagged_at": self.flags.get(org_id),
                "suspended": org_id in self.suspended}

    def org_flagged_at(self, org_id: str) -> datetime | None:
        return self.org_state(org_id)["flagged_at"]

    def active_flag_episodes(self, org_id: str) -> list[dict]:
        """The org's ACTIVE per-rule flag episodes (#4874).

        NOT an enforcement read — nothing gates on it. It exists so a human
        can answer "why does this org read ``flagged``, and how far over"
        without querying ``abuse_events`` by hand: the org-level chip carries
        a timestamp and nothing else.

        Registry/selfhost degradation, stated because it is invisible from the
        return value: these EVENT ROWS are in-process, so after a restart this
        reports no episodes — while ``org_state`` (which reads the durable
        registry props) still says flagged. ``flags`` and ``status`` therefore
        degrade differently, and the difference is exactly ``flags_error``:
        an empty ``flags`` next to ``status: "flagged"`` means "the reason is
        not readable from here", never "there is no reason".
        """
        with self._lock:
            rows = [dict(r) for r in self.rows if r["org_id"] == org_id]
        return fold_flag_episodes(rows)

    def flag_org(self, org_id: str, rule: str,
                  details: dict | None = None,
                  now: datetime | None = None) -> None:
        now = _ensure_aware(_utcnow(now))
        self.flags[org_id] = now
        self._append(org_id, EVENT_FLAG, rule=rule, details={
            **(details or {}), "rule": rule}, created_at=now)
        self._durable(org_id, "flagged_at", now.isoformat())



    def suspend_org(self, org_id: str, details: dict | None = None,
                     now: datetime | None = None) -> None:
        now = _ensure_aware(_utcnow(now))
        self.suspended[org_id] = now
        self._append(org_id, EVENT_SUSPEND,
                     rule=(details or {}).get("rule"),
                     details=details or {}, created_at=now)
        self._durable(org_id, "suspended_at", now.isoformat())

    def unsuspend_org(self, org_id: str, now: datetime | None = None) -> None:
        now = _ensure_aware(_utcnow(now))
        self.suspended.pop(org_id, None)
        self.flags.pop(org_id, None)
        self._append(org_id, EVENT_UNSUSPEND, created_at=now)
        # end every flag episode — a recovered org starts clean, so its
        # first post-recovery burst re-flags instead of escalating to review
        for rule in FLAGGING_RULES:
            self._append(org_id, EVENT_FLAG_CLEAR, rule=rule, created_at=now)
        self._durable(org_id, "suspended_at", None)
        self._durable(org_id, "flagged_at", None)

    def org_suspended(self, org_id: str) -> bool:
        return self.org_state(org_id)["suspended"]

    def org_email(self, org_id: str) -> str | None:
        return self.org_emails.get(org_id)

    def seen_countries(self, org_id: str) -> set[str]:
        with self._lock:
            return {r["country"] for r in self.rows
                    if r["org_id"] == org_id
                    and r["event_type"] == EVENT_AUTH_IP and r["country"]}

    def alert_scan_saturated(self, org_id: str) -> bool:
        """Was the last ``recent_alerts`` scan cut by its own budget? (#10156)

        `truncated` rides on a ROW, so a scan that its own filters emptied has no
        row to carry it: the newest ``_ALERT_SCAN`` rows can all be non-alert (a
        burst of dismissals, a day of ``flag_clear``), leaving a legitimately
        EMPTY feed while older alerts sit just below the window. Rendering
        nothing there is a silent "nothing happened" — the failure mode this
        whole change exists to remove (round-8 review finding) — so the route
        reports it and the panel says so.
        """
        with self._lock:
            return sum(1 for r in self.rows
                       if r["org_id"] == org_id
                       and r["event_type"] in ALERT_TYPES) > _ALERT_SCAN

    def recent_alerts(self, org_id: str, limit: int = 20,
                      include_identity: bool = False) -> list[dict]:
        with self._lock:
            alerts_only = [r for r in self.rows
                           if r["org_id"] == org_id
                           and r["event_type"] in ALERT_TYPES]
            # Saturation is measured BEFORE the slice, on the population the
            # slice cuts — a count taken after it is always exactly
            # ``_ALERT_SCAN`` and could never say "there may be more".
            scanned = len(alerts_only)
            # ONE EXTRA row, so "the scan returned exactly the budget" is
            # distinguishable from "there is more": `>` then means what it says
            # on both stores (review finding — the Supabase store used `>=` and
            # would call a complete 500-row set truncated).
            rows = sorted(alerts_only, key=lambda r: r["created_at"],
                          reverse=True)[:_ALERT_SCAN + 1]
            acks = _acks_from_rows(self.rows, org_id)
        return _alert_feed(rows[:_ALERT_SCAN], acks=acks, limit=limit,
                           scan_saturated=scanned > _ALERT_SCAN,
                           include_identity=include_identity)

    def alert_acks(self, org_id: str) -> list[dict]:
        with self._lock:
            return _acks_from_rows(self.rows, org_id)

    def record_alert_ack(self, org_id: str, episode: str,
                         ack_of: dict | None = None,
                         by: str | None = None,
                         now: datetime | None = None) -> None:
        self.record_event(org_id, EVENT_ALERT_ACK,
                          details=_ack_details(
                              episode, self._ack_of(org_id, episode, ack_of),
                              by=by),
                          created_at=now)

    def _ack_of(self, org_id: str, episode: str,
                ack_of: dict | None) -> dict:
        """The episode to record an acknowledgement FOR, or raise.

        Raising on an unresolvable episode is deliberate (review nit): an
        acknowledgement with no window is written as a KEY-ONLY row, which is
        exactly the shape that cannot survive the scan sliding — the failure this
        whole mechanism exists to prevent. The route already 404s an unknown
        episode before it gets here; this makes the store fail LOUD rather than
        quietly persist something that will evaporate.
        """
        resolved = ack_of or self._resolve_episode(org_id, episode)
        # A plain `isinstance(dict)` check was not enough (review finding P3):
        # `recent_alerts()`'s DEFAULT return is a dict WITHOUT `_identity`, and
        # writing it produces an ack that can only match by the exact key — the
        # evaporating shape. Every field the match needs is required here.
        if not isinstance(resolved, dict) or not resolved.get("_identity") \
                or not resolved.get("first_at") or not resolved.get("at"):
            raise ValueError(
                f"cannot acknowledge {episode!r}: no resolvable episode with a "
                f"window in {org_id} (a key-only acknowledgement would evaporate)")
        return resolved

    def _resolve_episode(self, org_id: str, episode: str) -> dict | None:
        """The episode as this store currently derives it, or None."""
        return next((a for a in self.recent_alerts(
            org_id, limit=_ALERT_SCAN, include_identity=True)
            if a.get("episode") == episode), None)

    def _durable(self, org_id: str, field: str, value) -> None:
        """Field-scoped write-through to the registry Org node when a
        callback is wired (selfhost durability, scoping delta 4). Writing
        ONLY the changed field means a concurrent flag/suspend can never
        clobber the other prop (code-review P2). Best-effort."""
        if self._registry_write is None:
            return
        try:
            self._registry_write(org_id, field, value)
        except Exception:
            logger.debug("abuse registry write-through failed for %s", org_id)


class SupabaseAbuseStore:
    """Supabase-backed durable store (migration 0015)."""

    def __init__(self, cp):
        self._cp = cp

    def record_event(self, org_id: str, event_type: str, *, weight: int = 1,
                     key_id: str | None = None, country: str | None = None,
                     rule: str | None = None, details: dict | None = None,
                     created_at: datetime | None = None) -> None:
        body = {"org_id": org_id, "event_type": event_type,
                "weight": int(weight)}
        if key_id is not None:
            body["key_id"] = key_id
        if country is not None:
            body["country"] = country
        if rule is not None:
            body["rule"] = rule
        if details:
            body["details"] = details
        self._cp.query("abuse_events", method="POST", json_body=body)

    def window_sum(self, org_id: str, event_type: str, window_s: int,
                   now: datetime | None = None) -> int:
        cutoff = (_ensure_aware(_utcnow(now))
                  - timedelta(seconds=window_s)).isoformat()
        rows = self._cp.query(
            "abuse_events", select=["weight"],
            filters=[("org_id", "eq", org_id),
                     ("event_type", "eq", event_type),
                     ("created_at", "gt", cutoff)],
        )
        return sum(int(r.get("weight") or 1) for r in rows)

    def latest_flag_at(self, org_id: str, rule: str) -> datetime | None:
        """ACTIVE per-rule flag-episode anchor (None when a flag_clear ended
        the episode after the newest flag)."""
        rows = self._cp.query(
            "abuse_events", select=["created_at"],
            filters=[("org_id", "eq", org_id),
                     ("event_type", "eq", EVENT_FLAG),
                     ("rule", "eq", rule)],
            order="created_at.desc", limit=1,
        )
        if not rows:
            return None
        newest_flag = _parse_ts(rows[0].get("created_at"))
        clears = self._cp.query(
            "abuse_events", select=["created_at"],
            filters=[("org_id", "eq", org_id),
                     ("event_type", "eq", EVENT_FLAG_CLEAR),
                     ("rule", "eq", rule)],
            order="created_at.desc", limit=1,
        )
        if clears:
            newest_clear = _parse_ts(clears[0].get("created_at"))
            if newest_clear is not None and newest_flag is not None \
                    and newest_clear >= newest_flag:
                return None  # episode ended
        return newest_flag

    def flag_clear(self, org_id: str, rule: str,
                   now: datetime | None = None) -> None:
        """End a flag episode; clear the org-level chip only when no other
        rule keeps an active episode."""
        self.record_event(org_id, EVENT_FLAG_CLEAR, rule=rule)
        other_active = any(
            self.latest_flag_at(org_id, r) is not None
            for r in FLAGGING_RULES if r != rule)
        if not other_active:
            self.clear_flag(org_id)

    def rule_event_between(self, org_id: str, rule: str, after: datetime,
                          before: datetime) -> bool:
        rows = self._cp.query(
            "abuse_events", select=["created_at"],
            filters=[("org_id", "eq", org_id),
                     ("event_type", "eq", rule),
                     ("created_at", "gt", _ensure_aware(after).isoformat()),
                     ("created_at", "lte", _ensure_aware(before).isoformat())],
            limit=1,
        )
        return bool(rows)

    def _org_field(self, org_id: str, field: str):
        rows = self._cp.query("organizations", select=[field],
                              filters=[("id", "eq", org_id)])
        return rows[0].get(field) if rows else None

    def org_state(self, org_id: str) -> dict:
        """The org's enforcement state in ONE read (#4874).

        ``{"found", "flagged_at", "suspended"}`` from the ``organizations``
        row — the same columns ``GET /v1/team`` renders, so the operator read
        and the customer chip share a source. ``found: False`` means no such
        org, which a mistyped ``org_id`` must be able to say out loud rather
        than render as a clean one.
        """
        rows = self._cp.query(
            "organizations", select=["id", "flagged_at", "suspended_at"],
            filters=[("id", "eq", org_id)])
        if not rows:
            return {"found": False, "flagged_at": None, "suspended": False}
        row = rows[0]
        return {"found": True,
                "flagged_at": _parse_ts(row.get("flagged_at")),
                "suspended": row.get("suspended_at") is not None}

    def org_flagged_at(self, org_id: str) -> datetime | None:
        return self.org_state(org_id)["flagged_at"]

    def active_flag_episodes(self, org_id: str) -> list[dict]:
        """The org's ACTIVE per-rule flag episodes (#4874) from ``abuse_events``.

        One read per flagging rule (the query dialect has no ``in`` operator —
        see ``supabase_control``), each ordered newest-first and bounded by
        ``_EPISODE_READ_LIMIT``. A read failure RAISES: the caller must be able
        to tell "no active episode" from "could not read", which is the whole
        point of the surface — see ``GET /v1/internal/abuse/state``.
        """
        rows: list[dict] = []
        for rule in FLAGGING_RULES:
            rows.extend(self._cp.query(
                "abuse_events",
                select=["event_type", "rule", "created_at", "details"],
                filters=[("org_id", "eq", org_id), ("rule", "eq", rule)],
                order="created_at.desc", limit=_EPISODE_READ_LIMIT,
            ))
        return fold_flag_episodes(rows)

    def flag_org(self, org_id: str, rule: str,
                  details: dict | None = None,
                  now: datetime | None = None) -> None:
        self._cp.query(
            "organizations", method="PATCH", filters=[("id", "eq", org_id)],
            json_body={"flagged_at": _ensure_aware(_utcnow(now)).isoformat()},
        )
        self.record_event(org_id, EVENT_FLAG, rule=rule,
                          details={**(details or {}), "rule": rule})

    def clear_flag(self, org_id: str) -> None:
        self._cp.query("organizations", method="PATCH",
                       filters=[("id", "eq", org_id)],
                       json_body={"flagged_at": None})

    def suspend_org(self, org_id: str, details: dict | None = None,
                     now: datetime | None = None) -> None:
        # The RPC sets suspended_at (DB-side now()) AND records the suspend
        # event atomically; ``now`` accepted for store-protocol parity.
        self._cp.rpc("abuse_suspend", {"p_org_id": org_id})

    def unsuspend_org(self, org_id: str, now: datetime | None = None) -> None:
        self._cp.rpc("abuse_unsuspend", {"p_org_id": org_id})

    def org_suspended(self, org_id: str) -> bool:
        return self.org_state(org_id)["suspended"]

    def org_email(self, org_id: str) -> str | None:
        # #1765 demotion re-point: prefer the owner's USER email (teams.email
        # is no longer synced by claim), fall back to the contact field.
        from tortoise.supabase_control import owner_email
        try:
            owner = owner_email(self._cp, org_id)
        except Exception:
            owner = None
        return owner or self._org_field(org_id, "email")

    def seen_countries(self, org_id: str) -> set[str]:
        rows = self._cp.query(
            "abuse_events", select=["country"],
            filters=[("org_id", "eq", org_id),
                     ("event_type", "eq", EVENT_AUTH_IP)],
        )
        return {r["country"] for r in rows if r.get("country")}

    def alert_scan_saturated(self, org_id: str) -> bool:
        """Is this Organization's alert scan at its budget right now? (#10156)

        `truncated` rides on a ROW, so a scan that its own filters emptied has no
        row to carry it: the newest ``_ALERT_SCAN`` rows can all be non-alert (a
        burst of dismissals, a day of ``flag_clear``), leaving a legitimately
        EMPTY feed while older alerts sit just below the window. Rendering
        nothing there is a silent "nothing happened" — the failure mode this
        whole change exists to remove (round-8 review finding) — so the route
        reports it and the panel says so.

        Deliberately NOT phrased as "was the previous ``recent_alerts`` scan
        cut": this issues its own independent query (the memory store computes
        the count inside the same lock as its slice, so the two stores answer a
        slightly different question), and rows can arrive between the two reads
        (round-23 review finding — the old docstring claimed an identity the two
        stores do not share).

        FAIL-SOFT IN THE SAFE DIRECTION: a probe failure returns True
        ("saturated / unknown"), never False. False is the positive claim "this
        feed is complete", which is exactly the false negative the flag exists to
        remove — and a ``debug`` log would hide it twice over.
        """
        try:
            rows = self._cp.query(
                "abuse_events", select=["event_type"],
                filters=[("org_id", "eq", org_id),
                         ("event_type", "neq", EVENT_POINT_CREATE),
                         ("event_type", "neq", EVENT_KEY_CREATE),
                         ("event_type", "neq", EVENT_ALERT_ACK)],
                order="created_at.desc", limit=_ALERT_SCAN + 1,
            )
        except Exception:
            # Unknown, and unknown must not read as "complete".
            logger.warning("alert saturation probe failed (%s); reporting "
                           "saturated=unknown", org_id)
            return True
        return len(rows) > _ALERT_SCAN

    def recent_alerts(self, org_id: str, limit: int = 20,
                      include_identity: bool = False) -> list[dict]:
        rows = self._cp.query(
            "abuse_events",
            select=["event_type", "created_at", "country", "key_id",
                    "details"],
            # #10156: exclude the high-volume NON-alert types at the server.
            # Without this the scan below is a window over ALL events, and a
            # busy org's ordinary traffic starves it: the live project holds
            # 6 450 ``point_create`` + 67 ``key_create`` rows against 16
            # ``read_velocity`` and 4 428 ``flag`` rows, so a 500-row newest
            # window can contain no alert at all and the panel would read
            # "nothing ever happened" — the exact false negative the ageing /
            # "show older" affordances below exist to prevent. Two ``neq``
            # filters AND together (they are negations, so composition is
            # correct where two ``eq`` filters on one column would be
            # unsatisfiable). The remaining non-alert types are low-volume
            # (``flag_clear`` / ``unsuspend``), and the client-side
            # ``ALERT_TYPES`` filter still decides what may RENDER — this only
            # protects the window's budget, so a future noisy non-alert type
            # costs scan depth, never correctness.
            filters=[("org_id", "eq", org_id),
                     ("event_type", "neq", EVENT_POINT_CREATE),
                     ("event_type", "neq", EVENT_KEY_CREATE),
                     # …and THIS change's own write (round-8 review finding):
                     # an `alert_ack` row can never render, so letting dismissals
                     # consume the scan budget means a customer who acknowledges
                     # a lot pushes their real alerts out of the window. Three
                     # `neq` filters on one column AND together correctly (they
                     # are negations, unlike `eq`s which would be unsatisfiable).
                     ("event_type", "neq", EVENT_ALERT_ACK)],
            order="created_at.desc", limit=_ALERT_SCAN + 1,
        )
        # #10156 review finding: saturation must be measured on the ROW SCAN,
        # i.e. BEFORE the alert-type filter. Computing it after the filter (as
        # this did) is true only when ≥500 *alert* rows came back from a
        # 500-row mixed scan, which is the rare case — so in production a cut
        # bout was reported as an exact count with its older half silently
        # missing, and no `+`. Both stores now answer the same QUESTION ("did
        # the scan that produced these rows hit its budget?"), each with the
        # budget its own read actually has.
        scanned = len(rows)
        saturated = scanned > _ALERT_SCAN
        rows = rows[:_ALERT_SCAN]
        rows = [r for r in rows if r.get("event_type") in ALERT_TYPES]
        return _alert_feed(rows, acks=self.alert_acks(org_id), limit=limit,
                           scan_saturated=saturated,
                           include_identity=include_identity)

    def alert_acks(self, org_id: str) -> list[dict]:
        """The acknowledgements this org has recorded (#10156).

        Its own read, deliberately NOT a fold over ``recent_alerts``' scan: the
        scan is a 500-row newest-first WINDOW, so an ack written after 500
        later events would fall out of it and the acknowledgement would
        silently evaporate — the one failure mode #10156 says a dismissal must
        not have.

        FAIL-SOFT, in the direction that cannot hide an alert: a failed read
        returns the empty set, so every row renders as un-acknowledged and
        stays VISIBLE. Raising would be worse than useless here —
        ``GET /v1/team/alerts`` catches, so a throw would blank the whole feed
        and take the ``suspend`` row off the appeal surface with it (review
        finding: the docstring promised this and the code raised).
        """
        try:
            rows = self._cp.query(
                "abuse_events", select=["org_id", "event_type", "details"],
                filters=[("org_id", "eq", org_id),
                         ("event_type", "eq", EVENT_ALERT_ACK)],
                # Bounded and newest-first (review nit): the server already
                # filtered to this one event_type, so this read must not depend
                # on PostgREST's silent `max_rows` to stay finite. Newest first,
                # because if the bound ever bites it must bite on the OLDEST
                # acknowledgement — an ack that falls out of the read reappears
                # as a current row, which is the failure this whole mechanism
                # exists to prevent.
                order="created_at.desc", limit=_ACK_SCAN,
            )
        except Exception:
            logger.debug("alert ack read failed (%s)", org_id)
            return []
        return _acks_from_rows(rows, org_id)

    def record_alert_ack(self, org_id: str, episode: str,
                         ack_of: dict | None = None,
                         by: str | None = None,
                         now: datetime | None = None) -> None:
        """Persist the acknowledgement as an ``alert_ack`` row.

        ⚠️ ``now`` is accepted for signature parity with the in-memory store but
        is NOT transmitted on this path (``record_event`` deliberately does not
        send ``created_at``; the column default decides). Passing it does nothing
        rather than raising, because the value has no consumer — matching reads
        the stored WINDOW fields, never the ack row's own timestamp (review nit).
        """
        self.record_event(org_id, EVENT_ALERT_ACK,
                          details=_ack_details(
                              episode, self._ack_of(org_id, episode, ack_of),
                              by=by),
                          created_at=now)

    def _ack_of(self, org_id: str, episode: str,
                ack_of: dict | None) -> dict:
        """The episode to record an acknowledgement FOR, or raise.

        Raising on an unresolvable episode is deliberate (review nit): an
        acknowledgement with no window would be written as a KEY-ONLY row, which
        is exactly the shape that cannot survive the scan sliding — the failure
        this whole mechanism exists to prevent. The route 404s an unknown episode
        before reaching here; this makes the store fail LOUD rather than quietly
        persist something that will evaporate.
        """
        resolved = ack_of or self._resolve_episode(org_id, episode)
        # A plain `isinstance(dict)` check was not enough (review finding P3):
        # `recent_alerts()`'s DEFAULT return is a dict WITHOUT `_identity`, and
        # writing it produces an ack that can only match by the exact key — the
        # evaporating shape. Every field the match needs is required here.
        if not isinstance(resolved, dict) or not resolved.get("_identity") \
                or not resolved.get("first_at") or not resolved.get("at"):
            raise ValueError(
                f"cannot acknowledge {episode!r}: no resolvable episode with a "
                f"window in {org_id} (a key-only acknowledgement would evaporate)")
        return resolved

    def _resolve_episode(self, org_id: str, episode: str) -> dict | None:
        """The episode as this store currently derives it, or None."""
        return next((a for a in self.recent_alerts(
            org_id, limit=_ALERT_SCAN, include_identity=True)
            if a.get("episode") == episode), None)


def _read_velocity_message(details: dict) -> str:
    """#10156 — the copy for a NOTIFY-ONLY velocity observation.

    The defect this replaces: the panel's heading said "Suspicious activity
    detected on this Organization. Revoke any key you don't recognize." and
    every ``read_velocity`` row rendered under it. Two untrue claims and one
    destructive instruction — the detector is notify-only (R3 never suspends),
    so nothing happened to the account, and the blanket imperative tells the
    customer to destroy a working key in response to their own normal agent
    traffic. The measured population: 16 rows, none after 2026-09-26, and ZERO
    ``suspend`` rows in the table's entire history.

    The replacement states the FACT first (a read volume crossed a threshold),
    then the consequence (nothing was changed), and keeps the revoke route
    CONDITIONAL on the one case where it is right (a key you do not recognise).

    ⚠️ The row NAMES NO IDENTIFIER (review finding). An earlier version printed
    ``details["id"]`` — which is ``api_keys.id``, a 24-hex value — and said
    "revoke it in Keys". The Keys table renders the key's NAME plus
    ``key_prefix`` (``tt_xxxxxxxx``); the id appears nowhere a customer can see
    it, so that sentence handed the reader an identifier they could not match to
    any row and made the one action it offered unperformable — the same class of
    unactionable remedy this change exists to remove. The remedy now points at
    the list they CAN read ("review your keys in Keys") instead of promising a
    locatability we do not provide.
    """
    scope = details.get("scope")
    if scope == "key":
        return ("Read volume on one of this Organization's API keys crossed "
                "our unusual-activity threshold. This is informational; it "
                "did not change anything. If you don't recognise this activity, "
                "review your API keys in Keys — an unrecognised key should be "
                "revoked there.")
    # Round 12 (review finding): the total-scope branch stated the benign
    # READING as fact ("a retrieval-heavy agent routinely reads at this rate")
    # and offered no remedy. The row knows ONE fact — the total crossed a
    # threshold. It cannot know the reads came from the customer's own agents:
    # an org-wide spike from a leaked key is precisely what this detector cannot
    # exclude, so presenting the benign interpretation as fact would reassure a
    # customer whose key IS being abused. The reading is a CONDITION now, and
    # the remedy is conditional on it failing — the same shape the key branch
    # already uses. No revoke instruction: there is no single key to point at,
    # so the route offered is the list where an unrecognised key would appear.
    return ("Total read volume for this Organization crossed our "
            "unusual-activity threshold. This is informational; it did not "
            "change anything. Retrieval-heavy agents commonly read at this "
            "rate — if these reads don't come from yours, review your API keys "
            "in Keys.")


def _alert_dict(row: dict) -> dict:
    etype = row.get("event_type")
    details = row.get("details") or {}
    # A human LABEL per type. The dashboard renders "<label> — <message>" in the
    # session-authed Security-alerts list, and it previously rendered the raw
    # `event_type` there — so the customer read "recovery_velocity — Unusual
    # account-recovery…", "flag — …", i.e. our internal enum leaked as the
    # heading even after round 6 fixed the message half (round 7). The label is
    # part of the response, so a client that wants the machine token still has
    # `type`.
    labels = {
        EVENT_FLAG: "Suspicious activity",
        # Round 10 (review finding): "Activity under review" asserted an
        # ongoing human review in the SAME response whose message was just
        # rewritten to stop asserting one — two different claims about the same
        # fact, one of them false. Stage 2 is a MEASUREMENT (a flag a full
        # window old, with continuity), so the label names the measurement.
        EVENT_REVIEW: "Sustained high activity",
        EVENT_SUSPEND: "Organization suspended",
        EVENT_AUTH_IP: "New location",
        EVENT_READ_VELOCITY: "Unusual read volume",
        EVENT_SIGNUP_VELOCITY: "Unusual signup activity",
        EVENT_RECOVERY_VELOCITY: "Unusual account-recovery activity",
    }
    messages = {
        # No `details['rule']` token here either: `point_create` / `key_create`
        # are internal rule names, and the EVENT_REVIEW comment below states the
        # rule travels on the operator path only. The customer is told what
        # happened, not which rule fired.
        EVENT_FLAG: "Suspicious activity flagged on this Organization",
        # #5425: this dict is CUSTOMER-FACING. Its one consumer is the
        # session-authed, membership-gated ``GET /v1/team/alerts``, rendered
        # verbatim in the dashboard's "Security alerts" list — so the message
        # must read as something an org member can act on, NOT as an operator
        # instruction. The operator wording ("review and reach out", the rule,
        # the issue id) travels separately on the notify/incident path.
        #
        # Round 6: the promise "and will be in touch" was removed. Delivery of
        # the operator signal is BEST-EFFORT — the durable dashboard row is
        # written first, but the ops notification depends on channel config.
        # #4778 routed the tracked ops incident through the un-gated
        # `_incident_alert_store` seam, so it is not sweep-gated: it is absent
        # only when no alert channel can be built at all (no `DR_ISSUES_PAT`,
        # or an unusable object store). A customer-facing
        # string must not promise a human follow-up the plumbing cannot
        # guarantee; it states what this ALERT did — never the org's state — and
        # leaves the route where the customer can reach us. (Round 12 review
        # finding: this comment still quoted the WHOLE-STATE wording round 11
        # removed — "nothing was disabled" — so an editor reading the rationale
        # rather than the string was told the removed claim was current copy.)
        # Rounds 6, 9, 10 — the same promise three times, so the rule is now
        # stated as a PROPERTY and pinned as one: a customer-facing string in
        # this dict says NOTHING about what a human has done, is doing, or will
        # do. Round 6 deleted "and will be in touch"; round 9 replaced it with
        # "our team is reviewing it" (the same promise in the present tense);
        # round 10 replaced THAT with "an operator has been asked to look at it",
        # which is weaker but still a factual claim — and the paragraph above
        # says delivery of the operator signal is BEST-EFFORT, so in exactly the
        # configurations it names (no `DR_ISSUES_PAT`, no usable object store) no
        # operator was asked at all. A string may state what was MEASURED (a
        # rule threshold was crossed), what was NOT changed (nothing is
        # disabled), and how to reach us. A human's involvement is not one of
        # the facts this row can know.
        # Round 15: "This is informational" is the ACTIVITY register's own
        # self-description, and this row is REGISTER_ALERT (it re-opens an
        # acknowledgement, because a flag is a state change). Saying it here
        # blurred the one distinction this change exists to draw, so the phrase is
        # gone while the FACTS it carried stay: nothing was disabled, and here is
        # the route. The two registers now read as what they are — one reports a
        # rate it measured, the other reports something that happened.
        EVENT_REVIEW: ("Unusually high activity on this Organization. It did "
                       "not disable anything. If you need a higher limit, "
                       "contact support@premiselabs.co."),
        EVENT_SUSPEND: "Organization suspended by an operator",
        # Round 6 (reviewer C): `recovery_velocity` is in ALERT_TYPES and is
        # really returned by `recent_alerts`, so without a message here the
        # customer's Security-alerts list rendered the bare TOKEN —
        # "recovery_velocity — recovery_velocity". Pre-existing, but in the two
        # lists this change edits, and the same class it exists to remove
        # (customer-facing prose nobody reviewed as prose). Now pinned by
        # test_every_alert_type_has_a_customer_message.
        # Round 14 (review finding): this was the only one of the three RATE
        # detectors whose string carried no informational clause and no next
        # step — it read as an incident, the exact framing this issue removes.
        # It now uses the same shape as its siblings, and the shared shape is
        # what the test pins (every ACTIVITY message must say nothing changed),
        # rather than one type's phrase.
        EVENT_RECOVERY_VELOCITY: ("Unusual account-recovery activity on this "
                                  "Organization. This is informational; it did "
                                  "not change anything. If you don't recognise "
                                  "it, contact support@premiselabs.co."),
        # Round 14 (review finding, narrowed): the row DOES carry the country
        # (`recent_alerts` selects it, and `check_new_country` only fires with
        # one), so the earlier "unknown" reading came from a review harness that
        # passed details without the column. The fallback was still wrong: with a
        # NULL country the string claimed we do not know where the access came
        # from, which is a claim of ignorance rather than a fact. The clause is
        # omitted instead.
        EVENT_AUTH_IP: ("Access from a new location: " + str(row["country"])
                        if row.get("country")
                        else "Access from a new location"),
        # #10156: informational, never incident-grade. See
        # ``_read_velocity_message`` for the defect this replaces.
        EVENT_READ_VELOCITY: _read_velocity_message(details),
        # Round 13 (review finding). This is PRE-EXISTING copy that the property
        # test cannot see (it only asserts the ABSENCE of claims) and that the
        # `has a message` pin only checks the presence of — so it shipped
        # unreviewed while being rendered verbatim, in the same `messages` dict
        # this change owns. Three of the defects this PR exists to remove:
        #   * "breach" asserts a violation. `signup_velocity` is an ACTIVITY
        #     register row — a notify-only rate measurement, like the other two
        #     velocity detectors. Nothing was breached.
        #   * "velocity" / "anon signups" is our internal vocabulary, and the raw
        #     `'?'` fallbacks are the placeholder class `_read_velocity_message`
        #     already calls out: with empty details it rendered
        #     "Signup velocity breach: ? anon signups from ?".
        #   * it offered no route at all.
        # The remedy is NOT the key one: these are ANONYMOUS signups from one
        # source, so revoking the reader's own keys would address nothing. The
        # row can know that an org was minted and that its rate was unusual, so
        # the route is for someone who does not recognise the Organization.
        EVENT_SIGNUP_VELOCITY: ("Signup activity from one source crossed our "
                                "unusual-activity threshold. This is "
                                "informational; it did not change anything. If "
                                "you didn't expect this Organization to be "
                                "created, contact support@premiselabs.co."),
    }
    at = row.get("created_at")
    return {
        "type": etype,
        # Fall back to the token only for a type outside ALERT_TYPES (which
        # `recent_alerts` filters out, so unreachable from this seam).
        "label": labels.get(etype, etype),
        "at": at.isoformat() if isinstance(at, datetime) else at,
        "message": messages.get(etype, etype),
        # #10156: which register this belongs in. A type this map does not know
        # is treated as an OBSERVATION (the quiet register), because incident
        # framing asserts that something happened and an unknown type cannot
        # support that claim.
        "register": ALERT_REGISTER_OF.get(etype, REGISTER_ACTIVITY),
    }


# ── #10156: episodes and acknowledgements ──────────────────────────────────

def _episode_identity(alert: dict) -> tuple:
    """What makes two alerts the SAME ongoing episode.

    Type plus the subject the row is about, and nothing else: a ``flag``
    episode is per rule (that is how the engine's own ``flag_clear`` ends one),
    a new-country alert is per country, and a velocity observation is per
    (scope, id) — the key or org the threshold was crossed on. Two different
    keys crossing on the same day are two episodes, and joining them would
    mis-state both.
    """
    etype = alert.get("type")
    subject = alert.get("_subject") or ()
    return (etype, subject)


def _episode_key(alert: dict) -> str:
    """A STABLE, reason-bearing identifier for an episode (#10156).

    Identity + the episode's FIRST timestamp. The key is recomputed on every
    read, so anything derived from live state (the count, the last-seen time)
    would re-key an acknowledged episode the moment another window fires.

    ⚠️ ``first_at`` is NOT stable, and an earlier version of this docstring
    wrongly said it was: it is the oldest row THE SCAN CAN SEE, so it moves
    whenever the bounded newest-first window slides past the episode's start —
    at the live org's volume (~440 ``flag`` rows/day) within about a day.
    Matching acknowledgements by this key alone therefore lost them (P1, round-3
    review). The key stays because it is a good human-readable identifier and a
    good exact-match fast path; durability comes from ``_episode_acknowledged``,
    which matches on identity plus WINDOW OVERLAP. Do not reintroduce a
    key-only ack match.

    ⚠️ STATED TRADE-OFF, because it is the one path by which an acknowledgement
    can hide something: an acked bout that Grows inside its own gap chain (more
    windows firing, no silence longer than ``EPISODE_GAP_S``) overlaps the
    acknowledged window, so it stays acknowledged and stays behind "Show older". That is the
    requested semantics — one episode, one decision — and the alternative
    (re-keying on count) would break the ack the first time another window
    fired. The upper bound on the risk is the gap chain itself (3h of unbroken
    activity) plus, on the incident register, the fact that a suspension writes
    its OWN event with its own episode key. A future lane that wants an
    escalation to re-open an acked episode should add a threshold ("re-alert
    above N× the acknowledged count") rather than re-key on every window.
    """
    etype, subject = _episode_identity(alert)
    parts = [str(etype)] + [str(p) for p in (subject or ())]
    parts.append(str(alert.get("first_at") or alert.get("at") or ""))
    return "|".join(parts)


def _alert_subject(row: dict) -> tuple:
    """The per-type subject used for episode identity (see ``_episode_identity``)."""
    etype = row.get("event_type")
    details = row.get("details") or {}
    if etype in (EVENT_FLAG, EVENT_REVIEW):
        return (row.get("rule") or details.get("rule") or "",)
    if etype == EVENT_AUTH_IP:
        return (row.get("country") or "",)
    if etype in (EVENT_READ_VELOCITY, EVENT_SIGNUP_VELOCITY,
                 EVENT_RECOVERY_VELOCITY):
        return (details.get("scope") or "", details.get("id")
                or details.get("ip") or "")
    return ()


def _ack_details(episode: str, ack_of: dict | None = None,
                 by: str | None = None) -> dict:
    """The ``details`` written for an ``alert_ack`` row (#10156).

    Carries the episode KEY and the WINDOW it covers. The window is what makes
    the acknowledgement survive the episode being re-derived with a moved
    ``first_at`` (see ``_episode_acknowledged``); without it the row is matched
    only by a key that the 500-row scan is free to change.

    The window is derived from ``ack_of`` — the episode as WE resolved it — never
    taken as loose arguments from the caller. An earlier signature accepted
    ``identity`` / ``first_at`` / ``at`` separately and a caller that omitted them
    silently wrote a key-only ack, which is the failure this whole mechanism
    exists to prevent (found by writing the P1 regression test: the store-level
    call omitted them and the acknowledgement evaporated).
    """
    details: dict = {"episode": episode}
    if isinstance(ack_of, dict):
        ident = ack_of.get("_identity")
        if isinstance(ident, (list, tuple)) and ident:
            details["identity"] = [str(p) for p in ident]
        for field in ("first_at", "at"):
            value = ack_of.get(field)
            if value:
                details[field] = str(value)
    if by:
        details["by"] = by
    return details


def _flat_identity(etype, subject) -> tuple[str, ...]:
    """``(type, subject...)`` in the flat, JSON-safe form acks store."""
    return tuple([str(etype)] + [str(p) for p in (subject or ())])


def _acks_from_rows(rows, org_id: str) -> list[dict]:
    """The acknowledgements an org has recorded, from ``EVENT_ALERT_ACK`` rows.

    Each entry is ``{"episode", "identity", "first", "at"}`` — a KEY plus the
    WINDOW it was written for. The window is not decoration: it is what keeps an
    acknowledgement alive when the episode is re-derived (see
    ``_episode_acknowledged``).
    """
    out: list[dict] = []
    for row in rows or ():
        if row.get("org_id") != org_id or row.get("event_type") != EVENT_ALERT_ACK:
            continue
        details = row.get("details")
        if not isinstance(details, dict) or not details.get("episode"):
            continue
        ident = details.get("identity")
        out.append({
            "episode": str(details["episode"]),
            "identity": (tuple(str(p) for p in ident)
                         if isinstance(ident, (list, tuple)) and ident else None),
            "first": _parse_ts(details.get("first_at")),
            "at": _parse_ts(details.get("at")),
        })
    return out


def _episode_acknowledged(episode: dict, acks,
                          gap_s: float = EPISODE_GAP_S) -> bool:
    """Is this episode covered by one of the org's acknowledgements (#10156)?

    NOT ``episode["episode"] in acks``. That was the first implementation and it
    had a silent failure the issue explicitly forbids: the episode key is
    ``identity + first_at``, and ``first_at`` is the OLDEST ROW THE SCAN CAN SEE
    — so it MOVES whenever the bounded 500-row window slides past the episode's
    beginning. Reproduced before this fix: ack a 300-window bout, let 300 newer
    alert rows arrive, and the same still-firing bout comes back with a new key,
    ``acked: False`` — an acknowledged row reappearing as CURRENT with no way to
    dismiss it again.

    So an acknowledgement is matched on what actually identifies the episode —
    its identity, plus a window test — and then gated by ONE more rule that
    depends on which register the episode belongs to.

    The window arms (both directions pinned):

    * OVERLAP ⇒ same bout.
    * a gap of 0 … ``EPISODE_GAP_S`` between the acknowledged window's END and
      this episode's START ⇒ same bout, the acknowledged stretch simply slid out
      of the scan (forward only: an unbounded delta also matched every EARLIER
      bout, which is how acking the newest bout used to acknowledge older ones).
    * a gap LONGER than ``EPISODE_GAP_S`` ⇒ a genuinely later bout, not
      acknowledged, renders as current.

    ⛔ THE INCIDENT GATE — ``register == alert`` AND ``episode.at > ack.at`` ⇒ NOT
    acknowledged. This is the distinction between the two registers, applied to
    dismissals: for an OBSERVATION (a velocity row) a newer row is the SAME
    ongoing measurement re-reported every 5 minutes, so one acknowledgement
    covering all of it is the whole point of the change. For an INCIDENT each row
    is a new FACT — a suspension, a re-suspension after an appeal, a second
    new-country sign-in — and folding it into the dismissed episode hides it. The
    reproduced failure: suspend at T, acknowledge it, appeal, get re-suspended at
    T+2h (inside ``EPISODE_GAP_S``, so grouping folds it into the same episode);
    the panel then printed "Everything shown here is acknowledged." while the org
    was suspended right now (review finding P1). The dismissal covers what the
    operator saw; anything that HAPPENS afterwards re-opens the episode.

    ⚠️ THE HORIZON, stated because it is bounded and the bound is not obvious:
    all of this is decided from a 500-row newest-first SCAN. A dense burst rolls
    that window in about a day at the live org's measured ~440 ``flag`` rows/day,
    and once the acknowledged stretch AND its continuation have both scrolled
    out, nothing in the data distinguishes "the same bout, still firing" from "a
    new bout of the same identity" — the rows that would prove it are outside the
    window. The choice here is to let the acknowledgement LAPSE: the row returns
    as CURRENT, visible and dismissable again. The other direction (letting an old
    acknowledgement cover whatever turns up next) would silently hide a genuinely
    new bout behind a week-old dismissal, which is the worse failure for a
    security panel. Both directions are pinned by test, so a future change has to
    argue with the one it is not making.
    """
    key = episode.get("episode")
    # `_identity` is set by `group_alert_episodes` and is still present here (the
    # `_`-field strip runs later, on the way out). Recomputing it from `_subject`
    # would read `()` — that field is already popped — and the identity check
    # would then never match, which is why this must read the stored form.
    flat = episode.get("_identity")
    if flat is None:
        flat = list(_flat_identity(*_episode_identity(episode)))
    e_first = _parse_ts(episode.get("first_at"))
    e_last = _parse_ts(episode.get("at"))
    incident = episode.get("register") == REGISTER_ALERT
    for ack in acks or ():
        matched = ack.get("episode") == key
        if not matched:
            ident = ack.get("identity")
            if ident is None or tuple(ident) != tuple(flat):
                continue
            first, last = ack.get("first"), ack.get("at")
            if None in (first, last, e_first, e_last):
                continue
            overlap = first <= e_last and e_first <= last
            delta = (e_first - last).total_seconds()
            matched = overlap or (0 <= delta <= gap_s)
        if not matched:
            continue
        if incident:
            acked_through = ack.get("at")
            if acked_through is None or e_last is None or e_last > acked_through:
                # Something HAPPENED after the dismissal. Not covered.
                continue
        return True
    return False


def group_alert_episodes(alerts: list[dict],
                         gap_s: int = EPISODE_GAP_S) -> list[dict]:
    """Fold a NEWEST-FIRST alert list into episodes (#10156).

    One row per sustained bout, not per detector window: the newest alert of a
    bout carries `count` (how many windows fired) and `first_at` (when the bout
    began), so the panel can say "×13, 12:19 → 19:08" instead of printing the
    same sentence thirteen times. Consecutive alerts of the same identity join
    the open episode while the silence between them is at most ``gap_s``; a
    longer silence starts a new one.

    The folded episode keeps the NEWEST row's label/message/at (so the existing
    consumers and their pins keep working) and adds `count`, `first_at`,
    `episode`. Nothing is deleted: the historical rows stay, this is a view.
    """
    episodes: list[dict] = []
    open_by_ident: dict[tuple, dict] = {}
    #: The OLDEST timestamp already folded into the open episode for an
    #: identity. The gap is measured between CONSECUTIVE firing windows, not
    #: from the episode's newest row: the 2026-09-25/26 bout runs 17:19 → 00:08,
    #: so measuring from its newest row would split it at every row more than
    #: ``gap_s`` older than 00:08 — the exact fragmentation #10156 removes.
    open_last: dict[tuple, object] = {}
    for alert in alerts:
        ts = _parse_ts(alert.get("at"))
        ident = _episode_identity(alert)
        episode = open_by_ident.get(ident)
        if episode is not None:
            previous = open_last.get(ident)
            if ts is not None and previous is not None \
                    and (previous - ts).total_seconds() <= gap_s:
                episode["count"] += 1
                episode["first_at"] = alert.get("at")
                open_last[ident] = ts
                continue
        episode = dict(alert)
        episode["count"] = 1
        episode["first_at"] = alert.get("at")
        open_by_ident[ident] = episode
        open_last[ident] = ts
        episodes.append(episode)
    for episode in episodes:
        episode["episode"] = _episode_key(episode)
        # The identity is an INTERNAL grouping field; it must not reach the API
        # response (a client would see a private convention it cannot rely on).
        # It is kept under `_identity` for the ONE caller that legitimately
        # needs it — the acknowledgement route, which must record the window it
        # is acknowledging without trusting the client to supply one — and
        # `_alert_feed` strips every `_`-prefixed field by default.
        episode["_identity"] = list(_flat_identity(*_episode_identity(episode)))
        episode.pop("_subject", None)
    return episodes


def _alert_feed(rows, *, acks, limit: int,
                scan_saturated: bool = False,
                include_identity: bool = False) -> list[dict]:
    """Rows (newest first) → the episode feed both stores return (#10156)."""
    alerts = []
    for row in rows:
        alert = _alert_dict(row)
        alert["_subject"] = _alert_subject(row)
        alerts.append(alert)
    episodes = group_alert_episodes(alerts)
    for episode in episodes:
        episode["acked"] = _episode_acknowledged(episode, acks)
    # The scan is newest-first, so only the OLDEST episode can have been cut by
    # it. Saying so is the difference between "×100" and "×100+": a count
    # measured on a truncated window is a floor, and rendering it as a count
    # would overstate what we know.
    #
    # Only stamped when the SCAN (not the caller's `limit`) is what cut the
    # tail: if `limit` dropped episodes after us, the last one we return was
    # complete and labelling it `×N+` would be the same overstatement in the
    # other direction (review finding).
    out = episodes[:limit]
    if scan_saturated and out and len(episodes) <= limit:
        out[-1]["truncated"] = True
    for episode in out:
        if not include_identity:
            # Private grouping conventions must never reach a client.
            for field in [k for k in episode if k.startswith("_")]:
                episode.pop(field, None)
    return out


# ── Engine ──────────────────────────────────────────────────────────────────

class AbuseEngine:
    """Two-stage rule engine with per-rule flag episodes (delta 13)."""

    def __init__(self, store):
        self.store = store
        # In-process alert dedup (#3631): (org, rule, stage) -> the instant the
        # alert budget for that key re-opens. Bounds ONE process; the durable
        # store remains authoritative for staging and is the cross-replica gate.
        self._last_notified: dict[tuple[str, str, str], datetime] = {}
        self._notify_lock = threading.Lock()

    def _claim_notify(self, org_id: str, rule: str, stage: str,
                      now: datetime, window_s: int) -> bool:
        """True only if no alert of this ``stage`` for ``(org_id, rule)`` was
        emitted within the last ``window_s`` seconds (#3631 target a).

        Keyed by stage as well as ``(org, rule)``: the two-stage machine is
        SUPPOSED to emit a stage-1 flag alert and, a window later, a stage-2
        review alert (#5425 — the escalation is a human decision, not a
        suspension) — sharing one key would swallow the escalation. Each
        entry stores its OWN expiry, so pruning never evicts a long-window
        (R2, 24h) claim by a short-window (R1, 1h) caller's clock.
        """
        key = (org_id, rule, stage)
        with self._notify_lock:
            expires = self._last_notified.get(key)
            if expires is not None and expires > now:
                return False
            self._last_notified[key] = now + timedelta(seconds=window_s)
            if len(self._last_notified) > 10_000:
                self._last_notified = {
                    k: exp for k, exp in self._last_notified.items()
                    if exp > now}
            return True

    def _release_notify(self, org_id: str, rule: str, stage: str) -> None:
        """Re-arm the alert budget for a stage when its episode ENDS, so a
        genuinely NEW episode alerts again. The claim is per-EPISODE, not a
        wall-clock cooldown (#3631) — without this release, a burst that
        recycles inside the previous episode's window would re-flag durably but
        stay silent to ops.

        An out-of-band un-suspend (the ``abuse_unsuspend`` RPC writes a
        ``flag_clear`` with no in-process caller here) is not observed until the
        next clean window, so a re-breach before then may be muted for up to
        ``window_s``. That is the deliberate conservative side: releasing on a
        bare ``None`` would re-open the stale-read storm this claim exists to
        stop.
        """
        with self._notify_lock:
            self._last_notified.pop((org_id, rule, stage), None)

    def point_threshold(self) -> int:
        return _int_env("TORTOISE_ABUSE_POINT_THRESHOLD", 500)

    def point_window_s(self) -> int:
        return _window_env("TORTOISE_ABUSE_POINT_WINDOW_S", 3600)

    def key_threshold(self) -> int:
        return _int_env("TORTOISE_ABUSE_KEY_THRESHOLD", 10)

    def key_window_s(self) -> int:
        return _window_env("TORTOISE_ABUSE_KEY_WINDOW_S", 86400)

    def record_point_create(self, org_id: str, n: int = 1,
                            now: datetime | None = None) -> str | None:
        """R1 recording + evaluation. Piggybacks R2 evaluation (delta-13 fix:
        trigger-recorded key_create events have no app request of their own —
        they evaluate on the org's next hooked request)."""
        if abuse_disabled() or not org_id or n <= 0:
            return None
        now = _ensure_aware(_utcnow(now))
        try:
            self.store.record_event(org_id, EVENT_POINT_CREATE, weight=n,
                                    created_at=now)
        except Exception:
            logger.debug("abuse record_point_create failed for %s", org_id)
        r1 = self._evaluate(org_id, EVENT_POINT_CREATE,
                            self.point_threshold(), self.point_window_s(), now)
        r2 = self._evaluate(org_id, EVENT_KEY_CREATE,
                            self.key_threshold(), self.key_window_s(), now)
        # #5425: neither branch can return "suspend" any more — the engine has
        # no suspension to report. The old `"suspend" if "suspend" in (r1, r2)`
        # collapse was the residue of the deleted path (#4872) and is deleted
        # with it rather than left advertising a state that cannot occur.
        return r1 or r2

    def evaluate_key_creates(self, org_id: str,
                             now: datetime | None = None) -> str | None:
        """R2 evaluation (key_create events land via the DB trigger)."""
        if abuse_disabled() or not org_id:
            return None
        return self._evaluate(org_id, EVENT_KEY_CREATE,
                              self.key_threshold(), self.key_window_s(),
                              _ensure_aware(_utcnow(now)))

    def _evaluate(self, org_id: str, rule: str, threshold: int,
                  window_s: int, now: datetime) -> str | None:
        try:
            total = self.store.window_sum(org_id, rule, window_s, now)
        except Exception as e:
            report_abuse_decision_fault("window_sum", org_id, rule, e)
            return None
        if total <= threshold:
            # Clean window → end any active flag episode for this rule, so a
            # later burst starts fresh (re-flag, never a stale-flag suspend).
            ended = False
            try:
                if self.store.latest_flag_at(org_id, rule) is not None:
                    self.store.flag_clear(org_id, rule, now=now)
                ended = True
            except Exception as e:
                report_abuse_decision_fault(
                    "clean_window_episode_end", org_id, rule, e)
            if ended:
                # Episode CONFIRMED over: re-arm the alert budget so a NEW
                # episode alerts again (#3631 — per-episode, not a wall-clock
                # cooldown). NOT released when the episode-end read/write
                # FAILED — a store failure must reduce alert volume, never
                # increase it, so the claim is left to expire on its own.
                self._release_notify(org_id, rule, EVENT_FLAG)
                self._release_notify(org_id, rule, EVENT_REVIEW)
            return None
        details = {"rule": rule, "count": total,
                   "threshold": threshold, "window_s": window_s}
        try:
            flagged_at = self.store.latest_flag_at(org_id, rule)
        except Exception as e:
            report_abuse_decision_fault("latest_flag_at", org_id, rule, e)
            # Unreadable anchor → treat as a fresh episode for STAGING, but the
            # alert itself stays bounded by _claim_notify (#3631 target a).
            flagged_at = None
        if flagged_at is None:
            return self._flag(org_id, rule, details, now, window_s)
        flagged_at = _ensure_aware(flagged_at)
        age_s = (now - flagged_at).total_seconds()
        if age_s < window_s:
            return "breach"  # still inside the staging window
        # The flag is a full window old. Stage 2 requires CONTINUITY — at
        # least one rule event between the flag and the current window's
        # start. Without it the breach is a NEW episode after a quiet period:
        # re-flag, never suspend (code-review fix; delta-13 guarantee).
        try:
            continuity = self.store.rule_event_between(
                org_id, rule, flagged_at,
                now - timedelta(seconds=window_s))
        except Exception as e:
            report_abuse_decision_fault(
                "rule_event_between", org_id, rule, e)
            continuity = True  # fail-safe toward the conservative path
        if not continuity:
            return self._flag(org_id, rule, details, now, window_s)
        # #5425 owner ruling (2026-10-08): rate-limit, don't suspend —
        # "we should just have rate-limits and if they want more they need to
        # speak with us". A persistent breach now raises an OPERATOR ALERT and
        # NOTHING ELSE: a human decides (raise the ceiling, or reach out).
        #
        # What actually bounds a persistent abuser now — stated so the next
        # reader does not assume a velocity cap exists: the CUMULATIVE tier
        # quota (``quota.enforce_org_limit``: points/graphs/keys/sessions/users/
        # documents — a 402 at the tier ceiling) plus the per-key REQUEST rate
        # (``RateLimitMiddleware``, 100/min) and the platform-wide in-flight
        # capture cap (429). There is NO per-window point-rate cap, so an org
        # can consume its whole tier quota in one burst — the quota TOTAL is
        # the stop, not a rate. Widening that is a separate product decision.
        #
        # The automatic ``suspend_org`` call is DELETED, not repaired. #4872's
        # collapsed-failure defect (a failed suspend returning "breach", the
        # same value as "still staging") only existed while the suspend was
        # meant to fire; polishing that branch would be polishing something we
        # have decided not to have. ``store.suspend_org`` itself SURVIVES for an
        # out-of-band operator suspension — but note it is now called by NOTHING
        # in this repo, so an operator suspension reaches the customer solely
        # through the durable ``suspended_at`` read (the 403), not through this
        # engine and not through ``mark_suspended`` (see the delta-14 note).
        if self._claim_notify(org_id, rule, EVENT_REVIEW, now, window_s):
            self._notify(KIND_ABUSE_REVIEW_NEEDED, org_id, details)
        return "breach"

    def _flag(self, org_id: str, rule: str, details: dict, now: datetime,
              window_s: int) -> str:
        """Stage-1 flag. The notification is CONTINGENT on the flag having
        been persisted (#3631 target b): a store write failure must reduce
        alert volume, never increase it — the pre-fix swallow notified on
        every evaluation, because a never-persisted flag also reads as None.

        The alert is ALSO bounded to once per ``(org, rule)`` per staging
        window (#3631 target a) even when the durable anchor is unreliable —
        a read that returns a stale ``None`` (replica lag) or raises would
        otherwise re-flag and re-notify on every evaluation. Staging is still
        store-authoritative; this bounds the ALERT only.
        """
        try:
            self.store.flag_org(org_id, rule, details, now=now)
        except Exception as e:
            # #4872 (main): a failed flag write is a DECISION fault — surface it
            # to the operator instead of swallowing it; keep #3631's guarantee
            # that a non-persisted flag must never amplify abuse alerts.
            report_abuse_decision_fault("flag_org", org_id, rule, e)
            logger.warning(
                "abuse: flag NOT persisted for %s/%s — suppressing the abuse "
                "notification (a store failure must never amplify alerts, "
                "#3631)", org_id, rule)
            return "flag"
        if not self._claim_notify(org_id, rule, EVENT_FLAG, now, window_s):
            return "flag"  # alert budget for this (org, rule)/window is spent
        self._notify("abuse_flag", org_id, details)
        return "flag"

    def _notify(self, kind: str, org_id: str, details: dict) -> None:
        # #5425: the review alert is the operator's PRIMARY signal that a
        # persistent breach needs a human decision. Its row write is
        # BEST-EFFORT (debug-swallowed, per the DELIBERATELY EXCLUDED note on
        # `_notify` in DECISION_FAULT_LANES): if this write fails AND no chat
        # channel is configured, the stage-2 marker is lost — so the stage-1
        # `flag` rows in the same abuse list are the BACKSTOP, and no operator
        # instruction may treat this row as guaranteed. (Reviewer C, round 6:
        # the earlier wording here and in the runbook asserted durability the
        # swallow does not provide.)
        # PERSISTENT breach needs a human decision — the engine no longer
        # suspends, so nothing else marks the account. Give it a dashboard row
        # as well as a chat notification: an escalation that reaches only the
        # chat channel is invisible to whoever is reading the abuse list.
        # Best-effort, like every other event-record leg.
        if kind == KIND_ABUSE_REVIEW_NEEDED:
            try:
                self.store.record_event(org_id, EVENT_REVIEW, details=details)
            except Exception:
                logger.debug("abuse review event record failed (%s)", org_id)
        try:
            from tortoise.notify import notify_abuse
            notify_abuse(kind,
                         {"org_id": org_id,
                          "email": _org_email(self.store, org_id)},
                         {**details, "appeal_url": appeal_url()})
        except Exception:
            logger.debug("abuse notify failed (%s, %s)", kind, org_id)


# ── R3: read-velocity tracker (in-memory, notify-only) ─────────────────────

class ReadVelocityTracker:
    """>100 reads / 5min per-key OR per-org, AND above that key's OWN baseline.

    In-memory by design (the 5-min window bounds deploy-reset damage);
    notify-only per the issue — R3 never suspends. The notification goes to
    the ops Telegram channel (abuse alerts are Telegram-only since #3639, so
    there is no email recipient to resolve); a best-effort read_velocity event
    row surfaces the alert in the dashboard list.

    #10156 CALIBRATION — why a constant alone was wrong. Tortoise is an
    agent-memory product, so ONE retrieval-heavy agent turn easily exceeds 100
    reads in five minutes: our own battery/probe traffic certainly does. A
    fixed number cannot express "unusual" — it fires on the product working as
    intended, calls it suspicious, and (before #10156) told the customer to
    revoke a working key over it. The measured population when this was fixed:
    16 rows, 2026-09-16 → 09-26, all for ONE org and ONE key, and 13 of them in
    a single 6h49m bout (`read_velocity` dedup is per 5-min window).

    So a breach now needs BOTH:

    * ``count > threshold``            — the absolute floor, unchanged (100/5min);
    * ``count > factor * baseline``    — the same scope's own recent rate.

    ``baseline`` is an EWMA of the COMPLETED per-window counts, sampled once
    per window, not once per read: sampling per read would let the baseline
    track the bucket call-for-call, which makes ``factor * baseline`` always
    exceed the current count and silently disables the detector. With one
    sample per window a key whose normal traffic is 500/5min never alerts,
    while a key that normally does 5 and suddenly does 400 does — and a
    sustained steady hammer still crosses the floor once, then stops repeating
    itself every window (the bout shows up as ONE episode with a count, which
    is what #10156 asks for).

    Two deliberate non-changes:

    * the floor is NOT raised. A detector turned down to silence is not a
      fixed detector, and crossing 100 reads/5min from a cold start is still a
      real fact worth one notification.
    * a NON-POSITIVE threshold keeps its #5493 fail-CLOSED deny-all semantics:
      when ``threshold <= 0`` the baseline is not consulted at all, because an
      operator who configured deny-all must not have it silently loosened.

    ``TORTOISE_ABUSE_EXEMPT_KEY_IDS`` / ``TORTOISE_ABUSE_EXEMPT_ORG_IDS``
    (comma-separated) exempt first-party and test keys. An exempt ORG never
    alerts at all; an exempt KEY only skips the per-key scope, so a genuinely
    unusual org total still surfaces.

    ⚠️ The exemption MEANS are shipped, the exemption VALUES are an operator
    step: this module cannot know which ids are first-party, so both lists
    default to empty and the deployment must set them. Empty means "exempt
    nobody", which is the pre-#10156 behaviour and therefore safe, but it leaves
    first-party traffic able to alert whenever it out-runs its own baseline — so
    setting them is part of rolling this out, not an optional tuning.

    MEASURED FIRING RATE — firing WINDOWS, defaults (floor 100, factor 3,
    samples 6), re-runnable from ``tests/test_10156_alert_registers.py``'s
    helpers. "before" is the pre-#10156 detector, i.e. the floor alone:

    ==================================  ======  =====  =====================
    traffic profile                     before  after  note
    ==================================  ======  =====  =====================
    steady 130/5min × 20 windows            20      1  the defect itself
    steady 500/5min × 20 windows            20      1  busy is not suspicious
    steady 20/5min × 20 windows              0      0  normal traffic
    bursty 20/500 alternating × 20          10      4  a real agent pattern
    bursty 50/300 alternating × 20          10      2
    geometric ×1.5/window ramp               6      6  still caught
    geometric ×1.3/window ramp               2      0  ⚠️ LOST
    linear 100→500 (+40/window, 11 win)     10      0  ⚠️ LOST
    linear 100→200 (+10/window, 11 win)     10      0  ⚠️ LOST
    linear 20→540 (+40/window, 14 win)      11      2  reduced, not silent
    linear 5→705 (+100/window, 8 win)        7      5  reduced, not silent
    5 → 400 in one window                    1      1  the case it is FOR
    cold start, 150 in the first window      1      1  no baseline ⇒ floor
    ==================================  ======  =====  =====================

    ⚠️ STATED LOSS, because a detector that quietly stops seeing something is
    worse than a noisy one: whether a MONOTONE ramp is still seen depends on where
    it STARTS relative to the floor, not on its slope alone. A ramp that begins
    already above the floor bends the EWMA up in its first windows, so
    ``3 × baseline`` outruns it: ``100 → 500`` (+40/window) and ``100 → 200``
    (+10/window) both go from 10 firing windows to **0**. A ramp that begins BELOW
    the floor keeps a low baseline and is merely damped: ``20 → 540`` goes 11 → 2
    and ``5 → 705`` goes 7 → 5. The mechanism is the EWMA's lag — the baseline
    trails a rising series by a few windows, so ``3 × baseline`` stays above a
    series that climbs slowly from a high start. ``baseline_factor`` is the knob, and it is a REAL trade in both
    directions — at factor 2 a linear ramp is still missed AND bursty 20/500
    alternating goes from 4 firing windows back to 10, i.e. the pre-#10156 noise
    returns. That is why the default stays 3: the profile it protects (a normal
    agent's alternating quiet/busy turns) is the product's ordinary traffic, while
    a clean monotone ramp from a cold start is rarer than the noise it would cost.

    The floor is unchanged, so nothing is reported that a cold start could not
    already report; what changes is that a scope which is ALWAYS busy is no
    longer reported every five minutes forever.
    """

    def __init__(self, threshold: int | None = None, window_s: int | None = None,
                 baseline_factor: int | None = None,
                 baseline_samples: int | None = None,
                 exempt_keys: set[str] | None = None,
                 exempt_orgs: set[str] | None = None):
        self.threshold = threshold if threshold is not None else _int_env(
            "TORTOISE_ABUSE_READ_THRESHOLD", 100)
        self.window_s = window_s if window_s is not None else _window_env(
            "TORTOISE_ABUSE_READ_WINDOW_S", 300)
        self.baseline_factor = baseline_factor if baseline_factor is not None else _int_env(
            "TORTOISE_ABUSE_READ_BASELINE_FACTOR", 3)
        self.baseline_samples = baseline_samples if baseline_samples is not None else _int_env(
            "TORTOISE_ABUSE_READ_BASELINE_SAMPLES", 6)
        self.exempt_keys = (exempt_keys if exempt_keys is not None
                            else _id_set_env("TORTOISE_ABUSE_EXEMPT_KEY_IDS"))
        self.exempt_orgs = (exempt_orgs if exempt_orgs is not None
                            else _id_set_env("TORTOISE_ABUSE_EXEMPT_ORG_IDS"))
        self._by_key: dict[str, list[float]] = defaultdict(list)
        self._by_org: dict[str, list[float]] = defaultdict(list)
        self._notified: dict[tuple[str, str], float] = {}
        # #10156 baseline state: per (scope, id) the start of the sampling
        # window, the reads counted inside it, and the EWMA of COMPLETED
        # windows. See ``_sample_baseline`` for why there are three.
        self._window_started: dict[tuple[str, str], float] = {}
        self._window_count: dict[tuple[str, str], int] = {}
        self._baseline: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def record_read(self, key_id: str | None, org_id: str | None,
                    now: float | None = None) -> tuple[str, str] | None:
        """Returns ('key'|'org', id) on breach, else None. Notify dedup is
        once per window per (scope, id)."""
        if abuse_disabled():
            return None
        now = now if now is not None else time.time()
        cutoff = now - self.window_s
        breach: tuple[str, str] | None = None
        with self._lock:
            if org_id and org_id in self.exempt_orgs:
                return None
            if key_id:
                kb = self._by_key[key_id]
                kb[:] = [t for t in kb if t > cutoff]
                kb.append(now)
                count = len(kb)
                if key_id not in self.exempt_keys and self._over(count, "key", key_id):
                    breach = ("key", key_id)
                self._sample_baseline("key", key_id, now)
            if org_id:
                tb = self._by_org[org_id]
                tb[:] = [t for t in tb if t > cutoff]
                tb.append(now)
                count = len(tb)
                if breach is None and self._over(count, "team", org_id):
                    breach = ("team", org_id)
                self._sample_baseline("team", org_id, now)
            # bound memory growth (mirrors _register_buckets hygiene).
            # #10156: the re-wrap in ``defaultdict`` is NOT cosmetic. A plain
            # dict comprehension left these as ORDINARY dicts, so the NEXT
            # unseen key raised KeyError at `self._by_key[key_id]` — swallowed by
            # the best-effort caller (`maybe_record_mcp_read`), which silently
            # stopped counting reads for every new key after the first prune.
            # Found by the calibration test that fills 10k keys.
            if len(self._by_key) > 10_000:
                self._by_key = defaultdict(list, {
                    k: v for k, v in self._by_key.items()
                    if any(t > cutoff for t in v)})
            if len(self._by_org) > 10_000:
                self._by_org = defaultdict(list, {
                    k: v for k, v in self._by_org.items()
                    if any(t > cutoff for t in v)})
            # #10156: the calibration maps grow one entry per (scope, id) EVER
            # seen, so they need the same bound. The gate is on
            # ``_window_started`` as well as ``_baseline`` — review finding: a
            # scope read ONCE never completes a window, so it never enters
            # ``_baseline``, and a baseline-only gate left ``_window_started``
            # growing with every key id ever seen while the map it tested stayed
            # empty (green test, unbounded process). A scope whose observation
            # window has expired is dropped with its baseline: keeping the
            # baseline while discarding the counts would judge a scope that went
            # quiet against a rate it no longer has, and a dropped entry is the
            # safe direction — it falls back to the absolute floor, which never
            # suppresses an alert.
            if len(self._window_started) > 10_000 or len(self._baseline) > 10_000:
                live = {k for k, ts in self._window_started.items()
                        if now - ts < self.window_s}
                self._baseline = {k: v for k, v in self._baseline.items()
                                  if k in live}
                self._window_started = {k: v for k, v in
                                        self._window_started.items()
                                        if k in live}
                self._window_count = {k: v for k, v in self._window_count.items()
                                      if k in live}
            # prune the notify-dedup map too (code-review P3)
            self._notified = {k: t for k, t in self._notified.items()
                              if now - t < self.window_s}
            if breach is not None:
                last = self._notified.get(breach)
                if last is not None and now - last < self.window_s:
                    return None  # already notified this window
                self._notified[breach] = now
        if breach is not None:
            self._notify(breach, org_id)
        return breach

    def _over(self, count: int, scope: str, ident: str) -> bool:
        """The breach decision (#10156): over the FLOOR and over the BASELINE.

        ``threshold <= 0`` is a configured deny-all (#5493) and short-circuits
        the baseline entirely — an operator's fail-CLOSED setting must never be
        loosened by a calibration feature.

        A scope with no baseline yet (cold start, or the first window after a
        deploy, since this tracker is in-memory) is judged on the floor alone:
        when we know nothing about a key's normal rate, the absolute threshold
        is the only fact available and suppressing it would be inventing one.
        """
        if count <= self.threshold:
            return False
        if self.threshold <= 0:
            return True
        baseline = self._baseline.get((scope, ident))
        if baseline is None or baseline <= 0:
            return True
        return count > self.baseline_factor * baseline

    def _sample_baseline(self, scope: str, ident: str, now: float) -> None:
        """Feed the per-scope baseline ONE sample per completed window.

        Sampling is gated on the tracker's OWN window, and that gate is the
        whole point: an EWMA updated on every read converges on the live count
        within a few calls, which makes ``factor * baseline`` always exceed the
        count and the detector permanently silent. One sample per window means
        the baseline describes the scope's normal rate, not its current burst.
        The sample is taken AFTER the breach decision for this call (the caller
        decides first), so a burst cannot raise the baseline that would have
        judged it.
        """
        key = (scope, ident)
        started = self._window_started.get(key)
        if started is None or now - started >= self.window_s:
            completed = self._window_count.get(key)
            if completed is not None:
                prev = self._baseline.get(key)
                alpha = 1.0 / max(1, self.baseline_samples)
                self._baseline[key] = (
                    float(completed) if prev is None
                    else prev + (float(completed) - prev) * alpha)
            self._window_started[key] = now
            self._window_count[key] = 0
        self._window_count[key] = self._window_count.get(key, 0) + 1

    def _notify(self, breach: tuple[str, str], org_id: str | None) -> None:
        scope, ident = breach
        # Dashboard alert row (best-effort; same dedup gate as the notify)
        store = None
        try:
            from tortoise.supabase_control import get_abuse_store
            store = get_abuse_store()
            if org_id:
                store.record_event(org_id, EVENT_READ_VELOCITY,
                                   details={"scope": scope, "id": ident})
        except Exception:
            logger.debug("read-velocity event record failed (%s)", breach)
        try:
            from tortoise.notify import notify_abuse
            notify_abuse("abuse_read_velocity",
                         {"org_id": org_id,
                          "email": _org_email(store, org_id)},
                         {"scope": scope, "id": ident,
                          "threshold": self.threshold,
                          "window_s": self.window_s,
                          "appeal_url": appeal_url()})
        except Exception:
            logger.debug("read-velocity notify failed (%s)", breach)


READ_TRACKER = ReadVelocityTracker()


def record_read(key_id: str | None, org_id: str | None,
                now: float | None = None):
    """Module-level seam (monkeypatchable) over the shared tracker."""
    return READ_TRACKER.record_read(key_id, org_id, now)


# ── R8: signup-velocity tracker (in-memory, notify-only) ───────────────────

class SignupVelocityTracker:
    """>N anonymous signups per IP per window → notify ops once per window.

    Anon orgs have NULL user_id, so R3/R4 owner-notify resolves nothing —
    R8 is the OPS-visible farming signal (abuse alerts are Telegram-only since
    #3639, so the anon case needs no recipient resolution at all). In-memory
    by design (mirrors
    ReadVelocityTracker/R3): R8 NEVER suspends, so deploy-reset damage is
    bounded to a notify. The durable multi-instance sweeper over audit_events
    is a documented follow-on (idx_audit_ip_time ships in #1081; sweeper
    contract in docs/scoping/scoping-1081-agent-signup-abuse.md).

    Two feeds:
    - record_signup  — SUCCESSFUL mint path (consensus: blocked farmers must
      not inflate the success count with attempts). Breach on >= threshold:
      threshold = allowance (2/24h) so the feed fires exactly when an IP
      consumes its entire anonymous allowance (the designed review signal).
    - record_block   — the signup limiter's 429 (the unmistakable farming
      evidence). Same dedup key as the success feed (bare ip) — one ops
      email per (ip, window), never two (P1-FIX-2).
    """

    def __init__(self, threshold: int | None = None, window_s: int | None = None):
        self.threshold = threshold if threshold is not None else _int_env(
            "TORTOISE_ABUSE_SIGNUP_THRESHOLD",
            _int_env("TORTOISE_SIGNUP_IP_LIMIT", 2))  # P3-5: defaults follow allowance
        self.window_s = window_s if window_s is not None else _window_env(
            "TORTOISE_ABUSE_SIGNUP_WINDOW_S", 86400)
        self._by_ip: dict[str, list[float]] = defaultdict(list)
        self._notified: dict[str, float] = {}  # bare ip -> last notify ts
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Test seam: clear per-IP counts and dedup state (P1-FIX-10 —
        module-scoped TestClient shares one host across tests)."""
        with self._lock:
            self._by_ip.clear()
            self._notified.clear()

    def record_signup(self, ip: str | None, org_id: str | None = None,
                      now: float | None = None) -> tuple[str, str] | None:
        """Success-path feed: count minted orgs per IP per window.
        Returns ('ip', ip) on breach (len >= threshold), else None. Notify
        dedup once per window per IP (bare-ip key, shared with block path)."""
        if abuse_disabled() or not ip:
            return None
        now = now if now is not None else time.time()
        cutoff = now - self.window_s
        breach: tuple[str, str] | None = None
        with self._lock:
            bucket = self._by_ip[ip]
            bucket[:] = [t for t in bucket if t > cutoff]
            bucket.append(now)
            if len(bucket) >= self.threshold:
                breach = ("ip", ip)
            if len(self._by_ip) > 10_000:
                # re-wrap: a dict-comprehension replace would lose defaultdict
                # semantics and KeyError on NEW ips after the prune (caught by
                # test_memory_bound — 10,100 distinct IPs)
                self._by_ip = defaultdict(
                    list, {k: v for k, v in self._by_ip.items()
                           if any(t > cutoff for t in v)})
            self._notified = {k: t for k, t in self._notified.items()
                              if now - t < self.window_s}
            if breach is not None:
                last = self._notified.get(ip)
                if last is not None and now - last < self.window_s:
                    return None  # already notified this window
                self._notified[ip] = now
        if breach is not None:
            self._notify("velocity", ip, org_id, {"count": len(bucket)})
        return breach

    def record_block(self, ip: str | None, org_id: str | None = None,
                     now: float | None = None) -> None:
        """Block-path feed: the signup limiter 429'd this IP. Same dedup key
        (bare ip) as the success feed — the 429 after a 2-mint allowance is
        dedup-suppressed (one email per episode, P1-FIX-2)."""
        if abuse_disabled() or not ip:
            return
        now = now if now is not None else time.time()
        with self._lock:
            self._notified = {k: t for k, t in self._notified.items()
                              if now - t < self.window_s}
            last = self._notified.get(ip)
            if last is not None and now - last < self.window_s:
                return
            self._notified[ip] = now
        self._notify("blocked", ip, org_id, {})

    def _notify(self, reason: str, ip: str, org_id: str | None,
                details: dict) -> None:
        # P4-FIX: payload carries count ALWAYS (block path details={} → count
        # 0 is fine; _alert_dict reads details.get('count')).
        details = dict(details)  # do not mutate caller's dict
        details.setdefault("count", 0)
        # Dashboard alert row (best-effort; minted org anchors the FK).
        store = None
        try:
            from tortoise.supabase_control import get_abuse_store
            store = get_abuse_store()
            if org_id:
                store.record_event(
                    org_id, EVENT_SIGNUP_VELOCITY,
                    details={"ip": ip, "reason": reason, **details})
        except Exception:
            logger.debug("signup-velocity event record failed (%s)", ip)
        try:
            from tortoise.notify import notify_abuse
            # org_id is None for anon orgs; the Telegram alert needs no
            # recipient resolution (#3639 — abuse alerts are Telegram-only)
            notify_abuse("abuse_signup_velocity",
                         {"org_id": org_id, "email": None},
                         {"ip": ip, "reason": reason,
                          "count": details.get("count", 0),
                          "threshold": self.threshold,
                          "window_s": self.window_s,
                          "appeal_url": appeal_url()})
        except Exception:
            logger.debug("signup-velocity notify failed (%s)", ip)


SIGNUP_TRACKER = SignupVelocityTracker()


def record_signup(ip: str | None, org_id: str | None = None,
                  now: float | None = None) -> tuple[str, str] | None:
    """Module-level seam (monkeypatchable) over the shared tracker."""
    return SIGNUP_TRACKER.record_signup(ip, org_id, now)


def record_signup_block(ip: str | None, org_id: str | None = None,
                        now: float | None = None) -> None:
    """Module-level seam for the 429 path."""
    SIGNUP_TRACKER.record_block(ip, org_id, now)


class RecoveryVelocityTracker:
    """>N keyless recoveries per IP per window → notify ops once per window.

    #1709: the keyless-recovery surface (POST /v1/agent/recover + the
    token-present signup branch) mints keys WITHOUT an authenticated identity
    — the signup-velocity feed (record_signup) must not conflate recoveries
    with mints (ops metrics + alert thresholds differ). This tracker mirrors
    SignupVelocityTracker: in-memory by design (never suspends — deploy-reset
    damage bounded to a notify), one notify per (ip, window) on breach.

    Fired on SUCCESSFUL recovery mints only (possession-authenticated); the
    per-IP + per-token attempt limiters handle the probe case (uniform 422).
    """

    def __init__(self, threshold: int | None = None, window_s: int | None = None):
        self.threshold = threshold if threshold is not None else _int_env(
            "TORTOISE_ABUSE_RECOVER_THRESHOLD",
            _int_env("TORTOISE_RECOVER_IP_LIMIT", 5))
        self.window_s = window_s if window_s is not None else _window_env(
            "TORTOISE_ABUSE_RECOVER_WINDOW_S", 86400)
        self._by_ip: dict[str, list[float]] = defaultdict(list)
        self._notified: dict[str, float] = {}  # bare ip -> last notify ts
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Test seam: clear per-IP counts and dedup state."""
        with self._lock:
            self._by_ip.clear()
            self._notified.clear()

    def record(self, ip: str | None, org_id: str | None = None,
               now: float | None = None) -> tuple[str, str] | None:
        """Success-path feed: count recovery mints per IP per window.
        Returns ('ip', ip) on breach (len >= threshold), else None."""
        if abuse_disabled() or not ip:
            return None
        now = now if now is not None else time.time()
        cutoff = now - self.window_s
        breach: tuple[str, str] | None = None
        with self._lock:
            bucket = self._by_ip[ip]
            bucket[:] = [t for t in bucket if t > cutoff]
            bucket.append(now)
            if len(bucket) >= self.threshold:
                breach = ("ip", ip)
            if len(self._by_ip) > 10_000:
                self._by_ip = defaultdict(
                    list, {k: v for k, v in self._by_ip.items()
                           if any(t > cutoff for t in v)})
            self._notified = {k: t for k, t in self._notified.items()
                              if now - t < self.window_s}
            if breach is not None:
                last = self._notified.get(ip)
                if last is not None and now - last < self.window_s:
                    return None  # already notified this window
                self._notified[ip] = now
        if breach is not None:
            self._notify("velocity", ip, org_id, {"count": len(bucket)})
        return breach

    def _notify(self, reason: str, ip: str, org_id: str | None,
                details: dict) -> None:
        details = dict(details)
        details.setdefault("count", 0)
        store = None
        try:
            from tortoise.supabase_control import get_abuse_store
            store = get_abuse_store()
            if org_id:
                store.record_event(
                    org_id, EVENT_RECOVERY_VELOCITY,
                    details={"ip": ip, "reason": reason, **details})
        except Exception:
            logger.debug("recovery-velocity event record failed (%s)", ip)
        try:
            from tortoise.notify import notify_abuse
            notify_abuse("abuse_recovery_velocity",
                         {"org_id": org_id, "email": None},
                         {"ip": ip, "reason": reason,
                          "count": details.get("count", 0),
                          "threshold": self.threshold,
                          "window_s": self.window_s,
                          "appeal_url": appeal_url()})
        except Exception:
            logger.debug("recovery-velocity notify failed (%s)", ip)


RECOVERY_TRACKER = RecoveryVelocityTracker()


def record_recovery(ip: str | None, org_id: str | None = None,
                    now: float | None = None) -> tuple[str, str] | None:
    """Module-level seam (monkeypatchable) over the shared recovery tracker."""
    return RECOVERY_TRACKER.record(ip, org_id, now)


# ── R4: geo (CF-IPCountry header, fail-open) ───────────────────────────────

_GEO_CACHE: dict[str, tuple[float, set[str]]] = {}
_GEO_TTL_S = 86400
_GEO_LOCK = threading.Lock()
# per-org geo-notify flood cap (CF-IPCountry is spoofable on Fly-direct —
# security review): at most N new-country notifications per org per 24h.
_GEO_NOTIFY_MAX_PER_DAY = 10
_GEO_NOTIFIED: dict[str, list[float]] = defaultdict(list)


def resolve_country(headers) -> str | None:
    """CF-IPCountry passthrough (Fly/Cloudflare set it). Fail-open: no header
    → None → R4 inactive. IPINFO_TOKEN resolver is a documented follow-on."""
    try:
        value = headers.get("cf-ipcountry") if headers else None
    except Exception:
        return None
    value = (value or "").strip()
    return value.upper() or None


def check_new_country(org_id: str, country: str | None, store,
                      now: float | None = None) -> bool:
    """True when the country is new for the org (records auth_ip + notifies
    ops, flood-capped). Seen-set cached in-process (24h TTL); durable lookup
    on cache miss."""
    if abuse_disabled() or not org_id or not country:
        return False
    now = now if now is not None else time.time()
    with _GEO_LOCK:
        cached = _GEO_CACHE.get(org_id)
        if cached is None or now - cached[0] > _GEO_TTL_S:
            try:
                seen = set(store.seen_countries(org_id))
            except Exception:
                return False  # fail-open: unseen-set unavailable → inactive
            cached = (now, seen)
            _GEO_CACHE[org_id] = cached
            # evict other expired entries (bounded memory)
            expired = [t for t, (ts, _) in _GEO_CACHE.items()
                       if now - ts > _GEO_TTL_S]
            for t in expired:
                _GEO_CACHE.pop(t, None)
                _GEO_NOTIFIED.pop(t, None)
        seen = cached[1]
        if country in seen:
            return False
        # flood cap before recording anything
        stamps = [t for t in _GEO_NOTIFIED[org_id] if now - t < _GEO_TTL_S]
        _GEO_NOTIFIED[org_id] = stamps
        if len(stamps) >= _GEO_NOTIFY_MAX_PER_DAY:
            seen.add(country)  # still track it; just don't notify/event
            return False
    try:
        store.record_event(org_id, EVENT_AUTH_IP, country=country)
    except Exception:
        # Unrecorded → not marked seen → retried on a later request (and
        # NOT notified: a notify without a durable event would re-fire on
        # every request until the store recovers — code-review P3).
        logger.debug("geo event record failed for %s", org_id)
        return False
    with _GEO_LOCK:
        cached2 = _GEO_CACHE.get(org_id)
        if cached2 is not None:
            cached2[1].add(country)
        _GEO_NOTIFIED[org_id].append(now)
    try:
        from tortoise.notify import notify_abuse
        notify_abuse("abuse_new_ip",
                     {"org_id": org_id,
                      "email": _org_email(store, org_id)},
                     {"country": country, "appeal_url": appeal_url()})
    except Exception:
        logger.debug("geo notify failed for %s", org_id)
    return True


def reset_geo_cache() -> None:
    with _GEO_LOCK:
        _GEO_CACHE.clear()
        _GEO_NOTIFIED.clear()


# ── Engine singleton seam ───────────────────────────────────────────────────

_engine: AbuseEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> AbuseEngine:
    """Lazy process-wide engine (tests monkeypatch or set_engine())."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from tortoise.supabase_control import get_abuse_store
                _engine = AbuseEngine(get_abuse_store())
    return _engine


def set_engine(engine: AbuseEngine | None) -> None:
    global _engine
    with _engine_lock:
        _engine = engine
