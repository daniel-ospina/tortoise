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

⛔ THE CTA OFFERS CAPACITY, NEVER A TIER. `[ Add capacity ]` opens a capacity
REQUEST (:func:`add_capacity_url` → the published support mailbox, subject
pre-filled). A plan-tier link is deliberately absent: a tier change is the wrong
answer to a capacity need (#13989 policy 4 — *"Capacity (overage/add-on) is
offered first; tier changes are for **feature** needs only"*), and offering it
here is the defect this thread fixes. ⚠️ The first cut used the dashboard's
`#/billing` tab, which is a real deep link AND the plan-tier chooser — a guard
pins a URL string, so that reached review before anyone read what the page
renders (#14038 review, P1).

⚠️ THE REPORTED GAP, STATED RATHER THAN GLOSSED: there is **no storage add-on
purchase surface in the product today** — `product/pricing.json` carries the
rates (`billing.storage.dimensions.graph_storage.overage_price_usd_per_gb_month`
= $85.00/GB-mo and `postgres_storage` = $0.30/GB-mo) and
`metering.storage_overage_cost_usd` prices a bill, but nothing sells a top-up.
So the CTA asks a human for capacity, which is what the product can actually
fulfil; the missing purchase flow is filed separately rather than papered over
here (see the PR body). The URL is one function on purpose: when the add-on flow
lands, this file changes and nothing else does.

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
* ⛔ **`{n}` IN THE APPROVED COPY IS THE THRESHOLD CROSSED (80 / 100), NOT THE
  GAUGE READING — and an earlier cut of this note implied otherwise (#14038
  review, P2).** The reading decides WHICH notice fires (the floored
  `capacity_pct`, carried on :class:`Notice` as ``measured_pct``); the copy then
  announces the tier that was reached, which is the fact the subject line claims
  ("Your search layer is at {n}%") and the number the ledger stamps. Rendering
  ``measured_pct`` instead was considered and NOT done: the sentence is the
  owner's verbatim copy (2026-11-20) and "using 97 % of …" is a different promise
  from "is at 80 %", so changing which figure fills a slot in HIS sentence is his
  call, not a lane's. The consequence is stated rather than hidden: a customer
  reading the mail beside the dashboard sees the threshold, not their live share.
  ⚠️ The reading is an ESTIMATE that systematically under-reports (it excludes
  the HNSW index — `graph_storage.VECTOR_INDEX_EXCLUDED`), which pushes this
  notice LATE. ⛔ It is NOT a one-way bound, and an earlier cut of this line said
  "errs LATE, never early" — the meter's own note (`allowance_gate`,
  `#13102` P1-3) states that the systematic exclusions all remove bytes while a
  SAMPLES-based estimate "wobbles both ways" with an UNMEASURED sampling error,
  so a reading can be high and the notice can fire slightly EARLY. Late is the
  intended bias (the percentage is floored too), not a guarantee.

EXACTLY ONE SEND PER THRESHOLD PER ACCOUNT, EVER. The durable ledger is
`organizations.storage_notice_pct`, an integer HIGH-WATER MARK of the highest threshold
ever notified, stamped only after the provider accepts. A high-water mark rather
than one column per threshold, because two independent markers let the emails
land OUT OF ORDER (a 100 % notice, then a stale 80 % pass emailing "at 80 %"
afterwards); with a mark the send is legal only when `threshold > mark`, so the
**LEDGER** is monotone by construction. ⛔ THE INBOX IS NOT — the two are not the
same claim, and only the first one is structural (#14038 review cycle 2). The send
precedes the stamp (send-then-stamp is what makes a failure retryable), so two
CONCURRENT passes can both read `mark=None`, both pass the re-read, and deliver in
the wrong order — "at 100 %" then "at 80 %". The pre-send re-read (:func:`notify_account`)
narrows that to one round trip; it does not close it, and closing it would need a
pre-send reservation (a stamp-first protocol with a compensating unset), which is
NOT taken here — this is a STATED, ACCEPTED RESIDUAL rather than a shelved one.
The LEDGER stays monotone in every case; only the INBOX can show the two notices
in the wrong order, and it needs two live processes reading the same account
within one round trip plus a gauge that CROSSED A THRESHOLD BOUNDARY between
those two reads. ⛔ The condition is a BOUNDARY CROSSING, not a large move: the
percentage is floored and every value in `[80,100)` maps to the 80 % tier, so
99.0 % → 100.1 % (about 1 pp) flips the tier from 80 to 100 and is enough to
deliver "at 100 %" then "at 80 %". An earlier cut of this sentence said
"≥20 pp", which understated the reachability by roughly an order of magnitude.
⛔ REACHABILITY, WITHOUT INVENTING A TOPOLOGY (#14038 review cycle 3, P2 — the
sentence this replaces was REASONED, and `fly.toml` refutes it). Two live
processes are what this needs, and the ordinary path excludes it twice over:
`worker_topology.assert_multi_worker_admissible` refuses N>1 replicas BY DEFAULT
(it warns instead when `TORTOISE_MULTI_WORKER=1` is acknowledged), and a deploy
does not create the second process either — the app runs ONE machine on a
SINGLE-ATTACH volume (`fly.toml`: `[[mounts]] source = "tortoise_api_data"`, with
its own lifecycle note "the volume is single-attach"), so a replacement machine
cannot mount the volume the old process still holds and the old process exits
FIRST. Fly does not promise the overlap either: its config reference describes
`rolling` as replacing the Machines one by one ("each running Machine is taken
down and replaced") and puts the alongside-boot in `bluegreen`, which it also
says "cannot be used for Machines with attached volumes" (`fly.toml` sets
`strategy = "rolling"`). The residual therefore
becomes reachable when a second process is ADMITTED (`TORTOISE_MULTI_WORKER=1`)
or the 2-machine topology lands — which `fly.toml` records as costed and BLOCKED
on runbook §6 — not because deploys are frequent. Its cost is bounded in every
case: two notices in the wrong order, never a lost or duplicated record. The
stamp itself is a
compare-and-swap on the value this pass read (:func:`claim_notice`), so two
processes cannot interleave into a LOWER mark, and the provider Idempotency-Key
(`capacity-notice:{org_id}:{pct}`) collapses the cross-process replay that
survives a crash between send and stamp — inside that key's 24 h idempotency
window only. Resend dedupes a repeated key against "the last **24 hours**"
(`resend.com/docs/dashboard/emails/idempotency-keys`), so a replay that arrives
later than that sends a SECOND copy and then stamps. ⛔ The bound is stated
rather than implied because nothing in this code expires a key: the window is
the PROVIDER's. Two paths can outlive it — a crash-replay slower than the window
(the process down past 24 h), and a ledger that cannot be marked at all, the
latter named in `_ACCOUNT_DRIFT_SOFT`.

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
import time
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

GREETING = "Hi {name},"
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

    ⛔ FLOOR, AND NEVER 100 UNTIL THE RATIO REACHES IT — the same
    discipline `website/apps/dashboard/src/nodeUsage.js` pins for the node nudge
    (#4331): the DISPLAYED percentage may not cross a threshold the level has
    not, so 79.6 % may not read "80 %" (a notice the account has not earned) and
    99.6 % may not read "100 %" while the account is still under its allowance.
    ⛔ THE 100 GUARD IS THE `ratio >= 1.0` BRANCH, not a clamp on the floor: a
    `floor()` of a ratio below 1.0 cannot exceed 99 by itself. An earlier revision
    wrote `min(99, math.floor(ratio * 100))` here, and #14038's mutation pass
    measured that clamp as UNREACHABLE — no mutant of that line changes any
    output, so no test can attribute it, and a second belt that can never fire is
    what the next reader mistakes for the mechanism. Deleted rather than kept
    "just in case": the property is enforced by the early return below — the line a
    mutant has to break to red `test_capacity_pct`'s `(153, 102, 100)` row, where
    removing it reads 150.
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
    return math.floor(ratio * 100)


def threshold_reached(pct: int | None) -> int | None:
    """The HIGHEST threshold this percentage reaches, or ``None``.

    Highest rather than first-unclaimed: an account that jumps from 60 % to 105 %
    between passes gets the 100 % notice only. Sending the 80 % one as well would
    be two emails in one pass. ⛔ It does NOT follow that a later 80 % send is
    IMPOSSIBLE — an earlier cut of this docstring said so, and the module
    docstring refutes it: `claim_notice` keeps the LEDGER monotone (a stamp may
    never LOWER the mark) but the send precedes the stamp, so two CONCURRENT
    passes can still deliver the two notices in the wrong order. "Highest" is what
    makes ONE PASS emit at most one notice; it does not order the inbox.
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
    #: The FLOORED measured share (`capacity_pct`) that selected `threshold`. It is carried so a caller
    #: can state the honest figure, and `render` does NOT read it: the approved copy's `{n}` is the
    #: THRESHOLD this notice announces, not the gauge reading (see the `{n}` note in the module
    #: docstring — the two are different facts and the copy template is the owner's, verbatim).
    measured_pct: int | None = None


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
#: How many notices ONE pass may SEND (default). Not an enumeration cap — see
#: ``sweep_pass``'s docstring — a burst guard on the SHARED Resend budget.
#:
#: ⛔ WHY IT EXISTS (#14038 review, P2). The budget is one counter across every
#: send on an install (``email_notify``: "every Resend send on this budget is
#: counted — invite, OTP, onboarding … and billing", default 100/day). A first
#: fleet-wide ramp would otherwise reserve whatever remains in one burst, and a
#: budget-exhausted INVITE/OTP is dropped with no retry — while a capacity notice
#: that is skipped is simply retried on the next pass.
#:
#: ⛔ THE NUMBER IS PRICED AGAINST ONE PROCESS'S BUDGET, NOT A DAY'S (#14038
#: review, P1). An earlier form of this note derived it per DAY — "25 at a pass
#: every 6 h is FIVE passes a day (one at boot plus four scheduled), so
#: 25 // 5 = 5" — and that premise is FALSE on the topology this module actually
#: runs on. The sweep is armed with a BOOT RUNG
#: (``_capacity_notice_sweep_loop``, whose docstring carries the measurement), and
#: `deploy-hosted.yml` replaces the machine on every `tortoise/**` push to `main`
#: (over the 30 days to 2026-11-20, `gh run list --workflow deploy-hosted.yml`,
#: n=619 runs = **20.6/day**, the MEDIAN gap was **0.48 h**, with a longest run of
#: **83** consecutive gaps under 6 h), so a day holds roughly one boot pass PER
#: DEPLOY rather than one. ⛔ An earlier cut of this parenthesis said "0.19 h … 90"
#: and NO population reproduces that pair — 0.19 h is nearer `main`'s ALL-COMMIT
#: median (0.08 h at n=1,594), which is the wrong population because most commits
#: touch nothing `deploy-hosted.yml` ships; the deploy-run and `tortoise/**`-commit
#: populations agree with each other to within their own denominators: **20.6**
#: (619/30) and **20.5** (615/30) runs per day, 0.47-0.48 h, run 82-83. ⛔ Quoting
#: one rate for both would only hold if the denominator moved between them.
#: The send budget is per-process in the same way
#: (``email_notify``: "The counter is per-process (N replicas × cap) … Counters
#: reset on process restart"), so no daily quotient across passes is a number
#: this tree can compute — and stating one was the defect: it claimed a daily
#: bound that nothing enforced.
#:
#: What this constant DOES bound: ONE process's ONE pass spends at most this
#: many sends, so the pass's own spend is <= 5 of that process's 100/day budget.
#: ⛔ IT IS NEITHER A RESERVATION NOR AN AVAILABILITY GUARANTEE, and that is the
#: whole honest claim — an earlier cut said this "leaves at least 95 for every
#: other send", which the very next sentence refutes. `reserve_send_slot` is a
#: first-come check-then-reserve with no priority (`email_notify` skips WHICHEVER
#: path's send arrives once the counter is at cap): a boot pass arriving at 96/100
#: sends its 4 allowed and leaves ZERO for a concurrent invite or OTP. The guard
#: lowers this notice's exposure from "whatever remains" to "at most 5"; it does
#: not ring-fence anyone else's mail, and it must not be read as doing so.
#: The fleet-wide aggregate is bounded, and it is not one expression: a process
#: spends at most this cap PER PASS — its boot pass plus every ``interval`` pass
#: it lives through (``_capacity_notice_sweep_loop``), so ~25 for a process alive
#: 24 h at the 6 h default — and across restarts that multiplies, with NO
#: restarts still leaving the periodic passes to spend. ⛔ An earlier cut wrote
#: "restarts × this cap", which is wrong in both directions (0 restarts ≠ 0
#: sends; 9 passes ≠ 1 cap). It stays FINITE regardless because the ledger is
#: once-per-threshold-per-account-ever: an account already marked at the TOP
#: threshold (100 %) short-circuits before the gauge is read (`notify_account`'s
#: fast path is `notice_pct >= max(THRESHOLDS)`). ⛔ An account marked 80 % does
#: NOT short-circuit there — an earlier cut of this note said it still returned
#: `already-notified`, which is FALSE in exactly the case this paragraph is about:
#: if the gauge crossed a tier boundary it legitimately sends the 100 % notice.
#: What bounds the total is the LEDGER, not the fast path: each threshold can be
#: stamped once, ever, so an account receives at most one 80 % and one 100 %
#: notice in its lifetime and every later pass finds nothing left to send. ``NOTICE_MAX_SENDS_ENV`` is an absolute override;
#: if you shorten the interval or raise the send budget, re-price against ONE
#: process's budget — never against a pass count you did not measure.
NOTICE_MAX_SENDS_PER_PASS_DEFAULT = 5
NOTICE_MAX_SENDS_ENV = "TORTOISE_CAPACITY_NOTICE_MAX_SENDS"

#: How long a SENT-BUT-UNSTAMPED (org, threshold) is suppressed from re-entering the send path.
#:
#: ⛔ WHY THIS EXISTS, AND WHAT IT FIXES (#14038 review, P2). The stamp-failure arm deliberately
#: reports ``sent`` so the delivered notice counts against the per-pass cap — correct for ONE pass,
#: but a pass whose ledger WRITES fail while its READS still work then spends that cap on the SAME
#: lowest-id accounts on every interval: the rest of the fleet is never reached (starvation), and
#: those accounts are re-mailed each pass, deduped by the provider only inside its 24 h window and
#: then genuinely duplicated roughly daily. The cap cannot tell "this account owes a send" from "this
#: account was already mailed and we could not record it", so the module remembers the second set
#: itself. In-process on purpose: it is a BURST guard for a broken ledger, not durable state — after a
#: restart the ledger is re-read (and if writes are still broken, the same account is mailed again,
#: which the provider dedupes inside the window). The TTL matches the provider's window so the two
#: mechanisms cannot disagree about what counts as a duplicate.
UNSTAMPED_SUPPRESS_TTL_S = 24 * 60 * 60
_UNSTAMPED: dict[tuple[str, int], float] = {}


def _note_unstamped(org_id: str, threshold: int) -> None:
    """Remember `(org_id, threshold)` was delivered but its mark could not be written."""
    _UNSTAMPED[(org_id, int(threshold))] = time.monotonic()


def _unstamped_recently(org_id: str, threshold: int) -> bool:
    """TRUE while a delivered-but-unrecorded `(org, threshold)` is still inside the TTL."""
    seen = _UNSTAMPED.get((org_id, int(threshold)))
    if seen is None:
        return False
    if (time.monotonic() - seen) >= UNSTAMPED_SUPPRESS_TTL_S:
        _UNSTAMPED.pop((org_id, int(threshold)), None)
        return False
    return True

#: Ops cap on accounts PROCESSED per pass.
#:
#: ⛔ REMOVED (#14038 review, P2). It forwarded to `_enumerate_billed_orgs`,
#: whose truncation is `candidates[:limit]` over a keyset walk ordered by id — a
#: DETERMINISTIC lowest-id prefix, so the same first N accounts were re-processed
#: (as cheap no-ops) on every pass and the rest of the fleet was never reached. It
#: re-created, one layer up, exactly the silent-partial-pass failure the
#: docstring above says the certified walk exists to prevent, and the send-budget
#: guard (#1138) is the real limiter for a large first pass anyway.


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


def max_sends_per_pass() -> int:
    """How many notices one pass may SEND — ``NOTICE_MAX_SENDS_ENV`` or the default."""
    import os

    raw = os.environ.get(NOTICE_MAX_SENDS_ENV)
    if raw is None or not str(raw).strip():
        return NOTICE_MAX_SENDS_PER_PASS_DEFAULT
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a whole number — using %d",
                       NOTICE_MAX_SENDS_ENV, raw,
                       NOTICE_MAX_SENDS_PER_PASS_DEFAULT)
        return NOTICE_MAX_SENDS_PER_PASS_DEFAULT
    if value < 1:
        logger.warning(
            "%s=%r is not positive — using %d (0 would disable the notices "
            "entirely, which is what TORTOISE_CAPACITY_NOTICE=0 is for)",
            NOTICE_MAX_SENDS_ENV, raw, NOTICE_MAX_SENDS_PER_PASS_DEFAULT)
        return NOTICE_MAX_SENDS_PER_PASS_DEFAULT
    return value


#: Where a capacity request goes TODAY: the published support address.
#:
#: ⛔ THIS IS NOT A TIER LINK, AND THE SHIPPED FORM IS A DELIBERATE CHOICE
#: (#14038 review, P1). The first cut pointed the CTA at the dashboard's
#: `#/billing` tab. That IS a real deep link, but the page it opens is a
#: current-plan card followed by a `<h3>Plans</h3>` grid whose every other card
#: renders an upgrade button — so the only action in an email about a CAPACITY
#: need landed the customer on the plan-tier chooser. #13998 requirement 2 and
#: #13989 policy 4 (owner, 2026-11-20 — *"Capacity (overage/add-on) is offered
#: first; tier changes are for feature needs only"*) forbid exactly that, and no
#: test can catch it: a guard pins the URL string, not what the string renders.
#:
#: No add-on/overage PURCHASE surface exists yet (there is no route, no SKU
#: checkout — the rates are in `product/pricing.json`, the fulfilment is a human),
#: so the honest destination is the human path that can actually add capacity.
#: When the add-on flow lands, this ONE function changes; nothing else does.
CAPACITY_REQUEST_EMAIL = "support@premiselabs.co"


def add_capacity_url() -> str:
    """Where `[ Add capacity ]` goes: a capacity REQUEST, never a tier page.

    A `mailto:` rather than an app route, for the reason in
    ``CAPACITY_REQUEST_EMAIL``: the app has no capacity-purchase surface, and the
    surface it does have is the one thing this email must not offer. The subject
    is pre-filled so the request arrives already classified, which is the whole
    benefit of routing it to a mailbox rather than to a page.
    """
    from urllib.parse import quote

    subject = quote("Add capacity to my Tortoise workspace")
    return f"mailto:{CAPACITY_REQUEST_EMAIL}?subject={subject}"


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
    # ⛔ BOTH DESTINATIONS ARE IN THE TEXT HALF, not only in the HTML one
    # (#14038 fix lane, P2). The two labels are the owner's words; a label with
    # no address is a dead action, and until this line existed the plain-text
    # alternative shipped `[ Add capacity ]` and the docs label with NO
    # destination at all — so a text-only client, a screen reader on the text
    # part, or any gateway that strips HTML received an email whose only action
    # went nowhere. #13998 requirement 2 is that the CTA *resolves* and
    # requirement 4 that the docs page is *linked*; neither the wording nor the
    # destination changes here, only the half of the message that was missing it.
    #
    # The URLs come from the same two functions the HTML half uses
    # (`add_capacity_url()` / `STORAGE_DOCS_URL`), so the halves cannot drift
    # onto different pages, and `<…>` is the plain-text convention for a bare
    # URI (RFC 6068 §2 for the mailto) that mail clients auto-link. The sibling
    # emails in `email_notify` do the same: `_invite_text` renders
    # `Accept: {link}` and `_onboarding_text` renders `book a call {link}`.
    return (
        f"{GREETING.format(name=name)}\n\n"
        f"{STATUS_LINE.format(n=pct)}\n\n"
        f"{SHORT_VERSION.format(changes=changes).replace('**', '')}\n\n"
        f"[ {ADD_CAPACITY_CTA} ]\n"
        f"<{add_capacity_url()}>\n\n"
        "---\n\n"
        f"{WHY_HEADING}\n\n"
        f"{WHY_POSTGRES}\n\n"
        f"{WHY_GRAPH}\n\n"
        f"{WHY_RESERVED}\n\n"
        f"{NOTICE_HEADING}\n\n"
        f"{KEEPS_WORKING.format(fetched=fetched)}\n\n"
        f"{CAN_ADD}\n\n"
        f"[ {DOCS_LINK} ]\n"
        f"<{STORAGE_DOCS_URL}>\n"
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
    <p style="color:#e2e8f0;font-size:16px;margin:0 0 16px;">{GREETING.format(name=nm)}</p>
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
        try:
            limits = quota.resolve_org_limits(org_id)
        except Exception as exc:  # a gauge fault is NOT a decision
            # ⛔ THE INSTRUMENT THIS MODULE PUBLISHES A REASON FOR. `notify_account`
            # documents a CLOSED list of statuses a caller aggregates on, and names
            # `unreadable-allowance` for "the gauge pair could not be read". Left
            # unguarded, a raise here reached the caller as `sweep_pass`'s generic
            # `error` — a different name for the same state, with the cause reduced
            # to a log line nothing aggregates, so the documented status was
            # unreachable for the very failure it names. Nothing is sent and the
            # mark is untouched, exactly as for every non-`sent` exit.
            logger.warning(
                "capacity notice: the allowance for %s could not be read (%s) "
                "— no email", org_id, _logged_reason(exc))
            return None, "unreadable-allowance"
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
        try:
            reading = allowance_gate.read_dimension(DIMENSION, org_id)
        except Exception as exc:  # the meter half, at THIS seam only
            # ⛔ NOT A METER-FAULT GUARD — an earlier cut of this comment claimed it was,
            # and the log line below still said "the meter … could not be read", which was
            # the same false attribution (measured: a meter patched to raise answers
            # `no-reading`, never this status). `allowance_gate.read_dimension` is a
            # documented NON-RAISING seam: it catches the METER's own exception and
            # answers `None` ("NOT A GATING API … never raises for a meter failure"), so
            # a meter outage reaches the `reading is None` branch BELOW and cannot arrive
            # here. What this guard catches is `read_dimension` ITSELF failing before it
            # reaches the meter — an undeclared dimension (`declared[dimension]` raising
            # `KeyError`) — a WIRING fault in this module, not a gauge fault. Both the
            # comment and the log line now name that seam fault, never a meter one.
            logger.warning(
                "capacity notice: the meter READER (read_dimension) for %s failed "
                "before it could answer (%s) — no email", org_id, _logged_reason(exc))
            return None, "unreadable-reading"
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
                  allowance_mb=allowance_mb, measured_pct=pct), None


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
    produces — inside the key's 24 h idempotency window only. ⛔ THAT COLLAPSE IS
    BOUNDED BY THE PROVIDER, NOT HERE: Resend keeps
    a key for "the last 24 hours"
    (`resend.com/docs/dashboard/emails/idempotency-keys`), so a crash-replay that
    arrives after the window sends a second copy and then stamps. Nothing in this
    module expires a key, so the window is stated rather than assumed. The
    opposite order would permanently swallow the notice on any send failure — the
    customer would never learn, which is the failure mode this email exists to
    prevent.
    """
    from .supabase_control import set_org_storage_notice_pct

    return set_org_storage_notice_pct(cp, org_id, threshold, previous=previous)


# ── the per-account decision, and the pass over the fleet ────────────────────

#: The org-row facts one notice needs. `deleted_at`/`suspended_at` are read for
#: the LIVENESS predicate, not for display: the fleet enumeration
#: (`billing._enumerate_billed_orgs`) deliberately does NOT filter soft-deleted
#: orgs — "a soft-deleted org can still be BILLED, and excluding it would hide a
#: live charge" (`billing.py`) — which is the right predicate for a Stripe mirror
#: and the WRONG one for a customer email. The copy says "nothing is lost or
#: deleted. Everything you've saved stays searchable", and the stamp is once-ever,
#: so mailing an account inside its deletion grace window would both state
#: something false and burn the one notice it can ever get (#14038 review, P2 —
#: found independently by two reviewers). #1853's suspension is the same class:
#: the org's calls 403 but its inbox is still open to us.
#:
#: ⛔ ONE RAW SELECT, and the two failure directions are DIFFERENT (#14038 review
#: cycle 2): `storage_notice_pct` and `deleted_at` must FAIL CLOSED (an unreadable
#: ledger must not re-send; a deletion column is never additive — see
#: `_ACCOUNT_DRIFT_SOFT`), while `suspended_at` is tier 0015 and must DEGRADE.
_ACCOUNT_SELECT = ("id", "name", "email", "storage_notice_pct", "deleted_at",
                   "suspended_at", "customer_email")

#: Columns in `_ACCOUNT_SELECT` whose ABSENCE must DEGRADE, not stop the pass.
#: `suspended_at` is migration **0015** — an additive tier (`_ORG_ADDITIVE_0015_TIER`),
#: NOT a base column, so the #1096 doctrine applies: a schema one migration behind
#: reads it as NULL and stays un-suspended instead of taking the feature down.
#:
#: ⛔ `customer_email` is here for the same reason (`0012_teams_billing_columns`,
#: renamed to organizations by `20260915000001_tenancy_team_to_org`): it is the
#: SECOND address in the recipient chain, not the first, so its absence costs the
#: chain one rung and must never cost the pass — a schema older than 0015 is
#: older than the billing tier too, and refusing to notify every account because a
#: FALLBACK column is missing would be the same failure in the other direction.
#:
#: ⛔ `storage_notice_pct` and `deleted_at` are DELIBERATELY NOT here: the mark is
#: the LEDGER, and an unreadable ledger must refuse to send
#: (`notice-mark-unavailable`) rather than mean "never notified" — the provider
#: idempotency key collapses only a replay of the SAME threshold, and only inside
#: that key's 24 h idempotency window (beyond it the provider sends again), so an
#: unledgered pass re-sends the one-shot notice every interval forever. Deletion is the
#: fail-closed direction too (#1096: a deletion column is never an additive tier).
#: A single PostgREST 400 does not say WHICH column it rejected, which is why
#: `read_account` retries without these and re-raises when the retry also fails.
_ACCOUNT_DRIFT_SOFT = ("suspended_at", "customer_email")

#: What a REJECTED PROJECTION looks like from this seam (#14038 review, P1).
#:
#: The rejection is what licenses the degrade in `read_account`, so it must be
#: told apart from a TRANSIENT read failure — retrying without `suspended_at`
#: after a timeout or a 5xx would read a suspended org as un-suspended, mail it
#: and burn its once-ever stamp (the stamp is not retryable).
#:
#: PostgREST answers an unknown column with **HTTP 400**, and its reference
#: documents TWO body shapes behind that status: `PGRST204` ("the column
#: specified in the `columns` query parameter is not found") and PostgreSQL's own
#: `42703` (`column organizations.suspended_at does not exist`), which PostgREST
#: forwards as a 400 because `undefined_column` falls in its catch-all
#: PostgreSQL row. ⛔ WHICH ONE A GIVEN `select=` REJECTION CARRIES IS NOT A
#: CLAIM THIS CODE CAN MAKE, and an earlier cut made it anyway: it concluded that
#: `42703` "is the shape it would see" while `_orgs_row_fail_soft` states
#: `PGRST204` for the same read. The reference pins `PGRST204` to the `columns=`
#: PARAMETER, not to `select=`, and `SupabaseControlPlane.query` renders only the
#: STATUS while discarding the body — so neither code is text this seam can ever
#: emit, and neither may be attributed to it (#14038 fix lane, same class as
#: the review's R2). The marker
#: list therefore accepts the STATUS and BOTH shapes. A bare `HTTP 400` from this
#: one-row, `id`-filtered GET is the drift shape — a PATCH/POST body rejection or
#: a bad filter cannot produce one here, and if a 400 has any other cause the
#: retry 400s too and the ORIGINAL error is re-raised. A transport failure or a
#: 5xx states a different text and is NOT drift: it propagates at once.
_SCHEMA_REJECTION_MARKERS = ("pgrst204", "42703", "does not exist",
                             "undefined column", "http 400")


def _logged_reason(err: BaseException) -> str:
    """The failure as a log line can carry it: TYPE + a bounded MESSAGE.

    The type alone cannot separate the two failures this seam retries differently —
    `supabase_control` raises `RuntimeError` for a REJECTED COLUMN and for a
    TRANSPORT error alike, and `_rejected_the_projection` decides on the MESSAGE
    text. So the operator-facing line keeps the message (collapsed to one line and
    bounded, since a PostgREST body can be long), and the docstring that promises
    "the log line names which" is true rather than aspirational (#14038 review).
    """
    text = " ".join(str(err).split())
    return f"{type(err).__name__}: {text[:240]}" if text else type(err).__name__


def _rejected_the_projection(err: BaseException) -> bool:
    """True only for a read whose error STATES a rejected column.

    Fails CLOSED on anything it does not recognise: an unknown error is treated
    as transient, so the account reports `notice-mark-unavailable` and is retried
    on the next pass instead of being mailed as if it were un-suspended.
    """
    text = str(err).lower()
    return any(marker in text for marker in _SCHEMA_REJECTION_MARKERS)


@dataclass(frozen=True)
class Account:
    """The org-row facts one notice needs."""

    org_id: str
    email: str | None
    name: str | None
    notice_pct: int | None
    deleted: bool = False
    suspended: bool = False
    #: The Stripe mirror's billing contact (0012, `billing.mirror_subscription`'s
    #: `customer_email=` twin) — the SECOND address in the chain, and the one a
    #: SESSION-lane org usually has when `email` is NULL. See `_notice_recipient`.
    customer_email: str | None = None


def read_account(cp, org_id: str) -> Account | None:
    """Read one account's address, mark and liveness columns; ``None`` if gone.

    ONE select, so the mark, the address and the two liveness columns are one
    snapshot. The retry exists because those columns carry DIFFERENT failure
    contracts (#1096): a schema missing `suspended_at` (0015, additive) must
    degrade to "not suspended" and keep the pass alive, while a schema missing
    `storage_notice_pct` (the ledger) or `deleted_at` (base, fail-closed) must
    stop this account's send. A single 400 does not say which, so the retry
    without the drift-soft columns separates the two — and its failure re-raises
    the original error into `notify_account`'s ``notice-mark-unavailable`` path.

    ⛔ BUT THE RETRY IS LICENSED ONLY BY A REJECTION (#14038 review, P1). The
    first read may also fail for a reason that has nothing to do with the schema
    — a connection reset, a 5xx, a timeout — and retrying THAT without
    `suspended_at` succeeds, so a suspended org reads as un-suspended, is mailed
    (the copy offers a capacity purchase to an account that cannot use it) and
    has its once-ever stamp burnt. So the degrade happens only when the first
    error states a rejected column (`_rejected_the_projection`); anything else
    propagates to `notice-mark-unavailable` and is retried next pass.
    """
    try:
        rows = cp.query("organizations", select=list(_ACCOUNT_SELECT),
                        filters=[("id", "eq", org_id)])
    except Exception as first:
        if not _rejected_the_projection(first):
            raise
        soft_select = [c for c in _ACCOUNT_SELECT if c not in _ACCOUNT_DRIFT_SOFT]
        try:
            rows = cp.query("organizations", select=soft_select,
                            filters=[("id", "eq", org_id)])
        except Exception:
            raise first from None
        logger.warning(
            "capacity notice: the orgs read for %s rejected %s (schema older "
            "than 0012/0015) — retried without %s, so the account reads as NOT "
            "suspended AND the recipient chain loses its billing-contact rung "
            "(it falls to the owner's GoTrue address); the ledger/base columns "
            "are still required (%s)",
            org_id, list(_ACCOUNT_DRIFT_SOFT), list(_ACCOUNT_DRIFT_SOFT),
            type(first).__name__)
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
                    name=row.get("name"), notice_pct=mark,
                    deleted=bool(row.get("deleted_at")),
                    suspended=bool(row.get("suspended_at")),
                    customer_email=row.get("customer_email"))


def _owner_email(org_id: str, cp) -> str | None:
    """The account's ACTIVE OWNER address via GoTrue — the THIRD chain rung.

    ``supabase_control.owner_email`` is the seam ``hosted_api._billing_owner_email``
    already uses for the same question, so this module does not invent a second
    resolver. ⛔ FAIL-SOFT AND ``None``-POSSIBLE: that function swallows the GoTrue
    admin transport error but its ``owner_user_id`` control-plane read sits
    OUTSIDE its guard (``hosted_api._billing_owner_email`` says so, and catches
    here for exactly that reason), so any exception degrades this rung to ``None``
    rather than costing the account its notice. Blocking I/O — callers run it off
    the loop.
    """
    from .supabase_control import owner_email

    try:
        return owner_email(cp, org_id)
    except Exception as e:  # degrade the RUNG, never the account's notice
        # ⛔ LOGGED, NOT SWALLOWED SILENTLY: this rung answers ``None`` for TWO
        # different states — no address, and an address that could not be READ —
        # and `notify_account`'s ``no-email`` line is what an operator reads to
        # diagnose a fleet-wide silence. Without this line a GoTrue/control-plane
        # outage is indistinguishable from every account genuinely having no
        # address, and the ``no-email`` count would be read as the second when it
        # is the first (#14038 fix lane — the same class as the docstring that
        # named one of two ``None`` causes).
        logger.warning(
            "capacity notice: the owner-address lookup for %s failed (%s) — "
            "rung 3 degrades to no address for this pass; this is NOT the same "
            "state as an account that has no address at all",
            org_id, _logged_reason(e))
        return None


def _notice_recipient(account: Account, cp) -> str | None:
    """Resolve WHO to mail — the chain, in the order the checkout already uses.

    ⛔ THE BARE COLUMN IS NOT THE ANSWER (#14038 review, P1). This notice used to
    grep `organizations.email` and stop: a NULL was reported as a plain ``no-email``
    status, so the account silently never received the one notice it can ever get.
    That column is populated ONLY by the lanes that pass `p_email` to
    `provision_team` (`/v1/register`, the Edge Function) — every SESSION-lane
    create (`create_org`, the onboarding sub-org, the new-org checkout webhook)
    passes none, so it is NULL **exactly where a session member is the one who
    upgraded** (`hosted_api._billing_owner_email` states this at length), which is
    precisely the paying, capacity-holding population these two thresholds exist
    to tell.

    This chain shares `hosted_api._billing_customer_email`'s two ENDPOINTS — the
    account's own ``email`` and the ACTIVE OWNER's GoTrue address — and
    deliberately INSERTS one rung between them, the Stripe-mirrored
    ``customer_email``. It is therefore NOT that chain's order, and an earlier cut
    of this sentence said it was; the distinction matters because a maintainer
    changing one chain would rely on that claim to keep the other in step, while
    `_billing_customer_email` never reads ``customer_email`` at all (only the
    Stripe mirror writes it, ``hosted_api`` ~:48696, ~:49293):

    1. ``organizations.email`` — the account's own recorded contact.
    2. ``organizations.customer_email`` — the Stripe mirror's billing contact
       (0012). Already on the row this pass read, so it costs ZERO round trips;
       this rung is what closes the common session-lane case.
    3. ``supabase_control.owner_email`` — the ACTIVE OWNER's GoTrue address
       (``#1765``'s promoted property, the rung `_billing_owner_email` calls).
       Two BLOCKING calls, so it is reached ONLY for an account whose notice is
       already known to be due.

    ⛔ "DUE" IS NOT "``max_sends``", AND AN EARLIER CUT SAID IT WAS — "at most
    ``max_sends`` times per pass" — which is MEASURED FALSE (#14038 fix lane).
    `sweep_pass` stops calling `notify_account` once its ``sent`` count reaches the
    cap; a due account that does NOT send (no address on any rung, a budget-skipped
    send, a provider failure) never increments ``sent``, so that loop's `continue`
    never fires for it and the pass walks straight past it, paying the rung again
    for the next one. Measured: three due addressless accounts at ``max_sends=1``
    pay it THREE times, asserted by
    ``test_the_owner_rung_is_paid_once_per_DUE_account_not_once_per_send``. The
    true bound is ONE CALL PAIR PER DUE ACCOUNT PER PASS — the DUE POPULATION, not
    the send cap. The cap bounds SPEND; it has never bounded this read.

    ⛔ AND A RUNG IS REACHED WHEN THE ONE ABOVE IT IS NOT AN ADDRESS, not when it
    is merely non-empty (#14038 review, mutation pass, P2). `email` and
    `customer_email` are contact columns, and `email` holds non-addresses in this
    same codebase (`"api"`, a Supabase user UUID — `hosted_api._billing_email_like`
    documents both, and #1765 demoted that column as stale-prone), so a truthiness
    test on rung 1 SHADOWS a deliverable rung 2: the send fails, the mark is never
    stamped, and every later pass retries the same undeliverable string — reported
    as ``failed``, never as ``no-email``, which is the exact outcome the rung order
    exists to remove. The gate is `_deliverable_address` below, which REUSES
    `hosted_api._billing_email_like` rather than respelling it here.

    ⛔ RESIDUAL, STATED RATHER THAN IMPLIED: an account with none of the three
    (a registry/agent-minted org carries no address at all) still answers
    ``no-email`` and is still not mailed — the copy greets a PERSON, so there is
    nobody to greet. What changed is that the RUNG ORDER now lets the notice reach
    the account that HAS an address anywhere on these surfaces, instead of only the
    ones whose row happens to carry the one column this module used to read.

    ⛔ AND FOR THAT ACCOUNT THE COST IS PER-PASS, NOT ONCE: it is never stamped
    (nothing was sent), so every later pass re-resolves it and pays rung 3 again.
    Comparable notification systems close that with a durable suppression ledger
    consulted BEFORE a retry and bound the transient case with backoff; this
    module does not adopt one here, because its only ledger is
    `organizations.storage_notice_pct`, whose contract is sent-then-stamp after
    the provider ACCEPTS — an "unsendable" mark would be a SECOND ledger, i.e. a
    schema change and a decision for #13998's owner rather than for a fix lane.
    """
    own = _deliverable_address(account.email)
    if own:
        return own
    billing = _deliverable_address(account.customer_email)
    if billing:
        # ⛔ A FALLBACK IS A DELIVERY DECISION, so it is RECORDED — by RUNG, never
        # by address (a log is not the place to copy a customer's address). The
        # account's own column was empty OR NOT AN ADDRESS, so this notice reaches a
        # different mailbox than the account recorded; whoever reads a bounce, or
        # asks "who was actually told", cannot recover that from the send path alone.
        logger.info(
            "capacity notice: team %s carries no deliverable organizations.email — the "
            "notice resolves on the SECOND rung of the recipient chain "
            "(organizations.customer_email, the Stripe billing contact)",
            account.org_id)
        return billing
    resolved = _deliverable_address(_owner_email(account.org_id, cp))
    if resolved:
        logger.info(
            "capacity notice: team %s carries no deliverable organizations.email and no "
            "customer_email — the notice resolves on the THIRD rung of the "
            "recipient chain (the active owner's GoTrue address)",
            account.org_id)
        return resolved
    return None


def _deliverable_address(value: object) -> str | None:
    """The address to hand the mailer, or ``None`` when *value* is not one.

    ⛔ ONE DEFINITION, IMPORTED: `hosted_api._billing_email_like` is the codebase's
    shape gate for this exact question (single ``@``, non-empty local part and
    domain, no interior whitespace, surrounding whitespace trimmed), and its
    history — #4504, #1765 — is why a truthy string is not an address: the same
    writers put ``"api"`` and Supabase user UUIDs into these columns. Respelling
    it here would be a second owner of one rule, which is how the two drift, so
    the billing chain's predicate is reused even though that couples this module
    to it.

    The import is INSIDE the function on purpose: `hosted_api` imports this module
    (lazily, in the sweep loop), so a module-level import here would be a cycle.
    """
    from .hosted_api import _billing_email_like

    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if _billing_email_like(trimmed) else None


async def notify_account(org_id: str, *, cp=None, account: Account | None = None,
                         limits: dict | None = None, reading: Any = None,
                         send=None) -> dict:
    """Decide + send + stamp for ONE account. Never raises for a mail problem.

    ``send`` is the awaitable used to deliver the rendered copy; production
    leaves it ``None`` and uses ``email_notify.send_notice_email`` — the ONE
    Resend path, budget-guarded and idempotency-keyed. Tests pass a fake.

    Returns ``{"status": …}`` with one of: ``sent``, ``already-notified``,
    ``no-email`` (a notice was DUE and the whole recipient chain —
    `organizations.email`, then `customer_email`, then the owner's GoTrue address;
    see `_notice_recipient` — resolved nothing, so it RANKS BELOW the gauge
    checks: an account that is also unarmed or below threshold reports that
    reason instead, because it is the more actionable of two true statements),
    ``deleted-org``, ``suspended-org``, ``notice-mark-unavailable``
    (the account ROW could not be read — the ledger column has not been migrated,
    a base column is missing, or the read failed TRANSIENTLY, which `read_account`
    refuses to treat as schema drift; the log line carries the exception TYPE and
    its bounded MESSAGE, which is the datum `_rejected_the_projection` itself
    decides on — not the type alone, because `supabase_control` raises
    `RuntimeError` for BOTH a rejected column and a transport failure),
    ``unreadable-allowance`` and ``unreadable-reading`` (a gauge could not be
    read: ``unreadable-allowance`` is the ALLOWANCE instrument raising — which
    includes `OrgNotFoundError`, a STATE rather than a fault (#9567), raised
    when the org record is not there to read: in the chain below, the account
    read has ALREADY ruled out the plain absence and reports it as
    ``unknown-account``, so for THAT case what is left here is the row vanishing
    between the two reads — or its figure being unusable (a non-number). It is
    not an exhaustive account of the raises that land here: any other exception
    out of the instrument takes the same branch (a transport `RuntimeError`
    re-raised out of `_orgs_row_fail_soft`, an unreadable or negative
    `pricing.json` tier figure surfacing as `QuotaCheckError`), and in REGISTRY
    mode the two reads are of different stores, so there is no single row to
    vanish. ``unreadable-reading`` is a reading
    that cannot be turned into a percentage (or ``read_dimension`` itself
    failing). ⛔ A METER that raises is NOT this status: `allowance_gate.read_dimension`
    is a documented non-raising seam that catches the meter's exception and answers
    ``None``, so a meter outage reaches this caller as ``no-reading`` — the same
    status as "never measured", and this module does not claim to tell them apart;
    returned verbatim, and listed
    here because the closed list below is what a caller aggregates on), ``below-threshold``/
    ``no-reading``/``no-allowance`` (the
    evaluate reasons are passed through verbatim so the caller can aggregate
    them), ``skipped`` (send budget), ``failed`` (provider), ``unknown-account``.

    ⛔ EVERY EXIT EXCEPT ``sent`` LEAVES THE MARK ALONE. That is what makes the
    ledger mean "the customer received this", never "we tried".

    ⛔ NO BLOCKING I/O ON THE EVENT LOOP (#14038 review, P1). The gauge pair
    (`quota.resolve_org_limits` + the meter) and the marker CAS are synchronous
    PostgREST/Redis calls; a serial pass over the fleet on the loop would stall
    every request. Both go through `asyncio.to_thread` — the same discipline the
    enumeration uses.
    """
    from . import email_notify

    if cp is None:
        from .supabase_control import get_control_plane

        cp = get_control_plane()
    if account is None:
        try:
            account = await asyncio.to_thread(read_account, cp, org_id)
        except Exception as e:
            # ⛔ NO SEND WITHOUT A READABLE LEDGER. A missing `storage_notice_pct`
            # column (code deployed before its migration) makes PostgREST reject the
            # `select=`, and the seam discards the body and renders the STATUS alone —
            # so this reader sees a bare **HTTP 400**, never `PGRST204`/`42703` (see
            # `read_account`, and the doctrine note above `read_account` that forbids
            # attributing either body code to this seam); sending anyway would re-send
            # the same one-shot notice on
            # EVERY pass, and the provider key only collapses a replay of the
            # SAME threshold inside its 24 h window — it cannot make a 6-hourly
            # re-send safe. So the
            # account is reported, loudly and by name, and nothing is sent until
            # the migration applies (see the migration's deploy-order note).
            logger.error(
                "capacity notice: the account row for team %s is unreadable "
                "(%s) — nothing is sent for this account. `suspended_at` already "
                "degrades on its own (0015, additive — read_account retries "
                "without it), so what reaches here is either a FAIL-CLOSED "
                "column (the ledger storage_notice_pct / 20261120000001, or the "
                "base deleted_at) or a TRANSIENT read failure, which "
                "`read_account` refuses to read as schema drift (#14038 review, "
                "P1). Both are retried on the next pass; neither sends.",
                org_id, _logged_reason(e))
            return {"status": "notice-mark-unavailable", "org_id": org_id}
    if account is None:
        return {"status": "unknown-account", "org_id": org_id}
    if account.deleted:
        # Inside the deletion grace window (or cancelled). The stamp is
        # once-EVER, so mailing here would spend the account's only notice on a
        # workspace whose data is on its way out — and the copy would tell it
        # "nothing is lost or deleted" while it is (see `_ACCOUNT_SELECT`).
        return {"status": "deleted-org", "org_id": org_id}
    if account.suspended:
        # #1853: a suspended org's calls 403 but its inbox is still open to us.
        # The notice offers a capacity purchase; offering it to an account that
        # cannot use the capacity is noise at best, and the stamp is once-ever.
        return {"status": "suspended-org", "org_id": org_id}
    if account.notice_pct is not None and account.notice_pct >= max(THRESHOLDS):
        # The high-water mark is already at the TOP threshold: no threshold can
        # be left to send, so skip the fleet's most expensive step (two sync
        # gauge reads) entirely — the fast path the note below refers to.
        return {"status": "already-notified", "org_id": org_id,
                "notified": account.notice_pct}
    # ⛔ OFF THE EVENT LOOP. ``evaluate`` is sync and performs TWO blocking
    # control-plane reads (`quota.resolve_org_limits` → `_orgs_row_fail_soft` →
    # `cp.query`, and `allowance_gate.read_dimension` → `cp.query`), and this
    # module's pass runs as an asyncio task INSIDE the API process
    # (`hosted_api._capacity_notice_sweep_loop`, armed by default). Called
    # inline, every account's two round-trips stalled the whole event loop for
    # the length of a fleet pass — the very thing `_cp_offload` exists for
    # (#3498). The two reads beside it (`read_account`, `claim_notice`) were
    # already offloaded; the decision between them was not.
    #
    # ⛔ AND AN ACCOUNT ALREADY AT THE TOP THRESHOLD NEVER GETS HERE: no threshold
    # can be left to send, so the two gauge reads are skipped entirely (the fast
    # path above). Before it, a fully-notified account paid both reads on every
    # pass, forever (#14038 review, P1 aggravator).
    notice, reason = await asyncio.to_thread(
        evaluate, org_id, limits=limits, reading=reading)
    if notice is None:
        return {"status": reason, "org_id": org_id}
    if account.notice_pct is not None and notice.threshold <= account.notice_pct:
        # The high-water mark already covers this threshold — the ledger's whole
        # job. What it SAVES is the render and the pre-send mark re-read below,
        # i.e. a re-run of a finished pass costs no template work and no extra
        # round trip. ⛔ It does NOT save the two GAUGE reads above: those are
        # skipped only when the mark is already at the TOP threshold (the fast
        # path higher up), because only then is no threshold left to send. An
        # earlier revision called this check "what makes a re-run free" without
        # that distinction, which reads as if an 80-marked account never paid the
        # gauge — it does (#14038 review, mutation pass).
        return {"status": "already-notified", "org_id": org_id,
                "threshold": notice.threshold,
                "notified": account.notice_pct}
    if _unstamped_recently(org_id, notice.threshold):
        # A previous pass DELIVERED this threshold and could not write its mark.
        # Re-sending here is what turned a broken ledger into a daily duplicate AND
        # starved the rest of the fleet of the per-pass cap (#14038 review, P2) — so a
        # delivered-but-unrecorded notice is held off the send path for the provider's
        # own idempotency window. The status is deliberately NOT `sent`: the burst guard
        # must not be spent on an account that is not being mailed.
        logger.warning(
            "capacity notice: team %s owes the %s%% notice but a previous pass "
            "DELIVERED it and could not write the mark — suppressed until the "
            "ledger is writable again, or for %s s from that send",
            org_id, notice.threshold, UNSTAMPED_SUPPRESS_TTL_S)
        return {"status": "unstamped-deferred", "org_id": org_id,
                "threshold": notice.threshold}
    # Above this point the account has not been shown to owe a notice, so paying
    # for the chain's third rung (two network calls per account) on the whole
    # fleet every interval would buy nothing. Below it, the notice is due and the
    # remaining question is only where to send it. `no-email` therefore RANKS
    # BELOW the gauge checks now — an account with no address reports
    # ``no-allowance``/``below-threshold``/``no-reading`` when that is also true,
    # which is the more actionable of two true statements about it.
    recipient = await asyncio.to_thread(_notice_recipient, account, cp)
    if not recipient:
        # A registry/agent-minted org carries no address on any rung; the copy
        # greets a PERSON, so there is nobody to greet.
        logger.info(
            "capacity notice: team %s owes the %s%% notice but resolves no "
            "DELIVERABLE address — organizations.email and "
            "organizations.customer_email are empty OR do not have an address "
            "shape (`_deliverable_address`), and the owner's GoTrue address "
            "answered none EITHER. That third rung has FOUR states and this "
            "line does not claim which: the owner row is absent; the value it "
            "carries is not an address shape (`_deliverable_address` refuses "
            "it silently, logging nothing); resolving the owner RAISED and "
            "logged a warning above; or the GoTrue admin call itself failed — "
            "and ⛔ THAT ONE IS LOGGED NOWHERE: `supabase_control.owner_email` "
            "swallows a transport error, a non-200 and missing config with "
            "`return None` (its contract is 'never raises'), so an auth outage "
            "reaches this line looking exactly like an account with no "
            "address. Read the SET, not this line: a burst of these while the "
            "control plane is otherwise healthy is an outage; a single one is "
            "a missing address.",
            org_id, notice.threshold)
        return {"status": "no-email", "org_id": org_id}
    copy = render(notice.threshold,
                  email_notify.recipient_first_name(None, recipient,
                                                   account.name))
    # Re-read the mark ONCE more immediately before the send (#14038 review, P2):
    # another process may have stamped while this one was reading the gauge, and
    # a send is irreversible. This narrows the cross-process window to one round
    # trip; it does not close it — closing it needs a pre-send reservation (a
    # stamp-first protocol with a compensating unset), which is a design decision
    # rather than a tidy-up, and it is NOT improvised here: it is a stated
    # residual, and the module docstring carries its reachability — two live
    # processes ADMITTED (`TORTOISE_MULTI_WORKER=1`) or the 2-machine topology,
    # NOT an ordinary deploy: the app runs one machine on a single-attach volume,
    # so a replacement exits before the new process starts (`fly.toml`).
    try:
        from .supabase_control import org_storage_notice_pct

        fresh = await asyncio.to_thread(org_storage_notice_pct, cp, org_id)
    except Exception as e:
        # ⛔ A SILENT FALLBACK HERE WOULD BE THE DEFECT (#14038 fix lane): this
        # re-read is the ONLY thing that narrows the cross-process window to one
        # round trip, and the paragraph above states, in the present tense, that
        # it did. Assigning the stale value with no record leaves a reader unable
        # to tell "narrowed" from "could not read, proceeded anyway" — the same
        # class as the `no-reading` line this module logs when the gauge cannot
        # be read. The send PROCEEDS because the value this pass already gated on
        # is the CAS guard (`claim_notice(..., previous=fresh)`), so the LEDGER
        # stays monotone either way; what the failure costs is the narrowing.
        logger.warning(
            "capacity notice: the pre-send mark re-read for team %s failed (%s) "
            "— proceeding on the value read earlier in this pass, so this "
            "send's cross-process window is NOT narrowed",
            org_id, type(e).__name__)
        fresh = account.notice_pct
    if fresh is not None and notice.threshold <= fresh:
        return {"status": "already-notified", "org_id": org_id,
                "threshold": notice.threshold, "notified": fresh}
    sender = send
    if sender is None:
        async def sender(c: Copy) -> dict:
            return await email_notify.send_notice_email(
                recipient, c.subject, c.html, c.text,
                idempotency_key=f"capacity-notice:{org_id}:{notice.threshold}",
                label=f"capacity notice {notice.threshold}% (team {org_id})")
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
    try:
        stamped = await asyncio.to_thread(
            claim_notice, cp, org_id, notice.threshold, fresh)
    except Exception as e:
        # ⛔ THE SEND HAPPENED, SO THIS PASS MUST COUNT IT (#14038 review, P2).
        # The provider ACCEPTED this notice, so it has already spent its share
        # of the shared Resend budget — and `sweep_pass` decrements the
        # per-pass cap on `status == "sent"`. Letting the marker failure
        # escape as `error` would do two wrong things at once: report a
        # DELIVERED notice as a failure, and leave the burst guard believing
        # nothing was spent — so a pass whose ledger WRITES fail while its
        # READS still work would send past `max_sends`, which is the exact
        # overrun the cap exists to prevent (budget-exhausted invites/OTPs are
        # dropped with no retry). The mark stays unset, so the next pass
        # re-reads it and re-sends this threshold once the provider
        # Idempotency-Key's 24 h window has passed — a duplicate bounded by
        # the provider, not by us.
        logger.warning(
            "capacity notice: sent to team %s at %s%% but the mark could not "
            "be written (%s) — reporting it as SENT so it counts against "
            "this pass's cap; the mark stays unset and a later pass may send "
            "this threshold again after the provider key's 24 h window",
            org_id, notice.threshold, type(e).__name__)
        # ⛔ AND IT GOES ON THE SUPPRESS LIST (#14038 review, P2): counting it against the cap is
        # right for THIS pass, and it is exactly what made a ledger-write outage mail the same
        # lowest-id accounts every interval and starve the rest of the fleet. The next pass sees the
        # unset mark (`threshold <= fresh` is false) but `_unstamped_recently` holds it off the send
        # path, so the cap is no longer consumed by accounts that are not being mailed.
        _note_unstamped(org_id, notice.threshold)
        return {"status": "sent", "org_id": org_id,
                "threshold": notice.threshold,
                "message_id": (result or {}).get("message_id"),
                "stamped": False}
    if not stamped:
        # Another pass moved the mark between our read and our stamp. The email
        # went out; the provider key collapses only a replay of the SAME
        # threshold, and only inside its 24 h window (the key names the
        # threshold), so it bounds a duplicate of this send inside that window and
        # says nothing about a different one.
        #
        # ⛔ A LOST RACE MUST NOT DISCARD THE RECORD (#14038 review, P2). Logging
        # and returning left the mark BELOW the threshold that had just been
        # DELIVERED, so the next pass found `threshold > mark` and sent it again —
        # collapsed by the provider inside 24 h, a real duplicate beyond it. The
        # module docstring's own claim ("two notices in the wrong order, never a
        # lost or duplicated record") was therefore false for exactly this arm.
        # The repair is a bounded, idempotent RE-CLAIM against the value just
        # observed: if the mark is still below what we sent, re-run the same CAS
        # with that fresh value as its guard. One retry, never a loop — a second
        # loss means another pass is stamping ahead of us and ITS value is the
        # higher, correct one.
        reclaimed = False
        try:
            from .supabase_control import org_storage_notice_pct

            current = await asyncio.to_thread(
                org_storage_notice_pct, cp, org_id)
        except Exception as e:
            current = None
            logger.warning(
                "capacity notice: team %s at %s%% lost the stamp race AND the "
                "mark could not be re-read (%s) — a later pass may re-send this "
                "threshold after the provider key's 24 h window",
                org_id, notice.threshold, type(e).__name__)
        # ⛔ `current is None` IS ALSO A RE-CLAIM CASE, and excluding it was a bug
        # caught by the test below: a lost CAS whose re-read finds the row STILL
        # UNSET (the peer that beat us stamped a different column state, or its own
        # write rolled back) is exactly the state our own stamp is entitled to claim.
        # The CAS keeps that safe — `previous=None` matches only an unset row.
        if current is None or notice.threshold > current:
            try:
                reclaimed = await asyncio.to_thread(
                    claim_notice, cp, org_id, notice.threshold, current)
            except Exception as e:
                reclaimed = False
                logger.warning(
                    "capacity notice: team %s at %s%% lost the stamp race and "
                    "the re-claim raised (%s) — the mark stays at %s",
                    org_id, notice.threshold, type(e).__name__, current)
        if reclaimed:
            logger.info(
                "capacity notice: team %s at %s%% lost the stamp race and "
                "RE-CLAIMED the mark from %s — no duplicate is pending",
                org_id, notice.threshold, current)
        else:
            logger.warning(
                "capacity notice: sent to team %s at %s%% but the mark moved "
                "concurrently and could not be re-claimed (replaying this "
                "threshold inside the provider key's 24 h window is collapsed; "
                "a DIFFERENT threshold's notice is not)",
                org_id, notice.threshold)
            _note_unstamped(org_id, notice.threshold)
    return {"status": "sent", "org_id": org_id, "threshold": notice.threshold,
            "message_id": (result or {}).get("message_id")}


async def sweep_pass(*, cp=None, send=None, enumerate_orgs=None,
                     max_sends: int | None = None) -> dict:
    """One pass over the fleet: at most ONE notice per account per pass.

    ``enumerate_orgs`` defaults to ``billing._enumerate_billed_orgs`` — the seam
    that CERTIFIES its enumeration complete (`query_all` keyset walk) and RAISES
    rather than publish a partial page. That matters here for the same reason it
    matters to the billing reconcile: a truncated read is not an error, it is a
    shorter list, and a pass that quietly covers half the fleet looks exactly
    like a pass with nothing to do.

    Per-account isolation: one account's unreadable gauge, missing address or
    provider failure costs that account's notice, never the pass.

    ⛔ NO PER-PASS ACCOUNT CAP. An earlier revision forwarded a
    ``TORTOISE_CAPACITY_NOTICE_LIMIT`` to the enumerator, whose truncation is a
    lowest-id PREFIX — so the same first N accounts were re-processed on every
    pass and the fleet's suffix was never notified, recreating one layer up the
    silent-partial-pass this docstring forbids (#14038 review, P2).

    There IS a per-pass SEND cap (``max_sends`` / ``TORTOISE_CAPACITY_NOTICE_MAX_SENDS``,
    default 5, derived and re-priced at ``NOTICE_MAX_SENDS_PER_PASS_DEFAULT``) and it
    cannot have that defect: it walks the WHOLE enumeration and
    stops SENDING, and a send that did not happen leaves its account's mark unset,
    so the next pass starts with a SMALLER unstamped population and the fleet
    ramps. The difference is per-pass PROCESSING (a stuck prefix) against
    per-pass SENDING (a moving frontier) — see ``NOTICE_MAX_SENDS_ENV``'s why.

    ⛔ A PRECONDITION THIS PASS CANNOT SUPPLY: the gauge it reads has ONE
    production writer, the backup sweep (``BACKUP_SWEEP_ENABLED``, off unless the
    deploy's secret conjunction is present). With it off no NEW reading is
    written, so accounts fall to ``no-reading`` as their readings AGE OUT — the
    graph-storage dimension carries a staleness bound (24 h), so a reading taken
    before the switch was turned off stays usable until then and a notice CAN
    still be sent in that window. ⛔ An earlier cut said "every account reads
    ABSENT … NOTHING is ever sent", which is true only AFTER the staleness bound,
    not immediately. Past that bound nothing is sent, silently. The summary
    therefore carries a ``no-reading`` count WHENEVER ANY account answers
    ``no-reading`` (``statuses`` gains a key only for a status the pass actually
    saw, so an all-``below-threshold`` pass has no such key), and an all-
    ``no-reading`` pass is logged at INFO when the whole pass agrees on it.
    """
    if max_sends is None:
        max_sends = max_sends_per_pass()
    summary: dict[str, Any] = {"checked": 0, "sent": 0, "statuses": {},
                               "max_sends": max_sends}
    if cp is None:
        from .supabase_control import get_control_plane

        cp = get_control_plane()
    if enumerate_orgs is None:
        from .billing import _enumerate_billed_orgs

        enumerate_orgs = _enumerate_billed_orgs
    try:
        orgs = await asyncio.to_thread(enumerate_orgs)
    except Exception as e:
        logger.warning("capacity notice sweep: enumeration failed (%s) — no "
                       "notices this pass", type(e).__name__)
        return {**summary, "error": "enumeration-failed"}
    for row in orgs:
        org_id = row.get("id") if isinstance(row, dict) else None
        if not org_id:
            continue
        summary["checked"] += 1
        if summary["sent"] >= max_sends:
            # The burst guard: the pass has spent its share of the shared Resend
            # budget. Count what it did not reach, so a capped pass is visible as
            # a capped pass rather than as a small fleet.
            summary["statuses"]["deferred"] = (
                summary["statuses"].get("deferred", 0) + 1)
            continue
        try:
            result = await notify_account(org_id, cp=cp, send=send)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "capacity notice sweep: team %s failed (%s) — continuing",
                org_id, _logged_reason(e))
            result = {"status": "error"}
        status = str(result.get("status") or "error")
        summary["statuses"][status] = summary["statuses"].get(status, 0) + 1
        if status == "sent":
            summary["sent"] += 1
    other = summary["statuses"].keys() - {"below-threshold"}
    if summary["sent"] or other:
        logger.info("capacity notice sweep %s", summary)
    elif summary["checked"]:
        # Every account answered `below-threshold` — nothing else can reach here:
        # `other` (`statuses` keys minus `below-threshold`) is truthy for ANY
        # other status, so an all-`no-reading` pass takes the branch above and is
        # logged with its own summary. ⛔ An earlier cut of this comment claimed
        # the `no-reading` case arrived HERE, which it cannot; the hint below is
        # still worth printing for the all-`below-threshold` case it does serve,
        # because `no-reading` dominating is what a stopped gauge looks like and
        # that state is visible in the branch above's summary.
        logger.info("capacity notice sweep: %s accounts checked, nothing sent (%s) "
                    "-- if `no-reading` dominates, the graph-storage gauge is not "
                    "being written (BACKUP_SWEEP_ENABLED)", summary["checked"],
                    summary["statuses"])
    return summary
