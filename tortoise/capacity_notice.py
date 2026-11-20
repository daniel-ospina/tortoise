"""The two working-set capacity notices — 80 % and 100 % (#13998).

WHAT THIS IS. One email template, two thresholds of the **fast-search (graph)
capacity included in the account's plan**. `{n}` and the TENSE of the status
lines are the only differences between the two sends (see
:func:`demotion_status` — at 80 % nothing has been demoted yet, so the two
sentences that describe demotion are FUTURE tense there and PRESENT at 100 %,
because stating them in the present at 80 % would describe a state the account
is not in).

⛔ THE COPY IS THE OWNER'S, VERBATIM (approved 2026-11-20, `#13998`). A lane may
not re-word it: :data:`SUBJECT` and the paragraph constants below are transcribed
from the issue, and `tests/test_13998_capacity_notice.py` renders them into
golden files so an edit to the wording reds CI instead of shipping silently. The
vocabulary guard in that test also reds on `allowance reached`,
`Upgrade your plan`, `exact-word`, `may miss` and any number that is not `{n}` —
the words the pre-destination drafts used, and the ones this whole thread exists
to keep out.

⛔ THE CTA OFFERS CAPACITY, NEVER A TIER. `[ Add capacity ]` resolves to the app's
billing surface (:func:`add_capacity_url`), where an account's capacity lives. A
plan-tier link is deliberately absent: a tier change is the wrong answer to a
capacity need (#13989 policy 4 — *"Capacity (overage/add-on) is offered first;
tier changes are for **feature** needs only"*), and offering it here is the
defect this thread fixes.

⚠️ THE REPORTED GAP, STATED RATHER THAN GLOSSED: there is **no storage add-on
purchase surface in the product today** — `product/pricing.json` carries the
rates (`billing.storage.dimensions.graph_storage.overage_price_usd_per_gb_month`
= $85.00/GB-mo and `postgres_storage` = $0.30/GB-mo) and
`metering.storage_overage_cost_usd` prices a bill, but nothing sells a top-up.
So the CTA lands a customer on the surface that OWNS capacity (usage + plan +
the portal) and a human fulfils the request; the missing purchase flow is filed
separately rather than papered over here (see the PR body). The URL is one
constant on purpose: when the add-on flow lands, this file changes and nothing
else does.

WHERE THE NUMBER COMES FROM — the gate's own instruments, never this module's
arithmetic (#13998 requirement 5):

* the ALLOWANCE is `quota.resolve_org_limits(org_id)["max_graph_storage_mb"]` —
  the per-ACCOUNT hot-window figure from `product/pricing.json`, armed on the
  resolved limits dict by `quota._arm_graph_storage` (#11758: the storage shape
  is per account only, so there is no per-user and no per-graph variance here);
* the READING is `allowance_gate.read_dimension("graph_storage", org_id)` — the
  same meter the write gate refuses on, so the email cannot disagree with what
  happens to the account;
* ⛔ IF EITHER CANNOT BE READ, NOTHING IS SENT AND THE PASS LOGS IT
  (:func:`evaluate` returns a named reason instead of a percentage). A guessed
  percentage in a customer's inbox is worse than no email.
  ⚠️ The reading is an ESTIMATE that under-reports (it excludes the HNSW index —
  `graph_storage.VECTOR_INDEX_EXCLUDED`), so this notice errs LATE, never early.

EXACTLY ONE SEND PER THRESHOLD PER ACCOUNT, EVER. The durable ledger is
`teams.storage_notice_pct`, an integer HIGH-WATER MARK of the highest threshold
ever notified, stamped only after the provider accepts. A high-water mark rather
than one column per threshold, because two independent markers let the emails
land OUT OF ORDER (a 100 % notice, then a stale 80 % pass emailing "at 80 %"
afterwards); with a mark the send is legal only when `threshold > mark`, so the
sequence is monotone by construction. The stamp is a compare-and-swap on the
value this pass read (:func:`claim_notice`), so two processes cannot interleave
into a lower mark, and the provider Idempotency-Key
(`capacity-notice:{org_id}:{pct}`) collapses the cross-process replay that
survives a crash between send and stamp.

BLAST CONTROL. The send budget (#1138/#3631, `email_notify.reserve_send_slot`)
is the limiter for the first fleet-wide pass: past the daily cap the remaining
accounts are SKIPPED, and a skipped send never stamps the mark, so they are
retried on a later pass — a ramp, not a blast, and never a duplicate.
"""
from __future__ import annotations

import asyncio
import html
import logging
import math
from dataclasses import dataclass
from typing import Any

from . import allowance_gate, quota

logger = logging.getLogger(__name__)

#: The two thresholds, as PERCENTAGES of the account's `max_graph_storage_mb`.
THRESHOLD_EIGHTY = 80
THRESHOLD_FULL = 100
THRESHOLDS: tuple[int, ...] = (THRESHOLD_EIGHTY, THRESHOLD_FULL)

#: The allowance dimension these notices are about. Named once: the email, the
#: meter and the pricing dimension must be the same storage dimension, and a
#: second spelling is how a notice drifts onto a figure the gate never reads.
DIMENSION = "graph_storage"

#: `GET /v1/team` / pricing key the allowance is read from.
ALLOWANCE_KEY = allowance_gate.ALLOWANCE_KEYS[DIMENSION]

#: The public page that explains the model in the same voice (#14013 owns its
#: content — this module only links it).
#:
#: ⛔ MEASURED 2026-11-20, not assumed: `https://premiselabs.co/tortoise/docs`
#: answers 200 with **0 redirect hops**; both `premiselabs.co/docs` and
#: `tortoise.premiselabs.co/docs` answer **301 → that same URL**, so either of
#: those spellings would cost the one hop this link exists to avoid (the same
#: 0-hop rule `email_notify.email_link_base` records for #11767).
#: ⚠️ The `#storage` SECTION lands with `#14013`; until it does, the link still
#: resolves to the docs page itself, so it is never a dead link.
STORAGE_DOCS_URL = "https://premiselabs.co/tortoise/docs#storage"

# ── The approved copy (owner, 2026-11-20) — VERBATIM, in one place ──────────
# ⛔ Do not re-word. `tests/test_13998_capacity_notice.py` pins the rendered
# output against golden files AND guards the vocabulary; an edit here that is not
# the owner's reds both.
SUBJECT = "Your search layer is at {n}% — nothing is lost"

GREETING = "Hi {first name},"
STATUS_LINE = ("Your workspace is using {n}% of the fast-search capacity "
               "included in your plan.")
SHORT_VERSION = ("Short version: **nothing is lost or deleted.** Everything "
                 "you've saved stays searchable. {changes}")
ADD_CAPACITY_CTA = "Add capacity"
WHY_HEADING = "Why it works this way"
WHY_POSTGRES = ("Tortoise keeps your data in a Postgres database. That's "
                "inexpensive, so we can store everything you give us without "
                "charging you for it twice.")
WHY_GRAPH = ("On top of that we run a graph database. It mirrors a slice of "
             "your data and makes search faster and better at following "
             "connections — finding things by meaning, and tracing how they "
             "relate to each other.")
WHY_RESERVED = ("That graph is expensive to run, so we reserve it for what "
                "matters most: your highest-value and most frequently searched "
                "items. Everything else stays in Postgres.")
NOTICE_HEADING = "What you'll notice"
KEEPS_WORKING = "Search keeps working across everything. {fetched}"
CAN_ADD = ("If you want everything in the fast layer, you can add capacity at "
           "any time.")
DOCS_LINK = "How Tortoise stores and searches your data →"


def demotion_status(threshold: int) -> tuple[str, str]:
    """``(changes, fetched)`` — the two sentences whose TENSE depends on the
    threshold, and nothing else does.

    The issue's rule is *"only `{n}` and the tense of the status line change (at
    80 % the items have not yet been demoted)"*. These are the two sentences that
    make a claim about demotion, so at 80 % both are FUTURE (the account is not
    in that state yet) and at 100 % both are PRESENT — the approved wording.

    ⛔ WHY THE PRESENT TENSE IS NOT SIMPLY REUSED AT 80 %: telling an account one
    sentence away from its cap that "what changes is how quickly some older items
    come back" describes a change that has not happened. That is the gloss #2952
    forbids — a degraded (here: a *pre*-degraded) state declared as if it were
    already in force. The tense is the honest minimum, and it is the only
    latitude this module takes with the owner's copy.

    Raises ``ValueError`` for any threshold outside :data:`THRESHOLDS` — an
    unknown threshold has no approved wording, and inventing one is exactly the
    drift this module exists to prevent.
    """
    if threshold == THRESHOLD_EIGHTY:
        return (
            "What will change is how quickly some older items come back.",
            ("Items outside the fast layer will be fetched when you search for "
             "them, so they may take a moment longer."),
        )
    if threshold == THRESHOLD_FULL:
        return (
            "What changes is how quickly some older items come back.",
            ("Items outside the fast layer are fetched when you search for "
             "them, so they can take a moment longer."),
        )
    raise ValueError(f"no approved copy for threshold {threshold!r} "
                     f"(the approved set is {THRESHOLDS})")


def capacity_pct(used: float, allowance: float) -> int | None:
    """The account's used share of its allowance, as a whole percentage.

    ``None`` when the pair cannot produce one: a non-finite or negative figure, or
    a non-positive allowance (an UNARMED tier — `free`/`anon` carry no hot window,
    so there is nothing for a percentage to be a percentage OF).

    ⛔ FLOOR, AND CAPPED BELOW 100 UNTIL THE RATIO REACHES IT — the same
    discipline `website/apps/dashboard/src/nodeUsage.js` pins for the node nudge
    (#4331): the DISPLAYED percentage may not cross a threshold the level has
    not, so 79.6 % may not read "80 %" (a notice the account has not earned) and
    99.6 % may not read "100 %" while the account is still under its allowance.
    """
    for value in (used, allowance):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        if not math.isfinite(float(value)):
            return None
    if used < 0 or allowance <= 0:
        return None
    ratio = float(used) / float(allowance)
    if ratio >= 1.0:
        return 100
    return min(99, math.floor(ratio * 100))


def threshold_reached(pct: int | None) -> int | None:
    """The HIGHEST threshold this percentage reaches, or ``None``.

    Highest rather than first-unclaimed: an account that jumps from 60 % to 105 %
    between passes gets the 100 % notice only. Sending the 80 % one as well would
    be two emails in one pass, and sending it *afterwards* is the out-of-order
    case :func:`claim_notice` makes impossible.
    """
    if pct is None:
        return None
    for threshold in sorted(THRESHOLDS, reverse=True):
        if pct >= threshold:
            return threshold
    return None


@dataclass(frozen=True)
class Notice:
    """A threshold this account has reached — the decision to render a notice."""

    org_id: str
    threshold: int
    used_mb: float
    allowance_mb: float

    @property
    def pct(self) -> int:
        return self.threshold


@dataclass(frozen=True)
class Copy:
    """The rendered notice — what a sender hands to the provider."""

    subject: str
    html: str
    text: str


#: Kill switch + cadence for the scheduled pass. Default ON, ``env_flag``
#: semantics: unset/blank/garbage resolve to the default so a typo can never
#: silently disarm the only trigger these notices have (the webhook sweep's own
#: reading of the same knob, `webhook_delivery.sweep_enabled`). A deployment that
#: wants them off says ``0`` explicitly.
#:
#: ⚠️ WHY A FLEET-WIDE DEFAULT PASS IS SAFE TO ARM: the notice is gated on THREE
#: real facts — a measured gauge row, an armed allowance, and a threshold — so a
#: tenant with no reading (every tenant until the sweep has measured it) gets
#: nothing, and the free/anon tiers carry no window so they get nothing. The
#: first pass over a fleet already above 80 % is bounded by the shared Resend
#: send budget (#1138), and a budget-skipped account is retried on a later pass
#: rather than dropped.
NOTICE_ENABLED_ENV = "TORTOISE_CAPACITY_NOTICE"
NOTICE_INTERVAL_ENV = "TORTOISE_CAPACITY_NOTICE_INTERVAL"
#: 6 h: the gauge is a periodic estimate whose gate staleness bound is 24 h
#: (`allowance_gate.STALE_AFTER_SECONDS`), so a cadence well inside it means a
#: threshold crossing is noticed the same day without a per-write cost.
NOTICE_INTERVAL_DEFAULT_S = 6 * 60 * 60
#: Ops cap on accounts PROCESSED per pass (never a relaxation of enumeration
#: completeness). Unset = no cap.
NOTICE_LIMIT_ENV = "TORTOISE_CAPACITY_NOTICE_LIMIT"


def notices_enabled() -> bool:
    """Is the scheduled capacity-notice pass armed? (default ON)."""
    from .env_truthy import env_flag

    return env_flag(NOTICE_ENABLED_ENV, True)


def sweep_interval_s() -> int:
    """Validate ``TORTOISE_CAPACITY_NOTICE_INTERVAL`` to a positive int.

    Same contract as `webhook_delivery.sweep_interval_s` and
    `monitoring.event_retention_interval`: a non-numeric value falls back to the
    default with a warning, and ``0``/negative is REFUSED rather than accepted,
    because ``asyncio.sleep(0)`` returns immediately and would turn the pass into
    a busy loop against every tenant's gauge.
    """
    import os

    raw = os.environ.get(NOTICE_INTERVAL_ENV)
    if raw is None or not str(raw).strip():
        return NOTICE_INTERVAL_DEFAULT_S
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a whole number of seconds — using %ds",
                       NOTICE_INTERVAL_ENV, raw, NOTICE_INTERVAL_DEFAULT_S)
        return NOTICE_INTERVAL_DEFAULT_S
    if value < 1:
        logger.warning(
            "%s=%r is not a positive number of seconds — using %ds (a "
            "zero/negative interval would re-evaluate the fleet in a busy loop)",
            NOTICE_INTERVAL_ENV, raw, NOTICE_INTERVAL_DEFAULT_S)
        return NOTICE_INTERVAL_DEFAULT_S
    return value


def sweep_limit() -> int | None:
    """Ops cap on how many accounts a pass PROCESSES, or ``None`` for all."""
    import os

    raw = os.environ.get(NOTICE_LIMIT_ENV)
    if raw is None or not str(raw).strip():
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a whole number — processing the whole "
                       "fleet", NOTICE_LIMIT_ENV, raw)
        return None
    return value if value > 0 else None


def add_capacity_url() -> str:
    """Where `[ Add capacity ]` goes: the app's billing surface.

    The origin comes from `email_notify.email_link_base()` — the ONE env-driven
    app origin (#11767) — so a staging or preview deployment does not mail a link
    to production. `#/billing` is a real deep link: `billing` is in the
    dashboard's `KNOWN_TABS` (`website/apps/dashboard/src/main.jsx`), so an
    authenticated reader lands on the surface that holds usage, the plan and the
    portal rather than on the Overview.
    """
    from .email_notify import email_link_base

    return f"{email_link_base()}/#/billing"


def render(threshold: int, first_name: str | None) -> Copy:
    """Render the approved copy for ``threshold``. Never sends anything.

    ``first_name`` is cosmetic (the #2406 greeting heuristic's output); an empty
    or missing name falls back to ``there`` so the email never reads "Hi ,".
    """
    name = (first_name or "").strip() or "there"
    changes, fetched = demotion_status(threshold)
    pct = str(threshold)
    subject = SUBJECT.format(n=pct)
    text = _text(name, pct, changes, fetched)
    return Copy(subject=subject, html=_html(name, pct, changes, fetched),
                text=text)


def _text(name: str, pct: str, changes: str, fetched: str) -> str:
    return (
        f"Hi {name},\n\n"
        f"{STATUS_LINE.format(n=pct)}\n\n"
        f"{SHORT_VERSION.format(changes=changes).replace('**', '')}\n\n"
        f"[ {ADD_CAPACITY_CTA} ]\n\n"
        "---\n\n"
        f"{WHY_HEADING}\n\n"
        f"{WHY_POSTGRES}\n\n"
        f"{WHY_GRAPH}\n\n"
        f"{WHY_RESERVED}\n\n"
        f"{NOTICE_HEADING}\n\n"
        f"{KEEPS_WORKING.format(fetched=fetched)}\n\n"
        f"{CAN_ADD}\n\n"
        f"[ {DOCS_LINK} ]\n"
    )


def _html(name: str, pct: str, changes: str, fetched: str) -> str:
    # Same shell as the invite / OTP / onboarding emails (#307, #2406) so every
    # transactional mail from this product looks like one product.
    #
    # ⛔ ONLY THE DYNAMIC VALUE IS ESCAPED. The paragraphs are OUR copy (the
    # owner's, transcribed above), so they are interpolated literally — escaping
    # them would turn every apostrophe into `&#x27;` and make the golden file
    # unreadable at exactly the place a reviewer must read it. `name` is the one
    # value that comes from data, and it is escaped.
    nm = html.escape(name)
    capacity = html.escape(add_capacity_url(), quote=True)
    docs = html.escape(STORAGE_DOCS_URL, quote=True)
    short = SHORT_VERSION.format(changes=changes).replace(
        "**nothing is lost or deleted.**",
        "<strong style=\"color:#cbd5e1;\">nothing is lost or deleted.</strong>")
    return f"""\
<div style="background:#060b14;padding:32px 16px;font-family:Helvetica,Arial,sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#0d1a2d;border:1px solid #1e293b;border-radius:12px;padding:28px;">
    <p style="color:#e2e8f0;font-size:16px;margin:0 0 16px;">Hi {nm},</p>
    <p style="color:#e2e8f0;font-size:16px;font-weight:600;margin:0 0 16px;">{STATUS_LINE.format(n=pct)}</p>
    <p style="color:#94a3b8;font-size:14px;margin:0 0 20px;">{short}</p>
    <a href="{capacity}" style="display:inline-block;background:#06b6d4;color:#04121a;text-decoration:none;font-weight:600;padding:12px 24px;border-radius:8px;">{ADD_CAPACITY_CTA}</a>
    <hr style="border:none;border-top:1px solid #1e293b;margin:24px 0;">
    <h3 style="color:#e2e8f0;font-size:15px;margin:0 0 12px;">{WHY_HEADING}</h3>
    <p style="color:#94a3b8;font-size:14px;margin:0 0 12px;">{WHY_POSTGRES}</p>
    <p style="color:#94a3b8;font-size:14px;margin:0 0 12px;">{WHY_GRAPH}</p>
    <p style="color:#94a3b8;font-size:14px;margin:0 0 12px;">{WHY_RESERVED}</p>
    <h3 style="color:#e2e8f0;font-size:15px;margin:20px 0 12px;">{NOTICE_HEADING}</h3>
    <p style="color:#94a3b8;font-size:14px;margin:0 0 12px;">{KEEPS_WORKING.format(fetched=fetched)}</p>
    <p style="color:#94a3b8;font-size:14px;margin:0 0 12px;">{CAN_ADD}</p>
    <p style="margin:20px 0 0;"><a href="{docs}" style="color:#06b6d4;font-size:13px;text-decoration:none;">{DOCS_LINK}</a></p>
  </div>
</div>"""


def evaluate(org_id: str, *, limits: dict | None = None,
             reading: Any = None) -> tuple[Notice | None, str | None]:
    """Decide whether this account is over a threshold — from the GATE's figures.

    Returns ``(notice, None)`` when a notice is due, else ``(None, reason)`` with
    a named reason (``no-allowance`` / ``no-reading`` / ``below-threshold`` / …).
    The reason is the log line's whole content, so an operator can tell "not near
    the cap" from "the gauge is broken" without a second probe.

    ⛔ NOTHING IS GUESSED. A missing allowance (the `free`/`anon` tiers carry no
    hot window — `quota._arm_graph_storage` deliberately leaves them unarmed), an
    unreadable allowance, and a reading the meter could not produce all resolve to
    NO EMAIL. `reading`/`limits` exist as seam parameters for tests and for a
    caller that has already paid for the resolution; the production default reads
    both from the gate's own instruments.
    """
    if limits is None:
        limits = quota.resolve_org_limits(org_id)
    allowance = limits.get(ALLOWANCE_KEY)
    if allowance is None:
        # An UNARMED tier: no hot window, so the email's sentence ("…included in
        # your plan") has no figure behind it. Nothing to notify.
        return None, "no-allowance"
    try:
        allowance_mb = float(allowance)
    except (TypeError, ValueError):
        return None, "unreadable-allowance"
    if not math.isfinite(allowance_mb) or allowance_mb <= 0:
        return None, "no-allowance"
    if reading is None:
        reading = allowance_gate.read_dimension(DIMENSION, org_id)
    if reading is None:
        # ABSENT (never measured) or a METER FAULT — indistinguishable to a caller
        # that must not guess, and both mean "no percentage". The pass logs this.
        return None, "no-reading"
    used = getattr(reading, "used", reading)
    pct = capacity_pct(used, allowance_mb)
    if pct is None:
        return None, "unreadable-reading"
    threshold = threshold_reached(pct)
    if threshold is None:
        return None, "below-threshold"
    return Notice(org_id=org_id, threshold=threshold, used_mb=float(used),
                  allowance_mb=allowance_mb), None


def claim_notice(cp, org_id: str, threshold: int,
                 previous: int | None) -> bool:
    """Stamp the high-water mark — TRUE only when THIS call moved it.

    A compare-and-swap on the value the caller READ (``previous``): ``None``
    claims a row whose mark is still unset, and a read ``80`` claims only a row
    still holding ``80``. Two passes that both saw ``None`` therefore cannot
    interleave into a lower mark, and a concurrent 100 % stamp is not overwritten
    by a lagging 80 % one.

    ⛔ SEND-THEN-STAMP, like the #2406 onboarding marker: the stamp follows a
    provider ACCEPT, so a failed send leaves the mark alone (retryable) and the
    provider Idempotency-Key collapses the duplicate a crash between the two
    produces. The opposite order would permanently swallow the notice on any
    send failure — the customer would never learn, which is the failure mode this
    email exists to prevent.
    """
    from .supabase_control import set_org_storage_notice_pct

    return set_org_storage_notice_pct(cp, org_id, threshold, previous=previous)


# ── the per-account decision, and the pass over the fleet ────────────────────

#: Columns this module reads from the orgs row. One read, so the marker and the
#: address are always the same snapshot (a mark read from one row and an email
#: from another is a bug with no cause to find).
_ACCOUNT_SELECT = ("id", "name", "email", "storage_notice_pct")


@dataclass(frozen=True)
class Account:
    """The org-row facts one notice needs."""

    org_id: str
    email: str | None
    name: str | None
    notice_pct: int | None


def read_account(cp, org_id: str) -> Account | None:
    """Read one account's address + mark, or ``None`` when the row is gone."""
    rows = cp.query("organizations", select=list(_ACCOUNT_SELECT),
                    filters=[("id", "eq", org_id)])
    if not rows:
        return None
    row = rows[0]
    mark = row.get("storage_notice_pct")
    try:
        mark = int(mark) if mark is not None else None
    except (TypeError, ValueError):
        # An unreadable mark is NO MARK — see supabase_control's reader for why
        # the extra-send direction is the safe one.
        mark = None
    return Account(org_id=org_id, email=row.get("email"),
                    name=row.get("name"), notice_pct=mark)


async def notify_account(org_id: str, *, cp=None, account: Account | None = None,
                         limits: dict | None = None, reading: Any = None,
                         send=None) -> dict:
    """Decide + send + stamp for ONE account. Never raises for a mail problem.

    ``send`` is the awaitable used to deliver the rendered copy; production
    leaves it ``None`` and uses ``email_notify.send_notice_email`` — the ONE
    Resend path, budget-guarded and idempotency-keyed. Tests pass a fake.

    Returns ``{"status": …}`` with one of: ``sent``, ``already-notified``,
    ``no-email``, ``below-threshold``/``no-reading``/``no-allowance`` (the
    evaluate reasons are passed through verbatim so the caller can aggregate
    them), ``skipped`` (send budget), ``failed`` (provider), ``unknown-account``.

    ⛔ EVERY EXIT EXCEPT ``sent`` LEAVES THE MARK ALONE. That is what makes the
    ledger mean "the customer received this", never "we tried".
    """
    from . import email_notify

    if cp is None:
        from .supabase_control import get_control_plane

        cp = get_control_plane()
    if account is None:
        account = await asyncio.to_thread(read_account, cp, org_id)
    if account is None:
        return {"status": "unknown-account", "org_id": org_id}
    if not account.email:
        # A registry/agent-minted org carries no address; the copy greets a
        # PERSON, so there is nobody to greet.
        return {"status": "no-email", "org_id": org_id}
    notice, reason = evaluate(org_id, limits=limits, reading=reading)
    if notice is None:
        return {"status": reason, "org_id": org_id}
    if account.notice_pct is not None and notice.threshold <= account.notice_pct:
        # The high-water mark already covers this threshold — the ledger's whole
        # job, and the check that makes a re-run of a finished pass free.
        return {"status": "already-notified", "org_id": org_id,
                "threshold": notice.threshold,
                "notified": account.notice_pct}
    copy = render(notice.threshold,
                  email_notify.recipient_first_name(None, account.email,
                                                   account.name))
    sender = send
    if sender is None:
        async def sender(c: Copy) -> dict:
            return await email_notify.send_notice_email(
                account.email, c.subject, c.html, c.text,
                idempotency_key=f"capacity-notice:{org_id}:{notice.threshold}",
                label=f"capacity notice {notice.threshold}%")
    result = await sender(copy)
    status = result.get("status") if isinstance(result, dict) else None
    if status != "sent":
        logger.warning(
            "capacity notice: %s for team %s at %s%% (%s MB of %s MB) — mark "
            "left unset, retryable",
            status or "failed", org_id, notice.threshold, notice.used_mb,
            notice.allowance_mb)
        return {"status": status or "failed", "org_id": org_id,
                "threshold": notice.threshold}
    stamped = await asyncio.to_thread(
        claim_notice, cp, org_id, notice.threshold, account.notice_pct)
    if not stamped:
        # Another pass moved the mark between our read and our stamp. The email
        # went out; the provider Idempotency-Key is what makes the pair safe, and
        # the log says so rather than leaving a silent marker mismatch.
        logger.warning(
            "capacity notice: sent to team %s at %s%% but the mark moved "
            "concurrently (provider key deduped any duplicate)",
            org_id, notice.threshold)
    return {"status": "sent", "org_id": org_id, "threshold": notice.threshold,
            "message_id": (result or {}).get("message_id")}


async def sweep_pass(*, cp=None, send=None, limit: int | None = None,
                     enumerate_orgs=None) -> dict:
    """One pass over the fleet: at most ONE notice per account per pass.

    ``enumerate_orgs`` defaults to ``billing._enumerate_billed_orgs`` — the seam
    that CERTIFIES its enumeration complete (`query_all` keyset walk) and RAISES
    rather than publish a partial page. That matters here for the same reason it
    matters to the billing reconcile: a truncated read is not an error, it is a
    shorter list, and a pass that quietly covers half the fleet looks exactly
    like a pass with nothing to do.

    Per-account isolation: one account's unreadable gauge, missing address or
    provider failure costs that account's notice, never the pass.
    """
    summary: dict[str, Any] = {"checked": 0, "sent": 0, "statuses": {}}
    if cp is None:
        from .supabase_control import get_control_plane

        cp = get_control_plane()
    if enumerate_orgs is None:
        from .billing import _enumerate_billed_orgs

        enumerate_orgs = _enumerate_billed_orgs
    try:
        orgs = await asyncio.to_thread(enumerate_orgs, limit)
    except Exception as e:
        logger.warning("capacity notice sweep: enumeration failed (%s) — no "
                       "notices this pass", type(e).__name__)
        return {**summary, "error": "enumeration-failed"}
    for row in orgs:
        org_id = row.get("id") if isinstance(row, dict) else None
        if not org_id:
            continue
        summary["checked"] += 1
        try:
            result = await notify_account(org_id, cp=cp, send=send)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "capacity notice sweep: team %s failed (%s) — continuing",
                org_id, type(e).__name__)
            result = {"status": "error"}
        status = str(result.get("status") or "error")
        summary["statuses"][status] = summary["statuses"].get(status, 0) + 1
        if status == "sent":
            summary["sent"] += 1
    if summary["sent"] or summary["statuses"].keys() - {"below-threshold"}:
        logger.info("capacity notice sweep %s", summary)
    return summary
