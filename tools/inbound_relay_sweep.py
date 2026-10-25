#!/usr/bin/env python3
"""inbound_relay_sweep.py — the converging half of the intake relay (#2409/#2441).

WHY THIS EXISTS (measured 2026-10-24, not derived)
-------------------------------------------------
`.github/workflows/inbound-relay.yml` is the SOURCE STOP for external
contributions: on `issues:opened` / `pull_request_target:opened` it labels
`queued-for-triage`, comments, closes, and relays the record to intake. It is
also the only thing standing between an external pull request and this repo's
CI matrix plus a lane's attention — which matters because an external producer
can open a PR whose body asserts a fix while its diff asserts nothing (a
"phantom"). The phantom this lane was opened on, PR #9883, had
`README.md +3 −0` — two blank lines and one HTML comment claiming a Postgres
identity migration — and it ran the FULL CI matrix (6 reds, every one inherited
from a base two commits behind main) plus a lane cycle
(`~/.pi/agent/state/pr-doctor-brief-prd-9883.txt`).

The relay never fired for it. It could not:

  * `GET /repos/<owner>/<repo>/actions/permissions/fork-pr-contributor-approval`
    → `{"approval_policy": "all_external_contributors"}`. A workflow run started
    by an EXTERNAL actor is held for a maintainer's approval; a run with no
    approver never executes. (`tools/ci_check_repair.py` documents the same
    class from the other side — bot/fork runs created with ZERO jobs.)
  * Measured over the 800 most recent runs of that workflow
    (2026-10-19T05:08Z → 2026-10-24T23:53Z): **792 `skipped`** (owner-authored
    traffic, correctly excluded before a runner exists), **8
    `startup_failure`** — every one an external actor's event — and **0
    executions**. On #9883's `opened` event at 10:04:01Z, ALL FIVE workflows on
    that sha failed the same way; CI/Python-CI/drift-guard later ran as
    `run_attempt: 2` (repaired), and the relay has no repair path.
  * Its trigger list is `opened` only, so the repo's universal re-trigger
    (close + reopen → `reopened`, `tools/ci_check_repair.py`) cannot re-fire it
    either, and `ci_check_repair` repairs REQUIRED contexts only — the relay is
    not one.

So the honest reading is not "the producer is buggy" (it is, and it is not
ours: `bot@hermes.agent` pushed through an outside contributor's account, so
there is nothing here to patch) and not "add a stub-detector gate" (a diff-shape
heuristic would be a new heuristic with false blocks of its own, and it is the
wrong layer — an external producer can defeat any client-side patch). The
promised control exists and never runs. THE EFFECT MUST CONVERGE WITHOUT AN
EVENT-TIME RUN.

A `schedule` run is started by the repository's own credential, not an external
actor's, so it is not subject to the approval policy and does not depend on any
event surviving. This tool is that path.

WHAT IT DOES — the same policy as the event path, not a second one
------------------------------------------------------------------
  owner-authored item                        → leave (the zero-cost bound)
  already labelled `queued-for-triage`       → leave (the idempotence marker)
  queue-owned branch (`queue_branch_prefix`) → leave (#5185: closing a batch
                                               dequeues every merge in flight)
  bot-authored (any kind)                    → leave (below)
  anything else                              → relay to intake, then comment,
                                               close, and LABEL LAST

⛔ BOT-AUTHORED ITEMS ARE SPARED, AND THAT IS DELIBERATELY SAFER THAN THE
EVENT PATH'S PR BRANCH. Our own automation opens issues we must not destroy:
`deploy-hosted: deploy-api failed at 'drift'` (#9613, open, `bug`, authored by
`github-actions[bot]`) is a live alarm thread. The event path's issue branch has
no bot exclusion — its only spare is the owner's login — so the relay would have
closed #9613 and every sibling monitor issue the moment it ran. That clause is
repaired in the workflow itself; this tool spares bots outright because an
external bot's PR left open costs a step, while our own monitor issue closed
destroys a record a human reads.

The two implementations of that policy are cross-checked by
`tests/test_inbound_relay_sweep.py::test_policy_agrees_with_the_event_guard`,
which evaluates the WORKFLOW's real job-level `if:` and asserts this tool
decides the same way on the same shapes. A new exclusion in one path and not the
other fails the suite instead of silently closing the wrong class.

SAFETY RAILS (each pinned by a test) — a sweeper is riskier than a per-event relay
---------------------------------------------------------------------------------
  * RELAY-THEN-CLOSE-THEN-LABEL. The intake POST happens first and the LABEL is
    applied LAST. The whole per-item sequence is guarded: if any step fails the
    item is left UNLABELLED (so the next 15-minute run selects it again), the
    failure is reported, the remaining items still run, and the run exits 1 — a
    dead intake is an alarm rather than a silent "handled". Applying the label
    before the close would invert this: the label is the idempotence marker, so
    an open+labelled item is skipped by every later run and retried by nothing
    (the event path only listens for `opened`).
  * A RETRY RESUMES ITS COMMENT, never its silence: before re-commenting, the
    run checks for this relay's OWN comment footer (`RELAY_MARKER`, bot-typed or
    the owner's own login — the string alone is forgeable), reading the thread
    NEWEST-FIRST — the comments endpoint ignores `sort`/`direction`, so its first
    page is the OLDEST while the relay's comment (posted at close time) is at the
    end; every page up to the thread's end is read (bounded), because an exact
    multiple of `per_page` puts the marker on a page followed by an EMPTY one and
    a later reply can push it off the terminal page. The intake POST is still
    made on a resume: a marker proves we commented, never that the record
    landed (the event path posts intake LAST), and `source_item_id` keys it.
  * THE AMBIGUOUS STATE IS REPORTED, NOT DIAGNOSED. An open item that already
    carries the label is EITHER a half-applied relay (the event path labels
    FIRST and nothing retries it) OR an item relayed and then REOPENED for
    review — the flow the relay's own comment promises, and nothing removes the
    label. It is counted (`stranded`) by its own filtered read (the window read
    cannot see an item that aged out) and named in the notes as something to
    verify. It is never auto-closed: the sweep cannot tell the two apart, and a
    human may also have labelled it deliberately.
  * COMPLETENESS — a FAILED page aborts. An unreadable page is not an empty
    page: a partial read (rate limit, transient 5xx, timeout) aborts the whole
    run BEFORE any mutation (exit 2). This is the "never bulk-close on a
    rate-limited read" rule.
  * A DENSE WINDOW ⇒ A PREFIX, NOT AN ABORT. A window holding more items than
    the page budget must not let a flood switch the control off (any GitHub user
    can open 1,000 issues on a public repo): the pages already read are acted
    on and the unread remainder is reported as DEFERRED to later runs.
  * NOT-CONFIGURED fails closed. No `INBOUND_URL` or no `INBOUND_SECRET_*` means
    the record cannot be written, so nothing is closed (exit 2).
  * A bounded WINDOW (`--since-hours`, default 24) and a bounded run
    (`--max`, default 20). The window is what keeps a first run from reaching
    back through history; the cap reports the remainder instead of hiding it.
  * TWO budgets, because a READ can cost as much as an ACTION. `--max` bounds
    the mutations; `DEFAULT_DETAIL_BUDGET` bounds the per-PR detail fetches that
    decide them (one `gh api` call each, against a REST budget of ~1,000/hour).
    Candidates beyond either budget are counted as `unexamined` and reported —
    a run that spent its reads would otherwise 403 on its mutations and re-relay
    the same items every 15 minutes without ever closing them.
  * `--dry-run` prints the plan and performs nothing.

It carries no merge capability at all: it closes and relays, and it never
merges (#8375's property for `ci_check_repair.py`, kept here).

EXIT CODES
  0  complete: every selected item was relayed (or the run was a dry run)
  1  at least one item's intake POST failed — that item was left untouched
  2  could not act: intake not configured, or the listing was incomplete
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/inbound_relay_sweep.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/inbound_relay_sweep.py`"
    )

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parent.parent
MERGIFY = REPO_ROOT / ".mergify.yml"

#: Mergify's documented default `queue_branch_prefix` (the repo sets none).
DOCUMENTED_DEFAULT_PREFIX = "mergify/merge-queue/"
#: The idempotence marker and the label the event path applies. Bound to
#: `.github/workflows/inbound-relay.yml` by a test, never copied by hand.
LABEL = "queued-for-triage"
OWNER = "daniel-ospina"
DEFAULT_REPO = f"{OWNER}/tortoise"

#: How many per-PR detail fetches one run may spend. `--max` bounds the MUTATIONS;
#: this bounds the READS that decide them, because a detail is one `gh api` call
#: and the page budget alone admits up to 1,000 candidates. Without it the run
#: spends the whole REST budget on reads, then 403s on the mutations — and a
#: mutation that fails after its intake POST re-relays the item every 15 minutes
#: without ever closing it (#9946 review, round 2).
DEFAULT_DETAIL_BUDGET = 60

_COMMENT_PULL = (
    "👋 Thanks for the contribution!",
    "",
    "This PR has been **queued for review** and will be processed by our intake agent.",
    "You'll be notified when an agent picks it up.",
    "",
    "> ⚠️ **Note:** External PRs are close-only in v1. If your PR is selected for",
    "> review, it will be reopened for the review process.",
    "",
    "— Premise Labs (automated relay)",
)
_COMMENT_ISSUE = (
    "👋 Thanks for submitting!",
    "",
    "This has been **queued for triage** and will be processed by our intake agent.",
    "You'll be notified when an agent picks it up.",
    "",
    "— Premise Labs (automated relay)",
)

#: The footer both comment templates end with. It is how a RETRY recognises a
#: comment this relay already posted: the sequence is relay → comment → close →
#: label, so a failed close leaves an item that the next run selects again, and
#: without this marker that retry would post a SECOND identical comment (and a
#: duplicate intake record) every 15 minutes until the close succeeds.
RELAY_MARKER = "— Premise Labs (automated relay)"


class ListingError(RuntimeError):
    """A listing could not be completed — never treat it as an empty listing."""


class ConfigError(RuntimeError):
    """A safety-relevant config could not be read — act on nothing."""


@dataclass(frozen=True)
class Config:
    repo: str
    owner: str
    label: str
    queue_prefixes: tuple[str, ...]
    intake_url: str
    intake_secret: str


@dataclass
class Report:
    selected: list[tuple[str, int]] = field(default_factory=list)
    relayed: list[tuple[str, int]] = field(default_factory=list)
    failed: list[tuple[str, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    deferred: int = 0
    #: Open items that ALREADY carry the label. The label means "fully handled",
    #: so an open+labelled item is a half-applied relay — the one state nothing
    #: repairs (the event path only listens for `opened`, and this sweep skips
    #: the label as its idempotence marker). Counted and reported, never
    #: auto-closed: a human may have labelled an item deliberately to keep it
    #: open for manual triage.
    stranded: int = 0
    #: Candidates whose per-PR detail was never fetched (detail budget spent, or
    #: the action cap already reached), so they were neither acted on nor
    #: classified. Reported, never silently dropped.
    unexamined: int = 0
    #: The window held more items than the page budget: the prefix was acted on
    #: and the unread remainder is deferred (never "nothing may be closed").
    truncated: bool = False
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def exit_code(self) -> int:
        if self.errors:
            return 2
        if self.failed:
            return 1
        return 0


# ── the decision (pure, unit-tested, cross-checked against the workflow) ─────


def _label_names(item: dict[str, Any]) -> list[str]:
    return [
        str(label.get("name") if isinstance(label, dict) else label)
        for label in item.get("labels") or []
    ]


def _labels(item: dict[str, Any]) -> set[str]:
    return {name.casefold() for name in _label_names(item)}


def _author(item: dict[str, Any]) -> str:
    user = item.get("user") or {}
    return str(user.get("login") or "")


def _author_type(item: dict[str, Any]) -> str:
    user = item.get("user") or {}
    return str(user.get("type") or "")


def _canon(value: Any) -> str:
    """GitHub comparisons are case-insensitive; null compares as empty."""
    return "" if value is None else str(value).casefold()


def _head_ref(item: dict[str, Any]) -> str:
    return _canon((item.get("head") or {}).get("ref"))


def _head_repo(item: dict[str, Any]) -> str:
    head = item.get("head") or {}
    repo = head.get("repo") or {}
    return _canon(repo.get("full_name"))


def should_relay(item: dict[str, Any], cfg: Config) -> bool:
    """True ⇔ this item is one the event path would also close-and-queue.

    The polarity is the guard's: an item the sweep cannot classify as traffic
    it must spare is RELAYED (fail closed on the enforcement side), and the
    listing rails are what stop a bad read from reaching this decision at all.
    """
    if _canon(_author(item)) == _canon(cfg.owner):
        return False
    if cfg.label.casefold() in _labels(item):
        return False

    # ⛔ BOT-AUTHORED ITEMS ARE NEVER OURS TO CLOSE. Our own automation opens
    # some of them: `deploy-hosted: deploy-api failed at 'drift'` (#9613) is a
    # live alarm thread opened by `github-actions[bot]`, and closing it would
    # destroy a monitor record that a human reads. The event path's
    # `pull_request_target` branch already spares the queue's own batch; this
    # rule also covers the ISSUE branch, where the guard's only exclusion is the
    # owner's login — measured on #9613, before this line existed.
    if _author_type(item).casefold() == "bot":
        return False

    same_repo = _head_repo(item) == _canon(cfg.repo)
    queue_branch = same_repo and any(
        _head_ref(item).startswith(prefix.casefold()) for prefix in cfg.queue_prefixes
    )
    return not queue_branch


def _prefilter(item: dict[str, Any], cfg: Config) -> bool:
    """The part of the decision that needs no PR detail: skip owner traffic and
    anything already relayed, so only live candidates cost a detail fetch."""
    if _canon(_author(item)) == _canon(cfg.owner):
        return False
    return cfg.label.casefold() not in _labels(item)


def _created_at(item: dict[str, Any]) -> datetime:
    raw = str(item.get("created_at") or "")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _kind(item: dict[str, Any]) -> str:
    return "issue" if "head" not in item else "pr"


# ── the intake payload, byte-identical in shape to the event path's ──────────


def intake_payload(item: dict[str, Any]) -> dict[str, Any]:
    kind = _kind(item)
    number = int(item["number"])
    user = item.get("user") or {}
    body: dict[str, Any] = {
        "subject": item.get("title") or "",
        "body": item.get("body") or "",
        "labels": _label_names(item),
        "url": item.get("html_url") or "",
        "number": number,
    }
    if kind == "pr":
        body["head_ref"] = (item.get("head") or {}).get("ref")
        body["head_repo"] = ((item.get("head") or {}).get("repo") or {}).get("full_name")
    return {
        "source": "github",
        "source_item_id": f"{kind}-{number}",
        "payload": body,
        "context_ref": item.get("html_url") or "",
        "reporter": user.get("login"),
        "actor": {"login": user.get("login"), "type": user.get("type")},
        "severity": None,
    }


def _needs_detail(item: dict[str, Any]) -> bool:
    """True ⇔ classifying this item costs a per-PR detail fetch.

    Only the queue-owned head branch needs one (it is the sole marker that lives
    on the detail). Owner, already-labelled and BOT-authored items are decided
    from the listing alone, and `should_relay` returns `False` for a bot-typed
    item BEFORE it ever looks at the head — so skipping the fetch here cannot
    change the decision, it only stops the run spending its REST budget on items
    it will spare.
    """
    if "pull_request" not in item:
        return False
    return _author_type(item).casefold() != "bot"


# ── the sweep ───────────────────────────────────────────────────────────────


class Client(Protocol):
    def list_open(self, *, stop_before: datetime | None = None) -> list[dict[str, Any]]: ...
    def list_labelled(self, label: str) -> list[dict[str, Any]]: ...
    def pull_detail(self, number: int) -> dict[str, Any]: ...
    def already_relayed(self, number: int) -> bool: ...
    def post_intake(self, payload: dict[str, Any]) -> None: ...
    def add_label(self, number: int, label: str) -> None: ...
    def comment(self, number: int, body: str) -> None: ...
    def close(self, kind: str, number: int) -> None: ...


def sweep(
    client: Client,
    cfg: Config,
    *,
    now: datetime,
    since_hours: int = 24,
    max_actions: int = 20,
    detail_budget: int = DEFAULT_DETAIL_BUDGET,
    dry_run: bool = False,
) -> Report:
    report = Report(dry_run=dry_run)

    # A dry run never POSTs, so it must not demand the credential it does not
    # use: demanding it turns "show me the plan" into a red run on exactly the
    # machine where the secret is not configured (a maintainer inspecting the
    # live repo).
    if not dry_run and (not cfg.intake_url or not cfg.intake_secret):
        report.errors.append(
            "intake is not configured (INBOUND_URL / INBOUND_SECRET_GITHUB): "
            "nothing is closed without a record"
        )
        return report

    cutoff = now - timedelta(hours=since_hours)
    try:
        listing = list(client.list_open(stop_before=cutoff))
        # CAPTURED HERE, not read later: `window_truncated` is a per-LISTING flag,
        # and the `list_labelled` read below resets it. Reading it after that call
        # (as this did) meant the dense-window report could only ever be a
        # FakeClient artefact — with the real client the prefix's truncation was
        # always erased before it was read, so a run that saw a fraction of the
        # window reported a clean run.
        window_truncated = bool(getattr(client, "window_truncated", False))
        candidates = [
            item for item in listing if _created_at(item) >= cutoff and _prefilter(item, cfg)
        ]
        # `/issues` carries a PR without its head/base; the detail is what tells a
        # queue batch from an external PR. Fetched per candidate (a handful per
        # run) rather than paging the whole `/pulls` backlog, which is both slow
        # (a 60 s timeout is a real measurement, not a hypothetical) and heavy.
        # OLDEST FIRST, and only until the actable prefix is known: the detail is
        # one REST call each, and the page budget admits up to 1,000 candidates.
        candidates.sort(key=_created_at)
        resolved: list[dict[str, Any]] = []
        actable = 0
        details = 0
        unexamined = 0
        for item in candidates:
            if not _needs_detail(item):
                resolved.append(item)
                if should_relay(item, cfg):
                    actable += 1
                continue
            if actable >= max(0, max_actions) or details >= max(0, detail_budget):
                # No detail will be spent on this one: report it rather than
                # classify it blind, and never let it abort the items before it.
                unexamined += 1
                continue
            details += 1
            detail = client.pull_detail(int(item["number"]))
            resolved.append(detail)
            if should_relay(detail, cfg):
                actable += 1
        items = resolved
    except Exception as exc:  # any read failure is "incomplete"
        report.errors.append(f"listing incomplete, no action taken: {exc}")
        return report

    report.unexamined = unexamined

    # The one state nothing repairs: OPEN and already carrying the marker that
    # means "fully handled" (the event path labels FIRST, so a 502 on its
    # comment/close leaves exactly this). It cannot be found in the window read:
    # a stranded item ages OUT of the window, and paging stops at the cutoff — so
    # it would go invisible a second time, in the very report that exists to
    # surface it. One bounded, server-filtered read instead, and a failure here
    # is a NOTE rather than an abort: it is a diagnostic, not a precondition for
    # acting on the window.
    try:
        labelled = list(client.list_labelled(cfg.label))
        report.stranded = sum(1 for item in labelled if _canon(_author(item)) != _canon(cfg.owner))
    except Exception as exc:
        report.notes.append(f"could not check for half-applied (open+labelled) items: {exc}")

    if report.stranded:
        report.notes.append(
            f"{report.stranded} open item(s) already carry {cfg.label!r}: the label "
            "means 'fully handled', so this is EITHER a half-applied relay (the "
            "event path labels FIRST, then comments and closes) OR an item that was "
            "relayed and then REOPENED for review — the flow the relay's own comment "
            "promises, and nothing in this repo removes the label. This sweep cannot "
            "tell them apart and will not touch either (a human may also have "
            "labelled it deliberately), so this is a count to verify, not a verdict"
        )

    if window_truncated:
        # NOT an error: a dense window acts on the prefix it read. Aborting here
        # would hand any flooder an off-switch for the whole control.
        report.truncated = True
        report.notes.append(
            "the window holds more open items than the page budget: the pages "
            "read were acted on below, and the UNREAD remainder is deferred to "
            "later runs (a dense window must never disable the sweep)"
        )

    selected = [item for item in items if should_relay(item, cfg)]
    # Oldest first: the longest-waiting item is the one whose absence hurts.
    selected.sort(key=_created_at)
    report.selected = [(_kind(item), int(item["number"])) for item in selected]

    actionable = selected[: max(0, max_actions)]
    report.deferred = len(selected) - len(actionable) + report.unexamined

    for item in actionable:
        kind, number = _kind(item), int(item["number"])
        if dry_run:
            report.relayed.append((kind, number))
            continue
        # ⛔ THE WHOLE SEQUENCE IS GUARDED, AND THE LABEL IS LAST. The label is
        # the idempotence marker: applying it before the comment/close would
        # mean a bookkeeping failure leaves the item OPEN AND LABELLED — skipped
        # by every later run and retried by nothing (the event path only listens
        # for `opened`). Applied last, any failure leaves the item unlabelled and
        # therefore re-selectable, and `main()` still prints the report.
        step = "resume check"
        try:
            # A retry must RESUME, not restart: if our own footer is already on
            # the item, the comment landed and only the close (or the label)
            # failed, so a second identical comment on an external contributor's
            # PR would be noise every 15 minutes for as long as the close fails.
            # ⛔ The RECORD is still posted. A marker proves we COMMENTED, never
            # that intake accepted the record — the event path posts intake LAST
            # (label → comment → close → POST), so its footer can exist with no
            # record at all. `source_item_id` keys the POST, so a repeat is a
            # no-op intake-side; closing without a record is unrecoverable.
            resume = client.already_relayed(number)
            step = "intake POST"
            client.post_intake(intake_payload(item))
            if not resume:
                step = "comment"
                client.comment(number, "\n".join(_COMMENT_PULL if kind == "pr" else _COMMENT_ISSUE))
            step = "close"
            client.close(kind, number)
            step = "label"
            client.add_label(number, cfg.label)
        except Exception as exc:
            report.failed.append((kind, number))
            if step == "label":
                # ⛔ NOT retried by anyone: the close succeeded, so the item is no
                # longer in the open listing and no later run re-selects it. Say
                # so, instead of claiming it self-heals.
                report.notes.append(
                    f"{kind} #{number}: label failed AFTER the close succeeded — "
                    f"the item is relayed and closed but NOT labelled, so no "
                    f"later run will select it again; apply `{cfg.label}` by "
                    f"hand: {exc}"
                )
            else:
                report.notes.append(
                    f"{kind} #{number}: {step} failed (item is still open and "
                    f"unlabelled, so a later run retries it): {exc}"
                )
            continue
        report.relayed.append((kind, number))

    return report


# ── the `gh` client ────────────────────────────────────────────────────────


def _gh(argv: list[str], *, timeout: int = 60) -> str:
    proc = subprocess.run(
        ["gh", *argv], capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(argv[:3])} failed (rc={proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return proc.stdout


class GhClient:
    """`gh api` over REST. A read is complete-for-the-window or raises.

    Paging is WINDOW-BOUNDED, not backlog-bounded: the list is requested
    newest-first and paging stops once a page's oldest item is older than the
    window. A backlog-shaped read would be both expensive and fragile — this
    repo has 1,202 open issues+pulls, so "walk every page or raise" would raise
    on every run and turn the sweep into a permanent no-op (the very defect it
    exists to repair).

    A FAILED page raises (`ListingError`) — a transient `gh` failure is not an
    empty page. A window holding more than the page budget does NOT raise: it
    returns the prefix it read and sets `window_truncated`, so the caller acts
    on what it saw and defers the rest. Raising there would let a flood of
    >`max_pages * per_page` opened items in 24 h — which any GitHub user can
    produce on a public repo — switch the control off permanently.
    """

    def __init__(
        self,
        repo: str,
        *,
        per_page: int = 100,
        max_pages: int = 10,
        comment_page_budget: int = 10,
    ) -> None:
        self.repo = repo
        #: The repository OWNER, from the repo slug — used only to attribute a
        #: comment this tool posts under its own (non-bot) identity.
        self.owner = repo.split("/")[0]
        self.per_page = per_page
        self.max_pages = max_pages
        #: How many comment pages one resume read may walk. The relay's comment
        #: is LAST in the endpoint's ascending order, so the read has to reach
        #: the end of the thread; 10 × 100 covers any thread this repo has seen.
        self.comment_page_budget = comment_page_budget
        #: True ⇔ the last listing stopped at the page budget with the window
        #: possibly continuing past it (the unread remainder is deferred).
        self.window_truncated = False

    def _paginate(self, path: str, *, stop_before: datetime | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        self.window_truncated = False
        # `path` may already carry a query (`?state=open`): a second `?` is a
        # 422 from the API, which the rail below correctly reads as "incomplete".
        separator = "&" if "?" in path else "?"
        for page in range(1, self.max_pages + 1):
            try:
                raw = _gh(
                    [
                        "api",
                        f"{path}{separator}per_page={self.per_page}&page={page}",
                    ]
                )
                chunk = json.loads(raw)
            except Exception as exc:
                raise ListingError(f"{path} page {page}: {exc}") from exc
            if not isinstance(chunk, list):
                raise ListingError(f"{path} page {page}: expected a list")
            items.extend(chunk)
            if len(chunk) < self.per_page:
                return items
            if stop_before is not None and chunk and _created_at(chunk[-1]) < stop_before:
                # Newest-first: this page already reaches past the window.
                return items
        # The page budget is exhausted with the window still possibly continuing.
        # Return the PREFIX (never raise — see the class docstring) and let the
        # caller report the remainder as deferred.
        self.window_truncated = True
        return items

    def list_open(self, *, stop_before: datetime | None = None) -> list[dict[str, Any]]:
        """Every open issue AND pull request, newest first, window-bounded.

        `/issues` is the cheap listing: it includes pull requests (marked by a
        `pull_request` key) with author, labels and `created_at` — everything
        the pre-filter needs. A PR's head/base needs a detail fetch, done per
        candidate, instead of paging the whole `/pulls` backlog (which is slow
        and heavy: a 60 s timeout on one page is a measurement, not a
        hypothetical).
        """
        return self._paginate(
            f"repos/{self.repo}/issues?state=open&sort=created&direction=desc",
            stop_before=stop_before,
        )

    def pull_detail(self, number: int) -> dict[str, Any]:
        try:
            obj = json.loads(_gh(["api", f"repos/{self.repo}/pulls/{number}"]))
        except Exception as exc:
            raise ListingError(f"pulls/{number}: {exc}") from exc
        if not isinstance(obj, dict):
            raise ListingError(f"pulls/{number}: expected an object")
        return obj

    def list_labelled(self, label: str) -> list[dict[str, Any]]:
        """Every OPEN item carrying `label`, server-filtered and bounded.

        The half-applied state (open + labelled) has to be found regardless of
        the 24-hour window: a window read stops at the cutoff, so a stranded item
        ages OUT of it and would go invisible for the second time — in the very
        report that exists to surface it. Its own filtered query, not a scan.
        """
        return self._paginate(
            f"repos/{self.repo}/issues?state=open&labels={quote(label, safe='')}",
            stop_before=None,
        )

    def already_relayed(self, number: int) -> bool:
        """True ⇔ THIS relay already commented on the item (see RELAY_MARKER).

        One bounded read of the NEWEST comments. The comments endpoint has NO
        `sort`/`direction` parameters — it accepts only `since`, `per_page` and
        `page`, ascending by id, and silently IGNORES anything else, so asking
        for `direction=desc` returns the OLDEST page (measured on #9613: an
        ascending triple with and without the parameters). The relay's comment
        is posted at close time, i.e. LAST, so the newest page has to be WALKED
        TO, or an item with more than `per_page` comments reports "not relayed"
        and the retry re-posts the identical comment every 15 minutes.

        ⛔ THE MARKER MUST BE OURS, and it proves only that we COMMENTED. The
        footer is a fixed public string printed on every relayed item, so an
        author could paste it into a comment of their own; only a BOT-typed
        comment (an installed App / GITHUB_TOKEN — not impersonable by a
        contributor) or the repository owner's own login counts. A read failure
        raises before any mutation, so the item is left untouched rather than
        acted on with a guess.
        """
        comments = self._comment_pages(number)
        for comment in reversed(comments):
            if RELAY_MARKER not in str(comment.get("body") or ""):
                continue
            user = comment.get("user") or {}
            if str(user.get("type") or "").casefold() == "bot":
                return True
            if _canon(user.get("login")) == _canon(self.owner):
                return True
        return False

    def _comment_pages(self, number: int) -> list[dict[str, Any]]:
        """Every comment the thread's pages hold, up to `comment_page_budget`.

        The endpoint is ASCENDING by id and ignores `sort`/`direction`, so the
        relay's own footer — posted at close time — is on the LAST page, and a
        read that stops early misses it. Two shapes make the terminal page the
        wrong page to scan on its own, so ALL pages read are kept and scanned
        newest-first:

        * an EXACT multiple of `per_page` comments has a FULL last page followed
          by an EMPTY one, so "the page past the end" is empty while the marker
          sits on the page before it;
        * a contributor can reply AFTER our comment, pushing it off the terminal
          page entirely.

        An empty page is the end of the thread (stop, keep what was read); a
        FAILED page raises, so a partial read never looks like a complete one.
        On exhausting the budget the deepest pages read are scanned — the best
        evidence available — rather than raising: an `ListingError` here would
        make a long thread permanently un-relayable, which is worse than the
        duplicate comment this guards against.
        """
        path = f"repos/{self.repo}/issues/{number}/comments"
        comments: list[dict[str, Any]] = []
        for page in range(1, self.comment_page_budget + 1):
            try:
                raw = _gh(["api", f"{path}?per_page={self.per_page}&page={page}"])
                chunk = json.loads(raw)
            except Exception as exc:
                raise ListingError(f"issues/{number}/comments page {page}: {exc}") from exc
            if not isinstance(chunk, list):
                raise ListingError(f"issues/{number}/comments: expected a list")
            if not chunk:
                break
            comments.extend(chunk)
            if len(chunk) < self.per_page:
                break
        return comments

    def post_intake(self, payload: dict[str, Any]) -> None:
        url = os.environ.get("INBOUND_URL", "")
        secret = os.environ.get("INBOUND_SECRET_GITHUB", "")
        if not url or not secret:
            raise RuntimeError("INBOUND_URL / INBOUND_SECRET_GITHUB unset")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "x-inbound-secret": secret,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"intake answered {response.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"intake refused ({exc.code})") from exc

    def add_label(self, number: int, label: str) -> None:
        _gh(
            [
                "api",
                "-X",
                "POST",
                f"repos/{self.repo}/issues/{number}/labels",
                "-f",
                f"labels[]={label}",
            ]
        )

    def comment(self, number: int, body: str) -> None:
        _gh(
            [
                "api",
                "-X",
                "POST",
                f"repos/{self.repo}/issues/{number}/comments",
                "-f",
                f"body={body}",
            ]
        )

    def close(self, kind: str, number: int) -> None:
        if kind == "pr":
            _gh(
                [
                    "api",
                    "-X",
                    "PATCH",
                    f"repos/{self.repo}/pulls/{number}",
                    "-f",
                    "state=closed",
                ]
            )
            return
        _gh(
            [
                "api",
                "-X",
                "PATCH",
                f"repos/{self.repo}/issues/{number}",
                "-f",
                "state=closed",
                "-f",
                "state_reason=not_planned",
            ]
        )


# ── configuration + entry point ────────────────────────────────────────────


def queue_prefixes(path: Path = MERGIFY) -> tuple[str, ...]:
    """The configured queue branch prefixes (documented default when unset).

    ⛔ An unreadable config RAISES instead of falling back to the documented
    default. Falling back would mean "no configured prefix matches" ⇒ a
    reconfigured queue's batch pull request is classified as external ⇒ the
    sweep closes it ⇒ every in-flight merge dequeues (#5185). Not being able to
    read the exclusion is a reason to act on nothing, not a reason to guess.
    """
    try:
        import yaml

        doc = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        raise ConfigError(
            f"cannot read the queue configuration at {path} ({exc}): the sweep "
            "cannot tell a queue batch from an external PR, so it must not act"
        ) from exc
    prefixes = {
        str(rule.get("queue_branch_prefix") or DOCUMENTED_DEFAULT_PREFIX)
        for rule in (doc.get("queue_rules") or [])
    }
    return tuple(sorted(prefixes or {DOCUMENTED_DEFAULT_PREFIX}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    parser.add_argument("--since-hours", type=int, default=24)
    parser.add_argument("--max", type=int, default=20, dest="max_actions")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    owner = args.repo.split("/")[0]
    try:
        prefixes = queue_prefixes()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    cfg = Config(
        repo=args.repo,
        owner=owner,
        label=LABEL,
        queue_prefixes=prefixes,
        intake_url=os.environ.get("INBOUND_URL", ""),
        intake_secret=os.environ.get("INBOUND_SECRET_GITHUB", ""),
    )
    try:
        report = sweep(
            GhClient(args.repo),
            cfg,
            now=datetime.now(UTC),
            since_hours=args.since_hours,
            max_actions=args.max_actions,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        # An escaping exception must still render as an error, not as a
        # traceback with no report: the run is the alarm, and a traceback on a
        # scheduled run tells a reader nothing about what was acted on.
        print(f"error: the sweep could not complete: {exc}", file=sys.stderr)
        return 2

    verb = "WOULD relay" if args.dry_run else "relayed"
    for kind, number in report.relayed:
        print(f"{verb} {kind} #{number}")
    for kind, number in report.failed:
        print(f"FAILED   {kind} #{number} (unlabelled — see the note)")
    for note in report.notes:
        print(f"note: {note}", file=sys.stderr)
    if report.deferred:
        print(
            f"deferred {report.deferred} item(s) past --max={args.max_actions} "
            "or not examined within the detail budget"
        )
    if report.stranded:
        print(
            f"STRANDED {report.stranded} open item(s) already carry {LABEL!r} "
            "— half-applied, nothing retries them; see the note"
        )
    if report.truncated:
        print(
            "deferred: the unread remainder of the window (the page budget was "
            "exhausted; later runs continue from the next window)"
        )
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)
    print(
        f"sweep: selected={len(report.selected)} {verb}={len(report.relayed)} "
        f"failed={len(report.failed)} deferred={report.deferred}"
    )
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
